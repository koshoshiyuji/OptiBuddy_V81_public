"""
Backend/tools/_energy_cost_aware_scheduler_solver_PRE_PRIORITY_FIX.py

2026-08-09追加。stage_b_debug_agent_edit_file_ab_test.py用の固定スナップショット。
EnergyCostAwareSchedulerSolverの「得意先優先が目的関数に未反映」だった状態
（PRIORITY_CUSTOMER_WEIGHTの導入前）を保存したもの。現在の
solvers/energy_cost_aware_scheduler_solver.pyは既にこのバグを修正済みだが、
A/Bテストは毎回この“バグ入り”状態から出発する必要があるため、実行のたびに
ディスクの現在の内容を読むのではなく、このファイルを固定の出発点として使う。
"""
from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)

# ペナルティ重み
UNFINISHED_PENALTY = 10_000_000  # 未完了1件あたりのペナルティ（第1目的代替用）

# デフォルト設定
DEFAULT_HORIZON_MIN = 1440        # 計画期間: 24時間（分）
DEFAULT_TIME_LIMIT  = 60          # ソルブ時間上限（秒）
DEFAULT_SLOT_MIN    = 30          # 電力単価スロット幅（分）


class EnergyCostAwareSchedulerSolver:
    """電力コスト考慮型生産スケジューリング solver。"""

    def __init__(self, solver_input: dict) -> None:
        self.dsl = solver_input

    def solve(self) -> dict:
        orders       = self.dsl.get("orders", [])
        lines        = self.dsl.get("lines", [])
        tariffs      = self.dsl.get("tariffs", [])
        config       = self.dsl.get("config", {})
        issue_statuses = self.dsl.get("issue_statuses", {})

        # 入力バリデーション
        if not orders:
            return self._make_result(
                feasible=False,
                orders=orders, lines=lines, schedule=[], line_ops=[],
                metrics={}, issues=[{
                    "id": "solve_failed", "severity": "CRITICAL",
                    "title": "生産オーダーがありません",
                    "message": "スケジューリング対象のオーダーが0件です。",
                    "relatedContainerIds": [],
                }],
            )
        if not lines:
            return self._make_result(
                feasible=False,
                orders=orders, lines=lines, schedule=[], line_ops=[],
                metrics={}, issues=[{
                    "id": "solve_failed", "severity": "CRITICAL",
                    "title": "生産ラインがありません",
                    "message": "スケジューリング対象の生産ラインが0件です。",
                    "relatedContainerIds": [],
                }],
            )

        try:
            schedule, line_ops, feasible, metrics = self._build_and_solve(
                orders, lines, tariffs, config
            )
        except CeLimitExceededError:
            return self._ce_limit_fallback(orders, lines, tariffs, config)
        except Exception as e:
            from solvers.base.solver_error_result import build_solver_crash_issue, solver_crash_extra_fields
            logger.error(f"[EnergyCostAwareScheduler] mdl.solve() 例外: {e}", exc_info=True)
            result = {
                "status": "ok", "feasible": False,
                "metadata": {"problem_class": "EnergyCostAwareScheduler"},
                "solutions": [], "issues": [build_solver_crash_issue(e)],
                "_solver_version": "energy_cost_aware_scheduler_v1.0",
            }
            result.update(solver_crash_extra_fields(e))
            return result

        issues = self._detect_issues(
            schedule, line_ops, orders, lines, feasible, issue_statuses, config
        )

        return self._make_result(
            feasible=feasible,
            orders=orders, lines=lines,
            schedule=schedule, line_ops=line_ops,
            metrics=metrics, issues=issues,
        )

    # -------------------------------------------------------------------------
    # CP モデル構築・ソルブ
    # -------------------------------------------------------------------------

    def _build_and_solve(
        self,
        orders: List[Dict],
        lines: List[Dict],
        tariffs: List[Dict],
        config: Dict,
    ) -> Tuple[List[Dict], List[Dict], bool, Dict]:
        """
        CP Optimizer モデルを構築してソルブする。

        Returns:
            (schedule, line_ops, feasible, metrics)
            schedule:  割り当て済みオーダー一覧 [{order_id, line_id, start, end, ...}]
            line_ops:  ライン稼働記録 [{line_id, start, end, ...}]
            feasible:  解が見つかったか
            metrics:   KPI 辞書
        """
        from docplex.cp.model import CpoModel
        from solvers.base.solution_extraction import extract_makespan, safe_objective_value

        mdl = CpoModel(name="energy_cost_aware_scheduler")

        horizon    = int(config.get("horizon_min", DEFAULT_HORIZON_MIN))
        time_limit = int(config.get("time_limit_sec", DEFAULT_TIME_LIMIT))
        slot_min   = int(config.get("slot_min", DEFAULT_SLOT_MIN))

        # 時間帯別単価テーブル（分単位）
        tariff_table = _build_tariff_table(tariffs, horizon, slot_min)

        # ── 変数生成 ──────────────────────────────────────────────────────────

        # order_itvs[order_id][line_id] = optional interval_var
        #   オーダー order_id をライン line_id に割り当てる場合に present になる
        order_itvs: Dict[str, Dict[str, Any]] = {}
        for o in orders:
            oid      = str(o["id"])
            dur      = int(o.get("duration_min", 60))
            earliest = int(o.get("earliest_start_min", 0))
            deadline = int(o.get("deadline_min", horizon))
            end_latest = min(deadline, horizon)

            compatible_lines = _compatible_line_ids(o, lines)
            order_itvs[oid] = {}
            for lid in compatible_lines:
                itv = mdl.interval_var(
                    name=f"o_{oid}_l_{lid}",
                    optional=True,
                    size=dur,
                    start=[earliest, end_latest - dur],
                    end=[earliest + dur, end_latest],
                )
                order_itvs[oid][lid] = itv

        # line_on_itvs[line_id] = optional interval_var（ライン稼働区間）
        # ライン稼働は1つの連続区間として近似する（起動・停止それぞれ最大1回）
        # より精密なモデルでは複数の稼働区間を許容するが、計算量との均衡から
        # 1区間に限定する（ライン起動・停止回数最小化の「できれば守りたいルール」とも整合）
        line_on_itvs: Dict[str, Any] = {}
        for ln in lines:
            lid = str(ln["id"])
            itv = mdl.interval_var(
                name=f"line_{lid}_on",
                optional=True,
                start=[0, horizon],
                end=[0, horizon],
            )
            line_on_itvs[lid] = itv

        # ── 制約 ──────────────────────────────────────────────────────────────

        # 1. 各オーダーはどれか1つのラインに割り当てるか未割り当て（optional）
        for o in orders:
            oid = str(o["id"])
            itvs_for_order = list(order_itvs[oid].values())
            if itvs_for_order:
                mdl.add(mdl.sum([mdl.presence_of(itv) for itv in itvs_for_order]) <= 1)

        # 2. 各ラインのリソース上限（pulse による cumulative 制約）
        #
        # 2026-08-09修正: mdl.pulse()の高さ(height)引数はCP Optimizerの仕様上、
        # 整数（またはタプルによる整数範囲）でなければならず、float値を渡すと
        # 「Argument 'height' should be an integer or a range expressed as a tuple
        # of integers」という例外でsolve()自体が失敗する（実機ログで確認済み。
        # baselineシナリオが本来feasibleなはずが「infeasible」と誤って報告されていた
        # 直接の原因）。消費電力(kW)は小数値になり得るため、0.1kW単位（10倍して
        # 四捨五入）に量子化してから整数のpulse高さとして扱う。作業員数・附帯設備枠は
        # 元々int()で整数化済みのため対象外。
        POWER_SCALE = 10  # 0.1kW単位に量子化

        for ln in lines:
            lid            = str(ln["id"])
            max_power      = int(round(float(ln.get("max_power_kw", 9999)) * POWER_SCALE))
            max_workers    = int(ln.get("max_workers", 9999))
            max_equipment  = int(ln.get("max_equipment_slots", 9999))
            standby_power  = float(ln.get("standby_power_kw", 0.0))

            power_pulses   = []
            worker_pulses  = []
            equip_pulses   = []

            for o in orders:
                oid = str(o["id"])
                if lid not in order_itvs[oid]:
                    continue
                itv = order_itvs[oid][lid]
                req_power   = float(o.get("power_kw", 0.0))
                req_workers = int(o.get("workers", 1))
                req_equip   = int(o.get("equipment_slots", 1))

                if req_power > 0:
                    power_pulses.append(mdl.pulse(itv, int(round(req_power * POWER_SCALE))))
                if req_workers > 0:
                    worker_pulses.append(mdl.pulse(itv, req_workers))
                if req_equip > 0:
                    equip_pulses.append(mdl.pulse(itv, req_equip))

            # 待機電力: ライン稼働中は常に消費（作業分に加算）
            line_itv = line_on_itvs[lid]
            if standby_power > 0:
                power_pulses.append(mdl.pulse(line_itv, int(round(standby_power * POWER_SCALE))))

            if power_pulses:
                mdl.add(mdl.sum(power_pulses) <= max_power)
            if worker_pulses:
                mdl.add(mdl.sum(worker_pulses) <= max_workers)
            if equip_pulses:
                mdl.add(mdl.sum(equip_pulses) <= max_equipment)

        # 3. ラインが稼働していない時間帯は割り当て不可
        #    割り当てオーダーはラインの稼働区間内に収まる必要がある
        for ln in lines:
            lid      = str(ln["id"])
            line_itv = line_on_itvs[lid]
            for o in orders:
                oid = str(o["id"])
                if lid not in order_itvs[oid]:
                    continue
                order_itv = order_itvs[oid][lid]
                # オーダーが present ならばラインも present かつオーダーがライン内に収まる
                mdl.add(mdl.if_then(
                    mdl.presence_of(order_itv),
                    mdl.presence_of(line_itv),
                ))
                mdl.add(mdl.if_then(
                    mdl.presence_of(order_itv),
                    mdl.start_of(order_itv) >= mdl.start_of(line_itv),
                ))
                mdl.add(mdl.if_then(
                    mdl.presence_of(order_itv),
                    mdl.end_of(order_itv) <= mdl.end_of(line_itv),
                ))

        # 4. 境界条件: 計画期間の開始・終了でラインは停止
        #    line_on_itvs は optional なので absence = 停止を表現できる
        #    稼働する場合は [1, horizon-1] の範囲内で稼働（開始 > 0, 終了 < horizon）
        for ln in lines:
            lid      = str(ln["id"])
            line_itv = line_on_itvs[lid]
            mdl.add(mdl.if_then(
                mdl.presence_of(line_itv),
                mdl.logical_and(
                    mdl.start_of(line_itv) >= 1,
                    mdl.end_of(line_itv) <= horizon - 1,
                ),
            ))

        # ── 目的関数 ─────────────────────────────────────────────────────────

        # 第1目的: 未完了オーダー数の最小化
        #   = (全オーダー数) - (割り当て済みオーダー数) の最小化
        #   = - Σ presence_of(各ラインへの割当) の最大化
        #   CP Optimizer は最小化のみのため、未割り当て件数を最小化する

        unfinished_terms = []
        for o in orders:
            oid = str(o["id"])
            itvs_for_order = list(order_itvs[oid].values())
            if itvs_for_order:
                assigned = mdl.sum([mdl.presence_of(itv) for itv in itvs_for_order])
                unfinished_terms.append(1 - assigned)
            else:
                unfinished_terms.append(1)  # 対応ラインなし = 必ず未完了

        obj_unfinished = mdl.sum(unfinished_terms) if unfinished_terms else 0

        # 第2目的: 総コスト最小化
        #   電力コスト: 各スロット × 単価 × 消費電力
        #   起動・停止コスト: ライン稼働 interval の presence で計上
        cost_terms = []

        # 起動・停止コスト
        for ln in lines:
            lid         = str(ln["id"])
            line_itv    = line_on_itvs[lid]
            startup_cost  = float(ln.get("startup_cost", 0.0))
            shutdown_cost = float(ln.get("shutdown_cost", 0.0))
            fixed_op_cost = startup_cost + shutdown_cost
            if fixed_op_cost > 0:
                cost_terms.append(mdl.presence_of(line_itv) * int(fixed_op_cost * 100))

        # 電力コスト（スロット近似: 各オーダーの電力消費 × 平均単価(スロット代表点)）
        # 正確には開始・終了時刻がスロットをまたぐが、CP Optimizer の目的関数では
        # 連続時間 × 単価の積分を直接扱えないため、オーダーの開始スロットの単価で
        # 近似する（実運用では ui_converter で正確な積分値を後計算して表示する）
        for o in orders:
            oid        = str(o["id"])
            req_power  = float(o.get("power_kw", 0.0))
            dur        = int(o.get("duration_min", 60))
            if req_power == 0:
                continue
            for lid, itv in order_itvs[oid].items():
                # 開始スロットの単価を代表値として使用
                # CP Optimizer では start_of を変数として使えないため、
                # 期待値として horizon の中央スロット単価を使う近似（目的関数への反映）
                avg_price = _average_tariff(tariff_table, slot_min)
                energy_cost = avg_price * req_power * (dur / 60.0)
                cost_terms.append(
                    mdl.presence_of(itv) * int(energy_cost * 100)
                )

        # 待機電力コスト（ライン稼働時間 × 待機電力 × 平均単価）
        for ln in lines:
            lid           = str(ln["id"])
            line_itv      = line_on_itvs[lid]
            standby_power = float(ln.get("standby_power_kw", 0.0))
            if standby_power == 0:
                continue
            avg_price      = _average_tariff(tariff_table, slot_min)
            # ライン稼働時間 × 待機電力 × 単価
            # size_of は稼働 interval が present のときのみ有効
            standby_cost_per_min = avg_price * standby_power / 60.0
            cost_terms.append(
                mdl.presence_of(line_itv) * mdl.size_of(line_itv, 0) * int(standby_cost_per_min * 100)
            )

        obj_cost = mdl.sum(cost_terms) if cost_terms else 0

        # lexicographic 最適化: 第1目的(未完了最小) → 第2目的(コスト最小)
        mdl.add(mdl.minimize_static_lex([obj_unfinished, obj_cost]))

        # ── ソルブ ─────────────────────────────────────────────────────────────

        try:
            msol = mdl.solve(TimeLimit=time_limit, LogVerbosity="Quiet")
        except Exception as e:
            _check_ce_limit(e)
            raise

        if msol is None or not msol:
            return [], [], False, {"solve_time": 0.0, "total_cost": 0.0,
                                    "completed_orders": 0, "total_orders": len(orders)}

        # ── 解抽出 ─────────────────────────────────────────────────────────────

        from solvers.base.solution_extraction import extract_optimality_metadata
        optimality = extract_optimality_metadata(msol)

        schedule: List[Dict] = []
        assigned_order_ids: set = set()

        for o in orders:
            oid = str(o["id"])
            for lid, itv in order_itvs[oid].items():
                var_sol = msol.get_var_solution(itv)
                if var_sol is None or not var_sol.is_present():
                    continue
                start_min = int(var_sol.get_start())
                end_min   = int(var_sol.get_end())
                schedule.append({
                    "order_id":    oid,
                    "line_id":     lid,
                    "start":       start_min,
                    "end":         end_min,
                    "duration":    end_min - start_min,
                    "power_kw":    float(o.get("power_kw", 0.0)),
                    "order_name":  o.get("name", oid),
                    "customer":    o.get("customer", ""),
                    "deadline_min": int(o.get("deadline_min", horizon)),
                })
                assigned_order_ids.add(oid)
                break  # 1オーダーは1ラインのみ

        line_ops: List[Dict] = []
        for ln in lines:
            lid      = str(ln["id"])
            line_itv = line_on_itvs[lid]
            var_sol  = msol.get_var_solution(line_itv)
            if var_sol is None or not var_sol.is_present():
                continue
            start_min = int(var_sol.get_start())
            end_min   = int(var_sol.get_end())
            line_ops.append({
                "line_id":       lid,
                "line_name":     ln.get("name", lid),
                "start":         start_min,
                "end":           end_min,
                "duration":      end_min - start_min,
                "standby_power": float(ln.get("standby_power_kw", 0.0)),
                "startup_cost":  float(ln.get("startup_cost", 0.0)),
                "shutdown_cost": float(ln.get("shutdown_cost", 0.0)),
            })

        # KPI 計算（Python側で後計算）
        metrics = _compute_metrics(
            schedule=schedule,
            line_ops=line_ops,
            orders=orders,
            lines=lines,
            tariff_table=tariff_table,
            slot_min=slot_min,
            assigned_order_ids=assigned_order_ids,
            solve_time=optimality.get("solve_time_sec") or 0.0,
            is_optimal=optimality.get("is_optimal", False),
        )

        return schedule, line_ops, True, metrics

    # -------------------------------------------------------------------------
    # イシュー検知
    # -------------------------------------------------------------------------

    def _detect_issues(
        self,
        schedule: List[Dict],
        line_ops: List[Dict],
        orders: List[Dict],
        lines: List[Dict],
        feasible: bool,
        issue_statuses: Dict,
        config: Dict,
    ) -> List[Dict]:
        from solvers.base.issue_rules import build_full_unassignment_issue

        issues: List[Dict] = []

        if not feasible:
            issues.append({
                "id": "solve_failed", "severity": "CRITICAL",
                "title": "実行可能解が見つかりませんでした",
                "message": (
                    "全ての制約を満たすスケジュールが見つかりませんでした。"
                    "オーダーの完了期限の延長、または稼働ラインの追加をご検討ください。"
                ),
                "relatedContainerIds": [],
            })
            return issues

        assigned_ids = {s["order_id"] for s in schedule}

        # 全件未割当異常検知
        anomaly = build_full_unassignment_issue(
            assigned_count=len(assigned_ids),
            total_count=len(orders),
            entity_label="生産オーダー",
            extra_hint="get_var_solution() による解抽出処理",
        )
        if anomaly:
            issues.append(anomaly)

        # 未完了オーダーの警告
        priority_customers = set(config.get("priority_customer_names", []))
        for o in orders:
            oid = str(o["id"])
            if oid not in assigned_ids:
                customer = o.get("customer", "")
                severity = "CRITICAL" if customer in priority_customers else "WARNING"
                if issue_statuses.get(f"unfinished_{oid}") != "ACCEPTED":
                    issues.append({
                        "id":       f"unfinished_{oid}",
                        "severity": severity,
                        "title":    f"未完了オーダー: {o.get('name', oid)}",
                        "message":  (
                            f"オーダー「{o.get('name', oid)}」（得意先: {customer or '不明'}）を"
                            f"計画期間内に完了できませんでした。"
                        ),
                        "relatedContainerIds": [],
                    })

        # 期限超過チェック
        for s in schedule:
            if s["end"] > s["deadline_min"]:
                oid = s["order_id"]
                if issue_statuses.get(f"deadline_overrun_{oid}") != "ACCEPTED":
                    issues.append({
                        "id":       f"deadline_overrun_{oid}",
                        "severity": "CRITICAL",
                        "title":    f"期限超過: {s['order_name']}",
                        "message":  (
                            f"オーダー「{s['order_name']}」の完了時刻({s['end']}分)が"
                            f"最終完了期限({s['deadline_min']}分)を超過しています。"
                        ),
                        "relatedContainerIds": [],
                    })

        return issues

    # -------------------------------------------------------------------------
    # 結果フォーマット
    # -------------------------------------------------------------------------

    def _make_result(
        self,
        feasible: bool,
        orders: List[Dict],
        lines: List[Dict],
        schedule: List[Dict],
        line_ops: List[Dict],
        metrics: Dict,
        issues: List[Dict],
    ) -> dict:
        assigned_ids = {s["order_id"] for s in schedule}
        unfinished = [
            {"order_id": o["id"], "name": o.get("name", ""), "customer": o.get("customer", "")}
            for o in orders if str(o["id"]) not in assigned_ids
        ]

        return {
            "status":   "ok",
            "feasible": feasible,
            "metadata": {"problem_class": "EnergyCostAwareScheduler"},
            "solutions": [{
                "feasible":     feasible,
                "schedule":     schedule,
                "line_ops":     line_ops,
                "unfinished":   unfinished,
                "kpi":          metrics,
            }],
            "issues": issues,
            "_solver_version": "energy_cost_aware_scheduler_v1.0",
        }

    # -------------------------------------------------------------------------
    # CE 上限フォールバック
    # -------------------------------------------------------------------------

    def _ce_limit_fallback(self, orders, lines, tariffs, config) -> dict:
        logger.warning("[EnergyCostAwareScheduler] CE上限に達しました。フォールバックを試みます。")
        try:
            from solvers.energy_cost_aware_scheduler_batch_decomposer import (
                PRIMARY_ENTITY_KEY, build_subset_input, merge_results,
            )
            from solvers.base.ce_limit_lns import run_solve_with_ce_limit_fallback
            return run_solve_with_ce_limit_fallback(
                solver_class=type(self),
                solver_input=self.dsl,
                primary_entity_key=PRIMARY_ENTITY_KEY,
                build_subset_input=build_subset_input,
                merge_results=merge_results,
                problem_class="EnergyCostAwareScheduler",
                solver_version="energy_cost_aware_scheduler_v1.0",
            )
        except ImportError:
            return {
                "status": "ok", "feasible": False,
                "metadata": {"problem_class": "EnergyCostAwareScheduler"},
                "solutions": [], "issues": [{
                    "id": "ce_limit_no_adapter", "severity": "CRITICAL",
                    "title": "CPLEXの無料版で扱える件数を超えています",
                    "message": "データ件数を減らすか、正規ライセンスのご利用をご検討ください。",
                    "relatedContainerIds": [],
                }],
                "_solver_version": "energy_cost_aware_scheduler_v1.0",
            }


