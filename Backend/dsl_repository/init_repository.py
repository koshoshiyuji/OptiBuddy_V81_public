"""
OptiBuddy DSL Repository 初期化スクリプト
RCPSP基底DSL、Yard Planning Extensions、FJSP Extensionを登録
"""

import json
from pathlib import Path

from repository import DslRepository


def init_rcpsp_base(repo: DslRepository) -> int:
    """RCPSP基底DSL v1.0を登録"""
    schema_json = {
        "tasks": {
            "type": "array",
            "description": "実行すべきタスクのリスト",
            "items": {
                "id": {"type": "string"},
                "duration": {"type": "number", "minimum": 0},
                "resource_requirements": {
                    "type": "object",
                    "additionalProperties": {"type": "number", "minimum": 0}
                }
            },
            "required": ["id", "duration"]
        },
        "resources": {
            "type": "array",
            "description": "利用可能なリソースのリスト",
            "items": {
                "id": {"type": "string"},
                "capacity": {"type": "number", "minimum": 0}
            },
            "required": ["id", "capacity"]
        },
        "precedences": {
            "type": "array",
            "description": "タスク間の先行関係",
            "items": {
                "from": {"type": "string"},
                "to": {"type": "string"},
                "delay": {"type": "number", "minimum": 0, "default": 0}
            },
            "required": ["from", "to"]
        },
        "objective": {
            "type": "enum",
            "values": ["minimize_makespan", "minimize_cost"],
            "default": "minimize_makespan"
        }
    }
    
    dsl_id = repo.create_dsl_definition(
        problem_class="RCPSP",
        version="1.0",
        extensions=[],
        schema_json=schema_json,
        description="Resource-Constrained Project Scheduling Problem (基底) - CSPLib prob061"
    )
    
    print(f"✓ RCPSP Base DSL v1.0 登録完了 (ID: {dsl_id})")
    return dsl_id


def init_physical_space_extension(repo: DslRepository) -> int:
    """physical_space extension登録"""
    schema_fragment = {
        "containers": {
            "items": {
                "yard": {
                    "type": "object",
                    "properties": {
                        "bay": {"type": "number", "minimum": 0},
                        "row": {"type": "number", "minimum": 0},
                        "tier": {"type": "number", "minimum": 1}
                    },
                    "required": ["bay", "row", "tier"]
                },
                "ship": {
                    "type": "object",
                    "properties": {
                        "bay": {"type": "number", "minimum": 0},
                        "row": {"type": "number", "minimum": 0},
                        "tier": {"type": "number", "minimum": 1}
                    },
                    "required": ["bay", "row", "tier"]
                }
            }
        },
        "config": {
            "max_bays": {"type": "number", "minimum": 1},
            "max_rows": {"type": "number", "minimum": 1},
            "max_tiers": {"type": "number", "minimum": 1},
            "max_vessel_bays": {"type": "number", "minimum": 1},
            "max_vessel_rows": {"type": "number", "minimum": 1},
            "max_vessel_tiers": {"type": "number", "minimum": 1},
            "safety_gap": {"type": "number", "minimum": 0, "default": 10}
        }
    }
    
    solver_mapping = {
        "constraints": [
            "yard_stack_order",
            "vessel_stack_order"
        ],
        "implementation": "full_yard_solver.py::build_physical_space_constraints - ヤード/船舶の積み順制約を構築"
    }
    
    ui_mapping = {
        "views": ["YardView", "VesselView"],
        "snapshot_structure": "BayGrid[bay][row][tier]"
    }
    
    ext_id = repo.create_extension(
        name="physical_space",
        category="constraint",
        applicable_domains=["YardPlanning"],
        schema_fragment=schema_fragment,
        solver_mapping=solver_mapping,
        ui_mapping=ui_mapping,
        description="物理的な3次元空間（Bay/Row/Tier）における配置制約"
    )
    
    print(f"✓ physical_space extension 登録完了 (ID: {ext_id})")
    return ext_id


