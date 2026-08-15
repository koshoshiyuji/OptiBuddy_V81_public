"""
VesselDeckLoaderSolver — 船舶甲板コンテナ積み付け最適化 (CSPLib prob008)
==========================================================================

定式化: CP Optimizer (docplex.cp)
  決定変数:
    x_i (IntVar, 0..DECK_LENGTH-1): コンテナiの左下角の奥行き方向座標 (西→東)
    y_i (IntVar, 0..DECK_WIDTH-w_i): コンテナiの左下角の幅方向座標 (南→北)
    Z   (IntVar, 0..DECK_LENGTH):    使用する甲板長 (最小化対象)

  制約:
    - 甲板幅制約: 0 <= y_i, y_i + w_i <= DECK_WIDTH (6)
    - 使用甲板長制約: Z >= x_i + l_i  (全コンテナ)
    - 2次元非重複制約: 任意ペア(i,j)で OR(x_i+l_i<=x_j, x_j+l_j<=x_i, y_i+w_i<=y_j, y_j+w_j<=y_i)
    - 危険物分離制約: hazardous コンテナ間は各方向の閾値に +1 マージン
    - 積み込み順序の支持制約: 順序k番目以降のコンテナは西壁・南壁・北壁または
      先行コンテナと正の長さを持つ境界線分で接していること

  目的: Minimize(Z)

solver_input keys:
  - problem_class: str ("VesselDeckLoader")
  - containers: List[Dict]  # id, name, length, width, load_order, is_hazardous
  - deck: Dict              # width (int), max_length (int)
  - config: Dict            # time_limit_sec, hazard_margin
  - issue_statuses: Dict[str, str]
  - meta: Dict
"""

from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)

_SOLVER_VERSION = "vessel_deck_loader_v1.0"
_DEFAULT_DECK_WIDTH = 6
_DEFAULT_MAX_LENGTH = 30
_DEFAULT_TIME_LIMIT = 30
_DEFAULT_HAZARD_MARGIN = 1


