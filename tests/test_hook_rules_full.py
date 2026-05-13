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
result = rule.evaluate(
    "preToolUse",
    {
        "toolName": "edit",
        "toolArgs": {"path": "browse-ui/dist/index.js", "old_str": "x", "new_str": "y"},
    },
)
test("edit browse-ui/dist/index.js → deny", result is not None)
test(
    "edit browse-ui/dist/ → deny has permissionDecision",
    isinstance(result, dict) and result.get("permissionDecision") == "deny",
)
test("deny message mentions pnpm build", "pnpm build" in (result or {}).get("permissionDecisionReason", ""))

# 1b. create targeting browse-ui/dist/ → deny
result = rule.evaluate(
    "preToolUse",
    {
        "toolName": "create",
        "toolArgs": {"path": "browse-ui/dist/chunk.js", "file_text": "var x = 1;"},
    },
)
test("create browse-ui/dist/chunk.js → deny", result is not None and result.get("permissionDecision") == "deny")

# 1c. edit targeting browse-ui/dist/ via absolute path
abs_dist = str(Path.home() / ".copilot" / "tools" / "browse-ui" / "dist" / "app.js")
result = rule.evaluate(
    "preToolUse",
    {
        "toolName": "edit",
        "toolArgs": {"path": abs_dist, "old_str": "a", "new_str": "b"},
    },
)
test("Absolute dist path → deny", result is not None and result.get("permissionDecision") == "deny")

# 1d. edit targeting browse-ui/src/ → allow
result = rule.evaluate(
    "preToolUse",
    {
        "toolName": "edit",
        "toolArgs": {"path": "browse-ui/src/components/Button.tsx", "old_str": "x", "new_str": "y"},
    },
)
test("edit browse-ui/src/ → allow", result is None)

# 1e. edit targeting any other path → allow
result = rule.evaluate(
    "preToolUse",
    {
        "toolName": "edit",
        "toolArgs": {"path": "src/main.py", "old_str": "x", "new_str": "y"},
    },
)
test("edit src/main.py → allow", result is None)

# 1f. path with /browse-ui/dist/ substring anywhere → deny
result = rule.evaluate(
    "preToolUse",
    {
        "toolName": "create",
        "toolArgs": {"path": "/some/deep/browse-ui/dist/bundle.js", "file_text": ""},
    },
)
test("/…/browse-ui/dist/ substring → deny", result is not None and result.get("permissionDecision") == "deny")

# 1g. Missing path → allow (no crash)
result = rule.evaluate(
    "preToolUse",
    {
        "toolName": "edit",
        "toolArgs": {},
    },
)
test("Missing path → allow (no crash)", result is None)

# 1h. Non-dict toolArgs → allow
result = rule.evaluate(
    "preToolUse",
    {
        "toolName": "edit",
        "toolArgs": None,
    },
)
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
result = rule.evaluate(
    "preToolUse",
    {
        "toolName": "edit",
        "toolArgs": {
            "path": "browse-ui/src/Foo.tsx",
            "new_str": "return <div dangerouslySetInnerHTML={{ __html: userContent }} />;",
        },
    },
)
test("dangerouslySetInnerHTML without sanitize → deny", result is not None)
test("XSS deny has permissionDecision=deny", isinstance(result, dict) and result.get("permissionDecision") == "deny")
test(
    "XSS deny message mentions DOMPurify or sanitize",
    "sanitize" in (result or {}).get("permissionDecisionReason", "").lower()
    or "DOMPurify" in (result or {}).get("permissionDecisionReason", ""),
)

# 2b. dangerouslySetInnerHTML WITH DOMPurify.sanitize → allow
result = rule.evaluate(
    "preToolUse",
    {
        "toolName": "edit",
        "toolArgs": {
            "path": "browse-ui/src/Bar.tsx",
            "new_str": "return <div dangerouslySetInnerHTML={{ __html: DOMPurify.sanitize(userContent) }} />;",
        },
    },
)
test("dangerouslySetInnerHTML + DOMPurify.sanitize → allow", result is None)

# 2c. dangerouslySetInnerHTML WITH sanitize() call → allow
result = rule.evaluate(
    "preToolUse",
    {
        "toolName": "edit",
        "toolArgs": {
            "path": "Comp.tsx",
            "new_str": "const clean = sanitize(raw); return <div dangerouslySetInnerHTML={{ __html: clean }} />;",
        },
    },
)
test("dangerouslySetInnerHTML + sanitize() → allow", result is None)

