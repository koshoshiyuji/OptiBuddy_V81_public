"""
Backend/tools/pilot_cpmpy_depot_route_planner_stage2.py

2026-08-13追加。DepotRoutePlannerSolver._stage2_cp() (docplex.cp: interval_var +
sequence_var + no_overlap + transition_matrix + first/last によるTSP定式化)を
CPMpy/CP-SATで代替できるかを検証するパイロット。

【CPOモデルの構造】
  - depot用ダミーinterval_var(size=0) + 顧客ごとのinterval_var(size=1)
  - sequence_var(全interval, types=各locationの一意インデックス)
  - no_overlap(seq, transition_matrix)  # 訪問順序 + 遷移コスト
  - first(seq, depot_itv) / last(seq, depot_itv)  # depotを先頭・末尾に固定
  - minimize(end_of(depot_itv))  # 遷移コスト合計の代理（全interval size合計+遷移コスト
    = depot終了時刻。今回のsize設定ではdepot終了時刻がそのまま総遷移距離の代理になる）

これは非対称TSP（depotを起点・終点とする閉路）そのものであり、CP-SAT/CPMpyでは
sequence_var/no_overlap/transition_matrixを個別に再現するのではなく、
cp.Circuit(succ)グローバル制約（Hamiltonian閉路）+ succ[i]の遷移先での距離を
Element()で引く目的関数、という直接的な定式化が自然に対応する。

succ[i] = ノードiの次に訪問するノードのインデックス（0=depot, 1..n=顧客）。
Circuit(succ)は「全ノードを含む単一の閉路」を強制するので、
depotを含む全顧客を1回ずつ訪問して戻ってくる巡回路と等価になる。

目的関数: Σ_i dist_mat[i][succ[i]] （閉路上の全辺の距離合計 = 総移動距離）
"""

from __future__ import annotations

import itertools
from typing import Any, Dict, List, Tuple


def _manhattan_xy(a: Dict, b: Dict) -> float:
    return abs(a.get("x", 0.0) - b.get("x", 0.0)) + abs(a.get("y", 0.0) - b.get("y", 0.0))


def build_dist_matrix(depot: Dict, customers: List[Dict]) -> List[List[float]]:
    locs = [depot] + customers
    n = len(locs)
    return [[_manhattan_xy(locs[i], locs[j]) for j in range(n)] for i in range(n)]


def build_cpmpy_model_and_solve(
    depot: Dict, customers: List[Dict], time_limit: float = 15.0
) -> Tuple[List[Dict], float]:
    """CP-SAT(CPMpy)でTSPを解き、(order, total_distance)を返す。

    order: [{"customer_id":..., "customer_name":..., "seq":...}, ...] (0-indexed訪問順)
    """
    import cpmpy as cp
    from cpmpy.expressions.globalconstraints import Circuit

    n = len(customers)
    if n == 1:
        c = customers[0]
        dist = _manhattan_xy(depot, c) + _manhattan_xy(c, depot)
        return [{"customer_id": str(c["id"]), "customer_name": c.get("name", str(c["id"])), "seq": 0}], dist

    dist_mat = build_dist_matrix(depot, customers)
    n_locs = len(dist_mat)

    # 整数化(距離*1000で精度確保、CPOモデルのtransition_matrix整数化と同じ規約)
    SCALE = 1000
    dist_int = [[int(round(dist_mat[i][j] * SCALE)) for j in range(n_locs)] for i in range(n_locs)]

    succ = cp.intvar(0, n_locs - 1, shape=n_locs, name="succ")
    m = cp.Model([Circuit(succ)])
    obj = cp.sum([cp.Element(dist_int[i], succ[i]) for i in range(n_locs)])
    m.minimize(obj)

    solved = m.solve(solver="ortools", time_limit=time_limit)
    if not solved:
        raise RuntimeError("CP-SAT: TSP infeasible (should not happen for a complete graph)")

    # succ[0]=depotの次ノード から辿って訪問順を復元
    succ_val = [int(v) for v in succ.value()]
    order = []
    cur = 0  # depot
    seq_num = 0
    visited = set()
    while True:
        nxt = succ_val[cur]
        if nxt == 0:
            break
        c = customers[nxt - 1]
        order.append({"customer_id": str(c["id"]), "customer_name": c.get("name", str(c["id"])), "seq": seq_num})
        seq_num += 1
        cur = nxt
        if cur in visited:
            raise RuntimeError("CP-SAT: サブツアー検出(Circuitのバグの疑い)")
        visited.add(cur)

    total_dist = sum(dist_mat[i][succ_val[i]] for i in range(n_locs))
    return order, total_dist


def brute_force_optimal(depot: Dict, customers: List[Dict]) -> float:
    """全順列探索による真の最適巡回距離（ゼロ依存、CPMpy/docplex不使用）。"""
    dist_mat = build_dist_matrix(depot, customers)
    n = len(customers)
    best = None
    for perm in itertools.permutations(range(1, n + 1)):
        total = 0.0
        prev = 0
        for idx in perm:
            total += dist_mat[prev][idx]
            prev = idx
        total += dist_mat[prev][0]
        if best is None or total < best:
            best = total
    return best


def compare_with_docplex(depot: Dict, customers: List[Dict], time_limit: float = 15.0):
    """実CPLEX(docplex.cp)側のStage2ロジックをそのまま呼び出し、CPMpy側と比較する。
    ローカルのdocplexインストール済み環境でのみ実行可能。
    """
    from solvers.depot_route_planner_solver import DepotRoutePlannerSolver
    solver = DepotRoutePlannerSolver({})
    order_cpo, dist_cpo = solver._stage2_cp(depot, customers, {"stage2_time_limit_sec": time_limit})

    order_cpmpy, dist_cpmpy = build_cpmpy_model_and_solve(depot, customers, time_limit)

    print(f"docplex.cp distance = {dist_cpo}")
    print(f"CPMpy(CP-SAT) distance = {dist_cpmpy}")
    print(f"MATCH: {abs(dist_cpo - dist_cpmpy) < 1e-3}")
    return dist_cpo, dist_cpmpy


def _scenario_small():
    depot = {"id": "D1", "name": "Depot1", "x": 0.0, "y": 0.0}
    customers = [
        {"id": "C1", "name": "Cust1", "x": 3.0, "y": 1.0},
        {"id": "C2", "name": "Cust2", "x": 5.0, "y": 5.0},
        {"id": "C3", "name": "Cust3", "x": -2.0, "y": 4.0},
        {"id": "C4", "name": "Cust4", "x": 1.0, "y": -3.0},
        {"id": "C5", "name": "Cust5", "x": -4.0, "y": -1.0},
    ]
    return depot, customers


if __name__ == "__main__":
    depot, customers = _scenario_small()

    expected = brute_force_optimal(depot, customers)
    order, dist = build_cpmpy_model_and_solve(depot, customers)
    print(f"brute_force_optimal = {expected}")
    print(f"CPMpy(CP-SAT) = {dist}")
    print(f"order = {order}")
    assert abs(expected - dist) < 1e-3, f"MISMATCH: brute_force={expected} vs cpmpy={dist}"
    print("PILOT PASSED: CPMpy(CP-SAT) matches brute-force optimal TSP distance")
