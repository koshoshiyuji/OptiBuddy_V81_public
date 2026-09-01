"""
Backend/solvers/transport_cost_minimizer_solver.py

TransportCostMinimizerSolver — 古典的Hitchcock輸送問題（LP/MIP）

solver_input keys:
  - problem_class: str ("TransportCostMinimizer")
  - factories: List[Dict]  # {id, name, supply}
  - stores:    List[Dict]  # {id, name, demand}
  - costs:     List[Dict]  # {factory_id, store_id, cost_per_unit}
  - config:    Dict        # {time_limit_sec, big_penalty}
  - time_limit_sec: int    # config.time_limit_sec のトップレベル複製
  - issue_statuses: Dict
"""

import logging
from typing import Any, Dict, List, Optional, Tuple

from solvers.base.solver_error_result import build_solver_crash_issue, solver_crash_extra_fields
from solvers.base.ce_limit_mip_fallback import solve_with_ce_fallback

logger = logging.getLogger(__name__)


class TransportCostMinimizerSolver:
    def __init__(self, solver_input: dict) -> None:
        self.dsl = solver_input

    def solve(self) -> dict:
        factories = self.dsl.get("factories", [])
        stores    = self.dsl.get("stores", [])
        costs     = self.dsl.get("costs", [])
        config    = self.dsl.get("config", {})

        # トップレベルの time_limit_sec が存在する場合は config へ反映する
        # （converter が両方のキーを出力するため、トップレベル値を優先して同期する）
        top_level_time_limit = self.dsl.get("time_limit_sec")
        if top_level_time_limit is not None:
            config = dict(config)
            config["time_limit_sec"] = top_level_time_limit

        validation_issues = self._validate(factories, stores, costs)
        if validation_issues:
            return self._make_result(
                feasible=False,
                flows=[],
                total_cost=0.0,
                issues=validation_issues,
            )

        try:
            flows, total_cost, mip_check_issues = self._build_and_solve(factories, stores, costs, config)
        except Exception as e:
            logger.error(f"[TransportCostMinimizer] mdl.solve() 例外: {e}", exc_info=True)
            result = self._make_result(
                feasible=False,
                flows=[],
                total_cost=0.0,
                issues=[build_solver_crash_issue(e)],
            )
            result.update(solver_crash_extra_fields(e))
            return result

        if flows is None:
            return self._make_result(
                feasible=False,
                flows=[],
                total_cost=0.0,
                issues=[{
                    "id":       "solve_failed",
                    "severity": "CRITICAL",
                    "category": "INFEASIBLE",
                    "title":    "輸送計画の実行可能解なし",
                    "message":  "供給量の合計が需要量の合計を下回るなど、制約を満たす輸送計画が存在しません。",
                    "relatedContainerIds": [],
                }],
            )

        issues = self._detect_issues(factories, stores, costs, flows)
        issues.extend(mip_check_issues)
        return self._make_result(
            feasible=True,
            flows=flows,
            total_cost=total_cost,
            issues=issues,
        )

    # ------------------------------------------------------------------

    def _validate(
        self,
        factories: List[Dict],
        stores: List[Dict],
        costs: List[Dict],
    ) -> List[Dict]:
        issues = []
        if not factories:
            issues.append({
                "id": "no_factories", "severity": "CRITICAL",
                "title": "工場データなし", "message": "factories が空です。",
                "relatedContainerIds": [],
            })
        if not stores:
            issues.append({
                "id": "no_stores", "severity": "CRITICAL",
                "title": "店舗データなし", "message": "stores が空です。",
                "relatedContainerIds": [],
            })
        total_supply = sum(float(f.get("supply", 0)) for f in factories)
        total_demand = sum(float(s.get("demand", 0)) for s in stores)
        if factories and stores and total_supply < total_demand - 1e-6:
            # supply_shortage: 詳細情報（供給量・需要量の数値を含む）
            issues.append({
                "id": "supply_shortage", "severity": "CRITICAL",
                "title": "総供給量不足",
                "message": (
                    f"総供給量 {total_supply:.1f} が総需要量 {total_demand:.1f} を下回っています。"
                    "需要を満たす輸送計画は存在しません。"
                ),
                "relatedContainerIds": [],
            })
            # solve_failed: Frontend（InfeasibleView.tsx 等）が id='solve_failed' を
            # 参照してInfeasible画面の表示可否を判定するため、必ず含める。
            issues.append({
                "id":       "solve_failed",
                "severity": "CRITICAL",
                "category": "INFEASIBLE",
                "title":    "輸送計画の実行可能解なし",
                "message":  (
                    f"総供給量 {total_supply:.1f} が総需要量 {total_demand:.1f} を下回っているため、"
                    "全店舗の需要を満たす輸送計画は存在しません。"
                ),
                "relatedContainerIds": [],
            })
        return issues

    def _build_and_solve(
        self,
        factories: List[Dict],
        stores: List[Dict],
        costs: List[Dict],
        config: Dict,
    ) -> Tuple[Optional[List[Dict]], float, List[Dict]]:
        from docplex.mp.model import Model

        cost_map: Dict[Tuple[str, str], float] = {}
        for c in costs:
            key = (str(c["factory_id"]), str(c["store_id"]))
            cost_map[key] = float(c.get("cost_per_unit", 0.0))

        mdl = Model(name="transport_cost_minimizer")
        mdl.parameters.timelimit = float(config.get("time_limit_sec", 60))

        # x[i][j]: 工場iから店舗jへの輸送量（連続変数、非負）
        x: Dict[Tuple[str, str], Any] = {}
        for f in factories:
            fid = str(f["id"])
            for s in stores:
                sid = str(s["id"])
                x[(fid, sid)] = mdl.continuous_var(
                    lb=0.0, name=f"x_{fid}_{sid}"
                )

        # 供給制約: Sum_j x[i][j] <= supply_i
        for f in factories:
            fid = str(f["id"])
            supply = float(f.get("supply", 0))
            mdl.add_constraint(
                mdl.sum(x[(fid, str(s["id"]))] for s in stores) <= supply,
                ctname=f"supply_{fid}",
            )

        # 需要制約: Sum_i x[i][j] == demand_j
        for s in stores:
            sid = str(s["id"])
            demand = float(s.get("demand", 0))
            mdl.add_constraint(
                mdl.sum(x[(str(f["id"]), sid)] for f in factories) == demand,
                ctname=f"demand_{sid}",
            )

        # 目的関数: 総輸送コスト最小化
        objective = mdl.sum(
            cost_map.get((str(f["id"]), str(s["id"])), 0.0) * x[(str(f["id"]), str(s["id"]))]
            for f in factories
            for s in stores
        )
        mdl.minimize(objective)

        sol = solve_with_ce_fallback(mdl, log_output=False)
        if sol is None:
            return None, 0.0, []

        # 2026-08-30追加: 解チェッカー（DESIGN_2026-07-21 9節、StoreSiteと同一パターン）。
        # docplex.mp（MIP）はSolveSolution.is_valid_solution()で「変数の型・範囲と
        # 全制約の充足」を一括判定できるネイティブ機能を持つ。既存の解オブジェクトへの
        # 評価であり追加探索を伴わないためコストは軽い（常に同期実行）。
        mip_check_issues: List[Dict] = []
        try:
            if not sol.is_valid_solution(tolerance=1e-6):
                from solvers.base.solution_checker import solver_bug_issue
                mip_check_issues.append(solver_bug_issue(
                    "mip_solution_invalid",
                    "MIPソルバー解の整合性エラー",
                    "docplex.mp の is_valid_solution() が、返ってきた解の制約充足性に"
                    "疑義があることを検出しました。解の抽出・変換コードにバグがある"
                    "可能性があります。",
                ))
        except Exception as e:
            logger.warning(f"[TransportCostMinimizer] is_valid_solution() 呼び出しに失敗（検証スキップ）: {e}")

        flows: List[Dict] = []
        total_cost = 0.0
        for f in factories:
            fid = str(f["id"])
            for s in stores:
                sid = str(s["id"])
                amount = sol.get_value(x[(fid, sid)])
                if amount is None:
                    amount = 0.0
                amount = max(0.0, float(amount))
                unit_cost = cost_map.get((fid, sid), 0.0)
                line_cost = unit_cost * amount
                total_cost += line_cost
                flows.append({
                    "factory_id":   fid,
                    "factory_name": f.get("name", fid),
                    "store_id":     sid,
                    "store_name":   s.get("name", sid),
                    "amount":       round(amount, 4),
                    "unit_cost":    unit_cost,
                    "line_cost":    round(line_cost, 2),
                })

        return flows, round(total_cost, 2), mip_check_issues

    def _detect_issues(
        self,
        factories: List[Dict],
        stores: List[Dict],
        costs: List[Dict],
        flows: List[Dict],
    ) -> List[Dict]:
        issues: List[Dict] = []

        # 全件未割当異常検知
        from solvers.base.issue_rules import build_full_unassignment_issue
        assigned = [fl for fl in flows if fl["amount"] > 1e-6]
        anomaly = build_full_unassignment_issue(
            assigned_count=len(assigned),
            total_count=len(factories) * len(stores),
            entity_label="輸送ルート",
            extra_hint="sol.get_value() での解抽出処理",
        )
        if anomaly:
            issues.append(anomaly)

        # 解チェッカー（DESIGN_2026-07-21、2026-08-30カタログ化）。需要未充足
        # （demand_unmet）・供給上限超過（supply_exceeded）はいずれもMIPモデル側の
        # ハード制約だが、返ってきたflowsから独立に再計算して突き合わせる。
        # いずれもO(n)（工場数・店舗数に比例）のため常に同期実行。
        from solvers.base.issue_rules import run_issue_rules, build_transport_cost_minimizer_contexts
        issue_statuses = self.dsl.get("issue_statuses", {})
        checker_ctxs = build_transport_cost_minimizer_contexts(factories, stores, flows)
        issues.extend(run_issue_rules(
            domain="TransportCostMinimizer", contexts=checker_ctxs, issue_statuses=issue_statuses,
        ))

        return issues

    def _make_result(
        self,
        feasible: bool,
        flows: List[Dict],
        total_cost: float,
        issues: List[Dict],
    ) -> Dict[str, Any]:
        return {
            "status":   "ok",
            "feasible": feasible,
            "metadata": {"problem_class": "TransportCostMinimizer"},
            "flows":    flows,
            "total_cost": total_cost,
            "solutions": [
                {
                    "name":     "Plan A",
                    "feasible": feasible,
                    "flows":    flows,
                    "kpi": {"total_cost": total_cost},
                }
            ] if feasible else [],
            "issues": issues,
            "_solver_version": "transport_cost_minimizer_v1.0",
        }
