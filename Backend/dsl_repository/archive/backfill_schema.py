"""
Backend/dsl_repository/backfill_schema.py

ワンショットスクリプト（1回限りの実行を想定。恒久的なパイプラインの一部ではない）。

背景:
  domain_generator.py の _ensure_dsl_definition() を修正し、新規ドメイン登録時に
  schema_json を実際のbaselineシナリオJSONから推定するようにした（_infer_schema_from_dsl）。
  ただしこの修正は「新規登録」にしか効かず、修正前に既に dsl_definitions に
  登録済みの行（TestDomain / TestDomainV2 / ProductionLotScheduler /
  CapacitatedVehicleRoutingProblem 等）は schema_json={} のまま残っている。

  このスクリプトは、既存の dsl_definitions のうち schema_json が空（{}）の行について、
  対応する problem_class のシナリオ（scenarios テーブル）から代表的な1件を探し、
  domain_generator._infer_schema_from_dsl() で推定したスキーマを UPDATE で書き戻す。

  1回実行すれば十分。以後は domain_generator.py 側の新規登録ロジックが
  正しい schema_json を最初から入れるので、このスクリプトを再実行する必要は基本的にない
  （再実行しても、既にschema_jsonが埋まっている行はスキップされるので安全＝冪等）。

実行方法:
  cd Backend
  conda activate optibuddy   # 環境に応じて
  python3 dsl_repository/backfill_schema.py

  --dry-run を付けると、実際には書き込まず対象一覧だけ表示する。
    python3 dsl_repository/backfill_schema.py --dry-run
"""

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))  # Backend/ を import path に追加

from dsl_repository.repository import DslRepository  # noqa: E402
from domain_generator import _infer_schema_from_dsl    # noqa: E402  既存ロジックをそのまま再利用


def _pick_sample_dsl(repo: DslRepository, problem_class: str) -> dict | None:
    """
    problem_class に一致するシナリオの中から、スキーマ推定に使う代表的な1件を選ぶ。
    「標準」を含む名前（= baseline相当）を優先し、無ければ最初の1件を使う。
    """
    scenarios = repo.list_scenarios()
    matched = [s for s in scenarios if s.get("dsl_json", {}).get("problem_class") == problem_class]
    if not matched:
        return None
    baseline_like = [s for s in matched if "標準" in s.get("name", "") or "baseline" in s.get("name", "").lower()]
    chosen = baseline_like[0] if baseline_like else matched[0]
    return chosen["dsl_json"]


def _update_schema_json(repo: DslRepository, dsl_definition_id: int, schema_json: dict) -> None:
    """
    DslRepository には schema_json だけを更新するメソッドが無いため、
    ここでは同じDB接続方式（_get_connection）を使い直接UPDATEする。
    """
    with repo._get_connection() as conn:
        conn.execute(
            "UPDATE dsl_definitions SET schema_json = ?, updated_at = CURRENT_TIMESTAMP WHERE id = ?",
            (json.dumps(schema_json, ensure_ascii=False), dsl_definition_id),
        )
        conn.commit()


def backfill(dry_run: bool = False) -> None:
    repo = DslRepository()
    definitions = repo.list_dsl_definitions()

    empty_defs = [d for d in definitions if not d.get("schema_json")]
    print(f"[backfill_schema] dsl_definitions 総数: {len(definitions)} 件 / "
          f"schema_json が空のもの: {len(empty_defs)} 件")

    if not empty_defs:
        print("[backfill_schema] 対象なし。終了します。")
        return

    updated, skipped = 0, 0
    for d in empty_defs:
        pc = d["problem_class"]
        sample_dsl = _pick_sample_dsl(repo, pc)
        if not sample_dsl:
            print(f"  - {pc} (id={d['id']}): 対応するシナリオが見つからず、スキップ")
            skipped += 1
            continue

        schema_json = _infer_schema_from_dsl(sample_dsl)
        if not schema_json:
            print(f"  - {pc} (id={d['id']}): スキーマ推定結果が空、スキップ")
            skipped += 1
            continue

        if dry_run:
            print(f"  - {pc} (id={d['id']}): 更新予定（--dry-runのため未実行）"
                  f" キー数={len(schema_json)}")
        else:
            _update_schema_json(repo, d["id"], schema_json)
            print(f"  - {pc} (id={d['id']}): 更新完了 キー数={len(schema_json)}")
        updated += 1

    print(f"[backfill_schema] 完了: 更新{'予定' if dry_run else '済み'}={updated}件, "
          f"スキップ={skipped}件")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="既存dsl_definitionsのschema_jsonを埋め直すワンショットスクリプト")
    parser.add_argument("--dry-run", action="store_true", help="実際には書き込まず対象一覧のみ表示する")
    args = parser.parse_args()
    backfill(dry_run=args.dry_run)
