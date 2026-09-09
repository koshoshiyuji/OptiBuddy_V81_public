
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable, Dict, List, Optional, Tuple

from solvers.base.solution_checker import solver_bug_issue, sweep_peak_usage
from i18n.common_messages import t as _common_t
from i18n.nurse_shift_weekly_cap_messages import t as _nurse_shift_t




# ---------------------------------------------------------------------------
# 共通コア: IssueRule / run_issue_rules
# ---------------------------------------------------------------------------

@dataclass
class IssueRule:
    """
    イシュー検知ルールの定義。

    Attributes:
        rule_id:   ルール識別子 (例: "is_yard")
        condition: (ctx) -> bool。True のとき発火。
        build:     (ctx) -> Dict。イシュー辞書を返す。
                   "id" キーは build 内で確定させること。
    """
    rule_id:   str
    condition: Callable[[Dict[str, Any]], bool]
    build:     Callable[[Dict[str, Any]], Dict[str, Any]]



def run_issue_rules(
    domain: str,
    contexts: List[Dict[str, Any]],
    issue_statuses: Dict[str, str],
) -> List[Dict[str, Any]]:
    """
    ルールカタログを contexts に対して実行し、イシューリストを返す。

    Args:
        domain:         "YardPlanning" や "NurseShiftWeeklyCap" など（ISSUE_RULESのキー）
        contexts:       各ルールに渡す context 辞書のリスト。
                        1つの context は1つのルール呼び出し単位
                        (コンテナペア、タスク1件、スタッフ1人 など)。
                        context に "_rule_id" キーを入れて対象ルールを指定する。
        issue_statuses: {issue_id: "ACCEPTED"} の辞書。ACCEPTEDのものはスキップ。

    Returns:
        List[Dict]: イシュー辞書のリスト (重複なし、ACCEPTED除外済み)
    """
    rules_by_id: Dict[str, IssueRule] = {
        r.rule_id: r for r in ISSUE_RULES.get(domain, [])
    }
    seen: Dict[str, Dict] = {}  # issue_id -> issue_dict (重複排除)

    for ctx in contexts:
        rule_id = ctx.get("_rule_id", "")
        rule = rules_by_id.get(rule_id)
        if rule is None:
            continue
        try:
            if not rule.condition(ctx):
                continue
            issue = rule.build(ctx)
            iid = issue.get("id", "")
            if not iid:
                continue
            if issue_statuses.get(iid) == "ACCEPTED":
                continue
            if iid not in seen:
                seen[iid] = issue
        except Exception:
            # 個別ルールの例外は握りつぶしてログに留める（他ルールを止めない）
            import logging
            logging.getLogger(__name__).warning(
                f"[IssueRule] rule_id={rule_id} で例外が発生しました。", exc_info=True
            )

    return list(seen.values())



# ---------------------------------------------------------------------------
# ルール定義ヘルパー
# ---------------------------------------------------------------------------

def _rule(rule_id: str, condition, build) -> IssueRule:
    return IssueRule(rule_id=rule_id, condition=condition, build=build)



# ---------------------------------------------------------------------------
# 共通ヘルパー: 全件未割当異常検知
# ---------------------------------------------------------------------------
#
# ドメインに依存しないチェック。
# 入力アイテムが1件以上あるにもかかわらず、解抽出後の割当済み件数が
# ゼロ（= 全件未割当）の場合に警告する。
#
# 実際に production_lot_scheduler で発生した不具合（presence_of()+get_value()
# の docplex API 誤用により、CP Optimizer内部では最適解（全件割当済み）が見つかっているのに
# 解抽出コードが毎回 KeyError で失敗し、結果的に「解けているのに全件未割当」になる）
# を検知するために追加。
#
# 【2026-08-06追記】ただし「全件未割当＝ほぼ確実に抽出バグ」と断定する文言は誤検知を生む
# ことが判明（PortfolioOverlapDesigner・WorkerLoadBalancer・PatientTransportPlannerで確認）。
# 全リクエストが共通の理由（例: 必要なcare_typeに対応する車両がフリートに1台もない等）で
# 構造的に0件対応が数学的に正しい最適解になるケースが実際にあり、この場合バグではない。
# そのためメッセージは「まず業務データ側の構造的原因を確認→なければバグを疑う」の順に
# 変更済み（Backend/i18n/common_messages.py の issue.zeroAssignmentAnomaly.*）。
# 検知条件（assigned_count==0 かつ total_count>0）自体は変更していない。

def build_full_unassignment_issue(
    assigned_count: int,
    total_count:    int,
    entity_label:   str = "アイテム",
    extra_hint:      str = "",
) -> Optional[Dict[str, Any]]:
    """
    全件未割当異常を検知し、該当すれば issue 辞書を返す。
    該当しなければ None。

    使い方（各ソルバーの _detect_issues 内）:
        issue = build_full_unassignment_issue(
            assigned_count=len(assignments), total_count=len(lots),
            entity_label="ロット", extra_hint="presence_of/get_var_solution等の解抽出処理",
        )
        if issue:
            issues.insert(0, issue)
    """
    if total_count <= 0 or assigned_count > 0:
        return None
    # 2026-07-30 i18n対応: title/messageはBackend/i18n/common_messages.py経由の
    # 辞書引き+テンプレート復元に変更（DESIGN_2026-07-29_dynamic_message_i18n_key_design.md）。
    # entity_labelは呼び出し側（各ドメインのソルバー）が渡す語で、現状は各ドメイン
    # ごとに日本語のまま（NurseShiftWeeklyCapのみ i18n.nurse_shift_weekly_cap_messages
    # 経由で英語化済みの値が渡ってくる）。
    hint = _common_t("issue.zeroAssignmentAnomaly.hint", extra_hint=extra_hint) if extra_hint else ""
    return {
        "id":       "zero_assignment_anomaly",
        "severity": "CRITICAL",
        "category": "SOLVER",
        "title":    _common_t("issue.zeroAssignmentAnomaly.title", entity_label=entity_label),
        "message":  _common_t(
            "issue.zeroAssignmentAnomaly.message",
            total_count=total_count, entity_label=entity_label, hint=hint,
        ),
        "relatedContainerIds": [],
    }
