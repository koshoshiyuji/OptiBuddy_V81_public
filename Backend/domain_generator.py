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

_PROJECT_ROOT  = Path(__file__).parent.parent
_BACKEND_ROOT  = Path(__file__).parent
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
    "TruckDispatcher":                       str(_SCENARIOS_DIR / "truck_dispatcher_baseline.json"),
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

# 目的関数の意味論的な見落としを検出するパターン（自動修正はしない、ログのみ）
# 例: 「urgent フラグを持つアイテムのみ未割り当てペナルティを付与し、
#      それ以外のアイテムには未割り当てペナルティが存在しない」という
#      実際に production_lot_scheduler で発生したパターンを検出する。
def _check_objective_coverage(code: str, path: str) -> str | None:
    """
    目的関数（penalty_terms 等への add）内に 'urgent' 等の条件分岐で
    presence_of / is_assigned 系のペナルティが「一部アイテムのみ」に
    限定されていないかを簡易チェックし、該当すれば警告ログを出す。

    完全な静的解析ではなく、ヒューリスティックな検出。
    誤検知の可能性があるため、自動修正は行わずログ警告のみとする。
    """
    # 「if ... urgent ...: ... presence_of(...) ...」のような
    # urgent 限定ブロック内で presence_of / is_assigned ペナルティが使われ、
    # かつ urgent 条件を伴わない同種のペナルティ加算が見当たらない場合に警告
    urgent_block_pattern = re.compile(
        r'for\s+\w+\s+in\s+\w+:\s*\n'
        r'(?:[^\n]*\n){0,3}?'
        r'[^\n]*if\s+not\s+\w+\.get\(["\']urgent["\'][^\n]*:\s*\n'
        r'[^\n]*continue',
    )
    presence_penalty_pattern = re.compile(r'presence_of\(.*?\)')

    if urgent_block_pattern.search(code) and presence_penalty_pattern.search(code):
        # urgent 限定の continue パターンが存在し、かつ presence_of ベースの
        # ペナルティ計算がその近くにある場合、非urgentアイテムへの
        # 同種ペナルティが欠落していないか要確認、という警告を出す
        return (
            f"[sanitize] {path}: 目的関数内で 'urgent' フラグにより処理をスキップして"
            f"いる箇所と presence_of() ベースのペナルティ計算が検出されました。"
            f"非urgentアイテムに対する同種の未割り当てペナルティが"
            f"目的関数から漏れていないか確認してください"
            f"（過去に urgent 限定の未割り当てペナルティのみ実装され、"
            f"非urgentアイテムが全て未割り当てになる不具合が発生しています）。"
        )
    return None


# 禁止パターン1: no_overlap に interval_var のリストと transition_matrix を直接渡している
# （sequence_var を経由していない）ことを検出する。
#
# [2026-07-16 修正] 元の実装は `mdl.no_overlap(引数1, 引数2)` という2引数呼び出しの形
# だけで判定しており、正しい書き方（`seq = mdl.sequence_var(...); mdl.no_overlap(seq, tm)`）
# も同じ形に見えるため常に誤検知していた（truck_dispatcher_solver.py で確認）。
# 第1引数が mdl.sequence_var() で作られた変数であれば正しい呼び方とみなし、
# それ以外（interval_varのリストを直接渡している等）の場合のみ警告する。
#
# [2026-07-28 修正] 今度は逆に見逃し（false negative）が発覚。第2引数が
# `mdl.transition_matrix(dist_matrix)` のようにその場で組み立てた関数呼び出し
# （ドット・括弧を含む）だと `\w+` にマッチせず、チェック自体が発火しなかった
# （NurseShiftEval registration で実際に発生 — `mdl.no_overlap(itvs,
# mdl.transition_matrix(dist_matrix))` という禁止パターン1そのものの誤用が
# 静的チェックをすり抜け、Gate2でも拾われず、実行時に毎回AssertionErrorで
# 落ちるバグとして初めて発覚した）。第2引数の中身は判定に使わず、単に
# 「カンマの後に何か（＝2引数以上）がある」ことだけを見て発火させ、第1引数が
# sequence_var かどうかだけで判定するように変更。第2引数の残り全体は警告文の
# 表示用にだけ緩く拾う（構文的に閉じていなくても表示上は問題ない）。
_SEQUENCE_VAR_ASSIGN_RE = re.compile(r'(\w+)\s*=\s*mdl\.sequence_var\(')
_NO_OVERLAP_CALL_RE     = re.compile(r'mdl\.no_overlap\(\s*(\w+)\s*,\s*(.+)$', re.MULTILINE)


def _check_no_overlap_without_sequence_var(code: str, path: str) -> list[str]:
    scan_code = _strip_line_comments(code)
    sequence_var_names = set(_SEQUENCE_VAR_ASSIGN_RE.findall(scan_code))
    warnings = []
    for m in _NO_OVERLAP_CALL_RE.finditer(scan_code):
        first_arg = m.group(1)
        if first_arg in sequence_var_names:
            continue
        warnings.append(
            f"{path}: no_overlap({first_arg}, {m.group(2)}) が検出されました（禁止パターン1）。"
            f"第1引数が mdl.sequence_var() で作られた変数だと確認できませんでした。"
            f"interval_var のリストを直接渡している場合は sequence_var 経由に変更してください。"
        )
    return warnings


def _strip_line_comments(code: str) -> str:
    """各行の最初の '#' 以降を雑に除去する（コード中で言及・説明しているだけの
    コメント行を、実際の違反コードと誤検知しないようにするための簡易前処理）。
    文字列リテラル内の '#' も区別なく切り捨てる粗い近似だが、ソルバーコードで
    '#' を含む文字列リテラルは稀なため実用上のヒューリスティックとして許容する。
    """
    return "\n".join(line.split("#", 1)[0] for line in code.splitlines())


# 禁止パターン6: mdl.minimize()/mdl.maximize() が mdl.add() で包まれていないかを検出する
# （HANDOFF_2026-07-15b 論点1-3で判明: プロンプトには追記済みだったが機械的検出が
#  存在しなかった。自動修正は括弧の対応を壊すリスクがあるため警告のみとする）
#
# 2026-07-20: このチェックはdocplex.cp.model（CP Optimizer）のAPI規約
# （mdl.add(mdl.minimize(...))と書く必要がある）を前提にしている。
# docplex.mp.model（CPLEX MP、StoreSiteで導入）ではmdl.minimize(obj)を
# 直接呼ぶのが正しい書き方であり、この前提が成立しないため恒常的に誤検知する
# （実機StoreSite登録で3回連続誤検知を確認、docs/ENGINEERING_LOG.md 2026-07-20参照）。
# docplex.mp.modelのimportを検出したファイルはこのチェック自体をスキップする。
_MINIMIZE_CALL_RE = re.compile(r'mdl\.(minimize|maximize)\(')
_DOCPLEX_MP_IMPORT_RE = re.compile(r'from\s+docplex\.mp\.model\s+import|import\s+docplex\.mp\.model')


def _check_unwrapped_minimize(code: str, path: str) -> list[str]:
    scan_code = _strip_line_comments(code)
    if _DOCPLEX_MP_IMPORT_RE.search(scan_code):
        # docplex.mp.model（CPLEX MP）はmdl.add()で包まない書き方が正しいため対象外。
        return []
    warnings = []
    for m in _MINIMIZE_CALL_RE.finditer(scan_code):
        preceding = scan_code[max(0, m.start() - 40): m.start()]
        # 直前（空白・改行を無視）が "mdl.add(" で終わっていれば正しく包まれている
        if re.search(r'mdl\.add\(\s*$', preceding):
            continue
        warnings.append(
            f"{path}: mdl.{m.group(1)}(...) が mdl.add() で包まれていない可能性があります"
            f"（禁止パターン6）。mdl.add(mdl.{m.group(1)}(...)) の形にしないと目的関数が"
            f"ソルバーに一度も登録されません。"
        )
    return warnings


# 禁止パターン6b: mdl.max()/mdl.min()/mdl.sum() で組み立てた無名式を、そのまま
# msol.get_value() でクエリしていないかを検出する（名前付き変数/KPIでないため
# 「Variable or KPI '...' not in the solution」で失敗する既知バグ）
_UNNAMED_EXPR_ASSIGN_RE = re.compile(r'(\w+)\s*=\s*mdl\.(?:max|min|sum)\(')
_GET_VALUE_VAR_RE       = re.compile(r'msol\.get_value\(\s*(\w+)\s*\)')


def _check_unnamed_expr_get_value(code: str, path: str) -> list[str]:
    scan_code = _strip_line_comments(code)
    unnamed_expr_vars = set(_UNNAMED_EXPR_ASSIGN_RE.findall(scan_code))
    if not unnamed_expr_vars:
        return []
    warnings = []
    for m in _GET_VALUE_VAR_RE.finditer(scan_code):
        var_name = m.group(1)
        if var_name in unnamed_expr_vars:
            warnings.append(
                f"{path}: msol.get_value({var_name}) が検出されました（禁止パターン6b）。"
                f"{var_name} は mdl.max/min/sum() で組み立てた名前無し式のため、"
                f"get_value() は『Variable or KPI が solution に無い』で失敗します。"
                f"既に get_var_solution() で抽出済みの schedule から直接計算するか、"
                f"msol.get_objective_value() を使ってください。"
            )
    return warnings


# ─────────────────────────────────────────────────────────────
# Gate2静的チェック: Big-M近似（多目的の優先順位偽装）検出
#
# 背景（2026-07-22追加）: lexicographicモード（複数目的の優先順位付け）が
# 必要な場面で、mdl.minimize_static_lex() ではなく極端に桁の異なる係数を
# 使った単一目的関数（例: mdl.minimize(1000000*a + b)）で優先順位を偽装する
# 「Big-M近似」が過去に使われ、下位優先度の目的の最適性が数値的な許容誤差で
# 検証不能になる問題が発生した（Key Learnings、objective_terms.py の
# 「Big-M代替の禁止」節参照）。
#
# 検出方針: mdl.minimize()/mdl.maximize() 呼び出し内の式を、トップレベルの
# '+' で加算項に分割し、各項の先頭/末尾の数値リテラルを明示係数として抽出する
# （例: "1000000 * a + b" → 項["1000000 * a", "b"] → 係数[1000000, 1(暗黙)]）。
# 明示係数が無くても変数を含む項は暗黙係数1として扱う（Big-Mパターンの典型形
# 「大きい係数付きの項 + 係数無しの項」を見逃さないため）。最大/最小比が
# 閾値以上であればBig-M近似の疑いとして警告する。
# _check_objective_coverage 等と同様、ヒューリスティックな検出であり誤検知の
# 可能性があるため自動修正は行わずログ警告のみとする（_CPO_WARN_PATTERNS/
# 禁止パターンには追加せず、needs_confirmationの警告に合流させるに留める。
# ブロックするかどうかは別途の判断が必要）。
#
# docplex.mp.model（CPLEX MP）はmdl.minimize(obj)を直接呼ぶのが正しい書き方
# であり、_check_unwrapped_minimize と同じ理由で対象外とする
# （StoreSiteで誤検知確認済み、docs/ENGINEERING_LOG.md 2026-07-20参照）。

_BIG_M_RATIO_THRESHOLD = 1000
_LEADING_COEF_RE = re.compile(r'^(\d[\d_]*(?:\.\d+)?(?:[eE][+-]?\d+)?)\s*\*(?!\*)')
_TRAILING_COEF_RE = re.compile(r'(?<!\*)\*(?!\*)\s*(\d[\d_]*(?:\.\d+)?(?:[eE][+-]?\d+)?)$')
_HAS_ALPHA_RE = re.compile(r'[A-Za-z_]')


def _extract_balanced_call_args(code: str, open_paren_idx: int) -> str | None:
    """code[open_paren_idx] が '(' である前提で、対応する閉じ括弧までの中身
    （括弧自体は含まない）を返す。文字列リテラル内の括弧は考慮しない簡易実装
    （目的関数の係数式に文字列リテラルが含まれることは実用上ほぼ無いため許容）。"""
    depth = 0
    for i in range(open_paren_idx, len(code)):
        ch = code[i]
        if ch == '(':
            depth += 1
        elif ch == ')':
            depth -= 1
            if depth == 0:
                return code[open_paren_idx + 1:i]
    return None


def _split_top_level_plus_terms(expr: str) -> list[str]:
    """expr をトップレベル（括弧の外）の '+' で加算項に分割する。
    括弧/角括弧/波括弧の深さを追跡し、内部の '+' では分割しない。"""
    terms = []
    depth = 0
    start = 0
    for i, ch in enumerate(expr):
        if ch in '([{':
            depth += 1
        elif ch in ')]}':
            depth -= 1
        elif ch == '+' and depth == 0:
            terms.append(expr[start:i])
            start = i + 1
    terms.append(expr[start:])
    return [t.strip() for t in terms if t.strip()]


def _term_coefficient(term: str) -> float | None:
    """加算項1つから係数を推定する。
    - 先頭/末尾に明示的な "数値 *" / "* 数値" があればその値。
    - 明示係数が無く、変数/式らしき内容（英字を含む）があれば暗黙係数1。
    - 変数を含まない純粋な数値定数項は係数比較の対象外として None を返す
      （目的関数のオフセット定数であり「複数目的の重み」ではないため）。
    符号（先頭の '-'）は絶対値化して係数の大きさの比較にのみ使う簡易近似。
    """
    term = term.strip()
    if term.startswith('-'):
        term = term[1:].strip()
    if not term:
        return None

    m = _LEADING_COEF_RE.match(term)
    if m:
        try:
            return float(m.group(1).replace('_', ''))
        except ValueError:
            return None

    m = _TRAILING_COEF_RE.search(term)
    if m:
        try:
            return float(m.group(1).replace('_', ''))
        except ValueError:
            return None

    if _HAS_ALPHA_RE.search(term):
        return 1.0

    return None


def _check_big_m_objective(code: str, path: str) -> list[str]:
    """
    mdl.minimize()/mdl.maximize() 呼び出し内の各加算項の係数（暗黙の1を含む）
    の最大/最小比を調べ、_BIG_M_RATIO_THRESHOLD 以上であればBig-M近似
    （多目的の優先順位を単一の重み付き合算で偽装するパターン）の疑いとして
    警告を返す。

    lexicographicモードで書くべき箇所を weighted_sum + 極端な係数差で
    代替していないかの確認を促すのが目的。優先順位付けが必要なら
    solvers.base.objective_terms.build_lexicographic_objective_exprs() 経由で
    mdl.minimize_static_lex([...]) を使うべき、という既存の設計方針
    （objective_terms.py参照）と対になっている。
    """
    scan_code = _strip_line_comments(code)
    if _DOCPLEX_MP_IMPORT_RE.search(scan_code):
        return []

    warnings: list[str] = []
    for m in _MINIMIZE_CALL_RE.finditer(scan_code):
        # m は 'mdl.minimize(' / 'mdl.maximize(' に一致し、末尾が '(' なので
        # m.end() - 1 がその開き括弧の位置になる。
        # （なお 'mdl.minimize_static_lex(' は '(' の直前が 'minimize' ではなく
        #  '_static_lex' なのでこの正規表現自体にマッチしない＝自動的に対象外）
        open_idx = m.end() - 1
        args = _extract_balanced_call_args(scan_code, open_idx)
        if args is None:
            continue

        coefs: list[float] = []
        for term in _split_top_level_plus_terms(args):
            c = _term_coefficient(term)
            if c is not None and c > 0:
                coefs.append(c)

        if len(coefs) < 2:
            continue
        ratio = max(coefs) / min(coefs)
        if ratio >= _BIG_M_RATIO_THRESHOLD:
            warnings.append(
                f"{path}: {m.group(0)}...) 内で係数比 約{ratio:.0f}倍（係数例: "
                f"{sorted(set(coefs))[:6]}）が検出されました。複数の目的の優先順位を"
                f"極端に異なる係数の重み付き合算（Big-M近似）で表現している疑いがあります。"
                f"優先順位付けが意図であれば、objective_terms.py の "
                f"build_lexicographic_objective_exprs() 経由で mdl.minimize_static_lex([...]) "
                f"を使ってください（Big-M近似は下位目的の最適性が数値的な許容誤差で検証不能に"
                f"なる既知の問題があります。意図的な重み付けで問題なければ無視して構いません）。"
            )
    return warnings


# ─────────────────────────────────────────────────────────────
# Gate2静的チェック: optional interval_var の absent値誤用検出
#
# 背景（2026-08-10追加）: ProductionLineSequencing登録時に実機発生。
# mdl.start_of(var, 0) のように第2引数（absent時のデフォルト値）を指定した
# 呼び出しを、そのまま mdl.add(... == ...) 等の等式・不等式制約に使うと、
# 「presentならX、absentならデフォルト値」という意味になるため、absent（不採用）
# 候補すべてに事実上presentを強制してしまう。1バッチにつき複数の候補
# interval_varがあり「exactly 1つだけpresent」であるべきところ、デフォルト値と
# 一致しない候補が軒並みpresent強制され、exactly-1制約と矛盾して必ずinfeasibleに
# なった（実機再現済み）。big_m_warnings/unused_in_solver_warningsと同じ理由
# （誤検知パターンが薄く、実際に業務事故を起こした既知パターン）でblocking側に
# 分離する（Koshoshi合意、2026-08-10）。
# ─────────────────────────────────────────────────────────────

_OPTIONAL_INTERVAL_ABSENT_VALUE_CMP_RE = re.compile(
    r'mdl\.(?:start_of|end_of|size_of|length_of)\(\s*\w+\s*,\s*[^()]+?\)\s*(?:==|<=|>=|!=|<|>)'
    r'|(?:==|<=|>=|!=|<|>)\s*mdl\.(?:start_of|end_of|size_of|length_of)\(\s*\w+\s*,\s*[^()]+?\)'
)


def _check_optional_interval_absent_value(code: str, path: str) -> list[str]:
    """
    mdl.start_of/end_of/size_of/length_of(var, <absent時デフォルト値>) の
    2引数呼び出しが、比較演算子（==等）と直接組み合わされていないかを検出する。

    ヒューリスティックな検出であり、比較対象が別の変数に一度代入されてから
    比較される間接的なケースまでは追えない（他の_check_*関数と同じ粒度）。
    """
    scan_code = _strip_line_comments(code)
    warnings: list[str] = []
    for m in _OPTIONAL_INTERVAL_ABSENT_VALUE_CMP_RE.finditer(scan_code):
        warnings.append(
            f"{path}: {m.group(0)!r} のように、start_of/end_of/size_of/length_of の"
            f"第2引数（absent時のデフォルト値）を指定した呼び出しが比較演算子と直接組み合わされて"
            f"います。この形は『presentならX、absentならデフォルト値』を意味するため、mdl.add()内の"
            f"等式・不等式にそのまま使うと、absent（不採用）候補すべてに事実上presentを強制して"
            f"しまう既知の実害パターンです（ProductionLineSequencing登録時に実機発生、"
            f"『exactly 1 present』制約と矛盾し必ずinfeasibleになった）。interval_var生成時に"
            f"start=<値>等を固定値として渡すか、mdl.if_then(mdl.presence_of(var), "
            f"mdl.start_of(var) == <値>) のようにpresence条件付きにしてください"
            f"（第2引数なしのstart_of(var)を使うこと）。"
        )
    return warnings


# ─────────────────────────────────────────────────────────────
# Gate2静的チェック（blocking）: Tier1 — MIP解の自己検証（is_valid_solution）
#
# 2026-09-01追加（Koshoshi合意）: MIPドメイン（docplex.mp採用）で、solve()後に
# is_valid_solution()による自己検証をしているかを検出する。MIPソルバーの実装
# によっては、solve()自体は成功してもソルバー内部のバグや数値誤差により
# 制約違反のある解を返すことがまれにある（実装例: solvers/store_site_solver.py）。
# required_gap_warningsと同じ扱い（blocking・force-apply可・非humanize）。
# ─────────────────────────────────────────────────────────────

def _check_mip_self_verification(code: str, path: str) -> list[str]:
    """
    docplex.mp（MIP）採用ドメインで、is_valid_solution()による解の自己検証が
    見当たらない場合に警告する。ヒューリスティックな検出（他の_check_*関数と
    同じ粒度）で、importの有無と呼び出し文字列の有無だけを見る。
    """
    scan_code = _strip_line_comments(code)
    if not _DOCPLEX_MP_IMPORT_RE.search(scan_code):
        return []
    if "is_valid_solution(" in scan_code:
        return []
    return [
        f"{path}: docplex.mp（MIP）を使用していますが、is_valid_solution()による解の"
        f"自己検証が見当たりません。MIPソルバーの実装によっては、solve()自体は成功しても"
        f"制約違反のある解をまれに返すことがあります。solve()直後に "
        f"sol.is_valid_solution(tolerance=1e-6) を呼び出し、Falseの場合はissueとして"
        f"報告するようにしてください（実装例: solvers/store_site_solver.py）。"
    ]


# ─────────────────────────────────────────────────────────────
# Gate2静的チェック（advisory）: i18nメッセージカバレッジ
#
# 背景（2026-08-11追加）: _I18N_MESSAGE_DICT_INSTRUCTION（Stage2プロンプト、
# is_dsl4のnew_domain登録に埋め込み）でBackend/i18n/{snake}_messages.py 経由の
# t()呼び出しを指示するようにしたが、プロンプト指示だけでは実効性が保証されない
# （LLMがtitle/message/label等に日本語文字列を直書きする既存の生成傾向に
# 戻ってしまう可能性がある）。本チェックはその検知用。
#
# advisory（非ブロッキング）とする理由: 「意味的カバレッジ判定」（ヒアリング
# 要件が実装に反映されているか等）と同種の性質の指摘であり、start_of誤用
# バグやBig-M近似のような客観的に実害が確定しているコード欠陥ではない。
# force_apply（指摘を残したまま登録する）で問題なく通せる（Koshoshi合意、
# 2026-08-11）。誤検知パターンも未検証の新規チェックである。
# ─────────────────────────────────────────────────────────────

_I18N_HARDCODED_JA_RE = re.compile(
    r'''["'](?:title|message|label|sub|unit)["']\s*:\s*'''
    r'''f?(["'])(?:(?!\1).)*?[぀-ヿ一-鿿](?:(?!\1).)*?\1''',
    re.DOTALL,
)


def _check_i18n_message_coverage(solver_code: str | None, ui_converter_code: str | None, snake: str) -> list[str]:
    """
    title/message/label/sub/unit キーに日本語文字列（f-string含む）が直書きされて
    おり、かつ Backend/i18n/{snake}_messages.py 経由の t() 呼び出し
    （`from i18n.{snake}_messages import t`）が見つからないファイルを検知する。
    """
    import_marker = f"from i18n.{snake}_messages import t"
    targets = [
        (f"Backend/solvers/{snake}_solver.py", solver_code),
        (f"Backend/dsl_transformer/{snake}_ui_converter.py", ui_converter_code),
    ]
    warnings: list[str] = []
    for path, code in targets:
        if not code:
            continue
        scan_code = _strip_line_comments(code)
        if import_marker in scan_code:
            continue
        if _I18N_HARDCODED_JA_RE.search(scan_code):
            warnings.append(
                f"[Gate2 i18nカバレッジチェック] {path}: title/message/label等のUI文言に"
                f"日本語文字列が直書きされているようですが、`{import_marker}` によるt()経由の"
                f"辞書参照が見つかりませんでした。Backend/i18n/{snake}_messages.py を作成（または"
                f"該当キーを追加）し、該当箇所を t(key, **params) 呼び出しに置き換えると日英"
                f"バイリンガル表示に対応できます（対応必須ではなくadvisoryです。既存の"
                f"nursing_workload_balance_messages.py がキー命名規則の実例です）。"
            )
    return warnings


# ─────────────────────────────────────────────────────────────
# Gate2静的チェック: table_sections配線チェック
#
# 背景（2026-07-22追加）: dsl_transformer/table_sections.py の
# build_table_section()/build_table_sections_from_issues() は「層A（共通コア）」
# として全ドメインのui_converterが経由すべきヘルパーだが、呼び出した結果を
# 実際に ui_dsl["table_sections"] として返し忘れる（変数に代入しただけ・
# 別の変数で上書きしてしまう等）と、ソルバーが計算した詳細テーブルが画面に
# 一切表示されないまま登録されてしまう。過去にGeneric4DSLView.tsx側が
# table_sectionsそのものを無視していた既知バグ（現在は修正済み、Key Learnings
# 「Generic4DSLView.tsx は新規ドメインの無言フォールバック」参照）と同種の、
# 「計算されたのに配線されていない」パターンをbackend側（{snake}_ui_converter.py）
# でも検出する。
#
# 検出方針: build_table_section(/build_table_sections_from_issues( の呼び出しが
# ファイル内にあるにもかかわらず、"table_sections" キーへの代入らしき記述
# （dict literalの "table_sections": ... ／ ui_dsl["table_sections"] = ... ／
# .setdefault("table_sections", ...)）が1つも見つからない場合に警告する。
# 他の_check_*関数と同じ粒度のヒューリスティックであり、制御フロー解析はしない
# （例えばif分岐の片方だけで配線されているケースまでは判定できない）。
# ヒューリスティックな検出のため自動修正は行わずログ警告のみとする。
# ─────────────────────────────────────────────────────────────

_BUILD_TABLE_SECTION_CALL_RE = re.compile(r'\bbuild_table_sections?(?:_from_issues)?\s*\(')
_TABLE_SECTIONS_KEY_ASSIGN_RE = re.compile(
    r'''["']table_sections["']\s*:'''             # dict literal: "table_sections": ...
    r'''|\[\s*["']table_sections["']\s*\]\s*='''  # ui_dsl["table_sections"] = ...
    r'''|\.setdefault\(\s*["']table_sections["']'''  # .setdefault("table_sections", ...)
)


def _check_table_sections_wiring(code: str, path: str) -> list[str]:
    """
    build_table_section()/build_table_sections_from_issues() を呼んでいるのに、
    その結果を ui_dsl の "table_sections" キーとして配線し忘れていないかを
    ヒューリスティックに検出する。{snake}_ui_converter.py にのみ適用する想定。
    """
    scan_code = _strip_line_comments(code)
    if not _BUILD_TABLE_SECTION_CALL_RE.search(scan_code):
        return []  # そもそもtable_sectionsを使っていないドメイン（対象外）
    if _TABLE_SECTIONS_KEY_ASSIGN_RE.search(scan_code):
        return []

    return [
        f"{path}: build_table_section()/build_table_sections_from_issues() が呼ばれていますが、"
        f"'table_sections' キーへの代入が見つかりませんでした。組み立てたテーブルが ui_dsl に"
        f"配線されず、画面に一切表示されないまま登録される疑いがあります"
        f"（過去にGenericResultTable側がtable_sectionsを無視していた既知バグと同種の"
        f"「計算されたが配線されていない」パターンです。意図的にtable_sectionsを使わない"
        f"設計であれば無視して構いません）。"
    ]


# ─────────────────────────────────────────────────────────────
# Gate2静的チェック: フィールド突き合わせ（converter出力キー ⇔ solver.py参照キー）
#
# 背景: HospitalShiftPlanner登録時に発覚した7件の不具合のうち5件
# （task["start"]/task["end"]参照ミス、required_skills丸ごと未実装、
#  availability無視、solver_time_limitキー名不一致、available_days依存）は、
# 「ソルバーが例外なく解を返す」ため feasible/infeasible 判定だけでは
# 検出できなかった。一方これらはすべて、converter.pyが実際に出力する
# 辞書キーとsolver.pyが参照する辞書キーの単純な集合比較で機械的に
# 検出可能だったことを確認済み（docs/DESIGN_2026-07-09_registration_gates_and_hard_soft.md
# 3-1節）。汎用ヒューリスティックであり厳密な意味解析ではない（ネストした
# オブジェクト単位の区別はしない、ファイル全体でのフラットな集合比較）。
# 誤検知はあり得るが、Gate2はneeds_confirmation（人間確認ゲート）への警告
# 追加であり登録を直接ブロックしないため許容する。シンプルさを優先し、
# ベンチマーク接続や自動デバッグループ等は今回のスコープに含めない。
# ─────────────────────────────────────────────────────────────

_FIELD_CHECK_IGNORE_KEYS = {
    "id", "name", "description", "label", "meta", "tag", "tagColor", "tag_color",
    "domain", "problem_class", "version", "issue_statuses", "config", "status",
    "feasible", "metadata", "solutions", "issues", "tasks", "staff",
    # 2026-07-24追加（解チェッカー、DESIGN_2026-07-21）: issue_statusesと同じ理由で
    # ここに追加する。いずれもconverter.pyが出力するドメイン固有DSLフィールドでは
    # なく、Gate2/app.py側がsolver_input/resultに後付けで注入・消費する
    # フレームワーク横断のプランビングキーのため、converter⇔solverのフィールド
    # 整合性チェックの対象外とする。
    #   _gate2_full_check: run_gate2_dynamic_verification()がsolver_inputに注入する
    #     フラグ（converterを経由しない）。現状MeetingRoomのみが参照するが、
    #     他ドメインが今後同様の非同期分岐チェックを追加した際にも同じ理由で
    #     誤検知するため、ドメイン個別ではなくここでグローバルに無視する。
    #   _deferred_checks: solverがresultに書き込む出力側キー（app.pyの
    #     _solve_4dsl_genericがpopして読む）。_extract_accessed_keys()が
    #     Subscriptのstore/load文脈を区別しないため、result["_deferred_checks"]=...
    #     という代入も「converterからの入力参照」として誤検知される
    #     （_extract_dict_literal_keys()はdictリテラル{...}のみを拾い、
    #     生成後にsubscript代入で追加したキーは自己参照抑制の対象にならない
    #     ため）。真の入力欠落バグではないためここで除外する。
    "_gate2_full_check", "_deferred_checks",
    # 2026-07-26追加（DSL⇔solver直接チェック新設に伴う既知の誤検知抑制）:
    #   _lns_used: TruckDispatcherのRouteDecomposer（route_decomposer.py、別ファイル）が
    #     result辞書へsubscript代入で設定するCE上限フォールバック発火フラグ。
    #     truck_dispatcher_solver.py側は result.get("_lns_used") で読むだけで、
    #     DSLにもconverterにも一度も現れない（正当にそうあるべき、ソルバー内部の
    #     実行時フラグのため）。_gate2_full_check/_deferred_checksと同種の
    #     「ドメインDSLフィールドではない、フレームワーク内部の配管キー」。
    "_lns_used",
}


def _extract_dict_literal_keys(tree: ast.AST) -> set:
    """辞書リテラルのキー（文字列定数のみ）を全て集める。"""
    keys = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Dict):
            for k in node.keys:
                if isinstance(k, ast.Constant) and isinstance(k.value, str):
                    keys.add(k.value)
    return keys


def _extract_accessed_keys(tree: ast.AST) -> set:
    """`.get("key", ...)` / `["key"]` の形で参照されているキーを全て集める。"""
    keys = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute) and node.func.attr == "get":
            if node.args and isinstance(node.args[0], ast.Constant) and isinstance(node.args[0].value, str):
                keys.add(node.args[0].value)
        if isinstance(node, ast.Subscript):
            sl = node.slice
            if isinstance(sl, ast.Constant) and isinstance(sl.value, str):
                keys.add(sl.value)
    return keys


def check_converter_solver_field_consistency(converter_code: str, solver_code: str) -> dict:
    """
    Gate2静的チェック（フィールド突き合わせ）本体。
    converter.py の出力dictキー集合と solver.py の参照キー集合をAST比較する。

    返り値:
      missing_in_converter: solverが参照するがconverterが一度も出力しないキー
                             （実行時KeyError／常時デフォルト値化の疑い）
      unused_in_solver:     converterが出力するがsolverが一度も参照しないキー
                             （宣言されたが無視される制約の疑い）
      suppressed_self_referential: missing_in_converter候補だったが、solver.py自身が
                             同名キーをdictリテラルとして構築している（＝solverが
                             自分で作った出力/中間dictを後で.get()/[...]で読み返している
                             だけ）と判定して除外したキー。誤検知として握りつぶすのではなく、
                             ここに残して監査可能にする（2026-07-18 MeetingRoom登録時、
                             assigned/meeting_id/meeting_name が誤ってmissing_in_converterに
                             出た件の恒久対応。詳細はENGINEERING_LOG.md参照）。
    """
    result = {"missing_in_converter": [], "unused_in_solver": [], "errors": [],
               "suppressed_self_referential": []}
    try:
        conv_tree = ast.parse(converter_code)
    except SyntaxError as e:
        result["errors"].append(f"converter.py 構文エラー: {e}")
        return result
    try:
        solver_tree = ast.parse(solver_code)
    except SyntaxError as e:
        result["errors"].append(f"solver.py 構文エラー: {e}")
        return result

    converter_emitted = _extract_dict_literal_keys(conv_tree) - _FIELD_CHECK_IGNORE_KEYS
    solver_accessed    = _extract_accessed_keys(solver_tree) - _FIELD_CHECK_IGNORE_KEYS
    solver_own_keys    = _extract_dict_literal_keys(solver_tree) - _FIELD_CHECK_IGNORE_KEYS

    raw_missing = solver_accessed - converter_emitted
    # solver.py自身がそのキーをdictリテラルとして組み立てている場合、
    # 「converterからの入力が欠けている」のではなく「solver内部で作った出力/中間
    # 構造を自己参照で読み返している」可能性が高い（真の入力欠落バグなら、
    # solver側が同じキーを自分でも生成している必然性はない）。
    self_referential = raw_missing & solver_own_keys

    result["missing_in_converter"] = sorted(raw_missing - self_referential)
    result["unused_in_solver"]     = sorted(converter_emitted - solver_accessed)
    result["suppressed_self_referential"] = sorted(self_referential)
    return result


# ─────────────────────────────────────────────────────────────
# Gate2静的チェック（track1拡張）: DSLシナリオ突き合わせ（DSL JSON ⇔ converter.py参照キー）
#
# 背景: HANDOFF_2026-07-15b「顧客ごとのOptiBuddy構想」議論より。従来の
# check_converter_solver_field_consistency() はconverter.py⇔solver.pyという
# コード同士の対称性しか見ておらず、「ヒアリング→DSL」「DSL→コード」という
# 手前2段の突き合わせは一切機械チェックされていなかった。本チェックはそのうち
# 「DSL→converter」の段を、既存のmissing_in_converter/unused_in_solverと同じ
# AST差集合パターンを1段前に伸ばすだけで実現する（新規機構ではなく既存機構の拡張）。
# ─────────────────────────────────────────────────────────────

def _extract_json_keys(obj) -> set:
    """DSLシナリオ（JSON dict/listの入れ子）から辞書キーを再帰的に全て集める。"""
    keys: set = set()

    def _walk(o):
        if isinstance(o, dict):
            for k, v in o.items():
                keys.add(k)
                _walk(v)
        elif isinstance(o, list):
            for item in o:
                _walk(item)

    _walk(obj)
    return keys


def check_dsl_converter_field_consistency(dsl_scenarios: list, converter_code: str) -> dict:
    """
    Gate2静的チェック（DSL⇔converter フィールド突き合わせ）本体。
    business DSL JSON（Stage1b生成シナリオ）が実際に持つキー集合の和集合と、
    converter.py が参照するキー集合をAST/JSON比較する。

    dsl_scenarios: 複数シナリオ（baseline/infeasible等）のdictのリスト。
                   シナリオ間でキーの有無が揺れることがあるため和集合で見る
                   （あるシナリオにしか出てこないオプショナルフィールドを
                   誤ってmissing_in_dslと報告しないため）。

    返り値:
      missing_in_dsl:      converterが参照するがDSLシナリオが一度も提供しないキー
                            （実行時は常にデフォルト値/空にフォールバックする疑い）
      unused_in_converter: DSLシナリオが持つがconverterが一度も参照しないキー
                            （ヒアリング→DSLで拾われたのにコードに反映されていない疑い。
                             check_converter_solver_field_consistencyのunused_in_solverと
                             同種だが、こちらはDSL層の「宣言されたが無視されるデータ」を
                             捉える）
    """
    result = {"missing_in_dsl": [], "unused_in_converter": [], "errors": []}
    try:
        conv_tree = ast.parse(converter_code)
    except SyntaxError as e:
        result["errors"].append(f"converter.py 構文エラー: {e}")
        return result

    dsl_provided: set = set()
    for scenario in dsl_scenarios:
        dsl_provided |= _extract_json_keys(scenario)
    dsl_provided -= _FIELD_CHECK_IGNORE_KEYS

    converter_accessed = _extract_accessed_keys(conv_tree) - _FIELD_CHECK_IGNORE_KEYS

    result["missing_in_dsl"]      = sorted(converter_accessed - dsl_provided)
    result["unused_in_converter"] = sorted(dsl_provided - converter_accessed)
    return result


# ─────────────────────────────────────────────────────────────
# Gate2静的チェック（2026-07-26追加）: DSLシナリオ⇔solver 直接フィールド突き合わせ
# （ネストキー対応、converterを経由しない）
#
# 背景: check_converter_solver_field_consistency()（converter⇔solver）と
# check_dsl_converter_field_consistency()（DSL⇔converter）は、いずれも
# converter.py自身のAST（dictリテラル・.get()/[...]アクセス）を経由してキー集合を
# 構築している。しかし work_limits のように「converterがキー名を一切書き換えず、
# ネスト構造ごとパススルーする」フィールドでは、個々のネストキー名
# （例: max_night_shifts_per_rolling_7days）がconverter.py自身のコード中に
# 一度も文字列として現れない。そのため、solver側がそのネスト構造の中を誤った
# キー名（例: weekly_night_shift_cap）で読んでいても、converterを経由する上記
# 2チェックのどちらもこれを検出できない（2026-07-12 NurseShiftWeeklyCap登録時の
# 「ハード制約が2重に無効化されていた」不具合1が、まさにこの抜け穴を素通りした。
# ENGINEERING_LOG.md 2026-07-12参照）。
#
# 本チェックはDSLシナリオの再帰的キー集合（_extract_json_keys、ネスト対応）と
# solver.pyの参照キー集合（_extract_accessed_keys）を直接突き合わせる。新規の
# 抽出ロジックは追加せず、既存のヘルパー関数の組み合わせを変えるだけで実現している
# （check_converter_solver_field_consistency と同じ自己参照抑制ロジックも流用）。
#
# 注意（実装上必須の除外）: converter.pyがDSLに存在しない新規フィールドを
# 「計算して」合成するケース（例: TruckDispatcherのdepot_open_min/locations/
# dist_matrixは、DSLのdepot.open_time/customers[].lat,lon等からconverterが
# 計算して初めて生成する。DSL側にこれらのキー名は一度も存在しない）は、
# 「パススルー時のキー名drift」とは全く別の正常系であり、誤検知させてはならない。
# これを区別するため、converter.py自身が該当キーをdictリテラルとして構築して
# いる場合（converter_emitted）は「converterが正しく合成した既知フィールド」として
# missing判定から除外する。これにより本チェックが本当に捉えたいのは
# 「DSLにもconverterにも一度も現れないのに、solverが参照しているキー」
# （＝パススルー構造でのネストキー名drift、または本当に何にも由来しない参照）
# だけに絞られる。
# ─────────────────────────────────────────────────────────────

