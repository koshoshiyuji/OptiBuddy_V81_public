

# --- 2026-08-15 Koshoshi調査済み ---------------------------------------
# 開発サーバー停止時（Ctrl+C）に
# "resource_tracker: There appear to be 1 leaked semaphore objects to
# clean up at shutdown" という UserWarning が出る件について。
#
# 原因はWerkzeugの対話型デバッガ(DebuggedApplication)が debug=True の
# たびに PIN認証失敗カウンタ用の multiprocessing.Value("B") を無条件で
# 生成し、セマフォを1個作ること（OptiBuddy自体のソルバー/並列化コード
# ではないことをスタックトレースで実証済み）。WERKZEUG_DEBUG_PIN=off でも
# Value自体の生成は回避できない。
#
# この warning は multiprocessing.resource_tracker が別プロセス
# （spawnv_passfdsで起動される専用の子プロセス）内で warnings.warn() を
# 呼んで出している。子プロセスは起動時に自分自身の環境変数
# PYTHONWARNINGS を見て抑制フィルタを適用するため、環境変数側で
# 対処する必要があるが、実行中のプロセス内で os.environ["PYTHONWARNINGS"]
# へ代入するだけでは実OSプロセスの環境には反映されず、後から
# fork_exec で起動される子プロセス（resource_tracker）には伝播しない
# ことを実機・再現テストの両方で確認済み（2026-08-15）。
# そのため、起動直後に自分自身を os.execve() で1度だけ再実行し、
# PYTHONWARNINGS が最初から設定された状態でプロセスを開始させることで
# 確実に子プロセスまで伝播させる（再現テストで抑制成功を確認済み）。
#
# 詳細な調査経緯は Claude Project「OptiBuddy開発」内の INSTALL_20260713.md
# （このリポジトリには含まれない、開発用ナレッジベース側のドキュメント）を参照。
# 実害はプロセス起動ごとに1個のみで蓄積・増殖はしないため、警告メッセージの
# 表示だけを抑制する（セマフォ自体が作られなくなるわけではない）。
import os as _os
import sys as _sys

_PW_FILTER = r"ignore:::multiprocessing.resource_tracker:0"
if _os.environ.get("_OPTIBUDDY_PW_PATCHED") != "1":
    _existing_pw = _os.environ.get("PYTHONWARNINGS", "")
    _os.environ["PYTHONWARNINGS"] = (
        f"{_existing_pw},{_PW_FILTER}" if _existing_pw else _PW_FILTER
    )
    _os.environ["_OPTIBUDDY_PW_PATCHED"] = "1"
    _os.execve(_sys.executable, [_sys.executable] + _sys.argv, _os.environ)
# -------------------------------------------------------------------------

import copy
import hmac
import importlib
import json
import logging
import os
import re
import sys
import threading
import time
import uuid
from urllib.parse import quote

import jsonpatch
from baplie.baplie_to_dsl import convert_baplie_text_to_dsl

try:
    from Backend.conflict_resolver import ConflictResolver
except ImportError:
    from conflict_resolver import ConflictResolver

from dsl_repository.repository import DslRepository
from dsl_transformer import convert_business_to_solver, convert_solver_to_ui
from llm.llm_client import upload_repomix_if_changed, call_llm_with_file
from flask import Flask, jsonify, request, send_file
from flask_cors import CORS
from llm.llm_interface import analyze_dsl, ask_about_dsl
from llm.relax_interface import suggest_relaxations
from solvers.decomposer import (
    DecomposerFactory, HorizonDecomposer, SpatialDecomposer,
)
from solvers.registry import SolverRegistry
from pathlib import Path
from i18n.context import set_lang, get_lang
from i18n.common_messages import t as _common_t
from i18n.nurse_shift_weekly_cap_messages import translate_scenario_meta as _ns_scenario_meta_t

