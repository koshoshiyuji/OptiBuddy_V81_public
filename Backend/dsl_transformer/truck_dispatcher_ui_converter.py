"""
dsl_transformer/cvrp_ui_converter.py

Solver Output DSL → UI DSL 変換（CVRP-TW）

Solver Output DSL（cvrp_solver.py が返す形式）:
  status, metadata, solutions[{feasible, routes, kpi, summary}], issues

UI DSL（フロントエンドが消費する形式）:
  domain      : "capacitated_vehicle_routing_problem"
  summary     : KPIカード用サマリー
  routes      : 車両別ルート（タイムライン表示用）
  map_points  : 地図表示用座標リスト
  alerts      : Human-in-the-Loop アラート（ソフト制約破断）
  kpi_cards   : KPIカードリスト
"""

from __future__ import annotations

import logging

from dsl_transformer.table_sections import build_table_section, TableColumn

logger = logging.getLogger(__name__)


def convert_truck_dispatcher_to_ui(solver_output: dict, business_dsl: dict | None = None) -> dict:
    """
    Solver Output DSL → UI DSL 変換。
    solver_to_ui.py から problem_class で振り分けて呼ばれる。

    business_dsl が渡された場合、顧客の lat/lon を map_points に含める。
    """
    solutions = solver_output.get("solutions", [])
    if not solutions:
        return _empty_ui_dsl()

    sol      = solutions[0]
    routes   = sol.get("routes",  [])
    kpi      = sol.get("kpi",     {})
    summary  = sol.get("summary", {})
    unserved = sol.get("unserved_customers", [])
    unserved_reasons = sol.get("unserved_reasons", {})

    # --- KPI カード ---
    kpi_cards = _build_kpi_cards(kpi, summary, unserved)

    # --- ルート（タイムライン表示用）---
    ui_routes = _build_ui_routes(routes)

    # --- 地図ポイント ---
    map_points = _build_map_points(routes, business_dsl)

    # --- アラート（Human-in-the-Loop）---
    alerts = _build_alerts(solver_output.get("issues", []))

    # --- 詳細テーブル（V8.6: table_sections）---
    # feasibleの値にかかわらず、実際に割り当てられた分は常に表示する（
    # 以前はtable_sectionsを一切生成しておらず、feasible/infeasibleを問わず
    # 配送ルートの一覧が一切表示されない不具合があった）。
    table_sections = _build_table_sections(routes, unserved, business_dsl, unserved_reasons)

    # --- gantt_tasks（Generic4DSLView の汎用Gantt表示用）---
    # 2026-07-14追加: NurseShiftWeeklyCapと同じ Frontend/src/domain/types.ts の
    # GanttTask 形式で出力する。table_sections同様、feasibleの値にかかわらず
    # 実際に割り当てられた分（一部未割当のinfeasibleなプランでも）は表示する。
    # 1台の車両 = 1レーン、1停車（顧客への訪問・荷役）= 1バーとして、
    # 到着時刻(arrival_min)〜到着+荷役時間(service_time_min)の区間を表す。
    gantt_tasks = _build_gantt_tasks(ui_routes)
    gantt_makespan = max((r.get("return_min", 0) for r in ui_routes), default=0) * 60

    return {
        "domain":     "truck_dispatcher",
        "feasible":   sol.get("feasible", False),
        "summary":    summary,
        "kpi_cards":  kpi_cards,
        "routes":     ui_routes,
        "map_points": map_points,
        "alerts":     alerts,
        "table_sections": table_sections,
        "raw_kpi":    kpi,
        "unserved_customers": unserved,
        "gantt_tasks":    gantt_tasks,
        "gantt_makespan": gantt_makespan,
    }



# ---------------------------------------------------------------------------
# KPI カード
# ---------------------------------------------------------------------------

