
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable, Dict, List, Optional, Tuple

from solvers.base.solution_checker import solver_bug_issue, sweep_peak_usage
from i18n.common_messages import t as _common_t
from i18n.nurse_shift_weekly_cap_messages import t as _nurse_shift_t

from ._common import IssueRule, _rule




# ---------------------------------------------------------------------------
# _ENERGY_COST_AWARE_SCHEDULER_RULES（2026-09-01 新規、solution checker展開バッチ3）
#
# 制約: (a) 各ラインの電力/作業員/附帯設備の同時使用量はそれぞれ
#       max_power_kw/max_workers/max_equipment_slotsを超えない（pulseによる
#       cumulative制約）。電力は待機電力（standby_power_kw、ライン稼働区間
#       全体で消費）も含めて検算する。solvers.base.solution_checker.
#       sweep_peak_usage()によるスイープライン検算（O(n log n)）。
#       CPO/CP-SATモデル側は0.1kW単位に量子化してpulse/cumulativeの高さを
#       整数化しているため、電力チェックのみ量子化誤差を吸収する許容誤差
#       （0.1kW）を設ける。作業員・附帯設備は元々整数のため誤差なし。
#       (b) 各オーダーの稼働区間は、割り当てられたラインの稼働区間
#       （line_ops）に収まる（start_of/end_ofによるハード制約）。O(n)。
#       (c) 各オーダーは高々1ラインにしか割り当てられない
#       （presence_of合計<=1というハード制約）。O(n)。
# いずれもsolver内部のinterval変数やmdlオブジェクトは一切参照せず、返って
# きたschedule/line_opsと、DSL宣言のorders/linesの容量値のみから検算する。
# ---------------------------------------------------------------------------

_ENERGY_COST_AWARE_SCHEDULER_RULES: List[IssueRule] = [

    _rule(
        "line_capacity_violation",
        condition=lambda ctx: ctx["peak"] > ctx["capacity"] + ctx["tolerance"],
        build=lambda ctx: solver_bug_issue(
            f"line_capacity_violation_{ctx['resource_type']}_{ctx['line_id']}",
            f"ライン資源容量超過（解チェッカー）: {ctx['line_name']} / {ctx['resource_label']}",
            f"ライン「{ctx['line_name']}」の{ctx['resource_label']}同時使用量が最大{ctx['peak']:.2f}"
            f"に達し、容量{ctx['capacity']:.2f}を超過しています"
            f"（時刻{ctx['peak_time']}付近、スイープライン検算による独立検証）。",
        ),
    ),

    _rule(
        "order_outside_line_window",
        condition=lambda ctx: ctx["order_start"] < ctx["line_start"] or ctx["order_end"] > ctx["line_end"],
        build=lambda ctx: solver_bug_issue(
            f"order_outside_line_window_{ctx['order_id']}",
            f"オーダーがライン稼働区間外（解チェッカー）: {ctx['order_id']}",
            f"オーダー「{ctx['order_id']}」の稼働区間[{ctx['order_start']}, {ctx['order_end']}]が、"
            f"割り当てられたライン「{ctx['line_id']}」の稼働区間"
            f"[{ctx['line_start']}, {ctx['line_end']}]をはみ出しています。",
        ),
    ),

    _rule(
        "order_duplicate_assignment",
        condition=lambda ctx: ctx["assigned_count"] > 1,
        build=lambda ctx: solver_bug_issue(
            f"order_duplicate_assignment_{ctx['order_id']}",
            f"オーダー重複割当（解チェッカー）: {ctx['order_id']}",
            f"オーダー「{ctx['order_id']}」が{ctx['assigned_count']}件のラインに"
            "重複して割り当てられています。各オーダーは高々1ラインにしか"
            "割り当てられない制約と矛盾しています。",
        ),
    ),
]



# ---------------------------------------------------------------------------
# EnergyCostAwareScheduler context ビルダー（2026-09-01 新規）
# ---------------------------------------------------------------------------

# 電力チェックの許容誤差（kW）。CPO/CP-SATモデルは共に電力を0.1kW単位に
# 量子化してpulse/cumulativeの高さを整数化しているため（各solverファイル内の
# POWER_SCALEコメント参照）、返ってきた生の(浮動小数の)power_kwから検算すると
# 量子化丸めの分だけ理論上ずれ得る。作業員数・附帯設備枠は元々整数のため
# 量子化誤差はない。
_ENERGY_POWER_TOLERANCE_KW = 0.1


