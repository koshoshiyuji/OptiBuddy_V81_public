"""
medical_appointment_sequence_scheduler_solver.py

MedicalAppointmentSequenceSchedulerSolver — MASSP (CSPLib prob091)

solver_input keys:
  - problem_class: str
  - sequences: List[Dict]   # 治療系列（患者単位）
  - resources: List[Dict]   # 医療資源（医師・検査室等）
  - config: Dict
  - issue_statuses: Dict

sequences[i]:
  id: str
  patient_name: str
  visits: List[Dict]        # 受診リスト（順序付き）
  interval_rules: List[Dict]  # {from_visit, to_visit, min_gap, max_gap, unit}
  same_resource_rules: List[Dict]  # {visit_a, visit_b} → 同一資源必須
  blackout_slots: List[Dict]  # {start_min, end_min} 患者都合の禁止スロット

visits[j]:
  id: str                   # "{sequence_id}_v{j}"
  required_resources: List[Dict]  # [{type, count}]
  duration_min: int
  order: int                # 系列内の相対順序（0始まり）

resources[k]:
  id: str
  name: str
  type: str
  available_slots: List[Dict]  # {start_min, end_min}
"""

import logging
from typing import Any, Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)

# ペナルティ・定数
SCHEDULE_HORIZON = 14 * 24 * 60  # 2週間（分）


