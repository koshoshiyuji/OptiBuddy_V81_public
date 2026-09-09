"""
Backend/test_gate2_ce_limit_stress_check.py

2026-07-27追加。domain_generator.run_gate2_ce_limit_stress_check()の単体テスト。

このテストはdocplexに依存しない（本物のCP Optimizerを呼ばない）。
_inflate_list_fieldのデータ変換ロジックと、run_gate2_ce_limit_stress_check()の
ループ制御（sizes_tried/status分岐）を、フェイクのconvert_fn/solver_classで検証する。

本物のCP Optimizerエンジンに対する実機確認は、2026-07-27にこのサンドボックスで
手動実行して確認済み（4ドメイン全てでstatusが期待通りになることを確認:
NurseShiftWeeklyCap/MeetingRoom -> fallback_succeeded,
LineChangeoverScheduler/CarSequencing -> graceful_no_adapter）。
"""

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

import domain_generator as dg


def test_inflate_list_field_duplicates_with_unique_ids():
    entities = [{"id": "a", "v": 1}, {"id": "b", "v": 2}]
    out = dg._inflate_list_field(entities, target_count=5, id_field="id")
    assert len(out) == 5
    ids = [e["id"] for e in out]
    assert len(ids) == len(set(ids))  # 全てユニーク
    assert ids[:2] == ["a", "b"]  # 元のエントリはそのまま残る


def test_inflate_list_field_noop_when_already_large_enough():
    entities = [{"id": "a"}, {"id": "b"}, {"id": "c"}]
    out = dg._inflate_list_field(entities, target_count=2, id_field="id")
    assert len(out) == 3  # 既にtarget以上なので複製しない


def test_inflate_list_field_empty_entities_stays_empty():
    out = dg._inflate_list_field([], target_count=10, id_field="id")
    assert out == []


def test_not_applicable_for_unknown_domain():
    result = dg.run_gate2_ce_limit_stress_check("truck_dispatcher", None, None)
    assert result["status"] == "not_applicable"


def test_missing_baseline_when_scenario_file_absent(monkeypatch, tmp_path):
    # _CE_LIMIT_STRESS_INFLATE_FIELDに登録済みだが、_SCENARIOS_DIRを空のtmp_pathに
    # 差し替えることでbaseline未検出パスを検証する。
    monkeypatch.setattr(dg.gate2, "_SCENARIOS_DIR", tmp_path)
    result = dg.run_gate2_ce_limit_stress_check(
        "meeting_room", None, None, require_cp_engine=False
    )
    assert result["status"] == "missing_baseline"


def test_engine_unavailable_short_circuits_without_solving(monkeypatch, tmp_path):
    # cpoptimizerバイナリが無い環境（require_cp_engine=True、既定）では、
    # baselineの有無に関わらず即座にengine_unavailableで返り、
    # 一切solve()を試みないこと（実機で発覚したハングの再発防止）。
    monkeypatch.setattr(dg.gate2, "_SCENARIOS_DIR", tmp_path)
    monkeypatch.setattr(dg.shutil, "which", lambda name: None)
    _write_meeting_room_baseline(tmp_path)

    class _FakeSolverThatShouldNeverBeCalled:
        def __init__(self, solver_input):
            raise AssertionError("engine_unavailableの場合はsolver_classが呼ばれてはいけない")

    result = dg.run_gate2_ce_limit_stress_check(
        "meeting_room", convert_fn=None, solver_class=_FakeSolverThatShouldNeverBeCalled
    )
    assert result["status"] == "engine_unavailable"


class _FakeSolverGracefulNoAdapter:
    """rooms件数が閾値を超えたらce_limit_unresolvable issueを返すフェイク
    （LineChangeoverScheduler/CarSequencing側の実装パターンを模擬）。"""

    def __init__(self, solver_input):
        self.solver_input = solver_input

    def solve(self):
        rooms = self.solver_input.get("rooms", [])
        if len(rooms) > 100:
            return {"feasible": False, "issues": [{"id": "ce_limit_unresolvable"}]}
        return {"feasible": True, "issues": []}


class _FakeSolverFallbackSucceeded:
    """rooms件数が閾値を超えたら_decompose_meta付きの成功結果を返すフェイク
    （MeetingRoom/NurseShiftWeeklyCap側の実装パターンを模擬）。"""

    def __init__(self, solver_input):
        self.solver_input = solver_input

    def solve(self):
        rooms = self.solver_input.get("rooms", [])
        if len(rooms) > 100:
            return {"feasible": True, "issues": [], "_decompose_meta": {"batch_count": 3}}
        return {"feasible": True, "issues": []}


def _write_meeting_room_baseline(tmp_path):
    scenario = {
        "meta": {"instance_name": "test"},
        "rooms": [{"id": f"r{i}"} for i in range(5)],
        "meetings": [{"id": "m1"}],
        "config": {"solve_time_sec": 30},
    }
    (tmp_path / "meeting_room_baseline.json").write_text(json.dumps(scenario), encoding="utf-8")


def test_graceful_no_adapter_status_when_no_decompose_meta(monkeypatch, tmp_path):
    monkeypatch.setattr(dg.gate2, "_SCENARIOS_DIR", tmp_path)
    _write_meeting_room_baseline(tmp_path)

    result = dg.run_gate2_ce_limit_stress_check(
        "meeting_room", convert_fn=None, solver_class=_FakeSolverGracefulNoAdapter,
        require_cp_engine=False,
    )
    assert result["status"] == "graceful_no_adapter"
    assert result["triggered_at"] > 100
    assert "ce_limit_unresolvable" in result["issue_ids"]


def test_fallback_succeeded_status_when_decompose_meta_present(monkeypatch, tmp_path):
    monkeypatch.setattr(dg.gate2, "_SCENARIOS_DIR", tmp_path)
    _write_meeting_room_baseline(tmp_path)

    result = dg.run_gate2_ce_limit_stress_check(
        "meeting_room", convert_fn=None, solver_class=_FakeSolverFallbackSucceeded,
        require_cp_engine=False,
    )
    assert result["status"] == "fallback_succeeded"
    assert result["decompose_meta"] == {"batch_count": 3}


class _FakeSolverNeverTriggers:
    def __init__(self, solver_input):
        pass

    def solve(self):
        return {"feasible": True, "issues": []}


def test_ce_limit_not_triggered_when_fake_solver_never_fails(monkeypatch, tmp_path):
    monkeypatch.setattr(dg.gate2, "_SCENARIOS_DIR", tmp_path)
    _write_meeting_room_baseline(tmp_path)

    result = dg.run_gate2_ce_limit_stress_check(
        "meeting_room", convert_fn=None, solver_class=_FakeSolverNeverTriggers, max_attempts=3,
        require_cp_engine=False,
    )
    assert result["status"] == "ce_limit_not_triggered"
    assert len(result["sizes_tried"]) == 3


class _FakeSolverRaisesUnrelatedException:
    def __init__(self, solver_input):
        pass

    def solve(self):
        raise RuntimeError("unrelated bug")


def test_exception_status_propagates_traceback_info(monkeypatch, tmp_path):
    monkeypatch.setattr(dg.gate2, "_SCENARIOS_DIR", tmp_path)
    _write_meeting_room_baseline(tmp_path)

    result = dg.run_gate2_ce_limit_stress_check(
        "meeting_room", convert_fn=None, solver_class=_FakeSolverRaisesUnrelatedException,
        require_cp_engine=False,
    )
    assert result["status"] == "exception"
    assert "unrelated bug" in result["traceback"]


if __name__ == "__main__":
    import pytest
    raise SystemExit(pytest.main([__file__, "-v"]))
