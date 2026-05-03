#!/usr/bin/env python3
"""
test_checkpoint_diff.py — Isolated tests for checkpoint-diff.py

Run with:
    python3 test_checkpoint_diff.py
"""

import io
import os
import re
import sys
import tempfile
from contextlib import redirect_stdout, redirect_stderr
from pathlib import Path

if os.name == "nt":
    for s in (sys.stdout, sys.stderr):
        if hasattr(s, "reconfigure"):
            s.reconfigure(encoding="utf-8", errors="replace")

# ── Minimal test harness ─────────────────────────────────────────────────────

_passed = 0
_failed = 0


def test(description: str, result: bool, detail: str = "") -> None:
    global _passed, _failed
    if result:
        print(f"  ✓ {description}")
        _passed += 1
    else:
        msg = f"  ✗ {description}"
        if detail:
            msg += f"\n    {detail}"
        print(msg)
        _failed += 1


def section(title: str) -> None:
    print(f"\n{title}")
    print("─" * len(title))


# ── Import target module ─────────────────────────────────────────────────────

TOOLS_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(TOOLS_DIR))

import importlib.util

_spec = importlib.util.spec_from_file_location(
    "checkpoint_diff", TOOLS_DIR / "checkpoint-diff.py"
)
_mod = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_mod)

parse_index = _mod.parse_index
parse_checkpoint_sections = _mod.parse_checkpoint_sections
resolve_selector = _mod.resolve_selector
diff_sections = _mod.diff_sections
format_diff_output = _mod.format_diff_output
format_summary_output = _mod.format_summary_output
detect_session = _mod.detect_session
main = _mod.main
CHECKPOINT_SECTIONS = _mod.CHECKPOINT_SECTIONS


# ── Fixtures ─────────────────────────────────────────────────────────────────


def make_session_dir(tmp: Path, session_id: str = "test-session", cwd: str | None = None) -> Path:
    d = tmp / session_id
    d.mkdir(parents=True, exist_ok=True)
    workspace = d / "workspace.yaml"
    workspace.write_text(
        f"id: {session_id}\ncwd: {cwd or str(tmp)}\ngit_root: {cwd or str(tmp)}\n",
        encoding="utf-8",
    )
    return d


def make_checkpoint(session_dir: Path, seq: int, title: str, sections: dict[str, str]) -> Path:
    """Write a checkpoint file + update index.md matching checkpoint-save.py format."""
    cp_dir = session_dir / "checkpoints"
    cp_dir.mkdir(parents=True, exist_ok=True)

    parts = []
    for s in CHECKPOINT_SECTIONS:
        content = sections.get(s, "").strip()
        if content:
            parts.append(f"<{s}>\n{content}\n</{s}>")
    if not parts:
        parts.append(f"<overview>\n{title}\n</overview>")
    file_content = "\n\n".join(parts) + "\n"

    slug = title.lower()
    slug = re.sub(r"[^a-z0-9]+", "-", slug).strip("-")[:35].rstrip("-")
    fname = f"{seq:03d}-{slug}.md"
    cp_path = cp_dir / fname
    cp_path.write_text(file_content, encoding="utf-8")

    index_path = cp_dir / "index.md"
    index_text = index_path.read_text(encoding="utf-8") if index_path.exists() else (
        "# Checkpoint History\n\n"
        "Checkpoints are listed in chronological order. Checkpoint 1 is the oldest, "
        "higher numbers are more recent.\n\n"
        "| # | Title | File |\n|---|-------|------|\n"
    )
    index_text += f"| {seq} | {title} | {fname} |\n"
    index_path.write_text(index_text, encoding="utf-8")

    return cp_path


# ── Regression: embedded tag text cannot bleed across section boundaries ──────

section("parse_checkpoint_sections() — embedded tag boundary regression")

import tempfile as _tempfile

