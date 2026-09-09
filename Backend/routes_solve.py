

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

bp = Blueprint("routes_solve", __name__)




# ---------------------------------------------------------------------------
# 解チェッカーの非同期分岐（DESIGN_2026-07-21 3-3節、2026-07-24実装）
#
# 各ドメインソルバーが result["_deferred_checks"] に積んだ
# {"domain", "check_id", "cost_class", "size", "run"} のリストを受け取り、
# background threadで実行して _job_set() に書き込む。/relax/start・
# /api/domain/run と同じ「即座にjob_idを返し、/checks/status/<job_id>で
# ポーリングする」慣習に従う。O(n²)以上のチェックが閾値を超えた場合のみ
# 発生する経路のため、通常のsolveでは呼ばれない（_deferred_checksがNone）。
# ---------------------------------------------------------------------------

def _run_deferred_checks_job(job_id: str, deferred_checks: list) -> None:
    """
    2026-07-24追加（DESIGN_2026-07-21 7節 未決事項2、Koshoshiと合意して決着）:
    非同期チェックで後から違反が見つかった場合の方針は「警告追記のみ」とする。
    既に返却・表示・承認済みの解を自動的に無効化・再ソルブさせる仕組みは
    意図的に実装しない（TruckDispatcher/MeetingRoom等、現実世界で既に
    行動が起きている可能性がある解を、バックエンドの後追い判定だけで
    勝手に覆すのは危険なため。実装コストも低く抑えられる）。
    各issueに _deferred_check=True を付与し、「最初のレスポンスより後に
    判明した追加警告である」ことをフロントエンド（未実装）が区別できるように
    しておく。
    """
    with app.app_context():
        try:
            all_issues: list = []
            for d in deferred_checks:
                check_id = d.get("check_id", "")
                try:
                    found = d["run"]()
                    for issue in found:
                        issue["_deferred_check"] = True
                    all_issues.extend(found)
                except Exception as e:
                    logger.error(f"[async_check:{job_id}] {d.get('domain')}/{check_id} で例外: {e}",
                                 exc_info=True)
            _job_set(job_id, stage="done", issues=all_issues)
        except Exception as e:
            logger.error(f"[async_check:{job_id}] エラー: {e}", exc_info=True)
            _job_set(job_id, stage="error", error=str(e))


def _start_deferred_checks_job(deferred_checks: list | None) -> str | None:
    """
    deferred_checksがあればbackground threadを起動しjob_idを返す。
    無ければ何もせずNoneを返す（通常のsolveではこちら）。
    """
    if not deferred_checks:
        return None
    job_id = uuid.uuid4().hex
    _job_set(job_id, stage="running",
             checks=[{"domain": d.get("domain"), "check_id": d.get("check_id"),
                      "cost_class": d.get("cost_class"), "size": d.get("size")}
                     for d in deferred_checks])
    thread = threading.Thread(target=_run_deferred_checks_job, args=(job_id, deferred_checks), daemon=True)
    thread.start()
    return job_id


# ---------------------------------------------------------
# ユーティリティ
# ---------------------------------------------------------

def build_issue_list(issue_actions, backend_issues):
    issue_list = []
    for issue in backend_issues:
        issue_id = issue.get("id", "")
        action   = issue_actions.get(issue_id)
        related  = issue.get("relatedContainerIds", [])
        pair = [related[1], related[0]] if issue_id.startswith("is_yard_") and len(related) == 2 else related
        issue_list.append({"id": issue_id, "action": action, "type": issue.get("type", ""), "pair": pair})
    return issue_list


def apply_yard_swaps(dsl, swaps):
    import copy as _copy
    dsl = _copy.deepcopy(dsl)
    containers    = dsl.get("containers", [])
    container_map = {str(c.get("id")): c for c in containers}
    for cid_a, cid_b in swaps:
        if cid_a not in container_map or cid_b not in container_map:
            continue
        c_a, c_b = container_map[cid_a], container_map[cid_b]
        c_a["yard"], c_b["yard"] = _copy.deepcopy(c_b["yard"]), _copy.deepcopy(c_a["yard"])
    return dsl


def normalize_issue_actions(issue_actions):
    return {k: ("ACCEPTED" if v == "ACCEPT" else v) for k, v in issue_actions.items()}


