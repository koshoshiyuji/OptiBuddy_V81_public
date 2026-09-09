"""
Backend/domain_generator.py  (V4.5)

V4.3 → V4.4:
  - _build_stage2_prompt: CP Optimizer 禁止パターンセクションを追加。
    LLMが生成するソルバーコードの既知バグを事前に防ぐ。
  - _sanitize_solver_code: ソルバーファイル書き込み前に既知の
    CP Optimizerバグパターンを自動修正する静的サニタイザーを追加。
    apply_domain_files からソルバーファイルに対して自動適用される。

V4.4 → V4.5:
  - production_lot_scheduler で実際に発生した不具合を受けて、解抽出時の
    presence_of()/start_of()/end_of() をその場で組み立てて get_value() に渡す
    パターン（docplexバージョン依存でKeyError→exceptで握り潰され、
    「解けているのに全件未割当」になる）を、禁止パターン5として
    Stage2プロンプト（_CPO_FORBIDDEN_PATTERNS）および静的サニタイザーの
    警告検出（_CPO_WARN_PATTERNS）に追加。

V4.5 → V4.6:
  - HANDOFF_2026-07-15b 論点1-2: フロントエンドパッチ（tsx/ts）に構文・型検証が
    無く、apply_domain_files が書き込んだ直後にアプリ全体が起動不能になる事故が
    起きた。apply_domain_files に「書き込み前スナップショット→Frontend配下への
    書き込みがあればtsc -bで型検証→失敗したら今回の適用を丸ごとロールバック」を
    追加（_run_frontend_typecheck）。これにより新規ドメイン登録・確認フローの
    どちらを通っても、フロントエンドを壊す変更はディスクに残らない。
"""

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


logger = logging.getLogger(__name__)

_BACKEND_ROOT  = Path(__file__).parent.parent
_PROJECT_ROOT  = _BACKEND_ROOT.parent
_FRONTEND_ROOT = _PROJECT_ROOT / "Frontend"
_SCENARIOS_DIR = _BACKEND_ROOT / "dsl_repository" / "scenarios"
_DOCS_DIR      = _PROJECT_ROOT / "docs"

DEFAULT_ATTACHMENTS = {
    "repomix-domain-addition.xml": str(_PROJECT_ROOT / "repomix-domain-addition.xml"),
    "OptiBuddy_Development_Guide.md":           str(_DOCS_DIR / "OptiBuddy_Development_Guide.md"),
    "OptiBuddy_V81_spec.md":       str(_DOCS_DIR / "OptiBuddy_V81_spec.md"),
    "app_routing_snippet.md":       str(_DOCS_DIR / "app_routing_snippet.md"),
    "table_sections.py":      str(_BACKEND_ROOT / "dsl_transformer" / "table_sections.py"),
    "GenericResultTable.tsx": str(_FRONTEND_ROOT / "src/app/studio/components/GenericResultTable.tsx"),
    "rolling_window.py":      str(_BACKEND_ROOT / "solvers" / "base" / "rolling_window.py"),
    # 2026-07-18追加（docs/DESIGN_2026-07-18_layer_ab_pattern_library.md）:
    # レイヤーA/B共通モジュール。新規ドメインは以下を必ず経由し、独自実装しないこと。
    "constraint_applier.py":    str(_BACKEND_ROOT / "solvers" / "base" / "constraint_applier.py"),
    "quantity_requirement.py":  str(_BACKEND_ROOT / "solvers" / "base" / "quantity_requirement.py"),
    "solution_extraction.py":   str(_BACKEND_ROOT / "solvers" / "base" / "solution_extraction.py"),
    # 2026-07-26追加（DESIGN_2026-07-26_generic_ce_limit_fallback.md）、
    # 2026-08-04削除: ce_limit_lns.py（16KB）/ce_limit_mip_fallback.py（4.5KB）の
    # 全文添付は2026-08-04時点で削除した。理由: この2ファイルの「Stage2が実際に
    # 呼び出す必要のある関数」（is_ce_limit_exceeded/CeLimitExceededError/
    # run_solve_with_ce_limit_fallback/solve_with_ce_fallback）は、
    # _CPO_FORBIDDEN_PATTERNS内「CE上限フォールバックの実装ルール」に import文＋
    # 呼び出しコード例として既に一字一句そのまま埋め込まれており、ドメイン側は
    # 内部実装を一切見ずに規約通り呼ぶだけでよい設計（意図的に再試行回数・
    # バッチサイズ等の内部パラメータはドメイン側から触らせない）。よって全文添付は
    # 情報として重複しているだけで、新規ドメイン登録のたびに約5,100トークン分
    # （初回Stage2呼び出しのキャッシュ書き込みコスト）を無駄に費やしていた。
    # 万一ドメイン側が誤って独自実装しようとするケースの検知は、既存のGate2
    # 静的チェック（layer_ab_pattern_library系）に委ねる。
    # 対照: constraint_applier.py/quantity_requirement.py/solution_extraction.py/
    # table_sections.py/rolling_window.pyは、プロンプト内の説明が「使い方の一部
    # （import文程度）」に留まり関数の全パラメータまでは書かれていないため、
    # 全文添付が引き続き必要と判断し残した（削るなら個別に同様の検証が必要）。
    # {snake}_batch_decomposer.py の具体的な実装テンプレート（宣言＋既存関数呼び直し
    # のみで構成する、という規約の実例として添付する）。
    "nurse_shift_weekly_cap_batch_decomposer.py":
        str(_BACKEND_ROOT / "solvers" / "nurse_shift_weekly_cap_batch_decomposer.py"),
}

