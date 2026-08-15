"""
test_route_decomposer.py
==========================

RouteDecomposer（TruckDispatcher用LNS）のユニットテスト。特に2026-07-18追加の
早期終了（stagnation_limit）ロジックを検証する。docs/ENGINEERING_LOG.md
2026-07-18参照（「iterationが無駄に回ってタイムアウト待ちになっている」への対応）。

CP Optimizerエンジンなしで動作する（cpo_solve_fnをスタブ化し、
_CVRPFallbackEngineもモンキーパッチで置き換える）。

実行方法:
    cd Backend
    python -m pytest solvers/test_route_decomposer.py -v
"""

import pytest

from solvers.route_decomposer import RouteDecomposer
import solvers.truck_dispatcher_solver as tds


def _fake_route(vehicle_id, dist=0.0, customer_id=None):
    stops = [{"customer_id": customer_id}] if customer_id else []
    return {
        "vehicle_id": vehicle_id, "stops": stops, "route_dist_km": dist,
        "load_kg": 0, "duty_min": 0, "n_stops": len(stops), "utilization_pct": 0,
    }


class _FakeFallbackEngine:
    """_CVRPFallbackEngineの代わりに、固定の初期解（2ルート、未割当なし）を返す。"""

    def __init__(self, customers, vehicles, dist_matrix, config):
        self._vehicles = vehicles

    def solve(self):
        routes = [
            _fake_route(v["id"], dist=10.0, customer_id=f"c{i}")
            for i, v in enumerate(self._vehicles[:2])
        ]
        return {
            "feasible": True,
            "routes": routes,
            "kpi": tds.TruckDispatcherSolver._build_kpi(routes, sum(r["route_dist_km"] for r in routes)),
            "unserved_customers": [],
        }


def _minimal_fleet(n_vehicles=3):
    vehicles = [{"id": f"v{i}", "capacity_kg": 1000} for i in range(n_vehicles)]
    customers = [{"id": f"c{i}", "_loc_idx": i + 1} for i in range(3)]
    locations = [{"id": "depot"}] + [{"id": f"loc{i}"} for i in range(3)]
    dist_matrix = [[0.0] * 4 for _ in range(4)]
    return locations, vehicles, customers, dist_matrix


@pytest.fixture(autouse=True)
def _patch_fallback_engine(monkeypatch):
    monkeypatch.setattr(tds, "_CVRPFallbackEngine", _FakeFallbackEngine)


# ---------------------------------------------------------------------------
# 早期終了（stagnation_limit）
# ---------------------------------------------------------------------------

def test_stops_early_after_stagnation_limit_when_never_improving():
    """
    Recreateが毎回「改善なし」（元と同じルート）を返し続ける場合、
    max_iterations（多め）まで回りきらず、stagnation_limit回で早期終了すること。
    """
    locations, vehicles, customers, dist_matrix = _minimal_fleet()

    call_count = {"n": 0}

    def never_improving_cpo_solve_fn(locations, pool_vehicles, pool_customers, dist_matrix, sub_config):
        call_count["n"] += 1
        # Ruinされた分をそのまま（改善なしの）ルートとして返す
        routes = [
            _fake_route(v["id"], dist=10.0, customer_id=(pool_customers[i]["id"] if i < len(pool_customers) else None))
            for i, v in enumerate(pool_vehicles)
        ]
        return {"feasible": True, "routes": routes, "unserved_customers": []}

    decomposer = RouteDecomposer(
        ruin_vehicle_count=2, max_iterations=100, iter_time_limit_sec=1,
        total_time_budget_sec=9999.0, stagnation_limit=5,
    )
    result = decomposer.solve(
        locations, vehicles, customers, dist_matrix, config={},
        cpo_solve_fn=never_improving_cpo_solve_fn,
    )

    # stagnation_limit=5なので、改善が一度もない場合は5回程度で打ち切られるはず
    # （max_iterations=100まで回りきらないことが本質的な確認ポイント）。
    assert call_count["n"] < 100
    assert call_count["n"] <= 6  # stagnation_limit + 若干の余裕
    assert result["_lns_used"] is True