def _build_unassignable_issues(unassignable):
    if not unassignable:
        return []
    container_ids = [u["container_id"] for u in unassignable]
    reasons = "\n".join(f"・{u['container_id']}: {u['reason']}" for u in unassignable)
    return [{"id": "solve_failed", "type": "SOLVE_FAILED", "severity": "CRITICAL",
             "title": "解なし — クレーン到達不可またはシフト制約違反",
             "message": f"{len(unassignable)} 件のコンテナを割り当てられるクレーンが存在しません。\n{reasons}",
             "relatedContainerIds": container_ids,
             "diagnostic": {"reason": "クレーン未到達またはシフト制約違反", "details": unassignable}}]


def _import_fresh(mod_name: str):
    """
    2026-07-19新設: domain_generator.py の run_gate2_dynamic_verification 内に
    ある同名ヘルパーと同じ理由で追加。Flaskは長時間稼働プロセスであるため、
    ドメイン削除→再登録で converter/solver/ui_converter が同じモジュールパスに
    上書きされても、既に sys.modules にキャッシュされていると
    importlib.import_module() はディスクを読み直さず古いコードを返し続ける。
    Gate2の動的検証（登録時）側は _import_fresh で既に対策済みだったが、
    実際にユーザーがStudio画面から「実行」する本番の解決パス
    （_try_dynamic_solver / _solve_4dsl_generic）には同じ対策が無く、
    登録し直した直後にサーバーを再起動しないと古いソルバーが動く恐れがあった。
    """
    if mod_name in sys.modules:
        return importlib.reload(sys.modules[mod_name])
    return importlib.import_module(mod_name)


def _try_dynamic_solver(problem_class: str, dsl: dict, issue_statuses: dict):
    """
    4DSL未実装ドメインへのフォールバック。
    管理済みドメインはここに到達しない。
    """
    if not problem_class:
        return None
    # 4DSL/レジストリ管理済みドメインは dynamic_solver に回さない
    _MANAGED = {"YardPlanning"}
    if problem_class in _MANAGED:
        return None
    snake       = _to_snake(problem_class)
    solver_path = _BACKEND_ROOT / "solvers" / f"{snake}_solver.py"
    if not solver_path.exists():
        return None
    try:
        module       = _import_fresh(f"solvers.{snake}_solver")
        solver_class = getattr(module, f"{problem_class}Solver", None)
        if solver_class is None:
            for attr_name in dir(module):
                if "Solver" in attr_name and attr_name != "BaseSolver":
                    solver_class = getattr(module, attr_name)
                    break
        if solver_class is None:
            return None
        result = solver_class({**dsl, "issue_statuses": issue_statuses}).solve()
        logger.info(f"[dynamic_solver] 完了: {problem_class}, status={result.get('status')}")
        return jsonify({"status": result.get("status", "ok"), "tasks": [], "makespan": 0,
                        "issues": result.get("issues", []), "containers": [],
                        "solutions": result.get("solutions", []),
                        "ui_dsl": {}, "resolved": [], "skipped": [], "skip_reasons": {}})
    except Exception as e:
        logger.error(f"[dynamic_solver] エラー: {problem_class} — {e}", exc_info=True)
        return jsonify({"status": "validation_error", "issues": [
            {"id": "dynamic-solver-error", "severity": "CRITICAL",
             "title": f"{problem_class} ソルバーエラー",
             "message": str(e), "relatedContainerIds": []}]}), 200


# 旧CapacitatedVehicleRoutingProblemは削除済み（CP Optimizer化されたTruckDispatcherに置き換え、
# domain_generator.pyのBASE_PROBLEM_MAPもTruckDispatcherを指すよう更新済み）。
# def _solve_next_domain(dsl, issue_statuses): ...


