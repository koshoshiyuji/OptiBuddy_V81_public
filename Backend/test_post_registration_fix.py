"""
test_post_registration_fix.py

2026-07-18f追加: run_post_registration_fix()（登録後に残ったGate2警告を
任意で直す入口）の単体テスト。実LLM（debug_agent.run_debug_agent）は
monkeypatchで差し替える（本サンドボックスにはLLM API keyもcpoptimizerも
無いため）。MeetingRoomの実ファイル（Backend/solvers/meeting_room_solver.py等、
本セッション内で既に登録済み）を対象に、written_pathsの組み立てと
戻り値の形を検証する。
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

import debug_agent
import domain_generator


def test_normal_fix_flow_returns_expected_shape(monkeypatch):
    recorded = {}

    def fake_run_debug_agent(questions, written_paths, domain_name, hearing_texts,
                              snake_name, human_notes="", max_turns=5, **kwargs):
        recorded["questions"] = questions
        recorded["written_paths"] = written_paths
        recorded["hearing_texts"] = hearing_texts
        return {
            "fixed_summary": "ダミー修正完了",
            "needs_human_decision": [],
            "turns_used": 2,
            "stopped_reason": "done",
            "actions": [],
        }

    monkeypatch.setattr(debug_agent, "run_debug_agent", fake_run_debug_agent)

    result = domain_generator.run_post_registration_fix(
        "meeting_room", "MeetingRoom", ["ダミーの残存警告"]
    )

    assert set(result.keys()) >= {"fixed_summary", "gate2_warnings", "turns_used", "stopped_reason"}
    assert result["fixed_summary"] == "ダミー修正完了"
    assert isinstance(result["gate2_warnings"], list)
    assert result["stopped_reason"] == "done"

    # hearing_textsは意図的に空リストで渡す設計（job_gc後に復元できないため）。
    assert recorded["hearing_texts"] == []
    assert recorded["questions"] == ["ダミーの残存警告"]


def test_written_paths_include_ui_converter_when_it_exists(monkeypatch):
    recorded = {}

    def fake_run_debug_agent(questions, written_paths, domain_name, hearing_texts,
                              snake_name, human_notes="", max_turns=5, **kwargs):
        recorded["written_paths"] = written_paths
        return {"fixed_summary": "", "needs_human_decision": [], "turns_used": 1,
                "stopped_reason": "done", "actions": []}

    monkeypatch.setattr(debug_agent, "run_debug_agent", fake_run_debug_agent)
    domain_generator.run_post_registration_fix("meeting_room", "MeetingRoom", [])

    assert "Backend/solvers/meeting_room_solver.py" in recorded["written_paths"]
    assert "Backend/dsl_transformer/meeting_room_converter.py" in recorded["written_paths"]
    # meeting_room_ui_converter.py は本セッションで既に作成済み（実在する）ので含まれるはず。
    assert "Backend/dsl_transformer/meeting_room_ui_converter.py" in recorded["written_paths"]


def test_waiting_for_human_short_circuits_without_reverification(monkeypatch):
    def fake_run_debug_agent(questions, written_paths, domain_name, hearing_texts,
                              snake_name, human_notes="", max_turns=5, **kwargs):
        return {
            "fixed_summary": "",
            "needs_human_decision": [],
            "turns_used": 3,
            "stopped_reason": "waiting_for_human",
            "pending_question": "この場合は0件許容でよいですか？",
            "actions": [],
        }

    monkeypatch.setattr(debug_agent, "run_debug_agent", fake_run_debug_agent)

    result = domain_generator.run_post_registration_fix(
        "meeting_room", "MeetingRoom", ["元の警告A"]
    )

    assert result["stopped_reason"] == "waiting_for_human"
    assert any("この場合は0件許容でよいですか？" in w for w in result["gate2_warnings"])
    # 元の警告も失われず残っていること（未対応のまま）。
    assert "元の警告A" in result["gate2_warnings"]


if __name__ == "__main__":
    import types
    class _FakeMonkeypatch:
        def setattr(self, obj, name, value):
            setattr(obj, name, value)
    mp = _FakeMonkeypatch()
    test_normal_fix_flow_returns_expected_shape(mp)
    test_written_paths_include_ui_converter_when_it_exists(mp)
    test_waiting_for_human_short_circuits_without_reverification(mp)
    print("全テスト成功")
