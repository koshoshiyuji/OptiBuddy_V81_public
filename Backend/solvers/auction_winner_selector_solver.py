"""
Backend/solvers/auction_winner_selector_solver.py

AuctionWinnerSelectorSolver — 組合せオークション落札者決定 (CSPLib prob063)
=============================================================================

集合パッキング問題（Set Packing ILP）として定式化。
docplex.mp（CPLEX MIP）使用。CE上限フォールバックは ce_limit_mip_fallback.py 経由。

solver_input keys:
  - problem_class: str ("AuctionWinnerSelector")
  - bids: List[Dict]  # id, name, price, item_ids (List[str])
  - items: List[Dict] # id, name, description
  - config: Dict      # time_limit_sec, min_revenue
  - issue_statuses: Dict[str, str]
"""

import logging
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

# ── ペナルティ定数 ──────────────────────────────────────────
# 全入札を落札しない場合のコストを表現するため、落札0件は feasible=False として扱う。
# 入札が1件もない / 商品ロットが1件もない場合は infeasible。


class AuctionWinnerSelectorSolver:
    """
    組合せオークション落札者決定ソルバー（docplex.mp / CPLEX MIP）。

    各入札 b に入札額 price[b] と対象商品ロット集合 items(b) を持たせ、
    0/1変数 x[b]（落札=1 / 不落札=0）を用いて Sum(price[b] * x[b]) を最大化する。
    制約: 各商品ロット j について Sum(x[b] for b in bids containing j) <= 1。
    """

    def __init__(self, solver_input: Dict[str, Any]) -> None:
        self.dsl = solver_input

    def solve(self) -> Dict[str, Any]:
        bids         = self.dsl.get("bids", [])
        items        = self.dsl.get("items", [])
        config       = self.dsl.get("config", {})
        issue_statuses = self.dsl.get("issue_statuses", {})
        time_limit   = float(config.get("time_limit_sec", 30.0))
        min_revenue  = float(config.get("min_revenue", 0.0))

        # ── バリデーション ───────────────────────────────────
        if not bids:
            return self._make_result(
                feasible=False,
                winners=[],
                revenue=0.0,
                issues=[{
                    "id": "solve_failed",
                    "severity": "CRITICAL",
                    "title": "入札データがありません",
                    "message": "入札（bids）が1件もありません。最低1件の入札が必要です。",
                    "relatedContainerIds": [],
                }],
                solve_time=0.0,
            )
        if not items:
            return self._make_result(
                feasible=False,
                winners=[],
                revenue=0.0,
                issues=[{
                    "id": "solve_failed",
                    "severity": "CRITICAL",
                    "title": "商品ロットデータがありません",
                    "message": "商品ロット（items）が1件もありません。最低1件の商品ロットが必要です。",
                    "relatedContainerIds": [],
                }],
                solve_time=0.0,
            )

        # ── MIPモデル構築 ────────────────────────────────────
        try:
            from docplex.mp.model import Model
        except ImportError as e:
            return self._make_result(
                feasible=False, winners=[], revenue=0.0,
                issues=[{
                    "id": "solve_failed",
                    "severity": "CRITICAL",
                    "title": "docplex未インストール",
                    "message": f"docplex が見つかりません: {e}",
                    "relatedContainerIds": [],
                }],
                solve_time=0.0,
            )

        from solvers.base.ce_limit_mip_fallback import solve_with_ce_fallback
        from solvers.base.issue_rules import build_full_unassignment_issue

        import time
        t0 = time.perf_counter()

        mdl = Model(name="auction_winner_selector")

        bid_ids   = [str(b["id"]) for b in bids]
        item_ids  = {str(it["id"]) for it in items}
        bid_map   = {str(b["id"]): b for b in bids}
        item_map  = {str(it["id"]): it for it in items}

        # 各入札の 0/1 変数
        x = {bid_id: mdl.binary_var(name=f"x_{bid_id}") for bid_id in bid_ids}

        # 目的関数: 落札総額の最大化
        # ヒアリング目的関数 項1: Sum(price[b] * x[b]) の最大化（全入札対象）
        mdl.maximize(mdl.sum(float(bid_map[bid_id].get("price", 0)) * x[bid_id] for bid_id in bid_ids))

        # 制約: 各商品ロット j について高々1件の入札にしか落札されない
        for item_id in item_ids:
            bids_containing_item = [
                bid_id for bid_id in bid_ids
                if item_id in [str(iid) for iid in bid_map[bid_id].get("item_ids", [])]
            ]
            if len(bids_containing_item) > 1:
                mdl.add_constraint(
                    mdl.sum(x[bid_id] for bid_id in bids_containing_item) <= 1,
                    ctname=f"item_{item_id}_uniqueness"
                )

        # 最低落札総額制約（config.min_revenue > 0 の場合のみ）
        if min_revenue > 0:
            mdl.add_constraint(
                mdl.sum(float(bid_map[bid_id].get("price", 0)) * x[bid_id] for bid_id in bid_ids) >= min_revenue,
                ctname="min_revenue"
            )

        # ── 求解 ─────────────────────────────────────────────
        try:
            sol = solve_with_ce_fallback(mdl, log_output=False, time_limit=time_limit)
        except Exception as e:
            from solvers.base.solver_error_result import build_solver_crash_issue, solver_crash_extra_fields
            elapsed = time.perf_counter() - t0
            logger.error(f"[AuctionWinnerSelector] mdl.solve() 例外: {e}", exc_info=True)
            result = self._make_result(
                feasible=False, winners=[], revenue=0.0,
                issues=[build_solver_crash_issue(e)],
                solve_time=elapsed,
            )
            result.update(solver_crash_extra_fields(e))
            return result

        elapsed = time.perf_counter() - t0

        if sol is None:
            return self._make_result(
                feasible=False, winners=[], revenue=0.0,
                issues=[{
                    "id": "solve_failed",
                    "severity": "CRITICAL",
                    "title": "最適解なし",
                    "message": "制約を満たす落札者の組み合わせが見つかりませんでした。"
                               "min_revenue の設定値を下げるか、入札データを確認してください。",
                    "relatedContainerIds": [],
                }],
                solve_time=elapsed,
            )

        # ── 解抽出 ───────────────────────────────────────────
        winners: List[Dict[str, Any]] = []
        for bid_id in bid_ids:
            val = sol.get_value(x[bid_id])
            if val is not None and val > 0.5:
                bid = bid_map[bid_id]
                winner_items = [
                    item_map.get(str(iid), {"id": str(iid), "name": str(iid)})
                    for iid in bid.get("item_ids", [])
                ]
                winners.append({
                    "bid_id":   bid_id,
                    "bid_name": bid.get("name", bid_id),
                    "price":    float(bid.get("price", 0)),
                    "item_ids": [str(iid) for iid in bid.get("item_ids", [])],
                    "items":    winner_items,
                })

        total_revenue = sum(w["price"] for w in winners)

        # ── 異常検知 ─────────────────────────────────────────
        issues: List[Dict[str, Any]] = []

        anomaly = build_full_unassignment_issue(
            assigned_count=len(winners),
            total_count=len(bids),
            entity_label="入札",
            extra_hint="sol.get_value() での解抽出処理",
        )
        # 全入札が落札されないのは正常（競合で弾かれる場合がある）ため、
        # 全件落札0は異常だが、一部落札0は正常。anomalyチェックは参考情報として留める。
        # ただし商品ロットが全て競合しており物理的に落札0件にしかならない場合を除き、
        # 落札0件は設定ミスの可能性が高いため INFO として通知する。
        if len(winners) == 0 and anomaly:
            issues.append(anomaly)

        # 落札されなかった商品ロットを特定
        sold_item_ids: set = set()
        for w in winners:
            sold_item_ids.update(w["item_ids"])
        unsold_items = [it for it in items if str(it["id"]) not in sold_item_ids]

        if unsold_items:
            unsold_names = ", ".join(it.get("name", str(it["id"])) for it in unsold_items[:5])
            suffix = f"（他{len(unsold_items) - 5}件）" if len(unsold_items) > 5 else ""
            issues.append({
                "id":       "unsold_items",
                "severity": "INFO",
                "title":    f"未落札商品ロットあり（{len(unsold_items)}件）",
                "message":  f"以下の商品ロットはいずれの入札にも含まれていないか、"
                            f"競合により落札されませんでした: {unsold_names}{suffix}",
                "relatedContainerIds": [],
            })

        # ACCEPTED イシューを除外
        filtered_issues = [
            iss for iss in issues
            if issue_statuses.get(iss["id"]) != "ACCEPTED"
        ]

        return self._make_result(
            feasible=True,
            winners=winners,
            revenue=total_revenue,
            issues=filtered_issues,
            solve_time=elapsed,
            obj_value=float(sol.objective_value) if sol.objective_value is not None else total_revenue,
        )

    @staticmethod
    def _make_result(
        feasible: bool,
        winners: List[Dict[str, Any]],
        revenue: float,
        issues: List[Dict[str, Any]],
        solve_time: float,
        obj_value: Optional[float] = None,
    ) -> Dict[str, Any]:
        return {
            "status":    "ok",
            "feasible":  feasible,
            "metadata":  {"problem_class": "AuctionWinnerSelector"},
            "solutions": [{
                "feasible": feasible,
                "winners":  winners,
                "kpi": {
                    "total_revenue":  revenue,
                    "winner_count":   len(winners),
                    "solve_time_sec": round(solve_time, 3),
                    "obj_value":      obj_value if obj_value is not None else revenue,
                },
            }] if feasible else [],
            "issues":    issues,
            "_solver_version": "auction_winner_selector_v1.0",
        }