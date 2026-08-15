# Solver Output DSL v1.0 仕様書

## 概要

**DSL名**: Solver Output DSL  
**バージョン**: 1.0  
**目的**: ソルバー（CP Optimizer）の実行結果を標準化された形式で表現

Solver Output DSLは、ソルバーが生成した解（スケジュール）を、UI DSLや業務DSLに変換可能な中間表現として定義します。
複数の解候補（solution profiles）、イシュー検出結果、最適化メトリクスを含む包括的な出力形式です。

---

## 設計原則

1. **ソルバー非依存性**: CP Optimizer以外のソルバー出力も同じ形式に変換可能
2. **完全性**: 解の再現・検証に必要なすべての情報を含む
3. **トレーサビリティ**: Solver Input DSLへの参照を保持
4. **UI変換容易性**: UI DSL（Gantt、Grid、KPI）への変換が容易な構造

---

## スキーマ定義

```json
{
  "solver_output": {
    "metadata": {
      "solver_name": "string",              // "CP Optimizer 22.1.1"
      "solver_version": "string",           // "22.1.1"
      "problem_class": "string",            // "RCPSP"
      "extensions": ["string"],             // ["physical_space", "shift", ...]
      "input_dsl_hash": "string",           // Solver Input DSLのハッシュ値
      "solved_at": "timestamp",             // "2026-06-02T11:30:00Z"
      "total_solve_time": "number"          // 総ソルブ時間（秒）
    },
    
    "status": {
      "type": "enum",
      "values": ["ok", "error", "timeout", "infeasible"],
      "description": "ソルブ結果のステータス"
    },
    
    "solutions": {
      "type": "array",
      "description": "複数の解候補（solution profiles）",
      "items": {
        "name": {
          "type": "string",
          "description": "解の名前（例: Plan A, Plan B）"
        },
        "label": {
          "type": "string",
          "description": "解の説明（例: 全作業時間最短、積み順遵守）"
        },
        "profile": {
          "type": "object",
          "description": "この解を生成した最適化プロファイル",
          "properties": {
            "w_makespan": "number",
            "w_penalty": "number",
            "w_cost": "number"
          }
        },
        "tasks": {
          "type": "array",
          "description": "スケジュールされたタスクのリスト",
          "items": {
            "id": "string",
            "containerId": "string",
            "operation": "string",
            "sequence": "number",
            "resourceId": "string",
            "start": {
              "type": "number",
              "description": "開始時刻（秒単位）"
            },
            "end": {
              "type": "number",
              "description": "終了時刻（秒単位）"
            },
            "duration": "number",
            "yard": "object",              // 業務DSLからコピー
            "ship": "object",              // 業務DSLからコピー
            "order": "number",             // 業務DSLからコピー
            "attrs": "array[string]"       // 業務DSLからコピー
          }
        },
        "metrics": {
          "type": "object",
          "description": "この解の評価メトリクス",
          "properties": {
            "makespan": {
              "type": "number",
              "description": "全タスク完了時刻（秒）"
            },
            "penalty": {
              "type": "number",
              "description": "順序違反ペナルティ数"
            },
            "cost": {
              "type": "number",
              "description": "総コスト（オプション）"
            },
            "objective_value": {
              "type": "number",
              "description": "目的関数値（w_makespan * makespan + w_penalty * penalty）"
            },
            "solve_time": {
              "type": "number",
              "description": "この解のソルブ時間（秒）"
            },
            "optimality_gap": {
              "type": "number",
              "description": "最適性ギャップ（%、オプション）"
            }
          }
        },
        "constraint_violations": {
          "type": "array",
          "description": "制約違反リスト（通常は空）",
          "items": {
            "constraint_type": "string",
            "constraint_id": "string",
            "severity": "enum[ERROR, WARNING]",
            "message": "string"
          }
        }
      }
    },
    
    "issues": {
      "type": "array",
      "description": "検出されたイシュー（業務制約違反、警告等）",
      "items": {
        "id": {
          "type": "string",
          "description": "イシューの一意識別子"
        },
        "severity": {
          "type": "enum",
          "values": ["CRITICAL", "WARNING", "INFO"],
          "description": "重要度"
        },
        "category": {
          "type": "enum",
          "values": ["YARD", "MBP", "ATTR", "SHIFT", "OPERATION", "WEIGHT"],
          "description": "イシューカテゴリ"
        },
        "title": {
          "type": "string",
          "description": "イシューのタイトル"
        },
        "message": {
          "type": "string",
          "description": "詳細メッセージ"
        },
        "containerId": {
          "type": "string",
          "description": "主対象コンテナID"
        },
        "relatedContainerIds": {
          "type": "array[string]",
          "description": "関連コンテナIDリスト"
        },
        "status": {
          "type": "enum",
          "values": ["ACTIVE", "ACCEPTED", "RESOLVED"],
          "default": "ACTIVE"
        },
        "detected_at": {
          "type": "timestamp",
          "description": "検出時刻"
        }
      }
    },
    
    "containers": {
      "type": "array",
      "description": "業務DSLのコンテナ情報（参照用）",
      "items": {
        "id": "string",
        "yard": "object",
        "ship": "object",
        "order": "number",
        "operation_type": "string",
        "attrs": "array[string]",
        "weight": "number",
        "pol": "string",
        "pod": "string"
      }
    },
    
    "config": {
      "type": "object",
      "description": "ソルバー実行時の設定（参照用）",
      "properties": {
        "max_bays": "number",
        "max_rows": "number",
        "max_tiers": "number",
        "max_vessel_bays": "number",
        "max_vessel_rows": "number",
        "max_vessel_tiers": "number",
        "time_pick": "number",
        "time_load": "number",
        "time_discharge": "number",
        "time_place": "number",
        "time_move": "number",
        "time_rehandle": "number",
        "safety_gap": "number",
        "time_limit": "number",
        "crane_interference": "boolean",
        "crane_safety_bays": "number",
        "current_port": "string"
      }
    },
    
    "yard_limits": {
      "type": "object",
      "description": "ヤード物理制限（UI表示用）",
      "properties": {
        "max_bay": "number",
        "max_row": "number",
        "max_tier": "number"
      }
    },
    
    "vessel_limits": {
      "type": "object",
      "description": "船舶物理制限（UI表示用）",
      "properties": {
        "max_bay": "number",
        "max_row": "number",
        "max_tier": "number"
      }
    }
  }
}
```

