
import pytest
from solvers.base.issue_rules import (
    build_yard_contexts,
    run_issue_rules,
)
from ._common import _container




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
