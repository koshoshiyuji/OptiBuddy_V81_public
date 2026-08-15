# OptiBuddy V81 実装仕様書
**対象バージョン**: V8.6（2026-07-04更新）
**目的**: 新ドメイン自動登録パイプラインの完全仕様。Claude（LLM）が読む技術仕様書。

**V8.5 → V8.6 変更点**:
- ソルバー結果の詳細テーブル出力を `ui_dsl.table_sections` として標準化（3-6節・4-4節・8節）。
- 共通コンポーネント `GenericResultTable.tsx` と、バックエンド側ビルダー `table_sections.py` を新設。
- display_hint（TABLE/CSV/GANTT切り替え）はヒアリング時ではなくStage2コード生成時にui_converter側で決定する方式に確定（詳細は本ファイル1-3節）。
- ヒアリングシート「7. 画面で確認したい情報」に、記入内容が詳細テーブルの列定義に直結する旨の注記を追加。

---

## 1. アーキテクチャ概要

### 1-1. 4DSLアーキテクチャ（基本原則）

```
Business DSL（ユーザー入力）
  ↓ dsl_transformer/{snake}_converter.py
Solver Input DSL（数理モデル入力）
  ↓ solvers/{snake}_solver.py
Solver Output DSL（解の生データ）
  ↓ dsl_transformer/{snake}_ui_converter.py
UI DSL（画面表示用）
```

### 1-2. ドメイン実装方式の分類

| 方式 | 対象 | 特徴 |
|---|---|---|
| **4DSL準拠** | TruckDispatcher, HospitalShiftPlanner, ProductionLotScheduler（および今後の新規ドメイン。旧CapacitatedVehicleRoutingProblem実装は2026-07-09に削除済み） | 4層全て実装。converter + ui_converter が必須 |
| **GhostKitchen方式（レガシー）** | GhostKitchen / ProjectPlanner / BinPacking | dsl_transformer非使用。solver が直接JSONを受け取る |
| **Registry方式** | YardPlanning / EventStaffing | convert_business_to_solver → SolverRegistry 経由 |

**重要**: 今後追加する新ドメインは原則4DSL準拠方式で実装する。GhostKitchen方式は既存ドメインのみで凍結。

### 1-3. 表形式出力（table_sections）に関する設計方針（V8.6で確定）

ソルバー結果の詳細一覧を画面に表示する方式について、以下を確定した経緯を記録する。

**不採用にした方式**: ヒアリングシート入力時に `display_hint`
（`TABLE`/`CSV`/`GANTT`）を決定し、独立したUIディスパッチャーで表示
コンポーネント全体を切り替える方式（初期検討案）。

**不採用の理由**:
1. 既存ドメイン専用View（KPIカード→アラート→詳細テーブルの3層構成）が
   Human-in-the-Loopのissue triage（FIX/ACCEPT判断）を担っており、
   汎用ディスパッチャーに一本化すると判断材料が失われる。
2. ヒアリング入力時点ではソルバー出力の実データ構造が未確定であり、
   その時点で表示形式を決めるのは時期尚早。
3. CSV（エクスポート手段）・GANTT（YardPlanning固有の既存機能）・
   TABLE（詳細一覧）は抽象度が異なり、3択の`preferred_view`として
   並列に扱うのは設計として歪む。

**確定した方式**:
- Viewの「詳細テーブル」部分のみを共通コンポーネント
  `GenericResultTable`（Frontend/src/app/studio/components/）で置き換える。
  KPIカード・アラートはドメイン固有のまま手書きを維持する。
- 表示形式の決定（列構成の設計）は**ヒアリング時ではなくStage2コード生成時**、
  `{snake}_ui_converter.py` 内で行う。
- CSVは独立した表示形式ではなく、各テーブルセクションの
  `allow_download` フラグによるエクスポート機能として統一する。
- 詳細は3-6節・4-4節・8節、およびバックエンドヘルパー
  `Backend/dsl_transformer/table_sections.py` を参照。

---

## 2. 自動登録パイプライン（domain_run）

### 2-1. 全体フロー