---

## Issue定義詳細

### 1. IS_YARD（ヤードリハンドリング）

```json
{
  "id": "is_yard_C01_C02",
  "severity": "WARNING",
  "category": "YARD",
  "title": "リハンドリングが発生します",
  "message": "上段(C02)が邪魔で先に積むべき下段(C01)を取り出せません。",
  "containerId": "C01",
  "relatedContainerIds": ["C01", "C02"],
  "status": "ACTIVE"
}
```

**検出条件**:
- 同じ(bay, row)スタック内で、上段コンテナのorder > 下段コンテナのorder

### 2. IS_SHIP（船舶積み順矛盾）

```json
{
  "id": "is_ship_C03_C04",
  "severity": "CRITICAL",
  "category": "MBP",
  "title": "Master Bay Plan 積み順矛盾",
  "message": "Master Bay Planに積み順矛盾があります。船社への確認が必要です。",
  "containerId": "C03",
  "relatedContainerIds": ["C03", "C04"],
  "status": "ACTIVE"
}
```

**検出条件**:
- 同じ(bay, row)スタック内で、上段コンテナのorder < 下段コンテナのorder

### 3. ATTR_REEFER（Reefer配置違反）

```json
{
  "id": "attr_reefer_REEFER01",
  "severity": "CRITICAL",
  "category": "ATTR",
  "title": "Reefer配置違反: REEFER01",
  "message": "REEFER01 がReeferスロット以外(bay=2,row=1,tier=1)に配置されています。電源供給ができません。",
  "containerId": "REEFER01",
  "relatedContainerIds": ["REEFER01"],
  "status": "ACTIVE"
}
```

