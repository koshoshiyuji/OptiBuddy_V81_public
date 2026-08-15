"""
Backend/app.py  (V8.6)

V8.4 → V8.5:
  - CapacitatedVehicleRoutingProblem を4DSL準拠ルートで追加
    _solve_capacitated_vehicle_routing_problem() を新設。
    _try_dynamic_solver より前に明示的ルーティング。
  - business_to_solver.py v6.1 に合わせ CVRP の変換を
    convert_cvrp_to_solver → solver → convert_cvrp_to_ui の順で処理。
  - 新規4DSLドメインはここのルーティングに1行追加するパターンで拡張。

V8.5 → V8.6:
  - /api/domain/run に人間確認ゲート（Q&Aゲート）を追加。
    Stage1a分類の missing_info、または新規ドメイン生成コードの
    サニタイザー警告（scan_diffs_for_warnings）が1件でもあれば、
    applyせず status="needs_confirmation" で人間の回答を待つ。
    何もなければ従来通り自動でapplyまで進む。
  - /api/domain/confirm を新設。人間が回答した後に呼ばれ、
    pending（diffs/patches/scenario_registrations または scenarios）を
    apply_domain_files/apply_scenarios に渡して確定させる。
    Q&Aの内容は dsl_evolution_log に change_type="human_confirmed" で監査記録。
  - /api/domain/run を非同期ジョブ方式に変更。即座に job_id を返し、
    実体（Stage1/Stage2/apply）はバックグラウンドスレッドで実行。
    フロントは /api/domain/run/status/<job_id> をポーリングする。
    接続が切れてもサーバー側の生成処理は続行し、ファイル書き込みは
    全工程終了後の最後の1ステップでしか行われないので、
    「一部のファイルだけ書き込まれて不整合」という状態は起きない。
"""


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
import importlib
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

app = Flask(__name__)
CORS(app)
# 2026-07-23追記: Flaskのjsonify()はデフォルトでensure_ascii=Trueのため、日本語を
# 含むJSONレスポンス（特に💾エクスポートでダウンロードされるシナリオJSON）が
# 全て"\uXXXX"エスケープになり、テキストエディタ等で開くと読めない状態だった
# （Koshoshiの指摘）。アプリ全体のjsonify()出力をUTF-8のまま返すよう変更する。
app.json.ensure_ascii = False

# 2026-07-30 i18n対応: Frontendが現在の表示言語をクエリパラメータ ?lang=en|ja で
# 送ってくる想定。リクエスト先頭でcontextvarsにセットし、以降の処理
# （issue_rules.py / {domain}_ui_converter.py / solver.py 等）はget_lang()経由で
# 参照する（引数のバケツリレーをしない）。
# DESIGN_2026-07-29_nurse_shift_i18n_implementation_spec.md 3節参照。
@app.before_request
def _set_request_language():
    set_lang(request.args.get("lang"))


# 2026-08-04追記: 従来はlogging.basicConfig(level=logging.INFO)のみでログが
# コンソール（stdout）にしか出ておらず、[llm_usage]等のキャッシュ効き具合の
# ログを後から抽出しようとすると毎回コンソール出力を手でコピペする必要があった
# （Koshoshiの指摘: 「2の意味がわからない、何か抽出するならツール作ってください」）。
# tools/llm_usage_report.py で機械的に集計できるよう、コンソール出力はそのまま
# 維持しつつ logs/backend.log にも同時出力するFileHandlerを追加した。
_LOG_DIR = Path(__file__).parent / "logs"
_LOG_DIR.mkdir(exist_ok=True)
logging.basicConfig(
    level=logging.INFO,
    handlers=[
        logging.StreamHandler(),
        logging.FileHandler(_LOG_DIR / "backend.log", encoding="utf-8"),
    ],
)
logger = logging.getLogger(__name__)

registry = SolverRegistry()
logger.info(f"SolverRegistry 初期化完了: {registry}")

_PROJECT_ROOT = Path(__file__).parent.parent
_BACKEND_ROOT = Path(__file__).parent


# ---------------------------------------------------------
# ドメイン自動生成ジョブストア（V8.6）
#
# 意図:
#   Stage2（LLMコード生成）は数分かかることがあり、その間にブラウザ/接続が
#   切れてもサーバー側の生成処理自体は続行させたい。そのため、
#   /api/domain/run は即座に job_id を返してバックグラウンドスレッドで実行し、
#   フロントは /api/domain/run/status/<job_id> をポーリングする。
#
#   重要: ファイル書き込み（apply_domain_files/apply_scenarios）は
#   、、Stage1/Stage2が完全に終了した後の最後の1ステップでしか呼ばれない。
#   接続が切れてもサーバーはこのジョブを最後まで完走させるので、
#   「一部のファイルだけ書き込まれて不整合」という状態は起きない。
#   フロントがリロードされてjob_idを失っても、サーバー側は完走または失敗して
#   ジョブはメモリ上に残るだけで、ドメイン登録自体は壊れない。
#   やり直したい場合は新しいjob_idで再度 /api/domain/run を呼べばよい
#   （同じdomain_nameでの二重登録は check_domain_exists がブロックする）。
# ---------------------------------------------------------

_DOMAIN_JOBS_LOCK = threading.Lock()
_DOMAIN_JOB_TTL_SEC = 3600  # 1時間以上前の終了済みジョブは新規作成時に掃除

# ジョブ状態はDB（domain_jobsテーブル、DslRepository経由）に永続化する。
# 以前はメモリ上のdictだったため、バックエンド再起動やタブを閉じると
# needs_confirmation待ちのpendingが失われ「確認待ちの間だけ1プロセスの
# 一部のように振る舞う」問題があった。呼び出し側のインターフェース
# （job_set(job_id, **fields) / job_get(job_id) -> dict|None）は変えていないので、
# _run_domain_job / _run_confirm_job / 各ルートのロジックは無改修で動く。


def _job_set(job_id: str, **fields) -> None:
    with _DOMAIN_JOBS_LOCK:
        DslRepository().job_set(job_id, **fields)


def _job_get(job_id: str) -> dict | None:
    return DslRepository().job_get(job_id)


def _job_gc() -> None:
    with _DOMAIN_JOBS_LOCK:
        removed = DslRepository().job_gc(_DOMAIN_JOB_TTL_SEC)
        if removed:
            logger.info(f"[job_gc] 古いジョブを{removed}件削除しました")


def _run_domain_job(job_id: str, domain_name: str, hearing_texts: list, overrides,
                     base_domain_override: str | None = None, force_new_domain: bool = False) -> None:
    """/api/domain/run のスレッドターゲット。実体は_run_hearing_pipeline()。"""
    _run_hearing_pipeline(job_id, domain_name, hearing_texts, overrides,
                          base_domain_override=base_domain_override, force_new_domain=force_new_domain)


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


