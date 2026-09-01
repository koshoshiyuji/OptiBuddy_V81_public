"""
issue_rules.py — 層B: イシュー検知ルールカタログ
==================================================

【設計方針】
- 各ルールは「条件関数」と「イシュー生成関数」のペアで定義。
- condition(ctx) -> bool : True のとき発火
- build(ctx)    -> Dict  : イシュー辞書を返す
- ctx (context 辞書) にドメイン固有データを注入する。

【層の境界】
- 層A (共通コア): run_issue_rules() エンジン、IssueRule dataclass
- 層B (レシピ): ISSUE_RULES 宣言（ドメイン別ルールリスト）
- 層C (ドメイン固有): なし (context 経由で注入)

【エントリポイント】
  from solvers.base.issue_rules import run_issue_rules

  issues = run_issue_rules(
      domain         = "YardPlanning",   # or "NurseShiftWeeklyCap" など（ISSUE_RULESのキー）
      contexts       = build_yard_contexts(solver_input, solution_data),
      issue_statuses = solver_input.get("issue_statuses", {}),
  )

【context キー一覧】

  --- YardPlanning ---
  ペアルール (is_yard / weight_yard / is_ship / weight_ship):
    - cid_a, cid_b: str
    - c_a, c_b: Dict (コンテナ辞書)
    - pair_id: str

  ATTRルール (attr_reefer / attr_imo / attr_oog):
    - cid: str
    - c: Dict
    - bay, row, tier: Any
    - reefer_ids, imo_ids, oog_ids: Set[str]
    - reefer_slot_set, imo_slot_set, oog_excluded_slots: Set
    - all_containers: List[Dict]  (OOGソース特定用)

  シフトルール (shift_violation / shift_break):
    - t: Dict (タスク結果)
    - crane_id: str
    - crane_windows: Dict[str, List[Tuple]]
    - break_windows: Dict[str, List[Tuple]]

  フェーズルール (discharge_before_load):
    - lt: Dict (LOADタスク結果)
    - max_d_end: int

  --- NurseShift/NurseShiftWeeklyCap（旧EventStaffing由来の契約を継承。下記407行目のコメント参照）---
  solve_failed:
    - solution_data: Optional[Dict]  (None のとき発火)

  understaffed / no_chief:
    - tid: str
    - req: Dict {required_count, min_chiefs, assigned, chiefs}

  unassigned_staff:
    - sid: str
    - staff: Dict
    - staff_ids_with_assignment: Set[str]

  pref_unmet:
    - sid: str
    - staff: Dict
    - assigned_task_ids: Set[str]
    - existing_issue_ids: Set[str]  (同一SIDで重複しないよう管理)

  high_wait:
    - sid: str
    - staff: Dict
    - bind_min: int
    - work_min: int
"""

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


# ---------------------------------------------------------------------------
# YardPlanning ルール
# ---------------------------------------------------------------------------

_YARD_RULES: List[IssueRule] = [

    # --- ペアルール ---

    _rule(
        "is_yard",
        condition=lambda ctx: (
            ctx["y_a"].get("bay") == ctx["y_b"].get("bay")
            and ctx["y_a"].get("row") == ctx["y_b"].get("row")
            and ctx["y_a"].get("bay") not in (None, 0)
            and ctx["o_u"] is not None and ctx["o_l"] is not None
            and ctx["o_u"] > ctx["o_l"]
        ),
        build=lambda ctx: {
            "id":                   f"is_yard_{ctx['lower_id']}_{ctx['upper_id']}",
            "severity":             "WARNING",
            "category":             "YARD",
            "title":                "リハンドリングが発生します",
            "message":              f"上段({ctx['upper_id']})が邪魔で先に積むべき下段({ctx['lower_id']})を取り出せません。",
            "containerId":          ctx["lower_id"],
            "relatedContainerIds":  [ctx["lower_id"], ctx["upper_id"]],
        },
    ),

    _rule(
        "weight_yard",
        condition=lambda ctx: (
            ctx["y_a"].get("bay") == ctx["y_b"].get("bay")
            and ctx["y_a"].get("row") == ctx["y_b"].get("row")
            and ctx["y_a"].get("bay") not in (None, 0)
            and ctx["w_u"] > ctx["w_l"]
        ),
        build=lambda ctx: {
            "id":                   f"weight-yard-{ctx['pair_id']}",
            "severity":             "WARNING",
            "category":             "WEIGHT",
            "title":                f"Weight Warning: {ctx['upper_id']}({ctx['w_u']}t) が {ctx['lower_id']}({ctx['w_l']}t) の上",
            "message":              f"重量の重い {ctx['upper_id']}({ctx['w_u']}t) が軽い {ctx['lower_id']}({ctx['w_l']}t) の上に積まれています。",
            "containerId":          ctx["upper_id"],
            "relatedContainerIds":  [ctx["upper_id"], ctx["lower_id"]],
        },
    ),

    _rule(
        "is_ship",
        condition=lambda ctx: (
            ctx["v_a"].get("bay") == ctx["v_b"].get("bay")
            and ctx["v_a"].get("row") == ctx["v_b"].get("row")
            and ctx["v_a"].get("bay") not in (None, 0)
            and ctx["o_u"] is not None and ctx["o_l"] is not None
            and ctx["o_u"] < ctx["o_l"]
        ),
        build=lambda ctx: {
            "id":                   f"is_ship_{ctx['lower_id']}_{ctx['upper_id']}",
            "severity":             "CRITICAL",
            "category":             "MBP",
            "title":                "Master Bay Plan 積み順矛盾",
            "message":              "Master Bay Planに積み順矛盾があります。船社への確認が必要です。",
            "containerId":          ctx["lower_id"],
            "relatedContainerIds":  [ctx["lower_id"], ctx["upper_id"]],
        },
    ),

    _rule(
        "weight_ship",
        condition=lambda ctx: (
            ctx["v_a"].get("bay") == ctx["v_b"].get("bay")
            and ctx["v_a"].get("row") == ctx["v_b"].get("row")
            and ctx["v_a"].get("bay") not in (None, 0)
            and ctx["w_u"] > ctx["w_l"]
        ),
        build=lambda ctx: {
            "id":                   f"weight-ship-{ctx['pair_id']}",
            "severity":             "WARNING",
            "category":             "WEIGHT",
            "title":                f"Weight Warning: {ctx['upper_id']}({ctx['w_u']}t) が {ctx['lower_id']}({ctx['w_l']}t) の上（船側）",
            "message":              f"重量の重い {ctx['upper_id']}({ctx['w_u']}t) が軽い {ctx['lower_id']}({ctx['w_l']}t) の上に積まれています。",
            "containerId":          ctx["upper_id"],
            "relatedContainerIds":  [ctx["upper_id"], ctx["lower_id"]],
        },
    ),

    # --- ATTRルール ---

    _rule(
        "attr_reefer",
        condition=lambda ctx: (
            ctx["bay"] is not None and ctx["row"] is not None
            and ctx["cid"] in ctx["reefer_ids"]
            and (ctx["bay"], ctx["row"], ctx["tier"]) not in ctx["reefer_slot_set"]
            and (ctx["bay"], ctx["row"], None)         not in ctx["reefer_slot_set"]
        ),
        build=lambda ctx: {
            "id":                   f"attr_reefer_{ctx['cid']}",
            "severity":             "CRITICAL",
            "category":             "ATTR",
            "title":                f"Reefer配置違反: {ctx['cid']}",
            "message":              f"{ctx['cid']} がReeferスロット以外(bay={ctx['bay']},row={ctx['row']},tier={ctx['tier']})に配置されています。",
            "containerId":          ctx["cid"],
            "relatedContainerIds":  [ctx["cid"]],
        },
    ),

    _rule(
        "attr_imo",
        condition=lambda ctx: (
            ctx["bay"] is not None and ctx["row"] is not None
            and ctx["cid"] in ctx["imo_ids"]
            and (ctx["bay"], ctx["row"]) not in ctx["imo_slot_set"]
        ),
        build=lambda ctx: {
            "id":                   f"attr_imo_{ctx['cid']}",
            "severity":             "CRITICAL",
            "category":             "ATTR",
            "title":                f"IMO危険物配置違反: {ctx['cid']}",
            "message":              f"{ctx['cid']} がIMO指定区画外(bay={ctx['bay']},row={ctx['row']})に配置されています。",
            "containerId":          ctx["cid"],
            "relatedContainerIds":  [ctx["cid"]],
        },
    ),

    # --- シフトルール ---

    _rule(
        "shift_violation",
        condition=lambda ctx: (
            bool(ctx.get("crane_windows", {}).get(ctx["crane_id"]))
            and not any(
                ws <= ctx["t_start"] and ctx["t_end"] <= we
                for ws, we in ctx["crane_windows"][ctx["crane_id"]]
            )
        ),
        build=lambda ctx: {
            "id":                   f"shift_violation_{ctx['tid']}",
            "severity":             "WARNING",
            "category":             "SHIFT",
            "title":                f"シフト外作業: {ctx['cid']} ({ctx['crane_id']})",
            "message":              f"{ctx['crane_id']} の稼働時間外({ctx['t_start']}〜{ctx['t_end']}分)にタスクが配置されています。",
            "containerId":          ctx["cid"],
            "relatedContainerIds":  [ctx["cid"]],
        },
    ),

    _rule(
        "shift_break",
        condition=lambda ctx: any(
            ctx["t_start"] < b_end and ctx["t_end"] > b_start
            for b_start, b_end in ctx.get("break_windows", {}).get(ctx["crane_id"], [])
        ),
        build=lambda ctx: {
            "id":                   f"shift_break_{ctx['tid']}",
            "severity":             "WARNING",
            "category":             "SHIFT",
            "title":                f"休憩時間跨ぎ: {ctx['cid']} ({ctx['crane_id']})",
            "message":              (
                lambda brk=next((
                    (b_start, b_end)
                    for b_start, b_end in ctx.get("break_windows", {}).get(ctx["crane_id"], [])
                    if ctx["t_start"] < b_end and ctx["t_end"] > b_start
                ), (0, 0)):
                f"{ctx['crane_id']} の休憩時間({brk[0]}〜{brk[1]}分)にタスクが跨がっています。"
            )(),
            "containerId":          ctx["cid"],
            "relatedContainerIds":  [ctx["cid"]],
        },
    ),

    # --- フェーズルール ---

    _rule(
        "discharge_before_load",
        condition=lambda ctx: ctx["lt"]["start"] < ctx["max_d_end"],
        build=lambda ctx: {
            "id":                   f"discharge_before_load_{ctx['lt']['containerId']}",
            "severity":             "CRITICAL",
            "category":             "OPERATION",
            "title":                f"DISCHARGE完了前のLOAD開始: {ctx['lt']['containerId']}",
            "message":              f"{ctx['lt']['containerId']} のLOADがDISCHARGE完了前({ctx['max_d_end']}秒)に開始({ctx['lt']['start']}秒)。",
            "containerId":          ctx["lt"]["containerId"],
            "relatedContainerIds":  [ctx["lt"]["containerId"]],
        },
    ),
]


# ---------------------------------------------------------------------------
# _NURSE_SHIFT_RULES（2026-07-12 追加）
#
# 背景: nurse_shift_solver.py / nurse_shift_weekly_cap_solver.py の
# _build_hospital_contexts() は本ファイル冒頭の
# "--- NurseShift/NurseShiftWeeklyCap（旧EventStaffing由来の契約を継承）---"
# として文書化されている契約（solve_failed / understaffed / no_chief /
# unassigned_staff、および看護ドメイン独自追加の night_rest_violation /
# consecutive_night_violation）でcontextを組み立てているが、実体である
# ルールリストは2026-07-11のEventStaffingドメイン削除時に
# ISSUE_RULES から一緒に失われ、"NurseShift" / "NurseShiftWeeklyCap" 側に
# 移植されていなかった。このため run_issue_rules() が常に空リストを返し、
# solve_failed issueが一度も生成されず、フロントエンドのInfeasibleView
# （"制約見直し"タブ）がinfeasibleを検知できずFeasible表示になっていた。
# ---------------------------------------------------------------------------

_NURSE_SHIFT_RULES: List[IssueRule] = [

    _rule(
        "solve_failed",
        condition=lambda ctx: ctx.get("solution_data") is None,
        build=lambda ctx: {
            "id":       "solve_failed",
            "type":     "SOLVE_FAILED",
            "severity": "CRITICAL",
            "category": "INFEASIBLE",
            "title":    _nurse_shift_t("issue.solve_failed.title"),
            "message":  _nurse_shift_t("issue.solve_failed.message"),
            "description": _nurse_shift_t("issue.solve_failed.message"),
            "infeasibleDetail": ctx.get("infeasible_detail"),
        },
    ),

    _rule(
        "understaffed",
        condition=lambda ctx: ctx["req"]["assigned"] < ctx["req"]["required_count"],
        build=lambda ctx: {
            "id":       f"understaffed_{ctx['tid']}",
            "severity": "CRITICAL",
            "category": "STAFFING",
            "title":    _nurse_shift_t("issue.understaffed.title", tid=ctx["tid"]),
            "message":  _nurse_shift_t(
                "issue.understaffed.message",
                tid=ctx["tid"], required_count=ctx["req"]["required_count"], assigned=ctx["req"]["assigned"],
            ),
        },
    ),

    _rule(
        "no_chief",
        condition=lambda ctx: (
            ctx["req"].get("min_chiefs", 0) > 0
            and ctx["req"].get("chiefs", 0) < ctx["req"]["min_chiefs"]
        ),
        build=lambda ctx: {
            "id":       f"no_chief_{ctx['tid']}",
            "severity": "WARNING",
            "category": "STAFFING",
            "title":    _nurse_shift_t("issue.no_chief.title", tid=ctx["tid"]),
            "message":  _nurse_shift_t(
                "issue.no_chief.message",
                tid=ctx["tid"], min_chiefs=ctx["req"]["min_chiefs"], chiefs=ctx["req"].get("chiefs", 0),
            ),
        },
    ),

    _rule(
        "unassigned_staff",
        condition=lambda ctx: ctx["sid"] not in ctx["staff_ids_with_assignment"],
        build=lambda ctx: {
            "id":       f"unassigned_staff_{ctx['sid']}",
            "severity": "INFO",
            "category": "UTILIZATION",
            "title":    _nurse_shift_t("issue.unassigned_staff.title", name=ctx["staff"].get("name", ctx["sid"])),
            "message":  _nurse_shift_t("issue.unassigned_staff.message", name=ctx["staff"].get("name", ctx["sid"])),
        },
    ),

    _rule(
        "night_rest_violation",
        condition=lambda ctx: ctx["rest_actual"] < ctx["min_rest"],
        build=lambda ctx: {
            "id":       f"night_rest_violation_{ctx['sid']}_{ctx['night_task_id']}",
            "severity": "CRITICAL",
            "category": "COMPLIANCE",
            "title":    _nurse_shift_t(
                "issue.night_rest_violation.title", name=ctx["staff"].get("name", ctx["sid"]),
            ),
            "message":  _nurse_shift_t(
                "issue.night_rest_violation.message",
                name=ctx["staff"].get("name", ctx["sid"]), night_task_id=ctx["night_task_id"],
                next_task_id=ctx["next_task_id"], rest_actual=ctx["rest_actual"], min_rest=ctx["min_rest"],
            ),
        },
    ),

    _rule(
        "consecutive_night_violation",
        condition=lambda ctx: ctx["consecutive_count"] > ctx["max_consecutive"],
        build=lambda ctx: {
            "id":       f"consecutive_night_violation_{ctx['sid']}",
            "severity": "CRITICAL",
            "category": "COMPLIANCE",
            "title":    _nurse_shift_t(
                "issue.consecutive_night_violation.title", name=ctx["staff"].get("name", ctx["sid"]),
            ),
            "message":  _nurse_shift_t(
                "issue.consecutive_night_violation.message",
                name=ctx["staff"].get("name", ctx["sid"]), consecutive_count=ctx["consecutive_count"],
                max_consecutive=ctx["max_consecutive"], night_days=ctx["night_days"],
            ),
        },
    ),
]


# ---------------------------------------------------------------------------
# _TRUCK_DISPATCHER_RULES（2026-07-24 追加）
#
# 背景: truck_dispatcher_solver.py の _build_issues() が持っていた
# tw_overdue（TW超過）/duty_overtime（拘束時間超過）のアドホックな検知ロジックを
# 層A/B（run_issue_rules/ISSUE_RULES）パターンに揃えるための移植
# （DESIGN_2026-07-21 6節「既に持つドメインは差分管理の拡張に該当」）。
# 元の実装は「複数件をまとめて1つの集約issueにする」挙動だったため、
# フロントエンドの表示仕様を変えないよう、ここでも集約1件を返す構造を保つ
# （YardPlanningのような1件=1ペアの粒度にはしていない）。
#
# category="SOLVER"の使い分け（2026-07-24, 5節の運用規約）:
#   - duty_overtime: max_duty_minは車両ごとのCP Optimizerハード制約
#     （mdl.add(... <= max_duty)）。ここが返ってきた解で破られているのは
#     本来ゼロのはずのバグ（抽出/変換ミス）の疑いが強いため category="SOLVER"。
#   - tw_overdue: tw_close_minはソフト制約（目的関数のペナルティ項）であり、
#     超過はモデル上正常にありうる業務結果。バグの疑いではないため
#     category="SOLVER"は付与しない。
# ---------------------------------------------------------------------------

def _tw_overdue_message(overdue_stops: List[tuple]) -> str:
    overdue_stops = sorted(overdue_stops, key=lambda x: -x[1])
    worst = overdue_stops[0]
    names = ", ".join(f"{n}({m}分超過)" for n, m in overdue_stops[:5])
    return (
        f"指定時間帯を過ぎて到着する配送が{len(overdue_stops)}件あります"
        f"（最大{worst[1]}分超過: {worst[0]}）: {names}"
        f"{'...' if len(overdue_stops) > 5 else ''}。"
    )


def _duty_overtime_message(overtime: List[tuple]) -> str:
    names = ", ".join(f"{name}({int(duty)}分 > 上限{int(limit)}分)" for name, duty, limit in overtime)
    return f"以下の車両で拘束時間が各車両の上限を超過: {names}。"


