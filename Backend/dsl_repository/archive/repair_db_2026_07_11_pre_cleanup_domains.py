#!/usr/bin/env python3
"""
OptiBuddy DB 整合性チェック & 修復スクリプト (旧 dsl_repository/repair_db.py)
==============================================================

【2026-07-27 アーカイブ化】
このファイルはdsl_repository/直下から本フォルダへ退避した。archive/README.md
（2026-07-20）は本ファイルを「現役の開発・保守ツール」として案内していたが、
中身を精査したところSCENARIO_CATALOG/DSL_CATALOG/EXTENSION_CATALOG/
SOLVER_CHECKが参照するドメイン（hospital_shift_planner・
production_lot_scheduler・kubernetes_scheduler系・micro_service_dispatcher・
servers_dispatch・vehicle_routing_problem1）は、いずれも現行7ドメイン
（CarSequencing/LineChangeoverScheduler/MeetingRoom/NurseShiftWeeklyCap/
StoreSite/TruckDispatcher/YardPlanning）に含まれていなかった
（hospital_shift_plannerは"EventStaffingベースの派生ドメイン"と明記されており、
EventStaffing自体が2026-07-11に削除済み）。「現役ツール」の体裁のまま
--auto-fixを実行すると、既に意図的に削除されたはずのこれらの旧ドメインが
scenarios/dsl_definitions/extensionsテーブルに復活してしまう実害があるため、
他のarchive/内スクリプトと同じ「役目を終えた一回限りの作業」として退避した
（Koshoshiとの会話で発覚）。

現行7ドメインを対象にした同種の整合性チェックツールが今後必要になった場合は、
本ファイルのC1〜C5チェックの枠組み自体は参考になるが、カタログ内容は
作り直しが必要。

以下は退避前の元docstring。
------------------------------------------------------------------------------
登録状況の不整合を検出し、対話的に修復する。

実行方法:
  cd /Users/arche/work/OptiBuddy_V81/Backend
  conda activate optibuddy
  python3 dsl_repository/repair_db.py [--check-only] [--auto-fix]

オプション:
  --check-only : 不整合の検出のみ（修復しない）
  --auto-fix   : 対話なしで全修復を自動実行

整合性チェック項目:
  [C1] scenarios テーブル ↔ シナリオJSONファイルの対応
  [C2] dsl_definitions テーブル ↔ scenarios の problem_class の対応
  [C3] extensions テーブル ↔ dsl_definitions.extensions の登録漏れ
  [C4] ソルバーなし新規ドメインの識別（実行不可シナリオの警告）
  [C5] JSONファイルあるがDBに未登録のシナリオの検出

修復内容（--auto-fix または対話確認後に実行）:
  - hospital_shift_planner: scenarios/dsl_definitions/extensions を登録
  - production_lot_scheduler: scenarios を登録（ソルバーなし警告付き）
  - kubernetes_scheduler系: scenarios を登録（ソルバーImportError警告付き）
"""

import atexit
import json
import sqlite3
import sys
from pathlib import Path

DB_PATH       = Path(__file__).parent / "optibuddy.db"
SCENARIOS_DIR = Path(__file__).parent / "scenarios"
SOLVERS_DIR   = Path(__file__).parent.parent / "solvers"

CHECK_ONLY = "--check-only" in sys.argv
AUTO_FIX   = "--auto-fix"   in sys.argv

# ── カラー出力 ────────────────────────────────────────────────
def ok(msg):    print(f"  \033[32m✅ OK\033[0m    {msg}")
def warn(msg):  print(f"  \033[33m⚠️  WARN\033[0m  {msg}")
def error(msg): print(f"  \033[31m❌ ERROR\033[0m {msg}")
def info(msg):  print(f"  \033[36mℹ️  INFO\033[0m  {msg}")
def head(msg):  print(f"\n\033[1m{msg}\033[0m")


# ── DB接続 ───────────────────────────────────────────────────
conn = sqlite3.connect(str(DB_PATH))
conn.row_factory = sqlite3.Row
cur  = conn.cursor()

# このファイルはトップレベルの手続き型スクリプトで、途中に複数のsys.exit()や
# 対話input()があるため、末尾までtry/finallyで包むには全体の再インデントが必要になる。
# atexitはsys.exit()・未捕捉例外（KeyboardInterrupt含む）のどちらでもインタプリタ終了
# 直前に必ず呼ばれるため、より小さい差分でtry/finallyと同じ保証（未コミット分の
# rollback + 確実なconn.close()）が得られる。SIGKILL等の強制終了には無力な点は
# try/finallyでも同じ（2026-07-14、stale journal再発の原因調査を受けて追加）。
def _safe_close_conn():
    try:
        conn.rollback()
    except Exception:
        pass
    try:
        conn.close()
    except Exception:
        pass

