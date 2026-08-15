"""
dsl_transformer/truck_dispatcher_converter.py

Business DSL → Solver Input DSL 変換（TruckDispatcher: CVRP-TW）

Business DSL スキーマ:
  problem_class : "TruckDispatcher"
  domain        : "truck_dispatcher"
  meta          : { instance_name, date, note }
  depot         : { id, name, lat, lon, open_time, close_time }
  vehicles      : [ { id, name, type, capacity_kg,
                       max_duty_min, max_continuous_drive_min,
                       preferred_customer_ids } ]
  customers     : [ { id, name, lat, lon, demand_kg,
                       tw_open, tw_close, service_time_min,
                       restricted_vehicle_types } ]
  config        : { avg_speed_kmh, highway_threshold_km,
                    objective_priority, solver_time_limit_sec }

Solver Input DSL スキーマ（cvrp_solver.py が受け取る形式）:
  problem_class : "TruckDispatcher"
  meta          : そのまま転写
  locations     : [ { id, name, lat, lon } ]   # index 0 = depot
  vehicles      : [ { id, name, type, capacity_kg,
                       max_duty_min, max_continuous_drive_min,
                       preferred_customer_ids } ]
  customers     : [ { id, name, lat, lon, demand_kg,
                       tw_open_min, tw_close_min, service_time_min,
                       restricted_vehicle_types,
                       _loc_idx } ]             # 距離行列インデックス
  dist_matrix   : list[list[float]]            # km、対称行列
  config        : { avg_speed_kmh, highway_threshold_km,
                    solver_time_limit_sec, depot_open_min, depot_close_min,
                    objective_priority }
"""

from __future__ import annotations