### 4. ATTR_IMO（IMO危険物配置違反）

```json
{
  "id": "attr_imo_IMO01",
  "severity": "CRITICAL",
  "category": "ATTR",
  "title": "IMO危険物配置違反: IMO01",
  "message": "IMO01 がIMO指定区画外(bay=3,row=2)に配置されています。法規制違反です。",
  "containerId": "IMO01",
  "relatedContainerIds": ["IMO01"],
  "status": "ACTIVE"
}
```

### 5. ATTR_OOG（OOG隣接スロット）

```json
{
  "id": "attr_oog_C05",
  "severity": "WARNING",
  "category": "ATTR",
  "title": "OOG隣接スロット: C05",
  "message": "C05 がOOGコンテナ(OOG01)の隣接スロット(bay=1,row=2,tier=1)に配置されています。",
  "containerId": "C05",
  "relatedContainerIds": ["C05", "OOG01"],
  "status": "ACTIVE"
}
```

### 6. SHIFT_VIOLATION（シフト外作業）

```json
{
  "id": "shift_violation_P-C10",
  "severity": "WARNING",
  "category": "SHIFT",
  "title": "シフト外作業: C10 (RC-1)",
  "message": "RC-1 の稼働時間外(500〜520分)にタスクが配置されています。稼働時間: [(480, 960)]",
  "containerId": "C10",
  "relatedContainerIds": ["C10"],
  "status": "ACTIVE"
}
```

### 7. SHIFT_BREAK（休憩時間跨ぎ）

```json
{
  "id": "shift_break_P-C11",
  "severity": "WARNING",
  "category": "SHIFT",
  "title": "休憩時間跨ぎ: C11 (RC-1)",
  "message": "RC-1 の休憩時間(720〜780分)にタスクが跨がっています。",
  "containerId": "C11",
  "relatedContainerIds": ["C11"],
  "status": "ACTIVE"
}
```

### 8. DISCHARGE_BEFORE_LOAD（DISCHARGE完了前のLOAD開始）

```json
{
  "id": "discharge_before_load_C12",
  "severity": "CRITICAL",
  "category": "OPERATION",
  "title": "DISCHARGE完了前のLOAD開始: C12",
  "message": "コンテナ C12 のLOAD作業が、すべてのDISCHARGE作業完了前（1800秒）に開始されています（1500秒）。",
  "containerId": "C12",
  "relatedContainerIds": ["C12"],
  "status": "ACTIVE"
}
```

### 9. WEIGHT_WARNING（重量警告）

```json
{
  "id": "weight-yard-C13_C14",
  "severity": "WARNING",
  "category": "WEIGHT",
  "title": "Weight Warning: C13(25t) が C14(20t) の上",
  "message": "重量の重い C13(25t) が軽い C14(20t) の上に積まれています。",
  "containerId": "C13",
  "relatedContainerIds": ["C13", "C14"],
  "status": "ACTIVE"
}
```

---

## 完全な出力例

### Solver Output DSL（Yard Planning問題）

