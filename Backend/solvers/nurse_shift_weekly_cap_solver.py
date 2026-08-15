"""
Backend/solvers/nurse_shift_weekly_cap_solver.py

NurseShiftWeeklyCapSolver — 病院シフト最適化ソルバー

【問題クラス】
  病院の当直・外来シフト割り当て問題（拡張スタッフスケジューリング）

【モデル定式化】
  決定変数:
    assignment_{staff_id}__{task_id} : optional interval_var
      - present = スタッフがそのタスクに割り当てられる

  目的関数（ヒアリング目的関数に完全対応）:
    最小化:
      項1: Σ_s( is_used_s × shift_bind_minutes_s / 60 × hourly_rate_s )  ← 総人件費
      項2: Σ_t( max(0, required_count_t - assigned_t) × UNDERSTAFFING_PENALTY )  ← 不足ペナルティ（全タスク対象）
      項3: Σ_s( unmet_desired_preference_s × preference_penalty_weight )  ← 希望未充足ペナルティ

  制約:
    1. 資格照合: task.requirement（and/or/not条件式）をstaff.skills/certifications等の
       属性セットに対して評価し、Falseになる候補は割り当て候補から除外する
       （2026-07-13: 旧required_skills(暗黙OR)/required_certifications(暗黙AND)の
       フィールドごとに異なる暗黙の論理演算を廃止し、requirement式に統一。
       solvers/base/requirement_expr.py 参照）
    2. 必要人数: quantity_requirement_mode（既定 "soft"）に従い、hardなら
       Σ present_of(assignment) >= required_count をハード制約として追加、
       softならハード制約を追加せず shortfall を項2のペナルティとして計上する
       （solvers/base/quantity_requirement.py 参照。2026-07-18修正: 旧実装は
       hard/softの区別なく無条件で `== required_count` のハード等式を追加しており、
       ヒアリング§4-1の回答（既定b=欠員許容）を一切反映していなかった。
       docs/DESIGN_2026-07-18_layer_ab_pattern_library.md 2-2-2節参照）。
    3. 最低CHIEF数: Σ present_of(CHIEF候補) >= min_chiefs（per task）
    4. no_overlap: 1スタッフは同時に1タスクのみ
    5. 夜勤後最低休憩: 夜勤タスク終了から次タスク開始まで >= min_rest_after_night_minutes
    6. 最大連続夜勤日数: スタッフが連続してnight_shiftタスクに割り当てられる日数 <= max_consecutive_night_shifts
    7. 日次労働時間上限: Σ length_of(assignment) per staff per day <= max_daily_hours * 60

【設計メモ】
  - 日またぎ対応: 各タスクの start/end は「0起点の絶対分数」
    (day_index * 1440 + hhmm_to_min(time)) として表現する。
    converter.py が Business DSL の "day": 0..N-1, "time": "HH:MM" を変換する。
  - 資格照合: task.requirement（and/or/not条件式）を満たさないスタッフは
    割り当て候補から完全に除外（dead dataにしない）。
  - optional interval_var の解抽出には必ず get_var_solution() を使う（禁止パターン5遵守）。
"""

import logging
import time
from typing import Any, Dict, List, Optional, Set, Tuple

from solvers.base.rolling_window import add_rolling_window_cap_constraints
from solvers.base.requirement_expr import evaluate_requirement
from solvers.base.quantity_requirement import (
    DEFAULT_QUANTITY_REQUIREMENT_MODE,
    apply_quantity_requirement,
)
from solvers.base.ce_limit_lns import (
    is_ce_limit_exceeded, CeLimitExceededError, run_solve_with_ce_limit_fallback,
)
from solvers.base.solver_error_result import build_solver_crash_issue, solver_crash_extra_fields
# 2026-07-30 i18n対応: `t` という名前で import すると、_build_and_solve() 内の
# 目的関数合算ループ `for t in all_terms[1:]:`（CP Optimizer項の合算、docplexの
# 慣習的な変数名）がローカル変数 `t` を関数スコープ全体に束縛してしまい、
# 後段の `t("solver.planLabel")` 呼び出しが `CpoFunctionCall` オブジェクトを
# 呼び出そうとして `TypeError: 'CpoFunctionCall' object is not callable` になる
# （Pythonの関数スコープは行単位ではなく関数全体で決まるため）。衝突を避けるため
# `i18n_t` という別名でimportする。
from i18n.nurse_shift_weekly_cap_messages import t as i18n_t

logger = logging.getLogger(__name__)

# 未割り当てペナルティ（1スタッフ分の人件費上限を大きく上回る値）
# 2026-07-18修正: 項1(総人件費)は cost_terms.append(int(unit_cost * 100) * presence)
# のように ×100 スケールで積算されているのに対し、この定数は ×100 されておらず
# 単位が揃っていなかった。実データ（hourly_rate最大2500円/h・duration最大480分/8h）で
# 検証したところ、1件あたりの人件費コスト（スケール後）は最大 2500*8*100=2,000,000 に
# 達するのに対し、旧定数500,000はその約1/4しかなく、「配置するより空席にする方が
# 目的関数上得」という逆転が起きていた。これはhard/softの区別なく常にハード等式で
# 縛っていた旧バグ（quantity_requirement.py導入前）の下では、ハード制約が絶対的に
# 優先されるため露呈しなかったが、hard/softを正しく機能させた結果、soft既定の
# タスクで全員が未割り当てになる形で顕在化した（docs/ENGINEERING_LOG.md参照）。
# cost_termsと同じ ×100 スケールに揃え、実データの最大コスト(2,000,000)を
# 十分上回る値にする。
UNDERSTAFFING_PENALTY = 500_000 * 100

# work_limits既定値。CP制約構築（本クラス内）と、ソルブ後のissue検出
# （_build_hospital_contexts、モジュールレベル関数）の両方から参照する単一のソース。
# 2026-07-14: 両者が別々にリテラルの既定値を持っていたため、issue検出側だけが
# 古い値のままドリフトし、「本当はスタッフ個別のwork_limitsを見るべきところを
# 誤ったデフォルト値と比較して誤検知する」バグが発覚した（INRC-II由来の
# nurse_shift_weekly_cap_inrc_n021w4シナリオで、実際の上限が5のスタッフを
# 誤って"2を超えたら違反"と判定していた）。同じ再発を防ぐため定数を集約する。
DEFAULT_MIN_REST_AFTER_NIGHT   = 480   # 8時間
DEFAULT_MAX_CONSECUTIVE_NIGHTS = 2
DEFAULT_MAX_DAILY_HOURS        = 16


