# RCPSP Base DSL v1.0 仕様書

## 概要

**問題クラス**: RCPSP (Resource-Constrained Project Scheduling Problem)  
**バージョン**: 1.0  
**Extensions**: なし（基底定義）  
**CSPLib参照**: [prob061](https://www.csplib.org/Problems/prob061/)

RCPSP基底DSLは、OptiBuddyの全ドメインの基盤となる最小限の問題定義です。
すべてのドメイン固有DSL（Yard Planning、FJSPなど）はこの基底に対してextensionsを追加する形で定義されます。

---

## スキーマ定義

```json
{
  "problem_class": "RCPSP",
  "version": "1.0",
  "extensions": [],
  "metadata": {
    "description": "Resource-Constrained Project Scheduling Problem (基底)",
    "csplib_ref": "prob061"
  },
  "schema": {
    "tasks": {
      "type": "array",
      "description": "実行すべきタスクのリスト",
      "items": {
        "id": {
          "type": "string",
          "description": "タスクの一意識別子"
        },
        "duration": {
          "type": "number",
          "description": "タスクの実行時間（秒単位）",
          "minimum": 0
        },
        "resource_requirements": {
          "type": "object",
          "description": "リソース要求量（リソースID → 必要量のマップ）",
          "additionalProperties": {
            "type": "number",
            "minimum": 0
          }
        }
      },
      "required": ["id", "duration"]
    },
    "resources": {
      "type": "array",
      "description": "利用可能なリソースのリスト",
      "items": {
        "id": {
          "type": "string",
          "description": "リソースの一意識別子"
        },
        "capacity": {
          "type": "number",
          "description": "リソースの容量（同時利用可能量）",
          "minimum": 0
        }
      },
      "required": ["id", "capacity"]
    },
    "precedences": {
      "type": "array",
      "description": "タスク間の先行関係",
      "items": {
        "from": {
          "type": "string",
          "description": "先行タスクID"
        },
        "to": {
          "type": "string",
          "description": "後続タスクID"
        },
        "delay": {
          "type": "number",
          "description": "最小遅延時間（秒単位、オプション）",
          "minimum": 0,
          "default": 0
        }
      },
      "required": ["from", "to"]
    },
    "objective": {
      "type": "enum",
      "description": "最適化目標",
      "values": ["minimize_makespan", "minimize_cost"],
      "default": "minimize_makespan"
    }
  }
}
```

---

## 制約

### 基本制約

1. **リソース容量制約**
   - 各時刻において、各リソースの使用量はその容量を超えてはならない
   - `∀t, ∀r: Σ(task_using_r_at_t) ≤ resource[r].capacity`

2. **先行関係制約**
   - 先行タスクが完了してから後続タスクを開始できる
   - `∀(from, to) ∈ precedences: end(from) + delay ≤ start(to)`

3. **タスク非中断制約**
   - タスクは一度開始したら中断せずに完了する
   - `∀task: end(task) = start(task) + duration(task)`

---

## ソルバーマッピング

### CP Optimizer制約への変換

```python
# タスク変数定義
task_itvs = {}
for task in tasks:
    task_itvs[task["id"]] = mdl.interval_var(
        size=task["duration"],
        name=f"T_{task['id']}"
    )

# リソース容量制約
for resource in resources:
    resource_usage = [
        task_itvs[task["id"]]
        for task in tasks
        if resource["id"] in task.get("resource_requirements", {})
    ]
    if resource_usage:
        mdl.add(mdl.no_overlap(resource_usage))

# 先行関係制約
for prec in precedences:
    delay = prec.get("delay", 0)
    mdl.add(mdl.end_before_start(
        task_itvs[prec["from"]],
        task_itvs[prec["to"]],
        delay=delay
    ))

# 目的関数
if objective == "minimize_makespan":
    makespan = mdl.max([mdl.end_of(itv) for itv in task_itvs.values()])
    mdl.add(mdl.minimize(makespan))
```

---

## UI DSLマッピング

### Ganttチャート表示

```typescript
interface GanttTask {
  id: string;
  name: string;
  start: number;
  end: number;
  resource: string;
}

// RCPSP DSL → Gantt表示
function toGanttTasks(solution: SolverResult): GanttTask[] {
  return solution.tasks.map(task => ({
    id: task.id,
    name: task.id,
    start: task.start,
    end: task.end,
    resource: Object.keys(task.resource_requirements || {})[0] || "default"
  }));
}
```

---

## 使用例

### 最小限のRCPSP問題

```json
{
  "problem_class": "RCPSP",
  "version": "1.0",
  "extensions": [],
  "tasks": [
    {
      "id": "T1",
      "duration": 300,
      "resource_requirements": {"R1": 1}
    },
    {
      "id": "T2",
      "duration": 200,
      "resource_requirements": {"R1": 1}
    },
    {
      "id": "T3",
      "duration": 150,
      "resource_requirements": {"R2": 1}
    }
  ],
  "resources": [
    {"id": "R1", "capacity": 1},
    {"id": "R2", "capacity": 1}
  ],
  "precedences": [
    {"from": "T1", "to": "T2", "delay": 10}
  ],
  "objective": "minimize_makespan"
}
```

**期待される解**:
- T1: 0-300秒（R1使用）
- T3: 0-150秒（R2使用、並列実行可能）
- T2: 310-510秒（R1使用、T1完了+10秒後）
- Makespan: 510秒

---

## Extension設計ガイドライン

RCPSP基底を拡張する際の原則：

1. **スキーマ追加のみ**
   - 既存フィールドの削除・変更は禁止
   - 新しいフィールドを`schema_fragment`として追加

2. **制約の追加**
   - 基本制約を緩和せず、追加制約のみ定義
   - `solver_mapping`で制約の実装方法を明示

3. **後方互換性**
   - Extension未使用時は基底DSLとして動作すること
   - Extensionフィールドが存在しない場合のデフォルト動作を定義

4. **命名規則**
   - Extension名: `snake_case`（例: `physical_space`, `shift`）
   - カテゴリ: `constraint`, `resource`, `objective`, `ui`のいずれか

---

## バージョン履歴

| バージョン | 日付 | 変更内容 |
|---|---|---|
| 1.0 | 2026-06-02 | 初版リリース（RCPSP基底定義） |

---

## 関連資料

- [CSPLib prob061: RCPSP](https://www.csplib.org/Problems/prob061/)
- [OptiBuddy Primary Document](../docs/OptiBuddy_primary_document.md)
- [Yard Planning Extension](./yard_planning_v1.md)
- [FJSP Extension](./fjsp_v1.md)