from app_core import (
    app, logger, registry,
    _job_set, _job_get, _job_gc,
    _to_snake, _ensure_dsl_definition_id,
    _PROJECT_ROOT, _BACKEND_ROOT,
)
from flask import Blueprint

bp = Blueprint("routes_misc", __name__)




# ---------------------------------------------------------
# その他のエンドポイント
# ---------------------------------------------------------

@bp.route("/baplie", methods=["POST"])
def baplie():
    try:
        baplie_text = request.json.get("baplie_text", "")
        if not baplie_text.strip():
            return jsonify({"status": "error", "message": "baplie_text が空です。"}), 400
        dsl = convert_baplie_text_to_dsl(baplie_text)
        return jsonify({"status": "ok", "dsl": dsl, "container_count": len(dsl["containers"])})
    except ValueError as e:
        return jsonify({"status": "validation_error", "message": str(e)}), 200
    except Exception as e:
        logger.error(f"BAPLIE Parse Error: {str(e)}", exc_info=True)
        return jsonify({"status": "error", "message": "BAPLIEのパースに失敗しました。"}), 500


@bp.route("/health", methods=["GET"])
def health():
    return jsonify({"status": "ok", "solvers": registry.list_solvers()})


@bp.route("/ask", methods=["POST"])
def ask():
    try:
        payload  = request.json
        question = payload.get("question", "")
        if not question:
            return jsonify({"status": "error", "message": "question が空です。"}), 400
        answer = ask_about_dsl(question, payload.get("dsl", {}), payload.get("solution", {}), payload.get("history", []))
        return jsonify({"status": "ok", "explanation": answer.get("explanation", ""),
                        "dsl_patch": answer.get("dsl_patch", []),
                        "can_optimize": answer.get("can_optimize", False),
                        "root_cause": answer.get("root_cause", "")})
    except Exception as e:
        logger.error(f"/ask Error: {str(e)}", exc_info=True)
        return jsonify({"status": "error", "message": str(e)}), 500


@bp.route("/analyze", methods=["POST"])
def analyze():
    try:
        answer = analyze_dsl(request.json.get("dsl", {}))
        return jsonify({"status": "ok", "answer": answer})
    except Exception as e:
        logger.error(f"/analyze Error: {str(e)}", exc_info=True)
        return jsonify({"status": "error", "message": str(e)}), 500


@bp.route("/apply_patch", methods=["POST"])
def apply_patch():
    try:
        payload   = request.json
        dsl       = payload.get("dsl", {})
        dsl_patch = payload.get("dsl_patch", [])
        if not dsl:
            return jsonify({"status": "error", "message": _common_t("baseline.dslEmpty")}), 400
        patched_dsl = jsonpatch.JsonPatch(dsl_patch).apply(dsl)
        return jsonify({"status": "ok", "patched_dsl": patched_dsl})
    except jsonpatch.JsonPatchException as e:
        return jsonify({"status": "error", "message": f"パッチ適用失敗: {str(e)}"}), 200
    except Exception as e:
        logger.error(f"/apply_patch Error: {str(e)}", exc_info=True)
        return jsonify({"status": "error", "message": str(e)}), 500


@bp.route("/api/llm/generate_prompt", methods=["POST"])
def llm_generate_prompt():
    try:
        data   = request.get_json(force=True)
        prompt = data.get("prompt", "").strip()
        if not prompt:
            return jsonify({"status": "error", "message": "prompt is required"}), 400
        file_id = upload_repomix_if_changed()
        text = call_llm_with_file(
            [{"role": "user", "content": prompt}], file_id,
            system="あなたはOptiBuddyの開発アシスタントです。添付されたrepomixを参照しながら新規ドメインの実装コードを生成してください。",
            max_tokens=8192)
        return jsonify({"status": "ok", "text": text})
    except Exception as e:
        logger.error(f"/api/llm/generate_prompt error: {e}", exc_info=True)
        return jsonify({"status": "error", "message": str(e)}), 500
