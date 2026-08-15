"""
Backend/debug_agent.py

2026-07-16新設（「デバッグチャット」構想の本実装）:
指摘事項（Gate2の再検証で見つかった問題）を、実際にdraftとして残っている
ファイル（solver.py / converter.py / シナリオJSON）に対してAnthropicの
tool_useで直接読み書きさせ、修正させるエージェントループ。

背景: 以前は人間（実質Claude、外部のチャットセッション）が指摘を見て手動で
ファイルを直していたが、これをOptiBuddy自身のバックエンドに組み込み、
Anthropic APIを直接呼んでツール使用で自律的に修正させる。

ただし「自動で直った」と偽って完了扱いにしない、という大原則は崩さない。
このエージェントが行うのはあくまで「修正の試行」であり、実際に直ったかどうかは
呼び出し元（app.py _run_confirm_job）が domain_generator.run_gate2_checks() で
Gate2を再検証して判定する。このモジュールはGate2の代わりにはならない。

安全のためのスコープ制限:
  - 読み書きできるファイルは、呼び出し元が渡した written_paths
    （Gate2動的検証用に書き出したdraftファイル）に限定する。
    それ以外のパスへのread_file/write_fileは拒否する。
  - 最大ターン数（max_turns）で強制終了する（無限ループ防止）。
  - should_stop コールバックで、呼び出し元（人間がUIで「中断」した等）から
    ターンの合間に打ち切れる（LLM呼び出し中は打ち切れないが、1ターンは
    高々1回のLLM呼び出し+ツール実行なので、次のターンに入る前には必ず
    中断が反映される）。
"""
import json
import logging
from pathlib import Path
from typing import Callable, Optional

logger = logging.getLogger(__name__)

_READ_FILE_TOOL = {
    "name": "read_file",
    "description": "指定したパスの現在のファイル内容を読む。修正対象ファイルの一覧は最初のメッセージに含まれている。",
    "input_schema": {
        "type": "object",
        "properties": {"path": {"type": "string", "description": "読むファイルのパス"}},
        "required": ["path"],
    },
}

_WRITE_FILE_TOOL = {
    "name": "write_file",
    "description": (
        "指定したパスのファイル内容を、渡したcontentで完全に置き換える（部分編集ではなく全文置換）。\n\n"
        "2026-08-09追加: 既存ファイルの一部だけを直したい場合はedit_fileを使うこと。write_fileは"
        "ファイルを新規作成する場合、またはファイル全体の構造を作り直す必要がある大きな変更の場合"
        "にのみ使う。既存の大きめのファイル（数百行以上）に対して数行の修正のためだけにwrite_fileで"
        "全文を書き直すと、出力トークン上限で内容が途中で切れて書き込みが拒否されるリスクが高い。"
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "path": {"type": "string", "description": "書き込むファイルのパス"},
            "content": {"type": "string", "description": "書き込む新しい全文"},
        },
        "required": ["path", "content"],
    },
}

_EDIT_FILE_TOOL = {
    "name": "edit_file",
    "description": (
        "2026-08-09新設: 既存ファイルの一部分だけを書き換える（部分編集）。old_stringで指定した"
        "文字列をnew_stringに置き換える。修正箇所が数行程度の場合は、write_file（全文置換）ではなく"
        "必ずこちらを使うこと——出力するのは変更箇所の前後だけでよく、ファイル全体を出力し直す必要が"
        "ないため、応答が短く済み、出力トークン上限による途中切れも起きにくい。\n\n"
        "old_stringは対象ファイル内でユニークに一致する文字列を指定すること（前後の行を含めて"
        "十分な文脈を持たせ、他の箇所と区別できるようにする）。ファイル内にold_stringが0件、"
        "または複数件見つかった場合はエラーになる（複数件を意図的に全て置き換えたい場合のみ"
        "replace_all=trueを指定する）。事前に必ずread_fileで現在の内容を確認し、実際にファイルに"
        "存在する文字列（インデント・改行を含め正確に一致するもの）をold_stringに指定すること。"
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "path": {"type": "string", "description": "編集するファイルのパス"},
            "old_string": {"type": "string", "description": "置き換え対象の既存文字列（ファイル内でユニークであること）"},
            "new_string": {"type": "string", "description": "置き換え後の文字列"},
            "replace_all": {
                "type": "boolean",
                "description": "old_stringに一致する全ての箇所を置き換える場合はtrue。省略時false（ユニーク一致必須）。",
            },
        },
        "required": ["path", "old_string", "new_string"],
    },
}

_ASK_HUMAN_TOOL = {
    "name": "ask_human",
    "description": (
        "業務ロジックの解釈など、あなたの判断だけでは決められない曖昧な点について、"
        "その場で人間（業務担当者）に直接質問し、回答を得てから対応を続ける。\n\n"
        "2026-07-17新設: 以前はこの種の曖昧さはreport_doneのneeds_human_decisionに"
        "積んで丸ごとセッションを終了していたが、それだと人間が回答した後、"
        "もう一度最初から新しいラウンドを回す必要があり、時間がかかる上に"
        "Gate2の指摘が収斂しないことがあった。ask_humanならこのセッションを"
        "終了せずにその場で質問→回答を待って続きを直せる。\n\n"
        "1回の呼び出しにつき質問は1つ。質問は単独で読んで意味が通じるように"
        "具体的に書くこと（「どうしますか？」のような曖昧な聞き方はしない）。"
        "呼び出すと、回答が来るまでここで処理は一時停止する。\n\n"
        "呼ぶ前の必須確認: 対象ファイルをread_fileで読み、フォールバックチェーンや"
        "デフォルト値など、質問の答えがコード自体に既に書かれていないか確認したか？"
        "書かれていればそれに従って進め、この関数は呼ばないこと。"
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "question": {
                "type": "string",
                "description": "業務担当者に聞きたい具体的な質問。",
            },
        },
        "required": ["question"],
    },
}