def _solve_4dsl_generic(problem_class: str, dsl: dict, issue_statuses: dict):
    """
    規約ベースの汎用4DSLソルバー。
    
    規約:
      - dsl_transformer/{snake}_converter.py に convert_{snake}_to_solver() が存在
      - solvers/{snake}_solver.py に {PascalCase}Solver クラスが存在
      - dsl_transformer/{snake}_ui_converter.py に convert_{snake}_to_ui() が存在
    
    処理フロー:
      1. Business DSL → Solver Input DSL (converter)
      2. Solver Input DSL → Solver Output DSL (solver)
      3. Solver Output DSL → UI DSL (ui_converter)
      4. solutions[i].ui_dsl を個別生成（CVRPパターン）
      5. トップレベル ui_dsl を最初のfeasibleなPlanから生成
    
    戻り値:
      - 成功: Flask jsonify レスポンス
      - 4DSL変換ファイルが存在しない: None（フォールバック用）
      - エラー: Flask jsonify エラーレスポンス
    """
    snake = _to_snake(problem_class)
    
    try:
        # Step 1: Business → Solver Input 変換
        converter_module = _import_fresh(f"dsl_transformer.{snake}_converter")
        convert_to_solver = getattr(converter_module, f"convert_{snake}_to_solver")
        solver_input = convert_to_solver({**dsl, "issue_statuses": issue_statuses})

        # Step 2: Solver 実行
        solver_module = _import_fresh(f"solvers.{snake}_solver")
        solver_class = getattr(solver_module, f"{problem_class}Solver")
        result = solver_class(solver_input).solve()

        # 2026-07-24追加: 解チェッカーの非同期分岐（DESIGN_2026-07-21 3-3節）。
        # ソルバーがO(n²)以上のチェックをインスタンス規模の閾値超過で
        # 見送った場合、result["_deferred_checks"] に {"run": callable, ...} の
        # リストが積まれている。ここでbackground threadを起動し、即座に
        # レスポンスを返す（結果は /checks/status/<job_id> で後から取得）。
        # 全4DSLドメイン共通の合流点のため、ここ1箇所にのみ実装する。
        async_check_job_id = _start_deferred_checks_job(result.pop("_deferred_checks", None))

        # Step 3: UI DSL 変換
        ui_converter_module = _import_fresh(f"dsl_transformer.{snake}_ui_converter")
        convert_to_ui = getattr(ui_converter_module, f"convert_{snake}_to_ui")
        
        raw_solutions = result.get("solutions", [])
        
        # 各Planのui_dslを個別生成（CVRPパターン）
        for plan in raw_solutions:
            plan_output = {**result, "solutions": [plan]}
            plan["ui_dsl"] = convert_to_ui(plan_output, business_dsl=dsl)
        
        # トップレベルui_dsl: 最初のfeasibleなPlanのものを使う
        first_feasible_idx = next((i for i, s in enumerate(raw_solutions) if s.get("feasible")), 0)
        top_ui_dsl = (raw_solutions[first_feasible_idx]["ui_dsl"]
                      if raw_solutions else convert_to_ui(result, business_dsl=dsl))
        
        logger.info(f"[4DSL Generic] {problem_class} 完了: {len(raw_solutions)} Plans")

        response_body = {
            "status": result.get("status", "ok"),
            "tasks": [],
            "makespan": 0,
            "issues": result.get("issues", []),
            "containers": [],
            "solutions": raw_solutions,
            "ui_dsl": top_ui_dsl,
        }
        if async_check_job_id:
            # 2026-07-24追加: 非同期に回された解チェッカーのjob_id。既存の
            # /relax/start・/api/domain/run と同じポーリング慣習
            # （/checks/status/<job_id>）に従う。閾値未満なら通常はNone。
            response_body["async_check_job_id"] = async_check_job_id

        return jsonify(response_body)
        
    except (ImportError, AttributeError) as e:
        # 4DSL変換ファイルが存在しない場合はNoneを返す（フォールバック）
        logger.debug(f"[4DSL Generic] {problem_class} の変換ファイルが見つかりません: {e}")
        return None
    except Exception as e:
        logger.error(f"[4DSL Generic] {problem_class} エラー: {e}", exc_info=True)
        return jsonify({"status": "validation_error", "issues": [
            {"id": "config-error", "severity": "CRITICAL",
             "title": _common_t("baseline.configErrorGeneric.title", problem_class=problem_class),
             "message": str(e), "relatedContainerIds": []}]}), 200


# ---------------------------------------------------------
# /baseline
# ---------------------------------------------------------

