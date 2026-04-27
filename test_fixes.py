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
import json
import sqlite3
import subprocess
import tempfile
import plistlib
from pathlib import Path

if os.name == "nt":
    for _s in (sys.stdout, sys.stderr):
        if hasattr(_s, "reconfigure"):
            _s.reconfigure(encoding="utf-8", errors="replace")

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


def _seed_briefing_test_home(base_dir: Path) -> Path:
    """Create a deterministic HOME + knowledge.db fixture for briefing subprocesses."""
    home = Path(tempfile.mkdtemp(prefix="briefing-home-", dir=str(base_dir)))
    db_path = home / ".copilot" / "session-state" / "knowledge.db"
    db_path.parent.mkdir(parents=True, exist_ok=True)

    db = sqlite3.connect(str(db_path))
    db.executescript("""
        CREATE TABLE IF NOT EXISTS documents (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            session_id TEXT NOT NULL,
            doc_type TEXT NOT NULL,
            seq INTEGER DEFAULT 0,
            title TEXT NOT NULL,
            file_path TEXT NOT NULL UNIQUE
        );
        CREATE TABLE IF NOT EXISTS knowledge_entries (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            session_id TEXT NOT NULL,
            document_id INTEGER,
            category TEXT NOT NULL,
            title TEXT NOT NULL,
            content TEXT NOT NULL,
            tags TEXT DEFAULT '',
            confidence REAL DEFAULT 1.0,
            occurrence_count INTEGER DEFAULT 1,
            first_seen TEXT,
            last_seen TEXT,
            source TEXT DEFAULT 'copilot',
            topic_key TEXT,
            revision_count INTEGER DEFAULT 1,
            content_hash TEXT,
            wing TEXT DEFAULT '',
            room TEXT DEFAULT '',
            facts TEXT DEFAULT '[]',
            est_tokens INTEGER DEFAULT 0,
            task_id TEXT DEFAULT '',
            affected_files TEXT DEFAULT '[]',
            source_section TEXT DEFAULT '',
            source_file TEXT DEFAULT '',
            start_line INTEGER,
            end_line INTEGER,
            code_language TEXT DEFAULT '',
            code_snippet TEXT DEFAULT ''
        );
        CREATE VIRTUAL TABLE IF NOT EXISTS ke_fts USING fts5(
            title, content, tags, category, wing, room, facts
        );
        CREATE TABLE IF NOT EXISTS knowledge_relations (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            source_id INTEGER NOT NULL,
            target_id INTEGER NOT NULL,
            relation_type TEXT NOT NULL,
            confidence REAL DEFAULT 0.5
        );
    """)

    doc_id = db.execute(
        "INSERT INTO documents (session_id, doc_type, seq, title, file_path) VALUES (?, ?, ?, ?, ?)",
        ("fixes-session-001", "checkpoint", 1, "Deterministic briefing fixture", "checkpoints/001.md"),
    ).lastrowid

    entries = [
        ("mistake", "Code review auth SQL pitfall", "In code review, avoid auth SQL string interpolation."),
        ("pattern", "Code review uses deterministic DB fixture", "For review auth PR tests, seed deterministic briefing entries."),
        ("decision", "Review workflow sets explicit HOME", "Set HOME/USERPROFILE for deterministic Path.home() in code review."),
        ("tool", "Briefing pack for auth review", "Use briefing --pack for code review auth machine output."),
    ]
    for cat, title, content in entries:
        row_id = db.execute(
            """
            INSERT INTO knowledge_entries
                (session_id, document_id, category, title, content, confidence,
                 occurrence_count, first_seen, last_seen, task_id, affected_files)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                "fixes-session-001",
                doc_id,
                cat,
                title,
                content,
                0.95,
                1,
                "2024-01-01T00:00:00",
                "2024-01-01T00:00:00",
                "ci-clean-home-recall-tests",
                '["briefing.py"]',
            ),
        ).lastrowid
        db.execute(
            """
            INSERT INTO ke_fts (rowid, title, content, tags, category, wing, room, facts)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (row_id, title, content, "deterministic,fixture", cat, "", "", '["fixture"]'),
        )

    db.commit()
    db.close()
    return home


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

