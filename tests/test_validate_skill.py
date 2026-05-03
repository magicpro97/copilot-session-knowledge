#!/usr/bin/env python3
"""
test_validate_skill.py — Focused tests for validate-skill.py validation logic.

Covers:
  - Missing file → error
  - Missing/malformed frontmatter → errors
  - Valid 'name' field formats (valid, too-long, leading hyphen, consecutive hyphens, empty)
  - Description word count and trigger phrase checks
  - Line count limit (500 max)
  - Example tag presence and mismatch detection
  - Required section detection (title, when, workflow)
  - Heavy-handed directive count
  - Dangling references/ warning
  - Path traversal in references skipped (security)
  - Directory input resolves to SKILL.md inside

Run: python3 tests/test_validate_skill.py
"""

import importlib.util
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

SCRATCH = REPO / ".test-scratch" / "validate-skill-tests"
SCRATCH.mkdir(parents=True, exist_ok=True)

# Ensure local modules importable
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))


def test(name: str, condition: bool, detail: str = "") -> None:
    global PASS, FAIL
    if condition:
        PASS += 1
        print(f"  ✅ {name}")
    else:
        FAIL += 1
        print(f"  ❌ {name}" + (f" — {detail}" if detail else ""))


# ---------------------------------------------------------------------------
# Load module
# ---------------------------------------------------------------------------

_script = REPO / "validate-skill.py"
_spec = importlib.util.spec_from_file_location("_vs", _script)
_vs = importlib.util.module_from_spec(_spec)
_saved_argv = sys.argv[:]
sys.argv = [str(_script)]
try:
    _spec.loader.exec_module(_vs)
finally:
    sys.argv = _saved_argv

validate = _vs.validate


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _skill_dir(name: str) -> Path:
    d = SCRATCH / name
    d.mkdir(parents=True, exist_ok=True)
    return d


def _write_skill(dir_name: str, content: str) -> Path:
    d = _skill_dir(dir_name)
    p = d / "SKILL.md"
    p.write_text(content, encoding="utf-8")
    return p


MINIMAL_VALID = """\
---
name: my-skill
description: >-
  Use when you need to do something useful. Trigger word: invoke this skill.
---

# My Skill

## When to use

Use when you need to do something.

## Workflow

1. Do the thing.
2. Done.

<example>
Before: nothing. After: something done.
</example>
"""


# ── 1. Missing file ────────────────────────────────────────────────────────────

print("\n❌ Missing file")

errors, warnings = validate(SCRATCH / "nonexistent" / "SKILL.md")
test("missing file → error returned", len(errors) == 1)
test("missing file error mentions 'not found'", "not found" in errors[0].lower())


# ── 2. Missing frontmatter ────────────────────────────────────────────────────

print("\n📄 Frontmatter checks")

no_fm = _write_skill("no-fm", "# My Skill\nNo frontmatter here.")
errors, _ = validate(no_fm)
test("no frontmatter → error", any("frontmatter" in e.lower() for e in errors))

malformed_fm = _write_skill("bad-fm", "---\nname: my-skill\n# no closing ---\n# Title\n")
errors2, _ = validate(malformed_fm)
test("unclosed frontmatter → error", any("frontmatter" in e.lower() or "closing" in e.lower() for e in errors2))

missing_name = _write_skill("no-name", "---\ndescription: A useful skill.\n---\n# T\n<example>e</example>")
errors3, _ = validate(missing_name)
test("missing name field → error", any("name" in e.lower() for e in errors3))

missing_desc = _write_skill("no-desc", "---\nname: my-skill\n---\n# T\n<example>e</example>")
errors4, _ = validate(missing_desc)
test("missing description field → error", any("description" in e.lower() for e in errors4))


# ── 3. Name format validation ─────────────────────────────────────────────────

print("\n🏷️  Name format")

def _make_name_skill(dir_name: str, skill_name: str) -> Path:
    content = f"---\nname: {skill_name}\ndescription: Use when you want to do stuff. Trigger: invoke this.\n---\n# T\n## When\nUse it.\n## Workflow\nDo it.\n<example>\nex\n</example>\n"
    return _write_skill(dir_name, content)

