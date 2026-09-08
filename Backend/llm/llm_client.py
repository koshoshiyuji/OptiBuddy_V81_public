# llm/llm_client.py
# マルチプロバイダー LLM アダプター
# 対応: anthropic / openai / gemini
# .env の LLM_PROVIDER で切り替え。デフォルトは anthropic。
#
# 必要パッケージ:
#   anthropic>=0.25
#   openai>=1.0
#   google-genai>=1.0   (pip install google-genai)

import hashlib
import json
import logging
import os
import re
import subprocess

logger = logging.getLogger(__name__)


def _log_usage(tag: str, usage) -> None:
    """
    2026-08-04追記: prompt cachingが実際に効いているかを検証する手段が
    どこにも無かった（レスポンスのusageを一度も見ていなかった）ため追加。
    cache_read_input_tokens（キャッシュヒット分）と
    cache_creation_input_tokens（新規キャッシュ書き込み分）を比較すれば
    ヒット率を実測できる。2026-07-23の「ヒット率15%程度」という指摘も
    体感ベースだったと思われるため、まずはこれで定量化する。
    """
    try:
        cr = getattr(usage, "cache_read_input_tokens", None) or 0
        cc = getattr(usage, "cache_creation_input_tokens", None) or 0
        it = getattr(usage, "input_tokens", None) or 0
        ot = getattr(usage, "output_tokens", None) or 0
        total_in = it + cr + cc
        hit_rate = (cr / total_in * 100) if total_in else 0.0
        logger.info(
            f"[llm_usage][{tag}] input={it} cache_read={cr} cache_creation={cc} "
            f"output={ot} cache_hit_rate={hit_rate:.1f}%"
        )
    except Exception as e:
        logger.warning(f"[llm_usage][{tag}] usage記録失敗: {e}")

try:
    from dotenv import load_dotenv
    load_dotenv()
except Exception:
    pass

# ── 環境変数 ──────────────────────────────────────────────
LLM_PROVIDER = os.environ.get("LLM_PROVIDER", "anthropic").lower()

# Anthropic
ANTHROPIC_API_KEY = os.environ.get("ANTHROPIC_API_KEY", "")
ANTHROPIC_MODEL   = os.environ.get("ANTHROPIC_MODEL", "claude-sonnet-4-20250514")
ANTHROPIC_HAIKU   = os.environ.get("ANTHROPIC_HAIKU_MODEL", "claude-haiku-4-5-20251001")

# OpenAI
OPENAI_API_KEY  = os.environ.get("OPENAI_API_KEY", "")
OPENAI_MODEL    = os.environ.get("OPENAI_MODEL", "gpt-4o")
OPENAI_MINI     = os.environ.get("OPENAI_MINI_MODEL", "gpt-4o-mini")  # stage1 用軽量モデル

# Gemini (google-genai SDK)
GEMINI_API_KEY  = os.environ.get("GEMINI_API_KEY", "")
GEMINI_MODEL    = os.environ.get("GEMINI_MODEL", "gemini-2.0-flash")
GEMINI_FLASH    = os.environ.get("GEMINI_FLASH_MODEL", "gemini-2.0-flash-lite")  # stage1 用


# ── クライアント初期化 ────────────────────────────────────

def _init_anthropic():
    if not ANTHROPIC_API_KEY:
        return None
    try:
        import anthropic
        return anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)
    except ImportError:
        print("[llm_client] anthropic パッケージが見つかりません: pip install anthropic")
        return None


def _init_openai():
    if not OPENAI_API_KEY:
        return None
    try:
        from openai import OpenAI
        return OpenAI(api_key=OPENAI_API_KEY)
    except ImportError:
        print("[llm_client] openai パッケージが見つかりません: pip install openai")
        return None


def _init_gemini():
    if not GEMINI_API_KEY:
        return None
    try:
        from google import genai
        return genai.Client(api_key=GEMINI_API_KEY)
    except ImportError:
        print("[llm_client] google-genai パッケージが見つかりません: pip install google-genai")
        return None


_anthropic_client = _init_anthropic()
_openai_client    = _init_openai()
_gemini_client    = _init_gemini()


def get_provider() -> str:
    """現在のプロバイダー名を返す"""
    return LLM_PROVIDER


