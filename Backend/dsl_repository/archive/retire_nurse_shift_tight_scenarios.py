"""
retire_nurse_shift_tight_scenarios.py

看護師シフト関連の「タイト」シナリオ（NurseShift / NurseShiftWeeklyCap 双方）を
非活性化する（論理削除。is_active=0。DBレコード自体は残るので復元も可能）。

対象:
  id 122: 病院看護師シフト自動作成 — タイト  (domain: nurse_shift)
  id 136: 看護師シフト最適化（週単位夜勤回数上限拡張） — タイト  (domain: nurse_shift_weekly_cap)

実行方法（Koshoshiさんの実機で）:
  cd /Users/arche/work/OptiBuddy_V81/Backend
  python3 dsl_repository/retire_nurse_shift_tight_scenarios.py
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

from repository import DslRepository  # noqa: E402

TARGET_IDS = [122, 136]


def main():
    repo = DslRepository()

    for scenario_id in TARGET_IDS:
        existing = repo.get_scenario(scenario_id)
        if existing is None:
            print(f"[警告] id={scenario_id} が見つかりません。スキップ。")
            continue
        if not existing.get("is_active"):
            print(f"[スキップ] id={scenario_id} ({existing['name']}) は既に非活性です。")
            continue
        ok = repo.delete_scenario(scenario_id)
        print(f"[{'非活性化完了' if ok else '失敗'}] id={scenario_id}: {existing['name']}")

    print("\n--- nurse_shift / nurse_shift_weekly_cap 現在のアクティブシナリオ ---")
    for domain in ("nurse_shift", "nurse_shift_weekly_cap"):
        for r in repo.list_scenarios(domain=domain):
            print(f"  [{domain}] id={r['id']}: {r['name']}")


if __name__ == "__main__":
    main()
