"""
test_constraint_applier.py
============================

BaseConstraintApplier のユニットテスト（特に2026-07-18追加の cumulative ハンドラ）。
CP Optimizer エンジンなしで動作する（モデル構築までを検証）。

実行方法:
    cd Backend
    python -m pytest solvers/base/test_constraint_applier.py -v

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
    from solvers.base.constraint_applier import BaseConstraintApplier

pytestmark = pytest.mark.skipif(not HAS_DOCPLEX, reason="docplex not installed")


@pytest.fixture
def mdl():
    return CpoModel(name="test")


@pytest.fixture
def three_task_itvs(mdl):
    """同じ時間帯に重なる3つの必須interval_var。"""
    return {
        "t1": mdl.interval_var(start=(0, 0), end=(10, 10), size=10, name="t1"),
        "t2": mdl.interval_var(start=(0, 0), end=(10, 10), size=10, name="t2"),
        "t3": mdl.interval_var(start=(0, 0), end=(10, 10), size=10, name="t3"),
    }


# ---------------------------------------------------------------------------
# no_overlap（既存ハンドラの回帰確認）
# ---------------------------------------------------------------------------

def test_no_overlap_adds_constraint(mdl, three_task_itvs):
    applier = BaseConstraintApplier(mdl, three_task_itvs)
    before = len(mdl.get_all_expressions())
    applier.apply_all([{"type": "no_overlap", "params": {"task_ids": ["t1", "t2", "t3"]}}])
    after = len(mdl.get_all_expressions())
    assert after > before


def test_no_overlap_single_task_noop(mdl, three_task_itvs):
    """1件だけの場合は制約を追加しない（重複しようがないため）。"""
    applier = BaseConstraintApplier(mdl, three_task_itvs)
    before = len(mdl.get_all_expressions())
    applier.apply_all([{"type": "no_overlap", "params": {"task_ids": ["t1"]}}])
    after = len(mdl.get_all_expressions())
    assert after == before


# ---------------------------------------------------------------------------
# cumulative（2026-07-18新規追加）
# ---------------------------------------------------------------------------

def test_cumulative_adds_constraint(mdl, three_task_itvs):
    applier = BaseConstraintApplier(mdl, three_task_itvs)
    before = len(mdl.get_all_expressions())
    applier.apply_all([{
        "type": "cumulative",
        "params": {"task_ids": ["t1", "t2", "t3"], "capacity": 2},
    }])
    after = len(mdl.get_all_expressions())
    assert after > before


def test_cumulative_with_per_task_requirements(mdl, three_task_itvs):
    """requirements指定時、各タスクの使用量がpulseの高さに反映されること。"""
    applier = BaseConstraintApplier(mdl, three_task_itvs)
    applier.apply_all([{
        "type": "cumulative",
        "params": {
            "task_ids": ["t1", "t2", "t3"],
            "capacity": 5,
            "requirements": {"t1": 2, "t2": 3, "t3": 1},
        },
    }])
    # モデルが構文的に妥当であること（例外なく構築できること）を確認
    assert mdl.get_all_expressions()


def test_cumulative_empty_task_ids_noop(mdl, three_task_itvs):
    applier = BaseConstraintApplier(mdl, three_task_itvs)
    before = len(mdl.get_all_expressions())
    applier.apply_all([{"type": "cumulative", "params": {"task_ids": [], "capacity": 2}}])
    after = len(mdl.get_all_expressions())
    assert after == before


def test_cumulative_unresolvable_task_id_skipped(mdl, three_task_itvs):
    """存在しないtask_idはスキップされ、例外を出さないこと。"""
    applier = BaseConstraintApplier(mdl, three_task_itvs)
    applier.apply_all([{
        "type": "cumulative",
        "params": {"task_ids": ["t1", "unknown"], "capacity": 2},
    }])
    # 例外なく完了すればOK（t1のみでpulse合計を構築）


def test_cumulative_registered_in_base_handlers(mdl, three_task_itvs):
    """no_overlapとcumulativeが両方とも基底ハンドラとして登録されていること。"""
    applier = BaseConstraintApplier(mdl, three_task_itvs)
    assert "no_overlap" in applier._CONSTRAINT_HANDLERS
    assert "cumulative" in applier._CONSTRAINT_HANDLERS


def test_apply_all_reports_applied_and_skipped_counts(mdl, three_task_itvs):
    applier = BaseConstraintApplier(mdl, three_task_itvs)
    applied, skipped = applier.apply_all([
        {"type": "no_overlap", "params": {"task_ids": ["t1", "t2"]}},
        {"type": "cumulative", "params": {"task_ids": ["t2", "t3"], "capacity": 1}},
        {"type": "unknown_type", "params": {}},
    ])
    assert applied == 2
    assert skipped == 1
