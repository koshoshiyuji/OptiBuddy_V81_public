"""
Backend/dsl_repository/delete_old_cvrp.py

ワンショットスクリプト。旧CapacitatedVehicleRoutingProblem（CP Optimizer非準拠の
旧ヒューリスティック実装）を完全削除する。

前提:
  - domain_generator.py の BASE_PROBLEM_MAP / EXISTING_DOMAINS / FOUR_DSL_DOMAINS は
    既に TruckDispatcher（CP Optimizer化された新実装）を指すよう更新済み。
  - 今後の新規CVRP系ヒアリングはすべてTruckDispatcherに分類される。
  - 旧実装は動作確認（TruckDispatcherが実際に動くこと）が済んだため、削除してよい
    という判断をKoshoshiから得た。

削除対象（cleanup_domain.py の既存機能を再利用。app.py等の共有ファイルへの
テキストパッチは一切行わない安全な設計）:
  - DB: scenarios（domain LIKE 'capacitated_vehicle_routing_problem'）
  - DB: dsl_definitions（problem_class LIKE 'CapacitatedVehicleRoutingProblem'）
  - DB: dsl_evolution_log（上記dsl_idに紐づくもの）
  - ファイル: capacitated_vehicle_routing_problem_solver.py
  - ファイル: cvrp_converter.py / cvrp_ui_converter.py
    （※ファイル名がsnake_caseの規約と一致しないため、このツールの自動検出では
       ヒットしない可能性がある。ヒットしなければ別途手動確認する）
  - ファイル: {snake}_*.json のシナリオファイル

削除しないもの（cleanup_domain.pyの対象外。別途手動で確認・除去する）:
  - Backend/app.py の _DSL4_SOLVERS 登録・_solve_capacitated_vehicle_routing_problem()
  - Backend/solvers/registry.py の _DSL4_DISPLAY_ONLY 登録
  - Frontend側のView/ルーティング

実行方法:
  cd Backend
  conda activate optibuddy
  python3 dsl_repository/delete_old_cvrp.py --dry-run   # まずプレビュー
  python3 dsl_repository/delete_old_cvrp.py             # 実際に削除
"""

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
sys.path.insert(0, str(Path(__file__).parent.parent))

from cleanup_domain import collect_deletion_targets, delete_domain_api, _print_targets, head, green, cyan  # noqa: E402

DOMAIN_NAME = "CapacitatedVehicleRoutingProblem"


def main():
    parser = argparse.ArgumentParser(description="旧CapacitatedVehicleRoutingProblemの完全削除")
    parser.add_argument("--dry-run", action="store_true", help="削除対象を表示するだけで実行しない")
    args = parser.parse_args()

    head(f"ドメイン削除プレビュー: {DOMAIN_NAME}")
    targets = collect_deletion_targets(DOMAIN_NAME)
    _print_targets(targets)

    total = (len(targets["scenarios"]) + len(targets["dsl_definitions"])
             + len(targets["evolution_logs"]) + len(targets["files"]))

    if total == 0:
        cyan("削除対象が見つかりませんでした。")
        return

    if args.dry_run:
        cyan("--dry-run 指定のため実際の削除は行いません。")
        return

    result = delete_domain_api(DOMAIN_NAME)
    head("削除完了")
    green(f"scenarios: {result['scenarios']} 件")
    green(f"dsl_definitions: {result['dsl_definitions']} 件")
    green(f"evolution_logs: {result['evolution_logs']} 件")
    for f in result["files"]:
        green(f"file: {f}")
    if not result["files"]:
        cyan("cleanup_domain.py の自動検出では削除されたファイルはありませんでした"
             "（cvrp_converter.py 等、命名規約に一致しないファイルは手動確認が必要です）。")


if __name__ == "__main__":
    main()