# 2d. dangerouslySetInnerHTML WITH rehype-sanitize → allow
result = rule.evaluate(
    "preToolUse",
    {
        "toolName": "create",
        "toolArgs": {
            "path": "browse-ui/src/Blog.tsx",
            "file_text": "// using rehype-sanitize\nreturn <div dangerouslySetInnerHTML={{ __html: html }} />;",
        },
    },
)
test("dangerouslySetInnerHTML + rehype-sanitize → allow", result is None)

# 2e. Non-JS/TS file (e.g., .py) with dangerouslySetInnerHTML → allow (not checked)
result = rule.evaluate(
    "preToolUse",
    {
        "toolName": "edit",
        "toolArgs": {
            "path": "notes.py",
            "new_str": "# comment: dangerouslySetInnerHTML is an XSS risk",
        },
    },
)
test("dangerouslySetInnerHTML in .py → allow (not JS)", result is None)

# 2f. .jsx file with dangerouslySetInnerHTML (unsanitized) → deny
result = rule.evaluate(
    "preToolUse",
    {
        "toolName": "create",
        "toolArgs": {
            "path": "client/Component.jsx",
            "file_text": "<div dangerouslySetInnerHTML={{__html: raw}} />",
        },
    },
)
test(
    "dangerouslySetInnerHTML in .jsx without sanitize → deny",
    result is not None and result.get("permissionDecision") == "deny",
)

# 2g. .js file → deny
result = rule.evaluate(
    "preToolUse",
    {
        "toolName": "edit",
        "toolArgs": {
            "path": "app.js",
            "new_str": "el.dangerouslySetInnerHTML = { __html: userInput };",
        },
    },
)
test(
    "dangerouslySetInnerHTML in .js without sanitize → deny",
    result is not None and result.get("permissionDecision") == "deny",
)

# 2h. No dangerous content → allow
result = rule.evaluate(
    "preToolUse",
    {
        "toolName": "edit",
        "toolArgs": {
            "path": "browse-ui/src/Safe.tsx",
            "new_str": "return <div>{children}</div>;",
        },
    },
)
test("No dangerouslySetInnerHTML → allow", result is None)

# 2i. Empty new_str → allow
result = rule.evaluate(
    "preToolUse",
    {
        "toolName": "edit",
        "toolArgs": {"path": "app.tsx", "new_str": ""},
    },
)
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
    result = rule.evaluate(
        "preToolUse",
        {
            "toolName": "bash",
            "toolArgs": {"command": "git commit -m 'update deps'"},
        },
    )
test("package.json staged without lockfile → deny", result is not None and result.get("permissionDecision") == "deny")
test("deny message mentions pnpm install", "pnpm install" in (result or {}).get("permissionDecisionReason", ""))
test("deny message mentions pnpm-lock.yaml", "pnpm-lock.yaml" in (result or {}).get("permissionDecisionReason", ""))

# 3b. git commit with both package.json and lockfile staged → allow
with patch.object(
    _pnpm_mod.subprocess,
    "run",
    return_value=_make_git_result(
        [
            "browse-ui/package.json",
            "browse-ui/pnpm-lock.yaml",
        ]
    ),
):
    result = rule.evaluate(
        "preToolUse",
        {
            "toolName": "bash",
            "toolArgs": {"command": "git commit -m 'update deps'"},
        },
    )
test("Both package.json and lockfile staged → allow", result is None)

# 3c. git commit with lockfile only → allow (no package.json)
with patch.object(_pnpm_mod.subprocess, "run", return_value=_make_git_result(["browse-ui/pnpm-lock.yaml"])):
    result = rule.evaluate(
        "preToolUse",
        {
            "toolName": "bash",
            "toolArgs": {"command": "git commit -m 'lock only'"},
        },
    )
test("Only lockfile staged (no package.json) → allow", result is None)

# 3d. Non-git-commit bash command → allow (no git call needed)
result = rule.evaluate(
    "preToolUse",
    {
        "toolName": "bash",
        "toolArgs": {"command": "ls -la"},
    },
)
test("Non-commit command → allow (no subprocess call)", result is None)

# 3e. git commit with no staged files → allow
with patch.object(_pnpm_mod.subprocess, "run", return_value=_make_git_result([])):
    result = rule.evaluate(
        "preToolUse",
        {
            "toolName": "bash",
            "toolArgs": {"command": "git commit -m 'empty'"},
        },
    )
test("No staged files → allow", result is None)

# 3f. Subprocess exception → fail-open (allow)
with patch.object(_pnpm_mod.subprocess, "run", side_effect=OSError("git not found")):
    result = rule.evaluate(
        "preToolUse",
        {
            "toolName": "bash",
            "toolArgs": {"command": "git commit -m 'test'"},
        },
    )
