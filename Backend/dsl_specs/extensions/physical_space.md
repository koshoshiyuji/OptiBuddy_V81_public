# Extension: physical_space

## 概要

**Extension名**: `physical_space`  
**カテゴリ**: `constraint`  
**適用ドメイン**: Yard Planning, Warehouse Management, 3D Bin Packing

物理的な3次元空間（Bay/Row/Tier）における配置制約を追加します。
コンテナヤード、倉庫、船舶などの物理的な格納構造を持つ問題に適用されます。

---

## スキーマ追加

### containers フィールド拡張

```json
{
  "containers": {
    "type": "array",
    "items": {
      "yard": {
        "type": "object",
        "description": "ヤード（初期配置）の3次元座標",
        "properties": {
          "bay": {"type": "number", "minimum": 0},
          "row": {"type": "number", "minimum": 0},
          "tier": {"type": "number", "minimum": 1}
        },
        "required": ["bay", "row", "tier"]
      },
      "ship": {
        "type": "object",
        "description": "船舶（最終配置）の3次元座標",
        "properties": {
          "bay": {"type": "number", "minimum": 0},
          "row": {"type": "number", "minimum": 0},
          "tier": {"type": "number", "minimum": 1}
        },
        "required": ["bay", "row", "tier"]
      }
    }
  }
}
```

### config フィールド拡張

```json
{
  "config": {
    "max_bays": {
      "type": "number",
      "description": "ヤードの最大ベイ数",
      "minimum": 1
    },
    "max_rows": {
      "type": "number",
      "description": "ヤードの最大列数",
      "minimum": 1
    },
    "max_tiers": {
      "type": "number",
      "description": "ヤードの最大段数",
      "minimum": 1
    },
    "max_vessel_bays": {
      "type": "number",
      "description": "船舶の最大ベイ数",
      "minimum": 1
    },
    "max_vessel_rows": {
      "type": "number",
      "description": "船舶の最大列数",
      "minimum": 1
    },
    "max_vessel_tiers": {
      "type": "number",
      "description": "船舶の最大段数",
      "minimum": 1
    },
    "safety_gap": {
      "type": "number",
      "description": "スタック操作間の安全時間（秒）",
      "minimum": 0,
      "default": 10
    }
  }
}
```

---

## 追加制約

### 1. Yard Stack Order Constraint（ヤード積み順制約）

**制約内容**:
- 同じ(bay, row)スタック内では、tier降順（上から）でPICK操作を実行
- 上段のコンテナを先に取り出さないと下段にアクセスできない

**数式表現**:
```
∀c1, c2 ∈ containers:
  if c1.yard.bay == c2.yard.bay AND 
     c1.yard.row == c2.yard.row AND
     c1.yard.tier > c2.yard.tier
  then end(PICK(c1)) + safety_gap ≤ start(PICK(c2))
```

**CP Optimizer実装**: `full_yard_solver.py::build_physical_space_constraints`
```python
yard_stacks = {}
for c in containers:
    pos = (c["yard"]["bay"], c["yard"]["row"])
    yard_stacks.setdefault(pos, []).append(c)

for pos, stack in yard_stacks.items():
    sorted_stack = sorted(stack, key=lambda x: x["yard"]["tier"], reverse=True)
    for i in range(len(sorted_stack) - 1):
        upper_pick = pick_tasks[sorted_stack[i]["id"]]
        lower_pick = pick_tasks[sorted_stack[i+1]["id"]]
        mdl.add(mdl.end_before_start(upper_pick, lower_pick, delay=safety_gap))
```

### 2. Vessel Stack Order Constraint（船舶積み順制約）

**制約内容**:
- 同じ(bay, row)スタック内では、tier昇順（下から）でLOAD操作を実行
- 下段から順に積み上げる

**数式表現**:
```
∀c1, c2 ∈ containers:
  if c1.ship.bay == c2.ship.bay AND 
     c1.ship.row == c2.ship.row AND
     c1.ship.tier < c2.ship.tier
  then end(LOAD(c1)) + 1 ≤ start(LOAD(c2))
```

**CP Optimizer実装**: `full_yard_solver.py::build_physical_space_constraints`
```python
vessel_stacks = {}
for c in containers:
    pos = (c["ship"]["bay"], c["ship"]["row"])
    vessel_stacks.setdefault(pos, []).append(c)

for pos, stack in vessel_stacks.items():
    sorted_stack = sorted(stack, key=lambda x: x["ship"]["tier"])
    for i in range(len(sorted_stack) - 1):
        lower_load = load_tasks[sorted_stack[i]["id"]]
        upper_load = load_tasks[sorted_stack[i+1]["id"]]
        mdl.add(mdl.end_before_start(lower_load, upper_load, delay=1))
```

---

