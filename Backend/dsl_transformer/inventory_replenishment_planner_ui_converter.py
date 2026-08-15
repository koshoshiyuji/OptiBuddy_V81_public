"""
Backend/dsl_transformer/inventory_replenishment_planner_ui_converter.py

Solver Output DSL → UI DSL 変換
InventoryReplenishmentPlanner（多段階ロットサイジング）
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional


def convert_inventory_replenishment_planner_to_ui(
    solver_output: dict,
    business_dsl: Optional[dict] = None,
) -> dict:
    """
    Solver Output DSL → UI DSL

    ui_dsl.domain は必ず "inventory_replenishment_planner" をセットする。
    """
    from dsl_transformer.table_sections import build_table_section, TableColumn

    feasible  = solver_output.get("feasible", False)
    solutions = solver_output.get("solutions", [])
    issues    = solver_output.get("issues", [])

    if not feasible or not solutions:
        return {
            "domain":        "inventory_replenishment_planner",
            "feasible":      False,
            "summary":       {},
            "kpi_cards":     [],
            "shipments":     [],
            "table_sections": [],
            "alerts":        _build_alerts(issues),
            "raw_kpi":       {},
        }

    sol = solutions[0]
    metrics   = sol.get("metrics", {})
    shipments = sol.get("shipments", [])

    # --- KPIカード ---
    kpi_cards = [
        {
            "label": "総費用",
            "value": f"¥{int(metrics.get('total_cost', 0)):,}",
            "color": "#f97316",
        },
        {
            "label": "発送固定費",
            "value": f"¥{int(metrics.get('total_shipping_cost', 0)):,}",
            "color": "#3b82f6",
        },
        {
            "label": "保管費",
            "value": f"¥{int(metrics.get('total_holding_cost', 0)):,}",
            "color": "#8b5cf6",
        },
        {
            "label": "未充足量",
            "value": f"{int(metrics.get('total_shortage', 0)):,} 個",
            "color": "#ef4444" if metrics.get("total_shortage", 0) > 0 else "#10b981",
        },
    ]

    # --- 発送計画テーブル ---
    shipment_rows = []
    for s in shipments:
        severity = None
        if s.get("shortage", 0) > 0.5:
            severity = "WARNING"
        shipment_rows.append({
            "id":            f"{s['center_id']}_{s['period_id']}",
            "center_id":     s["center_id"],
            "period_label":  f"第{s['period_index']+1}週",
            "demand":        int(round(s.get("demand", 0))),
            "quantity":      int(round(s.get("quantity", 0))),
            "inventory_end": int(round(s.get("inventory_end", 0))),
            "shortage":      int(round(s.get("shortage", 0))),
            "fixed_cost":    int(round(s.get("fixed_cost", 0))),
            "holding_cost":  int(round(s.get("holding_cost", 0))),
            "severity":      severity,
        })

    shipment_section = build_table_section(
        section_id="shipment_plan",
        title="発送計画詳細（拠点×週）",
        columns=[
            TableColumn(key="center_id",     label="配送センター"),
            TableColumn(key="period_label",   label="計画週"),
            TableColumn(key="demand",         label="需要(個)",         format="number", align="right"),
            TableColumn(key="quantity",       label="発送量(個)",       format="number", align="right"),
            TableColumn(key="inventory_end",  label="期末在庫(個)",     format="number", align="right"),
            TableColumn(key="shortage",       label="未充足(個)",       format="number", align="right"),
            TableColumn(key="fixed_cost",     label="発送固定費(円)",   format="currency", align="right"),
            TableColumn(key="holding_cost",   label="保管費(円)",       format="currency", align="right"),
        ],
        rows=shipment_rows,
        row_id_key="id",
        severity_key="severity",
        allow_download=True,
    )

    # --- センター別集計テーブル ---
    center_agg: Dict[str, Dict[str, Any]] = {}
    for s in shipments:
        cid = s["center_id"]
        if cid not in center_agg:
            center_agg[cid] = {
                "center_id":        cid,
                "total_demand":     0,
                "total_shipped":    0,
                "total_shortage":   0,
                "total_fixed_cost": 0,
                "total_holding":    0,
                "num_shipments":    0,
            }
        agg = center_agg[cid]
        agg["total_demand"]     += s.get("demand", 0)
        agg["total_shipped"]    += s.get("quantity", 0)
        agg["total_shortage"]   += s.get("shortage", 0)
        agg["total_fixed_cost"] += s.get("fixed_cost", 0)
        agg["total_holding"]    += s.get("holding_cost", 0)
        if s.get("shipped"):
            agg["num_shipments"] += 1

    center_rows = []
    for cid, agg in center_agg.items():
        sev = "WARNING" if agg["total_shortage"] > 0.5 else None
        center_rows.append({
            "center_id":        cid,
            "total_demand":     int(round(agg["total_demand"])),
            "total_shipped":    int(round(agg["total_shipped"])),
            "total_shortage":   int(round(agg["total_shortage"])),
            "total_fixed_cost": int(round(agg["total_fixed_cost"])),
            "total_holding":    int(round(agg["total_holding"])),
            "num_shipments":    agg["num_shipments"],
            "severity":         sev,
        })

    center_section = build_table_section(
        section_id="center_summary",
        title="配送センター別集計",
        columns=[
            TableColumn(key="center_id",        label="センターID"),
            TableColumn(key="total_demand",      label="総需要(個)",     format="number", align="right"),
            TableColumn(key="total_shipped",     label="総発送量(個)",   format="number", align="right"),
            TableColumn(key="total_shortage",    label="総未充足(個)",   format="number", align="right"),
            TableColumn(key="num_shipments",     label="発送回数(週)",   format="number", align="right"),
            TableColumn(key="total_fixed_cost",  label="発送固定費合計", format="currency", align="right"),
            TableColumn(key="total_holding",     label="保管費合計",     format="currency", align="right"),
        ],
        rows=center_rows,
        row_id_key="center_id",
        severity_key="severity",
        allow_download=True,
    )

    return {
        "domain":        "inventory_replenishment_planner",
        "feasible":      True,
        "summary": {
            "total_cost":          metrics.get("total_cost", 0),
            "total_shipping_cost": metrics.get("total_shipping_cost", 0),
            "total_holding_cost":  metrics.get("total_holding_cost", 0),
            "total_shortage":      metrics.get("total_shortage", 0),
            "num_centers":         metrics.get("num_centers", 0),
            "num_periods":         metrics.get("num_periods", 0),
        },
        "kpi_cards":      kpi_cards,
        "shipments":      shipments,
        "table_sections": [shipment_section, center_section],
        "alerts":         _build_alerts(issues),
        "raw_kpi":        metrics,
    }


def _build_alerts(issues: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    return [
        {
            "id":       i.get("id", ""),
            "severity": i.get("severity", "INFO"),
            "title":    i.get("title", ""),
            "message":  i.get("message", ""),
        }
        for i in issues
        if i.get("severity") in ("CRITICAL", "WARNING")
    ]