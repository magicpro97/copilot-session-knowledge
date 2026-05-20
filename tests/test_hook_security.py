#!/usr/bin/env python3
"""Security and edge-case regression tests for Copilot hook execution.

Run:
    python tests/test_hook_security.py
"""

import importlib.machinery
import importlib.util
import io
import json
import os
import shutil
import sqlite3
import subprocess
import sys
import tempfile
import time
import types
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

if os.name == "nt":
    for _s in (sys.stdout, sys.stderr):
        if hasattr(_s, "reconfigure"):
            _s.reconfigure(encoding="utf-8", errors="replace")

PASS = 0
FAIL = 0
REPO = Path(__file__).parent.parent
RUNNER = REPO / "hooks" / "hook_runner.py"
QUERY_SESSION = REPO / "query-session.py"


def test(name: str, condition: bool, detail: str = "") -> None:
    global PASS, FAIL
    if condition:
        PASS += 1
        print(f"  PASS  {name}")
    else:
        FAIL += 1
        print(f"  FAIL  {name}" + (f" - {detail}" if detail else ""))


def _isolated_home(prefix: str) -> Path:
    home = Path(tempfile.mkdtemp(prefix=prefix))
    (home / ".copilot" / "markers").mkdir(parents=True, exist_ok=True)
    return home


def _env_for_home(home: Path, extra: dict[str, str] | None = None) -> dict[str, str]:
    env = {
        **os.environ,
        "HOME": str(home),
        "USERPROFILE": str(home),
        "PYTHONIOENCODING": "utf-8",
    }
    env.pop("SK_HOOK_ACTIVE", None)
    if extra:
        env.update(extra)
    return env


def _run_hook(
    event: str,
    payload,
    *,
    home: Path,
    extra_env: dict[str, str] | None = None,
    timeout: int = 10,
) -> subprocess.CompletedProcess:
    input_text = payload if isinstance(payload, str) else json.dumps(payload)
    return subprocess.run(
        [sys.executable, str(RUNNER), event],
        input=input_text,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        cwd=str(REPO),
        env=_env_for_home(home, extra_env),
        timeout=timeout,
    )


def _read_audit(home: Path) -> list[dict]:
    audit = home / ".copilot" / "markers" / "audit.jsonl"
    entries = []
    if not audit.is_file():
        return entries
    for line in audit.read_text(encoding="utf-8", errors="replace").splitlines():
        try:
            entries.append(json.loads(line))
        except json.JSONDecodeError:
            pass
    return entries


print("\n-- Hook payload fail-open behavior -----------------------------------")


def test_malformed_json_logs_parse_error() -> None:
    home = _isolated_home("hook-security-malformed-")
    try:
        result = _run_hook("preToolUse", "{not valid json", home=home)
        decisions = [entry.get("decision") for entry in _read_audit(home)]
        test(
            "malformed JSON exits 0",
            result.returncode == 0,
            f"returncode={result.returncode}, stderr={result.stderr[:200]}",
        )
        test(
            "malformed JSON records parse-error audit entry",
            "parse-error" in decisions,
            f"decisions={decisions}",
        )
    finally:
        shutil.rmtree(home, ignore_errors=True)


def test_oversized_payload_completes_within_timeout() -> None:
    home = _isolated_home("hook-security-oversized-")
    try:
        payload = {
            "toolName": "view",
            "toolArgs": {"path": str(REPO / "README.md"), "blob": "A" * 1_000_000},
            "sessionId": "oversized-payload",
        }
        start = time.monotonic()
        try:
            result = _run_hook("preToolUse", payload, home=home, timeout=8)
        except subprocess.TimeoutExpired as exc:
            test("oversized hook payload exits 0", False, f"timed out after {exc.timeout}s")
            test("oversized hook payload stays within bounded timeout", False, f"timed out after {exc.timeout}s")
            return
        elapsed = time.monotonic() - start
        test(
            "oversized hook payload exits 0",
            result.returncode == 0,
            f"returncode={result.returncode}, stderr={result.stderr[:200]}",
        )
        test(
            "oversized hook payload stays within bounded timeout",
            elapsed < 8,
            f"elapsed={elapsed:.2f}s",
        )
    finally:
        shutil.rmtree(home, ignore_errors=True)


