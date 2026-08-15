"""
ce_limit_mip_fallback.py — 層A: CPLEX(MIP, docplex.mp)向けCE上限フォールバック
===============================================================================

【対象】
docplex.mp（CPLEX MIP、`Model.solve()`）を使うドメイン専用。CP Optimizer
（docplex.cp）ドメインは対象外（solvers/base/ce_limit_lns.pyの
run_sequential_batch/逐次バッチ分割を使うこと）。

【背景】
docplex.mpのモデルは、CPLEXエンジンを実際に呼び出す`.solve()`の時点で
初めてCommunity Edition（評価版）の上限（1000変数/1000制約、CPLEX Error 1016、
`DOcplexLimitsExceeded`）をチェックする。変数・制約の追加自体はPure Python内で
完結するため、上限を超えるモデルでも**構築自体は最後まで完了できる**
（実機で`Model.solve()`のトレースバックを確認済み: 変数を1100個追加した
モデルがexport_as_lp()まで問題なく実行でき、solve()呼び出し時にだけ
DOcplexLimitsExceededが発生する）。

このため、CP Optimizerドメイン（ce_limit_lns.py）のように「入力データを
先に絞り込んでモデルを小さくする」分解方式を使わなくても、**完成済みの
フルモデルをそのままオープンソースのMIPソルバーHiGHSに渡して解ける**。
分解しないため、CPドメインのような「どの軸で分割するか」というドメイン
固有の判断が一切不要（層Bアダプタ不要）。

【手順】
  1. mdl.solve()を試す。
  2. CE上限例外を検知した場合のみ、mdl.export_as_lp()で完成済みモデルを
     CPLEX LP形式でエクスポートする（この操作はCPLEXエンジンを呼ばないため
     上限の影響を受けない）。
  3. HiGHS（`highspy`、CPLEX LP形式をネイティブに読み込める。上限なし）で解く。
  4. HiGHSの解をdocplex.mp.solution.SolveSolutionとして組み立て直し、mdlに
     結合する。これにより、呼び出し元が元々書いている
     `sol.get_value(some_var)`のような結果抽出コードは一切変更不要で動く。

【使い方】
    from solvers.base.ce_limit_mip_fallback import solve_with_ce_fallback

    sol = solve_with_ce_fallback(mdl, log_output=False)
    # 従来通り: sol.get_value(open_var[cj]) など

参照: docs/DESIGN_2026-07-26_generic_ce_limit_fallback.md 2-A節
"""

from __future__ import annotations

import logging
import os
import tempfile
from typing import Any, Optional

from solvers.base.ce_limit_lns import is_ce_limit_exceeded

logger = logging.getLogger(__name__)

__all__ = ["solve_with_ce_fallback"]

# HiGHSのモデルステータス文字列のうち、解を使ってよいと判断するもの。
# "Optimal"（証明済み最適解）に加え、時間制限などで打ち切られても
# 実行可能解（インカンベント）が得られている場合を含める
# （value_validで実在するかどうかは別途確認する）。
_HIGHS_USABLE_STATUSES = {
    "Optimal",
    "Time limit reached",
    "Iteration limit reached",
    "Objective bound",
    "Objective target",
}


def solve_with_ce_fallback(
    mdl: Any,
    highs_time_limit: Optional[float] = None,
    **solve_kwargs: Any,
) -> Any:
    """
    docplex.mp.model.Model用。通常通りmdl.solve()を試み、CPLEXの無料版
    （Community Edition）の上限を検知した場合のみ、HiGHSへのフォールバックに
    切り替える。

    Args:
        mdl:              docplex.mp.model.Model インスタンス（完成済み、
                           mdl.minimize()/mdl.maximize()呼び出し後）。
        highs_time_limit:  HiGHS側の時間制限（秒）。Noneなら制限なし。
        **solve_kwargs:    mdl.solve()にそのまま渡す引数（log_output等）。

    Returns:
        docplex.mp.solution.SolveSolution 相当のオブジェクト、または解なしの
        場合はNone（mdl.solve()がNoneを返す場合と同じ挙動に合わせる）。
    """
    try:
        return mdl.solve(**solve_kwargs)
    except Exception as e:
        if not is_ce_limit_exceeded(e):
            raise
        logger.warning(
            f"[ce_limit_mip_fallback] CPLEXの無料版の上限を検知: {e}。"
            f"HiGHS（オープンソース、上限なし）へフォールバックします。"
        )
        return _solve_via_highs(mdl, time_limit=highs_time_limit)


