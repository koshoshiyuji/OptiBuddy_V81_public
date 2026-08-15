"""
Backend/tools/stage_b_debug_agent_history_summarization_test.py

2026-08-10追加。タスク5（debug_agent会話履歴肥大化対策）の段階B検証スクリプト。
段階A（ENGINEERING_LOG.md 2026-08-10追記3）で、read_fileで取得したファイル全文が
write_file/edit_file成功後も会話履歴に残り続け、以降の全ターンで再送されている
（turn5時点でその回のトークンの94%が履歴の再送）ことをコスト0のログ分析で確認した。

本スクリプトは、debug_agent.py に新設した`summarize_stale_reads`フラグ
（対応するpathへの書き込み成功後のread_file結果を短い要約プレースホルダに
置き換えてAPIへ送る。ディスク上のファイルやmessages本体は一切変更しない。
詳細はdebug_agent.py内`_summarize_stale_read_results()`のdocstring参照）の
効果を、同一の修正タスク・同一の出発コードに対する以下2回の比較で実測する。

  (A) summarize_stale_reads=False — 現行の本番挙動（履歴を一切要約しない）
  (B) summarize_stale_reads=True  — 要約を適用した新挙動

使う修正タスクは、今回のTankAllocationPlanner（prob051）実登録で実際に発見・
修正した本物の構造的バグ（目的関数に「未割当ロット数の最小化」が最優先項として
入っておらず、何も割り当てない解が常に最適になってしまう）を再現する。
出発コードは実際に発見時点のバグ入りコードのスナップショット
（tools/_tank_allocation_planner_solver_PRE_UNASSIGNED_FIX.py）を使う。
このタスクはsolver.pyの複数箇所（目的関数・metrics）にまたがる複数ターンの
read_file→edit_fileサイクルを要するため、要約対象（書き込み後に残る古いread_file
結果）が実際に発生しやすい。

比較する指標:
  - ターンごとのcache_read/cache_creation/inputトークン推移
  - 累計トークン量（cache_read+cache_creation）
  - 所要時間
  - 修正の正しさ（両条件とも最終的に同等の修正に到達したか、簡易チェック）
  - summarize_stale_reads=Trueの場合、実際に何件のread_file結果が要約に
    置換されたか（[debug_agent] summarize_stale_reads: N件... ログから集計）

注意（cache_controlとのトレードオフ、debug_agent.py本体のdocstringにも記載）:
会話履歴の内容を変えると、Anthropicのプロンプトキャッシュは変更箇所以降で
必ずミスする（最長一致prefix方式のため）。要約を適用したターンは一時的に
cache_creationが増える可能性がある。それ以降のターンで要約後の（より小さい）
内容が新しい基準としてキャッシュされ続けるため、残りターン数が十分あれば
正味の削減になる、という仮説を検証するのが本スクリプトの目的そのもの。

注意（ファイルの復元）: (A)実行後、(B)を公平な条件で開始するため、
solver.pyを出発スナップショットの内容に必ず復元する。最終的にディスクに
残るのは(B)（要約あり、検証対象の新挙動）の結果。ただし(B)がもし正しく
修正できなかった場合に備え、両条件とも実行前にmax_turnsに達しても
run_debug_agent自体はエラーにならず`stopped_reason`で判別できる設計。

使い方:
  cd Backend
  # Koshoshiの実環境（.envのANTHROPIC_API_KEYが実際に使える環境）で実行してください。
  python3 tools/stage_b_debug_agent_history_summarization_test.py
"""
import logging
import re
import sys
import time
from pathlib import Path

sys.path.insert(0, ".")

from debug_agent import run_debug_agent  # noqa: E402

SNAKE = "tank_allocation_planner"
DOMAIN_NAME = "TankAllocationPlanner"

SOLVER_PATH = Path(f"solvers/{SNAKE}_solver.py")
HEARING_PATH = Path("../docs/test_hearings/tank_allocation_planner_hearing_sheet_j.md")

# 2026-08-10追加: 実際にprob051登録時点で発見したバグ入りコードの固定スナップショット。
# ディスク上のsolvers/tank_allocation_planner_solver.pyは既に修正済み（本番運用中）
# のため、「毎回ディスクの現在の内容を読む」と2回目以降のテストが「既に直っているか
# 確認するだけ」になってしまう（stage_b_debug_agent_edit_file_ab_test.pyで実際に
# 起きた事故と同種）。original_solver_codeは必ずこのスナップショットから読み、
# ディスクの現在の状態には依存しない。
ORIGINAL_SNAPSHOT_PATH = Path("tools/_tank_allocation_planner_solver_PRE_UNASSIGNED_FIX.py")

WRITTEN_PATHS = [f"Backend/{SOLVER_PATH.as_posix()}"]

