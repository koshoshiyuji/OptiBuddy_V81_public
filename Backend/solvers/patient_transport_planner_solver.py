"""
Backend/solvers/patient_transport_planner_solver.py

PatientTransportPlannerSolver — 患者送迎配車計画ソルバー

solver_input keys:
  - problem_class: str
  - requests: List[Dict]  送迎依頼リスト
  - vehicles: List[Dict]  送迎車リスト
  - config: Dict          設定（移動速度・乗降時間・タイムリミット等）
  - issue_statuses: Dict  イシューステータス

実装方式: docplex.cp (CP Optimizer)
  - interval_var: 各フェーズ（往路乗車→降車、復路乗車→降車）を optional interval で表現
  - sequence_var + no_overlap: 各車両上のフェーズ順序管理と非重複制約
  - cumulative: 同時乗車定員制約（相乗り可・定員上限あり）
  - lexicographic目的: 対応依頼数最大化（= 未対応ペナルティ最小化）→ 総乗車時間最小化

目的関数設計（lexicographic）:
  - 優先1: 未対応依頼数を最小化（対応依頼数の最大化と等価）
    往復セット: 往路・復路の両方が present のときのみ「対応できた」
    往路のみ・復路のみ: そのフェーズが present のときのみ「対応できた」
  - 優先2: 対応した依頼の総乗車時間（duration の合計）を最小化
"""

from __future__ import annotations

import logging
import math
from typing import Any, Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)

# 移動時間計算用: 平均速度 30 km/h をデフォルトとする
DEFAULT_SPEED_KMH = 30.0
BOARDING_DEFAULT_MIN = 3   # 乗降デフォルト所要時間（分）
SOLVE_TIME_DEFAULT = 30    # ソルブ制限時間（秒）

# 往復セット未達成ペナルティ（片方だけ達成でも未達成扱い）
UNSERVED_PENALTY = 10000
ROUNDTRIP_PARTIAL_EXTRA = 5000  # 往復セットで片方のみ present になった場合の追加ペナルティ


def _travel_min(loc_a: Dict, loc_b: Dict, speed_kmh: float) -> int:
    """2地点間の移動時間（分, 切り上げ整数）を返す。loc は {lat, lng} 形式。"""
    lat1, lng1 = loc_a.get("lat", 0.0), loc_a.get("lng", 0.0)
    lat2, lng2 = loc_b.get("lat", 0.0), loc_b.get("lng", 0.0)
    # 簡易距離計算（度→km 近似: 緯度1度≒111km, 経度1度≒111km×cosθ）
    dlat = abs(lat2 - lat1) * 111.0
    dlng = abs(lng2 - lng1) * 111.0 * math.cos(math.radians((lat1 + lat2) / 2))
    dist_km = math.sqrt(dlat ** 2 + dlng ** 2)
    return max(1, math.ceil(dist_km / speed_kmh * 60))


def _hhmm_to_min(hhmm: str) -> int:
    """'HH:MM' → 分単位整数。"""
    try:
        h, m = hhmm.split(":")
        return int(h) * 60 + int(m)
    except Exception:
        return 0