def _run_hearing_pipeline(job_id: str, domain_name: str, hearing_texts: list, overrides,
                           base_domain_override: str | None = None, force_new_domain: bool = False) -> None:
    """
    /api/domain/run の実体。バックグラウンドスレッドで実行され、
    進捗を _DOMAIN_JOBS[job_id] に逐次書き込む。
    この関数自体はフロントの接続状態と完全に切り離されて動作する。

    2026-07-16修正（デバッグチャット構想 第一歩）: 従来 _run_domain_job という
    1つの関数だったものを、名前を変えてここに独立させた。理由は
    _run_confirm_job から「人間の回答を取り込んだ上でこの関数をもう一度
    最初から呼び直す」ためにこの関数を再利用する必要があるため
    （DESIGN_2026-07-14 5節の「hearing_texts追記→再投入」規約を、手動運用から
    自動ループに格上げした。新しい仕組みを追加したのではなく、既存の
    再投入手順をコード化しただけ）。

    base_domain_override / force_new_domain: Stage1aの自動分類（LLM、temperature=1.0で
    ヒアリング文の言い回しに揺れやすい）をユーザーが明示的に上書きしたい場合に使う。
    詳細は interpret_hearing() のdocstringを参照。
    """
    try:
        from domain_generator import (
            interpret_hearing, check_domain_exists, generate_domain_files,
        )

        _job_set(job_id, stage="interpreting", domain_name=domain_name)
        logger.info(f"[domain_job:{job_id}] Stage1 開始: {domain_name}")

        def _on_progress(stage: str) -> None:
            _job_set(job_id, stage=stage)

        s1               = interpret_hearing(domain_name, hearing_texts, on_progress=_on_progress,
                                              base_domain_override=base_domain_override,
                                              force_new_domain=force_new_domain)
        match_type       = s1.get("match_type", "new_domain")
        base_domain      = s1.get("base_domain")
        classification   = s1.get("classification", {})
        scenarios        = s1.get("scenarios")
        scenario_regs    = s1.get("scenario_registrations", [])
        extension_gaps   = s1.get("extension_gaps", []) or []
        missing_info     = classification.get("missing_info", []) or []
        # 2026-08-02追加: family_reference lookup（Stage1a.5b）の結果。new_domainの場合のみ
        # domain_defに乗せてStage2プロンプトに「参考情報」として渡す（詳細はdomain_generator.py
        # のlookup_family_reference()docstring、および
        # OptiBuddy_V81_devnotes/DESIGN_2026-08-02_family_structural_reference.md参照）。
        family_reference = s1.get("family_reference")
        # 2026-08-03追加: formulation_directive（Stage1a.5c、執筆型）。ヒアリング担当者
        # （本エージェント）がヒアリングテキストに明示的に埋め込んだ場合のみ存在する。
        # 詳細はdomain_generator.pyの_extract_formulation_directive()docstring、
        # OptiBuddy_V81_devnotes/DESIGN_2026-08-02_family_structural_reference.md参照。
        formulation_directive = s1.get("formulation_directive")
        # 2026-08-05追加: structural_requirements（Stage1a.5a）。ヒアリングテキストのみから
        # 機械的に抽出した業務構造チェックリスト。CSPLib一致の有無に関わらずnew_domainなら
        # 常に付与されうる。詳細はdomain_generator.pyのderive_structural_requirements()
        # docstring、ENGINEERING_LOG.md 2026-08-05追記参照。
        structural_requirements = s1.get("structural_requirements")

        _job_set(job_id, match_type=match_type, base_domain=base_domain, classification=classification)
        logger.info(f"[domain_job:{job_id}] match_type={match_type}, base_domain={base_domain}, "
                    f"missing_info={len(missing_info)}件, extension_gaps={len(extension_gaps)}件")

        if match_type in ("existing_domain", "base_problem") and scenarios:
            check = check_domain_exists(domain_name)
            scenario_conflicts = [c for c in check["conflicts"] if "scenarios" in c]
            if scenario_conflicts:
                _job_set(job_id, stage="error",
                    error="同名のシナリオファイルが既に存在します。別の業務名を使用してください。",
                    conflicts=scenario_conflicts)
                return

            gap_questions = [
                f"（拡張差分検出）既存の{base_domain}実装では対応できない可能性がある要件: "
                f"{g.get('description','')}（該当箇所: {g.get('hearing_evidence','')}）"
                for g in extension_gaps
            ]
            all_questions = list(missing_info) + gap_questions
            if all_questions:
                _job_set(job_id, stage="needs_confirmation", questions=all_questions,
                    pending={"scenarios": scenarios, "scenario_registrations": scenario_regs,
                             "extension_gaps": extension_gaps, "base_domain": base_domain,
                             "hearing_texts": hearing_texts})
                logger.info(f"[domain_job:{job_id}] 人間確認待ち: missing_info={len(missing_info)}件, "
                            f"extension_gaps={len(extension_gaps)}件")
                return

            from domain_generator import apply_scenarios
            _job_set(job_id, stage="applying")
            result = apply_scenarios(scenarios, domain_name, scenario_regs)
            _job_set(job_id, stage="done", result=result)
            logger.info(f"[domain_job:{job_id}] 完了（既存/基底ルート）")
            return

        check = check_domain_exists(domain_name)
        if check["exists"]:
            _job_set(job_id, stage="error",
                error=f"ドメイン '{domain_name}' は既に登録されています。別の名前を使用してください。",
                conflicts=check["conflicts"])
            return

        domain_def = {
            "domain_name": domain_name,
            "snake_name":  _to_snake(domain_name),
            "is_dsl4":     classification.get("is_dsl4_candidate", True),
            **{k: v for k, v in classification.items() if k not in ("domain_name", "snake_name")},
        }
        if structural_requirements:
            domain_def["structural_requirements"] = structural_requirements
            logger.info(
                f"[domain_job:{job_id}] structural_requirements: "
                f"entities={len(structural_requirements.get('entities') or [])}件, "
                f"hard_rules={len(structural_requirements.get('hard_rules') or [])}件 "
                "をStage2プロンプトに必須順守セクションとして付与"
            )
        if family_reference:
            domain_def["family_reference"] = family_reference
            logger.info(
                f"[domain_job:{job_id}] family_reference: family_id={family_reference.get('family_id')}, "
                f"兄弟{len(family_reference.get('siblings', []))}件をStage2プロンプトに付与"
            )
        if formulation_directive:
            domain_def["formulation_directive"] = formulation_directive
            logger.info(
                f"[domain_job:{job_id}] formulation_directive: "
                f"recommended_technology={formulation_directive.get('recommended_technology')} "
                "をStage2プロンプトに必須指示として付与"
            )

        _job_set(job_id, stage="generating")
        logger.info(f"[domain_job:{job_id}] Stage2 コード生成開始（数分かかる場合あり）")
        s2 = generate_domain_files(domain_def, overrides)

        # 2026-08-05追加: Stage2出力がmax_tokens上限で打ち切られると、SCENARIOSブロック
        # （baseline/infeasible JSON・ファイル登録用パッチ）が生成されないまま
        # scenario_registrations=[]で返ってくる（PatientTransportPlanner再登録実機テストで
        # 実際に発生。output=max_tokensちょうどで打ち切られていた。詳細はENGINEERING_LOG.md
        # 2026-08-05追記12参照）。このまま進めると、converter/solver等のファイルだけ
        # ディスクに書き込まれ、シナリオ未登録のまま「完了」扱いになる静かな不具合になる。
        # 「絶対に嘘の完了と言わない」方針（このファイルの既存コメント参照）に従い、
        # ここで明示的にエラーとして扱う。
        if not s2.get("scenario_registrations"):
            _job_set(job_id, stage="error",
                error=(
                    "Stage2のコード生成結果にシナリオ登録データが含まれていませんでした"
                    "（LLM出力がmax_tokens上限で打ち切られた可能性があります）。"
                    "生成されたファイルはディスクに残っていますが、ドメイン登録は完了していません。"
                    "再実行するか、ヒアリング内容を簡潔にすることを検討してください。"
                ))
            logger.error(
                f"[domain_job:{job_id}] scenario_registrations が空のため中断"
                "（Stage2出力が途中で打ち切られた可能性）"
            )
            return

        # 2026-07-16 再設計（デバッグチャット構想）: Gate2チェック一式は
        # domain_generator.run_gate2_checks() に集約した。指摘が出た場合、
        # write_files_for_dynamic_check()が書き出したdraftファイルは削除せず
        # ディスクに残す（Claudeが直接編集して直すため）。指摘が0件になるまで
        # Stage1a/Stage2を再実行することはない（_run_confirm_job側でGate2だけを
        # 再検証するループになる。詳細はrun_gate2_checks()のdocstring参照）。
        from domain_generator import run_gate2_checks
        snake_for_check = domain_def.get("snake_name") or _to_snake(domain_name)
        # 2026-08-07追加: Stage2の時間内訳を①LLM生成本体（stage="generating"、
        # 上のgenerate_domain_files()呼び出しで既に記録済み）／②Gate2静的チェック／
        # ③Gate2動的検証（実ソルブ）等で分離できるよう、on_stageでjob_setに接続する。
        # 既存のdomain_job_timing_log（stage_timeline自動記録）にそのまま乗る。
        gate2_result = run_gate2_checks(
            s2["diffs"], snake_for_check, domain_name, hearing_texts,
            on_stage=lambda s: _job_set(job_id, stage=s),
        )

        # 2026-07-19: gate2_result["questions"]（blocking_questions+advisory_questions）を
        # そのまま使わず、blocking_questionsのみをall_questionsに含める。advisory_questions
        # （静的field-checkのみに基づく、誤検知が多いと判明済みの指摘）は
        # 「advisoryしか残っていなければ人間に判断を求めずに進めてよい」という
        # run_gate2_checks側の既存方針をこの初回確認画面でも一貫させるための変更
        # （_advance_after_agent_round側は既に同様の対応済み。この初回確認画面だけ
        # 直っていなかった）。ログには件数を残す。
        if gate2_result["advisory_questions"]:
            logger.info(
                f"[domain_job:{job_id}] 静的チェックの参考情報{len(gate2_result['advisory_questions'])}件は"
                "誤検知が多いと判明済みのためall_questionsに含めません: "
                f"{gate2_result['advisory_questions']}"
            )
        all_questions = list(missing_info) + gate2_result["blocking_questions"]
        if all_questions:
            _job_set(job_id, stage="needs_confirmation", questions=all_questions,
                pending={"diffs": s2["diffs"], "patches": s2["patches"],
                         "scenario_registrations": s2["scenario_registrations"],
                         "hearing_texts": hearing_texts,
                         "snake": snake_for_check,
                         # 2026-07-16: ディスクに残っているdraftファイルのパス。
                         # Claudeが直接編集して直した後、続行時にここから
                         # refresh_diffs_from_disk()で最新内容を読み直す。
                         "written_paths": gate2_result["written_paths"],
                         # 2026-08-11追加: advisory_questions（画面には出さないが
                         # 「記録には残す」方針の対象）をpendingに保持しておく。
                         # これが無いと、この後force_apply等で再検証をスキップして
                         # 登録した場合にadvisory指摘が最終結果（gate2_warnings）
                         # から完全に消えてしまう既知の欠落があった
                         # （ProductionLineSequencing登録、2026-08-11に発覚）。
                         "advisory_questions": gate2_result["advisory_questions"],
                         # 2026-08-09追加: debug_agentラウンド後の再検証で
                         # hearing_dsl_gapsの差分渡し版を使えるよう、今回の
                         # カバレッジ判定結果を保持しておく（_advance_after_agent_round
                         # がprior_coverageとして渡す）。
                         "gate2_coverage": gate2_result.get("coverage")})
            logger.info(f"[domain_job:{job_id}] 人間確認待ち: missing_info={len(missing_info)}件, "
                        f"blocking_questions={len(gate2_result['blocking_questions'])}件")
            return

        # all_questions は空（missing_info・blocking_questions ともに0件）だが、
        # advisory_questionsだけ残っている場合がありうる（advisoryしか残っていなければ
        # 人間に判断を求めずに進めてよい、という既存方針通り）。「記録には残す」方針の
        # 通り、確認画面は出さずに登録した上で、完了画面のgate2_warningsとして
        # 残す（隠さない。「絶対に嘘の完了と言わない」方針）。
        # 動的検証用に書き出したdraftファイルはapply_domain_filesが同じ内容を
        # 正式に再度書き込むため、write_files_for_dynamic_checkが書いたものを
        # そのまま使い回して問題ない（idempotent）。
        from domain_generator import apply_domain_files
        _job_set(job_id, stage="applying")
        logger.info(f"[domain_job:{job_id}] Apply 開始")
        result = apply_domain_files(s2["diffs"], s2["patches"], s2["scenario_registrations"])
        result["gate2_warnings"] = gate2_result["advisory_questions"]
        _job_set(job_id, stage="done", result=result)
        logger.info(f"[domain_job:{job_id}] 完了（新規ドメイン）")

    except Exception as e:
        logger.error(f"[domain_job:{job_id}] エラー: {e}", exc_info=True)
        _job_set(job_id, stage="error", error=str(e))


