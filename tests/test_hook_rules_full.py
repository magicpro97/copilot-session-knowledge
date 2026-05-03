#!/usr/bin/env python3
"""test_hook_rules_full.py — Focused unit tests for hook rules: block_edit_dist,
block_unsafe_html, pnpm_lockfile_guard, subagent_guard, and syntax_gate.

Tests instantiate rule objects directly and monkeypatch module-level helpers
where needed (subprocess calls, marker files).  All state is isolated in temp
directories — no real marker files are touched.

Run:
    python3 tests/test_hook_rules_full.py
"""

import ast
import json
import os
import sys
import tempfile
import time
from pathlib import Path
from unittest.mock import MagicMock, patch

if os.name == "nt":
    for _s in (sys.stdout, sys.stderr):
        if hasattr(_s, "reconfigure"):
            _s.reconfigure(encoding="utf-8", errors="replace")

PASS = 0
FAIL = 0
REPO = Path(__file__).parent.parent
sys.path.insert(0, str(REPO / "hooks"))


def test(name: str, condition: bool, detail: str = "") -> None:
    global PASS, FAIL
    if condition:
        PASS += 1
        print(f"  ✅ {name}")
    else:
        FAIL += 1
        print(f"  ❌ {name}" + (f" — {detail}" if detail else ""))


# ══════════════════════════════════════════════════════════════════════
#  Section 1: BlockEditDistRule
# ══════════════════════════════════════════════════════════════════════

print("\n🚫 Section 1: BlockEditDistRule")

from rules.block_edit_dist import BlockEditDistRule

rule = BlockEditDistRule()

# 1a. edit targeting browse-ui/dist/ → deny
result = rule.evaluate("preToolUse", {
    "toolName": "edit",
    "toolArgs": {"path": "browse-ui/dist/index.js", "old_str": "x", "new_str": "y"},
})
test("edit browse-ui/dist/index.js → deny", result is not None)
test("edit browse-ui/dist/ → deny has permissionDecision", isinstance(result, dict) and result.get("permissionDecision") == "deny")
test("deny message mentions pnpm build", "pnpm build" in (result or {}).get("permissionDecisionReason", ""))

# 1b. create targeting browse-ui/dist/ → deny
result = rule.evaluate("preToolUse", {
    "toolName": "create",
    "toolArgs": {"path": "browse-ui/dist/chunk.js", "file_text": "var x = 1;"},
})
test("create browse-ui/dist/chunk.js → deny", result is not None and result.get("permissionDecision") == "deny")

# 1c. edit targeting browse-ui/dist/ via absolute path
abs_dist = str(Path.home() / ".copilot" / "tools" / "browse-ui" / "dist" / "app.js")
result = rule.evaluate("preToolUse", {
    "toolName": "edit",
    "toolArgs": {"path": abs_dist, "old_str": "a", "new_str": "b"},
})
test("Absolute dist path → deny", result is not None and result.get("permissionDecision") == "deny")

# 1d. edit targeting browse-ui/src/ → allow
result = rule.evaluate("preToolUse", {
    "toolName": "edit",
    "toolArgs": {"path": "browse-ui/src/components/Button.tsx", "old_str": "x", "new_str": "y"},
})
test("edit browse-ui/src/ → allow", result is None)

# 1e. edit targeting any other path → allow
result = rule.evaluate("preToolUse", {
    "toolName": "edit",
    "toolArgs": {"path": "src/main.py", "old_str": "x", "new_str": "y"},
})
test("edit src/main.py → allow", result is None)

# 1f. path with /browse-ui/dist/ substring anywhere → deny
result = rule.evaluate("preToolUse", {
    "toolName": "create",
    "toolArgs": {"path": "/some/deep/browse-ui/dist/bundle.js", "file_text": ""},
})
test("/…/browse-ui/dist/ substring → deny", result is not None and result.get("permissionDecision") == "deny")

# 1g. Missing path → allow (no crash)
result = rule.evaluate("preToolUse", {
    "toolName": "edit",
    "toolArgs": {},
})
test("Missing path → allow (no crash)", result is None)

