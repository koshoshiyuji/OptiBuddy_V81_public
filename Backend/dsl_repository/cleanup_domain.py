#!/usr/bin/env python3
"""
cleanup_domain.py — 指定ドメインの登録・ファイルを全削除する

使い方:
  cd /Users/arche/work/OptiBuddy_V81/Backend
  conda activate optibuddy
  python3 dsl_repository/cleanup_domain.py <SnakeCaseName> [--dry-run] [--yes]

例:
  python3 dsl_repository/cleanup_domain.py production_lot_scheduler2
  python3 dsl_repository/cleanup_domain.py production_lot_scheduler2 --dry-run
  python3 dsl_repository/cleanup_domain.py production_lot_scheduler2 --yes

オプション:
  --dry-run  : 削除対象を表示するだけで実際には削除しない
  --yes      : 確認プロンプトをスキップして即削除

削除対象:
  [DB]   scenarios テーブル (domain 一致)
  [DB]   dsl_definitions テーブル (problem_class 一致、PascalCase変換)
  [DB]   dsl_evolution_log テーブル (dsl_id 経由)
  [DB]   extensions テーブル (category 一致、PascalCase変換)
  [FILE] Backend/solvers/{snake}_solver.py
  [FILE] Backend/dsl_repository/scenarios/{snake}_*.json
  [FILE] Frontend/src/app/studio/views/{Pascal}View.tsx
  [FILE] Backend/dsl_transformer/{snake}_converter.py
  [FILE] Backend/dsl_transformer/{snake}_ui_converter.py
  [PATCH] Backend/dsl_transformer/business_to_solver.py の
          `if problem_class == "{Pascal}":` ディスパッチブロック
  [PATCH] Backend/dsl_transformer/solver_to_ui.py の
          `if problem_class == "{Pascal}":` ディスパッチブロック
  [PATCH] Frontend/src/app/HomeScreenNew.tsx の TAG_MAP エントリ

【2026-08-06修正】上記3つの[PATCH]項目を新設した。以前はdomain_generator.pyの
  `_patch_converter_dispatch()` / `_patch_home_screen_tag_map()` が新規登録時に
  自動追加する3箇所（business_to_solver.py・solver_to_ui.py・TAG_MAP）を、
  このスクリプトが削除対象に含んでいなかった。テストドメインを削除しても
  この3箇所だけ手動で除去する必要がある既知のギャップだった
  （2026-08-06、FactoryStoreTransport系3ドメイン削除時に実際に手動対応が発生。
  ENGINEERING_LOG 2026-08-06追記19参照）。挿入時のブロック形状
  （`_patch_converter_dispatch()`のインデント付きif-returnブロック、
  `_patch_home_screen_tag_map()`の1行TAG_MAPエントリ）に対応する形で
  逆操作（除去）を実装した。

備考:
  extensions テーブルは、パターン3（既存ドメインへのextension_gaps適用）で
  category=新ドメイン名（Pascal）として登録される。以前はこのスクリプトの
  対象外で、拡張適用が失敗・中断したときに孤立行として残り続けていた
  （2026-07-12、NurseShiftWeeklyCap登録の試行錯誤で実際に発生し手動SQLで
  対処した）。同じ理由でscenarios/dsl_definitionsが空でも、extensionsだけ
  残っているケースがあるので必ず確認すること。

【2026-07-13修正】既定の一致判定を完全一致に変更した。
  以前はドメイン名の一致判定に `LIKE '%snake%'` / `LIKE '%Pascal%'`（部分一致）を
  常に使っており、"nurse_shift" で実行すると"nurse_shift_weekly_cap"のような
  別ドメイン（片方がもう片方の部分文字列）まで巻き込んで削除される事故リスクが
  あった（実際に2026-07-13、専用の削除スクリプトで回避する形で対処が必要になった）。
  一方で、scenarios.domain="yard" / dsl_definitions.problem_class="YardPlanning"の
  ように、テーブル間でドメイン名の表記が完全には一致しない既存ケースがあり、
  部分一致が必要な場面も実在する。そのため：
    - デフォルトは完全一致のみ（安全側）。
    - `--fuzzy` オプション指定時のみ部分一致（LIKE）にフォールバックする。
      この場合、削除前に必ず一致リストを目視確認すること
      （dry-runで表示される一覧に無関係なドメインが混入していないか要確認）。
"""

