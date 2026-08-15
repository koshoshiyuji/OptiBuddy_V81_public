"""
Backend/tools/stage_b_hearing_dsl_gaps_model_variance.py

2026-08-05追加（registration_time_reduction_plan.md タスク3、段階B）。

2026-07-16時点の「temperature=0でもhearing_dsl_gapsの判定はSonnet/Haiku問わず
揺れる」という結論（detect_hearing_dsl_gaps()のコメント参照）を、新規登録ではなく
既に手元にあるLotSizingSchedulerの生成済みコード＋ヒアリングシートに対する
単体呼び出しの繰り返し（Sonnet/Haiku各3回、計6コール）で再検証する。

detect_hearing_dsl_gaps()自体はmodel=default_model()に固定されているため、
本スクリプトはその内部のプロンプト構築ロジック（_HEARING_DSL_COVERAGE_SYSTEM・
user_prompt組み立て）を再利用しつつ、call_llm()のmodel引数だけ差し替えて呼ぶ。

使い方:
  cd Backend
  # Koshoshiの実環境（.envのANTHROPIC_API_KEYが実際に使える環境）で実行してください。
  # このリポジトリのサンドボックスではAnthropic APIへの認証付きリクエストが
  # プロキシでブロックされることを確認済み（2026-08-05）。
  python3 tools/stage_b_hearing_dsl_gaps_model_variance.py
"""
import json
import sys

sys.path.insert(0, ".")

from domain_generator import _HEARING_DSL_COVERAGE_SYSTEM  # noqa: E402
from llm.llm_client import call_llm, extract_json, default_model, fast_model  # noqa: E402

SNAKE = "lot_sizing_scheduler"
DOMAIN_NAME = "LotSizingScheduler"

HEARING_FILE = "../docs/test_hearings/lot_sizing_scheduler_hearing_sheet_j.md"
CONVERTER_FILE = f"dsl_transformer/{SNAKE}_converter.py"
SOLVER_FILE = f"solvers/{SNAKE}_solver.py"
UI_CONVERTER_FILE = f"dsl_transformer/{SNAKE}_ui_converter.py"
BASELINE_SCENARIO = f"dsl_repository/scenarios/{SNAKE}_baseline.json"
INFEASIBLE_SCENARIO = f"dsl_repository/scenarios/{SNAKE}_infeasible.json"

N_RUNS_PER_MODEL = 3


def _build_user_prompt(hearing_text: str, scenarios: dict, converter_code: str,
                        solver_code: str, ui_converter_code: str) -> str:
    """detect_hearing_dsl_gaps()内のプロンプト組み立てと同一のロジック
    （domain_generator.py 1119〜1159行相当）を再現する。"""
    scenarios_block = "\n\n".join(
        f"### {name}\n```json\n{json.dumps(scenario, ensure_ascii=False, indent=2)}\n```"
        for name, scenario in scenarios.items()
    )
    ui_converter_block = f"""

## 生成されたui_converter.py（solver出力 → 画面表示DSL変換。§7等の表示要件はここまで
確認すること。solverにデータがあってもこのファイルが参照していなければgap）
```python
{ui_converter_code}
```"""
    return f"""## 業務名
{DOMAIN_NAME}

## ヒアリング内容
{hearing_text}

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


def _run_once(model: str, user_prompt: str) -> dict:
    messages = [
        {"role": "system", "content": _HEARING_DSL_COVERAGE_SYSTEM},
        {"role": "user", "content": user_prompt},
    ]
    raw = call_llm(messages, model=model, max_tokens=4000, temperature=0)
    result = extract_json(raw)
    items = result.get("items", [])
    gaps = [i for i in items if i.get("status") == "gap"]
    required = [g for g in gaps if g.get("priority") == "required"]
    return {"total": len(items), "gap": len(gaps), "required_gap": len(required)}


def main() -> None:
    hearing_text = open(HEARING_FILE, encoding="utf-8").read()
    converter_code = open(CONVERTER_FILE, encoding="utf-8").read()
    solver_code = open(SOLVER_FILE, encoding="utf-8").read()
    ui_converter_code = open(UI_CONVERTER_FILE, encoding="utf-8").read()
    baseline = json.load(open(BASELINE_SCENARIO, encoding="utf-8"))
    infeasible = json.load(open(INFEASIBLE_SCENARIO, encoding="utf-8"))
    scenarios = {"baseline": baseline, "infeasible": infeasible}

    user_prompt = _build_user_prompt(hearing_text, scenarios, converter_code, solver_code, ui_converter_code)

    results = {}
    for label, model in [("Sonnet", default_model()), ("Haiku", fast_model())]:
        print(f"\n=== {label} ({model}) を{N_RUNS_PER_MODEL}回実行 ===")
        runs = []
        for i in range(1, N_RUNS_PER_MODEL + 1):
            r = _run_once(model, user_prompt)
            runs.append(r)
            print(f"  {i}回目: total={r['total']}件 gap={r['gap']}件 required_gap={r['required_gap']}件")
        results[label] = runs

    print("\n=== 揺れの要約 ===")
    for label, runs in results.items():
        gaps = [r["gap"] for r in runs]
        required = [r["required_gap"] for r in runs]
        print(f"{label}: gap件数レンジ={min(gaps)}〜{max(gaps)}（幅{max(gaps) - min(gaps)}）, "
              f"required_gap件数レンジ={min(required)}〜{max(required)}（幅{max(required) - min(required)}）")

    print(
        "\n[判定目安] 2026-07-16時点の実測（Sonnet: 1回目20件/required2件 → "
        "2回目21件/required3件、幅1〜2件程度）と同程度かそれ以下の揺れであれば、"
        "Haikuへの切替候補として段階Cで最終確認する価値がある。Haikuの方が"
        "明らかに揺れが大きい（幅が数倍等）なら、精度リスクが高いためここでクローズし、"
        "案2（hearing_dsl_gaps入力サイズ削減）に集中する。"
    )


if __name__ == "__main__":
    main()
