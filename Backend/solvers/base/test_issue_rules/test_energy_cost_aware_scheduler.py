
import pytest
from solvers.base.issue_rules import (
    ISSUE_RULES,
    build_energy_cost_aware_scheduler_contexts,
    run_issue_rules,
)




# ---------------------------------------------------------------------------
# テスト: EnergyCostAwareScheduler 解チェッカー（2026-09-01新規、バッチ3）
# ---------------------------------------------------------------------------

class TestEnergyCostAwareSchedulerChecker:
    def test_line_power_capacity_violation_fires(self):
        lines = [{"id": "l1", "name": "Line1", "max_power_kw": 5,
                   "max_workers": 9999, "max_equipment_slots": 9999, "standby_power_kw": 0}]
        orders = [
            {"id": "o1", "power_kw": 3, "workers": 0, "equipment_slots": 0},
            {"id": "o2", "power_kw": 4, "workers": 0, "equipment_slots": 0},
        ]
        schedule = [
            {"order_id": "o1", "line_id": "l1", "start": 0, "end": 10},
            {"order_id": "o2", "line_id": "l1", "start": 5, "end": 15},
        ]
        line_ops = [{"line_id": "l1", "start": 0, "end": 15}]
        ctxs = build_energy_cost_aware_scheduler_contexts(schedule, line_ops, orders, lines)
        issues = run_issue_rules("EnergyCostAwareScheduler", ctxs, {})
        assert any(i["id"] == "line_capacity_violation_power_l1" for i in issues)
        assert all(i["category"] == "SOLVER" for i in issues)

    def test_line_power_capacity_ok_no_fire(self):
        lines = [{"id": "l1", "name": "Line1", "max_power_kw": 10,
                   "max_workers": 9999, "max_equipment_slots": 9999, "standby_power_kw": 0}]
        orders = [
            {"id": "o1", "power_kw": 3, "workers": 0, "equipment_slots": 0},
            {"id": "o2", "power_kw": 4, "workers": 0, "equipment_slots": 0},
        ]
        schedule = [
            {"order_id": "o1", "line_id": "l1", "start": 0, "end": 10},
            {"order_id": "o2", "line_id": "l1", "start": 5, "end": 15},
        ]
        line_ops = [{"line_id": "l1", "start": 0, "end": 15}]
        ctxs = build_energy_cost_aware_scheduler_contexts(schedule, line_ops, orders, lines)
        issues = run_issue_rules("EnergyCostAwareScheduler", ctxs, {})
        assert issues == []

    def test_order_outside_line_window_fires(self):
        lines = [{"id": "l1", "name": "Line1", "max_power_kw": 9999,
                   "max_workers": 9999, "max_equipment_slots": 9999, "standby_power_kw": 0}]
        orders = [{"id": "o1", "power_kw": 0, "workers": 0, "equipment_slots": 0}]
        schedule = [{"order_id": "o1", "line_id": "l1", "start": 0, "end": 5}]
        line_ops = [{"line_id": "l1", "start": 2, "end": 10}]
        ctxs = build_energy_cost_aware_scheduler_contexts(schedule, line_ops, orders, lines)
        issues = run_issue_rules("EnergyCostAwareScheduler", ctxs, {})
        assert any(i["id"] == "order_outside_line_window_o1" for i in issues)

    def test_order_duplicate_assignment_fires(self):
        lines = [
            {"id": "l1", "name": "Line1", "max_power_kw": 9999,
             "max_workers": 9999, "max_equipment_slots": 9999, "standby_power_kw": 0},
            {"id": "l2", "name": "Line2", "max_power_kw": 9999,
             "max_workers": 9999, "max_equipment_slots": 9999, "standby_power_kw": 0},
        ]
        orders = [{"id": "o1", "power_kw": 0, "workers": 0, "equipment_slots": 0}]
        schedule = [
            {"order_id": "o1", "line_id": "l1", "start": 0, "end": 5},
            {"order_id": "o1", "line_id": "l2", "start": 0, "end": 5},
        ]
        line_ops = [
            {"line_id": "l1", "start": 0, "end": 5},
            {"line_id": "l2", "start": 0, "end": 5},
        ]
        ctxs = build_energy_cost_aware_scheduler_contexts(schedule, line_ops, orders, lines)
        issues = run_issue_rules("EnergyCostAwareScheduler", ctxs, {})
        assert any(i["id"] == "order_duplicate_assignment_o1" for i in issues)

    def test_registered_in_issue_rules(self):
        assert "EnergyCostAwareScheduler" in ISSUE_RULES
