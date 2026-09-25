
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable, Dict, List, Optional, Tuple

from solvers.base.solution_checker import find_transit_shortfalls, solver_bug_issue, sweep_peak_usage
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



def _transit_shortfall_message(shortfalls: List[Dict[str, Any]]) -> str:
    worst = sorted(shortfalls, key=lambda x: -x["deficit_min"])
    parts = ", ".join(
        f"{s['vehicle_name']}: {s['from_label']}→{s['to_label']}"
        f"（必要{s['required_min']}分 / 間隔{s['available_min']}分）"
        for s in worst[:5]
    )
    return (
        f"同じ車両で、地点間の移動時間より短い間隔で次の地点に到着している区間が"
        f"{len(shortfalls)}件あります（最大{worst[0]['deficit_min']}分不足）: {parts}"
        f"{'...' if len(shortfalls) > 5 else ''}。"
        "移動時間行列付きのno_overlap（ハード制約）が返ってきた解で満たされておらず、"
        "解の抽出・変換または移動時間計算の不整合の疑いがあります。"
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
        "transit_shortfall",
        condition=lambda ctx: bool(ctx["shortfalls"]),
        build=lambda ctx: solver_bug_issue(
            "transit_shortfall",
            "地点間の移動時間不足（解チェッカー）",
            _transit_shortfall_message(ctx["shortfalls"]),
        ),
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

def _truck_travel_min_fn(dist_matrix: List[List[float]], config: Dict[str, Any]) -> Callable[[int, int], int]:
    """
    DSL宣言の値（dist_matrix・config の avg_speed_kmh / highway_speed_kmh /
    highway_threshold_km）から、solver と同じ式で移動時間(分)を計算し直す関数を返す。
    solver内部の travel_matrix は参照しない（解チェッカーの独立性、4節）。
    """
    avg_spd     = float(config.get("avg_speed_kmh", 30.0))
    highway_spd = float(config.get("highway_speed_kmh", 80.0))
    highway_thr = float(config.get("highway_threshold_km", 50.0))

    def travel_min(i: int, j: int) -> int:
        dist_km = dist_matrix[i][j]
        if dist_km > highway_thr and highway_spd > 0:
            return int(round((dist_km / highway_spd) * 60.0))
        return int(round((dist_km / avg_spd) * 60.0)) if avg_spd > 0 else 0

    return travel_min


def _truck_transit_shortfalls(
    routes: List[Dict],
    customers: List[Dict],
    dist_matrix: List[List[float]],
    config: Dict[str, Any],
) -> List[Dict[str, Any]]:
    """
    車両ごとに「拠点出発(depart_min) → 各訪問(arrival_min 〜 +service_time_min)
    → 拠点帰着(return_min)」のイベント列を作り、隣り合う区間の移動時間を検算する。
    地点は converter が付与した customers[]._loc_idx（拠点は0）で表す。
    許容誤差1分: フォールバック解（_CVRPFallbackEngine）は移動時間を丸めずに
    浮動小数で積算し到着時刻を切り捨てるため、round() で計算し直した値と
    最大1分程度ずれうる。この丸め差を違反とみなさない。
    """
    loc_by_customer = {c["id"]: c["_loc_idx"] for c in customers if "_loc_idx" in c}
    events_by_vehicle: Dict[str, List[Dict[str, Any]]] = {}
    names: Dict[str, str] = {}
    for r in routes:
        stops = r.get("stops") or []
        if not stops:
            continue
        vid = r["vehicle_id"]
        names[vid] = r.get("vehicle_name", vid)
        events = [{"label": "拠点出発", "loc": 0, "start": r["depart_min"], "end": r["depart_min"], "order": 0}]
        for s in stops:
            loc = loc_by_customer.get(s["customer_id"])
            if loc is None:
                continue
            start = s["arrival_min"]
            events.append({
                "label": s.get("customer_name", s["customer_id"]),
                "loc": loc,
                "start": start,
                "end": start + s.get("service_time_min", 0),
            })
        events.append({"label": "拠点帰着", "loc": 0, "start": r["return_min"], "end": r["return_min"], "order": 2})
        events_by_vehicle[vid] = events

    shortfalls = find_transit_shortfalls(
        events_by_vehicle, _truck_travel_min_fn(dist_matrix, config), tolerance_min=1,
    )
    for s in shortfalls:
        s["vehicle_name"] = names.get(s["resource_id"], s["resource_id"])
    return shortfalls


def build_truck_dispatcher_contexts(
    routes: List[Dict],
    max_duty_by_vehicle: Dict[str, int],
    *,
    customers: Optional[List[Dict]] = None,
    dist_matrix: Optional[List[List[float]]] = None,
    config: Optional[Dict[str, Any]] = None,
) -> List[Dict[str, Any]]:
    """
    TruckDispatcher 用の context を生成する。tw_overdue/duty_overtime いずれも
    「複数件をまとめて1件の集約issueにする」既存挙動を保つため、YardPlanningの
    ようなペア単位ではなく、ルール1つにつきcontext1件（集約データを内包）を返す。

    customers / dist_matrix が渡された場合は、移動時間の検算（transit_shortfall、
    2026-09-25追加）の context も生成する。
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
    contexts = [
        {"_rule_id": "tw_overdue",     "overdue_stops": overdue_stops},
        {"_rule_id": "duty_overtime",  "overtime": overtime},
    ]
    if customers is not None and dist_matrix:
        contexts.append({
            "_rule_id":   "transit_shortfall",
            "shortfalls": _truck_transit_shortfalls(routes, customers, dist_matrix, config or {}),
        })
    return contexts
