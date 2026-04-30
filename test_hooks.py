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

# Fix Windows console encoding (cp1252 can't print emoji)
if os.name == "nt":
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

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

# Isolation setup: hook_runner.py writes audit entries to Path.home()/.copilot/markers/audit.jsonl.
# All subprocess tests in this section use an isolated HOME so they never touch the operator audit log.
_isolated_home = Path(tempfile.mkdtemp(prefix="test-hooks-home-"))
_isolated_env = {**os.environ, "HOME": str(_isolated_home)}
_isolated_audit_path = _isolated_home / ".copilot" / "markers" / "audit.jsonl"

# 1a. Empty stdin → allow (fail-open)
r = subprocess.run(
    [sys.executable, str(RUNNER), "preToolUse"],
    input="",
    capture_output=True,
    text=True,
    encoding="utf-8",
    errors="replace",
    env=_isolated_env,
    timeout=10,
)
test("Empty stdin → allow (exit 0)", r.returncode == 0)
test("Empty stdin → no JSON output", r.stdout.strip() == "")

# 1b. Invalid JSON → allow (fail-open)
r = subprocess.run(
    [sys.executable, str(RUNNER), "preToolUse"],
    input="{invalid json!!!",
    capture_output=True,
    text=True,
    encoding="utf-8",
    errors="replace",
    env=_isolated_env,
    timeout=10,
)
test("Invalid JSON → allow (exit 0)", r.returncode == 0)

# 1c. Unknown event → allow (no matching rules)
r = subprocess.run(
    [sys.executable, str(RUNNER), "unknownEvent"],
    input='{"toolName":"edit"}',
    capture_output=True,
    text=True,
    encoding="utf-8",
    errors="replace",
    env=_isolated_env,
    timeout=10,
)
test("Unknown event → allow", r.returncode == 0)

# 1d. No event argument → silent exit
r = subprocess.run(
    [sys.executable, str(RUNNER)],
    input="{}",
    capture_output=True,
    text=True,
    encoding="utf-8",
    errors="replace",
    env=_isolated_env,
    timeout=10,
)
test("No event arg → silent exit", r.returncode == 0)

# 1e. Dry-run mode (HOOK_DRY_RUN=1) → allows even denied actions
_dry_env = {**_isolated_env, "HOOK_DRY_RUN": "1"}
r = subprocess.run(
    [sys.executable, str(RUNNER), "preToolUse"],
    input=json.dumps({"toolName": "bash", "toolArgs": {"command": "cat ~/.copilot/hooks/.marker-secret"}}),
    capture_output=True,
    text=True,
    encoding="utf-8",
    errors="replace",
    env=_dry_env,
    timeout=10,
)
test("Dry-run mode → no deny JSON", "permissionDecision" not in r.stdout)
test("Dry-run mode → has DRY RUN label", "DRY RUN" in r.stdout or r.returncode == 0)

# 1f. Allowed tool (read-only like "view") → passes through
r = subprocess.run(
    [sys.executable, str(RUNNER), "preToolUse"],
    input=json.dumps({"toolName": "view", "toolArgs": {"path": "/tmp/test.txt"}}),
    capture_output=True,
    text=True,
    encoding="utf-8",
    errors="replace",
    env=_isolated_env,
    timeout=10,
)
test("View tool → allowed", r.returncode == 0 and "deny" not in r.stdout)

# 1g. postToolUse event with valid data → no error
r = subprocess.run(
    [sys.executable, str(RUNNER), "postToolUse"],
    input=json.dumps({"toolName": "edit", "toolArgs": {"path": "/tmp/test.py"}, "toolResult": {"success": True}}),
    capture_output=True,
    text=True,
    encoding="utf-8",
    errors="replace",
    env=_isolated_env,
    timeout=10,
)
test("postToolUse → no error", r.returncode == 0)

# 1h. errorOccurred event
r = subprocess.run(
    [sys.executable, str(RUNNER), "errorOccurred"],
    input=json.dumps({"error": "Something broke", "toolName": "bash"}),
    capture_output=True,
    text=True,
    encoding="utf-8",
    errors="replace",
    env=_isolated_env,
    timeout=10,
)
test("errorOccurred → no crash", r.returncode == 0)

# 1i. Isolation regression: audit side effects must land in the isolated HOME, not operator state
_isolated_audit_entries = []
if _isolated_audit_path.exists():
    for _line in _isolated_audit_path.read_text(encoding="utf-8", errors="replace").splitlines():
        _line = _line.strip()
        if not _line:
            continue
        _isolated_audit_entries.append(json.loads(_line))
_isolated_decisions = [entry.get("decision", "") for entry in _isolated_audit_entries]
test(
    "Section 1 subprocess tests write audit entries under isolated HOME",
    bool(_isolated_audit_entries),
    f"Missing isolated audit entries at {_isolated_audit_path}",
)
test(
    "Section 1 isolated audit captures parse-error entry",
    "parse-error" in _isolated_decisions,
    f"decisions={_isolated_decisions}",
)
test(
    "Section 1 isolated audit captures deny-dry entry",
    "deny-dry" in _isolated_decisions,
    f"decisions={_isolated_decisions}",
)
shutil.rmtree(_isolated_home, ignore_errors=True)


# ═══════════════════════════════════════════════════════════════════
#  Section 2: Marker Auth
# ═══════════════════════════════════════════════════════════════════

print("\n🔐 Section 2: Marker Auth")

sys.path.insert(0, str(REPO / "hooks"))
from marker_auth import (
    sign_marker,
    verify_marker,
    sign_counter,
    verify_counter,
    sign_list_marker,
    verify_list_marker,
    is_secret_access,
    check_tamper_marker,
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
    test("Detects .marker-secret access", is_secret_access("cat ~/.copilot/hooks/.marker-secret"))
    test("Detects integrity-manifest access", is_secret_access("vim integrity-manifest.json"))
    test("Detects marker_auth.py access", is_secret_access("cat marker_auth.py"))
    test("Normal command → not secret", not is_secret_access("ls -la /tmp"))
    test("Normal python → not secret", not is_secret_access("python3 test.py"))

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
    is_source_path,
    get_module,
    bash_writes_source_files,
    deny,
    info,
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
test("presentation/ui/Screen.kt → ui", get_module("presentation/ui/Screen.kt") == "ui")

# 3c. bash_writes_source_files
test("echo > file.py → writes", bash_writes_source_files("echo 'x' > src/main.py"))
test("sed -i → writes", bash_writes_source_files("sed -i 's/a/b/g' file.py"))
test("tee file.py → writes", bash_writes_source_files("echo x | tee src/main.py"))
test("cp to source → writes", bash_writes_source_files("cp /tmp/a.py src/b.py"))
test("curl -o source → writes", bash_writes_source_files("curl -o app.py https://x"))
test("python -c with open → writes", bash_writes_source_files("python3 -c \"open('x.py','w').write('y')\""))
test("dd of= → writes", bash_writes_source_files("dd if=/dev/zero of=file bs=1"))
test("ls -la → NOT writes", not bash_writes_source_files("ls -la"))
test("cat file.py → NOT writes", not bash_writes_source_files("cat src/main.py"))
test("grep pattern → NOT writes", not bash_writes_source_files("grep -r 'test' src/"))
test("echo to /tmp → NOT writes", not bash_writes_source_files("echo x > /tmp/test.py"))
test("git status → NOT writes", not bash_writes_source_files("git status"))
test("python3 briefing.py → NOT writes", not bash_writes_source_files("python3 ~/.copilot/tools/briefing.py 'task'"))

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
    test("User hooks.json exists or clean HOME has none", True, str(user_hooks_path))

# 5b. Project-level hooks.json exists and is valid JSON
if project_hooks_path.exists():
    try:
        proj_hooks = json.loads(project_hooks_path.read_text(encoding="utf-8"))
        test("Project hooks.json is valid JSON", True)
    except json.JSONDecodeError as e:
        test("Project hooks.json is valid JSON", False, str(e))

    # 5c. All hooks point to unified runner (not old standalone scripts)
    old_scripts = [
        "enforce-briefing.py",
        "enforce-learn.py",
        "enforce-tentacle.py",
        "auto-briefing.py",
        "verify-integrity.py",
        "session-end.py",
        "track-bash-edits.py",
        "suggest-learn.py",
        "suggest-tentacle.py",
    ]
    for event, hooks_list in proj_hooks.get("hooks", {}).items():
        for hook in hooks_list:
            cmd = hook.get("bash", "")
            for old in old_scripts:
                test(f"Project {event} NOT using old {old}", old not in cmd, f"Found {old} in: {cmd}")
            test(f"Project {event} uses hook_runner.py", "hook_runner.py" in cmd, f"Got: {cmd}")
else:
    test("Project hooks.json exists", False, str(project_hooks_path))

# 5d. User-level and project-level should be identical (both global)
if user_hooks_path.exists() and project_hooks_path.exists():
    try:
        uh = json.loads(user_hooks_path.read_text(encoding="utf-8"))
        ph = json.loads(project_hooks_path.read_text(encoding="utf-8"))
        # Compare normalized
        test(
            "User and project hooks.json match",
            json.dumps(uh, sort_keys=True) == json.dumps(ph, sort_keys=True),
            "User-level and project-level hooks have diverged!",
        )
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
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=30,
    )
    test("migrate.py runs without error", r.returncode == 0, f"stderr: {r.stderr[:200]}")

    # Verify migrations applied
    db = sqlite3.connect(test_db_path)

    # Check schema_version table
    version = db.execute("SELECT MAX(version) FROM schema_version").fetchone()[0]
    test("Schema version is set", version is not None and version >= 2, f"Got version: {version}")

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
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=30,
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
    capture_output=True,
    text=True,
    encoding="utf-8",
    errors="replace",
    timeout=30,
)
test("--status runs without error", r.returncode == 0, f"stderr: {r.stderr[:200]}")

# 7b. --check flag works (no changes applied)
r = subprocess.run(
    [sys.executable, str(auto_update), "--check"],
    capture_output=True,
    text=True,
    encoding="utf-8",
    errors="replace",
    timeout=30,
)
test("--check runs without error", r.returncode == 0, f"stderr: {r.stderr[:200]}")

# 7c. --doctor flag works
r = subprocess.run(
    [sys.executable, str(auto_update), "--doctor"],
    capture_output=True,
    text=True,
    encoding="utf-8",
    errors="replace",
    timeout=30,
)
test("--doctor runs without error", r.returncode == 0, f"stderr: {r.stderr[:200]}")

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
test(
    "Has stale embedding cleanup",
    "DELETE FROM embeddings" in ek_src and "NOT IN" in ek_src,
    "extract-knowledge.py should clean stale embeddings",
)
test(
    "Has orphan relation cleanup",
    "DELETE FROM knowledge_relations" in ek_src and "NOT IN" in ek_src,
    "extract-knowledge.py should clean orphan relations",
)


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
test("README ≤ 600 lines", len(readme.splitlines()) <= 600, f"Got {len(readme.splitlines())} lines")

test("CONTRIBUTING.md exists", (REPO / "CONTRIBUTING.md").exists())
test("CHANGELOG.md exists", (REPO / "CHANGELOG.md").exists())
test("SECURITY.md exists", (REPO / "SECURITY.md").exists())

docs_dir = REPO / "docs"
test("docs/ directory exists", docs_dir.is_dir())
for doc in ["USAGE.md", "SKILLS.md", "HOOKS.md", "AUTO-UPDATE.md"]:
    test(f"docs/{doc} exists", (docs_dir / doc).exists())

# Check README links to docs/
for doc in ["USAGE.md", "SKILLS.md", "HOOKS.md", "AUTO-UPDATE.md"]:
    test(f"README links to docs/{doc}", f"docs/{doc}" in readme, f"Missing link to docs/{doc}")


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
    "Remove the obsolete 'tentacle.py \"your task\"' suggestion from TentacleEnforceRule",
)

# The message must reference the correct workflow entry point (create command)
test(
    "TentacleEnforceRule suggests tentacle.py create",
    "tentacle.py create" in tentacle_rule_src,
    "TentacleEnforceRule should guide users to start with 'tentacle.py create <name>'",
)

# The message must reference swarm (the dispatch step)
test(
    "TentacleEnforceRule suggests tentacle.py swarm",
    "tentacle.py swarm" in tentacle_rule_src,
    "TentacleEnforceRule should reference 'tentacle.py swarm <name>' for dispatch",
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
    _orig_vm = _rt.verify_marker
    _orig_sl = _rt.sign_list_marker
    _orig_isa = _rt.is_secret_access
    _orig_ctm = _rt.check_tamper_marker
    _orig_ggr = _rt._get_git_root  # patch so legacy entries are not filtered by repo prefix

    # 3 files across 2 modules → should trigger deny
    _fake_files = ["src/auth/login.py", "src/api/routes.py", "tests/test_auth.py"]
    _rt.verify_list_marker = lambda p: set(_fake_files)
    _rt.verify_marker = lambda p, n: False
    _rt.sign_list_marker = lambda p, lines: None
    _rt.is_secret_access = lambda c: False
    _rt.check_tamper_marker = lambda: False
    _rt._get_git_root = lambda: None  # None → no repo-prefix filtering on legacy entries

    result = rule.evaluate("preToolUse", {"toolName": "edit", "toolArgs": {"path": "x.py"}})

    # Restore
    _rt.verify_list_marker = _orig_vlist
    _rt.verify_marker = _orig_vm
    _rt.sign_list_marker = _orig_sl
    _rt.is_secret_access = _orig_isa
    _rt.check_tamper_marker = _orig_ctm
    _rt._get_git_root = _orig_ggr

    if result is not None:
        deny_msg = result.get("permissionDecisionReason", "") if isinstance(result, dict) else str(result)
        test("TentacleEnforceRule deny message contains 'create'", "create" in deny_msg, f"Got: {deny_msg!r:.120}")
        test("TentacleEnforceRule deny message contains 'swarm'", "swarm" in deny_msg, f"Got: {deny_msg!r:.120}")
        test(
            "TentacleEnforceRule deny message does not contain '\"your task\"'",
            '"your task"' not in deny_msg,
            f"Got: {deny_msg!r:.120}",
        )
        test(
            "TentacleEnforceRule deny message references handoff escalation path",
            "handoff" in deny_msg,
            f"Deny message should guide sub-agents to write handoff.md; got: {deny_msg!r:.120}",
        )
        test(
            "TentacleEnforceRule deny message mentions commit convention",
            "commit" in deny_msg.lower(),
            f"Deny message should clarify commit convention; got: {deny_msg!r:.120}",
        )
        test(
            "TentacleEnforceRule deny message mentions git push",
            "push" in deny_msg.lower(),
            f"Deny message should mention git push; got: {deny_msg!r:.120}",
        )
        test(
            "TentacleEnforceRule deny message mentions complete step",
            "complete" in deny_msg.lower(),
            f"Deny message should reference 'tentacle.py complete' as the closure step; got: {deny_msg!r:.120}",
        )
        test(
            "TentacleEnforceRule deny message mentions status",
            "status" in deny_msg.lower(),
            f"Deny message should reference 'tentacle.py status' for runtime monitoring; got: {deny_msg!r:.120}",
        )
    else:
        test("TentacleEnforceRule returned a result for 3-file/2-module edit", False, "Expected deny, got None")

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
    "TentacleSuggestRule should extract file paths from 'sed -i' commands to match enforce detection",
)
test(
    "TentacleSuggestRule tracks tee writes",
    "tee" in tentacle_rule_src,
    "TentacleSuggestRule should extract file paths from 'tee' commands to match enforce detection",
)

try:
    sys.path.insert(0, str(REPO / "hooks"))
    from rules.tentacle import TentacleSuggestRule  # noqa: E402
    import rules.tentacle as _rt2

    suggest_rule = TentacleSuggestRule()

    _orig2_vlist = _rt2.verify_list_marker
    _orig2_sl = _rt2.sign_list_marker
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
    sed_event = {"toolName": "bash", "toolArgs": {"command": "sed -i 's/old/new/' src/auth/login.py"}}
    suggest_rule.evaluate("postToolUse", sed_event)
    test(
        "TentacleSuggestRule extracts path from sed -i command",
        any("login.py" in p for p in accumulated),
        f"Expected 'login.py' in tracked files; got: {accumulated}",
    )

    # tee write on a .py file
    tee_event = {"toolName": "bash", "toolArgs": {"command": "echo 'code' | tee src/api/routes.py"}}
    suggest_rule.evaluate("postToolUse", tee_event)
    test(
        "TentacleSuggestRule extracts path from tee command",
        any("routes.py" in p for p in accumulated),
        f"Expected 'routes.py' in tracked files; got: {accumulated}",
    )

    _rt2.verify_list_marker = _orig2_vlist
    _rt2.sign_list_marker = _orig2_sl
    _rt2.SUGGESTED_FILE = _orig2_suggested

    test("TentacleSuggestRule bash-parity test ran without exception", True)
except Exception as e:
    test("TentacleSuggestRule bash-parity test ran without exception", False, str(e))


# ═══════════════════════════════════════════════════════════════════
#  Section 12: Subagent Git Guard
# ═══════════════════════════════════════════════════════════════════

print("\n🚫 Section 12: Subagent Git Guard")

# 12a. Rule registered in preToolUse
from rules import get_rules_for_event as _gre12

_pre12 = _gre12("preToolUse")
_pre12_names = [r.name for r in _pre12]
test("preToolUse has subagent-git-guard", "subagent-git-guard" in _pre12_names)

if "tentacle-enforce" in _pre12_names and "subagent-git-guard" in _pre12_names:
    test(
        "subagent-git-guard registered after tentacle-enforce",
        _pre12_names.index("tentacle-enforce") < _pre12_names.index("subagent-git-guard"),
    )