atexit.register(_safe_close_conn)

issues_found = []
fixes_to_run = []


def record_issue(level, check_id, msg, fix_fn=None, fix_desc=""):
    issues_found.append((level, check_id, msg))
    if fix_fn:
        fixes_to_run.append((check_id, fix_desc, fix_fn))
    if level == "error":
        error(f"[{check_id}] {msg}")
    elif level == "warn":
        warn(f"[{check_id}] {msg}")
    else:
        info(f"[{check_id}] {msg}")


# ── テーブル読み込み ─────────────────────────────────────────
def load_scenarios():
    return conn.execute(
        "SELECT id,name,domain,tag,tag_color,dsl_json FROM scenarios WHERE is_active=1 ORDER BY id"
    ).fetchall()

def load_dsl_definitions():
    return conn.execute(
        "SELECT id,problem_class,version,extensions FROM dsl_definitions ORDER BY id"
    ).fetchall()

def load_extensions():
    return conn.execute(
        "SELECT id,name,category FROM extensions ORDER BY id"
    ).fetchall()

def get_scenario_names():
    return {r["name"] for r in load_scenarios()}

def get_dsl_classes():
    return {r["problem_class"] for r in load_dsl_definitions()}

def get_extension_names():
    return {r["name"] for r in load_extensions()}


# ── 修復ヘルパー ─────────────────────────────────────────────
def insert_scenario_if_missing(name, description, tag, tag_color, domain, dsl_json):
    if name in get_scenario_names():
        return None
    cur.execute(
        "INSERT INTO scenarios (name,description,tag,tag_color,domain,dsl_json,is_active)"
        " VALUES (?,?,?,?,?,?,1)",
        (name, description, tag, tag_color, domain,
         json.dumps(dsl_json, ensure_ascii=False)),
    )
    conn.commit()
    return cur.lastrowid

def insert_dsl_if_missing(problem_class, version, extensions, description):
    if problem_class in get_dsl_classes():
        return None
    cur.execute(
        "INSERT INTO dsl_definitions (problem_class,version,extensions,schema_json,description)"
        " VALUES (?,?,?,?,?)",
        (problem_class, version, json.dumps(extensions), json.dumps({}), description),
    )
    conn.commit()
    did = cur.lastrowid
    cur.execute(
        "INSERT INTO dsl_evolution_log (dsl_id,change_type,trigger_type,business_context,created_by)"
        " VALUES (?,'initialization','initialization',?,'repair_db.py')",
        (did, f"{problem_class} の初期登録（repair_db.py による修復）"),
    )
    conn.commit()
    return did

def insert_extension_if_missing(name, category, description):
    if name in get_extension_names():
        return None
    cur.execute(
        "INSERT INTO extensions (name,category,schema_fragment,description)"
        " VALUES (?,?,?,?)",
        (name, category, json.dumps({}), description),
    )
    conn.commit()
    return cur.lastrowid

def load_json_file(filename):
    path = SCENARIOS_DIR / filename
    if not path.exists():
        return None
    return json.loads(path.read_text(encoding="utf-8"))

def solver_exists(snake_name):
    return (SOLVERS_DIR / f"{snake_name}_solver.py").exists()


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# チェック C1: シナリオJSONファイル ↔ scenarios テーブル
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
head("[C1] シナリオJSONファイル ↔ scenarios テーブル")

# 既知のシナリオファイル群と「期待するDB登録名」のマッピング
SCENARIO_CATALOG = [
    # (file_stem_prefix, label_base, tag, color, domain, note_suffix)
    ("hospital_shift_planner", "病院夜間・外来シフト最適化",
     "STAFFING", "#ec4899", "hospital_shift_planner",
     None),  # None = ソルバーあり (EventStaffing を使用)

    ("production_lot_scheduler", "食品工場ロット配分最適化",
     "PLANNER", "#f59e0b", "production_lot_scheduler",
     "⚠️ ソルバー未実装。▶ を押してもエラーになります。"),

    ("kubernetes_scheduler",      "Kubernetesマイクロサービス配置最適化",
     "SERVER", "#6366f1", "kubernetes_scheduler",
     "⚠️ ソルバーファイルが存在しません（ImportError）。"),

    ("kubernetes_scheduler2",     "Kubernetesスケジューリング v2",
     "SERVER", "#6366f1", "kubernetes_scheduler2",
     "⚠️ ソルバーファイルが存在しません（ImportError）。"),

    ("micro_service_dispatcher",  "マイクロサービスディスパッチャー最適化",
     "SERVER", "#6366f1", "micro_service_dispatcher",
     "⚠️ ソルバーファイルが存在しません（ImportError）。"),

    ("servers_dispatch",          "サーバーディスパッチ最適化",
     "SERVER", "#6366f1", "servers_dispatch",
     "⚠️ ソルバーファイルが存在しません（ImportError）。"),

    ("vehicle_routing_problem1", "日次配送ルート最適化",
     "ROUTING", "#0ea5e9", "vehicle_routing_problem1",
     None),  # ソルバー実装済
]

