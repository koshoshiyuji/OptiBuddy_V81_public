
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable, Dict, List, Optional, Tuple

from solvers.base.solution_checker import solver_bug_issue, sweep_peak_usage
from i18n.common_messages import t as _common_t
from i18n.nurse_shift_weekly_cap_messages import t as _nurse_shift_t

from ._common import IssueRule, _rule




# ---------------------------------------------------------------------------
# _SHIFT_ROTATION_SCHEDULER_RULES（2026-09-01 新規、solution checker展開バッチ5）
#
# 制約: shift_rotation_scheduler_solver.pyの5つのhard制約（週内土日一致/
# 連続日数[min_consecutive,max_consecutive]/シフト順序前進のみ/14日窓最低休日数/
# 曜日別必要人数の厳密一致）を、返ってきたsolution（template_shift_ids /
# employee_schedules）とDSL宣言（shifts/daily_requirements/constraints）のみから
# 独立に再検算する。solver内部のCpoModel/cpmpy変数は一切参照しない。
# _solve_with_cpsat()に元々issue_statuses変数が定義されていなかったバグも
# あわせて修正する（CPO側にはあった）。
# ---------------------------------------------------------------------------

# solvers/shift_rotation_scheduler_solver.py の SHIFT_TYPE_ORDER と同期させること
_SRS_SHIFT_TYPE_ORDER = {"off": 0, "early": 1, "late": 2, "night": 3}


_SHIFT_ROTATION_SCHEDULER_RULES: List[IssueRule] = [
    _rule(
        "weekend_shift_mismatch",
        condition=lambda ctx: ctx["sat_shift_id"] != ctx["sun_shift_id"],
        build=lambda ctx: solver_bug_issue(
            f"weekend_shift_mismatch_w{ctx['week']}",
            f"土日シフト不一致（解チェッカー）: 週{ctx['week']}",
            f"週{ctx['week']}の土曜シフト「{ctx['sat_shift_id']}」と日曜シフト"
            f"「{ctx['sun_shift_id']}」が一致していません。同一週の土日は同じ"
            "シフトになる制約と矛盾しています。",
        ),
    ),
    _rule(
        "daily_requirement_mismatch",
        condition=lambda ctx: ctx["actual_count"] != ctx["required_count"],
        build=lambda ctx: solver_bug_issue(
            f"daily_requirement_mismatch_w{ctx['week']}_d{ctx['day_of_week']}_{ctx['shift_id']}",
            f"曜日別必要人数不一致（解チェッカー）: week{ctx['week']} "
            f"day{ctx['day_of_week']} / {ctx['shift_id']}",
            f"週{ctx['week']}・曜日{ctx['day_of_week']}のシフト「{ctx['shift_id']}」の"
            f"実際の割当人数{ctx['actual_count']}人が、必要人数"
            f"{ctx['required_count']}人と一致しません。",
        ),
    ),
    _rule(
        "shift_progression_violation",
        condition=lambda ctx: ctx["order_cur"] < ctx["order_prev"],
        build=lambda ctx: solver_bug_issue(
            f"shift_progression_violation_{ctx['employee_id']}_day{ctx['day_index']}",
            f"シフト順序逆行（解チェッカー）: {ctx['employee_id']} day{ctx['day_index']}",
            f"従業員「{ctx['employee_id']}」の週{ctx['week_prev']}曜日{ctx['dow_prev']}"
            f"→週{ctx['week_cur']}曜日{ctx['dow_cur']}で、シフト"
            f"「{ctx['shift_type_prev']}」→「{ctx['shift_type_cur']}」と逆行しています"
            "（OFFを挟まない限りシフトタイプは前進のみ許可される制約と矛盾）。",
        ),
    ),
    _rule(
        "consecutive_run_length_violation",
        condition=lambda ctx: (
            ctx["run_length"] < ctx["min_consecutive"]
            or ctx["run_length"] > ctx["max_consecutive"]
        ),
        build=lambda ctx: solver_bug_issue(
            f"consecutive_run_length_violation_{ctx['employee_id']}_start{ctx['run_start_day']}",
            f"連続日数制約違反（解チェッカー）: {ctx['employee_id']}",
            f"従業員「{ctx['employee_id']}」のシフト「{ctx['shift_id']}」がday"
            f"{ctx['run_start_day']}から{ctx['run_length']}日連続しており、"
            f"許容範囲[{ctx['min_consecutive']}, {ctx['max_consecutive']}]から外れています。",
        ),
    ),
    _rule(
        "days_off_window_violation",
        condition=lambda ctx: ctx["off_count"] < ctx["min_days_off_per_14"],
        build=lambda ctx: solver_bug_issue(
            f"days_off_window_violation_{ctx['employee_id']}_start{ctx['window_start_day']}",
            f"14日窓休日不足（解チェッカー）: {ctx['employee_id']}",
            f"従業員「{ctx['employee_id']}」のday{ctx['window_start_day']}から14日間の"
            f"休日数が{ctx['off_count']}日しかなく、最低"
            f"{ctx['min_days_off_per_14']}日を下回っています。",
        ),
    ),
    _rule(
        "employee_schedule_derivation_mismatch",
        condition=lambda ctx: ctx["actual_shift_id"] != ctx["expected_shift_id"],
        build=lambda ctx: solver_bug_issue(
            f"employee_schedule_derivation_mismatch_{ctx['employee_id']}_w{ctx['week']}_d{ctx['day_of_week']}",
            f"従業員スケジュール導出不一致（解チェッカー）: {ctx['employee_id']}",
            f"従業員「{ctx['employee_id']}」の週{ctx['week']}曜日{ctx['day_of_week']}の"
            f"シフトが「{ctx['actual_shift_id']}」ですが、テンプレート導出式"
            f"（template[(week+k)%W][day]）から期待される値"
            f"「{ctx['expected_shift_id']}」と一致しません（template↔従業員展開の"
            "抽出処理に矛盾がある可能性があります）。",
        ),
    ),
]



