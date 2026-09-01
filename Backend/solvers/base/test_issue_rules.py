"""
test_issue_rules.py
===================

issue_rules.py のユニットテスト。外部依存なし（docplex 不要）。

実行方法:
    cd Backend
    python -m pytest solvers/base/test_issue_rules.py -v
"""

import pytest
from solvers.base.issue_rules import (
    run_issue_rules,
    build_yard_contexts,
    build_truck_dispatcher_contexts,
    build_meeting_room_field_contexts,
    build_meeting_room_overlap_contexts,
    build_store_site_contexts,
    build_line_changeover_precedence_contexts,
    build_line_changeover_resource_contexts,
    build_transport_cost_minimizer_contexts,
    build_tank_allocation_capacity_contexts,
    build_tank_allocation_incompatibility_contexts,
    build_auction_winner_selector_contexts,
    build_capital_project_selector_contexts,
    build_inventory_replenishment_planner_contexts,
    build_mystery_shopper_scheduler_contexts,
    build_nursing_workload_balance_contexts,
    build_portfolio_overlap_designer_contexts,
    build_car_sequencing_contexts,
    build_energy_cost_aware_scheduler_contexts,
    build_lot_sizing_scheduler_contexts,
    build_medical_appointment_scheduler_contexts,
    build_medical_appointment_sequence_scheduler_contexts,
    build_patient_transport_planner_contexts,
    build_production_line_sequencing_contexts,
    build_rideshare_matching_planner_contexts,
    build_shift_rotation_scheduler_contexts,
    build_steel_mill_slab_design_contexts,
    build_vessel_deck_loader_contexts,
    ISSUE_RULES,
)


# ---------------------------------------------------------------------------
# フィクスチャ
# ---------------------------------------------------------------------------

def _container(cid, yard=None, ship=None, order=None, weight=0, attrs=None):
    return {
        "id": cid, "yard": yard or {}, "ship": ship or {},
        "order": order, "weight": weight, "attrs": attrs or [],
    }


# ---------------------------------------------------------------------------
# テスト: ルール登録
# ---------------------------------------------------------------------------

class TestRegistration:
    def test_domains_registered(self):
        assert "YardPlanning"  in ISSUE_RULES

    def test_yard_rule_ids(self):
        ids = {r.rule_id for r in ISSUE_RULES["YardPlanning"]}
        expected = {"is_yard", "weight_yard", "is_ship", "weight_ship",
                    "attr_reefer", "attr_imo", "shift_violation", "shift_break",
                    "discharge_before_load"}
        assert expected <= ids

    def test_solution_checker_domains_registered(self):
        """2026-07-24追加: 解チェッカー対象4ドメインがISSUE_RULESに登録されている"""
        for domain in ("TruckDispatcher", "MeetingRoom", "StoreSite", "LineChangeoverScheduler"):
            assert domain in ISSUE_RULES


# ---------------------------------------------------------------------------
# テスト: run_issue_rules エンジン
# ---------------------------------------------------------------------------

class TestRunIssueRules:
    def test_accepted_issues_skipped(self):
        cid_a, cid_b = "A", "B"
        container_map = {
            cid_a: _container(cid_a, yard={"bay": 1, "row": "A", "tier": 2}, order=2, weight=5),
            cid_b: _container(cid_b, yard={"bay": 1, "row": "A", "tier": 1}, order=1, weight=3),
        }
        ctxs = build_yard_contexts(
            container_map=container_map, containers=list(container_map.values()),
            solution_data_list=[], reefer_ids=set(), imo_ids=set(), oog_ids=set(),
            reefer_slot_set=set(), imo_slot_set=set(), oog_excluded_slots=set(),
            crane_windows={}, break_windows={},
        )
        # ACCEPTED なしで発火
        issues = run_issue_rules("YardPlanning", ctxs, {})
        iids = {i["id"] for i in issues}
        assert "is_yard_B_A" in iids or any("is_yard" in iid for iid in iids)

        # ACCEPTED ありでスキップ
        iid = next(i["id"] for i in issues if "is_yard" in i["id"])
        issues2 = run_issue_rules("YardPlanning", ctxs, {iid: "ACCEPTED"})
        iids2 = {i["id"] for i in issues2}
        assert iid not in iids2

    def test_unknown_rule_id_skipped(self):
        ctxs = [{"_rule_id": "nonexistent_rule"}]
        issues = run_issue_rules("YardPlanning", ctxs, {})
        assert issues == []

    def test_exception_in_rule_does_not_crash(self):
        """condition が例外を投げても run_issue_rules がクラッシュしない"""
        ctxs = [{"_rule_id": "is_yard"}]  # 必要キーが欠けている → 例外発生
        issues = run_issue_rules("YardPlanning", ctxs, {})
        # クラッシュせず空リストを返す
        assert isinstance(issues, list)


# ---------------------------------------------------------------------------
# テスト: YardPlanning ルール
# ---------------------------------------------------------------------------

class TestYardPlanningRules:

    def _base_container_map(self):
        # lower=A(tier=1, order=1), upper=B(tier=2, order=2)
        # → 上段B(order大=後から積む)が下段A(order小=先に積む)の上にある
        # → A を取り出すには B を先に退かす必要 → リハンドリング発生
        # is_yard condition: o_u(2) > o_l(1) → True で発火
        return {
            "A": _container("A", yard={"bay": 1, "row": "A", "tier": 1}, order=1, weight=3),
            "B": _container("B", yard={"bay": 1, "row": "A", "tier": 2}, order=2, weight=5),
        }

    def test_is_yard_fires_on_reverse_order(self):
        """上段が先に積む順(order小)で下段が後(order大) → リハンドリング発生"""
        cmap = self._base_container_map()
        ctxs = build_yard_contexts(
            container_map=cmap, containers=list(cmap.values()),
            solution_data_list=[], reefer_ids=set(), imo_ids=set(), oog_ids=set(),
            reefer_slot_set=set(), imo_slot_set=set(), oog_excluded_slots=set(),
            crane_windows={}, break_windows={},
        )
        issues = run_issue_rules("YardPlanning", ctxs, {})
        yard_issues = [i for i in issues if i["id"].startswith("is_yard")]
        assert len(yard_issues) >= 1
        assert yard_issues[0]["severity"] == "WARNING"
        assert yard_issues[0]["category"] == "YARD"

    def test_weight_yard_fires_on_heavy_above_light(self):
        """上段が重い → WARNING"""
        cmap = {
            "A": _container("A", yard={"bay": 1, "row": "A", "tier": 1}, weight=3),
            "B": _container("B", yard={"bay": 1, "row": "A", "tier": 2}, weight=10),
        }
        ctxs = build_yard_contexts(
            container_map=cmap, containers=list(cmap.values()),
            solution_data_list=[], reefer_ids=set(), imo_ids=set(), oog_ids=set(),
            reefer_slot_set=set(), imo_slot_set=set(), oog_excluded_slots=set(),
            crane_windows={}, break_windows={},
        )
        issues = run_issue_rules("YardPlanning", ctxs, {})
        w_issues = [i for i in issues if i["id"].startswith("weight-yard")]
        assert len(w_issues) >= 1

    def test_is_yard_no_fire_same_bay_different_row(self):
        """同じbay・異なるrow → 同スタックではないので発火しない"""
        cmap = {
            "A": _container("A", yard={"bay": 1, "row": "A", "tier": 1}, order=2),
            "B": _container("B", yard={"bay": 1, "row": "B", "tier": 2}, order=1),
        }
        ctxs = build_yard_contexts(
            container_map=cmap, containers=list(cmap.values()),
            solution_data_list=[], reefer_ids=set(), imo_ids=set(), oog_ids=set(),
            reefer_slot_set=set(), imo_slot_set=set(), oog_excluded_slots=set(),
            crane_windows={}, break_windows={},
        )
        issues = run_issue_rules("YardPlanning", ctxs, {})
        yard_issues = [i for i in issues if i["id"].startswith("is_yard")]
        assert yard_issues == []

    def test_attr_reefer_fires(self):
        """Reeferコンテナが非Reeferスロット → CRITICAL"""
        c = _container("R1", yard={"bay": 2, "row": "A", "tier": 1}, attrs=["REEFER"])
        cmap = {"R1": c}
        ctxs = build_yard_contexts(
            container_map=cmap, containers=[c],
            solution_data_list=[], reefer_ids={"R1"}, imo_ids=set(), oog_ids=set(),
            reefer_slot_set={(1, "A", 1)},  # bay=1のみ → bay=2は違反
            imo_slot_set=set(), oog_excluded_slots=set(),
            crane_windows={}, break_windows={},
        )
        issues = run_issue_rules("YardPlanning", ctxs, {})
        reefer_issues = [i for i in issues if i["id"].startswith("attr_reefer")]
        assert len(reefer_issues) == 1
        assert reefer_issues[0]["severity"] == "CRITICAL"

    def test_attr_reefer_no_fire_in_reefer_slot(self):
        """Reeferコンテナが正しいスロット → 発火しない"""
        c = _container("R1", yard={"bay": 1, "row": "A", "tier": 1}, attrs=["REEFER"])
        cmap = {"R1": c}
        ctxs = build_yard_contexts(
            container_map=cmap, containers=[c],
            solution_data_list=[], reefer_ids={"R1"}, imo_ids=set(), oog_ids=set(),
            reefer_slot_set={(1, "A", 1)},
            imo_slot_set=set(), oog_excluded_slots=set(),
            crane_windows={}, break_windows={},
        )
        issues = run_issue_rules("YardPlanning", ctxs, {})
        assert not any(i["id"].startswith("attr_reefer") for i in issues)

    def test_shift_violation_fires(self):
        """タスクがシフト窓外 → WARNING"""
        t = {"id": "T1", "containerId": "C1", "start": 0, "end": 3600,
             "resource": "RC1", "operation": "PICK"}
        ctxs = build_yard_contexts(
            container_map={}, containers=[],
            solution_data_list=[{"tasks": [t]}],
            reefer_ids=set(), imo_ids=set(), oog_ids=set(),
            reefer_slot_set=set(), imo_slot_set=set(), oog_excluded_slots=set(),
            crane_windows={"RC1": [(60, 90)]},   # 60-90分のみ稼働
            break_windows={},
        )
        issues = run_issue_rules("YardPlanning", ctxs, {})
        shift_issues = [i for i in issues if "shift_violation" in i["id"]]
        assert len(shift_issues) >= 1

    def test_discharge_before_load_fires(self):
        """LOADがDISCHARGE完了前に開始 → CRITICAL"""
        d_task = {"id": "D1", "containerId": "C1", "start": 0,    "end": 100, "operation": "DISCHARGE"}
        l_task = {"id": "L1", "containerId": "C2", "start": 50,   "end": 150, "operation": "LOAD"}
        ctxs = build_yard_contexts(
            container_map={}, containers=[],
            solution_data_list=[{"tasks": [d_task, l_task]}],
            reefer_ids=set(), imo_ids=set(), oog_ids=set(),
            reefer_slot_set=set(), imo_slot_set=set(), oog_excluded_slots=set(),
            crane_windows={}, break_windows={},
        )
        issues = run_issue_rules("YardPlanning", ctxs, {})
        phase_issues = [i for i in issues if "discharge_before_load" in i["id"]]
        assert len(phase_issues) >= 1
        assert phase_issues[0]["severity"] == "CRITICAL"


# ---------------------------------------------------------------------------
# テスト: TruckDispatcher 解チェッカー（2026-07-24追加）
# ---------------------------------------------------------------------------

class TestTruckDispatcherChecker:
    def test_duty_overtime_fires_with_solver_category(self):
        """拘束時間ハード制約違反 → category=SOLVER（バグ疑い）"""
        routes = [{"vehicle_id": "V1", "vehicle_name": "車1", "duty_min": 600, "stops": []}]
        ctxs = build_truck_dispatcher_contexts(routes, {"V1": 540})
        issues = run_issue_rules("TruckDispatcher", ctxs, {})
        overtime = [i for i in issues if i["id"] == "duty_overtime"]
        assert len(overtime) == 1
        assert overtime[0]["category"] == "SOLVER"
        assert overtime[0]["severity"] == "CRITICAL"

    def test_duty_overtime_no_fire_within_limit(self):
        routes = [{"vehicle_id": "V1", "vehicle_name": "車1", "duty_min": 500, "stops": []}]
        ctxs = build_truck_dispatcher_contexts(routes, {"V1": 540})
        issues = run_issue_rules("TruckDispatcher", ctxs, {})
        assert not any(i["id"] == "duty_overtime" for i in issues)

    def test_tw_overdue_fires_without_solver_category(self):
        """TW超過はソフト制約の業務結果 → category=SOLVERは付与しない"""
        routes = [{"vehicle_id": "V1", "vehicle_name": "車1", "duty_min": 100, "stops": [
            {"customer_name": "顧客A", "arrival_min": 700, "tw_close_min": 600},
        ]}]
        ctxs = build_truck_dispatcher_contexts(routes, {"V1": 540})
        issues = run_issue_rules("TruckDispatcher", ctxs, {})
        overdue = [i for i in issues if i["id"] == "tw_overdue"]
        assert len(overdue) == 1
        assert overdue[0].get("category") != "SOLVER"


# ---------------------------------------------------------------------------
# テスト: MeetingRoom 解チェッカー（2026-07-24新規）
# ---------------------------------------------------------------------------

class TestMeetingRoomChecker:
    def _assignment(self, mid, room_id, room_name, start, end, attendees=4, features=None):
        return {
            "meeting_id": mid, "meeting_name": f"会議{mid}", "room_id": room_id,
            "room_name": room_name, "start_min": start, "end_min": end,
            "attendees": attendees, "features": features or [],
        }

    def test_capacity_violation_fires(self):
        a = self._assignment("M1", "R1", "会議室1", 0, 60, attendees=10)
        rooms = [{"id": "R1", "capacity": 5, "features": [], "available_start_min": 0, "available_end_min": 1440}]
        ctxs = build_meeting_room_field_contexts([a], rooms)
        issues = run_issue_rules("MeetingRoom", ctxs, {})
        cap_issues = [i for i in issues if i["id"].startswith("assignment_capacity_violation")]
        assert len(cap_issues) == 1
        assert cap_issues[0]["category"] == "SOLVER"

    def test_feature_violation_fires(self):
        a = self._assignment("M1", "R1", "会議室1", 0, 60, features=["projector"])
        rooms = [{"id": "R1", "capacity": 10, "features": [], "available_start_min": 0, "available_end_min": 1440}]
        ctxs = build_meeting_room_field_contexts([a], rooms)
        issues = run_issue_rules("MeetingRoom", ctxs, {})
        assert any(i["id"].startswith("assignment_feature_violation") for i in issues)

    def test_window_violation_fires(self):
        a = self._assignment("M1", "R1", "会議室1", 0, 60)
        rooms = [{"id": "R1", "capacity": 10, "features": [], "available_start_min": 30, "available_end_min": 1440}]
        ctxs = build_meeting_room_field_contexts([a], rooms)
        issues = run_issue_rules("MeetingRoom", ctxs, {})
        assert any(i["id"].startswith("assignment_window_violation") for i in issues)

    def test_no_violation_when_all_ok(self):
        a = self._assignment("M1", "R1", "会議室1", 100, 160, attendees=4, features=["projector"])
        rooms = [{"id": "R1", "capacity": 10, "features": ["projector"],
                  "available_start_min": 0, "available_end_min": 1440}]
        ctxs = build_meeting_room_field_contexts([a], rooms)
        issues = run_issue_rules("MeetingRoom", ctxs, {})
        assert issues == []

    def test_room_double_booking_fires_on_overlap(self):
        a = self._assignment("M1", "R1", "会議室1", 0, 100)
        b = self._assignment("M2", "R1", "会議室1", 50, 150)
        ctxs = build_meeting_room_overlap_contexts([a, b])
        issues = run_issue_rules("MeetingRoom", ctxs, {})
        assert len(issues) == 1
        assert issues[0]["category"] == "SOLVER"

    def test_room_double_booking_no_fire_when_disjoint(self):
        a = self._assignment("M1", "R1", "会議室1", 0, 100)
        b = self._assignment("M2", "R1", "会議室1", 100, 200)
        ctxs = build_meeting_room_overlap_contexts([a, b])
        issues = run_issue_rules("MeetingRoom", ctxs, {})
        assert issues == []

    def test_room_double_booking_no_fire_different_rooms(self):
        a = self._assignment("M1", "R1", "会議室1", 0, 100)
        b = self._assignment("M2", "R2", "会議室2", 0, 100)
        ctxs = build_meeting_room_overlap_contexts([a, b])
        issues = run_issue_rules("MeetingRoom", ctxs, {})
        assert issues == []


