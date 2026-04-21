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
    [sys.executable, str(REPO / "briefing.py"), "code review", "--for-subagent", "--min-confidence", "0"],
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

# LaunchAgent is macOS-only — skip on Linux/WSL
if sys.platform == "darwin":
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
else:
    print("  ⏭️  Skipped — LaunchAgent is macOS-only (running on Linux/WSL)")


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


# ─── Skill Packaging (validate-skill + setup-project references/) ────────

print("\n📦 Skill Packaging Tests")

# Import validate function from validate-skill.py (no package init, import by path)
import importlib.util as _ilu
_vs_spec = _ilu.spec_from_file_location("validate_skill", REPO / "validate-skill.py")
_vs = _ilu.module_from_spec(_vs_spec)
_vs_spec.loader.exec_module(_vs)
validate = _vs.validate

# Helper: create a minimal valid SKILL.md in a temp dir
import tempfile as _tf

def _make_skill_dir(skill_content: str, refs: dict[str, str] | None = None) -> Path:
    """Write SKILL.md (and optional references/ files) into a fresh temp dir.

    Temp dirs are created in the system temp area (not inside the repo tree)
    so they never appear as untracked files in git status.
    """
    d = Path(_tf.mkdtemp())
    (d / "SKILL.md").write_text(skill_content, encoding="utf-8")
    if refs:
        refs_dir = d / "references"
        refs_dir.mkdir()
        for name, body in refs.items():
            # Support nested paths (e.g. "sub/file.md")
            dest = refs_dir / name
            dest.parent.mkdir(parents=True, exist_ok=True)
            dest.write_text(body, encoding="utf-8")
    return d


MINIMAL_SKILL = """\
---
name: test-skill
description: Use when testing. Invoke for unit test validation. Trigger: test.
---

# Test Skill

## When to Use

Use this skill for testing.

## Workflow

Run tests.

<example>
Example usage here.
</example>
"""

import shutil as _shutil

# Sp1. No dangling references → no warnings about references/
_d1 = _make_skill_dir(MINIMAL_SKILL)
try:
    _errs1, _warns1 = validate(_d1 / "SKILL.md")
    test("Sp1: no spurious reference warnings when no refs mentioned",
         not any("Dangling" in w for w in _warns1),
         f"Got warnings: {_warns1}")
finally:
    _shutil.rmtree(_d1, ignore_errors=True)

# Sp2. Mentioned reference exists → no dangling warning
_SKILL_WITH_REF = MINIMAL_SKILL + "\nSee `references/guide.md` for details.\n"
_d2 = _make_skill_dir(_SKILL_WITH_REF, refs={"guide.md": "# Guide\nContent."})
try:
    _errs2, _warns2 = validate(_d2 / "SKILL.md")
    test("Sp2: existing references/guide.md → no dangling warning",
         not any("guide.md" in w for w in _warns2),
         f"Got warnings: {_warns2}")
finally:
    _shutil.rmtree(_d2, ignore_errors=True)

# Sp3. Mentioned reference missing → one dangling warning (positive case)
_d3 = _make_skill_dir(_SKILL_WITH_REF)  # no refs/ created
try:
    _errs3, _warns3 = validate(_d3 / "SKILL.md")
    test("Sp3: missing references/guide.md → dangling warning emitted",
         any("guide.md" in w and "Dangling" in w for w in _warns3),
         f"Got warnings: {_warns3}")
finally:
    _shutil.rmtree(_d3, ignore_errors=True)

# Sp4. Same reference mentioned twice → exactly ONE warning (deduplication)
_SKILL_DOUBLE_REF = MINIMAL_SKILL + (
    "\nSee `references/guide.md` for overview.\n"
    "Also `references/guide.md` covers advanced topics.\n"
)
_d4 = _make_skill_dir(_SKILL_DOUBLE_REF)
try:
    _errs4, _warns4 = validate(_d4 / "SKILL.md")
    dangling_count = sum(1 for w in _warns4 if "guide.md" in w and "Dangling" in w)
    test("Sp4: duplicate reference mention → exactly 1 warning (deduplication)",
         dangling_count == 1,
         f"Got {dangling_count} dangling warnings for guide.md")
finally:
    _shutil.rmtree(_d4, ignore_errors=True)