_TRUCK_DISPATCHER_RULES: List[IssueRule] = [

    _rule(
        "tw_overdue",
        condition=lambda ctx: bool(ctx["overdue_stops"]),
        build=lambda ctx: {
            "id":                  "tw_overdue",
            "severity":            "CRITICAL",
            "title":               "タイムウィンドウ超過（遅刻）",
            "message":             _tw_overdue_message(ctx["overdue_stops"]),
            "relatedContainerIds": [],
        },
    ),

    _rule(
        "duty_overtime",
        condition=lambda ctx: bool(ctx["overtime"]),
        build=lambda ctx: solver_bug_issue(
            "duty_overtime",
            "ドライバー拘束時間超過",
            _duty_overtime_message(ctx["overtime"]),
        ),
    ),
]


# ---------------------------------------------------------------------------
# _MEETING_ROOM_RULES（2026-07-24 新規）
#
# MeetingRoomSolverはこれまで「未割当」「収容ギリギリ」しか検知しておらず、
# DSL宣言のhard制約（収容人数・必要設備・部屋利用可能時間帯・同室重複禁止）を
# 返ってきたassignmentsから独立に検算する解チェッカーを持っていなかった
# （DESIGN_2026-07-21 6節の「当てはめ」対象）。
#
# 全ルールcategory="SOLVER": ここでチェックする4項目はいずれもCP Optimizer
# モデル側でハード制約として作り込まれている（収容人数・設備・時間帯は
# 変数生成時点でフィルタ済み、同室重複はno_overlap制約）。返ってきた解で
# 破られていれば、モデル自体ではなく解抽出・変換コードのバグを疑うべき。
#
# コスト分類: capacity/feature/window は assignment 1件ごとの単純比較でO(n)。
# 同室重複(room_double_booking)だけは同一部屋内のペア総当たりでO(n²)相当
# （3-3節の閾値/非同期分岐の対象。solvers/base/solution_checker.py参照）。
# ---------------------------------------------------------------------------

_MEETING_ROOM_RULES: List[IssueRule] = [

    _rule(
        "assignment_capacity_violation",
        condition=lambda ctx: ctx["a"]["attendees"] > ctx["room"].get("capacity", 0),
        build=lambda ctx: solver_bug_issue(
            f"assignment_capacity_violation_{ctx['a']['meeting_id']}",
            f"収容人数違反: {ctx['a']['meeting_name']}",
            f"「{ctx['a']['meeting_name']}」({ctx['a']['attendees']}名) が "
            f"収容人数{ctx['room'].get('capacity', 0)}名の部屋"
            f"「{ctx['a']['room_name']}」に割り当てられています。",
        ),
    ),

    _rule(
        "assignment_feature_violation",
        condition=lambda ctx: not set(ctx["a"].get("features", [])).issubset(set(ctx["room"].get("features", []))),
        build=lambda ctx: solver_bug_issue(
            f"assignment_feature_violation_{ctx['a']['meeting_id']}",
            f"必要設備違反: {ctx['a']['meeting_name']}",
            f"「{ctx['a']['meeting_name']}」が必要とする設備"
            f"{sorted(set(ctx['a'].get('features', [])) - set(ctx['room'].get('features', [])))}を"
            f"部屋「{ctx['a']['room_name']}」が備えていません。",
        ),
    ),

    _rule(
        "assignment_window_violation",
        condition=lambda ctx: (
            ctx["a"]["start_min"] < ctx["room"].get("available_start_min", 0)
            or ctx["a"]["end_min"] > ctx["room"].get("available_end_min", 1440)
        ),
        build=lambda ctx: solver_bug_issue(
            f"assignment_window_violation_{ctx['a']['meeting_id']}",
            f"利用可能時間帯外: {ctx['a']['meeting_name']}",
            f"「{ctx['a']['meeting_name']}」({ctx['a']['start_min']}〜{ctx['a']['end_min']}分) が "
            f"部屋「{ctx['a']['room_name']}」の利用可能時間帯"
            f"({ctx['room'].get('available_start_min', 0)}〜{ctx['room'].get('available_end_min', 1440)}分)"
            f"外に割り当てられています。",
        ),
    ),

    _rule(
        "room_double_booking",
        condition=lambda ctx: (
            ctx["a"]["start_min"] < ctx["b"]["end_min"]
            and ctx["b"]["start_min"] < ctx["a"]["end_min"]
        ),
        build=lambda ctx: solver_bug_issue(
            f"room_double_booking_{ctx['a']['meeting_id']}_{ctx['b']['meeting_id']}",
            f"同室重複: {ctx['a']['room_name']}",
            f"部屋「{ctx['a']['room_name']}」に「{ctx['a']['meeting_name']}」"
            f"({ctx['a']['start_min']}〜{ctx['a']['end_min']}分) と "
            f"「{ctx['b']['meeting_name']}」({ctx['b']['start_min']}〜{ctx['b']['end_min']}分) "
            f"が重複して割り当てられています。",
        ),
    ),
]


# ---------------------------------------------------------------------------
# _STORE_SITE_RULES（2026-07-24 新規）
#
# StoreSiteSolverはdocplex.mp（MIP）ベース。is_valid_solution()による
# ネイティブ検証（9節）は solver 側で直接呼ぶため、ここでは「宣言された
# capacity/max_distance_km制約と、返ってきたopened_stores/assigned_areasの
# 突き合わせ」という、issue_rules.py の既存パターン（層A/B）に沿った
# 独立チェックのみを扱う。いずれもO(n)（総assignment件数に比例、
# 全候補地×全エリアの総当たりではない）。
# ---------------------------------------------------------------------------

_STORE_SITE_RULES: List[IssueRule] = [

    _rule(
        "store_capacity_violation",
        condition=lambda ctx: ctx["total_demand"] > ctx["store"].get("capacity", 0),
        build=lambda ctx: solver_bug_issue(
            f"store_capacity_violation_{ctx['store']['candidate_id']}",
            f"容量超過: {ctx['store']['name']}",
            f"店舗「{ctx['store']['name']}」の割当済み総需要{ctx['total_demand']}が"
            f"容量{ctx['store'].get('capacity', 0)}を超過しています。",
        ),
    ),

    _rule(
        "store_distance_violation",
        condition=lambda ctx: ctx["distance_km"] > ctx["max_distance_km"],
        build=lambda ctx: solver_bug_issue(
            f"store_distance_violation_{ctx['area_id']}_{ctx['store']['candidate_id']}",
            f"到達可能距離超過: {ctx['area_id']} → {ctx['store']['name']}",
            f"エリア「{ctx['area_id']}」から店舗「{ctx['store']['name']}」までの距離"
            f"{round(ctx['distance_km'], 2)}kmが、上限max_distance_km="
            f"{ctx['max_distance_km']}を超過しています。",
        ),
    ),
]


# ---------------------------------------------------------------------------
# _LINE_CHANGEOVER_RULES（2026-07-24 新規）
#
# LineChangeoverScheduler（RCPSP、新フラグシップ）は解チェッカーを一切
# 持っていなかった。前後関係制約（precedences）と資源容量制約
# （resources[].capacity）は、いずれもCP Optimizer側でハード制約として
# 実装されている（end_before_start / cumul_function <= cap）ため、
# 返ってきたscheduleと突き合わせて破られていればバグの疑いが強い。
#
# resource_capacity_violation は solvers/base/solution_checker.sweep_peak_usage()
# によるスイープライン検算（O(n log n)、2節「ソート/スイープ」分類。
# DESIGN_2026-07-21時点では「現状未実装、将来の拡張候補」とされていた
# 分類の初適用）。
# ---------------------------------------------------------------------------

_LINE_CHANGEOVER_RULES: List[IssueRule] = [

    _rule(
        "precedence_violation",
        condition=lambda ctx: ctx["to_start"] < ctx["from_end"] + ctx["min_delay"],
        build=lambda ctx: solver_bug_issue(
            f"precedence_violation_{ctx['from_id']}_{ctx['to_id']}",
            f"前後関係制約違反: {ctx['from_id']} → {ctx['to_id']}",
            f"{ctx['from_id']}の終了({ctx['from_end']})+最小間隔({ctx['min_delay']})が "
            f"{ctx['to_id']}の開始({ctx['to_start']})を超えています。",
        ),
    ),

    _rule(
        "resource_capacity_violation",
        condition=lambda ctx: ctx["peak"] > ctx["capacity"],
        build=lambda ctx: solver_bug_issue(
            f"resource_capacity_violation_{ctx['resource_id']}",
            f"資源容量超過: {ctx['resource_name']}",
            f"資源「{ctx['resource_name']}」の同時使用量が最大{ctx['peak']}に達し、"
            f"容量{ctx['capacity']}を超過しています"
            f"（時刻{ctx['peak_time']}付近、スイープライン検算による独立検証）。",
        ),
    ),
]


# ---------------------------------------------------------------------------
# _TRANSPORT_COST_MINIMIZER_RULES（2026-08-30 新規、solution checker展開パイロット）
#
# TransportCostMinimizerSolverはdocplex.mp（MIP）ベース。供給制約
# （Sum_j x[i][j] <= supply_i）と需要制約（Sum_i x[i][j] == demand_j）は
# いずれもMIPモデル側のハード制約として作り込まれている。返ってきたflowsを
# 独立に集計し直し、宣言されたsupply/demandと突き合わせる。いずれもO(n)
# （工場数・店舗数に比例）のため常に同期実行。
#
# demand_unmetのロジック自体は本カタログ整備前からsolver.py内に書かれて
# いたが、ISSUE_RULES辞書に"TransportCostMinimizer"キーが未登録だったため
# 一度も実行されていなかった（2026-08-30、カタログ登録により有効化）。
# supply_exceededは同時に新規追加。
# ---------------------------------------------------------------------------

_TRANSPORT_COST_MINIMIZER_RULES: List[IssueRule] = [

    _rule(
        "demand_unmet",
        condition=lambda ctx: ctx["demand"] - ctx["delivered"] > 1e-6,
        build=lambda ctx: solver_bug_issue(
            f"demand_unmet_{ctx['store_id']}",
            f"需要未充足: {ctx['store_name']}",
            f"店舗「{ctx['store_name']}」の需要{ctx['demand']:.1f}に対し、"
            f"実際の配送量は{ctx['delivered']:.1f}でした"
            f"（{ctx['demand'] - ctx['delivered']:.1f}不足）。",
        ),
    ),

    _rule(
        "supply_exceeded",
        condition=lambda ctx: ctx["shipped"] - ctx["supply"] > 1e-6,
        build=lambda ctx: solver_bug_issue(
            f"supply_exceeded_{ctx['factory_id']}",
            f"供給上限超過: {ctx['factory_name']}",
            f"工場「{ctx['factory_name']}」の供給上限{ctx['supply']:.1f}に対し、"
            f"実際の出荷量は{ctx['shipped']:.1f}でした"
            f"（{ctx['shipped'] - ctx['supply']:.1f}超過）。",
        ),
    ),
]


# ---------------------------------------------------------------------------
# _TANK_ALLOCATION_RULES（2026-08-30 新規、solution checker展開パイロット）
#
# TankAllocationPlannerSolver（docplex.cp/CP-SAT両対応）は容量超過
# （capacity_violation）・相性違反（incompatible_pair_violation）の検算
# ロジックをsolver.py内の_detect_issues()に個別実装済みだったが、
# ISSUE_RULESカタログには未登録の非公式実装だった（2026-08-30、カタログ化）。
# 併せて、既存実装で相性違反のみcategory="SOLVER"タグが付いていなかった
# 不整合をsolver_bug_issue()経由に統一して解消する。
#
# capacity_violationはタンク1件ごとの単純比較でO(n)、常に同期実行。
# incompatible_pair_violationは同一タンク内のロット総当たりでO(n²)相当、
# run_or_defer()で規模に応じた同期/非同期分岐の対象とする。
# ---------------------------------------------------------------------------

_TANK_ALLOCATION_RULES: List[IssueRule] = [

    _rule(
        "capacity_violation",
        condition=lambda ctx: ctx["ta"]["total_volume"] > ctx["ta"].get("capacity", 0) + 1e-6,
        build=lambda ctx: solver_bug_issue(
            f"capacity_violation_{ctx['ta']['tank_id']}",
            f"容量超過（解チェッカー）: {ctx['ta']['tank_name']}",
            f"タンク「{ctx['ta']['tank_name']}」の積載量{ctx['ta']['total_volume']:.1f}kLが"
            f"容量{ctx['ta'].get('capacity', 0):.1f}kLを超えています。",
        ),
    ),

    _rule(
        "incompatible_pair_violation",
        condition=lambda ctx: (
            (ctx["lot_i"]["category"], ctx["lot_j"]["category"]) in ctx["incompatible_set"]
        ),
        build=lambda ctx: solver_bug_issue(
            f"incompatible_{ctx['tank_id']}_{ctx['lot_i']['lot_id']}_{ctx['lot_j']['lot_id']}",
            f"相性違反（解チェッカー）: {ctx['tank_name']}",
            f"タンク「{ctx['tank_name']}」に「{ctx['lot_i']['category']}」と"
            f"「{ctx['lot_j']['category']}」の混載禁止ロットが割り当てられています。",
        ),
    ),
]


# ---------------------------------------------------------------------------
# _AUCTION_WINNER_SELECTOR_RULES（2026-08-31 新規、solution checker展開バッチ1）
#
# 制約: (a) 各商品ロットは高々1件の落札入札にしか含まれない（set packing）、
#       (b) config.min_revenue > 0 の場合、落札総額はその下限を満たす。
# どちらもsolver.py側で明示的にmdl.add_constraintしているハード制約であり、
# 解チェッカーはDSL宣言値（bids/items/min_revenue）と返ってきたwinnersのみから
# 独立に再計算する。
# ---------------------------------------------------------------------------

_AUCTION_WINNER_SELECTOR_RULES: List[IssueRule] = [

    _rule(
        "item_won_by_multiple_bids",
        condition=lambda ctx: len(ctx["winning_bid_ids"]) > 1,
        build=lambda ctx: solver_bug_issue(
            f"item_won_by_multiple_bids_{ctx['item_id']}",
            f"商品ロット重複落札（解チェッカー）: {ctx['item_id']}",
            f"商品ロット「{ctx['item_id']}」が複数の入札"
            f"（{', '.join(ctx['winning_bid_ids'])}）に同時に含まれています。"
            "各商品ロットは高々1件にしか落札されない制約と矛盾しています。",
        ),
    ),

    _rule(
        "min_revenue_violated",
        condition=lambda ctx: ctx["min_revenue"] > 0 and ctx["total_revenue"] < ctx["min_revenue"] - 1e-6,
        build=lambda ctx: solver_bug_issue(
            "min_revenue_violated",
            "最低落札総額割れ（解チェッカー）",
            f"落札総額{ctx['total_revenue']:.1f}が最低落札総額{ctx['min_revenue']:.1f}を"
            "下回っています。min_revenue制約と矛盾しています。",
        ),
    ),
]


# ---------------------------------------------------------------------------
# _CAPITAL_PROJECT_SELECTOR_RULES（2026-08-31 新規、solution checker展開バッチ1）
#
# 制約: (a) 選定案件の投資額合計は予算上限を超えない、
#       (b) config.max_projects が設定されている場合、選定件数はその上限以下。
# ---------------------------------------------------------------------------

_CAPITAL_PROJECT_SELECTOR_RULES: List[IssueRule] = [

    _rule(
        "budget_exceeded",
        condition=lambda ctx: ctx["total_weight"] > ctx["budget"] + 1e-6,
        build=lambda ctx: solver_bug_issue(
            "budget_exceeded",
            "予算超過（解チェッカー）",
            f"選定案件の投資額合計{ctx['total_weight']:.1f}が"
            f"予算上限{ctx['budget']:.1f}を超えています。",
        ),
    ),

    _rule(
        "max_projects_exceeded",
        condition=lambda ctx: ctx["max_projects"] is not None and ctx["selected_count"] > ctx["max_projects"],
        build=lambda ctx: solver_bug_issue(
            "max_projects_exceeded",
            "最大選定件数超過（解チェッカー）",
            f"選定件数{ctx['selected_count']}件が"
            f"最大選定件数{ctx['max_projects']}件を超えています。",
        ),
    ),
]


# ---------------------------------------------------------------------------
# _INVENTORY_REPLENISHMENT_PLANNER_RULES（2026-08-31 新規、solution checker展開バッチ1）
#
# 制約: (a) 各計画期の中央倉庫からの発送合計は週次供給能力上限を超えない、
#       (b) 各拠点・各計画期の期末在庫は「前期末在庫+当期発送量-当期需要」と一致する
#           （在庫フロー保存則。返ってきたshipments/inventoriesのみから
#           1期ずつ独立に再計算し、solver内部のinv[]/ship[]変数は参照しない）。
# ---------------------------------------------------------------------------

_INVENTORY_REPLENISHMENT_PLANNER_RULES: List[IssueRule] = [

    _rule(
        "supply_capacity_exceeded",
        condition=lambda ctx: ctx["total_shipped"] > ctx["supply_capacity_per_period"] + 1e-6,
        build=lambda ctx: solver_bug_issue(
            f"supply_capacity_exceeded_{ctx['period_id']}",
            f"週次供給能力超過（解チェッカー）: {ctx['period_id']}",
            f"計画期「{ctx['period_id']}」の発送合計{ctx['total_shipped']:.1f}が"
            f"週次供給能力上限{ctx['supply_capacity_per_period']:.1f}を超えています。",
        ),
    ),

    _rule(
        "inventory_flow_mismatch",
        condition=lambda ctx: abs(ctx["expected_inv"] - ctx["actual_inv"]) > 1e-6,
        build=lambda ctx: solver_bug_issue(
            f"inventory_flow_mismatch_{ctx['center_id']}_{ctx['period_id']}",
            f"在庫フロー不整合（解チェッカー）: {ctx['center_id']} / {ctx['period_id']}",
            f"拠点「{ctx['center_id']}」計画期「{ctx['period_id']}」の期末在庫"
            f"{ctx['actual_inv']:.1f}が、前期末在庫+発送量-需要から計算した"
            f"期待値{ctx['expected_inv']:.1f}と一致しません。",
        ),
    ),
]


# ---------------------------------------------------------------------------
# _MYSTERY_SHOPPER_SCHEDULER_RULES（2026-08-31 新規、solution checker展開バッチ2）
#
# 制約: (a) 各訪問枠は高々1人×1日にしか割り当てられない、
#       (b) 調査員×日ごとの割り当ては高々1件。
# どちらもsolver.py側でmdl.add_constraintしているハード制約。
# 再訪問間隔チェック（revisit_short_*）は既にsolve()内で独立検証済み
# （WARNING severityの業務向け通知として運用中、稼働している実装のため
# 本バッチでは変更しない）。
# ---------------------------------------------------------------------------

