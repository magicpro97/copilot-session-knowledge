#!/usr/bin/env python3
"""
tests/test_learn_cerebrum_autoupdate.py — Regression tests for learn.py --update-cerebrum.

Verifies the opt-in auto-update path that regenerates CEREBRUM.md after a
successful learn write.  Uses import-level mocking so no real knowledge.db
is touched and no real subprocess is spawned.

Scenarios covered:
  1. --update-cerebrum absent  → subprocess.run NOT called (opt-in only)
  2. --update-cerebrum present + successful write → subprocess.run called
  3. --cerebrum-output honoured in subprocess args
  4. --cerebrum-sections honoured in subprocess args (configurable sections)
  5. export subprocess failure (non-zero) → main() exits non-zero
  6. entry rejected by injection scan (entry_id == -1) → auto-update NOT triggered
  7. default output path is CEREBRUM.md when --cerebrum-output not specified
  8. --sections absent → --sections NOT forwarded to subprocess
  9. --update-cerebrum works with --json mode
 10. _auto_update_cerebrum unit: correct cmd shape, sections=None
 11. _auto_update_cerebrum unit: sections forwarded
 12. _auto_update_cerebrum unit: subprocess exception → returns 1
 13. --from-file + --update-cerebrum → subprocess IS called (issue-1 regression)
 14. --from-file without --update-cerebrum → subprocess NOT called
 15. JSON mode: subprocess.run called with stdout=DEVNULL (issue-2 regression)
 16. _auto_update_cerebrum unit: TimeoutExpired → returns 1 (issue-3 regression)
 17. timeout from main() path → non-zero exit code (issue-3 integration)
 18. --from-file + --json + --update-cerebrum → stdout=DEVNULL (blocker-1 regression)
 19. --from-file file-not-found → auto-update NOT triggered (blocker-2 regression)
 20. --from-file no-entries → auto-update NOT triggered (blocker-2 regression)
 21. --from-file file-not-found → exit non-zero
 22. --from-file no importable entries → exit non-zero
"""

import importlib.util
import io
import os
import sqlite3
import subprocess
import sys
import tempfile
import unittest.mock
from contextlib import ExitStack
from pathlib import Path

if os.name == "nt":
    for _s in (sys.stdout, sys.stderr):
        if hasattr(_s, "reconfigure"):
            _s.reconfigure(encoding="utf-8", errors="replace")

_HERE = Path(__file__).parent.parent

# ---------------------------------------------------------------------------
# Load module under test
# ---------------------------------------------------------------------------

def _load(name: str, rel_path: str):
    spec = importlib.util.spec_from_file_location(name, str(_HERE / rel_path))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


learn = _load("learn_for_cerebrum_test", "learn.py")

# ---------------------------------------------------------------------------
# Simple test harness (consistent with other test files in this repo)
# ---------------------------------------------------------------------------
_PASS = 0
_FAIL = 0


def test(name: str, expr: bool) -> None:
    global _PASS, _FAIL
    if expr:
        _PASS += 1
        print(f"  PASS  {name}")
    else:
        _FAIL += 1
        print(f"  FAIL  {name}")


# ---------------------------------------------------------------------------
# Minimal SQLite schema matching knowledge_entries expectations in learn.py
# ---------------------------------------------------------------------------

_SCHEMA_STMTS = [
    """CREATE TABLE knowledge_entries (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        session_id TEXT, category TEXT, title TEXT, stable_id TEXT,
        content TEXT, tags TEXT DEFAULT '', confidence REAL DEFAULT 0.5,
        occurrence_count INTEGER DEFAULT 1,
        first_seen TEXT, last_seen TEXT, wing TEXT DEFAULT '', room TEXT DEFAULT '',
        facts TEXT DEFAULT '[]', est_tokens INTEGER DEFAULT 0,
        task_id TEXT DEFAULT '', affected_files TEXT DEFAULT '[]')""",
    """CREATE TABLE ke_fts (
        rowid INTEGER PRIMARY KEY, title TEXT, content TEXT, tags TEXT,
        category TEXT, wing TEXT, room TEXT, facts TEXT)""",
    """CREATE TABLE sync_state (
        key TEXT PRIMARY KEY, value TEXT,
        updated_at TEXT DEFAULT CURRENT_TIMESTAMP)""",
    """CREATE TABLE sync_table_policies (
        table_name TEXT PRIMARY KEY, sync_scope TEXT)""",
]