```
POST /api/domain/run
  { domain_name, hearing_texts }
  ↓
interpret_hearing()
  ├─ Stage1a: classify_problem() [Haiku, max_tokens=1024]
  │    → match_type: existing_domain / base_problem / new_domain
  │    → base_domain: EXISTING_DOMAINS のキー or null
  │
  ├─ existing_domain / base_problem かつ base_domain あり
  │    → Stage1b: generate_scenarios_from_schema() [Sonnet, max_tokens=16000]
  │    → apply_scenarios() → ファイル書き込み + DB登録
  │    → 終了（コード生成不要）
  │
  └─ new_domain
       → _ensure_repomix() [repomix --config repomix.domain-addition.json]
       → generate_domain_files() [Sonnet + repomix, max_tokens=16000]
       → apply_domain_files() → ファイル書き込み + DB登録
       → Flask再起動後に自動有効化
```

> **注記（V8.6）**: `table_sections` はStage1b（既存ドメインのシナリオ生成のみ）
> には影響しない。既存ドメインは既にui_converterのコードが確定しているため、
> Stage1bではシナリオJSON（データ）のみを生成し、表構成の再設計は行わない。
> `table_sections` が関与するのは `new_domain` 経路のStage2コード生成のみ。

> **注記（2026-07-09追記）**: `new_domain` 経路には上図には明示していないがGate 1（仕様確定ゲート）・
> Gate 2（検証・デバッグゲート）が掛かる。Gate 1はヒアリング段階（`hearing_template.md`の強制
> デフォルト項目）ですでに実装済み。Gate 2は `generate_domain_files()` 直後、`apply_domain_files()`
> の前に `scan_diffs_for_warnings()`（静的）と `run_gate2_dynamic_verification()`（動的）として
> `_run_domain_job()`（app.py）に自動接続されており、警告があれば`needs_confirmation`として
> 人間承認待ちで停止する。詳細は `docs/DESIGN_2026-07-09_registration_gates_and_hard_soft.md`
> （7節が最新）を参照。

### 2-2. Stage1a 分類ルール

**EXISTING_DOMAINS（固定6ドメイン、2026-07-09時点）**:
- BinPacking → bin_packing_baseline.json をスキーマ参照
- ProjectPlanner → project_planner_baseline.json
- GhostKitchen → None（スキーマなし）
- YardPlanning → congestion.json
- EventStaffing → staffing_small_event.json
- TruckDispatcher → truck_dispatcher_baseline.json
  （旧CapacitatedVehicleRoutingProblem実装は2026-07-09に削除・置き換え済み。Stage1aの
  分類プロンプトはCapacitatedVehicleRoutingProblemという名前を選ばないよう明示的に指示している）

**動的候補ドメイン（2026-07-10〜）**:
上記の固定6ドメインに加え、`classify_problem()` は毎回DB (`dsl_definitions`テーブル) から
固定6ドメイン以外の登録済みドメイン（例: HospitalShiftPlanner, ProductionLotScheduler）を
取得し、「追加候補ドメイン一覧」としてStage1aのuser_promptに埋め込む。ヒアリング内容が
これらのいずれかと実質的に同一の業務であれば、固定6ドメインへの汎用的な当てはめより
優先してそのドメインをbase_domainとして選ぶ（2026-07-09 HANDOFF 課題1の対応。従来は
DB登録済みでもStage1aの比較候補に一切上がらず、類似ドメインが際限なく並立していた）。

また `EXISTING_DOMAINS` / `_DOMAIN_SOURCE_FILES`（メモリ上の辞書）は、プロセス起動後の
初回利用時に一度だけDBから再構築される（`_reconcile_domain_registry_from_db()`）。
これにより、プロセス再起動でDB登録済みドメインの情報が失われ、Stage1b以降のスキーマ例参照・
拡張差分検出が空になる問題も解消済み。

（旧`FOUR_DSL_DOMAINS`は2026-07-10に削除。唯一の消費箇所だった`dsl4_route`はどこからも
参照されておらず死んでいたため。）

### 2-3. Stage2 マーカー出力フォーマット

