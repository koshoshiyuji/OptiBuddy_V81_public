"""
test_medical_appointment_sequence_scheduler_cpsat_engine.py

MedicalAppointmentSequenceSchedulerSolver の config.solver_engine="cpsat" 経路の
構造テスト。docplex/CPLEXがないサンドボックスでも実行できる。
"""

from __future__ import annotations

import sys
import os

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from solvers.medical_appointment_sequence_scheduler_solver import (
    MedicalAppointmentSequenceSchedulerSolver,
)


def _scenario():
    resources = [
        {"id": "D1", "name": "Dr.A", "type": "doctor", "available_slots": [{"start_min": 0, "end_min": 500}]},
        {"id": "D2", "name": "Dr.B", "type": "doctor", "available_slots": [{"start_min": 0, "end_min": 500}]},
        {"id": "L1", "name": "Lab1", "type": "lab", "available_slots": [{"start_min": 0, "end_min": 500}]},
    ]
    sequences = [
        {
            "id": "S1", "patient_name": "Patient1",
            "visits": [
                {"id": "S1_v1", "order": 0, "duration_min": 30,
                 "required_resources": [{"type": "doctor", "count": 1}]},
                {"id": "S1_v2", "order": 1, "duration_min": 20,
                 "required_resources": [{"type": "lab", "count": 1}]},
            ],
            "interval_rules": [{"from_visit": 0, "to_visit": 1, "min_gap": 20}],
            "same_resource_rules": [],
            "blackout_slots": [],
        },
        {
            "id": "S2", "patient_name": "Patient2",
            "visits": [
                {"id": "S2_v1", "order": 0, "duration_min": 30,
                 "required_resources": [{"type": "doctor", "count": 1}]},
                {"id": "S2_v2", "order": 1, "duration_min": 20,
                 "required_resources": [{"type": "lab", "count": 1}]},
            ],
            "interval_rules": [{"from_visit": 0, "to_visit": 1, "min_gap": 20}],
            "same_resource_rules": [],
            "blackout_slots": [],
        },
    ]
    return {
        "problem_class": "MedicalAppointmentSequenceScheduler",
        "sequences": sequences,
        "resources": resources,
        "config": {"solver_engine": "cpsat", "time_limit_sec": 20, "horizon_min": 1000},
        "issue_statuses": {},
    }


def test_cpsat_feasible_respects_order_gap_and_no_double_booking():
    solver_input = _scenario()
    result = MedicalAppointmentSequenceSchedulerSolver(solver_input).solve()
    assert result["feasible"] is True, result["issues"]
    sol = result["solutions"][0]

    # 1台のlab資源しかないので、両系列が同時にlabを使うことはできない
    # (docorは2台あるので少なくとも1系列はスケジュールされるはず)
    assert sol["kpi"]["scheduled_count"] >= 1

    for seq_data in sol["scheduled_sequences"]:
        visits_sorted = sorted(seq_data["visits"], key=lambda v: v["visit_order"])
        # 順序制約: 前の受診が後の受診より前に終わる
        for i in range(len(visits_sorted) - 1):
            assert visits_sorted[i]["end_min"] <= visits_sorted[i + 1]["start_min"]
        # 間隔制約(min_gap=20)
        if len(visits_sorted) >= 2:
            gap = visits_sorted[1]["start_min"] - visits_sorted[0]["end_min"]
            assert gap >= 20

    # 資源の二重予約が無いこと(独立検証)
    usage = {}
    for seq_data in sol["scheduled_sequences"]:
        for v in seq_data["visits"]:
            for ar in v["assigned_resources"]:
                usage.setdefault(ar["resource_id"], []).append((ar["start_min"], ar["end_min"]))
    for rid, intervals in usage.items():
        intervals.sort()
        for i in range(len(intervals) - 1):
            assert intervals[i][1] <= intervals[i + 1][0], f"資源{rid}の二重予約: {intervals}"


def test_cpsat_kpi_shape():
    solver_input = _scenario()
    result = MedicalAppointmentSequenceSchedulerSolver(solver_input).solve()
    assert result["feasible"] is True
    kpi = result["solutions"][0]["kpi"]
    assert kpi["total_sequences"] == 2
    assert "max_resource_load_min" in kpi


def test_unknown_engine_raises():
    solver_input = _scenario()
    solver_input["config"]["solver_engine"] = "bogus"
    result = MedicalAppointmentSequenceSchedulerSolver(solver_input).solve()
    assert result["feasible"] is False
    assert result["issues"]


if __name__ == "__main__":
    test_cpsat_feasible_respects_order_gap_and_no_double_booking()
    test_cpsat_kpi_shape()
    test_unknown_engine_raises()
    print("ALL PASSED")
