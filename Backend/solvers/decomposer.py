"""
Backend/solvers/decomposer.py

HorizonDecomposer / SpatialDecomposer
─────────────────────────────────────
Solver Input DSL を受け取り、CPLEX 評価版の変数制限を回避するために
「時間分割（Rolling Horizon）」または「空間分割（Spatial Decomposition）」で
複数の小 DSL に分割し、結果を結合して返す。

変更履歴:
  V6.0 初期: HorizonDecomposer / SpatialDecomposer / DecomposerFactory 新規実装
  V6.1〜V6.6: (詳細省略・git履歴参照)
  2026-07-27: EventStaffing専用だったStaffDecomposer（V6.6でmin_chief_count
    バグ修正済み）を削除。呼び出し元のEventStaffingドメインが2026-07-11に
    削除済みで、`DecomposerFactory.for_input()`の`problem_class=="EventStaffing"`
    分岐が到達不能（呼ばれることのない）デッドコードになっていたため
    （Koshoshiとの会話で発覚、app.py側の参照も含めて一括削除）。
"""

import copy
import logging
import math
import os
import time
from collections import defaultdict
from concurrent.futures import ProcessPoolExecutor, as_completed
from typing import Any, Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)


# =============================================================================
# ユーティリティ
# =============================================================================

def _get_resource_id(task: Dict) -> Optional[str]:
    """
    Solver Input タスクのリソースIDを正規化（None は未割当）。
    resourceId, resource, crane_id, craneId の順に探索。
    """
    for key in ("resourceId", "resource", "crane_id", "craneId"):
        val = task.get(key)
        if val:
            return str(val)
    return None


def _get_resource_id_from_output(task: Dict) -> Optional[str]:
    """
    Solver Output タスクのリソースIDを正規化（None は未割当）。
    """
    for key in ("resourceId", "resource", "crane_id", "craneId", "crane", "assignedCrane"):
        val = task.get(key)
        if val:
            return str(val)
    return None


def _task_est(t: Dict) -> int:
    if t.get("start") is not None:
        return int(t["start"])
    if t.get("est") is not None:
        return int(t["est"])
    seq = t.get("sequence", 1)
    dur = t.get("duration") or 120
    return max(0, (seq - 1) * dur)


def _task_duration(t: Dict, op_defaults: Dict) -> int:
    return t.get("duration") or op_defaults.get(t.get("operation", "MOVE"), 120)


# ブロックをまたぐと意味をなさない制約タイプ
_CROSS_BLOCK_EXCLUDE = frozenset({
    "phase_separation",
})


def _filter_constraints_for_block(
    constraints: List[Dict],
    block_task_ids: set,
    block_tasks: List[Dict],
) -> List[Dict]:
    """
    ブロック / パーティションに関連する制約のみを返す。
    HorizonDecomposer / SpatialDecomposer の両方で共用。
    """
    if not constraints:
        return []

    used_resources = set()
    for t in block_tasks:
        rid = _get_resource_id(t)
        if rid:
            used_resources.add(rid)

    filtered = []
    for c in constraints:
        c_type = c.get("type", "unknown")
        params  = c.get("params", {})

        if c_type in _CROSS_BLOCK_EXCLUDE:
            continue

        if c_type == "no_overlap":
            orig_tids = params.get("task_ids", [])
            scoped    = [tid for tid in orig_tids if str(tid) in block_task_ids]
            if len(scoped) > 1:
                scoped_c = copy.deepcopy(c)
                scoped_c["params"]["task_ids"] = scoped
                filtered.append(scoped_c)

        elif c_type in ("precedence", "physical_stack_order"):
            task_ids_in = set()
            if c_type == "physical_stack_order":
                task_ids_in = {str(item.get("task_id")) for item in params.get("stack", [])}
            else:
                for key in ("from_task", "to_task"):
                    val = params.get(key)
                    if val:
                        task_ids_in.add(str(val))
            if task_ids_in & block_task_ids:
                filtered.append(c)

        elif c_type == "crane_interference":
            crane_a = params.get("crane_a", "")
            crane_b = params.get("crane_b", "")
            if crane_a in used_resources or crane_b in used_resources:
                scoped_c = copy.deepcopy(c)
                scoped_c["params"]["tasks_a"] = [
                    t for t in params.get("tasks_a", []) if str(t) in block_task_ids
                ]
                scoped_c["params"]["tasks_b"] = [
                    t for t in params.get("tasks_b", []) if str(t) in block_task_ids
                ]
                if scoped_c["params"]["tasks_a"] or scoped_c["params"]["tasks_b"]:
                    filtered.append(scoped_c)

        elif c_type in ("shift_window", "shift_break"):
            task_ids_in = {str(tid) for tid in params.get("task_ids", [])}
            if task_ids_in & block_task_ids:
                scoped_c = copy.deepcopy(c)
                scoped_c["params"]["task_ids"] = [
                    tid for tid in params.get("task_ids", []) if str(tid) in block_task_ids
                ]
                filtered.append(scoped_c)

        elif c_type == "attribute_zone":
            container_ids_in = {str(cid) for cid in params.get("container_ids", [])}
            block_cids       = {str(t.get("containerId", "")) for t in block_tasks}
            if container_ids_in & block_cids:
                filtered.append(c)

        elif c_type == "rolling_horizon":
            filtered.append(c)

        else:
            filtered.append(c)

    logger.debug(
        f"[FilterConstraints] {len(constraints)} → {len(filtered)} "
        f"({len(block_task_ids)} tasks, {len(used_resources)} resources)"
    )
    return filtered


