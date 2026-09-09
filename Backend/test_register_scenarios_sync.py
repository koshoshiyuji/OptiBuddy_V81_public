"""
test_register_scenarios_sync.py

2026-07-18d追記の恒久対応（_register_scenarios()の再同期ロジック）に対する
回帰テスト。DB(SQLite)には依存せず、DslRepository相当のフェイクを
差し込んでロジックだけを検証する（このBackendのSQLiteはClaudeの
サンドボックスからマウント越しに書き込めない制約があるため、実DBを
使わないテストにしている）。

検証内容:
1. 新規シナリオ（DB未登録）は create_scenario が呼ばれ status="created"。
2. 既存シナリオでファイル内容がDBと一致 → 何もせず status="skipped_duplicate"。
3. 既存シナリオでファイル内容がDBと不一致（今回のMeetingRoomのケース）
   → update_scenario が呼ばれ status="synced_from_file"。
"""

import json
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

import domain_generator


class _FakeRepo:
    def __init__(self, initial_scenarios):
        # initial_scenarios: list of {"id", "name", "dsl_json"}
        self._scenarios = {s["name"]: dict(s) for s in initial_scenarios}
        self._next_id = max([s["id"] for s in initial_scenarios], default=0) + 1
        self.create_calls = []
        self.update_calls = []

    def list_scenarios(self, *args, **kwargs):
        return list(self._scenarios.values())

    def create_scenario(self, name, description, tag, tag_color, domain, dsl_json, source_file=None):
        sid = self._next_id
        self._next_id += 1
        self._scenarios[name] = {"id": sid, "name": name, "dsl_json": dsl_json, "source_file": source_file}
        self.create_calls.append(name)
        return sid

    def update_scenario(self, scenario_id, dsl_json=None, **kwargs):
        for s in self._scenarios.values():
            if s["id"] == scenario_id:
                s["dsl_json"] = dsl_json
        self.update_calls.append(scenario_id)
        return True


def _write_scenario_file(tmpdir: Path, filename: str, dsl_json: dict) -> str:
    path = tmpdir / filename
    path.write_text(json.dumps(dsl_json), encoding="utf-8")
    # _register_scenarios() は _resolve_path() 経由でBackendルート相対に解決するため、
    # ここではテスト用に abs pathをそのまま渡せるよう _resolve_path をパッチする。
    return str(path)


def test_new_scenario_is_created():
    with tempfile.TemporaryDirectory() as td:
        tmpdir = Path(td)
        dsl_json = {"problem_class": "MeetingRoom", "config": {"same_dept_same_room_bonus": 0.1}}
        file_path = _write_scenario_file(tmpdir, "new_scenario.json", dsl_json)

        fake_repo = _FakeRepo(initial_scenarios=[])

        import dsl_repository.repository as repo_module
        orig_repo_cls = repo_module.DslRepository
        orig_resolve = domain_generator.registry_patch._resolve_path
        try:
            repo_module.DslRepository = lambda: fake_repo
            domain_generator.registry_patch._resolve_path = lambda p: Path(file_path)

            result = domain_generator._register_scenarios([
                {"name": "新規シナリオ", "file": "dummy", "description": "", "tag": "X",
                 "tag_color": "#000", "domain": "meeting_room"}
            ])
        finally:
            repo_module.DslRepository = orig_repo_cls
            domain_generator.registry_patch._resolve_path = orig_resolve

        assert result[0]["status"] == "created"
        assert fake_repo.create_calls == ["新規シナリオ"]
        assert fake_repo.update_calls == []


def test_duplicate_with_identical_content_is_skipped_without_write():
    with tempfile.TemporaryDirectory() as td:
        tmpdir = Path(td)
        dsl_json = {"problem_class": "MeetingRoom", "config": {"same_dept_same_room_bonus": 0.1}}
        file_path = _write_scenario_file(tmpdir, "same.json", dsl_json)

        fake_repo = _FakeRepo(initial_scenarios=[
            {"id": 156, "name": "MeetingRoom - 標準", "dsl_json": dsl_json}
        ])

        import dsl_repository.repository as repo_module
        orig_repo_cls = repo_module.DslRepository
        orig_resolve = domain_generator.registry_patch._resolve_path
        try:
            repo_module.DslRepository = lambda: fake_repo
            domain_generator.registry_patch._resolve_path = lambda p: Path(file_path)

            result = domain_generator._register_scenarios([
                {"name": "MeetingRoom - 標準", "file": "dummy", "description": "", "tag": "X",
                 "tag_color": "#000", "domain": "meeting_room"}
            ])
        finally:
            repo_module.DslRepository = orig_repo_cls
            domain_generator.registry_patch._resolve_path = orig_resolve

        assert result[0]["status"] == "skipped_duplicate"
        assert fake_repo.update_calls == []
        assert fake_repo.create_calls == []


def test_duplicate_with_stale_db_content_gets_synced_from_file():
    """
    今回の実バグ相当のケース: ファイルは修正済み(same_dept_same_room_bonus: 0.1)だが
    DB側は古い値(same_dept_same_room_penalty: 50)のまま。再登録実行で
    update_scenario()が呼ばれ、status="synced_from_file"になるべき。
    """
    with tempfile.TemporaryDirectory() as td:
        tmpdir = Path(td)
        fixed_dsl_json = {"problem_class": "MeetingRoom", "config": {"same_dept_same_room_bonus": 0.1}}
        stale_dsl_json = {"problem_class": "MeetingRoom", "config": {"same_dept_same_room_penalty": 50}}
        file_path = _write_scenario_file(tmpdir, "baseline.json", fixed_dsl_json)

        fake_repo = _FakeRepo(initial_scenarios=[
            {"id": 156, "name": "MeetingRoom - 標準", "dsl_json": stale_dsl_json}
        ])

        import dsl_repository.repository as repo_module
        orig_repo_cls = repo_module.DslRepository
        orig_resolve = domain_generator.registry_patch._resolve_path
        try:
            repo_module.DslRepository = lambda: fake_repo
            domain_generator.registry_patch._resolve_path = lambda p: Path(file_path)

            result = domain_generator._register_scenarios([
                {"name": "MeetingRoom - 標準", "file": "dummy", "description": "", "tag": "X",
                 "tag_color": "#000", "domain": "meeting_room"}
            ])
        finally:
            repo_module.DslRepository = orig_repo_cls
            domain_generator.registry_patch._resolve_path = orig_resolve

        assert result[0]["status"] == "synced_from_file"
        assert result[0]["id"] == 156
        assert fake_repo.update_calls == [156]
        assert fake_repo._scenarios["MeetingRoom - 標準"]["dsl_json"] == fixed_dsl_json


if __name__ == "__main__":
    test_new_scenario_is_created()
    test_duplicate_with_identical_content_is_skipped_without_write()
    test_duplicate_with_stale_db_content_gets_synced_from_file()
    print("全テスト成功")
