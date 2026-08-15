"""
Backend/dsl_transformer/patient_transport_planner_ui_converter.py

Solver Output DSL → UI DSL 変換
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional


def convert_patient_transport_planner_to_ui(
    solver_output: dict,
    business_dsl: Optional[dict] = None,
) -> dict:
    from dsl_transformer.table_sections import build_table_section, TableColumn

    feasible = solver_output.get("feasible", False)
    solutions = solver_output.get("solutions", [])
    issues = solver_output.get("issues", [])

    if not feasible or not solutions:
        return {
            "domain": "patient_transport_planner",
            "feasible": False,
            "summary": {},
            "kpi_cards": [],
            "request_results": [],
            "vehicle_schedules": [],
            "table_sections": [],
            "alerts": _build_alerts(issues),
            "raw_kpi": {},
        }

    sol = solutions[0]
    kpi = sol.get("kpi", {})
    request_results: List[Dict] = sol.get("request_results", [])
    vehicle_schedules: List[Dict] = sol.get("vehicle_schedules", [])
    unserved: List[Dict] = sol.get("unserved_requests", [])

    # ── KPIカード ──
    service_rate_pct = round(kpi.get("service_rate", 0.0) * 100, 1)
    kpi_cards = [
        {
            "label": "対応件数",
            "value": kpi.get("served_count", 0),
            "unit": "件",
            "color": "green",
        },
        {
            "label": "未対応件数",
            "value": kpi.get("unserved_count", 0),
            "unit": "件",
            "color": "red" if kpi.get("unserved_count", 0) > 0 else "green",
        },
        {
            "label": "対応率",
            "value": service_rate_pct,
            "unit": "%",
            "color": "green" if service_rate_pct >= 80 else "orange" if service_rate_pct >= 50 else "red",
        },
        {
            "label": "総乗車時間",
            "value": kpi.get("total_ride_time_min", 0),
            "unit": "分",
            "color": "blue",
        },
    ]

    # ── table_sections ──
    # 1. 依頼別結果テーブル
    request_rows = []
    for r in request_results:
        severity = None if r["served"] else "WARNING"
        row: Dict[str, Any] = {
            "request_id": r["request_id"],
            "patient_id": r.get("patient_id", r["request_id"]),
            "care_type": _care_type_label(r.get("care_type", "general")),
            "req_type": _req_type_label(r.get("req_type", "roundtrip")),
            "served": "✓ 対応済み" if r["served"] else "✗ 未対応",
            "severity": severity,
        }
        if r.get("outbound_vehicle"):
            row["outbound_vehicle"] = r["outbound_vehicle"]
            row["outbound_time"] = f"{_min_to_hhmm(r['outbound_start'])}〜{_min_to_hhmm(r['outbound_end'])}"
        else:
            row["outbound_vehicle"] = "—"
            row["outbound_time"] = "—"
        if r.get("inbound_vehicle"):
            row["inbound_vehicle"] = r["inbound_vehicle"]
            row["inbound_time"] = f"{_min_to_hhmm(r['inbound_start'])}〜{_min_to_hhmm(r['inbound_end'])}"
        else:
            row["inbound_vehicle"] = "—"
            row["inbound_time"] = "—"
        request_rows.append(row)

    request_table = build_table_section(
        section_id="request_results",
        title="依頼別対応結果",
        columns=[
            TableColumn(key="patient_id", label="患者ID"),
            TableColumn(key="care_type", label="対応区分"),
            TableColumn(key="req_type", label="種別"),
            TableColumn(key="served", label="対応状況", format="badge"),
            TableColumn(key="outbound_vehicle", label="往路車両"),
            TableColumn(key="outbound_time", label="往路時刻"),
            TableColumn(key="inbound_vehicle", label="復路車両"),
            TableColumn(key="inbound_time", label="復路時刻"),
        ],
        rows=request_rows,
        row_id_key="request_id",
        severity_key="severity",
    )

    # 2. 車両別スケジュールテーブル
    vehicle_rows = []
    for vs in vehicle_schedules:
        vehicle_rows.append({
            "vehicle_id": vs["vehicle_id"],
            "vehicle_name": vs.get("vehicle_name", vs["vehicle_id"]),
            "total_phases": vs.get("total_phases", 0),
            "schedule_summary": _build_schedule_summary(vs.get("phases", [])),
        })

    vehicle_table = build_table_section(
        section_id="vehicle_schedules",
        title="車両別スケジュール",
        columns=[
            TableColumn(key="vehicle_name", label="車両名"),
            TableColumn(key="total_phases", label="担当フェーズ数", format="number", align="right"),
            TableColumn(key="schedule_summary", label="スケジュール概要"),
        ],
        rows=vehicle_rows,
        row_id_key="vehicle_id",
    )

    # ── アラート（未対応依頼の緩和提案） ──
    alerts = _build_alerts(issues)
    if unserved:
        alerts.append({
            "severity": "WARNING",
            "title": "緩和案の提案",
            "message": (
                f"{len(unserved)} 件の依頼が対応できませんでした。"
                "以下の緩和をご検討ください: ①車両を1台増やす、"
                "②稼働時間帯を30分延長する（開始を早める・終了を遅らせる）、"
                "③許容待機時間を15分拡大する。"
            ),
        })

    return {
        "domain": "patient_transport_planner",
        "feasible": feasible,
        "summary": {
            "served_count": kpi.get("served_count", 0),
            "total_requests": kpi.get("total_requests", 0),
            "unserved_count": kpi.get("unserved_count", 0),
            "service_rate": kpi.get("service_rate", 0.0),
            "total_ride_time_min": kpi.get("total_ride_time_min", 0),
        },
        "kpi_cards": kpi_cards,
        "request_results": request_results,
        "vehicle_schedules": vehicle_schedules,
        "table_sections": [request_table, vehicle_table],
        "alerts": alerts,
        "raw_kpi": kpi,
    }


def _care_type_label(care_type: str) -> str:
    return {"general": "一般", "wheelchair": "車椅子", "stretcher": "ストレッチャー"}.get(care_type, care_type)


def _req_type_label(req_type: str) -> str:
    return {"roundtrip": "往復", "outbound_only": "往路のみ", "inbound_only": "復路のみ"}.get(req_type, req_type)


def _min_to_hhmm(minutes: int) -> str:
    minutes = int(minutes)
    h = minutes // 60
    m = minutes % 60
    return f"{h:02d}:{m:02d}"


def _build_schedule_summary(phases: List[Dict]) -> str:
    if not phases:
        return "担当なし"
    parts = []
    for p in phases:
        pid = p.get("phase_id", "")
        s = _min_to_hhmm(p.get("start", 0))
        e = _min_to_hhmm(p.get("end", 0))
        parts.append(f"{pid}({s}〜{e})")
    return " / ".join(parts[:5]) + ("..." if len(parts) > 5 else "")


def _build_alerts(issues: List[Dict]) -> List[Dict]:
    alerts = []
    for issue in issues:
        if issue.get("severity") in ("CRITICAL", "WARNING"):
            alerts.append({
                "severity": issue["severity"],
                "title": issue.get("title", ""),
                "message": issue.get("message", ""),
            })
    return alerts