with _tempfile.TemporaryDirectory() as _td:
    _tmp = Path(_td)

    # overview content contains literal <history> open tag; history must still parse
    _cp = _tmp / "bleed.md"
    _cp.write_text(
        "<overview>\nThis mentions <history> for background context.\n</overview>\n\n"
        "<history>\nReal history entry.\n</history>\n",
        encoding="utf-8",
    )
    _r = parse_checkpoint_sections(_cp)
    test(
        "embedded <history> in overview: overview correct",
        _r.get("overview") == "This mentions <history> for background context.",
    )
    test(
        "embedded <history> in overview: history parses correctly",
        _r.get("history") == "Real history entry.",
    )

    # overview content contains a closing tag from another section
    _cp2 = _tmp / "close.md"
    _cp2.write_text(
        "<overview>\nReferencing </history> in prose.\n</overview>\n\n"
        "<history>\nActual history.\n</history>\n",
        encoding="utf-8",
    )
    _r2 = parse_checkpoint_sections(_cp2)
    test(
        "embedded </history> in overview: overview content intact",
        "Referencing </history> in prose." in _r2.get("overview", ""),
    )
    test(
        "embedded </history> in overview: history parses correctly",
        _r2.get("history") == "Actual history.",
    )

    # Multiple embedded tags in one section's content
    _cp3 = _tmp / "multi.md"
    _cp3.write_text(
        "<overview>\nSee <work_done> and <next_steps> for details.\n</overview>\n\n"
        "<work_done>\nImplemented.\n</work_done>\n\n"
        "<next_steps>\nDeploy.\n</next_steps>\n",
        encoding="utf-8",
    )
    _r3 = parse_checkpoint_sections(_cp3)
    test(
        "multiple embedded tags: overview intact",
        "See <work_done> and <next_steps> for details." in _r3.get("overview", ""),
    )
    test(
        "multiple embedded tags: work_done correct",
        _r3.get("work_done") == "Implemented.",
    )
    test(
        "multiple embedded tags: next_steps correct",
        _r3.get("next_steps") == "Deploy.",
    )


# ── Tests: diff_sections ──────────────────────────────────────────────────────

section("diff_sections()")

# Identical sections → no changes
same_a = {"overview": "Same text.", "next_steps": "Do more."}
same_b = {"overview": "Same text.", "next_steps": "Do more."}
result_same = diff_sections(same_a, same_b)
test("identical → changed=False for overview", result_same["overview"]["changed"] is False)
test("identical → changed=False for next_steps", result_same["next_steps"]["changed"] is False)
test("identical → no diff lines", result_same["overview"]["lines"] == [])
for s in CHECKPOINT_SECTIONS:
    test(f"identical → all sections present in result ({s})", s in result_same)

# Modified section
a_mod = {"overview": "Old content."}
b_mod = {"overview": "New content."}
result_mod = diff_sections(a_mod, b_mod)
test("modified → changed=True", result_mod["overview"]["changed"] is True)
test("modified → added=False", result_mod["overview"]["added"] is False)
test("modified → removed=False", result_mod["overview"]["removed"] is False)
test("modified → diff lines present", len(result_mod["overview"]["lines"]) > 0)
test("modified → old text in 'a'", result_mod["overview"]["a"] == "Old content.")
test("modified → new text in 'b'", result_mod["overview"]["b"] == "New content.")

# Added section (present in b, absent in a)
a_add = {}
b_add = {"history": "First event happened."}
result_add = diff_sections(a_add, b_add)
test("added → changed=True", result_add["history"]["changed"] is True)
test("added → added=True", result_add["history"]["added"] is True)
test("added → removed=False", result_add["history"]["removed"] is False)

# Removed section (present in a, absent in b)
a_rem = {"work_done": "Wrote some code."}
b_rem = {}
result_rem = diff_sections(a_rem, b_rem)
test("removed → changed=True", result_rem["work_done"]["changed"] is True)
test("removed → removed=True", result_rem["work_done"]["removed"] is True)
test("removed → added=False", result_rem["work_done"]["added"] is False)

# Multiple changes in one diff
a_multi = {"overview": "Alpha.", "history": "Step 1."}
b_multi = {"overview": "Beta.", "next_steps": "Go further."}
result_multi = diff_sections(a_multi, b_multi)
test("multi: overview changed", result_multi["overview"]["changed"] is True)
test("multi: history removed", result_multi["history"]["removed"] is True)
test("multi: next_steps added", result_multi["next_steps"]["added"] is True)
test("multi: work_done unchanged (both empty)", result_multi["work_done"]["changed"] is False)


# ── Tests: format_diff_output ────────────────────────────────────────────────

section("format_diff_output()")