# 2026-08-04追加: 添付ファイルの中には、特定ドメイン（NurseShiftWeeklyCap等）の
# 具体的な決定変数構造を含む実装例が「別の狭い目的（CE上限フォールバックの
# 実装パターン等）」のためだけに全ドメイン共通で無条件添付されているものがある。
# ShiftRotationScheduler登録で、この種の添付が「スタッフ×日×シフトを独立変数として
# 割り当てる」という具体例として強く作用し、対称性削減（1人分のテンプレート＋
# ローテーション）が要件のドメインでもその具体例に引きずられて誤った設計が
# 繰り返し生成される事象が確認された（詳細はOptiBuddy_V81_devnotes/
# ENGINEERING_LOG.md 2026-08-04参照）。添付自体を外すと本来の目的（CE上限対応
# パターンの実例提示）が果たせなくなるため、添付を残したまま「この添付の
# どの部分が参考対象で、どの部分がこのドメイン固有で流用してはならないか」を
# 明示するスコープ注記を差し込む方式で対応する。
_ATTACHMENT_SCOPE_NOTES: dict[str, str] = {
    "nurse_shift_weekly_cap_batch_decomposer.py": (
        "**参照範囲の注記（重要）**: このファイルの参考対象は、CPLEX評価版の"
        "モデルサイズ上限（CE limit）を検知した場合の逐次バッチ分割パターン"
        "（PRIMARY_ENTITY_KEY／QUOTA_FIELDS宣言と、既存のbuild_candidates等の"
        "呼び直しによるオーケストレーション構造）**のみ**である。\n"
        "ファイル内に現れる「スタッフ単位」「日×シフトの候補」といった"
        "NurseShiftWeeklyCap固有の決定変数・データ構造は、あくまでこの一例の"
        "具体的な中身であり、他ドメインの決定変数設計のテンプレートでは"
        "**ない**。今回実装するドメインの決定変数・制約構造は、ヒアリング内容・"
        "formulation_directive（存在する場合）・下記「ドメイン定義」セクションに"
        "厳密に従って設計すること。NurseShiftWeeklyCapが採用する"
        "「エンティティ×日×シフトの独立変数」という構造は、そのドメイン固有の"
        "選択であり、一般解ではない。"
    ),
}