def _make_db() -> sqlite3.Connection:
    db = sqlite3.connect(":memory:")
    db.row_factory = sqlite3.Row
    for stmt in _SCHEMA_STMTS:
        db.execute(stmt)
    db.commit()
    return db


def _seed_entry(db: sqlite3.Connection, entry_id: int = 42) -> None:
    """Insert a minimal knowledge entry so JSON-mode fetch doesn't return None."""
    db.execute(
        "INSERT INTO knowledge_entries "
        "(id, category, title, content, tags, confidence, session_id, task_id, "
        "affected_files, facts, occurrence_count, last_seen) "
        "VALUES (?, 'pattern', 'Test Title', 'Test content', '', 0.7, 'sess1', "
        "'', '[]', '[]', 1, '2025-01-01')",
        (entry_id,),
    )
    db.commit()


# ---------------------------------------------------------------------------
# Test helper: run learn.main() with patched DB, with_retry, and subprocess
# ---------------------------------------------------------------------------

class _FakeSubprocessResult:
    def __init__(self, returncode: int = 0):
        self.returncode = returncode


def _run_learn(
    argv: list[str],
    *,
    entry_id_return: int = 42,
    subprocess_returncode: int = 0,
    suppress_io: bool = True,
) -> tuple[list[list[str]], int | None]:
    """
    Invoke learn.main() with fully mocked I/O dependencies.

    Returns:
        (subprocess_calls, exit_code)
        subprocess_calls: list of cmd lists passed to subprocess.run
        exit_code: None if main() returned normally, int if SystemExit was raised
    """
    subprocess_calls: list[list[str]] = []

    def fake_subprocess_run(cmd, **kwargs):
        subprocess_calls.append(list(cmd))
        return _FakeSubprocessResult(subprocess_returncode)

    # DB for get_db() — seeded with entry so JSON mode can fetch it
    shared_db = _make_db()
    if entry_id_return >= 0:
        _seed_entry(shared_db, entry_id_return)

    def fake_get_db():
        # Return a fresh connection over the same in-memory DB (not possible with
        # :memory:, so we return the same connection object — sufficient for tests
        # because the tests are single-threaded).
        return shared_db

    def fake_with_retry(func, *args, **kwargs):
        # Skip the real add_entry; return the configured entry_id
        return entry_id_return

    exit_code: int | None = None

    null_io = io.StringIO()

    ctx_managers = [
        unittest.mock.patch.object(learn, "with_retry", fake_with_retry),
        unittest.mock.patch.object(learn, "get_db", fake_get_db),
        unittest.mock.patch.object(learn.subprocess, "run", fake_subprocess_run),
    ]
    if suppress_io:
        ctx_managers += [
            unittest.mock.patch("sys.stdout", null_io),
            unittest.mock.patch("sys.stderr", null_io),
        ]

    full_argv = ["learn.py"] + argv
    with unittest.mock.patch.object(sys, "argv", full_argv):
        with ExitStack() as stack:
            for cm in ctx_managers:
                stack.enter_context(cm)
            try:
                learn.main()
            except SystemExit as exc:
                exit_code = exc.code

    return subprocess_calls, exit_code


# ===========================================================================
# Test 1 — opt-in only: no --update-cerebrum → subprocess NOT called
# ===========================================================================

def test_no_autoupdate_without_flag():
    calls, code = _run_learn(["--pattern", "Title", "Some content", "--skip-gate", "--skip-scan"])
    test("no_autoupdate: subprocess.run not called without flag", len(calls) == 0)
    test("no_autoupdate: exit code is None (normal return)", code is None)


# ===========================================================================
# Test 2 — --update-cerebrum triggers subprocess.run with correct script
# ===========================================================================

def test_autoupdate_triggers_subprocess():
    calls, code = _run_learn([
        "--pattern", "Title", "Some content",
        "--update-cerebrum", "--skip-gate", "--skip-scan",
    ])
    test("autoupdate: subprocess.run called once", len(calls) == 1)
    test("autoupdate: export-cerebrum.py in cmd", any("export-cerebrum.py" in str(a) for a in calls[0]))
    test("autoupdate: --output in cmd", "--output" in calls[0])
    test("autoupdate: exit code is None (success)", code is None)