QUESTION = (
    "（自動検知・要件カバレッジ）現在の目的関数 "
    "mdl.minimize_static_lex([total_used, total_dispersion]) には、"
    "「未割当ロット数を最小化する」という項が一切含まれていません。tank_var[i]は"
    "0..n_tanksの範囲を持ち、n_tanks（ダミー値）は「そのロットをどのタンクにも"
    "割り当てない」ことを意味しますが、容量制約・相性制約はいずれも実タンク"
    "（0..n_tanks-1）に対してのみ課されており、ダミー値を選ぶことに一切ペナルティが"
    "ありません。そのため、使用タンク台数(total_used)と分散数(total_dispersion)を"
    "最小化する現在の目的関数のもとでは、「すべてのロットを未割当のままにする」"
    "（total_used=0, total_dispersion=0）が常に数学的最適解になってしまい、"
    "ヒアリング記載の「できるだけ多くのロットを割り当てたい」という要件を満たしません。"
    "目的関数の最優先項として未割当ロット数の最小化を追加し（lexicographicの1位に"
    "配置）、使用タンク台数・分散数はそれぞれ2位・3位に繰り下げてください。"
    "また、metricsに coverage_rate（=割当済みロット数/全ロット数）を追加し、"
    "Gate2の被覆率チェックが正しく機能するようにしてください。"
)

# 2026-08-10追加: ask_humanで停止した場合に自動で返す回答。A/B両条件で同一の
# 回答を与えることで、「質問への回答内容の違い」がA/B比較の交絡要因にならない
# ようにする。方針だけ示し、重みや優先順位の具体的な実装方法はエージェントの
# 判断に委ねる。
AUTO_HUMAN_ANSWER = (
    "未割当ロット数の最小化を目的関数の最優先項（lexicographicの1位）として追加し、"
    "既存の使用タンク台数・分散数はそれぞれ2位・3位に繰り下げてください。"
    "coverage_rateもmetricsに追加してください。具体的な実装方法はあなたの判断で"
    "構いません。"
)


class _UsageCapture(logging.Handler):
    """debug_agentロガーの「usage（Nターン目）」INFOログを正規表現で拾い、
    cache_read/cache_creationの推移をターン単位でリスト化する。
    summarize_stale_reads適用件数も同様にログから拾う。
    run_debug_agent()の戻り値契約を変えずに計測するための一時ハンドラ。
    """

    _RE_USAGE = re.compile(
        r"usage（(?P<turn>\d+)ターン目）: cache_read=(?P<cache_read>\d+), "
        r"cache_creation=(?P<cache_creation>\d+), input=(?P<input>\d+)"
    )
    _RE_SUMMARIZED = re.compile(
        r"summarize_stale_reads: (?P<count>\d+)件のread_file結果を要約に置換"
    )

    def __init__(self):
        super().__init__(level=logging.INFO)
        self.turns: list[dict] = []
        self.edit_file_calls = 0
        self.write_file_calls = 0
        self.summarized_events: list[int] = []  # 各ターンで要約された件数の履歴

    def emit(self, record):
        msg = record.getMessage()
        m = self._RE_USAGE.search(msg)
        if m:
            self.turns.append({
                "turn": int(m.group("turn")),
                "cache_read": int(m.group("cache_read")),
                "cache_creation": int(m.group("cache_creation")),
                "input": int(m.group("input")),
            })
        m2 = self._RE_SUMMARIZED.search(msg)
        if m2:
            self.summarized_events.append(int(m2.group("count")))
        if msg.startswith("[debug_agent] edit_file:"):
            self.edit_file_calls += 1
        if msg.startswith("[debug_agent] write_file:"):
            self.write_file_calls += 1


