"""
test_depot_route_planner_cpsat_engine.py

DepotRoutePlannerSolver._stage2_cpsat() の構造テスト。
docplex/CPLEXがないサンドボックスでも実行できる。brute-forceで最適値を独立検証する。

【注意】本ドメインはStage1がdocplex.mp(CPLEX MIP)固定であり、config.solver_engine
の対象はStage2(CP側のTSP)のみ。Stage1は"cpsat"を指定してもdocplex.mp依存が残る
ため、solve()全体をCPLEXなし環境で実行することはできない。本テストは
_stage2_cpsat()を直接呼び出し、Stage2部分の正しさのみを独立検証する。
"""

from __future__ import annotations

import sys
import os
import itertools

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from solvers.depot_route_planner_solver import DepotRoutePlannerSolver, _manhattan_xy


def _scenario():
    depot = {"id": "D1", "name": "Depot1", "x": 0.0, "y": 0.0}
    customers = [
        {"id": "C1", "name": "Cust1", "x": 3.0, "y": 1.0},
        {"id": "C2", "name": "Cust2", "x": 5.0, "y": 5.0},
        {"id": "C3", "name": "Cust3", "x": -2.0, "y": 4.0},
        {"id": "C4", "name": "Cust4", "x": 1.0, "y": -3.0},
        {"id": "C5", "name": "Cust5", "x": -4.0, "y": -1.0},
    ]
    return depot, customers


def _brute_force_optimal(depot, customers):
    locs = [depot] + customers
    n_locs = len(locs)
    dist_mat = [[_manhattan_xy(locs[i], locs[j]) for j in range(n_locs)] for i in range(n_locs)]
    n = len(customers)
    best = None
    for perm in itertools.permutations(range(1, n + 1)):
        total = 0.0
        prev = 0
        for idx in perm:
            total += dist_mat[prev][idx]
            prev = idx
        total += dist_mat[prev][0]
        if best is None or total < best:
            best = total
    return best


def test_stage2_cpsat_matches_brute_force_optimum():
    depot, customers = _scenario()
    solver = DepotRoutePlannerSolver({})
    order, dist = solver._stage2_cpsat(depot, customers, {"stage2_time_limit_sec": 15})

    expected = _brute_force_optimal(depot, customers)
    assert abs(dist - expected) < 1e-3, f"cpsat={dist} vs brute_force={expected}"

    # 全顧客を1回ずつ訪問しているか
    visited_ids = {o["customer_id"] for o in order}
    assert visited_ids == {c["id"] for c in customers}
    assert len(order) == len(customers)


def test_stage2_cpsat_single_customer():
    depot, customers = _scenario()
    solver = DepotRoutePlannerSolver({})
    single = [customers[0]]
    order, dist = solver._stage2_cpsat(depot, single, {"stage2_time_limit_sec": 5})
    expected = _manhattan_xy(depot, single[0]) * 2
    assert abs(dist - expected) < 1e-6
    assert len(order) == 1


if __name__ == "__main__":
    test_stage2_cpsat_matches_brute_force_optimum()
    test_stage2_cpsat_single_customer()
    print("ALL PASSED")
