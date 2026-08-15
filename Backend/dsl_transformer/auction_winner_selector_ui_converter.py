"""
Backend/dsl_transformer/auction_winner_selector_ui_converter.py

Solver Output DSL → UI DSL 変換
組合せオークション落札者決定（AuctionWinnerSelector）
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional


def convert_auction_winner_selector_to_ui(
    solver_output: Dict[str, Any],
    business_dsl: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """
    Solver Output DSL → UI DSL

    ui_dsl.domain は必ず "auction_winner_selector" （snake_case）をセットする。
    """
    from dsl_transformer.table_sections import build_table_section, TableColumn

    solutions = solver_output.get("solutions", [])
    feasible  = solver_output.get("feasible", False)
    issues    = solver_output.get("issues", [])

    if not feasible or not solutions:
        return {
            "domain":         "auction_winner_selector",
            "feasible":       False,
            "summary":        {"total_revenue": 0, "winner_count": 0},
            "kpi_cards":      [],
            "winners":        [],
            "table_sections": [],
            "alerts":         _build_alerts(issues),
            "raw_kpi":        {},
        }

    sol    = solutions[0]
    kpi    = sol.get("kpi", {})
    winners: List[Dict[str, Any]] = sol.get("winners", [])

    total_revenue = float(kpi.get("total_revenue", 0))
    winner_count  = int(kpi.get("winner_count", 0))
    solve_time    = float(kpi.get("solve_time_sec", 0))

    # ── KPIカード ────────────────────────────────────────────
    kpi_cards = [
        {
            "label": "落札総額",
            "value": f"¥{total_revenue:,.0f}",
            "color": "#10b981",
        },
        {
            "label": "落札件数",
            "value": str(winner_count),
            "color": "#3b82f6",
        },
        {
            "label": "求解時間",
            "value": f"{solve_time:.1f}秒",
            "color": "#888",
        },
    ]

    # ── 落札一覧テーブル ─────────────────────────────────────
    winner_rows = []
    for w in winners:
        item_names = ", ".join(it.get("name", str(it.get("id", ""))) for it in w.get("items", []))
        winner_rows.append({
            "bid_id":     w["bid_id"],
            "bid_name":   w["bid_name"],
            "price":      w["price"],
            "item_count": len(w.get("item_ids", [])),
            "item_names": item_names,
        })
    winner_rows.sort(key=lambda r: -r["price"])

    winner_table = build_table_section(
        section_id="winner_bids",
        title="落札入札一覧",
        columns=[
            TableColumn(key="bid_name",   label="入札者 / 入札ラベル"),
            TableColumn(key="price",      label="落札額",       format="currency", align="right"),
            TableColumn(key="item_count", label="ロット数",     format="number",   align="right"),
            TableColumn(key="item_names", label="落札商品ロット"),
        ],
        rows=winner_rows,
        row_id_key="bid_id",
        allow_download=True,
    )

    # ── 商品ロット別落札状況テーブル ─────────────────────────
    # business_dsl がある場合は全商品ロットを列挙する
    all_items: List[Dict] = []
    if business_dsl:
        all_items = [{"id": str(it.get("id", "")), "name": str(it.get("name", ""))}
                     for it in business_dsl.get("items", [])]
    else:
        # solver_output の winners から再構築
        seen: Dict[str, str] = {}
        for w in winners:
            for it in w.get("items", []):
                iid = str(it.get("id", ""))
                if iid not in seen:
                    seen[iid] = str(it.get("name", iid))
        all_items = [{"id": k, "name": v} for k, v in seen.items()]

    # 落札入札との対応を作る
    item_to_winner: Dict[str, Dict] = {}
    for w in winners:
        for iid in w.get("item_ids", []):
            item_to_winner[str(iid)] = w

    item_rows = []
    for it in all_items:
        iid = it["id"]
        w   = item_to_winner.get(iid)
        item_rows.append({
            "item_id":      iid,
            "item_name":    it["name"],
            "status":       "SOLD" if w else "UNSOLD",
            "winning_bid":  w["bid_name"] if w else "—",
            "winning_price": w["price"] if w else 0.0,
        })

    item_table = build_table_section(
        section_id="item_status",
        title="商品ロット落札状況",
        columns=[
            TableColumn(key="item_name",    label="商品ロット名"),
            TableColumn(key="status",       label="状態",        format="badge"),
            TableColumn(key="winning_bid",  label="落札入札"),
            TableColumn(key="winning_price", label="落札額",     format="currency", align="right"),
        ],
        rows=item_rows,
        row_id_key="item_id",
        allow_download=True,
    )

    return {
        "domain":         "auction_winner_selector",
        "feasible":       True,
        "summary": {
            "total_revenue": total_revenue,
            "winner_count":  winner_count,
        },
        "kpi_cards":      kpi_cards,
        "winners":        winners,
        "table_sections": [winner_table, item_table],
        "alerts":         _build_alerts(issues),
        "raw_kpi":        kpi,
    }


def _build_alerts(issues: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """INFO以上のissueをアラートとして変換する。"""
    return [
        {
            "severity": iss.get("severity", "INFO"),
            "title":    iss.get("title", ""),
            "message":  iss.get("message", ""),
        }
        for iss in issues
    ]