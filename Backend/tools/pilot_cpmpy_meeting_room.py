"""
pilot_cpmpy_meeting_room.py — CPMpyパイロット検証 (MeetingRoom / interval_var系)

目的:
  Backend/solvers/meeting_room_solver.py の docplex.cp モデル
  （optional interval_var + no_overlap）を CPMpy(ortools/CP-SATバックエンド)
  で等価に組めるかを検証する。

重要な前提の違い（車種順序付けとの比較で分かったこと）:
  CPMpyには docplex.cp の interval_var のような「単一の第一級オブジェクト」は
  無い。cpmpy.NoOverlapOptional(start, duration, end, is_present) のように、
  start/duration/end を別々の変数配列として渡し、is_presentで presence_of()
  相当を表現する「分解型」のAPIになっている。
  ※ meeting_room_solver.py の interval_var は start/end/size が全て
     定数（m_start, m_end, m_dur固定）で、時間側は動かない。つまりこの
     ドメインは「区間が固定時刻のリソース競合（presence選択のみ）」という、
     interval_var系の中でも簡単な部類のケース。start/endが変数になる
     真のフレキシブルスケジューリング（truck_dispatcher等RCPSP系）を
     この結果だけで検証したことにはならない点に注意。

このサンドボックスにはdocplex.cp/CP Optimizerの実行エンジンが無いため、
docplex.cp側とは数値比較できない。代わりに小規模ケースを手計算・
全探索(brute force)で検証する。
"""

from __future__ import annotations

import itertools
from typing import Dict, List

import cpmpy as cp


UNASSIGNED_PENALTY = 1000
WASTE_PENALTY_WEIGHT = 1
DEPT_SAME_ROOM_BONUS = 0.1
DEPT_CONSECUTIVE_GAP_MIN = 60


def compatible_rooms_for(meetings, rooms):
    compat = {}
    for m in meetings:
        mid = str(m["id"])
        compat[mid] = []
        for r in rooms:
            rid = str(r["id"])
            if int(r.get("capacity", 0)) < int(m.get("attendees", 1)):
                continue
            if not set(m.get("required_features", [])).issubset(set(r.get("features", []))):
                continue
            if m["start_min"] < r.get("available_start_min", 0) or m["end_min"] > r.get("available_end_min", 1440):
                continue
            compat[mid].append(rid)
    return compat


SCALE = 10  # CP-SAT/CPMpyは目的関数の係数がintのみ（floatは非対応）。
            # docplex.cpのDEPT_SAME_ROOM_BONUS=0.1のようなfloat係数を扱うため、
            # 目的関数全体を整数化できる倍率でスケールしてから解く。
            # これはCPO→CP-SAT移行で必ず踏む注意点（本パイロットでの発見）。


