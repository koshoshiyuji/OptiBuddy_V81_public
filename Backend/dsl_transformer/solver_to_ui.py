"""
Solver Output DSL → UI DSL 変換モジュール  (v6.1)

v6.0 → v6.1:
  - CapacitatedVehicleRoutingProblem を problem_class で振り分け追加。
    cvrp_ui_converter.convert_cvrp_to_ui() に委譲する。
  - 新規4DSLドメインも同じパターンで追加可能。
"""

import logging
from typing import Any, Dict, List, Set

logger = logging.getLogger(__name__)

def convert_solver_to_ui(solver_output: Dict[str, Any]) -> Dict[str, Any]:
    problem_class = solver_output.get("metadata", {}).get("problem_class", "")

    # 2026-07-11: EventStaffingは削除済み（StoreSiteはこの時点では未登録。2026-07-20に
    # 新規MIPドメインとして登録され、下記に振り分け先がある）。CapacitatedVehicleRoutingProblem（旧実装）は
    # TruckDispatcherに置き換え済みで、TruckDispatcher/NurseShiftはどちらも
    # app.py の _solve_4dsl_generic 経由（規約ベース自動検出）で処理されるため、
    # ここへの明示登録は不要。

    if problem_class == "LineChangeoverScheduler":
            from .line_changeover_scheduler_ui_converter import convert_line_changeover_scheduler_to_ui
            return convert_line_changeover_scheduler_to_ui(solver_output)


    if problem_class == "MeetingRoom":
            from .meeting_room_ui_converter import convert_meeting_room_to_ui
            return convert_meeting_room_to_ui(solver_output)


    if problem_class == "MeetingRoom2":
            from .meeting_room2_ui_converter import convert_meeting_room2_to_ui
            return convert_meeting_room2_to_ui(solver_output)


    if problem_class == "StoreSite":
            from .store_site_ui_converter import convert_store_site_to_ui
            return convert_store_site_to_ui(solver_output)


    if problem_class == "CarSequencing":
            from .car_sequencing_ui_converter import convert_car_sequencing_to_ui
            return convert_car_sequencing_to_ui(solver_output)


    if problem_class == "NursingWorkloadBalance":
            from .nursing_workload_balance_ui_converter import convert_nursing_workload_balance_to_ui
            return convert_nursing_workload_balance_to_ui(solver_output)


    if problem_class == "CapitalProjectSelector":
            from .capital_project_selector_ui_converter import convert_capital_project_selector_to_ui
            return convert_capital_project_selector_to_ui(solver_output)


    if problem_class == "CrewDutyScheduler":
            from .crew_duty_scheduler_ui_converter import convert_crew_duty_scheduler_to_ui
            return convert_crew_duty_scheduler_to_ui(solver_output)


    if problem_class == "ExampleDelivery":
            from .example_delivery_ui_converter import convert_example_delivery_to_ui
            return convert_example_delivery_to_ui(solver_output)


    if problem_class == "InventoryReplenishmentPlanner":
            from .inventory_replenishment_planner_ui_converter import convert_inventory_replenishment_planner_to_ui
            return convert_inventory_replenishment_planner_to_ui(solver_output)


    if problem_class == "AuctionWinnerSelector":
            from .auction_winner_selector_ui_converter import convert_auction_winner_selector_to_ui
            return convert_auction_winner_selector_to_ui(solver_output)


    if problem_class == "PortfolioOverlapDesigner":
            from .portfolio_overlap_designer_ui_converter import convert_portfolio_overlap_designer_to_ui
            return convert_portfolio_overlap_designer_to_ui(solver_output)


    if problem_class == "TransportCostMinimizer":
            from .transport_cost_minimizer_ui_converter import convert_transport_cost_minimizer_to_ui
            return convert_transport_cost_minimizer_to_ui(solver_output)


    if problem_class == "VesselDeckLoader":
            from .vessel_deck_loader_ui_converter import convert_vessel_deck_loader_to_ui
            return convert_vessel_deck_loader_to_ui(solver_output)


    if problem_class == "DepotRoutePlanner":
            from .depot_route_planner_ui_converter import convert_depot_route_planner_to_ui
            return convert_depot_route_planner_to_ui(solver_output)


    if problem_class == "ShiftRotationScheduler":
            from .shift_rotation_scheduler_ui_converter import convert_shift_rotation_scheduler_to_ui
            return convert_shift_rotation_scheduler_to_ui(solver_output)


    if problem_class == "LotSizingScheduler":
            from .lot_sizing_scheduler_ui_converter import convert_lot_sizing_scheduler_to_ui
            return convert_lot_sizing_scheduler_to_ui(solver_output)





    if problem_class == "PatientTransportPlanner":
            from .patient_transport_planner_ui_converter import convert_patient_transport_planner_to_ui
            return convert_patient_transport_planner_to_ui(solver_output)




    if problem_class == "MedicalAppointmentScheduler":
                from .medical_appointment_scheduler_ui_converter import convert_medical_appointment_scheduler_to_ui
                return convert_medical_appointment_scheduler_to_ui(solver_output)


    if problem_class == "MedicalAppointmentSequenceScheduler":
            from .medical_appointment_sequence_scheduler_ui_converter import convert_medical_appointment_sequence_scheduler_to_ui
            return convert_medical_appointment_sequence_scheduler_to_ui(solver_output)


    if problem_class == "MysteryShopperScheduler":
            from .mystery_shopper_scheduler_ui_converter import convert_mystery_shopper_scheduler_to_ui
            return convert_mystery_shopper_scheduler_to_ui(solver_output)


    if problem_class == "EnergyCostAwareScheduler":
            from .energy_cost_aware_scheduler_ui_converter import convert_energy_cost_aware_scheduler_to_ui
            return convert_energy_cost_aware_scheduler_to_ui(solver_output)


    if problem_class == "TankAllocationPlanner":
            from .tank_allocation_planner_ui_converter import convert_tank_allocation_planner_to_ui
            return convert_tank_allocation_planner_to_ui(solver_output)



    if problem_class == "ProductionLineSequencing":
            from .production_line_sequencing_ui_converter import convert_production_line_sequencing_to_ui
            return convert_production_line_sequencing_to_ui(solver_output)


    if problem_class == "RideshareMatchingPlanner":
            from .rideshare_matching_planner_ui_converter import convert_rideshare_matching_planner_to_ui
            return convert_rideshare_matching_planner_to_ui(solver_output)