LLMが生成するコードは以下のマーカー形式で出力する（domain_generator.py が parse する）:

```
===FILE:パス===
（ファイル内容）
===END===

===PATCH:パス===
INSTRUCTION: 何をするか
===CODE===
（追記コード）
===END===

===SCENARIOS===
[{"name": "...", "description": "...", "tag": "...", "tag_color": "#...", "domain": "...", "file": "..."}]
===END===
```

### 2-4. 4DSL新ドメイン登録時に生成するファイル一覧

| ファイル | 必須/任意 | 内容 |
|---|---|---|
| `Backend/solvers/{snake}_solver.py` | 必須 | クラス名: `{PascalCase}Solver` |
| `Backend/dsl_transformer/{snake}_converter.py` | 4DSLのみ必須 | Business DSL → Solver Input DSL |
| `Backend/dsl_transformer/{snake}_ui_converter.py` | 4DSLのみ必須 | Solver Output DSL → UI DSL（`table_sections`の組み立てを含む） |
| `Backend/dsl_repository/scenarios/{snake}_baseline.json` | 必須 | 標準シナリオ |
| `Backend/dsl_repository/scenarios/{snake}_infeasible.json` | 必須 | 解なしシナリオ |
| `Frontend/src/app/studio/views/{PascalCase}View.tsx` | 必須 | 専用ビュー（詳細テーブル部分は`GenericResultTable`使用必須） |

### 2-5. 4DSL新ドメイン登録時に生成するPATCH一覧

| 対象ファイル | 変更内容 |
|---|---|
| `Backend/app.py` | `_solve_{snake}()` 関数追加 + `_DSL4_SOLVERS` への登録 |
| `Backend/domain_generator.py` | `EXISTING_DOMAINS` への追加（コメント。2026-07-10以降は`_reconcile_domain_registry_from_db()`がDB起点で自動反映するため、手動パッチは省略可） |
| `Frontend/src/app/studio/types.ts` | `StudioMode` に新モードIDを追加 |
| `Frontend/src/app/studio/components/StudioModeTabs.tsx` | タブ定義と分岐追加 |
| `Frontend/src/app/studio/StudioShell.tsx` | import とルーティング追加 |

---

## 3. Backend 実装仕様

### 3-1. app.py ルーティング構造

```python
# 優先順位（2026-07-09時点の実装）:
# 1. _DSL4_SOLVERS（明示登録が必要な4DSLドメイン。命名規約に従わない例外のみ、例: ProductionLotScheduler）
# 2. _solve_4dsl_generic()（規約ベース自動ルーター。{snake}_converter.py/{snake}_solver.py/
#    {snake}_ui_converter.pyが命名規約通りに揃えば、app.pyへのコード追加なしに自動ルーティング
#    される。TruckDispatcherはこの経路。新規4DSLドメインは原則ここに乗る）
# 3. _LEGACY_SOLVERS（GhostKitchen方式）
# 4. _try_dynamic_solver()（solvers/に{snake}_solver.pyがあるが未登録・4DSL未準拠）
# 5. YardPlanningデフォルトルート（convert_business_to_solver → registry）

_DSL4_SOLVERS = {
    "ProductionLotScheduler": _solve_production_lot_scheduler,
    # 旧CapacitatedVehicleRoutingProblemは2026-07-09に削除済み。TruckDispatcherは
    # _solve_4dsl_generic()の規約ルーター経由で自動登録されるため、ここへの明示登録は不要。
    # 新規4DSLドメインはここに追加（domain_generator.pyが自動PATCH生成）
}

_LEGACY_SOLVERS = {
    "GhostKitchen":   _solve_ghost_kitchen,
    "ProjectPlanner": _solve_project_planner,
    "BinPacking":     _solve_bin_packing,
}
```

> **注記（V8.6）**: `table_sections` の追加により本ルーティング構造への
> 変更は発生しない。`/baseline` のドメイン分岐ロジック
> （`app_routing_snippet.md`）は変更不要であることを確認済み。

### 3-2. 4DSL専用ソルバー関数のテンプレート