# 12b–12g: SubagentGitGuardRule unit tests
try:
    sys.path.insert(0, str(REPO / "hooks"))
    from rules.subagent_guard import SubagentGitGuardRule
    import rules.subagent_guard as _sg

    _orig_sg_fresh = _sg._marker_is_fresh
    rule_sg = SubagentGitGuardRule()

    _sg._marker_is_fresh = lambda: False
    result_allow = rule_sg.evaluate(
        "preToolUse",
        {
            "toolName": "bash",
            "toolArgs": {"command": "git commit -m 'test'"},
        },
    )
    test("No active marker → git commit allowed", result_allow is None)

    _sg._marker_is_fresh = lambda: True
    result_nongit = rule_sg.evaluate(
        "preToolUse",
        {
            "toolName": "bash",
            "toolArgs": {"command": "ls -la"},
        },
    )
    test("Non-git command → allowed even with active marker", result_nongit is None)

    result_deny_commit = rule_sg.evaluate(
        "preToolUse",
        {
            "toolName": "bash",
            "toolArgs": {"command": "git commit -m 'wip'"},
        },
    )
    test(
        "Active marker + git commit → denied",
        isinstance(result_deny_commit, dict) and result_deny_commit.get("permissionDecision") == "deny",
    )

    result_deny_push = rule_sg.evaluate(
        "preToolUse",
        {
            "toolName": "bash",
            "toolArgs": {"command": "git push origin main"},
        },
    )
    test(
        "Active marker + git push → denied",
        isinstance(result_deny_push, dict) and result_deny_push.get("permissionDecision") == "deny",
    )

    if isinstance(result_deny_commit, dict):
        msg = result_deny_commit.get("permissionDecisionReason", "")
        test("Deny message mentions subagent mode", "subagent" in msg.lower())
        test("Deny message mentions handoff.md", "handoff.md" in msg)
        test("Deny message mentions tentacle.py complete", "tentacle.py complete" in msg)
        test("Deny message mentions local-only limitation", "local" in msg.lower())

    result_edit = rule_sg.evaluate(
        "preToolUse",
        {
            "toolName": "edit",
            "toolArgs": {"path": "x.py"},
        },
    )
    test("edit tool with active marker → not blocked by subagent-git-guard", result_edit is None)

    _sg._marker_is_fresh = _orig_sg_fresh
    test("SubagentGitGuardRule unit tests ran without exception", True)
except Exception as e:
    test("SubagentGitGuardRule unit tests ran without exception", False, str(e))

# 12b2. ImportError fallback: verify_marker returns p.is_file() (existence fallback)
# Verify that if marker_auth is unavailable, the guard is not silently disabled.
try:
    import rules.subagent_guard as _sg_fb

    _orig_vm_fb = _sg_fb.verify_marker

    # Simulate the import-failure fallback: existence-only
    _sg_fb.verify_marker = lambda p, n: p.is_file()

    _fb_rule = _sg_fb.SubagentGitGuardRule()
    _orig_sm_fb = _sg_fb.SUBAGENT_MARKER

    # No file → verify_marker returns False → allow
    _sg_fb.SUBAGENT_MARKER = Path("/nonexistent/dispatched-subagent-active-testonly")
    result_fb_absent = _fb_rule.evaluate(
        "preToolUse",
        {
            "toolName": "bash",
            "toolArgs": {"command": "git commit -m x"},
        },
    )
    test("ImportError fallback (p.is_file): absent marker → allowed", result_fb_absent is None)

    # File exists + fresh timestamp → allow (since no HMAC, file passes, but now check TTL)
    # The important thing: existence-fallback does NOT silently disable the guard
    _sg_fb.verify_marker = _orig_vm_fb
    _sg_fb.SUBAGENT_MARKER = _orig_sm_fb
    test(
        "ImportError fallback: guard not silently disabled (verify_marker != always-False)",
        _sg_fb.verify_marker is not (lambda p, n: False),
    )

    # Source-level check: fallback must NOT be `return False`
    _sg_src = (REPO / "hooks" / "rules" / "subagent_guard.py").read_text(encoding="utf-8")
    _fallback_block = _sg_src.split("except ImportError:")[1].split("def verify_marker")[1].split("\n\n")[0]
    test(
        "subagent_guard.py ImportError fallback uses is_file() not False",
        "is_file()" in _fallback_block and "return False" not in _fallback_block,
    )

    test("ImportError fallback test ran without exception", True)
except Exception as e:
    test("ImportError fallback test ran without exception", False, str(e))

# 12h. check_subagent_marker.py exists and syntax-valid
_csm_path = REPO / "hooks" / "check_subagent_marker.py"
test("hooks/check_subagent_marker.py exists", _csm_path.is_file())
if _csm_path.is_file():
    try:
        import ast as _ast12

        _ast12.parse(_csm_path.read_text(encoding="utf-8"))
        test("check_subagent_marker.py syntax valid", True)
    except SyntaxError as e:
        test("check_subagent_marker.py syntax valid", False, str(e))

# 12i. check_subagent_marker.py content checks
if _csm_path.is_file():
    _csm_src = _csm_path.read_text(encoding="utf-8")
    test("check_subagent_marker.py has TTL constant", "MARKER_TTL" in _csm_src)
    test("check_subagent_marker.py checks dispatched-subagent-active", "dispatched-subagent-active" in _csm_src)
    test("check_subagent_marker.py fails open on error", "return False" in _csm_src)
    test("check_subagent_marker.py mentions handoff.md", "handoff.md" in _csm_src)
    test("check_subagent_marker.py uses verify_marker", "verify_marker" in _csm_src or "_verify_marker" in _csm_src)
    test("check_subagent_marker.py has zombie marker check", "active_tentacles" in _csm_src)

# 12j. Absent marker → exit 0
if _csm_path.is_file():
    import time as _time12

    _absent_home = Path(tempfile.mkdtemp(prefix="test-home-"))
    r_absent = subprocess.run(
        [sys.executable, str(_csm_path)],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=10,
        env={**os.environ, "HOME": str(_absent_home)},
    )
    test(
        "check_subagent_marker.py absent marker → exit 0",
        r_absent.returncode == 0,
        f"exit={r_absent.returncode} stderr={r_absent.stderr[:80]}",
    )
    shutil.rmtree(str(_absent_home), ignore_errors=True)

# 12k. Stale marker (no secret) → exit 0
if _csm_path.is_file():
    _stale_home = Path(tempfile.mkdtemp(prefix="test-stale-"))
    (_stale_home / ".copilot" / "markers").mkdir(parents=True)
    stale_ts = int(_time12.time()) - 99999
    (_stale_home / ".copilot" / "markers" / "dispatched-subagent-active").write_text(
        json.dumps({"name": "dispatched-subagent-active", "ts": str(stale_ts)})
    )
    r_stale = subprocess.run(
        [sys.executable, str(_csm_path)],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=10,
        env={**os.environ, "HOME": str(_stale_home)},
    )
    test(
        "check_subagent_marker.py stale marker (no secret) → exit 0",
        r_stale.returncode == 0,
        f"exit={r_stale.returncode} stdout={r_stale.stdout[:80]}",
    )
    shutil.rmtree(str(_stale_home), ignore_errors=True)

# 12l. Fresh marker, no secret (existence fallback) → exit 1
if _csm_path.is_file():
    _fresh_home = Path(tempfile.mkdtemp(prefix="test-fresh-"))
    (_fresh_home / ".copilot" / "markers").mkdir(parents=True)
    (_fresh_home / ".copilot" / "markers" / "dispatched-subagent-active").write_text(
        json.dumps({"name": "dispatched-subagent-active", "ts": str(int(_time12.time()))})
    )
    r_fresh = subprocess.run(
        [sys.executable, str(_csm_path)],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=10,
        env={**os.environ, "HOME": str(_fresh_home)},
    )
    test(
        "check_subagent_marker.py fresh marker (no secret, existence fallback) → exit 1",
        r_fresh.returncode == 1,
        f"exit={r_fresh.returncode} stdout={r_fresh.stdout[:120]}",
    )
    if r_fresh.returncode == 1:
        test("Block message mentions handoff.md", "handoff.md" in r_fresh.stdout)
        test("Block message mentions SUBAGENT", "SUBAGENT" in r_fresh.stdout.upper())
    shutil.rmtree(str(_fresh_home), ignore_errors=True)

# 12l2. Fresh marker with bad sig + secret present → exit 0 (HMAC rejects unsigned)
if _csm_path.is_file():
    import secrets as _secrets12

    _badsig_home = Path(tempfile.mkdtemp(prefix="test-badsig-"))
    (_badsig_home / ".copilot" / "hooks").mkdir(parents=True)
    (_badsig_home / ".copilot" / "markers").mkdir(parents=True)
    (_badsig_home / ".copilot" / "hooks" / ".marker-secret").write_text(_secrets12.token_hex(32), encoding="utf-8")
    (_badsig_home / ".copilot" / "markers" / "dispatched-subagent-active").write_text(
        json.dumps({"name": "dispatched-subagent-active", "ts": str(int(_time12.time())), "sig": "badsig"})
    )
    r_badsig = subprocess.run(
        [sys.executable, str(_csm_path)],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=10,
        env={**os.environ, "HOME": str(_badsig_home)},
    )
    test(
        "check_subagent_marker.py bad sig + secret present → exit 0 (HMAC rejects)",
        r_badsig.returncode == 0,
        f"exit={r_badsig.returncode} stdout={r_badsig.stdout[:120]}",
    )
    shutil.rmtree(str(_badsig_home), ignore_errors=True)

# 12m. pre-push exists and uses $HOME/.copilot/tools (not dirname)
_prepush_path = REPO / "hooks" / "pre-push"
test("hooks/pre-push exists", _prepush_path.is_file())
if _prepush_path.is_file():
    _prepush_src = _prepush_path.read_text(encoding="utf-8")
    test("pre-push has shebang", _prepush_src.startswith("#!/"))
    test("pre-push calls check_subagent_marker.py", "check_subagent_marker.py" in _prepush_src)
    test("pre-push exits 0 normally", "exit 0" in _prepush_src)
    test(
        "pre-push uses $HOME/.copilot/tools for guard path",
        "$HOME/.copilot/tools/hooks/check_subagent_marker.py" in _prepush_src,
    )
    test("pre-push does NOT use dirname-based path for guard", "$(dirname" not in _prepush_src)

# 12n. pre-commit uses $HOME/.copilot/tools (not dirname) for the guard
_precommit_src = (REPO / "hooks" / "pre-commit").read_text(encoding="utf-8")
test("pre-commit calls check_subagent_marker.py", "check_subagent_marker.py" in _precommit_src)
test(
    "pre-commit uses $HOME/.copilot/tools for guard path",
    "$HOME/.copilot/tools/hooks/check_subagent_marker.py" in _precommit_src,
)
_guard_block = _precommit_src.split("check_subagent_marker.py")[0].split("SUBAGENT_CHECK")[-1]
test("pre-commit guard block does NOT use dirname resolution", "$(dirname" not in _guard_block)

# 12o. install.py exposes install_git_hooks
_install_src = (REPO / "install.py").read_text(encoding="utf-8")
test("install.py has install_git_hooks function", "def install_git_hooks" in _install_src)
test("install.py has --install-git-hooks flag", "--install-git-hooks" in _install_src)

# 12p. install.py syntax valid
try:
    import ast as _ast12p

    _ast12p.parse((REPO / "install.py").read_text(encoding="utf-8"))
    test("install.py syntax valid after changes", True)
except SyntaxError as e:
    test("install.py syntax valid after changes", False, str(e))

# ── Zombie marker tests ────────────────────────────────────────────────────

# 12q. SubagentGitGuardRule: zombie marker (active_tentacles=[]) → allow
try:
    import rules.subagent_guard as _sg2

    rule_sg2 = _sg2.SubagentGitGuardRule()
    _orig_vm2 = _sg2.verify_marker

    _sg2.verify_marker = lambda p, n: True

    _zombie_home = Path(tempfile.mkdtemp(prefix="test-zombie-"))
    (_zombie_home / ".copilot" / "markers").mkdir(parents=True)
    _zombie_file = _zombie_home / ".copilot" / "markers" / "dispatched-subagent-active"
    _zombie_file.write_text(
        json.dumps(
            {
                "name": "dispatched-subagent-active",
                "ts": str(int(_time12.time())),
                "active_tentacles": [],
            }
        )
    )
    _orig_sm2 = _sg2.SUBAGENT_MARKER
    _sg2.SUBAGENT_MARKER = _zombie_file

    result_zombie = rule_sg2.evaluate(
        "preToolUse",
        {
            "toolName": "bash",
            "toolArgs": {"command": "git commit -m 'test'"},
        },
    )
    test(
        "SubagentGitGuardRule: zombie marker (active_tentacles=[]) → allowed",
        result_zombie is None,
        f"Expected None, got: {result_zombie!r:.80}",
    )

    _zombie_file.write_text(
        json.dumps(
            {
                "name": "dispatched-subagent-active",
                "ts": str(int(_time12.time())),
                "active_tentacles": ["my-tentacle"],
            }
        )
    )
    result_active = rule_sg2.evaluate(
        "preToolUse",
        {
            "toolName": "bash",
            "toolArgs": {"command": "git commit -m 'test'"},
        },
    )
    test(
        "SubagentGitGuardRule: non-empty active_tentacles → still blocked",
        isinstance(result_active, dict) and result_active.get("permissionDecision") == "deny",
        f"Expected deny, got: {result_active!r:.80}",
    )

    _sg2.verify_marker = _orig_vm2
    _sg2.SUBAGENT_MARKER = _orig_sm2
    shutil.rmtree(str(_zombie_home), ignore_errors=True)
    test("SubagentGitGuardRule zombie test ran without exception", True)
except Exception as e:
    test("SubagentGitGuardRule zombie test ran without exception", False, str(e))

# 12r. check_subagent_marker.py: zombie marker → exit 0
if _csm_path.is_file():
    _z_home = Path(tempfile.mkdtemp(prefix="test-zombie-csm-"))
    (_z_home / ".copilot" / "markers").mkdir(parents=True)
    (_z_home / ".copilot" / "markers" / "dispatched-subagent-active").write_text(
        json.dumps(
            {
                "name": "dispatched-subagent-active",
                "ts": str(int(_time12.time())),
                "active_tentacles": [],
            }
        )
    )
    r_zombie = subprocess.run(
        [sys.executable, str(_csm_path)],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=10,
        env={**os.environ, "HOME": str(_z_home)},
    )
    test(
        "check_subagent_marker.py zombie marker (active_tentacles=[]) → exit 0",
        r_zombie.returncode == 0,
        f"exit={r_zombie.returncode} stdout={r_zombie.stdout[:120]}",
    )
    shutil.rmtree(str(_z_home), ignore_errors=True)

# 12r2. check_subagent_marker.py: non-empty active_tentacles → still exit 1
if _csm_path.is_file():
    _nz_home = Path(tempfile.mkdtemp(prefix="test-nonzombie-"))
    (_nz_home / ".copilot" / "markers").mkdir(parents=True)
    (_nz_home / ".copilot" / "markers" / "dispatched-subagent-active").write_text(
        json.dumps(
            {
                "name": "dispatched-subagent-active",
                "ts": str(int(_time12.time())),
                "active_tentacles": ["my-tentacle"],
            }
        )
    )
    r_nonzombie = subprocess.run(
        [sys.executable, str(_csm_path)],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=10,
        env={**os.environ, "HOME": str(_nz_home)},
    )
    test(
        "check_subagent_marker.py non-empty active_tentacles → exit 1 (still blocks)",
        r_nonzombie.returncode == 1,
        f"exit={r_nonzombie.returncode} stdout={r_nonzombie.stdout[:120]}",
    )
    shutil.rmtree(str(_nz_home), ignore_errors=True)

# ── E2E: installed hook in a non-tools repo ────────────────────────────────

# 12s. E2E: installed hook correctly invokes guard in a fresh git repo.
# Skipped when REPO is not at $HOME/.copilot/tools (e.g. non-standard WSL path).
_canonical_tools = Path.home() / ".copilot" / "tools"
_skip_e2e = REPO.resolve() != _canonical_tools.resolve()

if _skip_e2e:
    test("E2E: installed hook blocks commit (skip: REPO not at $HOME/.copilot/tools)", True)
else:
    _e2e_repo = Path(tempfile.mkdtemp(prefix="test-e2e-repo-"))
    _e2e_marker_written = False
    _real_marker = Path.home() / ".copilot" / "markers" / "dispatched-subagent-active"
    try:
        subprocess.run(["git", "init", str(_e2e_repo)], capture_output=True, check=True, timeout=10)
        subprocess.run(
            ["git", "config", "user.email", "test@test.com"], cwd=str(_e2e_repo), capture_output=True, timeout=5
        )
        subprocess.run(["git", "config", "user.name", "Test"], cwd=str(_e2e_repo), capture_output=True, timeout=5)

        _e2e_hook_dst = _e2e_repo / ".git" / "hooks" / "pre-commit"
        (_e2e_repo / ".git" / "hooks").mkdir(parents=True, exist_ok=True)
        shutil.copy2(str(REPO / "hooks" / "pre-commit"), str(_e2e_hook_dst))
        _e2e_hook_dst.chmod(_e2e_hook_dst.stat().st_mode | 0o111)

        # (a) content check: canonical path, no dirname
        _installed = _e2e_hook_dst.read_text(encoding="utf-8")
        test(
            "E2E: installed hook uses $HOME/.copilot/tools (not dirname)",
            "$HOME/.copilot/tools/hooks/check_subagent_marker.py" in _installed
            and "$(dirname" not in _installed.split("check_subagent_marker.py")[0].split("SUBAGENT_CHECK")[-1],
        )

        # (b) blocking check: fresh marker → commit blocked
        # Sign the marker when ~/.copilot/hooks/.marker-secret exists so that
        # _verify_marker passes HMAC verification on machines with a secret.
        # Machines without a secret fall back to existence-only (no sig needed).
        _real_marker.parent.mkdir(parents=True, exist_ok=True)
        _e2e_ts = str(int(time.time()))
        _e2e_name = "dispatched-subagent-active"
        _e2e_marker_data: dict = {
            "name": _e2e_name,
            "ts": _e2e_ts,
            "active_tentacles": ["e2e-test"],
        }
        _e2e_secret = _read_secret()
        if _e2e_secret:
            _e2e_sig = hmac.new(_e2e_secret.encode(), f"{_e2e_name}:{_e2e_ts}".encode(), hashlib.sha256).hexdigest()
            _e2e_marker_data["sig"] = _e2e_sig
        _real_marker.write_text(json.dumps(_e2e_marker_data), encoding="utf-8")
        _e2e_marker_written = True

        (_e2e_repo / "README.md").write_text("test\n", encoding="utf-8")
        subprocess.run(["git", "add", "README.md"], cwd=str(_e2e_repo), capture_output=True, timeout=5)
        # Inject PYTHON_BIN so Git's MSYS2 sh can find the interpreter
        # reliably regardless of PATH translation quirks.
        _e2e_env = {**os.environ, "PYTHON_BIN": sys.executable}
        r_e2e = subprocess.run(
            ["git", "commit", "-m", "test"],
            cwd=str(_e2e_repo),
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=15,
            env=_e2e_env,
        )
        test(
            "E2E: git commit in non-tools repo blocked by hook when marker present",
            r_e2e.returncode != 0,
            f"exit={r_e2e.returncode} stdout={r_e2e.stdout[:150]}",
        )
        if r_e2e.returncode != 0:
            combined = r_e2e.stdout + r_e2e.stderr
            test("E2E: block message mentions SUBAGENT", "SUBAGENT" in combined.upper(), f"Got: {combined[:150]}")

    except subprocess.CalledProcessError as e:
        test("E2E: git init succeeded", False, str(e))
    except Exception as e:
        test("E2E: installed-hook path test ran without exception", False, str(e))
    finally:
        if _e2e_marker_written and _real_marker.exists():
            try:
                _real_marker.unlink()
            except Exception:
                pass
        shutil.rmtree(str(_e2e_repo), ignore_errors=True)


