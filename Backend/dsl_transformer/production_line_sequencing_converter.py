"""
Backend/dsl_transformer/production_line_sequencing_converter.py

Business DSL → Solver Input DSL 変換

solver_input keys (全て出力する):
  - problem_class: str
  - batches: List[Dict]  # id, name, compatible_lines, line_on_day, line_off_day, vehicle_type
  - slots: List[Dict]    # id, line_id, line_name, day, start_hour
  - lines: List[Dict]    # id, name
  - days: List[int]
  - vehicle_types: List[Dict]  # id, name, daily_limit, priority_order
  - distribution_exceptions: List[Dict]
  - config: Dict
  - issue_statuses: Dict
"""

from __future__ import annotations

from typing import Any, Dict, List

def convert_production_line_sequencing_to_solver(business_dsl: dict) -> dict:
    """Business DSL → Solver Input DSL"""

    lines_raw = business_dsl.get("lines", [])
    batches_raw = business_dsl.get("batches", [])
    vehicle_types_raw = business_dsl.get("vehicle_types", [])
    config = business_dsl.get("config", {})
    distribution_exceptions_raw = business_dsl.get("distribution_exceptions", [])
    issue_statuses = business_dsl.get("issue_statuses", {})

    # ライン一覧の正規化
    lines = []
    for l in lines_raw:
        lines.append({
            "id": str(l.get("id", l.get("line_id", ""))),
            "name": l.get("name", str(l.get("id", ""))),
        })

    # 日一覧の構築
    days_raw = config.get("days", [])
    if not days_raw:
        # バッチのline_on/line_offから推定
        all_days = set()
        for b in batches_raw:
            on = b.get("line_on_day", 1)
            off = b.get("line_off_day", 1)
            for d in range(int(on), int(off) + 1):
                all_days.add(d)
        days = sorted(all_days) if all_days else [1]
    else:
        days = sorted(int(d) for d in days_raw)

    # スロット一覧の構築
    # config.slots が明示されていればそれを使う、なければ lines × days × hours_per_day から生成
    slots_raw = business_dsl.get("slots", [])
    if slots_raw:
        slots = []
        for s in slots_raw:
            lid = str(s.get("line_id", ""))
            line_name = next((l["name"] for l in lines if l["id"] == lid), lid)
            slots.append({
                "id": str(s.get("id", f"{lid}_d{s.get('day')}_h{s.get('start_hour')}")),
                "line_id": lid,
                "line_name": line_name,
                "day": int(s.get("day", 1)),
                "start_hour": int(s.get("start_hour", 8)),
            })
    else:
        # 自動生成: 各ライン × 各日 × slots_per_day
        slots_per_day = config.get("slots_per_day", 3)
        start_hour_base = config.get("start_hour", 8)
        slots = []
        for day in days:
            for line in lines:
                lid = line["id"]
                for h_idx in range(slots_per_day):
                    hour = start_hour_base + h_idx
                    sid = f"{lid}_d{day}_h{hour}"
                    slots.append({
                        "id": sid,
                        "line_id": lid,
                        "line_name": line["name"],
                        "day": day,
                        "start_hour": hour,
                    })

    # バッチ一覧の正規化
    batches = []
    for b in batches_raw:
        bid = str(b.get("id", ""))
        compatible_lines_raw = b.get("compatible_lines", None)
        if compatible_lines_raw is not None:
            compatible_lines = [str(l) for l in compatible_lines_raw]
        else:
            compatible_lines = None  # 全ライン可

        batches.append({
            "id": bid,
            "name": b.get("name", bid),
            "compatible_lines": compatible_lines,
            "line_on_day": int(b.get("line_on_day", days[0] if days else 1)),
            "line_off_day": int(b.get("line_off_day", days[-1] if days else 1)),
            "vehicle_type": str(b.get("vehicle_type", "")),
        })

    # 車種定義の正規化
    vehicle_types = []
    for i, vt in enumerate(vehicle_types_raw):
        vehicle_types.append({
            "id": str(vt.get("id", "")),
            "name": vt.get("name", str(vt.get("id", ""))),
            "daily_limit": int(vt.get("daily_limit", 99)),
            "priority_order": int(vt.get("priority_order", i + 1)),
        })

    # 分散ルール例外の正規化
    distribution_exceptions = []
    for exc in distribution_exceptions_raw:
        period_days_raw = exc.get("period_days", [])
        distribution_exceptions.append({
            "period_days": [int(d) for d in period_days_raw],
            "vehicle_type_id": str(exc.get("vehicle_type_id", "")),
            "max_per_day": int(exc["max_per_day"]) if "max_per_day" in exc else None,
            "min_per_day": int(exc["min_per_day"]) if "min_per_day" in exc else None,
        })

    return {
        "problem_class": "ProductionLineSequencing",
        "batches": batches,
        "slots": slots,
        "lines": lines,
        "days": days,
        "vehicle_types": vehicle_types,
        "distribution_exceptions": distribution_exceptions,
        "config": {
            "time_limit_sec": config.get("time_limit_sec", 30),
            "start_hour": config.get("start_hour", 8),
        },
        "issue_statuses": issue_statuses,
    }

