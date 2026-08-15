"""
consolidate_nurse_and_truck_scenarios.py

デモ用シナリオの整理。看護師シフト(NurseShiftWeeklyCap)とTruckDispatcherを、
それぞれ「標準（Feasible）」「解なし（Infeasible）」の2本だけが有効な状態に揃える
（論理削除のみ。is_active=0にするだけでレコードは残るため復元可能）。

--- NurseShiftWeeklyCap（現在4本アクティブ→2本に） ---
  残す: id 135 標準（CHIEF修正済み、Feasible）
        id 139 責任者不足（解なし）（今回追加したCHIEF不足デモ、Infeasible）
  非活性化: id 137 元の解なし（夜勤回数上限のキャパシティ不足が理由。139と役割が
            重複するため） / id 138 責任者体制OK版（135がCHIEF修正済みで同じ
            役割になったため重複）

--- TruckDispatcher（現在 96/97/98/105 がアクティブ→2本に） ---
  残す: id 96 標準 / id 98 解なし
  非活性化: id 97 タイト / id 105 Truck Dispatcher Convdata（標準/解なし以外の
            アクティブシナリオ）

実行方法（Koshoshiさんの実機で）:
  cd /Users/arche/work/OptiBuddy_V81/Backend
  python3 dsl_repository/consolidate_nurse_and_truck_scenarios.py
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

from repository import DslRepository  # noqa: E402

RETIRE_IDS = [137, 138, 97, 105]


def main():
    repo = DslRepository()

    for scenario_id in RETIRE_IDS:
        existing = repo.get_scenario(scenario_id)
        if existing is None:
            print(f"[警告] id={scenario_id} が見つかりません。スキップ。")
            continue
        if not existing.get("is_active"):
            print(f"[スキップ] id={scenario_id} ({existing['name']}) は既に非活性です。")
            continue
        ok = repo.delete_scenario(scenario_id)
        print(f"[{'非活性化完了' if ok else '失敗'}] id={scenario_id}: {existing['name']}")

    print("\n--- 整理後のアクティブシナリオ ---")
    for domain in ("nurse_shift_weekly_cap", "nurse_shift", "truck_dispatcher"):
        for r in repo.list_scenarios(domain=domain):
            print(f"  [{domain}] id={r['id']}: {r['name']}")


if __name__ == "__main__":
    main()