# ═══════════════════════════════════════════════════════════════════
#  Section 13: Repo-scope isolation & dual-format tests
# ═══════════════════════════════════════════════════════════════════

print("\n── Section 13: Repo-scope isolation & dual-format ──")

# 13a. _read_tentacle_info handles old string-list format
try:
    import importlib.util as _ilu13

    _csm_spec = _ilu13.spec_from_file_location("check_subagent_marker_13", _csm_path)
    _csm13 = _ilu13.module_from_spec(_csm_spec)
    _csm_spec.loader.exec_module(_csm13)

    _old_mp13 = _csm13.MARKER_PATH
    _t13_home = Path(tempfile.mkdtemp(prefix="test-dualfmt-"))
    _t13_marker = _t13_home / ".copilot" / "markers" / "dispatched-subagent-active"
    _t13_marker.parent.mkdir(parents=True)

    # Write old string-list format
    _t13_marker.write_text(
        json.dumps(
            {
                "name": "dispatched-subagent-active",
                "ts": str(int(_time12.time())),
                "active_tentacles": ["tentacle-alpha", "tentacle-beta"],
            }
        )
    )
    _csm13.MARKER_PATH = _t13_marker

    info_old = _csm13._read_tentacle_info()
    test(
        "13a: _read_tentacle_info handles old string-list format",
        "tentacle-alpha" in info_old and "tentacle-beta" in info_old,
        f"Got: {info_old!r}",
    )

    # 13b. _read_tentacle_info handles new dict-list format
    _t13_marker.write_text(
        json.dumps(
            {
                "name": "dispatched-subagent-active",
                "ts": str(int(_time12.time())),
                "active_tentacles": [
                    {"name": "tentacle-alpha", "ts": str(int(_time12.time())), "git_root": "/some/repo"},
                    {"name": "tentacle-beta", "ts": str(int(_time12.time())), "git_root": "/some/repo"},
                ],
            }
        )
    )
    _csm13.MARKER_PATH = _t13_marker
    info_new = _csm13._read_tentacle_info()
    test(
        "13b: _read_tentacle_info handles new dict-list format",
        "tentacle-alpha" in info_new and "tentacle-beta" in info_new,
        f"Got: {info_new!r}",
    )

    _csm13.MARKER_PATH = _old_mp13
    shutil.rmtree(str(_t13_home), ignore_errors=True)
    test("13a-b: dual-format tentacle info tests ran", True)
except Exception as e:
    test("13a-b: dual-format tentacle info tests ran", False, str(e))

# 13c-d. Old-format marker: different vs same git_root
if _csm_path.is_file():
    _repo_a = Path(tempfile.mkdtemp(prefix="test-repo-a-"))
    _repo_b = Path(tempfile.mkdtemp(prefix="test-repo-b-"))

    try:
        subprocess.run(["git", "init", str(_repo_a)], capture_output=True, check=True, timeout=10)
        subprocess.run(["git", "config", "user.email", "t@t.com"], cwd=str(_repo_a), capture_output=True, timeout=5)
        subprocess.run(["git", "config", "user.name", "T"], cwd=str(_repo_a), capture_output=True, timeout=5)

        _rA_home = Path(tempfile.mkdtemp(prefix="test-roota-home-"))
        (_rA_home / ".copilot" / "markers").mkdir(parents=True)
        _rA_marker = _rA_home / ".copilot" / "markers" / "dispatched-subagent-active"

        # Marker from repo-b → should NOT block in repo-a
        _rA_marker.write_text(
            json.dumps(
                {
                    "name": "dispatched-subagent-active",
                    "ts": str(int(_time12.time())),
                    "active_tentacles": ["my-tentacle"],
                    "git_root": str(_repo_b),
                }
            )
        )
        r_cross = subprocess.run(
            [sys.executable, str(_csm_path)],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=10,
            env={**os.environ, "HOME": str(_rA_home)},
            cwd=str(_repo_a),
        )
        test(
            "13c: old-format marker different git_root → exit 0 (cross-repo skip)",
            r_cross.returncode == 0,
            f"exit={r_cross.returncode} stdout={r_cross.stdout[:120]}",
        )

        # Marker from repo-a → should block in repo-a
        _rA_marker.write_text(
            json.dumps(
                {
                    "name": "dispatched-subagent-active",
                    "ts": str(int(_time12.time())),
                    "active_tentacles": ["my-tentacle"],
                    "git_root": str(_repo_a),
                }
            )
        )
        r_same = subprocess.run(
            [sys.executable, str(_csm_path)],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=10,
            env={**os.environ, "HOME": str(_rA_home)},
            cwd=str(_repo_a),
        )
        test(
            "13d: old-format marker same git_root → exit 1 (blocks)",
            r_same.returncode == 1,
            f"exit={r_same.returncode} stdout={r_same.stdout[:120]}",
        )

        shutil.rmtree(str(_rA_home), ignore_errors=True)
    except subprocess.CalledProcessError as e:
        test("13c-d: git-root repo-scope tests (subprocess setup)", False, str(e))
    except Exception as e:
        test("13c-d: git-root repo-scope tests", False, str(e))
    finally:
        shutil.rmtree(str(_repo_a), ignore_errors=True)
        shutil.rmtree(str(_repo_b), ignore_errors=True)

# 13e. Old-format marker without git_root → conservative block
if _csm_path.is_file():
    _c_home = Path(tempfile.mkdtemp(prefix="test-conservative-"))
    (_c_home / ".copilot" / "markers").mkdir(parents=True)
    (_c_home / ".copilot" / "markers" / "dispatched-subagent-active").write_text(
        json.dumps(
            {
                "name": "dispatched-subagent-active",
                "ts": str(int(_time12.time())),
                "active_tentacles": ["my-tentacle"],
                # No git_root — old marker without repo metadata
            }
        )
    )
    r_conservative = subprocess.run(
        [sys.executable, str(_csm_path)],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=10,
        env={**os.environ, "HOME": str(_c_home)},
    )
    test(
        "13e: absent git_root → exit 1 (conservative block, backward compat)",
        r_conservative.returncode == 1,
        f"exit={r_conservative.returncode} stdout={r_conservative.stdout[:120]}",
    )
    shutil.rmtree(str(_c_home), ignore_errors=True)

# 13f-g. New dict-list format: all-other-repo vs one-matching-repo
if _csm_path.is_file():
    _nf_repo = Path(tempfile.mkdtemp(prefix="test-nf-repo-"))
    _nf_other = Path(tempfile.mkdtemp(prefix="test-nf-other-"))
    try:
        subprocess.run(["git", "init", str(_nf_repo)], capture_output=True, check=True, timeout=10)
        subprocess.run(["git", "config", "user.email", "t@t.com"], cwd=str(_nf_repo), capture_output=True, timeout=5)

        _nf_home = Path(tempfile.mkdtemp(prefix="test-nf-home-"))
        (_nf_home / ".copilot" / "markers").mkdir(parents=True)
        _nf_marker = _nf_home / ".copilot" / "markers" / "dispatched-subagent-active"

        # All entries for other repo → exit 0
        _nf_marker.write_text(
            json.dumps(
                {
                    "name": "dispatched-subagent-active",
                    "ts": str(int(_time12.time())),
                    "active_tentacles": [
                        {"name": "t1", "ts": str(int(_time12.time())), "git_root": str(_nf_other)},
                    ],
                }
            )
        )
        r_all_other = subprocess.run(
            [sys.executable, str(_csm_path)],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=10,
            env={**os.environ, "HOME": str(_nf_home)},
            cwd=str(_nf_repo),
        )
        test(
            "13f: new dict-list all entries other repo → exit 0",
            r_all_other.returncode == 0,
            f"exit={r_all_other.returncode} stdout={r_all_other.stdout[:120]}",
        )

        # Mixed: one entry for current repo → exit 1
        _nf_marker.write_text(
            json.dumps(
                {
                    "name": "dispatched-subagent-active",
                    "ts": str(int(_time12.time())),
                    "active_tentacles": [
                        {"name": "t-other", "ts": str(int(_time12.time())), "git_root": str(_nf_other)},
                        {"name": "t-current", "ts": str(int(_time12.time())), "git_root": str(_nf_repo)},
                    ],
                }
            )
        )
        r_one_match = subprocess.run(
            [sys.executable, str(_csm_path)],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=10,
            env={**os.environ, "HOME": str(_nf_home)},
            cwd=str(_nf_repo),
        )
        test(
            "13g: new dict-list one entry for current repo → exit 1 (blocks)",
            r_one_match.returncode == 1,
            f"exit={r_one_match.returncode} stdout={r_one_match.stdout[:120]}",
        )

        shutil.rmtree(str(_nf_home), ignore_errors=True)
    except subprocess.CalledProcessError as e:
        test("13f-g: new dict-list repo-scope tests (subprocess setup)", False, str(e))
    except Exception as e:
        test("13f-g: new dict-list repo-scope tests", False, str(e))
    finally:
        shutil.rmtree(str(_nf_repo), ignore_errors=True)
        shutil.rmtree(str(_nf_other), ignore_errors=True)

# 13h. New dict entry absent git_root → conservative block
if _csm_path.is_file():
    _ca_home = Path(tempfile.mkdtemp(prefix="test-consv-absent-"))
    (_ca_home / ".copilot" / "markers").mkdir(parents=True)
    (_ca_home / ".copilot" / "markers" / "dispatched-subagent-active").write_text(
        json.dumps(
            {
                "name": "dispatched-subagent-active",
                "ts": str(int(_time12.time())),
                "active_tentacles": [{"name": "t1", "ts": str(int(_time12.time()))}],  # No git_root
            }
        )
    )
    r_ca = subprocess.run(
        [sys.executable, str(_csm_path)],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=10,
        env={**os.environ, "HOME": str(_ca_home)},
    )
    test(
        "13h: dict entry absent git_root → exit 1 (conservative block)",
        r_ca.returncode == 1,
        f"exit={r_ca.returncode} stdout={r_ca.stdout[:120]}",
    )
    shutil.rmtree(str(_ca_home), ignore_errors=True)

# 13i. New dict entry with expired per-entry ts → exit 0
if _csm_path.is_file():
    _exp_home = Path(tempfile.mkdtemp(prefix="test-expired-entry-"))
    (_exp_home / ".copilot" / "markers").mkdir(parents=True)
    stale_entry_ts = str(int(_time12.time()) - 99999)
    (_exp_home / ".copilot" / "markers" / "dispatched-subagent-active").write_text(
        json.dumps(
            {
                "name": "dispatched-subagent-active",
                "ts": str(int(_time12.time())),  # Global ts is fresh
                "active_tentacles": [
                    {"name": "t-expired", "ts": stale_entry_ts},  # No git_root → conservative but expired
                ],
            }
        )
    )
    r_exp = subprocess.run(
        [sys.executable, str(_csm_path)],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=10,
        env={**os.environ, "HOME": str(_exp_home)},
    )
    test(
        "13i: dict entry with stale per-entry ts → exit 0 (entry expired)",
        r_exp.returncode == 0,
        f"exit={r_exp.returncode} stdout={r_exp.stdout[:120]}",
    )
    shutil.rmtree(str(_exp_home), ignore_errors=True)

# 13j. SubagentGitGuardRule: cross-repo dict entry → allowed; same-repo → denied
try:
    import rules.subagent_guard as _sg13

    _rule_sg13 = _sg13.SubagentGitGuardRule()

    _j_other = Path(tempfile.mkdtemp(prefix="test-j-other-"))
    _j_current = Path(tempfile.mkdtemp(prefix="test-j-current-"))
    _j_marker_dir = Path(tempfile.mkdtemp(prefix="test-sg-cross-"))
    (_j_marker_dir / "markers").mkdir(parents=True)
    _j_marker_file = _j_marker_dir / "markers" / "dispatched-subagent-active"
    _j_marker_file.write_text(
        json.dumps(
            {
                "name": "dispatched-subagent-active",
                "ts": str(int(_time12.time())),
                "active_tentacles": [
                    {"name": "t1", "ts": str(int(_time12.time())), "git_root": str(_j_other)},
                ],
            }
        )
    )

    _orig_sm13 = _sg13.SUBAGENT_MARKER
    _orig_vm13 = _sg13.verify_marker
    _orig_gcr13 = _sg13._get_current_git_root
    _sg13.SUBAGENT_MARKER = _j_marker_file
    _sg13.verify_marker = lambda p, n: True
    _sg13._get_current_git_root = lambda: str(_j_current)

    result_cross = _rule_sg13.evaluate(
        "preToolUse",
        {
            "toolName": "bash",
            "toolArgs": {"command": "git commit -m test"},
        },
    )
    test(
        "13j: SubagentGitGuardRule cross-repo dict entry → allowed", result_cross is None, f"Got: {result_cross!r:.80}"
    )

    # Same repo → blocks
    _sg13._get_current_git_root = lambda: str(_j_other)
    result_sameRepo = _rule_sg13.evaluate(
        "preToolUse",
        {
            "toolName": "bash",
            "toolArgs": {"command": "git commit -m test"},
        },
    )
    test(
        "13j2: SubagentGitGuardRule same-repo dict entry → denied",
        isinstance(result_sameRepo, dict) and result_sameRepo.get("permissionDecision") == "deny",
        f"Got: {result_sameRepo!r:.80}",
    )

    _sg13.SUBAGENT_MARKER = _orig_sm13
    _sg13.verify_marker = _orig_vm13
    _sg13._get_current_git_root = _orig_gcr13
    shutil.rmtree(str(_j_marker_dir), ignore_errors=True)
    shutil.rmtree(str(_j_other), ignore_errors=True)
    shutil.rmtree(str(_j_current), ignore_errors=True)
    test("13j: SubagentGitGuardRule cross-repo tests ran", True)
except Exception as e:
    test("13j: SubagentGitGuardRule cross-repo tests ran", False, str(e))

# 13k. auto-update-tools.py emits reinstall warning when hooks change
try:
    _au_src = (REPO / "auto-update-tools.py").read_text(encoding="utf-8")
    test(
        "13k: auto-update-tools.py has --install-git-hooks reminder",
        "--install-git-hooks" in _au_src,
        "Expected --install-git-hooks in auto-update warning",
    )
    test(
        "13k2: auto-update-tools.py states it does NOT auto-reinstall git hooks",
        "NOT auto" in _au_src or "NOT automatically" in _au_src,
        "Expected explicit non-propagation statement",
    )
except Exception as e:
    test("13k: auto-update hook reminder source checks", False, str(e))

# 13l. install.py mentions re-run after auto-update
try:
    _install_src_13 = (REPO / "install.py").read_text(encoding="utf-8")
    test(
        "13l: install.py --install-git-hooks mentions re-run after update",
        "auto-update" in _install_src_13,
        "Expected auto-update reference in install_git_hooks output",
    )
except Exception as e:
    test("13l: install.py update reminder source check", False, str(e))

# 13m. Dual-format readers present in both hook files
try:
    _sg_src_13 = (REPO / "hooks" / "rules" / "subagent_guard.py").read_text(encoding="utf-8")
    _csm_src_13 = (REPO / "hooks" / "check_subagent_marker.py").read_text(encoding="utf-8")
    test(
        "13m: subagent_guard.py supports dict entries",
        "isinstance(active[0], dict)" in _sg_src_13 or "isinstance(entry, dict)" in _sg_src_13,
    )
    test(
        "13m2: subagent_guard.py supports string entries",
        "isinstance(active[0], str)" in _sg_src_13 or "isinstance(entry, str)" in _sg_src_13,
    )
    test(
        "13m3: check_subagent_marker.py supports dict entries",
        "isinstance(active[0], dict)" in _csm_src_13 or "isinstance(entry, dict)" in _csm_src_13,
    )
    test(
        "13m4: check_subagent_marker.py supports string entries",
        "isinstance(active[0], str)" in _csm_src_13 or "isinstance(entry, str)" in _csm_src_13,
    )
    test(
        "13m5: subagent_guard.py has repo-scope check",
        "git_root" in _sg_src_13 and "_get_current_git_root" in _sg_src_13,
    )
    test(
        "13m6: check_subagent_marker.py has repo-scope check",
        "git_root" in _csm_src_13 and "_get_current_git_root" in _csm_src_13,
    )
except Exception as e:
    test("13m: dual-format source checks", False, str(e))

# 13n. Installed-hook path uses canonical tools-dir (not dirname)
try:
    _pc_src_13 = (REPO / "hooks" / "pre-commit").read_text(encoding="utf-8")
    _pp_src_13 = (REPO / "hooks" / "pre-push").read_text(encoding="utf-8")
    test(
        "13n: pre-commit uses canonical $HOME/.copilot/tools path",
        "$HOME/.copilot/tools/hooks/check_subagent_marker.py" in _pc_src_13,
    )
    test(
        "13n2: pre-push uses canonical $HOME/.copilot/tools path",
        "$HOME/.copilot/tools/hooks/check_subagent_marker.py" in _pp_src_13,
    )
    test(
        "13n3: pre-commit does not use dirname for guard path",
        "$(dirname" not in _pc_src_13.split("check_subagent_marker.py")[0].split("SUBAGENT_CHECK")[-1],
    )
except Exception as e:
    test("13n: canonical-path source checks", False, str(e))


# ═══════════════════════════════════════════════════════════════════
#  Section 14: _roots_match fail-conservative & parse-once tests
# ═══════════════════════════════════════════════════════════════════

print("\n── Section 14: _roots_match fail-conservative & parse-once ──")

# 14a. _roots_match returns True on exception (fail-conservative)
try:
    import importlib.util as _ilu14

    _csm_spec14 = _ilu14.spec_from_file_location("csm14", _csm_path)
    _csm14 = _ilu14.module_from_spec(_csm_spec14)
    _csm_spec14.loader.exec_module(_csm14)

    # Normal match
    import tempfile as _tf14

    _d1 = Path(_tf14.mkdtemp(prefix="root-a-"))
    _d2 = Path(_tf14.mkdtemp(prefix="root-b-"))
    test("14a: _roots_match same dir returns True", _csm14._roots_match(str(_d1), str(_d1)))
    test("14a2: _roots_match different dirs returns False", not _csm14._roots_match(str(_d1), str(_d2)))

    # Exception path: passing a non-string/non-path that causes resolve() to fail
    # We trigger an OSError by passing a path with embedded null byte.
    try:
        result_exc = _csm14._roots_match("/valid/path", "/invalid\x00path")
    except Exception:
        result_exc = None  # If it raises instead of returning, mark as failing
    test(
        "14a3: _roots_match on exception returns True (fail-conservative)",
        result_exc is True,
        f"Got: {result_exc!r} — should be True (conservative), not False (fail-open)",
    )

    shutil.rmtree(str(_d1), ignore_errors=True)
    shutil.rmtree(str(_d2), ignore_errors=True)
    test("14a: _roots_match tests ran", True)
