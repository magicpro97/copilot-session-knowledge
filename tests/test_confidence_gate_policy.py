#!/usr/bin/env python3
"""Static policy checks for confidence-gated skill and conductor templates."""

import json
import os
import sys
from pathlib import Path

if os.name == "nt":
    for _s in (sys.stdout, sys.stderr):
        if hasattr(_s, "reconfigure"):
            _s.reconfigure(encoding="utf-8", errors="replace")

PASS = 0
FAIL = 0
REPO = Path(__file__).parent.parent


def test(name: str, condition: bool, detail: str = "") -> None:
    global PASS, FAIL
    if condition:
        PASS += 1
        print(f"  ✅ {name}")
    else:
        FAIL += 1
        print(f"  ❌ {name}" + (f" — {detail}" if detail else ""))


def read(rel: str) -> str:
    return (REPO / rel).read_text(encoding="utf-8")


print("\n🔍 Confidence-gate policy surface")

rules = json.loads(read("skills/conductor-creator/references/rules-template.json"))
gate_config = rules.get("research_gate_config", {})
test("rules-template enables research gate", gate_config.get("enabled") is True)
test("rules-template requires confidence 1.0", gate_config.get("required_confidence") == 1.0)
test("rules-template uses opus 4.7 validation", gate_config.get("validation_model") == "claude-opus-4.7")

conductor_template = read("skills/conductor-creator/templates/conductor.py")
test("conductor template defines research_gate", "research_gate" in conductor_template)
test("conductor template persists last-plan artifact", "persist_plan" in conductor_template)
test("conductor template blocks below confidence 1.0", "confidence_below_required_threshold" in conductor_template)

required_skill_files = [
    "skills/tentacle-orchestration/SKILL.md",
    "skills/tentacle-orchestration/references/verification-gates.md",
    "skills/tentacle-orchestration/references/decomposition-review.md",
    "skills/agent-creator/SKILL.md",
    "skills/skill-creator/SKILL.md",
    "skills/tentacle-creator/SKILL.md",
    "skills/conductor-creator/SKILL.md",
    "skills/workflow-creator/SKILL.md",
    "skills/task-step-generator/SKILL.md",
    "skills/find-skills/SKILL.md",
    "skills/code-reviewer/SKILL.md",
    "skills/hook-creator/SKILL.md",
    "skills/session-knowledge-creator/SKILL.md",
    "skills/multi-agent-workflow/SKILL.md",
    "skills/agent-stack-router/SKILL.md",
    "skills/session-knowledge/SKILL.md",
]

for rel in required_skill_files:
    text = read(rel)
    test(f"{rel} mentions confidence 1.0", "1.0" in text and "confidence" in text.lower())
    test(
        f"{rel} mentions research/validation",
        ("research" in text.lower() or "validation" in text.lower()),
    )

print(f"\n{'=' * 60}")
print(f"Results: {PASS} passed, {FAIL} failed")

if FAIL > 0:
    sys.exit(1)