# =============================================================================
# DslRepository ブリッジ
# =============================================================================

def _log_evolution_safe(event_type: str, payload: Dict) -> None:
    raw = os.environ.get("DECOMPOSER_LOG_EVOL", "0")
    logger.info(f"[EvolutionLog] DECOMPOSER_LOG_EVOL={raw!r}")
    if raw.strip() != "1":
        return
    try:
        from dsl_repository.repository import DslRepository  # type: ignore
        repo = DslRepository()
        repo.log_dsl_evolution(
            dsl_id=int(payload.get("dsl_id", 0)),
            change_type=event_type,
            change_description=f"Decomposer: {event_type}",
            diff_json=payload,
            llm_context=None,
        )
        logger.info(f"[EvolutionLog] 書き込み完了: event_type={event_type}")
    except ImportError:
        logger.warning(
            "[EvolutionLog] DslRepository をインポートできません。"
            " DECOMPOSER_LOG_EVOL=1 ですが evolution_log への書き込みをスキップします。"
        )
    except Exception as exc:
        logger.warning(f"[EvolutionLog] 書き込み失敗（続行）: {exc}")


# =============================================================================
# 並列実行ヘルパー（SpatialDecomposer 用）
# =============================================================================

def _solve_partition_worker(args: Tuple[Dict, Any]) -> Dict:
    sub_dsl, solver_class_path = args
    if solver_class_path is None:
        raise RuntimeError("solver_class_path が None — 並列ワーカーでは使用不可")
    module_path, class_name = solver_class_path.rsplit(":", 1)
    import importlib
    mod = importlib.import_module(module_path)
    solver_cls = getattr(mod, class_name)
    return solver_cls().solve(sub_dsl)


# =============================================================================
# HorizonDecomposer  — コンテナ単位グループ化 + 実 start 時間軸分割 (V6.4/V6.5)
# =============================================================================

