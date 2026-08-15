"""
Backend/solvers/nurse_shift_weekly_cap_batch_decomposer.py

NurseShiftWeeklyCap専用の層Bアダプタ（CE上限フォールバック、逐次バッチ分割）。

【背景】
NurseShiftWeeklyCapSolverがCPLEX Community Edition（評価版）のモデルサイズ上限
（"Problem size limit exceeded"）を検知した場合に、solvers/base/ce_limit_lns.py の
`run_sequential_batch()`（層A: 共通ループ）へ渡す3点（PRIMARY_ENTITY_KEY /
build_subset_input / merge_results）を提供する。
参照: docs/DESIGN_2026-07-26_generic_ce_limit_fallback.md

【分割軸の選定（staff軸、2026-07-26 Koshoshiとの相談で確定）】
NurseShiftWeeklyCapの制約は「スタッフ1人で完結する制約」と「タスク1つで完結する
制約」に分けられる。

  - スタッフ単位で完結（5種）: no_overlap・夜勤後休憩・連続夜勤日数上限・
    日次労働時間上限・週次夜勤ローリング上限
  - タスク単位で完結（2種）: required_count・min_chiefs

staff軸で分割すれば、1人のスタッフの全候補は必ず同じバッチに収まるため、
上記5種類のスタッフ制約はバッチをまたいでも正確に保たれる（壊れない）。
壊れるのはrequired_count/min_chiefsの2種類だけで、これは残数を次バッチへ
持ち越す方式（下記QUOTA_FIELDS宣言）で対応する。

逆にtask軸で分割すると、1人のスタッフが複数バッチに跨って候補を持つことになり、
上記5種類のスタッフ制約すべてがバッチをまたぐと正確に見えなくなる
（例: 同じ看護師が別バッチで二重予約される、休憩時間が計算されない等）。
5種対2種の非対称性から、本ドメインではstaff軸が明確に安全側であるため採用する。

【2026-07-26 追記: 宣言型への一般化】
本ファイルは当初、以下2点をNurseShiftWeeklyCap専用の独自Pythonロジックとして
直接書いていた。
  (a) 残数計算（required_count/min_chiefsの持ち越し）
  (b) 統合後のissue/task_summary/metrics再計算

実際に中身を精査したところ、(a)は「どのフィールドを・どのキーで・どういう条件で
集計するか」という**宣言**さえあればループ自体は汎用化できることが分かり、
solvers/base/ce_limit_lns.pyの`reduce_quota_fields()`（層A共通ヘルパー）に
切り出した。本ファイル側はQUOTA_FIELDS宣言だけを持つ。

(b)についても、実際に書いていたのは「新しいロジック」ではなく、
NurseShiftWeeklyCapSolver自身が通常経路（分割されない一括ソルブ時）で既に
使っている`build_candidates`/`build_task_summary`/`build_metrics`/
`_build_hospital_contexts`/`run_issue_rules`の**呼び直し**でしかなかった
（統合後の全件データに対して、通常経路と同じ関数をもう一度呼んでいるだけ）。
そこで通常経路側もこれらを共有関数として呼ぶよう整理し（solver.py参照）、
本ファイルは新規ロジックを一切持たず、それらの呼び出しの組み合わせ
（オーケストレーション）のみを行う形にした。

この結果、本ファイルが持つ「NurseShiftWeeklyCap固有」の情報は実質的に
PRIMARY_ENTITY_KEY（"staff"）とQUOTA_FIELDS（2フィールドの宣言）、および
最終的な解の見た目（label文言）だけになった。新しいドメインでCE上限対応が
必要になった場合、必要なのは（1）そのドメインが元々`build_candidates`相当の
関数群を持っていること（Layer A/B規約により通常はある）と、（2）この宣言部分
だけを書くこと、の2点で足りる見込みが立った。

【NurseShiftWeeklyCapの実データ形状に関する注意】
`solvers/decomposer.py`の`StaffDecomposer`は`constraints[]`という型付きリスト
（Solver Input DSL上に`{"type": "required_count", ...}`が並ぶ形式）を前提にしており、
NurseShiftWeeklyCapのSolver Input DSL（`staff`/`tasks`のみ、制約はsolver.py内の
ロジックとして直接組み込まれている）とはスキーマが異なるため、そのまま流用できない。
本ファイルはNurseShiftWeeklyCap専用に新規実装する（2026-07-26、Koshoshiとの相談で
StaffDecomposer自体には手を入れず独立実装とすることで合意）。
"""

