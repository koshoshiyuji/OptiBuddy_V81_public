"""
Backend/tools/compare_cpsat_vs_cpo_batch_2026-08-13.py

2026-08-13追加。config.solver_engine="cpsat" 本番実装済みの17ドメイン全件について、
同一シナリオを "cpo"(実CPLEX CP Optimizer) と "cpsat"(CP-SAT/CPMpy) の両エンジンで
解き、目的関数値・KPIが一致するかをまとめて検証するバッチスクリプト。
（初版は13ドメインだったが、Tier3(depot_route_planner/patient_transport_planner/
medical_appointment_sequence_scheduler)追加で16、NurseShiftWeeklyCap追加で
17ドメインに拡張。）

【depot_route_plannerについての注意（2026-08-13追記で解消）】
以前はStage1(施設配置・顧客割当)がdocplex.mp(CPLEX MIP)固定で、
config.solver_engineが影響するのはStage2(CP側のTSP)のみだった。
2026-08-13、Stage1にもCP-SAT(CPMpy)版(_stage1_cpsat)を追加し、
solver_engine="cpsat"を指定すればStage1・Stage2ともdocplexを一切
使わずに完走するようになった（solvers/test_depot_route_planner_stage1_cpsat.py
参照）。cpo指定時は従来通りStage1・Stage2ともCPLEXを使う。

【背景】
NurseShiftWeeklyCapパイロットの検証過程(2026-08-13)で、シナリオ構築や
フィールドマッピングのわずかな誤りが数値をサイレントに狂わせることが
繰り返し判明した。本スクリプトはこの教訓を踏まえ、各ドメインごとに
個別のcompare_with_docplex()往復を行う代わりに、13ドメイン分をまとめて
1回のローカル実行で確認できるようにしたもの。

【実行方法】
本サンドボックスにはCPLEX/docplexが無いため、cpsat側のみ実行され、
cpo側は "SKIPPED (docplex未インストール)" と表示される。
実CPLEXでの照合には、docplexがインストール済みのローカル環境
（例: ユーザーの "optibuddy" conda環境）で以下を実行する:

    cd Backend
    python tools/compare_cpsat_vs_cpo_batch_2026-08-13.py

各ドメインについて cpo値 / cpsat値 / MATCH の一覧が出力される。

【対象17ドメイン】
既にパイロット済みでMATCH:True確認済みの3ドメイン(car_sequencing/
meeting_room/line_changeover_scheduler)を含む、2026-08-13時点で
config.solver_engine="cpsat" 本番実装が完了している全ドメイン
（NurseShiftWeeklyCapを含む）。Yard・TruckDispatcherは対象外
（Yardは特殊需要でCPO専用、TruckDispatcherは別途フォールバック方針を検討中）。

シナリオは各ドメインの構造テストファイル(test_<domain>_cpsat_engine.py)の
_scenario()関数を再利用する（シナリオの二重管理を避けるため）。
"""

from __future__ import annotations

import sys
import os
import copy
import importlib
import traceback
from typing import Any, Callable, Optional, Tuple

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

try:
    import docplex.cp.model  # noqa: F401
    _HAS_DOCPLEX = True
except ImportError:
    _HAS_DOCPLEX = False


def _run_engine(solver_module: str, solver_class_name: str, scenario: dict, engine: str) -> dict:
    mod = importlib.import_module(solver_module)
    solver_class = getattr(mod, solver_class_name)
    s = copy.deepcopy(scenario)
    s["config"]["solver_engine"] = engine
    return solver_class(s).solve()


def _get(d: dict, *path, default=None):
    cur = d
    for p in path:
        if cur is None:
            return default
        if isinstance(p, int):
            if not isinstance(cur, list) or len(cur) <= p:
                return default
            cur = cur[p]
        else:
            if not isinstance(cur, dict) or p not in cur:
                return default
            cur = cur[p]
    return cur


# ─────────────────────────────────────────────────────────────────────────
# ドメイン定義: (表示名, testモジュール名, solverモジュール名, solverクラス名, metric抽出関数)
# metric抽出関数は result(dict) -> 比較可能な値(タプル/スカラー/None) を返す。
# Noneを返す場合はfeasibility(実行可能性)のみの比較(目的関数なしドメイン)。
# ─────────────────────────────────────────────────────────────────────────

