"""
quantity_requirement.py — 層A: 数量要件（hard/soft）共通コア
=================================================================

【背景】
ヒアリングシート§4-1「人数・数量に関するルールの扱い」は、
  a. 守れなければ「解なし」として扱いたい（hard）
  b. 人数不足を許容し、できるだけ近づける形で他を最適化したい（soft）
という選択式（未記入時はbが既定値）だが、実際にこの選択を受け止める共通実装は
これまで一度も存在しなかった。

`nurse_shift_weekly_cap_solver.py` を調査した結果、次のバグが判明した
（docs/DESIGN_2026-07-18_layer_ab_pattern_library.md 2-2-2節）:
  - 必要人数を `mdl.add(mdl.sum(present_vars) == required_count)` という
    無条件のハード等式制約で常に縛っていた（§4-1の回答に関わらず常にhard、
    しかも「以上」ではなく「ちょうど」なので過剰配置も禁止する、意図より厳しい制約）。
  - それとは別に、目的関数側に「不足人数 × ペナルティ」というsoft制約用の
    shortfall項も並存していたが、上のハード等式が無条件に効いている以上、
    shortfallは理論上ゼロにしかなり得ず、事実上のデッドコードだった。
  - Business DSLにもhard/softを区別するフィールドがそもそも存在しなかった。

このファイルは、この2択を明示的に切り替えられる単一のAPIとして提供する。

【層の境界】
- 層A (共通コア): このファイル全体（apply_quantity_requirement）。
- 層B/C: 各ドメインのsolver.pyは、presence_of()済みのCP式リストを
  組み立ててこの関数を呼ぶだけ。hard/softの分岐ロジック自体は
  再実装しないこと。

【使い方（例: NurseShiftWeeklyCap）】
    present_vars = [mdl.presence_of(assignment_itvs[c["id"]]) for c in tcands]
    shortfall = apply_quantity_requirement(
        mdl, present_vars, required_count,
        mode=task.get("quantity_requirement_mode", config_default_mode),
    )
    if shortfall is not None:
        # soft: ペナルティ項として目的関数に加算する
        understaffing_terms.append(UNDERSTAFFING_PENALTY * shortfall)
    # hard の場合は shortfall は None（ハード制約は関数内で mdl.add 済み）。

【mode="hard" の意味】
  comparison="at_least"（既定）: assigned_count >= required_count を必須にする
    （「最低N人必要」。過剰配置は禁止しない）。
  comparison="exact": assigned_count == required_count を必須にする
    （「ちょうどN人」。旧バグの挙動を明示的に再現したい場合のみ使用）。
  いずれも満たせなければモデル全体がinfeasibleになる
  （§4-1 a.「解なしとして扱いたい」の意図通り）。

【mode="soft" の意味】
  ハード制約は追加しない。shortfall = max(0, required_count - assigned_count)
  を返すので、呼び出し側が目的関数のペナルティ項として使う
  （§4-1 b.「欠員を許容し、できるだけ近づける」の意図通り）。
"""

from __future__ import annotations

from typing import Any, List, Literal, Optional

__all__ = ["apply_quantity_requirement", "DEFAULT_QUANTITY_REQUIREMENT_MODE"]

# ヒアリングシート§4-1の既定値（未記入の場合の既定選択）と一致させる。
DEFAULT_QUANTITY_REQUIREMENT_MODE = "soft"


def apply_quantity_requirement(
    mdl: Any,
    present_vars: List[Any],
    required_count: int,
    mode: Literal["hard", "soft"] = DEFAULT_QUANTITY_REQUIREMENT_MODE,
    comparison: Literal["at_least", "exact"] = "at_least",
) -> Optional[Any]:
    """
    数量要件（「◯人/◯台必要」）をhard/softいずれかの方針でモデルに適用する。

    Args:
        mdl:            CpoModel インスタンス
        present_vars:   presence_of() 済みのCP式のリスト（候補が0件でも可）
        required_count: 必要数
        mode:           "hard" または "soft"。未指定時は DEFAULT_QUANTITY_REQUIREMENT_MODE。
        comparison:     mode="hard" の場合のみ有効。
                        "at_least"（既定, assigned >= required）または
                        "exact"（assigned == required、過剰配置も禁止）。

    Returns:
        mode="soft" の場合: shortfall（CpoExpr, = max(0, required_count - assigned_count)）。
                             呼び出し側が目的関数のペナルティ項として使うこと。
        mode="hard" の場合: None（制約は関数内で mdl.add 済み）。

    Raises:
        ValueError: mode / comparison に未知の値が指定された場合。
    """
    if mode not in ("hard", "soft"):
        raise ValueError(f"mode は 'hard' または 'soft' である必要があります: {mode!r}")
    if comparison not in ("at_least", "exact"):
        raise ValueError(f"comparison は 'at_least' または 'exact' である必要があります: {comparison!r}")

    assigned_count = mdl.sum(present_vars) if present_vars else 0

    if mode == "hard":
        if comparison == "exact":
            mdl.add(assigned_count == required_count)
        else:
            mdl.add(assigned_count >= required_count)
        return None

    # mode == "soft"
    return mdl.max([0, required_count - assigned_count])
