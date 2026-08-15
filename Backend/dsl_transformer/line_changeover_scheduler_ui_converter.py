"""
line_changeover_scheduler_ui_converter.py
Solver Output DSL → UI DSL 変換
"""

from typing import Any, Dict, List, Optional

from dsl_transformer.table_sections import build_table_section, TableColumn


def convert_line_changeover_scheduler_to_ui(
    solver_output: dict,
    business_dsl: Optional[dict] = None,
) -> dict:
    """
    Solver Output DSL → UI DSL。
    ui_dsl["domain"] = "line_changeover_scheduler" を必ずセット。
    """
    solutions = solver_output.get("solutions", [])
    issues    = solver_output.get("issues", [])
    meta      = solver_output.get("metadata", {})

    solution = solutions[0] if solutions else {}
    feasible  = solution.get("feasible", False)
    schedule: List[Dict[str, Any]] = solution.get("schedule", [])
    kpi       = solution.get("kpi", {})
    # 2026-07-18追記: solution["makespan"]/kpi["objective_value"]/kpi["solve_time"]は、
    # infeasible時にsolver側がキー自体は残したまま値をNoneで返す（line_changeover_scheduler_solver.py
    # の infeasible_solution 構築箇所参照）。.get(key, default) はキーが「無い」時しか
    # defaultを使わないため、値がNoneのときはNoneがそのまま通り、round(None, 1)で
    # TypeErrorになっていた（実機ログで/baselineおよびrelax_verifyのドライランが
    # 複数回この例外で失敗していたことを確認）。NurseShiftWeeklyCapの
    # quantity_requirement_mode取得で踏んだのと同じ「.get()のdefaultはキー欠落時のみ」
    # という既知の落とし穴なので、ここでも明示的なNoneチェックに直す。
    makespan  = solution.get("makespan") or 0
    _obj_raw  = kpi.get("objective_value")
    obj_val   = _obj_raw if _obj_raw is not None else makespan
    _solve_time_raw = kpi.get("solve_time")
    solve_time_val  = _solve_time_raw if _solve_time_raw is not None else 0

    # ── KPI カード ──
    kpi_cards = [
        {"label": "メイクスパン", "value": makespan, "unit": "分", "color": "#3b82f6"},
        {"label": "スケジュール済みタスク", "value": kpi.get("task_count", len(schedule)), "unit": "件", "color": "#10b981"},
        {"label": "目的関数値", "value": round(obj_val, 1), "unit": "", "color": "#8b5cf6"},
        {"label": "ソルブ時間", "value": round(solve_time_val, 1), "unit": "秒", "color": "#f97316"},
    ]

    # ── アラート（issues の CRITICAL を前面に出す） ──
    alerts = [
        {"id": i["id"], "severity": i["severity"], "title": i["title"], "message": i.get("message", "")}
        for i in issues if i.get("severity") in ("CRITICAL", "WARNING")
    ]

    # ── スケジュール一覧テーブル ──
    schedule_rows = []
    for s in sorted(schedule, key=lambda x: x.get("start", 0)):
        reqs_str = ", ".join(
            f"{r.get('resource_id', '')}×{r.get('amount', 1)}"
            for r in s.get("resource_requirements", [])
        )
        schedule_rows.append({
            "task_id":    s["task_id"],
            "task_name":  s.get("task_name", s["task_id"]),
            "start":      s.get("start", 0),
            "end":        s.get("end", 0),
            "duration":   s.get("duration", 0),
            "resources":  reqs_str or "—",
        })

    schedule_section = build_table_section(
        section_id="lcs_schedule",
        title="タスクスケジュール一覧",
        columns=[
            TableColumn(key="task_id",   label="ID",        align="left"),
            TableColumn(key="task_name", label="タスク名",  align="left"),
            TableColumn(key="start",     label="開始(分)",  align="right", format="number"),
            TableColumn(key="end",       label="終了(分)",  align="right", format="number"),
            TableColumn(key="duration",  label="所要(分)",  align="right", format="number"),
            TableColumn(key="resources", label="使用リソース", align="left"),
        ],
        rows=schedule_rows,
        row_id_key="task_id",
        allow_download=True,
    )

    # ── 資源利用サマリーテーブル ──
    resource_usage = _build_resource_usage(schedule, business_dsl)
    resource_section = build_table_section(
        section_id="lcs_resources",
        title="リソース利用サマリー",
        columns=[
            TableColumn(key="resource_id",   label="リソースID",   align="left"),
            TableColumn(key="resource_name", label="リソース名",   align="left"),
            TableColumn(key="capacity",      label="容量",         align="right", format="number"),
            TableColumn(key="assigned_tasks", label="割当タスク数", align="right", format="number"),
            TableColumn(key="total_load",    label="総負荷(分)",   align="right", format="number"),
        ],
        rows=resource_usage,
        row_id_key="resource_id",
        allow_download=True,
    )

    return {
        "domain":         "line_changeover_scheduler",
        "feasible":       feasible,
        "summary": {
            "makespan":    makespan,
            "task_count":  len(schedule),
            "instance":    meta.get("instance_name", ""),
        },
        "kpi_cards":      kpi_cards,
        "schedule":       schedule,
        "table_sections": [schedule_section, resource_section],
        "alerts":         alerts,
        "raw_kpi":        kpi,
    }


def _build_resource_usage(
    schedule: List[Dict[str, Any]],
    business_dsl: Optional[dict],
) -> List[Dict[str, Any]]:
    """リソースごとの利用統計を集計する。"""
    resource_info: Dict[str, Dict] = {}
    if business_dsl:
        for r in business_dsl.get("resources", []):
            rid = str(r["id"])
            resource_info[rid] = {
                "resource_name": r.get("name", rid),
                "capacity":      int(r.get("capacity", 1)),
            }

    stats: Dict[str, Dict] = {}
    for s in schedule:
        for req in s.get("resource_requirements", []):
            rid    = str(req.get("resource_id", ""))
            amount = int(req.get("amount", 1))
            dur    = int(s.get("duration", 0))
            if rid not in stats:
                stats[rid] = {"assigned_tasks": 0, "total_load": 0}
            stats[rid]["assigned_tasks"] += 1
            stats[rid]["total_load"]     += dur * amount

    rows = []
    for rid, s in sorted(stats.items()):
        info = resource_info.get(rid, {})
        rows.append({
            "resource_id":    rid,
            "resource_name":  info.get("resource_name", rid),
            "capacity":       info.get("capacity", 1),
            "assigned_tasks": s["assigned_tasks"],
            "total_load":     s["total_load"],
        })
    return rows