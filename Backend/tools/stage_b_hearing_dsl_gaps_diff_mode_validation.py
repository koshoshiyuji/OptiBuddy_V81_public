"""
Backend/tools/stage_b_hearing_dsl_gaps_diff_mode_validation.py

2026-08-09追加（`2026-08-05_registration_time_reduction_plan.md` タスク2の再開）。

detect_hearing_dsl_gaps_incremental()（差分渡し版）の妥当性を、新規のドメイン登録を
行わずに検証する。MysteryShopperScheduler登録時に実際に発生した2つの不具合修正
（_build_revisit_pairsの効率化＋変数名のハイフン除去、いずれも
`Backend/solvers/mystery_shopper_scheduler_solver.py`のみの変更でconverter.py/
ui_converter.pyは無変更）を「debug_agent的な事後修正の実例」として流用する。

比較する3回のLLM呼び出し:
  (a) 修正前のsolver.py全文で通常のdetect_hearing_dsl_gaps()を呼ぶ
      → これを「前回のカバレッジ判定結果」とみなす
  (b) (a)の結果 + solver.pyの差分（converter/ui_converterは無変更なのでdiff無し）を
      detect_hearing_dsl_gaps_incremental()に渡す → 差分渡し版の結果
  (c) 修正後のsolver.py全文で通常のdetect_hearing_dsl_gaps()を呼ぶ（正解データ）
      → (b)の結果がこれとどれだけ一致するかで差分渡し版の精度を判定する

使い方:
  cd Backend
  # Koshoshiの実環境（.envのANTHROPIC_API_KEYが実際に使える環境）で実行してください。
  python3 tools/stage_b_hearing_dsl_gaps_diff_mode_validation.py
"""
import json
import sys
import time

sys.path.insert(0, ".")

from domain_generator import (  # noqa: E402
    detect_hearing_dsl_gaps,
    detect_hearing_dsl_gaps_incremental,
    build_code_diff,
)

SNAKE = "mystery_shopper_scheduler"
DOMAIN_NAME = "MysteryShopperScheduler"

HEARING_FILE = "../docs/test_hearings/mystery_shopper_scheduler_hearing_sheet_j_v1.md"
ORIGINAL_SOLVER_FILE = "tools/_mystery_shopper_scheduler_solver_ORIGINAL.py"
FIXED_SOLVER_FILE = f"solvers/{SNAKE}_solver.py"
CONVERTER_FILE = f"dsl_transformer/{SNAKE}_converter.py"
UI_CONVERTER_FILE = f"dsl_transformer/{SNAKE}_ui_converter.py"
BASELINE_SCENARIO = f"dsl_repository/scenarios/{SNAKE}_baseline.json"
INFEASIBLE_SCENARIO = f"dsl_repository/scenarios/{SNAKE}_infeasible.json"


def _rough_token_estimate(*texts: str) -> int:
    """目安（相対比較用）。厳密な値は実測usageログを見ること。"""
    return sum(len(t) for t in texts) // 3


def _summarize(label: str, coverage: dict, elapsed: float, input_chars: int) -> None:
    items = coverage.get("items", [])
    gaps = [i for i in items if i.get("status") == "gap"]
    required = [g for g in gaps if g.get("priority") == "required"]
    print(
        f"\n[{label}] elapsed={elapsed:.1f}秒 input目安=約{input_chars // 3}トークン "
        f"total={len(items)}件 gap={len(gaps)}件（うちrequired={len(required)}件）"
    )
    for g in gaps:
        print(f"    - [{g.get('priority')}] §{g.get('hearing_section')}: {g.get('requirement')}")


def _diff_items(prior_items: list, new_items: list) -> list:
    """要件ごとのstatus差分を一覧化する（要件文字列でペアリング）。"""
    prior_by_req = {i.get("requirement"): i for i in prior_items}
    changed = []
    for i in new_items:
        req = i.get("requirement")
        prior = prior_by_req.get(req)
        if prior is None:
            changed.append((req, "（前回に該当項目なし）", i.get("status")))
        elif prior.get("status") != i.get("status"):
            changed.append((req, prior.get("status"), i.get("status")))
    return changed


