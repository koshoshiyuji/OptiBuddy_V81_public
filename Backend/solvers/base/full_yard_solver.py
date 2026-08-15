"""
full_yard_solver.py  (v5.2 → v6.1 リファクタリング)

v6.1 変更点:
  - ConstraintApplier を削除し、YardConstraintApplier (solvers/yard/) を使用。
  - 目的関数ブロックを build_objective_expr (solvers/base/objective_terms) に委譲。
    penalty_pairs は DSL の objective.penalty_pairs を参照（インライン生成廃止）。
  - print → logger に統一。
  - registry の cplex_fallback 登録は不要のため、このファイルは
    registry から直接 import されない（solvers/full_yard_solver.py として配置するが
    registry._register_builtin_solvers の cplex_fallback ブロックは削除済み）。
"""

import copy
import logging
from typing import Any, Dict, List

from docplex.cp.model import INTERVAL_MAX, CpoModel

from solvers.yard.constraint_applier import YardConstraintApplier
from solvers.base.objective_terms import build_objective_expr

logger = logging.getLogger(__name__)

# =============================================================
# シフト関連ユーティリティ
# =============================================================

def parse_shifts(config: Dict[str, Any]):
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

    logger.info(f"[SHIFT] {len(shifts)}シフト定義, クレーン={list(crane_windows.keys())}")
    for cid, windows in crane_windows.items():
        logger.debug(f"  {cid}: 稼働={windows}, 休憩={break_windows.get(cid, [])}")

    return crane_windows, break_windows


def apply_shift_constraints(mdl, crane_windows, break_windows, resource_usage):
    SEC = 60
    for crane_id, itvs in resource_usage.items():
        if not itvs:
            continue
        windows = crane_windows.get(crane_id)
        if not windows:
            continue
        for itv in itvs:
            if len(windows) == 1:
                w_start, w_end = windows[0]
                mdl.add(mdl.start_of(itv) >= w_start * SEC)
                mdl.add(mdl.end_of(itv)   <= w_end   * SEC)
            else:
                mdl.add(mdl.logical_or([
                    mdl.logical_and(
                        mdl.start_of(itv) >= w_start * SEC,
                        mdl.end_of(itv)   <= w_end   * SEC
                    )
                    for (w_start, w_end) in windows
                ]))
            for (b_start, b_end) in break_windows.get(crane_id, []):
                mdl.add(mdl.logical_or(
                    mdl.end_of(itv)   <= b_start * SEC,
                    mdl.start_of(itv) >= b_end   * SEC
                ))
        logger.debug(f"[SHIFT] {crane_id}: 稼働={windows}, 休憩={break_windows.get(crane_id, [])}, タスク数={len(itvs)}")


def detect_shift_violations(tasks_result, crane_windows, break_windows, issue_statuses):
    issues = {}
    for t in tasks_result:
        crane_id = t.get("resource") or t.get("resourceId")
        if not crane_id:
            continue
        windows = crane_windows.get(crane_id, [])
        breaks  = break_windows.get(crane_id, [])
        start, end = t.get("start", 0), t.get("end", 0)
        SEC = 60

        # シフト外チェック
        if windows:
            in_any = any(ws * SEC <= start and end <= we * SEC for (ws, we) in windows)
            if not in_any:
                iid = f"shift_out_{t['id']}"
                if issue_statuses.get(iid) != "ACCEPTED":
                    issues[iid] = {
                        "id": iid, "severity": "WARNING", "category": "SHIFT",
                        "title": f"シフト外作業: {crane_id}",
                        "message": f"タスク {t['id']} がシフト時間外({windows})に実行されています。",
                        "containerId": t.get("containerId"), "relatedContainerIds": [t.get("containerId")],
                    }

        # 休憩跨ぎチェック
        for (bs, be) in breaks:
            if start < be * SEC and end > bs * SEC:
                iid = f"break_overlap_{t['id']}"
                if issue_statuses.get(iid) != "ACCEPTED":
                    issues[iid] = {
                        "id": iid, "severity": "WARNING", "category": "SHIFT",
                        "title": f"休憩時間跨ぎ: {crane_id}",
                        "message": f"タスク {t['id']} が休憩時間({bs}-{be}分)と重複しています。",
                        "containerId": t.get("containerId"), "relatedContainerIds": [t.get("containerId")],
                    }
    return issues