def test_missing_optional_briefing_script_fail_open() -> None:
    home = _isolated_home("hook-security-missing-script-")
    tools_dir = home / "empty-tools"
    tools_dir.mkdir()
    try:
        result = _run_hook(
            "sessionStart",
            {},
            home=home,
            extra_env={"SK_TOOLS_DIR": str(tools_dir)},
        )
        combined = result.stdout + result.stderr
        test(
            "missing optional briefing.py does not break sessionStart",
            result.returncode == 0 and "Traceback" not in combined,
            f"returncode={result.returncode}, output={combined[:300]}",
        )
        test(
            "missing optional briefing.py does not emit briefing output",
            "Session briefing" not in combined,
            f"output={combined[:300]}",
        )
    finally:
        shutil.rmtree(home, ignore_errors=True)


def test_rule_crash_isolation_records_error() -> None:
    loader = importlib.machinery.SourceFileLoader("hook_runner_for_security", str(RUNNER))
    spec = importlib.util.spec_from_loader(loader.name, loader)
    hook_runner = importlib.util.module_from_spec(spec)
    loader.exec_module(hook_runner)

    class ExplodingRule:
        name = "exploding-rule"
        tools = []

        def evaluate(self, event, data):
            raise RuntimeError("intentional regression-test crash")

    fake_rules = types.ModuleType("rules")
    fake_rules.get_rules_for_event = lambda event: [ExplodingRule()]

    home = _isolated_home("hook-security-crash-")
    markers = home / ".copilot" / "markers"
    old_rules = sys.modules.get("rules")
    old_argv = sys.argv
    old_stdin = sys.stdin
    old_stdout = sys.stdout
    old_markers = hook_runner.MARKERS_DIR
    old_sk_hook_active = os.environ.get("SK_HOOK_ACTIVE")
    condition = False
    detail = ""
    try:
        sys.modules["rules"] = fake_rules
        sys.argv = ["hook_runner.py", "preToolUse"]
        sys.stdin = io.StringIO(json.dumps({"toolName": "view"}))
        captured = io.StringIO()
        sys.stdout = captured
        hook_runner.MARKERS_DIR = markers
        os.environ.pop("SK_HOOK_ACTIVE", None)
        hook_runner.main()
        decisions = [entry.get("decision") for entry in _read_audit(home)]
        condition = "error" in decisions
        detail = f"decisions={decisions}, stdout={captured.getvalue()[:200]}"
    except Exception as exc:
        detail = repr(exc)
    finally:
        if old_rules is None:
            sys.modules.pop("rules", None)
        else:
            sys.modules["rules"] = old_rules
        sys.argv = old_argv
        sys.stdin = old_stdin
        sys.stdout = old_stdout
        hook_runner.MARKERS_DIR = old_markers
        if old_sk_hook_active is None:
            os.environ.pop("SK_HOOK_ACTIVE", None)
        else:
            os.environ["SK_HOOK_ACTIVE"] = old_sk_hook_active
        shutil.rmtree(home, ignore_errors=True)
    test("rule exception is isolated by hook_runner", condition, detail)


test_malformed_json_logs_parse_error()
test_oversized_payload_completes_within_timeout()
test_missing_optional_briefing_script_fail_open()
test_rule_crash_isolation_records_error()


print("\n-- Hook state isolation and injection resistance ----------------------")


def test_concurrent_skill_usage_hooks_do_not_surface_sqlite_locked() -> None:
    home = _isolated_home("hook-security-concurrent-")
    try:
        payload = {
            "toolName": "skill",
            "toolArgs": {"skill": "hook-security-regression"},
            "toolResult": {"exitCode": 0, "output": "loaded"},
            "sessionId": "concurrent-skill-usage",
        }

        def run_once(index: int) -> subprocess.CompletedProcess:
            return _run_hook(
                "postToolUse",
                {**payload, "nonce": index},
                home=home,
                timeout=15,
            )

        with ThreadPoolExecutor(max_workers=8) as pool:
            results = list(pool.map(run_once, range(8)))

        combined = "\n".join(result.stdout + result.stderr for result in results)
        all_zero = all(result.returncode == 0 for result in results)
        locked_surface = "SQLITE_LOCKED" in combined or "database is locked" in combined or "Traceback" in combined
        metrics_db = home / ".copilot" / "session-state" / "skill-metrics.db"
        row_count = 0
        if metrics_db.is_file():
            db = sqlite3.connect(str(metrics_db))
            try:
                row_count = db.execute("SELECT COUNT(*) FROM skill_usage_events").fetchone()[0]
            finally:
                db.close()
        test(
            "concurrent hook invocations exit 0",
            all_zero,
            f"returncodes={[r.returncode for r in results]}",
        )
        test(
            "concurrent hook invocations do not surface SQLITE_LOCKED",
            not locked_surface,
            combined[:500],
        )
        test(
            "concurrent skill usage writes at least one telemetry row",
            row_count > 0,
            f"row_count={row_count}",
        )
    finally:
        shutil.rmtree(home, ignore_errors=True)