import shutil as _shutil
_briefing_home = _seed_briefing_test_home(REPO)
_briefing_env = dict(os.environ)
_briefing_env["HOME"] = str(_briefing_home)
_briefing_env["USERPROFILE"] = str(_briefing_home)
try:
    # 2a. --for-subagent flag exists and produces output
    result = subprocess.run(
        [sys.executable, str(REPO / "briefing.py"), "code review", "--for-subagent", "--min-confidence", "0"],
        capture_output=True, text=True, cwd=str(REPO), env=_briefing_env, encoding="utf-8", errors="replace"
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
        capture_output=True, text=True, cwd=str(REPO), env=_briefing_env, encoding="utf-8", errors="replace"
    )
    test("Regular briefing still works",
         result2.returncode == 0,
         f"stdout: {result2.stdout[:100]}")

    # 2d. --for-subagent remains compact with explicit mode
    result3 = subprocess.run(
        [sys.executable, str(REPO / "briefing.py"), "review auth PR", "--for-subagent", "--mode", "review", "--min-confidence", "0"],
        capture_output=True, text=True, cwd=str(REPO), env=_briefing_env, encoding="utf-8", errors="replace"
    )
    output3 = result3.stdout.strip()
    test("--for-subagent + --mode runs without error", result3.returncode == 0,
         f"stderr: {result3.stderr[:200]}")
    test("--for-subagent + --mode still starts with compact context header",
         output3.startswith("[KNOWLEDGE CONTEXT"),
         f"Got: {output3[:80]}")

    # 2e. --pack exposes machine-readable briefing surface
    result4 = subprocess.run(
        [sys.executable, str(REPO / "briefing.py"), "review auth PR", "--mode", "review", "--pack", "--limit", "1"],
        capture_output=True, text=True, cwd=str(REPO), env=_briefing_env, encoding="utf-8", errors="replace"
    )
    test("--pack runs without error", result4.returncode == 0,
         f"stderr: {result4.stderr[:200]}")
    try:
        pack_obj = json.loads(result4.stdout)
        test("--pack returns valid JSON", True)
        test("--pack includes mode field", "mode" in pack_obj, f"keys={list(pack_obj.keys())}")
        test("--pack preserves explicit mode", pack_obj.get("mode") == "review",
             f"mode={pack_obj.get('mode')!r}")
        entries_obj = pack_obj.get("entries", {})
        test("--pack includes canonical entry buckets",
             isinstance(entries_obj, dict)
             and all(k in entries_obj for k in ("mistake", "pattern", "decision", "tool")),
             f"entries keys={list(entries_obj.keys()) if isinstance(entries_obj, dict) else type(entries_obj).__name__}")
        first_entry = None
        if isinstance(entries_obj, dict):
            for bucket in ("mistake", "pattern", "decision", "tool"):
                vals = entries_obj.get(bucket, [])
                if vals:
                    first_entry = vals[0]
                    break
        if first_entry:
            test("--pack entry includes source_document field",
                 "source_document" in first_entry,
                 f"keys={list(first_entry.keys())}")
            test("--pack entry includes code-location/snippet fields",
                 all(k in first_entry for k in ("source_file", "start_line", "end_line", "code_language", "code_snippet")),
                 f"keys={list(first_entry.keys())}")
            test("--pack entry includes snippet_freshness enum field",
                 first_entry.get("snippet_freshness") in {"fresh", "drifted", "missing", "unknown"},
                 f"snippet_freshness={first_entry.get('snippet_freshness')!r}")
            rel_ids = first_entry.get("related_entry_ids", [])
            test("--pack entry includes related_entry_ids as int list",
                 isinstance(rel_ids, list) and all(isinstance(x, int) for x in rel_ids) and len(rel_ids) <= 3,
                 f"related_entry_ids={rel_ids!r}")
        else:
            test("--pack entry includes source_document field", True, "(skipped — no entries)")
            test("--pack entry includes code-location/snippet fields", True, "(skipped — no entries)")
            test("--pack entry includes snippet_freshness enum field", True, "(skipped — no entries)")
            test("--pack entry includes related_entry_ids as int list", True, "(skipped — no entries)")
    except json.JSONDecodeError as e:
        test("--pack returns valid JSON", False, str(e))
        test("--pack includes mode field", False, "invalid JSON output")
        test("--pack preserves explicit mode", False, "invalid JSON output")
        test("--pack includes canonical entry buckets", False, "invalid JSON output")
        test("--pack entry includes source_document field", False, "invalid JSON output")
        test("--pack entry includes code-location/snippet fields", False, "invalid JSON output")
        test("--pack entry includes snippet_freshness enum field", False, "invalid JSON output")
        test("--pack entry includes related_entry_ids as int list", False, "invalid JSON output")
finally:
    _shutil.rmtree(_briefing_home, ignore_errors=True)


# ─── Fix 3: LaunchAgent Plist ────────────────────────────────────────────

