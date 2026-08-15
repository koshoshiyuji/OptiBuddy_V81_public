"""
Backend/dsl_transformer/depot_route_planner_converter.py

Business DSL → Solver Input DSL 変換 (DepotRoutePlanner)

solver_input keys:
  problem_class, meta, depots, customers, config, issue_statuses
"""

from __future__ import annotations

from typing import Any, Dict


def convert_depot_route_planner_to_solver(business_dsl: Dict[str, Any]) -> Dict[str, Any]:
    """
    Business DSL → Solver Input DSL

    Business DSLスキーマ:
      depots[]:
        id, name, x, y, opening_cost
      customers[]:
        id, name, x, y
      config:
        max_open_depots        (default: len(depots))
        stage1_time_limit_sec  (default: 30)
        stage2_time_limit_sec  (default: 20)
    """
    raw_depots = business_dsl.get("depots", [])
    raw_customers = business_dsl.get("customers", [])
    raw_config = business_dsl.get("config", {})

    depots = []
    for d in raw_depots:
        depots.append({
            "id":           str(d.get("id", "")),
            "name":         str(d.get("name", d.get("id", ""))),
            "x":            float(d.get("x", 0.0)),
            "y":            float(d.get("y", 0.0)),
            "opening_cost": float(d.get("opening_cost", 0.0)),
        })

    customers = []
    for c in raw_customers:
        customers.append({
            "id":   str(c.get("id", "")),
            "name": str(c.get("name", c.get("id", ""))),
            "x":    float(c.get("x", 0.0)),
            "y":    float(c.get("y", 0.0)),
        })

    config = {
        "max_open_depots":        int(raw_config.get("max_open_depots", len(depots))),
        "stage1_time_limit_sec":  float(raw_config.get("stage1_time_limit_sec", 30.0)),
        "stage2_time_limit_sec":  float(raw_config.get("stage2_time_limit_sec", 20.0)),
    }

    return {
        "problem_class":  "DepotRoutePlanner",
        "meta":           business_dsl.get("meta", {}),
        "depots":         depots,
        "customers":      customers,
        "config":         config,
        "issue_statuses": business_dsl.get("issue_statuses", {}),
    }
