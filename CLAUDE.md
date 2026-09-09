# CLAUDE.md — 環境依存の注意点（要点のみ）

> 詳細な経緯・原因・修正内容は `../OptiBuddy_V81_devnotes/ENGINEERING_LOG.md` を参照。
> ここは次セッションが同じ罠を再び踏まずに済むための要点だけに絞る。
> 業務ロジック・DSL設計は `docs/OptiBuddy_Development_Guide.md` を参照。

## リポジトリ構成（単一リポジトリ、GitHub直push）

`optibuddy_test_root` が唯一の開発リポジトリ。originはGitHub `koshoshiyuji/OptiBuddy_V81_public`
を直接指す（2026-09-09にリポジトリ構成を変更。旧`OptiBuddy_V81_public`・`OptiBuddy_V81`ローカル
リポジトリは廃止・削除予定）。GitHubへの`git push`はサンドボックスからは不可
（`device_bash`に`github.com`への到達性なし）。Koshoshi自身の端末で行う運用。

## クラウドサンドボックス（Cowork経由・device_bash）限定の注意点

**以下2点はClaude(LLM)がCowork等のクラウドサンドボックス経由（`device_bash`でこの
Macのフォルダをマウントして作業する形）で動いている場合のみ該当する。Koshoshi自身
のネイティブ端末（実機・ライセンス済みCPLEX環境）での作業には当たらない。**

- gitの癖: `.git/index.lock`残留 → `device_request_delete_permission`でそのフォル
  ダの削除許可を得てから`rm -f`。gitの`user.name`/`user.email`が未設定 →
  `git log -3 --format="%an <%ae>"`で既存コミット作者を確認し、**リポジトリローカ
  ルに**（`--global`は使わない）同じ値を設定。
- CP Optimizerの実行制約: `docplex`はpip installできるが、実際にsolve()する
  `cpoptimizer`実行ファイル本体（別ライセンス）はクラウドサンドボックスに無い。
  検証できるのは`mdl.add()`がPython例外を出さないことまで。**「モデル構築が通っ
  た」を「直った」と報告しない** — 実際に正しく解けるかはKoshoshi自身の実機（ラ
  イセンス環境）での確認が必要。Koshoshi自身のネイティブ端末で作業する場合は
  `cpoptimizer`が存在するため、この制約は無く通常通りsolve()まで検証してよい。
  詳細: ENGINEERING_LOG.md 2026-08-30追記1。

## Flask再起動

`Backend/*.py`編集は稼働中プロセスに自動反映されない。修正後は必ず再起動を依頼する。

## ドメイン登録・削除

- ヒアリングシートのブラウザへの転記: base64/チャンク分割手動継ぎ足しは禁止
  （内容破損の実績あり）。1回生成＋1回転記＋SHA-256ハッシュ検証方式を使う
  （`optibuddy-domain-registration`スキル参照）。
- 削除は`Backend/dsl_repository/cleanup_domain_cli.py <name> --yes`。DB・ドメイン
  専用ファイル・ディスパッチ登録・TAG_MAPは削除されるが、**`Backend/i18n/
  <domain>_messages.py`は削除対象外**（手動rm要）。詳細: ENGINEERING_LOG.md
  2026-08-30追記3。

## Gate2静的チェックの既知の弱点

`unused_in_solver`系チェック（converter出力キーをsolver.pyが未参照、という指摘）は
AST上の変数参照カウントに基づくため、辞書オブジェクトを丸ごとパススルーしている
ケース（キーは実際には出力まで伝播している）を誤検知することがある。指摘が出たら
実際のデータフロー（`solver_output`まで届くか）を追って確認してから判断する。
詳細: ENGINEERING_LOG.md 2026-08-30追記2・追記3。
