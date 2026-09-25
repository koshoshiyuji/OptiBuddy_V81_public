
import pytest
from solvers.base.issue_rules import (
    build_truck_dispatcher_contexts,
    run_issue_rules,
)




# ---------------------------------------------------------------------------
# テスト: TruckDispatcher 解チェッカー（2026-07-24追加）
# ---------------------------------------------------------------------------

class TestTruckDispatcherChecker:
    def test_duty_overtime_fires_with_solver_category(self):
        """拘束時間ハード制約違反 → category=SOLVER（バグ疑い）"""
        routes = [{"vehicle_id": "V1", "vehicle_name": "車1", "duty_min": 600, "stops": []}]
        ctxs = build_truck_dispatcher_contexts(routes, {"V1": 540})
        issues = run_issue_rules("TruckDispatcher", ctxs, {})
        overtime = [i for i in issues if i["id"] == "duty_overtime"]
        assert len(overtime) == 1
        assert overtime[0]["category"] == "SOLVER"
        assert overtime[0]["severity"] == "CRITICAL"

    def test_duty_overtime_no_fire_within_limit(self):
        routes = [{"vehicle_id": "V1", "vehicle_name": "車1", "duty_min": 500, "stops": []}]
        ctxs = build_truck_dispatcher_contexts(routes, {"V1": 540})
        issues = run_issue_rules("TruckDispatcher", ctxs, {})
        assert not any(i["id"] == "duty_overtime" for i in issues)

    def test_tw_overdue_fires_without_solver_category(self):
        """TW超過はソフト制約の業務結果 → category=SOLVERは付与しない"""
        routes = [{"vehicle_id": "V1", "vehicle_name": "車1", "duty_min": 100, "stops": [
            {"customer_name": "顧客A", "arrival_min": 700, "tw_close_min": 600},
        ]}]
        ctxs = build_truck_dispatcher_contexts(routes, {"V1": 540})
        issues = run_issue_rules("TruckDispatcher", ctxs, {})
        overdue = [i for i in issues if i["id"] == "tw_overdue"]
        assert len(overdue) == 1
        assert overdue[0].get("category") != "SOLVER"



# ---------------------------------------------------------------------------
# テスト: TruckDispatcher 移動時間の検算 transit_shortfall（2026-09-25追加）
# ---------------------------------------------------------------------------

class TestTruckDispatcherTransitShortfall:
    # 地点0=拠点、1=顧客A、2=顧客B。平均速度30km/h -> 10kmで20分。高速閾値50km。
    DIST = [
        [0.0, 10.0, 15.0],
        [10.0, 0.0, 5.0],
        [15.0, 5.0, 0.0],
    ]
    CONFIG = {"avg_speed_kmh": 30.0, "highway_speed_kmh": 80.0, "highway_threshold_km": 50.0}
    CUSTOMERS = [{"id": "A", "_loc_idx": 1}, {"id": "B", "_loc_idx": 2}]

    def _route(self, a_arrival, b_arrival, depart=480, ret=None):
        # A: 作業15分、B: 作業15分。B->拠点 15km=30分
        ret = ret if ret is not None else b_arrival + 15 + 30
        return [{"vehicle_id": "V1", "vehicle_name": "車1", "duty_min": ret - depart,
                 "depart_min": depart, "return_min": ret, "stops": [
                     {"customer_id": "A", "customer_name": "顧客A", "arrival_min": a_arrival,
                      "service_time_min": 15, "tw_close_min": 9999},
                     {"customer_id": "B", "customer_name": "顧客B", "arrival_min": b_arrival,
                      "service_time_min": 15, "tw_close_min": 9999},
                 ]}]

    def _issues(self, routes):
        ctxs = build_truck_dispatcher_contexts(
            routes, {"V1": 9999}, customers=self.CUSTOMERS, dist_matrix=self.DIST, config=self.CONFIG,
        )
        return [i for i in run_issue_rules("TruckDispatcher", ctxs, {}) if i["id"] == "transit_shortfall"]

    def test_feasible_route_no_fire(self):
        # 拠点480発 -> A 500着(20分) -> 作業515まで -> B 525着(5km=10分) -> 540まで -> 拠点570
        assert self._issues(self._route(500, 525)) == []

    def test_between_stops_shortfall_fires_as_solver(self):
        # A 515終了 -> B 516着（必要10分、間隔1分）
        issues = self._issues(self._route(500, 516))
        assert len(issues) == 1
        assert issues[0]["category"] == "SOLVER"
        assert "顧客A→顧客B（必要10分 / 間隔1分）" in issues[0]["message"]

    def test_depot_departure_leg_checked(self):
        # 拠点480発 -> A 490着（必要20分、間隔10分）
        issues = self._issues(self._route(490, 525))
        assert "拠点出発→顧客A（必要20分 / 間隔10分）" in issues[0]["message"]

    def test_depot_return_leg_checked(self):
        # B 540終了 -> 拠点 550帰着（必要30分、間隔10分）
        issues = self._issues(self._route(500, 525, ret=550))
        assert "顧客B→拠点帰着（必要30分 / 間隔10分）" in issues[0]["message"]

    def test_one_minute_rounding_difference_is_tolerated(self):
        # フォールバック解の丸め差（1分）は違反にしない
        assert self._issues(self._route(500, 524)) == []

    def test_highway_speed_used_above_threshold(self):
        dist = [[0.0, 80.0], [80.0, 0.0]]   # 80km > 閾値50km -> 80km/h で60分
        routes = [{"vehicle_id": "V1", "vehicle_name": "車1", "duty_min": 200,
                   "depart_min": 480, "return_min": 615, "stops": [
                       {"customer_id": "A", "customer_name": "顧客A", "arrival_min": 540,
                        "service_time_min": 15, "tw_close_min": 9999}]}]
        ctxs = build_truck_dispatcher_contexts(
            routes, {"V1": 9999}, customers=[{"id": "A", "_loc_idx": 1}], dist_matrix=dist, config=self.CONFIG,
        )
        assert [i for i in run_issue_rules("TruckDispatcher", ctxs, {}) if i["id"] == "transit_shortfall"] == []

    def test_not_checked_without_dist_matrix(self):
        ctxs = build_truck_dispatcher_contexts(self._route(500, 516), {"V1": 9999})
        assert not any(c["_rule_id"] == "transit_shortfall" for c in ctxs)