# ---------------------------------------------------------------------------
# テスト: StoreSite 解チェッカー（2026-07-24新規）
# ---------------------------------------------------------------------------

class TestStoreSiteChecker:
    def test_capacity_violation_fires(self):
        store = {"candidate_id": "C1", "name": "店舗1", "capacity": 100,
                 "total_demand": 150, "assigned_areas": []}
        ctxs = build_store_site_contexts([store], {}, 1e9, lambda a, c: 0.0, {"C1": store})
        issues = run_issue_rules("StoreSite", ctxs, {})
        assert any(i["id"].startswith("store_capacity_violation") for i in issues)
        assert all(i["category"] == "SOLVER" for i in issues)

    def test_distance_violation_fires(self):
        store = {"candidate_id": "C1", "name": "店舗1", "capacity": 100,
                 "total_demand": 50, "assigned_areas": ["A1"]}
        area_map = {"A1": {"id": "A1", "name": "エリア1"}}
        cand_map = {"C1": store}
        ctxs = build_store_site_contexts([store], area_map, 10.0, lambda a, c: 20.0, cand_map)
        issues = run_issue_rules("StoreSite", ctxs, {})
        dist_issues = [i for i in issues if i["id"].startswith("store_distance_violation")]
        assert len(dist_issues) == 1
        assert dist_issues[0]["category"] == "SOLVER"

    def test_no_violation_when_within_limits(self):
        store = {"candidate_id": "C1", "name": "店舗1", "capacity": 100,
                 "total_demand": 50, "assigned_areas": ["A1"]}
        area_map = {"A1": {"id": "A1", "name": "エリア1"}}
        cand_map = {"C1": store}
        ctxs = build_store_site_contexts([store], area_map, 10.0, lambda a, c: 5.0, cand_map)
        issues = run_issue_rules("StoreSite", ctxs, {})
        assert issues == []


# ---------------------------------------------------------------------------
# テスト: LineChangeoverScheduler 解チェッカー（2026-07-24新規）
# ---------------------------------------------------------------------------

class TestLineChangeoverSchedulerChecker:
    def test_precedence_violation_fires(self):
        schedule = [
            {"task_id": "T1", "start": 0, "end": 100},
            {"task_id": "T2", "start": 90, "end": 150},  # T1終了(100)+delay(10)=110 > 90 → 違反
        ]
        precedences = [{"from_task": "T1", "to_task": "T2", "min_delay": 10}]
        ctxs = build_line_changeover_precedence_contexts(schedule, precedences)
        issues = run_issue_rules("LineChangeoverScheduler", ctxs, {})
        assert len(issues) == 1
        assert issues[0]["category"] == "SOLVER"

    def test_precedence_ok_no_fire(self):
        schedule = [
            {"task_id": "T1", "start": 0, "end": 100},
            {"task_id": "T2", "start": 120, "end": 150},
        ]
        precedences = [{"from_task": "T1", "to_task": "T2", "min_delay": 10}]
        ctxs = build_line_changeover_precedence_contexts(schedule, precedences)
        issues = run_issue_rules("LineChangeoverScheduler", ctxs, {})
        assert issues == []

    def test_resource_capacity_violation_fires(self):
        # 資源capacity=1に対し、T1(0-100)とT2(50-150)が同時に1ずつ使用 → 50-100の間で2使用、超過
        schedule = [
            {"task_id": "T1", "start": 0, "end": 100,
             "resource_requirements": [{"resource_id": "R1", "amount": 1}]},
            {"task_id": "T2", "start": 50, "end": 150,
             "resource_requirements": [{"resource_id": "R1", "amount": 1}]},
        ]
        resources = [{"id": "R1", "name": "資源1", "capacity": 1}]
        ctxs = build_line_changeover_resource_contexts(schedule, resources)
        issues = run_issue_rules("LineChangeoverScheduler", ctxs, {})
        assert len(issues) == 1
        assert issues[0]["category"] == "SOLVER"

    def test_resource_capacity_ok_no_fire(self):
        # 直列（重複なし）なら容量1でも違反しない
        schedule = [
            {"task_id": "T1", "start": 0, "end": 100,
             "resource_requirements": [{"resource_id": "R1", "amount": 1}]},
            {"task_id": "T2", "start": 100, "end": 200,
             "resource_requirements": [{"resource_id": "R1", "amount": 1}]},
        ]
        resources = [{"id": "R1", "name": "資源1", "capacity": 1}]
        ctxs = build_line_changeover_resource_contexts(schedule, resources)
        issues = run_issue_rules("LineChangeoverScheduler", ctxs, {})
        assert issues == []


# ---------------------------------------------------------------------------
# テスト: TransportCostMinimizer 解チェッカー（2026-08-30新規、パイロット第1弾）
# ---------------------------------------------------------------------------

class TestTransportCostMinimizerChecker:
    def test_demand_unmet_fires(self):
        factories = [{"id": "f1", "name": "工場A", "supply": 100}]
        stores = [{"id": "s1", "name": "店舗X", "demand": 60}]
        flows = [{"factory_id": "f1", "store_id": "s1", "amount": 55}]  # 5不足
        ctxs = build_transport_cost_minimizer_contexts(factories, stores, flows)
        issues = run_issue_rules("TransportCostMinimizer", ctxs, {})
        assert len(issues) == 1
        assert issues[0]["id"] == "demand_unmet_s1"
        assert issues[0]["category"] == "SOLVER"

    def test_demand_met_no_fire(self):
        factories = [{"id": "f1", "name": "工場A", "supply": 100}]
        stores = [{"id": "s1", "name": "店舗X", "demand": 60}]
        flows = [{"factory_id": "f1", "store_id": "s1", "amount": 60}]
        ctxs = build_transport_cost_minimizer_contexts(factories, stores, flows)
        issues = run_issue_rules("TransportCostMinimizer", ctxs, {})
        assert issues == []

    def test_supply_exceeded_fires(self):
        factories = [{"id": "f1", "name": "工場A", "supply": 100}]
        stores = [{"id": "s1", "name": "店舗X", "demand": 105}]
        flows = [{"factory_id": "f1", "store_id": "s1", "amount": 105}]  # 供給上限100を超過
        ctxs = build_transport_cost_minimizer_contexts(factories, stores, flows)
        issues = run_issue_rules("TransportCostMinimizer", ctxs, {})
        assert any(i["id"] == "supply_exceeded_f1" for i in issues)
        assert all(i["category"] == "SOLVER" for i in issues)

    def test_registered_in_issue_rules(self):
        # 2026-08-30以前は"TransportCostMinimizer"がISSUE_RULESに未登録で、
        # demand_unmetチェックが死んでいた（solver.py側のガード節が常にFalse）。
        # この登録漏れの再発を防ぐための回帰テスト。
        assert "TransportCostMinimizer" in ISSUE_RULES


# ---------------------------------------------------------------------------
# テスト: TankAllocationPlanner 解チェッカー（2026-08-30新規、パイロット第1弾）
# ---------------------------------------------------------------------------

class TestTankAllocationPlannerChecker:
    def test_capacity_violation_fires(self):
        tank_assignments = [
            {"tank_id": "t1", "tank_name": "タンク1", "capacity": 100, "total_volume": 120, "lots": []},
        ]
        ctxs = build_tank_allocation_capacity_contexts(tank_assignments)
        issues = run_issue_rules("TankAllocationPlanner", ctxs, {})
        assert len(issues) == 1
        assert issues[0]["id"] == "capacity_violation_t1"
        assert issues[0]["category"] == "SOLVER"

    def test_capacity_ok_no_fire(self):
        tank_assignments = [
            {"tank_id": "t1", "tank_name": "タンク1", "capacity": 100, "total_volume": 80, "lots": []},
        ]
        ctxs = build_tank_allocation_capacity_contexts(tank_assignments)
        issues = run_issue_rules("TankAllocationPlanner", ctxs, {})
        assert issues == []

    def test_incompatible_pair_violation_fires_with_solver_category(self):
        # 旧実装（カタログ化前）は相性違反issueにcategory="SOLVER"が付いて
        # いなかった不整合があった。solver_bug_issue()経由への統一を確認する。
        tank_assignments = [
            {"tank_id": "t1", "tank_name": "タンク1", "lots": [
                {"lot_id": "l1", "category": "酸性"},
                {"lot_id": "l2", "category": "アルカリ性"},
            ]},
        ]
        incompatible_pairs = [("酸性", "アルカリ性")]
        ctxs = build_tank_allocation_incompatibility_contexts(tank_assignments, incompatible_pairs)
        issues = run_issue_rules("TankAllocationPlanner", ctxs, {})
        assert len(issues) == 1
        assert issues[0]["id"] == "incompatible_t1_l1_l2"
        assert issues[0]["category"] == "SOLVER"

    def test_compatible_pair_no_fire(self):
        tank_assignments = [
            {"tank_id": "t1", "tank_name": "タンク1", "lots": [
                {"lot_id": "l1", "category": "酸性"},
                {"lot_id": "l2", "category": "酸性"},
            ]},
        ]
        incompatible_pairs = [("酸性", "アルカリ性")]
        ctxs = build_tank_allocation_incompatibility_contexts(tank_assignments, incompatible_pairs)
        issues = run_issue_rules("TankAllocationPlanner", ctxs, {})
        assert issues == []

    def test_registered_in_issue_rules(self):
        assert "TankAllocationPlanner" in ISSUE_RULES


# ---------------------------------------------------------------------------
# テスト: AuctionWinnerSelector 解チェッカー（2026-08-31新規、バッチ1）
# ---------------------------------------------------------------------------

class TestAuctionWinnerSelectorChecker:
    def test_item_won_by_multiple_bids_fires(self):
        winners = [{"bid_id": "b1", "item_ids": ["i1"]}, {"bid_id": "b2", "item_ids": ["i1"]}]
        ctxs = build_auction_winner_selector_contexts(winners, min_revenue=0, total_revenue=100)
        issues = run_issue_rules("AuctionWinnerSelector", ctxs, {})
        assert len(issues) == 1
        assert issues[0]["id"] == "item_won_by_multiple_bids_i1"
        assert issues[0]["category"] == "SOLVER"

    def test_unique_items_no_fire(self):
        winners = [{"bid_id": "b1", "item_ids": ["i1"]}, {"bid_id": "b2", "item_ids": ["i2"]}]
        ctxs = build_auction_winner_selector_contexts(winners, min_revenue=0, total_revenue=100)
        issues = run_issue_rules("AuctionWinnerSelector", ctxs, {})
        assert issues == []

    def test_min_revenue_violated_fires(self):
        ctxs = build_auction_winner_selector_contexts([], min_revenue=1000, total_revenue=500)
        issues = run_issue_rules("AuctionWinnerSelector", ctxs, {})
        assert len(issues) == 1
        assert issues[0]["id"] == "min_revenue_violated"
        assert issues[0]["category"] == "SOLVER"

    def test_min_revenue_zero_no_fire(self):
        # min_revenue未設定（0）の場合はチェック対象外
        ctxs = build_auction_winner_selector_contexts([], min_revenue=0, total_revenue=0)
        issues = run_issue_rules("AuctionWinnerSelector", ctxs, {})
        assert issues == []

    def test_registered_in_issue_rules(self):
        assert "AuctionWinnerSelector" in ISSUE_RULES


# ---------------------------------------------------------------------------
# テスト: CapitalProjectSelector 解チェッカー（2026-08-31新規、バッチ1）
# ---------------------------------------------------------------------------

class TestCapitalProjectSelectorChecker:
    def test_budget_exceeded_fires(self):
        ctxs = build_capital_project_selector_contexts(
            total_weight=110, budget=100, selected_count=3, max_projects=None,
        )
        issues = run_issue_rules("CapitalProjectSelector", ctxs, {})
        assert len(issues) == 1
        assert issues[0]["id"] == "budget_exceeded"
        assert issues[0]["category"] == "SOLVER"

    def test_within_budget_no_fire(self):
        ctxs = build_capital_project_selector_contexts(
            total_weight=90, budget=100, selected_count=3, max_projects=None,
        )
        issues = run_issue_rules("CapitalProjectSelector", ctxs, {})
        assert issues == []

    def test_max_projects_exceeded_fires(self):
        ctxs = build_capital_project_selector_contexts(
            total_weight=50, budget=100, selected_count=6, max_projects=5,
        )
        issues = run_issue_rules("CapitalProjectSelector", ctxs, {})
        assert len(issues) == 1
        assert issues[0]["id"] == "max_projects_exceeded"

    def test_max_projects_none_no_fire(self):
        # max_projects未設定時は選定件数チェック対象外
        ctxs = build_capital_project_selector_contexts(
            total_weight=50, budget=100, selected_count=999, max_projects=None,
        )
        issues = run_issue_rules("CapitalProjectSelector", ctxs, {})
        assert issues == []

    def test_registered_in_issue_rules(self):
        assert "CapitalProjectSelector" in ISSUE_RULES


# ---------------------------------------------------------------------------
# テスト: InventoryReplenishmentPlanner 解チェッカー（2026-08-31新規、バッチ1）
# ---------------------------------------------------------------------------

class TestInventoryReplenishmentPlannerChecker:
    def test_supply_capacity_exceeded_fires(self):
        centers = [{"id": "c1", "initial_inventory": 0.0}]
        shipments = [
            {"center_id": "c1", "period_id": "p1", "period_index": 0,
             "quantity": 2000.0, "demand": 5.0, "inventory_end": 1995.0},
        ]
        ctxs = build_inventory_replenishment_planner_contexts(shipments, centers, supply_capacity_per_period=1000)
        issues = run_issue_rules("InventoryReplenishmentPlanner", ctxs, {})
        assert any(i["id"] == "supply_capacity_exceeded_p1" for i in issues)
        assert all(i["category"] == "SOLVER" for i in issues)

    def test_within_capacity_no_fire(self):
        centers = [{"id": "c1", "initial_inventory": 10.0}]
        shipments = [
            {"center_id": "c1", "period_id": "p1", "period_index": 0,
             "quantity": 20.0, "demand": 5.0, "inventory_end": 25.0},
        ]
        ctxs = build_inventory_replenishment_planner_contexts(shipments, centers, supply_capacity_per_period=1000)
        issues = run_issue_rules("InventoryReplenishmentPlanner", ctxs, {})
        assert issues == []

    def test_inventory_flow_mismatch_fires(self):
        # 解抽出バグの模擬: inventory_endが「前期末在庫+発送量-需要」と食い違う
        centers = [{"id": "c1", "initial_inventory": 10.0}]
        shipments = [
            {"center_id": "c1", "period_id": "p1", "period_index": 0,
             "quantity": 20.0, "demand": 5.0, "inventory_end": 999.0},
        ]
        ctxs = build_inventory_replenishment_planner_contexts(shipments, centers, supply_capacity_per_period=1000)
        issues = run_issue_rules("InventoryReplenishmentPlanner", ctxs, {})
        assert any(i["id"] == "inventory_flow_mismatch_c1_p1" for i in issues)

    def test_multi_period_chain_no_fire(self):
        # 複数期にまたがる在庫フローが正しく連鎖している場合は発火しない
        centers = [{"id": "c1", "initial_inventory": 10.0}]
        shipments = [
            {"center_id": "c1", "period_id": "p1", "period_index": 0,
             "quantity": 20.0, "demand": 5.0, "inventory_end": 25.0},
            {"center_id": "c1", "period_id": "p2", "period_index": 1,
             "quantity": 0.0, "demand": 10.0, "inventory_end": 15.0},
        ]
        ctxs = build_inventory_replenishment_planner_contexts(shipments, centers, supply_capacity_per_period=1000)
        issues = run_issue_rules("InventoryReplenishmentPlanner", ctxs, {})
        assert issues == []

    def test_registered_in_issue_rules(self):
        assert "InventoryReplenishmentPlanner" in ISSUE_RULES


