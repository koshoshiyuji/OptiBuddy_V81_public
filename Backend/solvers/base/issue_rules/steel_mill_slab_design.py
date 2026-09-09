
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable, Dict, List, Optional, Tuple

from solvers.base.solution_checker import solver_bug_issue, sweep_peak_usage
from i18n.common_messages import t as _common_t
from i18n.nurse_shift_weekly_cap_messages import t as _nurse_shift_t

from ._common import IssueRule, _rule




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
