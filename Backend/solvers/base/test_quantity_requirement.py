"""
test_quantity_requirement.py
==============================

quantity_requirement.py のユニットテスト。
CP Optimizer エンジンなしで動作する（モデル構築までを検証）。

実行方法:
    cd Backend
    python -m pytest solvers/base/test_quantity_requirement.py -v

前提:
    - docplex がインストール済み
"""

import pytest

try:
    from docplex.cp.model import CpoModel
    HAS_DOCPLEX = True
except ImportError:
    HAS_DOCPLEX = False

if HAS_DOCPLEX:
    from solvers.base.quantity_requirement import (
        DEFAULT_QUANTITY_REQUIREMENT_MODE,
        apply_quantity_requirement,
    )

pytestmark = pytest.mark.skipif(not HAS_DOCPLEX, reason="docplex not installed")


@pytest.fixture
def mdl():
    return CpoModel(name="test")


@pytest.fixture
def three_present_vars(mdl):
    return [mdl.binary_var(name=f"p{i}") for i in range(3)]


# ---------------------------------------------------------------------------
# 既定値
# ---------------------------------------------------------------------------

def test_default_mode_is_soft():
    """ヒアリングシート§4-1の既定値（未記入時はb=欠員許容）と一致すること。"""
    assert DEFAULT_QUANTITY_REQUIREMENT_MODE == "soft"


# ---------------------------------------------------------------------------
# soft モード
# ---------------------------------------------------------------------------

def test_soft_mode_returns_shortfall_expr_and_adds_no_hard_constraint(mdl, three_present_vars):
    before = len(mdl.get_all_expressions())
    shortfall = apply_quantity_requirement(mdl, three_present_vars, required_count=2, mode="soft")
    after = len(mdl.get_all_expressions())

    assert shortfall is not None
    # mdl.max()自体は式を構築するだけでadd()していないため、
    # apply_quantity_requirement呼び出しだけではモデルに制約は追加されない。
    assert after == before


def test_soft_mode_shortfall_can_be_used_as_objective_term(mdl, three_present_vars):
    """呼び出し側が shortfall を目的関数のペナルティ項として使えること。"""
    shortfall = apply_quantity_requirement(mdl, three_present_vars, required_count=2, mode="soft")
    penalty_weight = 100
    mdl.add(mdl.minimize(penalty_weight * shortfall))
    assert mdl.get_all_expressions()


def test_soft_mode_empty_present_vars(mdl):
    """候補が0件でも例外を出さないこと（assigned_count=0扱い）。"""
    shortfall = apply_quantity_requirement(mdl, [], required_count=3, mode="soft")
    assert shortfall is not None


# ---------------------------------------------------------------------------
# hard モード
# ---------------------------------------------------------------------------

def test_hard_mode_at_least_adds_ge_constraint_and_returns_none(mdl, three_present_vars):
    before = len(mdl.get_all_expressions())
    result = apply_quantity_requirement(
        mdl, three_present_vars, required_count=2, mode="hard", comparison="at_least"
    )
    after = len(mdl.get_all_expressions())

    assert result is None
    assert after > before  # ハード制約がmdlに追加されている


def test_hard_mode_exact_adds_eq_constraint(mdl, three_present_vars):
    before = len(mdl.get_all_expressions())
    result = apply_quantity_requirement(
        mdl, three_present_vars, required_count=2, mode="hard", comparison="exact"
    )
    after = len(mdl.get_all_expressions())

    assert result is None
    assert after > before


def test_hard_mode_default_comparison_is_at_least_not_exact(mdl, three_present_vars):
    """
    旧バグ（無条件でexact equality、過剰配置まで禁止していた）を再現しないよう、
    comparison省略時は 'at_least' が既定であることを明示的に確認する。
    """
    import inspect
    sig = inspect.signature(apply_quantity_requirement)
    assert sig.parameters["comparison"].default == "at_least"


# ---------------------------------------------------------------------------
# バリデーション
# ---------------------------------------------------------------------------

def test_unknown_mode_raises(mdl, three_present_vars):
    with pytest.raises(ValueError):
        apply_quantity_requirement(mdl, three_present_vars, required_count=2, mode="maybe")


def test_unknown_comparison_raises(mdl, three_present_vars):
    with pytest.raises(ValueError):
        apply_quantity_requirement(
            mdl, three_present_vars, required_count=2, mode="hard", comparison="approximately"
        )