import logging
import math
from typing import Any

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# エントリポイント
# ---------------------------------------------------------------------------
def convert_truck_dispatcher_to_solver(business_dsl: dict) -> dict:
    """
    Business DSL → Solver Input DSL 変換。
    convert_business_to_solver() から problem_class で振り分けて呼ばれる。
    """
    meta    = business_dsl.get("meta", {})
    depot   = business_dsl.get("depot", {})
    config  = business_dsl.get("config", {})
    raw_veh = business_dsl.get("vehicles", [])
    raw_cust_all = business_dsl.get("customers", [])

    # --- 除外フラグ（"_excluded": true）が付いた顧客をここで完全に除去する。
    #     データ品質問題（ジオコーディング誤り等）で一時的に対象外にしたい場合に使う。
    #     locations/dist_matrix にも含めないため、CPOの探索空間削減にもなる。
    excluded = [c for c in raw_cust_all if c.get("_excluded")]
    raw_cust = [c for c in raw_cust_all if not c.get("_excluded")]
    if excluded:
        logger.warning(
            "[CVRP converter] _excluded=true の顧客 %d 件を除外しました: %s",
            len(excluded),
            ", ".join(f"{c['id']}({c.get('_excluded_reason', '理由未記載')})" for c in excluded),
        )

    # --- バリデーション ---
    if not depot:
        raise ValueError("[CVRP converter] depot フィールドが空です")
    if not raw_veh:
        raise ValueError("[CVRP converter] vehicles フィールドが空です")
    if not raw_cust:
        raise ValueError("[CVRP converter] customers フィールドが空です")

    # --- locations 構築（depot を index 0 に固定）---
    locations: list[dict] = [
        {
            "id":   depot.get("id", "depot"),
            "name": depot.get("name", "倉庫"),
            "lat":  float(depot.get("lat", 0.0)),
            "lon":  float(depot.get("lon", 0.0)),
        }
    ]
    for c in raw_cust:
        locations.append({
            "id":   c["id"],
            "name": c.get("name", c["id"]),
            "lat":  float(c.get("lat", 0.0)),
            "lon":  float(c.get("lon", 0.0)),
        })

    # --- 距離行列構築 ---
    dist_matrix = _build_distance_matrix(locations)

    # --- customers に _loc_idx を付与 ---
    loc_id_to_idx = {loc["id"]: idx for idx, loc in enumerate(locations)}
    customers: list[dict] = []
    for c in raw_cust:
        loc_idx = loc_id_to_idx.get(c["id"])
        if loc_idx is None:
            logger.warning(f"[CVRP converter] 顧客 {c['id']} が locations に存在しません（スキップ）")
            continue

        # --- 積み付け制約フィールドの正規化 ---
        # stackable: True=段積み可, False=段積み不可（デフォルト True）
        stackable_raw = c.get("stackable", True)
        if isinstance(stackable_raw, str):
            stackable = stackable_raw.lower() not in ("false", "0", "no", "不可", "×")
        else:
            stackable = bool(stackable_raw)

        # cargo_shape: 貨物の形状分類（文字列）
        cargo_shape = str(c.get("cargo_shape", "")).strip()

        # stack_limit: この貨物の上に積み重ねられる最大段数（0=無制限）
        try:
            stack_limit = int(c.get("stack_limit", 0))
        except (TypeError, ValueError):
            stack_limit = 0

        customers.append({
            "id":                       c["id"],
            "name":                     c.get("name", c["id"]),
            "lat":                      float(c.get("lat", 0.0)),
            "lon":                      float(c.get("lon", 0.0)),
            "demand_kg":                int(c.get("demand_kg", 0)),
            "tw_open_min":              _hhmm_to_min(c.get("tw_open",  "06:00")),
            "tw_close_min":             _hhmm_to_min(c.get("tw_close", "20:00")),
            "service_time_min":         int(c.get("service_time_min", 15)),
            "restricted_vehicle_types": c.get("restricted_vehicle_types", []),
            "stackable":                stackable,
            "cargo_shape":              cargo_shape,
            "stack_limit":              stack_limit,
            "_loc_idx":                 loc_idx,
        })

    # --- config 変換 ---
    depot_open_min  = _hhmm_to_min(depot.get("open_time",  "06:00"))
    depot_close_min = _hhmm_to_min(depot.get("close_time", "20:00"))

    solver_config = {
        "avg_speed_kmh":          float(config.get("avg_speed_kmh", 30.0)),
        # 修正（2026-07-08, Koshoshiとの相談）: highway_speed_kmh がここに無く、
        # シナリオJSON側でいくら変更してもソルバーには一切伝わらず、常に
        # _solve_cpo/_CVRPFallbackEngine 側のデフォルト(80.0)が使われていた
        # （たまたま既存シナリオのデフォルト値と一致していたため実害が顕在化していなかった）。
        "highway_speed_kmh":      float(config.get("highway_speed_kmh", 80.0)),
        "highway_threshold_km":   float(config.get("highway_threshold_km", 30.0)),
        "solver_time_limit_sec":  int(config.get("solver_time_limit_sec", 30)),
        "depot_open_min":         depot_open_min,
        "depot_close_min":        depot_close_min,
        "soft_tw_penalty_per_min": float(config.get("soft_tw_penalty_per_min", 10.0)),
        "relaxation_strategy":     config.get("relaxation_strategy", "auto"),
        "objective_priority":     config.get("objective_priority", [
            "minimize_vehicles",
            "minimize_distance",
            "balance_duty",
        ]),
        # 2026-07-08 Koshoshiとの相談②: RouteDecomposer(LNS)のパラメータも同様に
        # ここで引き継がないと、シナリオJSON側の設定が黙って無視されてしまう。
        "lns_ruin_vehicle_count":    int(config.get("lns_ruin_vehicle_count", 4)),
        "lns_max_iterations":        int(config.get("lns_max_iterations", 25)),
        "lns_iter_time_limit_sec":   int(config.get("lns_iter_time_limit_sec", 3)),
        "lns_total_time_budget_sec": float(config.get("lns_total_time_budget_sec", 120.0)),
        # 2026-07-20 Gate2 pre-commitチェックで検出: stagnation_limit/max_pool_customers
        # 追加時（docs/ENGINEERING_LOG.md参照）にconverterへの引き継ぎが漏れていた。
        # 他のlns_*キーと同じ理由・同じパターンでここに追加。
        "lns_stagnation_limit":      int(config.get("lns_stagnation_limit", 8)),
        "lns_max_pool_customers":    int(config.get("lns_max_pool_customers", 12)),
    }

    # --- vehicles に capacity_m3 / size_type を補完 ---
    vehicles: list[dict] = []
    for v in raw_veh:
        veh = dict(v)
        veh.setdefault("capacity_m3", float(v.get("capacity_m3", 0.0)))
        veh.setdefault("size_type", v.get("size_type", ""))
        vehicles.append(veh)

    return {
        "problem_class": "TruckDispatcher",
        "meta":          meta,
        "locations":     locations,
        "vehicles":      vehicles,
        "customers":     customers,
        "dist_matrix":   dist_matrix,
        "config":        solver_config,
        "issue_statuses": business_dsl.get("issue_statuses", {}),
    }


# ---------------------------------------------------------------------------
# ユーティリティ
# ---------------------------------------------------------------------------

def _hhmm_to_min(hhmm: str) -> int:
    """
    "HH:MM" 形式の文字列を分単位整数に変換する。
    パース失敗時は 0 を返してログに警告を出す。
    """
    try:
        h, m = hhmm.strip().split(":")
        return int(h) * 60 + int(m)
    except Exception:
        logger.warning(f"[CVRP converter] 時刻パース失敗: '{hhmm}' → 0 を使用")
        return 0


def _haversine_km(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Haversine 公式による球面距離（km）"""
    R = 6371.0
    phi1, phi2 = math.radians(lat1), math.radians(lat2)
    dphi = math.radians(lat2 - lat1)
    dlam = math.radians(lon2 - lon1)
    a = math.sin(dphi / 2) ** 2 + math.cos(phi1) * math.cos(phi2) * math.sin(dlam / 2) ** 2
    return R * 2 * math.asin(math.sqrt(a))


def _build_distance_matrix(locations: list[dict]) -> list[list[float]]:
    """locations リストから対称距離行列を構築する（km単位）"""
    n = len(locations)
    matrix: list[list[float]] = [[0.0] * n for _ in range(n)]
    for i in range(n):
        for j in range(i + 1, n):
            d = _haversine_km(
                locations[i]["lat"], locations[i]["lon"],
                locations[j]["lat"], locations[j]["lon"],
            )
            matrix[i][j] = d
            matrix[j][i] = d
    return matrix
