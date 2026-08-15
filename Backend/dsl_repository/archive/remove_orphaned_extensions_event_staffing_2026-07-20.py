#!/usr/bin/env python3
"""
remove_orphaned_extensions_event_staffing_2026-07-20.py

export_domain.py/import_domain.py の設計検証中に見つかった、
どのドメインからも参照されていない「真の孤立extensions」6件を削除する。

対象6件はいずれも solver_mapping.implementation が event_staffing_solver.py を
参照しているが、このファイルは既にリポジトリ上に存在しない。過去に削除された
「event_staffing」ドメインの消し忘れ（cleanup_domain.pyのdocstringが指摘する
既知の孤立行パターン）と判断し削除する。

削除前の実データは同ディレクトリの
archived_extensions_event_staffing_2026-07-20.json に保全済み。
万一必要になった場合はそのJSONから復元できる。

使い方:
  cd Backend/dsl_repository
  python3 archive/remove_orphaned_extensions_event_staffing_2026-07-20.py [--dry-run]

備考:
  この操作は開発者のローカル環境（実ファイルシステム上のoptibuddy.db）で
  実行することを想定している。リモートのサンドボックス環境からこのdbファイルへ
  直接SQLiteトランザクションを書き込もうとしたところ、マウント方式の制約により
  disk I/O errorが発生し、直接の書き換えができなかった（ジャーナルファイルの
  削除が許可されていないマウントだったため）。そのためこのスクリプトとして
  切り出し、ユーザーの手元環境で実行してもらう形にしている。
"""

import argparse
import json
import sqlite3
from pathlib import Path

ORPHAN_NAMES = [
    "flexible_machine_assignment",
    "preference_constraint",
    "leader_assignment",
    "work_hour_limit",
    "certification_requirement",
    "fatigue_constraint",
]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", default=None, help="optibuddy.dbのパス（省略時はこのファイルと同階層のdsl_repository/optibuddy.db）")
    parser.add_argument("--dry-run", action="store_true", help="削除対象を表示するだけで実行しない")
    args = parser.parse_args()

    db_path = Path(args.db) if args.db else Path(__file__).parent.parent / "optibuddy.db"
    if not db_path.exists():
        raise SystemExit(f"DBファイルが見つかりません: {db_path}")

    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row

    placeholders = ",".join("?" * len(ORPHAN_NAMES))
    rows = conn.execute(
        f"SELECT id, name, category FROM extensions WHERE name IN ({placeholders})",
        ORPHAN_NAMES,
    ).fetchall()

    print(f"DB: {db_path}")
    print(f"削除対象: {len(rows)} 件")
    for r in rows:
        print(f"  id={r['id']} name={r['name']!r} category={r['category']!r}")

    if not rows:
        print("削除対象が見つかりませんでした（既に削除済みの可能性があります）。")
        conn.close()
        return

    if args.dry_run:
        print("\n--dry-run 指定のため実際の削除は行いません。")
        conn.close()
        return

    cur = conn.execute(
        f"DELETE FROM extensions WHERE name IN ({placeholders})", ORPHAN_NAMES
    )
    conn.commit()
    print(f"\n削除完了: {cur.rowcount} 件")

    remaining = conn.execute(
        "SELECT id, name, category FROM extensions ORDER BY category, name"
    ).fetchall()
    print(f"\n残っているextensions: {len(remaining)} 件")
    for r in remaining:
        print(f"  id={r['id']} name={r['name']!r} category={r['category']!r}")

    conn.close()


if __name__ == "__main__":
    main()