def _advance_after_agent_round(job_id: str, domain_name: str, pending: dict, agent_result: dict,
                                hearing_texts_for_check: list, snake: str,
                                gate2_warnings: list) -> dict | None:
    """
    2026-07-17再設計: debug_agentの1セッション（ask_humanでの対話を何度挟んでも
    「1ラウンド」として扱う）が終わった直後の分岐を、confirm_jobとagent_answer_job
    の両方から共通で呼べるように切り出したもの。

    背景: 以前は「指摘が残っていたら人間の回答を経てもう一度エージェントを
    最初から回す」設計だったが、実機テストで(1)Gate2の指摘が収斂せず
    ラウンドを重ねるほど増えることがある、(2)ラウンドごとにStage2相当の
    処理を待つのは時間がかかりすぎる、という2点が問題になった。
    そこでdebug_agent側にask_humanツールを追加し、業務判断が必要な曖昧さは
    セッションを終えずにその場で聞けるようにした。これに伴い、ここでは
    エージェントの1セッションが終わった後、自動でもう一度新しいラウンドを
    始めることはしない。まだ問題が残っていれば、取りやめ／このまま登録する
    の二択に絞って人間に委ねる。

    戻り値:
      - None: このジョブはここでは完了しない（人間の質問待ち、または
        取りやめ／このまま登録するの確認待ちにした）。呼び出し元はそのままreturnする。
      - dict: apply_domain_files() の結果。呼び出し元はこれをresultとして
        _finalize_confirm_job() に渡して完了処理を行う。
    """
    if agent_result["stopped_reason"] == "waiting_for_human":
        _job_set(job_id, stage="waiting_for_agent_question",
                 pending_question=agent_result["pending_question"],
                 pending={**pending,
                          "agent_resume_state": agent_result["resume_state"],
                          "hearing_texts": hearing_texts_for_check,
                          "snake": snake})
        logger.info(f"[confirm_job:{job_id}] エージェントが人間に質問: "
                    f"{agent_result['pending_question']}")
        return None

    if agent_result.get("fixed_summary"):
        logger.info(f"[confirm_job:{job_id}] エージェント対応内容: {agent_result['fixed_summary']}")
    logger.info(
        f"[confirm_job:{job_id}] デバッグエージェント終了: "
        f"reason={agent_result['stopped_reason']}, turns={agent_result['turns_used']}, "
        f"actions={len(agent_result['actions'])}件, "
        f"needs_human_decision={len(agent_result['needs_human_decision'])}件"
    )

    from domain_generator import refresh_diffs_from_disk, run_gate2_checks, apply_domain_files

    written_paths = pending.get("written_paths", [])
    diffs = refresh_diffs_from_disk(pending.get("diffs", []), written_paths)

    _job_set(job_id, stage="verifying")
    logger.info(f"[confirm_job:{job_id}] draftファイルの最新内容でGate2を再検証")
    # 2026-08-07追加: 上のnew_domain経路と同じon_stage計装（②静的／③動的検証等の分離）。
    # 2026-08-09追加: pendingに前回のカバレッジ判定結果（gate2_coverage）と
    # debug_agent実行前のdiffsが残っていれば、hearing_dsl_gapsの差分渡し版
    # （detect_hearing_dsl_gaps_incremental）を使う。単体検証で所要時間88%短縮・
    # 精度は全文再チェックと実質一致を確認済み（ENGINEERING_LOG.md 2026-08-09追記2）。
    gate2_result = run_gate2_checks(
        diffs, snake, domain_name, hearing_texts_for_check,
        on_stage=lambda s: _job_set(job_id, stage=s),
        prior_coverage=pending.get("gate2_coverage"),
        prior_diffs=pending.get("diffs"),
    )

    needs_human_gate = bool(gate2_result["blocking_questions"]) or bool(agent_result["needs_human_decision"])

    if needs_human_gate:
        # 2026-07-19: 以前は「（人間の判断が必要）」という接頭辞だったが、
        # この画面を見るのは常に人間（「AIの判断」と対比する意味での「人間」）
        # なので、あえて「人間」と言う意味が無いとKoshoshiより指摘。加えて、
        # ここは取りやめ／このまま登録するの二択しか提示しない画面であり、
        # 「判断してください」という言い方は個別に回答できるかのような
        # 誤解を招く。「未解決のまま残っている」という事実の表示にとどめる。
        human_decision_questions = [
            f"（未解決）{item}" for item in agent_result["needs_human_decision"]
        ]
        fixed_summary_note = (
            [f"（AIエージェント: ここまでの対応）{agent_result['fixed_summary']}"]
            if agent_result["fixed_summary"] else []
        )
        # 2026-07-19: advisory_questions（静的field-checkのみに基づく指摘。誤検知が
        # 多いと判明済み——run_gate2_checks側のコメント参照）は、ここでは
        # known_issuesに含めない。以前はblocking_questionsと一緒に混ぜて人間に
        # 提示していたため、「4件のうち3件は誤検知」という状態を毎回人間が
        # 自分でコードとシナリオJSONを突き合わせて切り分ける羽目になっていた
        # （2026-07-18実機で発生、ENGINEERING_LOG.md該当日エントリ参照）。
        # advisory_questionsは記録のためログには残すが、対応要否の判断対象
        # （known_issues＝画面表示・debug_agentへの再入力）からは外す。
        if gate2_result["advisory_questions"]:
            logger.info(
                f"[confirm_job:{job_id}] blocking残存のため人間確認に入りますが、"
                f"静的チェックの参考情報{len(gate2_result['advisory_questions'])}件は"
                "誤検知が多いと判明済みのためknown_issuesに含めません: "
                f"{gate2_result['advisory_questions']}"
            )
        known_issues = human_decision_questions + fixed_summary_note + gate2_result["blocking_questions"]
        _job_set(job_id, stage="needs_confirmation", loop_exhausted=True,
            questions=known_issues,
            pending={"diffs": diffs, "patches": pending.get("patches", []),
                     "scenario_registrations": pending.get("scenario_registrations", []),
                     "hearing_texts": hearing_texts_for_check,
                     "snake": snake, "written_paths": gate2_result["written_paths"],
                     # 2026-08-11追加: _run_domain_job側と同じ理由でadvisory_questionsを
                     # 引き継ぐ（force_apply時にgate2_warningsから消えないように）。
                     "advisory_questions": gate2_result["advisory_questions"],
                     # 2026-08-09追加: さらに次のラウンドがあった場合に備え、
                     # 今回のカバレッジ判定結果を引き継ぐ。
                     "gate2_coverage": gate2_result.get("coverage")})
        logger.info(f"[confirm_job:{job_id}] 1ラウンド終了。blocking="
                    f"{len(gate2_result['blocking_questions'])}件、human_decision="
                    f"{len(human_decision_questions)}件残存 → "
                    "取りやめ／このまま登録するの二択で確認待ち（追加のエージェントラウンドは行わない）")
        return None

    if gate2_result["advisory_questions"]:
        logger.info(
            f"[confirm_job:{job_id}] 静的チェックの指摘が{len(gate2_result['advisory_questions'])}件"
            "残っていますが、実行検証（動的）は問題なく、エージェントも人間判断不要と結論したため、"
            "人間には確認を求めず登録を進めます（内容はgate2_warningsとして記録）。"
        )
        gate2_warnings.extend(gate2_result["advisory_questions"])
    logger.info(f"[confirm_job:{job_id}] 再検証クリア。正式に登録します。")
    return apply_domain_files(diffs, pending.get("patches", []), pending.get("scenario_registrations", []))