test("Subprocess failure → fail-open (allow)", result is None)

# 3g. git commit --amend also matches
with patch.object(_pnpm_mod.subprocess, "run", return_value=_make_git_result(["browse-ui/package.json"])):
    result = rule.evaluate(
        "preToolUse",
        {
            "toolName": "bash",
            "toolArgs": {"command": "git commit --amend --no-edit"},
        },
    )
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
        result = rule.evaluate(
            "preToolUse",
            {
                "toolName": "bash",
                "toolArgs": {"command": "git commit -m 'feat: add feature'"},
            },
        )
    test("No marker → git commit allowed", result is None)

    # 4b. Fresh marker with active tentacle → git commit blocked
    _marker_payload = json.dumps(
        {
            "ts": int(time.time()),
            "active_tentacles": ["my-tentacle"],
        }
    )
    _tmp_marker.write_text(_marker_payload, encoding="utf-8")
    with (
        patch.object(_sg_mod, "SUBAGENT_MARKER", _tmp_marker),
        patch.object(_sg_mod, "verify_marker", return_value=True),
        patch.object(_sg_mod, "_get_current_git_root", return_value=None),
    ):
        result = rule.evaluate(
            "preToolUse",
            {
                "toolName": "bash",
                "toolArgs": {"command": "git commit -m 'sneaky commit'"},
            },
        )
    test(
        "Fresh marker + active tentacle → git commit blocked",
        result is not None and result.get("permissionDecision") == "deny",
    )
    test("Blocked message mentions SUBAGENT MODE", "SUBAGENT" in (result or {}).get("permissionDecisionReason", ""))

    # 4c. Fresh marker → git push also blocked
    with (
        patch.object(_sg_mod, "SUBAGENT_MARKER", _tmp_marker),
        patch.object(_sg_mod, "verify_marker", return_value=True),
        patch.object(_sg_mod, "_get_current_git_root", return_value=None),
    ):
        result = rule.evaluate(
            "preToolUse",
            {
                "toolName": "bash",
                "toolArgs": {"command": "git push origin main"},
            },
        )
    test("Fresh marker + git push → blocked", result is not None and result.get("permissionDecision") == "deny")

    # 4d. Non-git command → allowed regardless of marker
    with (
        patch.object(_sg_mod, "SUBAGENT_MARKER", _tmp_marker),
        patch.object(_sg_mod, "verify_marker", return_value=True),
        patch.object(_sg_mod, "_get_current_git_root", return_value=None),
    ):
        result = rule.evaluate(
            "preToolUse",
            {
                "toolName": "bash",
                "toolArgs": {"command": "python3 test_security.py"},
            },
        )
    test("Non-git command always passes through", result is None)

    # 4e. Expired marker → fail-open (allowed)
    _expired_payload = json.dumps(
        {
            "ts": int(time.time()) - 20000,  # 5.5 hours ago (beyond 4h TTL)
            "active_tentacles": ["old-tentacle"],
        }
    )
    _tmp_marker.write_text(_expired_payload, encoding="utf-8")
    with (
        patch.object(_sg_mod, "SUBAGENT_MARKER", _tmp_marker),
        patch.object(_sg_mod, "verify_marker", return_value=True),
        patch.object(_sg_mod, "_get_current_git_root", return_value=None),
    ):
        result = rule.evaluate(
            "preToolUse",
            {
                "toolName": "bash",
                "toolArgs": {"command": "git commit -m 'after expiry'"},
            },
        )
    test("Expired marker → fail-open (allowed)", result is None)

    # 4f. Zombie marker (empty active_tentacles) → allowed
    _zombie_payload = json.dumps(
        {
            "ts": int(time.time()),
            "active_tentacles": [],
        }
    )
    _tmp_marker.write_text(_zombie_payload, encoding="utf-8")
    with (
        patch.object(_sg_mod, "SUBAGENT_MARKER", _tmp_marker),
        patch.object(_sg_mod, "verify_marker", return_value=True),
        patch.object(_sg_mod, "_get_current_git_root", return_value=None),
    ):
        result = rule.evaluate(
            "preToolUse",
            {
                "toolName": "bash",
                "toolArgs": {"command": "git commit -m 'cleanup'"},
            },
        )
    test("Zombie marker (empty active_tentacles) → allowed", result is None)

    # 4g. Marker present but HMAC invalid → fail-open (allowed)
    _good_payload = json.dumps(
        {
            "ts": int(time.time()),
            "active_tentacles": ["legit"],
        }
    )
    _tmp_marker.write_text(_good_payload, encoding="utf-8")
    with (
        patch.object(_sg_mod, "SUBAGENT_MARKER", _tmp_marker),
        patch.object(_sg_mod, "verify_marker", return_value=False),
    ):
        result = rule.evaluate(
            "preToolUse",
            {
                "toolName": "bash",
                "toolArgs": {"command": "git commit -m 'unverified'"},
            },
        )
    test("HMAC invalid → fail-open (allowed)", result is None)

    # 4h. Marker with dict-format entries and tentacle name
    _dict_payload = json.dumps(
        {
            "ts": int(time.time()),
            "active_tentacles": [{"name": "my-tentacle", "ts": int(time.time()), "git_root": None}],
        }
    )
    _tmp_marker.write_text(_dict_payload, encoding="utf-8")
    with (
        patch.object(_sg_mod, "SUBAGENT_MARKER", _tmp_marker),
        patch.object(_sg_mod, "verify_marker", return_value=True),
        patch.object(_sg_mod, "_get_current_git_root", return_value=None),
    ):
        result = rule.evaluate(
            "preToolUse",
            {
                "toolName": "bash",
                "toolArgs": {"command": "git commit -m 'blocked again'"},
            },
        )
    test(
        "Dict-format active_tentacles → git commit blocked",
        result is not None and result.get("permissionDecision") == "deny",
    )
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
result = rule.evaluate(
    "preToolUse",
    {
        "toolName": "create",
        "toolArgs": {"path": "new_module.py", "file_text": "def hello():\n    return 'world'\n"},
    },
)
test("create valid Python → allow", result is None)

