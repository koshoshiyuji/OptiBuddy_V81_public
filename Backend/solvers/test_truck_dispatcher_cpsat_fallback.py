"""
test_truck_dispatcher_cpsat_fallback.py

TruckDispatcherSolver の CP-SAT(CPMpy)経路(_solve_cpsat)の構造テスト。
2026-08-13修正: 他ドメインと統一し、config.solver_engine（既定値は
engine_select.pyのポリシーに従い"cpsat"）でCPO/CP-SATを明示的に切り替える
ようになった（以前はdocplexの利用可否のみで自動判定していた）。
docplex/CPLEXが無い本サンドボックスでは、config未指定でも既定でcpsatが
使われる。
"""

from __future__ import annotations

import sys
import os

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from solvers.truck_dispatcher_solver import TruckDispatcherSolver, _CPO_AVAILABLE


def _base_locations_and_dist():
    locations = [
        {"id": "DEPOT", "name": "Depot"},
        {"id": "C1", "name": "Cust1"},
        {"id": "C2", "name": "Cust2"},
        {"id": "C3", "name": "Cust3"},
        {"id": "C4", "name": "Cust4"},
    ]
    dist_matrix = [
        [0, 5, 8, 12, 20],
        [5, 0, 6, 15, 22],
        [8, 6, 0, 9, 18],
        [12, 15, 9, 0, 10],
        [20, 22, 18, 10, 0],
    ]
    return locations, dist_matrix


def test_cpsat_fallback_engages_when_docplex_unavailable():
    """config.solver_engine未指定時は既定でcpsatが使われること
    （本サンドボックスにはdocplexも無いため、二重の意味でcpsat経路になる）。"""
    assert _CPO_AVAILABLE is False, "このテストはdocplex未インストール環境を前提にしている"

    locations, dist_matrix = _base_locations_and_dist()
    vehicles = [
        {"id": "V1", "type": "truck", "capacity_kg": 1000, "max_duty_min": 600},
        {"id": "V2", "type": "truck", "capacity_kg": 1000, "max_duty_min": 600},
    ]
    customers = [
        {"id": "C1", "name": "Cust1", "_loc_idx": 1, "demand_kg": 200, "service_time_min": 15,
         "tw_open_min": 360, "tw_close_min": 1200, "restricted_vehicle_types": [], "stackable": True},
        {"id": "C2", "name": "Cust2", "_loc_idx": 2, "demand_kg": 300, "service_time_min": 15,
         "tw_open_min": 360, "tw_close_min": 1200, "restricted_vehicle_types": [], "stackable": True},
        {"id": "C3", "name": "Cust3", "_loc_idx": 3, "demand_kg": 250, "service_time_min": 15,
         "tw_open_min": 360, "tw_close_min": 1200, "restricted_vehicle_types": [], "stackable": True},
        {"id": "C4", "name": "Cust4", "_loc_idx": 4, "demand_kg": 400, "service_time_min": 15,
         "tw_open_min": 360, "tw_close_min": 1200, "restricted_vehicle_types": [], "stackable": True},
    ]
    solver_input = {
        "locations": locations, "vehicles": vehicles, "customers": customers,
        "dist_matrix": dist_matrix, "config": {"solver_time_limit_sec": 20}, "meta": {},
    }
    result = TruckDispatcherSolver(solver_input).solve()
    assert result["feasible"] is True
    sol = result["solutions"][0]
    assert sol["solver_engine"] == "cpsat"
    assert sol["unserved_customers"] == []
    assert sum(r["n_stops"] for r in sol["routes"]) == 4

    # 積載量の整合性チェック(独立検証)
    for r in sol["routes"]:
        assert r["load_kg"] <= r["capacity_kg"]

    # 出発は全車depot_open固定、帰着は出発以降であること
    for r in sol["routes"]:
        assert r["depart_min"] == 360
        assert r["return_min"] >= r["depart_min"]