def test_does_not_stop_early_when_continuously_improving():
    """
    毎回改善し続ける場合は、stagnation_limitに関わらずmax_iterationsまで
    （または時間予算まで）回り続けること＝早期終了ロジックが「常に短縮する」
    誤動作をしていないことの確認。
    """
    locations, vehicles, customers, dist_matrix = _minimal_fleet()

    call_count = {"n": 0}

    def always_improving_cpo_solve_fn(locations, pool_vehicles, pool_customers, dist_matrix, sub_config):
        call_count["n"] += 1
        # 呼ばれるたびに距離が短くなる（＝毎回改善）ルートを返す
        dist = max(0.1, 10.0 - call_count["n"])
        routes = [
            _fake_route(v["id"], dist=dist, customer_id=(pool_customers[i]["id"] if i < len(pool_customers) else None))
            for i, v in enumerate(pool_vehicles)
        ]
        return {"feasible": True, "routes": routes, "unserved_customers": []}

    decomposer = RouteDecomposer(
        ruin_vehicle_count=2, max_iterations=10, iter_time_limit_sec=1,
        total_time_budget_sec=9999.0, stagnation_limit=3,
    )
    result = decomposer.solve(
        locations, vehicles, customers, dist_matrix, config={},
        cpo_solve_fn=always_improving_cpo_solve_fn,
    )

    assert call_count["n"] == 10  # max_iterationsまでフルに回っている
    assert result["_lns_used"] is True


def test_stagnation_limit_is_configurable_via_constructor():
    locations, vehicles, customers, dist_matrix = _minimal_fleet()
    call_count = {"n": 0}

    def never_improving(locations, pool_vehicles, pool_customers, dist_matrix, sub_config):
        call_count["n"] += 1
        routes = [
            _fake_route(v["id"], dist=10.0, customer_id=(pool_customers[i]["id"] if i < len(pool_customers) else None))
            for i, v in enumerate(pool_vehicles)
        ]
        return {"feasible": True, "routes": routes, "unserved_customers": []}

    decomposer = RouteDecomposer(
        ruin_vehicle_count=2, max_iterations=100, iter_time_limit_sec=1,
        total_time_budget_sec=9999.0, stagnation_limit=2,
    )
    decomposer.solve(locations, vehicles, customers, dist_matrix, config={}, cpo_solve_fn=never_improving)
    assert call_count["n"] <= 3  # stagnation_limit=2なので、より早く打ち切られる


def test_default_stagnation_limit_is_eight():
    """docs/ENGINEERING_LOG.md 2026-07-18で決めた既定値（8）が変わっていないこと。"""
    decomposer = RouteDecomposer()
    assert decomposer.stagnation_limit == 8


# ---------------------------------------------------------------------------
# プール顧客数の上限（2026-07-18e追加、Problem size limit exceeded対策）
# ---------------------------------------------------------------------------

def _fake_route_multi(vehicle_id, customer_ids, dist=0.0):
    stops = [{"customer_id": cid} for cid in customer_ids]
    return {
        "vehicle_id": vehicle_id, "stops": stops, "route_dist_km": dist,
        "load_kg": 0, "duty_min": 0, "n_stops": len(stops), "utilization_pct": 0,
    }


class _FakeFallbackEngineManyStopsPerVehicle:
    """
    各車両が多数(5件)の顧客を担当する初期解を返す。ruin_vehicle_count(既定4)を
    そのまま使うと、4台選んだだけでプールが20顧客になり得ることを確認するための
    フィクスチャ。
    """

    def __init__(self, customers, vehicles, dist_matrix, config):
        self._vehicles = vehicles
        self._customers = customers

    def solve(self):
        routes = []
        cursor = 0
        for v in self._vehicles:
            cids = [c["id"] for c in self._customers[cursor:cursor + 5]]
            cursor += 5
            if cids:
                routes.append(_fake_route_multi(v["id"], cids, dist=10.0))
        return {
            "feasible": True,
            "routes": routes,
            "kpi": tds.TruckDispatcherSolver._build_kpi(routes, sum(r["route_dist_km"] for r in routes)),
            "unserved_customers": [],
        }


