"""
issue_rules.py — 層B: イシュー検知ルールカタログ
==================================================

【設計方針】
- 各ルールは「条件関数」と「イシュー生成関数」のペアで定義。
- condition(ctx) -> bool : True のとき発火
- build(ctx)    -> Dict  : イシュー辞書を返す
- ctx (context 辞書) にドメイン固有データを注入する。

【層の境界】
- 層A (共通コア): run_issue_rules() エンジン、IssueRule dataclass
- 層B (レシピ): ISSUE_RULES 宣言（ドメイン別ルールリスト）
- 層C (ドメイン固有): なし (context 経由で注入)

【エントリポイント】
  from solvers.base.issue_rules import run_issue_rules

  issues = run_issue_rules(
      domain         = "YardPlanning",   # or "NurseShiftWeeklyCap" など（ISSUE_RULESのキー）
      contexts       = build_yard_contexts(solver_input, solution_data),
      issue_statuses = solver_input.get("issue_statuses", {}),
  )

【context キー一覧】

  --- YardPlanning ---
  ペアルール (is_yard / weight_yard / is_ship / weight_ship):
    - cid_a, cid_b: str
    - c_a, c_b: Dict (コンテナ辞書)
    - pair_id: str

  ATTRルール (attr_reefer / attr_imo / attr_oog):
    - cid: str
    - c: Dict
    - bay, row, tier: Any
    - reefer_ids, imo_ids, oog_ids: Set[str]
    - reefer_slot_set, imo_slot_set, oog_excluded_slots: Set
    - all_containers: List[Dict]  (OOGソース特定用)

  シフトルール (shift_violation / shift_break):
    - t: Dict (タスク結果)
    - crane_id: str
    - crane_windows: Dict[str, List[Tuple]]
    - break_windows: Dict[str, List[Tuple]]

  フェーズルール (discharge_before_load):
    - lt: Dict (LOADタスク結果)
    - max_d_end: int

  --- NurseShift/NurseShiftWeeklyCap（旧EventStaffing由来の契約を継承。下記407行目のコメント参照）---
  solve_failed:
    - solution_data: Optional[Dict]  (None のとき発火)

  understaffed / no_chief:
    - tid: str
    - req: Dict {required_count, min_chiefs, assigned, chiefs}

  unassigned_staff:
    - sid: str
    - staff: Dict
    - staff_ids_with_assignment: Set[str]

  pref_unmet:
    - sid: str
    - staff: Dict
    - assigned_task_ids: Set[str]
    - existing_issue_ids: Set[str]  (同一SIDで重複しないよう管理)

  high_wait:
    - sid: str
    - staff: Dict
    - bind_min: int
    - work_min: int
"""


from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable, Dict, List, Optional, Tuple

from solvers.base.solution_checker import solver_bug_issue, sweep_peak_usage
from i18n.common_messages import t as _common_t
from i18n.nurse_shift_weekly_cap_messages import t as _nurse_shift_t

from . import _common
from ._common import IssueRule, run_issue_rules, build_full_unassignment_issue

