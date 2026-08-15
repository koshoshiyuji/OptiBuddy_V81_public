"""
vessel_deck_loader_ui_converter.py
Solver Output DSL → UI DSL (VesselDeckLoader)
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional


def convert_vessel_deck_loader_to_ui(
    solver_output: dict,
    business_dsl: Optional[dict] = None,
) -> dict:
    """Solver Output DSL → UI DSL"""
    from dsl_transformer.table_sections import build_table_section, TableColumn

    feasible: bool = solver_output.get("feasible", False)
    solutions: List[Dict] = solver_output.get("solutions", [])
    issues: List[Dict] = solver_output.get("issues", [])
    meta: Dict = solver_output.get("metadata", {})

    sol = solutions[0] if solutions else {}
    placements: List[Dict] = sol.get("placements", [])
    Z_val: int = sol.get("Z_val", 0)
    deck_width: int = sol.get("deck_width", 6)
    kpi: Dict = sol.get("kpi", {})

    # --- KPIカード ---
    kpi_cards = []
    if feasible:
        kpi_cards = [
            {"label": "使用甲板長", "value": Z_val, "unit": "m", "color": "#3b82f6"},
            {"label": "積み付け数", "value": kpi.get("container_count", len(placements)), "unit": "個", "color": "#10b981"},
            {"label": "危険物数",   "value": kpi.get("hazardous_count", 0), "unit": "個", "color": "#f97316"},
        ]

    # --- アラート ---
    alerts = [
        {
            "id":       i.get("id", ""),
            "severity": i.get("severity", "WARNING"),
            "title":    i.get("title", ""),
            "message":  i.get("message", ""),
        }
        for i in issues
        if i.get("severity") in ("CRITICAL", "WARNING")
    ]

    # --- 配置図データ (SVG/Canvas描画用) ---
    layout = _build_layout(placements, Z_val, deck_width)

    # --- table_sections ---
    placement_rows = [
        {
            "container_id":   p["container_id"],
            "container_name": p["container_name"],
            "x":              p["x"],
            "y":              p["y"],
            "length":         p["length"],
            "width":          p["width"],
            "load_order":     p["load_order"],
            "is_hazardous":   "⚠ 危険物" if p["is_hazardous"] else "通常",
            "severity":       "WARNING" if p["is_hazardous"] else None,
        }
        for p in sorted(placements, key=lambda p: p["load_order"])
    ]

    table_sections = [
        build_table_section(
            section_id="vessel_deck_placement",
            title="コンテナ配置一覧",
            columns=[
                TableColumn(key="load_order",     label="積順",   align="right",  format="number"),
                TableColumn(key="container_name", label="コンテナ名"),
                TableColumn(key="x",              label="X位置(m)", align="right", format="number"),
                TableColumn(key="y",              label="Y位置(m)", align="right", format="number"),
                TableColumn(key="length",         label="長さ(m)",  align="right", format="number"),
                TableColumn(key="width",          label="幅(m)",    align="right", format="number"),
                TableColumn(key="is_hazardous",   label="危険物",  format="badge"),
            ],
            rows=placement_rows,
            row_id_key="container_id",
            severity_key="severity",
        )
    ]

    return {
        "domain":         "vessel_deck_loader",
        "feasible":       feasible,
        "summary": {
            "used_length": Z_val,
            "deck_width":  deck_width,
            "total_containers": len(placements),
        },
        "kpi_cards":      kpi_cards,
        "placements":     placements,
        "layout":         layout,
        "table_sections": table_sections,
        "alerts":         alerts,
        "raw_kpi":        kpi,
    }


def _build_layout(placements: List[Dict], Z_val: int, deck_width: int) -> Dict:
    """フロントエンドのSVG描画用レイアウトデータを生成する。"""
    return {
        "deck_length": max(Z_val, 1),
        "deck_width":  max(deck_width, 1),
        "containers":  [
            {
                "id":           p["container_id"],
                "name":         p["container_name"],
                "x":            p["x"],
                "y":            p["y"],
                "length":       p["length"],
                "width":        p["width"],
                "is_hazardous": p["is_hazardous"],
                "load_order":   p["load_order"],
            }
            for p in placements
        ],
    }