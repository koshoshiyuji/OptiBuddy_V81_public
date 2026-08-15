"""
meeting_room_ui_converter.py — Solver Output DSL → UI DSL

ui_dsl keys:
  - domain: "meeting_room"
  - feasible: bool
  - summary: Dict
  - kpi_cards: List[Dict]
  - assignments: List[Dict]
  - table_sections: List[Dict]  (GenericResultTable 用)
  - alerts: List[Dict]
  - raw_kpi: Dict
"""

from __future__ import annotations

from typing import Any, Dict, List


def _min_to_hhmm(minutes: int) -> str:
    minutes = int(minutes)
    h = minutes // 60
    m = minutes % 60
    return f"{h:02d}:{m:02d}"


def convert_meeting_room_to_ui(
    solver_output: dict,
    business_dsl: dict | None = None,
) -> dict:
    from dsl_transformer.table_sections import build_table_section, TableColumn

    solutions  = solver_output.get("solutions", [])
    solution   = solutions[0] if solutions else {}
    feasible   = solution.get("feasible", False)
    kpi        = solution.get("kpi", {})
    assignments = solution.get("assignments", [])
    issues     = solver_output.get("issues", [])

    total     = kpi.get("total_meetings", len(assignments))
    assigned  = kpi.get("assigned_count", len(assignments))
    unassigned = kpi.get("unassigned_count", total - assigned)
    obj_val   = kpi.get("objective_value", 0)

    # KPI カード
    kpi_cards = [
        {"label": "割当済み",   "value": assigned,   "unit": "件", "color": "#10b981"},
        {"label": "未割当",     "value": unassigned, "unit": "件", "color": "#ef4444" if unassigned > 0 else "#10b981"},
        {"label": "総会議数",   "value": total,      "unit": "件", "color": "#3b82f6"},
        {"label": "目的値",     "value": round(obj_val, 1), "unit": "",  "color": "#f97316"},
    ]

    # アラート
    alerts = []
    if unassigned > 0:
        alerts.append({
            "severity": "CRITICAL",
            "title":    f"{unassigned}件の会議が未割当です",
            "message":  "収容人数・設備・利用可能時間帯を確認してください。",
        })
    critical_issues = [i for i in issues if i.get("severity") == "CRITICAL"]
    warning_issues  = [i for i in issues if i.get("severity") == "WARNING"]
    for issue in warning_issues[:3]:
        alerts.append({
            "severity": "WARNING",
            "title":    issue.get("title", ""),
            "message":  issue.get("message", ""),
        })

    # テーブル行（割当一覧）
    assignment_rows = []
    for a in assignments:
        waste = a.get("capacity", 0) - a.get("attendees", 0)
        assignment_rows.append({
            "meeting_id":   a["meeting_id"],
            "meeting_name": a.get("meeting_name", ""),
            "dept":         a.get("dept") or "—",
            "room_name":    a.get("room_name", ""),
            "start":        _min_to_hhmm(a.get("start_min", 0)),
            "end":          _min_to_hhmm(a.get("end_min", 0)),
            "attendees":    a.get("attendees", 0),
            "capacity":     a.get("capacity", 0),
            "waste":        waste,
            "features":     ", ".join(a.get("features", [])) or "—",
            "severity":     "WARNING" if a.get("attendees", 0) >= a.get("capacity", 1) else None,
        })

    # 未割当行（ISSUESから補完）
    unassigned_rows = []
    for issue in critical_issues:
        if issue.get("id", "").startswith("unassigned_"):
            mid = issue["id"].replace("unassigned_", "")
            unassigned_rows.append({
                "meeting_id":   mid,
                "meeting_name": issue.get("title", "").replace("未割当: ", ""),
                "reason":       issue.get("message", ""),
                "severity":     "CRITICAL",
            })

    table_sections = [
        build_table_section(
            section_id="meeting_assignments",
            title="会議室割当一覧",
            columns=[
                TableColumn(key="meeting_name", label="会議名"),
                TableColumn(key="dept",         label="部署"),
                TableColumn(key="room_name",    label="会議室"),
                TableColumn(key="start",        label="開始"),
                TableColumn(key="end",          label="終了"),
                TableColumn(key="attendees",    label="参加人数",   format="number", align="right"),
                TableColumn(key="capacity",     label="収容人数",   format="number", align="right"),
                TableColumn(key="waste",        label="余剰人数",   format="number", align="right"),
                TableColumn(key="features",     label="利用設備"),
            ],
            rows=assignment_rows,
            row_id_key="meeting_id",
            severity_key="severity",
            allow_download=True,
        ),
    ]

    if unassigned_rows:
        table_sections.append(
            build_table_section(
                section_id="unassigned_meetings",
                title="未割当会議",
                columns=[
                    TableColumn(key="meeting_name", label="会議名"),
                    TableColumn(key="reason",       label="未割当理由"),
                ],
                rows=unassigned_rows,
                row_id_key="meeting_id",
                severity_key="severity",
                allow_download=True,
            )
        )

    return {
        "domain":          "meeting_room",
        "feasible":        feasible,
        "summary": {
            "total_meetings": total,
            "assigned":       assigned,
            "unassigned":     unassigned,
        },
        "kpi_cards":       kpi_cards,
        "assignments":     assignments,
        "table_sections":  table_sections,
        "alerts":          alerts,
        "raw_kpi":         kpi,
    }