def test_ruin_pool_size_never_exceeds_max_pool_customers():
    """
    各車両が5顧客ずつ担当する状況で ruin_vehicle_count=4, max_pool_customers=12 の場合、
    単純に4台選ぶと最大20顧客になり得るが、実際にRecreateへ渡されるpool_customersの
    サイズは常にmax_pool_customers以下（ただし最低1台分は超えてもよい）であることを
    確認する。
    """
    vehicles  = [{"id": f"v{i}", "capacity_kg": 1000} for i in range(6)]
    customers = [{"id": f"c{i}", "_loc_idx": i + 1} for i in range(30)]
    locations = [{"id": "depot"}] + [{"id": f"loc{i}"} for i in range(30)]
    dist_matrix = [[0.0] * 31 for _ in range(31)]

    import solvers.route_decomposer as rd_module
    orig_engine = tds._CVRPFallbackEngine
    tds._CVRPFallbackEngine = _FakeFallbackEngineManyStopsPerVehicle
    try:
        pool_sizes_seen = []

        def recording_cpo_solve_fn(locations, pool_vehicles, pool_customers, dist_matrix, sub_config):
            pool_sizes_seen.append(len(pool_customers))
            # テストの関心はpool_customersのサイズだけなので、返すルートは
            # 構造的に妥当であれば内容は問わない（全員1台目に割り当てて返す）。
            if pool_vehicles:
                routes = [_fake_route_multi(pool_vehicles[0]["id"], [c["id"] for c in pool_customers], dist=1.0)]
            else:
                routes = []
            return {"feasible": True, "routes": routes, "unserved_customers": []}

        decomposer = RouteDecomposer(
            ruin_vehicle_count=4, max_iterations=5, iter_time_limit_sec=1,
            total_time_budget_sec=9999.0, stagnation_limit=100, max_pool_customers=12,
        )
        decomposer.solve(
            locations, vehicles, customers, dist_matrix, config={},
            cpo_solve_fn=recording_cpo_solve_fn,
        )
    finally:
        tds._CVRPFallbackEngine = orig_engine

    assert pool_sizes_seen, "cpo_solve_fnが一度も呼ばれていない"
    # 各車両5顧客なので、1台だけなら5（上限12以下）、2台目を足すと10（まだ12以下）、
    # 3台目を足すと15で上限超過のため2台目までで打ち切られるはず＝各反復のプールは
    # 5または10で、12を超えないことを確認する。
    for size in pool_sizes_seen:
        assert size <= 12, f"pool_customersが上限を超えている: {size}"