# =============================================================
# メインソルバー
# =============================================================

def full_yard_solver(solver_input: Dict[str, Any]) -> Dict[str, Any]:

    # ---------------------------------------------------------
    # 1. 設定読み込み
    # ---------------------------------------------------------
    config = solver_input.get("config", {})

    yard_limits   = {"max_bay": config.get("max_bays"),  "max_row": config.get("max_rows"),  "max_tier": config.get("max_tiers")}
    vessel_limits = {"max_bay": config.get("max_vessel_bays"), "max_row": config.get("max_vessel_rows"), "max_tier": config.get("max_vessel_tiers")}

    op_defaults = {
        "PICK":      config.get("time_pick",      180),
        "MOVE":      config.get("time_move",      120),
        "LOAD":      config.get("time_load",      300),
        "REHANDLE":  config.get("time_rehandle",  240),
        "DISCHARGE": config.get("time_discharge", 300),
        "PLACE":     config.get("time_place",     180),
    }
    safety_gap = config.get("safety_gap", 10)

    default_profiles = [
        {"name": "Plan A", "label": "全作業時間最短", "w_makespan": 1, "w_penalty": 1},
        {"name": "Plan B", "label": "積み順遵守",     "w_makespan": 1, "w_penalty": op_defaults["LOAD"]},
    ]
    solution_profiles = config.get("solution_profiles", default_profiles)

    issue_statuses   = solver_input.get("issue_statuses", {})
    total_time_limit = config.get("time_limit", 20)
    time_per_profile = max(1, total_time_limit // len(solution_profiles))

    crane_interference = config.get("crane_interference", False)
    crane_safety_bays  = config.get("crane_safety_bays", 2)

    crane_windows, break_windows = parse_shifts(config)
    has_shift_config = bool(crane_windows)

    # objective DSL（penalty_pairs を使う）
    objective_dsl = solver_input.get("objective", {})
    penalty_pairs = objective_dsl.get("penalty_pairs", [])

    # ---------------------------------------------------------
    # 2. リソース / コンテナ正規化
    # ---------------------------------------------------------
    resources    = solver_input.get("resources", {})
    yard_cranes  = resources.get("yard_cranes", [])
    ship_cranes  = resources.get("ship_cranes", [])
    restricted   = resources.get("restricted_zones", {})

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
    logger.debug(f"[ATTR] reefer_slots={len(reefer_slot_set)}, imo_zones={len(imo_slot_set)}")

    containers    = solver_input.get("containers", [])
    container_map = {str(c["id"]): c for c in containers}

    current_port = config.get("current_port")
    if not current_port:
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
            logger.warning(f"[SOLVER] {c.get('id')}: current_port={current_port} が POL/POD と不一致。LOADとして処理。")
            c["operation_type"] = "LOAD"

    reefer_ids = {str(c["id"]) for c in containers if "REEFER" in c["attrs"]}
    imo_ids    = {str(c["id"]) for c in containers if any(a.startswith("IMO") for a in c["attrs"])}
    oog_ids    = {str(c["id"]) for c in containers if "OOG"    in c["attrs"]}

    oog_excluded_slots: set = set()
    for c in containers:
        if "OOG" not in c["attrs"]:
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

    all_tasks = solver_input.get("tasks", [])

    # フォールバック: tasksが空の場合のみ最小タスクを生成
    if not all_tasks:
        logger.warning("[SOLVER] tasks が空。フォールバックタスクを生成します。")
        def _rc(bay):
            for cr in yard_cranes:
                if bay in (cr.get("bay") or []):
                    return cr["id"]
            return yard_cranes[0]["id"] if yard_cranes else "RC-1"
        def _gc(bay):
            for cr in ship_cranes:
                if bay in (cr.get("bay") or []):
                    return cr["id"]
            return ship_cranes[0]["id"] if ship_cranes else "GC-1"
        for c in containers:
            cid = c["id"]
            op  = c.get("operation_type", "LOAD")
            yb  = c.get("yard", {}).get("bay", 0)
            sb  = c.get("ship", {}).get("bay", 0)
            if op == "LOAD":
                all_tasks += [
                    {"id": f"P-{cid}", "containerId": cid, "operation": "PICK",      "sequence": 1, "resourceId": _rc(yb)},
                    {"id": f"L-{cid}", "containerId": cid, "operation": "LOAD",      "sequence": 2, "resourceId": _gc(sb)},
                ]
            else:
                all_tasks += [
                    {"id": f"D-{cid}",  "containerId": cid, "operation": "DISCHARGE", "sequence": 1, "resourceId": _gc(sb)},
                    {"id": f"PL-{cid}", "containerId": cid, "operation": "PLACE",     "sequence": 2, "resourceId": _rc(yb)},
                ]

    # DSL制約配列
    dsl_constraints: List[Dict] = solver_input.get("constraints", [])
    use_dsl_constraints = len(dsl_constraints) > 0

    # クレーン干渉ペア（DSLにない場合のフォールバック用）
    def _interference_pairs(crane_list, safety_bays):
        pairs = []
        for i in range(len(crane_list)):
            for j in range(i + 1, len(crane_list)):
                a, b = crane_list[i], crane_list[j]
                ba, bb = a.get("bay", []), b.get("bay", [])
                if ba and bb and min(abs(x - y) for x in ba for y in bb) < safety_bays:
                    pairs.append((a["id"], b["id"]))
        return pairs

    yard_ifc = _interference_pairs(yard_cranes, crane_safety_bays) if crane_interference else []
    ship_ifc = _interference_pairs(ship_cranes, crane_safety_bays) if crane_interference else []

    logger.info(
        f"[SOLVER] {len(containers)} containers, {len(all_tasks)} tasks, "
        f"profiles={[p['name'] for p in solution_profiles]}, time_per_profile={time_per_profile}s, "
        f"dsl_constraints={len(dsl_constraints)}, use_dsl={'YES' if use_dsl_constraints else 'NO(fallback)'}"
    )

    # ---------------------------------------------------------
    # 3. モデル構築
    # ---------------------------------------------------------
    def build_model():
        mdl = CpoModel(name="OptiBuddy_v61")
        mdl.set_parameters({"RandomSeed": 1})
        task_itvs_      = {}
        resource_usage_ = {}

        for t in all_tasks:
            tid  = str(t["id"])
            op   = t.get("operation", "MOVE")
            dur  = t.get("duration") or op_defaults.get(op, op_defaults["MOVE"])
            itv  = mdl.interval_var(size=dur, start=(0, INTERVAL_MAX), name=f"T_{tid}")
            task_itvs_[tid] = itv
            rid = t.get("resourceId") or t.get("resource")
            if rid:
                resource_usage_.setdefault(rid, []).append(itv)

        if use_dsl_constraints:
            # ★ DSL駆動モード: YardConstraintApplier に全制約を委譲
            applier = YardConstraintApplier(
                mdl, task_itvs_, resource_usage_,
                all_tasks, containers,
                crane_windows, break_windows
            )
            n, skipped = applier.apply_all(dsl_constraints)
            logger.info(f"[CONSTRAINT] DSL制約 {n}/{len(dsl_constraints)} 件適用 (skipped={skipped})")

            # クレーン非重複の安全網（DSLに含まれていないリソース用）
            for rid, itvs in resource_usage_.items():
                if len(itvs) > 1:
                    mdl.add(mdl.no_overlap(itvs))

        else:
            # ★ フォールバックモード: ハードコード制約（後方互換）
            logger.warning("[CONSTRAINT] フォールバック: ハードコード制約を使用")

            # A. クレーン非重複
            for rid, itvs in resource_usage_.items():
                if itvs:
                    mdl.add(mdl.no_overlap(itvs))

            # B. コンテナシーケンス
            container_tasks_tmp = {}
            for t in all_tasks:
                container_tasks_tmp.setdefault(str(t["containerId"]), []).append(t)
            for _, t_list in container_tasks_tmp.items():
                sorted_ts = sorted(t_list, key=lambda x: x.get("sequence") or 0)
                for i in range(len(sorted_ts) - 1):
                    mdl.add(mdl.end_before_start(
                        task_itvs_[str(sorted_ts[i]["id"])],
                        task_itvs_[str(sorted_ts[i+1]["id"])], delay=2))

            # B-2. フェーズ分離
            d_tasks = [t for t in all_tasks if t.get("operation") in ("DISCHARGE", "PLACE")]
            l_tasks = [t for t in all_tasks if t.get("operation") in ("LOAD", "MOVE")]
            if d_tasks and l_tasks:
                max_d = mdl.max([mdl.end_of(task_itvs_[str(t["id"])]) for t in d_tasks])
                for t in l_tasks:
                    mdl.add(mdl.start_of(task_itvs_[str(t["id"])]) >= max_d)

            # C. ヤード物理制約
            yard_stacks = {}
            for c in containers:
                y = c.get("yard", {})
                pos = (y.get("bay"), y.get("row"))
                if None not in pos:
                    yard_stacks.setdefault(pos, []).append(c)
            for pos, stack in yard_stacks.items():
                for op_filter, sort_rev, task_op in [
                    ("DISCHARGE", False, "PLACE"),
                    ("LOAD",      True,  "PICK"),
                ]:
                    grp = sorted(
                        [c for c in stack if c.get("operation_type", "LOAD") == op_filter],
                        key=lambda x: x.get("yard", {}).get("tier", 0), reverse=sort_rev
                    )
                    task_ids = []
                    for c in grp:
                        cid = str(c["id"])
                        t = next((t["id"] for t in all_tasks if str(t["containerId"]) == cid and t["operation"] == task_op), None)
                        if t:
                            task_ids.append(t)
                    for i in range(len(task_ids) - 1):
                        if task_ids[i] in task_itvs_ and task_ids[i+1] in task_itvs_:
                            mdl.add(mdl.end_before_start(task_itvs_[task_ids[i]], task_itvs_[task_ids[i+1]], delay=safety_gap))

            # D. 船側物理制約
            vessel_stacks = {}
            for c in containers:
                v = c.get("ship", {})
                pos = (v.get("bay"), v.get("row"))
                if None not in pos:
                    vessel_stacks.setdefault(pos, []).append(c)
            for pos, stack in vessel_stacks.items():
                for op_filter, sort_rev, task_op in [
                    ("DISCHARGE", True,  "DISCHARGE"),
                    ("LOAD",      False, "LOAD"),
                ]:
                    grp = sorted(
                        [c for c in stack if c.get("operation_type", "LOAD") == op_filter],
                        key=lambda x: x.get("ship", {}).get("tier", 0), reverse=sort_rev
                    )
                    task_ids = []
                    for c in grp:
                        cid = str(c["id"])
                        t = next((t["id"] for t in all_tasks if str(t["containerId"]) == cid and t["operation"] == task_op), None)
                        if t:
                            task_ids.append(t)
                    for i in range(len(task_ids) - 1):
                        if task_ids[i] in task_itvs_ and task_ids[i+1] in task_itvs_:
                            mdl.add(mdl.end_before_start(task_itvs_[task_ids[i]], task_itvs_[task_ids[i+1]], delay=1))

            # E. クレーン干渉
            for (rid_a, rid_b) in yard_ifc + ship_ifc:
                itvs_a = resource_usage_.get(rid_a, [])
                itvs_b = resource_usage_.get(rid_b, [])
                if itvs_a and itvs_b:
                    mdl.add(mdl.no_overlap(itvs_a + itvs_b))

            # F. シフト
            if has_shift_config:
                apply_shift_constraints(mdl, crane_windows, break_windows, resource_usage_)

        # G. OOG制約（DSL/フォールバック共通）
        if oog_excluded_slots and oog_ids:
            oog_itvs = [
                task_itvs_[str(t["id"])]
                for t in all_tasks
                if str(t.get("containerId")) in oog_ids and t.get("operation") in ("PICK", "MOVE")
                and str(t["id"]) in task_itvs_
            ]
            if oog_itvs:
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
                        if str(t["id"]) in task_itvs_:
                            mdl.add(mdl.start_of(task_itvs_[str(t["id"])]) >= oog_last)

        container_tasks_ = {}
        for t in all_tasks:
            container_tasks_.setdefault(str(t["containerId"]), []).append(t)

        return mdl, task_itvs_, container_tasks_

    # ---------------------------------------------------------
    # 4. penalty 計算用ペア（結果評価用）
    # ---------------------------------------------------------
    def _build_order_pairs(op: str):
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

    load_order_pairs      = _build_order_pairs("LOAD")
    discharge_order_pairs = _build_order_pairs("DISCHARGE")

    def calc_penalty(tasks, load_pairs, discharge_pairs):
        ends = {t["id"]: t["end"] for t in tasks}
        lv = sum(1 for (a, b) in load_pairs      if ends.get(a, 0) > ends.get(b, 0))
        dv = sum(1 for (a, b) in discharge_pairs if ends.get(a, 0) > ends.get(b, 0))
        return lv + dv

    # ---------------------------------------------------------
    # 5. プロファイル別ソルブ
    # ---------------------------------------------------------
    all_solutions_data = []

    for profile in solution_profiles:
        p_name     = profile.get("name", f"Plan {chr(65 + len(all_solutions_data))}")
        p_label    = profile.get("label", "")
        w_makespan = profile.get("w_makespan", 1)
        w_penalty  = profile.get("w_penalty", op_defaults["LOAD"])

        logger.info(f"[Profile] {p_name} [{p_label}] w_makespan={w_makespan}, w_penalty={w_penalty}")

        mdl, task_itvs, container_tasks = build_model()

        # ★ 目的関数: build_objective_expr に委譲（penalty_pairs は DSL から取得）
        obj_context = {
            "task_itvs":     task_itvs,
            "penalty_pairs": penalty_pairs,
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
        penalty = calc_penalty(sol_tasks, load_order_pairs, discharge_order_pairs)
        logger.info(f"[Profile] {p_name}: makespan={mspan}, penalty={penalty}")

        all_solutions_data.append({
            "name": p_name, "label": p_label,
            "tasks": sol_tasks, "makespan": mspan, "penalty": penalty,
        })

    logger.info(f"[SOLVER] 合計{len(all_solutions_data)}解")

    # ---------------------------------------------------------
    # 6. イシュー検知
    # ---------------------------------------------------------
    issue_groups = {}

    for cid_a, c_a in container_map.items():
        for cid_b, c_b in container_map.items():
            if cid_a >= cid_b:
                continue
            o_a, o_b = c_a.get("order"), c_b.get("order")
            y_a, y_b = c_a.get("yard", {}), c_b.get("yard", {})
            v_a, v_b = c_a.get("ship", {}), c_b.get("ship", {})
            w_a, w_b = c_a.get("weight", 0), c_b.get("weight", 0)
            pair_id  = f"{cid_a}_{cid_b}"

            # IS_YARD
            if (y_a.get("bay") == y_b.get("bay") and y_a.get("row") == y_b.get("row")
                    and y_a.get("bay") not in (None, 0)):
                upper_id, lower_id, o_u, o_l, w_u, w_l = (
                    (cid_b, cid_a, o_b, o_a, w_b, w_a) if y_b.get("tier", 0) > y_a.get("tier", 0)
                    else (cid_a, cid_b, o_a, o_b, w_a, w_b)
                )
                if o_u is not None and o_l is not None and o_u > o_l:
                    iid = f"is_yard_{lower_id}_{upper_id}"
                    if issue_statuses.get(iid) != "ACCEPTED":
                        issue_groups[iid] = {
                            "id": iid, "severity": "WARNING", "category": "YARD",
                            "title": "リハンドリングが発生します",
                            "message": f"上段({upper_id})が邪魔で先に積むべき下段({lower_id})を取り出せません。",
                            "containerId": lower_id, "relatedContainerIds": [lower_id, upper_id],
                        }
                if w_u > w_l:
                    iid = f"weight-yard-{pair_id}"
                    if issue_statuses.get(iid) != "ACCEPTED":
                        issue_groups[iid] = {
                            "id": iid, "severity": "WARNING", "category": "WEIGHT",
                            "title": f"Weight Warning: {upper_id}({w_u}t) が {lower_id}({w_l}t) の上",
                            "message": f"重量の重い {upper_id}({w_u}t) が軽い {lower_id}({w_l}t) の上に積まれています。",
                            "containerId": upper_id, "relatedContainerIds": [upper_id, lower_id],
                        }

            # IS_SHIP
            if (v_a.get("bay") == v_b.get("bay") and v_a.get("row") == v_b.get("row")
                    and v_a.get("bay") not in (None, 0)):
                upper_id, lower_id, o_u, o_l, w_u, w_l = (
                    (cid_b, cid_a, o_b, o_a, w_b, w_a) if v_b.get("tier", 0) > v_a.get("tier", 0)
                    else (cid_a, cid_b, o_a, o_b, w_a, w_b)
                )
                if o_u is not None and o_l is not None and o_u < o_l:
                    iid = f"is_ship_{lower_id}_{upper_id}"
                    if issue_statuses.get(iid) != "ACCEPTED":
                        issue_groups[iid] = {
                            "id": iid, "severity": "CRITICAL", "category": "MBP",
                            "title": "Master Bay Plan 積み順矛盾",
                            "message": "Master Bay Planに積み順矛盾があります。船社への確認が必要です。",
                            "containerId": lower_id, "relatedContainerIds": [lower_id, upper_id],
                        }
                if w_u > w_l:
                    iid = f"weight-ship-{pair_id}"
                    if issue_statuses.get(iid) != "ACCEPTED":
                        issue_groups[iid] = {
                            "id": iid, "severity": "WARNING", "category": "WEIGHT",
                            "title": f"Weight Warning: {upper_id}({w_u}t) が {lower_id}({w_l}t) の上（船側）",
                            "message": f"重量の重い {upper_id}({w_u}t) が軽い {lower_id}({w_l}t) の上に積まれています。",
                            "containerId": upper_id, "relatedContainerIds": [upper_id, lower_id],
                        }

    # ATTR イシュー
    for c in containers:
        cid = str(c["id"])
        y   = c.get("yard", {})
        bay, row, tier = y.get("bay"), y.get("row"), y.get("tier")
        if bay is None or row is None:
            continue
        if cid in reefer_ids and not ((bay, row, tier) in reefer_slot_set or (bay, row, None) in reefer_slot_set):
            iid = f"attr_reefer_{cid}"
            if issue_statuses.get(iid) != "ACCEPTED":
                issue_groups[iid] = {
                    "id": iid, "severity": "CRITICAL", "category": "ATTR",
                    "title": f"Reefer配置違反: {cid}",
                    "message": f"{cid} がReeferスロット以外(bay={bay},row={row},tier={tier})に配置されています。",
                    "containerId": cid, "relatedContainerIds": [cid],
                }
        if cid in imo_ids and (bay, row) not in imo_slot_set:
            iid = f"attr_imo_{cid}"
            if issue_statuses.get(iid) != "ACCEPTED":
                issue_groups[iid] = {
                    "id": iid, "severity": "CRITICAL", "category": "ATTR",
                    "title": f"IMO危険物配置違反: {cid}",
                    "message": f"{cid} がIMO指定区画外(bay={bay},row={row})に配置されています。",
                    "containerId": cid, "relatedContainerIds": [cid],
                }
        if cid not in oog_ids and ((bay, row, tier) in oog_excluded_slots or (bay, row, None) in oog_excluded_slots):
            oog_src = [
                str(oc["id"]) for oc in containers
                if "OOG" in oc["attrs"] and (
                    (oc.get("yard", {}).get("bay") == bay and
                     isinstance(oc.get("yard", {}).get("row"), str) and
                     abs(ord(oc["yard"]["row"]) - ord(str(row))) == 1) or
                    (oc.get("yard", {}).get("bay") == bay and
                     oc.get("yard", {}).get("row") == row and
                     oc.get("yard", {}).get("tier") is not None and
                     abs(oc["yard"]["tier"] - (tier or 0)) == 1)
                )
            ]
            iid = f"attr_oog_{cid}"
            if issue_statuses.get(iid) != "ACCEPTED":
                issue_groups[iid] = {
                    "id": iid, "severity": "WARNING", "category": "ATTR",
                    "title": f"OOG隣接スロット: {cid}",
                    "message": f"{cid} がOOGコンテナ({', '.join(oog_src)})の隣接スロット(bay={bay},row={row},tier={tier})に配置されています。",
                    "containerId": cid, "relatedContainerIds": [cid] + oog_src,
                }

    if has_shift_config and all_solutions_data:
        issue_groups.update(detect_shift_violations(
            all_solutions_data[0]["tasks"], crane_windows, break_windows, issue_statuses))

    if all_solutions_data:
        d_res = [t for t in all_solutions_data[0]["tasks"] if t.get("operation") == "DISCHARGE"]
        l_res = [t for t in all_solutions_data[0]["tasks"] if t.get("operation") == "LOAD"]
        if d_res and l_res:
            max_d_end   = max(t["end"]   for t in d_res)
            min_l_start = min(t["start"] for t in l_res)
            if min_l_start < max_d_end:
                for lt in [t for t in l_res if t["start"] < max_d_end]:
                    iid = f"discharge_before_load_{lt['containerId']}"
                    if issue_statuses.get(iid) != "ACCEPTED":
                        issue_groups[iid] = {
                            "id": iid, "severity": "CRITICAL", "category": "OPERATION",
                            "title": f"DISCHARGE完了前のLOAD開始: {lt['containerId']}",
                            "message": f"{lt['containerId']} のLOADがDISCHARGE完了前({max_d_end}秒)に開始({lt['start']}秒)。",
                            "containerId": lt["containerId"], "relatedContainerIds": [lt["containerId"]],
                        }

    # ---------------------------------------------------------
    # 7. 結果返却
    # ---------------------------------------------------------
    return {
        "status":        "ok" if all_solutions_data else "error",
        "solutions":     all_solutions_data,
        "tasks":         all_solutions_data[0]["tasks"] if all_solutions_data else [],
        "makespan":      all_solutions_data[0]["makespan"] if all_solutions_data else 0,
        "issues":        list(issue_groups.values()),
        "yard_limits":   yard_limits,
        "vessel_limits": vessel_limits,
        "containers":    containers,
    }
