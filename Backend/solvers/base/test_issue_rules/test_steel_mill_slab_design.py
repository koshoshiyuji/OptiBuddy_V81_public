
import pytest
from solvers.base.issue_rules import (
    ISSUE_RULES,
    build_steel_mill_slab_design_contexts,
    run_issue_rules,
)




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