```python
def _solve_{snake}(dsl, issue_statuses):
    try:
        from dsl_transformer.{snake}_converter import convert_{snake}_to_solver
        from solvers.{snake}_solver import {PascalCase}Solver
        from dsl_transformer.{snake}_ui_converter import convert_{snake}_to_ui

        solver_input = convert_{snake}_to_solver({**dsl, "issue_statuses": issue_statuses})
        result       = {PascalCase}Solver(solver_input).solve()
        ui_dsl       = convert_{snake}_to_ui(result, business_dsl=dsl)

        return jsonify({
            "status":        result.get("status", "ok"),
            "problem_class": "{PascalCase}",  # ← フロントがドメイン判別に使う
            "tasks":         [],
            "makespan":      0,
            "issues":        result.get("issues", []),
            "containers":    [],
            "solutions":     result.get("solutions", []),
            "ui_dsl":        ui_dsl,
            "resolved":      [],
            "skipped":       [],
            "skip_reasons":  {},
        })
    except Exception as e:
        logger.error(f"[{PascalCase}] エラー: {e}", exc_info=True)
        return jsonify({"status": "validation_error", "issues": [
            {"id": "config-error", "severity": "CRITICAL",
             "title": "{PascalCase} 設定エラー", "message": str(e),
             "relatedContainerIds": []}]}), 200
```

### 3-3. SolverRegistry（V7.0以降）自動スキャン方式

`registry.py` は `solvers/` ディレクトリを自動スキャンして登録する。
**手動でregistry.pyを編集する必要はない。**

- `{snake}_solver.py` が存在し、`{PascalCase}Solver` クラスがあれば自動登録
- 4DSLドメインは `_DSL4_DISPLAY_ONLY` に登録し、表示のみ（実行はapp.pyが担当）
- スキップするファイル: `base_solver.py`, `registry.py`, `decomposer.py`, `full_yard_solver.py`

### 3-4. Solver クラスの実装規約

```python
class {PascalCase}Solver:
    def __init__(self, solver_input: dict) -> None:
        self.dsl = solver_input

    def solve(self) -> dict:
        # 返却フォーマット（必須フィールド）
        return {
            "status":   "ok" | "infeasible" | "error",
            "metadata": dict,
            "solutions": [{"feasible": bool, "routes/assignments/etc": ..., "kpi": dict}],
            "issues":   [{"id": str, "severity": "CRITICAL|WARNING|INFO",
                          "title": str, "message": str, "relatedContainerIds": []}],
            "_solver_version": "{snake}_v{N}.{n}",
        }
```

**重要**: `status` は `"ok"` か `"infeasible"` のみを返す（`"error"` は例外発生時のみ）。
feasible=False の場合も `status: "ok"` で返し、issues で CRITICAL イシューを通知する。
これによりフロントエンドの `result.status === 'ok'` 判定が一貫して動作する。

### 3-5. truck_dispatcher_converter.py のパターン（4DSL Converter の参考実装、2026-07-09に旧cvrp_converter.pyから差し替え）

```python
def convert_{snake}_to_solver(business_dsl: dict) -> dict:
    """
    Business DSL → Solver Input DSL
    - 時刻を分単位整数に変換 (_hhmm_to_min)
    - 距離行列を構築 (_build_distance_matrix)
    - _loc_idx を各顧客に付与（dist_matrix のインデックス）
    - problem_class を引き継ぎ
    """
    return {
        "problem_class": "{PascalCase}",
        "meta":          business_dsl.get("meta", {}),
        "locations":     [...],      # index 0 = depot/base
        "vehicles/resources/etc": ...,
        "customers/tasks/etc":    [...],  # _loc_idx 付き
        "dist_matrix":   [...],      # km, 対称行列
        "config":        {...},
        "issue_statuses": business_dsl.get("issue_statuses", {}),
    }
```

### 3-6. truck_dispatcher_ui_converter.py のパターン（4DSL UI Converter の参考実装、2026-07-09に旧cvrp_ui_converter.pyから差し替え）

