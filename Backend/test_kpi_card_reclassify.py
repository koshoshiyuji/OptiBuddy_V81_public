"""
2026-09-29追加（Koshoshi合意）: KPIカード配線の静的チェック（推測）を、
Gate2動的検証の実出力（baselineのkpi_card_ids）で確かめる処理のテスト。
背景は domain_generator/static_checks.py の reclassify_kpi_card_warnings() 参照。
"""

from domain_generator.static_checks import _check_kpi_card_coverage, reclassify_kpi_card_warnings

_I18N = (
    'MESSAGES = {"ja": {"kpi.max_load.label": "最大負荷", '
    '"kpi.worker_count.label": "ワーカー数", "kpi.idle.label": "遊休時間"}}'
)
_HEARING = ["## 7. 画面で確認したい情報\n- 最大負荷（作業員ごとの合計時間の最大値）\n- 遊休時間"]


def _static_warnings():
    # シングルクォートで書かれたカード定義は、静的チェックでは「無い」と誤判定される
    ui_code = "kpi_cards = [{'id': 'max_load'}]"
    return _check_kpi_card_coverage(ui_code, _I18N, "demo") + ["（無関係な別の指摘）"]


def test_static_check_flags_all_three_as_missing():
    assert len(_static_warnings()) == 4


def test_card_present_in_real_output_is_removed():
    kept, hearing = reclassify_kpi_card_warnings(_static_warnings(), ["max_load"], _I18N, _HEARING)
    assert not any("max_load" in w for w in kept + hearing)


def test_missing_card_named_in_hearing_becomes_hearing_display():
    kept, hearing = reclassify_kpi_card_warnings(_static_warnings(), ["max_load"], _I18N, _HEARING)
    assert hearing == [
        "ヒアリングで画面に表示したいと指定された『遊休時間』が、標準シナリオを実際に解いた"
        "結果の画面（上部のカード）に表示されていません。"
    ]


def test_missing_card_not_in_hearing_stays_advisory():
    kept, _ = reclassify_kpi_card_warnings(_static_warnings(), ["max_load"], _I18N, _HEARING)
    assert any("worker_count" in w for w in kept)
    assert "（無関係な別の指摘）" in kept


def test_card_without_id_but_same_label_is_treated_as_present():
    """idを持たないカード定義でも、表示ラベルが一致すれば「表示されている」とみなす。"""
    kept, hearing = reclassify_kpi_card_warnings(
        _static_warnings(), [], _I18N, _HEARING, kpi_card_labels=["最大負荷", "遊休時間"]
    )
    assert not any("max_load" in w or "idle" in w for w in kept)
    assert hearing == []


def test_no_ui_output_keeps_static_result_unchanged():
    w = _static_warnings()
    kept, hearing = reclassify_kpi_card_warnings(w, None, _I18N, _HEARING)
    assert kept == w and hearing == []


def test_dynamic_verification_records_kpi_card_ids(monkeypatch):
    """既存ドメインのbaselineを実際に解き、UI出力のKPIカードidが記録されること。"""
    monkeypatch.setenv("SOLVER_BACKEND", "oss")
    monkeypatch.delenv("DEFAULT_SOLVER_ENGINE", raising=False)
    from domain_generator.gate2 import run_gate2_dynamic_verification
    # nurse_shift_weekly_capのKPIカードはidを持つ（total_cost等）。
    # capital_project_selectorのようにidの無いカード定義のドメインは表示ラベルで記録される。
    report = run_gate2_dynamic_verification("nurse_shift_weekly_cap")
    entry = report["scenarios"]["baseline"]
    assert entry["status"] == "ok"
    assert "total_cost" in entry.get("kpi_card_ids", [])
    report2 = run_gate2_dynamic_verification("capital_project_selector")
    assert "期待効果合計" in report2["scenarios"]["baseline"].get("kpi_card_labels", [])
