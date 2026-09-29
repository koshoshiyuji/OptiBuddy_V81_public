"""
ce_limit_mip_fallback.py — 層A: CPLEX(MIP, docplex.mp)向けソルバー経路（SOLVER_BACKEND／CE上限フォールバック）
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

【手順】（2026-09-01時点の旧実装の記録。現行は下記【2026-09-29の変更】を参照）
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

【2026-09-01の変更】（旧実装の記録）単体`highspy`パッケージからortools内蔵HiGHSへの切替
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

【2026-09-29の変更】SOLVER_BACKEND対応、フォールバック先をSCIPへ、MPS経由を廃止
DESIGN_2026-09-28_v2_paid_license_and_repo_split.md 8節（V1修正）。

1. SOLVER_BACKEND（engine_select.get_solver_backend()）で経路を分ける。
   - oss  : mdl.solve()を呼ばず、最初からOSSのMIPソルバー（SCIP）で解く。
            CPLEX本体（cplexパッケージ）が無い環境でもMIP系ドメインが解ける。
   - cplex: 従来通りmdl.solve()。CE上限を超えたときだけSCIPへフォールバック。
            CPLEX本体が無い（"no CPLEX runtime found"）場合はSolverEngineUnavailableError
            を投げ、CPLEXの導入かSOLVER_BACKEND=ossへの変更を案内する（以前はこの
            例外がCE上限判定に一致せず、ドメイン側で「ソルバー内部エラー」扱いになっていた）。
2. フォールバック先をHiGHSからSCIP（どちらもortools同梱）に変えた。ortools 9.15の
   HiGHSラッパー（model_builder / pywraplp とも）は、時間制限に達すると暫定解が
   あってもUNKNOWN_STATUSを返し値を取得できない（2026-09-29クラウドで実測: 30制約×
   300変数の多次元ナップサック、3秒制限。highspy単体ではgap0.6%の解が得られていた）。
   SCIPは同条件でFEASIBLEと暫定解を返す。時間制限を渡すと「偽の解なし」になるHiGHSは使わない。
3. mdl.export_as_mps()をやめ、docplexのモデルオブジェクトから直接ortoolsモデルを組み立てる。
   export_as_mps()は "Exporting to MPS requires CPLEX" でCPLEX本体が無いと失敗する
   （2026-09-29実測）ため、旧実装のフォールバックはCPLEX無し環境では動かなかった。
   LP形式（export_as_lp_string、純Python）もortoolsのLPパーサーが読めなかった。
   直接変換では変数オブジェクトどうしで値を対応付けるため、名前・列順に依存した
   値の取り違え（2026-08-31 MysteryShopperSchedulerの2段階solve）が原理的に起きない。
   対応する要素: 連続・整数・0-1変数、線形制約、範囲制約、線形目的関数。それ以外
   （インジケータ制約、二次、PWL、論理制約など）を含むモデルはOssMipUnsupportedModelError
   （「このモデルはCPLEXが必要」）を投げ、解なし扱いにはしない。
4. 時間制限: highs_time_limit引数 → mdl.solve()に渡すtime_limit引数 →
   mdl.parameters.timelimit（docplex既定の1e75は無制限扱い）の順で決める。
   従来は8ドメインとも時間制限がOSS側に渡っていなかった。
5. 返す解にsolve_details（時間・状態）とsolve_statusを付ける（従来はNone）。

【使い方】
    from solvers.base.ce_limit_mip_fallback import solve_with_ce_fallback

    sol = solve_with_ce_fallback(mdl, log_output=False)
    # 従来通り: sol.get_value(open_var[cj]) など

参照: docs/DESIGN_2026-07-26_generic_ce_limit_fallback.md 2-A節
"""

from __future__ import annotations

import logging
import math
import time
from typing import Any, Dict, Optional

from solvers.base.ce_limit_lns import is_ce_limit_exceeded
from solvers.base.engine_select import BACKEND_OSS, get_solver_backend

