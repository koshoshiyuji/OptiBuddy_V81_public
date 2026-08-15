"""
Backend/tools/stage_b_debug_agent_edit_file_ab_test.py

2026-08-09追加。①(hearing_dsl_gaps差分渡し)・②(debug_agent edit_file)の両方を、
新規ドメイン登録の費用（Stage1a/Stage2）をかけずに実測する。

背景: EnergyCostAwareScheduler（prob059）の実登録でこの2つを実測しようとしたが、
Gate2の指摘5件のうち1件が「実行検証カテゴリ」（dynamic_check由来）に該当し、
2026-08-08の既存方針（このカテゴリはdebug_agentの自動修正ラウンドを丸ごとスキップし
直接人間判断に回す）により、debug_agent.run_debug_agent()が一度も呼ばれなかった
（詳細はENGINEERING_LOG.md 2026-08-09追記4参照）。

今回は、既に登録済みのEnergyCostAwareSchedulerに実在する本物のhearing_coverage系
ギャップ（config.get("priority_customer_names")は未完了オーダーの警告severity
判定にのみ使われており、目的関数のスケジューリング優先順位には一切反映されていない
＝ヒアリング5節「得意先優先完了」が未実装）を使い、debug_agent.run_debug_agent()を
直接呼び出す。実行検証カテゴリの文言（「（自動チェック・実装エラーの疑い／要コード
修正）」「（自動チェック・実行検証）」）を含まない、通常のhearing_coverage系の指摘
文言を渡すため、確実にdebug_agentの本体ループ（read_file/edit_file/write_file/
verify_gate2）が動く。

②の効果測定は、同一の指摘・同一の出発コードに対して以下2回を比較するA/Bテスト。
  (A) disable_edit_file=True  — edit_file導入前の挙動（write_fileのみ）を再現
  (B) disable_edit_file=False — edit_file込みの現行挙動

①の効果測定は、(B)完了後のsolver.py差分を使い、
  (a) 元コードでdetect_hearing_dsl_gaps()を全文渡しで実行（prior_result役）
  (b) (a)の結果 + (B)で生じた差分をdetect_hearing_dsl_gaps_incremental()に渡す
  (c) (B)後の新コードでdetect_hearing_dsl_gaps()を全文渡しで実行（正解データ）
を比較する（既存のstage_b_hearing_dsl_gaps_diff_mode_validation.pyと同じ方式）。

注意: (A)実行後は、(B)を公平な条件で開始するため、ファイルを実行前の内容に
必ず復元する。最終的にディスクに残るのは(B)（edit_file込み、実運用と同じ設定）の
結果であり、EnergyCostAwareSchedulerの実際の品質改善（得意先優先の実装）としても
残る。

使い方:
  cd Backend
  # Koshoshiの実環境（.envのANTHROPIC_API_KEYが実際に使える環境）で実行してください。
  python3 tools/stage_b_debug_agent_edit_file_ab_test.py
"""
import json
import logging
import re
import sys
import time
from pathlib import Path

sys.path.insert(0, ".")

from debug_agent import run_debug_agent  # noqa: E402
from domain_generator import (  # noqa: E402
    detect_hearing_dsl_gaps,
    detect_hearing_dsl_gaps_incremental,
    build_code_diff,
)

SNAKE = "energy_cost_aware_scheduler"
DOMAIN_NAME = "EnergyCostAwareScheduler"

SOLVER_PATH = Path(f"solvers/{SNAKE}_solver.py")
CONVERTER_PATH = Path(f"dsl_transformer/{SNAKE}_converter.py")
UI_CONVERTER_PATH = Path(f"dsl_transformer/{SNAKE}_ui_converter.py")
BASELINE_SCENARIO_PATH = Path(f"dsl_repository/scenarios/{SNAKE}_baseline.json")
INFEASIBLE_SCENARIO_PATH = Path(f"dsl_repository/scenarios/{SNAKE}_infeasible.json")
HEARING_PATH = Path("../docs/test_hearings/energy_cost_aware_scheduler_hearing_sheet_j_v1.md")

