"""
Backend/dsl_repository/register_merge_test2_tight_tw.py

ワンショットスクリプト。検証2（タイムウィンドウ厳格化）シナリオを登録する。
検証1（truck_dispatcher_merge_test.json）は緩いTWで2台・8件全件成功済み。
本シナリオは同一構成でTWだけ厳しくし、未割り当てが出るか確認する。

実行方法:
  cd Backend
  conda activate optibuddy
  python3 dsl_repository/register_merge_test2_tight_tw.py
"""

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from dsl_repository.repository import DslRepository  # noqa: E402

SCENARIO_NAME = "TruckDispatcher 検証用小規模シナリオ2（タイムウィンドウ厳格化検証）"
JSON_PATH = Path(__file__).parent / "scenarios" / "truck_dispatcher_merge_test2_tight_tw.json"


def register():
    repo = DslRepository()
    existing = [s for s in repo.list_scenarios() if s["name"] == SCENARIO_NAME]
    dsl_json = json.loads(JSON_PATH.read_text(encoding="utf-8"))

    if existing:
        sid = existing[0]["id"]
        repo.update_scenario(sid, dsl_json=dsl_json)
        print(f"[register_merge_test2] 既存シナリオを更新しました: ID={sid}")
        return

    sid = repo.create_scenario(
        name=SCENARIO_NAME,
        description="タイムウィンドウ厳格化によるルート統合機会の喪失を検証する。検証1（緩TW）は全件成功済み。",
        tag="MERGET2",
        tag_color="#f59e0b",
        domain="truck_dispatcher",
        dsl_json=dsl_json,
    )
    print(f"[register_merge_test2] 新規登録しました: ID={sid}")


if __name__ == "__main__":
    register()
