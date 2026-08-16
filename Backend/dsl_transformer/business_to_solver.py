"""
Business DSL → Solver Input DSL 変換モジュール  (v6.1)

変更点 (v6.0 → v6.1):
  - CapacitatedVehicleRoutingProblem を problem_class で振り分け追加。
    cvrp_converter.convert_cvrp_to_solver() に委譲する。
  - 他の新規4DSLドメインも同じパターンで追加可能。
"""

import copy
import hashlib
import json
from datetime import datetime
from typing import Any, Callable, Dict, List, Optional, Tuple

# =============================================================
# エントリポイント
# =============================================================

def convert_business_to_solver(business_dsl: Dict[str, Any]) -> Dict[str, Any]:
    """業務DSL → Solver Input DSL 変換"""

    problem_class = business_dsl.get("problem_class", "")

    # ── 4DSL準拠ドメイン：problem_class で振り分け ──
    # 2026-07-11: EventStaffingは削除済み（StoreSiteはこの時点では未登録。2026-07-20に
    # 新規MIPドメインとして登録され、下記に振り分け先がある）。CapacitatedVehicleRoutingProblem（旧実装）は
    # TruckDispatcherに置き換え済みで、TruckDispatcher/NurseShiftはどちらも
    # dsl_transformer/{snake}_converter.py の規約ベース自動検出（app.py の _solve_4dsl_generic）
    # 経由で処理されるため、ここへの明示登録は不要。

    if problem_class == "LineChangeoverScheduler":
            from .line_changeover_scheduler_converter import convert_line_changeover_scheduler_to_solver
            return convert_line_changeover_scheduler_to_solver(business_dsl)


    if problem_class == "MeetingRoom":
            from .meeting_room_converter import convert_meeting_room_to_solver
            return convert_meeting_room_to_solver(business_dsl)


    if problem_class == "MeetingRoom2":
            from .meeting_room2_converter import convert_meeting_room2_to_solver
            return convert_meeting_room2_to_solver(business_dsl)


    if problem_class == "StoreSite":
            from .store_site_converter import convert_store_site_to_solver
            return convert_store_site_to_solver(business_dsl)


    if problem_class == "CarSequencing":
            from .car_sequencing_converter import convert_car_sequencing_to_solver
            return convert_car_sequencing_to_solver(business_dsl)


    if problem_class == "NursingWorkloadBalance":
            from .nursing_workload_balance_converter import convert_nursing_workload_balance_to_solver
            return convert_nursing_workload_balance_to_solver(business_dsl)


    if problem_class == "CapitalProjectSelector":
            from .capital_project_selector_converter import convert_capital_project_selector_to_solver
            return convert_capital_project_selector_to_solver(business_dsl)


    if problem_class == "CrewDutyScheduler":
            from .crew_duty_scheduler_converter import convert_crew_duty_scheduler_to_solver
            return convert_crew_duty_scheduler_to_solver(business_dsl)


    if problem_class == "ExampleDelivery":
            from .example_delivery_converter import convert_example_delivery_to_solver
            return convert_example_delivery_to_solver(business_dsl)


    if problem_class == "InventoryReplenishmentPlanner":
            from .inventory_replenishment_planner_converter import convert_inventory_replenishment_planner_to_solver
            return convert_inventory_replenishment_planner_to_solver(business_dsl)


    if problem_class == "AuctionWinnerSelector":
            from .auction_winner_selector_converter import convert_auction_winner_selector_to_solver
            return convert_auction_winner_selector_to_solver(business_dsl)


    if problem_class == "PortfolioOverlapDesigner":
            from .portfolio_overlap_designer_converter import convert_portfolio_overlap_designer_to_solver
            return convert_portfolio_overlap_designer_to_solver(business_dsl)


    if problem_class == "TransportCostMinimizer":
            from .transport_cost_minimizer_converter import convert_transport_cost_minimizer_to_solver
            return convert_transport_cost_minimizer_to_solver(business_dsl)


    if problem_class == "VesselDeckLoader":
            from .vessel_deck_loader_converter import convert_vessel_deck_loader_to_solver
            return convert_vessel_deck_loader_to_solver(business_dsl)


    if problem_class == "DepotRoutePlanner":
            from .depot_route_planner_converter import convert_depot_route_planner_to_solver
            return convert_depot_route_planner_to_solver(business_dsl)


    if problem_class == "ShiftRotationScheduler":
            from .shift_rotation_scheduler_converter import convert_shift_rotation_scheduler_to_solver
            return convert_shift_rotation_scheduler_to_solver(business_dsl)


    if problem_class == "LotSizingScheduler":
            from .lot_sizing_scheduler_converter import convert_lot_sizing_scheduler_to_solver
            return convert_lot_sizing_scheduler_to_solver(business_dsl)







    if problem_class == "PatientTransportPlanner":
            from .patient_transport_planner_converter import convert_patient_transport_planner_to_solver
            return convert_patient_transport_planner_to_solver(business_dsl)




    if problem_class == "MedicalAppointmentScheduler":
                from .medical_appointment_scheduler_converter import convert_medical_appointment_scheduler_to_solver
                return convert_medical_appointment_scheduler_to_solver(business_dsl)


    if problem_class == "MedicalAppointmentSequenceScheduler":
            from .medical_appointment_sequence_scheduler_converter import convert_medical_appointment_sequence_scheduler_to_solver
            return convert_medical_appointment_sequence_scheduler_to_solver(business_dsl)


    if problem_class == "MysteryShopperScheduler":
            from .mystery_shopper_scheduler_converter import convert_mystery_shopper_scheduler_to_solver
            return convert_mystery_shopper_scheduler_to_solver(business_dsl)


    if problem_class == "EnergyCostAwareScheduler":
            from .energy_cost_aware_scheduler_converter import convert_energy_cost_aware_scheduler_to_solver
            return convert_energy_cost_aware_scheduler_to_solver(business_dsl)


    if problem_class == "TankAllocationPlanner":
            from .tank_allocation_planner_converter import convert_tank_allocation_planner_to_solver
            return convert_tank_allocation_planner_to_solver(business_dsl)



    if problem_class == "ProductionLineSequencing":
            from .production_line_sequencing_converter import convert_production_line_sequencing_to_solver
            return convert_production_line_sequencing_to_solver(business_dsl)


    if problem_class == "RideshareMatchingPlanner":
            from .rideshare_matching_planner_converter import convert_rideshare_matching_planner_to_solver
            return convert_rideshare_matching_planner_to_solver(business_dsl)

