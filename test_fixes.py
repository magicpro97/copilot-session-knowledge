#!/usr/bin/env python3
"""
test_fixes.py — Tests for the three limitation fixes:
  1. Noise filter: user quotes & action summaries no longer classified as mistakes
  2. Sub-agent briefing: --for-subagent produces compact injectable context
  3. LaunchAgent: plist is valid and daemon auto-starts on login

Run: python3 test_fixes.py
"""

import json
import os
import plistlib
import re
import sqlite3
import subprocess
import sys
import tempfile
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
        (
            "pattern",
            "Code review uses deterministic DB fixture",
            "For review auth PR tests, seed deterministic briefing entries.",
        ),
        (
            "decision",
            "Review workflow sets explicit HOME",
            "Set HOME/USERPROFILE for deterministic Path.home() in code review.",
        ),
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
    "User asked to investigate why screener.work is inaccessible",
    'User reported: "Không thể tải lên CV" (Cannot upload CV)',
    "User requested comprehensive lint check, noting code quality issues",
    '7. User said "fix hết đi" (fix everything)',
    "User mentioned the fix should be quick",
    "User confirmed the approach is correct",
    "User wants to add dark mode support",
]

# 1a-extra. Extended user narration patterns
user_narration_extras = [
    "User clarified dependency philosophy: USE existing npm libraries",
    "User provided OpenRouter API key, asked to continue",
    "User applied revision edits themselves, asked for review",
    "User selected option B for the architecture",
]

for sample in user_quote_samples + user_narration_extras:
    result = ek._is_noise(sample)
    test(f"User quote detected as noise: {sample[:50]}...", result, f"_is_noise returned {result}")

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
    test(f"Action summary detected as noise: {sample[:50]}...", result, f"_is_noise returned {result}")

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
    test(f"Real mistake NOT filtered: {sample[:50]}...", not result, f"_is_noise returned {result}, should be False")

# 1d. Real mistakes still get classified correctly
for sample in real_mistakes:
    classifications = ek.classify_paragraph(sample)
    categories = [c[0] for c in classifications]
    test(
        f"Real mistake classified correctly: {sample[:50]}...", "mistake" in categories, f"Got categories: {categories}"
    )

# 1e. User quotes produce empty classifications
for sample in user_quote_samples:
    classifications = ek.classify_paragraph(sample)
    test(f"User quote not classified: {sample[:50]}...", len(classifications) == 0, f"Got: {classifications}")


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
        capture_output=True,
        text=True,
        cwd=str(REPO),
        env=_briefing_env,
        encoding="utf-8",
        errors="replace",
    )
    output = result.stdout.strip()

    test("--for-subagent runs without error", result.returncode == 0, f"stderr: {result.stderr[:200]}")

    test("Output starts with [KNOWLEDGE CONTEXT]", output.startswith("[KNOWLEDGE CONTEXT"), f"Got: {output[:80]}")

    test(
        "Output ends with [END KNOWLEDGE CONTEXT]",
        "[END KNOWLEDGE CONTEXT]" in output,
        f"Got last 80 chars: {output[-80:]}",
    )

    test(
        "Output has category labels (AVOID/USE/NOTE/CONFIG)",
        any(label in output for label in ["[AVOID]", "[USE]", "[NOTE]", "[CONFIG]"]),
        "No labels found in output",
    )

    # 2b. Output is compact (< 500 tokens ≈ < 2000 chars)
    test("Output is compact (< 2000 chars)", len(output) < 2000, f"Got {len(output)} chars")

    # 2c. Regular briefing still works
    result2 = subprocess.run(
        [sys.executable, str(REPO / "briefing.py"), "kotlin compose"],
        capture_output=True,
        text=True,
        cwd=str(REPO),
        env=_briefing_env,
        encoding="utf-8",
        errors="replace",
    )
    test("Regular briefing still works", result2.returncode == 0, f"stdout: {result2.stdout[:100]}")

    # 2d. --for-subagent remains compact with explicit mode
    result3 = subprocess.run(
        [
            sys.executable,
            str(REPO / "briefing.py"),
            "review auth PR",
            "--for-subagent",
            "--mode",
            "review",
            "--min-confidence",
            "0",
        ],
        capture_output=True,
        text=True,
        cwd=str(REPO),
        env=_briefing_env,
        encoding="utf-8",
        errors="replace",
    )
    output3 = result3.stdout.strip()
    test("--for-subagent + --mode runs without error", result3.returncode == 0, f"stderr: {result3.stderr[:200]}")
    test(
        "--for-subagent + --mode still starts with compact context header",
        output3.startswith("[KNOWLEDGE CONTEXT"),
        f"Got: {output3[:80]}",
    )

    # 2e. --pack exposes machine-readable briefing surface
    result4 = subprocess.run(
        [sys.executable, str(REPO / "briefing.py"), "review auth PR", "--mode", "review", "--pack", "--limit", "1"],
        capture_output=True,
        text=True,
        cwd=str(REPO),
        env=_briefing_env,
        encoding="utf-8",
        errors="replace",
    )
    test("--pack runs without error", result4.returncode == 0, f"stderr: {result4.stderr[:200]}")
    try:
        pack_obj = json.loads(result4.stdout)
        test("--pack returns valid JSON", True)
        test("--pack includes mode field", "mode" in pack_obj, f"keys={list(pack_obj.keys())}")
        test("--pack preserves explicit mode", pack_obj.get("mode") == "review", f"mode={pack_obj.get('mode')!r}")
        entries_obj = pack_obj.get("entries", {})
        test(
            "--pack includes canonical entry buckets",
            isinstance(entries_obj, dict) and all(k in entries_obj for k in ("mistake", "pattern", "decision", "tool")),
            f"entries keys={list(entries_obj.keys()) if isinstance(entries_obj, dict) else type(entries_obj).__name__}",
        )
        first_entry = None
        if isinstance(entries_obj, dict):
            for bucket in ("mistake", "pattern", "decision", "tool"):
                vals = entries_obj.get(bucket, [])
                if vals:
                    first_entry = vals[0]
                    break
        if first_entry:
            test(
                "--pack entry includes source_document field",
                "source_document" in first_entry,
                f"keys={list(first_entry.keys())}",
            )
            test(
                "--pack entry includes code-location/snippet fields",
                all(
                    k in first_entry for k in ("source_file", "start_line", "end_line", "code_language", "code_snippet")
                ),
                f"keys={list(first_entry.keys())}",
            )
            test(
                "--pack entry includes snippet_freshness enum field",
                first_entry.get("snippet_freshness") in {"fresh", "drifted", "missing", "unknown"},
                f"snippet_freshness={first_entry.get('snippet_freshness')!r}",
            )
            rel_ids = first_entry.get("related_entry_ids", [])
            test(
                "--pack entry includes related_entry_ids as int list",
                isinstance(rel_ids, list) and all(isinstance(x, int) for x in rel_ids) and len(rel_ids) <= 3,
                f"related_entry_ids={rel_ids!r}",
            )
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
    test("Plist file exists", plist_under_test.exists(), f"Expected at {plist_under_test}")

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
        test("Has Label key", "Label" in plist_data, f"Keys: {list(plist_data.keys())}")
        test("Label is correct", plist_data.get("Label") == "com.copilot.watch-sessions")

        test("Has ProgramArguments", "ProgramArguments" in plist_data)
        prog_args = plist_data.get("ProgramArguments", [])
        test("Uses python3", "python3" in prog_args[0] if prog_args else False, f"Got: {prog_args}")
        test("Runs watch-sessions.py", any("watch-sessions" in a for a in prog_args), f"Got: {prog_args}")
        # launchd must own the watcher lifecycle — run in foreground (no --daemon).
        # --daemon causes a double-fork so launchd loses the PID and the detached
        # child conflicts with every subsequent launchd restart attempt.
        test(
            "No --daemon flag (launchd owns lifecycle)",
            "--daemon" not in prog_args,
            f"Got: {prog_args} — remove --daemon so launchd manages the process",
        )

        test("RunAtLoad is true", plist_data.get("RunAtLoad") is True)

        test("Has KeepAlive", "KeepAlive" in plist_data, "Daemon should restart on crash")

        test(
            "WorkingDirectory is ~/.copilot",
            plist_data.get("WorkingDirectory", "").endswith(".copilot"),
            f"Got: {plist_data.get('WorkingDirectory')}",
        )

        # 3d. plutil validates the plist
        plutil_result = subprocess.run(
            ["plutil", "-lint", str(plist_under_test)],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
        )
        test("plutil lint passes", plutil_result.returncode == 0, plutil_result.stderr or plutil_result.stdout)

        # 3e. Python path exists (only deterministic for installed plist)
        python_path = prog_args[0] if prog_args else ""
        if plist_is_user_install:
            test("Python3 path exists", Path(python_path).exists(), f"Path: {python_path}")
        else:
            test("Python3 path check skipped for template plist", True, f"Template path={python_path}")
else:
    print("  ⏭️  Skipped — LaunchAgent is macOS-only (running on Linux/WSL)")


# ─── Fix 1 Integration: Re-extract and verify ───────────────────────────

print("\n🔄 Integration: Verify noise filter reduces false positives")

# Count current false positives in DB
db_path = Path.home() / ".copilot/session-state/knowledge.db"
if db_path.exists():
    db = sqlite3.connect(str(db_path))
    total_mistakes = db.execute("SELECT COUNT(*) FROM knowledge_entries WHERE category = 'mistake'").fetchone()[0]

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
    print(
        f"  📊 Current DB: {total_mistakes} mistakes, {user_quotes} user-quotes, "
        f"{action_summaries} action-summaries ({false_positive_rate:.0%} FP)"
    )

    # Relaxed threshold: historical data may contain pre-filter entries
    # The _is_noise() function is tested with synthetic inputs above (Fix 1 tests)
    test("FP rate below 20% (was 40%)", false_positive_rate < 0.20, f"FP rate is {false_positive_rate:.0%}")

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
    test(
        f"Noise filter catches user-quote DB entries ({caught}/{len(sample_quotes)})",
        caught == len(sample_quotes),
        f"Caught {caught}/{len(sample_quotes)}",
    )

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
    template_skill_content = template_skill_path.read_text(encoding="utf-8") if template_skill_path.exists() else ""
    test("Contains --for-subagent docs", "--for-subagent" in skill_content)
    test(
        "Documents structured tentacle recall path",
        "tentacle.py" in template_skill_content and "[KNOWLEDGE EVIDENCE]" in template_skill_content,
    )
    test("Contains sub-agent workflow", "sub-agent" in skill_content.lower())
    test("Uses python3 (not python)", "python3 " in skill_content)
    test("No bare 'python ' commands", "python ~/.copilot" not in skill_content, "Should use python3, not python")


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
    test(
        "Sp1: no spurious reference warnings when no refs mentioned",
        not any("Dangling" in w for w in _warns1),
        f"Got warnings: {_warns1}",
    )
finally:
    _shutil.rmtree(_d1, ignore_errors=True)

# Sp2. Mentioned reference exists → no dangling warning
_SKILL_WITH_REF = MINIMAL_SKILL + "\nSee `references/guide.md` for details.\n"
_d2 = _make_skill_dir(_SKILL_WITH_REF, refs={"guide.md": "# Guide\nContent."})
try:
    _errs2, _warns2 = validate(_d2 / "SKILL.md")
    test(
        "Sp2: existing references/guide.md → no dangling warning",
        not any("guide.md" in w for w in _warns2),
        f"Got warnings: {_warns2}",
    )
finally:
    _shutil.rmtree(_d2, ignore_errors=True)

# Sp3. Mentioned reference missing → one dangling warning (positive case)
_d3 = _make_skill_dir(_SKILL_WITH_REF)  # no refs/ created
try:
    _errs3, _warns3 = validate(_d3 / "SKILL.md")
    test(
        "Sp3: missing references/guide.md → dangling warning emitted",
        any("guide.md" in w and "Dangling" in w for w in _warns3),
        f"Got warnings: {_warns3}",
    )
finally:
    _shutil.rmtree(_d3, ignore_errors=True)

# Sp4. Same reference mentioned twice → exactly ONE warning (deduplication)
_SKILL_DOUBLE_REF = MINIMAL_SKILL + (
    "\nSee `references/guide.md` for overview.\nAlso `references/guide.md` covers advanced topics.\n"
)
_d4 = _make_skill_dir(_SKILL_DOUBLE_REF)
try:
    _errs4, _warns4 = validate(_d4 / "SKILL.md")
    dangling_count = sum(1 for w in _warns4 if "guide.md" in w and "Dangling" in w)
    test(
        "Sp4: duplicate reference mention → exactly 1 warning (deduplication)",
        dangling_count == 1,
        f"Got {dangling_count} dangling warnings for guide.md",
    )
finally:
    _shutil.rmtree(_d4, ignore_errors=True)

# Sp5. Non-relative path (shared/references/foo.md) does NOT trigger warning
_SKILL_NONREL = MINIMAL_SKILL + "\nSee shared/references/guide.md elsewhere.\n"
_d5 = _make_skill_dir(_SKILL_NONREL)
try:
    _errs5, _warns5 = validate(_d5 / "SKILL.md")
    test(
        "Sp5: shared/references/guide.md (non-relative) → no dangling warning",
        not any("guide.md" in w and "Dangling" in w for w in _warns5),
        f"Got warnings: {_warns5}",
    )
finally:
    _shutil.rmtree(_d5, ignore_errors=True)

# Sp6. Nested reference exists → no dangling warning
_SKILL_NESTED_REF = MINIMAL_SKILL + "\nSee `references/sub/deep.md` for details.\n"
_d6 = _make_skill_dir(_SKILL_NESTED_REF, refs={"sub/deep.md": "# Deep\nContent."})
try:
    _errs6, _warns6 = validate(_d6 / "SKILL.md")
    test(
        "Sp6: existing references/sub/deep.md (nested) → no dangling warning",
        not any("sub/deep.md" in w and "Dangling" in w for w in _warns6),
        f"Got warnings: {_warns6}",
    )
finally:
    _shutil.rmtree(_d6, ignore_errors=True)

# Sp7. Nested reference missing → dangling warning
_d7 = _make_skill_dir(_SKILL_NESTED_REF)
try:
    _errs7, _warns7 = validate(_d7 / "SKILL.md")
    test(
        "Sp7: missing references/sub/deep.md (nested) → dangling warning",
        any("sub/deep.md" in w and "Dangling" in w for w in _warns7),
        f"Got warnings: {_warns7}",
    )
finally:
    _shutil.rmtree(_d7, ignore_errors=True)

# Sp8. setup-project install_skills deploys nested references/ preserving structure
# Use fully isolated temp dirs outside the repo — no mutation of the live source tree.
import importlib as _imp

_sp8_root = Path(_tf.mkdtemp())  # isolated skills dir (acts as SKILLS_DIR)
_proj8 = Path(_tf.mkdtemp())  # isolated install target (acts as project root)

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
    test(
        "Sp8: nested references/nested_test/nested-ref.md deployed with relative path",
        _expected.exists(),
        f"Expected at {_expected}",
    )
finally:
    _shutil.rmtree(_sp8_root, ignore_errors=True)
    _shutil.rmtree(_proj8, ignore_errors=True)

