"""
Backend/solvers/mystery_shopper_scheduler_solver.py

MysteryShopperSchedulerSolver — 覆面調査スケジューリングソルバー

solver_input keys:
  - problem_class: str
  - shoppers: List[Dict]  # 調査員
  - visits: List[Dict]    # 店舗訪問枠（店舗×回の展開済みリスト）
  - stores: List[Dict]    # 店舗マスタ
  - config: Dict
  - issue_statuses: Dict

問題構造:
  - 各店舗訪問枠を調査員の稼働日に割り当てる
  - 1調査員×1日: 1件まで（no_overlap相当、日ベース）
  - 同一調査員×同一店舗: 最低再訪問間隔（日数）を空ける
  - 資格要件: 該当資格を持つ調査員のみ担当可
  - 稼働可能日カレンダー内のみ割り当て可能
  - 目的: lexicographic
      1. 割り当て件数最大化（充足数）
      2. 調査員間の担当件数ばらつき（max - min）最小化

技術選択: docplex.mp (CPLEX MIP)
  - 時間軸は「日付×調査員」のbinary変数で表現
  - lexicographic目的はBig-Mを避けてCPLEX MIPのMulti-objective APIを使用
    （CPLEX 20.1+ の mdl.set_multi_objective_lexicographic() を使う。
      利用できない場合は段階解法にフォールバック）
"""

from __future__ import annotations

import logging
from datetime import date, timedelta
from typing import Any, Dict, List, Optional, Set, Tuple

logger = logging.getLogger(__name__)

_SOLVER_VERSION = "mystery_shopper_scheduler_v1.0"
_DEFAULT_MIN_REVISIT_DAYS = 28
_DEFAULT_SOLVE_TIME_SEC = 30