# ── 新規4DSLドメインはここに追加 ──
    # if problem_class == "NextDomain":
    #     from .next_domain_converter import convert_next_domain_to_solver
    #     return convert_next_domain_to_solver(business_dsl)

    # ── YardPlanning（既存デフォルト） ──
    resources = business_dsl.get("resources", {})

    solver_input: Dict[str, Any] = {
        "metadata": {
            "problem_class": business_dsl.get("problem_class", "RCPSP"),
            "version":       business_dsl.get("version", "1.0"),
            "extensions":    business_dsl.get("extensions", []),
            "source_dsl_hash": _compute_hash(business_dsl),
            "generated_at":    datetime.utcnow().isoformat() + "Z",
        },
        "tasks":       [],
        "resources":   resources,
        "constraints": [],
        "objective":   {},
        "containers":  [],
        "config":      business_dsl.get("config", {}),
        "unassignable_containers": [],  # v6.0: クレーン未到達コンテナ
    }

    containers = business_dsl.get("containers", [])
    config     = business_dsl.get("config", {})
    extensions = business_dsl.get("extensions", [])

    solver_input["containers"] = containers

    # 1. タスク生成（REHANDLE 含む）
    tasks, unassignable = _generate_tasks_with_rehandle(business_dsl)
    solver_input["tasks"] = tasks
    solver_input["unassignable_containers"] = unassignable

    # 2. 制約生成
    solver_input["constraints"] = _generate_constraints(
        containers, solver_input["tasks"], extensions, resources, config
    )

    # 3. 目的関数
    solver_input["objective"] = _build_objective(
        containers, solver_input["tasks"], config
    )

    return solver_input