logger = logging.getLogger(__name__)

__all__ = [
    "solve_with_ce_fallback",
    "SolverEngineUnavailableError",
    "OssMipUnsupportedModelError",
]

# OSS側のMIPソルバー（ortools model_builder経由、ortools同梱）。
_OSS_MIP_SOLVER = "SCIP"

# ortools model_builder.SolveStatus のうち、解を使ってよいもの。
# FEASIBLE = 時間制限などで打ち切られたが暫定解（インカンベント）がある。
_OSS_USABLE_STATUSES = {"OPTIMAL", "FEASIBLE"}

# docplexの timelimit 既定値（1e+75）など、実質無制限とみなす閾値。
_UNLIMITED_TIME = 1e20
# docplexの変数上下限の「無限大」（±1e+20）
_INF_BOUND = 1e20


class SolverEngineUnavailableError(RuntimeError):
    """
    SOLVER_BACKENDで指定されたエンジンがこの環境で使えない場合の例外。
    solver_error_result.build_solver_crash_issue() が「プログラムの不具合」ではなく
    「エンジン設定の問題」として表示する。
    """


class OssMipUnsupportedModelError(SolverEngineUnavailableError):
    """OSSのMIPソルバーへ変換できない要素（インジケータ制約等）をモデルが含む場合。"""


def _is_no_cplex_runtime(exc: BaseException) -> bool:
    return "no cplex runtime" in str(exc).lower()


def _resolve_time_limit(mdl: Any, explicit: Optional[float], solve_kwargs: Dict[str, Any]) -> Optional[float]:
    candidates = [explicit, solve_kwargs.get("time_limit")]
    try:
        candidates.append(mdl.parameters.timelimit.get())
    except Exception:
        pass
    for v in candidates:
        if v is None:
            continue
        try:
            v = float(v)
        except (TypeError, ValueError):
            continue
        if 0 < v < _UNLIMITED_TIME:
            return v
    return None


def solve_with_ce_fallback(
    mdl: Any,
    highs_time_limit: Optional[float] = None,
    **solve_kwargs: Any,
) -> Any:
    """
    docplex.mp.model.Model用。SOLVER_BACKENDに応じて解く。
      - oss  : OSSのMIPソルバー（SCIP）で直接解く。
      - cplex: mdl.solve()。CE上限を検知した場合のみSCIPへフォールバック。

    Args:
        mdl:              docplex.mp.model.Model インスタンス（完成済み、
                           mdl.minimize()/mdl.maximize()呼び出し後）。
        highs_time_limit:  OSSソルバー側の時間制限（秒）。名前は互換のため据え置き
                           （2026-09-29以降の実体はSCIP）。Noneなら time_limit 引数、
                           mdl.parameters.timelimit の順に使う。
        **solve_kwargs:    mdl.solve()にそのまま渡す引数（log_output等）。

    Returns:
        docplex.mp.solution.SolveSolution 相当のオブジェクト、または解なしの
        場合はNone（mdl.solve()がNoneを返す場合と同じ挙動に合わせる）。

    Raises:
        SolverEngineUnavailableError: SOLVER_BACKEND=cplex だがCPLEX本体が無い。
        OssMipUnsupportedModelError:  OSSソルバーへ変換できない要素をモデルが含む。
    """
    time_limit = _resolve_time_limit(mdl, highs_time_limit, solve_kwargs)

    if get_solver_backend() == BACKEND_OSS:
        return _solve_via_oss_checked(mdl, time_limit=time_limit)

    try:
        return mdl.solve(**solve_kwargs)
    except Exception as e:
        if _is_no_cplex_runtime(e):
            raise SolverEngineUnavailableError(
                "SOLVER_BACKEND=cplex が指定されていますが、CPLEX本体（cplexパッケージ）が"
                "見つかりません。`pip install -r requirements-cplex.txt` でCPLEXを導入するか、"
                ".env の SOLVER_BACKEND を oss に変更してください。"
            ) from e
        if not is_ce_limit_exceeded(e):
            raise
        logger.warning(
            f"[ce_limit_mip_fallback] CPLEXの無料版の上限を検知: {e}。"
            f"OSSのMIPソルバー（{_OSS_MIP_SOLVER}、上限なし）へフォールバックします。"
        )
        return _solve_via_oss_checked(mdl, time_limit=time_limit)


