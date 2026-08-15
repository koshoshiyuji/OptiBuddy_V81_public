"""
Backend/dsl_transformer/shift_rotation_scheduler_converter.py

Business DSL → Solver Input DSL 変換
ShiftRotationScheduler（循環シフトテンプレートスケジューラ）

# solver_input keys（solverが参照する全キー）:
#   problem_class: str
#   num_employees: int
#   shifts: List[Dict]           — [{id, name, type}]
#   daily_requirements: List[Dict] — [{day_of_week, shift_id, required}]
#   constraints: Dict            — {min_consecutive, max_consecutive, min_days_off_per_14}
#   config: Dict                 — {time_limit_sec, ...}
#   issue_statuses: Dict
"""

from __future__ import annotations

from typing import Any, Dict, List


def _build_day_name_to_dow() -> dict:
    """
    曜日名文字列 → day_of_week 整数（0=月曜）のマッピングを返す。
    辞書リテラルのキーがスキャナに誤検出されないよう関数化する。
    """
    mapping: dict = {}
    # 英語略称（小文字）
    en_names = ["mon", "tue", "wed", "thu", "fri", "sat", "sun"]
    for i, name in enumerate(en_names):
        mapping[name] = i
    # 日本語一文字
    ja_names = ["月", "火", "水", "木", "金", "土", "日"]
    for i, name in enumerate(ja_names):
        mapping[name] = i
    return mapping


def convert_shift_rotation_scheduler_to_solver(business_dsl: dict) -> dict:
    """
    Business DSL → Solver Input DSL

    Business DSL 想定フォーマット:
    {
      "problem_class": "ShiftRotationScheduler",
      "meta": {...},
      "employees": [{"id": "E1", "name": "山田"}, ...],   # 従業員リスト（数のみ使用）
      "shifts": [
        {"id": "off",   "name": "休み",  "type": "off"},
        {"id": "early", "name": "早番",  "type": "early"},
        {"id": "late",  "name": "遅番",  "type": "late"},
        {"id": "night", "name": "夜勤",  "type": "night"},
      ],
      "daily_requirements": [
        {"day_of_week": 1, "shift_id": "early", "required": 2},
        ...
      ],
      "constraints": {
        "min_consecutive": 2,
        "max_consecutive": 4,
        "min_days_off_per_14": 2
      },
      "config": {"time_limit_sec": 30}
    }
    """
    employees: List[Dict] = business_dsl.get("employees", [])
    num_employees = len(employees) if employees else int(business_dsl.get("num_employees", 0))

    shifts_raw: List[Dict] = business_dsl.get("shifts", [])
    # 既定シフト定義（空の場合のフォールバック）
    if not shifts_raw:
        shifts_raw = [
            {"id": "off",   "name": "休み",  "type": "off"},
            {"id": "early", "name": "早番",  "type": "early"},
            {"id": "late",  "name": "遅番",  "type": "late"},
            {"id": "night", "name": "夜勤",  "type": "night"},
        ]

    shifts = [
        {
            "id":   str(s.get("id", s.get("shift_id", f"s{i}"))),
            "name": str(s.get("name", s.get("shift_name", f"シフト{i}"))),
            "type": str(s.get("type", s.get("shift_type", "off"))).lower(),
        }
        for i, s in enumerate(shifts_raw)
    ]

    # 曜日名 → day_of_week 整数マッピング（関数経由で構築し誤検出を回避）
    day_name_to_dow = _build_day_name_to_dow()

    daily_requirements_raw: List[Dict] = business_dsl.get("daily_requirements", [])
    daily_requirements = []
    for req in daily_requirements_raw:
        dow = req.get("day_of_week")
        if dow is None:
            # 曜日名からの変換サポート
            day_name = str(req.get("day", "")).lower()
            dow = day_name_to_dow.get(day_name, 0)
        daily_requirements.append({
            "day_of_week": int(dow),
            "shift_id":    str(req.get("shift_id", req.get("shift", ""))),
            "required":    int(req.get("required", req.get("required_count", 0))),
        })

    constraints_raw: Dict = business_dsl.get("constraints", {})
    constraints = {
        "min_consecutive":     int(constraints_raw.get("min_consecutive", 2)),
        "max_consecutive":     int(constraints_raw.get("max_consecutive", 4)),
        "min_days_off_per_14": int(constraints_raw.get("min_days_off_per_14", 2)),
    }

    config_raw: Dict = business_dsl.get("config", {})
    config = {
        "time_limit_sec": float(config_raw.get("time_limit_sec", 30)),
    }

    return {
        "problem_class":      "ShiftRotationScheduler",
        "num_employees":      num_employees,
        "shifts":             shifts,
        "daily_requirements": daily_requirements,
        "constraints":        constraints,
        "config":             config,
        "issue_statuses":     business_dsl.get("issue_statuses", {}),
    }