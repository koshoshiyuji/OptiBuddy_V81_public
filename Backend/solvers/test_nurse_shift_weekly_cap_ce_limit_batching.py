"""
Backend/solvers/test_nurse_shift_weekly_cap_ce_limit_batching.py

NurseShiftWeeklyCapのCE上限（CPLEX無料版の上限）フォールバック処理のユニット
テスト。実際のCP Optimizerなしで動作する（_build_and_solveをモンキーパッチで
置き換える）。

2026-07-26 実機テストで見つかった不具合の再発防止テスト:
  「30人のグループに分けても、そのグループ自体がまだ上限を超えてしまう」場合に、
  同じ人数のグループを何度も作っては同じ理由で失敗し続け、無限にやり直して
  Pythonがエラー（RecursionError）で落ちる不具合があった。
  修正後は、やり直す度にグループの人数を必ず前回より小さくすることを検証する。

実行方法:
    cd Backend
    python -m pytest solvers/test_nurse_shift_weekly_cap_ce_limit_batching.py -v
"""

import pytest

import solvers.nurse_shift_weekly_cap_solver as ns
from solvers.nurse_shift_weekly_cap_solver import NurseShiftWeeklyCapSolver
from solvers.base.ce_limit_lns import CeLimitExceededError


def _make_solver_input(n_staff: int, n_tasks: int = 3) -> dict:
    staff = [{"id": f"s{i}", "grade": "STAFF"} for i in range(n_staff)]
    tasks = [
        {"id": f"t{i}", "required_count": 1, "min_chiefs": 0}
        for i in range(n_tasks)
    ]
    # 2026-08-13追加: CE-limit（CPLEX無料版の組合せ数上限）はCPO(docplex.cp)
    # 固有の概念で、CP-SAT(OR-Tools)には存在しない。本テストファイルは
    # _build_and_solve_cpo()をモンキーパッチしてCE-limitフォールバック動作を
    # 検証するものなので、明示的にengine="cpo"を指定する（既定値は
    # engine_select.pyのポリシーによりcpsatに変わったため、指定しないと
    # このパッチが素通りしてしまう）。
    return {"staff": staff, "tasks": tasks, "config": {"solver_engine": "cpo"}}


def _fake_assigned_tasks(staff_list, tasks):
    """
    スタッフ・タスクの数に応じて適当な割り当て結果を作る（テスト用）。
    2026-07-26修正: merge_results()がissue再計算のため_build_hospital_contexts()を
    呼ぶようになり、そこで各割り当てに"start"/"end"キーが必須になった
    （夜勤後休憩チェックでソートに使うため）。実際のソルバーが返す形に
    合わせてstart/end/day/is_night_shiftも含める。
    """
    assigned = []
    for i, task in enumerate(tasks):
        if i < len(staff_list):
            s = staff_list[i]
            assigned.append({
                "task_id": task["id"], "staff_id": s["id"], "grade": s.get("grade", "STAFF"),
                "cost": 10,
                "start": task.get("start_window", 0),
                "end":   task.get("end_window", 60),
                "day":   task.get("day", 1),
                "is_night_shift": task.get("is_night_shift", False),
            })
    return assigned


@pytest.fixture
def patch_build_and_solve(monkeypatch):
    """
    _build_and_solveを、staff件数が`threshold`を超える場合だけ
    CeLimitExceededError（層A共通例外）を投げるフェイク実装に差し替えるヘルパー。
    """
    def _apply(threshold: int, call_log: list):
        def fake_build_and_solve(self, staff_list, tasks, config):
            call_log.append(len(staff_list))
            if len(staff_list) > threshold:
                raise CeLimitExceededError(
                    f"fake CE limit: staff={len(staff_list)} > threshold={threshold}"
                )
            solution_data = {
                "name": "Plan A", "label": "test", "feasible": True,
                "tasks": _fake_assigned_tasks(staff_list, tasks),
                "task_summary": [],
                "metrics": {
                    "total_cost": 0, "assigned_count": 0, "understaffed_count": 0,
                    "coverage_rate": 1.0, "solve_time": 0.0,
                    "unmet_preference_count": 0, "preference_satisfaction_rate": 1.0,
                },
                "staff_rolling_night_counts": {},
            }
            return solution_data, []

        # 2026-08-13修正: config.solver_engine分岐の導入により、CPOパスは
        # _build_and_solve_cpo()に改名された（_build_and_solve_cpsat()も
        # 新設）。CE-limitはCPO固有の概念のため、CPOパスをパッチする。
        monkeypatch.setattr(
            ns.NurseShiftWeeklyCapSolver, "_build_and_solve_cpo", fake_build_and_solve
        )
        monkeypatch.setattr(
            ns.NurseShiftWeeklyCapSolver, "_detect_issues",
            lambda self, *a, **kw: []
        )

    return _apply


# ---------------------------------------------------------------------------
# ケース1: グループを繰り返し小さくすればいずれ解決できる場合
# ---------------------------------------------------------------------------

