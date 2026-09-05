---
name: optibuddy-domain-registration
description: "OptiBuddy(CPLEX CP Optimizerベースの最適化ドメイン自動生成プラットフォーム)で新規/既存ドメインをFlask APIから直接登録・検証する手順。初回承認後は指摘対応と登録可否判断も上限付きで自律的に行う。「OptiBuddyにドメイン登録して」「ヒアリングシートで登録して」等の依頼があった時に使う。"
---

# OptiBuddy ドメイン登録スキル

OptiBuddy V81（CPLEX CP Optimizerベースの最適化ドメイン自動生成プラットフォーム）で、ヒアリングシートから新規業務ドメインを登録する、または既存ドメインを再登録・修正するときの標準手順。CP/OR最適化とGate2検証ロジックの専門知識を前提に、Claude in Chromeでのボタンクリック連打ではなくFlask APIを直接叩くことで高速化する。初回のジョブ起動承認さえ得られれば、以降の指摘対応・登録可否の判断は上限付きでLLMが自律的に行う。ユーザーへの報告は「ユーザー向け進捗報告のルール」節に従い、業務用語レベルに翻訳した簡潔な進捗表示で行う。

## 呼び出し時の必須パラメータ

本スキルは以下3つを入力パラメータとして必要とする。いずれかがユーザーの依頼に含まれていない場合、登録処理を開始する前に必ずユーザーに確認する。

- **作業対象のプロジェクトルート**（例: `~/work/OptiBuddy_V81_public/` のように、このリポジトリをクローンしたディレクトリ）。複数の環境（本番相当・検証用など）を並行して起動している場合、どのルートに対して作業するかは自明ではないため、ユーザーから明示されていなければ必ず確認する。以下の全手順中の`Backend/`・`Frontend/`パスや、Vite/Flaskの接続先は、すべてこのプロジェクトルート配下のものを指す。
- **新規ドメイン名**
- **ヒアリングシートの内容（テキスト）**。ファイル添付の場合は下記「1. ヒアリングシートの準備」の注意（アップロードキャッシュ問題）に従うこと。

## ユーザー向け進捗報告のルール（必読）

登録処理の進み具合をユーザーに伝えるときは、必ず下記の固定9ステップに沿って
「[ステップ X/9] ステップ名」を先頭に付けて報告する。技術的な診断そのものは
これまで通り正確に行ってよいが、ユーザーへの報告はヒアリングシートに出てくる
のと同じレベルの業務用語に翻訳し、API名・ファイルパス・関数名・ハッシュ値・
スタックトレース・HTTPステータス・job_id・interval_var/presence_of等の
CP/OR専門用語といった内部情報は含めない。

**固定ステップ一覧**
1. 内容の確認（ヒアリングシートとドメイン名を確認）
2. 重複チェック（同じ名前の業務がすでに無いか確認）
3. 登録開始（AIによる自動作成を開始。実行には毎回ユーザーの許可が必要）
4. 業務ルールの読み取り
5. プログラムの自動作成
6. 作成内容のチェック
7. 確認事項への対応
8. 登録の確定
9. 反映確認（サーバー再起動後の動作テスト）

**メッセージの先頭には必ず次の4種類のいずれか1つのラベルを付ける**
- 【情報】... 経過報告のみ。反応不要。
- 【警告】... 気に留めてほしい点があるが、処理は自動的に続行する。反応不要。
- 【要対応】... Claudeが自律的に判断・対応したことの事後報告（例:
  指摘を検討して登録した／不具合を修正した）。いま反応する必要はない。
- 【要回答】... ここで止まっている。ユーザーの回答・許可がないと先に進めない。

技術的な詳細（コードの中身、API呼び出し、エラーメッセージの原文等）は、
ユーザーが「詳しく」「技術的な原因は」等と明示的に求めたときだけ出す。
それ以外は上記の業務用語レベルの1〜2文に要約して報告する。

## 前提

