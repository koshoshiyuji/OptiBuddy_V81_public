"""
car_sequencing_solver.py — CarSequencing ソルバー (v1.0)

問題: CSPLib prob001 Car Sequencing
      自動車組立ラインへの投入順序最適化

# solver_input keys:
#   problem_class, car_types, options, config, issue_statuses, meta

解法: docplex.cp (CP Optimizer)
  - integer_var の配列で各位置の車種を表現（位置ベース変数）
  - スライディングウィンドウ制約をソフト化（超過をペナルティ化）
  - 同一車種連続超過もソフトペナルティ化
  - ハード制約: 各車種の生産台数（count_of）

目的関数:
  minimize Σ(オプション違反台数 × w_option=10) + Σ(同一車種連続超過台数 × w_same=1)

同一車種連続超過の定義（ヒアリング指摘1回答 (a) 採用）:
  同一車種が連続する各区間について、超過数 = max(0, 連続長 - max_consecutive_same) を計算し、
  全区間・全車種の超過数の合計にペナルティ重み1を掛けて目的関数に加算する。
  （例: max_consecutive_same=2 のとき、3台連続→超過1、4台連続→超過2）
  実装上は、連続する3台以上の区間を「run」として検出し、
  各 run の長さ L に対して max(0, L - max_consecutive_same) を超過数とする。
  CP Optimizer では run 長を直接扱いにくいため、以下の等価な方法を採用:
    pos が「同一車種の連続区間の (max_consecutive_same+1) 台目以降」であれば超過1をカウント。
    すなわち、seq[pos] == seq[pos-1] == ... == seq[pos-max_consecutive_same] が成立する
    各 pos (pos >= max_consecutive_same) を超過1としてカウントする。
  これにより、連続長 L の区間では max(0, L - max_consecutive_same) 個の pos が該当し、
  超過数の合計が正しく計算される。

total_slots チェック（ヒアリング6-1節対応）:
  config に total_slots が指定された場合（None でない場合）、需要合計（sum of car_types[].count）と
  total_slots が一致しなければ infeasible を返す。
  これにより「需要合計 ≠ スロット数」という意図的なデータ矛盾を検出できる。
"""

from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional, Tuple

from solvers.base.solver_error_result import build_solver_crash_issue, solver_crash_extra_fields
from solvers.base.engine_select import get_solver_engine, CPO, CPSAT, cpmpy_optimality_metadata

logger = logging.getLogger(__name__)

_SOLVER_VERSION = "car_sequencing_v1.0"

# ペナルティ重み（ヒアリング記載の加重スカラー化）
W_OPTION_VIOLATION = 10
W_SAME_CAR_VIOLATION = 1

# 連続台数上限（「3台以上連続しないようにしたい」→ 2台まで許容、3台目以降を超過とする）
DEFAULT_MAX_CONSECUTIVE_SAME = 2  # 2台まで連続OK、3台目以降がペナルティ対象


