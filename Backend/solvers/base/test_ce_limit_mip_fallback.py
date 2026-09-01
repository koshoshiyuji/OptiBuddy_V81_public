"""
Backend/solvers/base/test_ce_limit_mip_fallback.py

ce_limit_mip_fallback.py（CPLEX MIP無料版の上限→HiGHSフォールバック）の
ユニットテスト。実際のdocplex.mp（cplexパッケージ）とortools（内蔵HiGHS
バックエンド、model_builder.Solver("HIGHS")）を使う（CP Optimizerと違い、
docplex.mp+cplexパッケージの組み合わせはpip環境だけで実際にソルブまで
動くため、モックなしで実挙動を検証できる）。

2026-09-01: フォールバック先を単体`highspy`パッケージからortools内蔵の
HiGHSバックエンドへ切替（同一プロセス内で単体highspyとortools(CP-SAT等)を
両方読み込むとネイティブ側のシンボル衝突/segfaultを起こすことが判明した
ため。ce_limit_mip_fallback.py冒頭の【2026-09-01の変更】参照）。

実行方法:
    cd Backend
    python -m pytest solvers/base/test_ce_limit_mip_fallback.py -v

前提: `pip install docplex cplex ortools` が必要（cplexは無料版で十分）。
"""

import pytest

docplex_mp = pytest.importorskip("docplex.mp.model")
pytest.importorskip("ortools.linear_solver.python.model_builder")

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
    （当時の実装の）export_as_lp()がCPLEXのLPライターを経由する際に名前を
    サニタイズ/別名化することがあり、名前ベースの解復元だと該当変数の値が
    0のまま欠落していた。HiGHS自身は正しい最適解（目的関数値）を見つけて
    いるにもかかわらず、sol.get_value(var)が誤って0を返す、というサイレント
    な不具合だった。2026-09-01のMPS(free format)への切替後は、この種の
    名前サニタイズが起きないことを前提に、名前ベースの対応を主経路として
    使っている（このテストはその前提が引き続き成立していることの確認）。
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
    # 旧実装（highspy+LP形式）のバグでは、名前不一致により var_value_map から
    # ハイフン付き変数が欠落し total == 0 になっていた。MPS形式+名前ベース
    # 対応（2026-09-01〜）により、目的関数値と個々の変数値抽出の合計が
    # 一致することを確認する。
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


def test_repeated_fallback_calls_on_same_model_with_new_variables_added_between_calls():
    """
    2026-08-31、MysteryShopperSchedulerで実際に発生した不具合の再現テスト:
    同一のmdlに対してsolve_with_ce_fallback()を複数回呼び、呼び出しの間に
    新しい制約・新しい変数を追加するケース（lexicographic多段階solveと同じ
    使い方）でも、2回目以降の呼び出しで正しい値に復元されること。

    背景: 当時の実装（highspy+LP形式、位置ベース優先・名前ベースに
    フォールバック）の変数値復元は、docplexのiter_variables()の順序とHiGHSが
    読み込んだLPファイルの列順序が一致している前提に依存していた。この前提が
    崩れると「値が消える」のではなく「別の変数の値と取り違える」形の
    サイレントな不具合になり、実機のMysteryShopperSchedulerで訪問枠の
    重複割当という制約違反が検知されずに返っていた。2026-08-31の修正で、
    SolveSolution構築後にis_valid_solution()で自己検証するようにした。
    2026-09-01にフォールバック先自体をortools内蔵HiGHS+MPS形式+名前ベース
    対応へ切り替えた後も、この自己検証は変わらず有効な安全網として残して
    いる。このテストは、複数回のフォールバック呼び出し（間でモデルが変化する
    ケース）でも実際に制約を満たす解が返ることを直接確認する。
    """
    mdl = Model(name="repeated_fallback")
    xs = [mdl.binary_var(name=f"x{i}") for i in range(1100)]
    mdl.add_constraint(mdl.sum(xs) <= 500)
    mdl.maximize(mdl.sum(xs))

    sol1 = solve_with_ce_fallback(mdl, log_output=False)
    assert sol1 is not None
    best = int(round(sol1.get_objective_value()))
    assert best == 500

    # Step2: MysteryShopperSchedulerと同じパターン（充足数を下限に固定しつつ、
    # 新しい変数を追加してから別目的で再度フォールバックさせる）。
    mdl.add_constraint(mdl.sum(xs) >= best, ctname="lock")
    slack = mdl.integer_var(lb=0, ub=len(xs), name="slack")
    mdl.add_constraint(slack >= mdl.sum(xs) - best)
    mdl.minimize(slack)

    sol2 = solve_with_ce_fallback(mdl, log_output=False)
    assert sol2 is not None

    # 制約充足を直接検証する: 割り当てられたxの合計はbest以上、
    # slackはその超過分と一致していなければならない。
    total_x = sum(sol2.get_value(x) for x in xs)
    assert total_x >= best
    assert sol2.get_value(slack) == total_x - best
    assert sol2.is_valid_solution(tolerance=1e-6)