_MYSTERY_SHOPPER_SCHEDULER_RULES: List[IssueRule] = [

    _rule(
        "visit_double_assigned",
        condition=lambda ctx: ctx["assigned_count"] > 1,
        build=lambda ctx: solver_bug_issue(
            f"visit_double_assigned_{ctx['visit_id']}",
            f"訪問枠重複割当（解チェッカー）: {ctx['visit_id']}",
            f"訪問枠「{ctx['visit_id']}」が{ctx['assigned_count']}件の割り当てを持っています。"
            "各訪問枠は高々1人×1日にしか割り当てられない制約と矛盾しています。",
        ),
    ),

    _rule(
        "shopper_day_double_booked",
        condition=lambda ctx: ctx["assigned_count"] > 1,
        build=lambda ctx: solver_bug_issue(
            f"shopper_day_double_booked_{ctx['shopper_id']}_{ctx['day']}",
            f"調査員の同日重複割当（解チェッカー）: {ctx['shopper_id']} / {ctx['day']}",
            f"調査員「{ctx['shopper_id']}」が{ctx['day']}に{ctx['assigned_count']}件の"
            "訪問枠を割り当てられています。調査員×日ごとに高々1件の制約と矛盾しています。",
        ),
    ),
]


# ---------------------------------------------------------------------------
# _NURSING_WORKLOAD_BALANCE_RULES（2026-08-31 新規、solution checker展開バッチ2）
#
# 制約: (a) 各看護師の担当患者数は[minPatientsPerNurse, maxPatientsPerNurse]の範囲、
#       (b) 各看護師の負荷（担当患者のアキュイティ合計）はmaxWorkloadPerNurse以下。
# どちらもCPOモデル側でmdl.add()しているハード制約。
# ---------------------------------------------------------------------------

_NURSING_WORKLOAD_BALANCE_RULES: List[IssueRule] = [

    _rule(
        "nurse_patient_count_out_of_range",
        condition=lambda ctx: (
            ctx["patient_count"] < ctx["min_patients"] or ctx["patient_count"] > ctx["max_patients"]
        ),
        build=lambda ctx: solver_bug_issue(
            f"nurse_patient_count_out_of_range_{ctx['nurse_id']}",
            f"担当患者数の範囲逸脱（解チェッカー）: {ctx['nurse_name']}",
            f"看護師「{ctx['nurse_name']}」の担当患者数{ctx['patient_count']}件が"
            f"許容範囲[{ctx['min_patients']}, {ctx['max_patients']}]の外にあります。",
        ),
    ),

    _rule(
        "nurse_workload_exceeded",
        condition=lambda ctx: ctx["total_acuity"] > ctx["max_workload"],
        build=lambda ctx: solver_bug_issue(
            f"nurse_workload_exceeded_{ctx['nurse_id']}",
            f"負荷上限超過（解チェッカー）: {ctx['nurse_name']}",
            f"看護師「{ctx['nurse_name']}」の負荷（アキュイティ合計）{ctx['total_acuity']}が"
            f"上限{ctx['max_workload']}を超えています。",
        ),
    ),
]


# ---------------------------------------------------------------------------
# _PORTFOLIO_OVERLAP_DESIGNER_RULES（2026-08-31 新規、solution checker展開バッチ2）
#
# 制約: (a) 各ファンドの組入銘柄数は指定されたrequired_countちょうど、
#       (b) 返ってきたoverlap_matrix（各ファンドペアの重複銘柄数）の実際の最大値と、
#           KPIとして返されているworst_overlapが一致する。
# (b)はsolve()内で既にworst_overlap/overlap_matrixを別々に計算しているため、
# 両者がずれる（例: _make_result側のfund_solutions構築が別経路の場合）ことを
# 返ってきたデータのみから検算する。
# ---------------------------------------------------------------------------

_PORTFOLIO_OVERLAP_DESIGNER_RULES: List[IssueRule] = [

    _rule(
        "selected_count_mismatch",
        condition=lambda ctx: ctx["selected_count"] != ctx["required_count"],
        build=lambda ctx: solver_bug_issue(
            f"selected_count_mismatch_{ctx['fund_id']}",
            f"組入銘柄数不一致（解チェッカー）: {ctx['fund_name']}",
            f"ファンド「{ctx['fund_name']}」の組入銘柄数{ctx['selected_count']}件が"
            f"指定された組入銘柄数{ctx['required_count']}件と一致しません。",
        ),
    ),

    _rule(
        "overlap_matrix_worst_overlap_mismatch",
        condition=lambda ctx: ctx["actual_max_overlap"] != ctx["worst_overlap"],
        build=lambda ctx: solver_bug_issue(
            "overlap_matrix_worst_overlap_mismatch",
            "重複数KPI不一致（解チェッカー）",
            f"overlap_matrixから再計算した実際の最大重複数{ctx['actual_max_overlap']}が、"
            f"KPIのworst_overlap（{ctx['worst_overlap']}）と一致しません。",
        ),
    ),
]


# ---------------------------------------------------------------------------
# _CAR_SEQUENCING_RULES（2026-09-01 新規、solution checker展開バッチ3）
#
# 制約: 各車種の投入順序中の実際の台数は car_types[].count（CP側で
# count_expr == required_count というハード制約）と一致する。返ってきた
# sequenceのみから車種別に再集計し、DSL宣言のcountと突き合わせる。
# O(車種数)。
# ---------------------------------------------------------------------------

_CAR_SEQUENCING_RULES: List[IssueRule] = [

    _rule(
        "car_type_count_mismatch",
        condition=lambda ctx: ctx["actual_count"] != ctx["required_count"],
        build=lambda ctx: solver_bug_issue(
            f"car_type_count_mismatch_{ctx['car_type_id']}",
            f"車種別生産台数不一致（解チェッカー）: {ctx['car_type_name']}",
            f"車種「{ctx['car_type_name']}」の投入順序中の実際の台数{ctx['actual_count']}が、"
            f"指定された生産台数{ctx['required_count']}と一致しません。",
        ),
    ),
]


# ---------------------------------------------------------------------------
# _ENERGY_COST_AWARE_SCHEDULER_RULES（2026-09-01 新規、solution checker展開バッチ3）
#
# 制約: (a) 各ラインの電力/作業員/附帯設備の同時使用量はそれぞれ
#       max_power_kw/max_workers/max_equipment_slotsを超えない（pulseによる
#       cumulative制約）。電力は待機電力（standby_power_kw、ライン稼働区間
#       全体で消費）も含めて検算する。solvers.base.solution_checker.
#       sweep_peak_usage()によるスイープライン検算（O(n log n)）。
#       CPO/CP-SATモデル側は0.1kW単位に量子化してpulse/cumulativeの高さを
#       整数化しているため、電力チェックのみ量子化誤差を吸収する許容誤差
#       （0.1kW）を設ける。作業員・附帯設備は元々整数のため誤差なし。
#       (b) 各オーダーの稼働区間は、割り当てられたラインの稼働区間
#       （line_ops）に収まる（start_of/end_ofによるハード制約）。O(n)。
#       (c) 各オーダーは高々1ラインにしか割り当てられない
#       （presence_of合計<=1というハード制約）。O(n)。
# いずれもsolver内部のinterval変数やmdlオブジェクトは一切参照せず、返って
# きたschedule/line_opsと、DSL宣言のorders/linesの容量値のみから検算する。
# ---------------------------------------------------------------------------

_ENERGY_COST_AWARE_SCHEDULER_RULES: List[IssueRule] = [

    _rule(
        "line_capacity_violation",
        condition=lambda ctx: ctx["peak"] > ctx["capacity"] + ctx["tolerance"],
        build=lambda ctx: solver_bug_issue(
            f"line_capacity_violation_{ctx['resource_type']}_{ctx['line_id']}",
            f"ライン資源容量超過（解チェッカー）: {ctx['line_name']} / {ctx['resource_label']}",
            f"ライン「{ctx['line_name']}」の{ctx['resource_label']}同時使用量が最大{ctx['peak']:.2f}"
            f"に達し、容量{ctx['capacity']:.2f}を超過しています"
            f"（時刻{ctx['peak_time']}付近、スイープライン検算による独立検証）。",
        ),
    ),

    _rule(
        "order_outside_line_window",
        condition=lambda ctx: ctx["order_start"] < ctx["line_start"] or ctx["order_end"] > ctx["line_end"],
        build=lambda ctx: solver_bug_issue(
            f"order_outside_line_window_{ctx['order_id']}",
            f"オーダーがライン稼働区間外（解チェッカー）: {ctx['order_id']}",
            f"オーダー「{ctx['order_id']}」の稼働区間[{ctx['order_start']}, {ctx['order_end']}]が、"
            f"割り当てられたライン「{ctx['line_id']}」の稼働区間"
            f"[{ctx['line_start']}, {ctx['line_end']}]をはみ出しています。",
        ),
    ),

    _rule(
        "order_duplicate_assignment",
        condition=lambda ctx: ctx["assigned_count"] > 1,
        build=lambda ctx: solver_bug_issue(
            f"order_duplicate_assignment_{ctx['order_id']}",
            f"オーダー重複割当（解チェッカー）: {ctx['order_id']}",
            f"オーダー「{ctx['order_id']}」が{ctx['assigned_count']}件のラインに"
            "重複して割り当てられています。各オーダーは高々1ラインにしか"
            "割り当てられない制約と矛盾しています。",
        ),
    ),
]


# ---------------------------------------------------------------------------
# _LOT_SIZING_SCHEDULER_RULES（2026-09-01 新規、solution checker展開バッチ3）
#
# 制約: 各期間に割り当てられる注文は高々1件（all_diff(ap)というハード制約。
# capacity_per_periodは現行実装では常に1として扱われる、solver.py内コメント
# 参照）。返ってきたassignmentsのみから期間別に再集計する。O(n)。
# ---------------------------------------------------------------------------

_LOT_SIZING_SCHEDULER_RULES: List[IssueRule] = [

    _rule(
        "period_double_assigned",
        condition=lambda ctx: ctx["assigned_count"] > 1,
        build=lambda ctx: solver_bug_issue(
            f"period_double_assigned_{ctx['period_id']}",
            f"期間重複割当（解チェッカー）: 期間{ctx['period_id']}",
            f"期間「{ctx['period_id']}」に{ctx['assigned_count']}件の注文が割り当てられています。"
            "各期間には高々1件までという制約（all_diff）と矛盾しています。",
        ),
    ),
]



# ---------------------------------------------------------------------------
# _MEDICAL_APPOINTMENT_SCHEDULER_RULES（2026-09-01 新規、solution checker展開バッチ4）
# ---------------------------------------------------------------------------
_MEDICAL_APPOINTMENT_SCHEDULER_RULES: List[IssueRule] = [
    _rule(
        "resource_double_booking",
        condition=lambda ctx: ctx["peak"] > ctx["capacity"] + ctx["tolerance"],
        build=lambda ctx: solver_bug_issue(
            f"resource_double_booking_{ctx['resource_id']}_{ctx['day']}",
            f"資源重複割当（解チェッカー）: {ctx['resource_name']} ({ctx['day']})",
            f"医療資源「{ctx['resource_name']}」の{ctx['day']}における同時割当件数が"
            f"最大{ctx['peak']:.0f}件に達し、資源容量1件を超過しています"
            f"（時刻{ctx['peak_time']}付近、スイープライン検算による独立検証。"
            "no_overlap制約と矛盾しています）。",
        ),
    ),
    _rule(
        "resource_type_mismatch",
        condition=lambda ctx: ctx["assigned_types"] != ctx["needed_types"],
        build=lambda ctx: solver_bug_issue(
            f"resource_type_mismatch_{ctx['request_id']}",
            f"割当資源タイプ不整合（解チェッカー）: {ctx['request_id']}",
            f"受診依頼「{ctx['request_id']}」の必要資源タイプ{dict(ctx['needed_types'])}に対し、"
            f"実際に割り当てられた資源タイプは{dict(ctx['assigned_types'])}であり、一致しません。",
        ),
    ),
    _rule(
        "avoid_day_violation",
        condition=lambda ctx: ctx["day"] in ctx["avoid_days"],
        build=lambda ctx: solver_bug_issue(
            f"avoid_day_violation_{ctx['request_id']}",
            f"忌避日への割当（解チェッカー）: {ctx['request_id']}",
            f"受診依頼「{ctx['request_id']}」が忌避日として指定された「{ctx['day']}」に"
            "割り当てられています。avoid_days制約と矛盾しています。",
        ),
    ),
]


# ---------------------------------------------------------------------------
# _MEDICAL_APPOINTMENT_SEQUENCE_SCHEDULER_RULES（2026-09-01 新規、solution checker展開バッチ4）
# ---------------------------------------------------------------------------
_MEDICAL_APPOINTMENT_SEQUENCE_SCHEDULER_RULES: List[IssueRule] = [
    _rule(
        "resource_double_booking",
        condition=lambda ctx: ctx["peak"] > 1,
        build=lambda ctx: solver_bug_issue(
            f"resource_double_booking_{ctx['resource_id']}",
            f"医療資源の重複割当（解チェッカー）: {ctx['resource_name']}",
            f"医療資源「{ctx['resource_name']}」が同時刻に最大{int(ctx['peak'])}件の受診に"
            f"割り当てられています（時刻{ctx['peak_time']}付近、スイープライン検算による"
            "独立検証）。no_overlap制約と矛盾しています。",
        ),
    ),
    _rule(
        "visit_outside_resource_window",
        condition=lambda ctx: not any(
            ctx["start_min"] >= s["start_min"] and ctx["end_min"] <= s["end_min"]
            for s in ctx["available_slots"]
        ),
        build=lambda ctx: solver_bug_issue(
            f"visit_outside_resource_window_{ctx['visit_id']}_{ctx['resource_id']}",
            f"受診が医療資源の稼働時間外（解チェッカー）: {ctx['visit_id']}",
            f"受診「{ctx['visit_id']}」の割当区間[{ctx['start_min']}, {ctx['end_min']}]が、"
            f"資源「{ctx['resource_name']}」のいずれの稼働スロットにも収まっていません。",
        ),
    ),
    _rule(
        "sequence_gap_violation",
        condition=lambda ctx: (
            ctx["to_start"] - ctx["from_end"] < ctx["min_gap"]
            or (ctx["max_gap"] is not None and ctx["to_start"] - ctx["from_end"] > ctx["max_gap"])
        ),
        build=lambda ctx: solver_bug_issue(
            f"sequence_gap_violation_{ctx['sequence_id']}_{ctx['from_visit_id']}_{ctx['to_visit_id']}",
            f"系列内の間隔制約違反（解チェッカー）: {ctx['sequence_id']}",
            f"系列「{ctx['sequence_id']}」の受診「{ctx['from_visit_id']}」→「{ctx['to_visit_id']}」"
            f"の間隔が{ctx['to_start'] - ctx['from_end']}分で、"
            f"要求範囲[{ctx['min_gap']}, {ctx['max_gap']}]（{ctx['rule_label']}）を満たしていません。",
        ),
    ),
    _rule(
        "same_resource_rule_violation",
        condition=lambda ctx: ctx["shared_resource_ids_a"].isdisjoint(ctx["shared_resource_ids_b"]),
        build=lambda ctx: solver_bug_issue(
            f"same_resource_rule_violation_{ctx['sequence_id']}_{ctx['visit_a_id']}_{ctx['visit_b_id']}",
            f"継続担当制約違反（解チェッカー）: {ctx['sequence_id']}",
            f"系列「{ctx['sequence_id']}」の受診「{ctx['visit_a_id']}」と「{ctx['visit_b_id']}」は"
            "同一資源を使う必要がありますが、割り当てられた資源が一致しません。",
        ),
    ),
    _rule(
        "visit_resource_time_mismatch",
        condition=lambda ctx: len({(ar["start_min"], ar["end_min"]) for ar in ctx["assigned_resources"]}) > 1,
        build=lambda ctx: solver_bug_issue(
            f"visit_resource_time_mismatch_{ctx['visit_id']}",
            f"受診内で資源ごとに開始/終了が不一致（解チェッカー）: {ctx['visit_id']}",
            f"受診「{ctx['visit_id']}」に割り当てられた複数資源の開始/終了時刻が一致していません: "
            f"{[(ar['resource_id'], ar['start_min'], ar['end_min']) for ar in ctx['assigned_resources']]}",
        ),
    ),
]


# ---------------------------------------------------------------------------
# _PATIENT_TRANSPORT_PLANNER_RULES（2026-09-01 新規、solution checker展開バッチ4）
# ---------------------------------------------------------------------------
_PATIENT_TRANSPORT_PLANNER_RULES: List[IssueRule] = [
    _rule(
        "vehicle_capacity_violation",
        condition=lambda ctx: ctx["peak"] > ctx["capacity"],
        build=lambda ctx: solver_bug_issue(
            f"vehicle_capacity_violation_{ctx['vehicle_id']}",
            f"車両定員超過（解チェッカー）: {ctx['vehicle_name']}",
            f"車両「{ctx['vehicle_name']}」の同時乗車定員（capacity_required合計）が最大"
            f"{ctx['peak']:.0f}に達し、定員{ctx['capacity']}を超過しています"
            f"（時刻{ctx['peak_time']}付近、スイープライン検算による独立検証）。",
        ),
    ),
    _rule(
        "vehicle_phase_overlap",
        condition=lambda ctx: ctx["peak"] > 1,
        build=lambda ctx: solver_bug_issue(
            f"vehicle_phase_overlap_{ctx['vehicle_id']}",
            f"車両フェーズ重複（解チェッカー）: {ctx['vehicle_name']}",
            f"車両「{ctx['vehicle_name']}」に割り当てられた2件以上のフェーズが時間的に重複しています"
            f"（時刻{ctx['peak_time']}付近、no_overlap制約の独立検証）。",
        ),
    ),
    _rule(
        "roundtrip_order_violation",
        condition=lambda ctx: ctx["inbound_start"] < ctx["outbound_end"] + ctx["exam_duration_min"],
        build=lambda ctx: solver_bug_issue(
            f"roundtrip_order_violation_{ctx['request_id']}",
            f"往復順序制約違反（解チェッカー）: 依頼{ctx['request_id']}",
            f"依頼「{ctx['request_id']}」の復路開始（{ctx['inbound_start']}）が、往路終了"
            f"（{ctx['outbound_end']}）+ 診察時間（{ctx['exam_duration_min']}分）より前です。",
        ),
    ),
    _rule(
        "phase_outside_time_window",
        condition=lambda ctx: ctx["start"] < ctx["tw_start"] or ctx["start"] > ctx["tw_end"],
        build=lambda ctx: solver_bug_issue(
            f"phase_outside_time_window_{ctx['request_id']}_{ctx['phase']}",
            f"時間窓逸脱（解チェッカー）: 依頼{ctx['request_id']}（{ctx['phase']}）",
            f"依頼「{ctx['request_id']}」の{ctx['phase']}フェーズの開始時刻（{ctx['start']}）が、"
            f"許容時間窓[{ctx['tw_start']}, {ctx['tw_end']}]の外です。",
        ),
    ),
]


