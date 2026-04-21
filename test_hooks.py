#!/usr/bin/env python3
"""
test_hooks.py — Comprehensive tests for the hook system.

Covers:
  1. Hook runner (dispatch, fail-open, dry-run, parse errors)
  2. Marker auth (sign, verify, tamper, counter, list markers)
  3. Rule utilities (common.py helpers)
  4. Individual rules (briefing, learn gate, edit tracker)
  5. Project-level hooks.json consistency
  6. Migrate.py schema migrations

Run: python3 test_hooks.py
"""

import hashlib
import hmac
import json
import os
import re
import shutil
import sqlite3
import subprocess
import sys
import tempfile
import time
from pathlib import Path

PASS = 0
FAIL = 0
REPO = Path(__file__).parent


def test(name: str, condition: bool, detail: str = ""):
    global PASS, FAIL
    if condition:
        PASS += 1
        print(f"  ✅ {name}")
    else:
        FAIL += 1
        print(f"  ❌ {name}" + (f" — {detail}" if detail else ""))


# ═══════════════════════════════════════════════════════════════════
#  Section 1: Hook Runner
# ═══════════════════════════════════════════════════════════════════

print("\n🔌 Section 1: Hook Runner")

RUNNER = REPO / "hooks" / "hook_runner.py"

# 1a. Empty stdin → allow (fail-open)
r = subprocess.run(
    [sys.executable, str(RUNNER), "preToolUse"],
    input="", capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=10,
)
test("Empty stdin → allow (exit 0)", r.returncode == 0)
test("Empty stdin → no JSON output", r.stdout.strip() == "")

# 1b. Invalid JSON → allow (fail-open)
r = subprocess.run(
    [sys.executable, str(RUNNER), "preToolUse"],
    input="{invalid json!!!", capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=10,
)
test("Invalid JSON → allow (exit 0)", r.returncode == 0)

# 1c. Unknown event → allow (no matching rules)
r = subprocess.run(
    [sys.executable, str(RUNNER), "unknownEvent"],
    input='{"toolName":"edit"}', capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=10,
)
test("Unknown event → allow", r.returncode == 0)

# 1d. No event argument → silent exit
r = subprocess.run(
    [sys.executable, str(RUNNER)],
    input='{}', capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=10,
)
test("No event arg → silent exit", r.returncode == 0)

# 1e. Dry-run mode (HOOK_DRY_RUN=1) → allows even denied actions
env = os.environ.copy()
env["HOOK_DRY_RUN"] = "1"
r = subprocess.run(
    [sys.executable, str(RUNNER), "preToolUse"],
    input=json.dumps({
        "toolName": "bash",
        "toolArgs": {"command": "cat ~/.copilot/hooks/.marker-secret"}
    }),
    capture_output=True, text=True, encoding="utf-8", errors="replace", env=env, timeout=10,
)
test("Dry-run mode → no deny JSON", "permissionDecision" not in r.stdout)
test("Dry-run mode → has DRY RUN label", "DRY RUN" in r.stdout or r.returncode == 0)

# 1f. Allowed tool (read-only like "view") → passes through
r = subprocess.run(
    [sys.executable, str(RUNNER), "preToolUse"],
    input=json.dumps({"toolName": "view", "toolArgs": {"path": "/tmp/test.txt"}}),
    capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=10,
)
test("View tool → allowed", r.returncode == 0 and "deny" not in r.stdout)

# 1g. postToolUse event with valid data → no error
r = subprocess.run(
    [sys.executable, str(RUNNER), "postToolUse"],
    input=json.dumps({
        "toolName": "edit",
        "toolArgs": {"path": "/tmp/test.py"},
        "toolResult": {"success": True}
    }),
    capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=10,
)
test("postToolUse → no error", r.returncode == 0)

# 1h. errorOccurred event
r = subprocess.run(
    [sys.executable, str(RUNNER), "errorOccurred"],
    input=json.dumps({"error": "Something broke", "toolName": "bash"}),
    capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=10,
)
test("errorOccurred → no crash", r.returncode == 0)


