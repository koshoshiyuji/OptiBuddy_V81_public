"""
test_objective_terms.py
=======================

objective_terms.py のユニットテスト。
CP Optimizer エンジンなしで動作する（モデル構築までを検証）。

実行方法:
    cd Backend
    python -m pytest solvers/base/test_objective_terms.py -v

前提:
    - docplex がインストール済み
    - solvers/base/objective_terms.py が存在する
"""

import pytest

try:
    from docplex.cp.model import CpoModel
    HAS_DOCPLEX = True
except ImportError:
    HAS_DOCPLEX = False

if HAS_DOCPLEX:
    from solvers.base.objective_terms import (
        build_objective_expr,
        build_makespan,
        build_order_penalty,
        build_workload_balance_range,
        build_lexicographic_objective_exprs,
        verify_lexicographic_mechanism,
        OBJECTIVE_RECIPES,
        OBJECTIVE_TERM_BUILDERS,
    )

pytestmark = pytest.mark.skipif(not HAS_DOCPLEX, reason="docplex not installed")


# ---------------------------------------------------------------------------
# フィクスチャ
# ---------------------------------------------------------------------------

@pytest.fixture
def mdl():
    return CpoModel(name="test")


@pytest.fixture
def simple_task_itvs(mdl):
    """3タスクのinterval_var辞書"""
    return {
        "1": mdl.interval_var(size=10, name="T1"),
        "2": mdl.interval_var(size=10, name="T2"),
        "3": mdl.interval_var(size=10, name="T3"),
    }


# ---------------------------------------------------------------------------
# テスト: OBJECTIVE_TERM_BUILDERS 登録確認
# ---------------------------------------------------------------------------

class TestRegistration:
    def test_all_terms_registered(self):
        expected = {
            "makespan", "order_penalty",
            "workload_balance_range",
        }
        assert expected <= set(OBJECTIVE_TERM_BUILDERS.keys()), \
            f"未登録の項: {expected - set(OBJECTIVE_TERM_BUILDERS.keys())}"

    def test_recipes_exist(self):
        assert "YardPlanning" in OBJECTIVE_RECIPES

    def test_yard_recipe_terms(self):
        recipe = OBJECTIVE_RECIPES["YardPlanning"]
        assert "makespan" in recipe["terms"]
        assert "order_penalty" in recipe["terms"]


# ---------------------------------------------------------------------------
# テスト: build_makespan
# ---------------------------------------------------------------------------

class TestBuildMakespan:
    def test_returns_expr(self, mdl, simple_task_itvs):
        expr = build_makespan(mdl, {"task_itvs": simple_task_itvs})
        assert expr is not None

    def test_export_contains_max_end(self, mdl, simple_task_itvs):
        expr = build_makespan(mdl, {"task_itvs": simple_task_itvs})
        exported = str(expr)
        # max と endOf のいずれかが含まれることを確認
        assert "max" in exported.lower() or "end" in exported.lower()


# ---------------------------------------------------------------------------
# テスト: build_order_penalty
# ---------------------------------------------------------------------------

class TestBuildOrderPenalty:
    def test_empty_pairs_returns_zero_var(self, mdl, simple_task_itvs):
        expr = build_order_penalty(mdl, {
            "task_itvs": simple_task_itvs,
            "penalty_pairs": [],
        })
        # integer_var(0,0) が返る
        assert expr is not None

    def test_valid_pairs(self, mdl, simple_task_itvs):
        pairs = [
            {"task_a": "1", "task_b": "2", "expected_order": "a_before_b"},
            {"task_a": "2", "task_b": "3", "expected_order": "a_before_b"},
        ]
        expr = build_order_penalty(mdl, {
            "task_itvs": simple_task_itvs,
            "penalty_pairs": pairs,
        })
        assert expr is not None

    def test_missing_task_id_is_skipped(self, mdl, simple_task_itvs):
        """存在しないタスクIDが含まれていてもクラッシュしない"""
        pairs = [
            {"task_a": "1", "task_b": "999", "expected_order": "a_before_b"},
        ]
        expr = build_order_penalty(mdl, {
            "task_itvs": simple_task_itvs,
            "penalty_pairs": pairs,
        })
        assert expr is not None

    def test_b_before_a_order(self, mdl, simple_task_itvs):
        """expected_order: b_before_a でもクラッシュしない"""
        pairs = [{"task_a": "1", "task_b": "2", "expected_order": "b_before_a"}]
        expr = build_order_penalty(mdl, {
            "task_itvs": simple_task_itvs,
            "penalty_pairs": pairs,
        })
        assert expr is not None