valid_name = _make_name_skill("valid-name", "my-skill")
errors, warnings = validate(valid_name)
name_errors = [e for e in errors if "name" in e.lower() and "invalid" in e.lower()]
test("valid name has no name errors", len(name_errors) == 0)

# Leading hyphen
bad_lead = _make_name_skill("bad-lead", "-my-skill")
errors_lead, _ = validate(bad_lead)
test("leading hyphen → name error", any("name" in e.lower() for e in errors_lead))

# Trailing hyphen
bad_trail = _make_name_skill("bad-trail", "my-skill-")
errors_trail, _ = validate(bad_trail)
test("trailing hyphen → name error", any("name" in e.lower() for e in errors_trail))

# Consecutive hyphens
bad_consec = _make_name_skill("bad-consec", "my--skill")
errors_consec, _ = validate(bad_consec)
test("consecutive hyphens → error", any("consecutive" in e.lower() for e in errors_consec))

# Name too long (65 chars)
long_name = "a" * 65
bad_long = _make_name_skill("bad-long", long_name)
errors_long, _ = validate(bad_long)
test("name > 64 chars → error", any("64" in e or "chars" in e.lower() for e in errors_long))

# Uppercase letters
bad_upper = _make_name_skill("bad-upper", "MySkill")
errors_upper, _ = validate(bad_upper)
test("uppercase name → error", len([e for e in errors_upper if "name" in e.lower() and ("invalid" in e.lower() or "lowercase" in e.lower())]) > 0
     or len([e for e in errors_upper if "invalid" in e.lower()]) > 0)

# Empty name (bare 'name:' key)
empty_name_content = "---\nname:\ndescription: Use when stuff. Invoke trigger.\n---\n# T\n## When\nUse it.\n<example>\nex\n</example>\n"
empty_name_path = _write_skill("empty-name", empty_name_content)
errors_en, _ = validate(empty_name_path)
test("empty name value → error", any("name" in e.lower() for e in errors_en))


# ── 4. Description checks ─────────────────────────────────────────────────────

print("\n📝 Description checks")

# Too short description (< 10 words)
short_desc = _write_skill("short-desc",
    "---\nname: my-skill\ndescription: Use it.\n---\n# T\n## When\nUse it.\n## Workflow\nDo it.\n<example>\nex\n</example>\n")
_, warnings_short = validate(short_desc)
test("short description → warning", any("word" in w.lower() for w in warnings_short))

# Missing trigger phrases
no_trigger = _write_skill("no-trigger",
    "---\nname: my-skill\ndescription: This skill does many useful things for development workflows.\n---\n# T\n## When\nUse it.\n<example>\nex\n</example>\n")
_, warnings_nt = validate(no_trigger)
test("description without triggers → warning", any("trigger" in w.lower() for w in warnings_nt))


# ── 5. Line count checks ─────────────────────────────────────────────────────

print("\n📏 Line count")

# 501 lines → error
long_content = MINIMAL_VALID + "\n" * 490  # MINIMAL_VALID is ~15 lines + 490 = ~505 lines
long_skill = _write_skill("too-long", long_content)
errors_lc, _ = validate(long_skill)
test("501+ lines → error", any("line" in e.lower() for e in errors_lc))


# ── 6. Example tag checks ─────────────────────────────────────────────────────

print("\n🏷️  Example tags")

no_examples = _write_skill("no-examples",
    "---\nname: my-skill\ndescription: Use when something useful happens. Trigger: invoke this.\n---\n# T\n## When\nUse.\n## Workflow\nDo.\n")
errors_ne, _ = validate(no_examples)
test("no examples → error", any("example" in e.lower() for e in errors_ne))

# Mismatched tags
mismatch = _write_skill("mismatch-tags",
    "---\nname: my-skill\ndescription: Use when stuff. Invoke trigger.\n---\n# T\n## When\nUse.\n<example>\nfoo\n")
errors_mm, _ = validate(mismatch)
test("mismatched example tags → error", any("mismatch" in e.lower() or "closing" in e.lower() for e in errors_mm))


