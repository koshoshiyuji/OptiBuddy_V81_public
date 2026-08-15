"""
Backend/dsl_repository/lint_field_consistency_all_domains.py

Gate 2の静的フィールド突き合わせチェック（domain_generator.py の
check_converter_solver_field_consistency）は、これまで「新規ドメイン登録の
diffスキャン時」（scan_diffs_for_warnings経由）にしか実行されていなかった。

2026-07-14、nurse_shift_weekly_cap_solver.py の _build_hospital_contexts() が
config.get("max_consecutive_night_shifts", 2) という、実際には存在しない
グローバルキー（本来は staff.work_limits 配下のスタッフ個別フィールド）に
フォールバックしていたバグが発覚した。試しに既存の
check_converter_solver_field_consistency() をこのペアに対して手動で実行した
ところ、"max_consecutive_night_shifts" は最初から missing_in_converter として
検出されていた ―― つまりチェックの検出ロジック自体（.get()呼び出しのAST検出も
含む）に不備は無く、「既に登録済みのドメインに対して定期的に/継続的に
実行する経路が無かった」ことが本当のギャップだった。

本スクリプトは、Backend/solvers/*_solver.py と Backend/dsl_transformer/*_converter.py
の実ファイルペアを全ドメイン分走査し、check_converter_solver_field_consistency()を
かけて結果を出力する。新規ドメイン登録時だけでなく、既存ドメインのバグ修正・
リファクタ後や、定期的な健全性チェックとして手動実行することを想定する。

注意: このチェックはヒューリスティック（ファイル全体でのフラットなキー集合比較、
ネストしたオブジェクト単位の区別はしない）なので誤検知を含む。人間が
`unused_in_solver`/`missing_in_converter` の一覧を見て「これは実害があるか」を
判断すること（Gate2本来の位置づけと同じ）。

実行方法:
  cd /Users/arche/work/OptiBuddy_V81/Backend
  python3 dsl_repository/lint_field_consistency_all_domains.py
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from domain_generator import check_converter_solver_field_consistency  # noqa: E402

BACKEND_ROOT = Path(__file__).parent.parent
SOLVERS_DIR = BACKEND_ROOT / "solvers"
TRANSFORMER_DIR = BACKEND_ROOT / "dsl_transformer"


def find_domain_pairs() -> list[tuple[str, Path, Path]]:
    """{snake}_solver.py と {snake}_converter.py が両方揃っているペアを探す。"""
    pairs = []
    for solver_path in sorted(SOLVERS_DIR.glob("*_solver.py")):
        snake = solver_path.stem[: -len("_solver")]
        converter_path = TRANSFORMER_DIR / f"{snake}_converter.py"
        if converter_path.exists():
            pairs.append((snake, solver_path, converter_path))
    return pairs


def main():
    pairs = find_domain_pairs()
    if not pairs:
        print("solver.py / converter.py のペアが見つかりませんでした。")
        return

    any_missing = False
    for snake, solver_path, converter_path in pairs:
        print("=" * 70)
        print(f"ドメイン: {snake}")
        print(f"  solver:    {solver_path.relative_to(BACKEND_ROOT.parent)}")
        print(f"  converter: {converter_path.relative_to(BACKEND_ROOT.parent)}")

        solver_code = solver_path.read_text(encoding="utf-8")
        converter_code = converter_path.read_text(encoding="utf-8")
        result = check_converter_solver_field_consistency(converter_code, solver_code)

        if result["errors"]:
            print(f"  [構文エラー] {result['errors']}")
            continue

        if result["missing_in_converter"]:
            any_missing = True
            print(f"  [missing_in_converter] solverが参照するがconverterが出力しないキー:")
            for k in result["missing_in_converter"]:
                print(f"    - {k}")
        else:
            print("  [missing_in_converter] 該当なし")

        if result["unused_in_solver"]:
            print(f"  [unused_in_solver] converterが出力するがsolverが参照しないキー:")
            for k in result["unused_in_solver"]:
                print(f"    - {k}")
        else:
            print("  [unused_in_solver] 該当なし")

    print("=" * 70)
    if any_missing:
        print(
            "[結論] missing_in_converter が1件以上あるドメインがあります。"
            "実行時KeyError、または.get()の暗黙デフォルト値への意図しない"
            "フォールバックが起きていないか、一覧を確認してください。"
        )
    else:
        print("[結論] 全ドメインでmissing_in_converterはありませんでした。")


if __name__ == "__main__":
    main()