import json
import re
import sqlite3
import sys
from pathlib import Path
from typing import Dict, List, Tuple

# ── snake / PascalCase 変換 ──────────────────────────────────
def to_snake(name: str) -> str:
    s = re.sub(r"([A-Z])", r"_\1", name).lower().lstrip("_")
    return s.replace("-", "_")

def to_pascal(snake: str) -> str:
    return "".join(w.capitalize() for w in snake.split("_"))

def _safe_scenario_json_files(scenarios_dir: Path, snake: str, conn: sqlite3.Connection) -> List[Path]:
    """
    scenarios_dir.glob(f"{snake}_*.json") は、"nurse_shift_*.json" が
    "nurse_shift_weekly_cap_baseline.json" のような別ドメイン（自分の名前を
    プレフィックスとして含むより具体的な別ドメイン）のファイルまで拾ってしまう
    バグがあった（2026-07-13、delete_nurse_shift_base_domain.py の docstring 参照）。
    DB上に登録されている「自分より長い、自分をプレフィックスとして持つ別ドメイン」
    (sibling domain) を調べ、そのドメインに属するファイルを除外する。
    """
    sibling_rows = conn.execute(
        "SELECT DISTINCT domain FROM scenarios WHERE domain != ?", (snake,)
    ).fetchall()
    siblings = [r["domain"] for r in sibling_rows if r["domain"].startswith(snake + "_")]

    candidates = list(scenarios_dir.glob(f"{snake}_*.json"))
    safe = []
    for f in candidates:
        if any(f.name.startswith(sib + "_") or f.name == f"{sib}.json" for sib in siblings):
            continue
        safe.append(f)
    return safe

# ── ディスパッチパッチ / TAG_MAP 検出・除去 ──────────────────────
# domain_generator.py の _patch_converter_dispatch() / _patch_home_screen_tag_map()
# が新規ドメイン登録時に挿入する3箇所（business_to_solver.py / solver_to_ui.py の
# if-returnブロック、HomeScreenNew.tsx の TAG_MAP エントリ）を、逆方向（検出→除去）
# で扱うためのヘルパー。挿入側の実装（domain_generator.py 5914行目付近・5967行目付近）
# とブロック形状の前提を共有しているため、挿入側の形式が変わった場合はここも追随が必要。

def _find_dispatch_block(content: str, pascal: str) -> bool:
    """business_to_solver.py / solver_to_ui.py に該当ドメインのディスパッチ
    ブロック（if problem_class == "Pascal":）が存在するか判定する。"""
    return f'if problem_class == "{pascal}":' in content


def _remove_dispatch_block(content: str, pascal: str) -> Tuple[str, bool]:
    """
    `if problem_class == "{pascal}":` から始まるifブロック（本体はより深い
    インデントの行が続く）を1つ除去する。本体終端は「空行はスキップしつつ、
    同じ/浅いインデントの非空行に達したら終了」で判定する
    （_patch_converter_dispatch() が挿入する形の逆変換）。
    """
    target = f'if problem_class == "{pascal}":'
    lines = content.split("\n")
    out: List[str] = []
    removed = False
    i = 0
    n = len(lines)
    while i < n:
        line = lines[i]
        if target in line and not removed:
            base_indent = len(line) - len(line.lstrip())
            i += 1
            while i < n:
                l = lines[i]
                if l.strip() == "":
                    i += 1
                    continue
                cur_indent = len(l) - len(l.lstrip())
                if cur_indent > base_indent:
                    i += 1
                    continue
                break
            removed = True
            continue
        out.append(line)
        i += 1
    return "\n".join(out), removed


