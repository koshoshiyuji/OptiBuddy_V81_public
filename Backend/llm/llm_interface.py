# llm/llm_interface.py  (V8: マルチプロバイダー対応)
import json
import os
import re

try:
    from dotenv import load_dotenv
    load_dotenv()
except Exception as e:
    print(f"Warning: .env の読み込みに失敗しました: {e}")

from llm.llm_client import call_llm_json, call_stage1_tool, default_model, fast_model, extract_json, is_available, get_provider

MODEL = default_model()  # 後方互換のためモジュールレベルで保持


# --- Utility ---
def _extract_json(text: str):
    """後方互換のためのラッパー（実体は llm_client.extract_json）"""
    return extract_json(text)


# call_llm_json は llm_client からそのままインポート済み。
# 呼び出し側の model 引数が文字列を渡す場合も llm_client 側で受け取る。


# --- 既存関数 ---
def generate_fjsp_dsl(instruction: str) -> dict:
    """新規FJSP DSL生成"""
    messages = [
        {
            "role": "system",
            "content": (
                "You are an expert in flexible job shop scheduling. "
                "Generate a valid FJSP DSL JSON according to the user's instruction. "
                "Return JSON only with keys: 'instance_name', 'problem', 'objective', 'jobs', 'machines'."
            ),
        },
        {"role": "user", "content": f"Instruction: {instruction}\nReturn JSON only."},
    ]
    return call_llm_json(messages)


def update_dsl_with_query(current_dsl: dict, query: str) -> dict:
    """既存DSLをLLMで更新（V3用安全版）"""
    messages = [
        {
            "role": "system",
            "content": (
                "You are an assistant that edits a Flexible Job Shop DSL JSON strictly according to user instructions. "
                "Return JSON only with keys: 'instance_name', 'problem', 'objective', 'jobs', 'machines'."
            ),
        },
        {"role": "user", "content": f"Current DSL:\n{json.dumps(current_dsl, ensure_ascii=False)}"},
        {"role": "user", "content": f"Apply modification: {query}\nReturn updated JSON only."},
    ]
    llm_result = call_llm_json(messages)
    for job in llm_result.get("jobs", []):
        for task in job.get("tasks", []):
            task.setdefault("alternatives", [])
    return llm_result


def generate_llm_suggestions(current_dsl: dict, objective: str = "makespan最小化"):
    """
    DSLを分析し、改善提案を日本語でリスト形式で返す。
    """
    messages = [
        {
            "role": "system",
            "content": (
                "あなたはOptiBuddyというスケジューリング最適化AIのエキスパートです。"
                "以下のDSL(JSON形式)を分析し、スケジュール改善のための提案を**日本語で3件以上**生成してください。"
                "出力は必ずJSON配列形式のみで、他の文章やマークダウンは絶対に含めないこと。\n"
                "各提案には必ず 'natural' (人間が理解できる日本語説明)、"
                "'dsl_patch' (JSON Pointer形式でDSL変更を記述)、"
                "'expected_effect' (改善効果の概要) を含めること。"
            ),
        },
        {
            "role": "user",
            "content": (
                f"以下のDSLを解析し、Objective={objective} に寄与する改善提案を生成してください。\n"
                f"--- DSL ---\n{json.dumps(current_dsl, ensure_ascii=False, indent=2)}"
            ),
        }
    ]

    try:
        raw = call_llm_json(messages, model=MODEL, temperature=1.0)
    except Exception as e:
        print(f"[generate_llm_suggestions] failed: {e}")
        return [{"natural": "LLM呼び出しエラー", "dsl_patch": [], "expected_effect": "(効果未記載)"}]

    suggestions = []
    try:
        if isinstance(raw, list):
            for s in raw:
                if not isinstance(s, dict):
                    continue
                suggestions.append({
                    "natural":         s.get("natural", "").strip() or "(説明なし)",
                    "dsl_patch":       s.get("dsl_patch", []) or [],
                    "expected_effect": s.get("expected_effect", "").strip() or "(効果未記載)"
                })
        elif isinstance(raw, dict):
            suggestions = [{
                "natural":         raw.get("natural", "").strip() or "(説明なし)",
                "dsl_patch":       raw.get("dsl_patch", []) or [],
                "expected_effect": raw.get("expected_effect", "").strip() or "(効果未記載)"
            }]
        else:
            suggestions = [{"natural": str(raw), "dsl_patch": [], "expected_effect": "(効果未記載)"}]
    except Exception as e:
        print("[llm_interface] suggestion normalization failed:", e)
        suggestions = [{"natural": "LLM出力エラー", "dsl_patch": [], "expected_effect": "(効果未記載)"}]

    return suggestions


