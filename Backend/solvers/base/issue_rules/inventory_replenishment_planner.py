
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable, Dict, List, Optional, Tuple

from solvers.base.solution_checker import solver_bug_issue, sweep_peak_usage
from i18n.common_messages import t as _common_t
from i18n.nurse_shift_weekly_cap_messages import t as _nurse_shift_t

from ._common import IssueRule, _rule




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
