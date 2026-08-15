"""
Backend/solvers/base/test_ce_limit_mip_fallback.py

ce_limit_mip_fallback.py（CPLEX MIP無料版の上限→HiGHSフォールバック）の
ユニットテスト。実際のdocplex.mp（cplexパッケージ）とhighspyを使う
（CP Optimizerと違い、docplex.mp+cplexパッケージの組み合わせはpip環境だけで
実際にソルブまで動くため、モックなしで実挙動を検証できる）。

実行方法:
    cd Backend
    python -m pytest solvers/base/test_ce_limit_mip_fallback.py -v

前提: `pip install docplex cplex highspy` が必要（無料版で十分）。
"""

import pytest

docplex_mp = pytest.importorskip("docplex.mp.model")
pytest.importorskip("highspy")

from docplex.mp.model import Model

from solvers.base.ce_limit_mip_fallback import solve_with_ce_fallback


def test_small_model_solves_normally_without_fallback():
    """CE上限未満のモデルは、通常通りmdl.solve()の結果がそのまま返ること。"""
    mdl = Model(name="small")
    x = mdl.binary_var(name="x")
    y = mdl.binary_var(name="y")
    mdl.add_constraint(x + y <= 1)
    mdl.maximize(x + y)

    sol = solve_with_ce_fallback(mdl, log_output=False)

    assert sol is not None
    assert sol.get_value(x) + sol.get_value(y) == 1


def test_oversized_model_falls_back_to_highs_and_solves():
    """
    CPLEX無料版の上限（1000変数/1000制約）を超えるモデルで、
    実際にHiGHSへフォールバックし正しい解が得られること。
    """
    mdl = Model(name="big")
    xs = [mdl.binary_var(name=f"x{i}") for i in range(1100)]
    mdl.add_constraint(mdl.sum(xs) <= 500)
    mdl.maximize(mdl.sum(xs))

    sol = solve_with_ce_fallback(mdl, log_output=False)

    assert sol is not None
    total = sum(sol.get_value(x) for x in xs)
    assert total == 500
    # 呼び出し元の既存コードと同じ書き方（sol.get_value(var)）がそのまま
    # 使えることの確認 = ドメイン側の変更が不要であることの裏付け。
    assert sol.get_value(xs[0]) in (0.0, 1.0)


def test_oversized_infeasible_model_returns_none_cleanly():
    """
    フォールバック後も解けない（infeasible）場合、例外を投げずNoneを返すこと
    （mdl.solve()自体がNoneを返す既存の挙動と合わせる）。
    """
    mdl = Model(name="big_infeasible")
    xs = [mdl.binary_var(name=f"x{i}") for i in range(1100)]
    mdl.add_constraint(mdl.sum(xs) >= 2000)  # 1100個の0/1変数の和が2000に達することは不可能
    mdl.maximize(mdl.sum(xs))

    sol = solve_with_ce_fallback(mdl, log_output=False)

    assert sol is None


def test_oversized_model_with_hyphenated_names_recovers_correct_values():
    """
    変数名にLP形式で無効な文字（ハイフン、日付文字列等）を含むオーバーサイズ
    モデルでも、フォールバック後に正しい変数値が復元されること。

    2026-08-09、MysteryShopperSchedulerで実際に発生した不具合の再現テスト:
    変数名が "x_ST001_v1_S001_2026-08-04" のようにハイフンを含む場合、
    export_as_lp()がCPLEXのLPライターを経由する際に名前をサニタイズ/別名化する
    ことがあり、名前ベースの解復元（旧実装）だと該当変数の値が0のまま欠落する。
    HiGHS自身は正しい最適解（目的関数値）を見つけているにもかかわらず、
    sol.get_value(var)が誤って0を返す、というサイレントな不具合だった。
    """
    mdl = Model(name="hyphenated")
    xs = [
        mdl.binary_var(name=f"x_v{i}_s1_2026-08-{(i % 28) + 1:02d}")
        for i in range(1100)
    ]
    mdl.add_constraint(mdl.sum(xs) <= 500)
    mdl.maximize(mdl.sum(xs))

    sol = solve_with_ce_fallback(mdl, log_output=False)

    assert sol is not None
    assert sol.get_objective_value() == 500
    total = sum(sol.get_value(x) for x in xs)
    # 旧実装のバグでは、名前不一致により var_value_map からハイフン付き変数が
    # 欠落し total == 0 になっていた。位置ベース対応により、目的関数値と
    # 個々の変数値抽出の合計が一致することを確認する。
    assert total == 500


def test_non_ce_limit_exceptions_are_not_swallowed():
    """
    CE上限以外の例外（モデル自体の記述ミス等）は、フォールバックせずそのまま
    再送出すること（is_ce_limit_exceeded()で無関係の例外まで握りつぶさない）。
    """
    class _FakeModel:
        def solve(self, **kwargs):
            raise ValueError("これはCE上限とは無関係のエラー")

    with pytest.raises(ValueError, match="CE上限とは無関係"):
        solve_with_ce_fallback(_FakeModel(), log_output=False)
