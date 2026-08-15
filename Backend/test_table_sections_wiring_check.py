"""
test_table_sections_wiring_check.py

2026-07-22追加: _check_table_sections_wiring()（build_table_section()を
呼んでいるのに ui_dsl["table_sections"] への配線を忘れているパターンの
静的検出）の単体テスト。

このチェックはdocplexに依存しない純粋な正規表現/文字列処理なので、
docplexやcpoptimizerバイナリが無いサンドボックスでも全件実行できる。

実行方法:
    cd Backend
    python -m pytest test_table_sections_wiring_check.py -v
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

import domain_generator as dg


def _warn_texts(code: str, path: str = "dummy_ui_converter.py") -> list[str]:
    return dg._check_table_sections_wiring(code, path)


class TestMissingWiringDetected:
    def test_built_but_never_assigned_to_key(self):
        code = '''
from dsl_transformer.table_sections import build_table_section

def convert_solver_to_ui(solver_output, business_dsl):
    section = build_table_section(section_id="items", title="items", columns=[], rows=[])
    return {"feasible": True, "kpi_cards": []}
'''
        warns = _warn_texts(code)
        assert len(warns) == 1
        assert "table_sections" in warns[0]

    def test_build_table_sections_from_issues_also_checked(self):
        code = '''
from dsl_transformer.table_sections import build_table_sections_from_issues

def convert_solver_to_ui(solver_output, business_dsl):
    section = build_table_sections_from_issues("issues", "Issues", solver_output.get("issues", []))
    return {"feasible": True}
'''
        assert len(_warn_texts(code)) == 1

    def test_variable_assigned_but_overwritten_before_use(self):
        """典型的な取り違えバグ: table_sectionsという名前の変数は作るが、
        実際に返す辞書には別のキー名で入れてしまっている（あるいは入れ忘れている）"""
        code = '''
from dsl_transformer.table_sections import build_table_section

def convert_solver_to_ui(solver_output, business_dsl):
    table_sections = [build_table_section(section_id="a", title="a", columns=[], rows=[])]
    return {
        "feasible": True,
        "sections": table_sections,  # キー名を間違えている
    }
'''
        assert len(_warn_texts(code)) == 1


class TestCorrectWiringNotFlagged:
    def test_dict_literal_key(self):
        code = '''
from dsl_transformer.table_sections import build_table_section

def f():
    sections = [build_table_section(section_id="a", title="a", columns=[], rows=[])]
    return {"table_sections": sections}
'''
        assert _warn_texts(code) == []

    def test_subscript_assignment(self):
        code = '''
from dsl_transformer.table_sections import build_table_section

def f():
    ui_dsl = {}
    ui_dsl["table_sections"] = [build_table_section(section_id="a", title="a", columns=[], rows=[])]
    return ui_dsl
'''
        assert _warn_texts(code) == []

    def test_single_quotes_variant(self):
        code = '''
from dsl_transformer.table_sections import build_table_section

def f():
    return {'table_sections': [build_table_section(section_id="a", title="a", columns=[], rows=[])]}
'''
        assert _warn_texts(code) == []

    def test_setdefault_variant(self):
        code = '''
from dsl_transformer.table_sections import build_table_section

def f():
    ui_dsl = {}
    ui_dsl.setdefault("table_sections", []).append(
        build_table_section(section_id="a", title="a", columns=[], rows=[])
    )
    return ui_dsl
'''
        assert _warn_texts(code) == []

    def test_early_return_empty_list_fallback_not_flagged_alone(self):
        """infeasible等の早期returnで table_sections: [] を返す分岐がある場合、
        キー自体は存在するので誤検知しない（制御フロー解析はしないため、
        本来のtable_sections組み立て箇所と同じ関数内にキーさえあればよい）"""
        code = '''
from dsl_transformer.table_sections import build_table_section

def f(feasible):
    if not feasible:
        return {"feasible": False, "table_sections": []}
    section = build_table_section(section_id="a", title="a", columns=[], rows=[])
    return {"feasible": True, "table_sections": [section]}
'''
        assert _warn_texts(code) == []


class TestNotApplicable:
    def test_no_table_sections_usage_at_all(self):
        """table_sectionsを使わないドメインは対象外（何も警告しない）"""
        code = '''
def f():
    return {"kpi_cards": [], "feasible": True}
'''
        assert _warn_texts(code) == []


class TestRealUiConvertersNoFalsePositives:
    """既存の実ui_converterファイル群に対して誤検知が出ないことの回帰確認"""

    def test_existing_ui_converters_have_no_new_warnings(self):
        backend_dir = Path(__file__).parent
        files = sorted((backend_dir / "dsl_transformer").glob("*_ui_converter.py"))
        assert files, "dsl_transformer/*_ui_converter.py が1件も見つかりません（テスト環境の問題）"

        offenders = {}
        for f in files:
            code = f.read_text(encoding="utf-8")
            warns = dg._check_table_sections_wiring(code, str(f))
            if warns:
                offenders[str(f)] = warns

        assert offenders == {}, f"既存ui_converterで誤検知: {offenders}"
