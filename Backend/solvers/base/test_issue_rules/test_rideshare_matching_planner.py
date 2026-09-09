
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