def check_dsl_solver_field_consistency(dsl_scenarios: list, solver_code: str, converter_code: str = "") -> dict:
    """
    Gate2静的チェック（DSL⇔solver 直接フィールド突き合わせ、ネストキー対応）本体。

    converter_code: 省略可。渡された場合、converter.py自身が計算・合成して
                    dictリテラルとして出力しているキー（DSLには存在しないが
                    converterが正当に新規生成するフィールド）を既知の情報源として
                    扱い、missing判定から除外する。

    返り値:
      missing_in_dsl_for_solver: solverが参照しているが、DSLシナリオにもconverterの
                                  合成結果にも（ネスト構造も含め）一度も現れないキー
                                  （実行時は常にデフォルト値化する疑い）
      suppressed_self_referential: missing_in_dsl_for_solver候補だったが、solver.py
                                  自身が同名キーをdictリテラルとして構築している
                                  （＝自分で作った中間/出力構造を読み返しているだけ）
                                  と判定して除外したキー。
    """
    result = {"missing_in_dsl_for_solver": [], "suppressed_self_referential": [], "errors": []}
    try:
        solver_tree = ast.parse(solver_code)
    except SyntaxError as e:
        result["errors"].append(f"solver.py 構文エラー: {e}")
        return result

    dsl_provided: set = set()
    for scenario in dsl_scenarios:
        dsl_provided |= _extract_json_keys(scenario)

    converter_emitted: set = set()
    if converter_code:
        try:
            converter_emitted = _extract_dict_literal_keys(ast.parse(converter_code))
        except SyntaxError as e:
            result["errors"].append(f"converter.py 構文エラー: {e}")

    known_sources = (dsl_provided | converter_emitted) - _FIELD_CHECK_IGNORE_KEYS

    solver_accessed = _extract_accessed_keys(solver_tree) - _FIELD_CHECK_IGNORE_KEYS
    solver_own_keys = _extract_dict_literal_keys(solver_tree) - _FIELD_CHECK_IGNORE_KEYS

    raw_missing = solver_accessed - known_sources
    self_referential = raw_missing & solver_own_keys

    result["missing_in_dsl_for_solver"] = sorted(raw_missing - self_referential)
    result["suppressed_self_referential"] = sorted(self_referential)
    return result


# ─────────────────────────────────────────────────────────────
# Gate2 LLMチェック（track1拡張）: ヒアリング⇔DSL/コード カバレッジ判定
#
# 背景: 上記のAST差集合チェックは「機械的な」対称性しか見られないため、
# ヒアリング文中の要件（特に自由記述・任意節）がDSL/コードに意味的に反映
# されているかどうかまでは判定できない。この段はdetect_extension_gaps()
# （Stage1a.5、既存ドメインの拡張差分検出）と全く同じLLM比較パターンを、
# new_domain登録でStage2が生成した直後の成果物（DSLシナリオ・solver/converter）
# に対して適用する。新規の仕組みではなく、既存の比較パターンの適用対象を
# 「既存ドメインの実装」から「たった今生成された新規実装」に変えただけ。
# ─────────────────────────────────────────────────────────────

_HEARING_DSL_COVERAGE_SYSTEM = """\
あなたは、業務ヒアリング内容と、そこから生成された最適化ドメインの実装（DSLシナリオ・
converter.py・solver.py）を突き合わせ、ヒアリングで述べられた要件がどこまで実装に
反映されているかを判定する専門家です。

対象は主に new_domain（新規コード生成）登録時のGate2チェックです。
「ヒアリング文中の要件」を最優先の情報源とし、それぞれについて以下のいずれかに分類してください:

1. covered:  DSLシナリオまたはsolver/converterのコードに明確に反映されている要件
2. gap:      ヒアリング文中で明示的に述べられているが、DSLシナリオにもsolver/converterの
             コードにも一切反映が見当たらない要件（「できれば守りたいルール」等の任意節の
             要件も対象に含める。ただしヒアリング文中で「今回のスコープに含めない」
             「将来判断」等と明示的に除外宣言されている項目はgapに含めないこと）
3. deferred: ヒアリング文中で明示的にスコープ外・将来対応と宣言されている要件
             （除外を明記した記述がある場合のみ）

判定基準:
- 迷った場合はcoveredと判断せず、gapとして報告すること（見落としより過検知を許容する）
- ヒアリングのチェックボックス選択（[x]）で選ばれた分岐が実際にコードのロジックに
  反映されているかも確認すること（例: 複数目的の優先方式、不足時の解なし/緩和の扱い等）
- 「画面で確認したい情報」のような表示要件は、solverのkpi/metrics等の出力に
  該当データが含まれているかだけでなく、ui_converter.py（渡されている場合）が
  そのデータを実際に読み取ってKPIカード・テーブルに反映しているかまで確認すること。
  solver側にデータが存在していても、ui_converter.pyが別のキー名を参照していたり
  （旧ドメインのコードが誤って残っている等）、そのデータを一切使っていない場合は
  coveredと判定せずgapとして報告すること

各要件について、それが記載されているヒアリング節が「必須」節か「任意」節かも判定する
こと（多くのヒアリングシートは節見出しに「（必須）」「（任意）」と明記されている。
明記が無い場合は、業務の中核（1〜4節相当: 業務概要・割り当て構造・最適化目的・
守らなければならないルール）は"required"、それ以外（5節以降の「できれば」「画面表示」
「特殊要件」等）は"optional"と推定してよい）。この判定はgap/deferredの重要度選別に
使うためのものであり、covered/gap/deferredの判定基準そのものを変えるものではない。

JSONのみを返してください。前置き・説明不要。

{
  "items": [
    {
      "requirement": "ヒアリング文中の要件を1文で要約",
      "hearing_section": "該当するヒアリング節番号（例: 3, 5, 8）",
      "priority": "required / optional（そのヒアリング節が必須節か任意節か）",
      "status": "covered / gap / deferred",
      "evidence": "coveredの場合はDSL/コード中の該当箇所、gapの場合は探したが見当たらなかった旨、deferredの場合はヒアリング中の除外宣言箇所"
    }
  ]
}
"""


def detect_hearing_dsl_gaps(
    domain_name: str,
    hearing_texts: list,
    dsl_scenarios: dict,
    converter_code: str,
    solver_code: str,
    ui_converter_code: str | None = None,
) -> dict:
    """
    論点: hearing→DSL/コード カバレッジチェック（Gate2拡張、track1）。
    detect_extension_gaps()（Stage1a.5、既存ドメイン向け）と同じLLM比較パターンを、
    new_domain登録時のStage2生成物へ向けて適用する。

    dsl_scenarios: {"baseline": {...}, "infeasible": {...}} 形式。

    2026-07-31追加: ui_converter_code（省略可）。以前はconverter.py/solver.pyの2つ
    しか比較対象に含めておらず、「§7のような画面表示要件は、solverの出力に該当データが
    含まれているか」だけで判定していた。これだと、solver.pyが正しくデータを出力していても
    ui_converter.pyがそれを一切参照していない（旧ドメインの表示コードが取り残されている等）
    不整合を検出できなかった（NursingWorkloadBalance登録で実際に発生。詳細は
    extract_domain_artifacts_from_diffs()のdocstring参照）。渡されなかった場合
    （呼び出し元がui_converter.pyを抽出できなかった場合）は従来通りconverter/solverのみで
    判定する。
    """
    from llm.llm_client import call_llm, extract_json, default_model

    hearing_combined = "\n\n---\n\n".join(hearing_texts)
    scenarios_block = "\n\n".join(
        f"### {name}\n```json\n{json.dumps(scenario, ensure_ascii=False, indent=2)}\n```"
        for name, scenario in dsl_scenarios.items()
    )
    ui_converter_block = (
        f"""

## 生成されたui_converter.py（solver出力 → 画面表示DSL変換。§7等の表示要件はここまで
確認すること。solverにデータがあってもこのファイルが参照していなければgap）
```python
{ui_converter_code}
```"""
        if ui_converter_code
        else ""
    )
    user_prompt = f"""## 業務名
{domain_name}

## ヒアリング内容
{hearing_combined}

## 生成されたDSLシナリオ
{scenarios_block}

## 生成されたconverter.py
```python
{converter_code}
```

## 生成されたsolver.py
```python
{solver_code}
```
{ui_converter_block}

上記を突き合わせ、ヒアリング文中の要件それぞれについてcovered/gap/deferredを判定し、
JSONで回答してください。
"""
    messages = [{"role": "system", "content": _HEARING_DSL_COVERAGE_SYSTEM}, {"role": "user", "content": user_prompt}]
    # temperature=0: classify_problem()（Stage1a）と同じ理由。実測で同一ヒアリング・
    # 同一生成物に対しても実行のたびに検出項目数・required判定が揺れることを確認した
    # （2026-07-16試験実装: 1回目20件/required2件 → 2回目21件/required3件）。
    # 判定タスクであり創造性は不要なため、決定的にして揺れを抑える。
    raw    = call_llm(messages, model=default_model(), max_tokens=4000, temperature=0)
    result = extract_json(raw)
    items  = result.get("items", [])
    gaps   = [i for i in items if i.get("status") == "gap"]
    required_gaps = [i for i in gaps if i.get("priority") == "required"]
    logger.info(
        f"[hearing_dsl_gaps] {domain_name}: total={len(items)}件, "
        f"gap={len(gaps)}件（うちrequired={len(required_gaps)}件）"
    )
    return {
        "items": items,
        "gap_count": len(gaps),
        "required_gap_count": len(required_gaps),
    }


_HEARING_DSL_COVERAGE_INCREMENTAL_SYSTEM = """\
あなたは、業務ヒアリング内容と最適化ドメイン実装（DSLシナリオ・converter.py・
solver.py）の要件カバレッジ判定を行う専門家です。

このタスクは「差分渡し」による再判定です。以前のカバレッジ判定結果（各要件に
idx番号を振ったcovered/gap/deferred判定の一覧）が既にあり、その後コードに変更が
加わりました。変更差分（unified diff形式）を見て、以前の判定のうち**影響を受ける
可能性がある項目だけ**を再判定してください。

出力は「判定が変わった項目」または「判定は変わらないが根拠（evidence）を
更新すべき項目」**のみ**を、対応する前回のidxとともに返してください。
影響が無い項目は出力に含めないでください（呼び出し側で前回の値をそのまま
使います）。1件も影響が無ければ空配列を返してください。

判定基準は元のカバレッジ判定と同じです:
- 迷った場合はcoveredと判断せず、gapとして報告すること（見落としより過検知を許容する）
- 差分が「新しく要件を満たすようになった」「既存の実装を壊した」のどちらの
  可能性もあることに注意し、両方向の変化を検討すること

JSONのみを返してください。前置き・説明不要。

{
  "changed": [
    {
      "idx": 3,
      "status": "covered / gap / deferred",
      "evidence": "新しい根拠"
    }
  ]
}
"""


def build_code_diff(prior_code: str | None, new_code: str | None, label: str) -> str | None:
    """
    2026-08-09追加（登録時間短縮タスク2の再開、差分渡し方式の実装）。

    prior_codeとnew_codeの unified diff を返す。差分が無い（prior_code is None、
    new_codeがNone、または完全一致）場合はNoneを返す。呼び出し側はNoneの場合
    「この観点では変更なし」として扱い、当該ファイルをdetect_hearing_dsl_gaps_incremental()
    のプロンプトに含めない（変更が無いファイルはLLMに送る意味が無いため）。
    """
    if prior_code is None or new_code is None or prior_code == new_code:
        return None
    diff_lines = difflib.unified_diff(
        prior_code.splitlines(keepends=True),
        new_code.splitlines(keepends=True),
        fromfile=f"{label}（前回チェック時）",
        tofile=f"{label}（今回）",
        n=3,
    )
    return "".join(diff_lines)


def detect_hearing_dsl_gaps_incremental(
    domain_name: str,
    hearing_texts: list,
    prior_result: dict,
    converter_diff: str | None,
    solver_diff: str | None,
    ui_converter_diff: str | None = None,
) -> dict:
    """
    2026-08-09追加（`2026-08-05_registration_time_reduction_plan.md` タスク2の再開）。

    detect_hearing_dsl_gaps()のコード全文渡し版に対する「差分渡し」版。self-repair
    v2（関数単位パッチ）が実証した「全文書き換え→差分のみ」という高速化パターンを
    hearing_dsl_gapsにも適用する。

    前提: この関数は「コードが完全に新規（1回目のGate2チェック）」なケースには使えない
    （比較対象となる`prior_result`が存在しないため）。debug_agentの修正ラウンド後の
    再検証など、「既に一度detect_hearing_dsl_gaps()でカバレッジ判定済みのコードに、
    小さな変更が加わった」場合の再判定にのみ使う想定。

    converter_diff/solver_diff/ui_converter_diffは、build_code_diff()で事前に
    計算した unified diff文字列（変更が無ければNone）。3つとも全てNoneの場合は
    LLM呼び出し自体を行わず、prior_resultをそのまま返す（差分が無ければ判定も
    変わりようがないため、コスト0で済ませる）。
    """
    if converter_diff is None and solver_diff is None and ui_converter_diff is None:
        logger.info(
            f"[hearing_dsl_gaps_incremental] {domain_name}: 差分なし、LLM呼び出しを"
            f"スキップして前回の判定をそのまま再利用（total={len(prior_result.get('items', []))}件）"
        )
        return prior_result

    from llm.llm_client import call_llm, extract_json, default_model

    prior_items = prior_result.get("items", [])
    hearing_combined = "\n\n---\n\n".join(hearing_texts)
    diff_blocks = []
    if converter_diff:
        diff_blocks.append(f"### converter.py の変更差分\n```diff\n{converter_diff}\n```")
    if solver_diff:
        diff_blocks.append(f"### solver.py の変更差分\n```diff\n{solver_diff}\n```")
    if ui_converter_diff:
        diff_blocks.append(f"### ui_converter.py の変更差分\n```diff\n{ui_converter_diff}\n```")
    diffs_combined = "\n\n".join(diff_blocks)

    # 出力側の削減（2026-08-09追加）: 前回のitemsを毎回全件出力させると、入力を
    # 削っても出力トークン量（≒レイテンシ）が下がらないことが実測で判明した
    # （MysteryShopperSchedulerでの検証: 入力-64%でも所要時間はほぼ横ばい、
    # むしろ全文渡しよりわずかに遅い結果になった）。出力もidxベースの差分のみに
    # 絞ることで、影響が無い項目についてはLLMに一切書かせない。
    indexed_items_block = "\n".join(
        f"{i}: [{item.get('priority')}/{item.get('status')}] §{item.get('hearing_section')} "
        f"{item.get('requirement')}"
        for i, item in enumerate(prior_items)
    )

    user_prompt = f"""## 業務名
{domain_name}

## ヒアリング内容
{hearing_combined}

## 前回のカバレッジ判定結果（idx: [優先度/判定] 節番号 要件、{len(prior_items)}件）
{indexed_items_block}

## 前回チェック時からのコード変更差分
{diffs_combined}

上記の差分が、前回判定のどのidxに影響しうるかを検討し、影響がある項目のみを
`changed`配列で返してください。
"""
    messages = [
        {"role": "system", "content": _HEARING_DSL_COVERAGE_INCREMENTAL_SYSTEM},
        {"role": "user", "content": user_prompt},
    ]
    raw = call_llm(messages, model=default_model(), max_tokens=2000, temperature=0)
    result = extract_json(raw)
    changed = result.get("changed", [])

    # prior_itemsをディープコピーした上で、changedに含まれるidxだけ上書きする。
    # 呼び出し側のprior_result（辞書オブジェクト）を意図せず書き換えないよう、
    # ここで新しいリストを作る。
    items = [dict(item) for item in prior_items]
    applied = 0
    for c in changed:
        idx = c.get("idx")
        if not isinstance(idx, int) or not (0 <= idx < len(items)):
            logger.warning(
                f"[hearing_dsl_gaps_incremental] {domain_name}: 不正なidx({idx!r})を"
                f"含む変更を無視しました（items件数={len(items)}）。"
            )
            continue
        if "status" in c:
            items[idx]["status"] = c["status"]
        if "evidence" in c:
            items[idx]["evidence"] = c["evidence"]
        applied += 1

    gaps = [i for i in items if i.get("status") == "gap"]
    required_gaps = [i for i in gaps if i.get("priority") == "required"]
    logger.info(
        f"[hearing_dsl_gaps_incremental] {domain_name}: total={len(items)}件, "
        f"changed={applied}件, gap={len(gaps)}件（うちrequired={len(required_gaps)}件）"
    )
    return {
        "items": items,
        "gap_count": len(gaps),
        "required_gap_count": len(required_gaps),
    }


def extract_domain_artifacts_from_diffs(diffs: list, snake: str) -> dict:
    """
    diffs（generate_domain_filesの戻り値）から、特定snakeドメインの
    converter.py/ui_converter.py/solver.py/DSLシナリオ（baseline/infeasible）を抜き出す。
    scan_diffs_for_warnings()のスキャンロジックと同じ分類パターンを、単一ドメイン
    向けに再利用可能な形に切り出したもの（detect_hearing_dsl_gaps呼び出し用）。

    2026-07-31追加: ui_converter_codeを追加。以前はconverter/solverの2ファイルしか
    detect_hearing_dsl_gaps()に渡していなかったため、「solverの出力データは正しいが
    ui_converter.pyがそれを参照せず画面表示が空になる」という不整合をヒアリング⇔コード
    カバレッジチェックが原理的に検出できなかった（NursingWorkloadBalance登録で実際に
    発生: solver.pyのmetrics/assignmentsは正しく実装されていたのに、ui_converter.pyが
    旧ドメイン（NurseShiftWeeklyCap）由来のキー（tasks/task_summary/staff_hours）を
    参照したまま取り残され、baseline画面のKPI・テーブルが全て0/空になった）。

    返り値: {"converter_code": str|None, "ui_converter_code": str|None,
             "solver_code": str|None, "scenarios": {"baseline": dict, "infeasible": dict}}
    """
    result = {"converter_code": None, "ui_converter_code": None, "solver_code": None, "scenarios": {}}
    scenario_re = re.compile(rf"(?:^|/)dsl_repository/scenarios/{re.escape(snake)}_(baseline|infeasible)\.json$")

    for item in diffs:
        path = item.get("path", "")
        code = item.get("new_content", "")

        if path.endswith("_solver.py") and Path(path).stem[: -len("_solver")] == snake:
            result["solver_code"] = code
        elif path.endswith("_ui_converter.py") and Path(path).stem[: -len("_ui_converter")] == snake:
            result["ui_converter_code"] = code
        elif (path.endswith("_converter.py") and not path.endswith("_ui_converter.py")
              and Path(path).stem[: -len("_converter")] == snake):
            result["converter_code"] = code
        else:
            m = scenario_re.search(path)
            if m:
                try:
                    result["scenarios"][m.group(1)] = json.loads(code)
                except Exception as e:
                    logger.warning(f"[extract_domain_artifacts] シナリオJSONパース失敗 {path}: {e}")

    return result


# 2026-08-28追記（Koshoshi合意）: カテゴリごとに「何の話か」「業務担当者が
# 照合できる手がかりとして何を残すべきか」を明示するヒント。以前は
# 「ファイルパス・変数名・例外名など技術的な表現は全部消す」という一律の指示で
# 言い換えていたため、Big-M近似の具体的な係数値やDSLキー名など、業務担当者や
# プログラムを読める担当者が「これは自分の入力したどの項目の話か」を照合する
# ための手がかりまで一緒に消えてしまい、「結局どこを見ればいいか分からない」
# という指摘（実機フィードバック）につながっていた。カテゴリごとに「何を
# 残すべきか」を具体的に指示することで、削るべき生のプログラム表現（スタック
# トレース断片・ファイルパスの羅列等）と、残すべき手がかり（数値・項目名・
# シナリオ名・ヒアリング節番号等）を区別させる。
_FINDING_CATEGORY_HINTS: dict[str, str] = {
    "dynamic": (
        "この指摘は、実際にCP Optimizerでシナリオを解いてみた結果、期待していた"
        "挙動（標準的な条件のシナリオ=baselineなら解が見つかる、あえて無理な"
        "条件にしたシナリオ=infeasibleなら解が見つからない、等）と食い違った、"
        "という客観的な事実です。対象のシナリオが『標準的な条件のシナリオ』か"
        "『あえて無理な条件にしたシナリオ』かを必ず日本語で明記し、実際に何が"
        "起きたか（解が見つかった／見つからなかった、coverage_rateの値など）を"
        "指摘に含まれる数値も含めてそのまま具体的に書いてください。"
    ),
    "big_m": (
        "この指摘は、複数の評価基準を1つの計算式にまとめる部分（目的関数）で、"
        "重みの差が極端に大きい項目が混在している、という内容です。指摘に含まれる"
        "具体的な係数の値は数値としてそのまま残し、『複数の評価基準に優先順位を"
        "つけている場合、その優先順位の付け方についての指摘であること』が伝わる"
        "ようにしてください。"
    ),
    "absent_value": (
        "この指摘は、候補の中から選ばれなかった場合の扱い方に矛盾がある可能性が"
        "ある、という内容です。『選ばれなかった候補（未採用のケース）の扱いに"
        "関する計算ロジックについての指摘であること』が伝わるようにしてください。"
    ),
    "missing_in_dsl_for_solver": (
        "この指摘は、入力データ（業務データ）の中の特定の設定項目が、実際の"
        "計算処理で一度も使われていない可能性がある、という内容です。指摘に"
        "含まれる具体的な項目名（英語表記のままでよい）は必ずそのまま残し、"
        "『ヒアリングで指定したどの項目に対応するか、業務側で照合できるように』"
        "してください。"
    ),
    "unused_in_solver": (
        "この指摘は、入力データの中の特定の項目が、実際の計算処理から一度も"
        "参照されていない可能性がある、という内容です。指摘に含まれる具体的な"
        "項目名（英語表記のままでよい）は必ずそのまま残してください。"
    ),
}


def _humanize_call_with_retry(
    system: str, user: str, expected_count: int, log_prefix: str,
    model, max_tokens: int,
) -> list[str] | None:
    """
    2026-09-07追加（Koshoshi合意）: 言い換え失敗時、従来は1回失敗しただけで
    無言で原文（内部用語混じりの生の指摘）を業務担当者にそのまま見せていた
    （フォールバックが起きたこと自体がログのwarningレベルでしか分からず、
    実機で気付きにくかった。実際に2026-09-07のReviewDocument登録で、Gate2
    lexicographic機構チェックの指摘がこのフォールバックを経由して生の技術
    文言のまま画面に出た実績をDBログで確認済み）。ここでは同じ呼び出しを
    最大2回試し、2回とも失敗した場合のみフォールバックとし、その場合は
    warningではなくerrorでログに残す（表示内容自体は変わらないが、運用側が
    失敗頻度を把握できるようにするため）。
    """
    from llm.llm_client import call_llm, extract_json

    last_error = "不明なエラー"
    for attempt in (1, 2):
        try:
            raw = call_llm(
                [{"role": "system", "content": system}, {"role": "user", "content": user}],
                model=model, max_tokens=max_tokens, temperature=0,
            )
            result = extract_json(raw)
            items = result.get("items", [])
            if isinstance(items, list) and len(items) == expected_count:
                return [str(x) for x in items]
            last_error = f"件数不一致（出力{len(items)}件 / 入力{expected_count}件）"
        except Exception as e:
            last_error = str(e)
        if attempt == 1:
            logger.warning(f"[{log_prefix}] 1回目失敗（{last_error}）、リトライします")
    logger.error(f"[{log_prefix}] 2回とも失敗（{last_error}）、原文のまま表示にフォールバック")
    return None


def humanize_technical_findings(
    findings: list[str], domain_name: str, category: str = "generic"
) -> list[str]:
    """
    2026-07-16追加: Gate2の技術的な指摘（CPO既知バグパターン検出、自己修復ループの
    結果、動的検証の例外メッセージ等）は、変数名・ファイルパス・Pythonの例外文が
    そのまま混ざっており、最適化やプログラミングの知識がない業務ユーザーには
    読んでも何を答えればよいか分からない。これをLLMで、業務の言葉で「何が起きたか」
    「何を判断すればよいか」が分かる文に言い換える。

    2026-08-28追記（Koshoshi合意）: 「技術的な表現は全部消す」という一律の指示を、
    「業務担当者が判断材料にできない生のプログラム表現（スタックトレース断片・
    ファイルパスの羅列等）は消すが、『これは自分の入力したどの項目/条件の話か』を
    照合できる具体的な手がかり（数値・項目名・シナリオ名・ヒアリング節番号等）は
    絶対に残す」という指示に変更。加えて、言い換え文の末尾に必ず「次に何を確認
    すればよいか」を明記させる。category を渡すと、カテゴリ別の手がかり
    （_FINDING_CATEGORY_HINTS参照）をプロンプトに追加する。

    - 意味を変えない・情報を削らない（要約ではなく言い換え）ことを厳守させる。
    - 入力と出力の件数は必ず一致させる（一致しなければLLM出力を信用せず原文を返す）。
    - LLM呼び出し自体に失敗した場合も原文をそのまま返す（表示できなくなるより、
      技術的でも原文が出る方が安全）。
    """
    if not findings:
        return []
    from llm.llm_client import call_llm, extract_json, fast_model

    items_block = "\n".join(f"{i + 1}. {f}" for i, f in enumerate(findings))
    category_hint = _FINDING_CATEGORY_HINTS.get(category, "")
    system = (
        "あなたはB2B業務システムの登録確認画面に表示する指摘事項を、"
        "プログラミングやCP最適化の知識がない業務担当者にも読んで判断できる"
        "平易な日本語に言い換えるアシスタントです。ファイルパスの羅列や"
        "Pythonのスタックトレースの断片など、業務担当者が読んでも判断材料に"
        "できない生のプログラム表現はそのまま出さないでください。ただし、"
        "指摘の中に含まれる具体的な数値・項目名・シナリオ名・ヒアリングの"
        "節番号や文言など、『これは自分が入力したどの内容の話か』を業務担当者や"
        "プログラムを読める担当者が照合するための手がかりになる情報は、"
        "絶対に削らずそのまま残してください（要約ではなく言い換えです）。"
        "元の指摘が持っている情報（対象・件数・深刻さ等）を削ったり意味を"
        "変えたりしてはいけません。言い換え文の最後には必ず一文、"
        "「次に何を確認すればよいか」を具体的に明記してください。ただしこの"
        "『次に確認すべきこと』は、プログラミングやCP最適化の知識がない業務"
        "担当者自身が実際に答えられる内容にしてください（例:「この方針のまま"
        "登録を進めてよいか」「ヒアリングのこの理解で合っているか」）。"
        "『開発担当者に確認してください』『コードを見てください』のように、"
        "業務担当者では実行できない依頼で締めくくってはいけません。\n\n"
        "（2026-09-07追記、Koshoshi合意）『Gate2』『lexicographic』『big_m』"
        "『Big-M』『interval_var』『presence_of』『coverage_rate』"
        "『force_apply』『job_id』など、CP最適化やこのシステムの内部実装を"
        "知らないと意味が分からない専門用語・識別子は、たとえ元の指摘に"
        "含まれていても言い換え後の文には一切出さないでください（ファイル"
        "パスやスタックトレースと同じく、業務担当者の判断材料にならない生の"
        "プログラム表現として扱います）。\n\n"
        "（2026-08-30追記、Koshoshiの実機フィードバックを受け追加）言い換え文の"
        "さらに後ろに、改行を挟んで必ず【推奨】ブロックを1つ追加してください。"
        "【推奨】には、この指摘が実際に業務や解の正しさに影響する可能性が"
        "高いか低いかを一言で判定し（例:「このまま登録して問題ない可能性が"
        "高いです」「実際に解の精度に影響している可能性があるため、修正を"
        "お勧めします」）、続けてその判定理由を1文で書いてください。ただし"
        "これはコードを実際に実行して確認したものではなく、あなたの推測に"
        "基づく参考情報であることが伝わる書き方にし、『必ずそうである』という"
        "断定は避けてください（例:「〜の可能性があります」「〜と考えられます」）。"
        "この推奨はあくまで参考であり、最終判断は人間が行うことを妨げては"
        "いけません。\n"
        "【推奨】ブロックの判定基準:\n"
        "  - 静的チェックの既知の弱点（自己参照キーの誤検知、値を丸ごと"
        "    別の辞書に渡しているためコード上は『未参照』に見えるだけで実際は"
        "    値が伝播しているケース等）に該当しそうな場合は「低い」寄りに判定する\n"
        "  - ヒアリングで人間が明示的に確定させた内容（数値・ルール・既定動作）と"
        "    生成コードの実際の挙動が食い違っていそうな場合は「高い」寄りに判定する\n"
        "  - 過去に同種の指摘（ファイルパス・変数名等が似た文脈）で実害が"
        "    確認された既知バグパターンに該当する場合も「高い」寄りに判定する"
        + (("\n\n" + category_hint) if category_hint else "")
    )
    user = (
        f"## 業務名\n{domain_name}\n\n## 言い換え対象の指摘事項（{len(findings)}件）\n{items_block}\n\n"
        '出力は次のJSON形式のみ: {"items": ["言い換え後の文1\n【推奨】...", "言い換え後の文2\n【推奨】...", ...]}\n'
        f"items の件数は必ず入力と同じ{len(findings)}件、順序も入力と一致させること。"
        "各要素は必ず本文＋改行＋【推奨】ブロックの2部構成にすること。"
    )
    items = _humanize_call_with_retry(system, user, len(findings), "humanize_findings", fast_model(), 2000)
    return items if items is not None else findings


def _humanize_exception_findings(findings: list[str], domain_name: str) -> list[str]:
    """
    2026-08-03追加: solver.pyが実行時例外でクラッシュした場合の指摘専用の言い換え。
    humanize_technical_findings()と同じ入力(dynamic_warnings)を渡すと、LLMが
    「実装が例外を投げてクラッシュした」という事実を「制約を満たす解が無い
    （真のinfeasible）」であるかのような文面に言い換えてしまうことがあり、
    Koshoshiが実機で毎回シナリオを手動実行して初めて真因（例外）に気付く、
    という手戻りが複数回発生した（WorkerLoadBalancer・VesselDeckLoader較正実験）。
    このため、システムプロンプト側で「これは実装のバグである」という前提を明示し、
    「業務データや制約の見直しではなく開発者側のコード修正が必要」という結論に
    誘導する。件数一致チェック・LLM失敗時のフォールバックはhumanize_technical_findings()
    と同じ方針。
    """
    if not findings:
        return []
    from llm.llm_client import call_llm, extract_json, fast_model

    items_block = "\n".join(f"{i + 1}. {f}" for i, f in enumerate(findings))
    # 2026-08-28追記（Koshoshi合意）: 「技術的な表現はそのまま出さない」の対象を
    # 「業務担当者にとって意味のないファイルパスの羅列・スタックトレースの行番号」
    # に絞り、(a) 対象シナリオが標準/無理な条件のどちらかという業務側の手がかりと
    # (b) Pythonの例外の種類（KeyError等）というプログラムを読める担当者向けの
    # 手がかりの両方を、削らずそのまま残すよう変更した。
    system = (
        "あなたはB2B業務システムの登録確認画面に表示する指摘事項を、"
        "プログラミングやCP最適化の知識がない業務担当者にも読んで判断できる"
        "平易な日本語に言い換えるアシスタントです。ここで渡される指摘は、"
        "すべて『生成されたプログラムが実行中に例外（プログラムのバグ）で"
        "停止した』というケースであり、『業務データや制約条件を満たす解が"
        "存在しない（真のinfeasible）』という判定とは明確に別物です。"
        "言い換え文には必ず、これが実装上の不具合（バグ）である可能性が高く、"
        "業務データや制約条件を見直すのではなく、開発者側でのコード修正が"
        "必要であることが伝わる表現を含めてください。ファイルパスの羅列や"
        "スタックトレースの行番号など、業務担当者にとって意味のない生の"
        "プログラム表現はそのまま出さないでください。ただし、対象のシナリオが"
        "『標準的な条件のシナリオ』か『あえて無理な条件にしたシナリオ』かは"
        "必ず日本語で明記し、また元の指摘に含まれるPythonの例外の種類"
        "（例: KeyError, TypeError等）は、プログラムを読める担当者が原因箇所を"
        "特定する手がかりになるため、削らずそのまま残してください。対象・件数と"
        "いった情報も削らないでください。また、言い換え文の最後は、業務担当者"
        "自身が実際に行える判断で締めくくってください（例:「このまま登録を"
        "保留し、修正を待つことをお勧めします」「今回は登録を見送ることを"
        "お勧めします」）。『開発担当者に連絡してください』『コードを確認して"
        "ください』のように、業務担当者では実行できない依頼を締めの行動として"
        "指示しないでください（実際の修正対応は別途行われるため、ここでは"
        "登録を進めるか保留するかの判断材料だけを伝えれば十分です）。\n"
        "（2026-09-07追記、Koshoshi合意）『Gate2』『lexicographic』『big_m』"
        "『Big-M』『interval_var』『presence_of』『coverage_rate』"
        "『force_apply』『job_id』など、CP最適化やこのシステムの内部実装を"
        "知らないと意味が分からない専門用語・識別子は、たとえ元の指摘に"
        "含まれていても言い換え後の文には一切出さないでください。"
    )
    user = (
        f"## 業務名\n{domain_name}\n\n## 言い換え対象の指摘事項（{len(findings)}件、"
        f"いずれも実行時例外によるクラッシュ）\n{items_block}\n\n"
        '出力は次のJSON形式のみ: {"items": ["言い換え後の文1", "言い換え後の文2", ...]}\n'
        f"items の件数は必ず入力と同じ{len(findings)}件、順序も入力と一致させること。"
    )
    items = _humanize_call_with_retry(system, user, len(findings), "humanize_exception_findings", fast_model(), 2000)
    return items if items is not None else findings


def _humanize_required_gap_findings(findings: list[str], domain_name: str) -> list[str]:
    """
    2026-09-06追加（Koshoshi合意）: ヒアリング必須項目が実装に反映されていない
    （required_gap）指摘は、他のblockingカテゴリと同じく「事実を薄めない」ために
    従来humanize_technical_findings()を通していなかった。しかしその結果、
    ファイルパス・変数名・コード内部の表現がそのままユーザーに出てしまい、
    業務担当者には対応不能な文面になっていた（2026-09-06 ReviewDocumentテストで
    実機確認、Koshoshiフィードバック）。

    dynamic_exception系（_humanize_exception_findings）と同様、事実（ヒアリング
    節番号・要求内容・実装の実際の挙動）を変えないことをシステムプロンプトで
    固定した上で、コード内部の固有名詞（ファイル名・変数名・関数名）だけを
    機械的に取り除く、限定的な言い換えを行う。結論を最初の1文で述べ、全体を
    2文以内に収めることを厳守させる。また、ユーザーが実行できない依頼
    （「コードを開いて確認してください」等）で締めくくらないことを明記する。
    """
    if not findings:
        return []
    from llm.llm_client import call_llm, extract_json, fast_model

    items_block = "\n".join(f"{i + 1}. {f}" for i, f in enumerate(findings))
    system = (
        "あなたはB2B業務システムの登録確認画面に表示する指摘事項を、"
        "プログラミングの知識がない業務担当者にも読んで判断できる平易な"
        "日本語に言い換えるアシスタントです。ここで渡される指摘は、"
        "ヒアリングで確定した必須要件が、生成された実装に正しく反映されて"
        "いるか確認が必要な箇所です。\n\n"
        "厳守事項:\n"
        "1. 事実（ヒアリングの節番号、要求されていた内容、実装が実際にどう"
        "   振る舞うか）は一切変えない・削らない。\n"
        "2. ファイルパス・変数名・関数名・「制約」等のコード内部の固有名詞、"
        "   および「Gate2」「lexicographic」「big_m」「interval_var」"
        "   「presence_of」「coverage_rate」等のCP最適化・システム内部の"
        "   専門用語も、すべて取り除き業務的な言葉に置き換える。\n"
        "3. 出力は必ず2文以内。1文目は結論（このまま登録して問題なさそうか、"
        "   確認が必要そうか）を述べ、2文目（必要な場合のみ）に、ヒアリングの"
        "   節番号と具体的な条件・数値を交えた根拠を簡潔に書く。\n"
        "4. 『ファイルを開いて確認してください』『コードを確認してください』"
        "   のような、プログラムを読めない担当者には実行不可能な依頼で"
        "   締めくくらない。判断に迷う場合は、ヒアリング内容の意図を確認する"
        "   問いかけ（例:「この理解で合っていますか？」）に留める。\n"
        "5. 断定的な結論を出す場合も、実際にコードを実行して確認したもの"
        "   ではなく静的な解析に基づく判断であることが伝わる言い回しにする"
        "   （例:「〜と考えられます」「〜と判断します」）。"
    )
    user = (
        f"## 業務名\n{domain_name}\n\n"
        f"## 言い換え対象の指摘事項（{len(findings)}件、いずれもヒアリング必須項目の"
        f"実装反映確認）\n{items_block}\n\n"
        '出力は次のJSON形式のみ: {"items": ["言い換え後の文1", "言い換え後の文2", ...]}\n'
        f"items の件数は必ず入力と同じ{len(findings)}件、順序も入力と一致させること。"
    )
    items = _humanize_call_with_retry(system, user, len(findings), "humanize_required_gap", fast_model(), 1200)
    return items if items is not None else findings


