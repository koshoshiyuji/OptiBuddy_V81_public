
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
    DEFAULT_ATTACHMENTS,
    _DOMAIN_SOURCE_FILES,
    logger,
)
from .apply import (
    copy_domain_for_extension,
)
from .stage2 import (
    _parse_marker_output,
)
from .static_checks import (
    check_converter_solver_field_consistency,
)
from .utils import (
    _resolve_path,
)




_STAGE2LITE_V2_SYSTEM = """\
あなたはOptiBuddyの新規ドメイン実装を拡張する専門家です。
与えられるファイルは、既存ドメインからこの新ドメイン専用にコピーされたものであり、
自由に編集してよい。

重要な制限（必ず守ること）:
- 出力は必ず「関数で1つの完全な定義（def行から関数末尾まで）」単位で行う。
  1つの関数の一部分だけを断片的に出力してはならない。
- 既存の関数名・シリーズ（引数）は変更しない。関数の中身だけを拡張する。
新しい判定が必要な場合は既存関数の中に処理を追加するか、新しいトフレベル関数を
1つ丸ごと追加してよい（この場合も完全な関数単位で出力する）。
ファイル内に定義されていないフィールドをDSLに参照させる場合は
.get("フィールド名", デフォルト値) で安全に取得する。

表示系の拡張（「〜ごとの〜を画面に表示してほしい」）を実装する場合:
- 添付した table_sections.py の build_table_section() は extra_metrics 引数を
  受け取る。これは {row_id_keyの値: {列ラベル: 値}} という完全に汎用的な
  差し込み口で、「行単位で新しい派生指標を1列足したい」という要求はほぼ
  すべてこれで表現できる。
- この場合、ui_converter.py側でTableColumnを新規に書いてはならない。
  代わりに: (1) solver.py側で該当する値を計算し、solution辞書のトップレベルに
  {エンティティID: 値} という単純なdict（例: solution["staff_rolling_night_counts"]）
  として含める、(2) ui_converter.py側でそのdictを
  {エンティティID: {"列ラベル": 値}} の形に詰め替えて、該当する
  build_table_section() 呼び出しに extra_metrics=... として渡す、の2ステップに
  分割して実装すること。列の追加・整形はbuild_table_section側が自動で行う。
- 上記に当てはまらない表示要求（新しいテーブルセクション自体を追加する、
  KPIカードを追加する等）は従来通り手書きで構わない。extra_metricsは
  「既存の行単位テーブルに1列足す」ケース専用。

「直近N日間で〜の回数はM回まで」のようなローリング/スライディングウィンドウ型の
上限制約を実装する場合:
- 添付した solvers/base/rolling_window.py の add_rolling_window_cap_constraints()
  を呼び出すこと。ウィンドウの境界処理（対象期間がウィンドウ長より短い場合の
  扱いを含む）は自分で実装しない。過去に、この境界処理をsolver.py側で都度
  書かせた結果、対象期間がウィンドウ長より短いケースで制約が丸ごと無効化される
  不具合が発生している。
- 使い方: 対象イベントの候補を日番号ごとにまとめ、
  presence_by_day = {day: [mdl.presence_of(itv) for ...], ...} を作り、
  add_rolling_window_cap_constraints(mdl, presence_by_day, window_days=N, cap=M,
  all_days=対象期間の全日番号リスト) を呼ぶ。

出力形式:
  既存同名関数を丸ごと入れ替える場合:
  ===REPLACE_FUNCTION:ファイルパス:関数名===
  <関数の完全な定義>
  ===END===

  新規関数を追加する場合:
  ===ADD_FUNCTION:ファイルパス===
  <新規関数の完全な定義>
  ===END===

上記2パターンのみで出力し、前置き・説明不要。
"""


def _parse_function_output(raw: str) -> dict:
    replacements, additions = [], []
    for m in re.finditer(r'===REPLACE_FUNCTION:(.+?):(.+?)===\n(.*?)===END===', raw, re.DOTALL):
        code = m.group(3)
        if code.endswith('\n'): code = code[:-1]
        replacements.append({"path": m.group(1).strip(), "function_name": m.group(2).strip(), "code": code})
    for m in re.finditer(r'===ADD_FUNCTION:(.+?)===\n(.*?)===END===', raw, re.DOTALL):
        code = m.group(2)
        if code.endswith('\n'): code = code[:-1]
        additions.append({"path": m.group(1).strip(), "code": code})
    logger.info(f"[extension_apply_v2] 関数単位出力パース: replacements={len(replacements)}, additions={len(additions)}")
    return {"replacements": replacements, "additions": additions}


