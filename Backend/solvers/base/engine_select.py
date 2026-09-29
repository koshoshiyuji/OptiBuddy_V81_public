"""
engine_select.py — 層A: CP側ソルバーエンジンの明示選択

【対象】
docplex.cp（CPLEX CP Optimizer, CPO）を使うドメイン専用。config.solver_engine
で明示的に "cpsat"（Google OR-Tools CP-SAT, CPMpy経由）を選べるようにする。

【命名の注意】
solvers/registry.py の `metadata.solver_hint` とは別物・別の軸。
  - registry.py の solver_hint : 「どのドメインソルバー（*_solver.pyファイル）
    を使うか」を選ぶ（例: "cplex_dynamic" vs 個別ドメインソルバー）。
  - 本モジュールの config.solver_engine : 「同一ドメイン内でCPOとCP-SATの
    どちらのエンジンで解くか」を選ぶ。1つのドメインソルバーファイルの中で
    分岐する。
両者は独立しており、混同しないこと。

【背景】
docs/DESIGN_2026-08-12_cp_sat_backend_support.md 追記2「CPMpyパイロット結果」
（car_sequencing/meeting_room）、および2026-08-13追記（line_changeover_scheduler/
nurse_shift_weekly_cap系の真のフレキシブルスケジューリング検証）。

MIP側（docplex.mp）の ce_limit_mip_fallback.py は、CPLEX LP形式という標準的な
中間ファイル形式があるため、完成済みモデルをそのままHiGHSに渡して解ける
「透過的な自動フォールバック」が可能だった。CP側（docplex.cp）にはこれに
相当する相互運用フォーマットが無く、CpoModelとCP-SAT(CPMpy)の間でモデルを
自動変換する手段が存在しない。そのため各ドメインごとに個別の変換層（層B）を
書く必要があり、かつCP-SATモデルが本番のCPOモデルと完全に同じ制約セットを
網羅できているとは限らない（例: NurseShiftWeeklyCapは2026-08-13時点でCHIEF数
下限・日次労働時間上限・週次夜勤ローリング上限がCP-SAT側未実装）。

この状況でCE上限検知時の「無言の自動切替」を行うと、CPOなら効いていたはずの
制約がCP-SAT側では抜け落ちたまま「解けた」ことになるサイレントな誤答リスクが
ある。Koshoshiとの相談により、各ドメインのCP-SATモデルが本番同等の機能網羅性
を持つと確認できるまでは、自動フォールバックを行わず明示選択のみをサポートする
方針とした（2026-08-13）。

【デプロイ時デフォルトの決定方針（2026-08-13 追記）】
CPLEX CP Optimizerは高性能だが、正規ライセンスがないと動かず、CE（コミュニティ
エディション）はモデルサイズに上限がある。一方でCPLEX CEを積極的に使いたい
場面も多い。そこでKoshoshiとの相談により、以下の3層優先順位を採用する
（詳細: docs/DESIGN_2026-08-12_cp_sat_backend_support.md）:

    1. config.solver_engine（最優先、ドメイン単位・リクエスト単位の明示指定）
    2. 環境変数 DEFAULT_SOLVER_ENGINE（.env、デプロイ環境単位の既定値）
    3. ハードコードされたフォールバック（_HARDCODED_DEFAULT_ENGINE = "cpsat"）

デプロイ時（リリース）のハードコード既定値は "cpsat"
（CPLEXが無い環境でも必ず動くことを優先）。CPLEX CE/正規ライセンスを使いたい
環境では .env に DEFAULT_SOLVER_ENGINE=cpo を設定する。CE上限を超えた場合は
（Yard/NurseShiftWeeklyCap/TruckDispatcherの3特殊ドメインを除き）自動フォール
バックはせず、必要であれば config.solver_engine="cpsat" を明示指定してもらう
運用とする（自動フォールバックと実質的な挙動は近いが、CPOなら効いていたはずの
制約が抜け落ちる既知の機能ギャップがあるドメインも将来的に増え得るため、
サイレントな切り替えは避ける）。

【2026-09-29追記: SOLVER_BACKEND への統一】
環境変数を SOLVER_BACKEND=cplex|oss の1つに統一した（DESIGN_2026-09-28_v2_paid_license_and_repo_split.md 8節）。
    cplex : CP=CPO（docplex.cp）、MIP=CPLEX（docplex.mp）
    oss   : CP=CP-SAT（CPMpy/OR-Tools）、MIP=SCIP（OR-Tools同梱、ce_limit_mip_fallback.py）
既定は oss。旧 DEFAULT_SOLVER_ENGINE は SOLVER_BACKEND が未設定のときだけ互換として読む
（cpo→cplex、cpsat→oss）。両方が設定されていて意味が食い違う場合は SOLVER_BACKEND を優先し、
警告ログを出す（Koshoshi決定、2026-09-29）。ドメイン単位の config.solver_engine（CP）は
これまで通り最優先。

【使い方】
    from solvers.base.engine_select import get_solver_engine, CPO, CPSAT

    engine = get_solver_engine(config)
    if engine == CPO:
        ...
    elif engine == CPSAT:
        ...
"""

