"""
meeting_room_solver.py — MeetingRoom 会議室予約自動割当ソルバー

solver_input keys:
  - meta: Dict             # instance_name, note（UI表示用パススルー）
  - problem_class: str
  - meetings: List[Dict]   # id, name, dept, start_min, end_min, attendees, required_features
  - rooms: List[Dict]      # id, name, capacity, features, available_start_min, available_end_min
  - config: Dict           # dept_same_room_bonus, waste_penalty_weight, dept_consecutive_gap_min, solve_time_sec
  - issue_statuses: Dict
"""

import logging
from typing import Any, Dict, List, Optional

from solvers.base.ce_limit_lns import (
    is_ce_limit_exceeded, CeLimitExceededError, run_solve_with_ce_limit_fallback,
)
from solvers.base.solver_error_result import build_solver_crash_issue, solver_crash_extra_fields
from solvers.base.engine_select import get_solver_engine, CPO, CPSAT, cpmpy_optimality_metadata

logger = logging.getLogger(__name__)

# ヒアリング4-1節: 未割当1件あたり1000点
UNASSIGNED_PENALTY = 1000
# ヒアリング4-1節: 部屋の無駄遣い1名分あたり1点
WASTE_PENALTY_WEIGHT = 1
# ヒアリング5節: 同部署同室維持ボーナス0.1点/件
DEPT_SAME_ROOM_BONUS = 0.1
# ヒアリング5節: 同部署の近接判定（前の会議終了〜次の会議開始が60分以内）
DEPT_CONSECUTIVE_GAP_MIN = 60


