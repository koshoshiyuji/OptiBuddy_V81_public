

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

bp = Blueprint("routes_dsl_repository", __name__)




@bp.route("/dsl_repository/dsl", methods=["GET"])
def dsl_repository_dsl():
    pc = request.args.get("problem_class", "").strip()
    v  = request.args.get("version", "").strip()
    if not pc or not v:
        return jsonify({"status": "error", "message": "problem_class と version を指定してください。"}), 400
    try:
        dsl = DslRepository().get_dsl_definition(pc, v)
        if not dsl:
            return jsonify({"status": "error", "message": "DSL定義が見つかりません。"}), 404
        return jsonify({"status": "ok", "dsl": dsl})
    except Exception as e:
        return jsonify({"status": "error", "message": "DSL定義の読み込みに失敗しました。"}), 500


@bp.route("/dsl_repository/ext", methods=["GET"])
def dsl_repository_ext():
    name = request.args.get("name", "").strip()
    if not name:
        return jsonify({"status": "error", "message": "name を指定してください。"}), 400
    try:
        ext = DslRepository().get_extension(name)
        if not ext:
            return jsonify({"status": "error", "message": "Extension が見つかりません。"}), 404
        return jsonify({"status": "ok", "extension": ext})
    except Exception as e:
        return jsonify({"status": "error", "message": "Extension の読み込みに失敗しました。"}), 500


@bp.route("/dsl_repository/scenarios", methods=["GET"])
def get_scenarios():
    try:
        scenarios = DslRepository().list_scenarios()
        # 2026-07-30 i18n対応: NurseShiftWeeklyCapの登録済みシナリオのname/
        # descriptionを、現在の表示言語に合わせて対訳辞書から解決する
        # （Backend/i18n/nurse_shift_weekly_cap_messages.py参照）。DBの内容は
        # 変更せず、レスポンス生成時にのみ差し替える。他ドメインは対象外
        # （対訳が無ければ元の日本語のまま返る）。
        for sc in scenarios:
            if sc.get("domain") == "nurse_shift_weekly_cap":
                meta = _ns_scenario_meta_t(sc.get("name", ""), sc.get("description", ""))
                sc["name"] = meta["name"]
                sc["description"] = meta["description"]
        return jsonify({"status": "ok", "scenarios": scenarios})
    except Exception as e:
        return jsonify({"status": "error", "message": str(e)}), 500


@bp.route("/dsl_repository/log_plan_selection", methods=["POST"])
def log_plan_selection():
    """
    複数Plan（例: CVRPの Plan A/B/C — 割り当てヒューリスティック×コスト重みプロファイルの
    組み合わせ）のうち、ユーザーがどれを選んだかを dsl_evolution_log に記録する。

    Request body:
      {
        "problem_class": "CapacitatedVehicleRoutingProblem",
        "selected_plan": {
          "name": "Plan B",
          "heuristic_id": "demand_desc_min_fit",
          "cost_profile": "minimize_distance"
        },
        "candidate_plans": [  # 同時に提示された全Plan（選ばれなかったものも含める）
          {"name": "Plan A", "heuristic_id": "...", "cost_profile": "...",
           "feasible": true, "kpi": {...}},
          ...
        ],
        "scenario_name": "近郊配送ルート最適化（標準）"  # 任意、業務コンテキスト用
      }
    """
    try:
        payload         = request.get_json(force=True)
        problem_class   = payload.get("problem_class", "").strip()
        selected_plan   = payload.get("selected_plan", {})
        candidate_plans = payload.get("candidate_plans", [])
        scenario_name   = payload.get("scenario_name", "")

        if not problem_class:
            return jsonify({"status": "error", "message": "problem_class は必須です"}), 400
        if not selected_plan:
            return jsonify({"status": "error", "message": "selected_plan は必須です"}), 400

        repo   = DslRepository()
        dsl_id = _ensure_dsl_definition_id(repo, problem_class)

        unselected = [p.get("name") for p in candidate_plans if p.get("name") != selected_plan.get("name")]
        before_summary = f"提示されたPlan: {', '.join(p.get('name','') for p in candidate_plans)}"
        after_summary  = (f"選択: {selected_plan.get('name','')} "
                          f"(heuristic={selected_plan.get('heuristic_id','')}, "
                          f"cost_profile={selected_plan.get('cost_profile','')})")

        log_id = repo.log_dsl_evolution(
            dsl_id=dsl_id,
            change_type="plan_selected",
            trigger_type="manual",
            business_context=scenario_name or None,
            before_summary=before_summary,
            after_summary=after_summary,
            dsl_patch={
                "selected_plan":   selected_plan,
                "candidate_plans": candidate_plans,
                "unselected_plan_names": unselected,
            },
            created_by="user",
        )
        logger.info(f"[log_plan_selection] 記録完了: problem_class={problem_class}, "
                   f"selected={selected_plan.get('name')}, log_id={log_id}")
        return jsonify({"status": "ok", "log_id": log_id})
    except Exception as e:
        logger.error(f"/dsl_repository/log_plan_selection error: {e}", exc_info=True)
        return jsonify({"status": "error", "message": str(e)}), 500


