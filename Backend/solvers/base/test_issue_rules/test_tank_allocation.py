
import pytest
from solvers.base.issue_rules import (
    ISSUE_RULES,
    build_tank_allocation_capacity_contexts,
    build_tank_allocation_incompatibility_contexts,
    run_issue_rules,
)




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
