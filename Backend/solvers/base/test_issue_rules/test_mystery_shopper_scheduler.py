
import pytest
from solvers.base.issue_rules import (
    ISSUE_RULES,
    build_mystery_shopper_scheduler_contexts,
    run_issue_rules,
)




# ---------------------------------------------------------------------------
# テスト: MysteryShopperScheduler 解チェッカー（2026-08-31新規、バッチ2）
# ---------------------------------------------------------------------------

class TestMysteryShopperSchedulerChecker:
    def test_visit_double_assigned_fires(self):
        assignments = [
            {"visit_id": "v1", "shopper_id": "s1", "day": "2026-09-01"},
            {"visit_id": "v1", "shopper_id": "s2", "day": "2026-09-02"},
        ]
        ctxs = build_mystery_shopper_scheduler_contexts(assignments)
        issues = run_issue_rules("MysteryShopperScheduler", ctxs, {})
        assert any(i["id"] == "visit_double_assigned_v1" for i in issues)
        assert all(i["category"] == "SOLVER" for i in issues)

    def test_shopper_day_double_booked_fires(self):
        assignments = [
            {"visit_id": "v1", "shopper_id": "s1", "day": "2026-09-01"},
            {"visit_id": "v2", "shopper_id": "s1", "day": "2026-09-01"},
        ]
        ctxs = build_mystery_shopper_scheduler_contexts(assignments)
        issues = run_issue_rules("MysteryShopperScheduler", ctxs, {})
        assert any(i["id"] == "shopper_day_double_booked_s1_2026-09-01" for i in issues)

    def test_no_violation_no_fire(self):
        assignments = [
            {"visit_id": "v1", "shopper_id": "s1", "day": "2026-09-01"},
            {"visit_id": "v2", "shopper_id": "s2", "day": "2026-09-01"},
        ]
        ctxs = build_mystery_shopper_scheduler_contexts(assignments)
        issues = run_issue_rules("MysteryShopperScheduler", ctxs, {})
        assert issues == []

    def test_registered_in_issue_rules(self):
        assert "MysteryShopperScheduler" in ISSUE_RULES