class HorizonDecomposer:
    """
    Solver Input DSL をコンテナ単位でグループ化し、
    実 start 時刻の分布に基づいた時間軸でブロック分割する。
    """

    def __init__(
        self,
        block_hours:         float = 0.5,
        overlap_minutes:     float = 10.0,
        max_tasks_per_block: int   = 40,
    ):
        self.block_sec   = int(block_hours    * 3600)
        self.overlap_sec = int(overlap_minutes * 60)
        self.max_tasks   = max_tasks_per_block

    def split(self, solver_input: Dict[str, Any]) -> List[Dict[str, Any]]:
        t0        = time.perf_counter()
        all_tasks = solver_input.get("tasks", [])

        if not all_tasks:
            logger.warning("[HorizonDecomposer] tasks が空 — 分割スキップ")
            return [solver_input]

        if len(all_tasks) <= self.max_tasks:
            logger.info(f"[HorizonDecomposer] 分割不要 (tasks={len(all_tasks)})")
            return [solver_input]

        blocks = self._group_by_container_then_split(all_tasks)

        if len(blocks) <= 1:
            logger.info("[HorizonDecomposer] 分割結果が1ブロック — そのまま返す")
            return [solver_input]

        logger.info(
            f"[HorizonDecomposer] V6.5 コンテナ単位時間軸分割: "
            f"tasks={len(all_tasks)}, blocks={len(blocks)}, "
            f"sizes={[len(b) for b in blocks]}"
        )

        sub_dsls = []
        for i, block_tasks in enumerate(blocks):
            block_task_ids = [str(t["id"]) for t in block_tasks]
            rh_params = {
                "horizon_id":    f"block_{i}",
                "initial_state": [],
                "task_ids":      block_task_ids,
            }
            rh_constraint = {"type": "rolling_horizon", "params": rh_params}
            sub_dsls.append(self._build_sub_dsl(solver_input, block_tasks, rh_constraint, i))

        self._log_split_info(solver_input, sub_dsls, elapsed=time.perf_counter() - t0)
        return sub_dsls if sub_dsls else [solver_input]

    def solve_sequentially(
        self,
        solver_input: Dict[str, Any],
        solve_fn,
    ) -> Dict[str, Any]:
        t_solve_start = time.perf_counter()
        all_tasks     = solver_input.get("tasks", [])

        if len(all_tasks) <= self.max_tasks:
            return solve_fn(solver_input)

        blocks = self._group_by_container_then_split(all_tasks)

        if len(blocks) <= 1:
            return solve_fn(solver_input)

        results: List[Dict] = []
        resource_last_end: Dict[str, int] = {}

        for i, block_tasks in enumerate(blocks):
            initial_state = []
            for t in block_tasks:
                rid = _get_resource_id(t)
                if rid and rid in resource_last_end:
                    initial_state.append({
                        "task_id":   str(t["id"]),
                        "min_start": resource_last_end[rid],
                    })

            rh_params = {
                "horizon_id":    f"block_{i}",
                "initial_state": initial_state,
                "task_ids":      [str(t["id"]) for t in block_tasks],
            }
            rh_constraint = {"type": "rolling_horizon", "params": rh_params}
            sub_dsl = self._build_sub_dsl(solver_input, block_tasks, rh_constraint, i)

            try:
                result = solve_fn(sub_dsl)
            except Exception as exc:
                msg = str(exc)
                if "Problem size limit exceeded" in msg or "size limit" in msg.lower():
                    logger.warning(
                        f"[HorizonDecomposer] Block {i} hit size limit "
                        f"(tasks={len(block_tasks)}). "
                        f"max_tasks_per_block={self.max_tasks} をさらに下げてください。"
                    )
                else:
                    logger.error(
                        f"[HorizonDecomposer] Block {i} failed: {exc}", exc_info=True
                    )
                result = {"status": "error", "solutions": [], "tasks": [], "issues": []}

            results.append(result)

            if result.get("status") == "ok" and result.get("solutions"):
                for t in result["solutions"][0].get("tasks", []):
                    rid = _get_resource_id_from_output(t)
                    if rid:
                        end_val = t.get("end", 0)
                        if end_val > resource_last_end.get(rid, 0):
                            resource_last_end[rid] = end_val

            task_count = (
                len(result["solutions"][0].get("tasks", []))
                if result.get("solutions") else 0
            )
            logger.info(
                f"[HorizonDecomposer] Block {i}/{len(blocks)-1} 完了: "
                f"status={result.get('status')}, tasks={task_count}, "
                f"resource_last_end(sample)="
                f"{dict(list(resource_last_end.items())[:3])}"
            )

        merged = self.merge(results)

        solve_elapsed = time.perf_counter() - t_solve_start
        _log_evolution_safe("decompose_horizon", {
            "type":               "rolling_horizon_v6.5",
            "original_tasks":     len(all_tasks),
            "block_count":        len(results),
            "block_sizes":        [len(b) for b in blocks],
            "ok_blocks":          sum(1 for r in results if r.get("status") == "ok"),
            "makespan":           merged.get("makespan", 0),
            "max_tasks_per_block": self.max_tasks,
            "solve_elapsed":      round(solve_elapsed, 3),
        })

        return merged

    def merge(self, results: List[Dict[str, Any]]) -> Dict[str, Any]:
        if not results:
            return {"status": "error", "solutions": [], "tasks": [], "issues": []}
        if len(results) == 1:
            return results[0]

        solutions_merged: Dict[str, Dict[str, Any]] = {}
        all_issues:       Dict[str, Dict]            = {}

        for result in results:
            if result.get("status") != "ok":
                for issue in result.get("issues", []):
                    all_issues[issue["id"]] = issue
                continue
            for sol in result.get("solutions", []):
                plan = sol.get("name", "Plan A")
                if plan not in solutions_merged:
                    solutions_merged[plan] = {"name": plan, "tasks": {}, "penalty": 0}
                for t in sol.get("tasks", []):
                    solutions_merged[plan]["tasks"][str(t["id"])] = t
            for issue in result.get("issues", []):
                all_issues[issue["id"]] = issue

        if not solutions_merged:
            return {
                "status":    "error",
                "solutions": [],
                "tasks":     [],
                "issues":    list(all_issues.values()),
            }

        final_solutions = []
        for plan_name, sol_data in solutions_merged.items():
            tasks = list(sol_data["tasks"].values())
            mspan = max((t["end"] for t in tasks), default=0)
            final_solutions.append({
                "name":     plan_name,
                "tasks":    tasks,
                "makespan": mspan,
                "penalty":  sol_data.get("penalty", 0),
            })

        final_solutions.sort(key=lambda s: (s["makespan"], s.get("penalty", 0)))

        primary_tasks = final_solutions[0]["tasks"] if final_solutions else []
        mspan         = final_solutions[0]["makespan"] if final_solutions else 0
        first         = next((r for r in results if r.get("status") == "ok"), results[0])

        logger.info(
            f"[HorizonDecomposer.merge] blocks={len(results)}, "
            f"ok_blocks={sum(1 for r in results if r.get('status') == 'ok')}, "
            f"tasks={len(primary_tasks)}, makespan={mspan}, issues={len(all_issues)}"
        )

        return {
            "status":        "ok",
            "solutions":     final_solutions,
            "tasks":         primary_tasks,
            "makespan":      mspan,
            "issues":        list(all_issues.values()),
            "containers":    first.get("containers", []),
            "yard_limits":   first.get("yard_limits", {}),
            "vessel_limits": first.get("vessel_limits", {}),
            "_decompose_meta": {
                "type":        "rolling_horizon_v6.5",
                "block_count": len(results),
                "ok_blocks":   sum(1 for r in results if r.get("status") == "ok"),
            },
        }

    def _group_by_container_then_split(
        self,
        all_tasks: List[Dict],
    ) -> List[List[Dict]]:
        groups: Dict[str, List[Dict]] = defaultdict(list)
        for t in all_tasks:
            cid = str(t.get("containerId", "__no_container__"))
            groups[cid].append(t)

        sorted_groups = sorted(
            groups.values(),
            key=lambda g: min(_task_est(t) for t in g),
        )

        block_sec = self._calc_dynamic_block_sec(all_tasks)

        blocks: List[List[Dict]] = []
        current_block: List[Dict] = []
        current_block_start: Optional[int] = None

        for group in sorted_groups:
            group_min_est = min(_task_est(t) for t in group)

            if current_block_start is None:
                current_block_start = group_min_est

            if (
                current_block
                and (group_min_est - current_block_start) > block_sec
            ):
                blocks.append(current_block)
                current_block = []
                current_block_start = group_min_est

            current_block.extend(group)

        if current_block:
            blocks.append(current_block)

        return blocks

    def _calc_dynamic_block_sec(self, all_tasks: List[Dict]) -> int:
        if len(all_tasks) < 2:
            return max(self.block_sec, 600)

        ests       = [_task_est(t) for t in all_tasks]
        total_span = max(ests) - min(ests)

        if total_span == 0:
            return max(self.block_sec, 600)

        target_blocks = max(1, math.ceil(len(all_tasks) / self.max_tasks))
        dynamic_sec   = math.ceil(total_span / target_blocks)
        result_sec    = max(600, dynamic_sec)

        logger.info(
            f"[HorizonDecomposer] 動的ブロック幅: "
            f"span={total_span}s, tasks={len(all_tasks)}, "
            f"target_blocks={target_blocks}, block_sec={result_sec}s"
        )
        return result_sec

    def _build_sub_dsl(
        self,
        base:          Dict[str, Any],
        block_tasks:   List[Dict],
        rh_constraint: Dict,
        block_idx:     int,
    ) -> Dict[str, Any]:
        sub = copy.deepcopy(base)
        sub["tasks"] = list(block_tasks)

        block_task_ids = {str(t["id"]) for t in block_tasks}
        filtered       = _filter_constraints_for_block(
            sub.get("constraints", []), block_task_ids, block_tasks
        )
        sub["constraints"] = [rh_constraint] + filtered
        sub.setdefault("metadata", {})["horizon_block"] = block_idx
        return sub

    def _log_split_info(self, solver_input: Dict, sub_dsls: List[Dict], elapsed: float) -> None:
        payload = {
            "type":               "rolling_horizon_v6.5",
            "original_tasks":     len(solver_input.get("tasks", [])),
            "block_count":        len(sub_dsls),
            "block_sizes":        [len(d.get("tasks", [])) for d in sub_dsls],
            "max_tasks_per_block": self.max_tasks,
            "split_elapsed":      round(elapsed, 3),
        }
        logger.info(f"[HorizonDecomposer] 分割完了: {payload}")
        _log_evolution_safe("decompose_horizon_split", payload)


