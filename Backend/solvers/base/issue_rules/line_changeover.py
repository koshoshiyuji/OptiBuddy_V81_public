
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable, Dict, List, Optional, Tuple

from solvers.base.solution_checker import solver_bug_issue, sweep_peak_usage
from i18n.common_messages import t as _common_t
from i18n.nurse_shift_weekly_cap_messages import t as _nurse_shift_t

from ._common import IssueRule, _rule




# ---------------------------------------------------------------------------
# _LINE_CHANGEOVER_RULES（2026-07-24 新規）
#
# LineChangeoverScheduler（RCPSP、新フラグシップ）は解チェッカーを一切
# 持っていなかった。前後関係制約（precedences）と資源容量制約
# （resources[].capacity）は、いずれもCP Optimizer側でハード制約として
# 実装されている（end_before_start / cumul_function <= cap）ため、
# 返ってきたscheduleと突き合わせて破られていればバグの疑いが強い。
#
# resource_capacity_violation は solvers/base/solution_checker.sweep_peak_usage()
# によるスイープライン検算（O(n log n)、2節「ソート/スイープ」分類。
# DESIGN_2026-07-21時点では「現状未実装、将来の拡張候補」とされていた
# 分類の初適用）。
# ---------------------------------------------------------------------------

_LINE_CHANGEOVER_RULES: List[IssueRule] = [

    _rule(
        "precedence_violation",
        condition=lambda ctx: ctx["to_start"] < ctx["from_end"] + ctx["min_delay"],
        build=lambda ctx: solver_bug_issue(
            f"precedence_violation_{ctx['from_id']}_{ctx['to_id']}",
            f"前後関係制約違反: {ctx['from_id']} → {ctx['to_id']}",
            f"{ctx['from_id']}の終了({ctx['from_end']})+最小間隔({ctx['min_delay']})が "
            f"{ctx['to_id']}の開始({ctx['to_start']})を超えています。",
        ),
    ),

    _rule(
        "resource_capacity_violation",
        # 2026-09-09追記: 許容誤差を追加（solver本体は容量をint、amountもint変換して
        # 制約を組んでいるが、この検算側は元々amountをint変換していなかった＝下の
        # build_line_changeover_resource_contexts側の修正と対の変更。境界上の正しい
        # 解を丸め誤差で誤検知しないよう他の容量超過チェックと同じ1e-6を採用）。
        condition=lambda ctx: ctx["peak"] > ctx["capacity"] + 1e-6,
        build=lambda ctx: solver_bug_issue(
            f"resource_capacity_violation_{ctx['resource_id']}",
            f"資源容量超過: {ctx['resource_name']}",
            f"資源「{ctx['resource_name']}」の同時使用量が最大{ctx['peak']}に達し、"
            f"容量{ctx['capacity']}を超過しています"
            f"（時刻{ctx['peak_time']}付近、スイープライン検算による独立検証）。",
        ),
    ),
]



# ---------------------------------------------------------------------------
# LineChangeoverScheduler context ビルダー（2026-07-24 新規）
# ---------------------------------------------------------------------------

def build_line_changeover_precedence_contexts(
    schedule: List[Dict],
    precedences: List[Dict],
) -> List[Dict[str, Any]]:
    """前後関係制約（O(E)、単純比較）のcontextを precedence 1件ごとに生成する。"""
    sched_by_id = {s["task_id"]: s for s in schedule}
    ctxs: List[Dict] = []
    for p in precedences:
        from_id = str(p.get("from_task", ""))
        to_id   = str(p.get("to_task", ""))
        delay   = int(p.get("min_delay", 0))
        sf, st  = sched_by_id.get(from_id), sched_by_id.get(to_id)
        if sf is None or st is None:
            continue
        ctxs.append({
            "_rule_id":  "precedence_violation",
            "from_id":   from_id,
            "to_id":     to_id,
            "from_end":  sf["end"],
            "to_start":  st["start"],
            "min_delay": delay,
        })
    return ctxs



def build_line_changeover_resource_contexts(
    schedule: List[Dict],
    resources: List[Dict],
) -> List[Dict[str, Any]]:
    """
    資源容量制約（O(n log n)、スイープライン検算）のcontextを資源1件ごとに
    生成する。solvers.base.solution_checker.sweep_peak_usage() で
    累積使用量のピークを計算し、容量と一緒にcontextへ積む
    （条件判定(peak > capacity)はルール側のcondition関数で行う）。
    """
    ctxs: List[Dict] = []
    for r in resources:
        rid = str(r["id"])
        cap = int(r.get("capacity", 1))
        events: List[tuple] = []
        for s in schedule:
            for rr in s.get("resource_requirements", []):
                if str(rr.get("resource_id")) != rid:
                    continue
                # 2026-09-09修正: solver本体（line_changeover_scheduler_solver.py）は
                # int(rreq.get("amount", 1))で必ずint変換してから容量制約を組んでいる
                # （328行目・498行目）。ここも同じint変換を行い、DSLのamountが小数の
                # 場合でもsolverが実際に守っている制約と検算内容を一致させる。
                amount = int(rr.get("amount", 1))
                events.append((s["start"], amount))
                events.append((s["end"], -amount))
        if not events:
            continue
        peak, peak_time = sweep_peak_usage(events)
        ctxs.append({
            "_rule_id":     "resource_capacity_violation",
            "resource_id":  rid,
            "resource_name": r.get("name", rid),
            "peak":         peak,
            "capacity":     cap,
            "peak_time":    peak_time,
        })
    return ctxs