def init_phase_separation_extension(repo: DslRepository) -> int:
    """phase_separation extension登録"""
    schema_fragment = {
        "containers": {
            "items": {
                "operation_type": {
                    "type": "enum",
                    "values": ["LOAD", "DISCHARGE"],
                    "description": "コンテナの操作タイプ（POL/PODから自動判定）"
                }
            }
        },
        "config": {
            "current_port": {
                "type": "string",
                "description": "現在の港コード（LOAD/DISCHARGE判定に使用）",
                "required": True
            }
        }
    }
    
    solver_mapping = {
        "constraints": ["discharge_before_load"],
        "implementation": "full_yard_solver.py::apply_phase_separation_constraints - DISCHARGE完了後にLOAD開始を強制"
    }
    
    ext_id = repo.create_extension(
        name="phase_separation",
        category="constraint",
        applicable_domains=["YardPlanning"],
        schema_fragment=schema_fragment,
        solver_mapping=solver_mapping,
        description="DISCHARGE完了後にLOAD開始を強制する制約"
    )
    
    print(f"✓ phase_separation extension 登録完了 (ID: {ext_id})")
    return ext_id


def init_crane_interference_extension(repo: DslRepository) -> int:
    """crane_interference extension登録"""
    schema_fragment = {
        "config": {
            "crane_interference": {
                "type": "boolean",
                "default": False,
                "description": "クレーン干渉制約を有効化"
            },
            "crane_safety_bays": {
                "type": "number",
                "minimum": 1,
                "default": 2,
                "description": "クレーン間の安全距離（ベイ数）"
            }
        }
    }
    
    solver_mapping = {
        "constraints": ["no_overlap_adjacent_cranes"],
        "implementation": "full_yard_solver.py::apply_crane_interference_constraints - 隣接クレーンの干渉防止"
    }
    
    ext_id = repo.create_extension(
        name="crane_interference",
        category="constraint",
        applicable_domains=["YardPlanning"],
        schema_fragment=schema_fragment,
        solver_mapping=solver_mapping,
        description="隣接クレーンの干渉防止制約"
    )
    
    print(f"✓ crane_interference extension 登録完了 (ID: {ext_id})")
    return ext_id


def init_attribute_zones_extension(repo: DslRepository) -> int:
    """attribute_zones extension登録"""
    schema_fragment = {
        "containers": {
            "items": {
                "attrs": {
                    "type": "array",
                    "items": {
                        "type": "enum",
                        "values": ["REEFER", "OOG", "IMO_CLASS_1", "IMO_CLASS_2", "..."]
                    },
                    "description": "コンテナ属性（冷凍、危険物、特殊サイズ等）"
                }
            }
        },
        "resources": {
            "restricted_zones": {
                "reefer": {
                    "type": "array",
                    "items": {
                        "bay": {"type": "number"},
                        "row": {"type": "number"},
                        "tier": {"type": "number", "optional": True}
                    }
                },
                "imo": {
                    "type": "array",
                    "items": {
                        "bay": {"type": "number"},
                        "row": {"type": "number", "optional": True},
                        "rows": {"type": "array", "items": {"type": "number"}, "optional": True}
                    }
                }
            }
        }
    }
    
    solver_mapping = {
        "constraints": [
            "reefer_in_reefer_slots",
            "imo_in_imo_zones",
            "oog_exclusion_zones"
        ],
        "implementation": "full_yard_solver.py::apply_attribute_zone_constraints + detect_attribute_issues - 属性ベース配置制約とIssue検出"
    }
    
    ext_id = repo.create_extension(
        name="attribute_zones",
        category="constraint",
        applicable_domains=["YardPlanning"],
        schema_fragment=schema_fragment,
        solver_mapping=solver_mapping,
        description="コンテナ属性（REEFER/IMO/OOG）に基づく配置制約"
    )
    
    print(f"✓ attribute_zones extension 登録完了 (ID: {ext_id})")
    return ext_id


