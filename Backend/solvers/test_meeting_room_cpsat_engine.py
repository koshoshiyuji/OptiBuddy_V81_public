"""
Backend/solvers/test_meeting_room_cpsat_engine.py

2026-08-13追加。MeetingRoomへの config.solver_engine="cpsat"（OR-Tools CP-SAT、
CPMpy経由）明示選択エンジンの配線テスト。

Backend/tools/pilot_cpmpy_meeting_room.py で全探索(brute force)により独立検証
済みの2シナリオ（_scenario() / _scenario_conflict()）をそのまま本番クラス
MeetingRoomSolver経由で再検証する。docplexには依存しない
（CP-SATエンジンの存在意義そのものが「CPLEX/CP Optimizerが無い環境でも動く」
ことなので、意図的にdocplexをimportしない）。
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from solvers.meeting_room_solver import MeetingRoomSolver


def _solver_input(meetings, rooms, config=None):
    return {
        "meta": {"instance_name": "cpsat_engine_test"},
        "problem_class": "MeetingRoom",
        "meetings": meetings,
        "rooms": rooms,
        "config": {**(config or {}), "solver_engine": "cpsat"},
        "issue_statuses": {},
    }


def _scenario_normal():
    """pilot_cpmpy_meeting_room.py の_scenario()と同一。ブルートフォースの
    真の最適値は -0.1（bonus）+ 1（waste M2の5-4） = 0.9。"""
    meetings = [
        {"id": "M1", "dept": "X", "start_min": 0, "end_min": 60, "attendees": 5},
        {"id": "M2", "dept": "X", "start_min": 60, "end_min": 120, "attendees": 4},
        {"id": "M3", "dept": "Y", "start_min": 30, "end_min": 90, "attendees": 3},
    ]
    rooms = [
        {"id": "R1", "capacity": 5, "available_start_min": 0, "available_end_min": 1440},
        {"id": "R2", "capacity": 3, "available_start_min": 0, "available_end_min": 1440},
    ]
    return meetings, rooms


def _scenario_conflict():
    """pilot_cpmpy_meeting_room.py の_scenario_conflict()と同一。
    R1しか入らない2会議が時間的に衝突 → 片方は未割当になるはず。"""
    meetings = [
        {"id": "M1", "dept": None, "start_min": 0, "end_min": 90, "attendees": 5},
        {"id": "M2", "dept": None, "start_min": 30, "end_min": 60, "attendees": 5},
    ]
    rooms = [{"id": "R1", "capacity": 5, "available_start_min": 0, "available_end_min": 1440}]
    return meetings, rooms


def test_cpsat_engine_normal_scenario_matches_pilot_result():
    meetings, rooms = _scenario_normal()
    result = MeetingRoomSolver(_solver_input(meetings, rooms)).solve()

    assert result["status"] == "ok"
    assert result["feasible"] is True
    kpi = result["solutions"][0]["kpi"]
    assert kpi["assigned_count"] == 3
    assert kpi["unassigned_count"] == 0
    assert abs(kpi["objective_value"] - 0.9) < 1e-6

    # M1/M2は同部署(X)・連続時間帯なので同室に割り当てられる（ボーナス対象）はず
    by_id = {a["meeting_id"]: a for a in result["solutions"][0]["assignments"]}
    assert by_id["M1"]["room_id"] == by_id["M2"]["room_id"]


def test_cpsat_engine_conflict_scenario_leaves_one_unassigned():
    meetings, rooms = _scenario_conflict()
    result = MeetingRoomSolver(_solver_input(meetings, rooms)).solve()

    assert result["status"] == "ok"
    assert result["feasible"] is False  # 全会議は割り当てられない
    kpi = result["solutions"][0]["kpi"]
    assert kpi["assigned_count"] == 1
    assert kpi["unassigned_count"] == 1


def test_cpsat_engine_kpi_has_same_shape_as_cpo_path():
    meetings, rooms = _scenario_normal()
    result = MeetingRoomSolver(_solver_input(meetings, rooms)).solve()
    kpi = result["solutions"][0]["kpi"]
    assert set(kpi.keys()) == {
        "total_meetings", "assigned_count", "unassigned_count",
        "total_rooms", "objective_value",
    }
    assert set(result["solutions"][0]["optimality"].keys()) == {
        "solve_status", "is_optimal", "solve_time_sec",
    }


if __name__ == "__main__":
    import pytest
    raise SystemExit(pytest.main([__file__, "-v"]))