class PatientTransportPlannerSolver:
    def __init__(self, solver_input: dict) -> None:
        self.dsl = solver_input

    def solve(self) -> dict:
        from solvers.base.solver_error_result import build_solver_crash_issue, solver_crash_extra_fields
        from solvers.base.ce_limit_lns import is_ce_limit_exceeded, CeLimitExceededError

        requests = self.dsl.get("requests", [])
        vehicles = self.dsl.get("vehicles", [])
        config = self.dsl.get("config", {})
        issue_statuses = self.dsl.get("issue_statuses", {})

        if not requests:
            return self._make_result(
                feasible=True,
                solutions=[],
                issues=[{
                    "id": "no_requests",
                    "severity": "INFO",
                    "title": "送迎依頼が0件です",
                    "message": "送迎依頼が入力されていません。",
                    "relatedContainerIds": [],
                }],
            )
        if not vehicles:
            return self._make_result(
                feasible=False,
                solutions=[],
                issues=[{
                    "id": "solve_failed",
                    "severity": "CRITICAL",
                    "title": "車両が登録されていません",
                    "message": "送迎車が1台もありません。",
                    "relatedContainerIds": [],
                }],
            )

        try:
            from solvers.base.engine_select import get_solver_engine, CPSAT
            engine = get_solver_engine(config)
            if engine == CPSAT:
                solution_data = self._solve_with_cpsat(requests, vehicles, config)
            else:
                solution_data = self._build_and_solve(requests, vehicles, config)
        except CeLimitExceededError:
            return self._make_result(
                feasible=False,
                solutions=[],
                issues=[{
                    "id": "ce_limit_no_adapter",
                    "severity": "CRITICAL",
                    "title": "CPLEXの無料版で扱える件数を超えています",
                    "message": "依頼件数または車両数を減らすか、正規ライセンスのご利用をご検討ください。",
                    "relatedContainerIds": [],
                }],
            )
        except Exception as e:
            logger.error(f"[PatientTransportPlanner] solve()例外: {e}", exc_info=True)
            result = self._make_result(
                feasible=False, solutions=[],
                issues=[build_solver_crash_issue(e)],
            )
            result.update(solver_crash_extra_fields(e))
            return result

        if solution_data is None:
            return self._make_result(
                feasible=False,
                solutions=[],
                issues=[{
                    "id": "solve_failed",
                    "severity": "CRITICAL",
                    "category": "INFEASIBLE",
                    "title": "実行可能解が見つかりませんでした",
                    "message": "制約を満たす送迎スケジュールが見つかりませんでした。"
                               "車両台数・稼働時間帯・許容待機時間の緩和をご検討ください。",
                    "relatedContainerIds": [],
                }],
            )

        issues = self._detect_issues(solution_data, requests, vehicles, config, issue_statuses)
        return self._make_result(
            feasible=True,
            solutions=[solution_data],
            issues=issues,
        )

    # ------------------------------------------------------------------
    # ソルブ本体
    # ------------------------------------------------------------------

    def _build_and_solve(
        self,
        requests: List[Dict],
        vehicles: List[Dict],
        config: Dict,
    ) -> Optional[Dict]:
        from docplex.cp.model import CpoModel
        from solvers.base.ce_limit_lns import is_ce_limit_exceeded, CeLimitExceededError
        from solvers.base.solution_extraction import extract_makespan, safe_objective_value, extract_optimality_metadata

        solve_time_sec = int(config.get("solve_time_sec", SOLVE_TIME_DEFAULT))

        phase_meta, roundtrip_exam = self._compute_phase_meta(requests, config)
        eligible_vids = self._compute_eligible_vehicles(phase_meta, vehicles)

        mdl = CpoModel(name="patient_transport_planner")

        # ── フェーズ（interval_var）の生成 ──
        # フェーズID: "{req_id}_outbound" / "{req_id}_inbound"
        # 各フェーズは optional（対応しないことが許容されるため）
        # duration = 乗降時間 + 移動時間

        phase_itvs: Dict[str, Any] = {}      # phase_id -> interval_var
        for phase_id, meta in phase_meta.items():
            phase_itvs[phase_id] = mdl.interval_var(
                name=phase_id,
                optional=True,
                size=meta["duration"],
                start=(meta["tw_start"], meta["tw_end"]),
            )

        # 往復セット順序制約: 復路は往路の終了後（診察時間を挟む）
        for req_id, exam_duration_min in roundtrip_exam.items():
            out_itv = phase_itvs.get(f"{req_id}_outbound")
            inb_itv = phase_itvs.get(f"{req_id}_inbound")
            if out_itv is not None and inb_itv is not None:
                mdl.add(mdl.end_before_start(out_itv, inb_itv, delay=exam_duration_min))

        # ── 車両ごとの sequence_var + no_overlap ──
        # vehicle × phase の optional interval_var
        # assign_itvs[vid][phase_id] = interval_var（vidalternative）
        assign_itvs: Dict[str, Dict[str, Any]] = {str(v["id"]): {} for v in vehicles}
        for phase_id, meta in phase_meta.items():
            for vid in eligible_vids.get(phase_id, []):
                asgn_itv = mdl.interval_var(
                    name=f"asgn_{vid}_{phase_id}",
                    optional=True,
                    size=meta["duration"],
                    start=(meta["tw_start"], meta["tw_end"]),
                )
                assign_itvs[vid][phase_id] = asgn_itv

        # alternative 制約: 各フェーズは高々1台の車両が担当する
        for phase_id, orig_itv in phase_itvs.items():
            alts = [assign_itvs[vid][phase_id]
                    for vid in assign_itvs if phase_id in assign_itvs[vid]]
            if alts:
                mdl.add(mdl.alternative(orig_itv, alts))
            # フェーズを担当できる車両が0台 → このフェーズは必ず absent
            # （no_overlapには参加しない; 自動的に optional で absent）

        # 車両ごとの no_overlap（sequence_var使用）
        for v in vehicles:
            vid = str(v["id"])
            v_itvs = list(assign_itvs[vid].values())
            if len(v_itvs) > 1:
                seq = mdl.sequence_var(v_itvs, name=f"seq_{vid}")
                mdl.add(mdl.no_overlap(seq))
            elif len(v_itvs) == 1:
                pass  # no_overlap 不要

        # 車両ごとの同時乗車定員制約（cumulative）
        # 定員計算: フェーズ中に乗車している患者の capacity_required の合計 <= vehicle capacity
        for v in vehicles:
            vid = str(v["id"])
            cap = int(v.get("capacity", 4))
            pulses = []
            for phase_id, asgn_itv in assign_itvs[vid].items():
                meta = phase_meta[phase_id]
                height = meta["capacity_required"]
                pulses.append(mdl.pulse(asgn_itv, height))
            if pulses:
                mdl.add(mdl.sum(pulses) <= cap)

        # ── 目的関数（lexicographic） ──
        # 優先1: 未対応依頼数の最小化
        # 優先2: 対応した依頼の総乗車時間の最小化

        unserved_terms = []
        ride_time_terms = []

        for req in requests:
            req_id = str(req["id"])
            req_type = req.get("request_type", "roundtrip")

            if req_type == "roundtrip":
                out_itv = phase_itvs.get(f"{req_id}_outbound")
                inb_itv = phase_itvs.get(f"{req_id}_inbound")
                if out_itv is not None and inb_itv is not None:
                    # 両方 present のとき対応済み（= 0）、それ以外はペナルティ
                    both_present = mdl.presence_of(out_itv) * mdl.presence_of(inb_itv)
                    # 未対応: 1 - both_present（往復セット）
                    unserved_terms.append(UNSERVED_PENALTY * (1 - both_present))
                    # 片方のみ present の場合の追加ペナルティ（往復セット部分達成禁止を
                    # ソフトに抑制: 真の意味では alternative 制約で強制はできないため
                    # ペナルティで誘導）
                    only_out = mdl.presence_of(out_itv) * (1 - mdl.presence_of(inb_itv))
                    only_inb = mdl.presence_of(inb_itv) * (1 - mdl.presence_of(out_itv))
                    unserved_terms.append(ROUNDTRIP_PARTIAL_EXTRA * (only_out + only_inb))
                    # 乗車時間: 両方 present のときのみカウント
                    out_meta = phase_meta.get(f"{req_id}_outbound", {})
                    inb_meta = phase_meta.get(f"{req_id}_inbound", {})
                    ride_time_terms.append(
                        both_present * (out_meta.get("duration", 0) + inb_meta.get("duration", 0))
                    )
            elif req_type == "outbound_only":
                out_itv = phase_itvs.get(f"{req_id}_outbound")
                if out_itv is not None:
                    unserved_terms.append(UNSERVED_PENALTY * (1 - mdl.presence_of(out_itv)))
                    out_meta = phase_meta.get(f"{req_id}_outbound", {})
                    ride_time_terms.append(
                        mdl.presence_of(out_itv) * out_meta.get("duration", 0)
                    )
            elif req_type == "inbound_only":
                inb_itv = phase_itvs.get(f"{req_id}_inbound")
                if inb_itv is not None:
                    unserved_terms.append(UNSERVED_PENALTY * (1 - mdl.presence_of(inb_itv)))
                    inb_meta = phase_meta.get(f"{req_id}_inbound", {})
                    ride_time_terms.append(
                        mdl.presence_of(inb_itv) * inb_meta.get("duration", 0)
                    )

        obj1 = mdl.sum(unserved_terms) if unserved_terms else mdl.integer_var(0, 0)
        obj2 = mdl.sum(ride_time_terms) if ride_time_terms else mdl.integer_var(0, 0)
        mdl.add(mdl.minimize_static_lex([obj1, obj2]))

        # ── ソルブ ──
        try:
            msol = mdl.solve(TimeLimit=solve_time_sec, LogVerbosity="Quiet")
        except Exception as e:
            if is_ce_limit_exceeded(e):
                from solvers.base.ce_limit_lns import CeLimitExceededError
                raise CeLimitExceededError(str(e)) from e
            raise

        if msol is None or not msol:
            return None

        # ── 解抽出 ──
        # phase ごとに担当車両と時刻を確定させる
        phase_result: Dict[str, Dict] = {}  # phase_id -> {vehicle_id, start, end, present}
        for v in vehicles:
            vid = str(v["id"])
            for phase_id, asgn_itv in assign_itvs[vid].items():
                var_sol = msol.get_var_solution(asgn_itv)
                if var_sol is None:
                    continue
                if var_sol.is_present():
                    phase_result[phase_id] = {
                        "vehicle_id": vid,
                        "start": var_sol.get_start(),
                        "end": var_sol.get_end(),
                        "present": True,
                    }

        # 依頼ごとの結果集計
        request_results = []
        served_count = 0
        total_ride_time = 0

        for req in requests:
            req_id = str(req["id"])
            req_type = req.get("request_type", "roundtrip")
            out_res = phase_result.get(f"{req_id}_outbound")
            inb_res = phase_result.get(f"{req_id}_inbound")

            if req_type == "roundtrip":
                served = (out_res is not None and out_res["present"] and
                          inb_res is not None and inb_res["present"])
            elif req_type == "outbound_only":
                served = out_res is not None and out_res["present"]
            else:
                served = inb_res is not None and inb_res["present"]

            if served:
                served_count += 1
                if out_res:
                    total_ride_time += out_res["end"] - out_res["start"]
                if inb_res:
                    total_ride_time += inb_res["end"] - inb_res["start"]

            rr: Dict[str, Any] = {
                "request_id": req_id,
                "patient_id": req.get("patient_id", req_id),
                "req_type": req_type,
                "care_type": req.get("care_type", "general"),
                "served": served,
            }
            if out_res:
                rr["outbound_vehicle"] = out_res["vehicle_id"]
                rr["outbound_start"] = out_res["start"]
                rr["outbound_end"] = out_res["end"]
            if inb_res:
                rr["inbound_vehicle"] = inb_res["vehicle_id"]
                rr["inbound_start"] = inb_res["start"]
                rr["inbound_end"] = inb_res["end"]
            request_results.append(rr)

        # 車両別スケジュール
        vehicle_schedules: List[Dict] = []
        for v in vehicles:
            vid = str(v["id"])
            phases_for_v = [
                {**phase_result[pid], "phase_id": pid}
                for pid in phase_result if phase_result[pid]["vehicle_id"] == vid
            ]
            phases_for_v.sort(key=lambda x: x["start"])
            vehicle_schedules.append({
                "vehicle_id": vid,
                "vehicle_name": v.get("name", vid),
                "phases": phases_for_v,
                "total_phases": len(phases_for_v),
            })

        unserved_requests = [r for r in request_results if not r["served"]]

        optimality = extract_optimality_metadata(msol)
        solve_time_val = optimality.get("solve_time_sec") or solve_time_sec

        kpi = {
            "served_count": served_count,
            "total_requests": len(requests),
            "unserved_count": len(requests) - served_count,
            "service_rate": round(served_count / len(requests), 4) if requests else 0.0,
            "total_ride_time_min": total_ride_time,
            "solve_time_sec": round(float(solve_time_val), 2),
            "is_optimal": optimality.get("is_optimal", False),
        }

        from solvers.base.issue_rules import build_full_unassignment_issue
        anomaly = build_full_unassignment_issue(
            assigned_count=served_count,
            total_count=len(requests),
            entity_label="送迎依頼",
            extra_hint="get_var_solution() での解抽出処理",
        )
        # anomaly は _detect_issues で使うため solution_data に含める
        return {
            "feasible": True,
            "request_results": request_results,
            "vehicle_schedules": vehicle_schedules,
            "unserved_requests": unserved_requests,
            "kpi": kpi,
            "_anomaly_issue": anomaly,
        }

    def _solve_with_cpsat(
        self,
        requests: List[Dict],
        vehicles: List[Dict],
        config: Dict,
    ) -> Optional[Dict]:
        """CP-SAT(CPMpy)版。_build_and_solve()と同一の制約セット・目的関数を実装する。

        docplex.cpの alternative()制約は、CPMpyでは「フェーズの開始/終了時刻を
        全候補車両で共有し、高々1台だけがpresentになる」という形で等価に表現できる
        （検証: Backend/tools/pilot_cpmpy_patient_transport_planner.py）。
        往復セットの前後関係はoptional同士なので「両方presentのときのみ」の
        条件付き制約にする。
        """
        import cpmpy as cp
        from solvers.base.cpmpy_lex_minimize import solve_lexicographic
        from solvers.base.engine_select import cpmpy_optimality_metadata
        import time as _time

        solve_time_sec = int(config.get("solve_time_sec", SOLVE_TIME_DEFAULT))

        phase_meta, roundtrip_exam = self._compute_phase_meta(requests, config)
        eligible_vids = self._compute_eligible_vehicles(phase_meta, vehicles)

        m = cp.Model()

        phase_start: Dict[str, Any] = {}
        phase_end: Dict[str, Any] = {}
        phase_present: Dict[str, Any] = {}  # boolvar または False(担当車両無し)
        alt_present: Dict[Tuple[str, str], Any] = {}  # (vid, phase_id) -> boolvar

        for phase_id, meta in phase_meta.items():
            s = cp.intvar(meta["tw_start"], meta["tw_end"], name=f"start_{phase_id}")
            e = s + meta["duration"]
            phase_start[phase_id] = s
            phase_end[phase_id] = e

            presents_for_phase = []
            for vid in eligible_vids.get(phase_id, []):
                av = cp.boolvar(name=f"alt_{vid}_{phase_id}")
                alt_present[(vid, phase_id)] = av
                presents_for_phase.append(av)

            if presents_for_phase:
                m += (cp.sum(presents_for_phase) <= 1)
                pp = cp.boolvar(name=f"present_{phase_id}")
                m += (pp == (cp.sum(presents_for_phase) >= 1))
                phase_present[phase_id] = pp
            else:
                phase_present[phase_id] = False

        # 往復セット順序制約(optional同士: 両方presentのときのみ)
        for req_id, exam_duration_min in roundtrip_exam.items():
            out_pid, inb_pid = f"{req_id}_outbound", f"{req_id}_inbound"
            out_p, inb_p = phase_present.get(out_pid), phase_present.get(inb_pid)
            if out_p is False or inb_p is False or out_p is None or inb_p is None:
                continue
            m += (out_p & inb_p).implies(
                phase_end[out_pid] + exam_duration_min <= phase_start[inb_pid]
            )

        # 車両ごとの no_overlap + cumulative(定員)
        for v in vehicles:
            vid = str(v["id"])
            cap = int(v.get("capacity", 4))
            starts, durs, ends, presents, demands = [], [], [], [], []
            for phase_id, meta in phase_meta.items():
                key = (vid, phase_id)
                if key not in alt_present:
                    continue
                starts.append(phase_start[phase_id])
                durs.append(meta["duration"])
                ends.append(phase_end[phase_id])
                presents.append(alt_present[key])
                demands.append(alt_present[key] * meta["capacity_required"])

            if len(starts) > 1:
                m += cp.NoOverlapOptional(starts, durs, ends, presents)
            if demands:
                m += cp.Cumulative(starts, durs, ends, demands, cap)

        # ── 目的関数(lexicographic) ──
        unserved_terms = []
        ride_time_terms = []

        handled_phase_ids = set()
        for req_id, exam_duration_min in roundtrip_exam.items():
            out_pid, inb_pid = f"{req_id}_outbound", f"{req_id}_inbound"
            out_p, inb_p = phase_present.get(out_pid), phase_present.get(inb_pid)
            handled_phase_ids.add(out_pid)
            handled_phase_ids.add(inb_pid)

            if out_p is False or inb_p is False:
                unserved_terms.append(UNSERVED_PENALTY)
                continue

            both_present = out_p & inb_p
            unserved_terms.append(UNSERVED_PENALTY * (1 - both_present))
            only_out = out_p & (~inb_p)
            only_inb = inb_p & (~out_p)
            unserved_terms.append(ROUNDTRIP_PARTIAL_EXTRA * (only_out + only_inb))

            out_dur = phase_meta[out_pid]["duration"]
            inb_dur = phase_meta[inb_pid]["duration"]
            ride_time_terms.append(both_present * (out_dur + inb_dur))

        for req in requests:
            req_id = str(req["id"])
            req_type = req.get("request_type", "roundtrip")
            if req_type == "roundtrip":
                continue
            phase_id = f"{req_id}_outbound" if req_type == "outbound_only" else f"{req_id}_inbound"
            if phase_id in handled_phase_ids or phase_id not in phase_present:
                continue
            pp = phase_present[phase_id]
            if pp is False:
                unserved_terms.append(UNSERVED_PENALTY)
            else:
                unserved_terms.append(UNSERVED_PENALTY * (1 - pp))
                ride_time_terms.append(pp * phase_meta[phase_id]["duration"])

        obj1 = cp.sum(unserved_terms) if unserved_terms else cp.intvar(0, 0)
        obj2 = cp.sum(ride_time_terms) if ride_time_terms else cp.intvar(0, 0)

        t0 = _time.perf_counter()
        solved, achieved = solve_lexicographic(m, [obj1, obj2], solver="ortools", time_limit=solve_time_sec)
        solve_time_val = _time.perf_counter() - t0

        if not solved:
            return None

        meta_info = cpmpy_optimality_metadata(m)

        # ── 解抽出 ──
        phase_result: Dict[str, Dict] = {}
        for phase_id in phase_meta:
            for v in vehicles:
                vid = str(v["id"])
                key = (vid, phase_id)
                if key in alt_present and alt_present[key].value():
                    phase_result[phase_id] = {
                        "vehicle_id": vid,
                        "start": int(phase_start[phase_id].value()),
                        "end": int(phase_end[phase_id].value()),
                        "present": True,
                    }
                    break

        request_results = []
        served_count = 0
        total_ride_time = 0

        for req in requests:
            req_id = str(req["id"])
            req_type = req.get("request_type", "roundtrip")
            out_res = phase_result.get(f"{req_id}_outbound")
            inb_res = phase_result.get(f"{req_id}_inbound")

            if req_type == "roundtrip":
                served = (out_res is not None and inb_res is not None)
            elif req_type == "outbound_only":
                served = out_res is not None
            else:
                served = inb_res is not None

            if served:
                served_count += 1
                if out_res:
                    total_ride_time += out_res["end"] - out_res["start"]
                if inb_res:
                    total_ride_time += inb_res["end"] - inb_res["start"]

            rr: Dict[str, Any] = {
                "request_id": req_id,
                "patient_id": req.get("patient_id", req_id),
                "req_type": req_type,
                "care_type": req.get("care_type", "general"),
                "served": served,
            }
            if out_res:
                rr["outbound_vehicle"] = out_res["vehicle_id"]
                rr["outbound_start"] = out_res["start"]
                rr["outbound_end"] = out_res["end"]
            if inb_res:
                rr["inbound_vehicle"] = inb_res["vehicle_id"]
                rr["inbound_start"] = inb_res["start"]
                rr["inbound_end"] = inb_res["end"]
            request_results.append(rr)

        vehicle_schedules: List[Dict] = []
        for v in vehicles:
            vid = str(v["id"])
            phases_for_v = [
                {**phase_result[pid], "phase_id": pid}
                for pid in phase_result if phase_result[pid]["vehicle_id"] == vid
            ]
            phases_for_v.sort(key=lambda x: x["start"])
            vehicle_schedules.append({
                "vehicle_id": vid,
                "vehicle_name": v.get("name", vid),
                "phases": phases_for_v,
                "total_phases": len(phases_for_v),
            })

        unserved_requests = [r for r in request_results if not r["served"]]

        kpi = {
            "served_count": served_count,
            "total_requests": len(requests),
            "unserved_count": len(requests) - served_count,
            "service_rate": round(served_count / len(requests), 4) if requests else 0.0,
            "total_ride_time_min": total_ride_time,
            "solve_time_sec": round(float(solve_time_val), 2),
            "is_optimal": meta_info.get("is_optimal", False),
        }

        from solvers.base.issue_rules import build_full_unassignment_issue
        anomaly = build_full_unassignment_issue(
            assigned_count=served_count,
            total_count=len(requests),
            entity_label="送迎依頼",
            extra_hint="cp.boolvar().value() での解抽出処理",
        )
        return {
            "feasible": True,
            "request_results": request_results,
            "vehicle_schedules": vehicle_schedules,
            "unserved_requests": unserved_requests,
            "kpi": kpi,
            "_anomaly_issue": anomaly,
        }

    # ------------------------------------------------------------------
    # フェーズメタ情報計算（エンジン非依存、_build_and_solve()/_solve_with_cpsat()共通）
    # ------------------------------------------------------------------

    def _compute_phase_meta(
        self, requests: List[Dict], config: Dict
    ) -> Tuple[Dict[str, Dict], Dict[str, int]]:
        """
        各送迎依頼からフェーズ(往路/復路)のメタ情報(時間窓・duration等)を計算する。
        docplex/cpmpyのどちらにも依存しない純Python処理。

        Returns:
            (phase_meta, roundtrip_exam)
            phase_meta: phase_id -> {duration, tw_start, tw_end, capacity_required,
                                      care_type, req_id, phase, req_type, ...}
            roundtrip_exam: req_id -> exam_duration_min（roundtripのreqのみ）
        """
        speed_kmh = float(config.get("speed_kmh", DEFAULT_SPEED_KMH))
        default_boarding_min = int(config.get("boarding_time_min", BOARDING_DEFAULT_MIN))
        hospital_loc = config.get("hospital_location", {"lat": 0.0, "lng": 0.0})

        phase_meta: Dict[str, Dict] = {}
        roundtrip_exam: Dict[str, int] = {}

        for req in requests:
            req_id = str(req["id"])
            req_type = req.get("request_type", "roundtrip")
            home_loc = req.get("home_location", {"lat": 0.0, "lng": 0.0})
            appointment_min = _hhmm_to_min(str(req.get("appointment_time", "09:00")))
            exam_duration_min = int(req.get("exam_duration_min", 60))
            wait_tolerance_min = int(req.get("wait_tolerance_min", 30))
            boarding_min = int(req.get("boarding_time_min", default_boarding_min))
            capacity_required = int(req.get("capacity_required", 1))
            care_type = req.get("care_type", "general")

            travel_to_hosp = _travel_min(home_loc, hospital_loc, speed_kmh)
            travel_to_home = _travel_min(hospital_loc, home_loc, speed_kmh)

            if req_type in ("outbound_only", "roundtrip"):
                out_duration = boarding_min + travel_to_hosp + boarding_min
                out_tw_start = appointment_min - wait_tolerance_min - travel_to_hosp - boarding_min
                out_tw_end = appointment_min - travel_to_hosp - boarding_min
                out_tw_start = max(0, out_tw_start)
                out_tw_end = max(out_tw_start + 1, out_tw_end)

                phase_meta[f"{req_id}_outbound"] = {
                    "req_id": req_id, "phase": "outbound", "req_type": req_type,
                    "duration": out_duration, "tw_start": out_tw_start, "tw_end": out_tw_end,
                    "capacity_required": capacity_required, "care_type": care_type,
                    "home_loc": home_loc, "appointment_min": appointment_min,
                }

            if req_type in ("inbound_only", "roundtrip"):
                inb_duration = boarding_min + travel_to_home + boarding_min
                inb_tw_start = appointment_min + exam_duration_min
                inb_tw_end = inb_tw_start + wait_tolerance_min
                inb_tw_end = max(inb_tw_start + 1, inb_tw_end)

                phase_meta[f"{req_id}_inbound"] = {
                    "req_id": req_id, "phase": "inbound", "req_type": req_type,
                    "duration": inb_duration, "tw_start": inb_tw_start, "tw_end": inb_tw_end,
                    "capacity_required": capacity_required, "care_type": care_type,
                    "home_loc": home_loc, "appointment_min": appointment_min,
                }

            if req_type == "roundtrip":
                if f"{req_id}_outbound" in phase_meta and f"{req_id}_inbound" in phase_meta:
                    roundtrip_exam[req_id] = exam_duration_min

        return phase_meta, roundtrip_exam

    def _compute_eligible_vehicles(
        self, phase_meta: Dict[str, Dict], vehicles: List[Dict]
    ) -> Dict[str, List[str]]:
        """各フェーズについて担当可能な車両IDリストを計算する（純Python）。"""
        care_type_compat: Dict[str, set] = {}
        op_hours: Dict[str, Tuple[int, int]] = {}
        for v in vehicles:
            vid = str(v["id"])
            care_type_compat[vid] = set(v.get("care_types", ["general"]))
            op_hours[vid] = (
                _hhmm_to_min(str(v.get("start_time", "08:00"))),
                _hhmm_to_min(str(v.get("end_time", "18:00"))),
            )

        eligible: Dict[str, List[str]] = {}
        for phase_id, meta in phase_meta.items():
            elig_list = []
            for v in vehicles:
                vid = str(v["id"])
                if meta["care_type"] not in care_type_compat[vid]:
                    continue
                op_start_min, op_end_min = op_hours[vid]
                if meta["tw_end"] + meta["duration"] > op_end_min:
                    continue
                if meta["tw_start"] < op_start_min:
                    continue
                elig_list.append(vid)
            eligible[phase_id] = elig_list
        return eligible

    # ------------------------------------------------------------------
    # イシュー検出
    # ------------------------------------------------------------------

    def _detect_issues(
        self,
        solution_data: Dict,
        requests: List[Dict],
        vehicles: List[Dict],
        config: Dict,
        issue_statuses: Dict,
    ) -> List[Dict]:
        from solvers.base.issue_rules import build_patient_transport_planner_contexts, run_issue_rules

        issues: List[Dict] = []

        # 全件未割当異常
        anomaly = solution_data.pop("_anomaly_issue", None)
        if anomaly:
            issues.append(anomaly)

        # 解チェッカー（2026-09-01追加、バッチ4）: 車両定員超過・車両フェーズ重複・往復順序制約・
        # 時間窓逸脱(任意)の独立検証
        checker_ctxs = build_patient_transport_planner_contexts(
            solution_data.get("request_results", []), requests, vehicles, config,
        )
        issues.extend(run_issue_rules(
            domain="PatientTransportPlanner", contexts=checker_ctxs, issue_statuses=issue_statuses,
        ))

        unserved = solution_data.get("unserved_requests", [])
        if unserved:
            vehicle_names = {str(v["id"]): v.get("name", v["id"]) for v in vehicles}
            for r in unserved:
                iid = f"unserved_{r['request_id']}"
                if issue_statuses.get(iid) == "ACCEPTED":
                    continue
                reason = self._estimate_unserved_reason(r, requests, vehicles, config)
                issues.append({
                    "id": iid,
                    "severity": "WARNING",
                    "title": f"未対応: 患者 {r.get('patient_id', r['request_id'])}",
                    "message": f"区分: {r['care_type']}、タイプ: {r['req_type']}。{reason}",
                    "relatedContainerIds": [],
                })

        # 対応率が低い場合のCRITICAL
        kpi = solution_data.get("kpi", {})
        if kpi.get("service_rate", 1.0) < 0.5:
            issues.append({
                "id": "low_service_rate",
                "severity": "CRITICAL",
                "title": "対応率が50%未満です",
                "message": (
                    f"対応できた依頼は {kpi['served_count']} / {kpi['total_requests']} 件"
                    f"（{round(kpi['service_rate']*100)}%）です。"
                    "車両台数の増加・稼働時間帯の延長・許容待機時間の拡大をご検討ください。"
                ),
                "relatedContainerIds": [],
            })

        # 解の最適性未証明の場合の INFO
        if not kpi.get("is_optimal", True):
            issues.append({
                "id": "not_proven_optimal",
                "severity": "INFO",
                "title": "計算時間内で見つかった暫定解です",
                "message": (
                    f"制限時間（{config.get('solve_time_sec', SOLVE_TIME_DEFAULT)}秒）内で"
                    "見つかった実行可能解であり、最適性は証明されていません。"
                    "さらに良い配車計画が存在する可能性があります。"
                ),
                "relatedContainerIds": [],
            })

        return issues

    def _estimate_unserved_reason(
        self,
        unserved_req: Dict,
        requests: List[Dict],
        vehicles: List[Dict],
        config: Dict,
    ) -> str:
        req_id = unserved_req["request_id"]
        care_type = unserved_req.get("care_type", "general")
        capable_vehicles = [v for v in vehicles if care_type in v.get("care_types", ["general"])]
        if not capable_vehicles:
            return f"対応可能な車両（区分: {care_type}）がありません。"
        return "時間帯の競合または定員不足のため対応できませんでした。車両追加または時間帯緩和をご検討ください。"

    # ------------------------------------------------------------------
    # 結果フォーマット
    # ------------------------------------------------------------------

    def _make_result(
        self,
        feasible: bool,
        solutions: List[Dict],
        issues: List[Dict],
    ) -> Dict:
        return {
            "status": "ok",
            "feasible": feasible,
            "metadata": {"problem_class": "PatientTransportPlanner"},
            "solutions": solutions,
            "issues": issues,
            "_solver_version": "patient_transport_planner_v1.0",
        }