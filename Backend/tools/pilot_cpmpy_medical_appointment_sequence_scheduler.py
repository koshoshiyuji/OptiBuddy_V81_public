"""
Backend/tools/pilot_cpmpy_medical_appointment_sequence_scheduler.py

2026-08-13追加。MedicalAppointmentSequenceSchedulerSolver._build_and_solve() の
CPMpy/CP-SAT代替を検証するパイロット。

【重要な発見】
このドメインは docstring に "transition_matrix" という語が登場するが、実際の
CPOモデルには sequence_var/transition_matrix/alternative() は一切使われていない
（`from docplex.cp.modeler import build_cpo_transition_matrix` は import される
だけで未使用のデッドコード）。実際に使われているのは:
  - 可変開始・固定duration の optional interval_var（既に実証済みパターン）
  - no_overlap（固定duration optional intervalの重複禁止、既に実証済み）
  - mdl.max()/mdl.min()（既に実証済み）
  - mdl.logical_or/logical_and（既に実証済み）
  - mdl.minimize_static_lex（solve_lexicographic()で対応済み）
つまり真に「未証明のCPO構築子」は無く、既存パターンの組み合わせで表現できる。

【CPMpy翻訳での簡略化】
CPOモデルは「1受診について複数の資源候補ごとに別々のinterval_varを作り、
presentな候補同士をvisit_start共有変数で同期させる」という構成だが、
制約1(presence_sum==sp*count)から導かれる事実として、sp==1のときは
その系列の全受診が必ず(いずれかの資源で)実施される。つまり「受診の開始/終了
時刻」は資源選択とは独立に1つの共有変数として扱ってよく、CPMpy側では
候補ごとの個別start変数を作らず「visit_start/visit_end(受診レベルで共有) +
cand_present(どの資源を使うかを表す真偽変数のみ)」というより直接的な形で
表現できる（CPOのように資源候補ごとにinterval_varを複製してsync制約を
張る必要がない）。
"""

from __future__ import annotations

import itertools
from typing import Any, Dict, List, Tuple


