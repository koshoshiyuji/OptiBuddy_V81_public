"""
Backend/solvers/base/test_engine_availability.py

2026-09-29追加（V1修正 第2弾）: cpoptimizer（CP Optimizer実行ファイル）の検出、
生成ソルバーのCPO使用判定、CPO無し時のエラー表示のテスト。
"""

import logging
import os
import stat
import sys

import pytest

from solvers.base import engine_availability as ea
from solvers.base.solver_error_result import build_solver_crash_issue


def _fake_exec(path):
    path.write_text("#!/bin/sh\n")
    path.chmod(path.stat().st_mode | stat.S_IXUSR)
    return path


@pytest.fixture
def no_cpo_anywhere(monkeypatch, tmp_path):
    """PATH・docplex設定・Python横のbinのどこにもcpoptimizerが無い状態にする。"""
    monkeypatch.delenv("CPOPTIMIZER_PATH", raising=False)
    monkeypatch.setenv("PATH", str(tmp_path / "empty"))
    monkeypatch.setattr(sys, "executable", str(tmp_path / "bin" / "python"))
    from docplex.cp.config import context
    monkeypatch.setattr(context.solver.local, "execfile", "cpoptimizer")
    return tmp_path


def test_not_found_returns_none(no_cpo_anywhere):
    assert ea.find_cpoptimizer() is None
    assert ea.cpo_available() is False
    assert ea.configure_cpo_execfile() is None


def test_env_var_wins(no_cpo_anywhere, monkeypatch):
    exe = _fake_exec(no_cpo_anywhere / "my_cpoptimizer")
    monkeypatch.setenv("CPOPTIMIZER_PATH", str(exe))
    assert ea.find_cpoptimizer() == str(exe)
    assert ea.cpo_available() is True


def test_bad_env_var_falls_through(no_cpo_anywhere, monkeypatch):
    monkeypatch.setenv("CPOPTIMIZER_PATH", str(no_cpo_anywhere / "missing"))
    assert ea.find_cpoptimizer() is None


def test_found_beside_python_and_configured_into_docplex(no_cpo_anywhere):
    """venvを有効化せずに起動した場合（PATHに無い）でも、Python横のbinから見つけ、
    docplexのexecfileへ絶対パスを設定すること。"""
    bindir = no_cpo_anywhere / "bin"
    bindir.mkdir()
    exe = _fake_exec(bindir / "cpoptimizer")
    assert ea.find_cpoptimizer() == str(exe)
    assert ea.configure_cpo_execfile() == str(exe)
    from docplex.cp.config import context
    assert context.solver.local.execfile == str(exe)


def test_found_on_path(no_cpo_anywhere, monkeypatch):
    pathdir = no_cpo_anywhere / "onpath"
    pathdir.mkdir()
    exe = _fake_exec(pathdir / "cpoptimizer")
    monkeypatch.setenv("PATH", str(pathdir))
    assert ea.find_cpoptimizer() == str(exe)


def test_generated_solver_uses_cpo():
    cp_diff = {"path": "Backend/solvers/new_dom_solver.py",
               "new_content": "from docplex.cp.model import CpoModel\n"}
    mip_diff = {"path": "Backend/solvers/new_dom_solver.py",
                "new_content": "from docplex.mp.model import Model\n"}
    other = {"path": "Backend/dsl_transformer/new_dom_converter.py",
             "new_content": "# docplex.cp の説明コメント\n"}
    assert ea.generated_solver_uses_cpo([other, cp_diff], "new_dom") is True
    assert ea.generated_solver_uses_cpo([other, mip_diff], "new_dom") is False
    assert ea.generated_solver_uses_cpo([other], "new_dom") is False


def test_warn_if_cpo_only_under_oss(tmp_path, monkeypatch, caplog):
    cpo_only = tmp_path / "a_solver.py"
    cpo_only.write_text("from docplex.cp.model import CpoModel\n")
    dual = tmp_path / "b_solver.py"
    dual.write_text("from docplex.cp.model import CpoModel\nimport cpmpy as cp\n")

    class _M:
        def __init__(self, f):
            self.__file__ = str(f)
            self.__name__ = os.path.basename(str(f))

    monkeypatch.delenv("DEFAULT_SOLVER_ENGINE", raising=False)
    monkeypatch.setenv("SOLVER_BACKEND", "oss")
    with caplog.at_level(logging.WARNING, logger="solvers.base.engine_availability"):
        ea.warn_if_cpo_only_under_oss(_M(cpo_only))
        ea.warn_if_cpo_only_under_oss(_M(dual))
    msgs = [r.getMessage() for r in caplog.records]
    assert len(msgs) == 1 and "a_solver.py" in msgs[0]

    caplog.clear()
    monkeypatch.setenv("SOLVER_BACKEND", "cplex")
    with caplog.at_level(logging.WARNING, logger="solvers.base.engine_availability"):
        ea.warn_if_cpo_only_under_oss(_M(cpo_only))
    assert not caplog.records


def test_crash_issue_for_missing_cpoptimizer_is_engine_message():
    from docplex.cp.utils import CpoException
    issue = build_solver_crash_issue(CpoException("Executable file 'cpoptimizer' does not exists"))
    assert issue["id"] == "solve_failed"
    assert "ソルバーエンジンが利用できません" in issue["title"]
    assert "requirements-cplex.txt" in issue["message"]
    other = build_solver_crash_issue(ValueError("x"))
    assert "不具合" in other["title"]