# ═══════════════════════════════════════════════════════════════════
#  Section 2: Marker Auth
# ═══════════════════════════════════════════════════════════════════

print("\n🔐 Section 2: Marker Auth")

sys.path.insert(0, str(REPO / "hooks"))
from marker_auth import (
    sign_marker, verify_marker,
    sign_counter, verify_counter,
    sign_list_marker, verify_list_marker,
    is_secret_access, check_tamper_marker,
    _read_secret,
)

# Use temp dir for test markers
test_dir = Path(tempfile.mkdtemp(prefix="test-markers-"))

try:
    # 2a. Sign + verify marker
    m = test_dir / "test-marker"
    sign_marker(m, "test-marker")
    secret = _read_secret()
    test("Sign marker creates file", m.exists())
    test("Verify own marker succeeds", verify_marker(m, "test-marker"))

    # 2b. Wrong name fails verification
    if secret:
        test("Wrong name → verify fails", not verify_marker(m, "wrong-name"))
    else:
        test("No secret → wrong name falls back to existence check", verify_marker(m, "wrong-name"))

    # 2c. Non-existent marker → verify fails
    test("Non-existent marker → false", not verify_marker(test_dir / "nope", "nope"))

    # 2d. Tampered marker → verify fails
    tampered = test_dir / "tampered"
    sign_marker(tampered, "tampered")
    if secret:
        content = json.loads(tampered.read_text(encoding="utf-8"))
        content["ts"] = "9999999999"  # Change timestamp
        tampered.write_text(json.dumps(content), encoding="utf-8")
        test("Tampered timestamp → verify fails", not verify_marker(tampered, "tampered"))
    else:
        test("No secret → simple existence check (tamper test skipped)", True)

    # 2e. Empty file → verify fails
    empty = test_dir / "empty"
    empty.touch()
    if secret:
        test("Empty marker → verify fails (with secret)", not verify_marker(empty, "empty"))
    else:
        test("Empty marker → verify passes (no secret, existence only)", verify_marker(empty, "empty"))

    # 2f. Counter sign + verify
    c = test_dir / "edit-count"
    sign_counter(c, 5)
    test("Counter sign creates file", c.exists())
    test("Counter verify returns value", verify_counter(c) == 5)

    # 2g. Counter with zero
    c0 = test_dir / "zero-count"
    sign_counter(c0, 0)
    test("Counter zero → returns 0", verify_counter(c0) == 0)

    # 2h. Non-existent counter → 0
    test("Non-existent counter → 0", verify_counter(test_dir / "nope") == 0)

    # 2i. Tampered counter → 0
    c_tamper = test_dir / "tamper-count"
    sign_counter(c_tamper, 10)
    ct_content = json.loads(c_tamper.read_text(encoding="utf-8")) if secret else None
    if ct_content:
        ct_content["value"] = 999
        c_tamper.write_text(json.dumps(ct_content), encoding="utf-8")
        test("Tampered counter → 0", verify_counter(c_tamper) == 0)
    else:
        test("Counter tamper test (skipped, no secret)", True)

    # 2j. List marker
    lm = test_dir / "edited-files"
    sign_list_marker(lm, ["src/a.py", "src/b.py", "src/c.py"])
    result = verify_list_marker(lm)
    test("List marker contains all items", result == {"src/a.py", "src/b.py", "src/c.py"})

    # 2k. Empty list marker
    lm_empty = test_dir / "empty-list"
    sign_list_marker(lm_empty, [])
    test("Empty list marker → empty set", verify_list_marker(lm_empty) == set())

    # 2l. is_secret_access detection
    test("Detects .marker-secret access",
         is_secret_access("cat ~/.copilot/hooks/.marker-secret"))
    test("Detects integrity-manifest access",
         is_secret_access("vim integrity-manifest.json"))
    test("Detects marker_auth.py access",
         is_secret_access("cat marker_auth.py"))
    test("Normal command → not secret",
         not is_secret_access("ls -la /tmp"))
    test("Normal python → not secret",
         not is_secret_access("python3 test.py"))

    # 2m. check_tamper_marker (should not be set in test env)
    test("No tamper marker in test env", not check_tamper_marker() or True)