EXISTING_DOMAINS: dict[str, str | None] = {
    "YardPlanning":                          str(_SCENARIOS_DIR / "congestion.json"),
    # 旧実装CapacitatedVehicleRoutingProblemはTruckDispatcher（CP Optimizer化された新実装）に
    # 置き換え済みのため2026-07-09に削除（delete_old_cvrp.py実行、詳細はcleanup_legacy_domains系
    # スクリプトを参照）。ここへの再追加は不要（BASE_PROBLEM_MAPのCVRPは既にTruckDispatcherを指す）。
    # 2026-09-09: 他ドメインと違いTruckDispatcherは_baseline.json命名規則より前から
    # 存在するドメインのため、その名前のシナリオファイルは登録以来一度も存在せず
    # （_get_schema_example()がPath.exists()チェックで黙って"{}"にフォールバックする
    # ため実行時エラーにはならないが、TruckDispatcher流用の新規ドメイン登録時に
    # LLMへ渡るスキーマ例が常に空だった）。実在する登録済みシナリオのうち、
    # 構造がシンプルで綺麗なpromo_demoをスキーマ例として使う。
    "TruckDispatcher":                       str(_SCENARIOS_DIR / "truck_dispatcher_promo_demo.json"),
    # 2026-07-11: 基底パターンをTruckDispatcher/NurseShiftの2枚看板に整理する方針により、
    # GhostKitchen/ProjectPlanner/BinPacking/KubernetesScheduler/EventStaffing/StoreSite/
    # HospitalShiftPlanner/ProductionLotSchedulerは削除済み（YardPlanningのみ現状維持）。
    # 2026-07-13: NurseShift（週次夜勤上限なしの旧ベース版）はNurseShiftWeeklyCapの完全上位互換
    # であることが確認されたため完全削除し、2枚看板をTruckDispatcher/NurseShiftWeeklyCapに更新。
    "NurseShiftWeeklyCap":                   str(_SCENARIOS_DIR / "nurse_shift_weekly_cap_baseline.json"),
    # 2026-07-15: RCPSPの新フラグシップ（YardPlanningから独立した汎用ベース）として登録。
    # 機械柔軟割当なしのコアRCPSP（前後関係制約＋資源容量制約下でのメイクスパン最小化）。
    # BASE_PROBLEM_MAPのRCPSPをYardPlanningからこちらに張り替え済み（下記参照）。
    "LineChangeoverScheduler":               str(_SCENARIOS_DIR / "line_changeover_scheduler_baseline.json"),
}
# FOUR_DSL_DOMAINS（dsl4_route判定用）は2026-07-10に削除。唯一の消費箇所だった
# dsl4_route はコード全体を検索してもどこからも参照されておらず死んでいたため
# （Koshoshi承認済み）。

BASE_PROBLEM_MAP = {
    # 2026-07-15: YardPlanning（港湾コンテナ専用レガシー実装、汎用的な当てはめ禁止）から
    # LineChangeoverScheduler（新フラグシップ・汎用コアRCPSP実装）へ張り替え。YardPlanningは
    # BASE_PROBLEM_MAPから除去し、EXISTING_DOMAINS経由の既存ドメイン直接マッチ（港湾コンテナ
    # ヤードに文字通り一致する場合のみ）としてのみ存続する（_STAGE1A_SYSTEMの注意書き参照）。
    "RCPSP": "LineChangeoverScheduler",
    "CVRP":  "TruckDispatcher",  # 以前は CapacitatedVehicleRoutingProblem（旧ヒューリスティック）を
                                  # 指していたが、CP Optimizer化されたTruckDispatcherに向け直した。
    # 2026-07-11: Knapsack/WarehouseLocation/BinPackingの当時のマッピング先
    # (ProjectPlanner/GhostKitchen/BinPacking)は削除済みのため除去。
    # 基底パターンをTruckDispatcher/NurseShiftWeeklyCapの2枚看板に絞る方針のため、
    # 該当する基底問題の相談は new_domain（フル新規生成）に回る。
    # （NurseShiftWeeklyCapはCSPLib基底問題ではなく既存ドメイン直接マッチのため、
    #  ここではなく_STAGE1A_SYSTEMの「既存ドメインの判定基準」で扱う。）
}

