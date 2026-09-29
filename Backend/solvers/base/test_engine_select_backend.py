"""
Backend/solvers/base/test_engine_select_backend.py

2026-09-29追加: SOLVER_BACKEND（cplex|oss）への設定統一のテスト
（DESIGN_2026-09-28_v2_paid_license_and_repo_split.md 8節、V1修正）。
"""

import logging

import pytest

from solvers.base import engine_select
from solvers.base.engine_select import (
    BACKEND_CPLEX, BACKEND_OSS, CPO, CPSAT, get_solver_backend, get_solver_engine,
)


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch):
    monkeypatch.delenv("SOLVER_BACKEND", raising=False)
    monkeypatch.delenv("DEFAULT_SOLVER_ENGINE", raising=False)
    monkeypatch.setattr(engine_select, "_conflict_warned", False)


def test_default_is_oss_and_cpsat():
    assert get_solver_backend() == BACKEND_OSS
    assert get_solver_engine({}) == CPSAT


@pytest.mark.parametrize("backend,engine", [("cplex", CPO), ("oss", CPSAT)])
def test_backend_decides_cp_engine(monkeypatch, backend, engine):
    monkeypatch.setenv("SOLVER_BACKEND", backend)
    assert get_solver_backend() == backend
    assert get_solver_engine({}) == engine


@pytest.mark.parametrize("legacy,backend", [("cpo", BACKEND_CPLEX), ("cpsat", BACKEND_OSS)])
def test_legacy_default_solver_engine_is_read_when_backend_unset(monkeypatch, legacy, backend):
    monkeypatch.setenv("DEFAULT_SOLVER_ENGINE", legacy)
    assert get_solver_backend() == backend
    assert get_solver_engine({}) == legacy


def test_backend_wins_over_conflicting_legacy_with_warning(monkeypatch, caplog):
    monkeypatch.setenv("SOLVER_BACKEND", "oss")
    monkeypatch.setenv("DEFAULT_SOLVER_ENGINE", "cpo")
    with caplog.at_level(logging.WARNING, logger="solvers.base.engine_select"):
        assert get_solver_backend() == BACKEND_OSS
        assert get_solver_engine({}) == CPSAT
    assert any("食い違っています" in r.getMessage() for r in caplog.records)


def test_consistent_legacy_does_not_warn(monkeypatch, caplog):
    monkeypatch.setenv("SOLVER_BACKEND", "oss")
    monkeypatch.setenv("DEFAULT_SOLVER_ENGINE", "cpsat")
    with caplog.at_level(logging.WARNING, logger="solvers.base.engine_select"):
        assert get_solver_backend() == BACKEND_OSS
    assert not caplog.records


def test_config_solver_engine_still_wins(monkeypatch):
    monkeypatch.setenv("SOLVER_BACKEND", "oss")
    assert get_solver_engine({"solver_engine": "cpo"}) == CPO


def test_unknown_backend_raises(monkeypatch):
    monkeypatch.setenv("SOLVER_BACKEND", "gurobi")
    with pytest.raises(ValueError, match="SOLVER_BACKEND"):
        get_solver_backend()


def test_unknown_legacy_raises_when_backend_unset(monkeypatch):
    monkeypatch.setenv("DEFAULT_SOLVER_ENGINE", "bogus")
    with pytest.raises(ValueError, match="DEFAULT_SOLVER_ENGINE"):
        get_solver_engine({})
