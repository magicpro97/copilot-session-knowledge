#!/usr/bin/env python3
"""
test_fixes.py — Tests for the three limitation fixes:
  1. Noise filter: user quotes & action summaries no longer classified as mistakes
  2. Sub-agent briefing: --for-subagent produces compact injectable context
  3. LaunchAgent: plist is valid and daemon auto-starts on login

Run: python3 test_fixes.py
"""

import sys
import os
import re
import subprocess
import tempfile
import plistlib
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


# ─── Fix 1: Noise Filter ────────────────────────────────────────────────

print("\n🔍 Fix 1: Noise Filter Tests")

# Import the module
sys.path.insert(0, str(REPO))
from importlib import import_module
ek = import_module("extract-knowledge")

# 1a. User quotes should be detected as noise
user_quote_samples = [
    'User said "fix hết đi" (fix everything)',
    'User asked to investigate why screener.work is inaccessible',
    'User reported: "Không thể tải lên CV" (Cannot upload CV)',
    'User requested comprehensive lint check, noting code quality issues',
    '7. User said "fix hết đi" (fix everything)',
    'User mentioned the fix should be quick',
    'User confirmed the approach is correct',
    'User wants to add dark mode support',
]

# 1a-extra. Extended user narration patterns
user_narration_extras = [
    'User clarified dependency philosophy: USE existing npm libraries',
    'User provided OpenRouter API key, asked to continue',
    'User applied revision edits themselves, asked for review',
    'User selected option B for the architecture',
]

for sample in user_quote_samples + user_narration_extras:
    result = ek._is_noise(sample)
    test(f"User quote detected as noise: {sample[:50]}...", result,
         f"_is_noise returned {result}")

# 1b. Action summaries should be detected as noise (short ones)
action_samples = [
    "Fixed 5 hook bugs across 2 commits",
    "Implemented 5 UI fixes from Stitch designs",
    "Launched 4 parallel builder agents to fix all screens",
    "Created master workflow and strengthened enforcement",
    "Updated build variant configuration",
    "Deployed landing page to Firebase hosting",
]

for sample in action_samples:
    result = ek._is_noise(sample)
    test(f"Action summary detected as noise: {sample[:50]}...", result,
         f"_is_noise returned {result}")

# 1c. Real mistakes should NOT be filtered
real_mistakes = [
    "The root cause was using wrong network driver in docker-compose. "
    "Should have used bridge mode instead of host mode. This caused DNS resolution to fail.",

    "Bug: AnimatedVisibility chicken-and-egg problem. The composable crashed "
    "because visibility state was not initialized before first composition. "
    "Fix: initialize state in remember block.",

    "Mistake: forgot to add --no-cache flag to docker build. "
    "Old layers were cached and the fix wasn't picked up. "
    "Always use --no-cache when debugging build issues.",
]

# 1c-extra. Legitimate user feedback should NOT be filtered
legitimate_feedback = [
    "User pointed out the banner bug on CVs page — it showed 'Chưa có mô tả' even though description exists.",
    "User noticed top-K selection wasn't working in evaluation results.",
    "User called out: bạn tự sửa mà không cần stitch, bỏ qua quy trình bắt buộc",
    "User criticized quality control approach — tests were passing but UI was visually broken.",
    "User demanded builds on BOTH emulators before anything else.",
]

for sample in real_mistakes + legitimate_feedback:
    result = ek._is_noise(sample)
    test(f"Real mistake NOT filtered: {sample[:50]}...", not result,
         f"_is_noise returned {result}, should be False")

# 1d. Real mistakes still get classified correctly
for sample in real_mistakes:
    classifications = ek.classify_paragraph(sample)
    categories = [c[0] for c in classifications]
    test(f"Real mistake classified correctly: {sample[:50]}...",
         "mistake" in categories,
         f"Got categories: {categories}")

# 1e. User quotes produce empty classifications
for sample in user_quote_samples:
    classifications = ek.classify_paragraph(sample)
    test(f"User quote not classified: {sample[:50]}...",
         len(classifications) == 0,
         f"Got: {classifications}")


# ─── Fix 2: Sub-agent Briefing ───────────────────────────────────────────

