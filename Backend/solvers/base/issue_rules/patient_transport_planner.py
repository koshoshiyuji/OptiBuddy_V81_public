
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable, Dict, List, Optional, Tuple

from solvers.base.solution_checker import solver_bug_issue, sweep_peak_usage
from i18n.common_messages import t as _common_t
from i18n.nurse_shift_weekly_cap_messages import t as _nurse_shift_t

from ._common import IssueRule, _rule




# ---------------------------------------------------------------------------
# _PATIENT_TRANSPORT_PLANNER_RULES（2026-09-01 新規、solution checker展開バッチ4）
# ---------------------------------------------------------------------------
_PATIENT_TRANSPORT_PLANNER_RULES: List[IssueRule] = [
    _rule(
        "vehicle_capacity_violation",
        condition=lambda ctx: ctx["peak"] > ctx["capacity"],
        build=lambda ctx: solver_bug_issue(
            f"vehicle_capacity_violation_{ctx['vehicle_id']}",
            f"車両定員超過（解チェッカー）: {ctx['vehicle_name']}",
            f"車両「{ctx['vehicle_name']}」の同時乗車定員（capacity_required合計）が最大"
            f"{ctx['peak']:.0f}に達し、定員{ctx['capacity']}を超過しています"
            f"（時刻{ctx['peak_time']}付近、スイープライン検算による独立検証）。",
        ),
    ),
    _rule(
        "vehicle_phase_overlap",
        condition=lambda ctx: ctx["peak"] > 1,
        build=lambda ctx: solver_bug_issue(
            f"vehicle_phase_overlap_{ctx['vehicle_id']}",
            f"車両フェーズ重複（解チェッカー）: {ctx['vehicle_name']}",
            f"車両「{ctx['vehicle_name']}」に割り当てられた2件以上のフェーズが時間的に重複しています"
            f"（時刻{ctx['peak_time']}付近、no_overlap制約の独立検証）。",
        ),
    ),
    _rule(
        "roundtrip_order_violation",
        condition=lambda ctx: ctx["inbound_start"] < ctx["outbound_end"] + ctx["exam_duration_min"],
        build=lambda ctx: solver_bug_issue(
            f"roundtrip_order_violation_{ctx['request_id']}",
            f"往復順序制約違反（解チェッカー）: 依頼{ctx['request_id']}",
            f"依頼「{ctx['request_id']}」の復路開始（{ctx['inbound_start']}）が、往路終了"
            f"（{ctx['outbound_end']}）+ 診察時間（{ctx['exam_duration_min']}分）より前です。",
        ),
    ),
    _rule(
        "phase_outside_time_window",
        condition=lambda ctx: ctx["start"] < ctx["tw_start"] or ctx["start"] > ctx["tw_end"],
        build=lambda ctx: solver_bug_issue(
            f"phase_outside_time_window_{ctx['request_id']}_{ctx['phase']}",
            f"時間窓逸脱（解チェッカー）: 依頼{ctx['request_id']}（{ctx['phase']}）",
            f"依頼「{ctx['request_id']}」の{ctx['phase']}フェーズの開始時刻（{ctx['start']}）が、"
            f"許容時間窓[{ctx['tw_start']}, {ctx['tw_end']}]の外です。",
        ),
    ),
]



# ---------------------------------------------------------------------------
# PatientTransportPlanner context ビルダー（2026-09-01 新規、バッチ4）
# ---------------------------------------------------------------------------