def _find_function_block(content: str, func_name: str):
    marker = f"def {func_name}("
    start = content.find(marker)
    if start == -1:
        return None

    line_start = content.rfind("\n", 0, start) + 1
    indent = content[line_start:start]  # def 直前の空白（クラスメソッドなら "    "）

    block_start = content.rfind("\n\n", 0, start)
    if block_start == -1:
        block_start = content.rfind("\n", 0, start)
        block_start = 0 if block_start == -1 else block_start + 1
    else:
        block_start += 1

    search_from  = start + len(marker)
    next_def     = content.find("\ndef ", search_from)
    next_method  = content.find("\n    def ", search_from)
    next_section = content.find("\n# ---", search_from)
    candidates = [c for c in (next_def, next_method, next_section) if c != -1]
    block_end = min(candidates) if candidates else len(content)
    return block_start, block_end, indent


def _reindent_code(code: str, indent: str) -> str:
    """
    LLMが返したコードのインデントを、置換対象の元の関数と同じ階層（indent引数）に
    強制的に合わせ直す。実際にTruckDispatcher拡張適用時、LLMがクラスメソッドを
    先頭列（非インデント）で返してしまい、対象クラスの構造が壊れる不具合が
    実際に発生した。この関数はそれを構造的に防ぐ。
    """
    lines = code.split("\n")
    non_empty = [l for l in lines if l.strip()]
    if not non_empty:
        return code
    min_indent = min(len(l) - len(l.lstrip(" ")) for l in non_empty)
    dedented = [l[min_indent:] if l.strip() else l for l in lines]
    return "\n".join((indent + l) if l.strip() else l for l in dedented)


def apply_function_replacements(replacements: list, additions: list) -> list:
    written = []
    cache: dict[Path, str] = {}

    for r in replacements:
        path = _resolve_path(r["path"])
        if path not in cache:
            if not path.exists():
                logger.warning(f"[extension_apply_v2] ファイルが存在しません: {path}")
                continue
            cache[path] = path.read_text(encoding="utf-8")
        content = cache[path]
        block = _find_function_block(content, r["function_name"])
        if block:
            b_start, b_end, indent = block
            new_code = _reindent_code(r["code"], indent)
            cache[path] = content[:b_start] + new_code.rstrip() + "\n\n" + content[b_end:]
            written.append(f"{r['path']} :: {r['function_name']} (関数置換、indent={len(indent)}スペースに合わせ直し)")
        else:
            cache[path] = content.rstrip() + "\n\n\n" + r["code"].rstrip() + "\n"
            written.append(f"{r['path']} :: {r['function_name']} (末尾に追加)")

    for a in additions:
        path = _resolve_path(a["path"])
        if path not in cache:
            if not path.exists():
                logger.warning(f"[extension_apply_v2] ファイルが存在しません: {path}")
                continue
            cache[path] = path.read_text(encoding="utf-8")
        cache[path] = cache[path].rstrip() + "\n\n\n" + a["code"].rstrip() + "\n"
        written.append(f"{a['path']} (新規関数追加)")

    for path, content in cache.items():
        path.write_text(content, encoding="utf-8")

    return written