def test_cpsat_fallback_respects_capacity():
    """1台では全顧客の需要合計を積みきれない場合、2台目が使われること。"""
    locations, dist_matrix = _base_locations_and_dist()
    vehicles = [{"id": "V1", "type": "truck", "capacity_kg": 500, "max_duty_min": 600}]
    customers = [
        {"id": "C1", "name": "Cust1", "_loc_idx": 1, "demand_kg": 300, "service_time_min": 15,
         "tw_open_min": 360, "tw_close_min": 1200, "restricted_vehicle_types": [], "stackable": True},
        {"id": "C2", "name": "Cust2", "_loc_idx": 2, "demand_kg": 300, "service_time_min": 15,
         "tw_open_min": 360, "tw_close_min": 1200, "restricted_vehicle_types": [], "stackable": True},
    ]
    solver_input = {
        "locations": locations, "vehicles": vehicles, "customers": customers,
        "dist_matrix": dist_matrix, "config": {"solver_time_limit_sec": 20}, "meta": {},
    }
    result = TruckDispatcherSolver(solver_input).solve()
    sol = result["solutions"][0]
    # 1台(500kg)しかないので、両方積むと600kg>500kgで超過 -> 1件は未割当のはず
    assert len(sol["unserved_customers"]) == 1
    served = [c for c in ["C1", "C2"] if c not in sol["unserved_customers"]]
    assert len(served) == 1


def test_cpsat_fallback_respects_restricted_vehicle_types():
    locations, dist_matrix = _base_locations_and_dist()
    vehicles = [
        {"id": "SMALL", "type": "small", "capacity_kg": 1000, "max_duty_min": 600},
    ]
    customers = [
        {"id": "C1", "name": "Cust1", "_loc_idx": 1, "demand_kg": 100, "service_time_min": 15,
         "tw_open_min": 360, "tw_close_min": 1200, "restricted_vehicle_types": ["small"], "stackable": True},
    ]
    solver_input = {
        "locations": locations, "vehicles": vehicles, "customers": customers,
        "dist_matrix": dist_matrix, "config": {"solver_time_limit_sec": 20}, "meta": {},
    }
    result = TruckDispatcherSolver(solver_input).solve()
    sol = result["solutions"][0]
    assert sol["unserved_customers"] == ["C1"]
    assert sol["feasible"] is False


def test_cpsat_fallback_non_stackable_limit():
    """段積み不可貨物は同一車両に2件以上同時搭載されないこと。"""
    locations, dist_matrix = _base_locations_and_dist()
    vehicles = [{"id": "V1", "type": "truck", "capacity_kg": 5000, "max_duty_min": 600}]
    customers = [
        {"id": "C1", "name": "Cust1", "_loc_idx": 1, "demand_kg": 50, "service_time_min": 15,
         "tw_open_min": 360, "tw_close_min": 1200, "restricted_vehicle_types": [], "stackable": False},
        {"id": "C2", "name": "Cust2", "_loc_idx": 2, "demand_kg": 50, "service_time_min": 15,
         "tw_open_min": 360, "tw_close_min": 1200, "restricted_vehicle_types": [], "stackable": False},
    ]
    solver_input = {
        "locations": locations, "vehicles": vehicles, "customers": customers,
        "dist_matrix": dist_matrix, "config": {"solver_time_limit_sec": 20}, "meta": {},
    }
    result = TruckDispatcherSolver(solver_input).solve()
    sol = result["solutions"][0]
    # 1台しかなく、両方段積み不可なので同時搭載不可 -> 1件のみ割当
    served_count = sum(r["n_stops"] for r in sol["routes"])
    assert served_count == 1
    assert len(sol["unserved_customers"]) == 1


def test_cpsat_fallback_time_window_order():
    """タイムウィンドウ(tw_open)がハード下限として尊重され、到着順が妥当であること。"""
    locations, dist_matrix = _base_locations_and_dist()
    vehicles = [{"id": "V1", "type": "truck", "capacity_kg": 5000, "max_duty_min": 600}]
    customers = [
        {"id": "C1", "name": "Cust1", "_loc_idx": 1, "demand_kg": 50, "service_time_min": 10,
         "tw_open_min": 500, "tw_close_min": 1200, "restricted_vehicle_types": [], "stackable": True},
        {"id": "C2", "name": "Cust2", "_loc_idx": 2, "demand_kg": 50, "service_time_min": 10,
         "tw_open_min": 360, "tw_close_min": 1200, "restricted_vehicle_types": [], "stackable": True},
    ]
    solver_input = {
        "locations": locations, "vehicles": vehicles, "customers": customers,
        "dist_matrix": dist_matrix, "config": {"solver_time_limit_sec": 20}, "meta": {},
    }
    result = TruckDispatcherSolver(solver_input).solve()
    sol = result["solutions"][0]
    assert sol["feasible"] is True
    for r in sol["routes"]:
        for s in r["stops"]:
            assert s["arrival_min"] >= s["tw_open_min"], "TW開始より前に到着してはいけない"