def scan_diffs_for_warnings(diffs: list) -> dict:
    """
    diffs（generate_domain_filesの戻り値）を、ファイルを書き込む前にスキャンし、
    CP Optimizer既知バグパターン・目的関数の見落とし・フィールド突き合わせ不一致を
    検出する。/api/domain/run の人間確認ゲート（needs_confirmation判定）に使用する。
    ファイルには一切書き込まない（ドライランスキャン）。

    V4.6: Gate2静的チェック（フィールド突き合わせ）を追加。同じdiffセットに
    {snake}_converter.py と {snake}_solver.py が両方含まれる場合（4DSL新規
    ドメイン登録時）、check_converter_solver_field_consistency() の結果を
    警告に追加する。

    V4.7（HANDOFF_2026-07-15b「顧客ごとのOptiBuddy構想」論点）: 同じdiffセットに
    {snake}_baseline/infeasible.json（DSLシナリオ）が含まれる場合、
    check_dsl_converter_field_consistency() でDSL⇔converter側のフィールド
    突き合わせも行う。converter⇔solverの対称性チェックだけでは、DSLシナリオ自体が
    silently欠落・死んだフィールドを持つケース（ヒアリング→DSL、DSL→converterの
    手前2段）を検出できなかったための拡張（新規機構ではなく既存パターンの延長）。

    2026-07-19変更: 戻り値をlistからdict（{"warnings": [...],
    "unused_in_solver_warnings": [...]}）に変更。理由: MeetingRoomの
    metadata.instance_name/note パススルー漏れ（本物のバグ、実機で複数回再発）が、
    同じ静的field-checkの`missing_in_converter`（自己参照キーの誤検知パターンが
    既知・専用の抑制ロジックあり）と一緒くたにadvisory（人間確認不要）扱いされ、
    見過ごされたまま登録された事故があった。`unused_in_solver`には
    `missing_in_converter`のような既知の誤検知パターンが無いため、
    呼び出し元（run_gate2_checks）でblocking_questions側に回せるよう分離した。

    2026-07-22変更: 戻り値に "big_m_warnings" キーを追加。_check_big_m_objective()
    が検出するBig-M近似（多目的の優先順位を単一の重み付き合算で偽装するパターン）は、
    unused_in_solverと同様の理由（過去に実際の業務問題を起こした既知パターンであり、
    誤検知が許容できるレベルの粗さの他の静的field-checkとは性質が異なる）で、
    Koshoshi合意（2026-07-22）によりadvisory側ではなくblocking側（人間の確認必須）に
    分離する。unused_in_solver_warningsと同じ扱いのため独立したリストにする。
    """
    warnings = []
    unused_in_solver_warnings = []
    big_m_warnings = []
    absent_value_warnings = []
    missing_in_dsl_for_solver_warnings = []
    mip_self_check_warnings = []
    solver_by_snake:    dict[str, tuple[str, str]] = {}
    converter_by_snake: dict[str, tuple[str, str]] = {}
    scenarios_by_snake: dict[str, list] = {}

    _scenario_re = re.compile(r"(?:^|/)dsl_repository/scenarios/(.+)_(baseline|infeasible)\.json$")

    for item in diffs:
        path = item.get("path", "")
        code = item.get("new_content", "")

        if path.endswith("_solver.py"):
            snake = Path(path).stem[: -len("_solver")]
            solver_by_snake[snake] = (path, code)
            for pattern, warning in _CPO_WARN_PATTERNS:
                if re.search(pattern, code):
                    warnings.append(f"{path}: {warning}")
            obj_warning = _check_objective_coverage(code, path)
            if obj_warning:
                warnings.append(obj_warning)
            warnings.extend(_check_unwrapped_minimize(code, path))
            warnings.extend(_check_unnamed_expr_get_value(code, path))
            warnings.extend(_check_no_overlap_without_sequence_var(code, path))
            big_m_warnings.extend(_check_big_m_objective(code, path))
            absent_value_warnings.extend(_check_optional_interval_absent_value(code, path))
            mip_self_check_warnings.extend(_check_mip_self_verification(code, path))

        elif path.endswith("_converter.py") and not path.endswith("_ui_converter.py"):
            snake = Path(path).stem[: -len("_converter")]
            converter_by_snake[snake] = (path, code)

        elif path.endswith("_ui_converter.py"):
            warnings.extend(_check_table_sections_wiring(code, path))

        else:
            m = _scenario_re.search(path)
            if m:
                snake = m.group(1)
                try:
                    scenarios_by_snake.setdefault(snake, []).append(json.loads(code))
                except Exception as e:
                    logger.warning(f"[Gate2 field-check] シナリオJSONパース失敗 {path}: {e}")

    # Gate2静的チェック: フィールド突き合わせ（converter/solverが両方揃うドメインのみ）
    for snake, (solver_path, solver_code) in solver_by_snake.items():
        if snake not in converter_by_snake:
            continue
        converter_path, converter_code = converter_by_snake[snake]
        check = check_converter_solver_field_consistency(converter_code, solver_code)
        for err in check["errors"]:
            warnings.append(f"[Gate2 field-check] {err}")
        if check["missing_in_converter"]:
            warnings.append(
                f"[Gate2 field-check] {solver_path}: solverが参照しているが "
                f"{converter_path} が一度も出力しないキー: {check['missing_in_converter']} "
                f"（実行時KeyErrorまたは常時デフォルト値化の疑い。converterの出力を確認してください）"
            )
        if check["unused_in_solver"]:
            unused_in_solver_warnings.append(
                f"[Gate2 field-check] {converter_path}: 出力しているが "
                f"{solver_path} が一度も参照しないキー: {check['unused_in_solver']} "
                f"（宣言したルールが実装で無視されている疑い。ヒアリング項目との対応を確認してください）"
            )

    # Gate2静的チェック（V4.7）: DSLシナリオ⇔converter フィールド突き合わせ
    for snake, converter_path_code in converter_by_snake.items():
        if snake not in scenarios_by_snake:
            continue
        converter_path, converter_code = converter_path_code
        dsl_check = check_dsl_converter_field_consistency(scenarios_by_snake[snake], converter_code)
        for err in dsl_check["errors"]:
            warnings.append(f"[Gate2 DSL-check] {err}")
        if dsl_check["missing_in_dsl"]:
            warnings.append(
                f"[Gate2 DSL-check] {converter_path}: converterが参照しているが "
                f"DSLシナリオが一度も提供しないキー: {dsl_check['missing_in_dsl']} "
                f"（実行時は常にデフォルト値化する疑い。DSLシナリオまたはconverterを確認してください）"
            )
        if dsl_check["unused_in_converter"]:
            warnings.append(
                f"[Gate2 DSL-check] DSLシナリオが持つが {converter_path} が一度も参照しないキー: "
                f"{dsl_check['unused_in_converter']} "
                f"（ヒアリング→DSLで拾われたのにコードに反映されていない疑い。converterを確認してください）"
            )

    # Gate2静的チェック（2026-07-26追加）: DSLシナリオ⇔solver 直接フィールド突き合わせ
    # （ネストキー対応）。work_limitsのようにconverterがキー名を書き換えずパススルー
    # するネスト構造では、converter経由の上記2チェックがネストキー名の不一致
    # （2026-07-12 NurseShiftWeeklyCap不具合1のパターン）を検出できない抜け穴が
    # あったため、converterを経由せず直接DSL⇔solverを突き合わせる。
    # unused_in_solver/big_m_warningsと同じ理由（既知の誤検知パターンが薄く、
    # 実際に本物のバグを見逃した実績がある）でblocking側に回す。
    for snake, (solver_path, solver_code) in solver_by_snake.items():
        if snake not in scenarios_by_snake:
            continue
        _converter_code_for_check = converter_by_snake.get(snake, (None, ""))[1]
        dsl_solver_check = check_dsl_solver_field_consistency(
            scenarios_by_snake[snake], solver_code, _converter_code_for_check
        )
        for err in dsl_solver_check["errors"]:
            warnings.append(f"[Gate2 DSL-solver-check] {err}")
        if dsl_solver_check["missing_in_dsl_for_solver"]:
            missing_in_dsl_for_solver_warnings.append(
                f"[Gate2 DSL-solver-check] {solver_path}: solverが参照しているが "
                f"DSLシナリオのどこにも（ネスト構造も含め）存在しないキー: "
                f"{dsl_solver_check['missing_in_dsl_for_solver']} "
                f"（converterがパススルーするネスト構造でキー名が食い違っている疑いがあります。"
                f"実行時は常にデフォルト値化し、対応する制約が一度もモデルに追加されない可能性があります）"
            )

    return {
        "warnings": warnings,
        "unused_in_solver_warnings": unused_in_solver_warnings,
        "big_m_warnings": big_m_warnings,
        "absent_value_warnings": absent_value_warnings,
        "missing_in_dsl_for_solver_warnings": missing_in_dsl_for_solver_warnings,
        "mip_self_check_warnings": mip_self_check_warnings,
    }


# ─────────────────────────────────────────────────────────────
# Gate2自己修復ループ: converter⇔solver キー不一致の自動修正（最大2回）
#
# 背景: _CONVERTER_SOLVER_CONTRACT をStage2プロンプトに埋め込んでも、
# 一発生成だけではキー不一致が解消しきらないことを複数ドメイン登録
# （StoreSite）で確認した（契約追加前16キー不一致 → 追加後も10キー不一致）。
# Stage2生成直後・人間確認画面より前に静的field-checkを実行し、不一致が
# 見つかった場合のみ、具体的な差分（どのキーが足りない／使われていないか）を
# LLMにフィードバックして修正させる。
#
# V2（本ブロック）: 1回の修正では解消しきらないケースが実際にあったため
# （StoreSite: 7→7件で1回では未解消）、最大試行回数を2回に増やした。
# 各回、直前の結果に対して再度field-checkし、まだ不一致があれば新しい
# 差分を渡してもう一度修正させる。無限リトライはしない（max_attemptsで
# 必ず打ち切り、それでも直らなければ従来通りfield-check警告として
# 人間確認画面に出す）。また、各回の出力は「元より悪化していないか」
# （不一致の総数が増えていないか）を確認してから採用する回帰ガードを設け、
# LLMの修正が逆効果だった場合に元の状態より悪い内容を書き込まないようにする。
# 恒久的な抽象化（factoryパターン等でキー生成を一元化する等）は複雑さが
# 増すだけで根本解決にならないため、ここでは導入しない。
# ─────────────────────────────────────────────────────────────

_FIELD_REPAIR_SYSTEM = """\
あなたはPythonコードの自動修正ツールです。
converter.py が出力する辞書のキーと solver.py が参照する辞書のキーが
一致していない箇所が機械的に検出されました。以下の2ファイルを、
指摘されたキー不一致がすべて解消されるように修正し、
===FILE:path===...===END=== マーカー形式で2ファイルとも全文出力してください
（差分ではなく、修正後の完全なファイル内容を出力すること）。

修正方針:
- missing_in_converter に列挙されたキーは、solverが実際に参照しているのに
  converterが一度も出力していないキーです。converter側にそのキーを追加して
  出力するか、solver側の参照名が誤っている場合はsolver側を正しいキー名に
  修正してください（converter/solverのどちらのキー名が意図されたものかを
  コードの文脈・変数名・コメントから判断すること）。
- unused_in_solver に列挙されたキーは、converterが出力しているのに
  solverが一度も参照していないキーです。solver側でそのキーを実際に
  使うよう実装を追加するか、不要であればconverter側から削除してください。
- 上記以外の既存ロジック（制約・目的関数・アルゴリズム）は変更しないこと。
"""


def _field_check_total(check: dict) -> int:
    """不一致の総数（missing_in_converter + unused_in_solver + errors）。回帰ガード用。"""
    return len(check["missing_in_converter"]) + len(check["unused_in_solver"]) + len(check["errors"])


def _is_field_check_clean(check: dict) -> bool:
    return not check["missing_in_converter"] and not check["unused_in_solver"] and not check["errors"]


def _call_field_repair_llm(converter_path: str, converter_code: str,
                            solver_path: str, solver_code: str,
                            snake: str, check: dict) -> tuple[str | None, str | None]:
    """1回分の修正LLM呼び出し。(new_converter, new_solver) を返す（失敗時は (None, None)）。"""
    from llm.llm_client import call_llm

    prompt = f"""## 対象ドメイン
snake_case: {snake}

## 検出されたキー不一致
- solverが参照するがconverterが一度も出力しないキー
  （missing_in_converter）: {check['missing_in_converter']}
- converterが出力しているがsolverが一度も参照しないキー
  （unused_in_solver）: {check['unused_in_solver']}

## 現在の {converter_path}
```python
{converter_code}
```

## 現在の {solver_path}
```python
{solver_code}
```

===FILE:{converter_path}===
（修正後の全文をここに出力）
===END===

===FILE:{solver_path}===
（修正後の全文をここに出力）
===END===
"""
    try:
        raw = call_llm(
            [{"role": "system", "content": _FIELD_REPAIR_SYSTEM},
             {"role": "user", "content": prompt}],
            max_tokens=8000,
            temperature=0,
        )
        parsed = _parse_marker_output(raw)
    except Exception as e:
        logger.warning(f"[Gate2 self-repair] LLM修正呼び出しに失敗: {e}")
        return None, None

    fixed_by_path = {f["path"]: f["content"] for f in parsed.get("files", [])}
    new_converter = fixed_by_path.get(converter_path)
    new_solver    = fixed_by_path.get(solver_path)
    if not new_converter or not new_solver:
        logger.warning(
            f"[Gate2 self-repair] 修正応答から {converter_path} / {solver_path} を抽出できませんでした。"
        )
        return None, None
    return new_converter, new_solver


# ─────────────────────────────────────────────────────────────
# Gate2自己修復ループ V2（実験、2026-08-05追加、まだ本番経路には未接続）
#
# 背景: registration_time_reduction_plan.md タスク7。段階Bの単体計測で、
# 上記_call_field_repair_llm()（ファイル全文書き換え型）が1回104.9秒かかり、
# 2回目Gate2再検証全体（実測189秒）の約55%を占めることが判明した
# （ENGINEERING_LOG.md 2026-08-05追記3参照）。実際のキー不一致は多くの場合
# converter/solverそれぞれ1〜2個の関数内に限られるにもかかわらず、
# 毎回2ファイルの全文（本件では出力7,553トークン）をLLMに書き直させて
# いたことが、生成トークン数＝時間の直接的な原因だった。
#
# 改善案: 「新規コードではなく既存箇所」であるgenerate_and_apply_extensions()
# (V2、5-1節付近)が既に使っている「関数単位パッチ」形式
# （===REPLACE_FUNCTION:path:関数名===/===ADD_FUNCTION:path===、
# _parse_function_output()・_find_function_block()・_reindent_code()）を
# そのまま転用し、self-repairにも「変更が必要な関数だけを返させる」方式を
# 適用する。パース・関数境界検出・インデント補正のロジックは実運用で
# 検証済みのものをそのまま再利用し、新規に書き起こさない（バグ混入リスクを
# 抑える）。extension applyはディスク上のファイルに対して動作するが、
# self-repairはdiffs（メモリ上の文字列）に対して動作するため、
# 適用部分だけメモリ上で完結する_apply_function_patches_in_memory()を新設した。
#
# 位置づけ: まだattempt_field_consistency_repair()からは呼ばれていない。
# tools/stage_b_selfrepair_v2_timing.py で単体計測（段階B）を行い、
# v1（104.9秒/7,553トークン）との比較結果を見てから本番経路に接続するか判断する。
# ─────────────────────────────────────────────────────────────

_FIELD_REPAIR_SYSTEM_V2 = """\
あなたはPythonコードの自動修正ツールです。
converter.py が出力する辞書のキーと solver.py が参照する辞書のキーが
一致していない箇所が機械的に検出されました。以下の2ファイルのうち、
指摘されたキー不一致の解消に**実際に必要な関数だけ**を修正してください。

修正方針:
- missing_in_converter に列挙されたキーは、solverが実際に参照しているのに
  converterが一度も出力していないキーです。converter側にそのキーを追加して
  出力するか、solver側の参照名が誤っている場合はsolver側を正しいキー名に
  修正してください（converter/solverのどちらのキー名が意図されたものかを
  コードの文脈・変数名・コメントから判断すること）。
- unused_in_solver に列挙されたキーは、converterが出力しているのに
  solverが一度も参照していないキーです。solver側でそのキーを実際に
  使うよう実装を追加するか、不要であればconverter側から削除してください。
- 上記以外の既存ロジック（制約・目的関数・アルゴリズム）は変更しないこと。
- **ファイル全文を出力してはいけない**。修正が必要な関数（メソッドを含む）
  単位でのみ、以下の形式で出力すること。

出力形式:
  既存関数を丸ごと入れ替える場合（最も一般的なケース）:
  ===REPLACE_FUNCTION:ファイルパス:関数名===
  <関数の完全な定義（def行から関数末尾まで、クラスメソッドの場合は
   元と同じインデント階層で記述）>
  ===END===

  新規関数を追加する場合（既存関数の入れ替えでは対応できない場合のみ）:
  ===ADD_FUNCTION:ファイルパス===
  <新規関数の完全な定義>
  ===END===

不一致の解消に必要な関数の数だけ、上記ブロックを繰り返し出力してよい
（1関数のみで解消するなら1ブロックのみでよい）。上記2パターン以外の
出力形式（ファイル全文、差分diff形式等）は禁止。前置き・説明も不要。
"""


def _apply_function_patches_in_memory(content: str, replacements: list[dict], additions: list[dict]) -> str:
    """
    apply_function_replacements()のディスクI/O版とは異なり、メモリ上の
    ファイル内容文字列に対して関数単位パッチを適用する（self-repair用、
    diffsがまだディスクに書かれていない前提のため）。

    apply_function_replacements()と異なり、対象関数が見つからない場合は
    黙って末尾追記にフォールバックせず例外を送出する（self-repairは正確性が
    最優先であり、「置換のつもりが末尾に孤立コードが増える」事故を避けるため。
    呼び出し側でこの例外を捕まえ、修正失敗として扱うこと）。
    """
    for r in replacements:
        block = _find_function_block(content, r["function_name"])
        if block is None:
            raise ValueError(f"対象関数が見つかりません: {r['function_name']}")
        b_start, b_end, indent = block
        new_code = _reindent_code(r["code"], indent)
        content = content[:b_start] + new_code.rstrip() + "\n\n" + content[b_end:]
    for a in additions:
        content = content.rstrip() + "\n\n\n" + a["code"].rstrip() + "\n"
    return content


def _call_field_repair_llm_v2(converter_path: str, converter_code: str,
                               solver_path: str, solver_code: str,
                               snake: str, check: dict) -> tuple[str | None, str | None]:
    """_call_field_repair_llm()の関数単位パッチ版。(new_converter, new_solver)を
    返す（失敗時は(None, None)、_call_field_repair_llm()と同じ契約）。"""
    from llm.llm_client import call_llm

    prompt = f"""## 対象ドメイン
snake_case: {snake}

## 検出されたキー不一致
- solverが参照するがconverterが一度も出力しないキー
  （missing_in_converter）: {check['missing_in_converter']}
- converterが出力しているがsolverが一度も参照しないキー
  （unused_in_solver）: {check['unused_in_solver']}

## 現在の {converter_path}
```python
{converter_code}
```

## 現在の {solver_path}
```python
{solver_code}
```
"""
    try:
        # v1のmax_tokens=8000は「2ファイル全文」を見込んだ値。関数単位パッチは
        # 出力がずっと小さくなる想定のため、上限も4000に下げてある
        # （万一長引いても打ち切られるだけで、成功時の速度には影響しない）。
        raw = call_llm(
            [{"role": "system", "content": _FIELD_REPAIR_SYSTEM_V2},
             {"role": "user", "content": prompt}],
            max_tokens=4000,
            temperature=0,
        )
        parsed = _parse_function_output(raw)
    except Exception as e:
        logger.warning(f"[Gate2 self-repair v2] LLM修正呼び出しに失敗: {e}")
        return None, None

    if not parsed["replacements"] and not parsed["additions"]:
        logger.warning("[Gate2 self-repair v2] 修正応答から関数パッチを抽出できませんでした。")
        return None, None

    try:
        new_converter = _apply_function_patches_in_memory(
            converter_code,
            [r for r in parsed["replacements"] if r["path"] == converter_path],
            [a for a in parsed["additions"] if a["path"] == converter_path],
        )
        new_solver = _apply_function_patches_in_memory(
            solver_code,
            [r for r in parsed["replacements"] if r["path"] == solver_path],
            [a for a in parsed["additions"] if a["path"] == solver_path],
        )
    except ValueError as e:
        logger.warning(f"[Gate2 self-repair v2] 関数パッチ適用失敗: {e}")
        return None, None

    return new_converter, new_solver


def attempt_field_consistency_repair(diffs: list, snake: str, max_attempts: int = 2) -> dict:
    """
    diffs（generate_domain_filesの戻り値）内の {snake}_converter.py /
    {snake}_solver.py ペアに静的field-check（check_converter_solver_field_consistency）
    を実行し、不一致があれば最大 max_attempts 回までLLMに修正させて diffs を書き換える。

    各回の出力は、直前の状態より不一致の総数が増えていないか（回帰ガード）を
    確認してから「これまでで最良の状態」として採用する。クリーンになった時点で
    打ち切る。max_attempts回試しても完全には解消しない場合、最良の状態
    （元より改善していれば改善後、そうでなければ元のまま）を diffs に書き戻す。

    返り値:
      {"attempted": bool, "healed": bool, "attempts": int,
       "before": dict|None, "after": dict|None}
        attempted: 修正を試みたか（converter/solverが両方diffsに揃っていない、
                   または元から不一致がない場合はFalse）
        healed:    最終的にfield-checkがクリーンになったか
        attempts:  実際にLLM修正呼び出しを行った回数
        before:    修正前の check_converter_solver_field_consistency 結果
        after:     採用した最終状態の同結果（attempted=Falseの場合はNone）
    """
    solver_path    = f"Backend/solvers/{snake}_solver.py"
    converter_path = f"Backend/dsl_transformer/{snake}_converter.py"

    solver_idx    = next((i for i, d in enumerate(diffs) if d.get("path") == solver_path), None)
    converter_idx = next((i for i, d in enumerate(diffs) if d.get("path") == converter_path), None)

    result = {"attempted": False, "healed": False, "attempts": 0, "before": None, "after": None}
    if solver_idx is None or converter_idx is None:
        return result

    best_converter = diffs[converter_idx].get("new_content", "")
    best_solver    = diffs[solver_idx].get("new_content", "")

    before = check_converter_solver_field_consistency(best_converter, best_solver)
    result["before"] = before
    if _is_field_check_clean(before):
        return result  # 元から一致している。修正不要。

    result["attempted"] = True
    best_check = before

    for attempt_num in range(1, max_attempts + 1):
        result["attempts"] = attempt_num
        # 2026-08-05変更: v2（関数単位パッチ、===REPLACE_FUNCTION===/===ADD_FUNCTION===）
        # をまず試す。段階Bの単体計測でv1（ファイル全文書き換え）比 約90%の時間短縮
        # （104.9秒→9.9秒、修正成功）を確認済み（ENGINEERING_LOG.md 2026-08-05追記4・5
        # 参照）。v2が関数を特定できない等で失敗した場合のみ、安全側としてv1（全文
        # 書き換え）にフォールバックする（複数関数にまたがる複雑な不一致等、v2が
        # まだ実地で検証できていないケースへの保険）。
        new_converter, new_solver = _call_field_repair_llm_v2(
            converter_path, best_converter, solver_path, best_solver, snake, best_check
        )
        if new_converter is None:
            logger.info(
                f"[Gate2 self-repair] {snake}: v2（関数単位パッチ）が失敗したため、"
                f"v1（ファイル全文書き換え）にフォールバックします（{attempt_num}回目）。"
            )
            new_converter, new_solver = _call_field_repair_llm(
                converter_path, best_converter, solver_path, best_solver, snake, best_check
            )
        if new_converter is None:
            break  # LLM呼び出し失敗（v1も失敗）。ここまでの最良状態で打ち切る。

        new_check = check_converter_solver_field_consistency(new_converter, new_solver)

        # 回帰ガード: 不一致の総数が悪化していなければ、この結果を新しい最良状態として採用する。
        if _field_check_total(new_check) <= _field_check_total(best_check):
            best_converter, best_solver, best_check = new_converter, new_solver, new_check
        else:
            logger.info(
                f"[Gate2 self-repair] {snake}: 修正{attempt_num}回目が悪化したため採用しません "
                f"（悪化前={_field_check_total(best_check)}件 → 悪化後={_field_check_total(new_check)}件）。"
            )

        if _is_field_check_clean(best_check):
            break

    result["after"] = best_check

    if _field_check_total(best_check) < _field_check_total(before):
        # 完全に直っていなくても、元より改善していれば診断結果として採用する
        diffs[converter_idx] = compute_diff(converter_path, best_converter)
        diffs[solver_idx]    = compute_diff(solver_path, best_solver)

    if _is_field_check_clean(best_check):
        result["healed"] = True
        logger.info(
            f"[Gate2 self-repair] {snake}: converter/solverのキー不一致を"
            f"{result['attempts']}回の修正で自動修正しました。"
        )
    else:
        logger.info(
            f"[Gate2 self-repair] {snake}: {max_attempts}回試行しても解消しませんでした "
            f"（修正前={_field_check_total(before)}件 → 最良={_field_check_total(best_check)}件）: {best_check}"
        )

    return result


# ─────────────────────────────────────────────────────────────
# Gate2動的チェック: ドメイン別バリデータ registry
#
# 背景: Backend/validators/ 配下にドメイン固有のDSLバリデータ（例:
# NurseShiftValidator）を追加した際、Gate2動的チェックから自動的に
# 呼び出せるようにするための最小限のレジストリ。
# 「snake_case ドメイン名 → Validatorクラス」の対応をここに1行足すだけで、
# run_gate2_dynamic_verification() が該当ドメインのシナリオ変換直後に
# .validate() を実行し、errors/warnings を report に合流させる。
# 未登録ドメインは従来通りスキップされる（solver.solve()のみで検証）。
# ─────────────────────────────────────────────────────────────

_DOMAIN_VALIDATORS: dict = {}


def _register_default_domain_validators() -> None:
    """既知ドメインの検証器を _DOMAIN_VALIDATORS に登録する。

    ここでのimport失敗（未登録ドメインのvalidatorsパッケージが存在しない等）は
    Gate2全体を止めないよう握りつぶす。新しいドメインバリデータを追加する場合は
    ここに setdefault() を1行足すだけでよい。

    【2026-07-13】旧"nurse_shift"登録（NurseShiftValidator）を削除した。
    NurseShift（週次夜勤上限なしの旧ベース版）自体をdelete_nurse_shift_base_domain.py で
    完全削除した際、このバリデータ登録・Backend/validators/nurse_shift_validator.py・
    Backend/dsl_repository/run_gate2_nurse_shift.py が削除対象漏れで孤立していたため、
    今回まとめて削除した。現時点で _DOMAIN_VALIDATORS に登録されたドメインはない
    （NurseShiftWeeklyCap用の専用バリデータは未実装）。
    """
    pass


_register_default_domain_validators()


# ─────────────────────────────────────────────────────────────
# Gate2動的チェック（2026-07-27追加）: CE上限（CPLEX Community Edition評価版
# モデルサイズ上限）の実機ストレス検証
#
# 背景: solvers/base/ce_limit_lns.py の汎用CE上限フォールバックは、これまで
# 各ドメインのユニットテスト（solve()をmonkeypatchして偽のCeLimitExceededError
# を発生させる）でしか配線を検証しておらず、「実際に上限に当たった時に本物の
# CP Optimizerエンジンが検知・フォールバックまで正しく動くか」は
# DESIGN_2026-07-26_generic_ce_limit_fallback.md 決定事項#12で「今後の課題」
# とされていた。2026-07-27にこのサンドボックスで実際にCP Optimizerエンジン
# （cpoptimizerバイナリ）が動くことを確認できたため、ここで実装する。
#
# 対象: solver.pyが solvers.base.ce_limit_lns をimportしているCPドメインのみ
# （TruckDispatcher/YardPlanningは専用の分解器(RouteDecomposer/
# DecomposerFactory)を持つため対象外。StoreSite等のMIPドメインは
# ce_limit_mip_fallback.py側で別のシグネチャ・別の対応方式を使うため、
# 本チェックのスコープ外＝今回は対象にしない）。
#
# 手法: {snake}_baseline.json を読み込み、ドメインごとに宣言した「分割軸に
# 相当するリストフィールド」を複製・拡大しながら実際にsolve()を呼び、本物の
# CE上限例外（"Problem size limit exceeded"等）を発生させる。発生したら、
# 結果が (a) ce_limit_unresolvable issue で graceful に終わっている
# （本ドメインにbatch_decomposerが無い場合）か、(b) _decompose_meta付きで
# 実際にバッチ分割フォールバックが機能している（batch_decomposerがある場合）
# かを確認する。CE上限の実機チェック自体は変数・制約の宣言数に対する
# ライセンス側の静的チェックのため、実際に探索を回すより先に即座に失敗する
# （2026-07-27に実機確認済み: LineChangeoverScheduler 0.02秒、
# CarSequencing 1.1秒）。そのため多少大きいサイズを試しても登録フロー全体の
# 所要時間への影響は小さい。
# ─────────────────────────────────────────────────────────────

# ドメインごとの「CE上限を発火させるために複製するリストフィールド」の宣言。
# ここに無いドメインは本チェックをスキップする（non-applicable扱い、
# 新しいCPドメインを追加する際はここに1行足すだけでよい）。
_CE_LIMIT_STRESS_INFLATE_FIELD: dict[str, tuple] = {
    # snake名: (フィールド名, そのリスト内エンティティのid キー名)
    "nurse_shift_weekly_cap":   ("staff", "id"),
    "meeting_room":             ("rooms", "id"),
    "line_changeover_scheduler": ("tasks", "id"),
    "car_sequencing":           ("car_types", "car_type_id"),
}


def _inflate_list_field(entities: list, target_count: int, id_field: str) -> list:
    """
    entities（baselineシナリオ由来の実データ）を複製し、target_count件になる
    まで増やす。複製元は既に実際に動くデータであることが保証されているため、
    スキーマ不整合のリスクなしにサイズだけを膨らませられる。
    id_fieldはユニーク性が必要な識別子フィールド（複製時にサフィックスを付与）。
    """
    if not entities or target_count <= len(entities):
        return copy.deepcopy(entities)
    out = list(copy.deepcopy(entities))
    i = 0
    while len(out) < target_count:
        base = entities[i % len(entities)]
        clone = copy.deepcopy(base)
        clone[id_field] = f"{clone.get(id_field, 'x')}_stress{i}"
        out.append(clone)
        i += 1
    return out


def run_gate2_ce_limit_stress_check(
    snake: str,
    convert_fn,
    solver_class,
    max_attempts: int = 6,
    max_total_seconds: float = 20.0,
    require_cp_engine: bool = True,
) -> dict:
    """
    {snake}_baseline.jsonを複製・拡大しながら実際にCE上限を発火させ、
    ドメインのCE上限フォールバック配線が実機で正しく動くかを検証する。

    2026-07-27追記（実機で発覚したハングの修正）: ドメインのsolve()が例外を
    自前でcatchして"solver_error"等のissueに変換して返す実装（MeetingRoom/
    CarSequencing等）の場合、CP Optimizerエンジン自体が使えない環境
    （cpoptimizerバイナリ不在）でも例外が外に伝播しないため、本関数の
    except Exceptionでは検知できない。この場合「CE上限がまだ発火していない
    だけ」と誤認して次のtargetでリトライを繰り返してしまい、docplexの内部
    エージェント生成処理が失敗を重ねることで**単発のsolve()呼び出し自体が
    ブロックする**（ループ外からの経過時間チェックでは検知できない）事象を
    実機（test_post_registration_fix.py経由）で確認した。このため、根本対策
    として require_cp_engine=True（既定）の場合は実行前に
    shutil.which("cpoptimizer") でエンジンの実在を確認し、無ければ即座に
    "engine_unavailable" を返して一切solveを試みない。壁時計時間の予算
    （max_total_seconds、既定20秒）は複数attempt間のみをチェックする
    セカンダリの安全弁（単発のsolve()自体がハングするケースは防げない）。
    テストでフェイクのsolver_classを使う場合はrequire_cp_engine=Falseを
    指定してこのチェックをバイパスできる。

    Returns:
        dict: {
          "status": "not_applicable" | "missing_baseline" | "engine_unavailable"
                    | "ce_limit_not_triggered" | "timed_out" | "graceful_no_adapter"
                    | "fallback_succeeded" | "exception",
          "sizes_tried": [...],       # 試行したエンティティ件数（該当時のみ）
          "triggered_at": int,        # CE上限が実際に発火した件数（該当時のみ）
          "issue_ids": [...],         # 発火時のresult["issues"]のidリスト（該当時のみ）
          "decompose_meta": dict,     # fallback_succeeded時のresult["_decompose_meta"]
          "traceback": str,           # exception時のみ
        }
    """
    import traceback as tb_module

    entry: dict = {"status": None}

    inflate_spec = _CE_LIMIT_STRESS_INFLATE_FIELD.get(snake)
    if inflate_spec is None:
        entry["status"] = "not_applicable"
        return entry

    if require_cp_engine and shutil.which("cpoptimizer") is None:
        entry["status"] = "engine_unavailable"
        return entry

    field_name, id_field = inflate_spec
    baseline_path = _SCENARIOS_DIR / f"{snake}_baseline.json"
    if not baseline_path.exists():
        entry["status"] = "missing_baseline"
        return entry

    baseline_dsl = json.loads(baseline_path.read_text(encoding="utf-8"))
    base_entities = baseline_dsl.get(field_name, [])
    if not base_entities:
        entry["status"] = "missing_baseline"
        return entry

    sizes_tried: list = []
    target = max(50, len(base_entities) * 10)
    start_time = time.time()

    for _attempt in range(max_attempts):
        if time.time() - start_time > max_total_seconds:
            entry["status"] = "timed_out"
            entry["sizes_tried"] = sizes_tried
            return entry

        sizes_tried.append(target)
        stress_dsl = copy.deepcopy(baseline_dsl)
        stress_dsl[field_name] = _inflate_list_field(base_entities, target, id_field)

        # 2026-07-27追記: CE上限がまだ発火しない小さすぎるtargetの場合、
        # ソルバーが本物のTimeLimit（既定30〜60秒）いっぱいまで探索してから
        # 次のtargetへ進んでしまい、max_attempts回×TimeLimit分の遅延に
        # なりうる（実機テストでタイムアウトを確認）。CE上限の検知自体は
        # 変数・制約の宣言数に対する静的チェックでTimeLimitに関係なく
        # 即座に発生するため、ここでは「発火しなかった場合の通常探索」の
        # 時間だけを短く抑える（発火した場合の判定結果には影響しない）。
        stress_config = dict(stress_dsl.get("config", {}))
        for time_key in ("time_limit_sec", "solve_time_sec", "time_limit"):
            if time_key in stress_config:
                stress_config[time_key] = 3
        stress_dsl["config"] = stress_config

        try:
            solver_input = convert_fn(stress_dsl) if convert_fn else stress_dsl
            solver_input.setdefault("issue_statuses", {})
            result = solver_class(solver_input).solve()
        except Exception:
            entry["status"] = "exception"
            entry["traceback"] = tb_module.format_exc()
            entry["sizes_tried"] = sizes_tried
            return entry

        issue_ids = {i.get("id") for i in result.get("issues", [])}
        if "ce_limit_unresolvable" in issue_ids:
            entry["status"] = "graceful_no_adapter"
            entry["sizes_tried"] = sizes_tried
            entry["triggered_at"] = target
            entry["issue_ids"] = sorted(issue_ids)
            return entry
        if "_decompose_meta" in result:
            entry["status"] = "fallback_succeeded"
            entry["sizes_tried"] = sizes_tried
            entry["triggered_at"] = target
            entry["decompose_meta"] = result["_decompose_meta"]
            return entry

        target *= 4

    entry["status"] = "ce_limit_not_triggered"
    entry["sizes_tried"] = sizes_tried
    return entry


# ─────────────────────────────────────────────────────────────
# Gate2動的チェック: 実ソルブ検証（baseline/infeasible）
#
# 背景: Gate1/Gate2設計合意（docs/DESIGN_2026-07-09_registration_gates_and_hard_soft.md）
# で定めた最小構成。例外を握り潰さない（HospitalShiftPlanner bug#1のように
# KeyErrorがexceptで飲み込まれ「解けているのに無言で失敗扱い」になることを
# 二度と起こさない）。期待ステータス（baseline→feasible, infeasible→infeasible）
# を確認する。
#
# 注記（2026-07-18）: 従来はここに tight シナリオ（「際どいが解ける」ケースを
# 想定し、baselineとのKPI比較で劣化方向を検出する仕組み）が含まれていたが、
# 実機で「tightの設計意図が曖昧になりやすく、デバッグエージェントがその解釈の
# 確認に往復を使うばかりで、登録所要時間の増加に見合う効果が薄い」という
# Koshoshiの判断により廃止した（MeetingRoom登録時の実例で判明）。
# 新規ドメインはbaseline/infeasibleの2シナリオのみを生成・検証する。
# 既存ドメインが持つtightシナリオファイル・DB登録は遡及削除しない
# （このロジック変更は新規登録のみに影響する）。
#
# 注記（V4.7）: このチェックは app.py の _run_domain_job（new_domain登録フロー）に
# 自動接続されている。write_files_for_dynamic_check() で承認前のsolver/converter/
# シナリオファイルを実ファイルとして書き出した上でこの関数を呼び、warningsを
# 静的Gate2と同じneeds_confirmationゲートに合流させることで、人間の確認画面は
# 1回のまま増やさない。Flaskは長時間稼働プロセスであるため、同一snake名の
# ドメインを複数回検証すると importlib のモジュールキャッシュが古いコードを
# 参照し続ける恐れがあり、既に sys.modules にある場合は importlib.reload() で
# 強制的に再読み込みする。
#
# 注記（V4.8）: solver.solve()を呼ぶ直前に、_DOMAIN_VALIDATORS に登録された
# ドメイン固有バリデータ（変換後のsolver_input DSLに対する構造的・意味的検証）
# があれば実行する。ソルバーが「例外なく解を返してしまう」ため feasible/infeasible
# 判定だけでは検出できない不具合（例: 資格要件を満たすスタッフの絶対数が
# required_countに足りていない）を、ソルブ前に機械的に洗い出す狙い。
# バリデータのerrorsもGate2の他の警告と同様にneeds_confirmationへの警告
# 追加であり、登録を直接ブロックしない。
# ─────────────────────────────────────────────────────────────

