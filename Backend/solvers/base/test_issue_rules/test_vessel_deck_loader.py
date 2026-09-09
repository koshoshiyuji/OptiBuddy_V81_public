
import pytest
from solvers.base.issue_rules import (
    ISSUE_RULES,
    build_vessel_deck_loader_contexts,
    run_issue_rules,
)




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