```json
{
  "metadata": {
    "solver_name": "CP Optimizer",
    "solver_version": "22.1.1",
    "problem_class": "RCPSP",
    "extensions": ["physical_space", "phase_separation"],
    "input_dsl_hash": "sha256:abc123...",
    "solved_at": "2026-06-02T11:30:15Z",
    "total_solve_time": 18.5
  },
  
  "status": "ok",
  
  "solutions": [
    {
      "name": "Plan A",
      "label": "全作業時間最短",
      "profile": {
        "w_makespan": 1,
        "w_penalty": 1,
        "w_cost": 0
      },
      "tasks": [
        {
          "id": "D-C03",
          "containerId": "C03",
          "operation": "DISCHARGE",
          "sequence": 1,
          "resourceId": "GC-1",
          "start": 0,
          "end": 300,
          "duration": 300,
          "ship": {"bay": 12, "row": 1, "tier": 1},
          "yard": {"bay": 2, "row": 1, "tier": 1},
          "order": 1,
          "attrs": []
        },
        {
          "id": "PL-C03",
          "containerId": "C03",
          "operation": "PLACE",
          "sequence": 2,
          "resourceId": "RC-1",
          "start": 302,
          "end": 482,
          "duration": 180,
          "ship": {"bay": 12, "row": 1, "tier": 1},
          "yard": {"bay": 2, "row": 1, "tier": 1},
          "order": 1,
          "attrs": []
        },
        {
          "id": "P-C02",
          "containerId": "C02",
          "operation": "PICK",
          "sequence": 1,
          "resourceId": "RC-1",
          "start": 482,
          "end": 662,
          "duration": 180,
          "yard": {"bay": 1, "row": 1, "tier": 2},
          "ship": {"bay": 10, "row": 1, "tier": 2},
          "order": 2,
          "attrs": []
        },
        {
          "id": "P-C01",
          "containerId": "C01",
          "operation": "PICK",
          "sequence": 1,
          "resourceId": "RC-1",
          "start": 672,
          "end": 852,
          "duration": 180,
          "yard": {"bay": 1, "row": 1, "tier": 1},
          "ship": {"bay": 10, "row": 1, "tier": 1},
          "order": 1,
          "attrs": []
        },
        {
          "id": "L-C01",
          "containerId": "C01",
          "operation": "LOAD",
          "sequence": 2,
          "resourceId": "GC-1",
          "start": 854,
          "end": 1154,
          "duration": 300,
          "yard": {"bay": 1, "row": 1, "tier": 1},
          "ship": {"bay": 10, "row": 1, "tier": 1},
          "order": 1,
          "attrs": []
        },
        {
          "id": "L-C02",
          "containerId": "C02",
          "operation": "LOAD",
          "sequence": 2,
          "resourceId": "GC-1",
          "start": 1155,
          "end": 1455,
          "duration": 300,
          "yard": {"bay": 1, "row": 1, "tier": 2},
          "ship": {"bay": 10, "row": 1, "tier": 2},
          "order": 2,
          "attrs": []
        }
      ],
      "metrics": {
        "makespan": 1455,
        "penalty": 0,
        "cost": 0,
        "objective_value": 1455,
        "solve_time": 8.2,
        "optimality_gap": 0.0
      },
      "constraint_violations": []
    },
    {
      "name": "Plan B",
      "label": "積み順遵守",
      "profile": {
        "w_makespan": 1,
        "w_penalty": 300,
        "w_cost": 0
      },
      "tasks": [
        {
          "id": "D-C03",
          "containerId": "C03",
          "operation": "DISCHARGE",
          "sequence": 1,
          "resourceId": "GC-1",
          "start": 0,
          "end": 300,
          "duration": 300,
          "ship": {"bay": 12, "row": 1, "tier": 1},
          "yard": {"bay": 2, "row": 1, "tier": 1},
          "order": 1,
          "attrs": []
        },
        {
          "id": "PL-C03",
          "containerId": "C03",
          "operation": "PLACE",
          "sequence": 2,
          "resourceId": "RC-1",
          "start": 302,
          "end": 482,
          "duration": 180,
          "ship": {"bay": 12, "row": 1, "tier": 1},
          "yard": {"bay": 2, "row": 1, "tier": 1},
          "order": 1,
          "attrs": []
        },
        {
          "id": "P-C02",
          "containerId": "C02",
          "operation": "PICK",
          "sequence": 1,
          "resourceId": "RC-1",
          "start": 482,
          "end": 662,
          "duration": 180,
          "yard": {"bay": 1, "row": 1, "tier": 2},
          "ship": {"bay": 10, "row": 1, "tier": 2},
          "order": 2,
          "attrs": []
        },
        {
          "id": "P-C01",
          "containerId": "C01",
          "operation": "PICK",
          "sequence": 1,
          "resourceId": "RC-1",
          "start": 672,
          "end": 852,
          "duration": 180,
          "yard": {"bay": 1, "row": 1, "tier": 1},
          "ship": {"bay": 10, "row": 1, "tier": 1},
          "order": 1,
          "attrs": []
        },
        {
          "id": "L-C01",
          "containerId": "C01",
          "operation": "LOAD",
          "sequence": 2,
          "resourceId": "GC-1",
          "start": 854,
          "end": 1154,
          "duration": 300,
          "yard": {"bay": 1, "row": 1, "tier": 1},
          "ship": {"bay": 10, "row": 1, "tier": 1},
          "order": 1,
          "attrs": []
        },
        {
          "id": "L-C02",
          "containerId": "C02",
          "operation": "LOAD",
          "sequence": 2,
          "resourceId": "GC-1",
          "start": 1155,
          "end": 1455,
          "duration": 300,
          "yard": {"bay": 1, "row": 1, "tier": 2},
          "ship": {"bay": 10, "row": 1, "tier": 2},
          "order": 2,
          "attrs": []
        }
      ],
      "metrics": {
        "makespan": 1455,
        "penalty": 0,
        "cost": 0,
        "objective_value": 1455,
        "solve_time": 10.3,
        "optimality_gap": 0.0
      },
      "constraint_violations": []
    }
  ],
  
  "issues": [],
  
  "containers": [
    {
      "id": "C01",
      "yard": {"bay": 1, "row": 1, "tier": 1},
      "ship": {"bay": 10, "row": 1, "tier": 1},
      "order": 1,
      "operation_type": "LOAD",
      "attrs": [],
      "weight": 20000,
      "pol": "JPYOK",
      "pod": "USOAK"
    },
    {
      "id": "C02",
      "yard": {"bay": 1, "row": 1, "tier": 2},
      "ship": {"bay": 10, "row": 1, "tier": 2},
      "order": 2,
      "operation_type": "LOAD",
      "attrs": [],
      "weight": 22000,
      "pol": "JPYOK",
      "pod": "USOAK"
    },
    {
      "id": "C03",
      "ship": {"bay": 12, "row": 1, "tier": 1},
      "yard": {"bay": 2, "row": 1, "tier": 1},
      "order": 1,
      "operation_type": "DISCHARGE",
      "attrs": [],
      "weight": 18000,
      "pol": "CNSHA",
      "pod": "JPYOK"
    }
  ],
  
  "config": {
    "max_bays": 5,
    "max_rows": 3,
    "max_tiers": 4,
    "max_vessel_bays": 20,
    "max_vessel_rows": 8,
    "max_vessel_tiers": 6,
    "time_pick": 180,
    "time_load": 300,
    "time_discharge": 300,
    "time_place": 180,
    "time_move": 120,
    "time_rehandle": 240,
    "safety_gap": 10,
    "time_limit": 20,
    "crane_interference": false,
    "crane_safety_bays": 2,
    "current_port": "JPYOK"
  },
  
  "yard_limits": {
    "max_bay": 5,
    "max_row": 3,
    "max_tier": 4
  },
  
  "vessel_limits": {
    "max_bay": 20,
    "max_row": 8,
    "max_tier": 6
  }
}
```