def build_cpmpy_model_and_solve(
    sequences: List[Dict],
    resources: List[Dict],
    horizon: int,
    time_limit: float = 15.0,
) -> Dict:
    """
    sequences[i]: {"id", "visits": [{"id","order","duration","required_type","required_count"}],
                   "interval_rules": [{"from_order","to_order","min_gap","max_gap"}],
                   "same_resource_rules": [{"order_a","order_b"}],
                   "blackout_slots": [{"start_min","end_min"}]}
    resources[k]: {"id", "type", "available_slots": [{"start_min","end_min"}]}

    (パイロット用に簡略化: 1受診=1資源タイプ・1個必要、のケースのみ扱う)
    """
    import cpmpy as cp
    from solvers.base.cpmpy_lex_minimize import solve_lexicographic

    m = cp.Model()

    resource_by_type: Dict[str, List[Dict]] = {}
    for r in resources:
        resource_by_type.setdefault(r["type"], []).append(r)

    sp: Dict[str, Any] = {}
    visit_start: Dict[str, Any] = {}
    visit_end: Dict[str, Any] = {}
    visit_dur: Dict[str, int] = {}
    cand_present: Dict[Tuple[str, str], Any] = {}  # (visit_id, resource_id) -> boolvar

    for seq in sequences:
        seq_id = seq["id"]
        sp[seq_id] = cp.boolvar(name=f"sp_{seq_id}")

        for visit in seq["visits"]:
            v_id = visit["id"]
            dur = visit["duration"]
            visit_dur[v_id] = dur
            vs = cp.intvar(0, max(0, horizon - dur), name=f"vs_{v_id}")
            visit_start[v_id] = vs
            visit_end[v_id] = vs + dur
            # パイロット検証用: brute-forceとの比較を離散的な組合せ探索に単純化する
            # ため、開始時刻はシナリオ側で指定した固定値に固定する(本番実装では
            # この制約は入れず、visit_startは自由変数のまま扱う)。
            if "fixed_start" in visit:
                m += (vs == visit["fixed_start"])

            rtype = visit["required_type"]
            candidates = resource_by_type.get(rtype, [])
            presents = []
            for res in candidates:
                cp_ = cp.boolvar(name=f"cand_{v_id}_{res['id']}")
                cand_present[(v_id, res["id"])] = cp_
                presents.append(cp_)

            if presents:
                m += (cp.sum(presents) == sp[seq_id])  # required_count=1想定
            else:
                m += (sp[seq_id] == 0)

            # 資源稼働スロット制約
            for res in candidates:
                key = (v_id, res["id"])
                slots = res.get("available_slots", [])
                if not slots:
                    m += (cand_present[key] == 0)
                    continue
                m += cand_present[key].implies(
                    cp.any([(vs >= s["start_min"]) & (vs + dur <= s["end_min"]) for s in slots])
                )

    # 資源ごとのno_overlap
    for res in resources:
        rid = res["id"]
        starts, durs, ends, presents = [], [], [], []
        for seq in sequences:
            for visit in seq["visits"]:
                v_id = visit["id"]
                key = (v_id, rid)
                if key not in cand_present:
                    continue
                starts.append(visit_start[v_id])
                durs.append(visit_dur[v_id])
                ends.append(visit_end[v_id])
                presents.append(cand_present[key])
        if len(starts) > 1:
            m += cp.NoOverlapOptional(starts, durs, ends, presents)

    # 順序制約 + 間隔ルール + 継続担当ルール + ブラックアウト
    for seq in sequences:
        seq_id = seq["id"]
        visits_sorted = sorted(seq["visits"], key=lambda v: v["order"])
        visit_by_order = {v["order"]: v for v in seq["visits"]}

        for i in range(len(visits_sorted) - 1):
            vi, vj = visits_sorted[i], visits_sorted[i + 1]
            m += sp[seq_id].implies(visit_end[vi["id"]] <= visit_start[vj["id"]])

        for rule in seq.get("interval_rules", []):
            v_from = visit_by_order.get(rule["from_order"])
            v_to = visit_by_order.get(rule["to_order"])
            if v_from is None or v_to is None:
                continue
            gap = visit_start[v_to["id"]] - visit_end[v_from["id"]]
            if rule.get("min_gap") is not None:
                m += sp[seq_id].implies(gap >= rule["min_gap"])
            if rule.get("max_gap") is not None:
                m += sp[seq_id].implies(gap <= rule["max_gap"])

        for rule in seq.get("same_resource_rules", []):
            v_a = visit_by_order.get(rule["order_a"])
            v_b = visit_by_order.get(rule["order_b"])
            if v_a is None or v_b is None:
                continue
            same_exprs = []
            for res in resources:
                key_a, key_b = (v_a["id"], res["id"]), (v_b["id"], res["id"])
                if key_a in cand_present and key_b in cand_present:
                    same_exprs.append(cand_present[key_a] & cand_present[key_b])
            if same_exprs:
                m += sp[seq_id].implies(cp.any(same_exprs))

        for blackout in seq.get("blackout_slots", []):
            bs, be = blackout["start_min"], blackout["end_min"]
            for visit in seq["visits"]:
                v_id = visit["id"]
                m += sp[seq_id].implies(
                    (visit_end[v_id] <= bs) | (visit_start[v_id] >= be)
                )

    # 目的関数(lexicographic): 未対応系列数の最小化 → 最大資源負荷の最小化
    total_unscheduled = len(sequences) - cp.sum(list(sp.values()))

    resource_load_exprs = []
    for res in resources:
        rid = res["id"]
        terms = []
        for seq in sequences:
            for visit in seq["visits"]:
                v_id = visit["id"]
                key = (v_id, rid)
                if key in cand_present:
                    terms.append(cand_present[key] * visit_dur[v_id])
        if terms:
            resource_load_exprs.append(cp.sum(terms))
    max_load = cp.max(resource_load_exprs) if resource_load_exprs else cp.intvar(0, 0)

    solved, achieved = solve_lexicographic(
        m, [total_unscheduled, max_load], solver="ortools", time_limit=time_limit
    )
    if not solved:
        raise RuntimeError("CP-SAT infeasible")

    scheduled_ids = [seq["id"] for seq in sequences if sp[seq["id"]].value()]
    return {"scheduled_ids": scheduled_ids, "objective": tuple(achieved)}