# --- /ask エンドポイント用：DSLを文脈にした質問応答 ---

DSL_EXPERT_SYSTEM_PROMPT = """
あなたはコンテナターミナルの作業スケジューリング最適化AI「OptiBuddy」です。
ユーザーからの質問に対して、提供されたDSL（スケジュール定義）と最適化結果を参照して回答します。

## DSLの主要構造
- containers: コンテナ一覧（id, yard位置, ship位置, order等）
- tasks: 作業タスク（operation: PICK/MOVE/LOAD/REHANDLE, resource: クレーン名, start/end）
- issues: 検出された問題（type: IS_YARD/IS_SHIP/MBP等, severity）
- relations: タスク間の依存関係

## solutionの構造
- solutions: 最適化プラン一覧（Plan A/B/C）
  - tasks[]: スケジュールされた作業リスト
    - id: タスクID（例: C01_LOAD_380）
    - containerId: 対象コンテナ
    - operation: PICK/MOVE/LOAD/REHANDLE
    - domain: YARD（ヤード作業）/ VESSEL（船積み作業）
    - resource / resourceId: 使用クレーン（RC-1=ヤードクレーン, GC-1=ガントリークレーン）
    - start / end: 開始・終了時刻（秒）
    - duration: 所要時間（秒）
    - from / to: 移動元・移動先 {bay, row, tier}
    - order: 積み付け順序（1が先）
  - makespan: 全作業完了時刻（秒）
  - penalty: 制約違反ペナルティ

## ★ 権限ルール（最重要：必ず守ること）

### 船主権限（変更禁止）
以下のフィールドはいかなる理由があっても dsl_patch に含めてはならない。
ユーザーから変更を求められても「船主権限のため変更できません」と説明すること。

- containers[*].ship.*（船内配置：bay/row/tier）
- containers[*].order（積み付け順序）

### 港側権限（変更可）
以下のフィールドのみ dsl_patch での変更を提案できる。

- containers[*].yard.*（ヤード配置：bay/row/tier）
- resources.yard_cranes[*].bay（ヤードクレーンの担当ベイ）
- resources.ship_cranes[*].bay（シップクレーンの担当ベイ）
- config.*（作業時間・ペナルティ等の設定値）

## 回答ルール
1. 必ずJSON形式のみで返すこと。マークダウンや説明文を含めないこと。
2. explanation: ユーザーが理解できる日本語で、DSLの具体的な数値を引用して説明すること。
3. dsl_patch: 修正提案がある場合のみJSON Patch形式で返す。なければ空配列 []。
4. can_optimize: 以下の「再最適化で改善できる変更」に該当する場合のみtrueにすること。
5. root_cause: 問題の根本原因を1行で。ユーザーが理解できる言葉で書くこと。

## DSLフィールドの定義（必ず守ること）
- `config.time_limit`: CP Optimizerソルバーの**実行時間上限（秒）**。デフォルト20秒。
  オペレーションの作業時間ではない。「time_limitが短い」＝ソルバーの探索時間が短い。
- `config.safety_gap`: タスク間の最小インターバル（秒）
- `makespan`: 全作業の完了時刻（秒）。オペレーション全体の所要時間。

## 用語ルール（必ず守ること）
- IS_SHIP、IS_YARD、MBP、issue_id などの内部コード名をexplanationやroot_causeに含めないこと。
- 代わりに以下の表現を使うこと：
  - IS_SHIP → 「船会社のMaster Bay Plan（積み付け計画書）に積み順の矛盾がある」
  - IS_YARD → 「ヤードでコンテナの積み順が逆になっており、取り出しのために仮置き作業が発生する」
  - REHANDLE → 「仮置き作業」または「取り出しのための移動」
  - makespan → 「全作業の完了時間」
  - order → 「積み付け順序」

## 回答の優先順位
- issuesにMaster Bay Planの矛盾（type: IS_SHIP）が含まれている場合、数値の分析より先に
  「船会社のMaster Bay Planに積み順の矛盾があるため、この問題はシステムで自動修正できません。
  船会社への確認・承認が必要です」と最初に伝えること。その後に補足説明を続けてよい。
- 回答は簡潔にすること。数値の羅列より「なぜ・どうすればよいか」を優先すること。

## 再最適化で改善できる変更（can_optimize: true にして良い）
- コンテナのヤード位置（yard.bay/row/tier）の変更 → リハンドリング削減につながる
- ヤードクレーン・シップクレーンの担当ベイ変更 → 並列処理によるmakespan短縮につながる
- config.*の作業時間・ペナルティ設定の変更

## 再最適化では解決できない問題（can_optimize: false にすること）
- 船会社のMaster Bay Plan（積み付け計画書）に矛盾がある場合：
  これはシステムで自動修正できません。船会社への確認・承認が必要です（ACCEPTフローで対応）。
- クレーンの移動コスト設定（weight_bay_move等）を変えても解は変わりません：
  現在の最適化はmakespan（全作業完了時間）の短縮のみを対象としており、
  コスト設定は最適化の対象外です。
- コンテナの重量・PORT情報（pol/pod/weight）の変更：
  ソルバーはこれらの値を参照しないため、変更しても結果は変わりません。
- containers[*].ship.* および containers[*].order の変更：
  これらは船主権限のフィールドです。港側では変更できません。

## JSON Patch形式の例（港側権限フィールドのみ）
{"op": "replace", "path": "/containers/0/yard/tier", "value": 1}
{"op": "replace", "path": "/resources/yard_cranes/0/bay", "value": [10, 11]}
{"op": "replace", "path": "/config/safety_gap", "value": 15}
"""


