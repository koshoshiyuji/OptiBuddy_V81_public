"""
Backend/dsl_transformer/crew_duty_scheduler_ui_converter.py

Solver Output DSL → UI DSL 変換（CrewDutyScheduler）
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional


def convert_crew_duty_scheduler_to_ui(
    solver_output: dict,
    business_dsl: Optional[dict] = None,
) -> dict:
    """Solver Output DSL → UI DSL"""
    from dsl_transformer.table_sections import build_table_section, TableColumn

    feasible = solver_output.get("feasible", False)
    solutions = solver_output.get("solutions", [])
    solution = solutions[0] if solutions else {}

    duty_assignments: List[Dict] = solution.get("duty_assignments", [])
    kpi = solution.get("kpi", {})

    # ── KPIカード
    kpi_cards = _build_kpi_cards(kpi, feasible)

    # ── アラート
    alerts = _build_alerts(solver_output.get("issues", []))

    # ── 勤務割り当て詳細テーブル
    duty_rows = _build_duty_rows(duty_assignments)
    duty_section = build_table_section(
        section_id="duty_assignments",
        title="勤務割り当て一覧",
        columns=[
            TableColumn(key="duty_id",            label="勤務ID"),
            TableColumn(key="piece_count",         label="ピース数",    align="right", format="number"),
            TableColumn(key="duty_start_hhmm",     label="勤務開始"),
            TableColumn(key="duty_end_hhmm",       label="勤務終了"),
            TableColumn(key="duty_duration_min",   label="拘束時間(分)", align="right", format="number"),
            TableColumn(key="piece_ids_str",       label="運行ピース"),
        ],
        rows=duty_rows,
        row_id_key="duty_id",
    )

    # ── ピース別カバー状況テーブル
    pieces_from_dsl = (business_dsl or {}).get("pieces", [])
    covered_ids = {pid for d in duty_assignments for pid in d.get("piece_ids", [])}
    piece_rows = _build_piece_rows(pieces_from_dsl, covered_ids, duty_assignments)
    piece_section = build_table_section(
        section_id="piece_coverage",
        title="運行ピース カバー状況",
        columns=[
            TableColumn(key="piece_id",    label="ピースID"),
            TableColumn(key="name",        label="名称"),
            TableColumn(key="route",       label="路線"),
            TableColumn(key="start_hhmm",  label="開始"),
            TableColumn(key="end_hhmm",    label="終了"),
            TableColumn(key="duration_min", label="所要(分)", align="right", format="number"),
            TableColumn(key="duty_id",     label="割当勤務"),
            TableColumn(key="status",      label="状態",    format="badge"),
        ],
        rows=piece_rows,
        row_id_key="piece_id",
        severity_key="severity",
    )

    return {
        "domain":           "crew_duty_scheduler",
        "feasible":         feasible,
        "summary": {
            "total_duties":          kpi.get("total_duties", 0),
            "total_pieces_covered":  kpi.get("total_pieces_covered", 0),
            "avg_pieces_per_duty":   kpi.get("avg_pieces_per_duty", 0),
            "avg_duty_duration_min": kpi.get("avg_duty_duration_min", 0),
            "solve_time_sec":        kpi.get("solve_time_sec", 0),
        },
        "kpi_cards":        kpi_cards,
        "duty_assignments": duty_assignments,
        "table_sections":   [duty_section, piece_section],
        "alerts":           alerts,
        "raw_kpi":          kpi,
    }


# ------------------------------------------------------------------
# KPIカード
# ------------------------------------------------------------------

def _build_kpi_cards(kpi: Dict, feasible: bool) -> List[Dict]:
    if not feasible:
        return []
    return [
        {
            "label": "必要勤務数",
            "value": kpi.get("total_duties", 0),
            "unit":  "勤務",
            "color": "#8b5cf6",
        },
        {
            "label": "カバー済みピース",
            "value": kpi.get("total_pieces_covered", 0),
            "unit":  "ピース",
            "color": "#10b981",
        },
        {
            "label": "平均ピース数/勤務",
            "value": kpi.get("avg_pieces_per_duty", 0),
            "unit":  "件",
            "color": "#3b82f6",
        },
        {
            "label": "平均拘束時間",
            "value": kpi.get("avg_duty_duration_min", 0),
            "unit":  "分",
            "color": "#f97316",
        },
    ]


# ------------------------------------------------------------------
# アラート
# ------------------------------------------------------------------

def _build_alerts(issues: List[Dict]) -> List[Dict]:
    alerts = []
    for iss in issues:
        if iss.get("severity") in ("CRITICAL", "WARNING"):
            alerts.append({
                "id":       iss.get("id", ""),
                "severity": iss.get("severity", "WARNING"),
                "title":    iss.get("title", ""),
                "message":  iss.get("message", ""),
            })
    return alerts


# ------------------------------------------------------------------
# テーブル行データ組み立て
# ------------------------------------------------------------------

def _min_to_hhmm(minutes: int) -> str:
    minutes = int(minutes)
    h = minutes // 60
    m = minutes % 60
    return f"{h:02d}:{m:02d}"


def _build_duty_rows(duty_assignments: List[Dict]) -> List[Dict]:
    rows = []
    for d in duty_assignments:
        rows.append({
            "duty_id":           d["duty_id"],
            "piece_count":       d.get("piece_count", len(d.get("piece_ids", []))),
            "duty_start_hhmm":   _min_to_hhmm(d.get("duty_start_min", 0)),
            "duty_end_hhmm":     _min_to_hhmm(d.get("duty_end_min", 0)),
            "duty_duration_min": d.get("duty_duration_min", 0),
            "piece_ids_str":     ", ".join(d.get("piece_ids", [])),
        })
    return rows


def _build_piece_rows(
    pieces_from_dsl: List[Dict],
    covered_ids: set,
    duty_assignments: List[Dict],
) -> List[Dict]:
    # piece_id → duty_id の逆引きマップ
    pid_to_duty: Dict[str, str] = {}
    for d in duty_assignments:
        for pid in d.get("piece_ids", []):
            pid_to_duty[pid] = d["duty_id"]

    rows = []
    for p in pieces_from_dsl:
        pid = str(p.get("id", ""))
        covered = pid in covered_ids

        # start/end_min の取得（Business DSL 形式に対応）
        if "start_min" in p:
            start_min = int(p["start_min"])
            end_min   = int(p["end_min"])
        elif "start_time" in p:
            from dsl_transformer.crew_duty_scheduler_converter import _hhmm_to_min
            start_min = _hhmm_to_min(str(p["start_time"]))
            end_min   = _hhmm_to_min(str(p["end_time"]))
        else:
            start_min = end_min = 0

        rows.append({
            "piece_id":    pid,
            "name":        p.get("name", pid),
            "route":       p.get("route", ""),
            "start_hhmm":  _min_to_hhmm(start_min),
            "end_hhmm":    _min_to_hhmm(end_min),
            "duration_min": max(0, end_min - start_min),
            "duty_id":     pid_to_duty.get(pid, "—"),
            "status":      "OK" if covered else "UNCOVERED",
            "severity":    None if covered else "CRITICAL",
        })
    return rows