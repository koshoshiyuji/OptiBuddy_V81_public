
import pytest
from solvers.base.issue_rules import (
    ISSUE_RULES,
    build_rideshare_matching_planner_contexts,
    run_issue_rules,
)




# ---------------------------------------------------------------------------
# テスト: RideshareMatchingPlanner 解チェッカー（2026-09-01新規、バッチ5）
# ---------------------------------------------------------------------------

class TestRideshareMatchingPlannerChecker:
    def test_seat_capacity_violation_fires(self):
        driver_routes = {"d1": {
            "driver_id": "d1", "driver_name": "D1", "seats": 1, "depart_min": 0, "arrive_max": 1000,
            "passengers": [
                {"passenger_id": "p1", "passenger_name": "P1", "pickup_min": 0, "dropoff_min": 30},
                {"passenger_id": "p2", "passenger_name": "P2", "pickup_min": 10, "dropoff_min": 40},
            ],
        }}
        matched_pairs = [
            {"passenger_id": "p1", "passenger_name": "P1", "driver_id": "d1", "driver_name": "D1",
             "pickup_min": 0, "dropoff_min": 30, "ride_min": 30, "direct_min": 30,
             "window_start_min": 0, "window_end_min": 1000},
            {"passenger_id": "p2", "passenger_name": "P2", "driver_id": "d1", "driver_name": "D1",
             "pickup_min": 10, "dropoff_min": 40, "ride_min": 30, "direct_min": 30,
             "window_start_min": 0, "window_end_min": 1000},
        ]
        passengers = [{"id": "p1"}, {"id": "p2"}]
        ctxs = build_rideshare_matching_planner_contexts(matched_pairs, [], driver_routes, passengers, {"detour_factor": 1.5})
        issues = run_issue_rules("RideshareMatchingPlanner", ctxs, {})
        assert any(i["id"] == "seat_capacity_violation_d1" for i in issues)
        assert all(i["category"] == "SOLVER" for i in issues)

    def test_seat_capacity_ok_no_fire(self):
        driver_routes = {"d1": {
            "driver_id": "d1", "driver_name": "D1", "seats": 3, "depart_min": 0, "arrive_max": 1000,
            "passengers": [
                {"passenger_id": "p1", "passenger_name": "P1", "pickup_min": 0, "dropoff_min": 30},
                {"passenger_id": "p2", "passenger_name": "P2", "pickup_min": 10, "dropoff_min": 40},
            ],
        }}
        matched_pairs = [
            {"passenger_id": "p1", "passenger_name": "P1", "driver_id": "d1", "driver_name": "D1",
             "pickup_min": 0, "dropoff_min": 30, "ride_min": 30, "direct_min": 30,
             "window_start_min": 0, "window_end_min": 1000},
            {"passenger_id": "p2", "passenger_name": "P2", "driver_id": "d1", "driver_name": "D1",
             "pickup_min": 10, "dropoff_min": 40, "ride_min": 30, "direct_min": 30,
             "window_start_min": 0, "window_end_min": 1000},
        ]
        passengers = [{"id": "p1"}, {"id": "p2"}]
        ctxs = build_rideshare_matching_planner_contexts(matched_pairs, [], driver_routes, passengers, {"detour_factor": 1.5})
        issues = [i for i in run_issue_rules("RideshareMatchingPlanner", ctxs, {}) if i["id"] == "seat_capacity_violation_d1"]
        assert issues == []

    def test_detour_time_violation_fires(self):
        driver_routes = {"d1": {
            "driver_id": "d1", "driver_name": "D1", "seats": 4, "depart_min": 0, "arrive_max": 1000,
            "passengers": [{"passenger_id": "p1", "passenger_name": "P1", "pickup_min": 0, "dropoff_min": 100}],
        }}
        matched_pairs = [{"passenger_id": "p1", "passenger_name": "P1", "driver_id": "d1", "driver_name": "D1",
                           "pickup_min": 0, "dropoff_min": 100, "ride_min": 100, "direct_min": 30,
                           "window_start_min": 0, "window_end_min": 1000}]
        passengers = [{"id": "p1"}]
        ctxs = build_rideshare_matching_planner_contexts(matched_pairs, [], driver_routes, passengers, {"detour_factor": 1.5})
        issues = run_issue_rules("RideshareMatchingPlanner", ctxs, {})
        assert any(i["id"] == "detour_time_violation_p1" for i in issues)

    def test_detour_time_ok_no_fire(self):
        driver_routes = {"d1": {
            "driver_id": "d1", "driver_name": "D1", "seats": 4, "depart_min": 0, "arrive_max": 1000,
            "passengers": [{"passenger_id": "p1", "passenger_name": "P1", "pickup_min": 0, "dropoff_min": 40}],
        }}
        matched_pairs = [{"passenger_id": "p1", "passenger_name": "P1", "driver_id": "d1", "driver_name": "D1",
                           "pickup_min": 0, "dropoff_min": 40, "ride_min": 40, "direct_min": 30,
                           "window_start_min": 0, "window_end_min": 1000}]
        passengers = [{"id": "p1"}]
        ctxs = build_rideshare_matching_planner_contexts(matched_pairs, [], driver_routes, passengers, {"detour_factor": 1.5})
        issues = [i for i in run_issue_rules("RideshareMatchingPlanner", ctxs, {}) if i["id"] == "detour_time_violation_p1"]
        assert issues == []

    def test_pickup_dropoff_order_violation_fires(self):
        driver_routes = {"d1": {
            "driver_id": "d1", "driver_name": "D1", "seats": 4, "depart_min": 0, "arrive_max": 1000,
            "passengers": [{"passenger_id": "p1", "passenger_name": "P1", "pickup_min": 50, "dropoff_min": 50}],
        }}
        matched_pairs = [{"passenger_id": "p1", "passenger_name": "P1", "driver_id": "d1", "driver_name": "D1",
                           "pickup_min": 50, "dropoff_min": 50, "ride_min": 0, "direct_min": 10,
                           "window_start_min": 0, "window_end_min": 1000}]
        passengers = [{"id": "p1"}]
        ctxs = build_rideshare_matching_planner_contexts(matched_pairs, [], driver_routes, passengers, {})
        issues = run_issue_rules("RideshareMatchingPlanner", ctxs, {})
        assert any(i["id"] == "pickup_dropoff_order_violation_p1" for i in issues)

    def test_pickup_dropoff_order_ok_no_fire(self):
        driver_routes = {"d1": {
            "driver_id": "d1", "driver_name": "D1", "seats": 4, "depart_min": 0, "arrive_max": 1000,
            "passengers": [{"passenger_id": "p1", "passenger_name": "P1", "pickup_min": 50, "dropoff_min": 60}],
        }}
        matched_pairs = [{"passenger_id": "p1", "passenger_name": "P1", "driver_id": "d1", "driver_name": "D1",
                           "pickup_min": 50, "dropoff_min": 60, "ride_min": 10, "direct_min": 10,
                           "window_start_min": 0, "window_end_min": 1000}]
        passengers = [{"id": "p1"}]
        ctxs = build_rideshare_matching_planner_contexts(matched_pairs, [], driver_routes, passengers, {})
        issues = [i for i in run_issue_rules("RideshareMatchingPlanner", ctxs, {}) if i["id"] == "pickup_dropoff_order_violation_p1"]
        assert issues == []

    def test_passenger_time_window_violation_fires(self):
        driver_routes = {"d1": {
            "driver_id": "d1", "driver_name": "D1", "seats": 4, "depart_min": 0, "arrive_max": 1000,
            "passengers": [{"passenger_id": "p1", "passenger_name": "P1", "pickup_min": 5, "dropoff_min": 20}],
        }}
        matched_pairs = [{"passenger_id": "p1", "passenger_name": "P1", "driver_id": "d1", "driver_name": "D1",
                           "pickup_min": 5, "dropoff_min": 20, "ride_min": 15, "direct_min": 15,
                           "window_start_min": 10, "window_end_min": 100}]
        passengers = [{"id": "p1"}]
        ctxs = build_rideshare_matching_planner_contexts(matched_pairs, [], driver_routes, passengers, {})
        issues = run_issue_rules("RideshareMatchingPlanner", ctxs, {})
        assert any(i["id"] == "passenger_time_window_violation_p1" for i in issues)

    def test_passenger_time_window_ok_no_fire(self):
        driver_routes = {"d1": {
            "driver_id": "d1", "driver_name": "D1", "seats": 4, "depart_min": 0, "arrive_max": 1000,
            "passengers": [{"passenger_id": "p1", "passenger_name": "P1", "pickup_min": 15, "dropoff_min": 30}],
        }}
        matched_pairs = [{"passenger_id": "p1", "passenger_name": "P1", "driver_id": "d1", "driver_name": "D1",
                           "pickup_min": 15, "dropoff_min": 30, "ride_min": 15, "direct_min": 15,
                           "window_start_min": 10, "window_end_min": 100}]
        passengers = [{"id": "p1"}]
        ctxs = build_rideshare_matching_planner_contexts(matched_pairs, [], driver_routes, passengers, {})
        issues = [i for i in run_issue_rules("RideshareMatchingPlanner", ctxs, {}) if i["id"] == "passenger_time_window_violation_p1"]
        assert issues == []

    def test_driver_operating_window_violation_fires(self):
        driver_routes = {"d1": {
            "driver_id": "d1", "driver_name": "D1", "seats": 4, "depart_min": 100, "arrive_max": 200,
            "passengers": [{"passenger_id": "p1", "passenger_name": "P1", "pickup_min": 50, "dropoff_min": 150}],
        }}
        matched_pairs = [{"passenger_id": "p1", "passenger_name": "P1", "driver_id": "d1", "driver_name": "D1",
                           "pickup_min": 50, "dropoff_min": 150, "ride_min": 100, "direct_min": 80,
                           "window_start_min": 0, "window_end_min": 1000}]
        passengers = [{"id": "p1"}]
        ctxs = build_rideshare_matching_planner_contexts(matched_pairs, [], driver_routes, passengers, {"detour_factor": 1.5})
        issues = run_issue_rules("RideshareMatchingPlanner", ctxs, {})
        assert any(i["id"] == "driver_operating_window_violation_d1_p1" for i in issues)

    def test_driver_operating_window_ok_no_fire(self):
        driver_routes = {"d1": {
            "driver_id": "d1", "driver_name": "D1", "seats": 4, "depart_min": 100, "arrive_max": 200,
            "passengers": [{"passenger_id": "p1", "passenger_name": "P1", "pickup_min": 150, "dropoff_min": 180}],
        }}
        matched_pairs = [{"passenger_id": "p1", "passenger_name": "P1", "driver_id": "d1", "driver_name": "D1",
                           "pickup_min": 150, "dropoff_min": 180, "ride_min": 30, "direct_min": 30,
                           "window_start_min": 0, "window_end_min": 1000}]
        passengers = [{"id": "p1"}]
        ctxs = build_rideshare_matching_planner_contexts(matched_pairs, [], driver_routes, passengers, {})
        issues = [i for i in run_issue_rules("RideshareMatchingPlanner", ctxs, {})
                  if i["id"] == "driver_operating_window_violation_d1_p1"]
        assert issues == []

    def test_passenger_duplicate_assignment_fires(self):
        driver_routes = {
            "d1": {"driver_id": "d1", "driver_name": "D1", "seats": 4, "depart_min": 0, "arrive_max": 1000,
                   "passengers": [{"passenger_id": "p1", "passenger_name": "P1", "pickup_min": 0, "dropoff_min": 20}]},
            "d2": {"driver_id": "d2", "driver_name": "D2", "seats": 4, "depart_min": 0, "arrive_max": 1000,
                   "passengers": [{"passenger_id": "p1", "passenger_name": "P1", "pickup_min": 0, "dropoff_min": 20}]},
        }
        matched_pairs = [
            {"passenger_id": "p1", "passenger_name": "P1", "driver_id": "d1", "driver_name": "D1",
             "pickup_min": 0, "dropoff_min": 20, "ride_min": 20, "direct_min": 20,
             "window_start_min": 0, "window_end_min": 1000},
            {"passenger_id": "p1", "passenger_name": "P1", "driver_id": "d2", "driver_name": "D2",
             "pickup_min": 0, "dropoff_min": 20, "ride_min": 20, "direct_min": 20,
             "window_start_min": 0, "window_end_min": 1000},
        ]
        passengers = [{"id": "p1"}]
        ctxs = build_rideshare_matching_planner_contexts(matched_pairs, [], driver_routes, passengers, {})
        issues = run_issue_rules("RideshareMatchingPlanner", ctxs, {})
        assert any(i["id"] == "passenger_duplicate_assignment_p1" for i in issues)

    def test_passenger_single_assignment_no_fire(self):
        driver_routes = {"d1": {"driver_id": "d1", "driver_name": "D1", "seats": 4, "depart_min": 0, "arrive_max": 1000,
                                 "passengers": [{"passenger_id": "p1", "passenger_name": "P1", "pickup_min": 0, "dropoff_min": 20}]}}
        matched_pairs = [{"passenger_id": "p1", "passenger_name": "P1", "driver_id": "d1", "driver_name": "D1",
                           "pickup_min": 0, "dropoff_min": 20, "ride_min": 20, "direct_min": 20,
                           "window_start_min": 0, "window_end_min": 1000}]
        passengers = [{"id": "p1"}]
        ctxs = build_rideshare_matching_planner_contexts(matched_pairs, [], driver_routes, passengers, {})
        issues = [i for i in run_issue_rules("RideshareMatchingPlanner", ctxs, {}) if i["id"] == "passenger_duplicate_assignment_p1"]
        assert issues == []

    def test_passenger_missing_from_output_fires(self):
        driver_routes = {"d1": {"driver_id": "d1", "driver_name": "D1", "seats": 4, "depart_min": 0, "arrive_max": 1000,
                                 "passengers": [{"passenger_id": "p1", "passenger_name": "P1", "pickup_min": 0, "dropoff_min": 20}]}}
        matched_pairs = [{"passenger_id": "p1", "passenger_name": "P1", "driver_id": "d1", "driver_name": "D1",
                           "pickup_min": 0, "dropoff_min": 20, "ride_min": 20, "direct_min": 20,
                           "window_start_min": 0, "window_end_min": 1000}]
        passengers = [{"id": "p1"}, {"id": "p2"}]
        ctxs = build_rideshare_matching_planner_contexts(matched_pairs, [], driver_routes, passengers, {})
        issues = run_issue_rules("RideshareMatchingPlanner", ctxs, {})
        assert any(i["id"] == "passenger_missing_from_output_p2" for i in issues)

    def test_passenger_present_in_unmatched_no_fire(self):
        driver_routes = {"d1": {"driver_id": "d1", "driver_name": "D1", "seats": 4, "depart_min": 0, "arrive_max": 1000,
                                 "passengers": [{"passenger_id": "p1", "passenger_name": "P1", "pickup_min": 0, "dropoff_min": 20}]}}
        matched_pairs = [{"passenger_id": "p1", "passenger_name": "P1", "driver_id": "d1", "driver_name": "D1",
                           "pickup_min": 0, "dropoff_min": 20, "ride_min": 20, "direct_min": 20,
                           "window_start_min": 0, "window_end_min": 1000}]
        unmatched_passengers = [{"id": "p2"}]
        passengers = [{"id": "p1"}, {"id": "p2"}]
        ctxs = build_rideshare_matching_planner_contexts(matched_pairs, unmatched_passengers, driver_routes, passengers, {})
        issues = [i for i in run_issue_rules("RideshareMatchingPlanner", ctxs, {}) if i["id"] == "passenger_missing_from_output_p2"]
        assert issues == []

    def test_all_clean_no_fire(self):
        driver_routes = {"d1": {"driver_id": "d1", "driver_name": "D1", "seats": 4, "depart_min": 0, "arrive_max": 1000,
                                 "passengers": [{"passenger_id": "p1", "passenger_name": "P1", "pickup_min": 10, "dropoff_min": 40}]}}
        matched_pairs = [{"passenger_id": "p1", "passenger_name": "P1", "driver_id": "d1", "driver_name": "D1",
                           "pickup_min": 10, "dropoff_min": 40, "ride_min": 30, "direct_min": 25,
                           "window_start_min": 0, "window_end_min": 1000}]
        passengers = [{"id": "p1"}]
        ctxs = build_rideshare_matching_planner_contexts(matched_pairs, [], driver_routes, passengers, {"detour_factor": 1.5})
        issues = run_issue_rules("RideshareMatchingPlanner", ctxs, {})
        assert issues == []

    def test_registered_in_issue_rules(self):
        assert "RideshareMatchingPlanner" in ISSUE_RULES



