"""
Backend/tools/gate2_coverage_check.py

Gate2トレーサビリティチェック（HANDOFF_2026-07-15b「顧客ごとのOptiBuddy構想」議論、
論点: hearing→DSL→converter カバレッジ）を単発scriptとして走らせるためのCLI。

背景:
  従来のGate2静的チェック（check_converter_solver_field_consistency）は
  converter.py⇔solver.pyというコード同士の対称性しか見ておらず、
  「ヒアリング→DSL」「DSL→converter」という手前2段の突き合わせは
  一切機械チェックされていなかった。本ツールはその2段を検証する
  domain_generator.py側の新関数（check_dsl_converter_field_consistency /
  detect_hearing_dsl_gaps）を、任意のドメインに対して走らせる。

用途:
  1. 試験実装として、過去のnew_domain登録（例: LineChangeoverScheduler,
     2026-07-15）に遡及的に適用し、機能そのものの妥当性を検証する
     （1-1「new_domain経路の信頼性の全般評価」を、次の登録機会を待たずに
     既存データで前倒しする）。
  2. 将来 _run_domain_job の同期チェーンへ inline 統合する際も、
     チェックロジック自体はdomain_generator.py側の関数のままで、
     このスクリプトは呼び出し口の一つに過ぎない（新規機構の重複を避ける）。

使い方:
  cd Backend
  python3 tools/gate2_coverage_check.py --domain line_changeover_scheduler \\
      --hearing ../docs/test_hearings/line_changeover_scheduler_hearing_j.md

  python3 tools/gate2_coverage_check.py --domain line_changeover_scheduler \\
      --hearing ../docs/test_hearings/line_changeover_scheduler_hearing_j.md --skip-llm
      # AST差集合チェック（チェック2）のみ、LLM呼び出しなしで即座に確認したい場合
"""
import argparse
import json
import sys
from pathlib import Path

BACKEND_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BACKEND_ROOT))

from domain_generator import (  # noqa: E402
    check_dsl_converter_field_consistency,
    detect_hearing_dsl_gaps,
)


def load_scenarios(snake: str) -> dict:
    scenarios = {}
    scenario_dir = BACKEND_ROOT / "dsl_repository" / "scenarios"
    for suffix in ("baseline", "tight", "infeasible"):
        path = scenario_dir / f"{snake}_{suffix}.json"
        if path.exists():
            scenarios[suffix] = json.loads(path.read_text(encoding="utf-8"))
    return scenarios


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--domain", required=True, help="snake_case ドメイン名（例: line_changeover_scheduler）")
    parser.add_argument("--hearing", required=True, help="ヒアリングテキストファイルのパス")
    parser.add_argument(
        "--skip-llm", action="store_true",
        help="LLM呼び出し（チェック1: hearing_dsl_gaps）をスキップし、チェック2（AST差集合）のみ実行"
    )
    args = parser.parse_args()

    snake = args.domain
    hearing_path = Path(args.hearing)
    if not hearing_path.is_absolute():
        hearing_path = Path.cwd() / hearing_path
    hearing_text = hearing_path.read_text(encoding="utf-8")

    scenarios = load_scenarios(snake)
    if not scenarios:
        print(f"[エラー] シナリオが見つかりません: dsl_repository/scenarios/{snake}_*.json", file=sys.stderr)
        sys.exit(1)

    converter_path = BACKEND_ROOT / "dsl_transformer" / f"{snake}_converter.py"
    solver_path = BACKEND_ROOT / "solvers" / f"{snake}_solver.py"
    ui_converter_path = BACKEND_ROOT / "dsl_transformer" / f"{snake}_ui_converter.py"
    if not converter_path.exists() or not solver_path.exists():
        print(f"[エラー] converter/solverが見つかりません: {converter_path} / {solver_path}", file=sys.stderr)
        sys.exit(1)

    converter_code = converter_path.read_text(encoding="utf-8")
    solver_code = solver_path.read_text(encoding="utf-8")
    # 2026-07-31追加: ui_converter.pyも比較対象に含める（省略可、無ければNone）。
    # solverのデータをui_converter.pyが実際に参照しているかまで判定させるため。
    ui_converter_code = ui_converter_path.read_text(encoding="utf-8") if ui_converter_path.exists() else None
    if ui_converter_code is None:
        print(f"  [注意] ui_converter.pyが見つかりません（画面表示の配線チェックはスキップされます）: {ui_converter_path}")

    print("=" * 70)
    print(f"Gate2トレーサビリティチェック: {snake}")
    print(f"シナリオ: {sorted(scenarios.keys())}")
    print("=" * 70)

    # --- チェック2: DSL⇔converter フィールド突き合わせ（AST差集合、LLM不要） ---
    print("\n[チェック2] DSL⇔converter フィールド突き合わせ")
    field_check = check_dsl_converter_field_consistency(list(scenarios.values()), converter_code)
    if field_check["errors"]:
        for e in field_check["errors"]:
            print(f"  ERROR: {e}")
    print(f"  missing_in_dsl      ({len(field_check['missing_in_dsl'])}件): {field_check['missing_in_dsl']}")
    print(f"  unused_in_converter ({len(field_check['unused_in_converter'])}件): {field_check['unused_in_converter']}")

    result_blob = {"field_check": field_check}

    # --- チェック1: hearing⇔DSL/コード カバレッジ（LLM） ---
    if args.skip_llm:
        print("\n[チェック1] スキップ（--skip-llm）")
    else:
        print("\n[チェック1] hearing⇔DSL/コード カバレッジ判定（LLM呼び出し中...）")
        coverage = detect_hearing_dsl_gaps(
            domain_name=snake,
            hearing_texts=[hearing_text],
            dsl_scenarios=scenarios,
            converter_code=converter_code,
            solver_code=solver_code,
            ui_converter_code=ui_converter_code,
        )
        items = coverage["items"]
        covered = [i for i in items if i.get("status") == "covered"]
        gaps = [i for i in items if i.get("status") == "gap"]
        deferred = [i for i in items if i.get("status") == "deferred"]
        required_gaps = [g for g in gaps if g.get("priority") == "required"]
        optional_gaps = [g for g in gaps if g.get("priority") != "required"]

        print(
            f"  covered={len(covered)}件 / gap={len(gaps)}件"
            f"（うちrequired={len(required_gaps)}件・optional={len(optional_gaps)}件）"
            f" / deferred={len(deferred)}件 (total={len(items)}件)"
        )

        if required_gaps:
            print("\n  --- gap 一覧（required、優先対応） ---")
            for g in required_gaps:
                print(f"  [節{g.get('hearing_section', '?')}] {g.get('requirement')}")
                print(f"      根拠: {g.get('evidence')}")

        if optional_gaps:
            print("\n  --- gap 一覧（optional） ---")
            for g in optional_gaps:
                print(f"  [節{g.get('hearing_section', '?')}] {g.get('requirement')}")
                print(f"      根拠: {g.get('evidence')}")

        if deferred:
            print("\n  --- deferred 一覧 ---")
            for d in deferred:
                print(f"  [節{d.get('hearing_section', '?')}] {d.get('requirement')}")
                print(f"      根拠: {d.get('evidence')}")

        result_blob["hearing_coverage"] = coverage

    out_path = BACKEND_ROOT.parent / "docs" / f"gate2_coverage_{snake}.json"
    out_path.write_text(json.dumps(result_blob, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\n詳細結果を保存: {out_path}")


if __name__ == "__main__":
    main()