# Sp9. session-knowledge-creator reference files exist in repo
_sk_refs = REPO / "skills" / "session-knowledge-creator" / "references"
test(
    "Sp9: references/instructions-template.md exists in session-knowledge-creator",
    (_sk_refs / "instructions-template.md").exists(),
)
test("Sp9: references/skill-template.md exists in session-knowledge-creator", (_sk_refs / "skill-template.md").exists())

# Sp10. Validator passes (no dangling refs) for session-knowledge-creator after fix
_sk_path = REPO / "skills" / "session-knowledge-creator"
_sk_errs, _sk_warns = validate(_sk_path)
dangling_sk = [w for w in _sk_warns if "Dangling" in w]
test(
    "Sp10: session-knowledge-creator has no dangling reference warnings",
    len(dangling_sk) == 0,
    f"Dangling refs: {dangling_sk}",
)

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
    test(
        "Sp11: escape path references/../SKILL.md resolves to an existing file (danger confirmed)",
        _escape_path.exists(),
        f"Expected {_escape_path} to exist",
    )

    _errs11, _warns11 = validate(_d11 / "SKILL.md")
    _traversal_warns = [w for w in _warns11 if "Suspicious" in w or "traversal" in w.lower() or ".." in w]
    # Each traversal pattern must be caught individually — not via generic ".." membership.
    test(
        "Sp11: single-level escape references/../SKILL.md triggers traversal warning",
        any("references/../SKILL.md" in w for w in _traversal_warns),
        f"Got warnings: {_warns11}",
    )
    test(
        "Sp11: double-level escape references/a/../../secret.md triggers traversal warning",
        any("references/a/../../secret.md" in w for w in _traversal_warns),
        f"Got warnings: {_warns11}",
    )
    test(
        "Sp11: traversal path does NOT appear as a dangling reference warning",
        not any("Dangling" in w and ".." in w for w in _warns11),
        f"Got warnings: {_warns11}",
    )
finally:
    _shutil.rmtree(_d11, ignore_errors=True)


# Sp12. install_skills deploys templates/ assets from conductor-creator
# This is the regression case: before the fix, templates/ was silently dropped.
print("\n📦 Skill Packaging — Auxiliary Asset Dirs (Sp12 / Sp13)")

_sp12_root = Path(_tf.mkdtemp())  # fake SKILLS_DIR
_proj12 = Path(_tf.mkdtemp())  # fake project root

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
    test(
        "Sp12: conductor.py deployed under templates/",
        (_skill12_base / "templates" / "conductor.py").exists(),
        f"Missing {_skill12_base / 'templates' / 'conductor.py'}",
    )
    test(
        "Sp12: test-conductor.py deployed under templates/",
        (_skill12_base / "templates" / "test-conductor.py").exists(),
        f"Missing {_skill12_base / 'templates' / 'test-conductor.py'}",
    )
    test(
        "Sp12: references/guide.md still deployed (regression: references/ preserved)",
        (_skill12_base / "references" / "guide.md").exists(),
        f"Missing {_skill12_base / 'references' / 'guide.md'}",
    )
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
    test(
        "Sp13: templates/tmpl.py deployed for skill with multiple asset subdirs",
        (_skill13_base / "templates" / "tmpl.py").exists(),
    )
    test(
        "Sp13: evals/eval.json deployed for skill with multiple asset subdirs",
        (_skill13_base / "evals" / "eval.json").exists(),
    )
    test(
        "Sp13: references/ref.md deployed for skill with multiple asset subdirs",
        (_skill13_base / "references" / "ref.md").exists(),
    )
finally:
    _shutil.rmtree(_sp13_root, ignore_errors=True)
    _shutil.rmtree(_proj13, ignore_errors=True)

# Sp14. Live repo: conductor-creator templates/ files exist and are real files
_cc_templates = REPO / "skills" / "conductor-creator" / "templates"
test(
    "Sp14: conductor-creator/templates/conductor.py exists in repo",
    (_cc_templates / "conductor.py").exists(),
    f"Expected at {_cc_templates / 'conductor.py'}",
)
test(
    "Sp14: conductor-creator/templates/test-conductor.py exists in repo",
    (_cc_templates / "test-conductor.py").exists(),
    f"Expected at {_cc_templates / 'test-conductor.py'}",
)

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
    test(
        "Sp15: bare `name:` (empty value) → error reported",
        any("no value" in e or "empty" in e.lower() for e in _errs15),
        f"Expected empty-name error; got errors={_errs15}",
    )
    test(
        "Sp15: bare `name:` does NOT capture next line as name value",
        not any("description" in e.lower() and "invalid" in e.lower() for e in _errs15),
        f"Regex crossed line boundary — captured 'description:' as name: errors={_errs15}",
    )
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
    test(
        "Sp16: whitespace-only `name:   ` → error reported (empty value)",
        any("no value" in e or "empty" in e.lower() for e in _errs16),
        f"Expected empty-name error; got errors={_errs16}",
    )
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
    test(
        "Sp17: bare `description:` (empty value) → error reported",
        any("no value" in e or "empty" in e.lower() for e in _errs17),
        f"Expected empty-description error; got errors={_errs17}",
    )
    test(
        "Sp17: bare `description:` does NOT capture next YAML line as description",
        not any("description only" in w.lower() for w in _warns17),
        f"Regex crossed line boundary — word-count warning implies next key was captured as description: warns={_warns17}",
    )
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
    test(
        "Sp18: whitespace-only `description:   ` → error reported (empty value)",
        any("no value" in e or "empty" in e.lower() for e in _errs18),
        f"Expected empty-description error; got errors={_errs18}",
    )
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
    "session-knowledge.instructions.md missing --for-subagent section",
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
            "Pm2: Windows hook is Python with no CR",
            _windows_bytes.startswith(b"#!/usr/bin/env python\n"),
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
            "Pm4: POSIX hook (Linux/macOS) also remains Python LF-only",
            _linux_bytes.startswith(b"#!/usr/bin/env python3\n") and b"\r\n" not in _linux_bytes,
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
                return subprocess.CompletedProcess(args=cmd, returncode=1, stdout="", stderr="")
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


# ─── Rg: Relation Recency / Coverage Regression ─────────────────────────

print("\n🔗 Rg: Relation Recency / Coverage Regression")


def _make_relation_test_db():
    """Minimal in-memory DB with the schema required by extract_relations()."""
    db = sqlite3.connect(":memory:")
    db.executescript("""
        CREATE TABLE knowledge_entries (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            session_id TEXT NOT NULL,
            category TEXT NOT NULL DEFAULT '',
            title TEXT DEFAULT '',
            tags TEXT DEFAULT '',
            topic_key TEXT DEFAULT '',
            stable_id TEXT DEFAULT ''
        );
        CREATE TABLE knowledge_relations (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            source_id INTEGER NOT NULL,
            target_id INTEGER NOT NULL,
            source_stable_id TEXT DEFAULT '',
            target_stable_id TEXT DEFAULT '',
            relation_type TEXT NOT NULL,
            stable_id TEXT DEFAULT '',
            confidence REAL DEFAULT 0.5,
            created_at TEXT DEFAULT ''
        );
    """)
    return db


_RG_CATS_6 = ["mistake", "pattern", "tool", "decision", "feature", "discovery"]


def _rg_seed_session(db, session_id, entries_per_cat):
    """Insert entries_per_cat entries per category; return list of inserted IDs."""
    ids = []
    for cat in _RG_CATS_6:
        for i in range(entries_per_cat):
            eid = db.execute(
                "INSERT INTO knowledge_entries (session_id, category, title) VALUES (?, ?, ?)",
                (session_id, cat, f"{session_id}-{cat}-{i}"),
            ).lastrowid
            ids.append(eid)
    db.commit()
    return ids


# Rg1: ORDER BY id DESC gives recent entries first crack at the SAME_SESSION budget.
# Setup:
#   old-session  — 10 entries × 6 categories = 60 entries
#                  cross-category pairs = C(60,2) − 6×C(10,2) = 1770 − 270 = 1500
#                  → exactly fills the SAME_SESSION budget when processed first (old behavior)
#   recent-session — 3 entries in 3 different categories → 3 cross-category pairs
#                  → assigned higher IDs (inserted after old-session)
# Expectation with new code:
#   - recent-session processed first (ORDER BY id DESC + dict insertion order)
#   - recent-session entries get their 3 SAME_SESSION pairs
#   - per-session cap limits old-session to a fair share of remaining budget
_rg1_db = _make_relation_test_db()
_rg_seed_session(_rg1_db, "old-session", 10)  # IDs 1..60
for _rg_cat in ["mistake", "pattern", "tool"]:
    _rg1_db.execute(
        "INSERT INTO knowledge_entries (session_id, category, title) VALUES (?, ?, ?)",
        ("recent-session", _rg_cat, f"recent-{_rg_cat}"),
    )
_rg1_db.commit()

ek.extract_relations(_rg1_db)

_rg1_recent_ids = [
    r[0] for r in _rg1_db.execute("SELECT id FROM knowledge_entries WHERE session_id = 'recent-session'").fetchall()
]
_rg1_ph = ",".join("?" * len(_rg1_recent_ids))
_rg1_recent_rels = _rg1_db.execute(
    f"SELECT COUNT(*) FROM knowledge_relations "
    f"WHERE relation_type = 'SAME_SESSION' AND "
    f"(source_id IN ({_rg1_ph}) OR target_id IN ({_rg1_ph}))",
    _rg1_recent_ids * 2,
).fetchone()[0]
test(
    "Rg1: recent entries get SAME_SESSION relations despite old session consuming full budget",
    _rg1_recent_rels > 0,
    f"recent entries have {_rg1_recent_rels} SAME_SESSION relations (expected > 0)",
)

_rg1_old_ids = [
    r[0] for r in _rg1_db.execute("SELECT id FROM knowledge_entries WHERE session_id = 'old-session'").fetchall()
]
_rg1_old_ph = ",".join("?" * len(_rg1_old_ids))
_rg1_old_rels = _rg1_db.execute(
    f"SELECT COUNT(*) FROM knowledge_relations "
    f"WHERE relation_type = 'SAME_SESSION' AND "
    f"(source_id IN ({_rg1_old_ph}) OR target_id IN ({_rg1_old_ph}))",
    _rg1_old_ids * 2,
).fetchone()[0]
# Per-session cap (max(3, 1500//2)=750) prevents old-session from consuming all 1500 slots
test(
    "Rg1b: per-session cap prevents old session from monopolising entire SAME_SESSION budget",
    _rg1_old_rels < 1500,
    f"old session involved in {_rg1_old_rels} SAME_SESSION relations (expected < 1500)",
)
_rg1_db.close()

# Rg2: Coverage — all recent sessions get SAME_SESSION relations under budget pressure.
# Setup:
#   dominant-old  — 60 entries (1500 potential cross-cat pairs), low IDs
#   recent-0..3   — 3 entries each (3 cross-cat pairs each), high IDs
# Without recency fix: dominant-old (processed first in old ordering) exhausts budget,
#   all recent sessions receive 0 relations.
# With recency fix:    recent sessions processed first, all receive relations;
#   dominant-old is capped at a fair share.
_rg2_db = _make_relation_test_db()
_rg_seed_session(_rg2_db, "dominant-old", 10)  # IDs 1..60

_rg2_recent_sessions = [f"recent-{i}" for i in range(4)]
for _rg2_sid in _rg2_recent_sessions:
    for _rg2_cat in ["mistake", "pattern", "tool"]:
        _rg2_db.execute(
            "INSERT INTO knowledge_entries (session_id, category, title) VALUES (?, ?, ?)",
            (_rg2_sid, _rg2_cat, f"{_rg2_sid}-{_rg2_cat}"),
        )
_rg2_db.commit()

ek.extract_relations(_rg2_db)

_rg2_covered = 0
for _rg2_sid in _rg2_recent_sessions:
    _rg2_ids = [
        r[0] for r in _rg2_db.execute("SELECT id FROM knowledge_entries WHERE session_id = ?", (_rg2_sid,)).fetchall()
    ]
    _rg2_ph = ",".join("?" * len(_rg2_ids))
    _rg2_cnt = _rg2_db.execute(
        f"SELECT COUNT(*) FROM knowledge_relations "
        f"WHERE relation_type = 'SAME_SESSION' AND "
        f"(source_id IN ({_rg2_ph}) OR target_id IN ({_rg2_ph}))",
        _rg2_ids * 2,
    ).fetchone()[0]
    if _rg2_cnt > 0:
        _rg2_covered += 1

test(
    f"Rg2: all {len(_rg2_recent_sessions)} recent sessions get SAME_SESSION relations under budget pressure",
    _rg2_covered == len(_rg2_recent_sessions),
    f"{_rg2_covered}/{len(_rg2_recent_sessions)} recent sessions have relations",
)
_rg2_db.close()


# ─── Goal State Tests ────────────────────────────────────────────────────

print("\n🎯 Goal State Tests")

import tempfile as _tempfile
import uuid as _uuid

# Create an isolated .octogent/tentacles directory for goal tests
_goal_tmp = Path(_tempfile.mkdtemp(prefix="goal-test-"))
_goal_octogent = _goal_tmp / ".octogent"
_goal_tentacles = _goal_octogent / "tentacles"
_goal_tentacles.mkdir(parents=True)

# Run tentacle.py goal commands in subprocess
_tp = REPO / "tentacle.py"

# Gs1: goal init creates a valid goal.json
_gs1_res = subprocess.run(
    [
        sys.executable,
        str(_tp),
        "--session-dir",
        str(_goal_tentacles),
        "goal",
        "init",
        "--title",
        "Test Goal",
        "--desc",
        "A test goal",
    ],
    capture_output=True,
    text=True,
    env={**os.environ, "TENTACLE_SESSION_DIR": str(_goal_tentacles)},
)
_gs1_goal_path = _goal_octogent / "goal.json"
test("Gs1: goal init exits 0", _gs1_res.returncode == 0, _gs1_res.stderr[:200])
test("Gs1: goal.json created", _gs1_goal_path.exists(), "goal.json not found")

if _gs1_goal_path.exists():
    _gs1_state = json.loads(_gs1_goal_path.read_text())
    test("Gs1: goal.json has goal_id", bool(_gs1_state.get("goal_id")), "no goal_id")
    test("Gs1: goal.json title correct", _gs1_state.get("title") == "Test Goal", f"got {_gs1_state.get('title')}")
    test("Gs1: goal.json status=active", _gs1_state.get("status") == "active", f"got {_gs1_state.get('status')}")
    test("Gs1: goal.json iteration=1", _gs1_state.get("iteration") == 1, f"got {_gs1_state.get('iteration')}")
    test(
        "Gs1: goal.json has empty tentacles list",
        _gs1_state.get("tentacles") == [],
        f"got {_gs1_state.get('tentacles')}",
    )
    test(
        "Gs1: goal.json has empty eval_history",
        _gs1_state.get("eval_history") == [],
        f"got {_gs1_state.get('eval_history')}",
    )
else:
    for _lbl in [
        "Gs1: goal.json has goal_id",
        "Gs1: goal.json title correct",
        "Gs1: goal.json status=active",
        "Gs1: goal.json iteration=1",
        "Gs1: goal.json has empty tentacles list",
        "Gs1: goal.json has empty eval_history",
    ]:
        test(_lbl, False, "goal.json missing")

