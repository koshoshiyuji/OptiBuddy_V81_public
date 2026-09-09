
import ast
import copy
import difflib
import json
import logging
import py_compile
import re
import shutil
import subprocess
import sys
import time
from pathlib import Path

from . import (
    logger,
)
from .extensions import (
    _find_function_block,
    _parse_function_output,
    _reindent_code,
)
from .stage2 import (
    _parse_marker_output,
)
from .static_checks import (
    check_converter_solver_field_consistency,
)
from .utils import (
    compute_diff,
)




# ─────────────────────────────────────────────────────────────
# Gate2自己修復ループ: converter⇔solver キー不一致の自動修正（最大2回）
#
# 背景: _CONVERTER_SOLVER_CONTRACT をStage2プロンプトに埋め込んでも、
# 一発生成だけではキー不一致が解消しきらないことを複数ドメイン登録
# （StoreSite）で確認した（契約追加前16キー不一致 → 追加後も10キー不一致）。
# Stage2生成直後・人間確認画面より前に静的field-checkを実行し、不一致が
# 見つかった場合のみ、具体的な差分（どのキーが足りない／使われていないか）を
# LLMにフィードバックして修正させる。
#
# V2（本ブロック）: 1回の修正では解消しきらないケースが実際にあったため
# （StoreSite: 7→7件で1回では未解消）、最大試行回数を2回に増やした。
# 各回、直前の結果に対して再度field-checkし、まだ不一致があれば新しい
# 差分を渡してもう一度修正させる。無限リトライはしない（max_attemptsで
# 必ず打ち切り、それでも直らなければ従来通りfield-check警告として
# 人間確認画面に出す）。また、各回の出力は「元より悪化していないか」
# （不一致の総数が増えていないか）を確認してから採用する回帰ガードを設け、
# LLMの修正が逆効果だった場合に元の状態より悪い内容を書き込まないようにする。
# 恒久的な抽象化（factoryパターン等でキー生成を一元化する等）は複雑さが
# 増すだけで根本解決にならないため、ここでは導入しない。
# ─────────────────────────────────────────────────────────────

_FIELD_REPAIR_SYSTEM = """\
あなたはPythonコードの自動修正ツールです。
converter.py が出力する辞書のキーと solver.py が参照する辞書のキーが
一致していない箇所が機械的に検出されました。以下の2ファイルを、
指摘されたキー不一致がすべて解消されるように修正し、
===FILE:path===...===END=== マーカー形式で2ファイルとも全文出力してください
（差分ではなく、修正後の完全なファイル内容を出力すること）。

修正方針:
- missing_in_converter に列挙されたキーは、solverが実際に参照しているのに
  converterが一度も出力していないキーです。converter側にそのキーを追加して
  出力するか、solver側の参照名が誤っている場合はsolver側を正しいキー名に
  修正してください（converter/solverのどちらのキー名が意図されたものかを
  コードの文脈・変数名・コメントから判断すること）。
- unused_in_solver に列挙されたキーは、converterが出力しているのに
  solverが一度も参照していないキーです。solver側でそのキーを実際に
  使うよう実装を追加するか、不要であればconverter側から削除してください。
- 上記以外の既存ロジック（制約・目的関数・アルゴリズム）は変更しないこと。
"""


def _field_check_total(check: dict) -> int:
    """不一致の総数（missing_in_converter + unused_in_solver + errors）。回帰ガード用。"""
    return len(check["missing_in_converter"]) + len(check["unused_in_solver"]) + len(check["errors"])


def _is_field_check_clean(check: dict) -> bool:
    return not check["missing_in_converter"] and not check["unused_in_solver"] and not check["errors"]