# ---------------------------------------------------------------------------
# _PRODUCTION_LINE_SEQUENCING_RULES（2026-09-01 新規、solution checker展開バッチ5）
#
# 制約: (1) 各スロットには高々1バッチ（all_diff、ハード制約）。
#       (2) 割当ラインはbatch["compatible_lines"]に含まれる（Noneなら制約なし）。
#       (3) 割当日はbatch["line_on_day"]〜["line_off_day"]の範囲内。
#       (4) 日×車種の割当数は、vehicle_types[].daily_limitを上限とし、
#           distribution_exceptionsが該当日を含む場合はmax_per_day/min_per_dayで
#           上書きされる（ハード制約）。
#       (5) 同一日内で、優先順位が隣接する2車種間は、高優先車種の最遅start_hourが
#           低優先車種の最早start_hourを上回ってはならない。
# いずれもsolver内部のbatch_vars/mdl/cpmpyモデルは一切参照せず、返ってきた
# assignmentsとDSL入力(batches/vehicle_types/distribution_exceptions)のみから
# 独立に検算する。既存の「Even Distribution違反チェック」はdistribution_exceptions
# を一切見ておらず（例外による上限緩和/下限指定を取り違える）、このカタログで置き換える。
# ---------------------------------------------------------------------------
_PRODUCTION_LINE_SEQUENCING_RULES: List[IssueRule] = [
    _rule(
        "slot_double_booked",
        condition=lambda ctx: ctx["assigned_count"] > 1,
        build=lambda ctx: solver_bug_issue(
            f"slot_double_booked_{ctx['slot_id']}",
            f"スロット重複割当（解チェッカー）: {ctx['slot_id']}",
            f"スロット「{ctx['slot_id']}」に{ctx['assigned_count']}件のバッチが"
            "割り当てられています。各スロットには高々1バッチまでという制約"
            "（all_diff）と矛盾しています。",
        ),
    ),
    _rule(
        "batch_incompatible_line",
        condition=lambda ctx: (
            ctx["compatible_lines"] is not None
            and ctx["assigned_line_id"] not in ctx["compatible_lines"]
        ),
        build=lambda ctx: solver_bug_issue(
            f"batch_incompatible_line_{ctx['batch_id']}",
            f"非互換ラインへの割当（解チェッカー）: {ctx['batch_id']}",
            f"バッチ「{ctx['batch_id']}」がライン「{ctx['assigned_line_id']}」に"
            f"割り当てられていますが、許容ライン一覧{ctx['compatible_lines']}に"
            "含まれていません。",
        ),
    ),
    _rule(
        "batch_outside_line_window",
        condition=lambda ctx: (
            ctx["assigned_day"] < ctx["line_on_day"] or ctx["assigned_day"] > ctx["line_off_day"]
        ),
        build=lambda ctx: solver_bug_issue(
            f"batch_outside_line_window_{ctx['batch_id']}",
            f"稼働期間外への割当（解チェッカー）: {ctx['batch_id']}",
            f"バッチ「{ctx['batch_id']}」が日{ctx['assigned_day']}に割り当てられていますが、"
            f"ライン稼働期間[{ctx['line_on_day']}, {ctx['line_off_day']}]の範囲外です。",
        ),
    ),
    _rule(
        "distribution_daily_max_violation",
        condition=lambda ctx: ctx["count"] > ctx["effective_max"],
        build=lambda ctx: solver_bug_issue(
            f"distribution_daily_max_violation_{ctx['day']}_{ctx['vtype_id']}",
            f"分散上限違反（解チェッカー）: 日{ctx['day']} 車種{ctx['vtype_id']}",
            f"日{ctx['day']}の車種「{ctx['vtype_id']}」が{ctx['count']}バッチ割り当てられており、"
            f"上限{ctx['effective_max']}"
            f"{'（分散例外適用）' if ctx['exception_applied'] else ''}を超過しています。",
        ),
    ),
    _rule(
        "distribution_daily_min_violation",
        condition=lambda ctx: ctx["count"] < ctx["effective_min"],
        build=lambda ctx: solver_bug_issue(
            f"distribution_daily_min_violation_{ctx['day']}_{ctx['vtype_id']}",
            f"分散下限違反（解チェッカー）: 日{ctx['day']} 車種{ctx['vtype_id']}",
            f"日{ctx['day']}の車種「{ctx['vtype_id']}」が{ctx['count']}バッチしか"
            f"割り当てられておらず、分散例外で指定された下限{ctx['effective_min']}を"
            "下回っています。",
        ),
    ),
    _rule(
        "batting_order_violation",
        condition=lambda ctx: ctx["max_higher_hour"] > ctx["min_lower_hour"],
        build=lambda ctx: solver_bug_issue(
            f"batting_order_violation_{ctx['day']}_{ctx['vt_higher_id']}_{ctx['vt_lower_id']}",
            f"車種投入順序違反（解チェッカー）: 日{ctx['day']} "
            f"{ctx['vt_higher_id']}→{ctx['vt_lower_id']}",
            f"日{ctx['day']}において、優先車種「{ctx['vt_higher_id']}」の最遅start_hour"
            f"({ctx['max_higher_hour']})が、後続車種「{ctx['vt_lower_id']}」の最早start_hour"
            f"({ctx['min_lower_hour']})を上回っています。投入順序制約と矛盾しています。",
        ),
    ),
    _rule(
        "assignment_unknown_slot",
        condition=lambda ctx: ctx["slot"] is None,
        build=lambda ctx: solver_bug_issue(
            f"assignment_unknown_slot_{ctx['batch_id']}_{ctx['slot_id']}",
            f"未知スロットへの参照（解チェッカー）: {ctx['batch_id']}",
            f"バッチ「{ctx['batch_id']}」の割当先スロットID「{ctx['slot_id']}」が"
            "DSL入力のslots一覧に存在しません（解抽出バグの疑い）。",
        ),
    ),
    _rule(
        "assignment_field_mismatch",
        condition=lambda ctx: (
            ctx["assign_line_id"] != ctx["slot_line_id"]
            or ctx["assign_day"] != ctx["slot_day"]
            or ctx["assign_start_hour"] != ctx["slot_start_hour"]
        ),
        build=lambda ctx: solver_bug_issue(
            f"assignment_field_mismatch_{ctx['batch_id']}",
            f"割当フィールド不整合（解チェッカー）: {ctx['batch_id']}",
            f"バッチ「{ctx['batch_id']}」のassignmentのline_id/day/start_hourが、"
            f"参照先スロット「{ctx['slot_id']}」の実際の属性と一致しません"
            f"（assignment: line={ctx['assign_line_id']}, day={ctx['assign_day']}, "
            f"hour={ctx['assign_start_hour']} / slot: line={ctx['slot_line_id']}, "
            f"day={ctx['slot_day']}, hour={ctx['slot_start_hour']}）。",
        ),
    ),
    _rule(
        "assignment_vehicle_type_mismatch",
        condition=lambda ctx: ctx["assign_vtype"] != ctx["batch_vtype"],
        build=lambda ctx: solver_bug_issue(
            f"assignment_vehicle_type_mismatch_{ctx['batch_id']}",
            f"車種フィールド不整合（解チェッカー）: {ctx['batch_id']}",
            f"バッチ「{ctx['batch_id']}」のassignmentのvehicle_type「{ctx['assign_vtype']}」が、"
            f"DSL入力のバッチ属性vehicle_type「{ctx['batch_vtype']}」と一致しません。",
        ),
    ),
]


# ---------------------------------------------------------------------------
# _RIDESHARE_MATCHING_PLANNER_RULES（2026-09-01 新規、solution checker展開バッチ5、CPO専用ドメイン）
#
# 制約: (1) 各乗客は高々1運転手に割当。(2) pickup→dropoff順序。
#       (3) 実乗車時間 <= 直接所要時間×detour_factor。(4) 座席容量（同時乗車数）。
#       (5) 乗客の希望時間窓内にpickup。(6) 運転手の稼働時間窓内にpickup/dropoff。
# 既存の座席超過チェック(seat_overflow)は「その運転手が1日に運んだ乗客の総数」を
# 見ているだけで実際の同時刻重複を見ておらず、このカタログのseat_capacity_violation
# （スイープライン検算）で置き換える。
# ---------------------------------------------------------------------------
_RIDESHARE_MATCHING_PLANNER_RULES: List[IssueRule] = [
    _rule(
        "seat_capacity_violation",
        condition=lambda ctx: ctx["peak"] > ctx["seats"],
        build=lambda ctx: solver_bug_issue(
            f"seat_capacity_violation_{ctx['driver_id']}",
            f"座席容量超過（解チェッカー）: {ctx['driver_name']}",
            f"運転手「{ctx['driver_name']}」の同時乗車人数が最大{ctx['peak']:.0f}人に達し、"
            f"座席数{ctx['seats']}を超過しています"
            f"（時刻{ctx['peak_time']}付近、スイープライン検算による独立検証）。",
        ),
    ),
    _rule(
        "detour_time_violation",
        condition=lambda ctx: ctx["ride_min"] > ctx["max_ride_min"],
        build=lambda ctx: solver_bug_issue(
            f"detour_time_violation_{ctx['passenger_id']}",
            f"乗車時間上限超過（解チェッカー）: {ctx['passenger_name']}",
            f"乗客「{ctx['passenger_name']}」の実乗車時間（{ctx['ride_min']}分）が、"
            f"直接所要時間{ctx['direct_min']}分×detour_factor{ctx['detour_factor']}="
            f"上限{ctx['max_ride_min']}分を超過しています。",
        ),
    ),
    _rule(
        "pickup_dropoff_order_violation",
        condition=lambda ctx: ctx["ride_min"] < 1,
        build=lambda ctx: solver_bug_issue(
            f"pickup_dropoff_order_violation_{ctx['passenger_id']}",
            f"乗降順序制約違反（解チェッカー）: {ctx['passenger_name']}",
            f"乗客「{ctx['passenger_name']}」の降車時刻（{ctx['dropoff_min']}）が"
            f"乗車時刻（{ctx['pickup_min']}）以前になっています（pickup→dropoff制約矛盾）。",
        ),
    ),
    _rule(
        "passenger_time_window_violation",
        condition=lambda ctx: ctx["pickup_min"] < ctx["window_start_min"] or ctx["pickup_min"] > ctx["window_end_min"],
        build=lambda ctx: solver_bug_issue(
            f"passenger_time_window_violation_{ctx['passenger_id']}",
            f"乗客時間窓逸脱（解チェッカー）: {ctx['passenger_name']}",
            f"乗客「{ctx['passenger_name']}」の乗車時刻（{ctx['pickup_min']}）が、"
            f"希望時間窓[{ctx['window_start_min']}, {ctx['window_end_min']}]の外です。",
        ),
    ),
    _rule(
        "driver_operating_window_violation",
        condition=lambda ctx: ctx["pickup_min"] < ctx["depart_min"] or ctx["dropoff_min"] > ctx["arrive_max"],
        build=lambda ctx: solver_bug_issue(
            f"driver_operating_window_violation_{ctx['driver_id']}_{ctx['passenger_id']}",
            f"運転手稼働時間外の乗降（解チェッカー）: {ctx['driver_name']}",
            f"運転手「{ctx['driver_name']}」の稼働時間[{ctx['depart_min']}, {ctx['arrive_max']}]の外で"
            f"乗客「{ctx['passenger_name']}」の乗降（乗車{ctx['pickup_min']}／降車{ctx['dropoff_min']}）"
            f"が発生しています。",
        ),
    ),
    _rule(
        "passenger_duplicate_assignment",
        condition=lambda ctx: ctx["assigned_count"] > 1,
        build=lambda ctx: solver_bug_issue(
            f"passenger_duplicate_assignment_{ctx['passenger_id']}",
            f"乗客重複割当（解チェッカー）: {ctx['passenger_id']}",
            f"乗客「{ctx['passenger_id']}」が{ctx['assigned_count']}件の運転手に"
            "重複して割り当てられています。各乗客は高々1運転手にしか"
            "割り当てられない制約と矛盾しています。",
        ),
    ),
    _rule(
        "passenger_missing_from_output",
        condition=lambda ctx: ctx["occurrence_count"] == 0,
        build=lambda ctx: solver_bug_issue(
            f"passenger_missing_from_output_{ctx['passenger_id']}",
            f"乗客が結果に存在しません（解チェッカー）: {ctx['passenger_id']}",
            f"乗客「{ctx['passenger_id']}」がmatched_pairsにもunmatched_passengersにも"
            "現れていません（解抽出処理で欠落した疑いがあります）。",
        ),
    ),
]


# ---------------------------------------------------------------------------
# _SHIFT_ROTATION_SCHEDULER_RULES（2026-09-01 新規、solution checker展開バッチ5）
#
# 制約: shift_rotation_scheduler_solver.pyの5つのhard制約（週内土日一致/
# 連続日数[min_consecutive,max_consecutive]/シフト順序前進のみ/14日窓最低休日数/
# 曜日別必要人数の厳密一致）を、返ってきたsolution（template_shift_ids /
# employee_schedules）とDSL宣言（shifts/daily_requirements/constraints）のみから
# 独立に再検算する。solver内部のCpoModel/cpmpy変数は一切参照しない。
# _solve_with_cpsat()に元々issue_statuses変数が定義されていなかったバグも
# あわせて修正する（CPO側にはあった）。
# ---------------------------------------------------------------------------

# solvers/shift_rotation_scheduler_solver.py の SHIFT_TYPE_ORDER と同期させること
_SRS_SHIFT_TYPE_ORDER = {"off": 0, "early": 1, "late": 2, "night": 3}

_SHIFT_ROTATION_SCHEDULER_RULES: List[IssueRule] = [
    _rule(
        "weekend_shift_mismatch",
        condition=lambda ctx: ctx["sat_shift_id"] != ctx["sun_shift_id"],
        build=lambda ctx: solver_bug_issue(
            f"weekend_shift_mismatch_w{ctx['week']}",
            f"土日シフト不一致（解チェッカー）: 週{ctx['week']}",
            f"週{ctx['week']}の土曜シフト「{ctx['sat_shift_id']}」と日曜シフト"
            f"「{ctx['sun_shift_id']}」が一致していません。同一週の土日は同じ"
            "シフトになる制約と矛盾しています。",
        ),
    ),
    _rule(
        "daily_requirement_mismatch",
        condition=lambda ctx: ctx["actual_count"] != ctx["required_count"],
        build=lambda ctx: solver_bug_issue(
            f"daily_requirement_mismatch_w{ctx['week']}_d{ctx['day_of_week']}_{ctx['shift_id']}",
            f"曜日別必要人数不一致（解チェッカー）: week{ctx['week']} "
            f"day{ctx['day_of_week']} / {ctx['shift_id']}",
            f"週{ctx['week']}・曜日{ctx['day_of_week']}のシフト「{ctx['shift_id']}」の"
            f"実際の割当人数{ctx['actual_count']}人が、必要人数"
            f"{ctx['required_count']}人と一致しません。",
        ),
    ),
    _rule(
        "shift_progression_violation",
        condition=lambda ctx: ctx["order_cur"] < ctx["order_prev"],
        build=lambda ctx: solver_bug_issue(
            f"shift_progression_violation_{ctx['employee_id']}_day{ctx['day_index']}",
            f"シフト順序逆行（解チェッカー）: {ctx['employee_id']} day{ctx['day_index']}",
            f"従業員「{ctx['employee_id']}」の週{ctx['week_prev']}曜日{ctx['dow_prev']}"
            f"→週{ctx['week_cur']}曜日{ctx['dow_cur']}で、シフト"
            f"「{ctx['shift_type_prev']}」→「{ctx['shift_type_cur']}」と逆行しています"
            "（OFFを挟まない限りシフトタイプは前進のみ許可される制約と矛盾）。",
        ),
    ),
    _rule(
        "consecutive_run_length_violation",
        condition=lambda ctx: (
            ctx["run_length"] < ctx["min_consecutive"]
            or ctx["run_length"] > ctx["max_consecutive"]
        ),
        build=lambda ctx: solver_bug_issue(
            f"consecutive_run_length_violation_{ctx['employee_id']}_start{ctx['run_start_day']}",
            f"連続日数制約違反（解チェッカー）: {ctx['employee_id']}",
            f"従業員「{ctx['employee_id']}」のシフト「{ctx['shift_id']}」がday"
            f"{ctx['run_start_day']}から{ctx['run_length']}日連続しており、"
            f"許容範囲[{ctx['min_consecutive']}, {ctx['max_consecutive']}]から外れています。",
        ),
    ),
    _rule(
        "days_off_window_violation",
        condition=lambda ctx: ctx["off_count"] < ctx["min_days_off_per_14"],
        build=lambda ctx: solver_bug_issue(
            f"days_off_window_violation_{ctx['employee_id']}_start{ctx['window_start_day']}",
            f"14日窓休日不足（解チェッカー）: {ctx['employee_id']}",
            f"従業員「{ctx['employee_id']}」のday{ctx['window_start_day']}から14日間の"
            f"休日数が{ctx['off_count']}日しかなく、最低"
            f"{ctx['min_days_off_per_14']}日を下回っています。",
        ),
    ),
    _rule(
        "employee_schedule_derivation_mismatch",
        condition=lambda ctx: ctx["actual_shift_id"] != ctx["expected_shift_id"],
        build=lambda ctx: solver_bug_issue(
            f"employee_schedule_derivation_mismatch_{ctx['employee_id']}_w{ctx['week']}_d{ctx['day_of_week']}",
            f"従業員スケジュール導出不一致（解チェッカー）: {ctx['employee_id']}",
            f"従業員「{ctx['employee_id']}」の週{ctx['week']}曜日{ctx['day_of_week']}の"
            f"シフトが「{ctx['actual_shift_id']}」ですが、テンプレート導出式"
            f"（template[(week+k)%W][day]）から期待される値"
            f"「{ctx['expected_shift_id']}」と一致しません（template↔従業員展開の"
            "抽出処理に矛盾がある可能性があります）。",
        ),
    ),
]


