"""
Backend/dsl_transformer/inventory_replenishment_planner_converter.py

Business DSL → Solver Input DSL 変換
InventoryReplenishmentPlanner（多段階ロットサイジング）

solver_input keys（必ず全キーを出力する）:
  - problem_class: str
  - warehouses: List[Dict]   # {id, name, ...}
  - centers: List[Dict]      # {id, name, initial_inventory}
  - periods: List[Dict]      # {id, label}
  - demands: List[Dict]      # {center_id, period_id, quantity}
  - config: Dict             # holding_cost_rate, fixed_shipping_cost,
                             #   supply_capacity_per_period, solve_time_sec,
                             #   use_integer_lots, lot_min_unit,
                             #   max_inventory_per_center
  - issue_statuses: Dict
"""

from __future__ import annotations

from typing import Any, Dict, List


def convert_inventory_replenishment_planner_to_solver(business_dsl: dict) -> dict:
    """
    Business DSL → Solver Input DSL

    Business DSL 想定スキーマ:
      problem_class: "InventoryReplenishmentPlanner"
      meta: {instance_name, note}
      warehouses: [{id, name}]
      centers: [{id, name, initial_inventory?}]
      periods: [{id, label?}]           # 順序がそのまま時系列の順
      demands: [{center_id, period_id, quantity}]
      config:
        holding_cost_rate: float         # 期末在庫×この率が保管費（既定0.05）
        fixed_shipping_cost: float       # 発送1回あたり固定費（既定5000）
        supply_capacity_per_period: float  # 倉庫の週次供給能力上限（既定無制限）
        solve_time_sec: int              # ソルブ上限秒数（既定30）
        use_integer_lots: bool           # 整数ロット強制（既定true）
        lot_min_unit: int                # ロット最小単位（既定1）
        max_inventory_per_center: float  # センター在庫上限（既定1e6）
    """
    warehouses_raw = business_dsl.get("warehouses", [])
    centers_raw    = business_dsl.get("centers", [])
    periods_raw    = business_dsl.get("periods", [])
    demands_raw    = business_dsl.get("demands", [])
    config_raw     = business_dsl.get("config", {})
    issue_statuses = business_dsl.get("issue_statuses", {})

    # warehouses
    warehouses: List[Dict[str, Any]] = []
    for i, w in enumerate(warehouses_raw):
        wid = str(w.get("id", f"W{i+1}"))
        warehouses.append({
            "id":   wid,
            "name": w.get("name", wid),
        })

    # centers
    centers: List[Dict[str, Any]] = []
    for i, c in enumerate(centers_raw):
        cid = str(c.get("id", f"C{i+1}"))
        centers.append({
            "id":                cid,
            "name":              c.get("name", cid),
            "initial_inventory": float(c.get("initial_inventory", 0.0)),
        })

    # periods（順序は出力リストの並び順で表現し、index フィールドは出力しない）
    periods: List[Dict[str, Any]] = []
    for i, p in enumerate(periods_raw):
        pid = str(p.get("id", f"T{i+1}"))
        periods.append({
            "id":    pid,
            "label": p.get("label", f"第{i+1}週"),
        })

    # demands
    demands: List[Dict[str, Any]] = []
    for d in demands_raw:
        demands.append({
            "center_id": str(d["center_id"]),
            "period_id": str(d["period_id"]),
            "quantity":  float(d.get("quantity", 0.0)),
        })

    # config（既定値を補完して全キー出力）
    # shortage_penalty は solver がハード制約設計のため不使用につき出力しない
    config: Dict[str, Any] = {
        "holding_cost_rate":           float(config_raw.get("holding_cost_rate", 0.05)),
        "fixed_shipping_cost":         float(config_raw.get("fixed_shipping_cost", 5000.0)),
        "supply_capacity_per_period":  float(config_raw.get("supply_capacity_per_period", 1e9)),
        "solve_time_sec":              int(config_raw.get("solve_time_sec", 30)),
        "use_integer_lots":            bool(config_raw.get("use_integer_lots", True)),
        "lot_min_unit":                int(config_raw.get("lot_min_unit", 1)),
        "max_inventory_per_center":    float(config_raw.get("max_inventory_per_center", 1e6)),
    }

    return {
        "problem_class":  "InventoryReplenishmentPlanner",
        "warehouses":     warehouses,
        "centers":        centers,
        "periods":        periods,
        "demands":        demands,
        "config":         config,
        "issue_statuses": issue_statuses,
    }