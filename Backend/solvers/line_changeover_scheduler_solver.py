"""
line_changeover_scheduler_solver.py — LineChangeoverScheduler ソルバー

# solver_input keys (converter との整合チェック済み):
#   problem_class, meta, tasks, resources, precedences, config, issue_statuses

RCPSP (Resource-Constrained Project Scheduling Problem) ベース。
タスク間の前後関係制約と資源容量制約のもとでメイクスパン最小化。

CP Optimizer 使用。禁止パターン厳守:
  - presence_of() == 1 は使用しない（presence_of() をそのまま boolean として使う）
  - if_then の第2引数に制約式を渡さない
  - 解抽出は get_var_solution(itv) を使用する（presence_of/start_of/end_of を式で組み立てない）
"""

import logging
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

# CP Optimizer が利用できない場合の純Pythonフォールバック用フラグ
try:
    from docplex.cp.model import CpoModel
    _HAS_CPO = True
except ImportError:
    _HAS_CPO = False
    logger.warning("[LineChangeoverScheduler] docplex が見つかりません。ヒューリスティックにフォールバックします。")

from solvers.base.issue_rules import build_full_unassignment_issue
from solvers.base.ce_limit_lns import is_ce_limit_exceeded
from solvers.base.engine_select import get_solver_engine, CPO, CPSAT