# ===========================================================================
# Test 3 — --cerebrum-output is forwarded to subprocess
# ===========================================================================

def test_cerebrum_output_flag_forwarded():
    calls, _ = _run_learn([
        "--pattern", "Title", "Some content",
        "--update-cerebrum", "--cerebrum-output", "docs/CEREBRUM.md",
        "--skip-gate", "--skip-scan",
    ])
    test("cerebrum_output: subprocess called", len(calls) == 1)
    output_idx = calls[0].index("--output") if "--output" in calls[0] else -1
    test("cerebrum_output: --output present in cmd", output_idx != -1)
    actual_path = calls[0][output_idx + 1] if output_idx != -1 else ""
    test("cerebrum_output: custom path passed through", actual_path == "docs/CEREBRUM.md")


# ===========================================================================
# Test 4 — --cerebrum-sections is forwarded (configurable sections)
# ===========================================================================

def test_cerebrum_sections_flag_forwarded():
    calls, _ = _run_learn([
        "--mistake", "Title", "Some mistake content",
        "--update-cerebrum", "--cerebrum-sections", "mistakes,decisions",
        "--skip-gate", "--skip-scan",
    ])
    test("cerebrum_sections: subprocess called", len(calls) == 1)
    test("cerebrum_sections: --sections in cmd", "--sections" in calls[0])
    sections_idx = calls[0].index("--sections") if "--sections" in calls[0] else -1
    actual_sections = calls[0][sections_idx + 1] if sections_idx != -1 else ""
    test("cerebrum_sections: correct value forwarded", actual_sections == "mistakes,decisions")


# ===========================================================================
# Test 5 — export subprocess failure → main() exits non-zero
# ===========================================================================

def test_autoupdate_failure_surfaces_exit_code():
    calls, code = _run_learn(
        [
            "--pattern", "Title", "Some content",
            "--update-cerebrum", "--skip-gate", "--skip-scan",
        ],
        subprocess_returncode=1,
    )
    test("failure: subprocess.run called", len(calls) == 1)
    test("failure: exit code is non-zero", code is not None and code != 0)


# ===========================================================================
# Test 6 — rejected entry (injection scan) → auto-update NOT triggered
# ===========================================================================

def test_rejected_entry_skips_autoupdate():
    calls, code = _run_learn(
        [
            "--pattern", "Title", "Some content",
            "--update-cerebrum", "--skip-gate", "--skip-scan",
        ],
        entry_id_return=-1,
    )
    test("rejected: subprocess.run NOT called", len(calls) == 0)


# ===========================================================================
# Test 7 — default output path is CEREBRUM.md
# ===========================================================================

def test_default_output_path_is_cerebrum_md():
    calls, _ = _run_learn([
        "--pattern", "Title", "Some content",
        "--update-cerebrum", "--skip-gate", "--skip-scan",
    ])
    test("default_output: subprocess called", len(calls) == 1)
    output_idx = calls[0].index("--output") if "--output" in calls[0] else -1
    actual_path = calls[0][output_idx + 1] if output_idx != -1 else ""
    test("default_output: path is CEREBRUM.md", actual_path == "CEREBRUM.md")


# ===========================================================================
# Test 8 — --sections absent → --sections NOT forwarded to subprocess
# ===========================================================================

def test_no_sections_flag_when_not_specified():
    calls, _ = _run_learn([
        "--pattern", "Title", "Some content",
        "--update-cerebrum", "--skip-gate", "--skip-scan",
    ])
    test("no_sections: --sections not added when not specified", "--sections" not in calls[0])


# ===========================================================================
# Test 9 — --update-cerebrum works with --json mode
# ===========================================================================

def test_autoupdate_with_json_mode():
    calls, code = _run_learn([
        "--pattern", "Title", "Some content",
        "--update-cerebrum", "--json", "--skip-gate", "--skip-scan",
    ])
    test("json_mode: subprocess.run called", len(calls) == 1)
    test("json_mode: exit code None (success)", code is None)


# ===========================================================================
# Test 10 — _auto_update_cerebrum unit: correct cmd shape, sections=None
# ===========================================================================