entry_a = {"seq": 1, "title": "Alpha", "file": "001-alpha.md"}
entry_b = {"seq": 2, "title": "Beta", "file": "002-beta.md"}

# No changes
diffs_no_change = diff_sections({"overview": "Same."}, {"overview": "Same."})
out_no_change = format_diff_output(entry_a, entry_b, diffs_no_change)
test("no-change: header present (seq a)", "001" in out_no_change)
test("no-change: header present (seq b)", "002" in out_no_change)
test("no-change: no changes message", "no section-level changes" in out_no_change)

# With change
diffs_change = diff_sections({"overview": "Old."}, {"overview": "New."})
out_change = format_diff_output(entry_a, entry_b, diffs_change)
test("changed: CHANGED label present", "CHANGED" in out_change)
test("changed: diff lines in output", "---" in out_change or "+++" in out_change or "-Old" in out_change)

# Added section
diffs_added = diff_sections({}, {"next_steps": "New steps."})
out_added = format_diff_output(entry_a, entry_b, diffs_added)
test("added: ADDED label present", "ADDED" in out_added)

# Removed section
diffs_removed = diff_sections({"history": "Past events."}, {})
out_removed = format_diff_output(entry_a, entry_b, diffs_removed)
test("removed: REMOVED label present", "REMOVED" in out_removed)

# show_unchanged=True includes unchanged sections that have content
diffs_mixed = diff_sections({"overview": "X.", "next_steps": "Y."}, {"overview": "Z.", "next_steps": "Y."})
out_show_unch = format_diff_output(entry_a, entry_b, diffs_mixed, show_unchanged=True)
test("show_unchanged: unchanged section noted", "(unchanged)" in out_show_unch)


# ── Tests: format_summary_output ─────────────────────────────────────────────

section("format_summary_output()")

entries = [
    {"seq": 1, "title": "Alpha", "file": "001-alpha.md"},
    {"seq": 2, "title": "Beta", "file": "002-beta.md"},
    {"seq": 3, "title": "Gamma", "file": "003-gamma.md"},
]
diffs_list = [
    diff_sections({"overview": "A"}, {"overview": "B"}),
    diff_sections({"overview": "B"}, {"overview": "B", "next_steps": "Added."}),
]
summary = format_summary_output(entries, diffs_list)
test("summary mentions checkpoint count", "3" in summary)
test("summary has Alpha entry", "Alpha" in summary)
test("summary has Beta entry", "Beta" in summary)
test("summary has Gamma entry", "Gamma" in summary)
test("summary shows changed sections for first pair", "overview" in summary)
test("summary shows added section for second pair", "next_steps" in summary)


# ── Tests: resolve_selector ───────────────────────────────────────────────────

section("resolve_selector() in diff module")

entries_r = [
    {"seq": 1, "title": "One", "file": "001.md"},
    {"seq": 3, "title": "Three", "file": "003.md"},
]
test("'latest' → seq 3", resolve_selector(entries_r, "latest")["seq"] == 3)
test("'first' → seq 1", resolve_selector(entries_r, "first")["seq"] == 1)
test("'3' → seq 3", resolve_selector(entries_r, "3")["seq"] == 3)
test("'2' → None (gap)", resolve_selector(entries_r, "2") is None)
test("empty list → None", resolve_selector([], "latest") is None)


# ── Tests: main() --from --to ────────────────────────────────────────────────

section("main() --from --to")

