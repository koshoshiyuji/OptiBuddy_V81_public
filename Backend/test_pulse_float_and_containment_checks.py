"""
test_pulse_float_and_containment_checks.py

2026-09-18作成: EnergyCostAwareSchedulerで過去に実際に発生した2件のバグ
（mdl.pulse()へのfloat height直渡し、if_thenの第2引数への比較式直渡し）の
再発防止用静的チェック
（_check_pulse_float_height / _check_if_then_comparison_arg）の単体テスト。

実行方法:
    cd Backend
    python3 test_pulse_float_and_containment_checks.py
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

from domain_generator.static_checks import (
    _check_pulse_float_height,
    _check_if_then_comparison_arg,
)


# ── _check_pulse_float_height ──────────────────────────────────

def test_pulse_direct_float_call_detected():
    code = """
mdl.add(mdl.sum(mdl.pulse(itv, float(row["power"])) for itv in itvs) <= capacity)
"""
    warnings = _check_pulse_float_height(code, "dummy.py")
    assert len(warnings) == 1, f"direct float() call should fire: {warnings}"


def test_pulse_float_assigned_variable_detected():
    code = """
req_power = float(task["power_kw"])
mdl.add(mdl.pulse(itv, req_power) <= capacity)
"""
    warnings = _check_pulse_float_height(code, "dummy.py")
    assert len(warnings) == 1, f"float-assigned variable should fire: {warnings}"


def test_pulse_int_wrapped_variable_not_flagged():
    code = """
req_power = float(task["power_kw"])
mdl.add(mdl.pulse(itv, int(round(req_power * POWER_SCALE))) <= capacity)
"""
    warnings = _check_pulse_float_height(code, "dummy.py")
    assert warnings == [], f"int()-wrapped usage should not fire: {warnings}"


def test_pulse_plain_int_variable_not_flagged():
    code = """
req_power = int(task["power_units"])
mdl.add(mdl.pulse(itv, req_power) <= capacity)
"""
    warnings = _check_pulse_float_height(code, "dummy.py")
    assert warnings == [], f"non-float-assigned variable should not fire: {warnings}"


# ── _check_if_then_comparison_arg ──────────────────────────────

def test_if_then_comparison_detected():
    code = """
mdl.add(mdl.if_then(mdl.presence_of(itv), mdl.end_of(a) <= mdl.start_of(b)))
"""
    warnings = _check_if_then_comparison_arg(code, "dummy.py")
    assert len(warnings) == 1, f"if_then comparison arg should fire: {warnings}"


def test_if_then_presence_only_not_flagged():
    code = """
mdl.add(mdl.if_then(mdl.presence_of(inner), mdl.presence_of(outer)))
"""
    warnings = _check_if_then_comparison_arg(code, "dummy.py")
    assert warnings == [], f"presence_of-only if_then should not fire: {warnings}"


def test_if_then_direct_constraint_not_flagged():
    code = """
mdl.add(mdl.end_before_start(a, b, delay=2))
"""
    warnings = _check_if_then_comparison_arg(code, "dummy.py")
    assert warnings == [], f"direct constraint (no if_then) should not fire: {warnings}"


def test_commented_out_mentions_ignored():
    code = """
# 過去はここで mdl.if_then(mdl.presence_of(itv), mdl.end_of(a) <= mdl.start_of(b)) だった
mdl.add(mdl._start_of(inner) >= mdl._start_of(outer))
"""
    warnings1 = _check_if_then_comparison_arg(code, "dummy.py")
    assert warnings1 == [], f"commented-out mention should be ignored: {warnings1}"

    code2 = """
# req_power = float(x)  -- 過去のコメント
mdl.add(mdl.pulse(itv, safe_int_power) <= capacity)
"""
    warnings2 = _check_pulse_float_height(code2, "dummy.py")
    assert warnings2 == [], f"commented-out float assignment should be ignored: {warnings2}"


if __name__ == "__main__":
    tests = [
        test_pulse_direct_float_call_detected,
        test_pulse_float_assigned_variable_detected,
        test_pulse_int_wrapped_variable_not_flagged,
        test_pulse_plain_int_variable_not_flagged,
        test_if_then_comparison_detected,
        test_if_then_presence_only_not_flagged,
        test_if_then_direct_constraint_not_flagged,
        test_commented_out_mentions_ignored,
    ]
    for t in tests:
        t()
        print(f"PASS: {t.__name__}")
    print("\nALL CHECKS PASSED")
