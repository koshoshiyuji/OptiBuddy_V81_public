"""
Backend/solvers/lot_sizing_scheduler_solver.py

LotSizingSchedulerSolver — 単一機械多品目ロットサイジング生産計画
====================================================================

# solver_input keys:
#   problem_class  : str  "LotSizingScheduler"
#   orders         : List[Dict]  各注文 {id, product_id, quantity, due_period, holding_cost_per_period}
#   periods        : List[int]   利用可能な期間番号リスト（例: [1,2,3,4,5]）
#   setup_costs    : List[Dict]  [{from_product, to_product, cost}]  製品切替コスト
#   config         : Dict        {time_limit_sec, capacity_per_period, allow_unassigned}
#   issue_statuses : Dict        {issue_id: "ACCEPTED"}

実装方式: IBM CP Optimizer (docplex.cp)
  - 決定変数: assigned_period[i] ∈ {eligible periods for order i}
      各注文 i を割り当てる期間番号を表す整数変数
  - 制約1: 各注文は納期以前のいずれか1期間に割り当てる（hard）
  - 制約2: 各期間に割り当てられる注文は1件まで（alldifferent / 期間ごとカウント制約）
  - 目的関数:
      Σ 保管コスト（due_period - assigned_period）× holding_cost_per_period × quantity
    + Σ 段取り替えコスト（実際に生産が行われた期間を時系列順に並べ、
        隣接する実生産期間ペアで製品が変わる場合にコストを加算。
        アイドル期間を飛び越えて実生産期間同士を比較する）

段取り替えコストの実装方針:
  - 各注文の assigned_period を取得し、期間番号でソートして「実生産順序」を得る。
  - CP Optimizer では実行時に変数値が確定するため、段取り替えコストは
    element() 式と conditional 式を組み合わせて定式化する。
  - 具体的には:
      * 注文を期間番号でソートするために、ランク変数 rank[i] を導入する。
        rank[i] = 注文 i が実生産順序で何番目か（0-indexed）
      * 隣接する実生産ペア (rank=k, rank=k+1) の製品が異なる場合にコストを加算する。
      * ただし CP Optimizer の docplex.cp では動的ソートが難しいため、
        実用的な定式化として以下を採用する:
        - 全注文ペア (i, j) について「i が j の直前の実生産である」フラグ変数を導入し、
          そのフラグが立つ場合に段取り替えコストを加算する。
        - 「i が j の直前の実生産」= assigned_period[i] < assigned_period[j]
          かつ 期間 (assigned_period[i], assigned_period[j]) の間に他の注文が存在しない
        - これは O(N^3) になるため、注文数が多い場合は近似として
          「assigned_period[i] + 1 == assigned_period[j]」ではなく
          「assigned_period[i] < assigned_period[j] かつ
           全 k≠i,j について NOT (assigned_period[i] < assigned_period[k] < assigned_period[j])」
          で表現する。

  注: 上記の全ペア定式化は注文数が増えると変数・制約数が爆発するため、
      実装では「注文を期間番号でソートした順序変数」を使う簡潔な方式を採用する。
      具体的には:
        - 各注文 i に対して「実生産順序インデックス」pos[i] を整数変数として導入
        - pos[i] ∈ [0, N-1]、全注文で alldifferent(pos)
        - pos[i] < pos[j] ⟺ assigned_period[i] < assigned_period[j]
          （同一期間は1件制約により発生しない）
        - 隣接ペア: pos[j] == pos[i] + 1 のとき、製品 i → 製品 j の段取り替えコスト

  最終実装: docplex.cp の integer_var + element + if_then を使い、
  「pos[j] == pos[i] + 1」かつ「product[i] != product[j]」のときコストを加算する。
"""

from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

_SOLVER_VERSION = "lot_sizing_scheduler_v2.0_cp"
_UNASSIGNED_PENALTY = 100_000