# ---------------------------------------------------------------------------
# テスト: MysteryShopperScheduler 解チェッカー（2026-08-31新規、バッチ2）
# ---------------------------------------------------------------------------

class TestMysteryShopperSchedulerChecker:
    def test_visit_double_assigned_fires(self):
        assignments = [
            {"visit_id": "v1", "shopper_id": "s1", "day": "2026-09-01"},
            {"visit_id": "v1", "shopper_id": "s2", "day": "2026-09-02"},
        ]
        ctxs = build_mystery_shopper_scheduler_contexts(assignments)
        issues = run_issue_rules("MysteryShopperScheduler", ctxs, {})
        assert any(i["id"] == "visit_double_assigned_v1" for i in issues)
        assert all(i["category"] == "SOLVER" for i in issues)

    def test_shopper_day_double_booked_fires(self):
        assignments = [
            {"visit_id": "v1", "shopper_id": "s1", "day": "2026-09-01"},
            {"visit_id": "v2", "shopper_id": "s1", "day": "2026-09-01"},
        ]
        ctxs = build_mystery_shopper_scheduler_contexts(assignments)
        issues = run_issue_rules("MysteryShopperScheduler", ctxs, {})
        assert any(i["id"] == "shopper_day_double_booked_s1_2026-09-01" for i in issues)

    def test_no_violation_no_fire(self):
        assignments = [
            {"visit_id": "v1", "shopper_id": "s1", "day": "2026-09-01"},
            {"visit_id": "v2", "shopper_id": "s2", "day": "2026-09-01"},
        ]
        ctxs = build_mystery_shopper_scheduler_contexts(assignments)
        issues = run_issue_rules("MysteryShopperScheduler", ctxs, {})
        assert issues == []

    def test_registered_in_issue_rules(self):
        assert "MysteryShopperScheduler" in ISSUE_RULES


# ---------------------------------------------------------------------------
# テスト: NursingWorkloadBalance 解チェッカー（2026-08-31新規、バッチ2）
# ---------------------------------------------------------------------------

class TestNursingWorkloadBalanceChecker:
    def test_patient_count_out_of_range_fires(self):
        nurses = [{"id": "n1", "minPatientsPerNurse": 2, "maxPatientsPerNurse": 5, "maxWorkloadPerNurse": 20}]
        nurse_workloads = [{"nurse_id": "n1", "nurse_name": "Aさん", "patient_count": 1, "total_acuity": 5}]
        ctxs = build_nursing_workload_balance_contexts(nurse_workloads, nurses)
        issues = run_issue_rules("NursingWorkloadBalance", ctxs, {})
        assert any(i["id"] == "nurse_patient_count_out_of_range_n1" for i in issues)
        assert all(i["category"] == "SOLVER" for i in issues)

    def test_workload_exceeded_fires(self):
        nurses = [{"id": "n1", "minPatientsPerNurse": 1, "maxPatientsPerNurse": 5, "maxWorkloadPerNurse": 10}]
        nurse_workloads = [{"nurse_id": "n1", "nurse_name": "Aさん", "patient_count": 3, "total_acuity": 15}]
        ctxs = build_nursing_workload_balance_contexts(nurse_workloads, nurses)
        issues = run_issue_rules("NursingWorkloadBalance", ctxs, {})
        assert any(i["id"] == "nurse_workload_exceeded_n1" for i in issues)

    def test_within_limits_no_fire(self):
        nurses = [{"id": "n1", "minPatientsPerNurse": 1, "maxPatientsPerNurse": 5, "maxWorkloadPerNurse": 20}]
        nurse_workloads = [{"nurse_id": "n1", "nurse_name": "Aさん", "patient_count": 3, "total_acuity": 15}]
        ctxs = build_nursing_workload_balance_contexts(nurse_workloads, nurses)
        issues = run_issue_rules("NursingWorkloadBalance", ctxs, {})
        assert issues == []

    def test_registered_in_issue_rules(self):
        assert "NursingWorkloadBalance" in ISSUE_RULES


# ---------------------------------------------------------------------------
# テスト: PortfolioOverlapDesigner 解チェッカー（2026-08-31新規、バッチ2）
# ---------------------------------------------------------------------------

class TestPortfolioOverlapDesignerChecker:
    def test_selected_count_mismatch_fires(self):
        funds = [{"id": "f1", "name": "Fund1", "required_count": 3}]
        assignments = {"f1": ["s1", "s2"]}
        overlap_matrix = []
        ctxs = build_portfolio_overlap_designer_contexts(funds, assignments, overlap_matrix, worst_overlap=0)
        issues = run_issue_rules("PortfolioOverlapDesigner", ctxs, {})
        assert any(i["id"] == "selected_count_mismatch_f1" for i in issues)
        assert all(i["category"] == "SOLVER" for i in issues)

    def test_overlap_kpi_mismatch_fires(self):
        funds = [{"id": "f1", "name": "Fund1", "required_count": 2}]
        assignments = {"f1": ["s1", "s2"]}
        overlap_matrix = [{"fund_id_a": "f1", "fund_id_b": "f2", "overlap_count": 2}]
        ctxs = build_portfolio_overlap_designer_contexts(funds, assignments, overlap_matrix, worst_overlap=0)
        issues = run_issue_rules("PortfolioOverlapDesigner", ctxs, {})
        assert any(i["id"] == "overlap_matrix_worst_overlap_mismatch" for i in issues)

    def test_no_violation_no_fire(self):
        funds = [{"id": "f1", "name": "Fund1", "required_count": 2}]
        assignments = {"f1": ["s1", "s2"]}
        overlap_matrix = [{"fund_id_a": "f1", "fund_id_b": "f2", "overlap_count": 1}]
        ctxs = build_portfolio_overlap_designer_contexts(funds, assignments, overlap_matrix, worst_overlap=1)
        issues = run_issue_rules("PortfolioOverlapDesigner", ctxs, {})
        assert issues == []

    def test_registered_in_issue_rules(self):
        assert "PortfolioOverlapDesigner" in ISSUE_RULES
# ---------------------------------------------------------------------------
# テスト: CarSequencing 解チェッカー（2026-09-01新規、バッチ3）
# ---------------------------------------------------------------------------

class TestCarSequencingChecker:
    def test_count_mismatch_fires(self):
        sequence_result = [{"car_type_id": "ct1"}, {"car_type_id": "ct1"}]
        car_types = [{"car_type_id": "ct1", "car_type_name": "SedanA", "count": 3}]
        ctxs = build_car_sequencing_contexts(sequence_result, car_types)
        issues = run_issue_rules("CarSequencing", ctxs, {})
        assert any(i["id"] == "car_type_count_mismatch_ct1" for i in issues)
        assert all(i["category"] == "SOLVER" for i in issues)

    def test_count_match_no_fire(self):
        sequence_result = [{"car_type_id": "ct1"}] * 3
        car_types = [{"car_type_id": "ct1", "car_type_name": "SedanA", "count": 3}]
        ctxs = build_car_sequencing_contexts(sequence_result, car_types)
        issues = run_issue_rules("CarSequencing", ctxs, {})
        assert issues == []

    def test_registered_in_issue_rules(self):
        assert "CarSequencing" in ISSUE_RULES


# ---------------------------------------------------------------------------
# テスト: EnergyCostAwareScheduler 解チェッカー（2026-09-01新規、バッチ3）
# ---------------------------------------------------------------------------

class TestEnergyCostAwareSchedulerChecker:
    def test_line_power_capacity_violation_fires(self):
        lines = [{"id": "l1", "name": "Line1", "max_power_kw": 5,
                   "max_workers": 9999, "max_equipment_slots": 9999, "standby_power_kw": 0}]
        orders = [
            {"id": "o1", "power_kw": 3, "workers": 0, "equipment_slots": 0},
            {"id": "o2", "power_kw": 4, "workers": 0, "equipment_slots": 0},
        ]
        schedule = [
            {"order_id": "o1", "line_id": "l1", "start": 0, "end": 10},
            {"order_id": "o2", "line_id": "l1", "start": 5, "end": 15},
        ]
        line_ops = [{"line_id": "l1", "start": 0, "end": 15}]
        ctxs = build_energy_cost_aware_scheduler_contexts(schedule, line_ops, orders, lines)
        issues = run_issue_rules("EnergyCostAwareScheduler", ctxs, {})
        assert any(i["id"] == "line_capacity_violation_power_l1" for i in issues)
        assert all(i["category"] == "SOLVER" for i in issues)

    def test_line_power_capacity_ok_no_fire(self):
        lines = [{"id": "l1", "name": "Line1", "max_power_kw": 10,
                   "max_workers": 9999, "max_equipment_slots": 9999, "standby_power_kw": 0}]
        orders = [
            {"id": "o1", "power_kw": 3, "workers": 0, "equipment_slots": 0},
            {"id": "o2", "power_kw": 4, "workers": 0, "equipment_slots": 0},
        ]
        schedule = [
            {"order_id": "o1", "line_id": "l1", "start": 0, "end": 10},
            {"order_id": "o2", "line_id": "l1", "start": 5, "end": 15},
        ]
        line_ops = [{"line_id": "l1", "start": 0, "end": 15}]
        ctxs = build_energy_cost_aware_scheduler_contexts(schedule, line_ops, orders, lines)
        issues = run_issue_rules("EnergyCostAwareScheduler", ctxs, {})
        assert issues == []

    def test_order_outside_line_window_fires(self):
        lines = [{"id": "l1", "name": "Line1", "max_power_kw": 9999,
                   "max_workers": 9999, "max_equipment_slots": 9999, "standby_power_kw": 0}]
        orders = [{"id": "o1", "power_kw": 0, "workers": 0, "equipment_slots": 0}]
        schedule = [{"order_id": "o1", "line_id": "l1", "start": 0, "end": 5}]
        line_ops = [{"line_id": "l1", "start": 2, "end": 10}]
        ctxs = build_energy_cost_aware_scheduler_contexts(schedule, line_ops, orders, lines)
        issues = run_issue_rules("EnergyCostAwareScheduler", ctxs, {})
        assert any(i["id"] == "order_outside_line_window_o1" for i in issues)

    def test_order_duplicate_assignment_fires(self):
        lines = [
            {"id": "l1", "name": "Line1", "max_power_kw": 9999,
             "max_workers": 9999, "max_equipment_slots": 9999, "standby_power_kw": 0},
            {"id": "l2", "name": "Line2", "max_power_kw": 9999,
             "max_workers": 9999, "max_equipment_slots": 9999, "standby_power_kw": 0},
        ]
        orders = [{"id": "o1", "power_kw": 0, "workers": 0, "equipment_slots": 0}]
        schedule = [
            {"order_id": "o1", "line_id": "l1", "start": 0, "end": 5},
            {"order_id": "o1", "line_id": "l2", "start": 0, "end": 5},
        ]
        line_ops = [
            {"line_id": "l1", "start": 0, "end": 5},
            {"line_id": "l2", "start": 0, "end": 5},
        ]
        ctxs = build_energy_cost_aware_scheduler_contexts(schedule, line_ops, orders, lines)
        issues = run_issue_rules("EnergyCostAwareScheduler", ctxs, {})
        assert any(i["id"] == "order_duplicate_assignment_o1" for i in issues)

    def test_registered_in_issue_rules(self):
        assert "EnergyCostAwareScheduler" in ISSUE_RULES


# ---------------------------------------------------------------------------
# テスト: LotSizingScheduler 解チェッカー（2026-09-01新規、バッチ3）
# ---------------------------------------------------------------------------

class TestLotSizingSchedulerChecker:
    def test_period_double_assigned_fires(self):
        assignments = [
            {"order_id": "o1", "assigned_period": 2},
            {"order_id": "o2", "assigned_period": 2},
        ]
        ctxs = build_lot_sizing_scheduler_contexts(assignments)
        issues = run_issue_rules("LotSizingScheduler", ctxs, {})
        assert any(i["id"] == "period_double_assigned_2" for i in issues)
        assert all(i["category"] == "SOLVER" for i in issues)

    def test_no_violation_no_fire(self):
        assignments = [
            {"order_id": "o1", "assigned_period": 1},
            {"order_id": "o2", "assigned_period": 2},
        ]
        ctxs = build_lot_sizing_scheduler_contexts(assignments)
        issues = run_issue_rules("LotSizingScheduler", ctxs, {})
        assert issues == []

    def test_registered_in_issue_rules(self):
        assert "LotSizingScheduler" in ISSUE_RULES



# ---------------------------------------------------------------------------
# テスト: MedicalAppointmentScheduler 解チェッカー（2026-09-01新規、バッチ4）
# ---------------------------------------------------------------------------