def _find_tag_map_entry(content: str, pascal: str) -> bool:
    """HomeScreenNew.tsx の TAG_MAP に該当ドメインのエントリが存在するか判定する。"""
    tag_map_start = content.find("const TAG_MAP:")
    tag_map_end = content.find("};", tag_map_start)
    if tag_map_start == -1 or tag_map_end == -1:
        return False
    tag_map_section = content[tag_map_start:tag_map_end + 2]
    pattern = rf'^\s*{re.escape(pascal)}:\s*\{{[^}}]+\}},?\s*$'
    return any(re.match(pattern, line) for line in tag_map_section.split("\n"))


def _remove_tag_map_entry(content: str, pascal: str) -> Tuple[str, bool]:
    """
    TAG_MAP から該当ドメインのエントリ行を除去する
    （_patch_home_screen_tag_map() の重複キー削除ロジックと同一の正規表現を
    削除方向に使う）。
    """
    tag_map_start = content.find("const TAG_MAP:")
    tag_map_end = content.find("};", tag_map_start)
    if tag_map_start == -1 or tag_map_end == -1:
        return content, False

    tag_map_section = content[tag_map_start:tag_map_end + 2]
    pattern = rf'^\s*{re.escape(pascal)}:\s*\{{[^}}]+\}},?\s*$'
    lines = tag_map_section.split("\n")
    filtered = []
    removed = False
    for line in lines:
        if re.match(pattern, line):
            removed = True
            continue
        filtered.append(line)

    if not removed:
        return content, False

    new_section = "\n".join(filtered)
    return content[:tag_map_start] + new_section + content[tag_map_end + 2:], True


class DomainDeletionBlocked(Exception):
    """削除すると他のファイルのimportが壊れるため、削除を中止した"""


# ── 削除候補ファイルを他のファイルがimportしていないかの確認 ─────────
# 2026-09-26追加: i18n/nurse_shift_weekly_cap_messages.py は名前上NurseShiftWeeklyCap
# 専用だが、app_core.py・routes_*.py・issue_rules/全ファイルがモジュール先頭で
# importする事実上の共通ファイル。2026-09-16(aebe327)で {snake}_messages.py を
# 削除対象に加えた結果、NurseShiftWeeklyCapを削除するとこのファイルが消え、
# 次回起動時にapp_core.pyのimportでImportErrorになりバックエンドが起動しなくなる
# 状態だった（未発生。2026-09-26のコード読解で発見）。
# 削除候補の.pyを他の.pyがモジュール先頭（インデント無し）でimportしていたら、
# 削除全体を中止する。以下は対象外:
#   - 関数内の遅延import（route_decomposer.py→truck_dispatcher_solver 等。
#     そのドメイン自身からしか呼ばれないファイルが大半で、含めると誤検知になる）
#   - test_*.py（削除後に失敗するだけでアプリは壊れない）
#   - business_to_solver.py / solver_to_ui.py のうち、削除時に一緒に除去する
#     ディスパッチブロック内のimport
def _find_import_blockers(backend_root: Path, candidate_files: List[Path], pascal: str) -> List[str]:
    stems = {f.stem for f in candidate_files if f.suffix == ".py" and f.exists()}
    if not stems:
        return []
    pattern = re.compile(
        r"^(?:from|import)\s+[\w.]*\b(" + "|".join(map(re.escape, sorted(stems))) + r")\b",
        re.M,
    )
    candidate_set = {f.resolve() for f in candidate_files}
    blockers = []
    for f in backend_root.rglob("*.py"):
        if "__pycache__" in f.parts or f.name.startswith("test_") or f.resolve() in candidate_set:
            continue
        text = f.read_text(encoding="utf-8", errors="ignore")
        if f.name in ("business_to_solver.py", "solver_to_ui.py"):
            text, _ = _remove_dispatch_block(text, pascal)
        m = pattern.search(text)
        if m:
            line_no = text.count("\n", 0, m.start()) + 1
            blockers.append(f"{f.relative_to(backend_root.parent)}:{line_no} ({m.group(1)})")
    return sorted(blockers)


