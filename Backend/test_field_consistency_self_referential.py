"""
test_field_consistency_self_referential.py

2026-07-18追加: check_converter_solver_field_consistency() の
「missing_in_converter誤検知」の恒久対応（self-referential抑制）に対する回帰テスト。

背景: MeetingRoom登録で missing_in_converter=[assigned, meeting_id, meeting_name] が
出たが、これはconverterがsolver入力として渡すべきキーではなく、solver自身が
assignments.append({...})で組み立てた出力用dictを、後続処理で
a.get("meeting_id") のように自己参照で読み返しているだけだった（真の入力欠落ではない）。

対応: solver.py自身がそのキーをdictリテラルとして構築していれば
（=solver_own_keys）、missing_in_converter候補から除外し、
suppressed_self_referential として監査用に別枠へ残す。

このテストは (1) 実際のMeetingRoomファイルで誤検知が消えること、
(2) 合成した「真の入力欠落バグ」ケースでは今まで通り検出されること、
の両方を確認する（抑制ロジックが過剰に効いて本物のバグまで隠さないことの保証）。
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

from domain_generator import check_converter_solver_field_consistency


def test_meeting_room_missing_in_converter_false_positive_is_suppressed():
    backend = Path(__file__).parent
    converter_code = (backend / "dsl_transformer" / "meeting_room_converter.py").read_text(encoding="utf-8")
    solver_code = (backend / "solvers" / "meeting_room_solver.py").read_text(encoding="utf-8")

    result = check_converter_solver_field_consistency(converter_code, solver_code)

    assert result["missing_in_converter"] == [], (
        f"誤検知が抑制されず残っている: {result['missing_in_converter']}"
    )
    # 2026-07-19: 「assigned」キーはsolver.pyのassignments.append辞書の
    # 構造が変わり現在は存在しない（実装ドリフト）。厳密に全キーを
    # ハードコードすると今後のsolver側フィールド追加のたびに再度壊れるため、
    # 2026-07-18のバグの本質だった meeting_id/meeting_name の2キーのみを
    # 恒久的な回帰対象として薄く検証する（過剰検知が起きていないことは
    # missing_in_converter == [] の確認で担保済み）。
    for key in ("meeting_id", "meeting_name"):
        assert key in result["suppressed_self_referential"], (
            f"{key} が監査用のsuppressed_self_referentialに記録されていない"
        )


def test_genuine_missing_converter_field_is_still_detected():
    """
    solverが真に外部入力として必要とするキー（solver内では一度もdictリテラルとして
    自分で構築していないキー）は、converterが出力し忘れていれば従来通り
    missing_in_converterとして検出されなければならない（抑制ロジックの過剰動作を防ぐ）。
    """
    converter_code = """
def convert(dsl):
    return {"rooms": [{"id": "R1", "name": "A"}]}
"""
    solver_code = """
def solve(dsl_input):
    for r in dsl_input["rooms"]:
        cap = r.get("capacity", 0)
        print(cap)
"""
    result = check_converter_solver_field_consistency(converter_code, solver_code)
    assert result["missing_in_converter"] == ["capacity"]
    assert result["suppressed_self_referential"] == []


def test_self_referential_suppression_does_not_touch_unused_in_solver():
    """
    suppressed_self_referentialの導入がunused_in_solver側の判定に影響しないことの確認
    （missing_in_converter側だけの変更であるべき）。
    """
    converter_code = """
def convert(dsl):
    return {"rooms": [{"id": "R1", "capacity": 10, "extra_unused_field": 1}]}
"""
    solver_code = """
def solve(dsl_input):
    for r in dsl_input["rooms"]:
        cap = r.get("capacity", 0)
        print(cap)
"""
    result = check_converter_solver_field_consistency(converter_code, solver_code)
    assert result["unused_in_solver"] == ["extra_unused_field"]


if __name__ == "__main__":
    test_meeting_room_missing_in_converter_false_positive_is_suppressed()
    test_genuine_missing_converter_field_is_still_detected()
    test_self_referential_suppression_does_not_touch_unused_in_solver()
    print("全テスト成功")
