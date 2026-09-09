
import pytest
from solvers.base.issue_rules import (
    ISSUE_RULES,
    build_shift_rotation_scheduler_contexts,
    run_issue_rules,
)




# ---------------------------------------------------------------------------
# テスト: ShiftRotationScheduler 解チェッカー（2026-09-01新規、バッチ5）
# ---------------------------------------------------------------------------

def _srs_baseline():
    """全制約を満たすクリーンなW=1テンプレート（E×4, OFF×3、土日ともOFF）。"""
    template_shift_ids = [["E", "E", "E", "E", "OFF", "OFF", "OFF"]]
    weeks = [[
        {"shift_id": "E", "shift_type": "early"},
        {"shift_id": "E", "shift_type": "early"},
        {"shift_id": "E", "shift_type": "early"},
        {"shift_id": "E", "shift_type": "early"},
        {"shift_id": "OFF", "shift_type": "off"},
        {"shift_id": "OFF", "shift_type": "off"},
        {"shift_id": "OFF", "shift_type": "off"},
    ]]
    employee_schedules = [{"employee_id": "EMP00", "weeks": weeks}]
    shifts_def = [{"id": "E", "type": "early"}, {"id": "OFF", "type": "off"}]
    return template_shift_ids, employee_schedules, shifts_def