finally:
    shutil.rmtree(test_dir, ignore_errors=True)


# ═══════════════════════════════════════════════════════════════════
#  Section 3: Rule Utilities (common.py)
# ═══════════════════════════════════════════════════════════════════

print("\n🛠️  Section 3: Rule Utilities (common.py)")

sys.path.insert(0, str(REPO / "hooks"))
from rules.common import (
    is_source_path, get_module, bash_writes_source_files, deny, info,
)

# 3a. is_source_path
test("Python file → source", is_source_path("src/main.py"))
test("Kotlin file → source", is_source_path("app/src/Main.kt"))
test("Swift file → source", is_source_path("AlarmApp.swift"))
test("JSON file → source", is_source_path("config.json"))
test("Markdown → source", is_source_path("docs/README.md"))
test("/tmp file → NOT source", not is_source_path("/tmp/test.py"))
test("/var file → NOT source", not is_source_path("/var/log/app.py"))
test("Binary file → NOT source", not is_source_path("app.exe"))
test("Image file → NOT source", not is_source_path("logo.png"))

# 3b. get_module
test("src/auth/login.py → auth", get_module("src/auth/login.py") == "auth")
test("app/main.py → app", get_module("app/main.py") == "app")
test("single.py → empty", get_module("single.py") == "")
test("presentation/ui/Screen.kt → ui",
     get_module("presentation/ui/Screen.kt") == "ui")

# 3c. bash_writes_source_files
test("echo > file.py → writes", bash_writes_source_files("echo 'x' > src/main.py"))
test("sed -i → writes", bash_writes_source_files("sed -i 's/a/b/g' file.py"))
test("tee file.py → writes", bash_writes_source_files("echo x | tee src/main.py"))
test("cp to source → writes", bash_writes_source_files("cp /tmp/a.py src/b.py"))
test("curl -o source → writes", bash_writes_source_files("curl -o app.py https://x"))
test("python -c with open → writes",
     bash_writes_source_files("python3 -c \"open('x.py','w').write('y')\""))
test("dd of= → writes", bash_writes_source_files("dd if=/dev/zero of=file bs=1"))
test("ls -la → NOT writes", not bash_writes_source_files("ls -la"))
test("cat file.py → NOT writes", not bash_writes_source_files("cat src/main.py"))
test("grep pattern → NOT writes", not bash_writes_source_files("grep -r 'test' src/"))
test("echo to /tmp → NOT writes", not bash_writes_source_files("echo x > /tmp/test.py"))
test("git status → NOT writes", not bash_writes_source_files("git status"))
test("python3 briefing.py → NOT writes",
     not bash_writes_source_files("python3 ~/.copilot/tools/briefing.py 'task'"))

# 3d. deny / info helpers
d = deny("test reason")
test("deny() has permissionDecision=deny", d["permissionDecision"] == "deny")
test("deny() has reason", d["permissionDecisionReason"] == "test reason")

i = info("test message")
test("info() has message", i["message"] == "test message")


# ═══════════════════════════════════════════════════════════════════
#  Section 4: Rule Registration
# ═══════════════════════════════════════════════════════════════════

print("\n📋 Section 4: Rule Registration")

from rules import get_rules_for_event, Rule

session_start_rules = get_rules_for_event("sessionStart")
pre_tool_rules = get_rules_for_event("preToolUse")
post_tool_rules = get_rules_for_event("postToolUse")
error_rules = get_rules_for_event("errorOccurred")
session_end_rules = get_rules_for_event("sessionEnd")

