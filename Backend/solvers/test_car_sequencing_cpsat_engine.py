"""
Backend/solvers/test_car_sequencing_cpsat_engine.py

2026-08-13追加。CarSequencingへの config.solver_engine="cpsat"（OR-Tools CP-SAT、
CPMpy経由）明示選択エンジンの配線テスト。

背景: docs/DESIGN_2026-08-12_cp_sat_backend_support.md 追記2「CPMpyパイロット
結果」で Backend/tools/pilot_cpmpy_car_sequencing.py により独立検証済みの
モデル構築ロジックを、solvers/car_sequencing_solver.py._solve_with_cpsat() に
本番実装した（solvers/base/engine_select.py で明示選択のみサポート、CE上限検知
時の自動フォールバックは非対応、Koshoshiとの相談2026-08-13）。

このテストファイルは意図的にdocplexに依存しない（importすらしない）。
CP-SATエンジンの存在意義そのものが「CPLEX/CP Optimizerが無い環境でも動く」
ことなので、このテストがdocplexの有無に依らず実行できることが、
その主張の直接的な裏付けになる（test_car_sequencing_ce_limit.pyは既存のCPO
パスのテストで、docplexのimportを前提にしている。両者は補完関係にあり、
本ファイルがCPOパスの代わりになるものではない）。
"""

import itertools
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from solvers.car_sequencing_solver import CarSequencingSolver


def _scenario_small():
    """test_car_sequencing_ce_limit.py の_scenario()と同一構成。"""
    return {
        "problem_class": "CarSequencing",
        "meta": {"instance_name": "cpsat_engine_test"},
        "car_types": [
            {"car_type_id": "A", "car_type_name": "A", "count": 2},
            {"car_type_id": "B", "car_type_name": "B", "count": 2},
        ],
        "options": [
            {"option_id": "opt1", "option_name": "opt1", "window_size": 2,
             "max_per_window": 1, "car_type_ids": ["A"]},
        ],
        "config": {"solve_time_sec": 5, "solver_engine": "cpsat"},
        "issue_statuses": {},
    }


def _brute_force_optimal(scenario):
    """docplex.cpにもCPMpyにも依存しない全探索（Backend/tools/pilot_cpmpy_car_sequencing.py
    のbrute_force_optimal()と同一ロジックをテスト内に自己完結させたもの）。"""
    car_types = scenario["car_types"]
    options = scenario["options"]
    config = scenario.get("config", {})
    max_consecutive = config.get("max_consecutive_same", 2)

    multiset = []
    for ct in car_types:
        multiset += [ct["car_type_id"]] * ct.get("count", 0)

    best = None
    seen = set()
    for perm in itertools.permutations(multiset):
        if perm in seen:
            continue
        seen.add(perm)
        n = len(perm)
        obj = 0
        for opt in options:
            q, p = opt["window_size"], opt["max_per_window"]
            needs = set(opt.get("car_type_ids", []))
            values = [1 if t in needs else 0 for t in perm]
            for s in range(max(0, n - q + 1)):
                obj += 10 * max(0, sum(values[s:s + q]) - p)
        if max_consecutive >= 1 and n > max_consecutive:
            for pos in range(max_consecutive, n):
                if all(perm[pos - i] == perm[pos] for i in range(1, max_consecutive + 1)):
                    obj += 1
        if best is None or obj < best:
            best = obj
    return best


def test_cpsat_engine_solves_and_matches_brute_force_optimum():
    scenario = _scenario_small()
    result = CarSequencingSolver(scenario).solve()

    assert result["status"] == "ok"
    assert result["feasible"] is True

    kpi = result["solutions"][0]["kpi"]
    expected_obj = _brute_force_optimal(scenario)
    assert kpi["objective_value"] == expected_obj

    # 車種ごとの生産台数がハード制約通り一致していること
    sequence = result["solutions"][0]["sequence"]
    assert len(sequence) == 4
    counts = {}
    for item in sequence:
        counts[item["car_type_id"]] = counts.get(item["car_type_id"], 0) + 1
    assert counts == {"A": 2, "B": 2}


def test_cpsat_engine_kpi_has_same_shape_as_cpo_path():
    """CP-SATパスが返すKPI辞書のキー集合が、既存CPOパス想定（_make_result呼び出し
    元が組み立てるkpi辞書）と同じであること。フロントエンド側は engine に依らず
    同じキーを参照するため、シェイプの一致は必須要件。"""
    result = CarSequencingSolver(_scenario_small()).solve()
    kpi = result["solutions"][0]["kpi"]
    assert set(kpi.keys()) == {
        "total_cars", "option_violation_total", "same_car_violation_total",
        "objective_value", "solve_time_sec", "is_optimal", "solve_status",
    }
    assert kpi["is_optimal"] is True
    assert kpi["solve_status"] == "Optimal"


def test_unknown_solver_engine_raises_clear_crash_issue():
    """未知のsolver_engineはサイレントにデフォルトへ落とさず、明確なissueとして
    表面化すること（solvers/base/engine_select.pyのValueErrorがsolve()外側の
    except Exceptionでcrash issueに変換される）。"""
    scenario = _scenario_small()
    scenario["config"]["solver_engine"] = "bogus_engine"

    result = CarSequencingSolver(scenario).solve()

    assert result["feasible"] is False
    assert result.get("_solver_crashed") is True


if __name__ == "__main__":
    import pytest
    raise SystemExit(pytest.main([__file__, "-v"]))