# ---------------------------------------------------------------------------
# _STEEL_MILL_SLAB_DESIGN_RULES（2026-09-01 新規、solution checker展開バッチ6・最終）
#
# 制約: (1) 各注文は必ずいずれか1つのスラブに割り当てる（分割不可、
#           sum(assign[i])==1、ハード制約）。
#       (2) 各スラブの積載重量合計 <= slab_capacity（ハード制約）。
#       (3) 各スラブに混在する色数 <= 2（ハード制約）。
# solver内部のCpoModel/cpmpy変数は一切参照せず、返ってきたassignments/
# slabs_usedとDSL入力(orders)のみから独立に再計算する。
# ---------------------------------------------------------------------------
_STEEL_MILL_SLAB_DESIGN_RULES: List[IssueRule] = [
    _rule(
        "order_duplicate_assignment",
        condition=lambda ctx: ctx["assigned_count"] > 1,
        build=lambda ctx: solver_bug_issue(
            f"order_duplicate_assignment_{ctx['order_id']}",
            f"注文重複割当（解チェッカー）: {ctx['order_id']}",
            f"注文「{ctx['order_id']}」が{ctx['assigned_count']}件のスラブに"
            "重複して割り当てられています。各注文は必ず1スラブに割り当てる"
            "制約（sum(assign[i])==1）と矛盾しています。",
        ),
    ),
    _rule(
        "order_missing_from_output",
        condition=lambda ctx: ctx["assigned_count"] == 0,
        build=lambda ctx: solver_bug_issue(
            f"order_missing_from_output_{ctx['order_id']}",
            f"注文が結果に存在しません（解チェッカー）: {ctx['order_id']}",
            f"注文「{ctx['order_id']}」がいずれのスラブのassignmentsにも"
            "現れていません（解抽出処理で欠落した疑いがあります）。",
        ),
    ),
    _rule(
        "slab_capacity_exceeded",
        condition=lambda ctx: ctx["recomputed_weight"] > ctx["capacity"],
        build=lambda ctx: solver_bug_issue(
            f"slab_capacity_exceeded_{ctx['slab_index']}",
            f"スラブ容量超過（解チェッカー）: スラブ{ctx['slab_index']+1}",
            f"スラブ{ctx['slab_index']+1}の積載重量合計（assignmentsから再計算: "
            f"{ctx['recomputed_weight']}t）が容量{ctx['capacity']}tを超過しています。",
        ),
    ),
    _rule(
        "slab_color_limit_exceeded",
        condition=lambda ctx: ctx["color_count"] > 2,
        build=lambda ctx: solver_bug_issue(
            f"slab_color_limit_exceeded_{ctx['slab_index']}",
            f"スラブ内色数超過（解チェッカー）: スラブ{ctx['slab_index']+1}",
            f"スラブ{ctx['slab_index']+1}に{ctx['color_count']}色"
            f"（{ctx['recomputed_colors']}）が混在しており、上限2色を超過しています。",
        ),
    ),
    _rule(
        "slab_used_weight_mismatch",
        condition=lambda ctx: (
            ctx["recomputed_weight"] != ctx["reported_weight"]
            or ctx["recomputed_waste"] != ctx["reported_waste"]
        ),
        build=lambda ctx: solver_bug_issue(
            f"slab_used_weight_mismatch_{ctx['slab_index']}",
            f"スラブ重量集計不一致（解チェッカー）: スラブ{ctx['slab_index']+1}",
            f"スラブ{ctx['slab_index']+1}の報告値（used_weight={ctx['reported_weight']}, "
            f"waste_weight={ctx['reported_waste']}）が、assignmentsから独立再計算した値"
            f"（{ctx['recomputed_weight']}, {ctx['recomputed_waste']}）と一致しません。",
        ),
    ),
]


# ---------------------------------------------------------------------------
# _VESSEL_DECK_LOADER_RULES（2026-09-01 新規、solution checker展開バッチ6・最終）
#
# 制約: (1) 甲板幅制約（既存のwidth_violationで検証済み、本カタログでは扱わない）。
#       (2) 2次元非重複制約（危険物マージン込み） — 最も複雑な部分（logical_or
#           を多用、過去2回のバグ修正実績あり）。
#       (3) 積み込み順序の支持制約（西壁/南壁/北壁 or 先行コンテナとの
#           正の長さを持つ境界接触） — 同じく複雑（logical_and多用）。
#       (4) Z（used_length）はmax(x_i+l_i)の最小化対象。
# solverのvessel_deck_loader_solver.py内に既にあった手書きの`hazard_proximity`
# チェック（危険物ペアのみ・マージンありの場合限定）は、本カタログの
# container_overlap_violation（全ペア総当たり、margin=0のケースも含む一般化）
# で置き換える。width_violationは維持（別の制約を検証しているため）。
# ---------------------------------------------------------------------------
_VESSEL_DECK_LOADER_RULES: List[IssueRule] = [
    _rule(
        "container_missing_from_placement",
        condition=lambda ctx: ctx["occurrence_count"] == 0,
        build=lambda ctx: solver_bug_issue(
            f"container_missing_from_placement_{ctx['container_id']}",
            f"コンテナが結果に存在しません（解チェッカー）: {ctx['container_id']}",
            f"コンテナ「{ctx['container_id']}」がplacementsに現れていません"
            "（解抽出処理で欠落した疑いがあります）。",
        ),
    ),
    _rule(
        "container_overlap_violation",
        condition=lambda ctx: not ctx["separated"],
        build=lambda ctx: solver_bug_issue(
            f"container_overlap_violation_{ctx['cid_a']}_{ctx['cid_b']}",
            f"コンテナ重なり（解チェッカー）: {ctx['name_a']} ⇔ {ctx['name_b']}",
            f"コンテナ「{ctx['name_a']}」と「{ctx['name_b']}」の2次元配置が"
            f"重なっています（必要マージン{ctx['margin']}を考慮した非重複制約の"
            "独立検証）。",
        ),
    ),
    _rule(
        "container_unsupported",
        condition=lambda ctx: not ctx["supported"],
        build=lambda ctx: solver_bug_issue(
            f"container_unsupported_{ctx['container_id']}",
            f"支持条件違反（解チェッカー）: {ctx['container_name']}",
            f"コンテナ「{ctx['container_name']}」が西壁・南壁・北壁のいずれにも"
            "接しておらず、かつ先行コンテナとも正の長さを持つ境界線分で"
            "接していません（積み込み順序の支持制約と矛盾しています）。",
        ),
    ),
    _rule(
        "used_length_mismatch",
        condition=lambda ctx: ctx["reported_z"] != ctx["recomputed_z"],
        build=lambda ctx: solver_bug_issue(
            "used_length_mismatch",
            "使用甲板長不整合（解チェッカー）",
            f"報告されたZ_val（used_length={ctx['reported_z']}）が、実際の配置から"
            f"再計算した最大値（{ctx['recomputed_z']}）と一致しません。",
        ),
    ),
]


# ---------------------------------------------------------------------------
# レシピ宣言 (層B)
# ---------------------------------------------------------------------------

ISSUE_RULES: Dict[str, List[IssueRule]] = {
    "YardPlanning":            _YARD_RULES,
    "NurseShift":              _NURSE_SHIFT_RULES,
    "NurseShiftWeeklyCap":     _NURSE_SHIFT_RULES,
    "TruckDispatcher":         _TRUCK_DISPATCHER_RULES,
    "MeetingRoom":             _MEETING_ROOM_RULES,
    "StoreSite":               _STORE_SITE_RULES,
    "LineChangeoverScheduler": _LINE_CHANGEOVER_RULES,
    "TransportCostMinimizer":       _TRANSPORT_COST_MINIMIZER_RULES,
    "TankAllocationPlanner":        _TANK_ALLOCATION_RULES,
    "AuctionWinnerSelector":        _AUCTION_WINNER_SELECTOR_RULES,
    "CapitalProjectSelector":       _CAPITAL_PROJECT_SELECTOR_RULES,
    "InventoryReplenishmentPlanner": _INVENTORY_REPLENISHMENT_PLANNER_RULES,
    "MysteryShopperScheduler":      _MYSTERY_SHOPPER_SCHEDULER_RULES,
    "NursingWorkloadBalance":       _NURSING_WORKLOAD_BALANCE_RULES,
    "PortfolioOverlapDesigner":     _PORTFOLIO_OVERLAP_DESIGNER_RULES,
    "CarSequencing":                 _CAR_SEQUENCING_RULES,
    "EnergyCostAwareScheduler":      _ENERGY_COST_AWARE_SCHEDULER_RULES,
    "LotSizingScheduler":            _LOT_SIZING_SCHEDULER_RULES,
    "MedicalAppointmentScheduler":         _MEDICAL_APPOINTMENT_SCHEDULER_RULES,
    "MedicalAppointmentSequenceScheduler": _MEDICAL_APPOINTMENT_SEQUENCE_SCHEDULER_RULES,
    "PatientTransportPlanner":             _PATIENT_TRANSPORT_PLANNER_RULES,
    "ProductionLineSequencing":            _PRODUCTION_LINE_SEQUENCING_RULES,
    "RideshareMatchingPlanner":            _RIDESHARE_MATCHING_PLANNER_RULES,
    "ShiftRotationScheduler":               _SHIFT_ROTATION_SCHEDULER_RULES,
    "SteelMillSlabDesign":                  _STEEL_MILL_SLAB_DESIGN_RULES,
    "VesselDeckLoader":                      _VESSEL_DECK_LOADER_RULES,
}


# ---------------------------------------------------------------------------
# context ビルダー (ソルバー側で呼び出す)
# ---------------------------------------------------------------------------

def build_yard_contexts(
    container_map:      Dict[str, Any],
    containers:         List[Dict],
    solution_data_list: List[Dict],
    reefer_ids:         set,
    imo_ids:            set,
    oog_ids:            set,
    reefer_slot_set:    set,
    imo_slot_set:       set,
    oog_excluded_slots: set,
    crane_windows:      Dict,
    break_windows:      Dict,
) -> List[Dict[str, Any]]:
    """
    YardPlanning 用の context リストを生成する。
    ソルバー側の _detect_issues() を置き換えるために呼び出す。
    """
    ctxs: List[Dict] = []

    # ペアルール: コンテナペア全組み合わせ
    cids = list(container_map.keys())
    for i in range(len(cids)):
        for j in range(i + 1, len(cids)):
            cid_a, cid_b = cids[i], cids[j]
            c_a, c_b = container_map[cid_a], container_map[cid_b]
            y_a, y_b = c_a.get("yard", {}), c_b.get("yard", {})
            v_a, v_b = c_a.get("ship", {}), c_b.get("ship", {})
            w_a, w_b = c_a.get("weight", 0), c_b.get("weight", 0)
            o_a, o_b = c_a.get("order"), c_b.get("order")
            pair_id  = f"{cid_a}_{cid_b}"

            # yard/ship それぞれの upper/lower を確定
            y_upper_id, y_lower_id, y_o_u, y_o_l, y_w_u, y_w_l = (
                (cid_b, cid_a, o_b, o_a, w_b, w_a) if y_b.get("tier", 0) > y_a.get("tier", 0)
                else (cid_a, cid_b, o_a, o_b, w_a, w_b)
            )
            v_upper_id, v_lower_id, v_o_u, v_o_l, v_w_u, v_w_l = (
                (cid_b, cid_a, o_b, o_a, w_b, w_a) if v_b.get("tier", 0) > v_a.get("tier", 0)
                else (cid_a, cid_b, o_a, o_b, w_a, w_b)
            )

            base = dict(
                cid_a=cid_a, cid_b=cid_b, c_a=c_a, c_b=c_b,
                y_a=y_a, y_b=y_b, v_a=v_a, v_b=v_b,
                pair_id=pair_id,
            )
            # yard系ルール: upper/lower は yard基準
            for rule_id in ("is_yard", "weight_yard"):
                ctxs.append({**base, "_rule_id": rule_id,
                    "upper_id": y_upper_id, "lower_id": y_lower_id,
                    "o_u": y_o_u, "o_l": y_o_l, "w_u": y_w_u, "w_l": y_w_l})
            # ship系ルール: upper/lower は ship基準
            for rule_id in ("is_ship", "weight_ship"):
                ctxs.append({**base, "_rule_id": rule_id,
                    "upper_id": v_upper_id, "lower_id": v_lower_id,
                    "o_u": v_o_u, "o_l": v_o_l, "w_u": v_w_u, "w_l": v_w_l})

    # ATTRルール: コンテナ単体
    for c in containers:
        cid = str(c["id"])
        y   = c.get("yard", {})
        bay, row, tier = y.get("bay"), y.get("row"), y.get("tier")
        base = dict(cid=cid, c=c, bay=bay, row=row, tier=tier,
                    reefer_ids=reefer_ids, imo_ids=imo_ids, oog_ids=oog_ids,
                    reefer_slot_set=reefer_slot_set, imo_slot_set=imo_slot_set,
                    oog_excluded_slots=oog_excluded_slots, all_containers=containers)
        for rule_id in ("attr_reefer", "attr_imo"):
            ctxs.append({**base, "_rule_id": rule_id})

    # シフトルール: タスク単体
    tasks = solution_data_list[0]["tasks"] if solution_data_list else []
    for t in tasks:
        crane_id = t.get("resource") or t.get("resourceId")
        if not crane_id:
            continue
        base = dict(
            t=t, crane_id=crane_id,
            tid=str(t.get("id", "")), cid=str(t.get("containerId", "")),
            t_start=t.get("start", 0) // 60, t_end=t.get("end", 0) // 60,
            crane_windows=crane_windows, break_windows=break_windows,
        )
        for rule_id in ("shift_violation", "shift_break"):
            ctxs.append({**base, "_rule_id": rule_id})

    # フェーズルール
    if solution_data_list:
        d_res = [t for t in tasks if t.get("operation") == "DISCHARGE"]
        l_res = [t for t in tasks if t.get("operation") == "LOAD"]
        if d_res and l_res:
            max_d_end = max(t["end"] for t in d_res)
            for lt in l_res:
                ctxs.append({"_rule_id": "discharge_before_load", "lt": lt, "max_d_end": max_d_end})

    return ctxs


# ---------------------------------------------------------------------------
# TruckDispatcher context ビルダー（2026-07-24 追加）
# ---------------------------------------------------------------------------

def build_truck_dispatcher_contexts(
    routes: List[Dict],
    max_duty_by_vehicle: Dict[str, int],
) -> List[Dict[str, Any]]:
    """
    TruckDispatcher 用の context を生成する。tw_overdue/duty_overtime いずれも
    「複数件をまとめて1件の集約issueにする」既存挙動を保つため、YardPlanningの
    ようなペア単位ではなく、ルール1つにつきcontext1件（集約データを内包）を返す。
    """
    overdue_stops = [
        (s["customer_name"], s["arrival_min"] - s["tw_close_min"])
        for r in routes for s in r["stops"]
        if s["arrival_min"] > s["tw_close_min"]
    ]
    overtime = [
        (r["vehicle_name"], r["duty_min"], max_duty_by_vehicle.get(r["vehicle_id"], 9999))
        for r in routes
        if r["duty_min"] > max_duty_by_vehicle.get(r["vehicle_id"], 9999)
    ]
    return [
        {"_rule_id": "tw_overdue",     "overdue_stops": overdue_stops},
        {"_rule_id": "duty_overtime",  "overtime": overtime},
    ]


# ---------------------------------------------------------------------------
# MeetingRoom context ビルダー（2026-07-24 新規）
# ---------------------------------------------------------------------------

def build_meeting_room_field_contexts(
    assignments: List[Dict],
    rooms: List[Dict],
) -> List[Dict[str, Any]]:
    """
    MeetingRoom 用のO(n)フィールドチェック（収容人数/設備/時間帯）のcontextを
    assignment 1件ごとに生成する。
    """
    room_map = {str(r["id"]): r for r in rooms}
    ctxs: List[Dict] = []
    for a in assignments:
        room = room_map.get(a["room_id"], {})
        base = {"a": a, "room": room}
        for rule_id in ("assignment_capacity_violation", "assignment_feature_violation",
                        "assignment_window_violation"):
            ctxs.append({**base, "_rule_id": rule_id})
    return ctxs


def build_meeting_room_overlap_contexts(assignments: List[Dict]) -> List[Dict[str, Any]]:
    """
    MeetingRoom 用の同室重複チェック（O(n²)、同一部屋内のペア総当たり）の
    contextを生成する。solvers/base/solution_checker.run_or_defer() で
    インスタンス規模に応じて同期/非同期を分岐させる想定のため、
    呼び出しコストが高くなりうる点に注意（本関数自体はO(n²)）。
    """
    by_room: Dict[str, List[Dict]] = {}
    for a in assignments:
        by_room.setdefault(a["room_id"], []).append(a)

    ctxs: List[Dict] = []
    for room_id, items in by_room.items():
        for i in range(len(items)):
            for j in range(i + 1, len(items)):
                ctxs.append({"_rule_id": "room_double_booking", "a": items[i], "b": items[j]})
    return ctxs


# ---------------------------------------------------------------------------
# StoreSite context ビルダー（2026-07-24 新規）
# ---------------------------------------------------------------------------

def build_store_site_contexts(
    opened_stores: List[Dict],
    area_map: Dict[str, Dict],
    max_distance_km: float,
    distance_fn: Callable[[Dict, Dict], float],
    cand_map: Dict[str, Dict],
) -> List[Dict[str, Any]]:
    """
    StoreSite 用のO(n)フィールドチェック（容量/到達可能距離）のcontextを
    「実際に生成された割当」1件ごとに生成する（全候補地×全エリアの
    総当たりではない点に注意。O(総assignment件数)）。

    Args:
        distance_fn: (area_dict, candidate_dict) -> float。
                     StoreSiteSolver._distance と同じ計算式を独立に
                     再実行する（4節: converter/solverの内部変数は使わず、
                     DSLの生値area/candidateからその場で計算し直す）。
    """
    ctxs: List[Dict] = []
    for store in opened_stores:
        cand = cand_map.get(store["candidate_id"], {})
        ctxs.append({"_rule_id": "store_capacity_violation",
                     "store": store, "total_demand": store.get("total_demand", 0)})
        for area_id in store.get("assigned_areas", []):
            area = area_map.get(area_id, {})
            dist_km = distance_fn(area, cand)
            ctxs.append({
                "_rule_id":        "store_distance_violation",
                "store":           store,
                "area_id":         area_id,
                "distance_km":     dist_km,
                "max_distance_km": max_distance_km,
            })
    return ctxs


# ---------------------------------------------------------------------------
# LineChangeoverScheduler context ビルダー（2026-07-24 新規）
# ---------------------------------------------------------------------------