def generate_and_apply_extensions(
    domain_name: str, base_domain: str, extension_gaps: list[dict], hearing_texts: list,
) -> dict:
    """
    パターン3の実行本体（V2：コピー+関数単位完全置換方式）。
    """
    from llm.llm_client import call_llm, default_model

    copy_result = copy_domain_for_extension(base_domain, domain_name)
    new_snake   = copy_result["new_snake"]
    new_pascal  = copy_result["new_pascal"]

    source_sections = []
    for role, rel_path in copy_result["copied"].items():
        try:
            code = _resolve_path(rel_path).read_text(encoding="utf-8")
            source_sections.append(f"### {role}: {rel_path}\n```python\n{code}\n```")
        except Exception as e:
            logger.warning(f"[extension_apply_v2] コピー先読み込み失敗: {rel_path} — {e}")

    # table_sections.py は共通コア（層A）で、この拡張のために編集する対象ではない。
    # ただし build_table_section() の extra_metrics 引数（V8.7で追加）をLLMに
    # 知らせないと、表示系の拡張要求のたびにui_converter側へ手書きの列追加を
    # 試みて失敗する（実際にNurseShiftWeeklyCap登録で発生）。編集対象外であることが
    # 伝わるよう「参考」として明示的に分けて渡す。
    table_sections_path = DEFAULT_ATTACHMENTS.get("table_sections.py", "")
    table_sections_ref = ""
    if table_sections_path:
        try:
            ts_code = Path(table_sections_path).read_text(encoding="utf-8")
            table_sections_ref = (
                f"### 参考（編集対象外・共通コア）: dsl_transformer/table_sections.py\n"
                f"```python\n{ts_code}\n```"
            )
        except Exception as e:
            logger.warning(f"[extension_apply_v2] table_sections.py読み込み失敗: {e}")

    # rolling_window.py も table_sections.py と同様に共通コア（層A）。
    # ローリングウィンドウ型の上限制約をsolver側に都度手書きさせないよう、
    # 参考として明示的に渡す（経緯: docs/ENGINEERING_LOG.md 2026-07-12）。
    rolling_window_path = DEFAULT_ATTACHMENTS.get("rolling_window.py", "")
    rolling_window_ref = ""
    if rolling_window_path:
        try:
            rw_code = Path(rolling_window_path).read_text(encoding="utf-8")
            rolling_window_ref = (
                f"### 参考（編集対象外・共通コア）: solvers/base/rolling_window.py\n"
                f"```python\n{rw_code}\n```"
            )
        except Exception as e:
            logger.warning(f"[extension_apply_v2] rolling_window.py読み込み失敗: {e}")

    # 2026-07-18追加（docs/DESIGN_2026-07-18_layer_ab_pattern_library.md）:
    # レイヤーA/B共通モジュール3点。table_sections.py/rolling_window.pyと同様、
    # 拡張要求がこれらに該当する場合は自前実装させず必ず経由させる。
    layer_ab_refs = []
    for fname, label in [
        ("constraint_applier.py",   "資源の同時使用制約（no_overlap/cumulative、ヒアリング§9）"),
        ("quantity_requirement.py", "数量要件hard/soft（ヒアリング§4-1）"),
        ("solution_extraction.py",  "目的値/makespanの解抽出"),
    ]:
        fpath = DEFAULT_ATTACHMENTS.get(fname, "")
        if not fpath:
            continue
        try:
            code = Path(fpath).read_text(encoding="utf-8")
            layer_ab_refs.append(
                f"### 参考（編集対象外・共通コア、{label}）: solvers/base/{fname}\n"
                f"```python\n{code}\n```"
            )
        except Exception as e:
            logger.warning(f"[extension_apply_v2] {fname}読み込み失敗: {e}")
    layer_ab_ref = "\n\n".join(layer_ab_refs)

    hearing_combined = "\n\n---\n\n".join(hearing_texts)
    gaps_desc = "\n".join(
        f"- {g.get('name','')}: {g.get('description','')}（対象フィールド: {g.get('affected_fields', [])}）"
        for g in extension_gaps
    )
    user_prompt = f"""## 業務名（新ドメイン）
{new_pascal}（snake_case: {new_snake}）

## コピー元ドメイン
{base_domain}

## コピー先ファイル（編集対象）
{chr(10).join(source_sections)}

{table_sections_ref}

{rolling_window_ref}

{layer_ab_ref}

## ヒアリング内容
{hearing_combined}

## 実装すべき拡張機能
{gaps_desc}

各拡張機能を、関数単位の完全な置換（===REPLACE_FUNCTION===）または
新規関数追加（===ADD_FUNCTION===）として実装してください。
table_sections.py・solvers/base/rolling_window.py・solvers/base/constraint_applier.py・
solvers/base/quantity_requirement.py・solvers/base/solution_extraction.pyへの
===REPLACE_FUNCTION===/===ADD_FUNCTION===は出力しないこと
（いずれも編集対象外の参考ファイルのため）。資源の同時使用制約・数量要件hard/soft・
目的値/makespan抽出に該当する拡張要求は、自前実装せず必ずこれらの関数を呼び出すこと。
"""
    messages = [{"role": "system", "content": _STAGE2LITE_V2_SYSTEM}, {"role": "user", "content": user_prompt}]
    raw    = call_llm(messages, model=default_model(), max_tokens=8000)
    parsed = _parse_function_output(raw)
    written = apply_function_replacements(parsed["replacements"], parsed["additions"])

    # Gate2静的チェック（V8.7で追加）: 新規ドメインのフル生成（Stage2）には
    # 既にast.parse/check_converter_solver_field_consistencyが繋がっていたが、
    # この拡張適用パス（Stage2-lite V2）には一切繋がっておらず、構文エラーの
    # ままファイルが書き込まれてSolverRegistryのimportごと壊れる不具合が
    # 実際に発生した（2026-07-12、NurseShiftWeeklyCap登録）。書き込み直後の
    # ファイルに対して同じ静的チェックをここでも実行する。
    static_warnings: list[str] = []
    converter_rel    = copy_result["copied"].get("converter")
    ui_converter_rel = copy_result["copied"].get("ui_converter")
    solver_rel       = copy_result["copied"].get("solver")

    if converter_rel and solver_rel:
        try:
            converter_code = _resolve_path(converter_rel).read_text(encoding="utf-8")
            solver_code    = _resolve_path(solver_rel).read_text(encoding="utf-8")
            field_check = check_converter_solver_field_consistency(converter_code, solver_code)
            for err in field_check.get("errors", []):
                static_warnings.append(f"[Gate2静的チェック] {err}")
            missing = field_check.get("missing_in_converter", [])
            if missing:
                static_warnings.append(
                    f"[Gate2静的チェック] {solver_rel} が参照しているが {converter_rel} が出力しないキー: "
                    f"{missing}（実行時KeyErrorの可能性）"
                )
            unused = field_check.get("unused_in_solver", [])
            if unused:
                static_warnings.append(
                    f"[Gate2静的チェック] {converter_rel} が出力しているが {solver_rel} が一度も参照しないキー: "
                    f"{unused}（宣言されたが無視される制約の疑い）"
                )
        except Exception as e:
            logger.warning(f"[extension_apply_v2] field-consistency チェック失敗（無視して続行）: {e}")

    if ui_converter_rel:
        try:
            ui_code = _resolve_path(ui_converter_rel).read_text(encoding="utf-8")
            ast.parse(ui_code)
        except SyntaxError as e:
            static_warnings.append(f"[Gate2静的チェック] {ui_converter_rel} 構文エラー: {e.msg} (line {e.lineno})")
        except Exception as e:
            logger.warning(f"[extension_apply_v2] {ui_converter_rel} 構文チェック失敗（無視して続行）: {e}")

    if static_warnings:
        logger.warning(
            f"[extension_apply_v2] Gate2静的チェックで{len(static_warnings)}件の警告: {static_warnings}"
        )
    else:
        logger.info("[extension_apply_v2] Gate2静的チェック: 問題なし")

    registered_extensions = []
    try:
        from dsl_repository.repository import DslRepository
        repo = DslRepository()
        for gap in extension_gaps:
            gap_name = gap.get("name", "extension")
            try:
                existing_ext = repo.get_extension(gap_name)
                if existing_ext:
                    registered_extensions.append({"name": gap_name, "id": existing_ext["id"], "status": "reused_existing"})
                    logger.info(f"[extension_apply_v2] Extension名重複のため既存を流用: {gap_name} "
                                f"(元のcategory={existing_ext.get('category')}) → ID={existing_ext['id']}")
                    continue
                # 2026-07-27修正: 以前はcategoryにドメイン名(new_pascal)を入れていたが、
                # categoryは種類ラベル専用に統一した（Koshoshiとの会話で発覚した不整合の
                # 修正）。適用対象ドメインはapplicable_domainsで持つ。LLMのgap判定結果に
                # 種類（constraint/resource/ui等）の分類までは含まれないため、暫定的に
                # "constraint"を既定値とする（Stage1a.5のgap判定は主に制約・フィールド
                # 追加を検出する用途のため）。誤りがあれば後でDB側で手動修正すること。
                ext_id = repo.create_extension(
                    name=gap_name,
                    category="constraint",
                    applicable_domains=[new_pascal],
                    schema_fragment={"affected_fields": gap.get("affected_fields", []), "derived_from": base_domain},
                    solver_mapping={"description": gap.get("description", "")},
                    ui_mapping=None,
                    description=gap.get("rationale") or gap.get("description", ""),
                )
                registered_extensions.append({"name": gap_name, "id": ext_id, "status": "created"})
                logger.info(f"[extension_apply_v2] Extension登録: {gap_name} (applicable_domains=[{new_pascal}]) → ID={ext_id}")
            except Exception as e:
                registered_extensions.append({"name": gap_name, "status": "error", "error": str(e)})
                logger.error(f"[extension_apply_v2] Extension登録失敗: {gap_name} — {e}")
    except Exception as e:
        logger.error(f"[extension_apply_v2] Extension DB登録エラー: {e}")

    return {
        "status": "ok", "new_domain_name": new_pascal, "new_snake": new_snake,
        "copied_files": copy_result["copied"], "written": written,
        "registered_extensions": registered_extensions,
        "static_warnings": static_warnings,
    }