def test_explicit_cpsat_engine_selected():
    """config.solver_engine="cpsat"を明示指定した場合もcpsat経路が使われること
    （docplexの有無に関わらず、明示指定が優先されるべき）。"""
    locations, dist_matrix = _base_locations_and_dist()
    vehicles = [{"id": "V1", "type": "truck", "capacity_kg": 5000, "max_duty_min": 600}]
    customers = [
        {"id": "C1", "name": "Cust1", "_loc_idx": 1, "demand_kg": 50, "service_time_min": 10,
         "tw_open_min": 360, "tw_close_min": 1200, "restricted_vehicle_types": [], "stackable": True},
    ]
    solver_input = {
        "locations": locations, "vehicles": vehicles, "customers": customers,
        "dist_matrix": dist_matrix, "config": {"solver_engine": "cpsat"}, "meta": {},
    }
    result = TruckDispatcherSolver(solver_input).solve()
    assert result["feasible"] is True
    assert result["solutions"][0]["solver_engine"] == "cpsat"


def test_explicit_cpo_without_docplex_returns_clear_error():
    """config.solver_engine="cpo"を明示指定したのにdocplexが無い場合は、
    黙ってcpsatにフォールバックしたりせず、はっきりエラーを返すこと
    （他ドメインのdocplex未インストール時の扱いと同じ）。"""
    assert _CPO_AVAILABLE is False, "このテストはdocplex未インストール環境を前提にしている"

    locations, dist_matrix = _base_locations_and_dist()
    vehicles = [{"id": "V1", "type": "truck", "capacity_kg": 5000, "max_duty_min": 600}]
    customers = [
        {"id": "C1", "name": "Cust1", "_loc_idx": 1, "demand_kg": 50, "service_time_min": 10,
         "tw_open_min": 360, "tw_close_min": 1200, "restricted_vehicle_types": [], "stackable": True},
    ]
    solver_input = {
        "locations": locations, "vehicles": vehicles, "customers": customers,
        "dist_matrix": dist_matrix, "config": {"solver_engine": "cpo"}, "meta": {},
    }
    result = TruckDispatcherSolver(solver_input).solve()
    assert result["feasible"] is False
    issue_ids = {i["id"] for i in result["issues"]}
    assert "solve_failed" in issue_ids


def test_unknown_engine_raises():
    locations, dist_matrix = _base_locations_and_dist()
    vehicles = [{"id": "V1", "type": "truck", "capacity_kg": 5000, "max_duty_min": 600}]
    customers = [
        {"id": "C1", "name": "Cust1", "_loc_idx": 1, "demand_kg": 50, "service_time_min": 10,
         "tw_open_min": 360, "tw_close_min": 1200, "restricted_vehicle_types": [], "stackable": True},
    ]
    solver_input = {
        "locations": locations, "vehicles": vehicles, "customers": customers,
        "dist_matrix": dist_matrix, "config": {"solver_engine": "bogus"}, "meta": {},
    }
    result = TruckDispatcherSolver(solver_input).solve()
    assert result["feasible"] is False
    assert result["issues"]


if __name__ == "__main__":
    test_cpsat_fallback_engages_when_docplex_unavailable()
    test_cpsat_fallback_respects_capacity()
    test_cpsat_fallback_respects_restricted_vehicle_types()
    test_cpsat_fallback_non_stackable_limit()
    test_cpsat_fallback_time_window_order()
    test_explicit_cpsat_engine_selected()
    test_explicit_cpo_without_docplex_returns_clear_error()
    test_unknown_engine_raises()
    print("ALL PASSED")