# =============================================================================
# SpatialDecomposer  — 空間分割（Spatial Decomposition）
# =============================================================================

class SpatialDecomposer:
    """
    ヤードブロック間の干渉を分析し、独立したサブ DSL に分割する。
    """

    def __init__(self, block_size: int = 10, min_tasks_to_split: int = 50):
        self.block_size         = block_size
        self.min_tasks_to_split = min_tasks_to_split

    def split(self, solver_input: Dict[str, Any]) -> List[Dict[str, Any]]:
        t0         = time.perf_counter()
        all_tasks  = solver_input.get("tasks", [])
        containers = solver_input.get("containers", [])

        if len(all_tasks) < self.min_tasks_to_split:
            logger.info(f"[SpatialDecomposer] 分割不要 (tasks={len(all_tasks)})")
            return [solver_input]

        container_block = self._map_container_to_block(containers)

        task_block: Dict[str, Optional[int]] = {}
        for t in all_tasks:
            cid = str(t.get("containerId", ""))
            task_block[str(t["id"])] = container_block.get(cid)

        resource_tasks:      Dict[str, List[str]] = {}
        unassigned_task_ids: List[str]            = []

        for t in all_tasks:
            rid = _get_resource_id(t)
            if rid:
                resource_tasks.setdefault(rid, []).append(str(t["id"]))
            else:
                unassigned_task_ids.append(str(t["id"]))

        resource_blocks: Dict[str, set] = {}
        for rid, tids in resource_tasks.items():
            blocks = {task_block[tid] for tid in tids if task_block.get(tid) is not None}
            resource_blocks[rid] = blocks

        resources = list(resource_blocks.keys())
        n         = len(resources)
        adj: Dict[str, set] = {r: set() for r in resources}
        for i in range(n):
            for j in range(i + 1, n):
                ri, rj = resources[i], resources[j]
                if resource_blocks[ri] & resource_blocks[rj]:
                    adj[ri].add(rj)
                    adj[rj].add(ri)

        components = self._connected_components(resources, adj)

        if len(components) <= 1:
            logger.info("[SpatialDecomposer] 独立パーティションなし — シリアル処理")
            return [solver_input]

        logger.info(
            f"[SpatialDecomposer] {len(components)} パーティションに分割 "
            f"(resources={n}, tasks={len(all_tasks)}, "
            f"unassigned={len(unassigned_task_ids)})"
        )

        all_task_map = {str(t["id"]): t for t in all_tasks}
        sub_dsls     = []

        for idx, component_resources in enumerate(components):
            comp_resource_set  = set(component_resources)
            partition_task_ids = [
                str(t["id"]) for t in all_tasks
                if _get_resource_id(t) in comp_resource_set
            ]
            if idx == 0 and unassigned_task_ids:
                seen = set(partition_task_ids)
                for tid in unassigned_task_ids:
                    if tid not in seen:
                        partition_task_ids.append(tid)

            if not partition_task_ids:
                continue

            partition_tasks = [
                all_task_map[tid] for tid in partition_task_ids if tid in all_task_map
            ]
            block_task_ids  = set(partition_task_ids)

            sp_constraint = {
                "type": "spatial_partition",
                "params": {
                    "partition_id":              f"partition_{idx}",
                    "task_ids":                  partition_task_ids,
                    "enforce_no_overlap_within": True,
                },
            }

            sub_dsl = copy.deepcopy(solver_input)
            sub_dsl["tasks"] = partition_tasks
            filtered = _filter_constraints_for_block(
                sub_dsl.get("constraints", []), block_task_ids, partition_tasks
            )
            sub_dsl["constraints"] = [sp_constraint] + filtered
            sub_dsl.setdefault("metadata", {})["spatial_partition"] = idx
            sub_dsls.append(sub_dsl)

        self._log_split_info(solver_input, sub_dsls, elapsed=time.perf_counter() - t0)
        return sub_dsls if sub_dsls else [solver_input]

    def solve_sequentially(
        self,
        solver_input: Dict[str, Any],
        solve_fn,
    ) -> Dict[str, Any]:
        t_start    = time.perf_counter()
        partitions = self.split(solver_input)
        if len(partitions) == 1:
            return solve_fn(partitions[0])

        results: List[Dict] = []
        for idx, sub_dsl in enumerate(partitions):
            try:
                result = solve_fn(sub_dsl)
            except Exception as exc:
                logger.error(
                    f"[SpatialDecomposer] Partition {idx} failed: {exc}", exc_info=True
                )
                result = {"status": "error", "solutions": [], "tasks": [], "issues": []}
            results.append(result)
            task_count = (
                len(result["solutions"][0].get("tasks", []))
                if result.get("solutions") else 0
            )
            logger.info(
                f"[SpatialDecomposer] Partition {idx}/{len(partitions)-1} 完了: "
                f"status={result.get('status')}, tasks={task_count}"
            )

        merged        = self.merge(results)
        solve_elapsed = time.perf_counter() - t_start
        _log_evolution_safe("decompose_spatial", {
            "type":            "spatial_decomposition",
            "original_tasks":  len(solver_input.get("tasks", [])),
            "partitions":      len(partitions),
            "partition_sizes": [len(d.get("tasks", [])) for d in partitions],
            "ok_partitions":   sum(1 for r in results if r.get("status") == "ok"),
            "makespan":        merged.get("makespan", 0),
            "solve_elapsed":   round(solve_elapsed, 3),
            "mode":            "sequential",
        })
        return merged

    def solve_parallel(
        self,
        solver_input: Dict[str, Any],
        solver_class_path: str,
        max_workers: Optional[int] = None,
    ) -> Dict[str, Any]:
        t_start    = time.perf_counter()
        partitions = self.split(solver_input)
        if len(partitions) == 1:
            module_path, class_name = solver_class_path.rsplit(":", 1)
            import importlib
            mod = importlib.import_module(module_path)
            result = getattr(mod, class_name)().solve(partitions[0])
            return result

        n_workers = max_workers or int(
            os.environ.get("DECOMPOSER_WORKERS", "0")
        ) or None

        logger.info(
            f"[SpatialDecomposer.solve_parallel] "
            f"partitions={len(partitions)}, workers={n_workers or 'auto'}"
        )

        results: List[Optional[Dict]] = [None] * len(partitions)
        args_list = [(sub_dsl, solver_class_path) for sub_dsl in partitions]

        with ProcessPoolExecutor(max_workers=n_workers) as pool:
            future_to_idx = {
                pool.submit(_solve_partition_worker, args): idx
                for idx, args in enumerate(args_list)
            }
            for future in as_completed(future_to_idx):
                idx = future_to_idx[future]
                try:
                    results[idx] = future.result()
                    task_count = (
                        len(results[idx]["solutions"][0].get("tasks", []))
                        if results[idx].get("solutions") else 0
                    )
                    logger.info(
                        f"[SpatialDecomposer.solve_parallel] "
                        f"Partition {idx} 完了: "
                        f"status={results[idx].get('status')}, tasks={task_count}"
                    )
                except Exception as exc:
                    logger.error(
                        f"[SpatialDecomposer.solve_parallel] "
                        f"Partition {idx} failed: {exc}", exc_info=True
                    )
                    results[idx] = {
                        "status": "error", "solutions": [], "tasks": [], "issues": []
                    }

        results = [
            r if r is not None else
            {"status": "error", "solutions": [], "tasks": [], "issues": []}
            for r in results
        ]

        merged        = self.merge(results)
        solve_elapsed = time.perf_counter() - t_start
        _log_evolution_safe("decompose_spatial", {
            "type":            "spatial_decomposition",
            "original_tasks":  len(solver_input.get("tasks", [])),
            "partitions":      len(partitions),
            "partition_sizes": [len(d.get("tasks", [])) for d in partitions],
            "ok_partitions":   sum(1 for r in results if r.get("status") == "ok"),
            "makespan":        merged.get("makespan", 0),
            "solve_elapsed":   round(solve_elapsed, 3),
            "mode":            "parallel",
            "workers":         n_workers,
        })
        return merged

    def merge(self, results: List[Dict[str, Any]]) -> Dict[str, Any]:
        if not results:
            return {"status": "error", "solutions": [], "tasks": [], "issues": []}
        if len(results) == 1:
            return results[0]

        merged_tasks: Dict[str, Dict] = {}
        all_issues:   Dict[str, Dict] = {}

        for result in results:
            for sol in result.get("solutions", []):
                for t in sol.get("tasks", []):
                    merged_tasks[str(t["id"])] = t
            for issue in result.get("issues", []):
                all_issues[issue["id"]] = issue

        tasks  = list(merged_tasks.values())
        mspan  = max((t["end"] for t in tasks), default=0)
        status = "ok" if any(r.get("status") == "ok" for r in results) else "error"
        first  = results[0]

        logger.info(
            f"[SpatialDecomposer.merge] partitions={len(results)}, "
            f"tasks={len(tasks)}, makespan={mspan}"
        )

        return {
            "status":    status,
            "solutions": [{"name": "Plan A", "tasks": tasks, "makespan": mspan, "penalty": 0}],
            "tasks":     tasks,
            "makespan":  mspan,
            "issues":    list(all_issues.values()),
            "containers":    first.get("containers", []),
            "yard_limits":   first.get("yard_limits", {}),
            "vessel_limits": first.get("vessel_limits", {}),
            "_decompose_meta": {
                "type":            "spatial_decomposition",
                "partition_count": len(results),
                "block_size":      self.block_size,
            },
        }

    def _map_container_to_block(self, containers: List[Dict]) -> Dict[str, Optional[int]]:
        result: Dict[str, Optional[int]] = {}
        for c in containers:
            cid = str(c.get("id", ""))
            bay = c.get("yard", {}).get("bay")
            if bay is not None:
                try:
                    result[cid] = int(bay) // self.block_size
                except (ValueError, TypeError):
                    result[cid] = None
            else:
                result[cid] = None
        return result

    def _connected_components(
        self, nodes: List[str], adj: Dict[str, set]
    ) -> List[List[str]]:
        visited:    set              = set()
        components: List[List[str]] = []
        for start in nodes:
            if start in visited:
                continue
            stack     = [start]
            component = []
            while stack:
                node = stack.pop()
                if node in visited:
                    continue
                visited.add(node)
                component.append(node)
                stack.extend(adj[node] - visited)
            components.append(component)
        return components

    def _log_split_info(self, solver_input: Dict, sub_dsls: List[Dict], elapsed: float) -> None:
        payload = {
            "type":            "spatial_decomposition",
            "original_tasks":  len(solver_input.get("tasks", [])),
            "partitions":      len(sub_dsls),
            "partition_sizes": [len(d.get("tasks", [])) for d in sub_dsls],
            "block_size":      self.block_size,
            "split_elapsed":   round(elapsed, 3),
        }
        logger.info(f"[SpatialDecomposer] 分割完了: {payload}")
        _log_evolution_safe("decompose_spatial_split", payload)


