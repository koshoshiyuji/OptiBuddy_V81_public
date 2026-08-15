"""
Backend/dsl_transformer/medical_appointment_scheduler_ui_converter.py

Solver Output DSL → UI DSL 変換（MedicalAppointmentScheduler）
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional


def convert_medical_appointment_scheduler_to_ui(
    solver_output: dict,
    business_dsl: Optional[dict] = None,
) -> dict:
    from dsl_transformer.table_sections import build_table_section, TableColumn

    feasible: bool = solver_output.get("feasible", False)
    solutions = solver_output.get("solutions", [])
    solution = solutions[0] if solutions else {}
    assignments: List[Dict] = solution.get("assignments", [])
    kpi: Dict = solution.get("kpi", {})
    issues: List[Dict] = solver_output.get("issues", [])

    total_req = int(kpi.get("total_requests", 0))
    assigned_count = int(kpi.get("assigned_count", 0))
    unassigned_count = int(kpi.get("unassigned_count", 0))
    coverage_rate = float(kpi.get("coverage_rate", 0.0))
    pref_violations = int(kpi.get("pref_total_violations", 0))

    # KPIカード
    kpi_cards = [
        {
            "id": "coverage",
            "label": "対応率",
            "value": f"{round(coverage_rate * 100, 1)}%",
            "sub": f"{assigned_count} / {total_req} 件",
            "color": "#10b981" if coverage_rate >= 0.9 else "#f97316" if coverage_rate >= 0.7 else "#ef4444",
        },
        {
            "id": "assigned",
            "label": "割当済み",
            "value": str(assigned_count),
            "sub": "受診依頼",
            "color": "#3b82f6",
        },
        {
            "id": "unassigned",
            "label": "未割当",
            "value": str(unassigned_count),
            "sub": "受診依頼",
            "color": "#ef4444" if unassigned_count > 0 else "#10b981",
        },
        {
            "id": "pref_violations",
            "label": "希望違反",
            "value": str(pref_violations),
            "sub": "件（日付/資源/曜日・時間帯）",
            "color": "#f59e0b" if pref_violations > 0 else "#10b981",
        },
    ]

    # アラート
    alerts = []
    critical_issues = [i for i in issues if i.get("severity") == "CRITICAL"]
    warning_issues = [i for i in issues if i.get("severity") == "WARNING"]
    if not feasible:
        alerts.append({
            "id": "infeasible",
            "severity": "CRITICAL",
            "message": "有効な割当が見つかりませんでした。資源・スロット設定を見直してください。",
        })
    if unassigned_count > 0 and feasible:
        alerts.append({
            "id": "unassigned_exists",
            "severity": "WARNING" if unassigned_count <= total_req * 0.2 else "CRITICAL",
            "message": f"{unassigned_count} 件の受診依頼が未割当です。",
        })
    if pref_violations > 0:
        alerts.append({
            "id": "pref_violation_exists",
            "severity": "INFO",
            "message": f"希望違反が {pref_violations} 件あります（日付・資源・曜日/時間帯）。",
        })

    # 詳細テーブル: 割当一覧
    def _sev(a: Dict) -> str:
        if a.get("day_preference_violated") or a.get("resource_preference_violated") or \
           a.get("day_time_preference_violated"):
            return "WARNING"
        return "INFO"

    assignment_rows = [
        {
            "request_id": a["request_id"],
            "patient_name": a.get("patient_name", a["request_id"]),
            "day": a.get("day", ""),
            "start_min": _fmt_min(a.get("start_min", 0)),
            "end_min": _fmt_min(a.get("end_min", 0)),
            "weekday": a.get("weekday", ""),
            "time_slot": a.get("time_slot", ""),
            "resources": ", ".join(
                r.get("resource_name", r.get("resource_id", ""))
                for r in a.get("assigned_resources", [])
            ),
            "pref_violations": int(a.get("pref_violation_count", 0)),
            "severity": _sev(a),
        }
        for a in assignments
    ]

    assignment_section = build_table_section(
        section_id="assignments",
        title="割当一覧",
        columns=[
            TableColumn(key="patient_name", label="患者名"),
            TableColumn(key="day", label="日付"),
            TableColumn(key="weekday", label="曜日"),
            TableColumn(key="time_slot", label="時間帯"),
            TableColumn(key="start_min", label="開始"),
            TableColumn(key="end_min", label="終了"),
            TableColumn(key="resources", label="割当資源"),
            TableColumn(key="pref_violations", label="希望違反数", format="number", align="right"),
        ],
        rows=assignment_rows,
        row_id_key="request_id",
        severity_key="severity",
    )

    # 未割当一覧
    assigned_ids = {a["request_id"] for a in assignments}
    unassigned_rows = []
    if business_dsl:
        for req in business_dsl.get("requests", []):
            req_id = str(req.get("id", ""))
            if req_id not in assigned_ids:
                unassigned_rows.append({
                    "request_id": req_id,
                    "patient_name": str(req.get("patient_name", req_id)),
                    "resource_type_needs": _fmt_type_needs(req.get("resource_type_needs", [])),
                    "duration_min": int(req.get("duration_min", 30)),
                    "severity": "WARNING",
                })

    unassigned_section = build_table_section(
        section_id="unassigned_requests",
        title="未割当受診依頼",
        columns=[
            TableColumn(key="patient_name", label="患者名"),
            TableColumn(key="resource_type_needs", label="必要資源タイプ"),
            TableColumn(key="duration_min", label="所要時間(分)", format="number", align="right"),
        ],
        rows=unassigned_rows,
        row_id_key="request_id",
        severity_key="severity",
    )

    return {
        "domain": "medical_appointment_scheduler",
        "feasible": feasible,
        "summary": {
            "total_requests": total_req,
            "assigned_count": assigned_count,
            "unassigned_count": unassigned_count,
            "coverage_rate": coverage_rate,
            "pref_total_violations": pref_violations,
        },
        "kpi_cards": kpi_cards,
        "assignments": assignments,
        "table_sections": [assignment_section, unassigned_section],
        "alerts": alerts,
        "raw_kpi": kpi,
    }


def _fmt_min(total_min: int) -> str:
    h = total_min // 60
    m = total_min % 60
    return f"{h:02d}:{m:02d}"


def _fmt_type_needs(needs: list) -> str:
    parts = []
    for n in needs:
        if isinstance(n, dict):
            parts.append(f"{n.get('resource_type', '')}×{n.get('count', 1)}")
        else:
            parts.append(str(n))
    return ", ".join(parts)