"""
Backend/solvers/shift_rotation_scheduler_solver.py

ShiftRotationSchedulerSolver — 循環シフトテンプレートスケジューラ
=================================================================

CSPLib prob087 (Rotating Rostering Problem) の定式化。

【設計方針】
- 決定変数: W週間 × 7曜日 のシフトテンプレート変数 shift[w][d]（整数変数）のみ。
  W = 従業員数 = ローテーション周期週数。
- 従業員 k（k=0..W-1）の週 w・曜日 d の実勤務は
  shift[(w + k) % W][d] で導出（新たな決定変数を作らない）。
- 目的: feasibility のみ（数値目標なし、制約を満たすテンプレートが1つ見つかれば十分）。

# solver_input keys（converterが必ず出力し、solverがここのみを参照するキー一覧）:
#   problem_class: str
#   num_employees: int         — 従業員数 W（= 周期週数）
#   shifts: List[Dict]         — シフト定義 [{id, name, type}, ...]
#                                type: "off" | "early" | "late" | "night"
#   daily_requirements: List[Dict]  — 曜日×シフト必要人数
#                                [{day_of_week: 0..6, shift_id: str, required: int}]
#   constraints: Dict          — 制約パラメータ
#     min_consecutive: int     — 最低連続日数（既定2）
#     max_consecutive: int     — 最大連続日数（既定4）
#     min_days_off_per_14: int — 14日窓内の最低休日数（既定2）
#   config: Dict               — タイムリミット等
#   issue_statuses: Dict
"""

from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

# シフトタイプの順序（前進のみ許可: OFF → EARLY → LATE → NIGHT → OFF ...）
# OFF は「リセット」として扱い、OFFの後はどのシフトへも進める
SHIFT_TYPE_ORDER = {"off": 0, "early": 1, "late": 2, "night": 3}


