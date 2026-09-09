
import pytest
from solvers.base.issue_rules import (
    build_meeting_room_field_contexts,
    build_meeting_room_overlap_contexts,
    run_issue_rules,
)




# ---------------------------------------------------------------------------
# テスト: MeetingRoom 解チェッカー（2026-07-24新規）
# ---------------------------------------------------------------------------

class TestMeetingRoomChecker:
    def _assignment(self, mid, room_id, room_name, start, end, attendees=4, features=None):
        return {
            "meeting_id": mid, "meeting_name": f"会議{mid}", "room_id": room_id,
            "room_name": room_name, "start_min": start, "end_min": end,
            "attendees": attendees, "features": features or [],
        }

    def test_capacity_violation_fires(self):
        a = self._assignment("M1", "R1", "会議室1", 0, 60, attendees=10)
        rooms = [{"id": "R1", "capacity": 5, "features": [], "available_start_min": 0, "available_end_min": 1440}]
        ctxs = build_meeting_room_field_contexts([a], rooms)
        issues = run_issue_rules("MeetingRoom", ctxs, {})
        cap_issues = [i for i in issues if i["id"].startswith("assignment_capacity_violation")]
        assert len(cap_issues) == 1
        assert cap_issues[0]["category"] == "SOLVER"

    def test_feature_violation_fires(self):
        a = self._assignment("M1", "R1", "会議室1", 0, 60, features=["projector"])
        rooms = [{"id": "R1", "capacity": 10, "features": [], "available_start_min": 0, "available_end_min": 1440}]
        ctxs = build_meeting_room_field_contexts([a], rooms)
        issues = run_issue_rules("MeetingRoom", ctxs, {})
        assert any(i["id"].startswith("assignment_feature_violation") for i in issues)

    def test_window_violation_fires(self):
        a = self._assignment("M1", "R1", "会議室1", 0, 60)
        rooms = [{"id": "R1", "capacity": 10, "features": [], "available_start_min": 30, "available_end_min": 1440}]
        ctxs = build_meeting_room_field_contexts([a], rooms)
        issues = run_issue_rules("MeetingRoom", ctxs, {})
        assert any(i["id"].startswith("assignment_window_violation") for i in issues)

    def test_no_violation_when_all_ok(self):
        a = self._assignment("M1", "R1", "会議室1", 100, 160, attendees=4, features=["projector"])
        rooms = [{"id": "R1", "capacity": 10, "features": ["projector"],
                  "available_start_min": 0, "available_end_min": 1440}]
        ctxs = build_meeting_room_field_contexts([a], rooms)
        issues = run_issue_rules("MeetingRoom", ctxs, {})
        assert issues == []

    def test_room_double_booking_fires_on_overlap(self):
        a = self._assignment("M1", "R1", "会議室1", 0, 100)
        b = self._assignment("M2", "R1", "会議室1", 50, 150)
        ctxs = build_meeting_room_overlap_contexts([a, b])
        issues = run_issue_rules("MeetingRoom", ctxs, {})
        assert len(issues) == 1
        assert issues[0]["category"] == "SOLVER"

    def test_room_double_booking_no_fire_when_disjoint(self):
        a = self._assignment("M1", "R1", "会議室1", 0, 100)
        b = self._assignment("M2", "R1", "会議室1", 100, 200)
        ctxs = build_meeting_room_overlap_contexts([a, b])
        issues = run_issue_rules("MeetingRoom", ctxs, {})
        assert issues == []

    def test_room_double_booking_no_fire_different_rooms(self):
        a = self._assignment("M1", "R1", "会議室1", 0, 100)
        b = self._assignment("M2", "R2", "会議室2", 0, 100)
        ctxs = build_meeting_room_overlap_contexts([a, b])
        issues = run_issue_rules("MeetingRoom", ctxs, {})
        assert issues == []