#
# 背景:
#   パターン1（既存流用）とパターン2（new_domain、全新規実装）の中間にあたる
#   パターン3。Stage1a.5（detect_extension_gaps）で検出され、人間が
#   needs_confirmationゲートで承認したextension_gapsを、既存ドメインの
#   converter/solver本体に孤立した追加関数として実装し、DBのExtension
#   エンティティ（DslRepository.create_extension）に正式登録する。
#
#   Stage2（generate_domain_files）との違い:
#     - 新しいproblem_classやファイル一式を一切作らない。既存ファイルへのPATCHのみ。
#     - 出力マーカーは_parse_marker_output()と同一形式を流用するが、
#       INSTRUCTION行に "ANCHOR: <既存テキスト>" を含めさせ、そのテキストの直前に
#       挿入する。ANCHORが見つからない場合は安全側に倒れてファイル末尾に追加する。
# ────────────────────────────────────────────────────────────

_STAGE2LITE_SYSTEM = """\
あなたはOptiBuddyの既存ソルバー実装を拡張するアシスタントです。

重要な制限:
- 既存のファイルを一から書き直さない。既存の関数・変数名を変えない。
- 各拡張は、新しいトプレベル関数（例: _build_xxx_constraint / _check_xxx）として追加し、
  既存の判定ロジック（例: _vehicle_can_serve, _generate_constraints の if 分岐）に
  1行の呼び出しを追加する形で組み込む。
- 存在しないフィールドを既存DSLに参照させる必要がある場合は、
  business_dsl/solver_inputの.get("フィールド名", デフォルト値) で安全に取得する。

出力形式:
  ===PATCH:既存ファイルのパス===
  INSTRUCTION: <拡張名> を <既存ファイル> に追加する | ANCHOR: <挿入先の直前にある既存テキストをそのまま一行引用>
  ===CODE===
  <追加するコード（新しい関数定義 + 既存判定関数への1行呼び出し追加の両方を含む）
  ===END===

上記パターンのみで出力し、前置き・説明不要。
"""