def _call_field_repair_llm(converter_path: str, converter_code: str,
                            solver_path: str, solver_code: str,
                            snake: str, check: dict) -> tuple[str | None, str | None]:
    """1回分の修正LLM呼び出し。(new_converter, new_solver) を返す（失敗時は (None, None)）。"""
    from llm.llm_client import call_llm

    prompt = f"""## 対象ドメイン
snake_case: {snake}

## 検出されたキー不一致
- solverが参照するがconverterが一度も出力しないキー
  （missing_in_converter）: {check['missing_in_converter']}
- converterが出力しているがsolverが一度も参照しないキー
  （unused_in_solver）: {check['unused_in_solver']}

## 現在の {converter_path}
```python
{converter_code}
```

## 現在の {solver_path}
```python
{solver_code}
```

===FILE:{converter_path}===
（修正後の全文をここに出力）
===END===

===FILE:{solver_path}===
（修正後の全文をここに出力）
===END===
"""
    try:
        raw = call_llm(
            [{"role": "system", "content": _FIELD_REPAIR_SYSTEM},
             {"role": "user", "content": prompt}],
            max_tokens=8000,
            temperature=0,
        )
        parsed = _parse_marker_output(raw)
    except Exception as e:
        logger.warning(f"[Gate2 self-repair] LLM修正呼び出しに失敗: {e}")
        return None, None

    fixed_by_path = {f["path"]: f["content"] for f in parsed.get("files", [])}
    new_converter = fixed_by_path.get(converter_path)
    new_solver    = fixed_by_path.get(solver_path)
    if not new_converter or not new_solver:
        logger.warning(
            f"[Gate2 self-repair] 修正応答から {converter_path} / {solver_path} を抽出できませんでした。"
        )
        return None, None
    return new_converter, new_solver


# ─────────────────────────────────────────────────────────────
# Gate2自己修復ループ V2（実験、2026-08-05追加、まだ本番経路には未接続）
#
# 背景: registration_time_reduction_plan.md タスク7。段階Bの単体計測で、
# 上記_call_field_repair_llm()（ファイル全文書き換え型）が1回104.9秒かかり、
# 2回目Gate2再検証全体（実測189秒）の約55%を占めることが判明した
# （ENGINEERING_LOG.md 2026-08-05追記3参照）。実際のキー不一致は多くの場合
# converter/solverそれぞれ1〜2個の関数内に限られるにもかかわらず、
# 毎回2ファイルの全文（本件では出力7,553トークン）をLLMに書き直させて
# いたことが、生成トークン数＝時間の直接的な原因だった。
#
# 改善案: 「新規コードではなく既存箇所」であるgenerate_and_apply_extensions()
# (V2、5-1節付近)が既に使っている「関数単位パッチ」形式
# （===REPLACE_FUNCTION:path:関数名===/===ADD_FUNCTION:path===、
# _parse_function_output()・_find_function_block()・_reindent_code()）を
# そのまま転用し、self-repairにも「変更が必要な関数だけを返させる」方式を
# 適用する。パース・関数境界検出・インデント補正のロジックは実運用で
# 検証済みのものをそのまま再利用し、新規に書き起こさない（バグ混入リスクを
# 抑える）。extension applyはディスク上のファイルに対して動作するが、
# self-repairはdiffs（メモリ上の文字列）に対して動作するため、
# 適用部分だけメモリ上で完結する_apply_function_patches_in_memory()を新設した。
#
# 位置づけ: まだattempt_field_consistency_repair()からは呼ばれていない。
# tools/stage_b_selfrepair_v2_timing.py で単体計測（段階B）を行い、
# v1（104.9秒/7,553トークン）との比較結果を見てから本番経路に接続するか判断する。
# ─────────────────────────────────────────────────────────────

_FIELD_REPAIR_SYSTEM_V2 = """\
あなたはPythonコードの自動修正ツールです。
converter.py が出力する辞書のキーと solver.py が参照する辞書のキーが
一致していない箇所が機械的に検出されました。以下の2ファイルのうち、
指摘されたキー不一致の解消に**実際に必要な関数だけ**を修正してください。

修正方針:
- missing_in_converter に列挙されたキーは、solverが実際に参照しているのに
  converterが一度も出力していないキーです。converter側にそのキーを追加して
  出力するか、solver側の参照名が誤っている場合はsolver側を正しいキー名に
  修正してください（converter/solverのどちらのキー名が意図されたものかを
  コードの文脈・変数名・コメントから判断すること）。
- unused_in_solver に列挙されたキーは、converterが出力しているのに
  solverが一度も参照していないキーです。solver側でそのキーを実際に
  使うよう実装を追加するか、不要であればconverter側から削除してください。
- 上記以外の既存ロジック（制約・目的関数・アルゴリズム）は変更しないこと。
- **ファイル全文を出力してはいけない**。修正が必要な関数（メソッドを含む）
  単位でのみ、以下の形式で出力すること。

出力形式:
  既存関数を丸ごと入れ替える場合（最も一般的なケース）:
  ===REPLACE_FUNCTION:ファイルパス:関数名===
  <関数の完全な定義（def行から関数末尾まで、クラスメソッドの場合は
   元と同じインデント階層で記述）>
  ===END===

  新規関数を追加する場合（既存関数の入れ替えでは対応できない場合のみ）:
  ===ADD_FUNCTION:ファイルパス===
  <新規関数の完全な定義>
  ===END===

不一致の解消に必要な関数の数だけ、上記ブロックを繰り返し出力してよい
（1関数のみで解消するなら1ブロックのみでよい）。上記2パターン以外の
出力形式（ファイル全文、差分diff形式等）は禁止。前置き・説明も不要。
"""


