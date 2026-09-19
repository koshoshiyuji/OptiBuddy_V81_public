

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

bp = Blueprint("routes_domain_registration", __name__)




def _run_domain_job(job_id: str, domain_name: str, hearing_texts: list, overrides,
                     base_domain_override: str | None = None, force_new_domain: bool = False) -> None:
    """/api/domain/run のスレッドターゲット。実体は_run_hearing_pipeline()。"""
    _run_hearing_pipeline(job_id, domain_name, hearing_texts, overrides,
                          base_domain_override=base_domain_override, force_new_domain=force_new_domain)


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
        # 2026-09-19追加（Koshoshi合意）: humanizeを経ない生のblocking_questionsも
        # missing_infoと同じ並びでjob状態に保持しておく。missing_infoは元々
        # humanize対象外（ヒアリングギャップの機械的な文言）のためそのまま使い回す。
        # _run_confirm_job側でrun_debug_agent()に渡す際、questionsではなく
        # こちらを使う（詳細はgate2.pyのblocking_questions_raw追加コメント参照）。
        all_questions_raw = list(missing_info) + gate2_result["blocking_questions_raw"]
        if all_questions:
            _job_set(job_id, stage="needs_confirmation", questions=all_questions,
                questions_raw=all_questions_raw,
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

    # 2026-08-28追記2（Koshoshi合意）: debug_agentがstopped_reason="max_turns"で
    # 終了した場合、needs_human_decisionには「ターン切れで検証できないまま終了した」
    # という自己申告メモ（debug_agent.py側のmax_turnsフォールバック文言）が入る。
    # しかしこの直後に実行するrun_gate2_checks（静的＋動的検証を実際にやり直す、
    # debug_agent自身のverify_gate2呼び出し有無に依存しない権威的な再検証）が
    # blocking_questions=0件と判定した場合、それはdebug_agent自身の「ターン切れで
    # 未確認」という自己申告よりも信頼できる「実際に直っている」という証拠である。
    # 実機で、debug_agentが5ターン全てを読み込み・修正に使い切りverify_gate2を
    # 一度も呼べなかったが、その後のrun_gate2_checksではblocking=0件だったケースを
    # 確認した（2026-08-28）。この場合に「未解決」「検証未実施」と表示するのは
    # 誤解を招くため、ここでは自己申告メモを画面表示から外す。
    # 一方、stopped_reason=="done"でreport_doneが明示的に返すneeds_human_decision
    # （業務判断が必要な指摘。例:「この設計判断でよいか確認してください」）は、
    # Gate2の静的・動的検証では検知できない性質のものなので、blocking件数に
    # 関わらず常に残す。
    agent_needs_human_decision = agent_result["needs_human_decision"]
    if agent_result["stopped_reason"] == "max_turns" and not gate2_result["blocking_questions"] \
            and agent_needs_human_decision:
        logger.info(
            f"[confirm_job:{job_id}] debug_agentはターン切れで自己検証できませんでし"
            f"たが、Gate2再検証はblocking=0件だったため、ターン切れの自己申告メモ"
            f"{len(agent_needs_human_decision)}件は、業務ユーザー向けの結論に置き換えます: "
            f"{agent_needs_human_decision}"
        )
        agent_needs_human_decision = []
        # 2026-08-29追加（Koshoshi合意）: 単に消すのではなく、「AIが確認済みで問題なし」
        # という肯定的な1行に置き換えて完了画面のGate2警告欄に残す。この分岐が
        # 発動する時点でblocking_questionsも空なので、この後needs_human_gateは必ず
        # Falseになり、人間の確認を挟まず自動登録される（取りやめ／登録の
        # 選択自体を出さない）。「聞く必要のないことは1箇所（ここ）で
        # 解決し、業務ユーザーには内部の仕組み（ターン数・verify_gate2等）を
        # 見せない」というKoshoshiの方針。
        # 2026-09-07修正（Koshoshi合意・実機で矛盾表示を確認）: この直後、
        # gate2_result["advisory_questions"]（静的チェックの参考情報。それぞれ
        # 独自の【推奨】判定を持つ）も同じgate2_warningsに追加される。advisory_
        # questionsが残っている場合にもこの「特に対応不要」という一般論を無条件に
        # 出すと、「対応不要」と「（参考情報側の）修正をお勧めします」が同じ画面に
        # 同時に表示され、結局どちらに従えばよいか分からない矛盾表示になる
        # （2026-09-07実機確認: instance_name/note未使用の指摘と共存した実例）。
        # advisory_questionsが他に残っている場合は、この一言を出さず、各advisory
        # 項目自身の【推奨】判定に判断を委ねる。
        if not gate2_result["advisory_questions"]:
            gate2_warnings.append(
                "✅ 自動チェックの結果、この内容のままお使いいただけます。"
                "特にご対応いただくことはありません。"
            )

    needs_human_gate = bool(gate2_result["blocking_questions"]) or bool(agent_needs_human_decision)

    if needs_human_gate:
        # 2026-07-19: 以前は「（人間の判断が必要）」という接頭辞だったが、
        # この画面を見るのは常に人間（「AIの判断」と対比する意味での「人間」）
        # なので、あえて「人間」と言う意味が無いとKoshoshiより指摘。加えて、
        # ここは取りやめ／このまま登録するの二択しか提示しない画面であり、
        # 「判断してください」という言い方は個別に回答できるかのような
        # 誤解を招く。「未解決のまま残っている」という事実の表示にとどめる。
        human_decision_questions = [
            f"（未解決）{item}" for item in agent_needs_human_decision
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
# 2026-08-28追記（Koshoshi合意）: 接頭辞の文言をプレーンな日本語に変更。
# Backend/domain_generator.py の blocking_questions 組み立てと
# Frontend/src/app/studio/components/RegisterModal.tsx の
# DYNAMIC_STRUCTURAL_PREFIXES を必ず同じ文字列に保つこと。
_DYNAMIC_STRUCTURAL_PREFIXES = (
    "（プログラムのエラーで停止・要修正）",
    "（実際に解いてみた結果が想定と違いました）",
)


def _run_confirm_job(job_id: str, domain_name: str, pending: dict, questions: list, answers: str,
                      force_apply: bool = False, dynamic_override: bool = False) -> None:
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

        # 2026-09-19追加（Koshoshi合意）: debug_agentへ渡すquestionsだけ、
        # humanize済みのquestions引数ではなくjob状態のquestions_raw
        # （無ければquestionsにフォールバック）を使う。詳細は
        # gate2.pyのblocking_questions_raw追加コメント参照。
        _job_for_raw_questions = _job_get(job_id) or {}
        questions_raw = _job_for_raw_questions.get("questions_raw") or questions

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
                # 2026-09-07追加（Koshoshi合意）: 「動的検証由来の指摘は無条件に
                # force_applyで握りつぶせない」という非交渉の原則そのものは変えない。
                # ただし、人間が実際にコード・シナリオを確認した上で「これは実装
                # バグではなく誤検知/設計上許容できる」と明示的に判断したケースに、
                # 正式に登録を通すルートが存在しなかった（実務上のギャップとして
                # 2026-09-07に実機テストで判明）。force_apply単体では従来通り通さず、
                # 別フラグdynamic_overrideと、理由（answers、空文字不可）の両方が
                # 揃った場合のみ、人間の個別・明示判断として通す。理由はGate2再検証を
                # スキップした事実と共に監査ログ・完了画面に必ず残す（隠さない）。
                if dynamic_override and answers.strip():
                    logger.warning(
                        f"[confirm_job:{job_id}] dynamic_override=True: 動的検証由来の"
                        f"指摘{len(_blocked_dynamic)}件について、人間が個別に確認・"
                        f"承認した上で登録します。理由: {answers.strip()!r} / 指摘内容: {_blocked_dynamic}"
                    )
                else:
                    logger.warning(
                        f"[confirm_job:{job_id}] force_apply=True だが、Gate2動的検証由来の"
                        f"指摘が{len(_blocked_dynamic)}件残っているため拒否しました"
                        f"（dynamic_override={dynamic_override}, 理由記入={bool(answers.strip())}）: {_blocked_dynamic}"
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
            # （静的field-check由来: 「（入力項目の反映漏れの疑い）」「（数値のざっくり
            # 近似に関する指摘）」「（設定項目の反映漏れの疑い）」等。動的検証由来の
            # 構造的指摘: 「（プログラムのエラーで停止・要修正）」＝実行時例外、
            # 「（実際に解いてみた結果が想定と違いました）」＝feasible不一致・退化解
            # 検知等。2026-08-28: 接頭辞をプレーンな日本語ラベルに変更済み）。
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
            # 2026-09-19追加: 上と同じ絞り込みをquestions_raw側にも並行して適用する
            # （件数・並び順はquestionsと常に一致する設計。gate2.py参照）。
            non_dynamic_questions_raw = [
                q for q in questions_raw
                if not (isinstance(q, str) and q.startswith(_DYNAMIC_STRUCTURAL_PREFIXES))
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
            # 2026-08-28追記（Koshoshi合意）: 「人間からの回答・指示」欄に何か
            # 書かれている場合は、動的検証由来の指摘もエージェントの正式なquestions
            # に含める。従来はhuman_notes（下記notes_block）には常に全文を渡して
            # いたのに、questions（エージェントに割り当てられた「対応すべき指摘」）
            # からは動的指摘を常に除外していたため、人間が動的指摘に対する答え
            # （例: 「割り当ては必須制約とし、infeasibleとして返してください」）を
            # 明示的に書いても、エージェントの正式なゴールにはならず、Gate2再検証
            # （動的指摘の解消確認）まで踏み込まない一因になっていた（実機:
            # ReviewDocument登録で確認）。回答欄が空の場合は、人間の診断を経ずに
            # 動的指摘をエージェントに丸投げしない、という従来の設計原則を維持する。
            agent_questions = questions if (answers or "").strip() else (
                non_dynamic_questions if dynamic_structural_questions else questions
            )
            # 2026-09-19追加（Koshoshi合意）: debug_agentに実際に渡す内容だけは、
            # 上のagent_questions（humanize済み・件数/絞り込みロジック決定用）
            # ではなくagent_questions_raw（生の技術情報）を使う。絞り込みの
            # 「する/しない」判断自体（dynamic_structural_questions等）は
            # 一切変更しない。
            agent_questions_raw = questions_raw if (answers or "").strip() else (
                non_dynamic_questions_raw if dynamic_structural_questions else questions_raw
            )
            logger.info(f"[confirm_job:{job_id}] 続行: デバッグエージェント開始"
                        f"（指摘{len(agent_questions)}件、対象ファイル{len(written_paths)}件、"
                        "questionsは生の技術情報を使用）")
            agent_result = run_debug_agent(
                questions=agent_questions_raw, written_paths=written_paths,
                domain_name=domain_name, hearing_texts=hearing_texts_for_check,
                snake_name=snake,
                human_notes=answers,
                should_stop=lambda: bool((_job_get(job_id) or {}).get("interrupt_requested")),
                # 2026-08-10: 段階B A/Bテストでトークン-19%・所要時間-5%を確認
                # （Koshoshi承認、詳細はENGINEERING_LOG.md 2026-08-10追記4）。
                # 段階C（本番切替）として有効化。
                summarize_stale_reads=True,
                # 2026-09-19追加（Koshoshi合意）: auto_resolveモードのジョブでは
                # ask_humanも自動代行する（debug_agent.py側の実装参照）。
                auto_resolve=bool((_job_get(job_id) or {}).get("auto_resolve", False)),
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


# ---------------------------------------------------------
# ★ V8.4: ドメイン自動生成エンドポイント
# ---------------------------------------------------------

@bp.route("/api/domain/template", methods=["GET"])
def domain_template():
    try:
        template_path = _PROJECT_ROOT / "docs" / "hearing_template.md"
        if not template_path.exists():
            return jsonify({"status": "error", "message": "テンプレートファイルが見つかりません"}), 404
        return send_file(str(template_path), mimetype="text/markdown",
                         as_attachment=True, download_name="hearing_template.md")
    except Exception as e:
        return jsonify({"status": "error", "message": str(e)}), 500


@bp.route("/api/domain/attachments", methods=["GET"])
def domain_attachments():
    try:
        from domain_generator import get_default_attachment_info
        return jsonify({"status": "ok", "attachments": get_default_attachment_info()})
    except Exception as e:
        return jsonify({"status": "error", "message": str(e)}), 500


@bp.route("/api/domain/check/<domain_name>", methods=["GET"])
def domain_check(domain_name: str):
    try:
        from domain_generator import check_domain_exists
        return jsonify(check_domain_exists(domain_name))
    except Exception as e:
        return jsonify({"status": "error", "message": str(e)}), 500


@bp.route("/api/domain/interpret", methods=["POST"])
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


@bp.route("/api/domain/generate", methods=["POST"])
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


@bp.route("/api/domain/apply", methods=["POST"])
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


# ============================================================
# 自動操縦（auto_resolve）モード（2026-09-19新設、Koshoshi合意）
# ============================================================
# 「業務担当者は最終登録承認のみ行う」機能。既存の_run_hearing_pipeline /
# _advance_after_agent_round / _run_confirm_job / run_debug_agentの内部ロジックは
# 一切変更せず（過去に何度もこの周辺の変更で実害のあるバグを出しているため、
# 意図的に触れない）、外側からジョブ状態をポーリングして、人間が画面から行う
# 操作（/api/domain/confirm・/api/domain/cancel相当）を自動で代行するだけの
# 「自動操縦」スレッドとして実装する。

def _auto_decide_confirm_action(
    questions: list, pending: dict, hearing_texts: list, domain_name: str,
    allow_continue: bool = True,
) -> dict:
    """
    needs_confirmation中の指摘に対し、業務担当者に代わって次のアクション
    （continue/force_apply/cancel）をOptiBuddy自身のLLM呼び出しで判断する。
    Chrome/Coworkは一切介在しない。

    動的検証由来の指摘（_DYNAMIC_STRUCTURAL_PREFIXES）が残っている場合は、
    既存のforce_apply拒否ガードレール（force_apply側の実装参照）と矛盾しない
    よう、force_applyを選択肢から機械的に除外する。allow_continue=Falseの
    場合（自動続行の上限到達後の「推奨だけ添える」用途）はcontinueも除外する。

    戻り値: {"action": "continue"|"force_apply"|"cancel", "reasoning": str}
    失敗時は安全側（登録しない）のcancelにフォールバックする。
    """
    from llm.llm_client import call_llm_json

    has_dynamic = any(
        isinstance(q, str) and q.startswith(_DYNAMIC_STRUCTURAL_PREFIXES) for q in questions
    )
    allowed_actions = []
    if allow_continue:
        allowed_actions.append("continue")
    if not has_dynamic:
        allowed_actions.append("force_apply")
    allowed_actions.append("cancel")

    diffs_block = "\n\n".join(
        f"### {d.get('path','')}\n```\n{(d.get('new_content','') or '')[:4000]}\n```"
        for d in (pending.get("diffs") or [])[:5]
    ) or "（コード差分なし）"
    hearing_block = "\n\n---\n\n".join(hearing_texts) if hearing_texts else "（ヒアリング内容なし）"
    questions_block = "\n".join(f"- {q}" for q in questions) or "（指摘なし）"

    system = (
        "あなたはOptiBuddy（業務最適化システムの自動登録基盤）で、ドメイン登録処理中に"
        "残った指摘事項に対し、業務担当者に代わって次のアクションを判断する役割を担います。"
        "登録する（force_apply）という判断は、ヒアリング内容や実装から見て実害が無い、"
        "または許容範囲内だと明確な根拠を持って言える場合のみ選んでください。"
        "確信が持てない場合はcontinue（あれば）かcancelを選び、無理にforce_applyしないこと。"
    )
    user = (
        f"## 業務名\n{domain_name}\n\n"
        f"## ヒアリングシート\n{hearing_block}\n\n"
        f"## 残っている指摘事項\n{questions_block}\n\n"
        f"## 生成されたコード（抜粋、最大5ファイル）\n{diffs_block}\n\n"
        f"## 選択可能なアクション\n{allowed_actions}\n"
        "- continue: 指摘への対応をAIエージェントにもう1ラウンド試させる\n"
        "- force_apply: 指摘を残したまま登録する\n"
        "- cancel: 登録を取りやめる（致命的な矛盾があり続行しても解決の見込みがない場合）\n\n"
        "以下のJSON形式のみで回答してください（他の文章は一切含めない）:\n"
        '{"action": "上記のいずれか", "reasoning": "判断理由（1〜2文）"}'
    )
    try:
        result = call_llm_json(
            [{"role": "system", "content": system}, {"role": "user", "content": user}],
            temperature=0.3, max_tokens=1024,
        )
        action = str(result.get("action", "")).strip()
        reasoning = str(result.get("reasoning", "")).strip() or "（理由の生成に失敗）"
        if action not in allowed_actions:
            logger.warning(
                f"[auto_decide_confirm] 許可されていないaction={action!r}が返されたため"
                f"安全側のcancelにフォールバックします（許可: {allowed_actions}）"
            )
            reasoning = f"（自動判断が不正な値を返したため安全側でcancelにフォールバック。元の理由: {reasoning}）"
            action = "cancel"
        return {"action": action, "reasoning": reasoning}
    except Exception as e:
        logger.warning(f"[auto_decide_confirm] 自動判断に失敗、安全側のcancelにフォールバック: {e}")
        return {
            "action": "cancel",
            "reasoning": f"自動判断呼び出し自体がエラーになったため、安全側でcancelを選択しました: {e}",
        }


def _run_auto_pilot(job_id: str, domain_name: str, max_rounds: int = 2) -> None:
    """
    auto_resolve=Trueのジョブに対する自動操縦スレッド。/api/domain/run発行直後に
    起動し、stageがneeds_confirmationになるたびに_auto_decide_confirm_actionで
    判断し、_run_confirm_job（人間がconfirmボタンを押した場合と全く同じ関数）を
    直接呼び出す。doneかerrorに達するか、waiting_for_agent_question（Component1の
    ask_human自動代行でも解決できず人間へのエスカレーションが必要と判定された状態）
    になったら終了する。

    自動でcontinueを選べるのは同一ジョブにつき最大max_rounds回まで
    （Koshoshi合意: 上限到達時は自動延長せず、推奨アクション付きで人間に委ねる）。
    """
    round_count = 0
    poll_interval_sec = 3
    max_idle_wait_sec = 1800  # 30分。ジョブ側で処理中のstageが続く限りidle扱いしない
    idle_waited = 0.0

    while idle_waited < max_idle_wait_sec:
        job = _job_get(job_id)
        if job is None:
            logger.warning(f"[auto_pilot:{job_id}] ジョブが見つかりません。自動操縦を終了します。")
            return
        stage = job.get("stage")

        if stage in ("done", "error", "cancelled"):
            logger.info(f"[auto_pilot:{job_id}] stage={stage}のため自動操縦を終了します。")
            return

        if stage == "waiting_for_agent_question":
            logger.info(
                f"[auto_pilot:{job_id}] ask_humanが自動代行できずエスカレーション済み"
                "（推奨回答つき）のため、自動操縦を終了し人間の回答を待ちます。"
            )
            return

        if stage != "needs_confirmation":
            time.sleep(poll_interval_sec)
            idle_waited += poll_interval_sec
            continue

        idle_waited = 0.0
        questions = job.get("questions", []) or []
        pending = job.get("pending", {}) or {}
        hearing_texts = pending.get("hearing_texts", [])
        loop_exhausted = bool(job.get("loop_exhausted", False))

        if round_count >= max_rounds:
            decision = _auto_decide_confirm_action(
                questions, pending, hearing_texts, domain_name, allow_continue=False,
            )
            _job_set(
                job_id,
                auto_pilot_recommendation={
                    "action": decision["action"], "reasoning": decision["reasoning"],
                    "round_count": round_count,
                },
            )
            logger.info(
                f"[auto_pilot:{job_id}] 自動続行の上限（{max_rounds}回）に到達。"
                f"推奨アクション「{decision['action']}」（理由: {decision['reasoning']}）"
                "を添えて人間に委ね、自動操縦を終了します。"
            )
            return

        allow_continue = not loop_exhausted
        decision = _auto_decide_confirm_action(
            questions, pending, hearing_texts, domain_name, allow_continue=allow_continue,
        )
        action = decision["action"]
        reasoning = decision["reasoning"]

        prior_log = job.get("auto_pilot_log") or []
        _job_set(job_id, auto_pilot_log=prior_log + [{
            "round": round_count, "questions": questions,
            "action": action, "reasoning": reasoning,
        }])
        logger.info(
            f"[auto_pilot:{job_id}] round{round_count}: 自動判断「{action}」"
            f"（理由: {reasoning}）"
        )

        if action == "cancel":
            try:
                written_paths = pending.get("written_paths", []) or []
                if written_paths:
                    from domain_generator import cleanup_dynamic_check_files
                    cleanup_dynamic_check_files(written_paths)
                _job_set(job_id, stage="cancelled", auto_pilot_final="cancel",
                         auto_pilot_final_reasoning=reasoning)
                logger.info(f"[auto_pilot:{job_id}] 自動判断によりドメイン登録を取りやめました。")
            except Exception as e:
                logger.error(f"[auto_pilot:{job_id}] 自動cancel処理でエラー: {e}", exc_info=True)
                _job_set(job_id, stage="error", error=f"自動操縦のcancel処理でエラー: {e}")
            return

        force_apply = (action == "force_apply")
        if action == "continue":
            round_count += 1
        try:
            _run_confirm_job(job_id, domain_name, pending, questions, reasoning, force_apply, False)
        except Exception as e:
            logger.error(f"[auto_pilot:{job_id}] _run_confirm_job呼び出しでエラー: {e}", exc_info=True)
            _job_set(job_id, stage="error", error=f"自動操縦の実行中にエラー: {e}")
            return
        # _run_confirm_jobは同期実行なので、次のループでjob状態を読み直せば
        # 最新のstageが反映されている。すぐ次を確認する。

    logger.warning(
        f"[auto_pilot:{job_id}] {max_idle_wait_sec}秒の間needs_confirmationへの遷移が"
        "無かったため（ジョブが他の理由で停止した可能性）、自動操縦を終了します。"
    )


@bp.route("/api/domain/run", methods=["POST"])
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
        # 2026-09-19追加（Koshoshi合意）: True の場合、needs_confirmation等の
        # 各確認ポイントを人間の/api/domain/confirm呼び出し待ちにせず、
        # OptiBuddy自身のLLM判断で自動的に代行する（_run_auto_pilot参照）。
        # 最終的な登録結果はいつも通りstage="done"/"cancelled"/"error"に現れるので、
        # 人間は最後に結果を確認するだけでよい。
        auto_resolve = bool(data.get("auto_resolve", False))

        if not domain_name:
            return jsonify({"status": "error", "message": "domain_name は必須です"}), 400
        if not hearing_texts:
            return jsonify({"status": "error", "message": "hearing_texts が空です"}), 400
        if base_domain_override and force_new_domain:
            return jsonify({"status": "error",
                "message": "base_domain_override と force_new_domain は同時に指定できません"}), 400

        _job_gc()
        job_id = uuid.uuid4().hex
        _job_set(job_id, stage="queued", domain_name=domain_name, auto_resolve=auto_resolve)

        thread = threading.Thread(
            target=_run_domain_job,
            args=(job_id, domain_name, hearing_texts, overrides, base_domain_override, force_new_domain),
            daemon=True,
        )
        thread.start()

        if auto_resolve:
            autopilot_thread = threading.Thread(
                target=_run_auto_pilot,
                args=(job_id, domain_name),
                daemon=True,
            )
            autopilot_thread.start()
            logger.info(f"[domain_run:{job_id}] auto_resolve=True: 自動操縦スレッドを起動しました。")

        return jsonify({"status": "started", "job_id": job_id})

    except Exception as e:
        logger.error(f"/api/domain/run error: {e}", exc_info=True)
        return jsonify({"status": "error", "message": str(e)}), 500


@bp.route("/api/domain/run/status/<job_id>", methods=["GET"])
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


@bp.route("/api/domain/confirm", methods=["POST"])
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
        # 2026-09-07追加（Koshoshi合意）: 動的検証由来の指摘を、force_applyとは
        # 別の明示フラグ＋理由必須で正式に通すためのオーバーライド。
        dynamic_override = bool(data.get("dynamic_override", False))
        job_id      = data.get("job_id", "") or uuid.uuid4().hex

        if not domain_name:
            return jsonify({"status": "error", "message": "domain_name は必須です"}), 400
        if not pending:
            return jsonify({"status": "error", "message": "pending が空です（/api/domain/run の応答をそのまま渡してください）"}), 400

        _job_set(job_id, stage="queued_confirm", domain_name=domain_name)

        thread = threading.Thread(
            target=_run_confirm_job,
            args=(job_id, domain_name, pending, questions, answers, force_apply, dynamic_override),
            daemon=True,
        )
        thread.start()

        return jsonify({"status": "started", "job_id": job_id})

    except Exception as e:
        logger.error(f"/api/domain/confirm error: {e}", exc_info=True)
        return jsonify({"status": "error", "message": str(e)}), 500


@bp.route("/api/domain/cancel", methods=["POST"])
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


@bp.route("/api/domain/interrupt", methods=["POST"])
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


@bp.route("/api/domain/agent_answer", methods=["POST"])
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


@bp.route("/api/domain/post_register_fix", methods=["POST"])
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


@bp.route("/api/domain/verify_dynamic_batch", methods=["POST"])
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