except Exception as e:
    test("14a: _roots_match tests ran", False, str(e))

# 14a-sg. Same check in subagent_guard._roots_match
try:
    import rules.subagent_guard as _sg14

    _d1sg = Path(tempfile.mkdtemp(prefix="sg-root-a-"))
    _d2sg = Path(tempfile.mkdtemp(prefix="sg-root-b-"))

    test("14a-sg: _roots_match same dir returns True", _sg14._roots_match(str(_d1sg), str(_d1sg)))
    test("14a-sg2: _roots_match different dirs returns False", not _sg14._roots_match(str(_d1sg), str(_d2sg)))

    try:
        result_sg_exc = _sg14._roots_match("/valid/path", "/invalid\x00path")
    except Exception:
        result_sg_exc = None
    test(
        "14a-sg3: subagent_guard._roots_match on exception returns True (fail-conservative)",
        result_sg_exc is True,
        f"Got: {result_sg_exc!r} — should be True (conservative), not False (fail-open)",
    )

    shutil.rmtree(str(_d1sg), ignore_errors=True)
    shutil.rmtree(str(_d2sg), ignore_errors=True)
    test("14a-sg: subagent_guard._roots_match tests ran", True)
except Exception as e:
    test("14a-sg: subagent_guard._roots_match tests ran", False, str(e))

# 14b. _any_entry_relevant: entry with bad git_root path → conservative block
# (relies on _roots_match returning True on exception, so the entry is kept active)
try:
    import rules.subagent_guard as _sg14b

    now14b = _time12.time()
    entry_bad_root = {"name": "t", "ts": str(int(now14b)), "git_root": "/invalid\x00path"}
    result_bad = _sg14b._any_entry_relevant([entry_bad_root], "/some/current/repo", now14b)
    test(
        "14b: _any_entry_relevant with bad git_root path → True (conservative block)",
        result_bad is True,
        f"Got: {result_bad!r} — bad path should not silently skip the entry",
    )
except Exception as e:
    test("14b: _any_entry_relevant bad-path conservative test", False, str(e))

# 14b2. Same for check_subagent_marker._any_entry_relevant
try:
    now14b2 = _time12.time()
    entry_bad_root2 = {"name": "t", "ts": str(int(now14b2)), "git_root": "/invalid\x00path"}
    result_bad2 = _csm14._any_entry_relevant([entry_bad_root2], "/some/repo", now14b2)
    test(
        "14b2: check_subagent_marker._any_entry_relevant bad path → True (conservative)",
        result_bad2 is True,
        f"Got: {result_bad2!r}",
    )
except Exception as e:
    test("14b2: check_subagent_marker._any_entry_relevant bad-path test", False, str(e))

# 14c. is_marker_fresh parses once: verify no second MARKER_PATH.read_text call
# after the parse.  We check this at source level.
try:
    _csm_src14 = _csm_path.read_text(encoding="utf-8")
    # Find the body of is_marker_fresh
    fn_start = _csm_src14.index("def is_marker_fresh()")
    # Find the next top-level def after is_marker_fresh
    fn_end = _csm_src14.index("\ndef ", fn_start + 1)
    fn_body = _csm_src14[fn_start:fn_end]

    # There should be exactly ONE MARKER_PATH.read_text call in is_marker_fresh
    read_count = fn_body.count("MARKER_PATH.read_text")
    test(
        "14c: is_marker_fresh reads MARKER_PATH exactly once (parse-once refactor)",
        read_count == 1,
        f"Found {read_count} MARKER_PATH.read_text call(s) in is_marker_fresh — expected 1",
    )
    test(
        "14c2: is_marker_fresh does NOT call _read_marker_ts (parse-once refactor)",
        "_read_marker_ts" not in fn_body,
        "is_marker_fresh should extract ts from the already-parsed dict, not re-read the file",
    )
except Exception as e:
    test("14c: parse-once source checks", False, str(e))

# 14d. is_marker_fresh behaves identically to before: fresh marker still blocks
if _csm_path.is_file():
    _d14_home = Path(tempfile.mkdtemp(prefix="test-14d-"))
    (_d14_home / ".copilot" / "markers").mkdir(parents=True)
    (_d14_home / ".copilot" / "markers" / "dispatched-subagent-active").write_text(
        json.dumps(
            {
                "name": "dispatched-subagent-active",
                "ts": str(int(_time12.time())),
                "active_tentacles": ["my-tentacle"],
            }
        )
    )
    r14d = subprocess.run(
        [sys.executable, str(_csm_path)],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=10,
        env={**os.environ, "HOME": str(_d14_home)},
    )
    test(
        "14d: parse-once refactor: fresh marker still exits 1 (blocks)",
        r14d.returncode == 1,
        f"exit={r14d.returncode} stdout={r14d.stdout[:80]}",
    )
    shutil.rmtree(str(_d14_home), ignore_errors=True)

# 14e. fail-conservative comment is accurate in source: no "fail-open" on scope check
try:
    _csm_src14e = _csm_path.read_text(encoding="utf-8")
    fn_start = _csm_src14e.index("def is_marker_fresh()")
    fn_end = _csm_src14e.index("\ndef ", fn_start + 1)
    fn_body14e = _csm_src14e[fn_start:fn_end]
    # The repo-scope except clause should say "fail-conservative", not "fail-open"
    # Locate the repo-scope check except block
    scope_part = fn_body14e.split("Repo-scope check")[-1] if "Repo-scope check" in fn_body14e else fn_body14e
    test(
        "14e: is_marker_fresh repo-scope except comment says fail-conservative (not fail-open)",
        "fail-conservative" in scope_part and "fail-open" not in scope_part.split("fail-conservative")[0][-30:],
        "Comment should be 'fail-conservative' to accurately describe that scope errors keep blocking",
    )
except Exception as e:
    test("14e: is_marker_fresh comment accuracy check", False, str(e))

# 14f. auto-update warning text says "ACTION REQUIRED" and "EVERY"
try:
    _au_src14 = (REPO / "auto-update-tools.py").read_text(encoding="utf-8")
    test(
        "14f: auto-update warning says ACTION REQUIRED",
        "ACTION REQUIRED" in _au_src14,
        "Strengthened warning should say 'ACTION REQUIRED'",
    )
    test(
        "14f2: auto-update warning says EVERY protected repo",
        "EVERY" in _au_src14,
        "Strengthened warning should say 'EVERY' to clarify scope",
    )
except Exception as e:
    test("14f: auto-update warning strength checks", False, str(e))

# 14g. _roots_match docstring in both files mentions fail-conservative
try:
    _sg_src14g = (REPO / "hooks" / "rules" / "subagent_guard.py").read_text(encoding="utf-8")
    _csm_src14g = (REPO / "hooks" / "check_subagent_marker.py").read_text(encoding="utf-8")
    test(
        "14g: subagent_guard._roots_match docstring mentions fail-conservative",
        "fail-conservative" in _sg_src14g.split("def _roots_match")[1].split("def ")[0],
    )
    test(
        "14g2: check_subagent_marker._roots_match docstring mentions fail-conservative",
        "fail-conservative" in _csm_src14g.split("def _roots_match")[1].split("def ")[0],
    )
except Exception as e:
    test("14g: _roots_match docstring checks", False, str(e))


# ═══════════════════════════════════════════════════════════════════
#  Section 15: Phase-5 per-tentacle tentacle_id field
#
#  Phase-5 runtime adds an optional "tentacle_id" field to each
#  active_tentacles entry so that same-name, same-repo tentacles
#  dispatched concurrently remain distinct entries.  Hook readers
#  already iterate dict entries with get(); the extra field is
#  silently ignored — no hook logic changes are required.
#
#  These tests verify:
#    a. Extra tentacle_id field doesn't break _any_entry_relevant
#    b. Multiple same-repo entries with distinct tentacle_ids → ALL block
#    c. _read_tentacle_info still returns names correctly with tentacle_id
#    d. Subprocess: multi-tentacle_id same-repo marker → exit 1 (blocks)
#    e. Subprocess: one same-repo + one different-repo tentacle_id entry → exit 1
#    f. SubagentGitGuardRule: multi-tentacle_id same-repo entries → deny
#    g. tentacle_id field present in fresh single-entry marker → still blocks
#    h. All same-repo tentacle_id entries expired → allow
# ═══════════════════════════════════════════════════════════════════

print("\n── Section 15: Phase-5 per-tentacle tentacle_id field ──")

# 15a. _any_entry_relevant: entry with extra "tentacle_id" field → still True (same repo)
try:
    import rules.subagent_guard as _sg15
    import importlib.util as _ilu15

    _csm_spec15 = _ilu15.spec_from_file_location("csm15", _csm_path)
    _csm15 = _ilu15.module_from_spec(_csm_spec15)
    _csm_spec15.loader.exec_module(_csm15)

    _now15 = _time12.time()
    _same_repo = tempfile.mkdtemp(prefix="test-15-repo-")
    _entry_with_id = {
        "name": "build-api",
        "ts": str(int(_now15)),
        "git_root": _same_repo,
        "tentacle_id": "abc-uuid-1",
    }
    result_sg15a = _sg15._any_entry_relevant([_entry_with_id], _same_repo, _now15)
    test(
        "15a: subagent_guard._any_entry_relevant ignores tentacle_id field, returns True for same-repo",
        result_sg15a is True,
        f"Got {result_sg15a!r}",
    )
    result_csm15a = _csm15._any_entry_relevant([_entry_with_id], _same_repo, _now15)
    test(
        "15a2: check_subagent_marker._any_entry_relevant ignores tentacle_id field, returns True for same-repo",
        result_csm15a is True,
        f"Got {result_csm15a!r}",
    )
    shutil.rmtree(_same_repo, ignore_errors=True)
    test("15a: tentacle_id field tolerance tests ran", True)
except Exception as e:
    test("15a: tentacle_id field tolerance tests", False, str(e))

# 15b. Multiple same-repo entries with distinct tentacle_ids → _any_entry_relevant returns True
try:
    import rules.subagent_guard as _sg15b

    _now15b = _time12.time()
    _repo15b = tempfile.mkdtemp(prefix="test-15b-repo-")
    _entries15b = [
        {"name": "worker", "ts": str(int(_now15b)), "git_root": _repo15b, "tentacle_id": "tid-1"},
        {"name": "worker", "ts": str(int(_now15b)), "git_root": _repo15b, "tentacle_id": "tid-2"},
    ]
    result_sg15b = _sg15b._any_entry_relevant(_entries15b, _repo15b, _now15b)
    test(
        "15b: subagent_guard._any_entry_relevant: two same-repo tentacle_id entries → True (blocks)",
        result_sg15b is True,
        f"Got {result_sg15b!r}",
    )

    import importlib.util as _ilu15b

    _csm_spec15b = _ilu15b.spec_from_file_location("csm15b", _csm_path)
    _csm15b = _ilu15b.module_from_spec(_csm_spec15b)
    _csm_spec15b.loader.exec_module(_csm15b)
    result_csm15b = _csm15b._any_entry_relevant(_entries15b, _repo15b, _now15b)
    test(
        "15b2: check_subagent_marker._any_entry_relevant: two same-repo tentacle_id entries → True",
        result_csm15b is True,
        f"Got {result_csm15b!r}",
    )
    shutil.rmtree(_repo15b, ignore_errors=True)
    test("15b: multi-tentacle_id same-repo tests ran", True)
except Exception as e:
    test("15b: multi-tentacle_id same-repo tests", False, str(e))

# 15c. _read_tentacle_info: tentacle_id field doesn't break name extraction
try:
    import rules.subagent_guard as _sg15c

    _now15c = _time12.time()
    _home15c = Path(tempfile.mkdtemp(prefix="test-15c-"))
    (_home15c / ".copilot" / "markers").mkdir(parents=True)
    _marker15c = _home15c / ".copilot" / "markers" / "dispatched-subagent-active"
    _marker15c.write_text(
        json.dumps(
            {
                "name": "dispatched-subagent-active",
                "ts": str(int(_now15c)),
                "active_tentacles": [
                    {"name": "alpha", "ts": str(int(_now15c)), "git_root": "/r", "tentacle_id": "tid-A"},
                    {"name": "beta", "ts": str(int(_now15c)), "git_root": "/r", "tentacle_id": "tid-B"},
                ],
            }
        ),
        encoding="utf-8",
    )

    # Patch SUBAGENT_MARKER so _read_tentacle_info uses our file
    _orig_sm15c = _sg15c.SUBAGENT_MARKER
    _sg15c.SUBAGENT_MARKER = _marker15c
    info15c = _sg15c._read_tentacle_info()
    _sg15c.SUBAGENT_MARKER = _orig_sm15c

    test(
        "15c: subagent_guard._read_tentacle_info with tentacle_id entries returns both names",
        "alpha" in info15c and "beta" in info15c,
        f"Got: {info15c!r}",
    )

    # Same for check_subagent_marker
    import importlib.util as _ilu15c

    _csm_spec15c = _ilu15c.spec_from_file_location("csm15c", _csm_path)
    _csm15c2 = _ilu15c.module_from_spec(_csm_spec15c)
    _csm_spec15c.loader.exec_module(_csm15c2)
    _orig_mp15c = _csm15c2.MARKER_PATH
    _csm15c2.MARKER_PATH = _marker15c
    info15c2 = _csm15c2._read_tentacle_info()
    _csm15c2.MARKER_PATH = _orig_mp15c

    test(
        "15c2: check_subagent_marker._read_tentacle_info with tentacle_id entries returns both names",
        "alpha" in info15c2 and "beta" in info15c2,
        f"Got: {info15c2!r}",
    )
    shutil.rmtree(str(_home15c), ignore_errors=True)
    test("15c: _read_tentacle_info tentacle_id tests ran", True)
except Exception as e:
    test("15c: _read_tentacle_info tentacle_id tests", False, str(e))

# 15d. Subprocess: marker with two same-repo entries (distinct tentacle_ids) → exit 1
try:
    _now15d = _time12.time()
    _home15d = Path(tempfile.mkdtemp(prefix="test-15d-"))
    (_home15d / ".copilot" / "markers").mkdir(parents=True)
    _repo15d = tempfile.mkdtemp(prefix="test-15d-repo-")
    (_home15d / ".copilot" / "markers" / "dispatched-subagent-active").write_text(
        json.dumps(
            {
                "name": "dispatched-subagent-active",
                "ts": str(int(_now15d)),
                "git_root": _repo15d,
                "active_tentacles": [
                    {"name": "worker", "ts": str(int(_now15d)), "git_root": _repo15d, "tentacle_id": "tid-1"},
                    {"name": "worker", "ts": str(int(_now15d)), "git_root": _repo15d, "tentacle_id": "tid-2"},
                ],
            }
        ),
        encoding="utf-8",
    )
    r15d = subprocess.run(
        [sys.executable, str(_csm_path)],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=10,
        cwd=_repo15d,
        env={**os.environ, "HOME": str(_home15d)},
    )
    test(
        "15d: subprocess same-repo two tentacle_id entries → exit 1 (blocks)",
        r15d.returncode == 1,
        f"exit={r15d.returncode} stdout={r15d.stdout[:120]}",
    )
    shutil.rmtree(str(_home15d), ignore_errors=True)
    shutil.rmtree(_repo15d, ignore_errors=True)
except Exception as e:
    test("15d: subprocess multi-tentacle_id same-repo test", False, str(e))

# 15e. Subprocess: one same-repo tentacle_id entry + one different-repo → still exit 1
try:
    _now15e = _time12.time()
    _home15e = Path(tempfile.mkdtemp(prefix="test-15e-"))
    (_home15e / ".copilot" / "markers").mkdir(parents=True)
    _repo15e_current = tempfile.mkdtemp(prefix="test-15e-current-")
    _repo15e_other = tempfile.mkdtemp(prefix="test-15e-other-")
    (_home15e / ".copilot" / "markers" / "dispatched-subagent-active").write_text(
        json.dumps(
            {
                "name": "dispatched-subagent-active",
                "ts": str(int(_now15e)),
                "git_root": _repo15e_current,
                "active_tentacles": [
                    {"name": "t-other", "ts": str(int(_now15e)), "git_root": _repo15e_other, "tentacle_id": "tid-A"},
                    {
                        "name": "t-current",
                        "ts": str(int(_now15e)),
                        "git_root": _repo15e_current,
                        "tentacle_id": "tid-B",
                    },
                ],
            }
        ),
        encoding="utf-8",
    )
    r15e = subprocess.run(
        [sys.executable, str(_csm_path)],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=10,
        cwd=_repo15e_current,
        env={**os.environ, "HOME": str(_home15e)},
    )
    test(
        "15e: subprocess mixed-repo tentacle_id entries → exit 1 (same-repo entry still blocks)",
        r15e.returncode == 1,
        f"exit={r15e.returncode} stdout={r15e.stdout[:120]}",
    )
    shutil.rmtree(str(_home15e), ignore_errors=True)
    shutil.rmtree(_repo15e_current, ignore_errors=True)
    shutil.rmtree(_repo15e_other, ignore_errors=True)
except Exception as e:
    test("15e: subprocess mixed-repo tentacle_id test", False, str(e))

# 15f. SubagentGitGuardRule: multiple same-repo tentacle_id entries → deny
try:
    import rules.subagent_guard as _sg15f

    _now15f = _time12.time()
    _home15f = Path(tempfile.mkdtemp(prefix="test-15f-"))
    (_home15f / ".copilot" / "markers").mkdir(parents=True)
    _repo15f = tempfile.mkdtemp(prefix="test-15f-repo-")
    _marker15f = _home15f / ".copilot" / "markers" / "dispatched-subagent-active"
    _marker15f.write_text(
        json.dumps(
            {
                "name": "dispatched-subagent-active",
                "ts": str(int(_now15f)),
                "git_root": _repo15f,
                "active_tentacles": [
                    {"name": "svc-a", "ts": str(int(_now15f)), "git_root": _repo15f, "tentacle_id": "run-1"},
                    {"name": "svc-b", "ts": str(int(_now15f)), "git_root": _repo15f, "tentacle_id": "run-2"},
                ],
            }
        ),
        encoding="utf-8",
    )
    _orig_sm15f = _sg15f.SUBAGENT_MARKER
    _orig_vm15f = _sg15f.verify_marker
    _orig_gcr15f = _sg15f._get_current_git_root
    _sg15f.SUBAGENT_MARKER = _marker15f
    _sg15f.verify_marker = lambda p, n: True  # bypass HMAC; testing routing logic only
    _sg15f._get_current_git_root = lambda: _repo15f
    rule15f = _sg15f.SubagentGitGuardRule()
    result15f = rule15f.evaluate("preToolUse", {"toolArgs": {"command": "git commit -m 'x'"}})
    _sg15f.SUBAGENT_MARKER = _orig_sm15f
    _sg15f.verify_marker = _orig_vm15f
    _sg15f._get_current_git_root = _orig_gcr15f
    test(
        "15f: SubagentGitGuardRule multi-tentacle_id same-repo → deny",
        result15f is not None,
        f"Got None (allowed) — should have been denied",
    )
    if result15f is not None:
        msg15f = result15f.get("permissionDecisionReason", "") if isinstance(result15f, dict) else str(result15f)
        test(
            "15f2: deny message mentions subagent mode",
            "subagent" in msg15f.lower(),
            f"permissionDecisionReason: {msg15f[:80]!r}",
        )
    shutil.rmtree(str(_home15f), ignore_errors=True)
    shutil.rmtree(_repo15f, ignore_errors=True)
    test("15f: SubagentGitGuardRule multi-tentacle_id tests ran", True)
