
import pytest
from solvers.base.issue_rules import (
    ISSUE_RULES,
    build_production_line_sequencing_contexts,
    run_issue_rules,
)





# ---------------------------------------------------------------------------
# テスト: ProductionLineSequencing 解チェッカー（2026-09-01新規、バッチ5）
# ---------------------------------------------------------------------------

class TestProductionLineSequencingChecker:
    def test_slot_double_booked_fires(self):
        assignments = [
            {"batch_id": "b1", "slot_id": "s1", "line_id": "L1", "day": 1, "start_hour": 8.0, "vehicle_type": "vt1"},
            {"batch_id": "b2", "slot_id": "s1", "line_id": "L1", "day": 1, "start_hour": 9.0, "vehicle_type": "vt1"},
        ]
        batches = [
            {"id": "b1", "compatible_lines": None, "line_on_day": 1, "line_off_day": 10, "vehicle_type": "vt1"},
            {"id": "b2", "compatible_lines": None, "line_on_day": 1, "line_off_day": 10, "vehicle_type": "vt1"},
        ]
        slots = [{"id": "s1", "line_id": "L1", "day": 1, "start_hour": 8.0}]
        vehicle_types = [{"id": "vt1", "daily_limit": 99, "priority_order": 1}]
        ctxs = build_production_line_sequencing_contexts(assignments, batches, slots, vehicle_types, [])
        issues = run_issue_rules("ProductionLineSequencing", ctxs, {})
        assert any(i["id"] == "slot_double_booked_s1" for i in issues)
        assert all(i["category"] == "SOLVER" for i in issues)

    def test_slot_double_booked_no_fire(self):
        assignments = [
            {"batch_id": "b1", "slot_id": "s1", "line_id": "L1", "day": 1, "start_hour": 8.0, "vehicle_type": "vt1"},
            {"batch_id": "b2", "slot_id": "s2", "line_id": "L1", "day": 1, "start_hour": 9.0, "vehicle_type": "vt1"},
        ]
        batches = [
            {"id": "b1", "compatible_lines": None, "line_on_day": 1, "line_off_day": 10, "vehicle_type": "vt1"},
            {"id": "b2", "compatible_lines": None, "line_on_day": 1, "line_off_day": 10, "vehicle_type": "vt1"},
        ]
        slots = [
            {"id": "s1", "line_id": "L1", "day": 1, "start_hour": 8.0},
            {"id": "s2", "line_id": "L1", "day": 1, "start_hour": 9.0},
        ]
        vehicle_types = [{"id": "vt1", "daily_limit": 99, "priority_order": 1}]
        ctxs = build_production_line_sequencing_contexts(assignments, batches, slots, vehicle_types, [])
        issues = run_issue_rules("ProductionLineSequencing", ctxs, {})
        assert issues == []

    def test_batch_incompatible_line_fires(self):
        assignments = [{"batch_id": "b1", "slot_id": "s1", "line_id": "L2", "day": 1, "start_hour": 8.0, "vehicle_type": "vt1"}]
        batches = [{"id": "b1", "compatible_lines": ["L1"], "line_on_day": 1, "line_off_day": 10, "vehicle_type": "vt1"}]
        slots = [{"id": "s1", "line_id": "L2", "day": 1, "start_hour": 8.0}]
        vehicle_types = [{"id": "vt1", "daily_limit": 99, "priority_order": 1}]
        ctxs = build_production_line_sequencing_contexts(assignments, batches, slots, vehicle_types, [])
        issues = run_issue_rules("ProductionLineSequencing", ctxs, {})
        assert any(i["id"] == "batch_incompatible_line_b1" for i in issues)

    def test_batch_compatible_line_no_fire(self):
        assignments = [{"batch_id": "b1", "slot_id": "s1", "line_id": "L2", "day": 1, "start_hour": 8.0, "vehicle_type": "vt1"}]
        batches = [{"id": "b1", "compatible_lines": ["L1", "L2"], "line_on_day": 1, "line_off_day": 10, "vehicle_type": "vt1"}]
        slots = [{"id": "s1", "line_id": "L2", "day": 1, "start_hour": 8.0}]
        vehicle_types = [{"id": "vt1", "daily_limit": 99, "priority_order": 1}]
        ctxs = build_production_line_sequencing_contexts(assignments, batches, slots, vehicle_types, [])
        issues = [i for i in run_issue_rules("ProductionLineSequencing", ctxs, {}) if i["id"] == "batch_incompatible_line_b1"]
        assert issues == []

    def test_batch_outside_line_window_fires(self):
        assignments = [{"batch_id": "b1", "slot_id": "s1", "line_id": "L1", "day": 15, "start_hour": 8.0, "vehicle_type": "vt1"}]
        batches = [{"id": "b1", "compatible_lines": None, "line_on_day": 1, "line_off_day": 10, "vehicle_type": "vt1"}]
        slots = [{"id": "s1", "line_id": "L1", "day": 15, "start_hour": 8.0}]
        vehicle_types = [{"id": "vt1", "daily_limit": 99, "priority_order": 1}]
        ctxs = build_production_line_sequencing_contexts(assignments, batches, slots, vehicle_types, [])
        issues = run_issue_rules("ProductionLineSequencing", ctxs, {})
        assert any(i["id"] == "batch_outside_line_window_b1" for i in issues)

    def test_batch_inside_line_window_no_fire(self):
        assignments = [{"batch_id": "b1", "slot_id": "s1", "line_id": "L1", "day": 5, "start_hour": 8.0, "vehicle_type": "vt1"}]
        batches = [{"id": "b1", "compatible_lines": None, "line_on_day": 1, "line_off_day": 10, "vehicle_type": "vt1"}]
        slots = [{"id": "s1", "line_id": "L1", "day": 5, "start_hour": 8.0}]
        vehicle_types = [{"id": "vt1", "daily_limit": 99, "priority_order": 1}]
        ctxs = build_production_line_sequencing_contexts(assignments, batches, slots, vehicle_types, [])
        issues = [i for i in run_issue_rules("ProductionLineSequencing", ctxs, {}) if i["id"] == "batch_outside_line_window_b1"]
        assert issues == []

    def test_distribution_daily_max_violation_fires(self):
        assignments = [
            {"batch_id": f"b{i}", "slot_id": f"s{i}", "line_id": "L1", "day": 1, "start_hour": 8.0 + i, "vehicle_type": "vt1"}
            for i in range(3)
        ]
        batches = [
            {"id": f"b{i}", "compatible_lines": None, "line_on_day": 1, "line_off_day": 10, "vehicle_type": "vt1"}
            for i in range(3)
        ]
        slots = [{"id": f"s{i}", "line_id": "L1", "day": 1, "start_hour": 8.0 + i} for i in range(3)]
        vehicle_types = [{"id": "vt1", "daily_limit": 2, "priority_order": 1}]
        ctxs = build_production_line_sequencing_contexts(assignments, batches, slots, vehicle_types, [])
        issues = run_issue_rules("ProductionLineSequencing", ctxs, {})
        assert any(i["id"] == "distribution_daily_max_violation_1_vt1" for i in issues)

    def test_distribution_daily_max_ok_no_fire(self):
        assignments = [
            {"batch_id": f"b{i}", "slot_id": f"s{i}", "line_id": "L1", "day": 1, "start_hour": 8.0 + i, "vehicle_type": "vt1"}
            for i in range(3)
        ]
        batches = [
            {"id": f"b{i}", "compatible_lines": None, "line_on_day": 1, "line_off_day": 10, "vehicle_type": "vt1"}
            for i in range(3)
        ]
        slots = [{"id": f"s{i}", "line_id": "L1", "day": 1, "start_hour": 8.0 + i} for i in range(3)]
        vehicle_types = [{"id": "vt1", "daily_limit": 3, "priority_order": 1}]
        ctxs = build_production_line_sequencing_contexts(assignments, batches, slots, vehicle_types, [])
        issues = [i for i in run_issue_rules("ProductionLineSequencing", ctxs, {})
                  if i["id"] == "distribution_daily_max_violation_1_vt1"]
        assert issues == []

    def test_distribution_daily_min_violation_fires(self):
        assignments = [{"batch_id": "b1", "slot_id": "s1", "line_id": "L1", "day": 1, "start_hour": 8.0, "vehicle_type": "vt2"}]
        batches = [{"id": "b1", "compatible_lines": None, "line_on_day": 1, "line_off_day": 10, "vehicle_type": "vt2"}]
        slots = [{"id": "s1", "line_id": "L1", "day": 1, "start_hour": 8.0}]
        vehicle_types = [{"id": "vt2", "daily_limit": 99, "priority_order": 1}]
        distribution_exceptions = [{"period_days": [1], "vehicle_type_id": "vt2", "min_per_day": 2}]
        ctxs = build_production_line_sequencing_contexts(assignments, batches, slots, vehicle_types, distribution_exceptions)
        issues = run_issue_rules("ProductionLineSequencing", ctxs, {})
        assert any(i["id"] == "distribution_daily_min_violation_1_vt2" for i in issues)

    def test_distribution_daily_min_ok_no_fire(self):
        assignments = [
            {"batch_id": "b1", "slot_id": "s1", "line_id": "L1", "day": 1, "start_hour": 8.0, "vehicle_type": "vt2"},
            {"batch_id": "b2", "slot_id": "s2", "line_id": "L1", "day": 1, "start_hour": 9.0, "vehicle_type": "vt2"},
        ]
        batches = [
            {"id": "b1", "compatible_lines": None, "line_on_day": 1, "line_off_day": 10, "vehicle_type": "vt2"},
            {"id": "b2", "compatible_lines": None, "line_on_day": 1, "line_off_day": 10, "vehicle_type": "vt2"},
        ]
        slots = [
            {"id": "s1", "line_id": "L1", "day": 1, "start_hour": 8.0},
            {"id": "s2", "line_id": "L1", "day": 1, "start_hour": 9.0},
        ]
        vehicle_types = [{"id": "vt2", "daily_limit": 99, "priority_order": 1}]
        distribution_exceptions = [{"period_days": [1], "vehicle_type_id": "vt2", "min_per_day": 2}]
        ctxs = build_production_line_sequencing_contexts(assignments, batches, slots, vehicle_types, distribution_exceptions)
        issues = [i for i in run_issue_rules("ProductionLineSequencing", ctxs, {})
                  if i["id"] == "distribution_daily_min_violation_1_vt2"]
        assert issues == []

    def test_batting_order_violation_fires(self):
        assignments = [
            {"batch_id": "b1", "slot_id": "s1", "line_id": "L1", "day": 1, "start_hour": 10.0, "vehicle_type": "vtA"},
            {"batch_id": "b2", "slot_id": "s2", "line_id": "L1", "day": 1, "start_hour": 5.0, "vehicle_type": "vtB"},
        ]
        batches = [
            {"id": "b1", "compatible_lines": None, "line_on_day": 1, "line_off_day": 10, "vehicle_type": "vtA"},
            {"id": "b2", "compatible_lines": None, "line_on_day": 1, "line_off_day": 10, "vehicle_type": "vtB"},
        ]
        slots = [
            {"id": "s1", "line_id": "L1", "day": 1, "start_hour": 10.0},
            {"id": "s2", "line_id": "L1", "day": 1, "start_hour": 5.0},
        ]
        vehicle_types = [{"id": "vtA", "daily_limit": 99, "priority_order": 1}, {"id": "vtB", "daily_limit": 99, "priority_order": 2}]
        ctxs = build_production_line_sequencing_contexts(assignments, batches, slots, vehicle_types, [])
        issues = run_issue_rules("ProductionLineSequencing", ctxs, {})
        assert any(i["id"] == "batting_order_violation_1_vtA_vtB" for i in issues)

    def test_batting_order_ok_no_fire(self):
        assignments = [
            {"batch_id": "b1", "slot_id": "s1", "line_id": "L1", "day": 1, "start_hour": 5.0, "vehicle_type": "vtA"},
            {"batch_id": "b2", "slot_id": "s2", "line_id": "L1", "day": 1, "start_hour": 10.0, "vehicle_type": "vtB"},
        ]
        batches = [
            {"id": "b1", "compatible_lines": None, "line_on_day": 1, "line_off_day": 10, "vehicle_type": "vtA"},
            {"id": "b2", "compatible_lines": None, "line_on_day": 1, "line_off_day": 10, "vehicle_type": "vtB"},
        ]
        slots = [
            {"id": "s1", "line_id": "L1", "day": 1, "start_hour": 5.0},
            {"id": "s2", "line_id": "L1", "day": 1, "start_hour": 10.0},
        ]
        vehicle_types = [{"id": "vtA", "daily_limit": 99, "priority_order": 1}, {"id": "vtB", "daily_limit": 99, "priority_order": 2}]
        ctxs = build_production_line_sequencing_contexts(assignments, batches, slots, vehicle_types, [])
        issues = [i for i in run_issue_rules("ProductionLineSequencing", ctxs, {})
                  if i["id"] == "batting_order_violation_1_vtA_vtB"]
        assert issues == []

    def test_assignment_unknown_slot_fires(self):
        assignments = [{"batch_id": "b1", "slot_id": "sX", "line_id": "L1", "day": 1, "start_hour": 8.0, "vehicle_type": "vt1"}]
        batches = [{"id": "b1", "compatible_lines": None, "line_on_day": 1, "line_off_day": 10, "vehicle_type": "vt1"}]
        vehicle_types = [{"id": "vt1", "daily_limit": 99, "priority_order": 1}]
        ctxs = build_production_line_sequencing_contexts(assignments, batches, [], vehicle_types, [])
        issues = run_issue_rules("ProductionLineSequencing", ctxs, {})
        assert any(i["id"] == "assignment_unknown_slot_b1_sX" for i in issues)

    def test_assignment_known_slot_no_fire(self):
        assignments = [{"batch_id": "b1", "slot_id": "sX", "line_id": "L1", "day": 1, "start_hour": 8.0, "vehicle_type": "vt1"}]
        batches = [{"id": "b1", "compatible_lines": None, "line_on_day": 1, "line_off_day": 10, "vehicle_type": "vt1"}]
        slots = [{"id": "sX", "line_id": "L1", "day": 1, "start_hour": 8.0}]
        vehicle_types = [{"id": "vt1", "daily_limit": 99, "priority_order": 1}]
        ctxs = build_production_line_sequencing_contexts(assignments, batches, slots, vehicle_types, [])
        issues = [i for i in run_issue_rules("ProductionLineSequencing", ctxs, {}) if i["id"] == "assignment_unknown_slot_b1_sX"]
        assert issues == []

    def test_assignment_field_mismatch_fires(self):
        assignments = [{"batch_id": "b1", "slot_id": "s1", "line_id": "L1", "day": 1, "start_hour": 8.0, "vehicle_type": "vt1"}]
        batches = [{"id": "b1", "compatible_lines": None, "line_on_day": 1, "line_off_day": 10, "vehicle_type": "vt1"}]
        slots = [{"id": "s1", "line_id": "L2", "day": 1, "start_hour": 8.0}]
        vehicle_types = [{"id": "vt1", "daily_limit": 99, "priority_order": 1}]
        ctxs = build_production_line_sequencing_contexts(assignments, batches, slots, vehicle_types, [])
        issues = run_issue_rules("ProductionLineSequencing", ctxs, {})
        assert any(i["id"] == "assignment_field_mismatch_b1" for i in issues)

    def test_assignment_field_match_no_fire(self):
        assignments = [{"batch_id": "b1", "slot_id": "s1", "line_id": "L1", "day": 1, "start_hour": 8.0, "vehicle_type": "vt1"}]
        batches = [{"id": "b1", "compatible_lines": None, "line_on_day": 1, "line_off_day": 10, "vehicle_type": "vt1"}]
        slots = [{"id": "s1", "line_id": "L1", "day": 1, "start_hour": 8.0}]
        vehicle_types = [{"id": "vt1", "daily_limit": 99, "priority_order": 1}]
        ctxs = build_production_line_sequencing_contexts(assignments, batches, slots, vehicle_types, [])
        issues = [i for i in run_issue_rules("ProductionLineSequencing", ctxs, {}) if i["id"] == "assignment_field_mismatch_b1"]
        assert issues == []

    def test_assignment_vehicle_type_mismatch_fires(self):
        assignments = [{"batch_id": "b1", "slot_id": "s1", "line_id": "L1", "day": 1, "start_hour": 8.0, "vehicle_type": "vtX"}]
        batches = [{"id": "b1", "compatible_lines": None, "line_on_day": 1, "line_off_day": 10, "vehicle_type": "vtY"}]
        slots = [{"id": "s1", "line_id": "L1", "day": 1, "start_hour": 8.0}]
        vehicle_types = [{"id": "vtX", "daily_limit": 99, "priority_order": 1}]
        ctxs = build_production_line_sequencing_contexts(assignments, batches, slots, vehicle_types, [])
        issues = run_issue_rules("ProductionLineSequencing", ctxs, {})
        assert any(i["id"] == "assignment_vehicle_type_mismatch_b1" for i in issues)

    def test_assignment_vehicle_type_match_no_fire(self):
        assignments = [{"batch_id": "b1", "slot_id": "s1", "line_id": "L1", "day": 1, "start_hour": 8.0, "vehicle_type": "vtX"}]
        batches = [{"id": "b1", "compatible_lines": None, "line_on_day": 1, "line_off_day": 10, "vehicle_type": "vtX"}]
        slots = [{"id": "s1", "line_id": "L1", "day": 1, "start_hour": 8.0}]
        vehicle_types = [{"id": "vtX", "daily_limit": 99, "priority_order": 1}]
        ctxs = build_production_line_sequencing_contexts(assignments, batches, slots, vehicle_types, [])
        issues = [i for i in run_issue_rules("ProductionLineSequencing", ctxs, {}) if i["id"] == "assignment_vehicle_type_mismatch_b1"]
        assert issues == []

    def test_all_clean_no_fire(self):
        batches = [
            {"id": "b1", "compatible_lines": ["L1"], "line_on_day": 1, "line_off_day": 10, "vehicle_type": "vtA"},
            {"id": "b2", "compatible_lines": ["L1"], "line_on_day": 1, "line_off_day": 10, "vehicle_type": "vtB"},
        ]
        slots = [
            {"id": "s1", "line_id": "L1", "day": 1, "start_hour": 5.0},
            {"id": "s2", "line_id": "L1", "day": 1, "start_hour": 10.0},
        ]
        assignments = [
            {"batch_id": "b1", "slot_id": "s1", "line_id": "L1", "day": 1, "start_hour": 5.0, "vehicle_type": "vtA"},
            {"batch_id": "b2", "slot_id": "s2", "line_id": "L1", "day": 1, "start_hour": 10.0, "vehicle_type": "vtB"},
        ]
        vehicle_types = [{"id": "vtA", "daily_limit": 5, "priority_order": 1}, {"id": "vtB", "daily_limit": 5, "priority_order": 2}]
        ctxs = build_production_line_sequencing_contexts(assignments, batches, slots, vehicle_types, [])
        issues = run_issue_rules("ProductionLineSequencing", ctxs, {})
        assert issues == []

    def test_registered_in_issue_rules(self):
        assert "ProductionLineSequencing" in ISSUE_RULES
