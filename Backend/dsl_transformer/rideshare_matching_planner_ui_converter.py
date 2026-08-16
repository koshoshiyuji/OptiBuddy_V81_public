"""
RideshareMatchingPlanner: Solver Output DSL → UI DSL

2026-08-16 修正: solver.py のCPモデル書き換え（デポ制約・実距離目的関数の追加）に
伴い、解の出力構造が matched_pairs / unmatched_passengers / driver_routes /
kpi（match_rateは0-100スケール、coverage_rateは0-1スケール）という命名に
変わっていたが、本ファイルは Stage2 生成当初の想定キー名
（assignments / driver_stats / coverage_rateをそのまま%表示 等）のまま
放置されており、実UIで「マッチング率 1.0%」「乗客マッチング一覧 0件」等の
表示崩れを起こしていた（動的検証は実ソルブのみでUI変換を経由しないため
これまで検出されなかった）。solver.py の実際の出力キーに合わせて全面的に
書き直した。
"""

from __future__ import annotations
from typing import Any, Dict, List, Optional


def convert_rideshare_matching_planner_to_ui(
    solver_output: Dict[str, Any],
    business_dsl: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    from dsl_transformer.table_sections import build_table_section, TableColumn
    from i18n.rideshare_matching_planner_messages import t

    feasible = solver_output.get("feasible", False)
    solutions = solver_output.get("solutions", [])
    sol = solutions[0] if solutions else {}

    matched_pairs: List[Dict] = sol.get("matched_pairs", [])
    unmatched_passengers: List[Dict] = sol.get("unmatched_passengers", [])
    driver_routes: List[Dict] = sol.get("driver_routes", [])
    kpi: Dict = sol.get("kpi", sol.get("metrics", {}))

    n_total = kpi.get("total_passengers", 0)
    n_assigned = kpi.get("matched_count", 0)
    n_unassigned = kpi.get("unmatched_count", 0)
    # match_rate は solver.py 側で既に 0〜100 のパーセント表記として計算済み
    # （coverage_rate は Gate2 動的検証用の 0.0〜1.0 fraction 表記で別物）。
    match_rate_pct = kpi.get("match_rate", 0.0)
    total_km = kpi.get("total_km", 0.0)
    n_drivers_active = kpi.get("driver_count", len(driver_routes))

    # ── KPI カード ──
    kpi_cards = [
        {
            "id": "coverage",
            "label": t("kpi.coverage.label"),
            "value": f"{match_rate_pct:.1f}%",
            "sub": t("kpi.coverage.sub", assigned=n_assigned, total=n_total),
            "color": "green" if match_rate_pct >= 80 else "orange" if match_rate_pct >= 50 else "red",
        },
        {
            "id": "n_unassigned",
            "label": t("kpi.unassigned.label"),
            "value": str(n_unassigned),
            "sub": t("kpi.unassigned.unit"),
            "color": "green" if n_unassigned == 0 else "orange" if n_unassigned <= 2 else "red",
        },
        {
            "id": "total_km",
            "label": t("kpi.total_km.label"),
            "value": f"{total_km:.1f}",
            "sub": t("kpi.total_km.unit"),
            "color": "teal",
        },
        {
            "id": "n_drivers",
            "label": t("kpi.n_drivers.label"),
            "value": str(n_drivers_active),
            "sub": t("kpi.n_drivers.unit"),
            "color": "blue",
        },
    ]

    # ── アラート ──
    alerts: List[Dict] = []
    if not feasible:
        alerts.append({
            "severity": "CRITICAL",
            "message": t("alert.infeasible"),
        })
    elif n_unassigned > 0:
        alerts.append({
            "severity": "WARNING",
            "message": t("alert.unassigned_exist", count=n_unassigned),
        })

    # ── 乗客一覧テーブル（matched_pairs + unmatched_passengers を統合） ──
    def _min_to_hhmm(m: Optional[int]) -> str:
        if m is None:
            return "—"
        m = int(m)
        return f"{m // 60:02d}:{m % 60:02d}"

    passenger_rows = []
    for mp in matched_pairs:
        passenger_rows.append({
            "passenger_id": mp.get("passenger_id"),
            "passenger_name": mp.get("passenger_name"),
            "status": t("passenger.status.assigned"),
            "driver_name": mp.get("driver_name") or mp.get("driver_id") or "—",
            "pickup_time": _min_to_hhmm(mp.get("pickup_min")),
            "dropoff_time": _min_to_hhmm(mp.get("dropoff_min")),
            "ride_time_min": mp.get("ride_min"),
            "direct_time_min": mp.get("direct_min"),
            "_severity": None,
        })
    for p in unmatched_passengers:
        passenger_rows.append({
            "passenger_id": p.get("id"),
            "passenger_name": p.get("name"),
            "status": t("passenger.status.unassigned"),
            "driver_name": "—",
            "pickup_time": "—",
            "dropoff_time": "—",
            "ride_time_min": None,
            "direct_time_min": p.get("direct_time_min"),
            "_severity": "WARNING",
        })

    passenger_section = build_table_section(
        section_id="passengers",
        title=t("table.section.passengers.title"),
        columns=[
            TableColumn(key="passenger_name", label=t("table.column.passenger_name")),
            TableColumn(key="status", label=t("table.column.status"), format="badge"),
            TableColumn(key="driver_name", label=t("table.column.driver_name")),
            TableColumn(key="pickup_time", label=t("table.column.pickup_time")),
            TableColumn(key="dropoff_time", label=t("table.column.dropoff_time")),
            TableColumn(key="ride_time_min", label=t("table.column.ride_time_min"), format="number", unit="分"),
            TableColumn(key="direct_time_min", label=t("table.column.direct_time_min"), format="number", unit="分"),
        ],
        rows=passenger_rows,
        row_id_key="passenger_id",
        severity_key="_severity",
    )

    # ── 運転手別ルートテーブル ──
    driver_rows = []
    for dr in driver_routes:
        route_passengers = dr.get("passengers", [])
        route_desc = " → ".join(rp.get("passenger_name", "") for rp in route_passengers)
        if route_passengers:
            last_event = max(
                max(rp.get("pickup_min", 0), rp.get("dropoff_min", 0))
                for rp in route_passengers
            )
            active_min = max(0, last_event - dr.get("depart_min", 0))
        else:
            active_min = 0
        driver_rows.append({
            "driver_id": dr.get("driver_id"),
            "driver_name": dr.get("driver_name"),
            "n_passengers": dr.get("passenger_count", len(route_passengers)),
            "travel_km": dr.get("total_km", 0.0),
            "active_min": active_min,
            "capacity": dr.get("seats", 0),
            "route": route_desc or "—",
        })

    driver_section = build_table_section(
        section_id="drivers",
        title=t("table.section.drivers.title"),
        columns=[
            TableColumn(key="driver_name", label=t("table.column.driver_name_col")),
            TableColumn(key="n_passengers", label=t("table.column.n_passengers"), format="number", unit="名"),
            TableColumn(key="capacity", label=t("table.column.capacity"), format="number", unit="席"),
            TableColumn(key="travel_km", label=t("table.column.travel_km"), format="number", unit="km"),
            TableColumn(key="active_min", label=t("table.column.active_min"), format="number", unit="分"),
            TableColumn(key="route", label=t("table.column.route")),
        ],
        rows=driver_rows,
        row_id_key="driver_id",
    )

    return {
        "domain": "rideshare_matching_planner",
        "feasible": feasible,
        "summary": {
            "n_passengers": n_total,
            "n_assigned": n_assigned,
            "n_unassigned": n_unassigned,
            "coverage_rate": kpi.get("coverage_rate", 0.0),
            "total_travel_km": total_km,
        },
        "kpi_cards": kpi_cards,
        "assignments": passenger_rows,
        "driver_stats": driver_rows,
        "table_sections": [passenger_section, driver_section],
        "alerts": alerts,
        "raw_kpi": kpi,
    }
