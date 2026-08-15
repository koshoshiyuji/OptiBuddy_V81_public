"""
pilot_cpmpy_line_changeover_scheduler.py — CPMpyパイロット検証 (LineChangeoverScheduler)

目的:
  Backend/solvers/line_changeover_scheduler_solver.py の docplex.cp モデル
  （RCPSP: interval_var + pulse累積資源制約 + end_before_start前後関係 +
  makespan最小化）を CPMpy(ortools/CP-SATバックエンド)で等価に組めるかを検証する。

  DESIGN_2026-08-12_cp_sat_backend_support.md の「追記2: CPMpyパイロット結果」で
  car_sequencing/meeting_roomの2ドメインが検証済みだったが、meeting_roomは
  start/end/sizeが全て定数（部屋割当のみが決定変数）という、interval_var系の
  中でも簡単な部類のケースだった。本スクリプトは「未検証」として残されていた
  真のフレキシブルスケジューリング（start自体が決定変数になるRCPSP系）の
  最初のパイロット。

このスクリプトが検証する対応関係:
  docplex.cp                                    CPMpy
  ------------------------------------------------------------------------
  mdl.interval_var(size=dur, end=(0,H))         start = cp.intvar(0, H-dur)
                                                 （start自体が決定変数。区間は
                                                 常にpresent=Trueなのでoptional
                                                 ではなくCumulative()で十分）
  mdl.pulse(itv, amount) の合計 <= capacity      cp.Cumulative(starts, durs,
                                                 ends, demands, capacity)
  mdl.end_before_start(itv_a, itv_b, delay=d)    end[a] + d <= start[b]
                                                 （= start[a]+dur[a]+d <= start[b]、
                                                 endが常にstart+durの線形式なので
                                                 素直に線形不等式に落ちる）
  mdl.max([mdl.end_of(iv) for iv in itvs])       cp.max([start[t]+dur[t] for t
                                                 in tasks])  (makespan)

注意（このサンドボックスの制約）:
  docplex.cp / CP Optimizerの実行エンジン(cpoptimizer)がこの環境には無いため、
  docplex.cp側を実際に解いて数値比較することはできない。代わりに、タスク数・
  horizonを小さく抑えたテストケースについて、全ての整数開始時刻の組み合わせを
  尽くす全探索（brute force）で真の最小メイクスパンを求め、CPMpy(CP-SAT)の解が
  それと一致するかを検証する。これはdocplex.cp実装の意図（前後関係+資源容量
  制約下でのメイクスパン最小化）そのものを独立に検証しているので、CPLEXが
  無くても「CPMpy版のモデルが正しいロジックを実装しているか」は確認できる。

  CPLEX側との実行結果の数値一致は、CPLEX Optimization Studioがインストール
  された環境で、本スクリプト末尾の compare_with_docplex() を使って別途確認
  する必要がある。
"""

from __future__ import annotations

import itertools
from typing import Dict, List

import cpmpy as cp


def build_cpmpy_model(scenario: Dict):
    tasks       = scenario["tasks"]
    resources   = scenario["resources"]
    precedences = scenario.get("precedences", [])
    horizon     = scenario.get("config", {}).get("horizon", 100)

    task_ids = [str(t["id"]) for t in tasks]
    dur = {str(t["id"]): int(t.get("duration", 1)) for t in tasks}

    # --- 変数: docplex.cp の interval_var(size=dur, end=(0,horizon)) と同一の
    #     自由度。start自体が決定変数（真のフレキシブルスケジューリング）。ここが
    #     meeting_roomパイロット（start/endが定数）との決定的な違い。
    start = {tid: cp.intvar(0, horizon - dur[tid], name=f"start_{tid}") for tid in task_ids}
    end   = {tid: start[tid] + dur[tid] for tid in task_ids}

    m = cp.Model()

    # --- ハード制約: 資源容量（cumul_function <= cap の docplex.pulse合計に相当）
    for r in resources:
        rid = str(r["id"])
        cap = int(r.get("capacity", 1))
        rt, rd, re, rdem = [], [], [], []
        for t in tasks:
            tid = str(t["id"])
            for rreq in t.get("resource_requirements", []):
                if str(rreq.get("resource_id")) == rid:
                    rt.append(start[tid])
                    rd.append(dur[tid])
                    re.append(end[tid])
                    rdem.append(int(rreq.get("amount", 1)))
        if rt:
            m += cp.Cumulative(rt, rd, re, rdem, cap)

    # --- ハード制約: 前後関係（end_before_start(a, b, delay) 相当）
    for prec in precedences:
        f = str(prec.get("from_task", ""))
        t = str(prec.get("to_task", ""))
        delay = int(prec.get("min_delay", 0))
        if f in start and t in start:
            m += (end[f] + delay <= start[t])

    # --- 目的関数: メイクスパン最小化
    makespan = cp.max([end[tid] for tid in task_ids])
    m.minimize(makespan)

    return m, start, end, task_ids