class MysteryShopperSchedulerSolver:
    def __init__(self, solver_input: dict) -> None:
        self.dsl = solver_input

    def solve(self) -> dict:
        shoppers: List[Dict] = self.dsl.get("shoppers", [])
        visits: List[Dict] = self.dsl.get("visits", [])
        stores: List[Dict] = self.dsl.get("stores", [])
        config: Dict = self.dsl.get("config", {})
        issue_statuses: Dict = self.dsl.get("issue_statuses", {})

        if not shoppers:
            return self._make_result(
                feasible=False,
                assignments=[],
                issues=[{
                    "id": "solve_failed",
                    "severity": "CRITICAL",
                    "title": "調査員が登録されていません",
                    "message": "shoppers リストが空です。少なくとも1名の調査員を登録してください。",
                    "relatedContainerIds": [],
                }],
                config=config,
                shoppers=shoppers,
                visits=visits,
                stores=stores,
            )

        if not visits:
            return self._make_result(
                feasible=False,
                assignments=[],
                issues=[{
                    "id": "solve_failed",
                    "severity": "CRITICAL",
                    "title": "店舗訪問枠がありません",
                    "message": "visits リストが空です。少なくとも1件の訪問枠を登録してください。",
                    "relatedContainerIds": [],
                }],
                config=config,
                shoppers=shoppers,
                visits=visits,
                stores=stores,
            )

        try:
            assignments, solve_time, mip_check_issues = self._solve_mip(shoppers, visits, stores, config)
        except _CeLimitError:
            return self._ce_limit_result(config, shoppers, visits, stores)
        except Exception as e:
            from solvers.base.solver_error_result import build_solver_crash_issue, solver_crash_extra_fields
            logger.error(f"[MysteryShopperScheduler] mdl.solve() 例外: {e}", exc_info=True)
            result = self._make_result(
                feasible=False,
                assignments=[],
                issues=[build_solver_crash_issue(e)],
                config=config,
                shoppers=shoppers,
                visits=visits,
                stores=stores,
                solve_time=0.0,
            )
            result.update(solver_crash_extra_fields(e))
            return result

        issues = self._detect_issues(
            assignments, visits, shoppers, stores, config, issue_statuses,
            mip_check_issues=mip_check_issues,
        )
        return self._make_result(
            feasible=True,
            assignments=assignments,
            issues=issues,
            config=config,
            shoppers=shoppers,
            visits=visits,
            stores=stores,
            solve_time=solve_time,
        )

    # -------------------------------------------------------------------------
    # MIP ソルブ本体
    # -------------------------------------------------------------------------

    def _solve_mip(
        self,
        shoppers: List[Dict],
        visits: List[Dict],
        stores: List[Dict],
        config: Dict,
    ) -> Tuple[List[Dict], float]:
        """
        docplex.mp を使った MIP による割り当て。
        変数: x[v_id][s_id][day] = 1 ならば 訪問枠v を 調査員s の day(date文字列)に割り当て

        スパース化: v ごとの担当可能 (s, day) ペアだけ変数を生成する。
        """
        from docplex.mp.model import Model
        from solvers.base.ce_limit_mip_fallback import solve_with_ce_fallback

        solve_time_sec = int(config.get("solve_time_sec", _DEFAULT_SOLVE_TIME_SEC))
        default_min_revisit = int(config.get("default_min_revisit_days", _DEFAULT_MIN_REVISIT_DAYS))

        store_map: Dict[str, Dict] = {str(s["id"]): s for s in stores}
        shopper_map: Dict[str, Dict] = {str(sh["id"]): sh for sh in shoppers}

        # 稼働可能日セット: shopper_id -> Set[str]
        avail: Dict[str, Set[str]] = {
            str(sh["id"]): set(sh.get("available_days", []))
            for sh in shoppers
        }

        # 資格セット: shopper_id -> Set[str]
        quals: Dict[str, Set[str]] = {
            str(sh["id"]): set(sh.get("qualifications", []))
            for sh in shoppers
        }

        # 店舗の必要資格: store_id -> List[str]
        req_quals: Dict[str, List[str]] = {
            str(s["id"]): s.get("required_qualifications", [])
            for s in stores
        }

        # 同一店舗×調査員の最低再訪問間隔: visit_id -> min_revisit_days
        min_revisit_by_visit: Dict[str, int] = {
            str(v["id"]): int(v.get("min_revisit_days", default_min_revisit))
            for v in visits
        }

        # 店舗ごとに同一調査員が複数回訪問する場合の間隔制約を事前計算
        # store_id -> List[visit_id]（訪問枠一覧）
        store_visits: Dict[str, List[str]] = {}
        for v in visits:
            sid = str(v["store_id"])
            store_visits.setdefault(sid, []).append(str(v["id"]))

        mdl = Model(name="mystery_shopper_scheduler")
        mdl.parameters.timelimit = solve_time_sec

        # 変数: x[(v_id, s_id, day)] ∈ {0,1}
        x: Dict[Tuple[str, str, str], Any] = {}

        # 担当可能 (v, s, day) の列挙
        for v in visits:
            v_id = str(v["id"])
            store_id = str(v["store_id"])
            r_quals = req_quals.get(store_id, [])
            for sh in shoppers:
                s_id = str(sh["id"])
                # 資格チェック
                if r_quals and not set(r_quals).issubset(quals.get(s_id, set())):
                    continue
                for day in avail.get(s_id, set()):
                    # 変数名にはLP形式で無効な文字（日付のハイフン等）を含めない。
                    # CE上限フォールバック（HiGHS）はexport_as_lp()→再読込を経由するため、
                    # ハイフンを含む変数名がCPLEXのLPライターにサニタイズ/改名され、
                    # 名前ベースの解復元（_solve_via_highs）が値を取り戻せなくなる不具合が
                    # 実際に発生した（2026-08-09、standardシナリオでHiGHS内部はobjective=22の
                    # 最適解を得ていたにもかかわらず、get_value()が全変数0を返した）。
                    safe_day = day.replace("-", "")
                    x[(v_id, s_id, day)] = mdl.binary_var(name=f"x_{v_id}_{s_id}_{safe_day}")

        if not x:
            # 担当可能な割り当て候補が1件もない
            return [], 0.0, []

        # 制約1: 各訪問枠は最大1人×1日に割り当てる
        for v in visits:
            v_id = str(v["id"])
            v_vars = [var for (vi, si, di), var in x.items() if vi == v_id]
            if v_vars:
                mdl.add_constraint(mdl.sum(v_vars) <= 1, ctname=f"assign_{v_id}")

        # 制約2: 調査員×日ごとに最大1件
        from collections import defaultdict
        shopper_day_vars: Dict[Tuple[str, str], List[Any]] = defaultdict(list)
        for (v_id, s_id, day), var in x.items():
            shopper_day_vars[(s_id, day)].append(var)
        for (s_id, day), vars_list in shopper_day_vars.items():
            if len(vars_list) > 1:
                mdl.add_constraint(mdl.sum(vars_list) <= 1,
                                    ctname=f"oneday_{s_id}_{day.replace('-', '')}")

        # 制約3: 同一調査員×同一店舗の再訪問間隔
        # 同じ店舗の訪問枠が複数ある場合、同一調査員が割り当てられるなら
        # 割り当て日が min_revisit_days 以上離れていなければならない。
        #
        # 実装方針（スライディングウィンドウ方式）:
        # 当初は「日付差が min_revisit_days 未満の全ての (v1, v2, s, d1, d2) ペア」に
        # x[v1,s,d1] + x[v2,s,d2] <= 1 を追加する総当たり方式だったが、稼働可能日数が
        # min_revisit_days と同程度以上ある場合（例: 稼働日20日・再訪問間隔28日）、
        # ほぼ全ての日付ペアが該当してしまい、制約数が O(店舗数 × 訪問枠ペア数 ×
        # 調査員数 × 稼働日数^2) に達して CPLEX Community Edition の上限
        # （1,000変数/1,000制約）を大幅に超え、フォールバック先のHiGHSでも
        # 制限時間内に解けず「解なし（実質全件未割当）」になる不具合が実際に発生した
        # （2026-08-09、標準シナリオで22件全件未割当を確認）。
        #
        # 代わりに「調査員s×店舗の任意の min_revisit_days 日間ウィンドウ内では、
        # その店舗への割り当ては高々1件」という等価な制約を、稼働可能日ごとに
        # 1本ずつ（O(稼働日数)）追加する方式に変更する。ウィンドウはその店舗の
        # 全ての訪問枠（visit_index違い）をまとめて対象にするため、訪問枠ペア数に
        # よる乗算も発生しない。
        for store_id, v_ids in store_visits.items():
            if len(v_ids) < 2:
                continue
            min_days = min(min_revisit_by_visit.get(vid, default_min_revisit) for vid in v_ids)
            for sh in shoppers:
                s_id = str(sh["id"])
                sorted_days = sorted(avail.get(s_id, set()))
                if len(sorted_days) < 2:
                    continue
                parsed = [(d, date.fromisoformat(d)) for d in sorted_days]
                for i, (d0_str, d0) in enumerate(parsed):
                    window_vars = []
                    for d_str, d in parsed[i:]:
                        if (d - d0).days >= min_days:
                            break
                        for v_id in v_ids:
                            k = (v_id, s_id, d_str)
                            if k in x:
                                window_vars.append(x[k])
                    if len(window_vars) > 1:
                        mdl.add_constraint(
                            mdl.sum(window_vars) <= 1,
                            ctname=f"revisit_{store_id}_{s_id}_{d0_str.replace('-', '')}"[:60],
                        )

        # 目的関数: lexicographic
        # 1st: 割り当て件数最大化
        # 2nd: 担当件数ばらつき（max - min）最小化
        # CPLEX MIP の multi-objective をシンプルに段階解法で実現:
        #   Step1: 充足数最大化を単目的で解く
        #   Step2: 充足数を下限に固定して、ばらつきを最小化

        total_assigned = mdl.sum(x.values())

        # Step 1
        mdl.maximize(total_assigned)
        sol1 = solve_with_ce_fallback(mdl, log_output=False)
        if sol1 is None:
            return [], 0.0, []

        best_assigned_val = int(round(sol1.get_objective_value()))
        solve_time1 = sol1.solve_details.time if sol1.solve_details else 0.0

        # sol1の妥当性は、Step2でmdlに制約・変数（lock_assigned/max_load/min_load等）が
        # 追加される「前」の、sol1が実際に解かれた時点のモデル状態に対して検証する。
        # Step2側の変数はsol1の値マップに存在しないため、Step2追加後のmdlに対して
        # 検証すると誤って「制約違反」判定になってしまう（2026-09-01発見のfalse
        # positive）。
        sol1_valid = sol1.is_valid_solution(tolerance=1e-6)

        # Step 2: ばらつき最小化（充足数を下限に固定）
        mdl.add_constraint(total_assigned >= best_assigned_val, ctname="lock_assigned")

        # 調査員ごとの担当件数
        shopper_loads: Dict[str, Any] = {}
        for sh in shoppers:
            s_id = str(sh["id"])
            s_vars = [var for (vi, si, di), var in x.items() if si == s_id]
            shopper_loads[s_id] = mdl.sum(s_vars) if s_vars else mdl.linear_expr(constant=0)

        n_shoppers = len(shoppers)
        if n_shoppers >= 2:
            max_load = mdl.integer_var(lb=0, ub=len(visits), name="max_load")
            min_load = mdl.integer_var(lb=0, ub=len(visits), name="min_load")
            for s_id, load_expr in shopper_loads.items():
                mdl.add_constraint(max_load >= load_expr, ctname=f"maxload_{s_id}")
                mdl.add_constraint(min_load <= load_expr, ctname=f"minload_{s_id}")
            range_expr = max_load - min_load
            mdl.minimize(range_expr)
        else:
            mdl.minimize(mdl.linear_expr(constant=0))

        sol2 = solve_with_ce_fallback(mdl, log_output=False)
        solve_time2 = sol2.solve_details.time if (sol2 and sol2.solve_details) else 0.0

        # sol2はここまでの最終mdl状態（Step2の制約・変数を含む）に対して解かれて
        # いるため、solve_with_ce_fallback()呼び出し直後・これ以上mdlを変更しない
        # このタイミングで検証すれば、sol2が実際に解かれた状態と一致する。
        sol2_valid = sol2.is_valid_solution(tolerance=1e-6) if sol2 is not None else None

        sol = sol2 if (sol2 is not None) else sol1
        sol_valid = sol2_valid if (sol2 is not None) else sol1_valid
        total_solve_time = solve_time1 + solve_time2

        mip_check_issues: List[Dict] = []
        if not sol_valid:
            mip_check_issues.append({
                "id": "mip_solution_invalid",
                "severity": "CRITICAL",
                "category": "SOLVER",
                "title": "解の制約充足検証に失敗（解チェッカー）",
                "message": "CPLEX/HiGHSが返した解が、モデルに追加した制約"
                           "（訪問枠1件1人1日・調査員×日1件・再訪問間隔）を満たしていません。",
                "relatedContainerIds": [],
            })

        # 解抽出
        assignments: List[Dict] = []
        for (v_id, s_id, day), var in x.items():
            val = sol.get_value(var)
            if val is not None and val > 0.5:
                assignments.append({
                    "visit_id": v_id,
                    "shopper_id": s_id,
                    "day": day,
                })

        return assignments, total_solve_time, mip_check_issues

    # -------------------------------------------------------------------------
    # Issue 検知
    # -------------------------------------------------------------------------

    def _detect_issues(
        self,
        assignments: List[Dict],
        visits: List[Dict],
        shoppers: List[Dict],
        stores: List[Dict],
        config: Dict,
        issue_statuses: Dict,
        mip_check_issues: List[Dict],
    ) -> List[Dict]:
        from solvers.base.issue_rules import (
            build_full_unassignment_issue,
            build_mystery_shopper_scheduler_contexts,
            run_issue_rules,
        )

        issues: List[Dict] = list(mip_check_issues)

        checker_ctxs = build_mystery_shopper_scheduler_contexts(assignments)
        issues.extend(run_issue_rules(
            domain="MysteryShopperScheduler", contexts=checker_ctxs, issue_statuses=issue_statuses,
        ))

        # 全件未割当チェック
        anomaly = build_full_unassignment_issue(
            assigned_count=len(assignments),
            total_count=len(visits),
            entity_label="店舗訪問枠",
            extra_hint="MIP解抽出（sol.get_value）",
        )
        if anomaly:
            issues.append(anomaly)

        # 未割当訪問枠一覧
        assigned_visit_ids: Set[str] = {a["visit_id"] for a in assignments}
        store_map = {str(s["id"]): s for s in stores}
        visit_map = {str(v["id"]): v for v in visits}

        for v in visits:
            v_id = str(v["id"])
            if v_id in assigned_visit_ids:
                continue
            if issue_statuses.get(f"unassigned_visit_{v_id}") == "ACCEPTED":
                continue
            store = store_map.get(str(v.get("store_id", "")), {})
            store_name = store.get("name", str(v.get("store_id", "")))
            issues.append({
                "id": f"unassigned_visit_{v_id}",
                "severity": "WARNING",
                "title": f"未割当: {store_name}（訪問枠{v.get('visit_index', '')}）",
                "message": (
                    f"店舗「{store_name}」の訪問枠{v.get('visit_index', '')}が"
                    f"割り当てられませんでした。"
                    f"稼働日カレンダーまたは資格要件を確認してください。"
                ),
                "relatedContainerIds": [],
            })

        # 再訪問間隔チェック（ソフト制約の事後検証）
        default_min_revisit = int(config.get("default_min_revisit_days", _DEFAULT_MIN_REVISIT_DAYS))
        shopper_store_days: Dict[Tuple[str, str], List[str]] = {}
        for a in assignments:
            v = visit_map.get(a["visit_id"], {})
            key = (a["shopper_id"], str(v.get("store_id", "")))
            shopper_store_days.setdefault(key, []).append(a["day"])

        for (s_id, store_id), days in shopper_store_days.items():
            if len(days) < 2:
                continue
            sorted_days = sorted(days)
            store = store_map.get(store_id, {})
            store_name = store.get("name", store_id)
            shopper = next((sh for sh in shoppers if str(sh["id"]) == s_id), {})
            shopper_name = shopper.get("name", s_id)
            for i in range(len(sorted_days) - 1):
                try:
                    d1 = date.fromisoformat(sorted_days[i])
                    d2 = date.fromisoformat(sorted_days[i + 1])
                    gap = (d2 - d1).days
                    # min_revisit_days を店舗の最初の訪問枠から取得
                    v_for_store = next(
                        (v for v in visits if str(v.get("store_id", "")) == store_id), {}
                    )
                    min_days = int(v_for_store.get("min_revisit_days", default_min_revisit))
                    if gap < min_days:
                        issue_id = f"revisit_short_{s_id}_{store_id}_{sorted_days[i]}"
                        if issue_statuses.get(issue_id) == "ACCEPTED":
                            continue
                        issues.append({
                            "id": issue_id,
                            "severity": "WARNING",
                            "title": f"再訪問間隔不足: {store_name} × {shopper_name}",
                            "message": (
                                f"調査員「{shopper_name}」が店舗「{store_name}」を"
                                f"{sorted_days[i]} と {sorted_days[i+1]} に訪問予定です（間隔{gap}日）。"
                                f"最低再訪問間隔（{min_days}日）を下回っています。"
                            ),
                            "relatedContainerIds": [],
                        })
                except ValueError:
                    continue

        return issues

    # -------------------------------------------------------------------------
    # 結果組み立て
    # -------------------------------------------------------------------------

    def _make_result(
        self,
        feasible: bool,
        assignments: List[Dict],
        issues: List[Dict],
        config: Dict,
        shoppers: List[Dict],
        visits: List[Dict],
        stores: List[Dict],
        solve_time: float = 0.0,
    ) -> dict:
        # KPI計算
        total_visits = len(visits)
        assigned_count = len(assignments)
        coverage_rate = assigned_count / total_visits if total_visits > 0 else 0.0

        # 調査員ごとの担当件数
        shopper_loads: Dict[str, int] = {str(sh["id"]): 0 for sh in shoppers}
        for a in assignments:
            s_id = a["shopper_id"]
            if s_id in shopper_loads:
                shopper_loads[s_id] += 1

        load_values = list(shopper_loads.values())
        max_load = max(load_values) if load_values else 0
        min_load = min(load_values) if load_values else 0
        load_range = max_load - min_load

        kpi = {
            "total_visits": total_visits,
            "assigned_count": assigned_count,
            "unassigned_count": total_visits - assigned_count,
            "coverage_rate": coverage_rate,
            "max_shopper_load": max_load,
            "min_shopper_load": min_load,
            "load_range": load_range,
            "solve_time": solve_time,
        }

        solution = {
            "feasible": feasible,
            "assignments": assignments,
            "shopper_loads": shopper_loads,
            "kpi": kpi,
        }

        return {
            "status": "ok",
            "feasible": feasible,
            "metadata": {"problem_class": "MysteryShopperScheduler"},
            "solutions": [solution],
            "issues": issues,
            "_solver_version": _SOLVER_VERSION,
        }

    def _ce_limit_result(self, config, shoppers, visits, stores) -> dict:
        return self._make_result(
            feasible=False,
            assignments=[],
            issues=[{
                "id": "solve_failed",
                "severity": "CRITICAL",
                "title": "CPLEXの無料版で扱える件数を超えています",
                "message": "訪問枠数または調査員数を減らすか、正規ライセンスのご利用をご検討ください。",
                "relatedContainerIds": [],
            }],
            config=config,
            shoppers=shoppers,
            visits=visits,
            stores=stores,
        )


class _CeLimitError(Exception):
    pass