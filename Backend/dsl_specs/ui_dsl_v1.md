# UI DSL v1.0 仕様書

## 概要

**DSL名**: UI DSL  
**バージョン**: 1.0  
**目的**: Solver Output DSLをフロントエンド表示用の構造に変換した最終表現

UI DSLは、ソルバーの実行結果を、React/TypeScriptフロントエンドが直接レンダリングできる形式に変換した表現です。
Ganttチャート、Grid（Yard/Vessel）、KPIダッシュボード、Issueリストなど、すべてのUI要素に必要なデータ構造を提供します。

---

## 設計原則

1. **UI最適化**: フロントエンドコンポーネントが直接利用できる構造
2. **完全性**: すべてのビュー（Overview、Timeline、Spatial、Issues）に必要なデータを含む
3. **トレーサビリティ**: Solver Output DSLへの参照を保持
4. **パフォーマンス**: 時系列スナップショット生成など、重い計算を事前実行

---

## スキーマ定義

```typescript
{
  "ui_dsl": {
    "metadata": {
      "solver_name": "string",              // "CP Optimizer 22.1.1"
      "problem_class": "string",            // "RCPSP"
      "extensions": ["string"],             // ["physical_space", "shift", ...]
      "solved_at": "timestamp",             // "2026-06-02T11:30:15Z"
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
        "name": "string",                   // "Plan A"
        "label": "string",                  // "全作業時間最短"
        "profile": {
          "w_makespan": "number",
          "w_penalty": "number",
          "w_cost": "number"
        },
        
        "tasks": {
          "type": "array",
          "description": "Gantt表示用タスクリスト",
          "items": {
            "id": "string",                 // "P-C01"
            "containerId": "string",        // "C01"
            "operation": "enum",            // "LOAD" | "DISCHARGE" | "MOVE" | "REHANDLE" | "PICK" | "PLACE"
            "resource": "string",           // "RC-1"
            "order": "number",              // 積み順（業務DSLから）
            "status": "enum",               // "pending" | "scheduled"
            "start": "number",              // 開始時刻（秒）
            "end": "number",                // 終了時刻（秒）
            "weight": "number",             // コンテナ重量（kg）
            "from": {
              "domain": "enum",             // "YARD" | "VESSEL" | "NONE"
              "bay": "number",
              "row": "number",
              "tier": "number"
            },
            "to": {
              "domain": "enum",
              "bay": "number",
              "row": "number",
              "tier": "number"
            },
            "domain": "enum",               // "YARD" | "VESSEL"
            "yard": {
              "bay": "number",
              "row": "number",
              "tier": "number"
            },
            "ship": {
              "bay": "number",
              "row": "number",
              "tier": "number"
            },
            "ports": {
              "pol": "string",              // "JPYOK"
              "pod": "string"               // "USOAK"
            }
          }
        },
        
        "snapshots": {
          "type": "object",
          "description": "時系列スナップショット（Grid表示用）",
          "properties": {
            "timePoints": {
              "type": "array[number]",
              "description": "スナップショット時刻リスト（秒）"
            },
            "data": {
              "type": "object",
              "description": "各時刻のスナップショット",
              "additionalProperties": {
                "yard": {
                  "type": "object",
                  "description": "Yard Grid構造",
                  "additionalProperties": {
                    "type": "object",
                    "description": "Bay → Row → Tier → ContainerId",
                    "additionalProperties": {
                      "type": "object",
                      "additionalProperties": "string"
                    }
                  }
                },
                "ship": {
                  "type": "object",
                  "description": "Vessel Grid構造",
                  "additionalProperties": {
                    "type": "object",
                    "additionalProperties": {
                      "type": "object",
                      "additionalProperties": "string"
                    }
                  }
                },
                "containers": {
                  "type": "object",
                  "description": "コンテナ詳細情報",
                  "additionalProperties": {
                    "containerId": "string",
                    "weight": "number",
                    "currentPos": "enum",       // "YARD" | "VESSEL"
                    "yard": "object",
                    "ship": "object",
                    "pod": "string",
                    "pol": "string",
                    "visibleInYard": "boolean",
                    "visibleInVessel": "boolean"
                  }
                },
                "availableBays": {
                  "type": "array[number]",
                  "description": "利用可能なベイリスト"
                }
              }
            }
          }
        },
        
        "kpi": {
          "type": "object",
          "description": "KPIダッシュボード用メトリクス",
          "properties": {
            "makespan": {
              "type": "number",
              "description": "全タスク完了時刻（秒）"
            },
            "makespanMin": {
              "type": "number",
              "description": "全タスク完了時刻（分）"
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
              "description": "目的関数値"
            },
            "solve_time": {
              "type": "number",
              "description": "この解のソルブ時間（秒）"
            },
            "craneUtil": {
              "type": "array",
              "description": "クレーン稼働率",
              "items": {
                "id": "string",
                "util": "number"
              }
            },
            "rehandleCount": {
              "type": "number",
              "description": "リハンドリング回数"
            },
            "avgWaitMin": {
              "type": "number",
              "description": "平均待機時間（分）"
            },
            "resolvedCount": {
              "type": "number",
              "description": "解決済みイシュー数"
            },
            "skippedCount": {
              "type": "number",
              "description": "スキップ済みイシュー数"
            },
            "remainCount": {
              "type": "number",
              "description": "未解決イシュー数"
            }
          }
        }
      }
    },
    
    "issues": {
      "type": "array",
      "description": "検出されたイシュー（Issue View用）",
      "items": {
        "id": "string",
        "title": "string",
        "message": "string",
        "containerId": "string",
        "relatedContainerIds": "array[string]",
        "severity": "enum",                 // "CRITICAL" | "WARNING" | "INFO"
        "category": "enum",                 // "YARD" | "MBP" | "WEIGHT" | "ATTR" | "SHIFT"
        "fixStrategy": "enum",              // "MANUAL" | "AI" | "NONE"
        "status": "enum",                   // "UNRESOLVED" | "FIXED" | "ACCEPTED"
        "action": "enum",                   // "NONE" | "FIX" | "ACCEPT"
        "pair": "array[string, string]"     // 関連コンテナペア（オプション）
      }
    },
    
    "config": {
      "type": "object",
      "description": "UI表示設定",
      "properties": {
        "yardBays": {
          "type": "array[number]",
          "description": "Yard利用可能ベイリスト"
        },
        "shipBays": {
          "type": "array[number]",
          "description": "Vessel利用可能ベイリスト"
        },
        "yard_limits": {
          "max_bay": "number",
          "max_row": "number",
          "max_tier": "number"
        },
        "vessel_limits": {
          "max_bay": "number",
          "max_row": "number",
          "max_tier": "number"
        },
        "operation_start": {
          "type": "string | number",
          "description": "作業開始時刻（HH:MM形式 or 秒）"
        },
        "operation_date": {
          "type": "string",
          "description": "作業日（YYYY-MM-DD形式）"
        },
        "current_port": "string",
        "shifts": {
          "type": "array",
          "items": {
            "id": "string",
            "start": "number",
            "end": "number",
            "cranes": "array[string]"
          }
        },
        "breaks": {
          "type": "array",
          "items": {
            "shift": "string",
            "start": "number",
            "duration": "number"
          }
        },
        "restricted_zones": {
          "reefer": "array[{bay, row, tier?}]",
          "imo": "array[{bay, row?, rows?}]"
        }
      }
    },
    
    "highlight": {
      "type": "object",
      "description": "ハイライト状態管理",
      "properties": {
        "kind": "enum",                     // "none" | "active"
        "hoverContainerId": "string",
        "selectedIssueId": "string",
        "relatedContainerIds": "array[string]"
      }
    }
  }
}
```

