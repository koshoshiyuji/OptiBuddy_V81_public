"""
test_meeting_room_solver_metadata.py

2026-07-18g追加: MeetingRoomをdeleteして再登録した際、以前の登録セッションで
debug_agentがライブファイルへ直接パッチしていた「metadata.instance_name/note
パススルー」が、Stage2コード生成のやり直しで再び失われていたことが判明
（登録後「今すぐ直す」実行時のGate2警告で再検知）。ライブファイルへの
その場パッチはヒアリングシートに明文化されていない限りコード生成をやり直すと
再発しうる、という教訓の再発防止テスト。
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from solvers.meeting_room_solver import MeetingRoomSolver


def _minimal_dsl(meta=None):
    # meetings/roomsを意図的に空にし、CP Optimizer(cpoptimizer)を起動する
    # _build_and_solve()を通らないバリデーション早期リターン経路
    # （meeting_room_solver.py: `if not meetings: return self._make_result(...)`)
    # を使う。本テストの目的はmetadataパススルーの検証のみで、実ソルブ結果は
    # 対象外（本サンドボックスにはcpoptimizerが無いため）。
    return {
        "meta": meta or {},
        "meetings": [],
        "rooms": [{"id": "R01", "name": "会議室1", "capacity": 4, "equipment": [],
                   "available_start_min": 0, "available_end_min": 600}],
        "config": {"unassigned_penalty": 1000, "capacity_waste_weight": 1.0,
                   "same_dept_bonus_weight": 0.1, "time_limit_sec": 5},
        "issue_statuses": {},
    }


def test_metadata_passes_through_instance_name_and_note():
    dsl = _minimal_dsl(meta={"instance_name": "本社ビル会議室", "note": "テスト備考"})
    result = MeetingRoomSolver(dsl).solve()
    metadata = result["metadata"]
    assert metadata["instance_name"] == "本社ビル会議室"
    assert metadata["note"] == "テスト備考"
    assert metadata["problem_class"] == "MeetingRoom"


def test_metadata_defaults_to_empty_string_when_meta_absent():
    dsl = _minimal_dsl(meta={})
    result = MeetingRoomSolver(dsl).solve()
    metadata = result["metadata"]
    assert metadata["instance_name"] == ""
    assert metadata["note"] == ""


def test_optimality_field_present_on_early_return_path():
    """
    2026-07-22追加: solve_status/is_optimal/solve_time_secメタデータ
    （solvers/base/solution_extraction.py の extract_optimality_metadata()）が
    _make_result() 経由の早期return（本サンドボックスにcpoptimizerが無いため
    使わざるを得ない meetings=[] 経路）でも solutions[0]["optimality"] に
    存在し、実ソルブしていない場合の既定値（is_optimal=False等）になることを
    確認する。実ソルブ時（Optimal/Feasible等の実際の値）は実機での確認が必要
    （このサンドボックスにはcpoptimizer実行バイナリが無い）。
    """
    dsl = _minimal_dsl(meta={})
    result = MeetingRoomSolver(dsl).solve()
    optimality = result["solutions"][0]["optimality"]
    assert set(optimality.keys()) == {"solve_status", "is_optimal", "solve_time_sec"}
    assert optimality["is_optimal"] is False
    assert optimality["solve_status"] is None  # ソルバー未実行のため


if __name__ == "__main__":
    test_metadata_passes_through_instance_name_and_note()
    test_metadata_defaults_to_empty_string_when_meta_absent()
    test_optimality_field_present_on_early_return_path()
    print("全テスト成功")
