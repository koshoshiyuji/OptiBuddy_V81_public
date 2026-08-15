"""
Backend/i18n/__init__.py — 動的メッセージ日英対応・共通コア
==========================================================

【背景】
DESIGN_2026-07-29_ui_i18n_status_and_plan.md / _dynamic_message_i18n_key_design.md /
_nurse_shift_i18n_implementation_spec.md の3設計ドキュメントに基づく実装（初回対象:
NurseShiftWeeklyCapドメインのみ、Studio画面でエンドユーザーが実際に目にする動的
テキストに限定。ドメイン登録パイプライン `/api/domain/*` 系は対象外）。

【方式】
Koshoshiさん提案の「backend側で辞書引き＋テンプレート復元」方式。
- キーは生の日本語文字列ではなく、既存の安定識別子（rule_id / TableColumn.key /
  KPIカードのid / section_id）をベースにする。
- 言語の受け渡しは contextvars（i18n/context.py の set_lang/get_lang）で行い、
  リクエスト先頭（app.pyのbefore_requestフック）でセットする。関数呼び出しの
  引数にlangをバケツリレー的に追加することはしない。
- backendは常に解決済み文字列（title/message/label等）を返す。Frontend側の
  en.json/ja.jsonとは別建てで、Python側辞書はこのモジュール配下で完結させる
  （唯一の例外: night_shift/is_night列のbool値→絵文字表示はFrontend側の
  表示ロジックが必要なため、Frontend側にも専用キーを追加している。
  DESIGN_2026-07-29_nurse_shift_i18n_implementation_spec.md 1-2節参照）。
- 未登録キー・フォーマットパラメータ不足時は日本語原文にフォールバックし、
  ログに警告を残す（既存動作を壊さないための後方互換方針）。

【使い方】
  # Backend/i18n/{domain}_messages.py
  from i18n import make_translator
  _JA = {"issue.foo.title": "..."}
  _EN = {"issue.foo.title": "..."}
  t = make_translator(_JA, _EN, "nurse_shift_weekly_cap")

  # 呼び出し側
  from i18n.nurse_shift_weekly_cap_messages import t
  title = t("issue.foo.title", tid=ctx["tid"])
"""

from __future__ import annotations

import logging
from typing import Callable, Dict

from i18n.context import get_lang

logger = logging.getLogger(__name__)


def make_translator(ja: Dict[str, str], en: Dict[str, str], module_name: str) -> Callable[..., str]:
    """
    ja/en 2つの辞書から t(key, **params) -> str な関数を作る。

    Args:
        ja:          日本語辞書 {key: テンプレート文字列}（フォールバック先も兼ねる）
        en:          英語辞書 {key: テンプレート文字列}
        module_name: ログ出力用の識別名（例: "nurse_shift_weekly_cap", "common"）

    Returns:
        t(key, **params) -> str
        - 現在言語（i18n.context.get_lang()）の辞書からkeyを引き、
          str.format(**params) でパラメータ復元して返す。
        - keyが現在言語の辞書に無ければ日本語辞書にフォールバック
          （それも無ければkey自体を返す）。フォールバック発生時はwarningログ。
        - フォーマット失敗時（paramsミスマッチ）も未展開のテンプレートを返し、
          warningログを残す（例外を上げてリクエスト全体を壊さない）。
    """

    def t(key: str, **params) -> str:
        lang = get_lang()
        table = en if lang == "en" else ja
        template = table.get(key)
        if template is None:
            if lang != "ja":
                logger.warning(
                    f"[i18n:{module_name}] key='{key}' が lang='{lang}' 辞書に見つかりません。"
                    f"日本語にフォールバックします。"
                )
            template = ja.get(key)
        if template is None:
            logger.warning(f"[i18n:{module_name}] key='{key}' がja辞書にも見つかりません。キー自体を返します。")
            return key
        if not params:
            return template
        try:
            return template.format(**params)
        except (KeyError, IndexError) as e:
            logger.warning(
                f"[i18n:{module_name}] key='{key}' のフォーマットパラメータ不足/不一致: {e}。"
                f"未展開のテンプレートを返します。"
            )
            return template

    return t
