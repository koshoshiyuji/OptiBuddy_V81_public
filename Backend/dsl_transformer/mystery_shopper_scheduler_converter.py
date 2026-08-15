"""
Backend/dsl_transformer/mystery_shopper_scheduler_converter.py

Business DSL → Solver Input DSL 変換

solver_input keys（solver.py と完全一致させること）:
  - problem_class: str
  - shoppers: List[Dict]     # id, name, area, available_days, qualifications
  - visits: List[Dict]       # id, store_id, visit_index, min_revisit_days
  - stores: List[Dict]       # id, name, area, required_qualifications, visit_count, duration_hours
  - config: Dict
  - issue_statuses: Dict
"""

from __future__ import annotations

from typing import Any, Dict, List

def convert_mystery_shopper_scheduler_to_solver(business_dsl: dict) -> dict:
    """
    Business DSL → Solver Input DSL

    Business DSL 想定スキーマ:
      problem_class: "MysteryShopperScheduler"
      meta: {instance_name, note}
      config:
        default_min_revisit_days: int  # 未指定時のデフォルト（28）
        solve_time_sec: int            # ソルブ制限時間（秒）
      shoppers:
        - id: str
          name: str
          area: str                    # 居住エリア
          available_days: [str, ...]   # 稼働可能日（ISO 8601, "YYYY-MM-DD"）
          qualifications: [str, ...]   # 保有資格リスト
      stores:
        - id: str
          name: str
          area: str                    # 所在エリア
          visit_count: int             # 期間内必要訪問回数
          min_revisit_days: int        # 最低再訪問間隔（省略時は config のデフォルト値）
          required_qualifications: [str, ...]  # 必要資格（空リスト可）
          duration_hours: float        # 所要時間（目安、表示用）
    """
    default_min_revisit = int(
        business_dsl.get("config", {}).get("default_min_revisit_days", 28)
    )

    shoppers_raw: List[Dict] = business_dsl.get("shoppers", [])
    stores_raw: List[Dict] = business_dsl.get("stores", [])

    # 調査員リスト正規化
    shoppers: List[Dict] = []
    for sh in shoppers_raw:
        shoppers.append({
            "id": str(sh["id"]),
            "name": sh.get("name", str(sh["id"])),
            "available_days": [str(d) for d in sh.get("available_days", [])],
            "qualifications": [str(q) for q in sh.get("qualifications", [])],
        })

    # 店舗マスタ正規化
    stores: List[Dict] = []
    for s in stores_raw:
        stores.append({
            "id": str(s["id"]),
            "name": s.get("name", str(s["id"])),
            "min_revisit_days": int(s.get("min_revisit_days", default_min_revisit)),
            "required_qualifications": [str(q) for q in s.get("required_qualifications", [])],
        })

    # 店舗訪問枠の展開（1店舗 × visit_count 回 = 複数の visit 枠）
    visits: List[Dict] = []
    for s in stores_raw:
        store_id = str(s["id"])
        visit_count = int(s.get("visit_count", 1))
        min_revisit = int(s.get("min_revisit_days", default_min_revisit))
        for i in range(visit_count):
            visits.append({
                "id": f"{store_id}_v{i+1}",
                "store_id": store_id,
                "visit_index": i + 1,
                "min_revisit_days": min_revisit,
            })

    config: Dict[str, Any] = {
        "default_min_revisit_days": default_min_revisit,
        "solve_time_sec": int(business_dsl.get("config", {}).get("solve_time_sec", 30)),
    }

    return {
        "problem_class": "MysteryShopperScheduler",
        "shoppers": shoppers,
        "visits": visits,
        "stores": stores,
        "config": config,
        "issue_statuses": business_dsl.get("issue_statuses", {}),
    }