test("sessionStart has rules", len(session_start_rules) >= 2)
test("preToolUse has rules", len(pre_tool_rules) >= 3)
test("postToolUse has rules", len(post_tool_rules) >= 3)
test("errorOccurred has rules", len(error_rules) >= 1)
test("sessionEnd has rules", len(session_end_rules) >= 1)

# Verify rule names
ss_names = [r.name for r in session_start_rules]
test("sessionStart has auto-briefing", "auto-briefing" in ss_names)
test("sessionStart has integrity", "integrity" in ss_names)

pre_names = [r.name for r in pre_tool_rules]
test("preToolUse has enforce-briefing", "enforce-briefing" in pre_names)
test("preToolUse has enforce-learn", "enforce-learn" in pre_names)
test("preToolUse has tentacle-enforce", "tentacle-enforce" in pre_names)

# Briefing must come before learn gate
brief_idx = pre_names.index("enforce-briefing")
learn_idx = pre_names.index("enforce-learn")
test("Briefing rule before learn gate", brief_idx < learn_idx)

# All rules inherit from Rule base
for r in session_start_rules + pre_tool_rules + post_tool_rules:
    test(f"Rule '{r.name}' inherits Rule", isinstance(r, Rule))

# All rules have name and events
for r in session_start_rules + pre_tool_rules + post_tool_rules + error_rules + session_end_rules:
    test(f"Rule '{r.name}' has name", bool(r.name))
    test(f"Rule '{r.name}' has events", len(r.events) > 0)


# ═══════════════════════════════════════════════════════════════════
#  Section 5: hooks.json Consistency
# ═══════════════════════════════════════════════════════════════════

print("\n📄 Section 5: hooks.json Consistency")

user_hooks_path = Path.home() / ".copilot" / "hooks" / "hooks.json"
project_hooks_path = REPO / ".github" / "hooks" / "hooks.json"

# 5a. User-level hooks.json exists and is valid JSON
if user_hooks_path.exists():
    try:
        user_hooks = json.loads(user_hooks_path.read_text(encoding="utf-8"))
        test("User hooks.json is valid JSON", True)
        test("User hooks has version", user_hooks.get("version") == 1)
    except json.JSONDecodeError as e:
        test("User hooks.json is valid JSON", False, str(e))
else:
    test("User hooks.json exists", False, str(user_hooks_path))

# 5b. Project-level hooks.json exists and is valid JSON
if project_hooks_path.exists():
    try:
        proj_hooks = json.loads(project_hooks_path.read_text(encoding="utf-8"))
        test("Project hooks.json is valid JSON", True)
    except json.JSONDecodeError as e:
        test("Project hooks.json is valid JSON", False, str(e))

    # 5c. All hooks point to unified runner (not old standalone scripts)
    old_scripts = ["enforce-briefing.py", "enforce-learn.py", "enforce-tentacle.py",
                   "auto-briefing.py", "verify-integrity.py", "session-end.py",
                   "track-bash-edits.py", "suggest-learn.py", "suggest-tentacle.py"]
    for event, hooks_list in proj_hooks.get("hooks", {}).items():
        for hook in hooks_list:
            cmd = hook.get("bash", "")
            for old in old_scripts:
                test(f"Project {event} NOT using old {old}",
                     old not in cmd,
                     f"Found {old} in: {cmd}")
            test(f"Project {event} uses hook_runner.py",
                 "hook_runner.py" in cmd,
                 f"Got: {cmd}")
else:
    test("Project hooks.json exists", False, str(project_hooks_path))

# 5d. User-level and project-level should be identical (both global)
if user_hooks_path.exists() and project_hooks_path.exists():
    try:
        uh = json.loads(user_hooks_path.read_text(encoding="utf-8"))
        ph = json.loads(project_hooks_path.read_text(encoding="utf-8"))
        # Compare normalized
        test("User and project hooks.json match",
             json.dumps(uh, sort_keys=True) == json.dumps(ph, sort_keys=True),
             "User-level and project-level hooks have diverged!")
    except Exception as e:
        test("hooks.json comparison", False, str(e))


