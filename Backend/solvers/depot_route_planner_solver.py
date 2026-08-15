"""
Backend/solvers/depot_route_planner_solver.py

DepotRoutePlannerSolver — 2段階 MIP+CP ハイブリッドソルバー
=============================================================

Stage1 (MIP, docplex.mp):
  open_d ∈ {0,1}: 拠点dを開設するか
  assign_cd ∈ {0,1}: 顧客cを拠点dに割り当てるか
  制約: assign_cd <= open_d, sum_d assign_cd == 1
  目的: 開設固定費用 + マンハッタン距離近似の割当コスト

Stage2 (CP, docplex.cp, 開設拠点ごとに独立):
  TruckDispatcherと同様の interval_var + sequence_var + no_overlap + transition_matrix
  で拠点↔顧客の巡回順序を決定。台数・容量・時間窓制約なし（単純TSP構造）。

距離計算: x/y平面座標のマンハッタン距離（|Δx|+|Δy|）をStage1・Stage2ともに使用。

最終報告コストは Stage1 近似値ではなく
  Σ_d open_d * opening_cost_d + Σ_d Stage2実巡回距離_d
を使う。

solver_input keys:
  problem_class, meta, depots, customers, config, issue_statuses
"""

from __future__ import annotations

import logging
import math
from typing import Any, Dict, List, Optional, Tuple

from solvers.base.issue_rules import run_issue_rules, build_full_unassignment_issue
from solvers.base.ce_limit_lns import CeLimitExceededError, is_ce_limit_exceeded
from solvers.base.ce_limit_mip_fallback import solve_with_ce_fallback
from solvers.base.solver_error_result import build_solver_crash_issue, solver_crash_extra_fields

logger = logging.getLogger(__name__)

_SOLVER_VERSION = "depot_route_planner_v1.1"
_LARGE_PENALTY = 1_000_000


