# Solver Input DSL v1.0 仕様書

## 概要

**DSL名**: Solver Input DSL  
**バージョン**: 1.0  
**目的**: 業務DSLからソルバー（CP Optimizer）への入力形式を標準化

Solver Input DSLは、業務DSL（RCPSP + Extensions）をCP Optimizerが直接処理できる形式に変換した中間表現です。
ドメイン固有の概念（コンテナ、ヤード、船舶など）を抽象化し、ソルバーが理解できる「タスク」「リソース」「制約」に変換します。

---

## 設計原則

1. **ソルバー非依存性**: CP Optimizer以外のソルバー（OR-Tools、Gurobi等）にも対応可能な汎用構造
2. **制約の明示化**: 業務DSLの暗黙的制約をすべて明示的な制約として表現
3. **トレーサビリティ**: 元の業務DSLエンティティ（コンテナID等）への参照を保持
4. **拡張性**: 新しい制約タイプを追加可能な構造

---

## スキーマ定義

```json
{
  "solver_input": {
    "metadata": {
      "problem_class": "string",           // "RCPSP"
      "version": "string",                 // "1.0"
      "extensions": ["string"],            // ["physical_space", "shift", ...]
      "source_dsl_hash": "string",         // 元の業務DSLのハッシュ値（トレーサビリティ）
      "generated_at": "timestamp"
    },
    
    "tasks": {
      "type": "array",
      "description": "ソルバーが実行すべきタスクのリスト",
      "items": {
        "id": {
          "type": "string",
          "description": "タスクの一意識別子（例: P-C01, L-C01）"
        },
        "duration": {
          "type": "number",
          "description": "タスクの実行時間（秒単位）",
          "minimum": 0
        },
        "operation": {
          "type": "string",
          "description": "操作タイプ（PICK, LOAD, DISCHARGE, PLACE, MOVE, REHANDLE）"
        },
        "containerId": {
          "type": "string",
          "description": "関連するコンテナID（業務DSLへの参照）"
        },
        "sequence": {
          "type": "number",
          "description": "同一コンテナ内でのタスク実行順序",
          "minimum": 1
        },
        "resourceId": {
          "type": "string",
          "description": "割り当てられるリソースID（クレーン等）"
        },
        "resource_requirements": {
          "type": "object",
          "description": "リソース要求量（RCPSP基底互換）",
          "additionalProperties": {"type": "number"}
        },
        "start_min": {
          "type": "number",
          "description": "最早開始時刻（オプション、デフォルト0）",
          "default": 0
        },
        "start_max": {
          "type": "number",
          "description": "最遅開始時刻（オプション、デフォルトINFINITY）",
          "default": null
        }
      },
      "required": ["id", "duration", "operation", "containerId", "sequence"]
    },
    
    "resources": {
      "type": "object",
      "description": "利用可能なリソース定義",
      "properties": {
        "yard_cranes": {
          "type": "array",
          "items": {
            "id": "string",
            "capacity": "number",
            "bay": "array[number]",        // 担当ベイ範囲
            "shift_windows": "array[object]"  // シフト定義（shift extension使用時）
          }
        },
        "ship_cranes": {
          "type": "array",
          "items": {
            "id": "string",
            "capacity": "number",
            "bay": "array[number]",
            "shift_windows": "array[object]"
          }
        },
        "restricted_zones": {
          "type": "object",
          "description": "属性制約ゾーン（attribute_zones extension使用時）",
          "properties": {
            "reefer": "array[{bay, row, tier?}]",
            "imo": "array[{bay, row?, rows?}]"
          }
        }
      }
    },
    
    "constraints": {
      "type": "array",
      "description": "明示的な制約リスト",
      "items": {
        "type": {
          "type": "enum",
          "values": [
            "precedence",              // 先行関係制約
            "no_overlap",              // リソース非重複制約
            "physical_stack_order",    // 物理スタック順序制約
            "phase_separation",        // フェーズ分離制約（DISCHARGE→LOAD）
            "crane_interference",      // クレーン干渉制約
            "shift_window",            // シフト時間帯制約
            "shift_break",             // 休憩時間制約
            "attribute_zone",          // 属性ゾーン制約
            "oog_exclusion"            // OOG隣接排除制約
          ]
        },
        "params": {
          "type": "object",
          "description": "制約タイプごとのパラメータ"
        }
      }
    },
    
    "objective": {
      "type": "object",
      "description": "最適化目標",
      "properties": {
        "primary": {
          "type": "enum",
          "values": ["minimize_makespan", "minimize_cost", "minimize_penalty"]
        },
        "weights": {
          "type": "object",
          "description": "多目的最適化の重み",
          "properties": {
            "w_makespan": {"type": "number", "default": 1},
            "w_penalty": {"type": "number", "default": 1},
            "w_cost": {"type": "number", "default": 0}
          }
        },
        "penalty_pairs": {
          "type": "array",
          "description": "順序違反ペナルティ対象（LOAD/DISCHARGE順序）",
          "items": {
            "task_a": "string",
            "task_b": "string",
            "expected_order": "enum[a_before_b, b_before_a]"
          }
        }
      }
    },
    
    "config": {
      "type": "object",
      "description": "ソルバー実行設定",
      "properties": {
        "time_limit": {"type": "number", "default": 20},
        "solution_profiles": {
          "type": "array",
          "items": {
            "name": "string",
            "label": "string",
            "w_makespan": "number",
            "w_penalty": "number"
          }
        },
        "random_seed": {"type": "number", "default": 1}
      }
    }
  }
}
```

