"""
Backend/dsl_transformer/lot_sizing_scheduler_converter.py

Business DSL → Solver Input DSL 変換（LotSizingScheduler）

# solver_input keys:
#   problem_class  : str
#   orders         : List[Dict] {id, product_id, quantity, due_period, holding_cost_per_period}
#   periods        : List[int]
#   setup_costs    : List[Dict] {from_product, to_product, cost}
#   config         : Dict       {time_limit_sec, capacity_per_period, allow_unassigned}
#   issue_statuses : Dict
"""

from __future__ import annotations

from typing import Any, Dict, List


def convert_lot_sizing_scheduler_to_solver(business_dsl: dict) -> dict:
    """
    Business DSL → Solver Input DSL

    Business DSL スキーマ:
      problem_class : "LotSizingScheduler"
      orders        : [{id, product_id, quantity, due_period, holding_cost_per_period}]
      periods       : [int, ...]  or  {count: int, start: int}
      setup_costs   : [{from_product, to_product, cost}]  （省略可）
      config        : {time_limit_sec?, capacity_per_period?, allow_unassigned?}
    """
    raw_periods = business_dsl.get("periods", [])
    periods = _resolve_periods(raw_periods)

    orders = _normalize_orders(business_dsl.get("orders", []), periods)
    setup_costs = _normalize_setup_costs(business_dsl.get("setup_costs", []))
    config = _normalize_config(business_dsl.get("config", {}))

    return {
        "problem_class":  "LotSizingScheduler",
        "orders":         orders,
        "periods":        periods,
        "setup_costs":    setup_costs,
        "config":         config,
        "issue_statuses": business_dsl.get("issue_statuses", {}),
    }


def _resolve_periods(raw: Any) -> List[int]:
    """
    periods フィールドを整数リストに正規化する。
    入力形式:
      - List[int]: そのまま使用
      - {"count": N, "start": S}: S から S+N-1 の連番
      - {"count": N}: 1 から N の連番
      - int: 1 から N の連番（後方互換）
    """
    if isinstance(raw, list):
        return sorted(int(p) for p in raw)
    if isinstance(raw, dict):
        count = int(raw.get("count", 5))
        start = int(raw.get("start", 1))
        return list(range(start, start + count))
    if isinstance(raw, int):
        return list(range(1, raw + 1))
    # フォールバック: 5期間
    return list(range(1, 6))


def _normalize_orders(raw_orders: List[Dict], periods: List[int]) -> List[Dict]:
    """
    各注文を正規化する。
    - id が未指定の場合は連番で補完
    - due_period が未指定の場合は最終期間で補完
    - quantity, holding_cost_per_period のデフォルト補完
    """
    max_period = max(periods) if periods else 1
    normalized = []
    for i, o in enumerate(raw_orders):
        oid = str(o.get("id", f"ORDER{i + 1:03d}"))
        normalized.append({
            "id":                       oid,
            "product_id":               str(o.get("product_id", oid)),
            "quantity":                 int(o.get("quantity", 1)),
            "due_period":               int(o.get("due_period", max_period)),
            "holding_cost_per_period":  float(o.get("holding_cost_per_period", 1.0)),
        })
    return normalized


def _normalize_setup_costs(raw: List[Dict]) -> List[Dict]:
    """段取り替えコストリストを正規化する。"""
    normalized = []
    for sc in raw:
        fp = str(sc.get("from_product", ""))
        tp = str(sc.get("to_product", ""))
        cost = float(sc.get("cost", 0.0))
        if fp and tp and cost > 0:
            normalized.append({
                "from_product": fp,
                "to_product":   tp,
                "cost":         cost,
            })
    return normalized


def _normalize_config(raw: Dict) -> Dict:
    return {
        "time_limit_sec":      float(raw.get("time_limit_sec", 30.0)),
        "capacity_per_period": int(raw.get("capacity_per_period", 999_999)),
        "allow_unassigned":    bool(raw.get("allow_unassigned", False)),
    }