def _metric_kpi_objective(result: dict):
    return _get(result, "solutions", 0, "kpi", "objective_value")


def _metric_top_kpi(key: str):
    def _fn(result: dict):
        return _get(result, "kpi", key)
    return _fn


def _metric_sol_kpi(key: str):
    def _fn(result: dict):
        return _get(result, "solutions", 0, "kpi", key)
    return _fn


def _metric_sol_metrics(*keys: str):
    def _fn(result: dict):
        return tuple(_get(result, "solutions", 0, "metrics", k) for k in keys)
    return _fn


def _metric_sol_kpi_multi(*keys: str):
    def _fn(result: dict):
        return tuple(_get(result, "solutions", 0, "kpi", k) for k in keys)
    return _fn


# build_input(test_mod, scenario_fn_name) -> solver_input(dict)
# 既定: シナリオ関数が直接solver_input辞書を返す想定。
def _build_input_direct(test_mod, fn_name: str) -> dict:
    return getattr(test_mod, fn_name)()


# MeetingRoom/LineChangeoverSchedulerのシナリオ関数は (meetings, rooms) /
# (tasks, resources[, precedences]) のタプルを返し、test_mod._solver_input()で
# solver_input辞書に組み立てる構成になっているため、それに合わせて展開する。
def _build_input_via_solver_input_helper(test_mod, fn_name: str) -> dict:
    raw = getattr(test_mod, fn_name)()
    return test_mod._solver_input(*raw)


DOMAINS = [
    ("CarSequencing", "solvers.test_car_sequencing_cpsat_engine", ["_scenario_small"],
     "solvers.car_sequencing_solver", "CarSequencingSolver",
     _metric_kpi_objective, _build_input_direct),
    ("MeetingRoom", "solvers.test_meeting_room_cpsat_engine", ["_scenario_normal", "_scenario_conflict"],
     "solvers.meeting_room_solver", "MeetingRoomSolver",
     _metric_kpi_objective, _build_input_via_solver_input_helper),
    ("LineChangeoverScheduler", "solvers.test_line_changeover_scheduler_cpsat_engine",
     ["_scenario_single_resource_serial", "_scenario_parallel_with_precedence"],
     "solvers.line_changeover_scheduler_solver", "LineChangeoverSchedulerSolver",
     _metric_kpi_objective, _build_input_via_solver_input_helper),
    ("ProductionLineSequencing", "solvers.test_production_line_sequencing_cpsat_engine", ["_scenario"],
     "solvers.production_line_sequencing_solver", "ProductionLineSequencingSolver",
     _metric_kpi_objective, _build_input_direct),
    ("PortfolioOverlapDesigner", "solvers.test_portfolio_overlap_designer_cpsat_engine", ["_scenario"],
     "solvers.portfolio_overlap_designer_solver", "PortfolioOverlapDesignerSolver",
     _metric_top_kpi("worst_overlap"), _build_input_direct),
    ("VesselDeckLoader", "solvers.test_vessel_deck_loader_cpsat_engine", ["_scenario"],
     "solvers.vessel_deck_loader_solver", "VesselDeckLoaderSolver",
     _metric_sol_kpi("used_length"), _build_input_direct),
    ("LotSizingScheduler", "solvers.test_lot_sizing_scheduler_cpsat_engine", ["_scenario"],
     "solvers.lot_sizing_scheduler_solver", "LotSizingSchedulerSolver",
     _metric_sol_kpi("objective_value"), _build_input_direct),
    ("TankAllocationPlanner", "solvers.test_tank_allocation_planner_cpsat_engine", ["_scenario"],
     "solvers.tank_allocation_planner_solver", "TankAllocationPlannerSolver",
     _metric_sol_metrics("objective_unassigned", "objective_used_tanks", "objective_dispersion"),
     _build_input_direct),
    ("ShiftRotationScheduler", "solvers.test_shift_rotation_scheduler_cpsat_engine", ["_scenario"],
     "solvers.shift_rotation_scheduler_solver", "ShiftRotationSchedulerSolver",
     None, _build_input_direct),  # feasibility-only(目的関数なし)。実行可能性のみ比較。
    ("NursingWorkloadBalance", "solvers.test_nursing_workload_balance_cpsat_engine", ["_scenario"],
     "solvers.nursing_workload_balance_solver", "NursingWorkloadBalanceSolver",
     _metric_sol_metrics("workload_sq_sum"), _build_input_direct),
    ("MedicalAppointmentScheduler", "solvers.test_medical_appointment_scheduler_cpsat_engine", ["_scenario"],
     "solvers.medical_appointment_scheduler_solver", "MedicalAppointmentSchedulerSolver",
     _metric_sol_kpi_multi("unassigned_count", "pref_total_violations"), _build_input_direct),
    ("EnergyCostAwareScheduler", "solvers.test_energy_cost_aware_scheduler_cpsat_engine", ["_scenario"],
     "solvers.energy_cost_aware_scheduler_solver", "EnergyCostAwareSchedulerSolver",
     _metric_sol_kpi_multi("completed_orders", "total_cost"), _build_input_direct),
    ("SteelMillSlabDesign", "solvers.test_steel_mill_slab_design_cpsat_engine", ["_scenario"],
     "solvers.steel_mill_slab_design_solver", "SteelMillSlabDesignSolver",
     _metric_sol_kpi_multi("total_waste_weight", "total_slabs_used"), _build_input_direct),
    ("DepotRoutePlanner", "solvers.test_depot_route_planner_cpsat_engine", ["__depot_route_full_scenario__"],
     "solvers.depot_route_planner_solver", "DepotRoutePlannerSolver",
     _metric_sol_kpi("travel_distance_total"), None),  # build_input は下でdepot専用に差し替え
    ("PatientTransportPlanner", "solvers.test_patient_transport_planner_cpsat_engine", ["_scenario"],
     "solvers.patient_transport_planner_solver", "PatientTransportPlannerSolver",
     _metric_sol_kpi_multi("unserved_count", "total_ride_time_min"), _build_input_direct),
    ("MedicalAppointmentSequenceScheduler", "solvers.test_medical_appointment_sequence_scheduler_cpsat_engine",
     ["_scenario"],
     "solvers.medical_appointment_sequence_scheduler_solver", "MedicalAppointmentSequenceSchedulerSolver",
     _metric_sol_kpi_multi("unscheduled_count", "max_resource_load_min"), _build_input_direct),
    # 2026-08-13追加: NurseShiftWeeklyCapもcpsat本番実装完了（Yard/TruckDispatcherは
    # 引き続き対象外。Yardは特殊需要のためCPO専用のまま、TruckDispatcherは別途
    # フォールバック方針を検討中）。
    ("NurseShiftWeeklyCap", "solvers.test_nurse_shift_weekly_cap_cpsat_engine",
     ["_scenario_flexible_conflict"],
     "solvers.nurse_shift_weekly_cap_solver", "NurseShiftWeeklyCapSolver",
     _metric_sol_metrics("total_cost", "understaffed_count"), _build_input_direct),
]


