"""
Backend/solvers/inventory_replenishment_planner_solver.py

InventoryReplenishmentPlannerSolver — 多段階ロットサイジング（MIP）

solver_input keys:
  - problem_class: str
  - warehouses: List[Dict]  # 中央倉庫リスト
  - centers: List[Dict]     # 配送センターリスト
  - periods: List[Dict]     # 計画期（週）リスト
  - demands: List[Dict]     # 需要データ（center_id, period_id, quantity）
  - config: Dict            # holding_cost_rate, solve_time_sec 等
  - issue_statuses: Dict
"""

from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)


class InventoryReplenishmentPlannerSolver:
    """多段階ロットサイジング最適化（CPLEX MIP / docplex.mp）ソルバー。

    ヒアリング4-1（a）に従い、需要充足はハード制約として実装する。
    shortage変数によるスラック吸収は行わない。
    中央倉庫の週次供給能力上限を満たせない需要パターンでは
    モデルがInfeasibleと判定される。
    """

    _SOLVER_VERSION = "inventory_replenishment_planner_v1.1"

    def __init__(self, solver_input: dict) -> None:
        self.dsl = solver_input

    def solve(self) -> dict:
        warehouses   = self.dsl.get("warehouses", [])
        centers      = self.dsl.get("centers", [])
        periods      = self.dsl.get("periods", [])
        demands      = self.dsl.get("demands", [])
        config       = self.dsl.get("config", {})
        issue_statuses = self.dsl.get("issue_statuses", {})

        # --- バリデーション ---
        if not centers:
            return self._make_result(
                feasible=False,
                solutions=[],
                issues=[{
                    "id": "solve_failed", "severity": "CRITICAL",
                    "title": "設定エラー：配送センターが未定義",
                    "message": "配送センター（centers）が1件も定義されていません。",
                    "relatedContainerIds": [],
                }],
            )
        if not periods:
            return self._make_result(
                feasible=False,
                solutions=[],
                issues=[{
                    "id": "solve_failed", "severity": "CRITICAL",
                    "title": "設定エラー：計画期が未定義",
                    "message": "計画期（periods）が1件も定義されていません。",
                    "relatedContainerIds": [],
                }],
            )

        try:
            from solvers.base.ce_limit_mip_fallback import solve_with_ce_fallback
            solution_data = self._build_and_solve(
                warehouses, centers, periods, demands, config,
                solve_with_ce_fallback=solve_with_ce_fallback,
            )
        except Exception as e:
            logger.error(f"[InventoryReplenishmentPlanner] solve() 例外: {e}", exc_info=True)
            from solvers.base.solver_error_result import build_solver_crash_issue, solver_crash_extra_fields
            result = self._make_result(
                feasible=False,
                solutions=[],
                issues=[build_solver_crash_issue(e)],
            )
            result.update(solver_crash_extra_fields(e))
            return result

        if solution_data is None:
            return self._make_result(
                feasible=False,
                solutions=[],
                issues=[{
                    "id": "solve_failed", "severity": "CRITICAL",
                    "title": "最適解なし（実行不可能）",
                    "message": (
                        "制約を満たす発注計画が見つかりませんでした。"
                        "中央倉庫の週次供給能力（supply_capacity_per_period）に対し、"
                        "需要合計が大きすぎる可能性があります。"
                        "【緩和案1】supply_capacity_per_period を引き上げてください。"
                        "【緩和案2】全拠点の需要を均等に削減する方向での再計画を検討してください。"
                    ),
                    "relatedContainerIds": [],
                }],
            )

        issues = self._detect_issues(solution_data, warehouses, centers, periods, demands, config, issue_statuses)
        solution_obj = self._build_solution_obj(solution_data, centers, periods, config)

        return self._make_result(
            feasible=True,
            solutions=[solution_obj],
            issues=issues,
        )

    # -------------------------------------------------------------------------

    def _build_and_solve(
        self,
        warehouses: List[Dict],
        centers: List[Dict],
        periods: List[Dict],
        demands: List[Dict],
        config: Dict,
        solve_with_ce_fallback,
    ) -> Optional[Dict[str, Any]]:
        from docplex.mp.model import Model

        # --- インデックス構築 ---
        center_ids = [c["id"] for c in centers]
        period_ids = [p["id"] for p in periods]

        # 需要辞書: (center_id, period_id) -> quantity
        demand_map: Dict[tuple, float] = {}
        for d in demands:
            key = (d["center_id"], d["period_id"])
            demand_map[key] = demand_map.get(key, 0.0) + float(d["quantity"])

        # 配送センターごとの情報
        center_map = {c["id"]: c for c in centers}

        # コスト・パラメータ
        holding_cost_rate = float(config.get("holding_cost_rate", 0.05))
        fixed_shipping_cost = float(config.get("fixed_shipping_cost", 5000.0))
        supply_capacity_per_period = float(config.get("supply_capacity_per_period", 1e9))
        solve_time_sec = int(config.get("solve_time_sec", 30))

        # 最大在庫上限（安全係数: DSLで指定されていない場合の保守上限）
        max_inventory_default = float(config.get("max_inventory_per_center", 1e6))

        mdl = Model(name="InventoryReplenishmentPlanner")

        T = len(period_ids)
        C = len(center_ids)

        # --- 決定変数 ---
        # ship[c][t]: 拠点 c への期 t の発送量（非負整数 or 連続）
        use_integer = config.get("use_integer_lots", True)
        lot_min = int(config.get("lot_min_unit", 1))

        if use_integer:
            ship = {
                (c_id, p_id): mdl.integer_var(lb=0, ub=int(max_inventory_default // lot_min),
                                               name=f"ship_{c_id}_{p_id}")
                for c_id in center_ids for p_id in period_ids
            }
        else:
            ship = {
                (c_id, p_id): mdl.continuous_var(lb=0, ub=max_inventory_default,
                                                  name=f"ship_{c_id}_{p_id}")
                for c_id in center_ids for p_id in period_ids
            }

        # y[c][t]: 発送フラグ（発送量>0 なら 1）→ 固定費計上のため
        y = {
            (c_id, p_id): mdl.binary_var(name=f"y_{c_id}_{p_id}")
            for c_id in center_ids for p_id in period_ids
        }

        # inv[c][t]: 期末在庫量（非負連続）
        # lb=0 により期末在庫の非負制約（欠品不可）を保証する
        inv = {
            (c_id, p_id): mdl.continuous_var(lb=0, ub=max_inventory_default,
                                              name=f"inv_{c_id}_{p_id}")
            for c_id in center_ids for p_id in period_ids
        }

        # --- 在庫フロー保存制約（ハード制約）---
        # inv[c][t] = inv[c][t-1] + ship[c][t] - demand[c][t]
        # inv[c][t] >= 0 は変数の lb=0 で保証済み（欠品・バックオーダー不可）
        for c_id in center_ids:
            c_info = center_map[c_id]
            init_inv = float(c_info.get("initial_inventory", 0.0))
            for ti, p_id in enumerate(period_ids):
                d = demand_map.get((c_id, p_id), 0.0)
                if ti == 0:
                    prev_inv = init_inv
                else:
                    prev_inv = inv[(c_id, period_ids[ti - 1])]
                mdl.add_constraint(
                    inv[(c_id, p_id)] == prev_inv + ship[(c_id, p_id)] - d,
                    ctname=f"flow_{c_id}_{p_id}",
                )

        # --- 発送フラグ連結（Big-M）---
        big_m = max_inventory_default
        for c_id in center_ids:
            for p_id in period_ids:
                # ship > 0 → y = 1（y=0 のとき ship=0 を強制）
                mdl.add_constraint(ship[(c_id, p_id)] <= big_m * y[(c_id, p_id)],
                                   ctname=f"flag_{c_id}_{p_id}")

        # --- 中央倉庫の週次供給能力上限（ハード制約）---
        for p_id in period_ids:
            mdl.add_constraint(
                mdl.sum(ship[(c_id, p_id)] for c_id in center_ids) <= supply_capacity_per_period,
                ctname=f"supply_cap_{p_id}",
            )

        # --- 目的関数 ---
        # 発送固定費 + 在庫保管費（shortage変数・ペナルティ項は廃止）
        # 項1: 発送固定費（発送フラグ y[c][t]=1 の週のみ）
        shipping_cost_term = mdl.sum(
            fixed_shipping_cost * y[(c_id, p_id)]
            for c_id in center_ids for p_id in period_ids
        )

        # 項2: 在庫保管費（期末在庫 × 保管費率）
        holding_cost_term = mdl.sum(
            holding_cost_rate * inv[(c_id, p_id)]
            for c_id in center_ids for p_id in period_ids
        )

        mdl.minimize(shipping_cost_term + holding_cost_term)

        # --- ソルブ ---
        sol = solve_with_ce_fallback(mdl, log_output=False, time_limit=solve_time_sec)
        if sol is None:
            return None

        # --- 解抽出 ---
        shipments = []
        inventories = []
        total_shipping_cost = 0.0
        total_holding_cost = 0.0

        for c_id in center_ids:
            for ti, p_id in enumerate(period_ids):
                ship_val = max(0.0, sol.get_value(ship[(c_id, p_id)]))
                inv_val  = max(0.0, sol.get_value(inv[(c_id, p_id)]))

                if use_integer:
                    ship_val = round(ship_val)

                d = demand_map.get((c_id, p_id), 0.0)

                fc = fixed_shipping_cost if ship_val > 0.5 else 0.0
                hc = holding_cost_rate * inv_val
                total_shipping_cost += fc
                total_holding_cost  += hc

                shipments.append({
                    "center_id":     c_id,
                    "period_id":     p_id,
                    "period_index":  ti,
                    "quantity":      ship_val,
                    "shipped":       ship_val > 0.5,
                    "inventory_end": inv_val,
                    "demand":        d,
                    "fixed_cost":    fc,
                    "holding_cost":  hc,
                })
                inventories.append({
                    "center_id": c_id,
                    "period_id": p_id,
                    "inventory":  inv_val,
                })

        obj_val = sol.get_objective_value()

        return {
            "shipments":            shipments,
            "inventories":          inventories,
            "total_shipping_cost":  total_shipping_cost,
            "total_holding_cost":   total_holding_cost,
            "total_cost":           obj_val,
            "center_ids":           center_ids,
            "period_ids":           period_ids,
        }

    # -------------------------------------------------------------------------

    def _detect_issues(
        self,
        solution_data: Dict,
        warehouses, centers, periods, demands, config, issue_statuses,
    ) -> List[Dict]:
        from solvers.base.issue_rules import build_full_unassignment_issue

        issues: List[Dict] = []

        shipments = solution_data.get("shipments", [])

        # 全件未割当チェック
        shipped_count = sum(1 for s in shipments if s.get("shipped"))
        anomaly = build_full_unassignment_issue(
            assigned_count=shipped_count,
            total_count=len(shipments),
            entity_label="発送計画",
            extra_hint="sol.get_value() での解抽出処理",
        )
        if anomaly:
            issues.append(anomaly)

        # 在庫過剰チェック
        max_inv_threshold = float(config.get("max_inventory_per_center", 1e6))
        if max_inv_threshold < 1e5:
            over_inv = [s for s in shipments if s.get("inventory_end", 0) > max_inv_threshold * 0.95]
            if over_inv:
                iid = "inventory_near_limit"
                if issue_statuses.get(iid) != "ACCEPTED":
                    issues.append({
                        "id": iid,
                        "severity": "INFO",
                        "title": f"在庫上限近接: {len(over_inv)}件",
                        "message": (
                            f"{len(over_inv)}件の期末在庫が上限({int(max_inv_threshold)}個)の95%を超えています。"
                            "在庫キャパシティを確認してください。"
                        ),
                        "relatedContainerIds": [],
                    })

        return issues

    def _build_solution_obj(
        self, solution_data: Dict, centers, periods, config
    ) -> Dict:
        total_cost = solution_data.get("total_cost", 0.0)
        total_shipping_cost = solution_data.get("total_shipping_cost", 0.0)
        total_holding_cost  = solution_data.get("total_holding_cost", 0.0)

        return {
            "name":    "Plan A",
            "label":   "最適発注計画",
            "feasible": True,
            "shipments":   solution_data.get("shipments", []),
            "inventories": solution_data.get("inventories", []),
            "metrics": {
                "total_cost":          round(total_cost, 2),
                "total_shipping_cost": round(total_shipping_cost, 2),
                "total_holding_cost":  round(total_holding_cost, 2),
                "num_centers":         len(centers),
                "num_periods":         len(periods),
            },
        }

    def _make_result(
        self,
        feasible: bool,
        solutions: List[Dict],
        issues: List[Dict],
    ) -> Dict[str, Any]:
        return {
            "status":    "ok",
            "feasible":  feasible,
            "metadata":  {"problem_class": "InventoryReplenishmentPlanner"},
            "solutions": solutions,
            "issues":    issues,
            "_solver_version": self._SOLVER_VERSION,
        }