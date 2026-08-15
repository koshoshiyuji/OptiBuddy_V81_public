"""
Backend/dsl_transformer/tank_allocation_planner_ui_converter.py

TankAllocationPlanner: Solver Output DSL → UI DSL
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional


def convert_tank_allocation_planner_to_ui(
    solver_output: Dict[str, Any],
    business_dsl: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """
    Solver Output DSL → UI DSL

    ui_dsl keys:
      domain: "tank_allocation_planner"
      feasible: bool
      summary: dict
      kpi_cards: list
      tank_assignments: list
      unassigned_lots: list
      customer_dispersion: list
      table_sections: list
      alerts: list
      raw_kpi: dict
    """
    from dsl_transformer.table_sections import build_table_section, TableColumn

    solutions = solver_output.get("solutions", [])
    sol = solutions[0] if solutions else {}
    feasible = sol.get("feasible", False)
    kpi = sol.get("kpi", {})
    metrics = sol.get("metrics", {})
    tank_assignments = sol.get("tank_assignments", [])
    unassigned_lots = sol.get("unassigned_lots", [])
    customer_dispersion = sol.get("customer_dispersion", [])

    # --- KPIカード ---
    used = kpi.get("used_tanks", 0)
    total = kpi.get("total_tanks", 0)
    usage_rate_pct = round(kpi.get("usage_rate", 0) * 100, 1)
    assigned = kpi.get("assigned_lots", 0)
    unassigned = kpi.get("unassigned_lots", 0)
    total_vol = kpi.get("total_volume", 0)

    kpi_cards = [
        {
            "label": "使用タンク台数",
            "value": f"{used} / {total} 台",
            "sub": f"使用率 {usage_rate_pct}%",
            "color": "blue",
        },
        {
            "label": "割当済ロット",
            "value": f"{assigned} 件",
            "sub": f"未割当 {unassigned} 件",
            "color": "green" if unassigned == 0 else "orange",
        },
        {
            "label": "総積載量",
            "value": f"{total_vol:.1f} kL",
            "sub": "",
            "color": "teal",
        },
        {
            "label": "最適性",
            "value": "証明済" if metrics.get("is_optimal") else "暫定解",
            "sub": f"計算時間 {metrics.get('solve_time', 0):.1f}秒",
            "color": "purple" if metrics.get("is_optimal") else "yellow",
        },
    ]

    # --- アラート ---
    alerts = []
    if unassigned > 0:
        alerts.append({
            "severity": "WARNING",
            "message": f"{unassigned} 件のロットが割り当てられませんでした。容量や相性制約を確認してください。",
        })
    if not metrics.get("is_optimal"):
        alerts.append({
            "severity": "INFO",
            "message": "制限時間内で見つかった解です。最適性は保証されていません。",
        })

    # --- table_sections ---
    # 1. タンク別積載一覧
    tank_rows = []
    for ta in tank_assignments:
        categories_str = "、".join(ta.get("categories", []))
        lot_names = "、".join(lot["lot_name"] for lot in ta.get("lots", []))
        cap = ta.get("capacity", 0)
        vol = ta.get("total_volume", 0)
        tank_rows.append({
            "tank_id": ta["tank_id"],
            "tank_name": ta["tank_name"],
            "lot_count": len(ta.get("lots", [])),
            "total_volume": round(vol, 2),
            "capacity": cap,
            "usage_rate": round(ta.get("usage_rate", 0) * 100, 1),
            "categories": categories_str,
            "lot_names": lot_names,
        })

    tank_section = build_table_section(
        section_id="tank_assignments",
        title="タンク別積載一覧",
        columns=[
            TableColumn(key="tank_name", label="タンク名"),
            TableColumn(key="lot_count", label="積載ロット数", format="number", align="right"),
            TableColumn(key="total_volume", label="積載量(kL)", format="number", align="right"),
            TableColumn(key="capacity", label="容量(kL)", format="number", align="right"),
            TableColumn(key="usage_rate", label="使用率(%)", format="number", align="right"),
            TableColumn(key="categories", label="薬品分類"),
            TableColumn(key="lot_names", label="積載ロット"),
        ],
        rows=tank_rows,
        row_id_key="tank_id",
    )

    # 2. 未割当ロット一覧
    unassigned_rows = [
        {
            "lot_id": ul["lot_id"],
            "lot_name": ul["lot_name"],
            "volume": round(float(ul.get("volume", 0)), 2),
            "category": ul.get("category", ""),
            "customer": ul.get("customer", ""),
            "reason": ul.get("reason", ""),
            "severity": "WARNING",
        }
        for ul in unassigned_lots
    ]

    unassigned_section = build_table_section(
        section_id="unassigned_lots",
        title="未割当ロット一覧",
        columns=[
            TableColumn(key="lot_name", label="ロット名"),
            TableColumn(key="volume", label="数量(kL)", format="number", align="right"),
            TableColumn(key="category", label="薬品分類"),
            TableColumn(key="customer", label="得意先"),
            TableColumn(key="reason", label="理由"),
        ],
        rows=unassigned_rows,
        row_id_key="lot_id",
        severity_key="severity",
    )

    # 3. 得意先別タンク分散一覧
    dispersion_rows = [
        {
            "customer": cd["customer"],
            "tank_count": cd["tank_count"],
            "tank_ids": "、".join(cd.get("tank_ids", [])),
            "severity": "WARNING" if cd["tank_count"] > 1 else None,
        }
        for cd in customer_dispersion
    ]

    dispersion_section = build_table_section(
        section_id="customer_dispersion",
        title="得意先別タンク分散",
        columns=[
            TableColumn(key="customer", label="得意先"),
            TableColumn(key="tank_count", label="使用タンク台数", format="number", align="right"),
            TableColumn(key="tank_ids", label="タンクID"),
        ],
        rows=dispersion_rows,
        row_id_key="customer",
        severity_key="severity",
    )

    summary = {
        "used_tanks": used,
        "total_tanks": total,
        "assigned_lots": assigned,
        "unassigned_lots": unassigned,
    }

    return {
        "domain": "tank_allocation_planner",
        "feasible": feasible,
        "summary": summary,
        "kpi_cards": kpi_cards,
        "tank_assignments": tank_assignments,
        "unassigned_lots": unassigned_lots,
        "customer_dispersion": customer_dispersion,
        "table_sections": [tank_section, unassigned_section, dispersion_section],
        "alerts": alerts,
        "raw_kpi": kpi,
    }