_VERIFY_TOOL = {
    "name": "verify_gate2",
    "description": (
        "2026-07-18新設: 今のdraftファイル（write_fileで書いた最新の内容）に対して、"
        "実際にbaseline/infeasibleシナリオをconverter→solver.solve()まで通しで"
        "実行し、Gate2の動的検証と全く同じ結果（例外トレースバック、期待feasibleとの"
        "不一致等）をその場で返す。\n\n"
        "これは『たぶん直った』というあなたの推測ではなく、実際にソルバーを動かした"
        "結果そのものなので、write_fileで修正した後はreport_doneを呼ぶ前に必ずこの"
        "ツールで確認すること。まだ問題が残っていれば、その結果を見てread_file/"
        "write_fileでさらに直し、直したら再びverify_gate2を呼ぶ。report_doneは、"
        "直前のwrite_fileより後にverify_gate2を呼んでいない場合は受け付けられず、"
        "エラーが返って同じループに留まる。\n\n"
        "引数は不要。呼び出しごとに対象ファイルを一時的にimport（必要ならreload）して"
        "実行するため、多少時間がかかる場合がある。"
    ),
    "input_schema": {"type": "object", "properties": {}, "required": []},
}

_DONE_TOOL = {
    "name": "report_done",
    "description": (
        "全ての指摘事項に決着をつけた（直せるものは直した、ask_humanで聞いても"
        "尚あなたには判断できないものだけ項目化した）と判断したら呼ぶ。"
        "呼んだ時点でこのループは終了する。\n\n"
        "2026-07-18変更: write_fileで何か直した場合、その直後にverify_gate2を"
        "呼んで実際にsolve()まで確認していないと、このツールはエラーを返し"
        "受け付けられない（ループは終了せず続く）。直した内容が本当に効いているかを"
        "自己申告ではなく実行結果で確認してから完了を報告すること。\n\n"
        "2026-07-17変更: fixed_summaryとneeds_human_decisionを分けたのは、"
        "以前は1つのsummary文字列に全部まとめていたため、確認画面で「実際に"
        "修正した内容の長い説明」と「人間が今すぐ答えるべき項目」が1つの塊に"
        "なってしまい、業務担当者がどれに答えればいいのか見分けられなかったため。"
        "needs_human_decisionの各要素は、確認画面に1件ずつ独立したカードとして"
        "表示される。\n\n"
        "注意: 業務判断が必要なだけならreport_doneの前にまずask_humanで"
        "その場で聞くこと。needs_human_decisionは、ask_humanで聞く意味がない"
        "（設計上そもそも自動修正の範囲外である等）ものに限定する。"
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "fixed_summary": {
                "type": "string",
                "description": (
                    "実際に直した内容の要約（参考情報。人間の回答を必要としないもの）。"
                    "直した箇所が無ければ空文字。"
                ),
            },
            "needs_human_decision": {
                "type": "array",
                "items": {"type": "string"},
                "description": (
                    "業務上の判断が必要で、あなたの判断だけでは決められなかった項目を"
                    "1件1文字列として列挙する。各項目は、業務担当者が読んだだけで"
                    "「何について」「どういう選択肢があるか」が分かるように、単独で"
                    "完結した文にすること（他の項目やfixed_summaryを読まないと"
                    "意味が通じない書き方はしない）。無ければ空配列。"
                ),
            },
        },
        "required": ["fixed_summary", "needs_human_decision"],
    },
}


def _cache_marked_messages(messages: list) -> list:
    """
    2026-07-17新設: このループはmax_turns（既定12）まで、1ターンごとに
    client.messages.create()をキャッシュなしで呼んでおり、messagesはターンを
    重ねるたびにread_file/write_fileの中身（solver.py/converter.py/シナリオJSON等の
    全文）を積み増して伸びていく。cache_controlを一切使っていなかったため、
    毎ターン、それまでの会話履歴全体をゼロから再処理・再課金しており、
    「1回の登録に数十分かかる」の主因はStage2単体のプロンプトよりもこちらの方が
    大きいと考えられる（実装コード上の根拠: この関数のみ.stream()ではなく素の
    messages.create()を使っており、cache_controlも一切付与されていなかった）。

    Anthropicの多ターンエージェント向け標準パターンに従い、直近リクエストの
    「最後のメッセージの最後のブロック」にだけcache_control(ephemeral)を付与した
    コピーを返す。これにより、直前ターンまでの履歴はキャッシュから読まれ、
    今回新たに増えた分だけが実際に処理・課金される。呼び出し元が保持する
    messages本体（次ターンでも使う永続的な会話状態）は変更しない。
    """
    if not messages:
        return messages
    out = []
    for m in messages:
        content = m.get("content")
        if isinstance(content, str):
            content = [{"type": "text", "text": content}]
        else:
            # 各ブロックから既存のcache_controlを除去したコピーを作る
            # （ブレークポイントは常に「今回の最後のメッセージ」1箇所だけに保つ。
            # Anthropic側のブレークポイント上限は4だが、12ターンぶん残し続けると
            # すぐ超えてしまうため）。
            content = [
                {k: v for k, v in block.items() if k != "cache_control"}
                if isinstance(block, dict) else block
                for block in content
            ]
        out.append({**m, "content": content})
    if out[-1]["content"]:
        last_block = dict(out[-1]["content"][-1])
        # 2026-08-04: デフォルトTTLが5分に変更された。debug_agentのターン間には
        # human-in-the-loopの確認待ち（実測で1件あたり最大74%を占める）が挟まる
        # ことが多く、5分では頻繁にキャッシュが切れてこの巨大な会話履歴
        # （read_file/write_fileの全文が毎ターン積み増される）を丸ごと再処理・
        # 再課金することになっていたと考えられる。1hへ延長。
        last_block["cache_control"] = {"type": "ephemeral", "ttl": "1h"}
        out[-1]["content"] = out[-1]["content"][:-1] + [last_block]
    return out


