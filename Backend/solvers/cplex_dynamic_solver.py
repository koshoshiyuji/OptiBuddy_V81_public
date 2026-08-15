"""
Backend/solvers/cplex_dynamic_solver.py  (V6.0)

CplexDynamicSolver — V6プライマリソルバー

V5.2 の full_yard_solver から「ソルバーコア」だけを抽出し、
以下の点を改善した。

変更点 (V5.2 full_yard_solver → V6 CplexDynamicSolver):
  1. フォールバック完全廃止
       DSL constraints が空の場合は ValueError を送出。
       「DSLがなければ動かない」を原則とし、ハードコード制約の
       二重管理を終わらせる。
  2. solve() の単一エントリポイント化
       registry.py が呼ぶのは CplexDynamicSolver(dsl).solve() だけ。
       build_model / _run_profiles / _detect_issues を内部メソッドに整理。
  3. ConstraintApplier に rolling_horizon / spatial_partition ハンドラを追加
       Phase 1 で規模制限回避の基盤を確立する（実ロジックは Phase 2 で充実）。
  4. その他のソルブロジック・イシュー検知は V5.2 から完全移植（動作変更なし）。

NOTE: docplex が未インストールの環境では ImportError になる。
      その場合 registry.py は cplex_fallback にフォールオーバーする。
"""

import copy
import logging
import time
from typing import Any, Callable, Dict, List, Optional, Tuple
from solvers.base.objective_terms import build_objective_expr
from solvers.base.issue_rules import run_issue_rules, build_yard_contexts

from docplex.cp.model import INTERVAL_MAX, CpoModel

logger = logging.getLogger(__name__)


# =============================================================================
# ConstraintApplier
# =============================================================================
#
# YardPlanning固有の制約適用ロジックは Backend/solvers/yard/constraint_applier.py
# (YardConstraintApplier) に分離された。共通の no_overlap / precedence /
# shift_window / shift_break は Backend/solvers/base/constraint_applier.py
# (BaseConstraintApplier) に集約されている。
#
# 既存コードとの互換性のため ConstraintApplier という名前のままインポートする。

from .yard.constraint_applier import YardConstraintApplier as ConstraintApplier  # noqa: E402

# =============================================================================
# シフトユーティリティ（V5.2から移植、変更なし）
# =============================================================================

def _parse_shifts(config: Dict[str, Any]):
    shifts = config.get("shifts", [])
    breaks = config.get("breaks", [])
    if not shifts:
        return {}, {}

    crane_windows: Dict[str, list] = {}
    for s in shifts:
        for cid in s.get("cranes", []):
            crane_windows.setdefault(cid, []).append((s.get("start", 0), s.get("end", 0)))

    shift_to_cranes = {s["id"]: s.get("cranes", []) for s in shifts}
    break_windows: Dict[str, list] = {}
    for b in breaks:
        b_end = b.get("start", 0) + b.get("duration", 0)
        for cid in shift_to_cranes.get(b.get("shift"), []):
            break_windows.setdefault(cid, []).append((b.get("start", 0), b_end))

    return crane_windows, break_windows


def _detect_shift_violations(tasks_result, crane_windows, break_windows, issue_statuses):
    issues = {}
    for t in tasks_result:
        crane_id = t.get("resource") or t.get("resourceId")
        if not crane_id:
            continue
        windows = crane_windows.get(crane_id)
        if not windows:
            continue
        t_start = t.get("start", 0) // 60
        t_end   = t.get("end",   0) // 60
        cid     = str(t.get("containerId", ""))
        tid     = str(t.get("id", ""))

        in_any = any(w_s <= t_start and t_end <= w_e for w_s, w_e in windows)
        if not in_any:
            iid = f"shift_violation_{tid}"
            if issue_statuses.get(iid) != "ACCEPTED":
                issues[iid] = {
                    "id": iid, "severity": "WARNING", "category": "SHIFT",
                    "title": f"シフト外作業: {cid} ({crane_id})",
                    "message": f"{crane_id} の稼働時間外({t_start}〜{t_end}分)にタスクが配置されています。",
                    "containerId": cid, "relatedContainerIds": [cid],
                }
            continue
        for (b_start, b_end) in break_windows.get(crane_id, []):
            if t_start < b_end and t_end > b_start:
                iid = f"shift_break_{tid}"
                if issue_statuses.get(iid) != "ACCEPTED":
                    issues[iid] = {
                        "id": iid, "severity": "WARNING", "category": "SHIFT",
                        "title": f"休憩時間跨ぎ: {cid} ({crane_id})",
                        "message": f"{crane_id} の休憩時間({b_start}〜{b_end}分)にタスクが跨がっています。",
                        "containerId": cid, "relatedContainerIds": [cid],
                    }
    return issues


