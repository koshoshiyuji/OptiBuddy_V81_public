"""
Backend/dsl_transformer/nursing_workload_balance_converter.py

Business DSL → Solver Input DSL 変換 (NursingWorkloadBalance)

問題の本質: 患者 → 看護師 の静的1対1割り当て・負荷分散問題
  - 各患者にちょうど1人の看護師を割り当てる
  - 看護師は自分のゾーン（病棟）の患者のみ担当可能
  - 各看護師の担当患者数は [minPatientsPerNurse, maxPatientsPerNurse] の範囲
  - 各看護師の負荷（担当患者のアキュイティ合計）は maxWorkloadPerNurse 以下
  - 目的: 各看護師の負荷の二乗和を最小化（標準偏差最小化と等価）

solver_input keys: nurses, patients, config, issue_statuses
"""

from typing import Any, Dict, List


def convert_nursing_workload_balance_to_solver(business_dsl: dict) -> dict:
    """
    Business DSL → Solver Input DSL

    Business DSL 構造:
      nurses[]:   {id, name, zone, minPatientsPerNurse?, maxPatientsPerNurse?,
                   maxWorkloadPerNurse?}
      patients[]: {id, name, zone, acuity}
      config:     {time_limit_sec?,
                   default_min_patients_per_nurse?,
                   default_max_patients_per_nurse?,
                   default_max_workload_per_nurse?}
    """
    raw_config = business_dsl.get("config", {})

    # config のデフォルト値
    default_min_patients = int(raw_config.get("default_min_patients_per_nurse", 1))
    default_max_patients = int(raw_config.get("default_max_patients_per_nurse", 10))
    default_max_workload = int(raw_config.get("default_max_workload_per_nurse", 100))

    nurses: List[Dict] = [
        {
            "id":                  str(n["id"]),
            "name":                n.get("name", str(n["id"])),
            "zone":                str(n.get("zone", "")),
            "minPatientsPerNurse": int(n.get("minPatientsPerNurse",
                                             raw_config.get("default_min_patients_per_nurse",
                                                            default_min_patients))),
            "maxPatientsPerNurse": int(n.get("maxPatientsPerNurse",
                                             raw_config.get("default_max_patients_per_nurse",
                                                            default_max_patients))),
            "maxWorkloadPerNurse": int(n.get("maxWorkloadPerNurse",
                                             raw_config.get("default_max_workload_per_nurse",
                                                            default_max_workload))),
        }
        for n in business_dsl.get("nurses", [])
    ]

    patients: List[Dict] = [
        {
            "id":     str(p["id"]),
            "name":   p.get("name", str(p["id"])),
            "zone":   str(p.get("zone", "")),
            "acuity": int(p.get("acuity", 1)),
        }
        for p in business_dsl.get("patients", [])
    ]

    config = {
        "time_limit_sec": int(raw_config.get("time_limit_sec", 30)),
    }

    return {
        "problem_class":  "NursingWorkloadBalance",
        "nurses":         nurses,
        "patients":       patients,
        "config":         config,
        "issue_statuses": business_dsl.get("issue_statuses", {}),
    }