from __future__ import annotations

import copy
import logging
from typing import Any, Dict, List, Set

from solvers.base.ce_limit_lns import reduce_quota_fields
from i18n.nurse_shift_weekly_cap_messages import t

logger = logging.getLogger(__name__)

__all__ = ["PRIMARY_ENTITY_KEY", "QUOTA_FIELDS", "build_subset_input", "merge_results"]

PRIMARY_ENTITY_KEY = "staff"

# タスク側の「残数持ち越し」対象フィールド宣言（層Aのreduce_quota_fields()へ渡す）。
# - required_count: 無条件（このタスクへの割り当て1件につき1消費）。
# - min_chiefs:      grade=="CHIEF"の割り当てのみ消費対象。
QUOTA_FIELDS = [
    {"field": "required_count"},
    {"field": "min_chiefs", "filter": lambda assigned: assigned.get("grade") == "CHIEF"},
]


def build_subset_input(
    solver_input: Dict[str, Any],
    entity_ids: Set[str],
    prior_results: List[Dict[str, Any]],
) -> Dict[str, Any]:
    """
    staffをentity_idsに絞り込み、tasksのrequired_count/min_chiefsを
    これまでのバッチ（prior_results）で割り当て済みの分を差し引いた残数に
    置き換えた縮小Solver Input DSLを作る。

    残数計算そのものは層Aの`reduce_quota_fields()`に委譲する（本関数は
    「何を・どういう条件で集計するか」の宣言＝QUOTA_FIELDSと、それをこの
    ドメインのDSL形状（staff/tasksのみ、assignedはtask_id/gradeを持つ）に
    合わせて呼び出す接続部分のみを持つ）。
    """
    staff_list: List[Dict] = solver_input.get("staff", [])
    tasks: List[Dict] = solver_input.get("tasks", [])

    prior_assigned_tasks: List[Dict] = [
        at
        for result in prior_results
        for sol in result.get("solutions", [])
        for at in sol.get("tasks", [])
    ]

    sub_tasks = reduce_quota_fields(
        entities=tasks,
        entity_key_field="id",
        assigned_items=prior_assigned_tasks,
        assigned_group_field="task_id",
        quota_fields=QUOTA_FIELDS,
    )
    sub_staff = [s for s in staff_list if s["id"] in entity_ids]

    sub_input = copy.deepcopy(solver_input)
    sub_input["staff"] = sub_staff
    sub_input["tasks"] = sub_tasks
    return sub_input


