
import pytest
from solvers.base.issue_rules import (
    ISSUE_RULES,
    build_transport_cost_minimizer_contexts,
    run_issue_rules,
)




# ---------------------------------------------------------------------------
# テスト: TransportCostMinimizer 解チェッカー（2026-08-30新規、パイロット第1弾）
# ---------------------------------------------------------------------------

class TestTransportCostMinimizerChecker:
    def test_demand_unmet_fires(self):
        factories = [{"id": "f1", "name": "工場A", "supply": 100}]
        stores = [{"id": "s1", "name": "店舗X", "demand": 60}]
        flows = [{"factory_id": "f1", "store_id": "s1", "amount": 55}]  # 5不足
        ctxs = build_transport_cost_minimizer_contexts(factories, stores, flows)
        issues = run_issue_rules("TransportCostMinimizer", ctxs, {})
        assert len(issues) == 1
        assert issues[0]["id"] == "demand_unmet_s1"
        assert issues[0]["category"] == "SOLVER"

    def test_demand_met_no_fire(self):
        factories = [{"id": "f1", "name": "工場A", "supply": 100}]
        stores = [{"id": "s1", "name": "店舗X", "demand": 60}]
        flows = [{"factory_id": "f1", "store_id": "s1", "amount": 60}]
        ctxs = build_transport_cost_minimizer_contexts(factories, stores, flows)
        issues = run_issue_rules("TransportCostMinimizer", ctxs, {})
        assert issues == []

    def test_supply_exceeded_fires(self):
        factories = [{"id": "f1", "name": "工場A", "supply": 100}]
        stores = [{"id": "s1", "name": "店舗X", "demand": 105}]
        flows = [{"factory_id": "f1", "store_id": "s1", "amount": 105}]  # 供給上限100を超過
        ctxs = build_transport_cost_minimizer_contexts(factories, stores, flows)
        issues = run_issue_rules("TransportCostMinimizer", ctxs, {})
        assert any(i["id"] == "supply_exceeded_f1" for i in issues)
        assert all(i["category"] == "SOLVER" for i in issues)

    def test_registered_in_issue_rules(self):
        # 2026-08-30以前は"TransportCostMinimizer"がISSUE_RULESに未登録で、
        # demand_unmetチェックが死んでいた（solver.py側のガード節が常にFalse）。
        # この登録漏れの再発を防ぐための回帰テスト。
        assert "TransportCostMinimizer" in ISSUE_RULES
