"""
Backend/solvers/yard/constraint_applier.py

YardConstraintApplier — YardPlanning(港湾コンテナ作業)ドメイン専用の制約適用クラス。

BaseConstraintApplier (層A) を継承し、no_overlap / precedence /
shift_window / shift_break は基底実装をそのまま利用する。
ここに残るのは YardPlanning 固有の制約ハンドラ (層C) のみ:

  physical_stack_order : 物理スタック順序制約
  phase_separation     : フェーズ分離制約（DISCHARGE→LOAD）
  crane_interference   : クレーン干渉制約
  attribute_zone       : 属性ゾーン制約
  rolling_horizon      : Rolling Horizon サブ問題境界制約
  spatial_partition    : Spatial Decomposition 結合制約

cplex_dynamic_solver.ConstraintApplier (V6) からの移行に伴う動作変更:
  - no_overlap / precedence / shift_window / shift_break は
    BaseConstraintApplier に移動。ロジックは完全に同一（移植元: V6）。
    YardPlanning の windows/breaks は秒単位のため
    time_unit_seconds=True (デフォルト) のまま使用する。
"""

import logging
from typing import Any, Callable, Dict, List

from ..base.constraint_applier import BaseConstraintApplier

logger = logging.getLogger(__name__)


class YardConstraintApplier(BaseConstraintApplier):
    """
    YardPlanning用 ConstraintApplier。

    is_optional_intervals = False (デフォルト): 全タスクのintervalは必須。
    time_unit_seconds = True (デフォルト): windows/breaksの値は秒。
    multi_window_policy = "logical_or" (デフォルト): 複数窓はlogical_orで処理。
    """

    def __init__(
        self,
        mdl: Any,
        task_itvs: Dict[str, Any],
        resource_usage: Dict[str, List],
        all_tasks: List[Dict],
        containers: List[Dict],
        crane_windows: Dict,
        break_windows: Dict,
    ):
        self.resource_usage = resource_usage
        self.all_tasks = all_tasks
        self.containers = containers
        self.crane_windows = crane_windows
        self.break_windows = break_windows
        super().__init__(mdl, task_itvs)

    # -------------------------------------------------------------------------
    # ドメイン固有ハンドラの登録 (層C)
    # -------------------------------------------------------------------------

    def _register_domain_handlers(self) -> None:
        self._CONSTRAINT_HANDLERS.update({
            "physical_stack_order": self._handle_physical_stack_order,
            "phase_separation":     self._handle_phase_separation,
            "crane_interference":   self._handle_crane_interference,
            "attribute_zone":       self._handle_attribute_zone,
            "rolling_horizon":      self._handle_rolling_horizon,
            "spatial_partition":    self._handle_spatial_partition,
        })

    # -------------------------------------------------------------------------
    # YardPlanning固有ハンドラ（V6から完全移植、動作変更なし）
    # -------------------------------------------------------------------------

    def _handle_physical_stack_order(self, p: Dict):
        stack = p.get("stack", [])
        delay = p.get("delay", 10)
        for i in range(len(stack) - 1):
            a_id = str(stack[i]["task_id"])
            b_id = str(stack[i + 1]["task_id"])
            if a_id in self.task_itvs and b_id in self.task_itvs:
                self.mdl.add(self.mdl.end_before_start(
                    self.task_itvs[a_id], self.task_itvs[b_id], delay=delay))

    def _handle_phase_separation(self, p: Dict):
        phase_a_ids = [str(tid) for tid in p.get("phase_a_tasks", [])]
        phase_b_ids = [str(tid) for tid in p.get("phase_b_tasks", [])]
        itvs_a = [self.task_itvs[tid] for tid in phase_a_ids if tid in self.task_itvs]
        itvs_b = [self.task_itvs[tid] for tid in phase_b_ids if tid in self.task_itvs]
        if itvs_a and itvs_b:
            max_a_end = self.mdl.max([self.mdl.end_of(iv) for iv in itvs_a])
            for iv in itvs_b:
                self.mdl.add(self.mdl.start_of(iv) >= max_a_end)

    def _handle_crane_interference(self, p: Dict):
        itvs_a = self.resource_usage.get(p.get("crane_a", ""), [])
        itvs_b = self.resource_usage.get(p.get("crane_b", ""), [])
        if itvs_a and itvs_b:
            self.mdl.add(self.mdl.no_overlap(itvs_a + itvs_b))

    def _handle_attribute_zone(self, p: dict):
        """
        属性ゾーン制約。特定属性を持つコンテナの作業を時間窓内に限定する。

        params:
          zone_id        : ゾーン識別子（ログ用）
          container_ids  : 対象コンテナID一覧
          attribute      : 属性名（例: "REEFER", "IMO", "OOG"）
          time_window    : {start: int, end: int}  ← 秒単位
                           この窓外に LOAD/DISCHARGE/PICK/PLACE タスクが
                           はみ出さないよう制約を追加する。
          operations     : 対象オペレーション種別
                           （デフォルト: ["LOAD","DISCHARGE","PICK","PLACE"]）

        time_window 未指定時は制約追加をスキップする。
        イシュー検知は solver 側の _detect_issues が行う。
        """
        zone_id       = p.get("zone_id", "unknown")
        container_ids = {str(cid) for cid in p.get("container_ids", [])}
        attribute     = p.get("attribute", "")
        window        = p.get("time_window")  # {start: int, end: int} or None
        target_ops    = set(p.get("operations", ["LOAD", "DISCHARGE", "PICK", "PLACE"]))

        if not window:
            logger.debug(f"[AttributeZone:{zone_id}] time_window 未指定 — CP制約追加スキップ")
            return

        w_start = window.get("start", 0)   # 秒
        w_end   = window.get("end",   0)   # 秒

        if w_end <= w_start:
            logger.warning(
                f"[AttributeZone:{zone_id}] time_window が無効 "
                f"(start={w_start}, end={w_end}) — スキップ"
            )
            return

        applied = 0
        skipped = 0
        for t in self.all_tasks:
            tid = str(t.get("id", ""))
            cid = str(t.get("containerId", ""))
            op  = t.get("operation", "")

            if container_ids and cid not in container_ids:
                continue
            if op not in target_ops:
                continue
            if tid not in self.task_itvs:
                skipped += 1
                continue

            itv = self.task_itvs[tid]
            self.mdl.add(self.mdl.start_of(itv) >= w_start)
            self.mdl.add(self.mdl.end_of(itv)   <= w_end)
            applied += 1
            logger.debug(f"[AttributeZone:{zone_id}] {cid}/{op}: [{w_start}, {w_end}] 窓制約を追加")

        logger.info(
            f"[AttributeZone:{zone_id}] attr='{attribute}', "
            f"window=[{w_start},{w_end}], applied={applied}, skipped={skipped}"
        )

    def _handle_rolling_horizon(self, p: dict):
        """
        Rolling Horizon サブ問題間の境界制約。

        params:
          horizon_id    : サブ問題の識別子（ログ用）
          initial_state : [{task_id, min_start}] 前ブロックからの継続制約
          deadline      : このブロックの終了デッドライン（秒）
          task_ids      : [str] deadline を適用するタスクID一覧。
                          未指定または空リストの場合は全タスクに適用（後方互換）。
        """
        horizon_id    = p.get("horizon_id", "unknown")
        initial_state = p.get("initial_state", [])
        deadline      = p.get("deadline")
        scoped_ids    = {str(tid) for tid in p.get("task_ids", [])}

        # --- 前ブロック継続制約 ---
        for state in initial_state:
            tid       = str(state.get("task_id", ""))
            min_start = state.get("min_start", 0)
            if tid in self.task_itvs:
                self.mdl.add(self.mdl.start_of(self.task_itvs[tid]) >= min_start)
                logger.debug(f"[RollingHorizon:{horizon_id}] {tid}: start >= {min_start}")

        # --- deadline 制約（task_ids で絞る）---
        if deadline is not None:
            if scoped_ids:
                targets = {tid: itv for tid, itv in self.task_itvs.items() if tid in scoped_ids}
                for tid, itv in targets.items():
                    self.mdl.add(self.mdl.end_of(itv) <= deadline)
                logger.debug(
                    f"[RollingHorizon:{horizon_id}] "
                    f"{len(targets)} タスクに deadline={deadline} を適用"
                )
            else:
                for itv in self.task_itvs.values():
                    self.mdl.add(self.mdl.end_of(itv) <= deadline)
                logger.debug(
                    f"[RollingHorizon:{horizon_id}] "
                    f"全タスク({len(self.task_itvs)}) に deadline={deadline} を適用（後方互換）"
                )

    def _handle_spatial_partition(self, p: dict):
        """
        Spatial Decomposition の結合制約。

        params:
          partition_id : パーティション識別子（ログ用）
          task_ids     : このパーティションに属するタスクID一覧
          enforce_no_overlap_within : bool（デフォルト True）
                          パーティション内タスクのリソース非重複を明示的に追加するか。

        設計:
          - 同一リソース(crane等)を使うタスク同士だけに no_overlap を追加する。
          - パーティション間の独立性確保は decomposer.py が担う。
            ConstraintApplier はあくまでパーティション内制約の実装にとどめる。
        """
        partition_id = p.get("partition_id", "unknown")
        scoped_ids   = {str(tid) for tid in p.get("task_ids", [])}
        enforce      = p.get("enforce_no_overlap_within", True)

        if not scoped_ids:
            logger.warning(f"[SpatialPartition:{partition_id}] task_ids が空 — スキップ")
            return

        scoped_itvs = {tid: self.task_itvs[tid] for tid in scoped_ids if tid in self.task_itvs}

        missing = scoped_ids - set(self.task_itvs.keys())
        if missing:
            logger.warning(
                f"[SpatialPartition:{partition_id}] "
                f"task_itvs に未登録のタスク {len(missing)} 件 — スキップ: {missing}"
            )

        if not enforce:
            logger.info(
                f"[SpatialPartition:{partition_id}] "
                f"enforce=False のためCP制約追加をスキップ (tasks={len(scoped_itvs)})"
            )
            return

        resource_groups: dict = {}
        for t in self.all_tasks:
            tid = str(t.get("id", ""))
            if tid not in scoped_ids:
                continue
            rid = t.get("resourceId") or t.get("resource")
            if rid:
                resource_groups.setdefault(rid, []).append(tid)

        added = 0
        for rid, tids in resource_groups.items():
            itvs = [self.task_itvs[tid] for tid in tids if tid in self.task_itvs]
            if len(itvs) > 1:
                self.mdl.add(self.mdl.no_overlap(itvs))
                added += 1
                logger.debug(
                    f"[SpatialPartition:{partition_id}] "
                    f"resource='{rid}' に no_overlap 追加 ({len(itvs)} タスク)"
                )

        logger.info(
            f"[SpatialPartition:{partition_id}] "
            f"完了: tasks={len(scoped_itvs)}, "
            f"resource_groups={len(resource_groups)}, no_overlap_added={added}"
        )