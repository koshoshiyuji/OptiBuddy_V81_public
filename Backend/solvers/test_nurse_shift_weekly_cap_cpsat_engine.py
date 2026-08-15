"""
test_nurse_shift_weekly_cap_cpsat_engine.py

NurseShiftWeeklyCapSolver の config.solver_engine="cpsat" 経路の構造テスト。
docplex/CPLEXがないサンドボックスでも実行できる。

シナリオはBackend/tools/pilot_cpmpy_nurse_shift_weekly_cap.py の
_scenario_flexible_conflict() を本番solver_input形式に移植したもの
（真にフレキシブルな開始時刻・no_overlap・夜勤後休憩(end_before_start+delay)
という本ドメイン最大の技術的難所を、brute-force照合済みのパイロットと同一の
構造で検証する）。
"""

from __future__ import annotations

import sys
import os

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from solvers.nurse_shift_weekly_cap_solver import NurseShiftWeeklyCapSolver


def _scenario_flexible_conflict():
    staff = [
        {"id": "S1", "hourly_rate": 10, "availability": {"start": -1_000_000, "end": 1_000_000},
         "work_limits": {"min_rest_after_night_shift": 160}},
        {"id": "S2", "hourly_rate": 20, "availability": {"start": -1_000_000, "end": 1_000_000},
         "work_limits": {"min_rest_after_night_shift": 160}},
    ]
    tasks = [
        {"id": "T1", "day": 1, "start_window": 0,   "end_window": 120, "duration": 60, "is_night_shift": False, "required_count": 1},
        {"id": "T2", "day": 1, "start_window": 200, "end_window": 260, "duration": 60, "is_night_shift": True,  "required_count": 1},
        {"id": "T3", "day": 2, "start_window": 400, "end_window": 520, "duration": 60, "is_night_shift": False, "required_count": 1},
        {"id": "T4", "day": 1, "start_window": 40,  "end_window": 100, "duration": 60, "is_night_shift": False, "required_count": 1},
    ]
    return {
        "staff": staff,
        "tasks": tasks,
        "config": {"solver_engine": "cpsat", "time_limit": 10, "preference_penalty_weight": 200},
        "issue_statuses": {},
    }


def test_cpsat_flexible_scheduling_optimal_cost():
    """真にフレキシブルなstart変数のもとで、no_overlap・夜勤後休憩(delay込み)を
    守りつつコスト最小(=50円、パイロットのbrute-force最適値と一致)を達成すること。"""
    result = NurseShiftWeeklyCapSolver(_scenario_flexible_conflict()).solve()
    assert result["feasible"] is True, result.get("issues")
    sol = result["solutions"][0]
    assert sol["metrics"]["total_cost"] == 50
    assert sol["metrics"]["understaffed_count"] == 0

    tasks_by_id = {t["task_id"]: t for t in sol["tasks"]}
    assert set(tasks_by_id.keys()) == {"T1", "T2", "T3", "T4"}

    # T1とT4は同一スタッフに割り当てられない（NoOverlapOptionalの構造的排他）
    assert tasks_by_id["T1"]["staff_id"] != tasks_by_id["T4"]["staff_id"]

    # T2(night)とT3が同一スタッフなら、夜勤後休憩(160分)が守られている
    if tasks_by_id["T2"]["staff_id"] == tasks_by_id["T3"]["staff_id"]:
        gap = tasks_by_id["T3"]["start"] - tasks_by_id["T2"]["end"]
        assert gap >= 160

    # 資源(スタッフ)の二重予約が無いこと(独立検証)
    usage = {}
    for t in sol["tasks"]:
        usage.setdefault(t["staff_id"], []).append((t["start"], t["end"]))
    for sid, intervals in usage.items():
        intervals.sort()
        for i in range(len(intervals) - 1):
            assert intervals[i][1] <= intervals[i + 1][0], f"スタッフ{sid}の二重予約: {intervals}"


