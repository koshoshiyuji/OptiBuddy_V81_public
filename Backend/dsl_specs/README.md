# OptiBuddy DSL仕様書ディレクトリ

このディレクトリには、OptiBuddyの4DSLアーキテクチャに関する仕様書が格納されています。

---

## 📚 DSL仕様書一覧

### 基底DSL

| ファイル | 説明 | バージョン |
|---------|------|-----------|
| [rcpsp_base_v1.md](./rcpsp_base_v1.md) | RCPSP基底DSL仕様書 | 1.0 |

RCPSP（Resource-Constrained Project Scheduling Problem）基底DSLは、すべてのドメイン固有DSLの基盤となる最小限の問題定義です。

### ソルバーDSL

| ファイル | 説明 | バージョン |
|---------|------|-----------|
| [solver_input_dsl_v1.md](./solver_input_dsl_v1.md) | Solver Input DSL仕様書 | 1.0 |
| [solver_output_dsl_v1.md](./solver_output_dsl_v1.md) | Solver Output DSL仕様書 | 1.0 |

**Solver Input DSL**: 業務DSLからソルバー（CP Optimizer）への入力形式を標準化した中間表現  
**Solver Output DSL**: ソルバーの実行結果を標準化された形式で表現した中間表現

### UI DSL

| ファイル | 説明 | バージョン |
|---------|------|-----------|
| [ui_dsl_v1.md](./ui_dsl_v1.md) | UI DSL仕様書 | 1.0 |

**UI DSL**: Solver Output DSLをフロントエンド表示用の構造に変換した最終表現。Gantt、Grid、KPI、Issueなどすべてのビューに必要なデータ構造を提供

### Extensions（拡張機能）

| ファイル | 説明 | カテゴリ | バージョン |
|---------|------|---------|-----------|
| [extensions/physical_space.md](./extensions/physical_space.md) | 物理的な3次元空間制約 | constraint | 1.0 |

Extensions は RCPSP基底DSLに対して追加される機能モジュールです。各extensionは独立して定義され、組み合わせて使用できます。

---

## 🏗️ 4DSLアーキテクチャ概要

OptiBuddyは以下の4層DSLアーキテクチャを採用しています：

```
┌─────────────────────────────────────────────────┐
│  ① 業務DSL (Business DSL)                       │
│  - RCPSP Base + Extensions                      │
│  - ドメイン固有の概念（コンテナ、ヤード等）      │
└─────────────────┬───────────────────────────────┘
                  │
                  ↓ 変換（business_to_solver.py）
┌─────────────────────────────────────────────────┐
│  ② ソルバー入力DSL (Solver Input DSL)           │
│  - タスク、リソース、制約の明示的表現            │
│  - ソルバー非依存の中間表現                      │
│  - 仕様書: solver_input_dsl_v1.md               │
└─────────────────┬───────────────────────────────┘
                  │
                  ↓ ソルバー実行（CP Optimizer等）
┌─────────────────────────────────────────────────┐
│  ③ ソルバー出力DSL (Solver Output DSL)          │
│  - スケジュール結果、メトリクス、イシュー        │
│  - 複数解候補（solution profiles）              │
│  - 仕様書: solver_output_dsl_v1.md              │
└─────────────────┬───────────────────────────────┘
                  │
                  ↓ 変換（solver_to_ui.py）
┌─────────────────────────────────────────────────┐
│  ④ UI DSL (UI DSL)                              │
│  - Gantt、Grid、KPI表示用データ構造              │
│  - 時系列スナップショット、ハイライト状態        │
│  - 仕様書: ui_dsl_v1.md                         │
│  - Frontend/src/domain/types.ts                 │
└─────────────────────────────────────────────────┘
```

---

## 📖 読み方ガイド

### 初めての方

1. **[rcpsp_base_v1.md](./rcpsp_base_v1.md)** から読み始めてください
   - OptiBuddyの基盤となる概念を理解できます
   - タスク、リソース、先行関係などの基本要素を学べます

2. **[extensions/physical_space.md](./extensions/physical_space.md)** でextensionの仕組みを理解
   - 基底DSLをどのように拡張するかの具体例
   - Yard Planning特有の物理制約の実装方法

3. **[solver_input_dsl_v1.md](./solver_input_dsl_v1.md)** でソルバー連携を理解
   - 業務DSLがどのようにソルバー入力に変換されるか
   - 制約の明示化プロセス

4. **[solver_output_dsl_v1.md](./solver_output_dsl_v1.md)** で結果の構造を理解
   - ソルバーが返す解の形式
   - イシュー検出の仕組み

5. **[ui_dsl_v1.md](./ui_dsl_v1.md)** でUI表示の仕組みを理解
   - Solver Output DSLからUI DSLへの変換
   - Gantt、Grid、KPI、Issueビューへのマッピング
   - 時系列スナップショット生成

### 実装者向け

- **新しいextensionを追加する場合**:
  1. `extensions/` ディレクトリに新しいMarkdownファイルを作成
  2. [extensions/physical_space.md](./extensions/physical_space.md) をテンプレートとして使用
  3. スキーマ追加、制約定義、ソルバーマッピング、UI変換を記述

