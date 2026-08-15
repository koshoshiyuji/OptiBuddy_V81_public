#!/usr/bin/env python3
"""
update_convdata_scenario.py

シナリオ id=101「Truck Dispatcher Convdata」のdsl_jsonを、
dsl_repository/scenarios/truck_dispatcher_convdata.json の最新内容（
マイクロカット除外・soft_tw_penalty_per_min引き上げ後）で上書きする。

実行前にバックエンド(app.py)を停止しておくこと（SQLiteの書き込み競合を避けるため）。

使い方:
  cd Backend && python3 update_convdata_scenario.py
"""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from dsl_repository.repository import DslRepository

SCENARIO_ID = 101
JSON_PATH = Path(__file__).parent / "dsl_repository/scenarios/truck_dispatcher_convdata.json"

repo = DslRepository()

with open(JSON_PATH, encoding="utf-8") as f:
    dsl_json = json.load(f)

before = repo.get_scenario(SCENARIO_ID)
if before is None:
    print(f"エラー: id={SCENARIO_ID} のシナリオが見つかりません。")
    sys.exit(1)

print(f"更新前: {before['name']}  updated_at={before['updated_at']}")

ok = repo.update_scenario(SCENARIO_ID, dsl_json=dsl_json)
print("update_scenario ->", ok)

after = repo.get_scenario(SCENARIO_ID)
cust_ids = [c["id"] for c in after["dsl_json"]["customers"] if not c.get("_excluded")]
print(f"更新後: updated_at={after['updated_at']}")
print(f"  有効な顧客数: {len(cust_ids)}件  (c832787除外されているか: {'c832787' not in cust_ids})")
print(f"  soft_tw_penalty_per_min: {after['dsl_json']['config']['soft_tw_penalty_per_min']}")
