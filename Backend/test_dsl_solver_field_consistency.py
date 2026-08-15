"""
test_dsl_solver_field_consistency.py

2026-07-26追加: check_dsl_solver_field_consistency()（DSL⇔solver直接フィールド
突き合わせ、ネストキー対応）の回帰テスト。

背景: check_converter_solver_field_consistency()（converter⇔solver）と
check_dsl_converter_field_consistency()（DSL⇔converter）は、いずれもconverter.py
自身のAST上に現れるキー名しか見ていない。そのため work_limits のように
「converterがキー名を一切書き換えず、ネスト構造ごとパススルーする」フィールドでは、
solverが誤ったネストキー名で読んでいても両チェックとも検出できなかった
（2026-07-12 NurseShiftWeeklyCap登録時の「ハード制約が2重に無効化されていた」
不具合1が、まさにこの抜け穴を素通りした）。

本テストは以下を確認する:
  1. 2026-07-12不具合1と同型の合成ケース（DSL側は正しいキー名、solver側は
     誤ったキー名でネストを読む）を、converter.pyを経由せずに検出できること。
  2. converter.pyがDSLに存在しない新規フィールドを計算・合成する正常系
     （TruckDispatcherのdepot_open_min/locations/dist_matrix等）を、
     誤検知として報告しないこと（converter_codeを渡すことで抑制される）。
  3. 実際に登録済みのTruckDispatcher/NurseShiftWeeklyCapの現行ファイルに対して
     実行しても、誤検知が出ないこと（実運用での回帰保証）。
"""

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

from domain_generator import check_dsl_solver_field_consistency


def test_nested_key_rename_bug_is_detected_without_converter():
    """2026-07-12不具合1と同型: パススルー構造でのネストキー名drift。"""
    dsl_scenarios = [
        {"staff": [{"id": "N04", "work_limits": {"max_night_shifts_per_rolling_7days": 2}}]}
    ]
    solver_code = """
def build(staff_list):
    for s in staff_list:
        work_limits = s.get("work_limits", {})
        weekly_night_cap = work_limits.get("weekly_night_shift_cap", 7)
        if weekly_night_cap < 7:
            add_constraint()
"""
    result = check_dsl_solver_field_consistency(dsl_scenarios, solver_code)
    assert "weekly_night_shift_cap" in result["missing_in_dsl_for_solver"], (
        "既知バグパターン（ネストキー名drift）が検出されなかった: "
        f"{result['missing_in_dsl_for_solver']}"
    )


def test_converter_synthesized_field_is_not_a_false_positive():
    """
    converterがDSLに存在しないフィールドを正当に計算・合成するケース
    （例: depot.open_time から depot_open_min を計算）は誤検知してはならない。
    """
    dsl_scenarios = [{"depot": {"open_time": "06:00", "close_time": "20:00"}}]
    converter_code = """
def convert(dsl):
    depot_open_min = _hhmm_to_min(dsl["depot"]["open_time"])
    return {"depot_open_min": depot_open_min}
"""
    solver_code = """
def build(solver_input, config):
    depot_open = int(config.get("depot_open_min", 360))
"""
    result = check_dsl_solver_field_consistency(dsl_scenarios, solver_code, converter_code)
    assert result["missing_in_dsl_for_solver"] == [], (
        f"converterが正当に合成したフィールドを誤検知した: {result['missing_in_dsl_for_solver']}"
    )


def test_truck_dispatcher_real_files_have_no_false_positive():
    backend = Path(__file__).parent
    scenarios = [
        json.loads((backend / "dsl_repository" / "scenarios" / "truck_dispatcher_baseline.json").read_text(encoding="utf-8")),
        json.loads((backend / "dsl_repository" / "scenarios" / "truck_dispatcher_infeasible.json").read_text(encoding="utf-8")),
    ]
    solver_code = (backend / "solvers" / "truck_dispatcher_solver.py").read_text(encoding="utf-8")
    converter_code = (backend / "dsl_transformer" / "truck_dispatcher_converter.py").read_text(encoding="utf-8")

    result = check_dsl_solver_field_consistency(scenarios, solver_code, converter_code)
    assert result["missing_in_dsl_for_solver"] == [], (
        f"実運用ファイルで誤検知が発生: {result['missing_in_dsl_for_solver']}"
    )


def test_nurse_shift_weekly_cap_real_files_have_no_false_positive():
    backend = Path(__file__).parent
    scenarios = [
        json.loads((backend / "dsl_repository" / "scenarios" / "nurse_shift_weekly_cap_baseline.json").read_text(encoding="utf-8")),
        json.loads((backend / "dsl_repository" / "scenarios" / "nurse_shift_weekly_cap_infeasible.json").read_text(encoding="utf-8")),
    ]
    solver_code = (backend / "solvers" / "nurse_shift_weekly_cap_solver.py").read_text(encoding="utf-8")
    converter_code = (backend / "dsl_transformer" / "nurse_shift_weekly_cap_converter.py").read_text(encoding="utf-8")

    result = check_dsl_solver_field_consistency(scenarios, solver_code, converter_code)
    assert result["missing_in_dsl_for_solver"] == [], (
        f"実運用ファイルで誤検知が発生: {result['missing_in_dsl_for_solver']}"
    )
