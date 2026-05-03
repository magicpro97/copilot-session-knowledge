#!/usr/bin/env python3
"""test_hook_entrypoints_wave3.py — Subprocess-level tests for hook_runner.py
dispatching the Wave 3 rule set.

Each test sends a JSON payload via stdin to hook_runner.py and asserts on the
process exit code and stdout.  An isolated HOME directory is used for every
subprocess call so no real audit logs or marker files are written.

Run:
    python3 tests/test_hook_entrypoints_wave3.py
"""

import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path

if os.name == "nt":
    for _s in (sys.stdout, sys.stderr):
        if hasattr(_s, "reconfigure"):
            _s.reconfigure(encoding="utf-8", errors="replace")

PASS = 0
FAIL = 0
REPO = Path(__file__).parent.parent
RUNNER = REPO / "hooks" / "hook_runner.py"

# Isolated HOME so all subprocess runs never touch real operator state.
_ISOLATED_HOME = Path(tempfile.mkdtemp(prefix="test-ep-home-"))
_ISOLATED_HOME.mkdir(parents=True, exist_ok=True)
_ISOLATED_MARKERS = _ISOLATED_HOME / ".copilot" / "markers"
_ISOLATED_MARKERS.mkdir(parents=True, exist_ok=True)
# Pre-create briefing-done marker so enforce-briefing doesn't block all tests.
# When no HMAC secret is configured, verify_marker falls back to existence-only.
(_ISOLATED_MARKERS / "briefing-done").write_text("test-briefing-done", encoding="utf-8")
_ISOLATED_ENV = {**os.environ, "HOME": str(_ISOLATED_HOME)}


def test(name: str, condition: bool, detail: str = "") -> None:
    global PASS, FAIL
    if condition:
        PASS += 1
        print(f"  ✅ {name}")
    else:
        FAIL += 1
        print(f"  ❌ {name}" + (f" — {detail}" if detail else ""))


def _run(event: str, payload: dict, env: dict | None = None, timeout: int = 15):
    """Run hook_runner.py with a JSON payload and return the completed process."""
    return subprocess.run(
        [sys.executable, str(RUNNER), event],
        input=json.dumps(payload),
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        env=env or _ISOLATED_ENV,
        timeout=timeout,
    )


# ══════════════════════════════════════════════════════════════════════
#  Section 1: BlockEditDistRule (preToolUse via hook_runner)
# ══════════════════════════════════════════════════════════════════════

print("\n🚫 Section 1: block-edit-dist via hook_runner subprocess")

# 1a. Edit targeting browse-ui/dist/ → deny output
r = _run("preToolUse", {
    "toolName": "edit",
    "toolArgs": {"path": "browse-ui/dist/bundle.js", "old_str": "a", "new_str": "b"},
})
test("edit browse-ui/dist/ → non-zero exit (denied)", r.returncode != 0 or "permissionDecision" in r.stdout,
     f"rc={r.returncode}, stdout={r.stdout[:200]}")
test("edit browse-ui/dist/ → deny JSON present", '"deny"' in r.stdout,
     f"stdout={r.stdout[:200]}")
test("edit browse-ui/dist/ → pnpm build in reason", "pnpm build" in r.stdout,
     f"stdout={r.stdout[:200]}")

# 1b. Edit targeting browse-ui/src/ → allowed (exit 0, no deny)
r = _run("preToolUse", {
    "toolName": "edit",
    "toolArgs": {"path": "browse-ui/src/components/Header.tsx", "old_str": "a", "new_str": "b"},
})
test("edit browse-ui/src/ → allowed (no deny JSON)", '"deny"' not in r.stdout,
     f"stdout={r.stdout[:200]}")

# 1c. Create targeting browse-ui/dist/ → deny
r = _run("preToolUse", {
    "toolName": "create",
    "toolArgs": {"path": "browse-ui/dist/chunk.js", "file_text": "// generated"},
})
test("create browse-ui/dist/ → deny JSON present", '"deny"' in r.stdout,
     f"stdout={r.stdout[:200]}")


# ══════════════════════════════════════════════════════════════════════
#  Section 2: BlockUnsafeHtmlRule (preToolUse via hook_runner)
# ══════════════════════════════════════════════════════════════════════

print("\n🛡️  Section 2: block-unsafe-html via hook_runner subprocess")

