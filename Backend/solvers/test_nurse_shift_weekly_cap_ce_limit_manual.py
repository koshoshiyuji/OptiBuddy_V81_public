"""
Backend/solvers/test_nurse_shift_weekly_cap_ce_limit_manual.py

NurseShiftWeeklyCapで、CPLEXの無料版（Community Edition）の上限に実際に
当たった時の自動切り替え（グループ分けして順番に解く処理）を、実機のCPLEX/
CP Optimizerで動作確認するための手動実行スクリプト。

このスクリプトは pytest 経由ではなく、実際にCP Optimizerがインストールされた
環境（cpoptimizer実行ファイルが必要）で直接実行することを想定している。
このセッションのサンドボックスにはCP Optimizerの実行ファイルが無いため、
ここでは動作確認できていない（pip版のdocplex/cplexにはCP Optimizerの
実行エンジンが含まれないため）。実際にCPLEXが入っている環境（Koshoshiさんの
手元環境）で実行して確認すること。

規模の考え方:
  候補（割り当て候補＝スタッフ×タスクの組み合わせのうちresourceの条件に合うもの）
  が1000件を超えると、CPLEXの無料版では解けなくなる。
  このスクリプトはスタッフ40人×タスク30個＝候補最大1200件になるように
  作っているので、実際に無料版の上限を超えるはず。

  グループ分けのしきい値（1グループ何人まで）は既定30人なので、
  40人なら「30人のグループ」＋「10人のグループ」の2グループに分かれて
  解かれるはず。

実行方法:
    cd Backend
    python3 solvers/test_nurse_shift_weekly_cap_ce_limit_manual.py

期待される結果:
  - 例外で落ちずに完了すること
  - ログに「CPLEXの無料版の上限を検知」「グループ分けして順番に解く処理に
    切り替えます」が出ること（無料版でなければ出ない。正規ライセンス環境では
    そもそも上限に当たらず、グループ分けなしで一度に解けてしまう点に注意。
    その場合は STAFF_COUNT / TASK_DAYS をさらに増やすか、正規ライセンス環境
    では本テストの目的（無料版上限のフォールバック確認）自体が成立しないので
    無料版のCPLEXで実行すること）
  - 戻り値の solutions[0] に "_decompose_meta" が含まれ、
    batch_count >= 2 であること（＝実際にグループ分けが発動した証拠）
"""

import json
import logging
import sys
import time
from pathlib import Path

logging.basicConfig(level=logging.INFO, format="%(message)s")

sys.path.insert(0, str(Path(__file__).parent.parent))

from solvers.nurse_shift_weekly_cap_solver import NurseShiftWeeklyCapSolver  # noqa: E402

# -----------------------------------------------------------------------------
# 規模設定（ここを変えれば候補数を調整できる）
# -----------------------------------------------------------------------------
STAFF_COUNT = 40   # スタッフ人数
TASK_DAYS   = 10   # 日数（1日3勤務帯: 日勤/準夜勤/深夜勤）
# 候補数の目安 = STAFF_COUNT × TASK_DAYS × 3（全員が全シフトの条件を満たす前提）


