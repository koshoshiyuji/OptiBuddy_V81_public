"""
Backend/dsl_transformer/shift_rotation_scheduler_ui_converter.py

Solver Output DSL → UI DSL 変換
ShiftRotationScheduler（循環シフトテンプレートスケジューラ）
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional


def convert_shift_rotation_scheduler_to_ui(
    solver_output: dict,
    business_dsl: Optional[dict] = None,
) -> dict:
    """
    Solver Output DSL → UI DSL

    ui_dsl["domain"] = "shift_rotation_scheduler" を必ずセット（フロント判別用）。
    """
    from dsl_transformer.table_sections import build_table_section, TableColumn

    feasible: bool = solver_output.get("feasible", False)
    solutions: List[Dict] = solver_output.get("solutions", [])
    issues: List[Dict] = solver_output.get("issues", [])

    if not feasible or not solutions:
        return {
            "domain":   "shift_rotation_scheduler",
            "feasible": False,
            "summary":  {},
            "kpi_cards": [],
            "template":  [],
            "employee_schedules": [],
            "table_sections": [],
            "alerts":   _build_alerts(issues),
            "raw_kpi":  {},
        }

    sol = solutions[0]
    kpi: Dict = sol.get("kpi", {})
    W: int = kpi.get("num_employees", 0)
    employee_schedules: List[Dict] = sol.get("employee_schedules", [])
    template_shift_ids: List[List[str]] = sol.get("template_shift_ids", [])

    # ── KPIカード ────────────────────────────────────────────────────────────
    kpi_cards = [
        {"label": "従業員数 / 周期週数", "value": str(W),   "unit": "名 / 週",  "color": "#8b5cf6"},
        {"label": "1人あたり勤務日数",   "value": str(kpi.get("working_days_per_emp", 0)), "unit": "日/周期", "color": "#10b981"},
        {"label": "1人あたり休日数",     "value": str(kpi.get("off_days_per_emp", 0)),     "unit": "日/周期", "color": "#3b82f6"},
    ]

    # ── テーブル1: シフトテンプレート（週×曜日）──────────────────────────────
    DAY_LABELS = ["月", "火", "水", "木", "金", "土", "日"]
    template_rows = []
    for w, week_row in enumerate(template_shift_ids):
        row: Dict[str, Any] = {"week": f"Week {w + 1}"}
        for d, sid in enumerate(week_row):
            row[DAY_LABELS[d]] = sid
        row["id"] = str(w)
        template_rows.append(row)

    template_columns = [TableColumn(key="week", label="週")] + [
        TableColumn(key=dl, label=dl) for dl in DAY_LABELS
    ]
    template_section = build_table_section(
        section_id="shift_template",
        title="シフトテンプレート（週×曜日）",
        columns=template_columns,
        rows=template_rows,
        row_id_key="id",
    )

    # ── テーブル2: 従業員別スケジュール（フラット展開）────────────────────────
    emp_rows = []
    for emp in employee_schedules:
        for w, week_data in enumerate(emp.get("weeks", [])):
            row: Dict[str, Any] = {
                "emp_id":   emp["employee_id"],
                "emp_name": emp["employee_name"],
                "week":     f"Week {w + 1}",
            }
            for d, day_info in enumerate(week_data):
                row[DAY_LABELS[d]] = day_info.get("shift_id", "")
            row["id"] = f"{emp['employee_id']}_w{w}"
            emp_rows.append(row)

    emp_columns = [
        TableColumn(key="emp_name", label="従業員"),
        TableColumn(key="week",     label="週"),
    ] + [TableColumn(key=dl, label=dl) for dl in DAY_LABELS]

    emp_section = build_table_section(
        section_id="employee_schedules",
        title="従業員別スケジュール",
        columns=emp_columns,
        rows=emp_rows,
        row_id_key="id",
    )

    summary = {
        "num_employees":      W,
        "cycle_weeks":        W,
        "total_working_days": kpi.get("total_working_days", 0),
        "total_off_days":     kpi.get("total_off_days", 0),
    }

    return {
        "domain":             "shift_rotation_scheduler",
        "feasible":           True,
        "summary":            summary,
        "kpi_cards":          kpi_cards,
        "template":           template_shift_ids,
        "employee_schedules": employee_schedules,
        "table_sections":     [template_section, emp_section],
        "alerts":             _build_alerts(issues),
        "raw_kpi":            kpi,
    }


def _build_alerts(issues: List[Dict]) -> List[Dict]:
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