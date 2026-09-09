
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable, Dict, List, Optional, Tuple

from solvers.base.solution_checker import solver_bug_issue, sweep_peak_usage
from i18n.common_messages import t as _common_t
from i18n.nurse_shift_weekly_cap_messages import t as _nurse_shift_t

from ._common import IssueRule, _rule




# ---------------------------------------------------------------------------
# _VESSEL_DECK_LOADER_RULES（2026-09-01 新規、solution checker展開バッチ6・最終）
#
# 制約: (1) 甲板幅制約（既存のwidth_violationで検証済み、本カタログでは扱わない）。
#       (2) 2次元非重複制約（危険物マージン込み） — 最も複雑な部分（logical_or
#           を多用、過去2回のバグ修正実績あり）。
#       (3) 積み込み順序の支持制約（西壁/南壁/北壁 or 先行コンテナとの
#           正の長さを持つ境界接触） — 同じく複雑（logical_and多用）。
#       (4) Z（used_length）はmax(x_i+l_i)の最小化対象。
# solverのvessel_deck_loader_solver.py内に既にあった手書きの`hazard_proximity`
# チェック（危険物ペアのみ・マージンありの場合限定）は、本カタログの
# container_overlap_violation（全ペア総当たり、margin=0のケースも含む一般化）
# で置き換える。width_violationは維持（別の制約を検証しているため）。
# ---------------------------------------------------------------------------
_VESSEL_DECK_LOADER_RULES: List[IssueRule] = [
    _rule(
        "container_missing_from_placement",
        condition=lambda ctx: ctx["occurrence_count"] == 0,
        build=lambda ctx: solver_bug_issue(
            f"container_missing_from_placement_{ctx['container_id']}",
            f"コンテナが結果に存在しません（解チェッカー）: {ctx['container_id']}",
            f"コンテナ「{ctx['container_id']}」がplacementsに現れていません"
            "（解抽出処理で欠落した疑いがあります）。",
        ),
    ),
    _rule(
        "container_overlap_violation",
        condition=lambda ctx: not ctx["separated"],
        build=lambda ctx: solver_bug_issue(
            f"container_overlap_violation_{ctx['cid_a']}_{ctx['cid_b']}",
            f"コンテナ重なり（解チェッカー）: {ctx['name_a']} ⇔ {ctx['name_b']}",
            f"コンテナ「{ctx['name_a']}」と「{ctx['name_b']}」の2次元配置が"
            f"重なっています（必要マージン{ctx['margin']}を考慮した非重複制約の"
            "独立検証）。",
        ),
    ),
    _rule(
        "container_unsupported",
        condition=lambda ctx: not ctx["supported"],
        build=lambda ctx: solver_bug_issue(
            f"container_unsupported_{ctx['container_id']}",
            f"支持条件違反（解チェッカー）: {ctx['container_name']}",
            f"コンテナ「{ctx['container_name']}」が西壁・南壁・北壁のいずれにも"
            "接しておらず、かつ先行コンテナとも正の長さを持つ境界線分で"
            "接していません（積み込み順序の支持制約と矛盾しています）。",
        ),
    ),
    _rule(
        "used_length_mismatch",
        condition=lambda ctx: ctx["reported_z"] != ctx["recomputed_z"],
        build=lambda ctx: solver_bug_issue(
            "used_length_mismatch",
            "使用甲板長不整合（解チェッカー）",
            f"報告されたZ_val（used_length={ctx['reported_z']}）が、実際の配置から"
            f"再計算した最大値（{ctx['recomputed_z']}）と一致しません。",
        ),
    ),
]



# ---------------------------------------------------------------------------
# VesselDeckLoader context ビルダー（2026-09-01 新規、バッチ6・最終）
# ---------------------------------------------------------------------------

def build_vessel_deck_loader_contexts(
    placements:  List[Dict],
    Z_val:       int,
    deck_width:  int,
    hazard_margin: int,
    containers:  List[Dict],
) -> List[Dict[str, Any]]:
    """
    VesselDeckLoader 用の独立検証contextを生成する。solver内部の
    CpoModel/cpmpy決定変数は一切参照せず、返ってきたplacementsとDSL入力
    (containers/deck/config)のみから独立に再計算する。container_unsupported
    の判定はsolverの支持制約ロジック（西壁/南壁/北壁 or 先行コンテナとの
    境界接触、load_orderで安定ソート）を独立に再実装したもの。
    """
    ctxs: List[Dict[str, Any]] = []

    placement_count: Dict[str, int] = {}
    for p in placements:
        placement_count[p["container_id"]] = placement_count.get(p["container_id"], 0) + 1
    for c in containers:
        cid = c["id"]
        ctxs.append({
            "_rule_id": "container_missing_from_placement",
            "container_id": cid,
            "occurrence_count": placement_count.get(cid, 0),
        })

    n = len(placements)
    for i in range(n):
        pi = placements[i]
        for j in range(i + 1, n):
            pj = placements[j]
            mg = hazard_margin if (pi["is_hazardous"] and pj["is_hazardous"]) else 0
            x_sep = (pi["x"] + pi["length"] + mg <= pj["x"] or
                     pj["x"] + pj["length"] + mg <= pi["x"])
            y_sep = (pi["y"] + pi["width"] + mg <= pj["y"] or
                     pj["y"] + pj["width"] + mg <= pi["y"])
            ctxs.append({
                "_rule_id": "container_overlap_violation",
                "cid_a": pi["container_id"], "cid_b": pj["container_id"],
                "name_a": pi["container_name"], "name_b": pj["container_name"],
                "separated": x_sep or y_sep,
                "margin": mg,
            })

    order_sorted = sorted(placements, key=lambda p: p["load_order"])
    for rank, pk in enumerate(order_sorted):
        if rank == 0:
            continue
        lk, wk = pk["length"], pk["width"]
        supported = (
            pk["x"] == 0
            or pk["y"] == 0
            or pk["y"] + wk == deck_width
        )
        if not supported:
            for pj in order_sorted[:rank]:
                lj, wj = pj["length"], pj["width"]
                contact_east = (pk["x"] == pj["x"] + lj and pj["y"] < pk["y"] + wk and pk["y"] < pj["y"] + wj)
                contact_west = (pj["x"] == pk["x"] + lk and pj["y"] < pk["y"] + wk and pk["y"] < pj["y"] + wj)
                contact_south = (pk["y"] == pj["y"] + wj and pj["x"] < pk["x"] + lk and pk["x"] < pj["x"] + lj)
                contact_north = (pj["y"] == pk["y"] + wk and pj["x"] < pk["x"] + lk and pk["x"] < pj["x"] + lj)
                if contact_east or contact_west or contact_south or contact_north:
                    supported = True
                    break
        ctxs.append({
            "_rule_id": "container_unsupported",
            "container_id": pk["container_id"], "container_name": pk["container_name"],
            "supported": supported,
        })

    max_extent = max((p["x"] + p["length"] for p in placements), default=0)
    ctxs.append({
        "_rule_id": "used_length_mismatch",
        "reported_z": Z_val, "recomputed_z": max_extent,
    })

    return ctxs
