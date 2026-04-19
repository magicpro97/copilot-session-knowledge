#!/usr/bin/env python3
"""
test_checkpoint_restore.py — Isolated tests for checkpoint-restore.py

Run with:
    python3 test_checkpoint_restore.py
"""

import io
import json
import os
import sys
import tempfile
import time
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

TOOLS_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(TOOLS_DIR))

import importlib.util

_spec = importlib.util.spec_from_file_location(
    "checkpoint_restore", TOOLS_DIR / "checkpoint-restore.py"
)
_mod = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_mod)

parse_index = _mod.parse_index
parse_checkpoint_sections = _mod.parse_checkpoint_sections
resolve_selector = _mod.resolve_selector
format_checkpoint_text = _mod.format_checkpoint_text
format_checkpoint_md = _mod.format_checkpoint_md
format_checkpoint_json = _mod.format_checkpoint_json
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
    """Write a checkpoint file + update index.md the same way checkpoint-save.py does."""
    cp_dir = session_dir / "checkpoints"
    cp_dir.mkdir(parents=True, exist_ok=True)

    # Build content
    parts = []
    for s in CHECKPOINT_SECTIONS:
        content = sections.get(s, "").strip()
        if content:
            parts.append(f"<{s}>\n{content}\n</{s}>")
    if not parts:
        parts.append(f"<overview>\n{title}\n</overview>")
    file_content = "\n\n".join(parts) + "\n"

    slug = title.lower()
    import re
    slug = re.sub(r"[^a-z0-9]+", "-", slug).strip("-")[:35].rstrip("-")
    fname = f"{seq:03d}-{slug}.md"
    cp_path = cp_dir / fname
    cp_path.write_text(file_content, encoding="utf-8")

    # Update index
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


# ── Tests: parse_checkpoint_sections ─────────────────────────────────────────

section("parse_checkpoint_sections()")

with tempfile.TemporaryDirectory() as td:
    tmp = Path(td)

    # Non-existent file → empty dict
    test("missing file → {}", parse_checkpoint_sections(tmp / "nonexistent.md") == {})

    # File with two sections
    cp = tmp / "cp1.md"
    cp.write_text(
        "<overview>\nDid stuff.\n</overview>\n\n<next_steps>\nDeploy.\n</next_steps>\n",
        encoding="utf-8",
    )
    result = parse_checkpoint_sections(cp)
    test("overview parsed", result.get("overview") == "Did stuff.")
    test("next_steps parsed", result.get("next_steps") == "Deploy.")
    test("absent section not in dict", "history" not in result)

    # All sections present
    all_content = "\n\n".join(
        f"<{s}>\nContent for {s}.\n</{s}>" for s in CHECKPOINT_SECTIONS
    )
    cp_all = tmp / "cp_all.md"
    cp_all.write_text(all_content, encoding="utf-8")
    result_all = parse_checkpoint_sections(cp_all)
    for s in CHECKPOINT_SECTIONS:
        test(f"all-sections: '{s}' parsed", result_all.get(s) == f"Content for {s}.")

    # Section with multi-line content
    cp_multi = tmp / "cp_multi.md"
    cp_multi.write_text(
        "<overview>\nLine 1.\nLine 2.\nLine 3.\n</overview>\n",
        encoding="utf-8",
    )
    result_multi = parse_checkpoint_sections(cp_multi)
    test("multi-line content preserved", "Line 1." in result_multi["overview"])
    test("multi-line all lines present", "Line 3." in result_multi["overview"])

    # Empty file → empty dict
    cp_empty = tmp / "cp_empty.md"
    cp_empty.write_text("", encoding="utf-8")
    test("empty file → {}", parse_checkpoint_sections(cp_empty) == {})


# ── Regression: embedded tag text cannot bleed across section boundaries ──────

section("parse_checkpoint_sections() — embedded tag boundary regression")

