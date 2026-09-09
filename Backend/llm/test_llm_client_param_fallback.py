"""
test_llm_client_param_fallback.py

2026-09-09追加。_call_with_param_fallback / _find_offending_param（llm_client.py）
に対する回帰テスト。

背景: 2026-09-06のclaude-sonnet-5対応（temperatureが"deprecated"エラーになる
問題）は、「temperature」という名前と「deprecated」という語を決め打ちで検知し、
temperatureだけを外して1回だけ再試行する場当たり的な実装だった。モデル/ベンダー
を容易に切り替えられるようにする、という2026-09-09のrefactor/llm方針に基づき、
パラメータ名を決め打ちしない汎用機構（_call_with_param_fallback）に一般化した。

このテストは、以下を実APIを呼ばずに（モックのみで、dry-run方式で）証明する:
  1. 従来通りtemperature+deprecatedのケースはそのまま動く（後方互換）
  2. temperature以外の任意のパラメータ名でも同じ機構が働く（一般化の証明）
  3. 非対応と判断できないエラーは握りつぶさずそのまま送出する
  4. 複数パラメータが連鎖して非対応になるケースもmax_retries内で吸収する
  5. retries上限に達したら最後の例外を送出する（無限に隠蔽しない）
  6. _call_anthropic自体（ヘルパー単体でなく実際の呼び出し関数）でも、
     元のtemperature+deprecatedシナリオが再現・解決されることを確認する

pytestのfixture（monkeypatch等）や外部パッケージ（pytest.raises等）には依存
していない（save/restore方式・素のtry/exceptで書いている）。そのため
`pytest llm/test_llm_client_param_fallback.py` でも、pytest未インストールの
環境で `python3 llm/test_llm_client_param_fallback.py` を直接実行しても、
どちらでも全件実行できる。
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from llm import llm_client
from llm.llm_client import _call_with_param_fallback, _find_offending_param


def _assert_raises(exc_type, fn, match=None):
    try:
        fn()
    except exc_type as e:
        if match is not None:
            assert match in str(e), f"expected {match!r} in {e!r}"
        return
    raise AssertionError(f"expected {exc_type.__name__} to be raised, but nothing was raised")


# ── _find_offending_param ──────────────────────────────────────────

def test_finds_temperature_when_deprecated_mentioned():
    # 2026-09-06に実際に発生したエラー文言のパターン
    err = "Error: `temperature` is deprecated for this model"
    assert _find_offending_param(err, ["model", "temperature", "max_tokens"]) == "temperature"


def test_generalizes_to_a_different_parameter_name():
    # temperature専用ではないことの証明: 全く別のパラメータ名でも検出できる
    err = "top_p is not permitted for this model version"
    assert _find_offending_param(err, ["model", "top_p", "max_tokens"]) == "top_p"


def test_returns_none_when_no_unsupported_hint_present():
    # キー名が文面に出てきても、非対応を示す語が無ければ誤爆しない
    err = "temperature must be between 0 and 1"
    assert _find_offending_param(err, ["model", "temperature"]) is None


def test_returns_none_when_hint_present_but_no_candidate_key_matches():
    err = "some_unrelated_field is deprecated"
    assert _find_offending_param(err, ["model", "temperature", "max_tokens"]) is None


# ── _call_with_param_fallback ──────────────────────────────────────

def test_succeeds_on_first_try_without_touching_kwargs():
    calls = []

    def fake_call(**kw):
        calls.append(dict(kw))
        return "ok"

    result = _call_with_param_fallback(fake_call, {"model": "m", "temperature": 0.5})
    assert result == "ok"
    assert calls == [{"model": "m", "temperature": 0.5}]


def test_removes_offending_param_and_retries_temperature_case():
    calls = []

    def fake_call(**kw):
        calls.append(dict(kw))
        if "temperature" in kw:
            raise RuntimeError("`temperature` is deprecated for this model")
        return "ok-without-temperature"

    result = _call_with_param_fallback(
        fake_call, {"model": "claude-sonnet-5", "temperature": 0.0, "max_tokens": 100}
    )
    assert result == "ok-without-temperature"
    assert len(calls) == 2
    assert "temperature" in calls[0]
    assert "temperature" not in calls[1]
    assert calls[1] == {"model": "claude-sonnet-5", "max_tokens": 100}


def test_generalizes_to_non_temperature_parameter():
    # temperature以外のパラメータでも同じ機構が働くことの証明
    calls = []

    def fake_call(**kw):
        calls.append(dict(kw))
        if "top_p" in kw:
            raise RuntimeError("top_p is not supported for this model")
        return "ok"

    result = _call_with_param_fallback(fake_call, {"model": "future-model", "top_p": 0.9})
    assert result == "ok"
    assert len(calls) == 2
    assert "top_p" not in calls[1]


def test_absorbs_two_chained_incompatible_params_within_max_retries():
    calls = []

    def fake_call(**kw):
        calls.append(dict(kw))
        if "temperature" in kw:
            raise RuntimeError("temperature is deprecated for this model")
        if "top_p" in kw:
            raise RuntimeError("top_p is unsupported for this model")
        return "ok"

    result = _call_with_param_fallback(
        fake_call,
        {"model": "m", "temperature": 0.0, "top_p": 0.9, "max_tokens": 100},
        max_retries=2,
    )
    assert result == "ok"
    assert len(calls) == 3
    assert calls[-1] == {"model": "m", "max_tokens": 100}


def test_does_not_swallow_unidentifiable_errors():
    def fake_call(**kw):
        raise RuntimeError("connection reset by peer")

    _assert_raises(
        RuntimeError,
        lambda: _call_with_param_fallback(fake_call, {"model": "m", "temperature": 0.0}),
        match="connection reset",
    )


def test_raises_last_error_when_retries_exhausted():
    # 常に別の非対応パラメータ(temperature→top_p→top_k)が見つかり続け、
    # max_retries回外しても解決しない場合、無限にリトライせず最後のエラーを送出する。
    # a/b/cのような架空のキー名ではなく、実際にホワイトリストされている
    # droppableなパラメータ名で検証する（モデル名等の必須パラメータは対象外な
    # ので、そちらで検証しても意味がないため）。
    calls = []
    droppable_order = ["temperature", "top_p", "top_k"]

    def fake_call(**kw):
        calls.append(dict(kw))
        for name in droppable_order:
            if name in kw:
                raise RuntimeError(f"{name} is deprecated for this model")
        raise RuntimeError("should not reach here")

    _assert_raises(
        RuntimeError,
        lambda: _call_with_param_fallback(
            fake_call,
            {"model": "m", "temperature": 0.0, "top_p": 0.9, "top_k": 40},
            max_retries=2,
        ),
    )
    # 初回 + max_retries(2)回 = 3回呼ばれて諦める（temperature→top_p→top_kの順で
    # 2つ外しても3つ目のtop_kでまだ失敗するため、解決しないまま諦めるケース）
    assert len(calls) == 3


# ── _call_anthropic 本体での再現確認（実APIは呼ばない） ──────────────

class _FakeTextBlock:
    def __init__(self, text):
        self.text = text


class _FakeUsage:
    input_tokens = 10
    output_tokens = 5
    cache_read_input_tokens = 0
    cache_creation_input_tokens = 0


class _FakeMessage:
    def __init__(self, text):
        self.content = [_FakeTextBlock(text)]
        self.usage = _FakeUsage()


class _FakeStreamCM:
    """with client.messages.stream(**kwargs) as stream: ... を模倣する"""

    def __init__(self, final_message):
        self._final_message = final_message

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False

    def get_final_message(self):
        return self._final_message


class _FakeMessagesNamespace:
    def __init__(self, side_effects):
        # side_effects: 呼ばれるたびに1つ消費するリスト。例外インスタンスなら送出、
        # それ以外は_FakeStreamCMでラップして返す。
        self._side_effects = list(side_effects)

    def stream(self, **kwargs):
        effect = self._side_effects.pop(0)
        if isinstance(effect, Exception):
            raise effect
        return _FakeStreamCM(effect)


class _FakeAnthropicClient:
    def __init__(self, side_effects):
        self.messages = _FakeMessagesNamespace(side_effects)


def test_call_anthropic_recovers_from_temperature_deprecated_end_to_end():
    """
    2026-09-06に実機で起きたシナリオ（claude-sonnet-5でtemperatureがdeprecated
    エラーになる）を、_call_anthropic自体を通しで再現し、一般化後も解決される
    ことを確認する。実APIは呼ばず、_anthropic_clientをモックに差し替えるのみ
    （pytestのmonkeypatchではなく、手動でsave/restoreする）。
    """
    fake_client = _FakeAnthropicClient(
        side_effects=[
            RuntimeError("`temperature` is deprecated for this model"),
            _FakeMessage("こんにちは"),
        ]
    )
    original_client = llm_client._anthropic_client
    llm_client._anthropic_client = fake_client
    try:
        result = llm_client._call_anthropic(
            messages=[{"role": "user", "content": "hi"}],
            model="claude-sonnet-5",
            temperature=0.0,
            max_tokens=100,
        )
        assert result == "こんにちは"
    finally:
        llm_client._anthropic_client = original_client


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
