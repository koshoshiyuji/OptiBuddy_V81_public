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

__all__ = ["CPO", "CPSAT", "get_solver_engine", "cpmpy_optimality_metadata"]

CPO = "cpo"
CPSAT = "cpsat"
_VALID_ENGINES = {CPO, CPSAT}

# デプロイ時のハードコードされた最終フォールバック値。
# .env の DEFAULT_SOLVER_ENGINE も未設定の場合にここへ落ちる。
# 2026-08-13: CPLEXが無い環境でも常に動くことを優先し "cpsat" とする
# （従来は "cpo" だったが、Koshoshiとの相談によりデプロイ時デフォルトを変更）。
_HARDCODED_DEFAULT_ENGINE = CPSAT


def get_solver_engine(config: Dict[str, Any]) -> str:
    """
    次の優先順位で solver_engine を決定する:
        1. config.solver_engine（明示指定、最優先）
        2. 環境変数 DEFAULT_SOLVER_ENGINE（.env、デプロイ環境単位の既定値）
        3. _HARDCODED_DEFAULT_ENGINE（"cpsat"）

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
        env_engine = os.environ.get("DEFAULT_SOLVER_ENGINE")
        if env_engine:
            engine = env_engine
            source = "環境変数 DEFAULT_SOLVER_ENGINE"
        else:
            engine = _HARDCODED_DEFAULT_ENGINE
            source = "ハードコードされたフォールバック"

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