def is_available() -> bool:
    """現在のプロバイダーが利用可能か確認"""
    if LLM_PROVIDER == "anthropic":
        return _anthropic_client is not None
    if LLM_PROVIDER == "openai":
        return _openai_client is not None
    if LLM_PROVIDER == "gemini":
        return _gemini_client is not None
    return False


def get_raw_anthropic_client():
    """
    tool_use付きの複数ターン会話（デバッグエージェント等）は call_llm() の
    単発テキスト抽象化では表現できないため、初期化済みのAnthropicクライアントを
    直接返す。プロバイダーがanthropicでない、またはAPIキー未設定の場合はNone。
    """
    return _anthropic_client


def default_model() -> str:
    """現在のプロバイダーのデフォルトモデルを返す"""
    if LLM_PROVIDER == "openai":
        return OPENAI_MODEL
    if LLM_PROVIDER == "gemini":
        return GEMINI_MODEL
    return ANTHROPIC_MODEL


def fast_model() -> str:
    """Stage1 用の軽量モデルを返す"""
    if LLM_PROVIDER == "openai":
        return OPENAI_MINI
    if LLM_PROVIDER == "gemini":
        return GEMINI_FLASH
    return ANTHROPIC_HAIKU


# ── JSON 抽出ユーティリティ ───────────────────────────────

def extract_json(text: str):
    """
    LLM テキスト出力から最初の有効な JSON 配列 or オブジェクトを抽出する。
 
    対処するケース:
      1. 正常な JSON テキスト
      2. ```json ... ``` マークダウンフェンス付き
      3. max_tokens 超過で JSON が途中で切れた（最も多いケース）
      4. テキスト前後に説明文が混入
 
    途中で切れた JSON の修復戦略:
      - 配列の場合: 最後の完全な要素までで閉じる（不完全な末尾要素を除去）
      - オブジェクトの場合: 最後の完全なキーバリューペアまでで閉じる
    """
    if not text or not text.strip():
        raise ValueError("[extract_json] 空のテキストが渡されました")
 
    # ── Step 1: そのまま parse できればそれが正解 ──
    try:
        return json.loads(text.strip())
    except json.JSONDecodeError:
        pass
 
    # ── Step 2: マークダウンフェンスを除去 ──
    # 2026-08-08修正: 従来はre.search()で最初の```...```ペアだけを試していたが、
    # 応答テキスト中に複数のフェンス区間がある場合（例: 前置き説明用のコード例
    # フェンス→本題のJSONフェンス、または引用元テキスト自体にフェンスが含まれる
    # ケース）、最初のペアが本来のJSONでないと即座に失敗し、Step 3以降の復旧も
    # 効かずJSONパース失敗として扱われることがあった（PatientTransportPlanner
    # 登録時のhearing_dsl_gapsチェックで実際に発生した既知の軽微な不具合）。
    # re.findall()で全フェンス区間を洗い出し、パースが通るものが見つかるまで
    # 順に試す（単一フェンスの既存ケースは従来通り1回で成功する）。
    for fence_body in re.findall(r'```(?:json)?\s*([\s\S]*?)```', text):
        try:
            return json.loads(fence_body.strip())
        except json.JSONDecodeError:
            continue
 
    # ── Step 3: [ または { の開始位置を探してスライス ──
    start = -1
    is_array = False
    for i, ch in enumerate(text):
        if ch == '[':
            start = i
            is_array = True
            break
        if ch == '{':
            start = i
            is_array = False
            break
 
    if start == -1:
        raise ValueError(f"[extract_json] JSON の開始文字が見つかりません: {text[:200]}")
 
    candidate = text[start:]
 
    # ── Step 4: そのまま parse を試みる（フェンスなし正常ケース）──
    try:
        return json.loads(candidate)
    except json.JSONDecodeError:
        pass
 
    # ── Step 5: 途中で切れた JSON の修復 ──
    repaired = _repair_truncated_json(candidate, is_array)
    if repaired is not None:
        return repaired
 
    # ── Step 6: 修復も失敗 → 詳細ログを出して例外 ──
    print(f"[extract_json] 全手段失敗。テキスト先頭500文字:\n{text[:500]}")
    raise ValueError(f"[extract_json] JSON パース失敗: {text[:200]}")
 
 
