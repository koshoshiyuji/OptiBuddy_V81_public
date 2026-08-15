"""
test_nursing_workload_balance_cpsat_engine.py

NursingWorkloadBalanceSolver の config.solver_engine="cpsat" 経路の構造テスト。
docplex/CPLEXがないサンドボックスでも実行できる。brute-forceで最適値を独立検証する。
"""

from __future__ import annotations

import sys
import os
import itertools

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from solvers.nursing_workload_balance_solver import NursingWorkloadBalanceSolver


def _scenario():
    nurses = [
        {"id": "n1", "name": "N1", "zone": "A", "minPatientsPerNurse": 1, "maxPatientsPerNurse": 3, "maxWorkloadPerNurse": 20},
        {"id": "n2", "name": "N2", "zone": "A", "minPatientsPerNurse": 1, "maxPatientsPerNurse": 3, "maxWorkloadPerNurse": 20},
    ]
    patients = [
        {"id": "p1", "name": "P1", "zone": "A", "acuity": 3},
        {"id": "p2", "name": "P2", "zone": "A", "acuity": 2},
        {"id": "p3", "name": "P3", "zone": "A", "acuity": 4},
        {"id": "p4", "name": "P4", "zone": "A", "acuity": 1},
    ]
    return {
        "problem_class": "NursingWorkloadBalance",
        "nurses": nurses,
        "patients": patients,
        "config": {"solver_engine": "cpsat", "time_limit_sec": 15},
        "issue_statuses": {},
    }


def brute_force_optimal(nurses, patients):
    n_nurses = len(nurses)
    best = None
    for combo in itertools.product(range(n_nurses), repeat=len(patients)):
        counts = [0] * n_nurses
        workloads = [0] * n_nurses
        for p_idx, k in enumerate(combo):
            counts[k] += 1
            workloads[k] += patients[p_idx]["acuity"]
        feasible = True
        for k, n in enumerate(nurses):
            if not (n["minPatientsPerNurse"] <= counts[k] <= n["maxPatientsPerNurse"]):
                feasible = False
                break
            if workloads[k] > n["maxWorkloadPerNurse"]:
                feasible = False
                break
        if not feasible:
            continue
        obj = sum(w * w for w in workloads)
        if best is None or obj < best:
            best = obj
    return best


def test_cpsat_matches_brute_force_optimum():
    solver_input = _scenario()
    result = NursingWorkloadBalanceSolver(solver_input).solve()
    assert result["feasible"] is True, result["issues"]
    sq_sum = result["solutions"][0]["metrics"]["workload_sq_sum"]

    expected = brute_force_optimal(solver_input["nurses"], solver_input["patients"])
    assert sq_sum == expected, f"cpsat={sq_sum} vs brute_force={expected}"


def test_cpsat_respects_min_max_and_workload_cap():
    solver_input = _scenario()
    result = NursingWorkloadBalanceSolver(solver_input).solve()
    assert result["feasible"] is True
    for w in result["solutions"][0]["nurse_workloads"]:
        nurse = next(n for n in solver_input["nurses"] if n["id"] == w["nurse_id"])
        assert nurse["minPatientsPerNurse"] <= w["patient_count"] <= nurse["maxPatientsPerNurse"]
        assert w["total_acuity"] <= nurse["maxWorkloadPerNurse"]
    assert len(result["solutions"][0]["assignments"]) == len(solver_input["patients"])


def test_unknown_engine_raises():
    solver_input = _scenario()
    solver_input["config"]["solver_engine"] = "bogus"
    result = NursingWorkloadBalanceSolver(solver_input).solve()
    assert result["feasible"] is False
    assert result["issues"]


if __name__ == "__main__":
    test_cpsat_matches_brute_force_optimum()
    test_cpsat_respects_min_max_and_workload_cap()
    test_unknown_engine_raises()
    print("ALL PASSED")