# 2026-08-09追加: 「バグ入り」出発点の固定スナップショット。
# ディスク上のsolvers/energy_cost_aware_scheduler_solver.pyは前回実行で既に
# 修正済み（PRIORITY_CUSTOMER_WEIGHT導入済み）になっているため、「毎回ディスクの
# 現在の内容を読む」と2回目以降のA/Bテストが「既に直っているか確認するだけ」に
# なってしまう事故が実際に起きた。original_solver_codeは必ずこのスナップショットから
# 読み、ディスクの現在の状態には依存しない。
ORIGINAL_SNAPSHOT_PATH = Path("tools/_energy_cost_aware_scheduler_solver_PRE_PRIORITY_FIX.py")

WRITTEN_PATHS = [f"Backend/{SOLVER_PATH.as_posix()}"]

QUESTION = (
    "ヒアリングセクション5「できれば守りたいルール」に記載された「特定の得意先向けの"
    "オーダーは、優先的に期限内に完了させたい」という要件が、目的関数に反映されて"
    "いません。現在の実装では config.get(\"priority_customer_names\", []) は、"
    "未完了オーダーの警告severity（CRITICAL/WARNING）の判定にのみ使われており、"
    "スケジューリングそのものの優先順位には一切影響していません。lexicographicな"
    "目的関数に、優先得意先のオーダーが完了しやすくなるよう追加の項（例: 優先得意先の"
    "未完了オーダー数を通常オーダーより重く数える）を導入するなど、この要件が実際の"
    "スケジューリング結果に反映されるように改善してください。"
)

# 2026-08-09追加: ask_humanで停止した場合に自動で返す回答。A/B両条件で同一の
# 回答を与えることで、「質問への回答内容の違い」がA/B比較の交絡要因にならない
# ようにする。方針だけ示し、重みの具体値等はエージェントの判断に委ねる。
AUTO_HUMAN_ANSWER = (
    "優先得意先のオーダーが完了しやすくなるよう、目的関数に適切な重み付けを追加する"
    "方法で対応してください。重みの具体的な値や実装方法はあなたの判断で構いません。"
)


class _UsageCapture(logging.Handler):
    """debug_agentロガーの「usage（Nターン目）」INFOログを正規表現で拾い、
    cache_read/cache_creationの推移をターン単位でリスト化する。
    run_debug_agent()の戻り値契約を変えずに計測するための一時ハンドラ。
    """

    _RE = re.compile(
        r"usage（(?P<turn>\d+)ターン目）: cache_read=(?P<cache_read>\d+), "
        r"cache_creation=(?P<cache_creation>\d+), input=(?P<input>\d+)"
    )

    def __init__(self):
        super().__init__(level=logging.INFO)
        self.turns: list[dict] = []
        self.write_file_rejections = 0
        self.edit_file_calls = 0
        self.write_file_calls = 0

    def emit(self, record):
        msg = record.getMessage()
        m = self._RE.search(msg)
        if m:
            self.turns.append({
                "turn": int(m.group("turn")),
                "cache_read": int(m.group("cache_read")),
                "cache_creation": int(m.group("cache_creation")),
                "input": int(m.group("input")),
            })
        if "write_file拒否" in msg:
            self.write_file_rejections += 1
        if msg.startswith("[debug_agent] edit_file:"):
            self.edit_file_calls += 1
        if msg.startswith("[debug_agent] write_file:"):
            self.write_file_calls += 1


