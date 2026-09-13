[English](./README.en.md)

# OptiBuddy

業務の現実をDSL（Domain Specific Language／業務記述データ）として定義すると、AIとソルバーが協働して最適スケジュールや構成を決定する、汎用業務最適化ツールです。

## システム概要

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
   ソルバー実行（CP Optimizer / CP-SAT）
        ↓
ソルバー出力DSL（スケジュール・問題点・複数解）
        ↓
   OptiBuddyコア → UI DSLへフィード
        ↓
最適作業スケジュール + 問題点の自動検出 + AIによる改善提案
```

## 主な特徴

- **業務登録はAIとの対話だけ** — ヒアリングシートに業務の現実を自社の言葉で記入すると、AIが既存ドメインとの類否を判定し、コード生成・DB登録まで自動で行う。既存ドメインの変種なら数十秒、完全新規ドメインでも数分で新しい業務が使えるようになる。数式やコーディングの知識は不要。
- **多数の業務ドメインを標準搭載** — コンテナターミナルの荷役計画、配送ルート最適化、シフト管理、施設配置などをカバー。RCPSP・CVRP・CFLP・ナップサック問題・ビンパッキングなど数理的に確立された基底問題を土台に、業務ごとの追加ルールをextensionsとして組み合わせる。
- **問題点の自動検出とAIによる改善提案** — 解を計算した後、issue_rulesが制約違反・非効率を自動チェックし、AIチャットが改善案を提示する。ユーザーは「適用する」か「やめる」かを答えるだけ。
- **ソルバーエンジンを選べる** — 既定はGoogle OR-Tools CP-SAT（Apache 2.0、追加インストール不要）。IBM CPLEX CP Optimizer / CPLEX MIP（`docplex`経由）にも切り替え可能（別ライセンス、任意インストール）。CPLEX Community Editionのモデルサイズ上限を超えるMIP系ドメインはHiGHS（OSS）へ自動フォールバックする。
- **Gantt/Grid/KPIによる結果確認** — 解いた結果はUI DSLを通じてGanttチャート・グリッド・KPIとして表示される。

## 技術スタック

- フロントエンド: React + TypeScript + Vite
- バックエンド: Python + Flask
- 最適化エンジン: Google OR-Tools CP-SAT（既定）/ IBM CPLEX CP Optimizer・CPLEX MIP（`docplex`、任意）/ HiGHS（MIP自動フォールバック）
- ドメイン自動生成: Claude API（LLMパイプライン、Stage1a → Stage1b / Stage2）

## セットアップ

インストール手順・起動方法・サンプルデータの投入方法は [Install.md](./Install.md) を参照してください。

## ドキュメント

- [OptiBuddy_User_Manual.md](./docs/OptiBuddy_User_Manual.md) — 操作方法（業務登録・シナリオ確認・AIチャットでの改善提案の使い方）
- [OptiBuddy_Development_Guide.md](./docs/OptiBuddy_Development_Guide.md) — 開発者向け（アーキテクチャ・DSL設計・extensionsの仕組み）
- [CSPLIB_REFERENCE.md](./docs/CSPLIB_REFERENCE.md) — 各ドメインが基づく数理的な基底問題の参照

## 関連記事

- [なぜLLMに最適化コードを書かせず、DSLを書かせるのか](https://zenn.dev/koshoshi/articles/609309957766e9)（Zenn）
- [LLMに数理最適化モデルを書かせるときの「もっともらしい間違い」をどう検出するか](https://qiita.com/koshoshiyuji/items/af3eb69e3e81e586b0c6)（Qiita）

## ライセンス

OptiBuddy本体は [Business Source License 1.1](./LICENSE)（BSL 1.1）で公開しています。既存のどのドメイン・自分で定義したドメインを問わず、自社の業務のための本番利用はどなたでも無償です。制限がかかるのは、IT/コンサルティングサービスを生業とする第三者が複数の顧客に反復的に有償サービス（ドメイン構築代行・ホスティング・システム連携など）を提供する場合のみで、その場合は事前にLicensorとの商用ライセンス契約が必要です。具体例・早見表は [LICENSE-FAQ.md](./LICENSE-FAQ.md) を参照してください。BSL 1.1は`LICENSE`記載のChange Dateをもって、Licensed Work全体がApache License 2.0へ自動的に切り替わります。

最適化エンジンとして任意で利用できるIBM CPLEX / CP Optimizer（`docplex`）は別ライセンス（IBM）で、OptiBuddy本体には同梱・再配布していません。既定エンジンはOR-Tools CP-SAT（Apache 2.0、追加インストール不要）です。エンジン切替の詳細は [Install.md](./Install.md) を参照してください。

依存OSSライブラリのライセンス一覧は [THIRD_PARTY_NOTICES.md](./THIRD_PARTY_NOTICES.md) を参照してください。