# 2a. dangerouslySetInnerHTML without sanitize in .tsx → deny
r = _run("preToolUse", {
    "toolName": "edit",
    "toolArgs": {
        "path": "browse-ui/src/Widget.tsx",
        "old_str": "return null;",
        "new_str": "return <div dangerouslySetInnerHTML={{ __html: content }} />;",
    },
})
test("dangerouslySetInnerHTML without sanitize → deny JSON", '"deny"' in r.stdout,
     f"stdout={r.stdout[:300]}")
test("XSS denial mentions sanitize/XSS",
     "sanitize" in r.stdout.lower() or "xss" in r.stdout.lower() or "DOMPurify" in r.stdout,
     f"stdout={r.stdout[:300]}")

# 2b. dangerouslySetInnerHTML WITH DOMPurify.sanitize → allowed
r = _run("preToolUse", {
    "toolName": "edit",
    "toolArgs": {
        "path": "browse-ui/src/Safe.tsx",
        "old_str": "return null;",
        "new_str": "const html = DOMPurify.sanitize(raw); return <div dangerouslySetInnerHTML={{ __html: html }} />;",
    },
})
test("dangerouslySetInnerHTML + DOMPurify → allowed (no deny)", '"deny"' not in r.stdout,
     f"stdout={r.stdout[:300]}")

# 2c. Non-TS/JS file with dangerouslySetInnerHTML → allowed
r = _run("preToolUse", {
    "toolName": "create",
    "toolArgs": {
        "path": "docs/notes.md",
        "file_text": "<!-- dangerouslySetInnerHTML is dangerous -->",
    },
})
test("dangerouslySetInnerHTML in .md → allowed (non-JS)", '"deny"' not in r.stdout,
     f"stdout={r.stdout[:200]}")


# ══════════════════════════════════════════════════════════════════════
#  Section 3: SyntaxGateRule (preToolUse via hook_runner)
# ══════════════════════════════════════════════════════════════════════

print("\n🔧 Section 3: syntax-gate via hook_runner subprocess")

# 3a. create with invalid Python → deny
r = _run("preToolUse", {
    "toolName": "create",
    "toolArgs": {
        "path": "broken.py",
        "file_text": "def foo(\n    syntactically broken !!!",
    },
})
test("create invalid Python → deny JSON", '"deny"' in r.stdout,
     f"stdout={r.stdout[:300]}")
test("Syntax denial mentions SyntaxError or syntax",
     "yntax" in r.stdout,
     f"stdout={r.stdout[:300]}")

# 3b. create with valid Python → allowed
r = _run("preToolUse", {
    "toolName": "create",
    "toolArgs": {
        "path": "valid.py",
        "file_text": "def greet(name: str) -> str:\n    return f'Hello, {name}'\n",
    },
})
test("create valid Python → allowed (no deny)", '"deny"' not in r.stdout,
     f"stdout={r.stdout[:200]}")

# 3c. create non-Python file (even with bad syntax) → allowed
r = _run("preToolUse", {
    "toolName": "create",
    "toolArgs": {
        "path": "data.json",
        "file_text": "{this is not valid json!!!}",
    },
})
test("create invalid JSON (non-Python) → allowed", '"deny"' not in r.stdout,
     f"stdout={r.stdout[:200]}")


# ══════════════════════════════════════════════════════════════════════
#  Section 4: PnpmLockfileGuardRule (preToolUse via hook_runner)
# ══════════════════════════════════════════════════════════════════════

print("\n📦 Section 4: pnpm-lockfile-guard via hook_runner subprocess")

# 4a. git status command (not commit) → allowed
r = _run("preToolUse", {
    "toolName": "bash",
    "toolArgs": {"command": "git status"},
})
test("git status → pnpm guard doesn't trigger", '"permissionDecision"' not in r.stdout or '"deny"' not in r.stdout,
     f"stdout={r.stdout[:200]}")

# 4b. Non-bash tool → allowed (guard only on bash)
r = _run("preToolUse", {
    "toolName": "view",
    "toolArgs": {"path": "browse-ui/package.json"},
})
test("view tool → no lockfile guard (not bash)", '"deny"' not in r.stdout,
     f"stdout={r.stdout[:200]}")

# 4c. git commit command — without real git state, subprocess call returns safely
# (no staged files in isolated env → allow is expected)
r = _run("preToolUse", {
    "toolName": "bash",
    "toolArgs": {"command": "git commit -m 'test'"},
}, env={**_ISOLATED_ENV})
# In the isolated home there are no staged files, so the guard should allow.
test("git commit with no staged files in isolated env → no lockfile deny", '"deny"' not in r.stdout or "pnpm" not in r.stdout,
     f"stdout={r.stdout[:300]}")