def test_progressively_shrinks_batch_size_until_it_succeeds(patch_build_and_solve):
    """
    40人中、6人を超えるグループは必ず上限に当たるという設定でテストする。
    既定のグループサイズ（30人）は最初は失敗するはずで、そこから半分ずつ
    小さくしていって、最終的に6人以下のグループに収まった時点で成功すること。
    """
    call_log: list = []
    patch_build_and_solve(threshold=6, call_log=call_log)

    solver_input = _make_solver_input(n_staff=40, n_tasks=3)
    solver_input["config"]["ce_limit_batch_size"] = 30

    result = NurseShiftWeeklyCapSolver(solver_input).solve()

    assert result["feasible"] is True, result
    # 途中経過として、30人・10人など「6人を超える」グループで一度は
    # 失敗が記録されているはず（call_logに6より大きい値が含まれる）
    assert any(n > 6 for n in call_log), call_log
    # 最終的には6人以下のグループでの成功が記録されているはず
    assert any(n <= 6 for n in call_log), call_log
    print(f"call_log（各_build_and_solve呼び出し時のスタッフ数）: {call_log}")


# ---------------------------------------------------------------------------
# ケース2: どこまでグループを小さくしても解決できない場合（無限ループ防止）
# ---------------------------------------------------------------------------

def test_gives_up_cleanly_instead_of_infinite_recursion(patch_build_and_solve):
    """
    2026-07-26に実機で発生した不具合の再現・修正確認テスト。
    どんなに小さいグループにしても（1人でも）上限に当たり続ける、という
    極端な設定で、無限に再試行してPythonがRecursionErrorで落ちるのではなく、
    有限回で諦めて明確なエラー結果を返すことを確認する。
    """
    call_log: list = []
    # threshold=0 → 1人のグループでも常に失敗する
    patch_build_and_solve(threshold=0, call_log=call_log)

    solver_input = _make_solver_input(n_staff=40, n_tasks=3)
    solver_input["config"]["ce_limit_batch_size"] = 30

    # RecursionErrorやその他の例外を投げずに戻ってくることそのものが最重要の確認事項
    result = NurseShiftWeeklyCapSolver(solver_input).solve()

    assert result["status"] == "ok"
    assert result["feasible"] is False
    issue_ids = {i["id"] for i in result["issues"]}
    assert "ce_limit_unresolvable" in issue_ids, result["issues"]
    # 呼び出し回数が有限であること（無限ループしていないことの間接証拠）
    assert len(call_log) < 200, len(call_log)
    print(f"call_log の長さ（有限であることの確認）: {len(call_log)}")


# ---------------------------------------------------------------------------
# ケース3: 最初から上限に当たらない場合は通常通り一度で解決すること
# ---------------------------------------------------------------------------

def test_no_fallback_when_under_limit(patch_build_and_solve):
    call_log: list = []
    patch_build_and_solve(threshold=1000, call_log=call_log)

    solver_input = _make_solver_input(n_staff=5, n_tasks=3)
    result = NurseShiftWeeklyCapSolver(solver_input).solve()

    assert result["feasible"] is True
    assert call_log == [5]
    assert "_decompose_meta" not in result["solutions"][0]


# ---------------------------------------------------------------------------
# ケース4: グループをまたいで人数が揃うタスクで、誤った「人員不足」警告が
#          残らないこと（2026-07-26 実機テストで発見した不具合の再発防止）
# ---------------------------------------------------------------------------

def test_merge_does_not_report_stale_understaffed_issue_once_later_batch_fills_it():
    """
    実機での不具合再現:
      必要人数3名のタスクに対し、1つ目のグループでは1名しか割り当てられず
      （そのグループ単体で見れば「人員不足」は正しい）、2つ目のグループで
      残り2名が割り当てられ、最終的には3名揃う。
    この時、統合後の結果には「人員不足」の警告が残ってはいけない
    （Overview側の集計＝充足、Issues側＝人員不足、という矛盾が実機で発生した）。
    また、必要人数を既に満たしたタスクに0人しか割り当てなかったグループが
    「全員未割当（異常）」と誤検知されることも無いようにする。
    """
    from solvers.nurse_shift_weekly_cap_batch_decomposer import merge_results

    original_solver_input = {
        "staff": [{"id": f"s{i}", "grade": "STAFF"} for i in range(3)],
        "tasks": [{
            "id": "t1", "required_count": 3, "min_chiefs": 0,
            "day": 1, "start_window": 0, "end_window": 60, "duration": 60,
        }],
        "config": {},
    }

    def _assignment(sid):
        return {
            "task_id": "t1", "staff_id": sid, "grade": "STAFF", "cost": 10,
            "start": 0, "end": 60, "day": 1, "is_night_shift": False,
        }

    batch_results = [
        {
            "status": "ok", "feasible": True,
            "solutions": [{"tasks": [_assignment("s0")]}],
            # このバッチ単体で見た「人員不足」警告（本来は統合後に捨てられるべき）
            "issues": [{
                "id": "understaffed_t1", "severity": "CRITICAL",
                "title": "人員不足: t1", "message": "バッチ単体では不足",
            }],
        },
        {
            "status": "ok", "feasible": True,
            "solutions": [{"tasks": [_assignment("s1"), _assignment("s2")]}],
            "issues": [],
        },
    ]

    merged = merge_results(batch_results, original_solver_input)

    assert merged["feasible"] is True
    sol = merged["solutions"][0]
    assert sol["metrics"]["assigned_count"] == 3
    assert sol["metrics"]["understaffed_count"] == 0

    issue_ids = {i["id"] for i in merged["issues"]}
    assert "understaffed_t1" not in issue_ids, (
        f"統合後も古い「人員不足」警告が残っている: {merged['issues']}"
    )
    for i in merged["issues"]:
        assert "未割当" not in i.get("title", ""), (
            f"「全員未割当」の誤検知が残っている: {i}"
        )