- 上記の「作業対象のプロジェクトルート」に対して、Frontend開発サーバー（Vite、通常 `http://localhost:5173`）が起動しており、Claude in Chromeで開いたタブが存在すること。複数ルートを同時に起動している場合はポート番号がルートごとに違うことがあるので、開いているタブが本当に指定されたプロジェクトルートのものかを確認する。
- Backendの Flask サーバー（通常 `http://localhost:5000`）が、同じプロジェクトルートで起動していること。フロントの `<プロジェクトルート>/Frontend/src/domain/constants.ts` の `API_BASE` で実際のポートと接続先ルートを確認できる。
- Backendのソースは `<プロジェクトルート>/Backend/` 配下（`domain_generator.py`, `app.py`, `llm/llm_client.py`, `debug_agent.py`, `solvers/`, `dsl_transformer/`, `dsl_repository/scenarios/` 等）。
- **Flaskはローカルの実プロセスであり、Claude側のBashサンドボックスから直接 `curl localhost:5000` 等ではアクセスできない**（別ネットワーク名前空間）。API呼び出しは必ずClaude in Chromeの `javascript_tool`（`action: "javascript_exec"`）で、実際に開いているブラウザタブの中から `fetch()` を実行して行うこと。

## 絶対に守ること: 実行前の確認と自律実行の上限

- `/api/domain/run`（新規ジョブ起動）は、実際にLLMを呼び登録処理を進める最初の操作であり、OptiBuddy側の`ANTHROPIC_API_KEY`（ユーザー自身のAPIアカウント）に課金が発生し後から取り消せない。**この初回起動だけは、必ずユーザーから明示の「やって良い」を得てから実行する**。「方法を確認された」ことと「実行して良いと言われた」ことは別。方法だけ聞かれた場合は、方法を説明して実行前にもう一度止まる。
- **初回起動の承認を得た後は**、指摘事項が出た場合の3択（「取りやめ（作業中ファイルを破棄）」「指摘を残したまま登録する」「続行（AIエージェントが対応）」）の選択と、それに伴う`/api/domain/confirm`（force_apply含む）の呼び出しは、手順5の診断結果に基づきLLMが自律的に行ってよい。都度ユーザーに選択肢を提示して判断を仰ぐ必要はない。
- **自律実行の上限（無限ループ防止）**: 同一ジョブに対して「続行（AIエージェントが対応）」を自律的に選択できるのは最大2回まで。3回目のneeds_confirmationでも同種の指摘が残っている場合は、それ以上自律的に確定処理を進めず、その時点までの診断結果・試行履歴（何を指摘され、何を試し、なぜ解決しなかったか）を整理してユーザーに提示し、次のアクションの指示を仰ぐ。カウントはジョブ単位でリセットする。
- 参照系のGET（`/api/domain/attachments`, `/api/domain/check/<name>`, `/dsl_repository/scenarios`, `/api/domain/run/status/<job_id>`のポーリング等）は無害なので確認不要。

## 手順

### 1. 入力パラメータの確認とヒアリングシートの準備

作業対象プロジェクトルート、新規ドメイン名、ヒアリングシート本文（Markdown等）の3つが揃っていることを確認する。揃っていなければ先にユーザーに確認する。ファイル添付の場合、内容をそのまま使う。

**注意（発生実績あり）**: 同じファイル名を複数回アップロードし直された場合、Readツールで取得できる内容が更新前の古い内容のままキャッシュされていることがある。ヒアリングシートがワークスペースフォルダ内の実ファイルとしても存在する場合は、アップロード経由ではなく実際のファイルパス（例: `<プロジェクトルート>/docs/test_hearings/<name>.md`）を直接Readして、内容が最新（ユーザーが確定させた版）であることを確認してから使う。

### 2. 事前チェック（無害・確認不要）

```js
await fetch('http://localhost:5000/api/domain/check/<DomainName>').then(r=>r.json())
```

`exists: false` を確認する（既存なら衝突。再登録したい場合は先にユーザーに削除してもらう）。

### 3. 登録ジョブの起動（要ユーザー許可・以降は自律実行に入る起点）

