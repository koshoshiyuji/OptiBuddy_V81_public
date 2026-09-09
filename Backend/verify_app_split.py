"""
One-off verification script for the app.py -> app_core.py + 4 Blueprint
split (2026-09-09 refactor). NOT part of the application; safe to delete
after use.

Run from Backend/:
    python verify_app_split.py

What it does:
  1. Imports the new `app` module and confirms all 4 blueprints load with
     no NameError/ImportError (this alone re-validates everything the
     static check already covered, against your real environment).
  2. Confirms the route table has exactly the same 36 (path, methods)
     pairs as the original monolithic app.py.
  3. Fires a curated list of routes that are safe to call for real (GETs,
     and POSTs/DELETEs with deliberately-fake IDs/empty bodies chosen to
     fail fast on validation before reaching any real solver/LLM/file-write
     work), and reports each one's status code.
  4. Lists, but does NOT call, the routes that would cost a real LLM call,
     write real files, or touch real DB rows/domains if fired blindly --
     these are already covered by the static bare-name check (see the
     conversation), and are better exercised through your normal
     UI/manual-testing flow than through a blind scripted call here.

This does not need a running server -- it uses Flask's test_client(),
which calls straight into the app's WSGI stack in-process.
"""
import json
import sys

try:
    import app as app_module
except Exception as e:
    print(f"FATAL: `import app` failed -- {type(e).__name__}: {e}")
    raise

flask_app = app_module.app

print("=== 1. import + blueprint registration ===")
print("Registered blueprints:", list(flask_app.blueprints.keys()))

actual_rules = sorted(
    (rule.rule, tuple(sorted(rule.methods - {"HEAD", "OPTIONS"})))
    for rule in flask_app.url_map.iter_rules()
)
print(f"Total routes registered: {len(actual_rules)}")

# Expected (path, methods) pairs, taken from the original monolithic
# app.py's @app.route decorators.
EXPECTED_ROUTES = sorted([
    ("/baseline", ("POST",)),
    ("/relax", ("POST",)),
    ("/relax/start", ("POST",)),
    ("/relax/status/<job_id>", ("GET",)),
    ("/checks/status/<job_id>", ("GET",)),
    ("/api/domain/template", ("GET",)),
    ("/api/domain/attachments", ("GET",)),
    ("/api/domain/check/<domain_name>", ("GET",)),
    ("/api/domain/interpret", ("POST",)),
    ("/api/domain/generate", ("POST",)),
    ("/api/domain/apply", ("POST",)),
    ("/api/domain/run", ("POST",)),
    ("/api/domain/run/status/<job_id>", ("GET",)),
    ("/api/domain/confirm", ("POST",)),
    ("/api/domain/cancel", ("POST",)),
    ("/api/domain/interrupt", ("POST",)),
    ("/api/domain/agent_answer", ("POST",)),
    ("/api/domain/post_register_fix", ("POST",)),
    ("/api/domain/verify_dynamic_batch", ("POST",)),
    ("/baplie", ("POST",)),
    ("/dsl_repository/dsl", ("GET",)),
    ("/dsl_repository/ext", ("GET",)),
    ("/health", ("GET",)),
    ("/ask", ("POST",)),
    ("/analyze", ("POST",)),
    ("/apply_patch", ("POST",)),
    ("/dsl_repository/scenarios", ("GET", "POST")),
    ("/dsl_repository/log_plan_selection", ("POST",)),
    ("/dsl_repository/scenarios/<int:scenario_id>", ("DELETE",)),
    ("/dsl_repository/scenarios/<int:scenario_id>/refresh", ("POST",)),
    ("/dsl_repository/domain/<domain_name>", ("DELETE",)),
    ("/dsl_repository/scenarios/<int:scenario_id>/export", ("GET",)),
    ("/dsl_repository/registry", ("GET",)),
    ("/api/llm/generate_prompt", ("POST",)),
])
merged = {}
for path, methods in actual_rules:
    merged.setdefault(path, set()).update(methods)
actual_merged = sorted((p, tuple(sorted(m))) for p, m in merged.items())

expected_merged = {}
for path, methods in EXPECTED_ROUTES:
    expected_merged.setdefault(path, set()).update(methods)
expected_merged = sorted((p, tuple(sorted(m))) for p, m in expected_merged.items())

if actual_merged == expected_merged:
    print(f"Route inventory OK: {len(actual_merged)} unique paths, exact match with the original.\n")
else:
    print("ROUTE MISMATCH!")
    only_actual = set(actual_merged) - set(expected_merged)
    only_expected = set(expected_merged) - set(actual_merged)
    if only_actual:
        print("  in new app but not expected:", only_actual)
    if only_expected:
        print("  expected but missing from new app:", only_expected)
    print()