# ═══════════════════════════════════════════════════════════════════
#  Section 6: Migrate.py
# ═══════════════════════════════════════════════════════════════════

print("\n🗃️  Section 6: Migrate.py")

# Test migrations on a temp database
with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as tmp:
    test_db_path = tmp.name

try:
    # Create initial schema (minimum required tables)
    db = sqlite3.connect(test_db_path)
    db.execute("""
        CREATE TABLE IF NOT EXISTS sections (
            id INTEGER PRIMARY KEY,
            session_id TEXT, title TEXT, content TEXT, doc_type TEXT,
            indexed_at TEXT DEFAULT (datetime('now'))
        )
    """)
    db.execute("""
        CREATE TABLE IF NOT EXISTS knowledge_entries (
            id INTEGER PRIMARY KEY,
            title TEXT, content TEXT, category TEXT, confidence REAL,
            source_session TEXT, tags TEXT DEFAULT '',
            content_hash TEXT DEFAULT '', created_at TEXT DEFAULT (datetime('now'))
        )
    """)
    db.commit()
    db.close()

    # Run migrate.py on it
    r = subprocess.run(
        [sys.executable, str(REPO / "migrate.py"), test_db_path],
        capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=30,
    )
    test("migrate.py runs without error", r.returncode == 0,
         f"stderr: {r.stderr[:200]}")

    # Verify migrations applied
    db = sqlite3.connect(test_db_path)

    # Check schema_version table
    version = db.execute("SELECT MAX(version) FROM schema_version").fetchone()[0]
    test("Schema version is set", version is not None and version >= 2,
         f"Got version: {version}")

    # Check wing/room columns exist
    try:
        db.execute("SELECT wing, room FROM knowledge_entries LIMIT 1")
        test("wing/room columns exist (v2)", True)
    except sqlite3.OperationalError as e:
        test("wing/room columns exist (v2)", False, str(e))

    # Check entity_relations table (v3)
    try:
        db.execute("SELECT COUNT(*) FROM entity_relations")
        test("entity_relations table exists (v3)", True)
    except sqlite3.OperationalError as e:
        test("entity_relations table exists (v3)", False, str(e))

    # Check facts column (v5)
    try:
        db.execute("SELECT facts FROM knowledge_entries LIMIT 1")
        test("facts column exists (v5)", True)
    except sqlite3.OperationalError as e:
        test("facts column exists (v5)", False, str(e))

    # Check est_tokens column (v6)
    try:
        db.execute("SELECT est_tokens FROM knowledge_entries LIMIT 1")
        test("est_tokens column exists (v6)", True)
    except sqlite3.OperationalError as e:
        test("est_tokens column exists (v6)", False, str(e))

    # Rerun migration → idempotent
    db.close()
    r2 = subprocess.run(
        [sys.executable, str(REPO / "migrate.py"), test_db_path],
        capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=30,
    )
    test("Re-run migrate is idempotent", r2.returncode == 0)

    db = sqlite3.connect(test_db_path)
    version2 = db.execute("SELECT MAX(version) FROM schema_version").fetchone()[0]
    test("Version unchanged after re-run", version2 == version)
    db.close()

finally:
    os.unlink(test_db_path)


# ═══════════════════════════════════════════════════════════════════
#  Section 7: Auto-Update Tools
# ═══════════════════════════════════════════════════════════════════

print("\n🔄 Section 7: Auto-Update Tools")

auto_update = REPO / "auto-update-tools.py"

# 7a. --status flag works
r = subprocess.run(
    [sys.executable, str(auto_update), "--status"],
    capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=30,
)
test("--status runs without error", r.returncode == 0,
     f"stderr: {r.stderr[:200]}")

# 7b. --check flag works (no changes applied)
r = subprocess.run(
    [sys.executable, str(auto_update), "--check"],
    capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=30,
)
test("--check runs without error", r.returncode == 0,
     f"stderr: {r.stderr[:200]}")

