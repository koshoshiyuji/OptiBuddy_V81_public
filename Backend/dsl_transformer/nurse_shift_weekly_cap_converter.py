"""
Backend/dsl_transformer/nurse_shift_weekly_cap_converter.py

Business DSL → Solver Input DSL 変換（NurseShiftWeeklyCap）

【重要な修正履歴】
旧実装は、実際のBusiness DSLスキーマとは異なる前提
（tasks[*].start_time/end_time のHH:MM文字列、day 0起点、
 staff[*].available_days による日単位可用性、config直下の
 グローバルwork_limits等）で書かれており、以下を実質的に破壊していた:
  - タスクの実際の時間窓（start_window/end_window は常に存在しないものとして
    無視され、全タスクが day*1440+8:00〜16:00 に潰れていた）
  - required_skills（丸ごと欠落し、職種チェックが常に無効化）
  - staff[*].availability（時間帯）（available_daysに置き換えられ、
    実質「毎日いつでも可」に緩和されていた）
  - staff[*].work_limits（スタッフ個別の連続夜勤・休憩・日次上限が
    config全体のグローバルデフォルト値に置き換えられていた）

【2026-07-13 スキーマ変更】
  - tasks[*].required_skills / required_certifications を廃止し、単一の
    requirement（and/or/not条件式）に統一した。旧2フィールドは「スキルはOR、
    資格はAND」という暗黙・不統一な判定だったため
    （solvers/base/requirement_expr.py 参照）。
  - staff[*].preferences.forbidden_tasks / must_assign_tasks / conflict_with を
    廃止した。solver.pyが元々一切参照しておらず、指定しても無視される
    死んだフィールドだったため（desired_tasksのみ実際にソフト制約として機能する
    ため残す）。

本実装は、実際にDBへ登録されているシナリオJSON
（Backend/dsl_repository/scenarios/nurse_shift_weekly_cap_*.json）の
実フィールド名にそのまま合わせ、変換らしい変換（単位変換・再計算）を
行わずに素通しする。start_window/end_window は既にプランニング
ホライズン全体（0〜4320分など）にわたる絶対分数として与えられている
ため、日×時刻からの再計算は不要。
"""

from __future__ import annotations

from typing import Any, Dict

