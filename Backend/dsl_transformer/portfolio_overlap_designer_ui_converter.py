"""
Backend/dsl_transformer/portfolio_overlap_designer_ui_converter.py

Solver Output DSL → UI DSL 変換
portfolio_overlap_designer ドメイン
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional


def convert_portfolio_overlap_designer_to_ui(
    solver_output: dict,
    business_dsl: Optional[dict] = None,
) -> dict:
    """
    Solver Output DSL → UI DSL

    ui_dsl.domain = "portfolio_overlap_designer" をセットする（フロント判別用）。
    """
    from dsl_transformer.table_sections import build_table_section, TableColumn

    feasible: bool = solver_output.get("feasible", False)
    kpi: Dict     = solver_output.get("kpi", {})
    solutions: List[Dict] = solver_output.get("solutions", [])
    solution = solutions[0] if solutions else {}

    fund_solutions: List[Dict] = solution.get("fund_solutions", [])
    overlap_matrix: List[Dict] = solution.get("overlap_matrix", [])

    # ── KPIカード ──
    worst_overlap = kpi.get("worst_overlap")
    kpi_cards = []
    if worst_overlap is not None:
        kpi_cards.append({
            "label": "最悪ペアワイズ重複数",
            "value": worst_overlap,
            "unit":  "銘柄",
            "color": "green" if worst_overlap == 0 else ("orange" if worst_overlap <= 2 else "red"),
        })
    kpi_cards.extend([
        {"label": "ファンド数",     "value": kpi.get("fund_count", 0),     "unit": "本"},
        {"label": "候補銘柄数",     "value": kpi.get("pool_size", 0),       "unit": "銘柄"},
        {"label": "計算時間",       "value": kpi.get("solve_time_sec", 0), "unit": "秒"},
    ])

    # ── アラート ──
    alerts: List[Dict] = []
    if not feasible:
        alerts.append({
            "severity": "CRITICAL",
            "title":    "解が見つかりませんでした",
            "message":  "制約を満たす銘柄組み合わせが存在しない可能性があります。",
        })
    elif worst_overlap is not None and worst_overlap >= 3:
        alerts.append({
            "severity": "WARNING",
            "title":    f"最悪ペアワイズ重複数が {worst_overlap} 銘柄あります",
            "message":  "複数ファンドを保有した場合の相関リスクが高い可能性があります。",
        })

    # ── table_sections: ファンド別組入銘柄一覧 ──
    fund_rows = [
        {
            "fund_id":        fs["fund_id"],
            "fund_name":      fs["fund_name"],
            "required_count": fs["required_count"],
            "selected_count": fs["selected_count"],
            "selected_stocks": "、".join(
                s["name"] for s in fs.get("selected_stocks", [])
            ),
            "coverage":       (
                fs["selected_count"] / fs["required_count"]
                if fs["required_count"] > 0 else 0
            ),
        }
        for fs in fund_solutions
    ]

    section_funds = build_table_section(
        section_id="portfolio_funds",
        title="ファンド別組入銘柄",
        columns=[
            TableColumn(key="fund_name",      label="ファンド名"),
            TableColumn(key="required_count", label="規定組入数",  format="number", align="right"),
            TableColumn(key="selected_count", label="実際の組入数", format="number", align="right"),
            TableColumn(key="coverage",       label="充足率",      format="percent", align="right"),
            TableColumn(key="selected_stocks", label="組入銘柄"),
        ],
        rows=fund_rows,
        row_id_key="fund_id",
    )

    # ── table_sections: ペアワイズ重複マトリックス ──
    overlap_rows = [
        {
            "pair_id":       f"{om['fund_id_a']}__{om['fund_id_b']}",
            "fund_name_a":   om["fund_name_a"],
            "fund_name_b":   om["fund_name_b"],
            "overlap_count": om["overlap_count"],
            "common_stocks": "、".join(om.get("common_stocks", [])) or "なし",
            "severity": (
                "CRITICAL" if om["overlap_count"] >= 3
                else "WARNING" if om["overlap_count"] >= 1
                else None
            ),
        }
        for om in overlap_matrix
    ]

    section_overlap = build_table_section(
        section_id="portfolio_overlap_matrix",
        title="ペアワイズ重複マトリックス",
        columns=[
            TableColumn(key="fund_name_a",   label="ファンドA"),
            TableColumn(key="fund_name_b",   label="ファンドB"),
            TableColumn(key="overlap_count", label="重複銘柄数", format="number", align="right"),
            TableColumn(key="common_stocks", label="共通銘柄"),
        ],
        rows=overlap_rows,
        row_id_key="pair_id",
        severity_key="severity",
    )

    table_sections = [section_funds, section_overlap]

    return {
        "domain":         "portfolio_overlap_designer",
        "feasible":       feasible,
        "summary": {
            "worst_overlap": worst_overlap,
            "fund_count":    kpi.get("fund_count", 0),
            "pool_size":     kpi.get("pool_size", 0),
        },
        "kpi_cards":      kpi_cards,
        "fund_solutions": fund_solutions,
        "overlap_matrix": overlap_matrix,
        "table_sections": table_sections,
        "alerts":         alerts,
        "raw_kpi":        kpi,
    }