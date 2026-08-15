"""
test_portfolio_overlap_designer_cpsat_engine.py

PortfolioOverlapDesignerSolver の config.solver_engine="cpsat" 経路の構造テスト。
docplex/CPLEXがないサンドボックスでも実行できる。brute-forceで最適値を独立検証する。
"""

from __future__ import annotations

import sys
import os
import itertools

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from solvers.portfolio_overlap_designer_solver import PortfolioOverlapDesignerSolver


def _scenario():
    funds = [
        {"id": "f1", "name": "Fund1", "required_count": 3},
        {"id": "f2", "name": "Fund2", "required_count": 3},
        {"id": "f3", "name": "Fund3", "required_count": 3},
    ]
    pool = [{"id": f"s{i}", "name": f"Stock{i}"} for i in range(1, 6)]  # 5銘柄
    return {
        "problem_class": "PortfolioOverlapDesigner",
        "funds": funds,
        "pool": pool,
        "config": {"solver_engine": "cpsat", "time_limit_sec": 10},
        "issue_statuses": {},
    }


def brute_force_worst_overlap(funds, pool):
    b = len(pool)
    stock_ids = [s["id"] for s in pool]
    best = None
    # 各ファンドの組合せ候補(required_count本選択)を全列挙し、直積の最良を探す
    combos_per_fund = []
    for f in funds:
        combos_per_fund.append(list(itertools.combinations(stock_ids, f["required_count"])))

    for combo in itertools.product(*combos_per_fund):
        worst = 0
        for i1 in range(len(funds)):
            for i2 in range(i1 + 1, len(funds)):
                overlap = len(set(combo[i1]) & set(combo[i2]))
                worst = max(worst, overlap)
        if best is None or worst < best:
            best = worst
    return best


def test_cpsat_matches_brute_force_optimum():
    solver_input = _scenario()
    result = PortfolioOverlapDesignerSolver(solver_input).solve()
    assert result["feasible"] is True, result["issues"]
    cpsat_worst = result["kpi"]["worst_overlap"]

    expected = brute_force_worst_overlap(solver_input["funds"], solver_input["pool"])
    assert cpsat_worst == expected, f"cpsat={cpsat_worst} vs brute_force={expected}"


def test_cpsat_required_counts_respected():
    solver_input = _scenario()
    result = PortfolioOverlapDesignerSolver(solver_input).solve()
    assert result["feasible"] is True
    for fs in result["solutions"][0]["fund_solutions"]:
        assert fs["selected_count"] == fs["required_count"]


def test_unknown_engine_raises():
    solver_input = _scenario()
    solver_input["config"]["solver_engine"] = "bogus"
    result = PortfolioOverlapDesignerSolver(solver_input).solve()
    assert result["feasible"] is False
    assert result["issues"]


if __name__ == "__main__":
    test_cpsat_matches_brute_force_optimum()
    test_cpsat_required_counts_respected()
    test_unknown_engine_raises()
    print("ALL PASSED")