class TestMedicalAppointmentSchedulerChecker:
    def test_resource_double_booking_fires(self):
        assignments = [
            {"request_id": "r1", "day": "2026-09-01", "start_min": 0, "end_min": 60,
             "assigned_resources": [{"resource_id": "res1", "resource_type": "doctor"}]},
            {"request_id": "r2", "day": "2026-09-01", "start_min": 30, "end_min": 90,
             "assigned_resources": [{"resource_id": "res1", "resource_type": "doctor"}]},
        ]
        requests = [
            {"id": "r1", "resource_type_needs": ["doctor"], "avoid_days": []},
            {"id": "r2", "resource_type_needs": ["doctor"], "avoid_days": []},
        ]
        resources = [{"id": "res1", "name": "DrA", "resource_type": "doctor"}]
        ctxs = build_medical_appointment_scheduler_contexts(assignments, requests, resources)
        issues = run_issue_rules("MedicalAppointmentScheduler", ctxs, {})
        assert any(i["id"] == "resource_double_booking_res1_2026-09-01" for i in issues)
        assert all(i["category"] == "SOLVER" for i in issues)

    def test_resource_double_booking_no_fire(self):
        assignments = [
            {"request_id": "r1", "day": "2026-09-01", "start_min": 0, "end_min": 60,
             "assigned_resources": [{"resource_id": "res1", "resource_type": "doctor"}]},
            {"request_id": "r2", "day": "2026-09-01", "start_min": 60, "end_min": 120,
             "assigned_resources": [{"resource_id": "res1", "resource_type": "doctor"}]},
        ]
        requests = [
            {"id": "r1", "resource_type_needs": ["doctor"], "avoid_days": []},
            {"id": "r2", "resource_type_needs": ["doctor"], "avoid_days": []},
        ]
        resources = [{"id": "res1", "name": "DrA", "resource_type": "doctor"}]
        ctxs = build_medical_appointment_scheduler_contexts(assignments, requests, resources)
        issues = run_issue_rules("MedicalAppointmentScheduler", ctxs, {})
        assert issues == []

    def test_resource_type_mismatch_fires(self):
        assignments = [
            {"request_id": "r1", "day": "2026-09-01", "start_min": 0, "end_min": 10,
             "assigned_resources": [{"resource_id": "res1", "resource_type": "nurse"}]},
        ]
        requests = [{"id": "r1", "resource_type_needs": ["doctor"], "avoid_days": []}]
        resources = [{"id": "res1", "name": "N", "resource_type": "nurse"}]
        ctxs = build_medical_appointment_scheduler_contexts(assignments, requests, resources)
        issues = run_issue_rules("MedicalAppointmentScheduler", ctxs, {})
        assert any(i["id"] == "resource_type_mismatch_r1" for i in issues)

    def test_resource_type_match_no_fire(self):
        assignments = [
            {"request_id": "r1", "day": "2026-09-01", "start_min": 0, "end_min": 10,
             "assigned_resources": [{"resource_id": "res1", "resource_type": "doctor"}]},
        ]
        requests = [{"id": "r1", "resource_type_needs": ["doctor"], "avoid_days": []}]
        resources = [{"id": "res1", "name": "DrA", "resource_type": "doctor"}]
        ctxs = build_medical_appointment_scheduler_contexts(assignments, requests, resources)
        issues = run_issue_rules("MedicalAppointmentScheduler", ctxs, {})
        assert issues == []

    def test_avoid_day_violation_fires(self):
        assignments = [
            {"request_id": "r1", "day": "2026-09-01", "start_min": 0, "end_min": 10,
             "assigned_resources": []},
        ]
        requests = [{"id": "r1", "resource_type_needs": [], "avoid_days": ["2026-09-01"]}]
        resources = []
        ctxs = build_medical_appointment_scheduler_contexts(assignments, requests, resources)
        issues = run_issue_rules("MedicalAppointmentScheduler", ctxs, {})
        assert any(i["id"] == "avoid_day_violation_r1" for i in issues)

    def test_avoid_day_ok_no_fire(self):
        assignments = [
            {"request_id": "r1", "day": "2026-09-01", "start_min": 0, "end_min": 10,
             "assigned_resources": []},
        ]
        requests = [{"id": "r1", "resource_type_needs": [], "avoid_days": ["2026-09-02"]}]
        resources = []
        ctxs = build_medical_appointment_scheduler_contexts(assignments, requests, resources)
        issues = run_issue_rules("MedicalAppointmentScheduler", ctxs, {})
        assert issues == []

    def test_registered_in_issue_rules(self):
        assert "MedicalAppointmentScheduler" in ISSUE_RULES


# ---------------------------------------------------------------------------
# テスト: MedicalAppointmentSequenceScheduler 解チェッカー（2026-09-01新規、バッチ4）
# ---------------------------------------------------------------------------

class TestMedicalAppointmentSequenceSchedulerChecker:
    def test_resource_double_booking_fires(self):
        scheduled = [{"sequence_id": "s1", "visits": [
            {"visit_id": "v1", "visit_order": 0, "duration_min": 30, "start_min": 0, "end_min": 30,
             "assigned_resources": [{"resource_id": "res1", "resource_type": "doctor", "start_min": 0, "end_min": 30}]},
            {"visit_id": "v2", "visit_order": 1, "duration_min": 30, "start_min": 15, "end_min": 45,
             "assigned_resources": [{"resource_id": "res1", "resource_type": "doctor", "start_min": 15, "end_min": 45}]},
        ]}]
        sequences = [{"id": "s1", "visits": [
            {"id": "v1", "order": 0, "required_resources": [{"type": "doctor", "count": 1}]},
            {"id": "v2", "order": 1, "required_resources": [{"type": "doctor", "count": 1}]},
        ], "interval_rules": [], "same_resource_rules": []}]
        resources = [{"id": "res1", "name": "DrA", "type": "doctor",
                       "available_slots": [{"start_min": 0, "end_min": 1000}]}]
        ctxs = build_medical_appointment_sequence_scheduler_contexts(scheduled, sequences, resources)
        issues = run_issue_rules("MedicalAppointmentSequenceScheduler", ctxs, {})
        assert any(i["id"] == "resource_double_booking_res1" for i in issues)
        assert all(i["category"] == "SOLVER" for i in issues)

    def test_visit_outside_resource_window_fires(self):
        scheduled = [{"sequence_id": "s1", "visits": [
            {"visit_id": "v1", "visit_order": 0, "duration_min": 30, "start_min": 0, "end_min": 30,
             "assigned_resources": [{"resource_id": "res1", "resource_type": "doctor", "start_min": 0, "end_min": 30}]},
        ]}]
        sequences = [{"id": "s1", "visits": [
            {"id": "v1", "order": 0, "required_resources": [{"type": "doctor", "count": 1}]},
        ], "interval_rules": [], "same_resource_rules": []}]
        resources = [{"id": "res1", "name": "DrA", "type": "doctor",
                       "available_slots": [{"start_min": 100, "end_min": 200}]}]
        ctxs = build_medical_appointment_sequence_scheduler_contexts(scheduled, sequences, resources)
        issues = run_issue_rules("MedicalAppointmentSequenceScheduler", ctxs, {})
        assert any(i["id"] == "visit_outside_resource_window_v1_res1" for i in issues)

    def test_sequence_gap_violation_fires(self):
        scheduled = [{"sequence_id": "s1", "visits": [
            {"visit_id": "v1", "visit_order": 0, "duration_min": 30, "start_min": 0, "end_min": 30,
             "assigned_resources": [{"resource_id": "res1", "resource_type": "doctor", "start_min": 0, "end_min": 30}]},
            {"visit_id": "v2", "visit_order": 1, "duration_min": 30, "start_min": 20, "end_min": 50,
             "assigned_resources": [{"resource_id": "res2", "resource_type": "nurse", "start_min": 20, "end_min": 50}]},
        ]}]
        sequences = [{"id": "s1", "visits": [
            {"id": "v1", "order": 0, "required_resources": [{"type": "doctor", "count": 1}]},
            {"id": "v2", "order": 1, "required_resources": [{"type": "nurse", "count": 1}]},
        ], "interval_rules": [], "same_resource_rules": []}]
        resources = [
            {"id": "res1", "name": "DrA", "type": "doctor", "available_slots": [{"start_min": 0, "end_min": 1000}]},
            {"id": "res2", "name": "NsB", "type": "nurse", "available_slots": [{"start_min": 0, "end_min": 1000}]},
        ]
        ctxs = build_medical_appointment_sequence_scheduler_contexts(scheduled, sequences, resources)
        issues = run_issue_rules("MedicalAppointmentSequenceScheduler", ctxs, {})
        assert any(i["id"] == "sequence_gap_violation_s1_v1_v2" for i in issues)

    def test_same_resource_rule_violation_fires(self):
        scheduled = [{"sequence_id": "s1", "visits": [
            {"visit_id": "va", "visit_order": 0, "duration_min": 30, "start_min": 0, "end_min": 30,
             "assigned_resources": [{"resource_id": "res1", "resource_type": "doctor", "start_min": 0, "end_min": 30}]},
            {"visit_id": "vb", "visit_order": 1, "duration_min": 30, "start_min": 30, "end_min": 60,
             "assigned_resources": [{"resource_id": "res2", "resource_type": "doctor", "start_min": 30, "end_min": 60}]},
        ]}]
        sequences = [{"id": "s1", "visits": [
            {"id": "va", "order": 0, "required_resources": [{"type": "doctor", "count": 1}]},
            {"id": "vb", "order": 1, "required_resources": [{"type": "doctor", "count": 1}]},
        ], "interval_rules": [], "same_resource_rules": [{"visit_a": 0, "visit_b": 1}]}]
        resources = [
            {"id": "res1", "name": "Dr1", "type": "doctor", "available_slots": [{"start_min": 0, "end_min": 1000}]},
            {"id": "res2", "name": "Dr2", "type": "doctor", "available_slots": [{"start_min": 0, "end_min": 1000}]},
        ]
        ctxs = build_medical_appointment_sequence_scheduler_contexts(scheduled, sequences, resources)
        issues = run_issue_rules("MedicalAppointmentSequenceScheduler", ctxs, {})
        assert any(i["id"] == "same_resource_rule_violation_s1_va_vb" for i in issues)

    def test_same_resource_rule_ok_no_fire(self):
        scheduled = [{"sequence_id": "s1", "visits": [
            {"visit_id": "va", "visit_order": 0, "duration_min": 30, "start_min": 0, "end_min": 30,
             "assigned_resources": [{"resource_id": "res1", "resource_type": "doctor", "start_min": 0, "end_min": 30}]},
            {"visit_id": "vb", "visit_order": 1, "duration_min": 30, "start_min": 30, "end_min": 60,
             "assigned_resources": [{"resource_id": "res1", "resource_type": "doctor", "start_min": 30, "end_min": 60}]},
        ]}]
        sequences = [{"id": "s1", "visits": [
            {"id": "va", "order": 0, "required_resources": [{"type": "doctor", "count": 1}]},
            {"id": "vb", "order": 1, "required_resources": [{"type": "doctor", "count": 1}]},
        ], "interval_rules": [], "same_resource_rules": [{"visit_a": 0, "visit_b": 1}]}]
        resources = [{"id": "res1", "name": "Dr1", "type": "doctor", "available_slots": [{"start_min": 0, "end_min": 1000}]}]
        ctxs = build_medical_appointment_sequence_scheduler_contexts(scheduled, sequences, resources)
        issues = run_issue_rules("MedicalAppointmentSequenceScheduler", ctxs, {})
        assert issues == []

    def test_visit_resource_time_mismatch_fires(self):
        scheduled = [{"sequence_id": "s1", "visits": [
            {"visit_id": "v1", "visit_order": 0, "duration_min": 30, "start_min": 0, "end_min": 35,
             "assigned_resources": [
                 {"resource_id": "res1", "resource_type": "doctor", "start_min": 0, "end_min": 30},
                 {"resource_id": "res2", "resource_type": "nurse", "start_min": 5, "end_min": 35},
             ]},
        ]}]
        sequences = [{"id": "s1", "visits": [
            {"id": "v1", "order": 0, "required_resources": [{"type": "doctor", "count": 1}, {"type": "nurse", "count": 1}]},
        ], "interval_rules": [], "same_resource_rules": []}]
        resources = [
            {"id": "res1", "name": "Dr", "type": "doctor", "available_slots": [{"start_min": 0, "end_min": 1000}]},
            {"id": "res2", "name": "Ns", "type": "nurse", "available_slots": [{"start_min": 0, "end_min": 1000}]},
        ]
        ctxs = build_medical_appointment_sequence_scheduler_contexts(scheduled, sequences, resources)
        issues = run_issue_rules("MedicalAppointmentSequenceScheduler", ctxs, {})
        assert any(i["id"] == "visit_resource_time_mismatch_v1" for i in issues)

    def test_visit_resource_time_match_no_fire(self):
        scheduled = [{"sequence_id": "s1", "visits": [
            {"visit_id": "v1", "visit_order": 0, "duration_min": 30, "start_min": 0, "end_min": 30,
             "assigned_resources": [
                 {"resource_id": "res1", "resource_type": "doctor", "start_min": 0, "end_min": 30},
                 {"resource_id": "res2", "resource_type": "nurse", "start_min": 0, "end_min": 30},
             ]},
        ]}]
        sequences = [{"id": "s1", "visits": [
            {"id": "v1", "order": 0, "required_resources": [{"type": "doctor", "count": 1}, {"type": "nurse", "count": 1}]},
        ], "interval_rules": [], "same_resource_rules": []}]
        resources = [
            {"id": "res1", "name": "Dr", "type": "doctor", "available_slots": [{"start_min": 0, "end_min": 1000}]},
            {"id": "res2", "name": "Ns", "type": "nurse", "available_slots": [{"start_min": 0, "end_min": 1000}]},
        ]
        ctxs = build_medical_appointment_sequence_scheduler_contexts(scheduled, sequences, resources)
        issues = run_issue_rules("MedicalAppointmentSequenceScheduler", ctxs, {})
        assert issues == []

    def test_all_clean_no_fire(self):
        sequences = [{"id": "s1", "visits": [
            {"id": "v1", "order": 0, "required_resources": [{"type": "doctor", "count": 1}]},
            {"id": "v2", "order": 1, "required_resources": [{"type": "doctor", "count": 1}]},
        ], "interval_rules": [], "same_resource_rules": [{"visit_a": 0, "visit_b": 1}]}]
        scheduled = [{"sequence_id": "s1", "visits": [
            {"visit_id": "v1", "visit_order": 0, "duration_min": 30, "start_min": 0, "end_min": 30,
             "assigned_resources": [{"resource_id": "res1", "resource_type": "doctor", "start_min": 0, "end_min": 30}]},
            {"visit_id": "v2", "visit_order": 1, "duration_min": 30, "start_min": 30, "end_min": 60,
             "assigned_resources": [{"resource_id": "res1", "resource_type": "doctor", "start_min": 30, "end_min": 60}]},
        ]}]
        resources = [{"id": "res1", "name": "Dr1", "type": "doctor", "available_slots": [{"start_min": 0, "end_min": 1000}]}]
        ctxs = build_medical_appointment_sequence_scheduler_contexts(scheduled, sequences, resources)
        issues = run_issue_rules("MedicalAppointmentSequenceScheduler", ctxs, {})
        assert issues == []

    def test_registered_in_issue_rules(self):
        assert "MedicalAppointmentSequenceScheduler" in ISSUE_RULES


# ---------------------------------------------------------------------------
# テスト: PatientTransportPlanner 解チェッカー（2026-09-01新規、バッチ4）
# ---------------------------------------------------------------------------