def _finalize_confirm_job(job_id: str, domain_name: str, questions: list, answers: str, result: dict,
                           gate2_warnings: list, extensions_applied: dict | None,
                           audit_problem_class: str, hearing_texts: list | None = None) -> None:
    """_run_confirm_job / _run_agent_answer_job 共通の末尾処理（監査ログ記録＋done確定）。

    2026-07-17修正: 以前はresultの成否に関わらず無条件に_ensure_dsl_definition_id()を
    呼んでいたため、apply_domain_files()がtsc型検証失敗でロールバックした場合でも
    dsl_definitionsに「登録されていないのに存在する」幽霊行が作られてしまっていた
    （実機で発生。過去にStage1a分類を誤らせた「幽霊ドメイン」バグと同種の原因）。
    実際に登録が成功した場合（status in ok/partial）のみ監査ログ・DSL定義行を作る。

    2026-08-10追加（#31設計3-2の最小実装）: gate2_warningsを残したまま登録した
    場合（force_apply等）、dsl_evolution_logのdsl_patchにhearing_textsと残存指摘を
    保存しておく。run_post_registration_fix()は従来hearing_textsを意図的に空リスト
    固定で渡していた（DESIGN_2026-08-09_post_registration_bugfix_flow.md 3-2で
    指摘の「文脈の再構築コストが高い」問題）が、ここに保存しておけば
    /api/domain/post_register_fix 側で読み出して渡せる。新規テーブル・新規画面は
    作らず、既存のplan_selected（本ファイル内、change_type="plan_selected"）と
    同じ「dsl_patchを構造化データの入れ物として再利用する」パターンを踏襲する。
    """
    if result.get("status") in ("ok", "partial"):
        try:
            repo   = DslRepository()
            dsl_id = _ensure_dsl_definition_id(repo, _to_pascal_case_for_log(audit_problem_class))
            unresolved_dsl_patch = (
                {"hearing_texts": hearing_texts or [], "unresolved_at_registration": list(gate2_warnings)}
                if gate2_warnings else None
            )
            repo.log_dsl_evolution(
                dsl_id=dsl_id, change_type="human_confirmed", trigger_type="manual",
                business_context=f"{domain_name} の新規登録確認（/api/domain/confirm）",
                before_summary="\n".join(f"- {q}" for q in questions) or None,
                after_summary=answers or None,
                dsl_patch=unresolved_dsl_patch,
                created_by="user",
            )
        except Exception as log_err:
            logger.warning(f"[confirm_job:{job_id}] 監査ログ記録失敗（無視して続行）: {log_err}")
    else:
        logger.info(f"[confirm_job:{job_id}] status={result.get('status')}のため監査ログ・"
                    "DSL定義行の作成をスキップ（幽霊ドメイン化防止）")

    extensions_written_zero = bool(
        extensions_applied is not None and not (extensions_applied.get("written") or [])
    )
    _job_set(job_id, stage="done", result={
        "status": result["status"], "domain_name": domain_name,
        "written": result["written"], "scenarios_registered": result["scenarios_registered"],
        "errors": result["errors"], "extensions_applied": extensions_applied,
        "extensions_written_zero_warning": extensions_written_zero,
        "gate2_warnings": gate2_warnings,
    })
    logger.info(f"[confirm_job:{job_id}] 完了")


def _run_agent_answer_job(job_id: str, answer: str) -> None:
    """
    /api/domain/agent_answer の実体。debug_agentがask_humanで質問し、
    ジョブがstage="waiting_for_agent_question"で待機している状態から、
    人間の回答を渡して同じエージェントセッションを再開する。

    2026-07-17新設。ここではGate2の再検証や新しいエージェントラウンドの
    開始はしない（それは_advance_after_agent_round内で、エージェントの
    セッションが本当に終わった時にだけ1回行われる）。あくまで一時停止していた
    同じセッションの続きを実行するだけ＝「1ラウンド」の枠内に収まる。
    """
    try:
        job          = _job_get(job_id) or {}
        pending      = job.get("pending", {}) or {}
        domain_name  = job.get("domain_name", "")
        resume_state = pending.get("agent_resume_state")
        if not resume_state:
            _job_set(job_id, stage="error",
                     error="再開すべきエージェントの状態が見つかりません。お手数ですが最初からやり直してください。")
            return

        from debug_agent import run_debug_agent

        written_paths           = pending.get("written_paths", [])
        hearing_texts_for_check = pending.get("hearing_texts", [])
        snake                   = pending.get("snake", "")

        _job_set(job_id, stage="fixing", interrupt_requested=False)
        logger.info(f"[agent_answer_job:{job_id}] 人間の回答を受けてエージェントを再開")
        agent_result = run_debug_agent(
            questions=[], written_paths=written_paths,
            domain_name=domain_name, hearing_texts=hearing_texts_for_check,
            snake_name=snake,
            resume_state=resume_state, human_answer=answer,
            should_stop=lambda: bool((_job_get(job_id) or {}).get("interrupt_requested")),
            # 2026-08-10: 段階C。同一ラウンドの再開なので、初回呼び出し
            # （下のrun_debug_agent呼び出し）と揃える。
            summarize_stale_reads=True,
        )

        gate2_warnings: list[str] = []
        pending_for_advance = {k: v for k, v in pending.items() if k != "agent_resume_state"}
        outcome = _advance_after_agent_round(
            job_id, domain_name, pending_for_advance, agent_result,
            hearing_texts_for_check, snake, gate2_warnings,
        )
        if outcome is None:
            return
        result = outcome

        question_for_audit = (resume_state.get("pending_ask", {}) or {}).get("question", "")
        _finalize_confirm_job(job_id, domain_name, [question_for_audit], answer, result,
                               gate2_warnings, None, domain_name,
                               hearing_texts=hearing_texts_for_check)
    except Exception as e:
        logger.error(f"[agent_answer_job:{job_id}] エラー: {e}", exc_info=True)
        _job_set(job_id, stage="error", error=str(e))


# 2026-08-10追加（Koshoshi合意）: Gate2動的検証（実ソルブ）由来の指摘の接頭辞。
# memory.mdの「Gate2動的検証は非交渉（non-negotiable）」という原則と、force_apply
# （指摘を残したまま登録する）がこれらも無条件に握りつぶせてしまう実装が食い違って
# いたため、この接頭辞を持つ指摘が残っている場合はforce_applyを拒否する
# （下記 _run_confirm_job の elif force_apply: 参照）。静的field-check由来
# （Big-M・absent値誤用・要件カバレッジ等）はこれまで通りforce_apply可能で、
# 対象はあくまで「実際にsolve()した結果に基づく客観的な失敗」のみに絞る。
# 元々は _run_confirm_job 内のローカル変数だったが、force_apply側でも参照する
# 必要が生じたためモジュールレベルに引き上げた。
_DYNAMIC_STRUCTURAL_PREFIXES = (
    "（自動チェック・実装エラーの疑い／要コード修正）",
    "（自動チェック・実行検証）",
)


