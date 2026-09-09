
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
    logger,
)




# ─────────────────────────────────────────────────────────────
# Gate2 LLMチェック（track1拡張）: ヒアリング⇔DSL/コード カバレッジ判定
#
# 背景: 上記のAST差集合チェックは「機械的な」対称性しか見られないため、
# ヒアリング文中の要件（特に自由記述・任意節）がDSL/コードに意味的に反映
# されているかどうかまでは判定できない。この段はdetect_extension_gaps()
# （Stage1a.5、既存ドメインの拡張差分検出）と全く同じLLM比較パターンを、
# new_domain登録でStage2が生成した直後の成果物（DSLシナリオ・solver/converter）
# に対して適用する。新規の仕組みではなく、既存の比較パターンの適用対象を
# 「既存ドメインの実装」から「たった今生成された新規実装」に変えただけ。
# ─────────────────────────────────────────────────────────────

_HEARING_DSL_COVERAGE_SYSTEM = """\
あなたは、業務ヒアリング内容と、そこから生成された最適化ドメインの実装（DSLシナリオ・
converter.py・solver.py）を突き合わせ、ヒアリングで述べられた要件がどこまで実装に
反映されているかを判定する専門家です。

対象は主に new_domain（新規コード生成）登録時のGate2チェックです。
「ヒアリング文中の要件」を最優先の情報源とし、それぞれについて以下のいずれかに分類してください:

1. covered:  DSLシナリオまたはsolver/converterのコードに明確に反映されている要件
2. gap:      ヒアリング文中で明示的に述べられているが、DSLシナリオにもsolver/converterの
             コードにも一切反映が見当たらない要件（「できれば守りたいルール」等の任意節の
             要件も対象に含める。ただしヒアリング文中で「今回のスコープに含めない」
             「将来判断」等と明示的に除外宣言されている項目はgapに含めないこと）
3. deferred: ヒアリング文中で明示的にスコープ外・将来対応と宣言されている要件
             （除外を明記した記述がある場合のみ）

判定基準:
- 迷った場合はcoveredと判断せず、gapとして報告すること（見落としより過検知を許容する）
- ヒアリングのチェックボックス選択（[x]）で選ばれた分岐が実際にコードのロジックに
  反映されているかも確認すること（例: 複数目的の優先方式、不足時の解なし/緩和の扱い等）
- 「画面で確認したい情報」のような表示要件は、solverのkpi/metrics等の出力に
  該当データが含まれているかだけでなく、ui_converter.py（渡されている場合）が
  そのデータを実際に読み取ってKPIカード・テーブルに反映しているかまで確認すること。
  solver側にデータが存在していても、ui_converter.pyが別のキー名を参照していたり
  （旧ドメインのコードが誤って残っている等）、そのデータを一切使っていない場合は
  coveredと判定せずgapとして報告すること

各要件について、それが記載されているヒアリング節が「必須」節か「任意」節かも判定する
こと（多くのヒアリングシートは節見出しに「（必須）」「（任意）」と明記されている。
明記が無い場合は、業務の中核（1〜4節相当: 業務概要・割り当て構造・最適化目的・
守らなければならないルール）は"required"、それ以外（5節以降の「できれば」「画面表示」
「特殊要件」等）は"optional"と推定してよい）。この判定はgap/deferredの重要度選別に
使うためのものであり、covered/gap/deferredの判定基準そのものを変えるものではない。

JSONのみを返してください。前置き・説明不要。

{
  "items": [
    {
      "requirement": "ヒアリング文中の要件を1文で要約",
      "hearing_section": "該当するヒアリング節番号（例: 3, 5, 8）",
      "priority": "required / optional（そのヒアリング節が必須節か任意節か）",
      "status": "covered / gap / deferred",
      "evidence": "coveredの場合はDSL/コード中の該当箇所、gapの場合は探したが見当たらなかった旨、deferredの場合はヒアリング中の除外宣言箇所"
    }
  ]
}
"""