def _build_input_depot_route_full(test_mod, fn_name: str) -> dict:
    """DepotRoutePlannerは構造テストが(depot, customers)タプルのみを返す
    (Stage2の単体テスト用のため)。本スクリプトはsolve()全体を実行したいので、
    ここで独立したフル solver_input を組み立てる(Stage1 MIP用に複数拠点候補を含む)。
    """
    depots = [
        {"id": "D1", "name": "Depot1", "x": 0.0, "y": 0.0, "opening_cost": 100.0},
        {"id": "D2", "name": "Depot2", "x": 10.0, "y": 10.0, "opening_cost": 100.0},
    ]
    customers = [
        {"id": "C1", "name": "Cust1", "x": 1.0, "y": 1.0},
        {"id": "C2", "name": "Cust2", "x": 2.0, "y": -1.0},
        {"id": "C3", "name": "Cust3", "x": -1.0, "y": 2.0},
        {"id": "C4", "name": "Cust4", "x": 9.0, "y": 11.0},
        {"id": "C5", "name": "Cust5", "x": 11.0, "y": 9.0},
    ]
    return {
        "problem_class": "DepotRoutePlanner",
        "depots": depots,
        "customers": customers,
        "config": {"solver_engine": "cpsat", "max_open_depots": 2,
                   "stage1_time_limit_sec": 15, "stage2_time_limit_sec": 15},
        "issue_statuses": {},
    }