def _apply_function_patches_in_memory(content: str, replacements: list[dict], additions: list[dict]) -> str:
    """
    apply_function_replacements()のディスクI/O版とは異なり、メモリ上の
    ファイル内容文字列に対して関数単位パッチを適用する（self-repair用、
    diffsがまだディスクに書かれていない前提のため）。

    apply_function_replacements()と異なり、対象関数が見つからない場合は
    黙って末尾追記にフォールバックせず例外を送出する（self-repairは正確性が
    最優先であり、「置換のつもりが末尾に孤立コードが増える」事故を避けるため。
    呼び出し側でこの例外を捕まえ、修正失敗として扱うこと）。
    """
    for r in replacements:
        block = _find_function_block(content, r["function_name"])
        if block is None:
            raise ValueError(f"対象関数が見つかりません: {r['function_name']}")
        b_start, b_end, indent = block
        new_code = _reindent_code(r["code"], indent)
        content = content[:b_start] + new_code.rstrip() + "\n\n" + content[b_end:]
    for a in additions:
        content = content.rstrip() + "\n\n\n" + a["code"].rstrip() + "\n"
    return content


def _call_field_repair_llm_v2(converter_path: str, converter_code: str,
                               solver_path: str, solver_code: str,
                               snake: str, check: dict) -> tuple[str | None, str | None]:
    """_call_field_repair_llm()の関数単位パッチ版。(new_converter, new_solver)を
    返す（失敗時は(None, None)、_call_field_repair_llm()と同じ契約）。"""
    from llm.llm_client import call_llm

    prompt = f"""## 対象ドメイン
snake_case: {snake}

## 検出されたキー不一致
- solverが参照するがconverterが一度も出力しないキー
  （missing_in_converter）: {check['missing_in_converter']}
- converterが出力しているがsolverが一度も参照しないキー
  （unused_in_solver）: {check['unused_in_solver']}

## 現在の {converter_path}
```python
{converter_code}
```

## 現在の {solver_path}
```python
{solver_code}
```
"""
    try:
        # v1のmax_tokens=8000は「2ファイル全文」を見込んだ値。関数単位パッチは
        # 出力がずっと小さくなる想定のため、上限も4000に下げてある
        # （万一長引いても打ち切られるだけで、成功時の速度には影響しない）。
        raw = call_llm(
            [{"role": "system", "content": _FIELD_REPAIR_SYSTEM_V2},
             {"role": "user", "content": prompt}],
            max_tokens=4000,
            temperature=0,
        )
        parsed = _parse_function_output(raw)
    except Exception as e:
        logger.warning(f"[Gate2 self-repair v2] LLM修正呼び出しに失敗: {e}")
        return None, None

    if not parsed["replacements"] and not parsed["additions"]:
        logger.warning("[Gate2 self-repair v2] 修正応答から関数パッチを抽出できませんでした。")
        return None, None

    try:
        new_converter = _apply_function_patches_in_memory(
            converter_code,
            [r for r in parsed["replacements"] if r["path"] == converter_path],
            [a for a in parsed["additions"] if a["path"] == converter_path],
        )
        new_solver = _apply_function_patches_in_memory(
            solver_code,
            [r for r in parsed["replacements"] if r["path"] == solver_path],
            [a for a in parsed["additions"] if a["path"] == solver_path],
        )
    except ValueError as e:
        logger.warning(f"[Gate2 self-repair v2] 関数パッチ適用失敗: {e}")
        return None, None

    return new_converter, new_solver


