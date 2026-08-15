"""
Backend/i18n/nurse_shift_weekly_cap_messages.py — NurseShiftWeeklyCap 専用メッセージ辞書

DESIGN_2026-07-29_nurse_shift_i18n_implementation_spec.md の完全インベントリ
（全58件）のうち、本ドメイン固有分を収録する。キーは既存の安定識別子
（issue_rules.py の rule_id / table_sections.py の TableColumn.key /
kpi_cards の id / section_id）をそのまま流用している。

参照元:
  - Backend/solvers/base/issue_rules.py（_NURSE_SHIFT_RULES）
  - Backend/dsl_transformer/nurse_shift_weekly_cap_ui_converter.py
  - Backend/solvers/nurse_shift_weekly_cap_solver.py
  - Backend/solvers/nurse_shift_weekly_cap_batch_decomposer.py
"""

from __future__ import annotations

from typing import Dict

from i18n import make_translator
from i18n.context import get_lang

_JA = {
    # --- issue_rules.py: _NURSE_SHIFT_RULES（6ルール・13件）---
    "issue.solve_failed.title": "実行可能解が見つかりませんでした",
    "issue.solve_failed.message": "現在の制約（人員・資格・休憩・週次夜勤上限など）をすべて満たすシフトが存在しません。",

    "issue.understaffed.title": "人員不足: {tid}",
    "issue.understaffed.message": "タスク {tid} は必要人数 {required_count} 名に対し {assigned} 名しか割り当てられていません。",

    "issue.no_chief.title": "責任者不足: {tid}",
    "issue.no_chief.message": "タスク {tid} は責任者(CHIEF)が最低 {min_chiefs} 名必要ですが、{chiefs} 名しか配置されていません。",

    "issue.unassigned_staff.title": "未割り当て: {name}",
    "issue.unassigned_staff.message": "{name} さんは今回のシフトに一件も割り当てられていません。",

    "issue.night_rest_violation.title": "夜勤後休憩不足: {name}",
    "issue.night_rest_violation.message": (
        "{name} さんは夜勤({night_task_id})後、次の勤務({next_task_id})まで "
        "{rest_actual} 分しか空いていません（最低 {min_rest} 分必要）。"
    ),

    "issue.consecutive_night_violation.title": "連続夜勤超過: {name}",
    "issue.consecutive_night_violation.message": (
        "{name} さんは {consecutive_count} 日連続で夜勤に割り当てられています"
        "（上限 {max_consecutive} 日）。対象日: {night_days}"
    ),

    # --- ui_converter.py: KPIカード（id基準）---
    "kpi.total_cost.label": "総人件費",
    "kpi.total_cost.sub": "割り当て済みスタッフの合計",
    "kpi.assigned_count.label": "割り当て件数",
    "kpi.assigned_count.sub": "スタッフ×タスク",
    "kpi.coverage_rate.label": "タスクカバー率",
    "kpi.coverage_rate.sub": "不足タスク数: {understaffed_count}",
    "kpi.preference_rate.label": "希望充足率",
    "kpi.preference_rate.sub": "未充足: {unmet_preference_count}件",
    "kpi.solve_time.label": "ソルブ時間",

    # --- ui_converter.py: テーブルセクションタイトル（section_id基準）---
    "table.section.assignments.title": "割り当て一覧",
    "table.section.task_coverage.title": "タスク別充足状況",
    "table.section.staff_summary.title": "スタッフ別稼働サマリ",

    # --- ui_converter.py: 列ラベル（TableColumn.key基準、セクション横断でユニーク）---
    "table.column.day": "日付",
    "table.column.task_id": "タスクID",
    "table.column.staff_name": "スタッフ名",
    "table.column.grade": "グレード",
    "table.column.time_range": "時間帯",
    "table.column.duration_h": "時間数",
    "table.column.night_shift": "シフト種別",
    "table.column.cost": "人件費",
    "table.column.task_name": "タスク名",
    "table.column.is_night": "種別",
    "table.column.required_count": "必要人数",
    "table.column.assigned_count": "割当人数",
    "table.column.chief_count": "うちCHIEF",
    "table.column.shortage": "不足数",
    "table.column.status": "状態",
    "table.column.total_tasks": "担当タスク数",
    "table.column.night_tasks": "うち夜勤",
    "table.column.total_hours": "合計時間",
    "table.column.total_cost": "人件費",

    # --- ui_converter.py: extra_metrics（動的列見出し）---
    "table.extraMetric.rollingNightCount": "直近7日間の夜勤回数（最大）",

    # --- solver.py / batch_decomposer.py ---
    "solver.planLabel": "人件費最小化プラン",
    "solver.planLabelBatch": "人件費最小化プラン（対象人数が多いため、グループに分けて計算）",
    "solver.assignmentCandidateLabel": "割り当て候補",
}