---

## 制約タイプ詳細

### 1. precedence（先行関係制約）

```json
{
  "type": "precedence",
  "params": {
    "from_task": "P-C01",
    "to_task": "L-C01",
    "delay": 2
  }
}
```

**CP Optimizer変換**:
```python
mdl.add(mdl.end_before_start(task_itvs["P-C01"], task_itvs["L-C01"], delay=2))
```

### 2. no_overlap（リソース非重複制約）

```json
{
  "type": "no_overlap",
  "params": {
    "resource_id": "RC-1",
    "task_ids": ["P-C01", "P-C02", "P-C03"]
  }
}
```

**CP Optimizer変換**:
```python
mdl.add(mdl.no_overlap([task_itvs[tid] for tid in ["P-C01", "P-C02", "P-C03"]]))
```

### 3. physical_stack_order（物理スタック順序制約）

```json
{
  "type": "physical_stack_order",
  "params": {
    "location": "yard",
    "bay": 1,
    "row": 1,
    "stack": [
      {"container_id": "C02", "tier": 2, "task_id": "P-C02"},
      {"container_id": "C01", "tier": 1, "task_id": "P-C01"}
    ],
    "order": "descending",  // tier降順（上から取る）
    "delay": 10
  }
}
```

**CP Optimizer変換**:
```python
# tier降順でPICK: C02 → C01
mdl.add(mdl.end_before_start(task_itvs["P-C02"], task_itvs["P-C01"], delay=10))
```

### 4. phase_separation（フェーズ分離制約）

```json
{
  "type": "phase_separation",
  "params": {
    "phase_a": "DISCHARGE",
    "phase_b": "LOAD",
    "phase_a_tasks": ["D-C01", "D-C02"],
    "phase_b_tasks": ["L-C03", "L-C04"],
    "constraint": "all_a_before_any_b"
  }
}
```

**CP Optimizer変換**:
```python
max_discharge_end = mdl.max([mdl.end_of(task_itvs[tid]) for tid in ["D-C01", "D-C02"]])
for load_tid in ["L-C03", "L-C04"]:
    mdl.add(mdl.start_of(task_itvs[load_tid]) >= max_discharge_end)
```

### 5. crane_interference（クレーン干渉制約）

```json
{
  "type": "crane_interference",
  "params": {
    "crane_a": "RC-1",
    "crane_b": "RC-2",
    "safety_bays": 2,
    "tasks_a": ["P-C01", "P-C02"],
    "tasks_b": ["P-C03", "P-C04"]
  }
}
```

