"""
test_solution_checker.py
=========================

solvers/base/solution_checker.py のユニットテスト。外部依存なし（docplex不要）。
DESIGN_2026-07-21_solution_checker_and_time_cost_tradeoff.md の実装検証。

実行方法:
    cd Backend
    python -m pytest solvers/base/test_solution_checker.py -v
"""

import pytest
from solvers.base.solution_checker import (
    COST_O_N, COST_O_NLOGN, COST_O_N2, COST_O_NM,
    ASYNC_THRESHOLD_O_N2,
    SOLVER_BUG_CATEGORY,
    is_deferred_to_async,
    run_or_defer,
    solver_bug_issue,
    sweep_peak_usage,
)


class TestIsDeferredToAsync:
    def test_o_n_always_sync(self):
        assert is_deferred_to_async(COST_O_N, 10**9) is False

    def test_o_nlogn_always_sync(self):
        assert is_deferred_to_async(COST_O_NLOGN, 10**9) is False

    def test_o_n2_below_threshold_is_sync(self):
        assert is_deferred_to_async(COST_O_N2, ASYNC_THRESHOLD_O_N2 - 1) is False

    def test_o_n2_above_threshold_is_async(self):
        assert is_deferred_to_async(COST_O_N2, ASYNC_THRESHOLD_O_N2 + 1) is True

    def test_o_nm_is_always_deferred_in_production(self):
        """3-3節: 組合せ的爆発系は本番solve時の対象外"""
        assert is_deferred_to_async(COST_O_NM, 1) is True

    def test_full_check_forces_sync_regardless_of_size(self):
        """3-1節: Gate2登録時はfull_check=Trueで閾値を無視して常に同期"""
        assert is_deferred_to_async(COST_O_N2, 10**9, full_check=True) is False
        assert is_deferred_to_async(COST_O_NM, 1, full_check=True) is False


class TestRunOrDefer:
    def test_sync_runs_check_fn_immediately(self):
        calls = []
        def check_fn():
            calls.append(1)
            return [{"id": "x"}]
        issues, deferred = run_or_defer(COST_O_N, 10**9, check_fn)
        assert issues == [{"id": "x"}]
        assert deferred is None
        assert calls == [1]

    def test_async_does_not_run_check_fn(self):
        calls = []
        def check_fn():
            calls.append(1)
            return [{"id": "x"}]
        issues, deferred = run_or_defer(
            COST_O_N2, ASYNC_THRESHOLD_O_N2 + 1, check_fn,
            domain="TestDomain", check_id="test_check",
        )
        assert issues == []
        assert calls == []  # 呼ばれていない
        assert deferred is not None
        assert deferred["domain"] == "TestDomain"
        assert deferred["check_id"] == "test_check"
        assert deferred["run"] is check_fn

    def test_full_check_runs_sync_even_above_threshold(self):
        issues, deferred = run_or_defer(
            COST_O_N2, ASYNC_THRESHOLD_O_N2 + 1, lambda: [{"id": "y"}],
            full_check=True,
        )
        assert issues == [{"id": "y"}]
        assert deferred is None

    def test_o_nm_in_production_returns_nothing(self):
        """3-3節: 組合せ的爆発系は本番solve時は非同期にすらせず、何もしない"""
        calls = []
        def check_fn():
            calls.append(1)
            return [{"id": "z"}]
        issues, deferred = run_or_defer(COST_O_NM, 1, check_fn)
        assert issues == []
        assert deferred is None
        assert calls == []


class TestSolverBugIssue:
    def test_category_is_solver(self):
        issue = solver_bug_issue("id1", "title1", "message1")
        assert issue["category"] == SOLVER_BUG_CATEGORY == "SOLVER"
        assert issue["severity"] == "CRITICAL"
        assert issue["id"] == "id1"

    def test_related_ids_default_empty(self):
        issue = solver_bug_issue("id1", "title1", "message1")
        assert issue["relatedContainerIds"] == []

    def test_extra_fields_merged(self):
        issue = solver_bug_issue("id1", "t", "m", extra={"foo": "bar"})
        assert issue["foo"] == "bar"


class TestSweepPeakUsage:
    def test_empty_events(self):
        peak, peak_time = sweep_peak_usage([])
        assert peak == 0
        assert peak_time is None

    def test_no_overlap_peak_is_single_amount(self):
        # [0,100) 使用量1, [100,200) 使用量1 → 重ならないのでピークは1
        events = [(0, 1), (100, -1), (100, 1), (200, -1)]
        peak, peak_time = sweep_peak_usage(events)
        assert peak == 1

    def test_overlap_peak_is_sum(self):
        # [0,100)と[50,150)が重なる → 50-100の間はピーク2
        events = [(0, 1), (100, -1), (50, 1), (150, -1)]
        peak, peak_time = sweep_peak_usage(events)
        assert peak == 2
        assert peak_time == 50

    def test_touching_boundary_not_double_counted(self):
        """同時刻の終了(-)と開始(+)では終了を先に処理し、瞬間的な超過を誤検知しない"""
        events = [(0, 1), (100, -1), (100, 1), (200, -1)]
        peak, _ = sweep_peak_usage(events)
        assert peak == 1  # 2にならない