# -------------------------------------------------------
# V7: 2段階呼び出し版 ask_about_dsl
# Stage 1 (Haiku): 質問を解析して必要なタスクデータを特定
# Stage 2 (Sonnet): 絞り込んだデータで回答生成
# -------------------------------------------------------

# FETCH_TASKS_TOOL は llm_client.FETCH_TASKS_TOOL_SCHEMA に定義済み


def _build_task_summary(all_tasks: list) -> dict:
    """全タスクから集計サマリーを生成"""
    resources = {}
    for t in all_tasks:
        r = t.get("resource") or t.get("resourceId", "unknown")
        resources.setdefault(r, {"count": 0, "rehandle": 0})
        resources[r]["count"] += 1
        if t.get("operation") == "REHANDLE":
            resources[r]["rehandle"] += 1

    return {
        "total_tasks": len(all_tasks),
        "rehandle_count": sum(1 for t in all_tasks if t.get("operation") == "REHANDLE"),
        "by_resource": resources,
        "makespan_sec": max((t.get("end", 0) for t in all_tasks), default=0),
    }


def _stage1_identify_filter(question: str, dsl: dict, solution: dict) -> dict:
    """
    Stage 1: 軽量モデルで質問を解析し、必要なタスクフィルタを特定する。
    プロバイダーごとの function calling は llm_client.call_stage1_tool が吸収する。
    """
    if not is_available():
        return {"filter": "summary_only"}

    resources = (
        [c["id"] for c in dsl.get("resources", {}).get("yard_cranes", [])] +
        [c["id"] for c in dsl.get("resources", {}).get("ship_cranes", [])]
    )
    container_ids = [c["id"] for c in dsl.get("containers", [])][:20]

    prompt = (
        f"質問: {question}\n"
        f"利用可能なリソース: {resources}\n"
        f"コンテナID例: {container_ids}\n\n"
        f"**必ず fetch_tasks ツールを使って**、この質問に答えるために必要なタスクデータを指定してください。\n"
        f"- 特定のクレーン/リソースについての質問 → filter='by_resource', resource_id='GC-1'など\n"
        f"- 特定のコンテナについての質問 → filter='by_container', container_id='C07'など\n"
        f"- 全体的な質問や集計のみで答えられる質問 → filter='summary_only'"
    )
    return call_stage1_tool(prompt)


