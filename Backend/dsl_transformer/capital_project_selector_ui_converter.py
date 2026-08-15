"""
Backend/dsl_transformer/capital_project_selector_ui_converter.py

Solver Output DSL → UI DSL 変換（CapitalProjectSelector）
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional

from dsl_transformer.table_sections import build_table_section, TableColumn


def convert_capital_project_selector_to_ui(
    solver_output: dict,
    business_dsl: Optional[dict] = None,
) -> dict:
    """
    Solver Output DSL → UI DSL

    ui_dsl.domain = "capital_project_selector"（フロントのドメイン判別に使う）
    """
    feasible = solver_output.get("feasible", False)
    solutions = solver_output.get("solutions", [])
    issues = solver_output.get("issues", [])
    meta = solver_output.get("metadata", {})

    sol = solutions[0] if solutions else {}
    kpi = sol.get("kpi", {})
    selected_projects: List[Dict[str, Any]] = sol.get("selected_projects", [])

    # ── KPIカード ──
    budget = float(kpi.get("budget", 0.0))
    total_value = float(kpi.get("total_value", 0.0))
    total_weight = float(kpi.get("total_weight", 0.0))
    utilization = float(kpi.get("budget_utilization", 0.0))
    selected_count = int(kpi.get("selected_count", 0))
    is_optimal = bool(kpi.get("is_optimal", False))

    kpi_cards = []
    if feasible:
        kpi_cards = [
            {
                "label": "期待効果合計",
                "value": f"¥{total_value:,.0f}",
                "color": "#10b981",
                "icon": "💹",
            },
            {
                "label": "投資額合計",
                "value": f"¥{total_weight:,.0f}",
                "color": "#f97316",
                "icon": "💰",
            },
            {
                "label": "予算消化率",
                "value": f"{round(utilization * 100, 1)}%",
                "color": "#3b82f6",
                "icon": "📊",
            },
            {
                "label": "選定案件数",
                "value": f"{selected_count} 件",
                "color": "#8b5cf6",
                "icon": "✅",
            },
        ]

    # ── アラート ──
    alerts = []
    if feasible and not is_optimal:
        alerts.append({
            "id": "not_proven_optimal",
            "severity": "INFO",
            "title": "最適性未証明",
            "message": "制限時間内に求解を打ち切りました。より良い組み合わせが存在する可能性があります。",
        })
    if feasible and utilization < 0.5:
        alerts.append({
            "id": "low_utilization_alert",
            "severity": "INFO",
            "title": "予算消化率が低い",
            "message": f"予算の {round(utilization * 100, 1)}% しか使われていません。追加案件の検討を推奨します。",
        })

    # ── 選定案件一覧テーブル ──
    project_rows = []
    for p in selected_projects:
        project_rows.append({
            "id":       p.get("id", ""),
            "name":     p.get("name", ""),
            "value":    float(p.get("value", 0.0)),
            "weight":   float(p.get("weight", 0.0)),
            "category": p.get("category", ""),
            "tags":     ", ".join(p.get("tags", [])),
        })

    selected_section = build_table_section(
        section_id="selected_projects",
        title="選定案件一覧",
        columns=[
            TableColumn(key="id",       label="案件ID",     align="left"),
            TableColumn(key="name",     label="案件名",     align="left"),
            TableColumn(key="value",    label="期待効果(円)", align="right", format="currency"),
            TableColumn(key="weight",   label="投資額(円)",  align="right", format="currency"),
            TableColumn(key="category", label="カテゴリ",   align="left"),
            TableColumn(key="tags",     label="タグ",       align="left"),
        ],
        rows=project_rows,
        row_id_key="id",
        allow_download=True,
    )

    # ── 全案件比較テーブル（business_dsl から全案件情報を取る） ──
    all_project_rows = []
    selected_ids = {p.get("id") for p in selected_projects}
    all_projects: List[Dict[str, Any]] = []
    if business_dsl:
        all_projects = business_dsl.get("projects", [])
    if not all_projects and selected_projects:
        all_projects = selected_projects

    for p in all_projects:
        pid = str(p.get("id", ""))
        is_selected = pid in selected_ids
        all_project_rows.append({
            "id":       pid,
            "name":     p.get("name", ""),
            "value":    float(p.get("value", p.get("expected_value", 0.0))),
            "weight":   float(p.get("weight", p.get("investment", p.get("cost", 0.0)))),
            "category": p.get("category", ""),
            "status":   "SELECTED" if is_selected else "NOT SELECTED",
        })

    all_projects_section = build_table_section(
        section_id="all_projects",
        title="全候補案件（比較）",
        columns=[
            TableColumn(key="id",       label="案件ID",     align="left"),
            TableColumn(key="name",     label="案件名",     align="left"),
            TableColumn(key="value",    label="期待効果(円)", align="right", format="currency"),
            TableColumn(key="weight",   label="投資額(円)",  align="right", format="currency"),
            TableColumn(key="category", label="カテゴリ",   align="left"),
            TableColumn(key="status",   label="選定状態",   align="center", format="badge"),
        ],
        rows=all_project_rows,
        row_id_key="id",
        allow_download=True,
    )

    table_sections = [selected_section, all_projects_section] if feasible else [all_projects_section]

    return {
        "domain":           "capital_project_selector",
        "feasible":         feasible,
        "summary": {
            "total_value":         total_value,
            "total_weight":        total_weight,
            "budget":              budget,
            "budget_utilization":  utilization,
            "selected_count":      selected_count,
            "is_optimal":          is_optimal,
        },
        "kpi_cards":        kpi_cards,
        "selected_projects": selected_projects,
        "table_sections":   table_sections,
        "alerts":           alerts,
        "raw_kpi":          kpi,
    }