def test_auto_update_cerebrum_unit_no_sections():
    """Direct unit test of the _auto_update_cerebrum helper."""
    calls = []

    def fake_run(cmd, **kwargs):
        calls.append(list(cmd))
        return _FakeSubprocessResult(0)

    with unittest.mock.patch.object(learn.subprocess, "run", fake_run):
        rc = learn._auto_update_cerebrum("OUT.md", None)

    test("unit_no_sections: returns 0 on success", rc == 0)
    test("unit_no_sections: --output present", "--output" in calls[0])
    test("unit_no_sections: --sections absent", "--sections" not in calls[0])
    output_idx = calls[0].index("--output")
    test("unit_no_sections: output path correct", calls[0][output_idx + 1] == "OUT.md")


# ===========================================================================
# Test 11 — _auto_update_cerebrum unit: sections forwarded
# ===========================================================================

def test_auto_update_cerebrum_unit_with_sections():
    calls = []

    def fake_run(cmd, **kwargs):
        calls.append(list(cmd))
        return _FakeSubprocessResult(0)

    with unittest.mock.patch.object(learn.subprocess, "run", fake_run):
        rc = learn._auto_update_cerebrum("X.md", "learnings,mistakes")

    test("unit_with_sections: returns 0", rc == 0)
    test("unit_with_sections: --sections present", "--sections" in calls[0])
    sec_idx = calls[0].index("--sections")
    test("unit_with_sections: sections value correct", calls[0][sec_idx + 1] == "learnings,mistakes")


# ===========================================================================
# Test 12 — _auto_update_cerebrum unit: subprocess exception → returns 1
# ===========================================================================

def test_auto_update_cerebrum_exception_returns_1():
    def fake_run(cmd, **kwargs):
        raise OSError("exporter not found")

    with unittest.mock.patch.object(learn.subprocess, "run", fake_run), \
         unittest.mock.patch("sys.stderr", io.StringIO()):
        rc = learn._auto_update_cerebrum("OUT.md", None)

    test("unit_exception: returns 1 on OSError", rc == 1)


# ===========================================================================
# Test 13 — --from-file + --update-cerebrum → subprocess IS called (issue 1)
# ===========================================================================

def _run_learn_from_file(
    argv: list[str],
    *,
    subprocess_returncode: int = 0,
    fake_import_return: int = 1,
) -> tuple[list[list[str]], int | None]:
    """Helper for --from-file tests: mocks import_from_file and subprocess.run.

    fake_import_return controls what import_from_file returns:
      >0  → success (auto-update may run)
       0  → failure (auto-update must NOT run)
    """
    subprocess_calls: list[list[str]] = []

    def fake_run(cmd, **kwargs):
        subprocess_calls.append(list(cmd))
        return _FakeSubprocessResult(subprocess_returncode)

    _import_rv = fake_import_return

    def fake_import(path: str) -> int:
        return _import_rv

    exit_code: int | None = None
    full_argv = ["learn.py"] + argv

    with ExitStack() as stack:
        stack.enter_context(unittest.mock.patch.object(learn.subprocess, "run", fake_run))
        stack.enter_context(unittest.mock.patch.object(learn, "import_from_file", fake_import))
        stack.enter_context(unittest.mock.patch("sys.stdout", io.StringIO()))
        stack.enter_context(unittest.mock.patch("sys.stderr", io.StringIO()))
        stack.enter_context(unittest.mock.patch.object(sys, "argv", full_argv))
        try:
            learn.main()
        except SystemExit as exc:
            exit_code = exc.code

    return subprocess_calls, exit_code


def test_from_file_with_autoupdate():
    """--from-file + --update-cerebrum must trigger the cerebrum refresh."""
    calls, code = _run_learn_from_file([
        "--from-file", "notes.md", "--update-cerebrum",
    ], fake_import_return=1)
    test("from_file_autoupdate: subprocess called once", len(calls) == 1)
    test("from_file_autoupdate: export-cerebrum.py in cmd",
         any("export-cerebrum.py" in str(a) for a in calls[0]))
    test("from_file_autoupdate: exit code None (success)", code is None)


# ===========================================================================
# Test 14 — --from-file alone → subprocess NOT called
# ===========================================================================

def test_from_file_without_autoupdate():
    """--from-file without --update-cerebrum must NOT trigger the refresh."""
    calls, _ = _run_learn_from_file(["--from-file", "notes.md"])
    test("from_file_no_autoupdate: subprocess.run not called", len(calls) == 0)


# ===========================================================================
# Test 15 — JSON mode subprocess stdout must be DEVNULL (issue 2)
# ===========================================================================