**呼び出し方の前提**: このスキルはユーザーがチャットで「（プロジェクトルート）にヒアリングシートからドメイン名（名前）として登録して」のように自然文でパラメータを伝え、ヒアリングシート本体はチャットに「＋」でファイル添付する運用を前提とする。**画面（フロントエンドのモーダル）をこちらから開く必要は無く、開いてはならない。** ユーザー自身が画面から手動登録する場合（登録方法1）とは別の運用であり、本手順はスキル起動（登録方法2）専用。

**2026-09-05再改訂（モーダル非経由・ファイル直送方式）**: 過去に試した「JS文字列リテラルへの埋め込み」も「画面テキストエリアへの直接入力」も、いずれも一度は私がヒアリングシート本文を読み取ってから別のツール呼び出しの引数として書き出す、という手作業の転記工程を経由していた。この転記工程自体が、全角/半角文字の混同など再現性のある人為ミスの温床だった（同一文字の誤変換が複数回のテスト登録で繰り返し発生した実績あり）。**この転記工程そのものを無くし、ヒアリングシートのファイルを一度も「読んで書き直す」ことなく、バイト列のまま登録APIまで届ける。**

**正しい手順（ファイルをそのまま経由させる。内容を読んで書き写す工程を挟まない）**:

1. チャット添付されたヒアリングシートのファイルを、`cp`等の単純ファイルコピーで `<プロジェクトルート>/Frontend/public/_hearing_tmp_<適当な一意な文字列>.md` に置く（Viteの開発サーバーは`public/`配下をそのままのURLで配信するため、フロントエンドが動いているoriginから`fetch`で直接取得できるようになる）。このコピーはBashの`cp`コマンドで行い、内容をいったんテキストとして読み込んで書き出す方法は使わない（読み書きの往復を挟むと転記ミスの経路が復活するため）。

```bash
cp '<hearing_sheet_path>' '<プロジェクトルート>/Frontend/public/_hearing_tmp_<一意な文字列>.md'
python3 -c "
import hashlib
print('HASH:', hashlib.sha256(open('<hearing_sheet_path>','rb').read()).hexdigest())
"
```

2. ブラウザ側（`javascript_exec`、対象タブはフロントエンドを開いているタブ）で、上記の一時ファイルを`fetch`でそのまま取得し、SHA-256を計算して手順1のHASHと照合する。一致すれば、その場で同じ`hearing_texts`として`/api/domain/run`に直接POSTする（モーダルを開く・テキストエリアに入力する、という工程を一切経由しない）。

```js
const text = await fetch('/_hearing_tmp_<一意な文字列>.md').then(r => r.text());
const enc = new TextEncoder().encode(text);
const digest = await crypto.subtle.digest('SHA-256', enc);
const hex = Array.from(new Uint8Array(digest)).map(b => b.toString(16).padStart(2, '0')).join('');
if (hex !== '<手順1で控えたHASH>') {
  throw new Error('ハッシュ不一致: public/への配置かfetch元が想定と違う可能性。この text は使用禁止。');
}
const res = await fetch('http://localhost:5000/api/domain/run', {
  method: 'POST', headers: {'Content-Type': 'application/json'},
  body: JSON.stringify({domain_name: '<DomainName>', hearing_texts: [text]})
});
const j = await res.json();
JSON.stringify(j)  // {"job_id": "...", "status": "started"}
```

3. ハッシュが一致しない場合は、その場で止めてユーザーに事実を報告する（`public/`への配置パス・フロントエンドの実際のorigin・Viteの静的配信設定を再確認する。テキストの再生成や手打ちでのフォールバックは行わない — 転記工程を復活させないため）。

4. 登録ジョブの起動が確認できたら、`Frontend/public/`に置いた一時ファイルは`rm`で削除する（放置しない）。

既存ドメインの拡張機能追加などで `base_domain_override` や `force_new_domain` を使う場合は、同じPOSTボディに`base_domain_override`・`force_new_domain`を追加すればよい（画面のチェックボックス操作は不要）。

このジョブ起動の承認を得た時点で、以降の指摘対応・アクション選択（手順5〜7、上限あり）はユーザーに都度確認せず自律的に進めてよい。

### 4. 進捗ポーリング

