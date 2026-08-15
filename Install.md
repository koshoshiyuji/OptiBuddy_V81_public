# OptiBuddy セットアップガイド

## 概要

OptiBuddy は「業務担当者が AI と対話しながら最適化問題を定義し、短期間でプロトタイプを稼働させる」ことを目標に設計された最適化プラットフォームです。

- **フロントエンド**: React + Vite（ブラウザで動作）
- **バックエンド**: Python + Flask（最適化エンジン）
- **最適化エンジン**: IBM CPLEX CP Optimizer（`docplex`経由）／一部ドメインはHiGHS（MIP）にも対応

本リポジトリは Apache License 2.0 で公開しています（詳細はリポジトリルートの `LICENSE` を参照）。

---

## 必要要件

| ソフトウェア | バージョン | 備考 |
|---|---|---|
| Python | 3.10以上 | |
| Node.js | 18以上 | |

> OptiBuddy本体からCPLEX/docplexは分離されています。CP Optimizer（IBM CPLEX、Community Edition評価版が`cplex`パッケージに同梱）を使うドメインを有効にする場合は、`requirements.txt`に加えて`requirements-cplex.txt`を追加でインストールしてください（手順2参照）。IBM CPLEX Optimization Studioを別途インストールする必要はありません。

### OS別の補足

**Windows**: Python・Node.jsともに公式インストーラーを使用してください。パスの設定が必要な場合があります。

**Mac**: Homebrewでのインストールを推奨します（`brew install python node`）。

**Linux（Ubuntu/Debian）**: `apt install python3 python3-pip nodejs npm` で導入可能です。

---

## インストール手順

### 1. リポジトリの取得

```bash
git clone <repository-url> optibuddy
cd optibuddy
```

### 2. バックエンドのセットアップ

```bash
cd Backend
python -m venv venv

# Windows
venv\Scripts\activate
# Mac / Linux
source venv/bin/activate

pip install -r requirements.txt

# CP Optimizer/CPLEXを使うドメインを有効にする場合（ほとんどのドメインで必要）
pip install -r requirements-cplex.txt
```

インストール確認:
```bash
python -c "import cplex; print('CPLEX OK')"
python -c "import docplex; print('docplex OK')"
```

`requirements-cplex.txt`を入れなかった場合、上記コマンドは`ModuleNotFoundError`になります（CPLEX/docplexが不要な範囲でbase installのみ試す場合はこれで問題ありません）。

### 3. フロントエンドのセットアップ

```bash
cd Frontend
npm install
```

### 4. 環境変数ファイルを作成

`Backend/`には2種類のテンプレートがあります。用途に応じてどちらかを選んでコピーしてください。両者の違いは`EVOLUTION_LOG_ENABLED`（Q&A回答内容・選択したプランなどのユーザー操作ログを記録する機能）の既定値のみです。

| テンプレート | `EVOLUTION_LOG_ENABLED`既定 | 想定用途 |
|---|---|---|
| `.env.example` | `1`（有効） | 開発者向け。自分の操作ログを残したい場合 |
| `.env.customer.example` | `0`（無効） | 第三者に使ってもらう場合。相手の操作内容を自動収集しない構成 |

```bash
cd Backend
cp .env.example .env
# もしくは
cp .env.customer.example .env
```

`.env`を開き、必要な値を設定してください。

- `ANTHROPIC_API_KEY`等のLLM APIキー。「新規業務登録」（AIによるドメイン自動生成）だけでなく、既存ドメインでInfeasible（解なし）になったシナリオに対して「制約見直し／緩和案」を求める`/relax`機能でもLLMを呼び出すため、いずれかを使う場合は設定が必要です（未設定でも、シナリオを選んで解くだけの基本的なソルバー機能は動作します）
- `CPLEX_PATH` / `CPOPTIMIZER_PATH`（通常は空欄のままで問題ありません。`requirements-cplex.txt`同梱のCommunity Edition cpoptimizerが使われます）
- `CPLEX_LICENSED`（商用ライセンス版を別途購入・登録した場合のみ`1`に変更）

> ⚠️ `.env`はGitにコミットしないでください（`.gitignore`で除外済みです）。`.env.example`／`.env.customer.example`はダミー値のままコミットして構いません。

### 5. サンプルデータ（シナリオ）の投入

