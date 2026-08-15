"""
Backend/tools/stage_b_selfrepair_timing.py

2026-08-05追加（registration_time_reduction_plan.md タスク7、段階B）。

背景: 段階A（ENGINEERING_LOG.md 2026-08-05追記）の時系列分解で、Gate2再検証
3分9秒のうち、detect_hearing_dsl_gaps()より先に走る「Gate2 self-repair」
（attempt_field_consistency_repair() → _call_field_repair_llm()、converter/solver
のキー不一致をLLMがファイル全文書き換えで自動修正する処理）の方が時間コストが
大きい可能性が浮上した。ただしログのタイムスタンプはポーリング間隔（約2秒）
単位の粒度しかなく、self-repair呼び出し自体の所要時間を直接測れていなかった。

本スクリプトは、既に手元にあるLotSizingSchedulerの生成済みconverter/solverに
**意図的なキー不一致**を1件だけ仕込み、`_call_field_repair_llm()`を単体で1回
呼び出して、`time.perf_counter()`で実時間を直接計測する。新規ドメイン登録は
一切行わない（段階Bの方針通り、1〜数コールで完結）。

使い方:
  cd Backend
  # このリポジトリのサンドボックスではAnthropic APIへの認証付きリクエストが
  # プロキシでブロックされることを確認済み（2026-08-05）。Koshoshiの実環境
  # （.envのANTHROPIC_API_KEYが実際に使える環境）で実行してください。
  python3 tools/stage_b_selfrepair_timing.py
"""
import sys
import time

sys.path.insert(0, ".")

from domain_generator import (  # noqa: E402
    check_converter_solver_field_consistency,
    _call_field_repair_llm,
    _field_check_total,
)

SNAKE = "lot_sizing_scheduler"
CONVERTER_FILE = f"dsl_transformer/{SNAKE}_converter.py"
SOLVER_FILE = f"solvers/{SNAKE}_solver.py"


def main() -> None:
    converter_code = open(CONVERTER_FILE, encoding="utf-8").read()
    solver_code = open(SOLVER_FILE, encoding="utf-8").read()

    # 意図的にキー不一致を作る: converterの出力キー名を書き換えて、
    # solver側が参照する "setup_costs" を一度も出力しないようにする。
    needle = '"setup_costs":    setup_costs,'
    if needle not in converter_code:
        print(f"[stage_b_selfrepair_timing] 想定していたキー行が見つかりません: {needle!r}")
        print("converter.pyの実装が変わっている可能性があります。別のキーで再仕込みしてください。")
        return
    mismatched_converter = converter_code.replace(needle, '"setup_cost_x":   setup_costs,')

    before = check_converter_solver_field_consistency(mismatched_converter, solver_code)
    print("[before] missing_in_converter:", before["missing_in_converter"])
    print("[before] unused_in_solver:", before["unused_in_solver"])
    print("[before] total:", _field_check_total(before))
    if _field_check_total(before) == 0:
        print("[stage_b_selfrepair_timing] 不一致を仕込めていません。中断します。")
        return

    converter_path = f"Backend/dsl_transformer/{SNAKE}_converter.py"
    solver_path = f"Backend/solvers/{SNAKE}_solver.py"

    t0 = time.perf_counter()
    new_converter, new_solver = _call_field_repair_llm(
        converter_path, mismatched_converter, solver_path, solver_code, SNAKE, before
    )
    elapsed = time.perf_counter() - t0

    print(f"\n[timing] self-repair 1回（LLM呼び出し1回分）の所要時間: {elapsed:.1f}秒")
    print("[result] new_converter is None:", new_converter is None)
    print("[result] new_solver is None:", new_solver is None)

    if new_converter and new_solver:
        after = check_converter_solver_field_consistency(new_converter, new_solver)
        print("[after] missing_in_converter:", after["missing_in_converter"])
        print("[after] unused_in_solver:", after["unused_in_solver"])
        print("[after] total:", _field_check_total(after))
        print(
            "\n[判定目安] elapsed が2回目Gate2再検証全体（実測3分9秒）の"
            "半分以上を占めるなら、self-repair削減（例: 差分パッチのみ返させる・"
            "キャッシュ可能な形にプロンプトを再設計する等）を段階Cの前に"
            "優先検討する価値がある。"
        )


if __name__ == "__main__":
    main()
