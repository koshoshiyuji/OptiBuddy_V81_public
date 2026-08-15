"""
Backend/dsl_transformer/steel_mill_slab_design_ui_converter.py

Solver Output DSL → UI DSL 変換（SteelMillSlabDesign, CSPLib prob038）
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional

from dsl_transformer.table_sections import build_table_section, TableColumn


def convert_steel_mill_slab_design_to_ui(
    solver_output: dict,
    business_dsl: Optional[dict] = None,
) -> dict:
    """
    Solver Output DSL → UI DSL

    ui_dsl.domain = "steel_mill_slab_design"（フロントのドメイン判別に使う）
    """
    feasible = solver_output.get("feasible", False)
    solutions = solver_output.get("solutions", [])
    issues = solver_output.get("issues", [])

    sol = solutions[0] if solutions else {}
    kpi: Dict[str, Any] = sol.get("kpi", {})
    assignments: List[Dict[str, Any]] = sol.get("assignments", [])
    slabs_used: List[Dict[str, Any]] = sol.get("slabs_used", [])

    total_slabs = int(kpi.get("total_slabs_used", 0))
    total_waste = float(kpi.get("total_waste_weight", 0))
    total_order_weight = float(kpi.get("total_order_weight", 0))
    waste_rate = float(kpi.get("waste_rate", 0.0))

    # ── KPIカード ──
    kpi_cards = []
    if feasible:
        kpi_cards = [
            {
                "label": "使用スラブ数",
                "value": f"{total_slabs} 個",
                "color": "#3b82f6",
                "icon": "🧱",
            },
            {
                "label": "鉄の使用量合計",
                "value": f"{total_order_weight + total_waste:,.0f} t",
                "color": "#8b5cf6",
                "icon": "⚙️",
            },
            {
                "label": "廃棄重量",
                "value": f"{total_waste:,.0f} t",
                "color": "#f97316",
                "icon": "🗑️",
            },
            {
                "label": "廃棄率",
                "value": f"{round(waste_rate * 100, 1)}%",
                "color": "#10b981" if waste_rate < 0.15 else "#f97316",
                "icon": "📊",
            },
        ]

    # ── アラート ──
    alerts = []
    for iss in issues:
        if iss.get("severity") in ("CRITICAL", "WARNING"):
            alerts.append({
                "id":       iss.get("id", ""),
                "severity": iss.get("severity", "INFO"),
                "title":    iss.get("title", ""),
                "message":  iss.get("message", ""),
            })

    # ── 割り付け一覧テーブル（注文 → スラブ） ──
    assignment_rows = [
        {
            "order_id":   a.get("order_id", ""),
            "order_name": a.get("order_name", ""),
            "customer":   a.get("customer", ""),
            "slab_index": (a.get("slab_index", 0) + 1),
            "weight":     float(a.get("weight", 0)),
            "colors":     ", ".join(a.get("colors", [])),
        }
        for a in assignments
    ]
    assignments_section = build_table_section(
        section_id="assignments",
        title="注文 → スラブ割り付け一覧",
        columns=[
            TableColumn(key="order_id",   label="注文ID",   align="left"),
            TableColumn(key="order_name", label="注文名",   align="left"),
            TableColumn(key="customer",   label="得意先",   align="left"),
            TableColumn(key="slab_index", label="スラブ番号", align="center"),
            TableColumn(key="weight",     label="重量(t)",  align="right", format="number"),
            TableColumn(key="colors",     label="色",       align="left"),
        ],
        rows=assignment_rows,
        row_id_key="order_id",
        allow_download=True,
    )

    # ── スラブ別サマリーテーブル ──
    slab_rows = [
        {
            "slab_index":  (s.get("slab_index", 0) + 1),
            "capacity":    float(s.get("capacity", 0)),
            "used_weight": float(s.get("used_weight", 0)),
            "waste_weight": float(s.get("waste_weight", 0)),
            "colors":      ", ".join(s.get("colors", [])),
            "order_count": int(s.get("order_count", 0)),
        }
        for s in slabs_used
    ]
    slabs_section = build_table_section(
        section_id="slabs_used",
        title="スラブ別サマリー",
        columns=[
            TableColumn(key="slab_index",   label="スラブ番号",  align="center"),
            TableColumn(key="capacity",     label="規格容量(t)", align="right", format="number"),
            TableColumn(key="used_weight",  label="積載重量(t)", align="right", format="number"),
            TableColumn(key="waste_weight", label="廃棄重量(t)", align="right", format="number"),
            TableColumn(key="colors",       label="色の種類",   align="left"),
            TableColumn(key="order_count",  label="注文件数",   align="center", format="number"),
        ],
        rows=slab_rows,
        row_id_key="slab_index",
        allow_download=True,
    )

    table_sections = [slabs_section, assignments_section] if feasible else []

    return {
        "domain":   "steel_mill_slab_design",
        "feasible": feasible,
        "summary": {
            "total_slabs_used":     total_slabs,
            "total_waste_weight":   total_waste,
            "total_order_weight":   total_order_weight,
            "waste_rate":           waste_rate,
        },
        "kpi_cards":      kpi_cards,
        "table_sections": table_sections,
        "alerts":         alerts,
        "raw_kpi":        kpi,
    }
