"""
Backend/dsl_transformer/patient_transport_planner_converter.py

Business DSL → Solver Input DSL 変換

solver_input keys（PatientTransportPlannerSolver が参照するキー一覧）:
  - problem_class: str
  - requests: List[Dict]  送迎依頼リスト（正規化済み）
  - vehicles: List[Dict]  送迎車リスト（正規化済み）
  - config: Dict          設定
  - issue_statuses: Dict  イシューステータス
"""

from __future__ import annotations

from typing import Any, Dict, List


def convert_patient_transport_planner_to_solver(business_dsl: dict) -> dict:
    """
    Business DSL → Solver Input DSL 変換。
    """
    requests_raw: List[Dict] = business_dsl.get("requests", [])
    vehicles_raw: List[Dict] = business_dsl.get("vehicles", [])
    config_raw: Dict = business_dsl.get("config", {})
    issue_statuses: Dict = business_dsl.get("issue_statuses", {})

    requests = [_normalize_request(r) for r in requests_raw]
    vehicles = [_normalize_vehicle(v) for v in vehicles_raw]
    config = _normalize_config(config_raw)

    return {
        "problem_class": "PatientTransportPlanner",
        "requests": requests,
        "vehicles": vehicles,
        "config": config,
        "issue_statuses": issue_statuses,
    }

def _normalize_request(r: Dict[str, Any]) -> Dict[str, Any]:
    """送迎依頼の正規化。必須フィールドのデフォルト値を補完する。"""
    return {
        "id": str(r.get("id", "")),
        "patient_id": str(r.get("patient_id", r.get("id", ""))),
        "request_type": r.get("request_type", "roundtrip"),  # "outbound_only"/"inbound_only"/"roundtrip"
        "care_type": r.get("care_type", "general"),           # "general"/"wheelchair"/"stretcher"
        "capacity_required": int(r.get("capacity_required", 1)),
        "home_location": r.get("home_location", {"lat": 0.0, "lng": 0.0}),
        "appointment_time": str(r.get("appointment_time", "09:00")),
        "exam_duration_min": int(r.get("exam_duration_min", 60)),
        "wait_tolerance_min": int(r.get("wait_tolerance_min", 30)),
        "boarding_time_min": int(r.get("boarding_time_min", 3)),
        "outbound_vehicle": r.get("outbound_vehicle", None),
        "outbound_start": r.get("outbound_start", None),
        "outbound_end": r.get("outbound_end", None),
        "inbound_vehicle": r.get("inbound_vehicle", None),
        "inbound_start": r.get("inbound_start", None),
        "inbound_end": r.get("inbound_end", None),
    }


def _normalize_vehicle(v: Dict[str, Any]) -> Dict[str, Any]:
    """送迎車の正規化。"""
    return {
        "id": str(v.get("id", "")),
        "name": str(v.get("name", v.get("id", ""))),
        "capacity": int(v.get("capacity", 4)),
        "care_types": list(v.get("care_types", ["general"])),
        "start_time": str(v.get("start_time", "08:00")),
        "end_time": str(v.get("end_time", "18:00")),
    }


def _normalize_config(c: Dict[str, Any]) -> Dict[str, Any]:
    """設定の正規化。"""
    return {
        "speed_kmh": float(c.get("speed_kmh", 30.0)),
        "boarding_time_min": int(c.get("boarding_time_min", 3)),
        "solve_time_sec": int(c.get("solve_time_sec", 30)),
        "hospital_location": c.get("hospital_location", {"lat": 35.6895, "lng": 139.6917}),
    }