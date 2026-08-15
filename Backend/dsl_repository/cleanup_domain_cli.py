#!/usr/bin/env python3
"""
cleanup_domain_cli.py — CLIからドメイン削除を実行するスクリプト

使い方:
  cd /Users/arche/work/OptiBuddy_V81/Backend
  conda activate optibuddy
  python3 dsl_repository/cleanup_domain_cli.py <SnakeCaseName> [--dry-run] [--yes]

例:
  python3 dsl_repository/cleanup_domain_cli.py production_lot_scheduler2
  python3 dsl_repository/cleanup_domain_cli.py production_lot_scheduler2 --dry-run
  python3 dsl_repository/cleanup_domain_cli.py production_lot_scheduler2 --yes

オプション:
  --dry-run  : 削除対象を表示するだけで実際には削除しない
  --yes      : 確認プロンプトをスキップして即削除
"""

import sys
from pathlib import Path
from cleanup_domain import collect_deletion_targets, delete_domain_api, to_snake, to_pascal, red, yellow, green, head

# ── 引数パース ───────────────────────────────────────────────
args      = sys.argv[1:]
DRY_RUN   = "--dry-run" in args
AUTO_YES  = "--yes" in args
positional = [a for a in args if not a.startswith("--")]

if not positional:
    print("使い方: python3 dsl_repository/cleanup_domain_cli.py <SnakeCaseName> [--dry-run] [--yes]")
    sys.exit(1)

INPUT_NAME = positional[0]

# 入力が snake_case か PascalCase かどちらでも対応
if "_" in INPUT_NAME or INPUT_NAME.islower():
    SNAKE = INPUT_NAME.lower()
    PASCAL = to_pascal(SNAKE)
else:
    PASCAL = INPUT_NAME
    SNAKE  = to_snake(INPUT_NAME)

# ── パス定義 ────────────────────────────────────────────────
PROJECT_ROOT = Path(__file__).parent.parent.parent

# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# 削除対象を収集
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

print(f"\n\033[1m=== cleanup_domain: {SNAKE} (Pascal: {PASCAL}) ===\033[0m")
if DRY_RUN:
    print("  \033[33m[DRY-RUN モード: 実際には削除しません]\033[0m")

targets = collect_deletion_targets(SNAKE)

# ── DB: scenarios ────────────────────────────────────────────
head("[DB] scenarios テーブル")
if targets["scenarios"]:
    for s in targets["scenarios"]:
        red(f"scenarios[{s['id']}] domain={s['domain']}  name={s['name']}")
else:
    yellow(f"domain='{SNAKE}' のシナリオはDBに存在しません")

# ── DB: dsl_definitions ──────────────────────────────────────
head("[DB] dsl_definitions テーブル")
if targets["dsl_definitions"]:
    for d in targets["dsl_definitions"]:
        red(f"dsl_definitions[{d['id']}] {d['problem_class']} v{d['version']}")
else:
    yellow(f"problem_class='{PASCAL}' のDSL定義はDBに存在しません")

# ── DB: dsl_evolution_log ────────────────────────────────────
if targets["evolution_logs"]:
    head("[DB] dsl_evolution_log テーブル")
    for log in targets["evolution_logs"]:
        red(f"dsl_evolution_log[{log['id']}] dsl_id={log['dsl_id']}")

# ── ファイル ─────────────────────────────────────────────────
head("[FILE] 削除候補ファイル")
if targets["files"]:
    for f in targets["files"]:
        red(f)
else:
    yellow("削除対象ファイルなし")

# ── サマリ ───────────────────────────────────────────────────
head("=== 削除サマリ ===")
print(f"  scenarios レコード:     {len(targets['scenarios'])} 件")
print(f"  dsl_definitions レコード: {len(targets['dsl_definitions'])} 件")
print(f"  evolution_logs レコード:  {len(targets['evolution_logs'])} 件")
print(f"  ファイル:               {len(targets['files'])} 件")

if not targets["scenarios"] and not targets["dsl_definitions"] and not targets["files"]:
    print("\n削除対象なし。終了します。")
    sys.exit(0)

if DRY_RUN:
    print("\n[DRY-RUN] 上記が削除対象です。実際に削除するには --dry-run を外してください。")
    sys.exit(0)

# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# 確認 & 実行
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

if not AUTO_YES:
    ans = input("\n上記を全て削除しますか？ (yes/no): ").strip().lower()
    if ans != "yes":
        print("キャンセルしました。")
        sys.exit(0)

head("=== 削除実行 ===")

result = delete_domain_api(SNAKE)

green(f"scenarios {result['scenarios']} 件削除")
green(f"dsl_definitions {result['dsl_definitions']} 件削除")
green(f"dsl_evolution_log {result['evolution_logs']} 件削除")
for f in result["files"]:
    green(f)

head("=== 完了 ===")
print(f"  ドメイン '{SNAKE}' の削除が完了しました。")
print("  Backendを再起動してください。")
