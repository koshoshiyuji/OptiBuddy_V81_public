
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable, Dict, List, Optional, Tuple

from solvers.base.solution_checker import solver_bug_issue, sweep_peak_usage
from i18n.common_messages import t as _common_t
from i18n.nurse_shift_weekly_cap_messages import t as _nurse_shift_t

from ._common import IssueRule, _rule




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
