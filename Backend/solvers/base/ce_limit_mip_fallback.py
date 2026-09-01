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
  2. CE上限例外を検知した場合のみ、mdl.export_as_mps()で完成済みモデルを
     MPS形式（free format）でエクスポートする（この操作はCPLEXエンジンを
     呼ばないため上限の影響を受けない）。
  3. HiGHS（`ortools.linear_solver.python.model_builder`経由、ortools自身が
     内部に静的リンクして持っているHiGHSバックエンド。上限なし）で解く。
     2026-09-01より、単体`highspy`パッケージの使用はやめている
     （後述の【2026-09-01の変更】参照）。
  4. HiGHSの解をdocplex.mp.solution.SolveSolutionとして組み立て直し、mdlに
     結合する。これにより、呼び出し元が元々書いている
     `sol.get_value(some_var)`のような結果抽出コードは一切変更不要で動く。

【2026-09-01の変更】単体`highspy`パッケージからortools内蔵HiGHSへの切替
単体`highspy`パッケージ（独自ビルドのHiGHS）と、ortoolsが内部に静的リンクして
持つHiGHS（CP-SAT等が使う`ortools`本体とは別に、MIP/LPバックエンドの1つとして
同梱）は、別ビルドの同一ライブラリであり、同一プロセス内で両方が読み込まれると
（読み込み順序に応じて）dlopenのシンボル解決エラーや、HiGHSワーカースレッド内での
segfaultを引き起こすことが判明した（PortfolioOverlapDesigner→MysteryShopper
Scheduler連続実行でのsegfault、MysteryShopperScheduler→NursingWorkloadBalance
連続実行でのImportError、両方とも実機で確認）。実機検証（diag_highs_via_ortools.py /
diag_ortools_highs_standalone.py、2026-09-01）により、単体`highspy`を一切importせず
ortools内蔵HiGHSのみを使えば、この衝突が起きないことを確認した。そのため
`_solve_via_highs()`を`highspy`から`ortools.linear_solver.python.model_builder`
（Solver("HIGHS")）へ切り替えた。副次効果として、LP形式のexportで起きていた
ハイフン入り変数名のサニタイズ問題（2026-08-09参照）もMPS(free format)への
切替により解消し、位置ベースの変数値復元という苦肉の策も不要になった
（名前ベースの対応が確実に機能することを実機確認済みのため、正式な主経路にした）。
これに伴い、旧`threads=1`によるsegfault回避策（HiGHSのワーカースレッド経由
コードパスを避ける対症療法だった）も、highspy自体を使わなくなったため削除した。

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