# ── 7. Required sections ──────────────────────────────────────────────────────

print("\n📑 Required sections")

no_title = _write_skill("no-title",
    "---\nname: my-skill\ndescription: Use when something. Trigger: invoke.\n---\n## When\nUse it.\n<example>\nex\n</example>\n")
errors_t, _ = validate(no_title)
test("missing H1 title → error", any("title" in e.lower() for e in errors_t))

# Missing 'when' section → warning (not error)
no_when = _write_skill("no-when",
    "---\nname: my-skill\ndescription: Use when stuff. Invoke trigger.\n---\n# Title\n<example>ex</example>\n")
_, warnings_nw = validate(no_when)
test("missing 'when' section → warning", any("when" in w.lower() or "trigger" in w.lower() for w in warnings_nw))


# ── 8. Heavy-handed directive count ──────────────────────────────────────────

print("\n💪 Heavy-handed directives")

heavy_lines = "\n".join(f"MUST do step {i}." for i in range(10))
heavy_content = MINIMAL_VALID + "\n" + heavy_lines
heavy_skill = _write_skill("heavy", heavy_content)
_, warnings_h = validate(heavy_skill)
test("many MUST/ALWAYS directives → warning", any("heavy" in w.lower() or "must" in w.lower() or "directive" in w.lower() for w in warnings_h))


# ── 9. Dangling references/ warning ──────────────────────────────────────────

print("\n🔗 Dangling references")

dangles = _write_skill("dangle",
    "---\nname: my-skill\ndescription: Use when something. Trigger: invoke.\n---\n# T\n## When\nUse it.\n## Workflow\nSee references/not-there.md for details.\n<example>ex</example>\n")
_, warnings_d = validate(dangles)
test("dangling reference → warning", any("dangling" in w.lower() or "not-there" in w for w in warnings_d))

# Real reference file → no warning
ref_dir = _skill_dir("real-ref")
(ref_dir / "references").mkdir(exist_ok=True)
(ref_dir / "references" / "guide.md").write_text("content", encoding="utf-8")
real_ref_content = "---\nname: real-ref\ndescription: Use when something. Trigger: invoke.\n---\n# T\n## When\nUse it.\n## Workflow\nSee references/guide.md.\n<example>ex</example>\n"
(ref_dir / "SKILL.md").write_text(real_ref_content, encoding="utf-8")
_, warnings_rr = validate(ref_dir / "SKILL.md")
dangling_warnings = [w for w in warnings_rr if "dangling" in w.lower()]
test("existing reference file → no dangling warning", len(dangling_warnings) == 0)


# ── 10. Path traversal in references (security) ───────────────────────────────

print("\n🔐 Path traversal")

traversal = _write_skill("traversal",
    "---\nname: my-skill\ndescription: Use when something. Trigger: invoke.\n---\n# T\n## When\nUse it.\n## Workflow\nSee references/../SKILL.md for details.\n<example>ex</example>\n")
_, warnings_tr = validate(traversal)
test("path traversal in reference → suspicious warning", any(".." in w or "traversal" in w.lower() or "suspicious" in w.lower() for w in warnings_tr))


# ── 11. Directory input ───────────────────────────────────────────────────────

print("\n📁 Directory input")

dir_path = _skill_dir("dir-input")
(dir_path / "SKILL.md").write_text(MINIMAL_VALID, encoding="utf-8")
errors_di, warnings_di = validate(dir_path)
# Valid skill should produce no errors
test("directory input resolves to SKILL.md", len(errors_di) == 0)


# ── 12. Full valid skill passes cleanly ──────────────────────────────────────

print("\n✅ Full valid skill")

valid_skill = _write_skill("valid-full", MINIMAL_VALID)
errors_v, warnings_v = validate(valid_skill)
test("valid skill has no errors", len(errors_v) == 0)


# ── Summary ──────────────────────────────────────────────────────────────────

print(f"\n{'='*50}")
print(f"Results: {PASS} passed, {FAIL} failed")

import shutil
try:
    shutil.rmtree(SCRATCH, ignore_errors=True)
except Exception:
    pass

sys.exit(1 if FAIL else 0)