_EN = {
    "issue.solve_failed.title": "No feasible solution found",
    "issue.solve_failed.message": (
        "No shift schedule satisfies all current constraints "
        "(staffing, qualifications, rest requirements, weekly night-shift cap, etc.)."
    ),

    "issue.understaffed.title": "Understaffed: {tid}",
    "issue.understaffed.message": "Task {tid} requires {required_count} staff but only {assigned} are assigned.",

    "issue.no_chief.title": "Missing chief: {tid}",
    "issue.no_chief.message": "Task {tid} requires at least {min_chiefs} chief(s), but only {chiefs} are assigned.",

    "issue.unassigned_staff.title": "Unassigned: {name}",
    "issue.unassigned_staff.message": "{name} has no assignments in this shift schedule.",

    "issue.night_rest_violation.title": "Insufficient post-night-shift rest: {name}",
    "issue.night_rest_violation.message": (
        "{name} has only {rest_actual} minutes between the night shift ({night_task_id}) "
        "and the next shift ({next_task_id}) (minimum {min_rest} minutes required)."
    ),

    "issue.consecutive_night_violation.title": "Excessive consecutive night shifts: {name}",
    "issue.consecutive_night_violation.message": (
        "{name} is assigned {consecutive_count} consecutive night shifts "
        "(limit: {max_consecutive} days). Affected days: {night_days}"
    ),

    "kpi.total_cost.label": "Total Labor Cost",
    "kpi.total_cost.sub": "Sum across assigned staff",
    "kpi.assigned_count.label": "Assignments",
    "kpi.assigned_count.sub": "Staff x Task",
    "kpi.coverage_rate.label": "Task Coverage Rate",
    "kpi.coverage_rate.sub": "Understaffed tasks: {understaffed_count}",
    "kpi.preference_rate.label": "Preference Satisfaction",
    "kpi.preference_rate.sub": "Unmet: {unmet_preference_count}",
    "kpi.solve_time.label": "Solve Time",

    "table.section.assignments.title": "Assignments",
    "table.section.task_coverage.title": "Task Coverage",
    "table.section.staff_summary.title": "Staff Summary",

    "table.column.day": "Day",
    "table.column.task_id": "Task ID",
    "table.column.staff_name": "Staff Name",
    "table.column.grade": "Grade",
    "table.column.time_range": "Time Range",
    "table.column.duration_h": "Hours",
    "table.column.night_shift": "Shift Type",
    "table.column.cost": "Cost",
    "table.column.task_name": "Task Name",
    "table.column.is_night": "Type",
    "table.column.required_count": "Required",
    "table.column.assigned_count": "Assigned",
    "table.column.chief_count": "Chiefs",
    "table.column.shortage": "Shortage",
    "table.column.status": "Status",
    "table.column.total_tasks": "Tasks",
    "table.column.night_tasks": "Night Shifts",
    "table.column.total_hours": "Total Hours",
    "table.column.total_cost": "Total Cost",

    "table.extraMetric.rollingNightCount": "Rolling 7-Day Night Count (Max)",

    "solver.planLabel": "Cost-Minimizing Plan",
    "solver.planLabelBatch": "Cost-Minimizing Plan (large group, solved in batches)",
    "solver.assignmentCandidateLabel": "assignment candidates",
}

t = make_translator(_JA, _EN, "nurse_shift_weekly_cap")


