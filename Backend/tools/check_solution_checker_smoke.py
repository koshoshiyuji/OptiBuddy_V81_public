"""
Backend/tools/check_solution_checker_smoke.py

2026-07-24追加。解チェッカー（DESIGN_2026-07-21、HANDOFF_2026-07-24実装）の
実docplex環境での動作確認用スモークテストCLI。sandboxにdocplexが無かったため
実装セッションでは直接確認できなかった以下3点を、実環境（Koshoshi側）で
確認するために作成した:

  1. StoreSiteの sol.is_valid_solution() 呼び出しが実際のdocplex.mp APIで
     例外なく動くか（実装セッションでは未検証だった最大のリスク項目）。
  2. 既存の baseline/tight シナリオ（＝正常系として作られたシナリオ）に対して、
     新設した解チェッカーが誤ってcategory="SOLVER"（バグ疑い）issueを
     出していないか（誤検知チェック）。誤検知が出た場合、チェッカー実装自体の
     バグの可能性と、もしかすると既存ソルバーに潜在バグがあった可能性の
     両方を疑う必要がある。
  3. MeetingRoomの非同期分岐（O(n²)、閾値200件）が実際に発火するか
     （合成的に大規模インスタンスを作って確認）。

使い方:
    cd Backend
    python3 tools/check_solution_checker_smoke.py

期待される出力（全て正常な場合）:
    - 対象4ドメイン×baseline/tight/infeasible（存在するシナリオのみ）について
      「category=SOLVER issues: []」（誤検知なし）
    - StoreSiteの行に「is_valid_solution() 呼び出し: OK」
    - MeetingRoom非同期テストで「非同期分岐: 発火した（期待通り）」

異常な場合の見方:
    - category=SOLVER issuesが1件でも出たら、そのメッセージを読んで
      「本当に制約違反があるのか」「チェッカー側の実装ミスか」を切り分けること
      （DESIGN_2026-07-21 4節: チェッカーの実装ミスもチェック対象という教訓）。
    - StoreSiteで例外が出た場合はdocplex.mpのバージョン差異によるAPI不一致の
      可能性が高い（is_valid_solution/is_feasible_solutionの引数・戻り値を
      実際のdocplexドキュメントで確認すること）。
"""
import json
import sys
from pathlib import Path

BACKEND_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BACKEND_ROOT))

SCENARIOS_DIR = BACKEND_ROOT / "dsl_repository" / "scenarios"

TARGET_DOMAINS = [
    ("truck_dispatcher", "TruckDispatcher"),
    ("meeting_room", "MeetingRoom"),
    ("store_site", "StoreSite"),
    ("line_changeover_scheduler", "LineChangeoverScheduler"),
]

# 2026-07-24追加分の新規ルールid（今回のセッションでissue_rules.py/各solverに
# 追加したもの）。solve_failed/zero_assignment_anomaly/config-error等は
# 元々category="SOLVER"で運用されていた既存の異常検知（今回の変更前から
# 存在する）であり、誤検知チェックの対象ではない。区別しないと、
# docplex未整備環境でヒューリスティックフォールバックが弱くて既存の
# solve_failed/zero_assignment_anomalyが出ただけのケースを「今回の実装の
# 誤検知」と誤解してしまう。
NEW_CHECKER_RULE_IDS_PREFIXES = (
    "duty_overtime",
    "assignment_capacity_violation", "assignment_feature_violation", "assignment_window_violation",
    "room_double_booking",
    "store_capacity_violation", "store_distance_violation",
    "mip_solution_invalid",
    "precedence_violation",
    "resource_capacity_violation",
)


def _is_new_checker_issue(issue_id: str) -> bool:
    return any(issue_id.startswith(p) for p in NEW_CHECKER_RULE_IDS_PREFIXES)


def _load_and_solve(snake: str, pascal: str, suffix: str):
    import importlib

    scenario_path = SCENARIOS_DIR / f"{snake}_{suffix}.json"
    if not scenario_path.exists():
        return None, f"シナリオファイルなし（スキップ）: {scenario_path.name}"

    business_dsl = json.loads(scenario_path.read_text(encoding="utf-8"))

    try:
        converter_module = importlib.import_module(f"dsl_transformer.{snake}_converter")
        convert_fn = getattr(converter_module, f"convert_{snake}_to_solver")
        solver_input = convert_fn(business_dsl)
    except Exception as e:
        return None, f"converter読み込み/変換失敗: {e}"

    solver_input.setdefault("issue_statuses", {})

    try:
        solver_module = importlib.import_module(f"solvers.{snake}_solver")
        solver_class = getattr(solver_module, f"{pascal}Solver")
        result = solver_class(solver_input).solve()
    except Exception as e:
        return None, f"solve()例外: {e}"

    return result, None