class TestPatientTransportPlannerChecker:
    def test_vehicle_capacity_violation_fires(self):
        requests = [{"id": "r1", "capacity_required": 3}, {"id": "r2", "capacity_required": 3}]
        vehicles = [{"id": "v1", "name": "Van1", "capacity": 4}]
        request_results = [
            {"request_id": "r1", "req_type": "outbound_only", "care_type": "c", "served": True,
             "outbound_vehicle": "v1", "outbound_start": 0, "outbound_end": 30},
            {"request_id": "r2", "req_type": "outbound_only", "care_type": "c", "served": True,
             "outbound_vehicle": "v1", "outbound_start": 10, "outbound_end": 40},
        ]
        ctxs = build_patient_transport_planner_contexts(request_results, requests, vehicles, {})
        issues = run_issue_rules("PatientTransportPlanner", ctxs, {})
        assert any(i["id"] == "vehicle_capacity_violation_v1" for i in issues)
        assert all(i["category"] == "SOLVER" for i in issues)

    def test_vehicle_capacity_ok_no_fire(self):
        requests = [{"id": "r1", "capacity_required": 3}, {"id": "r2", "capacity_required": 3}]
        vehicles = [{"id": "v1", "name": "Van1", "capacity": 4}]
        request_results = [
            {"request_id": "r1", "req_type": "outbound_only", "care_type": "c", "served": True,
             "outbound_vehicle": "v1", "outbound_start": 0, "outbound_end": 30},
            {"request_id": "r2", "req_type": "outbound_only", "care_type": "c", "served": True,
             "outbound_vehicle": "v1", "outbound_start": 40, "outbound_end": 70},
        ]
        ctxs = build_patient_transport_planner_contexts(request_results, requests, vehicles, {})
        issues = [i for i in run_issue_rules("PatientTransportPlanner", ctxs, {})
                  if i["id"] == "vehicle_capacity_violation_v1"]
        assert issues == []

    def test_vehicle_phase_overlap_fires(self):
        requests = [{"id": "r1", "capacity_required": 1}, {"id": "r2", "capacity_required": 1}]
        vehicles = [{"id": "v1", "name": "Van1", "capacity": 4}]
        request_results = [
            {"request_id": "r1", "req_type": "outbound_only", "care_type": "c", "served": True,
             "outbound_vehicle": "v1", "outbound_start": 0, "outbound_end": 30},
            {"request_id": "r2", "req_type": "outbound_only", "care_type": "c", "served": True,
             "outbound_vehicle": "v1", "outbound_start": 10, "outbound_end": 40},
        ]
        ctxs = build_patient_transport_planner_contexts(request_results, requests, vehicles, {})
        issues = run_issue_rules("PatientTransportPlanner", ctxs, {})
        assert any(i["id"] == "vehicle_phase_overlap_v1" for i in issues)

    def test_roundtrip_order_violation_fires(self):
        requests = [{"id": "r1", "exam_duration_min": 60}]
        vehicles = [{"id": "v1", "name": "Van1", "capacity": 10}]
        request_results = [
            {"request_id": "r1", "req_type": "roundtrip", "care_type": "c", "served": True,
             "outbound_vehicle": "v1", "outbound_start": 0, "outbound_end": 30,
             "inbound_vehicle": "v1", "inbound_start": 50, "inbound_end": 80},
        ]
        ctxs = build_patient_transport_planner_contexts(request_results, requests, vehicles, {})
        issues = run_issue_rules("PatientTransportPlanner", ctxs, {})
        assert any(i["id"] == "roundtrip_order_violation_r1" for i in issues)

    def test_roundtrip_order_ok_no_fire(self):
        requests = [{"id": "r1", "exam_duration_min": 60, "capacity_required": 1}]
        vehicles = [{"id": "v1", "name": "Van1", "capacity": 10}]
        request_results = [
            {"request_id": "r1", "req_type": "roundtrip", "care_type": "c", "served": True,
             "outbound_vehicle": "v1", "outbound_start": 0, "outbound_end": 30,
             "inbound_vehicle": "v1", "inbound_start": 90, "inbound_end": 120},
        ]
        ctxs = build_patient_transport_planner_contexts(request_results, requests, vehicles, {"boarding_time_min": 0})
        issues = [i for i in run_issue_rules("PatientTransportPlanner", ctxs, {})
                  if i["id"] == "roundtrip_order_violation_r1"]
        assert issues == []

    def test_phase_outside_time_window_fires(self):
        config = {"speed_kmh": 30.0, "boarding_time_min": 3, "hospital_location": {"lat": 0.0, "lng": 0.0}}
        requests = [{
            "id": "r1", "home_location": {"lat": 0.0, "lng": 0.0}, "appointment_time": "09:00",
            "exam_duration_min": 60, "wait_tolerance_min": 30, "boarding_time_min": 3,
            "capacity_required": 1, "care_type": "c", "request_type": "outbound_only",
        }]
        vehicles = [{"id": "v1", "name": "Van1", "capacity": 10}]
        request_results = [
            {"request_id": "r1", "req_type": "outbound_only", "care_type": "c", "served": True,
             "outbound_vehicle": "v1", "outbound_start": 100, "outbound_end": 130},
        ]
        ctxs = build_patient_transport_planner_contexts(request_results, requests, vehicles, config)
        issues = run_issue_rules("PatientTransportPlanner", ctxs, {})
        assert any(i["id"] == "phase_outside_time_window_r1_outbound" for i in issues)

    def test_phase_inside_time_window_no_fire(self):
        config = {"speed_kmh": 30.0, "boarding_time_min": 3, "hospital_location": {"lat": 0.0, "lng": 0.0}}
        requests = [{
            "id": "r1", "home_location": {"lat": 0.0, "lng": 0.0}, "appointment_time": "09:00",
            "exam_duration_min": 60, "wait_tolerance_min": 30, "boarding_time_min": 3,
            "capacity_required": 1, "care_type": "c", "request_type": "outbound_only",
        }]
        vehicles = [{"id": "v1", "name": "Van1", "capacity": 10}]
        # appointment_time 09:00 = 540分、travel=1分、boarding=3分
        # tw_start = max(0, 540-30-1-3) = 506, tw_end = max(507, 540-1-3) = 536
        request_results = [
            {"request_id": "r1", "req_type": "outbound_only", "care_type": "c", "served": True,
             "outbound_vehicle": "v1", "outbound_start": 520, "outbound_end": 536},
        ]
        ctxs = build_patient_transport_planner_contexts(request_results, requests, vehicles, config)
        issues = [i for i in run_issue_rules("PatientTransportPlanner", ctxs, {})
                  if i["id"] == "phase_outside_time_window_r1_outbound"]
        assert issues == []

    def test_registered_in_issue_rules(self):
        assert "PatientTransportPlanner" in ISSUE_RULES



# ---------------------------------------------------------------------------
# テスト: ProductionLineSequencing 解チェッカー（2026-09-01新規、バッチ5）
# ---------------------------------------------------------------------------

class TestProductionLineSequencingChecker:
    def test_slot_double_booked_fires(self):
        assignments = [
            {"batch_id": "b1", "slot_id": "s1", "line_id": "L1", "day": 1, "start_hour": 8.0, "vehicle_type": "vt1"},
            {"batch_id": "b2", "slot_id": "s1", "line_id": "L1", "day": 1, "start_hour": 9.0, "vehicle_type": "vt1"},
        ]
        batches = [
            {"id": "b1", "compatible_lines": None, "line_on_day": 1, "line_off_day": 10, "vehicle_type": "vt1"},
            {"id": "b2", "compatible_lines": None, "line_on_day": 1, "line_off_day": 10, "vehicle_type": "vt1"},
        ]
        slots = [{"id": "s1", "line_id": "L1", "day": 1, "start_hour": 8.0}]
        vehicle_types = [{"id": "vt1", "daily_limit": 99, "priority_order": 1}]
        ctxs = build_production_line_sequencing_contexts(assignments, batches, slots, vehicle_types, [])
        issues = run_issue_rules("ProductionLineSequencing", ctxs, {})
        assert any(i["id"] == "slot_double_booked_s1" for i in issues)
        assert all(i["category"] == "SOLVER" for i in issues)

    def test_slot_double_booked_no_fire(self):
        assignments = [
            {"batch_id": "b1", "slot_id": "s1", "line_id": "L1", "day": 1, "start_hour": 8.0, "vehicle_type": "vt1"},
            {"batch_id": "b2", "slot_id": "s2", "line_id": "L1", "day": 1, "start_hour": 9.0, "vehicle_type": "vt1"},
        ]
        batches = [
            {"id": "b1", "compatible_lines": None, "line_on_day": 1, "line_off_day": 10, "vehicle_type": "vt1"},
            {"id": "b2", "compatible_lines": None, "line_on_day": 1, "line_off_day": 10, "vehicle_type": "vt1"},
        ]
        slots = [
            {"id": "s1", "line_id": "L1", "day": 1, "start_hour": 8.0},
            {"id": "s2", "line_id": "L1", "day": 1, "start_hour": 9.0},
        ]
        vehicle_types = [{"id": "vt1", "daily_limit": 99, "priority_order": 1}]
        ctxs = build_production_line_sequencing_contexts(assignments, batches, slots, vehicle_types, [])
        issues = run_issue_rules("ProductionLineSequencing", ctxs, {})
        assert issues == []

    def test_batch_incompatible_line_fires(self):
        assignments = [{"batch_id": "b1", "slot_id": "s1", "line_id": "L2", "day": 1, "start_hour": 8.0, "vehicle_type": "vt1"}]
        batches = [{"id": "b1", "compatible_lines": ["L1"], "line_on_day": 1, "line_off_day": 10, "vehicle_type": "vt1"}]
        slots = [{"id": "s1", "line_id": "L2", "day": 1, "start_hour": 8.0}]
        vehicle_types = [{"id": "vt1", "daily_limit": 99, "priority_order": 1}]
        ctxs = build_production_line_sequencing_contexts(assignments, batches, slots, vehicle_types, [])
        issues = run_issue_rules("ProductionLineSequencing", ctxs, {})
        assert any(i["id"] == "batch_incompatible_line_b1" for i in issues)

    def test_batch_compatible_line_no_fire(self):
        assignments = [{"batch_id": "b1", "slot_id": "s1", "line_id": "L2", "day": 1, "start_hour": 8.0, "vehicle_type": "vt1"}]
        batches = [{"id": "b1", "compatible_lines": ["L1", "L2"], "line_on_day": 1, "line_off_day": 10, "vehicle_type": "vt1"}]
        slots = [{"id": "s1", "line_id": "L2", "day": 1, "start_hour": 8.0}]
        vehicle_types = [{"id": "vt1", "daily_limit": 99, "priority_order": 1}]
        ctxs = build_production_line_sequencing_contexts(assignments, batches, slots, vehicle_types, [])
        issues = [i for i in run_issue_rules("ProductionLineSequencing", ctxs, {}) if i["id"] == "batch_incompatible_line_b1"]
        assert issues == []

    def test_batch_outside_line_window_fires(self):
        assignments = [{"batch_id": "b1", "slot_id": "s1", "line_id": "L1", "day": 15, "start_hour": 8.0, "vehicle_type": "vt1"}]
        batches = [{"id": "b1", "compatible_lines": None, "line_on_day": 1, "line_off_day": 10, "vehicle_type": "vt1"}]
        slots = [{"id": "s1", "line_id": "L1", "day": 15, "start_hour": 8.0}]
        vehicle_types = [{"id": "vt1", "daily_limit": 99, "priority_order": 1}]
        ctxs = build_production_line_sequencing_contexts(assignments, batches, slots, vehicle_types, [])
        issues = run_issue_rules("ProductionLineSequencing", ctxs, {})
        assert any(i["id"] == "batch_outside_line_window_b1" for i in issues)

    def test_batch_inside_line_window_no_fire(self):
        assignments = [{"batch_id": "b1", "slot_id": "s1", "line_id": "L1", "day": 5, "start_hour": 8.0, "vehicle_type": "vt1"}]
        batches = [{"id": "b1", "compatible_lines": None, "line_on_day": 1, "line_off_day": 10, "vehicle_type": "vt1"}]
        slots = [{"id": "s1", "line_id": "L1", "day": 5, "start_hour": 8.0}]
        vehicle_types = [{"id": "vt1", "daily_limit": 99, "priority_order": 1}]
        ctxs = build_production_line_sequencing_contexts(assignments, batches, slots, vehicle_types, [])
        issues = [i for i in run_issue_rules("ProductionLineSequencing", ctxs, {}) if i["id"] == "batch_outside_line_window_b1"]
        assert issues == []

    def test_distribution_daily_max_violation_fires(self):
        assignments = [
            {"batch_id": f"b{i}", "slot_id": f"s{i}", "line_id": "L1", "day": 1, "start_hour": 8.0 + i, "vehicle_type": "vt1"}
            for i in range(3)
        ]
        batches = [
            {"id": f"b{i}", "compatible_lines": None, "line_on_day": 1, "line_off_day": 10, "vehicle_type": "vt1"}
            for i in range(3)
        ]
        slots = [{"id": f"s{i}", "line_id": "L1", "day": 1, "start_hour": 8.0 + i} for i in range(3)]
        vehicle_types = [{"id": "vt1", "daily_limit": 2, "priority_order": 1}]
        ctxs = build_production_line_sequencing_contexts(assignments, batches, slots, vehicle_types, [])
        issues = run_issue_rules("ProductionLineSequencing", ctxs, {})
        assert any(i["id"] == "distribution_daily_max_violation_1_vt1" for i in issues)

    def test_distribution_daily_max_ok_no_fire(self):
        assignments = [
            {"batch_id": f"b{i}", "slot_id": f"s{i}", "line_id": "L1", "day": 1, "start_hour": 8.0 + i, "vehicle_type": "vt1"}
            for i in range(3)
        ]
        batches = [
            {"id": f"b{i}", "compatible_lines": None, "line_on_day": 1, "line_off_day": 10, "vehicle_type": "vt1"}
            for i in range(3)
        ]
        slots = [{"id": f"s{i}", "line_id": "L1", "day": 1, "start_hour": 8.0 + i} for i in range(3)]
        vehicle_types = [{"id": "vt1", "daily_limit": 3, "priority_order": 1}]
        ctxs = build_production_line_sequencing_contexts(assignments, batches, slots, vehicle_types, [])
        issues = [i for i in run_issue_rules("ProductionLineSequencing", ctxs, {})
                  if i["id"] == "distribution_daily_max_violation_1_vt1"]
        assert issues == []

    def test_distribution_daily_min_violation_fires(self):
        assignments = [{"batch_id": "b1", "slot_id": "s1", "line_id": "L1", "day": 1, "start_hour": 8.0, "vehicle_type": "vt2"}]
        batches = [{"id": "b1", "compatible_lines": None, "line_on_day": 1, "line_off_day": 10, "vehicle_type": "vt2"}]
        slots = [{"id": "s1", "line_id": "L1", "day": 1, "start_hour": 8.0}]
        vehicle_types = [{"id": "vt2", "daily_limit": 99, "priority_order": 1}]
        distribution_exceptions = [{"period_days": [1], "vehicle_type_id": "vt2", "min_per_day": 2}]
        ctxs = build_production_line_sequencing_contexts(assignments, batches, slots, vehicle_types, distribution_exceptions)
        issues = run_issue_rules("ProductionLineSequencing", ctxs, {})
        assert any(i["id"] == "distribution_daily_min_violation_1_vt2" for i in issues)

    def test_distribution_daily_min_ok_no_fire(self):
        assignments = [
            {"batch_id": "b1", "slot_id": "s1", "line_id": "L1", "day": 1, "start_hour": 8.0, "vehicle_type": "vt2"},
            {"batch_id": "b2", "slot_id": "s2", "line_id": "L1", "day": 1, "start_hour": 9.0, "vehicle_type": "vt2"},
        ]
        batches = [
            {"id": "b1", "compatible_lines": None, "line_on_day": 1, "line_off_day": 10, "vehicle_type": "vt2"},
            {"id": "b2", "compatible_lines": None, "line_on_day": 1, "line_off_day": 10, "vehicle_type": "vt2"},
        ]
        slots = [
            {"id": "s1", "line_id": "L1", "day": 1, "start_hour": 8.0},
            {"id": "s2", "line_id": "L1", "day": 1, "start_hour": 9.0},
        ]
        vehicle_types = [{"id": "vt2", "daily_limit": 99, "priority_order": 1}]
        distribution_exceptions = [{"period_days": [1], "vehicle_type_id": "vt2", "min_per_day": 2}]
        ctxs = build_production_line_sequencing_contexts(assignments, batches, slots, vehicle_types, distribution_exceptions)
        issues = [i for i in run_issue_rules("ProductionLineSequencing", ctxs, {})
                  if i["id"] == "distribution_daily_min_violation_1_vt2"]
        assert issues == []

    def test_batting_order_violation_fires(self):
        assignments = [
            {"batch_id": "b1", "slot_id": "s1", "line_id": "L1", "day": 1, "start_hour": 10.0, "vehicle_type": "vtA"},
            {"batch_id": "b2", "slot_id": "s2", "line_id": "L1", "day": 1, "start_hour": 5.0, "vehicle_type": "vtB"},
        ]
        batches = [
            {"id": "b1", "compatible_lines": None, "line_on_day": 1, "line_off_day": 10, "vehicle_type": "vtA"},
            {"id": "b2", "compatible_lines": None, "line_on_day": 1, "line_off_day": 10, "vehicle_type": "vtB"},
        ]
        slots = [
            {"id": "s1", "line_id": "L1", "day": 1, "start_hour": 10.0},
            {"id": "s2", "line_id": "L1", "day": 1, "start_hour": 5.0},
        ]
        vehicle_types = [{"id": "vtA", "daily_limit": 99, "priority_order": 1}, {"id": "vtB", "daily_limit": 99, "priority_order": 2}]
        ctxs = build_production_line_sequencing_contexts(assignments, batches, slots, vehicle_types, [])
        issues = run_issue_rules("ProductionLineSequencing", ctxs, {})
        assert any(i["id"] == "batting_order_violation_1_vtA_vtB" for i in issues)

    def test_batting_order_ok_no_fire(self):
        assignments = [
            {"batch_id": "b1", "slot_id": "s1", "line_id": "L1", "day": 1, "start_hour": 5.0, "vehicle_type": "vtA"},
            {"batch_id": "b2", "slot_id": "s2", "line_id": "L1", "day": 1, "start_hour": 10.0, "vehicle_type": "vtB"},
        ]
        batches = [
            {"id": "b1", "compatible_lines": None, "line_on_day": 1, "line_off_day": 10, "vehicle_type": "vtA"},
            {"id": "b2", "compatible_lines": None, "line_on_day": 1, "line_off_day": 10, "vehicle_type": "vtB"},
        ]
        slots = [
            {"id": "s1", "line_id": "L1", "day": 1, "start_hour": 5.0},
            {"id": "s2", "line_id": "L1", "day": 1, "start_hour": 10.0},
        ]
        vehicle_types = [{"id": "vtA", "daily_limit": 99, "priority_order": 1}, {"id": "vtB", "daily_limit": 99, "priority_order": 2}]
        ctxs = build_production_line_sequencing_contexts(assignments, batches, slots, vehicle_types, [])
        issues = [i for i in run_issue_rules("ProductionLineSequencing", ctxs, {})
                  if i["id"] == "batting_order_violation_1_vtA_vtB"]
        assert issues == []

    def test_assignment_unknown_slot_fires(self):
        assignments = [{"batch_id": "b1", "slot_id": "sX", "line_id": "L1", "day": 1, "start_hour": 8.0, "vehicle_type": "vt1"}]
        batches = [{"id": "b1", "compatible_lines": None, "line_on_day": 1, "line_off_day": 10, "vehicle_type": "vt1"}]
        vehicle_types = [{"id": "vt1", "daily_limit": 99, "priority_order": 1}]
        ctxs = build_production_line_sequencing_contexts(assignments, batches, [], vehicle_types, [])
        issues = run_issue_rules("ProductionLineSequencing", ctxs, {})
        assert any(i["id"] == "assignment_unknown_slot_b1_sX" for i in issues)

    def test_assignment_known_slot_no_fire(self):
        assignments = [{"batch_id": "b1", "slot_id": "sX", "line_id": "L1", "day": 1, "start_hour": 8.0, "vehicle_type": "vt1"}]
        batches = [{"id": "b1", "compatible_lines": None, "line_on_day": 1, "line_off_day": 10, "vehicle_type": "vt1"}]
        slots = [{"id": "sX", "line_id": "L1", "day": 1, "start_hour": 8.0}]
        vehicle_types = [{"id": "vt1", "daily_limit": 99, "priority_order": 1}]
        ctxs = build_production_line_sequencing_contexts(assignments, batches, slots, vehicle_types, [])
        issues = [i for i in run_issue_rules("ProductionLineSequencing", ctxs, {}) if i["id"] == "assignment_unknown_slot_b1_sX"]
        assert issues == []

    def test_assignment_field_mismatch_fires(self):
        assignments = [{"batch_id": "b1", "slot_id": "s1", "line_id": "L1", "day": 1, "start_hour": 8.0, "vehicle_type": "vt1"}]
        batches = [{"id": "b1", "compatible_lines": None, "line_on_day": 1, "line_off_day": 10, "vehicle_type": "vt1"}]
        slots = [{"id": "s1", "line_id": "L2", "day": 1, "start_hour": 8.0}]
        vehicle_types = [{"id": "vt1", "daily_limit": 99, "priority_order": 1}]
        ctxs = build_production_line_sequencing_contexts(assignments, batches, slots, vehicle_types, [])
        issues = run_issue_rules("ProductionLineSequencing", ctxs, {})
        assert any(i["id"] == "assignment_field_mismatch_b1" for i in issues)

    def test_assignment_field_match_no_fire(self):
        assignments = [{"batch_id": "b1", "slot_id": "s1", "line_id": "L1", "day": 1, "start_hour": 8.0, "vehicle_type": "vt1"}]
        batches = [{"id": "b1", "compatible_lines": None, "line_on_day": 1, "line_off_day": 10, "vehicle_type": "vt1"}]
        slots = [{"id": "s1", "line_id": "L1", "day": 1, "start_hour": 8.0}]
        vehicle_types = [{"id": "vt1", "daily_limit": 99, "priority_order": 1}]
        ctxs = build_production_line_sequencing_contexts(assignments, batches, slots, vehicle_types, [])
        issues = [i for i in run_issue_rules("ProductionLineSequencing", ctxs, {}) if i["id"] == "assignment_field_mismatch_b1"]
        assert issues == []

    def test_assignment_vehicle_type_mismatch_fires(self):
        assignments = [{"batch_id": "b1", "slot_id": "s1", "line_id": "L1", "day": 1, "start_hour": 8.0, "vehicle_type": "vtX"}]
        batches = [{"id": "b1", "compatible_lines": None, "line_on_day": 1, "line_off_day": 10, "vehicle_type": "vtY"}]
        slots = [{"id": "s1", "line_id": "L1", "day": 1, "start_hour": 8.0}]
        vehicle_types = [{"id": "vtX", "daily_limit": 99, "priority_order": 1}]
        ctxs = build_production_line_sequencing_contexts(assignments, batches, slots, vehicle_types, [])
        issues = run_issue_rules("ProductionLineSequencing", ctxs, {})
        assert any(i["id"] == "assignment_vehicle_type_mismatch_b1" for i in issues)

    def test_assignment_vehicle_type_match_no_fire(self):
        assignments = [{"batch_id": "b1", "slot_id": "s1", "line_id": "L1", "day": 1, "start_hour": 8.0, "vehicle_type": "vtX"}]
        batches = [{"id": "b1", "compatible_lines": None, "line_on_day": 1, "line_off_day": 10, "vehicle_type": "vtX"}]
        slots = [{"id": "s1", "line_id": "L1", "day": 1, "start_hour": 8.0}]
        vehicle_types = [{"id": "vtX", "daily_limit": 99, "priority_order": 1}]
        ctxs = build_production_line_sequencing_contexts(assignments, batches, slots, vehicle_types, [])
        issues = [i for i in run_issue_rules("ProductionLineSequencing", ctxs, {}) if i["id"] == "assignment_vehicle_type_mismatch_b1"]
        assert issues == []

    def test_all_clean_no_fire(self):
        batches = [
            {"id": "b1", "compatible_lines": ["L1"], "line_on_day": 1, "line_off_day": 10, "vehicle_type": "vtA"},
            {"id": "b2", "compatible_lines": ["L1"], "line_on_day": 1, "line_off_day": 10, "vehicle_type": "vtB"},
        ]
        slots = [
            {"id": "s1", "line_id": "L1", "day": 1, "start_hour": 5.0},
            {"id": "s2", "line_id": "L1", "day": 1, "start_hour": 10.0},
        ]
        assignments = [
            {"batch_id": "b1", "slot_id": "s1", "line_id": "L1", "day": 1, "start_hour": 5.0, "vehicle_type": "vtA"},
            {"batch_id": "b2", "slot_id": "s2", "line_id": "L1", "day": 1, "start_hour": 10.0, "vehicle_type": "vtB"},
        ]
        vehicle_types = [{"id": "vtA", "daily_limit": 5, "priority_order": 1}, {"id": "vtB", "daily_limit": 5, "priority_order": 2}]
        ctxs = build_production_line_sequencing_contexts(assignments, batches, slots, vehicle_types, [])
        issues = run_issue_rules("ProductionLineSequencing", ctxs, {})
        assert issues == []

    def test_registered_in_issue_rules(self):
        assert "ProductionLineSequencing" in ISSUE_RULES