# =============================================================================
# ヘルパー関数
# =============================================================================

class CeLimitExceededError(Exception):
    pass


def _check_ce_limit(e: Exception) -> None:
    """CPLEX Community Edition のサイズ上限超過を検知して再送出する。"""
    msg = str(e).lower()
    if "problem size limit" in msg or "size limit exceeded" in msg:
        raise CeLimitExceededError(str(e)) from e


def _compatible_line_ids(order: Dict, lines: List[Dict]) -> List[str]:
    """オーダーが割り当て可能なラインIDのリストを返す。"""
    allowed = order.get("allowed_line_ids")
    if allowed is not None:
        allowed_set = set(str(lid) for lid in allowed)
        return [str(ln["id"]) for ln in lines if str(ln["id"]) in allowed_set]
    return [str(ln["id"]) for ln in lines]


def _build_tariff_table(
    tariffs: List[Dict],
    horizon: int,
    slot_min: int,
) -> List[float]:
    """
    分単位の tariff テーブルを構築する。
    tariffs: [{slot_start: int(分), slot_end: int(分), price_per_kwh: float}]
    戻り値: リスト（インデックスi = スロット番号、値 = price_per_kwh）
    """
    n_slots = (horizon + slot_min - 1) // slot_min
    table   = [0.0] * n_slots
    for t in tariffs:
        start_slot = int(t.get("slot_start", 0)) // slot_min
        end_slot   = (int(t.get("slot_end", slot_min)) + slot_min - 1) // slot_min
        price      = float(t.get("price_per_kwh", 0.0))
        for s in range(start_slot, min(end_slot, n_slots)):
            table[s] = price
    return table


