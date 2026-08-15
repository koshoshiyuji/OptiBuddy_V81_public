"""
car_sequencing_converter.py — Business DSL → Solver Input DSL (v1.0)

# solver_input keys:
#   problem_class, car_types, options, config, issue_statuses, meta
"""

from __future__ import annotations

from typing import Any, Dict, List


def convert_car_sequencing_to_solver(business_dsl: Dict[str, Any]) -> Dict[str, Any]:
    """
    CarSequencing Business DSL → Solver Input DSL

    Business DSL 構造:
      car_types: [{car_type_id, car_type_name, count, option_ids: []}]
      options:   [{option_id, option_name, max_per_window(p), window_size(q)}]
      config:    {max_consecutive_same, solve_time_sec, total_slots}
      meta:      {instance_name, note}
    """
    car_types_raw: List[Dict] = business_dsl.get("car_types", [])
    options_raw: List[Dict] = business_dsl.get("options", [])
    config: Dict = business_dsl.get("config", {})
    meta: Dict = business_dsl.get("meta", {})

    # --- 各オプションに「必要とする車種IDリスト」を付与 ---
    # Business DSLでは car_types[].option_ids から逆引きする
    option_to_car_types: Dict[str, List[str]] = {}
    for ct in car_types_raw:
        tid = ct.get("car_type_id", "")
        for oid in ct.get("option_ids", []):
            option_to_car_types.setdefault(oid, []).append(tid)

    car_types_out: List[Dict] = []
    for ct in car_types_raw:
        car_types_out.append({
            "car_type_id":   ct.get("car_type_id", ""),
            "car_type_name": ct.get("car_type_name", ct.get("car_type_id", "")),
            "count":         int(ct.get("count", 0)),
        })

    options_out: List[Dict] = []
    for opt in options_raw:
        oid = opt.get("option_id", "")
        options_out.append({
            "option_id":       oid,
            "option_name":     opt.get("option_name", oid),
            "max_per_window":  int(opt.get("max_per_window", 1)),
            "window_size":     int(opt.get("window_size", 1)),
            "car_type_ids":    option_to_car_types.get(oid, []),
        })

    # --- config 組み立て ---
    # total_slots は常に出力する（指定がない場合は None）
    raw_total_slots = config.get("total_slots", None)
    config_out: Dict[str, Any] = {
        "max_consecutive_same": int(config.get("max_consecutive_same", 2)),
        "solve_time_sec":       int(config.get("solve_time_sec", 30)),
        "total_slots":          int(raw_total_slots) if raw_total_slots is not None else None,
    }

    return {
        "problem_class":   "CarSequencing",
        "car_types":       car_types_out,
        "options":         options_out,
        "config":          config_out,
        "issue_statuses":  business_dsl.get("issue_statuses", {}),
        "meta":            meta,
    }