def merge_results(
    results: List[Dict[str, Any]],
    original_solver_input: Dict[str, Any],
) -> Dict[str, Any]:
    """
    各バッチのSolver Output DSLを1つに統合する。

    2026-07-26修正（実機テストで見つかった不具合の修正、宣言型への一般化後も
    据え置き）: issue（人員不足・CHIEF不足などの警告）は、各バッチが自分の
    担当分だけを見て出したものをそのまま集めるのはやめ、統合後の全スタッフ・
    全タスク・本来の（グループ分割前の）必要人数を使って最初から作り直す。

    理由: 例えば必要人数3名のタスクに対し、1つ目のグループでは1名しか
    割り当てられなかったが、2つ目のグループで残り2名が割り当てられ、最終的には
    3名全員が揃うケースがある。この時、1つ目のグループ単体の判定では「不足」の
    警告が出るのが正しいが、その警告を最終結果にそのまま残すと、実際には
    充足しているのに「人員不足」という誤った警告が出てしまう（Overview画面の
    集計＝充足率100%とIssues画面の警告＝人員不足、が矛盾する不具合として
    実機で発生した）。

    task_summary/metricsも同様に統合後の全assigned_tasksから作り直すが、
    ここで使う`build_candidates`/`build_task_summary`/`build_metrics`/
    `_build_hospital_contexts`は、いずれもNurseShiftWeeklyCapSolverの通常経路
    （分割されない一括ソルブ時）が既に使っている関数そのものを呼び直している
    だけであり、本ファイルはそれらを再実装していない（新規ロジックはQUOTA_FIELDS
    宣言以外に持たない、という本ファイル冒頭の方針の通り）。
    """
    from solvers.base.issue_rules import run_issue_rules, build_full_unassignment_issue
    from solvers.nurse_shift_weekly_cap_solver import (
        build_candidates, build_task_summary, build_metrics, _build_hospital_contexts,
    )

    any_feasible = any(r.get("feasible") for r in results)
    if not any_feasible:
        merged_issues: List[Dict] = []
        for r in results:
            merged_issues.extend(r.get("issues", []))
        return {
            "status": "ok",
            "feasible": False,
            "metadata": {"problem_class": "NurseShiftWeeklyCap"},
            "solutions": [],
            "issues": merged_issues,
            "_solver_version": "nurse_shift_weekly_cap_v1.0",
            "_decompose_meta": {"type": "sequential_staff_batch", "batch_count": len(results)},
        }

    all_assigned_tasks: List[Dict] = []
    for r in results:
        for sol in r.get("solutions", []):
            all_assigned_tasks.extend(sol.get("tasks", []))

    original_staff_full = original_solver_input.get("staff", [])
    original_tasks_full = original_solver_input.get("tasks", [])
    original_config      = original_solver_input.get("config", {})

    # --- issueの作り直し（各バッチが出したissuesは使わない） ---
    full_candidates = build_candidates(original_staff_full, original_tasks_full, original_config)

    all_issues: List[Dict] = []
    anomaly = build_full_unassignment_issue(
        assigned_count=len(all_assigned_tasks),
        total_count=len(full_candidates),
        entity_label=t("solver.assignmentCandidateLabel"),
        extra_hint="get_var_solution() での解抽出処理",
    )
    if anomaly:
        all_issues.append(anomaly)

    contexts = _build_hospital_contexts(
        solution_data={"tasks": all_assigned_tasks},
        staff_list=original_staff_full,
        tasks=original_tasks_full,
        candidates=full_candidates,
        config=original_config,
    )
    all_issues.extend(run_issue_rules(
        domain="NurseShiftWeeklyCap",
        contexts=contexts,
        issue_statuses=original_solver_input.get("issue_statuses", {}),
    ))

    # --- task_summary/metricsの作り直し（通常経路と同じ共有関数を呼ぶ） ---
    task_summary = build_task_summary(original_tasks_full, all_assigned_tasks)

    solve_time = sum(
        sol.get("metrics", {}).get("solve_time", 0.0)
        for r in results for sol in r.get("solutions", [])
    )
    metrics = build_metrics(all_assigned_tasks, task_summary, original_staff_full, solve_time=solve_time)

    # staff_rolling_night_counts: staff軸で分割しているため、1人のスタッフの
    # 夜勤回数は必ず単一バッチ内で完結して正しく計算済み。バッチをまたいで
    # 再計算する必要はなく、辞書としてそのまま合成すればよい。
    staff_rolling_night_counts: Dict[str, int] = {}
    for r in results:
        for sol in r.get("solutions", []):
            staff_rolling_night_counts.update(sol.get("staff_rolling_night_counts", {}))

    logger.info(
        f"[NurseShiftWeeklyCapBatchDecomposer.merge] batches={len(results)}, "
        f"assigned={len(all_assigned_tasks)}, understaffed={metrics['understaffed_count']}, "
        f"coverage={metrics['coverage_rate']:.1%}"
    )

    merged_solution = {
        "name":    "Plan A",
        "label":   t("solver.planLabelBatch"),
        "feasible": True,
        "tasks":    all_assigned_tasks,
        "task_summary": list(task_summary.values()),
        "metrics": metrics,
        "staff_rolling_night_counts": staff_rolling_night_counts,
    }

    return {
        "status":    "ok",
        "feasible":  True,
        "metadata":  {"problem_class": "NurseShiftWeeklyCap"},
        "solutions": [merged_solution],
        "issues":    all_issues,
        "_solver_version": "nurse_shift_weekly_cap_v1.0",
        "_decompose_meta": {"type": "sequential_staff_batch", "batch_count": len(results)},
    }