# 2026-07-20: MeetingRoom/StoreSite個別ハードコード版の_solve_meeting_room()/
# _solve_meeting_room2()/_solve_store_site()と、常に空だった_DSL4_SOLVERSルーティング
# テーブルを削除（デッドコード、docs/ENGINEERING_LOG.md参照）。両ドメインとも実際は
# _solve_4dsl_generic()の規約ベースルーターのみで動作しており、これらの関数は一度も
# 呼ばれていなかった（_DSL4_SOLVERSに要素が追加される箇所がコード上どこにもなかった
# ため）。特にmeeting_room2は存在しないconverter/solverファイルを参照する孤立コードだった。

# GhostKitchen方式（レガシー）のルーティングテーブル。
# 2026-07-11: GhostKitchen/ProjectPlanner/BinPackingを削除したため空。
# 2026-08-04: SteelMillSlabDesign登録時にGate2自己修復ループが _solve_steel_mill_slab_design()
# という専用関数をここに追加しようとしたが、これは _try_dynamic_solver()（solvers/{snake}_solver.py
# の {ProblemClass}Solver を規約ベースで自動検出・呼び出す既存の汎用フォールバック）と
# 完全に重複するデッドコードだった。しかもパッチ適用自体が誤った位置（llm_generate_prompt()の
# 例外ハンドラ内）に差し込まれてSyntaxErrorを起こしていた。SteelMillSlabDesignSolverは
# solvers/steel_mill_slab_design_solver.py に既に存在するため、_try_dynamic_solver()が
# 何もしなくても自動的に拾う。上記2026-07-20のMeetingRoom/StoreSiteと同じパターンのため、
# 専用関数は追加せずこのテーブルは空のままにする。
_LEGACY_SOLVERS = {}


@bp.route("/baseline", methods=["POST"])
def baseline():
    try:
        payload       = request.json
        dsl           = payload.get("dsl", {})
        issue_actions = payload.get("issueActions", {})
        if not dsl:
            return jsonify({"status": "error", "message": _common_t("baseline.dslEmpty")}), 400

        issue_statuses = normalize_issue_actions(issue_actions)
        problem_class  = dsl.get("problem_class") or dsl.get("metadata", {}).get("problem_class", "")

        # 汎用4DSLソルバー（規約ベース自動検出）
        generic_4dsl = _solve_4dsl_generic(problem_class, dsl, issue_statuses)
        if generic_4dsl is not None:
            return generic_4dsl

        # GhostKitchen方式（既存）
        if problem_class in _LEGACY_SOLVERS:
            return _LEGACY_SOLVERS[problem_class](dsl, issue_statuses)

        # 動的ソルバー（solver.py があるが4DSL未実装のもの）
        dynamic = _try_dynamic_solver(problem_class, dsl, issue_statuses)
        if dynamic is not None:
            return dynamic

        # YardPlanning（デフォルト）
        return _solve_yard_planning_default(dsl, issue_actions, issue_statuses)

    except ValueError as e:
        return jsonify({"status": "validation_error", "issues": [
            {"id": "config-error", "severity": "CRITICAL", "title": _common_t("baseline.configError"),
             "message": str(e), "relatedContainerIds": []}]}), 200
    except Exception as e:
        logger.error(f"Unexpected Error: {str(e)}", exc_info=True)
        return jsonify({"status": "error", "message": "Internal Server Error"}), 500