# Sp5. Non-relative path (shared/references/foo.md) does NOT trigger warning
_SKILL_NONREL = MINIMAL_SKILL + "\nSee shared/references/guide.md elsewhere.\n"
_d5 = _make_skill_dir(_SKILL_NONREL)
try:
    _errs5, _warns5 = validate(_d5 / "SKILL.md")
    test("Sp5: shared/references/guide.md (non-relative) → no dangling warning",
         not any("guide.md" in w and "Dangling" in w for w in _warns5),
         f"Got warnings: {_warns5}")
finally:
    _shutil.rmtree(_d5, ignore_errors=True)

# Sp6. Nested reference exists → no dangling warning
_SKILL_NESTED_REF = MINIMAL_SKILL + "\nSee `references/sub/deep.md` for details.\n"
_d6 = _make_skill_dir(_SKILL_NESTED_REF, refs={"sub/deep.md": "# Deep\nContent."})
try:
    _errs6, _warns6 = validate(_d6 / "SKILL.md")
    test("Sp6: existing references/sub/deep.md (nested) → no dangling warning",
         not any("sub/deep.md" in w and "Dangling" in w for w in _warns6),
         f"Got warnings: {_warns6}")
finally:
    _shutil.rmtree(_d6, ignore_errors=True)

# Sp7. Nested reference missing → dangling warning
_d7 = _make_skill_dir(_SKILL_NESTED_REF)
try:
    _errs7, _warns7 = validate(_d7 / "SKILL.md")
    test("Sp7: missing references/sub/deep.md (nested) → dangling warning",
         any("sub/deep.md" in w and "Dangling" in w for w in _warns7),
         f"Got warnings: {_warns7}")
finally:
    _shutil.rmtree(_d7, ignore_errors=True)

# Sp8. setup-project install_skills deploys nested references/ preserving structure
# Use fully isolated temp dirs outside the repo — no mutation of the live source tree.
import importlib as _imp

_sp8_root = Path(_tf.mkdtemp())   # isolated skills dir (acts as SKILLS_DIR)
_proj8 = Path(_tf.mkdtemp())      # isolated install target (acts as project root)

# Build a minimal fake skill: fake-skill/SKILL.md + references/nested_test/nested-ref.md
_fake_skill_name = "fake-nested-skill"
_fake_skill_dir = _sp8_root / _fake_skill_name
(_fake_skill_dir / "references" / "nested_test").mkdir(parents=True)
(_fake_skill_dir / "SKILL.md").write_text("# Fake skill\n")
(_fake_skill_dir / "references" / "nested_test" / "nested-ref.md").write_text("# Nested test\n")

try:
    _sp_spec = _ilu.spec_from_file_location("setup_project", REPO / "setup-project.py")
    _sp8 = _imp.util.module_from_spec(_sp_spec)
    _sp_spec.loader.exec_module(_sp8)

    # Monkeypatch SKILLS_DIR and INSTALL_ITEMS so only our fake skill is processed.
    _orig_skills_dir = _sp8.SKILLS_DIR
    _orig_install_items = _sp8.INSTALL_ITEMS
    _sp8.SKILLS_DIR = _sp8_root
    _sp8.INSTALL_ITEMS = {"skills": [{"src": _fake_skill_name, "label": "fake-nested-skill"}], "templates": []}

    _sp8.install_skills(_proj8, dry_run=False)

    _expected = _proj8 / ".github" / "skills" / _fake_skill_name / "references" / "nested_test" / "nested-ref.md"
    test("Sp8: nested references/nested_test/nested-ref.md deployed with relative path",
         _expected.exists(),
         f"Expected at {_expected}")
finally:
    _shutil.rmtree(_sp8_root, ignore_errors=True)
    _shutil.rmtree(_proj8, ignore_errors=True)

# Sp9. session-knowledge-creator reference files exist in repo
_sk_refs = REPO / "skills" / "session-knowledge-creator" / "references"
test("Sp9: references/instructions-template.md exists in session-knowledge-creator",
     (_sk_refs / "instructions-template.md").exists())
test("Sp9: references/skill-template.md exists in session-knowledge-creator",
     (_sk_refs / "skill-template.md").exists())

# Sp10. Validator passes (no dangling refs) for session-knowledge-creator after fix
_sk_path = REPO / "skills" / "session-knowledge-creator"
_sk_errs, _sk_warns = validate(_sk_path)
dangling_sk = [w for w in _sk_warns if "Dangling" in w]
test("Sp10: session-knowledge-creator has no dangling reference warnings",
     len(dangling_sk) == 0,
     f"Dangling refs: {dangling_sk}")

