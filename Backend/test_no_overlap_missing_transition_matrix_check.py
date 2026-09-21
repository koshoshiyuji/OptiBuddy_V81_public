"""
test_no_overlap_missing_transition_matrix_check.py

2026-09-21新規作成（対応C）: 禁止パターン13の静的チェック
(_check_no_overlap_missing_transition_matrix) の単体テスト。

このチェックは「sequence_var + no_overlap が、地点・距離を扱うドメインで
transition_matrix無しの単一引数で呼ばれている」ケースを検出する。
RideshareMatchingPlannerで実際に発生した、地点間の実移動時間が一切
要求されないバグの再発防止が目的。

実行方法:
    cd Backend
    python3 test_no_overlap_missing_transition_matrix_check.py
(pytestが無い環境でも動くように、手動ドライバも用意している)
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

from domain_generator.static_checks import _check_no_overlap_missing_transition_matrix


def test_missing_tm_with_dist_matrix_detected():
    code = """
n_loc = len(locations)
flat_dist = build_flat_dist(dist_matrix)
seq = mdl.sequence_var(all_itvs, types=types_d, name="seq_d0")
mdl.add(mdl.no_overlap(seq))
mdl.add(mdl.first(seq, depot_start))
"""
    warnings = _check_no_overlap_missing_transition_matrix(code, "dummy.py")
    assert len(warnings) == 1, f"dist_matrix + single-arg no_overlap should fire: {warnings}"


def test_missing_tm_with_type_of_next_detected():
    code = """
seq = mdl.sequence_var(all_itvs, types=types_d, name="seq_d0")
mdl.add(mdl.no_overlap(seq))
next_t = mdl.type_of_next(seq, itv, lastValue=end_t, absentValue=cur_t)
"""
    warnings = _check_no_overlap_missing_transition_matrix(code, "dummy.py")
    assert len(warnings) == 1, f"type_of_next signal should fire: {warnings}"


def test_tm_present_not_flagged():
    code = """
n_loc = len(locations)
tm = build_cpo_transition_matrix(travel_matrix)
seq = mdl.sequence_var(all_itvs, types=types_d, name="seq_d0")
mdl.add(mdl.no_overlap(seq, tm))
"""
    warnings = _check_no_overlap_missing_transition_matrix(code, "dummy.py")
    assert warnings == [], f"no_overlap(seq, tm) should not fire: {warnings}"


def test_no_location_concept_not_flagged():
    code = """
seq = mdl.sequence_var(job_itvs, name="seq_machine0")
mdl.add(mdl.no_overlap(seq))
"""
    warnings = _check_no_overlap_missing_transition_matrix(code, "dummy.py")
    assert warnings == [], f"no location/distance concept should not fire: {warnings}"


def test_single_arg_not_a_sequence_var_not_flagged():
    # no_overlap(x) で x が sequence_var 由来の変数だと確認できない場合は対象外
    # （禁止パターン1側の検出対象であり、本チェックの対象ではない）
    code = """
locations = get_locations()
mdl.add(mdl.no_overlap(some_other_var))
"""
    warnings = _check_no_overlap_missing_transition_matrix(code, "dummy.py")
    assert warnings == [], f"non-sequence_var single arg should not fire: {warnings}"


def test_commented_out_mentions_ignored():
    code = """
locations = get_locations()
# 過去はtransition_matrix無しでno_overlap(seq)を呼んでいた
# mdl.add(mdl.no_overlap(seq))
seq = mdl.sequence_var(all_itvs, name="seq_d0")
mdl.add(mdl.no_overlap(seq, tm))
"""
    warnings = _check_no_overlap_missing_transition_matrix(code, "dummy.py")
    assert warnings == [], f"commented-out mentions should be ignored: {warnings}"


if __name__ == "__main__":
    tests = [
        test_missing_tm_with_dist_matrix_detected,
        test_missing_tm_with_type_of_next_detected,
        test_tm_present_not_flagged,
        test_no_location_concept_not_flagged,
        test_single_arg_not_a_sequence_var_not_flagged,
        test_commented_out_mentions_ignored,
    ]
    for t in tests:
        t()
        print(f"PASS: {t.__name__}")
    print("\nALL CHECKS PASSED")