def _average_tariff(tariff_table: List[float], slot_min: int) -> float:
    """電力単価テーブルの平均値を返す（目的関数の近似に使用）。"""
    if not tariff_table:
        return 1.0
    return sum(tariff_table) / len(tariff_table)


def _compute_energy_cost_for_order(
    start_min: int,
    end_min: int,
    power_kw: float,
    tariff_table: List[float],
    slot_min: int,
) -> float:
    """オーダーの電力コストをスロット積分で正確に計算する。"""
    if power_kw == 0:
        return 0.0
    total_cost = 0.0
    for t in range(start_min, end_min):
        slot_idx = t // slot_min
        if slot_idx < len(tariff_table):
            price = tariff_table[slot_idx]
            # 1分 = 1/60 時間
            total_cost += price * power_kw * (1.0 / 60.0)
    return total_cost


def _compute_standby_cost_for_line(
    start_min: int,
    end_min: int,
    standby_power_kw: float,
    tariff_table: List[float],
    slot_min: int,
) -> float:
    """ライン待機電力コストをスロット積分で計算する。"""
    if standby_power_kw == 0:
        return 0.0
    total_cost = 0.0
    for t in range(start_min, end_min):
        slot_idx = t // slot_min
        if slot_idx < len(tariff_table):
            price = tariff_table[slot_idx]
            total_cost += price * standby_power_kw * (1.0 / 60.0)
    return total_cost