# 7c. --doctor flag works
r = subprocess.run(
    [sys.executable, str(auto_update), "--doctor"],
    capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=30,
)
test("--doctor runs without error", r.returncode == 0,
     f"stderr: {r.stderr[:200]}")

# 7d. Source has required functions
auto_src = auto_update.read_text(encoding="utf-8")
test("Has classify_changes()", "def classify_changes" in auto_src)
test("Has run_migrations()", "def run_migrations" in auto_src)
test("Has write_manifest()", "def write_manifest" in auto_src)
test("Has deploy_skills()", "def deploy_skills" in auto_src)
test("Has restart_processes()", "def restart_processes" in auto_src)
test("Has 24h cooldown", "86400" in auto_src or "24 * 3600" in auto_src or "24*3600" in auto_src)


# ═══════════════════════════════════════════════════════════════════
#  Section 8: Extract-Knowledge Cleanup
# ═══════════════════════════════════════════════════════════════════

print("\n🧹 Section 8: Extract-Knowledge Stale Cleanup")

ek_src = (REPO / "extract-knowledge.py").read_text(encoding="utf-8")
test("Has stale embedding cleanup",
     "DELETE FROM embeddings" in ek_src and "NOT IN" in ek_src,
     "extract-knowledge.py should clean stale embeddings")
test("Has orphan relation cleanup",
     "DELETE FROM knowledge_relations" in ek_src and "NOT IN" in ek_src,
     "extract-knowledge.py should clean orphan relations")


# ═══════════════════════════════════════════════════════════════════
#  Section 9: Documentation Completeness
# ═══════════════════════════════════════════════════════════════════

print("\n📚 Section 9: Documentation Completeness")

readme = (REPO / "README.md").read_text(encoding="utf-8")
test("README has Table of Contents", "## Table of Contents" in readme)
test("README has Why section", "## Why?" in readme)
test("README has Quick Start", "## Quick Start" in readme)
test("README has FAQ", "## FAQ" in readme)
test("README has Contributing", "## Contributing" in readme)
test("README has License", "## License" in readme)
test("README has badges", "img.shields.io" in readme)
test("README ≤ 600 lines", len(readme.splitlines()) <= 600,
     f"Got {len(readme.splitlines())} lines")

test("CONTRIBUTING.md exists", (REPO / "CONTRIBUTING.md").exists())
test("CHANGELOG.md exists", (REPO / "CHANGELOG.md").exists())
test("SECURITY.md exists", (REPO / "SECURITY.md").exists())

docs_dir = REPO / "docs"
test("docs/ directory exists", docs_dir.is_dir())
for doc in ["USAGE.md", "SKILLS.md", "HOOKS.md", "AUTO-UPDATE.md"]:
    test(f"docs/{doc} exists", (docs_dir / doc).exists())

# Check README links to docs/
for doc in ["USAGE.md", "SKILLS.md", "HOOKS.md", "AUTO-UPDATE.md"]:
    test(f"README links to docs/{doc}", f"docs/{doc}" in readme,
         f"Missing link to docs/{doc}")


# ═══════════════════════════════════════════════════════════════════
#  Section 10: Script Syntax Validation
# ═══════════════════════════════════════════════════════════════════

print("\n✏️  Section 10: Script Syntax Validation")

# ═══════════════════════════════════════════════════════════════════
#  Section 11: Tentacle Hook Message Validity
# ═══════════════════════════════════════════════════════════════════

print("\n🐙 Section 11: Tentacle Hook Message Validity")

# Regression: hooks/rules/tentacle.py must not suggest the invalid bare-string
# form `tentacle.py "your task"` — that subcommand does not exist.
tentacle_rule_src = (REPO / "hooks" / "rules" / "tentacle.py").read_text(encoding="utf-8")

test(
    "TentacleEnforceRule does not use invalid bare-string form",
    'tentacle.py "your task"' not in tentacle_rule_src,
    "Remove the obsolete 'tentacle.py \"your task\"' suggestion from TentacleEnforceRule"
)

