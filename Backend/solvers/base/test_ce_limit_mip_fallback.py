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

2026-09-29: SOLVER_BACKEND対応（フォールバック先をSCIPへ、MPS経由を廃止。
ce_limit_mip_fallback.py冒頭の【2026-09-29の変更】参照）。docplexが基本インストール
（requirements.txt）に入ったため、CPLEX本体（cplexパッケージ）が無いCI環境でも
本ファイルが実行される。既存テストは `backend` フィクスチャで
SOLVER_BACKEND=oss（常に実行）と cplex（CPLEX本体がある環境のみ実行。CE上限を
超えるモデルでSCIPへのフォールバックを検証）の両方で実行する。
"""

import random

import pytest

docplex_mp = pytest.importorskip("docplex.mp.model")
pytest.importorskip("ortools.linear_solver.python.model_builder")

from docplex.mp.model import Model

from solvers.base.ce_limit_mip_fallback import (
    OssMipUnsupportedModelError,
    SolverEngineUnavailableError,
    _resolve_time_limit,
    solve_with_ce_fallback,
)


def _cplex_runtime_available() -> bool:
    try:
        import cplex  # noqa: F401
        return True
    except Exception:
        return False


_CPLEX_AVAILABLE = _cplex_runtime_available()


@pytest.fixture(params=["oss", "cplex"])
def backend(request, monkeypatch):
    if request.param == "cplex" and not _CPLEX_AVAILABLE:
        pytest.skip("CPLEX本体（cplexパッケージ）が無い環境のためcplex経路はスキップ")
    monkeypatch.setenv("SOLVER_BACKEND", request.param)
    monkeypatch.delenv("DEFAULT_SOLVER_ENGINE", raising=False)
    return request.param


@pytest.fixture
def oss_backend(monkeypatch):
    monkeypatch.setenv("SOLVER_BACKEND", "oss")
    monkeypatch.delenv("DEFAULT_SOLVER_ENGINE", raising=False)


@pytest.fixture
def cplex_backend(monkeypatch):
    monkeypatch.setenv("SOLVER_BACKEND", "cplex")
    monkeypatch.delenv("DEFAULT_SOLVER_ENGINE", raising=False)


def test_small_model_solves_normally_without_fallback(backend):
    """CE上限未満のモデルは、通常通りmdl.solve()の結果がそのまま返ること。"""
    mdl = Model(name="small")
    x = mdl.binary_var(name="x")
    y = mdl.binary_var(name="y")
    mdl.add_constraint(x + y <= 1)
    mdl.maximize(x + y)

    sol = solve_with_ce_fallback(mdl, log_output=False)

    assert sol is not None
    assert sol.get_value(x) + sol.get_value(y) == 1


def test_oversized_model_falls_back_to_highs_and_solves(backend):
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


def test_oversized_infeasible_model_returns_none_cleanly(backend):
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


def test_oversized_model_with_hyphenated_names_recovers_correct_values(backend):
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


def test_non_ce_limit_exceptions_are_not_swallowed(cplex_backend):
    """
    CE上限以外の例外（モデル自体の記述ミス等）は、フォールバックせずそのまま
    再送出すること（is_ce_limit_exceeded()で無関係の例外まで握りつぶさない）。
    """
    class _FakeModel:
        def solve(self, **kwargs):
            raise ValueError("これはCE上限とは無関係のエラー")

    with pytest.raises(ValueError, match="CE上限とは無関係"):
        solve_with_ce_fallback(_FakeModel(), log_output=False)


def test_repeated_fallback_calls_on_same_model_with_new_variables_added_between_calls(backend):
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


# ---------------------------------------------------------------------------
# 2026-09-29追加: SOLVER_BACKEND / SCIP直接変換
# ---------------------------------------------------------------------------

def test_no_cplex_runtime_raises_engine_unavailable(cplex_backend):
    """SOLVER_BACKEND=cplex でCPLEX本体が無い場合、案内付きの専用例外になること
    （CE上限判定にも一致せず「ソルバー内部エラー」扱いになっていた旧挙動の修正）。"""
    from docplex.mp.utils import DOcplexException

    class _FakeModel:
        def solve(self, **kwargs):
            raise DOcplexException("Cannot solve model: no CPLEX runtime found.")

    with pytest.raises(SolverEngineUnavailableError, match="SOLVER_BACKEND"):
        solve_with_ce_fallback(_FakeModel(), log_output=False)


def test_oss_does_not_call_mdl_solve(oss_backend):
    """SOLVER_BACKEND=oss ではmdl.solve()（CPLEX）を呼ばないこと。"""
    mdl = Model(name="oss_only")
    x = mdl.binary_var(name="x")
    mdl.maximize(x)

    def _fail(**kwargs):
        raise AssertionError("mdl.solve() が呼ばれた")

    mdl.solve = _fail
    sol = solve_with_ce_fallback(mdl, log_output=False)
    assert sol is not None and sol.get_value(x) == 1


def test_oss_mixed_model_objective_sign_ranges_and_bounds(oss_backend):
    """連続・整数・0-1変数、負の下限、範囲制約、等式、目的関数の定数項、maximize。"""
    mdl = Model(name="mixed")
    x = mdl.integer_var(0, 10, "x")
    y = mdl.binary_var("y-1")
    z = mdl.continuous_var(-3, 5, "z[a,b]")
    mdl.add_constraint(x + 5 * y <= 12 - z, "c-1")
    mdl.add_range(1, x + y, 20)
    mdl.add_constraint(x == 2 * y + z + 1)
    mdl.maximize(x + 3 * y + 7)

    sol = solve_with_ce_fallback(mdl, log_output=False)
    assert sol is not None
    # x=5, y=1, z=2 が唯一の最適解（目的値15。y=0では最大13）
    assert sol.get_objective_value() == pytest.approx(15.0)
    assert sol.get_value(x) == 5
    assert sol.get_value(y) == 1
    assert sol.get_value(z) == pytest.approx(2.0)
    assert sol.is_valid_solution(tolerance=1e-6)
    # solve_details / solve_status が付くこと（capital_project_selector等が参照）
    assert sol.solve_details is not None and sol.solve_details.time >= 0
    assert "optimal" in str(sol.solve_status).lower()


def test_oss_minimize_with_constant_objective(oss_backend):
    """目的関数が定数（mystery_shopper_schedulerの調査員1人ケース）でも解けること。"""
    mdl = Model(name="const_obj")
    x = mdl.binary_var(name="x")
    mdl.add_constraint(x >= 1)
    mdl.minimize(mdl.linear_expr(constant=0))
    sol = solve_with_ce_fallback(mdl, log_output=False)
    assert sol is not None and sol.get_value(x) == 1


def test_oss_unsupported_indicator_constraint_raises(oss_backend):
    """OSSソルバーへ変換できない制約は「解なし」ではなく明示的な例外になること。"""
    mdl = Model(name="indicator")
    x = mdl.integer_var(0, 10, "x")
    y = mdl.binary_var("y")
    mdl.add_indicator(y, x >= 3)
    mdl.maximize(x + y)
    with pytest.raises(OssMipUnsupportedModelError, match="CPLEX"):
        solve_with_ce_fallback(mdl, log_output=False)


def test_oss_time_limit_keeps_incumbent(oss_backend):
    """時間制限で打ち切られても、暫定解があれば返すこと（Noneにしない）。
    ortools 9.15のHiGHSラッパーはこの条件でUNKNOWN_STATUSを返し値を失うため、
    SCIPへ切り替えた（2026-09-29）。30制約×300変数の多次元ナップサックは
    1秒では最適性を証明できない規模。"""
    rnd = random.Random(3)
    mdl = Model(name="mkp")
    xs = mdl.binary_var_list(300, name="x")
    for _ in range(30):
        mdl.add_constraint(mdl.sum(rnd.randint(1, 100) * v for v in xs) <= 5000)
    mdl.maximize(mdl.sum(rnd.randint(1, 100) * v for v in xs))
    mdl.parameters.timelimit = 1

    sol = solve_with_ce_fallback(mdl, log_output=False)
    assert sol is not None
    assert sol.get_objective_value() > 0
    assert sol.is_valid_solution(tolerance=1e-6)


def test_resolve_time_limit_priority():
    """時間制限の決め方: 引数 → solve()のtime_limit → mdl.parameters.timelimit。
    docplex既定（1e75）は無制限扱い。"""
    mdl = Model(name="tl")
    assert _resolve_time_limit(mdl, None, {}) is None
    mdl.parameters.timelimit = 12
    assert _resolve_time_limit(mdl, None, {}) == 12
    assert _resolve_time_limit(mdl, None, {"time_limit": 7}) == 7
    assert _resolve_time_limit(mdl, 3, {"time_limit": 7}) == 3
