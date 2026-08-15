"""
Backend/tools/pilot_cpmpy_patient_transport_planner.py

2026-08-13追加。PatientTransportPlannerSolver._build_and_solve() が使う
docplex.cp の alternative() + sequence_var/no_overlap + pulse(cumulative) +
minimize_static_lex という組み合わせを CPMpy/CP-SAT で代替できるかを検証する
パイロット。

【CPOモデルの構造(該当箇所)】
  - phase_itvs[phase_id]: 論理フェーズ(optional interval, 固定duration, 時間窓start範囲)
  - assign_itvs[vid][phase_id]: 車両vidがそのフェーズを担当する場合のoptionalコピー
  - alternative(phase_itvs[phase_id], [assign_itvs[vid][phase_id] for vid in eligible]):
    「phase_itvsが present なら候補の中からちょうど1台が present かつ同じstart/end」
    「phase_itvsが absent ならどの候補も absent」
  - 車両ごとの sequence_var + no_overlap（担当フェーズ同士の非重複）
  - 車両ごとの pulse合計 <= capacity（同時乗車定員）
  - minimize_static_lex([未対応ペナルティ合計, 対応済み乗車時間合計])

【CPMpy翻訳方針】
alternative()は「論理フェーズ変数」を別途持たなくても、以下で完全に等価に表現できる
（このプロジェクトの他ドメインで既に確立したパターンの組み合わせ）:
  - phase_start = cp.intvar(tw_start, tw_end)  # 全候補で共有する単一の時刻変数
  - phase_end = phase_start + duration          # duration固定なので共有可能
  - alt_present[vid] = cp.boolvar()  (各候補ごと)
  - sum(alt_present[vid] for vid in eligible) <= 1   # 高々1台が担当(0なら未対応)
  - phase_present = sum(alt_present[vid])            # 0/1、「対応済みか」の指標
  - 車両ごとのno_overlap/cumulativeは、共有のphase_start/phase_end + そのvid用の
    alt_present[vid]をpresenceとして使う（NoOverlapOptional/Cumulativeのpresence
    ゲート機能で、担当していない車両からは自動的に無関係になる）

往復セット前後関係(end_before_start相当)は、非opitonal intervalの場合と異なり
optional同士の場合は「両方presentのときのみ」の条件付き制約になる
（本プロジェクトの初期設計メモで確立済みのパターン:
 (present_a & present_b).implies(end_a+delay <= start_b)）。

このパイロットでは時間窓の幅を1分(tw_end=tw_start+1)に固定した小規模シナリオを
使い、開始時刻の選択肢を実質1通りに絞ることで、brute-force検証を
「どのフェーズをどの車両に割り当てるか(またはunservedにするか)」という
離散的な組合せ探索に単純化できるようにしている
（乗車時間はdurationが固定なので開始時刻に依らず目的関数に影響しない）。
"""

from __future__ import annotations

import itertools
from typing import Any, Dict, List, Optional, Tuple

UNSERVED_PENALTY = 10000
ROUNDTRIP_PARTIAL_EXTRA = 5000