# ---------------------------------------------------------------------------
# ShiftRotationScheduler context ビルダー（2026-09-01 新規、バッチ5）
# ---------------------------------------------------------------------------

def _shift_rotation_cyclic_runs(seq: List[str]) -> List[Tuple[int, int, str]]:
    """(run_start_index, run_length, value) を、円環状seqの全極大runについて返す。"""
    n = len(seq)
    if n == 0:
        return []
    if all(v == seq[0] for v in seq):
        return [(0, n, seq[0])]
    b = next(i for i in range(n) if seq[i] != seq[i - 1])
    runs: List[Tuple[int, int, str]] = []
    i, seen = b, 0
    while seen < n:
        val = seq[i % n]
        start, length = i % n, 0
        while seq[i % n] == val and length < n:
            i += 1
            length += 1
            seen += 1
        runs.append((start, length, val))
    return runs



def build_shift_rotation_scheduler_contexts(
    template_shift_ids: List[List[str]],   # solution["template_shift_ids"], W x 7
    employee_schedules: List[Dict],        # solution["employee_schedules"]
    shifts_def:         List[Dict],        # dsl["shifts"]
    daily_requirements: List[Dict],        # dsl["daily_requirements"]
    constraints_cfg:    Dict,              # dsl["constraints"]
) -> List[Dict[str, Any]]:
    """
    ShiftRotationScheduler 用の独立検証contextを生成する。solver内部の
    CpoModel/cpmpy変数は一切参照せず、返ってきたtemplate_shift_ids /
    employee_schedulesと、DSL宣言のshifts/daily_requirements/constraintsの
    みから検算する。デフォルト値はsolver本体
    （_solve_with_cpo()/_solve_with_cpsat()）と揃えてある
    （min_consecutive=2, max_consecutive=4, min_days_off_per_14=2）。
    """
    min_consec     = int(constraints_cfg.get("min_consecutive", 2))
    max_consec     = int(constraints_cfg.get("max_consecutive", 4))
    min_off_per_14 = int(constraints_cfg.get("min_days_off_per_14", 2))

    W = len(template_shift_ids)
    total_days = W * 7
    known_shift_ids = {s["id"] for s in shifts_def}

    ctxs: List[Dict[str, Any]] = []

    for w in range(W):
        ctxs.append({
            "_rule_id": "weekend_shift_mismatch", "week": w,
            "sat_shift_id": template_shift_ids[w][5],
            "sun_shift_id": template_shift_ids[w][6],
        })

    counts: Dict[int, Dict[int, Dict[str, int]]] = {
        w: {d: {} for d in range(7)} for w in range(W)
    }
    for emp in employee_schedules:
        for w, week_row in enumerate(emp["weeks"]):
            for d, day in enumerate(week_row):
                sid = day["shift_id"]
                counts[w][d][sid] = counts[w][d].get(sid, 0) + 1
    for w in range(W):
        for req in daily_requirements:
            shift_id = req["shift_id"]
            if shift_id not in known_shift_ids:
                continue
            d = int(req["day_of_week"])
            ctxs.append({
                "_rule_id": "daily_requirement_mismatch",
                "week": w, "day_of_week": d, "shift_id": shift_id,
                "actual_count": counts[w][d].get(shift_id, 0),
                "required_count": int(req["required"]),
            })

    for k, emp in enumerate(employee_schedules):
        eid = emp["employee_id"]
        flat = [day for week_row in emp["weeks"] for day in week_row]

        for i in range(total_days):
            cur, prev = flat[i], flat[i - 1]
            if cur["shift_type"] == "off" or prev["shift_type"] == "off":
                continue
            order_cur = _SRS_SHIFT_TYPE_ORDER.get(cur["shift_type"], 0)
            order_prev = _SRS_SHIFT_TYPE_ORDER.get(prev["shift_type"], 0)
            wk_cur, dow_cur = divmod(i, 7)
            wk_prev, dow_prev = divmod((i - 1) % total_days, 7)
            ctxs.append({
                "_rule_id": "shift_progression_violation",
                "employee_id": eid, "day_index": i,
                "order_cur": order_cur, "order_prev": order_prev,
                "shift_type_cur": cur["shift_type"], "shift_type_prev": prev["shift_type"],
                "week_cur": wk_cur, "dow_cur": dow_cur,
                "week_prev": wk_prev, "dow_prev": dow_prev,
            })

        shift_id_seq = [day["shift_id"] for day in flat]
        for start, length, sid in _shift_rotation_cyclic_runs(shift_id_seq):
            ctxs.append({
                "_rule_id": "consecutive_run_length_violation",
                "employee_id": eid, "run_start_day": start, "run_length": length,
                "shift_id": sid, "min_consecutive": min_consec, "max_consecutive": max_consec,
            })

        for i in range(total_days):
            off_count = sum(
                1 for j in range(14) if flat[(i + j) % total_days]["shift_type"] == "off"
            )
            ctxs.append({
                "_rule_id": "days_off_window_violation",
                "employee_id": eid, "window_start_day": i,
                "off_count": off_count, "min_days_off_per_14": min_off_per_14,
            })

        for w in range(W):
            for d in range(7):
                ctxs.append({
                    "_rule_id": "employee_schedule_derivation_mismatch",
                    "employee_id": eid, "week": w, "day_of_week": d,
                    "actual_shift_id": emp["weeks"][w][d]["shift_id"],
                    "expected_shift_id": template_shift_ids[(w + k) % W][d],
                })

    return ctxs