def _solve_yard_planning_default(dsl: dict, issue_actions: dict, issue_statuses: dict):
    """
    /baseline のYardPlanningデフォルト経路（4DSL/Legacy/Dynamicいずれの
    ソルバーにも該当しない problem_class のフォールバック実装）。

    2026-07 追記: `/relax` のドライラン検証（`_dry_run_feasibility`）からも
    同じロジックを再利用するため、`baseline()` から抽出した（純粋な関数抽出・
    ロジック変更なし）。issue_actions は ConflictResolver の重複解消判定にのみ
    使うため、ドライラン用に空辞書 {} を渡しても安全。
    """
    temp_solver_input = convert_business_to_solver(dsl)
    temp_solver_input["issue_statuses"] = issue_statuses

    unassignable = temp_solver_input.get("unassignable_containers", [])
    if unassignable:
        return jsonify({"status": "ok", "tasks": [], "makespan": 0,
                        "issues": _build_unassignable_issues(unassignable),
                        "containers": dsl.get("containers", []), "solutions": [],
                        "ui_dsl": {}, "resolved": [], "skipped": [], "skip_reasons": {}})

    temp_solution  = registry.solve(temp_solver_input)
    backend_issues = temp_solution.get("issues", [])

    issue_list = build_issue_list(issue_actions, backend_issues)
    resolver   = ConflictResolver(issues=issue_list, containers=dsl.get("containers", []))
    _, skipped_ids, skip_reasons = resolver.resolve()
    yard_swaps = resolver.get_yard_swaps()

    fixed_dsl    = apply_yard_swaps(dsl, yard_swaps)
    solver_input = convert_business_to_solver(fixed_dsl)
    solver_input["issue_statuses"] = issue_statuses

    unassignable2 = solver_input.get("unassignable_containers", [])
    if unassignable2:
        return jsonify({"status": "ok", "tasks": [], "makespan": 0,
                        "issues": _build_unassignable_issues(unassignable2),
                        "containers": dsl.get("containers", []), "solutions": [],
                        "ui_dsl": {}, "resolved": resolver.resolved_ids,
                        "skipped": skipped_ids, "skip_reasons": skip_reasons})

    def _is_size_limit(exc):
        msg = str(exc)
        return "Problem size limit exceeded" in msg or "size limit" in msg.lower()

    def _step4(si, reg, log):
        dec = DecomposerFactory.for_input(si)
        is_direct  = dec is None
        is_horizon = isinstance(dec, HorizonDecomposer)
        is_spatial = isinstance(dec, SpatialDecomposer)
        def _d():     return reg.solve(si)
        def _h():     return HorizonDecomposer(max_tasks_per_block=35).solve_sequentially(si, reg.solve)
        def _sp():    return SpatialDecomposer().solve_sequentially(si, reg.solve)
        try:
            if is_horizon: return _h()
            if is_spatial: return _sp()
            return _d()
        except Exception as exc:
            if not _is_size_limit(exc): raise
            if is_direct:
                try:    return _h()
                except Exception as e2:
                    if not _is_size_limit(e2): raise
                    return _sp()
            if is_horizon: return _sp()
            raise

    solver_output = _step4(solver_input, registry, logger)

    if solver_output.get("status") != "ok":
        solver_issues = solver_output.get("issues", [])
        if not any(i.get("type") == "SOLVE_FAILED" for i in solver_issues):
            solver_issues = solver_issues + [{"id": "solve_failed", "type": "SOLVE_FAILED",
                "severity": "CRITICAL", "title": _common_t("baseline.solveFailed.title"),
                "message": _common_t("baseline.solveFailed.message"),
                "relatedContainerIds": []}]
        return jsonify({"status": "ok", "tasks": [], "makespan": 0, "issues": solver_issues,
                        "containers": [], "solutions": [], "ui_dsl": {},
                        "resolved": [], "skipped": [], "skip_reasons": {}})

    ui_dsl    = convert_solver_to_ui(solver_output)
    solutions = sorted(solver_output.get("solutions", []), key=lambda s: (s["makespan"], s.get("penalty", 0)))
    for i, sol in enumerate(solutions):
        sol["name"] = f"Plan {chr(65 + i)}"

    primary_makespan = solutions[0]["makespan"] if solutions else 0
    ui_solutions     = ui_dsl.get("solutions", [])
    for i, ui_sol in enumerate(ui_solutions):
        if i < len(solutions):
            ui_sol["name"]     = solutions[i]["name"]
            ui_sol["makespan"] = solutions[i]["makespan"]
            ui_sol["penalty"]  = solutions[i].get("penalty", 0)

    primary_tasks = ui_solutions[0]["tasks"] if ui_solutions else []
    final_issues  = solver_output.get("issues", [])
    for issue in final_issues:
        iid = issue.get("id", "")
        issue["skipped"]     = iid in skipped_ids
        issue["skip_reason"] = skip_reasons.get(iid, "") if iid in skipped_ids else ""

    return jsonify({"status": "ok", "tasks": primary_tasks, "makespan": primary_makespan,
                    "issues": final_issues, "containers": solver_output.get("containers", []),
                    "solutions": ui_solutions, "ui_dsl": ui_dsl,
                    "resolved": resolver.resolved_ids, "skipped": skipped_ids, "skip_reasons": skip_reasons})


# ---------------------------------------------------------
# /relax
# ---------------------------------------------------------

