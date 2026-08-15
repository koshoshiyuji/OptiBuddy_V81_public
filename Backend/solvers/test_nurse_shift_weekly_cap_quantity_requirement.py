"""
test_nurse_shift_weekly_cap_quantity_requirement.py
=======================================================

NurseShiftWeeklyCapSolverの2026-07-18バグ修正（数量要件hard/softが機能して
いなかった件）の統合テスト。docs/DESIGN_2026-07-18_layer_ab_pattern_library.md
2-2-2節、docs/ENGINEERING_LOG.md参照。

本サンドボックスにはCP Optimizerソルバーエンジン（cpoptimizerバイナリ）が
無く実ソルブができないため、`CpoModel.solve` をスタブ化してモデル構築段階
（mdl.add()されたexpressionの中身）までを検証する。これは
docs/ENGINEERING_LOG.md 2026-07-12で採用された前例（ロジックを切り出して
シミュレートする方式）と同じ考え方を、既存ソルバーへのモンキーパッチで
実現したもの。

実行方法:
    cd Backend
    python -m pytest solvers/test_nurse_shift_weekly_cap_quantity_requirement.py -v
"""

import pytest

try:
    from docplex.cp.model import CpoModel
    HAS_DOCPLEX = True
except ImportError:
    HAS_DOCPLEX = False

pytestmark = pytest.mark.skipif(not HAS_DOCPLEX, reason="docplex not installed")


def _minimal_solver_input(quantity_requirement_mode, task_override_mode=None):
    """
    スタッフ1名・タスク1件（required_count=2、つまり構造的に必ず不足する）の
    最小シナリオ。hard/softの挙動差を確認するのが目的なので、意図的に
    「必要人数に対して候補が足りない」状況を作る。
    """
    task = {
        "id": "t1", "name": "T1", "day": 1,
        "start_window": 0, "end_window": 1440, "duration": 60,
        "shift_type": "day", "is_night_shift": False,
        "requirement": None, "required_count": 2, "min_chiefs": 0,
        "novice_requires_senior": False,
    }
    if task_override_mode is not None:
        task["quantity_requirement_mode"] = task_override_mode

    staff = {
        "id": "s1", "name": "A", "grade": "STAFF",
        "skills": [], "certifications": [], "hourly_rate": 2000,
        "availability": {"start": 0, "end": 1440}, "work_limits": {},
        "preferences": {"desired_tasks": []},
    }
    config = {
        "time_limit": 5, "preference_penalty_weight": 200,
        "min_preference_satisfaction_rate": 0.6, "operation_start": 0,
        "shifts": [], "breaks": [], "week_definition": {},
        "night_shift_fairness_weight": 100,
        "novice_supervision_rule": {
            "novice_experience_threshold_years": 1.0,
            "senior_experience_threshold_years": 3.0,
        },
        "relaxation_priority_order": ["desired_shift", "consecutive_night_limit", "fairness"],
        "rolling_window_days": 7,
        "relax_incomplete_window_at_period_end": True,
        "quantity_requirement_mode": quantity_requirement_mode,
    }
    return [staff], [task], config


def _build_captured_model(quantity_requirement_mode, task_override_mode=None, monkeypatch=None):
    """
    NurseShiftWeeklyCapSolver._build_and_solve() を、CpoModel.solve() を
    スタブ化した状態で実行し、構築済みの CpoModel を返す。
    """
    from solvers.nurse_shift_weekly_cap_solver import NurseShiftWeeklyCapSolver

    captured = {}

    def _fake_solve(self, *args, **kwargs):
        captured["mdl"] = self
        return None  # ソルブエンジンなしなので None（infeasible/timeout相当）を返す

    monkeypatch.setattr(CpoModel, "solve", _fake_solve)

    staff_list, tasks, config = _minimal_solver_input(quantity_requirement_mode, task_override_mode)
    solver = NurseShiftWeeklyCapSolver.__new__(NurseShiftWeeklyCapSolver)
    solver.dsl = {"staff": staff_list, "tasks": tasks, "config": config, "issue_statuses": {}}

    # 2026-08-13修正: config.solver_engine分岐の導入により、CPOパスは
    # _build_and_solve_cpo()に改名された（_build_and_solve_cpsat()も新設）。
    solution_data, candidates = solver._build_and_solve_cpo(staff_list, tasks, config)

    assert "mdl" in captured, "mdl.solve() が呼ばれていない（モデル構築中に例外で落ちた可能性）"
    assert len(candidates) == 1  # スタッフ1名のみ候補
    return captured["mdl"]


# ---------------------------------------------------------------------------
# soft モード（既定）: ハード制約は追加されず、モデル構築が正常に完了する
# ---------------------------------------------------------------------------

def test_soft_mode_does_not_add_hard_equality_constraint(monkeypatch):
    mdl = _build_captured_model(quantity_requirement_mode="soft", monkeypatch=monkeypatch)

    # softモードでは required_count(=2) に対して候補が1件しかなくても
    # ハード制約は追加されない（構造的unsatisfiableな制約が入らないこと）。
    exprs_str = [str(e[0]) for e in mdl.get_all_expressions()]
    assert not any("== 2" in s for s in exprs_str), (
        "softモードなのにハード等式制約が追加されている（旧バグの再発）"
    )