def _run_confirm_job(job_id: str, domain_name: str, pending: dict, questions: list, answers: str,
                      force_apply: bool = False) -> None:
    """
    /api/domain/confirm の実体。_run_domain_job と同じ設計方針で
    バックグラウンドスレッドとして実行し、フロントの接続状態と完全に切り離す。
    進捗は _DOMAIN_JOBS[job_id] に逐次書き込み、フロントは
    /api/domain/run/status/<job_id> をそのままポーリングに使い回せる。

    stage の推移:
      queued_confirm → applying → (generating_extensions) → registering_scenarios / applying_files
                     → (verifying) → done
      new_domain経路かつforce_apply=falseの場合は、_run_hearing_pipeline()の
      再実行に委譲するため、interpreting/generating/needs_confirmation等
      _run_hearing_pipeline側のstage推移がそのまま続く（何ラウンドでも）。
      任意の段階で error になり得る。

    2026-07-16修正: 従来このnew_domain経路（"scenarios"がpendingに無い場合）は
    人間の回答（answers）を監査ログに記録するだけで、実際には最初に警告が出た
    diffsをそのまま無条件に適用していた（＝人間が何を答えても「そのまま登録」に
    なる、名ばかりの確認ゲートだった）。force_apply=false（既定）では、
    answersをhearing_textsに追記し_run_hearing_pipeline()を再実行することで、
    実際に直っているかをGate1/Gate2で再検証してからでないとdoneにしない。
    """
    try:
        from domain_generator import apply_domain_files, apply_scenarios

        _job_set(job_id, stage="applying")

        extensions_applied = None
        gate2_warnings: list[str] = []
        audit_problem_class = domain_name  # デフォルト: new_domainやパターン3は domain_name 自体が実際の problem_class
        if "scenarios" in pending:
            extension_gaps = pending.get("extension_gaps", [])
            if extension_gaps:
                # パターン3: 人間が承認したextension_gapsを、base_domainの実装ファイルを
                # 新ドメイン名専用にコピーした上で適用する（既存ファイルは一切変更しない）。
                from domain_generator import generate_and_apply_extensions
                base_domain_for_ext   = pending.get("base_domain", "")
                hearing_texts_for_ext = pending.get("hearing_texts", [])
                logger.info(f"[confirm_job:{job_id}] extension_gaps 適用開始（コピー方式）: "
                            f"{len(extension_gaps)}件 (base_domain={base_domain_for_ext})")
                _job_set(job_id, stage="generating_extensions")
                extensions_applied = generate_and_apply_extensions(
                    domain_name, base_domain_for_ext, extension_gaps, hearing_texts_for_ext)
                written_count = len(extensions_applied.get("written", []) or [])
                logger.info(f"[confirm_job:{job_id}] extension_gaps 適用完了: "
                            f"status={extensions_applied.get('status')}, written={written_count}件")
                if written_count == 0:
                    # LLM出力が期待マーカー形式で返らず、実際には何もコードが
                    # 書き込まれなかった疑いがある（沈黙失敗）。ここでは処理は
                    # 続行するが、監査ログとjobステータス双方に警告として残す。
                    logger.warning(
                        f"[confirm_job:{job_id}] extension_gaps適用: written=0件。"
                        "LLM出力の解析に失敗した可能性があります（内容未確認のまま登録完了とみなさないこと）。"
                    )
                gate2_warnings.extend(extensions_applied.get("static_warnings", []) or [])
                # パターン3: コピー先のproblem_class = domain_name で正しい
            else:
                # 既存流用（existing_domain/base_problem）: 実際のルーティング先は
                # domain_name（人間が入力した業務ラベル）ではなく base_domain の方。
                audit_problem_class = pending.get("base_domain") or domain_name

            _job_set(job_id, stage="registering_scenarios")
            result = apply_scenarios(pending["scenarios"], domain_name, pending.get("scenario_registrations", []))

            if extensions_applied is not None:
                # Gate2動的検証（V8.7で追加）: 新規ドメインのフル生成には既に繋がっていたが、
                # この拡張適用パス（パターン3）には無かった。ここまで来ればbaseline/
                # infeasibleのシナリオJSON（2026-07-18よりtightは廃止）はapply_scenarios()が直前に書き出し済みなので、
                # 実際にsolve()まで通して例外・feasible不一致を検出できる。
                # ブロックはしない（従来のGate2方針と同じ: 警告として可視化するのみ）。
                _job_set(job_id, stage="verifying")
                try:
                    from domain_generator import run_gate2_dynamic_verification
                    new_snake = extensions_applied.get("new_snake") or _to_snake(domain_name)
                    logger.info(f"[confirm_job:{job_id}] Gate2動的検証開始: snake={new_snake}")
                    dyn_report = run_gate2_dynamic_verification(new_snake)
                    for w in dyn_report.get("warnings", []):
                        gate2_warnings.append(f"[Gate2動的検証] {w}")
                    for suffix, entry in dyn_report.get("scenarios", {}).items():
                        if entry.get("status") == "exception":
                            tb_text   = entry.get("traceback") or ""
                            last_line = tb_text.strip().splitlines()[-1] if tb_text.strip() else "不明なエラー"
                            gate2_warnings.append(f"[Gate2動的検証] {suffix}シナリオの実行中に例外: {last_line}")
                    logger.info(f"[confirm_job:{job_id}] Gate2動的検証完了: "
                                f"警告{len([w for w in gate2_warnings if 'Gate2動的検証' in w])}件")
                except Exception as e:
                    logger.warning(f"[confirm_job:{job_id}] Gate2動的検証をスキップ（実行エラー）: {e}", exc_info=True)
        elif force_apply:
            # 2026-08-10追加（Koshoshi合意）: Gate2動的検証（実ソルブ）由来の指摘が
            # 残っている場合、force_applyそのものを拒否する。memory.mdの
            # 「Gate2動的検証は非交渉」という原則を、ここで初めて実装として強制する
            # （従来はforce_applyがこの種の指摘も無条件に握りつぶせてしまっていた）。
            # 静的field-check由来（Big-M・absent値誤用・要件カバレッジ等）は
            # 引き続きforce_apply可能。job stageはneeds_confirmationのまま据え置き、
            # force_apply_blocked=Trueをフロントに返してその旨を表示させる。
            _blocked_dynamic = [
                q for q in questions
                if isinstance(q, str) and q.startswith(_DYNAMIC_STRUCTURAL_PREFIXES)
            ]
            if _blocked_dynamic:
                logger.warning(
                    f"[confirm_job:{job_id}] force_apply=True だが、Gate2動的検証由来の"
                    f"指摘が{len(_blocked_dynamic)}件残っているため拒否しました: {_blocked_dynamic}"
                )
                _job_set(job_id, stage="needs_confirmation", loop_exhausted=True,
                    questions=questions, force_apply_blocked=True,
                    pending={"diffs": pending.get("diffs", []), "patches": pending.get("patches", []),
                             "scenario_registrations": pending.get("scenario_registrations", []),
                             "hearing_texts": pending.get("hearing_texts", []),
                             "snake": pending.get("snake") or _to_snake(domain_name),
                             "written_paths": pending.get("written_paths", []),
                             # 2026-08-11追加: ここで拒否されても引き続きpendingを
                             # 引き継ぐループが続くため、advisory_questionsを
                             # 落とさず持ち越す。
                             "advisory_questions": pending.get("advisory_questions", [])})
                return  # まだ完了ではない（取りやめのみ選択可能）

            _job_set(job_id, stage="applying_files")
            logger.warning(
                f"[confirm_job:{job_id}] force_apply=True: 残っている警告を再検証せず、"
                "人間が明示的にこのまま登録するよう指示しました。"
            )
            if questions:
                # 2026-07-17追加: 上書きして登録した指摘事項を完了画面でも見えるよう
                # gate2_warningsとして引き継ぐ（隠さない。「絶対に嘘の完了と言わない」方針）。
                gate2_warnings.extend(questions)
            if pending.get("advisory_questions"):
                # 2026-08-11追加: force_applyはGate2の再検証をスキップするため、
                # 直前のneeds_confirmation時点で計算済みのadvisory_questions
                # （画面表示はしないが「記録には残す」方針の対象）をここで
                # gate2_warningsに合流させる。これが無いと、blocking項目が
                # 1件でもあってconfirm/force_apply経由で登録した場合、
                # advisory指摘が最終結果から完全に消えてしまっていた
                # （ProductionLineSequencing登録、2026-08-11に発覚。i18n
                # カバレッジのadvisory警告がsolver.py未対応を検知していたにも
                # 関わらず、gate2_warningsに一切現れなかった）。
                gate2_warnings.extend(pending["advisory_questions"])
            diffs_to_apply = pending.get("diffs", [])
            written_paths  = pending.get("written_paths", [])
            if written_paths:
                from domain_generator import refresh_diffs_from_disk
                diffs_to_apply = refresh_diffs_from_disk(diffs_to_apply, written_paths)
            result = apply_domain_files(diffs_to_apply, pending.get("patches", []),
                                         pending.get("scenario_registrations", []))

        else:
            # 2026-07-17再設計: 以前はエージェント1回＋Gate2再検証で指摘が残っていると、
            # 「続行」を押すたびに新しいラウンド（エージェントを最初から回す）を
            # 何度でも繰り返せる設計だった。しかし実機テストで(1)ラウンドを重ねる
            # ほどGate2の指摘が収斂せず増えることがある、(2)1ラウンドが長く、
            # 何度も待たされる、という2点が問題になった（ユーザー指摘：
            # 「結局ずっとこの画面に戻ってしまい終わらない」「収斂していなかった」）。
            #
            # そこでdebug_agentにask_humanツールを追加し、業務判断が必要な曖昧さは
            # セッションを終えずにその場で聞けるようにした（ask_humanでの対話は
            # 何度あっても同じ「1ラウンド」として扱う。詳細は
            # _advance_after_agent_round のdocstring参照）。この1ラウンドが終わって
            # Gate2を1回再検証してもなお問題が残る場合、もう新しいラウンドは
            # 自動で始めない。取りやめ／このまま登録するの二択に絞る。
            snake                   = pending.get("snake") or _to_snake(domain_name)
            written_paths           = pending.get("written_paths", [])
            hearing_texts_for_check = pending.get("hearing_texts", [])

            # 2026-08-08追加: Gate2の指摘カテゴリ振り分け。
            # run_gate2_checks()のblocking_questionsは接頭辞でカテゴリを識別できる
            # （静的field-check由来: 「（自動チェック）」「（自動検知・Big-M近似の疑い）」
            # 「（自動検知・ネストキー不一致の疑い）」。動的検証由来の構造的指摘:
            # 「（自動チェック・実装エラーの疑い／要コード修正）」＝実行時例外、
            # 「（自動チェック・実行検証）」＝feasible不一致・退化解検知等）。
            # 静的field-check由来はこれまで通りdebug_agentのエージェントラウンドに
            # 委ねる（自己修復ループ=self-repair v2はrun_gate2_checks冒頭で既に
            # 試行済みなので、ここに残っているのはそれでも直らなかったものだけ）。
            # 一方、動的検証由来の構造的指摘は「実際にsolve()した結果に基づく不具合」
            # であり、run_gate2_checks()のdocstring（2026-07-16設計）が元々述べていた
            # 「指摘が出た生成物は人間（実質Claude）が直接読んで直す」方針に立ち返り、
            # debug_agentの自動修復ラウンドを1回も回さずに直接診断（取りやめ／
            # このまま登録するの確認画面）へ回す。エージェントに任せて空振りする分の
            # 時間・ターン数を節約する狙い（Koshoshi合意、2026-08-08）。
            # _DYNAMIC_STRUCTURAL_PREFIXES はモジュールレベル（本関数の直前）に定義済み
            # （2026-08-10、force_apply側との共有のため引き上げ）。
            dynamic_structural_questions = [
                q for q in questions if isinstance(q, str) and q.startswith(_DYNAMIC_STRUCTURAL_PREFIXES)
            ]
            # 2026-08-10追加（#30）: 以前は動的検証系の指摘が1件でもあれば、同じ
            # ラウンドに混在する他カテゴリの指摘（hearing_coverage等、debug_agentで
            # 直せる可能性がある）もろとも丸ごとエージェントラウンドをスキップしていた。
            # 実機（prob059登録）で「得意先優先が目的関数に未反映」というhearing_coverage
            # 指摘が、たまたま同時に出ていた動的検証系の指摘のせいで一度もエージェントに
            # 渡らなかった事例があり、本来の設計意図（動的検証系"だけ"を人間判断に回す）
            # とズレていた。動的検証系以外の指摘が残っている場合は、そちらだけを
            # エージェントに渡して修正を試みる。動的検証系の指摘はエージェントには渡さず、
            # ラウンド後にrun_gate2_checks()が状態をゼロから再検証するので、まだ問題が
            # 残っていれば（動的検証系も含め）自然に確認画面へ戻る
            # （_advance_after_agent_round側の変更は不要）。
            non_dynamic_questions = [
                q for q in questions
                if not (isinstance(q, str) and q.startswith(_DYNAMIC_STRUCTURAL_PREFIXES))
            ]

            if dynamic_structural_questions and not non_dynamic_questions:
                logger.info(
                    f"[confirm_job:{job_id}] 動的検証由来の構造的指摘{len(dynamic_structural_questions)}件のみ検出"
                    "（他カテゴリの指摘なし）。"
                    "エージェントラウンドをスキップし、直接診断（取りやめ／このまま登録するの確認）に回します: "
                    f"{dynamic_structural_questions}"
                )
                _job_set(job_id, stage="needs_confirmation", loop_exhausted=True,
                    questions=questions,
                    pending={"diffs": pending.get("diffs", []), "patches": pending.get("patches", []),
                             "scenario_registrations": pending.get("scenario_registrations", []),
                             "hearing_texts": hearing_texts_for_check,
                             "snake": snake, "written_paths": written_paths})
                return  # まだ完了ではない（取りやめ／このまま登録するの確認待ち）

            if dynamic_structural_questions:
                logger.info(
                    f"[confirm_job:{job_id}] 動的検証由来の構造的指摘{len(dynamic_structural_questions)}件に加え、"
                    f"他カテゴリの指摘{len(non_dynamic_questions)}件を検出。動的検証系は今回のエージェント"
                    "ラウンドから除外し、それ以外の指摘だけをデバッグエージェントに渡します: "
                    f"{dynamic_structural_questions}"
                )

            from debug_agent import run_debug_agent

            # 中断フラグをリセットしてからエージェントを開始する
            # （/api/domain/interrupt がこのjob_idに立てたフラグをここで拾う）。
            _job_set(job_id, stage="fixing", interrupt_requested=False)
            # 動的検証系の指摘が混在していた場合は、それを除いた残りだけをエージェントに渡す
            # （混在していなければ従来通りquestions全件）。
            agent_questions = non_dynamic_questions if dynamic_structural_questions else questions
            logger.info(f"[confirm_job:{job_id}] 続行: デバッグエージェント開始"
                        f"（指摘{len(agent_questions)}件、対象ファイル{len(written_paths)}件）")
            agent_result = run_debug_agent(
                questions=agent_questions, written_paths=written_paths,
                domain_name=domain_name, hearing_texts=hearing_texts_for_check,
                snake_name=snake,
                human_notes=answers,
                should_stop=lambda: bool((_job_get(job_id) or {}).get("interrupt_requested")),
                # 2026-08-10: 段階B A/Bテストでトークン-19%・所要時間-5%を確認
                # （Koshoshi承認、詳細はENGINEERING_LOG.md 2026-08-10追記4）。
                # 段階C（本番切替）として有効化。
                summarize_stale_reads=True,
            )
            outcome = _advance_after_agent_round(
                job_id, domain_name, pending, agent_result,
                hearing_texts_for_check, snake, gate2_warnings,
            )
            if outcome is None:
                return  # まだ完了ではない（人間の質問待ち、または確認待ち）
            result = outcome

        _finalize_confirm_job(job_id, domain_name, questions, answers, result,
                               gate2_warnings, extensions_applied, audit_problem_class,
                               hearing_texts=pending.get("hearing_texts", []))

    except Exception as e:
        logger.error(f"[confirm_job:{job_id}] エラー: {e}", exc_info=True)
        _job_set(job_id, stage="error", error=str(e))


