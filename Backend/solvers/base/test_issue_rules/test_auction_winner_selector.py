
import pytest
from solvers.base.issue_rules import (
    ISSUE_RULES,
    build_auction_winner_selector_contexts,
    run_issue_rules,
)




# ---------------------------------------------------------------------------
# テスト: AuctionWinnerSelector 解チェッカー（2026-08-31新規、バッチ1）
# ---------------------------------------------------------------------------

class TestAuctionWinnerSelectorChecker:
    def test_item_won_by_multiple_bids_fires(self):
        winners = [{"bid_id": "b1", "item_ids": ["i1"]}, {"bid_id": "b2", "item_ids": ["i1"]}]
        ctxs = build_auction_winner_selector_contexts(winners, min_revenue=0, total_revenue=100)
        issues = run_issue_rules("AuctionWinnerSelector", ctxs, {})
        assert len(issues) == 1
        assert issues[0]["id"] == "item_won_by_multiple_bids_i1"
        assert issues[0]["category"] == "SOLVER"

    def test_unique_items_no_fire(self):
        winners = [{"bid_id": "b1", "item_ids": ["i1"]}, {"bid_id": "b2", "item_ids": ["i2"]}]
        ctxs = build_auction_winner_selector_contexts(winners, min_revenue=0, total_revenue=100)
        issues = run_issue_rules("AuctionWinnerSelector", ctxs, {})
        assert issues == []

    def test_min_revenue_violated_fires(self):
        ctxs = build_auction_winner_selector_contexts([], min_revenue=1000, total_revenue=500)
        issues = run_issue_rules("AuctionWinnerSelector", ctxs, {})
        assert len(issues) == 1
        assert issues[0]["id"] == "min_revenue_violated"
        assert issues[0]["category"] == "SOLVER"

    def test_min_revenue_zero_no_fire(self):
        # min_revenue未設定（0）の場合はチェック対象外
        ctxs = build_auction_winner_selector_contexts([], min_revenue=0, total_revenue=0)
        issues = run_issue_rules("AuctionWinnerSelector", ctxs, {})
        assert issues == []

    def test_registered_in_issue_rules(self):
        assert "AuctionWinnerSelector" in ISSUE_RULES
