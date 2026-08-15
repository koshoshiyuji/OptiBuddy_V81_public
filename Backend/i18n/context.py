"""
Backend/i18n/context.py — 現在の表示言語をcontextvarsで保持する

【設計方針】(DESIGN_2026-07-29_nurse_shift_i18n_implementation_spec.md 3-1節)
- Frontendは現在の表示言語をAPIリクエストのクエリパラメータ `?lang=en|ja` で送る。
- Backendはリクエスト先頭（app.pyのbefore_requestフック）でset_lang()を呼び、
  以降のリクエスト処理中は関数引数にlangを追加で持ち回らずget_lang()で参照する。
- contextvarsはリクエスト（スレッド/非同期タスク）ごとに独立した値を持つため、
  Flaskの複数ワーカー・並行リクエスト間で値が混ざらない。
"""

from __future__ import annotations

import contextvars

_SUPPORTED_LANGS = ("ja", "en")

_lang_var: contextvars.ContextVar[str] = contextvars.ContextVar("optibuddy_lang", default="ja")


def set_lang(lang: str | None) -> None:
    """現在リクエストの表示言語をセットする。未知の値は"ja"にフォールバック。"""
    _lang_var.set(lang if lang in _SUPPORTED_LANGS else "ja")


def get_lang() -> str:
    """現在リクエストの表示言語を取得する（デフォルト"ja"）。"""
    return _lang_var.get()
