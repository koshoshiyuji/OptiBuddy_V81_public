"""
Backend/dsl_transformer/nurse_shift_weekly_cap_ui_converter.py

Solver Output DSL → UI DSL 変換（NurseShiftWeeklyCap）

ui_dsl 構造:
  domain:          "nurse_shift_weekly_cap"
  feasible:        bool
  summary:         KPI サマリ
  kpi_cards:       KPI カード定義リスト
  shift_matrix:    日×タスク×スタッフのマトリックス（専用ビュー表示用）
  table_sections:  GenericResultTable 用
  alerts:          Human-in-the-Loop アラート
  raw_kpi:         生KPI数値
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional

from i18n.nurse_shift_weekly_cap_messages import t


def convert_nurse_shift_weekly_cap_to_ui(
    solver_output: Dict[str, Any],
    business_dsl: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """Solver Output DSL → UI DSL 変換。"""

    from dsl_transformer.table_sections import build_table_section, TableColumn

    feasible  = solver_output.get("feasible", False)
    solutions = solver_output.get("solutions", [])
    issues    = solver_output.get("issues", [])

    if not feasible or not solutions:
        return {
            "domain":         "nurse_shift_weekly_cap",
            "feasible":       False,
            "summary":        {},
            "kpi_cards":      [],
            "shift_matrix":   [],
            "table_sections": [],
            "alerts":         _build_alerts(issues),
            "raw_kpi":        {},
        }

    sol     = solutions[0]
    metrics = sol.get("metrics", {})
    tasks   = sol.get("tasks", [])               # assigned_tasks
    summary = sol.get("task_summary", [])         # タスク別集計

    # ── KPI カード ──────────────────────────────────────────────────────────
    total_cost         = metrics.get("total_cost", 0)
    assigned_count     = metrics.get("assigned_count", 0)
    understaffed_count = metrics.get("understaffed_count", 0)
    coverage_rate      = metrics.get("coverage_rate", 0.0)
    solve_time         = metrics.get("solve_time", 0.0)
    preference_rate    = metrics.get("preference_satisfaction_rate", 1.0)
    unmet_preference_count = metrics.get("unmet_preference_count", 0)

    # kpi_cards の各要素は Frontend/src/app/studio/views/Generic4DSLView.tsx の
    # KpiCard型（id/label/value/sub?/color/icon?）と1:1で対応させる必要がある。
    # 以前は "id" が無く "color" の代わりに独自の "accent" キーを使っていたため、
    # Generic4DSLView側のkey={card.id}が常にundefinedになり、Reactの
    # "Each child in a list should have a unique key prop" 警告が出ていた
    # （加えて左端のアクセントカラーも常にundefinedで無効化されていた）。
    # truck_dispatcher_ui_converter.py の _build_kpi_cards と同じ契約に揃える。
    # 2026-07-30 i18n対応: label/subはBackend/i18n/nurse_shift_weekly_cap_messages.py
    # 経由の辞書引きに変更（DESIGN_2026-07-29_nurse_shift_i18n_implementation_spec.md
    # 1-2節）。idはキー名としてそのまま流用している。
    kpi_cards = [
        {
            "id":    "total_cost",
            "label": t("kpi.total_cost.label"),
            "value": f"¥{total_cost:,}",
            "color": "#f97316",
            "sub":    t("kpi.total_cost.sub"),
        },
        {
            "id":    "assigned_count",
            "label": t("kpi.assigned_count.label"),
            "value": str(assigned_count),
            "color": "#00e5ff",
            "sub":    t("kpi.assigned_count.sub"),
        },
        {
            "id":    "coverage_rate",
            "label": t("kpi.coverage_rate.label"),
            "value": f"{round(coverage_rate * 100)}%",
            "color": "#50fa7b" if coverage_rate >= 1.0 else "#ffb86c" if coverage_rate >= 0.8 else "#ff4d4d",
            "sub":    t("kpi.coverage_rate.sub", understaffed_count=understaffed_count),
        },
        {
            "id":    "preference_rate",
            "label": t("kpi.preference_rate.label"),
            "value": f"{round(preference_rate * 100)}%",
            "color": "#50fa7b" if preference_rate >= 1.0 else "#ffb86c" if preference_rate >= 0.6 else "#ff4d4d",
            "sub":    t("kpi.preference_rate.sub", unmet_preference_count=unmet_preference_count),
        },
        {
            "id":    "solve_time",
            "label": t("kpi.solve_time.label"),
            "value": f"{solve_time:.2f}s",
            "color": "#6272a4",
            "sub":    "",
        },
    ]

    # ── シフトマトリックス（日×タスク ピボット）─────────────────────────────
    shift_matrix = _build_shift_matrix(tasks, summary)

    # ── table_sections（V8.6準拠: GenericResultTable 用）────────────────────

    # セクション1: 割り当て一覧
    assignment_rows = []
    for at in sorted(tasks, key=lambda x: (x.get("day", 0), x.get("start", 0))):
        day        = at.get("day", 0)
        start_abs  = at.get("start", 0)
        end_abs    = at.get("end", 0)
        start_disp = _abs_min_to_hhmm(start_abs % 1440)
        end_disp   = _abs_min_to_hhmm(end_abs % 1440)
        assignment_rows.append({
            "assignment_id": at["assignment_id"],
            "day":           f"Day {day}",
            "task_id":       at["task_id"],
            "staff_name":    at.get("staff_name", at["staff_id"]),
            "grade":         at["grade"],
            "time_range":    f"{start_disp} — {end_disp}",
            "duration_h":    round(at.get("duration", 0) / 60, 1),
            # 2026-07-30 i18n対応: 完成済み文字列("🌙 夜勤"等)ではなくbool値をそのまま
            # 渡す。表示直前の絵文字+ラベル解決はFrontend側（GenericResultTable.tsx、
            # format="nightShift"）が行う。DESIGN_2026-07-29_nurse_shift_i18n_
            # implementation_spec.md 1-2節「行データ内の埋め込み値」参照。
            "night_shift":   bool(at.get("is_night_shift")),
            "cost":          at.get("cost", 0),
        })

    section_assignments = build_table_section(
        section_id="assignments",
        title=t("table.section.assignments.title"),
        columns=[
            TableColumn(key="day",        label=t("table.column.day")),
            TableColumn(key="task_id",    label=t("table.column.task_id")),
            TableColumn(key="staff_name", label=t("table.column.staff_name")),
            TableColumn(key="grade",      label=t("table.column.grade"), format="badge"),
            TableColumn(key="time_range", label=t("table.column.time_range")),
            TableColumn(key="duration_h", label=t("table.column.duration_h"), format="number", unit="h"),
            TableColumn(key="night_shift", label=t("table.column.night_shift"), format="nightShift"),
            TableColumn(key="cost",       label=t("table.column.cost"), format="currency", align="right"),
        ],
        rows=assignment_rows,
        row_id_key="assignment_id",
    )

    # セクション2: タスク別充足状況
    task_coverage_rows = []
    for ts in sorted(summary, key=lambda x: (x.get("day", 0), x.get("start", 0))):
        required = ts.get("required_count", 1)
        assigned = ts.get("assigned_count", 0)
        shortage = max(0, required - assigned)
        status   = "OK" if shortage == 0 else "CRITICAL"
        task_coverage_rows.append({
            "task_id":         ts["task_id"],
            "task_name":       ts.get("task_name", ts["task_id"]),
            "day":             f"Day {ts.get('day', 0)}",
            "required_count":  required,
            "assigned_count":  assigned,
            "chief_count":     ts.get("chief_count", 0),
            "shortage":        shortage,
            "status":          status,
            # 2026-07-30 i18n対応: bool値をそのまま渡す（night_shift列と同様）。
            "is_night":        bool(ts.get("is_night_shift")),
            "_severity_val":   "CRITICAL" if shortage > 0 else "INFO",
        })

    section_coverage = build_table_section(
        section_id="task_coverage",
        title=t("table.section.task_coverage.title"),
        columns=[
            TableColumn(key="day",            label=t("table.column.day")),
            TableColumn(key="task_name",      label=t("table.column.task_name")),
            TableColumn(key="is_night",       label=t("table.column.is_night"), format="nightShiftIcon"),
            TableColumn(key="required_count", label=t("table.column.required_count"), format="number", align="right"),
            TableColumn(key="assigned_count", label=t("table.column.assigned_count"), format="number", align="right"),
            TableColumn(key="chief_count",    label=t("table.column.chief_count"), format="number", align="right"),
            TableColumn(key="shortage",       label=t("table.column.shortage"), format="number", align="right"),
            TableColumn(key="status",         label=t("table.column.status"), format="badge"),
        ],
        rows=task_coverage_rows,
        row_id_key="task_id",
        severity_key="_severity_val",
    )

    # セクション3: スタッフ別稼働サマリ
    staff_summary_map: Dict[str, Dict] = {}
    for at in tasks:
        sid = at["staff_id"]
        if sid not in staff_summary_map:
            staff_summary_map[sid] = {
                "staff_id":     sid,
                "staff_name":   at.get("staff_name", sid),
                "grade":        at["grade"],
                "total_tasks":  0,
                "night_tasks":  0,
                "total_hours":  0.0,
                "total_cost":   0,
            }
        staff_summary_map[sid]["total_tasks"]  += 1
        staff_summary_map[sid]["night_tasks"]  += 1 if at.get("is_night_shift") else 0
        staff_summary_map[sid]["total_hours"]  += at.get("duration", 0) / 60
        staff_summary_map[sid]["total_cost"]   += at.get("cost", 0)

    staff_rows = [
        {
            **v,
            "total_hours": round(v["total_hours"], 1),
        }
        for v in sorted(staff_summary_map.values(), key=lambda x: -x["total_cost"])
    ]

    # 週間夜勤上限（ローリングウィンドウ）拡張: ソルバーが計算済みの
    # staff_rolling_night_counts（スタッフごとの「期間中どの7日間ウィンドウでも
    # 最大何回夜勤か」）を、table_sections.pyの汎用extra_metrics差し込み口経由で
    # 列に反映する。この列を見れば「合計夜勤回数」ではなく「7日間ウィンドウ内の
    # 最大値」＝実際に制約が効いているかを直接確認できる。
    staff_rolling_night_counts = sol.get("staff_rolling_night_counts", {})
    # 2026-07-30 i18n対応: extra_metricsの新スキーマ（table_sections.py V8.8）に合わせ、
    # {row_id: {metric_key: {"label": ..., "value": ...}}} 形式に変更。
    # metric_key="rolling_night_count"はCSVヘッダーとして言語非依存な識別子になる。
    rolling_night_count_label = t("table.extraMetric.rollingNightCount")
    staff_extra_metrics = {
        sid: {"rolling_night_count": {"label": rolling_night_count_label, "value": count}}
        for sid, count in staff_rolling_night_counts.items()
    }

    section_staff = build_table_section(
        section_id="staff_summary",
        title=t("table.section.staff_summary.title"),
        columns=[
            TableColumn(key="staff_name",  label=t("table.column.staff_name")),
            TableColumn(key="grade",       label=t("table.column.grade"), format="badge"),
            TableColumn(key="total_tasks", label=t("table.column.total_tasks"), format="number", align="right"),
            TableColumn(key="night_tasks", label=t("table.column.night_tasks"), format="number", align="right"),
            TableColumn(key="total_hours", label=t("table.column.total_hours"), format="number", unit="h", align="right"),
            TableColumn(key="total_cost",  label=t("table.column.total_cost"), format="currency", align="right"),
        ],
        rows=staff_rows,
        row_id_key="staff_id",
        extra_metrics=staff_extra_metrics,
    )

    # ── アラート ──────────────────────────────────────────────────────────
    alerts = _build_alerts(issues)

    # ── gantt_tasks（Generic4DSLView の汎用Gantt表示用）───────────────────
    # Frontend/src/domain/types.ts の GanttTask（2026-07-14導入、ドメイン非依存）
    # の形で出力する。YARD/VESSEL固有のcontainerId/operation/yard/ship/ports等は
    # 一切含めない（NurseShiftWeeklyCapはYARDの特殊事情に依存しないため）。
    # start/end は分単位で保持しているため、TaskGanttが前提とする秒単位に変換する
    # （SEC_PER_UNIT=60）。
    gantt_tasks = _build_gantt_tasks(tasks)

    return {
        "domain":         "nurse_shift_weekly_cap",
        "feasible":       True,
        "summary": {
            "total_cost":                   total_cost,
            "assigned_count":               assigned_count,
            "understaffed_count":           understaffed_count,
            "coverage_rate":                coverage_rate,
            "preference_satisfaction_rate": preference_rate,
            "unmet_preference_count":       unmet_preference_count,
        },
        "kpi_cards":      kpi_cards,
        "shift_matrix":   shift_matrix,
        "table_sections": [section_assignments, section_coverage, section_staff],
        "alerts":         alerts,
        "raw_kpi":        metrics,
        "gantt_tasks":    gantt_tasks,
        "gantt_makespan": max((at.get("end", 0) for at in tasks), default=0) * 60,
    }


def _build_shift_matrix(
    tasks: List[Dict],
    summary: List[Dict],
) -> List[Dict]:
    """
    日×タスクのシフトマトリックスを構築する。
    専用Viewのシフト表表示に使用する。
    """
    # タスク別に割り当てスタッフをまとめる
    task_assignments: Dict[str, List[str]] = {}
    for at in tasks:
        tid = at["task_id"]
        task_assignments.setdefault(tid, []).append(
            f"{at.get('staff_name', at['staff_id'])}({'C' if at['grade'] == 'CHIEF' else 'S'})"
        )

    matrix = []
    for ts in sorted(summary, key=lambda x: (x.get("day", 0), x.get("start", 0))):
        tid      = ts["task_id"]
        required = ts.get("required_count", 1)
        assigned = ts.get("assigned_count", 0)
        matrix.append({
            "task_id":      tid,
            "task_name":    ts.get("task_name", tid),
            "day":          ts.get("day", 0),
            "start":        ts.get("start", 0),
            "end":          ts.get("end", 0),
            "is_night":     ts.get("is_night_shift", False),
            "required":     required,
            "assigned":     assigned,
            "shortage":     max(0, required - assigned),
            "staff_list":   task_assignments.get(tid, []),
        })
    return matrix


def _build_gantt_tasks(tasks: List[Dict]) -> List[Dict]:
    """
    割り当て結果（assigned tasks）を、Frontend/src/domain/types.ts の GanttTask
    （ドメイン非依存、2026-07-14導入）の形に変換する。
    YARD固有のcontainerId/operation/yard/ship/ports等は意図的に含めない
    （operationフィールドが無いことで、TaskGantt.tsx側は自動的に汎用の
    colorKeyベース色分け・凡例モードに切り替わる）。
    """
    gantt_tasks = []
    for at in tasks:
        start_sec  = int(at.get("start", 0)) * 60
        end_sec    = int(at.get("end", 0)) * 60
        staff_id   = at.get("staff_id", "")
        staff_name = at.get("staff_name", staff_id)
        task_id    = at.get("task_id", "")
        gantt_tasks.append({
            "id":            at.get("assignment_id", f"{staff_id}_{task_id}"),
            "resourceId":    staff_id,
            "resourceLabel": staff_name,
            "label":         task_id,
            "colorKey":      task_id,
            "start":         start_sec,
            "end":           end_sec,
            "status":        "scheduled",
        })
    return gantt_tasks


def _build_alerts(issues: List[Dict]) -> List[Dict]:
    """issues から CRITICAL/WARNING のアラートを生成する。"""
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


def _abs_min_to_hhmm(abs_min: int) -> str:
    """絶対分数（日内）を 'HH:MM' 文字列に変換する。"""
    abs_min = int(abs_min) % 1440
    h = abs_min // 60
    m = abs_min % 60
    return f"{h:02d}:{m:02d}"