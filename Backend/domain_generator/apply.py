
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
    _BACKEND_ROOT,
    _DOMAIN_SOURCE_FILES,
    _PROJECT_ROOT,
    _ensure_domain_registry_reconciled,
    _to_snake,
    logger,
)
from .build_checks import (
    _run_backend_import_check,
    _run_backend_pycheck,
    _run_frontend_typecheck,
    _sanitize_solver_code,
)
from .registry_patch import (
    _patch_app_py_legacy,
    _patch_converter_dispatch,
    _patch_home_screen_tag_map,
    _register_scenarios,
    _to_pascal,
    _update_domain_registry_from_diffs,
)
from .stage2 import (
    _build_scenario_registrations_from_diffs,
)
from .utils import (
    _resolve_path,
)




def apply_scenarios(scenarios: dict, domain_name: str, scenario_registrations: list) -> dict:
    snake   = _to_snake(domain_name)
    written, errors = [], []
    for suffix, scenario_data in scenarios.items():
        if not scenario_data: continue
        path     = f"Backend/dsl_repository/scenarios/{snake}_{suffix}.json"
        abs_path = _resolve_path(path)
        try:
            abs_path.parent.mkdir(parents=True, exist_ok=True)
            abs_path.write_text(json.dumps(scenario_data, ensure_ascii=False, indent=2), encoding="utf-8")
            written.append(path)
        except Exception as e:
            errors.append({"path": path, "error": str(e)})
    logger.info(f"[apply] _register_scenarios に渡す scenario_registrations: {len(scenario_registrations)} 件")
    scenarios_registered = _register_scenarios(scenario_registrations)
    return {"status": "ok" if not errors else "partial", "written": written,
            "scenarios_registered": scenarios_registered, "errors": errors}


