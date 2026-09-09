
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable, Dict, List, Optional, Tuple

from solvers.base.solution_checker import solver_bug_issue, sweep_peak_usage
from i18n.common_messages import t as _common_t
from i18n.nurse_shift_weekly_cap_messages import t as _nurse_shift_t

from ._common import IssueRule, _rule




# ---------------------------------------------------------------------------
# _LOT_SIZING_SCHEDULER_RULES（2026-09-01 新規、solution checker展開バッチ3）
#
# 制約: 各期間に割り当てられる注文は高々1件（all_diff(ap)というハード制約。
# capacity_per_periodは現行実装では常に1として扱われる、solver.py内コメント
# 参照）。返ってきたassignmentsのみから期間別に再集計する。O(n)。
# ---------------------------------------------------------------------------

_LOT_SIZING_SCHEDULER_RULES: List[IssueRule] = [

    _rule(
        "period_double_assigned",
        condition=lambda ctx: ctx["assigned_count"] > 1,
        build=lambda ctx: solver_bug_issue(
            f"period_double_assigned_{ctx['period_id']}",
            f"期間重複割当（解チェッカー）: 期間{ctx['period_id']}",
            f"期間「{ctx['period_id']}」に{ctx['assigned_count']}件の注文が割り当てられています。"
            "各期間には高々1件までという制約（all_diff）と矛盾しています。",
        ),
    ),
]



# ---------------------------------------------------------------------------
# LotSizingScheduler context ビルダー（2026-09-01 新規）
# ---------------------------------------------------------------------------

def build_lot_sizing_scheduler_contexts(
    assignments: List[Dict],
) -> List[Dict[str, Any]]:
    """
    LotSizingScheduler 用の期間重複割当チェック（O(n)）のcontextを生成する。
    solver内部のap変数やmdlオブジェクトは一切参照せず、返ってきた
    assignmentsのみから期間別に再集計する。
    """
    period_count: Dict[Any, int] = {}
    for a in assignments:
        pid = a["assigned_period"]
        period_count[pid] = period_count.get(pid, 0) + 1

    return [
        {"_rule_id": "period_double_assigned", "period_id": pid, "assigned_count": count}
        for pid, count in period_count.items()
    ]