def solve_with_cpmpy(scenario: Dict, time_limit=10):
    m, start, end, task_ids = build_cpmpy_model(scenario)
    ok = m.solve(solver="ortools", time_limit=time_limit)
    if not ok:
        return {"feasible": False, "makespan": None, "schedule": None}
    schedule = {tid: (int(start[tid].value()), int(end[tid].value())) for tid in task_ids}
    makespan = max(e for _, e in schedule.values())
    return {"feasible": True, "makespan": makespan, "schedule": schedule}


# ---------------------------------------------------------------------------
# 独立検証: 全探索によるブルートフォース（docplex.cpにもCPMpyにも依存しない）
# ---------------------------------------------------------------------------

def brute_force_optimal(scenario: Dict):
    """全タスクの開始時刻の取りうる組み合わせを尽くして、資源容量制約・前後関係
    制約を満たす中で最小のメイクスパンを求める。horizon・タスク数が小さい前提の
    厳密解法（RCPSPは一般にNP困難だが、パイロット検証用の小規模ケースなら
    全探索で真の最適値が求まる）。"""
    tasks       = scenario["tasks"]
    resources   = scenario["resources"]
    precedences = scenario.get("precedences", [])
    horizon     = scenario.get("config", {}).get("horizon", 100)

    task_ids = [str(t["id"]) for t in tasks]
    dur = {str(t["id"]): int(t.get("duration", 1)) for t in tasks}
    req = {
        str(t["id"]): [
            (str(rr.get("resource_id")), int(rr.get("amount", 1)))
            for rr in t.get("resource_requirements", [])
        ]
        for t in tasks
    }
    cap = {str(r["id"]): int(r.get("capacity", 1)) for r in resources}
    prec_pairs = [
        (str(p.get("from_task", "")), str(p.get("to_task", "")), int(p.get("min_delay", 0)))
        for p in precedences
    ]

    start_ranges = [range(0, horizon - dur[tid] + 1) for tid in task_ids]

    def resource_ok(starts):
        # 各時刻ごとに資源使用量を積算して容量超過が無いか確認（スイープ）
        usage: Dict[str, Dict[int, int]] = {rid: {} for rid in cap}
        for tid, s in zip(task_ids, starts):
            e = s + dur[tid]
            for rid, amount in req[tid]:
                if rid not in usage:
                    continue
                for tt in range(s, e):
                    usage[rid][tt] = usage[rid].get(tt, 0) + amount
        for rid, timeline in usage.items():
            if any(v > cap[rid] for v in timeline.values()):
                return False
        return True

    def precedence_ok(starts):
        s = dict(zip(task_ids, starts))
        for f, t, delay in prec_pairs:
            if f in s and t in s:
                if s[f] + dur[f] + delay > s[t]:
                    return False
        return True

    best = None
    for combo in itertools.product(*start_ranges):
        if not precedence_ok(combo):
            continue
        if not resource_ok(combo):
            continue
        makespan = max(s + dur[tid] for tid, s in zip(task_ids, combo))
        if best is None or makespan < best[0]:
            best = (makespan, dict(zip(task_ids, combo)))
    return best


def _scenario_single_resource_serial():
    """資源容量=1、amount=1の3タスクが同じ資源を取り合う → 純粋な直列化。
    最適メイクスパンは常に sum(duration) になるはず（並列不可能なため）。"""
    return {
        "tasks": [
            {"id": "A", "duration": 2, "resource_requirements": [{"resource_id": "R1", "amount": 1}]},
            {"id": "B", "duration": 3, "resource_requirements": [{"resource_id": "R1", "amount": 1}]},
            {"id": "C", "duration": 2, "resource_requirements": [{"resource_id": "R1", "amount": 1}]},
        ],
        "resources": [{"id": "R1", "capacity": 1}],
        "precedences": [],
        "config": {"horizon": 10},
    }