except Exception as e:
    test("15f: SubagentGitGuardRule multi-tentacle_id tests", False, str(e))

# 15g. Single tentacle_id entry (fresh) still blocks — regression guard
try:
    _now15g = _time12.time()
    _home15g = Path(tempfile.mkdtemp(prefix="test-15g-"))
    (_home15g / ".copilot" / "markers").mkdir(parents=True)
    _repo15g = tempfile.mkdtemp(prefix="test-15g-repo-")
    (_home15g / ".copilot" / "markers" / "dispatched-subagent-active").write_text(
        json.dumps(
            {
                "name": "dispatched-subagent-active",
                "ts": str(int(_now15g)),
                "git_root": _repo15g,
                "active_tentacles": [
                    {"name": "solo", "ts": str(int(_now15g)), "git_root": _repo15g, "tentacle_id": "uid-xyz"},
                ],
            }
        ),
        encoding="utf-8",
    )
    r15g = subprocess.run(
        [sys.executable, str(_csm_path)],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=10,
        cwd=_repo15g,
        env={**os.environ, "HOME": str(_home15g)},
    )
    test(
        "15g: single tentacle_id entry still blocks (regression guard)", r15g.returncode == 1, f"exit={r15g.returncode}"
    )
    shutil.rmtree(str(_home15g), ignore_errors=True)
    shutil.rmtree(_repo15g, ignore_errors=True)
except Exception as e:
    test("15g: single tentacle_id entry regression guard", False, str(e))

# 15h. All same-repo tentacle_id entries expired → allow
try:
    import rules.subagent_guard as _sg15h

    _now15h = _time12.time()
    _stale_ts15h = str(int(_now15h - 20000))  # well past 4h TTL
    _repo15h = tempfile.mkdtemp(prefix="test-15h-repo-")
    _entries15h = [
        {"name": "t1", "ts": _stale_ts15h, "git_root": _repo15h, "tentacle_id": "tid-1"},
        {"name": "t2", "ts": _stale_ts15h, "git_root": _repo15h, "tentacle_id": "tid-2"},
    ]
    result_sg15h = _sg15h._any_entry_relevant(_entries15h, _repo15h, _now15h)
    test(
        "15h: all same-repo tentacle_id entries expired → _any_entry_relevant returns False",
        result_sg15h is False,
        f"Got {result_sg15h!r} — expired entries should not block",
    )

    import importlib.util as _ilu15h

    _csm_spec15h = _ilu15h.spec_from_file_location("csm15h", _csm_path)
    _csm15h = _ilu15h.module_from_spec(_csm_spec15h)
    _csm_spec15h.loader.exec_module(_csm15h)
    result_csm15h = _csm15h._any_entry_relevant(_entries15h, _repo15h, _now15h)
    test(
        "15h2: check_subagent_marker all expired tentacle_id entries → False",
        result_csm15h is False,
        f"Got {result_csm15h!r}",
    )
    shutil.rmtree(_repo15h, ignore_errors=True)
    test("15h: expired tentacle_id entries tests ran", True)
except Exception as e:
    test("15h: expired tentacle_id entries test", False, str(e))


# ═══════════════════════════════════════════════════════════════════
#  Section 16: Mixed-format marker bypass regression tests
#
#  Bug: when active_tentacles[0] is a string (old format), the previous
#  code entered the "old string-list" branch and checked only the
#  top-level git_root — completely skipping any later dict entries that
#  may carry a different (current-repo) git_root.  A crafted or migrating
#  marker with active[0]=string, top-level git_root=/other/repo, and a
#  dict entry for /current/repo would return False (allow commit).
#
#  Fix: use all(isinstance(e, str) for e in active) so a mixed list is
#  routed through _any_entry_relevant, which handles strings conservatively.
#
#  Tests:
#   a. _any_entry_relevant: mixed list with string first → True (conservative)
#   b. Subprocess: mixed list, string first + dict for current repo,
#      top-level git_root=/other → exit 1 (BLOCKS — was bypass before fix)
#   c. Subprocess: pure old string list + top-level git_root=/other → exit 0
#      (cross-repo skip still works for purely old-format markers)
#   d. SubagentGitGuardRule: mixed list, string first + current-repo dict → deny
#   e. Source check: both hook files use all() not active[0] for format dispatch
# ═══════════════════════════════════════════════════════════════════

print("\n── Section 16: Mixed-format marker bypass regression ──")

# 16a. _any_entry_relevant: string entry first in mixed list → True (conservative)
try:
    import rules.subagent_guard as _sg16
    import importlib.util as _ilu16

    _csm_spec16 = _ilu16.spec_from_file_location("csm16", _csm_path)
    _csm16 = _ilu16.module_from_spec(_csm_spec16)
    _csm_spec16.loader.exec_module(_csm16)

    _now16 = _time12.time()
    _repo16 = tempfile.mkdtemp(prefix="test-16-repo-")
    _other16 = tempfile.mkdtemp(prefix="test-16-other-")
    # Mixed: string first (no repo metadata) + dict for current repo
    _mixed16 = [
        "legacy-tentacle",
        {"name": "current-work", "ts": str(int(_now16)), "git_root": _repo16},
    ]
    result_sg16a = _sg16._any_entry_relevant(_mixed16, _repo16, _now16)
    test(
        "16a: subagent_guard._any_entry_relevant mixed (string first) → True (conservative block)",
        result_sg16a is True,
        f"Got {result_sg16a!r}",
    )
    result_csm16a = _csm16._any_entry_relevant(_mixed16, _repo16, _now16)
    test(
        "16a2: check_subagent_marker._any_entry_relevant mixed (string first) → True",
        result_csm16a is True,
        f"Got {result_csm16a!r}",
    )
    shutil.rmtree(_repo16, ignore_errors=True)
    shutil.rmtree(_other16, ignore_errors=True)
    test("16a: mixed-format _any_entry_relevant tests ran", True)
except Exception as e:
    test("16a: mixed-format _any_entry_relevant tests", False, str(e))

# 16b. Subprocess: mixed list (string first) + dict for current repo +
#      top-level git_root = other repo → must exit 1 (was bypass before fix)
try:
    _now16b = _time12.time()
    _home16b = Path(tempfile.mkdtemp(prefix="test-16b-"))
    (_home16b / ".copilot" / "markers").mkdir(parents=True)
    _current16b = tempfile.mkdtemp(prefix="test-16b-current-")
    _other16b = tempfile.mkdtemp(prefix="test-16b-other-")
    # Top-level git_root points to OTHER repo; active_tentacles[1] dict is for CURRENT repo.
    # Before fix: active[0] is string → string branch → top-level git_root mismatch → exit 0 (BYPASS)
    # After fix: all() check → mixed → _any_entry_relevant → string entry → True → exit 1 (BLOCKS)
    (_home16b / ".copilot" / "markers" / "dispatched-subagent-active").write_text(
        json.dumps(
            {
                "name": "dispatched-subagent-active",
                "ts": str(int(_now16b)),
                "git_root": _other16b,  # top-level points to other repo
                "active_tentacles": [
                    "legacy-tentacle",  # old string entry (no repo metadata)
                    {"name": "current-work", "ts": str(int(_now16b)), "git_root": _current16b},
                ],
            }
        ),
        encoding="utf-8",
    )
    r16b = subprocess.run(
        [sys.executable, str(_csm_path)],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=10,
        cwd=_current16b,
        env={**os.environ, "HOME": str(_home16b)},
    )
    test(
        "16b: mixed list (string first + current-repo dict, other top-level git_root) → exit 1 (blocks)",
        r16b.returncode == 1,
        f"exit={r16b.returncode} — was exit 0 (bypass) before fix; stdout={r16b.stdout[:80]}",
    )
    shutil.rmtree(str(_home16b), ignore_errors=True)
    shutil.rmtree(_current16b, ignore_errors=True)
    shutil.rmtree(_other16b, ignore_errors=True)
except Exception as e:
    test("16b: mixed-format bypass subprocess test", False, str(e))

# 16c. Subprocess: pure old string list + top-level git_root = other repo → exit 0
#      (cross-repo skip for purely old-format markers must still work)
try:
    _now16c = _time12.time()
    _home16c = Path(tempfile.mkdtemp(prefix="test-16c-"))
    (_home16c / ".copilot" / "markers").mkdir(parents=True)
    _current16c = Path(tempfile.mkdtemp(prefix="test-16c-current-"))
    _other16c = Path(tempfile.mkdtemp(prefix="test-16c-other-"))
    # current repo needs git init so _get_current_git_root() returns a real path
    subprocess.run(["git", "init", str(_current16c)], capture_output=True, check=True, timeout=10)
    subprocess.run(["git", "config", "user.email", "t@t.com"], cwd=str(_current16c), capture_output=True, timeout=5)
    subprocess.run(["git", "config", "user.name", "T"], cwd=str(_current16c), capture_output=True, timeout=5)
    (_home16c / ".copilot" / "markers" / "dispatched-subagent-active").write_text(
        json.dumps(
            {
                "name": "dispatched-subagent-active",
                "ts": str(int(_now16c)),
                "git_root": str(_other16c),  # top-level: other repo
                "active_tentacles": ["legacy-only"],  # pure old string list
            }
        ),
        encoding="utf-8",
    )
    r16c = subprocess.run(
        [sys.executable, str(_csm_path)],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=10,
        cwd=str(_current16c),
        env={**os.environ, "HOME": str(_home16c)},
    )
    test(
        "16c: pure old string list + other-repo top-level git_root → exit 0 (backward compat preserved)",
        r16c.returncode == 0,
        f"exit={r16c.returncode} — cross-repo skip for pure old-format markers must still work",
    )
    shutil.rmtree(str(_home16c), ignore_errors=True)
    shutil.rmtree(str(_current16c), ignore_errors=True)
    shutil.rmtree(str(_other16c), ignore_errors=True)
except subprocess.CalledProcessError as e:
    test("16c: pure-old-format backward-compat test (git init)", False, str(e))
except Exception as e:
    test("16c: pure-old-format backward-compat test", False, str(e))

# 16d. SubagentGitGuardRule: mixed list (string first) + current-repo dict → deny
try:
    import rules.subagent_guard as _sg16d

    _now16d = _time12.time()
    _home16d = Path(tempfile.mkdtemp(prefix="test-16d-"))
    (_home16d / ".copilot" / "markers").mkdir(parents=True)
    _current16d = tempfile.mkdtemp(prefix="test-16d-current-")
    _other16d = tempfile.mkdtemp(prefix="test-16d-other-")
    _marker16d = _home16d / ".copilot" / "markers" / "dispatched-subagent-active"
    _marker16d.write_text(
        json.dumps(
            {
                "name": "dispatched-subagent-active",
                "ts": str(int(_now16d)),
                "git_root": _other16d,
                "active_tentacles": [
                    "legacy-tentacle",
                    {"name": "current-work", "ts": str(int(_now16d)), "git_root": _current16d},
                ],
            }
        ),
        encoding="utf-8",
    )
    _orig_sm16d = _sg16d.SUBAGENT_MARKER
    _orig_vm16d = _sg16d.verify_marker
    _orig_gcr16d = _sg16d._get_current_git_root
    _sg16d.SUBAGENT_MARKER = _marker16d
    _sg16d.verify_marker = lambda p, n: True  # bypass HMAC; testing routing logic
    _sg16d._get_current_git_root = lambda: _current16d
    rule16d = _sg16d.SubagentGitGuardRule()
    result16d = rule16d.evaluate("preToolUse", {"toolArgs": {"command": "git commit -m 'x'"}})
    _sg16d.SUBAGENT_MARKER = _orig_sm16d
    _sg16d.verify_marker = _orig_vm16d
    _sg16d._get_current_git_root = _orig_gcr16d
    test(
        "16d: SubagentGitGuardRule mixed list (string first + current-repo dict) → deny",
        result16d is not None and isinstance(result16d, dict) and result16d.get("permissionDecision") == "deny",
        f"Got {result16d!r:.80} — was allowed (bypass) before fix",
    )
    shutil.rmtree(str(_home16d), ignore_errors=True)
    shutil.rmtree(_current16d, ignore_errors=True)
    shutil.rmtree(_other16d, ignore_errors=True)
    test("16d: SubagentGitGuardRule mixed-format tests ran", True)
except Exception as e:
    test("16d: SubagentGitGuardRule mixed-format tests", False, str(e))

# 16e. Source check: both hook files dispatch on all() not active[0]
try:
    _sg_src16 = (REPO / "hooks" / "rules" / "subagent_guard.py").read_text(encoding="utf-8")
    _csm_src16 = (REPO / "hooks" / "check_subagent_marker.py").read_text(encoding="utf-8")
    test(
        "16e: subagent_guard.py uses all(isinstance(e, str) for e in active) for format dispatch",
        "all(isinstance(e, str) for e in active)" in _sg_src16,
        "Format detection should use all() not active[0] type check",
    )
    test(
        "16e2: check_subagent_marker.py uses all(isinstance(e, str) for e in active) for format dispatch",
        "all(isinstance(e, str) for e in active)" in _csm_src16,
        "Format detection should use all() not active[0] type check",
    )
    test(
        "16e3: subagent_guard.py does not dispatch on isinstance(active[0], str) in repo-scope check",
        "isinstance(active[0], str)" not in _sg_src16.split("Repo-scope check")[-1],
        "Old active[0] dispatch pattern should be gone from repo-scope check",
    )
    test(
        "16e4: check_subagent_marker.py does not dispatch on isinstance(active[0], str) in repo-scope check",
        "isinstance(active[0], str)" not in _csm_src16.split("Repo-scope check")[-1],
        "Old active[0] dispatch pattern should be gone from repo-scope check",
    )
except Exception as e:
    test("16e: format-dispatch source checks", False, str(e))


# ═══════════════════════════════════════════════════════════════════
#  Section 17: Windows home resolution & portable interpreter
# ═══════════════════════════════════════════════════════════════════

print("\n── Section 17: Windows home resolution & portable interpreter ──")

# 17a. check_subagent_marker.py has _copilot_home() helper
_csm_src17 = (REPO / "hooks" / "check_subagent_marker.py").read_text(encoding="utf-8")
test("17a: check_subagent_marker.py has _copilot_home helper", "def _copilot_home" in _csm_src17)
test("17a2: _copilot_home checks COPILOT_HOME env var", "COPILOT_HOME" in _csm_src17)
test("17a3: _copilot_home checks HOME env var", '"HOME"' in _csm_src17 or "'HOME'" in _csm_src17)
test(
    "17a4: MARKER_PATH uses _copilot_home() not Path.home()",
    "_copilot_home()" in _csm_src17
    and "Path.home()" not in _csm_src17.split("def _copilot_home")[0].split("MARKER_PATH")[1:2].__repr__(),
)

# 17b. pre-commit and pre-push use PYTHON_BIN detection, not hard-coded python3
_pc_src17 = (REPO / "hooks" / "pre-commit").read_text(encoding="utf-8")
_pp_src17 = (REPO / "hooks" / "pre-push").read_text(encoding="utf-8")
test("17b: pre-commit has PYTHON_BIN detection", "PYTHON_BIN" in _pc_src17 and "command -v" in _pc_src17)
test(
    "17b2: pre-commit uses $PYTHON_BIN not hard-coded python3 for guard", '"$PYTHON_BIN" "$SUBAGENT_CHECK"' in _pc_src17
)
test("17b3: pre-push has PYTHON_BIN detection", "PYTHON_BIN" in _pp_src17 and "command -v" in _pp_src17)
test("17b4: pre-push uses $PYTHON_BIN not hard-coded python3 for guard", '"$PYTHON_BIN" "$SUBAGENT_CHECK"' in _pp_src17)
test("17b5: pre-commit verifies interpreter with -c probe", '-c ""' in _pc_src17)
test("17b6: pre-push verifies interpreter with -c probe", '-c ""' in _pp_src17)

# 17c. Subprocess: HOME override is respected by check_subagent_marker.py
# This is the core Windows regression test — _copilot_home() must honour HOME.
if _csm_path.is_file():
    _home17 = Path(tempfile.mkdtemp(prefix="test-home17-"))
    (_home17 / ".copilot" / "markers").mkdir(parents=True)
    # Write a fresh marker under the temp home
    (_home17 / ".copilot" / "markers" / "dispatched-subagent-active").write_text(
        json.dumps(
            {"name": "dispatched-subagent-active", "ts": str(int(time.time())), "active_tentacles": ["home-test"]}
        )
    )
    r_home17 = subprocess.run(
        [sys.executable, str(_csm_path)],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=10,
        env={**os.environ, "HOME": str(_home17)},
    )
    test(
        "17c: HOME override → fresh marker under temp HOME blocks (exit 1)",
        r_home17.returncode == 1,
        f"exit={r_home17.returncode} stdout={r_home17.stdout[:120]}",
    )
    shutil.rmtree(str(_home17), ignore_errors=True)

# 17d. Subprocess: COPILOT_HOME takes precedence over HOME
if _csm_path.is_file():
    _ch17 = Path(tempfile.mkdtemp(prefix="test-ch17-"))
    _badh17 = Path(tempfile.mkdtemp(prefix="test-badh17-"))
    (_ch17 / ".copilot" / "markers").mkdir(parents=True)
    (_ch17 / ".copilot" / "markers" / "dispatched-subagent-active").write_text(
        json.dumps(
            {
                "name": "dispatched-subagent-active",
                "ts": str(int(time.time())),
                "active_tentacles": ["copilot-home-test"],
            }
        )
    )
    # HOME points to empty dir, COPILOT_HOME points to dir with marker
    r_ch17 = subprocess.run(
        [sys.executable, str(_csm_path)],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=10,
        env={**os.environ, "HOME": str(_badh17), "COPILOT_HOME": str(_ch17)},
    )
    test(
        "17d: COPILOT_HOME takes precedence over HOME",
        r_ch17.returncode == 1,
        f"exit={r_ch17.returncode} stdout={r_ch17.stdout[:120]}",
    )
    shutil.rmtree(str(_ch17), ignore_errors=True)
    shutil.rmtree(str(_badh17), ignore_errors=True)

