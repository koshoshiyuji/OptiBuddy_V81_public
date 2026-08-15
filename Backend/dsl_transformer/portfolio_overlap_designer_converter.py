"""
Backend/dsl_transformer/portfolio_overlap_designer_converter.py

Business DSL → Solver Input DSL 変換
portfolio_overlap_designer ドメイン

# solver_input keys:
#   problem_class:  str  "PortfolioOverlapDesigner"
#   meta:           dict
#   funds:          List[{id, name, required_count}]
#   pool:           List[{id, name, sector?}]
#   config:         {time_limit_sec}
#   issue_statuses: dict
"""

from __future__ import annotations

from typing import Any, Dict, List


def convert_portfolio_overlap_designer_to_solver(business_dsl: dict) -> dict:
    """
    Business DSL → Solver Input DSL

    Business DSL スキーマ:
      problem_class:  "PortfolioOverlapDesigner"
      meta:           {instance_name, note}
      funds:          [{id, name, required_count}]
      pool:           [{id, name, sector?}]
      config:         {time_limit_sec?}
      issue_statuses: {}
    """
    funds: List[Dict[str, Any]] = [
        {
            "id":             str(f.get("id", f"fund_{i}")),
            "name":           f.get("name", f"ファンド{i+1}"),
            "required_count": int(f.get("required_count", 0)),
        }
        for i, f in enumerate(business_dsl.get("funds", []))
    ]

    pool: List[Dict[str, Any]] = [
        {
            "id":     str(s.get("id", s.get("stock_id", f"stock_{i}"))),
            "name":   s.get("name", s.get("stock_name", f"銘柄{i+1}")),
            "sector": s.get("sector", ""),
        }
        for i, s in enumerate(business_dsl.get("pool", []))
    ]

    config_raw = business_dsl.get("config", {})
    config: Dict[str, Any] = {
        "time_limit_sec": int(config_raw.get("time_limit_sec", 60)),
    }

    return {
        "problem_class":  "PortfolioOverlapDesigner",
        "meta":           business_dsl.get("meta", {}),
        "funds":          funds,
        "pool":           pool,
        "config":         config,
        "issue_statuses": business_dsl.get("issue_statuses", {}),
    }