```python
def convert_{snake}_to_ui(solver_output: dict, business_dsl: dict | None = None) -> dict:
    """
    Solver Output DSL → UI DSL
    ui_dsl.domain を必ずセットすること（フロントのドメイン判別に使う）
    """
    from dsl_transformer.table_sections import build_table_section, TableColumn

    # ...既存の変換処理（routes/assignments/etc, kpi_cards, alerts など）...

    # ── table_sections の組み立て（V8.6で追加） ──
    # 列構成はここでLLM（Stage2コード生成時）がソルバー出力の実データ構造から
    # 設計する。display_hintという形での事前指定は不要。
    table_sections = [
        build_table_section(
            section_id="{snake}_detail",
            title="詳細一覧",
            columns=[
                TableColumn(key="...", label="..."),
                # ...ドメインごとに定義。ヒアリングシート「7. 画面で確認したい
                # 情報」の記入内容が、この列定義の設計材料になる。
            ],
            rows=detail_rows,          # ドメイン固有データから組み立てたリスト
            row_id_key="id",
            severity_key="severity",   # 任意。issueと連携させる場合に指定
        ),
    ]

    return {
        "domain":         "{snake}",          # ← 必須。フロントがこれで判別
        "feasible":       bool,
        "summary":        dict,
        "kpi_cards":      [...],
        "routes/assignments/etc": [...],
        "table_sections": table_sections,     # ← V8.6で追加（任意フィールド）
        "map_points":     [...],              # 地図表示用（任意）
        "alerts":         [...],              # Human-in-the-Loop警告
        "raw_kpi":        dict,
    }
```

**重要（V8.6）**: `table_sections` は現時点では任意フィールドだが、
新規ドメインでは付与を強く推奨する。付与しない場合、`{PascalCase}View.tsx`
の詳細テーブル部分は独自実装となり、4-4節の必須事項に違反する。

---

## 4. Frontend 実装仕様

### 4-1. StudioMode の追加方法

`Frontend/src/app/studio/types.ts` の `StudioMode` union に追加:
```typescript
export type StudioMode =
  | 'overview' | 'timeline' | 'spatial' | 'staff' | 'task'
  | 'kitchen' | 'kitchen_params' | 'planner' | 'bin_packing'
  | '{new_mode}'    // ← 新ドメインはここに追加（例: TruckDispatcher、HospitalShiftPlanner等。
                    //   旧'cvrp'モードは2026-07-09に削除済み）
  | 'issues' | 'register' | 'infeasible' | 'optimize';
```

### 4-2. StudioModeTabs の分岐パターン

```typescript
const MODES =
  problemClass === 'EventStaffing'   ? STAFFING_MODES         :
  problemClass === 'GhostKitchen'    ? KITCHEN_MODES          :
  problemClass === 'ProjectPlanner'  ? PLANNER_MODES          :
  problemClass === 'BinPacking'      ? BIN_PACKING_MODES      :
  problemClass === 'TruckDispatcher' ? TRUCK_DISPATCHER_MODES : // ← 2026-07-09に旧CVRP_MODESから差し替え
  problemClass === '{NewDomain}'     ? NEW_DOMAIN_MODES       : // ← 追加
  YARD_MODES;
```

### 4-3. TruckDispatcher ルーティングの仕組み（4DSLドメインの参考実装）

`useStudioState.ts` の `handleRunSolver` 内でTruckDispatcherを検知する部分（2026-07-09に旧CVRP例から差し替え）:

```typescript
// ui_dsl.domain で4DSLドメインを判別
const uiDslDomain = result.ui_dsl?.domain as string | undefined;
if (uiDslDomain === 'truck_dispatcher') {
    const issues = buildSolverIssues(result);
    dispatch({ type: 'SOLVER_SUCCESS', solutions: [], issues });
    setTruckDispatcherUiDsl(result.ui_dsl);  // useState で管理
    setMode(result.ui_dsl?.feasible !== false ? 'truck_dispatch' : 'infeasible');
    return;
}
```

