"""
Backend/dsl_transformer/lot_sizing_scheduler_ui_converter.py

Solver Output DSL → UI DSL 変換（LotSizingScheduler）
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional

from dsl_transformer.table_sections import build_table_section, TableColumn


def convert_lot_sizing_scheduler_to_ui(
    solver_output: dict,
    business_dsl: Optional[dict] = None,
) -> dict:
    """
    Solver Output DSL → UI DSL

    ui_dsl keys:
      domain          : "lot_sizing_scheduler"
      feasible        : bool
      summary         : Dict
      kpi_cards       : List[Dict]
      assignments     : List[Dict]
      table_sections  : List[TableSection]
      alerts          : List[Dict]
      raw_kpi         : Dict
    """
    solutions  = solver_output.get("solutions", [])
    sol        = solutions[0] if solutions else {}
    feasible   = bool(sol.get("feasible", False))
    assignments: List[Dict] = sol.get("assignments", [])
    kpi: Dict   = sol.get("kpi", {})
    issues      = solver_output.get("issues", [])

    # ── KPIカード ─────────────────────────────────────────
    kpi_cards = _build_kpi_cards(kpi, feasible)

    # ── アラート ──────────────────────────────────────────
    alerts = _build_alerts(issues)

    # ── サマリー ──────────────────────────────────────────
    summary = {
        "assigned":   kpi.get("assigned_count", 0),
        "unassigned": kpi.get("unassigned_count", 0),
        "total":      kpi.get("assigned_count", 0) + kpi.get("unassigned_count", 0),
        "objective":  kpi.get("objective_value", 0.0),
    }

    # ── table_sections ────────────────────────────────────
    table_sections = _build_table_sections(assignments, kpi, feasible)

    return {
        "domain":         "lot_sizing_scheduler",
        "feasible":       feasible,
        "summary":        summary,
        "kpi_cards":      kpi_cards,
        "assignments":    assignments,
        "table_sections": table_sections,
        "alerts":         alerts,
        "raw_kpi":        kpi,
    }


def _build_kpi_cards(kpi: Dict, feasible: bool) -> List[Dict]:
    if not feasible:
        return [{
            "label": "ステータス",
            "value": "実行不可能",
            "color": "#ef4444",
            "icon":  "❌",
        }]

    coverage_pct = round(kpi.get("coverage_rate", 0.0) * 100, 1)
    return [
        {
            "label": "割当済み注文",
            "value": str(kpi.get("assigned_count", 0)),
            "unit":  "件",
            "color": "#10b981",
            "icon":  "✅",
        },
        {
            "label": "未割当注文",
            "value": str(kpi.get("unassigned_count", 0)),
            "unit":  "件",
            "color": "#ef4444" if kpi.get("unassigned_count", 0) > 0 else "#888",
            "icon":  "⚠️" if kpi.get("unassigned_count", 0) > 0 else "—",
        },
        {
            "label": "割当率",
            "value": f"{coverage_pct}",
            "unit":  "%",
            "color": "#3b82f6",
            "icon":  "📊",
        },
        {
            "label": "総保管コスト",
            "value": str(kpi.get("total_holding_cost", 0.0)),
            "unit":  "",
            "color": "#f97316",
            "icon":  "💰",
        },
        {
            "label": "目的関数値",
            "value": str(kpi.get("objective_value", 0.0)),
            "unit":  "",
            "color": "#8b5cf6",
            "icon":  "🎯",
        },
    ]


def _build_alerts(issues: List[Dict]) -> List[Dict]:
    alerts = []
    for issue in issues:
        if issue.get("severity") in ("CRITICAL", "WARNING"):
            alerts.append({
                "id":       issue.get("id", ""),
                "severity": issue.get("severity", "WARNING"),
                "title":    issue.get("title", ""),
                "message":  issue.get("message", ""),
            })
    return alerts


def _build_table_sections(
    assignments: List[Dict],
    kpi: Dict,
    feasible: bool,
) -> List[Dict]:
    if not feasible or not assignments:
        return []

    # ── セクション1: 注文割当一覧 ─────────────────────────
    assignment_rows = []
    for a in sorted(assignments, key=lambda x: (x["assigned_period"], x["product_id"])):
        wait = a["due_period"] - a["assigned_period"]
        severity = None
        if wait == 0:
            status_label = "納期当日"
        elif wait > 0:
            status_label = f"{wait}期間前倒し"
        else:
            status_label = "納期超過"
            severity = "CRITICAL"

        assignment_rows.append({
            "order_id":        a["order_id"],
            "product_id":      a["product_id"],
            "quantity":        a["quantity"],
            "assigned_period": a["assigned_period"],
            "due_period":      a["due_period"],
            "holding_cost":    a["holding_cost"],
            "status":          status_label,
            "_severity_raw":   severity,
        })

    assignment_section = build_table_section(
        section_id="lot_sizing_assignments",
        title="注文割当一覧",
        columns=[
            TableColumn(key="order_id",        label="注文ID"),
            TableColumn(key="product_id",       label="製品"),
            TableColumn(key="quantity",         label="数量",     format="number", align="right"),
            TableColumn(key="assigned_period",  label="割当期間", format="number", align="right"),
            TableColumn(key="due_period",       label="納期",     format="number", align="right"),
            TableColumn(key="holding_cost",     label="保管コスト", format="number", align="right"),
            TableColumn(key="status",           label="ステータス", format="badge"),
        ],
        rows=assignment_rows,
        row_id_key="order_id",
        severity_key="_severity_raw",
    )

    # ── セクション2: 期間別生産サマリー ───────────────────
    period_map: Dict[int, Dict] = {}
    for a in assignments:
        p = a["assigned_period"]
        if p not in period_map:
            period_map[p] = {
                "period":          p,
                "order_count":     0,
                "total_quantity":  0,
                "total_holding":   0.0,
                "products":        set(),
            }
        period_map[p]["order_count"]    += 1
        period_map[p]["total_quantity"] += a["quantity"]
        period_map[p]["total_holding"]  += a["holding_cost"]
        period_map[p]["products"].add(a["product_id"])

    period_rows = []
    for p in sorted(period_map.keys()):
        d = period_map[p]
        period_rows.append({
            "period":         d["period"],
            "order_count":    d["order_count"],
            "total_quantity": d["total_quantity"],
            "total_holding":  round(d["total_holding"], 2),
            "products":       ", ".join(sorted(d["products"])),
        })

    period_section = build_table_section(
        section_id="lot_sizing_periods",
        title="期間別生産サマリー",
        columns=[
            TableColumn(key="period",         label="期間",       format="number", align="right"),
            TableColumn(key="order_count",    label="割当注文数", format="number", align="right"),
            TableColumn(key="total_quantity", label="総生産量",   format="number", align="right"),
            TableColumn(key="total_holding",  label="合計保管コスト", format="number", align="right"),
            TableColumn(key="products",       label="生産製品"),
        ],
        rows=period_rows,
        row_id_key="period",
    )

    return [assignment_section, period_section]