#!/usr/bin/env python3
"""
export_domain.py — 指定ドメインをDBから抽出し、配布用シードJSONとして書き出す

設計の経緯（2026-07-20、docs/DESIGN_2026-07-20_environment_distribution_strategy.md 参照）:
  - 配布方式は「案A: コード全同梱 + DB選択シード」。コード(solver.py/converter.py等)は
    全ドメイン分そのままgit配布し、DBだけを選択ドメインでシードする。
    そのため本スクリプトはDB行のみを対象にし、コードファイルは一切扱わない。
  - dsl_evolution_log は対象外（顧客環境の活動履歴を自動収集しない方針。
    repository.py の EVOLUTION_LOG_ENABLED と同じ理由）。
  - ドメイン名(snake_case)とproblem_class(PascalCase)の対応は domain_registry.py に
    一元化されている（"yard"->"YardPlanning"のような非機械変換ケースがあるため）。
  - extensions は「dsl_definitions.extensions配列で参照されている名前」と
    「extensions.category == problem_class の行」の和集合を対象にする
    （サンドボックス検証で、現状の6ドメイン全てで過不足なく求まることを確認済み）。
  - dsl_definitions はproblem_class配下の最新バージョンのみを対象にする
    （現状は全ドメインversion="1.0"のみで実害はないが、将来複数バージョンが
    増えた場合は文字列比較でのORDER BY DESCが崩れる可能性がある点に注意。
    その場合はrepository.get_latest_dsl_version()同等のロジックを見直すこと）。

使い方:
  cd Backend/dsl_repository
  python3 export_domain.py yard
  python3 export_domain.py nurse_shift_weekly_cap --output-dir domain_seeds
  python3 export_domain.py truck_dispatcher --include-inactive
"""

import argparse
import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

from domain_registry import domain_key_to_problem_class, normalize_domain_key


def _get_connection(db_path: Path) -> sqlite3.Connection:
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    return conn


def export_domain(domain_key: str, db_path: Path, include_inactive: bool = False) -> dict:
    # 2026-07-25: domain_key に PascalCase（problem_class）が誤って渡された場合の
    # サイレントな scenarios 0件バグを防ぐため、必ず snake_case へ正規化してから使う。
    # 元の入力と異なっていれば呼び出し側（main）で警告表示する。
    normalized_key = normalize_domain_key(domain_key)
    domain_key = normalized_key
    problem_class = domain_key_to_problem_class(domain_key)
    conn = _get_connection(db_path)

    # --- scenarios ---
    if include_inactive:
        scen_rows = conn.execute(
            "SELECT name, description, tag, tag_color, dsl_json, is_active "
            "FROM scenarios WHERE domain = ? ORDER BY id",
            (domain_key,),
        ).fetchall()
    else:
        scen_rows = conn.execute(
            "SELECT name, description, tag, tag_color, dsl_json, is_active "
            "FROM scenarios WHERE domain = ? AND is_active = 1 ORDER BY id",
            (domain_key,),
        ).fetchall()
    scenarios = [
        {
            "name": r["name"],
            "description": r["description"],
            "tag": r["tag"],
            "tag_color": r["tag_color"],
            "dsl_json": json.loads(r["dsl_json"]),
            "is_active": bool(r["is_active"]),
        }
        for r in scen_rows
    ]

    # --- dsl_definitions（最新バージョンのみ） ---
    dsl_row = conn.execute(
        "SELECT version, extensions, schema_json, description "
        "FROM dsl_definitions WHERE problem_class = ? ORDER BY version DESC LIMIT 1",
        (problem_class,),
    ).fetchone()

    dsl_definition = None
    referenced_extension_names: list[str] = []
    if dsl_row is not None:
        referenced_extension_names = json.loads(dsl_row["extensions"])
        dsl_definition = {
            "problem_class": problem_class,
            "version": dsl_row["version"],
            "extensions": referenced_extension_names,
            "schema_json": json.loads(dsl_row["schema_json"]),
            "description": dsl_row["description"],
        }

    # --- extensions（和集合方式: 配列参照 + category一致） ---
    category_rows = conn.execute(
        "SELECT name FROM extensions WHERE category = ?", (problem_class,)
    ).fetchall()
    category_owned_names = [r["name"] for r in category_rows]

    all_names = sorted(set(referenced_extension_names) | set(category_owned_names))
    extensions = []
    if all_names:
        placeholders = ",".join("?" * len(all_names))
        ext_rows = conn.execute(
            f"SELECT name, category, schema_fragment, solver_mapping, ui_mapping, description "
            f"FROM extensions WHERE name IN ({placeholders})",
            all_names,
        ).fetchall()
        for r in ext_rows:
            extensions.append({
                "name": r["name"],
                "category": r["category"],
                "schema_fragment": json.loads(r["schema_fragment"]) if r["schema_fragment"] else {},
                "solver_mapping": json.loads(r["solver_mapping"]) if r["solver_mapping"] else None,
                "ui_mapping": json.loads(r["ui_mapping"]) if r["ui_mapping"] else None,
                "description": r["description"],
            })

    conn.close()

    return {
        "domain_key": domain_key,
        "problem_class": problem_class,
        "exported_at": datetime.now(timezone.utc).isoformat(),
        "dsl_definition": dsl_definition,
        "extensions": extensions,
        "scenarios": scenarios,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("domain_key", help="ドメイン名（snake_case。scenarios.domainの値）")
    parser.add_argument("--db", default=None, help="DBファイルパス（省略時はこのファイルと同階層のoptibuddy.db）")
    parser.add_argument("--output-dir", default=None, help="出力先ディレクトリ（省略時はdomain_seeds/）")
    parser.add_argument("--include-inactive", action="store_true", help="is_active=0のシナリオも含める")
    args = parser.parse_args()

    db_path = Path(args.db) if args.db else Path(__file__).parent / "optibuddy.db"
    if not db_path.exists():
        raise SystemExit(f"DBファイルが見つかりません: {db_path}")

    output_dir = Path(args.output_dir) if args.output_dir else Path(__file__).parent / "domain_seeds"
    output_dir.mkdir(parents=True, exist_ok=True)

    normalized_key = normalize_domain_key(args.domain_key)
    if normalized_key != args.domain_key:
        print(
            f"⚠ domain_key を正規化しました: '{args.domain_key}' -> '{normalized_key}' "
            f"（scenarios.domain は snake_case のため）"
        )

    result = export_domain(args.domain_key, db_path, include_inactive=args.include_inactive)

    output_path = output_dir / f"{normalized_key}.json"
    with open(output_path, "w", encoding="utf-8") as f:
        json.dump(result, f, indent=2, ensure_ascii=False)

    print(f"エクスポート完了: {output_path}")
    print(f"  problem_class: {result['problem_class']}")
    print(f"  dsl_definition: {'あり (v' + result['dsl_definition']['version'] + ')' if result['dsl_definition'] else 'なし'}")
    print(f"  extensions: {len(result['extensions'])} 件 ({[e['name'] for e in result['extensions']]})")
    print(f"  scenarios: {len(result['scenarios'])} 件")
    if len(result["scenarios"]) == 0:
        print(
            "  ⚠ scenarios が0件です。domain_key の指定が正しいか、"
            "対象ドメインにシナリオが登録済みか確認してください。"
        )


if __name__ == "__main__":
    main()