class DepotRoutePlannerSolver:
    def __init__(self, solver_input: dict) -> None:
        self.dsl = solver_input

    def solve(self) -> dict:
        depots = self.dsl.get("depots", [])
        customers = self.dsl.get("customers", [])
        config = self.dsl.get("config", {})
        issue_statuses = self.dsl.get("issue_statuses", {})

        # バリデーション
        if not depots:
            return self._make_result(
                feasible=False,
                issues=[{
                    "id": "solve_failed",
                    "severity": "CRITICAL",
                    "title": "拠点が未定義です",
                    "message": "depots に候補拠点を1件以上指定してください。",
                    "relatedContainerIds": [],
                }],
            )
        if not customers:
            return self._make_result(
                feasible=False,
                issues=[{
                    "id": "solve_failed",
                    "severity": "CRITICAL",
                    "title": "顧客が未定義です",
                    "message": "customers に顧客を1件以上指定してください。",
                    "relatedContainerIds": [],
                }],
            )

        # 2026-08-13追加: Stage1(施設配置)もconfig.solver_engineで切り替え可能にする。
        # 従来はdocplex.mp(CPLEX MIP)を無条件に使っており、docplexが無い環境では
        # DepotRoutePlanner全体が使えなかった。施設配置は0/1変数+線形制約+線形目的
        # 関数のみのシンプルなモデルのため、CP-SAT(OR-Tools)はMIP互換の整数計画
        # ソルバーとしてそのまま解ける（Stage2のようなCP固有のグローバル制約は不要）。
        # Stage1・Stage2は同じconfig.solver_engineを共有する単一の軸として扱う
        # （エンジン解決は1回だけ行い、両ステージへ使い回す）。
        from solvers.base.engine_select import get_solver_engine, CPSAT
        try:
            engine = get_solver_engine(config)
        except Exception as e:
            logger.error(f"[DepotRoutePlanner] solver_engine解決エラー: {e}", exc_info=True)
            result = self._make_result(feasible=False, issues=[build_solver_crash_issue(e)])
            result.update(solver_crash_extra_fields(e))
            return result

        try:
            if engine == CPSAT:
                open_depots, assignments, stage1_cost = self._stage1_cpsat(depots, customers, config)
            else:
                open_depots, assignments, stage1_cost = self._stage1_mip(depots, customers, config)
        except Exception as e:
            if is_ce_limit_exceeded(e):
                return self._ce_limit_result()
            logger.error(f"[DepotRoutePlanner] Stage1 エラー: {e}", exc_info=True)
            result = self._make_result(feasible=False, issues=[build_solver_crash_issue(e)])
            result.update(solver_crash_extra_fields(e))
            return result

        if open_depots is None:
            return self._make_result(
                feasible=False,
                issues=[{
                    "id": "solve_failed",
                    "severity": "CRITICAL",
                    "title": "割当最適化に失敗しました",
                    "message": "拠点選定・顧客割当フェーズ（Stage1 MIP）で解が見つかりませんでした。"
                               "拠点候補数・顧客数・予算制約を見直してください。",
                    "relatedContainerIds": [],
                }],
            )

        # Stage2: 開設拠点ごとに独立CPモデルで巡回順序決定
        # 2026-08-13修正: engineはStage1と同じ解決結果を使い回す
        # （以前はここで再度get_solver_engine(config)を呼んでいたが、
        # Stage1・Stage2は単一の軸なので二重解決は不要かつドリフトの元）。
        routes = []
        total_travel_distance = 0.0
        stage2_error: Optional[str] = None

        for depot in open_depots:
            did = str(depot["id"])
            assigned_customers = [c for c in customers if assignments.get(str(c["id"])) == did]
            if not assigned_customers:
                routes.append({
                    "depot_id": did,
                    "depot_name": depot.get("name", did),
                    "order": [],
                    "travel_distance": 0.0,
                })
                continue
            try:
                if engine == CPSAT:
                    order, dist = self._stage2_cpsat(depot, assigned_customers, config)
                else:
                    order, dist = self._stage2_cp(depot, assigned_customers, config)
            except Exception as e:
                if is_ce_limit_exceeded(e):
                    return self._ce_limit_result()
                logger.warning(f"[DepotRoutePlanner] Stage2 CP 拠点{did} エラー: {e}", exc_info=True)
                # Stage2失敗時はgreedy近似で代替
                order, dist = self._greedy_tsp(depot, assigned_customers)
                stage2_error = str(e)

            total_travel_distance += dist
            routes.append({
                "depot_id": did,
                "depot_name": depot.get("name", did),
                "order": order,
                "travel_distance": round(dist, 3),
            })

        # 最終コスト（Stage2実距離使用）
        opening_cost_total = sum(d.get("opening_cost", 0) for d in open_depots)
        final_total_cost = opening_cost_total + total_travel_distance

        # assignments を顧客リストの形式に変換
        assigned_list = []
        depot_map = {str(d["id"]): d for d in depots}
        for c in customers:
            cid = str(c["id"])
            did = assignments.get(cid)
            assigned_list.append({
                "customer_id": cid,
                "customer_name": c.get("name", cid),
                "depot_id": did,
                "depot_name": depot_map.get(did, {}).get("name", did) if did else None,
            })

        # issue検知
        issues = self._detect_issues(
            open_depots, customers, assigned_list, routes,
            stage2_error=stage2_error,
            issue_statuses=issue_statuses,
        )

        anomaly = build_full_unassignment_issue(
            assigned_count=len([a for a in assigned_list if a["depot_id"]]),
            total_count=len(customers),
            entity_label="顧客",
            extra_hint="Stage1 MIP assign変数の解抽出処理",
        )
        if anomaly:
            issues.insert(0, anomaly)

        return self._make_result(
            feasible=True,
            solutions=[{
                "name": "Plan A",
                "label": "最適ルート",
                "feasible": True,
                "open_depots": [{"id": str(d["id"]), "name": d.get("name", str(d["id"])),
                                 "opening_cost": d.get("opening_cost", 0),
                                 "x": d.get("x", 0.0), "y": d.get("y", 0.0)}
                                for d in open_depots],
                "assignments": assigned_list,
                "routes": routes,
                "kpi": {
                    "opening_cost_total": round(opening_cost_total, 2),
                    "travel_distance_total": round(total_travel_distance, 3),
                    "final_total_cost": round(final_total_cost, 2),
                    "num_open_depots": len(open_depots),
                    "num_customers": len(customers),
                },
            }],
            issues=issues,
        )

    # -------------------------------------------------------------------------
    # Stage1: MIP（施設配置・顧客割当）
    # -------------------------------------------------------------------------

    def _stage1_mip(
        self,
        depots: List[Dict],
        customers: List[Dict],
        config: Dict,
    ) -> Tuple[Optional[List[Dict]], Dict[str, str], float]:
        """
        open_d / assign_cd を MIP で決定する。
        Returns: (open_depots_list, {customer_id -> depot_id}, approximate_cost)
        距離近似にはx/y平面マンハッタン距離を使用。
        """
        from docplex.mp.model import Model

        max_open = config.get("max_open_depots", len(depots))
        time_limit = float(config.get("stage1_time_limit_sec", 30.0))

        mdl = Model(name="DepotRoutePlanner_Stage1")
        mdl.parameters.timelimit = time_limit

        D = [str(d["id"]) for d in depots]
        C = [str(c["id"]) for c in customers]
        depot_map = {str(d["id"]): d for d in depots}
        cust_map = {str(c["id"]): c for c in customers}

        # 決定変数
        open_var = {d: mdl.binary_var(name=f"open_{d}") for d in D}
        assign_var = {(c, d): mdl.binary_var(name=f"assign_{c}_{d}") for c in C for d in D}

        # 制約1: 未開設拠点への割当不可
        for c in C:
            for d in D:
                mdl.add_constraint(assign_var[(c, d)] <= open_var[d])

        # 制約2: 全件割当（必ず1拠点に割当）
        for c in C:
            mdl.add_constraint(mdl.sum(assign_var[(c, d)] for d in D) == 1)

        # 制約3: 最大開設数
        mdl.add_constraint(mdl.sum(open_var[d] for d in D) <= max_open)

        # 目的: 開設固定費 + マンハッタン距離近似（x/y平面座標）
        opening_costs = mdl.sum(
            open_var[d] * depot_map[d].get("opening_cost", 0) for d in D
        )
        assign_costs = mdl.sum(
            assign_var[(c, d)] * _manhattan_xy(cust_map[c], depot_map[d])
            for c in C for d in D
        )
        mdl.minimize(opening_costs + assign_costs)

        sol = solve_with_ce_fallback(mdl, log_output=False)
        if sol is None:
            return None, {}, 0.0

        open_depots = [depot_map[d] for d in D if sol.get_value(open_var[d]) > 0.5]
        assignments = {}
        for c in C:
            for d in D:
                if sol.get_value(assign_var[(c, d)]) > 0.5:
                    assignments[c] = d
                    break

        approx_cost = sol.objective_value
        return open_depots, assignments, approx_cost

    def _stage1_cpsat(
        self,
        depots: List[Dict],
        customers: List[Dict],
        config: Dict,
    ) -> Tuple[Optional[List[Dict]], Dict[str, str], float]:
        """
        CP-SAT(CPMpy)版Stage1。_stage1_mip()と同じ入出力契約
        (open_depots_list, {customer_id -> depot_id}, approximate_cost)。

        施設配置（uncapacitated facility location）は0/1変数+線形制約+線形目的関数
        のみのシンプルなモデルであり、CP-SAT(OR-Tools)自体がMIP互換の整数計画
        ソルバーであるため、そのまま直接翻訳できる（Stage2のTSPのようにCircuit等の
        CP固有グローバル制約への置き換えが必要な新規性は無い）。
        """
        import cpmpy as cp

        max_open = config.get("max_open_depots", len(depots))
        time_limit = float(config.get("stage1_time_limit_sec", 30.0))

        D = [str(d["id"]) for d in depots]
        C = [str(c["id"]) for c in customers]
        depot_map = {str(d["id"]): d for d in depots}
        cust_map = {str(c["id"]): c for c in customers}

        # CP-SATは目的関数に整数係数のみ受け付けるため、開設費用・距離(いずれもfloat
        # ありうる)を整数化する（他ドメインと同じSCALEパターン）。
        SCALE = 1000

        open_var = {d: cp.boolvar(name=f"open_{d}") for d in D}
        assign_var = {(c, d): cp.boolvar(name=f"assign_{c}_{d}") for c in C for d in D}

        m = cp.Model()

        # 制約1: 未開設拠点への割当不可
        for c in C:
            for d in D:
                m += (assign_var[(c, d)] <= open_var[d])

        # 制約2: 全件割当（必ず1拠点に割当）
        for c in C:
            m += (cp.sum([assign_var[(c, d)] for d in D]) == 1)

        # 制約3: 最大開設数
        m += (cp.sum([open_var[d] for d in D]) <= max_open)

        # 目的: 開設固定費 + マンハッタン距離近似（x/y平面座標）
        opening_cost_terms = [
            int(round(depot_map[d].get("opening_cost", 0) * SCALE)) * open_var[d] for d in D
        ]
        assign_cost_terms = [
            int(round(_manhattan_xy(cust_map[c], depot_map[d]) * SCALE)) * assign_var[(c, d)]
            for c in C for d in D
        ]
        m.minimize(cp.sum(opening_cost_terms) + cp.sum(assign_cost_terms))

        solved = m.solve(solver="ortools", time_limit=time_limit)
        if not solved:
            return None, {}, 0.0

        open_depots = [depot_map[d] for d in D if open_var[d].value()]
        assignments: Dict[str, str] = {}
        for c in C:
            for d in D:
                if assign_var[(c, d)].value():
                    assignments[c] = d
                    break

        obj_scaled = m.objective_value()
        approx_cost = (obj_scaled / SCALE) if obj_scaled is not None else 0.0
        return open_depots, assignments, approx_cost

    # -------------------------------------------------------------------------
    # Stage2: CP（拠点ごとの巡回順序決定、TSP）
    # -------------------------------------------------------------------------

    def _stage2_cp(
        self,
        depot: Dict,
        customers: List[Dict],
        config: Dict,
    ) -> Tuple[List[Dict], float]:
        """
        拠点 depot から顧客 customers を訪問する巡回路を CP で決定する。
        距離行列はx/y平面マンハッタン距離（|Δx|+|Δy|）を使用。
        Returns: (order_list, total_distance)
        order_list: [{"customer_id": ..., "customer_name": ..., "seq": ...}, ...]
        """
        from docplex.cp.model import CpoModel
        from docplex.cp.modeler import build_cpo_transition_matrix

        n = len(customers)
        if n == 1:
            c = customers[0]
            dist = _manhattan_xy(depot, c) + _manhattan_xy(c, depot)
            return [{"customer_id": str(c["id"]), "customer_name": c.get("name", str(c["id"])), "seq": 0}], dist

        time_limit = float(config.get("stage2_time_limit_sec", 20.0))

        # 距離行列: 0=depot, 1..n=customers（x/y平面マンハッタン距離）
        locs = [depot] + customers
        n_locs = len(locs)
        dist_mat = [[_manhattan_xy(locs[i], locs[j]) for j in range(n_locs)] for i in range(n_locs)]

        # transition_matrix（整数化、距離*1000 で精度確保）
        tm_data = [[int(round(dist_mat[i][j] * 1000)) for j in range(n_locs)] for i in range(n_locs)]
        tm = build_cpo_transition_matrix(tm_data)

        mdl = CpoModel(name=f"DepotRoutePlanner_Stage2_{depot['id']}")

        # interval_var: 顧客ごとの訪問（size=1 単位時間）
        itvs = []
        for i, c in enumerate(customers):
            itv = mdl.interval_var(size=1, name=f"visit_{c['id']}")
            itvs.append(itv)

        # 2026-08-13修正（本番Matching照合テストで発覚した既存バグ修正）:
        # 従来はdepot用interval_varを1個だけ作り、それに対して
        # first(seq, depot_itv) と last(seq, depot_itv) を同時に課していた。
        # docplexのfirst()/last()は「そのinterval variableがpresentなら、
        # sequence中で最初(または最後)の位置に来る」制約であり、depot_itvは
        # 1個(size=0、非optional)しかないため、顧客が1人以上いれば
        # 「最初」と「最後」を同時に満たすことは数学的に不可能
        # （sequence長2以上なら最初の位置≠最後の位置）だった。
        # そのため顧客が1人以上いる拠点では本モデルは常にinfeasibleとなり、
        # 毎回_greedy_tsp()フォールバック（劣った解）に落ちていた
        # （実測: cpsat=50.0 vs cpo(greedy代替)=54.0 のMISMATCHとして表面化）。
        # 標準的なCPOの閉路TSPパターンに従い、開始用ダミー(depot_start)と
        # 終了用ダミー(depot_end)の2個の interval_var（共にsize=0）に分離し、
        # それぞれに first()/last() を個別に課す。transition_matrix上は
        # どちらも depot の行/列(インデックス0)を使うよう types で揃える。
        depot_start_itv = mdl.interval_var(size=0, name="depot_start")
        depot_end_itv = mdl.interval_var(size=0, name="depot_end")
        all_itvs = [depot_start_itv] + itvs + [depot_end_itv]
        types = [0] + list(range(1, n_locs)) + [0]
        seq = mdl.sequence_var(all_itvs, types=types, name="route")

        # no_overlap + transition_matrix で巡回順序と遷移コストを表現
        mdl.add(mdl.no_overlap(seq, tm))

        # depot_startを巡回路の先頭、depot_endを末尾に固定（矛盾なく両立する）
        mdl.add(mdl.first(seq, depot_start_itv))
        mdl.add(mdl.last(seq, depot_end_itv))

        # 目的: depot_end interval の終了時刻を最小化（遷移コスト合計の代理）
        mdl.add(mdl.minimize(mdl.end_of(depot_end_itv)))

        msol = mdl.solve(TimeLimit=time_limit, LogVerbosity="Quiet")

        if msol is None or not msol:
            logger.warning(f"[DepotRoutePlanner] Stage2 CP 拠点{depot['id']} 解なし。greedy代替。")
            return self._greedy_tsp(depot, customers)

        # 解抽出: sequence_var から順序を復元
        seq_sol = msol.get_var_solution(seq)
        order = []
        if seq_sol is not None:
            try:
                seq_list = seq_sol.get_interval_variables()
                seq_num = 0
                for itv_sol in seq_list:
                    name = itv_sol.get_name()
                    if name in ("depot_start", "depot_end"):
                        continue
                    # visit_{customer_id} から customer_id を復元
                    cid = name.replace("visit_", "")
                    c_info = next((c for c in customers if str(c["id"]) == cid), None)
                    if c_info:
                        order.append({
                            "customer_id": cid,
                            "customer_name": c_info.get("name", cid),
                            "seq": seq_num,
                        })
                        seq_num += 1
            except Exception as e:
                logger.warning(f"[DepotRoutePlanner] Stage2 sequence抽出失敗: {e}。greedy代替。")
                return self._greedy_tsp(depot, customers)
        else:
            return self._greedy_tsp(depot, customers)

        # 実移動距離の計算（マンハッタン距離ベースのdist_matを使用）
        dist = self._route_distance(depot, customers, order, dist_mat)
        return order, dist

    def _stage2_cpsat(
        self,
        depot: Dict,
        customers: List[Dict],
        config: Dict,
    ) -> Tuple[List[Dict], float]:
        """CP-SAT(CPMpy)版Stage2。_stage2_cp()と同じ入出力契約(order, total_distance)。

        CPOの interval_var+sequence_var+no_overlap+transition_matrix+first/last による
        TSP定式化は、CP-SATでは cp.Circuit(succ)（単一ハミルトン閉路制約）+
        遷移コストの目的関数、という直接的な定式化に対応する
        （検証: Backend/tools/pilot_cpmpy_depot_route_planner_stage2.py）。
        succ[i] = ノードiの次の訪問先インデックス(0=depot, 1..n=customers)。
        """
        import cpmpy as cp
        from cpmpy.expressions.globalconstraints import Circuit

        n = len(customers)
        if n == 1:
            c = customers[0]
            dist = _manhattan_xy(depot, c) + _manhattan_xy(c, depot)
            return [{"customer_id": str(c["id"]), "customer_name": c.get("name", str(c["id"])), "seq": 0}], dist

        time_limit = float(config.get("stage2_time_limit_sec", 20.0))

        locs = [depot] + customers
        n_locs = len(locs)
        dist_mat = [[_manhattan_xy(locs[i], locs[j]) for j in range(n_locs)] for i in range(n_locs)]

        SCALE = 1000
        dist_int = [[int(round(dist_mat[i][j] * SCALE)) for j in range(n_locs)] for i in range(n_locs)]

        succ = cp.intvar(0, n_locs - 1, shape=n_locs, name="succ")
        m = cp.Model([Circuit(succ)])
        obj = cp.sum([cp.Element(dist_int[i], succ[i]) for i in range(n_locs)])
        m.minimize(obj)

        solved = m.solve(solver="ortools", time_limit=time_limit)

        if not solved:
            logger.warning(f"[DepotRoutePlanner][cpsat] Stage2 CP-SAT 拠点{depot['id']} 解なし。greedy代替。")
            return self._greedy_tsp(depot, customers)

        succ_val = [int(v) for v in succ.value()]
        order = []
        cur = 0
        seq_num = 0
        visited = set()
        while True:
            nxt = succ_val[cur]
            if nxt == 0:
                break
            c = customers[nxt - 1]
            order.append({
                "customer_id": str(c["id"]),
                "customer_name": c.get("name", str(c["id"])),
                "seq": seq_num,
            })
            seq_num += 1
            cur = nxt
            if cur in visited:
                logger.warning(f"[DepotRoutePlanner][cpsat] サブツアー検出、拠点{depot['id']}。greedy代替。")
                return self._greedy_tsp(depot, customers)
            visited.add(cur)

        dist = self._route_distance(depot, customers, order, dist_mat)
        return order, dist

    # -------------------------------------------------------------------------
    # Greedy TSP フォールバック（Stage2 CP 失敗時）
    # -------------------------------------------------------------------------

    def _greedy_tsp(
        self,
        depot: Dict,
        customers: List[Dict],
    ) -> Tuple[List[Dict], float]:
        """最近傍法による巡回順序の近似解。距離はx/y平面マンハッタン距離を使用。"""
        remaining = list(customers)
        order = []
        current = depot
        total_dist = 0.0
        seq_num = 0
        while remaining:
            nearest = min(remaining, key=lambda c: _manhattan_xy(current, c))
            total_dist += _manhattan_xy(current, nearest)
            order.append({
                "customer_id": str(nearest["id"]),
                "customer_name": nearest.get("name", str(nearest["id"])),
                "seq": seq_num,
            })
            current = nearest
            remaining.remove(nearest)
            seq_num += 1
        # 拠点に戻る距離を加算
        total_dist += _manhattan_xy(current, depot)
        return order, total_dist

    def _route_distance(
        self,
        depot: Dict,
        customers: List[Dict],
        order: List[Dict],
        dist_mat: List[List[float]],
    ) -> float:
        """order に従って実移動距離を計算する（depot → ... → depot）。"""
        # locs[0] = depot, locs[1..n] = customers
        locs = [depot] + customers
        loc_idx = {str(locs[i]["id"]): i for i in range(len(locs))}
        loc_idx[str(depot["id"])] = 0

        total = 0.0
        prev_idx = 0  # depot
        for step in sorted(order, key=lambda x: x["seq"]):
            cid = step["customer_id"]
            cur_idx = loc_idx.get(cid, 0)
            total += dist_mat[prev_idx][cur_idx]
            prev_idx = cur_idx
        total += dist_mat[prev_idx][0]  # 拠点に戻る
        return total

    # -------------------------------------------------------------------------
    # Issue 検知
    # -------------------------------------------------------------------------

    def _detect_issues(
        self,
        open_depots: List[Dict],
        customers: List[Dict],
        assigned_list: List[Dict],
        routes: List[Dict],
        stage2_error: Optional[str],
        issue_statuses: Dict[str, str],
    ) -> List[Dict]:
        issues = []

        # 未割当顧客
        unassigned = [a for a in assigned_list if not a["depot_id"]]
        if unassigned:
            names = ", ".join(a["customer_name"] for a in unassigned[:5])
            issues.append({
                "id": "unassigned_customers",
                "severity": "CRITICAL",
                "title": f"未割当顧客が{len(unassigned)}件あります",
                "message": f"以下の顧客が拠点に割り当てられませんでした: {names}"
                           + ("..." if len(unassigned) > 5 else ""),
                "relatedContainerIds": [],
            })

        # Stage2フォールバック通知
        if stage2_error:
            issues.append({
                "id": "stage2_fallback",
                "severity": "WARNING",
                "title": "巡回順序の最適化でエラーが発生しました（近似解を使用）",
                "message": f"CP Optimizerでの巡回順序最適化に失敗し、最近傍法の近似解を使用しています。"
                           f"解の品質が低い可能性があります。詳細: {stage2_error}",
                "relatedContainerIds": [],
            })

        # ACCEPTEDフィルタ
        return [i for i in issues if issue_statuses.get(i["id"]) != "ACCEPTED"]

    # -------------------------------------------------------------------------
    # ヘルパー
    # -------------------------------------------------------------------------

    def _make_result(
        self,
        feasible: bool,
        solutions: Optional[List[Dict]] = None,
        issues: Optional[List[Dict]] = None,
    ) -> Dict[str, Any]:
        return {
            "status": "ok",
            "feasible": feasible,
            "metadata": {"problem_class": "DepotRoutePlanner"},
            "solutions": solutions or [],
            "issues": issues or [],
            "_solver_version": _SOLVER_VERSION,
        }

    def _ce_limit_result(self) -> Dict[str, Any]:
        return self._make_result(
            feasible=False,
            issues=[{
                "id": "ce_limit_unresolvable",
                "severity": "CRITICAL",
                "title": "CPLEXの無料版で扱える件数を超えています",
                "message": "拠点候補数または顧客数が多すぎます。件数を減らすか、正規ライセンスをご利用ください。",
                "relatedContainerIds": [],
            }],
        )


# -------------------------------------------------------------------------
# 距離計算ユーティリティ
# -------------------------------------------------------------------------

def _manhattan_xy(a: Dict, b: Dict) -> float:
    """x/y平面座標のマンハッタン距離（|Δx|+|Δy|）。Stage1・Stage2ともに使用。"""
    return abs(a.get("x", 0.0) - b.get("x", 0.0)) + abs(a.get("y", 0.0) - b.get("y", 0.0))