def _dry_run_feasibility(patched_dsl: dict) -> dict:
    """
    パッチ適用後のDSLを実際に解いてみて、Feasibleになったかどうかを判定する。

    /baseline と全く同じルーティング（4DSL専用 → 汎用4DSL → Legacy →
    Dynamic → YardPlanningデフォルト）を再利用することで、判定ロジックの
    二重実装を避ける。各ルートは Flask の jsonify レスポンスを返す関数な
    ので、ここでは Flask リクエストコンテキスト内で直接呼び出し、
    `.get_json()` で中身を取り出して判定する。

    Returns: {"feasible": bool, "reason": str}
    """
    problem_class  = patched_dsl.get("problem_class") or patched_dsl.get("metadata", {}).get("problem_class", "")
    issue_statuses: dict = {}

    resp = _solve_4dsl_generic(problem_class, patched_dsl, issue_statuses)
    if resp is None:
        if problem_class in _LEGACY_SOLVERS:
            resp = _LEGACY_SOLVERS[problem_class](patched_dsl, issue_statuses)
        else:
            resp = _try_dynamic_solver(problem_class, patched_dsl, issue_statuses)
            if resp is None:
                resp = _solve_yard_planning_default(patched_dsl, {}, issue_statuses)

    # 各ルートは (jsonify(...), status_code) のタプルを返すことがある
    if isinstance(resp, tuple):
        resp = resp[0]

    result = resp.get_json(silent=True) if hasattr(resp, "get_json") else None
    if result is None:
        return {"feasible": False, "reason": "ドライラン結果を取得できませんでした（レスポンス形式異常）。"}

    if result.get("status") not in ("ok",):
        return {"feasible": False, "reason": f"ドライラン status={result.get('status')}: {result.get('message', '')}"}

    result_issues = result.get("issues", [])
    solve_failed  = [i for i in result_issues
                     if i.get("type") == "SOLVE_FAILED" or i.get("id") == "solve_failed"]
    if solve_failed:
        return {"feasible": False,
                "reason": solve_failed[0].get("message") or "パッチ適用後も Infeasible のままです（solve_failed）。"}

    return {"feasible": True, "reason": ""}