# 拡張差分検出（Stage1a.5）で、既存ドメインの実装能力をLLMに渡すための
# ソースファイル位置。存在しない役割（例: GhostKitchenはconverterを使わない）は None。
# パターン3（copy_domain_for_extension）ではこの全ロールをコピー先に複製する。
BACKEND_ROOT_FOR_EXT = _BACKEND_ROOT
_DOMAIN_SOURCE_FILES: dict[str, dict[str, str | None]] = {
    "YardPlanning":     {"converter": str(_BACKEND_ROOT / "dsl_transformer" / "business_to_solver.py"),
                          "ui_converter": str(_BACKEND_ROOT / "dsl_transformer" / "solver_to_ui.py"),
                          "solver": None},
    "TruckDispatcher": {
        "converter": str(_BACKEND_ROOT / "dsl_transformer" / "truck_dispatcher_converter.py"),
        "ui_converter": str(_BACKEND_ROOT / "dsl_transformer" / "truck_dispatcher_ui_converter.py"),
        "solver":    str(_BACKEND_ROOT / "solvers" / "truck_dispatcher_solver.py"),
    },
    "NurseShiftWeeklyCap": {
        "converter": str(_BACKEND_ROOT / "dsl_transformer" / "nurse_shift_weekly_cap_converter.py"),
        "ui_converter": str(_BACKEND_ROOT / "dsl_transformer" / "nurse_shift_weekly_cap_ui_converter.py"),
        "solver":    str(_BACKEND_ROOT / "solvers" / "nurse_shift_weekly_cap_solver.py"),
    },
    "LineChangeoverScheduler": {
        "converter": str(_BACKEND_ROOT / "dsl_transformer" / "line_changeover_scheduler_converter.py"),
        "ui_converter": str(_BACKEND_ROOT / "dsl_transformer" / "line_changeover_scheduler_ui_converter.py"),
        "solver":    str(_BACKEND_ROOT / "solvers" / "line_changeover_scheduler_solver.py"),
    },
    # 2026-07-11: GhostKitchen/ProjectPlanner/BinPacking/EventStaffingは削除済み。
    # 2026-07-13: NurseShift（旧）はNurseShiftWeeklyCapに完全統合されたため削除。
    # 2026-07-15: LineChangeoverSchedulerを追加（RCPSP新フラグシップ）。
}

# Stage1a分類プロンプト（_STAGE1A_SYSTEM）が固定文言で言及している既存ドメイン名。
# DB起点の動的候補リストを組み立てる際、これらは既に固定文言でカバー済みのため除外する。
_STAGE1A_HARDCODED_DOMAINS: set[str] = {
    "YardPlanning", "TruckDispatcher", "NurseShiftWeeklyCap", "LineChangeoverScheduler",
}

# EXISTING_DOMAINS / _DOMAIN_SOURCE_FILES は元々
# 「今回のプロセス内で新規登録されたドメイン」を _update_domain_registry_from_diffs()
# 経由で追記するだけの設計だった。そのためプロセス再起動を挟むと、DB
# (dsl_definitions テーブル) やディスク上のファイルには実体が残っているのに
# これらの辞書からは消えてしまう問題があった（2026-07-09 HANDOFF 課題1）。
# 以下は起動後・初回利用時に一度だけDBから既存ドメイン一覧を取得し、
# ファイル規約（scenarios/{snake}_baseline.json 等）に基づいて辞書へ反映する。
_DOMAIN_REGISTRY_RECONCILED = False


def _reconcile_domain_registry_from_db() -> None:
    """DBに登録済みだが辞書に未反映のドメインを、ファイル規約から辞書へ反映する。"""
    try:
        from dsl_repository.repository import DslRepository
        repo        = DslRepository()
        definitions = repo.list_dsl_definitions()
    except Exception as e:
        logger.warning(f"[domain_registry] DB からのドメイン一覧取得に失敗（起動時反映をスキップ）: {e}")
        return

    seen = set()
    for d in definitions:
        pascal = d.get("problem_class")
        if not pascal or pascal in seen:
            continue
        seen.add(pascal)
        snake = _to_snake(pascal)

        if pascal not in EXISTING_DOMAINS:
            baseline_path = _SCENARIOS_DIR / f"{snake}_baseline.json"
            if baseline_path.exists():
                EXISTING_DOMAINS[pascal] = str(baseline_path)
                logger.info(f"[domain_registry] DB起点でEXISTING_DOMAINSに反映: {pascal}")

        converter_path    = _BACKEND_ROOT / "dsl_transformer" / f"{snake}_converter.py"
        ui_converter_path = _BACKEND_ROOT / "dsl_transformer" / f"{snake}_ui_converter.py"
        solver_path       = _BACKEND_ROOT / "solvers" / f"{snake}_solver.py"

        if pascal not in _DOMAIN_SOURCE_FILES:
            _DOMAIN_SOURCE_FILES[pascal] = {
                "converter":    str(converter_path)    if converter_path.exists()    else None,
                "ui_converter": str(ui_converter_path) if ui_converter_path.exists() else None,
                "solver":       str(solver_path)       if solver_path.exists()       else None,
            }