# ---------------------------------------------------------------------------
# テスト: RideshareMatchingPlanner 解チェッカー（2026-09-01新規、バッチ5）
# ---------------------------------------------------------------------------

class TestRideshareMatchingPlannerChecker:
    def test_seat_capacity_violation_fires(self):
        driver_routes = {"d1": {
            "driver_id": "d1", "driver_name": "D1", "seats": 1, "depart_min": 0, "arrive_max": 1000,
            "passengers": [
                {"passenger_id": "p1", "passenger_name": "P1", "pickup_min": 0, "dropoff_min": 30},
                {"passenger_id": "p2", "passenger_name": "P2", "pickup_min": 10, "dropoff_min": 40},
            ],
        }}
        matched_pairs = [
            {"passenger_id": "p1", "passenger_name": "P1", "driver_id": "d1", "driver_name": "D1",
             "pickup_min": 0, "dropoff_min": 30, "ride_min": 30, "direct_min": 30,
             "window_start_min": 0, "window_end_min": 1000},
            {"passenger_id": "p2", "passenger_name": "P2", "driver_id": "d1", "driver_name": "D1",
             "pickup_min": 10, "dropoff_min": 40, "ride_min": 30, "direct_min": 30,
             "window_start_min": 0, "window_end_min": 1000},
        ]
        passengers = [{"id": "p1"}, {"id": "p2"}]
        ctxs = build_rideshare_matching_planner_contexts(matched_pairs, [], driver_routes, passengers, {"detour_factor": 1.5})
        issues = run_issue_rules("RideshareMatchingPlanner", ctxs, {})
        assert any(i["id"] == "seat_capacity_violation_d1" for i in issues)
        assert all(i["category"] == "SOLVER" for i in issues)

    def test_seat_capacity_ok_no_fire(self):
        driver_routes = {"d1": {
            "driver_id": "d1", "driver_name": "D1", "seats": 3, "depart_min": 0, "arrive_max": 1000,
            "passengers": [
                {"passenger_id": "p1", "passenger_name": "P1", "pickup_min": 0, "dropoff_min": 30},
                {"passenger_id": "p2", "passenger_name": "P2", "pickup_min": 10, "dropoff_min": 40},
            ],
        }}
        matched_pairs = [
            {"passenger_id": "p1", "passenger_name": "P1", "driver_id": "d1", "driver_name": "D1",
             "pickup_min": 0, "dropoff_min": 30, "ride_min": 30, "direct_min": 30,
             "window_start_min": 0, "window_end_min": 1000},
            {"passenger_id": "p2", "passenger_name": "P2", "driver_id": "d1", "driver_name": "D1",
             "pickup_min": 10, "dropoff_min": 40, "ride_min": 30, "direct_min": 30,
             "window_start_min": 0, "window_end_min": 1000},
        ]
        passengers = [{"id": "p1"}, {"id": "p2"}]
        ctxs = build_rideshare_matching_planner_contexts(matched_pairs, [], driver_routes, passengers, {"detour_factor": 1.5})
        issues = [i for i in run_issue_rules("RideshareMatchingPlanner", ctxs, {}) if i["id"] == "seat_capacity_violation_d1"]
        assert issues == []

    def test_detour_time_violation_fires(self):
        driver_routes = {"d1": {
            "driver_id": "d1", "driver_name": "D1", "seats": 4, "depart_min": 0, "arrive_max": 1000,
            "passengers": [{"passenger_id": "p1", "passenger_name": "P1", "pickup_min": 0, "dropoff_min": 100}],
        }}
        matched_pairs = [{"passenger_id": "p1", "passenger_name": "P1", "driver_id": "d1", "driver_name": "D1",
                           "pickup_min": 0, "dropoff_min": 100, "ride_min": 100, "direct_min": 30,
                           "window_start_min": 0, "window_end_min": 1000}]
        passengers = [{"id": "p1"}]
        ctxs = build_rideshare_matching_planner_contexts(matched_pairs, [], driver_routes, passengers, {"detour_factor": 1.5})
        issues = run_issue_rules("RideshareMatchingPlanner", ctxs, {})
        assert any(i["id"] == "detour_time_violation_p1" for i in issues)

    def test_detour_time_ok_no_fire(self):
        driver_routes = {"d1": {
            "driver_id": "d1", "driver_name": "D1", "seats": 4, "depart_min": 0, "arrive_max": 1000,
            "passengers": [{"passenger_id": "p1", "passenger_name": "P1", "pickup_min": 0, "dropoff_min": 40}],
        }}
        matched_pairs = [{"passenger_id": "p1", "passenger_name": "P1", "driver_id": "d1", "driver_name": "D1",
                           "pickup_min": 0, "dropoff_min": 40, "ride_min": 40, "direct_min": 30,
                           "window_start_min": 0, "window_end_min": 1000}]
        passengers = [{"id": "p1"}]
        ctxs = build_rideshare_matching_planner_contexts(matched_pairs, [], driver_routes, passengers, {"detour_factor": 1.5})
        issues = [i for i in run_issue_rules("RideshareMatchingPlanner", ctxs, {}) if i["id"] == "detour_time_violation_p1"]
        assert issues == []

    def test_pickup_dropoff_order_violation_fires(self):
        driver_routes = {"d1": {
            "driver_id": "d1", "driver_name": "D1", "seats": 4, "depart_min": 0, "arrive_max": 1000,
            "passengers": [{"passenger_id": "p1", "passenger_name": "P1", "pickup_min": 50, "dropoff_min": 50}],
        }}
        matched_pairs = [{"passenger_id": "p1", "passenger_name": "P1", "driver_id": "d1", "driver_name": "D1",
                           "pickup_min": 50, "dropoff_min": 50, "ride_min": 0, "direct_min": 10,
                           "window_start_min": 0, "window_end_min": 1000}]
        passengers = [{"id": "p1"}]
        ctxs = build_rideshare_matching_planner_contexts(matched_pairs, [], driver_routes, passengers, {})
        issues = run_issue_rules("RideshareMatchingPlanner", ctxs, {})
        assert any(i["id"] == "pickup_dropoff_order_violation_p1" for i in issues)

    def test_pickup_dropoff_order_ok_no_fire(self):
        driver_routes = {"d1": {
            "driver_id": "d1", "driver_name": "D1", "seats": 4, "depart_min": 0, "arrive_max": 1000,
            "passengers": [{"passenger_id": "p1", "passenger_name": "P1", "pickup_min": 50, "dropoff_min": 60}],
        }}
        matched_pairs = [{"passenger_id": "p1", "passenger_name": "P1", "driver_id": "d1", "driver_name": "D1",
                           "pickup_min": 50, "dropoff_min": 60, "ride_min": 10, "direct_min": 10,
                           "window_start_min": 0, "window_end_min": 1000}]
        passengers = [{"id": "p1"}]
        ctxs = build_rideshare_matching_planner_contexts(matched_pairs, [], driver_routes, passengers, {})
        issues = [i for i in run_issue_rules("RideshareMatchingPlanner", ctxs, {}) if i["id"] == "pickup_dropoff_order_violation_p1"]
        assert issues == []

    def test_passenger_time_window_violation_fires(self):
        driver_routes = {"d1": {
            "driver_id": "d1", "driver_name": "D1", "seats": 4, "depart_min": 0, "arrive_max": 1000,
            "passengers": [{"passenger_id": "p1", "passenger_name": "P1", "pickup_min": 5, "dropoff_min": 20}],
        }}
        matched_pairs = [{"passenger_id": "p1", "passenger_name": "P1", "driver_id": "d1", "driver_name": "D1",
                           "pickup_min": 5, "dropoff_min": 20, "ride_min": 15, "direct_min": 15,
                           "window_start_min": 10, "window_end_min": 100}]
        passengers = [{"id": "p1"}]
        ctxs = build_rideshare_matching_planner_contexts(matched_pairs, [], driver_routes, passengers, {})
        issues = run_issue_rules("RideshareMatchingPlanner", ctxs, {})
        assert any(i["id"] == "passenger_time_window_violation_p1" for i in issues)

    def test_passenger_time_window_ok_no_fire(self):
        driver_routes = {"d1": {
            "driver_id": "d1", "driver_name": "D1", "seats": 4, "depart_min": 0, "arrive_max": 1000,
            "passengers": [{"passenger_id": "p1", "passenger_name": "P1", "pickup_min": 15, "dropoff_min": 30}],
        }}
        matched_pairs = [{"passenger_id": "p1", "passenger_name": "P1", "driver_id": "d1", "driver_name": "D1",
                           "pickup_min": 15, "dropoff_min": 30, "ride_min": 15, "direct_min": 15,
                           "window_start_min": 10, "window_end_min": 100}]
        passengers = [{"id": "p1"}]
        ctxs = build_rideshare_matching_planner_contexts(matched_pairs, [], driver_routes, passengers, {})
        issues = [i for i in run_issue_rules("RideshareMatchingPlanner", ctxs, {}) if i["id"] == "passenger_time_window_violation_p1"]
        assert issues == []

    def test_driver_operating_window_violation_fires(self):
        driver_routes = {"d1": {
            "driver_id": "d1", "driver_name": "D1", "seats": 4, "depart_min": 100, "arrive_max": 200,
            "passengers": [{"passenger_id": "p1", "passenger_name": "P1", "pickup_min": 50, "dropoff_min": 150}],
        }}
        matched_pairs = [{"passenger_id": "p1", "passenger_name": "P1", "driver_id": "d1", "driver_name": "D1",
                           "pickup_min": 50, "dropoff_min": 150, "ride_min": 100, "direct_min": 80,
                           "window_start_min": 0, "window_end_min": 1000}]
        passengers = [{"id": "p1"}]
        ctxs = build_rideshare_matching_planner_contexts(matched_pairs, [], driver_routes, passengers, {"detour_factor": 1.5})
        issues = run_issue_rules("RideshareMatchingPlanner", ctxs, {})
        assert any(i["id"] == "driver_operating_window_violation_d1_p1" for i in issues)

    def test_driver_operating_window_ok_no_fire(self):
        driver_routes = {"d1": {
            "driver_id": "d1", "driver_name": "D1", "seats": 4, "depart_min": 100, "arrive_max": 200,
            "passengers": [{"passenger_id": "p1", "passenger_name": "P1", "pickup_min": 150, "dropoff_min": 180}],
        }}
        matched_pairs = [{"passenger_id": "p1", "passenger_name": "P1", "driver_id": "d1", "driver_name": "D1",
                           "pickup_min": 150, "dropoff_min": 180, "ride_min": 30, "direct_min": 30,
                           "window_start_min": 0, "window_end_min": 1000}]
        passengers = [{"id": "p1"}]
        ctxs = build_rideshare_matching_planner_contexts(matched_pairs, [], driver_routes, passengers, {})
        issues = [i for i in run_issue_rules("RideshareMatchingPlanner", ctxs, {})
                  if i["id"] == "driver_operating_window_violation_d1_p1"]
        assert issues == []

    def test_passenger_duplicate_assignment_fires(self):
        driver_routes = {
            "d1": {"driver_id": "d1", "driver_name": "D1", "seats": 4, "depart_min": 0, "arrive_max": 1000,
                   "passengers": [{"passenger_id": "p1", "passenger_name": "P1", "pickup_min": 0, "dropoff_min": 20}]},
            "d2": {"driver_id": "d2", "driver_name": "D2", "seats": 4, "depart_min": 0, "arrive_max": 1000,
                   "passengers": [{"passenger_id": "p1", "passenger_name": "P1", "pickup_min": 0, "dropoff_min": 20}]},
        }
        matched_pairs = [
            {"passenger_id": "p1", "passenger_name": "P1", "driver_id": "d1", "driver_name": "D1",
             "pickup_min": 0, "dropoff_min": 20, "ride_min": 20, "direct_min": 20,
             "window_start_min": 0, "window_end_min": 1000},
            {"passenger_id": "p1", "passenger_name": "P1", "driver_id": "d2", "driver_name": "D2",
             "pickup_min": 0, "dropoff_min": 20, "ride_min": 20, "direct_min": 20,
             "window_start_min": 0, "window_end_min": 1000},
        ]
        passengers = [{"id": "p1"}]
        ctxs = build_rideshare_matching_planner_contexts(matched_pairs, [], driver_routes, passengers, {})
        issues = run_issue_rules("RideshareMatchingPlanner", ctxs, {})
        assert any(i["id"] == "passenger_duplicate_assignment_p1" for i in issues)

    def test_passenger_single_assignment_no_fire(self):
        driver_routes = {"d1": {"driver_id": "d1", "driver_name": "D1", "seats": 4, "depart_min": 0, "arrive_max": 1000,
                                 "passengers": [{"passenger_id": "p1", "passenger_name": "P1", "pickup_min": 0, "dropoff_min": 20}]}}
        matched_pairs = [{"passenger_id": "p1", "passenger_name": "P1", "driver_id": "d1", "driver_name": "D1",
                           "pickup_min": 0, "dropoff_min": 20, "ride_min": 20, "direct_min": 20,
                           "window_start_min": 0, "window_end_min": 1000}]
        passengers = [{"id": "p1"}]
        ctxs = build_rideshare_matching_planner_contexts(matched_pairs, [], driver_routes, passengers, {})
        issues = [i for i in run_issue_rules("RideshareMatchingPlanner", ctxs, {}) if i["id"] == "passenger_duplicate_assignment_p1"]
        assert issues == []

    def test_passenger_missing_from_output_fires(self):
        driver_routes = {"d1": {"driver_id": "d1", "driver_name": "D1", "seats": 4, "depart_min": 0, "arrive_max": 1000,
                                 "passengers": [{"passenger_id": "p1", "passenger_name": "P1", "pickup_min": 0, "dropoff_min": 20}]}}
        matched_pairs = [{"passenger_id": "p1", "passenger_name": "P1", "driver_id": "d1", "driver_name": "D1",
                           "pickup_min": 0, "dropoff_min": 20, "ride_min": 20, "direct_min": 20,
                           "window_start_min": 0, "window_end_min": 1000}]
        passengers = [{"id": "p1"}, {"id": "p2"}]
        ctxs = build_rideshare_matching_planner_contexts(matched_pairs, [], driver_routes, passengers, {})
        issues = run_issue_rules("RideshareMatchingPlanner", ctxs, {})
        assert any(i["id"] == "passenger_missing_from_output_p2" for i in issues)

    def test_passenger_present_in_unmatched_no_fire(self):
        driver_routes = {"d1": {"driver_id": "d1", "driver_name": "D1", "seats": 4, "depart_min": 0, "arrive_max": 1000,
                                 "passengers": [{"passenger_id": "p1", "passenger_name": "P1", "pickup_min": 0, "dropoff_min": 20}]}}
        matched_pairs = [{"passenger_id": "p1", "passenger_name": "P1", "driver_id": "d1", "driver_name": "D1",
                           "pickup_min": 0, "dropoff_min": 20, "ride_min": 20, "direct_min": 20,
                           "window_start_min": 0, "window_end_min": 1000}]
        unmatched_passengers = [{"id": "p2"}]
        passengers = [{"id": "p1"}, {"id": "p2"}]
        ctxs = build_rideshare_matching_planner_contexts(matched_pairs, unmatched_passengers, driver_routes, passengers, {})
        issues = [i for i in run_issue_rules("RideshareMatchingPlanner", ctxs, {}) if i["id"] == "passenger_missing_from_output_p2"]
        assert issues == []

    def test_all_clean_no_fire(self):
        driver_routes = {"d1": {"driver_id": "d1", "driver_name": "D1", "seats": 4, "depart_min": 0, "arrive_max": 1000,
                                 "passengers": [{"passenger_id": "p1", "passenger_name": "P1", "pickup_min": 10, "dropoff_min": 40}]}}
        matched_pairs = [{"passenger_id": "p1", "passenger_name": "P1", "driver_id": "d1", "driver_name": "D1",
                           "pickup_min": 10, "dropoff_min": 40, "ride_min": 30, "direct_min": 25,
                           "window_start_min": 0, "window_end_min": 1000}]
        passengers = [{"id": "p1"}]
        ctxs = build_rideshare_matching_planner_contexts(matched_pairs, [], driver_routes, passengers, {"detour_factor": 1.5})
        issues = run_issue_rules("RideshareMatchingPlanner", ctxs, {})
        assert issues == []

    def test_registered_in_issue_rules(self):
        assert "RideshareMatchingPlanner" in ISSUE_RULES