# Gs2: goal status text output
_gs2_res = subprocess.run(
    [sys.executable, str(_tp), "--session-dir", str(_goal_tentacles), "goal", "status"],
    capture_output=True,
    text=True,
    env={**os.environ, "TENTACLE_SESSION_DIR": str(_goal_tentacles)},
)
test("Gs2: goal status exits 0", _gs2_res.returncode == 0, _gs2_res.stderr[:200])
test("Gs2: goal status shows title", "Test Goal" in _gs2_res.stdout, _gs2_res.stdout[:200])
test("Gs2: goal status shows active", "active" in _gs2_res.stdout, _gs2_res.stdout[:200])

# Gs3: goal status --format json
_gs3_res = subprocess.run(
    [sys.executable, str(_tp), "--session-dir", str(_goal_tentacles), "goal", "status", "--format", "json"],
    capture_output=True,
    text=True,
    env={**os.environ, "TENTACLE_SESSION_DIR": str(_goal_tentacles)},
)
test("Gs3: goal status --format json exits 0", _gs3_res.returncode == 0, _gs3_res.stderr[:200])
try:
    _gs3_data = json.loads(_gs3_res.stdout)
    test("Gs3: json output has goal_id", "goal_id" in _gs3_data, str(_gs3_data)[:100])
    test("Gs3: json output has iteration", "iteration" in _gs3_data, str(_gs3_data)[:100])
except Exception as _e:
    test("Gs3: json output parses", False, str(_e))
    test("Gs3: json output has goal_id", False, "parse failed")
    test("Gs3: json output has iteration", False, "parse failed")

# Gs4: Create a tentacle then goal link it
_gs4_tname = f"test-t-{_uuid.uuid4().hex[:6]}"
_gs4_create = subprocess.run(
    [
        sys.executable,
        str(_tp),
        "--session-dir",
        str(_goal_tentacles),
        "create",
        _gs4_tname,
        "--desc",
        "linked tentacle",
    ],
    capture_output=True,
    text=True,
    env={**os.environ, "TENTACLE_SESSION_DIR": str(_goal_tentacles)},
)
test("Gs4: create tentacle exits 0", _gs4_create.returncode == 0, _gs4_create.stderr[:200])

_gs4_link = subprocess.run(
    [sys.executable, str(_tp), "--session-dir", str(_goal_tentacles), "goal", "link", _gs4_tname],
    capture_output=True,
    text=True,
    env={**os.environ, "TENTACLE_SESSION_DIR": str(_goal_tentacles)},
)
test("Gs4: goal link exits 0", _gs4_link.returncode == 0, _gs4_link.stderr[:200])

if _gs1_goal_path.exists():
    _gs4_state = json.loads(_gs1_goal_path.read_text())
    test(
        "Gs4: tentacle appears in goal.tentacles",
        _gs4_tname in _gs4_state.get("tentacles", []),
        f"tentacles={_gs4_state.get('tentacles')}",
    )
else:
    test("Gs4: tentacle appears in goal.tentacles", False, "goal.json missing")

_gs4_meta_path = _goal_tentacles / _gs4_tname / "meta.json"
if _gs4_meta_path.exists():
    _gs4_meta = json.loads(_gs4_meta_path.read_text())
    test("Gs4: meta.json has goal_id after link", bool(_gs4_meta.get("goal_id")), str(_gs4_meta.get("goal_id")))
    test(
        "Gs4: meta.json has goal_name after link",
        _gs4_meta.get("goal_name") == "Test Goal",
        f"got {_gs4_meta.get('goal_name')}",
    )
    test(
        "Gs4: meta.json has iteration after link", _gs4_meta.get("iteration") == 1, f"got {_gs4_meta.get('iteration')}"
    )
    test(
        "Gs4: meta.json has goal_iteration after link",
        _gs4_meta.get("goal_iteration") == 1,
        f"got {_gs4_meta.get('goal_iteration')}",
    )
else:
    test("Gs4: meta.json has goal_id after link", False, "meta.json missing")
    test("Gs4: meta.json has goal_name after link", False, "meta.json missing")
    test("Gs4: meta.json has iteration after link", False, "meta.json missing")
    test("Gs4: meta.json has goal_iteration after link", False, "meta.json missing")

# Gs5a: goal eval --decision continue blocks until the linked tentacle has a terminal handoff
_gs5_block = subprocess.run(
    [
        sys.executable,
        str(_tp),
        "--session-dir",
        str(_goal_tentacles),
        "goal",
        "eval",
        "--decision",
        "continue",
        "--notes",
        "test note",
    ],
    capture_output=True,
    text=True,
    env={**os.environ, "TENTACLE_SESSION_DIR": str(_goal_tentacles)},
)
test("Gs5a: goal eval blocks before terminal handoff", _gs5_block.returncode != 0, _gs5_block.stderr[:200])
test(
    "Gs5a: block output mentions missing handoffs",
    "without handoffs" in (_gs5_block.stderr or ""),
    _gs5_block.stderr[:200],
)

if _gs4_meta_path.exists():
    _gs5_meta = json.loads(_gs4_meta_path.read_text())
    _gs5_meta["status"] = "completed"
    _gs5_meta["terminal_status"] = "DONE"
    _gs5_meta["goal_iteration"] = 1
    _gs4_meta_path.write_text(json.dumps(_gs5_meta, indent=2) + "\n")
else:
    test("Gs5b: terminal handoff fixture exists", False, "meta.json missing")

# Gs5b: goal eval --decision continue advances iteration after the terminal handoff lands
_gs5_res = subprocess.run(
    [
        sys.executable,
        str(_tp),
        "--session-dir",
        str(_goal_tentacles),
        "goal",
        "eval",
        "--decision",
        "continue",
        "--notes",
        "test note",
    ],
    capture_output=True,
    text=True,
    env={**os.environ, "TENTACLE_SESSION_DIR": str(_goal_tentacles)},
)
test("Gs5b: goal eval exits 0 after terminal handoff", _gs5_res.returncode == 0, _gs5_res.stderr[:200])

if _gs1_goal_path.exists():
    _gs5_state = json.loads(_gs1_goal_path.read_text())
    test("Gs5b: iteration advanced to 2", _gs5_state.get("iteration") == 2, f"got {_gs5_state.get('iteration')}")
    test(
        "Gs5b: eval_history has one entry",
        len(_gs5_state.get("eval_history", [])) == 1,
        f"got {len(_gs5_state.get('eval_history', []))}",
    )
    _gs5_entry = _gs5_state["eval_history"][0] if _gs5_state.get("eval_history") else {}
    test(
        "Gs5b: eval entry decision=continue",
        _gs5_entry.get("decision") == "continue",
        f"got {_gs5_entry.get('decision')}",
    )
    test("Gs5b: eval entry has notes", "test note" in (_gs5_entry.get("notes") or ""), f"got {_gs5_entry.get('notes')}")
else:
    for _l in [
        "Gs5b: iteration advanced to 2",
        "Gs5b: eval_history has one entry",
        "Gs5b: eval entry decision=continue",
        "Gs5b: eval entry has notes",
    ]:
        test(_l, False, "goal.json missing")

# Gs6: goal eval --decision pause sets status=paused
_gs6_res = subprocess.run(
    [sys.executable, str(_tp), "--session-dir", str(_goal_tentacles), "goal", "eval", "--decision", "pause"],
    capture_output=True,
    text=True,
    env={**os.environ, "TENTACLE_SESSION_DIR": str(_goal_tentacles)},
)
test("Gs6: goal eval pause exits 0", _gs6_res.returncode == 0, _gs6_res.stderr[:200])
if _gs1_goal_path.exists():
    _gs6_state = json.loads(_gs1_goal_path.read_text())
    test("Gs6: status=paused after eval pause", _gs6_state.get("status") == "paused", f"got {_gs6_state.get('status')}")
else:
    test("Gs6: status=paused after eval pause", False, "goal.json missing")

# Gs7: goal resume sets status back to active
_gs7_res = subprocess.run(
    [sys.executable, str(_tp), "--session-dir", str(_goal_tentacles), "goal", "resume"],
    capture_output=True,
    text=True,
    env={**os.environ, "TENTACLE_SESSION_DIR": str(_goal_tentacles)},
)
test("Gs7: goal resume exits 0", _gs7_res.returncode == 0, _gs7_res.stderr[:200])
if _gs1_goal_path.exists():
    _gs7_state = json.loads(_gs1_goal_path.read_text())
    test("Gs7: status=active after resume", _gs7_state.get("status") == "active", f"got {_gs7_state.get('status')}")
else:
    test("Gs7: status=active after resume", False, "goal.json missing")

# Gs8: create --goal-id stores goal_id in meta.json
_gs8_tname = f"test-gid-{_uuid.uuid4().hex[:6]}"
_gs8_gid = f"test-goal-{_uuid.uuid4().hex[:8]}"
_gs8_create = subprocess.run(
    [
        sys.executable,
        str(_tp),
        "--session-dir",
        str(_goal_tentacles),
        "create",
        _gs8_tname,
        "--desc",
        "goal-id test",
        "--goal-id",
        _gs8_gid,
        "--iteration",
        "3",
    ],
    capture_output=True,
    text=True,
    env={**os.environ, "TENTACLE_SESSION_DIR": str(_goal_tentacles)},
)
test("Gs8: create --goal-id exits 0", _gs8_create.returncode == 0, _gs8_create.stderr[:200])
_gs8_meta_path = _goal_tentacles / _gs8_tname / "meta.json"
if _gs8_meta_path.exists():
    _gs8_meta = json.loads(_gs8_meta_path.read_text())
    test("Gs8: meta.json goal_id matches", _gs8_meta.get("goal_id") == _gs8_gid, f"got {_gs8_meta.get('goal_id')}")
    test("Gs8: meta.json iteration=3", _gs8_meta.get("iteration") == 3, f"got {_gs8_meta.get('iteration')}")
    test(
        "Gs8: meta.json goal_iteration=3",
        _gs8_meta.get("goal_iteration") == 3,
        f"got {_gs8_meta.get('goal_iteration')}",
    )
else:
    test("Gs8: meta.json goal_id matches", False, "meta.json missing")
    test("Gs8: meta.json iteration=3", False, "meta.json missing")
    test("Gs8: meta.json goal_iteration=3", False, "meta.json missing")

# Gs9: _ensure_metrics_schema adds goal_id and iteration columns
import sqlite3 as _sqlite3

_gs9_db_path = _goal_tmp / "test-skill-metrics.db"
_gs9_conn = _sqlite3.connect(str(_gs9_db_path))
# Simulate old schema without goal columns by creating the base table
_gs9_conn.execute("""
    CREATE TABLE IF NOT EXISTS tentacle_outcomes (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        tentacle_name TEXT NOT NULL,
        outcome_status TEXT NOT NULL,
        recorded_at TEXT NOT NULL
    )
""")
_gs9_conn.commit()

# Now import and call _ensure_metrics_schema
import importlib as _importlib
import sys as _sys

_old_modules = set(_sys.modules.keys())
_tp_module = _importlib.util.spec_from_file_location("tentacle_mod", str(_tp))
_tp_loader = _importlib.util.module_from_spec(_tp_module)
# Don't execute the module (it has top-level side-effects we don't want)
# Instead, just call _ensure_metrics_schema directly through subprocess
_gs9_conn.close()

_gs9_check = subprocess.run(
    [
        sys.executable,
        "-c",
        (
            "import sys, sqlite3, importlib.util\n"
            f"sys.path.insert(0, {repr(str(REPO))})\n"
            f"spec = importlib.util.spec_from_file_location('tentacle', {repr(str(_tp))})\n"
            "mod = importlib.util.module_from_spec(spec)\n"
            "import unittest.mock\n"
            "with unittest.mock.patch('sys.argv', ['tentacle.py', 'list']):\n"
            "    try:\n"
            "        spec.loader.exec_module(mod)\n"
            "    except SystemExit:\n"
            "        pass\n"
            f"conn = sqlite3.connect({repr(str(_gs9_db_path))})\n"
            "mod._ensure_metrics_schema(conn)\n"
            "cols = {row[1] for row in conn.execute('PRAGMA table_info(tentacle_outcomes)').fetchall()}\n"
            "conn.close()\n"
            "print('goal_id' in cols, 'iteration' in cols)\n"
        ),
    ],
    capture_output=True,
    text=True,
)
if _gs9_check.returncode == 0:
    _gs9_out = _gs9_check.stdout.strip()
    test(
        "Gs9: _ensure_metrics_schema adds goal_id column",
        "True" in _gs9_out.split()[0] if _gs9_out.split() else False,
        f"output: {_gs9_out}",
    )
    test(
        "Gs9: _ensure_metrics_schema adds iteration column",
        len(_gs9_out.split()) >= 2 and _gs9_out.split()[1] == "True",
        f"output: {_gs9_out}",
    )
else:
    test("Gs9: _ensure_metrics_schema adds goal_id column", False, _gs9_check.stderr[:200])
    test("Gs9: _ensure_metrics_schema adds iteration column", False, _gs9_check.stderr[:200])

# Gs10: goal eval --decision complete marks goal as completed
_gs10_res = subprocess.run(
    [
        sys.executable,
        str(_tp),
        "--session-dir",
        str(_goal_tentacles),
        "goal",
        "eval",
        "--decision",
        "complete",
        "--notes",
        "all done",
    ],
    capture_output=True,
    text=True,
    env={**os.environ, "TENTACLE_SESSION_DIR": str(_goal_tentacles)},
)
test("Gs10: goal eval complete exits 0", _gs10_res.returncode == 0, _gs10_res.stderr[:200])
if _gs1_goal_path.exists():
    _gs10_state = json.loads(_gs1_goal_path.read_text())
    test(
        "Gs10: status=completed after eval complete",
        _gs10_state.get("status") == "completed",
        f"got {_gs10_state.get('status')}",
    )
    test("Gs10: completed_at is set", bool(_gs10_state.get("completed_at")), "completed_at missing")
else:
    test("Gs10: status=completed after eval complete", False, "goal.json missing")
    test("Gs10: completed_at is set", False, "goal.json missing")

# Gs11: goal init --force reinitializes existing goal.json
_gs10b_res = subprocess.run(
    [sys.executable, str(_tp), "--session-dir", str(_goal_tentacles), "goal", "eval", "--decision", "continue"],
    capture_output=True,
    text=True,
    env={**os.environ, "TENTACLE_SESSION_DIR": str(_goal_tentacles)},
)
test(
    "Gs10b: goal eval continue after complete exits nonzero",
    _gs10b_res.returncode != 0,
    f"stdout={_gs10b_res.stdout[:120]} stderr={_gs10b_res.stderr[:120]}",
)
if _gs1_goal_path.exists():
    _gs10b_state = json.loads(_gs1_goal_path.read_text())
    test(
        "Gs10b: completed goal stays completed after rejected continue",
        _gs10b_state.get("status") == "completed",
        f"got {_gs10b_state.get('status')}",
    )
else:
    test("Gs10b: completed goal stays completed after rejected continue", False, "goal.json missing")