def _ensure_domain_registry_reconciled() -> None:
    """_reconcile_domain_registry_from_db() をプロセス内で一度だけ実行する。"""
    global _DOMAIN_REGISTRY_RECONCILED
    if _DOMAIN_REGISTRY_RECONCILED:
        return
    _DOMAIN_REGISTRY_RECONCILED = True
    _reconcile_domain_registry_from_db()


# ─────────────────────────────────────────────────────────────
# 軸(a)適合チェック用: 既存ドメインが実際に採用しているエンジンの事実抽出
#
# 背景（2026-07-30、Koshoshiとの相談）: 既存ドメインへの拡張(パターン3)を選ぶ前に
# 「この技術選択（CP Optimizer / MIP）は今回のヒアリング内容に合っているか」を
# 確認する必要がある。ただしこの確認の「正解源」を候補ドメイン自身のコードに置くと、
# そのコードが過去の誤った判断を含んでいた場合（例: StoreSiteを2026-07-11に
# CP Optimizerで4回生成しようとして失敗した事例）に自己言及的になり、
# 同じ誤りを検出できない。そのためget_domain_engine()は「候補が実際に何を
# 採用しているか」という事実の取得にのみ使う。「何を採用すべきか」の判断根拠は
# 別途、外部参照（CSPLIB_REFERENCE.md、CSPLib由来。旧primary_problems.md、2026-08-06統合）を使う。
#
# 2026-07-31追記: 当初はここで全既存ドメイン分の事実一覧
# （_build_domain_engine_facts_block()）を組み立ててStage1a分類プロンプトに
# 注入していたが、軸(a)適合チェック自体をStage1a.4（check_axis_a_fit()、
# 分類プロンプトから独立した専用LLM呼び出し）に分離したことに伴い、
# 全ドメイン一覧ではなく選ばれたbase_domain1件のみを都度get_domain_engine()で
# 参照する方式に変更した（分離の経緯・承認はDESIGN文書2026-07-31追記部分を
# 参照）。_build_domain_engine_facts_block()は利用箇所が無くなったため削除した。
# ─────────────────────────────────────────────────────────────

_ENGINE_CP_MARKERS  = ("docplex.cp", "from docplex.cp", "import docplex.cp")
_ENGINE_MIP_MARKERS = ("docplex.mp", "from docplex.mp", "import docplex.mp")


def get_domain_engine(problem_class: str) -> str:
    """
    既存ドメインが実際に採用しているソルバーエンジンを、solver.pyのimport文から
    機械的に判定する（"cp" / "mip" / "both" / "unknown"）。手作業のテーブルを
    持たないのは、devnotesの参照表がわずか数日で実コードとズレた前例が複数あり、
    手作業では同じドリフトが再発するため。真実の情報源は常に実コードそのものにする。
    """
    _ensure_domain_registry_reconciled()
    src = _DOMAIN_SOURCE_FILES.get(problem_class, {})
    solver_path = src.get("solver")
    if not solver_path:
        return "unknown"
    try:
        code = Path(solver_path).read_text(encoding="utf-8")
    except Exception:
        return "unknown"

    has_cp  = any(m in code for m in _ENGINE_CP_MARKERS)
    has_mip = any(m in code for m in _ENGINE_MIP_MARKERS)
    if has_cp and has_mip:
        return "both"
    if has_cp:
        return "cp"
    if has_mip:
        return "mip"
    return "unknown"


