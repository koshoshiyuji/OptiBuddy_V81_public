"""
Backend/dsl_transformer/production_line_sequencing_ui_converter.py

Solver Output DSL → UI DSL 変換
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional

from dsl_transformer.table_sections import build_table_section, TableColumn
from i18n.production_line_sequencing_messages import t


def convert_production_line_sequencing_to_ui(
    solver_output: dict, business_dsl: Optional[dict] = None
) -> dict:
    """Solver Output DSL → UI DSL"""

    feasible = solver_output.get("feasible", False)
    issues = solver_output.get("issues", [])
    solutions = solver_output.get("solutions", [])

    assignments: List[Dict] = []
    kpi: Dict = {}
    if solutions:
        sol = solutions[0]
        assignments = sol.get("assignments", [])
        kpi = sol.get("kpi", {})

    # KPI カード
    kpi_cards = _build_kpi_cards(kpi, assignments, feasible)

    # アラート
    alerts = _build_alerts(issues)

    # テーブルセクション
    table_sections = _build_table_sections(assignments)

    # スケジュール（日 × ライン のグリッド表現）
    schedule_grid = _build_schedule_grid(assignments)

    return {
        "domain": "production_line_sequencing",
        "feasible": feasible,
        "summary": {
            "total_batches": kpi.get("total_batches", 0),
            "assigned_batches": kpi.get("assigned_batches", len(assignments)),
            "balance_score": kpi.get("balance_score", 0),
            "solve_status": kpi.get("solve_status", ""),
        },
        "kpi_cards": kpi_cards,
        "assignments": assignments,
        "schedule_grid": schedule_grid,
        "table_sections": table_sections,
        "alerts": alerts,
        "raw_kpi": kpi,
    }


def _build_kpi_cards(kpi: Dict, assignments: List[Dict], feasible: bool) -> List[Dict]:
    cards = []
    if not feasible:
        cards.append({
            "id": "feasibility",
            "label": t("kpi.feasibility.label"),
            "value": t("kpi.feasibility.infeasible"),
            "unit": "",
            "color": "red",
        })
        return cards

    cards.append({
        "id": "assigned_batches",
        "label": t("kpi.assigned_batches.label"),
        "value": kpi.get("assigned_batches", len(assignments)),
        "unit": t("kpi.assigned_batches.unit"),
        "color": "green",
    })
    cards.append({
        "id": "balance_score",
        "label": t("kpi.balance_score.label"),
        "value": kpi.get("balance_score", 0),
        "unit": t("kpi.balance_score.unit"),
        "color": "blue",
        "sub": t("kpi.balance_score.sub"),
    })

    # ライン別使用数
    line_usage = kpi.get("line_usage", [])
    from collections import defaultdict
    line_total: Dict[str, int] = defaultdict(int)
    for u in line_usage:
        line_total[u["line_id"]] += u["count"]
    for lid, cnt in sorted(line_total.items()):
        cards.append({
            "id": f"line_usage_{lid}",
            "label": t("kpi.line_usage.label", line_id=lid),
            "value": cnt,
            "unit": t("kpi.line_usage.unit"),
            "color": "teal",
        })

    return cards


def _build_alerts(issues: List[Dict]) -> List[Dict]:
    alerts = []
    for issue in issues:
        if issue.get("severity") in ("CRITICAL", "WARNING"):
            alerts.append({
                "id": issue.get("id", ""),
                "severity": issue.get("severity", "WARNING"),
                "title": issue.get("title", ""),
                "message": issue.get("message", ""),
            })
    return alerts


def _build_table_sections(assignments: List[Dict]) -> List[Dict]:
    # 割当一覧テーブル
    assignment_rows = []
    for a in sorted(assignments, key=lambda x: (x["day"], x["start_hour"], x["line_id"])):
        assignment_rows.append({
            "id": f"{a['batch_id']}_{a['slot_id']}",
            "batch_id": a["batch_id"],
            "batch_name": a.get("batch_name", a["batch_id"]),
            "vehicle_type": a.get("vehicle_type", ""),
            "line_name": a.get("line_name", a["line_id"]),
            "day": a["day"],
            "start_hour": a["start_hour"],
            "slot_id": a["slot_id"],
        })

    assignment_section = build_table_section(
        section_id="assignment_detail",
        title=t("table.section.assignment_detail.title"),
        columns=[
            TableColumn(key="batch_name", label=t("table.column.batch_name")),
            TableColumn(key="vehicle_type", label=t("table.column.vehicle_type")),
            TableColumn(key="line_name", label=t("table.column.line_name")),
            TableColumn(key="day", label=t("table.column.day"), format="number", align="right"),
            TableColumn(key="start_hour", label=t("table.column.start_hour"), format="number", align="right"),
        ],
        rows=assignment_rows,
        row_id_key="id",
    )

    return [assignment_section]


def _build_schedule_grid(assignments: List[Dict]) -> List[Dict]:
    """日 × ライン × 時間帯 のスケジュールグリッド（フロント描画用）"""
    grid = []
    for a in assignments:
        grid.append({
            "day": a["day"],
            "line_id": a["line_id"],
            "line_name": a.get("line_name", a["line_id"]),
            "start_hour": a["start_hour"],
            "batch_id": a["batch_id"],
            "batch_name": a.get("batch_name", a["batch_id"]),
            "vehicle_type": a.get("vehicle_type", ""),
        })
    return sorted(grid, key=lambda x: (x["day"], x["line_id"], x["start_hour"]))