with tempfile.TemporaryDirectory() as td:
    tmp = Path(td)

    # overview content contains literal <history> open tag; history must still parse
    cp_bleed = tmp / "cp_bleed.md"
    cp_bleed.write_text(
        "<overview>\nThis mentions <history> for background context.\n</overview>\n\n"
        "<history>\nReal history entry.\n</history>\n",
        encoding="utf-8",
    )
    result_bleed = parse_checkpoint_sections(cp_bleed)
    test(
        "embedded <history> in overview: overview content is correct",
        result_bleed.get("overview") == "This mentions <history> for background context.",
    )
    test(
        "embedded <history> in overview: history still parses correctly",
        result_bleed.get("history") == "Real history entry.",
    )

    # overview content contains a closing tag from another section
    cp_close = tmp / "cp_close.md"
    cp_close.write_text(
        "<overview>\nReferencing </history> closing tag in prose.\n</overview>\n\n"
        "<history>\nActual history.\n</history>\n",
        encoding="utf-8",
    )
    result_close = parse_checkpoint_sections(cp_close)
    test(
        "embedded </history> in overview: overview content is correct",
        "Referencing </history> closing tag in prose." in result_close.get("overview", ""),
    )
    test(
        "embedded </history> in overview: history parses correctly",
        result_close.get("history") == "Actual history.",
    )

    # Multiple embedded tags from different sections in one section's content
    cp_multi_embed = tmp / "cp_multi_embed.md"
    cp_multi_embed.write_text(
        "<overview>\nSee <work_done> and <next_steps> sections below.\n</overview>\n\n"
        "<work_done>\nImplemented the feature.\n</work_done>\n\n"
        "<next_steps>\nDeploy it.\n</next_steps>\n",
        encoding="utf-8",
    )
    result_multi_embed = parse_checkpoint_sections(cp_multi_embed)
    test(
        "multiple embedded tags: overview content intact",
        "See <work_done> and <next_steps> sections below." in result_multi_embed.get("overview", ""),
    )
    test(
        "multiple embedded tags: work_done parses correctly",
        result_multi_embed.get("work_done") == "Implemented the feature.",
    )
    test(
        "multiple embedded tags: next_steps parses correctly",
        result_multi_embed.get("next_steps") == "Deploy it.",
    )

    # All six sections normal — existing behavior preserved
    normal_text = "\n\n".join(
        f"<{s}>\nNormal content for {s}.\n</{s}>" for s in CHECKPOINT_SECTIONS
    )
    cp_normal = tmp / "cp_normal.md"
    cp_normal.write_text(normal_text, encoding="utf-8")
    result_normal = parse_checkpoint_sections(cp_normal)
    for s in CHECKPOINT_SECTIONS:
        test(
            f"normal file: '{s}' unaffected by new parser",
            result_normal.get(s) == f"Normal content for {s}.",
        )


# ── Tests: resolve_selector ───────────────────────────────────────────────────

section("resolve_selector()")

entries = [
    {"seq": 1, "title": "First", "file": "001-first.md"},
    {"seq": 2, "title": "Second", "file": "002-second.md"},
    {"seq": 5, "title": "Fifth", "file": "005-fifth.md"},
]

test("'latest' → highest seq", resolve_selector(entries, "latest")["seq"] == 5)
test("'first' → lowest seq", resolve_selector(entries, "first")["seq"] == 1)
test("LATEST (uppercase) works", resolve_selector(entries, "LATEST")["seq"] == 5)
test("'2' → seq 2", resolve_selector(entries, "2")["seq"] == 2)
test("'5' → seq 5", resolve_selector(entries, "5")["seq"] == 5)
test("'99' → None (not found)", resolve_selector(entries, "99") is None)
test("'abc' → None (non-int)", resolve_selector(entries, "abc") is None)
test("empty entries → None", resolve_selector([], "latest") is None)
test("single entry 'latest' == 'first'", resolve_selector(entries[:1], "latest")["seq"] == 1)
test("single entry 'first' == 'latest'", resolve_selector(entries[:1], "first")["seq"] == 1)


# ── Tests: format_checkpoint_text ────────────────────────────────────────────

section("format_checkpoint_text()")

entry = {"seq": 3, "title": "My Checkpoint", "file": "003-my-checkpoint.md"}
sections_data = {"overview": "Did great work.", "next_steps": "Deploy it."}
text_out = format_checkpoint_text(entry, sections_data)

test("header contains seq", "[003]" in text_out)
test("header contains title", "My Checkpoint" in text_out)
test("overview section heading", "Overview" in text_out)
test("overview content", "Did great work." in text_out)
test("next_steps heading", "Next Steps" in text_out)
test("next_steps content", "Deploy it." in text_out)
test("absent section not in output", "History" not in text_out)


# ── Tests: format_checkpoint_md ──────────────────────────────────────────────

section("format_checkpoint_md()")

md_out = format_checkpoint_md(entry, sections_data)
test("md has overview XML tags", "<overview>" in md_out and "</overview>" in md_out)
test("md has next_steps XML tags", "<next_steps>" in md_out and "</next_steps>" in md_out)
test("md content present", "Did great work." in md_out)
test("absent section has no XML tag", "<history>" not in md_out)
test("title in md header", "My Checkpoint" in md_out)


# ── Tests: format_checkpoint_json ────────────────────────────────────────────

section("format_checkpoint_json()")

json_out = format_checkpoint_json(entry, sections_data)
parsed_json = json.loads(json_out)
test("json has seq", parsed_json["seq"] == 3)
test("json has title", parsed_json["title"] == "My Checkpoint")
test("json has file", parsed_json["file"] == "003-my-checkpoint.md")
test("json has sections dict", isinstance(parsed_json["sections"], dict))
test("json sections overview", parsed_json["sections"]["overview"] == "Did great work.")
test("json sections next_steps", parsed_json["sections"]["next_steps"] == "Deploy it.")