def _build_kpi_cards(kpi: dict, summary: dict, unserved: list[str] | None = None) -> list[dict]:
    n_total = summary.get("n_vehicles_total", 0)
    n_used  = kpi.get("n_vehicles_used",  0)
    saved   = n_total - n_used if n_total > 0 else 0
    n_unserved = len(unserved) if unserved else 0

    cards = [
        {
            "id":      "vehicles_used",
            "label":   "稼働車両台数",
            "value":   f"{n_used}台",
            "sub":     f"削減: {saved}台" if saved > 0 else f"最大{n_total}台中",
            "color":   "blue",
            "icon":    "truck",
        },
        {
            "id":      "total_stops",
            "label":   "総配送件数",
            "value":   f"{kpi.get('n_stops_total', 0)}件",
            # 未割り当てが実際にある場合のみ「未割り当てあり」を表示する。
            # 従来はn_stops_totalが1件でもあれば無条件で「全件割り当て完了」と
            # 表示していたため、一部未割当（infeasible）のPlanでも
            # 誤って「全件割り当て完了」と出てしまうバグがあった。
            "sub":     f"{n_unserved}件未割り当て" if n_unserved > 0 else "全件割り当て完了",
            "color":   "green",
            "icon":    "package",
        },

        {
            "id":      "total_dist",
            "label":   "総走行距離",
            "value":   f"{kpi.get('total_dist_km', 0):.1f} km",
            "sub":     f"平均 {kpi.get('avg_dist_per_veh', 0):.1f} km / 台",
            "color":   "orange",
            "icon":    "map",
        },
        {
            "id":      "avg_utilization",
            "label":   "平均積載率",
            "value":   f"{kpi.get('avg_util_pct', 0):.1f}%",
            "sub":     f"最大積載: {kpi.get('max_load_kg', 0):,} kg",
            "color":   "purple",
            "icon":    "weight",
        },
        {
            "id":      "duty_balance",
            "label":   "拘束時間格差",
            "value":   f"{kpi.get('duty_imbalance_min', 0)}分",
            "sub":     f"最大 {_min_to_hhmm(kpi.get('max_duty_min', 0))} / 最小 {_min_to_hhmm(kpi.get('min_duty_min', 0))}",
            "color":   "teal",
            "icon":    "clock",
        },
    ]
    return cards


# ---------------------------------------------------------------------------
# ルート（タイムライン）
# ---------------------------------------------------------------------------
def _build_ui_routes(routes: list[dict]) -> list[dict]:
    ui_routes = []
    for r in routes:
        stops = r.get("stops", [])
        ui_stops = []
        for s in stops:
            slack = s.get("tw_close_min", 1200) - (s.get("arrival_min", 0) + s.get("service_time_min", 15))
            ui_stops.append({
                "sequence":         s["sequence"],
                "customer_id":      s["customer_id"],
                "customer_name":    s["customer_name"],
                "arrival_time":     s.get("arrival_time", ""),
                "arrival_min":      s.get("arrival_min", 0),
                "tw_open_str":      s.get("tw_open_str", ""),
                "tw_close_str":     s.get("tw_close_str", ""),
                "tw_open_min":      s.get("tw_open_min", 360),
                "tw_close_min":     s.get("tw_close_min", 1200),
                "demand_kg":        s.get("demand_kg", 0),
                "service_time_min": s.get("service_time_min", 15),
                "seg_dist_km":      s.get("seg_dist_km", 0),
                "tw_slack_min":     slack,
                "tw_tight":         0 <= slack < 20,
                # 積み付け制約フィールド
                "stackable":        s.get("stackable", True),
                "cargo_shape":      s.get("cargo_shape", ""),
                "stack_limit":      s.get("stack_limit", 0),
                "stackability_note": _build_stackability_note(s),
            })

        ui_routes.append({
            "vehicle_id":      r["vehicle_id"],
            "vehicle_name":    r["vehicle_name"],
            "vehicle_type":    r.get("vehicle_type", ""),
            "capacity_kg":     r.get("capacity_kg", 0),
            "load_kg":         r.get("load_kg", 0),
            "utilization_pct": r.get("utilization_pct", 0),
            "n_stops":         r.get("n_stops", 0),
            "depart_time":     r.get("depart_time", ""),
            "return_time":     r.get("return_time", ""),
            "depart_min":      r.get("depart_min", 0),
            "return_min":      r.get("return_min", 0),
            "duty_min":        r.get("duty_min", 0),
            "duty_str":        _min_to_hhmm(r.get("duty_min", 0)),
            "route_dist_km":   r.get("route_dist_km", 0),
            "stops":           ui_stops,
        })
    return ui_routes


# ---------------------------------------------------------------------------
# Gantt（Generic4DSLViewの汎用Gantt表示用）
# ---------------------------------------------------------------------------

def _risk_level_from_slack(slack_min) -> str:
    """
    tw_slack_min（到着+荷役完了が指定時間帯の締切に対してどれだけ余裕があるか、分）
    から Gantt バーの意味付きリスクレベルを決める（2026-07-14、Koshoshiとの相談で
    「配送先ごとの色分け」より「時間指定の余裕度」の方が有用と判断し変更）。
      - critical: slack < 0（締切超過＝時間指定違反。本来はfeasibleな解では
        起きないはずだが、ソフト化された探索や境界ケースの防御として扱う）
      - warning : 0 <= slack < 20（_build_ui_routes()のtw_tightと同じ閾値）
      - ok      : それ以外（余裕あり）
    """
    if slack_min is None:
        return "ok"
    if slack_min < 0:
        return "critical"
    if slack_min < 20:
        return "warning"
    return "ok"


