"""
Backend/dsl_transformer/energy_cost_aware_scheduler_ui_converter.py

Solver Output DSL → UI DSL 変換
EnergyCostAwareScheduler ドメイン
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional


def convert_energy_cost_aware_scheduler_to_ui(
    solver_output: dict,
    business_dsl: Optional[dict] = None,
) -> dict:
    """
    Solver Output DSL → UI DSL。

    ui_dsl キー:
      domain:          "energy_cost_aware_scheduler"
      feasible:        bool
      summary:         Dict
      kpi_cards:       List[Dict]
      schedule:        List[Dict]   割り当て済みオーダー
      line_ops:        List[Dict]   ライン稼働記録
      unfinished:      List[Dict]   未完了オーダー
      table_sections:  List[Dict]   GenericResultTable 用
      alerts:          List[Dict]
      raw_kpi:         Dict
    """
    from dsl_transformer.table_sections import build_table_section, TableColumn

    solutions = solver_output.get("solutions", [])
    feasible  = solver_output.get("feasible", False)
    issues    = solver_output.get("issues", [])

    if not solutions or not feasible:
        return {
            "domain":         "energy_cost_aware_scheduler",
            "feasible":       False,
            "summary":        {"message": "実行可能解が見つかりませんでした。"},
            "kpi_cards":      [],
            "schedule":       [],
            "line_ops":       [],
            "unfinished":     [],
            "table_sections": [],
            "alerts":         _build_alerts(issues),
            "raw_kpi":        {},
        }

    sol     = solutions[0]
    schedule  = sol.get("schedule", [])
    line_ops  = sol.get("line_ops", [])
    unfinished = sol.get("unfinished", [])
    kpi       = sol.get("kpi", {})

    # ── KPI カード ──────────────────────────────────────────────────────────
    kpi_cards = [
        {
            "label": "完了オーダー数",
            "value": f"{kpi.get('completed_orders', 0)} / {kpi.get('total_orders', 0)}",
            "sub":   f"充足率 {round(kpi.get('completion_rate', 0) * 100, 1)}%",
            "color": "#10b981",
        },
        {
            "label": "総コスト",
            "value": f"¥{kpi.get('total_cost', 0):,.0f}",
            "sub":   (
                f"電力: ¥{kpi.get('order_energy_cost', 0) + kpi.get('line_energy_cost', 0):,.0f} "
                f"/ 起動停止: ¥{kpi.get('line_operation_cost', 0):,.0f}"
            ),
            "color": "#f97316",
        },
        {
            "label": "稼働ライン数",
            "value": str(len(line_ops)),
            "sub":   f"ソルブ時間 {kpi.get('solve_time', 0):.1f}秒",
            "color": "#3b82f6",
        },
    ]

    if kpi.get("is_optimal"):
        kpi_cards.append({
            "label": "最適性",
            "value": "最適解",
            "sub":   "最適性が証明されました",
            "color": "#10b981",
        })
    else:
        kpi_cards.append({
            "label": "最適性",
            "value": "実行可能解",
            "sub":   "制限時間内の最良解（最適性未証明）",
            "color": "#f59e0b",
        })

    # ── table_sections ──────────────────────────────────────────────────────

    # 1. スケジュール詳細（割り当て済みオーダー）
    schedule_rows = [
        {
            "order_id":    s["order_id"],
            "order_name":  s.get("order_name", s["order_id"]),
            "customer":    s.get("customer", ""),
            "line_id":     s["line_id"],
            "start":       s["start"],
            "end":         s["end"],
            "duration":    s.get("duration", s["end"] - s["start"]),
            "power_kw":    s.get("power_kw", 0.0),
            "deadline_min": s.get("deadline_min", 1440),
            "on_time":     "✓" if s["end"] <= s.get("deadline_min", 1440) else "⚠ 超過",
        }
        for s in schedule
    ]
    section_schedule = build_table_section(
        section_id="scheduled_orders",
        title="割り当て済みオーダー",
        columns=[
            TableColumn(key="order_name",  label="オーダー名"),
            TableColumn(key="customer",    label="得意先"),
            TableColumn(key="line_id",     label="割当ライン"),
            TableColumn(key="start",       label="開始(分)", format="number", align="right"),
            TableColumn(key="end",         label="終了(分)", format="number", align="right"),
            TableColumn(key="duration",    label="所要時間(分)", format="number", align="right"),
            TableColumn(key="power_kw",    label="消費電力(kW)", format="number", align="right"),
            TableColumn(key="deadline_min", label="期限(分)",  format="number", align="right"),
            TableColumn(key="on_time",     label="期限内", format="badge"),
        ],
        rows=schedule_rows,
        row_id_key="order_id",
        allow_download=True,
    )

    # 2. 未完了オーダー
    unfinished_rows = [
        {
            "order_id":  u.get("order_id", ""),
            "name":      u.get("name", ""),
            "customer":  u.get("customer", ""),
        }
        for u in unfinished
    ]
    section_unfinished = build_table_section(
        section_id="unfinished_orders",
        title="未完了オーダー（翌計画期間へ繰越）",
        columns=[
            TableColumn(key="name",     label="オーダー名"),
            TableColumn(key="customer", label="得意先"),
        ],
        rows=unfinished_rows,
        row_id_key="order_id",
        allow_download=True,
    )

    # 3. ライン稼働状況
    line_util_rows = kpi.get("line_utilization", [])
    section_line_ops = build_table_section(
        section_id="line_operations",
        title="ライン稼働状況",
        columns=[
            TableColumn(key="line_name",  label="ライン名"),
            TableColumn(key="active_min", label="稼働時間(分)", format="number", align="right"),
            TableColumn(key="order_min",  label="作業時間(分)", format="number", align="right"),
            TableColumn(key="util_rate",  label="稼働率",       format="percent", align="right"),
        ],
        rows=line_util_rows,
        row_id_key="line_id",
        allow_download=True,
    )

    table_sections = [section_schedule, section_unfinished, section_line_ops]

    # ── アラート ─────────────────────────────────────────────────────────────
    alerts = _build_alerts(issues)

    # ── サマリー ─────────────────────────────────────────────────────────────
    summary = {
        "completed_orders":    kpi.get("completed_orders", 0),
        "total_orders":        kpi.get("total_orders", 0),
        "completion_rate":     kpi.get("completion_rate", 0.0),
        "total_cost":          kpi.get("total_cost", 0.0),
        "unfinished_count":    len(unfinished),
        "active_lines":        len(line_ops),
    }

    return {
        "domain":         "energy_cost_aware_scheduler",
        "feasible":       True,
        "summary":        summary,
        "kpi_cards":      kpi_cards,
        "schedule":       schedule,
        "line_ops":       line_ops,
        "unfinished":     unfinished,
        "table_sections": table_sections,
        "alerts":         alerts,
        "raw_kpi":        kpi,
    }


def _build_alerts(issues: List[Dict]) -> List[Dict]:
    """issues から UI 表示用アラートを生成する。"""
    alerts = []
    for issue in issues:
        severity = issue.get("severity", "INFO")
        if severity in ("CRITICAL", "WARNING"):
            alerts.append({
                "id":       issue.get("id", ""),
                "severity": severity,
                "title":    issue.get("title", ""),
                "message":  issue.get("message", ""),
            })
    return alerts