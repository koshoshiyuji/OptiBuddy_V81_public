"""
pilot_cpmpy_car_sequencing.py — CPMpyパイロット検証 (CarSequencing)

目的:
  Backend/solvers/car_sequencing_solver.py の docplex.cp モデルを
  CPMpy(ortools/CP-SATバックエンド)で等価に組めるかを検証する。

このスクリプトが検証する対応関係:
  docplex.cp                              CPMpy
  ------------------------------------------------------------------
  mdl.integer_var(lb, ub)                 cp.intvar(lb, ub)
  seq[pos] == idx  (bool式として扱う)      同様に bool式として扱える
  mdl.sum([...])                          cp.sum([...]) / sum([...])
  mdl.max([0, expr])                      cp.max([0, expr])
  mdl.logical_and([...])                  cp.all([...])
  mdl.add(mdl.minimize(obj))              m = cp.Model(minimize=obj)

注意（このサンドボックスの制約）:
  docplex.cp / CP Optimizer の実行エンジン(cpoptimizer)がこの環境には
  無いため、docplex.cp側を実際に解いて数値比較することはできない。
  代わりに、位置数が小さいテストケースについて全探索（brute force）で
  真の最適値を求め、CPMpy(CP-SAT)の解がそれと一致するかを検証する。
  これはdocplex.cp実装の意図（car_sequencing_solver.pyのコメントに書かれた
  超過定義）そのものを独立に検証しているので、CPLEXが無くても
  「CPMpy版のモデルが正しいロジックを実装しているか」は確認できる。

  CPLEX側との実行結果の数値一致は、CPLEX Optimization Studioが
  インストールされたユーザーのローカル環境で、本スクリプト末尾の
  compare_with_docplex() を使って別途確認する必要がある。
"""

from __future__ import annotations

import itertools
from typing import Dict, List

import cpmpy as cp


W_OPTION_VIOLATION = 10
W_SAME_CAR_VIOLATION = 1
DEFAULT_MAX_CONSECUTIVE_SAME = 2


def sliding_window_ranges(n, window_size):
    if n < window_size:
        return []
    return [(s, s + window_size - 1) for s in range(n - window_size + 1)]


def build_cpmpy_model(scenario: Dict):
    car_types = scenario["car_types"]
    options = scenario["options"]
    config = scenario.get("config", {})

    total_cars = sum(ct.get("count", 0) for ct in car_types)
    max_consecutive = config.get("max_consecutive_same", DEFAULT_MAX_CONSECUTIVE_SAME)
    n = total_cars

    type_ids = [ct["car_type_id"] for ct in car_types]
    type_index = {tid: i for i, tid in enumerate(type_ids)}
    num_types = len(type_ids)

    option_has_type = []
    for opt in options:
        required_types = set(opt.get("car_type_ids", []))
        row = [1 if tid in required_types else 0 for tid in type_ids]
        option_has_type.append(row)

    # --- 変数: docplex.cp の integer_var(0, num_types-1) と同一 ---
    seq = cp.intvar(0, num_types - 1, shape=n, name="seq")

    m = cp.Model()

    # --- ハード制約: 各車種の生産台数 ---
    for ct in car_types:
        idx = type_index[ct["car_type_id"]]
        required_count = ct.get("count", 0)
        count_expr = cp.sum([seq[pos] == idx for pos in range(n)])
        m += (count_expr == required_count)

    # --- ソフト制約: スライディングウィンドウ超過 ---
    option_violation_terms = []
    for k, opt in enumerate(options):
        q = opt.get("window_size", 1)
        p = opt.get("max_per_window", 1)
        needs_opt = [j for j, has in enumerate(option_has_type[k]) if has == 1]
        if not needs_opt:
            continue
        count_exprs = [cp.sum([seq[pos] == j for j in needs_opt]) for pos in range(n)]
        for start, end in sliding_window_ranges(n, q):
            window_sum = cp.sum(count_exprs[start:end + 1])
            excess = cp.max([0, window_sum - p])
            option_violation_terms.append(W_OPTION_VIOLATION * excess)

    # --- ソフト制約: 同一車種連続超過 ---
    same_car_violation_terms = []
    if max_consecutive >= 1 and n > max_consecutive:
        for pos in range(max_consecutive, n):
            all_same = cp.all([seq[pos - i] == seq[pos] for i in range(1, max_consecutive + 1)])
            same_car_violation_terms.append(W_SAME_CAR_VIOLATION * all_same)

    objective_terms = option_violation_terms + same_car_violation_terms
    objective = cp.sum(objective_terms) if objective_terms else cp.intvar(0, 0)
    m.minimize(objective)

    return m, seq, type_ids


def solve_with_cpmpy(scenario: Dict, time_limit=10):
    m, seq, type_ids = build_cpmpy_model(scenario)
    ok = m.solve(solver="ortools", time_limit=time_limit)
    if not ok:
        return {"feasible": False, "objective": None, "sequence": None}
    sequence = [type_ids[int(v.value())] for v in seq]
    return {"feasible": True, "objective": int(m.objective_value()), "sequence": sequence}


