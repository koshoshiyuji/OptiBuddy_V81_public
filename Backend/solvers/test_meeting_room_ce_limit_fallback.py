"""
Backend/solvers/test_meeting_room_ce_limit_fallback.py

2026-07-27追加。MeetingRoomへのCE上限フォールバック層B（新規:
meeting_room_batch_decomposer.py）とsolve()側の配線を検証するテスト。

このサンドボックスにはdocplex/CP Optimizerの実行エンジンが無いため
（test_nurse_shift_weekly_cap_ce_limit_manual.pyと同じ制約）、実際の
CPLEX無料版の上限に当ててend-to-endで確認することはできない。そのため
本テストは2段構成にした。

  1. build_subset_input/merge_results単体のデータ変換ロジック検証
     （docplex不要、rooms軸で分割・除外する処理が正しいかのみを見る）。
  2. MeetingRoomSolver._build_and_solve() をモックに差し替えた
     wiring（配線）テスト（solve()がCeLimitExceededErrorを捕捉して
     run_solve_with_ce_limit_fallback()を正しく呼び出し、層Aの
     retry-depth付き再帰分割が実際に機能し、最終的に全会議が割り当て
     られること、を確認する）。CP Optimizerの実モデル構築・実ソルブは
     一切検証しない（それは実機のCPLEX環境で
     test_nurse_shift_weekly_cap_ce_limit_manual.py同様の手動スクリプトを
     別途用意して確認すること）。
"""

import sys
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).parent.parent))

from solvers.base.ce_limit_lns import CeLimitExceededError
from solvers.meeting_room_solver import MeetingRoomSolver
from solvers.meeting_room_batch_decomposer import (
    PRIMARY_ENTITY_KEY, build_subset_input, merge_results,
)


def _room(rid, capacity=4):
    return {"id": rid, "name": f"部屋{rid}", "capacity": capacity, "features": [],
            "available_start_min": 0, "available_end_min": 1440}


def _meeting(mid, start=0, end=60, attendees=2, dept=None):
    return {"id": mid, "name": f"会議{mid}", "dept": dept,
            "start_min": start, "end_min": end, "attendees": attendees,
            "required_features": []}


# -----------------------------------------------------------------------------
# 1. build_subset_input / merge_results 単体テスト（docplex不要）
# -----------------------------------------------------------------------------

def test_primary_entity_key_is_rooms():
    # 分割軸がmeetingsではなくroomsであること（no_overlapが部屋単位で
    # 完結する制約のため、meetings軸だと部屋のダブルブッキングを防げない）
    assert PRIMARY_ENTITY_KEY == "rooms"


def test_build_subset_input_filters_rooms_and_excludes_assigned_meetings():
    solver_input = {
        "meetings": [_meeting("m1"), _meeting("m2"), _meeting("m3")],
        "rooms": [_room("r1"), _room("r2"), _room("r3")],
        "config": {},
    }
    prior_results = [{
        "solutions": [{"assignments": [
            {"meeting_id": "m1", "room_id": "r1"},
        ]}]
    }]

    sub_input = build_subset_input(solver_input, entity_ids={"r2", "r3"}, prior_results=prior_results)

    assert {r["id"] for r in sub_input["rooms"]} == {"r2", "r3"}
    # m1はprior_resultsで既に割り当て済みのため除外され、m2/m3のみ残る
    assert {m["id"] for m in sub_input["meetings"]} == {"m2", "m3"}


def test_merge_results_combines_batches_and_computes_feasibility():
    original_solver_input = {
        "meetings": [_meeting("m1"), _meeting("m2")],
        "rooms": [_room("r1", capacity=4), _room("r2", capacity=3)],
        "config": {},
        "issue_statuses": {},
    }
    results = [
        {"solutions": [{"assignments": [
            {"meeting_id": "m1", "room_id": "r1", "attendees": 2, "capacity": 4,
             "meeting_name": "会議m1", "room_name": "部屋r1", "start_min": 0, "end_min": 60,
             "dept": None, "features": []},
        ], "optimality": {"solve_time_sec": 0.1}}]},
        {"solutions": [{"assignments": [
            {"meeting_id": "m2", "room_id": "r2", "attendees": 3, "capacity": 3,
             "meeting_name": "会議m2", "room_name": "部屋r2", "start_min": 0, "end_min": 60,
             "dept": None, "features": []},
        ], "optimality": {"solve_time_sec": 0.2}}]},
    ]

    merged = merge_results(results, original_solver_input)

    assert merged["feasible"] is True
    sol = merged["solutions"][0]
    assert sol["kpi"]["total_meetings"] == 2
    assert sol["kpi"]["assigned_count"] == 2
    assert sol["kpi"]["unassigned_count"] == 0
    assert {a["meeting_id"] for a in sol["assignments"]} == {"m1", "m2"}
    assert merged["_decompose_meta"]["batch_count"] == 2
    # m2はattendees==capacity（ギリギリ）のため、_detect_issuesのWARNINGが出るはず
    tight_issues = [i for i in merged["issues"] if i["id"].startswith("tight_room_")]
    assert len(tight_issues) == 1