def test_json_mode_subprocess_stdout_is_devnull():
    """In JSON mode, subprocess.run must receive stdout=DEVNULL."""
    captured_kwargs: list[dict] = []

    def fake_run(cmd, **kwargs):
        captured_kwargs.append(kwargs)
        return _FakeSubprocessResult(0)

    shared_db = _make_db()
    _seed_entry(shared_db, 42)

    with ExitStack() as stack:
        stack.enter_context(unittest.mock.patch.object(learn, "with_retry", lambda f, *a, **kw: 42))
        stack.enter_context(unittest.mock.patch.object(learn, "get_db", lambda: shared_db))
        stack.enter_context(unittest.mock.patch.object(learn.subprocess, "run", fake_run))
        stack.enter_context(unittest.mock.patch("sys.stdout", io.StringIO()))
        stack.enter_context(unittest.mock.patch("sys.stderr", io.StringIO()))
        stack.enter_context(unittest.mock.patch.object(sys, "argv", [
            "learn.py", "--pattern", "Title", "Content",
            "--update-cerebrum", "--json", "--skip-gate", "--skip-scan",
        ]))
        try:
            learn.main()
        except SystemExit:
            pass

    test("json_stdout: subprocess was called", len(captured_kwargs) == 1)
    test("json_stdout: stdout=DEVNULL passed to subprocess.run",
         captured_kwargs[0].get("stdout") == subprocess.DEVNULL)


# ===========================================================================
# Test 16 — _auto_update_cerebrum unit: TimeoutExpired → returns 1 (issue 3)
# ===========================================================================

def test_auto_update_cerebrum_timeout_returns_1():
    """TimeoutExpired from subprocess must be caught and return 1."""
    def fake_run(cmd, **kwargs):
        raise subprocess.TimeoutExpired(cmd, 120)

    with unittest.mock.patch.object(learn.subprocess, "run", fake_run), \
         unittest.mock.patch("sys.stderr", io.StringIO()):
        rc = learn._auto_update_cerebrum("OUT.md", None)

    test("unit_timeout: returns 1 on TimeoutExpired", rc == 1)


# ===========================================================================
# Test 17 — timeout surfaces as non-zero exit from main() (issue 3 integration)
# ===========================================================================

def test_autoupdate_timeout_surfaces_exit_code():
    """Timeout in the exporter subprocess must propagate as a non-zero exit."""
    subprocess_calls: list[list[str]] = []

    def fake_run_timeout(cmd, **kwargs):
        subprocess_calls.append(list(cmd))
        raise subprocess.TimeoutExpired(cmd, 120)

    shared_db = _make_db()
    _seed_entry(shared_db, 42)

    exit_code: int | None = None
    with ExitStack() as stack:
        stack.enter_context(unittest.mock.patch.object(learn, "with_retry", lambda f, *a, **kw: 42))
        stack.enter_context(unittest.mock.patch.object(learn, "get_db", lambda: shared_db))
        stack.enter_context(unittest.mock.patch.object(learn.subprocess, "run", fake_run_timeout))
        stack.enter_context(unittest.mock.patch("sys.stdout", io.StringIO()))
        stack.enter_context(unittest.mock.patch("sys.stderr", io.StringIO()))
        stack.enter_context(unittest.mock.patch.object(sys, "argv", [
            "learn.py", "--pattern", "Title", "Content",
            "--update-cerebrum", "--skip-gate", "--skip-scan",
        ]))
        try:
            learn.main()
        except SystemExit as exc:
            exit_code = exc.code

    test("timeout_integration: subprocess called", len(subprocess_calls) == 1)
    test("timeout_integration: exit code non-zero", exit_code is not None and exit_code != 0)


# ===========================================================================
# Test 18 — --from-file + --json + --update-cerebrum → stdout=DEVNULL (blocker 1)
# ===========================================================================