def build_patient_transport_planner_contexts(
    request_results: List[Dict],
    requests:        List[Dict],
    vehicles:        List[Dict],
    config:          Dict,
) -> List[Dict[str, Any]]:
    """
    PatientTransportPlanner 用の独立検証contextを生成する。
    - vehicle_capacity_violation: 車両ごとにcapacity_requiredの同時使用量をスイープラインで
      再計算し、車両定員(capacity)を超過していないか検算する。
    - vehicle_phase_overlap: 車両に割り当てられたフェーズ（往路/復路）が時間的に重複して
      いないかを検算する（no_overlap制約の独立検証）。
    - roundtrip_order_violation: 往復依頼で、復路開始が往路終了+診察時間より後になっている
      かを確認する。
    - phase_outside_time_window（任意チェック）: solver本体の_compute_phase_metaと同じ式で
      往路/復路それぞれの許容時間窓を再計算し、実際の開始時刻がその範囲内かを確認する。
    solver内部のinterval_var/mdlは一切参照せず、返ってきたrequest_results・DSL入力のみを使う。
    """
    import math

    requests_map = {str(r["id"]): r for r in requests}
    vehicles_map = {str(v["id"]): v for v in vehicles}
    ctxs: List[Dict] = []

    phases_by_vehicle: Dict[str, List[Dict]] = {}
    for rr in request_results:
        req_id = rr["request_id"]
        req = requests_map.get(req_id, {})
        cap_req = int(req.get("capacity_required", 1))
        if "outbound_vehicle" in rr:
            phases_by_vehicle.setdefault(rr["outbound_vehicle"], []).append({
                "req_id": req_id, "phase": "outbound",
                "start": rr["outbound_start"], "end": rr["outbound_end"],
                "capacity_required": cap_req,
            })
        if "inbound_vehicle" in rr:
            phases_by_vehicle.setdefault(rr["inbound_vehicle"], []).append({
                "req_id": req_id, "phase": "inbound",
                "start": rr["inbound_start"], "end": rr["inbound_end"],
                "capacity_required": cap_req,
            })

    for vid, phases in phases_by_vehicle.items():
        v = vehicles_map.get(vid, {})
        vname = v.get("name", vid)
        cap_events = [(p["start"], p["capacity_required"]) for p in phases] +                      [(p["end"], -p["capacity_required"]) for p in phases]
        peak, peak_time = sweep_peak_usage(cap_events)
        ctxs.append({
            "_rule_id":    "vehicle_capacity_violation",
            "vehicle_id":  vid,
            "vehicle_name": vname,
            "peak":        peak,
            "capacity":    int(v.get("capacity", 4)),
            "peak_time":   peak_time,
        })
        overlap_events = [(p["start"], 1) for p in phases] + [(p["end"], -1) for p in phases]
        opeak, opeak_time = sweep_peak_usage(overlap_events)
        ctxs.append({
            "_rule_id":     "vehicle_phase_overlap",
            "vehicle_id":   vid,
            "vehicle_name": vname,
            "peak":         opeak,
            "peak_time":    opeak_time,
        })

    for rr in request_results:
        if rr.get("req_type") != "roundtrip":
            continue
        if "outbound_end" not in rr or "inbound_start" not in rr:
            continue
        req = requests_map.get(rr["request_id"], {})
        ctxs.append({
            "_rule_id":          "roundtrip_order_violation",
            "request_id":        rr["request_id"],
            "outbound_end":      rr["outbound_end"],
            "inbound_start":     rr["inbound_start"],
            "exam_duration_min": int(req.get("exam_duration_min", 60)),
        })

    speed_kmh = float(config.get("speed_kmh", 30.0))
    default_boarding_min = int(config.get("boarding_time_min", 3))
    hospital_loc = config.get("hospital_location", {"lat": 0.0, "lng": 0.0})

    def _travel_min(a, b):
        dlat = abs(b.get("lat", 0.0) - a.get("lat", 0.0)) * 111.0
        dlng = abs(b.get("lng", 0.0) - a.get("lng", 0.0)) * 111.0 *             math.cos(math.radians((a.get("lat", 0.0) + b.get("lat", 0.0)) / 2))
        return max(1, math.ceil(math.sqrt(dlat ** 2 + dlng ** 2) / speed_kmh * 60))

    def _hhmm(s):
        h, m = str(s).split(":")
        return int(h) * 60 + int(m)

    for rr in request_results:
        req = requests_map.get(rr["request_id"], {})
        if not req:
            continue
        home = req.get("home_location", {"lat": 0.0, "lng": 0.0})
        appt = _hhmm(req.get("appointment_time", "09:00"))
        wait_tol = int(req.get("wait_tolerance_min", 30))
        exam_dur = int(req.get("exam_duration_min", 60))
        boarding = int(req.get("boarding_time_min", default_boarding_min))

        if "outbound_start" in rr:
            trav = _travel_min(home, hospital_loc)
            tw_start = max(0, appt - wait_tol - trav - boarding)
            tw_end = max(tw_start + 1, appt - trav - boarding)
            ctxs.append({
                "_rule_id":   "phase_outside_time_window",
                "request_id": rr["request_id"],
                "phase":      "outbound",
                "start":      rr["outbound_start"],
                "tw_start":   tw_start,
                "tw_end":     tw_end,
            })
        if "inbound_start" in rr:
            tw_start = appt + exam_dur
            tw_end = max(tw_start + 1, tw_start + wait_tol)
            ctxs.append({
                "_rule_id":   "phase_outside_time_window",
                "request_id": rr["request_id"],
                "phase":      "inbound",
                "start":      rr["inbound_start"],
                "tw_start":   tw_start,
                "tw_end":     tw_end,
            })
    return ctxs