def _summarize_stale_read_results(messages: list) -> list:
    """
    2026-08-10新設（会話履歴肥大化対策、段階B検証用）:
    OptiBuddy_V81_devnotes/2026-08-05_registration_time_reduction_plan.md タスク5
    （ENGINEERING_LOG.md 2026-08-10追記3の段階A分析）で確認した通り、read_fileで
    取得したファイル全文は、その後write_file/edit_fileで上書きされた後もそのまま
    会話履歴に残り続け、以降の全ターンで毎回まるごと再送されている（turn5時点で
    その回のトークンの94%が新規情報ではなく履歴の再送、という実測結果）。

    このうち、対応するpathへのwrite_file/edit_fileが**その後に成功している**
    read_file結果は、内容が既に古くなっており（実際のファイルは書き換わっている）、
    エージェントが以降の判断でそのまま使う価値はない（必要ならread_fileし直せば
    最新内容が得られる）。該当するtool_result.contentを短い注記に置き換えた
    「APIに送るための一時ビュー」を返す（_cache_marked_messages()と同じ方針で、
    呼び出し元が保持する本体のmessagesは変更しない）。

    注意（cache_controlとのトレードオフ）: 会話履歴の内容を変えると、Anthropicの
    プロンプトキャッシュは変更箇所以降で必ずミスする（最長一致prefix方式のため）。
    つまりこの要約を適用したターンは一時的にcache_creationが増える可能性がある。
    それ以降のターンでは要約後の（より小さい）内容が新しい基準としてキャッシュされ
    続けるため、残りターン数が十分あれば正味の削減になる、という仮説。この
    トレードオフの実測がタスク5・段階Bの検証目的そのもの
    （tools/stage_b_debug_agent_history_summarization_test.py参照）。
    """
    tool_use_meta: dict[str, tuple[str, str | None]] = {}
    for m in messages:
        if m.get("role") != "assistant":
            continue
        content = m.get("content")
        if not isinstance(content, list):
            continue
        for block in content:
            if isinstance(block, dict) and block.get("type") == "tool_use":
                inp = block.get("input") or {}
                tool_use_meta[block.get("id")] = (block.get("name"), inp.get("path"))

    successful_write_positions: list[tuple[int, str]] = []
    for idx, m in enumerate(messages):
        if m.get("role") != "user":
            continue
        content = m.get("content")
        if not isinstance(content, list):
            continue
        for block in content:
            if not (isinstance(block, dict) and block.get("type") == "tool_result"
                    and not block.get("is_error")):
                continue
            name, path = tool_use_meta.get(block.get("tool_use_id"), (None, None))
            if name in ("write_file", "edit_file") and path:
                successful_write_positions.append((idx, path))

    if not successful_write_positions:
        return messages  # 上書き実績が無ければ要約対象も無い

    def _is_stale(read_idx: int, path: str) -> bool:
        return any(widx > read_idx and wpath == path for widx, wpath in successful_write_positions)

    out = []
    for idx, m in enumerate(messages):
        if m.get("role") != "user":
            out.append(m)
            continue
        content = m.get("content")
        if not isinstance(content, list):
            out.append(m)
            continue
        new_blocks = []
        changed = False
        for block in content:
            if (isinstance(block, dict) and block.get("type") == "tool_result"
                    and not block.get("is_error")):
                name, path = tool_use_meta.get(block.get("tool_use_id"), (None, None))
                if name == "read_file" and path and _is_stale(idx, path):
                    new_blocks.append({
                        **block,
                        "content": (
                            f"（{path} の内容をここで読み込みましたが、その後write_file/"
                            "edit_fileで上書き済みのため要約のため省略しています。最新の"
                            "内容が必要な場合は改めてread_fileしてください。）"
                        ),
                    })
                    changed = True
                    continue
            new_blocks.append(block)
        out.append({**m, "content": new_blocks} if changed else m)
    summarized_count = sum(
        1 for m in out for b in (m.get("content") or [])
        if isinstance(b, dict) and isinstance(b.get("content"), str)
        and b["content"].startswith("（") and "要約のため省略" in b["content"]
    )
    if summarized_count:
        logger.info(f"[debug_agent] summarize_stale_reads: {summarized_count}件のread_file結果を要約に置換")
    return out