このリポジトリには**全26ドメイン分**のソルバーコードが含まれています。各ドメインをWebUIで試すには、`sample_domain_seeds/`に同梱されている**公開用サンプルデータ**をDBに投入してください。

> ℹ️ サンプルデータはすべて架空データ、または CVRPLIB・INRC 等の公開ベンチマークデータのみで構成されています（実在の顧客データは一切含まれません）。

```bash
cd Backend/dsl_repository

# 例: TruckDispatcherと看護師シフト編成の2ドメインを試す場合
python3 import_domain.py truck_dispatcher nurse_shift_weekly_cap

# 全26ドメインをまとめて投入する場合
python3 import_domain.py $(ls sample_domain_seeds | sed 's/\.json$//')
```

初回実行時、`Backend/dsl_repository/optibuddy.db`が存在しなければ自動的に新規作成されます（`schema.sql`適用）。このコマンドは何度実行しても安全です（非破壊・追記のみ。同じドメインを再指定しても重複投入されません）。

投入可能なドメイン一覧（`sample_domain_seeds/`のファイル名 = ドメイン名）:

| ドメイン名 | 内容 |
|---|---|
| `yard` | コンテナターミナルのヤード（コンテナ配置）計画 |
| `truck_dispatcher` | トラックの車両割当・配送ルート最適化（CVRP-TW） |
| `nurse_shift_weekly_cap` | 看護師シフト編成（週間夜勤回数上限つき） |
| `meeting_room` | 会議室の予約割当 |
| `store_site` | 店舗など拠点の立地選定 |
| `line_changeover_scheduler` | 工場ラインの切替スケジューリング |
| `car_sequencing` | 自動車組立ラインの車両生産順序決定 |
| `auction_winner_selector` | オークションの落札者選定 |
| `capital_project_selector` | 設備投資案件選定（0-1ナップサック型） |
| `crew_duty_scheduler` | 乗務員（クルー）の勤務スケジューリング |
| `depot_route_planner` | デポ発着の配送ルート計画 |
| `energy_cost_aware_scheduler` | 電力コストを考慮した生産スケジューリング |
| `inventory_replenishment_planner` | 在庫補充計画 |
| `lot_sizing_scheduler` | ロットサイズ決定・生産スケジューリング |
| `medical_appointment_scheduler` | 医療予約スケジューリング（医師・処置室の割当） |
| `medical_appointment_sequence_scheduler` | 医療予約の順序決定 |
| `mystery_shopper_scheduler` | 覆面調査員の訪問スケジューリング |
| `nursing_workload_balance` | 看護師間の患者負荷（アキュイティ）バランス調整 |
| `patient_transport_planner` | 患者搬送計画 |
| `portfolio_overlap_designer` | ポートフォリオ構成の最適化 |
| `production_line_sequencing` | 生産ラインの製造順序決定 |
| `shift_rotation_scheduler` | シフトローテーション編成 |
| `steel_mill_slab_design` | 製鋼スラブ設計 |
| `tank_allocation_planner` | タンクへの液体ロット割当 |
| `transport_cost_minimizer` | 輸送コスト最小化 |
| `vessel_deck_loader` | 船舶甲板へのコンテナ積み付け最適化 |

> 存在しないドメイン名を指定した場合は「⚠️ シードファイルが見つかりません」と表示され、他の指定ドメインの投入は続行されます。

---

## 起動方法

### バックエンドの起動

```bash
cd Backend
# 仮想環境が有効な状態で
python app.py
```

起動確認: ブラウザまたはcurlで `http://localhost:5000/health` にアクセスし、`{"status": "ok"}` が返ることを確認してください。

### フロントエンドの起動

別のターミナルで:

```bash
cd Frontend
npm run dev
```

ブラウザで `http://localhost:5173` を開いてください。

---

## 動作確認

1. ブラウザで `http://localhost:5173` を開く
2. ホーム画面のシナリオ一覧から、手順5で投入したシナリオを選択
3. 数秒後にGanttチャートやKPIカードなどの結果が表示されれば成功

---

## LLMを使う機能について（新規業務登録・緩和案提案）

`.env`に`ANTHROPIC_API_KEY`を設定すると、以下の2つの機能が使えます。

- **新規業務登録**: ホーム画面の「新規業務登録」からヒアリング内容を入力し、AIに新しい最適化ドメインを自動生成させる機能。`docs/hearing_template.md`にヒアリングシートのテンプレートがあります。
- **制約見直し／緩和案提案（`/relax`）**: シナリオがInfeasible（解なし）になった際、画面上の「制約見直し」タブからAIに緩和案を提案させる機能。