# ---------------------------------------------------------------------------
# テスト: ShiftRotationScheduler 解チェッカー（2026-09-01新規、バッチ5）
# ---------------------------------------------------------------------------

def _srs_baseline():
    """全制約を満たすクリーンなW=1テンプレート（E×4, OFF×3、土日ともOFF）。"""
    template_shift_ids = [["E", "E", "E", "E", "OFF", "OFF", "OFF"]]
    weeks = [[
        {"shift_id": "E", "shift_type": "early"},
        {"shift_id": "E", "shift_type": "early"},
        {"shift_id": "E", "shift_type": "early"},
        {"shift_id": "E", "shift_type": "early"},
        {"shift_id": "OFF", "shift_type": "off"},
        {"shift_id": "OFF", "shift_type": "off"},
        {"shift_id": "OFF", "shift_type": "off"},
    ]]
    employee_schedules = [{"employee_id": "EMP00", "weeks": weeks}]
    shifts_def = [{"id": "E", "type": "early"}, {"id": "OFF", "type": "off"}]
    return template_shift_ids, employee_schedules, shifts_def


class TestShiftRotationSchedulerChecker:
    def test_weekend_shift_mismatch_fires(self):
        template_shift_ids = [["E", "E", "E", "E", "OFF", "E", "OFF"]]
        employee_schedules = [{"employee_id": "EMP00", "weeks": [[
            {"shift_id": "E", "shift_type": "early"}, {"shift_id": "E", "shift_type": "early"},
            {"shift_id": "E", "shift_type": "early"}, {"shift_id": "E", "shift_type": "early"},
            {"shift_id": "OFF", "shift_type": "off"}, {"shift_id": "E", "shift_type": "early"},
            {"shift_id": "OFF", "shift_type": "off"},
        ]]}]
        shifts_def = [{"id": "E", "type": "early"}, {"id": "OFF", "type": "off"}]
        ctxs = build_shift_rotation_scheduler_contexts(template_shift_ids, employee_schedules, shifts_def, [], {})
        issues = run_issue_rules("ShiftRotationScheduler", ctxs, {})
        assert any(i["id"] == "weekend_shift_mismatch_w0" for i in issues)
        assert all(i["category"] == "SOLVER" for i in issues)

    def test_weekend_shift_match_no_fire(self):
        template_shift_ids, employee_schedules, shifts_def = _srs_baseline()
        ctxs = build_shift_rotation_scheduler_contexts(template_shift_ids, employee_schedules, shifts_def, [], {})
        issues = [i for i in run_issue_rules("ShiftRotationScheduler", ctxs, {}) if i["id"] == "weekend_shift_mismatch_w0"]
        assert issues == []

    def test_daily_requirement_mismatch_fires(self):
        template_shift_ids, employee_schedules, shifts_def = _srs_baseline()
        daily_requirements = [{"day_of_week": 0, "shift_id": "E", "required": 2}]
        ctxs = build_shift_rotation_scheduler_contexts(template_shift_ids, employee_schedules, shifts_def, daily_requirements, {})
        issues = run_issue_rules("ShiftRotationScheduler", ctxs, {})
        assert any(i["id"] == "daily_requirement_mismatch_w0_d0_E" for i in issues)

    def test_daily_requirement_match_no_fire(self):
        template_shift_ids, employee_schedules, shifts_def = _srs_baseline()
        daily_requirements = [{"day_of_week": 0, "shift_id": "E", "required": 1}]
        ctxs = build_shift_rotation_scheduler_contexts(template_shift_ids, employee_schedules, shifts_def, daily_requirements, {})
        issues = [i for i in run_issue_rules("ShiftRotationScheduler", ctxs, {}) if i["id"] == "daily_requirement_mismatch_w0_d0_E"]
        assert issues == []

    def test_shift_progression_violation_fires(self):
        template_shift_ids = [["L", "E", "OFF", "OFF", "OFF", "OFF", "OFF"]]
        shifts_def = [{"id": "L", "type": "late"}, {"id": "E", "type": "early"}, {"id": "OFF", "type": "off"}]
        employee_schedules = [{"employee_id": "EMP00", "weeks": [[
            {"shift_id": "L", "shift_type": "late"}, {"shift_id": "E", "shift_type": "early"},
            {"shift_id": "OFF", "shift_type": "off"}, {"shift_id": "OFF", "shift_type": "off"},
            {"shift_id": "OFF", "shift_type": "off"}, {"shift_id": "OFF", "shift_type": "off"},
            {"shift_id": "OFF", "shift_type": "off"},
        ]]}]
        ctxs = build_shift_rotation_scheduler_contexts(template_shift_ids, employee_schedules, shifts_def, [], {})
        issues = run_issue_rules("ShiftRotationScheduler", ctxs, {})
        assert any(i["id"] == "shift_progression_violation_EMP00_day1" for i in issues)

    def test_shift_progression_forward_ok_no_fire(self):
        template_shift_ids = [["E", "L", "OFF", "OFF", "OFF", "OFF", "OFF"]]
        shifts_def = [{"id": "L", "type": "late"}, {"id": "E", "type": "early"}, {"id": "OFF", "type": "off"}]
        employee_schedules = [{"employee_id": "EMP00", "weeks": [[
            {"shift_id": "E", "shift_type": "early"}, {"shift_id": "L", "shift_type": "late"},
            {"shift_id": "OFF", "shift_type": "off"}, {"shift_id": "OFF", "shift_type": "off"},
            {"shift_id": "OFF", "shift_type": "off"}, {"shift_id": "OFF", "shift_type": "off"},
            {"shift_id": "OFF", "shift_type": "off"},
        ]]}]
        ctxs = build_shift_rotation_scheduler_contexts(template_shift_ids, employee_schedules, shifts_def, [], {})
        issues = [i for i in run_issue_rules("ShiftRotationScheduler", ctxs, {})
                  if i["id"].startswith("shift_progression_violation_")]
        assert issues == []

    def test_consecutive_run_length_violation_fires(self):
        template_shift_ids = [["E", "OFF", "E", "E", "E", "E", "E"]]
        shifts_def = [{"id": "E", "type": "early"}, {"id": "OFF", "type": "off"}]
        employee_schedules = [{"employee_id": "EMP00", "weeks": [[
            {"shift_id": "E", "shift_type": "early"}, {"shift_id": "OFF", "shift_type": "off"},
            {"shift_id": "E", "shift_type": "early"}, {"shift_id": "E", "shift_type": "early"},
            {"shift_id": "E", "shift_type": "early"}, {"shift_id": "E", "shift_type": "early"},
            {"shift_id": "E", "shift_type": "early"},
        ]]}]
        ctxs = build_shift_rotation_scheduler_contexts(template_shift_ids, employee_schedules, shifts_def, [], {})
        issues = run_issue_rules("ShiftRotationScheduler", ctxs, {})
        assert any(i["id"] == "consecutive_run_length_violation_EMP00_start1" for i in issues)

    def test_consecutive_run_length_ok_no_fire(self):
        template_shift_ids, employee_schedules, shifts_def = _srs_baseline()
        ctxs = build_shift_rotation_scheduler_contexts(template_shift_ids, employee_schedules, shifts_def, [], {})
        issues = [i for i in run_issue_rules("ShiftRotationScheduler", ctxs, {})
                  if i["id"].startswith("consecutive_run_length_violation_")]
        assert issues == []

    def test_days_off_window_violation_fires(self):
        template_shift_ids = [["E", "E", "E", "E", "E", "E", "E"]]
        shifts_def = [{"id": "E", "type": "early"}]
        employee_schedules = [{"employee_id": "EMP00", "weeks": [[
            {"shift_id": "E", "shift_type": "early"} for _ in range(7)
        ]]}]
        ctxs = build_shift_rotation_scheduler_contexts(template_shift_ids, employee_schedules, shifts_def, [], {})
        issues = run_issue_rules("ShiftRotationScheduler", ctxs, {})
        assert any(i["id"] == "days_off_window_violation_EMP00_start0" for i in issues)

    def test_days_off_window_ok_no_fire(self):
        template_shift_ids, employee_schedules, shifts_def = _srs_baseline()
        ctxs = build_shift_rotation_scheduler_contexts(template_shift_ids, employee_schedules, shifts_def, [], {})
        issues = [i for i in run_issue_rules("ShiftRotationScheduler", ctxs, {})
                  if i["id"].startswith("days_off_window_violation_")]
        assert issues == []

    def test_employee_schedule_derivation_mismatch_fires(self):
        template_shift_ids, employee_schedules, shifts_def = _srs_baseline()
        employee_schedules[0]["weeks"][0][0] = {"shift_id": "WRONG", "shift_type": "early"}
        ctxs = build_shift_rotation_scheduler_contexts(template_shift_ids, employee_schedules, shifts_def, [], {})
        issues = run_issue_rules("ShiftRotationScheduler", ctxs, {})
        assert any(i["id"] == "employee_schedule_derivation_mismatch_EMP00_w0_d0" for i in issues)

    def test_employee_schedule_derivation_match_no_fire(self):
        template_shift_ids, employee_schedules, shifts_def = _srs_baseline()
        ctxs = build_shift_rotation_scheduler_contexts(template_shift_ids, employee_schedules, shifts_def, [], {})
        issues = [i for i in run_issue_rules("ShiftRotationScheduler", ctxs, {})
                  if i["id"].startswith("employee_schedule_derivation_mismatch_")]
        assert issues == []

    def test_all_clean_no_fire(self):
        template_shift_ids, employee_schedules, shifts_def = _srs_baseline()
        daily_requirements = [{"day_of_week": 0, "shift_id": "E", "required": 1}]
        ctxs = build_shift_rotation_scheduler_contexts(template_shift_ids, employee_schedules, shifts_def, daily_requirements, {})
        issues = run_issue_rules("ShiftRotationScheduler", ctxs, {})
        assert issues == []

    def test_registered_in_issue_rules(self):
        assert "ShiftRotationScheduler" in ISSUE_RULES