新しい4DSLドメインも同じパターン:
```typescript
if (uiDslDomain === '{snake_with_underscores}') {
    dispatch({ type: 'SOLVER_SUCCESS', solutions: [], issues: buildSolverIssues(result) });
    set{PascalCase}UiDsl(result.ui_dsl);
    setMode(result.ui_dsl?.feasible !== false ? '{mode_id}' : 'infeasible');
    return;
}
```

**重要**: `ui_dsl.domain` は snake_case（例: `truck_dispatcher`）。
`problem_class` は PascalCase（例: `TruckDispatcher`）。両方をレスポンスに含めること。

### 4-4. View コンポーネントの実装規約

```typescript
// props: studio に truckDispatcherUiDsl 等を as any でキャストして渡す
import { GenericResultTable } from '../components/GenericResultTable.tsx';

export function {PascalCase}View({ studio }: { studio: StudioState }) {
  const uiDsl = (studio as any).{snake}UiDsl as {PascalCase}UiDsl | null;

  if (!uiDsl) {
    return <div>データがありません。最適化を実行してください。</div>;
  }

  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: '12px', flex: 1, minHeight: 0 }}>
      {/* 1. KPIカード（ドメイン固有・手書き） */}
      {/* 2. アラート（ドメイン固有・手書き） */}
      {/* 3. 詳細テーブル（★ V8.6より GenericResultTable が必須） */}
      <GenericResultTable sections={uiDsl.table_sections ?? []} />
    </div>
  );
}
```

**必須事項（V8.6）**: 詳細テーブル部分を独自の `<table>` タグで手書き実装する
ことを禁止する。Stage2コード生成時、LLMは必ず `GenericResultTable` を
import して使用すること。KPIカード・アラート部分は従来通りドメイン固有の
手書きでよい。

**デザイントークン（全ビュー共通。GenericResultTableも同一トークンを使用）**:
```typescript
const COLOR = {
  bg: '#08080a', surface: '#121216', border: '#2a2a30',
  text: '#eee', muted: '#888',
  blue: '#3b82f6', green: '#10b981', orange: '#f97316',
  purple: '#8b5cf6', teal: '#14b8a6', red: '#ef4444', yellow: '#f59e0b',
};
```

### 4-5. StudioShell への追加パターン

```typescript
// import 追加
import { {PascalCase}View } from './views/{PascalCase}View.tsx';

// レンダリング分岐に追加
{studio.mode === '{mode_id}' && <{PascalCase}View studio={studio as any} />}

// ContainerTooltip の除外対象に追加
problemClass !== '{PascalCase}' &&
```

### 4-6. table_sections スキーマ定義（V8.6で追加）

```typescript
export type TableColumnFormat = 'number' | 'currency' | 'percent' | 'text' | 'badge';
export type TableColumnAlign = 'left' | 'right' | 'center';
export type RowSeverity = 'CRITICAL' | 'WARNING' | 'INFO' | null;

export interface TableColumn {
  key: string;
  label: string;
  align?: TableColumnAlign;
  format?: TableColumnFormat;
  unit?: string;
}

export interface TableRow {
  _id: string;
  _severity?: RowSeverity;
  [key: string]: unknown;
}

export interface TableSection {
  id: string;
  title: string;
  columns: TableColumn[];
  rows: TableRow[];
  allow_download?: boolean; // default: true
}
```

実装は `Frontend/src/app/studio/components/GenericResultTable.tsx` を参照。
行の重要度ハイライト（`_severity`）の配色は `OverviewView.tsx` の
`SEVERITY_COLOR` と統一する。CSVダウンロードは `allow_download` フラグで
制御し、独立した表示形式（`preferred_view`のような選択肢）としては扱わない。

---

## 5. DB・レジストリ管理仕様

### 5-1. DslRepository（SQLite）の管理テーブル

| テーブル | 用途 | 自動登録されるか |
|---|---|---|
| `dsl_definitions` | DSL定義（スキーマ）のメタ情報 | ❌ 手動 or スクリプト |
| `scenarios` | シナリオカードのデータ（dsl_jsonを含む） | ✅ apply_scenarios()が自動登録 |
| `evolution_logs` | DSL進化の履歴 | ✅ 自動 |

