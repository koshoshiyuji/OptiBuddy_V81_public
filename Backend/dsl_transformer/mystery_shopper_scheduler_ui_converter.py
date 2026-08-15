"""
Backend/dsl_transformer/mystery_shopper_scheduler_ui_converter.py

Solver Output DSL → UI DSL 変換

ui_dsl keys:
  - domain: "mystery_shopper_scheduler"
  - feasible: bool
  - summary: Dict
  - kpi_cards: List[Dict]
  - assignments: List[Dict]
  - shopper_summary: List[Dict]
  - table_sections: List[Dict]   # GenericResultTable 用
  - alerts: List[Dict]
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional


def convert_mystery_shopper_scheduler_to_ui(
    solver_output: dict,
    business_dsl: Optional[dict] = None,
) -> dict:
    from dsl_transformer.table_sections import build_table_section, TableColumn

    solutions = solver_output.get("solutions", [])
    issues = solver_output.get("issues", [])
    feasible = solver_output.get("feasible", False)

    if not solutions:
        return {
            "domain": "mystery_shopper_scheduler",
            "feasible": feasible,
            "summary": {},
            "kpi_cards": [],
            "assignments": [],
            "shopper_summary": [],
            "table_sections": [],
            "alerts": _build_alerts(issues),
        }

    sol = solutions[0]
    kpi: Dict = sol.get("kpi", {})
    assignments: List[Dict] = sol.get("assignments", [])
    shopper_loads: Dict[str, int] = sol.get("shopper_loads", {})

    # ── KPI カード ──
    coverage_pct = round(kpi.get("coverage_rate", 0.0) * 100, 1)
    kpi_cards = [
        {
            "label": "充足率",
            "value": f"{coverage_pct}%",
            "sub": f"{kpi.get('assigned_count', 0)} / {kpi.get('total_visits', 0)} 件",
            "color": "#10b981" if coverage_pct >= 90 else "#f97316",
        },
        {
            "label": "未割当件数",
            "value": str(kpi.get("unassigned_count", 0)),
            "sub": "件",
            "color": "#ef4444" if kpi.get("unassigned_count", 0) > 0 else "#10b981",
        },
        {
            "label": "担当件数ばらつき",
            "value": str(kpi.get("load_range", 0)),
            "sub": f"最大{kpi.get('max_shopper_load', 0)}件 / 最小{kpi.get('min_shopper_load', 0)}件",
            "color": "#3b82f6",
        },
        {
            "label": "ソルブ時間",
            "value": f"{kpi.get('solve_time', 0.0):.1f}s",
            "sub": "",
            "color": "#888",
        },
    ]

    # ── サマリー ──
    summary = {
        "total_visits": kpi.get("total_visits", 0),
        "assigned_count": kpi.get("assigned_count", 0),
        "unassigned_count": kpi.get("unassigned_count", 0),
        "coverage_rate": kpi.get("coverage_rate", 0.0),
        "load_range": kpi.get("load_range", 0),
    }

    # ── 調査員別サマリー（テーブル用行データ）──
    shopper_summary_rows: List[Dict] = []
    for s_id, load in shopper_loads.items():
        shopper_summary_rows.append({
            "shopper_id": s_id,
            "shopper_name": s_id,  # ui_converter は name を持たないため ID で代替
            "assigned_count": load,
        })
    # business_dsl から name を補完できる場合
    if business_dsl:
        name_map = {str(sh["id"]): sh.get("name", str(sh["id"]))
                    for sh in business_dsl.get("shoppers", [])}
        for row in shopper_summary_rows:
            row["shopper_name"] = name_map.get(row["shopper_id"], row["shopper_id"])

    # ── 割り当て詳細行データ ──
    store_name_map: Dict[str, str] = {}
    area_map: Dict[str, str] = {}
    if business_dsl:
        for s in business_dsl.get("stores", []):
            store_name_map[str(s["id"])] = s.get("name", str(s["id"]))
            area_map[str(s["id"])] = s.get("area", "")

    assignment_rows: List[Dict] = []
    for a in assignments:
        v_id = a["visit_id"]
        store_id = v_id.rsplit("_v", 1)[0] if "_v" in v_id else v_id
        store_name = store_name_map.get(store_id, store_id)
        assignment_rows.append({
            "visit_id": v_id,
            "store_name": store_name,
            "store_area": area_map.get(store_id, ""),
            "shopper_id": a["shopper_id"],
            "shopper_name": (name_map.get(a["shopper_id"], a["shopper_id"])
                             if business_dsl else a["shopper_id"]),
            "day": a["day"],
        })
    assignment_rows.sort(key=lambda r: (r["day"], r["store_name"]))

    # ── 未割当行データ ──
    assigned_v_ids = {a["visit_id"] for a in assignments}
    all_visits = []
    if business_dsl:
        for s in business_dsl.get("stores", []):
            s_id = str(s["id"])
            for i in range(int(s.get("visit_count", 1))):
                v_id = f"{s_id}_v{i+1}"
                if v_id not in assigned_v_ids:
                    all_visits.append({
                        "visit_id": v_id,
                        "store_name": store_name_map.get(s_id, s_id),
                        "store_area": s.get("area", ""),
                        "required_qualifications": ", ".join(s.get("required_qualifications", [])) or "なし",
                    })

    # ── アラート ──
    alerts = _build_alerts(issues)

    # ── table_sections ──
    # セクション1: 割り当て一覧
    assignment_section = build_table_section(
        section_id="mss_assignments",
        title="割り当て一覧",
        columns=[
            TableColumn(key="day", label="訪問日", align="left"),
            TableColumn(key="store_name", label="店舗名", align="left"),
            TableColumn(key="store_area", label="エリア", align="left"),
            TableColumn(key="shopper_name", label="担当調査員", align="left"),
        ],
        rows=assignment_rows,
        row_id_key="visit_id",
    )

    # セクション2: 調査員別担当件数
    shopper_section = build_table_section(
        section_id="mss_shopper_summary",
        title="調査員別担当件数",
        columns=[
            TableColumn(key="shopper_name", label="調査員", align="left"),
            TableColumn(key="assigned_count", label="担当件数", format="number", align="right"),
        ],
        rows=shopper_summary_rows,
        row_id_key="shopper_id",
    )

    # セクション3: 未割当訪問枠
    unassigned_section = build_table_section(
        section_id="mss_unassigned",
        title="未割当の訪問枠",
        columns=[
            TableColumn(key="store_name", label="店舗名", align="left"),
            TableColumn(key="store_area", label="エリア", align="left"),
            TableColumn(key="required_qualifications", label="必要資格", align="left"),
        ],
        rows=all_visits,
        row_id_key="visit_id",
    )

    table_sections = [assignment_section, shopper_section]
    if all_visits:
        table_sections.append(unassigned_section)

    return {
        "domain": "mystery_shopper_scheduler",
        "feasible": feasible,
        "summary": summary,
        "kpi_cards": kpi_cards,
        "assignments": assignment_rows,
        "shopper_summary": shopper_summary_rows,
        "table_sections": table_sections,
        "alerts": alerts,
    }


def _build_alerts(issues: List[Dict]) -> List[Dict]:
    alerts = []
    for iss in issues:
        if iss.get("severity") in ("CRITICAL", "WARNING"):
            alerts.append({
                "id": iss.get("id", ""),
                "severity": iss.get("severity", "WARNING"),
                "title": iss.get("title", ""),
                "message": iss.get("message", ""),
            })
    return alerts