# ortools model_builder.SolveStatus のうち、解を使ってよいと判断するもの
# （2026-09-01、highspy単体からortools内蔵HiGHSへ切替）。
# OPTIMAL（証明済み最適解）に加え、時間制限などで打ち切られても
# 実行可能解（インカンベント）が得られている場合（FEASIBLE）を含める。
# INFEASIBLE/UNBOUNDED/ABNORMAL/NOT_SOLVED/MODEL_INVALID等は含めない。
_HIGHS_USABLE_STATUSES = {
    "OPTIMAL",
    "FEASIBLE",
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
        solution = _solve_via_highs(mdl, time_limit=highs_time_limit)

        # 自己検証（2026-08-31、MysteryShopperSchedulerのsolution checker
        # パイロットで実際に発生）: _solve_via_highs()の変数値復元（位置ベース
        # 優先、列数不一致時のみ名前ベース）は、HiGHSのLP列順序と
        # mdl.iter_variables()の順序が一致している前提に依存しており、この
        # 前提が崩れると値が消えるのではなく「別の変数の値と取り違える」形の
        # サイレントな不具合になる（訪問枠の重複割当のような制約違反が、何の
        # エラーも出さずに返る）。実機で確認した唯一の再現条件は、同一のmdlに
        # 対してsolve_with_ce_fallback()を複数回呼び、呼び出しの間に新しい
        # 変数を追加するケース（MysteryShopperSchedulerのlexicographic
        # 2段階solve）。ここで一度だけis_valid_solution()（決定的・低コスト）
        # により、返す解が実際にmdlの制約を満たしているかを確認する。
        #
        # 検証コードは_solve_via_highs()の呼び出しが完全に終わった後（この
        # 関数のスタックフレーム内）でのみ行う。_solve_via_highs()の内部で
        # SolveSolutionを複数回組み立てて検証する実装を最初に試したところ、
        # CE上限例外を投げた直後のmdl（CPLEXエンジン側が例外直後の状態）に
        # 対してその場でis_valid_solution()を呼ぶとセグメンテーション違反が
        # 発生した（2026-08-31、Koshoshi実機で確認）。ここでの1回限りの
        # 呼び出しは、この不具合を最初に見つけた際の呼び出し方（ドメイン
        # 側のsolve()内で、_solve_via_highs()の呼び出しが終わった後に
        # sol.is_valid_solution()を呼ぶ）と同じタイミング・構造にしてあり、
        # そちらは実機で問題なく動作している。
        if solution is not None and not solution.is_valid_solution(tolerance=1e-6):
            logger.error(
                "[ce_limit_mip_fallback] HiGHSでの解復元が制約充足検証に失敗しました。"
                "HiGHS自体は解を見つけていますが、docplex側への値の復元"
                "（位置ベース/名前ベースの対応付け）に失敗している可能性があるため、"
                "解なしとして扱います。"
            )
            return None

        return solution


def _solve_via_highs(mdl: Any, time_limit: Optional[float] = None) -> Any:
    from ortools.linear_solver.python import model_builder
    from docplex.mp.solution import SolveSolution

    tmp_path = None
    try:
        with tempfile.NamedTemporaryFile(suffix=".mps", delete=False) as f:
            tmp_path = f.name
        # export_as_mps()はCPLEXエンジンを呼ばないため、上限を超えたモデルでも
        # 問題なく完成済みモデルをファイルに書き出せる。MPS(free format)は
        # 変数名に制限がなく、LP形式で起きていたハイフン入り変数名の
        # サニタイズ問題（2026-08-09参照）が起きない（2026-09-01実機確認済み）。
        mdl.export_as_mps(tmp_path)

        omodel = model_builder.Model()
        omodel.import_from_mps_file(tmp_path)

        solver = model_builder.Solver("HIGHS")
        if not solver.solver_is_supported():
            logger.error(
                "[ce_limit_mip_fallback] ortools内蔵HiGHSバックエンド"
                "（model_builder.Solver('HIGHS')）が利用できません。"
            )
            return None
        solver.enable_output(False)
        if time_limit is not None:
            solver.set_time_limit_in_seconds(float(time_limit))

        status = solver.solve(omodel)
        status_name = status.name

        if status_name not in _HIGHS_USABLE_STATUSES:
            logger.warning(
                f"[ce_limit_mip_fallback] HiGHS(ortools内蔵)でも解が得られません"
                f"でした（status={status_name}）。"
            )
            return None

        # 変数値の復元は名前ベースで行う。MPS(free format)は名前をそのまま
        # 保持するため（2026-09-01実機確認済み）、旧実装（LP形式+位置ベース
        # 優先、列数不一致時のみ名前ベース）のような苦肉の策は不要で、
        # 名前ベースの対応を正式な主経路として使える。
        ovar_by_name = {v.name: v for v in omodel.get_variables()}
        mdl_vars = list(mdl.iter_variables())
        var_value_map = {}
        missing_names = []
        for dv in mdl_vars:
            ov = ovar_by_name.get(dv.name)
            if ov is None:
                missing_names.append(dv.name)
                continue
            var_value_map[dv] = solver.value(ov)

        if missing_names:
            logger.warning(
                f"[ce_limit_mip_fallback] {len(missing_names)}個の変数がHiGHS"
                f"(ortools内蔵)側の変数名に見つかりませんでした"
                f"（先頭5件: {missing_names[:5]}）。該当変数は0として扱われます。"
            )

        # solver.objective_value はそのまま使わない。MPS形式には元々
        # 「maximize」を明示する標準的な手段が乏しく、docplexのMPSライターは
        # maximizeモデルの目的関数係数を符号反転して書き出し「読む側は常に
        # minimizeする」前提に依存することがある。ortools側は素直にminimize
        # するため、変数の割り当て自体は（数学的に等価な問題なので）正しく
        # 求まるが、solver.objective_valueは符号反転したまま返ってくる
        # （2026-09-01実機で確認: 最大化500のモデルで-500が返った、変数値は
        # 正しいのに）。
        #
        # objを省略してSolveSolutionに自動計算させる案も試したが、docplexは
        # 自動計算せず「未設定」を表す極端な値（-1e+75）を返すだけだった
        # （同じく2026-09-01実機で確認）。そこで、まずobjなしでSolveSolutionを
        # 作って変数値だけを保持させ、mdl自身の目的関数式(mdl.objective_expr、
        # 正しいmaximize/minimizeの向きを知っている)をsolution.get_value()で
        # 明示的に評価させることで、MPS形式の符号変換を一切経由しない、
        # 確実に正しい目的関数値を得る。
        provisional = SolveSolution(mdl, var_value_map)
        obj_value = provisional.get_value(mdl.objective_expr)
        solution = SolveSolution(mdl, var_value_map, obj=obj_value)
        logger.info(
            f"[ce_limit_mip_fallback] HiGHS(ortools内蔵)での解決完了: "
            f"status={status_name}, "
            f"objective(mdlの目的関数式から再計算)={obj_value}, "
            f"objective(solver生値、参考)={solver.objective_value}"
        )
        return solution
    finally:
        if tmp_path is not None:
            try:
                os.remove(tmp_path)
            except OSError:
                pass
