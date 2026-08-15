"""
Backend/i18n/production_line_sequencing_messages.py

ProductionLineSequencing ドメインのi18nメッセージ辞書
"""

from __future__ import annotations
from i18n import make_translator

_JA = {
    # issue
    "issue.no_input.title": "入力データ不足",
    "issue.no_input.message": "バッチデータが空です。",
    "issue.no_slots.title": "スロットデータ不足",
    "issue.no_slots.message": "スロットデータが空です。",
    "issue.solve_failed.title": "制約を満たす割り当てが見つかりませんでした",
    "issue.solve_failed.message": "全バッチをスロットに割り当てる解が存在しません。バッチ数・スロット数・制約条件を見直してください。",
    "issue.unassigned_batch.title": "未割当バッチ: {name}",
    "issue.unassigned_batch.message": "バッチ {name} がいずれのスロットにも割り当てられませんでした。",
    "issue.distribution_violation.title": "分散ルール違反: 日{day} 車種{vtid}",
    "issue.distribution_violation.message": "日{day}の車種{vtid}が{count}バッチ割り当てられており、上限{limit}を超えています。",
    # KPI
    "kpi.feasibility.label": "実行可能性",
    "kpi.feasibility.infeasible": "解なし",
    "kpi.assigned_batches.label": "割当済バッチ数",
    "kpi.assigned_batches.unit": "バッチ",
    "kpi.balance_score.label": "負荷偏差（合計）",
    "kpi.balance_score.unit": "スロット差",
    "kpi.balance_score.sub": "小さいほど平準化されています",
    "kpi.line_usage.label": "ライン {line_id} 使用数",
    "kpi.line_usage.unit": "スロット",
    # テーブル
    "table.section.assignment_detail.title": "割当一覧",
    "table.column.batch_name": "バッチ名",
    "table.column.vehicle_type": "車種",
    "table.column.line_name": "割当ライン",
    "table.column.day": "生産日",
    "table.column.start_hour": "開始時刻(時)",
    # ソルバー
    "solver.planLabel": "最適化プラン",
}

_EN = {
    # issue
    "issue.no_input.title": "Insufficient input data",
    "issue.no_input.message": "Batch data is empty.",
    "issue.no_slots.title": "Insufficient slot data",
    "issue.no_slots.message": "Slot data is empty.",
    "issue.solve_failed.title": "No feasible assignment found",
    "issue.solve_failed.message": "No solution exists that assigns all batches to slots. Please review batch count, slot count, and constraints.",
    "issue.unassigned_batch.title": "Unassigned batch: {name}",
    "issue.unassigned_batch.message": "Batch {name} could not be assigned to any slot.",
    "issue.distribution_violation.title": "Distribution violation: Day {day}, Type {vtid}",
    "issue.distribution_violation.message": "On day {day}, vehicle type {vtid} has {count} batches assigned, exceeding the limit of {limit}.",
    # KPI
    "kpi.feasibility.label": "Feasibility",
    "kpi.feasibility.infeasible": "No solution",
    "kpi.assigned_batches.label": "Assigned Batches",
    "kpi.assigned_batches.unit": "batches",
    "kpi.balance_score.label": "Load Imbalance (total)",
    "kpi.balance_score.unit": "slot diff",
    "kpi.balance_score.sub": "Lower is more balanced",
    "kpi.line_usage.label": "Line {line_id} usage",
    "kpi.line_usage.unit": "slots",
    # table
    "table.section.assignment_detail.title": "Assignment Detail",
    "table.column.batch_name": "Batch Name",
    "table.column.vehicle_type": "Vehicle Type",
    "table.column.line_name": "Assigned Line",
    "table.column.day": "Production Day",
    "table.column.start_hour": "Start Hour",
    # solver
    "solver.planLabel": "Optimized Plan",
}

t = make_translator(_JA, _EN, "production_line_sequencing")