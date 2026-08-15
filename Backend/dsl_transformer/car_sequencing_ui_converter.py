"""
car_sequencing_ui_converter.py — Solver Output DSL → UI DSL (v1.0)
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional

from dsl_transformer.table_sections import build_table_section, TableColumn


def convert_car_sequencing_to_ui(
    solver_output: Dict[str, Any],
    business_dsl: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """
    CarSequencing Solver Output DSL → UI DSL

    ui_dsl.domain = "car_sequencing"（フロントがこれで判別）
    """
    solutions = solver_output.get("solutions", [])
    sol = solutions[0] if solutions else {}
    feasible: bool = sol.get("feasible", False)
    sequence: List[Dict] = sol.get("sequence", [])
    violations: List[Dict] = sol.get("violations", [])
    kpi: Dict = sol.get("kpi", {})
    issues: List[Dict] = solver_output.get("issues", [])

    # --- KPI カード ---
    kpi_cards = _build_kpi_cards(kpi, feasible)

    # --- アラート ---
    alerts = _build_alerts(issues)

    # --- table_sections ---
    table_sections = _build_table_sections(sequence, violations, kpi)

    # --- サマリー ---
    summary = {
        "total_cars":              kpi.get("total_cars", 0),
        "option_violation_total":  kpi.get("option_violation_total", 0),
        "same_car_violation_total": kpi.get("same_car_violation_total", 0),
        "objective_value":         kpi.get("objective_value", 0),
        "is_optimal":              kpi.get("is_optimal", False),
        "solve_status":            kpi.get("solve_status"),
    }

    return {
        "domain":          "car_sequencing",
        "feasible":        feasible,
        "summary":         summary,
        "kpi_cards":       kpi_cards,
        "sequence":        sequence,
        "violations":      violations,
        "table_sections":  table_sections,
        "alerts":          alerts,
        "raw_kpi":         kpi,
    }


def _build_kpi_cards(kpi: Dict, feasible: bool) -> List[Dict]:
    cards = [
        {
            "id":    "total_cars",
            "label": "総生産台数",
            "value": kpi.get("total_cars", 0),
            "unit":  "台",
            "color": "#3b82f6",
        },
        {
            "id":    "option_violation",
            "label": "オプション違反",
            "value": kpi.get("option_violation_total", 0),
            "unit":  "件",
            "color": "#ef4444" if kpi.get("option_violation_total", 0) > 0 else "#10b981",
        },
        {
            "id":    "same_car_violation",
            "label": "同一車種連続超過",
            "value": kpi.get("same_car_violation_total", 0),
            "unit":  "箇所",
            "color": "#f97316" if kpi.get("same_car_violation_total", 0) > 0 else "#10b981",
        },
        {
            "id":    "objective_value",
            "label": "目的関数値",
            "value": round(kpi.get("objective_value", 0), 1),
            "unit":  "",
            "color": "#8b5cf6",
        },
    ]
    if kpi.get("solve_time_sec") is not None:
        cards.append({
            "id":    "solve_time",
            "label": "求解時間",
            "value": round(kpi["solve_time_sec"], 1),
            "unit":  "秒",
            "color": "#14b8a6",
        })
    return cards


def _build_alerts(issues: List[Dict]) -> List[Dict]:
    alerts = []
    for issue in issues:
        sev = issue.get("severity", "INFO")
        if sev in ("CRITICAL", "WARNING"):
            alerts.append({
                "id":       issue.get("id", ""),
                "severity": sev,
                "title":    issue.get("title", ""),
                "message":  issue.get("message", ""),
            })
    return alerts


def _build_table_sections(
    sequence: List[Dict],
    violations: List[Dict],
    kpi: Dict,
) -> List[Dict]:
    sections = []

    # --- セクション1: 投入順序一覧 ---
    if sequence:
        # 各行: position, car_type_id, car_type_name, オプション装備（テキスト）
        seq_rows = []
        for item in sequence:
            opts_required = [
                o["option_name"] for o in item.get("options", []) if o.get("required")
            ]
            seq_rows.append({
                "position":      item["position"],
                "car_type_id":   item["car_type_id"],
                "car_type_name": item["car_type_name"],
                "options":       "、".join(opts_required) if opts_required else "—",
            })

        sections.append(build_table_section(
            section_id="car_sequence",
            title="投入順序一覧",
            columns=[
                TableColumn(key="position",      label="投入位置", align="right", format="number"),
                TableColumn(key="car_type_id",   label="車種ID"),
                TableColumn(key="car_type_name", label="車種名"),
                TableColumn(key="options",       label="装備オプション"),
            ],
            rows=seq_rows,
            row_id_key="position",
            allow_download=True,
        ))

    # --- セクション2: オプション工程違反詳細 ---
    if violations:
        viol_rows = []
        for v in violations:
            severity_val = "WARNING" if v["violation_count"] > 0 else None
            viol_rows.append({
                "option_id":       v["option_id"],
                "option_name":     v["option_name"],
                "window_size":     v["window_size"],
                "max_per_window":  v["max_per_window"],
                "violation_count": v["violation_count"],
                "status":          "OVER" if v["violation_count"] > 0 else "OK",
                "_severity_raw":   "WARNING" if v["violation_count"] > 0 else None,
            })

        sections.append(build_table_section(
            section_id="option_violations",
            title="オプション工程制約チェック",
            columns=[
                TableColumn(key="option_name",     label="オプション名"),
                TableColumn(key="window_size",      label="窓幅(q)", align="right", format="number"),
                TableColumn(key="max_per_window",   label="上限(p)", align="right", format="number"),
                TableColumn(key="violation_count",  label="違反件数", align="right", format="number"),
                TableColumn(key="status",           label="状態", format="badge"),
            ],
            rows=viol_rows,
            row_id_key="option_id",
            severity_key="_severity_raw",
            allow_download=True,
        ))

    return sections