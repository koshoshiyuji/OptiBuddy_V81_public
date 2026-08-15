"""
Backend/solvers/test_truck_dispatcher_csplib_regression.py

2026-07-27追加。CSPLib連携によるベースレイヤー(層A: BaseConstraintApplier等の
共有機構)のリグレッションテスト、第1弾。

背景: これまで「実際に解いた結果が数学的に正しいか」を外部の客観的な基準
（既知の最適値）と突き合わせる回帰テストが存在せず、feasible/infeasibleの
真偽値だけを見る内部チェックに留まっていた。CSPLib prob086 (CVRP) は
TruckDispatcherの基底問題として対応付けられており、CVRPLIBの標準ベンチマーク
インスタンス E-n22-k4（Christofides & Eilon、既知最適値: 総距離375.0km・
車両4台）が dsl_repository/scenarios/truck_dispatcher_csplib_en22k4.json
として既に用意されていたが、どのテストからも参照されず眠っていた
（2026-07-27にKoshoshiとの会話で発覚）。本ファイルはこれを実際に
TruckDispatcherSolverへ通し、既知の最適値と突き合わせる。

検証方針（シナリオのmeta.noteに記録された2026-07-23の先行検証結果を踏襲）:
  - 主指標: 車両数が既知最適(4台)と一致すること。
  - 副指標: 総距離が既知最適(375.0km)からの乖離が一定範囲内であること。
    このシナリオはCVRPLIBの平面座標をHaversine近似で変換した擬似lat/lonを
    使っているため、理論値までの完全一致(gap 0%)ではなく、2026-07-23の
    先行検証で実際に得られたgap +2.25%（383.43km）程度を基準に、
    大幅な悪化（層Aの制約・目的関数ロジックの回帰を疑うべき水準）のみを
    検出する緩めの許容範囲（既知最適から15%以内）とする。

実行方法:
  cd Backend && python -m pytest solvers/test_truck_dispatcher_csplib_regression.py -v

このテストはdocplex.cp（本物のCP Optimizerエンジン）を実際に呼ぶため、
エンジンが無い環境ではスキップされる。注意: `docplex.cp.model`は
cpoptimizerバイナリが無くてもimport自体は成功してしまう（実行時にsolve()を
呼んで初めて失敗する）ため、pytest.importorskipだけでは不十分。
shutil.which("cpoptimizer")でバイナリの実在をPATH上で確認し、無ければ
明示的にスキップする（バイナリはPATH経由でしか検出できないため、通常の
CPLEX Studioインストール環境ではPATHが通っている前提。このサンドボックス
環境のように非標準の場所にある場合はPATHに追加してから実行すること）。
"""

import json
import shutil
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

if shutil.which("cpoptimizer") is None:
    pytest.skip(
        "cpoptimizerバイナリがPATH上に見つかりません（本物のCP Optimizerエンジンが"
        "必要なテストのためスキップ）。",
        allow_module_level=True,
    )

pytest.importorskip("docplex.cp.model")

from dsl_transformer.truck_dispatcher_converter import convert_truck_dispatcher_to_solver
from solvers.truck_dispatcher_solver import TruckDispatcherSolver

_SCENARIO_PATH = (
    Path(__file__).parent.parent / "dsl_repository" / "scenarios"
    / "truck_dispatcher_csplib_en22k4.json"
)

# CVRPLIB E-n22-k4（Christofides & Eilon）の既知最適値
_KNOWN_OPTIMAL_DISTANCE_KM = 375.0
_KNOWN_OPTIMAL_VEHICLE_COUNT = 4

# 2026-07-23の先行検証（シナリオmeta.note参照）で実際に得られたgapは+2.25%
# （Haversine近似座標を使うため理論値と完全一致はしない）。層Aの回帰を検出する
# 目的の閾値なので、既知の近似誤差にはある程度の余裕を持たせつつ、大幅な
# 悪化（例: 制約バグで大回り経路になる等）は検出できる水準にする。
_DISTANCE_GAP_TOLERANCE_RATIO = 0.15


def _solve_scenario():
    business_dsl = json.loads(_SCENARIO_PATH.read_text(encoding="utf-8"))
    solver_input = convert_truck_dispatcher_to_solver(business_dsl)
    solver_input.setdefault("issue_statuses", {})
    return TruckDispatcherSolver(solver_input).solve()


@pytest.fixture(scope="module")
def solved_result():
    # config.solver_time_limit_sec=30のため1回のsolve()に約30秒かかる。
    # テストごとに再solveすると4倍以上の時間がかかってしまうため、
    # module scopeでキャッシュし1回だけ実行する。
    return _solve_scenario()


def test_scenario_file_exists_and_documents_known_optimal():
    """既知最適値がシナリオのmeta.noteに記録されていること（前提条件の確認）。"""
    business_dsl = json.loads(_SCENARIO_PATH.read_text(encoding="utf-8"))
    note = business_dsl["meta"]["note"]
    assert "375.0" in note
    assert "4台" in note


def test_vehicle_count_matches_known_optimal(solved_result):
    """主指標: 使用車両数がCVRPLIB既知最適の4台と一致すること。"""
    sol = solved_result["solutions"][0]
    assert sol["feasible"] is True
    assert sol["kpi"]["n_vehicles_used"] == _KNOWN_OPTIMAL_VEHICLE_COUNT


def test_total_distance_within_tolerance_of_known_optimal(solved_result):
    """副指標: 総距離が既知最適(375.0km)から許容範囲(15%)以内であること。

    Haversine近似座標による誤差（2026-07-23検証時点で+2.25%）を踏まえた緩めの
    閾値。層Aの制約・目的関数ロジックに回帰が起きれば、これを大きく超える
    悪化（大回り経路・容量制約の誤適用等）が生じるはずで、それを検出する。
    """
    sol = solved_result["solutions"][0]
    total_dist = sol["kpi"]["total_dist_km"]
    max_acceptable = _KNOWN_OPTIMAL_DISTANCE_KM * (1 + _DISTANCE_GAP_TOLERANCE_RATIO)
    assert total_dist <= max_acceptable, (
        f"総距離{total_dist}kmが既知最適{_KNOWN_OPTIMAL_DISTANCE_KM}kmから"
        f"{_DISTANCE_GAP_TOLERANCE_RATIO*100:.0f}%を超えて乖離しています"
        f"（許容上限{max_acceptable:.1f}km）。層Aの制約・目的関数ロジックに"
        f"回帰が無いか確認してください。"
    )
    # 既知最適を下回ることは無いはず（下回ればCVRPLIB既知最適値自体が誤りか、
    # 距離計算ロジックにバグがある可能性が高い）
    assert total_dist >= _KNOWN_OPTIMAL_DISTANCE_KM * 0.95


def test_all_customers_served(solved_result):
    """全22顧客が割り当てられ、未割当が発生していないこと。"""
    sol = solved_result["solutions"][0]
    assert sol["unserved_customers"] == []
    assert sol["kpi"]["n_stops_total"] == 21  # depot除く22顧客の記述だが実データは21件


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-v"]))
