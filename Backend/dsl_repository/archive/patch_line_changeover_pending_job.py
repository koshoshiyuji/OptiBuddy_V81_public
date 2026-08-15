"""
patch_line_changeover_pending_job.py

domain_jobs テーブルに保留中の LineChangeoverScheduler 生成コード（job_id指定）に対し、
2026-07-15の診断で判明した3件の不具合（①mdl.add()漏れ、③feasible未伝播）を修正する。

- ①②の根本原因（mdl.minimize()がmdl.add()で包まれていない）
- ③の根本原因（ソルブ失敗時にトップレベルfeasibleが伝播しない）

実行方法:
    cd Backend/dsl_repository
    python3 patch_line_changeover_pending_job.py <job_id> --dry-run
    python3 patch_line_changeover_pending_job.py <job_id> --yes
"""
import argparse
import json
import sqlite3
import sys
from pathlib import Path

DB_PATH = Path(__file__).parent / "optibuddy.db"
TARGET_PATH = "Backend/solvers/line_changeover_scheduler_solver.py"

REPLACEMENTS = [
    (
        '        # 目的関数: メイクスパン最小化（ヒアリング目的関数 項1）\n'
        '        makespan_expr = mdl.max([mdl.end_of(iv) for iv in task_itvs.values()])\n'
        '        mdl.minimize(makespan_expr)\n',
        '        # 目的関数: メイクスパン最小化（ヒアリング目的関数 項1）\n'
        '        makespan_expr = mdl.max([mdl.end_of(iv) for iv in task_itvs.values()])\n'
        '        mdl.add(mdl.minimize(makespan_expr))\n',
    ),
    (
        '            return None, [issue]\n',
        '            infeasible_solution = {\n'
        '                "feasible": False,\n'
        '                "schedule": [],\n'
        '                "makespan": None,\n'
        '                "kpi": {"makespan": None, "task_count": 0, "objective_value": None, "solve_time": 0.0},\n'
        '            }\n'
        '            return infeasible_solution, [issue]\n',
    ),
    (
        '        issues = self._validate(tasks, resources)\n'
        '        if any(i["severity"] == "CRITICAL" for i in issues):\n'
        '            return self._make_result("ok", [], issues, meta)\n',
        '        issues = self._validate(tasks, resources)\n'
        '        if any(i["severity"] == "CRITICAL" for i in issues):\n'
        '            return self._make_result("ok", [], issues, meta, feasible=False)\n',
    ),
    (
        '        solutions = [solution_data] if solution_data else []\n'
        '        status = "ok" if solution_data and solution_data.get("feasible") else "ok"\n'
        '        # feasible=False でも status:"ok" で返し、issues で CRITICAL 通知\n'
        '        return self._make_result(status, solutions, issues, meta)\n',
        '        solutions = [solution_data] if solution_data else []\n'
        '        feasible  = bool(solution_data.get("feasible")) if solution_data else False\n'
        '        # feasible=False でも status:"ok" で返し、issues で CRITICAL 通知。\n'
        '        # feasible はトップレベルにも明示する（Gate2動的検証は result.get("feasible")\n'
        '        # をトップレベルから読むため）。\n'
        '        return self._make_result("ok", solutions, issues, meta, feasible=feasible)\n',
    ),
    (
        '    def _make_result(\n'
        '        self,\n'
        '        status: str,\n'
        '        solutions: List[dict],\n'
        '        issues: List[dict],\n'
        '        meta: dict,\n'
        '    ) -> dict:\n'
        '        return {\n'
        '            "status":           status,\n',
        '    def _make_result(\n'
        '        self,\n'
        '        status: str,\n'
        '        solutions: List[dict],\n'
        '        issues: List[dict],\n'
        '        meta: dict,\n'
        '        feasible: bool = False,\n'
        '    ) -> dict:\n'
        '        return {\n'
        '            "status":           status,\n'
        '            "feasible":         feasible,\n',
    ),
]


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("job_id")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--yes", action="store_true")
    args = parser.parse_args()

    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()
    cur.execute("SELECT data FROM domain_jobs WHERE job_id=?", (args.job_id,))
    row = cur.fetchone()
    if row is None:
        print(f"job_id {args.job_id} が見つかりません")
        sys.exit(1)

    d = json.loads(row[0])
    diffs = d.get("pending", {}).get("diffs", [])
    target = next((diff for diff in diffs if diff["path"] == TARGET_PATH), None)
    if target is None:
        print(f"{TARGET_PATH} の diff が見つかりません")
        sys.exit(1)

    content = target["new_content"]
    applied = 0
    for old, new in REPLACEMENTS:
        if old in content:
            content = content.replace(old, new, 1)
            applied += 1
        else:
            print(f"[警告] 置換対象が見つかりませんでした（既に適用済み、または文言が想定と違う可能性）:\n{old[:80]}...")

    print(f"適用できた置換: {applied} / {len(REPLACEMENTS)}")

    if args.dry_run:
        print("--dry-run のため書き込みは行いません。")
        return

    if not args.yes:
        print("--yes を指定してください（確認なしで書き込み）")
        return

    target["new_content"] = content
    cur.execute("UPDATE domain_jobs SET data=? WHERE job_id=?", (json.dumps(d, ensure_ascii=False), args.job_id))
    conn.commit()
    print("書き込み完了。")


if __name__ == "__main__":
    main()