# ══════════════════════════════════════════════════════════════════════
#  Section 5: NextjsTypecheckRule (postToolUse via hook_runner)
# ══════════════════════════════════════════════════════════════════════

print("\n🔷 Section 5: nextjs-typecheck-reminder via hook_runner subprocess")

# Set up an isolated HOME with a counter already at 5 so the next edit triggers
_nj_isolated = Path(tempfile.mkdtemp(prefix="test-nj-ep-home-"))
_nj_markers = _nj_isolated / ".copilot" / "markers"
_nj_markers.mkdir(parents=True, exist_ok=True)
(_nj_markers / "ts-edit-count").write_text("5", encoding="utf-8")
# Pre-create briefing marker to bypass enforce-briefing gate (existence-only fallback)
(_nj_markers / "briefing-done").write_text("test-briefing-done", encoding="utf-8")
_nj_env = {**os.environ, "HOME": str(_nj_isolated)}

try:
    # 5a. 6th browse-ui .ts edit → should fire reminder
    r = _run("postToolUse", {
        "toolName": "edit",
        "toolArgs": {"path": "browse-ui/src/api/types.ts"},
    }, env=_nj_env)
    test("6th browse-ui .ts edit → typecheck reminder output", "typecheck" in r.stdout or "pnpm" in r.stdout,
         f"stdout={r.stdout[:300]}")

    # 5b. Non-browse-ui .ts file → no reminder
    r = _run("postToolUse", {
        "toolName": "edit",
        "toolArgs": {"path": "src/utils.ts"},
    }, env=_nj_env)
    test("Non-browse-ui .ts file → no typecheck reminder", "typecheck" not in r.stdout,
         f"stdout={r.stdout[:300]}")
finally:
    shutil.rmtree(_nj_isolated, ignore_errors=True)


# ══════════════════════════════════════════════════════════════════════
#  Section 6: ErrorKBRule (errorOccurred via hook_runner)
# ══════════════════════════════════════════════════════════════════════

print("\n🔍 Section 6: error-kb via hook_runner subprocess")

# 6a. errorOccurred with no query_script → no crash, exit 0
r = _run("errorOccurred", {
    "error": "ModuleNotFoundError: No module named 'nonexistent'",
    "toolName": "bash",
})
test("errorOccurred with error message → no crash (exit 0)", r.returncode == 0,
     f"rc={r.returncode} stderr={r.stderr[:200]}")

# 6b. errorOccurred with dict error → no crash
r = _run("errorOccurred", {
    "error": {"message": "AttributeError: 'NoneType' object has no attribute 'strip'"},
    "toolName": "bash",
})
test("errorOccurred with dict error → no crash", r.returncode == 0,
     f"rc={r.returncode} stderr={r.stderr[:200]}")

# 6c. errorOccurred with empty error → no crash
r = _run("errorOccurred", {"error": "", "toolName": "bash"})
test("errorOccurred with empty error → no crash", r.returncode == 0,
     f"rc={r.returncode} stderr={r.stderr[:200]}")


# ══════════════════════════════════════════════════════════════════════
#  Section 7: SubagentGitGuardRule (preToolUse with real marker file)
# ══════════════════════════════════════════════════════════════════════

print("\n🔐 Section 7: subagent-git-guard via hook_runner subprocess")

# Create an isolated HOME with a fresh subagent marker
_sa_isolated = Path(tempfile.mkdtemp(prefix="test-sa-ep-home-"))
_sa_markers = _sa_isolated / ".copilot" / "markers"
_sa_markers.mkdir(parents=True, exist_ok=True)
# Pre-create briefing marker so enforce-briefing doesn't block the bash tests
(_sa_markers / "briefing-done").write_text("test-briefing-done", encoding="utf-8")
_sa_marker_file = _sa_markers / "dispatched-subagent-active"
_sa_marker_payload = json.dumps({
    "ts": int(time.time()),
    "active_tentacles": ["wave3-tentacle"],
})
_sa_marker_file.write_text(_sa_marker_payload, encoding="utf-8")
_sa_env = {**os.environ, "HOME": str(_sa_isolated)}

