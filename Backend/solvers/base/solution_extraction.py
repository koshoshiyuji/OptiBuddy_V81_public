"""
solution_extraction.py — 層A: ソルバー解抽出の共通コア（目的値/makespan）
=============================================================================

【背景】
`docs/ENGINEERING_LOG.md`（2026-07-15、LineChangeoverScheduler登録）で、
名前なしのCpoExpr（例: `makespan_expr = mdl.max([mdl.end_of(iv) for iv in ...])`）
に対して直接 `msol.get_value(makespan_expr)` を呼び出す実装が、docplexの仕様上
「Variable or KPI '...' not in the solution」で失敗する不具合が発生した。
1回目の修正（`mdl.add(mdl.minimize(makespan_expr))` で目的関数として登録する）は
真因を捉えきれておらず、同一のKeyErrorが再現した。

動作実績のあるTruckDispatcher/NurseShiftWeeklyCapは、いずれも `get_value()` を
この用途で使わず、`msol.get_var_solution()` で名前付き変数（interval_var）から
既に抽出済みのスケジュールに対して、Python側で `max()` 等を使い手計算していた。
LineChangeoverSchedulerも2026-07-15の2回目の修正でこの方式に揃えて解消した。

このファイルは、この「正しいパターン」を共通ユーティリティとして切り出したもの。
新規ドメインは最初からこれを使うことで、同じ不具合クラスを再発させない。

【層の境界】
- 層A (共通コア): このファイル全体。
- 層B/C: 各ドメインのsolver.pyは、`msol.get_var_solution()` で抽出済みの
  schedule（List[Dict]、各要素が "end" 等の時刻キーを持つ）を組み立てて
  `extract_makespan()` を呼ぶだけ。目的値の取得も `safe_objective_value()`
  を経由し、名前なし式に対する `get_value()` を直接呼ばないこと。

【使い方（例: LineChangeoverScheduler / TruckDispatcher系ドメイン）】
    schedule = []
    for t in tasks:
        itv = task_itvs.get(str(t["id"]))
        var_sol = msol.get_var_solution(itv)
        if var_sol is None or not var_sol.is_present():
            continue
        schedule.append({"task_id": str(t["id"]), "start": var_sol.get_start(),
                          "end": var_sol.get_end()})

    makespan_val = extract_makespan(schedule)
    obj_val = safe_objective_value(msol, fallback=float(makespan_val))
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional

__all__ = ["extract_makespan", "safe_objective_value", "extract_optimality_metadata"]


def extract_makespan(schedule: List[Dict[str, Any]], end_key: str = "end") -> int:
    """
    既に get_var_solution() で抽出済みのスケジュール（辞書のリスト）から、
    Python側でmakespan（終了時刻の最大値）を計算する。

    docplexの名前なしCpoExprに対する get_value() は使わない
    （「Variable or KPI '...' not in the solution」で失敗するため）。

    Args:
        schedule: 各要素が少なくとも end_key を持つ辞書のリスト
                  （例: [{"task_id": "t1", "start": 0, "end": 30}, ...]）。
        end_key:  終了時刻を表すキー名（既定 "end"）。

    Returns:
        int: schedule が空の場合は 0。
    """
    return int(max((s.get(end_key, 0) for s in schedule), default=0))


def safe_objective_value(msol: Any, fallback: float) -> float:
    """
    ソルバーの目的値を安全に取得する。

    `msol.get_objective_value()` はトップレベルの目的関数値を返す正規のAPIで
    あり、`get_value(名前なし式)` とは異なり本来安全に呼べるが、目的関数が
    複数（lexicographic等）の場合や一部のモデル構成では例外や None を返す
    ケースがあるため、フォールバック値（通常は extract_makespan() 等で
    Python側から計算した値）を必須の第二引数として受け取る。

    Args:
        msol:     ソルブ結果オブジェクト（CpoSolveResult）。
        fallback: 取得失敗時に使う値（例: extract_makespan() の戻り値）。

    Returns:
        float
    """
    try:
        val = msol.get_objective_value()
        return float(val) if val is not None else float(fallback)
    except Exception:
        return float(fallback)


# ---------------------------------------------------------------------------
# optimality metadata（2026-07-22追加）
# ---------------------------------------------------------------------------
#
# 背景: 既存のsolver出力は feasible/infeasible の2値判定のみで、
# 「TimeLimitで打ち切られた実行可能解（最適性は未証明）」と「最適性が証明
# された解」を区別する手段が無かった。運用上、TimeLimitで打ち切られた解を
# そうと分からないまま業務ユーザーに提示すると、実際にはもっと良い解が
# 存在するかもしれないことが伝わらない。CP Optimizerは
# msol.get_solve_status()（"Optimal"/"Feasible"/"Infeasible"/"Unknown"/
# "JobAborted"/"JobFailed"）でこれを区別する情報を既に持っており、
# 本関数はそれを安全に取り出すだけの薄いラッパーである。
#
# 【使い方】
#   msol = mdl.solve(TimeLimit=solve_time_sec, ...)
#   optimality = extract_optimality_metadata(msol)
#   solution["optimality"] = optimality
#   # optimality["is_optimal"] が False かつ optimality["solve_status"] が
#   # "Feasible" の場合、TimeLimitで打ち切られた未証明解である可能性が高い
#   # （業務ユーザー向けには「制限時間内で見つかった解であり、最適性は
#   # 保証されていません」等の注記に使える）。
#
# 【新規/既存ソルバーへの適用方針】
#   layer A（共通コア）として追加するが、既存の全ソルバーへの一括retrofitは
#   本変更のスコープに含めない（table_sections.py/extract_makespan等と同様、
#   このリポジトリでは新規共通ユーティリティは「使うドメインから順に適用」する
#   方針を踏襲する）。reference実装として meeting_room_solver.py に適用済み
#   （solvers/test_meeting_room_solver_metadata.py 参照）。他ドメインへの展開は
#   個別に対応すること。

def extract_optimality_metadata(msol: Any) -> Dict[str, Any]:
    """
    ソルブ結果から「解の最適性の証明状態」を抽出する。

    Args:
        msol: CP Optimizerのソルブ結果オブジェクト（CpoSolveResult）。
              None、またはget_solve_status()等のAPIを持たないオブジェクト
              （docplex.mp等、CP以外のsolve結果）でも例外を投げず、
              取得できたフィールドだけを埋めて返す。
              CpoSolveResultは infeasible/aborted 等でも None にはならず
              bool(msol) が False になるだけのことがあるため、
              呼び出し側で `if msol:` のような真偽判定でこの関数の呼び出し
              自体をスキップしないこと（Infeasible/Abortedのステータス自体が
              有用な情報のため）。None のみガードすればよい。

    Returns:
        {
          "solve_status":   str | None,  # "Optimal" | "Feasible" | "Infeasible" |
                                          # "Unknown" | "JobAborted" | "JobFailed" |
                                          # None（取得不可時）
          "is_optimal":     bool,        # solve_status == "Optimal" と同値
          "solve_time_sec": float | None,
        }
    """
    result: Dict[str, Any] = {"solve_status": None, "is_optimal": False, "solve_time_sec": None}
    if msol is None:
        return result

    try:
        status = msol.get_solve_status()
        result["solve_status"] = status if status is not None else None
    except Exception:
        pass

    try:
        result["is_optimal"] = bool(msol.is_solution_optimal())
    except Exception:
        pass

    try:
        t = msol.get_solve_time()
        result["solve_time_sec"] = float(t) if t is not None else None
    except Exception:
        pass

    return result
