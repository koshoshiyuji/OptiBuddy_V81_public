"""
apply_chief_fix_to_existing_scenarios.py

2026-07-13の責任者(CHIEF)不在バグ対応の一環。solvers/nurse_shift_weekly_cap_solver.py の
`if chief_vars:` ガードを修正した（CHIEF候補が0件の場合、以前は制約を無言でスキップして
いたが、修正後は明示的にinfeasible化するようにした）。

この修正だけを既存の標準/タイト/解なし（scenarios/nurse_shift_weekly_cap_{baseline,tight,
infeasible}.json、DB id 135/136/137）に適用すると、この3シナリオはいずれも
grade="CHIEF"の候補が0名だったため、min_chiefs>=1の全タスクで確実にinfeasibleになって
しまう（＝3シナリオとも壊れる）。

そのため、scenarios/ 配下の3つのJSONファイルは既に以下の内容に修正済み:
  - 各シナリオのSENIOR職員（先導的な立場のスタッフ）をgrade="CHIEF"に昇格
  - 責任者(CHIEF)要件は深夜勤のみ必須（日勤・準夜勤は min_chiefs=0）に変更
    （デモ用に追加した2シナリオ「責任者体制OK版」「責任者不足（解なし）版」と
    同じ設計方針。日勤・準夜勤にまで責任者必須は過剰という判断）

このスクリプトは、上記で更新済みのJSONファイルの内容を、DB上の既存レコード
（id 135 標準 / 136 タイト / 137 解なし）に反映する（dsl_json列を更新するのみ。
id・tag・domainは変更しない）。

実行方法（Koshoshiさんの実機で）:
  cd /Users/arche/work/OptiBuddy_V81/Backend
  python3 dsl_repository/apply_chief_fix_to_existing_scenarios.py
"""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

from repository import DslRepository  # noqa: E402

SCEN_DIR = Path(__file__).parent / "scenarios"

# (DB上のid, JSONファイルsuffix, 追記する注記)
TARGETS = [
    (135, "baseline", "標準"),
    (136, "tight", "タイト"),
    (137, "infeasible", "解なし"),
]

NOTE = (
    "\n\n[2026-07-13追記] 責任者(CHIEF)不在バグの修正: 以前はCHIEF grade該当者が"
    "0名だったため責任者要件が全タスクで無言で無効化されていた。SENIOR職員をCHIEFに"
    "昇格し、責任者要件は深夜勤のみ必須（日勤・準夜勤は不要）に変更した。"
    "併せてsolver.py側のガードも修正し、CHIEF候補が0件の要件は無視せず"
    "明示的にinfeasible化するようにした。"
)


def main():
    repo = DslRepository()

    for scenario_id, suffix, label in TARGETS:
        path = SCEN_DIR / f"nurse_shift_weekly_cap_{suffix}.json"
        dsl_json = json.loads(path.read_text(encoding="utf-8"))

        existing = repo.get_scenario(scenario_id)
        if existing is None:
            print(f"[警告] id={scenario_id} ({label}) がDBに見つかりません。スキップ。")
            continue

        base_desc = existing.get("description") or dsl_json.get("description", "")
        # 二重追記を避ける
        if "2026-07-13追記" not in base_desc:
            new_desc = base_desc + NOTE
        else:
            new_desc = base_desc

        ok = repo.update_scenario(
            scenario_id,
            dsl_json=dsl_json,
            description=new_desc,
        )
        print(f"[{'更新完了' if ok else '更新失敗'}] id={scenario_id} ({label})")

    print("\n--- 更新後の nurse_shift_weekly_cap シナリオ一覧 ---")
    for r in repo.list_scenarios(domain="nurse_shift_weekly_cap"):
        print(f"  id={r['id']}: {r['name']}")


if __name__ == "__main__":
    main()