# Gate2動的チェック: 退化解検知（簡易版、2026-08-08追加）
#
# 背景: baselineシナリオはsolve()がfeasible=Trueを返しさえすれば「OK」と
# 判定されてきたが、実際には「割当対象も資源も存在するのに、ほとんど何も
# 割当されていない」退化解（例: coverage_rate=0付近）でもfeasible=Trueには
# なり得る。制約の書き方次第では「何も割当しない」ことが目的関数上最も
# 安価な解になってしまうケースがあり、この種の不具合はfeasible/infeasible
# 判定だけでは検出できない。
#
# 「簡易版」とした理由: ドメインごとにmetricsのフィールド名は自由（Stage2が
# 都度生成するため）で、汎用的に「割当対象の総数」「資源の総数」を機械的に
# 取り出す共通スキーマは無い。そこで、(1) 各ドメインのKPI慣習として広く
# 使われている`coverage_rate`キー（0.0〜1.0）がmetricsにある場合のみ判定対象とし、
# (2) 誤検知防止として、solver_input（converter出力）のトップレベルに
# 空でないlistフィールドが2種類以上ある場合のみ「割当対象・資源とも
# 存在する」の代理指標とみなす。この2条件を満たさない場合は判定をスキップし、
# 何も警告しない（false negativeを許容し、false positiveを避ける設計）。
#
# infeasibleシナリオは対象外（意図的に資源を逼迫させ未割当を多く出す設計の
# ため、同じ閾値で判定すると誤検知になる。Koshoshi合意、2026-08-08）。
#
# 適用範囲: 今後の新規登録（new_domain・拡張とも）から。既存登録済み
# ドメインへの遡及チェックは対象外（別タスク）。
_DEGENERATE_COVERAGE_RATE_THRESHOLD = 0.05


def _check_baseline_degenerate_solution(solver_input: dict, metrics: dict | None) -> str | None:
    """
    baselineシナリオの退化解検知（簡易版）。該当すれば警告文字列を、
    判定対象外・問題なしならNoneを返す。
    """
    if not isinstance(metrics, dict):
        return None
    rate = metrics.get("coverage_rate")
    if not isinstance(rate, (int, float)):
        return None
    if rate > _DEGENERATE_COVERAGE_RATE_THRESHOLD:
        return None
    if not isinstance(solver_input, dict):
        return None
    nonempty_lists = [k for k, v in solver_input.items() if isinstance(v, list) and len(v) > 0]
    if len(nonempty_lists) < 2:
        # シナリオ自体が薄く（割当対象・資源のいずれかが実質0件の可能性があり）、
        # 判定材料が不足しているため誤検知回避のためスキップする。
        return None
    return (
        f"[Gate2 退化解検知] baselineシナリオでcoverage_rate={rate}です。"
        f"割当対象・資源に相当するフィールド（{sorted(nonempty_lists)}）が空でないにも"
        "かかわらず、割当がほぼ0件の退化解になっている疑いがあります。制約の書き方次第で"
        "「何も割当しない」ことが目的関数上最も安価になっていないか確認してください。"
    )


def run_gate2_dynamic_verification(
    snake_name: str, scenario_suffixes: tuple = ("baseline", "infeasible")
) -> dict:
    """
    指定ドメイン（snake_case）の baseline/infeasible シナリオを
    converter → solver.solve() まで通しで実行し、例外を握り潰さずに検証する。
    _DOMAIN_VALIDATORS に登録があれば、solve()の直前にDSLバリデータも実行する。

    2026-07-18: scenario_suffixesの既定値からtightを外した（新規ドメインは
    baseline/infeasibleのみ生成するため）。既存ドメインに対してtightも含めて
    検証したい場合は、呼び出し側でscenario_suffixesを明示的に指定すればよい
    （このシグネチャは既存ドメイン向けの呼び出しとも互換性を保っている）。

    返り値の scenarios[suffix] 各エントリ:
      status:     "ok" | "exception" | "missing_file"
      feasible:   bool | None
      metrics:    dict | None（solver出力 solutions[0]["metrics"]、取得できれば）
      traceback:  str | None（例外発生時のみ、フルトレースバック）
      validation: dict | None（{"errors": [...], "warnings": [...]}、
                  対応するValidatorが登録されている場合のみ）

    warnings には、期待ステータスとの不一致、および登録済みドメインバリデータが
    検出したerrors/warningsを追加する。

    返り値の lexicographic_mechanism_check（2026-07-22追加、該当ドメインのみ）:
      OBJECTIVE_RECIPES[pascal] が mode="lexicographic" の場合にのみ存在する。
      solvers.base.objective_terms.verify_lexicographic_mechanism() の戻り値
      そのもの（{"ok", "true_lex_objectives", "expected_objectives",
      "big_m_small_diverged", "warnings", "errors"}）。ok=False の場合、
      errorsの内容は warnings にも "[Gate2 lexicographic機構チェック]" 接頭辞
      付きで合流させている。
    """
    import importlib
    import sys
    import traceback as tb_module

    snake  = snake_name
    pascal = _to_pascal(snake)
    report = {"domain": pascal, "snake": snake, "scenarios": {}, "warnings": []}

    def _import_fresh(mod_name: str):
        # 既にimport済みなら reload して、直前に書き出したばかりの新しい
        # ファイル内容を確実に反映させる（同一プロセス内での再検証対策）。
        if mod_name in sys.modules:
            return importlib.reload(sys.modules[mod_name])
        return importlib.import_module(mod_name)

    convert_fn = None
    try:
        converter_module = _import_fresh(f"dsl_transformer.{snake}_converter")
        convert_fn = getattr(converter_module, f"convert_{snake}_to_solver")
    except Exception as e:
        report["warnings"].append(f"converter読み込み失敗（4DSL非準拠ドメインの可能性、素通しで続行）: {e}")

    try:
        solver_module = _import_fresh(f"solvers.{snake}_solver")
        solver_class  = getattr(solver_module, f"{pascal}Solver")
    except Exception as e:
        report["warnings"].append(f"solver読み込み失敗（検証中止）: {e}")
        return report

    validator_class = _DOMAIN_VALIDATORS.get(snake)

    # 【2026-07-22追加】lexicographicモード目的関数のメカニズム回帰ゲート。
    # OBJECTIVE_RECIPES[pascal] が mode="lexicographic" を宣言している場合、
    # このdocplex/CP Optimizer環境で mdl.minimize_static_lex() が正しく解ける
    # ことを、solvers.base.objective_terms.verify_lexicographic_mechanism() の
    # ドメイン非依存識別テストで確認する。過去にlexicographic目的がBig-M近似で
    # 偽装されていた反省から、実装しただけでは信用せず実ソルブで検証する
    # （objective_terms.py の「Big-M代替の禁止」節・Key Learnings参照）。
    # 未登録ドメインやmode未指定（weighted_sum）のドメインはスキップされる。
    try:
        from solvers.base.objective_terms import OBJECTIVE_RECIPES, verify_lexicographic_mechanism
        recipe = OBJECTIVE_RECIPES.get(pascal)
        if recipe is not None and recipe.get("mode") == "lexicographic":
            lex_report = verify_lexicographic_mechanism()
            report["lexicographic_mechanism_check"] = lex_report
            if not lex_report.get("ok"):
                for err in lex_report.get("errors", []):
                    report["warnings"].append(f"[Gate2 lexicographic機構チェック] ERROR: {err}")
            for warn in lex_report.get("warnings", []):
                report["warnings"].append(f"[Gate2 lexicographic機構チェック] WARNING: {warn}")
    except Exception as e:
        report["warnings"].append(f"[Gate2 lexicographic機構チェック] 実行に失敗（非ブロッキング）: {e}")

    for suffix in scenario_suffixes:
        scenario_path = _SCENARIOS_DIR / f"{snake}_{suffix}.json"
        entry = {"status": None, "feasible": None, "metrics": None, "traceback": None, "validation": None}
        if not scenario_path.exists():
            entry["status"] = "missing_file"
            report["scenarios"][suffix] = entry
            continue
        try:
            business_dsl = json.loads(scenario_path.read_text(encoding="utf-8"))
            solver_input = convert_fn(business_dsl) if convert_fn else business_dsl
            solver_input.setdefault("issue_statuses", {})
            # 2026-07-24追加: 解チェッカー（DESIGN_2026-07-21 3-1節）。
            # Gate2登録時はコストを気にせずフルチェックしてよいため、各ソルバーの
            # 解チェッカー（solvers/base/solution_checker.run_or_defer）に
            # 「閾値を無視して常に同期フル実行する」ことを伝える。本番solve時
            # （このフラグが立っていない通常の/baseline呼び出し）とはここでのみ
            # 挙動を変える、最小限のフラグ渡し。
            solver_input.setdefault("_gate2_full_check", True)

            if validator_class is not None:
                v_result = validator_class(solver_input).validate()
                entry["validation"] = {"errors": v_result.errors, "warnings": v_result.warnings}
                for err in v_result.errors:
                    report["warnings"].append(f"[Gate2 domain-validator/{suffix}] ERROR: {err}")
                for warn in v_result.warnings:
                    report["warnings"].append(f"[Gate2 domain-validator/{suffix}] WARNING: {warn}")

            result = solver_class(solver_input).solve()
            entry["status"]   = "ok"
            entry["feasible"] = result.get("feasible")
            entry["issues"]   = result.get("issues", [])
            solutions = result.get("solutions", [])
            entry["metrics"] = solutions[0].get("metrics") if solutions else None

            # 2026-08-08追加: 退化解検知（簡易版）。baselineのみ対象
            # （infeasibleは意図的に資源を逼迫させるため対象外。上部コメント参照）。
            if suffix == "baseline" and entry.get("feasible"):
                degenerate_warning = _check_baseline_degenerate_solution(solver_input, entry["metrics"])
                if degenerate_warning:
                    report["warnings"].append(degenerate_warning)

            # 2026-08-03追加: solver出力 → UI DSL変換（{snake}_ui_converter.py）も
            # 検証範囲に含める。背景: DepotRoutePlanner較正実験（B×Fハイブリッド）で、
            # solver.pyの出力キー（例: travel_distance）と、別ファイルとして生成された
            # ui_converter.pyが期待するキー（travel_distance_km）が一致せずKeyErrorで
            # 実機クラッシュするバグを発見したが、従来この関数はconverter→solver.solve()
            # までしか検証しておらず、solver↔ui_converter間のスキーマ不一致を一切
            # 検知できていなかった。app.py _solve_4dsl_generic()と同じ呼び出し規約
            # （convert_{snake}_to_ui(plan_output, business_dsl=dsl)）を模倣し、solver結果を
            # 実際にUI DSLへ変換できることまで確認する。ui_converter.py自体が存在しない
            # （4DSL非準拠）ドメインは ImportError/AttributeError として無視しスキップする。
            try:
                ui_converter_module = _import_fresh(f"dsl_transformer.{snake}_ui_converter")
                convert_to_ui = getattr(ui_converter_module, f"convert_{snake}_to_ui")
                solutions_for_ui = result.get("solutions", [])
                if solutions_for_ui:
                    for plan in solutions_for_ui:
                        plan_output = {**result, "solutions": [plan]}
                        convert_to_ui(plan_output, business_dsl=business_dsl)
                else:
                    convert_to_ui(result, business_dsl=business_dsl)
            except (ImportError, AttributeError):
                pass  # ui_converter未実装（4DSL非準拠ドメイン）は対象外
        except Exception:
            entry["status"]    = "exception"
            entry["traceback"] = tb_module.format_exc()
        report["scenarios"][suffix] = entry

    # baselineは従来通り厳格に「feasible=True」を期待する。
    baseline_entry = report["scenarios"].get("baseline")
    if baseline_entry is not None and baseline_entry.get("status") == "ok":
        if baseline_entry.get("feasible") is not True:
            report["warnings"].append(
                f"baseline: 期待feasible=True だが実際は {baseline_entry.get('feasible')}"
            )

    # 2026-08-08追加（Koshoshi合意）: infeasibleシナリオの期待値を
    # 「必ずfeasible=False」から「feasible=False、または深刻な未割当
    # （coverage_rateが低いfeasible=True）のいずれかを許容」に緩和する。
    #
    # 背景: MedicalAppointmentScheduler登録で発覚したfalse positive。
    # ヒアリング§4-1で「人数不足を許容し、できるだけ近づける形で他を最適化」
    # （＝資源不足は解なしではなく未割当として表現する設計）が既に確定して
    # いるドメインでは、CPモデルがoptional interval_var等で「全件未割当」を
    # 常に有効な解として許すため、構造上どれだけ資源を逼迫させても本物の
    # infeasible（feasible=False）にはならない。infeasibleシナリオを
    # 「必ずinfeasibleになるべき」と一律に期待する従来のチェックは、この種の
    # 未割当許容型ドメインの正しい設計と噛み合わずfalse positiveを生む。
    #
    # ここでは、Item①（退化解検知）で既に前提としているcoverage_rateという
    # 業界（プロジェクト内）慣習KPIを再利用する。infeasibleシナリオが
    # feasible=Trueでも、coverage_rateが十分低ければ「意図通り資源を逼迫
    # させ、未割当という形で表現できている」とみなし許容する。coverage_rate
    # が無い（その慣習を使っていない）ドメインでは、判定材料が無いため
    # 従来通り厳格にfeasible=Falseを期待する（false negativeよりfalse
    # positiveを避ける今回の目的には合わないため、フォールバックは変えない）。
    _INFEASIBLE_SCENARIO_MAX_ACCEPTABLE_COVERAGE_RATE = 0.8

    infeasible_entry = report["scenarios"].get("infeasible")
    if infeasible_entry is not None and infeasible_entry.get("status") == "ok":
        feasible = infeasible_entry.get("feasible")
        if feasible is False:
            pass  # 期待通り（本物のinfeasible）
        elif feasible is True:
            metrics = infeasible_entry.get("metrics")
            coverage_rate = metrics.get("coverage_rate") if isinstance(metrics, dict) else None
            if (isinstance(coverage_rate, (int, float))
                    and coverage_rate <= _INFEASIBLE_SCENARIO_MAX_ACCEPTABLE_COVERAGE_RATE):
                pass  # 未割当許容型ドメインとして許容（深刻な未割当を確認できた）
            else:
                report["warnings"].append(
                    "infeasible: 期待feasible=False だが実際は True"
                    + (f"（coverage_rate={coverage_rate}）" if coverage_rate is not None
                       else "（coverage_rateが取得できず、未割当許容型ドメインかどうか判定不能）")
                )
        else:
            report["warnings"].append(f"infeasible: 期待feasible=False だが実際は {feasible}")

    # 2026-07-31追加: "solve_failed" issue id 規約チェック。
    # Frontend（useStudioState.tsの hasSolveFailed 判定、InfeasibleView.tsx）は
    # feasibleフラグではなく issues[].id === "solve_failed" のみを見て
    # Infeasible画面（制約見直しタブ・AI緩和提案）を表示するかどうかを決めている
    # （この規約はStage2システムプロンプトにも明記されているが、想定外例外の
    # ハンドリング箇所にしか書かれておらず、業務上のinfeasible判定（msol is None等）
    # を手で書く箇所には注意書きが及んでいなかった。NursingWorkloadBalance登録で、
    # solver.pyがこの分岐で独自のid（"infeasible"）を使ってしまい、feasible=Falseで
    # 正しく解なし判定していたにもかかわらずFrontendがInfeasible画面を出さない
    # 不具合が実際に発生した）。feasible=False の結果を返しているのに
    # id="solve_failed" のissueが1件も無ければ、Frontendの表示が壊れている可能性が
    # 高いため警告する。
    for suffix, entry in report["scenarios"].items():
        if entry.get("status") != "ok" or entry.get("feasible") is not False:
            continue
        issues = entry.get("issues") or []
        if not any(i.get("id") == "solve_failed" for i in issues):
            found_ids = [i.get("id") for i in issues]
            report["warnings"].append(
                f"[Gate2 solve_failed規約チェック] {suffix}: feasible=Falseですが、"
                f"issuesにid='solve_failed'が含まれていません（実際のid: {found_ids}）。"
                "Frontend（InfeasibleView.tsx等）はこのidだけを見てInfeasible画面の"
                "表示可否を決めているため、画面上はFeasible扱いのまま表示される"
                "退行が起きている可能性があります。"
            )

    # 2026-07-27追加: CE上限（CPLEX Community Edition評価版）の実機ストレス検証。
    # solver_classがロードできた場合のみ実行（converter読み込み失敗時はconvert_fn=None
    # のまま続行、素通し）。_CE_LIMIT_STRESS_INFLATE_FIELDに登録の無いドメイン
    # （TruckDispatcher/YardPlanning等、専用分解器を持つドメイン）はnot_applicableで
    # 即座に返るため、対象外ドメインへのコスト増加はない。
    try:
        ce_limit_report = run_gate2_ce_limit_stress_check(snake, convert_fn, solver_class)
        report["ce_limit_stress"] = ce_limit_report
        status = ce_limit_report.get("status")
        if status == "exception":
            report["warnings"].append(
                f"[Gate2 CE上限ストレス検証] 例外が発生しました（CE上限を握り潰さず"
                f"伝播させてしまっている可能性）: {ce_limit_report.get('traceback', '').splitlines()[-1] if ce_limit_report.get('traceback') else ''}"
            )
        elif status == "ce_limit_not_triggered":
            report["warnings"].append(
                f"[Gate2 CE上限ストレス検証] sizes_tried={ce_limit_report.get('sizes_tried')}まで"
                f"複製しましたが、CPLEXの無料版のモデルサイズ上限を発火させられませんでした"
                f"（本チェックの限界の可能性もあるため非ブロッキング。詳細な実機検証が"
                f"必要な場合は手動でより大きいシナリオを試してください）。"
            )
        elif status == "timed_out":
            report["warnings"].append(
                f"[Gate2 CE上限ストレス検証] 壁時計時間の予算（既定20秒）を超えたため"
                f"打ち切りました（sizes_tried={ce_limit_report.get('sizes_tried')}）。"
                f"CP Optimizerエンジン自体が利用できない環境（cpoptimizerバイナリ不在）で、"
                f"solve()が内部で例外を握り潰して通常のエラー結果を返す実装のドメインの場合に"
                f"発生しうる（非ブロッキング。実機でCP Optimizerが利用可能な環境で"
                f"再実行して確認してください）。"
            )
    except Exception as e:
        report["warnings"].append(f"[Gate2 CE上限ストレス検証] 実行に失敗（非ブロッキング）: {e}")

    return report


def write_files_for_dynamic_check(diffs: list, snake: str) -> list[str]:
    """
    run_gate2_dynamic_verification() が実際にimport・実行できるよう、
    承認前のdiffsのうち動的検証に必要な最小限のファイル（solver.py / converter.py /
    baseline・infeasibleシナリオJSON）だけを実ファイルとして書き出す。

    apply_domain_files() と異なり、patches適用・TAG_MAP登録・DB上のシナリオ登録は
    一切行わない（人間確認前の段階のため）。人間が承認すれば、その後の
    apply_domain_files() が同じ内容を再度書き込み（idempotent）し、
    残りの登録処理（patches/TAG_MAP/DB）を行う。
    """
    written = []
    target_suffixes = ("baseline", "infeasible")
    wanted_paths = {
        f"Backend/solvers/{snake}_solver.py",
        f"Backend/dsl_transformer/{snake}_converter.py",
        f"Backend/i18n/{snake}_messages.py",  # 2026-08-21追加: 2026-08-11のi18n必須化(_I18N_MESSAGE_DICT_INSTRUCTION)
                                                  # 以降、solver.pyがsolve()内でこのモジュールに依存するため、
                                                  # 動的検証時点でもディスクに存在させる必要がある
                                                  # (ReviewDocument登録時にModuleNotFoundErrorで発覚)。
        *(f"Backend/dsl_repository/scenarios/{snake}_{suf}.json" for suf in target_suffixes),
    }
    for item in diffs:
        path = item.get("path", "")
        if path not in wanted_paths:
            continue
        content = item.get("new_content", "")
        try:
            if path.endswith("_solver.py"):
                content = _sanitize_solver_code(content, path)
            abs_path = _resolve_path(path)
            abs_path.parent.mkdir(parents=True, exist_ok=True)
            abs_path.write_text(content, encoding="utf-8")
            written.append(path)
        except Exception as e:
            logger.warning(f"[gate2_dynamic_prep] 書き込み失敗（動的検証をスキップ）: {path} — {e}")
    return written


def cleanup_dynamic_check_files(paths: list[str]) -> None:
    """
    write_files_for_dynamic_check() が書き出したファイルを削除し、人間確認前（承認前）は
    ディスクに何も書き込まれていない、という従来の不変条件を保つ。
    動的検証はこの関数呼び出し前に完了している前提。削除対象は
    write_files_for_dynamic_checkが新規作成したファイルのみ（_run_domain_job側で
    check_domain_existsで事前に重複なしを確認済みのため、上書きリスクはない）。
    承認後はapply_domain_files()が同じ内容を正式に再度書き込む。
    """
    for path in paths:
        try:
            abs_path = _resolve_path(path)
            if abs_path.exists():
                abs_path.unlink()
        except Exception as e:
            logger.warning(f"[gate2_dynamic_prep] クリーンアップ失敗: {path} — {e}")


def refresh_diffs_from_disk(diffs: list, written_paths: list[str]) -> list:
    """
    2026-07-16追加（デバッグチャット構想の再設計）: write_files_for_dynamic_check() が
    書き出したdraftファイル（written_paths）の「今の」ディスク上の内容を読み直し、
    diffs内の対応エントリのnew_contentをそれで上書きする。

    指摘事項が出た後、Stage1a/Stage2を自動で再実行するのではなく、実ファイルとして
    残っているdraftを人間（実質Claude）が直接編集して直す設計に変えたため、
    「直った後の状態」をdiffsに反映してrun_gate2_checks()の再検証やapply_domain_files()
    に渡すために必要。
    """
    written_set = set(written_paths)
    updated = []
    for item in diffs:
        path = item.get("path", "")
        if path in written_set:
            abs_path = _resolve_path(path)
            if abs_path.exists():
                item = {**item, "new_content": abs_path.read_text(encoding="utf-8")}
        updated.append(item)
    return updated


def run_post_registration_fix(snake: str, domain_name: str, warnings: list,
                               should_stop=None, hearing_texts: list | None = None) -> dict:
    """
    2026-07-18f追加: 登録が完了した後でも、Gate2の残存警告（result.gate2_warnings、
    「残りは誤検知と判断し、このまま登録する」で確定した後に残っていたもの）を
    人間が任意で「今すぐ直したい」と思った場合の入口。

    登録前のフロー（run_gate2_checks / _run_confirm_job）とは根本的に違う点:
    修正対象はもう pending/diffs（人間承認待ちの下書き）ではなく、既に登録済みの
    ライブファイル（Backend/solvers/{snake}_solver.py 等）そのもの。
    apply_domain_files()のような「承認後にまとめて書き込む」段階は無く、
    debug_agentのwrite_fileツールがこのパスへ直接書き込む。

    2026-08-10修正（#31設計3-2の最小実装）: 以前はhearing_textsを意図的に空リスト
    固定で渡していた（登録ジョブのpendingはjob_gcで消えている可能性があり、元の
    ヒアリング文脈を確実に復元する手段が無かったため）。現在は登録確定時
    （app.py _finalize_confirm_job）にdsl_evolution_logのdsl_patchへhearing_textsを
    保存するようにしたので、呼び出し側（app.py domain_post_register_fix）がそこから
    読み出して渡せる。渡されなかった場合は従来通り空リストとして扱う
    （後方互換。文脈が無いとエージェントはask_humanで止まりやすくなりうる——その
    場合、修正を完了させずに「要対応」として返す。登録前フローのような
    一時停止→再開の往復はv1では未対応）。

    戻り値:
      {"fixed_summary": str, "gate2_warnings": list[str], "turns_used": int,
       "stopped_reason": str}
      gate2_warningsは、修正後に静的field-check・動的検証を再実行した結果
      （直っていれば減る／空になる。完全解消を保証するものではない）。
    """
    from debug_agent import run_debug_agent

    solver_path        = f"Backend/solvers/{snake}_solver.py"
    converter_path     = f"Backend/dsl_transformer/{snake}_converter.py"
    ui_converter_path  = f"Backend/dsl_transformer/{snake}_ui_converter.py"

    written_paths = [solver_path, converter_path]
    if _resolve_path(ui_converter_path).exists():
        written_paths.append(ui_converter_path)

    agent_result = run_debug_agent(
        questions=warnings,
        written_paths=written_paths,
        domain_name=domain_name,
        hearing_texts=hearing_texts or [],
        snake_name=snake,
        max_turns=5,
        should_stop=should_stop,
        # 2026-08-10: 段階B A/Bテストでトークン-19%・所要時間-5%を確認
        # （Koshoshi承認、詳細はENGINEERING_LOG.md 2026-08-10追記4）。
        # 段階C（本番切替）として有効化。
        summarize_stale_reads=True,
    )

    if agent_result.get("stopped_reason") == "waiting_for_human":
        pending_q = agent_result.get("pending_question", "")
        logger.info(f"[post_registration_fix] {snake}: エージェントが業務判断待ちで停止 "
                    f"（v1では未対応、要対応として返す）: {pending_q}")
        return {
            "fixed_summary": "AIエージェントが業務判断が必要な質問をしたため、修正は完了していません。"
                              "手動で内容を確認・修正してください。",
            "gate2_warnings": [f"（要対応・登録後修正では自動応答できません）{pending_q}", *warnings],
            "turns_used": agent_result.get("turns_used", 0),
            "stopped_reason": "waiting_for_human",
        }

    # 修正後の再検証: run_gate2_checks全体（自己修復ループ・カバレッジLLM呼び出し）は
    # ここでは回さない。それは「新規生成の妥当性を一から確認する」用の重い処理で、
    # 登録後の「今回の修正で直ったか」の確認には過剰（実機ログで自己修復ループが
    # 1回100秒前後かかることを確認済み。docs/ENGINEERING_LOG.md 2026-07-18e参照）。
    new_warnings: list[str] = []
    try:
        converter_code = _resolve_path(converter_path).read_text(encoding="utf-8")
        solver_code    = _resolve_path(solver_path).read_text(encoding="utf-8")
        field_check = check_converter_solver_field_consistency(converter_code, solver_code)
        if field_check["missing_in_converter"]:
            new_warnings.append(
                f"{converter_path} が一度も出力しないキー: {field_check['missing_in_converter']} "
                f"（{solver_path} が参照）"
            )
        if field_check["unused_in_solver"]:
            new_warnings.append(
                f"{solver_path} が一度も参照しないキー: {field_check['unused_in_solver']} "
                f"（{converter_path} が出力）"
            )
    except Exception as e:
        new_warnings.append(f"修正後の静的field-check実行に失敗: {e}")

    try:
        dyn_report = run_gate2_dynamic_verification(snake)
        for w in dyn_report.get("warnings", []):
            new_warnings.append(f"[動的検証] {w}")
    except Exception as e:
        new_warnings.append(f"修正後の動的検証実行に失敗: {e}")

    if agent_result.get("needs_human_decision"):
        # 2026-07-19: app.py の _advance_after_agent_round と同じ理由で
        # 「（人間の判断が必要）」→「（未解決）」に統一。この画面（登録後の
        # 「今すぐ直す」結果表示）にも個別回答の手段は無く、「判断が必要」と
        # 言うと回答できるかのような誤解を招くため。
        for item in agent_result["needs_human_decision"]:
            new_warnings.append(f"（未解決）{item}")

    logger.info(f"[post_registration_fix] {snake}: 修正完了 turns={agent_result.get('turns_used', 0)}, "
                f"残警告={len(new_warnings)}件（修正前={len(warnings)}件）")

    return {
        "fixed_summary": agent_result.get("fixed_summary", ""),
        "gate2_warnings": new_warnings,
        "turns_used": agent_result.get("turns_used", 0),
        "stopped_reason": agent_result.get("stopped_reason", ""),
    }


