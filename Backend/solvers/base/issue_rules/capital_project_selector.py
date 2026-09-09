
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable, Dict, List, Optional, Tuple

from solvers.base.solution_checker import solver_bug_issue, sweep_peak_usage
from i18n.common_messages import t as _common_t
from i18n.nurse_shift_weekly_cap_messages import t as _nurse_shift_t

from ._common import IssueRule, _rule




# ---------------------------------------------------------------------------
# _CAPITAL_PROJECT_SELECTOR_RULES（2026-08-31 新規、solution checker展開バッチ1）
#
# 制約: (a) 選定案件の投資額合計は予算上限を超えない、
#       (b) config.max_projects が設定されている場合、選定件数はその上限以下。
# ---------------------------------------------------------------------------

_CAPITAL_PROJECT_SELECTOR_RULES: List[IssueRule] = [

    _rule(
        "budget_exceeded",
        condition=lambda ctx: ctx["total_weight"] > ctx["budget"] + 1e-6,
        build=lambda ctx: solver_bug_issue(
            "budget_exceeded",
            "予算超過（解チェッカー）",
            f"選定案件の投資額合計{ctx['total_weight']:.1f}が"
            f"予算上限{ctx['budget']:.1f}を超えています。",
        ),
    ),

    _rule(
        "max_projects_exceeded",
        condition=lambda ctx: ctx["max_projects"] is not None and ctx["selected_count"] > ctx["max_projects"],
        build=lambda ctx: solver_bug_issue(
            "max_projects_exceeded",
            "最大選定件数超過（解チェッカー）",
            f"選定件数{ctx['selected_count']}件が"
            f"最大選定件数{ctx['max_projects']}件を超えています。",
        ),
    ),
]



# ---------------------------------------------------------------------------
# CapitalProjectSelector context ビルダー（2026-08-31 新規）
# ---------------------------------------------------------------------------

def build_capital_project_selector_contexts(
    total_weight: float,
    budget: float,
    selected_count: int,
    max_projects: Optional[int],
) -> List[Dict[str, Any]]:
    """
    CapitalProjectSelector 用の予算超過チェックと最大選定件数超過チェック（共にO(1)）の
    contextを生成する。solver内部のx変数やmdlオブジェクトは一切参照しない。
    """
    return [
        {"_rule_id": "budget_exceeded", "total_weight": total_weight, "budget": budget},
        {"_rule_id": "max_projects_exceeded", "selected_count": selected_count, "max_projects": max_projects},
    ]