# ── 新規4DSLドメインはここに追加 ──
    # if problem_class == "NextDomain":
    #     from .next_domain_ui_converter import convert_next_domain_to_ui
    #     return convert_next_domain_to_ui(solver_output)

    # ── YardPlanning デフォルト ──
    ui_dsl = {
        "metadata": solver_output.get("metadata", {}),
        "status": solver_output.get("status", "ok"),
        "solutions": [],
        "issues": convert_issues(solver_output.get("issues", [])),
        "config": build_ui_config(solver_output),
        "highlight": {
            "kind": "none",
            "hoverContainerId": "",
            "selectedIssueId": "",
            "relatedContainerIds": []
        }
    }

    containers = solver_output.get("containers", [])
    for solution in solver_output.get("solutions", []):
        ui_solution = {
            "name": solution.get("name", "Plan"),
            "label": solution.get("label", ""),
            "profile": solution.get("profile", {}),
            "tasks": convert_tasks(solution.get("tasks", []), containers),
            "snapshots": build_snapshots(solution.get("tasks", []), containers),
            "kpi": calculate_kpi(solution, solver_output)
        }
        ui_dsl["solutions"].append(ui_solution)

    return ui_dsl


def convert_tasks(solver_tasks: List[Dict], containers: List[Dict]) -> List[Dict]:
    logger.info(f"Converting {len(solver_tasks)} solver tasks to UI tasks with {len(containers)} containers")
    ui_tasks = []
    container_map = {c.get("id") or c.get("containerId"): c for c in containers}

    for task in solver_tasks:
        container = container_map.get(task.get("containerId"), {})
        from_loc, to_loc = determine_locations(task.get("operation", ""), container)
        ui_task = {
            "id": task.get("id", ""),
            "containerId": task.get("containerId", ""),
            "operation": task.get("operation", ""),
            "resource": task.get("resourceId", ""),
            "order": task.get("order", 0),
            "status": "scheduled",
            "start": task.get("start", 0),
            "end": task.get("end", 0),
            "weight": container.get("weight", 0),
            "from": from_loc,
            "to": to_loc,
            "domain": "YARD" if task.get("operation") in ("PICK", "PLACE", "REHANDLE") else "VESSEL",
            "yard": container.get("yard", {"bay": 0, "row": 0, "tier": 0}),
            "ship": container.get("ship", {"bay": 0, "row": 0, "tier": 0}),
            "ports": {"pol": container.get("pol", ""), "pod": container.get("pod", "")}
        }
        ui_tasks.append(ui_task)

    return ui_tasks