def _run_one(label: str, disable_edit_file: bool, original_solver_code: str) -> dict:
    """
    2026-08-09修正: 初回実行がask_humanでwaiting_for_humanになった場合、
    AUTO_HUMAN_ANSWERで自動的に回答してresume_stateから再開し、done/max_turns/
    error/interruptedのいずれかに達するまで繰り返す。これにより「質問して停止した
    だけの未完了ラン」と「実際に最後まで修正した完了ラン」を比較してしまう不公平を
    避ける（初回実行で発生した実際の事故：A条件が2ターン目でwaiting_for_humanに
    なり、write_fileを一度も呼ばないまま終わっていた）。segments間でcapture
    ハンドラのターン数・トークン量を積算する。安全のため再開ループ回数に上限を設ける。
    """
    capture = _UsageCapture()
    logger = logging.getLogger("debug_agent")
    logger.addHandler(capture)
    logger.setLevel(logging.INFO)

    # 出発状態を必ず揃える
    SOLVER_PATH.write_text(original_solver_code, encoding="utf-8")

    hearing_text = HEARING_PATH.read_text(encoding="utf-8")

    t0 = time.perf_counter()
    result = run_debug_agent(
        questions=[QUESTION],
        written_paths=WRITTEN_PATHS,
        domain_name=DOMAIN_NAME,
        hearing_texts=[hearing_text],
        snake_name=SNAKE,
        max_turns=8,
        disable_edit_file=disable_edit_file,
    )

    resume_rounds = 0
    while result.get("stopped_reason") == "waiting_for_human" and resume_rounds < 5:
        resume_rounds += 1
        print(f"    [{label}] ask_humanで停止（質問: {result.get('pending_question')!r}）。"
              f"自動回答して再開します（{resume_rounds}回目）。")
        result = run_debug_agent(
            questions=[QUESTION],
            written_paths=WRITTEN_PATHS,
            domain_name=DOMAIN_NAME,
            hearing_texts=[hearing_text],
            snake_name=SNAKE,
            max_turns=8,
            disable_edit_file=disable_edit_file,
            resume_state=result["resume_state"],
            human_answer=AUTO_HUMAN_ANSWER,
        )
    elapsed = time.perf_counter() - t0

    logger.removeHandler(capture)

    if result.get("stopped_reason") == "waiting_for_human":
        print(f"    [{label}] 警告: 再開上限（5回）に達してもwaiting_for_humanのままでした。")

    final_code = SOLVER_PATH.read_text(encoding="utf-8")
    total_cache_read = sum(t["cache_read"] for t in capture.turns)
    total_cache_creation = sum(t["cache_creation"] for t in capture.turns)

    print(f"\n[{label}] disable_edit_file={disable_edit_file}")
    print(f"    elapsed={elapsed:.1f}秒  turns_used={result.get('turns_used')}  "
          f"stopped_reason={result.get('stopped_reason')}  resume_rounds={resume_rounds}")
    print(f"    actions={result.get('actions')}")
    print(f"    edit_file呼び出し={capture.edit_file_calls}件  write_file呼び出し={capture.write_file_calls}件  "
          f"write_file拒否={capture.write_file_rejections}件")
    print(f"    ターン別usage: {capture.turns}")
    print(f"    累計 cache_read={total_cache_read}  累計 cache_creation={total_cache_creation}  "
          f"合計={total_cache_read + total_cache_creation}")
    print(f"    fixed_summary: {result.get('fixed_summary')}")
    if result.get("needs_human_decision"):
        print(f"    needs_human_decision: {result.get('needs_human_decision')}")

    return {
        "label": label,
        "elapsed": elapsed,
        "result": result,
        "final_code": final_code,
        "total_cache_read": total_cache_read,
        "total_cache_creation": total_cache_creation,
        "edit_file_calls": capture.edit_file_calls,
        "write_file_calls": capture.write_file_calls,
        "write_file_rejections": capture.write_file_rejections,
        "resume_rounds": resume_rounds,
    }


