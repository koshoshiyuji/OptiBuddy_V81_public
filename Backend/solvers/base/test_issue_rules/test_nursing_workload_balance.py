
import pytest
from solvers.base.issue_rules import (
    ISSUE_RULES,
    build_nursing_workload_balance_contexts,
    run_issue_rules,
)




# ---------------------------------------------------------------------------
# テスト: NursingWorkloadBalance 解チェッカー（2026-08-31新規、バッチ2）
# ---------------------------------------------------------------------------

class TestNursingWorkloadBalanceChecker:
    def test_patient_count_out_of_range_fires(self):
        nurses = [{"id": "n1", "minPatientsPerNurse": 2, "maxPatientsPerNurse": 5, "maxWorkloadPerNurse": 20}]
        nurse_workloads = [{"nurse_id": "n1", "nurse_name": "Aさん", "patient_count": 1, "total_acuity": 5}]
        ctxs = build_nursing_workload_balance_contexts(nurse_workloads, nurses)
        issues = run_issue_rules("NursingWorkloadBalance", ctxs, {})
        assert any(i["id"] == "nurse_patient_count_out_of_range_n1" for i in issues)
        assert all(i["category"] == "SOLVER" for i in issues)

    def test_workload_exceeded_fires(self):
        nurses = [{"id": "n1", "minPatientsPerNurse": 1, "maxPatientsPerNurse": 5, "maxWorkloadPerNurse": 10}]
        nurse_workloads = [{"nurse_id": "n1", "nurse_name": "Aさん", "patient_count": 3, "total_acuity": 15}]
        ctxs = build_nursing_workload_balance_contexts(nurse_workloads, nurses)
        issues = run_issue_rules("NursingWorkloadBalance", ctxs, {})
        assert any(i["id"] == "nurse_workload_exceeded_n1" for i in issues)

    def test_within_limits_no_fire(self):
        nurses = [{"id": "n1", "minPatientsPerNurse": 1, "maxPatientsPerNurse": 5, "maxWorkloadPerNurse": 20}]
        nurse_workloads = [{"nurse_id": "n1", "nurse_name": "Aさん", "patient_count": 3, "total_acuity": 15}]
        ctxs = build_nursing_workload_balance_contexts(nurse_workloads, nurses)
        issues = run_issue_rules("NursingWorkloadBalance", ctxs, {})
        assert issues == []

    def test_registered_in_issue_rules(self):
        assert "NursingWorkloadBalance" in ISSUE_RULES