def apply_domain_files(approved_diffs, approved_patches, scenario_registrations) -> dict:
    written, errors = [], []

    # 2026-09-09追加: solvers/base/issue_rules.py は Backend/solvers/base/issue_rules/
    # パッケージへ分割済み（単一ファイルはもう存在しない）。現行のStage2テンプレートは
    # このパスへのPATCHを生成しないが、テンプレート指示なしでもLLMが自発的に旧パスへ
    # PATCHを出力した前例がある（2026-08-08 MedicalAppointmentScheduler登録時の事故。
    # 当時は単一ファイルが実在したため黙って末尾に追記され、無関係な5ドメインの
    # ソルバー登録が巻き添えで壊れた）。分割後は abs_path.exists() が False になり
    # 素のままなら「ファイルが存在しません」で即ロールバックされるだけだが（8月8日より
    # 安全な壊れ方ではある）、それでもPATCH自体は失われてしまう。実体である__init__.py
    # へここで一元的にリダイレクトしておけば、以降の_snapshot/written/pycheck/
    # importcheckが全て同じ実パスを参照するようになり、バックアップとロールバックの
    # 整合性も保たれる（このリダイレクトを個々のPATCH適用ループ内だけで行うと、
    # 冒頭の_snapshotが旧パスのままバックアップを取ってしまい、ロールバック時に
    # 実際の変更先（__init__.py）が復元されないというバグになるため、必ず
    # _snapshot/_backup構築より前、関数の先頭でapproved_patches自体を書き換える）。
    _LEGACY_ISSUE_RULES_PATH  = "Backend/solvers/base/issue_rules.py"
    _ISSUE_RULES_PACKAGE_INIT = "Backend/solvers/base/issue_rules/__init__.py"
    for patch in approved_patches:
        if patch.get("path") == _LEGACY_ISSUE_RULES_PATH:
            patch["path"] = _ISSUE_RULES_PACKAGE_INIT

    # 論点1-2: 書き込み前に対象ファイルの原状態を退避しておく。Frontend配下の
    # tsc検証に失敗した場合、ここに保存した内容で今回の適用を丸ごと元に戻す。
    _backup: dict[str, str | None] = {}

    def _snapshot(path: str):
        if not path or path in _backup:
            return
        abs_p = _resolve_path(path)
        _backup[path] = abs_p.read_text(encoding="utf-8") if abs_p.exists() else None

    for item in approved_diffs:
        _snapshot(item.get("path", ""))
    for patch in approved_patches:
        _snapshot(patch.get("path", ""))

    for item in approved_diffs:
        path, content = item.get("path", ""), item.get("new_content", "")
        if not path: continue
        try:
            # ソルバーファイルはサニタイズを適用
            if path.endswith("_solver.py"):
                content = _sanitize_solver_code(content, path)
                item = {**item, "new_content": content}

            abs_path = _resolve_path(path)
            abs_path.parent.mkdir(parents=True, exist_ok=True)
            abs_path.write_text(content, encoding="utf-8")
            written.append(path)
        except Exception as e:
            errors.append({"path": path, "error": str(e)})

    for patch in approved_patches:
        path        = patch.get("path", "")
        append_code = patch.get("append_code", "")
        instruction = patch.get("instruction", "")
        if not path or not append_code: continue
        try:
            abs_path = _resolve_path(path)
            if not abs_path.exists():
                errors.append({"path": path, "error": "ファイルが存在しません"}); continue
            existing = abs_path.read_text(encoding="utf-8")
            marker   = append_code.strip()[:60]
            if marker and marker in existing:
                written.append(f"{path} (skipped: already applied)"); continue

            if path == "Backend/app.py" and "_LEGACY_SOLVERS" in instruction:
                existing = _patch_app_py_legacy(existing, append_code)
            elif path == "Backend/dsl_transformer/business_to_solver.py":
                existing = _patch_converter_dispatch(existing, append_code, "# ── 新規4DSLドメインはここに追加 ──")
            elif path == "Backend/dsl_transformer/solver_to_ui.py":
                existing = _patch_converter_dispatch(existing, append_code, "# ── 新規4DSLドメインはここに追加 ──")
            else:
                # 2026-07-17修正: この汎用フォールバックはPython向けの`#`コメント記法を
                # 決め打ちしていたため、.ts/.tsx等の非Pythonファイルに対して呼ばれると
                # 確実にtsc型検証で構文エラーになっていた（実機で特定・再発防止）。
                # 拡張子に応じたコメント記法を使う。
                _comment_prefix = "//" if path.endswith((".ts", ".tsx", ".js", ".jsx")) else "#"
                existing = (existing + f"\n\n{_comment_prefix} ─── Auto-generated by domain_generator ───\n"
                            + append_code + "\n")

            abs_path.write_text(existing, encoding="utf-8")
            written.append(f"{path} (patched)")
        except Exception as e:
            errors.append({"path": path, "error": str(e)})

    def _rollback(_backup: dict[str, str | None]) -> list[dict]:
        rollback_errors = []
        for path, original in _backup.items():
            abs_p = _resolve_path(path)
            try:
                if original is None:
                    if abs_p.exists():
                        abs_p.unlink()
                else:
                    abs_p.write_text(original, encoding="utf-8")
            except Exception as e:
                rollback_errors.append({"path": path, "error": str(e)})
        return rollback_errors

    # 論点1-2: Frontend配下に何か書き込んだ場合のみtsc型検証を行う。失敗したら
    # 今回のapply_domain_files呼び出し全体（diffs+patches）をロールバックし、
    # ドメイン登録・シナリオ登録には進まない（フロントエンドが壊れた状態で
    # 「新規ドメイン登録完了」にはしない）。
    frontend_touched = any(w.split(" ", 1)[0].startswith("Frontend/") for w in written)
    if frontend_touched:
        typecheck = _run_frontend_typecheck()
        if not typecheck["ok"]:
            logger.warning("[apply] tsc型検証に失敗 — 今回の適用をロールバックします")
            rollback_errors = _rollback(_backup)
            result = {
                "status": "typecheck_failed",
                "written": [],
                "scenarios_registered": [],
                "errors": [{"path": "Frontend (tsc)", "error": typecheck["output"]}],
            }
            if rollback_errors:
                logger.error(f"[apply] ロールバック中にエラー: {rollback_errors}")
                result["errors"].append({
                    "path": "rollback",
                    "error": f"ロールバックにも失敗したファイルがあります: {rollback_errors}",
                })
            return result

    # 2026-08-04追加（SteelMillSlabDesign登録で発覚: _patch_app_py_legacy()の
    # rfind()バグにより、Backend/app.pyに構文エラーが書き込まれたまま
    # 「登録完了」画面が表示され、次回バックエンド再起動まで誰も気づけなかった。
    # Frontend/*.tsxがtsc型検証+ロールバックで守られているのと同じ理屈で、
    # Backend/*.pyもpy_compileで構文検証し、失敗したら同様にロールバックする。
    # これによりPATCH適用ロジックに今後別のバグが入っても、「登録完了」画面が
    # 構文的に壊れたapp.pyを黙って確定させることはなくなる。
    backend_py_touched = [
        w.split(" ", 1)[0] for w in written
        if w.split(" ", 1)[0].startswith("Backend/") and w.split(" ", 1)[0].endswith(".py")
    ]
    if backend_py_touched:
        pycheck = _run_backend_pycheck(backend_py_touched)
        if not pycheck["ok"]:
            logger.warning(f"[apply] Backend Python構文検証に失敗 — 今回の適用をロールバックします: "
                            f"{pycheck['failed_paths']}")
            rollback_errors = _rollback(_backup)
            result = {
                "status": "pycheck_failed",
                "written": [],
                "scenarios_registered": [],
                "errors": [{"path": p, "error": e} for p, e in pycheck["errors"].items()],
            }
            if rollback_errors:
                logger.error(f"[apply] ロールバック中にエラー: {rollback_errors}")
                result["errors"].append({
                    "path": "rollback",
                    "error": f"ロールバックにも失敗したファイルがあります: {rollback_errors}",
                })
            return result

    # 2026-08-08追加: py_compileは構文しか見ないため、共有ファイルへの追記が
    # 存在しないモジュールをimportするなど「構文は正しいが実行時にimportで
    # 落ちる」バグを検知できなかった（MedicalAppointmentScheduler登録時に
    # issue_rules.pyが壊れ、無関係な5ドメインのソルバー登録が巻き添えで
    # 失敗した実害を受けて追加）。py_compile通過後、実際にimportしてみて
    # 検証し、失敗したら同じ_backupからロールバックする。
    if backend_py_touched:
        importcheck = _run_backend_import_check(backend_py_touched)
        if not importcheck["ok"]:
            logger.warning(f"[apply] Backend importチェックに失敗 — 今回の適用をロールバックします: "
                            f"{importcheck['failed_paths']}")
            rollback_errors = _rollback(_backup)
            result = {
                "status": "import_check_failed",
                "written": [],
                "scenarios_registered": [],
                "errors": [{"path": p, "error": e} for p, e in importcheck["errors"].items()],
            }
            if rollback_errors:
                logger.error(f"[apply] ロールバック中にエラー: {rollback_errors}")
                result["errors"].append({
                    "path": "rollback",
                    "error": f"ロールバックにも失敗したファイルがあります: {rollback_errors}",
                })
            return result

    _update_domain_registry_from_diffs(approved_diffs)

    # TAG_MAP への自動追加
    for item in approved_diffs:
        path = item.get("path", "")
        m = re.match(r"Backend/dsl_repository/scenarios/(.+)_baseline\.json$", path)
        if m:
            snake = m.group(1)
            pascal = _to_pascal(snake)
            # シナリオJSONから tag_color を取得（なければデフォルト）
            try:
                dsl_json  = json.loads(item.get("new_content", "{}"))
                tag_color = "#8b5cf6"  # デフォルト色
            except Exception:
                tag_color = "#8b5cf6"
            _patch_home_screen_tag_map(pascal, snake, tag_color)

    if not scenario_registrations:
        logger.warning("[apply] scenario_registrations が空 → approved_diffs からフォールバック構築")
        scenario_registrations = _build_scenario_registrations_from_diffs(approved_diffs)

    logger.info(f"[apply] _register_scenarios に渡す scenario_registrations: {len(scenario_registrations)} 件")
    scenarios_registered = _register_scenarios(scenario_registrations)
    return {"status": "ok" if not errors else "partial", "written": written,
            "scenarios_registered": scenarios_registered, "errors": errors}


