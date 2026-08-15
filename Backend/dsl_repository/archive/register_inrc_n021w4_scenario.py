"""
Backend/dsl_repository/register_inrc_n021w4_scenario.py

ワンショットスクリプト。INRC-II 公式データセット n021w4（Sc-n021w4.txt +
WD-n021w4-0.txt、inrc_to_business_dsl_nurse_shift_weekly_cap.py で変換済み）を
NurseShiftWeeklyCap のシナリオとしてDBに登録する。

出典: scenarios/inrc_source/n021w4/（Sc-n021w4.txt, WD-n021w4-0.txt ほか）
変換レポート: scenarios/nurse_shift_weekly_cap_inrc_n021w4_report.json
  （変換しなかった制約の一覧。CONTRACTS総勤務数上限・夜勤以外のシフト遷移禁止・
  ソフトなSHIFT_OFF_REQUESTS等。詳細はレポート参照）

注意: このスクリプトはGate 2（静的フィールド突き合わせ・動的ソルブ検証）を
実行しない。登録前にfeasibleかどうかは別途（例: run_gate2_nurse_shift_weekly_cap.py
相当の手動ソルブ）で確認すること。

実行方法:
  cd /Users/arche/work/OptiBuddy_V81/Backend
  conda activate optibuddy
  python3 dsl_repository/register_inrc_n021w4_scenario.py
"""

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from dsl_repository.repository import DslRepository  # noqa: E402

SCENARIO_NAME = "NurseShiftWeeklyCap — INRC-II n021w4"
JSON_PATH = Path(__file__).parent / "scenarios" / "nurse_shift_weekly_cap_inrc_n021w4.json"


def register():
    repo = DslRepository()
    existing = [s for s in repo.list_scenarios() if s["name"] == SCENARIO_NAME]
    dsl_json = json.loads(JSON_PATH.read_text(encoding="utf-8"))

    if existing:
        sid = existing[0]["id"]
        repo.update_scenario(sid, dsl_json=dsl_json)
        print(f"[register_inrc_n021w4] 既存シナリオを更新しました: ID={sid}")
        return

    sid = repo.create_scenario(
        name=SCENARIO_NAME,
        description=(
            "INRC-II公式データセット n021w4（21名・1週間分, Sc-n021w4.txt + "
            "WD-n021w4-0.txt）をinrc_to_business_dsl_nurse_shift_weekly_cap.pyで変換。"
            "Gate 2回帰テスト用の外部ベンチマークインスタンス。"
            "変換しなかった制約はnurse_shift_weekly_cap_inrc_n021w4_report.json参照。"
        ),
        tag="HOSPITAL",
        tag_color="#8b5cf6",
        domain="nurse_shift_weekly_cap",
        dsl_json=dsl_json,
    )
    print(f"[register_inrc_n021w4] 新規登録しました: ID={sid}")


if __name__ == "__main__":
    register()