@bp.route("/dsl_repository/scenarios", methods=["POST"])
def post_scenario():
    try:
        p   = request.json
        sid = DslRepository().create_scenario(
            name=p.get("name",""), description=p.get("description",""),
            tag=p.get("tag","CUSTOM"), tag_color=p.get("tag_color","#888888"),
            domain=p.get("domain","yard"), dsl_json=p.get("dsl_json",{}))
        return jsonify({"status": "ok", "id": sid})
    except Exception as e:
        return jsonify({"status": "error", "message": str(e)}), 500


@bp.route("/dsl_repository/scenarios/<int:scenario_id>", methods=["DELETE"])
def delete_scenario(scenario_id: int):
    try:
        DslRepository().delete_scenario(scenario_id)
        return jsonify({"status": "ok"})
    except Exception as e:
        return jsonify({"status": "error", "message": str(e)}), 500


# 2026-07-23: 登録済みシナリオ単体のリフレッシュ機能。
# 当初はサーバー側でファイルパスを規約推測・DB保存する方式で実装したが、Koshoshiの
# 指摘（DBにパスを持つ必要は無い。ユーザーが元のJSONを分かっていればそれを編集、
# 分からなければ💾エクスポートでダンプしたものを編集し、その結果のファイルを
# リフレッシュ時に指定させればいい）を受けて方式変更。サーバー側でのファイル探索は
# 一切せず、リクエストボディで受け取ったdsl_json（＝ユーザーがローカルで選んで
# 読み込んだファイルの中身）でそのままDBを上書きするだけにした。
@bp.route("/dsl_repository/scenarios/<int:scenario_id>/refresh", methods=["POST"])
def refresh_scenario_endpoint(scenario_id: int):
    try:
        from domain_generator import refresh_scenario_from_upload
        payload  = request.json or {}
        dsl_json = payload.get("dsl_json")
        if not dsl_json:
            return jsonify({"status": "error", "message": "dsl_json が空です。"}), 400
        result = refresh_scenario_from_upload(scenario_id, dsl_json)
        return jsonify({"status": "ok", "result": result})
    except Exception as e:
        logger.error(f"/dsl_repository/scenarios/{scenario_id}/refresh error: {e}", exc_info=True)
        return jsonify({"status": "error", "message": str(e)}), 500


@bp.route("/dsl_repository/domain/<domain_name>", methods=["DELETE"])
def delete_domain(domain_name: str):
    """
    指定ドメインの完全削除（DB + ファイル）
    
    Query Parameters:
      - preview: true の場合、削除対象を返すだけで実際には削除しない
    
    Response:
      {
        "status": "ok",
        "domain": "production_lot_scheduler",
        "preview": false,
        "deleted": {
          "scenarios": [{"id": 1, "name": "...", "domain": "..."}],
          "dsl_definitions": [{"id": 1, "problem_class": "...", "version": "..."}],
          "evolution_logs": [{"id": 1, "dsl_id": 1}],
          "files": ["Backend/solvers/...", "Frontend/..."]
        }
      }
    """
    from dsl_repository.cleanup_domain import collect_deletion_targets, delete_domain_api, DomainDeletionBlocked
    
    preview = request.args.get("preview", "false").lower() == "true"
    
    try:
        if preview:
            # プレビューモード: 削除対象を返すだけ
            targets = collect_deletion_targets(domain_name)
            return jsonify({
                "status": "ok",
                "domain": domain_name,
                "preview": True,
                "targets": targets
            })
        else:
            # 実際に削除
            result = delete_domain_api(domain_name)
            return jsonify({
                "status": "ok",
                "domain": domain_name,
                "preview": False,
                "deleted": result
            })
    except DomainDeletionBlocked as e:
        # 2026-09-26追加: 削除すると他ファイルのimportが壊れるため中止（DB・ファイルは未変更）。
        # サーバー不具合ではないので409で返し、エラーログには残さない。
        return jsonify({"status": "error", "message": str(e)}), 409
    except Exception as e:
        logger.error(f"/dsl_repository/domain/{domain_name} DELETE error: {e}", exc_info=True)
        return jsonify({"status": "error", "message": str(e)}), 500


@bp.route("/dsl_repository/scenarios/<int:scenario_id>/export", methods=["GET"])
def export_scenario_api(scenario_id: int):
    try:
        scenario = DslRepository().get_scenario(scenario_id)
        if scenario is None:
            return jsonify({"status": "error", "message": f"シナリオID {scenario_id} が見つかりません"}), 404
        response = jsonify(scenario["dsl_json"])
        safe_name = quote(scenario['name'].replace(' ', '_') + '.json', safe='')
        response.headers["Content-Disposition"] = f"attachment; filename=\"scenario_{scenario_id}.json\"; filename*=UTF-8''{safe_name}"
        response.headers["Content-Type"] = "application/json; charset=utf-8"
        return response
    except Exception as e:
        return jsonify({"status": "error", "message": str(e)}), 500


@bp.route("/dsl_repository/registry", methods=["GET"])
def get_registry():
    try:
        repo = DslRepository()
        return jsonify({"status": "ok", "data": {
            "overview": repo.get_overview(), "dsl_definitions": repo.list_dsl_definitions(),
            "extensions": repo.list_extensions(),
            "evolution_logs": repo.get_evolution_history(dsl_id=None, limit=50),
            "solvers": registry.list_solvers()}})
    except Exception as e:
        return jsonify({"status": "error", "message": str(e)}), 500