from __future__ import annotations

import logging
import os
from typing import Any, Dict

try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass

logger = logging.getLogger(__name__)

__all__ = [
    "CPO", "CPSAT", "BACKEND_CPLEX", "BACKEND_OSS",
    "get_solver_backend", "get_solver_engine", "cpmpy_optimality_metadata",
]

CPO = "cpo"
CPSAT = "cpsat"
_VALID_ENGINES = {CPO, CPSAT}

# デプロイ時のハードコードされた最終フォールバック値。
# .env の DEFAULT_SOLVER_ENGINE も未設定の場合にここへ落ちる。
# 2026-08-13: CPLEXが無い環境でも常に動くことを優先し "cpsat" とする
# （従来は "cpo" だったが、Koshoshiとの相談によりデプロイ時デフォルトを変更）。
_HARDCODED_DEFAULT_ENGINE = CPSAT

BACKEND_CPLEX = "cplex"
BACKEND_OSS = "oss"
_VALID_BACKENDS = {BACKEND_CPLEX, BACKEND_OSS}
_HARDCODED_DEFAULT_BACKEND = BACKEND_OSS

# 旧 DEFAULT_SOLVER_ENGINE（CP側のみ）→ SOLVER_BACKEND の互換対応
_LEGACY_ENGINE_TO_BACKEND = {CPO: BACKEND_CPLEX, CPSAT: BACKEND_OSS}
_BACKEND_TO_CP_ENGINE = {BACKEND_CPLEX: CPO, BACKEND_OSS: CPSAT}

# 食い違い警告を1プロセスで1回だけ出すためのフラグ
_conflict_warned = False


def get_solver_backend() -> str:
    """
    デプロイ環境単位のソルバーバックエンド（"cplex" / "oss"）を返す。
        1. 環境変数 SOLVER_BACKEND
        2. 環境変数 DEFAULT_SOLVER_ENGINE（旧設定の互換読み: cpo→cplex、cpsat→oss）
        3. _HARDCODED_DEFAULT_BACKEND（"oss"）
    未知の値は例外（サイレントに既定値へ落とさない。get_solver_engine と同じ方針）。
    """
    global _conflict_warned
    backend_env = os.environ.get("SOLVER_BACKEND")
    legacy_env = os.environ.get("DEFAULT_SOLVER_ENGINE")

    legacy_backend = None
    if legacy_env:
        if legacy_env not in _LEGACY_ENGINE_TO_BACKEND:
            if not backend_env:
                raise ValueError(
                    f"[engine_select] 未知の DEFAULT_SOLVER_ENGINE='{legacy_env}' が指定されました。"
                    f"有効な値: {sorted(_LEGACY_ENGINE_TO_BACKEND)}（新しい設定は SOLVER_BACKEND=cplex|oss）"
                )
        else:
            legacy_backend = _LEGACY_ENGINE_TO_BACKEND[legacy_env]

    if backend_env:
        if backend_env not in _VALID_BACKENDS:
            raise ValueError(
                f"[engine_select] 未知の SOLVER_BACKEND='{backend_env}' が指定されました。"
                f"有効な値: {sorted(_VALID_BACKENDS)}"
            )
        if legacy_env and legacy_backend != backend_env and not _conflict_warned:
            logger.warning(
                f"[engine_select] SOLVER_BACKEND={backend_env} と DEFAULT_SOLVER_ENGINE={legacy_env} が"
                f"食い違っています。SOLVER_BACKEND を優先し、DEFAULT_SOLVER_ENGINE は無視します"
                f"（.env から DEFAULT_SOLVER_ENGINE の行を削除してください）。"
            )
            _conflict_warned = True
        return backend_env

    if legacy_backend is not None:
        return legacy_backend
    return _HARDCODED_DEFAULT_BACKEND