# (resource_type, order_field, capacity_field, order_field_default, label)
_ENERGY_RESOURCE_SPECS = [
    ("power",     "power_kw",        "max_power_kw",        0.0, "電力"),
    ("workers",   "workers",         "max_workers",         1.0, "作業員"),
    ("equipment", "equipment_slots", "max_equipment_slots", 1.0, "附帯設備"),
]



def build_energy_cost_aware_scheduler_contexts(
    schedule: List[Dict],
    line_ops: List[Dict],
    orders: List[Dict],
    lines: List[Dict],
) -> List[Dict[str, Any]]:
    """
    EnergyCostAwareScheduler 用の3チェックのcontextを生成する。solver内部の
    interval変数やmdlオブジェクトは一切参照せず、返ってきたschedule/line_ops
    と、DSL宣言のorders（workers/equipment_slots等の要求値。デフォルト値は
    solver本体の_build_and_solve()/_solve_with_cpsat()と揃えてある）・lines
    （容量上限）のみから検算する。

    (1) line_capacity_violation: ライン別・資源種別（電力/作業員/附帯設備）の
        同時使用量ピークをスイープライン検算（sweep_peak_usage、O(n log n)）
        し、容量上限と比較する。電力は待機電力（standby_power_kw、ライン
        稼働区間全体で消費）も含める。
    (2) order_outside_line_window: 各オーダーの稼働区間が、割り当てられた
        ラインの稼働区間（line_ops）に収まっているか（O(n)）。line_opsが
        見つからない場合はこのチェック対象から除く。
    (3) order_duplicate_assignment: 同一オーダーが複数ラインに重複割当されて
        いないか（O(n)）。
    """
    orders_map: Dict[str, Dict] = {str(o["id"]): o for o in orders}
    line_ops_map: Dict[str, Dict] = {op["line_id"]: op for op in line_ops}

    ctxs: List[Dict] = []

    # --- (1) ライン資源容量チェック ---
    for resource_type, order_field, capacity_field, default, label in _ENERGY_RESOURCE_SPECS:
        for ln in lines:
            lid = str(ln["id"])
            capacity = float(ln.get(capacity_field, 9999))
            events: List[tuple] = []
            for s in schedule:
                if s["line_id"] != lid:
                    continue
                o = orders_map.get(s["order_id"], {})
                amount = float(o.get(order_field, default))
                if amount <= 0:
                    continue
                events.append((s["start"], amount))
                events.append((s["end"], -amount))
            if resource_type == "power":
                standby = float(ln.get("standby_power_kw", 0.0))
                op = line_ops_map.get(lid)
                if standby > 0 and op is not None:
                    events.append((op["start"], standby))
                    events.append((op["end"], -standby))
            if not events:
                continue
            peak, peak_time = sweep_peak_usage(events)
            tolerance = _ENERGY_POWER_TOLERANCE_KW if resource_type == "power" else 1e-6
            ctxs.append({
                "_rule_id":        "line_capacity_violation",
                "resource_type":   resource_type,
                "resource_label":  label,
                "line_id":         lid,
                "line_name":       ln.get("name", lid),
                "peak":            peak,
                "capacity":        capacity,
                "peak_time":       peak_time,
                "tolerance":       tolerance,
            })

    # --- (2) オーダー稼働区間 ⊆ ライン稼働区間 ---
    for s in schedule:
        op = line_ops_map.get(s["line_id"])
        if op is None:
            continue
        ctxs.append({
            "_rule_id":    "order_outside_line_window",
            "order_id":    s["order_id"],
            "line_id":     s["line_id"],
            "order_start": s["start"],
            "order_end":   s["end"],
            "line_start":  op["start"],
            "line_end":    op["end"],
        })

    # --- (3) オーダー重複割当 ---
    order_count: Dict[str, int] = {}
    for s in schedule:
        order_count[s["order_id"]] = order_count.get(s["order_id"], 0) + 1
    for oid, count in order_count.items():
        ctxs.append({
            "_rule_id":       "order_duplicate_assignment",
            "order_id":       oid,
            "assigned_count": count,
        })

    return ctxs
