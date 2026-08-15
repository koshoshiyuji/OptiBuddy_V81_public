"""
Backend/dsl_transformer/crew_duty_scheduler_converter.py

Business DSL → Solver Input DSL 変換（CrewDutyScheduler）

solver_input keys（CrewDutySchedulerSolver と完全一致）:
  problem_class  : str   "CrewDutyScheduler"
  meta           : Dict  メタ情報
  pieces         : List[Dict]  運行ピース（id, start_min, end_min, name, route,
                               origin, destination, duration_min）
  config         : Dict  パラメータ（max_duty_min, min_break_min,
                          max_driving_min, break_threshold_min,
                          required_break_min, max_pieces_per_duty,
                          solve_time_sec）
  issue_statuses : Dict  {issue_id: "ACCEPTED"}
"""

from __future__ import annotations

from typing import Any, Dict, List


def convert_crew_duty_scheduler_to_solver(business_dsl: dict) -> dict:
    """Business DSL → Solver Input DSL"""
    pieces = _build_pieces(business_dsl.get("pieces", []))
    config = _build_config(business_dsl.get("config", {}))

    return {
        "problem_class":  "CrewDutyScheduler",
        "meta":           business_dsl.get("meta", {}),
        "pieces":         pieces,
        "config":         config,
        "issue_statuses": business_dsl.get("issue_statuses", {}),
    }


# ------------------------------------------------------------------
# ピース変換
# ------------------------------------------------------------------

def _build_pieces(raw_pieces: List[Dict]) -> List[Dict]:
    """
    Business DSL の pieces を Solver Input DSL 形式に変換する。
    start_time / end_time が "HH:MM" 形式の場合は分に変換する。
    start_min / end_min が直接与えられている場合はそのまま使う。

    接続性チェック用に origin（始発地）・destination（終着地）を引き継ぐ。
    これらが未設定の場合は空文字とし、solver 側で接続性チェックをスキップする。
    """
    pieces = []
    for i, p in enumerate(raw_pieces):
        pid = str(p.get("id", f"piece_{i}"))
        name = p.get("name", pid)
        route = p.get("route", "")
        origin = p.get("origin", "")
        destination = p.get("destination", "")

        if "start_min" in p and "end_min" in p:
            start_min = int(p["start_min"])
            end_min = int(p["end_min"])
        elif "start_time" in p and "end_time" in p:
            start_min = _hhmm_to_min(str(p["start_time"]))
            end_min = _hhmm_to_min(str(p["end_time"]))
        else:
            # フォールバック（不正データはスキップせず警告値を入れる）
            start_min = 0
            end_min = 0

        pieces.append({
            "id":           pid,
            "name":         name,
            "route":        route,
            "origin":       origin,
            "destination":  destination,
            "start_min":    start_min,
            "end_min":      end_min,
            "duration_min": max(0, end_min - start_min),
        })

    return pieces


def _build_config(raw_config: Dict) -> Dict:
    """パラメータのデフォルト値を補完して返す。

    追加パラメータ:
      max_driving_min     : 1勤務の実乗務時間（ピースの duration_min 合計）の上限（分）。
                            デフォルト 450 分（7時間30分）。
      break_threshold_min : 拘束時間がこの値を超える勤務には休憩が必須（分）。
                            デフォルト 360 分（6時間）。
      required_break_min  : 必須休憩の最小長さ（分）。break_threshold_min 超過時に
                            連続ピース間のいずれかの gap がこの値以上でなければ不可。
                            デフォルト 30 分。
    """
    return {
        "max_duty_min":          int(raw_config.get("max_duty_min", 480)),
        "min_break_min":         int(raw_config.get("min_break_min", 10)),
        "max_driving_min":       int(raw_config.get("max_driving_min", 450)),
        "break_threshold_min":   int(raw_config.get("break_threshold_min", 360)),
        "required_break_min":    int(raw_config.get("required_break_min", 30)),
        "max_pieces_per_duty":   int(raw_config.get("max_pieces_per_duty", 6)),
        "solve_time_sec":        float(raw_config.get("solve_time_sec", 30)),
    }


def _hhmm_to_min(hhmm: str) -> int:
    """'HH:MM' 形式の文字列を分に変換する。"""
    try:
        h, m = hhmm.split(":")
        return int(h) * 60 + int(m)
    except Exception:
        return 0