def convert_nurse_shift_weekly_cap_to_solver(business_dsl: Dict[str, Any]) -> Dict[str, Any]:
    """Business DSL → Solver Input DSL 変換。フィールド名はそのまま素通しする。"""

    staff_raw  = business_dsl.get("staff", [])
    tasks_raw  = business_dsl.get("tasks", [])
    config_raw = business_dsl.get("config", {})

    # スタッフ変換
    staff_list = []
    for s in staff_raw:
        staff_list.append({
            "id":                str(s["id"]),
            "name":              s.get("name", str(s["id"])),
            "grade":             s.get("grade", "STANDARD"),
            "skills":            s.get("skills", []),
            "certifications":    s.get("certifications", []),
            "hourly_rate":       int(s.get("hourly_rate", 2000)),
            "availability":      s.get("availability", {"start": 0, "end": 1440}),
            "work_limits":       s.get("work_limits", {}),
            "preferences":       s.get("preferences", {"desired_tasks": []}),
            # 看護師属性拡張
            "qualification_type":  s.get("qualification_type", "RN"),        # RN=正看護師, LPN=准看護師
            "experience_years":    float(s.get("experience_years", 0.0)),
            "has_leader_cert":     bool(s.get("has_leader_cert", False)),
            "employment_type":     s.get("employment_type", "full_time"),     # full_time / part_time
            "approved_days_off":   s.get("approved_days_off", []),            # 承認済み休暇日リスト（day番号）
            "paid_leave_days":     s.get("paid_leave_days", []),              # 有給日リスト（day番号）
        })

    # タスク変換
    tasks_list = []
    for t in tasks_raw:
        tasks_list.append({
            "id":                      str(t["id"]),
            "name":                    t.get("name", str(t["id"])),
            "day":                     int(t.get("day", 1)),
            "start_window":            int(t.get("start_window", 0)),
            "end_window":              int(t.get("end_window", 1440)),
            "duration":                int(t.get("duration", 60)),
            "shift_type":              t.get("shift_type", "day"),            # day / evening / night / off
            "is_night_shift":          bool(t.get("is_night_shift", False)),
            "requirement":             t.get("requirement"),
            "required_count":          int(t.get("required_count", 1)),
            "min_chiefs":              int(t.get("min_chiefs", 0)),
            "novice_requires_senior":  bool(t.get("novice_requires_senior", False)),
            # タスク単位の数量要件hard/soft上書き（ヒアリング§4-1補足欄の例外指定）。
            # 未指定ならNoneのまま通過させ、solver側でconfig.quantity_requirement_mode
            # （ドメイン既定値）にフォールバックさせる。
            "quantity_requirement_mode": t.get("quantity_requirement_mode"),
        })

    # config変換
    config = {
        "time_limit":                       int(config_raw.get("time_limit", 60)),
        "preference_penalty_weight":        int(config_raw.get("preference_penalty_weight", 200)),
        "min_preference_satisfaction_rate": config_raw.get("min_preference_satisfaction_rate", 0.6),
        "operation_start":                  config_raw.get("operation_start", 0),
        "shifts":                           config_raw.get("shifts", []),
        "breaks":                           config_raw.get("breaks", []),
        # 週定義（week_number → [day_numbers] のマッピング、または自動計算用）
        "week_definition":                  config_raw.get("week_definition", {}),
        # 夜勤公平配分ペナルティ重み
        "night_shift_fairness_weight":      int(config_raw.get("night_shift_fairness_weight", 100)),
        # 新人看護師スーパービジョンルール
        "novice_supervision_rule":          config_raw.get("novice_supervision_rule", {
            "novice_experience_threshold_years":  1.0,
            "senior_experience_threshold_years":  3.0,
        }),
        # 緩和優先順位
        "relaxation_priority_order":        config_raw.get("relaxation_priority_order", [
            "desired_shift", "consecutive_night_limit", "fairness",
        ]),
        # 2026-07-16修正（Gate2 DSL⇔converterトレーサビリティチェックで発覚）:
        # rolling_window_days / relax_incomplete_window_at_period_end はDSLシナリオに
        # 存在するのにconverterが一度も出力せず、solver.py側がwindow_days=7を
        # ハードコードしていたため、DSLでこの値を変えても常に7日固定で解かれる
        # 死んだ設定になっていた（HANDOFF系ではなくGate2トレーサビリティ試験実装で発見）。
        # ローリングウィンドウ日数（週次夜勤上限のウィンドウ長。solver.py側で
        # add_rolling_window_cap_constraints(window_days=...) に渡す）
        "rolling_window_days":              int(config_raw.get("rolling_window_days", 7)),
        # 期間末尾の不完全ウィンドウの扱い。現在の共通コア
        # （solvers/base/rolling_window.py）は常に「緩和する」実装のみを持ち、
        # 非緩和モードは未実装のため、この値がFalseの場合はsolver.py側で
        # 警告ログを出す（無言で無視しない）。
        "relax_incomplete_window_at_period_end": bool(config_raw.get("relax_incomplete_window_at_period_end", True)),
        # 数量要件hard/soft（ヒアリング§4-1）のドメイン既定値。"hard" または "soft"。
        # 未指定時はsolver側でsolvers.base.quantity_requirement.DEFAULT_QUANTITY_REQUIREMENT_MODE
        # （"soft"、ヒアリングシート未記入時の既定と一致）にフォールバックする。
        # 2026-07-18追加（経緯: docs/DESIGN_2026-07-18_layer_ab_pattern_library.md 2-2-2節、
        # 従来この設定自体が存在せず常にハード等式扱いだったバグの修正）。
        "quantity_requirement_mode": config_raw.get("quantity_requirement_mode"),
    }

    return {
        "problem_class":  "NurseShiftWeeklyCap",
        "meta":           business_dsl.get("meta", {}),
        "staff":          staff_list,
        "tasks":          tasks_list,
        "config":         config,
        "issue_statuses": business_dsl.get("issue_statuses", {}),
    }