# ---------------------------------------------------------------------------
# 独立検証: 全探索によるブルートフォース（docplex.cpに依存しない）
# ---------------------------------------------------------------------------

def brute_force_optimal(scenario: Dict):
    """car_sequencing_solver.py 本体のロジック定義（コメント参照）どおりに
    目的関数を再実装し、全順列を試して真の最適値を求める。
    docplex.cpにもCPMpyにも依存しない第三者的な検証。"""
    car_types = scenario["car_types"]
    options = scenario["options"]
    config = scenario.get("config", {})
    max_consecutive = config.get("max_consecutive_same", DEFAULT_MAX_CONSECUTIVE_SAME)

    multiset = []
    for ct in car_types:
        multiset += [ct["car_type_id"]] * ct.get("count", 0)

    best = None
    seen = set()
    for perm in itertools.permutations(multiset):
        if perm in seen:
            continue
        seen.add(perm)
        n = len(perm)
        obj = 0
        for opt in options:
            q = opt["window_size"]
            p = opt["max_per_window"]
            needs = set(opt.get("car_type_ids", []))
            values = [1 if t in needs else 0 for t in perm]
            for s, e in sliding_window_ranges(n, q):
                excess = max(0, sum(values[s:e + 1]) - p)
                obj += W_OPTION_VIOLATION * excess
        if max_consecutive >= 1 and n > max_consecutive:
            for pos in range(max_consecutive, n):
                if all(perm[pos - i] == perm[pos] for i in range(1, max_consecutive + 1)):
                    obj += W_SAME_CAR_VIOLATION
        if best is None or obj < best[0]:
            best = (obj, perm)
    return best


def _scenario_small():
    """既存テスト test_car_sequencing_ce_limit.py の _scenario() と同一。"""
    return {
        "car_types": [
            {"car_type_id": "A", "count": 2},
            {"car_type_id": "B", "count": 2},
        ],
        "options": [
            {"option_id": "opt1", "window_size": 2, "max_per_window": 1, "car_type_ids": ["A"]},
        ],
        "config": {},
    }


def _scenario_medium():
    """やや大きめのケース（3車種、2オプション）で追加検証。"""
    return {
        "car_types": [
            {"car_type_id": "A", "count": 3},
            {"car_type_id": "B", "count": 3},
            {"car_type_id": "C", "count": 2},
        ],
        "options": [
            {"option_id": "opt1", "window_size": 3, "max_per_window": 1, "car_type_ids": ["A"]},
            {"option_id": "opt2", "window_size": 2, "max_per_window": 1, "car_type_ids": ["B", "C"]},
        ],
        "config": {"max_consecutive_same": 1},
    }


def compare_with_docplex(scenario: Dict):
    """CPLEX Optimization Studio(cpoptimizer)がローカルにあるユーザー環境で
    実行する用。Backend/solvers/car_sequencing_solver.py の本番クラスを
    直接叩いて、CPMpy(CP-SAT)の目的関数値と突き合わせる。
    Cowork(このサンドボックス)ではcpoptimizerが無いため実行できない。"""
    import sys
    from pathlib import Path
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))  # Backend/ をパスに追加
    from solvers.car_sequencing_solver import CarSequencingSolver

    solver_input = {
        "problem_class": "CarSequencing",
        "meta": {"instance_name": "cpmpy_pilot_compare"},
        "car_types": scenario["car_types"],
        "options": scenario["options"],
        "config": {**scenario.get("config", {}), "solve_time_sec": 10},
        "issue_statuses": {},
    }
    docplex_result = CarSequencingSolver(solver_input).solve()
    docplex_obj = docplex_result["solutions"][0]["kpi"].get("objective_value")

    cpmpy_result = solve_with_cpmpy(scenario)

    print(f"docplex.cp objective = {docplex_obj}")
    print(f"CPMpy(CP-SAT) objective = {cpmpy_result['objective']}")
    print(f"MATCH: {docplex_obj == cpmpy_result['objective']}")
    return docplex_obj, cpmpy_result["objective"]


if __name__ == "__main__":
    for name, scenario in [("small (n=4)", _scenario_small()), ("medium (n=8)", _scenario_medium())]:
        print(f"\n=== {name} ===")
        cpmpy_result = solve_with_cpmpy(scenario)
        bf_obj, bf_perm = brute_force_optimal(scenario)
        print(f"CPMpy(CP-SAT) objective = {cpmpy_result['objective']}, sequence = {cpmpy_result['sequence']}")
        print(f"Brute force  optimal    = {bf_obj}, example = {list(bf_perm)}")
        match = cpmpy_result["objective"] == bf_obj
        print(f"MATCH: {match}")
        assert match, "CPMpy objective does not match brute-force optimum!"

    print("\n全ケースでCPMpy(CP-SAT)の最適値がブルートフォースと一致しました。")
    print("\n(参考) CPLEXがローカルにある環境では以下でdocplex.cpとも比較できます:")
    print("  from pilot_cpmpy_car_sequencing import compare_with_docplex, _scenario_small")
    print("  compare_with_docplex(_scenario_small())")