def _stage1_extract_tasks(filter_spec: dict, solution: dict) -> list:
    """フィルタ条件に基づいてPlan A/Bからタスクを抽出"""
    # 防御的処理: filter_spec が辞書でない場合
    if not isinstance(filter_spec, dict):
        print(f"[WARNING] filter_spec is not dict: {type(filter_spec)}")
        return []
    
    all_tasks = []
    for sol in solution.get("solutions", [])[:2]:  # Plan A/B
        all_tasks.extend(sol.get("tasks", []))

    f = filter_spec.get("filter", "summary_only")
    rid = filter_spec.get("resource_id", "")
    cid = filter_spec.get("container_id", "")

    if f == "by_resource" and rid:
        return [t for t in all_tasks if (t.get("resource") or t.get("resourceId", "")) == rid]
    elif f == "by_container" and cid:
        return [t for t in all_tasks if t.get("containerId") == cid]
    else:
        return []  # summary_only: タスク生データは渡さない


def ask_about_dsl(question: str, dsl: dict, solution: dict, history: list = []) -> dict:
    """
    V7: 2段階LLM呼び出しで質問に回答する。
    Stage 1 (Haiku) で必要データを特定 → Stage 2 (Sonnet) で回答生成。
    トークン超過を防ぎつつ質問に関連するタスクのみを渡す。
    """
    try:
        # --- Stage 1: 必要なタスクデータを特定 ---
        filter_spec = _stage1_identify_filter(question, dsl, solution)
        print(f"[ask_about_dsl] stage1 filter: {filter_spec}")

        filtered_tasks = _stage1_extract_tasks(filter_spec, solution)

        # 全タスクのサマリー（常に渡す）
        all_tasks = []
        for sol in solution.get("solutions", [])[:2]:
            all_tasks.extend(sol.get("tasks", []))
        summary = _build_task_summary(all_tasks)

        print(f"[ask_about_dsl] filtered_tasks={len(filtered_tasks)}, total={summary['total_tasks']}")

        # --- Stage 2: 絞り込んだデータで回答生成 ---
        context = {
            "dsl": dsl,
            "solution": {
                "makespan": solution.get("makespan", 0),
                "issues":   solution.get("issues", []),
                "summary":  summary,
                "filtered_tasks": filtered_tasks,  # 質問に関連するタスクのみ
            },
        }

        messages = [
            {"role": "system", "content": DSL_EXPERT_SYSTEM_PROMPT},
            {
                "role": "user",
                "content": (
                    f"## 現在のDSLと最適化結果\n"
                    f"{json.dumps(context, ensure_ascii=False, indent=2)}\n\n"
                    f"以降の質問にはこのDSLと結果を参照して回答してください。"
                ),
            },
            {
                "role": "assistant",
                "content": '{"explanation": "DSLと最適化結果を確認しました。質問をどうぞ。", "dsl_patch": [], "can_optimize": false, "root_cause": ""}',
            },
            *[{"role": h["role"], "content": h["content"]} for h in history[-10:]],
            {
                "role": "user",
                "content": (
                    f"{question}\n\n"
                    f"必ずJSON形式のみで返してください:\n"
                    f'{{"explanation": "...", "dsl_patch": [], "can_optimize": false, "root_cause": "..."}}'
                ),
            },
        ]

        raw = call_llm_json(messages, temperature=0.3, max_tokens=4096)
        if not isinstance(raw, dict):
            raise ValueError("LLM returned non-dict")
        return {
            "explanation":  raw.get("explanation", "(説明なし)"),
            "dsl_patch":    raw.get("dsl_patch", []),
            "can_optimize": bool(raw.get("can_optimize", False)),
            "root_cause":   raw.get("root_cause", ""),
        }

    except Exception as e:
        print(f"[ask_about_dsl] failed: {e}")
        return {
            "explanation":  f"LLM応答エラー: {e}",
            "dsl_patch":    [],
            "can_optimize": False,
            "root_cause":   "LLMエラー",
        }