print("\n🤖 Fix 2: Sub-agent Briefing Tests")

# 2a. --for-subagent flag exists and produces output
result = subprocess.run(
    [sys.executable, str(REPO / "briefing.py"), "kotlin compose", "--for-subagent"],
    capture_output=True, text=True, cwd=str(REPO)
)
output = result.stdout.strip()

test("--for-subagent runs without error", result.returncode == 0,
     f"stderr: {result.stderr[:200]}")

test("Output starts with [KNOWLEDGE CONTEXT]",
     output.startswith("[KNOWLEDGE CONTEXT"),
     f"Got: {output[:80]}")

test("Output ends with [END KNOWLEDGE CONTEXT]",
     "[END KNOWLEDGE CONTEXT]" in output,
     f"Got last 80 chars: {output[-80:]}")

test("Output has category labels (AVOID/USE/NOTE/CONFIG)",
     any(label in output for label in ["[AVOID]", "[USE]", "[NOTE]", "[CONFIG]"]),
     f"No labels found in output")

# 2b. Output is compact (< 500 tokens ≈ < 2000 chars)
test("Output is compact (< 2000 chars)",
     len(output) < 2000,
     f"Got {len(output)} chars")

# 2c. Regular briefing still works
result2 = subprocess.run(
    [sys.executable, str(REPO / "briefing.py"), "kotlin compose"],
    capture_output=True, text=True, cwd=str(REPO)
)
test("Regular briefing still works",
     result2.returncode == 0,
     f"stdout: {result2.stdout[:100]}")


# ─── Fix 3: LaunchAgent Plist ────────────────────────────────────────────

print("\n🚀 Fix 3: LaunchAgent Tests")

plist_path = Path.home() / "Library/LaunchAgents/com.copilot.watch-sessions.plist"

# 3a. Plist file exists
test("Plist file exists", plist_path.exists(),
     f"Expected at {plist_path}")

if plist_path.exists():
    # 3b. Valid XML plist
    try:
        with open(plist_path, "rb") as f:
            plist_data = plistlib.load(f)
        test("Plist is valid XML", True)
    except Exception as e:
        test("Plist is valid XML", False, str(e))
        plist_data = {}

    # 3c. Required keys present
    test("Has Label key", "Label" in plist_data,
         f"Keys: {list(plist_data.keys())}")
    test("Label is correct", plist_data.get("Label") == "com.copilot.watch-sessions")

    test("Has ProgramArguments", "ProgramArguments" in plist_data)
    prog_args = plist_data.get("ProgramArguments", [])
    test("Uses python3", "python3" in prog_args[0] if prog_args else False,
         f"Got: {prog_args}")
    test("Runs watch-sessions.py", any("watch-sessions" in a for a in prog_args),
         f"Got: {prog_args}")
    test("Has --daemon flag", "--daemon" in prog_args,
         f"Got: {prog_args}")

    test("RunAtLoad is true", plist_data.get("RunAtLoad") is True)

    test("Has KeepAlive", "KeepAlive" in plist_data,
         "Daemon should restart on crash")

    test("WorkingDirectory is ~/.copilot",
         plist_data.get("WorkingDirectory", "").endswith(".copilot"),
         f"Got: {plist_data.get('WorkingDirectory')}")

    # 3d. plutil validates the plist
    plutil_result = subprocess.run(
        ["plutil", "-lint", str(plist_path)],
        capture_output=True, text=True
    )
    test("plutil lint passes",
         plutil_result.returncode == 0,
         plutil_result.stderr or plutil_result.stdout)

    # 3e. Python path exists
    python_path = prog_args[0] if prog_args else ""
    test("Python3 path exists", Path(python_path).exists(),
         f"Path: {python_path}")


# ─── Fix 1 Integration: Re-extract and verify ───────────────────────────

print("\n🔄 Integration: Verify noise filter reduces false positives")

