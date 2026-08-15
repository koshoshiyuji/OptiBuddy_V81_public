"""
Backend/tools/stage_b_selfrepair_v2_timing.py

2026-08-05追加（registration_time_reduction_plan.md タスク7、段階B・改善案検証）。

`tools/stage_b_selfrepair_timing.py`（v1、ファイル全文書き換え型）の実測結果
（104.9秒、2回目Gate2再検証189秒の約55%）を受けて設計した改善案
`_call_field_repair_llm_v2()`（関数単位パッチ型、`===REPLACE_FUNCTION===`/
`===ADD_FUNCTION===`形式。既存のgenerate_and_apply_extensions() V2が使う
実運用検証済みのパース・適用ロジックを転用）を、**v1とまったく同じ意図的な
キー不一致**に対して単体で呼び出し、所要時間・出力の妥当性をv1と比較する。

新規ドメイン登録は行わない（段階Bの方針通り）。

使い方:
  cd Backend
  # Koshoshiの実環境（.envのANTHROPIC_API_KEYが実際に使える環境）で実行してください。
  python3 tools/stage_b_selfrepair_v2_timing.py

比較の読み方:
  v1実測（2026-08-05、Koshoshi実環境）: 104.9秒、修正成功（missing/unused とも0件）。
  このスクリプトのv2結果が同程度の精度（after totalが0件）で、かつ有意に
  短い所要時間（目安: 半分以下）であれば、本番のattempt_field_consistency_repair()
  を v2 に切り替える段階Cの検討に進める。関数が見つからない等でv2が失敗する
  場合は、_FIELD_REPAIR_SYSTEM_V2のプロンプト調整（関数名の明示指示等）を
  検討してから再測定すること。
"""
import sys
import time

sys.path.insert(0, ".")

from domain_generator import (  # noqa: E402
    check_converter_solver_field_consistency,
    _call_field_repair_llm_v2,
    _field_check_total,
)

SNAKE = "lot_sizing_scheduler"
CONVERTER_FILE = f"dsl_transformer/{SNAKE}_converter.py"
SOLVER_FILE = f"solvers/{SNAKE}_solver.py"


def main() -> None:
    converter_code = open(CONVERTER_FILE, encoding="utf-8").read()
    solver_code = open(SOLVER_FILE, encoding="utf-8").read()

    # v1（stage_b_selfrepair_timing.py）とまったく同じ仕込み方: converterの
    # 出力キー名を書き換えて、solver側が参照する "setup_costs" を一度も
    # 出力しないようにする。
    needle = '"setup_costs":    setup_costs,'
    if needle not in converter_code:
        print(f"[stage_b_selfrepair_v2_timing] 想定していたキー行が見つかりません: {needle!r}")
        print("converter.pyの実装が変わっている可能性があります。別のキーで再仕込みしてください。")
        return
    mismatched_converter = converter_code.replace(needle, '"setup_cost_x":   setup_costs,')

    before = check_converter_solver_field_consistency(mismatched_converter, solver_code)
    print("[before] missing_in_converter:", before["missing_in_converter"])
    print("[before] unused_in_solver:", before["unused_in_solver"])
    print("[before] total:", _field_check_total(before))
    if _field_check_total(before) == 0:
        print("[stage_b_selfrepair_v2_timing] 不一致を仕込めていません。中断します。")
        return

    converter_path = f"Backend/dsl_transformer/{SNAKE}_converter.py"
    solver_path = f"Backend/solvers/{SNAKE}_solver.py"

    t0 = time.perf_counter()
    new_converter, new_solver = _call_field_repair_llm_v2(
        converter_path, mismatched_converter, solver_path, solver_code, SNAKE, before
    )
    elapsed = time.perf_counter() - t0

    print(f"\n[timing] self-repair v2（関数単位パッチ）1回の所要時間: {elapsed:.1f}秒")
    print("[result] new_converter is None:", new_converter is None)
    print("[result] new_solver is None:", new_solver is None)

    if new_converter and new_solver:
        after = check_converter_solver_field_consistency(new_converter, new_solver)
        print("[after] missing_in_converter:", after["missing_in_converter"])
        print("[after] unused_in_solver:", after["unused_in_solver"])
        print("[after] total:", _field_check_total(after))
        # 変更範囲の目安: 元のconverter.pyから何文字変わったか（全文書き換えとの
        # 比較用。小さいほど「本当に必要な箇所だけ直せている」ことの傍証になる）
        changed_chars = sum(
            1 for a, b in zip(converter_code, new_converter) if a != b
        ) + abs(len(converter_code) - len(new_converter))
        print(f"[変更範囲目安] converter.py: 元{len(converter_code)}文字 → "
              f"新{len(new_converter)}文字（差分目安 約{changed_chars}文字）")
    else:
        print(
            "\n[失敗] 関数パッチの抽出・適用に失敗しました。ログの"
            "[Gate2 self-repair v2] 警告を確認してください。"
        )

    print(
        "\n[v1との比較] v1実測（2026-08-05）: 104.9秒、出力7,553トークン相当"
        "（ファイル全文書き換え、修正成功）。"
        "上記v2のelapsedがこれより有意に短ければ改善効果あり。"
    )


if __name__ == "__main__":
    main()