# ---------------------------------------------------------------------------
# 登録済みシナリオのname/description対訳（2026-07-30追加）
#
# 背景: Home画面に並ぶ登録済みシナリオのname/descriptionは、DBの`scenarios`
# テーブルに生の日本語文字列として保存されている（domain_generator.pyの
# シナリオ登録パイプライン、またはKoshoshiによる手動登録由来）。DSL由来の
# ユーザー自由記述（新規ドメイン登録時のヒアリング内容等）は当初
# 「翻訳対象外」としていたが、実際にはKoshoshi自身がデモ用に作成した
# NurseShiftWeeklyCapの登録済みシナリオ3件は事実上プロダクト側のコンテンツ
# であり、対訳を用意する価値があると判断し追加した。
#
# 方式: DBスキーマ変更（name_en/description_en列追加）は行わず、既存の
# 日本語name文字列をキーにした対訳辞書で解決する（他の辞書と同じ
# フォールバック方針: 未登録の場合は日本語原文のまま返す）。将来
# シナリオが再登録されてnameが変わった場合は対訳が外れて日本語表示に
# 自動的にフォールバックするだけで、エラーにはならない。
# ---------------------------------------------------------------------------

_SCENARIO_META_EN: Dict[str, Dict[str, str]] = {
    "看護師シフト最適化（週単位夜勤回数上限拡張） — 標準": {
        "name": "Nurse Shift Optimization (Weekly Night-Shift Cap) — Baseline",
        "description": (
            "Adds a new constraint to an existing hospital nurse shift schedule: a per-staff cap on "
            "the number of night shifts within any rolling 7-day window. The cap value can be set "
            "individually per staff attribute. The constraint is relaxed for an incomplete trailing "
            "7-day period at the end of the horizon. (Baseline)\n\n"
            "[Added 2026-07-13] Fixed a bug where the chief (CHIEF) requirement was silently disabled "
            "for all tasks because no staff had CHIEF grade. A senior staff member was promoted to "
            "CHIEF, and the chief requirement was changed to apply only to night shifts (not day/evening "
            "shifts). The solver-side guard was also fixed so that a requirement with zero CHIEF "
            "candidates is now explicitly marked infeasible instead of being silently ignored."
        ),
    },
    "看護師シフト最適化（週単位夜勤回数上限拡張） — 責任者不足（解なし）": {
        "name": "Nurse Shift Optimization (Weekly Night-Shift Cap) — Chief Shortage (Infeasible)",
        "description": (
            "A demo scenario that becomes infeasible due to a chief (CHIEF) shortage. Each night shift "
            "still requires at least 1 chief (same rule as the baseline scenario), but only one staff "
            "member (Head Nurse Tanaka) holds CHIEF grade. There are 5 night-shift slots over 5 days, "
            "but this individual's weekly night-shift cap is 3 per rolling 7-day window, so at most 3 "
            "of the 5 slots can be covered — the remaining slots have no chief available. "
            "required_count can be satisfied, but min_chiefs>=1 cannot, so the whole model becomes "
            "infeasible. Comparing this against the baseline scenario (3 CHIEFs) demonstrates how "
            "adding staff resolves a chief-coverage shortfall."
        ),
    },
    "NurseShiftWeeklyCap — INRC-II n021w4": {
        "name": "NurseShiftWeeklyCap — INRC-II n021w4",
        "description": (
            "The official INRC-II benchmark dataset n021w4 (21 nurses, 1 week; Sc-n021w4.txt + "
            "WD-n021w4-0.txt), converted via inrc_to_business_dsl_nurse_shift_weekly_cap.py. An "
            "external benchmark instance used for Gate 2 regression testing. Constraints that could "
            "not be converted are documented in nurse_shift_weekly_cap_inrc_n021w4_report.json."
        ),
    },
}


def translate_scenario_meta(name: str, description: str) -> Dict[str, str]:
    """
    登録済みシナリオのname/descriptionを現在言語に合わせて解決する。

    Args:
        name:        DBに保存された日本語のシナリオ名（対訳辞書のキー）
        description: DBに保存された日本語の説明文

    Returns:
        {"name": ..., "description": ...}
        対訳が無い場合、または現在言語が日本語の場合は入力をそのまま返す
        （フォールバック方針は他のi18n辞書と同じ）。
    """
    if get_lang() != "en":
        return {"name": name, "description": description}
    meta = _SCENARIO_META_EN.get(name)
    if meta is None:
        return {"name": name, "description": description}
    return {"name": meta["name"], "description": meta["description"]}