def _apply_edit(
    abs_path: Path, old_string: str, new_string: str, replace_all: bool = False,
) -> tuple[bool, str]:
    """
    2026-08-09新設: edit_fileツールの本体。write_file（全文置換）による出力トークン
    肥大化・途中切れ（ENGINEERING_LOG.md 2026-08-09追記参照、実機ログでmedical_appointment_
    scheduler_solver.pyの全文書き直しが2回連続で出力途中切れし書き込み拒否になった事例あり）
    への対策として、`Edit`ツールと同じold_string/new_string方式の部分編集を導入する。

    このロジックをrun_debug_agentのループ本体から切り出して独立関数にしているのは、
    Anthropic APIクライアントをモックしなくても純粋にファイル操作としてユニットテスト
    できるようにするため（test_debug_agent_edit.py参照）。

    戻り値: (成功したか, メッセージ)。メッセージは成功時は簡潔な確認、失敗時は
    エージェント自身が次のアクションを判断できるよう、原因（0件/複数件一致）を明示する。
    """
    try:
        content = abs_path.read_text(encoding="utf-8")
    except Exception as e:
        return False, f"読み込み失敗: {e}"

    count = content.count(old_string)
    if count == 0:
        return False, (
            "編集失敗: old_stringがファイル内に見つかりませんでした。"
            "read_fileで現在の内容を再確認し、インデント・改行を含め正確に一致する"
            "文字列を指定してください。"
        )
    if count > 1 and not replace_all:
        return False, (
            f"編集失敗: old_stringがファイル内に{count}箇所一致し、どこを編集すべきか"
            "一意に定まりません。前後の行を含めるなどしてold_stringをユニークな文字列に"
            "するか、全箇所を置き換える意図であればreplace_all=trueを指定してください。"
        )

    if replace_all:
        new_content = content.replace(old_string, new_string)
    else:
        new_content = content.replace(old_string, new_string, 1)

    try:
        abs_path.write_text(new_content, encoding="utf-8")
    except Exception as e:
        return False, f"書き込み失敗: {e}"

    return True, f"編集しました（{count}箇所置換）。" if replace_all and count > 1 else "編集しました。"


def _needs_reverify(actions: list[dict]) -> bool:
    """
    2026-07-18新設: report_doneをゲートするための判定。actionsを時系列に見て、
    直近のwrite_file/edit_fileより後にverify_gate2が呼ばれていなければTrue（＝まだ
    report_doneを受け付けてはいけない）を返す。verify_gate2が一度も
    呼ばれていない場合もTrue（write_file/edit_fileの有無に関わらず、少なくとも1回は
    実行結果で確認してからでないと完了扱いにしない）。

    2026-08-09変更: edit_file新設に伴い、write_fileだけでなくedit_fileも
    「ファイルを変更したアクション」として同様に扱うよう対象を拡張。
    """
    last_verify_idx = -1
    last_write_idx = -1
    for i, a in enumerate(actions):
        if a.get("tool") == "verify_gate2":
            last_verify_idx = i
        elif a.get("tool") in ("write_file", "edit_file"):
            last_write_idx = i
    if last_verify_idx < 0:
        return True
    return last_write_idx > last_verify_idx