def test_ruin_always_selects_at_least_one_vehicle_even_if_it_alone_exceeds_cap():
    """
    1台だけで既にmax_pool_customersを超える担当顧客数を持つ場合でも、
    ruinが完全に空（0台選択）にはならないこと。
    """
    vehicles  = [{"id": "v0", "capacity_kg": 1000}]
    customers = [{"id": f"c{i}", "_loc_idx": i + 1} for i in range(20)]
    locations = [{"id": "depot"}] + [{"id": f"loc{i}"} for i in range(20)]
    dist_matrix = [[0.0] * 21 for _ in range(21)]

    class _OneBigVehicle:
        def __init__(self, customers, vehicles, dist_matrix, config):
            self._vehicles = vehicles
            self._customers = customers

        def solve(self):
            routes = [_fake_route_multi("v0", [c["id"] for c in self._customers], dist=10.0)]
            return {
                "feasible": True, "routes": routes,
                "kpi": tds.TruckDispatcherSolver._build_kpi(routes, 10.0),
                "unserved_customers": [],
            }

    orig_engine = tds._CVRPFallbackEngine
    tds._CVRPFallbackEngine = _OneBigVehicle
    try:
        call_count = {"n": 0}

        def cpo_solve_fn(locations, pool_vehicles, pool_customers, dist_matrix, sub_config):
            call_count["n"] += 1
            assert len(pool_vehicles) >= 1, "1台も選ばれずruinが空振りになっている"
            return {"feasible": True, "routes": [], "unserved_customers": [c["id"] for c in pool_customers]}

        decomposer = RouteDecomposer(
            ruin_vehicle_count=4, max_iterations=1, iter_time_limit_sec=1,
            total_time_budget_sec=9999.0, stagnation_limit=100, max_pool_customers=5,
        )
        decomposer.solve(
            locations, vehicles, customers, dist_matrix, config={},
            cpo_solve_fn=cpo_solve_fn,
        )
    finally:
        tds._CVRPFallbackEngine = orig_engine

    assert call_count["n"] == 1


# ---------------------------------------------------------------------------
# 救済枠（2026-07-22追加）: 未割り当て顧客が「idle車両でなければ型式制限上
# 対応できない」場合に、そのidle車両を強制的にRuinプールへ投入する機構。
# 実機で「唯一対応可能な遠方チャーター車が初期解で一度も使われず、iterationを
# いくら回しても永久に検討対象に入らない」不具合が確認され、その再発防止。
# ---------------------------------------------------------------------------

class _FakeFallbackEngineStranded:
    """v0のみを使った初期解を返し、c_strandedを未割当のまま残す。
    v1は初期解で一度もroutesに現れない（idle）ため、救済枠が無ければ
    v1は永久にruin対象に選ばれない。"""

    def __init__(self, customers, vehicles, dist_matrix, config):
        self._vehicles = vehicles

    def solve(self):
        routes = [_fake_route("v0", dist=10.0, customer_id="c0")]
        return {
            "feasible": False,
            "routes": routes,
            "kpi": tds.TruckDispatcherSolver._build_kpi(routes, 10.0),
            "unserved_customers": ["c_stranded"],
        }


def _stranded_fleet():
    vehicles = [
        {"id": "v0", "type": "4t", "capacity_kg": 1000},
        {"id": "v1", "type": "10t", "capacity_kg": 1000},  # idle、c_strandedに対応可能
    ]
    customers = [
        {"id": "c0", "_loc_idx": 1},
        {"id": "c_stranded", "_loc_idx": 2, "restricted_vehicle_types": ["4t"], "demand_kg": 500},
    ]
    locations = [{"id": "depot"}, {"id": "loc1"}, {"id": "loc2"}]
    dist_matrix = [[0.0] * 3 for _ in range(3)]
    return locations, vehicles, customers, dist_matrix