def get_solver_engine(config: Dict[str, Any]) -> str:
    """
    次の優先順位で solver_engine を決定する:
        1. config.solver_engine（明示指定、最優先）
        2. get_solver_backend()（SOLVER_BACKEND、旧DEFAULT_SOLVER_ENGINEは互換読み）
           が cplex なら "cpo"、oss なら "cpsat"
        3. どちらも未設定なら oss → "cpsat"

    未知の値が指定された場合はサイレントにデフォルトへフォールバックせず、
    明示的に例外を投げる（solvers/registry.py V7.1のsolver_hint未検出時の
    方針と同じ考え方: サイレントバグの防止を優先する）。この方針は
    config指定・環境変数指定のどちらが未知値だった場合も同様に適用する。
    """
    config = config or {}
    if "solver_engine" in config and config["solver_engine"] is not None:
        engine = config["solver_engine"]
        source = "config.solver_engine"
    else:
        # 2026-09-29: 環境変数は get_solver_backend() に一本化（SOLVER_BACKEND、
        # 旧 DEFAULT_SOLVER_ENGINE は互換読み）。未設定時は "oss" → CP-SAT で、
        # 従来のハードコード既定（_HARDCODED_DEFAULT_ENGINE = cpsat）と同じ。
        backend = get_solver_backend()
        engine = _BACKEND_TO_CP_ENGINE[backend]
        source = f"SOLVER_BACKEND={backend}"

    if engine not in _VALID_ENGINES:
        raise ValueError(
            f"[engine_select] 未知の solver_engine='{engine}' が{source}経由で指定されました。"
            f"有効な値: {sorted(_VALID_ENGINES)}"
        )
    return engine


# ExitStatus(CPMpy) -> solve_status(docplex.cp互換の語彙)。
# solvers/base/solution_extraction.py の extract_optimality_metadata() が返す
# solve_status ("Optimal"/"Feasible"/"Infeasible"/"Unknown"/"JobAborted"/
# "JobFailed") と同じ語彙に揃え、下流コード（issue検出・UI表示）が
# エンジンに依らず同じ分岐で扱えるようにする。
_EXITSTATUS_TO_SOLVE_STATUS = {
    "OPTIMAL":       "Optimal",
    "FEASIBLE":      "Feasible",
    "UNSATISFIABLE": "Infeasible",
    "UNKNOWN":       "Unknown",
    "NOT_RUN":       "Unknown",
    "ERROR":         "JobFailed",
}


def cpmpy_optimality_metadata(model: Any) -> Dict[str, Any]:
    """
    CPMpyの cp.Model.solve() 実行後、model.status() から
    solvers/base/solution_extraction.extract_optimality_metadata() と同じ形の
    dict を組み立てる（docplex.cp/CP-SATのどちらでソルブしたかに依らず、
    呼び出し元が同じキーで扱えるようにするため）。
    """
    result: Dict[str, Any] = {"solve_status": None, "is_optimal": False, "solve_time_sec": None}
    try:
        status = model.status()
    except Exception:
        return result

    exitstatus_name = getattr(status.exitstatus, "name", str(status.exitstatus))
    result["solve_status"] = _EXITSTATUS_TO_SOLVE_STATUS.get(exitstatus_name, "Unknown")
    result["is_optimal"] = exitstatus_name == "OPTIMAL"
    try:
        result["solve_time_sec"] = float(status.runtime) if status.runtime is not None else None
    except Exception:
        pass
    return result