class LineChangeoverSchedulerSolver:
    """
    製造ライン切替プロジェクトの RCPSP ソルバー。

    solver_input keys:
      problem_class : "LineChangeoverScheduler"
      meta          : dict (instance_name 等)
      tasks         : List[dict] (id, name, duration, resource_requirements)
      resources     : List[dict] (id, name, capacity)
      precedences   : List[dict] (from_task, to_task, min_delay)
      config        : dict (time_limit_sec, horizon)
      issue_statuses: dict
    """

    _SOLVER_VERSION = "line_changeover_scheduler_v1.0"

    def __init__(self, solver_input: dict) -> None:
        self.dsl = solver_input

    def solve(self) -> dict:
        tasks      = self.dsl.get("tasks", [])
        resources  = self.dsl.get("resources", [])
        precedences = self.dsl.get("precedences", [])
        config     = self.dsl.get("config", {})
        meta       = self.dsl.get("meta", {})

        issues = self._validate(tasks, resources)
        if any(i["severity"] == "CRITICAL" for i in issues):
            return self._make_result("ok", [], issues, meta, feasible=False)

        # --- エンジン選択（2026-08-13追加: DESIGN_2026-08-12_cp_sat_backend_support.md）---
        # config.solver_engine="cpsat" が明示された場合はOR-Tools CP-SAT
        # (CPMpy経由) を使う。docplexの有無に依らず動く（CPLEXが無い環境向けの
        # 本来の目的）。既定("cpo")は従来通り、CPLEXがあればCP Optimizer、
        # 無ければヒューリスティックにフォールバックする既存挙動を維持する。
        engine = get_solver_engine(config)
        if engine == CPSAT:
            solution_data, solve_issues = self._solve_cpsat(tasks, resources, precedences, config)
        elif _HAS_CPO:
            solution_data, solve_issues = self._solve_cpo(tasks, resources, precedences, config)
        else:
            solution_data, solve_issues = self._solve_heuristic(tasks, resources, precedences, config)

        issues.extend(solve_issues)

        # 解抽出異常検知（全タスク未割当の場合に警告）
        # 2026-07-27追記: CE上限検知（ce_limit_unresolvable）による意図的な
        # 早期returnの場合はスキップする。schedule=[]は「解抽出のバグ」ではなく
        # 「そもそも解こうとしていない」ことによる正常な空配列のため、
        # ここで「解抽出処理にバグの疑い」という誤った警告を出さないようにする
        # （実機テストで実際に両方のissueが同時に出ることを確認、修正済み）。
        is_ce_limit_unresolvable = any(i.get("id") == "ce_limit_unresolvable" for i in solve_issues)
        if solution_data and not is_ce_limit_unresolvable:
            assigned_count = len(solution_data.get("schedule", []))
            anomaly = build_full_unassignment_issue(
                assigned_count=assigned_count,
                total_count=len(tasks),
                entity_label="タスク",
                extra_hint="get_var_solution() による解抽出処理",
            )
            if anomaly:
                issues.insert(0, anomaly)

        # 2026-07-24追加: 解チェッカー（DESIGN_2026-07-21）。LineChangeoverScheduler
        # （RCPSP、新フラグシップ）はこれまで独立の解チェッカーを一切持って
        # いなかった。前後関係制約（precedences）と資源容量制約
        # （resources[].capacity）はいずれもCP Optimizer側のハード制約
        # （end_before_start / cumul_function <= cap）のため、返ってきた
        # scheduleと突き合わせて破られていればバグの疑いが強い。
        #   - precedence_violation: O(E)（precedence件数分の単純比較）
        #   - resource_capacity_violation: O(n log n)（スイープライン検算。
        #     DESIGN文書2節で「現状未実装、将来の拡張候補」とされていた
        #     分類の初適用）
        # いずれもO(n)/O(n log n)のため常に同期実行（非同期分岐は不要）。
        if solution_data and solution_data.get("feasible") and solution_data.get("schedule"):
            from solvers.base.issue_rules import (
                run_issue_rules,
                build_line_changeover_precedence_contexts,
                build_line_changeover_resource_contexts,
            )
            schedule = solution_data["schedule"]
            checker_ctxs = (
                build_line_changeover_precedence_contexts(schedule, precedences)
                + build_line_changeover_resource_contexts(schedule, resources)
            )
            issues.extend(run_issue_rules(
                domain="LineChangeoverScheduler",
                contexts=checker_ctxs,
                issue_statuses=self.dsl.get("issue_statuses", {}),
            ))

        solutions = [solution_data] if solution_data else []
        feasible  = bool(solution_data.get("feasible")) if solution_data else False
        # feasible=False でも status:"ok" で返し、issues で CRITICAL 通知。
        # feasible はトップレベルにも明示する（Gate2動的検証は result.get("feasible")
        # をトップレベルから読むため）。
        return self._make_result("ok", solutions, issues, meta, feasible=feasible)

    # ------------------------------------------------------------------
    # 入力バリデーション
    # ------------------------------------------------------------------

    def _validate(self, tasks: List[dict], resources: List[dict]) -> List[dict]:
        issues = []
        if not tasks:
            issues.append({
                "id": "no_tasks", "severity": "CRITICAL", "category": "INPUT",
                "title": "タスクが定義されていません",
                "message": "solver_input.tasks が空です。",
                "relatedContainerIds": [],
            })
        if not resources:
            issues.append({
                "id": "no_resources", "severity": "CRITICAL", "category": "INPUT",
                "title": "リソースが定義されていません",
                "message": "solver_input.resources が空です。",
                "relatedContainerIds": [],
            })
        return issues

    # ------------------------------------------------------------------
    # CP Optimizer ソルバー
    # ------------------------------------------------------------------

    def _solve_cpo(
        self,
        tasks: List[dict],
        resources: List[dict],
        precedences: List[dict],
        config: dict,
    ):
        time_limit = config.get("time_limit_sec", 30)
        horizon    = config.get("horizon", 10000)

        mdl = CpoModel()

        # interval_var を各タスクに作成
        task_itvs: Dict[str, Any] = {}
        for t in tasks:
            tid = str(t["id"])
            dur = int(t.get("duration", 1))
            task_itvs[tid] = mdl.interval_var(size=dur, name=f"itv_{tid}", end=(0, horizon))

        # 資源容量制約: cumul_function
        resource_map: Dict[str, Any] = {}
        for r in resources:
            rid = str(r["id"])
            cap = int(r.get("capacity", 1))
            cumul = mdl.sum([
                mdl.pulse(task_itvs[str(t["id"])], int(req))
                for t in tasks
                for rreq in t.get("resource_requirements", [])
                if str(rreq.get("resource_id")) == rid
                for req in [rreq.get("amount", 1)]
                if str(t["id"]) in task_itvs
            ])
            resource_map[rid] = (cap, cumul)
            mdl.add(cumul <= cap)

        # 前後関係制約
        for prec in precedences:
            from_id  = str(prec.get("from_task", ""))
            to_id    = str(prec.get("to_task", ""))
            delay    = int(prec.get("min_delay", 0))
            itv_from = task_itvs.get(from_id)
            itv_to   = task_itvs.get(to_id)
            if itv_from is not None and itv_to is not None:
                mdl.add(mdl.end_before_start(itv_from, itv_to, delay=delay))

        # 目的関数: メイクスパン最小化（ヒアリング目的関数 項1）
        makespan_expr = mdl.max([mdl.end_of(iv) for iv in task_itvs.values()])
        mdl.add(mdl.minimize(makespan_expr))

        # ソルブ
        try:
            msol = mdl.solve(TimeLimit=time_limit, LogVerbosity="Quiet")
        except Exception as e:
            if is_ce_limit_exceeded(e):
                # 2026-07-27追加: CPLEXの無料版のCP Optimizerモデルサイズ上限を検知。
                # Koshoshiとの相談により、本ドメインではバッチ分割によるフォールバック
                # は行わない（precedences/resourcesの両方がtasksを横断するため、
                # tasks軸で分割すると資源容量超過をバッチ間で検知できず見過ごすリスクが
                # あると判断。詳細はDESIGN_2026-07-26_generic_ce_limit_fallback.md
                # 決定事項#12の安全弁、および会話記録参照）。上限超過を検知したら
                # 分割を試みず、その旨を伝えるissueのみ返す。
                return self._ce_limit_unresolvable()
            raise

        if msol is None or not msol.is_solution():
            issue = {
                "id": "solve_failed", "severity": "CRITICAL", "category": "INFEASIBLE",
                "title": "最適解が見つかりませんでした",
                "message": "現在の制約（前後関係・資源容量）を満たすスケジュールが見つかりませんでした。",
                "relatedContainerIds": [],
            }
            infeasible_solution = {
                "feasible": False,
                "schedule": [],
                "makespan": None,
                "kpi": {"makespan": None, "task_count": 0, "objective_value": None, "solve_time": 0.0},
            }
            return infeasible_solution, [issue]

        # 解抽出 — get_var_solution() を使用（禁止パターン5 回避）
        schedule = []
        for t in tasks:
            tid = str(t["id"])
            itv = task_itvs.get(tid)
            if itv is None:
                continue
            var_sol = msol.get_var_solution(itv)
            if var_sol is None or not var_sol.is_present():
                continue
            schedule.append({
                "task_id":   tid,
                "task_name": t.get("name", tid),
                "start":     var_sol.get_start(),
                "end":       var_sol.get_end(),
                "duration":  var_sol.get_size(),
                "resource_requirements": t.get("resource_requirements", []),
            })

        # メイクスパンは msol.get_value(makespan_expr) のような名前無し式のクエリでは
        # 取得できない（docplex.cp: get_value() は名前付き変数/KPI辞書へのルックアップの
        # ため「Variable or KPI '...' not in the solution」で失敗する。mdl.add(mdl.minimize())
        # で目的関数として登録しても解消しない、2026-07-15に実機で確認した不具合）。
        # 動作実績のあるTruckDispatcher/NurseShiftWeeklyCapと同様、既に get_var_solution() で
        # 抽出済みの schedule から直接計算する（CP Optimizerの式クエリAPIを使わない）。
        makespan_val = max((s["end"] for s in schedule), default=0)
        try:
            obj_val = float(msol.get_objective_value() or makespan_val)
        except Exception:
            obj_val = float(makespan_val)

        solution_data = {
            "feasible":  True,
            "schedule":  schedule,
            "makespan":  int(makespan_val),
            "kpi": {
                "makespan":        int(makespan_val),
                "task_count":      len(schedule),
                "objective_value": obj_val,
                "solve_time":      float(getattr(msol, "get_solve_time", lambda: 0)() or 0),
            },
        }
        return solution_data, []

    # ------------------------------------------------------------------
    # OR-Tools CP-SAT（CPMpy経由）ソルバー（2026-08-13追加）
    # ------------------------------------------------------------------

    def _solve_cpsat(
        self,
        tasks: List[dict],
        resources: List[dict],
        precedences: List[dict],
        config: dict,
    ):
        """
        Backend/tools/pilot_cpmpy_line_changeover_scheduler.py で全探索による
        独立検証済みのモデル構築ロジックをそのまま本番コードに移植したもの。
        _solve_cpo() と同じ (solution_data, issues) の契約で返す。

        CP-SATにはCPLEX Community Editionのようなモデルサイズ上限が無いため、
        本エンジンではCE上限フォールバック（_ce_limit_unresolvable）は発生しない。
        """
        import cpmpy as cp
        from solvers.base.engine_select import cpmpy_optimality_metadata

        time_limit = config.get("time_limit_sec", 30)
        horizon    = config.get("horizon", 10000)

        task_ids = [str(t["id"]) for t in tasks]
        dur: Dict[str, int] = {str(t["id"]): int(t.get("duration", 1)) for t in tasks}

        # start自体が決定変数（真のフレキシブルスケジューリング）。
        # docplex.cp の interval_var(size=dur, end=(0,horizon)) と同一の自由度。
        start = {tid: cp.intvar(0, horizon - dur[tid], name=f"start_{tid}") for tid in task_ids}
        end   = {tid: start[tid] + dur[tid] for tid in task_ids}

        m = cp.Model()

        # 資源容量制約: cumul_function <= cap の docplex.pulse合計に相当
        for r in resources:
            rid = str(r["id"])
            cap = int(r.get("capacity", 1))
            rt, rd, re_, rdem = [], [], [], []
            for t in tasks:
                tid = str(t["id"])
                for rreq in t.get("resource_requirements", []):
                    if str(rreq.get("resource_id")) == rid:
                        rt.append(start[tid])
                        rd.append(dur[tid])
                        re_.append(end[tid])
                        rdem.append(int(rreq.get("amount", 1)))
            if rt:
                m += cp.Cumulative(rt, rd, re_, rdem, cap)

        # 前後関係制約: end_before_start(a, b, delay) 相当
        for prec in precedences:
            from_id = str(prec.get("from_task", ""))
            to_id   = str(prec.get("to_task", ""))
            delay   = int(prec.get("min_delay", 0))
            if from_id in start and to_id in start:
                m += (end[from_id] + delay <= start[to_id])

        # 目的関数: メイクスパン最小化
        if task_ids:
            makespan_expr = cp.max([end[tid] for tid in task_ids])
        else:
            makespan_expr = cp.intvar(0, 0)
        m.minimize(makespan_expr)

        ok = m.solve(solver="ortools", time_limit=time_limit)
        optimality = cpmpy_optimality_metadata(m)

        if not ok:
            issue = {
                "id": "solve_failed", "severity": "CRITICAL", "category": "INFEASIBLE",
                "title": "最適解が見つかりませんでした",
                "message": "現在の制約（前後関係・資源容量）を満たすスケジュールが見つかりませんでした。",
                "relatedContainerIds": [],
            }
            infeasible_solution = {
                "feasible": False,
                "schedule": [],
                "makespan": None,
                "kpi": {"makespan": None, "task_count": 0, "objective_value": None, "solve_time": 0.0},
            }
            return infeasible_solution, [issue]

        schedule = []
        for t in tasks:
            tid = str(t["id"])
            if tid not in start:
                continue
            schedule.append({
                "task_id":   tid,
                "task_name": t.get("name", tid),
                "start":     int(start[tid].value()),
                "end":       int(end[tid].value()),
                "duration":  dur[tid],
                "resource_requirements": t.get("resource_requirements", []),
            })

        makespan_val = max((s["end"] for s in schedule), default=0)
        obj_val = m.objective_value()
        if obj_val is None:
            obj_val = float(makespan_val)

        solution_data = {
            "feasible":  True,
            "schedule":  schedule,
            "makespan":  int(makespan_val),
            "kpi": {
                "makespan":        int(makespan_val),
                "task_count":      len(schedule),
                "objective_value": float(obj_val),
                "solve_time":      float(optimality.get("solve_time_sec") or 0.0),
            },
        }
        return solution_data, []

    # ------------------------------------------------------------------
    # CE上限（CPLEX Community Edition評価版）超過時の結果（2026-07-27追加）
    # ------------------------------------------------------------------

    def _ce_limit_unresolvable(self):
        """
        CP OptimizerのCommunity Edition（評価版）モデルサイズ上限を検知した場合の
        結果。バッチ分割によるフォールバックは行わず（_solve_cpo呼び出し元の
        コメント参照）、上限超過を伝えるissueのみを返す。
        """
        issue = {
            "id": "ce_limit_unresolvable", "severity": "CRITICAL", "category": "INFEASIBLE",
            "title": "CPLEXの無料版で扱える件数を超えています",
            "message": (
                "タスク数・資源数が多すぎるため、CPLEXの無料版（Community Edition）の"
                "モデルサイズ上限を超えました。データ件数を減らすか、正規ライセンスの"
                "ご利用をご検討ください。"
            ),
            "relatedContainerIds": [],
        }
        solution_data = {
            "feasible": False,
            "schedule": [],
            "makespan": None,
            "kpi": {"makespan": None, "task_count": 0, "objective_value": None, "solve_time": 0.0},
        }
        return solution_data, [issue]

    # ------------------------------------------------------------------
    # ヒューリスティック（CP Optimizer 非利用時のフォールバック）
    # ------------------------------------------------------------------

    def _solve_heuristic(
        self,
        tasks: List[dict],
        resources: List[dict],
        precedences: List[dict],
        config: dict,
    ):
        """
        トポロジカルソート + 資源容量を考慮した貪欲直列スケジューリング（Serial SGS）。
        CP Optimizerが使えない環境向けの簡易実装。最適解は保証しない。

        [2026-07-16 修正] 以前は res_capacity を計算するだけで実際のスケジューリング
        判定に一度も使っておらず、資源容量を超過した割り当てでも常に feasible: True
        を返していた（HANDOFF_2026-07-15b で発見。CP Optimizerが使える本番環境では
        影響しないが、docplex不在のフォールバック時に「解なし」判定が機能しない実害
        があった）。本修正で各タスクの開始時刻を「必要な資源が全て同時に空いている
        最も早いタイミング」まで押し出すようにし（_find_feasible_start）、
        horizon（config.horizon）内に収まらない場合は feasible: False を返す。
        ヒューリスティックの順序は最適性を保証しないため、実際にはhorizon内に
        収まる別順序が存在する場合でも feasible: False と判定しうる点に注意。
        """
        from collections import defaultdict, deque

        # トポロジカルソート
        in_deg: Dict[str, int] = {str(t["id"]): 0 for t in tasks}
        succ:   Dict[str, List] = defaultdict(list)
        delays: Dict[tuple, int] = {}
        for p in precedences:
            f = str(p.get("from_task", ""))
            to = str(p.get("to_task", ""))
            if f in in_deg and to in in_deg:
                in_deg[to] += 1
                succ[f].append(to)
                delays[(f, to)] = int(p.get("min_delay", 0))

        queue = deque([tid for tid, d in in_deg.items() if d == 0])
        order = []
        while queue:
            tid = queue.popleft()
            order.append(tid)
            for s in succ[tid]:
                in_deg[s] -= 1
                if in_deg[s] == 0:
                    queue.append(s)

        task_map = {str(t["id"]): t for t in tasks}
        res_capacity: Dict[str, int] = {str(r["id"]): int(r.get("capacity", 1)) for r in resources}
        # 資源ごとの使用区間タイムライン: {resource_id: [(start, end, amount), ...]}
        resource_timeline: Dict[str, List[tuple]] = defaultdict(list)
        earliest: Dict[str, int] = {tid: 0 for tid in task_map}
        task_end: Dict[str, int] = {}

        horizon_raw = config.get("horizon")
        horizon = int(horizon_raw) if horizon_raw else None

        schedule = []
        infeasible = False
        for tid in order:
            t = task_map.get(tid)
            if t is None:
                continue
            # 前後関係から earliest を更新
            for p in precedences:
                f = str(p.get("from_task", ""))
                to = str(p.get("to_task", ""))
                if to == tid and f in task_end:
                    earliest[tid] = max(earliest[tid], task_end[f] + delays.get((f, to), 0))

            dur = int(t.get("duration", 1))
            req = [(str(rr.get("resource_id", "")), int(rr.get("amount", 1)))
                   for rr in t.get("resource_requirements", [])]

            # 資源容量を考慮した開始時刻（必要な資源が全て空いている最も早いタイミング）
            start = self._find_feasible_start(dur, req, earliest[tid], resource_timeline, res_capacity)
            end = start + dur

            if horizon is not None and end > horizon:
                infeasible = True

            for rid, amount in req:
                resource_timeline[rid].append((start, end, amount))

            schedule.append({
                "task_id":   tid,
                "task_name": t.get("name", tid),
                "start":     start,
                "end":       end,
                "duration":  dur,
                "resource_requirements": t.get("resource_requirements", []),
            })
            task_end[tid] = end

        if infeasible:
            issue = {
                "id": "solve_failed", "severity": "CRITICAL", "category": "INFEASIBLE",
                "title": "最適解が見つかりませんでした（ヒューリスティック）",
                "message": (
                    f"資源容量・前後関係制約を満たすと、horizon={horizon} 以内に収まる"
                    "スケジュールが見つかりませんでした（CP Optimizer非利用時の簡易"
                    "ヒューリスティックによる判定のため、実際には別の順序でhorizon内に"
                    "収まる可能性があります）。"
                ),
                "relatedContainerIds": [],
            }
            infeasible_solution = {
                "feasible": False,
                "schedule": [],
                "makespan": None,
                "kpi": {"makespan": None, "task_count": 0, "objective_value": None, "solve_time": 0.0},
            }
            return infeasible_solution, [issue]

        makespan = max((s["end"] for s in schedule), default=0)
        solution_data = {
            "feasible": True,
            "schedule": schedule,
            "makespan": makespan,
            "kpi": {
                "makespan":        makespan,
                "task_count":      len(schedule),
                "objective_value": float(makespan),
                "solve_time":      0.0,
            },
        }
        return solution_data, []

    @staticmethod
    def _find_feasible_start(
        dur: int,
        req: List[tuple],
        earliest_start: int,
        resource_timeline: Dict[str, List[tuple]],
        res_capacity: Dict[str, int],
    ) -> int:
        """
        req（[(resource_id, amount), ...]）で指定された各資源について、
        [start, start+dur) の間ずっと容量を超過しない最も早い start（>= earliest_start）
        を返す。候補時刻は「earliest_start」と「関連資源の既存使用区間の終了時刻」の
        みで十分（容量の空き状況が変化しうるのはそれらの時点だけのため）。
        """
        if not req:
            return earliest_start

        candidates = {earliest_start}
        for rid, _amount in req:
            for (_s, e, _a) in resource_timeline.get(rid, []):
                if e > earliest_start:
                    candidates.add(e)

        for start in sorted(candidates):
            end = start + dur
            ok = True
            for rid, amount in req:
                cap  = res_capacity.get(rid, 1)
                used = sum(a for (s, e, a) in resource_timeline.get(rid, []) if s < end and start < e)
                if used + amount > cap:
                    ok = False
                    break
            if ok:
                return start

        # 理論上ここには到達しない（最大候補時刻では該当区間が全て終了済みのため
        # 必ず空きがある）。念のためのフォールバック。
        return max(candidates)

    # ------------------------------------------------------------------
    # 結果フォーマット
    # ------------------------------------------------------------------

    def _make_result(
        self,
        status: str,
        solutions: List[dict],
        issues: List[dict],
        meta: dict,
        feasible: bool = False,
    ) -> dict:
        return {
            "status":           status,
            "feasible":         feasible,
            "metadata": {
                "problem_class": "LineChangeoverScheduler",
                "instance_name": meta.get("instance_name", ""),
            },
            "solutions":        solutions,
            "issues":           issues,
            "_solver_version":  self._SOLVER_VERSION,
        }