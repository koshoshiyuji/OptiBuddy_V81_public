#!/usr/bin/env python3
"""
cleanup_removed_domains_2026_07_11.py
======================================

背景:
  基底パターンをTruckDispatcher/NurseShiftの2枚看板に整理する方針により、
  以下8ドメインのコードファイル（converter/solver/ui_converter/scenarios/
  issue_rules.py内のルール等）はサンドボックス上で削除済み。
  残る作業は、これらのドメインのDBレコード（scenarios / dsl_definitions /
  dsl_evolution_log の各テーブル）を削除することだけ。

  サンドボックス環境からこのSQLite DBへの書き込みはロック制約のため
  失敗する（disk I/O error）ため、このスクリプトを実マシン上で
  直接実行してDB側の削除を完了させる。

  既存の dsl_repository/cleanup_domain.py の delete_domain_api() を
  そのまま呼び出すだけのラッパー。ロジックの重複は作らない。

削除対象ドメイン（8件）:
  GhostKitchen, ProjectPlanner, BinPacking, KubernetesScheduler,
  EventStaffing, StoreSite, HospitalShiftPlanner, ProductionLotScheduler

残すドメイン（触らない）:
  TruckDispatcher, NurseShift（2枚看板）, YardPlanning（レガシーのまま現状維持）

実行方法:
  cd /Users/arche/work/OptiBuddy_V81/Backend
  python3 dsl_repository/cleanup_removed_domains_2026_07_11.py --dry-run   # まず確認
  python3 dsl_repository/cleanup_removed_domains_2026_07_11.py --yes      # 実削除
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from dsl_repository.cleanup_domain import collect_deletion_targets, delete_domain_api  # noqa: E402

DOMAINS_TO_REMOVE = [
    "GhostKitchen",
    "ProjectPlanner",
    "BinPacking",
    "KubernetesScheduler",
    "EventStaffing",
    "StoreSite",
    "HospitalShiftPlanner",
    "ProductionLotScheduler",
]


def main():
    dry_run = "--dry-run" in sys.argv
    confirmed = "--yes" in sys.argv

    print("=" * 70)
    print("削除対象ドメイン一覧のDBレコードを確認します")
    print("=" * 70)

    any_targets = False
    for domain in DOMAINS_TO_REMOVE:
        targets = collect_deletion_targets(domain)
        total = (len(targets["scenarios"]) + len(targets["dsl_definitions"])
                 + len(targets["evolution_logs"]) + len(targets["files"]))
        if total == 0:
            print(f"  [{domain}] 該当なし（未登録、またはコードのみ存在していた）")
            continue
        any_targets = True
        print(f"  [{domain}] scenarios={len(targets['scenarios'])}, "
              f"dsl_definitions={len(targets['dsl_definitions'])}, "
              f"evolution_logs={len(targets['evolution_logs'])}, "
              f"files={len(targets['files'])}（ファイルは既に手動削除済みのはずなので通常0件）")

    if not any_targets:
        print("\n削除対象のDBレコードはありませんでした。何もせず終了します。")
        return

    if dry_run:
        print("\n--dry-run 指定のため実際の削除は行いません。")
        return

    if not confirmed:
        answer = input("\n上記8ドメインのDBレコードを削除しますか？ (y/N): ").strip().lower()
        if answer != "y":
            print("キャンセルしました。")
            return

    print("\n削除を実行します...")
    totals = {"scenarios": 0, "dsl_definitions": 0, "evolution_logs": 0}
    for domain in DOMAINS_TO_REMOVE:
        result = delete_domain_api(domain)
        totals["scenarios"] += result["scenarios"]
        totals["dsl_definitions"] += result["dsl_definitions"]
        totals["evolution_logs"] += result["evolution_logs"]
        if result["scenarios"] or result["dsl_definitions"] or result["evolution_logs"]:
            print(f"  [{domain}] scenarios={result['scenarios']}, "
                  f"dsl_definitions={result['dsl_definitions']}, "
                  f"evolution_logs={result['evolution_logs']}")

    print("\n完了。")
    print(f"合計: scenarios={totals['scenarios']}, "
          f"dsl_definitions={totals['dsl_definitions']}, "
          f"evolution_logs={totals['evolution_logs']}")
    print("フロントエンドをリロードすると、削除したドメインが一覧から消えているはずです。")


if __name__ == "__main__":
    main()