def build_cpmpy_model(meetings, rooms, config):
    dept_same_room_bonus = config.get("dept_same_room_bonus", DEPT_SAME_ROOM_BONUS)
    waste_penalty_weight = config.get("waste_penalty_weight", WASTE_PENALTY_WEIGHT)
    dept_gap = config.get("dept_consecutive_gap_min", DEPT_CONSECUTIVE_GAP_MIN)
    bonus_scaled = round(dept_same_room_bonus * SCALE)

    compat = compatible_rooms_for(meetings, rooms)

    # is_present[(mid, rid)] = BoolVar  ... docplex の presence_of(itv) に相当
    is_present: Dict = {}
    for m in meetings:
        mid = str(m["id"])
        for rid in compat[mid]:
            is_present[(mid, rid)] = cp.boolvar(name=f"present_{mid}_{rid}")

    model = cp.Model()

    # 制約1: 各会議は高々1部屋
    for m in meetings:
        mid = str(m["id"])
        vars_for_m = [is_present[(mid, rid)] for rid in compat[mid]]
        if vars_for_m:
            model += (cp.sum(vars_for_m) <= 1)

    # 制約2: 部屋ごとの NoOverlapOptional（docplexのno_overlapに相当）
    room_to_meetings: Dict[str, List[Dict]] = {str(r["id"]): [] for r in rooms}
    for m in meetings:
        mid = str(m["id"])
        for rid in compat[mid]:
            room_to_meetings[rid].append(m)

    for rid, ms in room_to_meetings.items():
        if len(ms) > 1:
            starts = [m["start_min"] for m in ms]
            durs = [m["end_min"] - m["start_min"] for m in ms]
            ends = [m["end_min"] for m in ms]
            presents = [is_present[(str(m["id"]), rid)] for m in ms]
            model += cp.NoOverlapOptional(starts, durs, ends, presents)

    # --- 目的関数 ---
    penalty_terms, waste_terms, bonus_terms = [], [], []

    for m in meetings:
        mid = str(m["id"])
        if not compat[mid]:
            penalty_terms.append(UNASSIGNED_PENALTY)
        else:
            assigned_sum = cp.sum([is_present[(mid, rid)] for rid in compat[mid]])
            penalty_terms.append(UNASSIGNED_PENALTY * (1 - assigned_sum))

    for m in meetings:
        mid = str(m["id"])
        attend = int(m.get("attendees", 1))
        for rid in compat[mid]:
            r_cap = int(next(rm["capacity"] for rm in rooms if str(rm["id"]) == rid))
            waste = r_cap - attend
            waste_terms.append(waste_penalty_weight * waste * is_present[(mid, rid)])

    dept_meetings: Dict[str, List[Dict]] = {}
    for m in meetings:
        dept = m.get("dept")
        if dept:
            dept_meetings.setdefault(str(dept), []).append(m)

    for dept, dms in dept_meetings.items():
        for i in range(len(dms)):
            for j in range(i + 1, len(dms)):
                ma, mb = dms[i], dms[j]
                gap_ab = mb["start_min"] - ma["end_min"]
                gap_ba = ma["start_min"] - mb["end_min"]
                is_consecutive = (0 <= gap_ab <= dept_gap) or (0 <= gap_ba <= dept_gap)
                if not is_consecutive:
                    continue
                mid_a, mid_b = str(ma["id"]), str(mb["id"])
                common = set(compat[mid_a]) & set(compat[mid_b])
                for rid in common:
                    both = cp.min([is_present[(mid_a, rid)], is_present[(mid_b, rid)]])
                    bonus_terms.append(bonus_scaled * both)

    # penalty/waste項もSCALE倍して、bonus項(整数化済み)とスケールを揃える
    obj = SCALE * cp.sum(penalty_terms + waste_terms)
    if bonus_terms:
        obj = obj - cp.sum(bonus_terms)

    model.minimize(obj)
    return model, is_present, compat


def solve_with_cpmpy(meetings, rooms, config, time_limit=10):
    model, is_present, compat = build_cpmpy_model(meetings, rooms, config)
    ok = model.solve(solver="ortools", time_limit=time_limit)
    if not ok:
        return {"feasible": False, "objective": None, "assignment": None}
    assignment = {}
    for (mid, rid), v in is_present.items():
        if v.value():
            assignment[mid] = rid
    # SCALEで整数化していたのを元に戻す(docplex.cp側と同じ単位に揃える)
    return {"feasible": True, "objective": model.objective_value() / SCALE, "assignment": assignment}


# ---------------------------------------------------------------------------
# 独立検証: 全探索（docplex.cpにもCPMpyにも依存しない、ロジックの手計算コード）
# ---------------------------------------------------------------------------

def brute_force_optimal(meetings, rooms, config):
    dept_same_room_bonus = config.get("dept_same_room_bonus", DEPT_SAME_ROOM_BONUS)
    waste_penalty_weight = config.get("waste_penalty_weight", WASTE_PENALTY_WEIGHT)
    dept_gap = config.get("dept_consecutive_gap_min", DEPT_CONSECUTIVE_GAP_MIN)
    compat = compatible_rooms_for(meetings, rooms)

    def overlaps(a, b):
        return a["start_min"] < b["end_min"] and b["start_min"] < a["end_min"]

    choices = [compat[str(m["id"])] + [None] for m in meetings]  # None=unassigned
    best = None
    for combo in itertools.product(*choices):
        # room衝突チェック
        room_assign: Dict[str, List[Dict]] = {}
        valid = True
        for m, rid in zip(meetings, combo):
            if rid is None:
                continue
            for other in room_assign.get(rid, []):
                if overlaps(m, other):
                    valid = False
                    break
            if not valid:
                break
            room_assign.setdefault(rid, []).append(m)
        if not valid:
            continue

        obj = 0.0
        for m, rid in zip(meetings, combo):
            if rid is None:
                obj += UNASSIGNED_PENALTY
            else:
                r_cap = next(rm["capacity"] for rm in rooms if str(rm["id"]) == rid)
                obj += waste_penalty_weight * (r_cap - int(m.get("attendees", 1)))

        mid_to_room = {str(m["id"]): rid for m, rid in zip(meetings, combo)}
        dept_meetings: Dict[str, List[Dict]] = {}
        for m in meetings:
            dept = m.get("dept")
            if dept:
                dept_meetings.setdefault(str(dept), []).append(m)
        for dept, dms in dept_meetings.items():
            for i in range(len(dms)):
                for j in range(i + 1, len(dms)):
                    ma, mb = dms[i], dms[j]
                    gap_ab = mb["start_min"] - ma["end_min"]
                    gap_ba = ma["start_min"] - mb["end_min"]
                    is_consecutive = (0 <= gap_ab <= dept_gap) or (0 <= gap_ba <= dept_gap)
                    if not is_consecutive:
                        continue
                    ra, rb = mid_to_room[str(ma["id"])], mid_to_room[str(mb["id"])]
                    if ra is not None and ra == rb:
                        obj -= dept_same_room_bonus

        if best is None or obj < best[0]:
            best = (obj, dict(zip([str(m["id"]) for m in meetings], combo)))
    return best


