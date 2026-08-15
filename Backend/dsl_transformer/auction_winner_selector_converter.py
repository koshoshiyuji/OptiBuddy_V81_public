"""
Backend/dsl_transformer/auction_winner_selector_converter.py

Business DSL → Solver Input DSL 変換
組合せオークション落札者決定（AuctionWinnerSelector）

solver_input keys（AuctionWinnerSelectorSolver が参照する全キー）:
  - problem_class: str
  - bids:          List[Dict]  # id, name, price, item_ids
  - items:         List[Dict]  # id, name, description
  - config:        Dict        # time_limit_sec, min_revenue
  - issue_statuses: Dict[str, str]
"""

from __future__ import annotations

from typing import Any, Dict, List


def convert_auction_winner_selector_to_solver(business_dsl: Dict[str, Any]) -> Dict[str, Any]:
    """
    Business DSL → Solver Input DSL

    Business DSL の期待するフィールド:
      bids[]:
        id:       str | int   入札ID
        name:     str         入札者名 / 入札ラベル
        price:    float       入札額
        item_ids: List[str|int]  対象商品ロットIDのリスト（1件以上）
      items[]:
        id:       str | int   商品ロットID
        name:     str         商品ロット名
        description: str      説明（任意）
      config:
        time_limit_sec: float  求解時間上限（秒）、既定30
        min_revenue:    float  最低落札総額の下限（0=制限なし）、既定0
    """
    raw_bids  = business_dsl.get("bids", [])
    raw_items = business_dsl.get("items", [])
    config    = dict(business_dsl.get("config", {}))

    # ── bids の正規化 ────────────────────────────────────────
    bids: List[Dict[str, Any]] = []
    for b in raw_bids:
        bids.append({
            "id":       str(b.get("id", "")),
            "name":     str(b.get("name", b.get("id", ""))),
            "price":    float(b.get("price", 0.0)),
            "item_ids": [str(iid) for iid in b.get("item_ids", [])],
        })

    # ── items の正規化 ───────────────────────────────────────
    items: List[Dict[str, Any]] = []
    for it in raw_items:
        items.append({
            "id":          str(it.get("id", "")),
            "name":        str(it.get("name", it.get("id", ""))),
            "description": str(it.get("description", "")),
        })

    # ── config の正規化 ──────────────────────────────────────
    solver_config: Dict[str, Any] = {
        "time_limit_sec": float(config.get("time_limit_sec", 30.0)),
        "min_revenue":    float(config.get("min_revenue", 0.0)),
    }

    return {
        "problem_class":  "AuctionWinnerSelector",
        "bids":           bids,
        "items":          items,
        "config":         solver_config,
        "issue_statuses": business_dsl.get("issue_statuses", {}),
    }