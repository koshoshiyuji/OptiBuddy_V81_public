"""
test_big_m_objective_check.py

2026-07-22追加: _check_big_m_objective()（Big-M近似で多目的の優先順位を
偽装しているパターンの静的検出）の単体テスト。

このチェックはdocplexに依存しない純粋な正規表現/文字列処理なので、
docplexやcpoptimizerバイナリが無いサンドボックスでも全件実行できる。

実行方法:
    cd Backend
    python -m pytest test_big_m_objective_check.py -v
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

import domain_generator as dg


def _warn_texts(code: str, path: str = "dummy_solver.py") -> list[str]:
    return dg._check_big_m_objective(code, path)


class TestBigMDetection:
    def test_extreme_ratio_with_implicit_coefficient_is_detected(self):
        """典型的なBig-Mパターン: 巨大係数の項 + 係数無し(暗黙1)の項"""
        code = "mdl.add(mdl.minimize(1000000 * a + b))"
        warns = _warn_texts(code)
        assert len(warns) == 1
        assert "係数比" in warns[0]

    def test_extreme_ratio_both_explicit(self):
        code = "mdl.add(mdl.minimize(2000000 * a + 1 * b))"
        assert len(_warn_texts(code)) == 1

    def test_trailing_coefficient_form_is_detected(self):
        """"式 * 係数" のように末尾に係数を書く形式も検出できること"""
        code = "mdl.add(mdl.minimize(penalty_expr * 5000000 + other_term))"
        assert len(_warn_texts(code)) == 1

    def test_three_terms_one_huge_is_detected(self):
        code = "mdl.add(mdl.minimize(a + b + 2000000 * c))"
        assert len(_warn_texts(code)) == 1

    def test_nested_parens_in_argument_are_handled(self):
        """括弧を含む項（mdl.max([...])等）でも係数抽出・比の判定が壊れないこと"""
        code = "mdl.add(mdl.minimize(1000000 * mdl.max([a, b]) + c))"
        assert len(_warn_texts(code)) == 1

    def test_maximize_is_also_checked(self):
        code = "mdl.add(mdl.maximize(1000000 * a + b))"
        assert len(_warn_texts(code)) == 1


class TestLegitimateWeightingNotFlagged:
    def test_moderate_weight_ratio_not_flagged(self):
        """1 vs 100 程度の重み比は正当な重み付けであり、
        Big-M疑いにはしない（閾値1000未満）"""
        code = "mdl.add(mdl.minimize(1 * task_cost + 100 * quality_penalty))"
        assert _warn_texts(code) == []

    def test_ratio_just_below_threshold_not_flagged(self):
        code = "mdl.add(mdl.minimize(999 * a + b))"
        assert _warn_texts(code) == []

    def test_ratio_at_threshold_is_flagged(self):
        code = "mdl.add(mdl.minimize(1000 * a + b))"
        assert len(_warn_texts(code)) == 1

    def test_single_term_not_flagged(self):
        """項が1つしかない場合は比較対象が無いため警告しない"""
        code = "mdl.add(mdl.minimize(makespan_expr))"
        assert _warn_texts(code) == []

    def test_no_minimize_call_not_flagged(self):
        code = "x = 1000000 * a + b  # 目的関数ではない単なる代入"
        assert _warn_texts(code) == []


class TestMinimizeStaticLexIsExcluded:
    def test_minimize_static_lex_never_flagged(self):
        """mdl.minimize_static_lex(...) はtrue lexicographicの正しい書き方
        であり、_MINIMIZE_CALL_RE自体がマッチしないため対象外になる"""
        code = (
            "exprs = build_lexicographic_objective_exprs(mdl, 'X', context)\n"
            "mdl.add(mdl.minimize_static_lex(exprs))"
        )
        assert _warn_texts(code) == []

    def test_minimize_static_lex_with_extreme_looking_literal_list_not_flagged(self):
        code = "mdl.add(mdl.minimize_static_lex([1000000 * a, b]))"
        assert _warn_texts(code) == []


class TestDocplexMpExcluded:
    def test_docplex_mp_model_import_disables_check(self):
        """docplex.mp.model（CPLEX MP）はmdl.minimize(obj)を直接呼ぶのが
        正しい書き方であり、_check_unwrapped_minimizeと同じ理由で対象外にする
        （StoreSiteでの誤検知パターン、docs/ENGINEERING_LOG.md 2026-07-20参照）"""
        code = (
            "from docplex.mp.model import Model\n"
            "mdl = Model()\n"
            "mdl.minimize(1000000 * a + b)\n"
        )
        assert _warn_texts(code) == []


class TestScanDiffsForWarningsRouting:
    """
    2026-07-22追加（Koshoshi合意）: Big-M検出はunused_in_solverと同様、
    advisory（自動で通す）ではなくblocking（人間の確認必須）側に回すため、
    scan_diffs_for_warnings() の戻り値で他の静的チェックとは別の
    "big_m_warnings" キーに分離されていることを確認する。
    scan_diffs_for_warnings() はドライランスキャンでファイル書き込みを
    行わないため、ここで直接呼び出してよい。
    """

    def test_big_m_warning_goes_to_dedicated_key_not_generic_warnings(self):
        diffs = [{
            "path": "solvers/x_solver.py",
            "new_content": "mdl.add(mdl.minimize(1000000 * a + b))",
        }]
        result = dg.scan_diffs_for_warnings(diffs)
        assert "big_m_warnings" in result
        assert len(result["big_m_warnings"]) == 1
        assert not any("係数比" in w for w in result["warnings"]), \
            "Big-M警告が汎用warningsに混入している（blocking/advisory振り分けが壊れている）"

    def test_no_big_m_pattern_yields_empty_list(self):
        diffs = [{
            "path": "solvers/x_solver.py",
            "new_content": "mdl.add(mdl.minimize(makespan_expr))",
        }]
        result = dg.scan_diffs_for_warnings(diffs)
        assert result["big_m_warnings"] == []


class TestRealSolverFilesNoFalsePositives:
    """既存の実ソルバーファイル群に対して誤検知が出ないことの回帰確認"""

    def test_existing_solvers_have_no_new_warnings(self):
        backend_dir = Path(__file__).parent
        solver_files = sorted((backend_dir / "solvers").glob("*_solver.py"))
        assert solver_files, "solvers/*_solver.py が1件も見つかりません（テスト環境の問題）"

        offenders = {}
        for f in solver_files:
            code = f.read_text(encoding="utf-8")
            warns = dg._check_big_m_objective(code, str(f))
            if warns:
                offenders[str(f)] = warns

        assert offenders == {}, f"既存ソルバーで誤検知: {offenders}"


class TestHelperFunctions:
    def test_split_top_level_plus_terms_ignores_nested_plus(self):
        terms = dg._split_top_level_plus_terms("1000000 * mdl.max([a + b, c]) + d")
        assert terms == ["1000000 * mdl.max([a + b, c])", "d"]

    def test_term_coefficient_leading(self):
        assert dg._term_coefficient("1000000 * a") == 1000000.0

    def test_term_coefficient_trailing(self):
        assert dg._term_coefficient("a * 1000000") == 1000000.0

    def test_term_coefficient_implicit_one(self):
        assert dg._term_coefficient("penalty_var") == 1.0

    def test_term_coefficient_pure_constant_returns_none(self):
        """変数を含まない純粋な定数項は「重み」ではないため比較対象外"""
        assert dg._term_coefficient("42") is None

    def test_term_coefficient_negative_sign_uses_absolute_value(self):
        assert dg._term_coefficient("-1000000 * a") == 1000000.0