def _py_candidates(backend_root: Path, snake: str) -> List[Path]:
    return [
        backend_root / "solvers" / f"{snake}_solver.py",
        backend_root / "dsl_transformer" / f"{snake}_converter.py",
        backend_root / "dsl_transformer" / f"{snake}_ui_converter.py",
        backend_root / "i18n" / f"{snake}_messages.py",
    ]


# ── カラー出力 ────────────────────────────────────────────────
def red(msg):    print(f"  \033[31m🗑  DELETE\033[0m  {msg}")
def yellow(msg): print(f"  \033[33m⚠️  SKIP\033[0m    {msg}")
def green(msg):  print(f"  \033[32m✅ DONE\033[0m    {msg}")
def cyan(msg):   print(f"  \033[36mℹ️  INFO\033[0m    {msg}")
def head(msg):   print(f"\n\033[1m{msg}\033[0m")

# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# API用関数（Web UIから呼び出し可能）
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

def collect_deletion_targets(domain_name: str, fuzzy: bool = False) -> Dict[str, any]:
    """
    削除対象を収集して返す（実際には削除しない）

    Args:
        domain_name: ドメイン名（snake_case または PascalCase）
        fuzzy: True の場合のみ部分一致(LIKE)も対象にする。デフォルトは完全一致のみ
               （2026-07-13: nurse_shift / nurse_shift_weekly_cap 巻き込み事故を受けて
               安全側をデフォルトに変更）。

    Returns:
        {
            "scenarios": [{"id": 1, "name": "...", "domain": "..."}],
            "dsl_definitions": [{"id": 1, "problem_class": "...", "version": "..."}],
            "evolution_logs": [{"id": 1, "dsl_id": 1}],
            "files": ["Backend/solvers/...", "Frontend/..."]
        }
    """
    # snake / PascalCase 変換
    if "_" in domain_name or domain_name.islower():
        snake = domain_name.lower()
        pascal = to_pascal(snake)
    else:
        pascal = domain_name
        snake = to_snake(domain_name)
    
    # パス定義
    backend_root = Path(__file__).parent.parent
    project_root = backend_root.parent
    db_path = Path(__file__).parent / "optibuddy.db"
    scenarios_dir = Path(__file__).parent / "scenarios"
    solvers_dir = backend_root / "solvers"
    transformer_dir = backend_root / "dsl_transformer"
    i18n_dir = backend_root / "i18n"
    frontend_views = project_root / "Frontend" / "src" / "app" / "studio" / "views"
    business_to_solver_path = transformer_dir / "business_to_solver.py"
    solver_to_ui_path = transformer_dir / "solver_to_ui.py"
    home_screen_path = project_root / "Frontend" / "src" / "app" / "HomeScreenNew.tsx"

    # DB接続
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row

    result = {
        "scenarios": [],
        "dsl_definitions": [],
        "evolution_logs": [],
        "extensions": [],
        "files": [],
        "dispatch_patches": [],
        "tag_map_entries": []
    }

    # scenarios
    if fuzzy:
        rows = conn.execute(
            "SELECT id, name, domain FROM scenarios WHERE domain = ? OR domain LIKE ?",
            (snake, f"%{snake}%")
        ).fetchall()
    else:
        rows = conn.execute(
            "SELECT id, name, domain FROM scenarios WHERE domain = ?",
            (snake,)
        ).fetchall()
    result["scenarios"] = [{"id": r["id"], "name": r["name"], "domain": r["domain"]} for r in rows]

    # dsl_definitions
    if fuzzy:
        rows = conn.execute(
            "SELECT id, problem_class, version FROM dsl_definitions WHERE problem_class = ? OR problem_class LIKE ?",
            (pascal, f"%{pascal}%")
        ).fetchall()
    else:
        rows = conn.execute(
            "SELECT id, problem_class, version FROM dsl_definitions WHERE problem_class = ?",
            (pascal,)
        ).fetchall()
    result["dsl_definitions"] = [{"id": r["id"], "problem_class": r["problem_class"], "version": r["version"]} for r in rows]

    # evolution_logs
    dsl_ids = [d["id"] for d in result["dsl_definitions"]]
    if dsl_ids:
        placeholders = ','.join('?' * len(dsl_ids))
        rows = conn.execute(
            f"SELECT id, dsl_id FROM dsl_evolution_log WHERE dsl_id IN ({placeholders})",
            dsl_ids
        ).fetchall()
        result["evolution_logs"] = [{"id": r["id"], "dsl_id": r["dsl_id"]} for r in rows]

    # extensions（パターン3の拡張適用で category=新ドメイン名(Pascal) として登録される）
    if fuzzy:
        rows = conn.execute(
            "SELECT id, name, category FROM extensions WHERE category = ? OR category LIKE ?",
            (pascal, f"%{pascal}%")
        ).fetchall()
    else:
        rows = conn.execute(
            "SELECT id, name, category FROM extensions WHERE category = ?",
            (pascal,)
        ).fetchall()
    result["extensions"] = [{"id": r["id"], "name": r["name"], "category": r["category"]} for r in rows]

    # ファイル（scenarios/*.jsonのglobは、より具体的な別ドメイン名を
    # プレフィックスとして誤って拾わないよう _safe_scenario_json_files でフィルタ）
    candidate_files = [
        solvers_dir / f"{snake}_solver.py",
        transformer_dir / f"{snake}_converter.py",
        transformer_dir / f"{snake}_ui_converter.py",
        frontend_views / f"{pascal}View.tsx",
        i18n_dir / f"{snake}_messages.py",
    ]
    candidate_files += _safe_scenario_json_files(scenarios_dir, snake, conn)

    conn.close()

    for f in candidate_files:
        if f.exists():
            result["files"].append(str(f.relative_to(project_root)))

    # ディスパッチパッチ（business_to_solver.py / solver_to_ui.py）
    for label, path in (("business_to_solver.py", business_to_solver_path),
                         ("solver_to_ui.py", solver_to_ui_path)):
        if path.exists() and _find_dispatch_block(path.read_text(encoding="utf-8"), pascal):
            result["dispatch_patches"].append({"file": str(path.relative_to(project_root)), "problem_class": pascal})

    # Frontend TAG_MAP エントリ
    if home_screen_path.exists() and _find_tag_map_entry(home_screen_path.read_text(encoding="utf-8"), pascal):
        result["tag_map_entries"].append({"file": str(home_screen_path.relative_to(project_root)), "problem_class": pascal})

    # 削除候補の.pyを他のファイルがモジュール先頭でimportしていないか（2026-09-26追加）
    result["import_blockers"] = _find_import_blockers(backend_root, _py_candidates(backend_root, snake), pascal)

    return result


