"""
test_requirement_expr.py
=========================

requirement_expr.py のユニットテスト。docplex非依存の純粋ロジックのみなので
docplexのインストール有無に関わらず常に実行される。

実行方法:
    cd Backend
    python -m pytest solvers/base/test_requirement_expr.py -v
"""

import pytest

from solvers.base.requirement_expr import (
    RequirementExprError,
    collect_requirement_atoms,
    evaluate_requirement,
)


# ---------------------------------------------------------------------------
# 要件なし (Falsy expr)
# ---------------------------------------------------------------------------

def test_none_expr_is_always_true():
    assert evaluate_requirement(None, {}) is True


def test_empty_dict_expr_is_always_true():
    assert evaluate_requirement({}, {"skills": {"nurse"}}) is True


# ---------------------------------------------------------------------------
# atom
# ---------------------------------------------------------------------------

def test_atom_true_when_value_present():
    expr = {"op": "atom", "attr": "skills", "value": "nurse"}
    assert evaluate_requirement(expr, {"skills": {"nurse", "CHIEF"}}) is True


def test_atom_false_when_value_absent():
    expr = {"op": "atom", "attr": "skills", "value": "ICU"}
    assert evaluate_requirement(expr, {"skills": {"nurse"}}) is False


def test_atom_false_when_attr_unknown():
    expr = {"op": "atom", "attr": "certifications", "value": "ACLS"}
    assert evaluate_requirement(expr, {"skills": {"nurse"}}) is False


def test_atom_missing_attr_or_value_raises():
    with pytest.raises(RequirementExprError):
        evaluate_requirement({"op": "atom", "value": "x"}, {})
    with pytest.raises(RequirementExprError):
        evaluate_requirement({"op": "atom", "attr": "skills"}, {})


# ---------------------------------------------------------------------------
# and / or
# ---------------------------------------------------------------------------

def test_and_requires_all_terms_true():
    expr = {
        "op": "and",
        "terms": [
            {"op": "atom", "attr": "skills", "value": "nurse"},
            {"op": "atom", "attr": "certifications", "value": "ACLS"},
        ],
    }
    ok_attrs = {"skills": {"nurse"}, "certifications": {"ACLS"}}
    missing_cert_attrs = {"skills": {"nurse"}, "certifications": set()}
    assert evaluate_requirement(expr, ok_attrs) is True
    assert evaluate_requirement(expr, missing_cert_attrs) is False


def test_or_requires_any_term_true():
    expr = {
        "op": "or",
        "terms": [
            {"op": "atom", "attr": "skills", "value": "ICU"},
            {"op": "atom", "attr": "skills", "value": "ER"},
        ],
    }
    assert evaluate_requirement(expr, {"skills": {"ER"}}) is True
    assert evaluate_requirement(expr, {"skills": {"nurse"}}) is False


def test_and_or_empty_terms_raises():
    with pytest.raises(RequirementExprError):
        evaluate_requirement({"op": "and", "terms": []}, {})
    with pytest.raises(RequirementExprError):
        evaluate_requirement({"op": "or", "terms": []}, {})


def test_and_or_missing_terms_raises():
    with pytest.raises(RequirementExprError):
        evaluate_requirement({"op": "and"}, {})


# ---------------------------------------------------------------------------
# not
# ---------------------------------------------------------------------------

def test_not_negates_single_term():
    expr = {"op": "not", "terms": [{"op": "atom", "attr": "skills", "value": "TRAINEE"}]}
    assert evaluate_requirement(expr, {"skills": {"nurse"}}) is True
    assert evaluate_requirement(expr, {"skills": {"nurse", "TRAINEE"}}) is False


def test_not_requires_exactly_one_term():
    with pytest.raises(RequirementExprError):
        evaluate_requirement({"op": "not", "terms": []}, {})
    with pytest.raises(RequirementExprError):
        evaluate_requirement(
            {"op": "not", "terms": [{"op": "atom", "attr": "a", "value": 1},
                                     {"op": "atom", "attr": "b", "value": 2}]},
            {},
        )