class ShiftRotationSchedulerSolver:
    """循環シフトテンプレートスケジューラ（CSPLib prob087ベース）"""

    def __init__(self, solver_input: dict) -> None:
        self.dsl = solver_input

    def solve(self) -> dict:
        try:
            config: Dict = self.dsl.get("config", {})
            from solvers.base.engine_select import get_solver_engine, CPSAT
            engine = get_solver_engine(config)
            if engine == CPSAT:
                return self._solve_with_cpsat()
            return self._solve_with_cpo()
        except Exception as e:
            logger.error(f"[ShiftRotationScheduler] solve() 例外: {e}", exc_info=True)
            from solvers.base.solver_error_result import build_solver_crash_issue, solver_crash_extra_fields
            result = {
                "status": "ok",
                "feasible": False,
                "metadata": {"problem_class": "ShiftRotationScheduler"},
                "solutions": [],
                "issues": [build_solver_crash_issue(e)],
                "_solver_version": "shift_rotation_scheduler_v1.0",
            }
            result.update(solver_crash_extra_fields(e))
            return result

    def _solve_with_cpo(self) -> dict:
        from docplex.cp.model import CpoModel

        dsl = self.dsl
        W = int(dsl.get("num_employees", 0))
        if W <= 0:
            return self._infeasible("invalid_input", "従業員数(num_employees)が0以下です。")

        shifts_def: List[Dict] = dsl.get("shifts", [])
        if not shifts_def:
            return self._infeasible("invalid_input", "shifts が空です。")

        daily_reqs: List[Dict] = dsl.get("daily_requirements", [])
        constraints_cfg: Dict = dsl.get("constraints", {})
        config: Dict = dsl.get("config", {})
        issue_statuses: Dict = dsl.get("issue_statuses", {})

        min_consec = int(constraints_cfg.get("min_consecutive", 2))
        max_consec = int(constraints_cfg.get("max_consecutive", 4))
        min_off_per_14 = int(constraints_cfg.get("min_days_off_per_14", 2))
        time_limit = float(config.get("time_limit_sec", 30))

        # シフトID → インデックス、インデックス → タイプ
        shift_ids = [s["id"] for s in shifts_def]
        shift_id_to_idx = {s["id"]: i for i, s in enumerate(shifts_def)}
        shift_types = [s.get("type", "off") for s in shifts_def]
        n_shifts = len(shift_ids)

        # OFF シフトのインデックス集合
        off_indices = {i for i, t in enumerate(shift_types) if t == "off"}
        if not off_indices:
            return self._infeasible("invalid_input", "type='off' のシフトが定義されていません。")

        mdl = CpoModel(name="ShiftRotationScheduler")

        # ── 決定変数: shift[w][d] (w=0..W-1, d=0..6) ──────────────────────────
        # 各変数の値域は 0..n_shifts-1（シフトインデックス）
        shift = [[mdl.integer_var(0, n_shifts - 1, name=f"s_w{w}_d{d}")
                  for d in range(7)]
                 for w in range(W)]

        # ── 制約1: 土曜(d=5)と日曜(d=6)は同じシフト ───────────────────────────
        for w in range(W):
            mdl.add(shift[w][5] == shift[w][6])

        # ── ヘルパー: 循環インデックス ──────────────────────────────────────────
        # テンプレートはW週7日=W*7日の「輪っか」
        # 線形インデックス i = w*7 + d（i=0..W*7-1）
        total_days = W * 7

        def idx(w: int, d: int) -> int:
            return w * 7 + d

        def wrap(i: int) -> tuple:
            """線形インデックス i を (w, d) に変換（循環）"""
            i = i % total_days
            return i // 7, i % 7

        def sv(w: int, d: int):
            """shift変数をwrap-aroundで取得"""
            w2, d2 = wrap(w * 7 + d)
            return shift[w2][d2]

        # ── 制約2: 連続日数 (min_consec..max_consec) ───────────────────────────
        # 輪っか全日を順に見て、シフトが変わる「遷移点」を表す補助変数を作る
        # approach: 各日 i について、連続ブロックの長さを管理するのではなく
        # 「ある日 i からの同一シフトの連続長さ」を直接求めるのは困難なため、
        # 「任意の連続 (min_consec+1) 日の中に変化なしのパターンを禁止」と
        # 「任意の連続 (max_consec+1) 日が全て同じ → 禁止」で実現する。
        #
        # 実装: change[i] = 1 if shift[i] != shift[i-1] else 0（循環）
        # - max_consec 超過禁止: 連続 (max_consec+1) 日に変化点が0なら違反
        # - min_consec 未満禁止: change[i]=1 のとき、直前 min_consec-1 日の中に
        #   変化点があってはならない（= 連続長 < min_consec を作れない）

        change = [mdl.binary_var(name=f"chg_{i}") for i in range(total_days)]

        for i in range(total_days):
            wi, di = wrap(i)
            wi_prev, di_prev = wrap(i - 1)
            # change[i] = 1 iff shift[i] != shift[i-1]
            mdl.add(change[i] == (shift[wi][di] != shift[wi_prev][di_prev]))

        # max_consec 超過禁止: 連続 max_consec+1 日の change 合計 >= 1
        for i in range(total_days):
            window = [change[(i + j) % total_days] for j in range(max_consec + 1)]
            mdl.add(mdl.sum(window) >= 1)

        # min_consec 未満禁止: change[i]=1 のとき直前 (min_consec-1) 日に change なし
        # ⟺ change[i]=1 → change[i-1]=0 AND change[i-2]=0 ... change[i-(min_consec-1)]=0
        # ⟺ change[i] + sum_{j=1}^{min_consec-1} change[i-j] <= 1
        if min_consec >= 2:
            for i in range(total_days):
                window_prev = [change[(i - j) % total_days] for j in range(min_consec)]
                mdl.add(mdl.sum(window_prev) <= 1)

        # ── 制約3: シフト順序（重い方から軽い方へ休みなしに戻れない）────────────
        # ルール: OFF を挟まなければ type_order は単調非減少
        # 形式: shift[i]がOFFでない かつ shift[i-1]がOFFでない
        #       → SHIFT_TYPE_ORDER[type(shift[i])] >= SHIFT_TYPE_ORDER[type(shift[i-1])]
        #
        # CPO では型は変数値経由なので、type_order を整数配列として扱う。
        # order_of[shift_val] = SHIFT_TYPE_ORDER[shift_type] を補助変数で表現する。
        type_order_arr = [SHIFT_TYPE_ORDER.get(t, 0) for t in shift_types]  # index → order

        for i in range(total_days):
            wi, di = wrap(i)
            wi_prev, di_prev = wrap(i - 1)
            v_cur  = shift[wi][di]
            v_prev = shift[wi_prev][di_prev]
            # OFFかどうか: off_indices の値に等しいかどうか
            is_off_cur  = mdl.logical_or([v_cur  == oi for oi in off_indices])
            is_off_prev = mdl.logical_or([v_prev == oi for oi in off_indices])
            # 両方OFFでないとき、type_order は非減少
            # order_cur >= order_prev
            # 要素テーブルで type_order_arr[v] を表現
            order_cur  = mdl.element(type_order_arr, v_cur)
            order_prev = mdl.element(type_order_arr, v_prev)
            both_not_off = mdl.logical_not(mdl.logical_or(is_off_cur, is_off_prev))
            mdl.add(mdl.if_then(both_not_off, order_cur >= order_prev))

        # ── 制約4: 14日窓内に最低 min_off_per_14 日の休み ──────────────────────
        # 輪っか上の全14日窓
        for i in range(total_days):
            window_14 = []
            for j in range(14):
                wi, di = wrap(i + j)
                v = shift[wi][di]
                # OFFなら1、それ以外0 → 要素テーブルで実現
                is_off_arr = [1 if k in off_indices else 0 for k in range(n_shifts)]
                window_14.append(mdl.element(is_off_arr, v))
            mdl.add(mdl.sum(window_14) >= min_off_per_14)

        # ── 制約5: 曜日・シフトごとの必要人数（ハード等号）────────────────────
        # 従業員k の週w・曜日d の実勤務 = shift[(w+k)%W][d]
        # 必要人数: Σ_{k=0}^{W-1} [shift[(w+k)%W][d] == shift_idx] = required
        # → 全W週にわたって足した合計 = W_周期内で shift_idx が登場する回数
        # （W週に1回ずつ全従業員がローテーションするので、
        #   週wの曜日dに対して全kを足す = 全週w'=0..W-1 の曜日dの合計と等価）
        for req in daily_reqs:
            d = int(req["day_of_week"])  # 0..6
            shift_id = req["shift_id"]
            required = int(req["required"])
            if shift_id not in shift_id_to_idx:
                continue
            sidx = shift_id_to_idx[shift_id]
            # Σ_{w=0}^{W-1} [shift[w][d] == sidx] == required
            indicator_arr = [1 if k == sidx else 0 for k in range(n_shifts)]
            count_expr = mdl.sum(
                mdl.element(indicator_arr, shift[w][d]) for w in range(W)
            )
            mdl.add(count_expr == required)

        # ── ソルブ ──────────────────────────────────────────────────────────────
        logger.info(f"[ShiftRotationScheduler] solve() W={W}, shifts={n_shifts}, time_limit={time_limit}s")

        try:
            msol = mdl.solve(TimeLimit=time_limit, LogVerbosity="Quiet")
        except Exception as e:
            from solvers.base.ce_limit_lns import is_ce_limit_exceeded, CeLimitExceededError
            if is_ce_limit_exceeded(e):
                raise CeLimitExceededError(str(e)) from e
            raise

        if msol is None or not msol:
            logger.info("[ShiftRotationScheduler] 解なし (infeasible)")
            return {
                "status": "ok",
                "feasible": False,
                "metadata": {"problem_class": "ShiftRotationScheduler"},
                "solutions": [],
                "issues": [{
                    "id": "solve_failed",
                    "severity": "CRITICAL",
                    "category": "INFEASIBLE",
                    "title": "制約を満たすシフトテンプレートが見つかりませんでした",
                    "message": (
                        "指定された必要人数・連続日数・シフト順序などの制約を"
                        "同時に満たすローテーションテンプレートが存在しません。"
                        "必要人数や従業員数を見直してください。"
                    ),
                    "relatedContainerIds": [],
                }],
                "_solver_version": "shift_rotation_scheduler_v1.0",
            }

        # ── 解抽出 ──────────────────────────────────────────────────────────────
        template = []  # w=0..W-1, d=0..6 のシフトインデックス
        for w in range(W):
            week_row = []
            for d in range(7):
                val = msol.get_value(shift[w][d])
                week_row.append(int(val) if val is not None else 0)
            template.append(week_row)

        # 従業員ごとのスケジュール（k=0..W-1）
        employee_schedules = []
        for k in range(W):
            schedule_rows = []
            for w in range(W):
                row = []
                for d in range(7):
                    sidx = template[(w + k) % W][d]
                    row.append({
                        "shift_id":   shift_ids[sidx],
                        "shift_name": shifts_def[sidx]["name"],
                        "shift_type": shift_types[sidx],
                    })
                schedule_rows.append(row)
            employee_schedules.append({
                "employee_id":   f"EMP{k:02d}",
                "employee_name": f"従業員{k + 1}",
                "weeks":         schedule_rows,
            })

        # KPI
        total_shifts_assigned = sum(
            1
            for k in range(W)
            for w in range(W)
            for d in range(7)
            if shift_types[template[(w + k) % W][d]] != "off"
        )
        off_count = W * W * 7 - total_shifts_assigned

        solution = {
            "name":    "Plan A",
            "label":   "循環シフトテンプレート",
            "feasible": True,
            "template": template,
            "template_shift_ids": [
                [shift_ids[template[w][d]] for d in range(7)]
                for w in range(W)
            ],
            "employee_schedules": employee_schedules,
            "kpi": {
                "num_employees":        W,
                "cycle_weeks":          W,
                "total_working_days":   total_shifts_assigned,
                "total_off_days":       off_count,
                "working_days_per_emp": round(total_shifts_assigned / W, 1) if W > 0 else 0,
                "off_days_per_emp":     round(off_count / W, 1) if W > 0 else 0,
            },
        }

        logger.info(
            f"[ShiftRotationScheduler] 解発見: W={W}, "
            f"working_days={total_shifts_assigned}, off_days={off_count}"
        )

        return {
            "status":    "ok",
            "feasible":  True,
            "metadata":  {"problem_class": "ShiftRotationScheduler"},
            "solutions": [solution],
            "issues":    [],
            "_solver_version": "shift_rotation_scheduler_v1.0",
        }

    def _solve_with_cpsat(self) -> dict:
        """CP-SAT(CPMpy)版。_solve_with_cpo()と同一の制約セットを実装する(目的関数なし=feasibility)。"""
        import cpmpy as cp

        dsl = self.dsl
        W = int(dsl.get("num_employees", 0))
        if W <= 0:
            return self._infeasible("invalid_input", "従業員数(num_employees)が0以下です。")

        shifts_def: List[Dict] = dsl.get("shifts", [])
        if not shifts_def:
            return self._infeasible("invalid_input", "shifts が空です。")

        daily_reqs: List[Dict] = dsl.get("daily_requirements", [])
        constraints_cfg: Dict = dsl.get("constraints", {})
        config: Dict = dsl.get("config", {})

        min_consec = int(constraints_cfg.get("min_consecutive", 2))
        max_consec = int(constraints_cfg.get("max_consecutive", 4))
        min_off_per_14 = int(constraints_cfg.get("min_days_off_per_14", 2))
        time_limit = float(config.get("time_limit_sec", 30))

        shift_ids = [s["id"] for s in shifts_def]
        shift_id_to_idx = {s["id"]: i for i, s in enumerate(shifts_def)}
        shift_types = [s.get("type", "off") for s in shifts_def]
        n_shifts = len(shift_ids)

        off_indices = {i for i, t in enumerate(shift_types) if t == "off"}
        if not off_indices:
            return self._infeasible("invalid_input", "type='off' のシフトが定義されていません。")

        m = cp.Model()

        shift = [[cp.intvar(0, n_shifts - 1, name=f"s_w{w}_d{d}")
                  for d in range(7)]
                 for w in range(W)]

        for w in range(W):
            m += (shift[w][5] == shift[w][6])

        total_days = W * 7

        def wrap(i: int) -> tuple:
            i = i % total_days
            return i // 7, i % 7

        change = [cp.boolvar(name=f"chg_{i}") for i in range(total_days)]

        for i in range(total_days):
            wi, di = wrap(i)
            wi_prev, di_prev = wrap(i - 1)
            m += (change[i] == (shift[wi][di] != shift[wi_prev][di_prev]))

        for i in range(total_days):
            window = [change[(i + j) % total_days] for j in range(max_consec + 1)]
            m += (cp.sum(window) >= 1)

        if min_consec >= 2:
            for i in range(total_days):
                window_prev = [change[(i - j) % total_days] for j in range(min_consec)]
                m += (cp.sum(window_prev) <= 1)

        type_order_arr = [SHIFT_TYPE_ORDER.get(t, 0) for t in shift_types]

        for i in range(total_days):
            wi, di = wrap(i)
            wi_prev, di_prev = wrap(i - 1)
            v_cur  = shift[wi][di]
            v_prev = shift[wi_prev][di_prev]
            is_off_cur  = cp.any([v_cur  == oi for oi in off_indices])
            is_off_prev = cp.any([v_prev == oi for oi in off_indices])
            order_cur  = cp.Element(type_order_arr, v_cur)
            order_prev = cp.Element(type_order_arr, v_prev)
            both_not_off = ~(is_off_cur | is_off_prev)
            m += both_not_off.implies(order_cur >= order_prev)

        for i in range(total_days):
            window_14 = []
            for j in range(14):
                wi, di = wrap(i + j)
                v = shift[wi][di]
                is_off_arr = [1 if k in off_indices else 0 for k in range(n_shifts)]
                window_14.append(cp.Element(is_off_arr, v))
            m += (cp.sum(window_14) >= min_off_per_14)

        for req in daily_reqs:
            d = int(req["day_of_week"])
            shift_id = req["shift_id"]
            required = int(req["required"])
            if shift_id not in shift_id_to_idx:
                continue
            sidx = shift_id_to_idx[shift_id]
            indicator_arr = [1 if k == sidx else 0 for k in range(n_shifts)]
            count_expr = cp.sum([
                cp.Element(indicator_arr, shift[w][d]) for w in range(W)
            ])
            m += (count_expr == required)

        logger.info(f"[ShiftRotationScheduler][cpsat] solve() W={W}, shifts={n_shifts}, time_limit={time_limit}s")

        solved = m.solve(solver="ortools", time_limit=time_limit)

        if not solved:
            logger.info("[ShiftRotationScheduler][cpsat] 解なし (infeasible)")
            return {
                "status": "ok",
                "feasible": False,
                "metadata": {"problem_class": "ShiftRotationScheduler"},
                "solutions": [],
                "issues": [{
                    "id": "solve_failed",
                    "severity": "CRITICAL",
                    "category": "INFEASIBLE",
                    "title": "制約を満たすシフトテンプレートが見つかりませんでした",
                    "message": (
                        "指定された必要人数・連続日数・シフト順序などの制約を"
                        "同時に満たすローテーションテンプレートが存在しません。"
                        "必要人数や従業員数を見直してください。"
                    ),
                    "relatedContainerIds": [],
                }],
                "_solver_version": "shift_rotation_scheduler_v1.0",
            }

        template = []
        for w in range(W):
            week_row = []
            for d in range(7):
                val = shift[w][d].value()
                week_row.append(int(val) if val is not None else 0)
            template.append(week_row)

        employee_schedules = []
        for k in range(W):
            schedule_rows = []
            for w in range(W):
                row = []
                for d in range(7):
                    sidx = template[(w + k) % W][d]
                    row.append({
                        "shift_id":   shift_ids[sidx],
                        "shift_name": shifts_def[sidx]["name"],
                        "shift_type": shift_types[sidx],
                    })
                schedule_rows.append(row)
            employee_schedules.append({
                "employee_id":   f"EMP{k:02d}",
                "employee_name": f"従業員{k + 1}",
                "weeks":         schedule_rows,
            })

        total_shifts_assigned = sum(
            1
            for k in range(W)
            for w in range(W)
            for d in range(7)
            if shift_types[template[(w + k) % W][d]] != "off"
        )
        off_count = W * W * 7 - total_shifts_assigned

        solution = {
            "name":    "Plan A",
            "label":   "循環シフトテンプレート",
            "feasible": True,
            "template": template,
            "template_shift_ids": [
                [shift_ids[template[w][d]] for d in range(7)]
                for w in range(W)
            ],
            "employee_schedules": employee_schedules,
            "kpi": {
                "num_employees":        W,
                "cycle_weeks":          W,
                "total_working_days":   total_shifts_assigned,
                "total_off_days":       off_count,
                "working_days_per_emp": round(total_shifts_assigned / W, 1) if W > 0 else 0,
                "off_days_per_emp":     round(off_count / W, 1) if W > 0 else 0,
            },
        }

        logger.info(
            f"[ShiftRotationScheduler][cpsat] 解発見: W={W}, "
            f"working_days={total_shifts_assigned}, off_days={off_count}"
        )

        return {
            "status":    "ok",
            "feasible":  True,
            "metadata":  {"problem_class": "ShiftRotationScheduler"},
            "solutions": [solution],
            "issues":    [],
            "_solver_version": "shift_rotation_scheduler_v1.0",
        }

    def _infeasible(self, issue_id: str, message: str) -> dict:
        return {
            "status": "ok",
            "feasible": False,
            "metadata": {"problem_class": "ShiftRotationScheduler"},
            "solutions": [],
            "issues": [{
                "id":       issue_id,
                "severity": "CRITICAL",
                "category": "INFEASIBLE",
                "title":    "ShiftRotationScheduler 設定エラー",
                "message":  message,
                "relatedContainerIds": [],
            }],
            "_solver_version": "shift_rotation_scheduler_v1.0",
        }