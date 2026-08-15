"""
Backend/solvers/test_line_changeover_scheduler_ce_limit.py

2026-07-27追加。LineChangeoverSchedulerへのCE上限（CPLEX Community Edition
評価版のモデルサイズ上限）検知の配線テスト。

Koshoshiとの相談により、本ドメインはバッチ分割によるフォールバックを行わない
（precedences/resourcesの両方がtasksを横断するため、tasks軸で分割すると資源
容量超過をバッチ間で検知できず見過ごすリスクがあると判断。詳細は
docs/DESIGN_2026-07-26_generic_ce_limit_fallback.md決定事項#12参照）。
CE上限を検知した場合は分割を試みず、"ce_limit_unresolvable" issueを返して
graceful に終わることだけを検証する（MeetingRoom/NurseShiftWeeklyCapのような
batch_decomposer.pyは本ドメインには存在しない）。

このサンドボックスにはdocplex/CP Optimizerの実行エンジンが無いため、
CpoModel.solve()自体をモックに差し替えて検証する（本物のモデル構築・
実ソルブは検証対象外）。
"""

import sys
from pathlib import Path
from unittest.mock import patch

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

# 2026-08-13修正: docplexが未インストールの環境（cpsatロールアウト後の
# サンドボックス等）では、モジュール冒頭の無条件importがpytestの
# collectionエラー（ModuleNotFoundError）を起こし、テストスイート全体の
# 実行を止めてしまっていた。他のdocplex依存テストと同じ
# try/except + skipif パターンに揃え、docplex未インストール時は
# collectionエラーではなく明示的にskipされるようにする。
try:
    from docplex.cp.model import CpoModel
    HAS_DOCPLEX = True
except ImportError:
    HAS_DOCPLEX = False

pytestmark = pytest.mark.skipif(not HAS_DOCPLEX, reason="docplex not installed")

from solvers.line_changeover_scheduler_solver import LineChangeoverSchedulerSolver


def _scenario():
    return {
        "problem_class": "LineChangeoverScheduler",
        "meta": {"instance_name": "test"},
        "tasks": [
            {"id": "t1", "name": "task1", "duration": 5, "resource_requirements": []},
            {"id": "t2", "name": "task2", "duration": 3, "resource_requirements": []},
        ],
        "resources": [{"id": "r1", "name": "res1", "capacity": 1}],
        "precedences": [],
        # 2026-08-13: engine_select.py のデプロイ時既定値が"cpsat"に変更された
        # ため、CPO/CE-limit経路を確実に通すにはsolver_engine="cpo"を明示する。
        "config": {"time_limit_sec": 5, "horizon": 100, "solver_engine": "cpo"},
        "issue_statuses": {},
    }


def test_ce_limit_exceeded_returns_graceful_issue_without_batching():
    def _fake_solve(self, **kwargs):
        raise Exception("Problem size limit exceeded (fake test)")

    with patch.object(CpoModel, "solve", _fake_solve):
        result = LineChangeoverSchedulerSolver(_scenario()).solve()

    assert result["status"] == "ok"
    assert result["feasible"] is False
    assert "_decompose_meta" not in result  # 分割は行わない
    issue_ids = {i["id"] for i in result["issues"]}
    assert "ce_limit_unresolvable" in issue_ids


def test_non_ce_limit_exception_still_propagates():
    """CE上限以外の例外は握り潰さず、そのまま呼び出し元に伝播すること
    （HospitalShiftPlanner bug#1の教訓: 例外を安易に飲み込まない）。"""
    def _fake_solve(self, **kwargs):
        raise RuntimeError("some unrelated docplex error")

    with patch.object(CpoModel, "solve", _fake_solve):
        try:
            LineChangeoverSchedulerSolver(_scenario()).solve()
            assert False, "例外が伝播しませんでした"
        except RuntimeError as e:
            assert "unrelated" in str(e)


if __name__ == "__main__":
    import pytest
    raise SystemExit(pytest.main([__file__, "-v"]))
