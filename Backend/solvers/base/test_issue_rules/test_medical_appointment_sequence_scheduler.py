
import pytest
from solvers.base.issue_rules import (
    ISSUE_RULES,
    build_medical_appointment_sequence_scheduler_contexts,
    run_issue_rules,
)




# ---------------------------------------------------------------------------
# テスト: MedicalAppointmentSequenceScheduler 解チェッカー（2026-09-01新規、バッチ4）
# ---------------------------------------------------------------------------

class TestMedicalAppointmentSequenceSchedulerChecker:
    def test_resource_double_booking_fires(self):
        scheduled = [{"sequence_id": "s1", "visits": [
            {"visit_id": "v1", "visit_order": 0, "duration_min": 30, "start_min": 0, "end_min": 30,
             "assigned_resources": [{"resource_id": "res1", "resource_type": "doctor", "start_min": 0, "end_min": 30}]},
            {"visit_id": "v2", "visit_order": 1, "duration_min": 30, "start_min": 15, "end_min": 45,
             "assigned_resources": [{"resource_id": "res1", "resource_type": "doctor", "start_min": 15, "end_min": 45}]},
        ]}]
        sequences = [{"id": "s1", "visits": [
            {"id": "v1", "order": 0, "required_resources": [{"type": "doctor", "count": 1}]},
            {"id": "v2", "order": 1, "required_resources": [{"type": "doctor", "count": 1}]},
        ], "interval_rules": [], "same_resource_rules": []}]
        resources = [{"id": "res1", "name": "DrA", "type": "doctor",
                       "available_slots": [{"start_min": 0, "end_min": 1000}]}]
        ctxs = build_medical_appointment_sequence_scheduler_contexts(scheduled, sequences, resources)
        issues = run_issue_rules("MedicalAppointmentSequenceScheduler", ctxs, {})
        assert any(i["id"] == "resource_double_booking_res1" for i in issues)
        assert all(i["category"] == "SOLVER" for i in issues)

    def test_visit_outside_resource_window_fires(self):
        scheduled = [{"sequence_id": "s1", "visits": [
            {"visit_id": "v1", "visit_order": 0, "duration_min": 30, "start_min": 0, "end_min": 30,
             "assigned_resources": [{"resource_id": "res1", "resource_type": "doctor", "start_min": 0, "end_min": 30}]},
        ]}]
        sequences = [{"id": "s1", "visits": [
            {"id": "v1", "order": 0, "required_resources": [{"type": "doctor", "count": 1}]},
        ], "interval_rules": [], "same_resource_rules": []}]
        resources = [{"id": "res1", "name": "DrA", "type": "doctor",
                       "available_slots": [{"start_min": 100, "end_min": 200}]}]
        ctxs = build_medical_appointment_sequence_scheduler_contexts(scheduled, sequences, resources)
        issues = run_issue_rules("MedicalAppointmentSequenceScheduler", ctxs, {})
        assert any(i["id"] == "visit_outside_resource_window_v1_res1" for i in issues)

    def test_sequence_gap_violation_fires(self):
        scheduled = [{"sequence_id": "s1", "visits": [
            {"visit_id": "v1", "visit_order": 0, "duration_min": 30, "start_min": 0, "end_min": 30,
             "assigned_resources": [{"resource_id": "res1", "resource_type": "doctor", "start_min": 0, "end_min": 30}]},
            {"visit_id": "v2", "visit_order": 1, "duration_min": 30, "start_min": 20, "end_min": 50,
             "assigned_resources": [{"resource_id": "res2", "resource_type": "nurse", "start_min": 20, "end_min": 50}]},
        ]}]
        sequences = [{"id": "s1", "visits": [
            {"id": "v1", "order": 0, "required_resources": [{"type": "doctor", "count": 1}]},
            {"id": "v2", "order": 1, "required_resources": [{"type": "nurse", "count": 1}]},
        ], "interval_rules": [], "same_resource_rules": []}]
        resources = [
            {"id": "res1", "name": "DrA", "type": "doctor", "available_slots": [{"start_min": 0, "end_min": 1000}]},
            {"id": "res2", "name": "NsB", "type": "nurse", "available_slots": [{"start_min": 0, "end_min": 1000}]},
        ]
        ctxs = build_medical_appointment_sequence_scheduler_contexts(scheduled, sequences, resources)
        issues = run_issue_rules("MedicalAppointmentSequenceScheduler", ctxs, {})
        assert any(i["id"] == "sequence_gap_violation_s1_v1_v2" for i in issues)

    def test_same_resource_rule_violation_fires(self):
        scheduled = [{"sequence_id": "s1", "visits": [
            {"visit_id": "va", "visit_order": 0, "duration_min": 30, "start_min": 0, "end_min": 30,
             "assigned_resources": [{"resource_id": "res1", "resource_type": "doctor", "start_min": 0, "end_min": 30}]},
            {"visit_id": "vb", "visit_order": 1, "duration_min": 30, "start_min": 30, "end_min": 60,
             "assigned_resources": [{"resource_id": "res2", "resource_type": "doctor", "start_min": 30, "end_min": 60}]},
        ]}]
        sequences = [{"id": "s1", "visits": [
            {"id": "va", "order": 0, "required_resources": [{"type": "doctor", "count": 1}]},
            {"id": "vb", "order": 1, "required_resources": [{"type": "doctor", "count": 1}]},
        ], "interval_rules": [], "same_resource_rules": [{"visit_a": 0, "visit_b": 1}]}]
        resources = [
            {"id": "res1", "name": "Dr1", "type": "doctor", "available_slots": [{"start_min": 0, "end_min": 1000}]},
            {"id": "res2", "name": "Dr2", "type": "doctor", "available_slots": [{"start_min": 0, "end_min": 1000}]},
        ]
        ctxs = build_medical_appointment_sequence_scheduler_contexts(scheduled, sequences, resources)
        issues = run_issue_rules("MedicalAppointmentSequenceScheduler", ctxs, {})
        assert any(i["id"] == "same_resource_rule_violation_s1_va_vb" for i in issues)

    def test_same_resource_rule_ok_no_fire(self):
        scheduled = [{"sequence_id": "s1", "visits": [
            {"visit_id": "va", "visit_order": 0, "duration_min": 30, "start_min": 0, "end_min": 30,
             "assigned_resources": [{"resource_id": "res1", "resource_type": "doctor", "start_min": 0, "end_min": 30}]},
            {"visit_id": "vb", "visit_order": 1, "duration_min": 30, "start_min": 30, "end_min": 60,
             "assigned_resources": [{"resource_id": "res1", "resource_type": "doctor", "start_min": 30, "end_min": 60}]},
        ]}]
        sequences = [{"id": "s1", "visits": [
            {"id": "va", "order": 0, "required_resources": [{"type": "doctor", "count": 1}]},
            {"id": "vb", "order": 1, "required_resources": [{"type": "doctor", "count": 1}]},
        ], "interval_rules": [], "same_resource_rules": [{"visit_a": 0, "visit_b": 1}]}]
        resources = [{"id": "res1", "name": "Dr1", "type": "doctor", "available_slots": [{"start_min": 0, "end_min": 1000}]}]
        ctxs = build_medical_appointment_sequence_scheduler_contexts(scheduled, sequences, resources)
        issues = run_issue_rules("MedicalAppointmentSequenceScheduler", ctxs, {})
        assert issues == []

    def test_visit_resource_time_mismatch_fires(self):
        scheduled = [{"sequence_id": "s1", "visits": [
            {"visit_id": "v1", "visit_order": 0, "duration_min": 30, "start_min": 0, "end_min": 35,
             "assigned_resources": [
                 {"resource_id": "res1", "resource_type": "doctor", "start_min": 0, "end_min": 30},
                 {"resource_id": "res2", "resource_type": "nurse", "start_min": 5, "end_min": 35},
             ]},
        ]}]
        sequences = [{"id": "s1", "visits": [
            {"id": "v1", "order": 0, "required_resources": [{"type": "doctor", "count": 1}, {"type": "nurse", "count": 1}]},
        ], "interval_rules": [], "same_resource_rules": []}]
        resources = [
            {"id": "res1", "name": "Dr", "type": "doctor", "available_slots": [{"start_min": 0, "end_min": 1000}]},
            {"id": "res2", "name": "Ns", "type": "nurse", "available_slots": [{"start_min": 0, "end_min": 1000}]},
        ]
        ctxs = build_medical_appointment_sequence_scheduler_contexts(scheduled, sequences, resources)
        issues = run_issue_rules("MedicalAppointmentSequenceScheduler", ctxs, {})
        assert any(i["id"] == "visit_resource_time_mismatch_v1" for i in issues)

    def test_visit_resource_time_match_no_fire(self):
        scheduled = [{"sequence_id": "s1", "visits": [
            {"visit_id": "v1", "visit_order": 0, "duration_min": 30, "start_min": 0, "end_min": 30,
             "assigned_resources": [
                 {"resource_id": "res1", "resource_type": "doctor", "start_min": 0, "end_min": 30},
                 {"resource_id": "res2", "resource_type": "nurse", "start_min": 0, "end_min": 30},
             ]},
        ]}]
        sequences = [{"id": "s1", "visits": [
            {"id": "v1", "order": 0, "required_resources": [{"type": "doctor", "count": 1}, {"type": "nurse", "count": 1}]},
        ], "interval_rules": [], "same_resource_rules": []}]
        resources = [
            {"id": "res1", "name": "Dr", "type": "doctor", "available_slots": [{"start_min": 0, "end_min": 1000}]},
            {"id": "res2", "name": "Ns", "type": "nurse", "available_slots": [{"start_min": 0, "end_min": 1000}]},
        ]
        ctxs = build_medical_appointment_sequence_scheduler_contexts(scheduled, sequences, resources)
        issues = run_issue_rules("MedicalAppointmentSequenceScheduler", ctxs, {})
        assert issues == []

    def test_all_clean_no_fire(self):
        sequences = [{"id": "s1", "visits": [
            {"id": "v1", "order": 0, "required_resources": [{"type": "doctor", "count": 1}]},
            {"id": "v2", "order": 1, "required_resources": [{"type": "doctor", "count": 1}]},
        ], "interval_rules": [], "same_resource_rules": [{"visit_a": 0, "visit_b": 1}]}]
        scheduled = [{"sequence_id": "s1", "visits": [
            {"visit_id": "v1", "visit_order": 0, "duration_min": 30, "start_min": 0, "end_min": 30,
             "assigned_resources": [{"resource_id": "res1", "resource_type": "doctor", "start_min": 0, "end_min": 30}]},
            {"visit_id": "v2", "visit_order": 1, "duration_min": 30, "start_min": 30, "end_min": 60,
             "assigned_resources": [{"resource_id": "res1", "resource_type": "doctor", "start_min": 30, "end_min": 60}]},
        ]}]
        resources = [{"id": "res1", "name": "Dr1", "type": "doctor", "available_slots": [{"start_min": 0, "end_min": 1000}]}]
        ctxs = build_medical_appointment_sequence_scheduler_contexts(scheduled, sequences, resources)
        issues = run_issue_rules("MedicalAppointmentSequenceScheduler", ctxs, {})
        assert issues == []

    def test_registered_in_issue_rules(self):
        assert "MedicalAppointmentSequenceScheduler" in ISSUE_RULES
