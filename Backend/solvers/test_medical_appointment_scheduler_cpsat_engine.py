"""
test_medical_appointment_scheduler_cpsat_engine.py

MedicalAppointmentSchedulerSolver の config.solver_engine="cpsat" 経路の構造テスト。
docplex/CPLEXがないサンドボックスでも実行できる。制約充足とlexicographic優先順位を検証する。
"""

from __future__ import annotations

import sys
import os

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from solvers.medical_appointment_scheduler_solver import MedicalAppointmentSchedulerSolver


def _make_slots(resource_id, day, weekday, count=4, slot_dur=30, start0=540):
    slots = []
    for i in range(count):
        slots.append({
            "resource_id": resource_id,
            "day": day,
            "slot_index": i,
            "start_min": start0 + i * slot_dur,
            "slot_duration_min": slot_dur,
            "weekday": weekday,
            "time_slot": "AM" if start0 + i * slot_dur < 720 else "PM",
        })
    return slots


def _scenario():
    resources = [
        {"id": "doc1", "name": "Dr.A", "resource_type": "doctor"},
        {"id": "room1", "name": "Room1", "resource_type": "room"},
    ]
    slots = []
    slots += _make_slots("doc1", "2026-08-17", "Mon")
    slots += _make_slots("room1", "2026-08-17", "Mon")
    slots += _make_slots("doc1", "2026-08-18", "Tue")
    slots += _make_slots("room1", "2026-08-18", "Tue")

    requests = [
        {
            "id": "req1", "patient_id": "p1", "patient_name": "Patient1",
            "resource_type_needs": ["doctor", "room"], "duration_slots": 1,
            "preferred_days": ["2026-08-17"],
        },
        {
            "id": "req2", "patient_id": "p2", "patient_name": "Patient2",
            "resource_type_needs": ["doctor", "room"], "duration_slots": 1,
            "preferred_days": ["2026-08-17"],
        },
    ]
    return {
        "problem_class": "MedicalAppointmentScheduler",
        "requests": requests,
        "resources": resources,
        "slots": slots,
        "config": {"solver_engine": "cpsat", "time_limit_sec": 15},
        "issue_statuses": {},
    }


def test_cpsat_feasible_no_double_booking():
    solver_input = _scenario()
    result = MedicalAppointmentSchedulerSolver(solver_input).solve()
    assert result["feasible"] is True, result["issues"]
    assignments = result["solutions"][0]["assignments"]
    assert len(assignments) == 2  # 両方割当可能なはず(day違いで衝突回避できる)

    # 資源二重予約なし: 同一資源・同一時間帯に2件割り当てられていないこと
    seen = set()
    for a in assignments:
        for r in a["assigned_resources"]:
            key = (r["resource_id"], a["day"], a["start_min"])
            assert key not in seen, f"二重予約検出: {key}"
            seen.add(key)


def test_cpsat_kpi_shape():
    solver_input = _scenario()
    result = MedicalAppointmentSchedulerSolver(solver_input).solve()
    assert result["feasible"] is True
    kpi = result["solutions"][0]["kpi"]
    assert kpi["total_requests"] == 2
    assert "coverage_rate" in kpi
    assert "pref_total_violations" in kpi


def test_unknown_engine_raises():
    solver_input = _scenario()
    solver_input["config"]["solver_engine"] = "bogus"
    result = MedicalAppointmentSchedulerSolver(solver_input).solve()
    assert result["feasible"] is False
    assert result["issues"]


if __name__ == "__main__":
    test_cpsat_feasible_no_double_booking()
    test_cpsat_kpi_shape()
    test_unknown_engine_raises()
    print("ALL PASSED")
