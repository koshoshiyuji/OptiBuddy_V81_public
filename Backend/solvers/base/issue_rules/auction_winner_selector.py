
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable, Dict, List, Optional, Tuple

from solvers.base.solution_checker import solver_bug_issue, sweep_peak_usage
from i18n.common_messages import t as _common_t
from i18n.nurse_shift_weekly_cap_messages import t as _nurse_shift_t

from ._common import IssueRule, _rule




# ---------------------------------------------------------------------------
# _AUCTION_WINNER_SELECTOR_RULES（2026-08-31 新規、solution checker展開バッチ1）
#
# 制約: (a) 各商品ロットは高々1件の落札入札にしか含まれない（set packing）、
#       (b) config.min_revenue > 0 の場合、落札総額はその下限を満たす。
# どちらもsolver.py側で明示的にmdl.add_constraintしているハード制約であり、
# 解チェッカーはDSL宣言値（bids/items/min_revenue）と返ってきたwinnersのみから
# 独立に再計算する。
# ---------------------------------------------------------------------------

_AUCTION_WINNER_SELECTOR_RULES: List[IssueRule] = [

    _rule(
        "item_won_by_multiple_bids",
        condition=lambda ctx: len(ctx["winning_bid_ids"]) > 1,
        build=lambda ctx: solver_bug_issue(
            f"item_won_by_multiple_bids_{ctx['item_id']}",
            f"商品ロット重複落札（解チェッカー）: {ctx['item_id']}",
            f"商品ロット「{ctx['item_id']}」が複数の入札"
            f"（{', '.join(ctx['winning_bid_ids'])}）に同時に含まれています。"
            "各商品ロットは高々1件にしか落札されない制約と矛盾しています。",
        ),
    ),

    _rule(
        "min_revenue_violated",
        condition=lambda ctx: ctx["min_revenue"] > 0 and ctx["total_revenue"] < ctx["min_revenue"] - 1e-6,
        build=lambda ctx: solver_bug_issue(
            "min_revenue_violated",
            "最低落札総額割れ（解チェッカー）",
            f"落札総額{ctx['total_revenue']:.1f}が最低落札総額{ctx['min_revenue']:.1f}を"
            "下回っています。min_revenue制約と矛盾しています。",
        ),
    ),
]



# ---------------------------------------------------------------------------
# AuctionWinnerSelector context ビルダー（2026-08-31 新規）
# ---------------------------------------------------------------------------

def build_auction_winner_selector_contexts(
    winners: List[Dict],
    min_revenue: float,
    total_revenue: float,
) -> List[Dict[str, Any]]:
    """
    AuctionWinnerSelector 用の商品ロット重複落札チェック（O(winners総item数)）と
    最低落札総額チェック（O(1)）のcontextを生成する。solver内部のx変数や
    mdlオブジェクトは一切参照せず、返ってきたwinnersのみから集計し直す。
    """
    item_winner_map: Dict[str, List[str]] = {}
    for w in winners:
        for item_id in w.get("item_ids", []):
            item_winner_map.setdefault(str(item_id), []).append(w["bid_id"])

    ctxs: List[Dict] = [
        {"_rule_id": "item_won_by_multiple_bids", "item_id": item_id, "winning_bid_ids": bid_ids}
        for item_id, bid_ids in item_winner_map.items()
    ]
    ctxs.append({
        "_rule_id":      "min_revenue_violated",
        "min_revenue":   min_revenue,
        "total_revenue": total_revenue,
    })
    return ctxs