def _repair_truncated_json(text: str, is_array: bool):
    """
    max_tokens 超過等で末尾が途切れた JSON を修復して返す。
    修復できない場合は None を返す。
 
    戦略:
      配列 [...] → 最後に完全にパースできた要素までを拾い、] で閉じる
      オブジェクト {...} → 最後の完全な } まで拾い、残りのカッコを閉じる
    """
    if is_array:
        return _repair_array(text)
    else:
        return _repair_object(text)
 
 
def _repair_array(text: str):
    """
    途切れた JSON 配列を修復する。
    各要素を貪欲にパースして、パースできた要素だけで配列を再構成する。
    """
    if not text.startswith('['):
        return None
 
    # 要素ごとに分離して個別にパースを試みる
    # json.JSONDecoder の raw_decode を使って位置ベースでパース
    decoder = json.JSONDecoder()
    results = []
    pos = 1  # '[' の次
 
    while pos < len(text):
        # 空白・カンマをスキップ
        while pos < len(text) and text[pos] in ' \t\n\r,':
            pos += 1
 
        if pos >= len(text):
            break
 
        if text[pos] == ']':
            # 正常な配列終端
            try:
                full = json.loads(text[:pos + 1])
                return full
            except Exception:
                break
 
        # 次の要素を試みる
        try:
            obj, end_pos = decoder.raw_decode(text, pos)
            results.append(obj)
            pos = end_pos
        except json.JSONDecodeError:
            # ここで失敗 = 末尾の不完全な要素に達した
            break
 
    if results:
        print(f"[extract_json] 途切れた配列を修復: {len(results)} 件の完全な要素を回収")
        return results
 
    return None
 
 
def _repair_object(text: str):
    """
    途切れた JSON オブジェクトを修復する。
    まず、開き括弧の深さを追跡し、トップレベルの } まで平衡が取れている場合は
    そこで切り取って閉じる（従来方式）。

    それで復旧できない場合（例: {"baseline": {...}, "tight": {...},
    "infeasible": {...} のようにトップレベルが複数キーを持つオブジェクトで、
    "tight" や "infeasible" の生成途中で切れ、外側の } に一度も到達していない
    場合）は、キー単位の部分修復（_repair_object_partial_keys）にフォールバック
    し、完全にパースできたキーだけを回収する（baseline は完全なら
    tight/infeasible が欠損していても baseline だけは救済する）。
    """
    if not text.startswith('{'):
        return None

    depth = 0
    in_string = False
    escape = False
    last_balanced_pos = -1
 
    for i, ch in enumerate(text):
        if escape:
            escape = False
            continue
        if ch == '\\' and in_string:
            escape = True
            continue
        if ch == '"':
            in_string = not in_string
            continue
        if in_string:
            continue
        if ch == '{':
            depth += 1
        elif ch == '}':
            depth -= 1
            if depth == 0:
                last_balanced_pos = i
 
    if last_balanced_pos > 0:
        truncated = text[:last_balanced_pos + 1]
        try:
            result = json.loads(truncated)
            print(f"[extract_json] 途切れたオブジェクトを修復: {last_balanced_pos + 1} 文字")
            return result
        except json.JSONDecodeError:
            pass

    # ── トップレベルの } に一度も到達していない（複数キーの途中で切れた）場合の
    #    フォールバック: キー単位の部分修復を試みる ──
    partial = _repair_object_partial_keys(text)
    if partial:
        return partial

    return None