def test_path_traversal_session_id_stays_inside_markers_dir() -> None:
    base = Path(tempfile.mkdtemp(prefix="hook-security-path-base-"))
    home = base / "home"
    (home / ".copilot" / "markers").mkdir(parents=True, exist_ok=True)
    sample = home / "sample.txt"
    sample.write_text("sample", encoding="utf-8")
    try:
        payload = {
            "toolName": "view",
            "toolArgs": {"path": str(sample)},
            "sessionId": "..\\..//escape/../../owned",
        }
        result = _run_hook("postToolUse", payload, home=home, timeout=10)
        markers = (home / ".copilot" / "markers").resolve()
        marker_paths = list(markers.rglob("*"))
        unsafe_names = [p.name for p in marker_paths if ".." in p.name or "/" in p.name or "\\" in p.name]
        unexpected_siblings = [p for p in base.iterdir() if p != home]
        expected_dirs = {(home / ".copilot").resolve(), markers}
        expected_files = {sample.resolve()}
        unexpected_home_paths = [
            p
            for p in home.rglob("*")
            if p.resolve() not in expected_dirs
            and p.resolve() not in expected_files
            and not p.resolve().is_relative_to(markers)
        ]
        test(
            "path traversal-like sessionId hook exits 0",
            result.returncode == 0,
            f"returncode={result.returncode}, stderr={result.stderr[:200]}",
        )
        test(
            "sessionId-derived marker names are sanitized",
            not unsafe_names,
            f"unsafe_names={unsafe_names}",
        )
        test(
            "sessionId payload does not create files outside markers dir",
            not unexpected_siblings and not unexpected_home_paths,
            f"unexpected_siblings={unexpected_siblings}, unexpected_home_paths={unexpected_home_paths}",
        )
    finally:
        shutil.rmtree(base, ignore_errors=True)


def test_fts_operator_input_is_sanitized_before_match_use() -> None:
    loader = importlib.machinery.SourceFileLoader("query_session_for_hook_security", str(QUERY_SESSION))
    spec = importlib.util.spec_from_loader(loader.name, loader)
    module = importlib.util.module_from_spec(spec)
    loader.exec_module(module)

    sanitized = module._sanitize_fts_query('alpha OR beta AND gamma NOT delta NEAR "quoted"*')
    stripped_terms = sanitized.replace('"alpha"*', "").replace('"beta"*', "").replace('"gamma"*', "")
    stripped_terms = stripped_terms.replace('"delta"*', "").replace('"quoted"*', "")
    source = QUERY_SESSION.read_text(encoding="utf-8")
    test(
        "FTS operators are stripped from sanitized query",
        all(op not in stripped_terms for op in ("OR", "AND", "NOT", "NEAR", "*")),
        f"sanitized={sanitized}",
    )
    test(
        "FTS MATCH clauses use parameterized query values",
        "MATCH ?" in source and "WHERE knowledge_fts MATCH ?" in source,
        "query-session.py should keep sanitized FTS input bound through MATCH ? placeholders",
    )


test_concurrent_skill_usage_hooks_do_not_surface_sqlite_locked()
test_path_traversal_session_id_stays_inside_markers_dir()
test_fts_operator_input_is_sanitized_before_match_use()

print(f"\n{'=' * 50}")
print(f"Results: {PASS} passed, {FAIL} failed out of {PASS + FAIL}")
if FAIL:
    print(f"{FAIL} hook security regression test(s) need attention")
    sys.exit(1)
print("All hook security regression tests passed!")
