"""
test_classify_problem_axis_a_fit_check.py
==========================================

2026-07-30追加、2026-07-31改訂。DESIGN_2026-07-30_domain_registration_
classification_taxonomy.md 3-1節「軸(a)適合チェック」の実装を検証するテスト。

背景: 既存ドメインへの拡張(パターン3)は、base_domainが実際に採用している技術的
前提（CP Optimizerかmipか等）をそのまま引き継ぐ。この技術選択が今回のヒアリング
内容に合っているかどうかを、候補ドメイン自身のコードではなく外部参照
（docs/CSPLIB_REFERENCE.md（旧primary_problems.md）、CSPLib由来）を判断根拠として確認する必要がある
（Koshoshiとの相談、2026-07-30。候補ドメイン自身のコードを「正解」として使うと、
過去の判断ミス＝StoreSiteの2026-07-11 CP Optimizer強行4回失敗のようなケースを
自己言及的に検出できないため）。

2026-07-31改訂: 当初はこのチェックをclassify_problem()の分類プロンプトに同梱して
いたが、Koshoshiの実環境での実LLM検証（test_classify_problem_axis_a_e2e.py、
N=10）で罠ケースごとの検知率が8/10〜2/10と大きくばらつくことが判明したため、
Koshoshi承認のもと、独立した専用LLM呼び出し check_axis_a_fit()（Stage1a.4）に
分離した。このファイルもその分離に合わせて全面改訂した:
  - classify_problem() はもはや軸(a)チェックの指示・エンジン事実ブロックを
    プロンプトに含まない（分類のみに専念する）ことを確認する回帰テストを追加。
  - 軸(a)チェックの指示・事実ブロック注入の検証は、新設の check_axis_a_fit() の
    プロンプトに対して行う。
  - _build_domain_engine_facts_block()（全ドメイン分の事実一覧）は
    check_axis_a_fit() が選ばれたbase_domain 1件のみを都度参照する方式に
    置き換わったことに伴い削除されたため、対応するテストも削除した。

このファイルは、call_llm()をモックしてAPI呼び出しを行わないため、sandbox環境
（APIキー・docplex無し）でも実行できる。検証範囲は3点:
  1. get_domain_engine() が既存ドメインのsolver.py実コードから機械的に
     エンジン（cp/mip）を正しく判定できること（手作業テーブル不使用の検証）。
  2. check_axis_a_fit() が実際にLLMへ渡すプロンプトに、選ばれたbase_domainの
     エンジン事実・軸(a)適合チェックの指示文が正しく差し込まれていること
     （配線チェック）。
  3. classify_problem() のプロンプトには、もはや軸(a)関連の文言が含まれない
     こと（分離の回帰防止）。

実行方法:
  cd Backend && python3 -m pytest test_classify_problem_axis_a_fit_check.py -v
"""
from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).parent))

import domain_generator as dg  # noqa: E402


# ─────────────────────────────────────────────────────────────
# 1. get_domain_engine(): 実コードからのエンジン判定
# ─────────────────────────────────────────────────────────────

def test_truck_dispatcher_is_detected_as_cp():
    """TruckDispatcherはdocplex.cp採用（CVRP-TW、interval_var/sequence_var）。"""
    assert dg.get_domain_engine("TruckDispatcher") == "cp"


def test_store_site_is_detected_as_mip():
    """StoreSiteはdocplex.mp採用（拠点選定、2026-07-19にMIPへ切り替え済み）。"""
    assert dg.get_domain_engine("StoreSite") == "mip"


def test_nurse_shift_weekly_cap_is_detected_as_cp():
    assert dg.get_domain_engine("NurseShiftWeeklyCap") == "cp"


def test_unknown_domain_returns_unknown():
    """未登録のドメイン名を渡した場合は unknown を返す（例外にしない）。"""
    assert dg.get_domain_engine("NoSuchDomainXYZ") == "unknown"


