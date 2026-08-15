"""
Backend/dsl_repository/migrate_2026_07_27_extension_applicable_domains.py

2026-07-27追加。one-offマイグレーションスクリプト。

背景: extensions.category列が「種類ラベル」（physical_space等5件:
"constraint"/"resource"）と「ドメイン名」（rolling_weekly_night_shift_cap /
weekly_night_count_ui_column の2件: "NurseShiftWeeklyCap"）の2通りの意味で
混在していた。domain_generator.detect_extension_gaps()はcategory=base_domain
で既存extension候補を検索するため、種類ラベル式の5件はどのドメインからも
検索にヒットしない不整合になっていた（Koshoshiとの会話で発覚、2026-07-27）。

また、rolling_weekly_night_shift_cap / weekly_night_count_ui_columnの2件は
extensionsテーブルには存在するのに、NurseShiftWeeklyCapのdsl_definitions.extensions
列（実際にそのドメインへ適用されているextension名の一覧）には紐付いていなかった
（実装自体はnurse_shift_weekly_cap_solver.py本体に直接書き込まれている）。

このスクリプトが行うこと（すべて冪等、複数回実行しても安全）:
  1. extensions テーブルに applicable_domains（JSON配列）列を追加する
     （無ければ追加、schema.sqlにも同じ列を追加済み＝新規DBには最初から入る）。
  2. 既存7件について、実際にdsl_definitions.extensionsで参照されているドメインを
     逆引きしてapplicable_domainsをbackfillする（実態に基づく、推測では埋めない）。
  3. rolling_weekly_night_shift_cap / weekly_night_count_ui_column の category を
     ドメイン名("NurseShiftWeeklyCap")から種類ラベル（"constraint"/"ui"）に修正する。
  4. NurseShiftWeeklyCapのdsl_definitions.extensions列に、実際に実装済みの
     この2件を追記する（実態に合わせる）。

実行方法:
    cd Backend/dsl_repository
    python3 migrate_2026_07_27_extension_applicable_domains.py
"""

import json
import sqlite3
from pathlib import Path

DB_PATH = Path(__file__).parent / "optibuddy.db"

# 2026-07-27時点でNurseShiftWeeklyCapのsolver.pyに実装済みと確認済みの2件
# （nurse_shift_weekly_cap_solver.pyのwork_limits.max_night_shifts_per_rolling_7days
#  読み込み箇所、およびUI側の週次夜勤回数列表示に対応）。
_CATEGORY_FIXES = {
    "rolling_weekly_night_shift_cap": "constraint",
    "weekly_night_count_ui_column":   "ui",
}


def main():
    conn = sqlite3.connect(str(DB_PATH))
    conn.row_factory = sqlite3.Row
    cur = conn.cursor()

    # --- 1. 列追加（冪等） ---
    cur.execute("PRAGMA table_info(extensions)")
    existing_cols = {row["name"] for row in cur.fetchall()}
    if "applicable_domains" not in existing_cols:
        cur.execute("ALTER TABLE extensions ADD COLUMN applicable_domains JSON")
        print("[migrate] extensions.applicable_domains 列を追加しました")
    else:
        print("[migrate] extensions.applicable_domains 列は既に存在します（スキップ）")

    # --- 2. category修正（冪等） ---
    for name, new_category in _CATEGORY_FIXES.items():
        cur.execute("SELECT category FROM extensions WHERE name = ?", (name,))
        row = cur.fetchone()
        if row is None:
            print(f"[migrate] 警告: extension '{name}' が見つかりません（スキップ）")
            continue
        if row["category"] != new_category:
            cur.execute(
                "UPDATE extensions SET category = ?, updated_at = CURRENT_TIMESTAMP WHERE name = ?",
                (new_category, name),
            )
            print(f"[migrate] {name}: category '{row['category']}' → '{new_category}'")
        else:
            print(f"[migrate] {name}: category は既に '{new_category}'（スキップ）")

    # --- 3. NurseShiftWeeklyCapのdsl_definitions.extensionsを実態に合わせる（冪等） ---
    cur.execute(
        "SELECT id, extensions FROM dsl_definitions WHERE problem_class = 'NurseShiftWeeklyCap'"
    )
    row = cur.fetchone()
    if row is None:
        print("[migrate] 警告: NurseShiftWeeklyCapのdsl_definitions行が見つかりません（スキップ）")
    else:
        current = json.loads(row["extensions"])
        merged = sorted(set(current) | set(_CATEGORY_FIXES.keys()))
        if merged != current:
            cur.execute(
                "UPDATE dsl_definitions SET extensions = ?, updated_at = CURRENT_TIMESTAMP WHERE id = ?",
                (json.dumps(merged), row["id"]),
            )
            print(f"[migrate] NurseShiftWeeklyCap.extensions: {current} → {merged}")
        else:
            print(f"[migrate] NurseShiftWeeklyCap.extensions は既に実態と一致（スキップ）: {current}")

    conn.commit()

    # --- 4. applicable_domainsのbackfill（dsl_definitions.extensionsからの逆引き、実態ベース） ---
    cur.execute("SELECT problem_class, extensions FROM dsl_definitions")
    usage_by_extension = {}
    for r in cur.fetchall():
        for ext_name in json.loads(r["extensions"]):
            usage_by_extension.setdefault(ext_name, set()).add(r["problem_class"])

    cur.execute("SELECT id, name, applicable_domains FROM extensions")
    for row in cur.fetchall():
        domains = sorted(usage_by_extension.get(row["name"], set()))
        current_json = row["applicable_domains"]
        current = json.loads(current_json) if current_json else []
        if not domains:
            print(f"[migrate] 警告: extension '{row['name']}' はどのdsl_definitions.extensionsからも"
                  f"参照されていません。applicable_domainsは空のままにします（要手動確認）")
            continue
        if sorted(current) != domains:
            cur.execute(
                "UPDATE extensions SET applicable_domains = ?, updated_at = CURRENT_TIMESTAMP WHERE id = ?",
                (json.dumps(domains), row["id"]),
            )
            print(f"[migrate] {row['name']}: applicable_domains {current} → {domains}")
        else:
            print(f"[migrate] {row['name']}: applicable_domains は既に実態と一致（スキップ）: {domains}")

    conn.commit()
    conn.close()
    print("[migrate] 完了")


if __name__ == "__main__":
    main()