class LotSizingSchedulerSolver:
    def __init__(self, solver_input: dict) -> None:
        self.dsl = solver_input

    def solve(self) -> dict:
        orders         = self.dsl.get("orders", [])
        periods        = self.dsl.get("periods", [])
        setup_costs    = self.dsl.get("setup_costs", [])
        config         = self.dsl.get("config", {})
        issue_statuses = self.dsl.get("issue_statuses", {})

        # --- バリデーション ---
        if not orders:
            return self._make_result(
                feasible=False,
                assignments=[],
                issues=[{
                    "id": "solve_failed", "severity": "CRITICAL",
                    "title": "注文が存在しません",
                    "message": "割り当て対象の注文が0件です。DSLを確認してください。",
                    "relatedContainerIds": [],
                }],
                kpi={},
            )
        if not periods:
            return self._make_result(
                feasible=False,
                assignments=[],
                issues=[{
                    "id": "solve_failed", "severity": "CRITICAL",
                    "title": "期間が定義されていません",
                    "message": "periods リストが空です。DSLを確認してください。",
                    "relatedContainerIds": [],
                }],
                kpi={},
            )

        try:
            from solvers.base.engine_select import get_solver_engine, CPSAT
            engine = get_solver_engine(config)
            if engine == CPSAT:
                assignments, obj_val, solve_time = self._solve_with_cpsat(
                    orders, periods, setup_costs, config
                )
            else:
                assignments, obj_val, solve_time = self._solve_with_cpo(
                    orders, periods, setup_costs, config
                )
        except Exception as e:
            logger.error(f"[LotSizingScheduler] solve() 例外: {e}", exc_info=True)
            from solvers.base.solver_error_result import build_solver_crash_issue, solver_crash_extra_fields
            result = self._make_result(
                feasible=False,
                assignments=[],
                issues=[build_solver_crash_issue(e)],
                kpi={},
            )
            result.update(solver_crash_extra_fields(e))
            return result

        if assignments is None:
            return self._make_result(
                feasible=False,
                assignments=[],
                issues=[{
                    "id": "solve_failed", "severity": "CRITICAL",
                    "title": "実行可能解が見つかりませんでした",
                    "message": (
                        "与えられた制約（納期・1期間1件）を満たす生産計画が存在しません。"
                        "注文数に対して期間数が不足しているか、特定の納期に注文が集中しすぎている"
                        "可能性があります。期間数の追加や納期の見直しを検討してください。"
                    ),
                    "relatedContainerIds": [],
                }],
                kpi={},
            )

        from solvers.base.issue_rules import build_full_unassignment_issue
        issues = self._detect_issues(orders, periods, assignments, issue_statuses)

        # 全件未割当異常検知
        anomaly = build_full_unassignment_issue(
            assigned_count=len(assignments),
            total_count=len(orders),
            entity_label="注文",
            extra_hint="CPモデルの解抽出処理",
        )
        if anomaly:
            issues.insert(0, anomaly)

        kpi = self._build_kpi(orders, periods, assignments, obj_val, solve_time, config)

        return self._make_result(
            feasible=True,
            assignments=assignments,
            issues=issues,
            kpi=kpi,
        )

    def _solve_with_cpo(
        self,
        orders: List[Dict],
        periods: List[int],
        setup_costs: List[Dict],
        config: Dict,
    ):
        from docplex.cp.model import CpoModel
        import time as _time

        time_limit = float(config.get("time_limit_sec", 30.0))
        # capacity_per_period: 1期間あたりの最大割当件数上限（設定値として参照）
        # 現行実装では all_diff により1期間1件のhard制約を適用しているため、
        # capacity_per_period の実効値は常に1として扱う。
        capacity_per_period = int(config.get("capacity_per_period", 999_999))
        logger.debug(
            f"[LotSizingScheduler] capacity_per_period={capacity_per_period} "
            f"(現行実装では all_diff により1期間1件のhard制約を適用)"
        )
        allow_unassigned = bool(config.get("allow_unassigned", False))

        sorted_periods = sorted(periods)
        n_orders = len(orders)

        # setup_costマップ: (from_product, to_product) -> cost
        setup_cost_map: Dict[tuple, float] = {}
        for sc in setup_costs:
            key = (str(sc["from_product"]), str(sc["to_product"]))
            setup_cost_map[key] = float(sc.get("cost", 0.0))

        # 製品IDを整数インデックスにマッピング（CP Optimizer は整数演算が基本）
        products_list = sorted({str(o.get("product_id", o["id"])) for o in orders})
        prod_to_idx = {p: i for i, p in enumerate(products_list)}
        n_products = len(products_list)

        # 各注文の製品インデックス
        order_prod_idx = [
            prod_to_idx[str(o.get("product_id", o["id"]))] for o in orders
        ]

        # 各注文の eligible periods（納期以前）
        order_eligible = []
        for o in orders:
            due = int(o.get("due_period", sorted_periods[-1]))
            eligible = [p for p in sorted_periods if p <= due]
            order_eligible.append(eligible)

        mdl = CpoModel(name="LotSizingScheduler")

        # ── 決定変数 ──────────────────────────────────────────
        # ap[i]: 注文 i を割り当てる期間番号
        ap = []
        for i, o in enumerate(orders):
            eligible = order_eligible[i]
            if not eligible:
                # 納期が periods の最小値より小さい → 割り当て不可
                # フォールバック: 最小期間（後で infeasible 検出）
                ap.append(mdl.integer_var(
                    domain=[sorted_periods[0]],
                    name=f"ap_{o['id']}"
                ))
            else:
                ap.append(mdl.integer_var(
                    domain=eligible,
                    name=f"ap_{o['id']}"
                ))

        # ── 制約1: 各期間に割り当てられる注文は1件まで（1期間1件） ──
        # all_diff で全注文の assigned_period が異なることを保証
        mdl.add(mdl.all_diff(ap))

        # ── 目的関数 ──────────────────────────────────────────
        obj_terms = []

        # 項1: 保管コスト（due_period - assigned_period）× holding_cost × quantity
        for i, o in enumerate(orders):
            due   = int(o.get("due_period", sorted_periods[-1]))
            hcost = float(o.get("holding_cost_per_period", 1.0))
            qty   = int(o.get("quantity", 1))
            # (due - ap[i]) * hcost * qty
            holding = (due - ap[i]) * hcost * qty
            obj_terms.append(holding)

        # 項2: 段取り替えコスト
        # ヒアリング要件: 実際に生産が行われた期間を時系列順に並べ、
        # 隣接する実生産期間ペアで製品が変わる場合にコストを加算する。
        # アイドル期間を飛び越えて実生産期間同士を比較する。
        #
        # 実装: 全注文ペア (i, j) について
        #   「注文 i の直後の実生産が注文 j である」
        #   = ap[i] < ap[j] かつ 全 k≠i,j について NOT(ap[i] < ap[k] < ap[j])
        #   このとき製品 i → 製品 j の段取り替えコストを加算する。
        #
        # 「直後の実生産」フラグ: is_next[i][j]
        #   is_next[i][j] = 1 ⟺ ap[i] < ap[j] かつ 間に他の注文がない
        #
        # 注文数 N に対して O(N^2) の binary 変数と O(N^3) の制約が生じるが、
        # 実用規模（N≦50程度）では許容範囲内。

        if n_orders >= 2 and setup_cost_map:
            # is_next[i][j]: 注文 i の直後の実生産が注文 j かどうか
            is_next = [[None] * n_orders for _ in range(n_orders)]
            for i in range(n_orders):
                for j in range(n_orders):
                    if i == j:
                        continue
                    # ap[i] < ap[j] かつ 間に他の注文がない
                    # = ap[i] < ap[j] かつ Σ_{k≠i,j} (ap[i] < ap[k] < ap[j]) == 0
                    # CP Optimizer では count_of を使って表現する
                    # 「ap[i] < ap[k] < ap[j]」を満たす k の数 == 0
                    between_count = mdl.sum(
                        mdl.logical_and(ap[i] < ap[k], ap[k] < ap[j])
                        for k in range(n_orders)
                        if k != i and k != j
                    ) if n_orders > 2 else mdl.integer_var(min=0, max=0, name=f"_zero_{i}_{j}")

                    flag = mdl.binary_var(name=f"isnext_{i}_{j}")
                    is_next[i][j] = flag

                    # flag == 1 ⟺ ap[i] < ap[j] AND between_count == 0
                    cond = mdl.logical_and(ap[i] < ap[j], between_count == 0)
                    mdl.add(flag == cond)

            # 段取り替えコストを加算
            for i in range(n_orders):
                for j in range(n_orders):
                    if i == j:
                        continue
                    pi_prod = products_list[order_prod_idx[i]]
                    pj_prod = products_list[order_prod_idx[j]]
                    if pi_prod == pj_prod:
                        continue
                    cost = setup_cost_map.get((pi_prod, pj_prod), 0.0)
                    if cost == 0.0:
                        continue
                    obj_terms.append(is_next[i][j] * cost)

        if obj_terms:
            mdl.minimize(mdl.sum(obj_terms))
        else:
            mdl.minimize(0)

        # ── ソルバー実行 ──────────────────────────────────────
        t0 = _time.perf_counter()
        sol = mdl.solve(TimeLimit=time_limit, LogVerbosity="Quiet")
        solve_time = round(_time.perf_counter() - t0, 3)

        if sol is None or not sol.is_solution():
            return None, None, None

        # ── 解の抽出 ──────────────────────────────────────────
        assignments: List[Dict] = []
        for i, o in enumerate(orders):
            oid = str(o["id"])
            due = int(o.get("due_period", sorted_periods[-1]))
            assigned_period = int(sol.get_value(ap[i]))
            holding_cost_total = (
                (due - assigned_period)
                * float(o.get("holding_cost_per_period", 1.0))
                * int(o.get("quantity", 1))
            )
            assignments.append({
                "order_id":        oid,
                "product_id":      str(o.get("product_id", oid)),
                "quantity":        int(o.get("quantity", 1)),
                "due_period":      due,
                "assigned_period": assigned_period,
                "holding_cost":    round(holding_cost_total, 2),
            })

        try:
            obj_val = float(sol.get_objective_value())
        except Exception:
            obj_val = 0.0

        return assignments, obj_val, solve_time

    def _solve_with_cpsat(
        self,
        orders: List[Dict],
        periods: List[int],
        setup_costs: List[Dict],
        config: Dict,
    ):
        """CP-SAT(CPMpy)版。_solve_with_cpo()と同一の制約セット・目的関数を実装する。

        CP-SAT/CPMpyの目的関数は整数係数のみのため、holding_cost・setup_cost
        （float想定）はSCALEで整数化し、最終目的関数値をSCALEで割り戻す。
        """
        import cpmpy as cp
        from cpmpy.expressions.globalconstraints import InDomain, AllDifferent
        import time as _time

        SCALE = 100

        time_limit = float(config.get("time_limit_sec", 30.0))
        capacity_per_period = int(config.get("capacity_per_period", 999_999))

        sorted_periods = sorted(periods)
        n_orders = len(orders)

        setup_cost_map: Dict[tuple, float] = {}
        for sc in setup_costs:
            key = (str(sc["from_product"]), str(sc["to_product"]))
            setup_cost_map[key] = float(sc.get("cost", 0.0))

        products_list = sorted({str(o.get("product_id", o["id"])) for o in orders})
        prod_to_idx = {p: i for i, p in enumerate(products_list)}

        order_prod_idx = [
            prod_to_idx[str(o.get("product_id", o["id"]))] for o in orders
        ]

        order_eligible = []
        for o in orders:
            due = int(o.get("due_period", sorted_periods[-1]))
            eligible = [p for p in sorted_periods if p <= due]
            order_eligible.append(eligible)

        m = cp.Model()

        ap = []
        for i, o in enumerate(orders):
            eligible = order_eligible[i]
            domain = eligible if eligible else [sorted_periods[0]]
            v = cp.intvar(min(domain), max(domain), name=f"ap_{o['id']}")
            m += InDomain(v, domain)
            ap.append(v)

        m += AllDifferent(ap)

        obj_terms = []

        for i, o in enumerate(orders):
            due   = int(o.get("due_period", sorted_periods[-1]))
            hcost = float(o.get("holding_cost_per_period", 1.0))
            qty   = int(o.get("quantity", 1))
            coeff = round(hcost * qty * SCALE)
            holding_scaled = (due - ap[i]) * coeff
            obj_terms.append(holding_scaled)

        if n_orders >= 2 and setup_cost_map:
            is_next = [[None] * n_orders for _ in range(n_orders)]
            for i in range(n_orders):
                for j in range(n_orders):
                    if i == j:
                        continue
                    if n_orders > 2:
                        between_count = cp.sum([
                            (ap[i] < ap[k]) & (ap[k] < ap[j])
                            for k in range(n_orders)
                            if k != i and k != j
                        ])
                    else:
                        between_count = 0

                    flag = cp.boolvar(name=f"isnext_{i}_{j}")
                    is_next[i][j] = flag

                    cond = (ap[i] < ap[j]) & (between_count == 0)
                    m += (flag == cond)

            for i in range(n_orders):
                for j in range(n_orders):
                    if i == j:
                        continue
                    pi_prod = products_list[order_prod_idx[i]]
                    pj_prod = products_list[order_prod_idx[j]]
                    if pi_prod == pj_prod:
                        continue
                    cost = setup_cost_map.get((pi_prod, pj_prod), 0.0)
                    if cost == 0.0:
                        continue
                    obj_terms.append(is_next[i][j] * round(cost * SCALE))

        if obj_terms:
            m.minimize(cp.sum(obj_terms))
        else:
            m.minimize(0)

        t0 = _time.perf_counter()
        solved = m.solve(solver="ortools", time_limit=time_limit)
        solve_time = round(_time.perf_counter() - t0, 3)

        if not solved:
            return None, None, None

        assignments: List[Dict] = []
        for i, o in enumerate(orders):
            oid = str(o["id"])
            due = int(o.get("due_period", sorted_periods[-1]))
            assigned_period = int(ap[i].value())
            holding_cost_total = (
                (due - assigned_period)
                * float(o.get("holding_cost_per_period", 1.0))
                * int(o.get("quantity", 1))
            )
            assignments.append({
                "order_id":        oid,
                "product_id":      str(o.get("product_id", oid)),
                "quantity":        int(o.get("quantity", 1)),
                "due_period":      due,
                "assigned_period": assigned_period,
                "holding_cost":    round(holding_cost_total, 2),
            })

        try:
            obj_val = float(m.objective_value()) / SCALE
        except Exception:
            obj_val = 0.0

        return assignments, obj_val, solve_time

    def _detect_issues(
        self,
        orders: List[Dict],
        periods: List[int],
        assignments: List[Dict],
        issue_statuses: Dict[str, str],
    ) -> List[Dict]:
        from solvers.base.issue_rules import build_lot_sizing_scheduler_contexts, run_issue_rules

        issues: List[Dict] = []
        assigned_ids = {a["order_id"] for a in assignments}

        # 解チェッカー（2026-09-01追加、バッチ3）: 期間重複割当（all_diff）の独立検証
        checker_ctxs = build_lot_sizing_scheduler_contexts(assignments)
        issues.extend(run_issue_rules(
            domain="LotSizingScheduler", contexts=checker_ctxs, issue_statuses=issue_statuses,
        ))

        # 未割当注文
        for o in orders:
            oid = str(o["id"])
            if oid not in assigned_ids:
                iid = f"unassigned_{oid}"
                if issue_statuses.get(iid) != "ACCEPTED":
                    issues.append({
                        "id":       iid,
                        "severity": "CRITICAL",
                        "title":    f"未割当注文: {oid}",
                        "message":  (
                            f"注文 {oid}（製品: {o.get('product_id', oid)}, "
                            f"納期: 期間{o.get('due_period', '?')}）が"
                            f"どの期間にも割り当てられませんでした。"
                            f"期間数不足または納期が厳しすぎる可能性があります。"
                        ),
                        "relatedContainerIds": [],
                    })

        # 納期超過チェック（念のため）
        for a in assignments:
            if a["assigned_period"] > a["due_period"]:
                iid = f"late_{a['order_id']}"
                if issue_statuses.get(iid) != "ACCEPTED":
                    issues.append({
                        "id":       iid,
                        "severity": "CRITICAL",
                        "title":    f"納期超過: {a['order_id']}",
                        "message":  (
                            f"注文 {a['order_id']} の割当期間 {a['assigned_period']} が"
                            f"納期 {a['due_period']} を超えています。"
                        ),
                        "relatedContainerIds": [],
                    })

        return issues

    def _build_kpi(
        self,
        orders: List[Dict],
        periods: List[int],
        assignments: List[Dict],
        obj_val: Optional[float],
        solve_time: Optional[float],
        config: Optional[Dict] = None,
    ) -> Dict:
        assigned_count   = len(assignments)
        unassigned_count = len(orders) - assigned_count
        total_holding    = round(sum(a["holding_cost"] for a in assignments), 2)
        coverage_rate    = round(assigned_count / max(len(orders), 1), 4)

        # capacity_per_period をKPIに含めて参照する
        capacity_per_period = int((config or {}).get("capacity_per_period", 999_999))

        return {
            "assigned_count":      assigned_count,
            "unassigned_count":    unassigned_count,
            "total_holding_cost":  total_holding,
            "objective_value":     round(obj_val or 0.0, 2),
            "coverage_rate":       coverage_rate,
            "solve_time_sec":      round(solve_time or 0.0, 3),
            "capacity_per_period": capacity_per_period,
        }

    @staticmethod
    def _make_result(
        feasible: bool,
        assignments: List[Dict],
        issues: List[Dict],
        kpi: Dict,
    ) -> Dict:
        return {
            "status":   "ok",
            "feasible": feasible,
            "metadata": {"problem_class": "LotSizingScheduler"},
            "solutions": [{
                "name":        "Plan A",
                "feasible":    feasible,
                "assignments": assignments,
                "kpi":         kpi,
            }],
            "issues":  issues,
            "_solver_version": _SOLVER_VERSION,
        }