def _solve_via_oss_checked(mdl: Any, time_limit: Optional[float]) -> Any:
    """
    _solve_via_oss()の呼び出しが完全に終わった後、このスタックフレームで一度だけ
    is_valid_solution()による自己検証を行う（2026-08-31導入の安全網を維持）。
    _solve_via_oss()の内部で検証しないのは、CE上限例外の直後のmdlに対して内部で
    is_valid_solution()を呼ぶとsegfaultした実績（2026-08-31、Koshoshi実機）があり、
    問題なく動いていた旧実装と同じ呼び出し位置・構造に合わせるため。
    変数オブジェクトで値を対応付けるため取り違えは起きないはずだが、検証に失敗
    した場合は誤った解を返さず解なしとして扱う。
    """
    solution = _solve_via_oss(mdl, time_limit=time_limit)
    if solution is not None and not solution.is_valid_solution(tolerance=1e-6):
        logger.error(
            f"[ce_limit_mip_fallback] {_OSS_MIP_SOLVER}の解がdocplex側の制約充足検証に"
            "失敗しました。誤った解を返さないため解なしとして扱います。"
        )
        return None
    return solution


def _linear_parts(expr: Any):
    """docplexの式を (項のリスト[(var, coef)], 定数) に分解する。線形でなければ例外。"""
    if hasattr(expr, "to_linear_expr"):
        expr = expr.to_linear_expr()
    if not hasattr(expr, "iter_terms"):
        raise OssMipUnsupportedModelError(
            f"線形式に変換できない式を含んでいます（{type(expr).__name__}: {expr}）。"
            "このモデルはCPLEXが必要です（SOLVER_BACKEND=cplex）。"
        )
    return list(expr.iter_terms()), float(expr.get_constant())


def _build_oss_model(mdl: Any):
    """docplexのモデルから ortools model_builder.Model を直接組み立てる。"""
    from ortools.linear_solver.python import model_builder
    from docplex.mp.constr import LinearConstraint, RangeConstraint

    def _unsupported(what: str) -> OssMipUnsupportedModelError:
        return OssMipUnsupportedModelError(
            f"このモデルはOSSのMIPソルバー（{_OSS_MIP_SOLVER}）では扱えない{what}を含んでいます。"
            "CPLEXが必要です（requirements-cplex.txt を導入し SOLVER_BACKEND=cplex）。"
        )

    omodel = model_builder.Model()
    ovars: Dict[Any, Any] = {}
    for dv in mdl.iter_variables():
        kind = dv.vartype.short_name
        lb = dv.lb if dv.lb > -_INF_BOUND else -math.inf
        ub = dv.ub if dv.ub < _INF_BOUND else math.inf
        if kind == "binary":
            ovars[dv] = omodel.new_bool_var(dv.name)
        elif kind == "integer":
            ovars[dv] = omodel.new_int_var(lb, ub, dv.name)
        elif kind == "continuous":
            ovars[dv] = omodel.new_num_var(lb, ub, dv.name)
        else:
            raise _unsupported(f"変数型（{kind}: {dv.name}）")

    def _expr(terms, const):
        e = const
        for v, coef in terms:
            e = e + float(coef) * ovars[v]
        return e

    for ct in mdl.iter_constraints():
        if isinstance(ct, RangeConstraint):
            terms, const = _linear_parts(ct.expr)
            e = _expr(terms, const)
            omodel.add(e >= float(ct.lb))
            omodel.add(e <= float(ct.ub))
        elif isinstance(ct, LinearConstraint):
            lt, lc = _linear_parts(ct.left_expr)
            rt, rc = _linear_parts(ct.right_expr)
            e = _expr(lt, lc) - _expr(rt, rc)
            sense = ct.sense.name
            if sense == "LE":
                omodel.add(e <= 0)
            elif sense == "GE":
                omodel.add(e >= 0)
            elif sense == "EQ":
                omodel.add(e == 0)
            else:
                raise _unsupported(f"比較演算（{sense}: {ct}）")
        else:
            raise _unsupported(f"制約（{type(ct).__name__}: {ct}）")

    # iter_constraints()に現れない種類（SOS、PWL、二次制約など）の検出
    for attr in ("number_of_sos", "number_of_pwl_constraints", "number_of_quadratic_constraints"):
        n = getattr(mdl, attr, 0) or 0
        if n:
            raise _unsupported(f"要素（{attr}={n}）")

    ot, oc = _linear_parts(mdl.objective_expr)
    obj = _expr(ot, oc)
    if mdl.is_maximized():
        omodel.maximize(obj)
    else:
        omodel.minimize(obj)
    return omodel, ovars