# =============================================================================
# CplexDynamicSolver
# =============================================================================

class CplexDynamicSolver:
    """
    V6 プライマリソルバー。

    DSL constraints 配列が空の場合は ValueError を送出する（フォールバックなし）。
    constraints を必ず持つ Solver Input DSL を前提とした、
    「DSL駆動のみ」の純粋な実装。
    """

    def __init__(self, solver_input: Dict[str, Any]):
        self.solver_input = solver_input
        self._validate()

    # -------------------------------------------------------------------------
    # バリデーション
    # -------------------------------------------------------------------------

    def _validate(self):
        if not self.solver_input.get("tasks"):
            raise ValueError("[CplexDynamicSolver] tasks が空です。business_to_solver の出力を確認してください。")
        if not self.solver_input.get("constraints"):
            raise ValueError(
                "[CplexDynamicSolver] constraints が空です。"
                "DSL constraints を生成できていません。"
                "フォールバックが必要な場合は solver_hint='cplex_fallback' を指定してください。"
            )

    # -------------------------------------------------------------------------
    # 公開エントリポイント
    # -------------------------------------------------------------------------

    def solve(self) -> Dict[str, Any]:
        t_start = time.perf_counter()
        logger.info("[CplexDynamicSolver] solve() 開始")

        config        = self.solver_input.get("config", {})
        all_tasks     = self.solver_input.get("tasks", [])
        containers    = self.solver_input.get("containers", [])
        resources     = self.solver_input.get("resources", {})
        dsl_constraints = self.solver_input.get("constraints", [])
        issue_statuses  = self.solver_input.get("issue_statuses", {})

        # 設定
        op_defaults = {
            "PICK":      config.get("time_pick",      180),
            "MOVE":      config.get("time_move",      120),
            "LOAD":      config.get("time_load",      300),
            "REHANDLE":  config.get("time_rehandle",  240),
            "DISCHARGE": config.get("time_discharge", 300),
            "PLACE":     config.get("time_place",     180),
        }
        default_profiles = [
            {"name": "Plan A", "label": "全作業時間最短",  "w_makespan": 1, "w_penalty": 1},
            {"name": "Plan B", "label": "積み順遵守",      "w_makespan": 1, "w_penalty": op_defaults["LOAD"]},
        ]
        solution_profiles = config.get("solution_profiles", default_profiles)
        total_time_limit  = config.get("time_limit", 20)
        time_per_profile  = max(1, total_time_limit // len(solution_profiles))

        crane_windows, break_windows = _parse_shifts(config)

        container_map = {str(c["id"]): c for c in containers}

        # current_port バリデーション
        current_port = config.get("current_port")
        domain = config.get("domain", "")
        if not current_port and domain == "staffing":
            raise ValueError("config に current_port が指定されていません。")
        for c in containers:
            if "attrs" not in c or c["attrs"] is None:
                c["attrs"] = []
            pol, pod = c.get("pol", ""), c.get("pod", "")
            if current_port == pol:
                c["operation_type"] = "LOAD"
            elif current_port == pod:
                c["operation_type"] = "DISCHARGE"
            else:
                c["operation_type"] = "LOAD"

        # 制限ゾーン
        restricted    = resources.get("restricted_zones", {})
        reefer_slot_set = {
            (s.get("bay"), s.get("row"), s.get("tier"))
            for s in restricted.get("reefer", [])
            if s.get("bay") is not None and s.get("row") is not None
        }
        imo_slot_set = {
            (z.get("bay"), row)
            for z in restricted.get("imo", [])
            for row in (z.get("rows") or ([z["row"]] if z.get("row") else []))
            if z.get("bay") is not None
        }

        reefer_ids = {str(c["id"]) for c in containers if "REEFER" in c["attrs"]}
        imo_ids    = {str(c["id"]) for c in containers if any(a.startswith("IMO") for a in c["attrs"])}
        oog_ids    = {str(c["id"]) for c in containers if "OOG" in c["attrs"]}

        oog_excluded_slots = self._build_oog_excluded_slots(containers)

        logger.info(
            f"[CplexDynamicSolver] containers={len(containers)}, tasks={len(all_tasks)}, "
            f"constraints={len(dsl_constraints)}, profiles={[p['name'] for p in solution_profiles]}"
        )

        # order ペア
        load_order_pairs      = self._build_order_pairs("LOAD",      all_tasks, container_map)
        discharge_order_pairs = self._build_order_pairs("DISCHARGE", all_tasks, container_map)

        # プロファイル別ソルブ
        objective_dsl = self.solver_input.get("objective", {})
        all_solutions_data = self._run_profiles(
            solution_profiles, time_per_profile, op_defaults,
            all_tasks, containers, dsl_constraints,
            crane_windows, break_windows,
            load_order_pairs, discharge_order_pairs, container_map,
            oog_excluded_slots, oog_ids,
            objective_dsl,                # ← 追加
        )

        # イシュー検知
        issues = self._detect_issues(
            all_solutions_data, containers, container_map,
            reefer_slot_set, imo_slot_set, reefer_ids, imo_ids, oog_ids, oog_excluded_slots,
            crane_windows, break_windows, issue_statuses,
        )

        elapsed = time.perf_counter() - t_start
        logger.info(f"[CplexDynamicSolver] 完了: solutions={len(all_solutions_data)}, issues={len(issues)}, elapsed={elapsed:.2f}s")

        yard_limits   = {"max_bay": config.get("max_bays"),  "max_row": config.get("max_rows"),  "max_tier": config.get("max_tiers")}
        vessel_limits = {"max_bay": config.get("max_vessel_bays"), "max_row": config.get("max_vessel_rows"), "max_tier": config.get("max_vessel_tiers")}

        return {
            "status":        "ok" if all_solutions_data else "error",
            "solutions":     all_solutions_data,
            "tasks":         all_solutions_data[0]["tasks"] if all_solutions_data else [],
            "makespan":      all_solutions_data[0]["makespan"] if all_solutions_data else 0,
            "issues":        issues,
            "yard_limits":   yard_limits,
            "vessel_limits": vessel_limits,
            "containers":    containers,
            "_solver_version": "cplex_dynamic_v6.0",
        }

    # -------------------------------------------------------------------------
    # モデル構築
    # -------------------------------------------------------------------------

    def _build_model(
        self,
        all_tasks, containers, dsl_constraints,
        op_defaults, crane_windows, break_windows,
        oog_excluded_slots, oog_ids,
    ):
        mdl = CpoModel(name="OptiBuddy_v60")
        mdl.set_parameters({"RandomSeed": 1})
        task_itvs      = {}
        resource_usage = {}

        for t in all_tasks:
            tid = str(t["id"])
            op  = t.get("operation", "MOVE")
            dur = t.get("duration") or op_defaults.get(op, op_defaults["MOVE"])
            itv = mdl.interval_var(size=dur, start=(0, INTERVAL_MAX), name=f"T_{tid}")
            task_itvs[tid] = itv
            rid = t.get("resourceId") or t.get("resource")
            if rid:
                resource_usage.setdefault(rid, []).append(itv)

        # ConstraintApplier に全制約を委譲
        applier = ConstraintApplier(
            mdl, task_itvs, resource_usage,
            all_tasks, containers,
            crane_windows, break_windows,
        )
        applied, skipped = applier.apply_all(dsl_constraints)
        logger.info(f"[Model] DSL制約: 適用={applied}, スキップ={skipped}/{len(dsl_constraints)}")

        # リソース非重複の安全網（DSL no_overlap でカバーされていないリソース向け）
        for rid, itvs in resource_usage.items():
            if len(itvs) > 1:
                mdl.add(mdl.no_overlap(itvs))

        # OOG制約（DSL/ドメイン共通）
        self._apply_oog_constraints(mdl, task_itvs, all_tasks, containers, oog_excluded_slots, oog_ids)

        container_tasks = {}
        for t in all_tasks:
            container_tasks.setdefault(str(t["containerId"]), []).append(t)

        return mdl, task_itvs, container_tasks

    def _apply_oog_constraints(self, mdl, task_itvs, all_tasks, containers, oog_excluded_slots, oog_ids):
        if not oog_excluded_slots or not oog_ids:
            return
        oog_itvs = [
            task_itvs[str(t["id"])]
            for t in all_tasks
            if str(t.get("containerId")) in oog_ids
            and t.get("operation") in ("PICK", "MOVE")
            and str(t["id"]) in task_itvs
        ]
        if not oog_itvs:
            return
        oog_last = mdl.max([mdl.end_of(iv) for iv in oog_itvs])
        for c in containers:
            cid = str(c["id"])
            if cid in oog_ids:
                continue
            y = c.get("yard", {})
            bay, row, tier = y.get("bay"), y.get("row"), y.get("tier")
            if bay is None or row is None:
                continue
            if not ((bay, row, tier) in oog_excluded_slots or (bay, row, None) in oog_excluded_slots):
                continue
            for t in all_tasks:
                if str(t.get("containerId")) != cid or t.get("operation") not in ("PICK", "REHANDLE"):
                    continue
                if str(t["id"]) in task_itvs:
                    mdl.add(mdl.start_of(task_itvs[str(t["id"])]) >= oog_last)

    # -------------------------------------------------------------------------
    # プロファイル別ソルブ
    # -------------------------------------------------------------------------

    def _run_profiles(
        self, solution_profiles, time_per_profile, op_defaults,
        all_tasks, containers, dsl_constraints,
        crane_windows, break_windows,
        load_order_pairs, discharge_order_pairs, container_map,
        oog_excluded_slots, oog_ids,
        objective_dsl=None,           # ← 追加 (デフォルトNoneで後方互換)
    ):
        all_solutions_data = []

        for profile in solution_profiles:
            p_name     = profile.get("name", f"Plan {chr(65 + len(all_solutions_data))}")
            p_label    = profile.get("label", "")
            w_makespan = profile.get("w_makespan", 1)
            w_penalty  = profile.get("w_penalty", op_defaults["LOAD"])

            logger.info(f"[Profile] {p_name} [{p_label}] w_makespan={w_makespan}, w_penalty={w_penalty}")

            mdl, task_itvs, _ = self._build_model(
                all_tasks, containers, dsl_constraints,
                op_defaults, crane_windows, break_windows,
                oog_excluded_slots, oog_ids,
            )

            # 目的関数: build_objective_expr で合成
            # penalty_pairs は DSL の objective.penalty_pairs を参照 (インライン生成廃止)
            obj_context = {
                "task_itvs":     task_itvs,
                "penalty_pairs": (objective_dsl or {}).get("penalty_pairs", []),
            }
            obj_expr = build_objective_expr(
                mdl,
                domain="YardPlanning",
                context=obj_context,
                weight_overrides={
                    "makespan":      w_makespan,
                    "order_penalty": w_penalty,
                },
            )
            mdl.add(mdl.minimize(obj_expr))

            try:
                msol = mdl.solve(TimeLimit=time_per_profile)
            except Exception as e:
                logger.error(f"[Profile] {p_name} 例外: {e}", exc_info=True)
                continue

            if msol is None or not msol.is_solution():
                logger.warning(f"[Profile] {p_name}: 解なし")
                continue

            sol_tasks = []
            for t in all_tasks:
                tid     = str(t["id"])
                cid     = str(t.get("containerId"))
                sol_itv = msol.get_var_solution(task_itvs[tid])
                t_res   = copy.deepcopy(t)
                if cid in container_map:
                    for k, v in container_map[cid].items():
                        if k not in t_res:
                            t_res[k] = v
                t_res["start"]      = sol_itv.get_start()
                t_res["end"]        = sol_itv.get_end()
                t_res["resourceId"] = t.get("resourceId") or t.get("resource")
                sol_tasks.append(t_res)

            mspan   = max(t["end"] for t in sol_tasks) if sol_tasks else 0
            penalty = self._calc_penalty(sol_tasks, load_order_pairs, discharge_order_pairs)
            logger.info(f"[Profile] {p_name}: makespan={mspan}, penalty={penalty}")

            all_solutions_data.append({
                "name": p_name, "label": p_label,
                "tasks": sol_tasks, "makespan": mspan, "penalty": penalty,
            })

        return all_solutions_data

    # -------------------------------------------------------------------------
    # イシュー検知（V5.2から移植）
    # -------------------------------------------------------------------------

    def _detect_issues(
        self, all_solutions_data, containers, container_map,
        reefer_slot_set, imo_slot_set, reefer_ids, imo_ids, oog_ids, oog_excluded_slots,
        crane_windows, break_windows, issue_statuses,
    ):
        contexts = build_yard_contexts(
            container_map      = container_map,
            containers         = containers,
            solution_data_list = all_solutions_data,
            reefer_ids         = reefer_ids,
            imo_ids            = imo_ids,
            oog_ids            = oog_ids,
            reefer_slot_set    = reefer_slot_set,
            imo_slot_set       = imo_slot_set,
            oog_excluded_slots = oog_excluded_slots,
            crane_windows      = crane_windows,
            break_windows      = break_windows,
        )
        return run_issue_rules(
            domain         = "YardPlanning",
            contexts       = contexts,
            issue_statuses = issue_statuses,
        )

    # -------------------------------------------------------------------------
    # ユーティリティ
    # -------------------------------------------------------------------------

    @staticmethod
    def _build_oog_excluded_slots(containers):
        oog_excluded_slots: set = set()
        for c in containers:
            if "OOG" not in c.get("attrs", []):
                continue
            y = c.get("yard", {})
            bay, row, tier = y.get("bay"), y.get("row"), y.get("tier")
            if bay is None or row is None:
                continue
            try:
                ri = int(row)
                for d in (-1, +1):
                    if ri + d >= 1:
                        oog_excluded_slots.add((bay, ri + d, None))
            except (ValueError, TypeError):
                if isinstance(row, str) and len(row) == 1:
                    for d in (-1, +1):
                        oog_excluded_slots.add((bay, chr(ord(row) + d), None))
            if tier is not None:
                for d in (-1, +1):
                    if tier + d >= 1:
                        oog_excluded_slots.add((bay, row, tier + d))
        return oog_excluded_slots

    @staticmethod
    def _build_order_pairs(op: str, all_tasks, container_map):
        pairs = []
        op_tasks = [t for t in all_tasks if t.get("operation") == op]
        for i in range(len(op_tasks)):
            for j in range(i + 1, len(op_tasks)):
                t1, t2 = op_tasks[i], op_tasks[j]
                o1 = container_map.get(str(t1["containerId"]), {}).get("order", 0)
                o2 = container_map.get(str(t2["containerId"]), {}).get("order", 0)
                if o1 < o2:   pairs.append((t1["id"], t2["id"]))
                elif o1 > o2: pairs.append((t2["id"], t1["id"]))
        return pairs

    @staticmethod
    def _calc_penalty(tasks, load_pairs, discharge_pairs):
        ends = {t["id"]: t["end"] for t in tasks}
        lv = sum(1 for (a, b) in load_pairs      if ends.get(a, 0) > ends.get(b, 0))
        dv = sum(1 for (a, b) in discharge_pairs if ends.get(a, 0) > ends.get(b, 0))
        return lv + dv