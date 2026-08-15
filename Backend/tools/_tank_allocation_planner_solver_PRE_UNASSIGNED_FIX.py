"""
Backend/solvers/tank_allocation_planner_solver.py

TankAllocationPlannerSolver — 液体薬品タンク割当最適化ソルバー
================================================================

solver_input keys:
  - problem_class: str
  - meta: dict
  - lots: list[dict]          # 受注ロット一覧
  - tanks: list[dict]         # タンクローリー一覧
  - incompatible_pairs: list[tuple[str,str]]  # 混載禁止分類ペア
  - config: dict
  - issue_statuses: dict

solutions[0] keys:
  - feasible: bool
  - tank_assignments: list[dict]   # タンクごとの割当結果
  - unassigned_lots: list[dict]    # 割り当てられなかったロット
  - customer_dispersion: list[dict]# 得意先ごとのタンク分散
  - kpi: dict
  - metrics: dict

アルゴリズム: IBM CPLEX CP Optimizer (docplex.cp)
  - 各ロットのタンク割当を整数変数 tank_var[lot_id] で表現
  - 容量制約: タンクごとの割当ロット数量合計 <= capacity
  - 相性制約: 同じタンクに割り当てられた任意の2ロットが禁止ペアに該当しないこと
  - 目的関数: lexicographic
      1位: 使用タンク台数の最小化
      2位: 得意先ごとのタンク分散数の合計最小化
"""

import logging
from typing import Any, Dict, List, Optional, Set, Tuple

logger = logging.getLogger(__name__)

# 禁止分類ペア（デフォルト）
DEFAULT_INCOMPATIBLE_PAIRS = [
    ("酸性", "アルカリ性"),
    ("酸化性", "可燃性"),
]

SOLVE_TIME_SEC = 30


