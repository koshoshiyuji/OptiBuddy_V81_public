
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable, Dict, List, Optional, Tuple

from solvers.base.solution_checker import solver_bug_issue, sweep_peak_usage
from i18n.common_messages import t as _common_t
from i18n.nurse_shift_weekly_cap_messages import t as _nurse_shift_t

from ._common import IssueRule, _rule




# ---------------------------------------------------------------------------
# _MEDICAL_APPOINTMENT_SEQUENCE_SCHEDULER_RULES（2026-09-01 新規、solution checker展開バッチ4）
# ---------------------------------------------------------------------------
_MEDICAL_APPOINTMENT_SEQUENCE_SCHEDULER_RULES: List[IssueRule] = [
    _rule(
        "resource_double_booking",
        condition=lambda ctx: ctx["peak"] > 1,
        build=lambda ctx: solver_bug_issue(
            f"resource_double_booking_{ctx['resource_id']}",
            f"医療資源の重複割当（解チェッカー）: {ctx['resource_name']}",
            f"医療資源「{ctx['resource_name']}」が同時刻に最大{int(ctx['peak'])}件の受診に"
            f"割り当てられています（時刻{ctx['peak_time']}付近、スイープライン検算による"
            "独立検証）。no_overlap制約と矛盾しています。",
        ),
    ),
    _rule(
        "visit_outside_resource_window",
        condition=lambda ctx: not any(
            ctx["start_min"] >= s["start_min"] and ctx["end_min"] <= s["end_min"]
            for s in ctx["available_slots"]
        ),
        build=lambda ctx: solver_bug_issue(
            f"visit_outside_resource_window_{ctx['visit_id']}_{ctx['resource_id']}",
            f"受診が医療資源の稼働時間外（解チェッカー）: {ctx['visit_id']}",
            f"受診「{ctx['visit_id']}」の割当区間[{ctx['start_min']}, {ctx['end_min']}]が、"
            f"資源「{ctx['resource_name']}」のいずれの稼働スロットにも収まっていません。",
        ),
    ),
    _rule(
        "sequence_gap_violation",
        condition=lambda ctx: (
            ctx["to_start"] - ctx["from_end"] < ctx["min_gap"]
            or (ctx["max_gap"] is not None and ctx["to_start"] - ctx["from_end"] > ctx["max_gap"])
        ),
        build=lambda ctx: solver_bug_issue(
            f"sequence_gap_violation_{ctx['sequence_id']}_{ctx['from_visit_id']}_{ctx['to_visit_id']}",
            f"系列内の間隔制約違反（解チェッカー）: {ctx['sequence_id']}",
            f"系列「{ctx['sequence_id']}」の受診「{ctx['from_visit_id']}」→「{ctx['to_visit_id']}」"
            f"の間隔が{ctx['to_start'] - ctx['from_end']}分で、"
            f"要求範囲[{ctx['min_gap']}, {ctx['max_gap']}]（{ctx['rule_label']}）を満たしていません。",
        ),
    ),
    _rule(
        "same_resource_rule_violation",
        condition=lambda ctx: ctx["shared_resource_ids_a"].isdisjoint(ctx["shared_resource_ids_b"]),
        build=lambda ctx: solver_bug_issue(
            f"same_resource_rule_violation_{ctx['sequence_id']}_{ctx['visit_a_id']}_{ctx['visit_b_id']}",
            f"継続担当制約違反（解チェッカー）: {ctx['sequence_id']}",
            f"系列「{ctx['sequence_id']}」の受診「{ctx['visit_a_id']}」と「{ctx['visit_b_id']}」は"
            "同一資源を使う必要がありますが、割り当てられた資源が一致しません。",
        ),
    ),
    _rule(
        "visit_resource_time_mismatch",
        condition=lambda ctx: len({(ar["start_min"], ar["end_min"]) for ar in ctx["assigned_resources"]}) > 1,
        build=lambda ctx: solver_bug_issue(
            f"visit_resource_time_mismatch_{ctx['visit_id']}",
            f"受診内で資源ごとに開始/終了が不一致（解チェッカー）: {ctx['visit_id']}",
            f"受診「{ctx['visit_id']}」に割り当てられた複数資源の開始/終了時刻が一致していません: "
            f"{[(ar['resource_id'], ar['start_min'], ar['end_min']) for ar in ctx['assigned_resources']]}",
        ),
    ),
]



# ---------------------------------------------------------------------------
# MedicalAppointmentSequenceScheduler context ビルダー（2026-09-01 新規、バッチ4）
# ---------------------------------------------------------------------------