- **新しいソルバーを追加する場合**:
  1. [solver_input_dsl_v1.md](./solver_input_dsl_v1.md) の制約タイプを確認
  2. 各制約タイプを新しいソルバーの制約に変換する実装を追加
  3. [solver_output_dsl_v1.md](./solver_output_dsl_v1.md) の形式で結果を返す

---

## 🔄 DSL変換フロー

### 業務DSL → Solver Input DSL

```python
# Backend/dsl_transformer/business_to_solver.py
def convert_business_to_solver(business_dsl: dict) -> dict:
    """
    業務DSL（RCPSP + Extensions）をSolver Input DSLに変換
    
    変換内容:
    - containers → tasks（PICK, LOAD, DISCHARGE, PLACE等）
    - extensions → constraints（precedence, physical_stack_order等）
    - config → objective（minimize_makespan + penalty）
    """
    pass
```

### Solver Output DSL → UI DSL

```python
# Backend/dsl_transformer/solver_to_ui.py
def convert_solver_to_ui(solver_output: dict) -> dict:
    """
    Solver Output DSLをUI DSL（Gantt、Grid、KPI）に変換
    
    変換内容:
    - tasks → UI Task[]（Gantt表示用、from/to Location追加）
    - containers + tasks → Snapshots（時系列Grid表示）
    - metrics + issues → KPI（ダッシュボード）
    - issues → UI Issue[]（Issue View用）
    
    詳細は ui_dsl_v1.md を参照
    """
    pass
```

---

## 🎯 Extension開発ガイドライン

新しいextensionを開発する際の原則：

### 1. スキーマ追加のみ
- 既存フィールドの削除・変更は禁止
- 新しいフィールドを `schema_fragment` として追加

### 2. 制約の追加
- 基本制約を緩和せず、追加制約のみ定義
- `solver_mapping` で制約の実装方法を明示

### 3. 後方互換性
- Extension未使用時は基底DSLとして動作すること
- Extensionフィールドが存在しない場合のデフォルト動作を定義

### 4. 命名規則
- Extension名: `snake_case`（例: `physical_space`, `shift`）
- カテゴリ: `constraint`, `resource`, `objective`, `ui` のいずれか

---

## 📝 Extension一覧（計画中）

| Extension名 | カテゴリ | 説明 | ステータス |
|------------|---------|------|-----------|
| `physical_space` | constraint | 3次元物理空間制約 | ✅ 実装済み |
| `phase_separation` | constraint | DISCHARGE→LOAD分離制約 | ✅ 実装済み |
| `crane_interference` | constraint | クレーン干渉制約 | ✅ 実装済み |
| `shift` | constraint | シフト・休憩時間制約 | ✅ 実装済み |
| `attribute_zones` | constraint | 属性ゾーン制約（REEFER, IMO, OOG） | ✅ 実装済み |
| `flexible_machine_assignment` | resource | FJSP用の柔軟な機械割当 | 📋 計画中 |
| `setup_time` | constraint | 段取り時間制約 | 📋 計画中 |
| `batch_processing` | constraint | バッチ処理制約 | 📋 計画中 |
| `multi_objective` | objective | 多目的最適化 | 📋 計画中 |

---

## 🔗 関連ドキュメント

- [OptiBuddy 4DSL Design Plan](../../docs/OptiBuddy_4DSL_Design_Plan.md) - アーキテクチャ設計全体像
- [DSL Repository Guide](../../docs/DSL_Repository_Guide.md) - SQLite3リポジトリの使い方
- [full_yard_solver.py](../solvers/full_yard_solver.py) - 実装参照（Yard Planning専用ソルバー）
- [Frontend/src/domain/types.ts](../../Frontend/src/domain/types.ts) - UI DSL型定義

---

## 📊 バージョン管理

各DSL仕様書はセマンティックバージョニング（`major.minor.patch`）を採用しています：

- **major**: 後方互換性のない変更
- **minor**: 後方互換性のある機能追加
- **patch**: バグ修正、ドキュメント改善

現在のバージョン:
- RCPSP Base DSL: **1.0**
- Solver Input DSL: **1.0**
- Solver Output DSL: **1.0**
- UI DSL: **1.0**
- physical_space extension: **1.0**

---

## 🤝 貢献ガイド

新しいextensionや改善提案は以下の手順で行ってください：

1. `extensions/` に新しい仕様書を作成
2. [physical_space.md](./extensions/physical_space.md) をテンプレートとして使用
3. 以下のセクションを必ず含める：
   - 概要（Extension名、カテゴリ、適用ドメイン）
   - スキーマ追加
   - 追加制約
   - Issue検出（該当する場合）
   - UI DSLマッピング
   - 使用例
   - バージョン履歴

---

## 📞 サポート

質問や提案がある場合は、以下のドキュメントを参照してください：

- [OptiBuddy Primary Document](../../docs/OptiBuddy_primary_document.md)
- [OptiBuddy Handover Prompt](../../docs/OptiBuddy_handover_prompt.md)

---

**最終更新**: 2026-06-02  
**メンテナー**: OptiBuddy Development Team
