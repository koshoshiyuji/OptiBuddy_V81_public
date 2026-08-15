"""
Backend/tools/llm_usage_report.py

プロンプトキャッシュのヒット率を、バックエンドログから機械的に集計するレポート。
2026-08-04追加（Koshoshi依頼: TTL修正（5分→1h）の効果を検証するのに、
[llm_usage]ログをコンソールから手でコピペするのは非効率という指摘に基づく）。

前提:
  - app.py が logs/backend.log にログを出力するよう2026-08-04に変更済み
    （それ以前に起動したバックエンドプロセスのログは対象外）。
  - 集計対象のログ行は3種類:
      a. llm_client.py _log_usage() が出す
         "[llm_usage][<tag>] input=.. cache_read=.. cache_creation=.. output=.. cache_hit_rate=..%"
      b. debug_agent.py が出す
         "[debug_agent] usage（Nターン目）: cache_read=.., cache_creation=.., input=.."
      c. debug_agent.py が出す cache diagnostics
         "[debug_agent] cache diagnostics（Nターン目）: cache_miss_reason=.., cache_missed_input_tokens=.."
        （ミスした場合のみ出力される＝ミスの理由の内訳集計に使う）

使い方:
  cd Backend
  python3 tools/llm_usage_report.py
  python3 tools/llm_usage_report.py --log logs/backend.log
  python3 tools/llm_usage_report.py --since "2026-08-04 10:00:00"

2026-08-05追記（registration_time_reduction_plan.md タスク1、段階A）:
  detect_hearing_dsl_gaps()（Gate2再検証のLLM判定部分）は call_llm() 経由で
  llm_client._call_anthropic() を呼ぶだけなので、[llm_usage]ログのtagは
  呼び出し元に関わらず一律 "_call_anthropic:<model>" になり、Stage1a等の他の
  Sonnet呼び出しと区別がつかない。
  一方、domain_generator.detect_hearing_dsl_gaps() は call_llm() の直後に
  必ず "[hearing_dsl_gaps] <domain>: total=..." をログ出力しており、両者は
  ログ上で隣接する（間にhttpx/werkzeugの雑音行が挟まることはあるが、次の
  [llm_usage]行が来る前に必ず現れる）。この隣接関係を使い、該当する
  _call_anthropic 行だけを事後的に "hearing_dsl_gaps" タグへ再分類する
  （新規ログ出力の追加は不要＝過去ログにも遡って適用できる）。
"""
import argparse
import re
import sys
from collections import defaultdict
from pathlib import Path

BACKEND_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_LOG = BACKEND_ROOT / "logs" / "backend.log"

# [llm_usage][tag] input=.. cache_read=.. cache_creation=.. output=.. cache_hit_rate=..%
RE_LLM_USAGE = re.compile(
    r"\[llm_usage\]\[(?P<tag>[^\]]+)\]\s+"
    r"input=(?P<input>\d+)\s+cache_read=(?P<cache_read>\d+)\s+"
    r"cache_creation=(?P<cache_creation>\d+)\s+output=(?P<output>\d+)"
)
# [debug_agent] usage（Nターン目）: cache_read=.., cache_creation=.., input=..
RE_DEBUG_USAGE = re.compile(
    r"\[debug_agent\] usage\（(?P<turn>\d+)ターン目\）:\s*"
    r"cache_read=(?P<cache_read>\d+),\s*cache_creation=(?P<cache_creation>\d+),\s*"
    r"input=(?P<input>\d+)"
)
# [debug_agent] cache diagnostics（Nターン目）: cache_miss_reason=xxx, cache_missed_input_tokens=NN
RE_DEBUG_MISS = re.compile(
    r"\[debug_agent\] cache diagnostics\（(?P<turn>\d+)ターン目\）:\s*"
    r"cache_miss_reason=(?P<reason>[\w_]+),\s*cache_missed_input_tokens=(?P<missed>[\d?]+)"
)
# domain_generator.detect_hearing_dsl_gaps() が call_llm() 直後に出す集計ログ。
# 隣接する _call_anthropic の [llm_usage] 行を hearing_dsl_gaps に再分類する目印。
RE_HEARING_DSL_GAPS = re.compile(r"\[hearing_dsl_gaps\]\s+(?P<domain>\S+):")

# _call_anthropic の後、hearing_dsl_gapsの目印を探して良い最大行数
# （間にhttpx/werkzeugの雑音行が数行挟まることがあるための許容幅）。
_HEARING_DSL_GAPS_LOOKAHEAD = 8