class TestShiftRotationSchedulerChecker:
    def test_weekend_shift_mismatch_fires(self):
        template_shift_ids = [["E", "E", "E", "E", "OFF", "E", "OFF"]]
        employee_schedules = [{"employee_id": "EMP00", "weeks": [[
            {"shift_id": "E", "shift_type": "early"}, {"shift_id": "E", "shift_type": "early"},
            {"shift_id": "E", "shift_type": "early"}, {"shift_id": "E", "shift_type": "early"},
            {"shift_id": "OFF", "shift_type": "off"}, {"shift_id": "E", "shift_type": "early"},
            {"shift_id": "OFF", "shift_type": "off"},
        ]]}]
        shifts_def = [{"id": "E", "type": "early"}, {"id": "OFF", "type": "off"}]
        ctxs = build_shift_rotation_scheduler_contexts(template_shift_ids, employee_schedules, shifts_def, [], {})
        issues = run_issue_rules("ShiftRotationScheduler", ctxs, {})
        assert any(i["id"] == "weekend_shift_mismatch_w0" for i in issues)
        assert all(i["category"] == "SOLVER" for i in issues)

    def test_weekend_shift_match_no_fire(self):
        template_shift_ids, employee_schedules, shifts_def = _srs_baseline()
        ctxs = build_shift_rotation_scheduler_contexts(template_shift_ids, employee_schedules, shifts_def, [], {})
        issues = [i for i in run_issue_rules("ShiftRotationScheduler", ctxs, {}) if i["id"] == "weekend_shift_mismatch_w0"]
        assert issues == []

    def test_daily_requirement_mismatch_fires(self):
        template_shift_ids, employee_schedules, shifts_def = _srs_baseline()
        daily_requirements = [{"day_of_week": 0, "shift_id": "E", "required": 2}]
        ctxs = build_shift_rotation_scheduler_contexts(template_shift_ids, employee_schedules, shifts_def, daily_requirements, {})
        issues = run_issue_rules("ShiftRotationScheduler", ctxs, {})
        assert any(i["id"] == "daily_requirement_mismatch_w0_d0_E" for i in issues)

    def test_daily_requirement_match_no_fire(self):
        template_shift_ids, employee_schedules, shifts_def = _srs_baseline()
        daily_requirements = [{"day_of_week": 0, "shift_id": "E", "required": 1}]
        ctxs = build_shift_rotation_scheduler_contexts(template_shift_ids, employee_schedules, shifts_def, daily_requirements, {})
        issues = [i for i in run_issue_rules("ShiftRotationScheduler", ctxs, {}) if i["id"] == "daily_requirement_mismatch_w0_d0_E"]
        assert issues == []

    def test_shift_progression_violation_fires(self):
        template_shift_ids = [["L", "E", "OFF", "OFF", "OFF", "OFF", "OFF"]]
        shifts_def = [{"id": "L", "type": "late"}, {"id": "E", "type": "early"}, {"id": "OFF", "type": "off"}]
        employee_schedules = [{"employee_id": "EMP00", "weeks": [[
            {"shift_id": "L", "shift_type": "late"}, {"shift_id": "E", "shift_type": "early"},
            {"shift_id": "OFF", "shift_type": "off"}, {"shift_id": "OFF", "shift_type": "off"},
            {"shift_id": "OFF", "shift_type": "off"}, {"shift_id": "OFF", "shift_type": "off"},
            {"shift_id": "OFF", "shift_type": "off"},
        ]]}]
        ctxs = build_shift_rotation_scheduler_contexts(template_shift_ids, employee_schedules, shifts_def, [], {})
        issues = run_issue_rules("ShiftRotationScheduler", ctxs, {})
        assert any(i["id"] == "shift_progression_violation_EMP00_day1" for i in issues)

    def test_shift_progression_forward_ok_no_fire(self):
        template_shift_ids = [["E", "L", "OFF", "OFF", "OFF", "OFF", "OFF"]]
        shifts_def = [{"id": "L", "type": "late"}, {"id": "E", "type": "early"}, {"id": "OFF", "type": "off"}]
        employee_schedules = [{"employee_id": "EMP00", "weeks": [[
            {"shift_id": "E", "shift_type": "early"}, {"shift_id": "L", "shift_type": "late"},
            {"shift_id": "OFF", "shift_type": "off"}, {"shift_id": "OFF", "shift_type": "off"},
            {"shift_id": "OFF", "shift_type": "off"}, {"shift_id": "OFF", "shift_type": "off"},
            {"shift_id": "OFF", "shift_type": "off"},
        ]]}]
        ctxs = build_shift_rotation_scheduler_contexts(template_shift_ids, employee_schedules, shifts_def, [], {})
        issues = [i for i in run_issue_rules("ShiftRotationScheduler", ctxs, {})
                  if i["id"].startswith("shift_progression_violation_")]
        assert issues == []

    def test_consecutive_run_length_violation_fires(self):
        template_shift_ids = [["E", "OFF", "E", "E", "E", "E", "E"]]
        shifts_def = [{"id": "E", "type": "early"}, {"id": "OFF", "type": "off"}]
        employee_schedules = [{"employee_id": "EMP00", "weeks": [[
            {"shift_id": "E", "shift_type": "early"}, {"shift_id": "OFF", "shift_type": "off"},
            {"shift_id": "E", "shift_type": "early"}, {"shift_id": "E", "shift_type": "early"},
            {"shift_id": "E", "shift_type": "early"}, {"shift_id": "E", "shift_type": "early"},
            {"shift_id": "E", "shift_type": "early"},
        ]]}]
        ctxs = build_shift_rotation_scheduler_contexts(template_shift_ids, employee_schedules, shifts_def, [], {})
        issues = run_issue_rules("ShiftRotationScheduler", ctxs, {})
        assert any(i["id"] == "consecutive_run_length_violation_EMP00_start1" for i in issues)

    def test_consecutive_run_length_ok_no_fire(self):
        template_shift_ids, employee_schedules, shifts_def = _srs_baseline()
        ctxs = build_shift_rotation_scheduler_contexts(template_shift_ids, employee_schedules, shifts_def, [], {})
        issues = [i for i in run_issue_rules("ShiftRotationScheduler", ctxs, {})
                  if i["id"].startswith("consecutive_run_length_violation_")]
        assert issues == []

    def test_days_off_window_violation_fires(self):
        template_shift_ids = [["E", "E", "E", "E", "E", "E", "E"]]
        shifts_def = [{"id": "E", "type": "early"}]
        employee_schedules = [{"employee_id": "EMP00", "weeks": [[
            {"shift_id": "E", "shift_type": "early"} for _ in range(7)
        ]]}]
        ctxs = build_shift_rotation_scheduler_contexts(template_shift_ids, employee_schedules, shifts_def, [], {})
        issues = run_issue_rules("ShiftRotationScheduler", ctxs, {})
        assert any(i["id"] == "days_off_window_violation_EMP00_start0" for i in issues)

    def test_days_off_window_ok_no_fire(self):
        template_shift_ids, employee_schedules, shifts_def = _srs_baseline()
        ctxs = build_shift_rotation_scheduler_contexts(template_shift_ids, employee_schedules, shifts_def, [], {})
        issues = [i for i in run_issue_rules("ShiftRotationScheduler", ctxs, {})
                  if i["id"].startswith("days_off_window_violation_")]
        assert issues == []

    def test_employee_schedule_derivation_mismatch_fires(self):
        template_shift_ids, employee_schedules, shifts_def = _srs_baseline()
        employee_schedules[0]["weeks"][0][0] = {"shift_id": "WRONG", "shift_type": "early"}
        ctxs = build_shift_rotation_scheduler_contexts(template_shift_ids, employee_schedules, shifts_def, [], {})
        issues = run_issue_rules("ShiftRotationScheduler", ctxs, {})
        assert any(i["id"] == "employee_schedule_derivation_mismatch_EMP00_w0_d0" for i in issues)

    def test_employee_schedule_derivation_match_no_fire(self):
        template_shift_ids, employee_schedules, shifts_def = _srs_baseline()
        ctxs = build_shift_rotation_scheduler_contexts(template_shift_ids, employee_schedules, shifts_def, [], {})
        issues = [i for i in run_issue_rules("ShiftRotationScheduler", ctxs, {})
                  if i["id"].startswith("employee_schedule_derivation_mismatch_")]
        assert issues == []

    def test_all_clean_no_fire(self):
        template_shift_ids, employee_schedules, shifts_def = _srs_baseline()
        daily_requirements = [{"day_of_week": 0, "shift_id": "E", "required": 1}]
        ctxs = build_shift_rotation_scheduler_contexts(template_shift_ids, employee_schedules, shifts_def, daily_requirements, {})
        issues = run_issue_rules("ShiftRotationScheduler", ctxs, {})
        assert issues == []

    def test_registered_in_issue_rules(self):
        assert "ShiftRotationScheduler" in ISSUE_RULES
