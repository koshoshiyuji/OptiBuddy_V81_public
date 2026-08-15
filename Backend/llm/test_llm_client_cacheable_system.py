"""
test_llm_client_cacheable_system.py

2026-07-23追記の`_cacheable_system()`（Anthropic prompt cachingを
_call_anthropic/call_llm_with_file/_stage1_anthropicの3箇所にまとめて
適用するための共通ヘルパー）に対する回帰テスト。

背景: relax_interface.py・llm_interface.py・domain_generator.pyの分類/ヒアリング系など、
ほぼ全てのLLM呼び出しでsystemプロンプトにcache_controlが付いておらず、
Koshoshiの報告するキャッシュヒット率の低さ（約15%）の主因と見られたため追加した。
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from llm.llm_client import _cacheable_system


def test_string_system_prompt_gets_wrapped_with_cache_control():
    result = _cacheable_system("あなたはOptiBuddyの開発アシスタントです。")
    assert result == [
        {
            "type": "text",
            "text": "あなたはOptiBuddyの開発アシスタントです。",
            "cache_control": {"type": "ephemeral"},
        }
    ]


def test_already_block_list_passes_through_unchanged():
    already_blocks = [{"type": "text", "text": "foo", "cache_control": {"type": "ephemeral"}}]
    result = _cacheable_system(already_blocks)
    assert result is already_blocks


if __name__ == "__main__":
    test_string_system_prompt_gets_wrapped_with_cache_control()
    test_already_block_list_passes_through_unchanged()
    print("全テスト成功")