def _repair_object_partial_keys(text: str):
    """
    トップレベルオブジェクトが複数キーを持ち、外側の } まで到達せず
    全体修復（_repair_object の平衡追跡）が失敗した場合のフォールバック。

    先頭から順に "key": <値> をデコードし、完全にパースできたキーだけを
    dict として回収する。値（またはキー自体）の途中で切れている箇所に
    達したら、そこで回収を打ち切る。1つもキーを回収できなければ None を返す。

    例: {"baseline": {...完全...}, "tight": {...完全...}, "infeasible": {...途中で切断
        → {"baseline": ..., "tight": ...} を返す（infeasible は捨てる）。
    """
    if not text.startswith('{'):
        return None

    decoder = json.JSONDecoder()
    pos = 1  # '{' の次
    n = len(text)
    result = {}

    while pos < n:
        # 空白・カンマをスキップ
        while pos < n and text[pos] in ' \t\n\r,':
            pos += 1
        if pos >= n or text[pos] == '}':
            break

        # キー文字列をデコード（キー自体が途切れている場合はここで打ち切り）
        if text[pos] != '"':
            break
        try:
            key, key_end = decoder.raw_decode(text, pos)
        except json.JSONDecodeError:
            break
        pos = key_end

        # コロンを探す
        while pos < n and text[pos] in ' \t\n\r':
            pos += 1
        if pos >= n or text[pos] != ':':
            break
        pos += 1
        while pos < n and text[pos] in ' \t\n\r':
            pos += 1
        if pos >= n:
            break

        # 値をデコード（値の途中で切れている = 打ち切り）
        try:
            value, val_end = decoder.raw_decode(text, pos)
        except json.JSONDecodeError:
            break

        result[key] = value
        pos = val_end

    if result:
        print(f"[extract_json] 途切れたオブジェクトをキー単位で部分修復: {list(result.keys())} を回収")
        return result

    return None


# ── 統一メッセージ形式 ────────────────────────────────────
# messages は OpenAI 互換: [{"role": "system"|"user"|"assistant", "content": "..."}]

def _extract_text_from_content(content_blocks) -> str:
    """
    2026-09-06追加（Koshoshi合意）: claude-sonnet-5系モデルへの切り替えに伴い、
    レスポンスの`content`に思考過程のブロック（ThinkingBlock、`.text`属性を
    持たない）が本文ブロックより前に入るようになったことが判明した
    （`'ThinkingBlock' object has no attribute 'text'`エラーで発覚）。
    従来の`resp.content[0].text`という決め打ちの読み方は、先頭が思考ブロックの
    場合に壊れる。ここでは`content`の各ブロックを見て、実際にテキストを持つ
    ブロック（type=="text"、または`.text`属性を持つブロック）だけを連結して
    返す（思考ブロック等は無視する）。
    """
    parts = []
    for block in content_blocks:
        text = getattr(block, "text", None)
        if text:
            parts.append(text)
    return "".join(parts)


def _cacheable_system(system_prompt) -> list | str:
    """
    2026-07-23追記: Anthropicのprompt cachingを、systemプロンプトを渡す
    ほぼ全ての呼び出し箇所（_call_anthropic/call_llm_with_file/_stage1_anthropic）に
    まとめて適用するための共通ヘルパー。

    これまでcache_control(ephemeral)は、domain_generator.pyのStage2ファイル生成
    （repomix添付部分）とdebug_agent.py（会話の最後のブロック）の2箇所にしか
    付いておらず、relax_interface.py・llm_interface.py・domain_generator.pyの
    分類/ヒアリング系など、ほぼ全てのsystemプロンプト呼び出しがキャッシュ対象外
    だった（Koshoshiの指摘: キャッシュヒット率が15%程度と低い）。各system
    プロンプトはリクエストのたびに同一内容で送られる静的な文字列なので、
    本来キャッシュが効きやすい部分。

    system_promptが既にブロックのリスト（呼び出し元で既にcache_control等を
    設定済みのケース）ならそのまま通す。文字列の場合のみ、末尾ブロックとして
    cache_control(ephemeral)を付けたテキストブロック1件に包む。
    """
    # 2026-08-04追記: デフォルトTTLは2026-03に1時間→5分へサイレント変更された
    # （Anthropic公式ドキュメント確認済み）。Stage2やdebug_agentの呼び出し間隔は
    # 人間の確認待ち（実測で全体の12〜74%）を挟むため5分を超えることが多く、
    # 明示的にttlを指定しないと事実上キャッシュがほぼ効かない状態になっていた
    # （2026-07-23時点で指摘されていた「ヒット率15%程度」の主因と推定）。
    # 1hキャッシュは書き込み単価が2倍になるが、3回目の読み取りで元が取れる
    # （読み取り単価が基本入力の1/10のため）ので、この用途では明確に得。
    if isinstance(system_prompt, list):
        return system_prompt
    return [{"type": "text", "text": system_prompt, "cache_control": {"type": "ephemeral", "ttl": "1h"}}]


