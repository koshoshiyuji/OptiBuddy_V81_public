"""
Backend/solvers/crew_duty_scheduler_solver.py

CrewDutySchedulerSolver — バス運転士勤務スケジューリング（集合分割MIP）

問題概要:
  バス事業者の1日の運行ダイヤに含まれるすべての運行ピース（piece）を、
  労働時間・休憩規則を満たす勤務パターン（duty）の組み合わせでカバーし、
  必要勤務数（稼働運転士数）を最小化する。

定式化（CSPLib prob022準拠）:
  - 実行可能パターンをあらかじめ列挙（ピースの組み合わせ総当たり→規則フィルタ）
  - 各パターンに 0/1 変数 x[p]（p=パターンインデックス）を割り当て
  - 目的: Σ x[p] を最小化（使用勤務数を最小化）
  - 制約: 各ピース j について Σ_{p: j ∈ p} x[p] = 1（集合分割）
  - 解法: docplex.mp（CPLEX MIP / HiGHSフォールバック）

solver_input keys:
  pieces         : List[Dict]  運行ピース（id, start_min, end_min, name,
                               route, origin, destination, duration_min）
  config         : Dict        パラメータ（max_duty_min, min_break_min,
                               max_driving_min, break_threshold_min,
                               required_break_min, max_pieces_per_duty,
                               solve_time_sec）
  issue_statuses : Dict        {issue_id: "ACCEPTED"}
  problem_class  : str         "CrewDutyScheduler"
  meta           : Dict        メタ情報（任意）
"""

from __future__ import annotations

import itertools
import logging
from typing import Any, Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)

# ── CE上限フォールバック
from solvers.base.ce_limit_mip_fallback import solve_with_ce_fallback
from solvers.base.issue_rules import run_issue_rules, build_full_unassignment_issue
from solvers.base.solver_error_result import build_solver_crash_issue, solver_crash_extra_fields