# ---------------------------------------------------------------------------
# テスト: RideshareMatchingPlanner 移動時間の検算 transit_shortfall（2026-09-25追加）
# ---------------------------------------------------------------------------

class TestRideshareTransitShortfall:
    # 地点: S=出発地, P=乗車地, Q=降車地, E=目的地。speed 60km/h -> km=分
    LOCATIONS = [{"id": "S"}, {"id": "P"}, {"id": "Q"}, {"id": "E"}]
    DIST = [
        [0, 10, 30, 40],
        [10, 0, 20, 30],
        [30, 20, 0, 10],
        [40, 30, 10, 0],
    ]
    CONFIG = {"detour_factor": 1.5, "speed_kmh": 60}

    def _ctxs(self, pickup, dropoff, depart=0, arrive_max=1000):
        mp = {"passenger_id": "p1", "passenger_name": "P1", "driver_id": "d1", "driver_name": "D1",
              "pickup_min": pickup, "dropoff_min": dropoff, "ride_min": dropoff - pickup,
              "direct_min": 20, "pickup_loc": "P", "dropoff_loc": "Q",
              "window_start_min": 0, "window_end_min": 1000}
        routes = {"d1": {"driver_id": "d1", "driver_name": "D1", "seats": 4,
                         "start_loc": "S", "end_loc": "E",
                         "depart_min": depart, "arrive_max": arrive_max, "passengers": [mp]}}
        return build_rideshare_matching_planner_contexts(
            [mp], [], routes, [{"id": "p1"}], self.CONFIG,
            dist_matrix=self.DIST, locations=self.LOCATIONS,
        )

    def _transit(self, ctxs):
        return [i for i in run_issue_rules("RideshareMatchingPlanner", ctxs, {})
                if i["id"].startswith("transit_shortfall")]

    def test_feasible_no_fire(self):
        # 出発0 -> P 10(10分) -> 乗車11終了 -> Q 31(20分) -> 32終了 -> E 42以降
        assert self._transit(self._ctxs(10, 31)) == []

    def test_pickup_to_dropoff_shortfall_fires(self):
        issues = self._transit(self._ctxs(10, 25))   # 乗車11終了 -> 降車25（必要20、間隔14）
        assert len(issues) == 1
        assert issues[0]["id"] == "transit_shortfall_d1"
        assert issues[0]["category"] == "SOLVER"
        assert "P1乗車→P1降車（必要20分 / 間隔14分）" in issues[0]["message"]

    def test_start_leg_checked(self):
        issues = self._transit(self._ctxs(5, 31))    # 出発0 -> 乗車5（必要10）
        assert "出発地→P1乗車（必要10分 / 間隔5分）" in issues[0]["message"]

    def test_end_leg_checked(self):
        issues = self._transit(self._ctxs(10, 31, arrive_max=38))   # 降車32終了 -> 目的地38（必要10）
        assert "P1降車→目的地（必要10分 / 間隔6分）" in issues[0]["message"]

    def test_not_checked_without_dist_matrix(self):
        mp = {"passenger_id": "p1", "passenger_name": "P1", "driver_id": "d1", "driver_name": "D1",
              "pickup_min": 0, "dropoff_min": 1, "ride_min": 1, "direct_min": 20,
              "pickup_loc": "P", "dropoff_loc": "Q", "window_start_min": 0, "window_end_min": 1000}
        routes = {"d1": {"driver_id": "d1", "driver_name": "D1", "seats": 4, "depart_min": 0,
                         "arrive_max": 1000, "passengers": [mp]}}
        ctxs = build_rideshare_matching_planner_contexts([mp], [], routes, [{"id": "p1"}], self.CONFIG)
        assert not any(c["_rule_id"] == "transit_shortfall" for c in ctxs)