def build_candidates(staff_list: List[Dict], tasks: List[Dict], config: Dict) -> List[Dict]:
    """
    資格照合（requirement式）と勤務可能時間帯のチェックを行い、有効な
    「スタッフ×タスク」の割り当て候補一覧を作る。

    2026-07-26追加: 元々は_build_and_solve内に直接書かれていた処理（Step1）を
    そのまま切り出したもので、挙動は変更していない。グループ分割後に
    「本来の（縮小する前の）人数・タスクで見た場合の候補一覧」を作り直して
    issue（不足件数など）を正しく再計算する必要があるため
    （nurse_shift_weekly_cap_batch_decomposer.pyのmerge_resultsから呼ばれる）、
    独立した関数として公開した。
    """
    quantity_requirement_mode_default = (
        config.get("quantity_requirement_mode") or DEFAULT_QUANTITY_REQUIREMENT_MODE
    )
    if quantity_requirement_mode_default not in ("hard", "soft"):
        quantity_requirement_mode_default = DEFAULT_QUANTITY_REQUIREMENT_MODE

    candidates: List[Dict] = []
    for task in tasks:
        task_id         = task["id"]
        requirement     = task.get("requirement")
        required_count  = task.get("required_count", 1)
        min_chiefs      = task.get("min_chiefs", 0)
        quantity_requirement_mode = (
            task.get("quantity_requirement_mode") or quantity_requirement_mode_default
        )
        duration        = task.get("duration", 60)
        task_start_window = task.get("start_window", 0)
        task_end_window   = task.get("end_window", 1440)
        task_day        = task.get("day", 1)
        day_offset      = (task_day - 1) * 1440

        for staff in staff_list:
            sid = staff["id"]
            staff_attrs: Dict[str, Set[str]] = {
                "skills":         set(staff.get("skills", [])),
                "certifications": set(staff.get("certifications", [])),
                "grade":          {staff.get("grade", "STAFF")},
            }

            if not evaluate_requirement(requirement, staff_attrs):
                continue

            avail = staff.get("availability", {})
            avail_start_abs = avail.get("start", 0) + day_offset
            avail_end_abs   = avail.get("end", 1440) + day_offset
            cand_start = max(task_start_window, avail_start_abs)
            cand_end   = min(task_end_window, avail_end_abs)
            if cand_end - cand_start < duration:
                continue

            grade = staff.get("grade", "STAFF")
            assignment_id = f"{task_id}__{sid}"

            candidates.append({
                "id":               assignment_id,
                "task_id":          task_id,
                "staff_id":         sid,
                "grade":            grade,
                "is_chief":         grade == "CHIEF",
                "required_count":   required_count,
                "quantity_requirement_mode": quantity_requirement_mode,
                "min_chiefs":       min_chiefs,
                "hourly_rate":      staff.get("hourly_rate", 2000),
                "start_window":     cand_start,
                "end_window":       cand_end,
                "duration":         duration,
                "day":              task_day,
                "is_night_shift":   task.get("is_night_shift", False),
            })
    return candidates


def build_task_summary(original_tasks: List[Dict], assigned_tasks: List[Dict]) -> Dict[str, Dict]:
    """
    タスクごとの充足状況サマリ（assigned_count/chief_count等）を作る。

    2026-07-26追加: 元々は_build_and_solve（通常の一括ソルブ経路）と
    nurse_shift_weekly_cap_batch_decomposer.merge_results（グループ分割後の
    統合経路）の両方に、ほぼ同一のロジックが別々にコピーされていた（ドリフトの
    温床）。「グループ分割時だけ専用のロジックを新たに書く」のではなく、通常経路が
    既に持っている集計ロジックをそのまま呼び直せるよう、独立関数として切り出した
    （build_candidates()と同じ考え方）。挙動は変更していない。
    """
    task_summary: Dict[str, Dict] = {}
    for task in original_tasks:
        tid = task["id"]
        task_summary[tid] = {
            "task_id":         tid,
            "task_name":       task.get("name", tid),
            "required_count":  task.get("required_count", 1),
            "min_chiefs":      task.get("min_chiefs", 0),
            "assigned_count":  0,
            "chief_count":     0,
            "day":             task.get("day", 0),
            "start":           task.get("start_window", 0),
            "end":             task.get("end_window", 0),
            "is_night_shift":  task.get("is_night_shift", False),
        }
    for at in assigned_tasks:
        tid = at["task_id"]
        if tid in task_summary:
            task_summary[tid]["assigned_count"] += 1
            if at.get("grade") == "CHIEF":
                task_summary[tid]["chief_count"] += 1
    return task_summary


def build_metrics(
    assigned_tasks: List[Dict],
    task_summary:   Dict[str, Dict],
    staff_list:     List[Dict],
    solve_time:     float = 0.0,
) -> Dict[str, Any]:
    """
    total_cost/coverage_rate/preference_satisfaction_rate等のKPIを作る。

    2026-07-26追加: build_task_summary()と同じ理由で切り出した独立関数
    （通常経路・グループ分割統合経路の両方から呼ばれる）。挙動は変更していない
    （total_costはassigned_tasksの"cost"フィールド（各割当ごとに既に丸め済み）の
    合計とする。通常経路は従来「丸める前のコストを積算してから最後に1回だけ丸める」
    方式だったが、グループ分割統合経路は元々「丸め済みコストの合計」方式だった。
    両者の差は丸め誤差程度（実データでは無視できる範囲）であり、経路によって
    KPI算出方法自体が変わる方が不健全なため、本切り出しを機に統一する）。
    """
    understaffed_count = sum(
        1 for ts in task_summary.values() if ts["assigned_count"] < ts["required_count"]
    )
    coverage_rate = (
        sum(1 for ts in task_summary.values() if ts["assigned_count"] >= ts["required_count"])
        / len(task_summary)
    ) if task_summary else 0.0

    total_cost = sum(at.get("cost", 0) for at in assigned_tasks)

    assigned_pairs = {(at["staff_id"], at["task_id"]) for at in assigned_tasks}
    total_desired = 0
    met_desired = 0
    for staff in staff_list:
        for desired_tid in staff.get("preferences", {}).get("desired_tasks", []):
            total_desired += 1
            if (staff["id"], desired_tid) in assigned_pairs:
                met_desired += 1
    unmet_preference_count = total_desired - met_desired
    preference_satisfaction_rate = (met_desired / total_desired) if total_desired > 0 else 1.0

    return {
        "total_cost":         round(total_cost),
        "assigned_count":     len(assigned_tasks),
        "understaffed_count": understaffed_count,
        "coverage_rate":      round(coverage_rate, 4),
        "solve_time":         round(solve_time, 3),
        "unmet_preference_count":       unmet_preference_count,
        "preference_satisfaction_rate": round(preference_satisfaction_rate, 4),
    }