SUFFIX_LABELS = [
    ("baseline",   "— 標準"),
    ("tight",      "— タイト"),
    ("infeasible", "— 解なし"),
]

for prefix, label_base, tag, color, domain, warning_note in SCENARIO_CATALOG:
    for suffix, slabel in SUFFIX_LABELS:
        fname = f"{prefix}_{suffix}.json"
        path  = SCENARIOS_DIR / fname
        full_label = f"{label_base} {slabel}"

        if not path.exists():
            ok(f"{fname} — ファイルなし（スキップ）")
            continue

        dsl = load_json_file(fname)
        existing_names = get_scenario_names()

        if full_label in existing_names:
            ok(f"{fname} → scenarios['{full_label}'] 登録済み")
        else:
            desc = warning_note or f"{label_base} の{slabel.strip('— ')}ケース"
            def _make_fix(n, d, t, c, dm, ds):
                def _fix():
                    sid = insert_scenario_if_missing(n, d, t, c, dm, ds)
                    if sid:
                        print(f"    → scenarios 登録: '{n}' ID={sid}")
                return _fix
            record_issue(
                "error", "C1",
                f"{fname} → scenarios テーブルに '{full_label}' が未登録",
                fix_fn=_make_fix(full_label, desc, tag, color, domain, dsl),
                fix_desc=f"scenarios に '{full_label}' を登録",
            )


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# チェック C2: dsl_definitions ↔ 主要ドメインの登録
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
head("[C2] dsl_definitions ↔ ドメイン登録")

DSL_CATALOG = [
    # (problem_class, version, extensions, description, require_solver)
    ("HospitalShiftPlanner", "1.0",
     ["preference_constraint", "work_hour_limit",
      "certification_requirement", "fatigue_constraint"],
     "病院夜間・外来シフト最適化（EventStaffing ベースの派生ドメイン）",
     False),  # EventStaffing ソルバーを使用するためソルバーファイル不要

    ("VehicleRoutingProblem1", "1.0",
     [],
     "日次配送ルート最適化（CVRPTW: 時間枠・车格制限・積載制約付き VRP）",
     True),   # vehicle_routing_problem1_solver.py が実装済
]

for pc, ver, exts, desc, need_solver in DSL_CATALOG:
    if pc in get_dsl_classes():
        ok(f"dsl_definitions['{pc}' v{ver}] 登録済み")
    else:
        def _make_dsl_fix(p, v, e, d):
            def _fix():
                did = insert_dsl_if_missing(p, v, e, d)
                if did:
                    print(f"    → dsl_definitions 登録: '{p}' v{v} ID={did}")
            return _fix
        record_issue(
            "error", "C2",
            f"dsl_definitions に '{pc}' v{ver} が未登録",
            fix_fn=_make_dsl_fix(pc, ver, exts, desc),
            fix_desc=f"dsl_definitions に '{pc}' v{ver} を登録",
        )


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# チェック C3: extensions テーブル ↔ dsl_definitions.extensions
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
head("[C3] extensions テーブル ↔ dsl_definitions.extensions")

EXTENSION_CATALOG = [
    ("certification_requirement", "constraint_extension",
     "医療資格要件。required_certifications フィールドで特定タスクに必要な資格を指定する。"),
    ("fatigue_constraint", "constraint_extension",
     "連続夜勤制限。max_consecutive_night_shifts と min_rest_after_night_shift で疲労制約を定義する。"),
]

# dsl_definitions に登録されている全extensionを収集
all_referenced_exts = set()
for row in load_dsl_definitions():
    try:
        exts = json.loads(row["extensions"])
        all_referenced_exts.update(exts)
    except Exception:
        pass