def _call_anthropic(
    messages: list, model: str, temperature: float, max_tokens: int,
    effort: str | None = None,
) -> str:
    """Anthropic Messages API を呼び出してテキストを返す

    2026-09-06追加（Koshoshi合意）: claude-sonnet-5系モデルはadaptive thinkingを
    持ち、思考にかけたトークンもmax_tokens予算から消費される（従来の
    extended thinkingのbudget_tokensのような別枠ではない）。これにより、
    従来モデル向けに設定していたmax_tokensのままだと、思考だけで予算を
    使い切り本文が0文字になる事故が発生した（実機確認: max_tokens=2048の
    呼び出しでoutput=2048ちょうど・本文空、というbackend.logの実測に基づく）。

    effortはNoneのままにしておけば、thinking/output_configを一切kwargsに
    追加しない（＝現行の.env設定モデルや将来のtemperature非対応と同様の
    非対応モデルでも安全に動く）。呼び出し元が明示的にeffort="low"/"high"を
    渡した場合のみ、そのタスクの複雑さに応じたeffort設定を付与する
    （呼び出し元のmax_tokensは合わせて底上げ済みであることが前提）。
    """
    system_prompt = ""
    user_messages = []
    for m in messages:
        if m["role"] == "system":
            system_prompt = m["content"]
        else:
            user_messages.append({"role": m["role"], "content": m["content"]})

    kwargs = dict(
        model=model,
        max_tokens=max_tokens,
        messages=user_messages,
        temperature=max(0.0, min(1.0, temperature)),
    )
    if system_prompt:
        kwargs["system"] = _cacheable_system(system_prompt)
    if effort is not None:
        # 2026-09-06追加（実機確認・Koshoshi合意）: thinkingを有効にすると、
        # Anthropic APIは「temperatureはthinking有効時は1のみ許可
        # （'temperature' may only be set to 1 when thinking is enabled or
        # in adaptive mode）」という制約を課す。呼び出し元は判定タスクの
        # 決定性を保つためtemperature=0を渡していることが多いため、そのまま
        # 送るとエラーになる。thinking有効時はAPI側のデフォルト（実質1相当）に
        # 委ね、temperatureキー自体を送らないようにする。
        kwargs.pop("temperature", None)
        kwargs["thinking"] = {"type": "adaptive"}
        kwargs["output_config"] = {"effort": effort}

    # 2026-09-06追加（Koshoshi合意）: claude-sonnet-5系モデルへの切り替えに伴い、
    # 「`temperature` is deprecated for this model」という400エラーが判明した
    # （新世代モデルではサンプリング温度ではなくadaptive thinking/effortで制御する
    # 方針に変わったと見られる）。model名をハードコードで分岐すると将来の新モデルで
    # また同じ修正が必要になるため、まず`temperature`ありで呼び、この特定のエラー
    # だけを検知したら`temperature`無しで1回だけ再試行する方式にする（後方互換:
    # 従来モデルはtemperatureありのまま動く）。
    try:
        with _anthropic_client.messages.stream(**kwargs) as stream:
            resp = stream.get_final_message()
    except Exception as e:
        if "temperature" in str(e) and "deprecated" in str(e):
            logger.warning(
                f"[_call_anthropic] model={model} は temperature 未対応のため、"
                f"temperature無しで再試行します: {e}"
            )
            kwargs_no_temp = {k: v for k, v in kwargs.items() if k != "temperature"}
            with _anthropic_client.messages.stream(**kwargs_no_temp) as stream:
                resp = stream.get_final_message()
        else:
            raise
    _log_usage(f"_call_anthropic:{model}", resp.usage)
    return _extract_text_from_content(resp.content)


def _call_openai(messages: list, model: str, temperature: float, max_tokens: int) -> str:
    """OpenAI Chat Completions API を呼び出してテキストを返す"""
    # OpenAI は system ロールをそのまま messages に含められる
    resp = _openai_client.chat.completions.create(
        model=model,
        messages=messages,
        temperature=max(0.0, min(2.0, temperature)),
        max_tokens=max_tokens,
    )
    return resp.choices[0].message.content