# =============================================================
# タスク生成 — REHANDLE 含む完全版
# =============================================================

class _CraneScheduler:
    def __init__(self, yard_cranes: List[Dict], ship_cranes: List[Dict], safety_gap: int):
        self._available: Dict[str, int] = {}
        self._gap = safety_gap
        for c in yard_cranes + ship_cranes:
            self._available[c["id"]] = 0

    def next_start(self, crane_id: str, not_before: int = 0) -> int:
        return max(self._available.get(crane_id, 0), not_before) + self._gap

    def book(self, crane_id: str, start: int, duration: int) -> int:
        end = start + duration
        self._available[crane_id] = end
        return end


def _build_crane_lookup(cranes: List[Dict]) -> Callable[[int], Optional[str]]:
    bay_map: Dict[int, str] = {}
    for crane in cranes:
        for bay in crane.get("bay", []):
            bay_map[bay] = crane["id"]
    def lookup(bay: int) -> Optional[str]:
        return bay_map.get(bay)
    return lookup


def _make_task(
    task_id: str, container_id: str, operation: str, duration: int,
    resource_id: str, sequence: int, start: int, end: int,
    from_pos: Optional[Dict] = None, to_pos: Optional[Dict] = None,
    extra: Optional[Dict] = None,
) -> Dict[str, Any]:
    task: Dict[str, Any] = {
        "id": task_id, "containerId": container_id, "operation": operation,
        "duration": duration, "resourceId": resource_id, "resource": resource_id,
        "sequence": sequence, "start": start, "end": end,
    }
    if from_pos is not None: task["from"] = from_pos
    if to_pos is not None:   task["to"]   = to_pos
    if extra:                task.update(extra)
    return task


