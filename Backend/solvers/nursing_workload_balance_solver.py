"""
Backend/solvers/nursing_workload_balance_solver.py

NursingWorkloadBalanceSolver — 看護師負荷均等化ソルバー

問題の本質: 患者 → 看護師 の静的1対1割り当て・負荷分散問題
  - 各患者にちょうど1人の看護師を割り当てる（IntVar: assign[p] ∈ 担当可能看護師集合）
  - 看護師は自分のゾーン（病棟）の患者のみ担当可能（変数ドメイン制限）
  - 各看護師の担当患者数は [minPatientsPerNurse, maxPatientsPerNurse] の範囲（ハード制約）
  - 各看護師の負荷（担当患者のアキュイティ合計）は maxWorkloadPerNurse 以下（ハード制約）
  - 目的: Sum(w[k] * w[k] for k in nNurses) の最小化（CSPLib参照実装どおり）

solver_input keys: nurses, patients, config, issue_statuses
"""

import logging
from typing import Any, Dict, List, Optional

from i18n.nursing_workload_balance_messages import t

logger = logging.getLogger(__name__)


class NursingWorkloadBalanceSolver:
    """看護師負荷均等化 CP Optimizer ソルバー（静的割り当て・分散問題）。"""

    def __init__(self, solver_input: dict) -> None:
        self.dsl = solver_input

    def solve(self) -> dict:
        try:
            config: Dict = self.dsl.get("config", {})
            from solvers.base.engine_select import get_solver_engine, CPSAT
            engine = get_solver_engine(config)
            if engine == CPSAT:
                return self._solve_with_cpsat()
            return self._solve_inner()
        except Exception as e:
            from solvers.base.solver_error_result import build_solver_crash_issue, solver_crash_extra_fields
            logger.error(f"[NursingWorkloadBalance] 例外: {e}", exc_info=True)
            result = {
                "status": "ok",
                "feasible": False,
                "metadata": {"problem_class": "NursingWorkloadBalance"},
                "solutions": [],
                "issues": [build_solver_crash_issue(e)],
                "_solver_version": "nursing_workload_balance_v2.0",
            }
            result.update(solver_crash_extra_fields(e))
            return result

    def _solve_inner(self) -> dict:
        nurses: List[Dict] = self.dsl.get("nurses", [])
        patients: List[Dict] = self.dsl.get("patients", [])
        config: Dict = self.dsl.get("config", {})
        issue_statuses: Dict = self.dsl.get("issue_statuses", {})

        if not nurses or not patients:
            return self._make_result(
                feasible=False,
                solutions=[],
                issues=[{
                    "id": "no_input",
                    "severity": "CRITICAL",
                    "category": "INFEASIBLE",
                    "title": t("issue.no_input.title"),
                    "message": t("issue.no_input.message"),
                    "relatedContainerIds": [],
                }],
            )

        mdl, assign_vars, nurse_workload_vars = self._build_model(nurses, patients, config)

        try:
            time_limit = int(config.get("time_limit_sec", 30))
            msol = mdl.solve(TimeLimit=time_limit, LogVerbosity="Quiet")
        except Exception as e:
            from solvers.base.ce_limit_lns import is_ce_limit_exceeded, CeLimitExceededError
            if is_ce_limit_exceeded(e):
                raise CeLimitExceededError(str(e)) from e
            from solvers.base.solver_error_result import build_solver_crash_issue, solver_crash_extra_fields
            logger.error(f"[NursingWorkloadBalance] mdl.solve() 例外: {e}", exc_info=True)
            result = {
                "status": "ok",
                "feasible": False,
                "metadata": {"problem_class": "NursingWorkloadBalance"},
                "solutions": [],
                "issues": [build_solver_crash_issue(e)],
                "_solver_version": "nursing_workload_balance_v2.0",
            }
            result.update(solver_crash_extra_fields(e))
            return result

        if msol is None or not msol:
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

        # 解の抽出
        assignments = self._extract_solution(msol, assign_vars, nurses, patients)
        nurse_workloads = self._compute_workloads(assignments, nurses, patients)

        issues = self._detect_issues(assignments, nurses, patients, nurse_workloads, issue_statuses)
        metrics = self._build_metrics(nurse_workloads)

        solution = {
            "name": "Plan A",
            "label": t("solver.planLabel"),
            "feasible": True,
            "assignments": assignments,
            "nurse_workloads": nurse_workloads,
            "metrics": metrics,
        }

        return self._make_result(feasible=True, solutions=[solution], issues=issues)

    def _solve_with_cpsat(self) -> dict:
        """CP-SAT(CPMpy)版。_solve_inner()/_build_model()と同一の制約セット・目的関数を実装する。"""
        import cpmpy as cp
        from cpmpy.expressions.globalconstraints import InDomain

        nurses: List[Dict] = self.dsl.get("nurses", [])
        patients: List[Dict] = self.dsl.get("patients", [])
        config: Dict = self.dsl.get("config", {})
        issue_statuses: Dict = self.dsl.get("issue_statuses", {})

        if not nurses or not patients:
            return self._make_result(
                feasible=False,
                solutions=[],
                issues=[{
                    "id": "no_input",
                    "severity": "CRITICAL",
                    "category": "INFEASIBLE",
                    "title": t("issue.no_input.title"),
                    "message": t("issue.no_input.message"),
                    "relatedContainerIds": [],
                }],
            )

        nNurses = len(nurses)
        nPatients = len(patients)

        m = cp.Model()

        assign_vars = []
        for p_idx, p in enumerate(patients):
            p_zone = str(p.get("zone", ""))
            eligible = [k for k, n in enumerate(nurses) if str(n.get("zone", "")) == p_zone]
            if not eligible:
                # 担当可能な看護師が存在しない → 解なし（実現不可能な制約を明示的に追加）
                v = cp.intvar(0, max(nNurses - 1, 0), name=f"assign_p{p_idx}")
                m += (v == -1)
            else:
                v = cp.intvar(min(eligible), max(eligible), name=f"assign_p{p_idx}")
                if len(eligible) < (max(eligible) - min(eligible) + 1):
                    m += InDomain(v, eligible)
            assign_vars.append(v)

        count_vars = []
        for k in range(nNurses):
            cnt = cp.sum([(assign_vars[p_idx] == k) for p_idx in range(nPatients)])
            count_vars.append(cnt)

        nurse_workload_vars = []
        for k in range(nNurses):
            wl = cp.sum([
                int(patients[p_idx]["acuity"]) * (assign_vars[p_idx] == k)
                for p_idx in range(nPatients)
            ])
            nurse_workload_vars.append(wl)

        for k, n in enumerate(nurses):
            min_p = int(n.get("minPatientsPerNurse", 1))
            max_p = int(n.get("maxPatientsPerNurse", nPatients))
            m += (count_vars[k] >= min_p)
            m += (count_vars[k] <= max_p)

        for k, n in enumerate(nurses):
            max_wl = int(n.get("maxWorkloadPerNurse", 10000))
            m += (nurse_workload_vars[k] <= max_wl)

        obj = cp.sum([nurse_workload_vars[k] * nurse_workload_vars[k] for k in range(nNurses)])
        m.minimize(obj)

        time_limit = int(config.get("time_limit_sec", 30))
        solved = m.solve(solver="ortools", time_limit=time_limit)

        if not solved:
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

        assignments = self._extract_solution_cpsat(assign_vars, nurses, patients)
        nurse_workloads = self._compute_workloads(assignments, nurses, patients)

        issues = self._detect_issues(assignments, nurses, patients, nurse_workloads, issue_statuses)
        metrics = self._build_metrics(nurse_workloads)

        solution = {
            "name": "Plan A",
            "label": t("solver.planLabel"),
            "feasible": True,
            "assignments": assignments,
            "nurse_workloads": nurse_workloads,
            "metrics": metrics,
        }

        return self._make_result(feasible=True, solutions=[solution], issues=issues)

    def _extract_solution_cpsat(self, assign_vars, nurses, patients):
        """CP-SAT版解抽出。_extract_solution()のCPMpy版。"""
        assignments = []
        for p_idx, p in enumerate(patients):
            v = assign_vars[p_idx]
            k = v.value()
            if k is None:
                continue
            k = int(k)
            if 0 <= k < len(nurses):
                nurse = nurses[k]
                assignments.append({
                    "patient_id":   str(p["id"]),
                    "patient_name": p.get("name", str(p["id"])),
                    "patient_zone": str(p.get("zone", "")),
                    "acuity":       int(p.get("acuity", 1)),
                    "nurse_id":     str(nurse["id"]),
                    "nurse_name":   nurse.get("name", str(nurse["id"])),
                    "nurse_zone":   str(nurse.get("zone", "")),
                })
        return assignments

    # -------------------------------------------------------------------------
    # モデル構築
    # -------------------------------------------------------------------------

    def _build_model(self, nurses: List[Dict], patients: List[Dict], config: Dict):
        from docplex.cp.model import CpoModel

        mdl = CpoModel(name="NursingWorkloadBalance")

        nNurses = len(nurses)
        nPatients = len(patients)

        # 看護師インデックス辞書
        nurse_idx: Dict[str, int] = {str(n["id"]): k for k, n in enumerate(nurses)}

        # ── 決定変数: assign[p] = 患者pを担当する看護師のインデックス
        # ゾーン制約をドメイン制限として実装:
        #   患者pのゾーンと一致する看護師のインデックスのみを許容値とする
        assign_vars = []
        for p_idx, p in enumerate(patients):
            p_zone = str(p.get("zone", ""))
            # 同ゾーンの看護師インデックスリスト
            eligible = [k for k, n in enumerate(nurses) if str(n.get("zone", "")) == p_zone]
            if not eligible:
                # 担当可能な看護師が存在しない → 解なし（空ドメインで制約違反）
                # ダミー変数を作り後で矛盾制約を追加
                v = mdl.integer_var(0, nNurses - 1, name=f"assign_p{p_idx}")
                mdl.add(v == -1)  # 実現不可能な制約
            else:
                v = mdl.integer_var_list(1, min(eligible), max(eligible),
                                         name=f"assign_p{p_idx}")[0]
                # eligible が連続していない場合は allowed_assignments で制限
                if len(eligible) < (max(eligible) - min(eligible) + 1):
                    mdl.add(mdl.allowed_assignments(v, [(e,) for e in eligible]))
                else:
                    # 連続範囲の場合はドメイン指定のみで十分
                    pass
            assign_vars.append(v)

        # ── 各看護師の担当患者数カウント変数（カーディナリティ制約）
        # count_vars[k] = 看護師kの担当患者数
        count_vars = []
        for k in range(nNurses):
            cnt = mdl.sum([mdl.equal(assign_vars[p_idx], k) for p_idx in range(nPatients)])
            count_vars.append(cnt)

        # ── 各看護師の負荷変数（アキュイティ合計）
        nurse_workload_vars = []
        for k in range(nNurses):
            wl = mdl.sum([
                patients[p_idx]["acuity"] * mdl.equal(assign_vars[p_idx], k)
                for p_idx in range(nPatients)
            ])
            nurse_workload_vars.append(wl)

        # ── ハード制約1: 担当患者数の下限・上限
        for k, n in enumerate(nurses):
            min_p = int(n.get("minPatientsPerNurse", 1))
            max_p = int(n.get("maxPatientsPerNurse", nPatients))
            mdl.add(count_vars[k] >= min_p)
            mdl.add(count_vars[k] <= max_p)

        # ── ハード制約2: 負荷上限（アキュイティ合計 ≤ maxWorkloadPerNurse）
        for k, n in enumerate(nurses):
            max_wl = int(n.get("maxWorkloadPerNurse", 10000))
            mdl.add(nurse_workload_vars[k] <= max_wl)

        # ── 目的関数: 各看護師の負荷の二乗和を最小化（CSPLib参照実装どおり）
        # Sum(w[k] * w[k] for k in range(nNurses))
        obj = mdl.sum([nurse_workload_vars[k] * nurse_workload_vars[k] for k in range(nNurses)])
        mdl.minimize(obj)

        return mdl, assign_vars, nurse_workload_vars

    # -------------------------------------------------------------------------
    # 解の抽出
    # -------------------------------------------------------------------------

    def _extract_solution(self, msol, assign_vars, nurses, patients):
        """各患者の割り当て看護師を抽出する。"""
        assignments = []
        for p_idx, p in enumerate(patients):
            v = assign_vars[p_idx]
            k = msol.get_value(v)
            if k is None:
                continue
            k = int(k)
            if 0 <= k < len(nurses):
                nurse = nurses[k]
                assignments.append({
                    "patient_id":   str(p["id"]),
                    "patient_name": p.get("name", str(p["id"])),
                    "patient_zone": str(p.get("zone", "")),
                    "acuity":       int(p.get("acuity", 1)),
                    "nurse_id":     str(nurse["id"]),
                    "nurse_name":   nurse.get("name", str(nurse["id"])),
                    "nurse_zone":   str(nurse.get("zone", "")),
                })
        return assignments

    def _compute_workloads(self, assignments, nurses, patients):
        """看護師ごとの担当患者数・アキュイティ合計を集計する。"""
        workloads = {str(n["id"]): {"nurse_id": str(n["id"]),
                                     "nurse_name": n.get("name", str(n["id"])),
                                     "nurse_zone": str(n.get("zone", "")),
                                     "patient_count": 0,
                                     "total_acuity": 0}
                     for n in nurses}
        for a in assignments:
            nid = a["nurse_id"]
            if nid in workloads:
                workloads[nid]["patient_count"] += 1
                workloads[nid]["total_acuity"] += a["acuity"]
        return list(workloads.values())

    # -------------------------------------------------------------------------
    # Issue 検出
    # -------------------------------------------------------------------------

    def _detect_issues(self, assignments, nurses, patients, nurse_workloads, issue_statuses):
        issues = []
        assigned_patient_ids = {a["patient_id"] for a in assignments}

        # 未割り当て患者
        for p in patients:
            pid = str(p["id"])
            if pid not in assigned_patient_ids:
                issue_id = f"unassigned_patient_{pid}"
                if issue_statuses.get(issue_id) != "ACCEPTED":
                    issues.append({
                        "id": issue_id,
                        "severity": "CRITICAL",
                        "category": "UNASSIGNED",
                        "title": t("issue.unassigned_patient.title", name=p.get('name', pid)),
                        "message": t("issue.unassigned_patient.message",
                                     name=p.get('name', pid), zone=p.get('zone', '')),
                        "relatedContainerIds": [],
                    })

        # 負荷偏在チェック（情報提供）
        acuity_list = [w["total_acuity"] for w in nurse_workloads if w["patient_count"] > 0]
        if len(acuity_list) >= 2:
            max_a = max(acuity_list)
            min_a = min(acuity_list)
            if max_a - min_a > 0:
                issue_id = "workload_imbalance"
                if issue_statuses.get(issue_id) != "ACCEPTED":
                    issues.append({
                        "id": issue_id,
                        "severity": "INFO",
                        "category": "WORKLOAD",
                        "title": t("issue.workload_imbalance.title"),
                        "message": t("issue.workload_imbalance.message",
                                     diff=max_a - min_a, max_a=max_a, min_a=min_a),
                        "relatedContainerIds": [],
                    })

        return issues

    # -------------------------------------------------------------------------
    # 集計・メトリクス
    # -------------------------------------------------------------------------

    def _build_metrics(self, nurse_workloads):
        acuity_list = [w["total_acuity"] for w in nurse_workloads]
        count_list = [w["patient_count"] for w in nurse_workloads]
        n = len(acuity_list)
        total = sum(acuity_list)
        avg = total / n if n > 0 else 0.0
        sq_sum = sum(a * a for a in acuity_list)
        # 標準偏差（参考値）
        variance = (sq_sum / n - avg * avg) if n > 0 else 0.0
        std_dev = variance ** 0.5 if variance > 0 else 0.0
        return {
            "total_patients":       sum(count_list),
            "max_acuity":           max(acuity_list) if acuity_list else 0,
            "min_acuity":           min(acuity_list) if acuity_list else 0,
            "avg_acuity":           round(avg, 2),
            "workload_sq_sum":      sq_sum,
            "workload_std_dev":     round(std_dev, 2),
        }

    # -------------------------------------------------------------------------
    # 結果フォーマット
    # -------------------------------------------------------------------------

    def _make_result(self, feasible: bool, solutions: list, issues: list) -> dict:
        return {
            "status": "ok",
            "feasible": feasible,
            "metadata": {"problem_class": "NursingWorkloadBalance"},
            "solutions": solutions,
            "issues": issues,
            "_solver_version": "nursing_workload_balance_v2.0",
        }
