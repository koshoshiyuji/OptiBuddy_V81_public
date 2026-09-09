
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable, Dict, List, Optional, Tuple

from solvers.base.solution_checker import solver_bug_issue, sweep_peak_usage
from i18n.common_messages import t as _common_t
from i18n.nurse_shift_weekly_cap_messages import t as _nurse_shift_t

from ._common import IssueRule, _rule




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