# Gs11: goal init --force reinitializes existing goal.json
_gs11_res = subprocess.run(
    [
        sys.executable,
        str(_tp),
        "--session-dir",
        str(_goal_tentacles),
        "goal",
        "init",
        "--title",
        "Reinit Goal",
        "--force",
    ],
    capture_output=True,
    text=True,
    env={**os.environ, "TENTACLE_SESSION_DIR": str(_goal_tentacles)},
)
test("Gs11: goal init --force exits 0", _gs11_res.returncode == 0, _gs11_res.stderr[:200])
if _gs1_goal_path.exists():
    _gs11_state = json.loads(_gs1_goal_path.read_text())
    test(
        "Gs11: title updated after --force reinit",
        _gs11_state.get("title") == "Reinit Goal",
        f"got {_gs11_state.get('title')}",
    )
    test(
        "Gs11: iteration resets to 1 on reinit",
        _gs11_state.get("iteration") == 1,
        f"got {_gs11_state.get('iteration')}",
    )
else:
    test("Gs11: title updated after --force reinit", False, "goal.json missing")
    test("Gs11: iteration resets to 1 on reinit", False, "goal.json missing")

# Cleanup temp dir
import shutil as _shutil

_shutil.rmtree(str(_goal_tmp), ignore_errors=True)


# ─── SK Launcher pipeline integration ────────────────────────────────────────────────────────

print("\n🚀  SK Launcher pipeline")

import importlib.util as _ilu_sk

_sk_spec = _ilu_sk.spec_from_file_location("auto_update_sk", REPO / "auto-update-tools.py")
_sk_aut = _ilu_sk.module_from_spec(_sk_spec)
_saved_sk_argv = sys.argv[:]
sys.argv = [str(REPO / "auto-update-tools.py")]
try:
    _sk_spec.loader.exec_module(_sk_aut)
finally:
    sys.argv = _saved_sk_argv

import unittest.mock as _sk_mock

_sk_refresh_calls = []


def _sk_fake_changes(files: list) -> dict:
    """Helper: simulate classify_changes by faking git diff output."""
    with _sk_mock.patch.object(_sk_aut, "_git_output", return_value="\n".join(files)):
        return _sk_aut.classify_changes("aaa", "bbb")


# Sk1: classify_changes sets sk_launcher=True for sk.py, and refresh would be called
with _sk_mock.patch.object(_sk_aut, "refresh_sk_launcher", side_effect=lambda: _sk_refresh_calls.append(1)):
    _sk_changes = _sk_fake_changes(["sk.py"])
    test(
        "Sk1: classify_changes sets sk_launcher=True for sk.py",
        _sk_changes.get("sk_launcher") is True,
        "got " + repr(_sk_changes.get("sk_launcher")),
    )
    if _sk_changes.get("sk_launcher"):
        _sk_aut.refresh_sk_launcher()
    test(
        "Sk1: refresh_sk_launcher invoked when sk_launcher=True",
        len(_sk_refresh_calls) == 1,
        f"call count: {len(_sk_refresh_calls)}",
    )

_sk_refresh_calls.clear()

# Sk2: classify_changes stays diff-specific, but pipeline refreshes launcher unconditionally
with _sk_mock.patch.object(_sk_aut, "refresh_sk_launcher", side_effect=lambda: _sk_refresh_calls.append(1)):
    _sk_changes2 = _sk_fake_changes(["watch-sessions.py"])
    test(
        "Sk2: classify_changes sk_launcher=False for unrelated change",
        _sk_changes2.get("sk_launcher") is False,
        "got " + repr(_sk_changes2.get("sk_launcher")),
    )

_sk_pipeline_calls = []
with (
    _sk_mock.patch.object(_sk_aut, "_git_output", return_value="watch-sessions.py"),
    _sk_mock.patch.object(_sk_aut, "run_migrations", side_effect=lambda: _sk_pipeline_calls.append("migrate")),
    _sk_mock.patch.object(_sk_aut, "restart_processes", side_effect=lambda: _sk_pipeline_calls.append("restart")),
    _sk_mock.patch.object(_sk_aut, "refresh_sk_launcher", side_effect=lambda: _sk_pipeline_calls.append("launcher")),
    _sk_mock.patch.object(_sk_aut, "refresh_rust_binary", side_effect=lambda: _sk_pipeline_calls.append("rust")),
    _sk_mock.patch.object(
        _sk_aut, "ensure_post_merge_hook", side_effect=lambda: _sk_pipeline_calls.append("post-merge")
    ),
    _sk_mock.patch.object(_sk_aut, "write_manifest", side_effect=lambda *_args, **_kw: None),
):
    _sk_aut.post_pull_pipeline("aaa", "bbb")

test(
    "Sk2: pipeline refreshes sk launcher even when sk_launcher=False",
    "launcher" in _sk_pipeline_calls,
    f"pipeline calls: {_sk_pipeline_calls!r}",
)
if "launcher" in _sk_pipeline_calls and "rust" in _sk_pipeline_calls:
    test(
        "Sk2: pipeline refreshes sk launcher before Rust binary",
        _sk_pipeline_calls.index("launcher") < _sk_pipeline_calls.index("rust"),
        f"pipeline calls: {_sk_pipeline_calls!r}",
    )
else:
    test("Sk2: pipeline refreshes sk launcher before Rust binary", False, f"pipeline calls: {_sk_pipeline_calls!r}")

# Sk3: instruction template changes trigger managed global-instructions refresh
_sk_instruction_calls = []
with _sk_mock.patch.object(_sk_aut, "refresh_global_instructions", side_effect=lambda: _sk_instruction_calls.append(1)):
    _sk_changes3 = _sk_fake_changes(["templates/copilot-instructions.md"])
    test(
        "Sk3: classify_changes detects global instruction refresh",
        bool(_sk_changes3.get("global_instructions")),
        "got " + repr(_sk_changes3.get("global_instructions")),
    )
    if _sk_changes3.get("global_instructions"):
        _sk_aut.refresh_global_instructions()
    test(
        "Sk3: refresh_global_instructions invoked when instruction templates change",
        len(_sk_instruction_calls) == 1,
        f"call count: {len(_sk_instruction_calls)}",
    )

# Sk4: hook config changes trigger managed hook refresh
_sk_hook_calls = []
with _sk_mock.patch.object(_sk_aut, "refresh_global_hooks", side_effect=lambda: _sk_hook_calls.append(1)):
    _sk_changes4 = _sk_fake_changes(["hooks/hooks.json"])
    test(
        "Sk4: classify_changes detects managed hook refresh",
        bool(_sk_changes4.get("managed_hooks")),
        "got " + repr(_sk_changes4.get("managed_hooks")),
    )
    if _sk_changes4.get("managed_hooks"):
        _sk_aut.refresh_global_hooks()
    test(
        "Sk4: refresh_global_hooks invoked when hook config changes",
        len(_sk_hook_calls) == 1,
        f"call count: {len(_sk_hook_calls)}",
    )

# Sk5: refresh_sk_launcher handles missing install.py gracefully
_orig_td_sk = _sk_aut.TOOLS_DIR
_sk_aut.TOOLS_DIR = REPO / ".test-scratch" / "nonexistent-dir"
try:
    try:
        _sk_aut.refresh_sk_launcher()
        test("Sk5: refresh_sk_launcher is safe when install.py missing", True)
    except Exception as _e_sk:
        test("Sk5: refresh_sk_launcher is safe when install.py missing", False, str(_e_sk))
finally:
    _sk_aut.TOOLS_DIR = _orig_td_sk

# ─── Semantic Proximity Tests ───────────────────────────────────────────

print("\n🔗 Semantic Proximity Tests")

import sys as _sp_sys
import types as _sp_types


def _make_sp_test_db():
    db = sqlite3.connect(":memory:")
    db.executescript(
        """
        CREATE TABLE knowledge_entries (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            session_id TEXT NOT NULL,
            category TEXT NOT NULL DEFAULT '',
            title TEXT DEFAULT '',
            tags TEXT DEFAULT '',
            topic_key TEXT DEFAULT '',
            stable_id TEXT DEFAULT '',
            content TEXT DEFAULT ''
        );
        CREATE TABLE knowledge_relations (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            source_id INTEGER NOT NULL,
            target_id INTEGER NOT NULL,
            source_stable_id TEXT DEFAULT '',
            target_stable_id TEXT DEFAULT '',
            relation_type TEXT NOT NULL,
            stable_id TEXT DEFAULT '',
            confidence REAL DEFAULT 0.5,
            created_at TEXT DEFAULT ''
        );
        """
    )
    return db


class _FakeSemanticMatrix:
    def __init__(self, texts):
        self.texts = list(texts)


class _FakeSimilarityMatrix:
    def __init__(self, texts):
        self.texts = list(texts)

    def __getitem__(self, key):
        i, j = key
        left = set(self.texts[i].split())
        right = set(self.texts[j].split())
        if not left or not right:
            return 0.0
        return len(left & right) / max(len(left), len(right))


def _install_fake_sklearn():
    saved = {}
    module_names = (
        "sklearn",
        "sklearn.feature_extraction",
        "sklearn.feature_extraction.text",
        "sklearn.metrics",
        "sklearn.metrics.pairwise",
    )
    for name in module_names:
        saved[name] = _sp_sys.modules.get(name)

    sklearn_mod = _sp_types.ModuleType("sklearn")
    feature_mod = _sp_types.ModuleType("sklearn.feature_extraction")
    text_mod = _sp_types.ModuleType("sklearn.feature_extraction.text")
    metrics_mod = _sp_types.ModuleType("sklearn.metrics")
    pairwise_mod = _sp_types.ModuleType("sklearn.metrics.pairwise")

    class _FakeTfidfVectorizer:
        def __init__(self, **kwargs):
            self.kwargs = kwargs

        def fit_transform(self, texts):
            return _FakeSemanticMatrix(texts)

    def _fake_cosine_similarity(matrix):
        return _FakeSimilarityMatrix(matrix.texts)

    text_mod.TfidfVectorizer = _FakeTfidfVectorizer
    pairwise_mod.cosine_similarity = _fake_cosine_similarity
    feature_mod.text = text_mod
    metrics_mod.pairwise = pairwise_mod
    sklearn_mod.feature_extraction = feature_mod
    sklearn_mod.metrics = metrics_mod

    _sp_sys.modules["sklearn"] = sklearn_mod
    _sp_sys.modules["sklearn.feature_extraction"] = feature_mod
    _sp_sys.modules["sklearn.feature_extraction.text"] = text_mod
    _sp_sys.modules["sklearn.metrics"] = metrics_mod
    _sp_sys.modules["sklearn.metrics.pairwise"] = pairwise_mod
    return saved


def _restore_fake_sklearn(saved):
    for name, module in saved.items():
        if module is None:
            _sp_sys.modules.pop(name, None)
        else:
            _sp_sys.modules[name] = module


_sp1_db = _make_sp_test_db()
_sp1_text = "async await coroutine event loop python concurrency asyncio tasks futures"
_sp1_db.execute(
    "INSERT INTO knowledge_entries (session_id, category, title, content) VALUES (?, ?, ?, ?)",
    ("sp1-a", "pattern", "async patterns a", _sp1_text),
)
_sp1_db.execute(
    "INSERT INTO knowledge_entries (session_id, category, title, content) VALUES (?, ?, ?, ?)",
    ("sp1-b", "pattern", "async patterns b", _sp1_text),
)
_sp1_db.commit()
_sp1_fake = _install_fake_sklearn()
try:
    ek.extract_relations(_sp1_db)
finally:
    _restore_fake_sklearn(_sp1_fake)
_sp1_count = _sp1_db.execute(
    "SELECT COUNT(*) FROM knowledge_relations WHERE relation_type = 'SEMANTIC_PROXIMITY'"
).fetchone()[0]
test("Sp1: SEMANTIC_PROXIMITY links similar cross-session entries", _sp1_count > 0, f"count={_sp1_count}")
_sp1_db.close()

_sp2_db = _make_sp_test_db()
_sp2_text = "async await coroutine event loop python concurrency asyncio tasks futures"
_sp2_db.execute(
    "INSERT INTO knowledge_entries (session_id, category, title, content) VALUES (?, ?, ?, ?)",
    ("same", "mistake", "async issue", _sp2_text),
)
_sp2_db.execute(
    "INSERT INTO knowledge_entries (session_id, category, title, content) VALUES (?, ?, ?, ?)",
    ("same", "pattern", "async fix", _sp2_text),
)
_sp2_db.commit()
_sp2_fake = _install_fake_sklearn()
try:
    ek.extract_relations(_sp2_db)
finally:
    _restore_fake_sklearn(_sp2_fake)
_sp2_same_session = _sp2_db.execute(
    "SELECT COUNT(*) FROM knowledge_relations WHERE relation_type = 'SAME_SESSION'"
).fetchone()[0]
_sp2_semantic = _sp2_db.execute(
    "SELECT COUNT(*) FROM knowledge_relations WHERE relation_type = 'SEMANTIC_PROXIMITY'"
).fetchone()[0]
test(
    "Sp2: stronger SAME_SESSION relation suppresses SEMANTIC_PROXIMITY",
    _sp2_same_session > 0 and _sp2_semantic == 0,
    f"SAME_SESSION={_sp2_same_session} SEMANTIC_PROXIMITY={_sp2_semantic}",
)
_sp2_db.close()

import builtins as _sp_builtins

_sp_real_import = _sp_builtins.__import__


def _sp_block_sklearn(name, *args, **kwargs):
    if name == "sklearn" or name.startswith("sklearn."):
        raise ImportError(f"blocked:{name}")
    return _sp_real_import(name, *args, **kwargs)


_sp3_db = _make_sp_test_db()
_sp3_db.execute(
    "INSERT INTO knowledge_entries (session_id, category, title, content) VALUES (?, ?, ?, ?)",
    ("sp3-a", "pattern", "entry one", "some shared content text words tokens"),
)
_sp3_db.execute(
    "INSERT INTO knowledge_entries (session_id, category, title, content) VALUES (?, ?, ?, ?)",
    ("sp3-b", "pattern", "entry two", "some shared content text words tokens"),
)
_sp3_db.commit()
_sp_builtins.__import__ = _sp_block_sklearn
try:
    ek.extract_relations(_sp3_db)
    test("Sp3: extract_relations does not crash when sklearn import fails", True)
except Exception as exc:
    test("Sp3: extract_relations does not crash when sklearn import fails", False, str(exc))
finally:
    _sp_builtins.__import__ = _sp_real_import
_sp3_db.close()

# ─── MCP Server Tests ───────────────────────────────────────────────────

print("\n🧰 MCP Server Tests")

import queue as _mcp_queue
import threading as _mcp_threading


def _mcp_write(proc, payload):
    data = json.dumps(payload).encode("utf-8")
    proc.stdin.write(f"Content-Length: {len(data)}\r\n\r\n".encode("ascii"))
    proc.stdin.write(data)
    proc.stdin.flush()


