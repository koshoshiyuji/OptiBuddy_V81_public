"""
RideshareMatchingPlanner Business DSL → Solver Input DSL 変換

solver_input keys:
  passengers, drivers, config, dist_matrix, locations, issue_statuses
"""

import math
from typing import Any, Dict, List


def convert_rideshare_matching_planner_to_solver(business_dsl: Dict[str, Any]) -> Dict[str, Any]:
    """Business DSL → Solver Input DSL"""

    passengers_raw = business_dsl.get("passengers", [])
    drivers_raw = business_dsl.get("drivers", [])
    config_raw = business_dsl.get("config", {})
    issue_statuses = business_dsl.get("issue_statuses", {})

    # ロケーション一覧の構築（重複排除）
    loc_ids_ordered: List[str] = []
    loc_set = set()

    def _add_loc(loc_id: str):
        if loc_id and loc_id not in loc_set:
            loc_ids_ordered.append(loc_id)
            loc_set.add(loc_id)

    for p in passengers_raw:
        _add_loc(p.get("pickup_loc", ""))
        _add_loc(p.get("dropoff_loc", ""))
    for d in drivers_raw:
        _add_loc(d.get("start_loc", ""))
        _add_loc(d.get("end_loc", ""))

    locations = [{"id": lid} for lid in loc_ids_ordered]
    loc_idx_map = {lid: i for i, lid in enumerate(loc_ids_ordered)}

    # 距離行列の構築（lat/lon がある場合はhaversine計算、なければ提供値を使用）
    n_loc = len(locations)
    dist_matrix = [[0.0] * n_loc for _ in range(n_loc)]

    # ロケーション座標マップ（業務DSLに座標がある場合）
    coord_map: Dict[str, Dict] = {}
    for p in passengers_raw:
        for loc_key in ("pickup_loc", "dropoff_loc"):
            lid = p.get(loc_key, "")
            coord_key = loc_key + "_coord"
            coord = p.get(coord_key)
            if lid and coord:
                coord_map[lid] = coord
    for d in drivers_raw:
        for loc_key in ("start_loc", "end_loc"):
            lid = d.get(loc_key, "")
            coord_key = loc_key + "_coord"
            coord = d.get(coord_key)
            if lid and coord:
                coord_map[lid] = coord

    # 距離行列の計算（座標がある場合はhaversine、なければ提供済みdist_matrixを使用）
    provided_dist = business_dsl.get("dist_matrix", [])
    if provided_dist:
        # 提供済みの距離行列を使用（インデックスはloc_ids_orderedの順）
        for i in range(min(n_loc, len(provided_dist))):
            for j in range(min(n_loc, len(provided_dist[i]))):
                dist_matrix[i][j] = float(provided_dist[i][j])
    elif coord_map:
        for i, lid_i in enumerate(loc_ids_ordered):
            for j, lid_j in enumerate(loc_ids_ordered):
                if i == j:
                    dist_matrix[i][j] = 0.0
                elif lid_i in coord_map and lid_j in coord_map:
                    dist_matrix[i][j] = _haversine(coord_map[lid_i], coord_map[lid_j])
                else:
                    dist_matrix[i][j] = 0.0

    # 乗客の変換
    passengers = []
    for p in passengers_raw:
        passengers.append({
            "id": str(p.get("id", "")),
            "name": str(p.get("name", p.get("id", ""))),
            "pickup_loc": str(p.get("pickup_loc", "")),
            "dropoff_loc": str(p.get("dropoff_loc", "")),
            "window_start_min": _hhmm_to_min(p.get("window_start", "00:00")),
            "window_end_min": _hhmm_to_min(p.get("window_end", "23:59")),
        })

    # 運転手の変換
    drivers = []
    for d in drivers_raw:
        drivers.append({
            "id": str(d.get("id", "")),
            "name": str(d.get("name", d.get("id", ""))),
            "start_loc": str(d.get("start_loc", "")),
            "end_loc": str(d.get("end_loc", "")),
            "seats": int(d.get("seats", 4)),
            "depart_min": _hhmm_to_min(d.get("depart_time", "00:00")),
            "arrive_max": _hhmm_to_min(d.get("arrive_time", "23:59")),
        })

    config = {
        "time_limit_sec": int(config_raw.get("time_limit_sec", 60)),
        "detour_factor": float(config_raw.get("detour_factor", 1.5)),
        "horizon_min": int(config_raw.get("horizon_min", 1440)),
        "speed_kmh": float(config_raw.get("speed_kmh", 0)),
    }

    return {
        "problem_class": "RideshareMatchingPlanner",
        "passengers": passengers,
        "drivers": drivers,
        "config": config,
        "dist_matrix": dist_matrix,
        "locations": locations,
        "issue_statuses": issue_statuses,
    }


def _hhmm_to_min(hhmm: str) -> int:
    """HH:MM 形式を分単位整数に変換"""
    if not hhmm:
        return 0
    try:
        parts = str(hhmm).split(":")
        h = int(parts[0])
        m = int(parts[1]) if len(parts) > 1 else 0
        return h * 60 + m
    except Exception:
        return 0


def _haversine(coord_a: Dict, coord_b: Dict) -> float:
    """lat/lon からhaversine距離（km）を計算"""
    R = 6371.0
    lat1 = math.radians(float(coord_a.get("lat", 0)))
    lon1 = math.radians(float(coord_a.get("lon", 0)))
    lat2 = math.radians(float(coord_b.get("lat", 0)))
    lon2 = math.radians(float(coord_b.get("lon", 0)))
    dlat = lat2 - lat1
    dlon = lon2 - lon1
    a = math.sin(dlat / 2) ** 2 + math.cos(lat1) * math.cos(lat2) * math.sin(dlon / 2) ** 2
    return R * 2 * math.asin(math.sqrt(a))