def brute_force_optimal(sequences: List[Dict], resources: List[Dict], horizon: int) -> Tuple[int, int]:
    """
    離散全探索(パイロット用の簡略ケース、1受診=1資源タイプ・1個必要)。
    各系列を実施する/しないの2択、実施する場合は各受診についてどの資源を
    (固定の受診開始時刻候補から)選ぶか、を全列挙してfeasibleなものの中から
    (unscheduled_count, max_load)を辞書式最小化する。
    """
    resource_by_type: Dict[str, List[Dict]] = {}
    for r in resources:
        resource_by_type.setdefault(r["type"], []).append(r)

    seq_ids = [s["id"] for s in sequences]
    best = None

    for sp_combo in itertools.product([0, 1], repeat=len(sequences)):
        sp_map = dict(zip(seq_ids, sp_combo))

        # 実施する系列の受診について資源割当候補を列挙
        active_visits = []  # (seq, visit)
        for seq in sequences:
            if sp_map[seq["id"]] == 0:
                continue
            for visit in seq["visits"]:
                active_visits.append((seq, visit))

        if not active_visits:
            unscheduled = len(sequences) - sum(sp_combo)
            key = (unscheduled, 0)
            if best is None or key < best:
                best = key
            continue

        candidate_lists = []
        for seq, visit in active_visits:
            rtype = visit["required_type"]
            cands = resource_by_type.get(rtype, [])
            candidate_lists.append(cands if cands else [None])

        feasible_found = False
        for assign_combo in itertools.product(*candidate_lists):
            if any(c is None for c in assign_combo):
                continue  # 資源タイプが存在しない → この組合せは常に不可

            # スロット制約チェック
            ok = True
            for (seq, visit), res in zip(active_visits, assign_combo):
                v_start = visit["fixed_start"]  # パイロット簡略化: 開始時刻は固定候補1つのみ
                v_end = v_start + visit["duration"]
                slots = res.get("available_slots", [])
                if not any(s["start_min"] <= v_start and v_end <= s["end_min"] for s in slots):
                    ok = False
                    break
            if not ok:
                continue

            # 順序・間隔・継続担当・ブラックアウト・重複チェック
            for seq in sequences:
                if sp_map[seq["id"]] == 0:
                    continue
                seq_visits = [(v, res) for (s2, v), res in zip(active_visits, assign_combo) if s2["id"] == seq["id"]]
                visits_sorted = sorted(seq_visits, key=lambda x: x[0]["order"])
                for i in range(len(visits_sorted) - 1):
                    vi, _ = visits_sorted[i]
                    vj, _ = visits_sorted[i + 1]
                    if not (vi["fixed_start"] + vi["duration"] <= vj["fixed_start"]):
                        ok = False
                for rule in seq.get("interval_rules", []):
                    v_from = next((v for v, _ in seq_visits if v["order"] == rule["from_order"]), None)
                    v_to = next((v for v, _ in seq_visits if v["order"] == rule["to_order"]), None)
                    if v_from is None or v_to is None:
                        continue
                    gap = v_to["fixed_start"] - (v_from["fixed_start"] + v_from["duration"])
                    if rule.get("min_gap") is not None and gap < rule["min_gap"]:
                        ok = False
                    if rule.get("max_gap") is not None and gap > rule["max_gap"]:
                        ok = False
                for rule in seq.get("same_resource_rules", []):
                    v_a_res = next((res for v, res in seq_visits if v["order"] == rule["order_a"]), None)
                    v_b_res = next((res for v, res in seq_visits if v["order"] == rule["order_b"]), None)
                    if v_a_res is not None and v_b_res is not None and v_a_res["id"] != v_b_res["id"]:
                        ok = False
                for blackout in seq.get("blackout_slots", []):
                    for v, _ in seq_visits:
                        vs, ve = v["fixed_start"], v["fixed_start"] + v["duration"]
                        if not (ve <= blackout["start_min"] or vs >= blackout["end_min"]):
                            ok = False
                if not ok:
                    break
            if not ok:
                continue

            # 資源重複チェック(同一資源が同時に2つ以上使われない)
            usage: Dict[str, List[Tuple[int, int]]] = {}
            for (seq, visit), res in zip(active_visits, assign_combo):
                vs, ve = visit["fixed_start"], visit["fixed_start"] + visit["duration"]
                usage.setdefault(res["id"], []).append((vs, ve))
            for rid, ivs in usage.items():
                ivs_sorted = sorted(ivs)
                for i in range(len(ivs_sorted) - 1):
                    if ivs_sorted[i][1] > ivs_sorted[i + 1][0]:
                        ok = False
                        break
                if not ok:
                    break
            if not ok:
                continue

            feasible_found = True
            # 資源負荷計算
            loads: Dict[str, int] = {}
            for (seq, visit), res in zip(active_visits, assign_combo):
                loads[res["id"]] = loads.get(res["id"], 0) + visit["duration"]
            max_load = max(loads.values()) if loads else 0
            unscheduled = len(sequences) - sum(sp_combo)
            key = (unscheduled, max_load)
            if best is None or key < best:
                best = key

        if not feasible_found and any(sp_combo):
            # この sp_combo では実施可能な資源割当が存在しない -> このsp_comboは無効
            continue

    return best


