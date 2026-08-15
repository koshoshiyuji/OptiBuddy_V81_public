"""
Backend/dsl_transformer/energy_cost_aware_scheduler_converter.py

Business DSL → Solver Input DSL 変換
EnergyCostAwareScheduler ドメイン

# solver_input keys:
#   problem_class: str
#   orders:        List[Dict]   生産オーダー一覧
#   lines:         List[Dict]   生産ライン一覧
#   tariffs:       List[Dict]   時間帯別電力単価
#   config:        Dict         horizon_min, time_limit_sec, slot_min,
#                               priority_customer_names
#   issue_statuses: Dict
"""

from __future__ import annotations

from typing import Any, Dict, List


def convert_energy_cost_aware_scheduler_to_solver(business_dsl: dict) -> dict:
    """
    Business DSL → Solver Input DSL。

    Business DSL スキーマ:
      problem_class: "EnergyCostAwareScheduler"
      orders: [{
        id, name, duration_min, earliest_start_min, deadline_min,
        power_kw, workers, equipment_slots,
        allowed_line_ids: List[str] | null,
        customer: str
      }]
      lines: [{
        id, name, max_power_kw, max_workers, max_equipment_slots,
        standby_power_kw, startup_cost, shutdown_cost
      }]
      tariffs: [{slot_start: int(分), slot_end: int(分), price_per_kwh: float}]
      config: {
        horizon_min: int,
        time_limit_sec: int,
        slot_min: int,
        priority_customer_names: List[str]
      }
      meta: {}
    """
    orders  = _normalize_orders(business_dsl.get("orders", []))
    lines   = _normalize_lines(business_dsl.get("lines", []))
    tariffs = _normalize_tariffs(business_dsl.get("tariffs", []))
    config  = _normalize_config(business_dsl.get("config", {}))

    return {
        "problem_class":   "EnergyCostAwareScheduler",
        "orders":          orders,
        "lines":           lines,
        "tariffs":         tariffs,
        "config":          config,
        "issue_statuses":  business_dsl.get("issue_statuses", {}),
    }


# =============================================================================
# 正規化ヘルパー
# =============================================================================

def _normalize_orders(raw: List[Dict]) -> List[Dict]:
    """生産オーダーを正規化する。"""
    result = []
    for o in raw:
        oid = str(o.get("id", ""))
        if not oid:
            continue
        result.append({
            "id":                str(o.get("id")),
            "name":              str(o.get("name", oid)),
            "duration_min":      int(o.get("duration_min", 60)),
            "earliest_start_min": int(o.get("earliest_start_min", 0)),
            "deadline_min":      int(o.get("deadline_min", 1440)),
            "power_kw":          float(o.get("power_kw", 0.0)),
            "workers":           int(o.get("workers", 1)),
            "equipment_slots":   int(o.get("equipment_slots", 1)),
            # allowed_line_ids: None の場合は全ライン対応可
            "allowed_line_ids":  (
                [str(lid) for lid in o["allowed_line_ids"]]
                if o.get("allowed_line_ids") is not None
                else None
            ),
            "customer":          str(o.get("customer", "")),
        })
    return result


def _normalize_lines(raw: List[Dict]) -> List[Dict]:
    """生産ラインを正規化する。"""
    result = []
    for ln in raw:
        lid = str(ln.get("id", ""))
        if not lid:
            continue
        result.append({
            "id":                  lid,
            "name":                str(ln.get("name", lid)),
            "max_power_kw":        float(ln.get("max_power_kw", 9999.0)),
            "max_workers":         int(ln.get("max_workers", 9999)),
            "max_equipment_slots": int(ln.get("max_equipment_slots", 9999)),
            "standby_power_kw":    float(ln.get("standby_power_kw", 0.0)),
            "startup_cost":        float(ln.get("startup_cost", 0.0)),
            "shutdown_cost":       float(ln.get("shutdown_cost", 0.0)),
        })
    return result


def _normalize_tariffs(raw: List[Dict]) -> List[Dict]:
    """時間帯別電力単価を正規化する。スロット境界を分単位に統一。"""
    result = []
    for t in raw:
        result.append({
            "slot_start":    int(t.get("slot_start", 0)),
            "slot_end":      int(t.get("slot_end", 30)),
            "price_per_kwh": float(t.get("price_per_kwh", 0.0)),
        })
    # slot_start 昇順でソート
    result.sort(key=lambda x: x["slot_start"])
    return result


def _normalize_config(raw: Dict) -> Dict:
    """設定値を正規化する。"""
    return {
        "horizon_min":              int(raw.get("horizon_min", 1440)),
        "time_limit_sec":           int(raw.get("time_limit_sec", 60)),
        "slot_min":                 int(raw.get("slot_min", 30)),
        "priority_customer_names":  [
            str(n) for n in raw.get("priority_customer_names", [])
        ],
    }