def delete_domain_api(domain_name: str, fuzzy: bool = False) -> Dict[str, any]:
    """
    指定ドメインを完全削除する（DB + ファイル）

    Args:
        domain_name: ドメイン名（snake_case または PascalCase）
        fuzzy: True の場合のみ部分一致(LIKE)も対象にする。デフォルトは完全一致のみ。

    Returns:
        {
            "scenarios": 3,
            "dsl_definitions": 1,
            "evolution_logs": 5,
            "files": ["Backend/solvers/...", ...]
        }
    """
    # snake / PascalCase 変換
    if "_" in domain_name or domain_name.islower():
        snake = domain_name.lower()
        pascal = to_pascal(snake)
    else:
        pascal = domain_name
        snake = to_snake(domain_name)
    
    # パス定義
    backend_root = Path(__file__).parent.parent
    project_root = backend_root.parent
    db_path = Path(__file__).parent / "optibuddy.db"
    scenarios_dir = Path(__file__).parent / "scenarios"
    solvers_dir = backend_root / "solvers"
    transformer_dir = backend_root / "dsl_transformer"
    i18n_dir = backend_root / "i18n"
    frontend_views = project_root / "Frontend" / "src" / "app" / "studio" / "views"
    business_to_solver_path = transformer_dir / "business_to_solver.py"
    solver_to_ui_path = transformer_dir / "solver_to_ui.py"
    home_screen_path = project_root / "Frontend" / "src" / "app" / "HomeScreenNew.tsx"

    # 削除すると他のファイルのimportが壊れる場合は、DBにもファイルにも触れる前に中止する
    # （2026-09-26追加。詳細は _find_import_blockers() のコメント参照）
    blockers = _find_import_blockers(backend_root, _py_candidates(backend_root, snake), pascal)
    if blockers:
        shown = ", ".join(blockers[:5]) + (f" ほか{len(blockers) - 5}件" if len(blockers) > 5 else "")
        raise DomainDeletionBlocked(
            f"{pascal} は削除できません。削除対象のファイルを他のファイルがモジュール先頭で"
            f"importしており、削除するとバックエンドが起動しなくなります: {shown}"
        )

    # DB接続
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    cur = conn.cursor()

    result = {
        "scenarios": 0,
        "dsl_definitions": 0,
        "evolution_logs": 0,
        "extensions": 0,
        "files": [],
        "dispatch_patches": [],
        "tag_map_entries": []
    }

    # try/finally: 途中で例外が起きても必ずrollback + conn.close()する。
    # ここは複数DELETE文をまとめて最後にconn.commit()する構成のため、
    # コミット前に異常終了するとstale journalが残るリスクが最も高い箇所
    # （2026-07-14、実際にこの種のジャーナルが残っていたことが判明した）。
    try:
        # scenarios削除
        if fuzzy:
            rows = conn.execute(
                "SELECT id FROM scenarios WHERE domain = ? OR domain LIKE ?",
                (snake, f"%{snake}%")
            ).fetchall()
        else:
            rows = conn.execute(
                "SELECT id FROM scenarios WHERE domain = ?", (snake,)
            ).fetchall()
        scenario_ids = [r["id"] for r in rows]
        if scenario_ids:
            placeholders = ','.join('?' * len(scenario_ids))
            cur.execute(f"DELETE FROM scenarios WHERE id IN ({placeholders})", scenario_ids)
            result["scenarios"] = len(scenario_ids)

        # dsl_definitions削除
        if fuzzy:
            rows = conn.execute(
                "SELECT id FROM dsl_definitions WHERE problem_class = ? OR problem_class LIKE ?",
                (pascal, f"%{pascal}%")
            ).fetchall()
        else:
            rows = conn.execute(
                "SELECT id FROM dsl_definitions WHERE problem_class = ?", (pascal,)
            ).fetchall()
        dsl_ids = [r["id"] for r in rows]
        if dsl_ids:
            # evolution_logs削除
            placeholders = ','.join('?' * len(dsl_ids))
            logs = conn.execute(
                f"SELECT id FROM dsl_evolution_log WHERE dsl_id IN ({placeholders})",
                dsl_ids
            ).fetchall()
            result["evolution_logs"] = len(logs)
            cur.execute(f"DELETE FROM dsl_evolution_log WHERE dsl_id IN ({placeholders})", dsl_ids)

            # dsl_definitions削除
            cur.execute(f"DELETE FROM dsl_definitions WHERE id IN ({placeholders})", dsl_ids)
            result["dsl_definitions"] = len(dsl_ids)

        # extensions削除（パターン3で category=新ドメイン名(Pascal) として登録されたもの）
        if fuzzy:
            rows = conn.execute(
                "SELECT id FROM extensions WHERE category = ? OR category LIKE ?",
                (pascal, f"%{pascal}%")
            ).fetchall()
        else:
            rows = conn.execute(
                "SELECT id FROM extensions WHERE category = ?", (pascal,)
            ).fetchall()
        ext_ids = [r["id"] for r in rows]
        if ext_ids:
            placeholders = ','.join('?' * len(ext_ids))
            cur.execute(f"DELETE FROM extensions WHERE id IN ({placeholders})", ext_ids)
            result["extensions"] = len(ext_ids)

        # ファイル候補の算出はDBクローズ前に（sibling domain判定にconnを使うため）
        candidate_files = [
            solvers_dir / f"{snake}_solver.py",
            transformer_dir / f"{snake}_converter.py",
            transformer_dir / f"{snake}_ui_converter.py",
            frontend_views / f"{pascal}View.tsx",
            i18n_dir / f"{snake}_messages.py",
        ]
        candidate_files += _safe_scenario_json_files(scenarios_dir, snake, conn)

        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()

    # ファイル削除
    for f in candidate_files:
        if f.exists():
            try:
                f.unlink()
                result["files"].append(str(f.relative_to(project_root)))
            except Exception:
                pass  # エラーは無視

    # ディスパッチパッチ除去（business_to_solver.py / solver_to_ui.py）
    # 2026-08-06追加: domain_generator.py の _patch_converter_dispatch() が
    # 挿入した if-return ブロックを、削除時にも対で除去する。
    for path in (business_to_solver_path, solver_to_ui_path):
        if not path.exists():
            continue
        try:
            content = path.read_text(encoding="utf-8")
            new_content, removed = _remove_dispatch_block(content, pascal)
            if removed:
                path.write_text(new_content, encoding="utf-8")
                result["dispatch_patches"].append(str(path.relative_to(project_root)))
        except Exception:
            pass  # エラーは無視（他の削除処理を継続）

    # TAG_MAP エントリ除去（Frontend/src/app/HomeScreenNew.tsx）
    # 2026-08-06追加: domain_generator.py の _patch_home_screen_tag_map() が
    # 追加したエントリを、削除時にも対で除去する。
    if home_screen_path.exists():
        try:
            content = home_screen_path.read_text(encoding="utf-8")
            new_content, removed = _remove_tag_map_entry(content, pascal)
            if removed:
                home_screen_path.write_text(new_content, encoding="utf-8")
                result["tag_map_entries"].append(str(home_screen_path.relative_to(project_root)))
        except Exception:
            pass  # エラーは無視

    return result


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# CLIエントリーポイント
#
# 以前はこのブロック自体が存在せず、docstringに書かれた
# --dry-run / --yes オプションや確認プロンプトが実際には何も実行されず、
# `python cleanup_domain.py <name> --yes` を実行してもスクリプトがインポートされるだけで
# 何も起きずリターンする不具合があった。
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

