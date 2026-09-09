
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
    _BACKEND_ROOT,
    _DOMAIN_SOURCE_FILES,
    _ensure_domain_registry_reconciled,
    logger,
)




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
