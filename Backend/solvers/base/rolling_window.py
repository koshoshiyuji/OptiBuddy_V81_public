"""
rolling_window.py — 層A: ウィンドウ制約（共通コア）
=======================================================

このファイルは2種類のウィンドウ制約パターンの共通コアをまとめる。

1. 日付ベース・ローリングウィンドウ「ハード」上限
   （rolling_window_ranges / add_rolling_window_cap_constraints）
2. 位置ベース・スライディングウィンドウ「ソフト」ペナルティ
   （sliding_window_ranges / build_sliding_window_penalty_terms /
     compute_sliding_window_violations / total_sliding_window_violation）

【1. 日付ベース・ローリングウィンドウ「ハード」上限】

【目的】
「任意の連続 window_days 日間において、対象イベントの発生回数 <= cap」という
制約パターン（ローリング/スライディングウィンドウ上限）を、ドメインごとに
LLMへ再生成させず、ここで一度だけ実装してユニットテストで境界条件を固める。

背景: NurseShiftWeeklyCap（7日間ローリング夜勤上限）で、スケジュール期間が
ウィンドウ長より短い場合に制約が丸ごと無効化されるバグが発生した
（docs/ENGINEERING_LOG.md 2026-07-12 不具合2）。この種のロジックはローリング
ウィンドウ制約であればどのドメインでも同じ形で必要になり、同じ形で間違えうる
ため、共通コアへ抽出する。

【層の境界】
- 層A (共通コア): rolling_window_ranges / add_rolling_window_cap_constraints
- 層B/C: ドメイン固有の solver は、presence_of() 済みの CP 式を日番号ごとに
  束ねた dict を組み立てて add_rolling_window_cap_constraints() を呼ぶだけにする。
  ウィンドウの境界処理（期間末尾のクリップ等）は再実装しないこと。

【使い方（例: NurseShiftWeeklyCap）】
    night_cands_by_day: Dict[int, List[Dict]] = {...}  # day -> 夜勤候補のリスト
    presence_by_day = {
        d: [mdl.presence_of(assignment_itvs[nc["id"]]) for nc in cands]
        for d, cands in night_cands_by_day.items()
    }
    add_rolling_window_cap_constraints(
        mdl, presence_by_day, window_days=7, cap=weekly_night_cap,
        all_days=all_days_sorted,
    )

【2. 位置ベース・スライディングウィンドウ「ソフト」ペナルティ】

詳細は当該セクションのコメントを参照（CarSequencing登録時に発覚したKPI集計
バグの再発防止として2026-07-25に追加）。
"""

from __future__ import annotations

from typing import Any, Dict, List, Sequence, Tuple

__all__ = [
    "rolling_window_ranges",
    "add_rolling_window_cap_constraints",
    "sliding_window_ranges",
    "build_sliding_window_penalty_terms",
    "compute_sliding_window_violations",
    "total_sliding_window_violation",
]


def rolling_window_ranges(all_days: Sequence[int], window_days: int) -> List[Tuple[int, int]]:
    """
    既知の期間 all_days （スケジュール対象の全日番号）に対して、上限制約を
    適用すべき (window_start, window_end) の日範囲リストを返す（両端含む）。

    ルール:
    - 各 window_start について、window_end = min(window_start + window_days - 1, 期間の最終日)。
      未来（期間より先）の未知の日は範囲に含めない（クリップする）。
    - クリップにより window_end が期間の最終日に達したら、それ以降の window_start は
      すべてこの範囲の部分集合になり制約として自明に含意されるため、生成を打ち切る。
    - 期間が window_days 日未満の場合でも、既知の全日をカバーする単一の範囲を返す
      （「期間末尾の不完全なウィンドウは緩和してよい」は、未来の未知日を含むウィンドウの
      話であり、既知の期間そのものが短いケースで制約をゼロにする話ではない）。

    docplex に依存しない純粋な日番号計算のみを行う（単体テスト容易化のため）。
    """
    if not all_days:
        return []
    if window_days < 1:
        raise ValueError(f"window_days must be >= 1, got {window_days}")

    max_day = max(all_days)
    ranges: List[Tuple[int, int]] = []
    for window_start in sorted(set(all_days)):
        window_end = min(window_start + window_days - 1, max_day)
        ranges.append((window_start, window_end))
        if window_start + window_days - 1 >= max_day:
            break
    return ranges


def add_rolling_window_cap_constraints(
    mdl: Any,
    presence_by_day: Dict[int, List[Any]],
    window_days: int,
    cap: int,
    all_days: Sequence[int],
) -> int:
    """
    「任意の連続 window_days 日間における presence 合計 <= cap」をハード制約として
    mdl に追加する。presence_by_day は日番号 -> presence_of() 済み CP 式のリスト。

    期間が window_days 日未満の場合も、rolling_window_ranges() により既知の期間
    全体をカバーする範囲が必ず1つ以上生成されるため、制約が完全に無効化されることはない。

    戻り値: 実際に追加した制約の数（対象 presence が1件もないウィンドウはスキップされる）。
    """
    added = 0
    for window_start, window_end in rolling_window_ranges(all_days, window_days):
        window_presence: List[Any] = []
        for d in range(window_start, window_end + 1):
            window_presence.extend(presence_by_day.get(d, []))
        if window_presence:
            mdl.add(mdl.sum(window_presence) <= cap)
            added += 1
    return added


