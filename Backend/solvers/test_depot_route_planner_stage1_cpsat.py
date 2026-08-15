"""
test_depot_route_planner_stage1_cpsat.py

DepotRoutePlannerSolver の Stage1(施設配置)cpsat経路(_stage1_cpsat)の構造テスト。
2026-08-13追加: Stage1がdocplex.mp(CPLEX MIP)無しで動くようになったことの検証。
これによりconfig.solver_engine="cpsat"を指定すれば、DepotRoutePlanner全体が
docplex(CPLEX)を一切インストールしていない環境でも動作する。
"""

from __future__ import annotations

import sys
import os
import itertools

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from solvers.depot_route_planner_solver import DepotRoutePlannerSolver, _manhattan_xy


def _brute_force_stage1(depots, customers, max_open):
    """全開設拠点部分集合 × 最近傍割当で真の最適値を求める(小規模専用)。
    施設配置は「拠点部分集合が決まれば、各顧客は最も近い開設拠点に割り当てるのが
    最適」という分離可能な構造を持つため、全探索対象は拠点の部分集合だけでよい。"""
    best = None
    for r in range(1, len(depots) + 1):
        if r > max_open:
            continue
        for subset in itertools.combinations(depots, r):
            assign_cost = sum(min(_manhattan_xy(c, d) for d in subset) for c in customers)
            open_cost = sum(d["opening_cost"] for d in subset)
            total = open_cost + assign_cost
            if best is None or total < best[0] - 1e-9:
                best = (total, {d["id"] for d in subset})
    return best


def test_stage1_cpsat_matches_brute_force():
    depots = [
        {"id": "D1", "name": "Depot1", "x": 0.0, "y": 0.0, "opening_cost": 50.0},
        {"id": "D2", "name": "Depot2", "x": 10.0, "y": 0.0, "opening_cost": 30.0},
        {"id": "D3", "name": "Depot3", "x": 5.0, "y": 8.0, "opening_cost": 40.0},
    ]
    customers = [
        {"id": "C1", "x": 1.0, "y": 1.0},
        {"id": "C2", "x": 9.0, "y": 1.0},
        {"id": "C3", "x": 4.0, "y": 7.0},
        {"id": "C4", "x": 6.0, "y": 9.0},
    ]
    config = {"max_open_depots": 2, "stage1_time_limit_sec": 10}

    bf_cost, bf_open_ids = _brute_force_stage1(depots, customers, config["max_open_depots"])

    solver = DepotRoutePlannerSolver({
        "depots": depots, "customers": customers,
        "config": {**config, "solver_engine": "cpsat"}, "issue_statuses": {},
    })
    open_depots, assignments, approx_cost = solver._stage1_cpsat(depots, customers, config)

    assert abs(approx_cost - bf_cost) < 1e-6
    assert {d["id"] for d in open_depots} == bf_open_ids
    # 割当が本当に開設拠点内であること
    for cid, did in assignments.items():
        assert did in bf_open_ids


def test_stage1_cpsat_respects_max_open_depots():
    depots = [
        {"id": "D1", "x": 0.0, "y": 0.0, "opening_cost": 10.0},
        {"id": "D2", "x": 100.0, "y": 0.0, "opening_cost": 10.0},
        {"id": "D3", "x": 0.0, "y": 100.0, "opening_cost": 10.0},
    ]
    customers = [
        {"id": "C1", "x": 1.0, "y": 1.0},
        {"id": "C2", "x": 99.0, "y": 1.0},
        {"id": "C3", "x": 1.0, "y": 99.0},
    ]
    config = {"max_open_depots": 1, "stage1_time_limit_sec": 10}
    solver = DepotRoutePlannerSolver({
        "depots": depots, "customers": customers,
        "config": {**config, "solver_engine": "cpsat"}, "issue_statuses": {},
    })
    open_depots, assignments, approx_cost = solver._stage1_cpsat(depots, customers, config)
    assert len(open_depots) == 1
    assert len(set(assignments.values())) == 1


def test_end_to_end_cpsat_no_docplex_needed():
    """Stage1・Stage2ともにcpsatで、docplexを一切importせずに完走すること
    （本サンドボックスにはdocplexが無いため、ここでfeasible=Trueになれば
    「CPLEX無しでDepotRoutePlannerが動く」ことの直接証拠になる）。"""
    depots = [
        {"id": "D1", "name": "Depot1", "x": 0.0, "y": 0.0, "opening_cost": 100.0},
        {"id": "D2", "name": "Depot2", "x": 10.0, "y": 10.0, "opening_cost": 100.0},
    ]
    customers = [
        {"id": "C1", "name": "Cust1", "x": 1.0, "y": 1.0},
        {"id": "C2", "name": "Cust2", "x": 2.0, "y": -1.0},
        {"id": "C3", "name": "Cust3", "x": -1.0, "y": 2.0},
        {"id": "C4", "name": "Cust4", "x": 9.0, "y": 11.0},
        {"id": "C5", "name": "Cust5", "x": 11.0, "y": 9.0},
    ]
    solver_input = {
        "problem_class": "DepotRoutePlanner", "depots": depots, "customers": customers,
        "config": {"solver_engine": "cpsat", "max_open_depots": 2,
                   "stage1_time_limit_sec": 15, "stage2_time_limit_sec": 15},
        "issue_statuses": {},
    }
    result = DepotRoutePlannerSolver(solver_input).solve()
    assert result["feasible"] is True
    sol = result["solutions"][0]
    assert sol["kpi"]["num_customers"] == 5
    assert len(sol["assignments"]) == 5
    assert all(a["depot_id"] is not None for a in sol["assignments"])


def test_unknown_engine_raises():
    depots = [{"id": "D1", "x": 0.0, "y": 0.0, "opening_cost": 10.0}]
    customers = [{"id": "C1", "x": 1.0, "y": 1.0}]
    solver_input = {
        "depots": depots, "customers": customers,
        "config": {"solver_engine": "bogus"}, "issue_statuses": {},
    }
    result = DepotRoutePlannerSolver(solver_input).solve()
    assert result["feasible"] is False
    assert result["issues"]


if __name__ == "__main__":
    test_stage1_cpsat_matches_brute_force()
    test_stage1_cpsat_respects_max_open_depots()
    test_end_to_end_cpsat_no_docplex_needed()
    test_unknown_engine_raises()
    print("ALL PASSED")
