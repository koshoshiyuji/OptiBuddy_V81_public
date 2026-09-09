
import pytest
from solvers.base.issue_rules import (
    ISSUE_RULES,
    build_medical_appointment_scheduler_contexts,
    run_issue_rules,
)





# ---------------------------------------------------------------------------
# テスト: MedicalAppointmentScheduler 解チェッカー（2026-09-01新規、バッチ4）
# ---------------------------------------------------------------------------

class TestMedicalAppointmentSchedulerChecker:
    def test_resource_double_booking_fires(self):
        assignments = [
            {"request_id": "r1", "day": "2026-09-01", "start_min": 0, "end_min": 60,
             "assigned_resources": [{"resource_id": "res1", "resource_type": "doctor"}]},
            {"request_id": "r2", "day": "2026-09-01", "start_min": 30, "end_min": 90,
             "assigned_resources": [{"resource_id": "res1", "resource_type": "doctor"}]},
        ]
        requests = [
            {"id": "r1", "resource_type_needs": ["doctor"], "avoid_days": []},
            {"id": "r2", "resource_type_needs": ["doctor"], "avoid_days": []},
        ]
        resources = [{"id": "res1", "name": "DrA", "resource_type": "doctor"}]
        ctxs = build_medical_appointment_scheduler_contexts(assignments, requests, resources)
        issues = run_issue_rules("MedicalAppointmentScheduler", ctxs, {})
        assert any(i["id"] == "resource_double_booking_res1_2026-09-01" for i in issues)
        assert all(i["category"] == "SOLVER" for i in issues)

    def test_resource_double_booking_no_fire(self):
        assignments = [
            {"request_id": "r1", "day": "2026-09-01", "start_min": 0, "end_min": 60,
             "assigned_resources": [{"resource_id": "res1", "resource_type": "doctor"}]},
            {"request_id": "r2", "day": "2026-09-01", "start_min": 60, "end_min": 120,
             "assigned_resources": [{"resource_id": "res1", "resource_type": "doctor"}]},
        ]
        requests = [
            {"id": "r1", "resource_type_needs": ["doctor"], "avoid_days": []},
            {"id": "r2", "resource_type_needs": ["doctor"], "avoid_days": []},
        ]
        resources = [{"id": "res1", "name": "DrA", "resource_type": "doctor"}]
        ctxs = build_medical_appointment_scheduler_contexts(assignments, requests, resources)
        issues = run_issue_rules("MedicalAppointmentScheduler", ctxs, {})
        assert issues == []

    def test_resource_type_mismatch_fires(self):
        assignments = [
            {"request_id": "r1", "day": "2026-09-01", "start_min": 0, "end_min": 10,
             "assigned_resources": [{"resource_id": "res1", "resource_type": "nurse"}]},
        ]
        requests = [{"id": "r1", "resource_type_needs": ["doctor"], "avoid_days": []}]
        resources = [{"id": "res1", "name": "N", "resource_type": "nurse"}]
        ctxs = build_medical_appointment_scheduler_contexts(assignments, requests, resources)
        issues = run_issue_rules("MedicalAppointmentScheduler", ctxs, {})
        assert any(i["id"] == "resource_type_mismatch_r1" for i in issues)

    def test_resource_type_match_no_fire(self):
        assignments = [
            {"request_id": "r1", "day": "2026-09-01", "start_min": 0, "end_min": 10,
             "assigned_resources": [{"resource_id": "res1", "resource_type": "doctor"}]},
        ]
        requests = [{"id": "r1", "resource_type_needs": ["doctor"], "avoid_days": []}]
        resources = [{"id": "res1", "name": "DrA", "resource_type": "doctor"}]
        ctxs = build_medical_appointment_scheduler_contexts(assignments, requests, resources)
        issues = run_issue_rules("MedicalAppointmentScheduler", ctxs, {})
        assert issues == []

    def test_avoid_day_violation_fires(self):
        assignments = [
            {"request_id": "r1", "day": "2026-09-01", "start_min": 0, "end_min": 10,
             "assigned_resources": []},
        ]
        requests = [{"id": "r1", "resource_type_needs": [], "avoid_days": ["2026-09-01"]}]
        resources = []
        ctxs = build_medical_appointment_scheduler_contexts(assignments, requests, resources)
        issues = run_issue_rules("MedicalAppointmentScheduler", ctxs, {})
        assert any(i["id"] == "avoid_day_violation_r1" for i in issues)

    def test_avoid_day_ok_no_fire(self):
        assignments = [
            {"request_id": "r1", "day": "2026-09-01", "start_min": 0, "end_min": 10,
             "assigned_resources": []},
        ]
        requests = [{"id": "r1", "resource_type_needs": [], "avoid_days": ["2026-09-02"]}]
        resources = []
        ctxs = build_medical_appointment_scheduler_contexts(assignments, requests, resources)
        issues = run_issue_rules("MedicalAppointmentScheduler", ctxs, {})
        assert issues == []

    def test_registered_in_issue_rules(self):
        assert "MedicalAppointmentScheduler" in ISSUE_RULES