# ---------------------------------------------------------------------------
# テスト: build_resource_usage_cost
# ---------------------------------------------------------------------------

# 2026-07-27削除: TestBuildResourceUsageCost / TestBuildPreferencePenalty
# （build_resource_usage_cost() / build_preference_penalty() 自体を
# EventStaffing削除に伴い削除したため、対応テストも削除。詳細は
# solvers/base/objective_terms.py のコメント参照）。


# ---------------------------------------------------------------------------
# テスト: build_workload_balance_range（2026-07-26追加、prob069実証実験）
# ---------------------------------------------------------------------------

class TestBuildWorkloadBalanceRange:
    def _make_context(self, mdl, workloads):
        """
        workloads: {候補id: (staff_id, workload値)} の辞書からcandidates/assignment_itvsを組み立てる。
        """
        candidates = []
        assignment_itvs = {}
        for aid, (sid, w) in workloads.items():
            candidates.append({"id": aid, "staff_id": sid, "workload": w})
            assignment_itvs[aid] = mdl.interval_var(optional=True, size=60, name=f"A_{aid}")
        return candidates, assignment_itvs

    def test_returns_expr_with_two_groups(self, mdl):
        candidates, itvs = self._make_context(mdl, {
            "A1": ("S1", 5), "A2": ("S1", 3), "A3": ("S2", 4),
        })
        expr = build_workload_balance_range(mdl, {
            "candidates": candidates, "assignment_itvs": itvs,
        })
        assert expr is not None
        exported = str(expr)
        assert "max" in exported.lower() and "min" in exported.lower()

    def test_single_group_returns_zero_var(self, mdl):
        """比較対象のグループが1つしかない場合はrange計算できないため0を返す"""
        candidates, itvs = self._make_context(mdl, {"A1": ("S1", 5)})
        expr = build_workload_balance_range(mdl, {
            "candidates": candidates, "assignment_itvs": itvs,
        })
        assert expr is not None

    def test_zero_workload_candidates_excluded(self, mdl):
        """workload=0（またはフィールド無し）の候補は集計対象から除外され、
        クラッシュしないこと"""
        candidates, itvs = self._make_context(mdl, {
            "A1": ("S1", 0), "A2": ("S2", 0),
        })
        expr = build_workload_balance_range(mdl, {
            "candidates": candidates, "assignment_itvs": itvs,
        })
        assert expr is not None

    def test_custom_group_key_and_workload_field(self, mdl):
        """group_key/workload_fieldをNurseShiftWeeklyCap以外の命名でも
        差し替えられること（本ビルダーが特定ドメインの列名に依存しない汎用実装であることの確認）"""
        candidates = [
            {"id": "A1", "team": "T1", "load": 10},
            {"id": "A2", "team": "T2", "load": 2},
        ]
        itvs = {
            "A1": mdl.interval_var(optional=True, size=60, name="A_A1"),
            "A2": mdl.interval_var(optional=True, size=60, name="A_A2"),
        }
        expr = build_workload_balance_range(mdl, {
            "candidates": candidates, "assignment_itvs": itvs,
            "group_key": "team", "workload_field": "load",
        })
        assert expr is not None

    def test_missing_interval_var_is_skipped(self, mdl):
        """assignment_itvsに対応するinterval_varが無い候補はスキップされクラッシュしない"""
        candidates = [{"id": "A1", "staff_id": "S1", "workload": 5}]
        expr = build_workload_balance_range(mdl, {
            "candidates": candidates, "assignment_itvs": {},
        })
        assert expr is not None


# ---------------------------------------------------------------------------
# テスト: build_objective_expr (統合)
# ---------------------------------------------------------------------------

