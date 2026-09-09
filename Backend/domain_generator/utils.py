
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
    _PROJECT_ROOT,
    logger,
)




# ─────────────────────────────────────────────────────────────
# 登録済みシナリオの単体リフレッシュ（2026-07-23追記、同日中に方式変更）
#
# 背景: _register_scenarios() はファイル→DBの差分同期ロジックを既に持つが、
# 従来は「ドメイン新規適用」フロー経由でしか呼ばれず、既存シナリオのJSONだけを
# 直接編集した場合、DB側は永久に古い値のまま取り残される問題があった。
#
# 当初はサーバー側のファイルパスを名前・domainから規約推測（または登録時に
# DBへ保存）する方式で実装したが、Koshoshiから「DBにファイルパスなど保存する
# 必要は無い。ユーザーが元のDSL JSONファイルが分かっていればそれを編集すれば
# よいし、分からなければ💾エクスポートでダンプしたものを編集すればよい。
# その結果のファイルをリフレッシュボタンを押した時に指定させればいい」との
# 指摘を受け、サーバー側でのファイル探索・パス保存を一切やめ、単純に
# 「ユーザーがローカルで選んだJSONファイルの内容でこのシナリオを上書きする」
# 方式に作り直した。これなら「📁シナリオ追加」経由でファイルを経由せず
# 登録されたシナリオも含め、全シナリオに同じ手順で対応できる。
# ─────────────────────────────────────────────────────────────

def refresh_scenario_from_upload(scenario_id: int, dsl_json: dict) -> dict:
    """
    登録済みシナリオ1件のdsl_jsonを、ユーザーがアップロードしたJSONの内容で
    そのまま上書きする。ファイルパスの探索・保存は一切行わない。

    Returns:
      {"status": "updated" | "no_change" | "not_found", ...}
    """
    from dsl_repository.repository import DslRepository
    repo = DslRepository()
    scenario = repo.get_scenario(scenario_id)
    if scenario is None:
        return {"status": "not_found", "scenario_id": scenario_id}

    if scenario["dsl_json"] == dsl_json:
        return {"status": "no_change", "scenario_id": scenario_id, "name": scenario["name"]}

    repo.update_scenario(scenario_id=scenario_id, dsl_json=dsl_json)
    logger.info(f"[refresh_scenario_from_upload] シナリオ更新: ID={scenario_id} ({scenario['name']})")
    return {"status": "updated", "scenario_id": scenario_id, "name": scenario["name"]}


def _infer_schema_from_dsl(dsl_json: dict, max_depth: int = 3) -> dict:
    """
    実際のシナリオJSON（主にbaseline）から、フィールド構造を推定して
    軽量なスキーマ情報を作る。完全なJSON Schemaではなく、キー名と型
    （配列の場合は要素の代表キー）を示す簡易版。レジストリー表示と、将来
    get_llm_context_for_dsl() 経由でLLMに渡す際の参考情報として使うことを想定。
    """
    def _describe(value, depth=0):
        if depth > max_depth:
            return type(value).__name__
        if isinstance(value, dict):
            return {k: _describe(v, depth + 1) for k, v in value.items()}
        if isinstance(value, list):
            if not value:
                return []
            return [_describe(value[0], depth + 1)]
        return type(value).__name__

    if not isinstance(dsl_json, dict):
        return {}
    return _describe(dsl_json)


def _ensure_dsl_definition(repo, problem_class: str, description: str = "",
                           sample_dsl_json: dict | None = None) -> None:
    """
    problem_classに対応するdsl_definitions行が無ければ新規作成する。
    既に行が存在する場合は通常何もしないが、既存行のschema_jsonが空（{}）で
    今回sample_dsl_jsonが渡されていれば、既存行を更新して埋める
    （V4.6修正: 以前はexisting行があれば無条件でreturnしていたため、
    何らかの理由でschema_json={}のまま登録された行が永久に空のままになる不具合があった。
    実際にHospitalShiftPlannerでこれが発生し確認済み）。
    """
    try:
        existing = repo.list_dsl_definitions(problem_class=problem_class)
        if existing:
            latest = existing[0]  # list_dsl_definitionsはversion DESCで返す
            if sample_dsl_json and not latest.get("schema_json") and hasattr(repo, "update_dsl_definition_schema"):
                schema_json = _infer_schema_from_dsl(sample_dsl_json)
                if schema_json:
                    repo.update_dsl_definition_schema(
                        problem_class=problem_class, version=latest["version"], schema_json=schema_json)
                    logger.info(f"[dsl_def] 既登録だがschema_json空のため更新: {problem_class} "
                                f"(id={latest.get('id')}, version={latest['version']})")
                    return
            logger.info(f"[dsl_def] 既登録: {problem_class}")
            return
        schema_json = _infer_schema_from_dsl(sample_dsl_json) if sample_dsl_json else {}
        dsl_id = repo.create_dsl_definition(
            problem_class=problem_class,
            version="1.0",
            extensions=[],
            schema_json=schema_json,
            description=description or f"{problem_class} — 自動登録（domain_generator）",
        )
        repo.log_dsl_evolution(
            dsl_id=dsl_id,
            change_type="initialization",
            trigger_type="initialization",
            business_context=f"{problem_class} の自動登録（domain_generator.py による）",
            created_by="domain_generator",
        )
        logger.info(f"[dsl_def] DSL定義登録完了: {problem_class} → ID={dsl_id} "
                    f"(schema_json {'推定済み' if schema_json else '空'})")
    except Exception as e:
        logger.error(f"[dsl_def] DSL定義登録エラー: {problem_class} — {e}", exc_info=True)


def compute_diff(path: str, new_content: str) -> dict:
    abs_path  = _resolve_path(path)
    is_new    = not abs_path.exists()
    old_lines = [] if is_new else abs_path.read_text(encoding="utf-8").splitlines(keepends=True)
    new_lines = new_content.splitlines(keepends=True)
    diff_lines = list(difflib.unified_diff(
        old_lines, new_lines, fromfile=f"a/{path}", tofile=f"b/{path}", lineterm=""))
    added   = sum(1 for l in diff_lines if l.startswith("+") and not l.startswith("+++"))
    removed = sum(1 for l in diff_lines if l.startswith("-") and not l.startswith("---"))
    return {"path": path, "new_content": new_content, "diff": "".join(diff_lines),
            "is_new_file": is_new, "lines_added": added, "lines_removed": removed}

def _resolve_path(relative_path: str) -> Path:
    return _PROJECT_ROOT / relative_path

def get_default_attachment_info() -> list:
    result = []
    for name, path in DEFAULT_ATTACHMENTS.items():
        p = Path(path)
        result.append({"name": name, "path": path, "exists": p.exists(),
                        "size": p.stat().st_size if p.exists() else 0})
    return result