---

## Solver Output DSL → UI DSL 変換

### Gantt表示への変換

```typescript
interface GanttTask {
  id: string;
  name: string;
  start: number;
  end: number;
  resource: string;
  operation: string;
}

function toGanttTasks(solverOutput: SolverOutput, solutionIndex: number = 0): GanttTask[] {
  const solution = solverOutput.solutions[solutionIndex];
  return solution.tasks.map(task => ({
    id: task.id,
    name: `${task.operation}-${task.containerId}`,
    start: task.start,
    end: task.end,
    resource: task.resourceId,
    operation: task.operation
  }));
}
```

### Grid表示（Snapshot）への変換

```typescript
interface Snapshot {
  yard: { [bay: number]: { [row: number]: { [tier: number]: string } } };
  ship: { [bay: number]: { [row: number]: { [tier: number]: string } } };
}

function buildSnapshot(solverOutput: SolverOutput, currentTime: number): Snapshot {
  const snapshot: Snapshot = { yard: {}, ship: {} };
  
  for (const container of solverOutput.containers) {
    // Yard配置（初期状態）
    const yb = container.yard.bay, yr = container.yard.row, yt = container.yard.tier;
    if (!snapshot.yard[yb]) snapshot.yard[yb] = {};
    if (!snapshot.yard[yb][yr]) snapshot.yard[yb][yr] = {};
    snapshot.yard[yb][yr][yt] = container.id;
    
    // Ship配置（最終状態）
    const sb = container.ship.bay, sr = container.ship.row, st = container.ship.tier;
    if (!snapshot.ship[sb]) snapshot.ship[sb] = {};
    if (!snapshot.ship[sb][sr]) snapshot.ship[sb][sr] = {};
    snapshot.ship[sb][sr][st] = container.id;
  }
  
  return snapshot;
}
```

