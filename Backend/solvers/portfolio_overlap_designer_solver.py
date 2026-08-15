"""
Backend/solvers/portfolio_overlap_designer_solver.py

PortfolioOverlapDesignerSolver — CSPLib prob065 ベース
=======================================================

業務概要:
  複数の投資信託ファンドそれぞれに対し、候補銘柄プールから規定本数の銘柄を選択する。
  任意の2ファンド間で共有される銘柄数（ペアワイズ重複数）の最悪値を最小化する。

定式化（CP Optimizer, docplex.cp）:
  変数:
    x[i][j] ∈ {0,1} : ファンドiが銘柄jを組み入れるか（BinaryVar）
    lam ∈ {0..b}     : ペアワイズ重複数の上界（補助変数、最小化対象）

  制約:
    Sum(x[i][j] for j in pool) == r[i]   各ファンドiの組入銘柄数ちょうど
    lam >= Sum(x[i1][j]*x[i2][j] for j in pool)  全ペア(i1,i2), i1<i2

  目的:
    Minimize(lam)   ミニマックス型（最悪ペアワイズ重複数の最小化）

# solver_input keys:
#   problem_class:  str  "PortfolioOverlapDesigner"
#   meta:           dict
#   funds:          List[{id, name, required_count}]
#   pool:           List[{id, name, sector?}]
#   config:         {time_limit_sec, ...}
#   issue_statuses: dict
"""

from __future__ import annotations

import logging
import time
from typing import Any, Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)


