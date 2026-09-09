"""
Backend/app.py -- entry point.

Creates the Flask app (see app_core.py for app/CORS/logging/auth/job-store
setup shared across all route modules) and registers each route Blueprint.
See the 2026-09-09 refactor commit for the split rationale (was a single
2683-line file; see git history / REFACTOR notes for the original content).
"""
import os

from app_core import app

from routes_domain_registration import bp as domain_registration_bp
from routes_solve import bp as solve_bp
from routes_dsl_repository import bp as dsl_repository_bp
from routes_misc import bp as misc_bp

app.register_blueprint(domain_registration_bp)
app.register_blueprint(solve_bp)
app.register_blueprint(dsl_repository_bp)
app.register_blueprint(misc_bp)




if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    # use_reloader=False: ドメイン登録フロー(Gate2動的検証・copy_domain_for_extension等)が
    # Backend配下に生成コードを書き込むため、reloaderが有効だとその書き込みを検知して
    # プロセスごと再起動し、進行中の登録処理が道連れで落ちる（2026-07-11発覚）。
    # デバッガ機能(debug=True)は残し、自動再起動のみ無効化する。
    app.run(debug=True, use_reloader=False, host="0.0.0.0", port=port)
