# llm/relax_interface.py  (V2: マルチプロバイダー対応)
# Infeasible 緩和候補を LLM に生成させる

import json
import os

try:
    from dotenv import load_dotenv
    load_dotenv()
except Exception:
    pass

from llm.llm_client import call_llm_json, extract_json, is_available, default_model

MODEL = default_model()  # 後方互換


def _extract_json(text: str):
    """後方互換ラッパー"""
    return extract_json(text)
 
 
RELAX_SYSTEM_PROMPT = """
あなたはスケジューリング最適化の専門家AIです。
ソルバーが「実行可能解なし（Infeasible）」を返した原因を分析し、
最小限の制約緩和で Feasible にする方法を提案します。
 
## 重要ルール
1. 必ずJSON配列のみを返すこと。マークダウン・説明文を含めないこと。
2. dsl_patch には DSL に**実際に存在するフィールドのパスのみ**を使うこと。
   存在しないパスへのパッチは無効になる。DSLの構造をよく確認してから生成すること。
3. 緩和案の件数は固定しない。DSLの変更で解決可能な原因を全て洗い出し、
   原因ごとに1件の緩和案を生成すること（3件などの上限を設けない）。
   - 同一原因に対する重複提案はしないこと。
   - 同じ理由で複数の対象（顧客・コンテナ・スタッフ等）が影響を受けている場合は、
     まとめて1件の緩和案にしてよい（例: 該当車種全体の capacity_kg を一律引き上げ）。
   - 原因が異なる場合は、原因ごとに別の緩和案に分けること。
   - 単一制約の緩和で解けない可能性がある場合のみ、複数制約を組み合わせた
     案を追加で出すこと（例: シフト延長 + safety_gap縮小、人数緩和 + シフト拡大）。
   - **DSLの変更では解決しない原因（探索不足など。下記「diagnostic / unserved_breakdown
     フィールドの読み方」を参照）には dsl_patch を生成しないこと。** 該当する原因は
     緩和案の対象外とし、出力しない。
4. 「ハード制約→ソフト制約化」「必要数の引き下げ」「時間枠の拡大」「safety_gap縮小」を優先して検討すること。
5. tradeoff には緩和によって失われるものを具体的に書くこと。
6. dsl_patch の各 op には、必ずトップレベルに "label" フィールド（人間向けの短い日本語説明）を
   付けること。value が既に決まっている op（後述「値を計算できる場合」）でも省略しないこと。
   "label" には "/customers/14/restricted_vehicle_types" のような生のDSLパスをそのまま
   使わず、"顧客14の使用可能車両タイプ" のように業務用語で言い換えること
   （UIやチャット画面にそのまま表示されるため、DSLの内部構造を知らないユーザーにも
   意味が伝わる書き方にすること）。

## diagnostic / unserved_breakdown フィールドの読み方
イシューには、ソルバーが収集した構造化診断情報が "diagnostic" または
ドメイン固有のフィールド名（例: TruckDispatcher の "unserved_breakdown"）で
含まれることがある。値の生成にはこれらのフィールドを最優先で参照し、
DSL全体を目視で推測することは避けること。

### diagnostic フィールド（YardPlanning 等）
- "reason": infeasible になった原因の要約（人間向けの自然文。値の計算には使わないこと）
- "details": ドメイン固有の詳細データ（以下の形式を取ることがある）

  - yard ドメイン（クレーン未到達）:
    [{container_id, reason, yard_bay, ship_bay, resource_type, target_bay, candidate_resources}]
    → resource_type は "yard_crane" または "ship_crane"。candidate_resources は
      [{id, bay}, ...] の形式で、該当リソース種別の全クレーンについて、現在の
      担当bay一覧を持つ。target_bay をカバーしていないクレーンの中から、
      target_bay に最も近い bay を担当しているものを1つ選び、
      resources.yard_cranes（または ship_cranes）配列のうち id が一致する要素の
      bay 配列に target_bay を追加する dsl_patch を生成すること。
      **reason の自然文中の「担当ベイ: [...]」を目視でパースしないこと。
      必ず candidate_resources の構造化データを使うこと。**

  - yard ドメイン（シフト制約違反）:
    [{container_id, reason, yard_bay, ship_bay, task_start_min, task_end_min,
      crane_id, shift_index}]
    → task_end_min がシフト終了時刻を超えている。crane_id は違反したクレーンの
      ID、shift_index は config.shifts 配列内の該当シフトのインデックス
      （0始まり）。**reason の自然文からクレーン名を読み取って
      config.shifts の何番目かを推測する必要はない。必ず shift_index
      フィールドの値をそのまま配列インデックスとして使うこと。**
      config.shifts[shift_index].end を task_end_min 以上の値に replace する
      ことで解消できる。

    ★ task_end_min 最大値ルール（絶対厳守）:
      1. diagnostic.details を shift_index の値ごとにグループ化する
         （reason の自然文ではなく、必ず shift_index フィールドで判定すること）。
      2. 各 shift_index グループ内で task_end_min の「最大値」を X とする。
      3. 該当シフト（config.shifts[shift_index]）の end は必ず X+2 以上に
         設定すること（X のみでは境界値で失敗する）。
      4. shift_index が複数存在する場合、それぞれの shift_index について
         個別に上記2〜3を行い、対応する config.shifts[shift_index].end を
         それぞれ replace する dsl_patch を1つの緩和案にまとめること
         （片方のシフトだけ延長しても解決しないケースが多い）。
      5. 絶対に X-1 や X と同値を設定しないこと。

    ★★ それでも1回の延長で解決しないことがある（重要）:
      あるクレーンのシフトを延長すると、そこで動けるようになったコンテナの
      分だけ別のクレーン側に新しい渋滞が生まれ、diagnostic.details の
      task_end_min（延長前のスケジュールから計算された値）だけでは
      不足することがある。この提案は返す前にサーバー側で実際に
      再ソルブして検証される（Infeasibleのままの案は自動的に除外される）ため、
      X+2ちょうどに揃えるより、多少余裕を持たせた値（迷ったら大きめ）を
      選ぶ方が安全である。

  - EventStaffing ドメイン: [{task_requirements: [...], staff_shifts: [...]}]
    → タスクの required_count・min_chiefs とスタッフのシフト時間窓を比較し、
      必要人数の引き下げ・シフト時間の拡大・制約のソフト化で解消できる。
  - 将来の新ドメインでも同じ "diagnostic.details" フィールドを参照すること。
    新しいドメインで構造化フィールド（crane_id 相当の識別子や、パッチ対象の
    配列インデックス）が無い場合は、自然文の reason に頼らざるを得ないが、
    その旨を explanation に明記すること。

### unserved_breakdown フィールド（TruckDispatcher）
solve_failed イシューの "unserved_breakdown" は
{customer_id: {reason, detail}} の形式。reason ごとに対応が異なる:

- "capacity_infeasible"（積載量超過。分割配送・増車なしでは構造的に不可能）
  → detail に「需要{demand}kgが、割当候補車両の最大積載量{max_cap}kgを超過」と
    数値が明記されている。該当車種の vehicles[i].capacity_kg を demand 以上
    （余裕を見て+10%程度）に replace するか、customers[i].restricted_vehicle_types
    から大型車種を外す緩和案を出すこと。
- "duty_infeasible"（拘束時間超過。出発時刻固定・直行往復のみでも構造的に不可能）
  → detail に「最低{X}分の拘束時間が必要ですが...上限は最大{Y}分です
    （{X-Y}分超過）」と数値が明記されている。X（不足分ちょうど）ではなく
    余裕を持たせて、該当車両の vehicles[i].max_duty_min を X+10分以上に
    replace するか、config.depot_open_min を早める緩和案を出すこと
    （yardの task_end_min ルールと同様、境界値ぴったりの設定は避けること）。
- "no_eligible_vehicle"（車格制限で候補車両ゼロ）
  → customers[i].restricted_vehicle_types から該当車種を remove する緩和案を出すこと。
- "insufficient_fleet_capacity"（2026-07-23追加。単体では容量・拘束時間の条件は
  満たせるが、同じ車両群（型式・積載量で絞られた候補車両の集合）を必要とする配送数が、
  その車両群の頭数を構造的に上回っている。探索不足ではなくフリート側の頭数不足）
  → detail に「この配送を運べる車両群は{N}台ですが、同じ車両群でしか運べない配送が
    {M}件あります（顧客ID一覧...）」と数値・対象顧客が明記されている。
    **この reason には dsl_patch を生成すること**（unresolved_by_searchとは異なり、
    構造的な頭数不足なので再探索では解決しない）。対応方針は次の優先順で検討する:
    1. 不足台数（目安: M - N、余裕を見て+1台）だけ、detail記載の車両群と同じ
       type・capacity_kg・max_duty_min を持つ新規車両を vehicles 配列に追加する
       （op: "add", path: "/vehicles/-", value: 新規車両オブジェクト一式。
       id は既存と重複しない値、name は「(増車提案)」等と分かる名前にすること。
       台数分は複数の add op を1つの dsl_patch 配列にまとめてよい）。
    2. 増車の代わりに、既存の当該車両群のうち稼働に余裕がありそうな車両の
       capacity_kg を引き上げる案でもよいが、その場合は引き上げ後も
       detail記載の他の同グループ配送を運びきれるかを考慮すること。
    3. 増車台数を自動計算しにくい場合は value を null にして
       param（min=1, max=M, default=max(1, M-N)）で人間に決めさせてもよい。
    いずれの案でも、tradeoff には増車コスト・車両確保の現実性を明記すること。
- "unresolved_by_search"（単体では容量・拘束時間の条件を満たせるはずだが、
  今回の探索では割当先が見つからなかった。DSLの構造的な問題ではなく、
  探索が足りていないだけの可能性が高い）
  → **この reason には dsl_patch を生成しないこと。** DSLの制約緩和では
    解決しない可能性が高いため、緩和案としては出力しない
    （再探索で解決する見込みが高いことは、緩和案とは別にissue表示側で扱う）。
 
## Human-in-the-Loop: ユーザーに値を決めさせる場合
値を自動計算できない場合や、人間の判断が必要な場合は
value を null にして param フィールドで調整情報を付与すること。
フロントエンドがスライダー UI を自動生成してユーザーに入力させる。

値を計算できる場合（task_end_min など診断情報から導出できる場合）は
value に具体的な数値を入れること（param は不要）。

{
  "op": "replace",
  "path": "/actual/path/in/dsl",
  "value": null,
  "label": "シフト終了時刻",       // UI・チャット画面に表示する人間向けラベル（必須）
  "param": {
    "label":   "シフト終了時刻",   // UI に表示するラベル（labelと同じ内容でよい）
    "min":     9,                 // 最小推奨値（診断情報から算出した下限）
    "max":     480,               // 現実的な上限
    "default": 9,                 // デフォルト値（min と同じにしてよい）
    "unit":    "分",              // 単位ラベル
    "step":    1                  // スライダーのステップ（省略可）
  }
}

値が決まっている場合も label は必須（param は不要）:
{
  "op": "remove",
  "path": "/customers/14/restricted_vehicle_types/0",
  "label": "顧客14の使用可能車両タイプ制限を解除"
}

## 出力形式（JSON配列）
[
  {
    "title": "緩和案のタイトル（20字以内）",
    "explanation": "なぜ Infeasible になっているか、この緩和でなぜ解決できるかの説明（日本語）",
    "tradeoff": "この緩和によって失われるもの・リスク（日本語）",
    "dsl_patch": [
      {"op": "replace", "path": "/actual/path/in/dsl", "value": <new_value_or_null>, "label": "人間向けの短い説明（必須）"}
    ]
  }
]

## JSON Patch の op の使い方
- "replace": 既存フィールドの値を変更する
- "add": 新規フィールドを追加する（"soft" 化時のペナルティ追加など）
- "remove": フィールドを削除する
"""