### 5-2. DSL定義の登録方法

新ドメイン追加時に以下を実行する（apply_domain_files内で自動化予定）:

```python
repo.create_dsl_definition(
    problem_class="{PascalCase}",
    version="1.0",
    extensions=[...],        # 制約・拡張のリスト
    schema_json={...},       # Business DSLのスキーマ定義
    description="...",
)
```

### 5-3. SolverRegistry（registry.py）V7.0以降

**ファイルスキャン方式に変更済み。** `_register_builtin_solvers()` は廃止。

```python
_DSL4_DISPLAY_ONLY: dict[str, str] = {
    # 2026-07-09時点で空。旧CapacitatedVehicleRoutingProblemエントリは削除済み。
    # 新4DSLドメインを表示専用でここに追加したい場合のみ使用
}
```

新ドメインのファイルを `solvers/` に配置してFlaskを再起動するだけで自動登録される。

---

## 6. repomix 設定

### 6-1. repomix.domain-addition.json の対象ファイル

`domain_generator.py`の`_ensure_repomix()`が`new_domain`登録前に自動実行する。
対象は約185KB（全体1.5MBから削減）:

- `Backend/app.py` → 除外（`docs/app_routing_snippet.md` で代替）
- `Backend/solvers/` 主要ファイル
- `Backend/dsl_transformer/` 全ファイル（`table_sections.py` を含む。V8.6で追加）
- `Backend/domain_generator.py`
- `Frontend/src/app/studio/` 主要ファイル（`components/GenericResultTable.tsx` を含む。V8.6で追加）
- `docs/OptiBuddy_V81_spec.md`（本ファイル）← 追加必須

### 6-2. DEFAULT_ATTACHMENTS（domain_generator.py）

Stage2コード生成時にLLMに渡す追加コンテキスト:

```python
DEFAULT_ATTACHMENTS = {
    "repomix-domain-addition.xml": str(_PROJECT_ROOT / "repomix-domain-addition.xml"),
    "OptiBuddy開発編.md":           str(_DOCS_DIR / "OptiBuddy開発編.md"),
    "OptiBuddy_V81_spec.md":       str(_DOCS_DIR / "OptiBuddy_V81_spec.md"),  # ← 本ファイル追加
    "app_routing_snippet.md":      str(_DOCS_DIR / "app_routing_snippet.md"),
    "table_sections.py":           str(_BACKEND_DIR / "dsl_transformer" / "table_sections.py"),               # ← V8.6で追加
    "GenericResultTable.tsx":      str(_FRONTEND_DIR / "src/app/studio/components/GenericResultTable.tsx"),   # ← V8.6で追加
}
```

---

## 7. 既知の問題と対処法

### 7-1. extract_json がコードブロックを剥がせない

**原因**: LLMが`\`\`\`json`で囲んで返す場合、`max_tokens`超過で途中で切れた場合。

**対処（V4.1で修正済み）**:
- Stage1b（シナリオ生成）は `default_model()` + `max_tokens=16000` を使用
- Stage1aは `fast_model()` + `max_tokens=1024`（小さいJSONのみなので問題なし）

### 7-2. float → int の変換エラー

**原因**: `_min_to_hhmm(minutes)` に float が渡される。

**対処（V2.1で修正済み）**:
```python
def _min_to_hhmm(minutes: int) -> str:
    minutes = int(minutes)  # ← 必ず int に変換してから使用
    h = minutes // 60
    m = minutes % 60
    return f"{h:02d}:{m:02d}"
```

### 7-3. EventStaffing が _try_dynamic_solver に落ちる

**原因**: `_LEGACY_SOLVERS` に EventStaffing が未登録の場合、`event_staffing_solver.py` を
dynamic_solver が発見して直接呼ぶが、solver の `_validate()` が `metadata.problem_class` を
チェックするため失敗する。

**対処（V8.5で修正済み）**:
```python
_MANAGED = {"CapacitatedVehicleRoutingProblem", "EventStaffing", "YardPlanning"}
if problem_class in _MANAGED:
    return None  # _try_dynamic_solver から除外
```