# 1h. Non-dict toolArgs → allow
result = rule.evaluate("preToolUse", {
    "toolName": "edit",
    "toolArgs": None,
})
test("Non-dict toolArgs → allow", result is None)

# 1i. Rule metadata
test("BlockEditDistRule name is 'block-edit-dist'", rule.name == "block-edit-dist")
test("BlockEditDistRule events includes preToolUse", "preToolUse" in rule.events)
test("BlockEditDistRule tools includes edit and create", "edit" in rule.tools and "create" in rule.tools)


# ══════════════════════════════════════════════════════════════════════
#  Section 2: BlockUnsafeHtmlRule
# ══════════════════════════════════════════════════════════════════════

print("\n🛡️  Section 2: BlockUnsafeHtmlRule")

from rules.block_unsafe_html import BlockUnsafeHtmlRule

rule = BlockUnsafeHtmlRule()

# 2a. dangerouslySetInnerHTML without sanitize in .tsx → deny
result = rule.evaluate("preToolUse", {
    "toolName": "edit",
    "toolArgs": {
        "path": "browse-ui/src/Foo.tsx",
        "new_str": 'return <div dangerouslySetInnerHTML={{ __html: userContent }} />;',
    },
})
test("dangerouslySetInnerHTML without sanitize → deny", result is not None)
test("XSS deny has permissionDecision=deny", isinstance(result, dict) and result.get("permissionDecision") == "deny")
test("XSS deny message mentions DOMPurify or sanitize", "sanitize" in (result or {}).get("permissionDecisionReason", "").lower() or "DOMPurify" in (result or {}).get("permissionDecisionReason", ""))

# 2b. dangerouslySetInnerHTML WITH DOMPurify.sanitize → allow
result = rule.evaluate("preToolUse", {
    "toolName": "edit",
    "toolArgs": {
        "path": "browse-ui/src/Bar.tsx",
        "new_str": 'return <div dangerouslySetInnerHTML={{ __html: DOMPurify.sanitize(userContent) }} />;',
    },
})
test("dangerouslySetInnerHTML + DOMPurify.sanitize → allow", result is None)

# 2c. dangerouslySetInnerHTML WITH sanitize() call → allow
result = rule.evaluate("preToolUse", {
    "toolName": "edit",
    "toolArgs": {
        "path": "Comp.tsx",
        "new_str": 'const clean = sanitize(raw); return <div dangerouslySetInnerHTML={{ __html: clean }} />;',
    },
})
test("dangerouslySetInnerHTML + sanitize() → allow", result is None)

# 2d. dangerouslySetInnerHTML WITH rehype-sanitize → allow
result = rule.evaluate("preToolUse", {
    "toolName": "create",
    "toolArgs": {
        "path": "browse-ui/src/Blog.tsx",
        "file_text": "// using rehype-sanitize\nreturn <div dangerouslySetInnerHTML={{ __html: html }} />;",
    },
})
test("dangerouslySetInnerHTML + rehype-sanitize → allow", result is None)

# 2e. Non-JS/TS file (e.g., .py) with dangerouslySetInnerHTML → allow (not checked)
result = rule.evaluate("preToolUse", {
    "toolName": "edit",
    "toolArgs": {
        "path": "notes.py",
        "new_str": "# comment: dangerouslySetInnerHTML is an XSS risk",
    },
})
test("dangerouslySetInnerHTML in .py → allow (not JS)", result is None)

# 2f. .jsx file with dangerouslySetInnerHTML (unsanitized) → deny
result = rule.evaluate("preToolUse", {
    "toolName": "create",
    "toolArgs": {
        "path": "client/Component.jsx",
        "file_text": "<div dangerouslySetInnerHTML={{__html: raw}} />",
    },
})
test("dangerouslySetInnerHTML in .jsx without sanitize → deny", result is not None and result.get("permissionDecision") == "deny")