# 5b. create with invalid Python (SyntaxError) → deny
result = rule.evaluate(
    "preToolUse",
    {
        "toolName": "create",
        "toolArgs": {"path": "bad.py", "file_text": "def foo(\n    broken code here !!!@#\n"},
    },
)
test("create invalid Python → deny", result is not None and result.get("permissionDecision") == "deny")
test(
    "deny message mentions SyntaxError",
    "Syntax" in (result or {}).get("permissionDecisionReason", "")
    or "syntax" in (result or {}).get("permissionDecisionReason", "").lower(),
)

# 5c. create with non-Python file → always allow
result = rule.evaluate(
    "preToolUse",
    {
        "toolName": "create",
        "toolArgs": {"path": "style.css", "file_text": "THIS IS NOT VALID CSS !@#$"},
    },
)
test("create non-.py file → allow (no check)", result is None)

# 5d. create with missing file_text → allow
result = rule.evaluate(
    "preToolUse",
    {
        "toolName": "create",
        "toolArgs": {"path": "empty.py"},
    },
)
test("create .py without file_text → allow", result is None)

# 5e. edit with old_str not found in file → allow (let edit tool raise)
with tempfile.NamedTemporaryFile(suffix=".py", mode="w", encoding="utf-8", delete=False) as f:
    f.write("x = 1\ny = 2\n")
    tmp_py = f.name
try:
    result = rule.evaluate(
        "preToolUse",
        {
            "toolName": "edit",
            "toolArgs": {"path": tmp_py, "old_str": "z = 999", "new_str": "z = 0"},
        },
    )
    test("edit with non-existent old_str → allow (let edit tool fail)", result is None)

    # 5f. edit producing valid replacement → allow
    result = rule.evaluate(
        "preToolUse",
        {
            "toolName": "edit",
            "toolArgs": {"path": tmp_py, "old_str": "x = 1", "new_str": "x = 42"},
        },
    )
    test("edit producing valid Python → allow", result is None)

    # 5g. edit producing syntax error → deny
    result = rule.evaluate(
        "preToolUse",
        {
            "toolName": "edit",
            "toolArgs": {"path": tmp_py, "old_str": "x = 1", "new_str": "def broken(\n"},
        },
    )
    test("edit producing SyntaxError → deny", result is not None and result.get("permissionDecision") == "deny")

finally:
    os.unlink(tmp_py)

# 5h. edit on non-existent file → allow (let edit tool raise)
result = rule.evaluate(
    "preToolUse",
    {
        "toolName": "edit",
        "toolArgs": {"path": "/no/such/file.py", "old_str": "x", "new_str": "y"},
    },
)
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
#  Section 7: AutoBugDetectorRule (issue #86)
# ══════════════════════════════════════════════════════════════════════

print("\n🐛 Section 7: AutoBugDetectorRule")

import rules.auto_bug_detector as _abd_mod
from rules.auto_bug_detector import AutoBugDetectorRule, _detect_categories_edit, _detect_categories_create

rule = AutoBugDetectorRule()

