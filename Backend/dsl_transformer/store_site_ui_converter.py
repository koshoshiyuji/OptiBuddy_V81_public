"""
store_site_ui_converter.py — StoreSite Solver Output DSL → UI DSL
"""

from typing import Any, Dict, List, Optional


def convert_store_site_to_ui(
    solver_output: Dict[str, Any],
    business_dsl: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    from dsl_transformer.table_sections import build_table_section, TableColumn

    feasible  = solver_output.get("feasible", False)
    solutions = solver_output.get("solutions", [])
    sol       = solutions[0] if solutions else {}
    issues    = solver_output.get("issues", [])

    opened_stores    = sol.get("opened_stores", [])
    unassigned_areas = sol.get("unassigned_areas", [])
    kpi              = sol.get("kpi", {})

    # ── KPI カード ──────────────────────────────────────────
    kpi_cards = []
    if feasible:
        kpi_cards = [
            {
                "label": "開設店舗数",
                "value": kpi.get("opened_store_count", 0),
                "unit":  "店舗",
                "color": "#10b981",
            },
            {
                "label": "カバー率",
                "value": f"{round(kpi.get('coverage_rate', 0) * 100, 1)}%",
                "unit":  "",
                "color": "#3b82f6",
            },
            {
                "label": "総固定費",
                "value": int(kpi.get("total_fixed_cost", 0)),
                "unit":  "円",
                "color": "#f97316",
                "format": "currency",
            },
            {
                "label": "未割当エリア",
                "value": kpi.get("unassigned_area_count", 0),
                "unit":  "エリア",
                "color": "#ef4444" if kpi.get("unassigned_area_count", 0) > 0 else "#888",
            },
        ]

    # ── アラート ────────────────────────────────────────────
    alerts = []
    for issue in issues:
        if issue.get("severity") in ("CRITICAL", "WARNING"):
            alerts.append({
                "severity": issue["severity"],
                "title":    issue.get("title", ""),
                "message":  issue.get("message", ""),
            })

    # ── table_sections ───────────────────────────────────────

    # 開設店舗一覧
    store_rows = []
    for s in opened_stores:
        store_rows.append({
            "candidate_id":   s["candidate_id"],
            "name":           s["name"],
            "fixed_cost":     int(s["fixed_cost"]),
            "capacity":       s["capacity"],
            "total_demand":   s["total_demand"],
            "utilization":    s["utilization"],
            "assigned_count": len(s.get("assigned_areas", [])),
            "assigned_areas": ", ".join(s.get("assigned_areas", [])),
            "severity": (
                "WARNING"
                if s["utilization"] > (
                    (business_dsl or {}).get("config", {}).get("utilization_threshold", 0.8)
                )
                else None
            ),
        })

    store_section = build_table_section(
        section_id="opened_stores",
        title="開設店舗一覧",
        columns=[
            TableColumn(key="name",           label="店舗名"),
            TableColumn(key="fixed_cost",     label="固定費", format="currency", align="right"),
            TableColumn(key="capacity",       label="容量",   format="number",   align="right"),
            TableColumn(key="total_demand",   label="受持需要", format="number", align="right"),
            TableColumn(key="utilization",    label="稼働率", format="percent",  align="right"),
            TableColumn(key="assigned_count", label="担当エリア数", format="number", align="right"),
            TableColumn(key="assigned_areas", label="担当エリアID"),
        ],
        rows=store_rows,
        row_id_key="candidate_id",
        severity_key="severity",
    )

    # 未割当エリア一覧
    unassigned_rows = []
    for ua in unassigned_areas:
        unassigned_rows.append({
            "area_id":  ua["area_id"],
            "name":     ua["name"],
            "demand":   ua["demand"],
            "severity": "CRITICAL",
        })

    unassigned_section = build_table_section(
        section_id="unassigned_areas",
        title="未割当エリア一覧",
        columns=[
            TableColumn(key="name",   label="エリア名"),
            TableColumn(key="demand", label="需要",    format="number", align="right"),
        ],
        rows=unassigned_rows,
        row_id_key="area_id",
        severity_key="severity",
    )

    table_sections = [store_section]
    if unassigned_rows:
        table_sections.append(unassigned_section)

    return {
        "domain":       "store_site",
        "feasible":     feasible,
        "summary": {
            "opened_store_count":    kpi.get("opened_store_count", 0),
            "coverage_rate":         kpi.get("coverage_rate", 0),
            "total_fixed_cost":      kpi.get("total_fixed_cost", 0),
            "total_supply_cost":     kpi.get("total_supply_cost", 0),
            "total_unmet_demand":    kpi.get("total_unmet_demand", 0),
            "unassigned_area_count": kpi.get("unassigned_area_count", 0),
            "objective_value":       kpi.get("objective_value", 0),
        },
        "kpi_cards":         kpi_cards,
        "opened_stores":     opened_stores,
        "unassigned_areas":  unassigned_areas,
        "table_sections":    table_sections,
        "alerts":            alerts,
        "raw_kpi":           kpi,
    }