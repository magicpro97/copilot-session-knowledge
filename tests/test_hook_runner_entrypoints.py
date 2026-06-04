#!/usr/bin/env python3
"""test_hook_runner_entrypoints.py — Subprocess-level tests for hook_runner.py
dispatching managed hook events through the current rule set.

Each test sends a JSON payload via stdin to hook_runner.py and asserts on the
process exit code and stdout.  An isolated HOME directory is used for every
subprocess call so no real audit logs or marker files are written.

Run:
    python3 tests/test_hook_runner_entrypoints.py
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
_ISOLATED_ENV = {**os.environ, "HOME": str(_ISOLATED_HOME), "USERPROFILE": str(_ISOLATED_HOME)}


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
r = _run(
    "preToolUse",
    {
        "toolName": "edit",
        "toolArgs": {"path": "browse-ui/dist/bundle.js", "old_str": "a", "new_str": "b"},
    },
)
test(
    "edit browse-ui/dist/ → non-zero exit (denied)",
    r.returncode != 0 or "permissionDecision" in r.stdout,
    f"rc={r.returncode}, stdout={r.stdout[:200]}",
)
test("edit browse-ui/dist/ → deny JSON present", '"deny"' in r.stdout, f"stdout={r.stdout[:200]}")
test("edit browse-ui/dist/ → pnpm build in reason", "pnpm build" in r.stdout, f"stdout={r.stdout[:200]}")

# 1b. Edit targeting browse-ui/src/ → allowed (exit 0, no deny)
r = _run(
    "preToolUse",
    {
        "toolName": "edit",
        "toolArgs": {"path": "browse-ui/src/components/Header.tsx", "old_str": "a", "new_str": "b"},
    },
)
test("edit browse-ui/src/ → allowed (no deny JSON)", '"deny"' not in r.stdout, f"stdout={r.stdout[:200]}")

# 1c. Create targeting browse-ui/dist/ → deny
r = _run(
    "preToolUse",
    {
        "toolName": "create",
        "toolArgs": {"path": "browse-ui/dist/chunk.js", "file_text": "// generated"},
    },
)
test("create browse-ui/dist/ → deny JSON present", '"deny"' in r.stdout, f"stdout={r.stdout[:200]}")


# ══════════════════════════════════════════════════════════════════════
#  Section 2: BlockUnsafeHtmlRule (preToolUse via hook_runner)
# ══════════════════════════════════════════════════════════════════════

print("\n🛡️  Section 2: block-unsafe-html via hook_runner subprocess")

# 2a. dangerouslySetInnerHTML without sanitize in .tsx → deny
r = _run(
    "preToolUse",
    {
        "toolName": "edit",
        "toolArgs": {
            "path": "browse-ui/src/Widget.tsx",
            "old_str": "return null;",
            "new_str": "return <div dangerouslySetInnerHTML={{ __html: content }} />;",
        },
    },
)
test("dangerouslySetInnerHTML without sanitize → deny JSON", '"deny"' in r.stdout, f"stdout={r.stdout[:300]}")
test(
    "XSS denial mentions sanitize/XSS",
    "sanitize" in r.stdout.lower() or "xss" in r.stdout.lower() or "DOMPurify" in r.stdout,
    f"stdout={r.stdout[:300]}",
)

# 2b. dangerouslySetInnerHTML WITH DOMPurify.sanitize → allowed
r = _run(
    "preToolUse",
    {
        "toolName": "edit",
        "toolArgs": {
            "path": "browse-ui/src/Safe.tsx",
            "old_str": "return null;",
            "new_str": "const html = DOMPurify.sanitize(raw); return <div dangerouslySetInnerHTML={{ __html: html }} />;",
        },
    },
)
test("dangerouslySetInnerHTML + DOMPurify → allowed (no deny)", '"deny"' not in r.stdout, f"stdout={r.stdout[:300]}")

# 2c. Non-TS/JS file with dangerouslySetInnerHTML → allowed
r = _run(
    "preToolUse",
    {
        "toolName": "create",
        "toolArgs": {
            "path": "docs/notes.md",
            "file_text": "<!-- dangerouslySetInnerHTML is dangerous -->",
        },
    },
)
test("dangerouslySetInnerHTML in .md → allowed (non-JS)", '"deny"' not in r.stdout, f"stdout={r.stdout[:200]}")


# ══════════════════════════════════════════════════════════════════════
#  Section 3: SyntaxGateRule (preToolUse via hook_runner)
# ══════════════════════════════════════════════════════════════════════

print("\n🔧 Section 3: syntax-gate via hook_runner subprocess")

# 3a. create with invalid Python → deny
r = _run(
    "preToolUse",
    {
        "toolName": "create",
        "toolArgs": {
            "path": "broken.py",
            "file_text": "def foo(\n    syntactically broken !!!",
        },
    },
)
test("create invalid Python → deny JSON", '"deny"' in r.stdout, f"stdout={r.stdout[:300]}")
test("Syntax denial mentions SyntaxError or syntax", "yntax" in r.stdout, f"stdout={r.stdout[:300]}")

# 3b. create with valid Python → allowed
r = _run(
    "preToolUse",
    {
        "toolName": "create",
        "toolArgs": {
            "path": "valid.py",
            "file_text": "def greet(name: str) -> str:\n    return f'Hello, {name}'\n",
        },
    },
)
test("create valid Python → allowed (no deny)", '"deny"' not in r.stdout, f"stdout={r.stdout[:200]}")

# 3c. create non-Python file (even with bad syntax) → allowed
r = _run(
    "preToolUse",
    {
        "toolName": "create",
        "toolArgs": {
            "path": "data.json",
            "file_text": "{this is not valid json!!!}",
        },
    },
)
test("create invalid JSON (non-Python) → allowed", '"deny"' not in r.stdout, f"stdout={r.stdout[:200]}")


# ══════════════════════════════════════════════════════════════════════
#  Section 4: PnpmLockfileGuardRule (preToolUse via hook_runner)
# ══════════════════════════════════════════════════════════════════════

print("\n📦 Section 4: pnpm-lockfile-guard via hook_runner subprocess")

# 4a. git status command (not commit) → allowed
r = _run(
    "preToolUse",
    {
        "toolName": "bash",
        "toolArgs": {"command": "git status"},
    },
)
test(
    "git status → pnpm guard doesn't trigger",
    '"permissionDecision"' not in r.stdout or '"deny"' not in r.stdout,
    f"stdout={r.stdout[:200]}",
)

# 4b. Non-bash tool → allowed (guard only on bash)
r = _run(
    "preToolUse",
    {
        "toolName": "view",
        "toolArgs": {"path": "browse-ui/package.json"},
    },
)
test("view tool → no lockfile guard (not bash)", '"deny"' not in r.stdout, f"stdout={r.stdout[:200]}")

# 4c. git commit command — without real git state, subprocess call returns safely
# (no staged files in isolated env → allow is expected)
r = _run(
    "preToolUse",
    {
        "toolName": "bash",
        "toolArgs": {"command": "git commit -m 'test'"},
    },
    env={**_ISOLATED_ENV},
)
# In the isolated home there are no staged files, so the guard should allow.
test(
    "git commit with no staged files in isolated env → no lockfile deny",
    '"deny"' not in r.stdout or "pnpm" not in r.stdout,
    f"stdout={r.stdout[:300]}",
)


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
_nj_env = {**os.environ, "HOME": str(_nj_isolated), "USERPROFILE": str(_nj_isolated)}

try:
    # 5a. 6th browse-ui .ts edit → should fire reminder
    r = _run(
        "postToolUse",
        {
            "toolName": "edit",
            "toolArgs": {"path": "browse-ui/src/api/types.ts"},
        },
        env=_nj_env,
    )
    test(
        "6th browse-ui .ts edit → typecheck reminder output",
        "typecheck" in r.stdout or "pnpm" in r.stdout,
        f"stdout={r.stdout[:300]}",
    )

    # 5b. Non-browse-ui .ts file → no reminder
    r = _run(
        "postToolUse",
        {
            "toolName": "edit",
            "toolArgs": {"path": "src/utils.ts"},
        },
        env=_nj_env,
    )
    test("Non-browse-ui .ts file → no typecheck reminder", "typecheck" not in r.stdout, f"stdout={r.stdout[:300]}")
finally:
    shutil.rmtree(_nj_isolated, ignore_errors=True)


# ══════════════════════════════════════════════════════════════════════
#  Section 6: ErrorKBRule (errorOccurred via hook_runner)
# ══════════════════════════════════════════════════════════════════════

print("\n🔍 Section 6: error-kb via hook_runner subprocess")

# 6a. errorOccurred with no query_script → no crash, exit 0
r = _run(
    "errorOccurred",
    {
        "error": "ModuleNotFoundError: No module named 'nonexistent'",
        "toolName": "bash",
    },
)
test(
    "errorOccurred with error message → no crash (exit 0)",
    r.returncode == 0,
    f"rc={r.returncode} stderr={r.stderr[:200]}",
)

# 6b. errorOccurred with dict error → no crash
r = _run(
    "errorOccurred",
    {
        "error": {"message": "AttributeError: 'NoneType' object has no attribute 'strip'"},
        "toolName": "bash",
    },
)
test("errorOccurred with dict error → no crash", r.returncode == 0, f"rc={r.returncode} stderr={r.stderr[:200]}")

# 6c. errorOccurred with empty error → no crash
r = _run("errorOccurred", {"error": "", "toolName": "bash"})
test("errorOccurred with empty error → no crash", r.returncode == 0, f"rc={r.returncode} stderr={r.stderr[:200]}")


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
_sa_marker_payload = json.dumps(
    {
        "ts": int(time.time()),
        "active_tentacles": ["hook-entrypoints-tentacle"],
    }
)
_sa_marker_file.write_text(_sa_marker_payload, encoding="utf-8")
_sa_env = {**os.environ, "HOME": str(_sa_isolated), "USERPROFILE": str(_sa_isolated)}

try:
    # 7a. git commit with marker present (no HMAC secret → fall back to existence check)
    r = _run(
        "preToolUse",
        {
            "toolName": "bash",
            "toolArgs": {"command": "git commit -m 'blocked by guard'"},
        },
        env=_sa_env,
    )
    # verify_marker falls back to existence-only when no secret configured
    # so the marker IS recognised → guard should block
    test(
        "git commit with subagent marker → deny or exit non-zero",
        '"deny"' in r.stdout or r.returncode != 0,
        f"rc={r.returncode}, stdout={r.stdout[:300]}",
    )

    # 7b. git push with marker present → also blocked
    r = _run(
        "preToolUse",
        {
            "toolName": "bash",
            "toolArgs": {"command": "git push origin feature"},
        },
        env=_sa_env,
    )
    test(
        "git push with subagent marker → deny or exit non-zero",
        '"deny"' in r.stdout or r.returncode != 0,
        f"rc={r.returncode}, stdout={r.stdout[:300]}",
    )

    # 7c. Non-git command with marker present → allowed
    r = _run(
        "preToolUse",
        {
            "toolName": "bash",
            "toolArgs": {"command": "python3 test_fixes.py"},
        },
        env=_sa_env,
    )
    test("Non-git command with marker → allowed (no deny)", '"deny"' not in r.stdout, f"stdout={r.stdout[:200]}")

    # 7d. Stale marker file (no active_tentacles) → allowed (zombie)
    _sa_marker_file.write_text(
        json.dumps(
            {
                "ts": int(time.time()),
                "active_tentacles": [],
            }
        ),
        encoding="utf-8",
    )
    r = _run(
        "preToolUse",
        {
            "toolName": "bash",
            "toolArgs": {"command": "git commit -m 'zombie check'"},
        },
        env=_sa_env,
    )
    test(
        "Zombie marker (empty active_tentacles) → allowed",
        '"deny"' not in r.stdout or "SUBAGENT" not in r.stdout,
        f"stdout={r.stdout[:300]}",
    )

finally:
    shutil.rmtree(_sa_isolated, ignore_errors=True)


# ══════════════════════════════════════════════════════════════════════
#  Section 8: SessionEndRule (sessionEnd event via hook_runner)
# ══════════════════════════════════════════════════════════════════════

print("\n🔚 Section 8: session-end via hook_runner subprocess")

# 8a. sessionEnd event → no crash, exit 0
r = _run("sessionEnd", {"reason": "normal_exit"})
test("sessionEnd event → no crash (exit 0)", r.returncode == 0, f"rc={r.returncode} stderr={r.stderr[:200]}")

# 8b. sessionEnd event with unknown reason → no crash
r = _run("sessionEnd", {"reason": "unexpected_disconnect"})
test(
    "sessionEnd with unexpected_disconnect → no crash", r.returncode == 0, f"rc={r.returncode} stderr={r.stderr[:200]}"
)

# 8c. sessionEnd with no goal.json present → no crash (fail-open path)
#     Even though there is no active goal, the hook must exit 0.
r = _run("sessionEnd", {"reason": "no_active_goal"})
test(
    "sessionEnd with no active goal → no crash (exit 0)",
    r.returncode == 0,
    f"rc={r.returncode} stderr={r.stderr[:200]}",
)


# ══════════════════════════════════════════════════════════════════════
#  Section 9: Multiple rules in sequence (no interference)
# ══════════════════════════════════════════════════════════════════════

print("\n🔗 Section 9: Multiple rules — no cross-contamination")

# 9a. preToolUse payload that triggers block-edit-dist should NOT also mention syntax errors
r = _run(
    "preToolUse",
    {
        "toolName": "edit",
        "toolArgs": {"path": "browse-ui/dist/app.js", "old_str": "x", "new_str": "y"},
    },
)
deny_reason = ""
if r.stdout:
    try:
        out = json.loads(r.stdout.strip().splitlines()[-1])
        deny_reason = out.get("permissionDecisionReason", "")
    except Exception:
        deny_reason = r.stdout
test(
    "block-edit-dist deny does not mention SyntaxError", "SyntaxError" not in deny_reason, f"reason={deny_reason[:200]}"
)

# 9b. Valid edit targeting a src/ file should not trigger any deny
r = _run(
    "preToolUse",
    {
        "toolName": "edit",
        "toolArgs": {"path": "src/utils.py", "old_str": "x = 1", "new_str": "x = 2"},
    },
)
test("Edit src/utils.py → no deny from any rule", '"deny"' not in r.stdout, f"stdout={r.stdout[:200]}")


# ══════════════════════════════════════════════════════════════════════
#  Section 10: sessionStart with MEMORY.md injection
# ══════════════════════════════════════════════════════════════════════

print("\n\U0001f4cc Section 10: sessionStart MEMORY.md injection via hook_runner")

# 10a. sessionStart with fresh MEMORY.md in project cwd → output includes memory content
#      Config: no hooks-config.json → default (injection enabled)
_mi_isolated = Path(tempfile.mkdtemp(prefix="test-mi-ep-home-"))
_mi_copilot = _mi_isolated / ".copilot"
_mi_copilot.mkdir(parents=True, exist_ok=True)
(_mi_isolated / ".copilot" / "markers").mkdir(parents=True, exist_ok=True)
# Create a dummy briefing.py so AutoBriefingRule doesn't short-circuit
_mi_tools = _mi_isolated / ".copilot" / "tools"
_mi_tools.mkdir(parents=True, exist_ok=True)
(_mi_tools / "briefing.py").write_text("# dummy briefing\nimport sys\n", encoding="utf-8")
# Put a fresh MEMORY.md in the working dir we'll run from
_mi_cwd = Path(tempfile.mkdtemp(prefix="test-mi-cwd-"))
_mi_memory = _mi_cwd / "MEMORY.md"
_mi_memory.write_text("# Promoted Memory\n\n## Pattern\nUse parameterised SQL always.", encoding="utf-8")
# No hooks-config.json → default enabled
_mi_env = {**os.environ, "HOME": str(_mi_isolated), "USERPROFILE": str(_mi_isolated)}

try:
    r = subprocess.run(
        [sys.executable, str(RUNNER), "sessionStart"],
        input=json.dumps({}),
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        env=_mi_env,
        cwd=str(_mi_cwd),
        timeout=15,
    )
    test(
        "10a: sessionStart with fresh MEMORY.md → exit 0",
        r.returncode == 0,
        f"rc={r.returncode} stderr={r.stderr[:200]}",
    )
    _combined_output10a = r.stdout + r.stderr
    test(
        "10a: sessionStart output contains 'MEMORY'",
        "MEMORY" in _combined_output10a or "parameterised SQL" in _combined_output10a,
        f"stdout={r.stdout[:400]}",
    )
finally:
    shutil.rmtree(str(_mi_isolated), ignore_errors=True)
    shutil.rmtree(str(_mi_cwd), ignore_errors=True)

# 10b. sessionStart with memory_inject_enabled=false in hooks-config.json → no memory content
_mi2_isolated = Path(tempfile.mkdtemp(prefix="test-mi2-ep-home-"))
(_mi2_isolated / ".copilot" / "markers").mkdir(parents=True, exist_ok=True)
# Write hooks-config.json with injection disabled
import json as _json10b

(_mi2_isolated / ".copilot" / "hooks-config.json").write_text(
    _json10b.dumps({"memory_inject_enabled": False}), encoding="utf-8"
)
_mi2_cwd = Path(tempfile.mkdtemp(prefix="test-mi2-cwd-"))
(_mi2_cwd / "MEMORY.md").write_text("# Promoted Memory\n\n## Pattern\nThis must NOT appear.", encoding="utf-8")
_mi2_env = {**os.environ, "HOME": str(_mi2_isolated), "USERPROFILE": str(_mi2_isolated)}

try:
    r = subprocess.run(
        [sys.executable, str(RUNNER), "sessionStart"],
        input=json.dumps({}),
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        env=_mi2_env,
        cwd=str(_mi2_cwd),
        timeout=15,
    )
    test("10b: sessionStart memory_inject_enabled=false → exit 0", r.returncode == 0, f"rc={r.returncode}")
    test(
        "10b: disabled injection → memory text not in output",
        "This must NOT appear." not in r.stdout,
        f"stdout={r.stdout[:400]}",
    )
finally:
    shutil.rmtree(str(_mi2_isolated), ignore_errors=True)
    shutil.rmtree(str(_mi2_cwd), ignore_errors=True)

# 10c. sessionStart with no MEMORY.md → no crash, exit 0
_mi3_isolated = Path(tempfile.mkdtemp(prefix="test-mi3-ep-home-"))
(_mi3_isolated / ".copilot" / "markers").mkdir(parents=True, exist_ok=True)
_mi3_cwd = Path(tempfile.mkdtemp(prefix="test-mi3-cwd-"))  # no MEMORY.md
_mi3_env = {**os.environ, "HOME": str(_mi3_isolated), "USERPROFILE": str(_mi3_isolated)}

try:
    r = subprocess.run(
        [sys.executable, str(RUNNER), "sessionStart"],
        input=json.dumps({}),
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        env=_mi3_env,
        cwd=str(_mi3_cwd),
        timeout=15,
    )
    test(
        "10c: sessionStart without MEMORY.md → no crash (exit 0)",
        r.returncode == 0,
        f"rc={r.returncode} stderr={r.stderr[:200]}",
    )
finally:
    shutil.rmtree(str(_mi3_isolated), ignore_errors=True)
    shutil.rmtree(str(_mi3_cwd), ignore_errors=True)

# 10d. sessionStart with memory_inject_max_tokens=5 in config → content truncated
_mi4_isolated = Path(tempfile.mkdtemp(prefix="test-mi4-ep-home-"))
(_mi4_isolated / ".copilot" / "markers").mkdir(parents=True, exist_ok=True)
(_mi4_isolated / ".copilot" / "hooks-config.json").write_text(
    _json10b.dumps({"memory_inject_enabled": True, "memory_inject_max_tokens": 5}), encoding="utf-8"
)
_mi4_cwd = Path(tempfile.mkdtemp(prefix="test-mi4-cwd-"))
(_mi4_cwd / "MEMORY.md").write_text("A" * 200, encoding="utf-8")
_mi4_env = {**os.environ, "HOME": str(_mi4_isolated), "USERPROFILE": str(_mi4_isolated)}

try:
    r = subprocess.run(
        [sys.executable, str(RUNNER), "sessionStart"],
        input=json.dumps({}),
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        env=_mi4_env,
        cwd=str(_mi4_cwd),
        timeout=15,
    )
    test("10d: sessionStart memory_inject_max_tokens=5 → exit 0", r.returncode == 0, f"rc={r.returncode}")
    _out10d = r.stdout + r.stderr
    test(
        "10d: output does not contain 200 'A's (content was truncated)",
        "A" * 200 not in _out10d,
        f"stdout={r.stdout[:400]}",
    )
finally:
    shutil.rmtree(str(_mi4_isolated), ignore_errors=True)
    shutil.rmtree(str(_mi4_cwd), ignore_errors=True)


# ══════════════════════════════════════════════════════════════════════
#  Section 11: preToolUse info message + later deny → clean JSON on stdout
# ══════════════════════════════════════════════════════════════════════

print("\n⚠️  Section 11: preToolUse info message does not pollute stdout before deny")

# Create an isolated HOME with a read-tracker state simulating a file already read
_rt_isolated = Path(tempfile.mkdtemp(prefix="test-rt-ep-home-"))
_rt_markers = _rt_isolated / ".copilot" / "markers"
_rt_markers.mkdir(parents=True, exist_ok=True)
# Pre-create briefing-done marker so enforce-briefing passes
(_rt_markers / "briefing-done").write_text("test-briefing-done", encoding="utf-8")
_rt_env = {**os.environ, "HOME": str(_rt_isolated), "USERPROFILE": str(_rt_isolated)}

# Pre-populate session state so ReadTrackerRule will emit an info message on second read
_rt_session_state = _rt_markers / "session-state-test-info-deny-sess"
_rt_session_state.write_text(
    json.dumps(
        {
            "files_read": {"/repo/hooks/hook_runner.py": {"count": 1, "tokens": 50, "first_read": 1000000}},
            "total_tokens": 50,
            "thresholds_warned": [],
        }
    ),
    encoding="utf-8",
)
_rt_env2 = {**_rt_env, "COPILOT_AGENT_SESSION_ID": "test-info-deny-sess"}

try:
    # 11a. A preToolUse that yields only an info/warn (e.g. repeat read of a .py file)
    #      must NOT place that message on stdout — only stderr.
    r11a = _run(
        "preToolUse",
        {
            "sessionId": "test-info-deny-sess",
            "toolName": "view",
            "toolArgs": {"path": "/repo/hooks/hook_runner.py"},
        },
        env=_rt_env2,
    )
    test(
        "11a: preToolUse info message → stdout is empty or pure JSON (no plain text)",
        r11a.stdout == "" or r11a.stdout.strip().startswith("{"),
        f"stdout={r11a.stdout[:300]!r}",
    )
    test(
        "11a: preToolUse info message exit code 0 (no deny)",
        r11a.returncode == 0,
        f"rc={r11a.returncode}",
    )

    # 11b. A preToolUse that triggers a deny must produce valid JSON deny on stdout.
    #      Use a deny-triggering payload: edit browse-ui/dist/ → block-edit-dist denies.
    #      Note: ReadTrackerRule only fires on `view`, not `edit`, so no info message
    #      is emitted here — this test verifies the stdout JSON channel is clean for
    #      deny-only scenarios (no plain text mixed into stdout before the deny JSON).
    r11b = _run(
        "preToolUse",
        {
            "sessionId": "test-info-deny-sess",
            "toolName": "edit",
            "toolArgs": {
                "path": "browse-ui/dist/bundle.js",
                "old_str": "a",
                "new_str": "b",
            },
        },
        env=_rt_env2,
    )
    # stdout must contain exactly one parseable JSON object
    stdout_lines = [ln for ln in r11b.stdout.splitlines() if ln.strip()]
    parseable = False
    deny_found = False
    for ln in stdout_lines:
        try:
            obj = json.loads(ln)
            parseable = True
            if obj.get("permissionDecision") == "deny":
                deny_found = True
        except Exception:
            pass
    test(
        "11b: deny present on stdout after any info message",
        deny_found,
        f"stdout={r11b.stdout[:400]!r} lines={stdout_lines}",
    )
    test(
        "11b: stdout lines that look like JSON are actually parseable",
        not stdout_lines or parseable,
        f"unparseable stdout={r11b.stdout[:400]!r}",
    )
    test(
        "11b: no plain-text line precedes the deny JSON on stdout",
        all(
            (lambda _ln: (lambda _s: _s == "" or _s.startswith("{"))(_ln.strip()))(_ln)
            for _ln in r11b.stdout.splitlines()
        ),
        f"mixed stdout={r11b.stdout[:400]!r}",
    )

    test("Section 11 ran without exception", True)

except Exception as e:
    test("Section 11 ran without exception", False, str(e))
finally:
    shutil.rmtree(_rt_isolated, ignore_errors=True)


# ══════════════════════════════════════════════════════════════════════
#  Section 12: sessionStart paused-goal resume banner (issue #185)
# ══════════════════════════════════════════════════════════════════════

print("\n⏸️  Section 12: sessionStart paused-goal resume banner via hook_runner (issue #185)")

import json as _json12  # noqa: E402 (already imported, re-alias for clarity below)

# ── helpers ──────────────────────────────────────────────────────────


def _make_12_home(prefix: str) -> "tuple[Path, dict]":
    """Create an isolated HOME with a dummy briefing.py and markers dir."""
    home = Path(tempfile.mkdtemp(prefix=prefix))
    markers = home / ".copilot" / "markers"
    markers.mkdir(parents=True, exist_ok=True)
    # Pre-sign briefing-done so EnforceBriefingRule never blocks
    (markers / "briefing-done").write_text("test-briefing-done", encoding="utf-8")
    tools = home / ".copilot" / "tools"
    tools.mkdir(parents=True, exist_ok=True)
    # Minimal dummy so AutoBriefingRule.evaluate() doesn't early-exit
    (tools / "briefing.py").write_text(
        "# dummy briefing\nimport sys\nprint('dummy-briefing-output')\n",
        encoding="utf-8",
    )
    env = {**os.environ, "HOME": str(home), "USERPROFILE": str(home)}
    return home, env


def _make_12_cwd(
    home: Path,
    goal_status: "str | None" = "paused",
    goal_title: str = "Test Paused Goal",
    pause_reason: str = "session_end:normal",
    include_breadcrumb: bool = True,
) -> Path:
    """Create a temp cwd with .octogent/breadcrumb and goal.json.

    goal_status=None skips goal.json creation (breadcrumb points at non-existent file).
    include_breadcrumb=False skips breadcrumb creation entirely.
    """
    cwd = Path(tempfile.mkdtemp(prefix="test12-cwd-"))
    octogent = cwd / ".octogent"
    octogent.mkdir(parents=True, exist_ok=True)
    if include_breadcrumb:
        goal_json_path = str(octogent / "goal.json")
        bc = {
            "goal_title": goal_title,
            "goal_id": "g12",
            "pause_reason": pause_reason,
            "resume_command": "sk tentacle goal resume",
            "paused_at": "2026-01-01T00:00:00Z",
            "goal_path": goal_json_path,
        }
        (octogent / "goal-resume-breadcrumb.json").write_text(_json12.dumps(bc), encoding="utf-8")
    if goal_status is not None:
        goal_state = {"status": goal_status, "title": goal_title, "goal_id": "g12"}
        (octogent / "goal.json").write_text(_json12.dumps(goal_state), encoding="utf-8")
    return cwd


# ── 12a: paused breadcrumb + paused goal → banner before briefing header ──

_h12a, _e12a = _make_12_home("test12a-home-")
_cwd12a = _make_12_cwd(_h12a, goal_status="paused", goal_title="My Wave16 Goal")
try:
    r12a = subprocess.run(
        [sys.executable, str(RUNNER), "sessionStart"],
        input=json.dumps({"sessionId": "test-resume-sess-12a"}),
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        env=_e12a,
        cwd=str(_cwd12a),
        timeout=15,
    )
    test(
        "12a: sessionStart with paused breadcrumb → exit 0",
        r12a.returncode == 0,
        f"rc={r12a.returncode} stderr={r12a.stderr[:200]}",
    )
    _out12a = r12a.stdout + r12a.stderr
    test(
        "12a: resume banner present (Paused goal)",
        "Paused goal" in _out12a or "My Wave16 Goal" in _out12a,
        f"combined={_out12a[:500]!r}",
    )
    test("12a: Session briefing header present", "Session briefing" in _out12a, f"combined={_out12a[:500]!r}")
    # Order check: "Paused goal" must appear BEFORE "Session briefing"
    _idx_banner = _out12a.find("Paused goal")
    if _idx_banner == -1:
        _idx_banner = _out12a.find("My Wave16 Goal")
    _idx_header = _out12a.find("Session briefing")
    test(
        "12a: resume banner appears BEFORE Session briefing header",
        0 <= _idx_banner < _idx_header,
        f"banner_pos={_idx_banner} header_pos={_idx_header} out={_out12a[:500]!r}",
    )
    test("12a: resume command present in banner", "sk tentacle goal resume" in _out12a, f"combined={_out12a[:500]!r}")
finally:
    shutil.rmtree(str(_h12a), ignore_errors=True)
    shutil.rmtree(str(_cwd12a), ignore_errors=True)

# ── 12b: breadcrumb present but goal is active (already resumed) → banner suppressed ──

_h12b, _e12b = _make_12_home("test12b-home-")
_cwd12b = _make_12_cwd(_h12b, goal_status="active", goal_title="Resumed Goal")
try:
    r12b = subprocess.run(
        [sys.executable, str(RUNNER), "sessionStart"],
        input=json.dumps({"sessionId": "test-resume-sess-12b"}),
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        env=_e12b,
        cwd=str(_cwd12b),
        timeout=15,
    )
    test(
        "12b: sessionStart with stale breadcrumb → exit 0",
        r12b.returncode == 0,
        f"rc={r12b.returncode} stderr={r12b.stderr[:200]}",
    )
    _out12b = r12b.stdout + r12b.stderr
    test(
        "12b: banner suppressed when goal already resumed (active)",
        "Paused goal" not in _out12b,
        f"unexpected banner in combined={_out12b[:500]!r}",
    )
    test("12b: Session briefing header still present", "Session briefing" in _out12b, f"combined={_out12b[:500]!r}")
finally:
    shutil.rmtree(str(_h12b), ignore_errors=True)
    shutil.rmtree(str(_cwd12b), ignore_errors=True)

# ── 12c: breadcrumb absent → no banner, normal exit 0 ────────────────

_h12c, _e12c = _make_12_home("test12c-home-")
_cwd12c = _make_12_cwd(_h12c, include_breadcrumb=False)
try:
    r12c = subprocess.run(
        [sys.executable, str(RUNNER), "sessionStart"],
        input=json.dumps({"sessionId": "test-resume-sess-12c"}),
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        env=_e12c,
        cwd=str(_cwd12c),
        timeout=15,
    )
    test(
        "12c: sessionStart without breadcrumb → exit 0",
        r12c.returncode == 0,
        f"rc={r12c.returncode} stderr={r12c.stderr[:200]}",
    )
    _out12c = r12c.stdout + r12c.stderr
    test("12c: no resume banner when breadcrumb absent", "Paused goal" not in _out12c, f"combined={_out12c[:400]!r}")
finally:
    shutil.rmtree(str(_h12c), ignore_errors=True)
    shutil.rmtree(str(_cwd12c), ignore_errors=True)

# ── 12d: breadcrumb present, goal.json absent → fail-open (banner shown) ──

_h12d, _e12d = _make_12_home("test12d-home-")
_cwd12d = _make_12_cwd(_h12d, goal_status=None, goal_title="Fail-Open Goal")
try:
    r12d = subprocess.run(
        [sys.executable, str(RUNNER), "sessionStart"],
        input=json.dumps({"sessionId": "test-resume-sess-12d"}),
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        env=_e12d,
        cwd=str(_cwd12d),
        timeout=15,
    )
    test(
        "12d: sessionStart with breadcrumb but no goal.json → exit 0",
        r12d.returncode == 0,
        f"rc={r12d.returncode} stderr={r12d.stderr[:200]}",
    )
    _out12d = r12d.stdout + r12d.stderr
    test(
        "12d: banner shown fail-open when goal.json absent",
        "Paused goal" in _out12d or "Fail-Open Goal" in _out12d,
        f"combined={_out12d[:500]!r}",
    )
finally:
    shutil.rmtree(str(_h12d), ignore_errors=True)
    shutil.rmtree(str(_cwd12d), ignore_errors=True)


# ══════════════════════════════════════════════════════════════════════
#  Section 13: Recursion guard (issue #396)
# ══════════════════════════════════════════════════════════════════════

print("\n── Section 13: Recursion guard (SK_HOOK_ACTIVE) ──")

_rg_home = Path(tempfile.mkdtemp(prefix="test-ep-rg-"))
_rg_markers = _rg_home / ".copilot" / "markers"
_rg_markers.mkdir(parents=True, exist_ok=True)
(_rg_markers / "briefing-done").write_text("test", encoding="utf-8")

# When SK_HOOK_ACTIVE=1 the hook should exit immediately (no output, exit 0).
_rg_env = {**os.environ, "HOME": str(_rg_home), "USERPROFILE": str(_rg_home), "SK_HOOK_ACTIVE": "1"}

_rg_result = subprocess.run(
    [sys.executable, str(RUNNER), "preToolUse"],
    input=json.dumps({"toolName": "edit", "toolArgs": {"path": "foo.py", "new_str": "x"}}),
    capture_output=True,
    text=True,
    encoding="utf-8",
    timeout=10,
    env=_rg_env,
    cwd=str(REPO / "hooks"),
)

test(
    "recursion-guard: SK_HOOK_ACTIVE=1 → exit 0",
    _rg_result.returncode == 0,
    f"rc={_rg_result.returncode}",
)
test(
    "recursion-guard: SK_HOOK_ACTIVE=1 → no stdout output",
    _rg_result.stdout.strip() == "",
    f"stdout={_rg_result.stdout[:200]!r}",
)

shutil.rmtree(str(_rg_home), ignore_errors=True)

# ══════════════════════════════════════════════════════════════════════
#  Section 14: Double-fire deduplication (issue #348)
# ══════════════════════════════════════════════════════════════════════

print("\n── Section 14: Double-fire deduplication ──")

_dd_home = Path(tempfile.mkdtemp(prefix="test-ep-dd-"))
_dd_markers = _dd_home / ".copilot" / "markers"
_dd_markers.mkdir(parents=True, exist_ok=True)
(_dd_markers / "briefing-done").write_text("test", encoding="utf-8")

_dd_env = {**os.environ, "HOME": str(_dd_home), "USERPROFILE": str(_dd_home), "HOOK_DRY_RUN": "1"}


def _run_dedup_event(event: str, payload: dict) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, str(RUNNER), event],
        input=json.dumps(payload),
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=10,
        env=_dd_env,
        cwd=str(REPO / "hooks"),
    )


# First call — should process normally (no dedup marker yet).
_dd_payload = {"toolName": "read", "toolArgs": {"path": "foo.py"}}
_dd_r1 = _run_dedup_event("preToolUse", _dd_payload)
test(
    "dedup: first call exits 0",
    _dd_r1.returncode == 0,
    f"rc={_dd_r1.returncode}",
)

# Dedup marker file should now exist — marker name includes a payload hash suffix.
_dd_marker_found = any(f.name.startswith("hook-dedup-preToolUse") for f in _dd_markers.iterdir() if f.is_file())
test(
    "dedup: marker file written after first call",
    _dd_marker_found,
    f"markers={list(_dd_markers.glob('hook-dedup-*'))}",
)

# Second call within 500 ms — should be deduplicated (no output, exit 0).
_dd_r2 = _run_dedup_event("preToolUse", _dd_payload)
test(
    "dedup: second call within 500 ms exits 0",
    _dd_r2.returncode == 0,
    f"rc={_dd_r2.returncode}",
)
test(
    "dedup: second call within 500 ms produces no stdout",
    _dd_r2.stdout.strip() == "",
    f"stdout={_dd_r2.stdout[:200]!r}",
)

# Wait >500 ms and verify that a third call is processed normally.
time.sleep(0.6)
_dd_r3 = _run_dedup_event("preToolUse", _dd_payload)
test(
    "dedup: call after 600 ms window exits 0 (normal processing)",
    _dd_r3.returncode == 0,
    f"rc={_dd_r3.returncode}",
)

shutil.rmtree(str(_dd_home), ignore_errors=True)

# 14b. Regression: stale hook-dedup-* markers are pruned so the directory does
# not leak one file per unique tool call (previously grew to 130k+ files).
_ddp_home = Path(tempfile.mkdtemp(prefix="test-ep-ddp-"))
_ddp_markers = _ddp_home / ".copilot" / "markers"
_ddp_markers.mkdir(parents=True, exist_ok=True)
_ddp_env = {**os.environ, "HOME": str(_ddp_home), "USERPROFILE": str(_ddp_home), "HOOK_DRY_RUN": "1"}

# Plant a stale dedup marker (mtime 10 s ago → older than the 5 s TTL).
_stale_marker = _ddp_markers / "hook-dedup-preToolUse-deadbeef"
_stale_marker.write_text("0", encoding="utf-8")
_stale_ts = time.time() - 10
os.utime(str(_stale_marker), (_stale_ts, _stale_ts))

# No sweep stamp yet → the first hook invocation must run the sweep.
_ddp_r = subprocess.run(
    [sys.executable, str(RUNNER), "preToolUse"],
    input=json.dumps({"toolName": "read", "toolArgs": {"path": "bar.py"}}),
    capture_output=True,
    text=True,
    encoding="utf-8",
    timeout=10,
    env=_ddp_env,
    cwd=str(REPO / "hooks"),
)
test(
    "dedup-prune: stale hook-dedup marker removed by sweep",
    not _stale_marker.exists(),
    f"stale marker still present; markers={list(_ddp_markers.glob('hook-dedup-*'))}",
)
test(
    "dedup-prune: fresh marker for current call survives sweep",
    any(f.name.startswith("hook-dedup-preToolUse") for f in _ddp_markers.iterdir() if f.is_file()),
    f"markers={list(_ddp_markers.glob('hook-dedup-*'))}",
)
shutil.rmtree(str(_ddp_home), ignore_errors=True)

shutil.rmtree(_ISOLATED_HOME, ignore_errors=True)

# ══════════════════════════════════════════════════════════════════════
#  Summary
# ══════════════════════════════════════════════════════════════════════

print(f"\n{'=' * 60}")
print(f"Results: {PASS} passed, {FAIL} failed")

if FAIL > 0:
    sys.exit(1)
