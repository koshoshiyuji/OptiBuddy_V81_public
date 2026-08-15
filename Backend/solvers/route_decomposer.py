"""
Backend/solvers/route_decomposer.py

RouteDecomposer — TruckDispatcher（CVRP-TW）専用の LNS（Large Neighborhood
Search / Ruin-and-Recreate）実装。

【背景】
  CP Optimizer（IBM CPLEX、評価版/Community Edition）には探索空間の上限
  （2^1000）があり、TruckDispatcherの実データ規模（顧客40〜50件・車両30台）
  では "Problem size limit exceeded" エラーで解が返らないことが実際に確認された。

  既存の Decomposer（HorizonDecomposer / SpatialDecomposer, decomposer.py）は
  YardPlanning 専用の tasks[]/constraints[] 構造を前提としており、CVRP
  （customers[]/vehicles[]/dist_matrix構造）にはそのまま適用できない
  （当時はEventStaffing専用のStaffDecomposerも同様の理由で個別実装されて
  いたが、EventStaffingドメイン自体が2026-07-11に削除されたため2026-07-27に
  StaffDecomposerごと削除済み）。同じ理由で、本ファイルを新設する。

【採用した設計（Koshoshiとの相談・承認済み）】
  1. 初期解: 既存の _CVRPFallbackEngine（truck_dispatcher_solver.py の
     貪欲法。docplex不在時のフォールバックとして既に実装済みのもの）を
     再利用し、高速に（品質は保証しないが）実行可能に近い叩き台を作る。
     一部未割り当てが残っても問題ない（Ruinプールに含めて扱う）。
  2. Ruin単位: 個々の顧客ではなく「車両ルート単位」。数台の車両を選び、
     その車両が担当していた顧客 + 現時点で未割り当ての顧客をプールにする。
     プールと選んだ車両群だけで CPO を解けば、探索空間が小さく保たれ、
     Community Edition の上限内に収まる。
  3. Recreate: 既存の TruckDispatcherSolver._solve_cpo() をそのまま
     再利用する（顧客・車両のリストを絞って渡すだけ。距離行列・
     ロケーション一覧は元のものをそのまま使う。_loc_idx は絶対インデックス
     なので部分集合でも整合する）。
  4. 切り替えタイミング: 顧客数などの事前閾値ではなく、CP Optimizerが
     実際に "Problem size limit exceeded" を返した場合にのみ発動する
     （既存の HorizonDecomposer.solve_sequentially() と同じ検知パターン）。
"""

from __future__ import annotations

import logging
import random
import time
from typing import Any, Callable

logger = logging.getLogger(__name__)