def _to_snake(name: str) -> str:
    return re.sub(r"([A-Z])", r"_\1", name).lower().lstrip("_")


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


@app.route("/baseline", methods=["POST"])
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


@app.route("/relax", methods=["POST"])
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


@app.route("/relax/start", methods=["POST"])
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


@app.route("/relax/status/<job_id>", methods=["GET"])
def relax_status(job_id: str):
    """
    /relax/start が返した job_id の進捗をポーリングする。
    stage: queued → generating → verifying → done / error
    """
    job = _job_get(job_id)
    if job is None:
        return jsonify({"status": "error", "message": "job_id が見つかりません（破棄済みまたは無効な job_id です）"}), 404
    return jsonify({"status": "ok", "job": job})


@app.route("/checks/status/<job_id>", methods=["GET"])
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


# ---------------------------------------------------------
# ★ V8.4: ドメイン自動生成エンドポイント
# ---------------------------------------------------------

@app.route("/api/domain/template", methods=["GET"])
def domain_template():
    try:
        template_path = _PROJECT_ROOT / "docs" / "hearing_template.md"
        if not template_path.exists():
            return jsonify({"status": "error", "message": "テンプレートファイルが見つかりません"}), 404
        return send_file(str(template_path), mimetype="text/markdown",
                         as_attachment=True, download_name="hearing_template.md")
    except Exception as e:
        return jsonify({"status": "error", "message": str(e)}), 500


@app.route("/api/domain/attachments", methods=["GET"])
def domain_attachments():
    try:
        from domain_generator import get_default_attachment_info
        return jsonify({"status": "ok", "attachments": get_default_attachment_info()})
    except Exception as e:
        return jsonify({"status": "error", "message": str(e)}), 500


@app.route("/api/domain/check/<domain_name>", methods=["GET"])
def domain_check(domain_name: str):
    try:
        from domain_generator import check_domain_exists
        return jsonify(check_domain_exists(domain_name))
    except Exception as e:
        return jsonify({"status": "error", "message": str(e)}), 500


@app.route("/api/domain/interpret", methods=["POST"])
def domain_interpret():
    try:
        data          = request.get_json(force=True)
        domain_name   = data.get("domain_name", "").strip()
        hearing_texts = data.get("hearing_texts", [])
        extra         = data.get("extra_attachments", [])
        if not domain_name:
            return jsonify({"status": "error", "message": "domain_name は必須です"}), 400
        if not hearing_texts:
            return jsonify({"status": "error", "message": "hearing_texts が空です"}), 400
        from domain_generator import interpret_hearing
        return jsonify(interpret_hearing(domain_name, hearing_texts, extra))
    except Exception as e:
        logger.error(f"/api/domain/interpret error: {e}", exc_info=True)
        return jsonify({"status": "error", "message": str(e)}), 500


@app.route("/api/domain/generate", methods=["POST"])
def domain_generate():
    try:
        data       = request.get_json(force=True)
        domain_def = data.get("domain_definition", {})
        overrides  = data.get("attachment_overrides", None)
        if not domain_def.get("domain_name"):
            return jsonify({"status": "error", "message": "domain_definition.domain_name は必須です"}), 400
        from domain_generator import check_domain_exists, generate_domain_files
        check = check_domain_exists(domain_def["domain_name"])
        if check["exists"]:
            return jsonify({"status": "conflict",
                            "message": f"ドメイン '{domain_def['domain_name']}' は既に登録されています。",
                            "conflicts": check["conflicts"]}), 409
        return jsonify(generate_domain_files(domain_def, overrides))
    except Exception as e:
        logger.error(f"/api/domain/generate error: {e}", exc_info=True)
        return jsonify({"status": "error", "message": str(e)}), 500


@app.route("/api/domain/apply", methods=["POST"])
def domain_apply():
    try:
        data      = request.get_json(force=True)
        diffs     = data.get("approved_diffs", [])
        patches   = data.get("approved_patches", [])
        scenarios = data.get("scenario_registrations", [])
        if not diffs and not patches:
            return jsonify({"status": "error", "message": "approved_diffs と approved_patches が両方空です"}), 400
        from domain_generator import apply_domain_files
        return jsonify(apply_domain_files(diffs, patches, scenarios))
    except Exception as e:
        logger.error(f"/api/domain/apply error: {e}", exc_info=True)
        return jsonify({"status": "error", "message": str(e)}), 500


@app.route("/api/domain/run", methods=["POST"])
def domain_run():
    """
    非同期ジョブとして即座に job_id を返し、実体はバックグラウンドスレッドで実行する。
    進捗は /api/domain/run/status/<job_id> でポーリングする。
    """
    try:
        data                 = request.get_json(force=True)
        domain_name          = data.get("domain_name", "").strip()
        hearing_texts        = data.get("hearing_texts", [])
        overrides            = data.get("attachment_overrides", None)
        base_domain_override = data.get("base_domain_override") or None
        force_new_domain     = bool(data.get("force_new_domain", False))

        if not domain_name:
            return jsonify({"status": "error", "message": "domain_name は必須です"}), 400
        if not hearing_texts:
            return jsonify({"status": "error", "message": "hearing_texts が空です"}), 400
        if base_domain_override and force_new_domain:
            return jsonify({"status": "error",
                "message": "base_domain_override と force_new_domain は同時に指定できません"}), 400

        _job_gc()
        job_id = uuid.uuid4().hex
        _job_set(job_id, stage="queued", domain_name=domain_name)

        thread = threading.Thread(
            target=_run_domain_job,
            args=(job_id, domain_name, hearing_texts, overrides, base_domain_override, force_new_domain),
            daemon=True,
        )
        thread.start()

        return jsonify({"status": "started", "job_id": job_id})

    except Exception as e:
        logger.error(f"/api/domain/run error: {e}", exc_info=True)
        return jsonify({"status": "error", "message": str(e)}), 500


@app.route("/api/domain/run/status/<job_id>", methods=["GET"])
def domain_run_status(job_id: str):
    """
    /api/domain/run が返した job_id の進捗をポーリングする。

    stage の推移:
      queued → interpreting → (generating) → applying → done
                                             └→ needs_confirmation (終了、人間待ち)
      任意の段階で error になり得る。

    フロントが接続切れ後にポーリングを再開しても、job_id が有効であれば
    このエンドポイントから現在の進捗をそのまま取得できる（サーバーは接続に依存しない）。
    """
    job = _job_get(job_id)
    if job is None:
        return jsonify({"status": "error", "message": "job_id が見つかりません（破棄済みまたは無効な job_id です）"}), 404
    return jsonify({"status": "ok", "job": job})


