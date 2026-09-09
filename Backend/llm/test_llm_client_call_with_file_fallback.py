"""
test_llm_client_call_with_file_fallback.py

2026-09-09追加。Gap 1対応: call_llm_with_file()にrepomix_path引数を追加し、
Anthropic以外（Files API相当が無いプロバイダー）でもrepomix添付の中身を
インラインでメッセージに含めるようにした変更に対する回帰テスト。

背景: 従来はLLM_PROVIDER!="anthropic"の場合、call_llm_with_file()は
repomix添付を丸ごと無視してcall_llm()にフォールバックしていた（Stage2の
ドメイン生成品質がAnthropicでしか得られないコードベース全体コンテキストに
依存していた）。実APIは一切呼ばず、call_llm自体をモックして「実際に何が
call_llmに渡ったか」を検証する。

このテストが検証しているのは「repomixの内容が実際にメッセージに含まれるか」
というコードレベルの正しさである。Anthropic経路（Files API添付）自体は
変更していないので、既存のtest_llm_client_param_fallback.py側の
test_call_anthropic_recovers_from_temperature_deprecated_end_to_end等で
引き続きカバーされている。
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from llm import llm_client


def test_non_anthropic_with_repomix_path_inlines_attachment(tmp_path=None):
    # tmp_pathはpytest fixtureだが、pytest無しでも動くよう手動でtempファイルを作る
    import tempfile

    fd, path = tempfile.mkstemp(suffix=".xml")
    with open(fd, "w", encoding="utf-8") as f:
        f.write("<repomix>dummy codebase content</repomix>")

    captured = {}

    def fake_call_llm(messages, max_tokens=4096, effort=None):
        captured["messages"] = messages
        captured["max_tokens"] = max_tokens
        return "generated-output"

    original_provider = llm_client.LLM_PROVIDER
    original_call_llm = llm_client.call_llm
    try:
        llm_client.LLM_PROVIDER = "openai"
        llm_client.call_llm = fake_call_llm

        result = llm_client.call_llm_with_file(
            messages=[{"role": "user", "content": "このドメインを生成して"}],
            file_id="",  # openaiなのでfile_idは使われない
            system="あなたは開発アシスタントです",
            max_tokens=24000,
            repomix_path=path,
        )

        assert result == "generated-output"
        msgs = captured["messages"]
        # systemメッセージが先頭に入っていること
        assert msgs[0] == {"role": "system", "content": "あなたは開発アシスタントです"}
        # repomixの中身がuserメッセージにインラインで含まれていること
        user_content = msgs[1]["content"]
        assert "dummy codebase content" in user_content
        assert "このドメインを生成して" in user_content
    finally:
        llm_client.LLM_PROVIDER = original_provider
        llm_client.call_llm = original_call_llm
        import os as _os
        _os.remove(path)


def test_non_anthropic_without_repomix_path_falls_back_to_plain_call_llm():
    captured = {}

    def fake_call_llm(messages, max_tokens=4096, effort=None):
        captured["messages"] = messages
        return "plain-output"

    original_provider = llm_client.LLM_PROVIDER
    original_call_llm = llm_client.call_llm
    try:
        llm_client.LLM_PROVIDER = "gemini"
        llm_client.call_llm = fake_call_llm

        result = llm_client.call_llm_with_file(
            messages=[{"role": "user", "content": "hi"}],
            file_id="",
            system="sys",
            max_tokens=100,
            repomix_path=None,
        )
        assert result == "plain-output"
        # 添付なしの元のmessagesがそのまま渡ること（systemも追加されない、従来通り）
        assert captured["messages"] == [{"role": "user", "content": "hi"}]
    finally:
        llm_client.LLM_PROVIDER = original_provider
        llm_client.call_llm = original_call_llm


def test_non_anthropic_with_nonexistent_repomix_path_falls_back_to_plain_call_llm():
    captured = {}

    def fake_call_llm(messages, max_tokens=4096, effort=None):
        captured["messages"] = messages
        return "plain-output"

    original_provider = llm_client.LLM_PROVIDER
    original_call_llm = llm_client.call_llm
    try:
        llm_client.LLM_PROVIDER = "openai"
        llm_client.call_llm = fake_call_llm

        result = llm_client.call_llm_with_file(
            messages=[{"role": "user", "content": "hi"}],
            file_id="",
            system="sys",
            max_tokens=100,
            repomix_path="/nonexistent/path/does_not_exist.xml",
        )
        assert result == "plain-output"
        assert captured["messages"] == [{"role": "user", "content": "hi"}]
    finally:
        llm_client.LLM_PROVIDER = original_provider
        llm_client.call_llm = original_call_llm


def test_anthropic_path_with_file_id_is_unaffected_by_repomix_path_arg():
    """
    Anthropicかつfile_idありの場合は、repomix_pathを渡してもFiles API経路
    （既存のcall_llm_with_file実装）がそのまま使われ、call_llmには行かないこと。

    2026-09-09修正: 当初のテストは「anthropicパッケージがこの環境に無ければ
    import anthropicでImportErrorになって止まるはず」という前提でtry/exceptして
    いたが、これはKoshoshiの実機（anthropicパッケージがインストール済み）で実行
    すると、ImportErrorにはならず本物のAnthropic API（Files API）へ実際にHTTP
    リクエストが飛んでしまい、ダミーのfile_id("file-abc123")が無効という400
    エラーになった。つまりこのテストは「dry-runのつもりが実際に課金APIを呼んで
    いた」という、まさに避けるべき事態を引き起こしていた。

    原因: call_llm_with_file()のAnthropic経路は、モック可能なモジュールレベルの
    _anthropic_clientを使わず、その場でanthropic.Anthropic(...)を新規生成して
    いる（_call_anthropicとは作りが異なる）。この関数内のimport anthropicが
    どの環境でも本物のSDKに到達しないよう、sys.modules["anthropic"]自体を
    偽物に差し替えることで、実行環境に関わらず確実にネットワークへ到達しない
    ようにした。
    """
    import types as pytypes

    class _FakeAnthropicStream:
        def __init__(self, msg):
            self._msg = msg

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            return False

        def get_final_message(self):
            return self._msg

    class _FakeBetaMessages:
        def stream(self, **kw):
            fake_usage = type("U", (), {})()
            fake_msg = type("M", (), {"content": [], "usage": fake_usage})()
            return _FakeAnthropicStream(fake_msg)

    class _FakeBeta:
        def __init__(self):
            self.messages = _FakeBetaMessages()

    class _FakeAnthropicClient:
        def __init__(self, api_key=None):
            self.beta = _FakeBeta()

    fake_anthropic_module = pytypes.SimpleNamespace(Anthropic=_FakeAnthropicClient)

    original_provider = llm_client.LLM_PROVIDER
    original_call_llm = llm_client.call_llm
    saved_anthropic_module = sys.modules.get("anthropic")
    call_llm_invoked = []

    def fake_call_llm(*args, **kwargs):
        call_llm_invoked.append(True)
        return "should-not-be-called"

    try:
        llm_client.LLM_PROVIDER = "anthropic"
        llm_client.call_llm = fake_call_llm
        sys.modules["anthropic"] = fake_anthropic_module

        llm_client.call_llm_with_file(
            messages=[{"role": "user", "content": "hi"}],
            file_id="file-abc123",
            system="sys",
            max_tokens=100,
            repomix_path="/tmp/whatever.xml",
        )
        assert call_llm_invoked == [], (
            "file_idありのAnthropic経路でcall_llm()（非Anthropicフォールバック）が"
            "呼ばれてしまっている"
        )
    finally:
        llm_client.LLM_PROVIDER = original_provider
        llm_client.call_llm = original_call_llm
        if saved_anthropic_module is None:
            sys.modules.pop("anthropic", None)
        else:
            sys.modules["anthropic"] = saved_anthropic_module


if __name__ == "__main__":
    import inspect

    mod = sys.modules[__name__]
    ran = 0
    for name, fn in sorted(inspect.getmembers(mod, inspect.isfunction)):
        if name.startswith("test_"):
            fn()
            ran += 1
            print(f"OK: {name}")
    print(f"全テスト成功（{ran}件）")