def determine_locations(operation: str, container: Dict) -> tuple:
    yard = container.get("yard", {"bay": 0, "row": 0, "tier": 0})
    ship = container.get("ship", {"bay": 0, "row": 0, "tier": 0})
    none_loc = {"domain": "NONE", "bay": 0, "row": 0, "tier": 0}
    if operation == "PICK":       return ({"domain": "YARD",   **yard}, none_loc)
    if operation == "LOAD":       return (none_loc,             {"domain": "VESSEL", **ship})
    if operation == "DISCHARGE":  return ({"domain": "VESSEL", **ship}, none_loc)
    if operation == "PLACE":      return (none_loc,             {"domain": "YARD",   **yard})
    if operation in ("MOVE", "REHANDLE"): return ({"domain": "YARD", **yard}, {"domain": "YARD", **yard})
    return (none_loc, none_loc)


def build_snapshots(tasks: List[Dict], containers: List[Dict]) -> Dict:
    time_points_set: Set[int] = {0}
    for t in tasks:
        time_points_set.add(t.get("start", 0))
        time_points_set.add(t.get("end", 0))
    time_points = sorted(list(time_points_set))
    snapshots = {"timePoints": time_points, "data": {}}
    for t in time_points:
        snapshots["data"][str(t)] = build_snapshot_at_time(t, tasks, containers)
    return snapshots


def build_snapshot_at_time(current_time: int, tasks: List[Dict], containers: List[Dict]) -> Dict:
    snapshot = {"yard": {}, "ship": {}, "containers": {}, "availableBays": []}
    container_map = {c.get("id") or c.get("containerId"): c for c in containers}

    for container in containers:
        cid = container.get("id") or container.get("containerId")
        container_tasks = sorted([t for t in tasks if t.get("containerId") == cid], key=lambda x: x.get("sequence", 0))
        op_type = container.get("operation_type", "LOAD")
        first_task_start = container_tasks[0].get("start", 0) if container_tasks else 0
        visible_in_yard = visible_in_vessel = False
        current_pos = "YARD"

        if current_time < first_task_start:
            if op_type == "LOAD":    visible_in_yard    = True; current_pos = "YARD"
            else:                    visible_in_vessel  = True; current_pos = "VESSEL"

        for task in container_tasks:
            if current_time < task.get("start", 0): break
            op  = task.get("operation", "")
            end = task.get("end", 0)
            if op == "PICK"      and current_time >= end: visible_in_yard    = False
            elif op == "LOAD"    and current_time >= end: visible_in_vessel  = True;  current_pos = "VESSEL"
            elif op == "DISCHARGE" and current_time >= end: visible_in_vessel = False
            elif op == "PLACE"   and current_time >= end: visible_in_yard    = True;  current_pos = "YARD"
            elif op in ("MOVE", "REHANDLE") and current_time >= end: visible_in_yard = True; current_pos = "YARD"

        snapshot["containers"][cid] = {
            "containerId": cid, "weight": container.get("weight", 0),
            "currentPos": current_pos,
            "yard": container.get("yard", {"bay": 0, "row": 0, "tier": 0}),
            "ship": container.get("ship", {"bay": 0, "row": 0, "tier": 0}),
            "pod": container.get("pod", ""), "pol": container.get("pol", ""),
            "visibleInYard": visible_in_yard, "visibleInVessel": visible_in_vessel
        }
        if visible_in_yard:
            yard = container.get("yard", {})
            b, r, t = yard.get("bay"), yard.get("row"), yard.get("tier")
            if None not in (b, r, t):
                snapshot["yard"].setdefault(b, {}).setdefault(r, {})[t] = cid
                if b not in snapshot["availableBays"]: snapshot["availableBays"].append(b)
        if visible_in_vessel:
            ship = container.get("ship", {})
            b, r, t = ship.get("bay"), ship.get("row"), ship.get("tier")
            if None not in (b, r, t):
                snapshot["ship"].setdefault(b, {}).setdefault(r, {})[t] = cid
                if b not in snapshot["availableBays"]: snapshot["availableBays"].append(b)

    snapshot["availableBays"].sort()
    return snapshot