try:
    # 7a. git commit with marker present (no HMAC secret → fall back to existence check)
    r = _run("preToolUse", {
        "toolName": "bash",
        "toolArgs": {"command": "git commit -m 'blocked by guard'"},
    }, env=_sa_env)
    # verify_marker falls back to existence-only when no secret configured
    # so the marker IS recognised → guard should block
    test("git commit with subagent marker → deny or exit non-zero",
         '"deny"' in r.stdout or r.returncode != 0,
         f"rc={r.returncode}, stdout={r.stdout[:300]}")

    # 7b. git push with marker present → also blocked
    r = _run("preToolUse", {
        "toolName": "bash",
        "toolArgs": {"command": "git push origin feature"},
    }, env=_sa_env)
    test("git push with subagent marker → deny or exit non-zero",
         '"deny"' in r.stdout or r.returncode != 0,
         f"rc={r.returncode}, stdout={r.stdout[:300]}")

    # 7c. Non-git command with marker present → allowed
    r = _run("preToolUse", {
        "toolName": "bash",
        "toolArgs": {"command": "python3 test_fixes.py"},
    }, env=_sa_env)
    test("Non-git command with marker → allowed (no deny)", '"deny"' not in r.stdout,
         f"stdout={r.stdout[:200]}")

    # 7d. Stale marker file (no active_tentacles) → allowed (zombie)
    _sa_marker_file.write_text(json.dumps({
        "ts": int(time.time()),
        "active_tentacles": [],
    }), encoding="utf-8")
    r = _run("preToolUse", {
        "toolName": "bash",
        "toolArgs": {"command": "git commit -m 'zombie check'"},
    }, env=_sa_env)
    test("Zombie marker (empty active_tentacles) → allowed",
         '"deny"' not in r.stdout or "SUBAGENT" not in r.stdout,
         f"stdout={r.stdout[:300]}")

finally:
    shutil.rmtree(_sa_isolated, ignore_errors=True)


# ══════════════════════════════════════════════════════════════════════
#  Section 8: SessionEndRule (sessionEnd event via hook_runner)
# ══════════════════════════════════════════════════════════════════════

print("\n🔚 Section 8: session-end via hook_runner subprocess")

# 8a. sessionEnd event → no crash, exit 0
r = _run("sessionEnd", {"reason": "normal_exit"})
test("sessionEnd event → no crash (exit 0)", r.returncode == 0,
     f"rc={r.returncode} stderr={r.stderr[:200]}")

# 8b. sessionEnd event with unknown reason → no crash
r = _run("sessionEnd", {"reason": "unexpected_disconnect"})
test("sessionEnd with unexpected_disconnect → no crash", r.returncode == 0,
     f"rc={r.returncode} stderr={r.stderr[:200]}")


# ══════════════════════════════════════════════════════════════════════
#  Section 9: Multiple rules in sequence (no interference)
# ══════════════════════════════════════════════════════════════════════

print("\n🔗 Section 9: Multiple rules — no cross-contamination")

# 9a. preToolUse payload that triggers block-edit-dist should NOT also mention syntax errors
r = _run("preToolUse", {
    "toolName": "edit",
    "toolArgs": {"path": "browse-ui/dist/app.js", "old_str": "x", "new_str": "y"},
})
deny_reason = ""
if r.stdout:
    try:
        out = json.loads(r.stdout.strip().splitlines()[-1])
        deny_reason = out.get("permissionDecisionReason", "")
    except Exception:
        deny_reason = r.stdout
test("block-edit-dist deny does not mention SyntaxError",
     "SyntaxError" not in deny_reason,
     f"reason={deny_reason[:200]}")

# 9b. Valid edit targeting a src/ file should not trigger any deny
r = _run("preToolUse", {
    "toolName": "edit",
    "toolArgs": {"path": "src/utils.py", "old_str": "x = 1", "new_str": "x = 2"},
})
test("Edit src/utils.py → no deny from any rule", '"deny"' not in r.stdout,
     f"stdout={r.stdout[:200]}")


# ══════════════════════════════════════════════════════════════════════
#  Cleanup
# ══════════════════════════════════════════════════════════════════════

shutil.rmtree(_ISOLATED_HOME, ignore_errors=True)

# ══════════════════════════════════════════════════════════════════════
#  Summary
# ══════════════════════════════════════════════════════════════════════

print(f"\n{'=' * 60}")
print(f"Results: {PASS} passed, {FAIL} failed")

if FAIL > 0:
    sys.exit(1)