def _call_gemini(messages: list, model: str, temperature: float, max_tokens: int) -> str:
    """google-genai SDK で Gemini を呼び出してテキストを返す"""
    from google.genai import types as gtypes

    # system を先頭から分離
    system_parts = []
    chat_messages = []
    for m in messages:
        if m["role"] == "system":
            system_parts.append(m["content"])
        elif m["role"] == "assistant":
            chat_messages.append(gtypes.Content(
                role="model",
                parts=[gtypes.Part(text=m["content"])],
            ))
        else:
            chat_messages.append(gtypes.Content(
                role="user",
                parts=[gtypes.Part(text=m["content"])],
            ))

    config = gtypes.GenerateContentConfig(
        temperature=max(0.0, min(2.0, temperature)),
        max_output_tokens=max_tokens,
        system_instruction="\n".join(system_parts) if system_parts else None,
    )

    resp = _gemini_client.models.generate_content(
        model=model,
        contents=chat_messages,
        config=config,
    )
    return resp.text


# ── 公開 API ─────────────────────────────────────────────

def call_llm(
    messages: list,
    model: str | None = None,
    temperature: float = 1.0,
    max_tokens: int = 1024,
    effort: str | None = None,
) -> str:
    """
    現在のプロバイダーでテキストを返す統一関数。
    model=None のとき default_model() を使用。
    effort: "low"/"high"等（Anthropic限定・2026-09-06追加）。Noneなら従来通り
    thinking/output_configを付与しない。openai/geminiプロバイダーでは無視される。
    """
    if not is_available():
        raise RuntimeError(
            f"[llm_client] プロバイダー '{LLM_PROVIDER}' のクライアントが初期化されていません。"
            f" API キーを .env に設定してください。"
        )

    m = model or default_model()

    if LLM_PROVIDER == "anthropic":
        return _call_anthropic(messages, m, temperature, max_tokens, effort=effort)
    if LLM_PROVIDER == "openai":
        return _call_openai(messages, m, temperature, max_tokens)
    if LLM_PROVIDER == "gemini":
        return _call_gemini(messages, m, temperature, max_tokens)

    raise ValueError(f"[llm_client] 未対応のプロバイダー: {LLM_PROVIDER}")


def call_llm_json(
    messages: list,
    model: str | None = None,
    temperature: float = 1.0,
    max_tokens: int = 1024,
    effort: str | None = None,
):
    """
    call_llm の結果を JSON パースして返す。
    llm_interface.py / relax_interface.py の call_llm_json の置き換え。
    """
    text = call_llm(messages, model=model, temperature=temperature, max_tokens=max_tokens, effort=effort)
    return extract_json(text)


# ── Stage1 用 tool_use / function calling ────────────────
# _stage1_identify_filter が Anthropic の tool_use API を直接使っているため、
# プロバイダーごとに function calling を吸収する。

FETCH_TASKS_TOOL_SCHEMA = {
    "name": "fetch_tasks",
    "description": "質問に回答するために必要なタスクデータのフィルタ条件を指定する",
    "parameters": {
        "type": "object",
        "properties": {
            "filter": {
                "type": "string",
                "enum": ["by_resource", "by_container", "summary_only"],
                "description": (
                    "by_resource: 特定クレーン/リソースのタスク, "
                    "by_container: 特定コンテナのタスク, "
                    "summary_only: 集計サマリーのみで回答可能"
                ),
            },
            "resource_id":  {"type": "string", "description": "クレーンID (例: GC-1, RC-2)"},
            "container_id": {"type": "string", "description": "コンテナID (例: C07)"},
        },
        "required": ["filter"],
    },
}

# Anthropic 形式 (input_schema)
_FETCH_TASKS_ANTHROPIC = {
    "name": FETCH_TASKS_TOOL_SCHEMA["name"],
    "description": FETCH_TASKS_TOOL_SCHEMA["description"],
    "input_schema": FETCH_TASKS_TOOL_SCHEMA["parameters"],
}

# OpenAI 形式
_FETCH_TASKS_OPENAI = {
    "type": "function",
    "function": FETCH_TASKS_TOOL_SCHEMA,
}

# Gemini 形式
_FETCH_TASKS_GEMINI = FETCH_TASKS_TOOL_SCHEMA  # google-genai は dict をそのまま受け取る