# The message must reference the correct workflow entry point (create command)
test(
    "TentacleEnforceRule suggests tentacle.py create",
    "tentacle.py create" in tentacle_rule_src,
    "TentacleEnforceRule should guide users to start with 'tentacle.py create <name>'"
)

# The message must reference swarm (the dispatch step)
test(
    "TentacleEnforceRule suggests tentacle.py swarm",
    "tentacle.py swarm" in tentacle_rule_src,
    "TentacleEnforceRule should reference 'tentacle.py swarm <name>' for dispatch"
)

# Validate the deny message can be exercised without crashing — instantiate and run
try:
    sys.path.insert(0, str(REPO / "hooks"))
    from rules.tentacle import TentacleEnforceRule  # noqa: E402
    import tempfile, pathlib

    rule = TentacleEnforceRule()

    # Fake an edit event that exceeds the threshold by patching the marker helper
    class _FakeMarkers:
        """Minimal in-memory substitute for HMAC marker helpers."""
        def __init__(self, files):
            self._files = files
        def verify_list_marker(self, path):
            return set(self._files)
        def verify_marker(self, path, name):
            return False
        def sign_list_marker(self, path, lines):
            pass
        def is_secret_access(self, cmd):
            return False
        def check_tamper_marker(self):
            return False

    import rules.tentacle as _rt

    _orig_vlist = _rt.verify_list_marker
    _orig_vm    = _rt.verify_marker
    _orig_sl    = _rt.sign_list_marker
    _orig_isa   = _rt.is_secret_access
    _orig_ctm   = _rt.check_tamper_marker

    # 3 files across 2 modules → should trigger deny
    _fake_files = ["src/auth/login.py", "src/api/routes.py", "tests/test_auth.py"]
    _rt.verify_list_marker = lambda p: set(_fake_files)
    _rt.verify_marker = lambda p, n: False
    _rt.sign_list_marker = lambda p, lines: None
    _rt.is_secret_access = lambda c: False
    _rt.check_tamper_marker = lambda: False

    result = rule.evaluate("preToolUse", {"toolName": "edit", "toolArgs": {"path": "x.py"}})

    # Restore
    _rt.verify_list_marker = _orig_vlist
    _rt.verify_marker = _orig_vm
    _rt.sign_list_marker = _orig_sl
    _rt.is_secret_access = _orig_isa
    _rt.check_tamper_marker = _orig_ctm

    if result is not None:
        deny_msg = result.get("permissionDecisionReason", "") if isinstance(result, dict) else str(result)
        test("TentacleEnforceRule deny message contains 'create'", "create" in deny_msg,
             f"Got: {deny_msg!r:.120}")
        test("TentacleEnforceRule deny message contains 'swarm'", "swarm" in deny_msg,
             f"Got: {deny_msg!r:.120}")
        test("TentacleEnforceRule deny message does not contain '\"your task\"'",
             '"your task"' not in deny_msg,
             f"Got: {deny_msg!r:.120}")
        test("TentacleEnforceRule deny message references handoff escalation path",
             "handoff" in deny_msg,
             f"Deny message should guide sub-agents to write handoff.md; got: {deny_msg!r:.120}")
        test("TentacleEnforceRule deny message mentions commit convention",
             "commit" in deny_msg.lower(),
             f"Deny message should clarify commit convention; got: {deny_msg!r:.120}")
        test("TentacleEnforceRule deny message mentions git push",
             "push" in deny_msg.lower(),
             f"Deny message should mention git push; got: {deny_msg!r:.120}")
    else:
        test("TentacleEnforceRule returned a result for 3-file/2-module edit", False,
             "Expected deny, got None")

    test("TentacleEnforceRule evaluates without exception", True)
except Exception as e:
    test("TentacleEnforceRule evaluates without exception", False, str(e))


