"""
Backend/solvers/medical_appointment_scheduler_solver.py

MedicalAppointmentSchedulerSolver — 外来診療予約スケジューリング（CP Optimizer）

CSPLib prob089 MASP に基づく制約プログラミング実装。

solver_input keys:
  - requests: List[Dict]   受診依頼リスト
  - resources: List[Dict]  医療資源リスト
  - slots: List[Dict]      利用可能スロットリスト（resource_id, day, slot_index, start_min, duration_min）
  - config: Dict           設定（time_limit_sec等）
  - issue_statuses: Dict

目的関数: lexicographic
  第1優先: 対応件数最大化（未割当件数最小化）
  第2優先: 希望違反総数最小化（希望日/希望資源/希望曜日・時間帯）
"""

from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional, Set, Tuple

logger = logging.getLogger(__name__)

_SOLVER_VERSION = "medical_appointment_scheduler_v1.0"
_DEFAULT_TIME_LIMIT = 30


class MedicalAppointmentSchedulerSolver:
    def __init__(self, solver_input: dict) -> None:
        self.dsl = solver_input

    def solve(self) -> dict:
        try:
            config: Dict = self.dsl.get("config", {})
            from solvers.base.engine_select import get_solver_engine, CPSAT
            engine = get_solver_engine(config)
            if engine == CPSAT:
                return self._solve_with_cpsat()
            return self._build_and_solve()
        except Exception as e:
            logger.error(f"[MedicalAppointmentScheduler] mdl.solve() 例外: {e}", exc_info=True)
            from solvers.base.solver_error_result import build_solver_crash_issue, solver_crash_extra_fields
            result = {
                "status": "ok",
                "feasible": False,
                "metadata": {"problem_class": "MedicalAppointmentScheduler"},
                "solutions": [],
                "issues": [build_solver_crash_issue(e)],
                "_solver_version": _SOLVER_VERSION,
            }
            result.update(solver_crash_extra_fields(e))
            return result

    def _build_and_solve(self) -> dict:
        requests: List[Dict] = self.dsl.get("requests", [])
        resources: List[Dict] = self.dsl.get("resources", [])
        slots: List[Dict] = self.dsl.get("slots", [])
        config: Dict = self.dsl.get("config", {})
        issue_statuses: Dict = self.dsl.get("issue_statuses", {})
        time_limit = int(config.get("time_limit_sec", _DEFAULT_TIME_LIMIT))

        # 事前バリデーション
        if not requests:
            return self._make_result([], [], {}, feasible=True,
                                     note="受診依頼が0件のため即時完了")
        if not resources:
            return self._make_result([], [], {}, feasible=False,
                                     note="医療資源が0件のため割当不可")
        if not slots:
            return self._make_result([], [], {}, feasible=False,
                                     note="利用可能スロットが0件のため割当不可")

        resource_map, slot_map, request_feasible_slots = self._compute_request_feasible_slots(
            requests, resources, slots
        )

        try:
            from docplex.cp.model import CpoModel
        except ImportError:
            logger.error("[MedicalAppointmentScheduler] docplex未インストール")
            from solvers.base.solver_error_result import build_solver_crash_issue
            return {
                "status": "ok", "feasible": False,
                "metadata": {"problem_class": "MedicalAppointmentScheduler"},
                "solutions": [], "issues": [build_solver_crash_issue(ImportError("docplex not installed"))],
                "_solver_version": _SOLVER_VERSION,
            }

        mdl = CpoModel(name="MedicalAppointmentScheduler")

        # ──────────────────────────────────────────────────────────
        # 変数構築
        # ──────────────────────────────────────────────────────────
        #
        # 各受診依頼 req に対して、必要資源タイプリスト（resource_type_needs）を取得し、
        # 各タイプ・各スロット候補について optional interval_var を生成する。
        #
        # assignment_itvs[(req_id, resource_type, resource_id, day, slot_index)] = interval_var
        #
        # 同時並行確保の制約: 同一 req の全資源タイプは同じ (day, slot_index) に割り当てる。
        # これは「どの (day, slot_index) を選ぶか」を req 単位で共有する方針で実現する。
        #
        # 実装方針:
        #   - req ごとに「割当スロット選択変数」として integer_var を使わず、
        #     各(req, resource_type)ペアに対して候補スロットごとの presence_of を使い、
        #     同一req内の全タイプが同じ(day,slot_index)を選ぶことを「存在の連動」で表現する。
        #   - 各 req について候補スロット一覧（全タイプが同時に確保できる(day,slot_index)の集合）
        #     を事前計算し、その候補ごとに「req全体の割当変数」(binary_var)を1つ定義する。
        #     全タイプの interval_var の presence_of はこの binary_var に連動させる。
        #   - 一部タイプのみ割当、は禁止（全タイプそろわなければ未割当）。
        #
        # (候補列挙は_compute_request_feasible_slots()に集約。以下は変数生成のみ)

        # ──────────────────────────────────────────────────────────
        # interval_var と binary_var の生成
        # ──────────────────────────────────────────────────────────
        #
        # req_slot_vars[req_id][k] = (day, slot_index, type_assignment, binary_var)
        # resource_itvs[(resource_id, day, slot_index)] = List[interval_var]  (no_overlap用)

        req_slot_vars: Dict[str, List[Tuple[str, int, Dict[str, str], Any]]] = {}
        # resource_slot_itvs[(rid, day)] = List[interval_var]
        resource_day_itvs: Dict[Tuple[str, str], List[Any]] = {}

        for req in requests:
            req_id = str(req["id"])
            duration_slots = int(req.get("duration_slots", 1))
            feasible = request_feasible_slots.get(req_id, [])
            req_slot_vars[req_id] = []

            for k, (day, sidx, type_assign) in enumerate(feasible):
                # 各候補 (day, slot_index) に対してbinary_var（0/1整数変数）を作る
                # presence_of() と同等の役割
                bv = mdl.binary_var(name=f"req_{req_id}_slot_{k}")

                # 各割り当て資源について interval_var を作る（同時並行確保の表現）
                for type_key, rid in type_assign.items():
                    # slot の開始・終了(分単位)
                    s0 = slot_map.get((rid, day, sidx), {})
                    start_min = int(s0.get("start_min", 0))
                    end_min = start_min + duration_slots * int(s0.get("slot_duration_min", 30))
                    itv = mdl.interval_var(
                        name=f"req_{req_id}_k{k}_{type_key}_{rid}",
                        optional=True,
                        start=start_min,
                        end=end_min,
                        size=end_min - start_min,
                    )
                    # presence_of(itv) == bv で連動させる
                    mdl.add(mdl.presence_of(itv) == bv)
                    # no_overlap 用
                    key = (rid, day)
                    resource_day_itvs.setdefault(key, []).append(itv)

                req_slot_vars[req_id].append((day, sidx, type_assign, bv))

        # ──────────────────────────────────────────────────────────
        # ハード制約
        # ──────────────────────────────────────────────────────────

        # 1. 各 req は最大1候補スロットにしか割り当てられない
        for req in requests:
            req_id = str(req["id"])
            slot_list = req_slot_vars.get(req_id, [])
            if len(slot_list) > 1:
                bvars = [item[3] for item in slot_list]
                mdl.add(mdl.sum(bvars) <= 1)

        # 2. 各資源は同一日・時間帯で重複割当不可 (no_overlap)
        for (rid, day), itvs in resource_day_itvs.items():
            if len(itvs) > 1:
                mdl.add(mdl.no_overlap(itvs))

        # ──────────────────────────────────────────────────────────
        # 目的関数（lexicographic: 第1優先=未割当最小化, 第2優先=希望違反最小化）
        # ──────────────────────────────────────────────────────────

        # 第1優先: 割当件数最大化 ≡ 未割当件数最小化
        unassigned_terms = []
        for req in requests:
            req_id = str(req["id"])
            slot_list = req_slot_vars.get(req_id, [])
            if not slot_list:
                # 割当候補なし → 必ず未割当
                unassigned_terms.append(1)
            else:
                bvars = [item[3] for item in slot_list]
                assigned_expr = mdl.sum(bvars)
                unassigned_terms.append(1 - assigned_expr)
        total_unassigned = mdl.sum(unassigned_terms) if unassigned_terms else mdl.integer_var(0, 0)

        # 第2優先: 希望違反合計（希望日/希望資源/希望曜日・時間帯の違反）
        pref_violation_terms = []
        for req in requests:
            req_id = str(req["id"])
            slot_list = req_slot_vars.get(req_id, [])
            if not slot_list:
                continue

            pref_days: Set[str] = set(str(d) for d in req.get("preferred_days", []))
            pref_resource_ids: Set[str] = set(str(r) for r in req.get("preferred_resource_ids", []))
            # preferred_day_time: List[{weekday: str, time_slot: str}]
            pref_day_time: List[Dict] = req.get("preferred_day_time_combinations", [])

            for day, sidx, type_assign, bv in slot_list:
                # このスロットが割り当てられた場合の希望違反を計算
                # bv が0(未割当)のときは違反カウントしない
                slot0 = slot_map.get((list(type_assign.values())[0], day, sidx), {})
                weekday = str(slot0.get("weekday", ""))
                time_slot = str(slot0.get("time_slot", ""))

                # 希望日違反
                if pref_days:
                    day_violated = 1 if day not in pref_days else 0
                    pref_violation_terms.append(bv * day_violated)

                # 希望資源違反（少なくとも1つの割当資源が希望に含まれれば充足）
                if pref_resource_ids:
                    assigned_rids = set(type_assign.values())
                    resource_ok = 1 if assigned_rids & pref_resource_ids else 0
                    pref_violation_terms.append(bv * (1 - resource_ok))

                # 希望曜日・時間帯違反
                if pref_day_time:
                    dt_ok = any(
                        str(pdt.get("weekday", "")) == weekday and
                        str(pdt.get("time_slot", "")) == time_slot
                        for pdt in pref_day_time
                    )
                    pref_violation_terms.append(bv * (0 if dt_ok else 1))

        total_pref_violation = mdl.sum(pref_violation_terms) if pref_violation_terms else mdl.integer_var(0, 0)

        # lexicographic 目的関数
        mdl.add(mdl.minimize_static_lex([total_unassigned, total_pref_violation]))

        # ──────────────────────────────────────────────────────────
        # ソルブ
        # ──────────────────────────────────────────────────────────
        try:
            msol = mdl.solve(TimeLimit=time_limit, LogVerbosity="Quiet")
        except Exception as e:
            from solvers.base.ce_limit_lns import is_ce_limit_exceeded, CeLimitExceededError
            if is_ce_limit_exceeded(e):
                raise CeLimitExceededError(str(e)) from e
            logger.error(f"[MedicalAppointmentScheduler] mdl.solve() 例外: {e}", exc_info=True)
            from solvers.base.solver_error_result import build_solver_crash_issue, solver_crash_extra_fields
            result = {
                "status": "ok", "feasible": False,
                "metadata": {"problem_class": "MedicalAppointmentScheduler"},
                "solutions": [], "issues": [build_solver_crash_issue(e)],
                "_solver_version": _SOLVER_VERSION,
            }
            result.update(solver_crash_extra_fields(e))
            return result

        if msol is None or not msol:
            return self._make_result([], [], {}, feasible=False)

        # ──────────────────────────────────────────────────────────
        # 解抽出
        # ──────────────────────────────────────────────────────────
        assignments: List[Dict] = []
        for req in requests:
            req_id = str(req["id"])
            slot_list = req_slot_vars.get(req_id, [])
            assigned = False
            for day, sidx, type_assign, bv in slot_list:
                try:
                    bv_val = msol.get_value(bv)
                except Exception:
                    bv_val = 0
                if bv_val and bv_val > 0.5:
                    # このスロットに割り当て確定
                    slot0 = slot_map.get((list(type_assign.values())[0], day, sidx), {})
                    start_min = int(slot0.get("start_min", 0))
                    duration_slots = int(req.get("duration_slots", 1))
                    slot_dur = int(slot0.get("slot_duration_min", 30))
                    end_min = start_min + duration_slots * slot_dur

                    assigned_resources = [
                        {
                            "resource_id": rid,
                            "resource_type_key": type_key,
                            "resource_name": resource_map.get(rid, {}).get("name", rid),
                        }
                        for type_key, rid in type_assign.items()
                    ]

                    # 希望違反フラグ計算（解抽出後、Python側で純粋計算）
                    pref_days = set(str(d) for d in req.get("preferred_days", []))
                    pref_rids = set(str(r) for r in req.get("preferred_resource_ids", []))
                    pref_dt = req.get("preferred_day_time_combinations", [])
                    weekday = str(slot0.get("weekday", ""))
                    time_slot = str(slot0.get("time_slot", ""))

                    day_vio = bool(pref_days and day not in pref_days)
                    res_vio = bool(pref_rids and not ({r["resource_id"] for r in assigned_resources} & pref_rids))
                    dt_vio = False
                    if pref_dt:
                        dt_ok = any(
                            str(p.get("weekday", "")) == weekday and
                            str(p.get("time_slot", "")) == time_slot
                            for p in pref_dt
                        )
                        dt_vio = not dt_ok

                    assignments.append({
                        "request_id": req_id,
                        "patient_id": str(req.get("patient_id", req_id)),
                        "patient_name": str(req.get("patient_name", f"患者{req_id}")),
                        "day": day,
                        "slot_index": sidx,
                        "start_min": start_min,
                        "end_min": end_min,
                        "weekday": weekday,
                        "time_slot": time_slot,
                        "assigned_resources": assigned_resources,
                        "day_preference_violated": day_vio,
                        "resource_preference_violated": res_vio,
                        "day_time_preference_violated": dt_vio,
                        "pref_violation_count": int(day_vio) + int(res_vio) + int(dt_vio),
                    })
                    assigned = True
                    break

        # KPI計算
        total_req = len(requests)
        assigned_count = len(assignments)
        unassigned_count = total_req - assigned_count
        pref_total_violations = sum(a["pref_violation_count"] for a in assignments)

        kpi = {
            "total_requests": total_req,
            "assigned_count": assigned_count,
            "unassigned_count": unassigned_count,
            "coverage_rate": round(assigned_count / total_req, 4) if total_req > 0 else 0.0,
            "pref_total_violations": pref_total_violations,
            "pref_violation_rate": round(pref_total_violations / (assigned_count * 3), 4)
                                   if assigned_count > 0 else 0.0,
        }

        # 全件未割当チェック
        from solvers.base.issue_rules import (
            build_full_unassignment_issue,
            build_medical_appointment_scheduler_contexts,
            run_issue_rules,
        )
        issues: List[Dict] = []
        anomaly = build_full_unassignment_issue(
            assigned_count=assigned_count,
            total_count=total_req,
            entity_label="受診依頼",
            extra_hint="get_value(binary_var)での解抽出処理",
        )
        if anomaly:
            issues.append(anomaly)

        # 解チェッカー（2026-09-01追加、バッチ4）: 資源重複割当・資源タイプ不整合・忌避日違反の独立検証
        checker_ctxs = build_medical_appointment_scheduler_contexts(assignments, requests, resources)
        issues.extend(run_issue_rules(
            domain="MedicalAppointmentScheduler", contexts=checker_ctxs, issue_statuses=issue_statuses,
        ))

        # 未割当受診依頼のISSUE生成
        assigned_ids = {a["request_id"] for a in assignments}
        for req in requests:
            req_id = str(req["id"])
            if req_id not in assigned_ids:
                feasible_count = len(request_feasible_slots.get(req_id, []))
                severity = "WARNING" if feasible_count > 0 else "CRITICAL"
                reason = "最適化の優先順位付けにより今回の割当対象外" if feasible_count > 0 \
                         else "対応可能な資源・時間枠の組み合わせがありません"
                issues.append({
                    "id": f"unassigned_{req_id}",
                    "severity": severity,
                    "title": f"未割当: {req.get('patient_name', req_id)}",
                    "message": f"受診依頼 {req_id}（{req.get('patient_name', '')}）は{reason}。",
                    "relatedContainerIds": [],
                })

        from solvers.base.solution_extraction import extract_optimality_metadata
        optimality = extract_optimality_metadata(msol)

        return self._make_result(assignments, issues, kpi, feasible=True, optimality=optimality)

    def _compute_request_feasible_slots(
        self,
        requests: List[Dict],
        resources: List[Dict],
        slots: List[Dict],
    ) -> Tuple[Dict[str, Dict], Dict[Tuple[str, str, int], Dict], Dict[str, List[Tuple[str, int, Dict[str, str]]]]]:
        """
        エンジン非依存の候補列挙処理。_build_and_solve()(CPO版)/_solve_with_cpsat()共通で使う。

        Returns:
            (resource_map, slot_map, request_feasible_slots)
            request_feasible_slots[req_id] = List[(day, slot_index, {type_key: resource_id})]
        """
        resource_map: Dict[str, Dict] = {str(r["id"]): r for r in resources}
        type_to_resource_ids: Dict[str, List[str]] = {}
        for r in resources:
            rt = str(r.get("resource_type", ""))
            type_to_resource_ids.setdefault(rt, []).append(str(r["id"]))

        slot_map: Dict[Tuple[str, str, int], Dict] = {}
        resource_slots: Dict[str, List[Dict]] = {}
        for s in slots:
            rid = str(s["resource_id"])
            day = str(s["day"])
            sidx = int(s["slot_index"])
            slot_map[(rid, day, sidx)] = s
            resource_slots.setdefault(rid, []).append(s)

        request_feasible_slots: Dict[str, List[Tuple[str, int, Dict[str, str]]]] = {}

        for req in requests:
            req_id = str(req["id"])
            type_needs: List[str] = req.get("resource_type_needs", [])
            duration_slots: int = int(req.get("duration_slots", 1))
            avoid_days: Set[str] = set(str(d) for d in req.get("avoid_days", []))

            if not type_needs:
                request_feasible_slots[req_id] = []
                continue

            type_slot_resources: Dict[Tuple[str, str, int], List[str]] = {}
            for rt in type_needs:
                for rid in type_to_resource_ids.get(rt, []):
                    for s in resource_slots.get(rid, []):
                        day = str(s["day"])
                        sidx = int(s["slot_index"])
                        can_fit = all(
                            (rid, day, sidx + k) in slot_map
                            for k in range(duration_slots)
                        )
                        if not can_fit:
                            continue
                        type_slot_resources.setdefault((rt, day, sidx), []).append(rid)

            feasible: List[Tuple[str, int, Dict[str, str]]] = []
            from collections import Counter
            type_count = Counter(type_needs)

            unique_types = list(type_count.keys())
            all_ds: Optional[Set[Tuple[str, int]]] = None
            for rt in unique_types:
                ds_set = set(
                    (day, sidx)
                    for (t2, day, sidx) in type_slot_resources
                    if t2 == rt
                )
                if all_ds is None:
                    all_ds = ds_set
                else:
                    all_ds &= ds_set
            if all_ds is None:
                all_ds = set()

            for (day, sidx) in sorted(all_ds):
                if day in avoid_days:
                    continue
                type_assignment: Dict[str, str] = {}
                ok = True
                used_rids: Set[str] = set()
                for rt, cnt in type_count.items():
                    avail = [
                        rid for rid in type_slot_resources.get((rt, day, sidx), [])
                        if rid not in used_rids
                    ]
                    if len(avail) < cnt:
                        ok = False
                        break
                    for i in range(cnt):
                        key = f"{rt}_{i}" if cnt > 1 else rt
                        type_assignment[key] = avail[i]
                        used_rids.add(avail[i])
                if ok:
                    feasible.append((day, sidx, type_assignment))

            request_feasible_slots[req_id] = feasible

        return resource_map, slot_map, request_feasible_slots

    def _solve_with_cpsat(self) -> dict:
        """CP-SAT(CPMpy)版。_build_and_solve()と同一の制約セット・目的関数を実装する。

        interval_varはすべて固定時刻(start/end定数)のoptional intervalなので、
        MeetingRoomSolverと同じ「固定時刻optional interval + presence + NoOverlapOptional」
        パターンで実装できる。目的関数はlexicographic(未割当最小化→希望違反最小化)。
        """
        import cpmpy as cp
        from solvers.base.cpmpy_lex_minimize import solve_lexicographic
        from solvers.base.engine_select import cpmpy_optimality_metadata

        requests: List[Dict] = self.dsl.get("requests", [])
        resources: List[Dict] = self.dsl.get("resources", [])
        slots: List[Dict] = self.dsl.get("slots", [])
        config: Dict = self.dsl.get("config", {})
        issue_statuses: Dict = self.dsl.get("issue_statuses", {})
        time_limit = int(config.get("time_limit_sec", _DEFAULT_TIME_LIMIT))

        if not requests:
            return self._make_result([], [], {}, feasible=True,
                                     note="受診依頼が0件のため即時完了")
        if not resources:
            return self._make_result([], [], {}, feasible=False,
                                     note="医療資源が0件のため割当不可")
        if not slots:
            return self._make_result([], [], {}, feasible=False,
                                     note="利用可能スロットが0件のため割当不可")

        resource_map, slot_map, request_feasible_slots = self._compute_request_feasible_slots(
            requests, resources, slots
        )

        m = cp.Model()

        # req_slot_vars[req_id] = List[(day, sidx, type_assign, bv)]
        req_slot_vars: Dict[str, List[Tuple[str, int, Dict[str, str], Any]]] = {}
        # resource_day_entries[(rid, day)] = List[(start, dur, end, bv)]
        resource_day_entries: Dict[Tuple[str, str], List[Tuple[int, int, int, Any]]] = {}

        for req in requests:
            req_id = str(req["id"])
            duration_slots = int(req.get("duration_slots", 1))
            feasible = request_feasible_slots.get(req_id, [])
            req_slot_vars[req_id] = []

            for k, (day, sidx, type_assign) in enumerate(feasible):
                bv = cp.boolvar(name=f"req_{req_id}_slot_{k}")

                for type_key, rid in type_assign.items():
                    s0 = slot_map.get((rid, day, sidx), {})
                    start_min = int(s0.get("start_min", 0))
                    end_min = start_min + duration_slots * int(s0.get("slot_duration_min", 30))
                    dur = end_min - start_min

                    key = (rid, day)
                    resource_day_entries.setdefault(key, []).append((start_min, dur, end_min, bv))

                req_slot_vars[req_id].append((day, sidx, type_assign, bv))

        # ハード制約1: 各reqは最大1候補スロット
        for req in requests:
            req_id = str(req["id"])
            slot_list = req_slot_vars.get(req_id, [])
            if len(slot_list) > 1:
                bvars = [item[3] for item in slot_list]
                m += (cp.sum(bvars) <= 1)

        # ハード制約2: 各資源は同一日で重複割当不可(固定時刻optional interval)
        for (rid, day), entries in resource_day_entries.items():
            if len(entries) > 1:
                starts = [e[0] for e in entries]
                durs   = [e[1] for e in entries]
                ends   = [e[2] for e in entries]
                presents = [e[3] for e in entries]
                m += cp.NoOverlapOptional(starts, durs, ends, presents)

        # 目的関数(lexicographic)
        unassigned_terms = []
        for req in requests:
            req_id = str(req["id"])
            slot_list = req_slot_vars.get(req_id, [])
            if not slot_list:
                unassigned_terms.append(1)
            else:
                bvars = [item[3] for item in slot_list]
                assigned_expr = cp.sum(bvars)
                unassigned_terms.append(1 - assigned_expr)
        total_unassigned = cp.sum(unassigned_terms) if unassigned_terms else cp.intvar(0, 0)

        pref_violation_terms = []
        for req in requests:
            req_id = str(req["id"])
            slot_list = req_slot_vars.get(req_id, [])
            if not slot_list:
                continue

            pref_days: Set[str] = set(str(d) for d in req.get("preferred_days", []))
            pref_resource_ids: Set[str] = set(str(r) for r in req.get("preferred_resource_ids", []))
            pref_day_time: List[Dict] = req.get("preferred_day_time_combinations", [])

            for day, sidx, type_assign, bv in slot_list:
                slot0 = slot_map.get((list(type_assign.values())[0], day, sidx), {})
                weekday = str(slot0.get("weekday", ""))
                time_slot = str(slot0.get("time_slot", ""))

                if pref_days:
                    day_violated = 1 if day not in pref_days else 0
                    pref_violation_terms.append(bv * day_violated)

                if pref_resource_ids:
                    assigned_rids = set(type_assign.values())
                    resource_ok = 1 if assigned_rids & pref_resource_ids else 0
                    pref_violation_terms.append(bv * (1 - resource_ok))

                if pref_day_time:
                    dt_ok = any(
                        str(pdt.get("weekday", "")) == weekday and
                        str(pdt.get("time_slot", "")) == time_slot
                        for pdt in pref_day_time
                    )
                    pref_violation_terms.append(bv * (0 if dt_ok else 1))

        total_pref_violation = cp.sum(pref_violation_terms) if pref_violation_terms else cp.intvar(0, 0)

        solved, achieved = solve_lexicographic(
            m, [total_unassigned, total_pref_violation],
            solver="ortools", time_limit=time_limit,
        )

        if not solved:
            return self._make_result([], [], {}, feasible=False)

        # 解抽出
        assignments: List[Dict] = []
        for req in requests:
            req_id = str(req["id"])
            slot_list = req_slot_vars.get(req_id, [])
            for day, sidx, type_assign, bv in slot_list:
                bv_val = bv.value()
                if bv_val:
                    slot0 = slot_map.get((list(type_assign.values())[0], day, sidx), {})
                    start_min = int(slot0.get("start_min", 0))
                    duration_slots = int(req.get("duration_slots", 1))
                    slot_dur = int(slot0.get("slot_duration_min", 30))
                    end_min = start_min + duration_slots * slot_dur

                    assigned_resources = [
                        {
                            "resource_id": rid,
                            "resource_type_key": type_key,
                            "resource_name": resource_map.get(rid, {}).get("name", rid),
                        }
                        for type_key, rid in type_assign.items()
                    ]

                    pref_days = set(str(d) for d in req.get("preferred_days", []))
                    pref_rids = set(str(r) for r in req.get("preferred_resource_ids", []))
                    pref_dt = req.get("preferred_day_time_combinations", [])
                    weekday = str(slot0.get("weekday", ""))
                    time_slot = str(slot0.get("time_slot", ""))

                    day_vio = bool(pref_days and day not in pref_days)
                    res_vio = bool(pref_rids and not ({r["resource_id"] for r in assigned_resources} & pref_rids))
                    dt_vio = False
                    if pref_dt:
                        dt_ok = any(
                            str(p.get("weekday", "")) == weekday and
                            str(p.get("time_slot", "")) == time_slot
                            for p in pref_dt
                        )
                        dt_vio = not dt_ok

                    assignments.append({
                        "request_id": req_id,
                        "patient_id": str(req.get("patient_id", req_id)),
                        "patient_name": str(req.get("patient_name", f"患者{req_id}")),
                        "day": day,
                        "slot_index": sidx,
                        "start_min": start_min,
                        "end_min": end_min,
                        "weekday": weekday,
                        "time_slot": time_slot,
                        "assigned_resources": assigned_resources,
                        "day_preference_violated": day_vio,
                        "resource_preference_violated": res_vio,
                        "day_time_preference_violated": dt_vio,
                        "pref_violation_count": int(day_vio) + int(res_vio) + int(dt_vio),
                    })
                    break

        total_req = len(requests)
        assigned_count = len(assignments)
        unassigned_count = total_req - assigned_count
        pref_total_violations = sum(a["pref_violation_count"] for a in assignments)

        kpi = {
            "total_requests": total_req,
            "assigned_count": assigned_count,
            "unassigned_count": unassigned_count,
            "coverage_rate": round(assigned_count / total_req, 4) if total_req > 0 else 0.0,
            "pref_total_violations": pref_total_violations,
            "pref_violation_rate": round(pref_total_violations / (assigned_count * 3), 4)
                                   if assigned_count > 0 else 0.0,
        }

        from solvers.base.issue_rules import (
            build_full_unassignment_issue,
            build_medical_appointment_scheduler_contexts,
            run_issue_rules,
        )
        issues: List[Dict] = []
        anomaly = build_full_unassignment_issue(
            assigned_count=assigned_count,
            total_count=total_req,
            entity_label="受診依頼",
            extra_hint="cp.boolvar().value()での解抽出処理",
        )
        if anomaly:
            issues.append(anomaly)

        # 解チェッカー（2026-09-01追加、バッチ4）: 資源重複割当・資源タイプ不整合・忌避日違反の独立検証
        checker_ctxs = build_medical_appointment_scheduler_contexts(assignments, requests, resources)
        issues.extend(run_issue_rules(
            domain="MedicalAppointmentScheduler", contexts=checker_ctxs, issue_statuses=issue_statuses,
        ))

        assigned_ids = {a["request_id"] for a in assignments}
        for req in requests:
            req_id = str(req["id"])
            if req_id not in assigned_ids:
                feasible_count = len(request_feasible_slots.get(req_id, []))
                severity = "WARNING" if feasible_count > 0 else "CRITICAL"
                reason = "最適化の優先順位付けにより今回の割当対象外" if feasible_count > 0 \
                         else "対応可能な資源・時間枠の組み合わせがありません"
                issues.append({
                    "id": f"unassigned_{req_id}",
                    "severity": severity,
                    "title": f"未割当: {req.get('patient_name', req_id)}",
                    "message": f"受診依頼 {req_id}（{req.get('patient_name', '')}）は{reason}。",
                    "relatedContainerIds": [],
                })

        meta = cpmpy_optimality_metadata(m)
        optimality = {
            "solve_status": meta.get("solve_status"),
            "is_optimal": meta.get("is_optimal", False),
            "solve_time_sec": meta.get("solve_time_sec"),
        }

        return self._make_result(assignments, issues, kpi, feasible=True, optimality=optimality)

    def _make_result(
        self,
        assignments: List[Dict],
        issues: List[Dict],
        kpi: Dict,
        feasible: bool,
        note: str = "",
        optimality: Optional[Dict] = None,
    ) -> dict:
        if not feasible and not issues:
            issues = [{
                "id": "solve_failed",
                "severity": "CRITICAL",
                "title": "割当不可",
                "message": note or "有効な割当が見つかりませんでした。資源・スロット設定を見直してください。",
                "relatedContainerIds": [],
            }]
        return {
            "status": "ok",
            "feasible": feasible,
            "metadata": {"problem_class": "MedicalAppointmentScheduler"},
            "solutions": [{
                "feasible": feasible,
                "assignments": assignments,
                "kpi": kpi,
                "optimality": optimality or {},
            }],
            "issues": issues,
            "_solver_version": _SOLVER_VERSION,
        }