"""
test_energy_cost_aware_scheduler_cpsat_engine.py

EnergyCostAwareSchedulerSolver の config.solver_engine="cpsat" 経路の構造テスト。
docplex/CPLEXがないサンドボックスでも実行できる。制約充足を独立検証する。
"""

from __future__ import annotations

import sys
import os

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from solvers.energy_cost_aware_scheduler_solver import EnergyCostAwareSchedulerSolver


def _scenario():
    orders = [
        {"id": "o1", "name": "Order1", "duration_min": 60, "earliest_start_min": 0,
         "deadline_min": 480, "power_kw": 5.0, "workers": 2, "equipment_slots": 1, "customer": "CustA"},
        {"id": "o2", "name": "Order2", "duration_min": 60, "earliest_start_min": 0,
         "deadline_min": 480, "power_kw": 4.0, "workers": 2, "equipment_slots": 1, "customer": "CustB"},
        {"id": "o3", "name": "Order3", "duration_min": 60, "earliest_start_min": 0,
         "deadline_min": 480, "power_kw": 3.0, "workers": 1, "equipment_slots": 1, "customer": "CustA"},
    ]
    lines = [
        {"id": "L1", "name": "Line1", "max_power_kw": 8.0, "max_workers": 3, "max_equipment_slots": 2,
         "standby_power_kw": 0.5, "startup_cost": 10.0, "shutdown_cost": 5.0},
    ]
    tariffs = [
        {"slot_start": 0, "slot_end": 480, "price_per_kwh": 20.0},
    ]
    return {
        "problem_class": "EnergyCostAwareScheduler",
        "orders": orders,
        "lines": lines,
        "tariffs": tariffs,
        "config": {"solver_engine": "cpsat", "time_limit_sec": 20, "horizon_min": 480},
        "issue_statuses": {},
    }


def test_cpsat_feasible_respects_power_cap_and_deadlines():
    solver_input = _scenario()
    result = EnergyCostAwareSchedulerSolver(solver_input).solve()
    assert result["feasible"] is True, result["issues"]
    sol = result["solutions"][0]
    schedule = sol["schedule"]

    assert len(schedule) >= 1

    for s in schedule:
        assert s["end"] <= s["deadline_min"]

    # ライン電力上限チェック(独立検証: 時間軸を1分刻みで走査し合計消費が上限以下か)
    line_map = {l["id"]: l for l in solver_input["lines"]}
    for lid, ln in line_map.items():
        max_power = ln["max_power_kw"]
        line_orders = [s for s in schedule if s["line_id"] == lid]
        if not line_orders:
            continue
        horizon = solver_input["config"]["horizon_min"]
        for t in range(horizon):
            total = sum(s["power_kw"] for s in line_orders if s["start"] <= t < s["end"])
            assert total <= max_power + 1e-6, f"t={t}: total_power={total} > max={max_power}"

    for iss in result["issues"]:
        assert iss["severity"] != "CRITICAL", iss


def test_cpsat_kpi_shape():
    solver_input = _scenario()
    result = EnergyCostAwareSchedulerSolver(solver_input).solve()
    assert result["feasible"] is True
    kpi = result["solutions"][0]["kpi"]
    assert "completed_orders" in kpi
    assert "total_cost" in kpi
    assert kpi["total_orders"] == 3


def test_unknown_engine_raises():
    solver_input = _scenario()
    solver_input["config"]["solver_engine"] = "bogus"
    result = EnergyCostAwareSchedulerSolver(solver_input).solve()
    assert result["feasible"] is False
    assert result["issues"]


if __name__ == "__main__":
    test_cpsat_feasible_respects_power_cap_and_deadlines()
    test_cpsat_kpi_shape()
    test_unknown_engine_raises()
    print("ALL PASSED")
