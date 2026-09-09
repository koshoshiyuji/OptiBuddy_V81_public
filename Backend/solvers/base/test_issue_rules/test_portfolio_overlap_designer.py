
import pytest
from solvers.base.issue_rules import (
    ISSUE_RULES,
    build_portfolio_overlap_designer_contexts,
    run_issue_rules,
)




# ---------------------------------------------------------------------------
# テスト: PortfolioOverlapDesigner 解チェッカー（2026-08-31新規、バッチ2）
# ---------------------------------------------------------------------------

class TestPortfolioOverlapDesignerChecker:
    def test_selected_count_mismatch_fires(self):
        funds = [{"id": "f1", "name": "Fund1", "required_count": 3}]
        assignments = {"f1": ["s1", "s2"]}
        overlap_matrix = []
        ctxs = build_portfolio_overlap_designer_contexts(funds, assignments, overlap_matrix, worst_overlap=0)
        issues = run_issue_rules("PortfolioOverlapDesigner", ctxs, {})
        assert any(i["id"] == "selected_count_mismatch_f1" for i in issues)
        assert all(i["category"] == "SOLVER" for i in issues)

    def test_overlap_kpi_mismatch_fires(self):
        funds = [{"id": "f1", "name": "Fund1", "required_count": 2}]
        assignments = {"f1": ["s1", "s2"]}
        overlap_matrix = [{"fund_id_a": "f1", "fund_id_b": "f2", "overlap_count": 2}]
        ctxs = build_portfolio_overlap_designer_contexts(funds, assignments, overlap_matrix, worst_overlap=0)
        issues = run_issue_rules("PortfolioOverlapDesigner", ctxs, {})
        assert any(i["id"] == "overlap_matrix_worst_overlap_mismatch" for i in issues)

    def test_no_violation_no_fire(self):
        funds = [{"id": "f1", "name": "Fund1", "required_count": 2}]
        assignments = {"f1": ["s1", "s2"]}
        overlap_matrix = [{"fund_id_a": "f1", "fund_id_b": "f2", "overlap_count": 1}]
        ctxs = build_portfolio_overlap_designer_contexts(funds, assignments, overlap_matrix, worst_overlap=1)
        issues = run_issue_rules("PortfolioOverlapDesigner", ctxs, {})
        assert issues == []

    def test_registered_in_issue_rules(self):
        assert "PortfolioOverlapDesigner" in ISSUE_RULES