# ── Tests: main() --list ──────────────────────────────────────────────────────

section("main() --list")

with tempfile.TemporaryDirectory() as td:
    tmp = Path(td)
    session_dir = make_session_dir(tmp, "list-session", cwd=str(Path.cwd()))
    make_checkpoint(session_dir, 1, "Alpha Work", {"overview": "Did alpha."})
    make_checkpoint(session_dir, 2, "Beta Work", {"overview": "Did beta."})

    out = io.StringIO()
    with redirect_stdout(out):
        ret = main(["--session-dir", str(tmp), "--list"])
    test("--list returns 0", ret == 0)
    output = out.getvalue()
    test("--list shows session name", "list-session" in output)
    test("--list shows first entry", "Alpha Work" in output)
    test("--list shows second entry", "Beta Work" in output)

    # No checkpoints → still returns 0
    session_dir2 = make_session_dir(tmp, "empty-session", cwd=str(Path.cwd()))
    out2 = io.StringIO()
    with redirect_stdout(out2):
        ret2 = main(["--session-dir", str(tmp), "--session", "empty-session", "--list"])
    test("--list empty session returns 0", ret2 == 0)
    test("--list empty session message", "No checkpoints" in out2.getvalue())


# ── Tests: main() --show ──────────────────────────────────────────────────────

section("main() --show")

with tempfile.TemporaryDirectory() as td:
    tmp = Path(td)
    session_dir = make_session_dir(tmp, "show-session", cwd=str(Path.cwd()))
    make_checkpoint(session_dir, 1, "Start", {"overview": "Beginning.", "next_steps": "Continue."})
    make_checkpoint(session_dir, 2, "Middle", {"overview": "Midpoint.", "history": "Did A and B."})

    out = io.StringIO()
    with redirect_stdout(out):
        ret = main(["--session-dir", str(tmp), "--show", "1"])
    test("--show 1 returns 0", ret == 0)
    output = out.getvalue()
    test("--show 1 has title", "Start" in output)
    test("--show 1 has overview content", "Beginning." in output)

    out2 = io.StringIO()
    with redirect_stdout(out2):
        ret2 = main(["--session-dir", str(tmp), "--show", "latest"])
    test("--show latest returns 0", ret2 == 0)
    test("--show latest has most recent title", "Middle" in out2.getvalue())

    out3 = io.StringIO()
    with redirect_stdout(out3):
        ret3 = main(["--session-dir", str(tmp), "--show", "first"])
    test("--show first returns 0", ret3 == 0)
    test("--show first has oldest title", "Start" in out3.getvalue())

    # Bad selector
    err4 = io.StringIO()
    with redirect_stderr(err4):
        ret4 = main(["--session-dir", str(tmp), "--show", "99"])
    test("--show 99 returns non-zero", ret4 != 0)
    test("--show 99 error message", "not found" in err4.getvalue().lower() or "available" in err4.getvalue().lower())


# ── Tests: main() --export formats ───────────────────────────────────────────

section("main() --export")

with tempfile.TemporaryDirectory() as td:
    tmp = Path(td)
    session_dir = make_session_dir(tmp, "export-session", cwd=str(Path.cwd()))
    make_checkpoint(session_dir, 1, "Export Test", {"overview": "Export content.", "next_steps": "Ship it."})

    # --export with --format text (default)
    out_text = io.StringIO()
    with redirect_stdout(out_text):
        ret = main(["--session-dir", str(tmp), "--export", "latest"])
    test("--export default (text) returns 0", ret == 0)
    test("--export text has content", "Export content." in out_text.getvalue())

    # --export with --format json
    out_json = io.StringIO()
    with redirect_stdout(out_json):
        ret_json = main(["--session-dir", str(tmp), "--export", "1", "--format", "json"])
    test("--export json returns 0", ret_json == 0)
    try:
        parsed = json.loads(out_json.getvalue())
        test("--export json parses correctly", parsed["title"] == "Export Test")
        test("--export json has sections", "sections" in parsed)
    except json.JSONDecodeError as e:
        test("--export json is valid JSON", False, str(e))

    # --export with --format md
    out_md = io.StringIO()
    with redirect_stdout(out_md):
        ret_md = main(["--session-dir", str(tmp), "--export", "1", "--format", "md"])
    test("--export md returns 0", ret_md == 0)
    test("--export md has XML tags", "<overview>" in out_md.getvalue())
    test("--export md has content", "Export content." in out_md.getvalue())


# ── Tests: session detection edge cases ──────────────────────────────────────

section("main() session detection edge cases")

