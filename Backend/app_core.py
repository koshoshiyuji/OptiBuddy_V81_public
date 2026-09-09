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


# 2026-08-21追記: 簡易APIキー認証（任意機能、既定は無効・後方互換）。
# 従来Backend/app.pyには認証機構が一切なく、開発ガイドも「社内利用前提、
# 外部公開時はリバースプロキシ等で認証層を追加すること」と明記していた
# （OptiBuddy_Development_Guide.md 7-3節）。しかしSタイプ（既存有償ドメインの
# お客様先導入）が実際の商品として提供され始めた以上、お客様が自分の環境に
# 導入した際に誤って外部公開してしまう事故を防ぐ最低限のデフォルトを
# 用意しておく。
#
# 設計方針:
#   - .env に OPTIBUDDY_API_KEY を設定した場合のみ有効化する。未設定（空文字）
#     なら従来通り認証なしで動作する。無償版のセルフホスト・ローカル検証用途を
#     壊さないための後方互換。
#   - 有効時は、/health を除く全エンドポイントで X-API-Key ヘッダー
#     （または Authorization: Bearer <key>）の一致を要求する。/health は
#     稼働監視用に外部から素通しできる必要があるため対象外にする。
#   - CORSプリフライト（OPTIONS）は認証チェックの対象外にする。ブラウザが
#     自動送信するpreflightリクエストにはカスタムヘッダーが付かないため、
#     ここでブロックするとCORS自体が機能しなくなる。
#   - タイミング攻撃を避けるため文字列の単純比較ではなく hmac.compare_digest
#     を使う。
#
# 2026-08-21(2)追記: 複数キー対応（呼び出し元ごとの識別）。
# 1顧客＝1台の自前ホスティング環境という前提は変わらないが、その1つの環境の
# 中で複数の内部システム（自社フロントエンド・ERP連携・夜間バッチ等）が
# それぞれ別のキーで呼び出せるようにし、どの名前のキーで認証されたかを
# ログに残せるようにする。複数の顧客デプロイをまたいだ中央管理（ライセンス
# サーバー的な仕組み）は範囲外（各顧客は引き続き自分の.envを自分で管理する
# 運用のまま）。
#   - OPTIBUDDY_API_KEYS（JSON、{"名前": "キー", ...}形式）を設定した場合は
#     こちらを使う。設定されていれば単一キーのOPTIBUDDY_API_KEYより優先する
#     （両方設定されている場合はOPTIBUDDY_API_KEYSのみが使われる）。
#   - OPTIBUDDY_API_KEYS未設定・OPTIBUDDY_API_KEYのみ設定の場合は従来通り
#     （後方互換、内部的には名前"default"の1件として扱う）。
#   - OPTIBUDDY_API_KEYSのJSONが壊れている場合、認証なしに静かにフォール
#     バックするとセキュリティ機能を意図せず無効化してしまうため、起動時に
#     例外を投げて気付けるようにする（fail-closed）。
_RAW_API_KEY = os.environ.get("OPTIBUDDY_API_KEY", "").strip()
_RAW_API_KEYS_JSON = os.environ.get("OPTIBUDDY_API_KEYS", "").strip()
_AUTH_EXEMPT_PATHS = {"/health"}

if _RAW_API_KEYS_JSON:
    try:
        _parsed_keys = json.loads(_RAW_API_KEYS_JSON)
        if not isinstance(_parsed_keys, dict) or not all(
            isinstance(k, str) and isinstance(v, str) for k, v in _parsed_keys.items()
        ):
            raise ValueError(
                'OPTIBUDDY_API_KEYSは {"名前": "キー", ...} 形式のJSONオブジェクトである必要があります。'
            )
        _API_KEYS = {name: key.strip() for name, key in _parsed_keys.items() if key.strip()}
        if not _API_KEYS:
            raise ValueError("OPTIBUDDY_API_KEYSに有効なキーが1件もありません。")
    except (json.JSONDecodeError, ValueError) as _e:
        raise RuntimeError(
            f"OPTIBUDDY_API_KEYSの設定が不正なため起動を中止します: {_e}\n"
            '例: OPTIBUDDY_API_KEYS={"frontend": "xxxx", "batch": "yyyy"}'
        ) from _e
elif _RAW_API_KEY:
    _API_KEYS = {"default": _RAW_API_KEY}
else:
    _API_KEYS = {}


@app.before_request
def _require_api_key():
    if not _API_KEYS:
        return None  # 未設定時は従来通り認証なし
    if request.method == "OPTIONS":
        return None  # CORSプリフライトは素通し
    if request.path in _AUTH_EXEMPT_PATHS:
        return None

    supplied = request.headers.get("X-API-Key", "")
    if not supplied:
        auth_header = request.headers.get("Authorization", "")
        if auth_header.startswith("Bearer "):
            supplied = auth_header[len("Bearer "):]

    matched_name = None
    for name, key in _API_KEYS.items():
        if hmac.compare_digest(supplied, key):
            matched_name = name
            break

    if matched_name is None:
        return jsonify({
            "status": "unauthorized",
            "message": "APIキーが必要です。X-API-Keyヘッダー、またはAuthorization: Bearer <key>で指定してください。",
        }), 401

    logger.info("[Auth] request authenticated key_name=%s path=%s", matched_name, request.path)
    return None


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


def _to_snake(name: str) -> str:
    return re.sub(r"([A-Z])", r"_\1", name).lower().lstrip("_")


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
