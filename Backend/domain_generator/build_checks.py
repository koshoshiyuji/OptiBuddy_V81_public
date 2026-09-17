
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
    _FRONTEND_ROOT,
    _PROJECT_ROOT,
    logger,
)
from .static_checks import (
    _check_big_m_objective,
    _check_no_overlap_cumulative_conflict,
    _check_no_overlap_without_sequence_var,
    _check_objective_coverage,
    _check_optional_interval_absent_value,
    _check_unnamed_expr_get_value,
    _check_unwrapped_minimize,
)
from .utils import (
    _resolve_path,
)


# 2026-08-06変更: 従来はprimary_problems.md（人手で37問題を維持、機械可読データである
# csplib_cp_mip_reference.json（31問題に意図的縮小済み）と食い違ったまま放置されていた）を
# 参照していたが、CSPLIB_REFERENCE.mdに統合した（Koshoshi指示）。REFERENCE_csplib_cp_mip_table.md・
# csplib_solver_family_tree.md・primary_problems.mdの3ファイルはdocs/_archived_20260806_
# csplib_reference_consolidation/へ退避済み。詳細はENGINEERING_LOG.md 2026-08-06参照。


# ─────────────────────────────────────────────────────────────
# CP Optimizer 既知バグの静的サニタイザー
# ─────────────────────────────────────────────────────────────

# (pattern, replacement, description) のリスト
# re.sub で順番に適用される
_CPO_SANITIZE_RULES: list[tuple[str, str, str]] = [

    # ── バグ1: no_overlap に interval_var リストと transition_matrix を直接渡している ──
    # 正しくは sequence_var 経由が必要
    # no_overlap(some_list, some_tm) → そのまま検出してログだけ出す（自動変換は複雑なため警告のみ）
    # ※ sequence_var への変換はコンテキスト依存のため正規表現での自動修正は行わない
    # 代わりに presence_of == 1 と if_then の問題を修正する

    # ── バグ2: transition_matrix の誤ったimportを自動修正 ──
    (
        r'from docplex\.cp\.modeler import transition_matrix',
        r'from docplex.cp.modeler import build_cpo_transition_matrix',
        "transition_matrix → build_cpo_transition_matrix (import修正)",
    ),
    (
        r'(\s+)tm\s*=\s*transition_matrix\(',
        r'\1tm = build_cpo_transition_matrix(',
        "transition_matrix() → build_cpo_transition_matrix() (関数呼び出し修正)",
    ),

    # ── バグ3: presence_of(x) == 1 を論理式として使っている ──
    # presence_of() はそれ自体が boolean expression なので == 1 は不要かつエラーになる
    (
        r'mdl\.presence_of\((\w+)\)\s*==\s*1',
        r'mdl.presence_of(\1)',
        "presence_of() == 1 → presence_of()",
    ),
    (
        r'presence_of\((\w+)\)\s*==\s*1',
        r'presence_of(\1)',
        "presence_of() == 1 → presence_of() (短縮形)",
    ),

    # ── バグ3: if_then の第2引数に end_before_start / start_before_end を渡している ──
    # これらは制約式であり boolean expression ではない
    # optional interval には直接渡せば absent 時に自動無効になる
    (
        r'mdl\.if_then\(\s*([^,]+),\s*mdl\.(end_before_start|start_before_end|end_before_end|start_before_start)\(([^)]+)\)\s*\)',
        r'mdl.\2(\3)',
        "if_then(cond, end_before_start(...)) → end_before_start(...) 直接適用",
    ),

    # ── バグ4: if_then の第2引数に end_of / start_of 比較を渡している ──
    # 例: mdl.if_then(cond, mdl.end_of(a) <= mdl.start_of(b))
    # これも boolean expression でなくエラーになることがある
    # → if_then ごと削除して直接制約に書き換えるのが正解だが、
    #   自動変換が難しいため、パターン検出時にログ警告のみ行う
]