def build_large_scenario(staff_count: int, task_days: int) -> dict:
    """nurse_shift_weekly_cap_baseline.jsonと同じ形（staff/tasks/config）で、
    候補数が1000件を大きく超えるスタッフ数・タスク数の疑似データを作る。"""

    staff = []
    for i in range(staff_count):
        # 3人に1人をCHIEFにする（深夜勤task.min_chiefs=1を満たせるように）
        grade = "CHIEF" if i % 3 == 0 else ("STANDARD" if i % 3 == 1 else "JUNIOR")
        staff.append({
            "id":   f"N{i:03d}",
            "name": f"看護師{i:03d}",
            "grade": grade,
            "hourly_rate": 2500 if grade == "CHIEF" else (1800 if grade == "STANDARD" else 1300),
            "skills": ["nurse"],
            "certifications": ["registered_nurse"] + (["leader"] if grade == "CHIEF" else []),
            "availability": {"start": 0, "end": task_days * 1440},
            "preferences": {"desired_tasks": []},
            "work_limits": {
                "max_consecutive_night_shifts": 2,
                "max_daily_hours": 8,
                "max_night_shifts_per_rolling_7days": 3,
                "min_rest_after_night_shift": 480,
            },
        })

    tasks = []
    shift_defs = [
        ("DAY",   480,  960,  480, False),   # 8:00-16:00 実働8h
        ("EVE",   960,  1200, 240, False),   # 16:00-20:00 実働4h
        ("NIGHT", 1200, 1920, 480, True),    # 20:00-翌8:00 実働8h（深夜勤）
    ]
    for day in range(1, task_days + 1):
        day_offset = (day - 1) * 1440
        for label, start, end, duration, is_night in shift_defs:
            tasks.append({
                "id":   f"T_{label}_D{day}",
                "name": f"{label}勤D{day}",
                "day":  day,
                "start_window": start + day_offset,
                "end_window":   end + day_offset,
                "duration":     duration,
                "is_night_shift": is_night,
                "required_count": 3,
                "min_chiefs": 1 if is_night else 0,
                "requirement": {"attr": "skills", "op": "atom", "value": "nurse"},
            })

    config = {
        "time_limit": 20,
        "preference_penalty_weight": 50,
        "rolling_window_days": 7,
        "relax_incomplete_window_at_period_end": True,
        # 既定は30。ここでは明示しておく（solvers/base/ce_limit_lns.pyの
        # run_sequential_batchに渡るグループの人数）。
        "ce_limit_batch_size": 30,
    }

    return {
        "problem_class": "NurseShiftWeeklyCap",
        "staff": staff,
        "tasks": tasks,
        "config": config,
        "issue_statuses": {},
    }


def main():
    scenario = build_large_scenario(STAFF_COUNT, TASK_DAYS)
    n_staff = len(scenario["staff"])
    n_tasks = len(scenario["tasks"])
    print(f"[準備] staff={n_staff}, tasks={n_tasks}, 候補数の目安={n_staff * n_tasks}"
          f"（全員が全シフトの資格条件を満たすため、ほぼこの数の割り当て候補＝決定変数が作られるはず）")

    # 参考: 生成したシナリオをJSONとして書き出しておく（Studio経由で別途試したい場合用）
    out_path = Path(__file__).parent.parent / "solvers" / "_ce_limit_test_scenario_generated.json"
    out_path.write_text(json.dumps(scenario, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"[準備] 生成したシナリオをJSONとしても書き出しました: {out_path}")

    print("[実行] NurseShiftWeeklyCapSolver.solve() を呼び出します...")
    t0 = time.perf_counter()
    result = NurseShiftWeeklyCapSolver(scenario).solve()
    elapsed = time.perf_counter() - t0

    print(f"[結果] status={result.get('status')}, feasible={result.get('feasible')}, "
          f"経過時間={elapsed:.1f}秒")

    # 2026-07-26修正: _decompose_metaはsolutions[0]の中ではなく、結果全体の
    # 直下（トップレベル）に入っている（nurse_shift_weekly_cap_batch_decomposer.py
    # のmerge_resultsの戻り値を参照）。誤って solutions[0] の中を見ていたため、
    # 実際にはグループ分けが発動していたのに「発動しませんでした」と誤判定していた。
    decompose_meta = result.get("_decompose_meta")

    if decompose_meta:
        print(f"[結果] グループ分けが発動しました: {decompose_meta}")
        print("[判定] OK: CPLEXの無料版の上限を検知し、自動でグループ分け処理に切り替わりました。")
    else:
        print("[結果] グループ分けは発動しませんでした（一度で解けた、または解けなかった）。")
        print("[判定] 正規ライセンス環境で実行した場合はこれが正常です"
              "（そもそも上限に当たらないため）。無料版で実行してこの結果になった場合は、"
              "STAFF_COUNT / TASK_DAYS をさらに増やして候補数を増やしてください。")

    if result.get("feasible") and result.get("solutions"):
        sol = result["solutions"][0]
        print(f"[結果] 割り当て件数={sol['metrics']['assigned_count']}, "
              f"人数不足のタスク数={sol['metrics']['understaffed_count']}")


if __name__ == "__main__":
    main()