# ───────────────────────────────────────────────────────
# Stage 2-lite: 拡張（extension_gaps）の生成・適用（旧方式、下に新方式あり）

# ───────────────────────────────────────────────────────
# Stage 2-lite V2: パターン3（拡張）の実行本体（コピー+関数単位完全置換方式）
#
# 背景: 直上の旧方式(generate_extension_files/apply_extension_files)はbase_domainの
# 共有ファイル（cvrp_converter.py等）を直接断片PATCHしていたが、(a) 既存シナリオ利用者全員が
# 影響を受ける、(b) LLMの断片挿入がPythonの構文境界を無視しやすい、という問題があり、
# 実際に2ファイルとも2回とも構文エラーでCVRP全体を破壊した。
#
# 新方式は以下2点を構造的に保障する:
#   1. 既存ファイル（base_domain本体）は一切変更しない。拡張が必要なときは
#      実装ファイル（converter/ui_converter/solver）を新ドメイン名でコピーし、
#      そのコピー先にのみ変更を加える。コピー先の関数名/クラス名は
#      _solve_4dsl_generic() の命名規約に一致させるので、app.pyへのPATCHは一切不要。
#   2. 拡張実装は断片挿入ではなく、必ず「関数一つ丸ごとの完全な定義」単位で
#      LLMに出力させる。
# ───────────────────────────────────────────────────────

