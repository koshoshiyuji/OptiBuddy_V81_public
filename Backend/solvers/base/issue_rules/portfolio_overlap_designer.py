
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable, Dict, List, Optional, Tuple

from solvers.base.solution_checker import solver_bug_issue, sweep_peak_usage
from i18n.common_messages import t as _common_t
from i18n.nurse_shift_weekly_cap_messages import t as _nurse_shift_t

from ._common import IssueRule, _rule




# ---------------------------------------------------------------------------
# _PORTFOLIO_OVERLAP_DESIGNER_RULES（2026-08-31 新規、solution checker展開バッチ2）
#
# 制約: (a) 各ファンドの組入銘柄数は指定されたrequired_countちょうど、
#       (b) 返ってきたoverlap_matrix（各ファンドペアの重複銘柄数）の実際の最大値と、
#           KPIとして返されているworst_overlapが一致する。
# (b)はsolve()内で既にworst_overlap/overlap_matrixを別々に計算しているため、
# 両者がずれる（例: _make_result側のfund_solutions構築が別経路の場合）ことを
# 返ってきたデータのみから検算する。
# ---------------------------------------------------------------------------

_PORTFOLIO_OVERLAP_DESIGNER_RULES: List[IssueRule] = [

    _rule(
        "selected_count_mismatch",
        condition=lambda ctx: ctx["selected_count"] != ctx["required_count"],
        build=lambda ctx: solver_bug_issue(
            f"selected_count_mismatch_{ctx['fund_id']}",
            f"組入銘柄数不一致（解チェッカー）: {ctx['fund_name']}",
            f"ファンド「{ctx['fund_name']}」の組入銘柄数{ctx['selected_count']}件が"
            f"指定された組入銘柄数{ctx['required_count']}件と一致しません。",
        ),
    ),

    _rule(
        "overlap_matrix_worst_overlap_mismatch",
        condition=lambda ctx: ctx["actual_max_overlap"] != ctx["worst_overlap"],
        build=lambda ctx: solver_bug_issue(
            "overlap_matrix_worst_overlap_mismatch",
            "重複数KPI不一致（解チェッカー）",
            f"overlap_matrixから再計算した実際の最大重複数{ctx['actual_max_overlap']}が、"
            f"KPIのworst_overlap（{ctx['worst_overlap']}）と一致しません。",
        ),
    ),
]



# ---------------------------------------------------------------------------
# PortfolioOverlapDesigner context ビルダー（2026-08-31 新規）
# ---------------------------------------------------------------------------

def build_portfolio_overlap_designer_contexts(
    funds: List[Dict],
    assignments: Dict[str, List[str]],
    overlap_matrix: List[Dict],
    worst_overlap: Optional[int],
) -> List[Dict[str, Any]]:
    """
    PortfolioOverlapDesigner 用の組入銘柄数チェック（O(ファンド数)）と
    重複数KPI整合性チェック（O(ペア数)）のcontextを生成する。solver内部のx変数や
    mdlオブジェクトは一切参照せず、返ってきたassignments/overlap_matrix/worst_overlap
    のみから再計算する。
    """
    ctxs: List[Dict] = []
    for f in funds:
        fid = f["id"]
        selected = assignments.get(fid, [])
        ctxs.append({
            "_rule_id":       "selected_count_mismatch",
            "fund_id":        fid,
            "fund_name":      f.get("name", fid),
            "selected_count": len(selected),
            "required_count": f.get("required_count", 0),
        })

    actual_max_overlap = max((m.get("overlap_count", 0) for m in overlap_matrix), default=0)
    ctxs.append({
        "_rule_id":          "overlap_matrix_worst_overlap_mismatch",
        "actual_max_overlap": actual_max_overlap,
        "worst_overlap":      worst_overlap if worst_overlap is not None else actual_max_overlap,
    })
    return ctxs
