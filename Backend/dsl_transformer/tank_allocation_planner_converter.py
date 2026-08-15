"""
Backend/dsl_transformer/tank_allocation_planner_converter.py

TankAllocationPlanner: Business DSL → Solver Input DSL

solver_input keys:
  - problem_class: str
  - meta: dict
  - lots: list[dict]               # 受注ロット一覧（id, name, volume, category, customer）
  - tanks: list[dict]              # タンクローリー一覧（id, name, capacity）
  - incompatible_pairs: list[tuple[str,str]]  # 混載禁止分類ペア
  - config: dict
  - issue_statuses: dict
"""

from __future__ import annotations

from typing import Any, Dict, List, Tuple

# 混載禁止のデフォルト分類ペア
_DEFAULT_INCOMPATIBLE_PAIRS: List[Tuple[str, str]] = [
    ("酸性", "アルカリ性"),
    ("酸化性", "可燃性"),
]


def convert_tank_allocation_planner_to_solver(business_dsl: Dict[str, Any]) -> Dict[str, Any]:
    """
    Business DSL → Solver Input DSL

    Business DSLスキーマ:
      lots[]:
        id, name, volume (kL), category (酸性|アルカリ性|酸化性|可燃性|中性), customer
      tanks[]:
        id, name, capacity (kL)
      config:
        incompatible_pairs: [[str, str], ...]  # 省略時はデフォルト2ペア
        solve_time_sec: int
    """
    meta = business_dsl.get("meta", {})
    config = business_dsl.get("config", {})

    # 受注ロット
    raw_lots = business_dsl.get("lots", [])
    lots = []
    for i, lot in enumerate(raw_lots):
        lots.append({
            "id": str(lot.get("id", i)),
            "name": lot.get("name", f"ロット{i+1}"),
            "volume": float(lot.get("volume", 0)),
            "category": str(lot.get("category", "中性")),
            "customer": str(lot.get("customer", "不明")),
        })

    # タンクローリー
    raw_tanks = business_dsl.get("tanks", [])
    tanks = []
    for j, tank in enumerate(raw_tanks):
        tanks.append({
            "id": str(tank.get("id", j)),
            "name": tank.get("name", f"タンク{j+1}"),
            "capacity": float(tank.get("capacity", 0)),
        })

    # 混載禁止ペア
    raw_pairs = config.get("incompatible_pairs", None)
    if raw_pairs is not None:
        incompatible_pairs = [tuple(p) for p in raw_pairs]
    else:
        incompatible_pairs = list(_DEFAULT_INCOMPATIBLE_PAIRS)

    solver_config = {
        "solve_time_sec": int(config.get("solve_time_sec", 30)),
    }

    return {
        "problem_class": "TankAllocationPlanner",
        "meta": meta,
        "lots": lots,
        "tanks": tanks,
        "incompatible_pairs": incompatible_pairs,
        "config": solver_config,
        "issue_statuses": business_dsl.get("issue_statuses", {}),
    }