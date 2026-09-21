"""
RideshareMatchingPlannerSolver — CP Optimizer による相乗りマッチングプランナー
====================================================================================

solver_input keys:
  passengers, drivers, config, dist_matrix, locations, issue_statuses

CSPLib prob060 準拠。

決定変数:
  - assign_pd[p_idx][d_idx] : optional interval_var（存在すれば乗客p→運転手d割当）
  - pickup_pd[p_idx][d_idx] : optional interval_var（乗車イベント）
  - dropoff_pd[p_idx][d_idx]: optional interval_var（降車イベント）
  - depot_start_d / depot_end_d : mandatory interval_var（運転手dの出発地点・目的地点。
    2026-08-16追加。運転手固有の始点・終点をsequence_var上のfirst/last固定点として
    表現し、実際の走行距離をtype_of_next+elementで積算するために導入）
  - seq_d[d_idx]            : sequence_var（運転手dの巡回順序。要素の type は
    ロケーションindex。2026-08-16変更: 以前はp_idx由来の識別子だったが、
    type_of_next()による距離集計に使うためロケーションindexに変更。
    2026-09-21修正: no_overlap(seq)に地点間移動時間のtransition matrix(tm)が
    渡されておらず、同一運転手の乗車・降車イベント間で実移動時間が一切
    要求されていなかった欠陥を修正。TruckDispatcherのno_overlap(seq, tm)
    パターンに倣い、dist_matrix由来のtravel_matrix/build_cpo_transition_matrix
    をno_overlapに渡すよう変更）

目的関数: lexicographic
  1位: 未割当乗客数の最小化
  2位: 運転手全体の走行距離合計の最小化
    2026-08-16修正: 以前は「担当乗客のpickup→dropoff直接距離の合計」という近似で、
    運転手固有の出発地点・目的地点や実際の巡回順序を反映していなかった
    （ヒアリング§3・§4の必須要件が未実装というGate2指摘を受けての修正）。
    現在はdepot_start_d（出発地点）→...（巡回順序通りに各pickup/dropoff）...→
    depot_end_d（目的地点）の実際の経路に沿った距離をtype_of_next()+element()で
    積算する、CP Optimizerの標準的なsequence_var距離集計パターンに変更した。
"""

import logging
import math
from typing import Any, Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)

_SOLVER_VERSION = "rideshare_matching_planner_v1.1"
_DEFAULT_TIME_LIMIT = 60  # 秒
_INF = 9999999