def build_medical_appointment_sequence_scheduler_contexts(
    scheduled: List[Dict],
    sequences: List[Dict],
    resources: List[Dict],
) -> List[Dict[str, Any]]:
    """
    MedicalAppointmentSequenceScheduler (CSPLib prob091 MASSP) 用の独立検証contextを生成する。
    - resource_double_booking: 資源単位で開始/終了イベントをスイープし、同時使用数を検算する。
    - visit_outside_resource_window: 各受診の割当区間が、割り当てられた資源の稼働スロットに
      収まっているかを確認する。
    - sequence_gap_violation: 系列内の受診順序間隔（連続する受診間、およびinterval_rulesで
      明示された間隔）を検算する。interval_rulesのfrom_visit/to_visitはvisitのorder値で
      あり、visit_id文字列ではない点に注意（solver本体と同じvisit_by_order解決を踏襲）。
    - same_resource_rule_violation: same_resource_rulesで指定された2受診が、共通の資源タイプ
      について同一資源を使っているかを確認する。
    - visit_resource_time_mismatch（任意チェック）: 1受診に複数資源が割り当てられている場合、
      各資源のstart_min/end_minが一致しているかを確認する。solver側は受診レベルの
      start_min/end_minをassigned_resourcesのmin/maxで集約しているため、個々の資源の
      時刻ズレはそのままでは検出できない。これを独立に検算する。
    """
    resources_map:    Dict[str, Dict] = {str(r["id"]): r for r in resources}
    scheduled_seq_map: Dict[str, Dict] = {s["sequence_id"]: s for s in scheduled}
    ctxs: List[Dict] = []

    events_by_resource: Dict[str, List[tuple]] = {}
    for s in scheduled:
        for v in s["visits"]:
            for ar in v.get("assigned_resources", []):
                rid = ar["resource_id"]
                events_by_resource.setdefault(rid, []).append((ar["start_min"], 1))
                events_by_resource.setdefault(rid, []).append((ar["end_min"], -1))
    for rid, events in events_by_resource.items():
        peak, peak_time = sweep_peak_usage(events)
        ctxs.append({
            "_rule_id":      "resource_double_booking",
            "resource_id":   rid,
            "resource_name": resources_map.get(rid, {}).get("name", rid),
            "peak":          peak,
            "peak_time":     peak_time,
        })

    for s in scheduled:
        for v in s["visits"]:
            for ar in v.get("assigned_resources", []):
                rid = ar["resource_id"]
                res = resources_map.get(rid, {})
                ctxs.append({
                    "_rule_id":        "visit_outside_resource_window",
                    "visit_id":        v["visit_id"],
                    "resource_id":     rid,
                    "resource_name":   res.get("name", rid),
                    "start_min":       ar["start_min"],
                    "end_min":         ar["end_min"],
                    "available_slots": res.get("available_slots", []),
                })

    for s in scheduled:
        for v in s["visits"]:
            if len(v.get("assigned_resources", [])) > 1:
                ctxs.append({
                    "_rule_id":  "visit_resource_time_mismatch",
                    "visit_id":  v["visit_id"],
                    "assigned_resources": v["assigned_resources"],
                })

    for seq in sequences:
        seq_id = seq["id"]
        sdata = scheduled_seq_map.get(seq_id)
        if sdata is None:
            continue
        dsl_visits = sorted(seq.get("visits", []), key=lambda v: v.get("order", 0))
        visit_by_order = {v.get("order", i): v for i, v in enumerate(dsl_visits)}
        sched_by_id = {v["visit_id"]: v for v in sdata["visits"]}
        sched_sorted = sorted(sdata["visits"], key=lambda v: v["visit_order"])

        for vi, vj in zip(sched_sorted, sched_sorted[1:]):
            ctxs.append({
                "_rule_id":      "sequence_gap_violation",
                "sequence_id":   seq_id,
                "from_visit_id": vi["visit_id"],
                "to_visit_id":   vj["visit_id"],
                "from_end":      vi["end_min"],
                "to_start":      vj["start_min"],
                "min_gap":       0,
                "max_gap":       None,
                "rule_label":    "順序制約",
            })

        for rule in seq.get("interval_rules", []):
            v_from = visit_by_order.get(rule.get("from_visit"))
            v_to   = visit_by_order.get(rule.get("to_visit"))
            if v_from is None or v_to is None:
                continue
            sf, st = sched_by_id.get(v_from["id"]), sched_by_id.get(v_to["id"])
            if sf is None or st is None:
                continue
            min_gap, max_gap = rule.get("min_gap", 0), rule.get("max_gap", None)
            if rule.get("unit", "min") == "day":
                min_gap = min_gap * 24 * 60
                max_gap = max_gap * 24 * 60 if max_gap is not None else None
            ctxs.append({
                "_rule_id":      "sequence_gap_violation",
                "sequence_id":   seq_id,
                "from_visit_id": v_from["id"],
                "to_visit_id":   v_to["id"],
                "from_end":      sf["end_min"],
                "to_start":      st["start_min"],
                "min_gap":       min_gap,
                "max_gap":       max_gap,
                "rule_label":    f"interval_rule({rule.get('from_visit')}->{rule.get('to_visit')})",
            })

        for rule in seq.get("same_resource_rules", []):
            v_a = visit_by_order.get(rule.get("visit_a"))
            v_b = visit_by_order.get(rule.get("visit_b"))
            if v_a is None or v_b is None:
                continue
            sa, sb = sched_by_id.get(v_a["id"]), sched_by_id.get(v_b["id"])
            if sa is None or sb is None:
                continue
            shared_types = (
                {r["type"] for r in v_a.get("required_resources", [])}
                & {r["type"] for r in v_b.get("required_resources", [])}
            )
            if not shared_types:
                continue
            ctxs.append({
                "_rule_id":    "same_resource_rule_violation",
                "sequence_id": seq_id,
                "visit_a_id":  v_a["id"],
                "visit_b_id":  v_b["id"],
                "shared_resource_ids_a": {
                    ar["resource_id"] for ar in sa.get("assigned_resources", [])
                    if ar["resource_type"] in shared_types
                },
                "shared_resource_ids_b": {
                    ar["resource_id"] for ar in sb.get("assigned_resources", [])
                    if ar["resource_type"] in shared_types
                },
            })
    return ctxs
