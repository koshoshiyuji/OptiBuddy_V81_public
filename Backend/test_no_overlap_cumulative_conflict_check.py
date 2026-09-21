"""
test_no_overlap_cumulative_conflict_check.py

2026-09-17再作成: 禁止パターン7の静的チェック
(_check_no_overlap_cumulative_conflict) の単体テスト。

このチェックは「同一ファイル内でno_overlap系(完全排他)と
pulse/cumulative系(上限付き同時使用)の両方が使われている」ケースを
検出する。PatientTransportPlannerで実際に発生した、no_overlapが
cumulativeの容量制約を常に無効化してしまうバグの再発防止が目的。

実行方法:
    cd Backend
    python3 test_no_overlap_cumulative_conflict_check.py
(pytestが無い環境でも動くように、手動ドライバも用意している)
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

from domain_generator.static_checks import _check_no_overlap_cumulative_conflict


def test_cpo_co_occurrence_detected():
    code = """
seq = mdl.sequence_var(vehicle_itvs)
mdl.add(mdl.no_overlap(seq, transition_matrix))
mdl.add(mdl.sum(mdl.pulse(itv, load) for itv in vehicle_itvs) <= capacity)
"""
    warnings = _check_no_overlap_cumulative_conflict(code, "dummy.py")
    assert len(warnings) == 1, f"CPO co-occurrence should fire: {warnings}"


def test_cpsat_co_occurrence_detected():
    code = """
model.Add(cp.NoOverlapOptional(intervals, presences))
model.Add(cp.Cumulative(intervals, demands, capacity))
"""
    warnings = _check_no_overlap_cumulative_conflict(code, "dummy.py")
    assert len(warnings) == 1, f"CP-SAT co-occurrence should fire: {warnings}"


def test_no_overlap_only_not_flagged():
    code = """
seq = mdl.sequence_var(vehicle_itvs)
mdl.add(mdl.no_overlap(seq, transition_matrix))
"""
    warnings = _check_no_overlap_cumulative_conflict(code, "dummy.py")
    assert warnings == [], f"no_overlap-only should not fire: {warnings}"


def test_cumulative_only_not_flagged():
    code = """
mdl.add(mdl.sum(mdl.pulse(itv, load) for itv in vehicle_itvs) <= capacity)
"""
    warnings = _check_no_overlap_cumulative_conflict(code, "dummy.py")
    assert warnings == [], f"cumulative-only should not fire: {warnings}"


def test_base_constraint_applier_usage_exempted():
    code = """
from solvers.base.constraint_applier import BaseConstraintApplier
applier = BaseConstraintApplier(mode="b")
seq = mdl.sequence_var(vehicle_itvs)
mdl.add(mdl.no_overlap(seq, transition_matrix))
mdl.add(mdl.sum(mdl.pulse(itv, load) for itv in vehicle_itvs) <= capacity)
"""
    warnings = _check_no_overlap_cumulative_conflict(code, "dummy.py")
    assert warnings == [], f"BaseConstraintApplier usage should be exempted: {warnings}"


def test_exempt_marker_suppresses_false_positive():
    code = """
# no_overlap_cumulative_conflict: exempt（目視確認済み、別資源）
seq = mdl.sequence_var(pickup_and_dropoff_itvs)
mdl.add(mdl.no_overlap(seq, transition_matrix))
mdl.add(mdl.sum(mdl.pulse(ride, 1) for ride in ride_itvs) <= capacity)
"""
    warnings = _check_no_overlap_cumulative_conflict(code, "dummy.py")
    assert warnings == [], f"exempt marker should suppress the finding: {warnings}"


def test_commented_out_mentions_ignored():
    code = """
# 過去はno_overlapとpulseを両方使っていたが今は使っていない
# mdl.add(mdl.no_overlap(seq, transition_matrix))
mdl.add(mdl.sum(mdl.pulse(itv, load) for itv in vehicle_itvs) <= capacity)
"""
    warnings = _check_no_overlap_cumulative_conflict(code, "dummy.py")
    assert warnings == [], f"commented-out mentions should be ignored: {warnings}"


if __name__ == "__main__":
    tests = [
        test_cpo_co_occurrence_detected,
        test_cpsat_co_occurrence_detected,
        test_no_overlap_only_not_flagged,
        test_cumulative_only_not_flagged,
        test_base_constraint_applier_usage_exempted,
        test_exempt_marker_suppresses_false_positive,
        test_commented_out_mentions_ignored,
    ]
    for t in tests:
        t()
        print(f"PASS: {t.__name__}")
    print("\nALL CHECKS PASSED")