# 2g. .js file → deny
result = rule.evaluate("preToolUse", {
    "toolName": "edit",
    "toolArgs": {
        "path": "app.js",
        "new_str": "el.dangerouslySetInnerHTML = { __html: userInput };",
    },
})
test("dangerouslySetInnerHTML in .js without sanitize → deny", result is not None and result.get("permissionDecision") == "deny")

# 2h. No dangerous content → allow
result = rule.evaluate("preToolUse", {
    "toolName": "edit",
    "toolArgs": {
        "path": "browse-ui/src/Safe.tsx",
        "new_str": "return <div>{children}</div>;",
    },
})
test("No dangerouslySetInnerHTML → allow", result is None)

# 2i. Empty new_str → allow
result = rule.evaluate("preToolUse", {
    "toolName": "edit",
    "toolArgs": {"path": "app.tsx", "new_str": ""},
})
test("Empty new_str → allow", result is None)

# 2j. Missing toolArgs → allow
result = rule.evaluate("preToolUse", {"toolName": "edit", "toolArgs": None})
test("Non-dict toolArgs → allow (no crash)", result is None)

# 2k. Rule metadata
test("BlockUnsafeHtmlRule name", rule.name == "block-unsafe-html")
test("BlockUnsafeHtmlRule events", "preToolUse" in rule.events)


# ══════════════════════════════════════════════════════════════════════
#  Section 3: PnpmLockfileGuardRule (subprocess mocked)
# ══════════════════════════════════════════════════════════════════════

print("\n📦 Section 3: PnpmLockfileGuardRule")

import rules.pnpm_lockfile_guard as _pnpm_mod
from rules.pnpm_lockfile_guard import PnpmLockfileGuardRule

rule = PnpmLockfileGuardRule()


def _make_git_result(lines):
    """Return a mock CompletedProcess with the given staged file list."""
    m = MagicMock()
    m.stdout = "\n".join(lines)
    m.returncode = 0
    return m


# 3a. git commit with package.json staged but no lockfile → deny
with patch.object(_pnpm_mod.subprocess, "run", return_value=_make_git_result(["browse-ui/package.json"])):
    result = rule.evaluate("preToolUse", {
        "toolName": "bash",
        "toolArgs": {"command": "git commit -m 'update deps'"},
    })
test("package.json staged without lockfile → deny", result is not None and result.get("permissionDecision") == "deny")
test("deny message mentions pnpm install", "pnpm install" in (result or {}).get("permissionDecisionReason", ""))
test("deny message mentions pnpm-lock.yaml", "pnpm-lock.yaml" in (result or {}).get("permissionDecisionReason", ""))

# 3b. git commit with both package.json and lockfile staged → allow
with patch.object(_pnpm_mod.subprocess, "run", return_value=_make_git_result([
    "browse-ui/package.json",
    "browse-ui/pnpm-lock.yaml",
])):
    result = rule.evaluate("preToolUse", {
        "toolName": "bash",
        "toolArgs": {"command": "git commit -m 'update deps'"},
    })
test("Both package.json and lockfile staged → allow", result is None)

# 3c. git commit with lockfile only → allow (no package.json)
with patch.object(_pnpm_mod.subprocess, "run", return_value=_make_git_result(["browse-ui/pnpm-lock.yaml"])):
    result = rule.evaluate("preToolUse", {
        "toolName": "bash",
        "toolArgs": {"command": "git commit -m 'lock only'"},
    })
test("Only lockfile staged (no package.json) → allow", result is None)

# 3d. Non-git-commit bash command → allow (no git call needed)
result = rule.evaluate("preToolUse", {
    "toolName": "bash",
    "toolArgs": {"command": "ls -la"},
})
test("Non-commit command → allow (no subprocess call)", result is None)

# 3e. git commit with no staged files → allow
with patch.object(_pnpm_mod.subprocess, "run", return_value=_make_git_result([])):
    result = rule.evaluate("preToolUse", {
        "toolName": "bash",
        "toolArgs": {"command": "git commit -m 'empty'"},
    })
test("No staged files → allow", result is None)

