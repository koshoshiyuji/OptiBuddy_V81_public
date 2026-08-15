"""
DSL Transformer テストスクリプト

業務DSL → Solver Input DSL → Solver Output DSL → UI DSL
の変換フローをテストします。
"""

import json

from dsl_transformer import convert_business_to_solver, convert_solver_to_ui


def test_business_to_solver():
    """業務DSL → Solver Input DSL 変換テスト"""
    print("=" * 60)
    print("Test 1: Business DSL → Solver Input DSL")
    print("=" * 60)
    
    # サンプル業務DSL
    business_dsl = {
        "problem_class": "RCPSP",
        "version": "1.0",
        "extensions": ["physical_space", "phase_separation"],
        "containers": [
            {
                "id": "C01",
                "yard": {"bay": 1, "row": 1, "tier": 1},
                "ship": {"bay": 10, "row": 1, "tier": 1},
                "order": 1,
                "operation_type": "LOAD",
                "weight": 20000,
                "pol": "JPYOK",
                "pod": "USOAK",
                "attrs": []
            },
            {
                "id": "C02",
                "yard": {"bay": 1, "row": 1, "tier": 2},
                "ship": {"bay": 10, "row": 1, "tier": 2},
                "order": 2,
                "operation_type": "LOAD",
                "weight": 22000,
                "pol": "JPYOK",
                "pod": "USOAK",
                "attrs": []
            },
            {
                "id": "C03",
                "ship": {"bay": 12, "row": 1, "tier": 1},
                "yard": {"bay": 2, "row": 1, "tier": 1},
                "order": 1,
                "operation_type": "DISCHARGE",
                "weight": 18000,
                "pol": "CNSHA",
                "pod": "JPYOK",
                "attrs": []
            }
        ],
        "resources": {
            "yard_cranes": [{"id": "RC-1", "capacity": 1, "bay": [1, 2, 3]}],
            "ship_cranes": [{"id": "GC-1", "capacity": 1, "bay": [10, 11, 12]}]
        },
        "config": {
            "current_port": "JPYOK",
            "time_pick": 180,
            "time_load": 300,
            "time_discharge": 300,
            "time_place": 180,
            "safety_gap": 10
        }
    }
    
    # 変換実行
    solver_input = convert_business_to_solver(business_dsl)
    
    # 結果表示
    print(f"\n✅ 変換成功!")
    print(f"  - タスク数: {len(solver_input['tasks'])}")
    print(f"  - 制約数: {len(solver_input['constraints'])}")
    print(f"  - Extensions: {solver_input['metadata']['extensions']}")
    
    print("\n📋 生成されたタスク:")
    for task in solver_input['tasks']:
        print(f"  - {task['id']}: {task['operation']} (duration={task['duration']}s, resource={task['resourceId']})")
    
    print("\n🔗 生成された制約:")
    for constraint in solver_input['constraints'][:5]:  # 最初の5つのみ表示
        print(f"  - {constraint['type']}: {list(constraint['params'].keys())}")
    
    print(f"\n🎯 目的関数:")
    print(f"  - Primary: {solver_input['objective']['primary']}")
    print(f"  - Weights: {solver_input['objective']['weights']}")
    print(f"  - Penalty pairs: {len(solver_input['objective']['penalty_pairs'])}")
    
    return solver_input


