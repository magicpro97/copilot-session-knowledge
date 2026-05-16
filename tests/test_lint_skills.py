#!/usr/bin/env python3
"""
test_lint_skills.py — Focused tests for hooks/lint-skills.py.

Covers:
  - SKILL.md frontmatter accepts `handoffs`
  - Nested handoff blocks do not trigger SK-007 unknown-field warnings
  - Existing skill/schema-confusion checks still fire alongside handoffs

Run: python3 tests/test_lint_skills.py
"""

import importlib.util
import os
import sys
from pathlib import Path

if os.name == "nt":
    for _stream in (sys.stdout, sys.stderr):
        if hasattr(_stream, "reconfigure"):
            _stream.reconfigure(encoding="utf-8", errors="replace")

PASS = 0
FAIL = 0
REPO = Path(__file__).parent.parent
SCRIPT = REPO / "hooks" / "lint-skills.py"


def _test(name: str, condition: bool, detail: str = "") -> None:
    global PASS, FAIL
    if condition:
        PASS += 1
        print(f"  ✅ {name}")
    else:
        FAIL += 1
        print(f"  ❌ {name}" + (f" — {detail}" if detail else ""))


def _load_module(path: Path, mod_name: str):
    spec = importlib.util.spec_from_file_location(mod_name, path)
    mod = importlib.util.module_from_spec(spec)
    saved_argv = sys.argv[:]
    sys.argv = [str(path)]
    try:
        spec.loader.exec_module(mod)
    finally:
        sys.argv = saved_argv
    return mod


lint = _load_module(SCRIPT, "_lint_skills")


print("\n🧭 skill handoffs")

skill_with_handoffs = """\
---
name: sample-skill
description: >
  Use when you need a guided follow-up. Triggers on skill chaining and next-step suggestions.
handoffs:
  - label: Review generated output
    skill: code-reviewer
    prompt: Review the generated output for correctness.
    send: true
  - label: Tighten generated hooks
    skill: hook-creator
    prompt: Tighten the generated hooks after review.
    send: false
---
"""

fields, _ = lint.parse_frontmatter(skill_with_handoffs)
issues = lint.lint_skill_file(Path("skills/sample-skill/SKILL.md"), skill_with_handoffs)

_test("parse_frontmatter captures handoffs field", "handoffs" in fields)
_test(
    "handoffs are allowed on skills",
    not any(issue.code == "SK-007" and "handoffs" in issue.message for issue in issues),
)
_test(
    "nested handoff keys do not trigger unknown-field warnings",
    not any(issue.code == "SK-007" and "skill" in issue.message.lower() for issue in issues),
)


print("\n🧱 existing checks still apply")

skill_with_tools_and_handoffs = """\
---
name: sample-skill
description: >
  Use when you need a guided follow-up. Triggers on skill chaining and next-step suggestions.
tools: [bash]
handoffs:
  - label: Review generated output
    skill: code-reviewer
    prompt: Review the generated output for correctness.
    send: true
---
"""

issues_with_tools = lint.lint_skill_file(
    Path("skills/sample-skill/SKILL.md"),
    skill_with_tools_and_handoffs,
)
_test(
    "tools still errors on skills",
    any(issue.code == "SK-008" for issue in issues_with_tools),
)
_test(
    "handoffs do not suppress tools error",
    any(issue.code == "SK-008" for issue in issues_with_tools)
    and not any(issue.code == "SK-007" and "handoffs" in issue.message for issue in issues_with_tools),
)


print(f"\n{'=' * 50}")
print(f"Results: {PASS} passed, {FAIL} failed")
if FAIL > 0:
    sys.exit(1)