existing_ext_names = get_extension_names()
for ext_name, category, desc in EXTENSION_CATALOG:
    if ext_name in existing_ext_names:
        ok(f"extensions['{ext_name}'] 登録済み")
    else:
        def _make_ext_fix(n, c, d):
            def _fix():
                eid = insert_extension_if_missing(n, c, d)
                if eid:
                    print(f"    → extensions 登録: '{n}' ID={eid}")
            return _fix
        record_issue(
            "error", "C3",
            f"extensions に '{ext_name}' ({category}) が未登録",
            fix_fn=_make_ext_fix(ext_name, category, desc),
            fix_desc=f"extensions に '{ext_name}' を登録",
        )


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# チェック C4: ソルバーなし新規ドメインの識別
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
head("[C4] ソルバーなし新規ドメインの識別")

SOLVER_CHECK = [
    ("production_lot_scheduler", "ProductionLotScheduler"),
    ("kubernetes_scheduler",     "KubernetesScheduler"),
    ("kubernetes_scheduler2",    "KubernetesScheduler2"),
    ("micro_service_dispatcher", "MicroServiceDispatcher"),
    ("servers_dispatch",         "ServersDispatch"),
    ("vehicle_routing_problem1", "VehicleRoutingProblem1"),  # 実装済
]

for snake, display in SOLVER_CHECK:
    if solver_exists(snake):
        ok(f"{snake}_solver.py — 存在")
    else:
        warn(f"[C4] {snake}_solver.py が存在しません。"
             f"'{display}' シナリオは ▶ を押してもエラーになります。（登録は問題なし）")


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# チェック C5: scenarios テーブルに孤立したファイル参照
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
head("[C5] scenarios テーブルの孤立エントリ確認")

for row in load_scenarios():
    dsl_json = json.loads(row["dsl_json"])
    sid_val  = dsl_json.get("id", "")
    domain   = row["domain"]
    # id から対応ファイル名を推測
    if sid_val:
        expected_file = SCENARIOS_DIR / f"{sid_val}.json"
        if not expected_file.exists():
            warn(f"[C5] scenarios[{row['id']}] '{row['name']}' の dsl_json.id='{sid_val}'"
                 f" に対応するファイル {expected_file.name} が存在しません（DB登録のみ）")
        else:
            ok(f"scenarios[{row['id']}] '{row['name'][:40]}' ↔ {expected_file.name}")
    else:
        ok(f"scenarios[{row['id']}] '{row['name'][:40]}' — dsl.id なし（DBのみ管理）")


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# 結果サマリ
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
head("=== 整合性チェック結果 ===")

errors = [i for i in issues_found if i[0] == "error"]
warns  = [i for i in issues_found if i[0] == "warn"]

print(f"  エラー: {len(errors)} 件")
print(f"  警告:   {len(warns)} 件")
print(f"  修復候補: {len(fixes_to_run)} 件")

if not issues_found:
    print("\n\033[32m全チェック通過。DBは整合しています。\033[0m")
    conn.close()
    sys.exit(0)

if CHECK_ONLY:
    print("\n--check-only モード: 修復は実行しません。")
    conn.close()
    sys.exit(1 if errors else 0)

if not fixes_to_run:
    conn.close()
    sys.exit(1 if errors else 0)

# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# 修復実行
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
head("=== 修復実行 ===")

if AUTO_FIX:
    print("--auto-fix モード: 全修復を自動実行します。")
    do_fix = True
else:
    print(f"\n以下の {len(fixes_to_run)} 件の修復を実行しますか？")
    for i, (cid, desc, _) in enumerate(fixes_to_run, 1):
        print(f"  {i}. [{cid}] {desc}")
    ans = input("\n実行する場合は 'yes' を入力: ").strip().lower()
    do_fix = ans == "yes"

if do_fix:
    success = 0
    failed  = 0
    for cid, desc, fix_fn in fixes_to_run:
        try:
            fix_fn()
            print(f"  \033[32m[完了]\033[0m [{cid}] {desc}")
            success += 1
        except Exception as e:
            print(f"  \033[31m[失敗]\033[0m [{cid}] {desc}: {e}")
            failed += 1

    head("=== 修復完了 ===")
    print(f"  成功: {success} 件 / 失敗: {failed} 件")

    if success > 0:
        head("=== 修復後の状態 ===")
        print("\nscenarios (active):")
        for r in load_scenarios():
            print(f"  [{r['id']:3}] {r['tag']:10} {r['domain']:30} {r['name']}")
        print("\ndsl_definitions:")
        for r in load_dsl_definitions():
            print(f"  [{r['id']:3}] {r['problem_class']:35} v{r['version']}")
        print("\nextensions:")
        for r in load_extensions():
            print(f"  [{r['id']:3}] {r['category']:25} {r['name']}")
else:
    print("修復をキャンセルしました。")

conn.close()
print("\n完了。")
