"""
requirement_expr.py — 層A: 汎用 and/or/not 条件式評価（共通コア）
====================================================

【背景】
NurseShiftWeeklyCapで、`required_skills`（「いずれか1つ一致すればOK」= 暗黙のOR判定）と
`required_certifications`（「全て一致必須」= 暗黙のAND判定）という、フィールドごとに
異なる暗黙の論理演算が使われていた。この暗黙さ・不統一さは意図せずAND/ORの取り違えを
招くリスクがあり、責任者(CHIEF)不在バグ（2026-07-13）の根本原因調査を機に問題として
認識された。

この2フィールドを廃止し、単一の汎用条件式 `requirement` に統一する。条件式は候補
（スタッフ等）の属性セット（skills, certifications, その他将来ドメインで追加される
カテゴリ属性）に対して、and/or/notを明示的にネストしたツリーとして評価する。
「スキルか、資格か、その他のリソース属性か」によらず、同じ式構造・同じ評価関数で
扱えるようにするのが目的。

【層の境界】
- 層A (共通コア): このファイル全体（evaluate_requirement / collect_requirement_atoms）。
  docplexに依存しない純粋なPython評価のみを行う。
- 層B/C: 各ドメインのsolver.pyは、候補生成（tcands方式の事前フィルタ）の中で
  `if not evaluate_requirement(task.get("requirement"), staff_attrs): continue`
  のように呼び出すだけ。CPモデルの決定変数には一切関与しない
  （required_skills/required_certificationsが元々そうだったのと同じ層）。

【式の形】
  atom: {"op": "atom", "attr": "skills", "value": "CHIEF"}
    -> attrs.get("skills", set()) に "CHIEF" が含まれるか
  and:  {"op": "and", "terms": [expr, expr, ...]}   # terms は1件以上
  or:   {"op": "or",  "terms": [expr, expr, ...]}   # terms は1件以上
  not:  {"op": "not", "terms": [expr]}              # terms はちょうど1件
  未指定 (None / {} / {}相当のFalsy値): 常にTrue（要件なし、無条件で候補に含める）

【使い方（例: NurseShiftWeeklyCap）】
    staff_attrs = {
        "skills": set(staff.get("skills", [])),
        "certifications": set(staff.get("certifications", [])),
    }
    if not evaluate_requirement(task.get("requirement"), staff_attrs):
        continue  # 候補から除外

  例: 「nurseスキル必須 かつ (ICUスキル または CHIEFグレード) かつ TRAINEEでない」
    {
      "op": "and",
      "terms": [
        {"op": "atom", "attr": "skills", "value": "nurse"},
        {"op": "or", "terms": [
          {"op": "atom", "attr": "skills", "value": "ICU"},
          {"op": "atom", "attr": "grade", "value": "CHIEF"}
        ]},
        {"op": "not", "terms": [{"op": "atom", "attr": "skills", "value": "TRAINEE"}]}
      ]
    }

  旧required_skills=["A","B"]（いずれか）相当:
    {"op": "or",  "terms": [{"op": "atom", "attr": "skills", "value": "A"},
                              {"op": "atom", "attr": "skills", "value": "B"}]}
  旧required_certifications=["A","B"]（全て）相当:
    {"op": "and", "terms": [{"op": "atom", "attr": "certifications", "value": "A"},
                              {"op": "atom", "attr": "certifications", "value": "B"}]}
"""

from __future__ import annotations

from typing import Any, Dict, List, Mapping, Set, Tuple

__all__ = ["RequirementExprError", "evaluate_requirement", "collect_requirement_atoms"]


class RequirementExprError(ValueError):
    """requirement式の構造が不正なときに送出する。"""


def evaluate_requirement(expr: Any, attrs: Mapping[str, Set[str]]) -> bool:
    """
    requirement式(expr)を候補の属性セット(attrs)に対して評価する。

    Args:
        expr: {"op": "atom"|"and"|"or"|"not", ...} 形式のツリー。
              None / {} などFalsyな値の場合は「要件なし」として常にTrueを返す。
        attrs: 属性名 -> 値の集合（例: {"skills": {"nurse", "CHIEF"}, "certifications": {"ACLS"}}）。
               未知の属性名は空集合とみなす（=そのatomは常にFalse）。

    Returns:
        bool

    Raises:
        RequirementExprError: exprの構造が不正な場合
                               （dictでない、opが未知、terms欠落など）。
    """
    if not expr:
        return True
    if not isinstance(expr, dict):
        raise RequirementExprError(f"requirement式はdictである必要があります: {expr!r}")

    op = expr.get("op")

    if op == "atom":
        attr = expr.get("attr")
        value = expr.get("value")
        if attr is None or value is None:
            raise RequirementExprError(f"atomにはattrとvalueが必須です: {expr!r}")
        return value in attrs.get(attr, set())

    if op in ("and", "or"):
        terms = expr.get("terms")
        if not isinstance(terms, list) or not terms:
            raise RequirementExprError(f"'{op}'にはterms(空でないlist)が必須です: {expr!r}")
        results = [evaluate_requirement(t, attrs) for t in terms]
        return all(results) if op == "and" else any(results)

    if op == "not":
        terms = expr.get("terms")
        if not isinstance(terms, list) or len(terms) != 1:
            raise RequirementExprError(f"'not'にはterms(要素ちょうど1件のlist)が必須です: {expr!r}")
        return not evaluate_requirement(terms[0], attrs)

    raise RequirementExprError(f"未知のop: {op!r} (expr={expr!r})")


def collect_requirement_atoms(expr: Any) -> List[Tuple[str, Any]]:
    """
    requirement式ツリーに含まれる全atomの (attr, value) ペアを列挙する。

    validator側で「この値を持つ候補が実在するか」を確認する用途を想定
    （旧required_skills/required_certificationsの「存在しないスキルを指定していないか」
    警告チェックの一般化）。

    exprがFalsyな場合は空リストを返す。構造検証は行わない
    （evaluate_requirement呼び出し時に別途RequirementExprErrorとして検出される前提）。
    """
    if not expr or not isinstance(expr, dict):
        return []

    op = expr.get("op")
    if op == "atom":
        attr = expr.get("attr")
        value = expr.get("value")
        if attr is not None and value is not None:
            return [(attr, value)]
        return []

    if op in ("and", "or", "not"):
        atoms: List[Tuple[str, Any]] = []
        for t in expr.get("terms") or []:
            atoms.extend(collect_requirement_atoms(t))
        return atoms

    return []
