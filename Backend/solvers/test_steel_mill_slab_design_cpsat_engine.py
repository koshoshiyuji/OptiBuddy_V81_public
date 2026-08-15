"""
test_steel_mill_slab_design_cpsat_engine.py

SteelMillSlabDesignSolver の config.solver_engine="cpsat" 経路の構造テスト。
docplex/CPLEXがないサンドボックスでも実行できる。brute-forceで最適値を独立検証する。
"""

from __future__ import annotations

import sys
import os
import itertools

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from solvers.steel_mill_slab_design_solver import SteelMillSlabDesignSolver


def _scenario():
    orders = [
        {"id": "o1", "name": "O1", "weight": 20, "colors": ["red"]},
        {"id": "o2", "name": "O2", "weight": 15, "colors": ["blue"]},
        {"id": "o3", "name": "O3", "weight": 10, "colors": ["red"]},
        {"id": "o4", "name": "O4", "weight": 25, "colors": ["green"]},
        {"id": "o5", "name": "O5", "weight": 5,  "colors": ["blue"]},
    ]
    return {
        "problem_class": "SteelMillSlabDesign",
        "orders": orders,
        "slab_capacity": 40,
        "config": {"solver_engine": "cpsat", "time_limit_sec": 15},
        "issue_statuses": {},
    }


def brute_force_optimal(orders, slab_capacity):
    n = len(orders)
    total_weight = sum(o["weight"] for o in orders)
    best = None  # (waste, slab_count)
    # 分割の全パターン(集合分割)を生成: 各注文をどのスラブ番号(0..n-1)に置くか
    for assign in itertools.product(range(n), repeat=n):
        slabs = {}
        for i, s in enumerate(assign):
            slabs.setdefault(s, []).append(i)
        ok = True
        for s, idxs in slabs.items():
            w = sum(orders[i]["weight"] for i in idxs)
            if w > slab_capacity:
                ok = False
                break
            colors = set()
            for i in idxs:
                colors.update(orders[i]["colors"])
            if len(colors) > 2:
                ok = False
                break
        if not ok:
            continue
        slab_count = len(slabs)
        waste = slab_count * slab_capacity - total_weight
        key = (waste, slab_count)
        if best is None or key < best:
            best = key
    return best


def test_cpsat_matches_brute_force_optimum():
    solver_input = _scenario()
    result = SteelMillSlabDesignSolver(solver_input).solve()
    assert result["feasible"] is True, result["issues"]
    kpi = result["solutions"][0]["kpi"]
    cpsat_key = (kpi["total_waste_weight"], kpi["total_slabs_used"])

    expected = brute_force_optimal(solver_input["orders"], solver_input["slab_capacity"])
    assert cpsat_key == expected, f"cpsat={cpsat_key} vs brute_force={expected}"


def test_cpsat_respects_capacity_and_color_limit():
    solver_input = _scenario()
    result = SteelMillSlabDesignSolver(solver_input).solve()
    assert result["feasible"] is True
    for s in result["solutions"][0]["slabs_used"]:
        assert s["used_weight"] <= s["capacity"]
        assert len(s["colors"]) <= 2

    assignments = result["solutions"][0]["assignments"]
    assert len(assignments) == len(solver_input["orders"])


def test_unknown_engine_raises():
    solver_input = _scenario()
    solver_input["config"]["solver_engine"] = "bogus"
    result = SteelMillSlabDesignSolver(solver_input).solve()
    assert result["feasible"] is False
    assert result["issues"]


if __name__ == "__main__":
    test_cpsat_matches_brute_force_optimum()
    test_cpsat_respects_capacity_and_color_limit()
    test_unknown_engine_raises()
    print("ALL PASSED")