with tempfile.TemporaryDirectory() as td:
    tmp = Path(td)
    session_dir = make_session_dir(tmp, "diff-session", cwd=str(Path.cwd()))
    make_checkpoint(session_dir, 1, "Start", {"overview": "Initial work.", "next_steps": "Do more."})
    make_checkpoint(session_dir, 2, "Finish", {"overview": "Final work.", "history": "Done A and B.", "next_steps": "Deploy."})

    # Diff 1 → 2
    out = io.StringIO()
    with redirect_stdout(out):
        ret = main(["--session-dir", str(tmp), "--from", "1", "--to", "2"])
    test("--from 1 --to 2 returns 0", ret == 0)
    output = out.getvalue()
    test("diff output has from-title", "Start" in output)
    test("diff output has to-title", "Finish" in output)
    test("diff output shows overview change", "CHANGED" in output or "overview" in output.lower())

    # --from latest → requires --to
    err = io.StringIO()
    with redirect_stderr(err):
        ret_no_to = main(["--session-dir", str(tmp), "--from", "1"])
    test("--from without --to returns non-zero", ret_no_to != 0)
    test("--from without --to error message", "--to" in err.getvalue())

    # Diff same checkpoint → non-zero
    err2 = io.StringIO()
    with redirect_stderr(err2):
        ret_same = main(["--session-dir", str(tmp), "--from", "1", "--to", "1"])
    test("same checkpoint → non-zero", ret_same != 0)
    test("same checkpoint → error message", "itself" in err2.getvalue().lower())

    # Bad selector → non-zero
    err3 = io.StringIO()
    with redirect_stderr(err3):
        ret_bad = main(["--session-dir", str(tmp), "--from", "99", "--to", "latest"])
    test("bad --from selector → non-zero", ret_bad != 0)
    test("bad --from selector → error message", "not found" in err3.getvalue().lower() or "available" in err3.getvalue().lower())

    # 'first' and 'latest' selectors
    out4 = io.StringIO()
    with redirect_stdout(out4):
        ret4 = main(["--session-dir", str(tmp), "--from", "first", "--to", "latest"])
    test("--from first --to latest returns 0", ret4 == 0)

    # Reverse direction (higher to lower seq)
    out5 = io.StringIO()
    with redirect_stdout(out5):
        ret5 = main(["--session-dir", str(tmp), "--from", "2", "--to", "1"])
    test("--from 2 --to 1 (reverse) returns 0", ret5 == 0)


# ── Tests: main() --consecutive ──────────────────────────────────────────────

section("main() --consecutive")

with tempfile.TemporaryDirectory() as td:
    tmp = Path(td)
    session_dir = make_session_dir(tmp, "consec-session", cwd=str(Path.cwd()))
    make_checkpoint(session_dir, 1, "Step One", {"overview": "Phase 1."})
    make_checkpoint(session_dir, 2, "Step Two", {"overview": "Phase 2.", "history": "Completed phase 1."})
    make_checkpoint(session_dir, 3, "Step Three", {"overview": "Phase 3.", "next_steps": "Wrap up."})

    out = io.StringIO()
    with redirect_stdout(out):
        ret = main(["--session-dir", str(tmp), "--consecutive"])
    test("--consecutive returns 0", ret == 0)
    output = out.getvalue()
    # Should show two pairs: 1→2 and 2→3
    test("--consecutive shows first pair", "Step One" in output and "Step Two" in output)
    test("--consecutive shows second pair", "Step Three" in output)

    # Only 1 checkpoint → non-zero
    session_dir2 = make_session_dir(tmp, "single-session", cwd=str(Path.cwd()))
    make_checkpoint(session_dir2, 1, "Only One", {"overview": "Alone."})
    err2 = io.StringIO()
    with redirect_stderr(err2):
        ret2 = main(["--session-dir", str(tmp), "--session", "single-session", "--consecutive"])
    test("--consecutive with 1 checkpoint → non-zero", ret2 != 0)
    test("--consecutive with 1 checkpoint → error message", "2" in err2.getvalue())


# ── Tests: main() --summary ───────────────────────────────────────────────────

section("main() --summary")

with tempfile.TemporaryDirectory() as td:
    tmp = Path(td)
    session_dir = make_session_dir(tmp, "summary-session", cwd=str(Path.cwd()))
    make_checkpoint(session_dir, 1, "Alpha", {"overview": "Start here."})
    make_checkpoint(session_dir, 2, "Beta", {"overview": "Changed this.", "history": "New step."})
    make_checkpoint(session_dir, 3, "Gamma", {"overview": "Changed this.", "next_steps": "Done."})

    out = io.StringIO()
    with redirect_stdout(out):
        ret = main(["--session-dir", str(tmp), "--summary"])
    test("--summary returns 0", ret == 0)
    output = out.getvalue()
    test("--summary shows all checkpoints", all(t in output for t in ["Alpha", "Beta", "Gamma"]))
    test("--summary shows changed sections", "changed" in output.lower() or "↳" in output)


# ── Tests: no checkpoints → error ────────────────────────────────────────────

section("main() no checkpoints")