def _build_gantt_tasks(ui_routes: list[dict]) -> list[dict]:
    """
    _build_ui_routes() が既に正規化した ui_routes を、Frontend/src/domain/types.ts
    の GanttTask 形式に変換する。YARD固有のcontainerId/operation/yard/ship/ports
    等は含めない（nurse_shift_weekly_cap_ui_converter.pyと同じ方針）。
    1台の車両（vehicle_id）= 1レーン、1停車 = 1バー（到着〜到着+荷役時間）。
    色は配送先ごと（colorKey）ではなく、時間指定の余裕度（riskLevel）で
    意味付けする。配送先数が多いドメインではcolorKeyの6色使い回しに実質
    意味が無く、時間的リスクを一目で拾えることの方が運用上有用なため。
    """
    gantt_tasks = []
    for r in ui_routes:
        vehicle_id = r.get("vehicle_id", "")
        vehicle_name = r.get("vehicle_name", vehicle_id)
        for s in r.get("stops", []):
            customer_id = s.get("customer_id", "")
            customer_name = s.get("customer_name", customer_id)
            arrival_min = s.get("arrival_min", 0)
            service_min = s.get("service_time_min", 15)
            gantt_tasks.append({
                "id":            f"{vehicle_id}_{customer_id}_{s.get('sequence', 0)}",
                "resourceId":    vehicle_id,
                "resourceLabel": vehicle_name,
                "label":         customer_name,
                "colorKey":      customer_id,
                "riskLevel":     _risk_level_from_slack(s.get("tw_slack_min")),
                "start":         int(arrival_min) * 60,
                "end":           int(arrival_min + service_min) * 60,
                "status":        "scheduled",
            })
    return gantt_tasks


# ---------------------------------------------------------------------------
# 地図ポイント
# ---------------------------------------------------------------------------

def _build_map_points(routes: list[dict], business_dsl: dict | None) -> list[dict]:
    """
    ルートに登場する顧客の座標を地図表示用に変換する。
    business_dsl が渡されていれば depot 座標も含める。
    """
    points: list[dict] = []

    # depot
    if business_dsl:
        depot = business_dsl.get("depot", {})
        if depot.get("lat") and depot.get("lon"):
            points.append({
                "id":   depot.get("id", "depot"),
                "name": depot.get("name", "倉庫"),
                "lat":  depot["lat"],
                "lon":  depot["lon"],
                "type": "depot",
                "vehicle_ids": [],
            })

    # 顧客（ルートに登場するもの）
    # business_dsl から lat/lon を取得
    cust_map: dict[str, dict] = {}
    if business_dsl:
        for c in business_dsl.get("customers", []):
            cust_map[c["id"]] = c

    # ルートに登場する順序で追加（重複除去）
    added_ids: set[str] = set()
    for r in routes:
        for s in r.get("stops", []):
            cid = s["customer_id"]
            if cid in added_ids:
                continue
            added_ids.add(cid)
            c = cust_map.get(cid, {})
            if c.get("lat") and c.get("lon"):
                points.append({
                    "id":           cid,
                    "name":         s["customer_name"],
                    "lat":          c["lat"],
                    "lon":          c["lon"],
                    "type":         "customer",
                    "vehicle_id":   r["vehicle_id"],
                    "vehicle_name": r["vehicle_name"],
                    "sequence":     s["sequence"],
                    "demand_kg":    s.get("demand_kg", 0),
                    "arrival_time": s.get("arrival_time", ""),
                })

    return points


# ---------------------------------------------------------------------------
# アラート（Human-in-the-Loop）
# ---------------------------------------------------------------------------

def _build_alerts(issues: list[dict]) -> list[dict]:
    """
    WARNING / CRITICAL なイシューをアラートに変換する。
    INFO は除外（KPI カードで表現済み）。
    """
    alerts = []
    for issue in issues:
        sev = issue.get("severity", "INFO")
        if sev == "INFO":
            continue
        alerts.append({
            "id":       issue.get("id", ""),
            "severity": sev,
            "title":    issue.get("title", ""),
            "message":  issue.get("message", ""),
        })
    return alerts


# ---------------------------------------------------------------------------
# 詳細テーブル（table_sections）
# ---------------------------------------------------------------------------