@app.route("/api/domain/confirm", methods=["POST"])
def domain_confirm():
    """
    /api/domain/run が status="needs_confirmation" を返した場合に、
    人間が questions に回答した後に呼び出すエンドポイント。

    非同期ジョブとして即座に job_id を返し、実体（LLM呼び出し・ファイル書き込み・
    DB登録）はバックグラウンドスレッドで実行する（_run_domain_job と同じ設計）。
    進捗は /api/domain/run/status/<job_id> をそのままポーリングに使い回せる
    （新規エンドポイントは追加しない）。

    以前はここでLLM呼び出しを含む全処理を同期実行しており、拡張適用のように
    数分かかる処理があるとフロントのread timeoutより先に処理が終わらず、
    「実際には裏で処理が続いているのにフロントはエラー表示」という状態になっていた。

    Request body:
      {
        "domain_name": "...",
        "pending": { ... domain_run が返した pending をそのまま ... },
        "questions": [ ... domain_run が返した questions をそのまま ... ],  # 監査用、任意
        "answers": "人間が入力した回答テキスト",  # 任意
        "job_id": "..."  # 任意。domain_run発行時のjob_idを渡せばステージ推移が連続する。
                          # 省略時はここで新規発行する。
        "force_apply": false  # 任意。既定はfalse。
            # false（既定、通常の「続行」）: needs_confirmation中ディスクに残っている
            #   draftファイル（solver.py/converter.py/シナリオJSON。人間 or Claudeが
            #   直接編集して直した後の状態）を読み直し、Gate2だけを再検証する
            #   （Stage1a/Stage2の再実行はしない）。指摘が0件になれば正式に登録、
            #   まだ残っていれば新しい指摘付きで再度needs_confirmationに戻る。
            # true: 再検証せず、draftファイルの現在の内容をそのまま適用する
            #   （人間が「この警告は無視してよい」と明示判断した場合の脱出口）。
      }

    Response: {"status": "started", "job_id": "..."}
    """
    try:
        data        = request.get_json(force=True)
        domain_name = data.get("domain_name", "").strip()
        pending     = data.get("pending", {})
        questions   = data.get("questions", [])
        answers     = data.get("answers", "")
        force_apply = bool(data.get("force_apply", False))
        job_id      = data.get("job_id", "") or uuid.uuid4().hex

        if not domain_name:
            return jsonify({"status": "error", "message": "domain_name は必須です"}), 400
        if not pending:
            return jsonify({"status": "error", "message": "pending が空です（/api/domain/run の応答をそのまま渡してください）"}), 400

        _job_set(job_id, stage="queued_confirm", domain_name=domain_name)

        thread = threading.Thread(
            target=_run_confirm_job,
            args=(job_id, domain_name, pending, questions, answers, force_apply),
            daemon=True,
        )
        thread.start()

        return jsonify({"status": "started", "job_id": job_id})

    except Exception as e:
        logger.error(f"/api/domain/confirm error: {e}", exc_info=True)
        return jsonify({"status": "error", "message": str(e)}), 500


@app.route("/api/domain/cancel", methods=["POST"])
def domain_cancel():
    """
    2026-07-16追加（デバッグチャット構想）: needs_confirmation中に「取りやめ」を
    選んだ場合に呼ぶ。write_files_for_dynamic_check() がGate2再検証のために
    実ファイルとしてディスクに書き出したdraft（solver.py/converter.py/
    シナリオJSON）は、続行時にClaudeが直接編集できるようneeds_confirmation中は
    削除せず残す設計にしたため、取りやめた場合は明示的にここで片付ける
    （放置すると、DB未登録・patches未適用のコードファイルだけが残る「幽霊状態」に
    なり、実機で実際に踏んだ事故と同じ問題を再発させる）。

    Request body: {"pending": { ... /api/domain/run または /api/domain/confirm が
                                  返した pending をそのまま ... }}
    Response: {"status": "ok", "cleaned": [...]}
    """
    try:
        data          = request.get_json(force=True)
        pending       = data.get("pending", {}) or {}
        written_paths = pending.get("written_paths", []) or []
        if written_paths:
            from domain_generator import cleanup_dynamic_check_files
            cleanup_dynamic_check_files(written_paths)
            logger.info(f"[domain_cancel] draftファイルを削除: {written_paths}")
        return jsonify({"status": "ok", "cleaned": written_paths})
    except Exception as e:
        logger.error(f"/api/domain/cancel error: {e}", exc_info=True)
        return jsonify({"status": "error", "message": str(e)}), 500


@app.route("/api/domain/interrupt", methods=["POST"])
def domain_interrupt():
    """
    2026-07-16追加: デバッグエージェント（debug_agent.run_debug_agent）は
    最大ターン数で自動的に打ち切られるが、それより前に人間が「今の状態で
    いったん止めたい」と判断した場合に、ここでフラグを立てて次のターンの
    前に打ち切らせる（LLM呼び出しの途中では止められないが、1ターンは
    高々1回のLLM呼び出し＋ツール実行なので、体感的にはすぐ止まる）。

    Request body: {"job_id": "..."}
    Response: {"status": "ok"}
    """
    try:
        data   = request.get_json(force=True)
        job_id = data.get("job_id", "")
        if not job_id:
            return jsonify({"status": "error", "message": "job_id は必須です"}), 400
        _job_set(job_id, interrupt_requested=True)
        logger.info(f"[domain_interrupt] job_id={job_id} に中断フラグを設定")
        return jsonify({"status": "ok"})
    except Exception as e:
        logger.error(f"/api/domain/interrupt error: {e}", exc_info=True)
        return jsonify({"status": "error", "message": str(e)}), 500


@app.route("/api/domain/agent_answer", methods=["POST"])
def domain_agent_answer():
    """
    2026-07-17新設: デバッグエージェントがask_humanツールでその場に質問し、
    ジョブがstage="waiting_for_agent_question"で待っている状態に対し、
    人間の回答を渡して同じエージェントセッションを再開させる。

    /api/domain/confirm とは別エンドポイントにしたのは、これは新しいラウンドの
    開始ではなく、一時停止していた同一セッションの再開だから（Gate2の再検証は
    行わず、_advance_after_agent_round内でエージェントのセッションが本当に
    終わった時にだけ1回行われる）。

    Request body: {"job_id": "...", "answer": "..."}
    Response: {"status": "started", "job_id": "..."}
    """
    try:
        data   = request.get_json(force=True)
        job_id = data.get("job_id", "")
        answer = data.get("answer", "")
        if not job_id:
            return jsonify({"status": "error", "message": "job_id は必須です"}), 400

        job = _job_get(job_id)
        if not job:
            return jsonify({"status": "error", "message": "指定されたjob_idが見つかりません"}), 404
        if job.get("stage") != "waiting_for_agent_question":
            return jsonify({"status": "error",
                             "message": f"このジョブは現在エージェントの質問待ちではありません"
                                        f"（stage={job.get('stage')}）"}), 409

        thread = threading.Thread(
            target=_run_agent_answer_job, args=(job_id, answer), daemon=True,
        )
        thread.start()

        return jsonify({"status": "started", "job_id": job_id})
    except Exception as e:
        logger.error(f"/api/domain/agent_answer error: {e}", exc_info=True)
        return jsonify({"status": "error", "message": str(e)}), 500


def _load_hearing_texts_for_post_register_fix(domain_name: str) -> list:
    """
    2026-08-10追加（#31設計3-2の最小実装）: dsl_evolution_log（human_confirmed、
    dsl_patch）に登録時保存しておいたhearing_textsを読み出す。
    _finalize_confirm_job側で保存していなかった（gate2_warningsが無い状態で
    登録された等の）古いドメインでは見つからず、その場合は空リストを返す
    （run_post_registration_fixの従来の挙動と同じ。新規テーブル・新規画面は作らない
    最小実装のため、ここが「未解決課題一覧UI」の代わりの入口になる）。
    """
    try:
        repo = DslRepository()
        existing = repo.list_dsl_definitions(problem_class=_to_pascal_case_for_log(domain_name))
        if not existing:
            return []
        dsl_id = existing[0]["id"]
        for entry in repo.get_evolution_history(dsl_id=dsl_id, limit=20):
            if entry.get("change_type") != "human_confirmed":
                continue
            dsl_patch = entry.get("dsl_patch") or {}
            hearing_texts = dsl_patch.get("hearing_texts")
            if hearing_texts:
                return hearing_texts
        return []
    except Exception as e:
        logger.warning(
            f"[post_register_fix] {domain_name}: hearing_texts復元に失敗"
            f"（従来通り空リストで続行）: {e}"
        )
        return []


def _run_post_register_fix_job(job_id: str, domain_name: str, snake: str, warnings: list) -> None:
    """
    2026-07-18f新設: /api/domain/post_register_fix の実体。

    登録が既に完了した後、result.gate2_warningsに残っていた指摘を
    人間が任意で「今すぐ直したい」と選んだ場合にだけ動く（登録フロー本体
    とは無関係の別ジョブ）。domain_generator.run_post_registration_fix()を
    そのまま呼ぶだけで、DB登録・監査ログの記録は一切行わない
    （既に/api/domain/confirmで確定済みのため二重記録を避ける）。
    """
    try:
        from domain_generator import run_post_registration_fix

        # 中断フラグをリセットしてから開始する（/api/domain/interrupt が
        # このjob_idに立てたフラグをここで拾う。stage='fixing'中は既存の
        # 中断ボタンがそのまま表示されるため、ここでも同じ仕組みに乗せる）。
        _job_set(job_id, stage="fixing", domain_name=domain_name, interrupt_requested=False)
        hearing_texts = _load_hearing_texts_for_post_register_fix(domain_name)
        logger.info(f"[post_register_fix_job:{job_id}] hearing_texts復元: {len(hearing_texts)}件"
                    f"（0件の場合は従来通り文脈無しで実行）")
        fix_result = run_post_registration_fix(
            snake, domain_name, warnings,
            should_stop=lambda: bool((_job_get(job_id) or {}).get("interrupt_requested")),
            hearing_texts=hearing_texts,
        )
        _job_set(job_id, stage="done", result={
            "status": "fix_done",
            "domain_name": domain_name,
            "fixed_summary": fix_result["fixed_summary"],
            "gate2_warnings": fix_result["gate2_warnings"],
            "turns_used": fix_result["turns_used"],
            "stopped_reason": fix_result["stopped_reason"],
        })
        logger.info(f"[post_register_fix_job:{job_id}] 完了 "
                    f"stopped_reason={fix_result['stopped_reason']}, "
                    f"残警告={len(fix_result['gate2_warnings'])}件")
    except Exception as e:
        logger.error(f"[post_register_fix_job:{job_id}] エラー: {e}", exc_info=True)
        _job_set(job_id, stage="error", error=str(e))


