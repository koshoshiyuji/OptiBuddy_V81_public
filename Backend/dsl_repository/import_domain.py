#!/usr/bin/env python3
"""
import_domain.py — domain_seeds/<domain>.json を読み込み、DBへ投入する

設計の経緯（2026-07-20、docs/DESIGN_2026-07-20_environment_distribution_strategy.md 参照）:
  - 「案A: コード全同梱 + DB選択シード」の要。ユーザー環境で新規作成された
    空DB（schema.sql適用済み）に対し、選択したドメインだけをシードする。
  - 再実行しても安全（非破壊・追記のみ）であることを重視する。ドメイン追加や
    バグ修正後の再シードで同じコマンドを何度実行しても、既存データを壊したり
    重複させたりしない設計にする。
      - dsl_definitions: (problem_class, version) が既存なら既定ではスキップ。
        スキーマ修正を配布したい場合のみ --force-update-dsl で上書きする。
      - extensions: name が既存ならスキップ（extensions.nameはUNIQUE制約あり）。
      - scenarios: (domain, name) の組が既存ならスキップ。ユーザー自身が作った
        独自シナリオ（seedに存在しない名前）には一切触れない。
  - dsl_evolution_log・domain_jobs は対象外（export_domain.py側でも扱っていない）。

使い方:
  cd Backend/dsl_repository
  python3 import_domain.py yard
  python3 import_domain.py yard truck_dispatcher --db /path/to/customer/optibuddy.db
  python3 import_domain.py yard --force-update-dsl   # スキーマの不具合修正を再配布する場合
"""

import argparse
import json
import sqlite3
from pathlib import Path


def _ensure_schema(db_path: Path) -> None:
    """DBファイルが無ければ schema.sql を適用して新規作成する。"""
    schema_path = Path(__file__).parent / "schema.sql"
    conn = sqlite3.connect(str(db_path))
    with open(schema_path, "r", encoding="utf-8") as f:
        conn.executescript(f.read())
    conn.commit()
    conn.close()


def import_domain(seed_path: Path, db_path: Path, force_update_dsl: bool = False) -> dict:
    with open(seed_path, "r", encoding="utf-8") as f:
        seed = json.load(f)

    domain_key = seed["domain_key"]
    problem_class = seed["problem_class"]

    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row

    result = {
        "domain_key": domain_key,
        "dsl_definition": "skip",
        "extensions_inserted": 0,
        "extensions_skipped": 0,
        "scenarios_inserted": 0,
        "scenarios_skipped": 0,
    }

    # --- dsl_definitions ---
    if seed["dsl_definition"] is not None:
        d = seed["dsl_definition"]
        existing = conn.execute(
            "SELECT id FROM dsl_definitions WHERE problem_class = ? AND version = ?",
            (d["problem_class"], d["version"]),
        ).fetchone()
        if existing is None:
            conn.execute(
                """
                INSERT INTO dsl_definitions (problem_class, version, extensions, schema_json, description)
                VALUES (?, ?, ?, ?, ?)
                """,
                (
                    d["problem_class"],
                    d["version"],
                    json.dumps(d["extensions"], ensure_ascii=False),
                    json.dumps(d["schema_json"], ensure_ascii=False),
                    d["description"],
                ),
            )
            result["dsl_definition"] = "inserted"
        elif force_update_dsl:
            conn.execute(
                """
                UPDATE dsl_definitions SET extensions = ?, schema_json = ?, description = ?,
                       updated_at = CURRENT_TIMESTAMP
                WHERE problem_class = ? AND version = ?
                """,
                (
                    json.dumps(d["extensions"], ensure_ascii=False),
                    json.dumps(d["schema_json"], ensure_ascii=False),
                    d["description"],
                    d["problem_class"],
                    d["version"],
                ),
            )
            result["dsl_definition"] = "updated(--force-update-dsl)"
        else:
            result["dsl_definition"] = "skip(既存あり)"

    # --- extensions（nameでスキップ判定） ---
    for e in seed["extensions"]:
        existing = conn.execute(
            "SELECT id FROM extensions WHERE name = ?", (e["name"],)
        ).fetchone()
        if existing is not None:
            result["extensions_skipped"] += 1
            continue
        conn.execute(
            """
            INSERT INTO extensions (name, category, schema_fragment, solver_mapping, ui_mapping, description)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (
                e["name"],
                e["category"],
                json.dumps(e["schema_fragment"], ensure_ascii=False),
                json.dumps(e["solver_mapping"], ensure_ascii=False) if e["solver_mapping"] else None,
                json.dumps(e["ui_mapping"], ensure_ascii=False) if e["ui_mapping"] else None,
                e["description"],
            ),
        )
        result["extensions_inserted"] += 1

    # --- scenarios（(domain, name)でスキップ判定。ユーザー独自シナリオには触れない） ---
    for s in seed["scenarios"]:
        existing = conn.execute(
            "SELECT id FROM scenarios WHERE domain = ? AND name = ?",
            (domain_key, s["name"]),
        ).fetchone()
        if existing is not None:
            result["scenarios_skipped"] += 1
            continue
        conn.execute(
            """
            INSERT INTO scenarios (name, description, tag, tag_color, domain, dsl_json, is_active)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (
                s["name"],
                s["description"],
                s["tag"],
                s["tag_color"],
                domain_key,
                json.dumps(s["dsl_json"], ensure_ascii=False),
                1 if s["is_active"] else 0,
            ),
        )
        result["scenarios_inserted"] += 1

    conn.commit()
    conn.close()
    return result


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("domain_key", nargs="+", help="ドメイン名（snake_case、複数指定可）")
    parser.add_argument("--seed-dir", default=None, help="シードJSONのディレクトリ（省略時はdomain_seeds/）")
    parser.add_argument("--db", default=None, help="投入先DBファイルパス（省略時はこのファイルと同階層のoptibuddy.db。無ければ新規作成）")
    parser.add_argument("--force-update-dsl", action="store_true", help="dsl_definitionsが既存でもschema_json/extensions/descriptionを上書きする")
    args = parser.parse_args()

    seed_dir = Path(args.seed_dir) if args.seed_dir else Path(__file__).parent / "domain_seeds"
    db_path = Path(args.db) if args.db else Path(__file__).parent / "optibuddy.db"

    if not db_path.exists():
        print(f"DBファイルが存在しないため新規作成します: {db_path}")
        _ensure_schema(db_path)

    for domain_key in args.domain_key:
        seed_path = seed_dir / f"{domain_key}.json"
        if not seed_path.exists():
            print(f"⚠️  シードファイルが見つかりません: {seed_path}（スキップ）")
            continue

        print(f"\n=== {domain_key} ===")
        result = import_domain(seed_path, db_path, force_update_dsl=args.force_update_dsl)
        print(f"  dsl_definition: {result['dsl_definition']}")
        print(f"  extensions: 追加{result['extensions_inserted']}件 / スキップ{result['extensions_skipped']}件")
        print(f"  scenarios: 追加{result['scenarios_inserted']}件 / スキップ{result['scenarios_skipped']}件")


if __name__ == "__main__":
    main()
