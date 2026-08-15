"""
Backend/dsl_repository/register_merge_test_scenario.py

ワンショットスクリプト。TruckDispatcherのマージ判定（Savingsアルゴリズムの
ルート統合ロジック）を検証するための小規模テストシナリオ
（truck_dispatcher_merge_test.json）をDBに登録する。

このシナリオは以下を検証する目的で設計されている:
  - s01→s02→s03、s04→s05→s06 という2つの3件近接クラスターが、
    正しく1台の車両にまとめられるか（ルート統合が正しく機能しているか）
  - 4台の車両で8件全てを割り当てられるか
    （統合が機能しなければ8件に4台では足りず、必ず未割り当てが出る設計）

実行方法:
  cd Backend
  conda activate optibuddy
  python3 dsl_repository/register_merge_test_scenario.py
"""

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from dsl_repository.repository import DslRepository  # noqa: E402

SCENARIO_NAME = "TruckDispatcher 検証用小規模シナリオ（マージ判定検証）"
JSON_PATH = Path(__file__).parent / "scenarios" / "truck_dispatcher_merge_test.json"


def register():
    repo = DslRepository()
    existing = [s for s in repo.list_scenarios() if s["name"] == SCENARIO_NAME]
    dsl_json = json.loads(JSON_PATH.read_text(encoding="utf-8"))

    if existing:
        sid = existing[0]["id"]
        repo.update_scenario(sid, dsl_json=dsl_json)
        print(f"[register_merge_test] 既存シナリオを更新しました: ID={sid}")
        return

    sid = repo.create_scenario(
        name=SCENARIO_NAME,
        description="マージ判定（ルート統合ロジック）の検証用。クラスター内3件が1ルートに統合されるかを確認する。",
        tag="MERGET",
        tag_color="#22c55e",
        domain="truck_dispatcher",
        dsl_json=dsl_json,
    )
    print(f"[register_merge_test] 新規登録しました: ID={sid}")


if __name__ == "__main__":
    register()