@app.route("/api/domain/post_register_fix", methods=["POST"])
def domain_post_register_fix():
    """
    2026-07-18f新設: 登録完了後の確認画面（result.gate2_warnings）から、
    残った指摘を人間が任意で今すぐ直したい場合に呼ぶエンドポイント。
    「最後のボタンのラベルが間違いのような感覚を与える」というフィードバックを受け、
    force_apply（残りは誤検知と判断してそのまま登録する）を選んだ後でも、
    後から気が変わったり実害が見つかったりした場合の逃げ道として新設。

    登録前フロー（/api/domain/confirm）とは独立した別ジョブとして動く
    （pending/draftの概念が無く、既に登録済みのライブファイルを直接直す）。
    進捗は既存の /api/domain/run/status/<job_id> をそのままポーリングに使い回す。

    Request body: {"domain_name": "...", "warnings": ["...", ...]}
    Response: {"status": "started", "job_id": "..."}
    """
    try:
        data        = request.get_json(force=True)
        domain_name = data.get("domain_name", "").strip()
        warnings    = data.get("warnings", [])

        if not domain_name:
            return jsonify({"status": "error", "message": "domain_name は必須です"}), 400

        snake  = _to_snake(domain_name)
        job_id = uuid.uuid4().hex

        _job_set(job_id, stage="queued", domain_name=domain_name)

        thread = threading.Thread(
            target=_run_post_register_fix_job, args=(job_id, domain_name, snake, warnings), daemon=True,
        )
        thread.start()

        return jsonify({"status": "started", "job_id": job_id})
    except Exception as e:
        logger.error(f"/api/domain/post_register_fix error: {e}", exc_info=True)
        return jsonify({"status": "error", "message": str(e)}), 500


def _run_verify_dynamic_batch_job(job_id: str, snakes: list[str]) -> None:
    """
    2026-08-11新設: 「Gate2の指摘ログ」ではなく「今この時点で実際に動くか」を
    直接確認するための一括検証ジョブ。domain_seeds/への追加要否を、過去の
    dsl_evolution_logの記録（人間/AIが本当に内容を了承したかは記録から
    確実には判別できない）から推測するのではなく、baseline/infeasibleの
    2シナリオを実際にsolve()まで通して今の挙動を直接確認する方針に切り替えた
    （Koshoshi合意、2026-08-11。「Gateを通すことがゴールではなく、登録された
    ドメインが与えられたデータで動くことがゴール」という原則に基づく）。

    既存ドメイン全件（新規追加19件だけでなく、既にdomain_seeds化済みの7件も
    含む）を対象にする。Gate自体がドメイン登録を重ねる中で修正され続けてきた
    可動式の基準であるため、古い登録ほど「その時点のGateは通ったが今のGateでは
    通らない」可能性があり、区別せず一律で検証する。
    """
    from domain_generator import run_gate2_dynamic_verification

    results: dict[str, dict] = {}
    _job_set(job_id, stage="verifying", domain_name="__batch__", total=len(snakes), done=0)
    for i, snake in enumerate(snakes):
        try:
            report = run_gate2_dynamic_verification(snake)
            results[snake] = report
        except Exception as e:
            logger.error(f"[verify_dynamic_batch:{job_id}] {snake}: 検証自体が例外で失敗: {e}", exc_info=True)
            results[snake] = {"status": "verification_error", "error": str(e)}
        _job_set(job_id, stage="verifying", domain_name="__batch__",
                 total=len(snakes), done=i + 1, results=dict(results))
        logger.info(f"[verify_dynamic_batch:{job_id}] {snake} 完了 ({i + 1}/{len(snakes)})")
    _job_set(job_id, stage="done", domain_name="__batch__", total=len(snakes), done=len(snakes),
             result={"results": results})
    logger.info(f"[verify_dynamic_batch:{job_id}] 全{len(snakes)}件完了")


@app.route("/api/domain/verify_dynamic_batch", methods=["POST"])
def domain_verify_dynamic_batch():
    """
    2026-08-11新設: 指定した（省略時は登録済み全件の）ドメインについて、
    baseline/infeasibleシナリオを実際にsolve()まで実行し、現時点での
    実際の挙動（feasible判定・例外の有無）をまとめて確認する。
    domain_seeds/への追加要否を、過去ログの解釈ではなく実機動作で判断する
    ための監査用エンドポイント（読み取り専用、DBやシードファイルへの
    書き込みは一切行わない）。

    Request body: {"domains": ["truck_dispatcher", ...]}  # snake_case、省略時は全件
    Response: {"status": "started", "job_id": "..."}
    """
    try:
        data = request.get_json(force=True, silent=True) or {}
        snakes = data.get("domains")
        if not snakes:
            from dsl_repository.repository import DslRepository
            repo = DslRepository()
            defs = repo.list_dsl_definitions()
            snakes = sorted({_to_snake(d["problem_class"]) for d in defs})

        job_id = uuid.uuid4().hex
        _job_set(job_id, stage="queued", domain_name="__batch__")
        thread = threading.Thread(
            target=_run_verify_dynamic_batch_job, args=(job_id, snakes), daemon=True,
        )
        thread.start()
        return jsonify({"status": "started", "job_id": job_id, "domains": snakes})
    except Exception as e:
        logger.error(f"/api/domain/verify_dynamic_batch error: {e}", exc_info=True)
        return jsonify({"status": "error", "message": str(e)}), 500


def _to_pascal_case_for_log(name: str) -> str:
    # 既に PascalCase ならそのまま、snake_case なら変換
    if "_" in name:
        return "".join(w.capitalize() for w in name.split("_"))
    return name


# ---------------------------------------------------------
# その他のエンドポイント
# ---------------------------------------------------------

@app.route("/baplie", methods=["POST"])
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


@app.route("/dsl_repository/dsl", methods=["GET"])
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


@app.route("/dsl_repository/ext", methods=["GET"])
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


@app.route("/dsl_repository/all", methods=["GET"])
def dsl_repository_all():
    try:
        return jsonify({"status": "ok", "repository": DslRepository().get_all()})
    except Exception as e:
        return jsonify({"status": "error", "message": "リポジトリ取得に失敗しました。"}), 500


@app.route("/health", methods=["GET"])
def health():
    return jsonify({"status": "ok", "solvers": registry.list_solvers()})


@app.route("/ask", methods=["POST"])
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


@app.route("/analyze", methods=["POST"])
def analyze():
    try:
        answer = analyze_dsl(request.json.get("dsl", {}))
        return jsonify({"status": "ok", "answer": answer})
    except Exception as e:
        logger.error(f"/analyze Error: {str(e)}", exc_info=True)
        return jsonify({"status": "error", "message": str(e)}), 500


@app.route("/apply_patch", methods=["POST"])
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


@app.route("/dsl_repository/scenarios", methods=["GET"])
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


# ---------------------------------------------------------
# Plan選択ロガー（CVRP等、複数ヒューリスティック×コスト重みを並行提示するドメイメン用）
# ユーザーが複数Planのうちどれを選んだかを dsl_evolution_log に記録する。
# 新しいテーブルは作らず、既存の dsl_evolution_log を change_type="plan_selected" で流用する。
# ---------------------------------------------------------

def _ensure_dsl_definition_id(repo: DslRepository, problem_class: str) -> int:
    """
    problem_class に対応する dsl_definitions の最新レコードIDを返す。
    未登録の場合は自動作成する（domain_generator.py の _ensure_dsl_definition と同様の振る舞い）。
    """
    existing = repo.list_dsl_definitions(problem_class=problem_class)
    if existing:
        return existing[0]["id"]
    return repo.create_dsl_definition(
        problem_class=problem_class, version="1.0", extensions=[], schema_json={},
        description=f"{problem_class} — 自動登録（log_plan_selection経由）",
    )


@app.route("/dsl_repository/log_plan_selection", methods=["POST"])
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


@app.route("/dsl_repository/scenarios", methods=["POST"])
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


@app.route("/dsl_repository/scenarios/<int:scenario_id>", methods=["DELETE"])
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
@app.route("/dsl_repository/scenarios/<int:scenario_id>/refresh", methods=["POST"])
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


@app.route("/dsl_repository/domain/<domain_name>", methods=["DELETE"])
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
    from dsl_repository.cleanup_domain import collect_deletion_targets, delete_domain_api
    
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
    except Exception as e:
        logger.error(f"/dsl_repository/domain/{domain_name} DELETE error: {e}", exc_info=True)
        return jsonify({"status": "error", "message": str(e)}), 500


@app.route("/dsl_repository/scenarios/<int:scenario_id>/export", methods=["GET"])
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


@app.route("/dsl_repository/registry", methods=["GET"])
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


@app.route("/api/llm/generate_prompt", methods=["POST"])
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


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    # use_reloader=False: ドメイン登録フロー(Gate2動的検証・copy_domain_for_extension等)が
    # Backend配下に生成コードを書き込むため、reloaderが有効だとその書き込みを検知して
    # プロセスごと再起動し、進行中の登録処理が道連れで落ちる（2026-07-11発覚）。
    # デバッガ機能(debug=True)は残し、自動再起動のみ無効化する。
    app.run(debug=True, use_reloader=False, host="0.0.0.0", port=port)