## Issue検出

### IS_YARD Issue（ヤードリハンドリング）

**検出条件**:
- 同じ(bay, row)スタック内で、上段コンテナのorder > 下段コンテナのorder

**Issue定義**:
```json
{
  "id": "is_yard_{lower_id}_{upper_id}",
  "severity": "WARNING",
  "category": "YARD",
  "title": "リハンドリングが発生します",
  "message": "上段({upper_id})が邪魔で先に積むべき下段({lower_id})を取り出せません。",
  "containerId": "{lower_id}",
  "relatedContainerIds": ["{lower_id}", "{upper_id}"]
}
```

### IS_SHIP Issue（船舶積み順矛盾）

**検出条件**:
- 同じ(bay, row)スタック内で、上段コンテナのorder < 下段コンテナのorder

**Issue定義**:
```json
{
  "id": "is_ship_{lower_id}_{upper_id}",
  "severity": "CRITICAL",
  "category": "MBP",
  "title": "Master Bay Plan 積み順矛盾",
  "message": "Master Bay Planに積み順矛盾があります。船社への確認が必要です。",
  "containerId": "{lower_id}",
  "relatedContainerIds": ["{lower_id}", "{upper_id}"]
}
```

---

## UI DSLマッピング

### YardView / VesselView表示

```typescript
interface BayGrid {
  [row: number]: { [tier: number]: string };
}

interface Snapshot {
  yard: { [bay: number]: BayGrid };
  ship: { [bay: number]: BayGrid };
}

// physical_space extension → Grid表示
function buildSnapshot(containers: Container[], currentTime: number): Snapshot {
  const snapshot: Snapshot = { yard: {}, ship: {} };
  
  for (const c of containers) {
    // Yard配置
    const yb = c.yard.bay, yr = c.yard.row, yt = c.yard.tier;
    if (!snapshot.yard[yb]) snapshot.yard[yb] = {};
    if (!snapshot.yard[yb][yr]) snapshot.yard[yb][yr] = {};
    snapshot.yard[yb][yr][yt] = c.id;
    
    // Ship配置
    const sb = c.ship.bay, sr = c.ship.row, st = c.ship.tier;
    if (!snapshot.ship[sb]) snapshot.ship[sb] = {};
    if (!snapshot.ship[sb][sr]) snapshot.ship[sb][sr] = {};
    snapshot.ship[sb][sr][st] = c.id;
  }
  
  return snapshot;
}
```

---

## 使用例

### Yard Planning問題（physical_space適用）

```json
{
  "problem_class": "RCPSP",
  "version": "1.0",
  "extensions": ["physical_space"],
  "containers": [
    {
      "id": "C01",
      "yard": {"bay": 1, "row": 1, "tier": 1},
      "ship": {"bay": 10, "row": 1, "tier": 1},
      "order": 1
    },
    {
      "id": "C02",
      "yard": {"bay": 1, "row": 1, "tier": 2},
      "ship": {"bay": 10, "row": 1, "tier": 2},
      "order": 2
    }
  ],
  "config": {
    "max_bays": 5,
    "max_rows": 3,
    "max_tiers": 4,
    "max_vessel_bays": 20,
    "max_vessel_rows": 8,
    "max_vessel_tiers": 6,
    "safety_gap": 10
  }
}
```

**制約の効果**:
- C02（tier=2）のPICKがC01（tier=1）のPICKより先に実行される
- C01のLOADがC02のLOADより先に実行される（下から積む）

---

## 他Extensionとの組み合わせ

### physical_space + shift

```json
{
  "extensions": ["physical_space", "shift"],
  "config": {
    "max_bays": 10,
    "safety_gap": 10,
    "shifts": [
      {"id": "day", "start": 480, "end": 960, "cranes": ["RC-1"]}
    ]
  }
}
```

物理制約とシフト制約が同時に適用される。

### physical_space + attribute_zones

```json
{
  "extensions": ["physical_space", "attribute_zones"],
  "containers": [
    {
      "id": "REEFER01",
      "yard": {"bay": 1, "row": 1, "tier": 1},
      "attrs": ["REEFER"]
    }
  ],
  "resources": {
    "restricted_zones": {
      "reefer": [{"bay": 1, "row": 1}]
    }
  }
}
```

物理配置と属性ゾーン制約が組み合わさる。

---

## バージョン履歴

| バージョン | 日付 | 変更内容 |
|---|---|---|
| 1.0 | 2026-06-02 | 初版リリース |

---

## 関連資料

- [RCPSP Base DSL](../rcpsp_base_v1.md)
- [Yard Planning DSL](../yard_planning_v1.md)
- [full_yard_solver.py](../../solvers/full_yard_solver.py) - 実装参照（関数: `build_physical_space_constraints`）