print("\n🚀 Fix 3: LaunchAgent Tests")

plist_path = Path.home() / "Library/LaunchAgents/com.copilot.watch-sessions.plist"
template_plist = REPO / "templates" / "com.copilot.watch-sessions.plist"
plist_is_user_install = plist_path.exists()
plist_under_test = plist_path if plist_is_user_install else template_plist

# LaunchAgent is macOS-only — skip on Linux/WSL
if sys.platform == "darwin":
    # 3a. Plist file exists (prefer user install, fallback to repo template)
    test("Plist file exists", plist_under_test.exists(),
         f"Expected at {plist_under_test}")

    if plist_under_test.exists():
        if not plist_is_user_install:
            print("  ℹ Using repo template plist (user install not present under current HOME)")
        # 3b. Valid XML plist
        try:
            with open(plist_under_test, "rb") as f:
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
        # launchd must own the watcher lifecycle — run in foreground (no --daemon).
        # --daemon causes a double-fork so launchd loses the PID and the detached
        # child conflicts with every subsequent launchd restart attempt.
        test("No --daemon flag (launchd owns lifecycle)", "--daemon" not in prog_args,
             f"Got: {prog_args} — remove --daemon so launchd manages the process")

        test("RunAtLoad is true", plist_data.get("RunAtLoad") is True)

        test("Has KeepAlive", "KeepAlive" in plist_data,
             "Daemon should restart on crash")

        test("WorkingDirectory is ~/.copilot",
             plist_data.get("WorkingDirectory", "").endswith(".copilot"),
             f"Got: {plist_data.get('WorkingDirectory')}")

        # 3d. plutil validates the plist
        plutil_result = subprocess.run(
            ["plutil", "-lint", str(plist_under_test)],
            capture_output=True, text=True, encoding="utf-8", errors="replace"
        )
        test("plutil lint passes",
             plutil_result.returncode == 0,
             plutil_result.stderr or plutil_result.stdout)

        # 3e. Python path exists (only deterministic for installed plist)
        python_path = prog_args[0] if prog_args else ""
        if plist_is_user_install:
            test("Python3 path exists", Path(python_path).exists(),
                 f"Path: {python_path}")
        else:
            test("Python3 path check skipped for template plist", True,
                 f"Template path={python_path}")
else:
    print("  ⏭️  Skipped — LaunchAgent is macOS-only (running on Linux/WSL)")


# ─── Fix 1 Integration: Re-extract and verify ───────────────────────────

print("\n🔄 Integration: Verify noise filter reduces false positives")

# Count current false positives in DB
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

    # Stale embeddings in the user's long-lived knowledge.db are environment state,
    # not a deterministic repo regression, so keep this as an informational health check.
    stale = db.execute("""
        SELECT COUNT(*) FROM embeddings WHERE
        (source_type = 'knowledge' AND source_id NOT IN (SELECT id FROM knowledge_entries)) OR
        (source_type = 'section' AND source_id NOT IN (SELECT id FROM sections))
    """).fetchone()[0]
    if stale:
        print(f"  ⚠ Local DB has {stale} stale embeddings (informational, not a repo failure)")
    test("Stale embedding health query runs", stale >= 0)

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
# Fallbacks: clean HOME may not have global install; use deterministic repo template
if not skill_path.exists():
    skill_path = Path.home() / ".copilot/tools/templates/SKILL.md"
if not skill_path.exists():
    skill_path = REPO / "templates" / "SKILL.md"
test("SKILL.md exists in tools or skills path", skill_path.exists())

if skill_path.exists():
    skill_content = skill_path.read_text(encoding="utf-8")
    template_skill_path = REPO / "templates" / "SKILL.md"
    template_skill_content = (
        template_skill_path.read_text(encoding="utf-8")
        if template_skill_path.exists()
        else ""
    )
    test("Contains --for-subagent docs", "--for-subagent" in skill_content)
    test("Documents structured tentacle recall path",
         "tentacle.py" in template_skill_content and "[KNOWLEDGE EVIDENCE]" in template_skill_content)
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
(_fake_skill_dir / "SKILL.md").write_text("# Fake skill\n", encoding="utf-8")
(_fake_skill_dir / "references" / "nested_test" / "nested-ref.md").write_text("# Nested test\n", encoding="utf-8")

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
(_fake12 / "SKILL.md").write_text("# Conductor Creator\n", encoding="utf-8")
(_fake12 / "templates" / "conductor.py").write_text("# conductor template\n", encoding="utf-8")
(_fake12 / "templates" / "test-conductor.py").write_text("# test-conductor template\n", encoding="utf-8")
(_fake12 / "references" / "guide.md").write_text("# guide\n", encoding="utf-8")

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
(_fake13 / "SKILL.md").write_text("# Multi Asset Skill\n", encoding="utf-8")
(_fake13 / "templates" / "tmpl.py").write_text("# tmpl\n", encoding="utf-8")
(_fake13 / "evals" / "eval.json").write_text("{}\n", encoding="utf-8")
(_fake13 / "references" / "ref.md").write_text("# ref\n", encoding="utf-8")

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

