
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable, Dict, List, Optional, Tuple

from solvers.base.solution_checker import solver_bug_issue, sweep_peak_usage
from i18n.common_messages import t as _common_t
from i18n.nurse_shift_weekly_cap_messages import t as _nurse_shift_t

from ._common import IssueRule, _rule




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