def _verify_relax_candidates(dsl: dict, candidates: list, progress_cb=None) -> tuple[list, list]:
    """
    LLMが生成した緩和候補を、実際にパッチ適用 → ドライラン再ソルブして検証する。

    設計方針（2026-07: Infeasible Tight Shift / Infeasible Crane Reach の
    両シナリオで、Infeasible判定後の緩和提案を適用してもFeasibleにならない
    問題への対処。診断コンテキスト自体は正しく伝播していることを確認済みで、
    残る根本対策として「LLMの計算結果を信用せず、返す前にサーバー側で
    再ソルブして裏取りする」検証ゲートを追加する）。

    - value: null を含む候補（人間の入力待ち＝スライダー入力後でないと
      具体的な値が定まらない）は自動検証できないため対象外とし、
      verified=None のまま提示する。
    - 既に patch_warnings（存在しないDSLパスへの操作）が付いている候補は
      パッチ適用自体が無効であることが分かっているため、検証するまでもなく
      除外する。
    - 上記以外は実際に dsl_patch を適用し、_dry_run_feasibility() で
      再ソルブする。Infeasibleのままだった候補は「解決策」として提示しない
      （ユーザーに解けないパッチを勧めてしまう問題の直接対策）。

    progress_cb(index, total, title, verified) が渡された場合、候補を1件
    処理し終えるたびに呼び出す（index は1始まり）。候補ごとに実ソルブが
    走るため、画面が止まって見える問題（2026-07-14: プログレッシブな
    進捗表示の要望）に対応するためのフック。呼び出し元は例外を投げても
    ここでは無視し、検証処理本体を止めない。

    Returns: (提示する候補リスト, 除外した候補の記録リスト)
    """
    verified: list = []
    dropped:  list = []
    total = len(candidates)

    def _report(index: int, title: str, is_verified) -> None:
        if progress_cb is None:
            return
        try:
            progress_cb(index, total, title, is_verified)
        except Exception:
            pass

    for idx, cand in enumerate(candidates, start=1):
        patch_ops = cand.get("dsl_patch", [])
        title     = cand.get("title", "(タイトルなし)")

        if not patch_ops:
            cand["verified"] = None
            cand["verification_note"] = "dsl_patch が空のため検証対象外です。"
            verified.append(cand)
            _report(idx, title, None)
            continue

        needs_human_input = any(op.get("value") is None for op in patch_ops)
        if needs_human_input:
            cand["verified"] = None
            cand["verification_note"] = "人間の入力待ちの値を含むため自動検証をスキップしました（値確定後に再検証してください）。"
            verified.append(cand)
            _report(idx, title, None)
            continue

        if cand.get("patch_warnings"):
            logger.warning(f"[relax_verify] 除外（不正パス）: title={title} warnings={cand['patch_warnings']}")
            dropped.append({"title": title, "reason": f"不正なDSLパス: {cand['patch_warnings']}"})
            _report(idx, title, False)
            continue

        try:
            patched_dsl = jsonpatch.JsonPatch(patch_ops).apply(copy.deepcopy(dsl))
        except Exception as e:
            logger.warning(f"[relax_verify] 除外（パッチ適用失敗）: title={title} error={e}")
            dropped.append({"title": title, "reason": f"パッチ適用失敗: {e}"})
            _report(idx, title, False)
            continue

        try:
            result = _dry_run_feasibility(patched_dsl)
        except Exception as e:
            # ドライラン自体が例外で落ちた場合は「検証不能」扱いとし、
            # 有望かもしれない候補を誤って握りつぶさないよう提示は残す。
            logger.error(f"[relax_verify] 検証エラー（未検証として提示）: title={title} error={e}", exc_info=True)
            cand["verified"] = None
            cand["verification_note"] = f"検証中にエラーが発生したため未検証として提示します: {e}"
            verified.append(cand)
            _report(idx, title, None)
            continue

        if result["feasible"]:
            cand["verified"] = True
            cand["verification_note"] = "サーバー側で再ソルブし、Feasibleになることを確認済みです。"
            verified.append(cand)
            _report(idx, title, True)
        else:
            logger.info(f"[relax_verify] 除外（依然Infeasible）: title={title} reason={result['reason']}")
            dropped.append({"title": title, "reason": result["reason"]})
            _report(idx, title, False)

    return verified, dropped


@bp.route("/relax", methods=["POST"])
def relax():
    try:
        payload = request.json
        dsl, issues = payload.get("dsl", {}), payload.get("issues", [])
        if not dsl:
            return jsonify({"status": "error", "message": _common_t("baseline.dslEmpty")}), 400
        raw_candidates = suggest_relaxations(dsl, issues, lang=get_lang())
        candidates, dropped_candidates = _verify_relax_candidates(dsl, raw_candidates)
        return jsonify({"status": "ok", "candidates": candidates, "dropped_candidates": dropped_candidates})
    except Exception as e:
        logger.error(f"/relax Error: {str(e)}", exc_info=True)
        return jsonify({"status": "error", "message": str(e)}), 500


# ---------------------------------------------------------
# /relax/start, /relax/status  (2026-07-14: 非同期ジョブ化)
#
# 緩和案生成(LLM呼び出し)と、候補ごとのドライラン再ソルブ検証
# (_verify_relax_candidates)はどちらも時間がかかり得る。特に検証は
# 候補数だけ実ソルブが走るため、画面が固まって見える（進んでいるのか
# 分からずストレスになる）という指摘を受け、/api/domain/run と同じ
# job_id + ポーリングの仕組みをそのまま流用する。
#
# 上の同期版 /relax はフロントの他の利用箇所や後方互換のために残す
# （ロジックは _verify_relax_candidates 等を共有しているため、
# 二重実装にはなっていない）。
# ---------------------------------------------------------