class NurseShiftWeeklyCapSolver:
    """病院シフト最適化ソルバー。"""

    def __init__(self, solver_input: Dict[str, Any]):
        self.dsl = solver_input
        self._validate()

    # -------------------------------------------------------------------------
    # バリデーション
    # -------------------------------------------------------------------------

    def _validate(self):
        if not self.dsl.get("staff"):
            raise ValueError("[NurseShiftWeeklyCapSolver] staff が空です。")
        if not self.dsl.get("tasks"):
            raise ValueError("[NurseShiftWeeklyCapSolver] tasks が空です。")

    # -------------------------------------------------------------------------
    # 公開エントリポイント
    # -------------------------------------------------------------------------

    def solve(self) -> Dict[str, Any]:
        t_start = time.perf_counter()
        logger.info("[NurseShiftWeeklyCapSolver] solve() 開始")

        staff_list = self.dsl.get("staff", [])
        tasks      = self.dsl.get("tasks", [])
        config     = self.dsl.get("config", {})
        issue_statuses = self.dsl.get("issue_statuses", {})

        logger.info(
            f"[NurseShiftWeeklyCapSolver] staff={len(staff_list)}, tasks={len(tasks)}"
        )

        try:
            # 2026-08-13追加: config.solver_engine（既定はengine_select.pyのポリシー
            # に従う）でCPO(docplex.cp)とCP-SAT(CPMpy)を切り替える。CE-limit
            # フォールバック（CPLEX無料版の組合せ数上限）はCPO固有の事情のため、
            # cpsat経路では発生し得ない（下のexcept節は素通りする）。
            from solvers.base.engine_select import get_solver_engine, CPSAT
            engine = get_solver_engine(config)
            if engine == CPSAT:
                solution_data, candidates = self._build_and_solve_cpsat(staff_list, tasks, config)
            else:
                solution_data, candidates = self._build_and_solve_cpo(staff_list, tasks, config)
        except CeLimitExceededError:
            # 2026-07-26追加、同日中にsolvers/base/ce_limit_lns.pyへ一般化:
            # 一度にまとめて解こうとするとCPLEXの無料版の上限に引っかかる場合、
            # スタッフを何人かずつのグループに分けて順番に解き、最後に結果を
            # まとめる処理に切り替える。retry-depth付き段階的バッチ縮小の
            # ロジック自体はドメイン非依存のため層Aに集約済み（このドメインが
            # 持つのはPRIMARY_ENTITY_KEY/build_subset_input/merge_resultsという
            # 層Bの3点だけ）。
            from solvers.nurse_shift_weekly_cap_batch_decomposer import (
                PRIMARY_ENTITY_KEY, build_subset_input, merge_results,
            )
            merged = run_solve_with_ce_limit_fallback(
                solver_class=NurseShiftWeeklyCapSolver,
                solver_input=self.dsl,
                primary_entity_key=PRIMARY_ENTITY_KEY,
                build_subset_input=build_subset_input,
                merge_results=merge_results,
                problem_class="NurseShiftWeeklyCap",
                solver_version="nurse_shift_weekly_cap_v1.0",
            )
            elapsed = time.perf_counter() - t_start
            logger.info(
                f"[NurseShiftWeeklyCapSolver] グループ分割での解決完了: "
                f"feasible={merged.get('feasible')}, elapsed={elapsed:.2f}s"
            )
            return merged
        except Exception as e:
            # [2026-07-28] mdl.solve()等でCE-limit以外の想定外例外が起きた場合。
            # 以前は_build_and_solve側でNoneを返して以下の通常infeasibleパスに
            # 合流させていたため、実装バグによるクラッシュと業務上のinfeasibleが
            # 区別できなかった。solvers/base/solver_error_result.py 参照。
            logger.error(f"[NurseShiftWeeklyCapSolver] solve() 例外: {e}", exc_info=True)
            result = {
                "status": "ok", "feasible": False,
                "metadata": {"problem_class": "NurseShiftWeeklyCap"},
                "solutions": [], "issues": [build_solver_crash_issue(e)],
                "_solver_version": "nurse_shift_weekly_cap_v1.0",
            }
            result.update(solver_crash_extra_fields(e))
            return result

        issues = self._detect_issues(solution_data, staff_list, tasks, candidates, issue_statuses)

        elapsed = time.perf_counter() - t_start
        feasible = solution_data is not None
        logger.info(
            f"[NurseShiftWeeklyCapSolver] 完了: feasible={feasible}, "
            f"issues={len(issues)}, elapsed={elapsed:.2f}s"
        )

        if not feasible:
            return {
                "status":    "ok",
                "feasible":  False,
                "metadata":  {"problem_class": "NurseShiftWeeklyCap"},
                "solutions": [],
                "issues":    issues,
                "_solver_version": "nurse_shift_weekly_cap_v1.0",
            }

        return {
            "status":    "ok",
            "feasible":  True,
            "metadata":  {"problem_class": "NurseShiftWeeklyCap"},
            "solutions": [solution_data],
            "issues":    issues,
            "_solver_version": "nurse_shift_weekly_cap_v1.0",
        }

    # -------------------------------------------------------------------------
    # モデル構築 & ソルブ
    # -------------------------------------------------------------------------
    def _build_and_solve_cpo(
        self,
        staff_list: List[Dict],
        tasks: List[Dict],
        config: Dict,
    ) -> Tuple[Optional[Dict], List[Dict]]:
        """
        CP Optimizer で病院シフトモデルを構築してソルブする。
        返値: (solution_data, candidates_list)
          - candidates_list: 割り当て候補（issue_rules用に常に返す）
        """
        try:
            from docplex.cp.model import CpoModel
        except ImportError:
            logger.error("[NurseShiftWeeklyCapSolver] docplex がインストールされていません。")
            return None, []

        mdl = CpoModel(name="OptiBuddy_NurseShiftWeeklyCap_v10")
        mdl.set_parameters({"RandomSeed": 42})

        time_limit                   = config.get("time_limit", 60)
        preference_penalty_weight    = config.get("preference_penalty_weight", 200)
        # モジュールレベル定数を使う（issue検出側の _build_hospital_contexts と
        # 既定値をドリフトさせないため。上のコメント参照）。
        DEFAULT_MIN_REST            = DEFAULT_MIN_REST_AFTER_NIGHT
        DEFAULT_MAX_CONSECUTIVE     = DEFAULT_MAX_CONSECUTIVE_NIGHTS
        DEFAULT_MAX_DAILY_HOURS     = 16     # _build_hospital_contexts側に対応チェックが無くドリフトリスク無し
        DEFAULT_WEEKLY_NIGHT_CAP    = 7     # 上限未設定時は実質無制限（7回/週）

        # 2026-07-16修正（Gate2 DSL⇔converterトレーサビリティチェックで発覚）:
        # 以前はここでwindow_days=7を直接ハードコードしており、DSLシナリオの
        # config.rolling_window_daysを設定してもconverterが素通ししないため
        # 一切反映されなかった（converter.py側も同日付で修正済み）。
        rolling_window_days = int(config.get("rolling_window_days", 7))
        if rolling_window_days < 1:
            logger.warning(
                f"[NurseShiftWeeklyCapSolver] rolling_window_days={rolling_window_days} は不正です。"
                "1にフォールバックします。"
            )
            rolling_window_days = 1

        # relax_incomplete_window_at_period_end: 現在のsolvers/base/rolling_window.pyは
        # 「期間末尾の不完全ウィンドウは緩和する」実装のみを持ち、非緩和モードは
        # 未実装。Falseが明示された場合、無言で無視せず警告する（この設定自体が
        # 死んだフィールドだったため、少なくとも「効いていない」ことを可視化する）。
        if not config.get("relax_incomplete_window_at_period_end", True):
            logger.warning(
                "[NurseShiftWeeklyCapSolver] relax_incomplete_window_at_period_end=False が指定されましたが、"
                "現在の実装（solvers/base/rolling_window.py）は緩和モードのみ対応しており、"
                "この設定は反映されません（常に緩和されます）。非緩和モードが必要な場合は"
                "共通コアの拡張が必要です。"
            )

        # ------------------------------------------------------------------
        # Step 1: 資格照合 → 割り当て候補リスト生成
        # 2026-07-26: モジュールレベルのbuild_candidates()に切り出し済み
        # （挙動は変更していない）。
        # ------------------------------------------------------------------
        candidates = build_candidates(staff_list, tasks, config)

        if not candidates:
            logger.warning("[NurseShiftWeeklyCapSolver] 有効な割り当て候補が0件です。")
            return None, candidates

        # ------------------------------------------------------------------
        # Step 2: optional interval_var を生成
        # ------------------------------------------------------------------
        assignment_itvs: Dict[str, Any] = {}
        for cand in candidates:
            aid      = cand["id"]
            start_w  = cand["start_window"]
            end_w    = cand["end_window"]
            dur      = cand["duration"]
            itv = mdl.interval_var(
                start=(start_w, end_w), end=(start_w, end_w), size=dur,
                optional=True, name=f"A_{aid}"
            )
            assignment_itvs[aid] = itv

        # ------------------------------------------------------------------
        # Step 3: 制約
        # ------------------------------------------------------------------

        # 3-1. タスクごとの必要人数制約
        # 2026-07-18修正: hard/softの区別なく無条件で `== required_count` の
        # ハード等式を追加していたバグを修正。solvers/base/quantity_requirement.py
        # を経由し、quantity_requirement_mode（既定 "soft"）に従って分岐する。
        # softの場合に返る shortfall はStep4の目的関数（項2）でそのまま使う
        # （ここで dict に保存し、Step4側では再計算しない）。
        task_cands: Dict[str, List[Dict]] = {}
        for cand in candidates:
            task_cands.setdefault(cand["task_id"], []).append(cand)

        task_shortfall: Dict[str, Optional[Any]] = {}

        for task_id, tcands in task_cands.items():
            required_count = tcands[0]["required_count"]
            mode = tcands[0]["quantity_requirement_mode"]
            present_vars = [mdl.presence_of(assignment_itvs[c["id"]]) for c in tcands]
            shortfall = apply_quantity_requirement(
                mdl, present_vars, required_count, mode=mode, comparison="at_least"
            )
            task_shortfall[task_id] = shortfall  # soft: CpoExpr / hard: None

            # 3-2. 最低CHIEF数
            min_chiefs = tcands[0]["min_chiefs"]
            if min_chiefs > 0:
                chief_vars = [
                    mdl.presence_of(assignment_itvs[c["id"]])
                    for c in tcands if c["is_chief"]
                ]
                if chief_vars:
                    mdl.add(mdl.sum(chief_vars) >= min_chiefs)
                else:
                    # 修正(2026-07-13): 以前はここで制約を追加せずスキップしていたため、
                    # grade="CHIEF"の候補が1人もいないシナリオでは min_chiefs 要件が
                    # 全タスクで無言で無効化されるバグがあった（Feasibleと出るのに
                    # 責任者0名、という見た目は動いているが実は違うバグ）。
                    # 候補が0件＝この要件は構造的に充足不可能なので、無視せず
                    # 矛盾する制約を明示的に追加してモデル全体を確実にinfeasible化する。
                    logger.warning(
                        f"[NurseShiftWeeklyCapSolver] タスク {task_id}: "
                        f"min_chiefs={min_chiefs} ですが grade=CHIEF の候補が0件です。"
                        f"構造的に充足不可能なため、制約により明示的にinfeasibleにします。"
                    )
                    dummy = mdl.binary_var(name=f"__no_chief_candidate__{task_id}")
                    mdl.add(dummy == 1)
                    mdl.add(dummy == 0)

        # 3-3. スタッフごとの no_overlap + 夜勤後休憩 + 連続夜勤制限 + ローリング週次夜勤上限
        staff_by_id: Dict[str, Dict] = {s["id"]: s for s in staff_list}
        staff_cands_map: Dict[str, List[Dict]] = {}
        for cand in candidates:
            staff_cands_map.setdefault(cand["staff_id"], []).append(cand)

        # 全タスクの日番号を収集（ローリングウィンドウ計算に使用）
        all_days_sorted = sorted({task.get("day", 1) for task in tasks})

        for sid, scands in staff_cands_map.items():
            itvs_for_staff = [assignment_itvs[c["id"]] for c in scands]

            staff_rec   = staff_by_id.get(sid, {})
            work_limits = staff_rec.get("work_limits", {})
            min_rest_after_night_minutes = work_limits.get("min_rest_after_night_shift", DEFAULT_MIN_REST)
            max_consecutive_nights       = work_limits.get("max_consecutive_night_shifts", DEFAULT_MAX_CONSECUTIVE)
            max_daily_hours              = work_limits.get("max_daily_hours", DEFAULT_MAX_DAILY_HOURS)
            # ローリング週次夜勤上限（スタッフ個別設定）
            # キー名は必ず "max_night_shifts_per_rolling_7days" と一致させること。
            # 別名にすると常にデフォルト値へフォールバックし、制約が無言で無効化される
            # （経緯: docs/ENGINEERING_LOG.md 2026-07-12 不具合1）。
            weekly_night_cap             = work_limits.get("max_night_shifts_per_rolling_7days", DEFAULT_WEEKLY_NIGHT_CAP)

            # no_overlap
            if len(itvs_for_staff) > 1:
                mdl.add(mdl.no_overlap(itvs_for_staff))

            # 夜勤後最低休憩制約
            night_cands = [c for c in scands if c["is_night_shift"]]
            day_cands   = [c for c in scands if not c["is_night_shift"]]

            for nc in night_cands:
                nc_itv = assignment_itvs[nc["id"]]
                for dc in day_cands:
                    if dc["day"] > nc["day"]:
                        dc_itv = assignment_itvs[dc["id"]]
                        mdl.add(mdl.end_before_start(nc_itv, dc_itv, delay=min_rest_after_night_minutes))

            # 連続夜勤日数制限
            if len(night_cands) > max_consecutive_nights:
                nights_by_day: Dict[int, List[str]] = {}
                for nc in night_cands:
                    nights_by_day.setdefault(nc["day"], []).append(nc["id"])

                days_sorted = sorted(nights_by_day.keys())
                for i in range(len(days_sorted) - max_consecutive_nights):
                    window_days = days_sorted[i : i + max_consecutive_nights + 1]
                    if window_days[-1] - window_days[0] == max_consecutive_nights:
                        window_presence = []
                        for d in window_days:
                            for aid in nights_by_day.get(d, []):
                                window_presence.append(mdl.presence_of(assignment_itvs[aid]))
                        if window_presence:
                            mdl.add(mdl.sum(window_presence) <= max_consecutive_nights)

            # 日次労働時間上限
            days_set: Set[int] = {c["day"] for c in scands}
            for day in days_set:
                day_itvs = [
                    assignment_itvs[c["id"]]
                    for c in scands if c["day"] == day
                ]
                if day_itvs:
                    total_dur = mdl.sum([mdl.length_of(itv, 0) for itv in day_itvs])
                    mdl.add(total_dur <= max_daily_hours * 60)

            # ── ローリングウィンドウ週次夜勤上限制約 ──────────────────────
            # 「任意の連続7日間において夜勤回数 <= weekly_night_cap」。
            # 境界処理（期間がウィンドウ長より短い場合のクリップ等）は
            # solvers/base/rolling_window.py に共通コア化済み。ここでは
            # 再実装せず、日番号ごとの presence 式を渡して呼び出すだけにする
            # （経緯: docs/ENGINEERING_LOG.md 2026-07-12 不具合2）。
            if night_cands and weekly_night_cap < DEFAULT_WEEKLY_NIGHT_CAP:
                night_cands_by_day: Dict[int, List[Dict]] = {}
                for nc in night_cands:
                    night_cands_by_day.setdefault(nc["day"], []).append(nc)

                presence_by_day = {
                    d: [mdl.presence_of(assignment_itvs[nc["id"]]) for nc in cands]
                    for d, cands in night_cands_by_day.items()
                }

                add_rolling_window_cap_constraints(
                    mdl,
                    presence_by_day,
                    window_days=rolling_window_days,
                    cap=weekly_night_cap,
                    all_days=all_days_sorted,
                )

        # ------------------------------------------------------------------
        # Step 4: 目的関数（ヒアリング目的関数 全3項）
        # ------------------------------------------------------------------

        # ── 項1: 総人件費 ────────────────────────────────────────────────────
        cost_terms = []
        for sid, scands in staff_cands_map.items():
            hourly_rate = scands[0].get("hourly_rate", 2000)
            for c in scands:
                presence = mdl.presence_of(assignment_itvs[c["id"]])
                unit_cost = (c["duration"] / 60.0) * hourly_rate
                if unit_cost > 0:
                    cost_terms.append(int(unit_cost * 100) * presence)

        # ── 項2: 不足ペナルティ ──────────────────────────────────────────────
        # 2026-07-18修正: Step3-1で算出済みの shortfall をそのまま使う（再計算しない）。
        # mode="hard" のタスクは shortfall=None（ハード制約により不足自体が
        # 発生し得ない＝infeasibleになるかexactに満たされるかのどちらか）なので
        # ペナルティ項からは除外する。
        understaffing_terms = []
        for task_id, shortfall in task_shortfall.items():
            if shortfall is not None:
                understaffing_terms.append(UNDERSTAFFING_PENALTY * shortfall)

        # ── 項3: 希望未充足ペナルティ ───────────────────────────────────────
        preference_terms = []
        for sid, scands in staff_cands_map.items():
            staff = next((s for s in staff_list if s["id"] == sid), {})
            desired_tasks = staff.get("preferences", {}).get("desired_tasks", [])
            if not desired_tasks:
                continue
            for desired_tid in desired_tasks:
                desired_cands = [c for c in scands if c["task_id"] == desired_tid]
                if not desired_cands:
                    continue
                desired_present = [mdl.presence_of(assignment_itvs[c["id"]]) for c in desired_cands]
                not_assigned = 1 - mdl.max(desired_present)
                preference_terms.append(preference_penalty_weight * not_assigned)

        all_terms = []
        if cost_terms:
            all_terms.append(mdl.sum(cost_terms))
        if understaffing_terms:
            all_terms.append(mdl.sum(understaffing_terms))
        if preference_terms:
            all_terms.append(mdl.sum(preference_terms))

        if all_terms:
            objective = all_terms[0]
            for t in all_terms[1:]:
                objective = objective + t
            mdl.add(mdl.minimize(objective))

        # ------------------------------------------------------------------
        # Step 5: ソルブ
        # ------------------------------------------------------------------
        try:
            msol = mdl.solve(TimeLimit=time_limit, LogVerbosity="Quiet")
        except Exception as e:
            if is_ce_limit_exceeded(e):
                # 2026-07-26追加: CPLEXの無料版で解ける組み合わせ数の上限に
                # 引っかかった場合。ここでは直せないので、呼び出し元のsolve()が
                # スタッフをグループ分けして順番に解く処理に切り替えられるよう、
                # 層A共通の例外として投げ直す（solvers/base/ce_limit_lns.py参照。
                # 以前はドメイン専用の_NurseShiftCeLimitErrorだったが層Aへ集約）。
                raise CeLimitExceededError(str(e)) from e
            # [2026-07-28] 従来はここで return None, candidates として、genuine
            # infeasibleと同じ扱いにしていた（呼び出し元solve()のfeasible=Falseパス
            # がそのまま走り、クラッシュか本当にinfeasibleか区別できなかった）。
            # line_changeover_scheduler_solver.py の非CE-limit例外と同じく再raiseし、
            # solve()側の except Exception で crash専用の結果を返す
            # （solvers/base/solver_error_result.py 参照）。
            logger.error(f"[NurseShiftWeeklyCapSolver] mdl.solve() 例外: {e}", exc_info=True)
            raise

        if msol is None or not msol.is_solution():
            logger.warning("[NurseShiftWeeklyCapSolver] 解なし (infeasible or timeout)")
            return None, candidates

        # 2026-07-18e追加: 実機ログでbaselineシナリオが毎回time_limit（既定30秒）を
        # フルに使い切っていた（elapsed=30.1x秒）ことを受け、「time_limitが長すぎるのでは」
        # という指摘への判断材料として、探索が実際に最適性を証明できたのか
        # （Optimal）、それとも打ち切りで終わっただけなのか（Feasible止まり）を
        # ログに残す。Optimalに到達しているのにtime_limitをフル消費している場合のみ、
        # 短縮しても解の質を落とさない証拠になる（get_solve_status/get_solve_timeは
        # line_changeover_scheduler_solver.py等で既に使用実績のある標準API）。
        try:
            solve_status = msol.get_solve_status()
            solve_time_actual = msol.get_solve_time()
            logger.info(
                f"[NurseShiftWeeklyCapSolver] 探索終了ステータス: status={solve_status}, "
                f"実測solve_time={solve_time_actual:.2f}s / time_limit={time_limit}s"
                + ("（最適性証明済み・time_limitを短縮しても解の質は変わらない可能性が高い）"
                   if solve_status == "Optimal" and solve_time_actual < time_limit * 0.95
                   else "")
            )
        except Exception as e:
            logger.warning(f"[NurseShiftWeeklyCapSolver] 探索終了ステータスの取得に失敗（無視して続行）: {e}")

        # ------------------------------------------------------------------
        # Step 6: 解の抽出
        # ------------------------------------------------------------------
        assigned_tasks: List[Dict] = []

        for cand in candidates:
            aid = cand["id"]
            itv = assignment_itvs.get(aid)
            if itv is None:
                continue

            var_sol = msol.get_var_solution(itv)
            if var_sol is None or not var_sol.is_present():
                continue

            start_val = var_sol.get_start()
            end_val   = var_sol.get_end()

            staff = next((s for s in staff_list if s["id"] == cand["staff_id"]), {})
            # total_costはbuild_metrics()がassigned_tasksの"cost"（丸め済み）から
            # 合算して算出する（2026-07-26: ここでの重複した積算をやめた）。
            cost_for_assignment = (cand["duration"] / 60.0) * cand["hourly_rate"]

            assigned_tasks.append({
                "assignment_id": aid,
                "task_id":       cand["task_id"],
                "staff_id":      cand["staff_id"],
                "staff_name":    staff.get("name", cand["staff_id"]),
                "grade":         cand["grade"],
                "start":         start_val,
                "end":           end_val,
                "duration":      cand["duration"],
                "day":           cand["day"],
                "is_night_shift": cand["is_night_shift"],
                "hourly_rate":   cand["hourly_rate"],
                "cost":          round(cost_for_assignment),
            })

        if len(candidates) > 0 and len(assigned_tasks) == 0:
            logger.error(
                "[NurseShiftWeeklyCapSolver] 全候補が未割当（解抽出バグの可能性）: "
                "get_var_solution() の戻り値を確認してください。"
            )

        solve_time = msol.get_solve_time() if hasattr(msol, "get_solve_time") else 0.0
        solution_data = self._finalize_nurse_solution(
            assigned_tasks, staff_list, solve_time, rolling_window_days
        )
        return solution_data, candidates

    # -------------------------------------------------------------------------
    # KPI計算・結果組み立て（CPO/CP-SAT共通、2026-08-13切り出し）
    # -------------------------------------------------------------------------
    def _finalize_nurse_solution(
        self,
        assigned_tasks: List[Dict],
        staff_list: List[Dict],
        solve_time: float,
        rolling_window_days: int,
    ) -> Dict[str, Any]:
        """
        解抽出済みの assigned_tasks から、KPI・スタッフ別集計・最終レスポンス辞書を
        組み立てる。CPO(_build_and_solve_cpo)・CP-SAT(_build_and_solve_cpsat)の
        両エンジンから同一ロジックで呼ばれる（KPI計算のドリフトを防ぐため、
        エンジンごとに再実装しない）。
        """
        # KPI計算
        # 2026-07-26: build_task_summary()/build_metrics()に切り出し済み
        # （グループ分割統合経路 nurse_shift_weekly_cap_batch_decomposer.merge_results
        # からも同じ関数を呼ぶことで、KPIロジックの二重実装・ドリフトを防ぐ）。
        task_summary = build_task_summary(self.dsl.get("tasks", []), assigned_tasks)

        # ── 週次夜勤回数 per staff を計算（UI表示用）──────────────────────────
        # 「スタッフごとの直近1週間の夜勤回数」として、ここでは
        # スケジューリング期間全体での合計夜勤回数を計算する（週単位の窓は
        # UI側でそのままカウントとして使う）。
        # より厳密には「最も多い7日間ウィンドウでの夜勤回数」を出すが、
        # ユーザーの意図（スタッフ別稼働サマリの追加列）としては全期間合計で
        # 十分な参考値になるため、集計はシンプルにする。
        # ただし、制約として課した「ローリングウィンドウ最大値」を表示するため
        # 各スタッフの最大ウィンドウ内夜勤回数も算出する。

        # スタッフ別・日別夜勤フラグを収集
        staff_night_days_map: Dict[str, List[int]] = {}
        for at in assigned_tasks:
            if at.get("is_night_shift"):
                staff_night_days_map.setdefault(at["staff_id"], []).append(at["day"])

        # スタッフごとに「全期間のローリングウィンドウ最大夜勤回数」を計算
        staff_rolling_night_counts: Dict[str, int] = {}
        for staff in staff_list:
            sid = staff["id"]
            night_days = sorted(set(staff_night_days_map.get(sid, [])))
            if not night_days:
                staff_rolling_night_counts[sid] = 0
                continue
            # ローリングウィンドウ（rolling_window_days日）で最大値を求める
            # 2026-07-16修正: 制約側と同じくハードコード7日だったため、
            # rolling_window_daysを変えても表示側だけ7日のまま食い違っていた。
            max_in_window = 0
            for i, d in enumerate(night_days):
                # d を起点として d+(rolling_window_days-1) 以内の夜勤日数を数える
                count = sum(1 for nd in night_days if d <= nd <= d + rolling_window_days - 1)
                if count > max_in_window:
                    max_in_window = count
            staff_rolling_night_counts[sid] = max_in_window

        metrics = build_metrics(assigned_tasks, task_summary, staff_list, solve_time=solve_time)

        logger.info(
            f"[NurseShiftWeeklyCapSolver] 解: "
            f"assigned={len(assigned_tasks)}, total_cost={metrics['total_cost']:,.0f}, "
            f"understaffed={metrics['understaffed_count']}, coverage={metrics['coverage_rate']:.1%}"
        )

        return {
            "name":          "Plan A",
            "label":         i18n_t("solver.planLabel"),
            "feasible":      True,
            "tasks":         assigned_tasks,
            "task_summary":  list(task_summary.values()),
            "metrics":       metrics,
            # UI表示用: スタッフごとのローリング7日間最大夜勤回数
            "staff_rolling_night_counts": staff_rolling_night_counts,
        }

    # -------------------------------------------------------------------------
    # CP-SAT(CPMpy)版
    # -------------------------------------------------------------------------
    def _build_and_solve_cpsat(
        self,
        staff_list: List[Dict],
        tasks: List[Dict],
        config: Dict,
    ) -> Tuple[Optional[Dict], List[Dict]]:
        """
        CP-SAT(CPMpy)で病院シフトモデルを構築してソルブする。
        _build_and_solve_cpo()と同じ入出力契約(solution_data, candidates_list)。

        docplex.cp の optional interval_var(start=(sw,ew), end=(sw,ew), size=dur)
        は、CPMpyでは「present=boolvar・start=intvar(sw, ew-dur)・end=start+dur」
        という真にフレキシブルな開始時刻を持つ表現に対応する
        （検証: Backend/tools/pilot_cpmpy_nurse_shift_weekly_cap.py。
        NoOverlapOptional・end_before_start(delay)相当の含意制約が真にフレキシブルな
        start変数のもとでも正しく機能することをbrute-forceで照合済み）。
        quantity_requirement(hard/soft)・週次夜勤ローリング上限は、層Aの共通
        ヘルパー(quantity_requirement.py/rolling_window.py)をCpmpyMdlAdapter
        経由でそのまま再利用する(hard/soft分岐やウィンドウ境界処理を
        エンジンごとに再実装しない)。
        """
        import cpmpy as cp
        from solvers.base.cpmpy_mdl_adapter import CpmpyMdlAdapter

        time_limit = config.get("time_limit", 60)
        preference_penalty_weight = config.get("preference_penalty_weight", 200)
        DEFAULT_MIN_REST = DEFAULT_MIN_REST_AFTER_NIGHT
        DEFAULT_MAX_CONSECUTIVE = DEFAULT_MAX_CONSECUTIVE_NIGHTS
        DEFAULT_MAX_DAILY_HOURS_LOCAL = 16
        DEFAULT_WEEKLY_NIGHT_CAP = 7

        rolling_window_days = int(config.get("rolling_window_days", 7))
        if rolling_window_days < 1:
            logger.warning(
                f"[NurseShiftWeeklyCapSolver][cpsat] rolling_window_days={rolling_window_days} は不正です。"
                "1にフォールバックします。"
            )
            rolling_window_days = 1

        if not config.get("relax_incomplete_window_at_period_end", True):
            logger.warning(
                "[NurseShiftWeeklyCapSolver][cpsat] relax_incomplete_window_at_period_end=False が指定されましたが、"
                "現在の実装（solvers/base/rolling_window.py）は緩和モードのみ対応しており、"
                "この設定は反映されません（常に緩和されます）。"
            )

        candidates = build_candidates(staff_list, tasks, config)
        if not candidates:
            logger.warning("[NurseShiftWeeklyCapSolver][cpsat] 有効な割り当て候補が0件です。")
            return None, candidates

        m = cp.Model()
        adapter = CpmpyMdlAdapter(m)

        # Step2相当: present(boolvar) + start(intvar, sw..ew-dur) + end(=start+dur)
        present: Dict[str, Any] = {}
        start: Dict[str, Any] = {}
        end: Dict[str, Any] = {}
        for cand in candidates:
            aid = cand["id"]
            sw, ew, dur = cand["start_window"], cand["end_window"], cand["duration"]
            present[aid] = cp.boolvar(name=f"present_{aid}")
            start[aid] = cp.intvar(sw, ew - dur, name=f"start_{aid}")
            end[aid] = start[aid] + dur
            # 2026-08-13修正（テストで発覚）: そのスタッフの候補が1件しかない等、
            # start[aid] がno_overlap/夜勤後休憩のどの制約からも一切参照されない
            # ケースでは、CPMpy(CP-SAT)がこの変数をフラットモデルに含めず、
            # solve()後も.value()がNoneのままになる（変数のドメイン自体は
            # [sw, ew-dur]なので、値がNoneでも制約上は問題ないが、Step6の解抽出で
            # int(None)がTypeErrorになる）。ドメイン境界を再掲するだけの自明な
            # 制約を明示的に追加し、必ずモデルに含めて具体値を持たせる。
            m += (start[aid] >= sw)

        # Step3-1: タスクごとの必要人数制約 + 最低CHIEF数
        task_cands: Dict[str, List[Dict]] = {}
        for cand in candidates:
            task_cands.setdefault(cand["task_id"], []).append(cand)

        task_shortfall: Dict[str, Optional[Any]] = {}
        for task_id, tcands in task_cands.items():
            required_count = tcands[0]["required_count"]
            mode = tcands[0]["quantity_requirement_mode"]
            present_vars = [present[c["id"]] for c in tcands]
            shortfall = apply_quantity_requirement(
                adapter, present_vars, required_count, mode=mode, comparison="at_least"
            )
            task_shortfall[task_id] = shortfall

            min_chiefs = tcands[0]["min_chiefs"]
            if min_chiefs > 0:
                chief_vars = [present[c["id"]] for c in tcands if c["is_chief"]]
                if chief_vars:
                    m += (cp.sum(chief_vars) >= min_chiefs)
                else:
                    logger.warning(
                        f"[NurseShiftWeeklyCapSolver][cpsat] タスク {task_id}: "
                        f"min_chiefs={min_chiefs} ですが grade=CHIEF の候補が0件です。"
                        f"構造的に充足不可能なため、制約により明示的にinfeasibleにします。"
                    )
                    dummy = cp.boolvar(name=f"__no_chief_candidate__{task_id}")
                    m += (dummy == 1)
                    m += (dummy == 0)

        # Step3-3: スタッフごとの no_overlap + 夜勤後休憩 + 連続夜勤制限
        #          + 日次上限 + ローリング週次夜勤上限
        staff_by_id: Dict[str, Dict] = {s["id"]: s for s in staff_list}
        staff_cands_map: Dict[str, List[Dict]] = {}
        for cand in candidates:
            staff_cands_map.setdefault(cand["staff_id"], []).append(cand)

        all_days_sorted = sorted({task.get("day", 1) for task in tasks})

        for sid, scands in staff_cands_map.items():
            staff_rec = staff_by_id.get(sid, {})
            work_limits = staff_rec.get("work_limits", {})
            min_rest_after_night_minutes = work_limits.get("min_rest_after_night_shift", DEFAULT_MIN_REST)
            max_consecutive_nights = work_limits.get("max_consecutive_night_shifts", DEFAULT_MAX_CONSECUTIVE)
            max_daily_hours = work_limits.get("max_daily_hours", DEFAULT_MAX_DAILY_HOURS_LOCAL)
            weekly_night_cap = work_limits.get("max_night_shifts_per_rolling_7days", DEFAULT_WEEKLY_NIGHT_CAP)

            # no_overlap（真にフレキシブルなstart込み。パイロットで検証済みのパターン）
            if len(scands) > 1:
                starts = [start[c["id"]] for c in scands]
                durs   = [c["duration"] for c in scands]
                ends   = [end[c["id"]] for c in scands]
                pres   = [present[c["id"]] for c in scands]
                m += cp.NoOverlapOptional(starts, durs, ends, pres)

            # 夜勤後最低休憩制約
            night_cands = [c for c in scands if c["is_night_shift"]]
            day_cands   = [c for c in scands if not c["is_night_shift"]]
            for nc in night_cands:
                for dc in day_cands:
                    if dc["day"] > nc["day"]:
                        both = present[nc["id"]] & present[dc["id"]]
                        m += both.implies(end[nc["id"]] + min_rest_after_night_minutes <= start[dc["id"]])

            # 連続夜勤日数制限
            if len(night_cands) > max_consecutive_nights:
                nights_by_day: Dict[int, List[str]] = {}
                for nc in night_cands:
                    nights_by_day.setdefault(nc["day"], []).append(nc["id"])

                days_sorted = sorted(nights_by_day.keys())
                for i in range(len(days_sorted) - max_consecutive_nights):
                    window_days = days_sorted[i : i + max_consecutive_nights + 1]
                    if window_days[-1] - window_days[0] == max_consecutive_nights:
                        window_presence = []
                        for d in window_days:
                            for aid in nights_by_day.get(d, []):
                                window_presence.append(present[aid])
                        if window_presence:
                            m += (cp.sum(window_presence) <= max_consecutive_nights)

            # 日次労働時間上限
            days_set: Set[int] = {c["day"] for c in scands}
            for day in days_set:
                day_cands_for_day = [c for c in scands if c["day"] == day]
                if day_cands_for_day:
                    total_dur = cp.sum([c["duration"] * present[c["id"]] for c in day_cands_for_day])
                    m += (total_dur <= max_daily_hours * 60)

            # ローリングウィンドウ週次夜勤上限制約（層Aの共通コアをアダプタ経由で再利用）
            if night_cands and weekly_night_cap < DEFAULT_WEEKLY_NIGHT_CAP:
                night_cands_by_day: Dict[int, List[Dict]] = {}
                for nc in night_cands:
                    night_cands_by_day.setdefault(nc["day"], []).append(nc)

                presence_by_day = {
                    d: [present[nc["id"]] for nc in cands_]
                    for d, cands_ in night_cands_by_day.items()
                }

                add_rolling_window_cap_constraints(
                    adapter,
                    presence_by_day,
                    window_days=rolling_window_days,
                    cap=weekly_night_cap,
                    all_days=all_days_sorted,
                )

        # Step4: 目的関数（ヒアリング目的関数 全3項）
        # SCALE=100はCPO側の `int(unit_cost * 100)` と完全に同じ切り捨てスケールに
        # 揃える（実CPLEX照合バッチで数値一致を確認する対象のため）。
        SCALE = 100
        cost_terms = []
        for sid, scands in staff_cands_map.items():
            hourly_rate = scands[0].get("hourly_rate", 2000)
            for c in scands:
                unit_cost = (c["duration"] / 60.0) * hourly_rate
                scaled = int(unit_cost * SCALE)
                if scaled != 0:
                    cost_terms.append(scaled * present[c["id"]])

        understaffing_terms = []
        for task_id, shortfall in task_shortfall.items():
            if shortfall is not None:
                understaffing_terms.append(UNDERSTAFFING_PENALTY * shortfall)

        preference_terms = []
        for sid, scands in staff_cands_map.items():
            staff = next((s for s in staff_list if s["id"] == sid), {})
            desired_tasks = staff.get("preferences", {}).get("desired_tasks", [])
            if not desired_tasks:
                continue
            for desired_tid in desired_tasks:
                desired_cands = [c for c in scands if c["task_id"] == desired_tid]
                if not desired_cands:
                    continue
                desired_present = [present[c["id"]] for c in desired_cands]
                not_assigned = 1 - cp.max(desired_present)
                preference_terms.append(preference_penalty_weight * not_assigned)

        all_terms = []
        if cost_terms:
            all_terms.append(cp.sum(cost_terms))
        if understaffing_terms:
            all_terms.append(cp.sum(understaffing_terms))
        if preference_terms:
            all_terms.append(cp.sum(preference_terms))

        if all_terms:
            objective = all_terms[0]
            for term in all_terms[1:]:
                objective = objective + term
            m.minimize(objective)

        # Step5: ソルブ
        t0 = time.perf_counter()
        solved = m.solve(solver="ortools", time_limit=time_limit)
        solve_time = time.perf_counter() - t0

        if not solved:
            logger.warning("[NurseShiftWeeklyCapSolver][cpsat] 解なし (infeasible or timeout)")
            return None, candidates

        # Step6: 解の抽出
        assigned_tasks: List[Dict] = []
        for cand in candidates:
            aid = cand["id"]
            if not present[aid].value():
                continue

            start_val = int(start[aid].value())
            end_val = start_val + cand["duration"]

            staff = next((s for s in staff_list if s["id"] == cand["staff_id"]), {})
            cost_for_assignment = (cand["duration"] / 60.0) * cand["hourly_rate"]

            assigned_tasks.append({
                "assignment_id": aid,
                "task_id":       cand["task_id"],
                "staff_id":      cand["staff_id"],
                "staff_name":    staff.get("name", cand["staff_id"]),
                "grade":         cand["grade"],
                "start":         start_val,
                "end":           end_val,
                "duration":      cand["duration"],
                "day":           cand["day"],
                "is_night_shift": cand["is_night_shift"],
                "hourly_rate":   cand["hourly_rate"],
                "cost":          round(cost_for_assignment),
            })

        if len(candidates) > 0 and len(assigned_tasks) == 0:
            logger.error(
                "[NurseShiftWeeklyCapSolver][cpsat] 全候補が未割当（解抽出バグの可能性）: "
                "present[aid].value() を確認してください。"
            )

        solution_data = self._finalize_nurse_solution(
            assigned_tasks, staff_list, solve_time, rolling_window_days
        )
        return solution_data, candidates

    def _detect_issues(
        self,
        solution_data:  Optional[Dict],
        staff_list:     List[Dict],
        tasks:          List[Dict],
        candidates:     List[Dict],
        issue_statuses: Dict,
    ) -> List[Dict]:
        from solvers.base.issue_rules import run_issue_rules, build_full_unassignment_issue

        config = self.dsl.get("config", {})

        # 全件未割当異常検知（安全網）
        extra_issues = []
        if solution_data is not None:
            assigned_count = len(solution_data.get("tasks", []))
            total_count    = len(candidates)
            anomaly = build_full_unassignment_issue(
                assigned_count=assigned_count,
                total_count=total_count,
                entity_label=i18n_t("solver.assignmentCandidateLabel"),
                extra_hint="get_var_solution() での解抽出処理",
            )
            if anomaly:
                extra_issues.append(anomaly)

        contexts = _build_hospital_contexts(
            solution_data=solution_data,
            staff_list=staff_list,
            tasks=tasks,
            candidates=candidates,
            config=config,
        )
        issues = run_issue_rules(
            domain         = "NurseShiftWeeklyCap",
            contexts       = contexts,
            issue_statuses = issue_statuses,
        )
        return extra_issues + issues