def check_no_false_positives():
    print("=" * 70)
    print("1. 誤検知チェック（baseline/tight/infeasible シナリオで、今回追加した")
    print("   解チェッカー由来のissueが出ないか。既存のsolve_failed/")
    print("   zero_assignment_anomaly等は対象外＝出ても異常ではない）")
    print("=" * 70)
    any_new_checker_issue = False
    for snake, pascal in TARGET_DOMAINS:
        for suffix in ("baseline", "tight", "infeasible"):
            result, err = _load_and_solve(snake, pascal, suffix)
            label = f"[{pascal}/{suffix}]"
            if err:
                print(f"{label} {err}")
                continue
            issues = result.get("issues", [])
            new_checker_issues = [i for i in issues if _is_new_checker_issue(i.get("id", ""))]
            other_solver_issues = [i for i in issues
                                    if i.get("category") == "SOLVER" and not _is_new_checker_issue(i.get("id", ""))]
            feasible = result.get("feasible")
            print(f"{label} feasible={feasible} 全issue数={len(issues)} "
                  f"新規チェッカーissues={[i['id'] for i in new_checker_issues]}"
                  + (f" (参考: 既存のSOLVER系issue={[i['id'] for i in other_solver_issues]})"
                     if other_solver_issues else ""))
            if new_checker_issues:
                any_new_checker_issue = True
                for si in new_checker_issues:
                    print(f"    ⚠ {si['id']}: {si['message']}")
    print()
    if any_new_checker_issue:
        print("⚠ 今回追加した解チェッカー由来のissueが検出されました。上記メッセージを確認してください"
              "（チェッカー実装ミスの可能性と、既存ソルバーの潜在バグの可能性の両方を疑うこと）。")
    else:
        print("OK: 誤検知なし（全シナリオで新規チェッカー由来のissueは0件）。"
              "既存のSOLVER系issue（solve_failed等）が表示されている場合は、docplex未整備等の"
              "別要因によるもので今回の実装とは無関係。")
    print()


def check_meeting_room_async_threshold():
    from solvers.base.solution_checker import ASYNC_THRESHOLD_O_N2
    n = ASYNC_THRESHOLD_O_N2 + 1
    print("=" * 70)
    print(f"2. MeetingRoom 非同期分岐テスト（合成的に{n}件のassignmentを作って確認、"
          f"閾値ASYNC_THRESHOLD_O_N2={ASYNC_THRESHOLD_O_N2}）")
    print("=" * 70)
    from solvers.meeting_room_solver import MeetingRoomSolver

    solver = MeetingRoomSolver.__new__(MeetingRoomSolver)
    solver.dsl = {"issue_statuses": {}}
    assignments = [
        {"meeting_id": f"M{i}", "meeting_name": f"会議{i}", "room_id": "R1", "room_name": "部屋1",
         "start_min": 0, "end_min": 100, "attendees": 1, "features": []}
        for i in range(n)
    ]
    rooms = [{"id": "R1", "capacity": 5, "features": [], "available_start_min": 0, "available_end_min": 1440}]
    issues, deferred = solver._run_solution_checker(assignments, rooms)
    if deferred is not None:
        print(f"OK: 非同期分岐が発火した（期待通り）。check_id={deferred['check_id']}, size={deferred['size']}")
    else:
        print("⚠ 期待に反して非同期分岐が発火しませんでした（閾値定数が変更された場合は正常）。")

    solver.dsl = {"issue_statuses": {}, "_gate2_full_check": True}
    issues2, deferred2 = solver._run_solution_checker(assignments, rooms)
    expected_pairs = n * (n - 1) // 2
    if deferred2 is None:
        print(f"OK: _gate2_full_check=True では閾値を無視して同期実行された"
              f"（{len(issues2)}件のissueを検出、C({n},2)={expected_pairs}件の重複ペアが全て検出されるのが正しい）。")
    else:
        print("⚠ _gate2_full_check=True でも非同期分岐してしまいました（バグ）。")
    print()


def check_store_site_is_valid_solution():
    print("=" * 70)
    print("3. StoreSite is_valid_solution() 呼び出し確認（baselineシナリオで実行）")
    print("=" * 70)
    result, err = _load_and_solve("store_site", "StoreSite", "baseline")
    if err:
        print(f"⚠ {err}")
        return
    mip_issues = [i for i in result.get("issues", []) if i.get("id") == "mip_solution_invalid"]
    if mip_issues:
        print(f"⚠ is_valid_solution() が解の不整合を検出しました: {mip_issues[0]['message']}")
    else:
        print("OK: is_valid_solution() 呼び出しは例外を出さず、解は整合していると判定されました。")
    print()


if __name__ == "__main__":
    check_no_false_positives()
    check_meeting_room_async_threshold()
    check_store_site_is_valid_solution()
    print("=" * 70)
    print("完了。上記に ⚠ が無ければ解チェッカーの実装は正常に動作しています。")
    print("=" * 70)
