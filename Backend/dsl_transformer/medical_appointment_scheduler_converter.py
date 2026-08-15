"""
Backend/dsl_transformer/medical_appointment_scheduler_converter.py

Business DSL → Solver Input DSL 変換（MedicalAppointmentScheduler）

solver_input keys:
  - problem_class: "MedicalAppointmentScheduler"
  - requests: List[Dict]   受診依頼
  - resources: List[Dict]  医療資源
  - slots: List[Dict]      利用可能スロット（resource_id, day, slot_index, start_min, slot_duration_min, weekday, time_slot）
  - config: Dict
  - issue_statuses: Dict
"""

from __future__ import annotations

from typing import Any, Dict, List


def convert_medical_appointment_scheduler_to_solver(business_dsl: dict) -> dict:
    """Business DSL → Solver Input DSL"""

    requests = _build_requests(business_dsl.get("requests", []))
    resources = _build_resources(business_dsl.get("resources", []))
    slots = _build_slots(business_dsl.get("resources", []), business_dsl.get("config", {}))

    return {
        "problem_class": "MedicalAppointmentScheduler",
        "meta": business_dsl.get("meta", {}),
        "requests": requests,
        "resources": resources,
        "slots": slots,
        "config": _build_config(business_dsl.get("config", {})),
        "issue_statuses": business_dsl.get("issue_statuses", {}),
    }

def _build_requests(raw_requests: List[Dict]) -> List[Dict]:
    result = []
    for r in raw_requests:
        req_id = str(r.get("id", ""))
        # resource_type_needs: 必要な資源タイプのリスト（重複可: 例 ["cardio_doctor", "cardio_doctor", "ct_room"]）
        type_needs: List[str] = []
        for need in r.get("resource_type_needs", []):
            if isinstance(need, dict):
                rtype = str(need.get("resource_type", ""))
                count = int(need.get("count", 1))
                type_needs.extend([rtype] * count)
            else:
                type_needs.append(str(need))

        # duration_slots: 分単位をslot_duration_minで割ってスロット数に変換
        duration_min = int(r.get("duration_min", 30))

        result.append({
            "id": req_id,
            "patient_id": str(r.get("patient_id", req_id)),
            "patient_name": str(r.get("patient_name", f"患者{req_id}")),
            "resource_type_needs": type_needs,
            "duration_slots": max(1, duration_min // max(1, int(r.get("slot_duration_min", 30)))),
            "avoid_days": [str(d) for d in r.get("avoid_days", [])],
            "preferred_days": [str(d) for d in r.get("preferred_days", [])],
            "preferred_resource_ids": [str(rid) for rid in r.get("preferred_resource_ids", [])],
            "preferred_day_time_combinations": [
                {"weekday": str(dtc.get("weekday", "")), "time_slot": str(dtc.get("time_slot", ""))}
                for dtc in r.get("preferred_day_time_combinations", [])
            ],
        })
    return result

def _build_resources(raw_resources: List[Dict]) -> List[Dict]:
    result = []
    for r in raw_resources:
        result.append({
            "id": str(r.get("id", "")),
            "name": str(r.get("name", r.get("id", ""))),
            "resource_type": str(r.get("resource_type", "")),
        })
    return result


def _build_slots(raw_resources: List[Dict], config: Dict) -> List[Dict]:
    """
    各医療資源のカレンダーからスロット一覧を展開する。
    カレンダーが空の場合はconfig.default_calendarから生成する。
    """
    slots: List[Dict] = []
    default_cal: List[Dict] = config.get("default_calendar", [])
    slot_duration_min = int(config.get("slot_duration_min", 30))

    for r in raw_resources:
        rid = str(r.get("id", ""))
        cal = r.get("calendar", default_cal)
        for entry in cal:
            day = str(entry.get("day", ""))
            slot_index = int(entry.get("slot_index", 0))
            start_min = int(entry.get("start_min", 0))
            dur = int(entry.get("slot_duration_min", slot_duration_min))
            weekday = str(entry.get("weekday", ""))
            time_slot = str(entry.get("time_slot", ""))
            slots.append({
                "resource_id": rid,
                "day": day,
                "slot_index": slot_index,
                "start_min": start_min,
                "slot_duration_min": dur,
                "weekday": weekday,
                "time_slot": time_slot,
            })
    return slots

def _build_config(raw_config: Dict) -> Dict:
    return {
        "time_limit_sec": int(raw_config.get("time_limit_sec", 30)),
        "slot_duration_min": int(raw_config.get("slot_duration_min", 30)),
    }