---

## UI DSL → React Component マッピング

### 1. Overview View

**使用データ**:
- `solutions[selectedIndex].kpi` → KpiDashboard
- `solutions[selectedIndex].tasks` → TaskGantt（簡易版）
- `issues` → IssueListView（サマリー）

**コンポーネント**:
```typescript
<OverviewView>
  <KpiDashboard kpi={solution.kpi} />
  <GanttPanel tasks={solution.tasks} />
  <IssueListView issues={issues} />
</OverviewView>
```

### 2. Timeline View

**使用データ**:
- `solutions[selectedIndex].tasks` → TaskGantt（詳細版）
- `config.shifts` → シフト時間帯表示
- `config.breaks` → 休憩時間表示

**コンポーネント**:
```typescript
<TimelineView>
  <GanttPanel 
    tasks={solution.tasks} 
    shifts={config.shifts}
    breaks={config.breaks}
    fill={true}
  />
</TimelineView>
```

### 3. Spatial View

**使用データ**:
- `solutions[selectedIndex].snapshots` → YardView / VesselView
- `config.yardBays` → ベイ選択
- `config.shipBays` → ベイ選択
- `highlight` → コンテナハイライト

**コンポーネント**:
```typescript
<SpatialView>
  <SimulationController currentTime={time} />
  <SolutionSelector solutions={solutions} />
  <YardVesselPanels>
    <YardView 
      snapshot={snapshots.data[currentTime].yard}
      containers={snapshots.data[currentTime].containers}
      highlight={highlight}
    />
    <VesselView 
      snapshot={snapshots.data[currentTime].ship}
      containers={snapshots.data[currentTime].containers}
      highlight={highlight}
    />
  </YardVesselPanels>
</SpatialView>
```

