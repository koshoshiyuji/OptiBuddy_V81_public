"""
Backend/tools/stage_b_hearing_dsl_gaps_input_size.py

2026-08-05追加（registration_time_reduction_plan.md タスク2、段階B）。

detect_hearing_dsl_gaps() を、既に手元にあるLotSizingSchedulerの生成済み
コード（converter.py/solver.py/ui_converter.py/DSLシナリオ/ヒアリングシート）を
入力にして単体で呼び出し、入力サイズを変えたときに判定結果（gap件数・
required_gap件数）がどう変わるかを比較する。新規ドメイン登録・新規Stage2生成は
行わない（段階Bの方針通り）。

**注記（2026-08-05）**: 当初案（「コード全文を渡す現状」vs「debug_agentの変更差分
のみを渡す案」の比較）は、実際にdebug_agentが生成した差分パッチが本リポジトリの
コミット履歴に残っておらず（LotSizingSchedulerは1コミットにスクワッシュ済み）、
再現できなかった。代替として、detect_hearing_dsl_gaps()の引数のうち
ui_converter_code（2026-07-31追加、任意引数）の有無で入力トークン数と判定結果が
どう変わるかを比較する。これは実際に「渡す情報量を減らす」ことの効果を測る
低コストな代替実験になる。

本当の意味での「diffのみ」案を検証したい場合は、次回の段階Cの生登録実行時に
debug_agentが書き込んだ差分（write_fileの前後内容）を保存しておく仕組みを
先に作る必要がある（今回のスコープ外）。

使い方:
  cd Backend
  # Koshoshiの実環境（.envのANTHROPIC_API_KEYが実際に使える環境）で実行してください。
  # このリポジトリのサンドボックスではAnthropic APIへの認証付きリクエストが
  # プロキシでブロックされることを確認済み（2026-08-05）。
  python3 tools/stage_b_hearing_dsl_gaps_input_size.py
"""
import json
import sys
import time

sys.path.insert(0, ".")

from domain_generator import detect_hearing_dsl_gaps  # noqa: E402

SNAKE = "lot_sizing_scheduler"
DOMAIN_NAME = "LotSizingScheduler"

HEARING_FILE = "../docs/test_hearings/lot_sizing_scheduler_hearing_sheet_j.md"
CONVERTER_FILE = f"dsl_transformer/{SNAKE}_converter.py"
SOLVER_FILE = f"solvers/{SNAKE}_solver.py"
UI_CONVERTER_FILE = f"dsl_transformer/{SNAKE}_ui_converter.py"
BASELINE_SCENARIO = f"dsl_repository/scenarios/{SNAKE}_baseline.json"
INFEASIBLE_SCENARIO = f"dsl_repository/scenarios/{SNAKE}_infeasible.json"


def _rough_token_estimate(*texts: str) -> int:
    """英数字/日本語混在のこのリポジトリでは文字数÷4が過小評価になることは
    2026-08-04追記9で確認済み。ここでは目安（相対比較用）としてのみ使う。
    厳密な値は実測のusage（input/cache_creation）ログを見ること。"""
    return sum(len(t) for t in texts) // 3


def _summarize(label: str, coverage: dict, elapsed: float) -> None:
    items = coverage.get("items", [])
    gaps = [i for i in items if i.get("status") == "gap"]
    required = [g for g in gaps if g.get("priority") == "required"]
    print(f"\n[{label}] elapsed={elapsed:.1f}秒 total={len(items)}件 "
          f"gap={len(gaps)}件（うちrequired={len(required)}件）")
    for g in gaps:
        print(f"    - [{g.get('priority')}] §{g.get('hearing_section')}: {g.get('requirement')}")


def main() -> None:
    hearing_text = open(HEARING_FILE, encoding="utf-8").read()
    converter_code = open(CONVERTER_FILE, encoding="utf-8").read()
    solver_code = open(SOLVER_FILE, encoding="utf-8").read()
    ui_converter_code = open(UI_CONVERTER_FILE, encoding="utf-8").read()
    baseline = json.load(open(BASELINE_SCENARIO, encoding="utf-8"))
    infeasible = json.load(open(INFEASIBLE_SCENARIO, encoding="utf-8"))
    scenarios = {"baseline": baseline, "infeasible": infeasible}
    hearing_texts = [hearing_text]

    common_kwargs = dict(
        domain_name=DOMAIN_NAME,
        hearing_texts=hearing_texts,
        dsl_scenarios=scenarios,
        converter_code=converter_code,
        solver_code=solver_code,
    )

    # (a) 現行方式: converter + solver + ui_converter 全文
    size_a = _rough_token_estimate(converter_code, solver_code, ui_converter_code)
    print(f"[入力サイズ目安(概算)] (a) converter+solver+ui_converter: 約{size_a}トークン")
    t0 = time.perf_counter()
    result_a = detect_hearing_dsl_gaps(ui_converter_code=ui_converter_code, **common_kwargs)
    _summarize("(a) ui_converter込み（現行）", result_a, time.perf_counter() - t0)

    # (b) 削減案: ui_converterを渡さない（solver/converterのみ）
    size_b = _rough_token_estimate(converter_code, solver_code)
    print(f"\n[入力サイズ目安(概算)] (b) converter+solverのみ: 約{size_b}トークン "
          f"（(a)比 -{100 * (size_a - size_b) / size_a:.0f}%）")
    t0 = time.perf_counter()
    result_b = detect_hearing_dsl_gaps(ui_converter_code=None, **common_kwargs)
    _summarize("(b) ui_converter抜き（削減案）", result_b, time.perf_counter() - t0)

    print(
        "\n[判定目安] (b)のgap/required_gap件数が(a)と大きくズレる（特に画面表示系の"
        "gapが(b)で検出漏れになる）なら、ui_converter.pyを削るのは精度リスクが高く"
        "見送るべき。ほぼ一致するなら、画面表示要件が薄いドメインに限定してui_converter"
        "を省略する運用は検討の余地がある。ただし2026-07-31のNursingWorkloadBalance"
        "retrospectiveでは、まさにui_converterを渡さなかったことが検出漏れの原因に"
        "なった前例があるため、再現性（他ドメインでも試す）を確認してから判断すること。"
    )


if __name__ == "__main__":
    main()