def run_gate2_checks(diffs: list, snake: str, domain_name: str, hearing_texts: list,
                      on_stage=None, prior_coverage: dict | None = None,
                      prior_diffs: list | None = None) -> dict:
    """
    2026-07-16追加（デバッグチャット構想の再設計）: new_domain登録のGate2チェック一式
    （自己修復ループ・静的field-check・ヒアリング⇔コード カバレッジLLMチェック・
    動的検証・業務向け言い換え）を1箇所にまとめたもの。

    以前の設計との違い: 従来は指摘が出ると「回答をhearing_textsに追記してStage1a〜
    Stage2をまるごと再実行する」自動ループになっていたが、これは「自動で直るという
    前提を置かない」という元々の方針に反していた（実機テストでも指摘が0件に収束せず
    ループするだけだった）。正しい設計は、指摘が出た生成物（このdiffsが指す実ファイル）
    を人間（実質Claude）が直接読んで直すこと。そのため、指摘がある間は
    write_files_for_dynamic_check() が書き出したdraftファイルを削除せず、Claudeが
    通常のファイル編集ツールで直接修正できる状態のままディスクに残す。修正後は
    Stage1a/Stage2を再実行するのではなく、refresh_diffs_from_disk() でdiffsを
    最新のディスク内容に更新した上で、この関数をもう一度呼んでGate2だけを
    再検証する（_run_confirm_job から呼ばれる想定）。

    on_stage: 2026-08-07追加（Koshoshi依頼: Stage2の時間内訳を①LLM生成本体／
      ②Gate2静的チェック／③Gate2動的検証（実ソルブ）の最低3分割で見られるようにする
      計装、CSPLIB_UNIMPLEMENTED_PRIORITY系の引き継ぎメモ対応）。
      呼び出し可能なら on_stage(stage_name: str) をフェーズの境目ごとに呼ぶ。
      app.py側は job_set(job_id, stage=stage_name) を渡すことで、既存の
      domain_job_timing_log（tools/domain_job_timing_report.py）にそのまま
      粒度が反映される（repository.py・job_set()自体の変更は不要——stageが
      変化するたびにstage_timelineへ自動記録される既存の仕組みに乗るだけ）。
      Noneのままなら何もしない（既存呼び出し元との後方互換のため必須引数にしない）。
      内訳の対応:
        verifying_self_repair      … Gate2自己修復ループ（converter/solverキー不一致のLLM自動修正）
        verifying_static_check     … 静的field-check等（AST走査、非LLM）。②に相当
        verifying_hearing_coverage … ヒアリング⇔コード カバレッジLLM判定（detect_hearing_dsl_gaps）
        verifying_tech_conformance … Gate2構造適合チェック
        verifying_dynamic_check    … 実ソルブ検証（write_files_for_dynamic_check + run_gate2_dynamic_verification）。③に相当
        verifying_humanize         … 指摘の業務向け言い換え（humanize_technical_findings一式）
      このコールバックが例外を投げても本体の処理は止めない（計装のためにGate2を
      失敗させては本末転倒のため、_emit_stage()内でtry/exceptして握りつぶす）。

    prior_coverage / prior_diffs: 2026-08-09追加（登録時間短縮タスク2の再開、
      hearing_dsl_gaps差分渡し化）。debug_agentの1ラウンド後にこの関数を
      再呼び出しする際（_advance_after_agent_round経由）、直前のrun_gate2_checks()
      が返した"coverage"（前回のカバレッジ判定結果）と、その時点のdiffs
      （debug_agent実行前のコード）を渡すと、detect_hearing_dsl_gaps()の
      コード全文渡しではなく、変更差分のみを渡すdetect_hearing_dsl_gaps_incremental()
      を使う。単体検証（`tools/stage_b_hearing_dsl_gaps_diff_mode_validation.py`、
      2026-08-09）で所要時間88%短縮・判定精度は全文再チェックと実質一致することを
      確認済み。両方とも省略した場合（初回チェック時等）は、従来通り
      detect_hearing_dsl_gaps()の全文渡しを使う（後方互換）。

    戻り値:
      {"questions": [...], "written_paths": [...]（ディスクに残っている draft ファイルパス）,
       "coverage": dict | None（今回のカバレッジ判定結果。次回呼び出し時にprior_coverageとして
       渡せる。hearing_coverageチェック自体をスキップした場合はNone）}
    """
    def _emit_stage(stage_name: str) -> None:
        if on_stage is None:
            return
        try:
            on_stage(stage_name)
        except Exception as e:
            logger.warning(f"[gate2_checks] on_stage({stage_name!r})の呼び出しに失敗（計装のみのため処理は継続）: {e}")

    repair_notes: list[str] = []
    _emit_stage("verifying_self_repair")
    try:
        repair_result = attempt_field_consistency_repair(diffs, snake)
        if repair_result["attempted"]:
            before, after, attempts = repair_result["before"], repair_result["after"], repair_result["attempts"]
            if repair_result["healed"]:
                repair_notes.append(
                    f"（自動修復）converter/solverのキー不一致を自動検出し、{attempts}回の修正で解消しました"
                    f"（修正前: missing_in_converter={before['missing_in_converter']}, "
                    f"unused_in_solver={before['unused_in_solver']}）。"
                )
            elif _field_check_total(after) < _field_check_total(before):
                repair_notes.append(
                    f"（自動修復試行・部分改善）converter/solverのキー不一致を検出し{attempts}回修正を試みましたが、"
                    f"完全には解消しませんでした（修正前 計{_field_check_total(before)}件 → "
                    f"修正後 計{_field_check_total(after)}件）。下記のfield-check警告を確認してください。"
                )
            else:
                repair_notes.append(
                    f"（自動修復試行・未解消）converter/solverのキー不一致を検出し{attempts}回修正を試みましたが、"
                    "解消しませんでした。下記のfield-check警告を確認してください。"
                )
    except Exception as e:
        logger.warning(f"[gate2_checks] 自己修復ループをスキップ（実行エラー）: {e}", exc_info=True)

    _emit_stage("verifying_static_check")
    try:
        _scan_result = scan_diffs_for_warnings(diffs)
        sanitizer_warnings = _scan_result["warnings"]
        unused_in_solver_warnings = _scan_result["unused_in_solver_warnings"]
        big_m_warnings = _scan_result["big_m_warnings"]
        absent_value_warnings = _scan_result["absent_value_warnings"]
        missing_in_dsl_for_solver_warnings = _scan_result["missing_in_dsl_for_solver_warnings"]
        mip_self_check_warnings = _scan_result["mip_self_check_warnings"]
    except Exception as e:
        logger.warning(f"[gate2_checks] 静的チェックをスキップ（実行エラー）: {e}", exc_info=True)
        sanitizer_warnings = []
        unused_in_solver_warnings = []
        big_m_warnings = []
        absent_value_warnings = []
        missing_in_dsl_for_solver_warnings = []
        mip_self_check_warnings = []

    required_gap_warnings: list[str] = []
    optional_gap_summary: list[str] = []
    coverage: dict | None = None

    _emit_stage("verifying_hearing_coverage")
    try:
        artifacts = extract_domain_artifacts_from_diffs(diffs, snake)
        if artifacts["converter_code"] and artifacts["solver_code"] and artifacts["scenarios"]:
            # 2026-08-09追加: prior_coverage/prior_diffsが両方渡されていれば、
            # 差分渡し版（detect_hearing_dsl_gaps_incremental）を使う。
            # prior側から対象ファイルを抽出できない、またはcoverage未取得
            # （前回hearing_coverageチェック自体がスキップされていた等）の場合は、
            # 安全側に倒して従来の全文渡しにフォールバックする。
            use_incremental = False
            if prior_coverage is not None and prior_diffs is not None:
                try:
                    prior_artifacts = extract_domain_artifacts_from_diffs(prior_diffs, snake)
                    converter_diff = build_code_diff(
                        prior_artifacts.get("converter_code"), artifacts.get("converter_code"), "converter.py"
                    )
                    solver_diff = build_code_diff(
                        prior_artifacts.get("solver_code"), artifacts.get("solver_code"), "solver.py"
                    )
                    ui_converter_diff = build_code_diff(
                        prior_artifacts.get("ui_converter_code"), artifacts.get("ui_converter_code"), "ui_converter.py"
                    )
                    use_incremental = True
                except Exception as e:
                    logger.warning(
                        f"[gate2_checks] hearing_dsl_gaps差分渡しの準備に失敗、全文渡しにフォールバック: {e}"
                    )

            if use_incremental:
                coverage = detect_hearing_dsl_gaps_incremental(
                    domain_name=domain_name, hearing_texts=hearing_texts,
                    prior_result=prior_coverage,
                    converter_diff=converter_diff, solver_diff=solver_diff,
                    ui_converter_diff=ui_converter_diff,
                )
            else:
                coverage = detect_hearing_dsl_gaps(
                    domain_name=domain_name, hearing_texts=hearing_texts,
                    dsl_scenarios=artifacts["scenarios"],
                    converter_code=artifacts["converter_code"], solver_code=artifacts["solver_code"],
                    ui_converter_code=artifacts.get("ui_converter_code"),
                )
            items = coverage.get("items", [])
            gaps = [i for i in items if i.get("status") == "gap"]
            required_gaps = [g for g in gaps if g.get("priority") == "required"]
            optional_gaps = [g for g in gaps if g.get("priority") != "required"]
            for g in required_gaps:
                required_gap_warnings.append(
                    f"ヒアリング{g.get('hearing_section','?')}節（必須）の要件が実装に見当たりません: "
                    f"{g.get('requirement','')}（{g.get('evidence','')}）"
                )
            if optional_gaps:
                optional_gap_summary.append(
                    f"（参考）ヒアリングの任意節の要件のうち{len(optional_gaps)}件が実装に見当たりません"
                    f"（節: {sorted({g.get('hearing_section','?') for g in optional_gaps})}）。"
                    "必須ではないため確認は必須ではありませんが、詳細はGate2カバレッジログを参照してください。"
                )
        else:
            logger.info("[gate2_checks] hearing_dsl_gaps: 対象ファイル（converter/solver/シナリオ）が揃わずスキップ")
    except Exception as e:
        logger.warning(f"[gate2_checks] hearing_dsl_gapsチェックをスキップ（実行エラー）: {e}", exc_info=True)

    # 2026-08-02追加: Gate2構造適合チェック（Phase3、advisory・非ブロッキング）。
    # lookup_family_reference()をdomain_name/hearing_textsだけで内部再利用するため、
    # このrun_gate2_checks()自体のシグネチャは変更不要（呼び出し元app.pyの2箇所
    # （初回登録・デバッグエージェント再検証ループ）双方に自動的に適用される）。
    tech_conformance_warnings: list[str] = []
    _emit_stage("verifying_tech_conformance")
    try:
        _tech_artifacts = extract_domain_artifacts_from_diffs(diffs, snake)
        tech_conformance_warnings = check_family_technology_conformance(
            domain_name, hearing_texts, _tech_artifacts.get("solver_code")
        )
    except Exception as e:
        logger.warning(f"[gate2_checks] Gate2構造適合チェックをスキップ（実行エラー）: {e}", exc_info=True)

    # 2026-08-11追加: i18nメッセージカバレッジチェック（advisory・非ブロッキング）。
    # 詳細は _check_i18n_message_coverage のdocstring・直前のコメント参照。
    # run_gate2_checks()自体がnew_domainフル生成ルート（app.py `_run_domain_job`の
    # generate_domain_files()直後）と、その確認画面からの再検証ループでしか
    # 呼ばれていない（base_domain/extension_gapsルートは早期returnで別処理・
    # パターン3のStage2-lite V2は本関数を経由しない）ため、このチェックも
    # 自動的にnew_domain登録のみに限定される。
    i18n_coverage_warnings: list[str] = []
    try:
        _i18n_artifacts = extract_domain_artifacts_from_diffs(diffs, snake)
        i18n_coverage_warnings = _check_i18n_message_coverage(
            _i18n_artifacts.get("solver_code"), _i18n_artifacts.get("ui_converter_code"), snake
        )
    except Exception as e:
        logger.warning(f"[gate2_checks] i18nカバレッジチェックをスキップ（実行エラー）: {e}", exc_info=True)

    # 2026-08-03変更: 「solver.pyが例外でクラッシュした」場合と「モデルは正常に
    # solve()まで完走したがfeasible/infeasibleの期待値と食い違った」場合を、
    # ここで別リストに分けておく。従来は両方とも dynamic_warnings に混ぜて
    # humanize_technical_findings() に一括で渡していたため、LLMによる言い換えの
    # 過程で「実装のバグでクラッシュした」という事実が「実行可能と判定される
    # べきでしたが実行不可と判定されました」のような、あたかも制約矛盾（真の
    # infeasible）であるかのような文面に薄まってしまい、Koshoshiが実機で毎回
    # シナリオを手動実行して初めて真因（例外）に気付く、という手戻りが複数回
    # 発生した（WorkerLoadBalancer・VesselDeckLoader較正実験で確認）。
    # exception由来の指摘だけ別の言い換え・別の接頭辞で扱うことで、LLMの
    # 言い換え精度に依存せず、UI上のラベルだけで「バグの疑い」と「制約矛盾の
    # 疑い」を機械的に区別できるようにする。
    dynamic_warnings: list[str] = []
    dynamic_exception_warnings: list[str] = []
    written_paths: list[str] = []
    _emit_stage("verifying_dynamic_check")
    try:
        written_paths = write_files_for_dynamic_check(diffs, snake)
        if written_paths:
            gate2_report = run_gate2_dynamic_verification(snake)
            dynamic_warnings.extend(gate2_report.get("warnings", []))
            for suffix, entry in gate2_report.get("scenarios", {}).items():
                if entry.get("status") == "exception":
                    tb_text   = entry.get("traceback") or ""
                    last_line = tb_text.strip().splitlines()[-1] if tb_text.strip() else "不明なエラー"
                    dynamic_exception_warnings.append(
                        f"{suffix}シナリオの実行中に例外が発生しました: {last_line}"
                    )
        else:
            logger.info("[gate2_checks] 動的検証: 対象ファイルなし（4DSL非準拠の可能性）— スキップ")
    except Exception as e:
        logger.warning(f"[gate2_checks] 動的検証をスキップ（実行エラー）: {e}", exc_info=True)

    # 2026-07-17再設計: 以前はstatic（repair_notes/sanitizer_warnings）と
    # dynamic（実際にsolve()した結果）を1つのquestionsリストにまとめて同じ重みで
    # 出していた。しかしstaticなfield-check（converter⇔solverのキー突き合わせ）は
    # AST上のキー抽出だけの粗い判定で、solver内部の中間オブジェクト（例: candidate
    # dictの計算済みキー）を「converterが出力すべきキー」と誤認識する既知の弱点が
    # あり、実機で「エージェントが正しく誤検知と説明しても、静的チェックは学習せず
    # 毎回同じ指摘を出し続けて収束しない」事故が起きた。加えて、業務ユーザーに
    # 「これは誤検知か本物のバグか」を判断させるのはそもそも無理がある。
    #
    # そこで、実際にCP Optimizerでsolve()した結果（dynamic）と、ヒアリング必須節の
    # 未実装（required coverage gap）だけを「人間の判断が必須」の指摘として扱い、
    # static findingsとoptionalなcoverage要約は「advisory（参考情報）」として区別
    # して返す。呼び出し元は、advisoryしか残っていない場合は人間に判断を求めずに
    # 進めてよい（ただし記録には残す）。
    _emit_stage("verifying_humanize")
    try:
        static_humanized = humanize_technical_findings(repair_notes + sanitizer_warnings, domain_name)
    except Exception as e:
        logger.warning(f"[gate2_checks] 静的指摘の言い換えをスキップ（実行エラー）: {e}", exc_info=True)
        static_humanized = repair_notes + sanitizer_warnings

    try:
        dynamic_humanized = humanize_technical_findings(dynamic_warnings, domain_name, category="dynamic")
    except Exception as e:
        logger.warning(f"[gate2_checks] 動的検証指摘の言い換えをスキップ（実行エラー）: {e}", exc_info=True)
        dynamic_humanized = dynamic_warnings

    # 2026-08-03追加: exception由来の指摘は「実装バグの疑い」であることが
    # 分かる文面に限定して言い換える（上記dynamic_humanizedと違うプロンプト・
    # 違う接頭辞で扱い、真のinfeasible判定と混同されないようにする）。
    try:
        dynamic_exception_humanized = _humanize_exception_findings(dynamic_exception_warnings, domain_name)
    except Exception as e:
        logger.warning(f"[gate2_checks] 例外指摘の言い換えをスキップ（実行エラー）: {e}", exc_info=True)
        dynamic_exception_humanized = dynamic_exception_warnings

    # 2026-07-19追加: unused_in_solver（converterが出力しているがsolverが一度も
    # 参照しないキー）は、他の静的field-check（missing_in_converterの自己参照
    # 誤検知等）と違って既知の誤検知パターンが無い。MeetingRoomの
    # metadata.instance_name/noteパススルー漏れが、advisory扱いだったために
    # 確認画面から自動的に除外され本物のバグのまま登録されてしまった実機事故を
    # 受け、blocking側（AIエージェントによる自動修正の対象、直せなければ
    # 取りやめ／このまま登録するの判断対象）に分離する。
    try:
        unused_in_solver_humanized = humanize_technical_findings(unused_in_solver_warnings, domain_name, category="unused_in_solver")
    except Exception as e:
        logger.warning(f"[gate2_checks] unused_in_solver指摘の言い換えをスキップ（実行エラー）: {e}", exc_info=True)
        unused_in_solver_humanized = unused_in_solver_warnings

    # 2026-07-22追加: Big-M近似検出（Koshoshi合意により例外的にblocking側へ）。
    # unused_in_solverと同じ理由（既知の誤検知パターンを持たず、過去に実際の
    # 業務問題を起こしたパターンであるため）で、advisory（自動で通す）ではなく
    # blocking（人間の確認必須）に分離する。他の静的field-check系（advisory側）
    # とは異なる扱いである点に注意。
    try:
        big_m_humanized = humanize_technical_findings(big_m_warnings, domain_name, category="big_m")
    except Exception as e:
        logger.warning(f"[gate2_checks] big_m指摘の言い換えをスキップ（実行エラー）: {e}", exc_info=True)
        big_m_humanized = big_m_warnings

    # 2026-08-10追加: optional interval_var のabsent値誤用検出（ProductionLineSequencing
    # 登録時に実機発生。start_of(var, 0) == s のような形がexactly-1 present制約と矛盾し
    # 必ずinfeasibleになる既知の実害パターン）。big_m_warningsと同じ理由でblocking側へ
    # （Koshoshi合意、2026-08-10）。
    try:
        absent_value_humanized = humanize_technical_findings(absent_value_warnings, domain_name, category="absent_value")
    except Exception as e:
        logger.warning(f"[gate2_checks] absent_value指摘の言い換えをスキップ（実行エラー）: {e}", exc_info=True)
        absent_value_humanized = absent_value_warnings

    # 2026-07-26追加: missing_in_dsl_for_solver（DSL⇔solver直接突き合わせ、ネストキー
    # 対応）も、unused_in_solver/big_mと同じ理由（既知の誤検知パターンが薄く、
    # work_limitsのようなパススルー構造で実際にハード制約が無効化された実機不具合の
    # 再発防止が目的）でblocking側に分離する。
    try:
        missing_in_dsl_for_solver_humanized = humanize_technical_findings(
            missing_in_dsl_for_solver_warnings, domain_name, category="missing_in_dsl_for_solver"
        )
    except Exception as e:
        logger.warning(f"[gate2_checks] missing_in_dsl_for_solver指摘の言い換えをスキップ（実行エラー）: {e}", exc_info=True)
        missing_in_dsl_for_solver_humanized = missing_in_dsl_for_solver_warnings

    # 2026-09-06追加: required_gapも他のblockingカテゴリ同様、事実を薄めない
    # 限定的な言い換え（_humanize_required_gap_findings）を通す。
    try:
        required_gap_humanized = _humanize_required_gap_findings(required_gap_warnings, domain_name)
    except Exception as e:
        logger.warning(f"[gate2_checks] required_gap指摘の言い換えをスキップ（実行エラー）: {e}", exc_info=True)
        required_gap_humanized = required_gap_warnings

    # 2026-08-28追記（Koshoshi合意）: 接頭辞を生の技術用語（Big-M近似／optional
    # interval absent値／ネストキー等）からプレーンな日本語ラベルに変更。
    # ただし先頭2つ（実装エラーの疑い／実行検証）は Backend/app.py の
    # _DYNAMIC_STRUCTURAL_PREFIXES と Frontend/.../RegisterModal.tsx の
    # DYNAMIC_STRUCTURAL_PREFIXES が同じ文字列を前方一致で参照しているため、
    # 3箇所を必ず同時に変更すること（「Gate2動的検証は非交渉」の判定に使われる）。
    blocking_questions = (
        [f"（プログラムのエラーで停止・要修正）{w}" for w in dynamic_exception_humanized]
        + [f"（実際に解いてみた結果が想定と違いました）{w}" for w in dynamic_humanized]
        + [f"（ヒアリング内容が未反映）{w}" for w in required_gap_humanized]
        + [f"（入力項目の反映漏れの疑い）{w}" for w in unused_in_solver_humanized]
        + [f"（数値のざっくり近似に関する指摘）{w}" for w in big_m_humanized]
        + [f"（特殊な条件の扱いに矛盾の疑い）{w}" for w in absent_value_humanized]
        + [f"（設定項目の反映漏れの疑い）{w}" for w in missing_in_dsl_for_solver_humanized]
        + [f"（解の自己検証が未実装の疑い）{w}" for w in mip_self_check_warnings]
    )
    advisory_questions = (
        [f"（参考情報）{w}" for w in static_humanized]
        + [f"（ヒアリング内容が未反映・任意項目）{w}" for w in optional_gap_summary]
        + tech_conformance_warnings
        + i18n_coverage_warnings
    )
    questions = blocking_questions + advisory_questions

    logger.info(
        f"[gate2_checks] {domain_name}: repair_notes={len(repair_notes)}件, "
        f"sanitizer_warnings={len(sanitizer_warnings)}件, unused_in_solver={len(unused_in_solver_warnings)}件, "
        f"big_m_warnings={len(big_m_warnings)}件, "
        f"absent_value_warnings={len(absent_value_warnings)}件, "
        f"missing_in_dsl_for_solver={len(missing_in_dsl_for_solver_warnings)}件, "
        f"required_gap={len(required_gap_warnings)}件, "
        f"optional_gap_summary={len(optional_gap_summary)}件, dynamic_warnings={len(dynamic_warnings)}件, "
        f"dynamic_exception_warnings={len(dynamic_exception_warnings)}件, "
        f"tech_conformance={len(tech_conformance_warnings)}件, "
        f"i18n_coverage={len(i18n_coverage_warnings)}件, "
        f"mip_self_check={len(mip_self_check_warnings)}件 "
        f"→ blocking={len(blocking_questions)}件, advisory={len(advisory_questions)}件"
    )
    return {
        "questions": questions,
        "blocking_questions": blocking_questions,
        "advisory_questions": advisory_questions,
        "written_paths": written_paths,
        "coverage": coverage,
    }


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


# ─────────────────────────────────────────────────────────────
# 同名チェック
# ─────────────────────────────────────────────────────────────

def check_domain_exists(domain_name: str) -> dict:
    snake = _to_snake(domain_name)
    conflicts = []
    if _resolve_path(f"Backend/solvers/{snake}_solver.py").exists():
        conflicts.append(f"Backend/solvers/{snake}_solver.py")
    if _resolve_path(f"Frontend/src/app/studio/views/{domain_name}View.tsx").exists():
        conflicts.append(f"Frontend/src/app/studio/views/{domain_name}View.tsx")
    for suffix in ("baseline", "tight", "infeasible"):
        p = _SCENARIOS_DIR / f"{snake}_{suffix}.json"
        if p.exists():
            conflicts.append(f"Backend/dsl_repository/scenarios/{snake}_{suffix}.json")
    return {"exists": len(conflicts) > 0, "conflicts": conflicts,
            "domain_name": domain_name, "snake_name": snake}


# ─────────────────────────────────────────────────────────────
# Stage 1a: 分類
# ─────────────────────────────────────────────────────────────

def _load_primary_problems() -> str:
    try:
        return _PRIMARY_PROBLEMS_PATH.read_text(encoding="utf-8")
    except Exception:
        return ""


_STAGE1A_SYSTEM = """\
あなたは業務要件を最適化問題に分類する専門家です。
ヒアリング内容を読み取り、以下のルールで分類してください。

【分類ルール（優先順位順）】
0. ユーザーメッセージ内に「追加候補ドメイン一覧」が提示されている場合、まずそれを確認し、
   ヒアリング内容と実質的に同一の業務を扱っているドメインがあれば、それを最優先で
   base_domain として選ぶこと（match_type: "existing_domain"）。これは下記の固定4ドメインの
   判定基準より優先する。固定4ドメインへの汎用的な当てはめを理由に、より具体的に一致する
   追加候補ドメインを見送ってはならない。
1. 上記に該当せず、既存ドメイン（固定4ドメイン）のどれかに「そのまま当てはまる」場合
   → match_type: "existing_domain"
2. 下記「基底問題との対応」に**既存ドメインへの転用先が明記されている**CSPLib基底問題
   （2026-08-06時点ではRCPSP→LineChangeoverScheduler拡張候補の1件のみ。以前はCVRP側にも
   prob082→TruckDispatcherの記載があったが、DARP系の構造上拡張が成立しないと判明し撤回した）
   のどれかで解ける場合 → match_type: "base_problem"
   （2026-07-25追記: 「基底問題参考」には転用先が明記されていない基底問題も多数含まれるが、
   それらは以下3のnew_domainとして扱うこと。基底問題参考に構造的に一致すること自体は
   base_problem判定の条件ではない。転用先が明記されているかどうかで判定する。）
3. 上記1・2のどちらにも当てはまらない場合 → match_type: "new_domain"
   （「基底問題参考」に構造的に一致する基底問題がある場合は、technical_directivesに
   その基底問題の推奨解法（CP/MIP）を参考情報として記載すること。下記technical_directivesの
   注意書き参照。ただしmatch_typeはnew_domainのままとし、base_problemにはしないこと。）

既存ドメインの判定基準（固定4ドメイン。ルール0の追加候補ドメインが優先）:
  - 積載量制限付き車両で顧客を巡回配送 → TruckDispatcher
    （旧称CapacitatedVehicleRoutingProblemはCP Optimizer非準拠の旧実装であり、
     新規ヒアリングでは絶対に選ばないこと。必ずTruckDispatcherを選ぶこと。）
  - スタッフ（看護師等）を勤務日・シフト枠に割り当てるシフト編成 → NurseShiftWeeklyCap
  - タスク間の前後関係制約と資源容量制約のもとでプロジェクト全体のメイクスパンを最小化する
    汎用的なプロジェクトスケジューリング（機械の柔軟割当は伴わない。例: 製造ライン切替、
    工程計画、設備メンテナンス手順等）→ LineChangeoverScheduler
    （これがRCPSP（prob061）の汎用実装。下記YardPlanningの注意書きに該当する場合のみ
    YardPlanningを優先すること。）
  - 【港湾コンテナヤード】でのクレーン作業・コンテナ搬出入・接岸船スケジューリングに
    文字通り一致する場合のみ → YardPlanning
    （注意: YardPlanningは港湾コンテナ専用のUI・オペレーション種別（LOAD/DISCHARGE/PLACE等）を
    ハードコードしたレガシー実装であり、他業種への転用はできない。「機械やラインにタスクを
    割り当ててスケジューリングする」という構造的な類似性だけでYardPlanningを選んではならない。
    印刷・製造・データセンター等、コンテナヤード以外の業務は、たとえ構造が似ていても
    YardPlanningではなく LineChangeoverScheduler（または一致しなければ new_domain）として
    扱うこと。）

基底問題との対応:
  - prob086 (CVRP) → TruckDispatcher（実装済み。CapacitatedVehicleRoutingProblemは選ばないこと）
  - prob061 (RCPSP) → LineChangeoverScheduler（2026-07-15に汎用実装として登録済み。上記
    YardPlanningの注意書きに該当する場合のみYardPlanningを優先すること）。
  - prob090 (Bin Packing), prob133 (Knapsack) に対応する既存の再利用可能な実装は
    現時点で存在しない（2026-07-11に整理済み）。これらに該当する場合は base_problem に
    分類せず、new_domain として扱うこと。
  - prob034 (Warehouse Location) は、StoreSite（2026-07-20登録）が構造的に対応する
    実装として存在する。ただし現時点ではStoreSite一件のみの検証であり、既存実装への
    自動流用（base_problem/existing_domain扱いでのStage2スキップ）はまだ有効化していない。
    prob034に該当するヒアリングが来た場合も、上記YardPlanningの注意書きと同じ理由
    （構造的な類似性だけで実装を流用すると、スキーマの前提が微妙に異なる案件に
    合わない実装が適用されるリスクがある）により、当面は base_problem に分類せず
    new_domain として扱うこと（Stage2から都度生成する）。

JSON のみを返してください。前置き・説明不要。

{
  "match_type": "existing_domain | base_problem | new_domain",
  "base_domain": "TruckDispatcher | NurseShiftWeeklyCap | YardPlanning | LineChangeoverScheduler | (ユーザーメッセージの「追加候補ドメイン一覧」に記載された正式名称) | null",
  "reason": "分類理由1〜2文。プログラミングやCP最適化の知識がない業務担当者にも分かる言葉で書くこと。『Family A』『Family C』のような内部分類ラベルや、メイクスパン・資源制約スケジューリング等のCP/OR専門用語は一切使わないこと。既存の実装で対応できない場合は、業務的にどのような特徴があるため専用に作る必要があるかを説明すること。",
  "confidence": 0.0〜1.0,
  "scenario_name_jp": "日本語シナリオ名",
  "scenario_description_jp": "シナリオ説明2〜3行",
  "technical_directives": "",
  "missing_info": [],
  "is_dsl4_candidate": true
}

注意: is_dsl4_candidate は「新ドメインの場合に4DSL準拠方式（converter + ui_converter + solver）で実装すべきか」の判断。
新規ドメインは原則 true とする（4DSLが現行の標準アーキテクチャ）。

注意: technical_directives は、ヒアリング内容の中に「使用すべきライブラリ／ソルバー技術
（例: docplex.mp、CPLEX MIP）」「具体的な数式・変数定義・制約の線形化方法」「重み係数の
目安値」など、Stage2（コード生成）が実装時に直接必要とする技術的指示が含まれている場合に、
**要約・言い換えをせず該当箇所を原文のまま抜粋してここに転記する**フィールド。
scenario_description_jp（2〜3行の業務説明）とは目的が異なり、字数制限は設けない。

（2026-07-25追記、2026-07-27改訂）ヒアリング内にこのような明示的な技術指示が**ない**場合、
「基底問題参考」を参照して以下のように扱う（technical_directivesフィールドへの記載対象は
match_type="new_domain" の場合のみ。existing_domain/base_problem は既存実装の技術を
そのまま使うためStage2向けの技術指示自体が不要）：
  - 構造的に類似する問題が見つかり、かつ推奨解法がCPまたはMIPのどちらか一方に定まっている
    場合: technical_directivesに断定的な技術指示として記載する（「構造推定」という留保は
    付けない。この推奨解法自体がCSPLibという参考情報源に基づく推定ではあるが、Stage2に
    対しては確定指示として扱わせる）。
    ただし、この断定的記載を行ってよいのは、**その基底問題への構造的一致自体に高い確信が
    ある場合に限る**。類似はしているが完全な一致ではない、業務の詳細（件数・分布・制約の
    細部など）次第では別の解法が適切になり得る、といった一致度自体への留保が必要な場合は、
    次の項目（両方/該当なし）と同じ扱いにすること。technical_directivesの中でCPとMIPの
    両方に言及したり、「ただし〜の場合はMIPも検討の余地がある」のように断定と留保を
    混在させた書き方をしてはならない（断定するなら一方のみを迷いなく記載し、確信が
    持てないなら何も記載せずmissing_infoに回す、のどちらか一方のみを選ぶこと）。
    例:「基底問題参考のprob034 Warehouse Locationに構造的に一致するため、
    docplex.mp（MIP）を使用すること。」
  - 構造的に類似する問題が見つかったが推奨解法が「両方」の場合、構造的に類似する
    問題が見つからない場合、または類似する問題は見つかったがその一致自体に高い確信が
    持てない場合（前述の通り、業務詳細次第で結論が変わりうる・複数の基底問題が同程度に
    候補になり得る等）: technical_directivesには何も記載せず、代わりにmissing_infoに
    「CP(制約プログラミング)とMIP(混合整数計画)のどちらで実装すべきか、ヒアリング内容・
    基底問題参考のどちらからも断定できません。ご希望があれば教えてください
    （指定が無ければ実装時にAIが判断します）。」という趣旨の質問を1件追加すること。
ヒアリング内の明示的な指示（原文転記）がある場合はそれを最優先し、上記のいずれも行わない。

（2026-07-30追記、2026-07-31分離）match_type="existing_domain"の場合、
technical_directivesへの記載は不要（Stage2を通らずbase_domainの実装をそのまま
拡張するため）。なお、選んだbase_domainの技術的前提（採用エンジン）が今回の
ヒアリング内容に本当に合っているかの「軸(a)適合チェック」は、この分類プロンプトでは
行わない。分類が完了した後に別の専用LLM呼び出し（check_axis_a_fit()、Stage1a.4）で
行うため、ここでは分類（match_type/base_domainの判定）にのみ集中すること。

注意: missing_info は、人間に確認を依頼すべき「曖昧さ・抱えているリスク」を見つけたときに
**必ず具体的な質問を記載する**フィールドであり、単なる形式上の空配列ではない。
以下のようなケースでは必ず1件以上記載すること：
  - 目的関数のうち、一部のアイテム（例: urgentフラグ付き）にのみ適用するべきか、
    全アイテムに適用すべきかがヒアリング文から断定できない場合
  - 「割り当て不可能なアイテム」が発生したときの振る舞い（スキップ/ペナルティ/エラー）が
    ヒアリング文に明記されていない場合
  - 制約の優先順位（例: 予算と納期が衝突する場合どちらを優先するか）が不明確な場合
  - 既存ドメインとの類似度が高いが完全一致しないため、従来式の流用か新規実装か判断が分かれる場合
  - match_type が new_domain で、CP/MIPどちらの技術を使うべきかヒアリング内容からも
    基底問題参考からも断定できない場合（推奨解法が「両方」、または構造的に類似する問題が
    見つからない場合。上記technical_directivesの注意を参照）
これらがなければ空配列のままでよい。無理に質問をひねり出さないこと。
（match_type="existing_domain"の軸(a)適合チェックはこのプロンプトの対象外。
上記2026-07-31分離の追記を参照）

（2026-08-29追記、Koshoshi合意）missing_infoの各項目は、質問文だけを書くのではなく、
必ず以下の3ブロック構成をこの順で1つの文字列にまとめて（改行を含めて）記載すること。

  {質問文（従来通り）}

  【今回の結論】{この質問に人間が何も回答しなかった場合、実装が実際にどう判断される
  かを1文で。「わかりません」「未定」のような回答放棄は禁止。必ず具体的な既定動作を
  書くこと（例:「AIがCPで実装します」「対象は全アイテムとして扱います」「早着・遅着
  ともに0.5単位未満の端数は切り捨てます」等）}
  【理由】{その既定動作をなぜ選んだかの根拠を1〜2文で}

（2026-08-30追記、Koshoshi実機フィードバック）【今回の結論】の文には、理由・根拠の
節（「〜のため」「〜なので」「〜だから」等）を絶対に含めないこと。理由は必ず
【理由】ブロックのみに書き、【今回の結論】は「何が起きるか」という結論そのものだけを
1文で言い切ること。この2つを分けて書かせているのに【今回の結論】の文末に
理由節を続けてしまうと、直後の【理由】ブロックと内容が重複し、人間が読んだときに
同じ理由を2回読まされる不自然な文章になる（実機で確認された不具合）。
  - NG:「割り当てられない出店希望者が発生しても『解なし』にせず、できるだけ多くの
    出店希望者を割り当てる方針で実装します（ヒアリング4-1節の補足で『解なし』に
    しない方針が明記されているため）。」← 文末の（〜ため）が禁止
  - OK:「割り当てられない出店希望者が発生しても『解なし』にせず、できるだけ多くの
    出店希望者を割り当てる方針で実装します。」← 理由節なしで言い切る

目的: 人間の判断を必須とする方針は維持しつつ、「聞かれても何を基準に答えればいいか
わからない」という状態を避け、画面上で質問と同時に「今回どう扱われるか」と
「なぜそう扱うか」が一目でわかるようにするため。この3ブロック構成は
missing_infoの全項目（既存ドメインとの類似度・優先順位・CP/MIP等、種類を問わず）に
適用すること。
"""


# 2026-07-27: 当初\bcp\b/\bmip\bで実装したが、Pythonのreはデフォルトで日本語の
# 平仮名・漢字もUnicode word文字（\w）として扱うため、「ではMIP」のように直前が
# 日本語の場合に\bが働かず不一致になる実バグが発覚した（実機再現テストで確認）。
# そのため\bには頼らず、ASCII文字（A-Za-z）による直前直後のみを境界条件とする
# 独自パターンに変更する。「CPLEX」のような単語内の部分文字列（"cp"の直後が
# ASCII文字"l"）は引き続き除外される。
_CP_TOKEN_RE = re.compile(r'(?<![A-Za-z])cp(?![A-Za-z])', re.IGNORECASE)
_MIP_TOKEN_RE = re.compile(r'(?<![A-Za-z])mip(?![A-Za-z])', re.IGNORECASE)


def _resolve_hedged_technical_directive(result: dict) -> dict:
    """2026-07-27追加。

    _STAGE1A_SYSTEMのプロンプト指示（構造的一致に確信が持てない場合は
    technical_directivesに何も書かずmissing_infoへ回す）は、Koshoshiの実機検証で
    2回とも守られなかった（LLMがCP/MIP両方に言及する「ただし〜も検討の余地が
    あります」式のヘッジ表現を出力し続けた。例: HelpdeskTicketRoutingヒアリングで
    prob062に構造類似としてCPを推奨しつつ、文中でMIPにも言及）。

    プロンプト文言だけに再度頼っても同じ再現が続く可能性が高いため、機械的に
    検出して強制的にmissing_info行きにする後処理をここに実装する。
    match_type=="new_domain"の場合のみ対象（他のmatch_typeはこの判断が不要）。
    technical_directivesの中にCP用語（\bcp\b。word boundaryで判定するため
    "CPLEX"の部分文字列とは区別される）とMIP用語（\bmip\b）が両方とも単語として
    出現していたら、断定ではなくヘッジ（または「両方」の言い換え）とみなし、
    technical_directivesを空にし、missing_infoにCP/MIP確認質問を追加する
    （既にCP/MIP確認相当の質問が入っていれば重複追加しない）。
    """
    if result.get("match_type") != "new_domain":
        return result
    td = result.get("technical_directives") or ""
    if not td:
        return result
    if _CP_TOKEN_RE.search(td) and _MIP_TOKEN_RE.search(td):
        result["technical_directives"] = ""
        missing = list(result.get("missing_info") or [])
        combined = " ".join(missing).lower()
        if not ("cp" in combined and "mip" in combined):
            missing.append(
                "CP(制約プログラミング)とMIP(混合整数計画)のどちらで実装すべきか、"
                "ヒアリング内容・基底問題参考のどちらからも断定できません。"
                "ご希望があれば教えてください。\n\n"
                "【今回の結論】指定が無いため、AIが実装時にCP/MIPいずれかを判断します。\n"
                "【理由】構造的に類似する基底問題は見つかりましたが、一致度自体への確信が"
                "十分ではなく、CP/MIPどちらか一方に断定できないためです。"
            )
        result["missing_info"] = missing
        logger.info(
            "[classify] technical_directivesがCP/MIP両方に言及していたため、"
            "missing_info行きに強制変換しました。"
        )
    return result


def classify_problem(domain_name: str, hearing_texts: list) -> dict:
    from llm.llm_client import call_llm, extract_json, fast_model
    primary_problems  = _load_primary_problems()
    hearing_combined  = "\n\n---\n\n".join(hearing_texts)
    dynamic_candidates = _get_dynamic_stage1a_candidates()

    if dynamic_candidates:
        candidates_block = "\n".join(
            f'- {c["problem_class"]}: {c["description"] or "（説明未登録）"}'
            for c in dynamic_candidates
        )
        dynamic_section = f"""

## 追加候補ドメイン一覧（後から自動生成され、DBに登録済みのドメイン）
以下は固定4ドメインの判定基準には含まれていないが、既に実装済みで
再利用可能なドメインである。ヒアリング内容がこれらのいずれかと
実質的に同一の業務を扱っている場合は、固定4ドメインへの汎用的な
当てはめより優先して、
該当するドメインの正式名称（下記の通り）を base_domain として選ぶこと。
{candidates_block}
"""
    else:
        dynamic_section = ""

    user_prompt = (f"## 業務名\n{domain_name}\n\n## ヒアリング内容\n{hearing_combined}\n\n"
                   f"## 基底問題参考\n{primary_problems}\n"
                   f"{dynamic_section}\n"
                   f"JSON で分類してください。")
    messages = [{"role": "system", "content": _STAGE1A_SYSTEM}, {"role": "user", "content": user_prompt}]
    # temperature=0: Stage1aは分類タスク（new_domain / existing_domain / base_problemの
    # 3値判定）であり、創造性は不要。旧デフォルト(temperature=1.0)では同一ヒアリング内容
    # でも実行のたびに判定が揺れる不具合が確認されている（施設配置系ヒアリングが
    # new_domain / GhostKitchen拡張のどちらにも分類される、等）。決定的な分類にすることで
    # この揺れを抑える。
    # 2026-07-20: max_tokensを1024→2048に引き上げ。technical_directives追加
    # （字数制限なしで原文転記）により、StoreSite実機登録で実際にJSON出力が
    # 途中で切れ、extract_jsonのキー単位部分修復で救われる事象が発生した
    # （docs/ENGINEERING_LOG.md 2026-07-20参照）。運任せの修復に頼らないよう
    # 余裕を持たせる。
    # 2026-08-05変更: 一時的にdefault_model()（Sonnet）へ変更したが、効果の良し悪しが
    # 未確定（PatientTransportPlannerの最小ベース検証でTruckDispatcher拡張候補を自力で
    # 検出する等、挙動は変わったが望ましい変化か未評価）だったため、Koshoshi指示により
    # fast_model()（Haiku）へ戻した。詳細はdocs/ENGINEERING_LOG.md 2026-08-05追記参照。
    raw    = call_llm(messages, model=fast_model(), max_tokens=2048, temperature=0)
    result = extract_json(raw)
    result = _resolve_hedged_technical_directive(result)
    logger.info(f"[classify] match_type={result.get('match_type')}, base_domain={result.get('base_domain')}")
    return result


# ─────────────────────────────────────────────────────────────
# Stage 1a.4: 軸(a)適合チェック（専用LLM呼び出し、2026-07-31分離）
#
# 背景: 2026-07-30に導入した軸(a)適合チェックは、当初classify_problem()の
# 分類プロンプト（_STAGE1A_SYSTEM）に同梱していた。Koshoshiの実環境での
# 実LLM検証（test_classify_problem_axis_a_e2e.py、N=10）で、罠ケースごとの
# 検知率が8/10（trap_bus_driver_as_shift）〜2/10（trap_winner_determination_as_
# meeting_room）と大きくばらつくことが判明した
# （DESIGN_2026-07-30_domain_registration_classification_taxonomy.md 5節）。
# 分類（3値判定）と軸(a)適合チェック（技術的前提の突合）は本来別の判断タスクであり、
# 1回のプロンプト・1回のLLM呼び出しに同梱すると分類本体に注意力が割かれ、
# 軸(a)チェックが疎かになる可能性がある。そのため、classify_problem()が
# match_type="existing_domain"と判定した場合にのみ、独立した専用LLM呼び出しで
# 軸(a)適合チェックを行う（2026-07-31、Koshoshi承認済みのアーキテクチャ変更。
# 効果検証はKoshoshiの実環境でtest_classify_problem_axis_a_e2e.py再実行により行う）。
#
# 判断根拠の分離は従来通り維持する:
#   - 事実: get_domain_engine(base_domain) — 選ばれたbase_domainのsolver.py
#     実コードのimport文から機械的に判定。手作業テーブルは持たない。
#   - 判断根拠: docs/CSPLIB_REFERENCE.md（CSPLib由来の外部参照。旧primary_problems.md、
#     2026-08-06統合）。候補ドメイン自身のコードを判断根拠にしない。
# ─────────────────────────────────────────────────────────────

_AXIS_A_FIT_SYSTEM = """\
あなたは、既存の最適化ドメイン実装が採用している技術的前提（ソルバーエンジン：
CP Optimizer か MIP か）が、新しい業務ヒアリングの内容に本当に合っているかを
検証する専門家です。

背景: 「拡張」（既存ドメインへの機能追加）は、選ばれたbase_domainが採用している
エンジンをそのまま引き継ぐ。もしbase_domainの実際のエンジンが、この業務に本来
推奨される解法と食い違っていれば、無理にそのエンジンで実装を進めるべきではない
（過去にStoreSiteという既存ドメインをCP Optimizerで4回生成しようとして失敗した
事例は、この確認の欠如が一因だった）。

あなたが行うのはこの1点のみです。分類（どのドメインに分類するか）は既に完了して
おり、あなたはその結果を疑う必要はありません。他の観点（目的関数の妥当性、
missing_infoの他の論点等）も一切扱わないでください。

手順:
1. 「基底問題参考」（CSPLib由来の外部参照）を参照し、ヒアリング内容が構造的に
   高い確信度で一致する問題があるかを確認する。曖昧な類似だけで無理に一致
   させないこと。
2. 一致する問題が見つかった場合、その問題の推奨解法（CP/MIP/両方）を確認する。
3. 一致する問題が見つかり、かつ推奨解法がCPまたはMIPのどちらか一方に定まって
   おり、それが下記「選ばれたbase_domainの実際の採用エンジン（事実）」と
   **食い違う**場合のみ、mismatch: true とする。
4. 以下のいずれかに該当する場合は mismatch: false とする（無理に疑いを作らない。
   過検知よりも明確な技術的矛盾の見逃し防止を優先するが、根拠のない疑いは
   ノイズになるため出さない）:
   - 一致する問題が見つからない
   - 推奨解法が「両方」
   - 一致度自体に高い確信が持てない（構造は似ているが業務の細部次第で
     結論が変わりうる、複数の基底問題が同程度に候補になり得る等）

JSONのみを返してください。前置き・説明不要。

{
  "csplib_match": "該当するCSPLib問題名・番号（例: prob063 Winner Determination）。無ければnull",
  "recommended_engine": "CP | MIP | 両方 | null",
  "mismatch": true または false,
  "concern": "mismatchがtrueの場合のみ、base_domain名・実際の採用エンジン・推奨解法・該当基底問題名・(基底問題参考に代替候補名の記載があれば)代替候補名を具体的に含めた、人間向けの確認質問1文。「このまま拡張してよいか、別の実装を検討すべきか」を確認する趣旨にすること。falseの場合は空文字列。"
}
"""


def check_axis_a_fit(domain_name: str, hearing_texts: list, base_domain: str) -> dict:
    """
    Stage1a.4: classify_problem()がmatch_type="existing_domain"と判定した直後に
    のみ呼ばれる、軸(a)（採用エンジンの技術的前提）適合チェック専用のLLM呼び出し。
    2026-07-31、Koshoshi承認によりclassify_problem()の分類プロンプトから分離した
    （分離の経緯は上記コメント・DESIGN文書2026-07-31追記部分を参照）。

    戻り値: {"mismatch": bool, "concern": str, "csplib_match": str|None,
             "recommended_engine": str|None}
    concern は mismatch=True の場合のみ非空文字列で、呼び出し元
    (interpret_hearing()) が classification["missing_info"] に合流させる。
    """
    from llm.llm_client import call_llm, extract_json, fast_model

    actual_engine = get_domain_engine(base_domain)
    engine_label = {"cp": "CP Optimizer(docplex.cp)", "mip": "MIP(docplex.mp)",
                    "both": "CP/MIP混在", "unknown": "不明（判定不能）"}.get(actual_engine, "不明（判定不能）")
    primary_problems  = _load_primary_problems()
    hearing_combined  = "\n\n---\n\n".join(hearing_texts)

    user_prompt = (
        f"## 業務名\n{domain_name}\n\n"
        f"## ヒアリング内容\n{hearing_combined}\n\n"
        f"## 基底問題参考\n{primary_problems}\n\n"
        f"## 選ばれたbase_domainの実際の採用エンジン（事実、コードから機械的に抽出。"
        f"判断根拠ではなく事実確認用）\n"
        f"- {base_domain}: {engine_label}\n\n"
        f"上記を踏まえ、JSON で回答してください。"
    )
    messages = [{"role": "system", "content": _AXIS_A_FIT_SYSTEM}, {"role": "user", "content": user_prompt}]
    # temperature=0: classify_problem()と同じ理由。判定タスクであり創造性は不要。
    raw    = call_llm(messages, model=fast_model(), max_tokens=1024, temperature=0)
    result = extract_json(raw)

    mismatch = bool(result.get("mismatch"))
    concern  = (result.get("concern") or "").strip() if mismatch else ""
    logger.info(
        f"[axis_a_fit] {domain_name} (base_domain={base_domain}, actual_engine={actual_engine}): "
        f"mismatch={mismatch}"
        + (f", csplib_match={result.get('csplib_match')!r}" if mismatch else "")
    )
    return {
        "mismatch": mismatch,
        "concern": concern,
        "csplib_match": result.get("csplib_match"),
        "recommended_engine": result.get("recommended_engine"),
    }