def _solve_via_oss(mdl: Any, time_limit: Optional[float] = None) -> Any:
    from ortools.linear_solver.python import model_builder
    from docplex.mp.solution import SolveSolution
    from docplex.mp.sdetails import SolveDetails
    from docplex.util.status import JobSolveStatus

    omodel, ovars = _build_oss_model(mdl)

    solver = model_builder.Solver(_OSS_MIP_SOLVER)
    if not solver.solver_is_supported():
        raise SolverEngineUnavailableError(
            f"ortools同梱の{_OSS_MIP_SOLVER}（model_builder.Solver('{_OSS_MIP_SOLVER}')）が利用できません。"
            "ortoolsのインストールを確認してください。"
        )
    solver.enable_output(False)
    if time_limit is not None:
        solver.set_time_limit_in_seconds(float(time_limit))

    t0 = time.perf_counter()
    status = solver.solve(omodel)
    elapsed = time.perf_counter() - t0
    status_name = status.name

    if status_name not in _OSS_USABLE_STATUSES:
        logger.warning(
            f"[ce_limit_mip_fallback] {_OSS_MIP_SOLVER}で解が得られませんでした"
            f"（status={status_name}, time_limit={time_limit}）。"
        )
        return None

    # 変数オブジェクトどうしで対応付ける（名前・列順に依存しない）。
    # 整数・0-1変数はソルバーの許容誤差内の値（0.9999999等）を丸める。
    var_value_map = {}
    for dv, ov in ovars.items():
        val = float(solver.value(ov))
        if dv.vartype.short_name in ("binary", "integer"):
            r = round(val)
            if abs(val - r) <= 1e-6:
                val = float(r)
        var_value_map[dv] = val

    # 目的関数値はmdl自身の目的関数式から計算する（maximize/minimizeの向きを
    # mdlが正しく知っているため、ソルバー側の符号の扱いに依存しない）。
    obj_value = SolveSolution(mdl, var_value_map).get_value(mdl.objective_expr)
    job_status = (JobSolveStatus.OPTIMAL_SOLUTION if status_name == "OPTIMAL"
                  else JobSolveStatus.FEASIBLE_SOLUTION)
    details = SolveDetails(time=elapsed, status_string=status_name.lower(),
                           problem_type=f"MILP({_OSS_MIP_SOLVER})")
    solution = SolveSolution.make_engine_solution(
        mdl, var_value_map, obj_value, None, _OSS_MIP_SOLVER, details, job_status,
    )

    logger.info(
        f"[ce_limit_mip_fallback] {_OSS_MIP_SOLVER}で解決: status={status_name}, "
        f"objective={obj_value}, time={elapsed:.2f}s, time_limit={time_limit}"
    )
    return solution