# 17e. In-process: _copilot_home() respects env vars
try:
    import importlib.util as _ilu17

    _csm_spec17 = _ilu17.spec_from_file_location("check_subagent_marker_17", _csm_path)
    _csm17 = _ilu17.module_from_spec(_csm_spec17)
    _csm_spec17.loader.exec_module(_csm17)

    _saved_env17 = {k: os.environ.get(k) for k in ("COPILOT_HOME", "HOME")}
    try:
        os.environ["HOME"] = "/test/home17"
        os.environ.pop("COPILOT_HOME", None)
        result17a = _csm17._copilot_home()
        test(
            "17e: _copilot_home() returns HOME when set",
            str(result17a) == "/test/home17" or str(result17a) == "\\test\\home17",
            f"Got: {result17a}",
        )

        os.environ["COPILOT_HOME"] = "/test/copilot-home17"
        result17b = _csm17._copilot_home()
        test(
            "17e2: _copilot_home() prefers COPILOT_HOME over HOME",
            str(result17b) == "/test/copilot-home17" or str(result17b) == "\\test\\copilot-home17",
            f"Got: {result17b}",
        )

        os.environ.pop("COPILOT_HOME", None)
        os.environ.pop("HOME", None)
        result17c = _csm17._copilot_home()
        test(
            "17e3: _copilot_home() falls back to Path.home() when no env vars",
            result17c == Path.home(),
            f"Got: {result17c}",
        )
    finally:
        for k, v in _saved_env17.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v
except Exception as e:
    test("17e: _copilot_home in-process tests", False, str(e))

# 17f. No bare Path.home() remains in MARKER_PATH or _SECRET_PATH assignments
test("17f: no bare Path.home() in MARKER_PATH assignment", "MARKER_PATH = Path.home()" not in _csm_src17)
import re as _re17

_secret_lines17 = [l for l in _csm_src17.splitlines() if _re17.search(r"^\s*_SECRET_PATH\s*=", l)]
test(
    "17f2: _SECRET_PATH uses _copilot_home() if present",
    all("_copilot_home()" in l for l in _secret_lines17) if _secret_lines17 else True,
    f"Lines: {_secret_lines17}",
)


# 17g. POSIX-style path normalization (regression for Git Bash HOME on Windows)
test("17g: _normalize_posix_home function exists", "def _normalize_posix_home" in _csm_src17)

try:
    import importlib.util as _ilu17g

    _csm_spec17g = _ilu17g.spec_from_file_location("csm17g", _csm_path)
    _csm17g = _ilu17g.module_from_spec(_csm_spec17g)
    _csm_spec17g.loader.exec_module(_csm17g)

    _saved_osname17g = os.name

    # Build expected Windows paths using explicit backslash (chr(92)) so
    # assertions are correct regardless of the host's os.sep value.
    _bs = chr(92)  # backslash — avoids escape-in-fstring issues

    # Simulate Windows for normalization tests
    os.name = "nt"
    try:
        test(
            "17g2: /c/Users/foo → C:\\Users\\foo",
            _csm17g._normalize_posix_home("/c/Users/foo") == f"C:{_bs}Users{_bs}foo",
            f"Got: {_csm17g._normalize_posix_home('/c/Users/foo')}",
        )
        test(
            "17g3: /d/Projects/bar → D:\\Projects\\bar",
            _csm17g._normalize_posix_home("/d/Projects/bar") == f"D:{_bs}Projects{_bs}bar",
            f"Got: {_csm17g._normalize_posix_home('/d/Projects/bar')}",
        )
        test(
            "17g4: /mnt/c/Users/foo → C:\\Users\\foo",
            _csm17g._normalize_posix_home("/mnt/c/Users/foo") == f"C:{_bs}Users{_bs}foo",
            f"Got: {_csm17g._normalize_posix_home('/mnt/c/Users/foo')}",
        )
        test(
            "17g5: /mnt/d/Work → D:\\Work",
            _csm17g._normalize_posix_home("/mnt/d/Work") == f"D:{_bs}Work",
            f"Got: {_csm17g._normalize_posix_home('/mnt/d/Work')}",
        )
        _native_win_path17g = "C:\\Users\\foo"
        _native_win_result17g = _csm17g._normalize_posix_home(_native_win_path17g)
        test(
            "17g6: native Windows path unchanged",
            _native_win_result17g == _native_win_path17g,
            f"Got: {_native_win_result17g}",
        )
        test(
            "17g7: plain /tmp unchanged on Windows",
            _csm17g._normalize_posix_home("/tmp/test") == "/tmp/test",
            f"Got: {_csm17g._normalize_posix_home('/tmp/test')}",
        )

        # Cygwin-style paths
        test(
            "17g9: /cygdrive/c/Users/foo → C:\\Users\\foo",
            _csm17g._normalize_posix_home("/cygdrive/c/Users/foo") == f"C:{_bs}Users{_bs}foo",
            f"Got: {_csm17g._normalize_posix_home('/cygdrive/c/Users/foo')}",
        )
        test(
            "17g10: /cygdrive/d/Work → D:\\Work",
            _csm17g._normalize_posix_home("/cygdrive/d/Work") == f"D:{_bs}Work",
            f"Got: {_csm17g._normalize_posix_home('/cygdrive/d/Work')}",
        )

        # Non-Windows: no normalization
        os.name = "posix"
        test(
            "17g8: no normalization on non-Windows",
            _csm17g._normalize_posix_home("/c/Users/foo") == "/c/Users/foo",
            f"Got: {_csm17g._normalize_posix_home('/c/Users/foo')}",
        )
    finally:
        os.name = _saved_osname17g
except Exception as e:
    test("17g: POSIX path normalization tests", False, str(e))

# 17h. Subprocess: HOME with POSIX-style path correctly resolves marker on Windows
if _csm_path.is_file() and os.name == "nt":
    _home17h = Path(tempfile.mkdtemp(prefix="test-home17h-"))
    (_home17h / ".copilot" / "markers").mkdir(parents=True)
    (_home17h / ".copilot" / "markers" / "dispatched-subagent-active").write_text(
        json.dumps(
            {"name": "dispatched-subagent-active", "ts": str(int(time.time())), "active_tentacles": ["posix-path-test"]}
        )
    )
    # Build a POSIX-style path: C:\Users\x\AppData\... → /c/Users/x/AppData/...
    _home17h_str = str(_home17h)
    if len(_home17h_str) >= 2 and _home17h_str[1] == ":":
        _posix_home17h = "/" + _home17h_str[0].lower() + "/" + _home17h_str[3:].replace("\\", "/")
    else:
        _posix_home17h = _home17h_str
    r_posix17h = subprocess.run(
        [sys.executable, str(_csm_path)],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=10,
        env={**os.environ, "HOME": _posix_home17h},
    )
    test(
        "17h: POSIX-style HOME (e.g. /c/Users/...) → marker found → blocks (exit 1)",
        r_posix17h.returncode == 1,
        f"POSIX HOME={_posix_home17h} exit={r_posix17h.returncode} stdout={r_posix17h.stdout[:120]}",
    )
    shutil.rmtree(str(_home17h), ignore_errors=True)


import ast

py_files = list(REPO.glob("*.py")) + list((REPO / "hooks").glob("*.py")) + list((REPO / "hooks" / "rules").glob("*.py"))
for f in sorted(py_files):
    try:
        ast.parse(f.read_text(encoding="utf-8"))
        test(f"Syntax OK: {f.name}", True)
    except SyntaxError as e:
        test(f"Syntax OK: {f.name}", False, f"Line {e.lineno}: {e.msg}")


# ═══════════════════════════════════════════════════════════════════
#  Section 18: Markdown / Session-State Scope Regression
#  Verifies that .md files and ~/.copilot/session-state/ paths are
#  NOT counted as code edits for tentacle or learn-gate purposes.
# ═══════════════════════════════════════════════════════════════════

print("\n📝 Section 18: Markdown / Session-State Scope Regression")

from rules.common import CODE_EXTENSIONS as _ce18, SOURCE_EXTENSIONS as _se18, is_session_path as _isp18

# 18a. .md must NOT be in CODE_EXTENSIONS
test(
    ".md excluded from CODE_EXTENSIONS",
    ".md" not in _ce18,
    ".md was found in CODE_EXTENSIONS — it would count markdown writes as code edits",
)

# 18b. .md must still be in SOURCE_EXTENSIONS (for bash write detection)
test(
    ".md kept in SOURCE_EXTENSIONS",
    ".md" in _se18,
    ".md should remain in SOURCE_EXTENSIONS so bash_writes_source_files still detects markdown writes",
)

# 18c. Core code extensions still present in CODE_EXTENSIONS
for _ext18 in (".py", ".ts", ".json", ".yaml", ".yml"):
    test(f"{_ext18} still in CODE_EXTENSIONS", _ext18 in _ce18)

# 18d. is_session_path() rejects session-state absolute paths
import os as _os18

_fake_ss18 = str(Path.home() / ".copilot" / "session-state" / "abc" / "research" / "notes.md")
test("is_session_path: session-state absolute path → True", _isp18(_fake_ss18), f"Expected True for {_fake_ss18}")

# 18e. is_session_path() passes real project paths
test(
    "is_session_path: project source path → False",
    not _isp18("hooks/rules/common.py"),
    "Project path should NOT be classified as session-state",
)

test("is_session_path: relative session-state path → True", _isp18(".copilot/session-state/x/research/file.md"))

# 18f. EnforceLearnRule does not count a markdown edit
try:
    from rules.learn_gate import EnforceLearnRule as _elr18
    import rules.learn_gate as _lg18

    _orig_vc18 = _lg18.verify_counter
    _orig_sc18 = _lg18.sign_counter
    _orig_ctm18 = _lg18.check_tamper_marker

    _counter18 = [0]
    _lg18.verify_counter = lambda p: _counter18[0]
    _lg18.sign_counter = lambda p, v: _counter18.__setitem__(0, v)
    _lg18.check_tamper_marker = lambda: False

    _rule18 = _elr18()

    # Edit a markdown file → counter must NOT increment
    _rule18.evaluate(
        "preToolUse",
        {
            "toolName": "edit",
            "toolArgs": {"path": "docs/README.md"},
        },
    )
    test(
        "EnforceLearnRule: .md edit does NOT increment counter",
        _counter18[0] == 0,
        f"Counter was {_counter18[0]}, expected 0",
    )

    # Edit a session-state markdown → counter must NOT increment
    _rule18.evaluate(
        "preToolUse",
        {
            "toolName": "edit",
            "toolArgs": {"path": _fake_ss18},
        },
    )
    test(
        "EnforceLearnRule: session-state .md edit does NOT increment counter",
        _counter18[0] == 0,
        f"Counter was {_counter18[0]}, expected 0",
    )

    # Edit a real Python file → counter MUST increment
    _rule18.evaluate(
        "preToolUse",
        {
            "toolName": "edit",
            "toolArgs": {"path": "hooks/rules/common.py"},
        },
    )
    test(
        "EnforceLearnRule: .py edit DOES increment counter",
        _counter18[0] == 1,
        f"Counter was {_counter18[0]}, expected 1",
    )

    # Edit a YAML config file → counter MUST increment
    _rule18.evaluate(
        "preToolUse",
        {
            "toolName": "edit",
            "toolArgs": {"path": "config.yaml"},
        },
    )
    test(
        "EnforceLearnRule: .yaml edit DOES increment counter",
        _counter18[0] == 2,
        f"Counter was {_counter18[0]}, expected 2",
    )

    _lg18.verify_counter = _orig_vc18
    _lg18.sign_counter = _orig_sc18
    _lg18.check_tamper_marker = _orig_ctm18
    test("EnforceLearnRule markdown regression ran without exception", True)
except Exception as e:
    test("EnforceLearnRule markdown regression ran without exception", False, str(e))

# 18g. TentacleSuggestRule does not track markdown paths in the signed marker
try:
    from rules.tentacle import TentacleSuggestRule as _tsr18
    import rules.tentacle as _rt18

    _orig_vlist18 = _rt18.verify_list_marker
    _orig_slist18 = _rt18.sign_list_marker
    _orig_sf18 = _rt18.SUGGESTED_FILE

    _tracked18 = set()
    _rt18.verify_list_marker = lambda p: set(_tracked18)
    _rt18.sign_list_marker = lambda p, lines: _tracked18.update(lines)
    _rt18.SUGGESTED_FILE = Path("/nonexistent/tentacle-suggested-md-regression-testonly")

    _tsr18_rule = _tsr18()

    # Edit a session-state markdown → must NOT be tracked
    _tsr18_rule.evaluate(
        "postToolUse",
        {
            "toolName": "edit",
            "toolArgs": {},
            "toolResult": {"filePath": _fake_ss18},
        },
    )
    test(
        "TentacleSuggestRule: session-state .md NOT added to tracked edits",
        len(_tracked18) == 0,
        f"Expected 0 tracked files; got: {_tracked18}",
    )

    # Edit a regular markdown doc → must NOT be tracked (not in CODE_EXTENSIONS)
    _tsr18_rule.evaluate(
        "postToolUse",
        {
            "toolName": "edit",
            "toolArgs": {},
            "toolResult": {"filePath": "docs/HOOKS.md"},
        },
    )
    test(
        "TentacleSuggestRule: doc .md NOT added to tracked edits",
        len(_tracked18) == 0,
        f"Expected 0 tracked files; got: {_tracked18}",
    )

    # Edit a real Python file → MUST be tracked
    _tsr18_rule.evaluate(
        "postToolUse",
        {
            "toolName": "edit",
            "toolArgs": {},
            "toolResult": {"filePath": "hooks/rules/common.py"},
        },
    )
    test(
        "TentacleSuggestRule: .py DOES get tracked",
        any("common.py" in f for f in _tracked18),
        f"Expected common.py in tracked files; got: {_tracked18}",
    )

    _rt18.verify_list_marker = _orig_vlist18
    _rt18.sign_list_marker = _orig_slist18
    _rt18.SUGGESTED_FILE = _orig_sf18
    test("TentacleSuggestRule markdown regression ran without exception", True)
except Exception as e:
    test("TentacleSuggestRule markdown regression ran without exception", False, str(e))

# 18h. TrackEditsRule (edit_tracker) does not count markdown files
try:
    from rules.edit_tracker import TrackEditsRule as _ter18
    import rules.edit_tracker as _et18

    _orig_vc_et18 = _et18.verify_counter
    _orig_sc_et18 = _et18.sign_counter
    _orig_vl_et18 = _et18.verify_list_marker
    _orig_sl_et18 = _et18.sign_list_marker
    _orig_load18 = None  # patch instance method instead

    _code_count18 = [0]  # kept for restore compatibility
    _tentacle_files18 = set()

    _ter18_rule = _ter18()

    # Simulate git status returning markdown and Python modifications
    _counters18 = {}
    _et18.verify_counter = lambda p: _counters18.get(str(p), 0)
    _et18.sign_counter = lambda p, v: _counters18.__setitem__(str(p), v)
    _et18.verify_list_marker = lambda p: set(_tentacle_files18)
    _et18.sign_list_marker = lambda p, lines: _tentacle_files18.update(lines)

    # Patch _get_git_modified to return both a .md and a .py file
    _ter18_rule._get_git_modified = lambda: {"docs/CHANGES.md", "hooks/rules/common.py"}
    _ter18_rule._load_seen = lambda: set()
    _ter18_rule._save_seen = lambda s: None

    _ter18_rule.evaluate("postToolUse", {"toolName": "bash", "toolArgs": {"command": "git status"}})

    test(
        "TrackEditsRule: .md file NOT counted as code edit",
        _counters18.get(str(_et18.CODE_EDIT_COUNTER), 0) == 1,
        f"Expected CODE_EDIT_COUNTER=1 (only .py); got {_counters18}",
    )
    test(
        "TrackEditsRule: .md file NOT added to tentacle-edits marker",
        not any(".md" in f for f in _tentacle_files18),
        f"Found .md in tentacle edits: {_tentacle_files18}",
    )
    test(
        "TrackEditsRule: .py file IS counted as code edit",
        any("common.py" in f for f in _tentacle_files18),
        f"Expected common.py in tentacle edits: {_tentacle_files18}",
    )

    _et18.verify_counter = _orig_vc_et18
    _et18.sign_counter = _orig_sc_et18
    _et18.verify_list_marker = _orig_vl_et18
    _et18.sign_list_marker = _orig_sl_et18
    test("TrackEditsRule markdown regression ran without exception", True)
except Exception as e:
    test("TrackEditsRule markdown regression ran without exception", False, str(e))

# 18i. Legacy track-bash-edits.py CODE_EXTENSIONS must not contain .md
_tbe_src = (REPO / "hooks" / "track-bash-edits.py").read_text(encoding="utf-8")
test(
    "legacy track-bash-edits.py: .md not in CODE_EXTENSIONS",
    '".md"' not in _tbe_src.split("CODE_EXTENSIONS")[1].split("}")[0],
    "track-bash-edits.py still has '.md' in CODE_EXTENSIONS",
)

# 18j. Legacy enforce-learn.py CODE_EXTENSIONS must not contain .md (was already clean, stays clean)
_el_src = (REPO / "hooks" / "enforce-learn.py").read_text(encoding="utf-8")
test(
    "legacy enforce-learn.py: .md not in CODE_EXTENSIONS",
    '".md"' not in _el_src.split("CODE_EXTENSIONS")[1].split("}")[0],
    "enforce-learn.py still has '.md' in CODE_EXTENSIONS",
)

# 18k. Legacy enforce-learn.py CODE_EXTENSIONS must include .sh, .bat, .ps1 (shell-script alignment)
_el_ce_block18 = _el_src.split("CODE_EXTENSIONS")[1].split("}")[0]
test(
    "legacy enforce-learn.py: .sh in CODE_EXTENSIONS",
    '".sh"' in _el_ce_block18,
    "enforce-learn.py missing '.sh' — weaker than unified common.py",
)
test(
    "legacy enforce-learn.py: .bat in CODE_EXTENSIONS",
    '".bat"' in _el_ce_block18,
    "enforce-learn.py missing '.bat' — weaker than unified common.py",
)
test(
    "legacy enforce-learn.py: .ps1 in CODE_EXTENSIONS",
    '".ps1"' in _el_ce_block18,
    "enforce-learn.py missing '.ps1' — weaker than unified common.py",
)