# 2026-07-30 i18n対応（NurseShiftWeeklyCapドメインのStudio画面のみ対象）:
# 上記RELAX_SYSTEM_PROMPT本体はJSON構造・診断フィールドの読み方などの指示が
# 中心で日本語のままでよい（LLMは日本語の指示を読んで英語の出力を生成できる）。
# 出力される title/explanation/tradeoff、および dsl_patch 内の各opの label
# （いずれもUI・チャット画面にそのまま表示される人間向けテキスト）だけを
# 現在の表示言語に切り替えるため、末尾に言語指示を追加する。
_RELAX_LANG_DIRECTIVE_EN = """

## Output language (IMPORTANT)
The current UI display language is English. Even though the instructions above
are written in Japanese, you must write the following **human-facing text**
fields entirely in English:
  - "title"
  - "explanation"
  - "tradeoff"
  - every "label" field inside "dsl_patch" entries (including nested "param.label")
Do NOT translate DSL field names, JSON keys, or DSL path strings (e.g.
"/vehicles/3/capacity_kg") — only the human-readable text fields listed above.
"""


def suggest_relaxations(dsl: dict, issues: list, lang: str = "ja") -> list:
    """
    Infeasible 状態の DSL とイシューリストを受け取り、
    DSLの変更で解決可能な原因の数だけ緩和候補を生成して返す（件数は固定しない）。

    Args:
        lang: "ja"（デフォルト）または "en"。呼び出し側（app.pyの/relax・
              /relax/start）が現在の表示言語（i18n.context.get_lang()）を
              明示的に渡す。/relax/startはバックグラウンドスレッドで実行される
              ためcontextvarsが自動伝搬しない点に注意（app.py _run_relax_job
              参照）。

    Returns:
        list of {title, explanation, tradeoff, dsl_patch}
    """
    if not is_available():
        if lang == "en":
            return [
                {
                    "title": "LLM not connected (sample)",
                    "explanation": "LLM_PROVIDER API key is not configured, so a sample relaxation option is shown.",
                    "tradeoff": "The actual DSL has not been analyzed.",
                    "dsl_patch": [],
                }
            ]
        return [
            {
                "title": "LLM未接続（サンプル）",
                "explanation": "LLM_PROVIDER の API キーが未設定のため、サンプル緩和案を返しています。",
                "tradeoff": "実際の DSL を分析していません。",
                "dsl_patch": [],
            }
        ]

    solve_failed = [i for i in issues if i.get("type") == "SOLVE_FAILED" or "solve_failed" in i.get("id", "")]
    issue_summary = json.dumps(solve_failed or issues, ensure_ascii=False, indent=2)
    dsl_str = json.dumps(dsl, ensure_ascii=False, indent=2)

    system_prompt = RELAX_SYSTEM_PROMPT + (_RELAX_LANG_DIRECTIVE_EN if lang == "en" else "")

    messages = [
        {"role": "system", "content": system_prompt},
        {
            "role": "user",
            "content": (
                f"## 検出されたイシュー（Infeasible の原因）\n{issue_summary}\n\n"
                f"## DSL（実際のフィールド構造を必ず確認してから dsl_patch を生成すること）\n{dsl_str}\n\n"
                f"この DSL を Feasible にする緩和案を、DSL変更で解決可能な原因ごとに"
                f"JSON 配列で返してください（件数は原因の数に応じて決めてよい）。"
            ),
        },
    ]

    try:
        raw = call_llm_json(messages, temperature=0.5, max_tokens=2048)
        if not isinstance(raw, list):
            raw = [raw] if isinstance(raw, dict) else []
 
        results = []
        for item in raw:
            if not isinstance(item, dict):
                continue
            patch_ops = item.get("dsl_patch", [])
 
            # パッチ検証: 存在しないパスへの操作を警告ログに出力
            warnings = _validate_patch_touches_dsl(patch_ops, dsl)
            if warnings:
                print(f"[suggest_relaxations] WARNING パッチ検証失敗: {warnings} / patch={patch_ops}")
            else:
                print(f"[suggest_relaxations] patch OK: {patch_ops}")
 
            results.append({
                "title":           item.get("title", "(Untitled)" if lang == "en" else "(タイトルなし)"),
                "explanation":     item.get("explanation", ""),
                "tradeoff":        item.get("tradeoff", ""),
                "dsl_patch":       patch_ops,
                "patch_warnings":  warnings,  # フロントエンドが警告表示に使用可
            })
        return results
 
    except Exception as e:
        print(f"[suggest_relaxations] failed: {e}")
        error_title = "LLM Error" if lang == "en" else "LLMエラー"
        return [{"title": error_title, "explanation": str(e), "tradeoff": "", "dsl_patch": []}]
 
 
def _validate_patch_touches_dsl(patch_ops: list, dsl: dict) -> list[str]:
    """
    LLM が生成した dsl_patch の各 op が DSL の実在パスを操作しているか検証する。
    存在しないパスへの replace/remove を検出して警告リストを返す。
    add は新規パス追加なので対象外。
    """
    import jsonpatch as jp
    warnings = []
    for op in patch_ops:
        if op.get("op") in ("replace", "remove"):
            path = op.get("path", "")
            # パスを辿って実在確認
            try:
                jp.JsonPatch([{"op": "test", "path": path, "value": None}]).apply(dsl)
            except jp.JsonPointerException:
                warnings.append(f"存在しないパス: {path}")
            except jp.JsonPatchTestFailed:
                pass  # 値が違うだけでパスは存在する → OK
            except Exception:
                pass
    return warnings