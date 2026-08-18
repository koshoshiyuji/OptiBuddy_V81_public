OptiBuddy (2026/06/24)

システム概要

OptiBuddyは、**業務の現実をDSL（Domain Specific Language／業務記述データ）として定義すると、AIとソルバーが協働して最適スケジュールや構成を決定するアプリケーションを提供する、汎用業務最適化ツール**です。

特定の業界・業務に固定された専用ツールではなく、「タスク・リソース・制約・目的関数」という共通構造（RCPSP：資源制約付きスケジューリングなどの最適化/制約問題）を基盤に、業務ごとの追加ルールを**extensions（拡張モジュール）**として組み合わせることで、コンテナターミナルの荷役計画からイベント運営のスタッフ配置まで、幅広い業務領域に対応します。

```
業務DSL（業務の現実を記述）
  problem_class + extensions
        ↓
   OptiBuddyコア（変換・制御）
        ↓                  ↓
ソルバー入力DSL          UI DSL
（変数・制約・目的関数）   （Gantt/Grid/KPIの構造）
        ↓
   ソルバー実行（CP Optimizer）
        ↓
ソルバー出力DSL（スケジュール・問題点・複数解）
        ↓
   OptiBuddyコア → UI DSLへフィード
        ↓
最適作業スケジュール + 問題点の自動検出 + AIによる改善提案
```

## セットアップ

インストール手順・起動方法・サンプルデータの投入方法は [Install.md](./Install.md) を参照してください。

## ライセンス

OptiBuddy本体は [Business Source License 1.1](./LICENSE)（BSL 1.1）の下で公開しています。無償で本番利用できるのは以下7ドメインのみです。

- `yard` / `truck_dispatcher` / `nurse_shift_weekly_cap` / `car_sequencing` / `meeting_room` / `store_site` / `line_changeover_scheduler`

上記以外のドメインは、コードの閲覧・改変・検証目的での利用（非本番利用）は自由ですが、本番利用には別途商用ライセンス契約が必要です。「本番利用」の定義や具体例は [LICENSE-FAQ.md](./LICENSE-FAQ.md) を参照してください。BSL 1.1は`LICENSE`記載のChange Dateをもって、Licensed Work全体（無償・有償ドメインを問わず）がApache License 2.0へ自動的に切り替わります。

最適化エンジンとしてIBM CPLEX / CP Optimizer(`docplex`)を任意で利用できますが、
これは別ライセンス(IBM)です。`Backend/requirements.txt`だけをインストールした
場合、CPLEX/docplexは含まれません(`Backend/requirements-cplex.txt`が別途必要)。

- MIP系ドメインは、CPLEXのCommunity Edition(評価版)のモデルサイズ上限を
  超えた場合、オープンソースのHiGHSへ自動フォールバックします。
- CP Optimizer系ドメインの一部は、`config.solver_engine`で"cpo"(IBM CPLEX CP
  Optimizer)と"cpsat"(Google OR-Tools CP-SAT、`cpmpy`経由)を切り替えられます。
  未指定時のデフォルトは"cpsat"のため、対応ドメインでは`Backend/requirements.txt`
  だけのインストールでもCPLEX無しでそのまま動作します(`.env`の
  `DEFAULT_SOLVER_ENGINE`でデプロイ時のデフォルトを変更可能)。CPLEXで
  解かせたい場合は`solver_engine="cpo"`を指定してください
  (`Backend/requirements-cplex.txt`のインストールが別途必要)。
  例外として、Yard・NurseShiftWeeklyCap・TruckDispatcherの3ドメインは
  decomposer前提の実装のため、この設定に関わらずCPLEX(Community Edition)が
  必須です。

商用利用時のIBM CPLEXの利用条件は、別途IBMとの契約・利用規約をご確認ください。
