"""
Backend/dsl_transformer/steel_mill_slab_design_converter.py

Business DSL → Solver Input DSL 変換（SteelMillSlabDesign, CSPLib prob038）

solver_input keys:
  - problem_class: str ("SteelMillSlabDesign")
  - orders: List[Dict]   # id, name, weight, colors, customer
  - slab_capacity: int   # 単一規格・固定容量（トン）
  - config: Dict         # time_limit_sec, max_slabs
  - issue_statuses: Dict
  - meta: Dict
"""

from __future__ import annotations

from typing import Any, Dict


def convert_steel_mill_slab_design_to_solver(business_dsl: dict) -> dict:
    """
    Business DSL → Solver Input DSL

    - orders[].weight を int に正規化する
    - slab_capacity（後方互換で slab_capacities[0]）を int に正規化する
    - problem_class / meta / config / issue_statuses を引き継ぐ
    """
    raw_orders = business_dsl.get("orders", [])
    orders = []
    for i, o in enumerate(raw_orders):
        orders.append({
            "id":       str(o.get("id", f"order_{i}")),
            "name":     str(o.get("name", f"注文{i+1}")),
            "weight":   int(o.get("weight", 0)),
            "colors":   list(o.get("colors", [])),
            "customer": str(o.get("customer", "")),
        })

    slab_capacity_raw = business_dsl.get("slab_capacity")
    if slab_capacity_raw is None:
        caps = business_dsl.get("slab_capacities", [])
        slab_capacity_raw = caps[0] if caps else 0
    slab_capacity = int(slab_capacity_raw)

    config_raw = business_dsl.get("config", {})
    config: Dict[str, Any] = {
        "time_limit_sec": int(config_raw.get("time_limit_sec", 30)),
        "max_slabs":      int(config_raw.get("max_slabs", len(orders))),
    }

    return {
        "problem_class":  "SteelMillSlabDesign",
        "meta":           business_dsl.get("meta", {}),
        "orders":         orders,
        "slab_capacity":  slab_capacity,
        "config":         config,
        "issue_statuses": business_dsl.get("issue_statuses", {}),
    }
