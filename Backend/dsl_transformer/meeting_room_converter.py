"""
meeting_room_converter.py — Business DSL → Solver Input DSL

solver_input keys（meeting_room_solver.py と完全対応）:
  - meta: Dict             # instance_name, note（UI表示用パススルー）
  - problem_class: str
  - meetings: List[Dict]
      id, name, dept, start_min, end_min, attendees, required_features
  - rooms: List[Dict]
      id, name, capacity, features, available_start_min, available_end_min
  - config: Dict
      dept_same_room_bonus, waste_penalty_weight, dept_consecutive_gap_min, solve_time_sec
  - issue_statuses: Dict
"""

from __future__ import annotations

from typing import Any, Dict


def _hhmm_to_min(hhmm: str) -> int:
    """'HH:MM' 形式の文字列を分単位整数に変換する。"""
    if not hhmm or ":" not in hhmm:
        return 0
    h, m = hhmm.split(":", 1)
    return int(h) * 60 + int(m)


def convert_meeting_room_to_solver(business_dsl: dict) -> dict:
    """
    Business DSL → Solver Input DSL

    Business DSL の meetings / rooms / config を整形して返す。
    時刻は 'HH:MM' 形式の場合は分整数に変換し、既に整数の場合はそのまま使う。
    metaはUI表示用にそのままパススルーする（ヒアリング10節1項）。
    """
    # ヒアリング10節1項: metaはsolver_inputのmetaキーへそのまま転記
    meta = business_dsl.get("meta", {})

    meetings_raw = business_dsl.get("meetings", [])
    rooms_raw    = business_dsl.get("rooms", [])
    config_raw   = business_dsl.get("config", {})

    meetings = []
    for m in meetings_raw:
        start_raw = m.get("start_min") or m.get("start", 0)
        end_raw   = m.get("end_min")   or m.get("end", 0)
        start_min = _hhmm_to_min(str(start_raw)) if isinstance(start_raw, str) and ":" in str(start_raw) else int(start_raw)
        end_min   = _hhmm_to_min(str(end_raw))   if isinstance(end_raw, str)   and ":" in str(end_raw)   else int(end_raw)
        meetings.append({
            "id":                str(m.get("id", "")),
            "name":              str(m.get("name", m.get("id", ""))),
            "dept":              m.get("dept") or None,
            "start_min":         start_min,
            "end_min":           end_min,
            "attendees":         int(m.get("attendees", 1)),
            "required_features": list(m.get("required_features", [])),
        })

    rooms = []
    for r in rooms_raw:
        avail_s_raw = r.get("available_start_min") or r.get("available_start", 0)
        avail_e_raw = r.get("available_end_min")   or r.get("available_end", 1440)
        avail_s = _hhmm_to_min(str(avail_s_raw)) if isinstance(avail_s_raw, str) and ":" in str(avail_s_raw) else int(avail_s_raw)
        avail_e = _hhmm_to_min(str(avail_e_raw)) if isinstance(avail_e_raw, str) and ":" in str(avail_e_raw) else int(avail_e_raw)
        rooms.append({
            "id":                  str(r.get("id", "")),
            "name":                str(r.get("name", r.get("id", ""))),
            "capacity":            int(r.get("capacity", 0)),
            "features":            list(r.get("features", [])),
            "available_start_min": avail_s,
            "available_end_min":   avail_e,
        })

    config = {
        # ヒアリング5節: 同部署同室維持ボーナス0.1点/件（キー名: dept_same_room_bonus）
        "dept_same_room_bonus":     float(config_raw.get("dept_same_room_bonus", 0.1)),
        # ヒアリング4-1節: 部屋の無駄遣い1名分あたり1点
        "waste_penalty_weight":     int(config_raw.get("waste_penalty_weight", 1)),
        # ヒアリング5節: 同部署近接判定の閾値（分）
        "dept_consecutive_gap_min": int(config_raw.get("dept_consecutive_gap_min", 60)),
        "solve_time_sec":           int(config_raw.get("solve_time_sec", 30)),
    }

    return {
        "meta":           meta,
        "problem_class":  "MeetingRoom",
        "meetings":       meetings,
        "rooms":          rooms,
        "config":         config,
        "issue_statuses": business_dsl.get("issue_statuses", {}),
    }
