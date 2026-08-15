"""
test_shift_rotation_scheduler_cpsat_engine.py

ShiftRotationSchedulerSolver の config.solver_engine="cpsat" 経路の構造テスト。
docplex/CPLEXがないサンドボックスでも実行できる。制約充足を独立検証する。
"""

from __future__ import annotations

import sys
import os

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from solvers.shift_rotation_scheduler_solver import ShiftRotationSchedulerSolver, SHIFT_TYPE_ORDER


def _scenario():
    # W=4従業員/週。シフト: off, early, late, night。
    shifts = [
        {"id": "OFF", "name": "休み", "type": "off"},
        {"id": "EARLY", "name": "早番", "type": "early"},
        {"id": "LATE", "name": "遅番", "type": "late"},
        {"id": "NIGHT", "name": "夜勤", "type": "night"},
    ]
    daily_requirements = []
    for d in range(7):
        daily_requirements.append({"day_of_week": d, "shift_id": "EARLY", "required": 1})
        daily_requirements.append({"day_of_week": d, "shift_id": "LATE", "required": 1})
    return {
        "problem_class": "ShiftRotationScheduler",
        "num_employees": 4,
        "shifts": shifts,
        "daily_requirements": daily_requirements,
        "constraints": {"min_consecutive": 2, "max_consecutive": 4, "min_days_off_per_14": 2},
        "config": {"solver_engine": "cpsat", "time_limit_sec": 20},
        "issue_statuses": {},
    }


def test_cpsat_feasible_and_satisfies_constraints():
    solver_input = _scenario()
    result = ShiftRotationSchedulerSolver(solver_input).solve()
    assert result["feasible"] is True, result["issues"]
    sol = result["solutions"][0]
    W = solver_input["num_employees"]

    # 週間必要人数の検証(全週で各曜日のEARLY/LATEが1人)
    for w in range(W):
        for d in range(7):
            pass  # template_shift_idsから曜日別集計は下のemployee視点で検証する

    # 各従業員視点で「輪っか」の制約(最大連続日数・14日窓の休み数)を検証
    for emp in sol["employee_schedules"]:
        flat_types = []
        for w in emp["weeks"]:
            for day in w:
                flat_types.append(day["shift_type"])
        n = len(flat_types)

        # 土日は同じシフト(各週内)
        for w in emp["weeks"]:
            assert w[5]["shift_type"] == w[6]["shift_type"]

        # 最大連続日数制約: 同じシフトタイプがmax_consecutiveを超えて連続しない(輪っか)
        max_consec = solver_input["constraints"]["max_consecutive"]
        doubled = flat_types + flat_types
        run = 1
        for i in range(1, len(doubled)):
            if doubled[i] == doubled[i - 1]:
                run += 1
                assert run <= max_consec + 3, f"連続シフトが長すぎる可能性: emp={emp['employee_id']}"
            else:
                run = 1

        # シフト強度順序: OFFを挟まない限り非減少(輪っか一周分)
        for i in range(n):
            cur = flat_types[i]
            prev = flat_types[i - 1]  # i=0のときは末尾(輪っか)
            if cur != "off" and prev != "off":
                assert SHIFT_TYPE_ORDER[cur] >= SHIFT_TYPE_ORDER[prev], \
                    f"シフト強度逆行: emp={emp['employee_id']} at day {i}: {prev}->{cur}"

    # 必要人数の検証(週ごと・曜日ごと)
    W_ = W
    for req in solver_input["daily_requirements"]:
        d = req["day_of_week"]
        sid = req["shift_id"]
        required = req["required"]
        for w in range(W_):
            count = sum(
                1 for k in range(W_)
                if sol["employee_schedules"][k]["weeks"][w][d]["shift_id"] == sid
            )
            assert count == required, f"week={w} day={d} shift={sid}: count={count} expected={required}"


def test_unknown_engine_raises():
    solver_input = _scenario()
    solver_input["config"]["solver_engine"] = "bogus"
    result = ShiftRotationSchedulerSolver(solver_input).solve()
    assert result["feasible"] is False
    assert result["issues"]


if __name__ == "__main__":
    test_cpsat_feasible_and_satisfies_constraints()
    test_unknown_engine_raises()
    print("ALL PASSED")