def detect_hearing_dsl_gaps(
    domain_name: str,
    hearing_texts: list,
    dsl_scenarios: dict,
    converter_code: str,
    solver_code: str,
    ui_converter_code: str | None = None,
) -> dict:
    """
    論点: hearing→DSL/コード カバレッジチェック（Gate2拡張、track1）。
    detect_extension_gaps()（Stage1a.5、既存ドメイン向け）と同じLLM比較パターンを、
    new_domain登録時のStage2生成物へ向けて適用する。

    dsl_scenarios: {"baseline": {...}, "infeasible": {...}} 形式。

    2026-07-31追加: ui_converter_code（省略可）。以前はconverter.py/solver.pyの2つ
    しか比較対象に含めておらず、「§7のような画面表示要件は、solverの出力に該当データが
    含まれているか」だけで判定していた。これだと、solver.pyが正しくデータを出力していても
    ui_converter.pyがそれを一切参照していない（旧ドメインの表示コードが取り残されている等）
    不整合を検出できなかった（NursingWorkloadBalance登録で実際に発生。詳細は
    extract_domain_artifacts_from_diffs()のdocstring参照）。渡されなかった場合
    （呼び出し元がui_converter.pyを抽出できなかった場合）は従来通りconverter/solverのみで
    判定する。
    """
    from llm.llm_client import call_llm, extract_json, default_model

    hearing_combined = "\n\n---\n\n".join(hearing_texts)
    scenarios_block = "\n\n".join(
        f"### {name}\n```json\n{json.dumps(scenario, ensure_ascii=False, indent=2)}\n```"
        for name, scenario in dsl_scenarios.items()
    )
    ui_converter_block = (
        f"""

## 生成されたui_converter.py（solver出力 → 画面表示DSL変換。§7等の表示要件はここまで
確認すること。solverにデータがあってもこのファイルが参照していなければgap）
```python
{ui_converter_code}
```"""
        if ui_converter_code
        else ""
    )
    user_prompt = f"""## 業務名
{domain_name}

## ヒアリング内容
{hearing_combined}

## 生成されたDSLシナリオ
{scenarios_block}

## 生成されたconverter.py
```python
{converter_code}
```

## 生成されたsolver.py
```python
{solver_code}
```
{ui_converter_block}

上記を突き合わせ、ヒアリング文中の要件それぞれについてcovered/gap/deferredを判定し、
JSONで回答してください。
"""
    messages = [{"role": "system", "content": _HEARING_DSL_COVERAGE_SYSTEM}, {"role": "user", "content": user_prompt}]
    # temperature=0: classify_problem()（Stage1a）と同じ理由。実測で同一ヒアリング・
    # 同一生成物に対しても実行のたびに検出項目数・required判定が揺れることを確認した
    # （2026-07-16試験実装: 1回目20件/required2件 → 2回目21件/required3件）。
    # 判定タスクであり創造性は不要なため、決定的にして揺れを抑える。
    raw    = call_llm(messages, model=default_model(), max_tokens=4000, temperature=0)
    result = extract_json(raw)
    items  = result.get("items", [])
    gaps   = [i for i in items if i.get("status") == "gap"]
    required_gaps = [i for i in gaps if i.get("priority") == "required"]
    logger.info(
        f"[hearing_dsl_gaps] {domain_name}: total={len(items)}件, "
        f"gap={len(gaps)}件（うちrequired={len(required_gaps)}件）"
    )
    return {
        "items": items,
        "gap_count": len(gaps),
        "required_gap_count": len(required_gaps),
    }


_HEARING_DSL_COVERAGE_INCREMENTAL_SYSTEM = """\
あなたは、業務ヒアリング内容と最適化ドメイン実装（DSLシナリオ・converter.py・
solver.py）の要件カバレッジ判定を行う専門家です。

このタスクは「差分渡し」による再判定です。以前のカバレッジ判定結果（各要件に
idx番号を振ったcovered/gap/deferred判定の一覧）が既にあり、その後コードに変更が
加わりました。変更差分（unified diff形式）を見て、以前の判定のうち**影響を受ける
可能性がある項目だけ**を再判定してください。

出力は「判定が変わった項目」または「判定は変わらないが根拠（evidence）を
更新すべき項目」**のみ**を、対応する前回のidxとともに返してください。
影響が無い項目は出力に含めないでください（呼び出し側で前回の値をそのまま
使います）。1件も影響が無ければ空配列を返してください。

判定基準は元のカバレッジ判定と同じです:
- 迷った場合はcoveredと判断せず、gapとして報告すること（見落としより過検知を許容する）
- 差分が「新しく要件を満たすようになった」「既存の実装を壊した」のどちらの
  可能性もあることに注意し、両方向の変化を検討すること

JSONのみを返してください。前置き・説明不要。

{
  "changed": [
    {
      "idx": 3,
      "status": "covered / gap / deferred",
      "evidence": "新しい根拠"
    }
  ]
}
"""