class MeetingRoomSolver:
    """会議室予約自動割当: CP Optimizer (optional interval_var) ベースの割当ソルバー"""

    def __init__(self, solver_input: dict) -> None:
        self.dsl = solver_input

    def solve(self) -> dict:
        meetings = self.dsl.get("meetings", [])
        rooms    = self.dsl.get("rooms", [])
        config   = self.dsl.get("config", {})

        if not meetings:
            return self._make_result(True, [], [], "会議が1件もありません")
        if not rooms:
            return self._make_result(False, [], [], "会議室が1件もありません")

        try:
            return self._build_and_solve(meetings, rooms, config)
        except CeLimitExceededError:
            # 2026-07-27追加: CPLEXの無料版のCP Optimizerモデルサイズ上限を検知した
            # 場合、部屋をグループ分けして順番に解く処理に切り替える。retry-depth付き
            # 段階的バッチ縮小のロジック自体はドメイン非依存のため層A
            # （solvers/base/ce_limit_lns.py）に集約済みで、このドメインが持つのは
            # PRIMARY_ENTITY_KEY/build_subset_input/merge_resultsという層Bの3点だけ
            # （NurseShiftWeeklyCapと同じ接続パターン。
            # 分割軸をroomsにした理由はmeeting_room_batch_decomposer.py参照）。
            from solvers.meeting_room_batch_decomposer import (
                PRIMARY_ENTITY_KEY, build_subset_input, merge_results,
            )
            return run_solve_with_ce_limit_fallback(
                solver_class=MeetingRoomSolver,
                solver_input=self.dsl,
                primary_entity_key=PRIMARY_ENTITY_KEY,
                build_subset_input=build_subset_input,
                merge_results=merge_results,
                problem_class="MeetingRoom",
                solver_version="meeting_room_v1.0",
            )
        except Exception as e:
            logger.error(f"[MeetingRoom] solve error: {e}", exc_info=True)
            # [2026-07-28] id は "solve_failed" に統一（独自idだと制約見直しタブが
            # Feasible誤判定する。solvers/base/solver_error_result.py 参照）。
            result = self._make_result(False, [], [build_solver_crash_issue(e)], str(e))
            result.update(solver_crash_extra_fields(e))
            return result

    # ------------------------------------------------------------------
    # CP Optimizer モデル構築
    # ------------------------------------------------------------------

    def _compute_compatible_rooms(self, meetings, rooms) -> Dict[str, List[str]]:
        """meeting_id -> 割当可能なroom_idのリスト。収容人数・必要設備・
        部屋の利用可能時間帯によるフィルタで、CPO/CP-SATどちらのエンジンでも
        使う共通の前処理（Backend/tools/pilot_cpmpy_meeting_room.py の
        compatible_rooms_for()と同一ロジック）。"""
        compatible_rooms: Dict[str, List[str]] = {}
        for m in meetings:
            mid      = str(m["id"])
            m_start  = int(m["start_min"])
            m_end    = int(m["end_min"])
            m_attend = int(m.get("attendees", 1))
            m_feats  = set(m.get("required_features", []))
            compatible_rooms[mid] = []
            for r in rooms:
                rid       = str(r["id"])
                r_cap     = int(r.get("capacity", 0))
                r_feats   = set(r.get("features", []))
                r_avail_s = int(r.get("available_start_min", 0))
                r_avail_e = int(r.get("available_end_min", 1440))
                if r_cap < m_attend:
                    continue
                if not m_feats.issubset(r_feats):
                    continue
                if m_start < r_avail_s or m_end > r_avail_e:
                    continue
                compatible_rooms[mid].append(rid)
        return compatible_rooms

    def _build_and_solve(self, meetings, rooms, config):
        # ヒアリング5節: dept_same_room_bonus=0.1（同部署同室維持ボーナス）
        dept_same_room_bonus     = config.get("dept_same_room_bonus", DEPT_SAME_ROOM_BONUS)
        # ヒアリング4-1節: waste_penalty_weight=1（部屋の無駄遣い1名分あたり1点）
        waste_penalty_weight     = config.get("waste_penalty_weight", WASTE_PENALTY_WEIGHT)
        dept_consecutive_gap_min = config.get("dept_consecutive_gap_min", DEPT_CONSECUTIVE_GAP_MIN)
        solve_time_sec           = config.get("solve_time_sec", 30)

        compatible_rooms = self._compute_compatible_rooms(meetings, rooms)

        # --- エンジン選択（2026-08-13追加: DESIGN_2026-08-12_cp_sat_backend_support.md）---
        engine = get_solver_engine(config)
        logger.info(f"[MeetingRoom] meetings={len(meetings)}, rooms={len(rooms)}, engine={engine}")
        if engine == CPSAT:
            assignments, obj_val, optimality, early = self._solve_with_cpsat(
                meetings, rooms, compatible_rooms, dept_same_room_bonus,
                waste_penalty_weight, dept_consecutive_gap_min, solve_time_sec,
            )
        else:
            assignments, obj_val, optimality, early = self._solve_with_cpo(
                meetings, rooms, compatible_rooms, dept_same_room_bonus,
                waste_penalty_weight, dept_consecutive_gap_min, solve_time_sec,
            )
        if early is not None:
            return early

        assigned_meeting_ids = {a["meeting_id"] for a in assignments}

        # 全件未割当異常検知
        from solvers.base.issue_rules import build_full_unassignment_issue
        anomaly = build_full_unassignment_issue(
            assigned_count=len(assigned_meeting_ids),
            total_count=len(meetings),
            entity_label="会議",
            extra_hint="get_var_solution()の解抽出処理",
        )

        # issues 生成
        issues = self._detect_issues(meetings, rooms, assignments, assigned_meeting_ids)
        if anomaly:
            issues.insert(0, anomaly)

        # 2026-07-24追加: 解チェッカー（DESIGN_2026-07-21）。収容人数・必要設備・
        # 利用可能時間帯・同室重複禁止は、いずれもCP Optimizerモデル側で
        # ハード制約として作り込まれている（変数生成時点でフィルタ済み、または
        # no_overlap制約）ため、返ってきたassignmentsから独立に検算して
        # 破られていればバグの疑いが強い（category="SOLVER"）。
        checker_issues, deferred_check = self._run_solution_checker(assignments, rooms)
        issues.extend(checker_issues)

        feasible = len(assigned_meeting_ids) == len(meetings)

        if obj_val is None:
            obj_val = 0.0

        solution = {
            "feasible":    feasible,
            "assignments": assignments,
            "kpi": {
                "total_meetings":    len(meetings),
                "assigned_count":    len(assigned_meeting_ids),
                "unassigned_count":  len(meetings) - len(assigned_meeting_ids),
                "total_rooms":       len(rooms),
                "objective_value":   round(obj_val, 2),
            },
            # solve_status/is_optimal/solve_time_sec。TimeLimitで打ち切られた
            # 未証明解かどうかの区別に使う（solution_extraction.py参照）。
            "optimality": optimality,
        }

        result = {
            "status":   "ok",
            "feasible": feasible,
            "metadata": self._build_metadata(),
            "solutions": [solution],
            "issues":    issues,
            "_solver_version": "meeting_room_v1.0",
        }
        if deferred_check is not None:
            # app.py側（_solve_4dsl_generic）がこのキーを見て非同期ジョブを
            # 起動する（レスポンスには含めず、pop済みの内容から
            # async_check_job_id を組み立てる）。DESIGN_2026-07-21 3-3節。
            result["_deferred_checks"] = [deferred_check]
        return result

    # ------------------------------------------------------------------
    # エンジン別ソルブ（2026-08-13追加）
    #
    # 両メソッドは同じ契約で戻り値を返す:
    #   (assignments, obj_val, optimality, early_result)
    # 詳細な設計意図は car_sequencing_solver.py の同名メソッド群のコメント参照。
    # ------------------------------------------------------------------

    def _solve_with_cpo(
        self, meetings, rooms, compatible_rooms, dept_same_room_bonus,
        waste_penalty_weight, dept_consecutive_gap_min, solve_time_sec,
    ):
        from docplex.cp.model import CpoModel
        from solvers.base.solution_extraction import safe_objective_value, extract_optimality_metadata

        mdl = CpoModel()

        # 変数: assignment_itvs[meeting_id][room_id] = optional interval_var
        assignment_itvs: Dict[str, Dict[str, Any]] = {}
        for m in meetings:
            mid     = str(m["id"])
            m_start = int(m["start_min"])
            m_end   = int(m["end_min"])
            m_dur   = m_end - m_start
            assignment_itvs[mid] = {}
            for rid in compatible_rooms.get(mid, []):
                itv = mdl.interval_var(
                    start=m_start, end=m_end, size=m_dur,
                    optional=True, name=f"asgn_{mid}_{rid}",
                )
                assignment_itvs[mid][rid] = itv

        # 制約1: 各会議は高々1つの部屋に割り当て（presence の和 <= 1）
        for mid, room_itvs in assignment_itvs.items():
            if room_itvs:
                mdl.add(mdl.sum([mdl.presence_of(itv) for itv in room_itvs.values()]) <= 1)

        # 制約2: 各部屋の同一時間帯に複数会議を割り当てない（no_overlap）
        room_to_itvs: Dict[str, List[Any]] = {str(r["id"]): [] for r in rooms}
        for mid, room_itvs in assignment_itvs.items():
            for rid, itv in room_itvs.items():
                room_to_itvs[rid].append(itv)
        for rid, itvs in room_to_itvs.items():
            if len(itvs) > 1:
                mdl.add(mdl.no_overlap(itvs))

        # 目的関数
        penalty_terms, waste_terms, bonus_terms = [], [], []

        for m in meetings:
            mid = str(m["id"])
            room_itvs = assignment_itvs.get(mid, {})
            if not room_itvs:
                penalty_terms.append(UNASSIGNED_PENALTY)
            else:
                assigned_sum = mdl.sum([mdl.presence_of(itv) for itv in room_itvs.values()])
                penalty_terms.append(UNASSIGNED_PENALTY * (1 - assigned_sum))

        for m in meetings:
            mid      = str(m["id"])
            m_attend = int(m.get("attendees", 1))
            for r in rooms:
                rid   = str(r["id"])
                r_cap = int(r.get("capacity", 0))
                itv   = assignment_itvs.get(mid, {}).get(rid)
                if itv is None:
                    continue
                waste = r_cap - m_attend
                waste_terms.append(waste_penalty_weight * waste * mdl.presence_of(itv))

        dept_meetings: Dict[str, List[Dict]] = {}
        for m in meetings:
            dept = m.get("dept")
            if dept:
                dept_meetings.setdefault(str(dept), []).append(m)

        for dept, dept_ms in dept_meetings.items():
            for i in range(len(dept_ms)):
                for j in range(i + 1, len(dept_ms)):
                    ma, mb = dept_ms[i], dept_ms[j]
                    end_a, start_b = int(ma["end_min"]), int(mb["start_min"])
                    end_b, start_a = int(mb["end_min"]), int(ma["start_min"])
                    gap_ab = start_b - end_a
                    gap_ba = start_a - end_b
                    is_consecutive = (0 <= gap_ab <= dept_consecutive_gap_min) or \
                                     (0 <= gap_ba <= dept_consecutive_gap_min)
                    if not is_consecutive:
                        continue
                    mid_a, mid_b = str(ma["id"]), str(mb["id"])
                    common_rooms = set(compatible_rooms.get(mid_a, [])) & set(compatible_rooms.get(mid_b, []))
                    for rid in common_rooms:
                        itv_a = assignment_itvs.get(mid_a, {}).get(rid)
                        itv_b = assignment_itvs.get(mid_b, {}).get(rid)
                        if itv_a is None or itv_b is None:
                            continue
                        both_assigned = mdl.min(mdl.presence_of(itv_a), mdl.presence_of(itv_b))
                        bonus_terms.append(dept_same_room_bonus * both_assigned)

        obj_expr = mdl.sum(penalty_terms + waste_terms)
        if bonus_terms:
            obj_expr = obj_expr - mdl.sum(bonus_terms)
        mdl.add(mdl.minimize(obj_expr))

        try:
            msol = mdl.solve(TimeLimit=solve_time_sec, LogVerbosity="Quiet")
        except Exception as e:
            if is_ce_limit_exceeded(e):
                # 2026-07-27追加: 層A共通の例外として再送出し、呼び出し元solve()の
                # CE上限フォールバック（部屋をグループ分けして順番に解く）に
                # 切り替えさせる。config.solver_engine="cpsat"を明示指定すれば
                # このCE上限自体を回避できる（2026-08-13追加）。
                raise CeLimitExceededError(str(e)) from e
            raise

        optimality = extract_optimality_metadata(msol)

        if msol is None:
            early = self._make_result(False, [], [
                {"id": "solve_failed", "severity": "CRITICAL",
                 "title": "実行可能解が見つかりませんでした",
                 "message": "制約（収容人数・設備・時間帯）をすべて満たす割当が存在しません。",
                 "relatedContainerIds": []}
            ], optimality=optimality)
            return [], 0.0, optimality, early

        assignments = []
        for m in meetings:
            mid = str(m["id"])
            for rid in compatible_rooms.get(mid, []):
                itv = assignment_itvs.get(mid, {}).get(rid)
                if itv is None:
                    continue
                var_sol = msol.get_var_solution(itv)
                if var_sol is None or not var_sol.is_present():
                    continue
                assignments.append({
                    "meeting_id":   mid,
                    "meeting_name": m.get("name", mid),
                    "room_id":      rid,
                    "room_name":    next((rm.get("name", rid) for rm in rooms if str(rm["id"]) == rid), rid),
                    "start_min":    var_sol.get_start(),
                    "end_min":      var_sol.get_end(),
                    "attendees":    int(m.get("attendees", 1)),
                    "capacity":     int(next((rm.get("capacity", 0) for rm in rooms if str(rm["id"]) == rid), 0)),
                    "dept":         m.get("dept"),
                    "features":     m.get("required_features", []),
                })
                break  # 1会議=1部屋

        obj_val = safe_objective_value(msol, fallback=0.0)
        return assignments, obj_val, optimality, None

    def _solve_with_cpsat(
        self, meetings, rooms, compatible_rooms, dept_same_room_bonus,
        waste_penalty_weight, dept_consecutive_gap_min, solve_time_sec,
    ):
        """
        OR-Tools CP-SAT（CPMpy経由）でのソルブ。
        Backend/tools/pilot_cpmpy_meeting_room.py で全探索による独立検証済みの
        モデル構築ロジックをそのまま本番コードに移植したもの。

        CPMpyには docplex.cp の interval_var のような単一の第一級オブジェクトが
        無いため、cp.NoOverlapOptional(start, duration, end, is_present) という
        「分解型」APIを使う（本ドメインのstart/end/durationは会議の固定時刻
        なので、この分解でも表現力に不足は無い）。
        CP-SAT(CPMpy)は目的関数にfloat係数を受け付けないため、
        dept_same_room_bonus（既定0.1）を含む目的関数全体を SCALE 倍して
        整数化してから解き、結果を割り戻す。
        """
        import cpmpy as cp
        from solvers.base.engine_select import cpmpy_optimality_metadata

        SCALE = 10  # dept_same_room_bonus等のfloat係数を整数化するための倍率
        bonus_scaled = round(dept_same_room_bonus * SCALE)

        is_present: Dict[Any, Any] = {}
        for m in meetings:
            mid = str(m["id"])
            for rid in compatible_rooms.get(mid, []):
                is_present[(mid, rid)] = cp.boolvar(name=f"present_{mid}_{rid}")

        model = cp.Model()

        # 制約1: 各会議は高々1部屋
        for m in meetings:
            mid = str(m["id"])
            vars_for_m = [is_present[(mid, rid)] for rid in compatible_rooms.get(mid, [])]
            if vars_for_m:
                model += (cp.sum(vars_for_m) <= 1)

        # 制約2: 部屋ごとの NoOverlapOptional（docplexのno_overlapに相当）
        room_to_meetings: Dict[str, List[Dict]] = {str(r["id"]): [] for r in rooms}
        for m in meetings:
            mid = str(m["id"])
            for rid in compatible_rooms.get(mid, []):
                room_to_meetings[rid].append(m)

        for rid, ms in room_to_meetings.items():
            if len(ms) > 1:
                starts   = [m["start_min"] for m in ms]
                durs     = [m["end_min"] - m["start_min"] for m in ms]
                ends     = [m["end_min"] for m in ms]
                presents = [is_present[(str(m["id"]), rid)] for m in ms]
                model += cp.NoOverlapOptional(starts, durs, ends, presents)

        # 目的関数
        penalty_terms, waste_terms, bonus_terms = [], [], []

        for m in meetings:
            mid = str(m["id"])
            room_ids = compatible_rooms.get(mid, [])
            if not room_ids:
                penalty_terms.append(UNASSIGNED_PENALTY)
            else:
                assigned_sum = cp.sum([is_present[(mid, rid)] for rid in room_ids])
                penalty_terms.append(UNASSIGNED_PENALTY * (1 - assigned_sum))

        for m in meetings:
            mid    = str(m["id"])
            attend = int(m.get("attendees", 1))
            for rid in compatible_rooms.get(mid, []):
                r_cap = int(next(rm["capacity"] for rm in rooms if str(rm["id"]) == rid))
                waste = r_cap - attend
                waste_terms.append(waste_penalty_weight * waste * is_present[(mid, rid)])

        dept_meetings: Dict[str, List[Dict]] = {}
        for m in meetings:
            dept = m.get("dept")
            if dept:
                dept_meetings.setdefault(str(dept), []).append(m)

        for dept, dms in dept_meetings.items():
            for i in range(len(dms)):
                for j in range(i + 1, len(dms)):
                    ma, mb = dms[i], dms[j]
                    gap_ab = mb["start_min"] - ma["end_min"]
                    gap_ba = ma["start_min"] - mb["end_min"]
                    is_consecutive = (0 <= gap_ab <= dept_consecutive_gap_min) or \
                                     (0 <= gap_ba <= dept_consecutive_gap_min)
                    if not is_consecutive:
                        continue
                    mid_a, mid_b = str(ma["id"]), str(mb["id"])
                    common = set(compatible_rooms.get(mid_a, [])) & set(compatible_rooms.get(mid_b, []))
                    for rid in common:
                        both = cp.min([is_present[(mid_a, rid)], is_present[(mid_b, rid)]])
                        bonus_terms.append(bonus_scaled * both)

        obj = SCALE * cp.sum(penalty_terms + waste_terms)
        if bonus_terms:
            obj = obj - cp.sum(bonus_terms)
        model.minimize(obj)

        ok = model.solve(solver="ortools", time_limit=solve_time_sec)
        optimality = cpmpy_optimality_metadata(model)

        if not ok:
            early = self._make_result(False, [], [
                {"id": "solve_failed", "severity": "CRITICAL",
                 "title": "実行可能解が見つかりませんでした",
                 "message": "制約（収容人数・設備・時間帯）をすべて満たす割当が存在しません。",
                 "relatedContainerIds": []}
            ], optimality=optimality)
            return [], 0.0, optimality, early

        assignments = []
        for m in meetings:
            mid = str(m["id"])
            for rid in compatible_rooms.get(mid, []):
                if not is_present[(mid, rid)].value():
                    continue
                assignments.append({
                    "meeting_id":   mid,
                    "meeting_name": m.get("name", mid),
                    "room_id":      rid,
                    "room_name":    next((rm.get("name", rid) for rm in rooms if str(rm["id"]) == rid), rid),
                    "start_min":    m["start_min"],
                    "end_min":      m["end_min"],
                    "attendees":    int(m.get("attendees", 1)),
                    "capacity":     int(next((rm.get("capacity", 0) for rm in rooms if str(rm["id"]) == rid), 0)),
                    "dept":         m.get("dept"),
                    "features":     m.get("required_features", []),
                })
                break  # 1会議=1部屋

        # SCALEで整数化していたのを元に戻す（docplex.cp側と同じ単位に揃える）
        obj_val = model.objective_value() / SCALE if model.objective_value() is not None else 0.0
        return assignments, obj_val, optimality, None

    # ------------------------------------------------------------------
    # イシュー検知
    # ------------------------------------------------------------------

    def _detect_issues(self, meetings, rooms, assignments, assigned_meeting_ids):
        issues = []
        assigned_map = {a["meeting_id"]: a for a in assignments}

        for m in meetings:
            mid = str(m["id"])
            if mid not in assigned_meeting_ids:
                issues.append({
                    "id":       f"unassigned_{mid}",
                    "severity": "CRITICAL",
                    "title":    f"未割当: {m.get('name', mid)}",
                    "message":  (
                        f"会議「{m.get('name', mid)}」({m.get('attendees',1)}名, "
                        f"{m.get('start_min',0)}〜{m.get('end_min',0)}分) "
                        f"を割り当てられる会議室がありませんでした。"
                        f"収容人数・設備・時間帯を確認してください。"
                    ),
                    "relatedContainerIds": [],
                })

        # 収容人数ギリギリ（余剰0）の警告
        for a in assignments:
            if a["attendees"] >= a["capacity"]:
                issues.append({
                    "id":       f"tight_room_{a['meeting_id']}",
                    "severity": "WARNING",
                    "title":    f"収容ギリギリ: {a['meeting_name']}",
                    "message":  (
                        f"「{a['meeting_name']}」({a['attendees']}名) が "
                        f"収容人数{a['capacity']}名の部屋「{a['room_name']}」に割り当てられています。"
                    ),
                    "relatedContainerIds": [],
                })

        return issues

    # ------------------------------------------------------------------
    # 解チェッカー（DESIGN_2026-07-21、2026-07-24実装）
    # ------------------------------------------------------------------

    def _run_solution_checker(self, assignments: list, rooms: list):
        """
        独立な制約充足検証（4節: solver内部のitv/mdl等は一切参照せず、
        返ってきたassignments/roomsのみを使う）。

        収容人数・必要設備・利用可能時間帯はO(n)（assignment 1件ごと）で
        常に同期実行。同室重複チェックはO(n²)（同一部屋内のペア総当たり）
        のため、solvers.base.solution_checker.run_or_defer() で
        インスタンス規模に応じて同期/非同期を分岐する。

        Returns:
            (issues, deferred): deferred は非同期に回った場合のみdict、
            それ以外はNone。
        """
        from solvers.base.issue_rules import (
            run_issue_rules, build_meeting_room_field_contexts, build_meeting_room_overlap_contexts,
        )
        from solvers.base.solution_checker import run_or_defer, COST_O_N2

        issue_statuses = self.dsl.get("issue_statuses", {})

        field_ctxs = build_meeting_room_field_contexts(assignments, rooms)
        issues = run_issue_rules(domain="MeetingRoom", contexts=field_ctxs, issue_statuses=issue_statuses)

        # Gate2登録時（run_gate2_dynamic_verification経由）は solver_input に
        # _gate2_full_check=True が入り、閾値を無視してフル同期実行する（3-1節）。
        full_check = bool(self.dsl.get("_gate2_full_check", False))

        overlap_issues, deferred = run_or_defer(
            COST_O_N2,
            len(assignments),
            lambda: run_issue_rules(
                domain="MeetingRoom",
                contexts=build_meeting_room_overlap_contexts(assignments),
                issue_statuses=issue_statuses,
            ),
            full_check=full_check,
            domain="MeetingRoom",
            check_id="room_double_booking",
        )
        issues.extend(overlap_issues)
        return issues, deferred

    # ------------------------------------------------------------------
    # 結果フォーマットヘルパー
    # ------------------------------------------------------------------

    def _build_metadata(self) -> dict:
        # 2026-07-19再修正: instance_name/noteのUI表示用パススルーが、
        # MeetingRoom削除→再登録（Stage2コード生成やり直し）のたびに失われる
        # ことが2回判明している（2026-07-18g、今回2026-07-19）。ライブファイルへの
        # その場パッチはヒアリングシートに明文化されない限り再発するため、
        # 専用メソッドに切り出してtruck_dispatcher_solver.py等と同じパターンに
        # 揃えた（solvers/test_meeting_room_solver_metadata.py で回帰確認）。
        meta = self.dsl.get("meta", {}) or {}
        return {
            "problem_class": "MeetingRoom",
            "instance_name": meta.get("instance_name", ""),
            "note":          meta.get("note", ""),
        }

    def _make_result(
        self,
        feasible: bool,
        assignments: list,
        issues: list,
        error_msg: str = "",
        optimality: dict | None = None,
    ) -> dict:
        # optimality省略時（会議/会議室が0件などソルバー自体を実行していない
        # 早期return）は、msol=None相当のデフォルト値を使う。
        from solvers.base.solution_extraction import extract_optimality_metadata
        if optimality is None:
            optimality = extract_optimality_metadata(None)

        return {
            "status":   "ok" if not error_msg else "ok",
            "feasible": feasible,
            "metadata": self._build_metadata(),
            "solutions": [{
                "feasible":    feasible,
                "assignments": assignments,
                "kpi": {
                    "total_meetings":   0,
                    "assigned_count":   len(assignments),
                    "unassigned_count": 0,
                    "total_rooms":      0,
                    "objective_value":  0,
                },
                "optimality": optimality,
            }],
            "issues": issues,
            "_solver_version": "meeting_room_v1.0",
        }