class TankAllocationPlannerSolver:
    def __init__(self, solver_input: dict) -> None:
        self.dsl = solver_input

    def solve(self) -> dict:
        lots = self.dsl.get("lots", [])
        tanks = self.dsl.get("tanks", [])
        incompatible_pairs = self.dsl.get("incompatible_pairs", DEFAULT_INCOMPATIBLE_PAIRS)
        config = self.dsl.get("config", {})
        issue_statuses = self.dsl.get("issue_statuses", {})

        # バリデーション
        validation_issues = self._validate(lots, tanks)
        if any(i["severity"] == "CRITICAL" for i in validation_issues):
            return self._make_result(
                feasible=False,
                tank_assignments=[],
                unassigned_lots=lots,
                customer_dispersion=[],
                kpi={},
                metrics={},
                issues=validation_issues,
            )

        try:
            from docplex.cp.model import CpoModel
            solution_data, issues = self._build_and_solve(lots, tanks, incompatible_pairs, config)
        except Exception as e:
            from solvers.base.ce_limit_lns import is_ce_limit_exceeded, CeLimitExceededError
            if is_ce_limit_exceeded(e):
                raise CeLimitExceededError(str(e)) from e
            logger.error(f"[TankAllocationPlanner] mdl.solve() 例外: {e}", exc_info=True)
            from solvers.base.solver_error_result import build_solver_crash_issue, solver_crash_extra_fields
            result = {
                "status": "ok",
                "feasible": False,
                "metadata": {"problem_class": "TankAllocationPlanner"},
                "solutions": [],
                "issues": [build_solver_crash_issue(e)],
                "_solver_version": "tank_allocation_planner_v1.0",
            }
            result.update(solver_crash_extra_fields(e))
            return result

        if solution_data is None:
            infeasible_issues = self._classify_infeasible(lots, tanks, incompatible_pairs)
            return self._make_result(
                feasible=False,
                tank_assignments=[],
                unassigned_lots=lots,
                customer_dispersion=[],
                kpi={},
                metrics={},
                issues=infeasible_issues,
            )

        detected_issues = self._detect_issues(
            solution_data["tank_assignments"],
            solution_data["unassigned_lots"],
            lots, tanks, incompatible_pairs, issue_statuses,
        )
        all_issues = issues + detected_issues
        from solvers.base.issue_rules import build_full_unassignment_issue
        anomaly = build_full_unassignment_issue(
            assigned_count=sum(len(ta["lots"]) for ta in solution_data["tank_assignments"]),
            total_count=len(lots),
            entity_label="受注ロット",
            extra_hint="get_var_solution()での解抽出処理",
        )
        if anomaly:
            all_issues.insert(0, anomaly)

        return self._make_result(
            feasible=True,
            tank_assignments=solution_data["tank_assignments"],
            unassigned_lots=solution_data["unassigned_lots"],
            customer_dispersion=solution_data["customer_dispersion"],
            kpi=solution_data["kpi"],
            metrics=solution_data["metrics"],
            issues=all_issues,
        )

    def _build_and_solve(
        self,
        lots: List[Dict],
        tanks: List[Dict],
        incompatible_pairs: List[Tuple[str, str]],
        config: Dict,
    ) -> Tuple[Optional[Dict], List[Dict]]:
        from docplex.cp.model import CpoModel

        n_lots = len(lots)
        n_tanks = len(tanks)
        solve_time = config.get("solve_time_sec", SOLVE_TIME_SEC)

        mdl = CpoModel(name="TankAllocationPlanner")

        # --- 決定変数: 各ロットが割り当てられるタンクのインデックス (0..n_tanks-1) ---
        # optional: 割り当て不可能な場合は -1 相当として扱うため、ダミータンク index n_tanks を用意
        # 実装: tank_var[i] in [0, n_tanks] (n_tanks = ダミー/未割当)
        tank_var = [
            mdl.integer_var(0, n_tanks, name=f"tank_{i}")
            for i in range(n_lots)
        ]

        # タンク使用フラグ: used_tank[j] = 1 if tank j が1件以上割り当てられている
        used_tank = [
            mdl.binary_var(name=f"used_{j}")
            for j in range(n_tanks)
        ]

        # --- 容量制約 ---
        for j, tank in enumerate(tanks):
            capacity = float(tank.get("capacity", 0))
            assigned_volume = mdl.sum(
                lots[i].get("volume", 0) * (tank_var[i] == j)
                for i in range(n_lots)
            )
            mdl.add(assigned_volume <= capacity)

            # used_tank[j]: このタンクに1件でも割り当てられていれば1
            any_assigned = mdl.sum((tank_var[i] == j) for i in range(n_lots))
            mdl.add(used_tank[j] == mdl.min(1, any_assigned))

        # --- 相性制約: 同タンクの任意2ロットが禁止ペアに当たらないこと ---
        # 禁止ペアを集合化（双方向）
        incompatible_set: Set[Tuple[str, str]] = set()
        for a, b in incompatible_pairs:
            incompatible_set.add((a, b))
            incompatible_set.add((b, a))

        for i in range(n_lots):
            for k in range(i + 1, n_lots):
                cat_i = lots[i].get("category", "")
                cat_k = lots[k].get("category", "")
                if (cat_i, cat_k) in incompatible_set:
                    # lot i と lot k は同じタンクに入れない
                    # tank_var[i] == tank_var[k] かつ どちらもダミーでない → 禁止
                    for j in range(n_tanks):
                        mdl.add(
                            mdl.logical_or([
                                tank_var[i] != j,
                                tank_var[k] != j,
                            ])
                        )

        # --- 得意先分散ペナルティ ---
        # 同じ得意先のロットが複数タンクに分散している数
        # customer_lots: 得意先 -> lot indices
        from collections import defaultdict
        customer_lots: Dict[str, List[int]] = defaultdict(list)
        for i, lot in enumerate(lots):
            cname = lot.get("customer", "unknown")
            customer_lots[cname].append(i)

        # 得意先ごとの分散数 = 使用しているタンク種類数 - 1 (0が理想)
        dispersion_terms = []
        for cname, lot_indices in customer_lots.items():
            if len(lot_indices) <= 1:
                continue
            # その得意先のロット群が何台のタンクに分かれているか
            for j in range(n_tanks):
                # tank j に得意先cのロットが1件でも入るか
                has_lot = mdl.sum((tank_var[idx] == j) for idx in lot_indices)
                # 「このタンクを使っている」= min(1, has_lot)
                dispersion_terms.append(mdl.min(1, has_lot))
            # dispersion = Σ(タンク使用フラグ) - 1 だが、最小化したいのは合計タンク使用数
            # なので dispersion_terms の合計を最小化すれば良い

        # --- 目的関数: lexicographic ---
        # 目的1: 使用タンク台数の最小化
        total_used = mdl.sum(used_tank)
        # 目的2: 得意先ごとの分散数の合計最小化 (得意先ごとに「そのロット群が何台に分かれているか」の合計)
        total_dispersion = mdl.sum(dispersion_terms) if dispersion_terms else mdl.integer_var(0, 0)

        mdl.add(mdl.minimize_static_lex([total_used, total_dispersion]))

        # --- ソルブ ---
        msol = mdl.solve(TimeLimit=solve_time, LogVerbosity="Quiet")
        if msol is None or not msol:
            return None, []

        # --- 解抽出 ---
        tank_assignments: List[Dict] = []
        lot_to_tank: Dict[int, int] = {}

        for i in range(n_lots):
            var_sol = msol.get_var_solution(tank_var[i])
            if var_sol is None:
                continue
            t_idx = int(var_sol.get_value())
            if 0 <= t_idx < n_tanks:
                lot_to_tank[i] = t_idx

        # タンクごとに集約
        tank_map: Dict[int, Dict] = {}
        for i, t_idx in lot_to_tank.items():
            if t_idx not in tank_map:
                tank = tanks[t_idx]
                tank_map[t_idx] = {
                    "tank_id": str(tank.get("id", t_idx)),
                    "tank_name": tank.get("name", f"タンク{t_idx+1}"),
                    "capacity": float(tank.get("capacity", 0)),
                    "lots": [],
                    "total_volume": 0.0,
                    "categories": set(),
                }
            lot = lots[i]
            vol = float(lot.get("volume", 0))
            tank_map[t_idx]["lots"].append({
                "lot_id": str(lot.get("id", i)),
                "lot_name": lot.get("name", f"ロット{i+1}"),
                "volume": vol,
                "category": lot.get("category", ""),
                "customer": lot.get("customer", ""),
            })
            tank_map[t_idx]["total_volume"] += vol
            tank_map[t_idx]["categories"].add(lot.get("category", ""))

        for t_idx, ta in tank_map.items():
            ta["categories"] = sorted(ta["categories"])
            cap = ta["capacity"]
            ta["usage_rate"] = round(ta["total_volume"] / cap, 4) if cap > 0 else 0.0
            tank_assignments.append(ta)

        # 未割当ロット
        assigned_lot_indices = set(lot_to_tank.keys())
        unassigned_lots = [
            {
                "lot_id": str(lots[i].get("id", i)),
                "lot_name": lots[i].get("name", f"ロット{i+1}"),
                "volume": float(lots[i].get("volume", 0)),
                "category": lots[i].get("category", ""),
                "customer": lots[i].get("customer", ""),
                "reason": "タンクへの割り当て不可（容量不足または相性制約違反）",
            }
            for i in range(n_lots) if i not in assigned_lot_indices
        ]

        # 得意先分散集計
        customer_dispersion = self._build_customer_dispersion(tank_assignments)

        # KPI
        n_used = len(tank_map)
        total_vol = sum(ta["total_volume"] for ta in tank_assignments)
        kpi = {
            "used_tanks": n_used,
            "total_tanks": n_tanks,
            "usage_rate": round(n_used / n_tanks, 4) if n_tanks > 0 else 0.0,
            "assigned_lots": len(assigned_lot_indices),
            "unassigned_lots": len(unassigned_lots),
            "total_volume": round(total_vol, 2),
        }

        from solvers.base.solution_extraction import safe_objective_value
        obj_vals = msol.get_objective_values() or []
        metrics = {
            "objective_used_tanks": int(obj_vals[0]) if len(obj_vals) > 0 else n_used,
            "objective_dispersion": int(obj_vals[1]) if len(obj_vals) > 1 else 0,
            "solve_time": round(msol.get_solve_time() or 0.0, 3),
            "is_optimal": bool(msol.is_solution_optimal()),
        }

        return {
            "tank_assignments": tank_assignments,
            "unassigned_lots": unassigned_lots,
            "customer_dispersion": customer_dispersion,
            "kpi": kpi,
            "metrics": metrics,
        }, []

    def _build_customer_dispersion(self, tank_assignments: List[Dict]) -> List[Dict]:
        from collections import defaultdict
        customer_tanks: Dict[str, Set[str]] = defaultdict(set)
        for ta in tank_assignments:
            for lot in ta["lots"]:
                customer_tanks[lot["customer"]].add(ta["tank_id"])
        result = []
        for customer, tank_ids in sorted(customer_tanks.items()):
            result.append({
                "customer": customer,
                "tank_count": len(tank_ids),
                "tank_ids": sorted(tank_ids),
            })
        return result

    def _validate(self, lots: List[Dict], tanks: List[Dict]) -> List[Dict]:
        issues = []
        if not lots:
            issues.append({
                "id": "no_lots",
                "severity": "CRITICAL",
                "title": "受注ロットがありません",
                "message": "割り当てる受注ロットが1件もありません。",
                "relatedContainerIds": [],
            })
        if not tanks:
            issues.append({
                "id": "no_tanks",
                "severity": "CRITICAL",
                "title": "タンクがありません",
                "message": "割り当て先のタンクが1台もありません。",
                "relatedContainerIds": [],
            })
        if not lots or not tanks:
            return issues

        max_capacity = max(float(t.get("capacity", 0)) for t in tanks)
        for lot in lots:
            vol = float(lot.get("volume", 0))
            lot_id = str(lot.get("id", ""))
            lot_name = lot.get("name", lot_id)
            if vol > max_capacity:
                issues.append({
                    "id": f"lot_oversized_{lot_id}",
                    "severity": "CRITICAL",
                    "title": f"容量超過ロット: {lot_name}",
                    "message": (
                        f"ロット「{lot_name}」の数量 {vol:.1f} kL が、"
                        f"最大タンク容量 {max_capacity:.1f} kL を超えています。"
                        f"このロットはいかなるタンクにも積み込めません。"
                    ),
                    "relatedContainerIds": [],
                })
        return issues

    def _classify_infeasible(
        self,
        lots: List[Dict],
        tanks: List[Dict],
        incompatible_pairs: List[Tuple[str, str]],
    ) -> List[Dict]:
        issues = [{
            "id": "solve_failed",
            "severity": "CRITICAL",
            "title": "割り当て可能な解が見つかりませんでした",
            "message": (
                "受注ロットをすべてのルールを満たしてタンクに割り当てる解が見つかりませんでした。"
                "タンク容量の合計に対してロットの総量が多すぎるか、"
                "相性制約（酸性×アルカリ性、酸化性×可燃性の混載禁止）により"
                "物理的に収まらない組み合わせが存在する可能性があります。"
            ),
            "relatedContainerIds": [],
        }]
        # 追加診断: 総容量チェック
        total_demand = sum(float(lot.get("volume", 0)) for lot in lots)
        total_capacity = sum(float(t.get("capacity", 0)) for t in tanks)
        if total_demand > total_capacity:
            issues.append({
                "id": "total_capacity_exceeded",
                "severity": "CRITICAL",
                "title": "総容量超過",
                "message": (
                    f"ロットの総数量 {total_demand:.1f} kL が"
                    f"タンクの総容量 {total_capacity:.1f} kL を超えています。"
                ),
                "relatedContainerIds": [],
            })
        return issues

    def _detect_issues(
        self,
        tank_assignments: List[Dict],
        unassigned_lots: List[Dict],
        lots: List[Dict],
        tanks: List[Dict],
        incompatible_pairs: List[Tuple[str, str]],
        issue_statuses: Dict[str, str],
    ) -> List[Dict]:
        issues = []
        incompatible_set: Set[Tuple[str, str]] = set()
        for a, b in incompatible_pairs:
            incompatible_set.add((a, b))
            incompatible_set.add((b, a))

        for ta in tank_assignments:
            tank_lots = ta["lots"]
            # 相性チェック（解チェッカー）
            for i in range(len(tank_lots)):
                for k in range(i + 1, len(tank_lots)):
                    ci = tank_lots[i]["category"]
                    ck = tank_lots[k]["category"]
                    if (ci, ck) in incompatible_set:
                        iid = f"incompatible_{ta['tank_id']}_{tank_lots[i]['lot_id']}_{tank_lots[k]['lot_id']}"
                        if issue_statuses.get(iid) != "ACCEPTED":
                            issues.append({
                                "id": iid,
                                "severity": "CRITICAL",
                                "title": f"相性違反（解チェッカー）: {ta['tank_name']}",
                                "message": (
                                    f"タンク「{ta['tank_name']}」に「{ci}」と「{ck}」の"
                                    f"混載禁止ロットが割り当てられています（ソルバーの抽出バグの疑い）。"
                                ),
                                "relatedContainerIds": [],
                            })
            # 容量チェック（解チェッカー）
            cap = ta["capacity"]
            if ta["total_volume"] > cap + 1e-6:
                iid = f"capacity_violation_{ta['tank_id']}"
                if issue_statuses.get(iid) != "ACCEPTED":
                    from solvers.base.solution_checker import solver_bug_issue
                    issues.append(solver_bug_issue(
                        iid,
                        f"容量超過（解チェッカー）: {ta['tank_name']}",
                        f"タンク「{ta['tank_name']}」の積載量 {ta['total_volume']:.1f} kL が"
                        f"容量 {cap:.1f} kL を超えています（ソルバーの抽出バグの疑い）。",
                    ))

        for ul in unassigned_lots:
            iid = f"unassigned_lot_{ul['lot_id']}"
            if issue_statuses.get(iid) != "ACCEPTED":
                issues.append({
                    "id": iid,
                    "severity": "WARNING",
                    "title": f"未割当ロット: {ul['lot_name']}",
                    "message": ul.get("reason", "タンクに割り当てられませんでした。"),
                    "relatedContainerIds": [],
                })
        return issues

    def _make_result(
        self,
        feasible: bool,
        tank_assignments: List[Dict],
        unassigned_lots: List[Dict],
        customer_dispersion: List[Dict],
        kpi: Dict,
        metrics: Dict,
        issues: List[Dict],
    ) -> Dict:
        return {
            "status": "ok",
            "feasible": feasible,
            "metadata": {"problem_class": "TankAllocationPlanner"},
            "solutions": [
                {
                    "name": "Plan A",
                    "label": "タンク割当最適化",
                    "feasible": feasible,
                    "tank_assignments": tank_assignments,
                    "unassigned_lots": unassigned_lots,
                    "customer_dispersion": customer_dispersion,
                    "kpi": kpi,
                    "metrics": metrics,
                }
            ],
            "issues": issues,
            "_solver_version": "tank_allocation_planner_v1.0",
        }