# no_overlap の直接渡しを検出するパターン（自動修正はしない、ログのみ）
_CPO_WARN_PATTERNS: list[tuple[str, str]] = [
    (
        r'get_value\(\s*mdl\.presence_of\(',
        "get_value(mdl.presence_of(...)) が検出されました。このdocplexバージョンでは解の内部マッピングと"
        "一致せず KeyError になり、except で握り潰されると『解けているのに全部未割当』になる既知バグです。"
        "msol.get_var_solution(itv) → .is_present()/.get_start()/.get_end() を使ってください。",
    ),
    (
        r'get_value\(\s*mdl\.(start_of|end_of)\(',
        "get_value(mdl.start_of/end_of(...)) が検出されました。optional interval_var の解抽出では"
        "msol.get_var_solution(itv) を使う方式に置き換えてください（presence_of()と同じ既知バグパターン）。",
    ),
    (
        r'mdl\.if_then\(\s*[^,]+,\s*mdl\.\w+\([^()]*\)\s*(?:<=|>=|==|!=|<|>)\s*mdl\.\w+\(',
        "if_then(condition, mdl.xxx(...) <op> mdl.yyy(...)) が検出されました（禁止パターン4）。"
        "if_then の第2引数に比較式は渡せません。if_then ごと削除し、mdl.add(mdl.end_before_start(...)) "
        "のように対象の制約を直接 mdl.add() することを検討してください。",
    ),
]


def _sanitize_solver_code(code: str, path: str) -> str:
    """
    CP Optimizer の既知バグパターンを検出・自動修正する。
    ソルバーファイル（*_solver.py）にのみ適用する。
    """
    original = code
    fixes    = []

    for pattern, replacement, description in _CPO_SANITIZE_RULES:
        new_code, count = re.subn(pattern, replacement, code)
        if count > 0:
            fixes.append(f"  [{count}箇所] {description}")
            code = new_code

    for pattern, warning in _CPO_WARN_PATTERNS:
        if re.search(pattern, code):
            logger.warning(f"[sanitize] {path}: {warning}")

    # 目的関数の意味論的見落とし（urgent限定の未割り当てペナルティ等）を検出
    obj_warning = _check_objective_coverage(code, path)
    if obj_warning:
        logger.warning(f"[sanitize] {obj_warning}")

    # 禁止パターン1 / 6 / 6b（HANDOFF_2026-07-15b 論点1-3）
    for w in _check_no_overlap_without_sequence_var(code, path):
        logger.warning(f"[sanitize] {w}")
    for w in _check_unwrapped_minimize(code, path):
        logger.warning(f"[sanitize] {w}")
    for w in _check_unnamed_expr_get_value(code, path):
        logger.warning(f"[sanitize] {w}")
    for w in _check_big_m_objective(code, path):
        logger.warning(f"[sanitize] {w}")
    for w in _check_optional_interval_absent_value(code, path):
        logger.warning(f"[sanitize] {w}")
    for w in _check_no_overlap_cumulative_conflict(code, path):
        logger.warning(f"[sanitize] {w}")

    if fixes:

        logger.info(f"[sanitize] {path} — 自動修正 {len(fixes)} 種:\n" + "\n".join(fixes))
    else:
        logger.info(f"[sanitize] {path} — 修正なし")

    return code


# ─────────────────────────────────────────────────────────────
# repomix 自動再生成
# ─────────────────────────────────────────────────────────────

def _ensure_repomix() -> bool:
    repomix_config = _PROJECT_ROOT / "repomix.domain-addition.json"
    repomix_output = _PROJECT_ROOT / "repomix-domain-addition.xml"
    if not repomix_config.exists():
        logger.warning("[repomix] 設定ファイルなし — スキップ")
        return False
    try:
        result = subprocess.run(
            ["repomix", "--config", str(repomix_config)],
            cwd=str(_PROJECT_ROOT), capture_output=True, text=True, timeout=120,
        )
        if result.returncode == 0:
            size = repomix_output.stat().st_size if repomix_output.exists() else 0
            logger.info(f"[repomix] 再生成完了: {size:,} bytes")
            return True
        logger.warning(f"[repomix] 失敗: {result.stderr[:200]}")
        return False
    except FileNotFoundError:
        logger.warning("[repomix] コマンドなし — スキップ"); return False
    except subprocess.TimeoutExpired:
        logger.warning("[repomix] タイムアウト — スキップ"); return False
    except Exception as e:
        logger.warning(f"[repomix] エラー: {e} — スキップ"); return False


