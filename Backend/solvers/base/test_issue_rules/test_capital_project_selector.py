
import pytest
from solvers.base.issue_rules import (
    ISSUE_RULES,
    build_capital_project_selector_contexts,
    run_issue_rules,
)




# ---------------------------------------------------------------------------
# テスト: CapitalProjectSelector 解チェッカー（2026-08-31新規、バッチ1）
# ---------------------------------------------------------------------------

class TestCapitalProjectSelectorChecker:
    def test_budget_exceeded_fires(self):
        ctxs = build_capital_project_selector_contexts(
            total_weight=110, budget=100, selected_count=3, max_projects=None,
        )
        issues = run_issue_rules("CapitalProjectSelector", ctxs, {})
        assert len(issues) == 1
        assert issues[0]["id"] == "budget_exceeded"
        assert issues[0]["category"] == "SOLVER"

    def test_within_budget_no_fire(self):
        ctxs = build_capital_project_selector_contexts(
            total_weight=90, budget=100, selected_count=3, max_projects=None,
        )
        issues = run_issue_rules("CapitalProjectSelector", ctxs, {})
        assert issues == []

    def test_max_projects_exceeded_fires(self):
        ctxs = build_capital_project_selector_contexts(
            total_weight=50, budget=100, selected_count=6, max_projects=5,
        )
        issues = run_issue_rules("CapitalProjectSelector", ctxs, {})
        assert len(issues) == 1
        assert issues[0]["id"] == "max_projects_exceeded"

    def test_max_projects_none_no_fire(self):
        # max_projects未設定時は選定件数チェック対象外
        ctxs = build_capital_project_selector_contexts(
            total_weight=50, budget=100, selected_count=999, max_projects=None,
        )
        issues = run_issue_rules("CapitalProjectSelector", ctxs, {})
        assert issues == []

    def test_registered_in_issue_rules(self):
        assert "CapitalProjectSelector" in ISSUE_RULES