> **注記（2026-07-09）**: `CapacitatedVehicleRoutingProblem` はドメイン自体は削除済みだが、
> この除外集合に残っていても問題はない（そのproblem_classを持つDSLがもう存在しないため）。

### 7-4. 旧CVRP実装のフロント空表示（履歴記録。当該ドメインは2026-07-09に削除済み）

**原因**: `solutions[0].tasks` が存在しないため `SolutionPlan` 型の処理で空になる。

**対処（V8.5で修正済み）**: `useStudioState.ts` の `handleRunSolver` で
`result.ui_dsl?.domain` を検知し、ドメイン専用stateに保存してモードを切り替える（現在の実装例は4-3節参照）。

### 7-5. table_sections未設定時のフォールバック（V8.6で追加）

**原因**: 新ドメイン追加時、LLMが `table_sections` の組み立てを省略した場合、
`GenericResultTable` に空配列が渡される。

**対処**: `GenericResultTable.tsx` は `sections.length === 0` の場合に
「表示する表データがありません。」というプレースホルダーを表示し、
クラッシュしない設計にしている（コンポーネント本体を参照）。ただし
これは緊急時のフォールバック表示であり、8節のチェックリストに従って
`table_sections` を必ず組み立てることが原則である。

---

## 8. 次の新ドメイン登録時のチェックリスト

新ドメインが4DSL方式の場合:

**Backend**:
- [ ] `{snake}_solver.py`: クラス名 `{PascalCase}Solver`、`solve()` が返す `status` は `"ok"` or `"infeasible"`
- [ ] `dsl_transformer/{snake}_converter.py`: `convert_{snake}_to_solver(business_dsl)` を実装
- [ ] `dsl_transformer/{snake}_ui_converter.py`: `convert_{snake}_to_ui(solver_output, business_dsl=None)` を実装、`ui_dsl["domain"]` に snake_case をセット
- [ ] `dsl_transformer/{snake}_ui_converter.py`: `ui_dsl["table_sections"]` を `dsl_transformer.table_sections.build_table_section()` 経由で組み立てる（V8.6で追加）
- [ ] `app.py`: `_solve_{snake}()` を追加し `_DSL4_SOLVERS` に登録
- [ ] `domain_generator.py`: `EXISTING_DOMAINS` に追加（DB登録済みなら`_reconcile_domain_registry_from_db()`が自動反映）
- [ ] シナリオJSON 2本（baseline / infeasible。2026-07-18よりtight廃止）

**Frontend**:
- [ ] `types.ts`: `StudioMode` に新モード追加
- [ ] `useStudioState.ts`: `ui_dsl.domain` 検知、専用 state (`set{PascalCase}UiDsl`) 追加、モード切り替え
- [ ] `{PascalCase}View.tsx`: KPI カード → アラート → 詳細テーブルの構成で実装
- [ ] `{PascalCase}View.tsx`: 詳細テーブル部分に `GenericResultTable` を使用している（独自`<table>`実装がないこと。V8.6で追加）
- [ ] `StudioShell.tsx`: import とルーティング追加
- [ ] `StudioModeTabs.tsx`: タブ定義と分岐追加

**DB**:
- [ ] `dsl_definitions` に登録（`repo.create_dsl_definition()`）
- [ ] シナリオ2本が `scenarios` テーブルに登録されていること

**動作確認**:
- [ ] `/baseline` POST でログに `[{PascalCase}]` が出る
- [ ] `result.ui_dsl.domain` が正しい値を返す
- [ ] フロントで専用ビュー（TruckDispatcherビュー相当）が表示される
- [ ] Issues タブに適切なイシューが表示される
- [ ] infeasible シナリオで infeasible モードに遷移する
- [ ] `result.ui_dsl.table_sections` が期待する列・行数で返る（V8.6で追加）
- [ ] CSVダウンロードボタンで正しい内容のCSVがダウンロードされる（V8.6で追加）
- [ ] severity付き行が正しい色でハイライトされる（V8.6で追加）