def _generate_tasks_with_rehandle(dsl: Dict[str, Any]) -> Tuple[List[Dict], List[Dict]]:
    config    = dsl.get("config", {})
    resources = dsl.get("resources", {})

    durations = {
        "PICK":      config.get("time_pick",      180),
        "MOVE":      config.get("time_move",      120),
        "LOAD":      config.get("time_load",      300),
        "DISCHARGE": config.get("time_discharge", 300),
        "PLACE":     config.get("time_place",     180),
        "REHANDLE":  config.get("time_rehandle",  240),
    }
    safety_gap = config.get("safety_gap", 10)
    max_rows   = config.get("max_rows",   6)
    max_tiers  = config.get("max_tiers",  5)
    w_bay      = config.get("weight_bay_move",   100)
    w_row      = config.get("weight_row_move",    10)
    w_block    = config.get("weight_block_risk", 200)

    yard_cranes = resources.get("yard_cranes", [])
    ship_cranes = resources.get("ship_cranes", [])
    get_rc = _build_crane_lookup(yard_cranes)
    get_gc = _build_crane_lookup(ship_cranes)

    shift_windows: Dict[str, List[Tuple[int, int]]] = {}
    # crane_id → config.shifts のインデックス。LLM(relax_interface.py)が
    # 緩和案の dsl_patch で config.shifts[i].end を編集する際、reason の自然文から
    # クレーン名を読み取ってインデックスを推測する必要がないよう、診断情報
    # (diagnostic.details)に shift_index として直接含める（2026-07-14）。
    crane_to_shift_index: Dict[str, int] = {}
    for idx, s in enumerate(config.get("shifts", [])):
        s_start = s.get("start", 0) * 60
        s_end   = s.get("end", 0)   * 60
        for cid in s.get("cranes", []):
            shift_windows.setdefault(cid, []).append((s_start, s_end))
            crane_to_shift_index[cid] = idx

    scheduler    = _CraneScheduler(yard_cranes, ship_cranes, safety_gap)
    current_port = config.get("current_port", "")

    states: Dict[str, Dict] = {}
    for c in dsl.get("containers", []):
        cid = str(c.get("id") or c.get("containerId"))
        pol = c.get("pol", "")
        pod = c.get("pod", "")
        if current_port and current_port == pol:
            op_type = "LOAD"
        elif current_port and current_port == pod:
            op_type = "DISCHARGE"
        else:
            op_type = c.get("operation_type", "LOAD")
        states[cid] = {
            "b": int(c["yard"]["bay"]),  "r": int(c["yard"]["row"]),  "t": int(c["yard"]["tier"]),
            "sb": int(c["ship"]["bay"]), "sr": int(c["ship"]["row"]), "st": int(c["ship"]["tier"]),
            "weight": c.get("weight", 0), "pol": c.get("pol", "---"), "pod": c.get("pod", "---"),
            "order": c.get("order", 999), "operation_type": op_type, "attrs": c.get("attrs", []),
        }

    sorted_ids = sorted(
        states.keys(),
        key=lambda k: (0 if states[k]["operation_type"] == "DISCHARGE" else 1, states[k]["order"])
    )

    tasks: List[Dict] = []
    unassignable: List[Dict] = []
    seq_counter: Dict[str, int] = {}

    def next_seq(cid: str) -> int:
        seq_counter[cid] = seq_counter.get(cid, 0) + 1
        return seq_counter[cid]

    def common_extra(state: Dict) -> Dict:
        return {"weight": state["weight"], "ports": {"pol": state["pol"], "pod": state["pod"]}, "attrs": state.get("attrs", [])}

    def _crane_unreachable_error(resource_type: str, target_bay: int, cranes: List[Dict]) -> Dict[str, Any]:
        """
        クレーンの担当bay一覧が target_bay をカバーしていない場合の構造化エラー。
        2026-07-14: 以前は「担当ベイ: [[1, 2, 3]]」のような一覧を reason の自然文に
        埋め込むだけで、LLM(relax_interface.py)がどのクレーンのbay配列を編集すべきか
        テキストから推測する必要があった。candidate_resources に {id, bay} の構造化
        一覧を持たせることで、目視パース不要でどのクレーンを拡張すべきか判定できる。
        """
        label     = "ヤードクレーン" if resource_type == "yard_crane" else "シップクレーン"
        bay_label = "bay" if resource_type == "yard_crane" else "ship bay"
        candidate_resources = [{"id": c["id"], "bay": c.get("bay", [])} for c in cranes]
        reason = f"{label}が {bay_label} {target_bay} に到達できません。担当ベイ: {[c.get('bay', []) for c in cranes]}"
        return {
            "reason": reason,
            "resource_type": resource_type,
            "target_bay": target_bay,
            "candidate_resources": candidate_resources,
        }

    def _shift_violation_error(crane_id: str, start: int, end: int, windows: List[Tuple[int, int]]) -> Dict[str, Any]:
        """
        シフト稼働時間外にタスクが収まってしまう場合の構造化エラー。
        2026-07-14: crane_id / shift_index を直接持たせることで、LLMが reason の
        自然文（例:「RC-1 のシフト稼働時間...」）からクレーン名を読み取って
        config.shifts配列のインデックスを逆引きする必要がなくなる。
        """
        windows_str = ", ".join(f"{ws//60}〜{we//60}分" for ws, we in windows)
        reason = f"{crane_id} のシフト稼働時間（{windows_str}）内にタスク（{start//60}〜{end//60}分）が収まりません"
        return {
            "reason": reason,
            "crane_id": crane_id,
            "shift_index": crane_to_shift_index.get(crane_id),
            "task_start_min": start // 60,
            "task_end_min": end // 60,
        }

    def _check_shift(crane_id: str, start: int, end: int) -> Optional[Dict[str, Any]]:
        windows = shift_windows.get(crane_id)
        if not windows:
            return None
        in_any = any(ws <= start and end <= we for ws, we in windows)
        if not in_any:
            return _shift_violation_error(crane_id, start, end, windows)
        return None

    def _build_load_tasks(cid: str, s: Dict, ready: int) -> Tuple[Optional[List[Dict]], Optional[Dict[str, Any]]]:
        rc = get_rc(s["b"])
        gc = get_gc(s["sb"])
        if rc is None:
            return None, _crane_unreachable_error("yard_crane", s["b"], yard_cranes)
        if gc is None:
            return None, _crane_unreachable_error("ship_crane", s["sb"], ship_cranes)
        f = {"domain": "YARD",   "bay": s["b"],  "row": s["r"],  "tier": s["t"]}
        t = {"domain": "VESSEL", "bay": s["sb"], "row": s["sr"], "tier": s["st"]}
        ms = scheduler.next_start(rc, ready)
        me = scheduler.book(rc, ms, durations["MOVE"])
        ls = scheduler.next_start(gc, me)
        le = scheduler.book(gc, ls, durations["LOAD"])
        shift_err = _check_shift(rc, ms, me) or _check_shift(gc, ls, le)
        if shift_err:
            return None, shift_err
        added = [
            _make_task(f"{cid}_MOVE_{ms}", cid, "MOVE", durations["MOVE"], rc, next_seq(cid), ms, me,
                       copy.deepcopy(f), copy.deepcopy(f), {**common_extra(s), "yard": copy.deepcopy(f)}),
            _make_task(f"{cid}_LOAD_{ls}", cid, "LOAD", durations["LOAD"], gc, next_seq(cid), ls, le,
                       copy.deepcopy(f), copy.deepcopy(t),
                       {**common_extra(s), "ship": copy.deepcopy(t),
                        "yard": {"bay": s["b"], "row": s["r"], "tier": s["t"]}, "order": s.get("order", 0)}),
        ]
        s["b"] = s["r"] = s["t"] = 0
        return added, None

    def _build_discharge_tasks(cid: str, s: Dict, ready: int) -> Tuple[Optional[List[Dict]], Optional[Dict[str, Any]]]:
        rc = get_rc(s["b"])
        gc = get_gc(s["sb"])
        if rc is None:
            return None, _crane_unreachable_error("yard_crane", s["b"], yard_cranes)
        if gc is None:
            return None, _crane_unreachable_error("ship_crane", s["sb"], ship_cranes)
        f = {"domain": "VESSEL", "bay": s["sb"], "row": s["sr"], "tier": s["st"]}
        t = {"domain": "YARD",   "bay": s["b"],  "row": s["r"],  "tier": s["t"]}
        ds = scheduler.next_start(gc, ready)
        de = scheduler.book(gc, ds, durations["DISCHARGE"])
        ps = scheduler.next_start(rc, de)
        pe = scheduler.book(rc, ps, durations["PLACE"])
        shift_err = _check_shift(gc, ds, de) or _check_shift(rc, ps, pe)
        if shift_err:
            return None, shift_err
        added = [
            _make_task(f"{cid}_DISCHARGE_{ds}", cid, "DISCHARGE", durations["DISCHARGE"], gc, next_seq(cid), ds, de,
                       copy.deepcopy(f), copy.deepcopy(f),
                       {**common_extra(s), "ship": copy.deepcopy(f),
                        "yard": {"bay": s["b"], "row": s["r"], "tier": s["t"]}, "order": s.get("order", 0)}),
            _make_task(f"{cid}_PLACE_{ps}", cid, "PLACE", durations["PLACE"], rc, next_seq(cid), ps, pe,
                       copy.deepcopy(f), copy.deepcopy(t), {**common_extra(s), "yard": copy.deepcopy(t)}),
        ]
        s["sb"] = s["sr"] = s["st"] = 0
        return added, None

    _OPERATION_BUILDERS: Dict[str, Callable] = {
        "LOAD":      _build_load_tasks,
        "DISCHARGE": _build_discharge_tasks,
    }

    for cid in sorted_ids:
        target = states[cid]
        blockers = sorted(
            [oid for oid in states if oid != cid
             and states[oid]["b"] == target["b"] and states[oid]["r"] == target["r"]
             and states[oid]["t"] > target["t"]],
            key=lambda b: states[b]["t"], reverse=True
        )
        container_ready = 0
        skip_cid = False

        for b_id in blockers:
            bs = states[b_id]
            rc = get_rc(bs["b"])
            if rc is None:
                err = _crane_unreachable_error("yard_crane", bs["b"], yard_cranes)
                unassignable.append({"container_id": b_id,
                    "yard_bay": bs["b"], "ship_bay": bs.get("sb"), **err})
                skip_cid = True; break
            f = {"domain": "YARD", "bay": bs["b"], "row": bs["r"], "tier": bs["t"]}
            candidates = []
            for bay in [bs["b"], bs["b"] - 1, bs["b"] + 1]:
                if bay < 1: continue
                for row in range(1, max_rows + 1):
                    stack = [sv for sv in states.values() if sv["b"] == bay and sv["r"] == row]
                    cur_max = max((sv["t"] for sv in stack), default=0)
                    if cur_max < max_tiers:
                        tgt_tier = cur_max + 1
                        score = (abs(bay - bs["b"]) * w_bay + abs(row - bs["r"]) * w_row + tgt_tier * 5
                                 + (w_block if any(sv.get("order", 0) < bs["order"] for sv in stack) else 0))
                        candidates.append({"bay": bay, "row": row, "tier": tgt_tier, "score": score})
            if candidates:
                best = min(candidates, key=lambda x: x["score"])
                tp = {"domain": "YARD", "bay": best["bay"], "row": best["row"], "tier": best["tier"]}
            else:
                tp = {"domain": "YARD", "bay": bs["b"], "row": (bs["r"] % max_rows) + 1, "tier": 1}
            rs = scheduler.next_start(rc, container_ready)
            re_end = scheduler.book(rc, rs, durations["REHANDLE"])
            shift_err = _check_shift(rc, rs, re_end)
            if shift_err:
                # 2026-07-14: 以前は "reason": shift_err として tuple をそのまま
                # 格納してしまっており（reasonが文字列でなくtupleになる不具合）、
                # _shift_violation_error() の構造化dictを展開する形に修正。
                unassignable.append({"container_id": b_id,
                    "yard_bay": bs["b"], "ship_bay": bs.get("sb"), **shift_err})
                skip_cid = True; break
            container_ready = re_end
            tasks.append(_make_task(f"{b_id}_REHL_{rs}", b_id, "REHANDLE", durations["REHANDLE"], rc,
                next_seq(b_id), rs, re_end, copy.deepcopy(f), copy.deepcopy(tp),
                {**common_extra(bs), "yard": copy.deepcopy(tp)}))
            bs["b"], bs["r"], bs["t"] = tp["bay"], tp["row"], tp["tier"]

        if skip_cid:
            unassignable.append({"container_id": cid,
                "reason": f"ブロッカー（{[b for b in blockers]}）の処理に失敗したためスキップ",
                "yard_bay": target["b"], "ship_bay": target.get("sb")}); continue

        op_type = target.get("operation_type", "LOAD")
        builder = _OPERATION_BUILDERS.get(op_type)
        if builder:
            added, err = builder(cid, target, container_ready)
            if err:
                # 2026-07-14: err は _crane_unreachable_error() /
                # _shift_violation_error() が返す構造化dict（reason/crane_id/
                # shift_index/resource_type/candidate_resources等）に統一。
                # 以前あった str と tuple の isinstance分岐は不要になった。
                unassignable.append({"container_id": cid,
                    "yard_bay": target["b"], "ship_bay": target.get("sb"), **err})
            else:
                tasks.extend(added)
        else:
            print(f"[WARNING] 未知の operation_type: {op_type} (container={cid})")

    return tasks, unassignable