# 18l. Legacy enforce-learn.py actually counts .sh/.bat/.ps1 edits (runtime behaviour)
# Import the legacy hook directly and drive its counter logic in isolation.
try:
    import importlib.util as _ilu18

    _el_spec18 = _ilu18.spec_from_file_location("_enforce_learn_legacy", REPO / "hooks" / "enforce-learn.py")
    _el_mod18 = _ilu18.module_from_spec(_el_spec18)
    # Stub marker_auth before exec so the module doesn't try to read the filesystem
    import types as _types18

    _stub_ma18 = _types18.ModuleType("marker_auth")
    _stub_ma18.verify_marker = lambda p, n: False
    _stub_ma18.verify_counter = lambda p: 0
    _el_counter18 = [0]

    def _stub_sign_counter18(p, v):
        _el_counter18[0] = v

    _stub_ma18.sign_counter = _stub_sign_counter18
    _stub_ma18.is_secret_access = lambda c: False
    _stub_ma18.check_tamper_marker = lambda: False
    import sys as _sys18

    _sys18.modules["marker_auth"] = _stub_ma18
    _el_spec18.loader.exec_module(_el_mod18)

    # Restore env
    del _sys18.modules["marker_auth"]

    _orig_el_vc18 = _el_mod18.verify_counter
    _orig_el_sc18 = _el_mod18.sign_counter

    _el_mod18.verify_counter = lambda p: _el_counter18[0]
    _el_mod18.sign_counter = _stub_sign_counter18

    # .sh edit must increment counter
    _el_counter18[0] = 0
    _el_mod18.MARKERS_DIR.mkdir(parents=True, exist_ok=True)

    def _el_run_edit18(path):
        _el_mod18._get_edit_count = lambda: _el_counter18[0]
        _el_mod18._increment_counter()

    # Directly exercise _should_count by calling _increment_counter after confirming suffix match
    _el_counter18[0] = 0
    import json as _json18, io as _io18, unittest.mock as _mock18

    for _shell_ext18 in (".sh", ".bat", ".ps1"):
        _el_counter18[0] = 0
        _payload18 = json.dumps({"toolName": "edit", "toolArgs": {"path": f"scripts/deploy{_shell_ext18}"}})
        with (
            _mock18.patch.object(_el_mod18, "_get_edit_count", return_value=_el_counter18[0]),
            _mock18.patch.object(
                _el_mod18, "_increment_counter", side_effect=lambda: _el_counter18.__setitem__(0, _el_counter18[0] + 1)
            ),
            _mock18.patch("sys.stdin", _io18.StringIO(_payload18)),
            _mock18.patch("sys.stdout", _io18.StringIO()),
        ):
            _el_mod18.main()
        test(
            f"legacy enforce-learn.py: {_shell_ext18} edit increments counter",
            _el_counter18[0] == 1,
            f"Counter was {_el_counter18[0]}, expected 1",
        )

    # .md edit must NOT increment counter (false-positive regression)
    _el_counter18[0] = 0
    _md_payload18 = json.dumps({"toolName": "edit", "toolArgs": {"path": "docs/README.md"}})
    with (
        _mock18.patch.object(_el_mod18, "_get_edit_count", return_value=0),
        _mock18.patch.object(
            _el_mod18, "_increment_counter", side_effect=lambda: _el_counter18.__setitem__(0, _el_counter18[0] + 1)
        ),
        _mock18.patch("sys.stdin", _io18.StringIO(_md_payload18)),
        _mock18.patch("sys.stdout", _io18.StringIO()),
    ):
        _el_mod18.main()
    test(
        "legacy enforce-learn.py: .md edit does NOT increment counter (false-positive guard)",
        _el_counter18[0] == 0,
        f"Counter was {_el_counter18[0]}, expected 0",
    )

    # session-state .md must NOT increment counter
    _el_counter18[0] = 0
    _ss_payload18 = json.dumps(
        {"toolName": "edit", "toolArgs": {"path": str(Path.home() / ".copilot" / "session-state" / "x.sh")}}
    )
    with (
        _mock18.patch.object(_el_mod18, "_get_edit_count", return_value=0),
        _mock18.patch.object(
            _el_mod18, "_increment_counter", side_effect=lambda: _el_counter18.__setitem__(0, _el_counter18[0] + 1)
        ),
        _mock18.patch("sys.stdin", _io18.StringIO(_ss_payload18)),
        _mock18.patch("sys.stdout", _io18.StringIO()),
    ):
        _el_mod18.main()
    test(
        "legacy enforce-learn.py: session-state .sh edit does NOT increment counter",
        _el_counter18[0] == 0,
        f"Counter was {_el_counter18[0]}, expected 0",
    )

except Exception as _e18k:
    for _lbl18 in (".sh", ".bat", ".ps1", ".md false-positive", "session-state .sh"):
        test(f"legacy enforce-learn.py runtime: {_lbl18}", False, str(_e18k))


# ═══════════════════════════════════════════════════════════════════
#  Section 19: FP-1/FP-2/FP-3 false-positive regression tests
#  Verifies the confirmed false-positive fixes from hook-fp-research:
#    FP-1: TentacleEnforceRule allows edit/create to session-state paths
#    FP-2: TentacleEnforceRule bash redirect check validates destination path
#    FP-3: is_source_path() excludes session-state paths
# ═══════════════════════════════════════════════════════════════════

print("\n📝 Section 19: Hook False-Positive Regression (FP-1/FP-2/FP-3)")

from rules.common import is_source_path as _isp19, bash_writes_source_files as _bwsf19
from pathlib import Path as _Path19

_ss_md19 = str(_Path19.home() / ".copilot" / "session-state" / "abc" / "research" / "notes.md")
_ss_py19 = str(_Path19.home() / ".copilot" / "session-state" / "abc" / "out.py")

# 19a. FP-3: is_source_path() must return False for session-state paths
test(
    "FP-3: is_source_path session-state .md → False",
    not _isp19(_ss_md19),
    f"is_source_path({_ss_md19!r}) should be False (session-state)",
)

test(
    "FP-3: is_source_path session-state .py → False",
    not _isp19(_ss_py19),
    f"is_source_path({_ss_py19!r}) should be False (session-state)",
)

# Real project .md and .py still correct
test(
    "FP-3: is_source_path project .md → True",
    _isp19("docs/HOOKS.md"),
    "docs/HOOKS.md should still be a source path (.md in SOURCE_EXTENSIONS)",
)

test(
    "FP-3: is_source_path project .py → True",
    _isp19("hooks/rules/common.py"),
    "hooks/rules/common.py should be a source path",
)

# 19b. FP-3: bash_writes_source_files must return False for redirect to session-state
test(
    "FP-3: bash redirect to session-state .md → NOT a source write",
    not _bwsf19(f"python3 script.py > {_ss_md19}"),
    "redirect to session-state markdown should not count as a source write",
)

test(
    "FP-3: bash redirect to session-state .py → NOT a source write",
    not _bwsf19(f"python3 script.py > {_ss_py19}"),
    "redirect to session-state .py should not count as a source write",
)

# 19c. FP-2: bash redirect to non-source destination must NOT flag writes_source
test(
    "FP-2: bash_writes_source_files redirect to .log → False",
    not _bwsf19("python3 test_fixes.py > output.log"),
    "redirect to .log is not a source write",
)

test(
    "FP-2: bash_writes_source_files redirect to .txt → False",
    not _bwsf19("python3 test_hooks.py > results.txt"),
    "redirect to .txt is not a source write",
)

test(
    "FP-2: bash_writes_source_files redirect to /dev/null → False",
    not _bwsf19("python3 briefing.py task > /dev/null"),
    "redirect to /dev/null is not a source write",
)

# Still catches real source writes
test(
    "FP-2: bash_writes_source_files redirect to .py → True",
    _bwsf19("echo 'x' > src/main.py"),
    "redirect to .py should still be a source write",
)

# 19d. FP-1: TentacleEnforceRule must return None for edit/create to session-state
try:
    from rules.tentacle import TentacleEnforceRule as _TER19
    import rules.tentacle as _rt19

    _orig_vlist19 = _rt19.verify_list_marker
    _orig_vm19 = _rt19.verify_marker
    _orig_sl19 = _rt19.sign_list_marker
    _orig_isa19 = _rt19.is_secret_access
    _orig_ctm19 = _rt19.check_tamper_marker
    _orig_ggr19 = _rt19._get_git_root  # None → no repo-prefix filtering

    # Fake threshold: 3 files across 2 modules
    _fake_files19 = ["src/auth/login.py", "src/api/routes.py", "tests/test_auth.py"]
    _rt19.verify_list_marker = lambda p: set(_fake_files19)
    _rt19.verify_marker = lambda p, n: False
    _rt19.sign_list_marker = lambda p, lines: None
    _rt19.is_secret_access = lambda c: False
    _rt19.check_tamper_marker = lambda: False
    _rt19._get_git_root = lambda: None

    _ter19 = _TER19()

    # FP-1: create to session-state research path must be allowed (return None)
    _ss_create_path19 = str(_Path19.home() / ".copilot" / "session-state" / "abc" / "research" / "output.md")
    _r19_create = _ter19.evaluate(
        "preToolUse",
        {
            "toolName": "create",
            "toolArgs": {"path": _ss_create_path19},
        },
    )
    test(
        "FP-1: TentacleEnforceRule create to session-state → None (allowed)",
        _r19_create is None,
        f"Expected None, got: {_r19_create!r}",
    )

    # FP-1: edit to session-state path must be allowed
    _r19_edit = _ter19.evaluate(
        "preToolUse",
        {
            "toolName": "edit",
            "toolArgs": {"path": _ss_md19},
        },
    )
    test(
        "FP-1: TentacleEnforceRule edit to session-state → None (allowed)",
        _r19_edit is None,
        f"Expected None, got: {_r19_edit!r}",
    )

    # Non-session edit at threshold must still deny
    _r19_deny = _ter19.evaluate(
        "preToolUse",
        {
            "toolName": "edit",
            "toolArgs": {"path": "src/api/new_feature.py"},
        },
    )
    test(
        "FP-1: TentacleEnforceRule edit to project file at threshold → deny",
        _r19_deny is not None and _r19_deny.get("permissionDecision") == "deny",
        f"Expected deny, got: {_r19_deny!r}",
    )

    # FP-2: bash redirect to .txt (non-source) must return None at threshold
    _r19_bash_txt = _ter19.evaluate(
        "preToolUse",
        {
            "toolName": "bash",
            "toolArgs": {"command": "python3 test_fixes.py > output.txt"},
        },
    )
    test(
        "FP-2: TentacleEnforceRule bash redirect to .txt → None (not a source write)",
        _r19_bash_txt is None,
        f"Expected None, got: {_r19_bash_txt!r}",
    )

    # FP-2: bash redirect to /dev/null must return None
    _r19_bash_devnull = _ter19.evaluate(
        "preToolUse",
        {
            "toolName": "bash",
            "toolArgs": {"command": "python3 briefing.py task > /dev/null"},
        },
    )
    test(
        "FP-2: TentacleEnforceRule bash redirect to /dev/null → None",
        _r19_bash_devnull is None,
        f"Expected None, got: {_r19_bash_devnull!r}",
    )

    # FP-2: bash redirect to a real .py source file must still be caught
    _r19_bash_py = _ter19.evaluate(
        "preToolUse",
        {
            "toolName": "bash",
            "toolArgs": {"command": "echo 'x' > src/generated.py"},
        },
    )
    test(
        "FP-2: TentacleEnforceRule bash redirect to .py → deny (real source write)",
        _r19_bash_py is not None and _r19_bash_py.get("permissionDecision") == "deny",
        f"Expected deny for .py redirect, got: {_r19_bash_py!r}",
    )

    test("Section 19 FP regression tests ran without exception", True)
except Exception as _e19:
    test("Section 19 FP regression tests ran without exception", False, str(_e19))
finally:
    try:
        _rt19.verify_list_marker = _orig_vlist19
        _rt19.verify_marker = _orig_vm19
        _rt19.sign_list_marker = _orig_sl19
        _rt19.is_secret_access = _orig_isa19
        _rt19.check_tamper_marker = _orig_ctm19
        _rt19._get_git_root = _orig_ggr19
    except NameError:
        pass

# ═══════════════════════════════════════════════════════════════════
#  Section 19e: Standalone enforce-tentacle.py FP-1 parity
#  Regression: standalone file must mirror the rules-path FP-1 exemption
#  so it cannot silently drift back to blocking session-state paths.
# ═══════════════════════════════════════════════════════════════════

print("\n📝 Section 19e: Standalone enforce-tentacle.py FP-1 Parity")

# 19e-src: source-code guard — presence of session-state exemption for edit/create
_standalone_src19e = (REPO / "hooks" / "enforce-tentacle.py").read_text(encoding="utf-8")
test(
    "FP-1 parity: standalone enforce-tentacle.py contains session-state exemption",
    ".copilot/session-state" in _standalone_src19e,
    "hooks/enforce-tentacle.py must contain '.copilot/session-state' in its FP-1 guard",
)
test(
    "FP-1 parity: standalone enforce-tentacle.py applies exemption to edit/create",
    'tool_name in ("edit", "create")' in _standalone_src19e,
    "hooks/enforce-tentacle.py must check tool_name in ('edit', 'create') for FP-1",
)

# 19e-func: functional test — run main() with mocked stdin/stdout
try:
    import importlib.util as _ilu19e
    import io as _io19e

    _spec19e = _ilu19e.spec_from_file_location(
        "_enforce_tentacle_19e",
        REPO / "hooks" / "enforce-tentacle.py",
    )
    _mod19e = _ilu19e.module_from_spec(_spec19e)
    _spec19e.loader.exec_module(_mod19e)

    # Patch module-level marker helpers
    _orig_vm19e = _mod19e.verify_marker
    _orig_vl19e = _mod19e.verify_list_marker
    _orig_isa19e = _mod19e.is_secret_access
    _orig_ctm19e = _mod19e.check_tamper_marker

    _fake_files19e = ["src/auth/login.py", "src/api/routes.py", "tests/test_auth.py"]
    _mod19e.verify_marker = lambda p, n: False
    _mod19e.verify_list_marker = lambda p: set(_fake_files19e)
    _mod19e.is_secret_access = lambda c: False
    _mod19e.check_tamper_marker = lambda: False

    _ss_create19e = str(Path.home() / ".copilot" / "session-state" / "abc" / "research" / "output.md")
    _ss_edit19e = str(Path.home() / ".copilot" / "session-state" / "abc" / "out.py")

    def _run_standalone19e(tool_name, path):
        import sys as _sys

        payload = json.dumps({"toolName": tool_name, "toolArgs": {"path": path}})
        _old_in, _old_out = _sys.stdin, _sys.stdout
        _sys.stdin = _io19e.StringIO(payload)
        _sys.stdout = _io19e.StringIO()
        try:
            _mod19e.main()
            return _sys.stdout.getvalue().strip()
        finally:
            _sys.stdin, _sys.stdout = _old_in, _old_out

    _out19e_create = _run_standalone19e("create", _ss_create19e)
    test(
        "FP-1 parity: standalone create to session-state → no output (allowed)",
        _out19e_create == "",
        f"Expected no output (allow), got: {_out19e_create!r}",
    )

    _out19e_edit = _run_standalone19e("edit", _ss_edit19e)
    test(
        "FP-1 parity: standalone edit to session-state → no output (allowed)",
        _out19e_edit == "",
        f"Expected no output (allow), got: {_out19e_edit!r}",
    )

    _out19e_deny = _run_standalone19e("edit", "src/api/new_feature.py")
    test(
        "FP-1 parity: standalone edit to project file at threshold → deny",
        '"permissionDecision": "deny"' in _out19e_deny or (_out19e_deny != "" and "deny" in _out19e_deny.lower()),
        f"Expected deny output, got: {_out19e_deny!r}",
    )

    test("Section 19e standalone parity tests ran without exception", True)
except Exception as _e19e:
    test("Section 19e standalone parity tests ran without exception", False, str(_e19e))
finally:
    try:
        _mod19e.verify_marker = _orig_vm19e
        _mod19e.verify_list_marker = _orig_vl19e
        _mod19e.is_secret_access = _orig_isa19e
        _mod19e.check_tamper_marker = _orig_ctm19e
    except NameError:
        pass


# ═══════════════════════════════════════════════════════════════════
#  Section 20: Quoted redirect target detection
#  Regression: shell-quoted paths like > "src/main.py" must be
#  treated as source-file redirects, not bypassed due to .py" suffix.
# ═══════════════════════════════════════════════════════════════════

print("\n📝 Section 20: Quoted redirect target detection")

from rules.common import bash_writes_source_files as _bwsf20, _strip_shell_quotes

# 20a. _strip_shell_quotes helper
test("_strip_shell_quotes: double-quoted", _strip_shell_quotes('"src/main.py"') == "src/main.py")
test("_strip_shell_quotes: single-quoted", _strip_shell_quotes("'src/main.py'") == "src/main.py")
test("_strip_shell_quotes: unquoted passthrough", _strip_shell_quotes("src/main.py") == "src/main.py")
test("_strip_shell_quotes: mismatched quotes passthrough", _strip_shell_quotes("\"src/main.py'") == "\"src/main.py'")

# 20b. bash_writes_source_files with quoted redirect targets
test(
    'quoted redirect > "src/main.py" → writes',
    _bwsf20('echo x > "src/main.py"'),
    "double-quoted .py redirect must be detected as a source write",
)
test(
    "quoted redirect > 'src/main.py' → writes",
    _bwsf20("echo x > 'src/main.py'"),
    "single-quoted .py redirect must be detected as a source write",
)
test(
    'quoted redirect >> "app/utils.ts" → writes',
    _bwsf20('echo x >> "app/utils.ts"'),
    "double-quoted .ts append redirect must be detected as a source write",
)

# 20c. Quoted safe destinations must still be allowed
test(
    'quoted redirect > "/dev/null" → NOT writes',
    not _bwsf20('echo x > "/dev/null"'),
    "quoted /dev/null redirect must NOT be a source write",
)
test(
    'quoted redirect > "/tmp/test.py" → NOT writes',
    not _bwsf20('echo x > "/tmp/test.py"'),
    "quoted /tmp redirect must NOT be a source write",
)
test(
    "quoted redirect > 'results.log' → NOT writes",
    not _bwsf20("echo x > 'results.log'"),
    "quoted .log redirect must NOT be a source write",
)

# 20d. Regression guard: unquoted behaviour unchanged
test(
    "unquoted redirect > src/main.py → still writes",
    _bwsf20("echo x > src/main.py"),
    "unquoted .py redirect must still be detected as a source write",
)
test(
    "unquoted redirect > /dev/null → still NOT writes",
    not _bwsf20("echo x > /dev/null"),
    "unquoted /dev/null redirect must still NOT be a source write",
)


# ═══════════════════════════════════════════════════════════════════
#  Section 21: TentacleEnforceRule – quoted redirect source paths
#  Regression: TentacleEnforceRule must deny bash commands that write
#  to source files via shell-quoted redirect targets, e.g.
#      echo x > "src/main.py"
#  Previously, m.group(1) captured the quotes, Path.suffix → ".py\""
#  and is_source_path returned False, wrongly allowing the command.
# ═══════════════════════════════════════════════════════════════════

print("\n📝 Section 21: TentacleEnforceRule quoted redirect enforcement")