class RideshareMatchingPlannerSolver:
    def __init__(self, solver_input: dict) -> None:
        self.dsl = solver_input

    def solve(self) -> dict:
        from i18n.rideshare_matching_planner_messages import t

        passengers = self.dsl.get("passengers", [])
        drivers = self.dsl.get("drivers", [])
        config = self.dsl.get("config", {})
        dist_matrix = self.dsl.get("dist_matrix", [])
        locations = self.dsl.get("locations", [])
        issue_statuses = self.dsl.get("issue_statuses", {})

        if not passengers:
            return self._make_result(
                feasible=False,
                solutions=[],
                issues=[{
                    "id": "solve_failed",
                    "severity": "CRITICAL",
                    "category": "INPUT",
                    "title": t("issue.no_passengers.title"),
                    "message": t("issue.no_passengers.message"),
                    "relatedContainerIds": [],
                }],
            )

        if not drivers:
            return self._make_result(
                feasible=False,
                solutions=[],
                issues=[{
                    "id": "solve_failed",
                    "severity": "CRITICAL",
                    "category": "INPUT",
                    "title": t("issue.no_drivers.title"),
                    "message": t("issue.no_drivers.message"),
                    "relatedContainerIds": [],
                }],
            )

        try:
            solution_data, issues = self._build_and_solve(
                passengers, drivers, config, dist_matrix, locations, issue_statuses
            )
        except _CeLimitError:
            return self._make_result(
                feasible=False,
                solutions=[],
                issues=[{
                    "id": "solve_failed",
                    "severity": "CRITICAL",
                    "category": "SOLVER",
                    "title": t("issue.ce_limit.title"),
                    "message": t("issue.ce_limit.message"),
                    "relatedContainerIds": [],
                }],
            )
        except Exception as e:
            logger.error(f"[RideshareMatchingPlanner] mdl.solve() 例外: {e}", exc_info=True)
            from solvers.base.solver_error_result import build_solver_crash_issue, solver_crash_extra_fields
            result = self._make_result(
                feasible=False,
                solutions=[],
                issues=[build_solver_crash_issue(e)],
            )
            result.update(solver_crash_extra_fields(e))
            return result

        if solution_data is None:
            return self._make_result(
                feasible=False,
                solutions=[],
                issues=[{
                    "id": "solve_failed",
                    "severity": "CRITICAL",
                    "category": "INFEASIBLE",
                    "title": t("issue.infeasible.title"),
                    "message": t("issue.infeasible.message"),
                    "relatedContainerIds": [],
                }],
            )

        from solvers.base.issue_rules import build_full_unassignment_issue
        anomaly = build_full_unassignment_issue(
            assigned_count=solution_data["matched_count"],
            total_count=len(passengers),
            entity_label=t("solver.passengerLabel"),
            extra_hint="get_var_solution()による解抽出処理",
        )
        if anomaly:
            issues.insert(0, anomaly)

        solution = self._build_solution(solution_data, passengers, drivers, config, t)
        return self._make_result(
            feasible=True,
            solutions=[solution],
            issues=issues,
        )

    # ------------------------------------------------------------------ #
    # コアソルブ
    # ------------------------------------------------------------------ #

    def _build_and_solve(
        self,
        passengers: List[Dict],
        drivers: List[Dict],
        config: Dict,
        dist_matrix: List[List[float]],
        locations: List[Dict],
        issue_statuses: Dict,
    ) -> Tuple[Optional[Dict], List[Dict]]:
        from docplex.cp.model import CpoModel
        from docplex.cp.modeler import build_cpo_transition_matrix
        from solvers.base.ce_limit_lns import is_ce_limit_exceeded, CeLimitExceededError as BaseCeLimitError

        mdl = CpoModel(name="RideshareMatchingPlanner")

        time_limit = int(config.get("time_limit_sec", _DEFAULT_TIME_LIMIT))
        detour_factor = float(config.get("detour_factor", 1.5))
        horizon = int(config.get("horizon_min", 1440))  # 分単位

        n_pass = len(passengers)
        n_driv = len(drivers)

        # --- ロケーションインデックスの構築 ---
        loc_idx_map: Dict[str, int] = {loc["id"]: i for i, loc in enumerate(locations)}
        n_loc = len(locations)
        # type_of_next() + element() による距離集計用に、距離行列を1次元に平坦化
        # （n_loc==0 の異常系は後続のget_distが0.0を返すのみで、距離集計ループは
        # 該当ロケーションを持つドライバ・乗客が無ければ実質発火しない）
        flat_dist: List[float] = [0.0] * max(n_loc * n_loc, 1)
        for i in range(min(n_loc, len(dist_matrix))):
            row = dist_matrix[i]
            for j in range(min(n_loc, len(row))):
                flat_dist[i * n_loc + j] = row[j]

        def get_dist(loc_a: str, loc_b: str) -> float:
            i = loc_idx_map.get(loc_a, 0)
            j = loc_idx_map.get(loc_b, 0)
            if i < len(dist_matrix) and j < len(dist_matrix[i]):
                return dist_matrix[i][j]
            return 0.0

        def loc_type(loc_id: str) -> int:
            # 未知ロケーションIDは0番扱い（get_distと同じフォールバック方針）。
            # 距離行列側もflat_dist配列外参照を防ぐため同じ0フォールバックにしている。
            return loc_idx_map.get(loc_id, 0)

        # --- 乗客ごとの「直接移動所要時間(分)」を計算 ---
        # direct_time_p = dist(pickup_loc, dropoff_loc) を速度で割った値
        # speed_kmh が config になければ dist 値を分として直接使用
        speed = float(config.get("speed_kmh", 0))

        def dist_to_min(km: float) -> int:
            if speed > 0:
                return max(1, int(math.ceil(km / speed * 60)))
            return max(1, int(math.ceil(km)))

        # --- 地点間の移動時間行列（no_overlapのtransition matrix用） ---
        # 2026-09-21修正: (6)のno_overlap(seq)に移動時間行列が渡されておらず、
        # 同一運転手の乗車・降車イベント間で実際の移動時間が一切要求されていなかった
        # （TruckDispatcherのno_overlap(seq, tm)パターンが未適用だった）。
        # dist_to_min()は他箇所（direct_min等）向けにmax(1, ceil(...))のフロアを
        # 持つが、ここでは同一地点間の連続イベントに人為的な1分待ちを強制しないよう、
        # TruckDispatcherのtravel_min()に倣いフロア無しのround()を使う。
        def _travel_min_between(loc_a_idx: int, loc_b_idx: int) -> int:
            if loc_a_idx == loc_b_idx:
                return 0
            km = flat_dist[loc_a_idx * n_loc + loc_b_idx]
            if speed > 0:
                return int(round(km / speed * 60))
            return int(round(km))

        travel_matrix = [[_travel_min_between(i, j) for j in range(n_loc)] for i in range(n_loc)]
        tm = build_cpo_transition_matrix(travel_matrix)

        # --- Interval変数の生成 ---
        # pickup_itvs[p][d], dropoff_itvs[p][d]: optional interval_var
        # assignment_itvs[p][d]: optional interval_var（割当の有無を表す0長区間）
        pickup_itvs: List[List[Any]] = []
        dropoff_itvs: List[List[Any]] = []
        assignment_itvs: List[List[Any]] = []
        ride_itvs: List[List[Any]] = []

        for p_idx, p in enumerate(passengers):
            pu_row, do_row, asgn_row, ride_row = [], [], [], []
            w_start = int(p.get("window_start_min", 0))
            w_end = int(p.get("window_end_min", horizon))
            p_loc = p.get("pickup_loc", "")
            d_loc_p = p.get("dropoff_loc", "")
            direct_km = get_dist(p_loc, d_loc_p)
            direct_min = dist_to_min(direct_km)
            max_ride_min = int(math.ceil(direct_min * detour_factor))

            for d_idx, d in enumerate(drivers):
                depart_min_d = int(d.get("depart_min", 0))
                arrive_max_d = int(d.get("arrive_max", horizon))
                # pickup の開始下限: 乗客の希望開始時刻 と 運転手の出発可能時刻 の大きい方
                pu_start_lb = max(w_start, depart_min_d)
                # pickup の開始上限: 乗客の希望終了時刻
                pu_start_ub = w_end
                # dropoff の終了上限: 運転手の到着希望時刻
                do_end_ub = arrive_max_d

                # 乗車イベント: 所要時間1分。start の時間窓制約は optional interval の
                # absent値誤用を避けるため interval_var 生成時には指定せず、
                # 後段の (7) で if_then(presence_of, ...) により条件付きで追加する。
                pu = mdl.interval_var(
                    optional=True,
                    size=1,
                    name=f"pickup_p{p_idx}_d{d_idx}",
                )
                # 降車イベント: 所要時間1分。end の時間窓制約も同様に後段 (7) で追加。
                # optional interval に start= / end= を直接指定すると absent 時にも
                # 制約が強制されて infeasible になる既知の問題を回避する。
                do = mdl.interval_var(
                    optional=True,
                    size=1,
                    name=f"dropoff_p{p_idx}_d{d_idx}",
                )
                # 割当変数: 0長区間（presence_of が割当フラグに相当）
                asgn = mdl.interval_var(
                    optional=True,
                    size=0,
                    name=f"assign_p{p_idx}_d{d_idx}",
                )
                # 乗車区間（座席容量制約用）: pu.start 〜 do.end を span する。
                # 2026-08-16修正: 以前は mdl.start_of(ride, 0) == mdl.start_of(pu, 0) の
                # ような「absentなら0扱い」の等値制約で組み立てており、Gate2静的チェックで
                # optional interval の absent値誤用の疑いとして5件指摘された。
                # mdl.span(ride, [pu, do]) はCP Optimizerの標準関数で、
                # 「ride は pu の開始から do の終了までをspanし、pu・do の両方が absent
                # の場合にのみ ride も absent」という presence 込みの意味論を持つため、
                # 手動のstart_of/end_of等値制約より安全（absent値の取り扱いを
                # モデラー側で明示的に書く必要がない）。
                ride = mdl.interval_var(
                    optional=True,
                    name=f"ride_p{p_idx}_d{d_idx}",
                )
                pu_row.append(pu)
                do_row.append(do)
                asgn_row.append(asgn)
                ride_row.append(ride)
            pickup_itvs.append(pu_row)
            dropoff_itvs.append(do_row)
            assignment_itvs.append(asgn_row)
            ride_itvs.append(ride_row)

        # --- 制約の追加 ---

        # (1) 各乗客は高々1人の運転手に割り当てる（未割当を許容するsoft）
        for p_idx in range(n_pass):
            asgn_sum = mdl.sum([mdl.presence_of(assignment_itvs[p_idx][d_idx]) for d_idx in range(n_driv)])
            mdl.add(asgn_sum <= 1)

        # (2) pickup, dropoff, assign の一貫性:
        #     assign が absent なら pickup, dropoff も absent。assign が present なら両方 present。
        for p_idx in range(n_pass):
            for d_idx in range(n_driv):
                asgn = assignment_itvs[p_idx][d_idx]
                pu = pickup_itvs[p_idx][d_idx]
                do = dropoff_itvs[p_idx][d_idx]
                # assign ↔ pickup, dropoff の同期
                mdl.add(mdl.presence_of(pu) == mdl.presence_of(asgn))
                mdl.add(mdl.presence_of(do) == mdl.presence_of(asgn))

        # (3) 乗車前置ペア制約: pickup → dropoff
        for p_idx in range(n_pass):
            for d_idx in range(n_driv):
                pu = pickup_itvs[p_idx][d_idx]
                do = dropoff_itvs[p_idx][d_idx]
                mdl.add(mdl.end_before_start(pu, do))

        # (4) 乗車時間上限(detour)制約
        # 2026-08-16修正: 以前は mdl.start_of(do, 0) - mdl.end_of(pu, 0) <= ... という
        # 「absent時は0がデフォルトで返る」2引数形式を比較演算子に直結する書き方で、
        # Gate2静的チェック（_check_optional_interval_absent_value）がoptional
        # interval_varのabsent値誤用として検出する既知の危険パターンに該当していた
        # （ビッグM項で数値的には無害化されていたが、パターン自体が過去に実害を
        # 出した書き方と一致するため機械的に検出される）。
        # mdl.if_then(presence_of, ...) + 1引数のstart_of/end_of に書き換えることで、
        # 「presentのときのみ制約が有効」という意味をCP Optimizerの標準イディオムで
        # 明示する（absentのときはif_then自体がvacuously trueになるため、absent時の
        # start_of/end_ofの値がそもそも評価されない）。
        for p_idx, p in enumerate(passengers):
            p_loc = p.get("pickup_loc", "")
            d_loc_p = p.get("dropoff_loc", "")
            direct_km = get_dist(p_loc, d_loc_p)
            direct_min = dist_to_min(direct_km)
            max_ride_min = int(math.ceil(direct_min * detour_factor))
            for d_idx in range(n_driv):
                pu = pickup_itvs[p_idx][d_idx]
                do = dropoff_itvs[p_idx][d_idx]
                asgn = assignment_itvs[p_idx][d_idx]
                # 割り当てられている場合のみ: do.start - pu.end <= max_ride_min
                mdl.add(mdl.if_then(
                    mdl.presence_of(asgn),
                    mdl.start_of(do) - mdl.end_of(pu) <= max_ride_min,
                ))

        # (5) 座席容量制約（cumulative: 乗車+1、降車-1）
        # no_overlap_cumulative_conflict: exempt（2026-09-21, Koshoshi確認済み）
        # このpulse制約は下記(6)のsequence_var/no_overlapとは別資源に対する制約。
        # (5)はride_itvs（乗車区間、pu.startからdo.endまでspanする実際の乗車時間、
        # 座席占有の概念）に対する容量制約。(6)のno_overlapはpickup_itvs/
        # dropoff_itvs（乗車・降車という一瞬の点イベント、size=1）のみを対象と
        # しており、ride_itvs自体はno_overlapの対象集合(all_itvs_d)に含まれない。
        # したがってPatientTransportPlannerのバグ（no_overlapが同一資源の
        # cumulativeを常にデッドコード化する）とは構造が異なり、相乗り容量制約は
        # 正しく機能する（禁止パターン7の静的チェックはファイル内の共存のみを見る
        # ヒューリスティックのため誤検知するが、目視確認済み）。
        for d_idx, d in enumerate(drivers):
            capacity = int(d.get("seats", 4))
            pulses = []
            for p_idx in range(n_pass):
                pu = pickup_itvs[p_idx][d_idx]
                do = dropoff_itvs[p_idx][d_idx]
                ride = ride_itvs[p_idx][d_idx]
                mdl.add(mdl.span(ride, [pu, do]))
                mdl.add(mdl.presence_of(ride) == mdl.presence_of(assignment_itvs[p_idx][d_idx]))
                pulses.append(mdl.pulse(ride, 1))

            if pulses:
                mdl.add(mdl.sum(pulses) <= capacity)

        # (6) 運転手固有の始点・終点（depotペア）＋ sequence_var + no_overlap
        # 2026-08-16追加: 運転手dはdepot_start_d（出発地点=d.start_loc、出発可能時刻）から
        # 出発し、担当する乗客のpickup/dropoffを巡回順序通りに経由し、
        # depot_end_d（目的地点=d.end_loc、到着希望時刻）で運行を終える。
        # depot_start_d/depot_end_dは常にpresent（mandatory）のsize=0区間とし、
        # mdl.first()/mdl.last()でsequence_var上の位置を固定することで、
        # 「運転手ごとの始点・終点」というヒアリング§4の必須要件をルート構造として
        # 表現する（以前のバージョンでは時間制約のみで、経路上の始点・終点は
        # 未実装だった）。
        seq_vars = []
        depot_starts: List[Any] = []
        depot_ends: List[Any] = []
        depot_end_types: List[int] = []
        for d_idx, d in enumerate(drivers):
            depart_min = int(d.get("depart_min", 0))
            arrive_max = int(d.get("arrive_max", horizon))
            start_loc_id = d.get("start_loc", "")
            end_loc_id = d.get("end_loc", "")

            depot_start = mdl.interval_var(
                optional=False, size=0, start=(depart_min, depart_min),
                name=f"depot_start_d{d_idx}",
            )
            depot_end = mdl.interval_var(
                optional=False, size=0, start=(arrive_max, arrive_max),
                name=f"depot_end_d{d_idx}",
            )
            depot_starts.append(depot_start)
            depot_ends.append(depot_end)
            depot_end_types.append(loc_type(end_loc_id))

            all_itvs_d = [depot_start, depot_end]
            types_d = [loc_type(start_loc_id), loc_type(end_loc_id)]
            for p_idx in range(n_pass):
                p = passengers[p_idx]
                all_itvs_d.append(pickup_itvs[p_idx][d_idx])
                types_d.append(loc_type(p.get("pickup_loc", "")))
                all_itvs_d.append(dropoff_itvs[p_idx][d_idx])
                types_d.append(loc_type(p.get("dropoff_loc", "")))

            if len(all_itvs_d) > 0:
                seq = mdl.sequence_var(all_itvs_d, types=types_d, name=f"seq_d{d_idx}")
                mdl.add(mdl.no_overlap(seq, tm))
                mdl.add(mdl.first(seq, depot_start))
                mdl.add(mdl.last(seq, depot_end))
                seq_vars.append(seq)
            else:
                seq_vars.append(None)

        # (7) 時間窓制約（pickup/dropoffの時間窓。(6)のdepotは
        # ルート構造・距離集計用で、こちらは各イベントの絶対時刻の下限・上限）
        # driver.depart_min: 出発可能時刻、driver.arrive_max: 到着希望時刻
        # passenger.window_start_min / window_end_min: 乗客の希望乗車時間帯
        for d_idx, d in enumerate(drivers):
            depart_min = int(d.get("depart_min", 0))
            arrive_max = int(d.get("arrive_max", horizon))
            for p_idx, p in enumerate(passengers):
                pu = pickup_itvs[p_idx][d_idx]
                do = dropoff_itvs[p_idx][d_idx]
                w_start = int(p.get("window_start_min", 0))
                w_end = int(p.get("window_end_min", horizon))
                # pickup は運転手の出発可能時刻以降、かつ乗客の希望開始時刻以降に始まる
                # if_then + 1引数start_of/end_ofに統一（(4)と同じ理由）。
                mdl.add(mdl.if_then(mdl.presence_of(pu), mdl.start_of(pu) >= max(depart_min, w_start)))
                # pickup は乗客の希望終了時刻以前に始まる（希望時間帯内乗車制約）
                mdl.add(mdl.if_then(mdl.presence_of(pu), mdl.start_of(pu) <= w_end))
                # dropoff は到着希望時刻以前に終わる
                mdl.add(mdl.if_then(mdl.presence_of(do), mdl.end_of(do) <= arrive_max))

        # --- 目的関数（lexicographic） ---
        # 1位: 未割当乗客数の最小化
        unmatched_terms = []
        for p_idx in range(n_pass):
            assigned_p = mdl.sum([mdl.presence_of(assignment_itvs[p_idx][d_idx]) for d_idx in range(n_driv)])
            unmatched_terms.append(1 - assigned_p)
        obj1 = mdl.sum(unmatched_terms)

        # 2位: 運転手全体の走行距離の最小化（実際の巡回順序に基づく合計距離）
        # 2026-08-16修正: type_of_next() + element() を使い、各運転手の実際の経路
        # （depot_start_d → ... 巡回順序通りの pickup/dropoff ... → depot_end_d）に
        # 沿った区間距離を合計する。CP Optimizerのsequence_var距離集計の標準パターン。
        # 未割当（absent）のpickup/dropoffはpresence_ofで0にガードし、目的関数に
        # 寄与しないようにする。
        dist_terms = []
        for d_idx, d in enumerate(drivers):
            seq = seq_vars[d_idx]
            if seq is None:
                continue
            depot_end_t = depot_end_types[d_idx]

            # depot_start（常にpresent）からの区間
            start_t = loc_type(d.get("start_loc", ""))
            next_t = mdl.type_of_next(seq, depot_starts[d_idx], lastValue=depot_end_t, absentValue=start_t)
            idx_expr = start_t * n_loc + next_t
            dist_terms.append(mdl.element(flat_dist, idx_expr))

            for p_idx in range(n_pass):
                p = passengers[p_idx]
                asgn = assignment_itvs[p_idx][d_idx]
                pu = pickup_itvs[p_idx][d_idx]
                do = dropoff_itvs[p_idx][d_idx]
                pu_t = loc_type(p.get("pickup_loc", ""))
                do_t = loc_type(p.get("dropoff_loc", ""))

                pu_next_t = mdl.type_of_next(seq, pu, lastValue=depot_end_t, absentValue=pu_t)
                pu_idx_expr = pu_t * n_loc + pu_next_t
                dist_terms.append(mdl.presence_of(asgn) * mdl.element(flat_dist, pu_idx_expr))

                do_next_t = mdl.type_of_next(seq, do, lastValue=depot_end_t, absentValue=do_t)
                do_idx_expr = do_t * n_loc + do_next_t
                dist_terms.append(mdl.presence_of(asgn) * mdl.element(flat_dist, do_idx_expr))

        obj2 = mdl.sum(dist_terms) if dist_terms else 0

        mdl.add(mdl.minimize_static_lex([obj1, obj2]))

        # --- ソルブ ---
        try:
            msol = mdl.solve(TimeLimit=time_limit, LogVerbosity="Quiet")
        except Exception as e:
            from solvers.base.ce_limit_lns import is_ce_limit_exceeded
            if is_ce_limit_exceeded(e):
                raise _CeLimitError(str(e)) from e
            raise

        if msol is None or not msol:
            return None, []

        # --- 解の抽出 ---
        matched_pairs: List[Dict] = []
        unmatched_passengers: List[Dict] = []

        for p_idx, p in enumerate(passengers):
            assigned_d_idx = None
            for d_idx in range(n_driv):
                asgn_sol = msol.get_var_solution(assignment_itvs[p_idx][d_idx])
                if asgn_sol is not None and asgn_sol.is_present():
                    assigned_d_idx = d_idx
                    break
            if assigned_d_idx is None:
                unmatched_passengers.append(p)
                continue

            d = drivers[assigned_d_idx]
            pu_sol = msol.get_var_solution(pickup_itvs[p_idx][assigned_d_idx])
            do_sol = msol.get_var_solution(dropoff_itvs[p_idx][assigned_d_idx])
            pickup_min = pu_sol.get_start() if pu_sol else 0
            dropoff_min = do_sol.get_start() if do_sol else 0

            # 直接所要時間
            p_loc = p.get("pickup_loc", "")
            d_loc_p = p.get("dropoff_loc", "")
            direct_km = get_dist(p_loc, d_loc_p)
            direct_min = dist_to_min(direct_km)

            matched_pairs.append({
                "passenger_id": p.get("id", f"p{p_idx}"),
                "passenger_name": p.get("name", f"乗客{p_idx+1}"),
                "driver_id": d.get("id", f"d{assigned_d_idx}"),
                "driver_name": d.get("name", f"運転手{assigned_d_idx+1}"),
                "pickup_min": pickup_min,
                "dropoff_min": dropoff_min,
                "ride_min": dropoff_min - pickup_min,
                "direct_min": direct_min,
                "pickup_loc": p_loc,
                "dropoff_loc": d_loc_p,
                "window_start_min": int(p.get("window_start_min", 0)),
                "window_end_min": int(p.get("window_end_min", horizon)),
            })

        # 運転手ごとの担当一覧・走行距離
        # 2026-08-16修正: 以前は「start_loc→end_loc直距離 + 各乗客のpickup→dropoff
        # 直接距離の合計」という、実際の巡回順序を反映しない近似だった。
        # 現在は実際に解ソルバーが決定した訪問順序（pickup_min・dropoff_min で
        # ソートした実時刻順）に沿って start_loc → 1件目の地点 → ... → end_loc の
        # 区間距離を積算する。これはsequence_var上の実際の順序（no_overlapにより
        # 保証される）と一致するため、目的関数（obj2）が最小化している距離と
        # 整合したレポーティング値になる。
        driver_routes: Dict[str, Dict] = {}
        for d_idx, d in enumerate(drivers):
            did = d.get("id", f"d{d_idx}")
            passengers_for_d = [mp for mp in matched_pairs if mp["driver_id"] == did]
            passengers_for_d.sort(key=lambda x: x["pickup_min"])

            # 訪問順序に沿ったロケーション列（乗車→降車がpickup→dropoffの順序で
            # 個別に並ぶわけではなく、実際にはno_overlapで決まった時刻順にpickup/
            # dropoffイベントが混在するため、pickup/dropoffそれぞれのイベント時刻で
            # 個別にソートする）
            events = []
            for mp in passengers_for_d:
                events.append((mp["pickup_min"], mp["pickup_loc"]))
                events.append((mp["dropoff_min"], mp["dropoff_loc"]))
            events.sort(key=lambda x: x[0])

            route_locs = [d.get("start_loc", "")] + [loc for _, loc in events] + [d.get("end_loc", "")]
            total_km = 0.0
            for i in range(len(route_locs) - 1):
                total_km += get_dist(route_locs[i], route_locs[i + 1])

            driver_routes[did] = {
                "driver_id": did,
                "driver_name": d.get("name", f"運転手{d_idx+1}"),
                "seats": int(d.get("seats", 4)),
                "start_loc": d.get("start_loc", ""),
                "end_loc": d.get("end_loc", ""),
                "depart_min": int(d.get("depart_min", 0)),
                "arrive_max": int(d.get("arrive_max", horizon)),
                "passengers": passengers_for_d,
                "passenger_count": len(passengers_for_d),
                "total_km": round(total_km, 2),
            }

        matched_count = len(matched_pairs)
        unmatched_count = len(unmatched_passengers)

        from solvers.base.solution_extraction import extract_optimality_metadata
        optimality = extract_optimality_metadata(msol)

        issues = self._detect_issues(
            matched_pairs, unmatched_passengers, driver_routes, drivers, issue_statuses, passengers, config
        )

        return {
            "matched_pairs": matched_pairs,
            "unmatched_passengers": unmatched_passengers,
            "driver_routes": list(driver_routes.values()),
            "matched_count": matched_count,
            "unmatched_count": unmatched_count,
            "total_passengers": len(passengers),
            "total_drivers": len(drivers),
            "match_rate": round(matched_count / max(len(passengers), 1), 4),
            "optimality": optimality,
        }, issues

    # ------------------------------------------------------------------ #
    # Issue 検知
    # ------------------------------------------------------------------ #

    def _detect_issues(
        self,
        matched_pairs: List[Dict],
        unmatched_passengers: List[Dict],
        driver_routes: Dict[str, Dict],
        drivers: List[Dict],
        issue_statuses: Dict,
        passengers: List[Dict],
        config: Dict,
    ) -> List[Dict]:
        from i18n.rideshare_matching_planner_messages import t
        from solvers.base.issue_rules import build_rideshare_matching_planner_contexts, run_issue_rules
        issues = []

        # 未割当乗客 警告
        for p in unmatched_passengers:
            pid = p.get("id", "?")
            iid = f"unmatched_passenger_{pid}"
            if issue_statuses.get(iid) == "ACCEPTED":
                continue
            issues.append({
                "id": iid,
                "severity": "WARNING",
                "category": "MATCHING",
                "title": t("issue.unmatched_passenger.title", name=p.get("name", pid)),
                "message": t("issue.unmatched_passenger.message", name=p.get("name", pid)),
                "relatedContainerIds": [],
            })

        # 解チェッカー（2026-09-01追加、バッチ5）: 座席容量（スイープライン検算）・乗車時間上限・
        # 乗降順序・乗客時間窓・運転手稼働時間窓・乗客重複割当・乗客欠落の独立検証。
        # 旧「座席使用率 > 座席数」チェックは総数の近似で実際の同時刻重複を見ておらず、置き換える。
        checker_ctxs = build_rideshare_matching_planner_contexts(
            matched_pairs, unmatched_passengers, driver_routes, passengers, config,
        )
        issues.extend(run_issue_rules(
            domain="RideshareMatchingPlanner", contexts=checker_ctxs, issue_statuses=issue_statuses,
        ))

        return issues

    # ------------------------------------------------------------------ #
    # 解の組み立て
    # ------------------------------------------------------------------ #

    def _build_solution(
        self,
        solution_data: Dict,
        passengers: List[Dict],
        drivers: List[Dict],
        config: Dict,
        t,
    ) -> Dict:
        matched_count = solution_data["matched_count"]
        unmatched_count = solution_data["unmatched_count"]
        total = solution_data["total_passengers"]
        match_rate = solution_data["match_rate"]

        total_km = sum(dr["total_km"] for dr in solution_data["driver_routes"])

        kpi = {
            "matched_count": matched_count,
            "unmatched_count": unmatched_count,
            "total_passengers": total,
            "match_rate": round(match_rate * 100, 1),
            # 2026-08-16追加: Gate2動的検証（退化解検知・infeasibleシナリオの
            # coverage判定）が参照する共通KPI慣習キー。match_rateと同じ値を
            # 0.0〜1.0のfraction形式で追加する（match_rateは0〜100のパーセント表記
            # のためscaleが異なり、既存の表示用KPIとしてそのまま残す）。
            "coverage_rate": round(match_rate, 4),
            "total_km": round(total_km, 2),
            "driver_count": solution_data["total_drivers"],
            "solve_time": solution_data["optimality"].get("solve_time_sec", 0.0),
            "is_optimal": solution_data["optimality"].get("is_optimal", False),
        }

        return {
            "name": "Plan A",
            "label": t("solver.planLabel"),
            "feasible": True,
            "matched_pairs": solution_data["matched_pairs"],
            "unmatched_passengers": solution_data["unmatched_passengers"],
            "driver_routes": solution_data["driver_routes"],
            "kpi": kpi,
            # 2026-08-16追加: run_gate2_dynamic_verification()はsolutions[0]["metrics"]
            # を読む規約（nursing_workload_balance_solver.py等の既存ドメインと同じ
            # キー名）。本ドメインは画面表示用に"kpi"というキー名で生成されていた
            # ため、Gate2のcoverage_rate参照が空振りしていた（実害: infeasibleシナリオ
            # の未割当許容判定ができず、期待feasible=Falseの一律チェックに引っかかる
            # false positiveの原因になっていた）。"kpi"はUI表示用にそのまま残し、
            # "metrics"はGate2互換のため同じ辞書を指す形で追加する（値は完全に同一）。
            "metrics": kpi,
        }

    # ------------------------------------------------------------------ #
    # 結果フォーマット
    # ------------------------------------------------------------------ #
    def _make_result(
        self,
        feasible: bool,
        solutions: List[Dict],
        issues: List[Dict],
    ) -> Dict:
        return {
            "status": "ok",
            "feasible": feasible,
            "metadata": {"problem_class": "RideshareMatchingPlanner"},
            "solutions": solutions,
            "issues": issues,
            "_solver_version": _SOLVER_VERSION,
            "solve_time_sec": (
                solutions[0]["kpi"].get("solve_time", 0.0)
                if solutions and "kpi" in solutions[0]
                else 0.0
            ),
        }

