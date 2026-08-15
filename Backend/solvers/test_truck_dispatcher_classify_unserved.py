"""
test_truck_dispatcher_classify_unserved.py
============================================

TruckDispatcherSolver._classify_unserved()の回帰テスト。特に2026-07-23追加の
insufficient_fleet_capacity判定（型式・積載量で絞られた車両群の頭数が、同じ車両群を
必要とする顧客の総数に対して構造的に不足している場合の分類）を検証する。

背景: truck_dispatcher_baselineシナリオで、lns_ruin_vehicle_count/lns_max_iterations/
lns_stagnation_limit/lns_total_time_budget_secをどれだけ増やしても未割当6件が
一向に減らなかった問題を調査した結果、実際には10t車8台に対して該当顧客が13件という
フリート頭数不足が真因であり、既存の_classify_unserved()はこれを検出できず
unresolved_by_search（探索の問題）に誤分類していたことが判明した。
そのため、この分類が正しく機能することを保証する回帰テストを追加する。
docs/ENGINEERING_LOG.md 2026-07-23参照。

実行方法:
    cd Backend
    python -m pytest solvers/test_truck_dispatcher_classify_unserved.py -v
"""

from solvers.truck_dispatcher_solver import TruckDispatcherSolver


def _make_solver() -> TruckDispatcherSolver:
    # _classify_unserved()はインスタンス状態を使わないため、__init__を経由せず
    # 直接インスタンス化する（test_route_decomposer.pyと同様の方針）。
    return TruckDispatcherSolver.__new__(TruckDispatcherSolver)


def _locations(customer_ids):
    return [{"id": "depot"}] + [{"id": cid} for cid in customer_ids]


def _config():
    return {
        "avg_speed_kmh": 30.0,
        "highway_speed_kmh": 80.0,
        "highway_threshold_km": 50.0,
        "depot_open_min": 360,  # 06:00
    }


def _dist_matrix(n_customers, dist_km):
    """depot(index 0)から各顧客までdist_kmの単純な距離行列（顧客間距離は使わない）。"""
    size = n_customers + 1
    row0 = [0.0] + [dist_km] * n_customers
    matrix = [row0]
    for i in range(1, size):
        row = [dist_km if j == 0 else 0.0 for j in range(size)]
        matrix.append(row)
    return matrix


def test_insufficient_fleet_capacity_fires_when_contenders_exceed_fleet():
    """
    同じ車両群（大型車2台）を必要とする長距離・大口顧客が3件あり、単体では
    容量・拘束時間とも条件を満たせるのに、車両群の頭数（2台）が競合顧客数（3件）を
    下回っている場合、insufficient_fleet_capacityに分類されるべき。
    """
    solver = _make_solver()

    vehicles = [
        {"id": "big1", "type": "large", "capacity_kg": 10000, "max_duty_min": 600},
        {"id": "big2", "type": "large", "capacity_kg": 10000, "max_duty_min": 600},
        {"id": "small1", "type": "small", "capacity_kg": 500, "max_duty_min": 600},
    ]
    # 3件の大口・長距離顧客（demand_kgが大型車以外では運べない）。
    # 往復所要時間が拘束時間上限(600分)の大半を占めるよう、長距離に設定する。
    customer_ids = ["big_a", "big_b", "big_c"]
    customers = [
        {"id": cid, "demand_kg": 8000, "service_time_min": 15, "tw_open_min": 360,
         "restricted_vehicle_types": []}
        for cid in customer_ids
    ]
    locations = _locations(customer_ids)
    # 往復所要時間が拘束時間上限(600分)の約48%を占めるよう距離を設定
    # （dominant tripヒューリスティックの閾値40%を上回らせる）。
    dist_matrix = _dist_matrix(len(customer_ids), dist_km=180.0)
    config = _config()

    result = solver._classify_unserved(customer_ids, customers, vehicles, dist_matrix, locations, config)

    for cid in customer_ids:
        assert result[cid]["reason"] == "insufficient_fleet_capacity", result[cid]
        assert "頭数" in result[cid]["detail"] or "フリート" in result[cid]["detail"]


