
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
    _DOMAIN_SOURCE_FILES,
    _PRIMARY_PROBLEMS_PATH,
    _SCENARIOS_DIR,
    _ensure_domain_registry_reconciled,
    _get_dynamic_stage1a_candidates,
    _to_snake,
    get_domain_engine,
    logger,
)
from .utils import (
    _resolve_path,
)




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

    # 2026-09-19追加（Koshoshi合意）: ドメイン名は分類LLM（classify_problem）に
    # とって所詮弱いヒントに過ぎない。DB・ファイルからは完全に削除されていても、
    # _STAGE1A_SYSTEM内に過去の誤分類事故の再発防止コメントとして実名で書かれた
    # ドメイン名と同じ名前で登録しようとすると、LLMが自身のシステムプロンプト中の
    # 事故事例をあたかも実在候補であるかのように読み込み、existing_domainへ誤って
    # 分類する実例が確認された（PatientTransportPlanner再登録時、2026-09-19）。
    # プロンプト全文を機械的に安全化する手段が無いため、せめて名前入力の時点で
    # 気付けるよう、_STAGE1A_SYSTEMの固定文言中にこの名前がそのまま含まれて
    # いないかをここで突き合わせる（新しい永続データは増やさない、その場限りの
    # 文字列検査。conflictsとは異なり登録をブロックはしない、注意喚起のみ）。
    prompt_name_warning = None
    if len(domain_name) >= 4 and domain_name in _STAGE1A_SYSTEM:
        prompt_name_warning = (
            f"「{domain_name}」という名前は、ドメイン分類AIへの固定指示文の中に"
            "過去の事例名としてそのまま書かれています。同じ名前で新規登録すると、"
            "無関係な内容でも「既存ドメインの拡張」と誤判定される可能性があります。"
            "別の名前を検討するか、あえて同じ名前で登録する場合は分類結果（既存/新規）"
            "を必ず確認してください。"
        )

    return {"exists": len(conflicts) > 0, "conflicts": conflicts,
            "domain_name": domain_name, "snake_name": snake,
            "prompt_name_warning": prompt_name_warning}


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
   [2026-09-18追加] ただし、候補ドメインの説明が「Xの標準シナリオ」のように名前を
   繰り返しただけで、実際の業務内容・対応済み制約を一切示していない場合は、ドメイン名の
   字面上の類似性だけで一致させてはならない（PatientTransportPlannerがRideshareMatchingPlanner
   と誤って同一視された実例: 説明文に実質的な情報が無く、名前の意味的な近さだけで一致判定
   されたことが根本原因だった）。この場合は安全側に倒し、match_type: "new_domain" を
   選ぶこと（confidenceも0.5以下に設定すること）。
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
# Stage 1a.3: 構造(DSL)類似度チェック（専用LLM呼び出し、2026-09-18新設）
#
# 背景: PatientTransportPlanner登録時の誤分類事故（既存ドメイン
# RideshareMatchingPlannerと誤って同一視され、Pattern 3の拡張生成が
# 中途半端な差分しか作れないまま登録されてしまった）を受けた対応。
#
# classify_problem()は候補ドメインの名前＋説明文（DBの短い要約テキスト）
# だけを見て判定しており、実際のDSL/ソースコードの構造を見ていない
# （説明文が薄い・名前が似ているだけで誤爆する既知パターン、
# HANDOFF_2026-07-31参照）。この誤爆を、新しいDSLを一切生成せずに
# 検出するため、detect_extension_gaps()（Stage1a.5）と同じ手法
# ——base_domainの実ソースコードをそのまま読ませ、ヒアリング文と
# 直接比較させる——を、Stage1a.5より前段（gap detail算定の前）で
# 「そもそもこの既存ドメインへの当てはめ自体が妥当か」という粗い
# ゲート判定として先出しする。
#
# Koshoshi合意の設計方針（2026-09-18）:
#   - 名前の一致・類似はあくまで「どの候補について実物を読みに行くか」の
#     トリガーに留め、最終判断は実物（DSL/ソース）比較で行う。
#   - ドメイン名が同一またはほぼ同一の場合は、LLM呼び出し無しで
#     即座に same_scenario と判定する（安価な決定的ショートカット）。
#   - 「差分がほぼ無い/名前がほぼ同一」→ same_scenario、
#     「差分小・再利用可能」→ extension、
#     それ以外（差分大・判断が難しい・確信が持てない）は全部 new_domain
#     に倒す（疑わしきは新規ドメイン。confidenceが低い場合も同様に
#     new_domain側に倒す非対称ルール）。
# ─────────────────────────────────────────────────────────────

_DOMAIN_NAME_IDENTITY_THRESHOLD = 0.9

