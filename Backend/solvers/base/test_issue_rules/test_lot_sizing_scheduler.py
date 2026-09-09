
import pytest
from solvers.base.issue_rules import (
    ISSUE_RULES,
    build_lot_sizing_scheduler_contexts,
    run_issue_rules,
)




# ---------------------------------------------------------------------------
# テスト: LotSizingScheduler 解チェッカー（2026-09-01新規、バッチ3）
# ---------------------------------------------------------------------------

class TestLotSizingSchedulerChecker:
    def test_period_double_assigned_fires(self):
        assignments = [
            {"order_id": "o1", "assigned_period": 2},
            {"order_id": "o2", "assigned_period": 2},
        ]
        ctxs = build_lot_sizing_scheduler_contexts(assignments)
        issues = run_issue_rules("LotSizingScheduler", ctxs, {})
        assert any(i["id"] == "period_double_assigned_2" for i in issues)
        assert all(i["category"] == "SOLVER" for i in issues)

    def test_no_violation_no_fire(self):
        assignments = [
            {"order_id": "o1", "assigned_period": 1},
            {"order_id": "o2", "assigned_period": 2},
        ]
        ctxs = build_lot_sizing_scheduler_contexts(assignments)
        issues = run_issue_rules("LotSizingScheduler", ctxs, {})
        assert issues == []

    def test_registered_in_issue_rules(self):
        assert "LotSizingScheduler" in ISSUE_RULES
