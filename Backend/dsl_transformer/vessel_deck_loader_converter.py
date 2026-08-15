"""
vessel_deck_loader_converter.py
Business DSL → Solver Input DSL (VesselDeckLoader)

solver_input keys:
  - problem_class: str ("VesselDeckLoader")
  - containers: List[Dict]  # id, name, length, width, load_order, is_hazardous
  - deck: Dict              # width, max_length
  - config: Dict            # time_limit_sec, hazard_margin
  - issue_statuses: Dict[str, str]
  - meta: Dict
"""

from __future__ import annotations

from typing import Any, Dict, List


def convert_vessel_deck_loader_to_solver(business_dsl: dict) -> dict:
    """Business DSL → Solver Input DSL"""

    containers_raw: List[Dict] = business_dsl.get("containers", [])
    deck_raw: Dict = business_dsl.get("deck", {})
    config_raw: Dict = business_dsl.get("config", {})
    meta: Dict = business_dsl.get("meta", {})
    issue_statuses: Dict = business_dsl.get("issue_statuses", {})

    containers = [
        {
            "id":           str(c.get("id", f"C{i}")),
            "name":         str(c.get("name", c.get("id", f"コンテナ{i+1}"))),
            "length":       int(c.get("length", 1)),
            "width":        int(c.get("width", 1)),
            "load_order":   int(c.get("load_order", i + 1)),
            "is_hazardous": bool(c.get("is_hazardous", False)),
        }
        for i, c in enumerate(containers_raw)
    ]

    deck = {
        "width":      int(deck_raw.get("width", 6)),
        "max_length": int(deck_raw.get("max_length", 30)),
    }

    config = {
        "time_limit_sec": int(config_raw.get("time_limit_sec", 30)),
        "hazard_margin":  int(config_raw.get("hazard_margin", 1)),
    }

    return {
        "problem_class":  "VesselDeckLoader",
        "containers":     containers,
        "deck":           deck,
        "config":         config,
        "issue_statuses": issue_statuses,
        "meta":           meta,
    }