**CP Optimizer変換**:
```python
mdl.add(mdl.no_overlap(
    [task_itvs[tid] for tid in ["P-C01", "P-C02"]] +
    [task_itvs[tid] for tid in ["P-C03", "P-C04"]]
))
```

### 6. shift_window（シフト時間帯制約）

```json
{
  "type": "shift_window",
  "params": {
    "resource_id": "RC-1",
    "windows": [
      {"start": 480, "end": 960}
    ],
    "task_ids": ["P-C01", "P-C02"]
  }
}
```

**CP Optimizer変換**:
```python
for tid in ["P-C01", "P-C02"]:
    mdl.add(mdl.start_of(task_itvs[tid]) >= 480)
    mdl.add(mdl.end_of(task_itvs[tid]) <= 960)
```

### 7. shift_break（休憩時間制約）

```json
{
  "type": "shift_break",
  "params": {
    "resource_id": "RC-1",
    "breaks": [
      {"start": 720, "end": 780}
    ],
    "task_ids": ["P-C01", "P-C02"]
  }
}
```

**CP Optimizer変換**:
```python
for tid in ["P-C01", "P-C02"]:
    mdl.add(mdl.logical_or(
        mdl.end_of(task_itvs[tid]) <= 720,
        mdl.start_of(task_itvs[tid]) >= 780
    ))
```

### 8. attribute_zone（属性ゾーン制約）

```json
{
  "type": "attribute_zone",
  "params": {
    "attribute": "REEFER",
    "container_id": "REEFER01",
    "allowed_zones": [
      {"bay": 1, "row": 1, "tier": null}
    ],
    "current_location": {"bay": 2, "row": 1, "tier": 1},
    "violation": true
  }
}
```

**Issue生成**: ソルバー制約ではなくIssue検出に使用

### 9. oog_exclusion（OOG隣接排除制約）

```json
{
  "type": "oog_exclusion",
  "params": {
    "oog_container_id": "OOG01",
    "excluded_slots": [
      {"bay": 1, "row": 2, "tier": null},
      {"bay": 1, "row": 1, "tier": 2}
    ],
    "affected_containers": ["C02", "C03"],
    "oog_pick_task": "P-OOG01",
    "blocked_tasks": ["P-C02", "P-C03"]
  }
}
```

**CP Optimizer変換**:
```python
oog_last_pick = mdl.end_of(task_itvs["P-OOG01"])
for tid in ["P-C02", "P-C03"]:
    mdl.add(mdl.start_of(task_itvs[tid]) >= oog_last_pick)
```

---

## 業務DSL → Solver Input DSL 変換例

### 入力: 業務DSL（Yard Planning）

```json
{
  "problem_class": "RCPSP",
  "version": "1.0",
  "extensions": ["physical_space", "phase_separation"],
  "containers": [
    {
      "id": "C01",
      "yard": {"bay": 1, "row": 1, "tier": 1},
      "ship": {"bay": 10, "row": 1, "tier": 1},
      "order": 1,
      "operation_type": "LOAD"
    },
    {
      "id": "C02",
      "yard": {"bay": 1, "row": 1, "tier": 2},
      "ship": {"bay": 10, "row": 1, "tier": 2},
      "order": 2,
      "operation_type": "LOAD"
    },
    {
      "id": "C03",
      "ship": {"bay": 12, "row": 1, "tier": 1},
      "yard": {"bay": 2, "row": 1, "tier": 1},
      "order": 1,
      "operation_type": "DISCHARGE"
    }
  ],
  "config": {
    "current_port": "JPYOK",
    "time_pick": 180,
    "time_load": 300,
    "time_discharge": 300,
    "time_place": 180,
    "safety_gap": 10
  }
}
```

### 出力: Solver Input DSL

