
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
    logger,
)
from .build_checks import (
    _CPO_WARN_PATTERNS,
)
from .static_checks import (
    _check_big_m_objective,
    _check_kpi_card_coverage,
    _check_mip_self_verification,
    _check_no_overlap_cumulative_conflict,
    _check_no_overlap_missing_transition_matrix,
    _check_no_overlap_without_sequence_var,
    _check_objective_coverage,
    _check_if_then_comparison_arg,
    _check_optional_interval_absent_value,
    _check_pulse_float_height,
    _check_table_sections_wiring,
    _check_unnamed_expr_get_value,
    _check_unwrapped_minimize,
    check_converter_solver_field_consistency,
    check_dsl_converter_field_consistency,
    check_dsl_solver_field_consistency,
)




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
    "pulse_float": (
        "この指摘は、数量を整数として扱うべき箇所に小数のままの値が渡されている"
        "可能性がある、という内容です。『数量の型（整数か小数か）の扱いについての"
        "指摘であること』が伝わるようにしてください。"
    ),
    "containment": (
        "この指摘は、ある条件が成り立つときだけ別の条件式が評価される、という"
        "書き方の一部が、システムの実行制約に反している可能性がある、という"
        "内容です。『条件分岐の書き方についての指摘であること』が伝わるように"
        "してください。"
    ),
    "resource_transition": (
        "この指摘は、複数の地点を巡回する順序を決める計算で、地点間の移動に"
        "かかる時間が考慮されていない可能性がある、という内容です。『地点間の"
        "移動時間の扱いについての指摘であること』が伝わるようにしてください。"
    ),
    "resource_sharing_conflict": (
        "この指摘は、複数人・複数台が同じ資源を『上限付きで同時に使ってよい』"
        "はずの場面で、実際には『同時には1つしか使えない』という強い制約と"
        "衝突している可能性がある、という内容です。『資源の同時使用（相乗り等）"
        "に関するルールについての指摘であること』が伝わるようにしてください。"
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

# 2026-09-18追記（Koshoshi合意）: 「言い換え文・推奨文の末尾を、業務担当者が
# 実行できない依頼で締めくくらない」という制約は、これまで言い換え関数ごとに
# 個別の文言で重複して書かれていた（_humanize_exception_findings、
# _humanize_required_gap_findings）。ところが【推奨】ブロック
# （humanize_technical_findings内、2026-08-30追加）にはこの制約が引き継がれず、
# しかもプロンプト内の例文自体が「修正をお勧めします」という業務担当者には
# 実行不可能な例になっていたため、実機で同種のバグが発覚した（一事が万事、
# Koshoshi指摘）。今後カテゴリや関数が増えるたびに同じ抜け漏れが再発しない
# よう、この制約を1箇所に集約し、全ての言い換え・推奨プロンプトはここを
# 参照すること。
_NO_UNACTIONABLE_ENDING_RULE = (
    "言い換え文・推奨文は、プログラミングやCP最適化の知識がない業務担当者"
    "自身が実際に行える判断・行動だけで締めくくってください。"
    "『開発担当者に連絡してください』『コードを確認してください』"
    "『ファイルを開いて確認してください』『修正をお勧めします』のように、"
    "業務担当者では実行できない依頼を締めの行動として指示しないでください。"
    "判断に迷う場合は、ヒアリング内容の意図を確認する問いかけ"
    "（例:「この理解で合っていますか？」）や、登録を進めるか保留するかの"
    "判断材料を伝えるだけに留めてください。"
)

# 2026-09-18追加: 上記はプロンプト指示だけでは徹底を保証できない（実際に
# 【推奨】ブロックで一度破られた実績がある）ため、生成後の文章を機械的に
# 検査し、業務担当者には実行不可能な依頼で終わっている文があれば、その文
# だけを取り除いて安全な定型の締めに差し替える事後チェックを設ける。
_UNACTIONABLE_ENDING_PATTERNS = [
    re.compile(p) for p in [
        r"コード(を|の)[^。]{0,15}(確認|見て|開いて|修正)",
        r"ファイル(を|の)[^。]{0,15}(確認|開いて|見て)",
        r"開発(担当者|者側|側)[^。]{0,15}(確認|連絡|相談|修正)",
        r"実装(を|の)[^。]{0,10}(確認|修正)してください",
        r"修正をお勧めします",
        r"スタックトレース",
        r"ログを確認してください",
    ]
]
_SAFE_FALLBACK_CLOSING = "この点を踏まえて、登録を続けるか、いったん保留するかをご判断ください。"


def _strip_unactionable_ending(text: str) -> str:
    """
    text中の各文（。区切り）を調べ、_UNACTIONABLE_ENDING_PATTERNSのいずれかに
    マッチする（＝業務担当者には実行不可能な依頼を含む）文があれば、その文だけ
    を取り除き、代わりに_SAFE_FALLBACK_CLOSINGを付け加える。該当しなければ
    textをそのまま返す。
    """
    if not text:
        return text
    sentences = [s for s in text.split("。") if s.strip()]
    if not sentences:
        return text
    bad = [s for s in sentences if any(pat.search(s) for pat in _UNACTIONABLE_ENDING_PATTERNS)]
    if not bad:
        return text
    logger.warning(f"[humanize] 業務担当者に実行不可能な依頼を検出、安全な定型文に差し替えます: {bad!r}")
    kept = [s for s in sentences if s not in bad]
    new_text = "。".join(kept)
    if new_text:
        new_text += "。"
    return new_text + _SAFE_FALLBACK_CLOSING


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
                return [_strip_unactionable_ending(str(x)) for x in items]
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
        + _NO_UNACTIONABLE_ENDING_RULE +
        "\n\n"
        "（2026-09-07追記、Koshoshi合意）『Gate2』『lexicographic』『big_m』"
        "『Big-M』『interval_var』『presence_of』『coverage_rate』"
        "『force_apply』『job_id』など、CP最適化やこのシステムの内部実装を"
        "知らないと意味が分からない専門用語・識別子は、たとえ元の指摘に"
        "含まれていても言い換え後の文には一切出さないでください（ファイル"
        "パスやスタックトレースと同じく、業務担当者の判断材料にならない生の"
        "プログラム表現として扱います）。\n\n"
        "（2026-08-30追記、2026-09-18改訂、Koshoshiの実機フィードバックを受け"
        "追加）言い換え文のさらに後ろに、改行を挟んで必ず【推奨】ブロックを"
        "1つ追加してください。【推奨】ブロックは必ず1文・40文字程度以内に"
        "収め、この指摘が実際に業務や解の正しさに影響する可能性が高いか"
        "低いかを一言で示すだけにしてください（例:「このまま登録して"
        "問題ない可能性が高いです」「解の精度に影響している可能性が"
        "あります」）。これはコードを実際に実行して確認したものではなく、"
        "あなたの推測に基づく参考情報であることが伝わる書き方にし、"
        "『必ずそうである』という断定は避けてください"
        "（例:「〜の可能性があります」「〜と考えられます」）。次に何を"
        "確認すべきかという具体的な行動の指示は【推奨】ブロックには書かず、"
        "言い換え文本文側の「次に何を確認すればよいか」の一文にまとめて"
        "ください。"
        + _NO_UNACTIONABLE_ENDING_RULE +
        "\n"
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
        "いった情報も削らないでください。また、"
        + _NO_UNACTIONABLE_ENDING_RULE +
        "（例:「このまま登録を保留し、修正を待つことをお勧めします」"
        "「今回は登録を見送ることをお勧めします」のように、登録を進めるか"
        "保留するかの判断材料として書いてください。実際の修正対応は別途"
        "行われるため、ここでは判断材料を伝えれば十分です）。\n"
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
        "4. " + _NO_UNACTIONABLE_ENDING_RULE + "\n"
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
    pulse_float_warnings = []
    containment_warnings = []
    missing_in_dsl_for_solver_warnings = []
    mip_self_check_warnings = []
    resource_transition_warnings = []
    resource_sharing_conflict_warnings = []
    solver_by_snake:       dict[str, tuple[str, str]] = {}
    converter_by_snake:    dict[str, tuple[str, str]] = {}
    ui_converter_by_snake: dict[str, tuple[str, str]] = {}
    i18n_messages_by_snake: dict[str, tuple[str, str]] = {}
    scenarios_by_snake:    dict[str, list] = {}

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
            resource_sharing_conflict_warnings.extend(_check_no_overlap_cumulative_conflict(code, path))
            big_m_warnings.extend(_check_big_m_objective(code, path))
            absent_value_warnings.extend(_check_optional_interval_absent_value(code, path))
            pulse_float_warnings.extend(_check_pulse_float_height(code, path))
            containment_warnings.extend(_check_if_then_comparison_arg(code, path))
            resource_transition_warnings.extend(_check_no_overlap_missing_transition_matrix(code, path))
            mip_self_check_warnings.extend(_check_mip_self_verification(code, path))

        elif path.endswith("_converter.py") and not path.endswith("_ui_converter.py"):
            snake = Path(path).stem[: -len("_converter")]
            converter_by_snake[snake] = (path, code)

        elif path.endswith("_ui_converter.py"):
            ui_snake = Path(path).stem[: -len("_ui_converter")]
            ui_converter_by_snake[ui_snake] = (path, code)
            warnings.extend(_check_table_sections_wiring(code, path))

        elif re.search(r"(?:^|/)i18n/(.+)_messages\.py$", path):
            i18n_snake = re.search(r"(?:^|/)i18n/(.+)_messages\.py$", path).group(1)
            i18n_messages_by_snake[i18n_snake] = (path, code)

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

    # Gate2静的チェック（2026-09-16追加）: KPIカード配線チェック
    # （ui_converterとi18nメッセージファイルが両方揃うドメインのみ。
    #  _check_table_sections_wiringと同じadvisory・ヒューリスティック扱い）
    for snake, (ui_converter_path, ui_converter_code) in ui_converter_by_snake.items():
        if snake not in i18n_messages_by_snake:
            continue
        _, i18n_code = i18n_messages_by_snake[snake]
        warnings.extend(_check_kpi_card_coverage(ui_converter_code, i18n_code, snake))

    return {
        "warnings": warnings,
        "unused_in_solver_warnings": unused_in_solver_warnings,
        "big_m_warnings": big_m_warnings,
        "absent_value_warnings": absent_value_warnings,
        "pulse_float_warnings": pulse_float_warnings,
        "containment_warnings": containment_warnings,
        "missing_in_dsl_for_solver_warnings": missing_in_dsl_for_solver_warnings,
        "mip_self_check_warnings": mip_self_check_warnings,
        "resource_transition_warnings": resource_transition_warnings,
        "resource_sharing_conflict_warnings": resource_sharing_conflict_warnings,
    }