def _scenario_parallel_with_precedence():
    """資源容量=2で一部並列化可能 + 前後関係(A→C, delay=1)を課したケース。
    並列化の余地と前後関係が両方効くため、単純なsum(duration)にはならない。"""
    return {
        "tasks": [
            {"id": "A", "duration": 2, "resource_requirements": [{"resource_id": "R1", "amount": 1}]},
            {"id": "B", "duration": 3, "resource_requirements": [{"resource_id": "R1", "amount": 1}]},
            {"id": "C", "duration": 2, "resource_requirements": [{"resource_id": "R1", "amount": 1}]},
            {"id": "D", "duration": 1, "resource_requirements": [{"resource_id": "R1", "amount": 1}]},
        ],
        "resources": [{"id": "R1", "capacity": 2}],
        "precedences": [{"from_task": "A", "to_task": "C", "min_delay": 1}],
        "config": {"horizon": 10},
    }


def compare_with_docplex(scenario: Dict):
    """CPLEX Optimization Studio(cpoptimizer)がローカルにあるユーザー環境で実行
    する用。Backend/solvers/line_changeover_scheduler_solver.py の本番クラスを
    直接叩いて、CPMpy(CP-SAT)のメイクスパンと突き合わせる。
    Cowork(このサンドボックス)ではcpoptimizerが無いため実行できない。"""
    import sys
    from pathlib import Path
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))  # Backend/ をパスに追加
    from solvers.line_changeover_scheduler_solver import LineChangeoverSchedulerSolver

    solver_input = {
        "problem_class": "LineChangeoverScheduler",
        "meta": {"instance_name": "cpmpy_pilot_compare"},
        "tasks": scenario["tasks"],
        "resources": scenario["resources"],
        "precedences": scenario.get("precedences", []),
        "config": {**scenario.get("config", {}), "time_limit_sec": 10},
        "issue_statuses": {},
    }
    docplex_result = LineChangeoverSchedulerSolver(solver_input).solve()
    docplex_makespan = docplex_result["solutions"][0]["makespan"] if docplex_result["solutions"] else None

    cpmpy_result = solve_with_cpmpy(scenario)

    print(f"docplex.cp makespan = {docplex_makespan}")
    print(f"CPMpy(CP-SAT) makespan = {cpmpy_result['makespan']}")
    print(f"MATCH: {docplex_makespan == cpmpy_result['makespan']}")
    return docplex_makespan, cpmpy_result["makespan"]


if __name__ == "__main__":
    for name, scenario in [
        ("single resource, forced serial (n=3)", _scenario_single_resource_serial()),
        ("parallel capacity + precedence (n=4)", _scenario_parallel_with_precedence()),
    ]:
        print(f"\n=== {name} ===")
        cpmpy_result = solve_with_cpmpy(scenario)
        bf_makespan, bf_starts = brute_force_optimal(scenario)
        print(f"CPMpy(CP-SAT) makespan = {cpmpy_result['makespan']}, schedule = {cpmpy_result['schedule']}")
        print(f"Brute force  optimal   = {bf_makespan}, example starts = {bf_starts}")
        match = cpmpy_result["makespan"] == bf_makespan
        print(f"MATCH: {match}")
        assert match, "CPMpy makespan does not match brute-force optimum!"

    print("\n全ケースでCPMpy(CP-SAT)の最小メイクスパンがブルートフォースと一致しました。")
    print("(car_sequencing/meeting_roomと異なり、本ドメインはstart自体が決定変数の")
    print(" 真のフレキシブルスケジューリング。CPMpyのCumulative()で問題なく表現できた。)")
    print("\n(参考) CPLEXがローカルにある環境では以下でdocplex.cpとも比較できます:")
    print("  from pilot_cpmpy_line_changeover_scheduler import compare_with_docplex, _scenario_single_resource_serial")
    print("  compare_with_docplex(_scenario_single_resource_serial())")
