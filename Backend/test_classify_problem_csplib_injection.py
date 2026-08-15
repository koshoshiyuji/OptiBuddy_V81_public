"""
test_classify_problem_csplib_injection.py
==========================================

HANDOFF_2026-07-25_csplib_technical_directives_integration.md 5節「次回への宿題」
1点目の(a)(b)を検証するためのテスト。宿題本文の該当箇所:

  1. 実環境で、新規追加した基底問題（例: Meeting Scheduling相当のヒアリング文）を使って
     classify_problem()を実際に呼び出し、(a) match_typeがnew_domainのままであること、
     (b) technical_directivesに構造推定の1文が追記されること、の2点をKoshoshiに確認してほしい。

このファイルは、上記(a)(b)そのもの（実際のLLMの振る舞い）ではなく、その**前提となる
プロンプトの配線**を検証する。call_llm()をモックして実際のAPI呼び出しを行わないため、
sandbox環境（API키・docplex無し）でも実行できる。

検証内容:
  1. classify_problem()が実際にLLMへ渡すプロンプト（system+user）に、
     docs/CSPLIB_REFERENCE.md（旧primary_problems.md、2026-08-06統合。31問題版）の
     内容が正しく差し込まれていること。
  2. 新規追加した基底問題（Meeting Scheduling等）の「推奨解法」記述が
     プロンプトに含まれていること。
  3. 改訂した分類ルール2（base_problemはCVRP/RCPSPの2件のみ）の文言が
     プロンプトに含まれていること。
  4. technical_directivesの構造推定に関する指示文がプロンプトに含まれていること。

これはあくまで「プロンプトに必要な材料が渡っているか」の配線チェックであり、
LLMが実際にその指示通りに振る舞うか（(a)(b)本体）は、本ファイルの下部に記載した
test_classify_problem_csplib_e2e.py（実LLM環境が必要）で確認すること。

実行方法:
  cd Backend && python3 -m pytest test_classify_problem_csplib_injection.py -v
"""
from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).parent))

import domain_generator as dg  # noqa: E402


def _fake_call_llm(messages, model=None, temperature=1.0, max_tokens=1024):
    """
    実際のLLMを呼ばず、渡されたmessagesをテスト側から検証できるように
    そのままグローバル変数に退避してから、classify_problemが期待する形の
    ダミーJSON文字列を返す。
    """
    _fake_call_llm.last_messages = messages
    return (
        '{"match_type": "new_domain", "base_domain": null, '
        '"reason": "dummy", "confidence": 0.9, '
        '"scenario_name_jp": "dummy", "scenario_description_jp": "dummy", '
        '"technical_directives": "", "missing_info": [], '
        '"is_dsl4_candidate": true}'
    )


def _classify_and_capture(domain_name: str, hearing_texts: list) -> str:
    """classify_problem()を呼び、LLMに渡された最終プロンプト全文(system+user)を返す。"""
    with patch("llm.llm_client.call_llm", side_effect=_fake_call_llm):
        dg.classify_problem(domain_name, hearing_texts)
    system_msg, user_msg = _fake_call_llm.last_messages
    return system_msg["content"] + "\n" + user_msg["content"]


def test_primary_problems_md_is_injected():
    """CSPLIB_REFERENCE.md（旧primary_problems.md、31問題版）の内容がプロンプトに含まれること。"""
    prompt = _classify_and_capture(
        "テスト業務", ["これはテスト用のヒアリング文です。"]
    )
    assert "基底問題参考" in prompt, "CSPLIB_REFERENCE.mdの見出しがプロンプトに見当たらない"
    assert "prob046" in prompt, "Meeting Scheduling(prob046)がプロンプトに見当たらない"
    assert "prob022" in prompt, "Bus Driver Scheduling(prob022)がプロンプトに見当たらない"
    assert "prob063" in prompt, "Winner Determination(prob063)がプロンプトに見当たらない"


def test_recommended_approach_text_is_present():
    """新規追加した37問題版の「推奨解法」記述が含まれていること。"""
    prompt = _classify_and_capture(
        "テスト業務", ["これはテスト用のヒアリング文です。"]
    )
    assert "推奨解法" in prompt
    # Meeting SchedulingはCP、Bus Driver SchedulingはMIPとして記載されているはず
    assert "会議スケジューリング" in prompt or "Meeting Scheduling" in prompt


def test_base_problem_rule_is_restricted_to_documented_extensions():
    """
    分類ルール2が「転用先が明記されている基底問題」に限定されている文言を含むこと。
    これが無いと、37問題への拡張によってLLMが未実装ドメインをbase_problemと誤判定する
    リスクを防げない（HANDOFF_2026-07-25 2節で発見したリスクそのもの）。

    2026-08-06改訂: 従来はCVRP(prob082→TruckDispatcher)・RCPSP(prob131→
    LineChangeoverScheduler)の2件だったが、prob082のextension_ofがDARP構造判明により
    nullに修正されたため、現在はRCPSP側の1件のみが該当する。テスト名・アサーション文言を
    実態に合わせて更新した（件数のハードコードに依存しない形に変更）。
    """
    prompt = _classify_and_capture(
        "テスト業務", ["これはテスト用のヒアリング文です。"]
    )
    assert "RCPSP" in prompt
    assert "LineChangeoverScheduler" in prompt
    assert "既存ドメインへの転用先が明記されている" in prompt
    assert "転用先が明記されている" in prompt


def test_technical_directives_structural_inference_instruction_present():
    """technical_directivesの構造推定に関する指示文がプロンプトに含まれること。"""
    prompt = _classify_and_capture(
        "テスト業務", ["これはテスト用のヒアリング文です。"]
    )
    assert "構造推定" in prompt


if __name__ == "__main__":
    test_primary_problems_md_is_injected()
    test_recommended_approach_text_is_present()
    test_base_problem_rule_is_restricted_to_two_domains()
    test_technical_directives_structural_inference_instruction_present()
    print("全テストOK（プロンプト配線の検証のみ。実LLMの振る舞いは未検証）")
