"""
test_llm_client_openai_gemini_dryrun.py

2026-09-09追加。「LLM_PROVIDERをopenai/geminiに切り替えたら実際に動くのか」を、
コードを読んだだけでなく実際に関数を実行して検証するテスト。

背景: refactor/llmの目的(2)「LLMモデル/ベンダーを容易に変更できるようにする」を
議論する中で、「そもそもOpenAI/Geminiへの切替が今の時点で本当に機能するかは
未検証（コードを読んだだけ）」という指摘があった。このテストは、実際の
openai/google-genaiパッケージがインストールされていない（このサンドボックス
環境の場合）状態でも、_openai_client/_gemini_clientをモックに差し替え、
Gemini側はSDKのtypesモジュール自体を軽量な偽物に差し替えることで、実APIを
一切呼ばずに（dry-run方式で）_call_openai/_call_gemini/_stage1_openai/
_stage1_gemini/call_llmのプロバイダー分岐ロジックを検証する。

このテストが検証しているのは「実際のOpenAI/Gemini APIの挙動と一致するか」
ではなく、「llm_client.py側のコード自体が、期待通りのリクエストを組み立て、
期待通りにレスポンスをパースするか」という、コードレベルの正しさである。
実際のAPIキーでの動作確認は別途必要（本テストはそれを代替しない）。
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from llm import llm_client


def _assert_raises(exc_type, fn, match=None):
    try:
        fn()
    except exc_type as e:
        if match is not None:
            assert match in str(e), f"expected {match!r} in {e!r}"
        return
    raise AssertionError(f"expected {exc_type.__name__} to be raised, but nothing was raised")


# ── google.genai.types の軽量スタブ ──────────────────────────────
# google-genaiパッケージ自体がインストールされていない環境でも
# _call_gemini/_stage1_gemini（内部でfrom google.genai import typesする）を
# importエラーなく実行できるようにする。任意のkwargsを受け取ってそのまま
# 属性として保持するだけの単純なプレースホルダ。実際の値の妥当性検証は
# 呼び出し元（_gemini_client.models.generate_content）のモック側で行う。
class _StubGenaiType:
    def __init__(self, **kwargs):
        self.__dict__.update(kwargs)


def _install_fake_google_genai_types():
    import types as pytypes

    fake_types_mod = pytypes.SimpleNamespace(
        Content=_StubGenaiType,
        Part=_StubGenaiType,
        GenerateContentConfig=_StubGenaiType,
        Tool=_StubGenaiType,
        FunctionDeclaration=_StubGenaiType,
        ToolConfig=_StubGenaiType,
        FunctionCallingConfig=_StubGenaiType,
    )
    fake_genai_mod = pytypes.SimpleNamespace(types=fake_types_mod)
    fake_google_mod = pytypes.SimpleNamespace(genai=fake_genai_mod)

    saved = {
        k: sys.modules.get(k)
        for k in ["google", "google.genai", "google.genai.types"]
    }
    sys.modules["google"] = fake_google_mod
    sys.modules["google.genai"] = fake_genai_mod
    sys.modules["google.genai.types"] = fake_types_mod
    return saved


def _restore_modules(saved):
    for k, v in saved.items():
        if v is None:
            sys.modules.pop(k, None)
        else:
            sys.modules[k] = v


# ── OpenAI: _call_openai ────────────────────────────────────────────

class _FakeOpenAIMessage:
    def __init__(self, content=None, tool_calls=None):
        self.content = content
        self.tool_calls = tool_calls


class _FakeOpenAIChoice:
    def __init__(self, message):
        self.message = message


class _FakeOpenAIResponse:
    def __init__(self, message):
        self.choices = [_FakeOpenAIChoice(message)]


class _FakeOpenAICompletions:
    def __init__(self, response_or_factory):
        self._response_or_factory = response_or_factory
        self.last_kwargs = None

    def create(self, **kwargs):
        self.last_kwargs = kwargs
        if callable(self._response_or_factory):
            return self._response_or_factory(kwargs)
        return self._response_or_factory


class _FakeOpenAIChat:
    def __init__(self, completions):
        self.completions = completions


class _FakeOpenAIClient:
    def __init__(self, response_or_factory):
        self.completions = _FakeOpenAICompletions(response_or_factory)
        self.chat = _FakeOpenAIChat(self.completions)


def test_call_openai_parses_response_text():
    fake_client = _FakeOpenAIClient(_FakeOpenAIResponse(_FakeOpenAIMessage(content="hello")))
    original = llm_client._openai_client
    llm_client._openai_client = fake_client
    try:
        result = llm_client._call_openai(
            messages=[{"role": "user", "content": "hi"}],
            model="gpt-4o",
            temperature=0.7,
            max_tokens=100,
        )
        assert result == "hello"
        assert fake_client.completions.last_kwargs["model"] == "gpt-4o"
        assert fake_client.completions.last_kwargs["max_tokens"] == 100
    finally:
        llm_client._openai_client = original


def test_call_openai_clamps_temperature_to_0_2_range():
    fake_client = _FakeOpenAIClient(_FakeOpenAIResponse(_FakeOpenAIMessage(content="x")))
    original = llm_client._openai_client
    llm_client._openai_client = fake_client
    try:
        llm_client._call_openai(messages=[], model="gpt-4o", temperature=5.0, max_tokens=10)
        assert fake_client.completions.last_kwargs["temperature"] == 2.0

        llm_client._call_openai(messages=[], model="gpt-4o", temperature=-3.0, max_tokens=10)
        assert fake_client.completions.last_kwargs["temperature"] == 0.0
    finally:
        llm_client._openai_client = original


def test_stage1_openai_extracts_tool_call_arguments():
    import json as _json

    class _FakeFunction:
        arguments = _json.dumps({"filter": "by_resource", "resource_id": "GC-1"})

    class _FakeToolCall:
        function = _FakeFunction()

    fake_client = _FakeOpenAIClient(
        _FakeOpenAIResponse(_FakeOpenAIMessage(content=None, tool_calls=[_FakeToolCall()]))
    )
    original = llm_client._openai_client
    llm_client._openai_client = fake_client
    try:
        result = llm_client._stage1_openai("質問", "system prompt", "gpt-4o-mini")
        assert result == {"filter": "by_resource", "resource_id": "GC-1"}
    finally:
        llm_client._openai_client = original


def test_stage1_openai_falls_back_when_no_tool_call():
    fake_client = _FakeOpenAIClient(
        _FakeOpenAIResponse(_FakeOpenAIMessage(content="text", tool_calls=None))
    )
    original = llm_client._openai_client
    llm_client._openai_client = fake_client
    try:
        result = llm_client._stage1_openai("質問", "", "gpt-4o-mini")
        assert result == {"filter": "summary_only"}
    finally:
        llm_client._openai_client = original


# ── Gemini: _call_gemini / _stage1_gemini ───────────────────────────

class _FakeGeminiResponse:
    def __init__(self, text):
        self.text = text


class _FakeGeminiModels:
    def __init__(self, response_or_factory):
        self._response_or_factory = response_or_factory
        self.last_kwargs = None

    def generate_content(self, **kwargs):
        self.last_kwargs = kwargs
        if callable(self._response_or_factory):
            return self._response_or_factory(kwargs)
        return self._response_or_factory


class _FakeGeminiClient:
    def __init__(self, response_or_factory):
        self.models = _FakeGeminiModels(response_or_factory)


def test_call_gemini_parses_response_text():
    saved_modules = _install_fake_google_genai_types()
    fake_client = _FakeGeminiClient(_FakeGeminiResponse(text="hello gemini"))
    original = llm_client._gemini_client
    llm_client._gemini_client = fake_client
    try:
        result = llm_client._call_gemini(
            messages=[
                {"role": "system", "content": "あなたはアシスタントです"},
                {"role": "user", "content": "こんにちは"},
                {"role": "assistant", "content": "はい"},
            ],
            model="gemini-2.0-flash",
            temperature=0.5,
            max_tokens=200,
        )
        assert result == "hello gemini"

        kw = fake_client.models.last_kwargs
        assert kw["model"] == "gemini-2.0-flash"
        # system メッセージが system_instruction に渡っていること
        assert kw["config"].system_instruction == "あなたはアシスタントです"
        # user/assistant が正しいroleに変換されていること（assistant→"model"）
        roles = [c.role for c in kw["contents"]]
        assert roles == ["user", "model"]
    finally:
        llm_client._gemini_client = original
        _restore_modules(saved_modules)


def test_stage1_gemini_extracts_function_call_args():
    saved_modules = _install_fake_google_genai_types()

    class _FakeFunctionCall:
        def __init__(self, args):
            self.args = args

        def keys(self):
            return self.args.keys()

        def __getitem__(self, k):
            return self.args[k]

    class _FakePart:
        def __init__(self, function_call=None):
            self.function_call = function_call

    class _FakeContent:
        def __init__(self, parts):
            self.parts = parts

    class _FakeCandidate:
        def __init__(self, content):
            self.content = content

    fake_call = _FakeFunctionCall({"filter": "by_container", "container_id": "C07"})
    fake_response = type(
        "R", (), {"candidates": [_FakeCandidate(_FakeContent([_FakePart(fake_call)]))]}
    )()

    fake_client = _FakeGeminiClient(fake_response)
    original = llm_client._gemini_client
    llm_client._gemini_client = fake_client
    try:
        result = llm_client._stage1_gemini("質問", "", "gemini-2.0-flash-lite")
        assert result == {"filter": "by_container", "container_id": "C07"}
    finally:
        llm_client._gemini_client = original
        _restore_modules(saved_modules)


def test_stage1_gemini_falls_back_when_no_function_call():
    saved_modules = _install_fake_google_genai_types()

    class _FakePart:
        function_call = None

    class _FakeContent:
        parts = [_FakePart()]

    class _FakeCandidate:
        content = _FakeContent()

    fake_response = type("R", (), {"candidates": [_FakeCandidate()]})()

    fake_client = _FakeGeminiClient(fake_response)
    original = llm_client._gemini_client
    llm_client._gemini_client = fake_client
    try:
        result = llm_client._stage1_gemini("質問", "", "gemini-2.0-flash-lite")
        assert result == {"filter": "summary_only"}
    finally:
        llm_client._gemini_client = original
        _restore_modules(saved_modules)


# ── call_llm / call_stage1_tool のプロバイダー分岐 ──────────────────

def test_call_llm_dispatches_to_openai_when_provider_is_openai():
    original_provider = llm_client.LLM_PROVIDER
    original_client = llm_client._openai_client
    original_fn = llm_client._call_openai
    calls = []
    try:
        llm_client.LLM_PROVIDER = "openai"
        llm_client._openai_client = object()  # is_available()がNoneでなければ良い

        def fake_call_openai(messages, model, temperature, max_tokens):
            calls.append((messages, model, temperature, max_tokens))
            return "dispatched-to-openai"

        llm_client._call_openai = fake_call_openai
        result = llm_client.call_llm([{"role": "user", "content": "hi"}])
        assert result == "dispatched-to-openai"
        assert len(calls) == 1
    finally:
        llm_client.LLM_PROVIDER = original_provider
        llm_client._openai_client = original_client
        llm_client._call_openai = original_fn


def test_call_llm_dispatches_to_gemini_when_provider_is_gemini():
    original_provider = llm_client.LLM_PROVIDER
    original_client = llm_client._gemini_client
    original_fn = llm_client._call_gemini
    calls = []
    try:
        llm_client.LLM_PROVIDER = "gemini"
        llm_client._gemini_client = object()

        def fake_call_gemini(messages, model, temperature, max_tokens):
            calls.append((messages, model, temperature, max_tokens))
            return "dispatched-to-gemini"

        llm_client._call_gemini = fake_call_gemini
        result = llm_client.call_llm([{"role": "user", "content": "hi"}])
        assert result == "dispatched-to-gemini"
        assert len(calls) == 1
    finally:
        llm_client.LLM_PROVIDER = original_provider
        llm_client._gemini_client = original_client
        llm_client._call_gemini = original_fn


def test_call_llm_raises_clear_error_when_client_not_configured():
    original_provider = llm_client.LLM_PROVIDER
    original_client = llm_client._openai_client
    try:
        llm_client.LLM_PROVIDER = "openai"
        llm_client._openai_client = None  # APIキー未設定を模擬
        _assert_raises(
            RuntimeError,
            lambda: llm_client.call_llm([{"role": "user", "content": "hi"}]),
            match="openai",
        )
    finally:
        llm_client.LLM_PROVIDER = original_provider
        llm_client._openai_client = original_client


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
