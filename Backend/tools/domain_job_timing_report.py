"""
Backend/tools/domain_job_timing_report.py

ドメイン登録処理（/api/domain/run パイプライン）の工程別所要時間レポート。
2026-08-03追加（Koshoshi依頼: 処理時間短縮を検討する前に、まず実測の基礎データが
必要という方針に基づく）。

背景:
  dsl_repository/repository.py の DslRepository.job_set() を改修し、
  domain_jobs のstageが変化するたびに stage_timeline に {"stage", "ts"} を
  自動記録するようにした。さらに、ジョブが done/error に到達した時点で
  そのスナップショットを domain_job_timing_log（GC対象外の恒久テーブル）に
  保存するようにした。本スクリプトはそのログを集計し、工程ごとの所要時間の
  分布（件数・平均・中央値・最小・最大）をレポートする。

前提・制約:
  - この変更が入る前（2026-08-03より前）に完了したジョブにはtimelineが
    無いため、遡及集計はできない。今後の登録実行分から蓄積される。
  - サンプル数が少ないうちは平均・中央値ともに参考値に留まる点に注意。

使い方:
  cd Backend
  python3 tools/domain_job_timing_report.py
  python3 tools/domain_job_timing_report.py --csv out.csv
  python3 tools/domain_job_timing_report.py --db dsl_repository/optibuddy.db
"""
import argparse
import csv
import json
import sqlite3
import statistics
import sys
from pathlib import Path

BACKEND_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_DB = BACKEND_ROOT / "dsl_repository" / "optibuddy.db"

# job_set()に渡されるstage文字列の並び順（表示順の目安。未知のstageが
# 出てきても集計自体は問題なく行われる——単に表示順が最後に回るだけ）。
STAGE_ORDER = [
    "queued",
    "interpreting",
    "stage1a_classifying",
    "stage1a4_axis_a_fit_check",
    "stage1a5_extension_gap_check",
    "stage1b_scenario_gen",
    "generating",
    "needs_confirmation",
    "queued_confirm",
    "fixing",
    "waiting_for_agent_question",
    "verifying",
    # 2026-08-07追加: run_gate2_checks()内のon_stage計装によるverifyingの内訳
    # （Koshoshi依頼、Stage2時間内訳を①LLM生成本体/②Gate2静的チェック/
    # ③Gate2動的検証(実ソルブ)の最低3分割で見られるようにする対応）。
    # 旧ログ（2026-08-07より前）には出てこず、単一の"verifying"のままになる。
    "verifying_self_repair",       # Gate2自己修復ループ（converter/solverキー不一致のLLM自動修正）
    "verifying_static_check",      # 静的field-check等（AST走査、非LLM）＝②に相当
    "verifying_hearing_coverage",  # ヒアリング⇔コード カバレッジLLM判定
    "verifying_tech_conformance",  # Gate2構造適合チェック
    "verifying_dynamic_check",     # 実ソルブ検証＝③に相当
    "verifying_humanize",          # 指摘の業務向け言い換え
    "applying",
    "applying_files",
    "done",
    "error",
]

# 2026-08-04追加: domain_jobsテーブル（job_set()）は /api/domain/run 以外にも
# /relax/start 等、他のバックグラウンドジョブ機構からも共用されている
# （job_set(job_id, **fields)という汎用インターフェースのため）。これらは
# domain_nameを一切渡さない一方、stage名の一部（generating/verifying/done等）が
# たまたまドメイン登録と重複するため、フィルタせずに集計するとworks混ざって
# 実態と異なる平均値になる（実機で確認: /relax/startの21秒ジョブが混入し、
# ドメイン登録全体の平均が実態より大幅に短く出た）。domain_nameが設定されている
# 行のみをドメイン登録ジョブとして扱う。
#
# また、needs_confirmation/queued_confirm/waiting_for_agent_questionの3stageは
# 「システムが処理中」ではなく「人間（業務担当者）の回答待ち」の時間を表す。
# 「登録処理を短縮したい」という目的に対しては、システム処理時間と人間の
# 回答待ち時間を混同すると誤った結論（「Stage2が遅い」等）を導きかねないため、
# 集計上も明示的に分離する。
_HUMAN_WAIT_STAGES = {"needs_confirmation", "queued_confirm", "waiting_for_agent_question"}