def _scenario():
    meetings = [
        {"id": "M1", "dept": "X", "start_min": 0, "end_min": 60, "attendees": 5},
        {"id": "M2", "dept": "X", "start_min": 60, "end_min": 120, "attendees": 4},
        {"id": "M3", "dept": "Y", "start_min": 30, "end_min": 90, "attendees": 3},
    ]
    rooms = [
        {"id": "R1", "capacity": 5, "available_start_min": 0, "available_end_min": 1440},
        {"id": "R2", "capacity": 3, "available_start_min": 0, "available_end_min": 1440},
    ]
    config = {}
    return meetings, rooms, config


def _scenario_conflict():
    """R1しか入らない2会議が時間的に衝突 → 片方は未割当になるはずのケース。"""
    meetings = [
        {"id": "M1", "dept": None, "start_min": 0, "end_min": 90, "attendees": 5},
        {"id": "M2", "dept": None, "start_min": 30, "end_min": 60, "attendees": 5},
    ]
    rooms = [
        {"id": "R1", "capacity": 5, "available_start_min": 0, "available_end_min": 1440},
    ]
    config = {}
    return meetings, rooms, config


def compare_with_docplex(meetings, rooms, config):
    """CPLEX Optimization Studio(cpoptimizer)がローカルにあるユーザー環境で
    実行する用。Backend/solvers/meeting_room_solver.py の本番クラスを
    直接叩いて、CPMpy(CP-SAT)の目的関数値と突き合わせる。
    Cowork(このサンドボックス)ではcpoptimizerが無いため実行できない。"""
    import sys
    from pathlib import Path
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))  # Backend/ をパスに追加
    from solvers.meeting_room_solver import MeetingRoomSolver

    solver_input = {
        "meta": {"instance_name": "cpmpy_pilot_compare"},
        "problem_class": "MeetingRoom",
        "meetings": meetings,
        "rooms": rooms,
        "config": {**config, "solve_time_sec": 10},
        "issue_statuses": {},
    }
    docplex_result = MeetingRoomSolver(solver_input).solve()
    docplex_obj = docplex_result["solutions"][0]["kpi"].get("objective_value")

    cpmpy_result = solve_with_cpmpy(meetings, rooms, config)

    print(f"docplex.cp objective = {docplex_obj}")
    print(f"CPMpy(CP-SAT) objective = {round(cpmpy_result['objective'], 2)}")
    print(f"MATCH: {docplex_obj == round(cpmpy_result['objective'], 2)}")
    return docplex_obj, cpmpy_result["objective"]


if __name__ == "__main__":
    for name, (meetings, rooms, config) in [
        ("normal (3 meetings, dept bonus)", _scenario()),
        ("forced conflict (1 unassigned)", _scenario_conflict()),
    ]:
        print(f"\n=== {name} ===")
        cpmpy_result = solve_with_cpmpy(meetings, rooms, config)
        bf_obj, bf_assign = brute_force_optimal(meetings, rooms, config)
        print(f"CPMpy(CP-SAT) objective = {cpmpy_result['objective']}, assignment = {cpmpy_result['assignment']}")
        print(f"Brute force  optimal    = {bf_obj}, example assignment = {bf_assign}")
        match = abs(cpmpy_result["objective"] - bf_obj) < 1e-6
        print(f"MATCH: {match}")
        assert match, "CPMpy objective does not match brute-force optimum!"

    print("\n全ケースでCPMpy(CP-SAT)の最適値がブルートフォースと一致しました。")
    print("\n(参考) CPLEXがローカルにある環境では以下でdocplex.cpとも比較できます:")
    print("  from pilot_cpmpy_meeting_room import compare_with_docplex, _scenario")
    print("  compare_with_docplex(*_scenario())")