### 4. Issues View

**使用データ**:
- `issues` → IssueListView（詳細版）
- `solutions[selectedIndex].tasks` → 関連タスク表示

**コンポーネント**:
```typescript
<IssuesView>
  <IssueListView 
    issues={issues}
    onIssueClick={handleIssueClick}
    onApplyFix={handleApplyFix}
  />
</IssuesView>
```

---

## Solver Output DSL → UI DSL 変換例

### 入力: Solver Output DSL

```json
{
  "metadata": {
    "solver_name": "CP Optimizer",
    "problem_class": "RCPSP",
    "extensions": ["physical_space", "phase_separation"],
    "solved_at": "2026-06-02T11:30:15Z",
    "total_solve_time": 18.5
  },
  "status": "ok",
  "solutions": [
    {
      "name": "Plan A",
      "label": "全作業時間最短",
      "profile": {"w_makespan": 1, "w_penalty": 1, "w_cost": 0},
      "tasks": [
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
        }
      ],
      "metrics": {
        "makespan": 1455,
        "penalty": 0,
        "cost": 0,
        "objective_value": 1455,
        "solve_time": 8.2,
        "optimality_gap": 0.0
      }
    }
  ],
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
    "current_port": "JPYOK"
  }
}
```

### 出力: UI DSL