def _stage_sort_key(stage: str) -> tuple:
    try:
        return (0, STAGE_ORDER.index(stage))
    except ValueError:
        return (1, stage)


def load_rows(db_path: Path, include_non_registration: bool = False) -> tuple[list[dict], int]:
    """
    戻り値: (ドメイン登録ジョブの行リスト, 除外した非登録ジョブの件数)
    """
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    try:
        rows = conn.execute(
            "SELECT job_id, domain_name, match_type, final_stage, created_at, "
            "finished_at, stage_timeline, logged_at FROM domain_job_timing_log "
            "ORDER BY logged_at ASC"
        ).fetchall()
    except sqlite3.OperationalError as e:
        print(f"domain_job_timing_log テーブルが見つかりません（{e}）。"
              "先に repository.py の変更を反映したapp.pyでドメイン登録を"
              "最低1件実行してください。", file=sys.stderr)
        sys.exit(1)
    finally:
        conn.close()
    all_rows = [dict(r) for r in rows]
    if include_non_registration:
        return all_rows, 0
    reg_rows = [r for r in all_rows if r.get("domain_name")]
    excluded = len(all_rows) - len(reg_rows)
    return reg_rows, excluded


def compute_stage_durations(rows: list[dict]) -> dict[str, list[float]]:
    """
    工程名 -> [その工程にかかった秒数のリスト] を返す。
    「工程Xの所要時間」= timeline上でXの次に来るエントリのts - Xのts。
    最後のエントリ（done/error自体）は「次」が無いため所要時間は計上しない
    （到達した瞬間のマーカーであり、区間ではないため）。
    """
    durations: dict[str, list[float]] = {}
    for row in rows:
        try:
            timeline = json.loads(row["stage_timeline"])
        except (TypeError, json.JSONDecodeError):
            continue
        for i in range(len(timeline) - 1):
            stage = timeline[i]["stage"]
            dt = timeline[i + 1]["ts"] - timeline[i]["ts"]
            if dt < 0:
                continue
            durations.setdefault(stage, []).append(dt)
    return durations


def compute_total_durations(rows: list[dict]) -> list[float]:
    totals = []
    for row in rows:
        created = row.get("created_at")
        finished = row.get("finished_at")
        if created and finished and finished >= created:
            totals.append(finished - created)
    return totals


def compute_human_vs_system_split(rows: list[dict]) -> list[dict]:
    """
    ジョブごとに、合計所要時間を「人間の回答待ち（_HUMAN_WAIT_STAGES滞留分）」と
    「システム処理（それ以外）」に分割する。
    戻り値: [{"job_id", "domain_name", "total", "human_wait", "system"}, ...]
    """
    result = []
    for row in rows:
        try:
            timeline = json.loads(row["stage_timeline"])
        except (TypeError, json.JSONDecodeError):
            continue
        human_wait = 0.0
        system = 0.0
        for i in range(len(timeline) - 1):
            dt = timeline[i + 1]["ts"] - timeline[i]["ts"]
            if dt < 0:
                continue
            if timeline[i]["stage"] in _HUMAN_WAIT_STAGES:
                human_wait += dt
            else:
                system += dt
        result.append({
            "job_id": row["job_id"],
            "domain_name": row.get("domain_name"),
            "total": human_wait + system,
            "human_wait": human_wait,
            "system": system,
        })
    return result


def format_seconds(sec: float) -> str:
    if sec < 60:
        return f"{sec:.1f}秒"
    m, s = divmod(sec, 60)
    return f"{int(m)}分{s:.0f}秒"