# --- Metadata ---
test("AutoBugDetectorRule name", rule.name == "auto-bug-detector")
test("AutoBugDetectorRule events includes postToolUse", "postToolUse" in rule.events)
test("AutoBugDetectorRule tools includes edit", "edit" in rule.tools)
test("AutoBugDetectorRule tools includes create", "create" in rule.tools)
test("AutoBugDetectorRule NOT preToolUse", "preToolUse" not in rule.events)

# --- _detect_categories_edit: error-handling ---
cats = _detect_categories_edit(
    "x = risky()",
    "try:\n    x = risky()\nexcept ValueError:\n    pass",
)
test(
    "edit: try/except added → error-handling detected",
    any(c == "error-handling" for c, _ in cats),
)

# error-handling already present → no detection
cats = _detect_categories_edit(
    "try:\n    x = a()\nexcept ValueError:\n    pass",
    "try:\n    x = a()\n    y = b()\nexcept ValueError:\n    pass",
)
test(
    "edit: error-handling already present → not re-detected",
    not any(c == "error-handling" for c, _ in cats),
)

cats = _detect_categories_edit(
    "function foo() { return 1; }",
    "function foo() { return 1; } // throw new TypeError if invalid",
)
test(
    "edit: inline comment throw new Error → no error-handling detection",
    not any(c == "error-handling" for c, _ in cats),
)

cats = _detect_categories_edit(
    "function foo() { return fetch(url); }",
    "function foo() { return fetch(url); } // always use .catch() for errors",
)
test(
    "edit: inline comment .catch() → no error-handling detection",
    not any(c == "error-handling" for c, _ in cats),
)

# --- _detect_categories_edit: null-safety ---
cats = _detect_categories_edit(
    "return obj.value",
    "if obj is None:\n    return None\nreturn obj.value",
)
test(
    "edit: is None guard added → null-safety detected",
    any(c == "null-safety" for c, _ in cats),
)

# optional chaining
cats = _detect_categories_edit(
    "return obj.value",
    "return obj?.value",
)
test(
    "edit: ?. added → null-safety detected",
    any(c == "null-safety" for c, _ in cats),
)

# nullish-coalescing in real code → null-safety detected
cats = _detect_categories_edit(
    "const x = foo;",
    "const x = foo ?? bar;",
)
test(
    "edit: ?? in code → null-safety detected",
    any(c == "null-safety" for c, _ in cats),
)

# ?? only in a comment-only line → no null-safety detection (blocker 21 regression)
cats = _detect_categories_edit(
    "return obj.value",
    "# Is this correct?? might be wrong\nreturn obj.value",
)
test(
    "edit: ?? only in comment → no null-safety detection",
    not any(c == "null-safety" for c, _ in cats),
)

# --- _detect_categories_edit: async-fix ---
cats = _detect_categories_edit(
    "def fetch():\n    return requests.get(url)",
    "async def fetch():\n    return await session.get(url)",
)
test(
    "edit: async def + await added → async-fix detected",
    any(c == "async-fix" for c, _ in cats),
)

# --- _detect_categories_edit: type-fix ---
cats = _detect_categories_edit(
    "def greet(name):\n    return name",
    "def greet(name: str) -> str:\n    return name",
)
test(
    "edit: type annotation added → type-fix detected",
    any(c == "type-fix" for c, _ in cats),
)

cats = _detect_categories_edit(
    "def greet(name):\n    return name",
    "def greet(name:str)->str:\n    return name",
)
test(
    "edit: compact type annotation added → type-fix detected",
    any(c == "type-fix" for c, _ in cats),
)

# type-fix already present → no detection
cats = _detect_categories_edit(
    "def greet(name: str):\n    return name",
    "def greet(name: str) -> str:\n    return name.upper()",
)
test(
    "edit: type annotation already present → not re-detected",
    not any(c == "type-fix" for c, _ in cats),
)

# dict literal with string keys → no type-fix detection (blocker 22 regression)
cats = _detect_categories_edit(
    "schema = {}",
    "schema = {'items': list, 'data': dict}",
)
test(
    "edit: dict literal string keys → no type-fix detection",
    not any(c == "type-fix" for c, _ in cats),
)

cats = _detect_categories_edit(
    "schema = {}",
    "schema = {'items' : list, 'data' : dict}",
)
test(
    "edit: spaced dict literal string keys → no type-fix detection",
    not any(c == "type-fix" for c, _ in cats),
)

# config-like `key: None` → no type-fix detection (blocker 22 regression)
cats = _detect_categories_edit(
    "cfg = {}",
    "cfg = {'return_type': None}",
)
test(
    "edit: return_type: None config-like → no type-fix detection",
    not any(c == "type-fix" for c, _ in cats),
)