def main() -> None:
    # ディスク上の現在のsolver.pyではなく、固定の「バグ入り」スナップショットを
    # 出発点として使う（前述の通り、ディスクは前回実行で修正済みになっているため）。
    original_solver_code = ORIGINAL_SNAPSHOT_PATH.read_text(encoding="utf-8")
    converter_code = CONVERTER_PATH.read_text(encoding="utf-8")
    ui_converter_code = UI_CONVERTER_PATH.read_text(encoding="utf-8")
    baseline = json.load(open(BASELINE_SCENARIO_PATH, encoding="utf-8"))
    infeasible = json.load(open(INFEASIBLE_SCENARIO_PATH, encoding="utf-8"))
    scenarios = {"baseline": baseline, "infeasible": infeasible}
    hearing_text = HEARING_PATH.read_text(encoding="utf-8")

    print("=" * 70)
    print("② debug_agent edit_file A/Bテスト")
    print("=" * 70)

    # --- (A) edit_file無効化（旧挙動再現） ---
    run_a = _run_one("(A) edit_file無効（旧write_fileのみ）", True, original_solver_code)

    # (A)実行後、(B)を同じ出発状態から開始するため復元
    SOLVER_PATH.write_text(original_solver_code, encoding="utf-8")

    # --- (B) edit_file有効（現行） ---
    run_b = _run_one("(B) edit_file有効（現行）", False, original_solver_code)

    print("\n" + "=" * 70)
    print("② A/B比較サマリ")
    print("=" * 70)
    print(f"    (A) 所要時間={run_a['elapsed']:.1f}秒  合計トークン(cache_read+creation)="
          f"{run_a['total_cache_read'] + run_a['total_cache_creation']}  "
          f"turns={run_a['result'].get('turns_used')}")
    print(f"    (B) 所要時間={run_b['elapsed']:.1f}秒  合計トークン(cache_read+creation)="
          f"{run_b['total_cache_read'] + run_b['total_cache_creation']}  "
          f"turns={run_b['result'].get('turns_used')}")
    a_done = run_a["result"].get("stopped_reason") == "done"
    b_done = run_b["result"].get("stopped_reason") == "done"
    if not (a_done and b_done):
        print(f"    警告: (A)stopped_reason={run_a['result'].get('stopped_reason')} / "
              f"(B)stopped_reason={run_b['result'].get('stopped_reason')} — "
              "両方が'done'（正常完了）でない場合、以下の比較は公平ではありません。")
    if run_a["elapsed"] > 0:
        pct = 100 * (run_a["elapsed"] - run_b["elapsed"]) / run_a["elapsed"]
        print(f"    (B)は(A)比 {pct:.0f}% {'短縮' if pct >= 0 else '悪化'}"
              f"{'（参考値。上記警告参照）' if not (a_done and b_done) else ''}")

    # --- ① hearing_dsl_gaps 差分渡しの効果測定（(B)後の差分を利用） ---
    print("\n" + "=" * 70)
    print("① hearing_dsl_gaps 差分渡し 効果測定（(B)完了後のsolver.py差分を利用）")
    print("=" * 70)

    new_solver_code = run_b["final_code"]
    if new_solver_code == original_solver_code:
        print("    警告: (B)でsolver.pyに変更が無かったため、①の測定はスキップします。")
        return

    common_kwargs = dict(
        domain_name=DOMAIN_NAME,
        hearing_texts=[hearing_text],
        dsl_scenarios=scenarios,
        converter_code=converter_code,
        ui_converter_code=ui_converter_code,
    )

    t0 = time.perf_counter()
    result_a = detect_hearing_dsl_gaps(solver_code=original_solver_code, **common_kwargs)
    elapsed_a = time.perf_counter() - t0
    print(f"\n    (a) 修正前フルコード（前回判定役）: {elapsed_a:.1f}秒  "
          f"gap={result_a.get('gap_count')}件")

    solver_diff = build_code_diff(original_solver_code, new_solver_code, "solver.py")
    t0 = time.perf_counter()
    result_b = detect_hearing_dsl_gaps_incremental(
        domain_name=DOMAIN_NAME, hearing_texts=[hearing_text],
        prior_result=result_a, converter_diff=None, solver_diff=solver_diff,
        ui_converter_diff=None,
    )
    elapsed_b = time.perf_counter() - t0
    print(f"    (b) 差分渡し版: {elapsed_b:.1f}秒  gap={result_b.get('gap_count')}件")

    t0 = time.perf_counter()
    result_c = detect_hearing_dsl_gaps(solver_code=new_solver_code, **common_kwargs)
    elapsed_c = time.perf_counter() - t0
    print(f"    (c) 修正後フルコード（正解データ）: {elapsed_c:.1f}秒  "
          f"gap={result_c.get('gap_count')}件")

    if elapsed_c > 0:
        pct = 100 * (elapsed_c - elapsed_b) / elapsed_c
        print(f"\n    (b)は(c)比 {pct:.0f}% {'短縮' if pct >= 0 else '悪化'}")

    print("\n完了。最終的にディスクに残っているsolver.pyは(B)（edit_file有効）の結果です。")


if __name__ == "__main__":
    main()