class MedicalAppointmentSequenceSchedulerSolver:
    def __init__(self, solver_input: dict) -> None:
        self.dsl = solver_input

    def solve(self) -> dict:
        sequences = self.dsl.get("sequences", [])
        resources = self.dsl.get("resources", [])
        config = self.dsl.get("config", {})
        issue_statuses = self.dsl.get("issue_statuses", {})

        if not sequences:
            return self._make_result(
                feasible=False,
                scheduled=[],
                unscheduled_ids=[],
                resource_loads={},
                issues=[{
                    "id": "solve_failed",
                    "severity": "CRITICAL",
                    "title": "入力データなし",
                    "message": "治療系列が1件もありません。",
                    "relatedContainerIds": [],
                }],
            )

        if not resources:
            return self._make_result(
                feasible=False,
                scheduled=[],
                unscheduled_ids=[s["id"] for s in sequences],
                resource_loads={},
                issues=[{
                    "id": "solve_failed",
                    "severity": "CRITICAL",
                    "title": "医療資源なし",
                    "message": "利用可能な医療資源が登録されていません。",
                    "relatedContainerIds": [],
                }],
            )

        try:
            from solvers.base.engine_select import get_solver_engine, CPSAT
            engine = get_solver_engine(config)
            if engine == CPSAT:
                scheduled, unscheduled_ids, resource_loads = self._solve_with_cpsat(
                    sequences, resources, config
                )
            else:
                scheduled, unscheduled_ids, resource_loads = self._build_and_solve(
                    sequences, resources, config
                )
        except _CeLimitError:
            return self._make_result(
                feasible=False,
                scheduled=[],
                unscheduled_ids=[s["id"] for s in sequences],
                resource_loads={},
                issues=[{
                    "id": "solve_failed",
                    "severity": "CRITICAL",
                    "title": "CPLEXの無料版で扱える件数を超えています",
                    "message": "治療系列数または受診数を減らすか、正規ライセンスのご利用をご検討ください。",
                    "relatedContainerIds": [],
                }],
            )
        except Exception as e:
            from solvers.base.solver_error_result import build_solver_crash_issue, solver_crash_extra_fields
            logger.error(f"[MedicalAppointmentSequenceScheduler] solve例外: {e}", exc_info=True)
            result = self._make_result(
                feasible=False,
                scheduled=[],
                unscheduled_ids=[s["id"] for s in sequences],
                resource_loads={},
                issues=[build_solver_crash_issue(e)],
            )
            result.update(solver_crash_extra_fields(e))
            return result

        issues = self._detect_issues(sequences, scheduled, unscheduled_ids, issue_statuses, resources)

        return self._make_result(
            feasible=len(scheduled) > 0,
            scheduled=scheduled,
            unscheduled_ids=unscheduled_ids,
            resource_loads=resource_loads,
            issues=issues,
        )

    # ------------------------------------------------------------------
    # Core solve
    # ------------------------------------------------------------------

    def _build_and_solve(
        self,
        sequences: List[Dict],
        resources: List[Dict],
        config: Dict,
    ) -> Tuple[List[Dict], List[str], Dict[str, int]]:
        from docplex.cp.model import CpoModel
        from docplex.cp.modeler import build_cpo_transition_matrix

        time_limit = config.get("time_limit_sec", 30)
        horizon = config.get("horizon_min", SCHEDULE_HORIZON)

        mdl = CpoModel(name="massp")

        # 資源タイプ別インデックス
        resource_by_type: Dict[str, List[Dict]] = {}
        for r in resources:
            resource_by_type.setdefault(r["type"], []).append(r)

        # 各受診に対して (sequence, visit) の情報を展開
        # visit_id → interval_var(s)：1受診が複数資源を必要とするとき、
        # 複数の optional interval var を作成し、その開始・終了を揃える。
        # 全資源タイプの候補を列挙し、タイプごとに必要数だけ選択。

        # データ構造: visit_assignments[visit_id] = {
        #   "type_groups": [{
        #     "resource_type": str,
        #     "count": int,
        #     "candidates": [(resource_id, interval_var), ...]
        #   }]
        # }
        visit_assignments: Dict[str, Dict] = {}
        # visit_presence[visit_id] = presence変数（系列全体の実施フラグと連動）
        visit_presence: Dict[str, Any] = {}

        # 系列実施フラグ: sequence_present[seq_id] = binary integer var
        sequence_present: Dict[str, Any] = {}

        for seq in sequences:
            seq_id = seq["id"]
            # 系列実施フラグ（0/1）
            sp = mdl.binary_var(name=f"sp_{seq_id}")
            sequence_present[seq_id] = sp

            for visit in seq.get("visits", []):
                v_id = visit["id"]
                duration = visit.get("duration_min", 60)
                type_groups = []

                for req in visit.get("required_resources", []):
                    rtype = req["type"]
                    count = req.get("count", 1)
                    candidates_for_type = resource_by_type.get(rtype, [])

                    # 各候補資源に対して optional interval_var を作成
                    cands = []
                    for res in candidates_for_type:
                        # 資源の稼働スロット内でのみ使用可能なので
                        # intensive_value を後で制約する
                        itv = mdl.interval_var(
                            size=duration,
                            optional=True,
                            end=(0, horizon),
                            name=f"itv_{v_id}_{res['id']}",
                        )
                        cands.append((res["id"], itv))

                    type_groups.append({
                        "resource_type": rtype,
                        "count": count,
                        "candidates": cands,
                    })

                visit_assignments[v_id] = {"type_groups": type_groups}

        # ------------------------------------------------------------------
        # 制約1: 系列実施フラグ → 受診の全割当を強制（オールオアナッシング）
        # 系列が実施される(sp==1)なら各受診は必ず1つの資源タイプ組合せに割り当て
        # 系列が実施されない(sp==0)なら全受診の全候補は absent
        # ------------------------------------------------------------------
        for seq in sequences:
            seq_id = seq["id"]
            sp = sequence_present[seq_id]

            for visit in seq.get("visits", []):
                v_id = visit["id"]
                tg_data = visit_assignments[v_id]["type_groups"]

                for tg in tg_data:
                    rtype = tg["resource_type"]
                    count = tg["count"]
                    cands = tg["candidates"]

                    if not cands:
                        # このタイプの資源が存在しない → 系列は実施不可
                        mdl.add(sp == 0)
                        continue

                    # 選択数 = sp * count (系列実施時は count 個、未実施は 0 個)
                    presence_sum = mdl.sum(mdl.presence_of(c[1]) for c in cands)
                    mdl.add(presence_sum == sp * count)

                    # 同一タイプで複数必要な場合の一意性制約は no_overlap で保証

        # ------------------------------------------------------------------
        # 制約2: 受診間の時間整合性（同一 visit の複数資源は同時開始・同時終了）
        # ------------------------------------------------------------------
        for seq in sequences:
            for visit in seq.get("visits", []):
                v_id = visit["id"]
                tg_data = visit_assignments[v_id]["type_groups"]
                duration = visit.get("duration_min", 60)

                # 全 present な interval の開始を揃える。
                # 修正(2026-08-08): 以前は「リスト内で隣り合う候補同士(i, i+1)」だけを
                # start_of(ia) == start_of(ib) で結びつけるチェーン方式だったが、
                # 1受診が複数の資源タイプ(例: physician + lab)を必要とする場合、
                # 実際に選ばれる候補(例: physician候補の末尾とlab候補の先頭)が
                # リスト内で隣接しているとは限らない。非隣接の組み合わせが選ばれると、
                # 間に挟まる候補が全てabsentであるために「両方absentなので自明に真」
                # という経路でチェーンが分断され、開始時刻の一致が実質的に強制されなく
                # なる（見かけ上は矛盾しない制約なのでUNSATにはならないが、正しい
                # 「同一受診＝同時刻」制約が抜け落ち、資源間で異なる開始時刻の解が
                # 許容されてしまう／逆にソルバーが候補間の対応関係を組み立てられず
                # 実行不可と誤判定するリスクもあった）。
                # 修正後は、visitごとに共通の開始時刻変数を1つ用意し、presentな
                # 候補は全てその共通変数と一致させる（候補の並び順に依存しない）。
                all_itvs_for_visit = [c[1] for tg in tg_data for c in tg["candidates"]]

                if len(all_itvs_for_visit) > 1:
                    visit_start = mdl.integer_var(min=0, max=horizon, name=f"visit_start_{v_id}")
                    for itv in all_itvs_for_visit:
                        mdl.add(
                            mdl.logical_or([
                                mdl.presence_of(itv) == 0,
                                mdl.start_of(itv, 0) == visit_start,
                            ])
                        )

        # ------------------------------------------------------------------
        # 制約3: 資源の同時使用禁止（同じ資源は同時に1件のみ）
        # ------------------------------------------------------------------
        for res in resources:
            rid = res["id"]
            res_itvs = [
                c[1]
                for seq in sequences
                for visit in seq.get("visits", [])
                for tg in visit_assignments[visit["id"]]["type_groups"]
                for c in tg["candidates"]
                if c[0] == rid
            ]
            if len(res_itvs) > 1:
                mdl.add(mdl.no_overlap(res_itvs))

        # ------------------------------------------------------------------
        # 制約4: 資源の稼働スロット外に割り当てない
        # ------------------------------------------------------------------
        res_slots: Dict[str, List[Dict]] = {r["id"]: r.get("available_slots", []) for r in resources}
        for seq in sequences:
            for visit in seq.get("visits", []):
                v_id = visit["id"]
                for tg in visit_assignments[v_id]["type_groups"]:
                    for rid, itv in tg["candidates"]:
                        slots = res_slots.get(rid, [])
                        if not slots:
                            # スロットなし → 使用不可
                            mdl.add(mdl.presence_of(itv) == 0)
                            continue
                        # いずれかのスロット内に収まる（absentな候補は対象外にする。
                        # 修正(2026-08-08): この除外がなかったため、選ばれなかった
                        # 候補(absent、start_of既定値=0)が「どのスロットにも属さない」
                        # という理由で制約違反となり、モデル全体が常に矛盾していた）
                        mdl.add(mdl.logical_or(
                            [mdl.presence_of(itv) == 0] +
                            [
                                mdl.logical_and(
                                    mdl.start_of(itv, 0) >= s["start_min"],
                                    mdl.end_of(itv, 0) <= s["end_min"],
                                )
                                for s in slots
                            ]
                        ))

        # ------------------------------------------------------------------
        # 制約5: 同一系列内の受診順序・間隔ルール
        # ------------------------------------------------------------------
        for seq in sequences:
            seq_id = seq["id"]
            visits = sorted(seq.get("visits", []), key=lambda v: v.get("order", 0))

            # 順序制約: order が小さい受診が先に終わる
            for i in range(len(visits) - 1):
                vi = visits[i]
                vj = visits[i + 1]
                # vi の全 present interval の end <= vj の全 present interval の start
                itvs_i = [c[1] for tg in visit_assignments[vi["id"]]["type_groups"] for c in tg["candidates"]]
                itvs_j = [c[1] for tg in visit_assignments[vj["id"]]["type_groups"] for c in tg["candidates"]]
                if itvs_i and itvs_j:
                    # vi の最大 end <= vj の最小 start
                    max_end_i = mdl.max([mdl.end_of(itv, 0) for itv in itvs_i])
                    min_start_j = mdl.min([mdl.start_of(itv, horizon) for itv in itvs_j])
                    sp = sequence_present[seq_id]
                    mdl.add(mdl.logical_or([
                        sp == 0,
                        max_end_i <= min_start_j,
                    ]))

            # 間隔ルール（min_gap / max_gap）
            # build visit lookup
            visit_by_order: Dict[int, Dict] = {v.get("order", i): v for i, v in enumerate(visits)}

            for rule in seq.get("interval_rules", []):
                from_order = rule.get("from_visit")
                to_order = rule.get("to_visit")
                min_gap = rule.get("min_gap", 0)
                max_gap = rule.get("max_gap", None)
                unit = rule.get("unit", "min")

                if unit == "day":
                    min_gap = min_gap * 24 * 60
                    max_gap = max_gap * 24 * 60 if max_gap is not None else None

                v_from = visit_by_order.get(from_order)
                v_to = visit_by_order.get(to_order)
                if v_from is None or v_to is None:
                    continue

                itvs_from = [c[1] for tg in visit_assignments[v_from["id"]]["type_groups"] for c in tg["candidates"]]
                itvs_to = [c[1] for tg in visit_assignments[v_to["id"]]["type_groups"] for c in tg["candidates"]]
                if not itvs_from or not itvs_to:
                    continue

                max_end_from = mdl.max([mdl.end_of(itv, 0) for itv in itvs_from])
                min_start_to = mdl.min([mdl.start_of(itv, horizon) for itv in itvs_to])
                sp = sequence_present[seq_id]

                if min_gap > 0:
                    mdl.add(mdl.logical_or([sp == 0, min_start_to - max_end_from >= min_gap]))
                if max_gap is not None:
                    mdl.add(mdl.logical_or([sp == 0, min_start_to - max_end_from <= max_gap]))

            # 継続担当制約（同一資源を使う）
            for rule in seq.get("same_resource_rules", []):
                order_a = rule.get("visit_a")
                order_b = rule.get("visit_b")
                v_a = visit_by_order.get(order_a)
                v_b = visit_by_order.get(order_b)
                if v_a is None or v_b is None:
                    continue
                # 同じ resource_type で同じ資源を使うことを制約
                # 簡略化: 同じタイプの候補の中で、同じ資源が両方に割り当てられることを要求
                for tg_a in visit_assignments[v_a["id"]]["type_groups"]:
                    for tg_b in visit_assignments[v_b["id"]]["type_groups"]:
                        if tg_a["resource_type"] != tg_b["resource_type"]:
                            continue
                        # 同じ rtype: pair (rid_a, rid_b) で rid_a==rid_b が選ばれる
                        sp = sequence_present[seq_id]
                        same_rid_exprs = []
                        for (rid_a, itv_a) in tg_a["candidates"]:
                            for (rid_b, itv_b) in tg_b["candidates"]:
                                if rid_a == rid_b:
                                    same_rid_exprs.append(
                                        mdl.logical_and(mdl.presence_of(itv_a), mdl.presence_of(itv_b))
                                    )
                        if same_rid_exprs:
                            mdl.add(mdl.logical_or([sp == 0] + same_rid_exprs))

        # ------------------------------------------------------------------
        # 制約6: 患者のブラックアウトスロット
        # ------------------------------------------------------------------
        for seq in sequences:
            seq_id = seq["id"]
            sp = sequence_present[seq_id]
            for blackout in seq.get("blackout_slots", []):
                bs = blackout["start_min"]
                be = blackout["end_min"]
                for visit in seq.get("visits", []):
                    for tg in visit_assignments[visit["id"]]["type_groups"]:
                        for _, itv in tg["candidates"]:
                            mdl.add(mdl.logical_or([
                                sp == 0,
                                mdl.presence_of(itv) == 0,
                                mdl.end_of(itv, 0) <= bs,
                                mdl.start_of(itv, horizon) >= be,
                            ]))

        # ------------------------------------------------------------------
        # 目的関数（lexicographic）
        # 第1: 対応系列数の最大化（= 系列実施フラグの合計）
        # 第2: 最大資源負荷の最小化
        # ------------------------------------------------------------------
        total_scheduled = mdl.sum(list(sequence_present.values()))

        # 各資源の負荷（割り当てられた受診時間の合計）
        resource_load_exprs: Dict[str, Any] = {}
        for res in resources:
            rid = res["id"]
            load_terms = []
            for seq in sequences:
                for visit in seq.get("visits", []):
                    for tg in visit_assignments[visit["id"]]["type_groups"]:
                        for c_rid, itv in tg["candidates"]:
                            if c_rid == rid:
                                load_terms.append(
                                    mdl.presence_of(itv) * visit.get("duration_min", 60)
                                )
            resource_load_exprs[rid] = mdl.sum(load_terms) if load_terms else 0

        if resource_load_exprs:
            max_load = mdl.max(list(resource_load_exprs.values()))
        else:
            max_load = 0

        # lexicographic: 第1優先（対応件数最大化）→ 第2優先（最大負荷最小化）
        mdl.add(mdl.minimize_static_lex([-total_scheduled, max_load]))

        # ------------------------------------------------------------------
        # ソルブ
        # ------------------------------------------------------------------
        try:
            msol = mdl.solve(TimeLimit=time_limit, LogVerbosity="Quiet")
        except Exception as e:
            if _is_ce_limit(e):
                raise _CeLimitError(str(e)) from e
            raise

        if msol is None or not msol:
            return [], [s["id"] for s in sequences], {}

        # ------------------------------------------------------------------
        # 解抽出
        # ------------------------------------------------------------------
        scheduled: List[Dict] = []
        scheduled_seq_ids = set()

        for seq in sequences:
            seq_id = seq["id"]
            sp_val = msol.get_value(sequence_present[seq_id])
            if not sp_val:
                continue

            seq_visits_scheduled = []
            for visit in seq.get("visits", []):
                v_id = visit["id"]
                assigned_resources = []

                for tg in visit_assignments[v_id]["type_groups"]:
                    for rid, itv in tg["candidates"]:
                        var_sol = msol.get_var_solution(itv)
                        if var_sol is None or not var_sol.is_present():
                            continue
                        assigned_resources.append({
                            "resource_id": rid,
                            "resource_type": tg["resource_type"],
                            "start_min": int(var_sol.get_start()),
                            "end_min": int(var_sol.get_end()),
                        })

                if assigned_resources:
                    start = min(r["start_min"] for r in assigned_resources)
                    end = max(r["end_min"] for r in assigned_resources)
                    seq_visits_scheduled.append({
                        "visit_id": v_id,
                        "visit_order": visit.get("order", 0),
                        "duration_min": visit.get("duration_min", 60),
                        "start_min": start,
                        "end_min": end,
                        "assigned_resources": assigned_resources,
                    })

            if seq_visits_scheduled:
                scheduled_seq_ids.add(seq_id)
                scheduled.append({
                    "sequence_id": seq_id,
                    "patient_name": seq.get("patient_name", seq_id),
                    "visits": seq_visits_scheduled,
                    "total_duration_min": sum(v["duration_min"] for v in seq_visits_scheduled),
                    "span_min": (
                        max(v["end_min"] for v in seq_visits_scheduled) -
                        min(v["start_min"] for v in seq_visits_scheduled)
                    ),
                })

        unscheduled_ids = [s["id"] for s in sequences if s["id"] not in scheduled_seq_ids]

        # 資源負荷計算（Python側、msol.get_value(式) は使わない）
        resource_loads: Dict[str, int] = {r["id"]: 0 for r in resources}
        for seq_data in scheduled:
            for v in seq_data["visits"]:
                for ar in v["assigned_resources"]:
                    rid = ar["resource_id"]
                    if rid in resource_loads:
                        resource_loads[rid] += v["duration_min"]

        return scheduled, unscheduled_ids, resource_loads

    def _solve_with_cpsat(
        self,
        sequences: List[Dict],
        resources: List[Dict],
        config: Dict,
    ) -> Tuple[List[Dict], List[str], Dict[str, int]]:
        """CP-SAT(CPMpy)版。_build_and_solve()と同一の制約セット・目的関数を実装する。

        本ドメインは実際にはsequence_var/transition_matrix/alternative()を
        使用していない（docstringや未使用importに反して、実装は可変開始・固定
        durationのoptional interval + no_overlap + max/min + logical_or/and の
        組み合わせのみ）。制約1(presence_sum==sp*count)から、sp==1のとき系列内の
        全受診は必ず(いずれかの資源で)実施されることが分かるため、CPMpy側では
        受診ごとに「候補資源ごとの個別interval」を複製せず、受診レベルで共有する
        単一のstart/end変数 + 「どの資源を使うか」を表す真偽変数のみで表現する
        （検証: Backend/tools/pilot_cpmpy_medical_appointment_sequence_scheduler.py）。
        """
        import cpmpy as cp
        from solvers.base.cpmpy_lex_minimize import solve_lexicographic
        from solvers.base.engine_select import cpmpy_optimality_metadata

        time_limit = config.get("time_limit_sec", 30)
        horizon = config.get("horizon_min", SCHEDULE_HORIZON)

        m = cp.Model()

        resource_by_type: Dict[str, List[Dict]] = {}
        for r in resources:
            resource_by_type.setdefault(r["type"], []).append(r)

        sp: Dict[str, Any] = {}
        visit_start: Dict[str, Any] = {}
        visit_end: Dict[str, Any] = {}
        visit_dur: Dict[str, int] = {}
        # cand_present[(visit_id, tg_idx, resource_id)] = boolvar
        cand_present: Dict[Tuple[str, int, str], Any] = {}
        # visit_type_group_candidates[visit_id] = [{"resource_type","count","candidates":[resource_id,...]}]
        visit_tg_candidates: Dict[str, List[Dict]] = {}

        for seq in sequences:
            seq_id = seq["id"]
            sp[seq_id] = cp.boolvar(name=f"sp_{seq_id}")

            for visit in seq.get("visits", []):
                v_id = visit["id"]
                dur = visit.get("duration_min", 60)
                visit_dur[v_id] = dur
                vs = cp.intvar(0, max(0, horizon - dur), name=f"vs_{v_id}")
                visit_start[v_id] = vs
                visit_end[v_id] = vs + dur

                tg_list = []
                for tg_idx, req in enumerate(visit.get("required_resources", [])):
                    rtype = req["type"]
                    count = req.get("count", 1)
                    candidates = resource_by_type.get(rtype, [])

                    presents = []
                    for res in candidates:
                        cpv = cp.boolvar(name=f"cand_{v_id}_{tg_idx}_{res['id']}")
                        cand_present[(v_id, tg_idx, res["id"])] = cpv
                        presents.append(cpv)

                    if presents:
                        m += (cp.sum(presents) == sp[seq_id] * count)
                    else:
                        m += (sp[seq_id] == 0)

                    tg_list.append({
                        "resource_type": rtype, "count": count,
                        "candidates": [res["id"] for res in candidates],
                    })
                visit_tg_candidates[v_id] = tg_list

        # 資源稼働スロット制約
        res_slots: Dict[str, List[Dict]] = {r["id"]: r.get("available_slots", []) for r in resources}
        for (v_id, tg_idx, rid), cpv in cand_present.items():
            slots = res_slots.get(rid, [])
            vs, ve = visit_start[v_id], visit_end[v_id]
            if not slots:
                m += (cpv == 0)
                continue
            m += cpv.implies(
                cp.any([(vs >= s["start_min"]) & (ve <= s["end_min"]) for s in slots])
            )

        # 資源ごとのno_overlap
        for res in resources:
            rid = res["id"]
            starts, durs, ends, presents = [], [], [], []
            for (v_id, tg_idx, r2) in cand_present:
                if r2 != rid:
                    continue
                starts.append(visit_start[v_id])
                durs.append(visit_dur[v_id])
                ends.append(visit_end[v_id])
                presents.append(cand_present[(v_id, tg_idx, r2)])
            if len(starts) > 1:
                m += cp.NoOverlapOptional(starts, durs, ends, presents)

        # 順序・間隔・継続担当・ブラックアウト
        for seq in sequences:
            seq_id = seq["id"]
            visits = sorted(seq.get("visits", []), key=lambda v: v.get("order", 0))
            visit_by_order: Dict[int, Dict] = {v.get("order", i): v for i, v in enumerate(visits)}

            for i in range(len(visits) - 1):
                vi, vj = visits[i], visits[i + 1]
                m += sp[seq_id].implies(visit_end[vi["id"]] <= visit_start[vj["id"]])

            for rule in seq.get("interval_rules", []):
                from_order = rule.get("from_visit")
                to_order = rule.get("to_visit")
                min_gap = rule.get("min_gap", 0)
                max_gap = rule.get("max_gap", None)
                unit = rule.get("unit", "min")
                if unit == "day":
                    min_gap = min_gap * 24 * 60
                    max_gap = max_gap * 24 * 60 if max_gap is not None else None

                v_from = visit_by_order.get(from_order)
                v_to = visit_by_order.get(to_order)
                if v_from is None or v_to is None:
                    continue
                gap = visit_start[v_to["id"]] - visit_end[v_from["id"]]
                if min_gap and min_gap > 0:
                    m += sp[seq_id].implies(gap >= min_gap)
                if max_gap is not None:
                    m += sp[seq_id].implies(gap <= max_gap)

            for rule in seq.get("same_resource_rules", []):
                order_a = rule.get("visit_a")
                order_b = rule.get("visit_b")
                v_a = visit_by_order.get(order_a)
                v_b = visit_by_order.get(order_b)
                if v_a is None or v_b is None:
                    continue
                same_exprs = []
                for tg_a_idx, tg_a in enumerate(visit_tg_candidates.get(v_a["id"], [])):
                    for tg_b_idx, tg_b in enumerate(visit_tg_candidates.get(v_b["id"], [])):
                        if tg_a["resource_type"] != tg_b["resource_type"]:
                            continue
                        common_rids = set(tg_a["candidates"]) & set(tg_b["candidates"])
                        for rid in common_rids:
                            key_a = (v_a["id"], tg_a_idx, rid)
                            key_b = (v_b["id"], tg_b_idx, rid)
                            if key_a in cand_present and key_b in cand_present:
                                same_exprs.append(cand_present[key_a] & cand_present[key_b])
                if same_exprs:
                    m += sp[seq_id].implies(cp.any(same_exprs))

            for blackout in seq.get("blackout_slots", []):
                bs, be = blackout["start_min"], blackout["end_min"]
                for visit in seq.get("visits", []):
                    v_id = visit["id"]
                    m += sp[seq_id].implies(
                        (visit_end[v_id] <= bs) | (visit_start[v_id] >= be)
                    )

        # ── 目的関数(lexicographic) ──
        total_unscheduled = len(sequences) - cp.sum(list(sp.values())) if sp else cp.intvar(0, 0)

        resource_load_exprs = []
        for res in resources:
            rid = res["id"]
            terms = []
            for (v_id, tg_idx, r2), cpv in cand_present.items():
                if r2 != rid:
                    continue
                terms.append(cpv * visit_dur[v_id])
            if terms:
                resource_load_exprs.append(cp.sum(terms))
        max_load = cp.max(resource_load_exprs) if resource_load_exprs else cp.intvar(0, 0)

        solved, achieved = solve_lexicographic(
            m, [total_unscheduled, max_load], solver="ortools", time_limit=time_limit
        )

        if not solved:
            return [], [s["id"] for s in sequences], {}

        # ── 解抽出 ──
        scheduled: List[Dict] = []
        scheduled_seq_ids = set()

        for seq in sequences:
            seq_id = seq["id"]
            if not sp[seq_id].value():
                continue

            seq_visits_scheduled = []
            for visit in seq.get("visits", []):
                v_id = visit["id"]
                assigned_resources = []
                for tg_idx, tg in enumerate(visit_tg_candidates.get(v_id, [])):
                    for rid in tg["candidates"]:
                        key = (v_id, tg_idx, rid)
                        if key in cand_present and cand_present[key].value():
                            assigned_resources.append({
                                "resource_id": rid,
                                "resource_type": tg["resource_type"],
                                "start_min": int(visit_start[v_id].value()),
                                "end_min": int(visit_end[v_id].value()),
                            })

                if assigned_resources:
                    start = min(r["start_min"] for r in assigned_resources)
                    end = max(r["end_min"] for r in assigned_resources)
                    seq_visits_scheduled.append({
                        "visit_id": v_id,
                        "visit_order": visit.get("order", 0),
                        "duration_min": visit.get("duration_min", 60),
                        "start_min": start,
                        "end_min": end,
                        "assigned_resources": assigned_resources,
                    })

            if seq_visits_scheduled:
                scheduled_seq_ids.add(seq_id)
                scheduled.append({
                    "sequence_id": seq_id,
                    "patient_name": seq.get("patient_name", seq_id),
                    "visits": seq_visits_scheduled,
                    "total_duration_min": sum(v["duration_min"] for v in seq_visits_scheduled),
                    "span_min": (
                        max(v["end_min"] for v in seq_visits_scheduled) -
                        min(v["start_min"] for v in seq_visits_scheduled)
                    ),
                })

        unscheduled_ids = [s["id"] for s in sequences if s["id"] not in scheduled_seq_ids]

        resource_loads: Dict[str, int] = {r["id"]: 0 for r in resources}
        for seq_data in scheduled:
            for v in seq_data["visits"]:
                for ar in v["assigned_resources"]:
                    rid = ar["resource_id"]
                    if rid in resource_loads:
                        resource_loads[rid] += v["duration_min"]

        return scheduled, unscheduled_ids, resource_loads

    # ------------------------------------------------------------------
    # Issue detection
    # ------------------------------------------------------------------

    def _detect_issues(
        self,
        sequences: List[Dict],
        scheduled: List[Dict],
        unscheduled_ids: List[str],
        issue_statuses: Dict[str, str],
        resources: List[Dict],
    ) -> List[Dict]:
        from solvers.base.issue_rules import (
            build_full_unassignment_issue,
            build_medical_appointment_sequence_scheduler_contexts,
            run_issue_rules,
        )

        issues = []

        total_count = len(sequences)
        assigned_count = len(scheduled)

        anomaly = build_full_unassignment_issue(
            assigned_count=assigned_count,
            total_count=total_count,
            entity_label="治療系列",
            extra_hint="sequence_present変数の解抽出処理",
        )
        if anomaly and issue_statuses.get(anomaly["id"]) != "ACCEPTED":
            issues.append(anomaly)

        # 解チェッカー（2026-09-01追加、バッチ4）: 資源重複割当・稼働時間外受診・間隔制約・
        # 継続担当制約・受診内資源時刻不整合(任意)の独立検証
        checker_ctxs = build_medical_appointment_sequence_scheduler_contexts(scheduled, sequences, resources)
        issues.extend(run_issue_rules(
            domain="MedicalAppointmentSequenceScheduler", contexts=checker_ctxs, issue_statuses=issue_statuses,
        ))

        for sid in unscheduled_ids:
            iid = f"unscheduled_sequence_{sid}"
            if issue_statuses.get(iid) == "ACCEPTED":
                continue
            seq = next((s for s in sequences if s["id"] == sid), {})
            issues.append({
                "id": iid,
                "severity": "WARNING",
                "title": f"治療系列を未割当: {seq.get('patient_name', sid)}",
                "message": (
                    f"患者「{seq.get('patient_name', sid)}」の治療系列（{len(seq.get('visits', []))}回の受診）を"
                    "スケジュールできませんでした。医療資源の空きまたは間隔ルールをご確認ください。"
                ),
                "relatedContainerIds": [],
            })

        if assigned_count == 0 and total_count > 0:
            iid = "solve_failed"
            if issue_statuses.get(iid) != "ACCEPTED":
                issues.insert(0, {
                    "id": "solve_failed",
                    "severity": "CRITICAL",
                    "title": "スケジュール可能な治療系列が見つかりません",
                    "message": (
                        "全ての治療系列をスケジュールできませんでした。"
                        "医療資源の稼働時間・受診間隔ルール・継続担当制約をご確認ください。"
                    ),
                    "relatedContainerIds": [],
                })

        return issues

    # ------------------------------------------------------------------
    # Result builder
    # ------------------------------------------------------------------

    def _make_result(
        self,
        feasible: bool,
        scheduled: List[Dict],
        unscheduled_ids: List[str],
        resource_loads: Dict[str, int],
        issues: List[Dict],
    ) -> Dict:
        sequences = self.dsl.get("sequences", [])
        total = len(sequences)
        sched_count = len(scheduled)
        max_load = max(resource_loads.values()) if resource_loads else 0

        return {
            "status": "ok",
            "feasible": feasible,
            "metadata": {"problem_class": "MedicalAppointmentSequenceScheduler"},
            "solutions": [{
                "feasible": feasible,
                "scheduled_sequences": scheduled,
                "unscheduled_sequence_ids": unscheduled_ids,
                "resource_loads": resource_loads,
                "kpi": {
                    "total_sequences": total,
                    "scheduled_count": sched_count,
                    "unscheduled_count": len(unscheduled_ids),
                    "coverage_rate": round(sched_count / total, 3) if total > 0 else 0.0,
                    "max_resource_load_min": max_load,
                },
            }],
            "issues": issues,
            "_solver_version": "medical_appointment_sequence_scheduler_v1.0",
        }


class _CeLimitError(Exception):
    pass


def _is_ce_limit(e: Exception) -> bool:
    msg = str(e).lower()
    return "size limit" in msg or "problem size" in msg or "community" in msg