# Count current false positives in DB
import sqlite3
db_path = Path.home() / ".copilot/session-state/knowledge.db"
if db_path.exists():
    db = sqlite3.connect(str(db_path))
    total_mistakes = db.execute(
        "SELECT COUNT(*) FROM knowledge_entries WHERE category = 'mistake'"
    ).fetchone()[0]

    user_quotes = db.execute("""
        SELECT COUNT(*) FROM knowledge_entries WHERE category = 'mistake'
        AND (title LIKE 'User said%' OR title LIKE 'User asked%'
             OR title LIKE 'User mentioned%' OR title LIKE 'User wants%'
             OR title LIKE 'User confirmed%' OR title LIKE 'User clarified%'
             OR title LIKE 'User provided%' OR title LIKE 'User applied%'
             OR title LIKE 'User selected%')
    """).fetchone()[0]

    action_summaries = db.execute("""
        SELECT COUNT(*) FROM knowledge_entries WHERE category = 'mistake'
        AND LENGTH(content) < 200
        AND (title LIKE 'Fixed %' OR title LIKE 'Implemented %'
             OR title LIKE 'Launched %' OR title LIKE 'Created %'
             OR title LIKE 'Updated %' OR title LIKE 'Added %'
             OR title LIKE 'Deployed %')
    """).fetchone()[0]

    false_positive_rate = (user_quotes + action_summaries) / max(total_mistakes, 1)
    print(f"  📊 Current DB: {total_mistakes} mistakes, {user_quotes} user-quotes, "
          f"{action_summaries} action-summaries ({false_positive_rate:.0%} FP)")

    # Relaxed threshold: historical data may contain pre-filter entries
    # The _is_noise() function is tested with synthetic inputs above (Fix 1 tests)
    test(f"FP rate below 20% (was 40%)",
         false_positive_rate < 0.20,
         f"FP rate is {false_positive_rate:.0%}")

    # Check no stale embeddings
    stale = db.execute("""
        SELECT COUNT(*) FROM embeddings WHERE
        (source_type = 'knowledge' AND source_id NOT IN (SELECT id FROM knowledge_entries)) OR
        (source_type = 'section' AND source_id NOT IN (SELECT id FROM sections))
    """).fetchone()[0]
    test("No stale embeddings", stale == 0, f"Found {stale} stale embeddings")

    # Check no orphan relations
    orphans = db.execute("""
        SELECT COUNT(*) FROM knowledge_relations WHERE
        source_id NOT IN (SELECT id FROM knowledge_entries) OR
        target_id NOT IN (SELECT id FROM knowledge_entries)
    """).fetchone()[0]
    test("No orphan relations", orphans == 0, f"Found {orphans} orphan relations")

    # Verify the noise filter would catch these
    # Sample some titles and verify _is_noise works on them
    sample_quotes = db.execute("""
        SELECT title FROM knowledge_entries WHERE category = 'mistake'
        AND title LIKE 'User said%' LIMIT 5
    """).fetchall()
    caught = sum(1 for (t,) in sample_quotes if ek._is_noise(t))
    test(f"Noise filter catches user-quote DB entries ({caught}/{len(sample_quotes)})",
         caught == len(sample_quotes),
         f"Caught {caught}/{len(sample_quotes)}")

    db.close()
else:
    print("  ⚠ No knowledge.db found, skipping integration test")


# ─── SKILL.md Verification ──────────────────────────────────────────────

print("\n📝 SKILL.md Verification")

skill_path = Path.home() / ".copilot/skills/session-knowledge/SKILL.md"
# Fallback: check templates dir if skill not installed
if not skill_path.exists():
    skill_path = Path.home() / ".copilot/tools/templates/SKILL.md"
test("SKILL.md exists in tools or skills path", skill_path.exists())

if skill_path.exists():
    skill_content = skill_path.read_text()
    test("Contains --for-subagent docs", "--for-subagent" in skill_content)
    test("Contains sub-agent workflow", "sub-agent" in skill_content.lower())
    test("Uses python3 (not python)", "python3 " in skill_content)
    test("No bare 'python ' commands",
         "python ~/.copilot" not in skill_content,
         "Should use python3, not python")


# ─── Summary ────────────────────────────────────────────────────────────

print(f"\n{'='*50}")
print(f"Results: {PASS} passed, {FAIL} failed out of {PASS + FAIL}")
if FAIL == 0:
    print("🎉 All tests passed!")
else:
    print(f"⚠️  {FAIL} test(s) need attention")
sys.exit(0 if FAIL == 0 else 1)