# 3f. Subprocess exception → fail-open (allow)
with patch.object(_pnpm_mod.subprocess, "run", side_effect=OSError("git not found")):
    result = rule.evaluate("preToolUse", {
        "toolName": "bash",
        "toolArgs": {"command": "git commit -m 'test'"},
    })
test("Subprocess failure → fail-open (allow)", result is None)

# 3g. git commit --amend also matches
with patch.object(_pnpm_mod.subprocess, "run", return_value=_make_git_result(["browse-ui/package.json"])):
    result = rule.evaluate("preToolUse", {
        "toolName": "bash",
        "toolArgs": {"command": "git commit --amend --no-edit"},
    })
test("git commit --amend triggers guard", result is not None and result.get("permissionDecision") == "deny")

# 3h. Rule metadata
test("PnpmLockfileGuardRule name", rule.name == "pnpm-lockfile-guard")
test("PnpmLockfileGuardRule events", "preToolUse" in rule.events)
test("PnpmLockfileGuardRule tools", "bash" in rule.tools)


# ══════════════════════════════════════════════════════════════════════
#  Section 4: SubagentGitGuardRule (marker-based)
# ══════════════════════════════════════════════════════════════════════

print("\n🔐 Section 4: SubagentGitGuardRule")

import rules.subagent_guard as _sg_mod
from rules.subagent_guard import SubagentGitGuardRule

rule = SubagentGitGuardRule()

_tmp_marker_dir = Path(tempfile.mkdtemp(prefix="test-subagent-markers-"))
_tmp_marker = _tmp_marker_dir / "dispatched-subagent-active"

