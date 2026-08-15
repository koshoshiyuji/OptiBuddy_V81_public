"""
store_site_converter.py — StoreSite Business DSL → Solver Input DSL

solver_input keys:
  problem_class, areas, candidates, config, meta, issue_statuses
"""

from typing import Any, Dict


def convert_store_site_to_solver(business_dsl: Dict[str, Any]) -> Dict[str, Any]:
    """
    Business DSL → Solver Input DSL

    Business DSL 構造:
      areas:      [{id, name, demand, lat?, lng?}, ...]
      candidates: [{id, name, fixed_cost, capacity, lat?, lng?}, ...]
      config:     {max_distance_km?, unmet_penalty?, store_count_weight?,
                   utilization_penalty_weight?, distance_penalty_weight?,
                   utilization_variance_weight?, utilization_threshold?,
                   supply_cost_per_km?,
                   distance_matrix?: {area_id: {cand_id: km}},
                   supply_cost_matrix?: {area_id: {cand_id: cost}},
                   time_limit_sec?}
      meta:       {instance_name?, note?}  ← UIパススルー用
    """
    areas      = business_dsl.get("areas", [])
    candidates = business_dsl.get("candidates", [])
    config_in  = business_dsl.get("config", {})
    meta_in    = business_dsl.get("meta", {})

    # 数値の型保証
    norm_areas = []
    for a in areas:
        norm_areas.append({
            "id":     str(a["id"]),
            "name":   a.get("name", str(a["id"])),
            "demand": float(a.get("demand", 0)),
            "lat":    float(a.get("lat", 0)),
            "lng":    float(a.get("lng", 0)),
        })

    norm_candidates = []
    for c in candidates:
        norm_candidates.append({
            "id":         str(c["id"]),
            "name":       c.get("name", str(c["id"])),
            "fixed_cost": float(c.get("fixed_cost", 0)),
            "capacity":   float(c.get("capacity", 1e9)),
            "lat":        float(c.get("lat", 0)),
            "lng":        float(c.get("lng", 0)),
        })

    config_out = {
        "max_distance_km":              float(config_in.get("max_distance_km", 1e9)),
        "unmet_penalty":                float(config_in.get("unmet_penalty", 1_000_000)),
        "store_count_weight":           float(config_in.get("store_count_weight", 10)),
        "utilization_penalty_weight":   float(config_in.get("utilization_penalty_weight", 5_000)),
        "utilization_variance_weight":  float(config_in.get("utilization_variance_weight", 1_000)),
        "distance_penalty_weight":      float(config_in.get("distance_penalty_weight", 100)),
        "utilization_threshold":        float(config_in.get("utilization_threshold", 0.8)),
        "supply_cost_per_km":           float(config_in.get("supply_cost_per_km", 10)),
        "time_limit_sec":               int(config_in.get("time_limit_sec", 30)),
        "distance_matrix":              config_in.get("distance_matrix", {}),
        "supply_cost_matrix":           config_in.get("supply_cost_matrix", {}),
    }

    # meta: UIパススルー用（instance_name / note）
    meta_out = {
        "instance_name": meta_in.get("instance_name", ""),
        "note":          meta_in.get("note", ""),
    }

    return {
        "problem_class":  "StoreSite",
        "areas":          norm_areas,
        "candidates":     norm_candidates,
        "config":         config_out,
        "meta":           meta_out,
        "issue_statuses": business_dsl.get("issue_statuses", {}),
    }
