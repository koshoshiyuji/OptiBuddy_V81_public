
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable, Dict, List, Optional, Tuple

from solvers.base.solution_checker import solver_bug_issue, sweep_peak_usage
from i18n.common_messages import t as _common_t
from i18n.nurse_shift_weekly_cap_messages import t as _nurse_shift_t

from ._common import IssueRule, _rule




# ---------------------------------------------------------------------------
# _TRANSPORT_COST_MINIMIZER_RULES（2026-08-30 新規、solution checker展開パイロット）
#
# TransportCostMinimizerSolverはdocplex.mp（MIP）ベース。供給制約
# （Sum_j x[i][j] <= supply_i）と需要制約（Sum_i x[i][j] == demand_j）は
# いずれもMIPモデル側のハード制約として作り込まれている。返ってきたflowsを
# 独立に集計し直し、宣言されたsupply/demandと突き合わせる。いずれもO(n)
# （工場数・店舗数に比例）のため常に同期実行。
#
# demand_unmetのロジック自体は本カタログ整備前からsolver.py内に書かれて
# いたが、ISSUE_RULES辞書に"TransportCostMinimizer"キーが未登録だったため
# 一度も実行されていなかった（2026-08-30、カタログ登録により有効化）。
# supply_exceededは同時に新規追加。
# ---------------------------------------------------------------------------

_TRANSPORT_COST_MINIMIZER_RULES: List[IssueRule] = [

    _rule(
        "demand_unmet",
        condition=lambda ctx: ctx["demand"] - ctx["delivered"] > 1e-6,
        build=lambda ctx: solver_bug_issue(
            f"demand_unmet_{ctx['store_id']}",
            f"需要未充足: {ctx['store_name']}",
            f"店舗「{ctx['store_name']}」の需要{ctx['demand']:.1f}に対し、"
            f"実際の配送量は{ctx['delivered']:.1f}でした"
            f"（{ctx['demand'] - ctx['delivered']:.1f}不足）。",
        ),
    ),

    _rule(
        "supply_exceeded",
        condition=lambda ctx: ctx["shipped"] - ctx["supply"] > 1e-6,
        build=lambda ctx: solver_bug_issue(
            f"supply_exceeded_{ctx['factory_id']}",
            f"供給上限超過: {ctx['factory_name']}",
            f"工場「{ctx['factory_name']}」の供給上限{ctx['supply']:.1f}に対し、"
            f"実際の出荷量は{ctx['shipped']:.1f}でした"
            f"（{ctx['shipped'] - ctx['supply']:.1f}超過）。",
        ),
    ),
]



# ---------------------------------------------------------------------------
# TransportCostMinimizer context ビルダー（2026-08-30 新規）
# ---------------------------------------------------------------------------

def build_transport_cost_minimizer_contexts(
    factories: List[Dict],
    stores: List[Dict],
    flows: List[Dict],
) -> List[Dict[str, Any]]:
    """
    TransportCostMinimizer 用のO(n)フィールドチェック（供給/需要）の
    contextを工場・店舗1件ごとに生成する。flowsから独立に集計し直す
    （solver内部のx変数やmdlオブジェクトは一切参照しない）。
    """
    store_flow_map: Dict[str, float] = {}
    factory_flow_map: Dict[str, float] = {}
    for fl in flows:
        store_flow_map[fl["store_id"]] = store_flow_map.get(fl["store_id"], 0.0) + fl["amount"]
        factory_flow_map[fl["factory_id"]] = factory_flow_map.get(fl["factory_id"], 0.0) + fl["amount"]

    ctxs: List[Dict] = []
    for s in stores:
        sid = str(s["id"])
        ctxs.append({
            "_rule_id":   "demand_unmet",
            "store_id":   sid,
            "store_name": s.get("name", sid),
            "demand":     float(s.get("demand", 0)),
            "delivered":  store_flow_map.get(sid, 0.0),
        })
    for f in factories:
        fid = str(f["id"])
        ctxs.append({
            "_rule_id":     "supply_exceeded",
            "factory_id":   fid,
            "factory_name": f.get("name", fid),
            "supply":       float(f.get("supply", 0)),
            "shipped":      factory_flow_map.get(fid, 0.0),
        })
    return ctxs