# Sp11. Traversal guard: references/../SKILL.md and references/a/../../x.md are rejected
# The skill dir has a real references/ subdirectory so that `references/../SKILL.md`
# resolves to the actual SKILL.md on disk — proving the escape IS dangerous and that
# the guard is the only thing preventing a silent false-pass.
_TRAVERSAL_SKILL = """\
---
name: traversal-test
description: Use when testing traversal guard. Trigger: test.
---

# Traversal Test Skill

## When to Use
Testing traversal guard.

## Workflow
See references/../SKILL.md and references/a/../../secret.md.

<example>
Bad ref: references/../SKILL.md
Nested bad ref: references/a/../../secret.md
</example>
"""
_d11 = _make_skill_dir(_TRAVERSAL_SKILL)
# Create a real references/ dir so the escape path actually resolves to an existing file.
(_d11 / "references").mkdir(exist_ok=True)
try:
    # Confirm the dangerous case: without the guard, the OS would resolve
    # `<d11>/references/../SKILL.md` → `<d11>/SKILL.md` which exists.
    _escape_path = _d11 / "references" / ".." / "SKILL.md"
    test("Sp11: escape path references/../SKILL.md resolves to an existing file (danger confirmed)",
         _escape_path.exists(),
         f"Expected {_escape_path} to exist")

    _errs11, _warns11 = validate(_d11 / "SKILL.md")
    _traversal_warns = [w for w in _warns11 if "Suspicious" in w or "traversal" in w.lower() or ".." in w]
    # Each traversal pattern must be caught individually — not via generic ".." membership.
    test("Sp11: single-level escape references/../SKILL.md triggers traversal warning",
         any("references/../SKILL.md" in w for w in _traversal_warns),
         f"Got warnings: {_warns11}")
    test("Sp11: double-level escape references/a/../../secret.md triggers traversal warning",
         any("references/a/../../secret.md" in w for w in _traversal_warns),
         f"Got warnings: {_warns11}")
    test("Sp11: traversal path does NOT appear as a dangling reference warning",
         not any("Dangling" in w and ".." in w for w in _warns11),
         f"Got warnings: {_warns11}")
finally:
    _shutil.rmtree(_d11, ignore_errors=True)


# Sp12. install_skills deploys templates/ assets from conductor-creator
# This is the regression case: before the fix, templates/ was silently dropped.
print("\n📦 Skill Packaging — Auxiliary Asset Dirs (Sp12 / Sp13)")

_sp12_root = Path(_tf.mkdtemp())  # fake SKILLS_DIR
_proj12 = Path(_tf.mkdtemp())     # fake project root

_fake12 = _sp12_root / "conductor-creator"
(_fake12 / "templates").mkdir(parents=True)
(_fake12 / "references").mkdir(parents=True)
(_fake12 / "SKILL.md").write_text("# Conductor Creator\n")
(_fake12 / "templates" / "conductor.py").write_text("# conductor template\n")
(_fake12 / "templates" / "test-conductor.py").write_text("# test-conductor template\n")
(_fake12 / "references" / "guide.md").write_text("# guide\n")

try:
    _sp12_spec = _ilu.spec_from_file_location("setup_project_sp12", REPO / "setup-project.py")
    _sp12_mod = _imp.util.module_from_spec(_sp12_spec)
    _sp12_spec.loader.exec_module(_sp12_mod)

    _sp12_mod.SKILLS_DIR = _sp12_root
    _sp12_mod.INSTALL_ITEMS = {
        "skills": [{"src": "conductor-creator", "label": "Conductor Creator"}],
        "templates": [],
    }
    _sp12_mod.install_skills(_proj12, dry_run=False)

    _skill12_base = _proj12 / ".github" / "skills" / "conductor-creator"
    test("Sp12: conductor.py deployed under templates/",
         (_skill12_base / "templates" / "conductor.py").exists(),
         f"Missing {_skill12_base / 'templates' / 'conductor.py'}")
    test("Sp12: test-conductor.py deployed under templates/",
         (_skill12_base / "templates" / "test-conductor.py").exists(),
         f"Missing {_skill12_base / 'templates' / 'test-conductor.py'}")
    test("Sp12: references/guide.md still deployed (regression: references/ preserved)",
         (_skill12_base / "references" / "guide.md").exists(),
         f"Missing {_skill12_base / 'references' / 'guide.md'}")
finally:
    _shutil.rmtree(_sp12_root, ignore_errors=True)
    _shutil.rmtree(_proj12, ignore_errors=True)