def test_rescue_slot_injects_idle_vehicle_capable_of_stranded_customer():
    """
    唯一対応可能な車両(v1)がidle（初期解で未使用）でも、救済枠経由でRuinプールに
    投入され、結果として未割り当てが解消されることを確認する。
    """
    locations, vehicles, customers, dist_matrix = _stranded_fleet()

    orig_engine = tds._CVRPFallbackEngine
    tds._CVRPFallbackEngine = _FakeFallbackEngineStranded
    try:
        seen_pool_vehicle_ids = []

        def cpo_solve_fn(locations, pool_vehicles, pool_customers, dist_matrix, sub_config):
            pool_vids = {v["id"] for v in pool_vehicles}
            pool_cids = {c["id"] for c in pool_customers}
            seen_pool_vehicle_ids.append(pool_vids)
            # プールに含まれる車両で、対応する顧客がいれば割り当てる
            # （v1がプールに来ればc_strandedを、v0がプールに来ればc0を解決する）。
            routes = []
            if "v1" in pool_vids and "c_stranded" in pool_cids:
                routes.append(_fake_route("v1", dist=5.0, customer_id="c_stranded"))
            if "v0" in pool_vids and "c0" in pool_cids:
                routes.append(_fake_route("v0", dist=1.0, customer_id="c0"))
            served = {r["stops"][0]["customer_id"] for r in routes if r["stops"]}
            unserved = [cid for cid in pool_cids if cid not in served]
            return {"feasible": not unserved, "routes": routes, "unserved_customers": unserved}

        decomposer = RouteDecomposer(
            ruin_vehicle_count=2, max_iterations=3, iter_time_limit_sec=1,
            total_time_budget_sec=9999.0, stagnation_limit=100,
        )
        result = decomposer.solve(
            locations, vehicles, customers, dist_matrix, config={},
            cpo_solve_fn=cpo_solve_fn,
        )
    finally:
        tds._CVRPFallbackEngine = orig_engine

    assert any("v1" in ids for ids in seen_pool_vehicle_ids), "idle車両v1が一度もプールに投入されなかった"
    assert result["unserved_customers"] == [], "救済機構でc_strandedが解決されるはず"


def test_rescue_slot_not_triggered_when_no_unserved_customers():
    """未割り当てが無ければ救済枠は発動せず、従来通りroutes内の車両からのみ選ばれる。"""
    locations, vehicles, customers, dist_matrix = _minimal_fleet()

    seen_pool_vehicle_ids = []

    def cpo_solve_fn(locations, pool_vehicles, pool_customers, dist_matrix, sub_config):
        seen_pool_vehicle_ids.append({v["id"] for v in pool_vehicles})
        routes = [
            _fake_route(v["id"], dist=10.0, customer_id=(pool_customers[i]["id"] if i < len(pool_customers) else None))
            for i, v in enumerate(pool_vehicles)
        ]
        return {"feasible": True, "routes": routes, "unserved_customers": []}

    decomposer = RouteDecomposer(
        ruin_vehicle_count=2, max_iterations=2, iter_time_limit_sec=1,
        total_time_budget_sec=9999.0, stagnation_limit=100,
    )
    decomposer.solve(
        locations, vehicles, customers, dist_matrix, config={},
        cpo_solve_fn=cpo_solve_fn,
    )

    # _FakeFallbackEngine（_minimal_fleet用）はunserved_customers=[]を返すので、
    # 救済枠は一度も発動せず、v2（初期解のroutesに含まれない3台目）は
    # 選ばれないはず（routesは v0, v1 の2台のみ）。
    for ids in seen_pool_vehicle_ids:
        assert ids.issubset({"v0", "v1"}), f"routes外の車両が選ばれている: {ids}"