**2026-09-05改訂**: 以前は「fetchで確認→結果を見る→少し待つ→また確認」をLLMが逐次繰り返していたが、この「待つ／もう一度確認する」という判断そのものに毎回LLMの生成時間（数秒）がかかり、待機ステージが長いジョブほど無駄な時間が線形に積み重なっていた（実測: 8往復のポーリングだけで数十秒〜1分規模）。**ポーリングループはLLMが1手ずつ行うのではなく、1回の`javascript_exec`呼び出しの中でブラウザ側に完結させる。** ループの待機自体はブラウザのイベントループが担うため、待っている間LLMは一切呼ばれない。

```js
let job;
const t0 = performance.now();
while (true) {
  const r = await fetch('http://localhost:5000/api/domain/run/status/<job_id>').then(r=>r.json());
  job = r.job;
  if (['needs_confirmation', 'done', 'error'].includes(job.stage)) break;
  if (performance.now() - t0 > 300000) break; // 5分の安全上限（無限ループ防止）
  await new Promise(res => setTimeout(res, 2000));
}
JSON.stringify({stage: job.stage, timeline: job.stage_timeline})
```

これを1回呼ぶだけで、ジョブが `needs_confirmation` か `done`/`error` になるまで待ってから返ってくる。`job.stage_timeline` に各ステージの開始タイムスタンプ（UNIX epoch秒）が入っているので、後で処理時間の内訳を出せる。ユーザーへの進捗報告（「[ステップ4/9]...」等）は、このループが返ってきた後に1回まとめて行う（ループ実行中に何度も報告メッセージを出す必要はない）。

### 5. 指摘が出たら盲目的に転記せず自分で診断する

`needs_confirmation` の場合、`job.pending`に指摘文が入っている。**`blocking_questions`と`advisory_questions`は別物であり、どちらか一方だけを見て済ませてはならない。** `job.pending.questions`はこの2つを結合したものなので、取得漏れを防ぐには常に3つとも別々に確認する。

**重大インシデント（2026-09-05発生・診断ミスの直接原因）**: 以前のこの節の一括取得コードは`advisory: job.pending.advisory_questions || job.pending.questions`という書き方をしていた。この版のAPIでは`advisory_questions`フィールドが常に存在するため、`||`の左側が常に真になり、**`blocking_questions`が一度も取得・診断されないまま登録を確定してしまった**。この状態で実際に実装バグ（後述の「危険度の高い区分」に該当する`blocking_questions`）を見落とし、正常ケースが解けないドメインをforce_apply=trueで登録する事故が起きた。この教訓から、`blocking_questions`は`advisory_questions`とは別のキーとして必ず明示的に取得すること。

```js
JSON.stringify({
  blocking: job.pending.blocking_questions || [],
  advisory: job.pending.advisory_questions || [],
  diffs: (job.pending.diffs || []).map(d => ({path: d.path, content: d.new_content}))
})
```

取得した`blocking`と`advisory`は、以下のようにそれぞれ:

- `job.pending.diffs` の中の該当ファイル（`new_content`フィールド）を実際に読み、コードやシナリオJSONの中身を確認する。
- 指摘の技術的な意味（例: 「実行不可能と判定すべきなのに実行可能と判定された」は、CPモデルがoptional interval_var等で「全件未割当」を常に有効解として許す構造だと構造的に起こり得る、等）を突き止める。
- ヒアリングシートの該当節（特に「守らなければならないルール」「人数・数量の扱い」「解けない場合の対応」節のユーザー確定回答）と矛盾しないか照らし合わせる。
- 実装バグなのか、Gate2チェック側の前提とドメイン設計の不一致（false positive）なのかを切り分ける。

**`blocking`側の指摘文の接頭辞ごとに、危険度が明確に異なる。「よくある誤検知パターンだから」という理由で一括処理しないこと。** 特に次の3種は、Gate2の動的検証（実際にsolverをimportしてbaseline/infeasibleシナリオを解いてみる工程）で検出された、実装コードの具体的な欠陥を指している可能性が高い区分であり、advisory側の「フィールド名未使用」系（false positiveであることが多いと分かっている）と同列に扱ってはならない:

- 「（プログラムのエラーで停止・要修正）」— 実際に例外で落ちている
- 「（実際に解いてみた結果が想定と違いました）」— 実ソルブ結果が期待と食い違っている
- 「（特殊な条件の扱いに矛盾の疑い）」— optional interval_var の start_of/end_of 等の絶対値誤用など、過去に実害を出した既知バグパターンの検出

これら3区分のいずれかが`blocking`に1件でも含まれる場合、手順6の「診断結果に基づく選択」に進む前に、必ず手順9aと同じ要領で対象ドメインのbaseline/infeasible両シナリオを自分で実際にsolveし、例外が出ないこと・feasible判定が期待通りであることを確認する（詳細は手順6を参照）。この検証を経ずにforce_apply=trueで確定することは禁止する。

この診断結果を、手順6でのアクション選択の根拠として使う。

### 6. 診断結果に基づきAPIを直接叩いて確定する（上限あり）

以前はここを`/api/domain/confirm`のJS直叩きではなく、開いているモーダルの実ボタンを`find`で探して`computer`ツールでクリックする方式にしていたが、この視覚操作（モーダルを開く→ポーリング再開待ち→ボタン探索→クリック）はOptiBuddy側のAPIを一切経由しないため、画面の状態によっては見えない待ち時間の原因になり得る。直接API呼び出しの方が確実で速い。3つの選択肢はいずれも既存の文書化されたエンドポイントにそのまま対応する。

**force_apply=trueを選ぶ前の必須検証（危険度の高い区分がある場合）**: 手順5で「（プログラムのエラーで停止・要修正）」「（実際に解いてみた結果が想定と違いました）」「（特殊な条件の扱いに矛盾の疑い）」のいずれかが`blocking`に含まれていた場合、force_applyを選ぶ前に、手順9aと同じsolve関数を使って対象ドメインのbaseline/infeasible両シナリオを実際に自分でsolveする（confirm前の`job.pending.diffs`はまだDBに反映されていないため、この時点でのsolve確認はconfirm後の確認とは別に必ず行う）。例外が出ず、feasible判定もヒアリング内容と整合していることを確認できて初めてforce_applyを選んでよい。**この検証をせずにforce_apply=trueを選ぶことは、この3区分に該当する指摘がある限り禁止する。** 検証で問題が再現した場合は、下記の「続行」または「取りやめ」を選ぶ。

**このスキルは特定ドメインの個別対応のためではなく、あらゆる登録ジョブに共通する再発防止のためのものである。** 「今回のケースさえ通ればよい」という場当たり対応をせず、上記の区分・検証手順を毎回同じように適用すること。

選択基準（手順5の診断結果に基づく）:

- 危険度の高い3区分に該当する指摘が無い、かつ残りの指摘がfalse positiveである、またはヒアリングで確定済みの設計上の許容差の範囲内と判断できる →
```js
await fetch('http://localhost:5000/api/domain/confirm', {
  method: 'POST', headers: {'Content-Type': 'application/json'},
  body: JSON.stringify({domain_name: '<DomainName>', pending: job.pending, questions: job.pending.questions, answers: '<診断理由>', job_id: '<job_id>', force_apply: true})
}).then(r=>r.json())
```
  （「指摘を残したまま登録する」相当）。判断理由を簡潔にユーザーへ報告する。
- 危険度の高い3区分に該当する指摘があり、かつ上記の事前solve検証で問題が再現した（または検証が済んでいない）→ **デフォルトでこちらを選ぶ。** 該当ファイルを直接編集して自分で直すのではなく、まず`/api/domain/confirm`を`force_apply: false`で叩き、debug_agentによる自動修正（「続行」相当）に委ねる。同一ジョブにつき最大2回まで（「絶対に守ること」節の上限を参照）。ユーザー自身による直接修正の指示がある場合を除き、Claudeが該当ファイルを直接編集するのは、続行を2回試しても解決しない場合の最後の手段とする。
- ヒアリング内容との致命的な矛盾など、続行しても解決の見込みがない場合 →
```js
await fetch('http://localhost:5000/api/domain/cancel', {
  method: 'POST', headers: {'Content-Type': 'application/json'},
  body: JSON.stringify({pending: job.pending})
}).then(r=>r.json())
```
  （「取りやめ」相当、draftファイルの後片付けも兼ねる）。理由をユーザーへ報告する。
