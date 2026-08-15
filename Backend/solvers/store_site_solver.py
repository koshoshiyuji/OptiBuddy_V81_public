"""
store_site_solver.py — StoreSite（小売店舗ネットワーク配置最適化）ソルバー

solver_input keys:
  problem_class, areas, candidates, config, meta, issue_statuses

使用技術: docplex.mp.model (CPLEX MIP)
  - open[j]: binary_var — 候補地 j を開設するか
  - assign[i][j]: binary_var — エリア i を候補地 j に割り当てるか
  - over[j]: continuous_var >= 0 — 稼働率 80% 超過量
  - util_max: continuous_var — 開設店舗の稼働率最大値（ばらつき最小化用）
  - util_min: continuous_var — 開設店舗の稼働率最小値（ばらつき最小化用）
"""

import logging
from typing import Any, Dict, List, Optional

from solvers.base.ce_limit_mip_fallback import solve_with_ce_fallback
from solvers.base.solver_error_result import build_solver_crash_issue, solver_crash_extra_fields

logger = logging.getLogger(__name__)

_SOLVER_VERSION = "store_site_v1.1"


class StoreSiteSolver:
    def __init__(self, solver_input: dict) -> None:
        self.dsl = solver_input

    def solve(self) -> dict:
        areas      = self.dsl.get("areas", [])
        candidates = self.dsl.get("candidates", [])
        config     = self.dsl.get("config", {})
        issue_statuses = self.dsl.get("issue_statuses", {})

        # ── バリデーション ──────────────────────────────────────
        if not areas:
            return self._make_result(
                feasible=False,
                issues=[{
                    "id": "no_areas", "severity": "CRITICAL",
                    "title": "エリアが定義されていません",
                    "message": "最低1件のエリアを定義してください。",
                    "relatedContainerIds": [],
                }],
            )
        if not candidates:
            return self._make_result(
                feasible=False,
                issues=[{
                    "id": "no_candidates", "severity": "CRITICAL",
                    "title": "候補地が定義されていません",
                    "message": "最低1件の店舗候補地を定義してください。",
                    "relatedContainerIds": [],
                }],
            )

        try:
            return self._build_and_solve(areas, candidates, config, issue_statuses)
        except Exception as e:
            logger.error(f"[StoreSite] ソルブ中に例外: {e}", exc_info=True)
            # [2026-07-28] id は "solve_failed" に統一（独自idだと制約見直しタブが
            # Feasible誤判定する。solvers/base/solver_error_result.py 参照）。
            result = self._make_result(
                feasible=False,
                issues=[build_solver_crash_issue(e)],
            )
            result.update(solver_crash_extra_fields(e))
            return result

    # ─────────────────────────────────────────────────────────────
    # MIP モデル構築・ソルブ
    # ─────────────────────────────────────────────────────────────

    def _build_and_solve(
        self,
        areas: List[Dict],
        candidates: List[Dict],
        config: Dict,
        issue_statuses: Dict,
    ) -> dict:
        from docplex.mp.model import Model

        max_distance_km           = config.get("max_distance_km", 1e9)
        unmet_penalty             = config.get("unmet_penalty", 1_000_000)
        store_count_weight        = config.get("store_count_weight", 10)
        utilization_penalty_w     = config.get("utilization_penalty_weight", 5_000)
        utilization_variance_w    = config.get("utilization_variance_weight", 1_000)
        distance_penalty_w        = config.get("distance_penalty_weight", 100)
        utilization_threshold     = config.get("utilization_threshold", 0.8)
        time_limit_sec            = config.get("time_limit_sec", 30)

        # エリア・候補地のIDマップ
        area_ids      = [str(a["id"]) for a in areas]
        candidate_ids = [str(c["id"]) for c in candidates]
        area_map      = {str(a["id"]): a for a in areas}
        cand_map      = {str(c["id"]): c for c in candidates}

        # 到達可能ペアの事前計算
        reachable: Dict[str, List[str]] = {ai: [] for ai in area_ids}
        for ai in area_ids:
            for cj in candidate_ids:
                dist = self._distance(area_map[ai], cand_map[cj])
                if dist <= max_distance_km:
                    reachable[ai].append(cj)

        mdl = Model(name="StoreSite")
        mdl.parameters.timelimit = time_limit_sec

        # ── 決定変数 ──────────────────────────────────────────
        # open[j]: 候補地 j を開設するか
        open_var: Dict[str, Any] = {
            cj: mdl.binary_var(name=f"open_{cj}") for cj in candidate_ids
        }

        # assign[i][j]: エリア i を候補地 j に割り当てるか（到達可能なペアのみ）
        assign_var: Dict[str, Dict[str, Any]] = {}
        for ai in area_ids:
            assign_var[ai] = {}
            for cj in reachable[ai]:
                assign_var[ai][cj] = mdl.binary_var(name=f"assign_{ai}_{cj}")

        # over[j]: 稼働率超過量（連続変数、>=0）
        over_var: Dict[str, Any] = {
            cj: mdl.continuous_var(lb=0, name=f"over_{cj}") for cj in candidate_ids
        }

        # util_max, util_min: 稼働率ばらつき最小化用補助変数（ヒアリング3節 第3優先目的）
        # util_max - util_min を最小化することで稼働率のレンジ（ばらつき）を抑える
        util_max_var = mdl.continuous_var(lb=0, ub=1, name="util_max")
        util_min_var = mdl.continuous_var(lb=0, ub=1, name="util_min")

        # ── ハード制約 ────────────────────────────────────────
        # 1. assign[i][j] <= open[j]（開設していない候補地への割当禁止）
        for ai in area_ids:
            for cj, av in assign_var[ai].items():
                mdl.add_constraint(av <= open_var[cj], ctname=f"open_req_{ai}_{cj}")

        # 2. sum_j assign[i][j] <= 1（各エリアは高々1店舗に割り当て）
        for ai in area_ids:
            if assign_var[ai]:
                mdl.add_constraint(
                    mdl.sum(assign_var[ai].values()) <= 1,
                    ctname=f"single_assign_{ai}",
                )

        # 3. 容量制約: sum_i demand[i]*assign[i][j] <= capacity[j]
        for cj in candidate_ids:
            cap = cand_map[cj].get("capacity", 1e9)
            demand_sum = mdl.sum(
                area_map[ai].get("demand", 0) * assign_var[ai][cj]
                for ai in area_ids if cj in assign_var[ai]
            )
            mdl.add_constraint(demand_sum <= cap, ctname=f"capacity_{cj}")

        # 4. 稼働率超過補助変数: over[j] >= util[j] - threshold
        for cj in candidate_ids:
            cap = cand_map[cj].get("capacity", 1)
            if cap <= 0:
                continue
            demand_sum = mdl.sum(
                area_map[ai].get("demand", 0) * assign_var[ai][cj]
                for ai in area_ids if cj in assign_var[ai]
            )
            mdl.add_constraint(
                over_var[cj] >= demand_sum / cap - utilization_threshold,
                ctname=f"over_{cj}",
            )

        # 5. 稼働率ばらつき最小化用制約（ヒアリング3節 第3優先目的）
        #    util_max >= util[j]  （全j）
        #    util_min <= util[j] + (1 - open[j]) * 1  （開設していない候補地は除外）
        #    ※ open[j]=0 の候補地は util[j]=0 となるため、util_min の下限計算から除外する
        #      Big-M=1 で (1-open[j]) を加算することで、未開設候補地を util_min 計算から除外
        for cj in candidate_ids:
            cap = cand_map[cj].get("capacity", 1)
            if cap <= 0:
                continue
            demand_sum = mdl.sum(
                area_map[ai].get("demand", 0) * assign_var[ai][cj]
                for ai in area_ids if cj in assign_var[ai]
            )
            util_expr = demand_sum / cap
            # util_max >= util[j] * open[j]（開設店舗の稼働率の最大値を捕捉）
            mdl.add_constraint(
                util_max_var >= util_expr - (1 - open_var[cj]),
                ctname=f"util_max_{cj}",
            )
            # util_min <= util[j] + (1 - open[j])（未開設候補地を除外: open[j]=0 なら右辺>=1 で制約が緩む）
            mdl.add_constraint(
                util_min_var <= util_expr + (1 - open_var[cj]),
                ctname=f"util_min_{cj}",
            )

        # ── 目的関数 ──────────────────────────────────────────
        # 項1: 固定費
        fixed_cost_term = mdl.sum(
            cand_map[cj].get("fixed_cost", 0) * open_var[cj]
            for cj in candidate_ids
        )
        # 項2: 変動費（supply_cost × demand）
        supply_cost_term = mdl.sum(
            self._supply_cost(area_map[ai], cand_map[cj])
            * area_map[ai].get("demand", 0)
            * assign_var[ai][cj]
            for ai in area_ids
            for cj in reachable[ai]
        )
        # 項3: 未割当ペナルティ（到達可能な候補地がないエリアも含む）
        unmet_term = mdl.sum(
            area_map[ai].get("demand", 0)
            * (1 - mdl.sum(assign_var[ai].values()) if assign_var[ai] else 1)
            for ai in area_ids
        )
        # 項4: 開設店舗数の最小化（副次）
        store_count_term = mdl.sum(open_var.values())
        # 項5: 稼働率超過ペナルティ（80%超過ソフト制約）
        utilization_penalty_term = mdl.sum(over_var.values())
        # 項6: 距離ペナルティ
        distance_penalty_term = mdl.sum(
            self._distance(area_map[ai], cand_map[cj]) * assign_var[ai][cj]
            for ai in area_ids
            for cj in reachable[ai]
        )
        # 項7: 稼働率ばらつき最小化（util_max - util_min）（ヒアリング3節 第3優先目的）
        #      第3優先のため utilization_variance_weight は utilization_penalty_weight より小さい値を使う
        utilization_variance_term = util_max_var - util_min_var

        # ヒアリング目的関数 項1+2+3+4+5+6+7 の重み付き和
        # 優先順位: 項1+2+3（コスト最小化）> 項4（開設数最小化）> 項5（稼働率超過）> 項7（ばらつき）> 項6（距離）
        objective = (
            fixed_cost_term
            + supply_cost_term
            + unmet_penalty * unmet_term                              # 項3: 未割当ペナルティ（全エリア対象）
            + store_count_weight * store_count_term                   # 項4: 開設数最小化（副次）
            + utilization_penalty_w * utilization_penalty_term        # 項5: 稼働率超過
            + utilization_variance_w * utilization_variance_term      # 項7: 稼働率ばらつき最小化（第3優先）
            + distance_penalty_w * distance_penalty_term              # 項6: 距離
        )
        mdl.minimize(objective)

        # ── ソルブ ────────────────────────────────────────────
        # 2026-07-26追加: CPLEXの無料版（Community Edition）の上限
        # （1000変数/1000制約、CPLEX Error 1016）に当たった場合のみ、
        # モデルを分解せずHiGHS（オープンソース、上限なし）へ自動的に
        # 切り替える。完成済みのモデルをそのままLP形式でエクスポートして
        # 渡すだけなので、ドメイン固有の追加実装は不要（詳細は
        # solvers/base/ce_limit_mip_fallback.py参照）。通常時（上限未満）は
        # 従来通りmdl.solve()がそのまま使われる。
        sol = solve_with_ce_fallback(mdl, log_output=False)

        if sol is None:
            return self._make_result(
                feasible=False,
                issues=[{
                    "id": "infeasible", "severity": "CRITICAL",
                    "title": "実行可能解が見つかりませんでした",
                    "message": "容量制約・距離制約を満たす店舗配置が存在しません。候補地の容量またはmax_distance_kmを緩和してください。",
                    "relatedContainerIds": [],
                }],
            )

        # 2026-07-24追加: 解チェッカー（DESIGN_2026-07-21 9節）。
        # docplex.mp（MIP）は SolveSolution.is_valid_solution() で
        # 「変数の型・範囲と全制約の充足」を一括判定できるネイティブ機能を
        # 持つ。既存の解オブジェクトへの評価であり追加探索を伴わないため
        # コストは軽い（O(n)相当、常に同期実行）。ソルバー公式APIによる
        # 検算のため、自作の抽出・検算コードのバグに引きずられない
        # （9-1節「③解を取り出す側のコードのバグ」に対する優先手段）。
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
            logger.warning(f"[StoreSite] is_valid_solution() 呼び出しに失敗（検証スキップ）: {e}")

        # ── 解抽出 ────────────────────────────────────────────
        opened_stores: List[Dict] = []
        for cj in candidate_ids:
            if sol.get_value(open_var[cj]) > 0.5:
                assigned_areas = []
                total_demand = 0.0
                for ai in area_ids:
                    if cj in assign_var[ai] and sol.get_value(assign_var[ai][cj]) > 0.5:
                        assigned_areas.append(ai)
                        total_demand += area_map[ai].get("demand", 0)
                cap = cand_map[cj].get("capacity", 1)
                util = total_demand / cap if cap > 0 else 0.0
                opened_stores.append({
                    "candidate_id": cj,
                    "name": cand_map[cj].get("name", cj),
                    "fixed_cost": cand_map[cj].get("fixed_cost", 0),
                    "capacity": cap,
                    "assigned_areas": assigned_areas,
                    "total_demand": total_demand,
                    "utilization": round(util, 4),
                })

        unassigned_areas: List[Dict] = []
        for ai in area_ids:
            assigned = any(
                cj in assign_var[ai] and sol.get_value(assign_var[ai][cj]) > 0.5
                for cj in candidate_ids
            )
            if not assigned:
                unassigned_areas.append({
                    "area_id": ai,
                    "name": area_map[ai].get("name", ai),
                    "demand": area_map[ai].get("demand", 0),
                })

        # KPI計算
        total_fixed_cost   = sum(s["fixed_cost"] for s in opened_stores)
        total_supply_cost  = sum(
            self._supply_cost(area_map[ai], cand_map[cj])
            * area_map[ai].get("demand", 0)
            for ai in area_ids
            for cj in reachable[ai]
            if cj in assign_var[ai] and sol.get_value(assign_var[ai][cj]) > 0.5
        )
        total_unmet_demand = sum(a["demand"] for a in unassigned_areas)
        objective_value    = float(sol.objective_value)

        # 稼働率ばらつきKPI
        utilizations = [s["utilization"] for s in opened_stores]
        util_range = (
            round(max(utilizations) - min(utilizations), 4)
            if len(utilizations) >= 2 else 0.0
        )

        # イシュー検知
        issues = self._detect_issues(
            opened_stores, unassigned_areas, areas, candidates,
            config, issue_statuses,
        )
        issues.extend(mip_check_issues)

        # 2026-07-24追加: 解チェッカー（DESIGN_2026-07-21 4節）。容量制約・
        # 到達可能距離（max_distance_km）はMIPモデル側のハード制約
        # （制約1,3）だが、is_valid_solution()はモデル自体が正しいかまでは
        # 保証しない（9-1節②「モデルはあるが値/参照フィールドが翻訳時に
        # 間違っている」バグ種別）。DSLの生値（area/candidate）から
        # 独立に再計算し、opened_storesの値と突き合わせる。
        # いずれもO(n)（総assignment件数に比例。全候補地×全エリアの
        # 総当たりではない）ため常に同期実行。
        from solvers.base.issue_rules import run_issue_rules, build_store_site_contexts
        area_map = {str(a["id"]): a for a in areas}
        cand_map_for_checker = {str(c["id"]): c for c in candidates}
        max_distance_km = config.get("max_distance_km", 1e9)
        checker_ctxs = build_store_site_contexts(
            opened_stores, area_map, max_distance_km, self._distance, cand_map_for_checker,
        )
        issues.extend(run_issue_rules(domain="StoreSite", contexts=checker_ctxs, issue_statuses=issue_statuses))

        # 全件未割当異常検知
        from solvers.base.issue_rules import build_full_unassignment_issue
        anomaly = build_full_unassignment_issue(
            assigned_count=len(opened_stores),
            total_count=len(candidates),
            entity_label="候補地",
            extra_hint="sol.get_value(open_var[j])の解抽出処理",
        )
        if anomaly:
            issues.insert(0, anomaly)

        return self._make_result(
            feasible=True,
            opened_stores=opened_stores,
            unassigned_areas=unassigned_areas,
            kpi={
                "total_fixed_cost":       total_fixed_cost,
                "total_supply_cost":      total_supply_cost,
                "total_unmet_demand":     total_unmet_demand,
                "opened_store_count":     len(opened_stores),
                "unassigned_area_count":  len(unassigned_areas),
                "objective_value":        objective_value,
                "utilization_range":      util_range,
                "coverage_rate": round(
                    1 - total_unmet_demand / max(sum(a.get("demand", 0) for a in areas), 1), 4
                ),
            },
            issues=issues,
        )

    # ─────────────────────────────────────────────────────────────
    # ヘルパー
    # ─────────────────────────────────────────────────────────────

    def _distance(self, area: Dict, candidate: Dict) -> float:
        """エリア-候補地間距離を取得（距離行列 or lat/lng ユークリッド代替）。"""
        dist_matrix = self.dsl.get("config", {}).get("distance_matrix", {})
        ai = str(area["id"])
        cj = str(candidate["id"])
        if dist_matrix and ai in dist_matrix and cj in dist_matrix[ai]:
            return float(dist_matrix[ai][cj])
        # lat/lng が指定されていれば簡易ユークリッド距離（度換算×111km）
        alat = area.get("lat", 0); alng = area.get("lng", 0)
        clat = candidate.get("lat", 0); clng = candidate.get("lng", 0)
        return ((alat - clat) ** 2 + (alng - clng) ** 2) ** 0.5 * 111.0

    def _supply_cost(self, area: Dict, candidate: Dict) -> float:
        """エリア-候補地間の1需要単位あたり変動費（距離比例またはDSL指定）。"""
        supply_costs = self.dsl.get("config", {}).get("supply_cost_matrix", {})
        ai = str(area["id"])
        cj = str(candidate["id"])
        if supply_costs and ai in supply_costs and cj in supply_costs[ai]:
            return float(supply_costs[ai][cj])
        dist = self._distance(area, candidate)
        cost_per_km = self.dsl.get("config", {}).get("supply_cost_per_km", 10)
        return dist * cost_per_km

    def _detect_issues(
        self,
        opened_stores: List[Dict],
        unassigned_areas: List[Dict],
        areas: List[Dict],
        candidates: List[Dict],
        config: Dict,
        issue_statuses: Dict,
    ) -> List[Dict]:
        issues = []
        utilization_threshold = config.get("utilization_threshold", 0.8)

        # 未割当エリアの警告
        for ua in unassigned_areas:
            iid = f"unassigned_area_{ua['area_id']}"
            if issue_statuses.get(iid) != "ACCEPTED":
                issues.append({
                    "id": iid,
                    "severity": "CRITICAL",
                    "title": f"エリア未割当: {ua['name']}",
                    "message": (
                        f"エリア「{ua['name']}」（需要 {ua['demand']}）を"
                        f"担当できる店舗がありません。候補地の対応距離または容量を見直してください。"
                    ),
                    "relatedContainerIds": [],
                })

        # 高稼働率警告
        for s in opened_stores:
            if s["utilization"] > utilization_threshold:
                iid = f"high_utilization_{s['candidate_id']}"
                if issue_statuses.get(iid) != "ACCEPTED":
                    issues.append({
                        "id": iid,
                        "severity": "WARNING",
                        "title": f"稼働率超過: {s['name']}",
                        "message": (
                            f"店舗「{s['name']}」の稼働率が "
                            f"{round(s['utilization']*100, 1)}% で"
                            f"目標 {round(utilization_threshold*100)}% を超えています。"
                        ),
                        "relatedContainerIds": [],
                    })

        return issues

    def _make_result(
        self,
        feasible: bool,
        opened_stores: Optional[List[Dict]] = None,
        unassigned_areas: Optional[List[Dict]] = None,
        kpi: Optional[Dict] = None,
        issues: Optional[List[Dict]] = None,
    ) -> dict:
        # DSLのmeta（instance_name / note）をUIパススルー
        meta_dsl = self.dsl.get("meta", {})
        return {
            "status":   "ok" if feasible else "infeasible",
            "feasible": feasible,
            "metadata": {
                "problem_class":    "StoreSite",
                "_solver_version":  _SOLVER_VERSION,
                "instance_name":    meta_dsl.get("instance_name", ""),
                "note":             meta_dsl.get("note", ""),
            },
            "solutions": [{
                "feasible":         feasible,
                "opened_stores":    opened_stores or [],
                "unassigned_areas": unassigned_areas or [],
                "kpi":              kpi or {},
            }],
            "issues": issues or [],
            "_solver_version": _SOLVER_VERSION,
        }
