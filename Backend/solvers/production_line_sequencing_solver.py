"""
Backend/solvers/production_line_sequencing_solver.py

ProductionLineSequencingSolver — 自動車組立ラインの生産順序最適化
================================================================

solver_input keys:
  - problem_class: str
  - batches: List[Dict]  # 各バッチの属性（id, compatible_lines, line_on_day, line_off_day, vehicle_type）
  - slots: List[Dict]    # 各スロットの属性（id, line_id, day, start_hour）
  - lines: List[Dict]    # 組立ライン一覧（id, name）
  - days: List[int]      # 対象日一覧（ day番号、例: [1,2,3]）
  - vehicle_types: List[Dict]  # 車種定義（id, name, daily_limit, priority_order）
  - distribution_exceptions: List[Dict]  # 分散ルール例外（period_days, vehicle_type_id, max_per_day, min_per_day）
  - config: Dict
  - issue_statuses: Dict
"""

from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)

_SOLVER_VERSION = "production_line_sequencing_v1.0"


class ProductionLineSequencingSolver:
    def __init__(self, solver_input: dict) -> None:
        self.dsl = solver_input

    def solve(self) -> dict:
        batches = self.dsl.get("batches", [])
        slots = self.dsl.get("slots", [])
        lines = self.dsl.get("lines", [])
        days = self.dsl.get("days", [])
        vehicle_types = self.dsl.get("vehicle_types", [])
        distribution_exceptions = self.dsl.get("distribution_exceptions", [])
        config = self.dsl.get("config", {})
        issue_statuses = self.dsl.get("issue_statuses", {})

        if not batches:
            return self._make_result(
                feasible=False,
                assignments=[],
                issues=[{
                    "id": "solve_failed",
                    "severity": "CRITICAL",
                    "title": "入力データ不足",
                    "message": "バッチデータが空です。",
                    "relatedContainerIds": [],
                }],
                metadata={"problem_class": "ProductionLineSequencing"},
            )

        if not slots:
            return self._make_result(
                feasible=False,
                assignments=[],
                issues=[{
                    "id": "solve_failed",
                    "severity": "CRITICAL",
                    "title": "スロットデータ不足",
                    "message": "スロットデータが空です。",
                    "relatedContainerIds": [],
                }],
                metadata={"problem_class": "ProductionLineSequencing"},
            )

        try:
            from solvers.base.engine_select import get_solver_engine, CPSAT
            engine = get_solver_engine(config)
            if engine == CPSAT:
                assignments, msol, solve_status = self._solve_with_cpsat(
                    batches, slots, lines, days, vehicle_types, distribution_exceptions, config
                )
            else:
                assignments, msol, solve_status = self._solve_with_cpo(
                    batches, slots, lines, days, vehicle_types, distribution_exceptions, config
                )
        except Exception as e:
            from solvers.base.ce_limit_lns import is_ce_limit_exceeded, CeLimitExceededError
            if is_ce_limit_exceeded(e):
                raise CeLimitExceededError(str(e)) from e
            logger.error(f"[ProductionLineSequencing] mdl.solve() 例外: {e}", exc_info=True)
            from solvers.base.solver_error_result import build_solver_crash_issue, solver_crash_extra_fields
            result = {
                "status": "ok",
                "feasible": False,
                "metadata": {"problem_class": "ProductionLineSequencing"},
                "solutions": [],
                "issues": [build_solver_crash_issue(e)],
                "_solver_version": _SOLVER_VERSION,
            }
            result.update(solver_crash_extra_fields(e))
            return result

        if assignments is None:
            return self._make_result(
                feasible=False,
                assignments=[],
                issues=[{
                    "id": "solve_failed",
                    "severity": "CRITICAL",
                    "title": "制約を満たす割り当てが見つかりませんでした",
                    "message": "全バッチをスロットに割り当てる解が存在しません。バッチ数・スロット数・制約条件を見直してください。",
                    "relatedContainerIds": [],
                }],
                metadata={"problem_class": "ProductionLineSequencing"},
            )

        # 解抽出異常検知
        from solvers.base.issue_rules import build_full_unassignment_issue
        issues = []
        anomaly = build_full_unassignment_issue(
            assigned_count=len(assignments),
            total_count=len(batches),
            entity_label="バッチ",
            extra_hint="get_var_solution() での解抽出処理",
        )
        if anomaly:
            issues.append(anomaly)

        issues.extend(self._detect_issues(assignments, batches, slots, vehicle_types, days, issue_statuses))

        # KPI計算
        line_usage = self._calc_line_usage(assignments, days)
        balance_score = self._calc_balance_score(line_usage)

        from solvers.base.solution_extraction import safe_objective_value
        obj_val = safe_objective_value(msol, fallback=float(balance_score)) if msol else float(balance_score)

        return self._make_result(
            feasible=True,
            assignments=assignments,
            issues=issues,
            metadata={"problem_class": "ProductionLineSequencing"},
            kpi={
                "total_batches": len(batches),
                "assigned_batches": len(assignments),
                "line_usage": line_usage,
                "balance_score": balance_score,
                "solve_status": solve_status,
                "objective_value": obj_val,
            },
        )

    def _solve_with_cpo(
        self,
        batches: List[Dict],
        slots: List[Dict],
        lines: List[Dict],
        days: List[int],
        vehicle_types: List[Dict],
        distribution_exceptions: List[Dict],
        config: Dict,
    ) -> Tuple[Optional[List[Dict]], Any, str]:
        from docplex.cp.model import CpoModel

        time_limit = config.get("time_limit_sec", 30)
        mdl = CpoModel(name="ProductionLineSequencing")

        # --- インデックス構築 ---
        slot_ids = [s["id"] for s in slots]
        batch_ids = [b["id"] for b in batches]
        slot_by_id = {s["id"]: s for s in slots}
        batch_by_id = {b["id"]: b for b in batches}

        # slot番号(0-indexed)とslot_idのマッピング
        slot_idx_by_id = {s["id"]: i for i, s in enumerate(slots)}
        n_slots = len(slots)
        n_batches = len(batches)

        # --- 決定変数: batch_assignment[i] = スロット番号 (0..n_slots-1) ---
        # all-different で全スロットに1バッチずつ割り当て
        batch_vars = {}
        for b in batches:
            bid = b["id"]
            # 配置可能スロットのフィルタリング
            compatible_slot_indices = self._get_compatible_slot_indices(b, slots, slot_idx_by_id)
            if not compatible_slot_indices:
                logger.warning(f"[ProductionLineSequencing] バッチ {bid} に配置可能なスロットがありません")
                return None, None, "Infeasible"
            batch_vars[bid] = mdl.integer_var(
                domain=compatible_slot_indices,
                name=f"slot_{bid}"
            )

        # --- all-different 制約 (各スロットに最大1バッチ) ---
        mdl.add(mdl.all_diff(list(batch_vars.values())))

        # --- Even Distribution 制約 ---
        vtype_priority = {}
        vtype_daily_limit = {}
        for vt in vehicle_types:
            vtype_daily_limit[vt["id"]] = vt.get("daily_limit", 99)
            vtype_priority[vt["id"]] = vt.get("priority_order", 99)

        # distribution_exceptions の解析
        exception_map: Dict[Tuple[int, str], Dict] = {}
        for exc in distribution_exceptions:
            for d in exc.get("period_days", []):
                exception_map[(d, exc["vehicle_type_id"])] = exc

        for day in days:
            # その日のスロットインデックス一覧
            day_slot_indices = [slot_idx_by_id[s["id"]] for s in slots if s["day"] == day]
            if not day_slot_indices:
                continue

            for vt in vehicle_types:
                vtid = vt["id"]
                exc = exception_map.get((day, vtid))
                if exc:
                    max_per_day = exc.get("max_per_day", vtype_daily_limit[vtid])
                else:
                    max_per_day = vtype_daily_limit[vtid]

                # この日・この車種のバッチが配置されるスロットの合計
                vt_batches = [b for b in batches if b.get("vehicle_type") == vtid]
                if not vt_batches:
                    continue

                count_expr = mdl.sum([
                    mdl.element(
                        [1 if si in day_slot_indices else 0 for si in range(n_slots)],
                        batch_vars[b["id"]]
                    )
                    for b in vt_batches
                ])
                mdl.add(count_expr <= max_per_day)

                # min_per_day (例外のみ)
                if exc and "min_per_day" in exc:
                    mdl.add(count_expr >= exc["min_per_day"])

        # --- Batting Order 制約 ---
        # 同一日内: 車種優先順位リスト順。後の車種を前の車種より前のスロットに置けない。
        # 実装: 同一日内で、車種A(priority=1)のバッチのスロット時間 <= 車種B(priority=2)のバッチのスロット時間
        # "スロット時間"はスロットのstart_hour(同一ライン同一日で単調増加している前提)
        # より厳密には: 同一日の中でpriority_order値が小さい車種のスロットstart_hourが、
        # priority_order値が大きい車種のスロットstart_hourより遅くなってはいけない

        # スロットインデックス -> start_hour のマッピング配列
        slot_hour_arr = [slot_by_id[sid]["start_hour"] for sid in slot_ids]

        sorted_vtypes = sorted(vehicle_types, key=lambda v: v.get("priority_order", 99))
        for day in days:
            day_slots = [s for s in slots if s["day"] == day]
            if not day_slots:
                continue
            day_slot_idx_set = set(slot_idx_by_id[s["id"]] for s in day_slots)

            for i in range(len(sorted_vtypes) - 1):
                vt_higher = sorted_vtypes[i]    # 高優先（より早く配置すべき）
                vt_lower = sorted_vtypes[i + 1]  # 低優先

                higher_batches = [b for b in batches
                                  if b.get("vehicle_type") == vt_higher["id"]]
                lower_batches = [b for b in batches
                                 if b.get("vehicle_type") == vt_lower["id"]]

                if not higher_batches or not lower_batches:
                    continue

                for bh in higher_batches:
                    for bl in lower_batches:
                        bh_var = batch_vars[bh["id"]]
                        bl_var = batch_vars[bl["id"]]

                        # 両方が同じ日にあるかどうかを制約で表現
                        # bh_var が day_slot_idx_set に属し、かつ bl_var も属する場合のみ
                        # bh の start_hour <= bl の start_hour を要求する
                        bh_in_day = mdl.element(
                            [1 if si in day_slot_idx_set else 0 for si in range(n_slots)],
                            bh_var
                        )
                        bl_in_day = mdl.element(
                            [1 if si in day_slot_idx_set else 0 for si in range(n_slots)],
                            bl_var
                        )
                        both_in_day = mdl.logical_and(bh_in_day == 1, bl_in_day == 1)

                        bh_hour = mdl.element(slot_hour_arr, bh_var)
                        bl_hour = mdl.element(slot_hour_arr, bl_var)

                        # both_in_day => bh_hour <= bl_hour
                        mdl.add(mdl.if_then(both_in_day, bh_hour <= bl_hour))

        # --- 目的関数: ライン間負荷平準化 ---
        # 各日・各ラインの使用スロット数の最大 - 最小 を最小化
        line_ids = [l["id"] for l in lines]
        if len(line_ids) >= 2:
            balance_terms = []
            for day in days:
                day_line_counts = []
                for lid in line_ids:
                    day_line_slots = [slot_idx_by_id[s["id"]]
                                      for s in slots if s["day"] == day and s["line_id"] == lid]
                    if not day_line_slots:
                        day_line_counts.append(mdl.integer_var(0, 0, name=f"cnt_{lid}_{day}"))
                        continue
                    cnt = mdl.sum([
                        mdl.element(
                            [1 if si in set(day_line_slots) else 0 for si in range(n_slots)],
                            batch_vars[b["id"]]
                        )
                        for b in batches
                    ])
                    day_line_counts.append(cnt)
                if len(day_line_counts) >= 2:
                    balance_terms.append(mdl.max(day_line_counts) - mdl.min(day_line_counts))

            if balance_terms:
                mdl.add(mdl.minimize(mdl.sum(balance_terms)))

        # --- ソルブ ---
        try:
            msol = mdl.solve(TimeLimit=time_limit, LogVerbosity="Quiet")
        except Exception as e:
            from solvers.base.ce_limit_lns import is_ce_limit_exceeded, CeLimitExceededError
            if is_ce_limit_exceeded(e):
                raise CeLimitExceededError(str(e)) from e
            raise

        if msol is None or not msol:
            return None, msol, "Infeasible"

        solve_status = str(msol.get_solve_status() or "Feasible")

        # --- 解抽出 ---
        assignments = []
        for b in batches:
            bid = b["id"]
            bvar = batch_vars[bid]
            try:
                slot_idx = msol.get_value(bvar)
                if slot_idx is None:
                    continue
                slot_idx = int(slot_idx)
                slot = slots[slot_idx]
                assignments.append({
                    "batch_id": bid,
                    "slot_id": slot["id"],
                    "line_id": slot["line_id"],
                    "day": slot["day"],
                    "start_hour": slot["start_hour"],
                    "vehicle_type": b.get("vehicle_type", ""),
                    "batch_name": b.get("name", bid),
                    "line_name": slot.get("line_name", slot["line_id"]),
                })
            except Exception as ex:
                logger.warning(f"[ProductionLineSequencing] 解抽出失敗 batch={bid}: {ex}")
                continue

        return assignments, msol, solve_status

    def _solve_with_cpsat(
        self,
        batches: List[Dict],
        slots: List[Dict],
        lines: List[Dict],
        days: List[int],
        vehicle_types: List[Dict],
        distribution_exceptions: List[Dict],
        config: Dict,
    ) -> Tuple[Optional[List[Dict]], Any, str]:
        """CP-SAT(CPMpy)版。_solve_with_cpo()と同一の制約セット・目的関数を実装する。"""
        import cpmpy as cp
        from cpmpy.expressions.globalconstraints import InDomain, AllDifferent
        from solvers.base.engine_select import cpmpy_optimality_metadata

        time_limit = config.get("time_limit_sec", 30)

        slot_ids = [s["id"] for s in slots]
        slot_by_id = {s["id"]: s for s in slots}
        slot_idx_by_id = {s["id"]: i for i, s in enumerate(slots)}
        n_slots = len(slots)

        # --- 決定変数: batch_vars[bid] = スロット番号 (配置可能スロットに限定) ---
        batch_vars: Dict[str, Any] = {}
        m = cp.Model()
        for b in batches:
            bid = b["id"]
            compatible_slot_indices = self._get_compatible_slot_indices(b, slots, slot_idx_by_id)
            if not compatible_slot_indices:
                logger.warning(f"[ProductionLineSequencing][cpsat] バッチ {bid} に配置可能なスロットがありません")
                return None, None, "Infeasible"
            v = cp.intvar(min(compatible_slot_indices), max(compatible_slot_indices), name=f"slot_{bid}")
            batch_vars[bid] = v
            m += InDomain(v, compatible_slot_indices)

        # --- all-different 制約 ---
        m += AllDifferent(list(batch_vars.values()))

        # --- Even Distribution 制約 ---
        vtype_daily_limit = {}
        for vt in vehicle_types:
            vtype_daily_limit[vt["id"]] = vt.get("daily_limit", 99)

        exception_map: Dict[Tuple[int, str], Dict] = {}
        for exc in distribution_exceptions:
            for d in exc.get("period_days", []):
                exception_map[(d, exc["vehicle_type_id"])] = exc

        for day in days:
            day_slot_indices = [slot_idx_by_id[s["id"]] for s in slots if s["day"] == day]
            if not day_slot_indices:
                continue
            day_slot_idx_set = set(day_slot_indices)
            day_indicator = [1 if si in day_slot_idx_set else 0 for si in range(n_slots)]

            for vt in vehicle_types:
                vtid = vt["id"]
                exc = exception_map.get((day, vtid))
                max_per_day = exc.get("max_per_day", vtype_daily_limit[vtid]) if exc else vtype_daily_limit[vtid]

                vt_batches = [b for b in batches if b.get("vehicle_type") == vtid]
                if not vt_batches:
                    continue

                count_expr = cp.sum([
                    cp.Element(day_indicator, batch_vars[b["id"]])
                    for b in vt_batches
                ])
                m += (count_expr <= max_per_day)

                if exc and "min_per_day" in exc:
                    m += (count_expr >= exc["min_per_day"])

        # --- Batting Order 制約 ---
        slot_hour_arr = [slot_by_id[sid]["start_hour"] for sid in slot_ids]
        sorted_vtypes = sorted(vehicle_types, key=lambda v: v.get("priority_order", 99))
        for day in days:
            day_slots = [s for s in slots if s["day"] == day]
            if not day_slots:
                continue
            day_slot_idx_set = set(slot_idx_by_id[s["id"]] for s in day_slots)
            day_indicator = [1 if si in day_slot_idx_set else 0 for si in range(n_slots)]

            for i in range(len(sorted_vtypes) - 1):
                vt_higher = sorted_vtypes[i]
                vt_lower = sorted_vtypes[i + 1]
                higher_batches = [b for b in batches if b.get("vehicle_type") == vt_higher["id"]]
                lower_batches = [b for b in batches if b.get("vehicle_type") == vt_lower["id"]]
                if not higher_batches or not lower_batches:
                    continue

                for bh in higher_batches:
                    for bl in lower_batches:
                        bh_var = batch_vars[bh["id"]]
                        bl_var = batch_vars[bl["id"]]

                        bh_in_day = cp.Element(day_indicator, bh_var)
                        bl_in_day = cp.Element(day_indicator, bl_var)
                        both_in_day = (bh_in_day == 1) & (bl_in_day == 1)

                        bh_hour = cp.Element(slot_hour_arr, bh_var)
                        bl_hour = cp.Element(slot_hour_arr, bl_var)

                        m += both_in_day.implies(bh_hour <= bl_hour)

        # --- 目的関数: ライン間負荷平準化 ---
        line_ids = [l["id"] for l in lines]
        if len(line_ids) >= 2:
            balance_terms = []
            for day in days:
                day_line_counts = []
                for lid in line_ids:
                    day_line_slots = [slot_idx_by_id[s["id"]]
                                      for s in slots if s["day"] == day and s["line_id"] == lid]
                    if not day_line_slots:
                        day_line_counts.append(0)
                        continue
                    indicator = [1 if si in set(day_line_slots) else 0 for si in range(n_slots)]
                    cnt = cp.sum([cp.Element(indicator, batch_vars[b["id"]]) for b in batches])
                    day_line_counts.append(cnt)
                if len(day_line_counts) >= 2:
                    balance_terms.append(cp.max(day_line_counts) - cp.min(day_line_counts))

            if balance_terms:
                m.minimize(cp.sum(balance_terms))

        # --- ソルブ ---
        solved = m.solve(solver="ortools", time_limit=time_limit)

        if not solved:
            return None, None, "Infeasible"

        meta = cpmpy_optimality_metadata(m)
        solve_status = meta.get("solve_status") or "Feasible"

        # --- 解抽出 ---
        assignments = []
        for b in batches:
            bid = b["id"]
            bvar = batch_vars[bid]
            try:
                slot_idx = bvar.value()
                if slot_idx is None:
                    continue
                slot_idx = int(slot_idx)
                slot = slots[slot_idx]
                assignments.append({
                    "batch_id": bid,
                    "slot_id": slot["id"],
                    "line_id": slot["line_id"],
                    "day": slot["day"],
                    "start_hour": slot["start_hour"],
                    "vehicle_type": b.get("vehicle_type", ""),
                    "batch_name": b.get("name", bid),
                    "line_name": slot.get("line_name", slot["line_id"]),
                })
            except Exception as ex:
                logger.warning(f"[ProductionLineSequencing][cpsat] 解抽出失敗 batch={bid}: {ex}")
                continue

        return assignments, None, solve_status

    def _get_compatible_slot_indices(
        self, batch: Dict, slots: List[Dict], slot_idx_by_id: Dict[str, int]
    ) -> List[int]:
        """バッチが配置可能なスロットのインデックスリストを返す"""
        compatible_lines = batch.get("compatible_lines", None)  # Noneなら全ライン可
        line_on = batch.get("line_on_day", 1)
        line_off = batch.get("line_off_day", 9999)

        indices = []
        for s in slots:
            # ライン適合チェック
            if compatible_lines is not None and s["line_id"] not in compatible_lines:
                continue
            # line-on/off チェック
            if s["day"] < line_on or s["day"] > line_off:
                continue
            indices.append(slot_idx_by_id[s["id"]])
        return indices

    def _detect_issues(
        self,
        assignments: List[Dict],
        batches: List[Dict],
        slots: List[Dict],
        vehicle_types: List[Dict],
        days: List[int],
        issue_statuses: Dict,
    ) -> List[Dict]:
        issues = []
        assigned_batch_ids = {a["batch_id"] for a in assignments}

        # 未割当バッチの検出
        for b in batches:
            if b["id"] not in assigned_batch_ids:
                iid = f"unassigned_batch_{b['id']}"
                if issue_statuses.get(iid) != "ACCEPTED":
                    issues.append({
                        "id": iid,
                        "severity": "CRITICAL",
                        "title": f"未割当バッチ: {b.get('name', b['id'])}",
                        "message": f"バッチ {b.get('name', b['id'])} がいずれのスロットにも割り当てられませんでした。",
                        "relatedContainerIds": [],
                    })

        # Even Distribution 違反チェック（独立検証）
        vtype_daily_limit = {vt["id"]: vt.get("daily_limit", 99) for vt in vehicle_types}
        day_vtype_count: Dict[Tuple[int, str], int] = {}
        for a in assignments:
            key = (a["day"], a["vehicle_type"])
            day_vtype_count[key] = day_vtype_count.get(key, 0) + 1

        for (day, vtid), count in day_vtype_count.items():
            limit = vtype_daily_limit.get(vtid, 99)
            if count > limit:
                iid = f"distribution_violation_{day}_{vtid}"
                if issue_statuses.get(iid) != "ACCEPTED":
                    issues.append({
                        "id": iid,
                        "severity": "WARNING",
                        "title": f"分散ルール違反: 日{day} 車種{vtid}",
                        "message": f"日{day}の車種{vtid}が{count}バッチ割り当てられており、上限{limit}を超えています。",
                        "relatedContainerIds": [],
                    })

        return issues

    def _calc_line_usage(self, assignments: List[Dict], days: List[int]) -> List[Dict]:
        from collections import defaultdict
        usage: Dict[Tuple[str, int], int] = defaultdict(int)
        for a in assignments:
            usage[(a["line_id"], a["day"])] += 1

        result = []
        for (lid, day), cnt in sorted(usage.items()):
            result.append({"line_id": lid, "day": day, "count": cnt})
        return result

    def _calc_balance_score(self, line_usage: List[Dict]) -> int:
        if not line_usage:
            return 0
        from collections import defaultdict
        day_line: Dict[int, List[int]] = defaultdict(list)
        for u in line_usage:
            day_line[u["day"]].append(u["count"])
        total_imbalance = 0
        for day, counts in day_line.items():
            if len(counts) >= 2:
                total_imbalance += max(counts) - min(counts)
        return total_imbalance

    def _make_result(
        self,
        feasible: bool,
        assignments: List[Dict],
        issues: List[Dict],
        metadata: Dict,
        kpi: Optional[Dict] = None,
    ) -> Dict:
        solutions = []
        if feasible and assignments:
            solutions = [{
                "feasible": True,
                "assignments": assignments,
                "kpi": kpi or {},
            }]
        return {
            "status": "ok",
            "feasible": feasible,
            "metadata": metadata,
            "solutions": solutions,
            "issues": issues,
            "_solver_version": _SOLVER_VERSION,
        }