# DepotRoutePlannerだけbuild_input差し替え(タプル定義後に上書きするため末尾で処理)
DOMAINS = [
    d if d[0] != "DepotRoutePlanner" else d[:-1] + (_build_input_depot_route_full,)
    for d in DOMAINS
]


def _values_match(a, b, tol: float = 1e-6) -> bool:
    if isinstance(a, tuple) and isinstance(b, tuple):
        if len(a) != len(b):
            return False
        return all(_values_match(x, y, tol) for x, y in zip(a, b))
    if isinstance(a, (int, float)) and isinstance(b, (int, float)):
        return abs(float(a) - float(b)) <= tol
    return a == b


def main() -> None:
    print("=" * 90)
    print("CP-SAT vs CPO(実CPLEX) バッチ照合 — 2026-08-13")
    print(f"docplex利用可能: {_HAS_DOCPLEX}")
    print("=" * 90)

    all_ok = True

    for name, test_mod_name, scenario_fn_names, solver_mod_name, solver_cls_name, metric_fn, build_input in DOMAINS:
        print(f"\n--- {name} ---")
        try:
            test_mod = importlib.import_module(test_mod_name)
        except Exception as e:
            print(f"  [ERROR] テストモジュール読み込み失敗: {e}")
            all_ok = False
            continue

        for scenario_fn_name in scenario_fn_names:
            label = f"{name}::{scenario_fn_name}"
            try:
                scenario = build_input(test_mod, scenario_fn_name)
            except Exception as e:
                print(f"  [{scenario_fn_name}] [ERROR] シナリオ読み込み失敗: {e}")
                all_ok = False
                continue

            # cpsat側
            try:
                result_cpsat = _run_engine(solver_mod_name, solver_cls_name, scenario, "cpsat")
                cpsat_feasible = result_cpsat.get("feasible")
            except Exception as e:
                print(f"  [{scenario_fn_name}] [ERROR] cpsat実行失敗: {e}")
                traceback.print_exc()
                all_ok = False
                continue

            # cpo側(docplexが無い環境ではスキップ)
            if not _HAS_DOCPLEX:
                print(f"  [{scenario_fn_name}] cpsat: feasible={cpsat_feasible}"
                      + (f", metric={metric_fn(result_cpsat)}" if metric_fn else " (feasibility-only)"))
                print(f"  [{scenario_fn_name}] cpo:   SKIPPED (docplexが無い環境のため実行不可。"
                      "実CPLEX照合はdocplexインストール済み環境で実行してください)")
                continue

            try:
                result_cpo = _run_engine(solver_mod_name, solver_cls_name, scenario, "cpo")
                cpo_feasible = result_cpo.get("feasible")
            except Exception as e:
                print(f"  [{scenario_fn_name}] [ERROR] cpo実行失敗: {e}")
                traceback.print_exc()
                all_ok = False
                continue

            if metric_fn is None:
                match = (cpsat_feasible == cpo_feasible == True)
                print(f"  [{scenario_fn_name}] cpo:   feasible={cpo_feasible} (feasibility-only, 目的関数なし)")
                print(f"  [{scenario_fn_name}] cpsat: feasible={cpsat_feasible}")
                print(f"  [{scenario_fn_name}] MATCH(feasibility): {match}")
            else:
                v_cpo = metric_fn(result_cpo) if cpo_feasible else None
                v_cpsat = metric_fn(result_cpsat) if cpsat_feasible else None
                match = (cpo_feasible == cpsat_feasible) and (
                    v_cpo is None or _values_match(v_cpo, v_cpsat)
                )
                print(f"  [{scenario_fn_name}] cpo:   feasible={cpo_feasible}, metric={v_cpo}")
                print(f"  [{scenario_fn_name}] cpsat: feasible={cpsat_feasible}, metric={v_cpsat}")
                print(f"  [{scenario_fn_name}] MATCH: {match}")

            if not match:
                all_ok = False

    print("\n" + "=" * 90)
    if not _HAS_DOCPLEX:
        print("docplexが無いためcpo側は全てSKIPPEDでした。"
              "実CPLEX照合結果を得るにはdocplexインストール済み環境で再実行してください。")
    else:
        print(f"総合結果: {'ALL MATCH' if all_ok else 'MISMATCH ALERT — 上記[ERROR]/MATCH:Falseを確認してください'}")
    print("=" * 90)


if __name__ == "__main__":
    main()