# ---------------------------------------------------------------------------
# テスト: SteelMillSlabDesign 解チェッカー（2026-09-01新規、バッチ6・最終）
# ---------------------------------------------------------------------------

class TestSteelMillSlabDesignChecker:
    def _baseline(self):
        orders = [
            {"id": "o1", "weight": 10, "colors": ["red"]},
            {"id": "o2", "weight": 15, "colors": ["blue"]},
        ]
        assignments = [
            {"order_id": "o1", "slab_index": 0, "weight": 10, "colors": ["red"]},
            {"order_id": "o2", "slab_index": 0, "weight": 15, "colors": ["blue"]},
        ]
        slabs_used = [{"slab_index": 0, "capacity": 30, "used_weight": 25, "waste_weight": 5}]
        return orders, assignments, slabs_used

    def test_order_duplicate_assignment_fires(self):
        orders = [{"id": "o1", "weight": 10, "colors": ["red"]}]
        assignments = [
            {"order_id": "o1", "slab_index": 0, "weight": 10, "colors": ["red"]},
            {"order_id": "o1", "slab_index": 1, "weight": 10, "colors": ["red"]},
        ]
        slabs_used = [
            {"slab_index": 0, "capacity": 30, "used_weight": 10, "waste_weight": 20},
            {"slab_index": 1, "capacity": 30, "used_weight": 10, "waste_weight": 20},
        ]
        ctxs = build_steel_mill_slab_design_contexts(assignments, slabs_used, orders)
        issues = run_issue_rules("SteelMillSlabDesign", ctxs, {})
        assert any(i["id"] == "order_duplicate_assignment_o1" for i in issues)
        assert all(i["category"] == "SOLVER" for i in issues)

    def test_order_single_assignment_no_fire(self):
        orders, assignments, slabs_used = self._baseline()
        ctxs = build_steel_mill_slab_design_contexts(assignments, slabs_used, orders)
        issues = [i for i in run_issue_rules("SteelMillSlabDesign", ctxs, {})
                  if i["id"].startswith("order_duplicate_assignment_")]
        assert issues == []

    def test_order_missing_from_output_fires(self):
        orders = [
            {"id": "o1", "weight": 10, "colors": ["red"]},
            {"id": "o2", "weight": 15, "colors": ["blue"]},
        ]
        assignments = [{"order_id": "o1", "slab_index": 0, "weight": 10, "colors": ["red"]}]
        slabs_used = [{"slab_index": 0, "capacity": 30, "used_weight": 10, "waste_weight": 20}]
        ctxs = build_steel_mill_slab_design_contexts(assignments, slabs_used, orders)
        issues = run_issue_rules("SteelMillSlabDesign", ctxs, {})
        assert any(i["id"] == "order_missing_from_output_o2" for i in issues)

    def test_all_orders_present_no_fire(self):
        orders, assignments, slabs_used = self._baseline()
        ctxs = build_steel_mill_slab_design_contexts(assignments, slabs_used, orders)
        issues = [i for i in run_issue_rules("SteelMillSlabDesign", ctxs, {})
                  if i["id"].startswith("order_missing_from_output_")]
        assert issues == []

    def test_slab_capacity_exceeded_fires(self):
        orders = [{"id": "o1", "weight": 35, "colors": ["red"]}]
        assignments = [{"order_id": "o1", "slab_index": 0, "weight": 35, "colors": ["red"]}]
        slabs_used = [{"slab_index": 0, "capacity": 30, "used_weight": 35, "waste_weight": -5}]
        ctxs = build_steel_mill_slab_design_contexts(assignments, slabs_used, orders)
        issues = run_issue_rules("SteelMillSlabDesign", ctxs, {})
        assert any(i["id"] == "slab_capacity_exceeded_0" for i in issues)

    def test_slab_within_capacity_no_fire(self):
        orders, assignments, slabs_used = self._baseline()
        ctxs = build_steel_mill_slab_design_contexts(assignments, slabs_used, orders)
        issues = [i for i in run_issue_rules("SteelMillSlabDesign", ctxs, {})
                  if i["id"].startswith("slab_capacity_exceeded_")]
        assert issues == []

    def test_slab_color_limit_exceeded_fires(self):
        orders = [
            {"id": "o1", "weight": 5, "colors": ["red"]},
            {"id": "o2", "weight": 5, "colors": ["blue"]},
            {"id": "o3", "weight": 5, "colors": ["green"]},
        ]
        assignments = [
            {"order_id": "o1", "slab_index": 0, "weight": 5, "colors": ["red"]},
            {"order_id": "o2", "slab_index": 0, "weight": 5, "colors": ["blue"]},
            {"order_id": "o3", "slab_index": 0, "weight": 5, "colors": ["green"]},
        ]
        slabs_used = [{"slab_index": 0, "capacity": 100, "used_weight": 15, "waste_weight": 85}]
        ctxs = build_steel_mill_slab_design_contexts(assignments, slabs_used, orders)
        issues = run_issue_rules("SteelMillSlabDesign", ctxs, {})
        assert any(i["id"] == "slab_color_limit_exceeded_0" for i in issues)

    def test_slab_two_colors_no_fire(self):
        orders, assignments, slabs_used = self._baseline()
        ctxs = build_steel_mill_slab_design_contexts(assignments, slabs_used, orders)
        issues = [i for i in run_issue_rules("SteelMillSlabDesign", ctxs, {})
                  if i["id"].startswith("slab_color_limit_exceeded_")]
        assert issues == []

    def test_slab_used_weight_mismatch_fires(self):
        orders = [{"id": "o1", "weight": 10, "colors": ["red"]}]
        assignments = [{"order_id": "o1", "slab_index": 0, "weight": 10, "colors": ["red"]}]
        slabs_used = [{"slab_index": 0, "capacity": 30, "used_weight": 999, "waste_weight": 1}]
        ctxs = build_steel_mill_slab_design_contexts(assignments, slabs_used, orders)
        issues = run_issue_rules("SteelMillSlabDesign", ctxs, {})
        assert any(i["id"] == "slab_used_weight_mismatch_0" for i in issues)

    def test_slab_used_weight_consistent_no_fire(self):
        orders, assignments, slabs_used = self._baseline()
        ctxs = build_steel_mill_slab_design_contexts(assignments, slabs_used, orders)
        issues = [i for i in run_issue_rules("SteelMillSlabDesign", ctxs, {})
                  if i["id"].startswith("slab_used_weight_mismatch_")]
        assert issues == []

    def test_all_clean_no_fire(self):
        orders, assignments, slabs_used = self._baseline()
        ctxs = build_steel_mill_slab_design_contexts(assignments, slabs_used, orders)
        issues = run_issue_rules("SteelMillSlabDesign", ctxs, {})
        assert issues == []

    def test_registered_in_issue_rules(self):
        assert "SteelMillSlabDesign" in ISSUE_RULES


# ---------------------------------------------------------------------------
# テスト: VesselDeckLoader 解チェッカー（2026-09-01新規、バッチ6・最終）
# ---------------------------------------------------------------------------

class TestVesselDeckLoaderChecker:
    def _baseline(self):
        # c1: 西壁・南壁・北壁に接し支持は自明（rank0のため未検証）。
        # c2: どの壁にも接しないが、c1と東側面で正の長さの境界接触あり（支持OK）。
        # 2コンテナは物理的に重なっていない（x=3で境界を共有するのみ）。
        containers = [{"id": "c1"}, {"id": "c2"}]
        placements = [
            {"container_id": "c1", "container_name": "C1", "x": 0, "y": 0,
             "length": 3, "width": 3, "load_order": 1, "is_hazardous": False},
            {"container_id": "c2", "container_name": "C2", "x": 3, "y": 2,
             "length": 3, "width": 2, "load_order": 2, "is_hazardous": False},
        ]
        Z_val = 6
        deck_width = 6
        hazard_margin = 1
        return containers, placements, Z_val, deck_width, hazard_margin

    def test_container_missing_from_placement_fires(self):
        containers = [{"id": "c1"}, {"id": "c2"}]
        placements = [{"container_id": "c1", "container_name": "C1", "x": 0, "y": 0,
                        "length": 3, "width": 3, "load_order": 1, "is_hazardous": False}]
        ctxs = build_vessel_deck_loader_contexts(placements, 3, 6, 1, containers)
        issues = run_issue_rules("VesselDeckLoader", ctxs, {})
        assert any(i["id"] == "container_missing_from_placement_c2" for i in issues)
        assert all(i["category"] == "SOLVER" for i in issues)

    def test_all_containers_placed_no_fire(self):
        containers, placements, Z_val, deck_width, hazard_margin = self._baseline()
        ctxs = build_vessel_deck_loader_contexts(placements, Z_val, deck_width, hazard_margin, containers)
        issues = [i for i in run_issue_rules("VesselDeckLoader", ctxs, {})
                  if i["id"].startswith("container_missing_from_placement_")]
        assert issues == []

    def test_container_overlap_violation_fires(self):
        containers = [{"id": "c1"}, {"id": "c2"}]
        placements = [
            {"container_id": "c1", "container_name": "C1", "x": 0, "y": 0,
             "length": 3, "width": 3, "load_order": 1, "is_hazardous": False},
            {"container_id": "c2", "container_name": "C2", "x": 1, "y": 1,
             "length": 3, "width": 3, "load_order": 2, "is_hazardous": False},
        ]
        ctxs = build_vessel_deck_loader_contexts(placements, 4, 6, 1, containers)
        issues = run_issue_rules("VesselDeckLoader", ctxs, {})
        assert any(i["id"] == "container_overlap_violation_c1_c2" for i in issues)

    def test_container_no_overlap_no_fire(self):
        containers, placements, Z_val, deck_width, hazard_margin = self._baseline()
        ctxs = build_vessel_deck_loader_contexts(placements, Z_val, deck_width, hazard_margin, containers)
        issues = [i for i in run_issue_rules("VesselDeckLoader", ctxs, {})
                  if i["id"].startswith("container_overlap_violation_")]
        assert issues == []

    def test_container_unsupported_fires(self):
        containers = [{"id": "c1"}, {"id": "c2"}]
        placements = [
            {"container_id": "c1", "container_name": "C1", "x": 0, "y": 0,
             "length": 3, "width": 3, "load_order": 1, "is_hazardous": False},
            {"container_id": "c2", "container_name": "C2", "x": 5, "y": 3,
             "length": 1, "width": 1, "load_order": 2, "is_hazardous": False},
        ]
        ctxs = build_vessel_deck_loader_contexts(placements, 6, 10, 1, containers)
        issues = run_issue_rules("VesselDeckLoader", ctxs, {})
        assert any(i["id"] == "container_unsupported_c2" for i in issues)

    def test_container_supported_via_contact_no_fire(self):
        containers, placements, Z_val, deck_width, hazard_margin = self._baseline()
        ctxs = build_vessel_deck_loader_contexts(placements, Z_val, deck_width, hazard_margin, containers)
        issues = [i for i in run_issue_rules("VesselDeckLoader", ctxs, {})
                  if i["id"].startswith("container_unsupported_")]
        assert issues == []

    def test_used_length_mismatch_fires(self):
        containers = [{"id": "c1"}]
        placements = [{"container_id": "c1", "container_name": "C1", "x": 0, "y": 0,
                        "length": 3, "width": 3, "load_order": 1, "is_hazardous": False}]
        ctxs = build_vessel_deck_loader_contexts(placements, 999, 6, 1, containers)
        issues = run_issue_rules("VesselDeckLoader", ctxs, {})
        assert any(i["id"] == "used_length_mismatch" for i in issues)

    def test_used_length_consistent_no_fire(self):
        containers, placements, Z_val, deck_width, hazard_margin = self._baseline()
        ctxs = build_vessel_deck_loader_contexts(placements, Z_val, deck_width, hazard_margin, containers)
        issues = [i for i in run_issue_rules("VesselDeckLoader", ctxs, {}) if i["id"] == "used_length_mismatch"]
        assert issues == []

    def test_all_clean_no_fire(self):
        containers, placements, Z_val, deck_width, hazard_margin = self._baseline()
        ctxs = build_vessel_deck_loader_contexts(placements, Z_val, deck_width, hazard_margin, containers)
        issues = run_issue_rules("VesselDeckLoader", ctxs, {})
        assert issues == []

    def test_registered_in_issue_rules(self):
        assert "VesselDeckLoader" in ISSUE_RULES