# --- _detect_categories_edit: no false positive on identical ---
cats = _detect_categories_edit("def foo():\n    return bar()", "def foo():\n    return bar()")
test("edit: identical old/new → no detections", cats == [])

# --- _detect_categories_create ---
# error-handling stays excluded on create (create_conf=0.0)
cats = _detect_categories_create("try:\n    x()\nexcept ValueError:\n    pass\n")
test(
    "create: try/except → no detection (error-handling create_conf=0.0)",
    cats == [],
)

# null-safety is now enabled on create (create_conf=0.62)
cats = _detect_categories_create("if obj is None:\n    return None\n")
test(
    "create: null guard → null-safety detection (create_conf=0.62)",
    any(cat == "null-safety" for cat, _ in cats),
)

# async-fix is now enabled on create (create_conf=0.62)
cats = _detect_categories_create("async def handle():\n    return await fetch()\n")
test(
    "create: async def/await → async-fix detection (create_conf=0.62)",
    any(cat == "async-fix" for cat, _ in cats),
)

# type-fix stays excluded on create
cats = _detect_categories_create("def greet(name: str) -> str:\n    return name\n")
test(
    "create: type annotation → no detection (type-fix create_conf=0.0)",
    not any(cat == "type-fix" for cat, _ in cats),
)

# --- Rule.evaluate: edit with error-handling pattern (learn.py mocked) ---
# Mock _call_learn to return True without actually calling learn.py
with patch.object(_abd_mod, "_call_learn", return_value=True), patch.object(_abd_mod, "_write_learn_done_marker"):
    result = rule.evaluate(
        "postToolUse",
        {
            "toolName": "edit",
            "toolArgs": {
                "path": "src/utils.py",
                "old_str": "x = risky()",
                "new_str": "try:\n    x = risky()\nexcept ValueError:\n    pass",
            },
        },
    )
test(
    "evaluate edit error-handling → non-None info result",
    result is not None and "message" in result,
)
test(
    "evaluate result contains auto-detect message",
    "auto" in (result or {}).get("message", "").lower() or "bug" in (result or {}).get("message", "").lower(),
)

# --- Rule.evaluate: no detection → None ---
with patch.object(_abd_mod, "_call_learn", return_value=True):
    result = rule.evaluate(
        "postToolUse",
        {
            "toolName": "edit",
            "toolArgs": {
                "path": "src/utils.py",
                "old_str": "def foo():\n    return 1",
                "new_str": "def foo():\n    return 2",
            },
        },
    )
test("evaluate edit no pattern → None", result is None)

# --- Rule.evaluate: create with error-handling → None (create_conf still 0.0) ---
with patch.object(_abd_mod, "_call_learn", return_value=True):
    result = rule.evaluate(
        "postToolUse",
        {
            "toolName": "create",
            "toolArgs": {
                "path": "src/new_file.py",
                "file_text": "try:\n    x()\nexcept ValueError:\n    pass\n",
            },
        },
    )
test("evaluate create error-handling → None (create_conf=0.0)", result is None)

# --- Rule.evaluate: create with null-safety → detects (create_conf=0.62) ---
with patch.object(_abd_mod, "_call_learn", return_value=True), patch.object(_abd_mod, "_write_learn_done_marker"):
    result = rule.evaluate(
        "postToolUse",
        {
            "toolName": "create",
            "toolArgs": {
                "path": "src/guard.py",
                "file_text": "def get(obj):\n    if obj is None:\n        return None\n    return obj.value\n",
            },
        },
    )
test("evaluate create null-safety → non-None (create_conf=0.62)", result is not None)

# --- Rule.evaluate: create with async-fix → detects (create_conf=0.62) ---
with patch.object(_abd_mod, "_call_learn", return_value=True), patch.object(_abd_mod, "_write_learn_done_marker"):
    result = rule.evaluate(
        "postToolUse",
        {
            "toolName": "create",
            "toolArgs": {
                "path": "src/handler.py",
                "file_text": "async def handle():\n    return await fetch()\n",
            },
        },
    )
test("evaluate create async-fix → non-None (create_conf=0.62)", result is not None)

# --- Rule.evaluate: session-state path → skipped ---
with patch.object(_abd_mod, "_call_learn", return_value=True):
    result = rule.evaluate(
        "postToolUse",
        {
            "toolName": "edit",
            "toolArgs": {
                "path": str(Path.home() / ".copilot" / "session-state" / "plan.md"),
                "old_str": "x",
                "new_str": "try:\n    x()\nexcept ValueError:\n    pass",
            },
        },
    )
test("evaluate session-state path → skipped (None)", result is None)

