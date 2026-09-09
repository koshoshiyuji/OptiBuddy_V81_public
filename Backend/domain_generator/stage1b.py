
import ast
import copy
import difflib
import json
import logging
import py_compile
import re
import shutil
import subprocess
import sys
import time
from pathlib import Path

from . import (
    EXISTING_DOMAINS,
    _ensure_domain_registry_reconciled,
    _to_snake,
)




# ─────────────────────────────────────────────────────────────
# Stage 1b: シナリオ生成
# ─────────────────────────────────────────────────────────────

def _get_schema_example(base_domain: str) -> str:
    _ensure_domain_registry_reconciled()
    path = EXISTING_DOMAINS.get(base_domain)
    if path and Path(path).exists():
        return Path(path).read_text(encoding="utf-8")
    return "{}"


# 2026-07-18: tightシナリオの生成を廃止し、baseline/infeasibleの2シナリオ原則に変更
# （旧: 3シナリオ原則。tightは「際どいが解ける」ケースの設計意図が曖昧になりやすく、
# 登録時間の増加に見合う効果が薄いとKoshoshiが判断したため）。
_STAGE1B_SYSTEM = """\
あなたは業務データをJSON形式に変換する専門家です。
指定されたJSONスキーマに厳密に従ってシナリオデータを生成してください。

ルール:
- スキーマと同じフィールド名・構造を使用
- problem_class と domain はスキーマ例の値を使用
- JSON のみを返すこと（コードブロックマーカー不要）
- 2シナリオ（baseline / infeasible）を生成
- 規模はヒアリング文中に記載された実際の業務規模（例:「30名」「120枠」）に関わらず、
  割り当てられる側・割り当て先ともに概ね5〜15件程度の小規模に収めること。
  この生成データは登録時の構造検証（Gate 2）用であり、本番規模を再現する必要はない。
  本番規模の実データは登録後に別途シナリオとして追加される。

返す形式:
{
  "baseline": { ...スキーマ準拠... },
  "infeasible": { ...解なし... }
}
"""


def generate_scenarios_from_schema(
    domain_name: str,
    base_domain: str,
    classification: dict,
    hearing_texts: list,
    override_problem_class: str | None = None,
) -> dict:
    """
    override_problem_class: パターン3（extension_gapsがある）の場合に渡す。
    指定すると、生成されるJSONのproblem_classをbase_domainではなく
    この値（コピー先の新ドメイン名）に強制する。
    """
    from llm.llm_client import call_llm, extract_json, default_model

    snake            = _to_snake(domain_name)
    schema_example   = _get_schema_example(base_domain)
    hearing_combined = "\n\n---\n\n".join(hearing_texts)
    problem_class_instruction = (
        f'problem_class は "{override_problem_class}" を使用すること（スキーマ例の値をそのまま使用しないこと）。'
        if override_problem_class else
        ""
    )

    user_prompt = f"""## 業務名
{domain_name}（snake_case: {snake}）

## 分類結果
- 基底ドメイン: {base_domain}
- シナリオ名: {classification.get('scenario_name_jp', domain_name)}
- 説明: {classification.get('scenario_description_jp', '')}

## ヒアリング内容
{hearing_combined}

## 従うべきJSONスキーマ例（このフィールド構造に厳密に従うこと）
{schema_example}

---
上記スキーマに従い2シナリオを生成してください。
- baseline: 標準的なケース
- infeasible: 制約過剰で解なし

{problem_class_instruction}

JSON のみを返すこと。コードブロックマーカー（```json）は不要。
"""
    messages = [
        {"role": "system", "content": _STAGE1B_SYSTEM},
        {"role": "user",   "content": user_prompt},
    ]
    raw    = call_llm(messages, model=default_model(), max_tokens=16000)
    result = extract_json(raw)

    scenarios = {
        "baseline":   result.get("baseline",   {}),
        "infeasible": result.get("infeasible", {}),
    }

    # override_problem_class指定時は、LLMが指示を守らずスキーマ例の値をコピーしてしまった場合に備え、
    # 事後で確実にproblem_class/domainを上書きする（ルーティング先のすれを確実に防ぐ）。
    if override_problem_class:
        for sc in scenarios.values():
            if isinstance(sc, dict) and sc:
                sc["problem_class"] = override_problem_class
                sc["domain"]        = snake

    tag       = (domain_name[:6]).upper()
    tag_color = _domain_color(base_domain)
    name_jp   = classification.get("scenario_name_jp", domain_name)

    scenario_registrations = [
        {"name": f"{name_jp} — 標準",   "description": f"{classification.get('scenario_description_jp','')} (標準)",       "tag": tag, "tag_color": tag_color, "domain": snake, "file": f"Backend/dsl_repository/scenarios/{snake}_baseline.json"},
        {"name": f"{name_jp} — 解なし", "description": f"{classification.get('scenario_description_jp','')} (Infeasible)", "tag": tag, "tag_color": tag_color, "domain": snake, "file": f"Backend/dsl_repository/scenarios/{snake}_infeasible.json"},
    ]

    return {"status": "ok", "scenarios": scenarios,
            "scenario_registrations": scenario_registrations, "base_domain": base_domain}


def _validate_scenario_capacity(scenarios: dict) -> list[str]:
    """
    CVRP系（customers/vehicles/demand_kg/capacity_kgを持つ）のシナリオについて、
    単一顧客の需要が車両の最大積載量を超えていないか機械的に検証する。

    実際にTruckDispatcherのbaselineシナリオで、本来複数トラックに分載すべき
    大口貨物が1顧客ノードに集約されてしまい、常に未割り当てになる不具合が
    発生したことを受けた再発防止の安全網。CVRP系以外のスキーマ（customers/vehiclesが
    ない）では何も検出せず空リストを返す。
    """
    warnings = []
    for suffix, sc in scenarios.items():
        if not isinstance(sc, dict):
            continue
        customers = sc.get("customers")
        vehicles  = sc.get("vehicles")
        if not isinstance(customers, list) or not isinstance(vehicles, list):
            continue
        max_capacity = max((v.get("capacity_kg", 0) for v in vehicles if isinstance(v, dict)), default=0)
        if max_capacity <= 0:
            continue
        over = [(c.get("id", "?"), c.get("demand_kg", 0)) for c in customers
                if isinstance(c, dict) and c.get("demand_kg", 0) > max_capacity]
        if over:
            names = ", ".join(f"{cid}({demand}kg)" for cid, demand in over[:5])
            more = "..." if len(over) > 5 else ""
            warnings.append(
                f"（データ検証）{suffix}シナリオに、車両の最大積載量（{max_capacity}kg）を超える"
                f"需要を持つ顧客が{len(over)}件あります: {names}{more}。"
                f"どの車両にも物理的に割り当てられず、常に未割り当てになります。"
                f"実際には複数便に分割して配送すべき貨物が1顧客ノードに集約されていないか確認してください。"
            )
    return warnings


def _domain_color(base_domain: str) -> str:
    return {"YardPlanning": "#3b82f6",
            "TruckDispatcher": "#f97316",
            "NurseShiftWeeklyCap": "#ec4899",
            "LineChangeoverScheduler": "#10b981"}.get(base_domain, "#8b5cf6")