def build_code_diff(prior_code: str | None, new_code: str | None, label: str) -> str | None:
    """
    2026-08-09追加（登録時間短縮タスク2の再開、差分渡し方式の実装）。

    prior_codeとnew_codeの unified diff を返す。差分が無い（prior_code is None、
    new_codeがNone、または完全一致）場合はNoneを返す。呼び出し側はNoneの場合
    「この観点では変更なし」として扱い、当該ファイルをdetect_hearing_dsl_gaps_incremental()
    のプロンプトに含めない（変更が無いファイルはLLMに送る意味が無いため）。
    """
    if prior_code is None or new_code is None or prior_code == new_code:
        return None
    diff_lines = difflib.unified_diff(
        prior_code.splitlines(keepends=True),
        new_code.splitlines(keepends=True),
        fromfile=f"{label}（前回チェック時）",
        tofile=f"{label}（今回）",
        n=3,
    )
    return "".join(diff_lines)


def detect_hearing_dsl_gaps_incremental(
    domain_name: str,
    hearing_texts: list,
    prior_result: dict,
    converter_diff: str | None,
    solver_diff: str | None,
    ui_converter_diff: str | None = None,
) -> dict:
    """
    2026-08-09追加（`2026-08-05_registration_time_reduction_plan.md` タスク2の再開）。

    detect_hearing_dsl_gaps()のコード全文渡し版に対する「差分渡し」版。self-repair
    v2（関数単位パッチ）が実証した「全文書き換え→差分のみ」という高速化パターンを
    hearing_dsl_gapsにも適用する。

    前提: この関数は「コードが完全に新規（1回目のGate2チェック）」なケースには使えない
    （比較対象となる`prior_result`が存在しないため）。debug_agentの修正ラウンド後の
    再検証など、「既に一度detect_hearing_dsl_gaps()でカバレッジ判定済みのコードに、
    小さな変更が加わった」場合の再判定にのみ使う想定。

    converter_diff/solver_diff/ui_converter_diffは、build_code_diff()で事前に
    計算した unified diff文字列（変更が無ければNone）。3つとも全てNoneの場合は
    LLM呼び出し自体を行わず、prior_resultをそのまま返す（差分が無ければ判定も
    変わりようがないため、コスト0で済ませる）。
    """
    if converter_diff is None and solver_diff is None and ui_converter_diff is None:
        logger.info(
            f"[hearing_dsl_gaps_incremental] {domain_name}: 差分なし、LLM呼び出しを"
            f"スキップして前回の判定をそのまま再利用（total={len(prior_result.get('items', []))}件）"
        )
        return prior_result

    from llm.llm_client import call_llm, extract_json, default_model

    prior_items = prior_result.get("items", [])
    hearing_combined = "\n\n---\n\n".join(hearing_texts)
    diff_blocks = []
    if converter_diff:
        diff_blocks.append(f"### converter.py の変更差分\n```diff\n{converter_diff}\n```")
    if solver_diff:
        diff_blocks.append(f"### solver.py の変更差分\n```diff\n{solver_diff}\n```")
    if ui_converter_diff:
        diff_blocks.append(f"### ui_converter.py の変更差分\n```diff\n{ui_converter_diff}\n```")
    diffs_combined = "\n\n".join(diff_blocks)

    # 出力側の削減（2026-08-09追加）: 前回のitemsを毎回全件出力させると、入力を
    # 削っても出力トークン量（≒レイテンシ）が下がらないことが実測で判明した
    # （MysteryShopperSchedulerでの検証: 入力-64%でも所要時間はほぼ横ばい、
    # むしろ全文渡しよりわずかに遅い結果になった）。出力もidxベースの差分のみに
    # 絞ることで、影響が無い項目についてはLLMに一切書かせない。
    indexed_items_block = "\n".join(
        f"{i}: [{item.get('priority')}/{item.get('status')}] §{item.get('hearing_section')} "
        f"{item.get('requirement')}"
        for i, item in enumerate(prior_items)
    )

    user_prompt = f"""## 業務名
{domain_name}

## ヒアリング内容
{hearing_combined}

## 前回のカバレッジ判定結果（idx: [優先度/判定] 節番号 要件、{len(prior_items)}件）
{indexed_items_block}

## 前回チェック時からのコード変更差分
{diffs_combined}

上記の差分が、前回判定のどのidxに影響しうるかを検討し、影響がある項目のみを
`changed`配列で返してください。
"""
    messages = [
        {"role": "system", "content": _HEARING_DSL_COVERAGE_INCREMENTAL_SYSTEM},
        {"role": "user", "content": user_prompt},
    ]
    raw = call_llm(messages, model=default_model(), max_tokens=2000, temperature=0)
    result = extract_json(raw)
    changed = result.get("changed", [])

    # prior_itemsをディープコピーした上で、changedに含まれるidxだけ上書きする。
    # 呼び出し側のprior_result（辞書オブジェクト）を意図せず書き換えないよう、
    # ここで新しいリストを作る。
    items = [dict(item) for item in prior_items]
    applied = 0
    for c in changed:
        idx = c.get("idx")
        if not isinstance(idx, int) or not (0 <= idx < len(items)):
            logger.warning(
                f"[hearing_dsl_gaps_incremental] {domain_name}: 不正なidx({idx!r})を"
                f"含む変更を無視しました（items件数={len(items)}）。"
            )
            continue
        if "status" in c:
            items[idx]["status"] = c["status"]
        if "evidence" in c:
            items[idx]["evidence"] = c["evidence"]
        applied += 1

    gaps = [i for i in items if i.get("status") == "gap"]
    required_gaps = [i for i in gaps if i.get("priority") == "required"]
    logger.info(
        f"[hearing_dsl_gaps_incremental] {domain_name}: total={len(items)}件, "
        f"changed={applied}件, gap={len(gaps)}件（うちrequired={len(required_gaps)}件）"
    )
    return {
        "items": items,
        "gap_count": len(gaps),
        "required_gap_count": len(required_gaps),
    }


