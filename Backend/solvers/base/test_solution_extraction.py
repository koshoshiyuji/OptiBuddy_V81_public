"""
test_solution_extraction.py
=============================

solution_extraction.py のユニットテスト。docplex非依存の純粋ロジックのみなので
docplexのインストール有無に関わらず常に実行される（safe_objective_valueは
msol相当のスタブオブジェクトでテストする）。

実行方法:
    cd Backend
    python -m pytest solvers/base/test_solution_extraction.py -v
"""

import pytest

from solvers.base.solution_extraction import (
    extract_makespan,
    safe_objective_value,
    extract_optimality_metadata,
)


# ---------------------------------------------------------------------------
# extract_makespan
# ---------------------------------------------------------------------------

def test_extract_makespan_returns_max_end():
    schedule = [
        {"task_id": "t1", "start": 0, "end": 30},
        {"task_id": "t2", "start": 10, "end": 90},
        {"task_id": "t3", "start": 20, "end": 50},
    ]
    assert extract_makespan(schedule) == 90


def test_extract_makespan_empty_schedule_returns_zero():
    assert extract_makespan([]) == 0


def test_extract_makespan_custom_end_key():
    schedule = [{"finish_min": 15}, {"finish_min": 45}]
    assert extract_makespan(schedule, end_key="finish_min") == 45


def test_extract_makespan_missing_key_defaults_to_zero():
    schedule = [{"task_id": "t1"}, {"task_id": "t2", "end": 20}]
    assert extract_makespan(schedule) == 20


def test_extract_makespan_returns_int_even_if_input_is_float():
    schedule = [{"end": 12.7}]
    result = extract_makespan(schedule)
    assert result == 12
    assert isinstance(result, int)


# ---------------------------------------------------------------------------
# safe_objective_value
# ---------------------------------------------------------------------------

class _MsolStub:
    def __init__(self, value=None, raises=False):
        self._value = value
        self._raises = raises

    def get_objective_value(self):
        if self._raises:
            raise RuntimeError("no objective in this solution")
        return self._value


def test_safe_objective_value_returns_actual_value_when_available():
    msol = _MsolStub(value=42.0)
    assert safe_objective_value(msol, fallback=0.0) == 42.0


def test_safe_objective_value_falls_back_when_none():
    msol = _MsolStub(value=None)
    assert safe_objective_value(msol, fallback=99.0) == 99.0


def test_safe_objective_value_falls_back_on_exception():
    msol = _MsolStub(raises=True)
    assert safe_objective_value(msol, fallback=77.0) == 77.0


def test_safe_objective_value_returns_float_type():
    msol = _MsolStub(value=10)
    result = safe_objective_value(msol, fallback=0.0)
    assert isinstance(result, float)


# ---------------------------------------------------------------------------
# extract_optimality_metadata（2026-07-22追加）
# ---------------------------------------------------------------------------

class _MsolOptimalityStub:
    def __init__(self, status=None, optimal=False, solve_time=None,
                 raise_status=False, raise_optimal=False, raise_time=False):
        self._status = status
        self._optimal = optimal
        self._solve_time = solve_time
        self._raise_status = raise_status
        self._raise_optimal = raise_optimal
        self._raise_time = raise_time

    def get_solve_status(self):
        if self._raise_status:
            raise RuntimeError("status unavailable")
        return self._status

    def is_solution_optimal(self):
        if self._raise_optimal:
            raise RuntimeError("optimal check unavailable")
        return self._optimal

    def get_solve_time(self):
        if self._raise_time:
            raise RuntimeError("solve time unavailable")
        return self._solve_time


def test_optimal_solution_reports_optimal_true():
    msol = _MsolOptimalityStub(status="Optimal", optimal=True, solve_time=1.23)
    result = extract_optimality_metadata(msol)
    assert result == {"solve_status": "Optimal", "is_optimal": True, "solve_time_sec": 1.23}


def test_time_limit_truncated_feasible_reports_optimal_false():
    """TimeLimitで打ち切られた実行可能解（最適性未証明）の典型ケース"""
    msol = _MsolOptimalityStub(status="Feasible", optimal=False, solve_time=30.0)
    result = extract_optimality_metadata(msol)
    assert result["solve_status"] == "Feasible"
    assert result["is_optimal"] is False


def test_infeasible_status_is_reported_not_swallowed():
    msol = _MsolOptimalityStub(status="Infeasible", optimal=False, solve_time=0.5)
    result = extract_optimality_metadata(msol)
    assert result["solve_status"] == "Infeasible"
    assert result["is_optimal"] is False


def test_none_msol_returns_defaults_without_raising():
    result = extract_optimality_metadata(None)
    assert result == {"solve_status": None, "is_optimal": False, "solve_time_sec": None}


def test_missing_api_methods_do_not_raise():
    """docplex.mp等、CP以外のsolve結果オブジェクトを渡しても例外を投げない"""
    class _NoOptimalityApi:
        pass

    result = extract_optimality_metadata(_NoOptimalityApi())
    assert result == {"solve_status": None, "is_optimal": False, "solve_time_sec": None}


def test_partial_api_failure_still_returns_other_fields():
    """一部のAPI呼び出しだけ例外を投げても、取得できたフィールドは返す"""
    msol = _MsolOptimalityStub(status="Optimal", raise_optimal=True, solve_time=2.0)
    result = extract_optimality_metadata(msol)
    assert result["solve_status"] == "Optimal"
    assert result["is_optimal"] is False  # 例外時のデフォルト
    assert result["solve_time_sec"] == 2.0
