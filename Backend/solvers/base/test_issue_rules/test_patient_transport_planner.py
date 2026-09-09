
import pytest
from solvers.base.issue_rules import (
    ISSUE_RULES,
    build_patient_transport_planner_contexts,
    run_issue_rules,
)




# ---------------------------------------------------------------------------
# テスト: PatientTransportPlanner 解チェッカー（2026-09-01新規、バッチ4）
# ---------------------------------------------------------------------------

class TestPatientTransportPlannerChecker:
    def test_vehicle_capacity_violation_fires(self):
        requests = [{"id": "r1", "capacity_required": 3}, {"id": "r2", "capacity_required": 3}]
        vehicles = [{"id": "v1", "name": "Van1", "capacity": 4}]
        request_results = [
            {"request_id": "r1", "req_type": "outbound_only", "care_type": "c", "served": True,
             "outbound_vehicle": "v1", "outbound_start": 0, "outbound_end": 30},
            {"request_id": "r2", "req_type": "outbound_only", "care_type": "c", "served": True,
             "outbound_vehicle": "v1", "outbound_start": 10, "outbound_end": 40},
        ]
        ctxs = build_patient_transport_planner_contexts(request_results, requests, vehicles, {})
        issues = run_issue_rules("PatientTransportPlanner", ctxs, {})
        assert any(i["id"] == "vehicle_capacity_violation_v1" for i in issues)
        assert all(i["category"] == "SOLVER" for i in issues)

    def test_vehicle_capacity_ok_no_fire(self):
        requests = [{"id": "r1", "capacity_required": 3}, {"id": "r2", "capacity_required": 3}]
        vehicles = [{"id": "v1", "name": "Van1", "capacity": 4}]
        request_results = [
            {"request_id": "r1", "req_type": "outbound_only", "care_type": "c", "served": True,
             "outbound_vehicle": "v1", "outbound_start": 0, "outbound_end": 30},
            {"request_id": "r2", "req_type": "outbound_only", "care_type": "c", "served": True,
             "outbound_vehicle": "v1", "outbound_start": 40, "outbound_end": 70},
        ]
        ctxs = build_patient_transport_planner_contexts(request_results, requests, vehicles, {})
        issues = [i for i in run_issue_rules("PatientTransportPlanner", ctxs, {})
                  if i["id"] == "vehicle_capacity_violation_v1"]
        assert issues == []

    def test_vehicle_phase_overlap_fires(self):
        requests = [{"id": "r1", "capacity_required": 1}, {"id": "r2", "capacity_required": 1}]
        vehicles = [{"id": "v1", "name": "Van1", "capacity": 4}]
        request_results = [
            {"request_id": "r1", "req_type": "outbound_only", "care_type": "c", "served": True,
             "outbound_vehicle": "v1", "outbound_start": 0, "outbound_end": 30},
            {"request_id": "r2", "req_type": "outbound_only", "care_type": "c", "served": True,
             "outbound_vehicle": "v1", "outbound_start": 10, "outbound_end": 40},
        ]
        ctxs = build_patient_transport_planner_contexts(request_results, requests, vehicles, {})
        issues = run_issue_rules("PatientTransportPlanner", ctxs, {})
        assert any(i["id"] == "vehicle_phase_overlap_v1" for i in issues)

    def test_roundtrip_order_violation_fires(self):
        requests = [{"id": "r1", "exam_duration_min": 60}]
        vehicles = [{"id": "v1", "name": "Van1", "capacity": 10}]
        request_results = [
            {"request_id": "r1", "req_type": "roundtrip", "care_type": "c", "served": True,
             "outbound_vehicle": "v1", "outbound_start": 0, "outbound_end": 30,
             "inbound_vehicle": "v1", "inbound_start": 50, "inbound_end": 80},
        ]
        ctxs = build_patient_transport_planner_contexts(request_results, requests, vehicles, {})
        issues = run_issue_rules("PatientTransportPlanner", ctxs, {})
        assert any(i["id"] == "roundtrip_order_violation_r1" for i in issues)

    def test_roundtrip_order_ok_no_fire(self):
        requests = [{"id": "r1", "exam_duration_min": 60, "capacity_required": 1}]
        vehicles = [{"id": "v1", "name": "Van1", "capacity": 10}]
        request_results = [
            {"request_id": "r1", "req_type": "roundtrip", "care_type": "c", "served": True,
             "outbound_vehicle": "v1", "outbound_start": 0, "outbound_end": 30,
             "inbound_vehicle": "v1", "inbound_start": 90, "inbound_end": 120},
        ]
        ctxs = build_patient_transport_planner_contexts(request_results, requests, vehicles, {"boarding_time_min": 0})
        issues = [i for i in run_issue_rules("PatientTransportPlanner", ctxs, {})
                  if i["id"] == "roundtrip_order_violation_r1"]
        assert issues == []

    def test_phase_outside_time_window_fires(self):
        config = {"speed_kmh": 30.0, "boarding_time_min": 3, "hospital_location": {"lat": 0.0, "lng": 0.0}}
        requests = [{
            "id": "r1", "home_location": {"lat": 0.0, "lng": 0.0}, "appointment_time": "09:00",
            "exam_duration_min": 60, "wait_tolerance_min": 30, "boarding_time_min": 3,
            "capacity_required": 1, "care_type": "c", "request_type": "outbound_only",
        }]
        vehicles = [{"id": "v1", "name": "Van1", "capacity": 10}]
        request_results = [
            {"request_id": "r1", "req_type": "outbound_only", "care_type": "c", "served": True,
             "outbound_vehicle": "v1", "outbound_start": 100, "outbound_end": 130},
        ]
        ctxs = build_patient_transport_planner_contexts(request_results, requests, vehicles, config)
        issues = run_issue_rules("PatientTransportPlanner", ctxs, {})
        assert any(i["id"] == "phase_outside_time_window_r1_outbound" for i in issues)

    def test_phase_inside_time_window_no_fire(self):
        config = {"speed_kmh": 30.0, "boarding_time_min": 3, "hospital_location": {"lat": 0.0, "lng": 0.0}}
        requests = [{
            "id": "r1", "home_location": {"lat": 0.0, "lng": 0.0}, "appointment_time": "09:00",
            "exam_duration_min": 60, "wait_tolerance_min": 30, "boarding_time_min": 3,
            "capacity_required": 1, "care_type": "c", "request_type": "outbound_only",
        }]
        vehicles = [{"id": "v1", "name": "Van1", "capacity": 10}]
        # appointment_time 09:00 = 540分、travel=1分、boarding=3分
        # tw_start = max(0, 540-30-1-3) = 506, tw_end = max(507, 540-1-3) = 536
        request_results = [
            {"request_id": "r1", "req_type": "outbound_only", "care_type": "c", "served": True,
             "outbound_vehicle": "v1", "outbound_start": 520, "outbound_end": 536},
        ]
        ctxs = build_patient_transport_planner_contexts(request_results, requests, vehicles, config)
        issues = [i for i in run_issue_rules("PatientTransportPlanner", ctxs, {})
                  if i["id"] == "phase_outside_time_window_r1_outbound"]
        assert issues == []

    def test_registered_in_issue_rules(self):
        assert "PatientTransportPlanner" in ISSUE_RULES