def print_report(rows: list[dict], excluded: int = 0) -> None:
    print(f"集計対象ジョブ数（ドメイン登録のみ）: {len(rows)}件")
    if excluded:
        print(f"  ※ domain_nameが無い{excluded}件（/relax等、登録以外のジョブ）は除外しました。"
              "含めたい場合は --include-non-registration を指定してください。")
    done_n = sum(1 for r in rows if r["final_stage"] == "done")
    err_n = sum(1 for r in rows if r["final_stage"] == "error")
    print(f"  内訳: done={done_n}件, error={err_n}件")
    if not rows:
        print("\nデータがまだありません。ドメイン登録を実行すると"
              "domain_job_timing_log に蓄積されます。")
        return

    print("\n--- 工程別所要時間（区間: そのstageに滞留していた時間） ---")
    print("    （※ needs_confirmation / queued_confirm / waiting_for_agent_question は"
          "「人間の回答待ち」時間。システムの処理時間ではない点に注意）")
    durations = compute_stage_durations(rows)
    header = f"{'工程':32s} {'件数':>5s} {'平均':>10s} {'中央値':>10s} {'最小':>10s} {'最大':>10s}"
    print(header)
    print("-" * len(header))
    for stage in sorted(durations.keys(), key=_stage_sort_key):
        vals = durations[stage]
        tag = " (人間待ち)" if stage in _HUMAN_WAIT_STAGES else ""
        print(
            f"{stage:32s} {len(vals):>5d} "
            f"{format_seconds(statistics.mean(vals)):>10s} "
            f"{format_seconds(statistics.median(vals)):>10s} "
            f"{format_seconds(min(vals)):>10s} "
            f"{format_seconds(max(vals)):>10s}{tag}"
        )

    totals = compute_total_durations(rows)
    if totals:
        print("\n--- 合計所要時間（created_at → done/error） ---")
        print(f"件数={len(totals)}, 平均={format_seconds(statistics.mean(totals))}, "
              f"中央値={format_seconds(statistics.median(totals))}, "
              f"最小={format_seconds(min(totals))}, 最大={format_seconds(max(totals))}")

    split = compute_human_vs_system_split(rows)
    if split:
        print("\n--- ジョブ別: システム処理時間 vs 人間の回答待ち時間 ---")
        header2 = f"{'ドメイン名':24s} {'合計':>10s} {'システム':>10s} {'人間待ち':>10s} {'人間待ち比率':>10s}"
        print(header2)
        print("-" * len(header2))
        for s in split:
            ratio = f"{round(s['human_wait'] / s['total'] * 100)}%" if s["total"] > 0 else "-"
            print(
                f"{(s['domain_name'] or '')[:24]:24s} "
                f"{format_seconds(s['total']):>10s} "
                f"{format_seconds(s['system']):>10s} "
                f"{format_seconds(s['human_wait']):>10s} "
                f"{ratio:>10s}"
            )


def write_csv(rows: list[dict], out_path: Path) -> None:
    """
    ジョブ単位・stage区間単位の明細をCSVに書き出す（Excel等での追加分析用）。
    """
    durations_by_job = []
    for row in rows:
        try:
            timeline = json.loads(row["stage_timeline"])
        except (TypeError, json.JSONDecodeError):
            continue
        for i in range(len(timeline) - 1):
            durations_by_job.append({
                "job_id": row["job_id"],
                "domain_name": row["domain_name"],
                "match_type": row["match_type"],
                "final_stage": row["final_stage"],
                "stage": timeline[i]["stage"],
                "duration_sec": round(timeline[i + 1]["ts"] - timeline[i]["ts"], 3),
            })
    with open(out_path, "w", newline="", encoding="utf-8-sig") as f:
        writer = csv.DictWriter(
            f, fieldnames=["job_id", "domain_name", "match_type", "final_stage",
                           "stage", "duration_sec"])
        writer.writeheader()
        writer.writerows(durations_by_job)
    print(f"\nCSVを書き出しました: {out_path} ({len(durations_by_job)}行)")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", type=Path, default=DEFAULT_DB,
                         help="optibuddy.dbのパス（既定: dsl_repository/optibuddy.db）")
    parser.add_argument("--csv", type=Path, default=None,
                         help="指定するとstage区間明細をこのパスにCSV出力する")
    parser.add_argument("--include-non-registration", action="store_true",
                         help="/relax等、ドメイン登録以外のジョブ（domain_name無し）も含めて集計する")
    args = parser.parse_args()

    if not args.db.exists():
        print(f"DBファイルが見つかりません: {args.db}", file=sys.stderr)
        sys.exit(1)

    rows, excluded = load_rows(args.db, include_non_registration=args.include_non_registration)
    print_report(rows, excluded=excluded)
    if args.csv:
        write_csv(rows, args.csv)


if __name__ == "__main__":
    main()
