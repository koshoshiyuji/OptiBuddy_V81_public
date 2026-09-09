
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable, Dict, List, Optional, Tuple

from solvers.base.solution_checker import solver_bug_issue, sweep_peak_usage
from i18n.common_messages import t as _common_t
from i18n.nurse_shift_weekly_cap_messages import t as _nurse_shift_t

from ._common import IssueRule, _rule




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