# ───────────────────────────────────────────────────────
# Stage 1a.5: 拡張差分検出
#
# 背景:
#   Stage1aはmatch_typeを「existing_domain / base_problem / new_domain」の
#   3値だけで判定し、existing_domain/base_problemと判定されると
#   既存ドメインのコードを一切見ないままシナリオ生成の呼び出しだけで進む。
#   このため、ヒアリング内容の一部が既存実装では対応できない（無視されてしまう）
#   ケースが検出されない。実際にCVRP新規案件で、容積(m3)とソフトタイムウィンドウという
#   2つの未対応項目を、人間が対話の中で手作業で発見したことがこの問題を実証した。
#
#   このブロックは、classify_problem()の後、generate_scenarios_from_schema()の前に
#   実行し、既存ドメインの実際のソースコード（converter/solver）をLLMに直接
#   渡して、ヒアリング内容と照合させ、未対応の要件（extension_gaps）を検出する。
#   検出された場合はmissing_infoと合流して既存のneeds_confirmationゲートに乗せる
#   （app.pyの_run_domain_jobで実装）。
# ────────────────────────────────────────────────────────────

_STAGE1A5_SYSTEM = """\
あなたは、既存の最適化ソルバー実装と、既に登録済みの拡張機能一覧、そして
新しい業務のヒアリング内容を比較し、要件がどこでどのように扱われるべきかを判定する専門家です。

3つの分類があります:
1. 既存の実装（コード）だけで完全に対応できる要件 → 何もリストに含めない
2. 既存の実装では直接対応できないが、「既存ドメインに既に登録済みのExtension一覧」に
   対応する項目がある要件 → reusable_extensions に列挙する（この場合、コード変更は不要で
   シナリオデータへの反映のみでよいと判断される）
3. 既存の実装にも既存Extensionにも対応するものがなく、新規コードが必要な要件 →
   extension_gaps に列挙する

判定基準:
- 迷った場合は2（reusable）と判断せず、3（gap）として報告すること
  （実際には対応できないのに「既存Extensionで足りる」と誤判定すると、
   後で「動くはずなのに実際には無視される」不具合を招くため）
- reusable_extensions の name は、与えられた既存Extension一覧の名前と
  完全一致させること（既存Extension一覧が空の場合、reusable_extensionsは常に空配列）

JSONのみを返してください。前置き・説明不要。

{
  "reusable_extensions": [
    {"name": "既存Extension一覧にある名前と完全一致させること", "reason": "なぜこの既存Extensionで足りると判断したか"}
  ],
  "extension_gaps": [
    {
      "name": "snake_case の短い識別子（例: dual_capacity, soft_time_window）",
      "description": "何が既存実装で扱えないか、1～2文",
      "hearing_evidence": "ヒアリング文中の該当箇所を短く引用または要約",
      "affected_fields": ["関係するDSLフィールド名（推定でよい）"],
      "rationale": "なぜ既存実装・既存Extensionでは対応できないと判断したか"
    }
  ]
}
"""


def detect_extension_gaps(domain_name: str, hearing_texts: list, base_domain: str) -> dict:
    """
    Stage1a.5: 既存ドメイン（base_domain）の実際のソースコード、
    すでにDBに登録済みのExtension一覧、そしてヒアリング内容をLLMに直接
    比較し、3分岐（対応不要 / 既存Extension流用 / 新規コード必要）で判定する。
    ソースファイルが見つからない場合は安全側に倒れて両方空のまま返す（検出をスキップしても
    従来通りのexisting_domain処理に進むだけで、パイプレイン全体を止めない）。
    """
    from llm.llm_client import call_llm, extract_json, default_model

    _ensure_domain_registry_reconciled()
    src = _DOMAIN_SOURCE_FILES.get(base_domain, {})
    source_sections = []
    for role, path in src.items():
        if not path:
            continue
        try:
            code = Path(path).read_text(encoding="utf-8")
            source_sections.append(f"### {role}: {Path(path).name}\n```python\n{code}\n```")
        except Exception as e:
            logger.warning(f"[extension_gaps] ソース読み込み失敗: {path} — {e}")

    if not source_sections:
        logger.warning(f"[extension_gaps] {base_domain} のソースファイルが見つからず検出をスキップ")
        return {"reusable_extensions": [], "extension_gaps": []}

    # 既存に登録済みのExtension一覧（同一 base_domain カテガリのもの）を取得する。
    # パターン3で実装された拡張はcategory=新ドメイン名で登録されるため、
    # ここには現れない（未変更の base_domain 本体に対応する Extension のみが対象）。
    existing_extensions_desc = "（なし）"
    try:
        from dsl_repository.repository import DslRepository
        repo = DslRepository()
        # 2026-07-27修正: 以前はcategory=base_domainで検索していたが、category列は
        # 本来「種類ラベル」（constraint/resource/ui等）専用であり、ドメイン名との
        # 完全一致検索とは意味が噛み合っていなかった（physical_space等、YardPlanningに
        # 実際に使われている5件が一切ヒットしない不整合があった）。適用対象ドメインの
        # 判定は新設のapplicable_domains列で行う（Koshoshiとの会話で発覚・修正）。
        existing = repo.list_extensions(applicable_domain=base_domain)
        if existing:
            existing_extensions_desc = "\n".join(
                f"- {e['name']}: {e.get('description','')}" for e in existing
            )
    except Exception as e:
        logger.warning(f"[extension_gaps] 既存Extension一覧取得失敗: {e}")

    hearing_combined = "\n\n---\n\n".join(hearing_texts)
    user_prompt = f"""## 業務名
{domain_name}

## 既存ドメイン
{base_domain}

## 既存実装のソースコード
{chr(10).join(source_sections)}

## 既存ドメインに既に登録済みのExtension一覧
{existing_extensions_desc}

## ヒアリング内容
{hearing_combined}

上記の既存実装・既存Extensionで、ヒアリング内容の要件がどこまでカバーされているか判定し、
JSON で回答してください。
"""
    messages = [{"role": "system", "content": _STAGE1A5_SYSTEM}, {"role": "user", "content": user_prompt}]
    raw    = call_llm(messages, model=default_model(), max_tokens=3000)
    result = extract_json(raw)
    reusable = result.get("reusable_extensions", [])
    gaps     = result.get("extension_gaps", [])
    logger.info(f"[extension_gaps] {domain_name} ({base_domain}): reusable={len(reusable)}件, gaps={len(gaps)}件")
    return {"reusable_extensions": reusable, "extension_gaps": gaps}


# ─────────────────────────────────────────────────────────────
# Stage 1a.5a: structural_requirements 抽出（2026-08-05追加）
#
# 背景: PatientTransportPlanner実機登録（2026-08-05）で、Stage2がヒアリングシートに
# 明記されていた具体的な業務構造（往復ペア必達・車両の乗車定員相乗り・対応可能区分・
# 付き添い者・lexicographic目的等）をほぼ無視し、repomix参考資料中の構造的に近い
# 既存ドメイン（MeetingRoom）の型と、学習データ上の一般的な「病院内患者搬送」パターンに
# 引っ張られて誤生成する事象が発生した（詳細: ENGINEERING_LOG.md 2026-08-05追記8・9）。
#
# 過去にformulation_directiveの自動LLM抽出（ヒアリング内容からCP/MIP等の技術的定式化を
# 都度推測させる）は、実案件で技術情報が乏しい場合に誤った定式化を混入させるリスクを
# 理由に見送った経緯がある（lookup_family_reference()のコメント参照）。この関数はそれとは
# 性質が異なる: 外部のCSPLib知識やCP/MIP技術選定には一切踏み込まず、**ヒアリングテキスト
# 自体に既に明記されている業務構造を、抜け漏れなく構造化して再掲するだけ**の要約作業。
# 新規の技術的判断を追加しないため、formulation_directiveで懸念されたリスクは生じない。
# CSPLib一致の有無に関わらず全ての新規ドメイン生成で機能するのが、family_reference/
# formulation_directive（CSPLib一致時のみ機能）との違い。
#
# Stage2プロンプトでは「必須順守」セクションとして提示し、添付のrepomix参考資料（他ドメイン
# のソースコード一式）よりも優先させる。これにより、後述の「他ドメインを安易に模倣するな」
# という抑制ルールを追加しても、Stage2が拠り所を失わないようにする（抑制と参考情報のバランス
# については、Koshoshiとの2026-08-05の議論を参照）。
# ─────────────────────────────────────────────────────────────

_STRUCTURAL_REQUIREMENTS_SYSTEM = """\
あなたはヒアリングシートの構造化担当です。以下のヒアリングテキストだけを読み、
そこに書かれている業務構造を、抜け漏れなく・改変せずに構造化して出力してください。

厳守事項:
- ヒアリングテキストに書かれていないことを推測・補完・一般化してはいけません。
  CSPLibの知識、CP/MIPの技術選定、業界の一般的なベストプラクティス等、ヒアリング
  テキスト外の知識は一切使わないでください。あなたの役割は「言い換え・再構造化」であり
  「技術的判断」ではありません。
- 特に見落としやすい構造（該当する場合は必ず拾うこと）:
  - 割り当てられる側の1件が、複数の訪問・工程・段階を要求する場合（例: 往路と復路、
    積込と荷卸、複数回の訪問）。それらの間に順序制約や「両方完了して初めて達成」
    のような完了条件があるか。
  - 資源（人・設備・車両等）が同時に複数の仕事に使える場合の上限（相乗り・複数担当）。
  - 対応可否・適性に関する区分（特定の資源しか特定の対象を扱えない、等）。
  - 目的が複数ある場合の優先方法（重み付けか、段階的優先（lexicographic）か）。
  - 「対応しきれない場合の扱い」（許容するのか、解なしとするのか）。
- 出力は日本語。ヒアリングに無い項目は空リスト／nullにしてください（無理に埋めない）。

JSON形式で出力してください:
{
  "entities": [
    {"name": "（エンティティ名。ヒアリングの用語をそのまま使う）",
     "role": "assigned または assignee または resource",
     "notes": "件数の目安・属性等、ヒアリングに書かれている補足"}
  ],
  "assignment_structure_notes": "通常の「1対象を1つの割り当て先に1回だけ割り当てる」構造と
    異なる点があれば具体的に記述。無ければ空文字列。",
  "hard_rules": ["絶対に守るべきルールを、ヒアリングの記述に忠実に列挙"],
  "soft_rules": ["できれば守りたいルールを列挙。無ければ空リスト"],
  "objective_priority_type": "lexicographic | weighted | single | unknown",
  "objective_stages": ["目的を優先順位順に列挙。1つしかなければ1件のみ"],
  "resource_sharing_notes": "資源の同時使用に関するルール（同時に1つのみ／複数可で上限あり等）",
  "infeasible_handling_notes": "対応しきれない場合・解けない場合の扱いについての記述",
  "other_structural_notes": ["上記に当てはまらないが構造上重要な記述があれば列挙"]
}
"""


def derive_structural_requirements(domain_name: str, hearing_texts: list) -> dict | None:
    """
    Stage1a.5a: ヒアリングテキストのみから業務構造チェックリストを抽出する。
    CSPLib知識・CP/MIP技術選定には踏み込まず、ヒアリングに明記されている内容の
    構造化のみを行う（新規の技術的判断を追加しないため、formulation_directiveの
    自動LLM抽出で懸念されたリスクは生じない設計）。抽出失敗時はNoneを返し、
    呼び出し元はスキップして従来通り進める（安全側設計）。
    """
    from llm.llm_client import call_llm, extract_json, default_model

    hearing_combined = "\n\n---\n\n".join(hearing_texts or [])
    if not hearing_combined.strip():
        return None

    user_prompt = f"## 業務名\n{domain_name}\n\n## ヒアリング内容\n{hearing_combined}\n\nJSON で構造化してください。"
    messages = [
        {"role": "system", "content": _STRUCTURAL_REQUIREMENTS_SYSTEM},
        {"role": "user", "content": user_prompt},
    ]
    try:
        raw = call_llm(messages, model=default_model(), max_tokens=2048, temperature=0)
        result = extract_json(raw)
    except Exception as e:
        logger.warning(f"[structural_requirements] 抽出失敗（無視してスキップ）: {e}")
        return None

    if not isinstance(result, dict):
        return None
    logger.info(
        f"[structural_requirements] {domain_name}: entities={len(result.get('entities') or [])}件, "
        f"hard_rules={len(result.get('hard_rules') or [])}件, "
        f"objective_priority_type={result.get('objective_priority_type')}"
    )
    return result


# ─────────────────────────────────────────────────────────────
# Stage 1a.5b: family_reference lookup（new_domain版、2026-08-02追加）
#
# 背景: docs/csplib_solver_family_tree.md はCSPLib 31問題を7ファミリーの親子関係で
# 整理しているが、`csplib_cp_mip_reference.json` の family_id/parent_problem_id は
# これまでコード側のどこからも参照されておらず、系統樹の構造知識はヒアリングシート
# 作成者が手書きする「【技術指示】」節を経由してのみStage2に伝わっていた
# （詳細: OptiBuddy_V81_devnotes/DESIGN_2026-08-02_family_structural_reference.md）。
#
# この関数は、match_type="new_domain"と判定されたヒアリングについて、同じfamily_id
# を持つ既に登録済みの兄弟ドメインを検索し、その「構造サマリ」（決定変数・制約・
# 目的関数の型の要約）を参考情報としてdomain_defに追加する。
#
# 意図的に行わないこと: 兄弟ドメインのソースコード全文は渡さない。LLMが参考コードを
# 強く模倣する傾向があり、技術選定（CP/MIP）が本来問題ごとに分かれるべき箇所まで
# 安易に揃えてしまうリスクがあるため（実例: Family A内でもCapitalProjectSelector・
# AuctionWinnerSelectorはMIP、PortfolioOverlapDesignerはミニマックス目的のためCP、
# と正しく技術選定が分かれている）。あくまで「判断材料」として渡すに留める。
# ─────────────────────────────────────────────────────────────

_CSPLIB_ID_RE = re.compile(r'CSPLib\s*ID[:：]\s*(prob\d+)', re.IGNORECASE)


def _extract_csplib_id(hearing_texts: list) -> str | None:
    """ヒアリングシート冒頭のヘッダーコメント（例: `CSPLib ID: prob065`）からCSPLib IDを抽出する。
    見つからない場合はNone（実案件のヒアリングにはCSPLib IDの記載が無いのが通常であり、
    その場合はfamily_reference lookup自体を単純にスキップする）。"""
    for text in hearing_texts or []:
        m = _CSPLIB_ID_RE.search(text or "")
        if m:
            return m.group(1)
    return None


# 2026-08-03追加: formulation_directive（執筆型）
#
# 背景: Koshoshiとの議論で「ユーザーは技術的な助言を出せないので、それを安定して供給する
# 責務は本エージェントが負うべき」という指摘を受けた。Phase2で一度は「Stage1a
# （_STAGE1A_SYSTEMの共通分類プロンプト）による自動LLM抽出」として設計したが、これは
# 技術情報が乏しい実案件ヒアリングで(a)質の低い当て推量、(b)抽出失敗によるブロック、の
# リスクがあるため見送った経緯がある。
#
# 今回の設計はそれとは執筆主体が異なる: _STAGE1A_SYSTEMには一切手を入れず、ヒアリング
# シートを実際に作成・処理する本エージェント自身が、CSPLib由来か実案件かに関わらず、
# ヒアリング内容とfamily_reference（family_reference lookupが返す同ファミリー内の
# 参考情報。あくまで「執筆時に参照する判断材料」であり、このJSON自体を機械的に
# 生成するものではない）を踏まえて明示的に書き、下記のマーカー形式でヒアリング
# テキストに埋め込む。書けない・自信が無い場合は素直にセクションを書かない（＝
# Noneのまま。free-textの【技術指示】節のみで進む既存フローに合流する）。
# 自動抽出ではなく人間相当の判断を経た執筆物なので、Phase2で懸念したリスクは
# 構造的に発生しない。
#
# 2026-08-04追記（バグ修正）: 従来 .{0,20}? という極端に短い許容幅で
# 「formulation_directive」という語の直後20文字以内に```json フェンスが
# 来ることを要求していたが、これは「見出しの直後に説明文を挟む」という
# 自然な書き方（このモジュール自身のdocstringが推奨する書式であり、実際
# transport_cost_minimizer_hearing_sheet_j.md等の実例でも見出しと```jsonの間に
# 155〜300文字程度の説明文が入っていた）を一切許容できず、常にサイレントに
# マッチ失敗＝formulation_directiveが一切Stage2に渡らない、という不具合が
# 過去の全実例で発生していたことが判明した（2026-08-04、ShiftRotationScheduler
# 3回目実行の事後検証で発覚。Gate2は別途ヒアリング全文を独自に読んで検証していた
# ため、Gate2の指摘文にformulation_directiveの内容が反映されているように見えても、
# 実際にはStage2の「必須順守」セクションには何も渡っていなかった）。
# 許容幅を2000文字に拡張し、通常の説明文を挟んだ書式でも正しく抽出できるようにする。
_FORMULATION_DIRECTIVE_RE = re.compile(
    r'formulation_directive.{0,2000}?```json\s*\n(.*?)\n```',
    re.IGNORECASE | re.DOTALL,
)

_FORMULATION_DIRECTIVE_REQUIRED_KEYS = (
    "decision_variables", "constraints", "objective_type", "recommended_technology",
)


def _extract_formulation_directive(hearing_texts: list) -> dict | None:
    """ヒアリングシート中の「## 技術指示（構造化 / formulation_directive）」節配下の
    ```json フェンスドコードブロックをパースする。本エージェントがヒアリング作成・処理時に
    明示的に書いた場合のみ存在する（自動LLM抽出ではない）。無い場合、パース失敗の場合、
    必須キーが揃っていない場合はいずれもNoneを返し、既存のfree-text技術指示のみのフローに
    安全に合流する。"""
    for text in hearing_texts or []:
        m = _FORMULATION_DIRECTIVE_RE.search(text or "")
        if not m:
            continue
        try:
            directive = json.loads(m.group(1))
        except Exception as e:
            logger.warning(f"[formulation_directive] JSONパース失敗（無視してスキップ）: {e}")
            continue
        if not isinstance(directive, dict):
            continue
        missing = [k for k in _FORMULATION_DIRECTIVE_REQUIRED_KEYS if not directive.get(k)]
        if missing:
            logger.warning(
                f"[formulation_directive] 必須キー不足のためスキップ: missing={missing}"
            )
            continue
        return directive
    return None


def _load_csplib_reference_problems() -> list[dict]:
    path = _BACKEND_ROOT / "dsl_repository" / "csplib_cp_mip_reference.json"
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return data.get("problems", [])
    except Exception as e:
        logger.warning(f"[family_reference] csplib_cp_mip_reference.json 読み込み失敗: {e}")
        return []


def _summarize_registered_domain_structure(problem_class: str) -> str | None:
    """登録済みドメインのsolver.pyモジュールdocstringだけを抽出して返す（ソース全文は含めない）。
    このリポジトリのsolver.pyは慣例として冒頭docstringに決定変数・制約・目的関数の定式化を
    記載しているため（例: capital_project_selector_solver.py, portfolio_overlap_designer_solver.py）、
    これを構造サマリとして再利用する。docstringが無い/読み込み失敗の場合はNone。"""
    _ensure_domain_registry_reconciled()
    src = _DOMAIN_SOURCE_FILES.get(problem_class, {})
    solver_path = src.get("solver")
    if not solver_path or not Path(solver_path).exists():
        return None
    try:
        code = Path(solver_path).read_text(encoding="utf-8")
        tree = ast.parse(code)
        doc = ast.get_docstring(tree)
        return doc.strip() if doc else None
    except Exception as e:
        logger.warning(f"[family_reference] {problem_class} のdocstring抽出失敗: {e}")
        return None


def lookup_family_reference(domain_name: str, hearing_texts: list) -> dict | None:
    """
    Stage1a.5b（family_reference lookup）。設計書の①に相当する。

    ヒアリングにCSPLib IDの記載があり、かつ同じfamily_idの兄弟ドメインが既に
    登録済みの場合のみ、参考情報の辞書を返す。それ以外（CSPLib ID記載なし、
    family_id不明、登録済み兄弟なし）は全てNoneを返し、呼び出し元は素通りできる
    設計にする（detect_extension_gaps()と同様、検出をスキップしてもパイプライン
    全体は止まらない安全側設計）。
    """
    csplib_id = _extract_csplib_id(hearing_texts)
    if not csplib_id:
        return None

    problems = _load_csplib_reference_problems()
    target = next((p for p in problems if p.get("csplib_id") == csplib_id), None)
    if not target:
        logger.info(f"[family_reference] {csplib_id} が csplib_cp_mip_reference.json に見つかりません")
        return None

    family_id = target.get("family_id")

    # 2026-08-02追加（Phase 2、Koshoshiとの議論により設計変更）: formulation_directiveを
    # LLMにヒアリング文から毎回新規推測させる（当初案）のではなく、csplib_cp_mip_reference.json
    # に既にキュレーション済みの「対象問題自身」の推奨解法・根拠を決定論的に返すだけに留める。
    # LLM呼び出しを追加しないため、実案件の「技術指示を書かない」ヒアリングに対しても
    # 誤った定式化を混入させるリスクがない（該当エントリが無ければ単にNoneが入るだけ）。
    this_problem = {
        "csplib_id": csplib_id,
        "title_ja": target.get("title_ja"),
        "summary_ja": target.get("summary_ja"),
        "recommended_approach": target.get("recommended_approach"),
        "approach_note_ja": target.get("approach_note_ja"),
    }

    if not family_id:
        # familyが特定できない場合でも、対象問題自身のCSPLib参照情報（上記this_problem）は
        # 有用な決定論的データなので、兄弟検索は諦めつつthis_problemだけ返す。
        logger.info(f"[family_reference] {csplib_id}: family_id不明のためthis_problemのみ返す")
        return {
            "family_id": None, "family_name_ja": None,
            "this_problem": this_problem, "siblings": [],
            "note": "family_idが特定できなかったため、対象問題自身のCSPLib参照情報のみです。",
        }

    siblings_meta = [
        p for p in problems
        if p.get("family_id") == family_id
        and p.get("csplib_id") != csplib_id
        and p.get("optibuddy_domain")
    ]

    sibling_summaries = []
    for sib in siblings_meta:
        sib_domain = sib["optibuddy_domain"]
        structure = _summarize_registered_domain_structure(sib_domain)
        sibling_summaries.append({
            "csplib_id": sib.get("csplib_id"),
            "title_ja": sib.get("title_ja"),
            "optibuddy_domain": sib_domain,
            "recommended_approach": sib.get("recommended_approach"),
            "structure_summary": structure or "（構造サマリを取得できませんでした）",
        })

    logger.info(
        f"[family_reference] {domain_name} ({csplib_id}, family={family_id}): "
        f"this_problem情報1件 + 兄弟ドメイン{len(sibling_summaries)}件を参考情報として付与"
    )

    return {
        "family_id": family_id,
        "family_name_ja": target.get("family_name_ja"),
        "this_problem": this_problem,
        "siblings": sibling_summaries,
        "note": (
            "this_problemは対象問題自身のCSPLib参照情報（推奨解法CP/MIPとその根拠）で、"
            "csplib_cp_mip_reference.jsonのキュレーション済みデータからの決定論的な参照です"
            "（LLMによる新規推測ではありません）。siblingsは同じCSPLibファミリー内で既に"
            "実装済みの兄弟ドメインの構造サマリで、判断材料として参考にしてください。"
            "ただし兄弟ドメインと同じ技術選定や同じコードパターンに揃えることを強制する"
            "ものではなく、this_problem.recommended_approachが兄弟と異なる場合はそちらを"
            "優先してください（実例: 同じファミリー内でも合計最大化型はMIP、ミニマックス型は"
            "CPを採用した前例があります）。"
        ),
    }


# 2026-08-02追加: Gate2構造適合チェック（Phase3、設計文書③の縮小版）
#
# 設計文書（DESIGN_2026-08-02_family_structural_reference.md）の③は本来、
# formulation_directive（決定変数・制約・目的関数を構造化した必須フィールド、②で導入予定
# だったもの）と生成コードを突き合わせる想定だった。しかしPhase2はKoshoshiの指摘を受けて
# 「formulation_directiveの新規LLM抽出」自体を見送り、lookup_family_reference()の
# 決定論的な参照データ（this_problem.recommended_approach）だけを返す設計に縮小した。
# そのためPhase3もformulation_directiveには依存できない。ここでは「決定論的に判定できる
# 範囲」に絞り、CSPLib参照データの推奨技術（CP/MIP）と生成solver.pyが実際に採用した技術を
# 静的パターンマッチで突き合わせるチェックのみを行う（新規LLM呼び出しなし）。
#
# advisory（非ブロッキング）として扱う: 誤検知率が未検証の新規チェックであり、design docの
# 段階導入案（§6 Phase3「警告として追加、まだブロッキングにしない」）に従う。CSPLib IDが
# ヒアリングに無い実案件では lookup_family_reference() が None を返すため常にスキップされ、
# 実案件の登録フローには一切影響しない。

_CP_TECH_RE = re.compile(r'docplex\.cp\b|CpoModel\(')
_MIP_TECH_RE = re.compile(r'docplex\.mp\b')


def _detect_solver_technology(solver_code: str) -> str | None:
    """
    生成されたsolver.pyのソースを静的パターンマッチし、CP/MIPどちらの技術を採用しているか
    を判定する。両方/どちらも検出できない場合は判定不能としてNoneを返す（安全側スキップ）。
    """
    if not solver_code:
        return None
    has_cp = bool(_CP_TECH_RE.search(solver_code))
    has_mp = bool(_MIP_TECH_RE.search(solver_code))
    if has_cp and not has_mp:
        return "CP"
    if has_mp and not has_cp:
        return "MIP"
    return None


def check_family_technology_conformance(
    domain_name: str, hearing_texts: list, solver_code: str | None
) -> list[str]:
    """
    Gate2構造適合チェック（Phase3で新設、Phase4でformulation_directiveに対応拡張。advisory）。
    技術選定の「正解」として何を使うかは優先順位を付ける:
      1. formulation_directive.recommended_technology — ヒアリング担当者（本エージェント）が
         明示的に確定させた技術的決定。あれば最優先でこちらを使う。
      2. family_reference.this_problem.recommended_approach — CSPLib文献キュレーション
         データに基づく決定論的な参照（formulation_directiveが無い場合のフォールバック）。
    どちらも新規LLM呼び出しは発生しない（前者はヒアリングに埋め込み済みのJSONをパースする
    だけ、後者は決定論的なJSON参照）。生成solver.pyの実採用技術は静的パターンマッチで判定し、
    不一致があれば警告文字列を1件返す。CSPLib由来でない・formulation_directive未記載の
    ヒアリング（実案件の大半）や、技術が静的に判定できない場合は空リストを返す。
    """
    recommended: str | None = None
    source_label = ""
    rationale = ""
    problem_label = domain_name

    try:
        formulation_directive = _extract_formulation_directive(hearing_texts)
    except Exception as e:
        logger.warning(f"[family_tech_check] formulation_directive抽出失敗（無視してスキップ）: {e}")
        formulation_directive = None

    if formulation_directive and formulation_directive.get("recommended_technology") in ("CP", "MIP"):
        recommended = formulation_directive["recommended_technology"]
        source_label = "formulation_directive（ヒアリング担当者が確定させた技術指示）"
        rationale = formulation_directive.get("rationale") or "(記載なし)"
    else:
        try:
            family_reference = lookup_family_reference(domain_name, hearing_texts)
        except Exception as e:
            logger.warning(f"[family_tech_check] family_reference lookup失敗（無視してスキップ）: {e}")
            family_reference = None
        if family_reference:
            this_problem = family_reference.get("this_problem") or {}
            if this_problem.get("recommended_approach") in ("CP", "MIP"):
                recommended = this_problem["recommended_approach"]
                source_label = "family_reference（CSPLib参照データ）"
                rationale = this_problem.get("approach_note_ja") or "(記載なし)"
                problem_label = this_problem.get("csplib_id", domain_name)

    if not recommended:
        return []

    actual = _detect_solver_technology(solver_code or "")
    if actual is None or actual == recommended:
        return []

    return [
        f"[Gate2構造適合チェック] {problem_label}: {source_label}上の推奨技術は{recommended}ですが、"
        f"生成されたsolver.pyは{actual}を採用しているようです（根拠: {rationale}）。技術選定が"
        "問題の性質（目的関数の型・対称性の有無等）と整合しているか確認してください。ただし"
        "推奨技術は参考情報であり、意図的な選択であればこの警告は無視して構いません。"
    ]


# ─────────────────────────────────────────────────────────────
# Stage 1b: シナリオ生成
# ─────────────────────────────────────────────────────────────

def _get_schema_example(base_domain: str) -> str:
    _ensure_domain_registry_reconciled()
    path = EXISTING_DOMAINS.get(base_domain)
    if path and Path(path).exists():
        return Path(path).read_text(encoding="utf-8")
    return "{}"


# 2026-07-18: tightシナリオの生成を廃止し、baseline/infeasibleの2シナリオ原則に変更
# （旧: 3シナリオ原則。tightは「際どいが解ける」ケースの設計意図が曖昧になりやすく、
# 登録時間の増加に見合う効果が薄いとKoshoshiが判断したため）。
_STAGE1B_SYSTEM = """\
あなたは業務データをJSON形式に変換する専門家です。
指定されたJSONスキーマに厳密に従ってシナリオデータを生成してください。

ルール:
- スキーマと同じフィールド名・構造を使用
- problem_class と domain はスキーマ例の値を使用
- JSON のみを返すこと（コードブロックマーカー不要）
- 2シナリオ（baseline / infeasible）を生成
- 規模はヒアリング文中に記載された実際の業務規模（例:「30名」「120枠」）に関わらず、
  割り当てられる側・割り当て先ともに概ね5〜15件程度の小規模に収めること。
  この生成データは登録時の構造検証（Gate 2）用であり、本番規模を再現する必要はない。
  本番規模の実データは登録後に別途シナリオとして追加される。

返す形式:
{
  "baseline": { ...スキーマ準拠... },
  "infeasible": { ...解なし... }
}
"""


def generate_scenarios_from_schema(
    domain_name: str,
    base_domain: str,
    classification: dict,
    hearing_texts: list,
    override_problem_class: str | None = None,
) -> dict:
    """
    override_problem_class: パターン3（extension_gapsがある）の場合に渡す。
    指定すると、生成されるJSONのproblem_classをbase_domainではなく
    この値（コピー先の新ドメイン名）に強制する。
    """
    from llm.llm_client import call_llm, extract_json, default_model

    snake            = _to_snake(domain_name)
    schema_example   = _get_schema_example(base_domain)
    hearing_combined = "\n\n---\n\n".join(hearing_texts)
    problem_class_instruction = (
        f'problem_class は "{override_problem_class}" を使用すること（スキーマ例の値をそのまま使用しないこと）。'
        if override_problem_class else
        ""
    )

    user_prompt = f"""## 業務名
{domain_name}（snake_case: {snake}）

## 分類結果
- 基底ドメイン: {base_domain}
- シナリオ名: {classification.get('scenario_name_jp', domain_name)}
- 説明: {classification.get('scenario_description_jp', '')}

## ヒアリング内容
{hearing_combined}

## 従うべきJSONスキーマ例（このフィールド構造に厳密に従うこと）
{schema_example}

---
上記スキーマに従い2シナリオを生成してください。
- baseline: 標準的なケース
- infeasible: 制約過剰で解なし

{problem_class_instruction}

JSON のみを返すこと。コードブロックマーカー（```json）は不要。
"""
    messages = [
        {"role": "system", "content": _STAGE1B_SYSTEM},
        {"role": "user",   "content": user_prompt},
    ]
    raw    = call_llm(messages, model=default_model(), max_tokens=16000)
    result = extract_json(raw)

    scenarios = {
        "baseline":   result.get("baseline",   {}),
        "infeasible": result.get("infeasible", {}),
    }

    # override_problem_class指定時は、LLMが指示を守らずスキーマ例の値をコピーしてしまった場合に備え、
    # 事後で確実にproblem_class/domainを上書きする（ルーティング先のすれを確実に防ぐ）。
    if override_problem_class:
        for sc in scenarios.values():
            if isinstance(sc, dict) and sc:
                sc["problem_class"] = override_problem_class
                sc["domain"]        = snake

    tag       = (domain_name[:6]).upper()
    tag_color = _domain_color(base_domain)
    name_jp   = classification.get("scenario_name_jp", domain_name)

    scenario_registrations = [
        {"name": f"{name_jp} — 標準",   "description": f"{classification.get('scenario_description_jp','')} (標準)",       "tag": tag, "tag_color": tag_color, "domain": snake, "file": f"Backend/dsl_repository/scenarios/{snake}_baseline.json"},
        {"name": f"{name_jp} — 解なし", "description": f"{classification.get('scenario_description_jp','')} (Infeasible)", "tag": tag, "tag_color": tag_color, "domain": snake, "file": f"Backend/dsl_repository/scenarios/{snake}_infeasible.json"},
    ]

    return {"status": "ok", "scenarios": scenarios,
            "scenario_registrations": scenario_registrations, "base_domain": base_domain}


def _validate_scenario_capacity(scenarios: dict) -> list[str]:
    """
    CVRP系（customers/vehicles/demand_kg/capacity_kgを持つ）のシナリオについて、
    単一顧客の需要が車両の最大積載量を超えていないか機械的に検証する。

    実際にTruckDispatcherのbaselineシナリオで、本来複数トラックに分載すべき
    大口貨物が1顧客ノードに集約されてしまい、常に未割り当てになる不具合が
    発生したことを受けた再発防止の安全網。CVRP系以外のスキーマ（customers/vehiclesが
    ない）では何も検出せず空リストを返す。
    """
    warnings = []
    for suffix, sc in scenarios.items():
        if not isinstance(sc, dict):
            continue
        customers = sc.get("customers")
        vehicles  = sc.get("vehicles")
        if not isinstance(customers, list) or not isinstance(vehicles, list):
            continue
        max_capacity = max((v.get("capacity_kg", 0) for v in vehicles if isinstance(v, dict)), default=0)
        if max_capacity <= 0:
            continue
        over = [(c.get("id", "?"), c.get("demand_kg", 0)) for c in customers
                if isinstance(c, dict) and c.get("demand_kg", 0) > max_capacity]
        if over:
            names = ", ".join(f"{cid}({demand}kg)" for cid, demand in over[:5])
            more = "..." if len(over) > 5 else ""
            warnings.append(
                f"（データ検証）{suffix}シナリオに、車両の最大積載量（{max_capacity}kg）を超える"
                f"需要を持つ顧客が{len(over)}件あります: {names}{more}。"
                f"どの車両にも物理的に割り当てられず、常に未割り当てになります。"
                f"実際には複数便に分割して配送すべき貨物が1顧客ノードに集約されていないか確認してください。"
            )
    return warnings


def _domain_color(base_domain: str) -> str:
    return {"YardPlanning": "#3b82f6",
            "TruckDispatcher": "#f97316",
            "NurseShiftWeeklyCap": "#ec4899",
            "LineChangeoverScheduler": "#10b981"}.get(base_domain, "#8b5cf6")


# ─────────────────────────────────────────────────────────────
# interpret_hearing
# ─────────────────────────────────────────────────────────────