def build_cpmpy_model_and_solve(
    phases: List[Dict],
    vehicles: List[Dict],
    roundtrip_pairs: List[Tuple[str, str, int]],
    time_limit: float = 15.0,
) -> Dict:
    """
    phases: [{"id":..., "tw_start":int, "tw_end":int, "duration":int,
              "capacity_required":int, "eligible_vehicle_ids":[...]}]
    vehicles: [{"id":..., "capacity":int}]
    roundtrip_pairs: [(out_phase_id, inb_phase_id, exam_duration_min), ...]

    Returns: {"served": {phase_id: bool}, "objective": (obj1, obj2), "assignment": {phase_id: vid|None}}
    """
    import cpmpy as cp

    m = cp.Model()

    phase_start: Dict[str, Any] = {}
    phase_end: Dict[str, Any] = {}
    phase_present: Dict[str, Any] = {}
    alt_present: Dict[Tuple[str, str], Any] = {}  # (vid, phase_id) -> boolvar

    for p in phases:
        pid = p["id"]
        s = cp.intvar(p["tw_start"], p["tw_end"], name=f"start_{pid}")
        e = s + p["duration"]
        phase_start[pid] = s
        phase_end[pid] = e

        eligible = p["eligible_vehicle_ids"]
        presents_for_phase = []
        for vid in eligible:
            av = cp.boolvar(name=f"alt_{vid}_{pid}")
            alt_present[(vid, pid)] = av
            presents_for_phase.append(av)

        if presents_for_phase:
            m += (cp.sum(presents_for_phase) <= 1)
            pp = cp.boolvar(name=f"present_{pid}")
            m += (pp == (cp.sum(presents_for_phase) >= 1))
            phase_present[pid] = pp
        else:
            # 担当可能な車両が無い → 必ず unserved
            phase_present[pid] = False  # 定数

    # 往復セット前後関係(optional interval同士): 両方presentのときのみ
    for out_pid, inb_pid, exam_dur in roundtrip_pairs:
        out_p, inb_p = phase_present[out_pid], phase_present[inb_pid]
        if out_p is False or inb_p is False:
            continue
        m += (out_p & inb_p).implies(phase_end[out_pid] + exam_dur <= phase_start[inb_pid])

    # 車両ごとの no_overlap + cumulative(定員)
    for v in vehicles:
        vid = v["id"]
        starts, durs, ends, presents, demands = [], [], [], [], []
        for p in phases:
            pid = p["id"]
            key = (vid, pid)
            if key not in alt_present:
                continue
            starts.append(phase_start[pid])
            durs.append(p["duration"])
            ends.append(phase_end[pid])
            presents.append(alt_present[key])
            demands.append(alt_present[key] * p["capacity_required"])

        if len(starts) > 1:
            m += cp.NoOverlapOptional(starts, durs, ends, presents)
        if demands:
            m += cp.Cumulative(starts, durs, ends, demands, v["capacity"])

    # 目的関数(lexicographic)
    unserved_terms = []
    ride_time_terms = []

    roundtrip_out_ids = {out_pid for out_pid, _, _ in roundtrip_pairs}
    roundtrip_inb_ids = {inb_pid for _, inb_pid, _ in roundtrip_pairs}
    roundtrip_map = {out_pid: inb_pid for out_pid, inb_pid, _ in roundtrip_pairs}

    handled = set()
    for out_pid, inb_pid, _ in roundtrip_pairs:
        out_p, inb_p = phase_present[out_pid], phase_present[inb_pid]
        both = (out_p & inb_p) if (out_p is not False and inb_p is not False) else False
        if both is False:
            unserved_terms.append(UNSERVED_PENALTY)
        else:
            unserved_terms.append(UNSERVED_PENALTY * (1 - both))
            only_out = (out_p & (~inb_p)) if (out_p is not False and inb_p is not False) else False
            only_inb = (inb_p & (~out_p)) if (out_p is not False and inb_p is not False) else False
            if only_out is not False or only_inb is not False:
                extra = 0
                if only_out is not False:
                    extra = extra + only_out
                if only_inb is not False:
                    extra = extra + only_inb
                unserved_terms.append(ROUNDTRIP_PARTIAL_EXTRA * extra)
            out_dur = next(p["duration"] for p in phases if p["id"] == out_pid)
            inb_dur = next(p["duration"] for p in phases if p["id"] == inb_pid)
            if both is not False:
                ride_time_terms.append(both * (out_dur + inb_dur))
        handled.add(out_pid)
        handled.add(inb_pid)

    for p in phases:
        pid = p["id"]
        if pid in handled:
            continue
        pp = phase_present[pid]
        if pp is False:
            unserved_terms.append(UNSERVED_PENALTY)
        else:
            unserved_terms.append(UNSERVED_PENALTY * (1 - pp))
            ride_time_terms.append(pp * p["duration"])

    obj1 = cp.sum(unserved_terms) if unserved_terms else 0
    obj2 = cp.sum(ride_time_terms) if ride_time_terms else 0

    from solvers.base.cpmpy_lex_minimize import solve_lexicographic
    solved, achieved = solve_lexicographic(m, [obj1, obj2], solver="ortools", time_limit=time_limit)

    if not solved:
        raise RuntimeError("CP-SAT: infeasible")

    served = {}
    assignment = {}
    for p in phases:
        pid = p["id"]
        pp = phase_present[pid]
        served[pid] = bool(pp.value()) if pp is not False else False
        assigned_vid = None
        for v in vehicles:
            key = (v["id"], pid)
            if key in alt_present and alt_present[key].value():
                assigned_vid = v["id"]
                break
        assignment[pid] = assigned_vid

    return {"served": served, "objective": tuple(achieved), "assignment": assignment}