def build_line_changeover_precedence_contexts(
    schedule: List[Dict],
    precedences: List[Dict],
) -> List[Dict[str, Any]]:
    """前後関係制約（O(E)、単純比較）のcontextを precedence 1件ごとに生成する。"""
    sched_by_id = {s["task_id"]: s for s in schedule}
    ctxs: List[Dict] = []
    for p in precedences:
        from_id = str(p.get("from_task", ""))
        to_id   = str(p.get("to_task", ""))
        delay   = int(p.get("min_delay", 0))
        sf, st  = sched_by_id.get(from_id), sched_by_id.get(to_id)
        if sf is None or st is None:
            continue
        ctxs.append({
            "_rule_id":  "precedence_violation",
            "from_id":   from_id,
            "to_id":     to_id,
            "from_end":  sf["end"],
            "to_start":  st["start"],
            "min_delay": delay,
        })
    return ctxs


def build_line_changeover_resource_contexts(
    schedule: List[Dict],
    resources: List[Dict],
) -> List[Dict[str, Any]]:
    """
    資源容量制約（O(n log n)、スイープライン検算）のcontextを資源1件ごとに
    生成する。solvers.base.solution_checker.sweep_peak_usage() で
    累積使用量のピークを計算し、容量と一緒にcontextへ積む
    （条件判定(peak > capacity)はルール側のcondition関数で行う）。
    """
    ctxs: List[Dict] = []
    for r in resources:
        rid = str(r["id"])
        cap = int(r.get("capacity", 1))
        events: List[tuple] = []
        for s in schedule:
            for rr in s.get("resource_requirements", []):
                if str(rr.get("resource_id")) != rid:
                    continue
                amount = rr.get("amount", 1)
                events.append((s["start"], amount))
                events.append((s["end"], -amount))
        if not events:
            continue
        peak, peak_time = sweep_peak_usage(events)
        ctxs.append({
            "_rule_id":     "resource_capacity_violation",
            "resource_id":  rid,
            "resource_name": r.get("name", rid),
            "peak":         peak,
            "capacity":     cap,
            "peak_time":    peak_time,
        })
    return ctxs


# ---------------------------------------------------------------------------
# TransportCostMinimizer context ビルダー（2026-08-30 新規）
# ---------------------------------------------------------------------------

def build_transport_cost_minimizer_contexts(
    factories: List[Dict],
    stores: List[Dict],
    flows: List[Dict],
) -> List[Dict[str, Any]]:
    """
    TransportCostMinimizer 用のO(n)フィールドチェック（供給/需要）の
    contextを工場・店舗1件ごとに生成する。flowsから独立に集計し直す
    （solver内部のx変数やmdlオブジェクトは一切参照しない）。
    """
    store_flow_map: Dict[str, float] = {}
    factory_flow_map: Dict[str, float] = {}
    for fl in flows:
        store_flow_map[fl["store_id"]] = store_flow_map.get(fl["store_id"], 0.0) + fl["amount"]
        factory_flow_map[fl["factory_id"]] = factory_flow_map.get(fl["factory_id"], 0.0) + fl["amount"]

    ctxs: List[Dict] = []
    for s in stores:
        sid = str(s["id"])
        ctxs.append({
            "_rule_id":   "demand_unmet",
            "store_id":   sid,
            "store_name": s.get("name", sid),
            "demand":     float(s.get("demand", 0)),
            "delivered":  store_flow_map.get(sid, 0.0),
        })
    for f in factories:
        fid = str(f["id"])
        ctxs.append({
            "_rule_id":     "supply_exceeded",
            "factory_id":   fid,
            "factory_name": f.get("name", fid),
            "supply":       float(f.get("supply", 0)),
            "shipped":      factory_flow_map.get(fid, 0.0),
        })
    return ctxs


# ---------------------------------------------------------------------------
# TankAllocationPlanner context ビルダー（2026-08-30 新規）
# ---------------------------------------------------------------------------

def build_tank_allocation_capacity_contexts(
    tank_assignments: List[Dict],
) -> List[Dict[str, Any]]:
    """TankAllocationPlanner 用の容量チェック（O(n)）のcontextをタンク1件ごとに生成する。"""
    return [{"_rule_id": "capacity_violation", "ta": ta} for ta in tank_assignments]


def build_tank_allocation_incompatibility_contexts(
    tank_assignments: List[Dict],
    incompatible_pairs: List[Tuple[str, str]],
) -> List[Dict[str, Any]]:
    """
    TankAllocationPlanner 用の相性違反チェック（O(n²)、同一タンク内の
    ロット総当たり）のcontextを生成する。solvers.base.solution_checker.
    run_or_defer() でインスタンス規模に応じて同期/非同期を分岐させる想定。
    """
    incompatible_set: set = set()
    for a, b in incompatible_pairs:
        incompatible_set.add((a, b))
        incompatible_set.add((b, a))

    ctxs: List[Dict] = []
    for ta in tank_assignments:
        lots = ta["lots"]
        for i in range(len(lots)):
            for j in range(i + 1, len(lots)):
                ctxs.append({
                    "_rule_id":         "incompatible_pair_violation",
                    "tank_id":          ta["tank_id"],
                    "tank_name":        ta["tank_name"],
                    "lot_i":            lots[i],
                    "lot_j":            lots[j],
                    "incompatible_set": incompatible_set,
                })
    return ctxs


# ---------------------------------------------------------------------------
# AuctionWinnerSelector context ビルダー（2026-08-31 新規）
# ---------------------------------------------------------------------------

def build_auction_winner_selector_contexts(
    winners: List[Dict],
    min_revenue: float,
    total_revenue: float,
) -> List[Dict[str, Any]]:
    """
    AuctionWinnerSelector 用の商品ロット重複落札チェック（O(winners総item数)）と
    最低落札総額チェック（O(1)）のcontextを生成する。solver内部のx変数や
    mdlオブジェクトは一切参照せず、返ってきたwinnersのみから集計し直す。
    """
    item_winner_map: Dict[str, List[str]] = {}
    for w in winners:
        for item_id in w.get("item_ids", []):
            item_winner_map.setdefault(str(item_id), []).append(w["bid_id"])

    ctxs: List[Dict] = [
        {"_rule_id": "item_won_by_multiple_bids", "item_id": item_id, "winning_bid_ids": bid_ids}
        for item_id, bid_ids in item_winner_map.items()
    ]
    ctxs.append({
        "_rule_id":      "min_revenue_violated",
        "min_revenue":   min_revenue,
        "total_revenue": total_revenue,
    })
    return ctxs


# ---------------------------------------------------------------------------
# CapitalProjectSelector context ビルダー（2026-08-31 新規）
# ---------------------------------------------------------------------------

def build_capital_project_selector_contexts(
    total_weight: float,
    budget: float,
    selected_count: int,
    max_projects: Optional[int],
) -> List[Dict[str, Any]]:
    """
    CapitalProjectSelector 用の予算超過チェックと最大選定件数超過チェック（共にO(1)）の
    contextを生成する。solver内部のx変数やmdlオブジェクトは一切参照しない。
    """
    return [
        {"_rule_id": "budget_exceeded", "total_weight": total_weight, "budget": budget},
        {"_rule_id": "max_projects_exceeded", "selected_count": selected_count, "max_projects": max_projects},
    ]


# ---------------------------------------------------------------------------
# InventoryReplenishmentPlanner context ビルダー（2026-08-31 新規）
# ---------------------------------------------------------------------------

def build_inventory_replenishment_planner_contexts(
    shipments: List[Dict],
    centers: List[Dict],
    supply_capacity_per_period: float,
) -> List[Dict[str, Any]]:
    """
    InventoryReplenishmentPlanner 用の週次供給能力超過チェック（期ごとにO(拠点数)）と
    在庫フロー保存則チェック（拠点ごとに前期との1期比較、O(拠点数×期数)）の
    contextを生成する。solver内部のship[]/inv[]変数やmdlオブジェクトは一切参照せず、
    返ってきたshipments（center_id/period_id/period_index/quantity/demand/inventory_end
    を含む）のみから集計・再計算する。
    """
    ctxs: List[Dict] = []

    # 週次供給能力超過チェック（期ごとに集計）
    period_shipped: Dict[str, float] = {}
    for s in shipments:
        pid = s["period_id"]
        period_shipped[pid] = period_shipped.get(pid, 0.0) + float(s.get("quantity", 0.0))
    for pid, total_shipped in period_shipped.items():
        ctxs.append({
            "_rule_id":                   "supply_capacity_exceeded",
            "period_id":                  pid,
            "total_shipped":              total_shipped,
            "supply_capacity_per_period": supply_capacity_per_period,
        })

    # 在庫フロー保存則チェック（拠点ごとに period_index 順で1期ずつ比較）
    initial_inv_map: Dict[str, float] = {
        str(c["id"]): float(c.get("initial_inventory", 0.0)) for c in centers
    }
    by_center: Dict[str, List[Dict]] = {}
    for s in shipments:
        by_center.setdefault(s["center_id"], []).append(s)

    for center_id, center_shipments in by_center.items():
        ordered = sorted(center_shipments, key=lambda s: s.get("period_index", 0))
        prev_inv = initial_inv_map.get(str(center_id), 0.0)
        for s in ordered:
            expected_inv = prev_inv + float(s.get("quantity", 0.0)) - float(s.get("demand", 0.0))
            actual_inv = float(s.get("inventory_end", 0.0))
            ctxs.append({
                "_rule_id":     "inventory_flow_mismatch",
                "center_id":    center_id,
                "period_id":    s["period_id"],
                "expected_inv": expected_inv,
                "actual_inv":   actual_inv,
            })
            prev_inv = actual_inv  # 次期の起点は実際に返ってきた値を使う（1期ごとの独立検証）
    return ctxs


# ---------------------------------------------------------------------------
# MysteryShopperScheduler context ビルダー（2026-08-31 新規）
# ---------------------------------------------------------------------------

def build_mystery_shopper_scheduler_contexts(
    assignments: List[Dict],
) -> List[Dict[str, Any]]:
    """
    MysteryShopperScheduler 用の訪問枠重複割当チェックと調査員×日重複予約チェック
    （共にO(assignments件数)）のcontextを生成する。solver内部のx変数やmdlオブジェクトは
    一切参照せず、返ってきたassignmentsのみから集計し直す。
    """
    visit_map: Dict[str, int] = {}
    shopper_day_map: Dict[Tuple[str, str], int] = {}
    for a in assignments:
        visit_map[a["visit_id"]] = visit_map.get(a["visit_id"], 0) + 1
        key = (a["shopper_id"], a["day"])
        shopper_day_map[key] = shopper_day_map.get(key, 0) + 1

    ctxs: List[Dict] = []
    for visit_id, count in visit_map.items():
        ctxs.append({"_rule_id": "visit_double_assigned", "visit_id": visit_id, "assigned_count": count})
    for (shopper_id, day), count in shopper_day_map.items():
        ctxs.append({
            "_rule_id":      "shopper_day_double_booked",
            "shopper_id":    shopper_id,
            "day":           day,
            "assigned_count": count,
        })
    return ctxs


# ---------------------------------------------------------------------------
# NursingWorkloadBalance context ビルダー（2026-08-31 新規）
# ---------------------------------------------------------------------------

def build_nursing_workload_balance_contexts(
    nurse_workloads: List[Dict],
    nurses: List[Dict],
) -> List[Dict[str, Any]]:
    """
    NursingWorkloadBalance 用の担当患者数範囲チェックと負荷上限チェック
    （共にO(看護師数)）のcontextを生成する。solver内部のassign_vars/mdlオブジェクトは
    一切参照せず、返ってきたnurse_workloadsとDSL宣言のnurses（min/max設定値）のみから
    集計し直す。
    """
    nurse_config: Dict[str, Dict] = {str(n["id"]): n for n in nurses}
    default_max_patients = len(nurses)  # solver.py側のデフォルト(nPatients)相当は呼び出し側で上書き可

    ctxs: List[Dict] = []
    for w in nurse_workloads:
        nid = w["nurse_id"]
        n = nurse_config.get(nid, {})
        min_patients = int(n.get("minPatientsPerNurse", 1))
        max_patients = int(n.get("maxPatientsPerNurse", default_max_patients))
        max_workload = int(n.get("maxWorkloadPerNurse", 10000))
        ctxs.append({
            "_rule_id":     "nurse_patient_count_out_of_range",
            "nurse_id":     nid,
            "nurse_name":   w.get("nurse_name", nid),
            "patient_count": w.get("patient_count", 0),
            "min_patients": min_patients,
            "max_patients": max_patients,
        })
        ctxs.append({
            "_rule_id":     "nurse_workload_exceeded",
            "nurse_id":     nid,
            "nurse_name":   w.get("nurse_name", nid),
            "total_acuity": w.get("total_acuity", 0),
            "max_workload": max_workload,
        })
    return ctxs


# ---------------------------------------------------------------------------
# PortfolioOverlapDesigner context ビルダー（2026-08-31 新規）
# ---------------------------------------------------------------------------

def build_portfolio_overlap_designer_contexts(
    funds: List[Dict],
    assignments: Dict[str, List[str]],
    overlap_matrix: List[Dict],
    worst_overlap: Optional[int],
) -> List[Dict[str, Any]]:
    """
    PortfolioOverlapDesigner 用の組入銘柄数チェック（O(ファンド数)）と
    重複数KPI整合性チェック（O(ペア数)）のcontextを生成する。solver内部のx変数や
    mdlオブジェクトは一切参照せず、返ってきたassignments/overlap_matrix/worst_overlap
    のみから再計算する。
    """
    ctxs: List[Dict] = []
    for f in funds:
        fid = f["id"]
        selected = assignments.get(fid, [])
        ctxs.append({
            "_rule_id":       "selected_count_mismatch",
            "fund_id":        fid,
            "fund_name":      f.get("name", fid),
            "selected_count": len(selected),
            "required_count": f.get("required_count", 0),
        })

    actual_max_overlap = max((m.get("overlap_count", 0) for m in overlap_matrix), default=0)
    ctxs.append({
        "_rule_id":          "overlap_matrix_worst_overlap_mismatch",
        "actual_max_overlap": actual_max_overlap,
        "worst_overlap":      worst_overlap if worst_overlap is not None else actual_max_overlap,
    })
    return ctxs


# ---------------------------------------------------------------------------
# CarSequencing context ビルダー（2026-09-01 新規）
# ---------------------------------------------------------------------------

def build_car_sequencing_contexts(
    sequence_result: List[Dict],
    car_types: List[Dict],
) -> List[Dict[str, Any]]:
    """
    CarSequencing 用の車種別生産台数チェック（O(車種数)）のcontextを生成する。
    solver内部のseq変数やmdlオブジェクトは一切参照せず、返ってきた
    sequence_resultのみから車種別に再集計し、DSL宣言のcar_types[].countと
    突き合わせる。
    """
    actual_counts: Dict[str, int] = {}
    for item in sequence_result:
        tid = item["car_type_id"]
        actual_counts[tid] = actual_counts.get(tid, 0) + 1

    ctxs: List[Dict] = []
    for ct in car_types:
        tid = ct["car_type_id"]
        ctxs.append({
            "_rule_id":       "car_type_count_mismatch",
            "car_type_id":    tid,
            "car_type_name":  ct.get("car_type_name", tid),
            "actual_count":   actual_counts.get(tid, 0),
            "required_count": ct.get("count", 0),
        })
    return ctxs


# ---------------------------------------------------------------------------
# EnergyCostAwareScheduler context ビルダー（2026-09-01 新規）
# ---------------------------------------------------------------------------

# 電力チェックの許容誤差（kW）。CPO/CP-SATモデルは共に電力を0.1kW単位に
# 量子化してpulse/cumulativeの高さを整数化しているため（各solverファイル内の
# POWER_SCALEコメント参照）、返ってきた生の(浮動小数の)power_kwから検算すると
# 量子化丸めの分だけ理論上ずれ得る。作業員数・附帯設備枠は元々整数のため
# 量子化誤差はない。
_ENERGY_POWER_TOLERANCE_KW = 0.1

# (resource_type, order_field, capacity_field, order_field_default, label)
_ENERGY_RESOURCE_SPECS = [
    ("power",     "power_kw",        "max_power_kw",        0.0, "電力"),
    ("workers",   "workers",         "max_workers",         1.0, "作業員"),
    ("equipment", "equipment_slots", "max_equipment_slots", 1.0, "附帯設備"),
]


def build_energy_cost_aware_scheduler_contexts(
    schedule: List[Dict],
    line_ops: List[Dict],
    orders: List[Dict],
    lines: List[Dict],
) -> List[Dict[str, Any]]:
    """
    EnergyCostAwareScheduler 用の3チェックのcontextを生成する。solver内部の
    interval変数やmdlオブジェクトは一切参照せず、返ってきたschedule/line_ops
    と、DSL宣言のorders（workers/equipment_slots等の要求値。デフォルト値は
    solver本体の_build_and_solve()/_solve_with_cpsat()と揃えてある）・lines
    （容量上限）のみから検算する。

    (1) line_capacity_violation: ライン別・資源種別（電力/作業員/附帯設備）の
        同時使用量ピークをスイープライン検算（sweep_peak_usage、O(n log n)）
        し、容量上限と比較する。電力は待機電力（standby_power_kw、ライン
        稼働区間全体で消費）も含める。
    (2) order_outside_line_window: 各オーダーの稼働区間が、割り当てられた
        ラインの稼働区間（line_ops）に収まっているか（O(n)）。line_opsが
        見つからない場合はこのチェック対象から除く。
    (3) order_duplicate_assignment: 同一オーダーが複数ラインに重複割当されて
        いないか（O(n)）。
    """
    orders_map: Dict[str, Dict] = {str(o["id"]): o for o in orders}
    line_ops_map: Dict[str, Dict] = {op["line_id"]: op for op in line_ops}

    ctxs: List[Dict] = []

    # --- (1) ライン資源容量チェック ---
    for resource_type, order_field, capacity_field, default, label in _ENERGY_RESOURCE_SPECS:
        for ln in lines:
            lid = str(ln["id"])
            capacity = float(ln.get(capacity_field, 9999))
            events: List[tuple] = []
            for s in schedule:
                if s["line_id"] != lid:
                    continue
                o = orders_map.get(s["order_id"], {})
                amount = float(o.get(order_field, default))
                if amount <= 0:
                    continue
                events.append((s["start"], amount))
                events.append((s["end"], -amount))
            if resource_type == "power":
                standby = float(ln.get("standby_power_kw", 0.0))
                op = line_ops_map.get(lid)
                if standby > 0 and op is not None:
                    events.append((op["start"], standby))
                    events.append((op["end"], -standby))
            if not events:
                continue
            peak, peak_time = sweep_peak_usage(events)
            tolerance = _ENERGY_POWER_TOLERANCE_KW if resource_type == "power" else 1e-6
            ctxs.append({
                "_rule_id":        "line_capacity_violation",
                "resource_type":   resource_type,
                "resource_label":  label,
                "line_id":         lid,
                "line_name":       ln.get("name", lid),
                "peak":            peak,
                "capacity":        capacity,
                "peak_time":       peak_time,
                "tolerance":       tolerance,
            })

    # --- (2) オーダー稼働区間 ⊆ ライン稼働区間 ---
    for s in schedule:
        op = line_ops_map.get(s["line_id"])
        if op is None:
            continue
        ctxs.append({
            "_rule_id":    "order_outside_line_window",
            "order_id":    s["order_id"],
            "line_id":     s["line_id"],
            "order_start": s["start"],
            "order_end":   s["end"],
            "line_start":  op["start"],
            "line_end":    op["end"],
        })

    # --- (3) オーダー重複割当 ---
    order_count: Dict[str, int] = {}
    for s in schedule:
        order_count[s["order_id"]] = order_count.get(s["order_id"], 0) + 1
    for oid, count in order_count.items():
        ctxs.append({
            "_rule_id":       "order_duplicate_assignment",
            "order_id":       oid,
            "assigned_count": count,
        })

    return ctxs


