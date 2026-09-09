
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable, Dict, List, Optional, Tuple

from solvers.base.solution_checker import solver_bug_issue, sweep_peak_usage
from i18n.common_messages import t as _common_t
from i18n.nurse_shift_weekly_cap_messages import t as _nurse_shift_t

from ._common import IssueRule, _rule




# ---------------------------------------------------------------------------
# _TRUCK_DISPATCHER_RULES（2026-07-24 追加）
#
# 背景: truck_dispatcher_solver.py の _build_issues() が持っていた
# tw_overdue（TW超過）/duty_overtime（拘束時間超過）のアドホックな検知ロジックを
# 層A/B（run_issue_rules/ISSUE_RULES）パターンに揃えるための移植
# （DESIGN_2026-07-21 6節「既に持つドメインは差分管理の拡張に該当」）。
# 元の実装は「複数件をまとめて1つの集約issueにする」挙動だったため、
# フロントエンドの表示仕様を変えないよう、ここでも集約1件を返す構造を保つ
# （YardPlanningのような1件=1ペアの粒度にはしていない）。
#
# category="SOLVER"の使い分け（2026-07-24, 5節の運用規約）:
#   - duty_overtime: max_duty_minは車両ごとのCP Optimizerハード制約
#     （mdl.add(... <= max_duty)）。ここが返ってきた解で破られているのは
#     本来ゼロのはずのバグ（抽出/変換ミス）の疑いが強いため category="SOLVER"。
#   - tw_overdue: tw_close_minはソフト制約（目的関数のペナルティ項）であり、
#     超過はモデル上正常にありうる業務結果。バグの疑いではないため
#     category="SOLVER"は付与しない。
# ---------------------------------------------------------------------------

def _tw_overdue_message(overdue_stops: List[tuple]) -> str:
    overdue_stops = sorted(overdue_stops, key=lambda x: -x[1])
    worst = overdue_stops[0]
    names = ", ".join(f"{n}({m}分超過)" for n, m in overdue_stops[:5])
    return (
        f"指定時間帯を過ぎて到着する配送が{len(overdue_stops)}件あります"
        f"（最大{worst[1]}分超過: {worst[0]}）: {names}"
        f"{'...' if len(overdue_stops) > 5 else ''}。"
    )



def _duty_overtime_message(overtime: List[tuple]) -> str:
    names = ", ".join(f"{name}({int(duty)}分 > 上限{int(limit)}分)" for name, duty, limit in overtime)
    return f"以下の車両で拘束時間が各車両の上限を超過: {names}。"



_TRUCK_DISPATCHER_RULES: List[IssueRule] = [

    _rule(
        "tw_overdue",
        condition=lambda ctx: bool(ctx["overdue_stops"]),
        build=lambda ctx: {
            "id":                  "tw_overdue",
            "severity":            "CRITICAL",
            "title":               "タイムウィンドウ超過（遅刻）",
            "message":             _tw_overdue_message(ctx["overdue_stops"]),
            "relatedContainerIds": [],
        },
    ),

    _rule(
        "duty_overtime",
        condition=lambda ctx: bool(ctx["overtime"]),
        build=lambda ctx: solver_bug_issue(
            "duty_overtime",
            "ドライバー拘束時間超過",
            _duty_overtime_message(ctx["overtime"]),
        ),
    ),
]



# ---------------------------------------------------------------------------
# TruckDispatcher context ビルダー（2026-07-24 追加）
# ---------------------------------------------------------------------------

def build_truck_dispatcher_contexts(
    routes: List[Dict],
    max_duty_by_vehicle: Dict[str, int],
) -> List[Dict[str, Any]]:
    """
    TruckDispatcher 用の context を生成する。tw_overdue/duty_overtime いずれも
    「複数件をまとめて1件の集約issueにする」既存挙動を保つため、YardPlanningの
    ようなペア単位ではなく、ルール1つにつきcontext1件（集約データを内包）を返す。
    """
    overdue_stops = [
        (s["customer_name"], s["arrival_min"] - s["tw_close_min"])
        for r in routes for s in r["stops"]
        if s["arrival_min"] > s["tw_close_min"]
    ]
    overtime = [
        (r["vehicle_name"], r["duty_min"], max_duty_by_vehicle.get(r["vehicle_id"], 9999))
        for r in routes
        if r["duty_min"] > max_duty_by_vehicle.get(r["vehicle_id"], 9999)
    ]
    return [
        {"_rule_id": "tw_overdue",     "overdue_stops": overdue_stops},
        {"_rule_id": "duty_overtime",  "overtime": overtime},
    ]