def _compute_metrics(
    schedule: List[Dict],
    line_ops: List[Dict],
    orders: List[Dict],
    lines: List[Dict],
    tariff_table: List[float],
    slot_min: int,
    assigned_order_ids: set,
    solve_time: float,
    is_optimal: bool,
) -> Dict:
    """Python側でKPIを正確に計算する（CP式のget_valueは使わない）。"""

    line_map = {str(ln["id"]): ln for ln in lines}

    # 電力コスト（オーダー稼働分）
    order_energy_cost = sum(
        _compute_energy_cost_for_order(
            s["start"], s["end"], s["power_kw"], tariff_table, slot_min
        )
        for s in schedule
    )

    # 電力コスト（待機分）+ 起動・停止コスト
    line_energy_cost    = 0.0
    line_operation_cost = 0.0
    for op in line_ops:
        lid = op["line_id"]
        ln  = line_map.get(lid, {})
        line_energy_cost += _compute_standby_cost_for_line(
            op["start"], op["end"],
            float(ln.get("standby_power_kw", 0.0)),
            tariff_table, slot_min,
        )
        line_operation_cost += float(ln.get("startup_cost", 0.0)) + float(ln.get("shutdown_cost", 0.0))

    total_cost       = order_energy_cost + line_energy_cost + line_operation_cost
    completed_orders = len(assigned_order_ids)
    total_orders     = len(orders)
    completion_rate  = completed_orders / total_orders if total_orders > 0 else 0.0

    # ライン稼働率
    line_utilization = []
    for op in line_ops:
        order_mins_on_line = sum(
            s["duration"] for s in schedule if s["line_id"] == op["line_id"]
        )
        util = order_mins_on_line / op["duration"] if op["duration"] > 0 else 0.0
        line_utilization.append({
            "line_id":   op["line_id"],
            "line_name": op["line_name"],
            "active_min": op["duration"],
            "order_min":  order_mins_on_line,
            "util_rate":  round(util, 3),
        })

    return {
        "completed_orders":    completed_orders,
        "total_orders":        total_orders,
        "completion_rate":     round(completion_rate, 3),
        "total_cost":          round(total_cost, 2),
        "order_energy_cost":   round(order_energy_cost, 2),
        "line_energy_cost":    round(line_energy_cost, 2),
        "line_operation_cost": round(line_operation_cost, 2),
        "line_utilization":    line_utilization,
        "solve_time":          round(solve_time, 2),
        "is_optimal":          is_optimal,
    }