- 「続行」を2回選択してもなお同種の指摘が解消しない場合 → これ以上はどちらのAPIも呼ばず、診断結果と試行履歴を整理してユーザーに提示し、指示を仰ぐ。

`/api/domain/confirm`・`/api/domain/cancel`とも`job_id`ベースの非同期ジョブとして即座にレスポンスを返す。**2026-09-05確認: サーバー側の実処理は実測0.056秒で完了しており、待つ必要はほぼない。** confirm/cancelの応答を受け取ったら、間を置かずに手順4のポーリングループ（`javascript_exec`を1回）をそのまま呼び直して結果を追う。「10秒待ってから確認する」といった固定時間のwaitを挟まない。

どの選択をした場合も、選んだ理由と診断内容を簡潔にユーザーへ報告すること（テキストでの報告が主。画面を開く必要はない）。

### 7. 完了後、必要なら実際のUI画面で最終結果を見せる（任意）

Claude in Chromeでの画面確認は必須ではない。ユーザーが画面で見たいと言った場合、または最終結果（登録完了・エラー）を視覚的に確認したい場合のみ、フロントエンド画面を開いてスクリーンショットを1回撮る程度に留める。確認のたびに開閉するのはやめる。

### 8. 登録完了後、Flask再起動を依頼する

コード生成・共有ファイルへのpatchは、Flaskプロセスが読み込み直すまで反映されない。「バックエンドを再起動してフロントエンドをリロードすると変更が反映されます」という完了画面の注記通り、ユーザーにFlask再起動を依頼する。

### 9. 再起動後の動作確認(結果判定・処理時間・巻き添え確認)

再起動確認後、以下を行う。

**a. 実ソルブでの結果確認**

**2026-09-05改訂**: シナリオ一覧の取得と、各シナリオのソルブを、それぞれ別の`javascript_exec`呼び出しに分けない。対象ドメインの全シナリオを1回の呼び出しでまとめて取得・ソルブし、結果一覧を一度に受け取る。