class PortfolioOverlapDesignerSolver:
    def __init__(self, solver_input: dict) -> None:
        self.dsl = solver_input

    def solve(self) -> dict:
        funds: List[Dict]  = self.dsl.get("funds", [])
        pool:  List[Dict]  = self.dsl.get("pool", [])
        config: Dict       = self.dsl.get("config", {})
        issue_statuses     = self.dsl.get("issue_statuses", {})

        validation_error = self._validate(funds, pool)
        if validation_error:
            return self._make_result(
                feasible=False,
                funds=funds,
                pool=pool,
                assignments={},
                overlap_matrix=[],
                worst_overlap=None,
                solve_time=0.0,
                extra_issues=[{
                    "id": "solve_failed",
                    "severity": "CRITICAL",
                    "title": "入力データ検証エラー",
                    "message": validation_error,
                    "relatedContainerIds": [],
                }],
                issue_statuses=issue_statuses,
            )

        try:
            from solvers.base.engine_select import get_solver_engine, CPSAT
            engine = get_solver_engine(config)
            if engine == CPSAT:
                assignments, worst_overlap, solve_time = self._solve_with_cpsat(funds, pool, config)
            else:
                assignments, worst_overlap, solve_time = self._solve_with_cpo(funds, pool, config)
        except Exception as e:
            from solvers.base.ce_limit_lns import is_ce_limit_exceeded, CeLimitExceededError
            if is_ce_limit_exceeded(e):
                try:
                    from solvers.portfolio_overlap_designer_batch_decomposer import (
                        PRIMARY_ENTITY_KEY, build_subset_input, merge_results,
                    )
                    from solvers.base.ce_limit_lns import run_solve_with_ce_limit_fallback
                    return run_solve_with_ce_limit_fallback(
                        solver_class=type(self),
                        solver_input=self.dsl,
                        primary_entity_key=PRIMARY_ENTITY_KEY,
                        build_subset_input=build_subset_input,
                        merge_results=merge_results,
                        problem_class="PortfolioOverlapDesigner",
                        solver_version="portfolio_overlap_designer_v1.0",
                    )
                except ImportError:
                    return self._make_result(
                        feasible=False, funds=funds, pool=pool,
                        assignments={}, overlap_matrix=[], worst_overlap=None,
                        solve_time=0.0,
                        extra_issues=[{
                            "id": "ce_limit_no_adapter",
                            "severity": "CRITICAL",
                            "title": "CPLEXの無料版で扱える件数を超えています",
                            "message": "データ件数を減らすか、正規ライセンスのご利用をご検討ください。",
                            "relatedContainerIds": [],
                        }],
                        issue_statuses=issue_statuses,
                    )

            from solvers.base.solver_error_result import build_solver_crash_issue, solver_crash_extra_fields
            logger.error(f"[PortfolioOverlapDesigner] mdl.solve() 例外: {e}", exc_info=True)
            result = self._make_result(
                feasible=False, funds=funds, pool=pool,
                assignments={}, overlap_matrix=[], worst_overlap=None,
                solve_time=0.0,
                extra_issues=[build_solver_crash_issue(e)],
                issue_statuses=issue_statuses,
            )
            result.update(solver_crash_extra_fields(e))
            return result

        if assignments is None:
            return self._make_result(
                feasible=False,
                funds=funds,
                pool=pool,
                assignments={},
                overlap_matrix=[],
                worst_overlap=None,
                solve_time=solve_time,
                extra_issues=[{
                    "id": "solve_failed",
                    "severity": "CRITICAL",
                    "title": "最適解が見つかりませんでした",
                    "message": (
                        "制約を満たす銘柄組み合わせが見つかりませんでした。"
                        "各ファンドの組入銘柄数（required_count）が候補銘柄プール数を超えていないか確認してください。"
                    ),
                    "relatedContainerIds": [],
                }],
                issue_statuses=issue_statuses,
            )

        overlap_matrix = self._compute_overlap_matrix(funds, pool, assignments)

        # 全件未割当異常検知
        from solvers.base.issue_rules import build_full_unassignment_issue
        total_expected = sum(f.get("required_count", 0) for f in funds)
        total_assigned = sum(len(v) for v in assignments.values())
        anomaly = build_full_unassignment_issue(
            assigned_count=total_assigned,
            total_count=total_expected,
            entity_label="銘柄割当",
            extra_hint="get_var_solution() での解抽出処理",
        )
        extra_issues = [anomaly] if anomaly else []

        return self._make_result(
            feasible=True,
            funds=funds,
            pool=pool,
            assignments=assignments,
            overlap_matrix=overlap_matrix,
            worst_overlap=worst_overlap,
            solve_time=solve_time,
            extra_issues=extra_issues,
            issue_statuses=issue_statuses,
        )

    # -------------------------------------------------------------------------
    # 内部実装
    # -------------------------------------------------------------------------

    def _validate(self, funds: List[Dict], pool: List[Dict]) -> Optional[str]:
        if not funds:
            return "ファンドが1件も定義されていません。"
        if not pool:
            return "候補銘柄プールが空です。"
        b = len(pool)
        for f in funds:
            r = f.get("required_count", 0)
            if r <= 0:
                return f"ファンド '{f.get('name', f.get('id'))}' の組入銘柄数(required_count)は1以上を指定してください。"
            if r > b:
                return (
                    f"ファンド '{f.get('name', f.get('id'))}' の組入銘柄数({r})が"
                    f"候補銘柄数({b})を超えています。"
                )
        return None

    def _solve_with_cpo(
        self,
        funds: List[Dict],
        pool:  List[Dict],
        config: Dict,
    ) -> Tuple[Optional[Dict[str, List[str]]], Optional[int], float]:
        """
        CP Optimizer でミニマックス型ポートフォリオ設計を解く。

        Returns:
            (assignments, worst_overlap, solve_time_sec)
            assignments: {fund_id: [stock_id, ...]} または None（解なし）
            worst_overlap: int または None
        """
        from docplex.cp.model import CpoModel
        from solvers.base.ce_limit_lns import is_ce_limit_exceeded, CeLimitExceededError

        mdl = CpoModel(name="portfolio_overlap_designer")

        v = len(funds)   # ファンド数
        b = len(pool)    # 銘柄数

        fund_ids  = [f["id"] for f in funds]
        stock_ids = [s["id"] for s in pool]

        # x[i][j] ∈ {0,1}: ファンドiが銘柄jを組み入れるか
        x: List[List[Any]] = [
            [mdl.binary_var(name=f"x_{i}_{j}") for j in range(b)]
            for i in range(v)
        ]

        # 補助変数: ペアワイズ重複数の上界（最小化対象）
        # docplex.cp では integer_var の範囲指定に min/max を使う（lb/ub ではない）
        lam = mdl.integer_var(min=0, max=b, name="lam")

        # 制約1: 各ファンドの組入銘柄数ちょうど
        for i, fund in enumerate(funds):
            r_i = fund.get("required_count", 0)
            mdl.add(mdl.sum(x[i]) == r_i)

        # 制約2: ペアワイズ重複数の上界制約（全ペア）
        # lam >= Sum(x[i1][j]*x[i2][j] for j)  ∀ i1 < i2
        # CPO では binary_var の積を直接書けないため、補助 binary_var z[i1][i2][j] で線形化する。
        # z[i1][i2][j] = x[i1][j] AND x[i2][j]
        # ペア数が多い場合 z 変数は O(v^2 * b) になるが、
        # prob065 の標準インスタンスは v,b とも数十以下のため許容範囲内。
        for i1 in range(v):
            for i2 in range(i1 + 1, v):
                overlap_terms = []
                for j in range(b):
                    z = mdl.binary_var(name=f"z_{i1}_{i2}_{j}")
                    mdl.add(z <= x[i1][j])
                    mdl.add(z <= x[i2][j])
                    mdl.add(z >= x[i1][j] + x[i2][j] - 1)
                    overlap_terms.append(z)
                mdl.add(lam >= mdl.sum(overlap_terms))

        # 目的関数: ミニマックス重複数の最小化
        mdl.add(mdl.minimize(lam))

        time_limit = config.get("time_limit_sec", 60)
        t0 = time.perf_counter()
        try:
            msol = mdl.solve(TimeLimit=time_limit, LogVerbosity="Quiet")
        except Exception as e:
            if is_ce_limit_exceeded(e):
                raise CeLimitExceededError(str(e)) from e
            raise
        solve_time = time.perf_counter() - t0

        if msol is None or not msol:
            return None, None, solve_time

        # 解抽出（禁止パターン5対策: get_var_solution() を使う）
        assignments: Dict[str, List[str]] = {}
        for i, fund in enumerate(funds):
            selected = []
            for j, stock in enumerate(pool):
                var_sol = msol.get_var_solution(x[i][j])
                if var_sol is not None and var_sol.get_value() > 0.5:
                    selected.append(stock_ids[j])
            assignments[fund_ids[i]] = selected

        # worst_overlap を Python 側で計算（禁止パターン6b対策）
        worst_overlap = 0
        for i1 in range(v):
            set_i1 = set(assignments.get(fund_ids[i1], []))
            for i2 in range(i1 + 1, v):
                set_i2 = set(assignments.get(fund_ids[i2], []))
                overlap = len(set_i1 & set_i2)
                if overlap > worst_overlap:
                    worst_overlap = overlap

        return assignments, worst_overlap, solve_time

    def _solve_with_cpsat(
        self,
        funds: List[Dict],
        pool:  List[Dict],
        config: Dict,
    ) -> Tuple[Optional[Dict[str, List[str]]], Optional[int], float]:
        """CP-SAT(CPMpy)版。_solve_with_cpo()と同一の制約セット・目的関数を実装する。"""
        import cpmpy as cp

        v = len(funds)
        b = len(pool)

        fund_ids  = [f["id"] for f in funds]
        stock_ids = [s["id"] for s in pool]

        x: List[List[Any]] = [
            [cp.boolvar(name=f"x_{i}_{j}") for j in range(b)]
            for i in range(v)
        ]
        lam = cp.intvar(0, b, name="lam")

        m = cp.Model()
        for i, fund in enumerate(funds):
            r_i = fund.get("required_count", 0)
            m += (cp.sum(x[i]) == r_i)

        for i1 in range(v):
            for i2 in range(i1 + 1, v):
                # boolvar同士のANDはbool式のまま合計できる（線形化用のz変数は不要）
                overlap_terms = [x[i1][j] & x[i2][j] for j in range(b)]
                m += (lam >= cp.sum(overlap_terms))

        m.minimize(lam)

        time_limit = config.get("time_limit_sec", 60)
        t0 = time.perf_counter()
        solved = m.solve(solver="ortools", time_limit=time_limit)
        solve_time = time.perf_counter() - t0

        if not solved:
            return None, None, solve_time

        assignments: Dict[str, List[str]] = {}
        for i, fund in enumerate(funds):
            selected = []
            for j, stock in enumerate(pool):
                val = x[i][j].value()
                if val:
                    selected.append(stock_ids[j])
            assignments[fund_ids[i]] = selected

        worst_overlap = 0
        for i1 in range(v):
            set_i1 = set(assignments.get(fund_ids[i1], []))
            for i2 in range(i1 + 1, v):
                set_i2 = set(assignments.get(fund_ids[i2], []))
                overlap = len(set_i1 & set_i2)
                if overlap > worst_overlap:
                    worst_overlap = overlap

        return assignments, worst_overlap, solve_time

    def _compute_overlap_matrix(
        self,
        funds:       List[Dict],
        pool:        List[Dict],
        assignments: Dict[str, List[str]],
    ) -> List[Dict[str, Any]]:
        """全ファンドペアの重複銘柄数を計算してマトリックスとして返す。"""
        fund_ids = [f["id"] for f in funds]
        matrix = []
        for i1 in range(len(funds)):
            for i2 in range(i1 + 1, len(funds)):
                fid1, fid2 = fund_ids[i1], fund_ids[i2]
                set1 = set(assignments.get(fid1, []))
                set2 = set(assignments.get(fid2, []))
                common = sorted(set1 & set2)
                matrix.append({
                    "fund_id_a":      fid1,
                    "fund_name_a":    funds[i1].get("name", fid1),
                    "fund_id_b":      fid2,
                    "fund_name_b":    funds[i2].get("name", fid2),
                    "overlap_count":  len(common),
                    "common_stocks":  common,
                })
        return matrix

    def _make_result(
        self,
        feasible:       bool,
        funds:          List[Dict],
        pool:           List[Dict],
        assignments:    Dict[str, List[str]],
        overlap_matrix: List[Dict],
        worst_overlap:  Optional[int],
        solve_time:     float,
        extra_issues:   List[Dict],
        issue_statuses: Dict[str, str],
    ) -> dict:
        issues = [
            i for i in extra_issues
            if issue_statuses.get(i.get("id", "")) != "ACCEPTED"
        ]

        fund_solutions = []
        for f in funds:
            fid = f["id"]
            selected_ids = assignments.get(fid, [])
            selected_set = set(selected_ids)
            fund_solutions.append({
                "fund_id":       fid,
                "fund_name":     f.get("name", fid),
                "required_count": f.get("required_count", 0),
                "selected_count": len(selected_ids),
                "selected_stocks": [
                    {
                        "id":     s["id"],
                        "name":   s.get("name", s["id"]),
                        "sector": s.get("sector", ""),
                    }
                    for s in pool if s["id"] in selected_set
                ],
            })

        kpi = {
            "worst_overlap":  worst_overlap,
            "fund_count":     len(funds),
            "pool_size":      len(pool),
            "solve_time_sec": round(solve_time, 2),
        }

        return {
            "status":   "ok",
            "feasible": feasible,
            "metadata": {
                "problem_class": "PortfolioOverlapDesigner",
                "fund_count":    len(funds),
                "pool_size":     len(pool),
            },
            "solutions": [{
                "name":           "Plan A",
                "feasible":       feasible,
                "fund_solutions": fund_solutions,
                "overlap_matrix": overlap_matrix,
                "kpi":            kpi,
            }] if feasible else [],
            "issues":  issues,
            "kpi":     kpi,
            "_solver_version": "portfolio_overlap_designer_v1.0",
        }
