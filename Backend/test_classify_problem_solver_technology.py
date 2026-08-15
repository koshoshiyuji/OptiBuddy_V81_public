"""
test_classify_problem_solver_technology.py

2026-07-27追加。domain_generator._STAGE1A_SYSTEM の改修（Koshoshiとの相談、
「5番: Stage1aでのソルバー技術選択分岐」対応）を実LLMで検証するスクリプト。

改修内容の確認observationは3点:
  (a) 基底問題参考（docs/CSPLIB_REFERENCE.md（旧primary_problems.md））に構造的に一致し、かつ推奨解法が
      CPかMIPのどちらか一方に定まる場合、technical_directivesに断定的な
      技術指示（「構造推定」という留保無し）が書かれること。
  (b) 推奨解法が「両方」の場合、technical_directivesには何も書かれず、
      代わりにmissing_infoにCP/MIPどちらか確認する趣旨の質問が追加されること。
  (c) 基底問題参考のどれにも構造的に一致しない場合も、(b)と同様に
      missing_infoへ質問が追加されること（technical_directivesは空のまま）。

sandbox環境（API키無し）では実行できない。Koshoshiの実環境（LLM APIキー設定済み）
で実行することを想定している。

【重要な設計上の注意（test_classify_problem_csplib_e2e.pyと同じ注意）】
classify_problem()は、DB（dsl_repository/optibuddy.db）に登録済みの動的候補
ドメインを「追加候補ドメイン一覧」としてルール0（最優先）でプロンプトに含める。
そのため、既にDBに登録済みのドメインに似たヒアリング文を使うと、本来検証したい
「基底問題参考への構造マッチ」経路ではなく、ルール0（登録済みドメインへの
直接マッチ）に先に捕まってしまう。本テストでは2026-07-27時点でDB未登録の
基底問題（Bus Driver Scheduling・Steel Mill Slab Design・Template Design）を
選んでいる。登録状況が変わった場合はテストケースの見直しが必要
（実行前に `SELECT problem_class FROM dsl_definitions` で現在の登録済み
ドメインを確認すること）。

実行方法:
  cd Backend && python3 test_classify_problem_solver_technology.py
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

import domain_generator as dg  # noqa: E402


# ケースA: 構造的に一致し、推奨解法がMIPの1つに定まる基底問題
# （prob022 Bus Driver Scheduling → MIP、CSPLIB_REFERENCE.md（旧primary_problems.md）記載）
HEARING_CLEAR_MIP = """
バス事業者です。複数の運転士に、日々発生する「乗務」（早朝便・昼便・深夜便など、
時間帯・系統がバラバラな運転区間の単位）を割り当てたいです。
全ての乗務を、必要な資格（大型二種免許等）を持つ運転士でカバーしつつ、
使用する運転士の人数（＝人件費）を最小化したいです。
1人の運転士が担当する乗務同士は、時間的に重複してはいけません。
特に使用したい技術やライブラリの指定はありません。
"""

# ケースB: 構造的に一致し、推奨解法がCPの1つに定まる基底問題
# （prob038 Steel Mill Slab Design → CP、CSPLIB_REFERENCE.md（旧primary_problems.md）記載）
HEARING_CLEAR_CP = """
製鋼工場です。受注ごとに「色」と「重量」が決まっています。スラブ（鋼材の塊）には
容量上限があり、1つのスラブに詰め込める色の種類数にも上限があります。
受注をスラブに割り付ける際、詰め込みきれずに廃棄される重量を最小化したいです。
特に使用したい技術やライブラリの指定はありません。
"""

# ケースC: 構造的に一致するが、推奨解法が「両方」の基底問題
# （prob002 Template Design → 両方、CSPLIB_REFERENCE.md（旧primary_problems.md）記載）
HEARING_BOTH = """
印刷会社です。複数の注文（アイテムの種類×必要枚数）があり、これらを版下
（テンプレート）に割り付けて印刷したいです。使用する版下の枚数をできるだけ
少なくしたいです。1枚の版下には複数種類のアイテムを配置できますが、
版のサイズに上限があります。
特に使用したい技術やライブラリの指定はありません。
"""

# ケースD: 基底問題参考のどれにも構造的に一致しない、ごく一般的な業務ヒアリング
HEARING_NO_MATCH = """
社内のヘルプデスク業務です。問い合わせ（チケット）が日々発生し、担当者に
割り振っています。各担当者は同時に対応できるチケット数に上限があり、
問い合わせ内容によって対応できる担当者(得意分野)が異なります。
できるだけ早く全チケットに対応できるよう、担当割り当てを最適化したいです。
特に使用したい技術やライブラリの指定はありません。
"""


def _run_case(label: str, domain_name: str, hearing: str):
    print("=" * 70)
    print(f"[{label}] domain_name={domain_name}")
    result = dg.classify_problem(domain_name, [hearing])
    print(f"  match_type          = {result.get('match_type')}")
    print(f"  base_domain         = {result.get('base_domain')}")
    print(f"  technical_directives= {result.get('technical_directives')!r}")
    print(f"  missing_info        = {result.get('missing_info')}")
    return result


def main():
    print(
        "実行前に、以下のドメインがdsl_definitionsに未登録であることを確認してください:\n"
        "  BusDriverScheduling / SteelMillSlabDesign / TemplateDesign\n"
        "  (SELECT problem_class FROM dsl_repository/optibuddy.db の dsl_definitions)\n"
    )

    r_mip = _run_case("ケースA: 明確なMIP", "BusDriverScheduling", HEARING_CLEAR_MIP)
    assert r_mip.get("match_type") == "new_domain", (
        f"想定外: match_type={r_mip.get('match_type')}（登録済みドメインに先に"
        f"捕まった可能性。上記の事前確認を参照）"
    )
    td = (r_mip.get("technical_directives") or "")
    assert "mip" in td.lower() or "docplex.mp" in td.lower(), (
        f"技術指示にMIPの断定的な記載が見当たりません: {td!r}"
    )
    assert "構造推定" not in td, "断定指示のはずが、まだ留保表現（構造推定）が残っています"
    print("  → OK: MIPが断定的に指示された")

    r_cp = _run_case("ケースB: 明確なCP", "SteelMillSlabDesign", HEARING_CLEAR_CP)
    assert r_cp.get("match_type") == "new_domain"
    td = (r_cp.get("technical_directives") or "")
    assert "cp" in td.lower() or "docplex.cp" in td.lower(), (
        f"技術指示にCPの断定的な記載が見当たりません: {td!r}"
    )
    assert "構造推定" not in td
    print("  → OK: CPが断定的に指示された")

    r_both = _run_case("ケースC: 両方(BOTH)", "TemplateDesign", HEARING_BOTH)
    assert r_both.get("match_type") == "new_domain"
    missing = " ".join(r_both.get("missing_info") or [])
    assert ("cp" in missing.lower() and "mip" in missing.lower()), (
        f"missing_infoにCP/MIP確認の質問が見当たりません: {r_both.get('missing_info')}"
    )
    print("  → OK: missing_infoにCP/MIP確認質問が追加された")

    r_none = _run_case("ケースD: 該当なし", "HelpdeskTicketRouting", HEARING_NO_MATCH)
    assert r_none.get("match_type") == "new_domain"
    missing = " ".join(r_none.get("missing_info") or [])
    assert ("cp" in missing.lower() and "mip" in missing.lower()), (
        f"missing_infoにCP/MIP確認の質問が見当たりません: {r_none.get('missing_info')}"
    )
    print("  → OK: 基底問題不一致でもmissing_infoに質問が追加された")

    print("=" * 70)
    print("全ケースOK。")


if __name__ == "__main__":
    main()