def generate_extension_files(
    domain_name: str, base_domain: str, extension_gaps: list[dict], hearing_texts: list,
) -> dict:
    """
    承認済みのextension_gapsを、base_domainの既存converter/solverへの
    追加PATCH（新規関数 + 呼び出し）として生成する（Stage2-lite）。
    新しいproblem_class・ファイル一式は一切生成しない。
    """
    from llm.llm_client import call_llm, default_model

    src = _DOMAIN_SOURCE_FILES.get(base_domain, {})
    source_sections = []
    for role, path in src.items():
        if not path:
            continue
        try:
            code = Path(path).read_text(encoding="utf-8")
            source_sections.append(f"### {role}: {path}\n```python\n{code}\n```")
        except Exception as e:
            logger.warning(f"[extension_apply] ソース読み込み失敗: {path} — {e}")

    hearing_combined = "\n\n---\n\n".join(hearing_texts)
    gaps_desc = "\n".join(
        f"- {g.get('name','')}: {g.get('description','')}"
        f"（対象フィールド: {g.get('affected_fields', [])}）"
        for g in extension_gaps
    )
    user_prompt = f"""## 業務名
{domain_name}

## 既存ドメイン
{base_domain}

## 既存実装
{chr(10).join(source_sections)}

## ヒアリング内容
{hearing_combined}

## 実装すべき拡張機能（人間が承認済み）
{gaps_desc}

各拡張機能について、既存ファイルへの ===PATCH:path=== ブロックを出力してください。
"""
    messages = [{"role": "system", "content": _STAGE2LITE_SYSTEM}, {"role": "user", "content": user_prompt}]
    raw    = call_llm(messages, model=default_model(), max_tokens=8000)
    parsed = _parse_marker_output(raw)
    return {"status": "ok", "patches": parsed["patches"],
            "extension_gaps": extension_gaps, "base_domain": base_domain}