def _run_relax_job(job_id: str, dsl: dict, issues: list, lang: str = "ja") -> None:
    """
    /relax/start の実体。バックグラウンドスレッドで実行し、
    進捗を job_set() に逐次書き込む。/relax/status/<job_id> でポーリングする。

    stage の推移:
      queued → generating（LLMが緩和案を検討中） → verifying（候補ごとに
      ドライラン再ソルブして検証中。total_candidates/verified_index/
      current_title が併記される） → done
      任意の段階で error になり得る。

    _dry_run_feasibility() 等が内部で jsonify() を呼ぶため、Flaskの
    リクエストコンテキスト外で動くこのバックグラウンドスレッドでは
    明示的に app_context() を張る必要がある。

    2026-07-30 i18n対応: contextvars（i18n.context）は新規スレッドに自動継承
    されない（Pythonの仕様。threading.Threadは親スレッドのcontextを引き継がず
    デフォルト値から始まる）。そのため、呼び出し元のrelax_start()（Flask
    リクエストスレッド側、before_requestでset_lang済み）でget_lang()した値を
    langとして明示的に引数で受け取り、このスレッドの先頭で改めてset_lang()
    する。以降このスレッド内で呼ばれるsuggest_relaxations()・
    _verify_relax_candidates()内の_dry_run_feasibility()（issue_rules.py/
    {domain}_ui_converter.py経由でt()を呼ぶ）は、このスレッドローカルな
    contextvarを正しく参照できる。
    """
    with app.app_context():
        set_lang(lang)
        try:
            _job_set(job_id, stage="generating")
            raw_candidates = suggest_relaxations(dsl, issues, lang=lang)

            _job_set(job_id, stage="verifying",
                      total_candidates=len(raw_candidates), verified_index=0, current_title=None)

            def _on_progress(index: int, total: int, title: str, is_verified) -> None:
                _job_set(job_id, stage="verifying", total_candidates=total,
                          verified_index=index, current_title=title)

            candidates, dropped_candidates = _verify_relax_candidates(
                dsl, raw_candidates, progress_cb=_on_progress)

            _job_set(job_id, stage="done",
                      result={"candidates": candidates, "dropped_candidates": dropped_candidates})
        except Exception as e:
            logger.error(f"[relax_job:{job_id}] エラー: {e}", exc_info=True)
            _job_set(job_id, stage="error", error=str(e))


@bp.route("/relax/start", methods=["POST"])
def relax_start():
    """
    /relax の非同期版。即座に job_id を返し、実体はバックグラウンドスレッドで
    実行する。進捗は /relax/status/<job_id> でポーリングする。
    """
    try:
        payload = request.json
        dsl, issues = payload.get("dsl", {}), payload.get("issues", [])
        if not dsl:
            return jsonify({"status": "error", "message": _common_t("baseline.dslEmpty")}), 400

        _job_gc()
        job_id = uuid.uuid4().hex
        _job_set(job_id, stage="queued")

        # 2026-07-30 i18n対応: バックグラウンドスレッドにはcontextvarsが自動
        # 継承されないため、このリクエストスレッドで確定しているget_lang()の
        # 値を明示的にスレッド引数として渡す（_run_relax_job参照）。
        thread = threading.Thread(
            target=_run_relax_job,
            args=(job_id, dsl, issues, get_lang()),
            daemon=True,
        )
        thread.start()

        return jsonify({"status": "started", "job_id": job_id})

    except Exception as e:
        logger.error(f"/relax/start error: {e}", exc_info=True)
        return jsonify({"status": "error", "message": str(e)}), 500


@bp.route("/relax/status/<job_id>", methods=["GET"])
def relax_status(job_id: str):
    """
    /relax/start が返した job_id の進捗をポーリングする。
    stage: queued → generating → verifying → done / error
    """
    job = _job_get(job_id)
    if job is None:
        return jsonify({"status": "error", "message": "job_id が見つかりません（破棄済みまたは無効な job_id です）"}), 404
    return jsonify({"status": "ok", "job": job})


@bp.route("/checks/status/<job_id>", methods=["GET"])
def checks_status(job_id: str):
    """
    /baseline のレスポンスに async_check_job_id が含まれていた場合の
    ポーリング先（DESIGN_2026-07-21 3-3節: O(n²)以上のチェックが
    インスタンス規模の閾値を超えて非同期に回された場合のみ発生。
    通常のsolveでは async_check_job_id 自体が返らないため、この
    エンドポイントを叩く必要はない）。

    job.stage: running → done（job.issues に検出結果が入る） / error
    """
    job = _job_get(job_id)
    if job is None:
        return jsonify({"status": "error", "message": "job_id が見つかりません（破棄済みまたは無効な job_id です）"}), 404
    return jsonify({"status": "ok", "job": job})
