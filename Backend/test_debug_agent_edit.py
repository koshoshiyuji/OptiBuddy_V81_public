"""
Backend/test_debug_agent_edit.py

2026-08-09新設: debug_agent.py の edit_file ツール本体 `_apply_edit()` の単体テスト。

背景: debug_agent.run_debug_agent() 自体はAnthropic APIを直接呼ぶため、実LLM無しでは
統合テストしづらい。既存の test_post_registration_fix.py もrun_debug_agent自体は
monkeypatchで丸ごと差し替えている。今回追加したedit_fileの本体ロジック(_apply_edit)は
純粋なファイル操作関数として切り出してあるため、実LLM無しで直接テストできる。

対策の背景（実データ）: Backend/logs/backend.log の実際のdebug_agentセッション
（medical_appointment_scheduler、3428〜4192行目）で、write_file（全文置換）が
「新内容0文字/既存28289文字、truncated=True」で2回連続拒否される事故が観測された。
solver.py全体を毎回書き直す設計では、ファイルが大きいほど出力トークン上限に達しやすい。
edit_file（old_string/new_string方式の部分編集）はこの出力サイズ問題を構造的に回避する。
"""
import tempfile
from pathlib import Path

import pytest

from debug_agent import _apply_edit


@pytest.fixture
def tmp_file():
    with tempfile.TemporaryDirectory() as d:
        p = Path(d) / "sample.py"
        p.write_text(
            "def foo():\n"
            "    x = 1\n"
            "    return x\n"
            "\n"
            "def bar():\n"
            "    x = 1\n"
            "    return x * 2\n",
            encoding="utf-8",
        )
        yield p


def test_apply_edit_unique_match_succeeds(tmp_file):
    ok, msg = _apply_edit(tmp_file, "def foo():\n    x = 1\n    return x", "def foo():\n    x = 2\n    return x")
    assert ok
    content = tmp_file.read_text(encoding="utf-8")
    assert "def foo():\n    x = 2\n    return x" in content
    # bar()側は変更されていないこと
    assert "def bar():\n    x = 1\n    return x * 2" in content


def test_apply_edit_zero_match_fails_without_writing(tmp_file):
    original = tmp_file.read_text(encoding="utf-8")
    ok, msg = _apply_edit(tmp_file, "this string does not exist anywhere", "replacement")
    assert not ok
    assert "見つかりませんでした" in msg
    # ファイルが変更されていないこと
    assert tmp_file.read_text(encoding="utf-8") == original


def test_apply_edit_ambiguous_match_fails_without_replace_all(tmp_file):
    original = tmp_file.read_text(encoding="utf-8")
    # "x = 1" はfoo()/bar()両方に出現する（曖昧）
    ok, msg = _apply_edit(tmp_file, "    x = 1\n", "    x = 99\n")
    assert not ok
    assert "2箇所" in msg
    assert tmp_file.read_text(encoding="utf-8") == original


def test_apply_edit_replace_all_replaces_every_occurrence(tmp_file):
    ok, msg = _apply_edit(tmp_file, "    x = 1\n", "    x = 99\n", replace_all=True)
    assert ok
    content = tmp_file.read_text(encoding="utf-8")
    assert content.count("x = 99") == 2
    assert "x = 1" not in content


def test_apply_edit_missing_file_reports_read_failure():
    ok, msg = _apply_edit(Path("/nonexistent/path/does_not_exist.py"), "a", "b")
    assert not ok
    assert "読み込み失敗" in msg
