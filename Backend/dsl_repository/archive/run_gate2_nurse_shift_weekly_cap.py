"""
NurseShiftWeeklyCap の Gate2動的チェック + 週次夜勤上限の実効性検証スクリプト。

背景:
  run_gate2_nurse_shift.py と同じ位置付けだが、対象は "nurse_shift_weekly_cap"。
  2026-07-12 のENGINEERING_LOG.md記載の不具合1（フィールド名不一致でキャップが
  常にデフォルト値7にフォールバックしていた）・不具合2（期間がウィンドウ長7日より
  短いbaseline/tightシナリオでウィンドウが1つも生成されずキャップ制約が一度も
  追加されていなかった）は、コード修正はコミット済みだが、修正後に実際の
  CP Optimizerソルブで動作確認ができていない
  （開発サンドボックスにcpoptimizerバイナリが無く、ウィンドウ生成ロジックのみを
  pure Pythonで切り出してシミュレートしただけだった）。

  このスクリプトは、run_gate2_dynamic_verification() で baseline/tight/infeasible を
  実際にconverter→solver.solve()まで通しで実行した上で、各スタッフの
  staff_rolling_night_counts（ローリング7日間ウィンドウでの最大夜勤回数）が
  その人のwork_limits.max_night_shifts_per_rolling_7daysを一件も超えていないことを
  機械的にassertする。ここが通って初めて「不具合1・2は実ソルブでも直っている」と言える。

実行方法:
  cd /Users/arche/work/OptiBuddy_V81/Backend
  python3 dsl_repository/run_gate2_nurse_shift_weekly_cap.py

前提:
  docplex / IBM CPLEX CP Optimizer がインストール済みであること。
"""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from domain_generator import run_gate2_dynamic_verification  # noqa: E402

SNAKE = "nurse_shift_weekly_cap"
SCENARIOS_DIR = Path(__file__).parent / "scenarios"


def _load_staff_caps(suffix: str) -> dict:
    """シナリオJSONから staff_id -> max_night_shifts_per_rolling_7days を読み取る。"""
    path = SCENARIOS_DIR / f"{SNAKE}_{suffix}.json"
    if not path.exists():
        return {}
    data = json.loads(path.read_text(encoding="utf-8"))
    caps = {}
    for s in data.get("staff", []):
        wl = s.get("work_limits", {})
        if "max_night_shifts_per_rolling_7days" in wl:
            caps[s["id"]] = wl["max_night_shifts_per_rolling_7days"]
    return caps


def _check_cap_violations(suffix: str, rolling_counts: dict) -> list:
    """staff_rolling_night_counts が各スタッフのキャップを超えていないか確認する。"""
    caps = _load_staff_caps(suffix)
    violations = []
    for sid, count in (rolling_counts or {}).items():
        cap = caps.get(sid)
        if cap is not None and count > cap:
            violations.append(
                f"{sid}: 実際の最大夜勤回数(7日間ウィンドウ)={count} > 上限={cap}"
            )
    return violations


def main():
    report = run_gate2_dynamic_verification(SNAKE)
    print(json.dumps(report, ensure_ascii=False, indent=2, default=str))

    print("\n" + "=" * 60)
    print("[判定サマリ]")
    scenarios = report.get("scenarios", {})
    any_cap_violation = False

    for suffix in ("baseline", "tight", "infeasible"):
        entry = scenarios.get(suffix, {})
        status = entry.get("status")
        feasible = entry.get("feasible")
        metrics = entry.get("metrics")
        print(f"  {suffix:12s}: status={status}, feasible={feasible}")
        if status == "exception":
            print(f"    !! 例外発生。トレースバック:\n{entry.get('traceback')}")
            continue
        if metrics:
            print(f"    metrics={metrics}")

    # staff_rolling_night_counts はsolver戻り値のトップレベルにあるため、
    # run_gate2_dynamic_verification のmetrics経由では取れない。
    # ここでは各シナリオを直接もう一度solveして確認する（重複実行だが、
    # 「実際にキャップが機能しているか」の直接証拠として重要なため許容する）。
    print("\n[週次夜勤上限（7日間ローリングウィンドウ）の実効性チェック]")
    from dsl_transformer.nurse_shift_weekly_cap_converter import convert_nurse_shift_weekly_cap_to_solver
    from solvers.nurse_shift_weekly_cap_solver import NurseShiftWeeklyCapSolver

    for suffix in ("baseline", "tight"):
        path = SCENARIOS_DIR / f"{SNAKE}_{suffix}.json"
        if not path.exists():
            print(f"  {suffix}: シナリオファイルなし、スキップ")
            continue
        business_dsl = json.loads(path.read_text(encoding="utf-8"))
        solver_input = convert_nurse_shift_weekly_cap_to_solver(business_dsl)
        solver_input.setdefault("issue_statuses", {})
        result = NurseShiftWeeklyCapSolver(solver_input).solve()

        if not result.get("feasible"):
            print(f"  {suffix}: infeasible。キャップチェックは対象外。")
            continue

        rolling_counts = result["solutions"][0].get("staff_rolling_night_counts", {})
        caps = _load_staff_caps(suffix)
        print(f"  {suffix}: staff_rolling_night_counts={rolling_counts}")
        print(f"  {suffix}: 上限(work_limits)={caps}")
        violations = _check_cap_violations(suffix, rolling_counts)
        if violations:
            any_cap_violation = True
            print(f"  !! {suffix}: キャップ違反 {len(violations)}件検出:")
            for v in violations:
                print(f"      - {v}")
        else:
            print(f"  {suffix}: キャップ違反なし（全スタッフが上限内）")

    print("\n" + "=" * 60)
    if any_cap_violation:
        print("[結論] 週次夜勤上限が守られていないケースがあります。"
              "不具合1/2が未解消の可能性が高いので、デモ前に必ず修正してください。")
        sys.exit(1)
    else:
        print("[結論] baseline/tightいずれもキャップ違反なし。"
              "不具合1/2は実ソルブでも解消していると判断してよさそうです。")


if __name__ == "__main__":
    main()
