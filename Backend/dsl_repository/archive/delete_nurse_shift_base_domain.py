"""
delete_nurse_shift_base_domain.py

NurseShift（週次夜勤上限なしの旧ベース版、problem_class="NurseShift" / domain="nurse_shift"）
を完全削除する。NurseShiftWeeklyCap（rolling window付き）が内容的に完全上位互換のため、
2026-07-11時点の「TruckDispatcher/NurseShiftの2枚看板」方針を「TruckDispatcher/
NurseShiftWeeklyCapの2枚看板」に更新する一環。

【注意】既存の dsl_repository/cleanup_domain.py はドメイン名の一致判定に
`LIKE '%snake%'` / `LIKE '%Pascal%'`（部分一致）を使っている。"nurse_shift" で実行すると
"nurse_shift_weekly_cap"（scenarios.domain）や"NurseShiftWeeklyCap"（dsl_definitions.
problem_class）も部分一致してしまい、残すべきNurseShiftWeeklyCap側まで巻き込んで
削除される（scenarios/dsl_definitionsのDB行、および
`scenarios_dir.glob("nurse_shift_*.json")` で weekly_cap のシナリオJSONファイルも
誤って削除対象になる）。そのため今回は cleanup_domain.py を使わず、
"nurse_shift"に完全一致する行・ファイルだけを明示的に削除するこの専用スクリプトを使う。

削除対象:
  [DB] scenarios: domain = 'nurse_shift' （完全一致。id 121 標準 / 122 タイトを含む）
  [DB] dsl_definitions: problem_class = 'NurseShift' （完全一致。'NurseShiftWeeklyCap'は対象外）
  [DB] dsl_evolution_log: 上記dsl_definitions.idに紐づく行
  [FILE] Backend/solvers/nurse_shift_solver.py
  [FILE] Backend/dsl_transformer/nurse_shift_converter.py
  [FILE] Backend/dsl_transformer/nurse_shift_ui_converter.py
  [FILE] Backend/dsl_repository/scenarios/nurse_shift_baseline.json
  [FILE] Backend/dsl_repository/scenarios/nurse_shift_tight.json
  （nurse_shift_weekly_cap_*.json / nurse_shift_weekly_cap_*.py は一切対象外）

実行方法（Koshoshiさんの実機で）:
  cd /Users/arche/work/OptiBuddy_V81/Backend
  python3 dsl_repository/delete_nurse_shift_base_domain.py --dry-run   # まず確認
  python3 dsl_repository/delete_nurse_shift_base_domain.py --yes       # 実削除
"""
import sqlite3
import sys
from pathlib import Path

BACKEND_ROOT = Path(__file__).parent.parent
PROJECT_ROOT = BACKEND_ROOT.parent
DB_PATH = Path(__file__).parent / "optibuddy.db"

TARGET_FILES = [
    BACKEND_ROOT / "solvers" / "nurse_shift_solver.py",
    BACKEND_ROOT / "dsl_transformer" / "nurse_shift_converter.py",
    BACKEND_ROOT / "dsl_transformer" / "nurse_shift_ui_converter.py",
    Path(__file__).parent / "scenarios" / "nurse_shift_baseline.json",
    Path(__file__).parent / "scenarios" / "nurse_shift_tight.json",
]


def collect():
    conn = sqlite3.connect(str(DB_PATH))
    conn.row_factory = sqlite3.Row
    scenarios = conn.execute(
        "SELECT id, name FROM scenarios WHERE domain = 'nurse_shift'"
    ).fetchall()
    dsl_defs = conn.execute(
        "SELECT id, problem_class, version FROM dsl_definitions WHERE problem_class = 'NurseShift'"
    ).fetchall()
    dsl_ids = [d["id"] for d in dsl_defs]
    evo_logs = []
    if dsl_ids:
        placeholders = ",".join("?" * len(dsl_ids))
        evo_logs = conn.execute(
            f"SELECT id, dsl_id FROM dsl_evolution_log WHERE dsl_id IN ({placeholders})",
            dsl_ids,
        ).fetchall()
    conn.close()
    files = [f for f in TARGET_FILES if f.exists()]
    return scenarios, dsl_defs, evo_logs, files


def main():
    dry_run = "--dry-run" in sys.argv
    confirmed = "--yes" in sys.argv

    scenarios, dsl_defs, evo_logs, files = collect()

    print("=" * 70)
    print("削除対象（NurseShift 完全一致のみ。NurseShiftWeeklyCapは対象外）")
    print("=" * 70)
    for s in scenarios:
        print(f"  [DB scenarios] id={s['id']}: {s['name']}")
    for d in dsl_defs:
        print(f"  [DB dsl_definitions] id={d['id']}: {d['problem_class']} v{d['version']}")
    print(f"  [DB dsl_evolution_log] {len(evo_logs)} 件")
    for f in files:
        print(f"  [FILE] {f.relative_to(PROJECT_ROOT)}")

    total = len(scenarios) + len(dsl_defs) + len(evo_logs) + len(files)
    if total == 0:
        print("\n削除対象が見つかりませんでした。")
        return

    if dry_run:
        print("\n--dry-run のため実際には削除しません。")
        return

    if not confirmed:
        answer = input("\n本当に削除しますか？ (y/N): ").strip().lower()
        if answer != "y":
            print("キャンセルしました。")
            return

    conn = sqlite3.connect(str(DB_PATH))
    cur = conn.cursor()
    try:
        if scenarios:
            ids = [s["id"] for s in scenarios]
            placeholders = ",".join("?" * len(ids))
            cur.execute(f"DELETE FROM scenarios WHERE id IN ({placeholders})", ids)
        if dsl_defs:
            dsl_ids = [d["id"] for d in dsl_defs]
            placeholders = ",".join("?" * len(dsl_ids))
            cur.execute(f"DELETE FROM dsl_evolution_log WHERE dsl_id IN ({placeholders})", dsl_ids)
            cur.execute(f"DELETE FROM dsl_definitions WHERE id IN ({placeholders})", dsl_ids)
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()

    deleted_files = []
    for f in files:
        try:
            f.unlink()
            deleted_files.append(str(f.relative_to(PROJECT_ROOT)))
        except Exception as e:
            print(f"  [警告] ファイル削除失敗: {f} ({e})")

    print("\n完了。")
    print(f"scenarios={len(scenarios)}, dsl_definitions={len(dsl_defs)}, "
          f"evolution_logs={len(evo_logs)}, files={len(deleted_files)}")
    print("フロントエンドをリロードすると、NurseShiftが一覧から消えているはずです。"
          " NurseShiftWeeklyCapには影響ありません。")


if __name__ == "__main__":
    main()