```json
{
  "metadata": {
    "solver_name": "CP Optimizer",
    "problem_class": "RCPSP",
    "extensions": ["physical_space", "phase_separation"],
    "solved_at": "2026-06-02T11:30:15Z",
    "total_solve_time": 18.5
  },
  "status": "ok",
  "solutions": [
    {
      "name": "Plan A",
      "label": "全作業時間最短",
      "profile": {"w_makespan": 1, "w_penalty": 1, "w_cost": 0},
      "tasks": [
        {
          "id": "P-C01",
          "containerId": "C01",
          "operation": "PICK",
          "resource": "RC-1",
          "order": 1,
          "status": "scheduled",
          "start": 672,
          "end": 852,
          "weight": 20000,
          "from": {
            "domain": "YARD",
            "bay": 1,
            "row": 1,
            "tier": 1
          },
          "to": {
            "domain": "NONE",
            "bay": 0,
            "row": 0,
            "tier": 0
          },
          "domain": "YARD",
          "yard": {"bay": 1, "row": 1, "tier": 1},
          "ship": {"bay": 10, "row": 1, "tier": 1},
          "ports": {"pol": "JPYOK", "pod": "USOAK"}
        },
        {
          "id": "L-C01",
          "containerId": "C01",
          "operation": "LOAD",
          "resource": "GC-1",
          "order": 1,
          "status": "scheduled",
          "start": 854,
          "end": 1154,
          "weight": 20000,
          "from": {
            "domain": "NONE",
            "bay": 0,
            "row": 0,
            "tier": 0
          },
          "to": {
            "domain": "VESSEL",
            "bay": 10,
            "row": 1,
            "tier": 1
          },
          "domain": "VESSEL",
          "yard": {"bay": 1, "row": 1, "tier": 1},
          "ship": {"bay": 10, "row": 1, "tier": 1},
          "ports": {"pol": "JPYOK", "pod": "USOAK"}
        }
      ],
      "snapshots": {
        "timePoints": [0, 672, 852, 854, 1154, 1455],
        "data": {
          "0": {
            "yard": {
              "1": {
                "1": {
                  "1": "C01"
                }
              }
            },
            "ship": {},
            "containers": {
              "C01": {
                "containerId": "C01",
                "weight": 20000,
                "currentPos": "YARD",
                "yard": {"bay": 1, "row": 1, "tier": 1},
                "ship": {"bay": 10, "row": 1, "tier": 1},
                "pod": "USOAK",
                "pol": "JPYOK",
                "visibleInYard": true,
                "visibleInVessel": false
              }
            },
            "availableBays": [1]
          },
          "672": {
            "yard": {
              "1": {
                "1": {
                  "1": "C01"
                }
              }
            },
            "ship": {},
            "containers": {
              "C01": {
                "containerId": "C01",
                "weight": 20000,
                "currentPos": "YARD",
                "yard": {"bay": 1, "row": 1, "tier": 1},
                "ship": {"bay": 10, "row": 1, "tier": 1},
                "pod": "USOAK",
                "pol": "JPYOK",
                "visibleInYard": true,
                "visibleInVessel": false
              }
            },
            "availableBays": [1]
          },
          "852": {
            "yard": {},
            "ship": {},
            "containers": {
              "C01": {
                "containerId": "C01",
                "weight": 20000,
                "currentPos": "YARD",
                "yard": {"bay": 1, "row": 1, "tier": 1},
                "ship": {"bay": 10, "row": 1, "tier": 1},
                "pod": "USOAK",
                "pol": "JPYOK",
                "visibleInYard": false,
                "visibleInVessel": false
              }
            },
            "availableBays": []
          },
          "1154": {
            "yard": {},
            "ship": {
              "10": {
                "1": {
                  "1": "C01"
                }
              }
            },
            "containers": {
              "C01": {
                "containerId": "C01",
                "weight": 20000,
                "currentPos": "VESSEL",
                "yard": {"bay": 1, "row": 1, "tier": 1},
                "ship": {"bay": 10, "row": 1, "tier": 1},
                "pod": "USOAK",
                "pol": "JPYOK",
                "visibleInYard": false,
                "visibleInVessel": true
              }
            },
            "availableBays": [10]
          }
        }
      },
      "kpi": {
        "makespan": 1455,
        "makespanMin": 24,
        "penalty": 0,
        "cost": 0,
        "objective_value": 1455,
        "solve_time": 8.2,
        "craneUtil": [
          {"id": "RC-1", "util": 75},
          {"id": "GC-1", "util": 82}
        ],
        "rehandleCount": 0,
        "avgWaitMin": 2,
        "resolvedCount": 0,
        "skippedCount": 0,
        "remainCount": 0
      }
    }
  ],
  "issues": [],
  "config": {
    "yardBays": [1, 2, 3, 4, 5],
    "shipBays": [10, 11, 12, 13, 14, 15, 16, 17, 18, 19, 20],
    "yard_limits": {
      "max_bay": 5,
      "max_row": 3,
      "max_tier": 4
    },
    "vessel_limits": {
      "max_bay": 20,
      "max_row": 8,
      "max_tier": 6
    },
    "operation_start": "08:00",
    "operation_date": "2026-06-02",
    "current_port": "JPYOK",
    "shifts": [],
    "breaks": [],
    "restricted_zones": {
      "reefer": [],
      "imo": []
    }
  },
  "highlight": {
    "kind": "none",
    "hoverContainerId": "",
    "selectedIssueId": "",
    "relatedContainerIds": []
  }
}
```

---

## 変換ロジック実装ガイド

### solver_to_ui.py 実装例

