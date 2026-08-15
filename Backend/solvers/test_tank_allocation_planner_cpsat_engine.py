"""
test_tank_allocation_planner_cpsat_engine.py

TankAllocationPlannerSolver の config.solver_engine="cpsat" 経路の構造テスト。
docplex/CPLEXがないサンドボックスでも実行できる。制約充足とlexicographic優先順位を検証する。
"""

from __future__ import annotations

import sys
import os

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from solvers.tank_allocation_planner_solver import TankAllocationPlannerSolver


def _scenario():
    lots = [
        {"id": "l1", "name": "Lot1", "volume": 5.0, "category": "酸性", "customer": "A"},
        {"id": "l2", "name": "Lot2", "volume": 5.0, "category": "アルカリ性", "customer": "A"},
        {"id": "l3", "name": "Lot3", "volume": 4.0, "category": "中性", "customer": "B"},
        {"id": "l4", "name": "Lot4", "volume": 3.0, "category": "中性", "customer": "B"},
    ]
    tanks = [
        {"id": "t1", "name": "Tank1", "capacity": 10.0},
        {"id": "t2", "name": "Tank2", "capacity": 10.0},
    ]
    return {
        "problem_class": "TankAllocationPlanner",
        "lots": lots,
        "tanks": tanks,
        "incompatible_pairs": [("酸性", "アルカリ性")],
        "config": {"solver_engine": "cpsat", "solve_time_sec": 15},
        "issue_statuses": {},
    }


def test_cpsat_feasible_and_respects_capacity_and_incompatibility():
    solver_input = _scenario()
    result = TankAllocationPlannerSolver(solver_input).solve()
    assert result["feasible"] is True, result["issues"]
    sol = result["solutions"][0]

    # 容量制約
    for ta in sol["tank_assignments"]:
        assert ta["total_volume"] <= ta["capacity"] + 1e-6

    # 相性制約: 酸性とアルカリ性が同じタンクに入っていない
    for ta in sol["tank_assignments"]:
        cats = [lot["category"] for lot in ta["lots"]]
        assert not ("酸性" in cats and "アルカリ性" in cats), f"相性違反: {ta}"

    # critical issueが無いこと(ソルバー抽出バグの解チェッカーが引っかかっていない)
    for iss in result["issues"]:
        assert iss["severity"] != "CRITICAL", iss


def test_cpsat_unassigned_minimized_first():
    # 全ロットが余裕をもって収まる設定なので未割当は0のはず(lex優先度1)
    solver_input = _scenario()
    result = TankAllocationPlannerSolver(solver_input).solve()
    assert result["feasible"] is True
    assert result["solutions"][0]["kpi"]["unassigned_lots"] == 0
    assert result["solutions"][0]["metrics"]["objective_unassigned"] == 0


def test_unknown_engine_raises():
    solver_input = _scenario()
    solver_input["config"]["solver_engine"] = "bogus"
    result = TankAllocationPlannerSolver(solver_input).solve()
    assert result["feasible"] is False
    assert result["issues"]


if __name__ == "__main__":
    test_cpsat_feasible_and_respects_capacity_and_incompatibility()
    test_cpsat_unassigned_minimized_first()
    test_unknown_engine_raises()
    print("ALL PASSED")