class CarSequencingSolver:
    """
    CarSequencing (CSPLib prob001) の CP Optimizer ソルバー。

    各オプション工程の「連続q台中p台まで」制約違反を最小化しながら、
    車種ごとの生産台数を過不足なく充足する順序を求める。
    """

    def __init__(self, solver_input: dict) -> None:
        self.dsl = solver_input

    def solve(self) -> dict:
        try:
            return self._solve_internal()
        except Exception as e:
            logger.error(f"[CarSequencing] ソルバー例外: {e}", exc_info=True)
            # [2026-07-28] id は "solve_failed" に統一する（このファイル自身の
            # コメント参照: 独自idにすると制約見直しタブがFeasible誤判定する）。
            # title/message とトップレベルの _solver_crashed マーカーで、
            # 業務上のinfeasibleとコードの例外を区別できるようにする
            # （solvers/base/solver_error_result.py 参照）。
            result = self._make_result(
                feasible=False,
                sequence=[],
                schedule=[],
                violations=[],
                kpi={},
                issues=[build_solver_crash_issue(e)],
                status="infeasible",
            )
            result.update(solver_crash_extra_fields(e))
            return result

    def _solve_internal(self) -> dict:
        # 2026-08-13: docplex.cp関連のimportは_solve_with_cpo()側に移した
        # （engine="cpsat"選択時にdocplex/CPLEXが未インストールの環境でも
        # importエラーにならないようにするため。CPLEXの無いOSS環境でCP-SAT
        # フォールバックを使う、というのが本対応の目的そのものなので、ここで
        # 無条件にdocplex.cp.model.CpoModelをimportしてしまうと本末転倒になる）。
        from solvers.base.issue_rules import (
            build_full_unassignment_issue, build_car_sequencing_contexts, run_issue_rules,
        )

        car_types: List[Dict] = self.dsl.get("car_types", [])
        options: List[Dict] = self.dsl.get("options", [])
        config: Dict = self.dsl.get("config", {})
        issue_statuses: Dict = self.dsl.get("issue_statuses", {})

        if not car_types:
            # id は "solve_failed" で統一する（Frontend/InfeasibleView.tsx が
            # issues[].id === "solve_failed" のみでInfeasible判定するため。
            # 2026-07-25: 独自idを使ったことでOverviewは正しくInfeasibleと
            # 表示される一方、制約見直しタブがFeasible誤判定する不具合が発覚。
            # truck_dispatcher_solver.py / meeting_room_solver.py と同じ規約に揃える）
            return self._make_result(
                feasible=False, sequence=[], schedule=[], violations=[], kpi={},
                issues=[{"id": "solve_failed", "severity": "CRITICAL",
                         "title": "車種定義がありません", "message": "car_types が空です。",
                         "relatedContainerIds": []}],
                status="infeasible",
            )

        # --- 入力整理 ---
        total_cars = sum(ct.get("count", 0) for ct in car_types)
        max_consecutive = config.get("max_consecutive_same", DEFAULT_MAX_CONSECUTIVE_SAME)
        solve_time_sec = config.get("solve_time_sec", 30)

        # total_slots チェック（ヒアリング6-1節: 需要合計 ≠ スロット数 → infeasible）
        total_slots = config.get("total_slots", None)
        if total_slots is not None and int(total_slots) != total_cars:
            # id は "solve_failed" で統一（上記no_car_typesと同じ理由。
            # 具体的な原因は message/title に残す）
            return self._make_result(
                feasible=False, sequence=[], schedule=[], violations=[], kpi={},
                issues=[{
                    "id": "solve_failed",
                    "severity": "CRITICAL",
                    "title": "需要台数とスロット数が一致しません",
                    "message": (
                        f"車種ごとの需要台数の合計（{total_cars}台）が"
                        f"ライン上のスロット総数（{int(total_slots)}台）と一致しません。"
                        f"生産計画データを確認してください。"
                    ),
                    "relatedContainerIds": [],
                }],
                status="infeasible",
            )

        n = total_cars  # シーケンス長 = 総生産台数

        if n == 0:
            # id は "solve_failed" で統一（上記と同じ理由）
            return self._make_result(
                feasible=False, sequence=[], schedule=[], violations=[], kpi={},
                issues=[{"id": "solve_failed", "severity": "CRITICAL",
                         "title": "生産台数がゼロです", "message": "全車種の count 合計が 0 です。",
                         "relatedContainerIds": []}],
                status="infeasible",
            )

        # car_type_id -> index (0-based)
        type_ids = [ct["car_type_id"] for ct in car_types]
        type_index = {tid: i for i, tid in enumerate(type_ids)}
        num_types = len(type_ids)

        # オプション情報: {option_id, max_per_window(p), window_size(q), car_type_ids}
        option_ids = [opt["option_id"] for opt in options]
        num_options = len(option_ids)

        # option i が car type j に必要か: option_has_type[i][j] = 0 or 1
        option_has_type: List[List[int]] = []
        for opt in options:
            required_types = set(opt.get("car_type_ids", []))
            row = [1 if tid in required_types else 0 for tid in type_ids]
            option_has_type.append(row)

        # オプションごとの「このオプションを必要とする車種インデックス」一覧。
        # KPI再計算・CPO/CP-SAT両エンジンの制約構築で共通に使う（エンジン非依存）。
        option_needs_idx: List[List[int]] = [
            [j for j, has in enumerate(option_has_type[k]) if has == 1]
            for k in range(num_options)
        ]
        option_needs_types: List[set] = [
            {type_ids[j] for j in option_needs_idx[k]} for k in range(num_options)
        ]

        # --- エンジン選択（2026-08-13追加: DESIGN_2026-08-12_cp_sat_backend_support.md）---
        # config.solver_engine="cpsat" が明示された場合のみOR-Tools CP-SAT
        # (CPMpy経由) を使う。既定は従来通りCPLEX CP Optimizer。
        engine = get_solver_engine(config)
        logger.info(f"[CarSequencing] n={n}, types={num_types}, options={num_options}, engine={engine}")
        if engine == CPSAT:
            sequence_result, obj_val, optimality, early = self._solve_with_cpsat(
                car_types, options, n, type_ids, type_index, num_types,
                option_needs_idx, max_consecutive, solve_time_sec,
            )
        else:
            sequence_result, obj_val, optimality, early = self._solve_with_cpo(
                car_types, options, n, type_ids, type_index, num_types,
                option_needs_idx, max_consecutive, solve_time_sec,
            )
        if early is not None:
            return early

        # 全件未割当チェック
        unassigned_anomaly = build_full_unassignment_issue(
            assigned_count=len(sequence_result),
            total_count=n,
            entity_label="車両スロット",
            extra_hint="seq変数のget_value()",
        )

        # --- KPI計算 ---
        # 2026-08-13: CP-SATエンジン追加に伴い、KPI計算は完全にエンジン非依存
        # （sequence_resultというPythonのプレーンなlistのみを見る）に統一した。
        # CPOの合成式に対する msol.get_value() には一切依存せず、常にソルブ確定後の
        # 実際の sequence_result からPython側で再計算する（元々2026-07-25:
        # get_value()がmdl.max()を含む合成式に対して失敗し常に0を返していた不具合の
        # 再発防止のために採られていた方針。CP-SAT側にも同じ理由でそのまま適用できる。
        # docs/HANDOFF_2026-07-25_car_sequencing_registration.md参照）。
        from solvers.base.rolling_window import (
            total_sliding_window_violation,
            compute_sliding_window_violations,
        )

        if obj_val is None:
            obj_val = 0

        violations_detail: List[Dict] = []
        total_opt_viol = 0
        seq_types_for_kpi = [item["car_type_id"] for item in sequence_result]
        for k, opt in enumerate(options):
            q = opt.get("window_size", 1)
            p = opt.get("max_per_window", 1)
            needs_opt_types = option_needs_types[k]
            v = 0
            windows_detail: List[Dict] = []
            if needs_opt_types and seq_types_for_kpi:
                values = [1 if t in needs_opt_types else 0 for t in seq_types_for_kpi]
                windows_detail = compute_sliding_window_violations(values, q, p)
                v = total_sliding_window_violation(values, q, p)
            total_opt_viol += v
            violations_detail.append({
                "option_id": opt["option_id"],
                "option_name": opt.get("option_name", opt["option_id"]),
                "violation_count": v,
                "window_size": q,
                "max_per_window": p,
                "windows": windows_detail,  # 窓ごとの内訳（将来のUI drill-down用）
            })

        # 同一車種連続超過台数
        # 定義: 連続長 L の区間で max(0, L - max_consecutive) 件の超過
        # （2026-08-13: エンジン非依存化に伴い、常にsequence_resultからのPython
        # 手計算に一本化した。定義はCPOの目的関数項と同一。）
        total_same_viol = 0
        if max_consecutive >= 1 and sequence_result:
            i = 0
            while i < len(sequence_result):
                j = i + 1
                while j < len(sequence_result) and \
                      sequence_result[j]["car_type_id"] == sequence_result[i]["car_type_id"]:
                    j += 1
                run_len = j - i
                total_same_viol += max(0, run_len - max_consecutive)
                i = j

        kpi = {
            "total_cars": total_cars,
            "option_violation_total": total_opt_viol,
            "same_car_violation_total": total_same_viol,
            "objective_value": float(obj_val),
            "solve_time_sec": optimality.get("solve_time_sec"),
            "is_optimal": optimality.get("is_optimal", False),
            "solve_status": optimality.get("solve_status"),
        }

        # --- issue 生成 ---
        issues = []
        if unassigned_anomaly:
            issues.insert(0, unassigned_anomaly)

        # 解チェッカー（2026-09-01追加、バッチ3）: 車種別生産台数の独立検証
        checker_ctxs = build_car_sequencing_contexts(sequence_result, car_types)
        issues.extend(run_issue_rules(
            domain="CarSequencing", contexts=checker_ctxs, issue_statuses=issue_statuses,
        ))

        if total_opt_viol > 0:
            worst = max(violations_detail, key=lambda x: x["violation_count"])
            issues.append({
                "id": "option_violation",
                "severity": "WARNING",
                "title": f"オプション工程容量違反: {total_opt_viol}件",
                "message": (
                    f"スライディングウィンドウ制約を{total_opt_viol}件超過しています。"
                    f"最多: {worst['option_name']}（{worst['violation_count']}件超過）。"
                ),
                "relatedContainerIds": [],
            })

        if total_same_viol > 0:
            issues.append({
                "id": "same_car_violation",
                "severity": "INFO",
                "title": f"同一車種連続超過: {total_same_viol}箇所",
                "message": (
                    f"同一車種が{max_consecutive + 1}台以上連続する箇所が"
                    f"{total_same_viol}箇所あります（{max_consecutive}台まで許容）。"
                ),
                "relatedContainerIds": [],
            })

        if total_opt_viol == 0 and total_same_viol == 0:
            issues.append({
                "id": "all_constraints_satisfied",
                "severity": "INFO",
                "title": "全制約を充足しました",
                "message": "オプション工程容量・同一車種連続制約をすべて満たす順序が見つかりました。",
                "relatedContainerIds": [],
            })

        return self._make_result(
            feasible=True,
            sequence=sequence_result,
            schedule=[],  # ガントチャート用タスクなし（投入順序問題のため）
            violations=violations_detail,
            kpi=kpi,
            issues=issues,
            status="ok",
            optimality=optimality,
        )

    # ------------------------------------------------------------------
    # エンジン別ソルブ（2026-08-13追加）
    #
    # 両メソッドは同じ契約で戻り値を返す:
    #   (sequence_result, obj_val, optimality, early_result)
    #   - 正常にソルブできた場合: sequence_result/obj_val/optimalityを設定し、
    #     early_result=None（呼び出し元がKPI計算・issue生成を続行する）。
    #   - infeasible等で即座にreturnすべき場合: sequence_result=[]/obj_val=0、
    #     early_result に _make_result() 済みの完成レスポンスを入れる
    #     （呼び出し元はそのままreturnする）。
    # これにより、KPI計算・issue生成ロジック（_solve_internal後半）は
    # 完全にエンジン非依存のまま1箇所に保たれる。
    # ------------------------------------------------------------------

    def _solve_with_cpo(
        self, car_types, options, n, type_ids, type_index, num_types,
        option_needs_idx, max_consecutive, solve_time_sec,
    ) -> Tuple[List[Dict], float, Dict, Optional[dict]]:
        from docplex.cp.model import CpoModel
        from solvers.base.solution_extraction import extract_optimality_metadata
        from solvers.base.ce_limit_lns import is_ce_limit_exceeded
        from solvers.base.rolling_window import build_sliding_window_penalty_terms

        mdl = CpoModel(name="car_sequencing")

        # --- 位置変数: seq[pos] = car_type_index (0 <= pos < n) ---
        seq = [mdl.integer_var(0, num_types - 1, name=f"seq_{pos}") for pos in range(n)]

        # --- ハード制約: 各車種の生産台数 (count制約) ---
        for ct in car_types:
            tid = ct["car_type_id"]
            idx = type_index[tid]
            required_count = ct.get("count", 0)
            # count_of(seq, value) は CP Optimizer では sum([seq[i] == idx]) で代替
            count_expr = mdl.sum([seq[pos] == idx for pos in range(n)])
            mdl.add(count_expr == required_count)

        # --- ソフト制約: オプションのスライディングウィンドウ超過 ---
        option_violation_terms = []
        for k, opt in enumerate(options):
            q = opt.get("window_size", 1)
            p = opt.get("max_per_window", 1)
            needs_opt = option_needs_idx[k]
            if not needs_opt:
                continue
            count_exprs = [
                mdl.sum([seq[pos] == j for j in needs_opt]) for pos in range(n)
            ]
            terms = build_sliding_window_penalty_terms(mdl, count_exprs, q, p)
            if terms:
                option_violation_terms.append(W_OPTION_VIOLATION * mdl.sum(terms))

        # --- ソフト制約: 同一車種の連続超過 ---
        same_car_violation_terms = []
        if max_consecutive >= 1 and n > max_consecutive:
            for pos in range(max_consecutive, n):
                all_same = mdl.logical_and(
                    [seq[pos - i] == seq[pos] for i in range(1, max_consecutive + 1)]
                )
                same_car_violation_terms.append(W_SAME_CAR_VIOLATION * all_same)

        # --- 目的関数 ---
        total_option_violation = (
            mdl.sum(option_violation_terms) if option_violation_terms
            else mdl.integer_var(0, 0)
        )
        total_same_car_violation = (
            mdl.sum(same_car_violation_terms) if same_car_violation_terms
            else mdl.integer_var(0, 0)
        )
        objective = total_option_violation + total_same_car_violation
        mdl.add(mdl.minimize(objective))

        # --- ソルブ ---
        try:
            msol = mdl.solve(TimeLimit=solve_time_sec, LogVerbosity="Quiet")
        except Exception as e:
            if is_ce_limit_exceeded(e):
                # 2026-07-27追加: CPLEXの無料版のCP Optimizerモデルサイズ上限を検知。
                # Koshoshiとの相談により、本ドメインではバッチ分割によるフォールバック
                # は行わない（seq[pos]は位置ベースの単一変数列で、スライディングウィンドウ
                # 制約・同一車種連続制約のどちらもシーケンス全体を横断するため、位置や
                # 車種を軸に分割すると窓制約がバッチ境界をまたぐケースを正しく扱えない。
                # DESIGN_2026-07-26_generic_ce_limit_fallback.md決定事項#12の安全弁、
                # および会話記録参照）。上限超過を検知したら分割を試みず、その旨を
                # 伝えるissueのみ返す。config.solver_engine="cpsat" を明示指定すれば
                # このCE上限自体を回避できる（2026-08-13追加のCP-SATエンジン参照）。
                early = self._make_result(
                    feasible=False, sequence=[], schedule=[], violations=[], kpi={},
                    issues=[{
                        "id": "ce_limit_unresolvable",
                        "severity": "CRITICAL",
                        "title": "CPLEXの無料版で扱える件数を超えています",
                        "message": (
                            "生産台数・オプション数が多すぎるため、CPLEXの無料版"
                            "（Community Edition）のモデルサイズ上限を超えました。"
                            "データ件数を減らすか、正規ライセンスのご利用、または"
                            "config.solver_engine=\"cpsat\"（OR-Tools CP-SAT、上限なし）"
                            "のご利用をご検討ください。"
                        ),
                        "relatedContainerIds": [],
                    }],
                    status="infeasible",
                )
                return [], 0.0, extract_optimality_metadata(None), early
            raise

        optimality = extract_optimality_metadata(msol)

        if msol is None or not msol:
            early = self._make_result(
                feasible=False, sequence=[], schedule=[], violations=[], kpi={},
                issues=[{
                    "id": "solve_failed",
                    "severity": "CRITICAL",
                    "title": "実行可能解が見つかりませんでした",
                    "message": "現在の制約（車種台数・オプション装備）を満たす投入順序が見つかりません。",
                    "relatedContainerIds": [],
                }],
                status="infeasible",
                optimality=optimality,
            )
            return [], 0.0, optimality, early

        # --- 解抽出 ---
        sequence_result: List[Dict] = []
        for pos in range(n):
            type_idx = msol.get_value(seq[pos])
            if type_idx is None:
                continue
            type_idx = int(type_idx)
            tid = type_ids[type_idx] if 0 <= type_idx < num_types else "unknown"
            ct_info = car_types[type_idx] if 0 <= type_idx < num_types else {}
            pos_options = []
            for k, opt in enumerate(options):
                pos_options.append({
                    "option_id": opt["option_id"],
                    "option_name": opt.get("option_name", opt["option_id"]),
                    "required": type_idx in option_needs_idx[k],
                })
            sequence_result.append({
                "position": pos + 1,  # 1-indexed
                "car_type_id": tid,
                "car_type_name": ct_info.get("car_type_name", tid),
                "options": pos_options,
            })

        obj_val = msol.get_objective_value()
        return sequence_result, obj_val, optimality, None

    def _solve_with_cpsat(
        self, car_types, options, n, type_ids, type_index, num_types,
        option_needs_idx, max_consecutive, solve_time_sec,
    ) -> Tuple[List[Dict], float, Dict, Optional[dict]]:
        """
        OR-Tools CP-SAT（CPMpy経由）でのソルブ。
        Backend/tools/pilot_cpmpy_car_sequencing.py で全探索による独立検証済みの
        モデル構築ロジックをそのまま本番コードに移植したもの。

        CP-SATにはCPLEX Community Editionのようなモデルサイズ上限が無いため、
        CarSequencing独自のCE上限フォールバック（分割不可・issueのみ返す方式）
        は本エンジンでは発生しない。
        """
        import cpmpy as cp
        from solvers.base.engine_select import cpmpy_optimality_metadata

        seq = cp.intvar(0, num_types - 1, shape=n, name="seq")
        m = cp.Model()

        # --- ハード制約: 各車種の生産台数 ---
        for ct in car_types:
            idx = type_index[ct["car_type_id"]]
            required_count = ct.get("count", 0)
            count_expr = cp.sum([seq[pos] == idx for pos in range(n)])
            m += (count_expr == required_count)

        # --- ソフト制約: スライディングウィンドウ超過 ---
        option_violation_terms = []
        for k, opt in enumerate(options):
            q = opt.get("window_size", 1)
            p = opt.get("max_per_window", 1)
            needs_opt = option_needs_idx[k]
            if not needs_opt:
                continue
            count_exprs = [cp.sum([seq[pos] == j for j in needs_opt]) for pos in range(n)]
            for start in range(max(0, n - q + 1)):
                window_sum = cp.sum(count_exprs[start:start + q])
                excess = cp.max([0, window_sum - p])
                option_violation_terms.append(W_OPTION_VIOLATION * excess)

        # --- ソフト制約: 同一車種連続超過 ---
        same_car_violation_terms = []
        if max_consecutive >= 1 and n > max_consecutive:
            for pos in range(max_consecutive, n):
                all_same = cp.all([seq[pos - i] == seq[pos] for i in range(1, max_consecutive + 1)])
                same_car_violation_terms.append(W_SAME_CAR_VIOLATION * all_same)

        objective_terms = option_violation_terms + same_car_violation_terms
        objective = cp.sum(objective_terms) if objective_terms else cp.intvar(0, 0)
        m.minimize(objective)

        ok = m.solve(solver="ortools", time_limit=solve_time_sec)
        optimality = cpmpy_optimality_metadata(m)

        if not ok:
            early = self._make_result(
                feasible=False, sequence=[], schedule=[], violations=[], kpi={},
                issues=[{
                    "id": "solve_failed",
                    "severity": "CRITICAL",
                    "title": "実行可能解が見つかりませんでした",
                    "message": "現在の制約（車種台数・オプション装備）を満たす投入順序が見つかりません。",
                    "relatedContainerIds": [],
                }],
                status="infeasible",
                optimality=optimality,
            )
            return [], 0.0, optimality, early

        sequence_result: List[Dict] = []
        for pos in range(n):
            type_idx = int(seq[pos].value())
            tid = type_ids[type_idx] if 0 <= type_idx < num_types else "unknown"
            ct_info = car_types[type_idx] if 0 <= type_idx < num_types else {}
            pos_options = []
            for k, opt in enumerate(options):
                pos_options.append({
                    "option_id": opt["option_id"],
                    "option_name": opt.get("option_name", opt["option_id"]),
                    "required": type_idx in option_needs_idx[k],
                })
            sequence_result.append({
                "position": pos + 1,
                "car_type_id": tid,
                "car_type_name": ct_info.get("car_type_name", tid),
                "options": pos_options,
            })

        obj_val = m.objective_value()
        return sequence_result, obj_val, optimality, None

    def _make_result(
        self,
        feasible: bool,
        sequence: List[Dict],
        schedule: List[Dict],
        violations: List[Dict],
        kpi: Dict,
        issues: List[Dict],
        status: str,
        optimality: Optional[Dict] = None,
    ) -> dict:
        return {
            "status": status,
            "feasible": feasible,
            "metadata": {
                "problem_class": "CarSequencing",
                "solver_version": _SOLVER_VERSION,
                **(optimality or {}),
            },
            "solutions": [{
                "feasible": feasible,
                "sequence": sequence,
                "schedule": schedule,
                "violations": violations,
                "kpi": kpi,
            }],
            "issues": issues,
            "_solver_version": _SOLVER_VERSION,
        }