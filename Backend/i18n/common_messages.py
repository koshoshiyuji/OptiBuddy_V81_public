"""
Backend/i18n/common_messages.py — ドメイン非依存の共通メッセージ辞書

対象: issue_rules.py の build_full_unassignment_issue（全件未割当異常検知、
全ドメイン共通）と、app.py の /baseline 系エンドポイントが返す汎用エラー
メッセージ（設定エラー・dsl空・解なしフォールバック）。

【スコープに関する注記】(DESIGN_2026-07-29_nurse_shift_i18n_implementation_spec.md)
domain_generator.py内のCE上限フォールバック生成テンプレート・Stage2設定エラー
テンプレート・シナリオ命名テンプレートは、実地調査の結果NurseShiftWeeklyCapの
実行時パスには到達しない（batch_decomposerのimportは常に成功する／このドメインは
_solve_4dsl_genericのexcept Exceptionハンドラを使う／シナリオ登録は管理画面側の
生成時メタデータで対象外）ことを確認したため、今回の実装対象からは除外している。
"""

from __future__ import annotations

from i18n import make_translator

_JA = {
    "issue.zeroAssignmentAnomaly.title": "全{entity_label}が未割当です（要確認）",
    "issue.zeroAssignmentAnomaly.message": (
        "{total_count}件全ての{entity_label}が未割当になっています。"
        "まず、対応可能なリソース（車両・スキル等）が業務データ上そもそも0件、"
        "といった構造的な制約超過で説明できないかご確認ください。"
        "心当たりがない場合は、ソルバー側にバグがある可能性を疑ってください{hint}。"
    ),
    "issue.zeroAssignmentAnomaly.hint": "（{extra_hint}を確認）",

    "baseline.configErrorGeneric.title": "{problem_class} 設定エラー",
    "baseline.dslEmpty": "dsl が空です。",
    "baseline.configError": "設定エラー",
    "baseline.solveFailed.title": "解なし",
    "baseline.solveFailed.message": "制約設定を確認してください。",
}

_EN = {
    "issue.zeroAssignmentAnomaly.title": "All {entity_label} are unassigned (please verify)",
    "issue.zeroAssignmentAnomaly.message": (
        "All {total_count} {entity_label} are unassigned. "
        "First check whether this is explained by a structural constraint violation — "
        "for example, zero available resources (vehicles, skills, etc.) in the business data "
        "for the required attribute. "
        "If no such explanation applies, suspect a bug on the solver side{hint}."
    ),
    "issue.zeroAssignmentAnomaly.hint": " (check {extra_hint})",

    "baseline.configErrorGeneric.title": "{problem_class} configuration error",
    "baseline.dslEmpty": "dsl is empty.",
    "baseline.configError": "Configuration error",
    "baseline.solveFailed.title": "No solution",
    "baseline.solveFailed.message": "Please review your constraint settings.",
}

t = make_translator(_JA, _EN, "common")
