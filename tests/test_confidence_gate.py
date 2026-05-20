#!/usr/bin/env python3
"""Regression tests for decision-confidence research gate enforcement."""

import json
import os
import sys
import tempfile
from pathlib import Path
from unittest.mock import patch

if os.name == "nt":
    for _s in (sys.stdout, sys.stderr):
        if hasattr(_s, "reconfigure"):
            _s.reconfigure(encoding="utf-8", errors="replace")

PASS = 0
FAIL = 0
REPO = Path(__file__).parent.parent
sys.path.insert(0, str(REPO / "hooks"))

from rules.confidence_gate import ConfidenceGateRule


def test(name: str, condition: bool, detail: str = "") -> None:
    global PASS, FAIL
    if condition:
        PASS += 1
        print(f"  ✅ {name}")
    else:
        FAIL += 1
        print(f"  ❌ {name}" + (f" — {detail}" if detail else ""))


print("\n🔍 ConfidenceGateRule")

with tempfile.TemporaryDirectory() as tmp:
    root = Path(tmp)
    plan = root / ".github" / "conductor" / "last-plan.json"
    plan.parent.mkdir(parents=True)
    plan.write_text(
        json.dumps(
            {
                "research_gate": {
                    "required": True,
                    "confidence_score": 0.6,
                    "required_confidence": 1.0,
                }
            }
        ),
        encoding="utf-8",
    )

    rule = ConfidenceGateRule()
    with patch("rules.confidence_gate._get_git_root", return_value=root):
        result = rule.evaluate("preToolUse", {"toolName": "edit", "toolArgs": {"path": "src/app.py"}})
        test("open gate blocks edit", result is not None and result.get("permissionDecision") == "deny")
        test("deny message mentions opus research", "opus-class research" in result.get("permissionDecisionReason", ""))

        result = rule.evaluate("preToolUse", {"toolName": "bash", "toolArgs": {"command": "git push origin head"}})
        test("open gate blocks git push", result is not None and result.get("permissionDecision") == "deny")

        result = rule.evaluate(
            "preToolUse", {"toolName": "bash", "toolArgs": {"command": "python conductor.py --json"}}
        )
        test("open gate allows read/research bash", result is None)

    plan.write_text(json.dumps({"research_gate": None}), encoding="utf-8")
    with patch("rules.confidence_gate._get_git_root", return_value=root):
        result = rule.evaluate("preToolUse", {"toolName": "edit", "toolArgs": {"path": "src/app.py"}})
        test("closed gate allows edit", result is None)

    plan.write_text(json.dumps({"research_gate": {"required": True}, "override": True}), encoding="utf-8")
    with patch("rules.confidence_gate._get_git_root", return_value=root):
        result = rule.evaluate("preToolUse", {"toolName": "create", "toolArgs": {"path": "src/app.py"}})
        test("explicit override allows create", result is None)

print(f"\n{'=' * 60}")
print(f"Results: {PASS} passed, {FAIL} failed")

if FAIL > 0:
    sys.exit(1)