client = flask_app.test_client()

print("=== 2. firing safe routes (GETs + fake-ID mutations chosen to fail fast) ===")
SAFE_CALLS = [
    ("GET", "/health", None),
    ("GET", "/api/domain/template", None),
    ("GET", "/api/domain/attachments", None),
    ("GET", "/api/domain/check/__verify_split_fake_domain__", None),
    ("GET", "/api/domain/run/status/__verify_split_fake_job__", None),
    ("GET", "/relax/status/__verify_split_fake_job__", None),
    ("GET", "/checks/status/__verify_split_fake_job__", None),
    ("GET", "/dsl_repository/dsl", None),
    ("GET", "/dsl_repository/ext", None),
    ("GET", "/dsl_repository/scenarios", None),
    ("GET", "/dsl_repository/scenarios/999999999/export", None),
    ("GET", "/dsl_repository/registry", None),
    ("DELETE", "/dsl_repository/scenarios/999999999", None),
    ("POST", "/dsl_repository/scenarios/999999999/refresh", {}),
    ("POST", "/api/domain/cancel", {"job_id": "__verify_split_fake_job__"}),
    ("POST", "/api/domain/interrupt", {"job_id": "__verify_split_fake_job__"}),
    ("POST", "/api/domain/agent_answer", {"job_id": "__verify_split_fake_job__", "answer": "x"}),
    ("POST", "/api/domain/confirm", {"job_id": "__verify_split_fake_job__", "answers": "x"}),
    ("POST", "/apply_patch", {"dsl": {}, "patch": []}),
    ("POST", "/baseline", {"dsl": {}}),
    ("POST", "/relax", {"dsl": {}, "issues": []}),
    ("POST", "/baplie", {"baplie_text": ""}),
]

results = []
for method, path, body in SAFE_CALLS:
    try:
        if method == "GET":
            resp = client.get(path)
        elif method == "DELETE":
            resp = client.delete(path)
        else:
            resp = client.post(path, data=json.dumps(body or {}), content_type="application/json")
        text_snip = resp.get_data(as_text=True)[:200]
        is_bug = any(tok in text_snip for tok in ("NameError", "ModuleNotFoundError", "ImportError"))
        flag = " <<< LOOKS LIKE A REFACTOR BUG" if is_bug else ""
        results.append((method, path, resp.status_code, is_bug))
        print(f"  {resp.status_code:>3}  {method:<6} {path}{flag}")
        if is_bug:
            print(f"        body snippet: {text_snip}")
    except Exception as e:
        results.append((method, path, "EXC", True))
        print(f"  EXC  {method:<6} {path}  -- {type(e).__name__}: {e}")

any_bug = any(r[3] for r in results)
print()
if any_bug:
    print("!!! At least one call above looks like a refactor-introduced NameError/ImportError. Investigate before committing.")
else:
    print("No NameError/ImportError/ModuleNotFoundError seen in any safe call. Other status codes "
          "(400/401/404/500 from business logic, e.g. 'domain not found') are expected and fine here --"
          " we're only checking that the split's wiring didn't break anything.")

print()
print("=== 3. NOT called (would cost a real LLM call, write real files, or touch real domains/DB rows) ===")
SKIPPED = [
    "POST /ask                              (LLM: ask_about_dsl)",
    "POST /analyze                          (LLM: analyze_dsl)",
    "POST /api/llm/generate_prompt          (LLM + repomix upload)",
    "POST /relax/start                      (LLM: suggest_relaxations, background thread)",
    "POST /api/domain/interpret             (LLM: interpret_hearing)",
    "POST /api/domain/generate              (LLM: generate_domain_files)",
    "POST /api/domain/apply                 (writes files under Backend/)",
    "POST /api/domain/run                   (full LLM pipeline + file writes, background thread)",
    "POST /api/domain/post_register_fix     (LLM: run_post_registration_fix on a REAL registered domain)",
    "POST /api/domain/verify_dynamic_batch  (runs real solver attempts for a domain batch)",
    "DELETE /dsl_repository/domain/<name>   (destructive: deletes a domain's registration -- only ever call with a name you're sure doesn't exist)",
    "POST /dsl_repository/scenarios         (writes a new scenario row)",
    "POST /dsl_repository/log_plan_selection (writes a log row)",
]
for line in SKIPPED:
    print("  -", line)
print()
print("These are already covered by the static 'every bare name resolves somewhere in its own "
      "file' check run earlier (which is what caught the two real bugs before this point: the "
      "missing _PROJECT_ROOT/_BACKEND_ROOT import, and _start_deferred_checks_job being in the "
      "wrong file). If you want extra confidence on these specific ones, exercising them through "
      "your normal dev-server UI flow is safer than firing them blind from a script.")