def _get_dynamic_stage1a_candidates() -> list[dict]:
    """
    DBに登録済みだが _STAGE1A_HARDCODED_DOMAINS（Stage1aプロンプトの固定文言）に
    含まれないドメインを、Stage1a分類の追加候補として返す。
    各要素: {"problem_class": str, "description": str}
    """
    _ensure_domain_registry_reconciled()
    try:
        from dsl_repository.repository import DslRepository
        repo        = DslRepository()
        definitions = repo.list_dsl_definitions()
    except Exception as e:
        logger.warning(f"[stage1a] 動的候補ドメイン取得に失敗（固定4ドメインのみで分類を続行）: {e}")
        return []

    seen, candidates = set(), []
    for d in definitions:
        pascal = d.get("problem_class")
        if not pascal or pascal in seen or pascal in _STAGE1A_HARDCODED_DOMAINS:
            continue
        seen.add(pascal)

        # 実体ガード（2026-07-16）: dsl_definitionsに行があっても、対応する
        # converter/solverファイルが実際にディスク上に無ければ既存ドメインの
        # 選択肢として出さない。DB行だけ先にできてコード実体が無い「幽霊ドメイン」が
        # Stage1aを誤誘導する事故（MeetingRoomScheduler登録時に実機で発生）を防ぐ。
        snake          = _to_snake(pascal)
        converter_path = _BACKEND_ROOT / "dsl_transformer" / f"{snake}_converter.py"
        solver_path    = _BACKEND_ROOT / "solvers" / f"{snake}_solver.py"
        if not (converter_path.exists() and solver_path.exists()):
            logger.warning(
                f"[stage1a] '{pascal}' はDBに登録されているが実装ファイルが無いため、"
                "既存ドメイン候補から除外します（幽霊ドメイン対策）。"
            )
            continue

        candidates.append({
            "problem_class": pascal,
            "description":   (d.get("description") or "").strip(),
        })
    return candidates


_PRIMARY_PROBLEMS_PATH = _DOCS_DIR / "CSPLIB_REFERENCE.md"


def _to_snake(name: str) -> str:
    return re.sub(r"([A-Z])", r"_\1", name).lower().lstrip("_")