def init_shift_extension(repo: DslRepository) -> int:
    """shift extension登録"""
    schema_fragment = {
        "config": {
            "shifts": {
                "type": "array",
                "items": {
                    "id": {"type": "string"},
                    "start": {"type": "number", "description": "開始時刻（分単位）"},
                    "end": {"type": "number", "description": "終了時刻（分単位）"},
                    "cranes": {"type": "array", "items": {"type": "string"}}
                }
            },
            "breaks": {
                "type": "array",
                "items": {
                    "shift": {"type": "string"},
                    "start": {"type": "number", "description": "休憩開始時刻（分単位）"},
                    "duration": {"type": "number", "description": "休憩時間（分）"}
                }
            }
        }
    }
    
    solver_mapping = {
        "constraints": [
            "task_within_shift_window",
            "task_avoid_break_time"
        ],
        "implementation": "full_yard_solver.py::parse_shifts + apply_shift_constraints - シフト時間窓と休憩時間の制約"
    }
    
    ext_id = repo.create_extension(
        name="shift",
        category="resource",
        applicable_domains=["YardPlanning"],
        schema_fragment=schema_fragment,
        solver_mapping=solver_mapping,
        description="クレーンのシフト・休憩時間制約"
    )
    
    print(f"✓ shift extension 登録完了 (ID: {ext_id})")
    return ext_id


def init_flexible_machine_assignment_extension(repo: DslRepository) -> int:
    """flexible_machine_assignment extension登録（FJSP用）"""
    schema_fragment = {
        "tasks": {
            "items": {
                "alternatives": {
                    "type": "array",
                    "description": "タスク実行可能な機械の選択肢",
                    "items": {
                        "machine_id": {"type": "string"},
                        "duration": {"type": "number", "minimum": 0}
                    }
                }
            }
        }
    }
    
    solver_mapping = {
        "constraints": ["select_one_alternative_per_task"],
        "implementation": "CP Optimizer: interval_var with optional + alternative"
    }
    
    ext_id = repo.create_extension(
        name="flexible_machine_assignment",
        category="constraint",
        schema_fragment=schema_fragment,
        solver_mapping=solver_mapping,
        description="Flexible Job Shop Scheduling: タスクごとに複数の機械選択肢"
    )
    
    print(f"✓ flexible_machine_assignment extension 登録完了 (ID: {ext_id})")
    return ext_id


def init_yard_planning_dsl(repo: DslRepository) -> int:
    """Yard Planning DSL v1.0を登録（YardPlanning + 5 extensions）"""
    schema_json = {
        "containers": {
            "type": "array",
            "items": {
                "id": {"type": "string"},
                "yard": {"type": "object"},
                "ship": {"type": "object"},
                "weight": {"type": "number"},
                "pod": {"type": "string"},
                "pol": {"type": "string"},
                "order": {"type": "number"},
                "attrs": {"type": "array"}
            }
        },
        "resources": {
            "yard_cranes": {"type": "array"},
            "ship_cranes": {"type": "array"},
            "restricted_zones": {"type": "object"}
        },
        "config": {
            "current_port": {"type": "string"},
            "time_pick": {"type": "number"},
            "time_load": {"type": "number"},
            "time_discharge": {"type": "number"},
            "time_place": {"type": "number"},
            "time_move": {"type": "number"},
            "time_rehandle": {"type": "number"},
            "safety_gap": {"type": "number"},
            "max_bays": {"type": "number"},
            "max_rows": {"type": "number"},
            "max_tiers": {"type": "number"},
            "crane_interference": {"type": "boolean"},
            "shifts": {"type": "array"},
            "breaks": {"type": "array"}
        }
    }
    
    dsl_id = repo.create_dsl_definition(
        problem_class="YardPlanning",
        version="1.0",
        extensions=[
            "physical_space",
            "phase_separation",
            "crane_interference",
            "attribute_zones",
            "shift"
        ],
        schema_json=schema_json,
        description="Yard Planning DSL v1.0 - コンテナターミナルのヤード計画問題（V5実装ベース）"
    )
    
    print(f"✓ Yard Planning DSL v1.0 登録完了 (ID: {dsl_id})")
    return dsl_id


def init_fjsp_dsl(repo: DslRepository) -> int:
    """FJSP DSL v1.0を登録（FJSP + flexible_machine_assignment）"""
    schema_json = {
        "jobs": {
            "type": "array",
            "items": {
                "id": {"type": "string"},
                "tasks": {
                    "type": "array",
                    "items": {
                        "id": {"type": "string"},
                        "alternatives": {"type": "array"}
                    }
                }
            }
        },
        "machines": {
            "type": "array",
            "items": {
                "id": {"type": "string"},
                "capacity": {"type": "number"}
            }
        },
        "objective": {
            "type": "enum",
            "values": ["minimize_makespan", "minimize_cost"]
        }
    }
    
    dsl_id = repo.create_dsl_definition(
        problem_class="FJSP",
        version="1.0",
        extensions=["flexible_machine_assignment"],
        schema_json=schema_json,
        description="Flexible Job Shop Scheduling Problem (FJSP) - V2資産ベース"
    )
    
    print(f"✓ FJSP DSL v1.0 登録完了 (ID: {dsl_id})")
    return dsl_id