def _print_targets(targets: Dict[str, any]) -> None:
    head("削除対象")
    if targets["scenarios"]:
        for s in targets["scenarios"]:
            red(f"scenarios: id={s['id']} name={s['name']!r} domain={s['domain']!r}")
    else:
        yellow("scenarios: 該当なし")

    if targets["dsl_definitions"]:
        for d in targets["dsl_definitions"]:
            red(f"dsl_definitions: id={d['id']} problem_class={d['problem_class']!r} version={d['version']}")
    else:
        yellow("dsl_definitions: 該当なし")

    if targets["evolution_logs"]:
        red(f"dsl_evolution_log: {len(targets['evolution_logs'])} 件")
    else:
        yellow("dsl_evolution_log: 該当なし")

    if targets["extensions"]:
        for e in targets["extensions"]:
            red(f"extensions: id={e['id']} name={e['name']!r} category={e['category']!r}")
    else:
        yellow("extensions: 該当なし")

    if targets["files"]:
        for f in targets["files"]:
            red(f"file: {f}")
    else:
        yellow("files: 該当なし")

    if targets["dispatch_patches"]:
        for d in targets["dispatch_patches"]:
            red(f"dispatch patch: {d['file']} の if problem_class == \"{d['problem_class']}\": ブロック")
    else:
        yellow("dispatch patch: 該当なし")

    if targets["tag_map_entries"]:
        for t in targets["tag_map_entries"]:
            red(f"TAG_MAP: {t['file']} の {t['problem_class']} エントリ")
    else:
        yellow("TAG_MAP: 該当なし")

    for b in targets.get("import_blockers", []):
        yellow(f"削除不可（他ファイルがimport）: {b}")


