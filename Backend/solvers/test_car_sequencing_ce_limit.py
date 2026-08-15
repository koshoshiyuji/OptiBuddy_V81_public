"""
Backend/solvers/test_car_sequencing_ce_limit.py

2026-07-27追加。CarSequencingへのCE上限（CPLEX Community Edition評価版の
モデルサイズ上限）検知の配線テスト。

Koshoshiとの相談により、本ドメインはバッチ分割によるフォールバックを行わない
（seq[pos]は位置ベースの単一変数列で、スライディングウィンドウ制約・同一車種
連続制約のどちらもシーケンス全体を横断するため、位置や車種を軸に分割すると
窓制約がバッチ境界をまたぐケースを正しく扱えないと判断。詳細は
docs/DESIGN_2026-07-26_generic_ce_limit_fallback.md決定事項#12参照）。
CE上限を検知した場合は分割を試みず、"ce_limit_unresolvable" issueを返して
graceful に終わることだけを検証する。

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
# 実行を止めてしまっていた。他のdocplex依存テスト
# （test_nurse_shift_weekly_cap_quantity_requirement.py等）と同じ
# try/except + skipif パターンに揃え、docplex未インストール時は
# collectionエラーではなく明示的にskipされるようにする。
try:
    from docplex.cp.model import CpoModel
    HAS_DOCPLEX = True
except ImportError:
    HAS_DOCPLEX = False

pytestmark = pytest.mark.skipif(not HAS_DOCPLEX, reason="docplex not installed")

from solvers.car_sequencing_solver import CarSequencingSolver


def _scenario():
    return {
        "problem_class": "CarSequencing",
        "meta": {"instance_name": "test"},
        "car_types": [
            {"car_type_id": "A", "car_type_name": "A", "count": 2},
            {"car_type_id": "B", "car_type_name": "B", "count": 2},
        ],
        "options": [
            {"option_id": "opt1", "option_name": "opt1", "window_size": 2,
             "max_per_window": 1, "car_type_ids": ["A"]},
        ],
        # 2026-08-13: solvers/base/engine_select.py のデプロイ時既定値が
        # "cpsat" に変更されたため、本テストが検証したいCPO/CE-limit経路を
        # 確実に通すには solver_engine="cpo" を明示する必要がある
        # (指定しなければCPSAT経路に流れ、CpoModel.solve()のモックが
        # 一切呼ばれずテストの前提が崩れる)。
        "config": {"solve_time_sec": 5, "solver_engine": "cpo"},
        "issue_statuses": {},
    }


def test_ce_limit_exceeded_returns_graceful_issue_without_batching():
    def _fake_solve(self, **kwargs):
        raise Exception("Problem size limit exceeded (fake test)")

    with patch.object(CpoModel, "solve", _fake_solve):
        result = CarSequencingSolver(_scenario()).solve()

    assert result["feasible"] is False
    issue_ids = {i["id"] for i in result["issues"]}
    assert "ce_limit_unresolvable" in issue_ids
    # CarSequencingのsolve()は例外を握り潰すtry/exceptを外側に持つが、
    # CE上限は"solver_exception"（汎用握り潰し）ではなく専用issueとして
    # 返るべき（そうでないと"ce_limit_unresolvable"にならないはず）
    assert "solver_exception" not in issue_ids


def test_non_ce_limit_exception_falls_back_to_generic_solver_exception():
    """CE上限以外の例外は、solve()の外側except（既存の握り潰し）で
    "solver_exception" issueに変換される既存の挙動を維持すること。"""
    def _fake_solve(self, **kwargs):
        raise RuntimeError("some unrelated docplex error")

    with patch.object(CpoModel, "solve", _fake_solve):
        result = CarSequencingSolver(_scenario()).solve()

    issue_ids = {i["id"] for i in result["issues"]}
    assert "solver_exception" in issue_ids
    assert "ce_limit_unresolvable" not in issue_ids


if __name__ == "__main__":
    import pytest
    raise SystemExit(pytest.main([__file__, "-v"]))