# ─────────────────────────────────────────────────────────────
# 2. check_axis_a_fit() のプロンプト配線チェック（LLMはモック）
# ─────────────────────────────────────────────────────────────

def _fake_axis_a_call_llm(messages, model=None, temperature=1.0, max_tokens=1024):
    _fake_axis_a_call_llm.last_messages = messages
    return (
        '{"csplib_match": null, "recommended_engine": null, '
        '"mismatch": false, "concern": ""}'
    )


def _check_axis_a_and_capture(domain_name: str, hearing_texts: list, base_domain: str) -> str:
    with patch("llm.llm_client.call_llm", side_effect=_fake_axis_a_call_llm):
        dg.check_axis_a_fit(domain_name, hearing_texts, base_domain)
    system_msg, user_msg = _fake_axis_a_call_llm.last_messages
    return system_msg["content"] + "\n" + user_msg["content"]


def test_axis_a_fit_prompt_contains_only_selected_base_domain_engine():
    """
    check_axis_a_fit()は、旧_build_domain_engine_facts_block()のような
    全ドメイン一覧ではなく、選ばれたbase_domain 1件のみのエンジン事実を
    プロンプトに含めること（専用LLM呼び出しへの分離で、判断対象を絞り込む
    意図の検証）。
    """
    prompt = _check_axis_a_and_capture(
        "テスト業務", ["これはテスト用のヒアリング文です。"], "TruckDispatcher"
    )
    assert "- TruckDispatcher: CP Optimizer(docplex.cp)" in prompt
    # 他ドメインの「事実」の行（"- StoreSite: ..."形式）は含まれない
    # （全ドメイン一覧を渡していた旧_build_domain_engine_facts_block()方式との
    # 違い）。注: "StoreSite" という文字列自体はCSPLIB_REFERENCE.md（旧primary_problems.md）本文中の
    # 説明文（備考欄）に正当に含まれるため、事実ブロックの行形式でのみ判定する。
    assert "- StoreSite:" not in prompt


def test_axis_a_fit_prompt_reflects_actual_engine_of_selected_domain():
    """base_domainにStoreSite（mip）を渡した場合、事実ブロックがMIPを示すこと。"""
    prompt = _check_axis_a_and_capture(
        "テスト業務", ["これはテスト用のヒアリング文です。"], "StoreSite"
    )
    assert "StoreSite" in prompt
    assert "MIP" in prompt


def test_axis_a_fit_check_instruction_is_present():
    """軸(a)適合チェックの指示文（食い違うかどうかの判定基準）が含まれること。"""
    prompt = _check_axis_a_and_capture(
        "テスト業務", ["これはテスト用のヒアリング文です。"], "TruckDispatcher"
    )
    assert "食い違う" in prompt


def test_axis_a_fit_check_is_evidence_based_not_self_referential():
    """
    プロンプト中で、エンジン事実ブロックが「判断根拠ではなく事実確認用」と
    明記されていること（候補ドメイン自身のコードを正解源にしないという
    Koshoshiの指摘への対応が、専用LLM呼び出しに分離した後も残っていることの検証）。
    """
    prompt = _check_axis_a_and_capture(
        "テスト業務", ["これはテスト用のヒアリング文です。"], "TruckDispatcher"
    )
    assert "判断根拠ではなく事実確認用" in prompt


def test_check_axis_a_fit_returns_empty_concern_when_no_mismatch():
    with patch("llm.llm_client.call_llm", side_effect=_fake_axis_a_call_llm):
        result = dg.check_axis_a_fit(
            "テスト業務", ["これはテスト用のヒアリング文です。"], "TruckDispatcher"
        )
    assert result["mismatch"] is False
    assert result["concern"] == ""


def _fake_axis_a_call_llm_mismatch(messages, model=None, temperature=1.0, max_tokens=1024):
    return (
        '{"csplib_match": "prob063 Winner Determination", "recommended_engine": "MIP", '
        '"mismatch": true, "concern": "MeetingRoomはCP Optimizerを採用していますが、'
        'prob063 Winner Determinationの推奨解法はMIPです。このまま拡張してよいか、'
        '別の実装を検討すべきかご確認ください。"}'
    )