def interpret_hearing(domain_name: str, hearing_texts: list, extra_attachments: list = None,
                       on_progress=None, base_domain_override: str | None = None,
                       force_new_domain: bool = False) -> dict:
    """
    on_progress: 任意のコールバック callable(stage: str)。
    "stage1a_classifying" → ("stage1a4_axis_a_fit_check" | なし) →
    ("stage1a5_extension_gap_check" | なし) → ("stage1b_scenario_gen" | なし) の
    順で呼ばれる（stage1a4はmatch_type="existing_domain"かつStage1a分類を
    実際に実行した場合のみ、2026-07-31追加）。
    /api/domain/run のジョブ進捗報告に使用する（app.py から渡される）。

    base_domain_override: 指定すると Stage1a（LLMによる自動分類）を呼ばずに、
    match_type="existing_domain" / base_domain=<この値> を強制する。
    Stage1aの分類プロンプトは temperature=1.0 かつヒアリング文の言い回しに
    敏感なため、同一内容でも施設配置系ヒアリング（例: 「候補地から開設先を選ぶ」）が
    new_domain / GhostKitchen拡張のどちらにも分類されうる。ユーザーがどちらの経路を
    通すか既に決めている場合は、この引数で確定させ、分類の揺れを避ける。
    force_new_domain: True の場合は逆に match_type="new_domain" を強制し、
    既存ドメインへの当てはめを一切試みない（Stage2のフル新規コード生成を
    確実に通したい場合に使う）。base_domain_override と同時指定時はこちらを優先する。
    """
    snake        = _to_snake(domain_name)
    exists_check = check_domain_exists(domain_name)

    all_texts = list(hearing_texts)
    if extra_attachments:
        for fpath in extra_attachments:
            try:
                content = Path(fpath).read_text(encoding="utf-8")
                all_texts.append(f"## 追加添付: {Path(fpath).name}\n\n{content}")
            except Exception as e:
                logger.warning(f"添付ファイル読み込み失敗: {fpath} — {e}")

    if force_new_domain:
        if on_progress: on_progress("stage1a_classifying")
        logger.info(f"[interpret] force_new_domain 指定によりStage1a分類をスキップ: {domain_name}")
        classification = {
            "match_type": "new_domain", "base_domain": None,
            "reason": "ユーザーが force_new_domain を明示指定",
            "confidence": 1.0, "scenario_name_jp": domain_name,
            "scenario_description_jp": "", "missing_info": [], "is_dsl4_candidate": True,
        }
    elif base_domain_override:
        if on_progress: on_progress("stage1a_classifying")
        logger.info(f"[interpret] base_domain_override 指定によりStage1a分類をスキップ: "
                    f"{domain_name} → {base_domain_override}")
        classification = {
            "match_type": "existing_domain", "base_domain": base_domain_override,
            "reason": f"ユーザーが base_domain_override={base_domain_override} を明示指定",
            "confidence": 1.0, "scenario_name_jp": domain_name,
            "scenario_description_jp": "", "missing_info": [], "is_dsl4_candidate": True,
        }
    else:
        if on_progress: on_progress("stage1a_classifying")
        logger.info(f"[interpret] Stage1a 分類開始: {domain_name}")
        classification = classify_problem(domain_name, all_texts)

    match_type     = classification.get("match_type", "new_domain")
    base_domain    = classification.get("base_domain")

    # 2026-08-04追加: Stage1aプロンプト自身のルール（_STAGE1A_SYSTEM）は
    # 「match_type=base_problemは既存ドメインへの転用先(base_domain)が明記されている
    # CSPLib基底問題（2026-08-06時点ではRCPSP→LineChangeoverSchedulerの1件のみ）の
    # 場合に限る」と定めているが、
    # LLMがこのルールに反してbase_domain=nullのままmatch_type="base_problem"を返す
    # ケースが実機で確認された（2026-08-03 SteelMillSlabDesign登録）。後続の分岐は
    # 「match_typeがexisting_domain/base_problemかつbase_domainが非nullか」でしか
    # 判定していないため実害はない（base_domainがnullなら結局new_domain相当の
    # フルコード生成ルートに落ちる）が、確認画面の「処理方式」表示がユーザーに
    # 誤解を与えるため、ここでラベル自体を正規化しておく。
    if match_type == "base_problem" and not base_domain:
        logger.warning(
            "[interpret] Stage1a分類の不整合を検出: match_type='base_problem' なのに "
            "base_domainが未設定です。プロンプトのルール2（転用先が明記されたCSPLib基底問題"
            "のみbase_problem）に反するため、match_type='new_domain' に補正します。"
        )
        match_type = "new_domain"
        classification["match_type"] = "new_domain"

    if base_domain and base_domain not in EXISTING_DOMAINS:
        normalized = _normalize_base_domain(base_domain)
        if normalized:
            logger.info(f"[interpret] base_domain 正規化: {base_domain} → {normalized}")
            base_domain = normalized
            classification["base_domain"] = normalized
        elif base_domain_override:
            # ユーザーが明示指定した値が正規化しても既存ドメインに一致しない場合は、
            # サイレントに new_domain 扱いにせず明確なエラーで止める。
            raise ValueError(
                f"[interpret_hearing] base_domain_override='{base_domain_override}' は "
                f"既知の既存ドメインに一致しません。EXISTING_DOMAINS: {sorted(EXISTING_DOMAINS.keys())}"
            )

    # Stage1a.4: 軸(a)適合チェック（2026-07-31分離、専用LLM呼び出し）。
    # classify_problem()が実際にLLM分類を行い、その結果match_type="existing_domain"
    # となった場合にのみ実行する。base_domain_override/force_new_domainで
    # classify_problem()自体をスキップした経路（上記if/elif）では、ユーザーが
    # 明示的にbase_domainを指定/上書きしているため、この自動チェックは行わない
    # （分離前の挙動と同じ: 軸(a)チェックはclassify_problem()の呼び出しに
    # 紐づいていたため、override経路では元々発火していなかった）。
    if (match_type == "existing_domain" and base_domain
            and not force_new_domain and not base_domain_override):
        if on_progress: on_progress("stage1a4_axis_a_fit_check")
        logger.info(f"[interpret] Stage1a.4 軸(a)適合チェック: base_domain={base_domain}")
        axis_a_result = check_axis_a_fit(domain_name, all_texts, base_domain)
        if axis_a_result["concern"]:
            classification.setdefault("missing_info", [])
            classification["missing_info"].append(axis_a_result["concern"])
            logger.warning(
                f"[interpret] 軸(a)適合チェック: 技術的前提の食い違いを検出 "
                f"(base_domain={base_domain}) — {axis_a_result['concern']}"
            )

    scenarios              = None
    scenario_registrations = []
    needs_code_generation  = False
    extension_gaps: list[dict] = []
    reusable_extensions: list[dict] = []

    if match_type in ("existing_domain", "base_problem") and base_domain:
        if on_progress: on_progress("stage1a5_extension_gap_check")
        logger.info(f"[interpret] Stage1a.5 拡張差分検出: base_domain={base_domain}")
        gap_result          = detect_extension_gaps(domain_name, all_texts, base_domain)
        reusable_extensions = gap_result["reusable_extensions"]
        extension_gaps      = gap_result["extension_gaps"]

        if on_progress: on_progress("stage1b_scenario_gen")
        logger.info(f"[interpret] Stage1b シナリオ生成: base_domain={base_domain}")
        # extension_gapsがある場合（パターン3）は、シナリオのproblem_classを
        # base_domainではなく新ドメイン名（domain_name）にする。確認後にapplyされる
        # コピー先ファイル（copy_domain_for_extension）の problem_class と一致させる必要があるため。
        override_pc = domain_name if extension_gaps else None
        s1b                    = generate_scenarios_from_schema(
            domain_name, base_domain, classification, all_texts, override_problem_class=override_pc)
        scenarios              = s1b["scenarios"]
        scenario_registrations = s1b["scenario_registrations"]

        # データ検証: 車両最大積載量を超える需要がないかをチェックし、
        # あればmissing_infoと合流させてneeds_confirmationゲートで人間に警告する。
        capacity_warnings = _validate_scenario_capacity(scenarios)
        if capacity_warnings:
            classification.setdefault("missing_info", [])
            classification["missing_info"].extend(capacity_warnings)
            logger.warning(f"[interpret] データ検証警告: {len(capacity_warnings)}件")
    else:
        logger.info("[interpret] new_domain → repomix 自動更新 + Stage2が必要")
        _ensure_repomix()
        needs_code_generation = True

    # Stage1a.5a: structural_requirements 抽出（2026-08-05追加）。new_domain（＝Stage2で
    # フル新規コード生成する場合）にのみ実行する。既存/基底ルートの拡張生成
    # （generate_scenarios_from_schema）はStage2を通らないため対象外。
    structural_requirements = None
    if needs_code_generation:
        if on_progress: on_progress("stage1a5a_structural_requirements")
        try:
            structural_requirements = derive_structural_requirements(domain_name, all_texts)
        except Exception as e:
            logger.warning(f"[interpret] structural_requirements抽出失敗（無視して続行）: {e}")

    # Stage1a.5b: family_reference lookup（2026-08-02追加）。match_typeに関わらず
    # 呼び出して問題ない（CSPLib IDの記載が無い、または該当ファミリーに登録済み兄弟が
    # 無ければNoneが返るだけで、既存の分岐処理には一切影響しない安全側設計）。
    family_reference = None
    try:
        family_reference = lookup_family_reference(domain_name, all_texts)
    except Exception as e:
        logger.warning(f"[interpret] family_reference lookup失敗（無視して続行）: {e}")

    # 2026-08-03追加: formulation_directive（執筆型、Phase4）。本エージェントがヒアリング
    # テキスト内に明示的に書いた場合のみ存在する。詳細は_extract_formulation_directive()
    # docstring・OptiBuddy_V81_devnotes/DESIGN_2026-08-02_family_structural_reference.md参照。
    formulation_directive = None
    try:
        formulation_directive = _extract_formulation_directive(all_texts)
    except Exception as e:
        logger.warning(f"[interpret] formulation_directive抽出失敗（無視して続行）: {e}")

    # 2026-08-03追加: classify_problem()（_STAGE1A_SYSTEM）が生成するmissing_infoは、
    # 同じinterpret_hearing()呼び出しの後段で確定するformulation_directiveの結果を
    # 知らずに独立して動く。そのため「CP/MIPどちらか断定できません」という質問が、
    # formulation_directiveで既に技術選定を明示指定していてもそのまま出てしまう
    # （2026-08-03のTransportCostMinimizer実機登録で実際に発生・確認済み）。
    # ここでformulation_directiveが確定していれば、classify_problem()側が独立生成した
    # 同種の質問（CP/MIP双方の語を含む＝_resolve_hedged_technical_directive()と同じ
    # 判定基準）をmissing_infoから除去し、確認画面に「答えが既に出ている質問」が
    # 重複して出ないようにする。formulation_directive自体を書き換えるわけではないので
    # 安全側（formulation_directive未記載の場合は何もしない）。
    if (
        formulation_directive
        and formulation_directive.get("recommended_technology") in ("CP", "MIP")
        and classification.get("missing_info")
    ):
        before_list = classification["missing_info"]
        after_list = [
            m for m in before_list
            if not (_CP_TOKEN_RE.search(m or "") and _MIP_TOKEN_RE.search(m or ""))
        ]
        removed = len(before_list) - len(after_list)
        if removed:
            classification["missing_info"] = after_list
            logger.info(
                f"[interpret] formulation_directive.recommended_technology="
                f"{formulation_directive['recommended_technology']} により、"
                f"classify_problem()が独立生成したCP/MIP確認質問{removed}件をmissing_infoから"
                "除去しました（formulation_directiveで既に解決済みのため）。"
            )

    return {
        "status": "ok", "match_type": match_type, "base_domain": base_domain,
        "classification": classification, "scenarios": scenarios,
        "scenario_registrations": scenario_registrations,
        "extension_gaps": extension_gaps,
        "reusable_extensions": reusable_extensions,
        "domain_name": domain_name, "snake_name": snake, "exists_check": exists_check,
        "needs_code_generation": needs_code_generation,
        "structural_requirements": structural_requirements,
        "family_reference": family_reference,
        "formulation_directive": formulation_directive,
    }


def _normalize_base_domain(raw: str) -> str | None:
    mapping = {
        "cvrp": "TruckDispatcher",
        "capacitatedvehiclerouting": "TruckDispatcher",
        "capacitated_vehicle_routing": "TruckDispatcher",
        "truckdispatcher": "TruckDispatcher",
        "rcpsp": "LineChangeoverScheduler",
        "nurseshift": "NurseShiftWeeklyCap", "nurse_shift": "NurseShiftWeeklyCap",
        "nurseshiftweeklycap": "NurseShiftWeeklyCap", "nurse_shift_weekly_cap": "NurseShiftWeeklyCap",
        "shiftscheduling": "NurseShiftWeeklyCap", "shift_scheduling": "NurseShiftWeeklyCap",
        "linechangeoverscheduler": "LineChangeoverScheduler",
        "line_changeover_scheduler": "LineChangeoverScheduler",
    }
    return mapping.get(raw.lower().replace(" ", "").replace("-", ""))


# ─────────────────────────────────────────────────────────────
# Stage 2: コード生成プロンプト（V4.4: 禁止パターンセクション追加）
# ─────────────────────────────────────────────────────────────

# CP Optimizer 禁止パターン（プロンプトに埋め込む）
_CPO_FORBIDDEN_PATTERNS = """\
## CP Optimizer 実装ルール（必ず守ること）

### ❌ 禁止パターン1: no_overlap に配列と transition_matrix を直接渡す
```python
# NG
mdl.add(mdl.no_overlap(interval_var_list, transition_matrix))
```
```python
# OK: sequence_var を経由する
from docplex.cp.modeler import build_cpo_transition_matrix  # 正しいimport

tm = build_cpo_transition_matrix([[0, 10, 20], [10, 0, 15], [20, 15, 0]])
seq = mdl.sequence_var(interval_var_list, types=list(range(n)), name="seq_xxx")
mdl.add(mdl.no_overlap(seq, tm))
```

### ⚠️ 重要: transition_matrix の正しいimport
```python
# ❌ NG: この関数は存在しない
from docplex.cp.modeler import transition_matrix

# ✅ OK: 正しい関数名
from docplex.cp.modeler import build_cpo_transition_matrix
```

### ❌ 禁止パターン2: presence_of() == 1 を論理式として使う
```python
# NG
mdl.presence_of(itv) == 1   # これは boolean expression でない
```
```python
# OK: presence_of() をそのまま使う
mdl.presence_of(itv)   # これが正しい boolean expression
```

### ❌ 禁止パターン3: if_then の第2引数に制約式を渡す
```python
# NG
mdl.if_then(condition, mdl.end_before_start(a, b))  # e2 が boolean でないエラー
mdl.if_then(condition, mdl.start_before_end(a, b))  # 同様
```
```python
# OK: optional interval には直接制約を適用する
# absent のとき自動的に無効になる
mdl.add(mdl.end_before_start(a, b))
```

### ❌ 禁止パターン4: if_then の第2引数に比較式を渡す
```python
# NG
mdl.if_then(condition, mdl.end_of(a) <= mdl.start_of(b))
```
```python
# OK: 補助変数または end_before_start を使う
mdl.add(mdl.end_before_start(a, b))
```

### ❌ 禁止パターン5: presence_of()/start_of()/end_of() をその場で組み立てて get_value() に渡す
```python
# NG: 解抽出時に optional interval_var の presence/start/end を毎回式で組み立てる
# この書き方はdocplexのバージョンによって解の内部マッピングと一致せず
# 毎回 KeyError になり、exceptで握り潰されると「解けているのに
# 全アイテム未割当」になる実際に発生した不具合パターン！
try:
    if msol.get_value(mdl.presence_of(itv)):
        start_val = msol.get_value(mdl.start_of(itv))
        end_val   = msol.get_value(mdl.end_of(itv))
except KeyError:
    continue
```
```python
# OK: get_var_solution() で interval var の解オブジェクトを直接取得する
var_sol = msol.get_var_solution(itv)
if var_sol is None or not var_sol.is_present():
    continue
start_val = var_sol.get_start()
end_val   = var_sol.get_end()
```

### ❌ 禁止パターン6: mdl.minimize()/mdl.maximize() を mdl.add() で包まない
```python
# NG: minimize() の戻り値をモデルに追加していないため、目的関数が一度も
# ソルバーに登録されない
makespan_expr = mdl.max([mdl.end_of(iv) for iv in task_itvs.values()])
mdl.minimize(makespan_expr)
```
```python
# OK: 必ず mdl.add() で包む（動作実績のあるTruckDispatcher/NurseShiftWeeklyCapは
# 両方ともこの形）
makespan_expr = mdl.max([mdl.end_of(iv) for iv in task_itvs.values()])
mdl.add(mdl.minimize(makespan_expr))
```

### ❌ 禁止パターン6b: 名前を付けていない式を msol.get_value(expr) でクエリする
```python
# NG: mdl.add(mdl.minimize(makespan_expr)) で目的関数として正しく登録していても、
# makespan_expr 自体は名前付き変数でもKPIでもないため、msol.get_value(makespan_expr) は
# 「Variable or KPI '...' not in the solution」で失敗する。禁止パターン6を修正しただけでは
# 解消しない別問題（2026-07-15 LineChangeoverScheduler登録で、禁止パターン6の修正を
# 適用した後も実際に再現した）。
makespan_val = msol.get_value(makespan_expr)
```
```python
# OK（2026-07-18〜必須）: 添付した solvers/base/solution_extraction.py の
# extract_makespan() / safe_objective_value() を使う。動作実績のある
# TruckDispatcher/NurseShiftWeeklyCapが手書きしていたPython手計算パターンを
# 共通コアに切り出したもの。inline で max(...) や get_objective_value() を
# 自分で書かず、必ずこの2関数を呼ぶこと（層A、独自実装は不可）。
from solvers.base.solution_extraction import extract_makespan, safe_objective_value

makespan_val = extract_makespan(schedule)                       # end_key既定"end"
obj_val      = safe_objective_value(msol, fallback=float(makespan_val))
```

### ❌ 禁止パターン7: no_overlap / cumulative（資源の同時使用制約）を自分で実装する

ヒアリングシート§9「資源の同時使用に関するルール」（a=同時に1つのみ / b=上限付きで
複数）は、添付した solvers/base/constraint_applier.py の `BaseConstraintApplier` を
経由すること。自前で `mdl.no_overlap(...)` や `mdl.pulse(...)` を直接書かない。
```python
# OK: BaseConstraintApplierのサブクラスを作り、constraints DSL配列を渡すだけにする
class {Domain}ConstraintApplier(BaseConstraintApplier):
    def _register_domain_handlers(self):
        pass  # ドメイン固有ハンドラがなければ何もしない

applier = {Domain}ConstraintApplier(mdl, task_itvs)
applier.apply_all(constraints)  # constraints[].type が "no_overlap" または "cumulative"
```
§9 a.（同時に1つのみ）は `{"type": "no_overlap", "params": {"task_ids": [...]}}`、
§9 b.（上限付き複数）は `{"type": "cumulative", "params": {"task_ids": [...], "capacity": N, "requirements": {...}}}`
というconstraints DSL要素に変換すること（converter側の役目）。

### ❌ 禁止パターン8: エンティティの割当先を `== 1` で強制する
```python
# NG: 「各顧客/タスクは必ずどこか1箇所に割り当てる」を等式で強制すると、
# 時間枠・容量等の理由で1件でも物理的に入らないエンティティがプールに
# 含まれているだけで、モデル全体が即座にinfeasibleになる
# （TruckDispatcher実機、2026-07-08〜同種のバグ。LNS Recreateが毎回
# 失敗する原因になった）。
mdl.add(sum(assign_vars[c][v] for v in vehicles) == 1)
```
```python
# OK: <= 1 に緩め、未割当を目的関数のペナルティとして表現する
# （ProductionLotSchedulerが採用している「未割当ペナルティ」方式と同じ考え方）。
mdl.add(sum(assign_vars[c][v] for v in vehicles) <= 1)
# 目的関数側: unassigned_penalty * (1 - sum(assign_vars[c][v] for v in vehicles))
```

### ❌ 禁止パターン9: feasible=False 判定で即returnし、他の検証を丸ごとスキップする
```python
# NG: 「解けなかった（infeasible/タイムリミット到達）」を一括りに早期returnすると、
# 既に組めている部分解に対する他の検証（時間枠逼迫・上限超過等）が一切実行されない
# まま握りつぶされる（TruckDispatcher実機、2026-07-08、Koshoshiとの相談で発覚）。
def check_issues(solution, feasible, ...):
    if not feasible:
        return [{"category": "infeasible", ...}]
    # tw_tight/duty_overtime等のチェックはここに書かれていても、
    # feasible=Falseのときは一度も実行されない
    ...
```
```python
# OK: feasibleの真偽に関わらず、既に組まれている部分解に対する追加検証を必ず実行する。
# 原因の内訳（例: insufficient_fleet_capacity等の分類）はfeasible判定と独立に出す。
def check_issues(solution, feasible, ...):
    issues = []
    if not feasible:
        issues.extend(_classify_infeasible_reason(solution, ...))
    issues.extend(_check_time_window_and_overtime(solution, ...))  # feasibleに関わらず実行
    return issues
```

### ❌ 禁止パターン10: リソースのハード制約を容量/距離のみで判定し、時間軸の累積を見ない
```python
# NG: 車両・スタッフ等のリソースへの割当を、容量や距離だけで判定すると、
# 遠方の顧客/タスクを後から詰め込んだ結果、拘束時間（max_duty_min等）の
# ハード制約を実行時に超過してしまう（TruckDispatcher実機、2026-07-08、
# Koshoshiとの相談⑤で発覚。容量チェックだけでは検出できなかった）。
if remaining_capacity(vehicle) >= demand(customer):
    assign(vehicle, customer)
```
```python
# OK: 各リソースの現在の状態（時刻・直前地点等）を逐次シミュレーションし、
# 割当後の完了予測がハード制約（max_duty_min等）を超えないことを確認してから
# 割り当てる。
projected_finish = simulate_finish_time(vehicle, customer)
if remaining_capacity(vehicle) >= demand(customer) and projected_finish <= vehicle["max_duty_min"]:
    assign(vehicle, customer)
```

### ❌ 禁止パターン11: logical_or()/logical_and() に3個以上の条件を個別の位置引数で渡す
```python
# NG: docplex.cp の logical_or()/logical_and() は位置引数を最大2個
# (e1, e2=None) までしか受け付けない。3個以上を個別の位置引数で渡すと
# TypeError: logical_or() takes from 1 to 2 positional arguments but N were given
# で実行時にクラッシュする（2026-08-03 VesselDeckLoader実機登録で発覚。
# Gate2の動的検証はこれを「実行不可能」と誤解されやすい文言で報告して
# しまい、真因の特定にKoshoshiとの往復が発生した）。
mdl.add(mdl.logical_or(
    x_i + l_i <= x_j,
    x_j + l_j <= x_i,
    y_i + w_i <= y_j,
    y_j + w_j <= y_i,
))
```
```python
# OK: 3個以上まとめる場合は必ずリストで渡す（logical_and()も同様）
mdl.add(mdl.logical_or([
    x_i + l_i <= x_j,
    x_j + l_j <= x_i,
    y_i + w_i <= y_j,
    y_j + w_j <= y_i,
]))
```

## 数量要件（hard/soft）の実装ルール（必ず守ること）

ヒアリングシート§4-1「人数・数量に関するルールの扱い」（a=解なし扱い=hard /
b=欠員許容=soft、未記入時はb=soft）は、添付した solvers/base/quantity_requirement.py
の `apply_quantity_requirement()` を必ず経由すること。過去に、この分岐自体が
存在せず常に無条件のハード等式制約（`== required_count`）を追加していた不具合が
発見されている（`docs/DESIGN_2026-07-18_layer_ab_pattern_library.md` 2-2-2節）。
```python
from solvers.base.quantity_requirement import apply_quantity_requirement

shortfall = apply_quantity_requirement(
    mdl, present_vars, required_count,
    mode="soft",             # ヒアリング§4-1の回答（"hard" or "soft"）
    comparison="at_least",   # hardの場合の既定。過剰配置まで禁止したい場合のみ"exact"
)
if shortfall is not None:
    understaffing_terms.append(PENALTY_WEIGHT * shortfall)  # soft時のみ目的関数に加算
```

## 解が見つからない場合の結果フォーマット規約（必ず守ること）

ソルブが失敗した場合（`msol is None` 等）も、`solve()` の戻り値の**トップレベル**に
明示的に `"feasible": False` を含めること。`solutions` 配列を空にするだけでは、
Gate2動的検証が `result.get("feasible")` をトップレベルから読み取れず
`None`（期待値`False`との不一致）として検出してしまう
（2026-07-15 LineChangeoverScheduler登録で実際に発生した不具合）。
`_make_result()` のようなヘルパーには `feasible: bool` を明示的な引数として持たせ、
呼び出し側の全経路（バリデーションエラー時・ソルブ失敗時・成功時）で明示的に渡すこと。

この `msol is None` 分岐（＝業務上の制約矛盾によるinfeasible判定）で組み立てるissueの
`id` も、下記「想定外例外と業務上のinfeasibleを区別すること」節と同じ理由で、必ず
`"solve_failed"` にすること（`"infeasible"` 等の独自名を付けないこと）。Frontend
(`InfeasibleView.tsx`) は `issues[].id === "solve_failed"` であることだけを見て
Infeasible画面を表示するかどうかを決めており、`feasible` フラグそのものは見ていない。
「想定外例外」と「業務上のinfeasible」はコード上別々の分岐（try/exceptの外と中）に
分かれて実装されることが多いため、片方の分岐にだけこの規約を適用し、もう片方に
適用し忘れる事故が実際に発生している（2026-07-31 NursingWorkloadBalance登録:
`msol is None` 分岐のissue idを独自に`"infeasible"`としてしまい、feasible=Falseは
正しく返っていたにもかかわらずFrontendのInfeasible画面が表示されなかった）。
**この規約は「例外処理」限定ではなく、feasible=Falseを返す全ての分岐に適用される。**

## 想定外例外と業務上のinfeasibleを区別すること（必ず守ること）

`mdl.solve()` 等を囲む `except Exception` ハンドラで、CE-limit（ライセンス上限）
以外の例外を「制約を満たす解が存在しない(infeasible)」と同じ形の結果に丸めて
返さないこと。過去に、`no_overlap()` のAPI誤用によるAssertionError（実装バグ）が
`except Exception: return self._infeasible_result(...)` でそのままinfeasible扱いに
され、「意識して入れた要件がなぜ実装に反映されないのか」の原因調査に長時間を
要した実例がある（NurseShiftEval実機、2026-07-28）。同種の握りつぶしは
car_sequencing_solver.py / store_site_solver.py / meeting_room_solver.py /
nurse_shift_weekly_cap_solver.py / truck_dispatcher_solver.py でも見つかり、
2026-07-28時点で修正済み。

対策: `solvers/base/solver_error_result.py` の `build_solver_crash_issue(exc)` /
`solver_crash_extra_fields(exc)` を使うこと。
```python
from solvers.base.solver_error_result import build_solver_crash_issue, solver_crash_extra_fields

try:
    msol = mdl.solve(TimeLimit=time_limit, LogVerbosity="Quiet")
except Exception as e:
    if is_ce_limit_exceeded(e):
        raise CeLimitExceededError(str(e)) from e   # 既存のCE-limitフォールバック経路へ
    logger.error(f"[YourDomain] mdl.solve() 例外: {e}", exc_info=True)
    result = {
        "status": "ok", "feasible": False,
        "metadata": {"problem_class": "YourDomain"},
        "solutions": [], "issues": [build_solver_crash_issue(e)],
        "_solver_version": "your_domain_v1.0",
    }
    result.update(solver_crash_extra_fields(e))
    return result
```
注意: issueの `id` は必ず `"solve_failed"` のままにすること。Frontend
(`useStudioState.ts` の `hasSolveFailed` 判定、`InfeasibleView.tsx`) は
`issues[].id === "solve_failed"` であることのみを見て「Infeasible画面
（制約見直しタブ・AI緩和提案）」を表示するかどうかを決めており、`feasible`
フラグは見ていない。id を独自の値（`"solver_exception"` 等）にすると
Infeasible画面自体が表示されなくなり、CRITICALな例外が画面上どこにも
明示されない退行になる（car_sequencing_solver.py の2026-07-25付コメント
参照）。`build_solver_crash_issue()` はこの点を踏まえて実装済みなので、
自前でissue dictを組み立てず必ずこのヘルパーを使うこと。

## 能力・互換性フィールドの死データ化防止（必ず守ること）

Business DSLの「割当先」オブジェクト（ライン/プリンター/マシン等）に `supported_types` /
`compatible_products` のような能力・対応種別を表すフィールドを定義した場合、**そのフィールドは
必ずconverterの互換性解決ロジック（割当先候補リストの絞り込み）で実際に参照すること**。
これまで以下2件の実際の不具合が発生している：

1. ProductionLotScheduler: ラインの `compatible_products` を定義したのに、ロットの
   `compatible_lines` しか見ていなかったため、本来割り当てられないはずの組み合わせが
   割り当て可能になっていた。
2. TestDomain: プリンターの `supported_types` を定義したのに、ジョブの `job_type` と
   突き合わせるロジックが一切なく、`compatible_printers` のみで判定されていたため、
   本来不可能な組み合わせ（カラージョブ→白黒専用機）が可能になっていた。

対策: 割当先オブジェクトに能力/対応種別フィールドを定義したら、割当される側オブジェクトの
対応する種別フィールドとの突合をconverter内で必ず計算し、明示的ホワイトリスト（あれば）との
交差を最終的な互換リストとすること。定義したフィールドはどこにも参照されない「死データ」にしないこと。

## 解抽出異常検知（必ず守ること）

`solvers/base/issue_rules.py` に `build_full_unassignment_issue(assigned_count, total_count, entity_label, extra_hint)`
という共通ヘルパーがある。解抽出後の割当済み件数が0件（=入力アイテムが1件以上あるにもかかわらず全件未割当）の場合に
警告 issue を返す。新規ドメインの `_detect_issues()` でも必ずこれを呼び出して issues に含めること（パターンは
`solvers/production_lot_scheduler_solver.py` の `_detect_issues()` を参照）。
これはCP Optimizerの解抽出バグ（禁止パターン5参照）を実際に発生させてしまった場合でも、
ドメイン知識ゼロで異常を検知できる最後の安全網である。

## 目的関数実装ルール（必ず守ること）

ヒアリング資料には「目的関数」セクション（例: "最小化: Σ(...) + Σ(...) + Σ(未割り当て...)"）が
記載されている場合がある。このセクションに列挙された **各項（Σ(...)で表現される要素）は、
1つも欠かすことなくコードの目的関数（penalty_terms / objective 式）に反映すること。**

過去に発生した実際の不具合パターン:
  - ヒアリングには「Σ(未割り当てロット × 大きなペナルティ)」と明記されていたにもかかわらず、
    生成コードでは「緊急(urgent)フラグを持つアイテムのみ」に未割り当てペナルティを限定してしまい、
    非緊急アイテムは「割り当てなくてもコスト0」になった。
  - その結果、ソルバーは「全アイテムを未割り当てにする」解を最適解として選んでしまい、
    solve() 自体は成功 (status=ok, 1 solution found) しているのに、
    実質的に何も割り当てられない実用不可能な結果を返す不具合が発生した。

このパターンを避けるため、以下を必ず守ること:
  1. ヒアリングの目的関数に登場する Σ 項（遅延・コスト・未割り当て等）は、
     "urgent" や "priority" などの一部フラグを持つアイテムに限定せず、
     **原則として全アイテムに適用**すること。
     優先度・緊急度によって重み（ペナルティの大きさ）を変えるのは良いが、
     「対象から完全に除外する」のは要件逸脱になりやすいので避けること。
  2. optional interval_var など「割り当てなし」を許容する変数を使う場合は、
     未割り当てのままだと目的関数上のペナルティが 0 になる設計は避け、
     必ず「割り当てないこと自体に対するペナルティ項」を全アイテムに対して追加すること。
  3. 生成したコードの目的関数部分には、ヒアリングの目的関数セクションとの対応を
     コメントで明記すること（例: `# ヒアリング目的関数 項3: 未割り当てペナルティ（全アイテム対象）`）。

## CE上限（CPLEX Community Edition評価版のモデルサイズ上限）フォールバックの実装ルール（必ず守ること）

CPLEXの無料版（Community Edition）にはモデルサイズ上限があり、業務データの規模が
大きいシナリオで超過すると例外が発生する（docplex.cp: "Problem size limit exceeded"、
docplex.mp: "CPLEX Error 1016"）。この上限は変数・制約の宣言時点ではなく**エンジンを
実際に呼び出す時点（`.solve()`）でのみ**発生する。ヒアリングシートには現れない
実装技術上の要件だが、業務データが小規模な間は気づかれず、後から規模が増えて
初めて発覚することが多いため、新規ドメインは登録時点から必ずこの対応を組み込むこと。
`solvers/base/ce_limit_lns.py`（CP用）/ `solvers/base/ce_limit_mip_fallback.py`（MIP用）を
必ず経由し、独自の分割・フォールバックロジックを書かないこと（下記の import文・呼び出し
コード例の通りに呼ぶだけでよく、内部実装を読む必要はない）。

### MIPドメイン（docplex.mp採用時）: 必ず守ること

`mdl.solve(...)` の呼び出し箇所を、素の呼び出しではなく
`solve_with_ce_fallback(mdl, ...)`（`solvers/base/ce_limit_mip_fallback.py`）に置き換えるだけでよい。
ドメイン側の追加コード・追加ファイルは不要。
```python
from solvers.base.ce_limit_mip_fallback import solve_with_ce_fallback
sol = solve_with_ce_fallback(mdl, log_output=False)  # 素のmdl.solve(...)から置き換えるだけ
```

### CPドメイン（docplex.cp採用時）: 必ず守ること

1. モデル構築関数（`_build_and_solve`等）の `mdl.solve(...)` 呼び出しをtry/exceptで包み、
   `is_ce_limit_exceeded(e)` がTrueの場合のみ `CeLimitExceededError` を投げ直す
   （それ以外の例外は通常通り扱う）。
```python
from solvers.base.ce_limit_lns import is_ce_limit_exceeded, CeLimitExceededError

try:
    msol = mdl.solve(TimeLimit=time_limit, LogVerbosity="Quiet")
except Exception as e:
    if is_ce_limit_exceeded(e):
        raise CeLimitExceededError(str(e)) from e
    raise
```
2. 公開の `solve()` メソッド側で `CeLimitExceededError` を捕捉し、
   `run_solve_with_ce_limit_fallback()` を呼ぶだけにする（再試行回数・バッチサイズの
   段階的縮小・打ち切りロジックは全て層Aが持つため、ここに独自ロジックを書かないこと）。
```python
from solvers.base.ce_limit_lns import run_solve_with_ce_limit_fallback

try:
    solution_data, candidates = self._build_and_solve(entities, tasks, config)
except CeLimitExceededError:
    try:
        from solvers.{snake}_batch_decomposer import (
            PRIMARY_ENTITY_KEY, build_subset_input, merge_results,
        )
    except ImportError:
        # 3.の分割軸が特定できずbatch_decomposerを生成しなかった場合の安全な
        # フォールバック。クラッシュさせず、解なし＋警告として返す。
        return {
            "status": "ok", "feasible": False,
            "metadata": {"problem_class": "{ProblemClass}"},
            "solutions": [], "issues": [{
                "id": "ce_limit_no_adapter", "severity": "CRITICAL",
                "title": "CPLEXの無料版で扱える件数を超えています",
                "message": "データ件数を減らすか、正規ライセンスのご利用をご検討ください。",
                "relatedContainerIds": [],
            }],
            "_solver_version": "{snake}_v1.0",
        }
    return run_solve_with_ce_limit_fallback(
        solver_class=type(self), solver_input=self.dsl,
        primary_entity_key=PRIMARY_ENTITY_KEY,
        build_subset_input=build_subset_input, merge_results=merge_results,
        problem_class="{ProblemClass}", solver_version="{snake}_v1.0",
    )
```
3. `Backend/solvers/{snake}_batch_decomposer.py` を新設し、`PRIMARY_ENTITY_KEY`・
   `QUOTA_FIELDS`・`build_subset_input`・`merge_results` を宣言する。
   **`PRIMARY_ENTITY_KEY`（どのリストを分割軸にするか）の決め方（必ずこの手順で判断すること）**:
   a. ドメインの制約を1つずつ、「ある1種類のエンティティ（例: スタッフ・車両・機械）の
      1インスタンス内だけで完結する制約か（例: 1人のスタッフのno_overlap・1台の車両の
      積載上限）」「複数インスタンスにまたがる集計制約か（例: 1タスクへの必要人数の合計、
      1日の総処理件数）」に仕分ける。
   b. 「1インスタンス内で完結する制約」の対象エンティティで**全ての**そのような制約が
      説明できるなら、そのエンティティのリストフィールド名を`PRIMARY_ENTITY_KEY`にする
      （分割してもそれらの制約は必ず同じバッチ内に収まるため壊れない）。
   c. 残った「複数インスタンスにまたがる集計制約」（通常は必要数・上限数のような
      カウント系）は、`QUOTA_FIELDS`宣言でバッチ間の残数持ち越しとして表現する。
   d. **bで単一のエンティティに全て説明がつかない場合**（例: 2種類のエンティティの
      両方にまたがる制約が複数ある）は、分割軸の選定を安全に自動化できないケースである。
      この場合は`{snake}_batch_decomposer.py`を生成せず、2.のtry/except ImportErrorに
      よる「ce_limit_no_adapter」警告フォールバックに委ねること。無理に分割軸を推測して
      誤った実装をするより、警告を出して規模を理由に断る方が安全（誤った分割は
      クラッシュではなく「一部の制約が静かに破られた解」という気づきにくい不具合になる
      ため）。
   `build_subset_input`/`merge_results`の実装は、`solvers/nurse_shift_weekly_cap_batch_decomposer.py`
   をテンプレートとすること（`reduce_quota_fields()`への宣言＋そのドメイン自身の
   `build_candidates`/`build_task_summary`/`build_metrics`/issue検出関数の呼び直し
   のみで構成し、新規の集計ロジックを書かないこと）。

## ⚠️ 添付の参考資料（既存ドメインのソースコード一式）の扱いについて（必ず守ること）

添付されている参考資料には、このリポジトリに既に登録済みの他ドメイン（別の業務）のソースコード
全体が含まれています。これは以下の目的にのみ使ってよい参考情報です。

- Pythonの実装規約・共通ユーティリティの呼び出し方・上記の禁止パターン集の具体例としての参照
- converter/solver/ui_converterの3層構造やファイル配置など、リポジトリ全体の慣例の把握
- **アルゴリズム・データ構造レベルの実装パターンの再利用**（積極的に行ってよい）:
  例えば「時間軸上のスケジューリングには interval_var + no_overlap（必要なら sequence_var
  で順序も表現）」「経路・訪問順序を伴う割当には sequence_var ベースのルーティング表現」
  「拠点間の連続量の配分には docplex.mp の線形計画」といった、CP Optimizer/MIPの一般的な
  定式化テクニックは、今回の業務構造に合っている限り、参考資料中のどのドメインの実装から
  学んでもよい。これはコードの中身（変数名・アルゴリズム構造）の話であり、以下で禁止する
  「業務構造の借用」（フィールド名・エンティティ名等、ドメイン固有の語彙や概念）とは別物。

**やってはいけないこと**: 今回実装する業務のフィールド構成・エンティティ構造・制約・目的関数を、
参考資料中の別ドメイン（特に表面的に似て見えるもの、例: 「部屋」「割り当て」「スケジューリング」
等の語を含むドメイン）から借用・模倣すること。今回実装すべき業務構造は、後述の「ドメイン定義」
セクションおよび（存在する場合）「技術指示」「業務構造チェックリスト」セクションに書かれている
内容が唯一の正解です。参考資料中の他ドメインと構造的に似ていたとしても、それは偶然の一致に
過ぎず、真似する理由にはなりません。逆に、ヒアリング内容に明記されている構造（例: 1件の依頼が
複数の訪問・工程を要求する、資源の共有に上限がある、目的が段階的優先順位を持つ、等）を、
参考資料に無いからという理由で単純化・省略することも禁止します。

要するに: 「どう実装するか（アルゴリズム・技術パターン）」は参考資料から自由に学んでよいが、
「何を実装するか（業務のエンティティ・構造・ルール）」は今回のドメイン定義・技術指示・
業務構造チェックリストだけを見ること。
"""

