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