def main() -> None:
    hearing_text = open(HEARING_FILE, encoding="utf-8").read()
    original_solver_code = open(ORIGINAL_SOLVER_FILE, encoding="utf-8").read()
    fixed_solver_code = open(FIXED_SOLVER_FILE, encoding="utf-8").read()
    converter_code = open(CONVERTER_FILE, encoding="utf-8").read()
    ui_converter_code = open(UI_CONVERTER_FILE, encoding="utf-8").read()
    baseline = json.load(open(BASELINE_SCENARIO, encoding="utf-8"))
    infeasible = json.load(open(INFEASIBLE_SCENARIO, encoding="utf-8"))
    scenarios = {"baseline": baseline, "infeasible": infeasible}
    hearing_texts = [hearing_text]

    if original_solver_code == fixed_solver_code:
        print("警告: original/fixedのsolver.pyが完全一致しています。"
              "差分が無いテストになるため検証の意味がありません。ファイルを確認してください。")
        return

    # --- (a) 修正前のsolver.py全文で通常チェック（=「前回の判定結果」役） ---
    common_kwargs = dict(
        domain_name=DOMAIN_NAME,
        hearing_texts=hearing_texts,
        dsl_scenarios=scenarios,
        converter_code=converter_code,
        ui_converter_code=ui_converter_code,
    )
    input_a = _rough_token_estimate(converter_code, original_solver_code, ui_converter_code) * 3
    t0 = time.perf_counter()
    result_a = detect_hearing_dsl_gaps(solver_code=original_solver_code, **common_kwargs)
    elapsed_a = time.perf_counter() - t0
    _summarize("(a) 修正前フルコード（前回判定役）", result_a, elapsed_a, input_a)

    # --- (b) 差分渡し版 ---
    solver_diff = build_code_diff(original_solver_code, fixed_solver_code, "solver.py")
    converter_diff = build_code_diff(converter_code, converter_code, "converter.py")  # 無変更→None
    ui_converter_diff = build_code_diff(ui_converter_code, ui_converter_code, "ui_converter.py")  # 無変更→None
    input_b = len(solver_diff or "") + len(json.dumps(result_a.get("items", []), ensure_ascii=False))
    t0 = time.perf_counter()
    result_b = detect_hearing_dsl_gaps_incremental(
        domain_name=DOMAIN_NAME,
        hearing_texts=hearing_texts,
        prior_result=result_a,
        converter_diff=converter_diff,
        solver_diff=solver_diff,
        ui_converter_diff=ui_converter_diff,
    )
    elapsed_b = time.perf_counter() - t0
    _summarize("(b) 差分渡し版", result_b, elapsed_b, input_b)
    print(f"    差分渡し版の入力サイズ: 全文渡し比 -{100 * (input_a - input_b) / input_a:.0f}%"
          f"（全文=約{input_a // 3}トークン → 差分=約{input_b // 3}トークン）")

    # --- (c) 修正後のsolver.py全文で通常チェック（正解データ） ---
    input_c = _rough_token_estimate(converter_code, fixed_solver_code, ui_converter_code) * 3
    t0 = time.perf_counter()
    result_c = detect_hearing_dsl_gaps(solver_code=fixed_solver_code, **common_kwargs)
    elapsed_c = time.perf_counter() - t0
    _summarize("(c) 修正後フルコード（正解データ）", result_c, elapsed_c, input_c)

    # --- (b) vs (c) の一致度 ---
    print("\n[精度検証] (b)差分渡し版 と (c)修正後フル再チェック（正解）の差分:")
    diffs_bc = _diff_items(result_c.get("items", []), result_b.get("items", []))
    if not diffs_bc:
        print("    一致。差分渡し版は正解データと同じ判定を返した。")
    else:
        for req, old, new in diffs_bc:
            print(f"    - 「{req}」: 正解={old} / 差分渡し版={new}")

    print("\n[所要時間比較]")
    print(f"    (a) 全文渡し（修正前）: {elapsed_a:.1f}秒")
    print(f"    (b) 差分渡し          : {elapsed_b:.1f}秒")
    print(f"    (c) 全文渡し（修正後、正解データ）: {elapsed_c:.1f}秒")
    if elapsed_c > 0:
        print(f"    (b)は(c)比 {100 * (elapsed_c - elapsed_b) / elapsed_c:.0f}% 短縮")

    print(
        "\n[判定目安] (b)と(c)のgap/status判定がほぼ一致していれば、差分渡し方式は"
        "「debug_agent的な事後修正の再検証」用途で妥当と判断できる。所要時間・入力"
        "トークン量の削減幅も合わせて確認すること。今回の修正（制約ロジックの効率化・"
        "変数名変更）はヒアリング要件のカバレッジには無関係なはずなので、理想的には"
        "(a)=(b)=(c)で全項目一致するはず。"
    )


if __name__ == "__main__":
    main()