class VesselDeckLoaderSolver:
    def __init__(self, solver_input: dict) -> None:
        self.dsl = solver_input

    def solve(self) -> dict:
        containers: List[Dict] = self.dsl.get("containers", [])
        deck: Dict = self.dsl.get("deck", {})
        config: Dict = self.dsl.get("config", {})
        issue_statuses: Dict = self.dsl.get("issue_statuses", {})
        meta: Dict = self.dsl.get("meta", {})

        deck_width = int(deck.get("width", _DEFAULT_DECK_WIDTH))
        max_length = int(deck.get("max_length", _DEFAULT_MAX_LENGTH))
        time_limit = int(config.get("time_limit_sec", _DEFAULT_TIME_LIMIT))
        hazard_margin = int(config.get("hazard_margin", _DEFAULT_HAZARD_MARGIN))

        if not containers:
            return self._make_result(
                feasible=False,
                placements=[],
                Z_val=0,
                deck_width=deck_width,
                issues=[{
                    "id": "solve_failed",
                    "severity": "CRITICAL",
                    "title": "積み付け対象コンテナなし",
                    "message": "コンテナが1件も指定されていません。",
                    "relatedContainerIds": [],
                }],
                meta=meta,
            )

        try:
            from solvers.base.engine_select import get_solver_engine, CPSAT
            engine = get_solver_engine(config)
            if engine == CPSAT:
                placements, Z_val, issues = self._solve_with_cpsat(
                    containers, deck_width, max_length, hazard_margin, time_limit, issue_statuses
                )
            else:
                placements, Z_val, issues = self._solve_with_cpo(
                    containers, deck_width, max_length, hazard_margin, time_limit, issue_statuses
                )
        except Exception as exc:
            from solvers.base.ce_limit_lns import is_ce_limit_exceeded
            if is_ce_limit_exceeded(exc):
                logger.warning(f"[VesselDeckLoader] CE上限検知: {exc}。件数を減らして再試行してください。")
                return self._make_result(
                    feasible=False,
                    placements=[],
                    Z_val=0,
                    deck_width=deck_width,
                    issues=[{
                        "id": "solve_failed",
                        "severity": "CRITICAL",
                        "title": "CPLEXの無料版で扱える件数を超えています",
                        "message": "コンテナ件数を減らすか、正規ライセンスのご利用をご検討ください。",
                        "relatedContainerIds": [],
                    }],
                    meta=meta,
                )
            from solvers.base.solver_error_result import build_solver_crash_issue, solver_crash_extra_fields
            logger.error(f"[VesselDeckLoader] mdl.solve() 例外: {exc}", exc_info=True)
            result = self._make_result(
                feasible=False,
                placements=[],
                Z_val=0,
                deck_width=deck_width,
                issues=[build_solver_crash_issue(exc)],
                meta=meta,
            )
            result.update(solver_crash_extra_fields(exc))
            return result

        if placements is None:
            return self._make_result(
                feasible=False,
                placements=[],
                Z_val=0,
                deck_width=deck_width,
                issues=[{
                    "id": "solve_failed",
                    "severity": "CRITICAL",
                    "title": "制約を満たす積み付け配置が見つかりません",
                    "message": (
                        "指定された制約（甲板幅・非重複・危険物分離・支持条件）を"
                        "すべて同時に満たす配置が存在しないか、制限時間内に見つかりませんでした。"
                    ),
                    "relatedContainerIds": [],
                }],
                meta=meta,
            )

        from solvers.base.issue_rules import build_full_unassignment_issue
        anomaly = build_full_unassignment_issue(
            assigned_count=len(placements),
            total_count=len(containers),
            entity_label="コンテナ",
            extra_hint="get_var_solution()での解抽出処理",
        )
        if anomaly:
            issues.insert(0, anomaly)

        return self._make_result(
            feasible=True,
            placements=placements,
            Z_val=Z_val,
            deck_width=deck_width,
            issues=issues,
            meta=meta,
        )

    # ------------------------------------------------------------------
    # CP モデル構築 + ソルブ
    # ------------------------------------------------------------------

    def _solve_with_cpo(
        self,
        containers: List[Dict],
        deck_width: int,
        max_length: int,
        hazard_margin: int,
        time_limit: int,
        issue_statuses: Dict,
    ) -> Tuple[Optional[List[Dict]], int, List[Dict]]:
        from docplex.cp.model import CpoModel

        mdl = CpoModel(name="VesselDeckLoader")
        n = len(containers)

        # --- 決定変数 ---
        x_vars: List[Any] = []
        y_vars: List[Any] = []
        for c in containers:
            l_i = int(c["length"])
            w_i = int(c["width"])
            xi = mdl.integer_var(0, max(0, max_length - l_i), name=f"x_{c['id']}")
            yi = mdl.integer_var(0, max(0, deck_width - w_i), name=f"y_{c['id']}")
            x_vars.append(xi)
            y_vars.append(yi)

        Z = mdl.integer_var(0, max_length, name="Z")

        # --- 甲板長制約 ---
        for i, c in enumerate(containers):
            mdl.add(Z >= x_vars[i] + int(c["length"]))

        # --- 2次元非重複制約 + 危険物分離制約 ---
        for i in range(n):
            ci = containers[i]
            li = int(ci["length"])
            wi = int(ci["width"])
            hi = bool(ci.get("is_hazardous", False))

            for j in range(i + 1, n):
                cj = containers[j]
                lj = int(cj["length"])
                wj = int(cj["width"])
                hj = bool(cj.get("is_hazardous", False))

                mg = hazard_margin if (hi and hj) else 0

                # 修正(2026-08-03): docplex.cpのlogical_or()は位置引数を最大2個
                # (e1, e2=None)しか受け付けないため、3個以上の節をまとめる場合は
                # リストとして渡す必要がある(TypeError: logical_or() takes from 1
                # to 2 positional arguments but 4 were given、で発覚)。
                mdl.add(mdl.logical_or([
                    x_vars[i] + li + mg <= x_vars[j],
                    x_vars[j] + lj + mg <= x_vars[i],
                    y_vars[i] + wi + mg <= y_vars[j],
                    y_vars[j] + wj + mg <= y_vars[i],
                ]))

        # --- 積み込み順序の支持制約 ---
        # コンテナを load_order でソートし、順序付きインデックスを決定
        order_sorted = sorted(
            enumerate(containers),
            key=lambda iv: int(iv[1].get("load_order", 999)),
        )
        # 先頭（順序1番目）は制約不要
        for rank, (k, ck) in enumerate(order_sorted):
            if rank == 0:
                continue
            lk = int(ck["length"])
            wk = int(ck["width"])
            prior_indices = [idx for idx, _ in order_sorted[:rank]]

            # 支持条件: 西壁(x_k==0) OR 南壁(y_k==0) OR 北壁(y_k+w_k==deck_width)
            # OR 先行コンテナjとの境界共有（接触）
            support_clauses = [
                x_vars[k] == 0,
                y_vars[k] == 0,
                y_vars[k] + wk == deck_width,
            ]
            for j in prior_indices:
                cj = containers[j]
                lj = int(cj["length"])
                wj = int(cj["width"])
                # 修正(2026-08-03): logical_and()も同様に位置引数は最大2個までの
                # ため、3条件をまとめる場合はリストで渡す。
                # 東側面で接触: x_k = x_j + l_j かつ y-方向オーバーラップあり
                contact_east = mdl.logical_and([
                    x_vars[k] == x_vars[j] + lj,
                    y_vars[j] < y_vars[k] + wk,
                    y_vars[k] < y_vars[j] + wj,
                ])
                # 西側面で接触: x_j = x_k + l_k かつ y-方向オーバーラップあり
                contact_west = mdl.logical_and([
                    x_vars[j] == x_vars[k] + lk,
                    y_vars[j] < y_vars[k] + wk,
                    y_vars[k] < y_vars[j] + wj,
                ])
                # 南側面で接触: y_k = y_j + w_j かつ x-方向オーバーラップあり
                contact_south = mdl.logical_and([
                    y_vars[k] == y_vars[j] + wj,
                    x_vars[j] < x_vars[k] + lk,
                    x_vars[k] < x_vars[j] + lj,
                ])
                # 北側面で接触: y_j = y_k + w_k かつ x-方向オーバーラップあり
                contact_north = mdl.logical_and([
                    y_vars[j] == y_vars[k] + wk,
                    x_vars[j] < x_vars[k] + lk,
                    x_vars[k] < x_vars[j] + lj,
                ])
                support_clauses.extend([contact_east, contact_west, contact_south, contact_north])

            mdl.add(mdl.logical_or(support_clauses))

        # --- 目的関数 ---
        mdl.add(mdl.minimize(Z))

        # --- ソルブ ---
        try:
            msol = mdl.solve(TimeLimit=time_limit, LogVerbosity="Quiet")
        except Exception as exc:
            from solvers.base.ce_limit_lns import is_ce_limit_exceeded, CeLimitExceededError
            if is_ce_limit_exceeded(exc):
                raise CeLimitExceededError(str(exc)) from exc
            raise

        if msol is None or not msol:
            return None, 0, []

        # --- 解抽出 ---
        placements: List[Dict] = []
        for i, c in enumerate(containers):
            xi_sol = msol.get_var_solution(x_vars[i])
            yi_sol = msol.get_var_solution(y_vars[i])
            if xi_sol is None or yi_sol is None:
                continue
            placements.append({
                "container_id":   c["id"],
                "container_name": c.get("name", c["id"]),
                "x":              xi_sol.get_value(),
                "y":              yi_sol.get_value(),
                "length":         int(c["length"]),
                "width":          int(c["width"]),
                "load_order":     int(c.get("load_order", 999)),
                "is_hazardous":   bool(c.get("is_hazardous", False)),
            })

        Z_sol = msol.get_var_solution(Z)
        Z_val = int(Z_sol.get_value()) if Z_sol is not None else 0

        issues = self._detect_issues(placements, Z_val, deck_width, hazard_margin, issue_statuses)
        return placements, Z_val, issues

    def _solve_with_cpsat(
        self,
        containers: List[Dict],
        deck_width: int,
        max_length: int,
        hazard_margin: int,
        time_limit: int,
        issue_statuses: Dict,
    ) -> Tuple[Optional[List[Dict]], int, List[Dict]]:
        """CP-SAT(CPMpy)版。_solve_with_cpo()と同一の制約セット・目的関数を実装する。"""
        import cpmpy as cp

        n = len(containers)
        m = cp.Model()

        x_vars: List[Any] = []
        y_vars: List[Any] = []
        for c in containers:
            l_i = int(c["length"])
            w_i = int(c["width"])
            xi = cp.intvar(0, max(0, max_length - l_i), name=f"x_{c['id']}")
            yi = cp.intvar(0, max(0, deck_width - w_i), name=f"y_{c['id']}")
            x_vars.append(xi)
            y_vars.append(yi)

        Z = cp.intvar(0, max_length, name="Z")

        for i, c in enumerate(containers):
            m += (Z >= x_vars[i] + int(c["length"]))

        for i in range(n):
            ci = containers[i]
            li = int(ci["length"])
            wi = int(ci["width"])
            hi = bool(ci.get("is_hazardous", False))

            for j in range(i + 1, n):
                cj = containers[j]
                lj = int(cj["length"])
                wj = int(cj["width"])
                hj = bool(cj.get("is_hazardous", False))

                mg = hazard_margin if (hi and hj) else 0

                m += cp.any([
                    x_vars[i] + li + mg <= x_vars[j],
                    x_vars[j] + lj + mg <= x_vars[i],
                    y_vars[i] + wi + mg <= y_vars[j],
                    y_vars[j] + wj + mg <= y_vars[i],
                ])

        order_sorted = sorted(
            enumerate(containers),
            key=lambda iv: int(iv[1].get("load_order", 999)),
        )
        for rank, (k, ck) in enumerate(order_sorted):
            if rank == 0:
                continue
            lk = int(ck["length"])
            wk = int(ck["width"])
            prior_indices = [idx for idx, _ in order_sorted[:rank]]

            support_clauses = [
                x_vars[k] == 0,
                y_vars[k] == 0,
                y_vars[k] + wk == deck_width,
            ]
            for j in prior_indices:
                cj = containers[j]
                lj = int(cj["length"])
                wj = int(cj["width"])
                contact_east = cp.all([
                    x_vars[k] == x_vars[j] + lj,
                    y_vars[j] < y_vars[k] + wk,
                    y_vars[k] < y_vars[j] + wj,
                ])
                contact_west = cp.all([
                    x_vars[j] == x_vars[k] + lk,
                    y_vars[j] < y_vars[k] + wk,
                    y_vars[k] < y_vars[j] + wj,
                ])
                contact_south = cp.all([
                    y_vars[k] == y_vars[j] + wj,
                    x_vars[j] < x_vars[k] + lk,
                    x_vars[k] < x_vars[j] + lj,
                ])
                contact_north = cp.all([
                    y_vars[j] == y_vars[k] + wk,
                    x_vars[j] < x_vars[k] + lk,
                    x_vars[k] < x_vars[j] + lj,
                ])
                support_clauses.extend([contact_east, contact_west, contact_south, contact_north])

            m += cp.any(support_clauses)

        m.minimize(Z)

        solved = m.solve(solver="ortools", time_limit=time_limit)

        if not solved:
            return None, 0, []

        placements: List[Dict] = []
        for i, c in enumerate(containers):
            xi_val = x_vars[i].value()
            yi_val = y_vars[i].value()
            if xi_val is None or yi_val is None:
                continue
            placements.append({
                "container_id":   c["id"],
                "container_name": c.get("name", c["id"]),
                "x":              int(xi_val),
                "y":              int(yi_val),
                "length":         int(c["length"]),
                "width":          int(c["width"]),
                "load_order":     int(c.get("load_order", 999)),
                "is_hazardous":   bool(c.get("is_hazardous", False)),
            })

        Z_val = int(Z.value()) if Z.value() is not None else 0

        issues = self._detect_issues(placements, Z_val, deck_width, hazard_margin, issue_statuses)
        return placements, Z_val, issues

    # ------------------------------------------------------------------
    # イシュー検知
    # ------------------------------------------------------------------

    def _detect_issues(
        self,
        placements: List[Dict],
        Z_val: int,
        deck_width: int,
        hazard_margin: int,
        issue_statuses: Dict,
    ) -> List[Dict]:
        issues: List[Dict] = []

        # 甲板幅はみ出し検証
        for p in placements:
            if p["y"] + p["width"] > deck_width:
                iid = f"width_violation_{p['container_id']}"
                if issue_statuses.get(iid) != "ACCEPTED":
                    issues.append({
                        "id": iid,
                        "severity": "CRITICAL",
                        "title": f"甲板幅超過: {p['container_name']}",
                        "message": (
                            f"コンテナ「{p['container_name']}」(y={p['y']}, width={p['width']}) が"
                            f"甲板幅{deck_width}を超えています。"
                        ),
                        "relatedContainerIds": [p["container_id"]],
                    })

        # 危険物間隔検証
        hazardous = [p for p in placements if p["is_hazardous"]]
        for i in range(len(hazardous)):
            for j in range(i + 1, len(hazardous)):
                pi, pj = hazardous[i], hazardous[j]
                x_sep = (pi["x"] + pi["length"] + hazard_margin <= pj["x"] or
                         pj["x"] + pj["length"] + hazard_margin <= pi["x"])
                y_sep = (pi["y"] + pi["width"] + hazard_margin <= pj["y"] or
                         pj["y"] + pj["width"] + hazard_margin <= pi["y"])
                if not (x_sep or y_sep):
                    iid = f"hazard_proximity_{pi['container_id']}_{pj['container_id']}"
                    if issue_statuses.get(iid) != "ACCEPTED":
                        issues.append({
                            "id": iid,
                            "severity": "CRITICAL",
                            "title": f"危険物近接: {pi['container_name']} ⇔ {pj['container_name']}",
                            "message": (
                                f"危険物コンテナ「{pi['container_name']}」と「{pj['container_name']}」の間に"
                                f"必要な隔離マージン({hazard_margin})が確保されていません。"
                            ),
                            "relatedContainerIds": [pi["container_id"], pj["container_id"]],
                        })
        return issues

    # ------------------------------------------------------------------
    # 結果組み立て
    # ------------------------------------------------------------------

    def _make_result(
        self,
        feasible: bool,
        placements: List[Dict],
        Z_val: int,
        deck_width: int,
        issues: List[Dict],
        meta: Dict,
    ) -> dict:
        return {
            "status":   "ok",
            "feasible": feasible,
            "metadata": {"problem_class": "VesselDeckLoader"},
            "solutions": [{
                "name":       "Plan A",
                "feasible":   feasible,
                "placements": placements,
                "Z_val":      Z_val,
                "deck_width": deck_width,
                "kpi": {
                    "used_length":       Z_val,
                    "container_count":   len(placements),
                    "hazardous_count":   sum(1 for p in placements if p["is_hazardous"]),
                },
            }],
            "issues": issues,
            "_solver_version": _SOLVER_VERSION,
        }