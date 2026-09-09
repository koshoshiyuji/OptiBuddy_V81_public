
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable, Dict, List, Optional, Tuple

from solvers.base.solution_checker import solver_bug_issue, sweep_peak_usage
from i18n.common_messages import t as _common_t
from i18n.nurse_shift_weekly_cap_messages import t as _nurse_shift_t

from ._common import IssueRule, _rule




# ---------------------------------------------------------------------------
# _CAR_SEQUENCING_RULES（2026-09-01 新規、solution checker展開バッチ3）
#
# 制約: 各車種の投入順序中の実際の台数は car_types[].count（CP側で
# count_expr == required_count というハード制約）と一致する。返ってきた
# sequenceのみから車種別に再集計し、DSL宣言のcountと突き合わせる。
# O(車種数)。
# ---------------------------------------------------------------------------

_CAR_SEQUENCING_RULES: List[IssueRule] = [

    _rule(
        "car_type_count_mismatch",
        condition=lambda ctx: ctx["actual_count"] != ctx["required_count"],
        build=lambda ctx: solver_bug_issue(
            f"car_type_count_mismatch_{ctx['car_type_id']}",
            f"車種別生産台数不一致（解チェッカー）: {ctx['car_type_name']}",
            f"車種「{ctx['car_type_name']}」の投入順序中の実際の台数{ctx['actual_count']}が、"
            f"指定された生産台数{ctx['required_count']}と一致しません。",
        ),
    ),
]



# ---------------------------------------------------------------------------
# CarSequencing context ビルダー（2026-09-01 新規）
# ---------------------------------------------------------------------------

def build_car_sequencing_contexts(
    sequence_result: List[Dict],
    car_types: List[Dict],
) -> List[Dict[str, Any]]:
    """
    CarSequencing 用の車種別生産台数チェック（O(車種数)）のcontextを生成する。
    solver内部のseq変数やmdlオブジェクトは一切参照せず、返ってきた
    sequence_resultのみから車種別に再集計し、DSL宣言のcar_types[].countと
    突き合わせる。
    """
    actual_counts: Dict[str, int] = {}
    for item in sequence_result:
        tid = item["car_type_id"]
        actual_counts[tid] = actual_counts.get(tid, 0) + 1

    ctxs: List[Dict] = []
    for ct in car_types:
        tid = ct["car_type_id"]
        ctxs.append({
            "_rule_id":       "car_type_count_mismatch",
            "car_type_id":    tid,
            "car_type_name":  ct.get("car_type_name", tid),
            "actual_count":   actual_counts.get(tid, 0),
            "required_count": ct.get("count", 0),
        })
    return ctxs
