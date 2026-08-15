"""
Backend/dsl_transformer/transport_cost_minimizer_ui_converter.py

Solver Output DSL → UI DSL（TransportCostMinimizer）
"""

from __future__ import annotations
from typing import Any, Dict, List, Optional


def convert_transport_cost_minimizer_to_ui(
    solver_output: dict,
    business_dsl: Optional[dict] = None,
) -> dict:
    """
    Solver Output DSL → UI DSL

    ui_dsl キー:
      domain, feasible, summary, kpi_cards, flows,
      table_sections, alerts, raw_kpi
    """
    from dsl_transformer.table_sections import build_table_section, TableColumn

    feasible:   bool       = solver_output.get("feasible", False)
    flows:      List[Dict] = solver_output.get("flows", [])
    total_cost: float      = float(solver_output.get("total_cost", 0.0))
    issues:     List[Dict] = solver_output.get("issues", [])

    # ── KPI カード ──────────────────────────────────────────────────────────
    active_flows     = [fl for fl in flows if fl["amount"] > 1e-6]
    factory_ids_used = {fl["factory_id"] for fl in active_flows}
    store_ids_served = {fl["store_id"]   for fl in active_flows}

    kpi_cards = []
    if feasible:
        kpi_cards = [
            {"label": "総輸送コスト",   "value": f"¥{total_cost:,.0f}",         "color": "#f97316"},
            {"label": "使用ルート数",   "value": str(len(active_flows)),          "color": "#3b82f6"},
            {"label": "稼働工場数",     "value": str(len(factory_ids_used)),      "color": "#10b981"},
            {"label": "配送完了店舗数", "value": str(len(store_ids_served)),      "color": "#8b5cf6"},
        ]

    # ── アラート ─────────────────────────────────────────────────────────────
    alerts = [
        {
            "id":       iss.get("id", ""),
            "severity": iss.get("severity", "INFO"),
            "title":    iss.get("title", ""),
            "message":  iss.get("message", ""),
        }
        for iss in issues
        if iss.get("severity") in ("CRITICAL", "WARNING")
    ]

    # ── table_sections ───────────────────────────────────────────────────────
    # 工場→店舗 輸送明細テーブル
    detail_rows: List[Dict] = []
    for fl in sorted(flows, key=lambda x: (x["factory_id"], x["store_id"])):
        severity = None
        if fl["amount"] < 1e-6:
            severity = "INFO"
        detail_rows.append({
            "id":           f"{fl['factory_id']}_{fl['store_id']}",
            "factory_name": fl.get("factory_name", fl["factory_id"]),
            "store_name":   fl.get("store_name",   fl["store_id"]),
            "amount":       fl["amount"],
            "unit_cost":    fl["unit_cost"],
            "line_cost":    fl["line_cost"],
            "severity":     severity,
        })

    flow_section = build_table_section(
        section_id="transport_flow_detail",
        title="輸送ルート明細",
        columns=[
            TableColumn(key="factory_name", label="工場"),
            TableColumn(key="store_name",   label="店舗"),
            TableColumn(key="amount",       label="輸送量",       format="number",   align="right"),
            TableColumn(key="unit_cost",    label="単位コスト",   format="number",   align="right"),
            TableColumn(key="line_cost",    label="輸送コスト",   format="currency", align="right"),
        ],
        rows=detail_rows,
        row_id_key="id",
        severity_key="severity",
        allow_download=True,
    )

    # 工場別集計テーブル
    factory_summary: Dict[str, Dict] = {}
    for fl in flows:
        fid = fl["factory_id"]
        if fid not in factory_summary:
            factory_summary[fid] = {
                "id":           fid,
                "factory_name": fl.get("factory_name", fid),
                "total_amount": 0.0,
                "total_cost":   0.0,
            }
        factory_summary[fid]["total_amount"] += fl["amount"]
        factory_summary[fid]["total_cost"]   += fl["line_cost"]

    factory_rows = [
        {
            **v,
            "total_amount": round(v["total_amount"], 4),
            "total_cost":   round(v["total_cost"],   2),
        }
        for v in sorted(factory_summary.values(), key=lambda x: x["id"])
    ]
    factory_section = build_table_section(
        section_id="transport_factory_summary",
        title="工場別出荷集計",
        columns=[
            TableColumn(key="factory_name", label="工場"),
            TableColumn(key="total_amount", label="総出荷量",     format="number",   align="right"),
            TableColumn(key="total_cost",   label="総輸送コスト", format="currency", align="right"),
        ],
        rows=factory_rows,
        row_id_key="id",
        allow_download=True,
    )

    # 店舗別集計テーブル
    store_summary: Dict[str, Dict] = {}
    for fl in flows:
        sid = fl["store_id"]
        if sid not in store_summary:
            store_summary[sid] = {
                "id":         sid,
                "store_name": fl.get("store_name", sid),
                "total_received": 0.0,
                "total_cost":     0.0,
            }
        store_summary[sid]["total_received"] += fl["amount"]
        store_summary[sid]["total_cost"]     += fl["line_cost"]

    store_rows = [
        {
            **v,
            "total_received": round(v["total_received"], 4),
            "total_cost":     round(v["total_cost"],     2),
        }
        for v in sorted(store_summary.values(), key=lambda x: x["id"])
    ]
    store_section = build_table_section(
        section_id="transport_store_summary",
        title="店舗別受取集計",
        columns=[
            TableColumn(key="store_name",     label="店舗"),
            TableColumn(key="total_received", label="受取量",       format="number",   align="right"),
            TableColumn(key="total_cost",     label="輸送コスト計", format="currency", align="right"),
        ],
        rows=store_rows,
        row_id_key="id",
        allow_download=True,
    )

    table_sections = [flow_section, factory_section, store_section]

    # ── summary ──────────────────────────────────────────────────────────────
    summary: Dict[str, Any] = {
        "feasible":          feasible,
        "total_cost":        total_cost,
        "active_route_count": len(active_flows),
        "factory_count":     len(factory_ids_used),
        "store_count":       len(store_ids_served),
    }

    return {
        "domain":         "transport_cost_minimizer",
        "feasible":       feasible,
        "summary":        summary,
        "kpi_cards":      kpi_cards,
        "flows":          flows,
        "table_sections": table_sections,
        "alerts":         alerts,
        "raw_kpi": {
            "total_cost":        total_cost,
            "active_route_count": len(active_flows),
        },
    }