def test_from_file_json_mode_subprocess_stdout_is_devnull():
    """--from-file + --json + --update-cerebrum must forward stdout=DEVNULL to subprocess."""
    captured_kwargs: list[dict] = []

    def fake_run(cmd, **kwargs):
        captured_kwargs.append(kwargs)
        return _FakeSubprocessResult(0)

    def fake_import(path: str) -> int:
        return 1  # pretend the import succeeded

    exit_code: int | None = None
    full_argv = ["learn.py", "--from-file", "notes.md", "--json", "--update-cerebrum"]

    with ExitStack() as stack:
        stack.enter_context(unittest.mock.patch.object(learn.subprocess, "run", fake_run))
        stack.enter_context(unittest.mock.patch.object(learn, "import_from_file", fake_import))
        stack.enter_context(unittest.mock.patch("sys.stdout", io.StringIO()))
        stack.enter_context(unittest.mock.patch("sys.stderr", io.StringIO()))
        stack.enter_context(unittest.mock.patch.object(sys, "argv", full_argv))
        try:
            learn.main()
        except SystemExit as exc:
            exit_code = exc.code

    test("from_file_json_devnull: subprocess was called", len(captured_kwargs) == 1)
    test("from_file_json_devnull: stdout=DEVNULL forwarded to subprocess.run",
         captured_kwargs[0].get("stdout") == subprocess.DEVNULL)
    test("from_file_json_devnull: exit code None (success)", exit_code is None)


# ===========================================================================
# Test 19 — --from-file file-not-found → auto-update must NOT run (blocker 2)
# ===========================================================================

def test_from_file_not_found_skips_autoupdate():
    """When import_from_file returns 0 (file not found), auto-update must NOT trigger."""
    calls, _ = _run_learn_from_file(
        ["--from-file", "nonexistent.md", "--update-cerebrum"],
        fake_import_return=0,
    )
    test("from_file_not_found: subprocess.run NOT called", len(calls) == 0)


# ===========================================================================
# Test 20 — --from-file no-entries → auto-update must NOT run (blocker 2)
# ===========================================================================

def test_from_file_no_entries_skips_autoupdate():
    """When import_from_file returns 0 (no entries found), auto-update must NOT trigger."""
    calls, _ = _run_learn_from_file(
        ["--from-file", "empty.md", "--update-cerebrum"],
        fake_import_return=0,
    )
    test("from_file_no_entries: subprocess.run NOT called", len(calls) == 0)


# ===========================================================================
# Test 21 — --from-file file-not-found → exit non-zero
# ===========================================================================

def test_from_file_not_found_exits_nonzero():
    """File not found must cause main() to exit non-zero (not silently succeed)."""
    calls, code = _run_learn_from_file(
        ["--from-file", "nonexistent.md"],
        fake_import_return=0,
    )
    test("from_file_not_found_exit: subprocess.run NOT called", len(calls) == 0)
    test("from_file_not_found_exit: exit code is non-zero", code is not None and code != 0)


# ===========================================================================
# Test 22 — --from-file no importable entries → exit non-zero
# ===========================================================================

def test_from_file_no_entries_exits_nonzero():
    """No importable entries must cause main() to exit non-zero (not silently succeed)."""
    calls, code = _run_learn_from_file(
        ["--from-file", "empty.md"],
        fake_import_return=0,
    )
    test("from_file_no_entries_exit: subprocess.run NOT called", len(calls) == 0)
    test("from_file_no_entries_exit: exit code is non-zero", code is not None and code != 0)


def test_import_from_file_not_found_uses_stderr():
    """File-not-found diagnostics should go to stderr, not stdout."""
    captured_out = io.StringIO()
    captured_err = io.StringIO()
    with unittest.mock.patch("sys.stdout", captured_out), unittest.mock.patch("sys.stderr", captured_err):
        rv = learn.import_from_file("nonexistent.md")
    test("from_file_not_found_stderr: returns 0", rv == 0)
    test("from_file_not_found_stderr: stdout stays empty", captured_out.getvalue() == "")
    test("from_file_not_found_stderr: stderr has message", "Error: File not found" in captured_err.getvalue())


def test_import_from_file_no_entries_uses_stderr():
    """No-entry diagnostics should go to stderr, not stdout."""
    with tempfile.TemporaryDirectory() as tmpdir:
        path = Path(tmpdir) / "no-entries.md"
        path.write_text("plain text without headers\n", encoding="utf-8")
        captured_out = io.StringIO()
        captured_err = io.StringIO()
        with unittest.mock.patch("sys.stdout", captured_out), unittest.mock.patch("sys.stderr", captured_err):
            rv = learn.import_from_file(str(path))
    test("from_file_no_entries_stderr: returns 0", rv == 0)
    test("from_file_no_entries_stderr: stdout stays empty", captured_out.getvalue() == "")
    test("from_file_no_entries_stderr: stderr has message", "No entries found" in captured_err.getvalue())