# =============================================================
# ユーティリティ
# =============================================================

def _compute_hash(data: Dict[str, Any]) -> str:
    return "sha256:" + hashlib.sha256(json.dumps(data, sort_keys=True).encode()).hexdigest()[:16]


# =============================================================
# 制約生成
# =============================================================

def _generate_constraints(containers, tasks, extensions, resources, config):
    constraints = []
    constraints.extend(_build_precedence_constraints(tasks, config))
    constraints.extend(_build_no_overlap_constraints(tasks))
    if "physical_space"    in extensions: constraints.extend(_build_physical_space_constraints(containers, tasks, config))
    if "phase_separation"  in extensions: constraints.extend(_build_phase_separation_constraints(tasks))
    if "crane_interference" in extensions and config.get("crane_interference"):
        constraints.extend(_build_crane_interference_constraints(tasks, resources, config))
    if "shift" in extensions and config.get("shifts"):
        constraints.extend(_build_shift_constraints(tasks, config))
    if "attribute_zones"   in extensions: constraints.extend(_build_attribute_zone_constraints(containers, resources))
    return constraints

def _build_precedence_constraints(tasks, config):
    safety_gap = config.get("safety_gap", 2)
    container_tasks: Dict[str, List] = {}
    for task in tasks:
        container_tasks.setdefault(str(task["containerId"]), []).append(task)
    constraints = []
    for _, ctasks in container_tasks.items():
        ctasks.sort(key=lambda t: t.get("sequence", 0))
        for i in range(len(ctasks) - 1):
            constraints.append({"type": "precedence", "params": {
                "from_task": ctasks[i]["id"], "to_task": ctasks[i+1]["id"], "delay": safety_gap}})
    return constraints