# --- Rule.evaluate: missing path → None ---
with patch.object(_abd_mod, "_call_learn", return_value=True):
    result = rule.evaluate(
        "postToolUse",
        {
            "toolName": "edit",
            "toolArgs": {"old_str": "x", "new_str": "try:\n    x()\nexcept ValueError:\n    pass"},
        },
    )
test("evaluate missing path → None", result is None)

# --- Rule.evaluate: non-dict toolArgs → None ---
result = rule.evaluate("postToolUse", {"toolName": "edit", "toolArgs": None})
test("evaluate non-dict toolArgs → None", result is None)

# --- 5-minute bucket semantics ---
# _bucket_id() increments every 300 seconds
t1 = _abd_mod._bucket_id()
test("_bucket_id returns int", isinstance(t1, int) and t1 > 0)
# Calling twice in the same second returns same bucket
t2 = _abd_mod._bucket_id()
test("_bucket_id stable within same second", t1 == t2)

# --- Blocker 23: learn-done marker written once, not per detection (concurrent) ---
_write_marker_call_count = 0


def _counting_marker():
    global _write_marker_call_count
    _write_marker_call_count += 1


_write_marker_call_count = 0
with (
    patch.object(_abd_mod, "_call_learn", return_value=True),
    patch.object(_abd_mod, "_write_learn_done_marker", side_effect=_counting_marker),
):
    rule.evaluate(
        "postToolUse",
        {
            "toolName": "edit",
            "toolArgs": {
                "path": "src/both.py",
                # Edit adds both error-handling AND type annotation → 2 detections
                "old_str": "def greet(name):\n    return name",
                "new_str": "def greet(name: str) -> str:\n    try:\n        return name\n    except ValueError:\n        pass",
            },
        },
    )
test(
    "learn-done marker written once (not per detection) for 2 concurrent detections",
    _write_marker_call_count <= 1,
    f"expected ≤1 calls, got {_write_marker_call_count}",
)

# Confidence scores
_conf_map = {spec["category"]: spec["confidence"] for spec in _abd_mod._CATEGORY_PATTERNS}
test("error-handling confidence >= 0.80", _conf_map["error-handling"] >= 0.80)
test("null-safety confidence >= 0.70", _conf_map["null-safety"] >= 0.70)
test("async-fix confidence >= 0.70", _conf_map["async-fix"] >= 0.70)
test("guard-clause confidence >= 0.70", _conf_map["guard-clause"] >= 0.70)
test("type-fix confidence >= 0.60", _conf_map["type-fix"] >= 0.60)


# ══════════════════════════════════════════════════════════════════════
#  Section 7b: AutoBugDetectorRule — blocker regressions (wave13 follow-up)
# ══════════════════════════════════════════════════════════════════════

print("\n🐛 Section 7b: AutoBugDetectorRule — inline-comment/type-boundary/code-ext regressions")

from rules.auto_bug_detector import _has_null_safety_indicator

# --- Blocker 1: inline trailing comment `??` must NOT fire ---
test(
    "inline trailing ?? in Python comment → no null-safety detection",
    not _has_null_safety_indicator("x = foo  # is this right?? maybe"),
)

# inline trailing `?.` in JS `//` comment → no null-safety detection
test(
    "inline trailing ?. in JS // comment → no null-safety detection",
    not _has_null_safety_indicator("const x = getData(); // no ?. used here"),
)

# Real code-side `??` must still detect
test(
    "?? in code part (before comment) → null-safety detected",
    _has_null_safety_indicator("const x = foo ?? bar;  # assign with fallback"),
)

# Real code-side `?.` must still detect when comment follows
test(
    "?. in code part (before // comment) → null-safety detected",
    _has_null_safety_indicator("const v = obj?.value;  // safe access"),
)

test(
    ".unwrap_or() only in comment → no null-safety detection",
    not _has_null_safety_indicator("# prefer value.unwrap_or(default)"),
)

test(
    ".ok_or() only in comment → no null-safety detection",
    not _has_null_safety_indicator("// prefer result.ok_or(err)"),
)

test(
    ".unwrap_or() in code → null-safety detected",
    _has_null_safety_indicator("return value.unwrap_or(default)"),
)

test(
    ".ok_or() in code → null-safety detected",
    _has_null_safety_indicator("return result.ok_or(err)"),
)

test(
    "if-line trailing comment mentioning is None → no null-safety detection",
    not _has_null_safety_indicator("if condition:  # check if x is None later"),
)

test(
    "if-line trailing // comment mentioning == null → no null-safety detection",
    not _has_null_safety_indicator("if (ready) { // compare == null later"),
)