# Sp13. Generic: any skill with a custom subdir has its files deployed
_sp13_root = Path(_tf.mkdtemp())
_proj13 = Path(_tf.mkdtemp())

_fake13 = _sp13_root / "multi-asset-skill"
(_fake13 / "templates").mkdir(parents=True)
(_fake13 / "evals").mkdir(parents=True)
(_fake13 / "references").mkdir(parents=True)
(_fake13 / "SKILL.md").write_text("# Multi Asset Skill\n")
(_fake13 / "templates" / "tmpl.py").write_text("# tmpl\n")
(_fake13 / "evals" / "eval.json").write_text("{}\n")
(_fake13 / "references" / "ref.md").write_text("# ref\n")

try:
    _sp13_spec = _ilu.spec_from_file_location("setup_project_sp13", REPO / "setup-project.py")
    _sp13_mod = _imp.util.module_from_spec(_sp13_spec)
    _sp13_spec.loader.exec_module(_sp13_mod)

    _sp13_mod.SKILLS_DIR = _sp13_root
    _sp13_mod.INSTALL_ITEMS = {
        "skills": [{"src": "multi-asset-skill", "label": "Multi Asset Skill"}],
        "templates": [],
    }
    _sp13_mod.install_skills(_proj13, dry_run=False)

    _skill13_base = _proj13 / ".github" / "skills" / "multi-asset-skill"
    test("Sp13: templates/tmpl.py deployed for skill with multiple asset subdirs",
         (_skill13_base / "templates" / "tmpl.py").exists())
    test("Sp13: evals/eval.json deployed for skill with multiple asset subdirs",
         (_skill13_base / "evals" / "eval.json").exists())
    test("Sp13: references/ref.md deployed for skill with multiple asset subdirs",
         (_skill13_base / "references" / "ref.md").exists())
finally:
    _shutil.rmtree(_sp13_root, ignore_errors=True)
    _shutil.rmtree(_proj13, ignore_errors=True)

# Sp14. Live repo: conductor-creator templates/ files exist and are real files
_cc_templates = REPO / "skills" / "conductor-creator" / "templates"
test("Sp14: conductor-creator/templates/conductor.py exists in repo",
     (_cc_templates / "conductor.py").exists(),
     f"Expected at {_cc_templates / 'conductor.py'}")
test("Sp14: conductor-creator/templates/test-conductor.py exists in repo",
     (_cc_templates / "test-conductor.py").exists(),
     f"Expected at {_cc_templates / 'test-conductor.py'}")


# ─── Guidance Alignment (Ga1–Ga5) ──────────────────────────────────────────
# Verify that the injected GLOBAL_INJECT_BLOCK and the canonical
# session-knowledge.instructions.md template do not drift on briefing strategy.

print("\n🔍 Guidance Alignment Tests (Ga)")

import importlib.util as _ilu

# Load install.py as a module without executing its __main__ block
_install_spec = _ilu.spec_from_file_location("install_mod", REPO / "install.py")
_install_mod = _ilu.module_from_spec(_install_spec)
_install_spec.loader.exec_module(_install_mod)  # type: ignore[union-attr]

_inject_block: str = _install_mod.GLOBAL_INJECT_BLOCK
_template_path = REPO / "templates" / "session-knowledge.instructions.md"
_template_text: str = _template_path.read_text(encoding="utf-8") if _template_path.exists() else ""

# Ga1. Injected block must NOT contain any line that calls briefing.py with --full.
#      This rejects all forms: briefing.py "task" --full, briefing.py --auto --full, etc.
test(
    "Ga1: GLOBAL_INJECT_BLOCK does not mandate --full for complex tasks",
    not any("briefing.py" in ln and "--full" in ln for ln in _inject_block.splitlines()),
    "Found a line that calls briefing.py with --full — use --compact and escalate only when needed",
)

# Ga2. Injected block must contain --for-subagent (sub-agent path)
test(
    "Ga2: GLOBAL_INJECT_BLOCK includes --for-subagent guidance",
    "--for-subagent" in _inject_block,
    "GLOBAL_INJECT_BLOCK is missing --for-subagent sub-agent context injection",
)

# Ga3. Injected block must contain --compact (start-minimal signal)
test(
    "Ga3: GLOBAL_INJECT_BLOCK references --compact (start-minimal strategy)",
    "--compact" in _inject_block,
    "GLOBAL_INJECT_BLOCK missing --compact — injected guidance conflicts with start-minimal policy",
)