def _parse(lines):
    """タグ別に (input, cache_read, cache_creation) のリストを集める。"""
    by_tag = defaultdict(list)
    miss_reasons = defaultdict(int)

    n = len(lines)
    for i, line in enumerate(lines):
        m = RE_LLM_USAGE.search(line)
        if m:
            tag = m.group("tag")
            if tag.startswith("_call_anthropic:"):
                # 直後（雑音行を挟んでも良い）に [hearing_dsl_gaps] の集計ログが
                # 現れ、かつその前に別の [llm_usage] 行が割り込んでいなければ、
                # このAPI呼び出しは detect_hearing_dsl_gaps() 由来と判定する。
                for j in range(i + 1, min(i + 1 + _HEARING_DSL_GAPS_LOOKAHEAD, n)):
                    nxt = lines[j]
                    if RE_HEARING_DSL_GAPS.search(nxt):
                        tag = "hearing_dsl_gaps"
                        break
                    if RE_LLM_USAGE.search(nxt):
                        break
            by_tag[tag].append((
                int(m.group("input")), int(m.group("cache_read")), int(m.group("cache_creation")),
            ))
            continue
        m = RE_DEBUG_USAGE.search(line)
        if m:
            by_tag["debug_agent"].append((
                int(m.group("input")), int(m.group("cache_read")), int(m.group("cache_creation")),
            ))
            continue
        m = RE_DEBUG_MISS.search(line)
        if m:
            miss_reasons[m.group("reason")] += 1

    return by_tag, miss_reasons


def _report(by_tag, miss_reasons):
    if not by_tag:
        print("[llm_usage_report] 対象ログ行が見つかりませんでした。"
              "logs/backend.log が存在するか、対象期間内にドメイン登録/debug_agentの"
              "実行があったか確認してください。")
        return

    grand_total_in, grand_total_read, grand_total_create = 0, 0, 0

    print(f"{'tag':40s} {'calls':>6s} {'input':>10s} {'cache_read':>12s} {'cache_creation':>15s} {'hit_rate':>9s}")
    print("-" * 100)
    for tag, rows in sorted(by_tag.items()):
        t_in = sum(r[0] for r in rows)
        t_read = sum(r[1] for r in rows)
        t_create = sum(r[2] for r in rows)
        total = t_in + t_read + t_create
        hit_rate = (t_read / total * 100) if total else 0.0
        print(f"{tag:40s} {len(rows):>6d} {t_in:>10d} {t_read:>12d} {t_create:>15d} {hit_rate:>8.1f}%")
        grand_total_in += t_in
        grand_total_read += t_read
        grand_total_create += t_create

    grand_total = grand_total_in + grand_total_read + grand_total_create
    grand_hit_rate = (grand_total_read / grand_total * 100) if grand_total else 0.0
    print("-" * 100)
    print(f"{'TOTAL':40s} {'':>6s} {grand_total_in:>10d} {grand_total_read:>12d} {grand_total_create:>15d} {grand_hit_rate:>8.1f}%")

    if miss_reasons:
        print("\ncache_miss_reason 内訳（debug_agentのみ計測。ミス発生時にのみ出力される）:")
        for reason, count in sorted(miss_reasons.items(), key=lambda kv: -kv[1]):
            print(f"  {reason}: {count}件")


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--log", default=str(DEFAULT_LOG), help="対象ログファイル（既定: logs/backend.log）")
    ap.add_argument("--since", default=None,
                     help="この文字列が出現する行より前は無視する（例: タイムスタンプの一部やジョブID等の目印）")
    args = ap.parse_args()

    log_path = Path(args.log)
    if not log_path.exists():
        print(f"[llm_usage_report] ログファイルが見つかりません: {log_path}", file=sys.stderr)
        print("app.py起動後、logs/backend.log が作られているか確認してください"
              "（2026-08-04より前に起動したプロセスのログはファイル出力されていません）。", file=sys.stderr)
        sys.exit(1)

    with open(log_path, encoding="utf-8", errors="replace") as f:
        lines = f.readlines()

    if args.since:
        for i, line in enumerate(lines):
            if args.since in line:
                lines = lines[i:]
                break

    by_tag, miss_reasons = _parse(lines)
    _report(by_tag, miss_reasons)


if __name__ == "__main__":
    main()