try:
    # 4a. No marker → git commit allowed
    with patch.object(_sg_mod, "SUBAGENT_MARKER", _tmp_marker):
        result = rule.evaluate("preToolUse", {
            "toolName": "bash",
            "toolArgs": {"command": "git commit -m 'feat: add feature'"},
        })
    test("No marker → git commit allowed", result is None)

    # 4b. Fresh marker with active tentacle → git commit blocked
    _marker_payload = json.dumps({
        "ts": int(time.time()),
        "active_tentacles": ["my-tentacle"],
    })
    _tmp_marker.write_text(_marker_payload, encoding="utf-8")
    with patch.object(_sg_mod, "SUBAGENT_MARKER", _tmp_marker), \
         patch.object(_sg_mod, "verify_marker", return_value=True), \
         patch.object(_sg_mod, "_get_current_git_root", return_value=None):
        result = rule.evaluate("preToolUse", {
            "toolName": "bash",
            "toolArgs": {"command": "git commit -m 'sneaky commit'"},
        })
    test("Fresh marker + active tentacle → git commit blocked", result is not None and result.get("permissionDecision") == "deny")
    test("Blocked message mentions SUBAGENT MODE", "SUBAGENT" in (result or {}).get("permissionDecisionReason", ""))

    # 4c. Fresh marker → git push also blocked
    with patch.object(_sg_mod, "SUBAGENT_MARKER", _tmp_marker), \
         patch.object(_sg_mod, "verify_marker", return_value=True), \
         patch.object(_sg_mod, "_get_current_git_root", return_value=None):
        result = rule.evaluate("preToolUse", {
            "toolName": "bash",
            "toolArgs": {"command": "git push origin main"},
        })
    test("Fresh marker + git push → blocked", result is not None and result.get("permissionDecision") == "deny")

    # 4d. Non-git command → allowed regardless of marker
    with patch.object(_sg_mod, "SUBAGENT_MARKER", _tmp_marker), \
         patch.object(_sg_mod, "verify_marker", return_value=True), \
         patch.object(_sg_mod, "_get_current_git_root", return_value=None):
        result = rule.evaluate("preToolUse", {
            "toolName": "bash",
            "toolArgs": {"command": "python3 test_security.py"},
        })
    test("Non-git command always passes through", result is None)

    # 4e. Expired marker → fail-open (allowed)
    _expired_payload = json.dumps({
        "ts": int(time.time()) - 20000,  # 5.5 hours ago (beyond 4h TTL)
        "active_tentacles": ["old-tentacle"],
    })
    _tmp_marker.write_text(_expired_payload, encoding="utf-8")
    with patch.object(_sg_mod, "SUBAGENT_MARKER", _tmp_marker), \
         patch.object(_sg_mod, "verify_marker", return_value=True), \
         patch.object(_sg_mod, "_get_current_git_root", return_value=None):
        result = rule.evaluate("preToolUse", {
            "toolName": "bash",
            "toolArgs": {"command": "git commit -m 'after expiry'"},
        })
    test("Expired marker → fail-open (allowed)", result is None)

    # 4f. Zombie marker (empty active_tentacles) → allowed
    _zombie_payload = json.dumps({
        "ts": int(time.time()),
        "active_tentacles": [],
    })
    _tmp_marker.write_text(_zombie_payload, encoding="utf-8")
    with patch.object(_sg_mod, "SUBAGENT_MARKER", _tmp_marker), \
         patch.object(_sg_mod, "verify_marker", return_value=True), \
         patch.object(_sg_mod, "_get_current_git_root", return_value=None):
        result = rule.evaluate("preToolUse", {
            "toolName": "bash",
            "toolArgs": {"command": "git commit -m 'cleanup'"},
        })
    test("Zombie marker (empty active_tentacles) → allowed", result is None)

    # 4g. Marker present but HMAC invalid → fail-open (allowed)
    _good_payload = json.dumps({
        "ts": int(time.time()),
        "active_tentacles": ["legit"],
    })
    _tmp_marker.write_text(_good_payload, encoding="utf-8")
    with patch.object(_sg_mod, "SUBAGENT_MARKER", _tmp_marker), \
         patch.object(_sg_mod, "verify_marker", return_value=False):
        result = rule.evaluate("preToolUse", {
            "toolName": "bash",
            "toolArgs": {"command": "git commit -m 'unverified'"},
        })
    test("HMAC invalid → fail-open (allowed)", result is None)

    # 4h. Marker with dict-format entries and tentacle name
    _dict_payload = json.dumps({
        "ts": int(time.time()),
        "active_tentacles": [{"name": "my-tentacle", "ts": int(time.time()), "git_root": None}],
    })
    _tmp_marker.write_text(_dict_payload, encoding="utf-8")
    with patch.object(_sg_mod, "SUBAGENT_MARKER", _tmp_marker), \
         patch.object(_sg_mod, "verify_marker", return_value=True), \
         patch.object(_sg_mod, "_get_current_git_root", return_value=None):
        result = rule.evaluate("preToolUse", {
            "toolName": "bash",
            "toolArgs": {"command": "git commit -m 'blocked again'"},
        })
    test("Dict-format active_tentacles → git commit blocked", result is not None and result.get("permissionDecision") == "deny")
    deny_msg = (result or {}).get("permissionDecisionReason", "")
    test("Deny message mentions tentacle name 'my-tentacle'", "my-tentacle" in deny_msg)

finally:
    import shutil
    shutil.rmtree(_tmp_marker_dir, ignore_errors=True)

# 4i. Rule metadata
test("SubagentGitGuardRule name", rule.name == "subagent-git-guard")
test("SubagentGitGuardRule events", "preToolUse" in rule.events)
test("SubagentGitGuardRule tools", "bash" in rule.tools)


# ══════════════════════════════════════════════════════════════════════
#  Section 5: SyntaxGateRule
# ══════════════════════════════════════════════════════════════════════

print("\n🔧 Section 5: SyntaxGateRule")

from rules.syntax_gate import SyntaxGateRule

rule = SyntaxGateRule()

# 5a. create with valid Python → allow
result = rule.evaluate("preToolUse", {
    "toolName": "create",
    "toolArgs": {"path": "new_module.py", "file_text": "def hello():\n    return 'world'\n"},
})
test("create valid Python → allow", result is None)

# 5b. create with invalid Python (SyntaxError) → deny
result = rule.evaluate("preToolUse", {
    "toolName": "create",
    "toolArgs": {"path": "bad.py", "file_text": "def foo(\n    broken code here !!!@#\n"},
})
test("create invalid Python → deny", result is not None and result.get("permissionDecision") == "deny")
test("deny message mentions SyntaxError", "Syntax" in (result or {}).get("permissionDecisionReason", "") or "syntax" in (result or {}).get("permissionDecisionReason", "").lower())