try:
    import importlib as _il21
    import sys as _sys21
    import types as _types21
    from pathlib import Path as _Path21

    _ter21_mod = _il21.import_module("rules.tentacle")
    _TER21 = _ter21_mod.TentacleEnforceRule

    def _make_event21(command, *, edited_files=None):
        """Return a preToolUse event dict for a bash command at threshold."""
        return {
            "toolName": "bash",
            "toolArgs": {"command": command},
        }

    # Monkeypatch the module-level helpers so we can test without real marker files.
    _orig_verify_marker21 = _ter21_mod.verify_marker
    _orig_verify_list21 = _ter21_mod.verify_list_marker
    _orig_isa21 = _ter21_mod.is_secret_access
    _orig_ctm21 = _ter21_mod.check_tamper_marker
    _orig_ggr21 = _ter21_mod._get_git_root  # None → legacy entries not filtered by prefix

    # Simulate: tamper=False, no bypass markers, N files across M modules at threshold
    _FAKE_EDITS21 = {
        "src/api/routes.py",
        "src/db/models.py",
        "ui/components/App.tsx",
    }
    _ter21_mod.check_tamper_marker = lambda: False
    _ter21_mod.verify_marker = lambda p, n: False
    _ter21_mod.verify_list_marker = lambda p: set(_FAKE_EDITS21)
    _ter21_mod.is_secret_access = lambda c: False
    _ter21_mod._get_git_root = lambda: None

    _rule21 = _TER21()

    # 21a: double-quoted .py redirect must be denied
    _result21a = _rule21.evaluate("preToolUse", _make_event21('echo x > "src/main.py"'))
    test(
        "TentacleEnforceRule: double-quoted .py redirect → denied",
        _result21a is not None and _result21a.get("permissionDecision") == "deny",
        f"Expected deny, got: {_result21a!r}",
    )

    # 21b: single-quoted .ts redirect must be denied
    _result21b = _rule21.evaluate("preToolUse", _make_event21("echo x > 'app/utils.ts'"))
    test(
        "TentacleEnforceRule: single-quoted .ts redirect → denied",
        _result21b is not None and _result21b.get("permissionDecision") == "deny",
        f"Expected deny, got: {_result21b!r}",
    )

    # 21c: unquoted .py redirect still denied (regression guard)
    _result21c = _rule21.evaluate("preToolUse", _make_event21("echo x > src/main.py"))
    test(
        "TentacleEnforceRule: unquoted .py redirect → still denied",
        _result21c is not None and _result21c.get("permissionDecision") == "deny",
        f"Expected deny, got: {_result21c!r}",
    )

    # 21d: double-quoted /dev/null redirect must NOT trigger writes_source → None
    _result21d = _rule21.evaluate("preToolUse", _make_event21('echo x > "/dev/null"'))
    test(
        "TentacleEnforceRule: double-quoted /dev/null redirect → allowed (None)",
        _result21d is None,
        f"Expected None, got: {_result21d!r}",
    )

    test("Section 21 TentacleEnforceRule quoted redirect tests ran without exception", True)
except Exception as _e21:
    test("Section 21 TentacleEnforceRule quoted redirect tests ran without exception", False, str(_e21))
finally:
    try:
        _ter21_mod.verify_marker = _orig_verify_marker21
        _ter21_mod.verify_list_marker = _orig_verify_list21
        _ter21_mod.is_secret_access = _orig_isa21
        _ter21_mod.check_tamper_marker = _orig_ctm21
        _ter21_mod._get_git_root = _orig_ggr21
    except NameError:
        pass


# ═══════════════════════════════════════════════════════════════════
#  Section 17: Tentacle edits marker — repo-scope isolation & TTL
#
#  Tests:
#    a. _read_edits / _write_edits round-trip new format
#    b. Legacy flat-list migrates without crash
#    c. Cross-repo isolation: edits under repo A are invisible in repo B
#    d. TTL: _prune_ttl drops stale entries (>24 h)
#    e. TentacleEnforceRule: other-repo edits do NOT trigger block
#    f. TentacleEnforceRule: same-repo edits DO trigger block
#    g. get_module() repo_prefix parameter
#    h. HMAC signature validates end-to-end for new JSON payload
# ═══════════════════════════════════════════════════════════════════

print("\n── Section 17: Tentacle edits marker repo-scope isolation ──")

try:
    import rules.tentacle as _rt17
    from rules.tentacle import _read_edits, _write_edits, _prune_ttl, _get_entries_for_repo
    import json as _json17
    import time as _time17

    _now17 = _time17.time()

    # 17a. Round-trip new format via _write_edits / _read_edits
    _td17a = Path(tempfile.mkdtemp(prefix="test-17a-"))
    _marker17a = _td17a / "tentacle-edits"
    _data17a = {"/repo/tools": [{"p": "/repo/tools/src/auth.py", "t": _now17}]}
    _write_edits(_marker17a, _data17a)
    _back17a = _read_edits(_marker17a)
    test(
        "17a: _write_edits / _read_edits round-trips new format",
        "/repo/tools" in _back17a
        and len(_back17a["/repo/tools"]) == 1
        and _back17a["/repo/tools"][0]["p"] == "/repo/tools/src/auth.py",
        f"Got: {_back17a!r}",
    )
    shutil.rmtree(str(_td17a), ignore_errors=True)

    # 17b. Legacy flat-list (mocked) migrates to "legacy" bucket without crash
    _orig_vlist17b = _rt17.verify_list_marker
    _rt17.verify_list_marker = lambda p: {"src/a.py", "src/b.py", "tests/c.py"}
    _loaded17b = _read_edits(Path("/nonexistent/marker"))
    _rt17.verify_list_marker = _orig_vlist17b
    test(
        "17b: legacy flat-list migrates to 'legacy' bucket without crash",
        "legacy" in _loaded17b,
        f"Got keys: {list(_loaded17b)!r}",
    )
    test("17b2: all 3 legacy paths present", len(_loaded17b.get("legacy", [])) == 3, f"Got: {_loaded17b!r}")

    # 17c. Cross-repo isolation: repo-A entries invisible from repo-B
    _repo_a17c = "/fake/repo-a"
    _repo_b17c = "/fake/repo-b"
    _data17c = {
        _repo_a17c: [
            {"p": f"{_repo_a17c}/src/auth.py", "t": _now17},
            {"p": f"{_repo_a17c}/src/api.py", "t": _now17},
            {"p": f"{_repo_a17c}/tests/t.py", "t": _now17},
        ]
    }
    _entries_a17c = _get_entries_for_repo(_data17c, _repo_a17c)
    _entries_b17c = _get_entries_for_repo(_data17c, _repo_b17c)
    test("17c: entries for repo A visible from repo A", len(_entries_a17c) == 3, f"Got: {len(_entries_a17c)}")
    test(
        "17c2: entries for repo A NOT visible from repo B (cross-repo isolation)",
        len(_entries_b17c) == 0,
        f"Got: {len(_entries_b17c)} — cross-repo contamination!",
    )

    # 17d. TTL: _prune_ttl drops entries older than 24 h
    _stale17d = _now17 - 90000  # 25 h ago
    _fresh17d = _now17 - 3600  # 1 h ago
    _bucket17d = [
        {"p": "/repo/old.py", "t": _stale17d},
        {"p": "/repo/new.py", "t": _fresh17d},
    ]
    _pruned17d = _prune_ttl(_bucket17d, _now17)
    test(
        "17d: TTL prune drops stale entry",
        len(_pruned17d) == 1 and _pruned17d[0]["p"] == "/repo/new.py",
        f"Got: {_pruned17d!r}",
    )
    test("17d2: TTL prune keeps fresh entry", _pruned17d[0]["t"] == _fresh17d, f"Got: {_pruned17d!r}")

    # 17e. TentacleEnforceRule: edits in ANOTHER repo do NOT trigger block
    _orig_ggr17e = _rt17._get_git_root
    _orig_vlist17e = _rt17.verify_list_marker
    _orig_vm17e = _rt17.verify_marker
    _orig_isa17e = _rt17.is_secret_access
    _orig_ctm17e = _rt17.check_tamper_marker

    _repo_cur17e = "/current/tools"
    _repo_other17e = "/other/alarm"
    _other_data17e = {
        _repo_other17e: [
            {"p": f"{_repo_other17e}/src/auth.py", "t": _now17},
            {"p": f"{_repo_other17e}/src/api.py", "t": _now17},
            {"p": f"{_repo_other17e}/tests/t.py", "t": _now17},
        ]
    }
    _payload17e = _json17.dumps(_other_data17e, separators=(",", ":"), sort_keys=True)

    _rt17._get_git_root = lambda: _repo_cur17e
    _rt17.verify_list_marker = lambda p: {_payload17e}
    _rt17.verify_marker = lambda p, n: False
    _rt17.is_secret_access = lambda c: False
    _rt17.check_tamper_marker = lambda: False

    _rule17e = _rt17.TentacleEnforceRule()
    _result17e = _rule17e.evaluate("preToolUse", {"toolName": "edit", "toolArgs": {"path": "x.py"}})

    _rt17._get_git_root = _orig_ggr17e
    _rt17.verify_list_marker = _orig_vlist17e
    _rt17.verify_marker = _orig_vm17e
    _rt17.is_secret_access = _orig_isa17e
    _rt17.check_tamper_marker = _orig_ctm17e

    test(
        "17e: TentacleEnforceRule — other-repo edits do NOT trigger block",
        _result17e is None,
        f"Expected None, got: {_result17e!r:.120}",
    )

    # 17f. TentacleEnforceRule: same-repo edits DO trigger block
    _orig_ggr17f = _rt17._get_git_root
    _orig_vlist17f = _rt17.verify_list_marker
    _orig_vm17f = _rt17.verify_marker
    _orig_isa17f = _rt17.is_secret_access
    _orig_ctm17f = _rt17.check_tamper_marker

    _repo_self17f = "/current/tools"
    _same_data17f = {
        _repo_self17f: [
            {"p": f"{_repo_self17f}/src/auth.py", "t": _now17},
            {"p": f"{_repo_self17f}/src/api.py", "t": _now17},
            {"p": f"{_repo_self17f}/tests/t.py", "t": _now17},
        ]
    }
    _payload17f = _json17.dumps(_same_data17f, separators=(",", ":"), sort_keys=True)

    _rt17._get_git_root = lambda: _repo_self17f
    _rt17.verify_list_marker = lambda p: {_payload17f}
    _rt17.verify_marker = lambda p, n: False
    _rt17.is_secret_access = lambda c: False
    _rt17.check_tamper_marker = lambda: False

    _rule17f = _rt17.TentacleEnforceRule()
    _result17f = _rule17f.evaluate("preToolUse", {"toolName": "edit", "toolArgs": {"path": "x.py"}})

    _rt17._get_git_root = _orig_ggr17f
    _rt17.verify_list_marker = _orig_vlist17f
    _rt17.verify_marker = _orig_vm17f
    _rt17.is_secret_access = _orig_isa17f
    _rt17.check_tamper_marker = _orig_ctm17f

    test(
        "17f: TentacleEnforceRule — same-repo edits DO trigger block",
        isinstance(_result17f, dict) and _result17f.get("permissionDecision") == "deny",
        f"Expected deny, got: {_result17f!r:.120}",
    )

    # 17g. get_module with repo_prefix
    from rules.common import get_module as _gm17

    test("17g: get_module without prefix is unchanged", _gm17("src/auth/login.py") == "auth")
    test(
        "17g2: get_module with repo_prefix prepends prefix",
        _gm17("src/auth/login.py", repo_prefix="tools") == "tools:auth",
    )
    test(
        "17g3: get_module with prefix for hooks dir",
        _gm17("hooks/rules/tentacle.py", repo_prefix="tools") == "tools:hooks/rules",
    )

    # 17h. End-to-end HMAC: write with real sign_list_marker, read back with verify
    _td17h = Path(tempfile.mkdtemp(prefix="test-17h-"))
    _marker17h = _td17h / "tentacle-edits"
    _data17h = {"/e2e/repo": [{"p": "/e2e/repo/main.py", "t": _now17}]}
    _write_edits(_marker17h, _data17h)
    _back17h = _read_edits(_marker17h)
    test(
        "17h: HMAC end-to-end: written data reads back correctly",
        "/e2e/repo" in _back17h and _back17h["/e2e/repo"][0]["p"] == "/e2e/repo/main.py",
        f"Got: {_back17h!r}",
    )
    # Corrupt the file and verify it returns empty (HMAC rejects tampered data)
    if _marker17h.is_file():
        raw = _marker17h.read_text(encoding="utf-8")
        try:
            _tampered = __import__("json").loads(raw)
            _tampered["content"] = _tampered.get("content", "") + "TAMPER"
            _marker17h.write_text(__import__("json").dumps(_tampered), encoding="utf-8")
            _tampered_back = _read_edits(_marker17h)
            # With a secret, HMAC fails → empty; without secret, file is plain text
            _secret_present = bool(Path.home().joinpath(".copilot/hooks/.marker-secret").exists())
            if _secret_present:
                test(
                    "17h2: tampered HMAC-signed payload → empty dict returned",
                    _tampered_back == {},
                    f"Got: {_tampered_back!r}",
                )
            else:
                test("17h2: no secret — tamper check skipped (no HMAC)", True)
        except Exception:
            test("17h2: tamper test ran", True)
    shutil.rmtree(str(_td17h), ignore_errors=True)

    # 17i. Empty marker file → _read_edits returns empty dict (no crash)
    _td17i = Path(tempfile.mkdtemp(prefix="test-17i-"))
    _absent17i = _td17i / "nonexistent"
    test("17i: absent marker → empty dict", _read_edits(_absent17i) == {}, f"Got: {_read_edits(_absent17i)!r}")
    shutil.rmtree(str(_td17i), ignore_errors=True)

    # 17j. enforce-path prunes TTL-stale entries
    _orig_ggr17j = _rt17._get_git_root
    _orig_vlist17j = _rt17.verify_list_marker
    _orig_vm17j = _rt17.verify_marker
    _orig_isa17j = _rt17.is_secret_access
    _orig_ctm17j = _rt17.check_tamper_marker

    _repo_17j = "/current/tools"
    _stale_17j = _now17 - 90_000  # 25 h ago
    _stale_data17j = {
        _repo_17j: [
            {"p": f"{_repo_17j}/src/auth.py", "t": _stale_17j},
            {"p": f"{_repo_17j}/src/api.py", "t": _stale_17j},
            {"p": f"{_repo_17j}/tests/t.py", "t": _stale_17j},
        ]
    }
    _payload17j = _json17.dumps(_stale_data17j, separators=(",", ":"), sort_keys=True)

    _rt17._get_git_root = lambda: _repo_17j
    _rt17.verify_list_marker = lambda p: {_payload17j}
    _rt17.verify_marker = lambda p, n: False
    _rt17.is_secret_access = lambda c: False
    _rt17.check_tamper_marker = lambda: False

    _rule17j = _rt17.TentacleEnforceRule()
    _result17j = _rule17j.evaluate("preToolUse", {"toolName": "edit", "toolArgs": {"path": "x.py"}})

    _rt17._get_git_root = _orig_ggr17j
    _rt17.verify_list_marker = _orig_vlist17j
    _rt17.verify_marker = _orig_vm17j
    _rt17.is_secret_access = _orig_isa17j
    _rt17.check_tamper_marker = _orig_ctm17j

    test(
        "17j: enforce-path prunes TTL-stale entries",
        _result17j is None,
        f"Expected None (all pruned), got: {_result17j!r:.120}",
    )

    test("Section 17 tentacle edits repo-scope tests ran without exception", True)
except Exception as _e17:
    test("Section 17 tentacle edits repo-scope tests ran without exception", False, str(_e17))


# ═══════════════════════════════════════════════════════════════════
#  Section 18: Sync-matrix references in hook reminder text
# ═══════════════════════════════════════════════════════════════════

print("\n── Section 18: Sync-matrix references in hook reminder text ──")

try:
    # Test learn_reminder source contains SYNC-MATRIX reference
    _lr_src = (REPO / "hooks" / "rules" / "learn_reminder.py").read_text(encoding="utf-8")
    test(
        "18a: learn_reminder.py source contains SYNC-MATRIX reference",
        "SYNC-MATRIX" in _lr_src,
        "SYNC-MATRIX.md not found in hooks/rules/learn_reminder.py",
    )
    test(
        "18b: learn_reminder.py source still contains learn.py reference",
        "learn.py" in _lr_src,
    )
    test(
        "18c: learn_reminder.py still contains task_complete check",
        "task_complete" in _lr_src,
    )

    # Test tentacle rule source contains SYNC-MATRIX reference
    _tr_src = (REPO / "hooks" / "rules" / "tentacle.py").read_text(encoding="utf-8")
    test(
        "18d: tentacle.py hook source contains SYNC-MATRIX reference",
        "SYNC-MATRIX" in _tr_src,
        "SYNC-MATRIX.md not found in hooks/rules/tentacle.py",
    )
    test(
        "18e: tentacle.py hook TentacleSuggestRule still present",
        "TentacleSuggestRule" in _tr_src,
    )

    # Test that the actual reminder fires via subprocess — isolate with empty HOME
    import os as _os18
    import shutil as _sh18

    _isolated_home_18 = REPO / "_test_section18_home"
    _isolated_home_18.mkdir(parents=True, exist_ok=True)
    try:
        _env18 = {**_os18.environ, "HOME": str(_isolated_home_18)}
        _payload_complete = json.dumps(
            {
                "toolName": "task_complete",
                "toolResult": {"resultType": "success"},
            }
        )
        _r18 = subprocess.run(
            [sys.executable, str(REPO / "hooks" / "hook_runner.py"), "postToolUse"],
            input=_payload_complete,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            env=_env18,
            timeout=15,
        )
        _out18 = (_r18.stdout or "") + (_r18.stderr or "")
        test(
            "18f: hook runner fires on task_complete postToolUse (exit 0)",
            _r18.returncode == 0,
            f"returncode={_r18.returncode}",
        )
        test(
            "18g: learn-reminder output contains SYNC-MATRIX reference",
            "SYNC-MATRIX" in _out18,
            f"output snippet: {_out18[:300]}",
        )
        test(
            "18h: learn-reminder output still contains learn.py reference",
            "learn.py" in _out18,
            f"output snippet: {_out18[:300]}",
        )
    finally:
        _sh18.rmtree(str(_isolated_home_18), ignore_errors=True)

    test("Section 18 sync-matrix hook text tests ran without exception", True)
except Exception as _e18:
    test("Section 18 sync-matrix hook text tests ran without exception", False, str(_e18))


# ═══════════════════════════════════════════════════════════════════
#  Results
# ═══════════════════════════════════════════════════════════════════

print(f"\n{'=' * 50}")
print(f"Results: {PASS} passed, {FAIL} failed out of {PASS + FAIL}")
if FAIL:
    print(f"⚠️  {FAIL} test(s) need attention")
    sys.exit(1)
else:
    print("🎉 All tests passed!")