```json
{
  "metadata": {
    "problem_class": "RCPSP",
    "version": "1.0",
    "extensions": ["physical_space", "phase_separation"],
    "source_dsl_hash": "sha256:abc123...",
    "generated_at": "2026-06-02T11:30:00Z"
  },
  
  "tasks": [
    {"id": "P-C01", "duration": 180, "operation": "PICK", "containerId": "C01", "sequence": 1, "resourceId": "RC-1"},
    {"id": "L-C01", "duration": 300, "operation": "LOAD", "containerId": "C01", "sequence": 2, "resourceId": "GC-1"},
    {"id": "P-C02", "duration": 180, "operation": "PICK", "containerId": "C02", "sequence": 1, "resourceId": "RC-1"},
    {"id": "L-C02", "duration": 300, "operation": "LOAD", "containerId": "C02", "sequence": 2, "resourceId": "GC-1"},
    {"id": "D-C03", "duration": 300, "operation": "DISCHARGE", "containerId": "C03", "sequence": 1, "resourceId": "GC-1"},
    {"id": "PL-C03", "duration": 180, "operation": "PLACE", "containerId": "C03", "sequence": 2, "resourceId": "RC-1"}
  ],
  
  "resources": {
    "yard_cranes": [{"id": "RC-1", "capacity": 1, "bay": [1, 2, 3]}],
    "ship_cranes": [{"id": "GC-1", "capacity": 1, "bay": [10, 11, 12]}]
  },
  
  "constraints": [
    {"type": "precedence", "params": {"from_task": "P-C01", "to_task": "L-C01", "delay": 2}},
    {"type": "precedence", "params": {"from_task": "P-C02", "to_task": "L-C02", "delay": 2}},
    {"type": "precedence", "params": {"from_task": "D-C03", "to_task": "PL-C03", "delay": 2}},
    {"type": "no_overlap", "params": {"resource_id": "RC-1", "task_ids": ["P-C01", "P-C02", "PL-C03"]}},
    {"type": "no_overlap", "params": {"resource_id": "GC-1", "task_ids": ["L-C01", "L-C02", "D-C03"]}},
    {
      "type": "physical_stack_order",
      "params": {
        "location": "yard",
        "bay": 1,
        "row": 1,
        "stack": [
          {"container_id": "C02", "tier": 2, "task_id": "P-C02"},
          {"container_id": "C01", "tier": 1, "task_id": "P-C01"}
        ],
        "order": "descending",
        "delay": 10
      }
    },
    {
      "type": "physical_stack_order",
      "params": {
        "location": "ship",
        "bay": 10,
        "row": 1,
        "stack": [
          {"container_id": "C01", "tier": 1, "task_id": "L-C01"},
          {"container_id": "C02", "tier": 2, "task_id": "L-C02"}
        ],
        "order": "ascending",
        "delay": 1
      }
    },
    {
      "type": "phase_separation",
      "params": {
        "phase_a": "DISCHARGE",
        "phase_b": "LOAD",
        "phase_a_tasks": ["D-C03", "PL-C03"],
        "phase_b_tasks": ["L-C01", "L-C02"],
        "constraint": "all_a_before_any_b"
      }
    }
  ],
  
  "objective": {
    "primary": "minimize_makespan",
    "weights": {
      "w_makespan": 1,
      "w_penalty": 300
    },
    "penalty_pairs": [
      {"task_a": "L-C01", "task_b": "L-C02", "expected_order": "a_before_b"}
    ]
  },
  
  "config": {
    "time_limit": 20,
    "solution_profiles": [
      {"name": "Plan A", "label": "全作業時間最短", "w_makespan": 1, "w_penalty": 1},
      {"name": "Plan B", "label": "積み順遵守", "w_makespan": 1, "w_penalty": 300}
    ],
    "random_seed": 1
  }
}
```

---

## 変換ロジック実装ガイド

### business_to_solver.py 実装例