def call_stage1_tool(
    prompt: str,
    system: str = "",
    model: str | None = None,
) -> dict:
    """
    Stage1 用 function calling。fetch_tasks ツールの引数を返す。
    tool_use が取れない場合は {"filter": "summary_only"} にフォールバック。
    """
    m = model or fast_model()

    try:
        if LLM_PROVIDER == "anthropic":
            return _stage1_anthropic(prompt, system, m)
        if LLM_PROVIDER == "openai":
            return _stage1_openai(prompt, system, m)
        if LLM_PROVIDER == "gemini":
            return _stage1_gemini(prompt, system, m)
    except Exception as e:
        print(f"[llm_client] stage1 tool call failed: {e}")

    return {"filter": "summary_only"}


def _stage1_anthropic(prompt: str, system: str, model: str) -> dict:
    kwargs = dict(
        model=model,
        max_tokens=256,
        tools=[_FETCH_TASKS_ANTHROPIC],
        tool_choice={"type": "tool", "name": "fetch_tasks"},
        messages=[{"role": "user", "content": prompt}],
    )
    if system:
        kwargs["system"] = _cacheable_system(system)

    resp = _anthropic_client.messages.create(**kwargs)
    tool_use = next((b for b in resp.content if b.type == "tool_use"), None)
    if not tool_use:
        return {"filter": "summary_only"}

    inp = tool_use.input
    if isinstance(inp, dict):
        return inp
    if isinstance(inp, str):
        try:
            parsed = json.loads(inp)
            if isinstance(parsed, dict):
                return parsed
        except Exception:
            pass
    return {"filter": "summary_only"}


def _stage1_openai(prompt: str, system: str, model: str) -> dict:
    messages = []
    if system:
        messages.append({"role": "system", "content": system})
    messages.append({"role": "user", "content": prompt})

    resp = _openai_client.chat.completions.create(
        model=model,
        max_tokens=256,
        tools=[_FETCH_TASKS_OPENAI],
        tool_choice={"type": "function", "function": {"name": "fetch_tasks"}},
        messages=messages,
    )
    tool_call = None
    choice = resp.choices[0]
    if choice.message.tool_calls:
        tool_call = choice.message.tool_calls[0]

    if not tool_call:
        return {"filter": "summary_only"}

    try:
        args = json.loads(tool_call.function.arguments)
        if isinstance(args, dict):
            return args
    except Exception:
        pass
    return {"filter": "summary_only"}


def _stage1_gemini(prompt: str, system: str, model: str) -> dict:
    from google.genai import types as gtypes

    tool = gtypes.Tool(
        function_declarations=[gtypes.FunctionDeclaration(**_FETCH_TASKS_GEMINI)]
    )
    config = gtypes.GenerateContentConfig(
        tools=[tool],
        tool_config=gtypes.ToolConfig(
            function_calling_config=gtypes.FunctionCallingConfig(
                mode="ANY",
                allowed_function_names=["fetch_tasks"],
            )
        ),
        system_instruction=system or None,
    )

    contents = [gtypes.Content(role="user", parts=[gtypes.Part(text=prompt)])]
    resp = _gemini_client.models.generate_content(
        model=model,
        contents=contents,
        config=config,
    )

    for part in resp.candidates[0].content.parts:
        if part.function_call:
            args = dict(part.function_call.args)
            if isinstance(args, dict):
                return args

    return {"filter": "summary_only"}



# ── repomix ハッシュ管理 + Files API ──────────────────────────────────────────
#
# 使い方:
#   file_id = upload_repomix_if_changed()  # repomix実行→ハッシュ比較→必要時アップロード
#   response = call_llm_with_file(messages, file_id, system="...")
#
# ハッシュキャッシュ: プロジェクトルートの llm_file_cache.json に保存
# repomix設定:       プロジェクトルートの repomix.domain-addition.json を使用

# プロジェクトルート = Backend/ の親
_PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
_CACHE_FILE   = os.path.join(_PROJECT_ROOT, "llm_file_cache.json")
_REPOMIX_CONFIG = os.path.join(_PROJECT_ROOT, "repomix.domain-addition.json")
_REPOMIX_OUTPUT = os.path.join(_PROJECT_ROOT, "repomix-domain-addition.xml")


def _load_cache() -> dict:
    try:
        with open(_CACHE_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}


def _save_cache(cache: dict) -> None:
    with open(_CACHE_FILE, "w", encoding="utf-8") as f:
        json.dump(cache, f, ensure_ascii=False, indent=2)


def _file_hash(path: str) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


