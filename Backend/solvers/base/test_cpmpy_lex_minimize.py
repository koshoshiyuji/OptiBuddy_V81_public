"""
test_cpmpy_lex_minimize.py — solve_lexicographic() の単体テスト。
"""

from __future__ import annotations

import sys
import os

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

import cpmpy as cp
from solvers.base.cpmpy_lex_minimize import solve_lexicographic


def test_two_stage_lex_matches_expected():
    # x+y<=10, x>=2, y>=2. 優先1: xを最小化(期待2)。優先2: yを最小化(期待2)。
    x = cp.intvar(0, 10)
    y = cp.intvar(0, 10)
    m = cp.Model([x + y <= 10, x >= 2, y >= 2])

    solved, values = solve_lexicographic(m, [x, y], solver="ortools", time_limit=10)
    assert solved
    assert values == [2, 2]


def test_lex_priority_is_respected_even_if_it_worsens_second_objective():
    # z = a - b. 優先1: aを最大化したいので -a を最小化(期待 a=5)。
    # 優先2: bを最小化(a=5に固定後の残り自由度でbの最小値)。
    a = cp.intvar(0, 5)
    b = cp.intvar(0, 5)
    m = cp.Model([a + b <= 8, a <= 5, b >= 1])

    solved, values = solve_lexicographic(m, [-a, b], solver="ortools", time_limit=10)
    assert solved
    neg_a_val, b_val = values
    assert -neg_a_val == 5  # aは最大化(5)が最優先で確保されている
    assert b_val == 1       # aが5に固定された後、bはその範囲内で最小(1)


def test_infeasible_returns_false():
    x = cp.intvar(0, 10)
    m = cp.Model([x >= 5, x <= 3])  # infeasible
    solved, values = solve_lexicographic(m, [x], solver="ortools", time_limit=5)
    assert solved is False
    assert values == []


def test_single_objective_behaves_like_normal_minimize():
    x = cp.intvar(0, 10)
    y = cp.intvar(0, 10)
    m = cp.Model([x + y == 10, x >= 3])
    solved, values = solve_lexicographic(m, [x], solver="ortools", time_limit=5)
    assert solved
    assert values == [3]


if __name__ == "__main__":
    test_two_stage_lex_matches_expected()
    test_lex_priority_is_respected_even_if_it_worsens_second_objective()
    test_infeasible_returns_false()
    test_single_objective_behaves_like_normal_minimize()
    print("ALL PASSED")