class RouteDecomposer:
    """
    TruckDispatcher専用 LNS（Ruin-and-Recreate）。

    solve() の呼び出し元（TruckDispatcherSolver.solve()）は、
    cpo_solve_fn として TruckDispatcherSolver._solve_cpo をそのまま渡す。
    これにより Recreate フェーズで CPO のモデル構築ロジックを重複実装しない。
    """

    def __init__(
        self,
        ruin_vehicle_count:     int = 4,
        max_iterations:         int = 25,
        iter_time_limit_sec:    int = 3,
        total_time_budget_sec:  float = 120.0,
        random_seed:            int = 42,
        stagnation_limit:       int = 8,
        max_pool_customers:     int = 12,
    ):
        self.ruin_vehicle_count    = ruin_vehicle_count
        self.max_iterations        = max_iterations
        self.iter_time_limit_sec   = iter_time_limit_sec
        self.total_time_budget_sec = total_time_budget_sec
        # 2026-07-18追加: 「iterationが無駄に回ってタイムアウト待ちになっている」という
        # 指摘への対応。従来はmax_iterations（既定25）かtotal_time_budget_sec（既定120秒）に
        # 達するまで、改善が全く無くても必ず全反復を消化していた。連続でstagnation_limit回
        # 改善が無ければ、それ以上続けても改善する見込みが薄いとみなして早期終了する
        # （最良解の質は変えない。改善が続く限りは従来通り最大まで回る）。
        self.stagnation_limit = stagnation_limit
        # 2026-07-18e追加: 実機ログで、Ruinで選んだ車両群の顧客数がたまたま多いと
        # RecreateのCPOモデルがCommunity Editionのサイズ上限（2^1000）に引っかかり
        # 「Problem size limit exceeded」で丸ごとスキップされる反復が1回の実行で
        # 20件以上発生していた。ruin_vehicle_count（台数固定）だけでは、選ばれた
        # 車両の担当顧客数が多い場合にプールが肥大化するのを防げない。ここでは
        # Ruinで車両を1台ずつ追加していき、プールの顧客数がmax_pool_customersを
        # 超える手前で止める（ただし最低1台は必ず選ぶ）ことで、失敗が見込まれる
        # 反復を事前に減らす。
        self.max_pool_customers = max_pool_customers
        self._rng = random.Random(random_seed)

    def solve(
        self,
        locations:    list[dict],
        vehicles:     list[dict],
        customers:    list[dict],
        dist_matrix:  list[list[float]],
        config:       dict,
        cpo_solve_fn: Callable[[list, list, list, list, dict], dict],
    ) -> dict:
        """
        戻り値は TruckDispatcherSolver._solve_cpo() と同じ形式
        {"feasible", "routes", "kpi", "unserved_customers"} に、
        LNSが使われたことを示す "_lns_used": True を加えたもの。
        """
        t_start = time.time()

        # ------------------------------------------------------------
        # Step 1: 初期解（貪欲法）。品質は保証しない。一部未割り当てでも可。
        # ------------------------------------------------------------
        from solvers.truck_dispatcher_solver import _CVRPFallbackEngine, TruckDispatcherSolver

        initial_engine = _CVRPFallbackEngine(
            customers=customers, vehicles=vehicles, dist_matrix=dist_matrix, config=config)
        best = initial_engine.solve()
        logger.info(
            f"[RouteDecomposer] 初期解（貪欲法）: "
            f"vehicles_used={best['kpi'].get('n_vehicles_used', 0)}, "
            f"unserved={len(best['unserved_customers'])}"
        )

        if not best["routes"]:
            logger.warning("[RouteDecomposer] 初期解が空（貪欲法でも1台も割り当てられず）。LNSを実行せず終了。")
            best["_lns_used"] = True
            return best

        customers_by_id = {c["id"]: c for c in customers}
        vehicles_by_id  = {v["id"]: v for v in vehicles}

        # ------------------------------------------------------------
        # 救済枠で使う「型式制限だけでなく拘束時間(max_duty_min)も満たすか」の
        # 判定は truck_dispatcher_solver.py の _classify_unserved() と全く同じ式
        # （depot出発固定・直行を仮定した理論上の最小拘束時間）を用いる。
        # （2026-07-22追加: 型式制限のみで判定すると、対応可能候補が実質1台しか
        # ない顧客（遠方チャーター等）が、対応可能候補が多い他の未割当顧客に
        # 先に idle 車両を取られてしまい、救済枠が発動しても本来救うべき顧客に
        # 届かない不具合が実機で確認された）
        # ------------------------------------------------------------
        loc_id_to_idx = {loc["id"]: idx for idx, loc in enumerate(locations)}
        _avg_spd     = float(config.get("avg_speed_kmh", 30.0))
        _highway_spd = float(config.get("highway_speed_kmh", 80.0))
        _highway_thr = float(config.get("highway_threshold_km", 50.0))
        _depot_open  = int(config.get("depot_open_min", 360))

        def _travel_min(dist_km: float) -> float:
            if dist_km > _highway_thr and _highway_spd > 0:
                return (dist_km / _highway_spd) * 60.0
            return (dist_km / _avg_spd) * 60.0 if _avg_spd > 0 else 0.0

        def _min_duty_for_customer(c: dict) -> float | None:
            loc_idx = loc_id_to_idx.get(c["id"])
            if loc_idx is None:
                return None
            dist_km = dist_matrix[0][loc_idx]
            one_way = _travel_min(dist_km)
            svc = c.get("service_time_min", 15)
            tw_open_min = c.get("tw_open_min", _depot_open)
            arrival_direct = _depot_open + one_way
            start_service = max(arrival_direct, tw_open_min)
            finish_service = start_service + svc
            return (finish_service + one_way) - _depot_open

        def _feasible_vehicle_ids_for(c: dict, pool: list[dict]) -> list[str]:
            """型式制限・積載量・拘束時間の全てを満たす車両IDを返す（idle/使用中を問わない）。"""
            restricted = set(c.get("restricted_vehicle_types", []))
            demand = c.get("demand_kg", 0)
            min_duty = _min_duty_for_customer(c)
            out = []
            for v in pool:
                if v.get("type", "") in restricted:
                    continue
                if v.get("capacity_kg", 0) < demand:
                    continue
                if min_duty is not None and min_duty > v.get("max_duty_min", 9999):
                    continue
                out.append(v["id"])
            return out

        iterations_done = 0
        stagnant_count = 0
        for it in range(self.max_iterations):
            if time.time() - t_start > self.total_time_budget_sec:
                logger.info(f"[RouteDecomposer] \u6642\u9593\u4e88\u7b97\u5230\u9054 (iteration={it})")
                break
            if stagnant_count >= self.stagnation_limit:
                logger.info(
                    f"[RouteDecomposer] {self.stagnation_limit}\u56de\u9023\u7d9a\u3067\u6539\u5584\u306a\u3057\u306e\u305f\u3081\u65e9\u671f\u7d42\u4e86 "
                    f"(iteration={it}, \u6b8b\u308a\u4e88\u7b97={self.total_time_budget_sec - (time.time() - t_start):.1f}\u79d2\u3092\u7bc0\u7d04)"
                )
                break
            iterations_done = it + 1

            routes = best["routes"]

            # ------------------------------------------------------------
            # Step 2a: 救済枠 — 未割り当て顧客の中に「現在idle（=routesに
            #   一台も現れない）車両でなければ（型式制限・積載量・拘束時間の
            #   いずれかの理由で）そもそも対応できない」ものがあれば、その
            #   idle車両を強制的にプールへ加える。
            #   （2026-07-22追加: Ruin対象がroutes内＝使用中の車両からしか
            #   選ばれない設計だったため、唯一対応可能な車両が初期解で一度も
            #   使われていない場合、iterationをいくら回しても永久にその車両が
            #   検討対象に入らず、当該顧客が救えない不具合が実機で確認された。
            #   例: 遠方チャーター専用車のような「対応可能候補が事実上1台しか
            #   ない」ケース。
            #   2026-07-22追記: 型式制限のみで判定すると、対応可能候補が多い
            #   顧客（例: 8台どれでもよい）が先にidle車両を確保してしまい、
            #   本当に候補が少ない顧客（例: 候補1台のみ）に順番が回ってこない
            #   不具合も実機で確認された。対応可能候補数が少ない顧客から順に
            #   救済枠を割り当て、判定自体も型式制限だけでなく積載量・拘束時間
            #   （_classify_unserved()と同じ最小拘束時間の式）まで含めて行う）
            # ------------------------------------------------------------
            unserved_before = set(best["unserved_customers"])
            used_vehicle_ids = {r["vehicle_id"] for r in routes}
            rescue_vehicle_ids: list[str] = []
            if unserved_before:
                idle_vehicles = [v for v in vehicles if v["id"] not in used_vehicle_ids]
                idle_ids = {v["id"] for v in idle_vehicles}

                # 対応可能候補（型式・積載量・拘束時間の全条件）が少ない顧客ほど
                # 「idle車両を逃すと二度と救えない」ため優先度を上げる。
                # 候補数が同じ場合はcid順で決定的にする（テスト・ログの再現性のため）。
                unserved_list = sorted(unserved_before)
                priority: list[tuple[int, str, dict]] = []
                for cid in unserved_list:
                    c = customers_by_id.get(cid)
                    if not c:
                        continue
                    feasible_ids = _feasible_vehicle_ids_for(c, vehicles)
                    priority.append((len(feasible_ids), cid, c))
                priority.sort(key=lambda t: (t[0], t[1]))

                seen: set[str] = set()
                for _n_feasible, cid, c in priority:
                    feasible_idle_ids = [
                        vid for vid in _feasible_vehicle_ids_for(c, idle_vehicles)
                        if vid in idle_ids and vid not in seen
                    ]
                    if feasible_idle_ids:
                        chosen_id = feasible_idle_ids[0]
                        rescue_vehicle_ids.append(chosen_id)
                        seen.add(chosen_id)
                    if len(rescue_vehicle_ids) >= self.ruin_vehicle_count:
                        break

            # ------------------------------------------------------------
            # Step 2b: Ruin — 車両ルートを数台選ぶ（プールの顧客数が
            # max_pool_customersを超えない範囲で、1台ずつ追加していく）。
            # 救済枠で確保した分だけ、ランダム選択の枠を減らす。
            # ------------------------------------------------------------
            pool_customer_ids: set[str] = set(best["unserved_customers"])
            ruined_indices: list[int] = []
            random_slot_budget = max(0, self.ruin_vehicle_count - len(rescue_vehicle_ids))
            if len(routes) > 0 and random_slot_budget > 0:
                candidate_indices = list(range(len(routes)))
                self._rng.shuffle(candidate_indices)
                for idx in candidate_indices:
                    if len(ruined_indices) >= random_slot_budget:
                        break
                    route_customer_ids = {s["customer_id"] for s in routes[idx]["stops"]}
                    would_be_pool_size = len(pool_customer_ids | route_customer_ids)
                    # 既に1台以上選択済みで、これ以上追加するとプールが上限を超えるなら打ち切る。
                    # 最低1台は必ず選ぶ（それだけで上限を超える場合でも、ruinが完全な
                    # 空振りになるよりは1台だけで試す方がまし）。
                    if ruined_indices and would_be_pool_size > self.max_pool_customers:
                        break
                    ruined_indices.append(idx)
                    pool_customer_ids |= route_customer_ids

            if not ruined_indices and not rescue_vehicle_ids:
                break

            ruined_vehicle_ids = {routes[i]["vehicle_id"] for i in ruined_indices}

            pool_customers = [customers_by_id[cid] for cid in pool_customer_ids if cid in customers_by_id]
            pool_vehicles  = [vehicles_by_id[vid] for vid in ruined_vehicle_ids if vid in vehicles_by_id]
            pool_vehicles += [vehicles_by_id[vid] for vid in rescue_vehicle_ids if vid in vehicles_by_id]

            if rescue_vehicle_ids:
                logger.info(
                    f"[RouteDecomposer] iteration={it}: 救済枠でidle車両を投入 "
                    f"({rescue_vehicle_ids})"
                )

            if not pool_customers or not pool_vehicles:
                stagnant_count += 1
                continue

            # ------------------------------------------------------------
            # Step 3: Recreate — 既存の _solve_cpo を小規模プールで再利用
            # ------------------------------------------------------------
            sub_config = dict(config)
            sub_config["solver_time_limit_sec"] = self.iter_time_limit_sec

            try:
                recreated = cpo_solve_fn(locations, pool_vehicles, pool_customers, dist_matrix, sub_config)
            except Exception as e:
                logger.warning(f"[RouteDecomposer] iteration={it} Recreate\u5931\u6557\uff08\u3053\u306e\u53cd\u5fa9\u306f\u30b9\u30ad\u30c3\u30d7\uff09: {e}")
                stagnant_count += 1
                continue

            # ------------------------------------------------------------
            # Step 4: マージ — Ruinされなかったルートはそのまま、
            #   Ruinされた分だけ Recreate結果に差し替える
            # ------------------------------------------------------------
            kept_routes = [r for i, r in enumerate(routes) if i not in ruined_indices]
            new_routes  = kept_routes + recreated["routes"]

            served_in_pool = {s["customer_id"] for r in recreated["routes"] for s in r["stops"]}
            new_unserved = [cid for cid in pool_customer_ids if cid not in served_in_pool]

            total_dist = sum(r["route_dist_km"] for r in new_routes)
            new_kpi = TruckDispatcherSolver._build_kpi(new_routes, total_dist)

            if self._is_better(new_routes, routes, vehicles_by_id, new_kpi, best["kpi"],
                                len(new_unserved), len(best["unserved_customers"])):
                best = {
                    "feasible": len(new_unserved) == 0,
                    "routes": new_routes,
                    "kpi": new_kpi,
                    "unserved_customers": new_unserved,
                }
                stagnant_count = 0
                logger.info(
                    f"[RouteDecomposer] iteration={it}: \u6539\u5584 "
                    f"(unserved={len(new_unserved)}, vehicles_used={new_kpi['n_vehicles_used']}, "
                    f"dist={new_kpi['total_dist_km']}km)"
                )
            else:
                stagnant_count += 1

        elapsed = time.time() - t_start
        logger.info(
            f"[RouteDecomposer] \u5b8c\u4e86: iterations={iterations_done}, elapsed={elapsed:.1f}s, "
            f"final_unserved={len(best['unserved_customers'])}, "
            f"vehicles_used={best['kpi'].get('n_vehicles_used', 0)}"
        )

        best["_lns_used"] = True
        return best

    @staticmethod
    def _violation_score(routes: list[dict], vehicles_by_id: dict) -> float:
        """
        修正（2026-07-08, Koshoshiとの相談④）: TW超過(遅刻)・拘束時間超過(max_duty_min超過)を
        分単位でスコア化する。0なら制約違反なし。_is_better() がこれを無視していたため、
        「距離は縮んだが実は拘束時間が数時間超過している」ルートを"改善"と誤判定していた。
        """
        score = 0.0
        for r in routes:
            v = vehicles_by_id.get(r.get("vehicle_id"), {})
            max_duty = v.get("max_duty_min", 9999)
            duty_over = r.get("duty_min", 0) - max_duty
            if duty_over > 0:
                score += duty_over
            for s in r.get("stops", []):
                tw_over = s.get("arrival_min", 0) - s.get("tw_close_min", 9999)
                if tw_over > 0:
                    score += tw_over
        return score

    @classmethod
    def _is_better(cls, new_routes: list[dict], old_routes: list[dict], vehicles_by_id: dict,
                    new_kpi: dict, old_kpi: dict, new_unserved_count: int, old_unserved_count: int) -> bool:
        """
        比較順序: 未割り当て件数 → 制約違反スコア(TW超過+拘束時間超過) → 使用車両数 → 総走行距離。
        制約違反スコアを使用車両数・距離より先に見ることで、「見た目の距離/台数は良いが
        実際には拘束時間やTWを大幅に破っている」解を改善として採用しないようにする。
        """
        if new_unserved_count != old_unserved_count:
            return new_unserved_count < old_unserved_count

        new_violation = cls._violation_score(new_routes, vehicles_by_id)
        old_violation = cls._violation_score(old_routes, vehicles_by_id)
        if new_violation != old_violation:
            return new_violation < old_violation

        if new_kpi.get("n_vehicles_used", 0) != old_kpi.get("n_vehicles_used", 0):
            return new_kpi.get("n_vehicles_used", 0) < old_kpi.get("n_vehicles_used", 0)
        return new_kpi.get("total_dist_km", 0) < old_kpi.get("total_dist_km", 0)