def attempt_field_consistency_repair(diffs: list, snake: str, max_attempts: int = 2) -> dict:
    """
    diffs（generate_domain_filesの戻り値）内の {snake}_converter.py /
    {snake}_solver.py ペアに静的field-check（check_converter_solver_field_consistency）
    を実行し、不一致があれば最大 max_attempts 回までLLMに修正させて diffs を書き換える。

    各回の出力は、直前の状態より不一致の総数が増えていないか（回帰ガード）を
    確認してから「これまでで最良の状態」として採用する。クリーンになった時点で
    打ち切る。max_attempts回試しても完全には解消しない場合、最良の状態
    （元より改善していれば改善後、そうでなければ元のまま）を diffs に書き戻す。

    返り値:
      {"attempted": bool, "healed": bool, "attempts": int,
       "before": dict|None, "after": dict|None}
        attempted: 修正を試みたか（converter/solverが両方diffsに揃っていない、
                   または元から不一致がない場合はFalse）
        healed:    最終的にfield-checkがクリーンになったか
        attempts:  実際にLLM修正呼び出しを行った回数
        before:    修正前の check_converter_solver_field_consistency 結果
        after:     採用した最終状態の同結果（attempted=Falseの場合はNone）
    """
    solver_path    = f"Backend/solvers/{snake}_solver.py"
    converter_path = f"Backend/dsl_transformer/{snake}_converter.py"

    solver_idx    = next((i for i, d in enumerate(diffs) if d.get("path") == solver_path), None)
    converter_idx = next((i for i, d in enumerate(diffs) if d.get("path") == converter_path), None)

    result = {"attempted": False, "healed": False, "attempts": 0, "before": None, "after": None}
    if solver_idx is None or converter_idx is None:
        return result

    best_converter = diffs[converter_idx].get("new_content", "")
    best_solver    = diffs[solver_idx].get("new_content", "")

    before = check_converter_solver_field_consistency(best_converter, best_solver)
    result["before"] = before
    if _is_field_check_clean(before):
        return result  # 元から一致している。修正不要。

    result["attempted"] = True
    best_check = before

    for attempt_num in range(1, max_attempts + 1):
        result["attempts"] = attempt_num
        # 2026-08-05変更: v2（関数単位パッチ、===REPLACE_FUNCTION===/===ADD_FUNCTION===）
        # をまず試す。段階Bの単体計測でv1（ファイル全文書き換え）比 約90%の時間短縮
        # （104.9秒→9.9秒、修正成功）を確認済み（ENGINEERING_LOG.md 2026-08-05追記4・5
        # 参照）。v2が関数を特定できない等で失敗した場合のみ、安全側としてv1（全文
        # 書き換え）にフォールバックする（複数関数にまたがる複雑な不一致等、v2が
        # まだ実地で検証できていないケースへの保険）。
        new_converter, new_solver = _call_field_repair_llm_v2(
            converter_path, best_converter, solver_path, best_solver, snake, best_check
        )
        if new_converter is None:
            logger.info(
                f"[Gate2 self-repair] {snake}: v2（関数単位パッチ）が失敗したため、"
                f"v1（ファイル全文書き換え）にフォールバックします（{attempt_num}回目）。"
            )
            new_converter, new_solver = _call_field_repair_llm(
                converter_path, best_converter, solver_path, best_solver, snake, best_check
            )
        if new_converter is None:
            break  # LLM呼び出し失敗（v1も失敗）。ここまでの最良状態で打ち切る。

        new_check = check_converter_solver_field_consistency(new_converter, new_solver)

        # 回帰ガード: 不一致の総数が悪化していなければ、この結果を新しい最良状態として採用する。
        if _field_check_total(new_check) <= _field_check_total(best_check):
            best_converter, best_solver, best_check = new_converter, new_solver, new_check
        else:
            logger.info(
                f"[Gate2 self-repair] {snake}: 修正{attempt_num}回目が悪化したため採用しません "
                f"（悪化前={_field_check_total(best_check)}件 → 悪化後={_field_check_total(new_check)}件）。"
            )

        if _is_field_check_clean(best_check):
            break

    result["after"] = best_check

    if _field_check_total(best_check) < _field_check_total(before):
        # 完全に直っていなくても、元より改善していれば診断結果として採用する
        diffs[converter_idx] = compute_diff(converter_path, best_converter)
        diffs[solver_idx]    = compute_diff(solver_path, best_solver)

    if _is_field_check_clean(best_check):
        result["healed"] = True
        logger.info(
            f"[Gate2 self-repair] {snake}: converter/solverのキー不一致を"
            f"{result['attempts']}回の修正で自動修正しました。"
        )
    else:
        logger.info(
            f"[Gate2 self-repair] {snake}: {max_attempts}回試行しても解消しませんでした "
            f"（修正前={_field_check_total(before)}件 → 最良={_field_check_total(best_check)}件）: {best_check}"
        )

    return result
