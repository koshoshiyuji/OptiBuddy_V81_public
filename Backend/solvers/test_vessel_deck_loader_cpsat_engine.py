"""
test_vessel_deck_loader_cpsat_engine.py

VesselDeckLoaderSolver の config.solver_engine="cpsat" 経路の構造テスト。
docplex/CPLEXがないサンドボックスでも実行できる。制約充足の独立検証を行う。
"""

from __future__ import annotations

import sys
import os

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from solvers.vessel_deck_loader_solver import VesselDeckLoaderSolver


def _scenario():
    containers = [
        {"id": "c1", "name": "C1", "length": 4, "width": 2, "load_order": 1, "is_hazardous": False},
        {"id": "c2", "name": "C2", "length": 3, "width": 2, "load_order": 2, "is_hazardous": False},
        {"id": "c3", "name": "C3", "length": 2, "width": 2, "load_order": 3, "is_hazardous": True},
        {"id": "c4", "name": "C4", "length": 2, "width": 2, "load_order": 4, "is_hazardous": True},
    ]
    deck = {"width": 6, "max_length": 20}
    return {
        "problem_class": "VesselDeckLoader",
        "containers": containers,
        "deck": deck,
        "config": {"solver_engine": "cpsat", "time_limit_sec": 15, "hazard_margin": 1},
        "issue_statuses": {},
        "meta": {},
    }


def _rects_overlap(p1, p2):
    return not (
        p1["x"] + p1["length"] <= p2["x"] or
        p2["x"] + p2["length"] <= p1["x"] or
        p1["y"] + p1["width"] <= p2["y"] or
        p2["y"] + p2["width"] <= p1["y"]
    )


def test_cpsat_no_overlap_and_within_deck():
    solver_input = _scenario()
    result = VesselDeckLoaderSolver(solver_input).solve()
    assert result["feasible"] is True, result["issues"]
    placements = result["solutions"][0]["placements"]
    deck_width = solver_input["deck"]["width"]

    assert len(placements) == len(solver_input["containers"])

    for p in placements:
        assert p["y"] + p["width"] <= deck_width, f"甲板幅超過: {p}"
        assert p["x"] >= 0 and p["y"] >= 0

    for i in range(len(placements)):
        for j in range(i + 1, len(placements)):
            assert not _rects_overlap(placements[i], placements[j]), \
                f"重複検出: {placements[i]} vs {placements[j]}"

    # ハザードマージン検証
    hazardous = [p for p in placements if p["is_hazardous"]]
    mg = solver_input["config"]["hazard_margin"]
    for i in range(len(hazardous)):
        for j in range(i + 1, len(hazardous)):
            pi, pj = hazardous[i], hazardous[j]
            x_sep = (pi["x"] + pi["length"] + mg <= pj["x"] or pj["x"] + pj["length"] + mg <= pi["x"])
            y_sep = (pi["y"] + pi["width"] + mg <= pj["y"] or pj["y"] + pj["width"] + mg <= pi["y"])
            assert x_sep or y_sep, f"危険物マージン不足: {pi} vs {pj}"

    # issuesにcritical違反が含まれていないこと
    for iss in result["issues"]:
        assert iss["severity"] != "CRITICAL", iss


def test_cpsat_kpi_shape():
    solver_input = _scenario()
    result = VesselDeckLoaderSolver(solver_input).solve()
    assert result["feasible"] is True
    kpi = result["solutions"][0]["kpi"]
    assert "used_length" in kpi
    assert kpi["container_count"] == len(solver_input["containers"])
    assert kpi["hazardous_count"] == 2


def test_unknown_engine_raises():
    solver_input = _scenario()
    solver_input["config"]["solver_engine"] = "bogus"
    result = VesselDeckLoaderSolver(solver_input).solve()
    assert result["feasible"] is False
    assert result["issues"]


if __name__ == "__main__":
    test_cpsat_no_overlap_and_within_deck()
    test_cpsat_kpi_shape()
    test_unknown_engine_raises()
    print("ALL PASSED")
