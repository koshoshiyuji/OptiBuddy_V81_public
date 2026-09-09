
import pytest
from solvers.base.issue_rules import (
    build_truck_dispatcher_contexts,
    run_issue_rules,
)




# ---------------------------------------------------------------------------
# テスト: TruckDispatcher 解チェッカー（2026-07-24追加）
# ---------------------------------------------------------------------------

class TestTruckDispatcherChecker:
    def test_duty_overtime_fires_with_solver_category(self):
        """拘束時間ハード制約違反 → category=SOLVER（バグ疑い）"""
        routes = [{"vehicle_id": "V1", "vehicle_name": "車1", "duty_min": 600, "stops": []}]
        ctxs = build_truck_dispatcher_contexts(routes, {"V1": 540})
        issues = run_issue_rules("TruckDispatcher", ctxs, {})
        overtime = [i for i in issues if i["id"] == "duty_overtime"]
        assert len(overtime) == 1
        assert overtime[0]["category"] == "SOLVER"
        assert overtime[0]["severity"] == "CRITICAL"

    def test_duty_overtime_no_fire_within_limit(self):
        routes = [{"vehicle_id": "V1", "vehicle_name": "車1", "duty_min": 500, "stops": []}]
        ctxs = build_truck_dispatcher_contexts(routes, {"V1": 540})
        issues = run_issue_rules("TruckDispatcher", ctxs, {})
        assert not any(i["id"] == "duty_overtime" for i in issues)

    def test_tw_overdue_fires_without_solver_category(self):
        """TW超過はソフト制約の業務結果 → category=SOLVERは付与しない"""
        routes = [{"vehicle_id": "V1", "vehicle_name": "車1", "duty_min": 100, "stops": [
            {"customer_name": "顧客A", "arrival_min": 700, "tw_close_min": 600},
        ]}]
        ctxs = build_truck_dispatcher_contexts(routes, {"V1": 540})
        issues = run_issue_rules("TruckDispatcher", ctxs, {})
        overdue = [i for i in issues if i["id"] == "tw_overdue"]
        assert len(overdue) == 1
        assert overdue[0].get("category") != "SOLVER"