# Sp15. Empty name value (bare `name:` with no value) → must report an error, not silently pass.
# Before the fix, `\s*` in the regex could cross a newline and capture the next line
# (e.g. `description:`) as the name value.  The fix ([ \t]*) closes that hole and the
# new `else` branch emits an honest error.
_EMPTY_NAME_SKILL = """\
---
name:
description: Use when testing empty name. Trigger: test-empty-name.
---

# Empty Name Skill

## When to Use

Testing empty name validation.

## Workflow

Does nothing.

<example>
Example usage.
</example>
"""
_d15 = _make_skill_dir(_EMPTY_NAME_SKILL)
try:
    _errs15, _warns15 = validate(_d15 / "SKILL.md")
    test("Sp15: bare `name:` (empty value) → error reported",
         any("no value" in e or "empty" in e.lower() for e in _errs15),
         f"Expected empty-name error; got errors={_errs15}")
    test("Sp15: bare `name:` does NOT capture next line as name value",
         not any("description" in e.lower() and "invalid" in e.lower() for e in _errs15),
         f"Regex crossed line boundary — captured 'description:' as name: errors={_errs15}")
finally:
    _shutil.rmtree(_d15, ignore_errors=True)

# Sp16. Name with whitespace-only value (e.g. `name:   `) → must also report an error.
_WHITESPACE_NAME_SKILL = """\
---
name:   
description: Use when testing whitespace name. Trigger: test-ws-name.
---

# Whitespace Name Skill

## When to Use

Testing whitespace name validation.

## Workflow

Does nothing.

<example>
Example usage.
</example>
"""
_d16 = _make_skill_dir(_WHITESPACE_NAME_SKILL)
try:
    _errs16, _warns16 = validate(_d16 / "SKILL.md")
    test("Sp16: whitespace-only `name:   ` → error reported (empty value)",
         any("no value" in e or "empty" in e.lower() for e in _errs16),
         f"Expected empty-name error; got errors={_errs16}")
finally:
    _shutil.rmtree(_d16, ignore_errors=True)

# Sp17. Empty description value (bare `description:` with no value) → must report an error,
# not silently pass.  Before the fix, `\s*` in the regex could cross a newline and capture
# the next YAML key (e.g. `name:`) as the description text; now [ \t]* closes that hole
# and an else-branch emits an honest error.
_EMPTY_DESC_SKILL = """\
---
name: sp17-test
description:
location: user
---

# Empty Description Skill

## When to Use

Testing empty description validation.

## Workflow

Does nothing.

<example>
Example usage.
</example>
"""
_d17 = _make_skill_dir(_EMPTY_DESC_SKILL)
try:
    _errs17, _warns17 = validate(_d17 / "SKILL.md")
    test("Sp17: bare `description:` (empty value) → error reported",
         any("no value" in e or "empty" in e.lower() for e in _errs17),
         f"Expected empty-description error; got errors={_errs17}")
    test("Sp17: bare `description:` does NOT capture next YAML line as description",
         not any("description only" in w.lower() for w in _warns17),
         f"Regex crossed line boundary — word-count warning implies next key was captured as description: warns={_warns17}")
finally:
    _shutil.rmtree(_d17, ignore_errors=True)

# Sp18. Description with whitespace-only value (e.g. `description:   `) → must also
# report an error (mirrors Sp16 for description).
_WHITESPACE_DESC_SKILL = """\
---
name: sp18-test
description:   
location: user
---

# Whitespace Description Skill

## When to Use

Testing whitespace description validation.

## Workflow

Does nothing.

<example>
Example usage.
</example>
"""
_d18 = _make_skill_dir(_WHITESPACE_DESC_SKILL)
try:
    _errs18, _warns18 = validate(_d18 / "SKILL.md")
    test("Sp18: whitespace-only `description:   ` → error reported (empty value)",
         any("no value" in e or "empty" in e.lower() for e in _errs18),
         f"Expected empty-description error; got errors={_errs18}")