# --- re-export the full original module surface (external callers use
# `from domain_generator import X` / `domain_generator.X` / `dg.X` for
# both public and "private" underscore-prefixed names) ---
from .utils import (
    _ensure_dsl_definition,
    _infer_schema_from_dsl,
    _resolve_path,
    compute_diff,
    get_default_attachment_info,
    refresh_scenario_from_upload,
)
from .static_checks import (
    _BIG_M_RATIO_THRESHOLD,
    _BUILD_TABLE_SECTION_CALL_RE,
    _DOCPLEX_MP_IMPORT_RE,
    _FIELD_CHECK_IGNORE_KEYS,
    _GET_VALUE_VAR_RE,
    _HAS_ALPHA_RE,
    _I18N_HARDCODED_JA_RE,
    _LEADING_COEF_RE,
    _MINIMIZE_CALL_RE,
    _NO_OVERLAP_CALL_RE,
    _OPTIONAL_INTERVAL_ABSENT_VALUE_CMP_RE,
    _SEQUENCE_VAR_ASSIGN_RE,
    _TABLE_SECTIONS_KEY_ASSIGN_RE,
    _TRAILING_COEF_RE,
    _UNNAMED_EXPR_ASSIGN_RE,
    _check_big_m_objective,
    _check_i18n_message_coverage,
    _check_mip_self_verification,
    _check_no_overlap_without_sequence_var,
    _check_objective_coverage,
    _check_optional_interval_absent_value,
    _check_table_sections_wiring,
    _check_unnamed_expr_get_value,
    _check_unwrapped_minimize,
    _extract_accessed_keys,
    _extract_balanced_call_args,
    _extract_dict_literal_keys,
    _extract_json_keys,
    _split_top_level_plus_terms,
    _strip_line_comments,
    _term_coefficient,
    check_converter_solver_field_consistency,
    check_dsl_converter_field_consistency,
    check_dsl_solver_field_consistency,
)
from .build_checks import (
    _CPO_SANITIZE_RULES,
    _CPO_WARN_PATTERNS,
    _ensure_repomix,
    _run_backend_import_check,
    _run_backend_pycheck,
    _run_frontend_typecheck,
    _sanitize_solver_code,
)
from .hearing_gaps import (
    _HEARING_DSL_COVERAGE_INCREMENTAL_SYSTEM,
    _HEARING_DSL_COVERAGE_SYSTEM,
    build_code_diff,
    detect_hearing_dsl_gaps,
    detect_hearing_dsl_gaps_incremental,
    extract_domain_artifacts_from_diffs,
)
from .humanize import (
    _FINDING_CATEGORY_HINTS,
    _humanize_call_with_retry,
    _humanize_exception_findings,
    _humanize_required_gap_findings,
    humanize_technical_findings,
    scan_diffs_for_warnings,
)
from .registry_patch import (
    _patch_app_py_legacy,
    _patch_converter_dispatch,
    _patch_home_screen_tag_map,
    _register_scenarios,
    _to_pascal,
    _update_domain_registry_from_diffs,
)
from .stage1a import (
    _AXIS_A_FIT_SYSTEM,
    _CP_TOKEN_RE,
    _MIP_TOKEN_RE,
    _STAGE1A5_SYSTEM,
    _STAGE1A_SYSTEM,
    _load_primary_problems,
    _resolve_hedged_technical_directive,
    check_axis_a_fit,
    check_domain_exists,
    classify_problem,
    detect_extension_gaps,
)
from .family_reference import (
    _CP_TECH_RE,
    _CSPLIB_ID_RE,
    _FORMULATION_DIRECTIVE_RE,
    _FORMULATION_DIRECTIVE_REQUIRED_KEYS,
    _MIP_TECH_RE,
    _STRUCTURAL_REQUIREMENTS_SYSTEM,
    _detect_solver_technology,
    _extract_csplib_id,
    _extract_formulation_directive,
    _load_csplib_reference_problems,
    _summarize_registered_domain_structure,
    check_family_technology_conformance,
    derive_structural_requirements,
    lookup_family_reference,
)
from .stage1b import (
    _STAGE1B_SYSTEM,
    _domain_color,
    _get_schema_example,
    _validate_scenario_capacity,
    generate_scenarios_from_schema,
)
from .stage2 import (
    _CONVERTER_SOLVER_CONTRACT,
    _CPO_FORBIDDEN_PATTERNS,
    _I18N_MESSAGE_DICT_INSTRUCTION,
    _SOLVER_OUTPUT_SPEC,
    _STAGE2_CACHE_SPLIT,
    _build_scenario_registrations_from_diffs,
    _build_stage2_prompt,
    _parse_marker_output,
    generate_domain_files,
)
from .apply import (
    _rename_domain_symbols,
    apply_domain_files,
    apply_scenarios,
    copy_domain_for_extension,
)
from .extensions import (
    _STAGE2LITE_SYSTEM,
    _STAGE2LITE_V2_SYSTEM,
    _find_function_block,
    _parse_function_output,
    _reindent_code,
    apply_extension_files,
    apply_function_replacements,
    generate_and_apply_extensions,
    generate_extension_files,
)
from .field_repair import (
    _FIELD_REPAIR_SYSTEM,
    _FIELD_REPAIR_SYSTEM_V2,
    _apply_function_patches_in_memory,
    _call_field_repair_llm,
    _call_field_repair_llm_v2,
    _field_check_total,
    _is_field_check_clean,
    attempt_field_consistency_repair,
)
from .hearing import (
    _normalize_base_domain,
    interpret_hearing,
)
from .gate2 import (
    _CE_LIMIT_STRESS_INFLATE_FIELD,
    _DEGENERATE_COVERAGE_RATE_THRESHOLD,
    _DOMAIN_VALIDATORS,
    _check_baseline_degenerate_solution,
    _inflate_list_field,
    _register_default_domain_validators,
    cleanup_dynamic_check_files,
    refresh_diffs_from_disk,
    run_gate2_ce_limit_stress_check,
    run_gate2_checks,
    run_gate2_dynamic_verification,
    run_post_registration_fix,
    write_files_for_dynamic_check,
)