# ---------------------------------------------------------------------------
# LotSizingScheduler context ビルダー（2026-09-01 新規）
# ---------------------------------------------------------------------------

def build_lot_sizing_scheduler_contexts(
    assignments: List[Dict],
) -> List[Dict[str, Any]]:
    """
    LotSizingScheduler 用の期間重複割当チェック（O(n)）のcontextを生成する。
    solver内部のap変数やmdlオブジェクトは一切参照せず、返ってきた
    assignmentsのみから期間別に再集計する。
    """
    period_count: Dict[Any, int] = {}
    for a in assignments:
        pid = a["assigned_period"]
        period_count[pid] = period_count.get(pid, 0) + 1

    return [
        {"_rule_id": "period_double_assigned", "period_id": pid, "assigned_count": count}
        for pid, count in period_count.items()
    ]


# ---------------------------------------------------------------------------
# MedicalAppointmentScheduler context ビルダー（2026-09-01 新規、バッチ4）
# ---------------------------------------------------------------------------

def build_medical_appointment_scheduler_contexts(
    assignments: List[Dict],
    requests:    List[Dict],
    resources:   List[Dict],
) -> List[Dict[str, Any]]:
    """
    MedicalAppointmentScheduler 用の独立検証contextを生成する。
    - resource_double_booking: 資源×日の同時割当件数をスイープラインで再計算し、
      no_overlap制約（資源1件=同時刻1件まで）を検算する。
    - resource_type_mismatch: 依頼の必要資源タイプと実際の割当資源タイプを突合する。
    - avoid_day_violation: 忌避日(avoid_days)への割当が無いか確認する。
    solver内部のvar/mdlは一切参照せず、返ってきたassignments・DSL入力のみを使う。
    """
    from collections import Counter

    requests_map:  Dict[str, Dict] = {str(r["id"]): r for r in requests}
    resource_map:  Dict[str, Dict] = {str(r["id"]): r for r in resources}
    ctxs: List[Dict] = []

    intervals_by_rd: Dict[Tuple[str, str], List[Tuple[int, int]]] = {}
    for a in assignments:
        day = a["day"]
        start, end = a["start_min"], a["end_min"]
        for ar in a.get("assigned_resources", []):
            key = (ar["resource_id"], day)
            intervals_by_rd.setdefault(key, []).append((start, end))

    for (rid, day), intervals in intervals_by_rd.items():
        if len(intervals) < 2:
            continue
        events: List[tuple] = []
        for start, end in intervals:
            events.append((start, 1))
            events.append((end, -1))
        peak, peak_time = sweep_peak_usage(events)
        ctxs.append({
            "_rule_id":      "resource_double_booking",
            "resource_id":   rid,
            "resource_name": resource_map.get(rid, {}).get("name", rid),
            "day":           day,
            "peak":          peak,
            "peak_time":     peak_time,
            "capacity":      1,
            "tolerance":     1e-9,
        })

    for a in assignments:
        req = requests_map.get(a["request_id"], {})
        needed_types = Counter(str(t) for t in req.get("resource_type_needs", []))
        assigned_types = Counter(
            str(resource_map.get(ar["resource_id"], {}).get("resource_type", ""))
            for ar in a.get("assigned_resources", [])
        )
        ctxs.append({
            "_rule_id":       "resource_type_mismatch",
            "request_id":     a["request_id"],
            "needed_types":   needed_types,
            "assigned_types": assigned_types,
        })
        ctxs.append({
            "_rule_id":   "avoid_day_violation",
            "request_id": a["request_id"],
            "day":        a["day"],
            "avoid_days": set(str(d) for d in req.get("avoid_days", [])),
        })
    return ctxs


# ---------------------------------------------------------------------------
# MedicalAppointmentSequenceScheduler context ビルダー（2026-09-01 新規、バッチ4）
# ---------------------------------------------------------------------------

def build_medical_appointment_sequence_scheduler_contexts(
    scheduled: List[Dict],
    sequences: List[Dict],
    resources: List[Dict],
) -> List[Dict[str, Any]]:
    """
    MedicalAppointmentSequenceScheduler (CSPLib prob091 MASSP) 用の独立検証contextを生成する。
    - resource_double_booking: 資源単位で開始/終了イベントをスイープし、同時使用数を検算する。
    - visit_outside_resource_window: 各受診の割当区間が、割り当てられた資源の稼働スロットに
      収まっているかを確認する。
    - sequence_gap_violation: 系列内の受診順序間隔（連続する受診間、およびinterval_rulesで
      明示された間隔）を検算する。interval_rulesのfrom_visit/to_visitはvisitのorder値で
      あり、visit_id文字列ではない点に注意（solver本体と同じvisit_by_order解決を踏襲）。
    - same_resource_rule_violation: same_resource_rulesで指定された2受診が、共通の資源タイプ
      について同一資源を使っているかを確認する。
    - visit_resource_time_mismatch（任意チェック）: 1受診に複数資源が割り当てられている場合、
      各資源のstart_min/end_minが一致しているかを確認する。solver側は受診レベルの
      start_min/end_minをassigned_resourcesのmin/maxで集約しているため、個々の資源の
      時刻ズレはそのままでは検出できない。これを独立に検算する。
    """
    resources_map:    Dict[str, Dict] = {str(r["id"]): r for r in resources}
    scheduled_seq_map: Dict[str, Dict] = {s["sequence_id"]: s for s in scheduled}
    ctxs: List[Dict] = []

    events_by_resource: Dict[str, List[tuple]] = {}
    for s in scheduled:
        for v in s["visits"]:
            for ar in v.get("assigned_resources", []):
                rid = ar["resource_id"]
                events_by_resource.setdefault(rid, []).append((ar["start_min"], 1))
                events_by_resource.setdefault(rid, []).append((ar["end_min"], -1))
    for rid, events in events_by_resource.items():
        peak, peak_time = sweep_peak_usage(events)
        ctxs.append({
            "_rule_id":      "resource_double_booking",
            "resource_id":   rid,
            "resource_name": resources_map.get(rid, {}).get("name", rid),
            "peak":          peak,
            "peak_time":     peak_time,
        })

    for s in scheduled:
        for v in s["visits"]:
            for ar in v.get("assigned_resources", []):
                rid = ar["resource_id"]
                res = resources_map.get(rid, {})
                ctxs.append({
                    "_rule_id":        "visit_outside_resource_window",
                    "visit_id":        v["visit_id"],
                    "resource_id":     rid,
                    "resource_name":   res.get("name", rid),
                    "start_min":       ar["start_min"],
                    "end_min":         ar["end_min"],
                    "available_slots": res.get("available_slots", []),
                })

    for s in scheduled:
        for v in s["visits"]:
            if len(v.get("assigned_resources", [])) > 1:
                ctxs.append({
                    "_rule_id":  "visit_resource_time_mismatch",
                    "visit_id":  v["visit_id"],
                    "assigned_resources": v["assigned_resources"],
                })

    for seq in sequences:
        seq_id = seq["id"]
        sdata = scheduled_seq_map.get(seq_id)
        if sdata is None:
            continue
        dsl_visits = sorted(seq.get("visits", []), key=lambda v: v.get("order", 0))
        visit_by_order = {v.get("order", i): v for i, v in enumerate(dsl_visits)}
        sched_by_id = {v["visit_id"]: v for v in sdata["visits"]}
        sched_sorted = sorted(sdata["visits"], key=lambda v: v["visit_order"])

        for vi, vj in zip(sched_sorted, sched_sorted[1:]):
            ctxs.append({
                "_rule_id":      "sequence_gap_violation",
                "sequence_id":   seq_id,
                "from_visit_id": vi["visit_id"],
                "to_visit_id":   vj["visit_id"],
                "from_end":      vi["end_min"],
                "to_start":      vj["start_min"],
                "min_gap":       0,
                "max_gap":       None,
                "rule_label":    "順序制約",
            })

        for rule in seq.get("interval_rules", []):
            v_from = visit_by_order.get(rule.get("from_visit"))
            v_to   = visit_by_order.get(rule.get("to_visit"))
            if v_from is None or v_to is None:
                continue
            sf, st = sched_by_id.get(v_from["id"]), sched_by_id.get(v_to["id"])
            if sf is None or st is None:
                continue
            min_gap, max_gap = rule.get("min_gap", 0), rule.get("max_gap", None)
            if rule.get("unit", "min") == "day":
                min_gap = min_gap * 24 * 60
                max_gap = max_gap * 24 * 60 if max_gap is not None else None
            ctxs.append({
                "_rule_id":      "sequence_gap_violation",
                "sequence_id":   seq_id,
                "from_visit_id": v_from["id"],
                "to_visit_id":   v_to["id"],
                "from_end":      sf["end_min"],
                "to_start":      st["start_min"],
                "min_gap":       min_gap,
                "max_gap":       max_gap,
                "rule_label":    f"interval_rule({rule.get('from_visit')}->{rule.get('to_visit')})",
            })

        for rule in seq.get("same_resource_rules", []):
            v_a = visit_by_order.get(rule.get("visit_a"))
            v_b = visit_by_order.get(rule.get("visit_b"))
            if v_a is None or v_b is None:
                continue
            sa, sb = sched_by_id.get(v_a["id"]), sched_by_id.get(v_b["id"])
            if sa is None or sb is None:
                continue
            shared_types = (
                {r["type"] for r in v_a.get("required_resources", [])}
                & {r["type"] for r in v_b.get("required_resources", [])}
            )
            if not shared_types:
                continue
            ctxs.append({
                "_rule_id":    "same_resource_rule_violation",
                "sequence_id": seq_id,
                "visit_a_id":  v_a["id"],
                "visit_b_id":  v_b["id"],
                "shared_resource_ids_a": {
                    ar["resource_id"] for ar in sa.get("assigned_resources", [])
                    if ar["resource_type"] in shared_types
                },
                "shared_resource_ids_b": {
                    ar["resource_id"] for ar in sb.get("assigned_resources", [])
                    if ar["resource_type"] in shared_types
                },
            })
    return ctxs


# ---------------------------------------------------------------------------
# PatientTransportPlanner context ビルダー（2026-09-01 新規、バッチ4）
# ---------------------------------------------------------------------------

def build_patient_transport_planner_contexts(
    request_results: List[Dict],
    requests:        List[Dict],
    vehicles:        List[Dict],
    config:          Dict,
) -> List[Dict[str, Any]]:
    """
    PatientTransportPlanner 用の独立検証contextを生成する。
    - vehicle_capacity_violation: 車両ごとにcapacity_requiredの同時使用量をスイープラインで
      再計算し、車両定員(capacity)を超過していないか検算する。
    - vehicle_phase_overlap: 車両に割り当てられたフェーズ（往路/復路）が時間的に重複して
      いないかを検算する（no_overlap制約の独立検証）。
    - roundtrip_order_violation: 往復依頼で、復路開始が往路終了+診察時間より後になっている
      かを確認する。
    - phase_outside_time_window（任意チェック）: solver本体の_compute_phase_metaと同じ式で
      往路/復路それぞれの許容時間窓を再計算し、実際の開始時刻がその範囲内かを確認する。
    solver内部のinterval_var/mdlは一切参照せず、返ってきたrequest_results・DSL入力のみを使う。
    """
    import math

    requests_map = {str(r["id"]): r for r in requests}
    vehicles_map = {str(v["id"]): v for v in vehicles}
    ctxs: List[Dict] = []

    phases_by_vehicle: Dict[str, List[Dict]] = {}
    for rr in request_results:
        req_id = rr["request_id"]
        req = requests_map.get(req_id, {})
        cap_req = int(req.get("capacity_required", 1))
        if "outbound_vehicle" in rr:
            phases_by_vehicle.setdefault(rr["outbound_vehicle"], []).append({
                "req_id": req_id, "phase": "outbound",
                "start": rr["outbound_start"], "end": rr["outbound_end"],
                "capacity_required": cap_req,
            })
        if "inbound_vehicle" in rr:
            phases_by_vehicle.setdefault(rr["inbound_vehicle"], []).append({
                "req_id": req_id, "phase": "inbound",
                "start": rr["inbound_start"], "end": rr["inbound_end"],
                "capacity_required": cap_req,
            })

    for vid, phases in phases_by_vehicle.items():
        v = vehicles_map.get(vid, {})
        vname = v.get("name", vid)
        cap_events = [(p["start"], p["capacity_required"]) for p in phases] +                      [(p["end"], -p["capacity_required"]) for p in phases]
        peak, peak_time = sweep_peak_usage(cap_events)
        ctxs.append({
            "_rule_id":    "vehicle_capacity_violation",
            "vehicle_id":  vid,
            "vehicle_name": vname,
            "peak":        peak,
            "capacity":    int(v.get("capacity", 4)),
            "peak_time":   peak_time,
        })
        overlap_events = [(p["start"], 1) for p in phases] + [(p["end"], -1) for p in phases]
        opeak, opeak_time = sweep_peak_usage(overlap_events)
        ctxs.append({
            "_rule_id":     "vehicle_phase_overlap",
            "vehicle_id":   vid,
            "vehicle_name": vname,
            "peak":         opeak,
            "peak_time":    opeak_time,
        })

    for rr in request_results:
        if rr.get("req_type") != "roundtrip":
            continue
        if "outbound_end" not in rr or "inbound_start" not in rr:
            continue
        req = requests_map.get(rr["request_id"], {})
        ctxs.append({
            "_rule_id":          "roundtrip_order_violation",
            "request_id":        rr["request_id"],
            "outbound_end":      rr["outbound_end"],
            "inbound_start":     rr["inbound_start"],
            "exam_duration_min": int(req.get("exam_duration_min", 60)),
        })

    speed_kmh = float(config.get("speed_kmh", 30.0))
    default_boarding_min = int(config.get("boarding_time_min", 3))
    hospital_loc = config.get("hospital_location", {"lat": 0.0, "lng": 0.0})

    def _travel_min(a, b):
        dlat = abs(b.get("lat", 0.0) - a.get("lat", 0.0)) * 111.0
        dlng = abs(b.get("lng", 0.0) - a.get("lng", 0.0)) * 111.0 *             math.cos(math.radians((a.get("lat", 0.0) + b.get("lat", 0.0)) / 2))
        return max(1, math.ceil(math.sqrt(dlat ** 2 + dlng ** 2) / speed_kmh * 60))

    def _hhmm(s):
        h, m = str(s).split(":")
        return int(h) * 60 + int(m)

    for rr in request_results:
        req = requests_map.get(rr["request_id"], {})
        if not req:
            continue
        home = req.get("home_location", {"lat": 0.0, "lng": 0.0})
        appt = _hhmm(req.get("appointment_time", "09:00"))
        wait_tol = int(req.get("wait_tolerance_min", 30))
        exam_dur = int(req.get("exam_duration_min", 60))
        boarding = int(req.get("boarding_time_min", default_boarding_min))

        if "outbound_start" in rr:
            trav = _travel_min(home, hospital_loc)
            tw_start = max(0, appt - wait_tol - trav - boarding)
            tw_end = max(tw_start + 1, appt - trav - boarding)
            ctxs.append({
                "_rule_id":   "phase_outside_time_window",
                "request_id": rr["request_id"],
                "phase":      "outbound",
                "start":      rr["outbound_start"],
                "tw_start":   tw_start,
                "tw_end":     tw_end,
            })
        if "inbound_start" in rr:
            tw_start = appt + exam_dur
            tw_end = max(tw_start + 1, tw_start + wait_tol)
            ctxs.append({
                "_rule_id":   "phase_outside_time_window",
                "request_id": rr["request_id"],
                "phase":      "inbound",
                "start":      rr["inbound_start"],
                "tw_start":   tw_start,
                "tw_end":     tw_end,
            })
    return ctxs


# ---------------------------------------------------------------------------
# ProductionLineSequencing context ビルダー（2026-09-01 新規、バッチ5）
# ---------------------------------------------------------------------------