class CrewDutySchedulerSolver:
    """バス運転士勤務スケジューリング（集合分割MIP）。"""

    def __init__(self, solver_input: dict) -> None:
        self.dsl = solver_input

    # ------------------------------------------------------------------
    # 公開エントリポイント
    # ------------------------------------------------------------------

    def solve(self) -> dict:
        pieces = self.dsl.get("pieces", [])
        config = self.dsl.get("config", {})
        issue_statuses = self.dsl.get("issue_statuses", {})

        # ── バリデーション
        if not pieces:
            return self._make_result(
                feasible=False,
                duty_assignments=[],
                patterns=[],
                issues=[{
                    "id": "solve_failed",
                    "severity": "CRITICAL",
                    "title": "運行ピースが未定義です",
                    "message": "pieces が空です。少なくとも1件以上の運行ピースを指定してください。",
                    "relatedContainerIds": [],
                }],
            )

        # ── 実行可能パターン列挙
        patterns = self._enumerate_patterns(pieces, config)
        if not patterns:
            return self._make_result(
                feasible=False,
                duty_assignments=[],
                patterns=[],
                issues=[{
                    "id": "solve_failed",
                    "severity": "CRITICAL",
                    "title": "実行可能な勤務パターンが見つかりません",
                    "message": (
                        "労働時間・休憩規則を満たす運行ピースの組み合わせが1件もありません。"
                        "max_duty_min / min_break_min の設定を見直してください。"
                    ),
                    "relatedContainerIds": [],
                }],
            )

        # ── MIPソルブ
        try:
            selected_patterns, solve_time = self._solve_mip(pieces, patterns, config)
        except Exception as e:
            logger.error(f"[CrewDutyScheduler] mdl.solve() 例外: {e}", exc_info=True)
            result = {
                "status": "ok", "feasible": False,
                "metadata": {"problem_class": "CrewDutyScheduler"},
                "solutions": [], "issues": [build_solver_crash_issue(e)],
                "_solver_version": "crew_duty_scheduler_v1.0",
            }
            result.update(solver_crash_extra_fields(e))
            return result

        if selected_patterns is None:
            return self._make_result(
                feasible=False,
                duty_assignments=[],
                patterns=patterns,
                issues=[{
                    "id": "solve_failed",
                    "severity": "CRITICAL",
                    "title": "最適解が見つかりませんでした",
                    "message": (
                        "制約を満たす勤務割り当てが見つかりませんでした。"
                        "運行ピースの時間設定や制約パラメータを確認してください。"
                    ),
                    "relatedContainerIds": [],
                }],
            )

        # ── 解の組み立て
        duty_assignments = self._build_assignments(selected_patterns, pieces)

        # ── イシュー検知
        issues = self._detect_issues(duty_assignments, pieces, patterns, config, issue_statuses, solve_time)

        return self._make_result(
            feasible=True,
            duty_assignments=duty_assignments,
            patterns=patterns,
            solve_time=solve_time,
            issues=issues,
        )

    # ------------------------------------------------------------------
    # パターン列挙
    # ------------------------------------------------------------------

    def _enumerate_patterns(
        self, pieces: List[Dict], config: Dict
    ) -> List[Dict]:
        """
        労働時間・休憩規則を満たすピースの組み合わせをすべて列挙する。

        規則:
          - max_duty_min        : 1勤務の最大拘束時間（分）。start最小〜end最大の幅で計算。
          - min_break_min       : 連続する2ピースの間隔が min_break_min 未満のとき不可。
          - max_driving_min     : 1勤務の実乗務時間（ピースの duration_min 合計）の上限（分）。
          - break_threshold_min : 拘束時間がこの値を超える勤務には休憩が必須（分）。
          - required_break_min  : 必須休憩の最小長さ（分）。break_threshold_min 超過時に
                                  連続ピース間のいずれかの gap がこの値以上でなければ不可。
          - max_pieces_per_duty : 1勤務に含めるピース数の上限。

        接続性チェック:
          同じ勤務内で連続する2ピースは、前のピースの destination と
          次のピースの origin が一致しなければならない。
          origin / destination が未設定（空文字）のピースは接続性チェックをスキップする。
        """
        max_duty_min = int(config.get("max_duty_min", 480))
        min_break_min = int(config.get("min_break_min", 10))
        max_driving_min = int(config.get("max_driving_min", 450))
        break_threshold_min = int(config.get("break_threshold_min", 360))
        required_break_min = int(config.get("required_break_min", 30))
        max_pieces = int(config.get("max_pieces_per_duty", 6))

        # ピースを開始時刻順にソート
        sorted_pieces = sorted(pieces, key=lambda p: p["start_min"])
        n = len(sorted_pieces)

        # 各ピースの duration_min を参照してデバッグログを出力する
        total_piece_duration = sum(p.get("duration_min", 0) for p in sorted_pieces)
        logger.debug(
            f"[CrewDutyScheduler] ピース合計所要時間: {total_piece_duration} 分 / {n} ピース"
        )

        patterns: List[Dict] = []

        for r in range(1, min(max_pieces, n) + 1):
            for combo in itertools.combinations(range(n), r):
                combo_pieces = [sorted_pieces[i] for i in combo]
                # combinations は昇順インデックスなので start_min 昇順が保証される

                # 1. 拘束時間チェック
                duty_start = min(p["start_min"] for p in combo_pieces)
                duty_end = max(p["end_min"] for p in combo_pieces)
                duty_duration = duty_end - duty_start
                if duty_duration > max_duty_min:
                    continue

                # 2. ピース間チェック（重複・最小休憩・接続性）
                valid = True
                has_sufficient_break = False  # break_threshold_min 超過時の必須休憩フラグ
                for k in range(len(combo_pieces) - 1):
                    gap = combo_pieces[k + 1]["start_min"] - combo_pieces[k]["end_min"]

                    # 2a. 時間的重複チェック（gap < 0 は重複）
                    if gap < 0:
                        valid = False
                        break

                    # 2b. 最小休憩チェック
                    if gap < min_break_min:
                        valid = False
                        break

                    # 2c. 接続性チェック: 前のピースの destination == 次のピースの origin
                    dest = combo_pieces[k].get("destination", "")
                    orig = combo_pieces[k + 1].get("origin", "")
                    if dest and orig and dest != orig:
                        valid = False
                        break

                    # 2d. 必須休憩の充足確認（gap >= required_break_min なら休憩あり）
                    if gap >= required_break_min:
                        has_sufficient_break = True

                if not valid:
                    continue

                # 3. 実乗務時間（duration_min 合計）の上限チェック
                total_driving = sum(p.get("duration_min", 0) for p in combo_pieces)
                if total_driving > max_driving_min:
                    continue

                # 4. 休憩必須ルール:
                #    拘束時間が break_threshold_min を超える場合、
                #    required_break_min 以上の休憩が少なくとも1回必要
                if duty_duration > break_threshold_min and len(combo_pieces) > 1:
                    if not has_sufficient_break:
                        continue
                # 単一ピースで拘束時間が break_threshold_min を超える場合は
                # 休憩を挟む余地がないため候補から除外
                if duty_duration > break_threshold_min and len(combo_pieces) == 1:
                    continue

                # 5. ピース重複チェック（同一ピースが複数回使われないよう）
                piece_ids = [p["id"] for p in combo_pieces]

                patterns.append({
                    "pattern_id": f"P{len(patterns):04d}",
                    "piece_ids": piece_ids,
                    "duty_start_min": duty_start,
                    "duty_end_min": duty_end,
                    "duty_duration_min": duty_duration,
                    "driving_min": total_driving,
                    "piece_count": len(piece_ids),
                })

        logger.info(f"[CrewDutyScheduler] 列挙完了: {len(patterns)} パターン / {n} ピース")
        return patterns

    # ------------------------------------------------------------------
    # MIPソルブ
    # ------------------------------------------------------------------

    def _solve_mip(
        self,
        pieces: List[Dict],
        patterns: List[Dict],
        config: Dict,
    ) -> Tuple[Optional[List[Dict]], float]:
        """
        集合分割 MIP を解き、選択されたパターンのリストと解時間を返す。
        解なし/タイムアウトで解なしの場合は (None, elapsed) を返す。
        CE上限例外は呼び出し元（solve()）へ投げ直す。
        """
        from docplex.mp.model import Model
        import time

        solve_time_sec = float(config.get("solve_time_sec", 30))

        mdl = Model(name="crew_duty_scheduler")
        mdl.parameters.timelimit = solve_time_sec

        # 変数: x[p] ∈ {0, 1}
        x = {p["pattern_id"]: mdl.binary_var(name=f"x_{p['pattern_id']}") for p in patterns}

        # 制約: 各ピースはちょうど1つの選択済みパターンに含まれる（集合分割）
        piece_ids = [p["id"] for p in pieces]
        for pid in piece_ids:
            covering = [x[pat["pattern_id"]] for pat in patterns if pid in pat["piece_ids"]]
            if not covering:
                # このピースをカバーできるパターンが皆無 → 常にinfeasible
                logger.warning(f"[CrewDutyScheduler] ピース {pid} をカバーするパターンなし")
                mdl.add_constraint(mdl.binary_var(name=f"dummy_{pid}") == 2)  # 強制infeasible
            else:
                mdl.add_constraint(mdl.sum(covering) == 1, f"cover_{pid}")

        # 目的: 使用勤務数を最小化
        mdl.minimize(mdl.sum(x.values()))

        t0 = time.perf_counter()
        sol = solve_with_ce_fallback(mdl, log_output=False)
        elapsed = time.perf_counter() - t0

        if sol is None:
            return None, elapsed

        selected = [
            pat for pat in patterns
            if sol.get_value(x[pat["pattern_id"]]) > 0.5
        ]
        logger.info(
            f"[CrewDutyScheduler] MIP完了: {len(selected)} 勤務 / "
            f"{len(pieces)} ピース ({elapsed:.2f}s)"
        )
        return selected, elapsed

    # ------------------------------------------------------------------
    # 解の組み立て
    # ------------------------------------------------------------------

    def _build_assignments(
        self,
        selected_patterns: List[Dict],
        pieces: List[Dict],
    ) -> List[Dict]:
        """選択されたパターンから勤務割り当てリストを組み立てる。

        各ピースの route / origin / destination および duration_min をそのまま引き継ぎ、
        勤務内訳情報として duty_assignments に含める。
        """
        piece_map = {p["id"]: p for p in pieces}
        assignments = []
        for i, pat in enumerate(selected_patterns):
            duty_pieces = []
            for pid in pat["piece_ids"]:
                if pid not in piece_map:
                    continue
                p = piece_map[pid]
                duty_pieces.append({
                    "id":           p["id"],
                    "name":         p.get("name", p["id"]),
                    "route":        p.get("route", ""),
                    "origin":       p.get("origin", ""),
                    "destination":  p.get("destination", ""),
                    "start_min":    p["start_min"],
                    "end_min":      p["end_min"],
                    "duration_min": p.get("duration_min", 0),
                })
            assignments.append({
                "duty_id": f"D{i + 1:03d}",
                "pattern_id": pat["pattern_id"],
                "piece_ids": pat["piece_ids"],
                "duty_start_min": pat["duty_start_min"],
                "duty_end_min": pat["duty_end_min"],
                "duty_duration_min": pat["duty_duration_min"],
                "driving_min": pat.get("driving_min", 0),
                "piece_count": pat["piece_count"],
                "pieces": duty_pieces,
            })
        return assignments

    # ------------------------------------------------------------------
    # イシュー検知
    # ------------------------------------------------------------------

    def _detect_issues(
        self,
        duty_assignments: List[Dict],
        pieces: List[Dict],
        patterns: List[Dict],
        config: Dict,
        issue_statuses: Dict,
        solve_time: float,
    ) -> List[Dict]:
        issues: List[Dict] = []

        # 全件未割当チェック
        anomaly = build_full_unassignment_issue(
            assigned_count=sum(len(d["piece_ids"]) for d in duty_assignments),
            total_count=len(pieces),
            entity_label="運行ピース",
            extra_hint="集合分割MIPの解抽出（x変数のget_value）",
        )
        if anomaly:
            issues.append(anomaly)

        # ピースのカバー確認
        covered_pieces = {pid for d in duty_assignments for pid in d["piece_ids"]}
        all_piece_ids = {p["id"] for p in pieces}
        uncovered = all_piece_ids - covered_pieces
        for pid in sorted(uncovered):
            iid = f"uncovered_piece_{pid}"
            if issue_statuses.get(iid) != "ACCEPTED":
                issues.append({
                    "id": iid,
                    "severity": "CRITICAL",
                    "title": f"カバーされていない運行ピース: {pid}",
                    "message": (
                        f"運行ピース {pid} がどの勤務にも割り当てられていません。"
                        "パターン列挙の制約条件を確認してください。"
                    ),
                    "relatedContainerIds": [],
                })

        # 拘束時間超過チェック（解チェッカー）
        max_duty_min = int(config.get("max_duty_min", 480))
        for d in duty_assignments:
            if d["duty_duration_min"] > max_duty_min:
                iid = f"duty_overtime_{d['duty_id']}"
                if issue_statuses.get(iid) != "ACCEPTED":
                    issues.append({
                        "id": iid,
                        "severity": "CRITICAL",
                        "title": f"拘束時間超過: {d['duty_id']}",
                        "message": (
                            f"勤務 {d['duty_id']} の拘束時間 {d['duty_duration_min']} 分が"
                            f"上限 {max_duty_min} 分を超えています。"
                        ),
                        "relatedContainerIds": [],
                    })

        # 実乗務時間超過チェック（解チェッカー）
        max_driving_min = int(config.get("max_driving_min", 450))
        for d in duty_assignments:
            driving = d.get("driving_min", 0)
            if driving > max_driving_min:
                iid = f"duty_driving_overtime_{d['duty_id']}"
                if issue_statuses.get(iid) != "ACCEPTED":
                    issues.append({
                        "id": iid,
                        "severity": "CRITICAL",
                        "title": f"実乗務時間超過: {d['duty_id']}",
                        "message": (
                            f"勤務 {d['duty_id']} の実乗務時間 {driving} 分が"
                            f"上限 {max_driving_min} 分を超えています。"
                        ),
                        "relatedContainerIds": [],
                    })

        return issues

    # ------------------------------------------------------------------
    # 結果フォーマット
    # ------------------------------------------------------------------

    def _make_result(
        self,
        feasible: bool,
        duty_assignments: List[Dict],
        patterns: List[Dict],
        solve_time: float = 0.0,
        issues: Optional[List[Dict]] = None,
    ) -> Dict[str, Any]:
        if issues is None:
            issues = []

        total_duties = len(duty_assignments)
        total_pieces = sum(len(d["piece_ids"]) for d in duty_assignments)
        avg_pieces = round(total_pieces / total_duties, 1) if total_duties > 0 else 0.0
        avg_duration = (
            round(sum(d["duty_duration_min"] for d in duty_assignments) / total_duties, 1)
            if total_duties > 0 else 0.0
        )

        solution = {
            "feasible": feasible,
            "duty_assignments": duty_assignments,
            "pattern_count": len(patterns),
            "kpi": {
                "total_duties": total_duties,
                "total_pieces_covered": total_pieces,
                "avg_pieces_per_duty": avg_pieces,
                "avg_duty_duration_min": avg_duration,
                "solve_time_sec": round(solve_time, 2),
            },
        }

        return {
            "status": "ok",
            "feasible": feasible,
            "metadata": {"problem_class": "CrewDutyScheduler"},
            "solutions": [solution] if feasible else [],
            "issues": issues,
            "_solver_version": "crew_duty_scheduler_v1.0",
        }
