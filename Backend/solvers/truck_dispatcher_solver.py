"""
Backend/solvers/truck_dispatcher_solver.py  (v4.0 — CP Optimizer 化)

TruckDispatcherSolver — 配送ルート最適化ソルバー（CVRP-TW, CP Optimizer実装）

【問題クラス】
  Capacitated Vehicle Routing Problem with Time Windows (CVRP-TW)
  — 容量制約・タイムウィンドウ付き車両ルーティング問題

【設計方針】
  OptiBuddyの公式アーキテクチャ（INSTALL.md: 最適化エンジン = IBM CPLEX CP
  Optimizer / docplex経由）に準拠する。以前のバージョン（v3.0）は
  Clark-Wright Savings法 + 2-opt という純粋なPythonヒューリスティックのみで
  実装されており、この標準から逸脱していた。本バージョンで是正する。

  ProductionLotScheduler（RCPSP系）のコードをテンプレートとして流用せず、
  CVRP固有の意味づけ（デポ発着・移動時間行列によるルーティング構造）で
  ネイティブに設計している。

【モデル定式化】
  決定変数:
    visit[v][c]   : optional interval_var — 車両 v が顧客 c を訪問するか（訪問するなら何分に）
    depot_out[v]  : optional interval_var — 車両 v のデポ出発（size=0, 固定時刻）
    depot_in[v]   : optional interval_var — 車両 v のデポ帰着（size=0, depot_close_min以内）
    seq[v]        : sequence_var — 車両 v の訪問順序（depot_out/depot_inを含む）

  制約:
    1. 各顧客はちょうど1台の車両に割り当てられる（presence_of の合計 == 1）
    2. 車両ごとの訪問順序は重複しない（sequence_var + no_overlap + 移動時間行列）
    3. デポ出発は必ず最初、デポ帰着は必ず最後（mdl.first / mdl.last）
    4. 積載量制約（割り当てられた需要合計 <= 車両容量）
    5. 車格制限（restricted_vehicle_types）: 対象外の車両では interval_var 自体を作らない
    6. 積み付け制約: 段積み不可貨物は同一車両に2件以上同時に割り当てない
    7. デポ帰着は depot_close_min 以内（ハード制約）
    8. 拘束時間上限（max_duty_min）

  目的関数:
    最小化: 使用車両数×重み + 総拘束時間×重み + タイムウィンドウ超過ペナルティ

  既知の簡略化（今後の拡張課題）:
    - 連続運転時間上限（max_continuous_drive_min）は本バージョンでは未実装
      （休憩ウィンドウのモデル化が必要なため）

【2026-08-13追記: CP-SATエンジン対応】
  他の16ドメインと同様に config.solver_engine（既定は engine_select.py の
  ポリシーに従い "cpsat"）でCPO(docplex.cp)/CP-SAT(CPMpy、_solve_cpsat)を
  明示的に切り替えられるようになった。CP-SATは2段階分解（Stage1: 割当を
  集合分割問題として解く、Stage2: 車両ごとの巡回順序+タイミングを
  cp.Circuit+含意制約で厳密に解く）でCPOのjoint最適化を近似する。
  solver_engine="cpo"が指定されたのにdocplexが無い場合、または
  solver_engine="cpsat"でCP-SAT自体が例外を投げた場合は、それぞれ明示的な
  エラー/貪欲法（Savings法ではなく単純First-Fit、v3.0由来）フォールバックに
  なる。詳細は docs/DESIGN_2026-08-12_cp_sat_backend_support.md 参照。
"""

from __future__ import annotations

import logging
import time
from typing import Any, Optional

from solvers.base.solver_error_result import build_solver_crash_issue, solver_crash_extra_fields

logger = logging.getLogger(__name__)

try:
    from docplex.cp.model import CpoModel
    from docplex.cp.modeler import build_cpo_transition_matrix
    _CPO_AVAILABLE = True
except ImportError:
    _CPO_AVAILABLE = False
    logger.warning("[TruckDispatcher] docplex が未インストール。フォールバックソルバー（ヒューリスティック）を使用します。")


class _CpoSizeLimitError(Exception):
    """
    CP Optimizer（Community Edition/評価版）の探索空間上限（2^1000）を
    超えた場合に専用で発生させる例外。これを検知した場合のみ、
    solve() が RouteDecomposer（LNS）にフォールバックする。
    """
    pass


# ---------------------------------------------------------------------------
# メインソルバークラス（入出力契約は v3.0 と同一。中身のみCPO化）
# ---------------------------------------------------------------------------