def test_merge_results_infeasible_when_some_meetings_never_assigned():
    original_solver_input = {
        "meetings": [_meeting("m1"), _meeting("m2")],
        "rooms": [_room("r1")],
        "config": {},
        "issue_statuses": {},
    }
    # m2はどのバッチでも割り当てられなかった想定
    results = [
        {"solutions": [{"assignments": [
            {"meeting_id": "m1", "room_id": "r1", "attendees": 2, "capacity": 4,
             "meeting_name": "会議m1", "room_name": "部屋r1", "start_min": 0, "end_min": 60,
             "dept": None, "features": []},
        ], "optimality": {"solve_time_sec": 0.1}}]},
    ]

    merged = merge_results(results, original_solver_input)

    assert merged["feasible"] is False
    assert merged["solutions"][0]["kpi"]["unassigned_count"] == 1
    assert merged["solutions"][0]["optimality"]["solve_status"] == "Infeasible"


# -----------------------------------------------------------------------------
# 2. solve()側の配線テスト（_build_and_solveをモック化、docplex不要）
# -----------------------------------------------------------------------------

def _fake_build_and_solve(self, meetings, rooms, config):
    """
    実際のCP Optimizerモデル構築の代わりに、rooms件数が3件を超えたら
    CeLimitExceededErrorを投げる（本物のCPLEX無料版上限検知のスタブ）。
    3件以下なら「全会議を先頭の部屋に割り当てる」という単純化した
    成功結果を返す（配線の検証が目的で、割当の最適性・実行可能性の
    正しさそのものは検証対象外）。
    """
    if len(rooms) > 3:
        raise CeLimitExceededError(f"Problem size limit exceeded (fake test, rooms={len(rooms)})")

    assignments = []
    for m in meetings:
        r = rooms[0]
        assignments.append({
            "meeting_id": m["id"], "meeting_name": m.get("name", m["id"]),
            "room_id": r["id"], "room_name": r.get("name", r["id"]),
            "start_min": m["start_min"], "end_min": m["end_min"],
            "attendees": m.get("attendees", 1), "capacity": r.get("capacity", 0),
            "dept": m.get("dept"), "features": m.get("required_features", []),
        })
    assigned_meeting_ids = {a["meeting_id"] for a in assignments}
    issues = self._detect_issues(meetings, rooms, assignments, assigned_meeting_ids)

    return {
        "status": "ok",
        "feasible": len(assigned_meeting_ids) == len(meetings),
        "metadata": self._build_metadata(),
        "solutions": [{
            "feasible": True,
            "assignments": assignments,
            "kpi": {"total_meetings": len(meetings), "assigned_count": len(assigned_meeting_ids),
                    "unassigned_count": 0, "total_rooms": len(rooms), "objective_value": 0.0},
            "optimality": {"solve_status": "Optimal", "is_optimal": True, "solve_time_sec": 0.01},
        }],
        "issues": issues,
        "_solver_version": "meeting_room_v1.0(fake_test)",
    }


def test_solve_falls_back_to_room_batching_when_ce_limit_exceeded():
    # rooms=6件（>3件のフェイク上限）で、少なくとも1段はバッチ分割が
    # 発動しないと解けない状況を作る。層Aの再試行ロジックにより、
    # 5件→(2,2,1)のように必要な回数だけ細分化されて最終的に全会議が
    # 割り当てられることを確認する。
    scenario = {
        "meetings": [_meeting(f"m{i}") for i in range(4)],
        "rooms":    [_room(f"r{i}") for i in range(6)],
        "config":   {},
        "issue_statuses": {},
    }

    with patch.object(MeetingRoomSolver, "_build_and_solve", _fake_build_and_solve):
        result = MeetingRoomSolver(scenario).solve()

    assert result["feasible"] is True
    assert "_decompose_meta" in result
    assert result["_decompose_meta"]["batch_count"] >= 2

    sol = result["solutions"][0]
    assert {a["meeting_id"] for a in sol["assignments"]} == {"m0", "m1", "m2", "m3"}
    assert sol["kpi"]["unassigned_count"] == 0


def test_solve_no_fallback_needed_when_under_limit():
    # rooms=2件（フェイク上限3件以下）なので、CeLimitExceededErrorは
    # 発生せず、通常の（分割なしの）solve()経路がそのまま使われる。
    scenario = {
        "meetings": [_meeting("m1")],
        "rooms":    [_room("r1"), _room("r2")],
        "config":   {},
        "issue_statuses": {},
    }

    with patch.object(MeetingRoomSolver, "_build_and_solve", _fake_build_and_solve):
        result = MeetingRoomSolver(scenario).solve()

    assert result["feasible"] is True
    assert "_decompose_meta" not in result


if __name__ == "__main__":
    import pytest
    raise SystemExit(pytest.main([__file__, "-v"]))
