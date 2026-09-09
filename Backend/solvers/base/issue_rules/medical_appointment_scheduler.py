
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable, Dict, List, Optional, Tuple

from solvers.base.solution_checker import solver_bug_issue, sweep_peak_usage
from i18n.common_messages import t as _common_t
from i18n.nurse_shift_weekly_cap_messages import t as _nurse_shift_t

from ._common import IssueRule, _rule





# ---------------------------------------------------------------------------
# _MEDICAL_APPOINTMENT_SCHEDULER_RULES（2026-09-01 新規、solution checker展開バッチ4）
# ---------------------------------------------------------------------------
_MEDICAL_APPOINTMENT_SCHEDULER_RULES: List[IssueRule] = [
    _rule(
        "resource_double_booking",
        condition=lambda ctx: ctx["peak"] > ctx["capacity"] + ctx["tolerance"],
        build=lambda ctx: solver_bug_issue(
            f"resource_double_booking_{ctx['resource_id']}_{ctx['day']}",
            f"資源重複割当（解チェッカー）: {ctx['resource_name']} ({ctx['day']})",
            f"医療資源「{ctx['resource_name']}」の{ctx['day']}における同時割当件数が"
            f"最大{ctx['peak']:.0f}件に達し、資源容量1件を超過しています"
            f"（時刻{ctx['peak_time']}付近、スイープライン検算による独立検証。"
            "no_overlap制約と矛盾しています）。",
        ),
    ),
    _rule(
        "resource_type_mismatch",
        condition=lambda ctx: ctx["assigned_types"] != ctx["needed_types"],
        build=lambda ctx: solver_bug_issue(
            f"resource_type_mismatch_{ctx['request_id']}",
            f"割当資源タイプ不整合（解チェッカー）: {ctx['request_id']}",
            f"受診依頼「{ctx['request_id']}」の必要資源タイプ{dict(ctx['needed_types'])}に対し、"
            f"実際に割り当てられた資源タイプは{dict(ctx['assigned_types'])}であり、一致しません。",
        ),
    ),
    _rule(
        "avoid_day_violation",
        condition=lambda ctx: ctx["day"] in ctx["avoid_days"],
        build=lambda ctx: solver_bug_issue(
            f"avoid_day_violation_{ctx['request_id']}",
            f"忌避日への割当（解チェッカー）: {ctx['request_id']}",
            f"受診依頼「{ctx['request_id']}」が忌避日として指定された「{ctx['day']}」に"
            "割り当てられています。avoid_days制約と矛盾しています。",
        ),
    ),
]



# ---------------------------------------------------------------------------
# MedicalAppointmentScheduler context ビルダー（2026-09-01 新規、バッチ4）
# ---------------------------------------------------------------------------

def build_medical_appointment_scheduler_contexts(
    assignments: List[Dict],
    requests:    List[Dict],
    resources:   List[Dict],
) -> List[Dict[str, Any]]:
    """
    MedicalAppointmentScheduler 用の独立検証contextを生成する。
    - resource_double_booking: 資源×日の同時割当件数をスイープラインで再計算し、
      no_overlap制約（資源1件=同時刻1件まで）を検算する。
    - resource_type_mismatch: 依頼の必要資源タイプと実際の割当資源タイプを突合する。
    - avoid_day_violation: 忌避日(avoid_days)への割当が無いか確認する。
    solver内部のvar/mdlは一切参照せず、返ってきたassignments・DSL入力のみを使う。
    """
    from collections import Counter

    requests_map:  Dict[str, Dict] = {str(r["id"]): r for r in requests}
    resource_map:  Dict[str, Dict] = {str(r["id"]): r for r in resources}
    ctxs: List[Dict] = []

    intervals_by_rd: Dict[Tuple[str, str], List[Tuple[int, int]]] = {}
    for a in assignments:
        day = a["day"]
        start, end = a["start_min"], a["end_min"]
        for ar in a.get("assigned_resources", []):
            key = (ar["resource_id"], day)
            intervals_by_rd.setdefault(key, []).append((start, end))

    for (rid, day), intervals in intervals_by_rd.items():
        if len(intervals) < 2:
            continue
        events: List[tuple] = []
        for start, end in intervals:
            events.append((start, 1))
            events.append((end, -1))
        peak, peak_time = sweep_peak_usage(events)
        ctxs.append({
            "_rule_id":      "resource_double_booking",
            "resource_id":   rid,
            "resource_name": resource_map.get(rid, {}).get("name", rid),
            "day":           day,
            "peak":          peak,
            "peak_time":     peak_time,
            "capacity":      1,
            "tolerance":     1e-9,
        })

    for a in assignments:
        req = requests_map.get(a["request_id"], {})
        needed_types = Counter(str(t) for t in req.get("resource_type_needs", []))
        assigned_types = Counter(
            str(resource_map.get(ar["resource_id"], {}).get("resource_type", ""))
            for ar in a.get("assigned_resources", [])
        )
        ctxs.append({
            "_rule_id":       "resource_type_mismatch",
            "request_id":     a["request_id"],
            "needed_types":   needed_types,
            "assigned_types": assigned_types,
        })
        ctxs.append({
            "_rule_id":   "avoid_day_violation",
            "request_id": a["request_id"],
            "day":        a["day"],
            "avoid_days": set(str(d) for d in req.get("avoid_days", [])),
        })
    return ctxs