def calculate_kpi(solution: Dict, solver_output: Dict) -> Dict:
    tasks    = solution.get("tasks", [])
    metrics  = solution.get("metrics") or {}
    makespan = metrics.get("makespan") or solution.get("makespan", 0)
    crane_util: Dict[str, int] = {}
    for task in tasks:
        resource = task.get("resourceId") or task.get("resource", "")
        if resource: crane_util[resource] = crane_util.get(resource, 0) + task.get("duration", 0)
    crane_util_list = [{"id": k, "util": round((v / makespan) * 100)}
                       for k, v in sorted(crane_util.items())] if makespan > 0 else []
    rehandle_count = sum(1 for t in tasks if t.get("operation") == "REHANDLE")
    total_wait = task_count = 0
    container_tasks: Dict[str, List] = {}
    for task in tasks:
        container_tasks.setdefault(task.get("containerId", ""), []).append(task)
    for ctasks in container_tasks.values():
        ctasks.sort(key=lambda t: t.get("sequence", 0))
        for i in range(1, len(ctasks)):
            wait = ctasks[i].get("start", 0) - ctasks[i-1].get("end", 0)
            if wait > 0: total_wait += wait; task_count += 1
    avg_wait_min = round(total_wait / task_count / 60) if task_count > 0 else 0
    issues = solver_output.get("issues", [])
    return {
        "makespan": makespan, "makespanMin": round(makespan / 60),
        "penalty": metrics.get("penalty") if "penalty" in metrics else solution.get("penalty", 0),
        "cost": metrics.get("cost", 0),
        "objective_value": metrics.get("objective_value", makespan),
        "solve_time": metrics.get("solve_time", 0),
        "craneUtil": crane_util_list,
        "rehandleCount": rehandle_count, "avgWaitMin": avg_wait_min,
        "resolvedCount": sum(1 for i in issues if i.get("status") == "RESOLVED"),
        "skippedCount":  sum(1 for i in issues if i.get("status") == "ACCEPTED"),
        "remainCount":   sum(1 for i in issues if i.get("status") not in ("RESOLVED","ACCEPTED")),
    }


def convert_issues(solver_issues: List[Dict]) -> List[Dict]:
    return [{
        "id": i.get("id", ""), "title": i.get("title", ""), "message": i.get("message", ""),
        "containerId": i.get("containerId", ""), "relatedContainerIds": i.get("relatedContainerIds", []),
        "severity": i.get("severity", "WARNING"), "category": i.get("category", "YARD"),
        "fixStrategy": "MANUAL", "status": "UNRESOLVED", "action": "NONE", "pair": i.get("pair")
    } for i in solver_issues]


def build_ui_config(solver_output: Dict) -> Dict:
    config    = solver_output.get("config", {})
    containers = solver_output.get("containers", [])
    resources  = solver_output.get("resources", {})
    yard_bays  = sorted({c["yard"]["bay"] for c in containers if c.get("yard") and c["yard"].get("bay") is not None})
    ship_bays  = sorted({c["ship"]["bay"] for c in containers if c.get("ship") and c["ship"].get("bay") is not None})
    return {
        "yardBays": yard_bays, "shipBays": ship_bays,
        "yard_limits":   solver_output.get("yard_limits",   {"max_bay": config.get("max_bays", 5),    "max_row": config.get("max_rows", 3),        "max_tier": config.get("max_tiers", 4)}),
        "vessel_limits": solver_output.get("vessel_limits", {"max_bay": config.get("max_vessel_bays", 20), "max_row": config.get("max_vessel_rows", 8), "max_tier": config.get("max_vessel_tiers", 6)}),
        "operation_start": config.get("operation_start", "08:00"),
        "operation_date":  config.get("operation_date", ""),
        "current_port":    config.get("current_port", ""),
        "shifts": config.get("shifts", []), "breaks": config.get("breaks", []),
        "restricted_zones": resources.get("restricted_zones", {"reefer": [], "imo": []}),
        "resources": resources,
    }
