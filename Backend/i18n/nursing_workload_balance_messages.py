"""
Backend/i18n/nursing_workload_balance_messages.py — NursingWorkloadBalance 専用メッセージ辞書

ヒアリングシート補足「日英バイリンガル対応について」の指示に基づく。
nurse_shift_weekly_cap_messages.py と同じキー命名規則（issue.{rule_id}.title/.message、
kpi.{id}.label/.sub、table.section.{section_id}.title、table.column.{key}、
solver.planLabel）を用いる。

参照元:
  - Backend/solvers/nursing_workload_balance_solver.py
  - Backend/dsl_transformer/nursing_workload_balance_ui_converter.py
"""

from __future__ import annotations

from i18n import make_translator

_JA = {
    # --- solver.py: issues ---
    "issue.no_input.title": "入力データ不足",
    "issue.no_input.message": "看護師または患者データが空です。",

    "issue.infeasible.title": "制約を満たす割り当てが見つかりませんでした",
    "issue.infeasible.message": (
        "患者数・アキュイティ・看護師数・担当患者数上下限・負荷上限の"
        "組み合わせが矛盾しています。条件を見直してください。"
    ),

    "issue.unassigned_patient.title": "未割り当て患者: {name}",
    "issue.unassigned_patient.message": "患者「{name}」（ゾーン: {zone}）に看護師が割り当てられていません。",

    "issue.workload_imbalance.title": "負荷偏在",
    "issue.workload_imbalance.message": (
        "看護師間のアキュイティ合計の差が {diff} あります（最大 {max_a} / 最小 {min_a}）。"
    ),

    # --- ui_converter.py: KPIカード ---
    "kpi.total_patients.label": "割当済み患者数",
    "kpi.total_patients.unit": "件",
    "kpi.workload_std_dev.label": "負荷の標準偏差",
    "kpi.max_acuity.label": "最大アキュイティ合計",
    "kpi.avg_acuity.label": "平均アキュイティ合計",

    # --- ui_converter.py: テーブルセクションタイトル ---
    "table.section.assignments.title": "患者割当一覧",
    "table.section.nurse_workloads.title": "看護師別負荷状況",

    # --- ui_converter.py: テーブル列見出し ---
    "table.column.patient_name":  "患者名",
    "table.column.patient_zone":  "ゾーン",
    "table.column.acuity":        "アキュイティ",
    "table.column.nurse_name":    "看護師名",
    "table.column.nurse_zone":    "ゾーン",
    "table.column.patient_count": "担当患者数",
    "table.column.total_acuity":  "負荷（アキュイティ合計）",

    # --- solver.py: プランラベル ---
    "solver.planLabel": "負荷均等化プラン",
}

_EN = {
    # --- solver.py: issues ---
    "issue.no_input.title": "Missing Input Data",
    "issue.no_input.message": "Nurse or patient data is empty.",

    "issue.infeasible.title": "No Assignment Satisfying All Constraints Was Found",
    "issue.infeasible.message": (
        "The combination of patient count, acuity, nurse count, per-nurse patient "
        "count bounds, and workload cap is contradictory. Please review the conditions."
    ),

    "issue.unassigned_patient.title": "Unassigned Patient: {name}",
    "issue.unassigned_patient.message": "Patient \"{name}\" (zone: {zone}) has not been assigned a nurse.",

    "issue.workload_imbalance.title": "Workload Imbalance",
    "issue.workload_imbalance.message": (
        "The difference in total acuity between nurses is {diff} (max {max_a} / min {min_a})."
    ),

    # --- ui_converter.py: KPI cards ---
    "kpi.total_patients.label": "Assigned Patients",
    "kpi.total_patients.unit": "",
    "kpi.workload_std_dev.label": "Workload Std. Deviation",
    "kpi.max_acuity.label": "Max Total Acuity",
    "kpi.avg_acuity.label": "Avg Total Acuity",

    # --- ui_converter.py: table section titles ---
    "table.section.assignments.title": "Patient Assignments",
    "table.section.nurse_workloads.title": "Nurse Workload Summary",

    # --- ui_converter.py: table column headers ---
    "table.column.patient_name":  "Patient Name",
    "table.column.patient_zone":  "Zone",
    "table.column.acuity":        "Acuity",
    "table.column.nurse_name":    "Nurse Name",
    "table.column.nurse_zone":    "Zone",
    "table.column.patient_count": "Patients",
    "table.column.total_acuity":  "Workload (Total Acuity)",

    # --- solver.py: plan label ---
    "solver.planLabel": "Workload-Balancing Plan",
}

t = make_translator(_JA, _EN, "nursing_workload_balance")
