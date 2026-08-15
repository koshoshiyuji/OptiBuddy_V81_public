"""
Backend/dsl_transformer/transport_cost_minimizer_converter.py

Business DSL → Solver Input DSL（TransportCostMinimizer）

solver_input keys:
  - problem_class: str
  - factories: List[Dict]  # {id, name, supply}
  - stores:    List[Dict]  # {id, name, demand}
  - costs:     List[Dict]  # {factory_id, store_id, cost_per_unit}
  - config:    Dict        # {time_limit_sec}
  - time_limit_sec: int    # config.time_limit_sec のトップレベル複製
  - issue_statuses: Dict
"""

from __future__ import annotations
from typing import Any, Dict, List


def convert_transport_cost_minimizer_to_solver(business_dsl: dict) -> dict:
    """
    Business DSL → Solver Input DSL

    Business DSL 想定スキーマ:
      {
        "problem_class": "TransportCostMinimizer",
        "meta": {...},
        "factories": [{"id": "F1", "name": "工場A", "supply": 100}, ...],
        "stores":    [{"id": "S1", "name": "店舗A", "demand": 60}, ...],
        "costs":     [{"factory_id": "F1", "store_id": "S1", "cost_per_unit": 3.5}, ...],
        "config":    {"time_limit_sec": 60},
      }
    """
    factories: List[Dict] = [
        {
            "id":     str(f.get("id", "")),
            "name":   str(f.get("name", f.get("id", ""))),
            "supply": float(f.get("supply", 0)),
        }
        for f in business_dsl.get("factories", [])
    ]

    stores: List[Dict] = [
        {
            "id":     str(s.get("id", "")),
            "name":   str(s.get("name", s.get("id", ""))),
            "demand": float(s.get("demand", 0)),
        }
        for s in business_dsl.get("stores", [])
    ]

    raw_costs = business_dsl.get("costs", [])
    # costs が与えられていない場合は全ルートにコスト1.0を補完する
    if not raw_costs and factories and stores:
        raw_costs = [
            {"factory_id": f["id"], "store_id": s["id"], "cost_per_unit": 1.0}
            for f in factories
            for s in stores
        ]

    costs: List[Dict] = [
        {
            "factory_id":   str(c.get("factory_id", "")),
            "store_id":     str(c.get("store_id", "")),
            "cost_per_unit": float(c.get("cost_per_unit", 1.0)),
        }
        for c in raw_costs
    ]

    config = dict(business_dsl.get("config", {}))
    config.setdefault("time_limit_sec", 60)

    # time_limit_sec をトップレベルにも出力し、solver が直接参照できるようにする
    time_limit_sec = config["time_limit_sec"]

    return {
        "problem_class":  "TransportCostMinimizer",
        "meta":           business_dsl.get("meta", {}),
        "factories":      factories,
        "stores":         stores,
        "costs":          costs,
        "config":         config,
        "time_limit_sec": time_limit_sec,
        "issue_statuses": business_dsl.get("issue_statuses", {}),
    }