def _scenario():
    horizon = 1000
    resources = [
        {"id": "D1", "type": "doctor", "available_slots": [{"start_min": 0, "end_min": 500}]},
        {"id": "D2", "type": "doctor", "available_slots": [{"start_min": 0, "end_min": 500}]},
        {"id": "L1", "type": "lab", "available_slots": [{"start_min": 0, "end_min": 500}]},
    ]
    sequences = [
        {
            "id": "S1",
            "visits": [
                {"id": "S1_v1", "order": 0, "duration": 30, "required_type": "doctor", "fixed_start": 0},
                {"id": "S1_v2", "order": 1, "duration": 20, "required_type": "lab", "fixed_start": 100},
            ],
            "interval_rules": [{"from_order": 0, "to_order": 1, "min_gap": 20, "max_gap": None}],
            "same_resource_rules": [],
            "blackout_slots": [],
        },
        {
            "id": "S2",
            "visits": [
                {"id": "S2_v1", "order": 0, "duration": 30, "required_type": "doctor", "fixed_start": 0},
                {"id": "S2_v2", "order": 1, "duration": 20, "required_type": "lab", "fixed_start": 100},
            ],
            "interval_rules": [{"from_order": 0, "to_order": 1, "min_gap": 20, "max_gap": None}],
            "same_resource_rules": [],
            "blackout_slots": [],
        },
    ]
    return sequences, resources, horizon


if __name__ == "__main__":
    import sys
    import os
    sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

    sequences, resources, horizon = _scenario()

    result = build_cpmpy_model_and_solve(sequences, resources, horizon)
    print("CPMpy(CP-SAT) result:", result)

    expected = brute_force_optimal(sequences, resources, horizon)
    print("brute_force_optimal (unscheduled, max_load):", expected)

    assert result["objective"] == expected, f"MISMATCH: cpmpy={result['objective']} vs brute_force={expected}"
    print("PILOT PASSED: CPMpy(CP-SAT) matches brute-force optimal")
