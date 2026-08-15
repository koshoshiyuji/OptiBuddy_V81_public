"""
solution_checker.py — 解チェッカー: 独立な制約充足検証の共通基盤
==================================================

DESIGN_2026-07-21_solution_checker_and_time_cost_tradeoff.md（devnotes）の実装。
2026-07-24、Koshoshiとの相談で以下の決定事項を反映して着手:
  - 対象ドメイン: MeetingRoom / StoreSite / LineChangeoverScheduler（新規）
    + TruckDispatcher（既存_build_issuesのカタログ化）
  - StoreSiteにも docplex.mp の is_valid_solution() を適用する
  - 本番solve時の非同期分岐（3-3節）の枠組みまで実装する
  - 解チェッカーが検出するissueには category="SOLVER" を付与し、
    既存の業務系category（STAFFING/COMPLIANCE/YARD等）と区別する

【独立性の担保（4節）】
このモジュール、および各ドメインの build_*_context 系関数は
solver.py / converter.py の内部ヘルパー（決定変数・目的関数値等）を
importしない。入力は「DSL側で宣言された制約」と「返ってきた具体的な値」
のみとし、ソルバーの内部変数には一切触れない。

【コスト分類（2節）】
  COST_O_N     : 単純比較（O(n)）。常に同期実行。追加コストはほぼゼロ。
  COST_O_NLOGN : ソート/スイープ（O(n log n)）。常に同期実行。
  COST_O_N2    : ペア総当たり（O(n²)）。本番solve時はインスタンス規模の
                 閾値で同期/非同期を分岐する（3-3節）。Gate2登録時
                 （full_check=True）は閾値を無視して常にフル同期実行する
                 （3-1節）。
  COST_O_NM    : 掛け算的（O(n·m)以上、組合せ的爆発系）。本番solve時は
                 常に対象外とし、Gate2側の代表シナリオ検証にのみ委ねる
                 （3-3節）。

【非同期閾値（7節 未決事項1 → 2026-07-24決着）】
  ASYNC_THRESHOLD_O_N2=1000 は実測ベンチマークに基づく確定値
  （n=1000で151ms、CP Optimizer/CPLEX本体のソルブ時間（数秒〜数十秒）に
  比べ無視できる水準と判断。Koshoshiと合意、2026-07-24）。

【category規約（5節）】
  この解チェッカーが検出するissue（DSL宣言のhard制約と返ってきた解の
  矛盾＝コードバグの疑い）には、新規スキーマを追加せず、既存の
  category フィールドの値として "SOLVER" を統一的に使う
  （既存の solve_failed / zero_assignment_anomaly と同じ意味づけ）。
  業務上正常な警告（understaffed等、issue_rules.py の通常ルール）とは
  区別する。
"""

from __future__ import annotations

import logging
from typing import Any, Callable, Dict, List, Optional

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# コスト分類
# ---------------------------------------------------------------------------

COST_O_N     = "O_N"
COST_O_NLOGN = "O_NLOGN"
COST_O_N2    = "O_N2"
COST_O_NM    = "O_NM"

# 非同期閾値（2026-07-24、実測により確定。7節 未決事項1はこれで決着）。
# solvers/tools/check_solution_checker_smoke.py 相当のベンチマークで、
# room_double_bookingパターン（純Python、O(n²)ペア総当たり）の実測時間は
# n=200:4.4ms / n=1000:151ms / n=2000:823ms / n=5000:約7s。CP Optimizer/CPLEX
# 本体のソルブ時間（数秒〜数十秒、config.solve_time_sec等）と比較して、
# 150ms程度の追加コストは体感上無視できる水準と判断し、200から1000に
# 引き上げた（Koshoshiと合意、2026-07-24）。n=2000超で約800ms、n=5000で
# 7秒と急増するため、依然として閾値自体は必要。
# インスタンス規模（チェック対象アイテム数）がこれを超えたら本番solve時は
# 非同期に回す。O_N/O_NLOGNは常に同期、O_NMは本番solve時は常に対象外
# （閾値の概念がそもそも無い）。
ASYNC_THRESHOLD_O_N2 = 1000

# category規約（5節）: 解チェッカーが検出するissueは全てこの値を使う
SOLVER_BUG_CATEGORY = "SOLVER"


def is_deferred_to_async(cost_class: str, instance_size: int, *, full_check: bool = False) -> bool:
    """
    本番solve時にこのチェックを非同期に回すべきかを判定する（3-3節）。

    Args:
        cost_class:    COST_O_N / COST_O_NLOGN / COST_O_N2 / COST_O_NM のいずれか
        instance_size: チェック対象のインスタンス規模（アイテム数）
        full_check:    True の場合 Gate2登録時（3-1節）を意味し、閾値を無視して
                       常に False（＝同期でフル実行）を返す

    Returns:
        True  : 非同期に回すべき（本番solve時のみ。呼び出し側は同期実行せず、
                deferredパッケージを組み立てて後段に委ねる）
        False : 同期実行してよい
    """
    if full_check:
        return False
    if cost_class == COST_O_N2:
        return instance_size > ASYNC_THRESHOLD_O_N2
    if cost_class == COST_O_NM:
        # 組合せ的爆発系は本番solve時の対象外（3-3節）。「非同期にすれば実行できる」
        # という話ではなく、そもそも本番solve時には実行しない方針。Gate2のみで担保する。
        return True
    # COST_O_N / COST_O_NLOGN は常に同期（追加コストはほぼゼロという前提、2節）
    return False


