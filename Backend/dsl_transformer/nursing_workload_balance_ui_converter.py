"""
Backend/dsl_transformer/nursing_workload_balance_ui_converter.py

Solver Output DSL → UI DSL 変換 (NursingWorkloadBalance)

問題の本質: 患者 → 看護師 の静的1対1割り当て・負荷分散問題。
solver（nursing_workload_balance_solver.py）が返す solution の実際のキーは
assignments / nurse_workloads / metrics であり、シフトスケジューリング系の
tasks / task_summary / staff_hours ではない点に注意（旧ドメイン
NurseShiftWeeklyCap由来の雛形をそのまま流用すると不整合になる）。
"""

from typing import Any, Dict, List, Optional

from i18n.nursing_workload_balance_messages import t


def convert_nursing_workload_balance_to_ui(
    solver_output: dict,
    business_dsl: Optional[dict] = None,
) -> dict:
    from dsl_transformer.table_sections import build_table_section, TableColumn

    feasible: bool = solver_output.get("feasible", False)
    solutions: List[Dict] = solver_output.get("solutions", [])
    issues: List[Dict] = solver_output.get("issues", [])

    solution = solutions[0] if solutions else {}
    assignments: List[Dict] = solution.get("assignments", [])
    nurse_workloads: List[Dict] = solution.get("nurse_workloads", [])
    metrics: Dict = solution.get("metrics", {})

    # ── KPI カード
    kpi_cards = []
    if feasible and metrics:
        kpi_cards = [
            {
                "label": t("kpi.total_patients.label"),
                "value": str(metrics.get("total_patients", 0)),
                "unit": t("kpi.total_patients.unit"),
                "color": "green",
            },
            {
                "label": t("kpi.workload_std_dev.label"),
                "value": str(metrics.get("workload_std_dev", 0)),
                "unit": "",
                "color": "blue",
            },
            {
                "label": t("kpi.max_acuity.label"),
                "value": str(metrics.get("max_acuity", 0)),
                "unit": "",
                "color": "orange",
            },
            {
                "label": t("kpi.avg_acuity.label"),
                "value": str(metrics.get("avg_acuity", 0)),
                "unit": "",
                "color": "teal",
            },
        ]

    # ── アラート
    alerts = [
        {
            "id":       i.get("id", ""),
            "severity": i.get("severity", "INFO"),
            "title":    i.get("title", ""),
            "message":  i.get("message", ""),
        }
        for i in issues
        if i.get("severity") in ("CRITICAL", "WARNING")
    ]

    # ── 患者割当一覧テーブル
    assignment_section = build_table_section(
        section_id="assignments",
        title=t("table.section.assignments.title"),
        columns=[
            TableColumn(key="patient_name", label=t("table.column.patient_name")),
            TableColumn(key="patient_zone", label=t("table.column.patient_zone")),
            TableColumn(key="acuity",       label=t("table.column.acuity"), format="number", align="right"),
            TableColumn(key="nurse_name",   label=t("table.column.nurse_name")),
            TableColumn(key="nurse_zone",   label=t("table.column.nurse_zone")),
        ],
        rows=assignments,
        row_id_key="patient_id",
    )

    # ── 看護師別負荷状況テーブル
    nurse_section = build_table_section(
        section_id="nurse_workloads",
        title=t("table.section.nurse_workloads.title"),
        columns=[
            TableColumn(key="nurse_name",     label=t("table.column.nurse_name")),
            TableColumn(key="nurse_zone",     label=t("table.column.nurse_zone")),
            TableColumn(key="patient_count",  label=t("table.column.patient_count"), format="number", align="right"),
            TableColumn(key="total_acuity",   label=t("table.column.total_acuity"), format="number", align="right"),
        ],
        rows=nurse_workloads,
        row_id_key="nurse_id",
    )

    return {
        "domain":         "nursing_workload_balance",
        "feasible":       feasible,
        "summary": {
            "total_patients":   metrics.get("total_patients", 0),
            "workload_std_dev": metrics.get("workload_std_dev", 0),
            "max_acuity":       metrics.get("max_acuity", 0),
            "avg_acuity":       metrics.get("avg_acuity", 0),
        },
        "kpi_cards":      kpi_cards,
        "assignments":    assignments,
        "nurse_workloads": nurse_workloads,
        "table_sections": [assignment_section, nurse_section],
        "alerts":         alerts,
        "raw_kpi":        metrics,
    }
