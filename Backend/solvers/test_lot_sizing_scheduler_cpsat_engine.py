"""
test_lot_sizing_scheduler_cpsat_engine.py

LotSizingSchedulerSolver の config.solver_engine="cpsat" 経路の構造テスト。
docplex/CPLEXがないサンドボックスでも実行できる。brute-forceで最適値を独立検証する。
"""

from __future__ import annotations

import sys
import os
import itertools

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from solvers.lot_sizing_scheduler_solver import LotSizingSchedulerSolver


def _scenario():
    orders = [
        {"id": "o1", "product_id": "PA", "quantity": 10, "due_period": 3, "holding_cost_per_period": 2.0},
        {"id": "o2", "product_id": "PB", "quantity": 5,  "due_period": 3, "holding_cost_per_period": 1.5},
        {"id": "o3", "product_id": "PA", "quantity": 8,  "due_period": 4, "holding_cost_per_period": 1.0},
        {"id": "o4", "product_id": "PB", "quantity": 6,  "due_period": 4, "holding_cost_per_period": 2.5},
    ]
    periods = [1, 2, 3, 4]
    setup_costs = [
        {"from_product": "PA", "to_product": "PB", "cost": 50.0},
        {"from_product": "PB", "to_product": "PA", "cost": 30.0},
    ]
    return {
        "problem_class": "LotSizingScheduler",
        "orders": orders,
        "periods": periods,
        "setup_costs": setup_costs,
        "config": {"solver_engine": "cpsat", "time_limit_sec": 15},
        "issue_statuses": {},
    }


def brute_force_optimal(orders, periods, setup_costs):
    setup_cost_map = {(sc["from_product"], sc["to_product"]): sc["cost"] for sc in setup_costs}
    n = len(orders)
    best = None
    for perm_periods in itertools.permutations(periods, n):
        # perm_periods[i] = period assigned to orders[i]
        feasible = True
        for i, o in enumerate(orders):
            if perm_periods[i] > o["due_period"]:
                feasible = False
                break
        if not feasible:
            continue

        holding_total = 0.0
        for i, o in enumerate(orders):
            holding_total += (o["due_period"] - perm_periods[i]) * o["holding_cost_per_period"] * o["quantity"]

        # 実生産順(period昇順)で隣接製品切替コスト
        order_idx_sorted = sorted(range(n), key=lambda i: perm_periods[i])
        setup_total = 0.0
        for k in range(len(order_idx_sorted) - 1):
            i, j = order_idx_sorted[k], order_idx_sorted[k + 1]
            pi, pj = orders[i]["product_id"], orders[j]["product_id"]
            if pi != pj:
                setup_total += setup_cost_map.get((pi, pj), 0.0)

        total = holding_total + setup_total
        if best is None or total < best:
            best = total
    return best


def test_cpsat_matches_brute_force_optimum():
    solver_input = _scenario()
    result = LotSizingSchedulerSolver(solver_input).solve()
    assert result["feasible"] is True, result["issues"]
    cpsat_obj = result["solutions"][0]["kpi"]["objective_value"]

    expected = brute_force_optimal(solver_input["orders"], solver_input["periods"], solver_input["setup_costs"])
    assert abs(cpsat_obj - expected) < 0.05, f"cpsat={cpsat_obj} vs brute_force={expected}"


def test_cpsat_all_orders_assigned_distinct_periods():
    solver_input = _scenario()
    result = LotSizingSchedulerSolver(solver_input).solve()
    assert result["feasible"] is True
    assignments = result["solutions"][0]["assignments"]
    assert len(assignments) == len(solver_input["orders"])
    periods_used = [a["assigned_period"] for a in assignments]
    assert len(periods_used) == len(set(periods_used)), "1期間1件制約違反"
    for a in assignments:
        assert a["assigned_period"] <= a["due_period"]


def test_unknown_engine_raises():
    solver_input = _scenario()
    solver_input["config"]["solver_engine"] = "bogus"
    result = LotSizingSchedulerSolver(solver_input).solve()
    assert result["feasible"] is False
    assert result["issues"]


if __name__ == "__main__":
    test_cpsat_matches_brute_force_optimum()
    test_cpsat_all_orders_assigned_distinct_periods()
    test_unknown_engine_raises()
    print("ALL PASSED")
