"""
test_structural_similarity_check.py

2026-09-18作成: Stage1a.3（構造(DSL)類似度チェック、check_structural_similarity）と、
interpret_hearing()側でのmatch_type上書きロジックの単体テスト。
PatientTransportPlanner誤分類事故の再発防止策。実LLM呼び出しはこのサンドボックス
からできないため（既知の環境制約）、境界ケース（ドメイン名同一・ソース不在）は
直接テストし、LLM呼び出しを伴う判定ロジックはinterpret_hearing()の呼び出し元を
モックして検証する。

実行方法:
    cd Backend
    python3 test_structural_similarity_check.py
"""

import sys
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).parent))

from domain_generator.stage1a import check_structural_similarity
import domain_generator.hearing as hearing_mod


# ── check_structural_similarity() 単体 ─────────────────────────

def test_identical_domain_name_shortcuts_to_same_scenario_no_llm():
    # domain_name と base_domain が同一 → LLM呼び出し無しでsame_scenario
    # (call_llmはcheck_structural_similarity内でローカルimportされるため、
    #  ショートカットで早期returnすればそもそも呼ばれない。ここではその
    #  早期returnの結果だけを検証する。)
    result = check_structural_similarity("PatientTransportPlanner", ["dummy hearing"], "PatientTransportPlanner")
    assert result["verdict"] == "same_scenario", result
    assert result["confidence"] == 1.0, result


def test_near_identical_domain_name_shortcuts_to_same_scenario():
    result = check_structural_similarity("patient_transport_planner", ["dummy"], "PatientTransportPlanner")
    assert result["verdict"] == "same_scenario", result


def test_missing_source_files_falls_back_to_new_domain():
    result = check_structural_similarity("SomeNewThing", ["dummy hearing"], "NoSuchDomainAtAll12345")
    assert result["verdict"] == "new_domain", result
    assert result["confidence"] == 0.0, result


# ── interpret_hearing() 側のmatch_type上書きロジック ────────────

def _base_mocks():
    """axis_a_fit/detect_extension_gapsが呼ばれたら即失敗させる（スキップされたことの証明）。"""
    return {
        "check_domain_exists": patch("domain_generator.hearing.check_domain_exists", return_value={"exists": False}),
        "check_axis_a_fit": patch(
            "domain_generator.hearing.check_axis_a_fit",
            side_effect=AssertionError("axis_a_fit should be skipped when overridden to new_domain"),
        ),
        "detect_extension_gaps": patch(
            "domain_generator.hearing.detect_extension_gaps",
            side_effect=AssertionError("detect_extension_gaps should be skipped when overridden to new_domain"),
        ),
        "_ensure_repomix": patch("domain_generator.hearing._ensure_repomix", return_value=True),
        "derive_structural_requirements": patch("domain_generator.hearing.derive_structural_requirements", return_value=None),
        "lookup_family_reference": patch("domain_generator.hearing.lookup_family_reference", return_value=None),
        "_extract_formulation_directive": patch("domain_generator.hearing._extract_formulation_directive", return_value=None),
    }


def test_low_confidence_verdict_overrides_to_new_domain():
    classify_result = {
        "match_type": "existing_domain", "base_domain": "RideshareMatchingPlanner",
        "reason": "字面が似ている", "confidence": 0.9, "scenario_name_jp": "x",
        "scenario_description_jp": "", "missing_info": [], "is_dsl4_candidate": True,
    }
    structural_result = {"verdict": "new_domain", "confidence": 0.1, "reason": "業務単位が個人対応と乗合で違う"}

    mocks = _base_mocks()
    with patch("domain_generator.hearing.classify_problem", return_value=classify_result), \
         patch("domain_generator.hearing.check_structural_similarity", return_value=structural_result), \
         mocks["check_domain_exists"], mocks["check_axis_a_fit"], mocks["detect_extension_gaps"], \
         mocks["_ensure_repomix"], mocks["derive_structural_requirements"], \
         mocks["lookup_family_reference"], mocks["_extract_formulation_directive"]:
        result = hearing_mod.interpret_hearing("PatientTransportPlanner", ["dummy hearing"])

    assert result["match_type"] == "new_domain", result["match_type"]
    assert result["base_domain"] is None, result["base_domain"]
    assert result["needs_code_generation"] is True
    assert any("既存ドメイン" in m for m in result["classification"].get("missing_info", []))


def test_high_confidence_extension_verdict_is_not_overridden():
    classify_result = {
        "match_type": "base_problem", "base_domain": "LineChangeoverScheduler",
        "reason": "prob131は明記された転用先", "confidence": 0.85, "scenario_name_jp": "x",
        "scenario_description_jp": "", "missing_info": [], "is_dsl4_candidate": True,
    }
    structural_result = {"verdict": "extension", "confidence": 0.9, "reason": "段取り替え構造が共通"}

    with patch("domain_generator.hearing.classify_problem", return_value=classify_result), \
         patch("domain_generator.hearing.check_structural_similarity", return_value=structural_result), \
         patch("domain_generator.hearing.check_domain_exists", return_value={"exists": False}), \
         patch("domain_generator.hearing.check_axis_a_fit", return_value={"concern": ""}), \
         patch("domain_generator.hearing.detect_extension_gaps",
               return_value={"reusable_extensions": [], "extension_gaps": []}), \
         patch("domain_generator.hearing.generate_scenarios_from_schema",
               return_value={"scenarios": {}, "scenario_registrations": []}), \
         patch("domain_generator.hearing._validate_scenario_capacity", return_value=[]), \
         patch("domain_generator.hearing.derive_structural_requirements", return_value=None), \
         patch("domain_generator.hearing.lookup_family_reference", return_value=None), \
         patch("domain_generator.hearing._extract_formulation_directive", return_value=None):
        result = hearing_mod.interpret_hearing("ProductionLineSequencing", ["dummy hearing"])

    assert result["match_type"] == "base_problem", result["match_type"]
    assert result["base_domain"] == "LineChangeoverScheduler", result["base_domain"]


def test_override_skipped_when_force_new_domain():
    # force_new_domain指定時は元々classify_problem/check_structural_similarityどちらも
    # 呼ばれない経路（既存仕様）。ここではcheck_structural_similarityが万一
    # 呼ばれたら失敗させて、スキップされていることを証明する。
    with patch("domain_generator.hearing.check_structural_similarity",
               side_effect=AssertionError("should not be called when force_new_domain=True")), \
         patch("domain_generator.hearing.check_domain_exists", return_value={"exists": False}), \
         patch("domain_generator.hearing._ensure_repomix", return_value=True), \
         patch("domain_generator.hearing.derive_structural_requirements", return_value=None), \
         patch("domain_generator.hearing.lookup_family_reference", return_value=None), \
         patch("domain_generator.hearing._extract_formulation_directive", return_value=None):
        result = hearing_mod.interpret_hearing("Anything", ["dummy"], force_new_domain=True)
    assert result["match_type"] == "new_domain"


if __name__ == "__main__":
    tests = [
        test_identical_domain_name_shortcuts_to_same_scenario_no_llm,
        test_near_identical_domain_name_shortcuts_to_same_scenario,
        test_missing_source_files_falls_back_to_new_domain,
        test_low_confidence_verdict_overrides_to_new_domain,
        test_high_confidence_extension_verdict_is_not_overridden,
        test_override_skipped_when_force_new_domain,
    ]
    for t in tests:
        t()
        print(f"PASS: {t.__name__}")
    print("\nALL CHECKS PASSED")
