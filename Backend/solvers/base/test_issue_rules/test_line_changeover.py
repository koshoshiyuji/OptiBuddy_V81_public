
import pytest
from solvers.base.issue_rules import (
    build_line_changeover_precedence_contexts,
    build_line_changeover_resource_contexts,
    run_issue_rules,
)




# ---------------------------------------------------------------------------
# テスト: LineChangeoverScheduler 解チェッカー（2026-07-24新規）
# ---------------------------------------------------------------------------

class TestLineChangeoverSchedulerChecker:
    def test_precedence_violation_fires(self):
        schedule = [
            {"task_id": "T1", "start": 0, "end": 100},
            {"task_id": "T2", "start": 90, "end": 150},  # T1終了(100)+delay(10)=110 > 90 → 違反
        ]
        precedences = [{"from_task": "T1", "to_task": "T2", "min_delay": 10}]
        ctxs = build_line_changeover_precedence_contexts(schedule, precedences)
        issues = run_issue_rules("LineChangeoverScheduler", ctxs, {})
        assert len(issues) == 1
        assert issues[0]["category"] == "SOLVER"

    def test_precedence_ok_no_fire(self):
        schedule = [
            {"task_id": "T1", "start": 0, "end": 100},
            {"task_id": "T2", "start": 120, "end": 150},
        ]
        precedences = [{"from_task": "T1", "to_task": "T2", "min_delay": 10}]
        ctxs = build_line_changeover_precedence_contexts(schedule, precedences)
        issues = run_issue_rules("LineChangeoverScheduler", ctxs, {})
        assert issues == []

    def test_resource_capacity_violation_fires(self):
        # 資源capacity=1に対し、T1(0-100)とT2(50-150)が同時に1ずつ使用 → 50-100の間で2使用、超過
        schedule = [
            {"task_id": "T1", "start": 0, "end": 100,
             "resource_requirements": [{"resource_id": "R1", "amount": 1}]},
            {"task_id": "T2", "start": 50, "end": 150,
             "resource_requirements": [{"resource_id": "R1", "amount": 1}]},
        ]
        resources = [{"id": "R1", "name": "資源1", "capacity": 1}]
        ctxs = build_line_changeover_resource_contexts(schedule, resources)
        issues = run_issue_rules("LineChangeoverScheduler", ctxs, {})
        assert len(issues) == 1
        assert issues[0]["category"] == "SOLVER"

    def test_resource_capacity_ok_no_fire(self):
        # 直列（重複なし）なら容量1でも違反しない
        schedule = [
            {"task_id": "T1", "start": 0, "end": 100,
             "resource_requirements": [{"resource_id": "R1", "amount": 1}]},
            {"task_id": "T2", "start": 100, "end": 200,
             "resource_requirements": [{"resource_id": "R1", "amount": 1}]},
        ]
        resources = [{"id": "R1", "name": "資源1", "capacity": 1}]
        ctxs = build_line_changeover_resource_contexts(schedule, resources)
        issues = run_issue_rules("LineChangeoverScheduler", ctxs, {})
        assert issues == []