# ===========================================================================
# Test 23 — --from-file with no filepath argument → exit non-zero
# ===========================================================================

def test_from_file_missing_arg_exits_nonzero():
    """--from-file with no following argument must exit non-zero."""
    exit_code: int | None = None
    with ExitStack() as stack:
        stack.enter_context(unittest.mock.patch("sys.stdout", io.StringIO()))
        stack.enter_context(unittest.mock.patch("sys.stderr", io.StringIO()))
        stack.enter_context(unittest.mock.patch.object(sys, "argv", ["learn.py", "--from-file"]))
        try:
            learn.main()
        except SystemExit as exc:
            exit_code = exc.code
    test("from_file_missing_arg: exit code is non-zero", exit_code is not None and exit_code != 0)


# ===========================================================================
# Test 24 — header-only import (return -1) → exit 0, no auto-update (fix-1)
# ===========================================================================

def test_from_file_header_only_exits_zero():
    """import_from_file returning -1 (headers parsed, no body) must exit 0."""
    calls, code = _run_learn_from_file(
        ["--from-file", "notes.md"],
        fake_import_return=-1,
    )
    test("header_only_exit: subprocess.run NOT called", len(calls) == 0)
    test("header_only_exit: exit code is None (success)", code is None)


def test_from_file_header_only_skips_autoupdate():
    """Header-only import must not trigger auto-update even when --update-cerebrum is set."""
    calls, code = _run_learn_from_file(
        ["--from-file", "notes.md", "--update-cerebrum"],
        fake_import_return=-1,
    )
    test("header_only_autoupdate: subprocess.run NOT called", len(calls) == 0)
    test("header_only_autoupdate: exit code is None (success)", code is None)


# ===========================================================================
# Test 26 — malformed --cerebrum-output (next token is a flag) (fix-4)
# ===========================================================================

def test_cerebrum_output_flag_no_swallow():
    """--cerebrum-output followed immediately by another flag must not swallow that flag."""
    calls, _ = _run_learn([
        "--pattern", "Title", "Some content",
        "--update-cerebrum", "--cerebrum-output", "--cerebrum-sections", "mistakes",
        "--skip-gate", "--skip-scan",
    ])
    test("no_swallow_output: subprocess called", len(calls) == 1)
    output_idx = calls[0].index("--output") if "--output" in calls[0] else -1
    actual_output = calls[0][output_idx + 1] if output_idx != -1 else ""
    # --cerebrum-output had a flag as next token → must fall back to default CEREBRUM.md
    test("no_swallow_output: output is CEREBRUM.md (not the swallowed flag)", actual_output == "CEREBRUM.md")
    # --cerebrum-sections "mistakes" must still be parsed correctly
    test("no_swallow_output: --sections present in cmd", "--sections" in calls[0])
    sec_idx = calls[0].index("--sections") if "--sections" in calls[0] else -1
    actual_sections = calls[0][sec_idx + 1] if sec_idx != -1 else ""
    test("no_swallow_output: sections value is 'mistakes'", actual_sections == "mistakes")


def test_cerebrum_output_flag_no_content_pollution():
    """Malformed --cerebrum-output must not leak later values into stored content."""
    captured: dict[str, str] = {}

    def fake_with_retry(func, *args, **kwargs):
        captured["content"] = args[2]
        return 42

    with ExitStack() as stack:
        stack.enter_context(unittest.mock.patch.object(learn, "with_retry", fake_with_retry))
        stack.enter_context(
            unittest.mock.patch.object(
                learn.subprocess,
                "run",
                lambda cmd, **kwargs: _FakeSubprocessResult(0),
            )
        )
        stack.enter_context(unittest.mock.patch("sys.stdout", io.StringIO()))
        stack.enter_context(unittest.mock.patch("sys.stderr", io.StringIO()))
        stack.enter_context(
            unittest.mock.patch.object(
                sys,
                "argv",
                [
                    "learn.py",
                    "--pattern",
                    "Title",
                    "Some content",
                    "--update-cerebrum",
                    "--cerebrum-output",
                    "--cerebrum-sections",
                    "mistakes",
                    "--skip-gate",
                    "--skip-scan",
                ],
            )
        )
        learn.main()

    test("no_swallow_output: content preserved", captured.get("content") == "Some content")