def run_or_defer(
    cost_class: str,
    instance_size: int,
    check_fn: Callable[[], List[Dict[str, Any]]],
    *,
    full_check: bool = False,
    domain: str = "",
    check_id: str = "",
) -> "tuple[List[Dict[str, Any]], Optional[Dict[str, Any]]]":
    """
    コスト分類とインスタンス規模に応じて、チェックを同期実行するか
    非同期に回すかを決定し、実行する（3-3節）。

    Args:
        check_fn: 引数なしのクロージャ。呼び出し側で必要なcontextを
                  キャプチャ済みであること（例: lambda: run_issue_rules(...)）。
        full_check: Gate2登録時はTrueを渡す（閾値を無視して常に同期フル実行）。
        domain / check_id: 非同期に回った場合のジョブ識別・ログ用ラベル。

    Returns:
        (issues, deferred)
          - 同期実行した場合: (check_fn()の結果, None)
          - 非同期に回した場合: ([], deferredパッケージ)
            deferredパッケージは {"domain", "check_id", "cost_class",
            "size", "run"} を持つdict。"run"キーがcheck_fnそのもの
            （呼び出し元がbackground threadで実行する）。
          - COST_O_NM（組合せ的爆発系）で本番solve時（full_check=False）の場合:
            (["Gate2でのみ検証されます"を示す形跡は残さず] [], None)
            ではなく、単に何もチェックせず ([], None) を返す
            （3-3節: 全数チェックは本番solve時の対象外、Gate2のみで担保）。
    """
    if cost_class == COST_O_NM and not full_check:
        # 組合せ的爆発系は本番solve時は非同期化してまで実行する対象ではない
        # （非同期にしても結局重すぎる想定）。何もせず空を返す。
        logger.debug(
            f"[solution_checker] {domain}/{check_id}: COST_O_NM は本番solve時の対象外"
            "（Gate2側の代表シナリオ検証にのみ委ねる）"
        )
        return [], None

    if is_deferred_to_async(cost_class, instance_size, full_check=full_check):
        logger.info(
            f"[solution_checker] {domain}/{check_id}: インスタンス規模{instance_size}が"
            f"閾値を超えたため非同期実行に回します（cost_class={cost_class}）"
        )
        return [], {
            "domain": domain,
            "check_id": check_id,
            "cost_class": cost_class,
            "size": instance_size,
            "run": check_fn,
        }

    return check_fn(), None


def solver_bug_issue(
    id_: str,
    title: str,
    message: str,
    *,
    related_ids: Optional[List[str]] = None,
    extra: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """
    category="SOLVER"（バグ疑い）のissue辞書を統一フォーマットで生成する（5節）。

    解チェッカーが「DSLで宣言されたhard制約と返ってきた解が矛盾している」ことを
    検出した場合に使う。既存のsolve_failed/zero_assignment_anomalyと同じ
    category値を再利用しており、新規スキーマの追加ではない。
    """
    issue = {
        "id": id_,
        "severity": "CRITICAL",
        "category": SOLVER_BUG_CATEGORY,
        "title": title,
        "message": message,
        "relatedContainerIds": related_ids or [],
    }
    if extra:
        issue.update(extra)
    return issue


def sweep_peak_usage(events: "List[tuple[float, float]]") -> "tuple[float, Optional[float]]":
    """
    (時刻, 増減量) のイベント列をスイープし、累積使用量のピーク値と
    そのピークが発生した時刻を返す（O(n log n)、3-2節「ソート/スイープ」分類）。

    資源の同時使用量が容量を超えていないかを検算する用途（例:
    LineChangeoverSchedulerの資源容量チェック）を主眼に置いた汎用ヘルパー。
    solver/converterの内部を一切参照せず、(start, +amount)/(end, -amount)
    という「返ってきた具体的な値」だけから計算する。

    Args:
        events: [(time, delta), ...] のリスト。開始時刻に+amount、
                終了時刻に-amountを入れる想定。

    Returns:
        (peak, peak_time): 累積量の最大値と、それが最初に発生した時刻。
        events が空の場合は (0, None)。

    Note:
        同時刻に終了(-)と開始(+)が重なる場合、終了を先に処理することで
        「ちょうど入れ替わるタイミング」を誤って超過とみなさないようにする
        （実務上、前のタスクが終わった瞬間に次のタスクが資源を使い始める
        ケースを過剰検知しないための配慮）。
    """
    if not events:
        return 0, None
    # 終了(-)を開始(+)より先に処理する: delta昇順（負が先）でソート
    sorted_events = sorted(events, key=lambda e: (e[0], e[1]))
    running = 0.0
    peak = 0.0
    peak_time: Optional[float] = None
    for t, delta in sorted_events:
        running += delta
        if running > peak:
            peak = running
            peak_time = t
    return peak, peak_time
