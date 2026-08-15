"""
SteelMillSlabDesignSolver — CSPLib prob038 Steel Mill Slab Design

各注文（色×重量）を複数のスラブに割り付ける。
制約:
  - 各スラブの積載重量合計 <= スラブの固定最大重量（全スラブ共通・単一規格）
  - 各スラブに混在する色数 <= 2
目的（二段階、lexicographic）:
  1. 廃棄重量（使用スラブの容量合計 - 注文重量合計）を最小化
  2. 同じ廃棄重量を維持する範囲でスラブ使用個数を最小化

CP Optimizer (docplex.cp) を使用。
"""

from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)


class SteelMillSlabDesignSolver:
    def __init__(self, solver_input: dict) -> None:
        self.dsl = solver_input

    def solve(self) -> dict:
        try:
            config: Dict = self.dsl.get("config", {})
            from solvers.base.engine_select import get_solver_engine, CPSAT
            engine = get_solver_engine(config)
            if engine == CPSAT:
                return self._solve_with_cpsat()
            return self._build_and_solve()
        except Exception as e:
            from solvers.base.ce_limit_lns import is_ce_limit_exceeded, CeLimitExceededError
            if is_ce_limit_exceeded(e):
                raise CeLimitExceededError(str(e)) from e
            logger.error(f"[SteelMillSlabDesign] 予期しない例外: {e}", exc_info=True)
            from solvers.base.solver_error_result import build_solver_crash_issue, solver_crash_extra_fields
            result = {
                "status": "ok",
                "feasible": False,
                "metadata": {"problem_class": "SteelMillSlabDesign"},
                "solutions": [],
                "issues": [build_solver_crash_issue(e)],
                "_solver_version": "steel_mill_slab_design_v1.0",
            }
            result.update(solver_crash_extra_fields(e))
            return result

    def _build_and_solve(self) -> dict:
        from docplex.cp.model import CpoModel

        orders: List[Dict] = self.dsl.get("orders", [])
        # 単一規格: slab_capacity（スカラー）を優先し、
        # 後方互換のため slab_capacities リストの先頭値もフォールバックとして受け付ける
        slab_capacity: Optional[int] = self.dsl.get("slab_capacity", None)
        if slab_capacity is None:
            caps = self.dsl.get("slab_capacities", [])
            if caps:
                slab_capacity = int(caps[0])
        config: Dict = self.dsl.get("config", {})
        issue_statuses: Dict = self.dsl.get("issue_statuses", {})

        time_limit: int = int(config.get("time_limit_sec", 30))
        max_slabs: int = int(config.get("max_slabs", len(orders)))

        if not orders:
            return self._make_result(
                feasible=False,
                assignments=[],
                slabs_used=[],
                issues=[{
                    "id": "no_orders",
                    "severity": "CRITICAL",
                    "title": "注文が存在しません",
                    "message": "割り付ける注文が1件もありません。",
                    "relatedContainerIds": [],
                }],
                kpi={},
            )

        if slab_capacity is None or slab_capacity <= 0:
            return self._make_result(
                feasible=False,
                assignments=[],
                slabs_used=[],
                issues=[{
                    "id": "no_slab_capacity",
                    "severity": "CRITICAL",
                    "title": "スラブ容量が定義されていません",
                    "message": "slab_capacity に正の整数を指定してください。",
                    "relatedContainerIds": [],
                }],
                kpi={},
            )

        n_orders = len(orders)
        n_slabs = min(max_slabs, n_orders)

        # 全注文の色セットを収集
        all_colors = sorted({c for o in orders for c in o.get("colors", [])})
        color_index = {c: i for i, c in enumerate(all_colors)}
        n_colors = len(all_colors)

        total_order_weight = sum(int(o.get("weight", 0)) for o in orders)

        mdl = CpoModel(name="SteelMillSlabDesign")

        # --- 決定変数 ---
        # assign[i][j]: 注文iをスラブjに割り当てる（0/1）
        # 1注文は必ず1スラブに完全に収める（分割不可）
        assign = [
            [mdl.binary_var(name=f"assign_{i}_{j}") for j in range(n_slabs)]
            for i in range(n_orders)
        ]

        # slab_used[j]: スラブjを使用するか（0/1）
        slab_used = [
            mdl.binary_var(name=f"slab_used_{j}")
            for j in range(n_slabs)
        ]

        # color_used[j][c]: スラブjで色cが使われているか（0/1）
        color_used = [
            [mdl.binary_var(name=f"color_used_{j}_{c}") for c in range(n_colors)]
            for j in range(n_slabs)
        ]

        # --- 制約 ---

        # 1. 各注文は必ずいずれか1つのスラブに割り当てる（分割不可）
        for i in range(n_orders):
            mdl.add(mdl.sum(assign[i]) == 1)

        # 2. スラブjの積載重量 <= 固定容量（全スラブ共通・単一規格）
        for j in range(n_slabs):
            total_weight_j = mdl.sum(
                assign[i][j] * int(orders[i].get("weight", 0))
                for i in range(n_orders)
            )
            mdl.add(total_weight_j <= slab_capacity)

        # 3. 各スラブに混在する色数 <= 2
        for j in range(n_slabs):
            for c_idx in range(n_colors):
                # color_used[j][c] = OR_{i: 色cを持つ注文} assign[i][j]
                orders_with_color = [
                    i for i in range(n_orders)
                    if c_idx in [color_index[col] for col in orders[i].get("colors", [])]
                ]
                if orders_with_color:
                    mdl.add(color_used[j][c_idx] == mdl.max(
                        [assign[i][j] for i in orders_with_color]
                    ))
                else:
                    mdl.add(color_used[j][c_idx] == 0)
            mdl.add(mdl.sum(color_used[j]) <= 2)

        # 4. スラブjが使用されているか
        for j in range(n_slabs):
            mdl.add(slab_used[j] == mdl.max([assign[i][j] for i in range(n_orders)]))

        # 5. 対称性削減: slab_used[j] >= slab_used[j+1]（使用スラブを前詰め）
        for j in range(n_slabs - 1):
            mdl.add(slab_used[j] >= slab_used[j + 1])

        # --- 目的関数（二段階 lexicographic） ---
        # 廃棄重量 = Σ_j (slab_used[j] * slab_capacity) - total_order_weight
        # スラブ個数 = Σ_j slab_used[j]
        slab_count = mdl.sum(slab_used)
        waste_weight = slab_count * slab_capacity - total_order_weight

        mdl.add(mdl.minimize_static_lex([waste_weight, slab_count]))

        # --- ソルブ ---
        msol = mdl.solve(TimeLimit=time_limit, LogVerbosity="Quiet")

        if msol is None or not msol:
            return self._make_result(
                feasible=False,
                assignments=[],
                slabs_used=[],
                issues=[{
                    "id": "solve_failed",
                    "severity": "CRITICAL",
                    "title": "最適解が見つかりませんでした",
                    "message": "制約を満たすスラブ割り付けが見つかりませんでした。注文数・規格重量・スラブ数の設定を見直してください。",
                    "relatedContainerIds": [],
                }],
                kpi={},
            )

        # --- 解抽出 ---
        assignments_out: List[Dict] = []
        slabs_used_out: List[Dict] = []

        for j in range(n_slabs):
            used_val = msol.get_var_solution(slab_used[j])
            if used_val is None or not used_val.get_value():
                continue

            orders_in_slab: List[Dict] = []
            slab_weight = 0
            slab_colors = set()

            for i in range(n_orders):
                a_sol = msol.get_var_solution(assign[i][j])
                if a_sol is None or not a_sol.get_value():
                    continue
                o = orders[i]
                assignments_out.append({
                    "order_id": str(o.get("id", i)),
                    "order_name": o.get("name", f"注文{i+1}"),
                    "customer": o.get("customer", ""),
                    "slab_index": j,
                    "weight": int(o.get("weight", 0)),
                    "colors": o.get("colors", []),
                })
                orders_in_slab.append(o)
                slab_weight += int(o.get("weight", 0))
                slab_colors.update(o.get("colors", []))

            waste = slab_capacity - slab_weight
            slabs_used_out.append({
                "slab_index": j,
                "capacity": slab_capacity,
                "used_weight": slab_weight,
                "waste_weight": waste,
                "colors": sorted(slab_colors),
                "order_count": len(orders_in_slab),
            })

        total_waste = sum(s["waste_weight"] for s in slabs_used_out)
        issues = self._detect_issues(
            assignments_out, slabs_used_out, orders, issue_statuses
        )

        kpi = {
            "total_slabs_used": len(slabs_used_out),
            "total_waste_weight": total_waste,
            "total_order_weight": total_order_weight,
            "waste_rate": round(total_waste / (total_order_weight + total_waste), 4) if (total_order_weight + total_waste) > 0 else 0.0,
        }

        return self._make_result(
            feasible=True,
            assignments=assignments_out,
            slabs_used=slabs_used_out,
            issues=issues,
            kpi=kpi,
        )

    def _solve_with_cpsat(self) -> dict:
        """CP-SAT(CPMpy)版。_build_and_solve()と同一の制約セット・目的関数を実装する。"""
        import cpmpy as cp
        from solvers.base.cpmpy_lex_minimize import solve_lexicographic
        from solvers.base.engine_select import cpmpy_optimality_metadata

        orders: List[Dict] = self.dsl.get("orders", [])
        slab_capacity: Optional[int] = self.dsl.get("slab_capacity", None)
        if slab_capacity is None:
            caps = self.dsl.get("slab_capacities", [])
            if caps:
                slab_capacity = int(caps[0])
        config: Dict = self.dsl.get("config", {})
        issue_statuses: Dict = self.dsl.get("issue_statuses", {})

        time_limit: int = int(config.get("time_limit_sec", 30))
        max_slabs: int = int(config.get("max_slabs", len(orders)))

        if not orders:
            return self._make_result(
                feasible=False, assignments=[], slabs_used=[],
                issues=[{
                    "id": "no_orders", "severity": "CRITICAL",
                    "title": "注文が存在しません",
                    "message": "割り付ける注文が1件もありません。",
                    "relatedContainerIds": [],
                }],
                kpi={},
            )

        if slab_capacity is None or slab_capacity <= 0:
            return self._make_result(
                feasible=False, assignments=[], slabs_used=[],
                issues=[{
                    "id": "no_slab_capacity", "severity": "CRITICAL",
                    "title": "スラブ容量が定義されていません",
                    "message": "slab_capacity に正の整数を指定してください。",
                    "relatedContainerIds": [],
                }],
                kpi={},
            )

        n_orders = len(orders)
        n_slabs = min(max_slabs, n_orders)

        all_colors = sorted({c for o in orders for c in o.get("colors", [])})
        color_index = {c: i for i, c in enumerate(all_colors)}
        n_colors = len(all_colors)

        total_order_weight = sum(int(o.get("weight", 0)) for o in orders)

        m = cp.Model()

        assign = [
            [cp.boolvar(name=f"assign_{i}_{j}") for j in range(n_slabs)]
            for i in range(n_orders)
        ]
        slab_used = [cp.boolvar(name=f"slab_used_{j}") for j in range(n_slabs)]
        color_used = [
            [cp.boolvar(name=f"color_used_{j}_{c}") for c in range(n_colors)]
            for j in range(n_slabs)
        ]

        for i in range(n_orders):
            m += (cp.sum(assign[i]) == 1)

        for j in range(n_slabs):
            total_weight_j = cp.sum([
                assign[i][j] * int(orders[i].get("weight", 0))
                for i in range(n_orders)
            ])
            m += (total_weight_j <= slab_capacity)

        for j in range(n_slabs):
            for c_idx in range(n_colors):
                orders_with_color = [
                    i for i in range(n_orders)
                    if c_idx in [color_index[col] for col in orders[i].get("colors", [])]
                ]
                if orders_with_color:
                    m += (color_used[j][c_idx] == cp.max([assign[i][j] for i in orders_with_color]))
                else:
                    m += (color_used[j][c_idx] == 0)
            m += (cp.sum(color_used[j]) <= 2)

        for j in range(n_slabs):
            m += (slab_used[j] == cp.max([assign[i][j] for i in range(n_orders)]))

        for j in range(n_slabs - 1):
            m += (slab_used[j] >= slab_used[j + 1])

        slab_count = cp.sum(slab_used)
        waste_weight = slab_count * slab_capacity - total_order_weight

        solved, achieved = solve_lexicographic(
            m, [waste_weight, slab_count],
            solver="ortools", time_limit=time_limit,
        )

        if not solved:
            return self._make_result(
                feasible=False, assignments=[], slabs_used=[],
                issues=[{
                    "id": "solve_failed", "severity": "CRITICAL",
                    "title": "最適解が見つかりませんでした",
                    "message": "制約を満たすスラブ割り付けが見つかりませんでした。注文数・規格重量・スラブ数の設定を見直してください。",
                    "relatedContainerIds": [],
                }],
                kpi={},
            )

        assignments_out: List[Dict] = []
        slabs_used_out: List[Dict] = []

        for j in range(n_slabs):
            if not slab_used[j].value():
                continue

            orders_in_slab: List[Dict] = []
            slab_weight = 0
            slab_colors = set()

            for i in range(n_orders):
                if not assign[i][j].value():
                    continue
                o = orders[i]
                assignments_out.append({
                    "order_id": str(o.get("id", i)),
                    "order_name": o.get("name", f"注文{i+1}"),
                    "customer": o.get("customer", ""),
                    "slab_index": j,
                    "weight": int(o.get("weight", 0)),
                    "colors": o.get("colors", []),
                })
                orders_in_slab.append(o)
                slab_weight += int(o.get("weight", 0))
                slab_colors.update(o.get("colors", []))

            waste = slab_capacity - slab_weight
            slabs_used_out.append({
                "slab_index": j,
                "capacity": slab_capacity,
                "used_weight": slab_weight,
                "waste_weight": waste,
                "colors": sorted(slab_colors),
                "order_count": len(orders_in_slab),
            })

        total_waste = sum(s["waste_weight"] for s in slabs_used_out)
        issues = self._detect_issues(
            assignments_out, slabs_used_out, orders, issue_statuses
        )

        kpi = {
            "total_slabs_used": len(slabs_used_out),
            "total_waste_weight": total_waste,
            "total_order_weight": total_order_weight,
            "waste_rate": round(total_waste / (total_order_weight + total_waste), 4) if (total_order_weight + total_waste) > 0 else 0.0,
        }

        return self._make_result(
            feasible=True,
            assignments=assignments_out,
            slabs_used=slabs_used_out,
            issues=issues,
            kpi=kpi,
        )

    def _detect_issues(
        self,
        assignments: List[Dict],
        slabs_used: List[Dict],
        orders: List[Dict],
        issue_statuses: Dict,
    ) -> List[Dict]:
        issues: List[Dict] = []

        from solvers.base.issue_rules import build_full_unassignment_issue
        anomaly = build_full_unassignment_issue(
            assigned_count=len(assignments),
            total_count=len(orders),
            entity_label="注文",
            extra_hint="assign変数のget_var_solution()での解抽出処理",
        )
        if anomaly:
            issues.append(anomaly)
            return issues

        # 廃棄重量が大きいスラブへの警告
        for s in slabs_used:
            iid = f"high_waste_slab_{s['slab_index']}"
            if issue_statuses.get(iid) == "ACCEPTED":
                continue
            waste_rate = s["waste_weight"] / s["capacity"] if s["capacity"] > 0 else 0
            if waste_rate > 0.5:
                issues.append({
                    "id": iid,
                    "severity": "WARNING",
                    "title": f"スラブ{s['slab_index']+1}: 廃棄率が高い ({round(waste_rate*100)}%)",
                    "message": (
                        f"スラブ{s['slab_index']+1}（規格{s['capacity']}t）の廃棄重量は"
                        f"{s['waste_weight']}t（廃棄率{round(waste_rate*100)}%）です。"
                        f"注文の組み合わせを見直してください。"
                    ),
                    "relatedContainerIds": [],
                })

        return issues

    @staticmethod
    def _make_result(
        feasible: bool,
        assignments: List[Dict],
        slabs_used: List[Dict],
        issues: List[Dict],
        kpi: Dict,
    ) -> dict:
        solutions = []
        if feasible:
            solutions.append({
                "name": "Plan A",
                "label": "最適割り付け",
                "feasible": True,
                "assignments": assignments,
                "slabs_used": slabs_used,
                "kpi": kpi,
            })
        return {
            "status": "ok",
            "feasible": feasible,
            "metadata": {"problem_class": "SteelMillSlabDesign"},
            "solutions": solutions,
            "issues": issues,
            "_solver_version": "steel_mill_slab_design_v1.0",
        }
