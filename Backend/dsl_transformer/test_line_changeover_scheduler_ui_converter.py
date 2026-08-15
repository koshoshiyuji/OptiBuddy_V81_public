"""
test_line_changeover_scheduler_ui_converter.py

2026-07-18d追記: 実機ログで確認された TypeError 回帰テスト。
solverがinfeasible/エラー時に "objective_value": None / "solve_time": None /
"makespan": None を明示的にキーごと返すケース（.get(key, default) はキー欠落時
にしかdefaultを使わないため、Noneがそのまま round() に渡って落ちていた）を再現する。
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from dsl_transformer.line_changeover_scheduler_ui_converter import convert_line_changeover_scheduler_to_ui


def _infeasible_solver_output():
    return {
        "solutions": [{
            "feasible": False,
            "schedule": [],
            "makespan": None,
            "kpi": {"makespan": None, "task_count": 0, "objective_value": None, "solve_time": 0.0},
        }],
        "issues": [{
            "id": "solve_failed", "severity": "CRITICAL", "category": "INFEASIBLE",
            "title": "最適解が見つかりませんでした", "message": "...", "relatedContainerIds": [],
        }],
        "metadata": {},
    }


def test_infeasible_result_with_explicit_none_kpi_does_not_crash():
    ui = convert_line_changeover_scheduler_to_ui(_infeasible_solver_output())
    obj_card = next(c for c in ui["kpi_cards"] if c["label"] == "目的関数値")
    solve_time_card = next(c for c in ui["kpi_cards"] if c["label"] == "ソルブ時間")
    makespan_card = next(c for c in ui["kpi_cards"] if c["label"] == "メイクスパン")
    assert obj_card["value"] == 0
    assert solve_time_card["value"] == 0.0
    assert makespan_card["value"] == 0
    assert ui["feasible"] is False


def test_feasible_result_with_real_values_still_reported_correctly():
    solver_output = {
        "solutions": [{
            "feasible": True,
            "schedule": [{"task_id": "t1", "start": 0, "end": 30, "duration": 30, "resource_requirements": []}],
            "makespan": 30,
            "kpi": {"task_count": 1, "objective_value": 30.0, "solve_time": 1.23},
        }],
        "issues": [],
        "metadata": {},
    }
    ui = convert_line_changeover_scheduler_to_ui(solver_output)
    obj_card = next(c for c in ui["kpi_cards"] if c["label"] == "目的関数値")
    solve_time_card = next(c for c in ui["kpi_cards"] if c["label"] == "ソルブ時間")
    assert obj_card["value"] == 30.0
    assert solve_time_card["value"] == 1.2  # round(1.23, 1)


def test_zero_objective_value_is_not_masked_by_makespan_fallback():
    """objective_valueが正当に0の場合、makespanへフォールバックしてはならない。"""
    solver_output = {
        "solutions": [{
            "feasible": True,
            "schedule": [],
            "makespan": 50,
            "kpi": {"task_count": 0, "objective_value": 0.0, "solve_time": 0.0},
        }],
        "issues": [],
        "metadata": {},
    }
    ui = convert_line_changeover_scheduler_to_ui(solver_output)
    obj_card = next(c for c in ui["kpi_cards"] if c["label"] == "目的関数値")
    assert obj_card["value"] == 0.0


if __name__ == "__main__":
    test_infeasible_result_with_explicit_none_kpi_does_not_crash()
    test_feasible_result_with_real_values_still_reported_correctly()
    test_zero_objective_value_is_not_masked_by_makespan_fallback()
    print("全テスト成功")