def _rename_domain_symbols(code: str, old_pascal: str, new_pascal: str, new_snake: str) -> str:
    """
    コピーしたファイル内の関数名・クラス名・ problem_class 文字列を
    新ドメイン名に付け替える（実装ロジック自体は一切変えない）。
    """
    code = re.sub(rf'\b{re.escape(old_pascal)}Solver\b', f'{new_pascal}Solver', code)
    code = re.sub(r'\bdef convert_\w+_to_solver\b', f'def convert_{new_snake}_to_solver', code)
    code = re.sub(r'\bdef convert_\w+_to_ui\b', f'def convert_{new_snake}_to_ui', code)
    code = re.sub(rf'\b{re.escape(old_pascal)}\b', new_pascal, code)
    return code


def copy_domain_for_extension(base_domain: str, new_domain_name: str) -> dict:
    """
    パターン3: base_domain の実装ファイル（converter/ui_converter/solver）を、
    新しい problem_class（new_domain_name）専用にコピーし、既存ファイルは一切変更しない。
    """
    _ensure_domain_registry_reconciled()
    new_snake  = _to_snake(new_domain_name)
    new_pascal = new_domain_name
    old_pascal = base_domain
    src = _DOMAIN_SOURCE_FILES.get(base_domain, {})

    role_to_target = {
        "converter":    _BACKEND_ROOT / "dsl_transformer" / f"{new_snake}_converter.py",
        "ui_converter": _BACKEND_ROOT / "dsl_transformer" / f"{new_snake}_ui_converter.py",
        "solver":       _BACKEND_ROOT / "solvers" / f"{new_snake}_solver.py",
    }

    copied = {}
    for role, src_path in src.items():
        if not src_path:
            continue
        target_path = role_to_target.get(role)
        if target_path is None:
            continue
        try:
            code = Path(src_path).read_text(encoding="utf-8")
            code = _rename_domain_symbols(code, old_pascal, new_pascal, new_snake)
            target_path.parent.mkdir(parents=True, exist_ok=True)
            target_path.write_text(code, encoding="utf-8")
            copied[role] = str(target_path.relative_to(_PROJECT_ROOT))
            logger.info(f"[copy_domain] {role}: {src_path} → {target_path}")
        except Exception as e:
            logger.error(f"[copy_domain] コピー失敗 role={role}: {e}")

    return {"new_snake": new_snake, "new_pascal": new_pascal, "copied": copied}