```python
def convert_solver_to_ui(solver_output: dict) -> dict:
    """Solver Output DSL → UI DSL変換"""
    
    ui_dsl = {
        "metadata": solver_output["metadata"],
        "status": solver_output["status"],
        "solutions": [],
        "issues": convert_issues(solver_output.get("issues", [])),
        "config": build_ui_config(solver_output),
        "highlight": {
            "kind": "none",
            "hoverContainerId": "",
            "selectedIssueId": "",
            "relatedContainerIds": []
        }
    }
    
    # 各ソリューションを変換
    for solution in solver_output.get("solutions", []):
        ui_solution = {
            "name": solution["name"],
            "label": solution["label"],
            "profile": solution["profile"],
            "tasks": convert_tasks(solution["tasks"], solver_output["containers"]),
            "snapshots": build_snapshots(solution["tasks"], solver_output["containers"]),
            "kpi": calculate_kpi(solution, solver_output)
        }
        ui_dsl["solutions"].append(ui_solution)
    
    return ui_dsl


def convert_tasks(solver_tasks: list, containers: list) -> list:
    """Solver Output Task → UI Task変換"""
    ui_tasks = []
    container_map = {c["id"]: c for c in containers}
    
    for task in solver_tasks:
        container = container_map.get(task["containerId"], {})
        
        # from/to Location決定
        from_loc, to_loc = determine_locations(task["operation"], container)
        
        ui_task = {
            "id": task["id"],
            "containerId": task["containerId"],
            "operation": task["operation"],
            "resource": task["resourceId"],
            "order": task.get("order", 0),
            "status": "scheduled",
            "start": task["start"],
            "end": task["end"],
            "weight": container.get("weight", 0),
            "from": from_loc,
            "to": to_loc,
            "domain": "YARD" if task["operation"] in ("PICK", "PLACE", "REHANDLE") else "VESSEL",
            "yard": container.get("yard", {"bay": 0, "row": 0, "tier": 0}),
            "ship": container.get("ship", {"bay": 0, "row": 0, "tier": 0}),
            "ports": {
                "pol": container.get("pol", ""),
                "pod": container.get("pod", "")
            }
        }
        ui_tasks.append(ui_task)
    
    return ui_tasks


def determine_locations(operation: str, container: dict) -> tuple:
    """操作タイプからfrom/to Locationを決定"""
    yard = container.get("yard", {"bay": 0, "row": 0, "tier": 0})
    ship = container.get("ship", {"bay": 0, "row": 0, "tier": 0})
    none_loc = {"domain": "NONE", "bay": 0, "row": 0, "tier": 0}
    
    if operation == "PICK":
        return (
            {"domain": "YARD", **yard},
            none_loc
        )
    elif operation == "LOAD":
        return (
            none_loc,
            {"domain": "VESSEL", **ship}
        )
    elif operation == "DISCHARGE":
        return (
            {"domain": "VESSEL", **ship},
            none_loc
        )
    elif operation == "PLACE":
        return (
            none_loc,
            {"domain": "YARD", **yard}
        )
    else:
        return (none_loc, none_loc)


def build_snapshots(tasks: list, containers: list) -> dict:
    """時系列スナップショット生成"""
    # タスクイベント時刻を収集
    time_points = sorted(set([0] + [t["start"] for t in tasks] + [t["end"] for t in tasks]))
    
    snapshots = {
        "timePoints": time_points,
        "data": {}
    }
    
    for t in time_points:
        snapshot = build_snapshot_at_time(t, tasks, containers)
        snapshots["data"][str(t)] = snapshot
    
    return snapshots


def build_snapshot_at_time(current_time: int, tasks: list, containers: list) -> dict:
    """特定時刻のスナップショット生成"""
    snapshot = {
        "yard": {},
        "ship": {},
        "containers": {},
        "availableBays": []
    }
    
    container_map = {c["id"]: c for c in containers}
    
    # 各コンテナの状態を判定
    for container in containers:
        cid = container["id"]
        
        # このコンテナに関連するタスクを取得
        container_tasks = [t for t in tasks if t["containerId"] == cid]
        container_tasks.sort(key=lambda x: x["sequence"])
        
        # 現在時刻での状態を判定
        visible_in_yard = False
        visible_in_vessel = False
        current_pos = "YARD"
        
        for task in container_tasks:
            if current_time < task["start"]:
                break
            
            if task["operation"] == "PICK" and current_time >= task["end"]:
                visible_in_yard = False
            elif task["operation"] == "LOAD" and current_time >= task["end"]:
                visible_in_vessel = True
                current_pos = "VESSEL"
            elif task["operation"] == "DISCHARGE" and current_time >= task["end"]:
                visible_in_vessel = False
            elif task["operation"] == "PLACE" and current_time >= task["end"]:
                visible_in_yard = True
                current_pos = "YARD"
        
        # 初期状態（t=0）
        if current_time == 0:
            if container.get("operation_type") == "LOAD":
                visible_in_yard = True
                current_pos = "YARD"
            elif container.get("operation_type") == "DISCHARGE":
                visible_in_vessel = True
                current_pos = "VESSEL"
        
        # スナップショットに追加
        snapshot["containers"][cid] = {
            "containerId": cid,
            "weight": container.get("weight", 0),
            "currentPos": current_pos,
            "yard": container.get("yard", {"bay": 0, "row": 0, "tier": 0}),
            "ship": container.get("ship", {"bay": 0, "row": 0, "tier": 0}),
            "pod": container.get("pod", ""),
            "pol": container.get("pol", ""),
            "visibleInYard": visible_in_yard,
            "visibleInVessel": visible_in_vessel
        }
        
        # Grid構造に追加
        if visible_in_yard:
            yard = container["yard"]
            b, r, t = yard["bay"], yard["row"], yard["tier"]
            if b not in snapshot["yard"]:
                snapshot["yard"][b] = {}
            if r not in snapshot["yard"][b]:
                snapshot["yard"][b][r] = {}
            snapshot["yard"][b][r][t] = cid
            
            if b not in snapshot["availableBays"]:
                snapshot["availableBays"].append(b)
        
        if visible_in_vessel:
            ship = container["ship"]
            b, r, t = ship["bay"], ship["row"], ship["tier"]
            if b not in snapshot["ship"]:
                snapshot["ship"][b] = {}
            if r not in snapshot["ship"][b]:
                snapshot["ship"][b][r] = {}
            snapshot["ship"][b][r][t] = cid
            
            if b not in snapshot["availableBays"]:
                snapshot["availableBays"].append(b)
    
    snapshot["availableBays"].sort()
    return snapshot


def calculate_kpi(solution: dict, solver_output: dict) -> dict:
    """KPI計算"""
    tasks = solution["tasks"]
    metrics = solution["metrics"]
    
    # クレーン稼働率計算
    crane_util = {}
    for task in tasks:
        resource = task["resourceId"]
        if resource not in crane_util:
            crane_util[resource] = 0
        crane_util[resource] += task["duration"]
    
    makespan = metrics["makespan"]
    crane_util_list = [
        {
            "id": crane_id,
            "util": round((total_time / makespan) * 100) if makespan > 0 else 0
        }
        for crane_id, total_time in crane_util.items()
    ]
    
    # リハンドリング回数
    rehandle_count = sum(1 for t in tasks if t["operation"] == "REHANDLE")
    
    # 平均待機時間（簡易計算）
    total_wait = 0
    task_count = 0
    for task in tasks:
        if task["sequence"] > 1:
            prev_task = next((t for t in tasks if t["containerId"] == task["containerId"] and t["sequence"] == task["sequence"] - 1), None)
            if prev_task:
                wait = task["start"] - prev_task["end"]
                total_wait += wait
                task_count += 1
    
    avg_wait_min = round(total_wait / task_count / 60) if task_count > 0 else 0
    
    return {
        "makespan": metrics["makespan"],
        "makespanMin": round(metrics["makespan"] / 60),
        "penalty": metrics.get("penalty", 0),
        "cost": metrics.get("cost", 0),
        "objective_value": metrics["objective_value"],
        "solve_time": metrics["solve_time"],
        "craneUtil": crane_util_list,
        "rehandleCount": rehandle_count,
        "avgWaitMin": avg_wait_min,
        "resolvedCount": 0,
        "skippedCount": 0,
        "remainCount": len(solver_output.get("issues", []))
    }


def convert_issues(solver_issues: list) -> list:
    """Solver Output Issue → UI Issue変換"""
    ui_issues = []
    
    for issue in solver_issues:
        ui_issue = {
            "id": issue["id"],
            "title": issue["title"],
            "message": issue["message"],
            "containerId": issue.get("containerId", ""),
            "relatedContainerIds": issue.get("relatedContainerIds", []),
            "severity": issue["severity"],
            "category": issue.get("category", "YARD"),
            "fixStrategy": "MANUAL",  # デフォルト
            "status": "UNRESOLVED",
            "action": "NONE",
            "pair": issue.get("pair")
        }
        ui_issues.append(ui_issue)
    
    return ui_issues


def build_ui_config(solver_output: dict) -> dict:
    """UI設定構築"""
    config = solver_output.get("config", {})
    
    # Yard/Ship Bays抽出
    yard_bays = set()
    ship_bays = set()
    for container in solver_output.get("containers", []):
        if "yard" in container:
            yard_bays.add(container["yard"]["bay"])
        if "ship" in container:
            ship_bays.add(container["ship"]["bay"])
    
    return {
        "yardBays": sorted(list(yard_bays)),
        "shipBays": sorted(list(ship_bays)),
        "yard_limits": solver_output.get("yard_limits", {}),
        "vessel_limits": solver_output.get("vessel_limits", {}),
        "operation_start": config.get("operation_start", "08:00"),
        "operation_date": config.get("operation_date", ""),
        "current_port": config.get("current_port", ""),
        "shifts": config.get("shifts", []),
        "breaks": config.get("breaks", []),
        "restricted_zones": solver_output.get("resources", {}).get("restricted_zones", {})
    }
```