def test_solver_to_ui():
    """Solver Output DSL → UI DSL 変換テスト"""
    print("\n" + "=" * 60)
    print("Test 2: Solver Output DSL → UI DSL")
    print("=" * 60)
    
    # サンプルSolver Output DSL（簡易版）
    solver_output = {
        "metadata": {
            "solver_name": "CP Optimizer",
            "solver_version": "22.1.1",
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
                        "id": "D-C03",
                        "containerId": "C03",
                        "operation": "DISCHARGE",
                        "sequence": 1,
                        "resourceId": "GC-1",
                        "start": 0,
                        "end": 300,
                        "duration": 300,
                        "order": 1
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
                        "order": 1
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
                        "order": 2
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
                        "order": 1
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
                        "order": 1
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
                        "order": 2
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
        "issues": [],
        "containers": [
            {
                "id": "C01",
                "yard": {"bay": 1, "row": 1, "tier": 1},
                "ship": {"bay": 10, "row": 1, "tier": 1},
                "order": 1,
                "operation_type": "LOAD",
                "weight": 20000,
                "pol": "JPYOK",
                "pod": "USOAK",
                "attrs": []
            },
            {
                "id": "C02",
                "yard": {"bay": 1, "row": 1, "tier": 2},
                "ship": {"bay": 10, "row": 1, "tier": 2},
                "order": 2,
                "operation_type": "LOAD",
                "weight": 22000,
                "pol": "JPYOK",
                "pod": "USOAK",
                "attrs": []
            },
            {
                "id": "C03",
                "ship": {"bay": 12, "row": 1, "tier": 1},
                "yard": {"bay": 2, "row": 1, "tier": 1},
                "order": 1,
                "operation_type": "DISCHARGE",
                "weight": 18000,
                "pol": "CNSHA",
                "pod": "JPYOK",
                "attrs": []
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
            "current_port": "JPYOK"
        },
        "resources": {
            "yard_cranes": [{"id": "RC-1", "capacity": 1, "bay": [1, 2, 3]}],
            "ship_cranes": [{"id": "GC-1", "capacity": 1, "bay": [10, 11, 12]}]
        }
    }
    
    # 変換実行
    ui_dsl = convert_solver_to_ui(solver_output)
    
    # 結果表示
    print(f"\n✅ 変換成功!")
    print(f"  - Solutions: {len(ui_dsl['solutions'])}")
    print(f"  - Status: {ui_dsl['status']}")
    
    solution = ui_dsl['solutions'][0]
    print(f"\n📊 Solution: {solution['name']} - {solution['label']}")
    print(f"  - UI Tasks: {len(solution['tasks'])}")
    print(f"  - Time Points: {len(solution['snapshots']['timePoints'])}")
    print(f"  - Makespan: {solution['kpi']['makespanMin']} 分")
    
    print("\n🎬 UI Tasks (from/to Location付き):")
    for task in solution['tasks'][:3]:  # 最初の3つのみ表示
        print(f"  - {task['id']}: {task['operation']}")
        print(f"    from: {task['from']['domain']} (bay={task['from']['bay']})")
        print(f"    to:   {task['to']['domain']} (bay={task['to']['bay']})")
    
    print(f"\n📸 Snapshots:")
    print(f"  - Time Points: {solution['snapshots']['timePoints']}")
    print(f"  - Snapshot Count: {len(solution['snapshots']['data'])}")
    
    # 時刻0のスナップショット詳細
    snapshot_0 = solution['snapshots']['data']['0']
    print(f"\n  📷 Snapshot at t=0:")
    print(f"    - Yard containers: {len(snapshot_0['yard'])}")
    print(f"    - Ship containers: {len(snapshot_0['ship'])}")
    print(f"    - Available bays: {snapshot_0['availableBays']}")
    
    print(f"\n📈 KPI:")
    kpi = solution['kpi']
    print(f"  - Makespan: {kpi['makespanMin']} 分")
    print(f"  - Rehandle: {kpi['rehandleCount']} 回")
    print(f"  - Avg Wait: {kpi['avgWaitMin']} 分")
    print(f"  - Crane Utilization:")
    for crane in kpi['craneUtil']:
        print(f"    - {crane['id']}: {crane['util']}%")
    
    print(f"\n⚙️ UI Config:")
    config = ui_dsl['config']
    print(f"  - Yard Bays: {config['yardBays']}")
    print(f"  - Ship Bays: {config['shipBays']}")
    print(f"  - Operation Start: {config['operation_start']}")
    print(f"  - Current Port: {config['current_port']}")
    
    return ui_dsl


def save_results(solver_input, ui_dsl):
    """結果をJSONファイルに保存"""
    print("\n" + "=" * 60)
    print("Saving Results")
    print("=" * 60)
    
    with open("test_solver_input.json", "w", encoding="utf-8") as f:
        json.dump(solver_input, f, indent=2, ensure_ascii=False)
    print("✅ Solver Input DSL saved to: test_solver_input.json")
    
    with open("test_ui_dsl.json", "w", encoding="utf-8") as f:
        json.dump(ui_dsl, f, indent=2, ensure_ascii=False)
    print("✅ UI DSL saved to: test_ui_dsl.json")


if __name__ == "__main__":
    print("\n🚀 DSL Transformer Test Suite")
    print("=" * 60)
    
    # Test 1: Business → Solver Input
    solver_input = test_business_to_solver()
    
    # Test 2: Solver Output → UI
    ui_dsl = test_solver_to_ui()
    
    # Save results
    save_results(solver_input, ui_dsl)
    
    print("\n" + "=" * 60)
    print("✅ All tests completed successfully!")
    print("=" * 60)