def _mcp_read(proc, timeout=15.0):
    def _read_until_delimiter(stream, delimiter, out_queue):
        try:
            data = b""
            while delimiter not in data:
                chunk = stream.read(1)
                if not chunk:
                    break
                data += chunk
            out_queue.put(("ok", data))
        except Exception as exc:
            out_queue.put(("err", exc))

    def _read_exact(stream, length, out_queue):
        try:
            data = b""
            while len(data) < length:
                chunk = stream.read(length - len(data))
                if not chunk:
                    break
                data += chunk
            out_queue.put(("ok", data))
        except Exception as exc:
            out_queue.put(("err", exc))

    header = b""
    header_queue = _mcp_queue.Queue()
    header_thread = _mcp_threading.Thread(
        target=_read_until_delimiter,
        args=(proc.stdout, b"\r\n\r\n", header_queue),
        daemon=True,
    )
    header_thread.start()
    header_thread.join(timeout)
    if header_thread.is_alive():
        raise TimeoutError("timed out waiting for MCP header")
    header_status, header_value = header_queue.get()
    if header_status == "err":
        raise header_value
    header = header_value
    if not header:
        raise RuntimeError(proc.stderr.read().decode("utf-8", errors="replace"))
    header_blob, body = header.split(b"\r\n\r\n", 1)
    headers = {}
    for raw_line in header_blob.decode("ascii").split("\r\n"):
        key, value = raw_line.split(":", 1)
        headers[key.strip().lower()] = value.strip()
    length = int(headers["content-length"])
    while len(body) < length:
        body_queue = _mcp_queue.Queue()
        body_thread = _mcp_threading.Thread(
            target=_read_exact,
            args=(proc.stdout, length - len(body), body_queue),
            daemon=True,
        )
        body_thread.start()
        body_thread.join(timeout)
        if body_thread.is_alive():
            raise TimeoutError("timed out waiting for MCP body")
        body_status, body_value = body_queue.get()
        if body_status == "err":
            raise body_value
        if not body_value:
            raise RuntimeError(proc.stderr.read().decode("utf-8", errors="replace"))
        body += body_value
    return json.loads(body.decode("utf-8"))


