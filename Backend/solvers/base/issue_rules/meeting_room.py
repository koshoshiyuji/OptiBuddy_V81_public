
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable, Dict, List, Optional, Tuple

from solvers.base.solution_checker import solver_bug_issue, sweep_peak_usage
from i18n.common_messages import t as _common_t
from i18n.nurse_shift_weekly_cap_messages import t as _nurse_shift_t

from ._common import IssueRule, _rule




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