def _run_one(label: str, summarize_stale_reads: bool, original_solver_code: str) -> dict:
    """
    初回実行がask_humanでwaiting_for_humanになった場合、AUTO_HUMAN_ANSWERで
    自動的に回答してresume_stateから再開し、done/max_turns/error/interruptedの
    いずれかに達するまで繰り返す（stage_b_debug_agent_edit_file_ab_test.pyと
    同じ方式。「質問して停止しただけの未完了ラン」と「実際に最後まで修正した
    完了ラン」を比較してしまう不公平を避けるため）。安全のため再開ループ回数に
    上限を設ける。
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
        summarize_stale_reads=summarize_stale_reads,
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
            summarize_stale_reads=summarize_stale_reads,
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

    # 簡易正しさチェック: 最終コードに未割当最小化らしき項とcoverage_rateが
    # 導入されているか（厳密な意味解析ではなく、A/B比較の粗い健全性チェック）
    has_coverage_rate = "coverage_rate" in final_code
    has_unassigned_term = (
        "unassigned" in final_code.lower() or "未割当" in final_code
    ) and "minimize_static_lex" in final_code

    print(f"\n[{label}] summarize_stale_reads={summarize_stale_reads}")
    print(f"    elapsed={elapsed:.1f}秒  turns_used={result.get('turns_used')}  "
          f"stopped_reason={result.get('stopped_reason')}  resume_rounds={resume_rounds}")
    print(f"    actions={result.get('actions')}")
    print(f"    edit_file呼び出し={capture.edit_file_calls}件  write_file呼び出し={capture.write_file_calls}件")
    print(f"    ターン別usage: {capture.turns}")
    print(f"    ターン別 summarize_stale_reads適用件数: {capture.summarized_events}"
          f"（合計{sum(capture.summarized_events)}件）")
    print(f"    累計 cache_read={total_cache_read}  累計 cache_creation={total_cache_creation}  "
          f"合計={total_cache_read + total_cache_creation}")
    print(f"    fixed_summary: {result.get('fixed_summary')}")
    print(f"    簡易正しさチェック: coverage_rate導入={has_coverage_rate}  "
          f"未割当最小化項らしき記述={has_unassigned_term}")
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
        "summarized_total": sum(capture.summarized_events),
        "resume_rounds": resume_rounds,
        "has_coverage_rate": has_coverage_rate,
        "has_unassigned_term": has_unassigned_term,
        "turns": capture.turns,
    }


def main() -> None:
    # ディスク上の現在のsolver.py（既に本番修正済み）ではなく、固定の
    # 「バグ入り」スナップショットを出発点として使う。
    original_solver_code = ORIGINAL_SNAPSHOT_PATH.read_text(encoding="utf-8")

    print("=" * 70)
    print("debug_agent 会話履歴要約（summarize_stale_reads）A/Bテスト")
    print("=" * 70)

    # --- (A) 要約なし（現行本番挙動） ---
    run_a = _run_one("(A) summarize_stale_reads=False（現行）", False, original_solver_code)

    # (A)実行後、(B)を同じ出発状態から開始するため復元
    SOLVER_PATH.write_text(original_solver_code, encoding="utf-8")

    # --- (B) 要約あり（検証対象の新挙動） ---
    run_b = _run_one("(B) summarize_stale_reads=True（新挙動）", True, original_solver_code)

    print("\n" + "=" * 70)
    print("A/B比較サマリ")
    print("=" * 70)
    a_total = run_a["total_cache_read"] + run_a["total_cache_creation"]
    b_total = run_b["total_cache_read"] + run_b["total_cache_creation"]
    print(f"    (A) 所要時間={run_a['elapsed']:.1f}秒  合計トークン(cache_read+creation)={a_total}  "
          f"turns={run_a['result'].get('turns_used')}")
    print(f"    (B) 所要時間={run_b['elapsed']:.1f}秒  合計トークン(cache_read+creation)={b_total}  "
          f"turns={run_b['result'].get('turns_used')}  要約適用={run_b['summarized_total']}件")

    a_done = run_a["result"].get("stopped_reason") == "done"
    b_done = run_b["result"].get("stopped_reason") == "done"
    if not (a_done and b_done):
        print(f"    警告: (A)stopped_reason={run_a['result'].get('stopped_reason')} / "
              f"(B)stopped_reason={run_b['result'].get('stopped_reason')} — "
              "両方が'done'（正常完了）でない場合、以下の比較は公平ではありません。")

    if a_total > 0:
        pct = 100 * (a_total - b_total) / a_total
        print(f"    (B)は(A)比トークン量 {pct:.0f}% {'削減' if pct >= 0 else '増加'}"
              f"{'（参考値。上記警告参照）' if not (a_done and b_done) else ''}")
    if run_a["elapsed"] > 0:
        pct_t = 100 * (run_a["elapsed"] - run_b["elapsed"]) / run_a["elapsed"]
        print(f"    (B)は(A)比 所要時間 {pct_t:.0f}% {'短縮' if pct_t >= 0 else '悪化'}")

    both_correct = run_a["has_coverage_rate"] and run_a["has_unassigned_term"] \
        and run_b["has_coverage_rate"] and run_b["has_unassigned_term"]
    print(f"    簡易正しさチェック: 両条件とも修正成立={both_correct}"
          f"（(A)={run_a['has_coverage_rate'] and run_a['has_unassigned_term']}, "
          f"(B)={run_b['has_coverage_rate'] and run_b['has_unassigned_term']}）")

    print("\n完了。最終的にディスクに残っているsolver.pyは(B)（要約あり、検証対象の新挙動）の結果です。")
    print("本番切替の要否は、上記トークン削減率・所要時間・正しさチェックを踏まえてKoshoshiと相談してください。")


if __name__ == "__main__":
    main()