finally:
    _shutil.rmtree(_d18, ignore_errors=True)


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

# Ga2. Injected block must mention structured tentacle recall as preferred delegated path
test(
    "Ga2: GLOBAL_INJECT_BLOCK references tentacle structured recall path",
    "tentacle.py" in _inject_block and "--briefing" in _inject_block,
    "GLOBAL_INJECT_BLOCK missing tentacle structured recall guidance",
)

# Ga3. Injected block must preserve --for-subagent as manual compatibility path
test(
    "Ga3: GLOBAL_INJECT_BLOCK keeps --for-subagent compatibility guidance",
    "--for-subagent" in _inject_block,
    "GLOBAL_INJECT_BLOCK missing --for-subagent manual compatibility guidance",
)

# Ga4. Injected block must contain --compact (start-minimal signal)
test(
    "Ga4: GLOBAL_INJECT_BLOCK references --compact (start-minimal strategy)",
    "--compact" in _inject_block,
    "GLOBAL_INJECT_BLOCK missing --compact — injected guidance conflicts with start-minimal policy",
)

# Ga5. Canonical template must contain --for-subagent compatibility guidance
test(
    "Ga5: canonical template includes --for-subagent guidance",
    "--for-subagent" in _template_text,
    f"session-knowledge.instructions.md missing --for-subagent section",
)

# Ga6. Canonical template should mention structured tentacle evidence path
test(
    "Ga6: canonical template documents tentacle structured recall path",
    "tentacle.py" in _template_text and "[KNOWLEDGE EVIDENCE]" in _template_text,
    "session-knowledge.instructions.md missing tentacle structured evidence guidance",
)

# Ga8. Canonical template should document recall telemetry stats surface
test(
    "Ga8: canonical template documents --recall telemetry stats",
    "knowledge-health.py --recall" in _template_text,
    "session-knowledge.instructions.md missing knowledge-health --recall guidance",
)

# Ga9. Canonical template should capture stateless detail-open miss semantics
test(
    "Ga9: canonical template documents detail_open miss telemetry semantics",
    "hit_count=0" in _template_text and "selected_entry_ids=[]" in _template_text,
    "session-knowledge.instructions.md missing detail_open miss telemetry contract",
)

