"""
test_refresh_scenario.py

2026-07-23追記の登録済みシナリオ「リフレッシュ」機能に対する回帰テスト。
DB(SQLite)には依存せず、DslRepository相当のフェイクを差し込んでロジックだけを
検証する（test_register_scenarios_sync.pyと同じ方針）。

設計方針: サーバー側でファイルパスを探索・保存することは一切せず、ユーザーが
ローカルで選んだJSONファイルの中身（Frontendが読み込んでリクエストボディに
そのまま載せる）で、指定scenario_idのdsl_jsonを単純に上書きする。
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

import domain_generator


class _FakeRepo:
    def __init__(self, scenarios_by_id):
        self._scenarios = dict(scenarios_by_id)
        self.update_calls = []

    def get_scenario(self, scenario_id):
        return self._scenarios.get(scenario_id)

    def update_scenario(self, scenario_id, dsl_json=None, **kwargs):
        self._scenarios[scenario_id]["dsl_json"] = dsl_json
        self.update_calls.append(scenario_id)
        return True


def _with_patched_repo(fake_repo, fn):
    import dsl_repository.repository as repo_module
    orig = repo_module.DslRepository
    try:
        repo_module.DslRepository = lambda: fake_repo
        return fn()
    finally:
        repo_module.DslRepository = orig


def test_refresh_not_found():
    fake_repo = _FakeRepo({})
    result = _with_patched_repo(
        fake_repo, lambda: domain_generator.refresh_scenario_from_upload(999, {"a": 1}))
    assert result["status"] == "not_found"


def test_refresh_no_change_when_identical():
    dsl_json = {"problem_class": "TruckDispatcher", "config": {"lns_max_iterations": 400}}
    fake_repo = _FakeRepo({1: {"id": 1, "name": "TruckDispatcher — 標準", "dsl_json": dict(dsl_json)}})
    result = _with_patched_repo(
        fake_repo, lambda: domain_generator.refresh_scenario_from_upload(1, dict(dsl_json)))
    assert result["status"] == "no_change"
    assert fake_repo.update_calls == []


def test_refresh_updated_when_different():
    stale = {"problem_class": "TruckDispatcher", "config": {"lns_max_iterations": 100}}
    uploaded = {"problem_class": "TruckDispatcher", "config": {"lns_max_iterations": 400}}
    fake_repo = _FakeRepo({1: {"id": 1, "name": "TruckDispatcher — 標準", "dsl_json": stale}})
    result = _with_patched_repo(
        fake_repo, lambda: domain_generator.refresh_scenario_from_upload(1, uploaded))
    assert result["status"] == "updated"
    assert fake_repo.update_calls == [1]
    assert fake_repo._scenarios[1]["dsl_json"]["config"]["lns_max_iterations"] == 400


if __name__ == "__main__":
    test_refresh_not_found()
    test_refresh_no_change_when_identical()
    test_refresh_updated_when_different()
    print("全テスト成功")