def test_check_axis_a_fit_surfaces_concern_when_mismatch():
    with patch("llm.llm_client.call_llm", side_effect=_fake_axis_a_call_llm_mismatch):
        result = dg.check_axis_a_fit(
            "テスト業務", ["これはテスト用のヒアリング文です。"], "MeetingRoom"
        )
    assert result["mismatch"] is True
    assert "MIP" in result["concern"]
    assert result["csplib_match"] == "prob063 Winner Determination"


# ─────────────────────────────────────────────────────────────
# 3. classify_problem() のプロンプトに軸(a)関連の文言が無いこと（分離の回帰防止）
# ─────────────────────────────────────────────────────────────

def _fake_classify_call_llm(messages, model=None, temperature=1.0, max_tokens=1024):
    _fake_classify_call_llm.last_messages = messages
    return (
        '{"match_type": "existing_domain", "base_domain": "TruckDispatcher", '
        '"reason": "dummy", "confidence": 0.9, '
        '"scenario_name_jp": "dummy", "scenario_description_jp": "dummy", '
        '"technical_directives": "", "missing_info": [], '
        '"is_dsl4_candidate": true}'
    )


def _classify_and_capture(domain_name: str, hearing_texts: list) -> str:
    with patch("llm.llm_client.call_llm", side_effect=_fake_classify_call_llm):
        dg.classify_problem(domain_name, hearing_texts)
    system_msg, user_msg = _fake_classify_call_llm.last_messages
    return system_msg["content"] + "\n" + user_msg["content"]


def test_classify_problem_prompt_no_longer_contains_engine_facts_block():
    """
    2026-07-31分離後、classify_problem()のプロンプトには「既存ドメインの
    採用エンジン」の事実ブロックはもう注入されない（check_axis_a_fit()側に
    移動したため）。
    """
    prompt = _classify_and_capture(
        "テスト業務", ["これはテスト用のヒアリング文です。"]
    )
    assert "既存ドメインの採用エンジン" not in prompt


def test_classify_problem_prompt_no_longer_contains_axis_a_instruction():
    """
    分離後、classify_problem()のプロンプトは軸(a)食い違い判定の指示文
    （「食い違う」等）を含まない。分類（match_type/base_domainの判定）に
    専念する内容になっていることの確認。
    """
    prompt = _classify_and_capture(
        "テスト業務", ["これはテスト用のヒアリング文です。"]
    )
    assert "食い違う" not in prompt


def test_build_domain_engine_facts_block_is_removed():
    """
    旧_build_domain_engine_facts_block()は利用箇所が無くなったため削除された。
    誤って復活・再参照されていないことの確認（属性が存在しないこと）。
    """
    assert not hasattr(dg, "_build_domain_engine_facts_block")


if __name__ == "__main__":
    test_truck_dispatcher_is_detected_as_cp()
    test_store_site_is_detected_as_mip()
    test_nurse_shift_weekly_cap_is_detected_as_cp()
    test_unknown_domain_returns_unknown()
    test_axis_a_fit_prompt_contains_only_selected_base_domain_engine()
    test_axis_a_fit_prompt_reflects_actual_engine_of_selected_domain()
    test_axis_a_fit_check_instruction_is_present()
    test_axis_a_fit_check_is_evidence_based_not_self_referential()
    test_check_axis_a_fit_returns_empty_concern_when_no_mismatch()
    test_check_axis_a_fit_surfaces_concern_when_mismatch()
    test_classify_problem_prompt_no_longer_contains_engine_facts_block()
    test_classify_problem_prompt_no_longer_contains_axis_a_instruction()
    test_build_domain_engine_facts_block_is_removed()
    print("全テストOK（get_domain_engine()実コード判定 + check_axis_a_fit()専用呼び出し配線 + "
          "classify_problem()分離の回帰防止の検証）")