class TruckDispatcherSolver:

    def __init__(self, solver_input: dict) -> None:
        self.dsl = solver_input

    def solve(self) -> dict:
        dsl       = self.dsl
        locations = dsl.get("locations", [])
        vehicles  = dsl.get("vehicles", [])
        customers = dsl.get("customers", [])
        config    = dsl.get("config", {})
        meta      = dsl.get("meta", {})
        dist_matrix = dsl.get("dist_matrix", [])

        if not locations:
            return self._error("locations フィールドが空です（truck_dispatcher_converter を経由していない可能性があります）")
        if not vehicles:
            return self._error("vehicles フィールドが空です")
        if not customers:
            return self._error("customers フィールドが空です")
        if "_loc_idx" not in customers[0]:
            return self._error("customers に _loc_idx がありません（truck_dispatcher_converter.py を経由していません）")
        if not dist_matrix:
            return self._error("dist_matrix が空です")

        t0 = time.time()

        # 2026-08-13修正: 他ドメインと統一し、docplexの利用可否による自動判定
        # ではなく config.solver_engine（既定はengine_select.pyのポリシーに従い
        # "cpsat"）でCPO/CP-SATを明示的に切り替える。これにより「docplexが
        # インストールされている環境でも、明示的にcpsatを選べる／既定でcpsatが
        # 使われる」という他の16ドメインと同じ挙動になる（以前は本ドメインだけ
        # docplexさえ入っていれば常にCPOが最優先という別ポリシーだった）。
        from solvers.base.engine_select import get_solver_engine, CPO, CPSAT
        try:
            engine = get_solver_engine(config)
        except Exception as e:
            logger.error(f"[TruckDispatcher] solver_engine解決エラー: {e}", exc_info=True)
            result = {
                "status": "ok", "feasible": False, "metadata": meta, "solutions": [],
                "issues": [build_solver_crash_issue(e)],
                "_solver_version": "truck_dispatcher_v4.0_cpo",
            }
            result.update(solver_crash_extra_fields(e))
            return result

        if engine == CPO:
            if not _CPO_AVAILABLE:
                # 他ドメイン（medical_appointment_scheduler等）と同じ扱い:
                # cpoが明示要求されたのにdocplexが無い場合は、cpsatへ黙って
                # 切り替えたりせず、はっきりエラーとして返す。
                err = ImportError("docplex がインストールされていないため solver_engine='cpo' を実行できません")
                logger.error(f"[TruckDispatcher] {err}")
                result = {
                    "status": "ok", "feasible": False, "metadata": meta, "solutions": [],
                    "issues": [build_solver_crash_issue(err)],
                    "_solver_version": "truck_dispatcher_v4.0_cpo",
                }
                result.update(solver_crash_extra_fields(err))
                return result
            try:
                result = self._solve_cpo(locations, vehicles, customers, dist_matrix, config)
                result["_engine_used"] = "docplex_cpo"
            except _CpoSizeLimitError as e:
                logger.warning(f"[TruckDispatcher] CP Optimizerライセンス上限を検知: {e}。"
                               f"RouteDecomposer（LNS: Ruin-and-Recreate）にフォールバックします。")
                from solvers.route_decomposer import RouteDecomposer
                # LNSのパラメータ（1 iterationあたりのCPO時間・全体予算・ruin台数・最大反復数）を
                # ハードコードから config 経由で上書き可能にする（2026-07-08、Koshoshiとの相談②）。
                # config未指定の場合は従来通りのデフォルト値のまま動く。
                decomposer = RouteDecomposer(
                    ruin_vehicle_count=int(config.get("lns_ruin_vehicle_count", 4)),
                    max_iterations=int(config.get("lns_max_iterations", 25)),
                    iter_time_limit_sec=int(config.get("lns_iter_time_limit_sec", 3)),
                    total_time_budget_sec=float(config.get("lns_total_time_budget_sec", 120.0)),
                    # 2026-07-18追加: 改善が続く限りは連続回数にかかわらず回り続けるが、
                    # stagnation_limit回連続で改善が無ければ「これ以上回しても無駄」と
                    # みなして早期終了する（「iterationが無駄に回っている」への対応）。
                    stagnation_limit=int(config.get("lns_stagnation_limit", 8)),
                    # 2026-07-18e追加: Ruinで選ぶ車両群の担当顧客数がCommunity Edition
                    # のサイズ上限に頻繁に抵触していたため、プール顧客数の上限を追加。
                    max_pool_customers=int(config.get("lns_max_pool_customers", 12)),
                )
                result = decomposer.solve(locations, vehicles, customers, dist_matrix, config,
                                           cpo_solve_fn=self._solve_cpo)
            except Exception as e:
                # [2026-07-28] _solve_cpo が size-limit 以外の想定外例外を再raiseした場合。
                # solvers/base/solver_error_result.py 参照（crashと業務上infeasibleの区別）。
                logger.error(f"[TruckDispatcher] solve() 例外: {e}", exc_info=True)
                result = {
                    "status": "ok", "feasible": False, "metadata": meta, "solutions": [],
                    "issues": [build_solver_crash_issue(e)],
                    "_solver_version": "truck_dispatcher_v4.0_cpo",
                }
                result.update(solver_crash_extra_fields(e))
                return result
        else:
            # engine == CPSAT（config未指定時の既定でもここに来る）。
            # CP-SAT(CPMpy)による2段階分解ソルバー（Stage1: 割当、Stage2: 車両
            # ごとの巡回順序+タイミングをCircuit+含意制約で厳密に解く）を使う。
            # CE-limitはCPLEX固有のライセンス制約なのでCP-SATには存在しない。
            # CP-SAT自体が想定外の例外を投げた場合のみ、最終手段として貪欲法
            # （Savings+2-opt）にフォールバックする。
            try:
                result = self._solve_cpsat(locations, vehicles, customers, dist_matrix, config)
                result["_engine_used"] = "cpsat"
            except Exception as e:
                logger.error(
                    f"[TruckDispatcher] CP-SAT例外: {e}。"
                    f"貪欲法（Savings+2-opt）フォールバックに切り替えます。", exc_info=True
                )
                result = self._solve_fallback_heuristic(locations, vehicles, customers, dist_matrix, config)
                result["_engine_used"] = "savings_2opt_fallback"

        elapsed = round(time.time() - t0, 2)
        routes   = result["routes"]
        kpi      = result["kpi"]
        unserved = result["unserved_customers"]
        feasible = result["feasible"]

        summary = self._build_summary(routes, kpi, meta, vehicles, locations[0])
        unserved_reasons = self._classify_unserved(unserved, customers, vehicles, dist_matrix, locations, config)
        issues  = self._build_issues(routes, unserved, feasible, config, unserved_reasons, vehicles,
                                     customers=customers, dist_matrix=dist_matrix)

        engine_used = result.get(
            "_engine_used",
            "lns_ruin_recreate" if result.get("_lns_used") else "unknown",
        )
        label_by_engine = {
            "docplex_cpo":          "CP Optimizer 解",
            "lns_ruin_recreate":    "LNS(Ruin-and-Recreate)解（CPOライセンス上限のため自動切り替え）",
            "cpsat":                "CP-SAT 解",
            "savings_2opt_fallback": "貪欲法フォールバック解（CP-SATが例外を投げたため）",
        }

        solution = {
            "name":          "Plan A",
            "label":         label_by_engine.get(engine_used, "解"),
            "solver_engine": engine_used,
            "feasible":          feasible,
            "routes":            routes,
            "kpi":               kpi,
            "summary":           summary,
            "unserved_customers": unserved,
            "unserved_reasons":  unserved_reasons,
            "elapsed_sec":       elapsed,
        }

        return {
            "status":          "ok",
            "feasible":        feasible,
            "metadata":        meta,
            "solutions":       [solution],
            "issues":          issues,
            "_solver_version": "truck_dispatcher_v4.0_cpo",
        }

    # ------------------------------------------------------------------
    # CP Optimizer 実装本体
    # ------------------------------------------------------------------
    def _solve_cpo(self, locations, vehicles, customers, dist_matrix, config) -> dict:
        mdl = CpoModel(name="OptiBuddy_TruckDispatcher_v4")
        mdl.set_parameters({"RandomSeed": 42})

        avg_spd          = float(config.get("avg_speed_kmh", 30.0))
        highway_spd      = float(config.get("highway_speed_kmh", 80.0))
        highway_thr      = float(config.get("highway_threshold_km", 50.0))
        depot_open       = int(config.get("depot_open_min", 360))
        depot_close      = int(config.get("depot_close_min", 1200))
        # フォールバックデフォルトも本番想定値（83000/分、未割当ペナルティ1000万点との
        # 120分クロスオーバーで校正）に合わせる。以前は10.0のままで、config未指定時に
        # 実質「遅刻し放題」になってしまっていた。
        soft_penalty_min = float(config.get("soft_tw_penalty_per_min", 83000.0))
        time_limit       = int(config.get("solver_time_limit_sec", 60))

        def travel_min(dist_km: float) -> int:
            if dist_km > highway_thr and highway_spd > 0:
                return int(round((dist_km / highway_spd) * 60.0))
            return int(round((dist_km / avg_spd) * 60.0)) if avg_spd > 0 else 0

        n_loc = len(locations)
        # 移動時間行列（分単位の整数。分単位精度で十分なため四捨五入する）
        travel_matrix = [[travel_min(dist_matrix[i][j]) for j in range(n_loc)] for i in range(n_loc)]
        tm = build_cpo_transition_matrix(travel_matrix)

        # ------------------------------------------------------------------
        # Step 1: 決定変数（visit[v][c] は許可された組み合わせのみ作成）
        # ------------------------------------------------------------------
        visit: dict[str, dict[str, Any]] = {}
        depot_out: dict[str, Any] = {}
        depot_in: dict[str, Any] = {}

        for v in vehicles:
            vid = v["id"]
            visit[vid] = {}
            for c in customers:
                cid = c["id"]
                if v.get("type", "") in c.get("restricted_vehicle_types", []):
                    continue  # 車格制限：対象外車両では変数自体を作らない
                svc = int(c.get("service_time_min", 15))
                tw_open  = int(c.get("tw_open_min",  depot_open))
                tw_close = int(c.get("tw_close_min", depot_close))
                itv = mdl.interval_var(
                    optional=True,
                    size=svc,
                    # 開始はTW開始をハード下限、終了は depot_close_min までをソフト上限として許容
                    # （ヒアリング要件「配送時間は多少の遅延許容」に対応）
                    start=(tw_open, depot_close),
                    end=(tw_open + svc, depot_close),
                    name=f"visit_{vid}_{cid}",
                )
                visit[vid][cid] = itv

            depot_out[vid] = mdl.interval_var(
                optional=True, size=0,
                start=(depot_open, depot_open),
                name=f"depot_out_{vid}",
            )
            depot_in[vid] = mdl.interval_var(
                optional=True, size=0,
                end=(depot_open, depot_close),
                name=f"depot_in_{vid}",
            )

        # ------------------------------------------------------------------
        # Step 2: 各顧客は最大1台に割り当てる（修正: == 1 ではなく <= 1 に緩め、
        #   未割り当てを目的関数のペナルティとして表現する（Step4で追加）。
        #   ==1のままだと、1件でも物理的に入らない顧客がプールに含まれるだけで
        #   モデル全体が即座にin feasibleになり、LNSのRecreateで実際に
        #   25回全て失敗する不具合が発生した。ProductionLotSchedulerが採用している
        #   「未割当ペナルティを目的関数に加える」方式と同じ考え方。
        # ------------------------------------------------------------------
        for c in customers:
            cid = c["id"]
            presence_vars = [mdl.presence_of(visit[v["id"]][cid])
                              for v in vehicles if cid in visit[v["id"]]]
            if not presence_vars:
                logger.warning(f"[TruckDispatcher/CPO] 顧客 {cid} を割り当てられる車両がありません（型式制限で全滅）")
                continue
            mdl.add(mdl.sum(presence_vars) <= 1)

        # ------------------------------------------------------------------
        # Step 3: 車両ごとの制約（順序・容量・積み付け・拘束時間・デポ在籍）
        # ------------------------------------------------------------------
        for v in vehicles:
            vid = v["id"]
            allowed_custs = [c for c in customers if c["id"] in visit[vid]]
            if not allowed_custs:
                continue

            # 訪問順序（デポ出発を先頭・デポ帰着を末尾に固定）
            seq_items = [depot_out[vid], depot_in[vid]] + [visit[vid][c["id"]] for c in allowed_custs]
            seq_types = [0, 0] + [c["_loc_idx"] for c in allowed_custs]
            seq = mdl.sequence_var(seq_items, types=seq_types, name=f"seq_{vid}")
            mdl.add(mdl.no_overlap(seq, tm))
            mdl.add(mdl.first(seq, depot_out[vid]))
            mdl.add(mdl.last(seq, depot_in[vid]))

            # デポ出発/帰着の在籍は、その車両に割り当てられた顧客の有無と一致させる
            for c in allowed_custs:
                cid = c["id"]
                mdl.add(mdl.presence_of(visit[vid][cid]) <= mdl.presence_of(depot_out[vid]))
                mdl.add(mdl.presence_of(visit[vid][cid]) <= mdl.presence_of(depot_in[vid]))
            mdl.add(mdl.presence_of(depot_out[vid]) == mdl.presence_of(depot_in[vid]))

            # 積載量制約
            cap_kg = v.get("capacity_kg", 0)
            mdl.add(mdl.sum([c.get("demand_kg", 0) * mdl.presence_of(visit[vid][c["id"]])
                              for c in allowed_custs]) <= cap_kg)

            # 積み付け制約: 段積み不可貨物は同一車両に2件以上同時搭載しない
            non_stackable = [c for c in allowed_custs if not c.get("stackable", True)]
            if len(non_stackable) >= 2:
                mdl.add(mdl.sum([mdl.presence_of(visit[vid][c["id"]]) for c in non_stackable]) <= 1)

            # 拘束時間上限（デポ帰着 - デポ出発 <= max_duty_min。不在時は差0で常に満たす）
            max_duty = v.get("max_duty_min", 9999)
            mdl.add(mdl.end_of(depot_in[vid], depot_open) - mdl.start_of(depot_out[vid], depot_open) <= max_duty)

        # ------------------------------------------------------------------
        # Step 4: 目的関数
        # ------------------------------------------------------------------
        used_terms   = [mdl.presence_of(depot_out[v["id"]]) for v in vehicles]
        duty_terms   = [mdl.end_of(depot_in[v["id"]], depot_open) - mdl.start_of(depot_out[v["id"]], depot_open)
                        for v in vehicles]
        penalty_terms = []
        for v in vehicles:
            vid = v["id"]
            for c in customers:
                cid = c["id"]
                if cid not in visit[vid]:
                    continue
                tw_close = int(c.get("tw_close_min", depot_close))
                overdue  = mdl.max(0, mdl.start_of(visit[vid][cid], tw_close) - tw_close)
                penalty_terms.append(soft_penalty_min * overdue)

        # 未割当ペナルティ（Step2で <= 1 に緩めた分をここで目的関数へ反映する）。
        # 使用車両数削減（重み100000）よりも圧倒的に重くし、
        # 「車両を減らすために顧客を損にする」という本末転倒を避ける。
        unassigned_terms = []
        for c in customers:
            cid = c["id"]
            presence_vars = [mdl.presence_of(visit[v["id"]][cid])
                              for v in vehicles if cid in visit[v["id"]]]
            if not presence_vars:
                continue
            unassigned_terms.append(1 - mdl.sum(presence_vars))

        obj = (100000 * mdl.sum(used_terms)
               + 1 * mdl.sum(duty_terms)
               + mdl.sum(penalty_terms)
               + 10000000 * mdl.sum(unassigned_terms))
        mdl.add(mdl.minimize(obj))

        # ------------------------------------------------------------------
        # Step 5: ソルブ
        # （以前ここに mdl.end() を try/finally で呼んでいたが、CpoModelには
        #  end() メソッド自体が存在せず、毎回例外が無意味にログされていただけだった。
        #  docplexのCpoSolverは solve() 完了時に自己管理でネイティブプロセスを終了させるため不要。）
        # ------------------------------------------------------------------
        try:
            msol = mdl.solve(TimeLimit=time_limit, LogVerbosity="Quiet")
        except Exception as e:
            msg = str(e)
            if "Problem size limit exceeded" in msg or "size limit" in msg.lower():
                # ライセンス上限エラーはここでは捕まえた形では修復できない（問題規模自体が大きすぎる）。
                # 後はinfeasibleとして握りつぶさず、呼び出し元（solve()）がLNSに
                # フォールバックできるよう、専用例外として再度発生させる。
                raise _CpoSizeLimitError(msg) from e
            # [2026-07-28] 従来はここで feasible=False の通常結果を返し、_build_issues()が
            # 「配送先を割り当てられなかった」という業務向けsolve_failed issueを生成していた。
            # これだと実装バグによるクラッシュが「全件未割当」という business な理由に
            # 見えてしまう。再raiseし、呼び出し元solve()のexcept Exceptionでcrash専用の
            # 結果を返す（solvers/base/solver_error_result.py 参照）。
            logger.error(f"[TruckDispatcher/CPO] mdl.solve() 例外: {e}", exc_info=True)
            raise

        if msol is None or not msol.is_solution():
            logger.warning("[TruckDispatcher/CPO] 解なし（infeasible or timeout）")
            return {"feasible": False, "routes": [], "kpi": {}, "unserved_customers": [c["id"] for c in customers]}

        return self._extract_solution(msol, vehicles, customers, visit, depot_out, depot_in,
                                       dist_matrix, travel_matrix, locations)

    # ------------------------------------------------------------------
    # CP-SAT(CPMpy)フォールバック実装（2026-08-13追加）
    # ------------------------------------------------------------------
    def _solve_cpsat(self, locations, vehicles, customers, dist_matrix, config) -> dict:
        """
        docplex(CPLEX CP Optimizer)が使えない環境向けの、貪欲法より高品質な
        CP-SATベースのフォールバック。_solve_cpo()と同じ入出力契約
        ({"feasible","routes","kpi","unserved_customers"})。

        _solve_cpo()の単一joint最適化（割当+巡回順序+タイミングを同時に解く）とは
        異なり、2段階に分解して解く:
          Stage1（割当）: 顧客→車両の割当をCP-SATで解く（容量・車格制限・積み付け・
            「往復直行時間の合計」という安全な拘束時間上限proxyのみを見た集合分割
            問題。三角不等式より「任意の巡回順序での実際の拘束時間 <= 個別往復の合計」
            が常に成り立つため、このproxyで通った割当はStage2で拘束時間超過に
            なることは基本的に無い＝安全側のフィルタ）。
          Stage2（巡回順序+タイミング）: 車両ごとに、Stage1で割り当てられた顧客
            集合について cp.Circuit(depot_start/depot_end込み) + reified含意制約
            ("succ[i]==jなら end[i]+travel(i,j)<=start[j]") で巡回順序と到着時刻を
            厳密に解く。Circuit単体はdepot_route_plannerで、reified含意による
            タイミング伝播はpatient_transport_planner/medical_appointment_sequence_
            schedulerでそれぞれ個別にbrute-force照合済みだが、両者の組み合わせ自体は
            本ドメイン固有の新規性のため、実装前に3顧客の小規模ケースで単体
            ブルートフォース照合を行い、一致を確認済み。

        Stage2が特定の車両でinfeasible（TW制約とCircuitの組合せ上どうしても
        解が無い等、稀なケース）になった場合、その車両に割り当てられた顧客は
        全て「未割当」として扱う（再割当・repairは行わない）。_solve_cpo()の
        joint最適化と完全に同一品質にはならないが、フォールバック用途としては
        現行の貪欲法（Savings+2-opt、依存関係の共有区間を一切考慮しない単純な
        First-Fit）より明らかに高品質。
        """
        import cpmpy as cp
        from cpmpy.expressions.globalconstraints import Circuit

        avg_spd          = float(config.get("avg_speed_kmh", 30.0))
        highway_spd      = float(config.get("highway_speed_kmh", 80.0))
        highway_thr      = float(config.get("highway_threshold_km", 50.0))
        depot_open       = int(config.get("depot_open_min", 360))
        depot_close      = int(config.get("depot_close_min", 1200))
        # 2026-08-13修正（テストで発覚）: CPMpy(CP-SAT)は係数にfloat定数を受け付けない
        # （docplex.cpは目的関数の重みとしてfloatをそのまま許容するが、CP-SATは整数
        # 係数のみ）。ペナルティ重みは「他の項を圧倒する巨大な抑止力」としてのみ
        # 使われる値のため、四捨五入して整数化しても意味は変わらない。
        soft_penalty_min = int(round(float(config.get("soft_tw_penalty_per_min", 83000.0))))
        time_limit       = int(config.get("solver_time_limit_sec", 60))

        def travel_min(dist_km: float) -> int:
            if dist_km > highway_thr and highway_spd > 0:
                return int(round((dist_km / highway_spd) * 60.0))
            return int(round((dist_km / avg_spd) * 60.0)) if avg_spd > 0 else 0

        n_loc = len(locations)
        travel_matrix = [[travel_min(dist_matrix[i][j]) for j in range(n_loc)] for i in range(n_loc)]

        # ------------------------------------------------------------------
        # Stage1: 顧客→車両の割当
        # ------------------------------------------------------------------
        allowed: dict[str, list[dict]] = {}  # vid -> 割当候補customerリスト
        present1: dict[tuple, Any] = {}       # (vid, cid) -> boolvar
        for v in vehicles:
            vid = v["id"]
            allowed[vid] = [c for c in customers if v.get("type", "") not in c.get("restricted_vehicle_types", [])]
            for c in allowed[vid]:
                present1[(vid, c["id"])] = cp.boolvar(name=f"assign_{vid}_{c['id']}")

        m1 = cp.Model()
        for c in customers:
            cid = c["id"]
            vars_c = [present1[(v["id"], cid)] for v in vehicles if (v["id"], cid) in present1]
            if not vars_c:
                logger.warning(f"[TruckDispatcher/CPSAT] 顧客 {cid} を割り当てられる車両がありません（型式制限で全滅）")
                continue
            m1 += (cp.sum(vars_c) <= 1)

        used1: dict[str, Any] = {}
        for v in vehicles:
            vid = v["id"]
            vcands = allowed[vid]
            used1[vid] = cp.boolvar(name=f"used_{vid}")
            if not vcands:
                m1 += (used1[vid] == 0)
                continue
            for c in vcands:
                m1 += (present1[(vid, c["id"])] <= used1[vid])

            cap_kg = v.get("capacity_kg", 0)
            m1 += (cp.sum([c.get("demand_kg", 0) * present1[(vid, c["id"])] for c in vcands]) <= cap_kg)

            non_stackable = [c for c in vcands if not c.get("stackable", True)]
            if len(non_stackable) >= 2:
                m1 += (cp.sum([present1[(vid, c["id"])] for c in non_stackable]) <= 1)

            # 拘束時間上限の安全側proxy（三角不等式により実際の巡回時間の上界）
            max_duty = v.get("max_duty_min", 9999)
            duty_upper_terms = []
            for c in vcands:
                one_way = travel_matrix[0][c["_loc_idx"]]
                svc = int(c.get("service_time_min", 15))
                duty_upper_terms.append((svc + 2 * one_way) * present1[(vid, c["id"])])
            if duty_upper_terms:
                m1 += (cp.sum(duty_upper_terms) <= max_duty)

        unassigned_terms1 = []
        for c in customers:
            cid = c["id"]
            vars_c = [present1[(v["id"], cid)] for v in vehicles if (v["id"], cid) in present1]
            if not vars_c:
                continue
            unassigned_terms1.append(1 - cp.sum(vars_c))

        # 地理的分散ペナルティ（小規模インスタンスのみ）: 同一車両に割り当てられた
        # 顧客ペアの距離を軽い重みで最小化し、Stage1が「車両数最小化」だけを見て
        # 遠方の顧客同士を1台にまとめてしまう（Stage2の巡回距離が無駄に伸びる）のを
        # 抑止する。O(顧客数^2)のペア項を追加するため、フォールバック用途として
        # 許容できる規模（顧客数<=40）に限定する（それ以上はvehicles_used/
        # unassignedの2項のみで妥協し、Stage1のソルブ時間を優先する）。
        dispersion_terms = []
        if len(customers) <= 40:
            import itertools as _itertools
            for v in vehicles:
                vid = v["id"]
                vcands = allowed[vid]
                for c1, c2 in _itertools.combinations(vcands, 2):
                    pair_dist = travel_matrix[c1["_loc_idx"]][c2["_loc_idx"]]
                    if pair_dist == 0:
                        continue
                    both = present1[(vid, c1["id"])] & present1[(vid, c2["id"])]
                    dispersion_terms.append(pair_dist * both)

        obj1_terms = []
        if used1:
            obj1_terms.append(100000 * cp.sum(list(used1.values())))
        if unassigned_terms1:
            obj1_terms.append(10000000 * cp.sum(unassigned_terms1))
        if dispersion_terms:
            obj1_terms.append(cp.sum(dispersion_terms))
        if obj1_terms:
            objective1 = obj1_terms[0]
            for t in obj1_terms[1:]:
                objective1 = objective1 + t
            m1.minimize(objective1)

        solved1 = m1.solve(solver="ortools", time_limit=max(5, time_limit // 2))
        if not solved1:
            logger.warning("[TruckDispatcher/CPSAT] Stage1(割当)が解けませんでした。")
            return {"feasible": False, "routes": [], "kpi": {}, "unserved_customers": [c["id"] for c in customers]}

        vehicle_customers: dict[str, list[dict]] = {v["id"]: [] for v in vehicles}
        for v in vehicles:
            vid = v["id"]
            for c in allowed[vid]:
                if present1[(vid, c["id"])].value():
                    vehicle_customers[vid].append(c)

        # ------------------------------------------------------------------
        # Stage2: 車両ごとの巡回順序+タイミング
        # ------------------------------------------------------------------
        routes_out: list[dict] = []
        served_ids: set[str] = set()
        total_dist = 0.0

        for v in vehicles:
            vid = v["id"]
            vcust = vehicle_customers[vid]
            if not vcust:
                continue

            n = len(vcust)
            nodes_loc = [0] + [c["_loc_idx"] for c in vcust] + [0]  # depot_start, customers..., depot_end
            svc = [0] + [int(c.get("service_time_min", 15)) for c in vcust] + [0]
            tw_open_arr = [depot_open] + [int(c.get("tw_open_min", depot_open)) for c in vcust] + [depot_open]
            n_nodes = n + 2  # depot_start(0), customers(1..n), depot_end(n+1)

            succ = cp.intvar(0, n_nodes - 1, shape=n_nodes, name=f"succ_{vid}")
            start_vars = [cp.intvar(tw_open_arr[i], depot_close, name=f"start_{vid}_{i}") for i in range(n_nodes)]
            end_vars = [start_vars[i] + svc[i] for i in range(n_nodes)]

            m2 = cp.Model()
            m2 += Circuit(succ)
            m2 += (start_vars[0] == depot_open)
            m2 += (succ[n_nodes - 1] == 0)  # depot_endの直後はdepot_start(閉路の折り返し点を固定)
            for i in range(n_nodes):
                for j in range(n_nodes):
                    if i == j or j == 0:
                        continue
                    tt = travel_matrix[nodes_loc[i]][nodes_loc[j]]
                    m2 += (succ[i] == j).implies(end_vars[i] + tt <= start_vars[j])

            max_duty = v.get("max_duty_min", 9999)
            m2 += (start_vars[n_nodes - 1] - depot_open <= max_duty)

            overdue_terms = []
            for idx, c in enumerate(vcust, start=1):
                tw_close = int(c.get("tw_close_min", depot_close))
                overdue = cp.max([0, start_vars[idx] - tw_close])
                overdue_terms.append(soft_penalty_min * overdue)
            objective2 = cp.sum(overdue_terms) + start_vars[n_nodes - 1] if overdue_terms else start_vars[n_nodes - 1]
            m2.minimize(objective2)

            solved2 = m2.solve(solver="ortools", time_limit=max(3, time_limit // max(1, len(vehicles))))
            if not solved2:
                logger.warning(
                    f"[TruckDispatcher/CPSAT] 車両{vid}のStage2(巡回順序+タイミング)が解けませんでした。"
                    f"この車両に割り当てられた{len(vcust)}件は未割当として扱います。"
                )
                continue

            # 訪問順序の復元（succからdepot_startを起点にたどる）
            order_idx = []
            cur = 0
            for _ in range(n_nodes):
                nxt = int(succ[cur].value())
                if nxt == n_nodes - 1:
                    break
                order_idx.append(nxt)
                cur = nxt

            stops = []
            for seq_no, idx in enumerate(order_idx):
                c = vcust[idx - 1]
                cid = c["id"]
                start_val = int(start_vars[idx].value())
                stops.append({
                    "customer_id":      cid,
                    "customer_name":    c.get("name", cid),
                    "sequence":         seq_no + 1,
                    "arrival_min":      start_val,
                    "arrival_time":     _min_to_hhmm(start_val),
                    "tw_open_min":      c.get("tw_open_min", 360),
                    "tw_close_min":     c.get("tw_close_min", 1200),
                    "tw_open_str":      _min_to_hhmm(c.get("tw_open_min", 360)),
                    "tw_close_str":     _min_to_hhmm(c.get("tw_close_min", 1200)),
                    "demand_kg":        c.get("demand_kg", 0),
                    "service_time_min": c.get("service_time_min", 15),
                    "seg_dist_km":      0.0,
                    "stackable":        c.get("stackable", True),
                    "cargo_shape":      c.get("cargo_shape", ""),
                    "stack_limit":      c.get("stack_limit", 0),
                })
                served_ids.add(cid)

            route_dist = 0.0
            prev_idx = 0
            for s in stops:
                loc_idx = next(c for c in vcust if c["id"] == s["customer_id"])["_loc_idx"]
                seg_km = dist_matrix[prev_idx][loc_idx]
                s["seg_dist_km"] = round(seg_km, 2)
                route_dist += seg_km
                prev_idx = loc_idx
            home_dist = dist_matrix[prev_idx][0]
            route_dist += home_dist
            total_dist += route_dist

            dep_min = depot_open
            ret_min = int(start_vars[n_nodes - 1].value())
            cap = v.get("capacity_kg", 0)
            load_kg = sum(s["demand_kg"] for s in stops)

            routes_out.append({
                "vehicle_id":      vid,
                "vehicle_name":    v.get("name", vid),
                "vehicle_type":    v.get("type", ""),
                "capacity_kg":     cap,
                "load_kg":         load_kg,
                "utilization_pct": round(load_kg / cap * 100, 1) if cap > 0 else 0,
                "n_stops":         len(stops),
                "depart_min":      dep_min,
                "depart_time":     _min_to_hhmm(dep_min),
                "return_min":      ret_min,
                "return_time":     _min_to_hhmm(ret_min),
                "duty_min":        ret_min - dep_min,
                "route_dist_km":   round(route_dist, 2),
                "stops":           stops,
            })

        unserved = [c["id"] for c in customers if c["id"] not in served_ids]
        feasible = len(unserved) == 0
        kpi = self._build_kpi(routes_out, total_dist)
        return {"feasible": feasible, "routes": routes_out, "kpi": kpi, "unserved_customers": unserved}

    # ------------------------------------------------------------------
    def _extract_solution(self, msol, vehicles, customers, visit, depot_out, depot_in,
                           dist_matrix, travel_matrix, locations) -> dict:
        cust_by_id = {c["id"]: c for c in customers}
        routes_out: list[dict] = []
        served_ids: set[str] = set()
        total_dist = 0.0

        for v in vehicles:
            vid = v["id"]
            out_sol = msol.get_var_solution(depot_out[vid])
            if out_sol is None or not out_sol.is_present():
                continue

            stop_entries = []
            for c in customers:
                cid = c["id"]
                if cid not in visit[vid]:
                    continue
                var_sol = msol.get_var_solution(visit[vid][cid])
                if var_sol is None or not var_sol.is_present():
                    continue
                stop_entries.append((var_sol.get_start(), c, var_sol))
            stop_entries.sort(key=lambda x: x[0])

            stops = []
            for seq_no, (start_val, c, var_sol) in enumerate(stop_entries):
                cid = c["id"]
                stops.append({
                    "customer_id":      cid,
                    "customer_name":    c.get("name", cid),
                    "sequence":         seq_no + 1,
                    "arrival_min":      int(var_sol.get_start()),
                    "arrival_time":     _min_to_hhmm(var_sol.get_start()),
                    "tw_open_min":      c.get("tw_open_min", 360),
                    "tw_close_min":     c.get("tw_close_min", 1200),
                    "tw_open_str":      _min_to_hhmm(c.get("tw_open_min", 360)),
                    "tw_close_str":     _min_to_hhmm(c.get("tw_close_min", 1200)),
                    "demand_kg":        c.get("demand_kg", 0),
                    "service_time_min": c.get("service_time_min", 15),
                    "seg_dist_km":      0.0,  # 下で補正
                    "stackable":        c.get("stackable", True),
                    "cargo_shape":      c.get("cargo_shape", ""),
                    "stack_limit":      c.get("stack_limit", 0),
                })
                served_ids.add(cid)

            # 区間距離（seg_dist_km）を実際の訪問順序（loc_idx）から計算する
            route_dist = 0.0
            prev_idx = 0  # depot
            for s in stops:
                loc_idx = cust_by_id[s["customer_id"]]["_loc_idx"]
                seg_km = dist_matrix[prev_idx][loc_idx]
                s["seg_dist_km"] = round(seg_km, 2)
                route_dist += seg_km
                prev_idx = loc_idx
            home_dist = dist_matrix[prev_idx][0]
            route_dist += home_dist
            total_dist += route_dist

            in_sol   = msol.get_var_solution(depot_in[vid])
            dep_min  = int(out_sol.get_start())
            ret_min  = int(in_sol.get_end()) if in_sol is not None else dep_min
            cap      = v.get("capacity_kg", 0)
            load_kg  = sum(s["demand_kg"] for s in stops)

            routes_out.append({
                "vehicle_id":      vid,
                "vehicle_name":    v.get("name", vid),
                "vehicle_type":    v.get("type", ""),
                "capacity_kg":     cap,
                "load_kg":         load_kg,
                "utilization_pct": round(load_kg / cap * 100, 1) if cap > 0 else 0,
                "n_stops":         len(stops),
                "depart_min":      dep_min,
                "depart_time":     _min_to_hhmm(dep_min),
                "return_min":      ret_min,
                "return_time":     _min_to_hhmm(ret_min),
                "duty_min":        ret_min - dep_min,
                "route_dist_km":   round(route_dist, 2),
                "stops":           stops,
            })

        unserved = [c["id"] for c in customers if c["id"] not in served_ids]
        feasible = len(unserved) == 0
        kpi = self._build_kpi(routes_out, total_dist)
        return {"feasible": feasible, "routes": routes_out, "kpi": kpi, "unserved_customers": unserved}

    # ------------------------------------------------------------------
    @staticmethod
    def _build_kpi(routes: list[dict], total_dist: float) -> dict:
        n_v = len(routes)
        loads  = [r["load_kg"] for r in routes]
        duties = [int(r["duty_min"]) for r in routes]
        return {
            "n_vehicles_used":    n_v,
            "n_stops_total":      sum(r["n_stops"] for r in routes),
            "total_dist_km":      round(total_dist, 2),
            "avg_dist_per_veh":   round(total_dist / n_v, 2) if n_v > 0 else 0,
            "max_load_kg":        max(loads) if loads else 0,
            "min_load_kg":        min(loads) if loads else 0,
            "avg_util_pct":       round(sum(r["utilization_pct"] for r in routes) / n_v, 1) if n_v > 0 else 0,
            "max_duty_min":       max(duties) if duties else 0,
            "min_duty_min":       min(duties) if duties else 0,
            "load_imbalance_kg":  max(loads) - min(loads) if loads else 0,
            "duty_imbalance_min": max(duties) - min(duties) if duties else 0,
        }

    @staticmethod
    def _build_summary(routes, kpi, meta, vehicles, depot) -> dict:
        return {
            "depot_name":        depot.get("name", "倉庫"),
            "instance_name":     meta.get("instance_name", ""),
            "n_vehicles_total":  len(vehicles),
            "n_vehicles_used":   kpi.get("n_vehicles_used", 0),
            "n_stops_total":     kpi.get("n_stops_total", 0),
            "total_dist_km":     kpi.get("total_dist_km", 0),
            "avg_dist_per_veh":  kpi.get("avg_dist_per_veh", 0),
            "avg_util_pct":      kpi.get("avg_util_pct", 0),
            "max_duty_min":      kpi.get("max_duty_min", 0),
            "load_imbalance_kg": kpi.get("load_imbalance_kg", 0),
        }

    # ------------------------------------------------------------------
    # 未割当理由の分類（2026-07-08, Koshoshiとの相談①）
    # ------------------------------------------------------------------
    # 2026-07-23追記: insufficient_fleet_capacity判定で「1配送が車両の拘束時間を
    # 支配的に占める」とみなす閾値（詳細は_classify_unserved()のdocstring参照）。
    _FLEET_CAPACITY_DOMINANT_TRIP_RATIO = 0.4

    @staticmethod
    def _eligible_vehicle_ids(cust: dict, vehicles: list[dict]) -> set[str]:
        """型式制限・積載量の両方を満たす車両IDの集合を返す（型式のみは考慮しない点に注意。
        capacity_infeasible/no_eligible_vehicleの粒度分けが必要な箇所では使わず、
        insufficient_fleet_capacity判定の「同じ車両群を奪い合っているか」の比較にのみ使う）。"""
        restricted = cust.get("restricted_vehicle_types", [])
        demand = cust.get("demand_kg", 0)
        return {v["id"] for v in vehicles
                if v.get("type", "") not in restricted and v.get("capacity_kg", 0) >= demand}

    def _classify_unserved(self, unserved: list[str], customers: list[dict], vehicles: list[dict],
                            dist_matrix: list[list[float]], locations: list[dict], config: dict) -> dict[str, dict]:
        """
        未割当の各顧客について、なぜ割り当てられなかったかを判定する。
          - capacity_infeasible      : 需要 > 割当可能などの車両の最大積載量。分割配送なしでは
            数学的に不可能（時間をかけても解けない）。
          - duty_infeasible          : depot出発が全車両06:00固定という現行モデルの制約上、
            直行往復だけでも max_duty_min を超過する。分割配送や出発時刻の柔軟化なしでは不可能。
          - insufficient_fleet_capacity: 単体では容量・拘束時間とも条件を満たせるが、同じ
            車両群（型式制限・積載量で絞られた候補車両の集合）を必要とする他の顧客との
            合計人数が、その車両群の台数を上回っている（2026-07-23追記）。
            探索の問題ではなくフリート側の頭数不足の可能性がある、という近似判定
            （Hall/結婚定理型の充足不足チェック。詳細は下記）。
          - unresolved_by_search     : 単体では容量・拘束時間とも条件を満たせるはずで、
            上記のフリート頭数不足の兆候も見られないのに、今回のLNS/CPO探索では割当先を
            見つけられなかった（反復不足の可能性）。
          - no_eligible_vehicle      : restricted_vehicle_types によりそもそも割当候補車両がゼロ。
        """
        if not unserved:
            return {}

        cust_by_id = {c["id"]: c for c in customers}
        loc_id_to_idx = {loc["id"]: idx for idx, loc in enumerate(locations)}

        avg_spd     = float(config.get("avg_speed_kmh", 30.0))
        highway_spd = float(config.get("highway_speed_kmh", 80.0))
        highway_thr = float(config.get("highway_threshold_km", 50.0))
        depot_open  = int(config.get("depot_open_min", 360))

        def travel_min(dist_km: float) -> float:
            if dist_km > highway_thr and highway_spd > 0:
                return (dist_km / highway_spd) * 60.0
            return (dist_km / avg_spd) * 60.0 if avg_spd > 0 else 0.0

        result: dict[str, dict] = {}
        for cid in unserved:
            c = cust_by_id.get(cid)
            if c is None:
                result[cid] = {"reason": "unknown", "detail": "顧客データが見つかりません"}
                continue

            restricted = c.get("restricted_vehicle_types", [])
            candidates = [v for v in vehicles if v.get("type", "") not in restricted]
            if not candidates:
                result[cid] = {"reason": "no_eligible_vehicle",
                                "detail": "車格制限（restricted_vehicle_types）により割当可能な車両がありません。"}
                continue

            demand = c.get("demand_kg", 0)
            cap_ok = [v for v in candidates if v.get("capacity_kg", 0) >= demand]
            if not cap_ok:
                max_cap = max((v.get("capacity_kg", 0) for v in candidates), default=0)
                result[cid] = {
                    "reason": "capacity_infeasible",
                    "detail": f"需要{demand}kgが、割当候補車両の最大積載量{max_cap}kgを超過しています。"
                              f"分割配送（複数便への分割）または増車なしでは、探索時間に関わらず割当不可能です。",
                }
                continue

            loc_idx = loc_id_to_idx.get(cid)
            if loc_idx is None:
                result[cid] = {"reason": "unresolved_by_search", "detail": "位置情報を特定できず、詳細判定不能です。"}
                continue

            dist_km = dist_matrix[0][loc_idx]
            one_way = travel_min(dist_km)
            svc = c.get("service_time_min", 15)
            tw_open_min = c.get("tw_open_min", depot_open)

            # depot出発06:00固定・直行を想定した理論上の最小拘束時間
            # （実際には他の顧客と組み合わせる分、多少前後しうるが、これが絶対的な下限）
            arrival_direct  = depot_open + one_way
            start_service   = max(arrival_direct, tw_open_min)
            finish_service  = start_service + svc
            min_duty        = (finish_service + one_way) - depot_open

            duty_ok = [v for v in cap_ok if min_duty <= v.get("max_duty_min", 9999)]
            if not duty_ok:
                best_max_duty = max((v.get("max_duty_min", 0) for v in cap_ok), default=0)
                result[cid] = {
                    "reason": "duty_infeasible",
                    "detail": f"depot出発06:00固定・直行往復のみでも最低{int(min_duty)}分の拘束時間が必要ですが、"
                              f"割当候補車両の拘束時間上限は最大{best_max_duty}分です"
                              f"（{int(min_duty - best_max_duty)}分超過）。出発時刻の柔軟化なしでは割当不可能です。",
                }
                continue

            # ------------------------------------------------------------
            # insufficient_fleet_capacity判定（2026-07-23追記）
            #
            # ここまでの各チェックは「この顧客1件」だけを車両群に照らして判定しており、
            # 同じ車両群を必要とする他の顧客との奪い合いを見ていない。そのため、
            # 「型式・積載量に絞られた車両群の台数に対し、同じ車両群でしか運べない
            # 顧客の総数が明らかに多い」場合でも、単体チェックだけでは全員
            # unresolved_by_search（探索の問題）に分類されてしまい、実際には
            # フリート側の頭数不足（DSL・運用側の対応が必要）であることを見逃す。
            #
            # 近似ヒューリスティック: 型式・積載量でこの顧客と同じかそれ以下の候補しか
            # 持たない「競合顧客」（全顧客中、この顧客の候補車両集合の部分集合しか
            # 持たない顧客）を数え、その人数が車両群の台数を上回っていれば頭数不足の
            # 兆候とみなす。ただし1台が1日に複数件こなせるような短時間配送まで
            # 一律に「1台1件専有」と決めつけると誤検知になるため、この顧客自身の
            # 往復所要時間が、車両群内で最も厳しい拘束時間上限の
            # _FLEET_CAPACITY_DOMINANT_TRIP_RATIO（40%）以上を占める場合に限って
            # 適用する（＝1台が同種の配送を2件以上こなすのが現実的に難しいケースのみ）。
            # 顧客ID・シナリオ固有の数値は一切参照しない、DSLの一般フィールド
            # （demand_kg/capacity_kg/restricted_vehicle_types/max_duty_min等）だけを
            # 使った汎用チェック。証明ではなく参考情報であることをdetailに明記する。
            # ------------------------------------------------------------
            cap_ok_ids = {v["id"] for v in cap_ok}
            contenders = [c2 for c2 in customers
                          if self._eligible_vehicle_ids(c2, vehicles) <= cap_ok_ids]
            tightest_max_duty = min((v.get("max_duty_min", 9999) for v in cap_ok), default=9999)
            dominant_trip = (tightest_max_duty > 0
                              and min_duty / tightest_max_duty >= self._FLEET_CAPACITY_DOMINANT_TRIP_RATIO)

            if len(contenders) > len(cap_ok_ids) and dominant_trip:
                contender_ids = sorted(c2["id"] for c2 in contenders)
                result[cid] = {
                    "reason": "insufficient_fleet_capacity",
                    "detail": f"この配送を運べる車両群は{len(cap_ok_ids)}台ですが、同じ車両群でしか"
                              f"運べない配送が{len(contenders)}件あります"
                              f"（{', '.join(contender_ids[:8])}{'...' if len(contender_ids) > 8 else ''}）。"
                              f"この配送1件の往復所要時間だけで車両の拘束時間上限の"
                              f"約{round(min_duty / tightest_max_duty * 100)}%を占めるため、"
                              f"1台が複数件こなすのも現実的ではありません。探索を続けても解決せず、"
                              f"該当車両群の増車や分割配送の許可などフリート側の対応が必要な可能性が"
                              f"あります（簡易ヒューリスティック判定であり、厳密な証明ではありません）。",
                }
                continue

            result[cid] = {
                "reason": "unresolved_by_search",
                "detail": "単体では容量・拘束時間の条件は満たせるはずですが、今回の探索では割当先が"
                          "見つかりませんでした。他の顧客との車両争奪・LNSの反復不足が原因の可能性があります。",
            }

        return result

    def _build_issues(self, routes: list[dict], unserved: list[str], feasible: bool, config: dict,
                       unserved_reasons: dict[str, dict] | None = None,
                       vehicles: list[dict] | None = None,
                       customers: list[dict] | None = None,
                       dist_matrix: list[list[float]] | None = None) -> list[dict]:
        # 修正（2026-07-08, Koshoshiとの相談）: 以前はここで `if not feasible: ... return issues`
        # としており、1件でも未割当があると tw_tight/duty_overtime のチェックが
        # 一切実行されなかった（=既に組めているルートの検証が丸ごとスキップされていた）。
        # 未割当の有無に関わらず、実際に生成された routes は必ず検証する。
        issues: list[dict] = []
        unserved_reasons = unserved_reasons or {}

        if not feasible:
            # 修正（2026-07-08, Koshoshiとの相談①）: 「CP Optimizerが証明した実行不可能、
            # またはタイムリミット到達」という一括りの文言は原因を隠してしまうため、
            # _classify_unserved() の分類結果で内訳を出す。
            #   - capacity_infeasible        : 需要が最大車両の積載量を超過（分割配送なしでは原理的に不可能）
            #   - duty_infeasible            : depot出発固定＋往復所要時間の制約上、原理的に不可能
            #   - insufficient_fleet_capacity: 単体では条件を満たせるが、同じ車両群を奪い合う顧客数が
            #     車両群の台数を上回っている（フリート頭数不足の可能性。2026-07-23追記）
            #   - unresolved_by_search       : 単体では条件を満たせるはずだが、今回の探索で解が見つからなかった
            reason_order = ["capacity_infeasible", "duty_infeasible", "insufficient_fleet_capacity",
                             "unresolved_by_search", "no_eligible_vehicle", "unknown"]
            reason_label = {
                "capacity_infeasible":         "容量超過（分割配送なしでは不可能）",
                "duty_infeasible":             "拘束時間超過（現行モデルでは不可能）",
                "insufficient_fleet_capacity": "車両群の頭数不足の可能性（増車等フリート側の対応が必要）",
                "unresolved_by_search":        "探索未達（条件は満たせるはずだが今回は未発見）",
                "no_eligible_vehicle":         "車格制限で割当可能車両なし",
                "unknown":                     "不明",
            }
            counts: dict[str, list[str]] = {r: [] for r in reason_order}
            for cid in unserved:
                r = unserved_reasons.get(cid, {}).get("reason", "unknown")
                counts.setdefault(r, []).append(cid)

            breakdown = "、".join(
                f"{reason_label.get(r, r)}={len(ids)}件" + (f"({', '.join(ids[:5])}{'...' if len(ids) > 5 else ''})" if ids else "")
                for r, ids in counts.items() if ids
            )
            msg = f"すべての配送先を割り当てることができませんでした。未割り当て{len(unserved)}件の内訳: {breakdown}。"
            issues.append({
                "id": "solve_failed", "severity": "CRITICAL", "category": "SOLVER",
                "title": "実行可能解なし",
                "message": msg,
                "relatedContainerIds": [],
                "unserved_breakdown": {cid: unserved_reasons.get(cid, {"reason": "unknown", "detail": ""}) for cid in unserved},
            })

        tight = [s["customer_name"] for r in routes for s in r["stops"]
                 if s["tw_close_min"] - (s["arrival_min"] + s["service_time_min"]) < 20]
        if tight:
            issues.append({"id": "tw_tight", "severity": "WARNING",
                "title": "タイムウィンドウ余裕不足",
                "message": f"以下の配送先でTW余裕が20分未満: {', '.join(tight[:5])}{'...' if len(tight)>5 else ''}。",
                "relatedContainerIds": []})

        # 2026-07-24: tw_overdue（TW超過）/duty_overtime（拘束時間超過）は
        # DESIGN_2026-07-21「解チェッカー」の体系化に伴い、solvers/base/issue_rules.py
        # の層A/B（ISSUE_RULES["TruckDispatcher"]）パターンに移植した
        # （複数件を1件の集約issueにまとめる既存挙動・文言は変えていない）。
        # duty_overtime はハード制約（車両ごとのmax_duty_min、281-283行目参照）
        # 違反のため category="SOLVER"（バグ疑い）が build_truck_dispatcher_contexts
        # 側で付与される。tw_overdueはソフト制約（目的関数のペナルティ）の超過で
        # 業務上正常にありうるため付与しない。
        # 2026-07-21修正（本移植でも維持）: 以前はconfig.get("max_driver_duty_min", 540)
        # という、converterが一度も引き継がないグローバル値（常にデフォルト540分固定）
        # を見ており、実際の拘束時間ハード制約と食い違っていた不具合を修正済み。
        max_duty_by_vehicle = {v.get("id"): v.get("max_duty_min", 9999) for v in (vehicles or [])}
        from solvers.base.issue_rules import run_issue_rules, build_truck_dispatcher_contexts
        # 2026-09-25追加: customers/dist_matrix を渡すと、移動時間の検算
        # （transit_shortfall、category="SOLVER"）も実行される。移動時間は
        # DSL宣言の dist_matrix・config から issue_rules 側で計算し直す。
        contexts = build_truck_dispatcher_contexts(
            routes, max_duty_by_vehicle,
            customers=customers, dist_matrix=dist_matrix, config=config,
        )
        issues.extend(run_issue_rules(domain="TruckDispatcher", contexts=contexts, issue_statuses={}))

        overdue_exists  = any(s["arrival_min"] > s["tw_close_min"] for r in routes for s in r["stops"])
        overtime_exists = any(r["duty_min"] > max_duty_by_vehicle.get(r["vehicle_id"], 9999) for r in routes)

        transit_exists  = any(i.get("id") == "transit_shortfall" for i in issues)

        if feasible and not tight and not overtime_exists and not overdue_exists and not transit_exists:
            issues.append({"id": "optimal_route", "severity": "INFO",
                "title": "最適ルート生成完了(CP Optimizer)",
                "message": "CP Optimizerにより全件が実行可能な形で割り当てられました。",
                "relatedContainerIds": []})

        return issues

    @staticmethod
    def _error(msg: str) -> dict:
        return {
            "status": "error", "metadata": {}, "solutions": [],
            "issues": [{"id": "config-error", "severity": "CRITICAL",
                        "title": "入力データエラー", "message": msg, "relatedContainerIds": []}],
            "_solver_version": "truck_dispatcher_v4.0_cpo",
        }

    # ------------------------------------------------------------------
    # フォールバック: docplex が利用できない環境向けの貪欲法（v3.0を保持）
    # ------------------------------------------------------------------
    def _solve_fallback_heuristic(self, locations, vehicles, customers, dist_matrix, config) -> dict:
        engine = _CVRPFallbackEngine(customers=customers, vehicles=vehicles, dist_matrix=dist_matrix, config=config)
        return engine.solve()


def _min_to_hhmm(minutes) -> str:
    minutes = int(minutes)
    h = minutes // 60
    m = minutes % 60
    return f"{h:02d}:{m:02d}"


# ---------------------------------------------------------------------------
# フォールバックエンジン（docplex 未インストール時のみ使用。v3.0の実装を保持）
# ---------------------------------------------------------------------------
class _CVRPFallbackEngine:
    """docplexが使えない環境専用の簡易貪欲法。CP Optimizerの代替として最低限の
    動作を保証するためのものであり、通常運用ではCPOパスが使われる想定。"""

    def __init__(self, customers, vehicles, dist_matrix, config):
        self.customers = customers
        self.vehicles  = vehicles
        self.dist      = dist_matrix
        self.config    = config
        self.avg_spd    = float(config.get("avg_speed_kmh", 30.0))
        self.highway_speed_kmh = float(config.get("highway_speed_kmh", 80.0))
        self.highway_threshold_km = float(config.get("highway_threshold_km", 50.0))
        self.depot_open  = int(config.get("depot_open_min", 360))
        self.depot_close = int(config.get("depot_close_min", 1200))

    def solve(self) -> dict:
        customers = self.customers
        vehicles  = self.vehicles
        unserved  = []
        routes_out: list[dict] = []
        used_veh: set[int] = set()
        total_dist = 0.0

        # 需要降順の単純な First-Fit（docplex不在時の最終手段。品質は保証しない）
        sorted_custs = sorted(range(len(customers)), key=lambda i: -customers[i].get("demand_kg", 0))
        veh_load: dict[int, int] = {}
        veh_stops: dict[int, list] = {}
        # 修正（2026-07-08, Koshoshiとの相談⑤）: 各車両の現在の走行状態（時刻・直前地点）を
        # 逐次シミュレーションし、帰庫予測時刻が max_duty_min を超えるならこの車両には
        # 積まない。以前は容量しか見ておらず、遠方顧客を後から詰め込んだ結果、
        # 900km超・数日がかりの到着という異常ルート（マイクロカット問題）を生成していた。
        veh_state: dict[int, tuple[float, int]] = {}  # vi -> (現在時刻, 直前地点loc_idx)

        for ci in sorted_custs:
            c = customers[ci]
            loc_idx = c["_loc_idx"]
            demand = c.get("demand_kg", 0)
            svc = float(c.get("service_time_min", 15))
            tw_open = float(c.get("tw_open_min", self.depot_open))

            assigned = False
            for vi, v in enumerate(vehicles):
                if v.get("type", "") in c.get("restricted_vehicle_types", []):
                    continue
                cap = v.get("capacity_kg", 0)
                cur_load = veh_load.get(vi, 0)
                if cur_load + demand > cap:
                    continue

                cur_t, prev_idx = veh_state.get(vi, (float(self.depot_open), 0))
                _, _, finish_service = self._simulate_leg(cur_t, prev_idx, loc_idx, tw_open, svc)
                home_dist = self.dist[loc_idx][0]
                projected_return = finish_service + self._travel_min(home_dist)
                max_duty = v.get("max_duty_min", 9999)
                if projected_return - self.depot_open > max_duty:
                    continue  # この車両に積むと拘束時間超過。次の車両を試す。

                veh_load[vi] = cur_load + demand
                veh_stops.setdefault(vi, []).append(c)
                veh_state[vi] = (finish_service, loc_idx)
                used_veh.add(vi)
                assigned = True
                break
            if not assigned:
                unserved.append(c["id"])

        for vi, stops_c in veh_stops.items():
            v = vehicles[vi]
            prev_idx = 0
            t = float(self.depot_open)
            stops = []
            route_dist = 0.0
            for c in stops_c:
                loc_idx = c["_loc_idx"]
                seg = self.dist[prev_idx][loc_idx]
                tw_open, tw_close = c.get("tw_open_min", self.depot_open), c.get("tw_close_min", self.depot_close)
                svc = float(c.get("service_time_min", 15))
                _, t, finish = self._simulate_leg(t, prev_idx, loc_idx, float(tw_open), svc)
                route_dist += seg
                arrival = int(t)
                stops.append({
                    "customer_id": c["id"], "customer_name": c.get("name", c["id"]),
                    "sequence": len(stops) + 1, "arrival_min": arrival, "arrival_time": _min_to_hhmm(arrival),
                    "tw_open_min": tw_open, "tw_close_min": tw_close,
                    "tw_open_str": _min_to_hhmm(tw_open), "tw_close_str": _min_to_hhmm(tw_close),
                    "demand_kg": c.get("demand_kg", 0), "service_time_min": c.get("service_time_min", 15),
                    "seg_dist_km": round(seg, 2),
                    "stackable": c.get("stackable", True), "cargo_shape": c.get("cargo_shape", ""),
                    "stack_limit": c.get("stack_limit", 0),
                })
                t = finish
                prev_idx = loc_idx
            home_dist = self.dist[prev_idx][0]
            route_dist += home_dist
            total_dist += route_dist
            cap = v.get("capacity_kg", 0)
            load_kg = veh_load.get(vi, 0)
            routes_out.append({
                "vehicle_id": v["id"], "vehicle_name": v.get("name", v["id"]), "vehicle_type": v.get("type", ""),
                "capacity_kg": cap, "load_kg": load_kg,
                "utilization_pct": round(load_kg / cap * 100, 1) if cap > 0 else 0,
                "n_stops": len(stops), "depart_min": self.depot_open, "depart_time": _min_to_hhmm(self.depot_open),
                "return_min": int(t + self._travel_min(home_dist)),
                "return_time": _min_to_hhmm(int(t + self._travel_min(home_dist))),
                "duty_min": int(t + self._travel_min(home_dist)) - self.depot_open,
                "route_dist_km": round(route_dist, 2), "stops": stops,
            })

        feasible = len(unserved) == 0
        kpi = TruckDispatcherSolver._build_kpi(routes_out, total_dist)
        return {"feasible": feasible, "routes": routes_out, "kpi": kpi, "unserved_customers": unserved}

    def _travel_min(self, dist_km: float) -> float:
        if dist_km > self.highway_threshold_km and self.highway_speed_kmh > 0:
            return (dist_km / self.highway_speed_kmh) * 60.0
        return (dist_km / self.avg_spd) * 60.0 if self.avg_spd > 0 else 0.0

    def _simulate_leg(self, cur_t: float, prev_idx: int, loc_idx: int,
                       tw_open: float, svc: float) -> tuple[float, float, float]:
        """
        時刻cur_t・直前地点prev_idxから loc_idx へ向かう1区間を進めた場合の
        (素の到着時刻, サービス開始時刻(TW待ち反映後), サービス終了時刻) を返す。
        割当可否の仮チェック（本割当前）と、確定ルートの明細作成の両方で使う共通ロジック。
        """
        seg = self.dist[prev_idx][loc_idx]
        arrive_raw = cur_t + self._travel_min(seg)
        start_service = max(arrive_raw, tw_open)
        finish_service = start_service + svc
        return arrive_raw, start_service, finish_service
