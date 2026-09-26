"""
test_cleanup_domain_import_guard.py

2026-09-26作成: cleanup_domain._find_import_blockers() の単体テスト。

ドメイン削除時、削除候補の.py（solver/converter/ui_converter/messages）を
他のファイルがモジュール先頭でimportしていたら削除を中止する仕組みの確認。
i18n/nurse_shift_weekly_cap_messages.py が事実上の共通ファイルになっており、
NurseShiftWeeklyCapを削除するとバックエンドが起動しなくなる潜在不具合の再発防止が目的。

実行方法:
    cd Backend
    python3 test_cleanup_domain_import_guard.py
(pytestが無い環境でも動くように、手動ドライバも用意している)
"""

import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from dsl_repository.cleanup_domain import _find_import_blockers, _py_candidates


def _make_backend(root: Path, files: dict) -> Path:
    backend = root / "Backend"
    for rel, text in files.items():
        p = backend / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(text, encoding="utf-8")
    return backend


def _blockers(files: dict) -> list:
    with tempfile.TemporaryDirectory() as d:
        backend = _make_backend(Path(d), files)
        return _find_import_blockers(backend, _py_candidates(backend, "foo_bar"), "FooBar")


BASE = {
    "solvers/foo_bar_solver.py": "x = 1\n",
    "i18n/foo_bar_messages.py": "def t(k): return k\n",
}


def test_top_level_import_blocks():
    files = dict(BASE, **{"app_core.py": "import os\nfrom i18n.foo_bar_messages import t\n"})
    assert _blockers(files) == ["Backend/app_core.py:2 (foo_bar_messages)"]


def test_function_local_import_does_not_block():
    files = dict(BASE, **{"solvers/helper.py": "def f():\n    from solvers.foo_bar_solver import x\n"})
    assert _blockers(files) == []


def test_test_file_does_not_block():
    files = dict(BASE, **{"solvers/test_foo_bar.py": "from solvers.foo_bar_solver import x\n"})
    assert _blockers(files) == []


def test_candidate_files_importing_each_other_do_not_block():
    files = dict(BASE, **{"solvers/foo_bar_solver.py": "from i18n.foo_bar_messages import t\n"})
    assert _blockers(files) == []


def test_similar_name_does_not_block():
    # foo_bar_messages と foo_bar_extra_messages を取り違えないこと
    files = dict(BASE, **{"app_core.py": "from i18n.foo_bar_extra_messages import t\n"})
    assert _blockers(files) == []


def test_no_candidate_files_returns_empty():
    files = {"app_core.py": "from i18n.foo_bar_messages import t\n"}
    assert _blockers(files) == []


if __name__ == "__main__":
    tests = [v for k, v in dict(globals()).items() if k.startswith("test_") and callable(v)]
    failed = 0
    for fn in tests:
        try:
            fn()
            print(f"PASS {fn.__name__}")
        except AssertionError as e:
            failed += 1
            print(f"FAIL {fn.__name__}: {e}")
    print(f"\n{len(tests) - failed}/{len(tests)} passed")
    sys.exit(1 if failed else 0)