def _solve_via_highs(mdl: Any, time_limit: Optional[float] = None) -> Any:
    import highspy
    from docplex.mp.solution import SolveSolution

    tmp_path = None
    try:
        with tempfile.NamedTemporaryFile(suffix=".lp", delete=False) as f:
            tmp_path = f.name
        # export_as_lp()はCPLEXエンジンを呼ばないため、上限を超えたモデルでも
        # 問題なく完成済みモデルをファイルに書き出せる。
        mdl.export_as_lp(tmp_path)

        h = highspy.Highs()
        h.silent()
        if time_limit is not None:
            h.setOptionValue("time_limit", float(time_limit))
        h.readModel(tmp_path)
        h.run()

        status = h.modelStatusToString(h.getModelStatus())
        sol_h = h.getSolution()

        if status not in _HIGHS_USABLE_STATUSES or not sol_h.value_valid:
            logger.warning(
                f"[ce_limit_mip_fallback] HiGHSでも解が得られませんでした "
                f"（status={status}）。"
            )
            return None

        # 変数値の復元は名前ベースではなく、export_as_lp()が書き出した列順序と
        # HiGHSが読み込んだ列順序が一致することを前提にした「位置ベース」の対応を
        # 優先する。
        #
        # 背景（2026-08-09、MysteryShopperSchedulerで実際に発生）: CPLEX LP形式は
        # 変数名に使える文字が限定されており（"-"はLP形式の演算子と衝突するため
        # 不可）、docplex側の変数名に日付文字列（例: "x_v1_s1_2026-08-04"）のような
        # ハイフンを含む名前を使うと、export_as_lp()がCPLEXのLPライターを経由する際に
        # 名前を内部的にサニタイズ/別名化することがある。この場合、旧実装の
        # 名前ベース対応（var_by_name[n]）では`h.allVariableNames()`が返す名前と
        # `mdl.iter_variables()`側の元の名前が一致せず、対象変数がvar_value_mapから
        # 丸ごと欠落する。結果、HiGHS自身は正しい最適解（例: objective=22）を
        # 見つけているにもかかわらず、`sol.get_value(var)`が全て0を返すという
        # サイレントな不具合になっていた（値の欠落先はSolveSolutionのデフォルト
        # （未指定変数=0）にフォールバックするため、例外にもならず気づきにくい）。
        #
        # export_as_lp()が書き出す変数の順序は`mdl.iter_variables()`の順序と一致し、
        # HiGHSの`readModel()`もLPファイル中の出現順を列インデックスとして保持する
        # ため、名前が変わっていても位置（インデックス）は保たれる。これを利用し、
        # 列数が一致する場合は位置ベースで対応付ける。列数が一致しない
        # （通常は起こらないはずだが、将来LP変換の挙動が変わった場合の安全網として）
        # 場合のみ、旧来の名前ベース対応にフォールバックする。
        mdl_vars = list(mdl.iter_variables())
        names = h.allVariableNames()
        values = sol_h.col_value

        if len(names) == len(mdl_vars) and len(values) == len(mdl_vars):
            var_value_map = dict(zip(mdl_vars, values))
        else:
            logger.warning(
                f"[ce_limit_mip_fallback] HiGHSの変数列数（{len(names)}）が"
                f"docplexモデルの変数数（{len(mdl_vars)}）と一致しないため、"
                f"位置ベースの解復元ができません。名前ベースの対応にフォールバック"
                f"します（変数名にLP形式で無効な文字を含む場合、値が欠落する"
                f"可能性があります）。"
            )
            var_by_name = {v.name: v for v in mdl_vars}
            var_value_map = {
                var_by_name[n]: val for n, val in zip(names, values) if n in var_by_name
            }

        solution = SolveSolution(mdl, var_value_map, obj=h.getObjectiveValue())
        logger.info(
            f"[ce_limit_mip_fallback] HiGHSでの解決完了: "
            f"status={status}, objective={h.getObjectiveValue()}"
        )
        return solution
    finally:
        if tmp_path is not None:
            try:
                os.remove(tmp_path)
            except OSError:
                pass
