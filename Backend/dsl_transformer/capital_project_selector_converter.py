"""
Backend/dsl_transformer/capital_project_selector_converter.py

Business DSL → Solver Input DSL 変換（CapitalProjectSelector）

solver_input keys:
  - problem_class: str ("CapitalProjectSelector")
  - projects: List[Dict]  # id, name, value, weight, category, tags
  - budget: float         # 予算上限
  - config: Dict          # time_limit_sec, max_projects
  - issue_statuses: Dict
  - meta: Dict
"""

from __future__ import annotations

from typing import Any, Dict


def convert_capital_project_selector_to_solver(business_dsl: dict) -> dict:
    """
    Business DSL → Solver Input DSL

    - projects[].value / weight を float に正規化する
    - budget を float に正規化する
    - problem_class / meta / config / issue_statuses を引き継ぐ
    - 選定件数は制約なし（0件〜全件）。制約は予算上限の1本のみ（CSPLib prob133 基本形）。
    """
    raw_projects = business_dsl.get("projects", [])
    projects = []
    for i, p in enumerate(raw_projects):
        projects.append({
            "id":       str(p.get("id", f"proj_{i}")),
            "name":     str(p.get("name", f"案件{i+1}")),
            "value":    float(p.get("value", p.get("expected_value", 0.0))),
            "weight":   float(p.get("weight", p.get("investment", p.get("cost", 0.0)))),
            "category": str(p.get("category", "")),
            "tags":     list(p.get("tags", [])),
        })

    budget_raw = (
        business_dsl.get("budget")
        or business_dsl.get("config", {}).get("budget")
        or 0.0
    )
    budget = float(budget_raw)

    config_raw = business_dsl.get("config", {})

    # max_projects は任意オプション。未指定時は None（件数上限なし）。
    # min_projects は本ドメインの基本形（CSPLib prob133）には存在しない。
    # 選定件数は予算内で価値最大となる組み合わせとして自然に決まる。
    max_projects_raw = config_raw.get("max_projects")

    config: Dict[str, Any] = {
        "time_limit_sec": int(config_raw.get("time_limit_sec", 30)),
        "max_projects":   int(max_projects_raw) if max_projects_raw is not None else None,
    }

    return {
        "problem_class":  "CapitalProjectSelector",
        "meta":           business_dsl.get("meta", {}),
        "projects":       projects,
        "budget":         budget,
        "config":         config,
        "issue_statuses": business_dsl.get("issue_statuses", {}),
    }
