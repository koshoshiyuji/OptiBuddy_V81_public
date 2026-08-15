"""
medical_appointment_sequence_scheduler_converter.py

Business DSL → Solver Input DSL 変換

solver_input keys:
  - problem_class: str
  - sequences: List[Dict]
  - resources: List[Dict]
  - config: Dict
  - issue_statuses: Dict
"""

from __future__ import annotations

from typing import Any, Dict, List


def convert_medical_appointment_sequence_scheduler_to_solver(business_dsl: dict) -> dict:
    """
    Business DSL → Solver Input DSL

    Business DSL 想定フィールド:
      problem_class: "MedicalAppointmentSequenceScheduler"
      meta: {}
      sequences: [{
        id, patient_name,
        visits: [{id, required_resources: [{type, count}], duration_min, order}],
        interval_rules: [{from_visit, to_visit, min_gap, max_gap, unit}],
        same_resource_rules: [{visit_a, visit_b}],
        blackout_slots: [{start_min, end_min}],
      }]
      resources: [{id, name, type, available_slots: [{start_min, end_min}]}]
      config: {time_limit_sec, horizon_min}
    """
    sequences_raw = business_dsl.get("sequences", [])
    resources_raw = business_dsl.get("resources", [])
    config_raw = business_dsl.get("config", {})

    sequences = _normalize_sequences(sequences_raw)
    resources = _normalize_resources(resources_raw)
    config = _normalize_config(config_raw)

    return {
        "problem_class": "MedicalAppointmentSequenceScheduler",
        "meta": business_dsl.get("meta", {}),
        "sequences": sequences,
        "resources": resources,
        "config": config,
        "issue_statuses": business_dsl.get("issue_statuses", {}),
    }


def _normalize_sequences(sequences_raw: List[Dict]) -> List[Dict]:
    result = []
    for i, seq in enumerate(sequences_raw):
        seq_id = str(seq.get("id", f"seq_{i}"))
        visits_raw = seq.get("visits", [])
        visits = []
        for j, v in enumerate(visits_raw):
            v_id = str(v.get("id", f"{seq_id}_v{j}"))
            required_resources = [
                {"type": str(r.get("type", "")), "count": int(r.get("count", 1))}
                for r in v.get("required_resources", [])
            ]
            visits.append({
                "id": v_id,
                "required_resources": required_resources,
                "duration_min": int(v.get("duration_min", 60)),
                "order": int(v.get("order", j)),
            })

        interval_rules = []
        for rule in seq.get("interval_rules", []):
            interval_rules.append({
                "from_visit": rule.get("from_visit"),
                "to_visit": rule.get("to_visit"),
                "min_gap": int(rule.get("min_gap", 0)),
                "max_gap": int(rule["max_gap"]) if rule.get("max_gap") is not None else None,
                "unit": str(rule.get("unit", "min")),
            })

        same_resource_rules = [
            {"visit_a": r.get("visit_a"), "visit_b": r.get("visit_b")}
            for r in seq.get("same_resource_rules", [])
        ]

        blackout_slots = [
            {"start_min": int(bs.get("start_min", 0)), "end_min": int(bs.get("end_min", 0))}
            for bs in seq.get("blackout_slots", [])
        ]

        result.append({
            "id": seq_id,
            "patient_name": str(seq.get("patient_name", seq_id)),
            "visits": visits,
            "interval_rules": interval_rules,
            "same_resource_rules": same_resource_rules,
            "blackout_slots": blackout_slots,
        })
    return result


def _normalize_resources(resources_raw: List[Dict]) -> List[Dict]:
    result = []
    for r in resources_raw:
        rid = str(r.get("id", ""))
        slots = [
            {"start_min": int(s.get("start_min", 0)), "end_min": int(s.get("end_min", 0))}
            for s in r.get("available_slots", [])
        ]
        result.append({
            "id": rid,
            "name": str(r.get("name", rid)),
            "type": str(r.get("type", "")),
            "available_slots": slots,
        })
    return result


def _normalize_config(config_raw: Dict) -> Dict:
    return {
        "time_limit_sec": int(config_raw.get("time_limit_sec", 30)),
        "horizon_min": int(config_raw.get("horizon_min", 14 * 24 * 60)),
    }