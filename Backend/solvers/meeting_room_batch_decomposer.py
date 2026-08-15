"""
Backend/solvers/meeting_room_batch_decomposer.py

MeetingRoomSolver専用の層Bアダプタ（CE上限フォールバック、逐次バッチ分割）。
2026-07-27追加。CE上限フォールバックの層B部分が「宣言＋既存関数呼び出し」だけで
済むかの2件目の実証（1件目はnurse_shift_weekly_cap_batch_decomposer.py）。
ただし本ドメインは分割軸・持ち越し処理のどちらも前例とは形が異なるため、
その差分を明記する。

参照: docs/DESIGN_2026-07-26_generic_ce_limit_fallback.md,
      solvers/base/ce_limit_lns.py

【分割軸の選定: rooms軸（meetings軸ではない）】
MeetingRoomのモデルサイズは概ね「meetings数 × 各会議に互換な部屋数」の
optional interval_var総数（assignment_itvs）で決まる。部屋をまたぐ制約は
no_overlap（同一部屋内の重複禁止）だけで、これはNurseShiftWeeklyCap/
BalancedNursingWorkloadにおける「スタッフ1人で完結する制約」と同型
（＝「部屋1つで完結する制約」）。roomsを分割軸にすれば、各部屋のno_overlap
制約は必ず単一バッチ内で完結し、バッチをまたいでも壊れない。

逆にmeetings軸で分割すると、同じ部屋が複数バッチに跨って現れる。各バッチは
CP Optimizerを独立にsolveし他バッチの割当を一切知らないため、バッチ間の
同室ダブルブッキングを防げない。この非対称性から、本ドメインではrooms軸が
明確に安全側であるため採用する（nurse_shift_weekly_cap_batch_decomposer.py
のstaff軸選定と同じ考え方）。

品質面の制約（既存ドメインと同じく feasibility 優先・quality は許容妥協）:
  - dept_same_room_bonus（同部署同室ボーナス）は、両会議の互換部屋が同じ
    room batchに含まれる場合のみ効果を持つ。batchをまたぐ会議ペアでは
    ボーナスが計算されないが、これは目的関数の一部が失われるだけで
    feasibilityには影響しない（HorizonDecomposer等、既存の逐次バッチ方式が
    共通して受け入れている「品質改善の反復は行わない」という制約と同じ扱い）。

【QUOTA_FIELDSを使わない理由】
required_count/min_chiefsのような「残数を持ち越す集計値」がこのドメインには
存在しない（会議は割当済みか未割当かの二値であり、部分充足という概念がない）。
そのため層Aのreduce_quota_fields()は使わず、「既に割り当て済みの会議を
次バッチのmeetingsから単純に除外する」というより単純な持ち越し処理を
本ファイルに直接書く。
"""

from __future__ import annotations

import copy
import logging
from typing import Any, Dict, List, Set

logger = logging.getLogger(__name__)

__all__ = ["PRIMARY_ENTITY_KEY", "build_subset_input", "merge_results"]

PRIMARY_ENTITY_KEY = "rooms"


def build_subset_input(
    solver_input: Dict[str, Any],
    entity_ids: Set[str],
    prior_results: List[Dict[str, Any]],
) -> Dict[str, Any]:
    """
    roomsをentity_idsに絞り込み、これまでのバッチで既に割り当て済みの会議を
    meetingsから除外した縮小Solver Input DSLを作る。

    reduce_quota_fields()（層A）が前提とする「残数（数量）の持ち越し」ではなく、
    「割当済みエンティティそのものを除外する」という、より単純な持ち越し
    （NurseShiftWeeklyCap的な残数計算が不要なドメイン向けの最小形）。
    """
    rooms_list: List[Dict] = solver_input.get("rooms", [])
    meetings: List[Dict] = solver_input.get("meetings", [])

    prior_assigned_meeting_ids: Set[str] = {
        str(a["meeting_id"])
        for result in prior_results
        for sol in result.get("solutions", [])
        for a in sol.get("assignments", [])
    }

    sub_meetings = [m for m in meetings if str(m["id"]) not in prior_assigned_meeting_ids]
    sub_rooms = [r for r in rooms_list if str(r["id"]) in entity_ids]

    sub_input = copy.deepcopy(solver_input)
    sub_input["rooms"] = sub_rooms
    sub_input["meetings"] = sub_meetings
    return sub_input