with tempfile.TemporaryDirectory() as td:
    tmp = Path(td)
    make_session_dir(tmp, "no-cp-session", cwd=str(Path.cwd()))

    err = io.StringIO()
    with redirect_stderr(err):
        ret = main(["--session-dir", str(tmp), "--from", "1", "--to", "2"])
    test("no checkpoints --from/--to → non-zero", ret != 0)
    test("no checkpoints error message", "no checkpoints" in err.getvalue().lower())

    err2 = io.StringIO()
    with redirect_stderr(err2):
        ret2 = main(["--session-dir", str(tmp), "--summary"])
    test("no checkpoints --summary → non-zero", ret2 != 0)

    err3 = io.StringIO()
    with redirect_stderr(err3):
        ret3 = main(["--session-dir", str(tmp), "--consecutive"])
    test("no checkpoints --consecutive → non-zero", ret3 != 0)


# ── Tests: no session → error ────────────────────────────────────────────────

section("main() no session")

with tempfile.TemporaryDirectory() as td:
    tmp = Path(td)
    err = io.StringIO()
    with redirect_stderr(err):
        ret = main(["--session-dir", str(tmp), "--from", "1", "--to", "2"])
    test("no session → non-zero", ret != 0)
    test("no session → error message", "session" in err.getvalue().lower())


# ── Tests: no args → help (returns 0) ────────────────────────────────────────

section("main() no args")

with tempfile.TemporaryDirectory() as td:
    tmp = Path(td)
    make_session_dir(tmp, "noop-diff-session", cwd=str(Path.cwd()))
    out = io.StringIO()
    with redirect_stdout(out):
        ret = main(["--session-dir", str(tmp)])
    test("no args returns 0", ret == 0)


# ── Tests: --show-unchanged flag ─────────────────────────────────────────────

section("main() --show-unchanged flag")

with tempfile.TemporaryDirectory() as td:
    tmp = Path(td)
    session_dir = make_session_dir(tmp, "unch-session", cwd=str(Path.cwd()))
    make_checkpoint(session_dir, 1, "Before", {"overview": "Same overview.", "next_steps": "Old steps."})
    make_checkpoint(session_dir, 2, "After", {"overview": "Same overview.", "next_steps": "New steps."})

    out_normal = io.StringIO()
    with redirect_stdout(out_normal):
        main(["--session-dir", str(tmp), "--from", "1", "--to", "2"])

    out_unch = io.StringIO()
    with redirect_stdout(out_unch):
        main(["--session-dir", str(tmp), "--from", "1", "--to", "2", "--show-unchanged"])

    test("--show-unchanged adds unchanged label", "(unchanged)" in out_unch.getvalue())
    test("without --show-unchanged no unchanged label", "(unchanged)" not in out_normal.getvalue())


# ── Tests: SESSION_STATE env var ─────────────────────────────────────────────

section("SESSION_STATE env var")

import importlib.util as _ilu


def _reload_diff_module(env_overrides: dict):
    old_env = {k: os.environ.get(k) for k in env_overrides}
    for k, v in env_overrides.items():
        if v is None:
            os.environ.pop(k, None)
        else:
            os.environ[k] = v
    try:
        spec = _ilu.spec_from_file_location(
            "_cp_diff_fresh", TOOLS_DIR / "checkpoint-diff.py"
        )
        mod = _ilu.module_from_spec(spec)
        spec.loader.exec_module(mod)
        return mod
    finally:
        for k, v in old_env.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v


mod_default = _reload_diff_module({"COPILOT_SESSION_STATE": None})
expected_default = Path.home() / ".copilot" / "session-state"
test(
    "SESSION_STATE defaults to ~/.copilot/session-state",
    mod_default.SESSION_STATE == expected_default,
    f"got {mod_default.SESSION_STATE}",
)

mod_custom = _reload_diff_module({"COPILOT_SESSION_STATE": "/custom/diff/dir"})
test(
    "SESSION_STATE uses COPILOT_SESSION_STATE when set",
    mod_custom.SESSION_STATE == Path("/custom/diff/dir"),
    f"got {mod_custom.SESSION_STATE}",
)


# ── Summary ──────────────────────────────────────────────────────────────────

total = _passed + _failed
print(f"\n{'─' * 40}")
print(f"Results: {_passed}/{total} passed", end="")
if _failed:
    print(f", {_failed} FAILED")
    sys.exit(1)
else:
    print(" ✓")
    sys.exit(0)