```python
def convert_business_to_solver(business_dsl: dict) -> dict:
    """業務DSL → Solver Input DSL変換"""
    
    solver_input = {
        "metadata": {
            "problem_class": business_dsl["problem_class"],
            "version": business_dsl["version"],
            "extensions": business_dsl.get("extensions", []),
            "source_dsl_hash": compute_hash(business_dsl),
            "generated_at": datetime.utcnow().isoformat() + "Z"
        },
        "tasks": [],
        "resources": {},
        "constraints": [],
        "objective": {},
        "config": business_dsl.get("config", {})
    }
    
    # 1. タスク生成
    for container in business_dsl.get("containers", []):
        op_type = container.get("operation_type", "LOAD")
        if op_type == "LOAD":
            solver_input["tasks"].extend([
                {
                    "id": f"P-{container['id']}",
                    "duration": business_dsl["config"].get("time_pick", 180),
                    "operation": "PICK",
                    "containerId": container["id"],
                    "sequence": 1,
                    "resourceId": "RC-1"  # デフォルト
                },
                {
                    "id": f"L-{container['id']}",
                    "duration": business_dsl["config"].get("time_load", 300),
                    "operation": "LOAD",
                    "containerId": container["id"],
                    "sequence": 2,
                    "resourceId": "GC-1"
                }
            ])
        elif op_type == "DISCHARGE":
            solver_input["tasks"].extend([
                {
                    "id": f"D-{container['id']}",
                    "duration": business_dsl["config"].get("time_discharge", 300),
                    "operation": "DISCHARGE",
                    "containerId": container["id"],
                    "sequence": 1,
                    "resourceId": "GC-1"
                },
                {
                    "id": f"PL-{container['id']}",
                    "duration": business_dsl["config"].get("time_place", 180),
                    "operation": "PLACE",
                    "containerId": container["id"],
                    "sequence": 2,
                    "resourceId": "RC-1"
                }
            ])
    
    # 2. リソース定義
    solver_input["resources"] = business_dsl.get("resources", {})
    
    # 3. 制約生成
    # 3-1. precedence制約（コンテナシーケンス）
    for task in solver_input["tasks"]:
        if task["sequence"] > 1:
            prev_seq = task["sequence"] - 1
            prev_task = next(
                (t for t in solver_input["tasks"] 
                 if t["containerId"] == task["containerId"] and t["sequence"] == prev_seq),
                None
            )
            if prev_task:
                solver_input["constraints"].append({
                    "type": "precedence",
                    "params": {
                        "from_task": prev_task["id"],
                        "to_task": task["id"],
                        "delay": 2
                    }
                })
    
    # 3-2. physical_space制約（extension使用時）
    if "physical_space" in business_dsl.get("extensions", []):
        solver_input["constraints"].extend(
            build_physical_space_constraints(business_dsl, solver_input["tasks"])
        )
    
    # 3-3. phase_separation制約（extension使用時）
    if "phase_separation" in business_dsl.get("extensions", []):
        discharge_tasks = [t["id"] for t in solver_input["tasks"] if t["operation"] in ("DISCHARGE", "PLACE")]
        load_tasks = [t["id"] for t in solver_input["tasks"] if t["operation"] in ("LOAD", "MOVE")]
        if discharge_tasks and load_tasks:
            solver_input["constraints"].append({
                "type": "phase_separation",
                "params": {
                    "phase_a": "DISCHARGE",
                    "phase_b": "LOAD",
                    "phase_a_tasks": discharge_tasks,
                    "phase_b_tasks": load_tasks,
                    "constraint": "all_a_before_any_b"
                }
            })
    
    # 4. 目的関数
    solver_input["objective"] = {
        "primary": "minimize_makespan",
        "weights": {
            "w_makespan": 1,
            "w_penalty": business_dsl["config"].get("time_load", 300)
        },
        "penalty_pairs": build_penalty_pairs(business_dsl, solver_input["tasks"])
    }
    
    return solver_input
```

---

## バージョン履歴

| バージョン | 日付 | 変更内容 |
|---|---|---|
| 1.0 | 2026-06-02 | 初版リリース |

---

## 関連資料

- [RCPSP Base DSL](./rcpsp_base_v1.md)
- [Solver Output DSL](./solver_output_dsl_v1.md)
- [full_yard_solver.py](../solvers/full_yard_solver.py)
- [OptiBuddy 4DSL Design Plan](../../docs/OptiBuddy_4DSL_Design_Plan.md)