def test_domain_default_mode_is_soft_when_unspecified(monkeypatch):
    """config.quantity_requirement_mode が None（未指定）の場合、既定でsoftになること。"""
    mdl = _build_captured_model(quantity_requirement_mode=None, monkeypatch=monkeypatch)
    exprs_str = [str(e[0]) for e in mdl.get_all_expressions()]
    assert not any("== 2" in s for s in exprs_str)


# ---------------------------------------------------------------------------
# hard モード: >= 制約が追加される（旧バグの exact equality ではなく at_least）
# ---------------------------------------------------------------------------

def test_hard_mode_adds_at_least_constraint_not_exact_equality(monkeypatch):
    mdl = _build_captured_model(quantity_requirement_mode="hard", monkeypatch=monkeypatch)

    exprs_str = [str(e[0]) for e in mdl.get_all_expressions()]
    # sum(...) >= 2 の形の制約が追加されていること（exact equality "== 2" ではない）
    assert any(">= 2" in s for s in exprs_str), (
        "hardモードなのに 'assigned_count >= required_count' 制約が見当たらない"
    )
    assert not any("== 2" in s for s in exprs_str), (
        "hardモードで exact equality ('== 2') が使われている（旧バグの挙動）"
    )


# ---------------------------------------------------------------------------
# タスク単位の上書き
# ---------------------------------------------------------------------------

def test_task_level_override_takes_precedence_over_domain_default(monkeypatch):
    """ドメイン既定値がsoftでも、タスク側でhardを指定していればhardが優先されること。"""
    mdl = _build_captured_model(
        quantity_requirement_mode="soft", task_override_mode="hard", monkeypatch=monkeypatch
    )
    exprs_str = [str(e[0]) for e in mdl.get_all_expressions()]
    assert any(">= 2" in s for s in exprs_str)


# ---------------------------------------------------------------------------
# 2026-07-18追加: UNDERSTAFFING_PENALTYのスケール不整合バグ（全員未割当の原因）
# ---------------------------------------------------------------------------
#
# 経緯: quantity_requirement_mode="soft"を正しく機能させた結果（このファイルの
# 他テスト参照）、旧実装のハード等式では隠れていた別のバグが露呈した。
# 項1(総人件費)は cost_terms.append(int(unit_cost * 100) * presence) と
# ×100スケールで積算されるのに対し、UNDERSTAFFING_PENALTYは×100されておらず
# 単位が揃っていなかった。実データ（最大hourly_rate=2500円/h, 最大duration=480分）
# では1件あたりのコスト（スケール後）が最大2,000,000に達し、旧定数500,000を
# 上回っていたため、「配置するより空席にする方が目的関数上得」という逆転が起き、
# 全員未割当になっていた（docs/ENGINEERING_LOG.md参照）。
# このテストは、この2項が同じスケール前提で比較可能であることを将来にわたって保証する。

def test_understaffing_penalty_exceeds_max_realistic_single_assignment_cost():
    """
    UNDERSTAFFING_PENALTY（1人不足あたりのペナルティ）が、現実的な最大値の
    1件あたり人件費コスト（cost_termsと同じ×100スケール）を確実に上回ること。
    将来hourly_rateの想定上限やduration上限が変わってもすぐ気づけるよう、
    余裕を持った上限値（hourly_rate=5000円/h, duration=12時間）で計算する。
    """
    from solvers.nurse_shift_weekly_cap_solver import UNDERSTAFFING_PENALTY

    max_hourly_rate = 5000
    max_duration_hours = 12
    max_single_assignment_cost_scaled = int(max_hourly_rate * max_duration_hours * 100)

    assert UNDERSTAFFING_PENALTY > max_single_assignment_cost_scaled, (
        f"UNDERSTAFFING_PENALTY({UNDERSTAFFING_PENALTY}) が最大想定人件費コスト"
        f"({max_single_assignment_cost_scaled}, ×100スケール)を上回っていない。"
        f"「配置するより空席にする方が得」という逆転が起こり得る。"
    )


def test_soft_mode_prefers_assignment_over_understaffing_with_real_baseline_scenario(monkeypatch):
    """
    登録済みbaselineシナリオ（実データ）で、soft既定のタスクについて
    「1人配置するコスト」より「1人分の不足ペナルティ」の方が大きいことを、
    実際にモデル構築に使われた候補データから直接検証する
    （＝全員未割当になっていた実際のシナリオでの再発防止）。
    """
    import json
    from solvers.nurse_shift_weekly_cap_solver import UNDERSTAFFING_PENALTY
    from dsl_transformer.nurse_shift_weekly_cap_converter import (
        convert_nurse_shift_weekly_cap_to_solver,
    )

    with open("dsl_repository/scenarios/nurse_shift_weekly_cap_baseline.json") as f:
        scenario = json.load(f)
    business_dsl = scenario.get("dsl", scenario)
    solver_input = convert_nurse_shift_weekly_cap_to_solver(business_dsl)

    max_hourly_rate = max(s.get("hourly_rate", 2000) for s in solver_input["staff"])
    max_duration_min = max(t.get("duration", 60) for t in solver_input["tasks"])
    max_cost_scaled = int((max_duration_min / 60.0) * max_hourly_rate * 100)

    assert UNDERSTAFFING_PENALTY > max_cost_scaled, (
        f"実データの最大コスト({max_cost_scaled})がUNDERSTAFFING_PENALTY"
        f"({UNDERSTAFFING_PENALTY})以上。全員未割当バグが再発する可能性がある。"
    )