def apply_extension_files(patches: list, extension_gaps: list, base_domain: str) -> dict:
    """
    generate_extension_files() が生成したPATCHを既存ファイルに実際に適用し、
    対応するExtensionをDslRepository.create_extension()で正式登録する。
    """
    written, errors = [], []

    for patch in patches:
        path        = patch.get("path", "")
        append_code = patch.get("append_code", "")
        instruction = patch.get("instruction", "")
        if not path or not append_code:
            continue
        try:
            abs_path = _resolve_path(path)
            if not abs_path.exists():
                errors.append({"path": path, "error": "ファイルが存在しません"}); continue
            existing = abs_path.read_text(encoding="utf-8")
            marker   = append_code.strip()[:60]
            if marker and marker in existing:
                written.append(f"{path} (skipped: already applied)"); continue

            anchor_match = re.search(r"ANCHOR:\s*(.+?)(?:\||$)", instruction)
            anchor = anchor_match.group(1).strip() if anchor_match else None
            if anchor and anchor in existing:
                existing = existing.replace(anchor, append_code + "\n\n" + anchor, 1)
            else:
                existing = (existing.rstrip()
                            + "\n\n\n# ─── Extension patch (domain_generator Stage2-lite) ───\n"
                            + append_code + "\n")

            abs_path.write_text(existing, encoding="utf-8")
            written.append(f"{path} (extension patched)")
        except Exception as e:
            errors.append({"path": path, "error": str(e)})

    registered_extensions = []
    try:
        from dsl_repository.repository import DslRepository
        repo = DslRepository()
        for gap in extension_gaps:
            try:
                # 2026-07-27修正: categoryにドメイン名(base_domain)を入れていた旧実装が
                # rolling_weekly_night_shift_cap/weekly_night_count_ui_columnの不整合
                # （category="NurseShiftWeeklyCap"）の実際の発生源だった。categoryは
                # 種類ラベル専用に統一し、適用対象ドメインはapplicable_domainsに移す。
                ext_id = repo.create_extension(
                    name=gap.get("name", "extension"),
                    category="constraint",
                    applicable_domains=[base_domain],
                    schema_fragment={"affected_fields": gap.get("affected_fields", [])},
                    solver_mapping={"description": gap.get("description", "")},
                    ui_mapping=None,
                    description=gap.get("rationale") or gap.get("description", ""),
                )
                registered_extensions.append({"name": gap.get("name"), "id": ext_id, "status": "created"})
                logger.info(f"[extension_apply] Extension登録: {gap.get('name')} (applicable_domains=[{base_domain}]) → ID={ext_id}")
            except Exception as e:
                registered_extensions.append({"name": gap.get("name"), "status": "error", "error": str(e)})
                logger.error(f"[extension_apply] Extension登録失敗: {gap.get('name')} — {e}")
    except Exception as e:
        errors.append({"path": "dsl_repository.extensions", "error": str(e)})

    return {"status": "ok" if not errors else "partial", "written": written,
            "registered_extensions": registered_extensions, "errors": errors}
