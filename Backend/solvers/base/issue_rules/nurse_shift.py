
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable, Dict, List, Optional, Tuple

from solvers.base.solution_checker import solver_bug_issue, sweep_peak_usage
from i18n.common_messages import t as _common_t
from i18n.nurse_shift_weekly_cap_messages import t as _nurse_shift_t

from ._common import IssueRule, _rule




# ---------------------------------------------------------------------------
# _NURSE_SHIFT_RULES（2026-07-12 追加）
#
# 背景: nurse_shift_solver.py / nurse_shift_weekly_cap_solver.py の
# _build_hospital_contexts() は本ファイル冒頭の
# "--- NurseShift/NurseShiftWeeklyCap（旧EventStaffing由来の契約を継承）---"
# として文書化されている契約（solve_failed / understaffed / no_chief /
# unassigned_staff、および看護ドメイン独自追加の night_rest_violation /
# consecutive_night_violation）でcontextを組み立てているが、実体である
# ルールリストは2026-07-11のEventStaffingドメイン削除時に
# ISSUE_RULES から一緒に失われ、"NurseShift" / "NurseShiftWeeklyCap" 側に
# 移植されていなかった。このため run_issue_rules() が常に空リストを返し、
# solve_failed issueが一度も生成されず、フロントエンドのInfeasibleView
# （"制約見直し"タブ）がinfeasibleを検知できずFeasible表示になっていた。
# ---------------------------------------------------------------------------

_NURSE_SHIFT_RULES: List[IssueRule] = [

    _rule(
        "solve_failed",
        condition=lambda ctx: ctx.get("solution_data") is None,
        build=lambda ctx: {
            "id":       "solve_failed",
            "type":     "SOLVE_FAILED",
            "severity": "CRITICAL",
            "category": "INFEASIBLE",
            "title":    _nurse_shift_t("issue.solve_failed.title"),
            "message":  _nurse_shift_t("issue.solve_failed.message"),
            "description": _nurse_shift_t("issue.solve_failed.message"),
            "infeasibleDetail": ctx.get("infeasible_detail"),
        },
    ),

    _rule(
        "understaffed",
        condition=lambda ctx: ctx["req"]["assigned"] < ctx["req"]["required_count"],
        build=lambda ctx: {
            "id":       f"understaffed_{ctx['tid']}",
            "severity": "CRITICAL",
            "category": "STAFFING",
            "title":    _nurse_shift_t("issue.understaffed.title", tid=ctx["tid"]),
            "message":  _nurse_shift_t(
                "issue.understaffed.message",
                tid=ctx["tid"], required_count=ctx["req"]["required_count"], assigned=ctx["req"]["assigned"],
            ),
        },
    ),

    _rule(
        "no_chief",
        condition=lambda ctx: (
            ctx["req"].get("min_chiefs", 0) > 0
            and ctx["req"].get("chiefs", 0) < ctx["req"]["min_chiefs"]
        ),
        build=lambda ctx: {
            "id":       f"no_chief_{ctx['tid']}",
            "severity": "WARNING",
            "category": "STAFFING",
            "title":    _nurse_shift_t("issue.no_chief.title", tid=ctx["tid"]),
            "message":  _nurse_shift_t(
                "issue.no_chief.message",
                tid=ctx["tid"], min_chiefs=ctx["req"]["min_chiefs"], chiefs=ctx["req"].get("chiefs", 0),
            ),
        },
    ),

    _rule(
        "unassigned_staff",
        condition=lambda ctx: ctx["sid"] not in ctx["staff_ids_with_assignment"],
        build=lambda ctx: {
            "id":       f"unassigned_staff_{ctx['sid']}",
            "severity": "INFO",
            "category": "UTILIZATION",
            "title":    _nurse_shift_t("issue.unassigned_staff.title", name=ctx["staff"].get("name", ctx["sid"])),
            "message":  _nurse_shift_t("issue.unassigned_staff.message", name=ctx["staff"].get("name", ctx["sid"])),
        },
    ),

    _rule(
        "night_rest_violation",
        condition=lambda ctx: ctx["rest_actual"] < ctx["min_rest"],
        build=lambda ctx: {
            "id":       f"night_rest_violation_{ctx['sid']}_{ctx['night_task_id']}",
            "severity": "CRITICAL",
            "category": "COMPLIANCE",
            "title":    _nurse_shift_t(
                "issue.night_rest_violation.title", name=ctx["staff"].get("name", ctx["sid"]),
            ),
            "message":  _nurse_shift_t(
                "issue.night_rest_violation.message",
                name=ctx["staff"].get("name", ctx["sid"]), night_task_id=ctx["night_task_id"],
                next_task_id=ctx["next_task_id"], rest_actual=ctx["rest_actual"], min_rest=ctx["min_rest"],
            ),
        },
    ),

    _rule(
        "consecutive_night_violation",
        condition=lambda ctx: ctx["consecutive_count"] > ctx["max_consecutive"],
        build=lambda ctx: {
            "id":       f"consecutive_night_violation_{ctx['sid']}",
            "severity": "CRITICAL",
            "category": "COMPLIANCE",
            "title":    _nurse_shift_t(
                "issue.consecutive_night_violation.title", name=ctx["staff"].get("name", ctx["sid"]),
            ),
            "message":  _nurse_shift_t(
                "issue.consecutive_night_violation.message",
                name=ctx["staff"].get("name", ctx["sid"]), consecutive_count=ctx["consecutive_count"],
                max_consecutive=ctx["max_consecutive"], night_days=ctx["night_days"],
            ),
        },
    ),
]