class TestBuildObjectiveExpr:
    def test_yard_planning(self, mdl, simple_task_itvs):
        context = {
            "task_itvs":     simple_task_itvs,
            "penalty_pairs": [
                {"task_a": "1", "task_b": "2", "expected_order": "a_before_b"}
            ],
        }
        expr = build_objective_expr(mdl, "YardPlanning", context)
        assert expr is not None

    def test_yard_with_weight_overrides(self, mdl, simple_task_itvs):
        context = {
            "task_itvs":     simple_task_itvs,
            "penalty_pairs": [],
        }
        # Plan B: w_penalty を大きくする
        expr = build_objective_expr(
            mdl, "YardPlanning", context,
            weight_overrides={"makespan": 1, "order_penalty": 300},
        )
        assert expr is not None

    def test_unknown_domain_raises(self, mdl):
        with pytest.raises(KeyError):
            build_objective_expr(mdl, "UnknownDomain", {})

    def test_default_weights_not_mutated(self, mdl, simple_task_itvs):
        """weight_overrides が OBJECTIVE_RECIPES のデフォルト値を汚染しないこと"""
        recipe_before = dict(OBJECTIVE_RECIPES["YardPlanning"]["default_weights"])
        context = {"task_itvs": simple_task_itvs, "penalty_pairs": []}
        build_objective_expr(
            mdl, "YardPlanning", context,
            weight_overrides={"makespan": 999},
        )
        assert OBJECTIVE_RECIPES["YardPlanning"]["default_weights"] == recipe_before


# ---------------------------------------------------------------------------
# テスト: 旧インライン実装との等価性確認
# ---------------------------------------------------------------------------

class TestEquivalenceWithInlineImpl:
    """
    旧実装(インライン生成)と新実装(build_objective_expr)で
    生成される CPO 式の文字列表現が等価であることを確認する。

    CP Optimizer エンジンは不要 (export_as_cpo() レベルの検証)。
    """

    def test_makespan_equivalent(self, mdl):
        itvs = {str(i): mdl.interval_var(size=10, name=f"T{i}") for i in range(3)}

        # 旧実装
        old_expr = mdl.max([mdl.end_of(iv) for iv in itvs.values()])

        # 新実装
        new_expr = build_makespan(mdl, {"task_itvs": itvs})

        assert str(old_expr) == str(new_expr), \
            f"makespan 式が不一致:\n旧: {old_expr}\n新: {new_expr}"

    def test_order_penalty_equivalent(self, mdl):
        itvs = {
            "10": mdl.interval_var(size=10, name="T10"),
            "20": mdl.interval_var(size=10, name="T20"),
        }
        pairs = [{"task_a": "10", "task_b": "20", "expected_order": "a_before_b"}]

        # 旧実装相当: conditional(end_of(T20) < end_of(T10), 1, 0)
        old_terms = [mdl.conditional(
            mdl.end_of(itvs["20"]) < mdl.end_of(itvs["10"]), 1, 0
        )]
        old_expr = mdl.sum(old_terms)

        # 新実装
        new_expr = build_order_penalty(mdl, {"task_itvs": itvs, "penalty_pairs": pairs})

        assert str(old_expr) == str(new_expr), \
            f"order_penalty 式が不一致:\n旧: {old_expr}\n新: {new_expr}"


# ---------------------------------------------------------------------------
# テスト: lexicographicモード（2026-07-22追加）
# ---------------------------------------------------------------------------

@pytest.fixture
def _lex_test_recipe():
    """
    テスト専用の一時的な mode="lexicographic" レシピを OBJECTIVE_RECIPES に
    登録し、テスト後に必ず削除する。既存レシピを汚染しないため fixture 化。
    """
    OBJECTIVE_RECIPES["_TestLexDomain"] = {
        "mode": "lexicographic",
        "priority_terms": ["makespan", "order_penalty"],
    }
    yield "_TestLexDomain"
    del OBJECTIVE_RECIPES["_TestLexDomain"]


class TestLexicographicRecipeGuards:
    """weighted_sum用/lexicographic用の関数を取り違えて呼んだ場合に
    明確なエラーになることを確認する（Big-M代替の抑止が目的）。"""

    def test_build_objective_expr_rejects_lexicographic_domain(self, mdl, _lex_test_recipe, simple_task_itvs):
        context = {"task_itvs": simple_task_itvs, "penalty_pairs": []}
        with pytest.raises(ValueError, match="lexicographic"):
            build_objective_expr(mdl, _lex_test_recipe, context)

    def test_build_lexicographic_rejects_weighted_sum_domain(self, mdl, simple_task_itvs):
        context = {"task_itvs": simple_task_itvs, "penalty_pairs": []}
        with pytest.raises(ValueError, match="lexicographic"):
            build_lexicographic_objective_exprs(mdl, "YardPlanning", context)

    def test_empty_priority_terms_raises(self, mdl, simple_task_itvs):
        OBJECTIVE_RECIPES["_TestLexEmpty"] = {"mode": "lexicographic", "priority_terms": []}
        try:
            with pytest.raises(ValueError, match="priority_terms"):
                build_lexicographic_objective_exprs(mdl, "_TestLexEmpty", {"task_itvs": simple_task_itvs})
        finally:
            del OBJECTIVE_RECIPES["_TestLexEmpty"]