def test_unresolved_by_search_when_fleet_capacity_is_sufficient():
    """
    同じ車両群に対して競合顧客数が車両群の頭数を上回らない（1件だけ）場合は、
    従来通りunresolved_by_searchに分類されるべき（誤検知しないことの確認）。
    """
    solver = _make_solver()

    vehicles = [
        {"id": "big1", "type": "large", "capacity_kg": 10000, "max_duty_min": 600},
        {"id": "big2", "type": "large", "capacity_kg": 10000, "max_duty_min": 600},
        {"id": "small1", "type": "small", "capacity_kg": 500, "max_duty_min": 600},
    ]
    customer_ids = ["big_a"]
    customers = [
        {"id": "big_a", "demand_kg": 8000, "service_time_min": 15, "tw_open_min": 360,
         "restricted_vehicle_types": []}
    ]
    locations = _locations(customer_ids)
    dist_matrix = _dist_matrix(len(customer_ids), dist_km=140.0)
    config = _config()

    result = solver._classify_unserved(customer_ids, customers, vehicles, dist_matrix, locations, config)

    assert result["big_a"]["reason"] == "unresolved_by_search"


def test_short_trip_does_not_trigger_fleet_capacity_even_if_contenders_exceed_fleet():
    """
    競合顧客数が車両群の頭数を上回っていても、この顧客の往復所要時間が拘束時間上限に
    対して十分短い（1台で複数件こなせる）場合は、insufficient_fleet_capacityと
    誤判定すべきではない（trip dominanceヒューリスティックの確認）。
    """
    solver = _make_solver()

    vehicles = [
        {"id": "big1", "type": "large", "capacity_kg": 10000, "max_duty_min": 600},
    ]
    customer_ids = ["big_a", "big_b"]
    customers = [
        {"id": cid, "demand_kg": 8000, "service_time_min": 15, "tw_open_min": 360,
         "restricted_vehicle_types": []}
        for cid in customer_ids
    ]
    locations = _locations(customer_ids)
    # 短距離（往復所要時間が拘束時間上限の一部のみ）
    dist_matrix = _dist_matrix(len(customer_ids), dist_km=10.0)
    config = _config()

    result = solver._classify_unserved(customer_ids, customers, vehicles, dist_matrix, locations, config)

    for cid in customer_ids:
        assert result[cid]["reason"] == "unresolved_by_search", result[cid]


def test_capacity_infeasible_and_duty_infeasible_and_no_eligible_vehicle_unaffected():
    """既存の3分類（capacity_infeasible/duty_infeasible/no_eligible_vehicle）が
    今回の変更で壊れていないことを確認する。"""
    solver = _make_solver()

    vehicles = [
        {"id": "v1", "type": "small", "capacity_kg": 1000, "max_duty_min": 100},
    ]
    customers = [
        {"id": "c_cap", "demand_kg": 5000, "service_time_min": 15, "tw_open_min": 360,
         "restricted_vehicle_types": []},
        {"id": "c_duty", "demand_kg": 500, "service_time_min": 15, "tw_open_min": 360,
         "restricted_vehicle_types": []},
        {"id": "c_norestriction", "demand_kg": 500, "service_time_min": 15, "tw_open_min": 360,
         "restricted_vehicle_types": ["small"]},
    ]
    locations = _locations(["c_cap", "c_duty", "c_norestriction"])
    dist_matrix = _dist_matrix(3, dist_km=100.0)
    config = _config()

    result = solver._classify_unserved(
        ["c_cap", "c_duty", "c_norestriction"], customers, vehicles, dist_matrix, locations, config
    )

    assert result["c_cap"]["reason"] == "capacity_infeasible"
    assert result["c_duty"]["reason"] == "duty_infeasible"
    assert result["c_norestriction"]["reason"] == "no_eligible_vehicle"


if __name__ == "__main__":
    test_insufficient_fleet_capacity_fires_when_contenders_exceed_fleet()
    test_unresolved_by_search_when_fleet_capacity_is_sufficient()
    test_short_trip_does_not_trigger_fleet_capacity_even_if_contenders_exceed_fleet()
    test_capacity_infeasible_and_duty_infeasible_and_no_eligible_vehicle_unaffected()
    print("全テスト成功")