with tempfile.TemporaryDirectory() as td:
    tmp = Path(td)

    # No session → non-zero
    err = io.StringIO()
    with redirect_stderr(err):
        ret = main(["--session-dir", str(tmp), "--list"])
    test("no session → non-zero", ret != 0)
    test("no session → error message", "session" in err.getvalue().lower())

    # Explicit bad session ID → non-zero
    err2 = io.StringIO()
    with redirect_stderr(err2):
        ret2 = main(["--session-dir", str(tmp), "--session", "ghost-session", "--list"])
    test("bad session ID → non-zero", ret2 != 0)

    # COPILOT_SESSION_ID env var respected
    session_dir = make_session_dir(tmp, "env-session", cwd="/other/path")
    make_checkpoint(session_dir, 1, "Env Test", {"overview": "Via env var."})
    os.environ["COPILOT_SESSION_ID"] = "env-session"
    out_env = io.StringIO()
    with redirect_stdout(out_env):
        ret_env = main(["--session-dir", str(tmp), "--list"])
    del os.environ["COPILOT_SESSION_ID"]
    test("COPILOT_SESSION_ID env var used", "Env Test" in out_env.getvalue())

    # CWD-matching session preferred
    cwd = str(Path.cwd())
    make_session_dir(tmp, "other-session", cwd="/completely/different")
    cwd_session = make_session_dir(tmp, "cwd-session", cwd=cwd)
    make_checkpoint(cwd_session, 1, "CWD Match", {"overview": "Matched by cwd."})
    out_cwd = io.StringIO()
    with redirect_stdout(out_cwd):
        ret_cwd = main(["--session-dir", str(tmp), "--list"])
    test("cwd-matching session preferred", "CWD Match" in out_cwd.getvalue())


# ── Tests: missing checkpoint file (index entry but no file) ─────────────────

section("Missing checkpoint file handling")

with tempfile.TemporaryDirectory() as td:
    tmp = Path(td)
    session_dir = make_session_dir(tmp, "missing-file-session", cwd=str(Path.cwd()))
    cp_dir = session_dir / "checkpoints"
    cp_dir.mkdir(parents=True)

    # Write index with a reference to a non-existent file
    (cp_dir / "index.md").write_text(
        "# Checkpoint History\n\n"
        "| # | Title | File |\n|---|-------|------|\n"
        "| 1 | Ghost Checkpoint | 001-ghost.md |\n",
        encoding="utf-8",
    )

    # --show should return non-zero (file not found → sections empty is ok, but show still works)
    # parse_checkpoint_sections returns {} for missing file → format still renders header
    out = io.StringIO()
    with redirect_stdout(out):
        ret = main(["--session-dir", str(tmp), "--show", "1"])
    test("show with missing cp file returns 0", ret == 0)
    test("show with missing cp file has title from index", "Ghost Checkpoint" in out.getvalue())

    # parse_checkpoint_sections returns {} for missing file
    missing_path = cp_dir / "001-ghost.md"
    test("parse_checkpoint_sections missing file → {}", parse_checkpoint_sections(missing_path) == {})


# ── Tests: no args → help (returns 0) ────────────────────────────────────────

section("main() no args")

with tempfile.TemporaryDirectory() as td:
    tmp = Path(td)
    make_session_dir(tmp, "noop-session", cwd=str(Path.cwd()))
    out = io.StringIO()
    with redirect_stdout(out):
        ret = main(["--session-dir", str(tmp)])
    test("no args returns 0", ret == 0)


# ── Tests: SESSION_STATE env var ─────────────────────────────────────────────

section("SESSION_STATE env var")

import importlib.util as _ilu


def _reload_module(env_overrides: dict):
    old_env = {k: os.environ.get(k) for k in env_overrides}
    for k, v in env_overrides.items():
        if v is None:
            os.environ.pop(k, None)
        else:
            os.environ[k] = v
    try:
        spec = _ilu.spec_from_file_location(
            "_cp_restore_fresh", TOOLS_DIR / "checkpoint-restore.py"
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


mod_default = _reload_module({"COPILOT_SESSION_STATE": None})
expected_default = Path.home() / ".copilot" / "session-state"
test(
    "SESSION_STATE defaults to ~/.copilot/session-state",
    mod_default.SESSION_STATE == expected_default,
    f"got {mod_default.SESSION_STATE}",
)

mod_custom = _reload_module({"COPILOT_SESSION_STATE": "/custom/restore/dir"})
test(
    "SESSION_STATE uses COPILOT_SESSION_STATE when set",
    mod_custom.SESSION_STATE == Path("/custom/restore/dir"),
    f"got {mod_custom.SESSION_STATE}",
)

mod_empty = _reload_module({"COPILOT_SESSION_STATE": ""})
test(
    "SESSION_STATE defaults when env var is empty string",
    mod_empty.SESSION_STATE == expected_default,
    f"got {mod_empty.SESSION_STATE}",
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
