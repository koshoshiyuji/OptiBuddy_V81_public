# CLAUDE.md — 環境依存の注意点（要点のみ）

> ここは次セッションが同じ罠を再び踏まずに済むための要点だけに絞る。
> 業務ロジック・DSL設計は `docs/OptiBuddy_Development_Guide.md` を参照。

## Flask再起動

`Backend/*.py`編集は稼働中プロセスに自動反映されない。修正後は必ず再起動を依頼する。

## ドメイン登録・削除

- ヒアリングシートのブラウザへの転記: base64/チャンク分割手動継ぎ足しは禁止
  （内容破損の実績あり）。1回生成＋1回転記＋SHA-256ハッシュ検証方式を使う
  （`optibuddy-domain-registration`スキル参照）。
- 削除は`Backend/dsl_repository/cleanup_domain_cli.py <name> --yes`。DB・ドメイン
  専用ファイル・ディスパッチ登録・TAG_MAPは削除されるが、**`Backend/i18n/
  <domain>_messages.py`は削除対象外**（手動rm要）。

## Gate2静的チェックの既知の弱点

`unused_in_solver`系チェック（converter出力キーをsolver.pyが未参照、という指摘）は
AST上の変数参照カウントに基づくため、辞書オブジェクトを丸ごとパススルーしている
ケース（キーは実際には出力まで伝播している）を誤検知することがある。指摘が出たら
実際のデータフロー（`solver_output`まで届くか）を追って確認してから判断する。
