"""
Backend/solvers/capital_project_selector_solver.py

CapitalProjectSelectorSolver — 設備投資案件選定問題（CSPLib prob133: 0-1ナップサック）

solver_input keys:
  - problem_class: str ("CapitalProjectSelector")
  - projects: List[Dict]  # id, name, value, weight, category, tags
  - budget: float         # 予算上限
  - config: Dict          # time_limit_sec, max_projects
  - issue_statuses: Dict
  - meta: Dict

returns:
  - status: "ok" | "infeasible"
  - feasible: bool
  - metadata: Dict
  - solutions: List[Dict]
  - issues: List[Dict]
  - _solver_version: str
"""

import logging
from typing import Any, Dict, List, Optional

from solvers.base.ce_limit_mip_fallback import solve_with_ce_fallback
from solvers.base.issue_rules import build_full_unassignment_issue
from solvers.base.solver_error_result import build_solver_crash_issue, solver_crash_extra_fields

logger = logging.getLogger(__name__)


class CapitalProjectSelectorSolver:
    """設備投資案件選定問題 (0-1ナップサック, docplex.mp) ソルバー。"""

    def __init__(self, solver_input: dict) -> None:
        self.dsl = solver_input

    def solve(self) -> dict:
        projects = self.dsl.get("projects", [])
        budget = float(self.dsl.get("budget", 0.0))
        config = self.dsl.get("config", {})
        issue_statuses = self.dsl.get("issue_statuses", {})
        meta = self.dsl.get("meta", {})

        # バリデーション
        validation_issues = self._validate(projects, budget)
        if validation_issues:
            return self._make_result(
                feasible=False,
                selected=[],
                total_value=0.0,
                total_weight=0.0,
                issues=validation_issues,
                solve_time=0.0,
                meta=meta,
            )

        try:
            selected, total_value, total_weight, solve_time, is_optimal = \
                self._build_and_solve(projects, budget, config)
        except Exception as e:
            logger.error(f"[CapitalProjectSelector] mdl.solve() 例外: {e}", exc_info=True)
            result = self._make_result(
                feasible=False,
                selected=[],
                total_value=0.0,
                total_weight=0.0,
                issues=[build_solver_crash_issue(e)],
                solve_time=0.0,
                meta=meta,
            )
            result.update(solver_crash_extra_fields(e))
            return result

        if selected is None:
            return self._make_result(
                feasible=False,
                selected=[],
                total_value=0.0,
                total_weight=0.0,
                issues=[{
                    "id": "solve_failed",
                    "severity": "CRITICAL",
                    "category": "INFEASIBLE",
                    "title": "予算内に実施可能な案件が見つかりませんでした",
                    "message": (
                        "設定された予算上限内で選定可能な案件の組み合わせが存在しません。"
                        "予算を増やすか、案件の投資額を見直してください。"
                    ),
                    "relatedContainerIds": [],
                }],
                solve_time=solve_time,
                meta=meta,
            )

        issues = self._detect_issues(projects, budget, selected, total_weight, issue_statuses)
        return self._make_result(
            feasible=True,
            selected=selected,
            total_value=total_value,
            total_weight=total_weight,
            issues=issues,
            solve_time=solve_time,
            meta=meta,
            is_optimal=is_optimal,
        )

    # ------------------------------------------------------------------
    # バリデーション
    # ------------------------------------------------------------------

    def _validate(self, projects: List[Dict], budget: float) -> List[Dict]:
        issues = []
        if not projects:
            issues.append({
                "id": "solve_failed",
                "severity": "CRITICAL",
                "title": "案件データがありません",
                "message": "projects が空です。少なくとも1件の案件が必要です。",
                "relatedContainerIds": [],
            })
        if budget <= 0:
            issues.append({
                "id": "solve_failed",
                "severity": "CRITICAL",
                "title": "予算が設定されていません",
                "message": f"budget={budget} は有効な予算上限ではありません（正の値を設定してください）。",
                "relatedContainerIds": [],
            })
        return issues

    # ------------------------------------------------------------------
    # MIPモデル構築・求解
    # ------------------------------------------------------------------

    def _build_and_solve(
        self,
        projects: List[Dict],
        budget: float,
        config: Dict,
    ):
        """
        docplex.mp で 0-1ナップサック問題を定式化して解く。

        制約は「実施案件の投資額合計が予算上限を超えない」の1本のみ（CSPLib prob133 基本形）。
        選定件数は0件から全件まで完全に自由で、予算内で期待効果合計を最大化した結果として決まる。

        Returns:
            (selected, total_value, total_weight, solve_time, is_optimal)
            解なし時は selected=None
        """
        from docplex.mp.model import Model

        time_limit = int(config.get("time_limit_sec", 30))
        max_projects = config.get("max_projects")

        mdl = Model(name="CapitalProjectSelector")
        mdl.parameters.timelimit = time_limit

        n = len(projects)
        # 0/1 binary変数: x[i] = 1 なら案件iを選択
        x = [mdl.binary_var(name=f"x_{i}") for i in range(n)]

        # 制約: 投資額合計 ≦ 予算上限（唯一のハード制約）
        mdl.add_constraint(
            mdl.sum(float(projects[i].get("weight", 0.0)) * x[i] for i in range(n)) <= budget,
            ctname="budget_constraint"
        )

        # 最大選択件数（max_projects）— 任意
        if max_projects is not None:
            mdl.add_constraint(
                mdl.sum(x[i] for i in range(n)) <= int(max_projects),
                ctname="max_projects_constraint"
            )

        # 目的関数: 期待効果（value）の合計を最大化
        # ヒアリング目的関数 項1: Σ(value[i] * x[i]) の最大化
        mdl.maximize(
            mdl.sum(float(projects[i].get("value", 0.0)) * x[i] for i in range(n))
        )

        sol = solve_with_ce_fallback(mdl, log_output=False)

        if sol is None:
            return None, 0.0, 0.0, 0.0, False

        is_optimal = mdl.solve_status is not None and "optimal" in str(mdl.solve_status).lower()

        selected = []
        total_value = 0.0
        total_weight = 0.0
        for i, proj in enumerate(projects):
            val = sol.get_value(x[i])
            if val is not None and val > 0.5:
                # category / tags をそのまま引き継いで selected_projects に含める
                selected.append({
                    "id":       proj.get("id"),
                    "name":     proj.get("name"),
                    "value":    proj.get("value"),
                    "weight":   proj.get("weight"),
                    "category": proj.get("category", ""),
                    "tags":     proj.get("tags", []),
                })
                total_value += float(proj.get("value", 0.0))
                total_weight += float(proj.get("weight", 0.0))

        solve_time = float(getattr(mdl, "_solve_details", None) and
                           mdl._solve_details.time or 0.0)

        return selected, total_value, total_weight, solve_time, is_optimal

    # ------------------------------------------------------------------
    # イシュー検知
    # ------------------------------------------------------------------

    def _detect_issues(
        self,
        projects: List[Dict],
        budget: float,
        selected: List[Dict],
        total_weight: float,
        issue_statuses: Dict,
    ) -> List[Dict]:
        issues = []

        # 全件未割当異常検知（解抽出バグのセーフティネット）
        anomaly = build_full_unassignment_issue(
            assigned_count=len(selected),
            total_count=len(projects),
            entity_label="投資案件",
            extra_hint="sol.get_value(x[i]) での解抽出処理",
        )
        if anomaly:
            issues.insert(0, anomaly)
            return issues

        # 予算消化率が極端に低い場合（情報提供）
        if budget > 0 and total_weight / budget < 0.3 and len(selected) > 0:
            iid = "low_budget_utilization"
            if issue_statuses.get(iid) != "ACCEPTED":
                utilization = round(total_weight / budget * 100, 1)
                issues.append({
                    "id": iid,
                    "severity": "INFO",
                    "title": "予算消化率が低い状態です",
                    "message": (
                        f"選定案件の投資額合計が予算の {utilization}% にとどまっています。"
                        "追加の候補案件を検討するか、予算を削減することを検討してください。"
                    ),
                    "relatedContainerIds": [],
                })

        return issues

    # ------------------------------------------------------------------
    # 結果整形
    # ------------------------------------------------------------------

    def _make_result(
        self,
        feasible: bool,
        selected: List[Dict],
        total_value: float,
        total_weight: float,
        issues: List[Dict],
        solve_time: float,
        meta: Dict,
        is_optimal: bool = False,
    ) -> Dict[str, Any]:
        budget = float(self.dsl.get("budget", 0.0))
        budget_utilization = round(total_weight / budget, 4) if budget > 0 else 0.0

        return {
            "status": "ok",
            "feasible": feasible,
            "metadata": {
                "problem_class": "CapitalProjectSelector",
                "instance_name": meta.get("instance_name", ""),
                "is_optimal": is_optimal,
            },
            "solutions": [{
                "name": "Plan A",
                "label": "最適投資ポートフォリオ" if feasible else "解なし",
                "feasible": feasible,
                "selected_projects": selected,
                "kpi": {
                    "total_value": round(total_value, 2),
                    "total_weight": round(total_weight, 2),
                    "budget": budget,
                    "budget_utilization": budget_utilization,
                    "selected_count": len(selected),
                    "solve_time": round(solve_time, 3),
                    "is_optimal": is_optimal,
                },
            }] if feasible else [],
            "issues": issues,
            "_solver_version": "capital_project_selector_v1.0",
        }
