"""
medical_appointment_sequence_scheduler_ui_converter.py

Solver Output DSL → UI DSL 変換
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional


def convert_medical_appointment_sequence_scheduler_to_ui(
    solver_output: dict,
    business_dsl: Optional[dict] = None,
) -> dict:
    from dsl_transformer.table_sections import build_table_section, TableColumn

    solutions = solver_output.get("solutions", [])
    sol = solutions[0] if solutions else {}

    feasible = sol.get("feasible", False)
    kpi = sol.get("kpi", {})
    scheduled = sol.get("scheduled_sequences", [])
    unscheduled_ids = sol.get("unscheduled_sequence_ids", [])
    resource_loads = sol.get("resource_loads", {})

    total = kpi.get("total_sequences", 0)
    sched_count = kpi.get("scheduled_count", 0)
    coverage = kpi.get("coverage_rate", 0.0)
    max_load = kpi.get("max_resource_load_min", 0)

    # KPIカード
    kpi_cards = [
        {
            "label": "対応系列数",
            "value": sched_count,
            "unit": f"/ {total} 系列",
            "color": "green" if feasible else "red",
        },
        {
            "label": "カバー率",
            "value": round(coverage * 100, 1),
            "unit": "%",
            "color": "blue",
        },
        {
            "label": "最大資源負荷",
            "value": max_load,
            "unit": "分",
            "color": "orange",
        },
        {
            "label": "未対応系列数",
            "value": len(unscheduled_ids),
            "unit": "系列",
            "color": "red" if unscheduled_ids else "muted",
        },
    ]

    # アラート
    alerts = []
    if unscheduled_ids:
        alerts.append({
            "severity": "WARNING",
            "title": f"{len(unscheduled_ids)}件の治療系列を割り当てできませんでした",
            "message": "医療資源の稼働時間、間隔ルール、継続担当制約を確認してください。",
        })

    # 詳細テーブル: スケジュール済み系列一覧
    sequence_rows = []
    for seq in scheduled:
        visits = seq.get("visits", [])
        first_start = min((v["start_min"] for v in visits), default=0)
        last_end = max((v["end_min"] for v in visits), default=0)
        resource_names = list({
            ar["resource_id"]
            for v in visits
            for ar in v.get("assigned_resources", [])
        })
        sequence_rows.append({
            "sequence_id": seq["sequence_id"],
            "patient_name": seq.get("patient_name", seq["sequence_id"]),
            "visit_count": len(visits),
            "total_duration_min": seq.get("total_duration_min", 0),
            "span_min": seq.get("span_min", 0),
            "first_start_min": first_start,
            "last_end_min": last_end,
            "resources_used": ", ".join(resource_names),
        })

    sequence_table = build_table_section(
        section_id="scheduled_sequences",
        title="スケジュール済み治療系列",
        columns=[
            TableColumn(key="patient_name", label="患者名"),
            TableColumn(key="visit_count", label="受診回数", format="number", align="right"),
            TableColumn(key="total_duration_min", label="合計所要時間(分)", format="number", align="right"),
            TableColumn(key="span_min", label="系列全体のスパン(分)", format="number", align="right"),
            TableColumn(key="first_start_min", label="初回開始(分)", format="number", align="right"),
            TableColumn(key="last_end_min", label="最終終了(分)", format="number", align="right"),
            TableColumn(key="resources_used", label="使用資源"),
        ],
        rows=sequence_rows,
        row_id_key="sequence_id",
    )

    # 詳細テーブル: 受診単位の割り当て詳細
    visit_rows = []
    for seq in scheduled:
        for v in seq.get("visits", []):
            for ar in v.get("assigned_resources", []):
                visit_rows.append({
                    "visit_id": v["visit_id"],
                    "patient_name": seq.get("patient_name", seq["sequence_id"]),
                    "visit_order": v.get("visit_order", 0),
                    "resource_id": ar["resource_id"],
                    "resource_type": ar["resource_type"],
                    "start_min": ar["start_min"],
                    "end_min": ar["end_min"],
                    "duration_min": v.get("duration_min", 0),
                })

    visit_table = build_table_section(
        section_id="visit_assignments",
        title="受診割り当て詳細",
        columns=[
            TableColumn(key="patient_name", label="患者名"),
            TableColumn(key="visit_order", label="受診順序", format="number", align="right"),
            TableColumn(key="resource_id", label="割当資源ID"),
            TableColumn(key="resource_type", label="資源タイプ"),
            TableColumn(key="start_min", label="開始(分)", format="number", align="right"),
            TableColumn(key="end_min", label="終了(分)", format="number", align="right"),
            TableColumn(key="duration_min", label="所要時間(分)", format="number", align="right"),
        ],
        rows=visit_rows,
        row_id_key="visit_id",
    )

    # 詳細テーブル: 資源負荷一覧
    load_rows = [
        {
            "resource_id": rid,
            "load_min": load_min,
        }
        for rid, load_min in sorted(resource_loads.items(), key=lambda x: -x[1])
    ]
    load_table = build_table_section(
        section_id="resource_loads",
        title="医療資源負荷",
        columns=[
            TableColumn(key="resource_id", label="資源ID"),
            TableColumn(key="load_min", label="割当合計(分)", format="number", align="right"),
        ],
        rows=load_rows,
        row_id_key="resource_id",
    )

    return {
        "domain": "medical_appointment_sequence_scheduler",
        "feasible": feasible,
        "summary": {
            "total_sequences": total,
            "scheduled_count": sched_count,
            "unscheduled_count": len(unscheduled_ids),
            "coverage_rate": coverage,
            "max_resource_load_min": max_load,
        },
        "kpi_cards": kpi_cards,
        "scheduled_sequences": scheduled,
        "unscheduled_sequence_ids": unscheduled_ids,
        "resource_loads": resource_loads,
        "table_sections": [sequence_table, visit_table, load_table],
        "alerts": alerts,
        "raw_kpi": kpi,
    }