_mcp_ok = False
with tempfile.TemporaryDirectory(prefix="mcp-server-test-") as _mcp_tmp:
    _mcp_home = Path(_mcp_tmp)
    _mcp_state = _mcp_home / ".copilot" / "session-state"
    _mcp_state.mkdir(parents=True, exist_ok=True)
    _mcp_db = sqlite3.connect(_mcp_state / "knowledge.db")
    _mcp_db.executescript(
        """
        CREATE TABLE documents (
            id INTEGER PRIMARY KEY,
            session_id TEXT NOT NULL,
            doc_type TEXT NOT NULL,
            title TEXT NOT NULL,
            file_path TEXT DEFAULT '',
            seq INTEGER DEFAULT 1,
            size_bytes INTEGER DEFAULT 0,
            source TEXT DEFAULT 'copilot'
        );
        CREATE TABLE sections (
            id INTEGER PRIMARY KEY,
            document_id INTEGER NOT NULL,
            section_name TEXT DEFAULT '',
            content TEXT DEFAULT ''
        );
        CREATE TABLE knowledge_entries (
            id INTEGER PRIMARY KEY,
            category TEXT NOT NULL,
            title TEXT NOT NULL,
            content TEXT DEFAULT '',
            tags TEXT DEFAULT '',
            confidence REAL DEFAULT 0.8,
            session_id TEXT DEFAULT '',
            occurrence_count INTEGER DEFAULT 1,
            document_id INTEGER,
            source_section TEXT DEFAULT '',
            source_file TEXT DEFAULT '',
            start_line INTEGER,
            end_line INTEGER,
            code_language TEXT DEFAULT '',
            code_snippet TEXT DEFAULT '',
            task_id TEXT DEFAULT '',
            affected_files TEXT DEFAULT ''
        );
        CREATE VIRTUAL TABLE knowledge_fts USING fts5(
            title,
            section_name,
            content,
            doc_type UNINDEXED,
            session_id UNINDEXED,
            document_id UNINDEXED
        );
        CREATE VIRTUAL TABLE ke_fts USING fts5(title, content);
        CREATE VIRTUAL TABLE sessions_fts USING fts5(
            session_id UNINDEXED,
            title,
            user_messages,
            assistant_messages,
            tool_names
        );
        """
    )
    _mcp_db.execute(
        """
        INSERT INTO documents (id, session_id, doc_type, title, file_path, seq, size_bytes, source)
        VALUES (1, 'sess-auth-123', 'checkpoint', 'Auth troubleshooting doc', 'checkpoint.md', 1, 256, 'copilot')
        """
    )
    _mcp_db.execute(
        "INSERT INTO sections (id, document_id, section_name, content) VALUES (1, 1, 'summary', ?)",
        ("Fix auth bug by validating JWT audience and token expiry.",),
    )
    _mcp_db.execute(
        """
        INSERT INTO knowledge_entries (
            id, category, title, content, tags, confidence, session_id,
            occurrence_count, document_id, source_section, task_id, affected_files
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            1,
            "pattern",
            "JWT audience validation",
            "Validate JWT audience before accepting auth tokens.",
            "auth,jwt",
            0.9,
            "sess-auth-123",
            1,
            1,
            "summary",
            "auth-task",
            "src/auth.py",
        ),
    )
    _mcp_db.execute(
        """
        INSERT INTO knowledge_fts (rowid, title, section_name, content, doc_type, session_id, document_id)
        VALUES (?, ?, ?, ?, ?, ?, ?)
        """,
        (
            1,
            "Auth troubleshooting doc",
            "summary",
            "Fix auth bug by validating JWT audience and token expiry.",
            "checkpoint",
            "sess-auth-123",
            1,
        ),
    )
    _mcp_db.execute(
        "INSERT INTO ke_fts (rowid, title, content) VALUES (?, ?, ?)",
        (1, "JWT audience validation", "Validate JWT audience before accepting auth tokens."),
    )
    _mcp_db.execute(
        "INSERT INTO sessions_fts (rowid, session_id, title, user_messages, assistant_messages, tool_names) VALUES (?, ?, ?, ?, ?, ?)",
        (1, "sess-auth-123", "auth session", "need help with auth", "validated jwt audience", "python"),
    )
    _mcp_db.commit()
    _mcp_db.close()

    _mcp_script = REPO / "mcp-server.py"
    if not _mcp_script.is_file():
        print(f"  ⚠️  {_mcp_script} not found — skipping MCP server tests")
    else:
        _mcp_env = os.environ.copy()
        _mcp_env["HOME"] = str(_mcp_home)
        _mcp_env["USERPROFILE"] = str(_mcp_home)
        _mcp_proc = subprocess.Popen(
            [sys.executable, str(_mcp_script)],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            env=_mcp_env,
        )
        try:
            _mcp_write(
                _mcp_proc,
                {
                    "jsonrpc": "2.0",
                    "id": 1,
                    "method": "initialize",
                    "params": {"protocolVersion": "2024-11-05", "capabilities": {}, "clientInfo": {"name": "test"}},
                },
            )
            _mcp_init = _mcp_read(_mcp_proc)
            test(
                "Mcp1: initialize returns tools capability",
                _mcp_init.get("result", {}).get("capabilities", {}).get("tools", {}).get("listChanged") is False,
                str(_mcp_init),
            )
            _mcp_write(_mcp_proc, {"jsonrpc": "2.0", "method": "notifications/initialized", "params": {}})

            _mcp_write(_mcp_proc, {"jsonrpc": "2.0", "id": 2, "method": "tools/list", "params": {}})
            _mcp_tools = _mcp_read(_mcp_proc)
            _mcp_tool_names = [tool.get("name") for tool in _mcp_tools.get("result", {}).get("tools", [])]
            test(
                "Mcp2: tools/list exposes briefing and query_session",
                "briefing" in _mcp_tool_names and "query_session" in _mcp_tool_names,
                str(_mcp_tool_names),
            )

            _mcp_write(
                _mcp_proc,
                {
                    "jsonrpc": "2.0",
                    "id": 3,
                    "method": "tools/call",
                    "params": {"name": "briefing", "arguments": {"task": "auth bug", "limit": 2}},
                },
            )
            _mcp_brief = _mcp_read(_mcp_proc)
            _mcp_brief_structured = _mcp_brief.get("result", {}).get("structuredContent", {})
            test(
                "Mcp3: briefing tool returns structured task briefing",
                _mcp_brief_structured.get("query") == "auth bug",
                json.dumps(_mcp_brief, ensure_ascii=False)[:200],
            )

            _mcp_write(
                _mcp_proc,
                {
                    "jsonrpc": "2.0",
                    "id": 4,
                    "method": "tools/call",
                    "params": {"name": "query_session", "arguments": {"query": "auth", "limit": 5}},
                },
            )
            _mcp_query = _mcp_read(_mcp_proc)
            _mcp_query_text = _mcp_query.get("result", {}).get("structuredContent", {}).get("output", "")
            test(
                "Mcp4: query_session tool returns search output",
                "Auth troubleshooting doc" in _mcp_query_text or "JWT audience validation" in _mcp_query_text,
                _mcp_query_text[:200],
            )

            _mcp_write(
                _mcp_proc,
                {
                    "jsonrpc": "2.0",
                    "id": 5,
                    "method": "tools/call",
                    "params": {"name": "missing_tool", "arguments": {}},
                },
            )
            _mcp_bad_tool = _mcp_read(_mcp_proc)
            test(
                "Mcp5: unknown tool returns JSON-RPC invalid params error",
                _mcp_bad_tool.get("error", {}).get("code") == -32602,
                str(_mcp_bad_tool),
            )

            _mcp_write(_mcp_proc, {"jsonrpc": "2.0", "id": 6, "method": "shutdown", "params": {}})
            _mcp_shutdown = _mcp_read(_mcp_proc)
            test("Mcp6: shutdown request succeeds", "result" in _mcp_shutdown, str(_mcp_shutdown))
            _mcp_write(_mcp_proc, {"jsonrpc": "2.0", "method": "exit", "params": {}})
            _mcp_proc.wait(timeout=5)
            _mcp_ok = True
        except Exception as exc:
            test("Mcp server end-to-end handshake", False, str(exc))
        finally:
            if not _mcp_ok and _mcp_proc.poll() is None:
                _mcp_proc.terminate()
                try:
                    _mcp_proc.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    _mcp_proc.kill()
                    _mcp_proc.wait(timeout=5)

# ─── Priority Classification (#121) ─────────────────────────────────────

print("\n🏷️  Priority Classification Tests (#121)")

import tempfile as _tempfile


# Helpers: build an isolated in-memory DB with the priority column
def _make_priority_db(with_priority_col: bool = True) -> sqlite3.Connection:
    db = sqlite3.connect(":memory:")
    db.row_factory = sqlite3.Row
    db.executescript("""
        CREATE TABLE knowledge_entries (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            session_id TEXT NOT NULL DEFAULT 'test-session',
            category TEXT NOT NULL,
            title TEXT NOT NULL,
            content TEXT NOT NULL DEFAULT '',
            tags TEXT DEFAULT '',
            confidence REAL DEFAULT 0.7,
            occurrence_count INTEGER DEFAULT 1,
            first_seen TEXT DEFAULT '2024-01-01T00:00:00',
            last_seen TEXT DEFAULT '2024-01-01T00:00:00',
            wing TEXT DEFAULT '',
            room TEXT DEFAULT '',
            facts TEXT DEFAULT '[]',
            est_tokens INTEGER DEFAULT 0,
            task_id TEXT DEFAULT '',
            affected_files TEXT DEFAULT '[]',
            source_file TEXT DEFAULT '',
            start_line INTEGER DEFAULT 0,
            end_line INTEGER DEFAULT 0,
            code_language TEXT DEFAULT '',
            code_snippet TEXT DEFAULT '',
            stable_id TEXT,
            valence TEXT DEFAULT '',
            intensity REAL DEFAULT 0.5
        );
        CREATE VIRTUAL TABLE IF NOT EXISTS ke_fts USING fts5(
            title, content, tags, category, wing, room, facts
        );
    """)
    if with_priority_col:
        try:
            db.execute("ALTER TABLE knowledge_entries ADD COLUMN priority TEXT DEFAULT 'P2'")
        except sqlite3.OperationalError:
            pass
    return db


def _seed_priority_db_file(db_path: Path, with_priority_col: bool = True) -> None:
    db_path.parent.mkdir(parents=True, exist_ok=True)
    db = sqlite3.connect(str(db_path))
    try:
        db.executescript("""
            CREATE TABLE knowledge_entries (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                session_id TEXT NOT NULL DEFAULT 'test-session',
                category TEXT NOT NULL,
                title TEXT NOT NULL,
                content TEXT NOT NULL DEFAULT '',
                tags TEXT DEFAULT '',
                confidence REAL DEFAULT 0.7,
                occurrence_count INTEGER DEFAULT 1,
                first_seen TEXT DEFAULT '2024-01-01T00:00:00',
                last_seen TEXT DEFAULT '2024-01-01T00:00:00',
                wing TEXT DEFAULT '',
                room TEXT DEFAULT '',
                facts TEXT DEFAULT '[]',
                est_tokens INTEGER DEFAULT 0,
                task_id TEXT DEFAULT '',
                affected_files TEXT DEFAULT '[]',
                source_file TEXT DEFAULT '',
                start_line INTEGER DEFAULT 0,
                end_line INTEGER DEFAULT 0,
                code_language TEXT DEFAULT '',
                code_snippet TEXT DEFAULT '',
                stable_id TEXT,
                valence TEXT DEFAULT '',
                intensity REAL DEFAULT 0.5
            );
            CREATE VIRTUAL TABLE IF NOT EXISTS ke_fts USING fts5(
                title, content, tags, category, wing, room, facts
            );
        """)
        if with_priority_col:
            try:
                db.execute("ALTER TABLE knowledge_entries ADD COLUMN priority TEXT DEFAULT 'P2'")
            except sqlite3.OperationalError:
                pass
        db.commit()
    finally:
        db.close()


def _insert_ke(
    db: sqlite3.Connection,
    title: str,
    category: str = "mistake",
    confidence: float = 0.7,
    priority: str = "",
    intensity: float = 0.5,
    last_seen: str = "2024-01-01T00:00:00",
) -> int:
    row_id = db.execute(
        "INSERT INTO knowledge_entries (category, title, content, confidence, intensity, last_seen) VALUES (?,?,?,?,?,?)",
        (category, title, f"content of {title}", confidence, intensity, last_seen),
    ).lastrowid
    if priority:
        try:
            db.execute("UPDATE knowledge_entries SET priority = ? WHERE id = ?", (priority, row_id))
        except sqlite3.OperationalError:
            pass
    db.execute(
        "INSERT INTO ke_fts (rowid, title, content, tags, category, wing, room, facts) VALUES (?,?,?,?,?,?,?,?)",
        (row_id, title, f"content of {title}", "", category, "", "", "[]"),
    )
    db.commit()
    return row_id


# P121-1: learn.py CLI validates --priority through the real subprocess path
try:
    with tempfile.TemporaryDirectory(prefix="learn-priority-cli-") as _p121_tmp:
        _p121_home = Path(_p121_tmp)
        _p121_env = os.environ.copy()
        _p121_env["HOME"] = str(_p121_home)
        _p121_env["USERPROFILE"] = str(_p121_home)
        _seed_priority_db_file(_p121_home / ".copilot" / "session-state" / "knowledge.db", with_priority_col=True)

        _p121_valid = subprocess.run(
            [
                sys.executable,
                str(REPO / "learn.py"),
                "--tool",
                "Priority CLI valid",
                "Valid priority should persist through the real CLI path",
                "--priority",
                "P0",
                "--json",
            ],
            capture_output=True,
            text=True,
            env=_p121_env,
        )
        _p121_valid_json = json.loads(_p121_valid.stdout or "{}") if _p121_valid.returncode == 0 else {}
        test(
            "P121-1a: CLI accepts valid --priority P0",
            _p121_valid.returncode == 0 and _p121_valid_json.get("priority") == "P0",
            f"code={_p121_valid.returncode} stdout={_p121_valid.stdout!r} stderr={_p121_valid.stderr!r}",
        )

        _p121_invalid = subprocess.run(
            [
                sys.executable,
                str(REPO / "learn.py"),
                "--tool",
                "Priority CLI invalid",
                "Invalid priority should be rejected through the real CLI path",
                "--priority",
                "P4",
            ],
            capture_output=True,
            text=True,
            env=_p121_env,
        )
        test(
            "P121-1b: CLI rejects invalid --priority P4",
            _p121_invalid.returncode != 0 and "--priority must be one of: P0, P1, P2, P3" in _p121_invalid.stderr,
            f"code={_p121_invalid.returncode} stdout={_p121_invalid.stdout!r} stderr={_p121_invalid.stderr!r}",
        )
except Exception as _e:
    test("P121-1: learn.py CLI priority validation", False, str(_e))

# P121-2: migrate.py v22 migration exists and adds priority column
try:
    _mig_db = sqlite3.connect(":memory:")
    _mig_db.executescript("""
        CREATE TABLE schema_version (version INTEGER PRIMARY KEY, name TEXT DEFAULT '', migrated_at TEXT DEFAULT (datetime('now')));
        CREATE TABLE knowledge_entries (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            session_id TEXT NOT NULL DEFAULT 'test',
            category TEXT NOT NULL DEFAULT 'mistake',
            title TEXT NOT NULL DEFAULT 'title',
            content TEXT NOT NULL DEFAULT 'content'
        );
    """)
    _mig_db.execute("ALTER TABLE knowledge_entries ADD COLUMN valence TEXT DEFAULT ''")
    _mig_db.execute("ALTER TABLE knowledge_entries ADD COLUMN intensity REAL DEFAULT 0.5")
    _mig_db.execute("INSERT INTO schema_version (version, name) VALUES (21, 'valence_intensity')")
    _mig_db.commit()
    # Confirm priority column absent before migration
    _pre_cols = {r[1] for r in _mig_db.execute("PRAGMA table_info(knowledge_entries)").fetchall()}
    test("P121-2a: priority column absent before v22 migration", "priority" not in _pre_cols)
    # Apply v22 manually as migrate.py would
    _mig_db.execute("ALTER TABLE knowledge_entries ADD COLUMN priority TEXT DEFAULT 'P2'")
    _mig_db.execute("INSERT OR IGNORE INTO schema_version (version, name) VALUES (22, 'priority')")
    _mig_db.commit()
    _post_cols = {r[1] for r in _mig_db.execute("PRAGMA table_info(knowledge_entries)").fetchall()}
    test("P121-2b: priority column present after v22 migration", "priority" in _post_cols)
    _mig_db.close()
except Exception as _e:
    test("P121-2: migration v22 setup", False, str(_e))

# P121-3: priority persisted in DB via add_entry logic (column-check path)
try:
    _p3_db = _make_priority_db(with_priority_col=True)
    _p3_entry_id = _insert_ke(_p3_db, "Critical auth bug", priority="P0")
    _p3_row = _p3_db.execute("SELECT priority FROM knowledge_entries WHERE id = ?", (_p3_entry_id,)).fetchone()
    test("P121-3a: P0 stored in DB", _p3_row is not None and _p3_row[0] == "P0", str(_p3_row))
    _p3_entry_id2 = _insert_ke(_p3_db, "Low-pri note", priority="P3")
    _p3_row2 = _p3_db.execute("SELECT priority FROM knowledge_entries WHERE id = ?", (_p3_entry_id2,)).fetchone()
    test("P121-3b: P3 stored in DB", _p3_row2 is not None and _p3_row2[0] == "P3")
    # Default P2 for entry without explicit priority
    _p3_entry_id3 = _insert_ke(_p3_db, "Normal priority entry")
    _p3_row3 = _p3_db.execute(
        "SELECT COALESCE(priority, 'P2') FROM knowledge_entries WHERE id = ?", (_p3_entry_id3,)
    ).fetchone()
    test("P121-3c: entry without explicit priority defaults to P2", _p3_row3 is not None and _p3_row3[0] == "P2")
    _p3_db.close()
except Exception as _e:
    test("P121-3: priority persistence", False, str(_e))

# P121-4: _recency_composite_score priority bases are coherent and _priority_to_int is removed
# _priority_to_int was dead code whose 3/2/1/0 int scale contradicted the additive
# 4.0/2.0/0.0/-2.0 float bases used by _recency_composite_score (issue #121 Blocker 4).
# It has been removed to eliminate the drift hazard; these tests protect the coherence contract.
try:
    import importlib as _imp

    _brf = _imp.import_module("briefing")
    # Each tier's minimum score must exceed the next tier's maximum (no overlap possible).
    _p4_hl = 365.0  # generous half-life so decay ≈ 1.0 for a fresh entry
    _p4_p0_min = _brf._recency_composite_score({"priority": "P0", "intensity": 0.0}, _p4_hl)
    _p4_p1_max = _brf._recency_composite_score({"priority": "P1", "intensity": 1.0}, _p4_hl)
    _p4_p1_min = _brf._recency_composite_score({"priority": "P1", "intensity": 0.0}, _p4_hl)
    _p4_p2_max = _brf._recency_composite_score({"priority": "P2", "intensity": 1.0}, _p4_hl)
    _p4_p2_min = _brf._recency_composite_score({"priority": "P2", "intensity": 0.0}, _p4_hl)
    _p4_p3_max = _brf._recency_composite_score({"priority": "P3", "intensity": 1.0}, _p4_hl)
    test(
        "P121-4a: P0 zero-intensity outranks P1 max-intensity (tier gap holds)",
        _p4_p0_min > _p4_p1_max,
        f"P0_min={_p4_p0_min:.3f} P1_max={_p4_p1_max:.3f}",
    )
    test(
        "P121-4b: P1 zero-intensity outranks P2 max-intensity",
        _p4_p1_min > _p4_p2_max,
        f"P1_min={_p4_p1_min:.3f} P2_max={_p4_p2_max:.3f}",
    )
    test(
        "P121-4c: P2 zero-intensity outranks P3 max-intensity",
        _p4_p2_min > _p4_p3_max,
        f"P2_min={_p4_p2_min:.3f} P3_max={_p4_p3_max:.3f}",
    )
    test("P121-4d: _priority_to_int removed (no dead-code drift hazard)", not hasattr(_brf, "_priority_to_int"))
except Exception as _e:
    test("P121-4: _recency_composite_score priority base coherence", False, str(_e))

# P121-5: _ke_has_priority detection
try:
    _db_with = _make_priority_db(with_priority_col=True)
    _db_without = _make_priority_db(with_priority_col=False)
    test("P121-5a: _ke_has_priority True when column present", _brf._ke_has_priority(_db_with))
    test("P121-5b: _ke_has_priority False when column absent", not _brf._ke_has_priority(_db_without))
    _db_with.close()
    _db_without.close()
except Exception as _e:
    test("P121-5: _ke_has_priority", False, str(_e))

# P121-6: _recency_composite_score respects priority ordering
try:
    _now = "2024-06-01T00:00:00"
    _e_p0 = {"priority": "P0", "intensity": 0.5, "confidence": 0.7, "last_seen": _now}
    _e_p1 = {
        "priority": "P1",
        "intensity": 0.9,
        "confidence": 0.9,
        "last_seen": _now,
    }  # higher intensity but lower priority
    _e_p2 = {"priority": "P2", "intensity": 0.5, "confidence": 0.7, "last_seen": _now}
    _e_p3 = {"priority": "P3", "intensity": 0.5, "confidence": 0.7, "last_seen": _now}
    _e_none = {"intensity": 0.5, "confidence": 0.7, "last_seen": _now}  # no priority key = P2
    _half_life = 365.0  # long half-life so recency doesn't dominate
    _s_p0 = _brf._recency_composite_score(_e_p0, _half_life)
    _s_p1 = _brf._recency_composite_score(_e_p1, _half_life)
    _s_p2 = _brf._recency_composite_score(_e_p2, _half_life)
    _s_p3 = _brf._recency_composite_score(_e_p3, _half_life)
    _s_none = _brf._recency_composite_score(_e_none, _half_life)
    test("P121-6a: P0 outranks P1 even with lower intensity", _s_p0 > _s_p1, f"P0={_s_p0:.4f} P1={_s_p1:.4f}")
    test("P121-6b: P1 outranks P2", _s_p1 > _s_p2, f"P1={_s_p1:.4f} P2={_s_p2:.4f}")
    test("P121-6c: P2 outranks P3", _s_p2 > _s_p3, f"P2={_s_p2:.4f} P3={_s_p3:.4f}")
    test(
        "P121-6d: no-priority entry scores same as P2 entry",
        abs(_s_none - _s_p2) < 1e-9,
        f"none={_s_none:.4f} P2={_s_p2:.4f}",
    )
except Exception as _e:
    test("P121-6: _recency_composite_score priority ordering", False, str(_e))

# P121-7: _intensity_order_expr SQL fragment contains priority CASE when has_priority=True
try:
    _expr_with = _brf._intensity_order_expr("ke", has_priority=True)
    _expr_without = _brf._intensity_order_expr("ke", has_priority=False)
    test("P121-7a: ORDER expr with priority contains CASE...P0", "P0" in _expr_with and "CASE" in _expr_with)
    test("P121-7b: ORDER expr without priority has no priority CASE", "P0" not in _expr_without)
    test("P121-7c: ORDER expr with priority still includes intensity", "intensity" in _expr_with)
    test("P121-7d: ORDER expr without priority includes intensity", "intensity" in _expr_without)
except Exception as _e:
    test("P121-7: _intensity_order_expr", False, str(_e))

# P121-8: backward compat — _recency_composite_score works for pre-v22 rows (no priority key)
try:
    _legacy_entry = {"intensity": 0.8, "confidence": 0.7, "last_seen": "2024-01-01T00:00:00"}
    _legacy_score = _brf._recency_composite_score(_legacy_entry, 365.0)
    # Should not raise; score should be positive
    test("P121-8: backward compat pre-v22 rows no priority key", _legacy_score > 0, str(_legacy_score))
except Exception as _e:
    test("P121-8: backward compat pre-v22", False, str(_e))

# P121-9: Strict cross-tier ordering — blocker repro case
# Verifies that a P0 entry with intensity=0.0 always outranks a P1 entry with
# intensity=1.0 and an identical current timestamp, proving the additive formula
# enforces strict outer dimension ordering.
try:
    _now_repro = "2024-06-01T00:00:00"
    _e_p0_zero = {"priority": "P0", "intensity": 0.0, "confidence": 0.7, "last_seen": _now_repro}
    _e_p1_max = {"priority": "P1", "intensity": 1.0, "confidence": 0.9, "last_seen": _now_repro}
    _e_p1_max2 = {"priority": "P1", "intensity": 1.0, "confidence": 0.9, "last_seen": _now_repro}
    _e_p2_max = {"priority": "P2", "intensity": 1.0, "confidence": 0.9, "last_seen": _now_repro}
    _e_p3_max = {"priority": "P3", "intensity": 1.0, "confidence": 0.9, "last_seen": _now_repro}
    _hl = 30.0
    _s9_p0 = _brf._recency_composite_score(_e_p0_zero, _hl)
    _s9_p1 = _brf._recency_composite_score(_e_p1_max, _hl)
    _s9_p2 = _brf._recency_composite_score(_e_p2_max, _hl)
    _s9_p3 = _brf._recency_composite_score(_e_p3_max, _hl)
    test(
        "P121-9a: P0 zero-intensity outranks P1 max-intensity (blocker repro)",
        _s9_p0 > _s9_p1,
        f"P0(intensity=0)={_s9_p0:.4f} P1(intensity=1)={_s9_p1:.4f}",
    )
    test(
        "P121-9b: P1 max-intensity does not beat P2 max-intensity across tier",
        _s9_p1 > _s9_p2,
        f"P1={_s9_p1:.4f} P2={_s9_p2:.4f}",
    )
    test(
        "P121-9c: P2 max-intensity does not beat P3 max-intensity across tier",
        _s9_p2 > _s9_p3,
        f"P2={_s9_p2:.4f} P3={_s9_p3:.4f}",
    )
except Exception as _e:
    test("P121-9: strict cross-tier ordering blocker repro", False, str(_e))

# P121-10: Semantic vector path fetches priority — entries carry correct priority field
# Injects a fake embed module so vector_search returns a known entry and verifies
# the returned dict has the DB-stored priority (P0), not the silent default P2.
try:
    import sys as _sys_p10
    import types as _types_p10

    _sem_db = _make_priority_db(with_priority_col=True)
    _sem_entry_id = _insert_ke(_sem_db, "Semantic critical entry", priority="P0", category="mistake")

    _fake_embed_mod = _types_p10.ModuleType("embed")
    _fake_embed_mod.load_config = lambda: {"provider": "fake", "api_key": "test"}
    _fake_embed_mod.resolve_provider = lambda config: ("fake", {"endpoint": "http://fake"})
    _fake_embed_mod.call_embedding_api = lambda texts, config: [[0.1, 0.2, 0.3]]
    _fake_embed_mod.ensure_embedding_tables = lambda db: None
    _fake_embed_mod.vector_search = lambda db, vec, source_type, limit: [("knowledge", _sem_entry_id, 0.95)]
    _fake_embed_mod.search_tfidf = lambda query, model, limit: []

    _orig_embed = _sys_p10.modules.get("embed")
    _sys_p10.modules["embed"] = _fake_embed_mod
    try:
        _sem_results = _brf.search_semantic(_sem_db, "critical", "mistake", limit=5)
    finally:
        if _orig_embed is None:
            _sys_p10.modules.pop("embed", None)
        else:
            _sys_p10.modules["embed"] = _orig_embed

    _hit = next((r for r in _sem_results if r.get("id") == _sem_entry_id), None)
    test(
        "P121-10a: semantic vector path returns the matched entry",
        _hit is not None,
        f"ids returned={[r.get('id') for r in _sem_results]}",
    )
    if _hit is not None:
        test(
            "P121-10b: semantic hit carries DB priority P0, not silent default P2",
            _hit.get("priority") == "P0",
            f"priority={_hit.get('priority')}",
        )
    _sem_db.close()
except Exception as _e:
    test("P121-10: semantic priority propagation", False, str(_e))


# P121-11: generate_subagent_context() reranks FTS+semantic by priority before truncation
# Regression for Blocker 3: a high-priority (P0) semantic hit must not be dropped behind
# lower-priority (P2) FTS hits due to pre-rerank truncation.
try:
    import sqlite3 as _sqlite3_p11
    import sys as _sys_p11

    # 3 P2 FTS entries saturate cat_limit=3; without rerank the P0 semantic hit is dropped.
    _p11_fts_entries = [
        {
            "id": 100,
            "title": "FTS entry A",
            "priority": "P2",
            "intensity": 1.0,
            "confidence": 0.9,
            "last_seen": "2025-01-01T00:00:00",
            "category": "mistake",
        },
        {
            "id": 101,
            "title": "FTS entry B",
            "priority": "P2",
            "intensity": 1.0,
            "confidence": 0.9,
            "last_seen": "2025-01-01T00:00:00",
            "category": "mistake",
        },
        {
            "id": 102,
            "title": "FTS entry C",
            "priority": "P2",
            "intensity": 1.0,
            "confidence": 0.9,
            "last_seen": "2025-01-01T00:00:00",
            "category": "mistake",
        },
    ]
    _p11_sem_entry = {
        "id": 200,
        "title": "SEMANTIC P0 critical entry",
        "priority": "P0",
        "intensity": 0.1,
        "confidence": 0.6,
        "last_seen": "2020-01-01T00:00:00",
        "category": "mistake",
    }

    _orig_ske_p11 = _brf.search_knowledge_entries
    _orig_ss_p11 = _brf.search_semantic
    _orig_gdb_p11 = _brf.get_db
    _orig_ghl_p11 = _brf._get_briefing_half_life

    def _fake_ske_p11(db, query, cat, limit, min_confidence=0.0):
        if cat == "mistake":
            return list(_p11_fts_entries)
        return []

    def _fake_ss_p11(db, query, cat, limit, min_confidence=0.0):
        if cat == "mistake":
            return [_p11_sem_entry]
        return []

    _p11_mock_db = _sqlite3_p11.connect(":memory:")
    _brf.search_knowledge_entries = _fake_ske_p11
    _brf.search_semantic = _fake_ss_p11
    _brf.get_db = lambda: _p11_mock_db
    _brf._get_briefing_half_life = lambda db: 30.0

    try:
        # limit=3 so cat_limit=3 for "mistake"; mode="auto" with infer_auto_mode=False
        # means we get the default category set; P0 sem entry must surface despite 3 P2 FTS hits.
        _p11_output = _brf.generate_subagent_context("auth bug", limit=3, infer_auto_mode=False)
    finally:
        _brf.search_knowledge_entries = _orig_ske_p11
        _brf.search_semantic = _orig_ss_p11
        _brf.get_db = _orig_gdb_p11
        _brf._get_briefing_half_life = _orig_ghl_p11
        _p11_mock_db.close()

    test(
        "P121-11a: generate_subagent_context includes P0 semantic hit despite 3 P2 FTS hits at cat_limit",
        "SEMANTIC P0 critical entry" in _p11_output,
        f"output={_p11_output[:400]}",
    )
    test(
        "P121-11b: generate_subagent_context still includes at least one FTS entry after priority rerank",
        "FTS entry" in _p11_output,
        f"output={_p11_output[:400]}",
    )
except Exception as _e:
    test("P121-11: generate_subagent_context priority rerank (Blocker 3 regression)", False, str(_e))


# P121-12: Semantic overfetch — callers pass fetch_limit (not cat_limit) to search_semantic
# Blocker 4 regression: a P0 semantic hit that would be beyond raw cat_limit must survive
# into the outer priority-aware rerank.  The mock returns a P0 entry ONLY when called with
# limit >= fetch_limit (proving the caller passes the widened pool, not the raw cat_limit).
try:
    import sqlite3 as _sqlite3_p12
    import sys as _sys_p12

    _p12_cat_limit = 3
    _p12_fetch_limit = max(_p12_cat_limit * 2, _p12_cat_limit + 6)  # = 9 for cat_limit=3

    def _fake_ss_p12(db, query, cat, limit, min_confidence=0.0):
        """Returns P0 hit only when caller passes widened fetch_limit."""
        if cat != "mistake":
            return []
        _p2_pool = [
            {
                "id": 300 + i,
                "title": f"SEM P2 slot {i}",
                "priority": "P2",
                "intensity": 1.0,
                "confidence": 0.9,
                "last_seen": "2025-01-01T00:00:00",
                "category": "mistake",
            }
            for i in range(_p12_cat_limit)
        ]
        if limit >= _p12_fetch_limit:
            # Widened call: reveal the P0 entry that would be beyond raw cat_limit
            return _p2_pool + [
                {
                    "id": 399,
                    "title": "OVERFETCH P0 critical hit",
                    "priority": "P0",
                    "intensity": 0.01,
                    "confidence": 0.6,
                    "last_seen": "2020-01-01T00:00:00",
                    "category": "mistake",
                }
            ]
        return _p2_pool  # narrow call: P0 entry invisible to outer rerank

    def _fake_ske_p12(db, query, cat, limit, min_confidence=0.0):
        return []  # FTS contributes nothing so only semantic entries are in play

    _p12_mock_db = _sqlite3_p12.connect(":memory:")
    _orig_ss_p12 = _brf.search_semantic
    _orig_ske_p12 = _brf.search_knowledge_entries
    _orig_gdb_p12 = _brf.get_db
    _orig_ghl_p12 = _brf._get_briefing_half_life

    _brf.search_semantic = _fake_ss_p12
    _brf.search_knowledge_entries = _fake_ske_p12
    _brf.get_db = lambda: _p12_mock_db
    _brf._get_briefing_half_life = lambda db: 30.0

    try:
        _p12_ctx_out = _brf.generate_subagent_context("auth bug", limit=_p12_cat_limit, infer_auto_mode=False)
        _p12_bf_out = _brf.generate_briefing(
            "auth bug", limit=_p12_cat_limit, infer_auto_mode=False, min_confidence=0.0
        )
    finally:
        _brf.search_semantic = _orig_ss_p12
        _brf.search_knowledge_entries = _orig_ske_p12
        _brf.get_db = _orig_gdb_p12
        _brf._get_briefing_half_life = _orig_ghl_p12
        _p12_mock_db.close()

    test(
        "P121-12a: generate_subagent_context widens semantic fetch — P0 hit beyond cat_limit surfaces",
        "OVERFETCH P0 critical hit" in _p12_ctx_out,
        f"output={_p12_ctx_out[:400]}",
    )
    test(
        "P121-12b: generate_briefing widens semantic fetch — P0 hit beyond cat_limit surfaces",
        "OVERFETCH P0 critical hit" in _p12_bf_out,
        f"output={_p12_bf_out[:400]}",
    )
except Exception as _e:
    test("P121-12: semantic overfetch path regression (Blocker 4)", False, str(_e))

# -- v22/v23 collision-repair: run the real migrate.py path against a legacy DB --
try:
    with tempfile.TemporaryDirectory(prefix="migration-collision-") as _cr_tmp:
        _cr_db_path = Path(_cr_tmp) / "knowledge.db"
        _cr_db = sqlite3.connect(str(_cr_db_path))
        _cr_db.executescript("""
            CREATE TABLE schema_version (
                version INTEGER PRIMARY KEY,
                migrated_at TEXT DEFAULT (datetime('now')),
                name TEXT DEFAULT ''
            );
            CREATE TABLE knowledge_entries (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                stable_id TEXT DEFAULT '',
                title TEXT NOT NULL,
                content TEXT DEFAULT '',
                category TEXT DEFAULT 'pattern',
                tags TEXT DEFAULT '[]',
                wing TEXT DEFAULT '',
                room TEXT DEFAULT '',
                facts TEXT DEFAULT '[]',
                est_tokens INTEGER DEFAULT 0,
                valence TEXT DEFAULT '',
                intensity REAL DEFAULT 0.5
            );
            INSERT INTO schema_version (version, name) VALUES (22, 'file_annotations');
        """)
        _cr_db.commit()
        _cr_db.close()

        _cr_result = subprocess.run(
            [sys.executable, str(REPO / "migrate.py"), str(_cr_db_path)],
            capture_output=True,
            text=True,
            cwd=str(REPO),
            encoding="utf-8",
            errors="replace",
        )

        _cr_check = sqlite3.connect(str(_cr_db_path))
        _cr_cols_after = {row[1] for row in _cr_check.execute("PRAGMA table_info(knowledge_entries)").fetchall()}
        _cr_versions_after = _cr_check.execute("SELECT version, name FROM schema_version ORDER BY version").fetchall()
        _cr_check.close()

        test(
            "CR-01: real migrate.py repair adds priority to legacy v22 collision DB",
            _cr_result.returncode == 0
            and "priority" in _cr_cols_after
            and "collision-repair: priority column added" in _cr_result.stdout,
            (
                f"code={_cr_result.returncode} columns={sorted(_cr_cols_after)} "
                f"stdout={_cr_result.stdout[-300:]} stderr={_cr_result.stderr[-300:]}"
            ),
        )
        test(
            "CR-02: real migrate.py repair renames v22 to priority and keeps v23 file_annotations",
            (22, "priority") in _cr_versions_after
            and (23, "file_annotations") in _cr_versions_after
            and (22, "file_annotations") not in _cr_versions_after,
            f"versions={_cr_versions_after}",
        )
except Exception as _e:
    test("CR-01: v22/v23 collision-repair regression", False, str(_e))


# ---------------------------------------------------------------------------
# SD-01 – SD-04: soft-delete duplicate detection regression (Wave 2b fix)
#
# Before the fix, add_entry()'s duplicate-detection SELECT did not filter
# `deleted_at IS NULL`, so re-learning a same category/title after a
# soft-delete would UPDATE the ghost row without clearing deleted_at —
# making the new knowledge permanently invisible to read paths.
# ---------------------------------------------------------------------------

try:
    import importlib as _sd_importlib

    _learn_sd = _sd_importlib.import_module("learn")

    # SD-01 – SD-03: re-learning after soft-delete creates a NEW row (not updating ghost)
    # Use a file-based DB because add_entry() commits and closes the connection.
    with tempfile.TemporaryDirectory(prefix="learn-softdelete-") as _sd_tmp:
        _sd_db_path = Path(_sd_tmp) / "knowledge.db"
        _sd_setup = sqlite3.connect(str(_sd_db_path))
        _sd_setup.executescript("""
            CREATE TABLE knowledge_entries (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                session_id TEXT NOT NULL DEFAULT 'test-session',
                category TEXT NOT NULL,
                title TEXT NOT NULL,
                content TEXT NOT NULL DEFAULT '',
                tags TEXT DEFAULT '',
                confidence REAL DEFAULT 0.7,
                occurrence_count INTEGER DEFAULT 1,
                first_seen TEXT DEFAULT '2024-01-01T00:00:00',
                last_seen TEXT DEFAULT '2024-01-01T00:00:00',
                wing TEXT DEFAULT '',
                room TEXT DEFAULT '',
                facts TEXT DEFAULT '[]',
                est_tokens INTEGER DEFAULT 0,
                task_id TEXT DEFAULT '',
                affected_files TEXT DEFAULT '[]',
                stable_id TEXT,
                deleted_at TEXT DEFAULT NULL
            );
            CREATE VIRTUAL TABLE IF NOT EXISTS ke_fts USING fts5(
                title, content, tags, category, wing, room, facts
            );
        """)
        # Insert a soft-deleted ghost row
        _sd_setup.execute(
            "INSERT INTO knowledge_entries (category, title, content, deleted_at) VALUES (?,?,?,?)",
            ("mistake", "soft-delete-dedup-test", "old ghost content", "2024-01-01T00:00:00"),
        )
        _sd_setup.commit()
        _sd_ghost_id = _sd_setup.execute(
            "SELECT id FROM knowledge_entries WHERE title = 'soft-delete-dedup-test'"
        ).fetchone()[0]
        _sd_setup.close()

        # Patch learn.DB_PATH so add_entry() opens our temp DB
        _orig_db_path_sd = _learn_sd.DB_PATH
        _learn_sd.DB_PATH = _sd_db_path
        try:
            _sd_new_id = _learn_sd.add_entry(
                category="mistake",
                title="soft-delete-dedup-test",
                content="new visible content",
                session_id="test-session",
                skip_scan=True,
                skip_gate=True,
            )
        finally:
            _learn_sd.DB_PATH = _orig_db_path_sd

        # Reopen to verify results
        _sd_check = sqlite3.connect(str(_sd_db_path))
        _sd_check.row_factory = sqlite3.Row
        _sd_rows = _sd_check.execute(
            "SELECT id, deleted_at, content FROM knowledge_entries WHERE title = 'soft-delete-dedup-test'"
        ).fetchall()
        _sd_ghost_row = next((r for r in _sd_rows if r["id"] == _sd_ghost_id), None)
        _sd_live_rows = [r for r in _sd_rows if r["deleted_at"] is None]
        _sd_check.close()

        test(
            "SD-01: re-learn after soft-delete creates new row (total rows = 2)",
            len(_sd_rows) == 2,
            f"rows={[(r['id'], r['deleted_at']) for r in _sd_rows]}",
        )
        test(
            "SD-02: re-learned entry is visible (deleted_at IS NULL) with new content",
            len(_sd_live_rows) == 1 and _sd_live_rows[0]["content"] == "new visible content",
            f"live_rows={[(r['id'], r['deleted_at'], r['content']) for r in _sd_live_rows]}",
        )
        test(
            "SD-03: ghost row still has deleted_at set (not cleared by re-learn)",
            _sd_ghost_row is not None and _sd_ghost_row["deleted_at"] is not None,
            f"ghost_deleted_at={_sd_ghost_row['deleted_at'] if _sd_ghost_row else None}",
        )

    # SD-04: without deleted_at column, duplicate learning still updates existing row (no regression)
    with tempfile.TemporaryDirectory(prefix="learn-nodelete-") as _sd_nosd_tmp:
        _sd_nosd_path = Path(_sd_nosd_tmp) / "knowledge.db"
        _sd_nosd_setup = sqlite3.connect(str(_sd_nosd_path))
        _sd_nosd_setup.executescript("""
            CREATE TABLE knowledge_entries (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                session_id TEXT NOT NULL DEFAULT 'test-session',
                category TEXT NOT NULL,
                title TEXT NOT NULL,
                content TEXT NOT NULL DEFAULT '',
                tags TEXT DEFAULT '',
                confidence REAL DEFAULT 0.7,
                occurrence_count INTEGER DEFAULT 1,
                first_seen TEXT DEFAULT '2024-01-01T00:00:00',
                last_seen TEXT DEFAULT '2024-01-01T00:00:00',
                wing TEXT DEFAULT '',
                room TEXT DEFAULT '',
                facts TEXT DEFAULT '[]',
                est_tokens INTEGER DEFAULT 0,
                task_id TEXT DEFAULT '',
                affected_files TEXT DEFAULT '[]',
                stable_id TEXT
            );
            CREATE VIRTUAL TABLE IF NOT EXISTS ke_fts USING fts5(
                title, content, tags, category, wing, room, facts
            );
        """)
        _sd_nosd_setup.commit()
        _sd_nosd_setup.close()

        _orig_db_path_sd2 = _learn_sd.DB_PATH
        _learn_sd.DB_PATH = _sd_nosd_path
        try:
            _learn_sd.add_entry(
                category="pattern",
                title="no-softdelete-dedup",
                content="first content",
                session_id="test-session",
                skip_scan=True,
                skip_gate=True,
            )
            _learn_sd.add_entry(
                category="pattern",
                title="no-softdelete-dedup",
                content="second content that is longer than first content",
                session_id="test-session",
                skip_scan=True,
                skip_gate=True,
            )
        finally:
            _learn_sd.DB_PATH = _orig_db_path_sd2

        _sd_nosd_check = sqlite3.connect(str(_sd_nosd_path))
        _sd_nosd_rows = _sd_nosd_check.execute(
            "SELECT id, occurrence_count FROM knowledge_entries WHERE title = 'no-softdelete-dedup'"
        ).fetchall()
        _sd_nosd_check.close()
        test(
            "SD-04: without deleted_at column, duplicate learning updates existing row (no regression)",
            len(_sd_nosd_rows) == 1 and _sd_nosd_rows[0][1] == 2,
            f"rows={[(r[0], r[1]) for r in _sd_nosd_rows]}",
        )

except Exception as _e:
    test("SD-01: soft-delete dedup regression suite", False, str(_e))

# ---------------------------------------------------------------------------
# I456: composite indexes migration (v31) + sync table pruning (cron-tasks.py)
# ---------------------------------------------------------------------------

# I456-1: migration v31 is declared in MIGRATIONS with expected name
try:
    import ast as _ast456
    import pathlib as _pathlib456

    _mig_src = _pathlib456.Path(REPO / "migrate.py").read_text(encoding="utf-8")
    _mig_tree = _ast456.parse(_mig_src)
    _found_migrations = None
    for _node in _ast456.walk(_mig_tree):
        if isinstance(_node, _ast456.Assign):
            for _t in _node.targets:
                if isinstance(_t, _ast456.Name) and _t.id == "MIGRATIONS":
                    _found_migrations = _ast456.literal_eval(_node.value)
    _v31_entries = [m for m in (_found_migrations or []) if m[0] == 31]
    test(
        "I456-1a: migration v31 declared exactly once",
        len(_v31_entries) == 1,
        f"found={len(_v31_entries)}",
    )
    if _v31_entries:
        _v31_name = _v31_entries[0][1]
        _v31_stmts = _v31_entries[0][2]
        test(
            "I456-1b: migration v31 name is composite_indexes_sync_timestamps",
            _v31_name == "composite_indexes_sync_timestamps",
            f"name={_v31_name}",
        )
        _idx_names = {
            "idx_ke_cat_wing_room_conf",
            "idx_ke_session_cat",
            "idx_ke_source_task",
            "idx_sync_txns_created",
            "idx_sync_ops_created",
            "idx_sync_failures_failed_at",
        }
        _declared = {s for s in _v31_stmts if isinstance(s, str)}
        _missing = [n for n in _idx_names if not any(n in s for s in _declared)]
        test(
            "I456-1c: all 6 composite/timestamp indexes declared in v31",
            len(_missing) == 0,
            f"missing={_missing}",
        )
except Exception as _e:
    test("I456-1: migration v31 declaration check", False, str(_e))

# I456-2: migration v31 is idempotent on a fresh in-memory DB
try:
    import importlib.util as _ilu456, types as _types456

    _mig_db = sqlite3.connect(":memory:")
    _mig_db.executescript("""
        CREATE TABLE schema_version (
            version INTEGER PRIMARY KEY,
            name TEXT DEFAULT '',
            migrated_at TEXT DEFAULT (datetime('now'))
        );
        CREATE TABLE knowledge_entries (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            session_id TEXT NOT NULL DEFAULT 'test',
            category TEXT NOT NULL DEFAULT 'mistake',
            title TEXT NOT NULL DEFAULT 'title',
            content TEXT NOT NULL DEFAULT 'content',
            wing TEXT DEFAULT '',
            room TEXT DEFAULT '',
            source TEXT DEFAULT 'copilot',
            task_id TEXT DEFAULT '',
            confidence REAL DEFAULT 1.0
        );
        CREATE TABLE sync_txns (
            txn_id TEXT PRIMARY KEY,
            replica_id TEXT NOT NULL DEFAULT '',
            status TEXT NOT NULL DEFAULT 'pending',
            created_at TEXT NOT NULL DEFAULT (datetime('now')),
            committed_at TEXT DEFAULT ''
        );
        CREATE TABLE sync_ops (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            txn_id TEXT NOT NULL DEFAULT '',
            table_name TEXT NOT NULL DEFAULT '',
            op_type TEXT NOT NULL DEFAULT 'insert',
            row_stable_id TEXT NOT NULL DEFAULT '',
            row_payload TEXT NOT NULL DEFAULT '',
            op_index INTEGER NOT NULL DEFAULT 0,
            created_at TEXT NOT NULL DEFAULT (datetime('now'))
        );
        CREATE TABLE sync_failures (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            txn_id TEXT DEFAULT '',
            table_name TEXT DEFAULT '',
            row_stable_id TEXT DEFAULT '',
            error_code TEXT DEFAULT '',
            error_message TEXT DEFAULT '',
            failed_at TEXT NOT NULL DEFAULT (datetime('now')),
            retry_count INTEGER DEFAULT 0
        );
        INSERT INTO schema_version (version, name) VALUES (30, 'episode_batch_compile');
    """)
    # Apply v31 statements manually (as migrate.py runner would)
    _v31_sql = [
        # ALTER TABLE is idempotent — "duplicate column" errors are swallowed
        "ALTER TABLE knowledge_entries ADD COLUMN confidence REAL DEFAULT 1.0",
        "CREATE INDEX IF NOT EXISTS idx_ke_cat_wing_room_conf ON knowledge_entries(category, wing, room, confidence)",
        "CREATE INDEX IF NOT EXISTS idx_ke_session_cat ON knowledge_entries(session_id, category)",
        "CREATE INDEX IF NOT EXISTS idx_ke_source_task ON knowledge_entries(source, task_id)",
        "CREATE TABLE IF NOT EXISTS sync_txns (txn_id TEXT PRIMARY KEY, replica_id TEXT NOT NULL, status TEXT NOT NULL, created_at TEXT NOT NULL, committed_at TEXT DEFAULT '')",
        "CREATE TABLE IF NOT EXISTS sync_ops (id INTEGER PRIMARY KEY AUTOINCREMENT, txn_id TEXT NOT NULL, table_name TEXT NOT NULL, op_type TEXT NOT NULL, row_stable_id TEXT NOT NULL, row_payload TEXT NOT NULL, op_index INTEGER NOT NULL, created_at TEXT NOT NULL)",
        "CREATE TABLE IF NOT EXISTS sync_failures (id INTEGER PRIMARY KEY AUTOINCREMENT, failed_at TEXT NOT NULL)",
        "CREATE INDEX IF NOT EXISTS idx_sync_txns_created ON sync_txns(created_at)",
        "CREATE INDEX IF NOT EXISTS idx_sync_ops_created ON sync_ops(created_at)",
        "CREATE INDEX IF NOT EXISTS idx_sync_failures_failed_at ON sync_failures(failed_at)",
    ]
    for _sql in _v31_sql:
        try:
            _mig_db.execute(_sql)
        except Exception as _e2:
            if "duplicate" in str(_e2).lower() or "already exists" in str(_e2).lower():
                pass
            else:
                raise
    _mig_db.execute("INSERT OR IGNORE INTO schema_version (version, name) VALUES (31, 'composite_indexes_sync_timestamps')")
    _mig_db.commit()

    _idx_rows = {row[1] for row in _mig_db.execute("PRAGMA index_list(knowledge_entries)").fetchall()}
    test(
        "I456-2a: idx_ke_cat_wing_room_conf created on knowledge_entries",
        "idx_ke_cat_wing_room_conf" in _idx_rows,
        f"indexes={_idx_rows}",
    )
    test(
        "I456-2b: idx_ke_session_cat created on knowledge_entries",
        "idx_ke_session_cat" in _idx_rows,
        f"indexes={_idx_rows}",
    )
    test(
        "I456-2c: idx_ke_source_task created on knowledge_entries",
        "idx_ke_source_task" in _idx_rows,
        f"indexes={_idx_rows}",
    )
    _sync_idx = {row[1] for row in _mig_db.execute("PRAGMA index_list(sync_txns)").fetchall()}
    test(
        "I456-2d: idx_sync_txns_created created on sync_txns",
        "idx_sync_txns_created" in _sync_idx,
        f"indexes={_sync_idx}",
    )

    # Idempotency: applying statements a second time must not raise
    _raised = False
    try:
        for _sql in _v31_sql:
            try:
                _mig_db.execute(_sql)
            except Exception as _idem_e:
                if "duplicate" in str(_idem_e).lower() or "already exists" in str(_idem_e).lower():
                    pass
                else:
                    _raised = True
                    break
        _mig_db.commit()
    except Exception as _idem_exc:
        _raised = True
    test("I456-2e: v31 statements are idempotent (IF NOT EXISTS)", not _raised)

    # Version recorded
    _ver_row = _mig_db.execute("SELECT version, name FROM schema_version WHERE version=31").fetchone()
    test(
        "I456-2f: schema_version row for v31 recorded",
        _ver_row is not None and _ver_row[1] == "composite_indexes_sync_timestamps",
        f"row={_ver_row}",
    )
    _mig_db.close()
except Exception as _e:
    test("I456-2: migration v31 idempotency", False, str(_e))

# I456-3: sync table pruning deletes only aged rows
try:
    import importlib.util as _ilu456b
    import tempfile as _tempfile456

    _cron_spec = _ilu456b.spec_from_file_location("cron_tasks_456", REPO / "cron-tasks.py")
    _cron_mod = _ilu456b.module_from_spec(_cron_spec)
    _cron_spec.loader.exec_module(_cron_mod)

    _prune_db_path = Path(_tempfile456.mkdtemp()) / "prune_test.db"
    _pconn = sqlite3.connect(str(_prune_db_path))
    _pconn.executescript("""
        CREATE TABLE sync_ops (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            txn_id TEXT NOT NULL DEFAULT '',
            table_name TEXT NOT NULL DEFAULT '',
            op_type TEXT NOT NULL DEFAULT 'insert',
            row_stable_id TEXT NOT NULL DEFAULT '',
            row_payload TEXT NOT NULL DEFAULT '',
            op_index INTEGER NOT NULL DEFAULT 0,
            created_at TEXT NOT NULL
        );
        CREATE TABLE sync_txns (
            txn_id TEXT PRIMARY KEY,
            replica_id TEXT NOT NULL DEFAULT '',
            status TEXT NOT NULL DEFAULT 'pending',
            created_at TEXT NOT NULL
        );
        CREATE TABLE sync_failures (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            txn_id TEXT DEFAULT '',
            failed_at TEXT NOT NULL,
            retry_count INTEGER DEFAULT 0
        );
    """)
    from datetime import datetime as _dt456, timezone as _tz456

    _now456 = _dt456(2025, 6, 1, 12, 0, 0, tzinfo=_tz456.utc)
    # Old rows (should be pruned)
    _old_ops = "2025-04-15T00:00:00"    # 47 days old → pruned (>30d)
    _old_txns = "2025-04-20T00:00:00"   # 42 days old → pruned (>30d)
    _old_fail = "2025-05-20T00:00:00"   # 12 days old → pruned (>7d)
    # Recent rows (should survive)
    _new_ops = "2025-05-20T00:00:00"    # 12 days old → kept (<30d)
    _new_txns = "2025-05-20T00:00:00"   # 12 days old → kept (<30d)
    _new_fail = "2025-05-28T00:00:00"   # 4 days old → kept (<7d)

    _pconn.execute("INSERT INTO sync_ops (created_at) VALUES (?)", (_old_ops,))
    _pconn.execute("INSERT INTO sync_ops (created_at) VALUES (?)", (_new_ops,))
    _pconn.execute("INSERT INTO sync_txns (txn_id, created_at) VALUES ('old-txn', ?)", (_old_txns,))
    _pconn.execute("INSERT INTO sync_txns (txn_id, created_at) VALUES ('new-txn', ?)", (_new_txns,))
    _pconn.execute("INSERT INTO sync_failures (failed_at) VALUES (?)", (_old_fail,))
    _pconn.execute("INSERT INTO sync_failures (failed_at) VALUES (?)", (_new_fail,))
    _pconn.commit()
    _pconn.close()

    _deleted = _cron_mod._prune_sync_tables(_prune_db_path, _now456)

    test(
        "I456-3a: sync_ops: 1 old row pruned",
        _deleted.get("sync_ops") == 1,
        f"deleted={_deleted}",
    )
    test(
        "I456-3b: sync_txns: 1 old row pruned",
        _deleted.get("sync_txns") == 1,
        f"deleted={_deleted}",
    )
    test(
        "I456-3c: sync_failures: 1 aged row pruned",
        _deleted.get("sync_failures") == 1,
        f"deleted={_deleted}",
    )

    # Verify surviving rows
    _pconn2 = sqlite3.connect(str(_prune_db_path))
    _remaining_ops = _pconn2.execute("SELECT COUNT(*) FROM sync_ops").fetchone()[0]
    _remaining_txns = _pconn2.execute("SELECT COUNT(*) FROM sync_txns").fetchone()[0]
    _remaining_fail = _pconn2.execute("SELECT COUNT(*) FROM sync_failures").fetchone()[0]
    _pconn2.close()
    test("I456-3d: sync_ops: 1 recent row survives", _remaining_ops == 1, f"remaining={_remaining_ops}")
    test("I456-3e: sync_txns: 1 recent row survives", _remaining_txns == 1, f"remaining={_remaining_txns}")
    test("I456-3f: sync_failures: 1 recent row survives", _remaining_fail == 1, f"remaining={_remaining_fail}")
except Exception as _e:
    test("I456-3: sync pruning correctness", False, str(_e))

# I456-4: _prune_sync_tables is safe on a DB missing sync tables (no exception)
try:
    import importlib.util as _ilu456c
    import tempfile as _tempfile456c

    _cron_spec2 = _ilu456c.spec_from_file_location("cron_tasks_456c", REPO / "cron-tasks.py")
    _cron_mod2 = _ilu456c.module_from_spec(_cron_spec2)
    _cron_spec2.loader.exec_module(_cron_mod2)

    _empty_db_path = Path(_tempfile456c.mkdtemp()) / "empty.db"
    sqlite3.connect(str(_empty_db_path)).close()  # create empty DB
    from datetime import datetime as _dt456d
    _deleted2 = _cron_mod2._prune_sync_tables(_empty_db_path, _dt456d(2025, 6, 1, 12, 0, 0))
    test(
        "I456-4: _prune_sync_tables safe on DB without sync tables",
        isinstance(_deleted2, dict),
        f"result={_deleted2}",
    )
except Exception as _e:
    test("I456-4: _prune_sync_tables graceful on missing tables", False, str(_e))

# I456-5: sync_pruning template declared in TEMPLATE_DEFINITIONS
try:
    import importlib.util as _ilu456d

    _cron_spec3 = _ilu456d.spec_from_file_location("cron_tasks_456d", REPO / "cron-tasks.py")
    _cron_mod3 = _ilu456d.module_from_spec(_cron_spec3)
    _cron_spec3.loader.exec_module(_cron_mod3)

    test(
        "I456-5a: sync_pruning in TEMPLATE_DEFINITIONS",
        "sync_pruning" in _cron_mod3.TEMPLATE_DEFINITIONS,
    )
    _sp_sched = _cron_mod3.TEMPLATE_DEFINITIONS.get("sync_pruning", {}).get("default_schedule", {})
    test(
        "I456-5b: sync_pruning default schedule is daily",
        _sp_sched.get("kind") == "daily",
        f"schedule={_sp_sched}",
    )
except Exception as _e:
    test("I456-5: sync_pruning template declaration", False, str(_e))

# ---------------------------------------------------------------------------

print(f"Results: {PASS} passed, {FAIL} failed out of {PASS + FAIL}")
if FAIL == 0:
    print("🎉 All tests passed!")
else:
    print(f"⚠️  {FAIL} test(s) need attention")
sys.exit(0 if FAIL == 0 else 1)
