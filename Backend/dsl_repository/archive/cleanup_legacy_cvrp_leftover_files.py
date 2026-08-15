"""
delete_old_cvrp.py が自動検出できなかった旧CVRP関連ファイルを削除するスクリプト。

背景:
  delete_old_cvrp.py（cleanup_domain.py経由）はDB（scenarios/dsl_definitions/
  dsl_evolution_log）の削除には対応しているが、ファイル削除はドメイン名の
  snake_case規約（capacitated_vehicle_routing_problem_*）に厳密一致するものしか
  検出しない。実際のファイルは短縮名"cvrp_*"や既にリネーム済みの".bak"のため
  自動検出にヒットしない。delete_old_cvrp.py自身のdocstringにもこの制限が
  明記されている。

削除対象（すべて2026-07-09時点で存在確認済み、旧CapacitatedVehicleRoutingProblem
関連であることを確認済み）:
  - Backend/dsl_repository/scenarios/cvrp_baseline.json
  - Backend/dsl_repository/scenarios/cvrp_infeasible.json
  - Backend/dsl_repository/scenarios/cvrp_tight.json
  - Backend/dsl_transformer/_deleted_cvrp_converter.py.bak
  - Backend/dsl_transformer/_deleted_cvrp_ui_converter.py.bak
  - Backend/register_cvrp_scenarios.py（削除済みドメインの登録スクリプトのため不要）

実行方法:
  cd /Users/arche/work/OptiBuddy_V81/Backend
  python3 dsl_repository/cleanup_legacy_cvrp_leftover_files.py --dry-run   # まずプレビュー
  python3 dsl_repository/cleanup_legacy_cvrp_leftover_files.py            # 実際に削除

前提:
  先に dsl_repository/delete_old_cvrp.py を実行し、DB側（scenarios/dsl_definitions/
  dsl_evolution_log）が削除済みであること（順序が逆でも実害はないが、DB側を先に
  片付けておく方が状態として分かりやすい）。
"""
import argparse
import sys
from pathlib import Path

_BACKEND_ROOT = Path(__file__).parent.parent
_PROJECT_ROOT = _BACKEND_ROOT.parent

TARGETS = [
    _BACKEND_ROOT / "dsl_repository" / "scenarios" / "cvrp_baseline.json",
    _BACKEND_ROOT / "dsl_repository" / "scenarios" / "cvrp_infeasible.json",
    _BACKEND_ROOT / "dsl_repository" / "scenarios" / "cvrp_tight.json",
    _BACKEND_ROOT / "dsl_transformer" / "_deleted_cvrp_converter.py.bak",
    _BACKEND_ROOT / "dsl_transformer" / "_deleted_cvrp_ui_converter.py.bak",
    _BACKEND_ROOT / "register_cvrp_scenarios.py",
]


def main():
    parser = argparse.ArgumentParser(description="旧CVRP関連の残置ファイルを削除する")
    parser.add_argument("--dry-run", action="store_true", help="削除対象を表示するだけで実行しない")
    args = parser.parse_args()

    print("削除対象:")
    existing = [p for p in TARGETS if p.exists()]
    missing = [p for p in TARGETS if not p.exists()]
    for p in existing:
        print(f"  [DELETE] {p.relative_to(_PROJECT_ROOT)}")
    for p in missing:
        print(f"  [既に無し] {p.relative_to(_PROJECT_ROOT)}")

    if not existing:
        print("\n削除対象が見つかりませんでした（既に削除済みの可能性）。")
        return

    if args.dry_run:
        print("\n--dry-run 指定のため実際の削除は行いません。")
        return

    for p in existing:
        p.unlink()
        print(f"  削除完了: {p.relative_to(_PROJECT_ROOT)}")

    print(f"\n{len(existing)}件削除しました。")


if __name__ == "__main__":
    main()
