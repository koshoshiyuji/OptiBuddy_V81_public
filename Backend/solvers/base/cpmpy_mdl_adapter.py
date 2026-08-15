"""
cpmpy_mdl_adapter.py — 層A: CPMpy用 CpoModel風アダプタ

【背景】
quantity_requirement.py（apply_quantity_requirement）と rolling_window.py
（add_rolling_window_cap_constraints）は、CPOの CpoModel インスタンスが持つ
`mdl.add(constraint)` / `mdl.sum(exprs)` / `mdl.max(exprs)` というメソッド形式の
API を前提に書かれている（層Aの共通コアとして、hard/soft分岐やウィンドウ境界
処理をドメインごとに再実装させないための設計）。

CPMpyの `cp.Model()` はこれらをインスタンスメソッドとして持たない
（制約追加は `m += constraint`、集約は `cp.sum(...)`/`cp.max(...)` という
モジュールレベル関数）。ドメインのcpsat実装でこれら2つの共通ヘルパーを
そのまま呼べるよう、CpoModel風のadd/sum/maxメソッドを持つ薄いアダプタで
`cp.Model()` インスタンスをラップする。

【使い方】
    import cpmpy as cp
    from solvers.base.cpmpy_mdl_adapter import CpmpyMdlAdapter

    m = cp.Model()
    mdl = CpmpyMdlAdapter(m)
    shortfall = apply_quantity_requirement(mdl, present_vars, required_count, mode=mode)
    add_rolling_window_cap_constraints(mdl, presence_by_day, window_days=7, cap=cap, all_days=all_days)

【層の境界】
- 層A (共通コア): このファイル全体。ロジックは一切持たず、API変換のみ行う。
- quantity_requirement.py / rolling_window.py 側は、このアダプタの存在を
  一切意識しない（"mdl-likeな何か"としてadd/sum/maxを呼ぶだけ）。
"""

from __future__ import annotations

from typing import Any, List

import cpmpy as cp

__all__ = ["CpmpyMdlAdapter"]


class CpmpyMdlAdapter:
    """cp.Model() インスタンスを CpoModel 風の add/sum/max API でラップする薄いアダプタ。"""

    def __init__(self, cpmpy_model: "cp.Model"):
        self.m = cpmpy_model

    def add(self, constraint: Any) -> None:
        """CpoModelの `mdl.add(constraint)` 相当。"""
        self.m += constraint

    def sum(self, exprs: List[Any]) -> Any:
        """CpoModelの `mdl.sum(exprs)` 相当。"""
        return cp.sum(exprs)

    def max(self, exprs: List[Any]) -> Any:
        """CpoModelの `mdl.max(exprs)` 相当。"""
        return cp.max(exprs)