# =============================================================
# 位置ベース・スライディングウィンドウ「ソフト」制約（層A: 共通コア）
# =============================================================
#
# 【目的】
# 「固定長シーケンス（位置0..n-1）上で、任意の連続 window_size 個の窓において
# 対象条件を満たす個数の合計が cap を超えた分をペナルティとして最小化する」
# というパターン（例: CarSequencingの「連続q台中p台まで」オプション制約）を、
# ドメインごとに手書きさせず共通コアへ抽出する。
#
# 背景: CarSequencing登録時、この計算をソルバー内で個別実装したところ、
# mdl.max() を含む合成CP式に対する msol.get_value() が本環境で失敗し、
# 例外を except で握りつぶして常に0を返す不具合が発生した（目的関数値は
# 正しいのに内訳表示だけ0になり、実機検証で発覚。docs/HANDOFF_2026-07-25参照）。
#
# 【設計方針（今回のバグの再発防止そのもの）】
# CPO側（build_sliding_window_penalty_terms）は目的関数の構築にのみ使う。
# 結果の報告・KPI集計には CPO の合成式に対する msol.get_value() を
# 一切使わず、ソルブ後に確定した「各位置の実際の値」を Python 側の純粋関数
# （compute_sliding_window_violations）に渡して再計算する。この2つの定義は
# 完全に一致させてあるため、どちらの経路でも同じ数値になる。
#
# 【層の境界】
# - 層A (共通コア): sliding_window_ranges / build_sliding_window_penalty_terms /
#   compute_sliding_window_violations / total_sliding_window_violation
# - 層B/C: ドメイン固有の solver は、各位置について「対象条件を満たすか」を
#   表すCP式のリスト（目的関数構築用）と、ソルブ後に確定した同じ条件の
#   0/1値のリスト（KPI集計用）を組み立てて、この2つの関数にそれぞれ渡すだけ。
#   窓の範囲計算・超過計算のロジックは再実装しないこと。
#
# 【使い方（例: CarSequencing、1オプションあたり）】
#   # ① モデル構築時（CPO式）
#   count_exprs = [mdl.sum([seq[pos] == j for j in needs_opt_type_indices])
#                  for pos in range(n)]
#   violation_terms = build_sliding_window_penalty_terms(mdl, count_exprs, q, p)
#   objective += w_option * mdl.sum(violation_terms)
#
#   # ② ソルブ後（Python純粋計算、msol.get_value()は使わない）
#   values = [1 if pos_needs_option[pos] else 0 for pos in range(n)]  # 確定済み
#   windows = compute_sliding_window_violations(values, q, p)
#   total = total_sliding_window_violation(values, q, p)


def sliding_window_ranges(n: int, window_size: int) -> List[Tuple[int, int]]:
    """
    長さ n の固定長シーケンス（0-indexed）に対して、サイズ window_size の
    連続ウィンドウの (start, end) 範囲リストを返す（両端含む、0-indexed）。

    range(n - window_size + 1) 個のウィンドウが生成される。
    n < window_size の場合は空リストを返す（窓が1つも取れないため制約なし）。

    docplex に依存しない純粋な位置計算のみを行う（単体テスト容易化のため）。
    """
    if window_size < 1:
        raise ValueError(f"window_size must be >= 1, got {window_size}")
    if n < window_size:
        return []
    return [(start, start + window_size - 1) for start in range(n - window_size + 1)]


def build_sliding_window_penalty_terms(
    mdl: Any,
    count_exprs: Sequence[Any],
    window_size: int,
    cap: int,
) -> List[Any]:
    """
    層A・CPO側。count_exprs（各位置における「対象条件を満たすなら1」等の
    CP式のリスト、長さ n）に対して、各窓の超過分 max(0, 窓内合計 - cap) を
    表すCP式のリストを構築する。目的関数へは
    `mdl.sum(violation_terms) * weight` の形で加算する想定。

    戻り値の要素順序は sliding_window_ranges(len(count_exprs), window_size) と
    完全に対応する（呼び出し側で窓範囲と紐付けたい場合はこの関数を再度呼ぶ）。

    注意: この関数の戻り値（CP式）に対して msol.get_value() で結果を取り出そう
    としないこと（本ファイル冒頭の経緯を参照）。KPI集計・結果報告には
    compute_sliding_window_violations() を使うこと。
    """
    terms: List[Any] = []
    for start, end in sliding_window_ranges(len(count_exprs), window_size):
        window_sum = mdl.sum(count_exprs[start:end + 1])
        terms.append(mdl.max([0, window_sum - cap]))
    return terms


def compute_sliding_window_violations(
    values: Sequence[int],
    window_size: int,
    cap: int,
) -> List[Dict[str, int]]:
    """
    層A・Python側（docplexに依存しない）。ソルブ後に確定した各位置の実際の値
    （0/1フラグ、または個数）のリストを受け取り、各窓の内訳を再計算して返す。

    build_sliding_window_penalty_terms が構築するCPO側の定義
    （各窓内合計がcapを超えた分の合計）と完全に一致させてある。
    KPIの集計・画面表示（窓ごとの内訳）には、CPOの合成式に対する
    msol.get_value() ではなく、必ずこの関数を使うこと。

    戻り値: 各窓の内訳を window_start の昇順で返す。
      [{"window_start": int, "window_end": int, "count": int,
        "cap": int, "excess": int}, ...]
    """
    detail: List[Dict[str, int]] = []
    for start, end in sliding_window_ranges(len(values), window_size):
        count = sum(values[start:end + 1])
        excess = max(0, count - cap)
        detail.append({
            "window_start": start,
            "window_end": end,
            "count": count,
            "cap": cap,
            "excess": excess,
        })
    return detail


def total_sliding_window_violation(
    values: Sequence[int],
    window_size: int,
    cap: int,
) -> int:
    """compute_sliding_window_violations() の excess 合計（= 目的関数の対応項）を返す。"""
    return sum(d["excess"] for d in compute_sliding_window_violations(values, window_size, cap))