def _build_no_overlap_constraints(tasks):
    resource_tasks: Dict[str, List] = {}
    for task in tasks:
        rid = task.get("resourceId", "")
        if rid: resource_tasks.setdefault(rid, []).append(task["id"])
    return [{"type": "no_overlap", "params": {"resource_id": rid, "task_ids": tids}}
            for rid, tids in resource_tasks.items() if len(tids) > 1]

def _build_physical_space_constraints(containers, tasks, config):
    safety_gap = config.get("safety_gap", 10)
    constraints = []
    for (bay, row), stack in _group_by_stack(containers, "yard").items():
        if len(stack) < 2: continue
        discharge = sorted([c for c in stack if c.get("operation_type") == "DISCHARGE"], key=lambda c: c["yard"]["tier"])
        load      = sorted([c for c in stack if c.get("operation_type", "LOAD") == "LOAD"], key=lambda c: c["yard"]["tier"], reverse=True)
        for group, op, delay in [(discharge, "PLACE", safety_gap), (load, "PICK", safety_gap)]:
            task_stack = []
            for c in group:
                cid = str(c.get("id") or c.get("containerId"))
                t = next((t for t in tasks if t["containerId"] == cid and t["operation"] == op), None)
                if t: task_stack.append({"container_id": cid, "tier": c["yard"]["tier"], "task_id": t["id"]})
            if len(task_stack) > 1:
                constraints.append({"type": "physical_stack_order", "params": {
                    "location": "yard", "bay": bay, "row": row, "stack": task_stack,
                    "order": "ascending" if op == "PLACE" else "descending", "delay": delay}})
    for (bay, row), stack in _group_by_stack(containers, "ship").items():
        if len(stack) < 2: continue
        discharge = sorted([c for c in stack if c.get("operation_type") == "DISCHARGE"], key=lambda c: c["ship"]["tier"], reverse=True)
        load      = sorted([c for c in stack if c.get("operation_type", "LOAD") == "LOAD"], key=lambda c: c["ship"]["tier"])
        for group, op, order, delay in [(discharge, "DISCHARGE", "descending", 1), (load, "LOAD", "ascending", 1)]:
            task_stack = []
            for c in group:
                cid = str(c.get("id") or c.get("containerId"))
                t = next((t for t in tasks if t["containerId"] == cid and t["operation"] == op), None)
                if t: task_stack.append({"container_id": cid, "tier": c["ship"]["tier"], "task_id": t["id"]})
            if len(task_stack) > 1:
                constraints.append({"type": "physical_stack_order", "params": {
                    "location": "ship", "bay": bay, "row": row, "stack": task_stack, "order": order, "delay": delay}})
    return constraints