def _run_repomix() -> str:
    """repomix を実行して出力XMLのパスを返す。失敗時は例外を投げる。"""
    result = subprocess.run(
        ["repomix", "--config", _REPOMIX_CONFIG],
        cwd=_PROJECT_ROOT,
        capture_output=True,
        text=True,
        timeout=60,
    )
    if result.returncode != 0:
        raise RuntimeError(f"repomix failed: {result.stderr}")
    if not os.path.exists(_REPOMIX_OUTPUT):
        raise FileNotFoundError(f"repomix output not found: {_REPOMIX_OUTPUT}")
    return _REPOMIX_OUTPUT


def upload_repomix_if_changed() -> str:
    """
    repomixを実行し、前回からハッシュが変わっていればFiles APIにアップロードする。
    変わっていなければキャッシュのfile_idを返す。
    Anthropic以外のプロバイダーでは空文字を返す。
    """
    if LLM_PROVIDER != "anthropic":
        return ""

    # repomix実行
    xml_path = _run_repomix()
    current_hash = _file_hash(xml_path)

    # キャッシュ確認
    cache = _load_cache()
    entry = cache.get("repomix-domain-addition", {})
    if entry.get("hash") == current_hash and entry.get("file_id"):
        return entry["file_id"]

    # ハッシュが変わった or 初回 → アップロード
    import anthropic
    client = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)
    with open(xml_path, "rb") as f:
        response = client.beta.files.upload(
            file=(os.path.basename(xml_path), f, "text/plain"),
        )
    file_id = response.id

    # キャッシュ更新
    cache["repomix-domain-addition"] = {
        "hash": current_hash,
        "file_id": file_id,
    }
    _save_cache(cache)

    return file_id


def call_llm_with_file(
    messages: list,
    file_id: str,
    system: str = "",
    max_tokens: int = 4096,
    effort: str | None = None,
) -> str:
    """
    Anthropic Files API の file_id を添付してLLMを呼び出す。
    file_id が空の場合は通常の call_llm にフォールバック。
    effort: "low"/"high"等（2026-09-06追加）。Noneなら従来通り付与しない。
    """
    if LLM_PROVIDER != "anthropic" or not file_id:
        return call_llm(messages, max_tokens=max_tokens, effort=effort)

    import anthropic
    client = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)

    # file_id をユーザーメッセージの先頭に添付。
    # 2026-07-17: repomix添付ファイルはコードベースが変わらない限り毎回同一内容なので、
    # cache_control(ephemeral)を付けてprompt cachingの対象にする。呼び出し元
    # (domain_generator.generate_domain_files)が既にcontentをブロックのリストとして
    # 渡してくる場合（静的パート/動的パートに分割済み）はそのリストの先頭に文書ブロックを
    # 追加し、従来通り文字列で渡された場合はテキストブロック化してから追加する。
    augmented = []
    for i, msg in enumerate(messages):
        if i == 0 and msg["role"] == "user":
            doc_block = {
                "type": "document",
                "source": {"type": "file", "file_id": file_id},
                "cache_control": {"type": "ephemeral", "ttl": "1h"},
            }
            existing_content = msg["content"]
            if isinstance(existing_content, list):
                content_blocks = [doc_block] + existing_content
            else:
                content_blocks = [doc_block, {"type": "text", "text": existing_content}]
            augmented.append({"role": "user", "content": content_blocks})
        else:
            augmented.append(msg)

    kwargs = {
        "model": ANTHROPIC_MODEL,
        "max_tokens": max_tokens,
        "messages": augmented,
        "betas": ["files-api-2025-04-14"],
    }
    if effort is not None:
        kwargs["thinking"] = {"type": "adaptive"}
        kwargs["output_config"] = {"effort": effort}
    if system:
        # 2026-07-23追記: 添付ファイル(doc_block)側は既にcache_control済みだったが、
        # systemプロンプト自体はキャッシュ対象外だったため、_call_anthropicと
        # 同じ_cacheable_system()を適用する。
        kwargs["system"] = _cacheable_system(system)

    # ストリーミング経由で呼び出す（_call_anthropic と同じ理由）
    with client.beta.messages.stream(**kwargs) as stream:
        response = stream.get_final_message()
    _log_usage("call_llm_with_file(Stage2+repomix)", response.usage)
    return _extract_text_from_content(response.content)