def run_debug_agent(
    questions: list[str],
    written_paths: list[str],
    domain_name: str,
    hearing_texts: list[str],
    snake_name: str,
    human_notes: str = "",
    max_turns: int = 5,
    should_stop: Optional[Callable[[], bool]] = None,
    resume_state: Optional[dict] = None,
    human_answer: Optional[str] = None,
    disable_edit_file: bool = False,
    summarize_stale_reads: bool = False,
) -> dict:
    """
    disable_edit_file: 2026-08-09新設。edit_file導入前の挙動（write_fileのみ）を
      再現するためのテスト専用フラグ。本番コード（app.py）からは常にFalseで
      呼ばれる想定で、edit_fileのA/B効果測定（tools/配下の検証スクリプト）でのみ
      Trueを指定する。Trueの場合、tools一覧からedit_fileを除外し、system prompt
      からもedit_file優先を指示する項目（3.5）を外す。

    summarize_stale_reads: 2026-08-10新設（会話履歴肥大化対策、段階B検証用の
      テスト専用フラグ。本番コード（app.py）からは常にFalseで呼ばれる想定）。
      Trueの場合、read_fileで取得したファイル全文のうち、その後同じpathへの
      write_file/edit_fileが成功しているもの（＝もう古くなった内容）を、APIへ
      送る直前の会話履歴ビューでのみ短い要約に置き換える（本体のmessagesは
      変更しない。_cache_marked_messages()と同じ「ビューだけ変える」方針）。
      詳細は_summarize_stale_read_results()のdocstring、および
      OptiBuddy_V81_devnotes/2026-08-05_registration_time_reduction_plan.md
      タスク5参照。

    snake_name: 2026-07-18新設。verify_gate2ツールがdomain_generator.
      run_gate2_dynamic_verification(snake_name)を呼ぶために必要なドメインの
      snake_case名。written_pathsのファイル名（{snake}_solver.py等）と揃える。

    max_turns: 2026-07-18変更（既定3→5）。report_doneの前にverify_gate2の呼び出しを
      必須化したため、「write_file→verify_gate2→(必要ならさらにwrite_file→
      verify_gate2)→report_done」という往復が最低でも2〜3ターン増える。旧既定の3では
      1回の修正サイクルすら収まらない恐れがあるため引き上げた。

    human_notes: 前回のラウンド（このモジュールの旧設計。今はask_humanに置き換わり
      基本的には使わないが、下位互換のため残す）でエージェントが「業務上の判断が
      必要」と自己申告して止まった指摘に対し、人間が確認画面の回答欄に書いた内容。

    resume_state / human_answer: 2026-07-17新設。ask_humanツールを呼んで一時停止した
      セッションを再開するための引数。resume_stateは前回このループが
      stopped_reason="waiting_for_human"で返した際のresume_stateをそのまま渡す。
      human_answerはそのask_humanの質問に対する人間の回答。両方渡された場合、
      questions/domain_name/hearing_textsによる最初のユーザーメッセージ構築は
      スキップし、保存済みmessagesの続きから再開する（＝新しいラウンドではなく、
      同じセッションの続き）。

    戻り値:
      {"fixed_summary": str, "needs_human_decision": [str, ...], "turns_used": int,
       "stopped_reason": "done" | "max_turns" | "interrupted" | "error" | "no_llm"
                          | "waiting_for_human",
       "actions": [{"tool": "read_file"|"edit_file"|"write_file"|"verify_gate2", "path": str|None}, ...],
       # stopped_reason=="waiting_for_human" の場合のみ追加:
       "pending_question": str,
       "resume_state": {...このまま次回呼び出しにresume_stateとして渡す...}}

    2026-07-17変更: 以前はsummary（1つの自由記述文字列）に「直した内容」と
    「人間が答えるべき項目」を両方詰め込んでいたため、確認画面で1つの長い
    テキストブロックになり、業務担当者がどこに回答すればいいのか分からなく
    なる問題があった（実機で発生：「回答すべきものがどれか見当たらない」）。
    呼び出し元（app.py）はneeds_human_decisionの各要素を、確認画面に1件ずつ
    独立したカードとして表示すること。

    2026-07-17再設計: さらに、needs_human_decisionで止まったセッションを人間が
    回答した後に「新しいラウンド」としてもう一度最初から回す方式は、実機で
    Gate2の指摘が収斂しない（直すたびに別の指摘が増える）ことがあり、時間も
    かかっていた。ask_humanツールにより、業務判断が必要な曖昧さはセッションを
    終えずにその場で質問できるようにした。
    """
    from llm.llm_client import get_raw_anthropic_client, default_model, is_available, LLM_PROVIDER

    actions: list[dict] = list((resume_state or {}).get("actions", []))

    if LLM_PROVIDER != "anthropic" or not is_available():
        msg = ("デバッグエージェントはAnthropicプロバイダーのみ対応です"
               "（現在のLLM_PROVIDER設定、またはAPIキー未設定のため実行できません）。"
               "人間が直接ファイルを修正してから「続行」を押してください。")
        return {
            "fixed_summary": "", "needs_human_decision": [msg],
            "turns_used": 0, "stopped_reason": "no_llm", "actions": actions,
        }

    client = get_raw_anthropic_client()
    model = default_model()

    from domain_generator import _resolve_path

    allowed_paths = set(written_paths)
    file_list_block = "\n".join(f"- {p}" for p in written_paths) or "（対象ファイルなし）"

    system = (
        "あなたはOptiBuddy（制約プログラミング/CP Optimizerを使う業務最適化システムの"
        "自動登録基盤）のGate2デバッグエージェントです。新規業務ドメイン登録時に生成された"
        "コード（converter.py: 業務データ→DSL変換、solver.py: DSL→CP Optimizerモデル、"
        "シナリオJSON: baseline/infeasibleのテストデータ）に対し、自動チェック（Gate2）が"
        "検出した指摘事項を、実際のファイルを読み書きして直します。\n\n"
        "厳守事項:\n"
        "1. 必ずread_fileで現在の内容を確認してから直すこと。想像で書き換えない。\n"
        "2. 読み書きできるファイルは以下に限定される。それ以外のファイルには一切アクセスできない:\n"
        f"{file_list_block}\n"
        "3. 意味を変える大きな設計変更はしない。指摘事項に対する最小限で正確な修正に留める。\n"
        + ("" if disable_edit_file else (
            "3.5. 修正手段はedit_file（部分編集）を基本とし、write_file（全文置換）は新規ファイル"
            "作成、またはファイル構造そのものを大きく作り直す場合にのみ使うこと。既存の大きめの"
            "ファイルに対する数行の修正でwrite_fileを使うと、全文を出力し直すことになり応答が"
            "長くなって出力トークン上限で途中切れするリスクがある。\n"
        ))
        + "4. ask_humanを呼ぶ前に、対象ファイル自身のコードに答えが既に書かれていないか"
        "必ず確認すること。read_fileした内容の中に、フォールバックチェーン"
        "（例: `m.get(\"start\", m.get(\"start_min\", 0))`のような複数キー名を順に試す"
        "実装）、デフォルト値、コメントでの説明などがあれば、それ自体がそのフィールドの"
        "意図（キー名の揺れ・優先順位・既定動作）を示す一次情報である。書かれている"
        "実装から読み取れることを、書かれていないことと混同しないこと。ask_humanは"
        "『対象ファイルを読んでもなお決め手がない、業務ルールそのものの選択（例: "
        "hard制約かsoft制約か、丸め方針、優先順位のポリシー）』にのみ使う。"
        "『コードのどこかに答えがあるはずだが自分で探せていないだけ』の状態でask_humanを"
        "呼ぶのは誤り。\n"
        "5. 業務要件の解釈にあなたの判断だけでは決められない曖昧さがある場合（ヒアリングにも"
        "対象ファイルのコードにも根拠が無く、複数の解釈がありうる等）は、ask_humanでその場で"
        "業務担当者に質問し、回答を得てから対応を続けること。無理に決め打ちしない。\n"
        "6. needs_human_decision（report_doneの引数）に残すのは、ask_humanで聞いても意味がない"
        "もの、またはそもそも自動修正の範囲外のものに限定する。ask_humanで聞ける曖昧さは"
        "先にask_humanで聞くこと。\n"
        + ("7. write_fileでファイルを直したら" if disable_edit_file else "7. edit_file/write_fileでファイルを直したら")
        + "、その直後に必ずverify_gate2を呼び、実際に"
        "baseline/infeasibleがsolve()できるかを確認すること。直したつもりでも"
        "実行すると別の箇所が壊れていることがあるため、自己申告ではなく実行結果で確認する。"
        "verify_gate2の結果、まだ問題が残っていればread_file/write_fileでさらに直し、"
        "再度verify_gate2を呼ぶ。この往復は問題が解消するかmax_turnsに達するまで繰り返してよい。\n"
        "8. 全ての指摘に決着をつけたら（直した上でverify_gate2で確認した／ask_humanで確認した／"
        "人間判断が必要と結論した、いずれかの結論を出したら）report_doneを呼ぶこと。"
        "直前のwrite_fileより後にverify_gate2を呼んでいない場合、report_doneはエラーを返し"
        "受け付けられない。"
    )

    tools = [_READ_FILE_TOOL, _WRITE_FILE_TOOL, _ASK_HUMAN_TOOL, _VERIFY_TOOL, _DONE_TOOL]
    if not disable_edit_file:
        tools.insert(1, _EDIT_FILE_TOOL)

    if resume_state:
        messages: list = resume_state["messages"]
        turns_used_before = resume_state["turns_used"]
        pending_ask = resume_state["pending_ask"]
        partial_tool_results = list(resume_state.get("partial_tool_results", []))
        answer_text = (human_answer or "").strip() or (
            "（回答なし。あなたの判断で妥当と思う対応を進めてください。）"
        )
        tool_results = partial_tool_results + [{
            "type": "tool_result", "tool_use_id": pending_ask["tool_use_id"], "content": answer_text,
        }]
        messages.append({"role": "user", "content": tool_results})
        start_turn = turns_used_before + 1
        # 2026-07-30新設: resume後は前回ターンのresponse idを保持していないため、
        # 再開直後の1ターンだけはdiagnostics比較なし（previous_message_id=None）から入る。
        last_message_id = None
    else:
        hearing_block = "\n\n---\n\n".join(hearing_texts) if hearing_texts else "（ヒアリング内容なし）"
        questions_block = "\n".join(f"- {q}" for q in questions)
        notes_block = human_notes.strip() if human_notes and human_notes.strip() else (
            "（追加の回答・指示なし）"
        )
        user_text = (
            f"## 業務名\n{domain_name}\n\n"
            f"## 元のヒアリング内容\n{hearing_block}\n\n"
            f"## Gate2の指摘事項（{len(questions)}件）\n{questions_block}\n\n"
            f"## 人間からの追加回答・指示\n{notes_block}\n\n"
            f"## 修正対象ファイル\n{file_list_block}\n\n"
            "上記の指摘に対応してください。業務判断が必要な曖昧さがあれば、"
            "ask_humanでその場で聞いてください。"
        )
        messages = [{"role": "user", "content": user_text}]
        start_turn = 1
        last_message_id = None

    for turn in range(start_turn, max_turns + 1):
        if should_stop and should_stop():
            logger.info(f"[debug_agent] {turn}ターン目の前に中断要求を検知。打ち切ります。")
            return {
                "fixed_summary": "",
                "needs_human_decision": [
                    f"人間の指示により{turn - 1}ターン目で中断しました。"
                    "それまでの修正内容はディスクに残っています。続行すると再開します。"
                ],
                "turns_used": turn - 1, "stopped_reason": "interrupted", "actions": actions,
            }

        try:
            # 2026-07-30新設: Sonnetのキャッシュヒット率調査のため、cache diagnostics
            # (beta)を有効化。previous_message_idに前ターンのresponse.idを渡すと、
            # APIがrequest構造を比較し、キャッシュミスした場合はどこで前ターンと
            # 食い違ったか（system_changed/tools_changed/messages_changed等）を
            # resp.diagnosticsで返してくれる。betaのため通常のmessages.createではなく
            # beta.messages.createを使う必要がある。
            # 2026-08-10追加: summarize_stale_reads=Trueの場合、キャッシュ用の
            # ビューを作る前に、既に上書き済みのread_file結果を要約したビューを
            # 先に作る（本体messagesは不変）。段階B検証専用、既定Falseでは従来通り。
            messages_for_api = (
                _summarize_stale_read_results(messages) if summarize_stale_reads else messages
            )
            resp = client.beta.messages.create(
                model=model, max_tokens=8000, temperature=0,
                # systemはセッション内（file_list_block込みで）常に同一なのでキャッシュ対象に
                # する。messagesは_cache_marked_messages()で末尾ブロックにのみ
                # ブレークポイントを付けたビューを渡す（本体のmessagesは変更しない）。
                system=[{"type": "text", "text": system, "cache_control": {"type": "ephemeral", "ttl": "1h"}}],
                tools=tools, messages=_cache_marked_messages(messages_for_api),
                betas=["cache-diagnosis-2026-04-07"],
                diagnostics={"previous_message_id": last_message_id},
            )
        except Exception as e:
            logger.warning(f"[debug_agent] LLM呼び出し失敗（{turn}ターン目）: {e}", exc_info=True)
            return {
                "fixed_summary": "",
                "needs_human_decision": [f"エージェント実行中にエラーが発生しました（{turn}ターン目）: {e}"],
                "turns_used": turn - 1, "stopped_reason": "error", "actions": actions,
            }

        diag = getattr(resp, "diagnostics", None)
        cache_miss_reason = getattr(diag, "cache_miss_reason", None) if diag else None
        if cache_miss_reason is not None:
            logger.info(
                f"[debug_agent] cache diagnostics（{turn}ターン目）: "
                f"cache_miss_reason={cache_miss_reason.type}, "
                f"cache_missed_input_tokens={getattr(cache_miss_reason, 'cache_missed_input_tokens', '?')}"
            )
        logger.info(
            f"[debug_agent] usage（{turn}ターン目）: "
            f"cache_read={resp.usage.cache_read_input_tokens}, "
            f"cache_creation={resp.usage.cache_creation_input_tokens}, "
            f"input={resp.usage.input_tokens}"
        )
        last_message_id = resp.id

        assistant_content = [block.model_dump() for block in resp.content]
        messages.append({"role": "assistant", "content": assistant_content})

        tool_uses = [b for b in resp.content if b.type == "tool_use"]
        if not tool_uses:
            text = "".join(b.text for b in resp.content if b.type == "text")
            return {
                "fixed_summary": "",
                "needs_human_decision": [text or "（エージェントがツールを呼ばずに終了しました。理由不明。）"],
                "turns_used": turn, "stopped_reason": "done", "actions": actions,
            }

        tool_results = []
        done_result = None
        pending_ask = None
        for tu in tool_uses:
            if tu.name == "report_done":
                if _needs_reverify(actions):
                    tool_results.append({
                        "type": "tool_result", "tool_use_id": tu.id, "is_error": True,
                        "content": (
                            "report_doneは受け付けられません: write_file/edit_fileでの変更"
                            "（あるいはセッション全体）に対して、まだverify_gate2で実際に"
                            "solve()まで確認していません。まずverify_gate2を呼び、その結果を"
                            "見てから改めてreport_done（またはさらなる修正）を判断してください。"
                        ),
                    })
                    continue
                done_result = {
                    "fixed_summary": (tu.input.get("fixed_summary") or "").strip(),
                    "needs_human_decision": [
                        s for s in (tu.input.get("needs_human_decision") or []) if s and s.strip()
                    ],
                }
                tool_results.append({
                    "type": "tool_result", "tool_use_id": tu.id, "content": "了解しました。",
                })
                continue

            if tu.name == "verify_gate2":
                try:
                    from domain_generator import run_gate2_dynamic_verification
                    report = run_gate2_dynamic_verification(snake_name)
                    tool_results.append({
                        "type": "tool_result", "tool_use_id": tu.id,
                        "content": json.dumps(report, ensure_ascii=False, indent=2),
                    })
                    actions.append({"tool": "verify_gate2", "path": None})
                    logger.info(f"[debug_agent] verify_gate2実行: snake={snake_name}, "
                                f"warnings={len(report.get('warnings', []))}件")
                except Exception as e:
                    logger.warning(f"[debug_agent] verify_gate2実行失敗: {e}", exc_info=True)
                    tool_results.append({
                        "type": "tool_result", "tool_use_id": tu.id, "is_error": True,
                        "content": f"verify_gate2の実行自体に失敗しました: {e}",
                    })
                continue

            if tu.name == "ask_human":
                question = (tu.input.get("question") or "").strip()
                if not question:
                    tool_results.append({
                        "type": "tool_result", "tool_use_id": tu.id, "is_error": True,
                        "content": "質問が空です。具体的な質問文を指定してください。",
                    })
                    continue
                # このtool_useへの結果は、人間の回答が来るまでここでは作らずに保留する
                # （resume時にpartial_tool_resultsと合わせて1つのuserメッセージにする）。
                pending_ask = {"tool_use_id": tu.id, "question": question}
                continue

            path = tu.input.get("path", "")
            if path not in allowed_paths:
                tool_results.append({
                    "type": "tool_result", "tool_use_id": tu.id, "is_error": True,
                    "content": f"エラー: '{path}' は許可された修正対象ファイルではありません。"
                               f"修正対象は次のいずれかのみです: {sorted(allowed_paths)}",
                })
                continue

            abs_path = _resolve_path(path)
            if tu.name == "read_file":
                try:
                    content = abs_path.read_text(encoding="utf-8")
                    tool_results.append({"type": "tool_result", "tool_use_id": tu.id, "content": content})
                    actions.append({"tool": "read_file", "path": path})
                except Exception as e:
                    tool_results.append({
                        "type": "tool_result", "tool_use_id": tu.id, "is_error": True,
                        "content": f"読み込み失敗: {e}",
                    })
            elif tu.name == "edit_file":
                old_string = tu.input.get("old_string", "")
                new_string = tu.input.get("new_string", "")
                replace_all = bool(tu.input.get("replace_all", False))
                if not old_string:
                    tool_results.append({
                        "type": "tool_result", "tool_use_id": tu.id, "is_error": True,
                        "content": "old_stringが空です。置き換え対象の文字列を指定してください。",
                    })
                    continue
                ok, msg = _apply_edit(abs_path, old_string, new_string, replace_all)
                tool_results.append({
                    "type": "tool_result", "tool_use_id": tu.id, "content": msg,
                    **({"is_error": True} if not ok else {}),
                })
                if ok:
                    actions.append({"tool": "edit_file", "path": path})
                    logger.info(
                        f"[debug_agent] edit_file: {path} "
                        f"(old={len(old_string)}文字, new={len(new_string)}文字)"
                    )
                else:
                    logger.warning(f"[debug_agent] edit_file失敗: {path} — {msg}")
            elif tu.name == "write_file":
                new_content = tu.input.get("content", "")

                # 2026-08-05追加: 空（または極端に短い）書き込みガード。
                # 実機で、書き換え対象ファイルのサイズに対しdebug_agentのLLM呼び出しの
                # max_tokens（8000固定）が不足し、tool_use JSONが出力トークン上限で
                # 途中で切れてcontentが空文字のまま返ってきたことがあり、write_fileには
                # 内容の妥当性チェックが一切無かったため、それがそのままファイルへ書き込まれて
                # 約620行のsolver.pyが2回連続で0バイトになる事故が発生した
                # （2026-08-05、PatientTransportPlanner 4回目の登録試行。詳細は
                # devnotes/ENGINEERING_LOG.md 追記14参照）。write_fileは全文置換方式であり、
                # 対象はPython/JSONの実装ファイルのみなので「意図的に空にする」ケースは
                # 想定されない。空、または既存ファイルサイズに対して極端に短い（切り詰め
                # の疑いがある）contentは書き込まずに拒否し、エージェント自身に
                # read_fileでの再確認とやり直しを促す。
                try:
                    existing_size = abs_path.stat().st_size if abs_path.exists() else 0
                except Exception:
                    existing_size = 0
                stripped = new_content.strip()
                # このターンの応答がmax_tokens上限で打ち切られていないか（分かる場合は
                # 拒否メッセージに含め、原因の切り分けをエージェント自身にも伝える）。
                truncated = getattr(resp, "stop_reason", None) == "max_tokens"
                # 200文字以下の既存ファイルは対象外（相対比較が意味を持たないほど小さい）。
                # それ以外は、新しい内容が既存の30%未満なら「切り詰めの疑いあり」とみなす。
                suspiciously_short = existing_size > 200 and len(new_content) < existing_size * 0.3
                if not stripped or suspiciously_short:
                    if not stripped:
                        reason = "出力が空でした"
                    else:
                        reason = (
                            f"出力が元のファイルサイズ（{existing_size}文字）に対して極端に短く"
                            f"（{len(new_content)}文字）、意図しない切り詰めの疑いがあります"
                        )
                    if truncated:
                        reason += "（このターンの応答はmax_tokens上限で打ち切られています）"
                    tool_results.append({
                        "type": "tool_result", "tool_use_id": tu.id, "is_error": True,
                        "content": (
                            f"書き込みを拒否しました: {reason}。ファイルへの書き込みは行って"
                            "いません（元の内容はそのまま残っています）。ファイル全体を1回で"
                            "書き直そうとすると出力が長くなりすぎて途中で切れることがあります。"
                            "read_fileで現在の内容を確認した上で、変更が必要な箇所だけに絞った、"
                            "より短い書き直しに分けて対応してください。"
                        ),
                    })
                    logger.warning(
                        f"[debug_agent] write_file拒否: {path} "
                        f"(新内容{len(new_content)}文字 / 既存{existing_size}文字, "
                        f"truncated={truncated})"
                    )
                    continue

                try:
                    abs_path.write_text(new_content, encoding="utf-8")
                    tool_results.append({
                        "type": "tool_result", "tool_use_id": tu.id, "content": "書き込みました。",
                    })
                    actions.append({"tool": "write_file", "path": path})
                    logger.info(f"[debug_agent] write_file: {path} ({len(new_content)}文字)")
                except Exception as e:
                    tool_results.append({
                        "type": "tool_result", "tool_use_id": tu.id, "is_error": True,
                        "content": f"書き込み失敗: {e}",
                    })
            else:
                tool_results.append({
                    "type": "tool_result", "tool_use_id": tu.id, "is_error": True,
                    "content": f"不明なツール: {tu.name}",
                })

        if pending_ask is not None:
            logger.info(f"[debug_agent] {turn}ターン目でask_human: {pending_ask['question']}")
            return {
                "fixed_summary": "", "needs_human_decision": [],
                "turns_used": turn, "stopped_reason": "waiting_for_human", "actions": actions,
                "pending_question": pending_ask["question"],
                "resume_state": {
                    "messages": messages, "turns_used": turn,
                    "pending_ask": pending_ask, "partial_tool_results": tool_results,
                    "actions": actions,
                },
            }

        if done_result is not None:
            return {**done_result, "turns_used": turn, "stopped_reason": "done", "actions": actions}

        messages.append({"role": "user", "content": tool_results})

    # 2026-07-19修正: 以前は「残った指摘は人間の確認が必要です」という
    # 文言だったが、この結果を受け取る側の画面（登録前の確認画面は
    # 取りやめ／このまま登録するの二択、登録後の画面は「今すぐ直す」の
    # 再試行ボタンのみ）には、そもそも指摘に個別に回答・確認する手段が
    # 無い。「確認が必要」と言われても確認する場所が無いのは矛盾している
    # とKoshoshiより指摘（実機ログで実演）。debug_agent自身は呼び出し元の
    # 画面がどちらかを知らないため、ここでは「何が起きたか」の事実だけを
    # 述べ、「次に何をすべきか」の指図はしない（それは呼び出し元がUI文脈に
    # 応じて判断する）。
    return {
        "fixed_summary": "",
        "needs_human_decision": [
            f"AIエージェントが最大ターン数（{max_turns}）に達し、この指摘への対応が"
            "未完了のまま終了しました。"
        ],
        "turns_used": max_turns, "stopped_reason": "max_turns", "actions": actions,
    }