def _build_table_sections(routes: list[dict], unserved: list[str], business_dsl: dict | None,
                           unserved_reasons: dict[str, dict] | None = None) -> list[dict]:
    """
    実際に割り当てられた配送ルート一覧を「配送ルート明細」テーブルとして常に生成する。
    一部未割当（infeasible）の場合でも、割り当てられた分はこのテーブルで確認できる。
    未割当顧客があれば、別途「未割当一覧」テーブルも追加する（2026-07-08、Koshoshiとの
    相談③: 未割当の理由内訳をここで明示し、「DSL変更が必要か／再探索で解決しうるか」を
    行ごとに区別できるようにする）。
    """
    sections: list[dict] = []

    route_rows = []
    for r in routes:
        for s in r.get("stops", []):
            route_rows.append({
                "row_key":         f"{r['vehicle_id']}_{s['customer_id']}",
                "sequence":        s.get("sequence", 0),
                "vehicle_name":    r.get("vehicle_name", r.get("vehicle_id", "")),
                "customer_name":   s.get("customer_name", s.get("customer_id", "")),
                "arrival_time":    s.get("arrival_time", ""),
                "tw_window":       f"{s.get('tw_open_str','')}～{s.get('tw_close_str','')}",
                "demand_kg":       s.get("demand_kg", 0),
                "seg_dist_km":     s.get("seg_dist_km", 0),
            })

    if route_rows:
        sections.append(build_table_section(
            section_id="routes",
            title="配送ルート明細",
            columns=[
                TableColumn(key="vehicle_name",  label="車両"),
                TableColumn(key="sequence",      label="便", align="right"),
                TableColumn(key="customer_name", label="配送先"),
                TableColumn(key="arrival_time",  label="到着予定"),
                TableColumn(key="tw_window",     label="指定時間帯"),
                TableColumn(key="demand_kg",     label="需要", format="number", unit="kg"),
                TableColumn(key="seg_dist_km",   label="区間距離", format="number", unit="km"),
            ],
            rows=route_rows,
            row_id_key="row_key",
        ))

    if unserved:
        cust_by_id: dict[str, dict] = {}
        if business_dsl:
            cust_by_id = {c["id"]: c for c in business_dsl.get("customers", [])}
        unserved_reasons = unserved_reasons or {}
        unserved_rows = []
        for cid in unserved:
            reason_info = unserved_reasons.get(cid, {})
            reason_label, row_severity = _unserved_reason_meta(reason_info.get("reason", "unknown"))
            unserved_rows.append({
                "customer_id":     cid,
                "customer_name":   cust_by_id.get(cid, {}).get("name", cid),
                "demand_kg":       cust_by_id.get(cid, {}).get("demand_kg", 0),
                "reason_label":    reason_label,
                "detail":          reason_info.get("detail", ""),
                "_row_severity":   row_severity,
            })
        sections.append(build_table_section(
            section_id="unserved",
            title=f"未割当一覧（{len(unserved)}件）— 理由の内訳",
            columns=[
                TableColumn(key="customer_name", label="配送先"),
                TableColumn(key="demand_kg",     label="需要", format="number", unit="kg"),
                TableColumn(key="reason_label",  label="未割当理由"),
                TableColumn(key="detail",        label="詳細"),
            ],
            rows=unserved_rows,
            row_id_key="customer_id",
            severity_key="_row_severity",
        ))

    return sections


# ---------------------------------------------------------------------------
# ユーティリティ
# ---------------------------------------------------------------------------

def _empty_ui_dsl() -> dict:
    return {
        "domain": "truck_dispatcher",
        "feasible": False,
        "summary": {}, "kpi_cards": [], "routes": [],
        "map_points": [], "alerts": [], "table_sections": [], "raw_kpi": {},
    }


def _min_to_hhmm(minutes: int) -> str:
    h = int(minutes) // 60
    m = int(minutes) % 60
    return f"{h:02d}:{m:02d}"


def _unserved_reason_meta(reason: str) -> tuple[str, str]:
    """
    truck_dispatcher_solver.py の _classify_unserved() が返す reason 値を、
    テーブル表示用の (日本語ラベル, 行severity) に変換する。

    severity の使い分け（2026-07-08、Koshoshiとの相談③）:
      - CRITICAL: DSLの変更（容量・拘束時間上限・車格制限の緩和）なしでは
        原理的に解決不可能。制約見直しタブでの緩和案検討が必要。
      - WARNING : 単体では条件を満たせるはずで、再探索（LNS反復増加等）で
        解決しうる可能性がある。
    """
    meta = {
        "capacity_infeasible":  ("容量超過",     "CRITICAL"),
        "duty_infeasible":      ("拘束時間超過", "CRITICAL"),
        "no_eligible_vehicle":  ("車格制限",     "CRITICAL"),
        "unresolved_by_search": ("探索未達",     "WARNING"),
    }
    return meta.get(reason, ("不明", "CRITICAL"))


def _build_stackability_note(stop: dict) -> str:
    """
    ストップの積み付け制約を人間が読めるメモ文字列に変換する。
    フロントエンドのツールチップ・アイコン表示に使用する。
    """
    parts: list[str] = []
    if not stop.get("stackable", True):
        parts.append("段積み不可")
    stack_limit = stop.get("stack_limit", 0)
    if stack_limit and stack_limit > 0:
        parts.append(f"上積み{stack_limit}段まで")
    cargo_shape = stop.get("cargo_shape", "").strip()
    if cargo_shape:
        parts.append(f"形状: {cargo_shape}")
    return " / ".join(parts) if parts else ""