from .yard import _YARD_RULES, build_yard_contexts
from .nurse_shift import _NURSE_SHIFT_RULES
from .truck_dispatcher import _TRUCK_DISPATCHER_RULES, build_truck_dispatcher_contexts
from .meeting_room import _MEETING_ROOM_RULES, build_meeting_room_field_contexts, build_meeting_room_overlap_contexts
from .store_site import _STORE_SITE_RULES, build_store_site_contexts
from .line_changeover import _LINE_CHANGEOVER_RULES, build_line_changeover_precedence_contexts, build_line_changeover_resource_contexts
from .transport_cost_minimizer import _TRANSPORT_COST_MINIMIZER_RULES, build_transport_cost_minimizer_contexts
from .tank_allocation import _TANK_ALLOCATION_RULES, build_tank_allocation_capacity_contexts, build_tank_allocation_incompatibility_contexts
from .auction_winner_selector import _AUCTION_WINNER_SELECTOR_RULES, build_auction_winner_selector_contexts
from .capital_project_selector import _CAPITAL_PROJECT_SELECTOR_RULES, build_capital_project_selector_contexts
from .inventory_replenishment_planner import _INVENTORY_REPLENISHMENT_PLANNER_RULES, build_inventory_replenishment_planner_contexts
from .mystery_shopper_scheduler import _MYSTERY_SHOPPER_SCHEDULER_RULES, build_mystery_shopper_scheduler_contexts
from .nursing_workload_balance import _NURSING_WORKLOAD_BALANCE_RULES, build_nursing_workload_balance_contexts
from .portfolio_overlap_designer import _PORTFOLIO_OVERLAP_DESIGNER_RULES, build_portfolio_overlap_designer_contexts
from .car_sequencing import _CAR_SEQUENCING_RULES, build_car_sequencing_contexts
from .energy_cost_aware_scheduler import _ENERGY_COST_AWARE_SCHEDULER_RULES, build_energy_cost_aware_scheduler_contexts
from .lot_sizing_scheduler import _LOT_SIZING_SCHEDULER_RULES, build_lot_sizing_scheduler_contexts
from .medical_appointment_scheduler import _MEDICAL_APPOINTMENT_SCHEDULER_RULES, build_medical_appointment_scheduler_contexts
from .medical_appointment_sequence_scheduler import _MEDICAL_APPOINTMENT_SEQUENCE_SCHEDULER_RULES, build_medical_appointment_sequence_scheduler_contexts
from .production_line_sequencing import _PRODUCTION_LINE_SEQUENCING_RULES, build_production_line_sequencing_contexts
from .rideshare_matching_planner import _RIDESHARE_MATCHING_PLANNER_RULES, build_rideshare_matching_planner_contexts
from .shift_rotation_scheduler import _SHIFT_ROTATION_SCHEDULER_RULES, build_shift_rotation_scheduler_contexts
from .steel_mill_slab_design import _STEEL_MILL_SLAB_DESIGN_RULES, build_steel_mill_slab_design_contexts
from .vessel_deck_loader import _VESSEL_DECK_LOADER_RULES, build_vessel_deck_loader_contexts




# ---------------------------------------------------------------------------
# レシピ宣言 (層B)
# ---------------------------------------------------------------------------

ISSUE_RULES: Dict[str, List[IssueRule]] = {
    "YardPlanning":            _YARD_RULES,
    "NurseShift":              _NURSE_SHIFT_RULES,
    "NurseShiftWeeklyCap":     _NURSE_SHIFT_RULES,
    "TruckDispatcher":         _TRUCK_DISPATCHER_RULES,
    "MeetingRoom":             _MEETING_ROOM_RULES,
    "StoreSite":               _STORE_SITE_RULES,
    "LineChangeoverScheduler": _LINE_CHANGEOVER_RULES,
    "TransportCostMinimizer":       _TRANSPORT_COST_MINIMIZER_RULES,
    "TankAllocationPlanner":        _TANK_ALLOCATION_RULES,
    "AuctionWinnerSelector":        _AUCTION_WINNER_SELECTOR_RULES,
    "CapitalProjectSelector":       _CAPITAL_PROJECT_SELECTOR_RULES,
    "InventoryReplenishmentPlanner": _INVENTORY_REPLENISHMENT_PLANNER_RULES,
    "MysteryShopperScheduler":      _MYSTERY_SHOPPER_SCHEDULER_RULES,
    "NursingWorkloadBalance":       _NURSING_WORKLOAD_BALANCE_RULES,
    "PortfolioOverlapDesigner":     _PORTFOLIO_OVERLAP_DESIGNER_RULES,
    "CarSequencing":                 _CAR_SEQUENCING_RULES,
    "EnergyCostAwareScheduler":      _ENERGY_COST_AWARE_SCHEDULER_RULES,
    "LotSizingScheduler":            _LOT_SIZING_SCHEDULER_RULES,
    "MedicalAppointmentScheduler":         _MEDICAL_APPOINTMENT_SCHEDULER_RULES,
    "MedicalAppointmentSequenceScheduler": _MEDICAL_APPOINTMENT_SEQUENCE_SCHEDULER_RULES,
    "ProductionLineSequencing":            _PRODUCTION_LINE_SEQUENCING_RULES,
    "RideshareMatchingPlanner":            _RIDESHARE_MATCHING_PLANNER_RULES,
    "ShiftRotationScheduler":               _SHIFT_ROTATION_SCHEDULER_RULES,
    "SteelMillSlabDesign":                  _STEEL_MILL_SLAB_DESIGN_RULES,
    "VesselDeckLoader":                      _VESSEL_DECK_LOADER_RULES,
}

# run_issue_rules() lives in _common.py, but it references the bare name
# ISSUE_RULES via its own module globals. Since ISSUE_RULES is only built
# here (after all per-domain modules are imported), it must be injected
# into _common's namespace explicitly, or run_issue_rules() raises
# NameError at call time.
_common.ISSUE_RULES = ISSUE_RULES