# 5c. create with non-Python file → always allow
result = rule.evaluate("preToolUse", {
    "toolName": "create",
    "toolArgs": {"path": "style.css", "file_text": "THIS IS NOT VALID CSS !@#$"},
})
test("create non-.py file → allow (no check)", result is None)

# 5d. create with missing file_text → allow
result = rule.evaluate("preToolUse", {
    "toolName": "create",
    "toolArgs": {"path": "empty.py"},
})
test("create .py without file_text → allow", result is None)

# 5e. edit with old_str not found in file → allow (let edit tool raise)
with tempfile.NamedTemporaryFile(suffix=".py", mode="w", encoding="utf-8", delete=False) as f:
    f.write("x = 1\ny = 2\n")
    tmp_py = f.name
try:
    result = rule.evaluate("preToolUse", {
        "toolName": "edit",
        "toolArgs": {"path": tmp_py, "old_str": "z = 999", "new_str": "z = 0"},
    })
    test("edit with non-existent old_str → allow (let edit tool fail)", result is None)

    # 5f. edit producing valid replacement → allow
    result = rule.evaluate("preToolUse", {
        "toolName": "edit",
        "toolArgs": {"path": tmp_py, "old_str": "x = 1", "new_str": "x = 42"},
    })
    test("edit producing valid Python → allow", result is None)

    # 5g. edit producing syntax error → deny
    result = rule.evaluate("preToolUse", {
        "toolName": "edit",
        "toolArgs": {"path": tmp_py, "old_str": "x = 1", "new_str": "def broken(\n"},
    })
    test("edit producing SyntaxError → deny", result is not None and result.get("permissionDecision") == "deny")

finally:
    os.unlink(tmp_py)

# 5h. edit on non-existent file → allow (let edit tool raise)
result = rule.evaluate("preToolUse", {
    "toolName": "edit",
    "toolArgs": {"path": "/no/such/file.py", "old_str": "x", "new_str": "y"},
})
test("edit non-existent file → allow", result is None)

# 5i. Non-dict toolArgs → allow
result = rule.evaluate("preToolUse", {"toolName": "create", "toolArgs": "string"})
test("Non-dict toolArgs → allow", result is None)

# 5j. Rule metadata
test("SyntaxGateRule name", rule.name == "syntax-gate")
test("SyntaxGateRule events", "preToolUse" in rule.events)
test("SyntaxGateRule covers edit and create", "edit" in rule.tools and "create" in rule.tools)

# ══════════════════════════════════════════════════════════════════════
#  Section 6: _compile_content helper
# ══════════════════════════════════════════════════════════════════════

print("\n🔍 Section 6: SyntaxGateRule._compile_content helper")

# 6a. Valid Python
err = SyntaxGateRule._compile_content("x = 1\n", "test.py")
test("_compile_content valid Python → None", err is None)

# 6b. SyntaxError
err = SyntaxGateRule._compile_content("def broken(\n", "test.py")
test("_compile_content invalid Python → error string", err is not None and len(err) > 0)

# 6c. Empty string is valid Python
err = SyntaxGateRule._compile_content("", "empty.py")
test("_compile_content empty string → None", err is None)

# 6d. Unicode content compiles fine
err = SyntaxGateRule._compile_content("# 日本語コメント\nx = 1\n", "unicode.py")
test("_compile_content unicode content → None", err is None)

# 6e. Non-.py label uses .py suffix anyway (extension from label)
err = SyntaxGateRule._compile_content("1 + 1\n", "module")
test("_compile_content valid expression → None", err is None)


# ══════════════════════════════════════════════════════════════════════
#  Summary
# ══════════════════════════════════════════════════════════════════════

print(f"\n{'=' * 60}")
print(f"Results: {PASS} passed, {FAIL} failed")

if FAIL > 0:
    sys.exit(1)