### KPI計算

```typescript
interface KPI {
  makespan: number;
  totalTasks: number;
  totalContainers: number;
  loadContainers: number;
  dischargeContainers: number;
  rehandleCount: number;
  issueCount: number;
  criticalIssueCount: number;
}

function calculateKPI(solverOutput: SolverOutput, solutionIndex: number = 0): KPI {
  const solution = solverOutput.solutions[solutionIndex];
  
  return {
    makespan: solution.metrics.makespan,
    totalTasks: solution.tasks.length,
    totalContainers: solverOutput.containers.length,
    loadContainers: solverOutput.containers.filter(c => c.operation_type === "LOAD").length,
    dischargeContainers: solverOutput.containers.filter(c => c.operation_type === "DISCHARGE").length,
    rehandleCount: solution.tasks.filter(t => t.operation === "REHANDLE").length,
    issueCount: solverOutput.issues.length,
    criticalIssueCount: solverOutput.issues.filter(i => i.severity === "CRITICAL").length
  };
}
```

---

## エラーハンドリング

### ソルブ失敗時の出力

```json
{
  "metadata": {
    "solver_name": "CP Optimizer",
    "solver_version": "22.1.1",
    "problem_class": "RCPSP",
    "extensions": ["physical_space"],
    "input_dsl_hash": "sha256:def456...",
    "solved_at": "2026-06-02T11:35:00Z",
    "total_solve_time": 20.0
  },
  
  "status": "timeout",
  
  "solutions": [],
  
  "issues": [
    {
      "id": "solver_timeout",
      "severity": "CRITICAL",
      "category": "OPERATION",
      "title": "ソルバータイムアウト",
      "message": "制限時間（20秒）内に解が見つかりませんでした。問題サイズを縮小するか、制限時間を延長してください。",
      "containerId": "",
      "relatedContainerIds": [],
      "status": "ACTIVE"
    }
  ],
  
  "containers": [],
  "config": {},
  "yard_limits": {},
  "vessel_limits": {}
}
```

### 制約違反時の出力

```json
{
  "status": "infeasible",
  
  "solutions": [],
  
  "issues": [
    {
      "id": "infeasible_problem",
      "severity": "CRITICAL",
      "category": "OPERATION",
      "title": "実行不可能な問題",
      "message": "制約を満たす解が存在しません。DISCHARGE/LOAD分離制約とシフト制約が競合している可能性があります。",
      "containerId": "",
      "relatedContainerIds": [],
      "status": "ACTIVE"
    }
  ]
}
```

---

## バージョン履歴

| バージョン | 日付 | 変更内容 |
|---|---|---|
| 1.0 | 2026-06-02 | 初版リリース |

---

## 関連資料

- [Solver Input DSL](./solver_input_dsl_v1.md)
- [RCPSP Base DSL](./rcpsp_base_v1.md)
- [full_yard_solver.py](../solvers/full_yard_solver.py)
- [OptiBuddy 4DSL Design Plan](../../docs/OptiBuddy_4DSL_Design_Plan.md)
- [UI DSL Types](../../Frontend/src/domain/types.ts)