def main() -> None:
    import argparse

    parser = argparse.ArgumentParser(
        description="指定ドメインの登録・ファイルを全削除する",
    )
    parser.add_argument("domain_name", help="ドメイン名（snake_case または PascalCase）")
    parser.add_argument("--dry-run", action="store_true", help="削除対象を表示するだけで実行しない")
    parser.add_argument("--yes", action="store_true", help="確認プロンプトをスキップして即削除")
    parser.add_argument(
        "--fuzzy", action="store_true",
        help="部分一致(LIKE)も対象にする（既定は完全一致のみ）。"
             "scenarios.domain / dsl_definitions.problem_class の表記がテーブル間で"
             "完全一致しないドメイン（例: 'yard' vs 'YardPlanning'）を削除する場合のみ指定し、"
             "必ず一致リストを目視確認すること。他ドメインの巻き込み削除の原因になり得る。"
    )
    args = parser.parse_args()

    head(f"ドメイン削除: {args.domain_name}" + ("（--fuzzy: 部分一致あり）" if args.fuzzy else "（完全一致のみ）"))
    targets = collect_deletion_targets(args.domain_name, fuzzy=args.fuzzy)
    _print_targets(targets)

    if targets.get("import_blockers"):
        yellow(f"削除対象のファイルを他の{len(targets['import_blockers'])}ファイルがモジュール先頭でimportしているため、"
               "削除すると他のファイルのimportが壊れバックエンドが起動しなくなります。削除を中止します。")
        return

    total = (len(targets["scenarios"]) + len(targets["dsl_definitions"])
             + len(targets["evolution_logs"]) + len(targets["extensions"]) + len(targets["files"])
             + len(targets["dispatch_patches"]) + len(targets["tag_map_entries"]))
    if total == 0:
        cyan("削除対象が見つかりませんでした。ドメイン名の表記を確認してください"
             "（snake_case / PascalCaseの違いや、テーブル間の表記揺れにより完全一致しない場合は"
             " --fuzzy を検討。ただし他ドメインの巻き込みに注意して結果を必ず確認すること）。")
        return

    if args.dry_run:
        cyan("--dry-run 指定のため実際の削除は行いません。")
        return

    if args.fuzzy:
        yellow("--fuzzy 指定: 上記の一致リストに無関係なドメインが混入していないか、"
               "必ず目視確認してから続行してください。")

    if not args.yes:
        answer = input("\n上記を本当に削除しますか？ (y/N): ").strip().lower()
        if answer != "y":
            yellow("キャンセルしました。")
            return

    result = delete_domain_api(args.domain_name, fuzzy=args.fuzzy)
    head("削除完了")
    green(f"scenarios: {result['scenarios']} 件")
    green(f"dsl_definitions: {result['dsl_definitions']} 件")
    green(f"evolution_logs: {result['evolution_logs']} 件")
    green(f"extensions: {result['extensions']} 件")
    for f in result["files"]:
        green(f"file: {f}")
    if not result["files"]:
        cyan("削除されたファイルはありませんでした。")
    for d in result["dispatch_patches"]:
        green(f"dispatch patch除去: {d}")
    if not result["dispatch_patches"]:
        cyan("除去したディスパッチパッチはありませんでした。")
    for t in result["tag_map_entries"]:
        green(f"TAG_MAPエントリ除去: {t}")
    if not result["tag_map_entries"]:
        cyan("除去したTAG_MAPエントリはありませんでした。")


if __name__ == "__main__":
    main()
