
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
    EXISTING_DOMAINS,
    _to_snake,
    logger,
)
from .build_checks import (
    _ensure_repomix,
)
from .family_reference import (
    _extract_formulation_directive,
    derive_structural_requirements,
    lookup_family_reference,
)
from .stage1a import (
    _CP_TOKEN_RE,
    _MIP_TOKEN_RE,
    check_axis_a_fit,
    check_domain_exists,
    classify_problem,
    detect_extension_gaps,
)
from .stage1b import (
    _validate_scenario_capacity,
    generate_scenarios_from_schema,
)




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