def test_cerebrum_sections_flag_no_value():
    """--cerebrum-sections followed by another flag must not forward a flag token as value."""
    calls, _ = _run_learn([
        "--pattern", "Title", "Some content",
        "--update-cerebrum", "--cerebrum-sections", "--skip-gate", "--skip-scan",
    ])
    test("no_swallow_sections: subprocess called", len(calls) == 1)
    # --cerebrum-sections next token is --skip-gate (a flag) → sections must be None → not forwarded
    test("no_swallow_sections: --sections NOT in cmd", "--sections" not in calls[0])


# ===========================================================================
# Test 28 — malformed --cerebrum-output in --from-file path (fix-4)
# ===========================================================================

def test_from_file_cerebrum_output_no_swallow():
    """In the --from-file path, --cerebrum-output followed by a flag must fall back to default."""
    calls, code = _run_learn_from_file([
        "--from-file", "notes.md", "--update-cerebrum",
        "--cerebrum-output", "--cerebrum-sections", "mistakes",
    ], fake_import_return=1)
    test("from_file_no_swallow: subprocess called", len(calls) == 1)
    output_idx = calls[0].index("--output") if "--output" in calls[0] else -1
    actual_output = calls[0][output_idx + 1] if output_idx != -1 else ""
    test("from_file_no_swallow: output is CEREBRUM.md", actual_output == "CEREBRUM.md")
    test("from_file_no_swallow: --sections in cmd", "--sections" in calls[0])
    sec_idx = calls[0].index("--sections") if "--sections" in calls[0] else -1
    actual_sections = calls[0][sec_idx + 1] if sec_idx != -1 else ""
    test("from_file_no_swallow: sections value is 'mistakes'", actual_sections == "mistakes")
    test("from_file_no_swallow: exit code None (success)", code is None)


# ===========================================================================
# Entry point
# ===========================================================================

def _run_all():
    print("\n=== test_learn_cerebrum_autoupdate ===\n")
    test_no_autoupdate_without_flag()
    test_autoupdate_triggers_subprocess()
    test_cerebrum_output_flag_forwarded()
    test_cerebrum_sections_flag_forwarded()
    test_autoupdate_failure_surfaces_exit_code()
    test_rejected_entry_skips_autoupdate()
    test_default_output_path_is_cerebrum_md()
    test_no_sections_flag_when_not_specified()
    test_autoupdate_with_json_mode()
    test_auto_update_cerebrum_unit_no_sections()
    test_auto_update_cerebrum_unit_with_sections()
    test_auto_update_cerebrum_exception_returns_1()
    # Issue-1 regression: --from-file + --update-cerebrum contract
    test_from_file_with_autoupdate()
    test_from_file_without_autoupdate()
    # Issue-2 regression: JSON mode stdout contract
    test_json_mode_subprocess_stdout_is_devnull()
    # Blocker-1 regression: --from-file + --json + --update-cerebrum → stdout=DEVNULL
    test_from_file_json_mode_subprocess_stdout_is_devnull()
    # Issue-3 regression: timeout handling
    test_auto_update_cerebrum_timeout_returns_1()
    test_autoupdate_timeout_surfaces_exit_code()
    # Blocker-2 regressions: failed import must not trigger auto-update
    test_from_file_not_found_skips_autoupdate()
    test_from_file_no_entries_skips_autoupdate()
    # Exit-code regressions: failed --from-file must exit non-zero
    test_from_file_not_found_exits_nonzero()
    test_from_file_no_entries_exits_nonzero()
    test_import_from_file_not_found_uses_stderr()
    test_import_from_file_no_entries_uses_stderr()
    # Bug: --from-file with no filepath must exit non-zero
    test_from_file_missing_arg_exits_nonzero()
    # Fix-1 regressions: header-only import must exit 0
    test_from_file_header_only_exits_zero()
    test_from_file_header_only_skips_autoupdate()
    # Fix-4 regressions: malformed flag ordering must not swallow next flag as value
    test_cerebrum_output_flag_no_swallow()
    test_cerebrum_output_flag_no_content_pollution()
    test_cerebrum_sections_flag_no_value()
    test_from_file_cerebrum_output_no_swallow()

    print(f"\n  {_PASS} passed, {_FAIL} failed\n")
    return _FAIL


if __name__ == "__main__":
    sys.exit(_run_all())