# ─────────────────────────────────────────────────────────────
# フロントエンドパッチ後の型検証（HANDOFF_2026-07-15b 論点1-2）
#
# apply_domain_files がFrontend配下のtsx/tsに書き込みを行った場合、書き込み後に
# tsc（composite project全体のインクリメンタルビルド）を走らせて型・構文エラーを
# 検出する。ここで失敗を検出した場合、呼び出し元は今回の適用全体をロールバック
# する（部分適用のまま「壊れたフロントエンド」がディスクに残る事故を防ぐ）。
# ─────────────────────────────────────────────────────────────

def _run_frontend_typecheck() -> dict:
    """Frontend/ を `tsc -b --force` で型検証する。戻り値: {"ok": bool, "output": str}。

    npx/tscが環境に無い場合は「検証不能」として警告ログのみでokを返す（検証を
    追加したことで、tsc未セットアップの開発環境でapply自体が全滅しないようにする）。
    """
    if not _FRONTEND_ROOT.exists():
        return {"ok": True, "output": "(Frontendディレクトリなし — スキップ)"}
    try:
        result = subprocess.run(
            ["npx", "--yes", "tsc", "-b", "--force"],
            cwd=str(_FRONTEND_ROOT), capture_output=True, text=True, timeout=180,
        )
        if result.returncode == 0:
            logger.info("[typecheck] tsc OK — フロントエンドの型検証に成功")
            return {"ok": True, "output": ""}
        output = (result.stdout + "\n" + result.stderr).strip()[:4000]
        logger.warning(f"[typecheck] tsc失敗（フロントエンドの型/構文エラー）:\n{output}")
        return {"ok": False, "output": output}
    except FileNotFoundError:
        logger.warning("[typecheck] npx/tscコマンドが見つからないため型検証をスキップします")
        return {"ok": True, "output": "(npx/tsc未検出 — 検証スキップ)"}
    except subprocess.TimeoutExpired:
        logger.warning("[typecheck] tscがタイムアウトしました（180秒）")
        return {"ok": False, "output": "tscがタイムアウトしました（180秒）。手動で `cd Frontend && npx tsc -b` を確認してください。"}
    except Exception as e:
        logger.warning(f"[typecheck] 型検証中に予期しないエラー: {e}")
        return {"ok": False, "output": str(e)}


def _run_backend_pycheck(paths: list[str]) -> dict:
    """
    2026-08-04追加: apply_domain_files()で書き込んだBackend/*.pyファイルを
    py_compileで構文検証する。戻り値: {"ok": bool, "errors": {path: msg},
    "failed_paths": [path, ...]}。

    背景: _patch_app_py_legacy()のrfind()バグ（2026-08-04修正済み、
    ENGINEERING_LOG参照）により、Backend/app.pyに構文エラーが書き込まれた
    まま「登録完了」がユーザーに表示され、次回バックエンド再起動時に
    SyntaxErrorで気づく、という実害が発生した。_run_frontend_typecheck()が
    Frontend/*.tsxをtscで検証しているのと同じ理屈で、Backend側もPATCH適用の
    直後に必ず構文検証する。

    py_compileはCPython標準ライブラリのみで完結し、docplex等の重い依存を
    importしないため、モジュールをimportして実行するより安全（副作用が無い）。
    """
    errors: dict[str, str] = {}
    for path in paths:
        abs_p = _resolve_path(path)
        if not abs_p.exists():
            continue
        try:
            py_compile.compile(str(abs_p), doraise=True)
        except py_compile.PyCompileError as e:
            errors[path] = str(e)
            logger.warning(f"[pycheck] 構文エラー: {path} — {e}")
        except Exception as e:
            errors[path] = str(e)
            logger.warning(f"[pycheck] 検証中に予期しないエラー: {path} — {e}")
    return {"ok": not errors, "errors": errors, "failed_paths": list(errors.keys())}