# ─────────────────────────────────────────────────────────────
# UI文言のi18n対応指示（Stage2プロンプトに埋め込む、is_dsl4ドメインのみ）
#
# 背景（2026-08-11追加）: ドメイン登録パイプライン（/api/domain/*系）は元々
# i18n対応の対象外だった（Backend/i18n/__init__.py参照）。実際にi18n対応した
# のはNurseShiftWeeklyCap/NursingWorkloadBalanceの2ドメインのみで、いずれも
# 登録完了後の手動追加実装（ヒアリングシート補足欄の自由記述がきっかけ）で
# あり、新規ドメイン全般に自動適用される仕組みはなかった（prob131登録に
# 際してKoshoshiから指摘）。Koshoshi合意（2026-08-11）により、Stage2フル
# 新規生成（is_dsl4のnew_domain登録）ではBackend/i18n/{snake}_messages.py を
# 毎回自動生成する方針に変更する。パターン3（拡張登録、Stage2-lite V2）は
# 別コードパスのため対象外（既存25ドメインの遡及対応も対象外）。
# ドメイン名・snake_case名を含まない全ドメイン共通の指示文のみをここに置き、
# static_part（prompt cachingのプレフィックス対象）に含める。
# ─────────────────────────────────────────────────────────────
_I18N_MESSAGE_DICT_INSTRUCTION = """\
## UI文言の日英i18n対応（必ず守ること）

新規ドメインのUI文言（issue/KPI/テーブル列見出し/プランラベル等、solver.py・
ui_converter.pyがフロントエンドに返すtitle/message/label/sub/unitの値）は、
日本語文字列を直接埋め込まず、必ず Backend/i18n/{snake}_messages.py
（{snake}は後述の「実装方針」セクションのdomain (snake_case)の値）経由の
t(key, **params) 呼び出しで解決すること。

### 実装パターン（既存の nursing_workload_balance_messages.py と同じキー命名規則）

```python
# Backend/i18n/{snake}_messages.py
from __future__ import annotations
from i18n import make_translator

_JA = {
    "issue.no_input.title": "入力データ不足",
    "issue.no_input.message": "...データが空です。",
    "issue.infeasible.title": "制約を満たす割り当てが見つかりませんでした",
    "issue.infeasible.message": "...",
    "issue.unassigned_x.title": "未割り当て...: {name}",  # {} はstr.format用プレースホルダ
    "kpi.xxx.label": "...",
    "kpi.xxx.unit": "件",
    "table.section.xxx.title": "...",
    "table.column.xxx": "...",
    "solver.planLabel": "...",
}
_EN = {
    # _JAと同じキーを全て英訳して用意すること（欠けているキーは自動的に日本語へ
    # フォールバックするが、フォールバック発生時はwarningログが出るため未対応キーを
    # 残さないこと）
    "issue.no_input.title": "Insufficient input data",
    # ...（以下同様、_JAの全キーに対応する英訳を用意）
}
t = make_translator(_JA, _EN, "{snake}")
```

```python
# solver.py / ui_converter.py 側
from i18n.{snake}_messages import t
...
"title": t("issue.no_input.title"),
"message": t("issue.unassigned_x.title", name=name),
```

- キーは生の日本語文字列ではなく、rule_id / KPIのid / テーブル列key / section_id等の
  安定識別子ベースにすること。
- str.format()の埋め込みパラメータ（{name}等）は _JA/_EN 両方の対応キーで
  同じプレースホルダ名を使うこと。
- _JA/_EN は本ドメインで実際に使うキーを両方とも網羅すること（日本語のみを
  直書きした場合、Gate2の静的チェックでadvisory警告として報告される）。
"""

# ソルバー戻り値フォーマット仕様（プロンプトに埋め込む）
_SOLVER_OUTPUT_SPEC = """\
## ソルバー戻り値フォーマット（必ず守ること）

solve() メソッドは以下のキーを含む dict を返すこと。
Frontend の汎用 Gantt コンポーネントは `tasks` 配列のみを参照する。

```python
return {
    "status":   "ok",
    "tasks":    [          # ← 必須。Gantt 表示用。空リストでも可
        {
            "id":         str,   # ユニークID
            "name":       str,   # 表示名
            "resource":   str,   # Y軸ラベル（ライン名・機械名・担当者名など）
            "resourceId": str,   # resource の ID
            "start":      int,   # 開始時刻（分）
            "end":        int,   # 終了時刻（分）
            "duration":   int,   # end - start
            "type":       str,   # "LOAD" | "DISCHARGE" | "MOVE" | "OTHER"
        },
        ...
    ],
    "makespan": int,       # 全タスク終了時刻の最大値（分）
    "issues":   list,      # イシューリスト
    "solutions": list,     # ドメイン固有の詳細データ（任意）
}
```
"""

# converter⇔solver キー整合性契約（4DSLドメインのプロンプトに埋め込む）
#
# 背景: HospitalShiftPlanner登録時の solver_time_limit キー名不一致を皮切りに、
# 4DSLドメイン登録のたびに「converterが出力しないキーをsolverが参照する」
# 不具合が、毎回異なるキー名の組み合わせで繰り返し発生している
# （StoreSite: 1回目converter未検出、2回目不一致10キー、3回目不一致4キー、
# 4回目不一致16キー）。Gate2静的field-check（check_converter_solver_field_consistency）
# は生成後に不一致を検出できるが、生成前にLLMへ「キー名を一致させる」よう
# 明示的に指示する仕組みがこれまで存在しなかった。この定数はその穴を埋める。
_CONVERTER_SOLVER_CONTRACT = """
## converter ⇔ solver のキー整合性（必ず守ること・過去に繰り返し発生した不具合）

{snake}_converter.py の convert_{snake}_to_solver() が返す dict のキーと、
{snake}_solver.py の {name}Solver が参照するキーは、完全に一致させること。
これまでの複数ドメイン登録で、converterが出力しないキーをsolverが
.get("キー名") や ["キー名"] で参照し、常時デフォルト値へフォールバックする、
または実行時例外になる不具合が繰り返し発生している。

手順:
1. solver_input dict に含める全キーの一覧を先に決め、両ファイルの冒頭に
   コメントで列挙する（例: # solver_input keys: staff, tasks, config, ...）。
2. converter.py はその全キーを漏れなく出力する（値が空でもキー自体は必ず存在させる）。
3. solver.py はその一覧に無いキーを一切参照しない。
4. 出力前に、converterが出力する全キーとsolverが参照する全キーを
   自己チェックで突き合わせ、一致することを確認してから出力する。
"""


# 2026-07-17: Anthropicのprompt cachingを有効化するためのマーカー。_build_stage2_prompt()
# が返す文字列に埋め込まれ、generate_domain_files()側でこのマーカーの前後を分割する。
# マーカーより前（静的パート）は attachment_section + _CPO_FORBIDDEN_PATTERNS のみで構成され、
# ドメイン名や snake_case 名の埋め込みを一切含まないため、どのドメインを生成する場合でも
# バイト単位で完全に同一の内容になる。これによりAnthropic側でcache_controlを使った
# プレフィックスキャッシュの対象にでき、Stage2呼び出しのコスト・レイテンシを削減できる
# （マーカーより後の動的パートはドメインごとに内容が変わるためキャッシュ対象外）。
# 非Anthropicプロバイダー等、分割せず1本の文字列として使いたい呼び出し元は、
# マーカーを空文字に置換すれば従来通りの結合済みプロンプトになる。
_STAGE2_CACHE_SPLIT = "\n\n<<<STAGE2_DYNAMIC_SECTION>>>\n\n"


def _build_stage2_prompt(domain_def: dict, attachment_texts: dict, is_dsl4: bool = False) -> str:
    name  = domain_def.get("domain_name", "NewDomain")
    snake = domain_def.get("snake_name") or _to_snake(name)

    attachment_section = ""
    for fname, content in attachment_texts.items():
        scope_note = _ATTACHMENT_SCOPE_NOTES.get(fname)
        scope_block = f"\n\n{scope_note}\n" if scope_note else ""
        attachment_section += f"\n\n---\n## 参考: {fname}\n{scope_block}\n{content}"

    if is_dsl4:
        impl_note = f"""- 実装方式: 4DSL準拠（Business DSL → Solver Input DSL → Solver Output DSL → UI DSL）
- converter: Backend/dsl_transformer/{snake}_converter.py を作成
- ui_converter: Backend/dsl_transformer/{snake}_ui_converter.py を作成
- solver: 問題構造に適した技術を選択すること。以下の3択から選び、貪欲法等の独自ヒューリスティックへの
  安易な逃避は避けること（ヒューリスティックはいずれの技術でも定式化できない場合の最終手段）。
    (a) IBM CPLEX CP Optimizer (docplex.cp.model) — interval_var/sequence_var/no_overlap等、
        時間軸に沿ったスケジューリング・割当構造に適する
    (b) IBM CPLEX（docplex.mp, CPLEX MIP/LP） — 施設配置・拠点選定・予算配分等、時間軸を
        持たない選択・割当構造の混合整数計画問題に適する。下記「参考」セクションおよび
        ドメイン定義中の technical_directives に docplex.mp / MIP の使用指示がある場合は
        必ずこちらを選ぶこと
    (c) 純Python — 上記いずれの定式化にも自然に収まらない場合のみ"""
    else:
        impl_note = """- 実装方式: 従来方式（dsl_transformer 非使用、レガシー）
- solver: IBM CPLEX CP Optimizer (docplex.cp.model)"""

    dsl4_files = f"""
===FILE:Backend/dsl_transformer/{snake}_converter.py===
（Business DSL → Solver Input DSL 変換。truck_dispatcher_converter.py を参考に実装）
===END===

===FILE:Backend/dsl_transformer/{snake}_ui_converter.py===
（Solver Output DSL → UI DSL 変換。truck_dispatcher_ui_converter.py を参考に実装）
===END===
""" if is_dsl4 else ""

    # 2026-08-11追加: 上記「UI文言の日英i18n対応」指示に対応するファイル。
    # is_dsl4のみ対象（パターン3/拡張登録・非dsl4レガシー方式は対象外）。
    i18n_messages_file = f"""
===FILE:Backend/i18n/{snake}_messages.py===
（i18nメッセージ辞書。上記「UI文言の日英i18n対応」の指示に従い、_JA/_EN辞書と
make_translator()でt(key, **params)を定義し、{snake}_solver.py・{snake}_ui_converter.py
の該当箇所から実際に呼び出すこと）
===END===
""" if is_dsl4 else ""

    converter_solver_contract = (
        _CONVERTER_SOLVER_CONTRACT.format(snake=snake, name=name) if is_dsl4 else ""
    )

    # 2026-07-20: 以前ここにあった「Backend/app.pyへ_solve_{{snake}}()を追加し
    # _DSL4_SOLVERSに登録する」PATCHブロックを削除した。is_dsl4ドメインは
    # 実際には_solve_4dsl_generic()の規約ベースルーターのみで動作しており、
    # かつ_patch_app_py_dsl4()側のマーカー不一致バグにより_DSL4_SOLVERSへの
    # 辞書登録は常に静かに失敗、関数定義だけがapp.pyに残る一方の状態になって
    # いた（MeetingRoom/StoreSiteで実際に発生・削除済み。docs/ENGINEERING_LOG.md
    # 2026-07-20参照）。今後の新規ドメイン登録で同じデッドコードを再生成しない
    # よう、このPATCHブロック自体を削除した。
    dsl4_patches = f"""
===PATCH:Backend/dsl_transformer/business_to_solver.py===
INSTRUCTION: convert_business_to_solver の 4DSLドメイン分岐ブロックに {name} のルートを追加
===CODE===
    if problem_class == "{name}":
        from .{snake}_converter import convert_{snake}_to_solver
        return convert_{snake}_to_solver(business_dsl)
===END===

===PATCH:Backend/dsl_transformer/solver_to_ui.py===
INSTRUCTION: convert_solver_to_ui の 4DSLドメイン分岐ブロックに {name} のルートを追加
===CODE===
    if problem_class == "{name}":
        from .{snake}_ui_converter import convert_{snake}_to_ui
        return convert_{snake}_to_ui(solver_output)
===END===
""" if is_dsl4 else ""
    # 2026-08-04: 以前ここにあった、is_dsl4=false（従来方式）ドメイン向けの
    # 「Backend/app.pyへ_solve_{{snake}}()を追加し_LEGACY_SOLVERSに登録する」
    # PATCHブロックを削除した。理由は2つ。
    # (1) 実際には app.py の _try_dynamic_solver()（solvers/{{snake}}_solver.py の
    #     {{ProblemClass}}Solver を命名規約ベースで自動検出・呼び出す既存の汎用
    #     フォールバック）が何もしなくても同じ結果を返すため、このPATCHは
    #     完全に不要な重複コードだった（is_dsl4=trueで2026-07-20に
    #     _solve_4dsl_generic()を理由に同種のPATCHを削除したのと同じ構図）。
    # (2) _patch_app_py_legacy()側のrfind()バグ（マーカー直後ではなくファイル内
    #     最後の"}"に新エントリを挿入してしまう）と組み合わさり、
    #     Backend/app.py自体にSyntaxErrorを書き込み、バックエンド再起動まで
    #     誰も気づけないという実害が発生した（SteelMillSlabDesign登録、
    #     2026-08-04。rfind()バグ自体とpy_compile安全網は別途修正済みだが、
    #     そもそも不要なPATCHを生成しなければこのバグの発生条件自体が無くなる）。
    # 詳細は OptiBuddy_V81_devnotes/ENGINEERING_LOG.md 2026-08-04参照。

    # 2026-07-17削除: 以前はここで StudioModeTabs.tsx / StudioShell.tsx に対して
    # 新ドメイン専用のタブ・ルーティングを追加するPATCHをLLMに生成させていたが、
    # 2026-07-11のリファクタでStudio側は既に「未知problem_classは全てGeneric4DSLView/
    # GENERIC_MODESに一本化する」設計に変わっており（TruckDispatcher/NurseShiftも
    # 専用タブ・専用Viewを持たない）、このPATCHは実際には不要だった。
    # それにもかかわらずapply_domain_files()の汎用PATCH適用フォールバック
    # （非Backend/非DSL4ファイル向けの `existing + "\\n\\n# ─── ... ───\\n" + append_code`）
    # がPythonの`#`コメント記法をそのまま.tsxファイルに追記しており、これがtsc型検証で
    # 確実に構文エラー（TS1127 Invalid character等）になり、新規ドメイン登録のapplyが
    # 毎回ロールバックされる原因になっていた（実機ログで確認・特定）。
    # 実際には何もパッチしなくてもGeneric4DSLViewが新ドメインを正しく表示するため、
    # このPATCH生成自体を削除する（{name}View.tsxの個別ビュー生成も同じ理由で削除）。

    # 静的パート: ドメイン名・snake_case名の埋め込みを一切含まない、全ドメイン共通の
    # 参考資料・禁止パターンのみで構成する（prompt cachingのプレフィックス対象）。
    static_part = f"""# OptiBuddy 新ドメイン実装 — 共通参考資料・実装ルール

以下は、どの業務ドメインを新規実装する場合にも共通する参考資料と禁止パターンです。
このあとに続く「新ドメイン実装依頼」の内容を実装する際に踏まえてください。

{_CPO_FORBIDDEN_PATTERNS}
{_I18N_MESSAGE_DICT_INSTRUCTION if is_dsl4 else ""}
{attachment_section}
"""

    # 動的パート: ドメイン固有の内容（毎回変わるためキャッシュ対象外）。
    # 2026-07-18: tightシナリオのFILEブロック・SCENARIOS登録を廃止。
    # baseline/infeasibleの2シナリオのみ生成させる。
    #
    # 2026-08-02追加: family_reference（同CSPLibファミリー内の既存実装の構造サマリ）は
    # domain_def内に混在させず、明示的に区切ったセクションとして提示する（「ドメイン定義」の
    # 一部＝仕様の一部と誤解されないようにするため。あくまで参考情報であり、本ドメイン自身の
    # 構造が兄弟ドメインと異なるならそれに従ってよい）。詳細は lookup_family_reference() および
    # 2026-08-05追加: structural_requirements（ヒアリングテキストのみから機械的に抽出した
    # 業務構造チェックリスト、詳細はderive_structural_requirements()docstring参照）。
    # family_reference/formulation_directiveと異なりCSPLib一致の有無に関わらず全ての
    # new_domain生成で存在しうる。「今回何を実装すべきか」の一次情報として、
    # family_reference/formulation_directive（存在する場合の補助的な技術選定情報）より
    # 前段に、かつ「必須順守」として提示する。
    structural_requirements = domain_def.pop("structural_requirements", None)
    if structural_requirements:
        def _bullets(items):
            items = items or []
            return "\n".join(f"- {i}" for i in items) or "（記載なし）"

        entities = structural_requirements.get("entities") or []
        entities_block = "\n".join(
            f"- {e.get('name','')}（{e.get('role','')}）: {e.get('notes') or '(補足なし)'}"
            for e in entities
        ) or "（記載なし）"

        structural_requirements_section = f"""
## 業務構造チェックリスト（必須順守・ヒアリング内容の構造化要約）

> ヒアリングテキストのみから機械的に抽出した、今回の業務構造の要約です。CSPLibの知識や
> CP/MIPの技術選定は含まれておらず、ヒアリングに書かれている内容の言い換えに過ぎません。
> 添付の参考資料（他ドメインのソースコード）と構造が食い違う場合は、必ず本セクションを
> 優先してください。ここに書かれている構造（特に「通常と異なる割り当て構造」「資源共有」
> 「目的の優先方法」）を単純化・省略しないこと。

### エンティティ
{entities_block}

### 通常と異なる割り当て構造
{structural_requirements.get('assignment_structure_notes') or '（特になし・通常の1対1割り当て）'}

### 絶対に守るべきルール
{_bullets(structural_requirements.get('hard_rules'))}

### できれば守りたいルール
{_bullets(structural_requirements.get('soft_rules'))}

### 目的の優先方法
種別: {structural_requirements.get('objective_priority_type') or 'unknown'}
{_bullets(structural_requirements.get('objective_stages'))}

### 資源の同時使用に関するルール
{structural_requirements.get('resource_sharing_notes') or '（記載なし）'}

### 対応しきれない場合・解けない場合の扱い
{structural_requirements.get('infeasible_handling_notes') or '（記載なし）'}

### その他の構造上の注意点
{_bullets(structural_requirements.get('other_structural_notes'))}
"""
    else:
        structural_requirements_section = ""

    # OptiBuddy_V81_devnotes/DESIGN_2026-08-02_family_structural_reference.md 参照。
    family_reference = domain_def.pop("family_reference", None)
    if family_reference:
        tp = family_reference.get("this_problem") or {}
        this_problem_block = f"""### 対象問題自身のCSPLib参照情報（{tp.get('csplib_id','')} {tp.get('title_ja','')}）
概要: {tp.get('summary_ja') or '(なし)'}
推奨解法: {tp.get('recommended_approach') or '(断定情報なし)'}
根拠: {tp.get('approach_note_ja') or '(なし)'}
"""
        siblings = family_reference.get("siblings", [])
        if siblings:
            sibling_blocks = "\n\n".join(
                f"### 兄弟: {s.get('csplib_id')} {s.get('title_ja')} → {s.get('optibuddy_domain')}"
                f"（採用技術: {s.get('recommended_approach') or '不明'}）\n{s.get('structure_summary')}"
                for s in siblings
            )
        else:
            sibling_blocks = "（同ファミリー内に登録済みの兄弟ドメインはまだありません）"
        family_reference_section = f"""
## 参考情報: CSPLibファミリー構造（{family_reference.get('family_name_ja') or ''}、強制ではない）

> {family_reference.get('note', '')}

{this_problem_block}
{sibling_blocks}
"""
    else:
        family_reference_section = ""

    # 2026-08-03追加: formulation_directive（執筆型、Phase4）。family_referenceは
    # 「参考情報・強制ではない」と明示するのに対し、formulation_directiveは
    # ヒアリング担当者（本エージェント）がヒアリング内容とfamily_referenceを踏まえて
    # 明示的に確定させた技術的決定であるため、「必須順守」として別枠で強く提示する。
    formulation_directive = domain_def.pop("formulation_directive", None)
    if formulation_directive:
        constraints = formulation_directive.get("constraints") or []
        constraints_block = "\n".join(f"- {c}" for c in constraints) or "（記載なし）"
        formulation_directive_section = f"""
## 技術指示（必須順守・formulation_directive）

> ヒアリング担当者がヒアリング内容を精査して確定させた、この業務固有の正しい定式化です。
> 下記の参考情報（同ファミリーの実装例）と矛盾する場合でも、本セクションの内容を優先してください。

決定変数: {formulation_directive.get('decision_variables', '(記載なし)')}
制約:
{constraints_block}
目的関数の型: {formulation_directive.get('objective_type', '(記載なし)')}
目的関数: {formulation_directive.get('objective_expression') or '(記載なし)'}
採用技術: {formulation_directive.get('recommended_technology', '(記載なし)')}
根拠: {formulation_directive.get('rationale') or '(記載なし)'}
"""
    else:
        formulation_directive_section = ""

    dynamic_part = f"""# 新ドメイン実装依頼（V4.4）

## ドメイン定義
{json.dumps(domain_def, ensure_ascii=False, indent=2)}
{structural_requirements_section}
{formulation_directive_section}
{family_reference_section}
## 実装方針
- problem_class: "{name}"
- domain (snake_case): "{snake}"
{impl_note}

{converter_solver_contract}
## 出力形式（マーカー区切り・全ファイルを出力すること）

===FILE:Backend/solvers/{snake}_solver.py===
（完全なソルバー実装。クラス名は {name}Solver とすること）
===END===
{dsl4_files}
{i18n_messages_file}
===FILE:Backend/dsl_repository/scenarios/{snake}_baseline.json===
（標準シナリオJSON。必ず problem_class="{name}", domain="{snake}" を含めること）
===END===

===FILE:Backend/dsl_repository/scenarios/{snake}_infeasible.json===
（実行不可能シナリオJSON。必ず problem_class="{name}", domain="{snake}" を含めること）
===END===

{dsl4_patches}
===SCENARIOS===
[
  {{"name": "{name} — 標準",   "description": "{name}の標準シナリオ", "tag": "{name[:6].upper()}", "tag_color": "#8b5cf6", "domain": "{snake}", "file": "Backend/dsl_repository/scenarios/{snake}_baseline.json"}},
  {{"name": "{name} — 解なし", "description": "{name}のInfeasibleシナリオ", "tag": "{name[:6].upper()}", "tag_color": "#ef4444", "domain": "{snake}", "file": "Backend/dsl_repository/scenarios/{snake}_infeasible.json"}}
]
===END===
"""

    return static_part + _STAGE2_CACHE_SPLIT + dynamic_part


# ─────────────────────────────────────────────────────────────
# Stage 2: ファイル生成・適用
# ─────────────────────────────────────────────────────────────

def _parse_marker_output(raw: str) -> dict:
    files, patches = [], []
    scenario_registrations = []
    for m in re.finditer(r'===FILE:(.+?)===\n(.*?)===END===', raw, re.DOTALL):
        content = m.group(2)
        if content.endswith('\n'): content = content[:-1]
        files.append({"path": m.group(1).strip(), "content": content})
    for m in re.finditer(
        r'===PATCH:(.+?)===\n(?:INSTRUCTION:\s*(.+?)\n)?===CODE===\n(.*?)===END===',
        raw, re.DOTALL
    ):
        append_code = m.group(3)
        if append_code.endswith('\n'): append_code = append_code[:-1]
        patches.append({"path": m.group(1).strip(), "instruction": (m.group(2) or "").strip(),
                        "append_code": append_code, "approved": True})
    m = re.search(r'===SCENARIOS===\n(.*?)===END===', raw, re.DOTALL)
    if m:
        try:
            scenario_registrations = json.loads(m.group(1).strip())
        except json.JSONDecodeError as e:
            logger.warning(f"[parse_marker] SCENARIOS パース失敗: {e}")
    logger.info(f"[parse_marker] files={len(files)}, patches={len(patches)}, scenarios={len(scenario_registrations)}")
    return {"files": files, "patches": patches, "scenario_registrations": scenario_registrations}


def _build_scenario_registrations_from_diffs(approved_diffs: list) -> list:
    # 2026-07-18: tightを廃止（baseline/infeasibleの2シナリオ原則）。
    # 既存ドメインのtightファイルがdiffsに混ざっていても単に無視されるだけで、
    # 過去の登録・DB行には影響しない（このヘルパーは新規/追加登録時のみ使用）。
    registrations = []
    suffix_label = {"baseline": "— 標準", "infeasible": "— 解なし"}
    suffix_color = {"baseline": "#8b5cf6", "infeasible": "#ef4444"}
    for item in approved_diffs:
        path    = item.get("path", "")
        content = item.get("new_content", "")
        m = re.match(r"Backend/dsl_repository/scenarios/(.+)_(baseline|infeasible)\.json$", path)
        if not m:
            continue
        snake  = m.group(1)
        suffix = m.group(2)
        try:
            dsl_json      = json.loads(content)
            problem_class = dsl_json.get("problem_class", _to_pascal(snake))
            label         = f"{problem_class} {suffix_label[suffix]}"
            registrations.append({
                "name":        label,
                "description": f"{problem_class} の{suffix_label[suffix].strip('— ')}ケース",
                "tag":         problem_class[:6].upper(),
                "tag_color":   suffix_color[suffix],
                "domain":      snake,
                "file":        path,
            })
        except Exception as e:
            logger.warning(f"[fallback] シナリオJSON パース失敗: {path} — {e}")
    if registrations:
        logger.info(f"[fallback] approved_diffs から {len(registrations)} 件構築")
    return registrations


def generate_domain_files(domain_def: dict, attachment_overrides: dict = None) -> dict:
    from llm.llm_client import upload_repomix_if_changed, call_llm_with_file, call_llm, LLM_PROVIDER

    domain_name  = domain_def.get("domain_name", "NewDomain")
    # 2026-08-04: classify_problem()の is_dsl4_candidate をそのまま使わず、
    # 常に4DSL準拠（converter/ui_converter/solverの3点セット）で生成するよう固定した。
    # 背景: Stage1aプロンプト自身が「新規ドメインは原則true（4DSLが現行の標準
    # アーキテクチャ）」と明記しているにもかかわらず、is_dsl4_candidate=falseと
    # 判定されるケース（SteelMillSlabDesign、2026-08-04）が実際に発生した。
    # false側の「従来方式（レガシー）」はconverter/ui_converterを生成しないため
    # ui_dslが常に空になり、Overview画面が「UI DSLデータがありません」になる
    # （ソルバー自体は正しく動いていても画面に何も出ない、実害あり）。
    # コード上にfalseを正当化する明確な基準も見当たらなかったため、
    # is_dsl4_candidateの値に関わらず常にTrueとして扱うことにした
    # （詳細はOptiBuddy_V81_devnotes/ENGINEERING_LOG.md 2026-08-04参照）。
    # classification自体のis_dsl4_candidateフィールドはログ・記録用にそのまま残す。
    is_dsl4      = True
    exists_check = check_domain_exists(domain_name)

    attachments = dict(DEFAULT_ATTACHMENTS)
    if attachment_overrides:
        for key, val in attachment_overrides.items():
            if val is None: attachments.pop(key, None)
            else: attachments[key] = val

    repomix_path     = attachments.pop("repomix-domain-addition.xml", None)
    attachment_texts = {}
    for fname, fpath in attachments.items():
        try:
            attachment_texts[fname] = Path(fpath).read_text(encoding="utf-8")
        except Exception as e:
            logger.warning(f"添付ファイル読み込み失敗 {fpath}: {e}")

    prompt = _build_stage2_prompt(domain_def, attachment_texts, is_dsl4=is_dsl4)
    system = "あなたはOptiBuddyの開発アシスタントです。指定されたマーカー形式のみで出力。説明文不要。"

    if _STAGE2_CACHE_SPLIT in prompt:
        static_text, dynamic_text = prompt.split(_STAGE2_CACHE_SPLIT, 1)
    else:
        static_text, dynamic_text = "", prompt

    if LLM_PROVIDER == "anthropic" and static_text.strip():
        # 静的パート（全ドメイン共通の参考資料・禁止パターン）を独立したcontentブロックとして
        # cache_control付きで送る。動的パート（ドメイン固有内容）は別ブロックでキャッシュ対象外。
        user_content = [
            # 2026-08-04: デフォルトTTLが5分に変更されたため明示的に1hへ延長
            # （詳細はllm_client.py _cacheable_system のコメント参照）。
            {"type": "text", "text": static_text, "cache_control": {"type": "ephemeral", "ttl": "1h"}},
            {"type": "text", "text": dynamic_text},
        ]
    else:
        # 非Anthropicプロバイダー、または静的パートが空の場合は従来通り1本の文字列。
        user_content = static_text + dynamic_text

    messages = [{"role": "user", "content": user_content}]

    # 2026-08-05変更: 16000→24000に引き上げ。structural_requirements（業務構造チェックリスト）
    # セクション追加によりStage2の出力が伸び、PatientTransportPlanner再登録実機テストで
    # output=16000ちょうどで打ち切られ、SCENARIOSブロック（baseline/infeasible JSON・
    # ファイル登録用パッチ）が生成されないまま終わる不具合が発生した（ENGINEERING_LOG.md
    # 2026-08-05追記12参照）。converter/solver/ui_converterのファイル本体を出力し切った後に
    # SCENARIOSブロックが来る出力順のため、打ち切りは静かな「一部ファイルだけ書き込まれ、
    # 登録は完了しない」という気づきにくい不具合になる。
    if LLM_PROVIDER == "anthropic" and repomix_path and Path(repomix_path).exists():
        file_id = upload_repomix_if_changed()
        raw = call_llm_with_file(messages, file_id, system=system, max_tokens=24000)
    else:
        raw = call_llm([{"role": "system", "content": system}] + messages, max_tokens=24000)

    parsed = _parse_marker_output(raw)
    diffs  = [compute_diff(f["path"], f["content"]) for f in parsed["files"] if f.get("path")]
    return {"status": "ok", "diffs": diffs, "patches": parsed["patches"],
            "scenario_registrations": parsed["scenario_registrations"], "exists_check": exists_check}


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


def _patch_converter_dispatch(content: str, if_block_code: str, marker: str) -> str:
    """
    business_to_solver.py / solver_to_ui.py の 4DSL分岐ブロックに
    'if problem_class == "Xxx":' ブロックを挿入する。
    マーカーが見つかればその直前に挿入。
    見つからなければ CapacitatedVehicleRoutingProblem の ifブロックの直前に挿入。
    """
    # problem_class を if文から抽出して重複チェック
    m = re.search(r'if problem_class == "([^"]+)"', if_block_code)
    if m and f'if problem_class == "{m.group(1)}"' in content:
        return content  # 既登録

    indent = "    "  # convert_ 関数の内部はインデント、4スペース
    block = "\n".join(indent + line if line.strip() else line for line in if_block_code.strip().splitlines())
    block = "\n" + block + "\n"

    if marker in content:
        content = content.replace(marker, block + "\n" + marker, 1)
        return content

    # フォールバック: CapacitatedVehicleRoutingProblem の ifブロック直前
    cvrp_marker = '    if problem_class == "CapacitatedVehicleRoutingProblem":'
    if cvrp_marker in content:
        content = content.replace(cvrp_marker, block + "\n" + cvrp_marker, 1)
        return content

    # 最終フォールバック: 関数末尾の return の直前
    return content + "\n\n# ─── Auto-generated by domain_generator ───\n" + if_block_code + "\n"


def _patch_app_py_legacy(content: str, solver_fn_code: str) -> str:
    legacy_marker = "_LEGACY_SOLVERS = {"
    if legacy_marker in content:
        content = content.replace(legacy_marker, solver_fn_code + "\n\n" + legacy_marker, 1)
    fn_match = re.search(r'def (_solve_(\w+))\(dsl, issue_statuses\)', solver_fn_code)
    if fn_match:
        fn_name   = fn_match.group(1)
        snake     = fn_match.group(2)
        pascal    = _to_pascal(snake)
        new_entry = f'    "{pascal}": {fn_name},'
        # 2026-08-04修正: rfind()はcontent全体（マーカーより後ろの数千行）から
        # 「最後」の"}"を探すため、_LEGACY_SOLVERS = {} 自身の閉じ括弧ではなく、
        # ファイル末尾付近の無関係な"}"（例: 別関数のjsonify(...)}), 500）に
        # マッチしてしまい、そこに新エントリを誤挿入してSyntaxErrorを起こす
        # 実害が実機で確認された（SteelMillSlabDesign登録、ENGINEERING_LOG
        # 2026-08-04参照）。_LEGACY_SOLVERS = {} は空dict即閉じなので、
        # マーカー直後の最初の"}"（=find()）が正しい閉じ括弧の位置になる。
        idx = content.find("}", content.find("_LEGACY_SOLVERS = {"))
        if idx > 0:
            content = content[:idx] + new_entry + "\n" + content[idx:]
    return content


def _patch_home_screen_tag_map(problem_class: str, snake: str, tag_color: str) -> None:
    """
    HomeScreenNew.tsx の TAG_MAP に新規ドメインエントリを自動追加する。
    既に登録済みの場合はスキップ。重複キーがあれば削除してから追加。
    """
    tsx_path = _resolve_path("Frontend/src/app/HomeScreenNew.tsx")
    if not tsx_path.exists():
        logger.warning("[tag_map] HomeScreenNew.tsx が見つかりません")
        return

    content = tsx_path.read_text(encoding="utf-8")

    # TAG_MAP の範囲を特定
    tag_map_start = content.find("const TAG_MAP:")
    tag_map_end = content.find("};", tag_map_start)
    if tag_map_start == -1 or tag_map_end == -1:
        logger.warning("[tag_map] TAG_MAP が見つかりません")
        return

    tag_map_section = content[tag_map_start:tag_map_end + 2]
    
    # 既存の同名キーを全て削除（重複対策）
    # 正規表現で problem_class のキー定義行を検出して削除
    pattern = rf'^\s*{re.escape(problem_class)}:\s*\{{[^}}]+\}},?\s*$'
    lines = tag_map_section.split('\n')
    filtered_lines = []
    removed_count = 0
    
    for line in lines:
        if re.match(pattern, line, re.MULTILINE):
            removed_count += 1
            logger.info(f"[tag_map] 重複キー削除: {line.strip()}")
        else:
            filtered_lines.append(line)
    
    if removed_count > 0:
        tag_map_section = '\n'.join(filtered_lines)
        content = content[:tag_map_start] + tag_map_section + content[tag_map_end + 2:]

    # 新しいエントリを追加
    tag   = problem_class[:6].upper()
    entry = (
        f'  {problem_class}:'
        f' {{ tag: \'{tag}\', tagColor: \'{tag_color}\','
        f' domain: \'{snake}\' }},\n'
    )

    # マーカーコメントの直前に挿入
    marker = "  // ↓ 自動登録ドメインはここに自動追加される（domain_generator.py が管理）\n"
    if marker in content:
        content = content.replace(marker, entry + marker)
        tsx_path.write_text(content, encoding="utf-8")
        logger.info(f"[tag_map] TAG_MAP に追加: {problem_class} (重複削除: {removed_count}件)")
    else:
        logger.warning(f"[tag_map] マーカーが見つかりません。手動で追加してください: {problem_class}")


def _update_domain_registry_from_diffs(approved_diffs: list):
    for item in approved_diffs:
        path = item.get("path", "")
        m = re.match(r"Backend/dsl_repository/scenarios/(.+)_baseline\.json$", path)
        if m:
            snake  = m.group(1)
            pascal = _to_pascal(snake)
            if pascal not in EXISTING_DOMAINS:
                EXISTING_DOMAINS[pascal] = str(_resolve_path(path))
                logger.info(f"[domain_registry] EXISTING_DOMAINS に追加: {pascal}")


def _to_pascal(snake: str) -> str:
    return "".join(w.capitalize() for w in snake.split("_"))


# ─────────────────────────────────────────────────────────────
# DB登録
# ─────────────────────────────────────────────────────────────

def _register_scenarios(scenario_registrations: list) -> list:
    logger.info(f"[register] _register_scenarios 呼び出し: {len(scenario_registrations)} 件")
    if not scenario_registrations:
        logger.warning("[register] scenario_registrations が空のため終了")
        return []
    registered = []
    seen_problem_classes: dict[str, tuple[str, dict]] = {}  # pc -> (description, sample_dsl_json)
    try:
        from dsl_repository.repository import DslRepository
        repo = DslRepository()
        # name -> {id, dsl_json} のマップ（2026-07-18d追記: 従来は名前集合だけを
        # 見て「既存なら無条件skip」していたため、登録後にシナリオJSONファイルを
        # 修正して再登録を実行しても、DB側のdsl_jsonカラムが一度書き込まれたまま
        # 永遠に更新されない不具合があった（MeetingRoomのsame_dept_same_room_bonus
        # 修正がDBに反映されず、-27という不正な目的値が再実行後も直らなかった件）。
        # ファイルが「正」でDBはそのミラーであるべきという原則に合わせ、名前が
        # 既存でも内容が食い違っていればupdate_scenario()で同期する。
        existing_by_name = {s["name"]: s for s in repo.list_scenarios()}
        for reg in scenario_registrations:
            abs_file = _resolve_path(reg.get("file", ""))
            if not abs_file.exists():
                logger.warning(f"[register] ファイルなし: {abs_file}")
                registered.append({"name": reg["name"], "status": "skipped_no_file"}); continue

            # dsl_jsonの読み込みとseen_problem_classesへの追跡は、シナリオ名が既登録
            # でも必ず行う（V4.6修正: 以前はskipped_duplicateの場合ここより先でcontinueしていたため、
            # 既に全シナリオが登録済みのドメインを再登録実行してもdsl_definitions.schema_jsonを
            # 更新する機会が一切なく、HospitalShiftPlannerでschema_jsonが{}のまま放置されていた
            # 不具合の根本原因だった）。
            dsl_json = json.loads(abs_file.read_text(encoding="utf-8"))
            pc = dsl_json.get("problem_class", "")
            if pc and pc not in seen_problem_classes:
                # baselineシナリオを優先してスキーマ推定のサンプルに使う（一番具体的で完全な形のことが多いため）
                seen_problem_classes[pc] = (reg.get("description", ""), dsl_json)
            elif pc in seen_problem_classes and reg.get("file", "").endswith("_baseline.json"):
                prev_desc, _ = seen_problem_classes[pc]
                seen_problem_classes[pc] = (prev_desc, dsl_json)

            existing = existing_by_name.get(reg["name"])
            if existing is not None:
                if existing["dsl_json"] != dsl_json:
                    repo.update_scenario(scenario_id=existing["id"], dsl_json=dsl_json)
                    registered.append({"name": reg["name"], "id": existing["id"], "status": "synced_from_file"})
                    logger.info(f"[register] シナリオ再同期（内容差分検出）: {reg['name']} → ID={existing['id']}")
                else:
                    registered.append({"name": reg["name"], "status": "skipped_duplicate"})
                continue

            sid = repo.create_scenario(
                name=reg.get("name",""), description=reg.get("description",""),
                tag=reg.get("tag","CUSTOM"), tag_color=reg.get("tag_color","#888888"),
                domain=reg.get("domain",""), dsl_json=dsl_json)
            registered.append({"name": reg["name"], "id": sid, "status": "created"})
            existing_by_name[reg["name"]] = {"id": sid, "dsl_json": dsl_json}
            logger.info(f"[register] シナリオ登録: {reg['name']} → ID={sid}")

        for pc, (desc, sample_dsl) in seen_problem_classes.items():
            _ensure_dsl_definition(repo, pc, desc, sample_dsl_json=sample_dsl)

    except Exception as e:
        logger.error(f"[register] DB登録エラー: {e}", exc_info=True)
    return registered


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


def _to_snake(name: str) -> str:
    return re.sub(r"([A-Z])", r"_\1", name).lower().lstrip("_")

def _resolve_path(relative_path: str) -> Path:
    return _PROJECT_ROOT / relative_path

def get_default_attachment_info() -> list:
    result = []
    for name, path in DEFAULT_ATTACHMENTS.items():
        p = Path(path)
        result.append({"name": name, "path": path, "exists": p.exists(),
                        "size": p.stat().st_size if p.exists() else 0})
    return result
