
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable, Dict, List, Optional, Tuple

from solvers.base.solution_checker import solver_bug_issue, sweep_peak_usage
from i18n.common_messages import t as _common_t
from i18n.nurse_shift_weekly_cap_messages import t as _nurse_shift_t

from ._common import IssueRule, _rule




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
