"""
test_production_line_sequencing_cpsat_engine.py

ProductionLineSequencingSolver の config.solver_engine="cpsat" 経路の構造テスト。
docplex/CPLEXがないサンドボックスでも実行できる（CPO経路は呼ばれない）。
"""

from __future__ import annotations

import sys
import os

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from solvers.production_line_sequencing_solver import ProductionLineSequencingSolver


def _scenario():
    # 2ライン x 2日 x 各日3スロット。2車種。バッチはスロット数と同数。
    lines = [{"id": "L1", "name": "Line1"}, {"id": "L2", "name": "Line2"}]
    days = [1, 2]
    slots = []
    sid = 0
    for day in days:
        for lid in ["L1", "L2"]:
            for h in [8, 10, 12]:
                sid += 1
                slots.append({"id": f"s{sid}", "line_id": lid, "day": day, "start_hour": h, "line_name": lid})

    vehicle_types = [
        {"id": "VA", "name": "TypeA", "daily_limit": 10, "priority_order": 1},
        {"id": "VB", "name": "TypeB", "daily_limit": 10, "priority_order": 2},
    ]

    batches = []
    for i, s in enumerate(slots):
        vtid = "VA" if i % 2 == 0 else "VB"
        batches.append({
            "id": f"b{i+1}",
            "vehicle_type": vtid,
            "name": f"Batch{i+1}",
            "compatible_lines": None,
            "line_on_day": 1,
            "line_off_day": 9999,
        })

    return {
        "problem_class": "ProductionLineSequencing",
        "batches": batches,
        "slots": slots,
        "lines": lines,
        "days": days,
        "vehicle_types": vehicle_types,
        "distribution_exceptions": [],
        "config": {"solver_engine": "cpsat", "time_limit_sec": 10},
        "issue_statuses": {},
    }


def test_cpsat_assigns_all_batches_feasibly():
    solver_input = _scenario()
    result = ProductionLineSequencingSolver(solver_input).solve()
    assert result["feasible"] is True, result["issues"]
    assignments = result["solutions"][0]["assignments"]
    assert len(assignments) == len(solver_input["batches"])

    # 各スロットに高々1バッチ（all_diff制約の検証）
    slot_ids_used = [a["slot_id"] for a in assignments]
    assert len(slot_ids_used) == len(set(slot_ids_used))

    # Batting order: 同日内の全(VA,VB)ペアで VAのstart_hour <= VBのstart_hour
    by_day = {}
    for a in assignments:
        by_day.setdefault(a["day"], []).append(a)
    for day, alist in by_day.items():
        va_hours = [a["start_hour"] for a in alist if a["vehicle_type"] == "VA"]
        vb_hours = [a["start_hour"] for a in alist if a["vehicle_type"] == "VB"]
        for vah in va_hours:
            for vbh in vb_hours:
                assert vah <= vbh, f"day={day}: VA({vah}) should be <= VB({vbh})"


def test_cpsat_kpi_shape():
    solver_input = _scenario()
    result = ProductionLineSequencingSolver(solver_input).solve()
    assert result["feasible"] is True
    kpi = result["solutions"][0]["kpi"]
    assert "line_usage" in kpi
    assert "balance_score" in kpi
    assert "solve_status" in kpi
    assert "objective_value" in kpi
    assert kpi["solve_status"] in ("Optimal", "Feasible")
    # 目的関数値とKPIのbalance_scoreが一致するはず(同じ定義のため)
    assert abs(kpi["objective_value"] - kpi["balance_score"]) < 1e-6


def test_unknown_engine_raises():
    solver_input = _scenario()
    solver_input["config"]["solver_engine"] = "bogus"
    result = ProductionLineSequencingSolver(solver_input).solve()
    # get_solver_engine が例外を投げ、solve()内のexceptで crash issue に変換される
    assert result["feasible"] is False
    assert result["issues"], "crash issueが記録されているはず"


if __name__ == "__main__":
    test_cpsat_assigns_all_batches_feasibly()
    test_cpsat_kpi_shape()
    test_unknown_engine_raises()
    print("ALL PASSED")
