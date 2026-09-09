
import pytest
from solvers.base.issue_rules import (
    ISSUE_RULES,
    build_car_sequencing_contexts,
    run_issue_rules,
)


# ---------------------------------------------------------------------------
# テスト: CarSequencing 解チェッカー（2026-09-01新規、バッチ3）
# ---------------------------------------------------------------------------

class TestCarSequencingChecker:
    def test_count_mismatch_fires(self):
        sequence_result = [{"car_type_id": "ct1"}, {"car_type_id": "ct1"}]
        car_types = [{"car_type_id": "ct1", "car_type_name": "SedanA", "count": 3}]
        ctxs = build_car_sequencing_contexts(sequence_result, car_types)
        issues = run_issue_rules("CarSequencing", ctxs, {})
        assert any(i["id"] == "car_type_count_mismatch_ct1" for i in issues)
        assert all(i["category"] == "SOLVER" for i in issues)

    def test_count_match_no_fire(self):
        sequence_result = [{"car_type_id": "ct1"}] * 3
        car_types = [{"car_type_id": "ct1", "car_type_name": "SedanA", "count": 3}]
        ctxs = build_car_sequencing_contexts(sequence_result, car_types)
        issues = run_issue_rules("CarSequencing", ctxs, {})
        assert issues == []

    def test_registered_in_issue_rules(self):
        assert "CarSequencing" in ISSUE_RULES