def test_rescue_slot_respects_ruin_vehicle_count_cap():
    """複数の未割当顧客がそれぞれ別のidle車両でしか対応できない場合でも、
    1反復あたりの救済枠はruin_vehicle_countを超えない。"""
    vehicles = [
        {"id": "v0", "type": "used", "capacity_kg": 1000},
        {"id": "v1", "type": "typeA", "capacity_kg": 1000},
        {"id": "v2", "type": "typeB", "capacity_kg": 1000},
        {"id": "v3", "type": "typeC", "capacity_kg": 1000},
    ]
    customers = [
        {"id": "c0", "_loc_idx": 1},
        {"id": "cA", "_loc_idx": 2, "restricted_vehicle_types": ["used", "typeB", "typeC"], "demand_kg": 100},
        {"id": "cB", "_loc_idx": 3, "restricted_vehicle_types": ["used", "typeA", "typeC"], "demand_kg": 100},
        {"id": "cC", "_loc_idx": 4, "restricted_vehicle_types": ["used", "typeA", "typeB"], "demand_kg": 100},
    ]
    locations = [{"id": "depot"}] + [{"id": f"loc{i}"} for i in range(4)]
    dist_matrix = [[0.0] * 5 for _ in range(5)]

    class _FakeFallbackEngineThreeStranded:
        def __init__(self, customers, vehicles, dist_matrix, config):
            pass

        def solve(self):
            routes = [_fake_route("v0", dist=10.0, customer_id="c0")]
            return {
                "feasible": False,
                "routes": routes,
                "kpi": tds.TruckDispatcherSolver._build_kpi(routes, 10.0),
                "unserved_customers": ["cA", "cB", "cC"],
            }

    orig_engine = tds._CVRPFallbackEngine
    tds._CVRPFallbackEngine = _FakeFallbackEngineThreeStranded
    try:
        pool_sizes = []

        def cpo_solve_fn(locations, pool_vehicles, pool_customers, dist_matrix, sub_config):
            pool_sizes.append(len(pool_vehicles))
            return {"feasible": False, "routes": [], "unserved_customers": [c["id"] for c in pool_customers]}

        decomposer = RouteDecomposer(
            ruin_vehicle_count=2, max_iterations=2, iter_time_limit_sec=1,
            total_time_budget_sec=9999.0, stagnation_limit=100,
        )
        decomposer.solve(
            locations, vehicles, customers, dist_matrix, config={},
            cpo_solve_fn=cpo_solve_fn,
        )
    finally:
        tds._CVRPFallbackEngine = orig_engine

    assert pool_sizes, "cpo_solve_fnが一度も呼ばれていない"
    for size in pool_sizes:
        assert size <= 2, f"ruin_vehicle_count(2)を超えて車両が選ばれている: {size}"


def test_rescue_slot_does_not_inject_idle_vehicle_that_fails_duty_check():
    """
    型式・積載量は満たすが max_duty_min が全く足りない idle 車両は、
    2026-07-22の拘束時間考慮追加により救済枠から除外されること
    （追加前は型式制限＋積載量のみで判定していたため誤って投入されていた。
    実機で c12（往復19時間超が必要）の救済枠が、拘束時間が全く足りない
    別のidle車両に奪われ続ける不具合として確認された）。
    """
    vehicles = [
        {"id": "v_used", "type": "X", "capacity_kg": 1000, "max_duty_min": 100},
        {"id": "v_short", "type": "X", "capacity_kg": 1000, "max_duty_min": 100},  # idle、拘束時間不足
    ]
    customers = [
        {"id": "c0", "_loc_idx": 1},
        {"id": "c_far", "_loc_idx": 2, "demand_kg": 100},  # depotから1000km、往復で1500分超必要
    ]
    locations = [{"id": "depot"}, {"id": "c0"}, {"id": "c_far"}]
    dist_matrix = [
        [0.0, 10.0, 1000.0],
        [10.0, 0.0, 990.0],
        [1000.0, 990.0, 0.0],
    ]

    class _FakeFallbackEngineDutyMismatch:
        def __init__(self, customers, vehicles, dist_matrix, config):
            pass

        def solve(self):
            routes = [_fake_route("v_used", dist=10.0, customer_id="c0")]
            return {
                "feasible": False,
                "routes": routes,
                "kpi": tds.TruckDispatcherSolver._build_kpi(routes, 10.0),
                "unserved_customers": ["c_far"],
            }

    orig_engine = tds._CVRPFallbackEngine
    tds._CVRPFallbackEngine = _FakeFallbackEngineDutyMismatch
    try:
        seen_pool_vehicle_ids = []

        def cpo_solve_fn(locations, pool_vehicles, pool_customers, dist_matrix, sub_config):
            seen_pool_vehicle_ids.append({v["id"] for v in pool_vehicles})
            return {"feasible": False, "routes": [], "unserved_customers": [c["id"] for c in pool_customers]}

        decomposer = RouteDecomposer(
            ruin_vehicle_count=2, max_iterations=3, iter_time_limit_sec=1,
            total_time_budget_sec=9999.0, stagnation_limit=100,
        )
        decomposer.solve(
            locations, vehicles, customers, dist_matrix, config={},
            cpo_solve_fn=cpo_solve_fn,
        )
    finally:
        tds._CVRPFallbackEngine = orig_engine

    for ids in seen_pool_vehicle_ids:
        assert "v_short" not in ids, "拘束時間を満たさないidle車両が救済枠に投入されている"


