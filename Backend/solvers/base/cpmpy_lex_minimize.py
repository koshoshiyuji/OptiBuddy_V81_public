"""
cpmpy_lex_minimize.py — 層A: CPMpy用レキシコグラフィック(辞書式)目的関数ヘルパー

【背景】
CP Optimizer (docplex.cp) の mdl.minimize_static_lex([obj1, obj2, ...]) は、
objectivesを優先順位順に並べ、obj1を最優先で最小化し、obj1の最適値を保った
まま obj2 を最小化…という辞書式(lexicographic)多目的最適化を1回のsolve()で
行う。tank_allocation_planner / medical_appointment_scheduler /
energy_cost_aware_scheduler / steel_mill_slab_design / patient_transport_planner /
medical_appointment_sequence_scheduler など多数のドメインがこれを使用している。

CPMpy/CP-SAT(ortools)には直接の等価APIが無いため、本モジュールは
「逐次solve方式」で同じ意味論を実装する:
  1. objectives[0] を最小化してsolve、達成値 v0 を得る
  2. モデルに (objectives[0] == v0) を追加（凍結）
  3. objectives[1] を最小化してsolve、達成値 v1 を得る
  4. モデルに (objectives[1] == v1) を追加（凍結）
  ... 以降同様

段階が増えるほどsolve()呼び出し回数が増えるため、time_limitは各段階に
適用される点に注意（CPOのminimize_static_lexは内部的に同様の多段phaseを
1回のsolve呼び出しの中で行うが、ユーザー視点の挙動としては近い）。

【層の境界】
- 層A (共通コア): このファイル全体（solve_lexicographic）。
- 層B/C: 各ドメインのsolver.pyは、制約を追加済みのcp.Modelと目的関数式の
  優先順位リストを渡すだけ。逐次solve制御ロジック自体は再実装しないこと。

【使い方】
    from solvers.base.cpmpy_lex_minimize import solve_lexicographic

    m = cp.Model([...制約...])
    solved, values = solve_lexicographic(
        m, [total_unassigned, total_used, total_dispersion],
        solver="ortools", time_limit=30,
    )
    if not solved:
        ...infeasible...
    total_unassigned_val, total_used_val, total_dispersion_val = values
"""

from __future__ import annotations

import logging
from typing import Any, List, Optional, Tuple

logger = logging.getLogger(__name__)

__all__ = ["solve_lexicographic"]


def solve_lexicographic(
    model: Any,
    objectives: List[Any],
    solver: str = "ortools",
    time_limit: Optional[float] = None,
) -> Tuple[bool, List[int]]:
    """
    辞書式(lexicographic)多目的最適化をCPMpyの逐次solve方式で実行する。

    Args:
        model:      cpmpy.Model インスタンス（制約は追加済み、目的関数は未設定でよい）。
                    このモデルは呼び出し中に破壊的変更される（各段階の最適値を
                    固定する等式制約が追加されていく）。
        objectives: 優先順位順（最優先が先頭）のCPMpy整数式のリスト。1件以上必須。
        solver:     cpmpyのsolver名（既定 "ortools" = CP-SAT）。
        time_limit: 各段階に適用するタイムリミット（秒）。Noneなら無制限。

    Returns:
        (solved, achieved_values)
        solved=True の場合、achieved_values は objectives と同じ順序・同じ長さの
        int リスト（各段階での最適値）。
        solved=False の場合、achieved_values は空リスト（いずれかの段階で
        infeasibleだったことを意味する。通常は最初の段階=最優先目的の時点で
        infeasibleならモデル全体がinfeasible）。

    Raises:
        ValueError: objectives が空の場合。
    """
    if not objectives:
        raise ValueError("[cpmpy_lex_minimize] objectives は1件以上必要です。")

    achieved: List[int] = []
    for idx, obj in enumerate(objectives):
        model.minimize(obj)
        kwargs = {"solver": solver}
        if time_limit is not None:
            kwargs["time_limit"] = time_limit
        solved = model.solve(**kwargs)
        if not solved:
            logger.warning(
                f"[cpmpy_lex_minimize] 段階{idx}(優先度{idx + 1}位, 全{len(objectives)}段階)で"
                f"infeasible。lexicographic最適化を中断します。"
            )
            return False, []
        val = obj.value()
        achieved.append(int(val))
        # 最後の目的関数の段階では、この後さらに解を絞る必要がないため凍結不要。
        if idx < len(objectives) - 1:
            model += (obj == val)

    return True, achieved
