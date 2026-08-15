"""
Backend/solvers/test_line_changeover_scheduler_cpsat_engine.py

2026-08-13追加。LineChangeoverSchedulerへの config.solver_engine="cpsat"
（OR-Tools CP-SAT、CPMpy経由）明示選択エンジンの配線テスト。

Backend/tools/pilot_cpmpy_line_changeover_scheduler.py で全探索(brute force)
により独立検証済みの2シナリオをそのまま本番クラス
LineChangeoverSchedulerSolver経由で再検証する。docplexには依存しない
（CP-SATエンジンの存在意義そのものが「CPLEX/CP Optimizerが無い環境でも動く」
ことなので、意図的にdocplexをimportしない）。
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from solvers.line_changeover_scheduler_solver import LineChangeoverSchedulerSolver


def _solver_input(tasks, resources, precedences=None, config=None):
    return {
        "problem_class": "LineChangeoverScheduler",
        "meta": {"instance_name": "cpsat_engine_test"},
        "tasks": tasks,
        "resources": resources,
        "precedences": precedences or [],
        "config": {**(config or {}), "solver_engine": "cpsat"},
        "issue_statuses": {},
    }


def _scenario_single_resource_serial():
    """pilot_cpmpy_line_changeover_scheduler.py の_scenario_single_resource_serial()
    と同一。資源容量=1で3タスクが直列化を強制される→最適メイクスパンは
    sum(duration)=2+3+2=7になるはず。"""
    tasks = [
        {"id": "A", "duration": 2, "resource_requirements": [{"resource_id": "R1", "amount": 1}]},
        {"id": "B", "duration": 3, "resource_requirements": [{"resource_id": "R1", "amount": 1}]},
        {"id": "C", "duration": 2, "resource_requirements": [{"resource_id": "R1", "amount": 1}]},
    ]
    resources = [{"id": "R1", "capacity": 1}]
    return tasks, resources


def _scenario_parallel_with_precedence():
    """pilot_cpmpy_line_changeover_scheduler.py の_scenario_parallel_with_precedence()
    と同一。資源容量=2で一部並列化可能 + 前後関係(A→C, delay=1)。
    全探索による真の最適メイクスパンは5。"""
    tasks = [
        {"id": "A", "duration": 2, "resource_requirements": [{"resource_id": "R1", "amount": 1}]},
        {"id": "B", "duration": 3, "resource_requirements": [{"resource_id": "R1", "amount": 1}]},
        {"id": "C", "duration": 2, "resource_requirements": [{"resource_id": "R1", "amount": 1}]},
        {"id": "D", "duration": 1, "resource_requirements": [{"resource_id": "R1", "amount": 1}]},
    ]
    resources = [{"id": "R1", "capacity": 2}]
    precedences = [{"from_task": "A", "to_task": "C", "min_delay": 1}]
    return tasks, resources, precedences


def test_cpsat_engine_forced_serial_matches_pilot_makespan():
    tasks, resources = _scenario_single_resource_serial()
    result = LineChangeoverSchedulerSolver(
        _solver_input(tasks, resources, config={"horizon": 10})
    ).solve()

    assert result["status"] == "ok"
    assert result["feasible"] is True
    sol = result["solutions"][0]
    assert sol["makespan"] == 7
    assert sol["kpi"]["objective_value"] == 7
    assert len(sol["schedule"]) == 3

    # 資源容量1なので、どの2タスクを取っても時間が重ならないこと
    intervals = sorted((s["start"], s["end"]) for s in sol["schedule"])
    for i in range(len(intervals) - 1):
        assert intervals[i][1] <= intervals[i + 1][0]


def test_cpsat_engine_parallel_with_precedence_matches_pilot_makespan():
    tasks, resources, precedences = _scenario_parallel_with_precedence()
    result = LineChangeoverSchedulerSolver(
        _solver_input(tasks, resources, precedences, config={"horizon": 10})
    ).solve()

    assert result["status"] == "ok"
    assert result["feasible"] is True
    sol = result["solutions"][0]
    assert sol["makespan"] == 5

    # 前後関係制約 end(A)+1 <= start(C) を満たしていること
    by_id = {s["task_id"]: s for s in sol["schedule"]}
    assert by_id["A"]["end"] + 1 <= by_id["C"]["start"]


def test_cpsat_engine_kpi_has_same_shape_as_cpo_path():
    tasks, resources = _scenario_single_resource_serial()
    result = LineChangeoverSchedulerSolver(
        _solver_input(tasks, resources, config={"horizon": 10})
    ).solve()
    kpi = result["solutions"][0]["kpi"]
    assert set(kpi.keys()) == {"makespan", "task_count", "objective_value", "solve_time"}


if __name__ == "__main__":
    import pytest
    raise SystemExit(pytest.main([__file__, "-v"]))