class TestBuildLexicographicObjectiveExprs:
    def test_returns_list_in_priority_order(self, mdl, _lex_test_recipe, simple_task_itvs):
        context = {
            "task_itvs": simple_task_itvs,
            "penalty_pairs": [{"task_a": "1", "task_b": "2", "expected_order": "a_before_b"}],
        }
        exprs = build_lexicographic_objective_exprs(mdl, _lex_test_recipe, context)
        assert isinstance(exprs, list)
        assert len(exprs) == 2  # makespan, order_penalty の2ティア

    def test_tier_with_multiple_terms_is_summed(self, mdl, simple_task_itvs):
        """同一ティアに複数項を束ねた場合、そのティアの式が1個にまとまること"""
        # 2026-07-27修正: 以前はEventStaffing専用のresource_usage_cost/
        # preference_penalty（削除済み）を例として使っていたが、単に「1つの
        # contextで2項が計算できる組」であれば何でもよいテストのため、
        # 現存するmakespan/order_penalty（どちらもtask_itvsのみで計算可能）に
        # 差し替えた。
        OBJECTIVE_RECIPES["_TestLexTiered"] = {
            "mode": "lexicographic",
            "priority_terms": [["makespan", "order_penalty"]],  # 1ティアに2項
        }
        try:
            context = {
                "task_itvs":     simple_task_itvs,
                "penalty_pairs": [],
            }
            exprs = build_lexicographic_objective_exprs(mdl, "_TestLexTiered", context)
            assert len(exprs) == 1  # 2項が1ティアに合算されている
        finally:
            del OBJECTIVE_RECIPES["_TestLexTiered"]

    def test_can_be_passed_to_minimize_static_lex(self, mdl, _lex_test_recipe, simple_task_itvs):
        """戻り値をそのまま mdl.minimize_static_lex() に渡してモデル構築できること
        （実ソルブはしない。CP Optimizerエンジンがない環境でも検証可能な範囲）。"""
        context = {"task_itvs": simple_task_itvs, "penalty_pairs": []}
        exprs = build_lexicographic_objective_exprs(mdl, _lex_test_recipe, context)
        obj = mdl.minimize_static_lex(exprs)
        mdl.add(obj)
        exported = str(obj)
        assert "minimizeStaticLex" in exported or "minimize_static_lex" in exported.lower()

    def test_weight_overrides_do_not_mutate_recipe(self, mdl, _lex_test_recipe, simple_task_itvs):
        before = dict(OBJECTIVE_RECIPES[_lex_test_recipe].get("tier_weights", {}))
        context = {"task_itvs": simple_task_itvs, "penalty_pairs": []}
        build_lexicographic_objective_exprs(
            mdl, _lex_test_recipe, context, weight_overrides={"makespan": 999}
        )
        assert OBJECTIVE_RECIPES[_lex_test_recipe].get("tier_weights", {}) == before


class TestVerifyLexicographicMechanism:
    """
    識別テスト本体の検証。本サンドボックスには cpoptimizer 実行バイナリが
    無いため実ソルブは失敗するが、その失敗が verify_lexicographic_mechanism()
    内で例外として握り潰されず、report["errors"] に反映されて ok=False に
    なることを確認する（＝関数自体が壊れていないことの確認）。
    実際のCP Optimizerエンジンがある開発環境では ok=True かつ
    true_lex_objectives == (0, 4, 0) になることを別途、実機で確認する。
    """

    def test_returns_report_dict_shape(self):
        report = verify_lexicographic_mechanism()
        assert set(report.keys()) == {
            "ok", "true_lex_objectives", "expected_objectives",
            "big_m_small_diverged", "warnings", "errors",
        }
        assert report["expected_objectives"] == (0, 4, 0)

    def test_missing_engine_reports_error_not_exception(self):
        """cpoptimizerバイナリが無い環境でも例外を外に投げず、errorsに積んでok=Falseで返す"""
        report = verify_lexicographic_mechanism()
        if not report["ok"]:
            assert len(report["errors"]) >= 1
