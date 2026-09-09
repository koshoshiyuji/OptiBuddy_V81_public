
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable, Dict, List, Optional, Tuple

from solvers.base.solution_checker import solver_bug_issue, sweep_peak_usage
from i18n.common_messages import t as _common_t
from i18n.nurse_shift_weekly_cap_messages import t as _nurse_shift_t

from ._common import IssueRule, _rule




# ---------------------------------------------------------------------------
# _NURSING_WORKLOAD_BALANCE_RULES（2026-08-31 新規、solution checker展開バッチ2）
#
# 制約: (a) 各看護師の担当患者数は[minPatientsPerNurse, maxPatientsPerNurse]の範囲、
#       (b) 各看護師の負荷（担当患者のアキュイティ合計）はmaxWorkloadPerNurse以下。
# どちらもCPOモデル側でmdl.add()しているハード制約。
# ---------------------------------------------------------------------------

_NURSING_WORKLOAD_BALANCE_RULES: List[IssueRule] = [

    _rule(
        "nurse_patient_count_out_of_range",
        condition=lambda ctx: (
            ctx["patient_count"] < ctx["min_patients"] or ctx["patient_count"] > ctx["max_patients"]
        ),
        build=lambda ctx: solver_bug_issue(
            f"nurse_patient_count_out_of_range_{ctx['nurse_id']}",
            f"担当患者数の範囲逸脱（解チェッカー）: {ctx['nurse_name']}",
            f"看護師「{ctx['nurse_name']}」の担当患者数{ctx['patient_count']}件が"
            f"許容範囲[{ctx['min_patients']}, {ctx['max_patients']}]の外にあります。",
        ),
    ),

    _rule(
        "nurse_workload_exceeded",
        condition=lambda ctx: ctx["total_acuity"] > ctx["max_workload"],
        build=lambda ctx: solver_bug_issue(
            f"nurse_workload_exceeded_{ctx['nurse_id']}",
            f"負荷上限超過（解チェッカー）: {ctx['nurse_name']}",
            f"看護師「{ctx['nurse_name']}」の負荷（アキュイティ合計）{ctx['total_acuity']}が"
            f"上限{ctx['max_workload']}を超えています。",
        ),
    ),
]



# ---------------------------------------------------------------------------
# NursingWorkloadBalance context ビルダー（2026-08-31 新規）
# ---------------------------------------------------------------------------

def build_nursing_workload_balance_contexts(
    nurse_workloads: List[Dict],
    nurses: List[Dict],
) -> List[Dict[str, Any]]:
    """
    NursingWorkloadBalance 用の担当患者数範囲チェックと負荷上限チェック
    （共にO(看護師数)）のcontextを生成する。solver内部のassign_vars/mdlオブジェクトは
    一切参照せず、返ってきたnurse_workloadsとDSL宣言のnurses（min/max設定値）のみから
    集計し直す。
    """
    nurse_config: Dict[str, Dict] = {str(n["id"]): n for n in nurses}
    default_max_patients = len(nurses)  # solver.py側のデフォルト(nPatients)相当は呼び出し側で上書き可

    ctxs: List[Dict] = []
    for w in nurse_workloads:
        nid = w["nurse_id"]
        n = nurse_config.get(nid, {})
        min_patients = int(n.get("minPatientsPerNurse", 1))
        max_patients = int(n.get("maxPatientsPerNurse", default_max_patients))
        max_workload = int(n.get("maxWorkloadPerNurse", 10000))
        ctxs.append({
            "_rule_id":     "nurse_patient_count_out_of_range",
            "nurse_id":     nid,
            "nurse_name":   w.get("nurse_name", nid),
            "patient_count": w.get("patient_count", 0),
            "min_patients": min_patients,
            "max_patients": max_patients,
        })
        ctxs.append({
            "_rule_id":     "nurse_workload_exceeded",
            "nurse_id":     nid,
            "nurse_name":   w.get("nurse_name", nid),
            "total_acuity": w.get("total_acuity", 0),
            "max_workload": max_workload,
        })
    return ctxs
