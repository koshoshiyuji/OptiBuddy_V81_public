"""
Backend/dsl_transformer/depot_route_planner_ui_converter.py

Solver Output DSL → UI DSL 変換 (DepotRoutePlanner)
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional

from dsl_transformer.table_sections import build_table_section, TableColumn


def convert_depot_route_planner_to_ui(
    solver_output: Dict[str, Any],
    business_dsl: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """
    Solver Output DSL → UI DSL
    ui_dsl["domain"] = "depot_route_planner" (snake_case, フロント判別用)
    """
    feasible = solver_output.get("feasible", False)
    solutions = solver_output.get("solutions", [])
    issues = solver_output.get("issues", [])

    if not feasible or not solutions:
        return {
            "domain":         "depot_route_planner",
            "feasible":       False,
            "summary":        {},
            "kpi_cards":      [],
            "open_depots":    [],
            "assignments":    [],
            "routes":         [],
            "table_sections": [],
            "alerts":         _build_alerts(issues),
            "raw_kpi":        {},
        }

    sol = solutions[0]
    kpi = sol.get("kpi", {})
    open_depots: List[Dict] = sol.get("open_depots", [])
    assignments: List[Dict] = sol.get("assignments", [])
    routes: List[Dict] = sol.get("routes", [])

    kpi_cards = [
        {
            "label": "総コスト",
            "value": f"¥{kpi.get('final_total_cost', 0):,.0f}",
            "sub":   "開設費 + 巡回距離",
            "color": "#8b5cf6",
        },
        {
            "label": "開設拠点数",
            "value": str(kpi.get("num_open_depots", 0)),
            "sub":   f"開設固定費合計 ¥{kpi.get('opening_cost_total', 0):,.0f}",
            "color": "#3b82f6",
        },
        {
            "label": "総巡回距離",
            "value": f"{kpi.get('travel_distance_total', 0):.1f}",
            "sub":   "全拠点の巡回路合計",
            "color": "#10b981",
        },
        {
            "label": "顧客数",
            "value": str(kpi.get("num_customers", 0)),
            "sub":   "全割当済み顧客",
            "color": "#f97316",
        },
    ]

    # ── table_sections ──────────────────────────────────────
    # 1. 開設拠点一覧
    depot_rows = [
        {
            "depot_id":            d["id"],
            "depot_name":          d["name"],
            "opening_cost":        d.get("opening_cost", 0),
            "travel_distance":     next(
                (r["travel_distance"] for r in routes if r["depot_id"] == d["id"]), 0.0
            ),
            "num_customers":       sum(
                1 for a in assignments if a.get("depot_id") == d["id"]
            ),
        }
        for d in open_depots
    ]
    depot_section = build_table_section(
        section_id="open_depots",
        title="開設拠点一覧",
        columns=[
            TableColumn(key="depot_name",         label="拠点名"),
            TableColumn(key="opening_cost",        label="開設固定費", format="currency", align="right"),
            TableColumn(key="travel_distance",     label="巡回距離", format="number", align="right"),
            TableColumn(key="num_customers",       label="担当顧客数",  format="number", align="right"),
        ],
        rows=depot_rows,
        row_id_key="depot_id",
    )

    # 2. 顧客割当一覧
    assignment_rows = [
        {
            "customer_id":   a["customer_id"],
            "customer_name": a["customer_name"],
            "depot_name":    a.get("depot_name") or "（未割当）",
            "assigned":      "✓" if a.get("depot_id") else "✗",
        }
        for a in assignments
    ]
    # 未割当行に CRITICAL severity を付与
    for row, a in zip(assignment_rows, assignments):
        if not a.get("depot_id"):
            row["_severity_flag"] = "CRITICAL"

    assignment_section = build_table_section(
        section_id="assignments",
        title="顧客割当一覧",
        columns=[
            TableColumn(key="customer_name", label="顧客名"),
            TableColumn(key="depot_name",    label="担当拠点"),
            TableColumn(key="assigned",      label="割当済", format="badge", align="center"),
        ],
        rows=assignment_rows,
        row_id_key="customer_id",
        severity_key="_severity_flag",
    )

    # 3. 巡回ルート詳細
    route_rows = []
    for r in routes:
        visit_names = " → ".join(s["customer_name"] for s in r.get("order", []))
        route_rows.append({
            "depot_id":           r["depot_id"],
            "depot_name":         r["depot_name"],
            "num_stops":          len(r.get("order", [])),
            "travel_distance":    r.get("travel_distance", 0.0),
            "visit_order":        visit_names or "（顧客なし）",
        })
    route_section = build_table_section(
        section_id="routes",
        title="巡回ルート詳細",
        columns=[
            TableColumn(key="depot_name",         label="担当拠点"),
            TableColumn(key="num_stops",           label="訪問件数",    format="number", align="right"),
            TableColumn(key="travel_distance",     label="巡回距離", format="number", align="right"),
            TableColumn(key="visit_order",         label="訪問順序"),
        ],
        rows=route_rows,
        row_id_key="depot_id",
    )

    return {
        "domain":         "depot_route_planner",
        "feasible":       True,
        "summary": {
            "final_total_cost":           kpi.get("final_total_cost", 0),
            "opening_cost_total":         kpi.get("opening_cost_total", 0),
            "travel_distance_total":      kpi.get("travel_distance_total", 0),
            "num_open_depots":            kpi.get("num_open_depots", 0),
            "num_customers":              kpi.get("num_customers", 0),
        },
        "kpi_cards":      kpi_cards,
        "open_depots":    open_depots,
        "assignments":    assignments,
        "routes":         routes,
        "table_sections": [depot_section, assignment_section, route_section],
        "alerts":         _build_alerts(issues),
        "raw_kpi":        kpi,
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