```js
async function solveScenario(id) {
  const dsl = await fetch(`http://localhost:5000/dsl_repository/scenarios/${id}/export`).then(r=>r.json());
  const t0 = performance.now();
  const res = await fetch('http://localhost:5000/baseline?lang=ja', {
    method: 'POST', headers: {'Content-Type':'application/json'},
    body: JSON.stringify({dsl, issueActions: {}})
  });
  const j = await res.json();
  return {id, http_ms: Math.round(performance.now() - t0), feasible: j.solutions?.[0]?.feasible, kpi: j.solutions?.[0]?.kpi};
}
const scs = await fetch('http://localhost:5000/dsl_repository/scenarios').then(r=>r.json());
const mine = (scs.scenarios || scs).filter(s => (s.domain || '').includes('<snake_domain_name>'));
const results = [];
for (const s of mine) {
  results.push(await solveScenario(s.id));
}
JSON.stringify(results)
```

**b. 処理時間の内訳報告**

手順4で取得した `stage_timeline` の隣接タイムスタンプの差分を計算し、「Stage1a分類: X秒」「Stage2コード生成: Y秒」等の内訳をユーザーに報告する。Stage2（本体コード生成、LLM呼び出し）が全体の大半を占めるのが通常で、これはAPI直叩きでもUI操作でも変わらない固定コスト。UI操作由来の追加オーバーヘッド（スクリーンショット往復等）と区別して説明すること。

**c. 共有ファイルへのpatchがあった場合、巻き添え確認**

もし今回のdiffsに `Backend/solvers/base/issue_rules.py` 等の共有ファイルへのpatchが含まれていた場合、他の既存ドメインのシナリオを1〜2件実際にsolveしてみて、import破損等の巻き添え事故が起きていないか確認する（`/dsl_repository/scenarios` で他ドメインのIDを探し、手順aと同じ要領でsolveする）。

## 既知の落とし穴（要注意）

- **ヒアリングシートのテキストをbase64等で手動チャンク分割してブラウザ側に継ぎ足す方式は、文字数チェックが全て一致していても内容が破損することがある（発生実績あり）。** 手順3に記載の「1回のスクリプト生成＋1回の転記＋ハッシュ検証」方式を必ず使うこと。2回失敗したら即座にUIテキストエリアへの直接貼り付け方式に切り替える。時間を浪費する前に早めに見切りをつけること。
- **共有ファイル(`solvers/base/issue_rules.py`等)へのLLM生成PATCHが、存在しないモジュールへのimportを混入させることがある**（例: 実在しない `i18n.xxx_messages` モジュール）。py_compileは構文チェックのみで検知できないため、`apply_domain_files()` には実importチェック（`_run_backend_import_check()`）が組み込まれている。この種のバグが起きると、無関係な既存ドメインのソルバー登録まで巻き添えで壊れる。
- **「infeasibleシナリオは必ずfeasible=Falseになるべき」という一律の期待値チェックは、未割当を許容する設計のドメイン（ヒアリングで「人数不足を許容し、できるだけ近づける」等と確定しているもの）ではfalse positiveになる**。この種のドメインはCPモデル構造上（optional interval_var等）真のinfeasibleを作れないため、資源を逼迫させても`feasible=True`＋低`coverage_rate`という形になるのが正しい。Gate2側はこの場合、`coverage_rate`が閾値（0.8）以下ならfeasible=Trueでも許容する。
- **`llm/llm_client.py`の`extract_json()`は、LLM応答に複数のマークダウンフェンス（```...```）が含まれる場合、全フェンスを順に試す実装になっている。** ヒアリングシート自体に```コードフェンスが含まれるドメインで、LLM応答が説明用フェンス＋本題のJSONフェンスの2段構成になるケースへの対策。
- **Flask再起動を忘れると、コード修正やpatchの効果が反映されないまま古い挙動でテストしてしまう**。バックエンド側の`.py`ファイルを編集した直後は必ず再起動を依頼してから再検証する。
- **debug_agent（続行時のAIエージェント自動修正）は`write_files_for_dynamic_check()`が書き出した「そのドメイン専用の新規ファイルのみ」しか編集できない**（`allowed_paths`で制限済み）。共有ファイルへの変更は`apply_domain_files()`の`approved_patches`経由でのみ行われ、そこでpy_compile検証＋実importチェックの両方を通る。
- **自律実行時に「続行」を選び続けると、debug_agentの内部max_turns（`loop_exhausted`）とは別に、本スキル側の呼び出しレベルでも無限に繰り返すリスクがある**。本スキル側の上限（同一ジョブにつき最大2回）は、debug_agent内部のmax_turnsとは独立したもう一段の歯止めとして必ず守ること。
- **手順6の確認/取りやめ判断は、実際のUI画面のボタンをClaude in Chromeで探してクリックする方式ではなく、直接API呼び出しで行う。** 画面操作はOptiBuddy側のAPIを一切経由しないため、画面の状態によっては見えない待ち時間の原因になり得る上、常に画面が見られているとも限らないため。

## スタイル・トーン

CP/OR最適化の専門家として発言する。指摘事項は必ず実データ（生成コード・シナリオJSON・実ソルブ結果）を見て技術的に診断してから、平易な言葉でユーザーに推奨案を提示する。自律的にアクションを選択した場合も、何を・なぜ選んだかを簡潔に報告する。アーキテクチャ変更や共有ファイルの修正内容そのものは、`/api/domain/confirm`経由の自律実行の範囲内であれば都度の承認なしに進めてよいが、それ以外の場面（初回のジョブ起動、上限到達後の追加対応）ではユーザーの明示的な承認を得てから行う。作業に時間がかかりすぎている・同じ方式を繰り返し失敗している場合は、早めに見切りをつけてユーザーに率直に報告する。ユーザーへの全ての報告は「ユーザー向け進捗報告のルール」節のステップ番号・メッセージレベル表示・業務用語ルールに従うこと。専門用語やAPI名・ファイルパス・関数名・スタックトレース等の内部情報は、ユーザーが明示的に技術詳細を求めない限り出さない。