def _group_by_stack(containers, location):
    stacks: Dict = {}
    for c in containers:
        loc = c.get(location)
        if loc:
            key = (loc.get("bay"), loc.get("row"))
            if None not in key: stacks.setdefault(key, []).append(c)
    return stacks

def _build_phase_separation_constraints(tasks):
    phase_a = [t["id"] for t in tasks if t["operation"] in ("DISCHARGE", "PLACE")]
    phase_b = [t["id"] for t in tasks if t["operation"] in ("LOAD", "MOVE")]
    if phase_a and phase_b:
        return [{"type": "phase_separation", "params": {
            "phase_a": "DISCHARGE", "phase_b": "LOAD",
            "phase_a_tasks": phase_a, "phase_b_tasks": phase_b, "constraint": "all_a_before_any_b"}}]
    return []

def _build_crane_interference_constraints(tasks, resources, config):
    safety_bays = config.get("crane_safety_bays", 2)
    constraints = []
    yard_cranes = resources.get("yard_cranes", [])
    for i in range(len(yard_cranes) - 1):
        a, b = yard_cranes[i], yard_cranes[i+1]
        ta = [t["id"] for t in tasks if t.get("resourceId") == a["id"]]
        tb = [t["id"] for t in tasks if t.get("resourceId") == b["id"]]
        if ta and tb:
            constraints.append({"type": "crane_interference", "params": {
                "crane_a": a["id"], "crane_b": b["id"], "safety_bays": safety_bays, "tasks_a": ta, "tasks_b": tb}})
    return constraints