def test_rescue_slot_prioritizes_most_constrained_customer_first():
    """
    2台のidle車両（v_versatile: どちらの顧客にも対応可、v_specialist: c_rareにしか
    対応できない）と2件の未割当顧客（c_common: v_versatile/v_specialist両方で対応可、
    c_rare: v_specialistでしか対応できない）がある場合、対応可能候補が少ない
    c_rare が優先されv_specialistを確保できること（先にc_commonが処理されて
    v_specialistを含む候補を掴んでしまい、c_rareが救えなくなる逆転を防ぐ）。
    """
    vehicles = [
        {"id": "v_used", "type": "X", "capacity_kg": 1000, "max_duty_min": 9999},
        {"id": "v_versatile", "type": "X", "capacity_kg": 1000, "max_duty_min": 9999},  # idle
        {"id": "v_specialist", "type": "Y", "capacity_kg": 1000, "max_duty_min": 9999},  # idle
    ]
    customers = [
        {"id": "c0", "_loc_idx": 1},
        # c_common: type制限なし（X, Yどちらでも可）＝v_versatile, v_specialist両方が候補
        {"id": "c_common", "_loc_idx": 2, "demand_kg": 100},
        # c_rare: type Xを禁止＝v_specialist(Y)しか候補にならない
        {"id": "c_rare", "_loc_idx": 3, "demand_kg": 100, "restricted_vehicle_types": ["X"]},
    ]
    locations = [{"id": "depot"}, {"id": "c0"}, {"id": "c_common"}, {"id": "c_rare"}]
    dist_matrix = [[0.0] * 4 for _ in range(4)]

    class _FakeFallbackEngineTwoUnserved:
        def __init__(self, customers, vehicles, dist_matrix, config):
            pass

        def solve(self):
            routes = [_fake_route("v_used", dist=1.0, customer_id="c0")]
            return {
                "feasible": False,
                "routes": routes,
                "kpi": tds.TruckDispatcherSolver._build_kpi(routes, 1.0),
                "unserved_customers": ["c_common", "c_rare"],
            }

    orig_engine = tds._CVRPFallbackEngine
    tds._CVRPFallbackEngine = _FakeFallbackEngineTwoUnserved
    try:
        seen_pool_vehicle_ids = []

        def cpo_solve_fn(locations, pool_vehicles, pool_customers, dist_matrix, sub_config):
            seen_pool_vehicle_ids.append({v["id"] for v in pool_vehicles})
            return {"feasible": False, "routes": [], "unserved_customers": [c["id"] for c in pool_customers]}

        # ruin_vehicle_count=1にして「どちらか一方しか救済枠に入らない」状況を作り、
        # 優先順位ロジックの効果を明確にする。
        decomposer = RouteDecomposer(
            ruin_vehicle_count=1, max_iterations=1, iter_time_limit_sec=1,
            total_time_budget_sec=9999.0, stagnation_limit=100,
        )
        decomposer.solve(
            locations, vehicles, customers, dist_matrix, config={},
            cpo_solve_fn=cpo_solve_fn,
        )
    finally:
        tds._CVRPFallbackEngine = orig_engine

    assert seen_pool_vehicle_ids, "cpo_solve_fnが一度も呼ばれていない"
    # c_rareの方が候補が少ない(1台)ので優先され、v_specialistが救済枠に入るはず。
    assert seen_pool_vehicle_ids[0] == {"v_specialist"}, (
        f"候補が少ないc_rare用のv_specialistが優先されていない: {seen_pool_vehicle_ids[0]}"
    )
