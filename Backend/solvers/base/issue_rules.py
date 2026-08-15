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
from typing import Any, Callable, Dict, List, Optional

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