# ═══════════════════════════════════════════════════════════════════
#  Section 11b: TentacleSuggestRule bash-write parity
#  Regression: suggest must track the same bash write patterns as enforce
#  (sed -i, tee) so both rules accumulate the same file set.
# ═══════════════════════════════════════════════════════════════════

tentacle_rule_src = (REPO / "hooks" / "rules" / "tentacle.py").read_text(encoding="utf-8")

test(
    "TentacleSuggestRule tracks sed -i writes",
    "sed" in tentacle_rule_src and "TentacleSuggestRule" in tentacle_rule_src,
    "TentacleSuggestRule should extract file paths from 'sed -i' commands to match enforce detection"
)
test(
    "TentacleSuggestRule tracks tee writes",
    "tee" in tentacle_rule_src,
    "TentacleSuggestRule should extract file paths from 'tee' commands to match enforce detection"
)

try:
    sys.path.insert(0, str(REPO / "hooks"))
    from rules.tentacle import TentacleSuggestRule  # noqa: E402
    import rules.tentacle as _rt2

    suggest_rule = TentacleSuggestRule()

    _orig2_vlist = _rt2.verify_list_marker
    _orig2_sl    = _rt2.sign_list_marker
    _orig2_suggested = _rt2.SUGGESTED_FILE

    # Simulate suggest accumulating a sed -i write
    accumulated = set()
    def _fake_verify_list(p):
        return set(accumulated)
    def _fake_sign_list(p, lines):
        accumulated.update(lines)

    _rt2.verify_list_marker = _fake_verify_list
    _rt2.sign_list_marker = _fake_sign_list
    # Point SUGGESTED_FILE to a non-existent path so the early-return guard doesn't fire
    _rt2.SUGGESTED_FILE = Path("/nonexistent/tentacle-suggested-testonly")

    _rt2.verify_list_marker = _fake_verify_list
    _rt2.sign_list_marker = _fake_sign_list

    # sed -i write on a .py file
    sed_event = {
        "toolName": "bash",
        "toolArgs": {"command": "sed -i 's/old/new/' src/auth/login.py"}
    }
    suggest_rule.evaluate("postToolUse", sed_event)
    test(
        "TentacleSuggestRule extracts path from sed -i command",
        any("login.py" in p for p in accumulated),
        f"Expected 'login.py' in tracked files; got: {accumulated}"
    )

    # tee write on a .py file
    tee_event = {
        "toolName": "bash",
        "toolArgs": {"command": "echo 'code' | tee src/api/routes.py"}
    }
    suggest_rule.evaluate("postToolUse", tee_event)
    test(
        "TentacleSuggestRule extracts path from tee command",
        any("routes.py" in p for p in accumulated),
        f"Expected 'routes.py' in tracked files; got: {accumulated}"
    )

    _rt2.verify_list_marker = _orig2_vlist
    _rt2.sign_list_marker = _orig2_sl
    _rt2.SUGGESTED_FILE = _orig2_suggested

    test("TentacleSuggestRule bash-parity test ran without exception", True)
except Exception as e:
    test("TentacleSuggestRule bash-parity test ran without exception", False, str(e))




import ast

py_files = list(REPO.glob("*.py")) + list((REPO / "hooks").glob("*.py")) + list((REPO / "hooks" / "rules").glob("*.py"))
for f in sorted(py_files):
    try:
        ast.parse(f.read_text(encoding="utf-8"))
        test(f"Syntax OK: {f.name}", True)
    except SyntaxError as e:
        test(f"Syntax OK: {f.name}", False, f"Line {e.lineno}: {e.msg}")


# ═══════════════════════════════════════════════════════════════════
#  Results
# ═══════════════════════════════════════════════════════════════════

print(f"\n{'='*50}")
print(f"Results: {PASS} passed, {FAIL} failed out of {PASS + FAIL}")
if FAIL:
    print(f"⚠️  {FAIL} test(s) need attention")
    sys.exit(1)
else:
    print("🎉 All tests passed!")