def brute_force_optimal(
    phases: List[Dict],
    vehicles: List[Dict],
    roundtrip_pairs: List[Tuple[str, str, int]],
) -> Tuple[int, int]:
    """
    離散全探索。各フェーズについて「unserved」または「eligible車両のどれか」を選び、
    各車両内で時間重複が無いか(固定start/end)・容量超過が無いかをチェックする。
    往復セットの前後関係(end+exam<=start)もチェックする。
    フィージブルな割当のうち(obj1, obj2)を辞書式最小化するものを返す。
    """
    phase_ids = [p["id"] for p in phases]
    phase_by_id = {p["id"]: p for p in phases}
    roundtrip_map = {out_pid: (inb_pid, exam) for out_pid, inb_pid, exam in roundtrip_pairs}
    roundtrip_inv = {inb_pid: out_pid for out_pid, inb_pid, _ in roundtrip_pairs}

    choices_per_phase = []
    for p in phases:
        choices_per_phase.append([None] + list(p["eligible_vehicle_ids"]))

    best = None
    for combo in itertools.product(*choices_per_phase):
        assignment = dict(zip(phase_ids, combo))

        # 往復前後関係チェック
        feasible = True
        for out_pid, (inb_pid, exam) in roundtrip_map.items():
            out_assigned = assignment[out_pid] is not None
            inb_assigned = assignment[inb_pid] is not None
            if out_assigned and inb_assigned:
                out_p = phase_by_id[out_pid]
                inb_p = phase_by_id[inb_pid]
                if not (out_p["tw_start"] + out_p["duration"] + exam <= inb_p["tw_start"]):
                    feasible = False
                    break
        if not feasible:
            continue

        # 車両ごとの重複・容量チェック
        for v in vehicles:
            vid = v["id"]
            assigned_phases = [phase_by_id[pid] for pid, a in assignment.items() if a == vid]
            # 時間重複チェック(区間が重ならないか)
            intervals = sorted(
                [(p["tw_start"], p["tw_start"] + p["duration"]) for p in assigned_phases]
            )
            for i in range(len(intervals) - 1):
                if intervals[i][1] > intervals[i + 1][0]:
                    feasible = False
                    break
            if not feasible:
                break
            # 容量チェック: 同時刻の需要合計
            timepoints = set()
            for s, e in intervals:
                timepoints.add(s)
            for t in timepoints:
                demand = sum(
                    p["capacity_required"] for p in assigned_phases
                    if p["tw_start"] <= t < p["tw_start"] + p["duration"]
                )
                if demand > v["capacity"]:
                    feasible = False
                    break
            if not feasible:
                break
        if not feasible:
            continue

        # 目的関数計算
        unserved = 0
        ride_time = 0
        handled = set()
        for out_pid, (inb_pid, exam) in roundtrip_map.items():
            out_ok = assignment[out_pid] is not None
            inb_ok = assignment[inb_pid] is not None
            if out_ok and inb_ok:
                ride_time += phase_by_id[out_pid]["duration"] + phase_by_id[inb_pid]["duration"]
            else:
                unserved += UNSERVED_PENALTY
                if out_ok or inb_ok:
                    unserved += ROUNDTRIP_PARTIAL_EXTRA
            handled.add(out_pid)
            handled.add(inb_pid)

        for pid, a in assignment.items():
            if pid in handled:
                continue
            if a is not None:
                ride_time += phase_by_id[pid]["duration"]
            else:
                unserved += UNSERVED_PENALTY

        key = (unserved, ride_time)
        if best is None or key < best:
            best = key

    return best


def _scenario():
    # 2件のroundtrip依頼(往路/復路の2フェーズずつ)、2台の車両。窓幅=1分固定。
    phases = [
        {"id": "R1_out", "tw_start": 0, "tw_end": 1, "duration": 10,
         "capacity_required": 1, "eligible_vehicle_ids": ["V1", "V2"]},
        {"id": "R1_inb", "tw_start": 80, "tw_end": 81, "duration": 10,
         "capacity_required": 1, "eligible_vehicle_ids": ["V1", "V2"]},
        {"id": "R2_out", "tw_start": 5, "tw_end": 6, "duration": 8,
         "capacity_required": 1, "eligible_vehicle_ids": ["V1"]},
        {"id": "R2_inb", "tw_start": 90, "tw_end": 91, "duration": 8,
         "capacity_required": 1, "eligible_vehicle_ids": ["V1", "V2"]},
    ]
    vehicles = [{"id": "V1", "capacity": 2}, {"id": "V2", "capacity": 2}]
    roundtrip_pairs = [("R1_out", "R1_inb", 60), ("R2_out", "R2_inb", 60)]
    return phases, vehicles, roundtrip_pairs


if __name__ == "__main__":
    import sys
    import os
    sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

    phases, vehicles, roundtrip_pairs = _scenario()

    result = build_cpmpy_model_and_solve(phases, vehicles, roundtrip_pairs)
    print("CPMpy(CP-SAT) result:", result)

    expected = brute_force_optimal(phases, vehicles, roundtrip_pairs)
    print("brute_force_optimal (obj1, obj2):", expected)

    assert result["objective"] == expected, f"MISMATCH: cpmpy={result['objective']} vs brute_force={expected}"
    print("PILOT PASSED: CPMpy(CP-SAT) matches brute-force optimal (obj1, obj2)")