def extract_domain_artifacts_from_diffs(diffs: list, snake: str) -> dict:
    """
    diffs（generate_domain_filesの戻り値）から、特定snakeドメインの
    converter.py/ui_converter.py/solver.py/DSLシナリオ（baseline/infeasible）を抜き出す。
    scan_diffs_for_warnings()のスキャンロジックと同じ分類パターンを、単一ドメイン
    向けに再利用可能な形に切り出したもの（detect_hearing_dsl_gaps呼び出し用）。

    2026-07-31追加: ui_converter_codeを追加。以前はconverter/solverの2ファイルしか
    detect_hearing_dsl_gaps()に渡していなかったため、「solverの出力データは正しいが
    ui_converter.pyがそれを参照せず画面表示が空になる」という不整合をヒアリング⇔コード
    カバレッジチェックが原理的に検出できなかった（NursingWorkloadBalance登録で実際に
    発生: solver.pyのmetrics/assignmentsは正しく実装されていたのに、ui_converter.pyが
    旧ドメイン（NurseShiftWeeklyCap）由来のキー（tasks/task_summary/staff_hours）を
    参照したまま取り残され、baseline画面のKPI・テーブルが全て0/空になった）。

    返り値: {"converter_code": str|None, "ui_converter_code": str|None,
             "solver_code": str|None, "scenarios": {"baseline": dict, "infeasible": dict}}
    """
    result = {"converter_code": None, "ui_converter_code": None, "solver_code": None, "scenarios": {}}
    scenario_re = re.compile(rf"(?:^|/)dsl_repository/scenarios/{re.escape(snake)}_(baseline|infeasible)\.json$")

    for item in diffs:
        path = item.get("path", "")
        code = item.get("new_content", "")

        if path.endswith("_solver.py") and Path(path).stem[: -len("_solver")] == snake:
            result["solver_code"] = code
        elif path.endswith("_ui_converter.py") and Path(path).stem[: -len("_ui_converter")] == snake:
            result["ui_converter_code"] = code
        elif (path.endswith("_converter.py") and not path.endswith("_ui_converter.py")
              and Path(path).stem[: -len("_converter")] == snake):
            result["converter_code"] = code
        else:
            m = scenario_re.search(path)
            if m:
                try:
                    result["scenarios"][m.group(1)] = json.loads(code)
                except Exception as e:
                    logger.warning(f"[extract_domain_artifacts] シナリオJSONパース失敗 {path}: {e}")

    return result