# -------------------------------------------------------------------------
# issue_rules コンテキストビルダー（モジュールレベル関数）
# -------------------------------------------------------------------------

def _build_hospital_contexts(
    solution_data: Optional[Dict],
    staff_list:    List[Dict],
    tasks:         List[Dict],
    candidates:    List[Dict],
    config:        Dict,
) -> List[Dict[str, Any]]:
    """NurseShiftWeeklyCap 用の context リストを生成する。"""
    ctxs: List[Dict] = []

    infeasible_detail: List[Dict] = []
    if solution_data is None:
        # 診断情報を構築
        task_req_summary = {}
        for c in candidates:
            tid = c["task_id"]
            if tid not in task_req_summary:
                task_req_summary[tid] = {
                    "task_id":          tid,
                    "required_count":   c["required_count"],
                    "min_chiefs":       c["min_chiefs"],
                    "eligible_staff":   [],
                }
            task_req_summary[tid]["eligible_staff"].append(c["staff_id"])

        staff_summary = [
            {
                "staff_id":         s["id"],
                "grade":            s.get("grade", "STAFF"),
                "certifications":   s.get("certifications", []),
                "available_days":   s.get("available_days", []),
            }
            for s in staff_list
        ]
        infeasible_detail = [
            {"task_requirements": list(task_req_summary.values())},
            {"staff_summary":     staff_summary},
            {"config":            config},
        ]

    ctxs.append({
        "_rule_id":          "solve_failed",
        "solution_data":     solution_data,
        "infeasible_detail": infeasible_detail,
    })

    if solution_data is None:
        return ctxs

    assigned_tasks = solution_data.get("tasks", [])

    # タスク別集計
    task_req: Dict[str, Dict] = {}
    for c in candidates:
        tid = c["task_id"]
        if tid not in task_req:
            task_req[tid] = {
                "required_count": c["required_count"],
                "min_chiefs":     c["min_chiefs"],
                "assigned":       0,
                "chiefs":         0,
            }
    for at in assigned_tasks:
        tid = at["task_id"]
        if tid in task_req:
            task_req[tid]["assigned"] += 1
            if at.get("grade") == "CHIEF":
                task_req[tid]["chiefs"] += 1

    for tid, req in task_req.items():
        # 不足チェック
        ctxs.append({"_rule_id": "understaffed", "tid": tid, "req": req})
        # CHIEF不足チェック
        ctxs.append({"_rule_id": "no_chief", "tid": tid, "req": req})

    # スタッフ未割り当てチェック
    staff_ids_with_assignment = {at["staff_id"] for at in assigned_tasks}
    for staff in staff_list:
        sid = staff["id"]
        ctxs.append({
            "_rule_id": "unassigned_staff",
            "sid": sid,
            "staff": staff,
            "staff_ids_with_assignment": staff_ids_with_assignment,
        })

    # スタッフID→レコードの索引（このあとの2チェックで、スタッフ個別の
    # work_limitsを引くために使う。既存の `next((s for s in staff_list ...` という
    # 線形探索パターンはそのまま残す＝挙動を変えない範囲の修正に留める）。
    staff_by_id = {s["id"]: s for s in staff_list}

    # 夜勤後休憩違反チェック
    # 2026-07-14修正: 以前は config.get("min_rest_after_night_minutes", 480) という
    # グローバル・かつ存在しないキー（実際は staff.work_limits.min_rest_after_night_shift
    # というスタッフ個別フィールド）を見ており、常にデフォルト値480にフォールバックして
    # いた。スタッフごとの実際の設定値を見るよう修正する。
    staff_tasks_map: Dict[str, List[Dict]] = {}
    for at in assigned_tasks:
        staff_tasks_map.setdefault(at["staff_id"], []).append(at)

    for sid, atasks in staff_tasks_map.items():
        staff_work_limits = staff_by_id.get(sid, {}).get("work_limits", {})
        min_rest = staff_work_limits.get("min_rest_after_night_shift", DEFAULT_MIN_REST_AFTER_NIGHT)

        atasks_sorted = sorted(atasks, key=lambda t: t["start"])
        for i in range(len(atasks_sorted) - 1):
            cur = atasks_sorted[i]
            nxt = atasks_sorted[i + 1]
            if cur.get("is_night_shift"):
                rest_actual = nxt["start"] - cur["end"]
                if rest_actual < min_rest:
                    ctxs.append({
                        "_rule_id":       "night_rest_violation",
                        "sid":            sid,
                        "staff":          next((s for s in staff_list if s["id"] == sid), {"id": sid}),
                        "night_task_id":  cur["task_id"],
                        "next_task_id":   nxt["task_id"],
                        "rest_actual":    rest_actual,
                        "min_rest":       min_rest,
                    })

    # 連続夜勤違反チェック
    # 2026-07-14修正: 以前は config.get("max_consecutive_night_shifts", 2) という
    # グローバル・かつ存在しないキー（実際は staff.work_limits.max_consecutive_night_shifts
    # というスタッフ個別フィールド）を見ており、常にデフォルト値2と比較していた。
    # INRC-II由来のnurse_shift_weekly_cap_inrc_n021w4シナリオ（実際の上限は5）で、
    # CP Optimizerは正しく5を上限として解いているにもかかわらず、この後処理チェックだけが
    # 「2を超えたら違反」と誤検知することが発覚した。スタッフごとの実際の上限を見るよう修正する。
    for sid, atasks in staff_tasks_map.items():
        staff_work_limits = staff_by_id.get(sid, {}).get("work_limits", {})
        max_consecutive = staff_work_limits.get(
            "max_consecutive_night_shifts", DEFAULT_MAX_CONSECUTIVE_NIGHTS
        )

        night_days = sorted({at["day"] for at in atasks if at.get("is_night_shift")})
        if not night_days:
            continue
        # 連続日数をカウント
        consecutive = 1
        for i in range(1, len(night_days)):
            if night_days[i] == night_days[i - 1] + 1:
                consecutive += 1
                if consecutive > max_consecutive:
                    ctxs.append({
                        "_rule_id":           "consecutive_night_violation",
                        "sid":                sid,
                        "staff":              next((s for s in staff_list if s["id"] == sid), {"id": sid}),
                        "consecutive_count":  consecutive,
                        "max_consecutive":    max_consecutive,
                        "night_days":         night_days,
                    })
                    break
            else:
                consecutive = 1

    return ctxs