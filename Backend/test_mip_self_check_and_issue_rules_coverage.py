"""
test_mip_self_check_and_issue_rules_coverage.py

2026-09-01追加、2026-09-03改訂: ソリューションチェッカー自動紐付け（Tier1）の
単体テスト。

Tier1: _check_mip_self_verification() — MIPドメイン（docplex.mp）でis_valid_solution()
       による自己検証が抜けている場合のGate2静的チェック（blocking）。

2026-09-03、Koshoshi合意によりTier2（独立ソリューションチェッカーのISSUE_RULESへの
自動紐付け）は全廃した。経緯は二段階:
  1. 当初のTier2は「新規ドメインがISSUE_RULESに未登録の場合、advisoryとして
     知らせるだけ」の静的リマインダー（_check_issue_rules_coverage()）のみだった。
  2. 同日中に「LLMでその場チェッカー本体を生成し、合成テストで自己検証してから
     登録に含める」自動生成方式に拡張したが、実機テスト（ReviewDocument登録、
     2回とも失敗）で、生成コストに対して品質保証の実効性が不十分だったため
     （3回呼び出し分割後も自己テストが構文エラーで失敗する事象が解消しな
     かった）取りやめた。
  3. さらにKoshoshiの指摘（「このcheckerを取りやめるのだからリマインダーも
     不要」）を受け、1.のリマインダーも含めてTier2を完全に削除した。
この改訂に伴い、Tier2関連のテスト（_extract_code_block・_run_issue_rule_self_test・
generate_and_verify_issue_rule_checker・_check_issue_rules_coverage 向け）は
すべて本ファイルから削除した。既存17ドメイン（batch1-6、コミットc213ac8）の
ISSUE_RULESエントリと、それらを検証する solvers/base/test_issue_rules.py
（173件）は影響を受けない。ユーザーからカスタムドメインの開発が依頼された場合、
チェッカーを手動で追加することは今後も検討対象（自動化はしない）。

実行方法:
    cd Backend
    python -m pytest test_mip_self_check_and_issue_rules_coverage.py -v
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

import domain_generator as dg


# Tier1: _check_mip_self_verification
# ─────────────────────────────────────────────────────────────

class TestMipSelfVerificationCheck:
    def test_mip_domain_without_is_valid_solution_is_flagged(self):
        code = """
from docplex.mp.model import Model
mdl = Model()
sol = mdl.solve()
"""
        warnings = dg._check_mip_self_verification(code, "Backend/solvers/x_solver.py")
        assert len(warnings) == 1
        assert "is_valid_solution" in warnings[0]

    def test_mip_domain_with_is_valid_solution_not_flagged(self):
        code = """
from docplex.mp.model import Model
mdl = Model()
sol = mdl.solve()
if not sol.is_valid_solution(tolerance=1e-6):
    pass
"""
        assert dg._check_mip_self_verification(code, "Backend/solvers/x_solver.py") == []

    def test_non_mip_domain_not_flagged(self):
        code = """
from docplex.cp.model import CpoModel
mdl = CpoModel()
msol = mdl.solve()
"""
        assert dg._check_mip_self_verification(code, "Backend/solvers/x_solver.py") == []

    def test_alternate_import_style_detected(self):
        code = "import docplex.mp.model\nmdl = docplex.mp.model.Model()\nsol = mdl.solve()\n"
        warnings = dg._check_mip_self_verification(code, "Backend/solvers/x_solver.py")
        assert len(warnings) == 1

    def test_regression_crew_duty_scheduler_flagged(self):
        code = Path("solvers/crew_duty_scheduler_solver.py").read_text(encoding="utf-8")
        warnings = dg._check_mip_self_verification(code, "Backend/solvers/crew_duty_scheduler_solver.py")
        assert len(warnings) == 1, "既知の未対応ドメイン（is_valid_solution未実装）のはず"

    def test_regression_depot_route_planner_flagged(self):
        code = Path("solvers/depot_route_planner_solver.py").read_text(encoding="utf-8")
        warnings = dg._check_mip_self_verification(code, "Backend/solvers/depot_route_planner_solver.py")
        assert len(warnings) == 1, "既知の未対応ドメイン（is_valid_solution未実装）のはず"

    def test_regression_store_site_not_flagged(self):
        code = Path("solvers/store_site_solver.py").read_text(encoding="utf-8")
        assert dg._check_mip_self_verification(code, "Backend/solvers/store_site_solver.py") == []