def test_cpsat_min_chiefs_hard_infeasible_when_no_chief_candidate():
    """min_chiefsを満たすCHIEF候補が0件の場合、構造的にinfeasibleになること
    （本番CPOパスと同じ「明示的contradiction制約」パターンの検証）。"""
    solver_input = _scenario_flexible_conflict()
    solver_input["tasks"] = [
        {"id": "T1", "day": 1, "start_window": 0, "end_window": 120, "duration": 60,
         "is_night_shift": False, "required_count": 1, "min_chiefs": 1},
    ]
    # staffは全員grade=STAFF(既定)なのでCHIEF候補が0件
    result = NurseShiftWeeklyCapSolver(solver_input).solve()
    assert result["feasible"] is False


def test_cpsat_min_chiefs_satisfied_when_chief_present():
    solver_input = _scenario_flexible_conflict()
    solver_input["staff"][0]["grade"] = "CHIEF"
    solver_input["tasks"] = [
        {"id": "T1", "day": 1, "start_window": 0, "end_window": 120, "duration": 60,
         "is_night_shift": False, "required_count": 1, "min_chiefs": 1},
    ]
    result = NurseShiftWeeklyCapSolver(solver_input).solve()
    assert result["feasible"] is True
    sol = result["solutions"][0]
    assert sol["tasks"][0]["staff_id"] == "S1"
    assert sol["tasks"][0]["grade"] == "CHIEF"


def test_cpsat_soft_quantity_requirement_understaffed_allowed():
    """quantity_requirement_mode既定(soft)で、担当可能スタッフが不足していても
    infeasibleにならず、understaffed_countとして計上されること。"""
    solver_input = _scenario_flexible_conflict()
    solver_input["staff"] = [
        {"id": "S1", "hourly_rate": 10, "availability": {"start": -1_000_000, "end": 1_000_000},
         "work_limits": {}},
    ]
    solver_input["tasks"] = [
        {"id": "T1", "day": 1, "start_window": 0, "end_window": 120, "duration": 60,
         "is_night_shift": False, "required_count": 2},
    ]
    result = NurseShiftWeeklyCapSolver(solver_input).solve()
    assert result["feasible"] is True
    sol = result["solutions"][0]
    assert sol["metrics"]["understaffed_count"] == 1


def test_cpsat_daily_hours_cap_respected():
    """日次労働時間上限(max_daily_hours)を超える割り当てが選ばれないこと。"""
    solver_input = _scenario_flexible_conflict()
    solver_input["staff"] = [
        {"id": "S1", "hourly_rate": 10, "availability": {"start": -1_000_000, "end": 1_000_000},
         "work_limits": {"max_daily_hours": 1}},  # 60分/日まで
    ]
    solver_input["tasks"] = [
        {"id": "T1", "day": 1, "start_window": 0,  "end_window": 60,  "duration": 60, "is_night_shift": False, "required_count": 1},
        {"id": "T2", "day": 1, "start_window": 60, "end_window": 120, "duration": 60, "is_night_shift": False, "required_count": 1},
    ]
    result = NurseShiftWeeklyCapSolver(solver_input).solve()
    assert result["feasible"] is True
    sol = result["solutions"][0]
    # S1しかいないので、日次上限(60分)により両方は割り当てられない
    assert sol["metrics"]["understaffed_count"] == 1
    assert len(sol["tasks"]) == 1


def test_unknown_engine_raises():
    solver_input = _scenario_flexible_conflict()
    solver_input["config"]["solver_engine"] = "bogus"
    result = NurseShiftWeeklyCapSolver(solver_input).solve()
    assert result["feasible"] is False
    assert result["issues"]


if __name__ == "__main__":
    test_cpsat_flexible_scheduling_optimal_cost()
    test_cpsat_min_chiefs_hard_infeasible_when_no_chief_candidate()
    test_cpsat_min_chiefs_satisfied_when_chief_present()
    test_cpsat_soft_quantity_requirement_understaffed_allowed()
    test_cpsat_daily_hours_cap_respected()
    test_unknown_engine_raises()
    print("ALL PASSED")