# =============================================================================
# DecomposerFactory  — 規模ベース自動選択
# =============================================================================

class DecomposerFactory:
    """
    タスク規模に応じて適切な Decomposer を自動選択する。

    problem_class による振り分け:
      HorizonDecomposer / SpatialDecomposer（タスク数ベース）

    閾値（Community Edition デフォルト）:
      tasks >= 150 → HorizonDecomposer
      tasks  50-149 → SpatialDecomposer
      tasks  < 50  → None（分割不要）

    正規ライセンス（Enterprise）への移行時:
      環境変数 CPLEX_LICENSED=1 を設定するだけで閾値が大幅に上がり、
      通常規模の問題では Decomposer をバイパスする。

    閾値一覧:
                        Community   Licensed
      Horizon 閾値        150        10000
      Spatial 閾値         50         3000

    V6.5: HorizonDecomposer, SpatialDecomposer の内部実装変更のみ。
          Factory の選択ロジックは変更なし。
    2026-07-27: EventStaffing専用だったStaffDecomposerを削除（呼び出し元の
          EventStaffingドメインが2026-07-11に削除済みで、到達不能なデッド
          コードだったため。app.py/_step4()の参照・decomposer.py内クラス
          本体・本FactoryのEventStaffing分岐を一括削除）。
    """

    TASK_THRESHOLD_HORIZON_CE  = 150
    TASK_THRESHOLD_SPATIAL_CE  =  50
    TASK_THRESHOLD_HORIZON_ENT = 10000
    TASK_THRESHOLD_SPATIAL_ENT =  3000

    @classmethod
    def _is_licensed(cls) -> bool:
        return os.environ.get("CPLEX_LICENSED", "0").strip() == "1"

    @classmethod
    def _thresholds(cls):
        if cls._is_licensed():
            return cls.TASK_THRESHOLD_HORIZON_ENT, cls.TASK_THRESHOLD_SPATIAL_ENT
        return cls.TASK_THRESHOLD_HORIZON_CE, cls.TASK_THRESHOLD_SPATIAL_CE

    @classmethod
    def for_input(
        cls,
        solver_input: Dict[str, Any],
        prefer: str = "horizon",  # 後方互換のため残す（無視される）
    ) -> "Optional[HorizonDecomposer | SpatialDecomposer]":
        licensed = cls._is_licensed()
        metadata = solver_input.get("metadata", {})
        problem_class = metadata.get("problem_class", "")

        # ----------------------------------------------------------------
        # skip_decompose フラグ: metadata.skip_decompose=true で強制バイパス。
        # デバッグ・小規模テスト・ライセンス環境への移行期に使用。
        # 環境変数 DECOMPOSER_SKIP=1 でも同様にバイパス可能。
        # ----------------------------------------------------------------
        if metadata.get("skip_decompose") or os.environ.get("DECOMPOSER_SKIP", "0") == "1":
            logger.info(
                f"[DecomposerFactory] skip_decompose=true — 分割をバイパス "
                f"(problem_class={problem_class!r})"
            )
            return None

        n = len(solver_input.get("tasks", []))
        threshold_horizon, threshold_spatial = cls._thresholds()

        if n >= threshold_horizon:
            logger.info(
                f"[DecomposerFactory] HorizonDecomposer を選択 "
                f"(tasks={n}, licensed={licensed})"
            )
            return HorizonDecomposer()

        if n >= threshold_spatial:
            logger.info(
                f"[DecomposerFactory] SpatialDecomposer を選択 "
                f"(tasks={n}, licensed={licensed})"
            )
            return SpatialDecomposer()

        logger.info(
            f"[DecomposerFactory] 分割不要 (tasks={n}, licensed={licensed})"
        )
        return None