def merge_results(
    results: List[Dict[str, Any]],
    original_solver_input: Dict[str, Any],
) -> Dict[str, Any]:
    """
    各バッチのSolver Output DSLを1つに統合する。

    issue/kpiは各バッチが自分の担当分（部屋のサブセット・未割当の会議のみ）を
    見て出したものをそのまま集めるのではなく、統合後の全assignments・全rooms・
    全meetingsを使ってMeetingRoomSolver自身の_detect_issues/
    _run_solution_checkerを呼び直して作り直す
    （nurse_shift_weekly_cap_batch_decomposer.pyの「通常経路と同じ関数の
    呼び直し」という方針を踏襲。理由も同じ: 例えばバッチ1では未割当だった
    会議がバッチ2で割り当てられるケースがあり、バッチ単体の判定をそのまま
    残すと最終結果に矛盾が生じる）。

    MeetingRoomSolverの_detect_issues/_run_solution_checkerはインスタンス
    メソッドだが、内部で参照するself.dslはissue_statuses/_gate2_full_check
    のみ（実際のissue判定ロジックはmeetings/rooms/assignments引数から計算）
    のため、元のsolver_inputを持つ一時インスタンスを介して呼び直すだけで、
    本ファイルは新規のissue判定ロジックを一切持たない。
    """
    from solvers.meeting_room_solver import MeetingRoomSolver
    from solvers.base.issue_rules import build_full_unassignment_issue

    original_meetings = original_solver_input.get("meetings", [])
    original_rooms     = original_solver_input.get("rooms", [])

    all_assignments: List[Dict[str, Any]] = []
    for r in results:
        for sol in r.get("solutions", []):
            all_assignments.extend(sol.get("assignments", []))

    assigned_meeting_ids = {a["meeting_id"] for a in all_assignments}
    feasible = len(assigned_meeting_ids) == len(original_meetings)

    tmp_solver = MeetingRoomSolver(original_solver_input)

    issues = tmp_solver._detect_issues(
        original_meetings, original_rooms, all_assignments, assigned_meeting_ids
    )
    anomaly = build_full_unassignment_issue(
        assigned_count=len(assigned_meeting_ids),
        total_count=len(original_meetings),
        entity_label="会議",
        extra_hint="get_var_solution()での解抽出処理（グループ分割経路）",
    )
    if anomaly:
        issues.insert(0, anomaly)

    checker_issues, deferred_check = tmp_solver._run_solution_checker(all_assignments, original_rooms)
    issues.extend(checker_issues)

    # objective_valueはKPI表示用の概算値（waste + 未割当ペナルティのみ）。
    # dept_same_room_bonusはバッチをまたぐペアで計算されないため、通常経路の
    # 目的関数値と完全には一致しない（本ファイル冒頭の品質面の制約の通り、
    # feasibilityのみを保証しquality計算の完全再現は対象外とする）。
    from solvers.meeting_room_solver import UNASSIGNED_PENALTY, WASTE_PENALTY_WEIGHT
    config = original_solver_input.get("config", {})
    waste_penalty_weight = config.get("waste_penalty_weight", WASTE_PENALTY_WEIGHT)
    waste_total = sum(
        max(0, a.get("capacity", 0) - a.get("attendees", 0)) for a in all_assignments
    )
    unassigned_total = len(original_meetings) - len(assigned_meeting_ids)
    objective_value = waste_penalty_weight * waste_total + UNASSIGNED_PENALTY * unassigned_total

    solve_times = [
        sol.get("optimality", {}).get("solve_time_sec")
        for r in results for sol in r.get("solutions", [])
        if sol.get("optimality", {}).get("solve_time_sec") is not None
    ]

    solution = {
        "feasible":    feasible,
        "assignments": all_assignments,
        "kpi": {
            "total_meetings":   len(original_meetings),
            "assigned_count":   len(assigned_meeting_ids),
            "unassigned_count": unassigned_total,
            "total_rooms":      len(original_rooms),
            "objective_value":  round(float(objective_value), 2),
        },
        # 複数バッチに分けて解いているため全体最適性は未証明。既知の
        # solve_status値（extract_optimality_metadata参照）のうち、
        # 実行可能解が得られたかどうかだけをFeasible/Infeasibleで表す。
        "optimality": {
            "solve_status":   "Feasible" if feasible else "Infeasible",
            "is_optimal":     False,
            "solve_time_sec": sum(solve_times) if solve_times else None,
        },
    }

    logger.info(
        f"[MeetingRoomBatchDecomposer.merge] batches={len(results)}, "
        f"assigned={len(assigned_meeting_ids)}/{len(original_meetings)}"
    )

    result = {
        "status":   "ok",
        "feasible": feasible,
        "metadata": tmp_solver._build_metadata(),
        "solutions": [solution],
        "issues":    issues,
        "_solver_version": "meeting_room_v1.0",
        "_decompose_meta": {"type": "sequential_room_batch", "batch_count": len(results)},
    }
    if deferred_check is not None:
        result["_deferred_checks"] = [deferred_check]
    return result