---

## TypeScript型定義（Frontend）

```typescript
// Frontend/src/domain/types.ts に追加

export interface UiDsl {
  metadata: {
    solver_name: string;
    problem_class: string;
    extensions: string[];
    solved_at: string;
    total_solve_time: number;
  };
  status: "ok" | "error" | "timeout" | "infeasible";
  solutions: UiSolution[];
  issues: Issue[];
  config: UiConfig;
  highlight: HighlightId;
}

export interface UiSolution {
  name: string;
  label: string;
  profile: SolutionProfile;
  tasks: Task[];
  snapshots: {
    timePoints: number[];
    data: Record<string, Snapshot>;
  };
  kpi: KpiMetrics;
}

export interface KpiMetrics {
  makespan: number;
  makespanMin: number;
  penalty: number;
  cost: number;
  objective_value: number;
  solve_time: number;
  craneUtil: { id: string; util: number }[];
  rehandleCount: number;
  avgWaitMin: number;
  resolvedCount: number;
  skippedCount: number;
  remainCount: number;
}

export interface UiConfig {
  yardBays: number[];
  shipBays: number[];
  yard_limits: {
    max_bay: number;
    max_row: number;
    max_tier: number;
  };
  vessel_limits: {
    max_bay: number;
    max_row: number;
    max_tier: number;
  };
  operation_start: string | number;
  operation_date: string;
  current_port: string;
  shifts: { id: string; start: number; end: number; cranes: string[] }[];
  breaks: { shift: string; start: number; duration: number }[];
  restricted_zones: {
    reefer?: { bay: number; row: number; tier?: number }[];
    imo?: { bay: number; row?: number; rows?: number[] }[];
  };
}
```

---

## バージョン履歴

| バージョン | 日付 | 変更内容 |
|---|---|---|
| 1.0 | 2026-06-02 | 初版リリース |

---

## 関連資料

- [Solver Output DSL](./solver_output_dsl_v1.md)
- [Solver Input DSL](./solver_input_dsl_v1.md)
- [RCPSP Base DSL](./rcpsp_base_v1.md)
- [Frontend/src/domain/types.ts](../../Frontend/src/domain/types.ts)
- [OptiBuddy 4DSL Design Plan](../../docs/OptiBuddy_4DSL_Design_Plan.md)
