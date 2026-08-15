"""
test_patient_transport_planner_cpsat_engine.py

PatientTransportPlannerSolver の config.solver_engine="cpsat" 経路の構造テスト。
docplex/CPLEXがないサンドボックスでも実行できる（本ドメインはStage分割の無い
純CPドメインのため、solve()全体を通しでテストできる）。
"""

from __future__ import annotations

import sys
import os

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from solvers.patient_transport_planner_solver import PatientTransportPlannerSolver


def _scenario():
    requests = [
        {
            "id": "R1", "patient_id": "P1", "request_type": "roundtrip",
            "home_location": {"lat": 0.01, "lng": 0.01},
            "appointment_time": "09:00", "exam_duration_min": 60,
            "wait_tolerance_min": 30, "capacity_required": 1, "care_type": "general",
        },
        {
            "id": "R2", "patient_id": "P2", "request_type": "roundtrip",
            "home_location": {"lat": -0.01, "lng": 0.02},
            "appointment_time": "09:30", "exam_duration_min": 45,
            "wait_tolerance_min": 30, "capacity_required": 1, "care_type": "general",
        },
    ]
    vehicles = [
        {"id": "V1", "name": "Van1", "capacity": 2, "care_types": ["general"],
         "start_time": "07:00", "end_time": "19:00"},
        {"id": "V2", "name": "Van2", "capacity": 2, "care_types": ["general"],
         "start_time": "07:00", "end_time": "19:00"},
    ]
    return {
        "problem_class": "PatientTransportPlanner",
        "requests": requests,
        "vehicles": vehicles,
        "config": {"solver_engine": "cpsat", "solve_time_sec": 20},
        "issue_statuses": {},
    }


def test_cpsat_feasible_and_serves_requests():
    solver_input = _scenario()
    result = PatientTransportPlannerSolver(solver_input).solve()
    assert result["feasible"] is True, result["issues"]
    sol = result["solutions"][0]
    assert sol["kpi"]["total_requests"] == 2

    # 少なくとも1件は対応できているはず(車両2台で余裕がある設定)
    assert sol["kpi"]["served_count"] >= 1

    # 各車両の担当フェーズが時間的に重複していないか(独立検証)
    for vs in sol["vehicle_schedules"]:
        phases = sorted(vs["phases"], key=lambda p: p["start"])
        for i in range(len(phases) - 1):
            assert phases[i]["end"] <= phases[i + 1]["start"], \
                f"車両{vs['vehicle_id']}: 重複検出 {phases[i]} vs {phases[i+1]}"

    # 往復セット: 出発済みなら復路も対応(または両方未対応)、片方だけはNG想定と一致するか確認
    for rr in sol["request_results"]:
        if rr["req_type"] == "roundtrip" and rr["served"]:
            assert "outbound_vehicle" in rr and "inbound_vehicle" in rr
            # 往路終了 + 診察時間 <= 復路開始
            req = next(r for r in solver_input["requests"] if r["id"] == rr["request_id"])
            exam = req["exam_duration_min"]
            assert rr["outbound_end"] + exam <= rr["inbound_start"]


def test_cpsat_kpi_shape():
    solver_input = _scenario()
    result = PatientTransportPlannerSolver(solver_input).solve()
    assert result["feasible"] is True
    kpi = result["solutions"][0]["kpi"]
    for k in ("served_count", "total_requests", "unserved_count", "service_rate",
              "total_ride_time_min", "solve_time_sec", "is_optimal"):
        assert k in kpi


def test_unknown_engine_raises():
    solver_input = _scenario()
    solver_input["config"]["solver_engine"] = "bogus"
    result = PatientTransportPlannerSolver(solver_input).solve()
    assert result["feasible"] is False
    assert result["issues"]


if __name__ == "__main__":
    test_cpsat_feasible_and_serves_requests()
    test_cpsat_kpi_shape()
    test_unknown_engine_raises()
    print("ALL PASSED")