def build_production_line_sequencing_contexts(
    assignments:              List[Dict],
    batches:                  List[Dict],
    slots:                    List[Dict],
    vehicle_types:            List[Dict],
    distribution_exceptions:  List[Dict],
) -> List[Dict[str, Any]]:
    """
    ProductionLineSequencing 用の独立検証contextを生成する。
    solver内部のbatch_vars/mdl/cpmpyモデルは一切参照せず、返ってきた
    assignmentsとDSL入力(batches/vehicle_types/distribution_exceptions)
    のみから再計算する。
    """
    batch_by_id = {b["id"]: b for b in batches}
    slot_by_id = {s["id"]: s for s in slots}
    vtype_daily_limit = {vt["id"]: vt.get("daily_limit", 99) for vt in vehicle_types}

    exception_map: Dict[Tuple[Any, str], Dict] = {}
    for exc in distribution_exceptions:
        for d in exc.get("period_days", []):
            exception_map[(d, exc["vehicle_type_id"])] = exc

    ctxs: List[Dict[str, Any]] = []

    slot_count: Dict[Any, int] = {}
    for a in assignments:
        slot_count[a["slot_id"]] = slot_count.get(a["slot_id"], 0) + 1
    for sid, count in slot_count.items():
        ctxs.append({"_rule_id": "slot_double_booked", "slot_id": sid, "assigned_count": count})

    day_vtype_count: Dict[Tuple[Any, str], int] = {}
    hours_by_day_vtype: Dict[Tuple[Any, str], List[float]] = {}

    for a in assignments:
        bid = a["batch_id"]
        batch = batch_by_id.get(bid, {})
        compatible_lines = batch.get("compatible_lines")
        line_on = batch.get("line_on_day", 1)
        line_off = batch.get("line_off_day", 9999)
        slot = slot_by_id.get(a["slot_id"])

        ctxs.append({
            "_rule_id": "batch_incompatible_line",
            "batch_id": bid, "assigned_line_id": a["line_id"],
            "compatible_lines": compatible_lines,
        })
        ctxs.append({
            "_rule_id": "batch_outside_line_window",
            "batch_id": bid, "assigned_day": a["day"],
            "line_on_day": line_on, "line_off_day": line_off,
        })
        ctxs.append({
            "_rule_id": "assignment_unknown_slot",
            "batch_id": bid, "slot_id": a["slot_id"], "slot": slot,
        })
        if slot is not None:
            ctxs.append({
                "_rule_id": "assignment_field_mismatch",
                "batch_id": bid, "slot_id": a["slot_id"],
                "assign_line_id": a["line_id"], "assign_day": a["day"],
                "assign_start_hour": a["start_hour"],
                "slot_line_id": slot["line_id"], "slot_day": slot["day"],
                "slot_start_hour": slot["start_hour"],
            })
        ctxs.append({
            "_rule_id": "assignment_vehicle_type_mismatch",
            "batch_id": bid,
            "assign_vtype": a.get("vehicle_type", ""),
            "batch_vtype": batch.get("vehicle_type", ""),
        })

        key = (a["day"], a.get("vehicle_type", ""))
        day_vtype_count[key] = day_vtype_count.get(key, 0) + 1
        hours_by_day_vtype.setdefault(key, []).append(a["start_hour"])

    for (day, vtid), count in day_vtype_count.items():
        exc = exception_map.get((day, vtid))
        base_limit = vtype_daily_limit.get(vtid, 99)
        effective_max = exc.get("max_per_day", base_limit) if exc else base_limit
        ctxs.append({
            "_rule_id": "distribution_daily_max_violation",
            "day": day, "vtype_id": vtid, "count": count,
            "effective_max": effective_max,
            "exception_applied": bool(exc and "max_per_day" in exc),
        })

    for (day, vtid), exc in exception_map.items():
        if "min_per_day" not in exc:
            continue
        count = day_vtype_count.get((day, vtid), 0)
        ctxs.append({
            "_rule_id": "distribution_daily_min_violation",
            "day": day, "vtype_id": vtid, "count": count,
            "effective_min": exc["min_per_day"],
        })

    sorted_vtypes = sorted(vehicle_types, key=lambda v: v.get("priority_order", 99))
    days_present = sorted({a["day"] for a in assignments})
    for day in days_present:
        for i in range(len(sorted_vtypes) - 1):
            vt_h, vt_l = sorted_vtypes[i], sorted_vtypes[i + 1]
            hours_h = hours_by_day_vtype.get((day, vt_h["id"]))
            hours_l = hours_by_day_vtype.get((day, vt_l["id"]))
            if not hours_h or not hours_l:
                continue
            ctxs.append({
                "_rule_id": "batting_order_violation",
                "day": day,
                "vt_higher_id": vt_h["id"], "vt_lower_id": vt_l["id"],
                "max_higher_hour": max(hours_h), "min_lower_hour": min(hours_l),
            })

    return ctxs


# ---------------------------------------------------------------------------
# RideshareMatchingPlanner context ビルダー（2026-09-01 新規、バッチ5）
# ---------------------------------------------------------------------------

def build_rideshare_matching_planner_contexts(
    matched_pairs:        List[Dict],
    unmatched_passengers: List[Dict],
    driver_routes:        Any,   # dict-of-dicts または list-of-dicts のどちらでも受け付ける
    passengers:            List[Dict],
    config:                Dict,
) -> List[Dict[str, Any]]:
    """
    RideshareMatchingPlanner 用の独立検証contextを生成する。
    solver内部のinterval_var/mdl/sequence_varは一切参照せず、返ってきた
    matched_pairs・unmatched_passengers・driver_routes・DSL入力のみを使う。
    """
    import math
    from collections import Counter

    ctxs: List[Dict[str, Any]] = []

    routes = list(driver_routes.values()) if isinstance(driver_routes, dict) else driver_routes

    for dr in routes:
        events = [(mp["pickup_min"], 1) for mp in dr["passengers"]] + \
                 [(mp["dropoff_min"], -1) for mp in dr["passengers"]]
        peak, peak_time = sweep_peak_usage(events)
        ctxs.append({
            "_rule_id":    "seat_capacity_violation",
            "driver_id":   dr["driver_id"],
            "driver_name": dr["driver_name"],
            "peak":        peak,
            "seats":       dr["seats"],
            "peak_time":   peak_time,
        })
        for mp in dr["passengers"]:
            ctxs.append({
                "_rule_id":       "driver_operating_window_violation",
                "driver_id":      dr["driver_id"],
                "driver_name":    dr["driver_name"],
                "passenger_id":   mp["passenger_id"],
                "passenger_name": mp["passenger_name"],
                "pickup_min":     mp["pickup_min"],
                "dropoff_min":    mp["dropoff_min"],
                "depart_min":     dr["depart_min"],
                "arrive_max":     dr["arrive_max"],
            })

    detour_factor = float(config.get("detour_factor", 1.5))
    for mp in matched_pairs:
        max_ride_min = int(math.ceil(mp["direct_min"] * detour_factor))
        ctxs.append({
            "_rule_id":       "detour_time_violation",
            "passenger_id":   mp["passenger_id"],
            "passenger_name": mp["passenger_name"],
            "ride_min":       mp["ride_min"],
            "direct_min":     mp["direct_min"],
            "detour_factor":  detour_factor,
            "max_ride_min":   max_ride_min,
        })
        ctxs.append({
            "_rule_id":       "pickup_dropoff_order_violation",
            "passenger_id":   mp["passenger_id"],
            "passenger_name": mp["passenger_name"],
            "ride_min":       mp["ride_min"],
            "pickup_min":     mp["pickup_min"],
            "dropoff_min":    mp["dropoff_min"],
        })
        ctxs.append({
            "_rule_id":         "passenger_time_window_violation",
            "passenger_id":     mp["passenger_id"],
            "passenger_name":   mp["passenger_name"],
            "pickup_min":       mp["pickup_min"],
            "window_start_min": mp["window_start_min"],
            "window_end_min":   mp["window_end_min"],
        })

    assign_counts = Counter(mp["passenger_id"] for mp in matched_pairs)
    for pid, count in assign_counts.items():
        if count > 1:
            ctxs.append({
                "_rule_id":       "passenger_duplicate_assignment",
                "passenger_id":   pid,
                "assigned_count": count,
            })

    matched_ids = {mp["passenger_id"] for mp in matched_pairs}
    unmatched_ids = {p.get("id", "") for p in unmatched_passengers}
    for p in passengers:
        pid = p.get("id", "")
        occurrence_count = (1 if pid in matched_ids else 0) + (1 if pid in unmatched_ids else 0)
        ctxs.append({
            "_rule_id":         "passenger_missing_from_output",
            "passenger_id":     pid,
            "occurrence_count": occurrence_count,
        })

    return ctxs


# ---------------------------------------------------------------------------
# ShiftRotationScheduler context ビルダー（2026-09-01 新規、バッチ5）
# ---------------------------------------------------------------------------

def _shift_rotation_cyclic_runs(seq: List[str]) -> List[Tuple[int, int, str]]:
    """(run_start_index, run_length, value) を、円環状seqの全極大runについて返す。"""
    n = len(seq)
    if n == 0:
        return []
    if all(v == seq[0] for v in seq):
        return [(0, n, seq[0])]
    b = next(i for i in range(n) if seq[i] != seq[i - 1])
    runs: List[Tuple[int, int, str]] = []
    i, seen = b, 0
    while seen < n:
        val = seq[i % n]
        start, length = i % n, 0
        while seq[i % n] == val and length < n:
            i += 1
            length += 1
            seen += 1
        runs.append((start, length, val))
    return runs


def build_shift_rotation_scheduler_contexts(
    template_shift_ids: List[List[str]],   # solution["template_shift_ids"], W x 7
    employee_schedules: List[Dict],        # solution["employee_schedules"]
    shifts_def:         List[Dict],        # dsl["shifts"]
    daily_requirements: List[Dict],        # dsl["daily_requirements"]
    constraints_cfg:    Dict,              # dsl["constraints"]
) -> List[Dict[str, Any]]:
    """
    ShiftRotationScheduler 用の独立検証contextを生成する。solver内部の
    CpoModel/cpmpy変数は一切参照せず、返ってきたtemplate_shift_ids /
    employee_schedulesと、DSL宣言のshifts/daily_requirements/constraintsの
    みから検算する。デフォルト値はsolver本体
    （_solve_with_cpo()/_solve_with_cpsat()）と揃えてある
    （min_consecutive=2, max_consecutive=4, min_days_off_per_14=2）。
    """
    min_consec     = int(constraints_cfg.get("min_consecutive", 2))
    max_consec     = int(constraints_cfg.get("max_consecutive", 4))
    min_off_per_14 = int(constraints_cfg.get("min_days_off_per_14", 2))

    W = len(template_shift_ids)
    total_days = W * 7
    known_shift_ids = {s["id"] for s in shifts_def}

    ctxs: List[Dict[str, Any]] = []

    for w in range(W):
        ctxs.append({
            "_rule_id": "weekend_shift_mismatch", "week": w,
            "sat_shift_id": template_shift_ids[w][5],
            "sun_shift_id": template_shift_ids[w][6],
        })

    counts: Dict[int, Dict[int, Dict[str, int]]] = {
        w: {d: {} for d in range(7)} for w in range(W)
    }
    for emp in employee_schedules:
        for w, week_row in enumerate(emp["weeks"]):
            for d, day in enumerate(week_row):
                sid = day["shift_id"]
                counts[w][d][sid] = counts[w][d].get(sid, 0) + 1
    for w in range(W):
        for req in daily_requirements:
            shift_id = req["shift_id"]
            if shift_id not in known_shift_ids:
                continue
            d = int(req["day_of_week"])
            ctxs.append({
                "_rule_id": "daily_requirement_mismatch",
                "week": w, "day_of_week": d, "shift_id": shift_id,
                "actual_count": counts[w][d].get(shift_id, 0),
                "required_count": int(req["required"]),
            })

    for k, emp in enumerate(employee_schedules):
        eid = emp["employee_id"]
        flat = [day for week_row in emp["weeks"] for day in week_row]

        for i in range(total_days):
            cur, prev = flat[i], flat[i - 1]
            if cur["shift_type"] == "off" or prev["shift_type"] == "off":
                continue
            order_cur = _SRS_SHIFT_TYPE_ORDER.get(cur["shift_type"], 0)
            order_prev = _SRS_SHIFT_TYPE_ORDER.get(prev["shift_type"], 0)
            wk_cur, dow_cur = divmod(i, 7)
            wk_prev, dow_prev = divmod((i - 1) % total_days, 7)
            ctxs.append({
                "_rule_id": "shift_progression_violation",
                "employee_id": eid, "day_index": i,
                "order_cur": order_cur, "order_prev": order_prev,
                "shift_type_cur": cur["shift_type"], "shift_type_prev": prev["shift_type"],
                "week_cur": wk_cur, "dow_cur": dow_cur,
                "week_prev": wk_prev, "dow_prev": dow_prev,
            })

        shift_id_seq = [day["shift_id"] for day in flat]
        for start, length, sid in _shift_rotation_cyclic_runs(shift_id_seq):
            ctxs.append({
                "_rule_id": "consecutive_run_length_violation",
                "employee_id": eid, "run_start_day": start, "run_length": length,
                "shift_id": sid, "min_consecutive": min_consec, "max_consecutive": max_consec,
            })

        for i in range(total_days):
            off_count = sum(
                1 for j in range(14) if flat[(i + j) % total_days]["shift_type"] == "off"
            )
            ctxs.append({
                "_rule_id": "days_off_window_violation",
                "employee_id": eid, "window_start_day": i,
                "off_count": off_count, "min_days_off_per_14": min_off_per_14,
            })

        for w in range(W):
            for d in range(7):
                ctxs.append({
                    "_rule_id": "employee_schedule_derivation_mismatch",
                    "employee_id": eid, "week": w, "day_of_week": d,
                    "actual_shift_id": emp["weeks"][w][d]["shift_id"],
                    "expected_shift_id": template_shift_ids[(w + k) % W][d],
                })

    return ctxs


# ---------------------------------------------------------------------------
# SteelMillSlabDesign context ビルダー（2026-09-01 新規、バッチ6・最終）
# ---------------------------------------------------------------------------

def build_steel_mill_slab_design_contexts(
    assignments: List[Dict],
    slabs_used:  List[Dict],
    orders:      List[Dict],
) -> List[Dict[str, Any]]:
    """
    SteelMillSlabDesign 用の独立検証contextを生成する。solver内部の
    CpoModel/cpmpy変数は一切参照せず、返ってきたassignments/slabs_usedと
    DSL入力(orders)のみから独立に再計算する。order_idの導出はsolverの
    解抽出処理（str(o.get("id", i))）と揃えてある。
    """
    ctxs: List[Dict[str, Any]] = []

    order_ids = [str(o.get("id", i)) for i, o in enumerate(orders)]

    assign_count: Dict[str, int] = {}
    weight_by_slab: Dict[int, int] = {}
    colors_by_slab: Dict[int, set] = {}
    for a in assignments:
        oid = a["order_id"]
        assign_count[oid] = assign_count.get(oid, 0) + 1
        j = a["slab_index"]
        weight_by_slab[j] = weight_by_slab.get(j, 0) + int(a["weight"])
        colors_by_slab.setdefault(j, set()).update(a.get("colors", []))

    for oid in order_ids:
        count = assign_count.get(oid, 0)
        ctxs.append({"_rule_id": "order_duplicate_assignment", "order_id": oid, "assigned_count": count})
        ctxs.append({"_rule_id": "order_missing_from_output", "order_id": oid, "assigned_count": count})

    for s in slabs_used:
        j = s["slab_index"]
        recomputed_weight = weight_by_slab.get(j, 0)
        ctxs.append({
            "_rule_id": "slab_capacity_exceeded",
            "slab_index": j, "recomputed_weight": recomputed_weight, "capacity": s["capacity"],
        })
        colors = colors_by_slab.get(j, set())
        ctxs.append({
            "_rule_id": "slab_color_limit_exceeded",
            "slab_index": j, "recomputed_colors": sorted(colors), "color_count": len(colors),
        })
        recomputed_waste = s["capacity"] - recomputed_weight
        ctxs.append({
            "_rule_id": "slab_used_weight_mismatch",
            "slab_index": j,
            "recomputed_weight": recomputed_weight, "reported_weight": s["used_weight"],
            "recomputed_waste": recomputed_waste, "reported_waste": s["waste_weight"],
        })

    return ctxs


# ---------------------------------------------------------------------------
# VesselDeckLoader context ビルダー（2026-09-01 新規、バッチ6・最終）
# ---------------------------------------------------------------------------

def build_vessel_deck_loader_contexts(
    placements:  List[Dict],
    Z_val:       int,
    deck_width:  int,
    hazard_margin: int,
    containers:  List[Dict],
) -> List[Dict[str, Any]]:
    """
    VesselDeckLoader 用の独立検証contextを生成する。solver内部の
    CpoModel/cpmpy決定変数は一切参照せず、返ってきたplacementsとDSL入力
    (containers/deck/config)のみから独立に再計算する。container_unsupported
    の判定はsolverの支持制約ロジック（西壁/南壁/北壁 or 先行コンテナとの
    境界接触、load_orderで安定ソート）を独立に再実装したもの。
    """
    ctxs: List[Dict[str, Any]] = []

    placement_count: Dict[str, int] = {}
    for p in placements:
        placement_count[p["container_id"]] = placement_count.get(p["container_id"], 0) + 1
    for c in containers:
        cid = c["id"]
        ctxs.append({
            "_rule_id": "container_missing_from_placement",
            "container_id": cid,
            "occurrence_count": placement_count.get(cid, 0),
        })

    n = len(placements)
    for i in range(n):
        pi = placements[i]
        for j in range(i + 1, n):
            pj = placements[j]
            mg = hazard_margin if (pi["is_hazardous"] and pj["is_hazardous"]) else 0
            x_sep = (pi["x"] + pi["length"] + mg <= pj["x"] or
                     pj["x"] + pj["length"] + mg <= pi["x"])
            y_sep = (pi["y"] + pi["width"] + mg <= pj["y"] or
                     pj["y"] + pj["width"] + mg <= pi["y"])
            ctxs.append({
                "_rule_id": "container_overlap_violation",
                "cid_a": pi["container_id"], "cid_b": pj["container_id"],
                "name_a": pi["container_name"], "name_b": pj["container_name"],
                "separated": x_sep or y_sep,
                "margin": mg,
            })

    order_sorted = sorted(placements, key=lambda p: p["load_order"])
    for rank, pk in enumerate(order_sorted):
        if rank == 0:
            continue
        lk, wk = pk["length"], pk["width"]
        supported = (
            pk["x"] == 0
            or pk["y"] == 0
            or pk["y"] + wk == deck_width
        )
        if not supported:
            for pj in order_sorted[:rank]:
                lj, wj = pj["length"], pj["width"]
                contact_east = (pk["x"] == pj["x"] + lj and pj["y"] < pk["y"] + wk and pk["y"] < pj["y"] + wj)
                contact_west = (pj["x"] == pk["x"] + lk and pj["y"] < pk["y"] + wk and pk["y"] < pj["y"] + wj)
                contact_south = (pk["y"] == pj["y"] + wj and pj["x"] < pk["x"] + lk and pk["x"] < pj["x"] + lj)
                contact_north = (pj["y"] == pk["y"] + wk and pj["x"] < pk["x"] + lk and pk["x"] < pj["x"] + lj)
                if contact_east or contact_west or contact_south or contact_north:
                    supported = True
                    break
        ctxs.append({
            "_rule_id": "container_unsupported",
            "container_id": pk["container_id"], "container_name": pk["container_name"],
            "supported": supported,
        })

    max_extent = max((p["x"] + p["length"] for p in placements), default=0)
    ctxs.append({
        "_rule_id": "used_length_mismatch",
        "reported_z": Z_val, "recomputed_z": max_extent,
    })

    return ctxs