def _run_backend_import_check(paths: list[str]) -> dict:
    """
    2026-08-08追加: MedicalAppointmentScheduler登録時、Stage2が共有ファイル
    solvers/base/issue_rules.py に存在しないモジュール
    (i18n.medical_appointment_scheduler_messages) へのimportを追記し、
    py_compile()の構文検証は通過したまま「登録完了」してしまった実害を受けて追加。
    py_compileは構文しか見ないため、ModuleNotFoundError等の実行時import
    エラーは検知できない（今回のバグはまさにこれ）。issue_rules.pyは全ドメイン
    共通で読み込まれる共有ファイルのため、この1件の壊れたimportだけで無関係な
    5ドメイン(capital_project_selector / cplex_dynamic / crew_duty_scheduler /
    depot_route_planner / line_changeover_scheduler)のソルバー登録が
    軒並み失敗する事態になった。

    py_compile通過後、対象ファイルを実際に（独立した一時モジュールとして、
    完了後にsys.modulesから片付けた上で）importしてみて、ImportError/
    ModuleNotFoundError等を検知する。対象はBackend/solvers/, Backend/dsl_transformer/,
    Backend/i18n/ 配下のみ（Backend/app.py等Flaskアプリ本体は起動時副作用
    ・ルート二重登録等のリスクがあるため対象外）。import自体はsolve()等の
    重い処理を一切呼ばないため、cpoptimizer実行バイナリが無い環境でも安全。

    2026-08-11修正: 当初は「sys.modulesは汚さずに」という理由で
    sys.modules[temp_name]への登録を省略していたが、これがモジュール側の
    @dataclassデコレータ処理（Backend/solvers/base/issue_rules.pyのIssueRule等）を
    壊す既知のPython挙動を引き起こしていた。module_from_spec()で作った
    モジュールをsys.modulesに登録せずexec_module()すると、dataclasses内部の
    _is_type()がsys.modules.get(cls.__module__)（＝temp_name）でNoneを引き、
    「AttributeError: 'NoneType' object has no attribute '__dict__'」で
    exec_module自体が失敗する（Python自身がスタンドアロンimportをサポートする
    ために要求する挙動——importlib公式ドキュメントでも「sys.modulesに
    登録するのはこちらの責任」と明記されている）。issue_rules.pyを間接的に
    importする（ほぼ全ドメインのsolver.pyが該当）だけで本チェックが常に
    誤ってimport_check_failedと判定し、ロールバックしてしまう実害を
    ProductionLineSequencing登録（2026-08-11）で確認。対策として、
    exec_module前にsys.modulesへ登録し、完了後（成功・失敗どちらでも）に
    popして後片付けする。
    """
    import importlib.util
    import sys
    import uuid

    SAFE_PREFIXES = ("Backend/solvers/", "Backend/dsl_transformer/", "Backend/i18n/")
    errors: dict[str, str] = {}
    for path in paths:
        if not path.startswith(SAFE_PREFIXES) or not path.endswith(".py"):
            continue
        abs_p = _resolve_path(path)
        if not abs_p.exists():
            continue
        temp_name = f"_gate2_importcheck_{uuid.uuid4().hex}"
        try:
            spec = importlib.util.spec_from_file_location(temp_name, abs_p)
            module = importlib.util.module_from_spec(spec)
            sys.modules[temp_name] = module
            try:
                spec.loader.exec_module(module)
            finally:
                sys.modules.pop(temp_name, None)
        except ImportError as e:
            if "relative import" in str(e):
                # ファイル単体を一時モジュールとしてロードしているため、相対import
                # を使うファイルは（このチェックの都合上）誤検知しうる。既存コード
                # のimport規約（絶対import）から外れる稀なケースなので、判定不能
                # としてスキップし、誤ってロールバックしない。
                logger.info(f"[importcheck] 相対importのため判定スキップ: {path}")
                continue
            errors[path] = f"{type(e).__name__}: {e}"
            logger.warning(f"[importcheck] importエラー: {path} — {e}")
        except Exception as e:
            errors[path] = f"{type(e).__name__}: {e}"
            logger.warning(f"[importcheck] importエラー: {path} — {e}")
    return {"ok": not errors, "errors": errors, "failed_paths": list(errors.keys())}