def main():
    """リポジトリ初期化メイン処理"""
    print("=" * 60)
    print("OptiBuddy DSL Repository 初期化")
    print("=" * 60)
    
    # リポジトリ初期化
    repo = DslRepository()
    
    # 既存データ確認
    overview = repo.get_overview()
    if overview["dsl_definitions_count"] > 0:
        print(f"\n⚠ 既存データが存在します:")
        print(f"  - DSL定義: {overview['dsl_definitions_count']}件")
        print(f"  - Extension: {overview['extensions_count']}件")
        print(f"  - Problem Classes: {overview['problem_classes']}")
        
        response = input("\n既存データを削除して再初期化しますか？ (yes/no): ")
        if response.lower() != "yes":
            print("初期化をキャンセルしました。")
            return
        
        # データベース再作成
        import os
        os.remove(repo.db_path)
        repo = DslRepository()
        print("✓ データベースを再作成しました\n")
    
    # RCPSP基底DSL登録
    print("\n[1/8] RCPSP Base DSL v1.0")
    rcpsp_id = init_rcpsp_base(repo)
    
    # Extensions登録
    print("\n[2/8] Extensions登録")
    init_physical_space_extension(repo)
    init_phase_separation_extension(repo)
    init_crane_interference_extension(repo)
    init_attribute_zones_extension(repo)
    init_shift_extension(repo)
    init_flexible_machine_assignment_extension(repo)
    
    # Yard Planning DSL登録
    print("\n[3/8] Yard Planning DSL v1.0")
    yard_id = init_yard_planning_dsl(repo)
    
    # FJSP DSL登録
    print("\n[4/8] FJSP DSL v1.0")
    fjsp_id = init_fjsp_dsl(repo)
    
    # 進化ログ記録（初期化イベント）
    print("\n[5/8] 進化ログ記録")
    repo.log_dsl_evolution(
        dsl_id=rcpsp_id,
        change_type="initialization",
        change_description="RCPSP Base DSL v1.0 初期登録",
        llm_context="OptiBuddy V5からの抽出・整理"
    )
    repo.log_dsl_evolution(
        dsl_id=yard_id,
        change_type="initialization",
        change_description="Yard Planning DSL v1.0 初期登録（V5実装ベース）",
        llm_context="full_yard_solver.py + types.ts から抽出"
    )
    repo.log_dsl_evolution(
        dsl_id=fjsp_id,
        change_type="initialization",
        change_description="FJSP DSL v1.0 初期登録（V2資産ベース）",
        llm_context="llm_interface.py generate_fjsp_dsl から抽出"
    )
    print("✓ 進化ログ記録完了")
    
    # 最終確認
    print("\n[6/8] リポジトリ概要")
    overview = repo.get_overview()
    print(f"  - DSL定義: {overview['dsl_definitions_count']}件")
    print(f"  - Extension: {overview['extensions_count']}件")
    print(f"  - 進化ログ: {overview['evolution_logs_count']}件")
    print(f"  - Problem Classes: {', '.join(overview['problem_classes'])}")
    
    # DSL一覧表示
    print("\n[7/8] 登録済みDSL一覧")
    for dsl in repo.list_dsl_definitions():
        ext_count = len(dsl["extensions"])
        ext_str = f" + {ext_count} extensions" if ext_count > 0 else ""
        print(f"  - {dsl['problem_class']} v{dsl['version']}{ext_str}")
        print(f"    {dsl['description']}")
    
    # Extension一覧表示
    print("\n[8/8] 登録済みExtension一覧")
    for ext in repo.list_extensions():
        print(f"  - {ext['name']} ({ext['category']})")
        print(f"    {ext['description']}")
    
    print("\n" + "=" * 60)
    print("✓ 初期化完了！")
    print(f"データベース: {repo.db_path}")
    print("=" * 60)


if __name__ == "__main__":
    main()