> ⚠️ いずれも実際にLLM APIを呼び出し課金が発生します。新規業務登録では、生成されたコードの動作確認（Gate 2）を経てから使うことを推奨します。

---

## ポート設定

| サービス | デフォルトポート | 変更方法 |
|---|---|---|
| バックエンド（Flask） | 5000 | 環境変数`PORT`を設定して起動 |
| フロントエンド（Vite） | 5173 | `vite.config.ts`の`server.port`を変更 |
| フロントエンド→バックエンド接続先 | http://localhost:5000 | `Frontend/src/domain/constants.ts`の`SOLVER_CONFIG.API_BASE`を変更 |

---

## トラブルシューティング

### `import cplex` でエラーが出る

`requirements-cplex.txt`がインストールされていない可能性があります。手順2を再実行し、仮想環境が有効な状態で`pip install -r requirements.txt -r requirements-cplex.txt`が正常終了しているか確認してください。

### 大きめの問題規模で解決に失敗する／一部の割り当てが解決しない

`requirements-cplex.txt`のCPLEXはCommunity Edition（無償版）で、モデルサイズ上限（MIP: 1000変数/1000制約、CP Optimizer: 探索空間2^1000）があります。実データ規模がこの上限を超えると失敗することがあります。

- **MIP系ドメイン**: 上限を検知すると自動的にオープンソースのHiGHSへフォールバックします（追加設定不要）。
- **CP Optimizer系ドメイン**: TruckDispatcherのみ、LNS（Ruin-and-Recreate）で自動的に問題を小分けにして再試行しますが、内部的にはCP Optimizerを呼び続けるため、CPLEX自体が使えない状態の解決策にはなりません。他のCP Optimizer系ドメインには現時点で自動フォールバックがありません。

いずれの場合も、商用ライセンス版への切替で上限自体を無くすことは可能です（`.env`の`CPLEX_LICENSED=1`と合わせて設定）。

### `pip install`でエラーが出る

Pythonのバージョンが古い可能性があります。`python --version`で確認してください。

### フロントエンドからバックエンドに接続できない

- バックエンドが起動しているか確認（`http://localhost:5000/health`）
- CORSエラーの場合は`Backend/app.py`内の`CORS(app)`設定を確認

### シナリオ一覧に何も表示されない

手順5のシード投入がまだの可能性があります。`python3 import_domain.py <ドメイン名>`を実行してください。

### シードファイルが見つからないと表示される

`sample_domain_seeds/<ドメイン名>.json`が存在するか、ドメイン名のスペルを確認してください。

---

## ディレクトリ構成（参考）

```
optibuddy/
├── Backend/
│   ├── app.py                     # Flask エントリーポイント
│   ├── requirements.txt
│   ├── requirements-cplex.txt     # CP Optimizer/CPLEXを使うドメイン向け（任意extra）
│   ├── domain_generator.py        # ドメイン自動生成（AIによる新規業務登録）
│   ├── dsl_repository/
│   │   ├── import_domain.py       # 手順5で使うシード投入スクリプト
│   │   ├── sample_domain_seeds/   # 公開用サンプルシード（架空データ/公開ベンチマークのみ）
│   │   ├── scenarios/             # 各ドメインの動作検証用シナリオ
│   │   ├── schema.sql
│   │   └── optibuddy.db           # 初回実行時に自動生成
│   ├── dsl_transformer/           # 4DSL変換層
│   ├── i18n/                      # Backend側動的メッセージのi18n辞書
│   ├── solvers/                   # ドメインごとのソルバー実装
│   └── llm/
└── Frontend/
    ├── package.json
    └── src/
        ├── domain/
        │   └── constants.ts
        └── i18n/                  # Frontend側静的UI文言のi18n辞書
```

---

## ライセンス・注意事項

- OptiBuddy本体はApache License 2.0で公開しています（リポジトリルートの`LICENSE`参照）。
- IBM CPLEX/CP Optimizer（`cplex`/`docplex`、`requirements-cplex.txt`）はOptiBuddy本体とは別のライセンスです。商用利用の場合はIBMとの契約を確認してください。
- Anthropic API（AIチャット機能）の利用には別途APIキーと課金が必要です。