_STRUCTURAL_SIMILARITY_SYSTEM = """あなたは、新しい業務ヒアリングの内容と、既存に実装済みの最適化ドメインの実際の
ソースコードを見比べて、両者がどれだけ構造的に近いかを判定する専門家です。

背景: この判定の前段（別のLLM呼び出し）で、候補ドメインの「名前」と「短い説明文」
だけを見て、この既存ドメインに当てはめられそうだという一次判定が既に出ています。
しかしその一次判定は、業務用語がドメイン名と字面上似ているだけで、実際には全く
別の業務構造を誤って同一視してしまうことがあります（実例: 「送迎」という言葉が
似ているだけで、通院送迎の1対1割当を、乗合マッチングの別ドメインと誤って
同一視した事故）。あなたの仕事は、実際のソースコード（変数構造・制約ファミリー・
目的関数の形）とヒアリング文を直接見比べて、この一次判定が本当に正しいかを
検証することです。

判定基準:
1. **same_scenario**: ヒアリング内容が、既存ドメインの実装が既にカバーしている
   業務とほぼ同一（変数構造・制約・目的関数がほぼそのまま使え、新しいコードを
   一切書く必要がない）。
2. **extension**: 業務の骨格（主要な変数構造・制約ファミリー）は既存ドメインと
   共通しているが、新しい制約や項目の追加が必要（既存コードへの部分的な拡張で
   対応できる）。
3. **new_domain**: 骨格から異なる（変数構造・制約ファミリー・目的関数の形が
   別物）、または既存ドメインとの対応関係の判断に確信が持てない。

**重要**: 2（extension）と判定してよいのは、既存コードの主要な構造をそのまま
再利用できると高い確信を持てる場合のみです。少しでも「本当にこの既存ドメインを
土台にしてよいか」に迷いがある場合、あるいは業務の対象・単位・粒度が違う
（例: 個人単位 vs グループ単位、施設単位 vs 案件単位）場合は、無理に
same_scenario/extensionに寄せず、3（new_domain）と判定してください。
疑わしきは new_domain です。

JSONのみを返してください。前置き・説明不要。

{
  "verdict": "same_scenario" または "extension" または "new_domain",
  "confidence": 0.0〜1.0の数値（judgmentへの確信度。少しでも迷いがあれば0.5以下にすること）,
  "reason": "判定理由を1〜2文で。実際のソースコードのどの部分（関数名・変数名・制約の形等）を
             根拠にしたかを具体的に含めること。"
}
"""


def check_structural_similarity(domain_name: str, hearing_texts: list, base_domain: str) -> dict:
    """
    Stage1a.3: classify_problem()がmatch_type="existing_domain"または
    "base_problem"と判定した直後に呼ぶ。base_domainの実ソースコードを
    直接読ませてヒアリング文と比較し、その一次判定が本当に妥当かを検証する。

    戻り値: {"verdict": "same_scenario"|"extension"|"new_domain",
             "confidence": float, "reason": str}
    ソースファイルが見つからない場合は安全側に倒し、new_domain・confidence=0.0を返す
    （実ソースで検証できない以上、既存ドメインへの当てはめを続ける根拠が無いため）。

    [2026-09-18修正] 実装ファイルの存在確認を、ドメイン名一致ショートカットより
    「先に」行うよう順序を変更した。修正前は名前が一致しさえすれば実装ファイルの
    有無を一切見ずに same_scenario を確定させていたため、DBにレコードだけ残り
    実装ファイルが存在しない「幽霊ドメイン」が候補になった場合（PatientTransportPlanner
    再登録時に実機発生: post_register_fixが失敗し実装ファイルが失われた後も
    dsl_definitionsの行だけが残り、同名で再登録するたびに名前一致ショートカットが
    発火して「same_scenario」と誤認定し、実装コードの無いDSL定義だけが際限なく
    積み上がった）、これを検出できなかった。「名前が同じだから中身も同じはず」という
    前提そのものが、まさに幽霊ドメインでは成り立たないため、ファイル存在確認を
    名前一致より優先する。
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
            logger.warning(f"[structural_similarity] ソース読み込み失敗: {path} — {e}")

    if not source_sections:
        logger.warning(
            f"[structural_similarity] {base_domain} の実装ファイルが1つも見つからず"
            f"検証不能（幽霊ドメインの疑い）。ドメイン名の一致有無に関わらず安全側に倒し "
            f"new_domain と判定します。"
        )
        return {"verdict": "new_domain", "confidence": 0.0,
                "reason": f"{base_domain} の実ソースコードが1つも見つからず構造検証が"
                          f"できなかった（幽霊ドメインの疑い）"}

    # 決定的ショートカット: 実装ファイルの存在を確認できた上で、ドメイン名が同一または
    # ほぼ同一な場合のみ、LLM呼び出し無しで same_scenario と判定する
    # （Koshoshi合意の設計方針）。
    name_ratio = difflib.SequenceMatcher(
        None, _to_snake(domain_name), _to_snake(base_domain)
    ).ratio()
    if name_ratio >= _DOMAIN_NAME_IDENTITY_THRESHOLD:
        logger.info(
            f"[structural_similarity] {domain_name} と {base_domain} のドメイン名が"
            f"ほぼ同一（一致率{name_ratio:.2f}）、かつ実装ファイルの存在を確認できたため、"
            f"LLM呼び出し無しで same_scenario と判定"
        )
        return {"verdict": "same_scenario", "confidence": 1.0,
                "reason": f"ドメイン名がほぼ同一（一致率{name_ratio:.2f}）、"
                          f"実装ファイルの存在も確認済み"}

    hearing_combined = "\n\n---\n\n".join(hearing_texts)
    user_prompt = f"""## 新しい業務名
{domain_name}

## 一次判定で候補になった既存ドメイン
{base_domain}

## 既存ドメインの実際のソースコード
{chr(10).join(source_sections)}

## 新しい業務のヒアリング内容
{hearing_combined}

上記を見比べて、JSON で判定してください。
"""
    messages = [{"role": "system", "content": _STRUCTURAL_SIMILARITY_SYSTEM},
                {"role": "user", "content": user_prompt}]
    raw    = call_llm(messages, model=default_model(), max_tokens=1024, temperature=0)
    result = extract_json(raw)
    verdict    = result.get("verdict", "new_domain")
    confidence = result.get("confidence", 0.0)
    reason     = result.get("reason", "")
    if verdict not in ("same_scenario", "extension", "new_domain"):
        verdict = "new_domain"
    logger.info(
        f"[structural_similarity] {domain_name} vs {base_domain}: "
        f"verdict={verdict}, confidence={confidence}"
    )
    return {"verdict": verdict, "confidence": confidence, "reason": reason}


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