# ---------------------------------------------------------------------------
# ネスト・複合例（HANDOFFで挙げた例の回帰確認）
# ---------------------------------------------------------------------------

def test_nested_and_or_not_example():
    # 「nurseスキル必須 かつ (ICUスキル または CHIEFグレード) かつ TRAINEEでない」
    expr = {
        "op": "and",
        "terms": [
            {"op": "atom", "attr": "skills", "value": "nurse"},
            {"op": "or", "terms": [
                {"op": "atom", "attr": "skills", "value": "ICU"},
                {"op": "atom", "attr": "grade", "value": "CHIEF"},
            ]},
            {"op": "not", "terms": [{"op": "atom", "attr": "skills", "value": "TRAINEE"}]},
        ],
    }
    assert evaluate_requirement(expr, {"skills": {"nurse", "ICU"}, "grade": {"STAFF"}}) is True
    assert evaluate_requirement(expr, {"skills": {"nurse"}, "grade": {"CHIEF"}}) is True
    assert evaluate_requirement(expr, {"skills": {"nurse"}, "grade": {"STAFF"}}) is False
    assert evaluate_requirement(
        expr, {"skills": {"nurse", "ICU", "TRAINEE"}, "grade": {"STAFF"}}
    ) is False


def test_旧required_skills_or_semantics_equivalent():
    # 旧 required_skills=["A","B"]（いずれか1つ）と同じ結果になることを確認
    expr = {"op": "or", "terms": [
        {"op": "atom", "attr": "skills", "value": "A"},
        {"op": "atom", "attr": "skills", "value": "B"},
    ]}
    assert evaluate_requirement(expr, {"skills": {"A"}}) is True
    assert evaluate_requirement(expr, {"skills": {"B"}}) is True
    assert evaluate_requirement(expr, {"skills": {"C"}}) is False


def test_旧required_certifications_and_semantics_equivalent():
    # 旧 required_certifications=["A","B"]（全て必須）と同じ結果になることを確認
    expr = {"op": "and", "terms": [
        {"op": "atom", "attr": "certifications", "value": "A"},
        {"op": "atom", "attr": "certifications", "value": "B"},
    ]}
    assert evaluate_requirement(expr, {"certifications": {"A", "B"}}) is True
    assert evaluate_requirement(expr, {"certifications": {"A"}}) is False


# ---------------------------------------------------------------------------
# 不正な構造
# ---------------------------------------------------------------------------

def test_non_dict_expr_raises():
    with pytest.raises(RequirementExprError):
        evaluate_requirement(["op", "atom"], {})


def test_unknown_op_raises():
    with pytest.raises(RequirementExprError):
        evaluate_requirement({"op": "xor", "terms": []}, {})


# ---------------------------------------------------------------------------
# collect_requirement_atoms
# ---------------------------------------------------------------------------

def test_collect_atoms_empty_for_falsy_expr():
    assert collect_requirement_atoms(None) == []
    assert collect_requirement_atoms({}) == []


def test_collect_atoms_single_atom():
    expr = {"op": "atom", "attr": "skills", "value": "nurse"}
    assert collect_requirement_atoms(expr) == [("skills", "nurse")]


def test_collect_atoms_nested():
    expr = {
        "op": "and",
        "terms": [
            {"op": "atom", "attr": "skills", "value": "nurse"},
            {"op": "or", "terms": [
                {"op": "atom", "attr": "skills", "value": "ICU"},
                {"op": "atom", "attr": "grade", "value": "CHIEF"},
            ]},
            {"op": "not", "terms": [{"op": "atom", "attr": "skills", "value": "TRAINEE"}]},
        ],
    }
    atoms = collect_requirement_atoms(expr)
    assert set(atoms) == {
        ("skills", "nurse"),
        ("skills", "ICU"),
        ("grade", "CHIEF"),
        ("skills", "TRAINEE"),
    }