# --- V7: /analyze エンドポイント用：データ受信時のプロアクティブ分析 ---

ANALYZE_SYSTEM_PROMPT = """
あなたはコンテナターミナルの作業スケジューリング最適化AI「OptiBuddy」です。
渡されたDSL（スケジュール定義）を見て、ユーザーが気づいていない問題や改善機会を
コンサルタントとして先回りして指摘します。

## 出力形式
必ずJSON配列のみを返すこと。マークダウンや説明文を一切含めないこと。

各要素は以下の構造:
{
  "id": "ai_suggest_001",          // 一意ID（ai_suggest_連番）
  "severity": "WARNING",           // "WARNING" | "INFO"
  "title": "短いタイトル（20文字以内）",
  "message": "ユーザーが理解できる具体的な説明。数値を引用すること。",
  "containerId": "C01",            // 関連コンテナID（なければ空文字）
  "relatedContainerIds": [],       // 関連する他のコンテナID
  "dsl_patch": [],                 // 修正提案（JSON Patch形式、なければ空配列）
  "can_optimize": false,           // 再最適化で改善できるならtrue
  "category": "yard_layout"        // "yard_layout" | "resource" | "opportunity" | "risk"
}

## 分析の観点
1. ヤードレイアウトの問題（積み順逆転、リハンドリングが多そうな配置）
2. リソース効率（クレーン担当ベイの偏り、並列化できそうな箇所）
3. 改善機会（コストを入れるとKPIが出せる、この制約を追加すると精度が上がる等）
4. リスク（コンテナ数に対してクレーンが少ない、タイト過ぎるシフト設定等）

## DSLフィールドの定義（必ず守ること）
- `config.time_limit`: CP Optimizerソルバーの**実行時間上限（秒）**。デフォルト20秒。
  オペレーションの作業時間ではない。「time_limitが短い」とはソルバーの探索時間が短いという意味。
- `config.safety_gap`: タスク間の最小インターバル（秒）
- `makespan`: 全作業の完了時刻（秒）。オペレーション全体の所要時間。

## 制約
- 船主権限フィールド（containers[*].ship.*, containers[*].order）は dsl_patch に含めない
- 内部コード名（IS_SHIP, IS_YARD等）をmessageに含めない
- 提案は3件以上5件以内
- 明らかな問題がなければINFO（改善機会）として提案する
"""


def analyze_dsl(dsl: dict) -> list:
    """
    V7: データ受信時にDSLを見てプロアクティブに問題・改善機会を返す。

    Returns:
        list of issue-like dicts with AI suggestions
    """
    messages = [
        {"role": "system", "content": ANALYZE_SYSTEM_PROMPT},
        {
            "role": "user",
            "content": (
                f"以下のDSLを分析し、問題点と改善機会を指摘してください。\n"
                f"--- DSL ---\n{json.dumps(dsl, ensure_ascii=False, indent=2)}\n\n"
                f"JSON配列のみで返してください。"
            ),
        },
    ]

    try:
        raw = call_llm_json(messages, temperature=0.3, max_tokens=2048)
        if not isinstance(raw, list):
            raw = [raw] if isinstance(raw, dict) else []

        results = []
        for i, item in enumerate(raw):
            if not isinstance(item, dict):
                continue
            results.append({
                "id":                 item.get("id", f"ai_suggest_{i+1:03d}"),
                "severity":           item.get("severity", "INFO"),
                "title":              item.get("title", "(タイトルなし)"),
                "message":            item.get("message", ""),
                "containerId":        item.get("containerId", ""),
                "relatedContainerIds": item.get("relatedContainerIds", []),
                "dsl_patch":          item.get("dsl_patch", []),
                "can_optimize":       bool(item.get("can_optimize", False)),
                "category":           item.get("category", "opportunity"),
                "source":             "ai_proactive",  # フロント側でバッジ表示に使う
            })
        return results

    except Exception as e:
        print(f"[analyze_dsl] failed: {e}")
        return []
