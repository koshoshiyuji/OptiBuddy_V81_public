
import pytest
from solvers.base.issue_rules import (
    build_store_site_contexts,
    run_issue_rules,
)




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