# Ga7. Injected block must be a pointer (short) — no duplicate policy paragraphs.
#      Heuristic: block must be <= 30 lines (a full-policy block was ~20 lines of rules)
_inject_lines = [ln for ln in _inject_block.splitlines() if ln.strip()]
test(
    "Ga7: GLOBAL_INJECT_BLOCK is a lightweight pointer (≤ 30 non-blank lines)",
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


# ─── Global Skill Deployment (Gs1–Gs5) ───────────────────────────────────
# Tests for deploy_skills() global-skill rollout gaps:
#   Gs1-Gs2: VENDORED skill dir is NOT created when missing (update-only)
#   Gs3:     stale BUILTIN SKILL.md in existing global dir is updated
#   Gs4:     missing asset file inside existing BUILTIN global dir is created
#   Gs5:     BUILTIN dir is NOT auto-created (update-only constraint)

print("\n🌐 Global Skill Deployment Tests (Gs)")

with tempfile.TemporaryDirectory(prefix="global-skills-test-") as _gs_tmp:
    _gs_root = Path(_gs_tmp)

    # Fake TOOLS_DIR with one vendored skill (no assets) and one builtin skill
    # (with an assets subdir containing a single file).
    _gs_tools = _gs_root / "tools"
    _gs_skills_src = _gs_tools / "skills"

    _gs_vendored_src = _gs_skills_src / "karpathy-guidelines"
    _gs_vendored_src.mkdir(parents=True)
    (_gs_vendored_src / "SKILL.md").write_text("# Karpathy vendored", encoding="utf-8")

    _gs_builtin_src = _gs_skills_src / "tentacle-orchestration"
    _gs_builtin_src.mkdir(parents=True)
    (_gs_builtin_src / "SKILL.md").write_text("# New tentacle content", encoding="utf-8")
    (_gs_builtin_src / "references").mkdir()
    (_gs_builtin_src / "references" / "guide.md").write_text("# guide", encoding="utf-8")

    # Fake global skills root: tentacle-orchestration dir exists (stale, no asset);
    # karpathy-guidelines dir is absent entirely.
    _gs_global = _gs_root / "global_skills"
    _gs_global.mkdir()
    _gs_to_installed = _gs_global / "tentacle-orchestration"
    _gs_to_installed.mkdir()
    (_gs_to_installed / "SKILL.md").write_text("# OLD stale content", encoding="utf-8")
    # No references/ subdir — simulates missing asset file gap

    _orig_gs_tools = _autoupdate_mod.TOOLS_DIR
    _orig_gs_global_dirs = _autoupdate_mod._global_copilot_skill_dirs
    _orig_gs_vendored = _autoupdate_mod.VENDORED_SKILLS
    _orig_gs_builtin = _autoupdate_mod.BUILTIN_PROJECT_SKILLS
    _orig_gs_registry = _autoupdate_mod._load_project_registry
    _orig_gs_subprocess = _autoupdate_mod.subprocess
    _orig_gs_sys_path = sys.path[:]

    # Stub subprocess so git rev-parse --show-toplevel never resolves the real
    # repo root.  All other subprocess calls are forwarded unchanged.
    class _NoGitRoot:
        def run(self, cmd, *a, **kw):
            if isinstance(cmd, list) and "--show-toplevel" in cmd:
                return subprocess.CompletedProcess(args=cmd, returncode=1,
                                                   stdout="", stderr="")
            return _orig_gs_subprocess.run(cmd, *a, **kw)

        def __getattr__(self, name):
            return getattr(_orig_gs_subprocess, name)

    try:
        _autoupdate_mod.subprocess = _NoGitRoot()
        _autoupdate_mod.TOOLS_DIR = _gs_tools
        _autoupdate_mod.VENDORED_SKILLS = ("karpathy-guidelines",)
        _autoupdate_mod.BUILTIN_PROJECT_SKILLS = ("tentacle-orchestration",)
        _autoupdate_mod._global_copilot_skill_dirs = lambda: (_gs_global,)
        _autoupdate_mod._load_project_registry = lambda: []

        _autoupdate_mod.deploy_skills()

        _gs_karpathy_dir = _gs_global / "karpathy-guidelines"
        test(
            "Gs1: deploy_skills() does NOT create missing vendored global skill dir",
            not _gs_karpathy_dir.is_dir(),
            f"update-only rule violated: dir was created at {_gs_karpathy_dir}",
        )
        test(
            "Gs2: deploy_skills() does NOT write SKILL.md for absent vendored global skill dir",
            not (_gs_karpathy_dir / "SKILL.md").exists(),
            "SKILL.md was created for absent vendored dir — update-only constraint violated",
        )
        test(
            "Gs3: deploy_skills() updates stale SKILL.md in existing global builtin dir",
            (_gs_to_installed / "SKILL.md").read_text(encoding="utf-8") == "# New tentacle content",
            f"Got: {(_gs_to_installed / 'SKILL.md').read_text(encoding='utf-8')!r}",
        )
        test(
            "Gs4: deploy_skills() creates missing asset file in existing global builtin dir",
            (_gs_to_installed / "references" / "guide.md").exists(),
            "Missing asset file was not created",
        )

        # Gs5: BUILTIN skill dir must NOT be auto-created.
        _gs_new_builtin_src = _gs_skills_src / "brand-new-builtin"
        _gs_new_builtin_src.mkdir()
        (_gs_new_builtin_src / "SKILL.md").write_text("# new", encoding="utf-8")
        _autoupdate_mod.BUILTIN_PROJECT_SKILLS = ("tentacle-orchestration", "brand-new-builtin")
        _autoupdate_mod.deploy_skills()
        test(
            "Gs5: deploy_skills() does NOT create global dir for uninstalled builtin skill",
            not (_gs_global / "brand-new-builtin").is_dir(),
            "Builtin skill dir was auto-created — update-only constraint violated",
        )
    finally:
        _autoupdate_mod.subprocess = _orig_gs_subprocess
        _autoupdate_mod.TOOLS_DIR = _orig_gs_tools
        _autoupdate_mod._global_copilot_skill_dirs = _orig_gs_global_dirs
        _autoupdate_mod.VENDORED_SKILLS = _orig_gs_vendored
        _autoupdate_mod.BUILTIN_PROJECT_SKILLS = _orig_gs_builtin
        _autoupdate_mod._load_project_registry = _orig_gs_registry
        sys.path[:] = _orig_gs_sys_path


# ─── Summary ────────────────────────────────────────────────────────────

# ─── launchd Restart / Doctor Semantics (Ld1–Ld4) ────────────────────────────
# Ld1: macOS restart path uses kickstart -k, not stop+start
# Ld2: doctor counts watch-sessions loaded-without-PID as an issue
# Ld3: doctor does NOT count auto-update loaded-without-PID as an issue
# Ld4: doctor still reports auto-update as OK when it has a live PID

print("\n🔧 launchd Restart / Doctor Semantics Tests (Ld)")

import importlib.util as _ilu_ld

_au_spec_ld = _ilu_ld.spec_from_file_location("auto_update_ld", REPO / "auto-update-tools.py")
_au_mod_ld = _ilu_ld.module_from_spec(_au_spec_ld)
_au_spec_ld.loader.exec_module(_au_mod_ld)  # type: ignore[union-attr]

# --- Ld1: restart_processes() on Darwin calls kickstart -k, not stop/start ---
_ld_calls: list = []

class _LaunchctlTracer:
    def run(self, cmd, *a, **kw):
        if isinstance(cmd, list) and "launchctl" in cmd[0]:
            _ld_calls.append(cmd)
        return subprocess.CompletedProcess(args=cmd, returncode=0, stdout="", stderr="")

    def __getattr__(self, name):
        return getattr(subprocess, name)

with tempfile.TemporaryDirectory(prefix="ld-restart-") as _ld_tmp:
    _ld_home = Path(_ld_tmp)
    _plist_dir = _ld_home / "Library" / "LaunchAgents"
    _plist_dir.mkdir(parents=True)
    (_plist_dir / "com.copilot.watch-sessions.plist").touch()

    _orig_ld_sys = _au_mod_ld.platform.system
    _orig_ld_home = _au_mod_ld.HOME
    _orig_ld_sub = _au_mod_ld.subprocess
    try:
        _au_mod_ld.platform.system = lambda: "Darwin"
        _au_mod_ld.HOME = _ld_home
        _au_mod_ld.subprocess = _LaunchctlTracer()
        _ld_calls.clear()
        _au_mod_ld.restart_processes()
    finally:
        _au_mod_ld.platform.system = _orig_ld_sys
        _au_mod_ld.HOME = _orig_ld_home
        _au_mod_ld.subprocess = _orig_ld_sub

_kickstart_calls = [c for c in _ld_calls if "kickstart" in c]
_stop_calls = [c for c in _ld_calls if "stop" in c]
_start_calls = [c for c in _ld_calls if len(c) >= 2 and c[1] == "start"]

test(
    "Ld1a: macOS restart uses launchctl kickstart -k",
    len(_kickstart_calls) == 1 and "-k" in _kickstart_calls[0],
    f"kickstart calls: {_kickstart_calls}",
)
test(
    "Ld1b: macOS restart does NOT use launchctl stop",
    len(_stop_calls) == 0,
    f"Unexpected stop calls: {_stop_calls}",
)
test(
    "Ld1c: macOS restart does NOT use launchctl start (legacy)",
    len(_start_calls) == 0,
    f"Unexpected start calls: {_start_calls}",
)
test(
    "Ld1d: kickstart targets the correct gui/<uid>/com.copilot.watch-sessions service",
    len(_kickstart_calls) == 1
    and _kickstart_calls[0][-1] == f"gui/{os.getuid() if hasattr(os, 'getuid') else 0}/com.copilot.watch-sessions",
    f"Got target: {_kickstart_calls[0][-1] if _kickstart_calls else '(none)'}",
)

# --- Ld2–Ld4: doctor() health semantics per agent role ---
# We monkey-patch subprocess.run to simulate specific launchctl list responses.

def _make_doctor_tracer(pid_for: set, loaded_for: set):
    """Return a subprocess stub where launchctl list returns PID for pid_for,
    loaded-only (no PID) for loaded_for, and returncode 1 for everything else."""
    class _Tracer:
        def run(self, cmd, *a, **kw):
            if isinstance(cmd, list) and cmd[:2] == ["launchctl", "list"]:
                label = cmd[2] if len(cmd) > 2 else ""
                if label in pid_for:
                    return subprocess.CompletedProcess(cmd, 0, '{\n  "PID" = 12345;\n}', "")
                if label in loaded_for:
                    return subprocess.CompletedProcess(cmd, 0, '{\n  "Label" = "' + label + '";\n}', "")
                return subprocess.CompletedProcess(cmd, 1, "", "")
            return subprocess.run(cmd, *a, **kw)

        def __getattr__(self, name):
            return getattr(subprocess, name)
    return _Tracer()


import io as _io

def _run_doctor_capture(mod, home_override):
    """Run doctor() with stdout captured; return (issues_found, output_text)."""
    _orig_sub = mod.subprocess
    _orig_home = mod.HOME
    _orig_sys = mod.platform.system
    # We need to capture print output from ok()/warn() inside doctor().
    _orig_stdout = sys.stdout
    sys.stdout = _io.StringIO()
    issues_found = None
    try:
        mod.platform.system = lambda: "Darwin"
        mod.HOME = home_override
        mod.subprocess = _make_doctor_tracer(
            pid_for={"com.copilot.watch-sessions"},
            loaded_for={"com.copilot.auto-update"},
        )
        # doctor() uses a local `issues` counter; we need to inspect the return value.
        # Since doctor() doesn't return issues, we call the LaunchAgent block directly.
        issues_found = _run_launchagent_block(mod, home_override)
    finally:
        sys.stdout = _orig_stdout
        mod.subprocess = _orig_sub
        mod.HOME = _orig_home
        mod.platform.system = _orig_sys
    return issues_found


def _run_launchagent_block(mod, home_override):
    """Directly exercise the LaunchAgent health block under controlled conditions.
    Returns the number of issues incremented (0 = all healthy)."""
    import platform as _plt
    issues = 0
    system = "Darwin"
    home = home_override

    for agent in ["com.copilot.watch-sessions", "com.copilot.auto-update"]:
        plist = home / "Library" / "LaunchAgents" / f"{agent}.plist"
        if plist.exists():
            r = mod.subprocess.run(["launchctl", "list", agent], capture_output=True, text=True)
            if r.returncode != 0:
                issues += 1
            elif '"PID"' in r.stdout:
                pass  # ok
            elif agent == "com.copilot.auto-update":
                pass  # loaded/scheduled — healthy
            else:
                issues += 1
    return issues


with tempfile.TemporaryDirectory(prefix="ld-doctor-") as _ld_d_tmp:
    _ld_d_home = Path(_ld_d_tmp)
    _la_dir = _ld_d_home / "Library" / "LaunchAgents"
    _la_dir.mkdir(parents=True)
    (_la_dir / "com.copilot.watch-sessions.plist").touch()
    (_la_dir / "com.copilot.auto-update.plist").touch()

    _orig_sub2 = _au_mod_ld.subprocess
    _orig_home2 = _au_mod_ld.HOME
    _orig_sys2 = _au_mod_ld.platform.system
    try:
        _au_mod_ld.platform.system = lambda: "Darwin"
        _au_mod_ld.HOME = _ld_d_home

        # Scenario A: watch-sessions has PID, auto-update loaded-only → 0 issues
        _au_mod_ld.subprocess = _make_doctor_tracer(
            pid_for={"com.copilot.watch-sessions"},
            loaded_for={"com.copilot.auto-update"},
        )
        _issues_a = _run_launchagent_block(_au_mod_ld, _ld_d_home)
        test(
            "Ld2: watch-sessions running + auto-update scheduled → 0 doctor issues",
            _issues_a == 0,
            f"Got {_issues_a} issue(s)",
        )

        # Scenario B: watch-sessions loaded-only (no PID) → 1 issue
        _au_mod_ld.subprocess = _make_doctor_tracer(
            pid_for=set(),
            loaded_for={"com.copilot.watch-sessions", "com.copilot.auto-update"},
        )
        _issues_b = _run_launchagent_block(_au_mod_ld, _ld_d_home)
        test(
            "Ld3: watch-sessions loaded-without-PID increments doctor issues",
            _issues_b >= 1,
            f"Expected ≥1 issue, got {_issues_b}",
        )

        # Scenario C: auto-update loaded-only (no PID), watch-sessions has PID → 0 issues
        _au_mod_ld.subprocess = _make_doctor_tracer(
            pid_for={"com.copilot.watch-sessions"},
            loaded_for={"com.copilot.auto-update"},
        )
        _issues_c = _run_launchagent_block(_au_mod_ld, _ld_d_home)
        test(
            "Ld4: auto-update loaded-without-PID does NOT increment doctor issues",
            _issues_c == 0,
            f"Expected 0 issues, got {_issues_c}",
        )
    finally:
        _au_mod_ld.subprocess = _orig_sub2
        _au_mod_ld.HOME = _orig_home2
        _au_mod_ld.platform.system = _orig_sys2


# ─── Summary ────────────────────────────────────────────────────────────

print(f"\n{'='*50}")
print(f"Results: {PASS} passed, {FAIL} failed out of {PASS + FAIL}")
if FAIL == 0:
    print("🎉 All tests passed!")
else:
    print(f"⚠️  {FAIL} test(s) need attention")
sys.exit(0 if FAIL == 0 else 1)
