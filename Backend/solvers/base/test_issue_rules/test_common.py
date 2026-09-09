
import pytest
from solvers.base.issue_rules import (
    ISSUE_RULES,
    build_yard_contexts,
    run_issue_rules,
)
from ._common import _container




# ---------------------------------------------------------------------------
# テスト: ルール登録
# ---------------------------------------------------------------------------

class TestRegistration:
    def test_domains_registered(self):
        assert "YardPlanning"  in ISSUE_RULES

    def test_yard_rule_ids(self):
        ids = {r.rule_id for r in ISSUE_RULES["YardPlanning"]}
        expected = {"is_yard", "weight_yard", "is_ship", "weight_ship",
                    "attr_reefer", "attr_imo", "shift_violation", "shift_break",
                    "discharge_before_load"}
        assert expected <= ids

    def test_solution_checker_domains_registered(self):
        """2026-07-24追加: 解チェッカー対象4ドメインがISSUE_RULESに登録されている"""
        for domain in ("TruckDispatcher", "MeetingRoom", "StoreSite", "LineChangeoverScheduler"):
            assert domain in ISSUE_RULES


# ---------------------------------------------------------------------------
# テスト: run_issue_rules エンジン
# ---------------------------------------------------------------------------

class TestRunIssueRules:
    def test_accepted_issues_skipped(self):
        cid_a, cid_b = "A", "B"
        container_map = {
            cid_a: _container(cid_a, yard={"bay": 1, "row": "A", "tier": 2}, order=2, weight=5),
            cid_b: _container(cid_b, yard={"bay": 1, "row": "A", "tier": 1}, order=1, weight=3),
        }
        ctxs = build_yard_contexts(
            container_map=container_map, containers=list(container_map.values()),
            solution_data_list=[], reefer_ids=set(), imo_ids=set(), oog_ids=set(),
            reefer_slot_set=set(), imo_slot_set=set(), oog_excluded_slots=set(),
            crane_windows={}, break_windows={},
        )
        # ACCEPTED なしで発火
        issues = run_issue_rules("YardPlanning", ctxs, {})
        iids = {i["id"] for i in issues}
        assert "is_yard_B_A" in iids or any("is_yard" in iid for iid in iids)

        # ACCEPTED ありでスキップ
        iid = next(i["id"] for i in issues if "is_yard" in i["id"])
        issues2 = run_issue_rules("YardPlanning", ctxs, {iid: "ACCEPTED"})
        iids2 = {i["id"] for i in issues2}
        assert iid not in iids2

    def test_unknown_rule_id_skipped(self):
        ctxs = [{"_rule_id": "nonexistent_rule"}]
        issues = run_issue_rules("YardPlanning", ctxs, {})
        assert issues == []

    def test_exception_in_rule_does_not_crash(self):
        """condition が例外を投げても run_issue_rules がクラッシュしない"""
        ctxs = [{"_rule_id": "is_yard"}]  # 必要キーが欠けている → 例外発生
        issues = run_issue_rules("YardPlanning", ctxs, {})
        # クラッシュせず空リストを返す
        assert isinstance(issues, list)
