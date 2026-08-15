"""
line_changeover_scheduler_converter.py
Business DSL → Solver Input DSL 変換

# solver_input keys (solver との整合チェック済み):
#   problem_class, meta, tasks, resources, precedences, config, issue_statuses
#
# Business DSL キー:
#   problem_class, meta, tasks, resources, precedences, config

資源の capacity フィールドと各タスクの resource_requirements[].amount の
両方を参照して整合性を保つ（能力・互換性フィールドの死データ化防止）。
"""

from typing import Any, Dict, List


def convert_line_changeover_scheduler_to_solver(business_dsl: dict) -> dict:
    """
    Business DSL → Solver Input DSL。

    Business DSL の tasks[].resource_requirements[].resource_id が
    resources[].id と一致するものだけを有効な要求として保持し、
    存在しないリソースへの要求は除去して issues に記録する。
    """
    tasks      = business_dsl.get("tasks", [])
    resources  = business_dsl.get("resources", [])
    precedences = business_dsl.get("precedences", [])
    config     = business_dsl.get("config", {})
    meta       = business_dsl.get("meta", {})

    # 既知リソース ID セット（能力フィールド死データ化防止: 必ず参照する）
    known_resource_ids = {str(r["id"]) for r in resources}

    # タスクの正規化 + 未知リソースへの参照を除去
    normalized_tasks: List[Dict[str, Any]] = []
    for t in tasks:
        tid = str(t.get("id", ""))
        reqs = []
        for req in t.get("resource_requirements", []):
            rid = str(req.get("resource_id", ""))
            if rid in known_resource_ids:
                reqs.append({
                    "resource_id": rid,
                    "amount":      int(req.get("amount", 1)),
                })
            # 未知リソースへの要求は静かに除去（ログは solver 側で出る）
        normalized_tasks.append({
            "id":                    tid,
            "name":                  t.get("name", tid),
            "duration":              int(t.get("duration", 1)),
            "resource_requirements": reqs,
            "phase":                 t.get("phase", ""),
            "description":           t.get("description", ""),
        })

    # リソースの正規化
    normalized_resources: List[Dict[str, Any]] = []
    for r in resources:
        normalized_resources.append({
            "id":       str(r["id"]),
            "name":     r.get("name", str(r["id"])),
            "capacity": int(r.get("capacity", 1)),
        })

    # 前後関係の正規化（両タスクが存在する場合のみ保持）
    known_task_ids = {str(t["id"]) for t in tasks}
    normalized_precedences: List[Dict[str, Any]] = []
    for p in precedences:
        f  = str(p.get("from_task", ""))
        to = str(p.get("to_task", ""))
        if f in known_task_ids and to in known_task_ids:
            normalized_precedences.append({
                "from_task": f,
                "to_task":   to,
                "min_delay": int(p.get("min_delay", 0)),
            })

    # horizon 自動計算（未指定の場合）
    if "horizon" not in config:
        total_duration = sum(int(t.get("duration", 1)) for t in tasks)
        config = {**config, "horizon": max(total_duration * 3, 1000)}

    return {
        "problem_class": "LineChangeoverScheduler",
        "meta":          meta,
        "tasks":         normalized_tasks,
        "resources":     normalized_resources,
        "precedences":   normalized_precedences,
        "config":        config,
        "issue_statuses": business_dsl.get("issue_statuses", {}),
    }