def _build_shift_constraints(tasks, config):
    constraints = []
    shifts = config.get("shifts", [])
    breaks = config.get("breaks", [])
    crane_windows: Dict[str, List] = {}
    for s in shifts:
        for cid in s.get("cranes", []):
            crane_windows.setdefault(cid, []).append({"start": s.get("start", 0), "end": s.get("end", 0)})
    for cid, windows in crane_windows.items():
        tids = [t["id"] for t in tasks if t.get("resourceId") == cid]
        if tids:
            constraints.append({"type": "shift_window", "params": {"resource_id": cid, "windows": windows, "task_ids": tids}})
    shift_to_cranes = {s["id"]: s.get("cranes", []) for s in shifts}
    for brk in breaks:
        for cid in shift_to_cranes.get(brk.get("shift"), []):
            tids = [t["id"] for t in tasks if t.get("resourceId") == cid]
            if tids:
                constraints.append({"type": "shift_break", "params": {
                    "resource_id": cid,
                    "breaks": [{"start": brk.get("start", 0), "end": brk.get("start", 0) + brk.get("duration", 0)}],
                    "task_ids": tids}})
    return constraints

def _build_attribute_zone_constraints(containers, resources):
    constraints = []
    restricted = resources.get("restricted_zones", {})
    for attr, zone_key, match_fn in [
        ("REEFER",    "reefer", lambda c, z: z.get("bay") == c["yard"].get("bay") and z.get("row") == c["yard"].get("row")),
        ("IMO_CLASS", "imo",    lambda c, z: z.get("bay") == c["yard"].get("bay")),
    ]:
        zones = restricted.get(zone_key, [])
        if not zones: continue
        for c in containers:
            attrs = c.get("attrs", [])
            if not any(a == attr or a.startswith(attr) for a in attrs): continue
            yard = c.get("yard", {})
            if not any(match_fn(c, z) for z in zones):
                constraints.append({"type": "attribute_zone", "params": {
                    "attribute": attr, "container_id": c.get("id") or c.get("containerId"),
                    "allowed_zones": zones, "current_location": yard, "violation": True}})
    return constraints


# =============================================================
# 目的関数
# =============================================================

def _build_objective(containers, tasks, config):
    penalty_pairs = []
    for op, task_op in [("LOAD", "LOAD"), ("DISCHARGE", "DISCHARGE")]:
        op_containers = sorted([c for c in containers if c.get("operation_type") == op], key=lambda c: c.get("order", 0))
        for i in range(len(op_containers) - 1):
            cid1 = op_containers[i].get("id") or op_containers[i].get("containerId")
            cid2 = op_containers[i+1].get("id") or op_containers[i+1].get("containerId")
            t1 = next((t for t in tasks if t["containerId"] == cid1 and t["operation"] == task_op), None)
            t2 = next((t for t in tasks if t["containerId"] == cid2 and t["operation"] == task_op), None)
            if t1 and t2:
                penalty_pairs.append({"task_a": t1["id"], "task_b": t2["id"], "expected_order": "a_before_b"})
    return {"primary": "minimize_makespan",
            "weights": {"w_makespan": 1, "w_penalty": config.get("time_load", 300), "w_cost": 0},
            "penalty_pairs": penalty_pairs}