# Ga4. Canonical template must contain --for-subagent guidance
test(
    "Ga4: canonical template includes --for-subagent guidance",
    "--for-subagent" in _template_text,
    f"session-knowledge.instructions.md missing --for-subagent section",
)

# Ga5. Injected block must be a pointer (short) — no duplicate policy paragraphs.
#      Heuristic: block must be <= 30 lines (a full-policy block was ~20 lines of rules)
_inject_lines = [ln for ln in _inject_block.splitlines() if ln.strip()]
test(
    "Ga5: GLOBAL_INJECT_BLOCK is a lightweight pointer (≤ 30 non-blank lines)",
    len(_inject_lines) <= 30,
    f"Block has {len(_inject_lines)} non-blank lines — it may duplicate canonical policy",
)


# ─── Post-Merge Hook Newline Normalization (Pm1–Pm4) ──────────────────────

print("\n🪝 Post-Merge Hook Tests (Pm)")

_autoupdate_spec = _ilu.spec_from_file_location("auto_update_mod", REPO / "auto-update-tools.py")
_autoupdate_mod = _ilu.module_from_spec(_autoupdate_spec)
_autoupdate_spec.loader.exec_module(_autoupdate_mod)  # type: ignore[union-attr]

with tempfile.TemporaryDirectory(prefix="auto-update-hook-") as _tmp:
    _tools_dir = Path(_tmp)
    (_tools_dir / ".git" / "hooks").mkdir(parents=True, exist_ok=True)
    _hook_path = _tools_dir / ".git" / "hooks" / "post-merge"

    _orig_tools_dir = _autoupdate_mod.TOOLS_DIR
    _orig_platform_system = _autoupdate_mod.platform.system
    try:
        _autoupdate_mod.TOOLS_DIR = _tools_dir
        _autoupdate_mod.platform.system = lambda: "Windows"

        _autoupdate_mod.ensure_post_merge_hook()
        _windows_bytes = _hook_path.read_bytes()

        test(
            "Pm1: Windows hook is written with LF line endings",
            b"\r\n" not in _windows_bytes,
            "Generated Windows hook contains CRLF bytes",
        )
        test(
            "Pm2: Windows hook shebang has no CR",
            _windows_bytes.startswith(b"#!/bin/sh\n"),
            f"Got prefix: {_windows_bytes[:16]!r}",
        )

        _hook_path.write_bytes(_windows_bytes.replace(b"\n", b"\r\n"))
        _autoupdate_mod.ensure_post_merge_hook()
        _normalized_bytes = _hook_path.read_bytes()

        test(
            "Pm3: ensure_post_merge_hook normalizes existing CRLF hooks back to LF",
            b"\r\n" not in _normalized_bytes,
            "Existing CRLF hook was not rewritten",
        )

        _autoupdate_mod.platform.system = lambda: "Linux"
        _autoupdate_mod.ensure_post_merge_hook()
        _linux_bytes = _hook_path.read_bytes()

        test(
            "Pm4: POSIX hook (Linux/macOS) also remains LF-only",
            _linux_bytes.startswith(b"#!/bin/bash\n") and b"\r\n" not in _linux_bytes,
            f"Got prefix: {_linux_bytes[:18]!r}",
        )
    finally:
        _autoupdate_mod.TOOLS_DIR = _orig_tools_dir
        _autoupdate_mod.platform.system = _orig_platform_system

_editorconfig_path = REPO / ".editorconfig"
_editorconfig_text = _editorconfig_path.read_text(encoding="utf-8") if _editorconfig_path.exists() else ""

test(
    "Pm5: .editorconfig exists for editor-level line ending control",
    _editorconfig_path.exists(),
    "Missing .editorconfig",
)
test(
    "Pm6: .editorconfig marks repo root",
    "root = true" in _editorconfig_text,
    "Expected root = true",
)
test(
    "Pm7: .editorconfig enforces LF for all files",
    "[*]" in _editorconfig_text and re.search(r"(?mi)^end_of_line\s*=\s*lf$", _editorconfig_text) is not None,
    "Expected [*] section with end_of_line = lf",
)


# ─── Summary ────────────────────────────────────────────────────────────

print(f"\n{'='*50}")
print(f"Results: {PASS} passed, {FAIL} failed out of {PASS + FAIL}")
if FAIL == 0:
    print("🎉 All tests passed!")
else:
    print(f"⚠️  {FAIL} test(s) need attention")
sys.exit(0 if FAIL == 0 else 1)
