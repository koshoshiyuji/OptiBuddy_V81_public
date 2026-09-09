
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable, Dict, List, Optional, Tuple

from solvers.base.solution_checker import solver_bug_issue, sweep_peak_usage
from i18n.common_messages import t as _common_t
from i18n.nurse_shift_weekly_cap_messages import t as _nurse_shift_t

from ._common import IssueRule, _rule




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