cats = _detect_categories_edit(
    "if condition:  # check if x is None later\n    return condition\n",
    "if value is None:\n    return None\n",
)
test(
    "edit: old trailing-comment is None must not suppress real new null guard",
    any(c == "null-safety" for c, _ in cats),
)

cats = _detect_categories_edit(
    "if (ready) { // compare == null later\n  return ready;\n}\n",
    "if (value == null) {\n  return fallback;\n}\n",
)
test(
    "edit: old trailing-comment == null must not suppress real new null guard",
    any(c == "null-safety" for c, _ in cats),
)

# --- Blocker 2: type-annotation word boundary (Python already uses `\b`, verify via _detect_categories_edit) ---
# `type: string` must NOT fire type-fix (YAML/OpenAPI value)
cats = _detect_categories_edit("schema = {}", "fields:\n  type: string\n  required: true\n")
test(
    "edit: type: string (YAML value) → no type-fix detection",
    not any(c == "type-fix" for c, _ in cats),
)

cats = _detect_categories_edit("schema = {}", "fields:\n  type: boolean\n")
test(
    "edit: type: boolean (YAML value) → no type-fix detection",
    not any(c == "type-fix" for c, _ in cats),
)

cats = _detect_categories_edit("schema = {}", "fields:\n  type: integer\n")
test(
    "edit: type: integer (YAML value) → no type-fix detection",
    not any(c == "type-fix" for c, _ in cats),
)

# Real annotation must still detect
cats = _detect_categories_edit("def foo(name):\n    pass", "def foo(name: str) -> str:\n    pass")
test(
    "edit: name: str (real annotation) → type-fix detected",
    any(c == "type-fix" for c, _ in cats),
)

# --- Blocker 3: code-extension gate ---
# README.md (non-code) must be skipped by edit handler
with patch.object(_abd_mod, "_call_learn", return_value=True):
    result = rule.evaluate(
        "postToolUse",
        {
            "toolName": "edit",
            "toolArgs": {
                "path": "README.md",
                "old_str": "What?? maybe later",
                "new_str": "const x = foo ?? bar;",
            },
        },
    )
test("evaluate edit on README.md → skipped (None)", result is None)

# YAML files stay eligible for other categories, but type-fix is suppressed there
with patch.object(_abd_mod, "_call_learn", return_value=True):
    result = rule.evaluate(
        "postToolUse",
        {
            "toolName": "edit",
            "toolArgs": {
                "path": "workflow.yaml",
                "old_str": "jobs: {}\n",
                "new_str": "jobs:\n  timeout: int\n",
            },
        },
    )
test("evaluate edit on .yaml short type value → skipped (type-fix suppressed)", result is None)

with patch.object(_abd_mod, "_call_learn", return_value=True):
    result = rule.evaluate(
        "postToolUse",
        {
            "toolName": "edit",
            "toolArgs": {
                "path": "workflow.yml",
                "old_str": "jobs: {}\n",
                "new_str": "jobs:\n  enabled: bool\n",
            },
        },
    )
test("evaluate edit on .yml short type value → skipped (type-fix suppressed)", result is None)

# .md create must be skipped
with patch.object(_abd_mod, "_call_learn", return_value=True):
    result = rule.evaluate(
        "postToolUse",
        {
            "toolName": "create",
            "toolArgs": {
                "path": "docs/notes.md",
                "file_text": "if obj is None:\n    return None\n",
            },
        },
    )
test("evaluate create on .md file → skipped (None)", result is None)

# .py file (code) must still be processed
with patch.object(_abd_mod, "_call_learn", return_value=True), patch.object(_abd_mod, "_write_learn_done_marker"):
    result = rule.evaluate(
        "postToolUse",
        {
            "toolName": "edit",
            "toolArgs": {
                "path": "src/utils.py",
                "old_str": "return obj.value",
                "new_str": "if obj is None:\n    return None\nreturn obj.value",
            },
        },
    )
test("evaluate edit on .py file (code) → still detected (not None)", result is not None)

# .ts file (code) must still be processed
with patch.object(_abd_mod, "_call_learn", return_value=True), patch.object(_abd_mod, "_write_learn_done_marker"):
    result = rule.evaluate(
        "postToolUse",
        {
            "toolName": "edit",
            "toolArgs": {
                "path": "src/app.ts",
                "old_str": "const v = obj.value;",
                "new_str": "const v = obj?.value;",
            },
        },
    )
test("evaluate edit on .ts file (code) → still detected (not None)", result is not None)


print(f"\n{'=' * 60}")
print(f"Results: {PASS} passed, {FAIL} failed")

if FAIL > 0:
    sys.exit(1)
