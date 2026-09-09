
import pytest
from solvers.base.issue_rules import (
    ISSUE_RULES,
    build_inventory_replenishment_planner_contexts,
    run_issue_rules,
)




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
