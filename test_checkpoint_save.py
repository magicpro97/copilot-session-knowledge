#!/usr/bin/env python3
"""
test_checkpoint_save.py — Isolated tests for checkpoint-save.py

Run with:
    python3 test_checkpoint_save.py
"""
import os
import sys
import textwrap
import traceback
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
    "checkpoint_save", TOOLS_DIR / "checkpoint-save.py"
)
_mod = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_mod)

slug = _mod.slug
next_filename = _mod.next_filename
parse_index = _mod.parse_index
write_index = _mod.write_index
build_checkpoint_content = _mod.build_checkpoint_content
save_checkpoint = _mod.save_checkpoint
detect_session = _mod.detect_session
main = _mod.main
INDEX_HEADER = _mod.INDEX_HEADER
CHECKPOINT_SECTIONS = _mod.CHECKPOINT_SECTIONS


# ── Fixtures ─────────────────────────────────────────────────────────────────

def make_session_dir(tmp: Path, session_id: str = "test-session-001", cwd: str | None = None) -> Path:
    """Create a minimal session directory with workspace.yaml."""
    d = tmp / session_id
    d.mkdir(parents=True, exist_ok=True)
    workspace = d / "workspace.yaml"
    workspace.write_text(
        f"id: {session_id}\ncwd: {cwd or str(tmp)}\ngit_root: {cwd or str(tmp)}\n",
        encoding="utf-8",
    )
    return d


# ── Tests: slug ───────────────────────────────────────────────────────────────

section("slug()")
test("basic slug", slug("Hello World") == "hello-world")
test("special chars removed", slug("Fix: bug #42!") == "fix-bug-42")
test("max length", len(slug("a" * 100)) <= 35)
test("leading/trailing hyphens stripped", not slug("  hi  ").startswith("-"))
test("lowercase", slug("ABC") == "abc")
test("already lowercase", slug("hello") == "hello")


# ── Tests: next_filename ──────────────────────────────────────────────────────

section("next_filename()")
test("first entry = 001", next_filename([], "My Title") == (1, "001-my-title.md"))
test("second entry = 002", next_filename([{"seq": 1, "title": "x", "file": "001-x.md"}], "Y")[0] == 2)
test("seq is zero-padded", next_filename([], "test")[1].startswith("001-"))
test("filename ends in .md", next_filename([], "test")[1].endswith(".md"))


# ── Tests: build_checkpoint_content ──────────────────────────────────────────

section("build_checkpoint_content()")
content = build_checkpoint_content("My Title", {"overview": "Great work.", "next_steps": "Deploy."})
test("contains overview tag", "<overview>" in content and "</overview>" in content)
test("contains next_steps tag", "<next_steps>" in content and "</next_steps>" in content)
test("does NOT contain empty tags", "<history>" not in content)
test("overview content present", "Great work." in content)
test("next_steps content present", "Deploy." in content)

# All sections populated
all_sections = {s: f"Content for {s}" for s in CHECKPOINT_SECTIONS}
full_content = build_checkpoint_content("Full", all_sections)
for s in CHECKPOINT_SECTIONS:
    test(f"section '{s}' in full content", f"<{s}>" in full_content)

# Empty sections dict → at least overview fallback
empty_content = build_checkpoint_content("Fallback Test", {})
test("empty sections → fallback overview", "<overview>" in empty_content)


# ── Tests: parse_index / write_index ─────────────────────────────────────────

section("parse_index() / write_index()")
import tempfile

with tempfile.TemporaryDirectory() as td:
    tmp = Path(td)
    cp_dir = tmp / "checkpoints"
    cp_dir.mkdir()
    idx = cp_dir / "index.md"

    # write + parse round-trip
    entries = [
        {"seq": 1, "title": "First checkpoint", "file": "001-first-checkpoint.md"},
        {"seq": 2, "title": "Second one", "file": "002-second-one.md"},
    ]
    write_index(idx, entries)
    parsed = parse_index(idx)
    test("round-trip: count matches", len(parsed) == 2)
    test("round-trip: seq preserved", parsed[0]["seq"] == 1 and parsed[1]["seq"] == 2)
    test("round-trip: title preserved", parsed[0]["title"] == "First checkpoint")
    test("round-trip: file preserved", parsed[1]["file"] == "002-second-one.md")

    # parse on missing file → empty list
    missing = cp_dir / "nonexistent.md"
    test("parse missing file → []", parse_index(missing) == [])


# ── Tests: save_checkpoint ────────────────────────────────────────────────────

section("save_checkpoint()")

with tempfile.TemporaryDirectory() as td:
    tmp = Path(td)
    session_dir = tmp / "my-session"
    session_dir.mkdir()

    cp_path = save_checkpoint(
        session_dir,
        title="My First Checkpoint",
        sections={"overview": "Did some work.", "next_steps": "Keep going."},
    )

    test("returns a Path", isinstance(cp_path, Path))
    test("file was created", cp_path.exists())
    test("file is in checkpoints/", cp_path.parent.name == "checkpoints")
    test("filename starts with 001-", cp_path.name.startswith("001-"))

    content = cp_path.read_text(encoding="utf-8")
    test("overview tag present", "<overview>" in content)
    test("next_steps tag present", "<next_steps>" in content)
    test("overview content correct", "Did some work." in content)

    # Index was updated
    idx_path = session_dir / "checkpoints" / "index.md"
    test("index.md was created", idx_path.exists())
    parsed = parse_index(idx_path)
    test("index has one entry", len(parsed) == 1)
    test("index title matches", parsed[0]["title"] == "My First Checkpoint")

    # Second checkpoint increments seq
    cp2 = save_checkpoint(
        session_dir,
        title="Second Checkpoint",
        sections={"overview": "More work."},
    )
    test("second cp seq=2", cp2.name.startswith("002-"))
    parsed2 = parse_index(idx_path)
    test("index now has two entries", len(parsed2) == 2)


# ── Tests: save_checkpoint dry_run ───────────────────────────────────────────

section("save_checkpoint() dry-run")

with tempfile.TemporaryDirectory() as td:
    tmp = Path(td)
    session_dir = tmp / "dry-session"
    session_dir.mkdir()

    import io
    from contextlib import redirect_stdout
    out = io.StringIO()
    with redirect_stdout(out):
        cp_path = save_checkpoint(
            session_dir,
            title="Dry Run Test",
            sections={"overview": "Test dry run."},
            dry_run=True,
        )

    cp_dir = session_dir / "checkpoints"
    test("dry-run: no checkpoint file created", not cp_path.exists())
    test("dry-run: no index.md created", not (cp_dir / "index.md").exists())
    output = out.getvalue()
    test("dry-run: prints would-write message", "[dry-run]" in output)


# ── Tests: detect_session ────────────────────────────────────────────────────

section("detect_session()")

with tempfile.TemporaryDirectory() as td:
    tmp = Path(td)

    # No sessions → None
    test("empty dir → None", detect_session(tmp) is None)

    # Session matching cwd
    cwd = str(Path.cwd())
    s1 = make_session_dir(tmp, "session-a", cwd=cwd)
    result = detect_session(tmp)
    test("cwd-matching session found", result is not None and result.name == "session-a")

    # Explicit session ID override
    s2 = make_session_dir(tmp, "session-b", cwd="/some/other/path")
    result_explicit = detect_session(tmp, session_id="session-b")
    test("explicit session ID respected", result_explicit is not None and result_explicit.name == "session-b")

    # Non-existent explicit ID → None
    result_bad = detect_session(tmp, session_id="nonexistent-999")
    test("nonexistent session ID → None", result_bad is None)

    # COPILOT_SESSION_ID env var
    os.environ["COPILOT_SESSION_ID"] = "session-b"
    result_env = detect_session(tmp)
    test("env var COPILOT_SESSION_ID respected", result_env is not None and result_env.name == "session-b")
    del os.environ["COPILOT_SESSION_ID"]


# ── Tests: main() CLI ─────────────────────────────────────────────────────────

section("main() CLI integration")

with tempfile.TemporaryDirectory() as td:
    tmp = Path(td)
    session_dir = make_session_dir(tmp, "cli-session", cwd=str(Path.cwd()))

    # Run main with --title and --overview using session-dir override
    ret = main([
        "--session-dir", str(tmp),
        "--title", "CLI Test Checkpoint",
        "--overview", "Integration test.",
        "--next_steps", "Verify tests pass.",
    ])
    test("main() returns 0 on success", ret == 0)

    cp_dir = session_dir / "checkpoints"
    test("checkpoints dir created", cp_dir.is_dir())
    idx = parse_index(cp_dir / "index.md")
    test("index has CLI entry", any(e["title"] == "CLI Test Checkpoint" for e in idx))

    # --list mode
    import io
    from contextlib import redirect_stdout
    out = io.StringIO()
    with redirect_stdout(out):
        ret_list = main(["--session-dir", str(tmp), "--list"])
    test("--list returns 0", ret_list == 0)
    test("--list shows entry", "CLI Test Checkpoint" in out.getvalue())

    # Missing --title without --list → non-zero
    import contextlib
    with contextlib.suppress(SystemExit):
        ret_no_title = main(["--session-dir", str(tmp)])
    # argparse calls sys.exit(2) on error; test by catching it
    try:
        main(["--session-dir", str(tmp)])
        test("missing --title → error", False, "Should have raised SystemExit")
    except SystemExit as e:
        test("missing --title → SystemExit", e.code != 0)

    # --dry-run does not write
    with tempfile.TemporaryDirectory() as td2:
        tmp2 = Path(td2)
        make_session_dir(tmp2, "dry-cli-session", cwd=str(Path.cwd()))
        out2 = io.StringIO()
        with redirect_stdout(out2):
            ret_dry = main([
                "--session-dir", str(tmp2),
                "--title", "Dry CLI Test",
                "--overview", "Should not write.",
                "--dry-run",
            ])
        test("--dry-run returns 0", ret_dry == 0)
        # no checkpoint file should exist
        dry_cp_dir = tmp2 / "dry-cli-session" / "checkpoints"
        cp_files = [f for f in dry_cp_dir.iterdir() if f.name != "index.md"] if dry_cp_dir.exists() else []
        test("--dry-run: no checkpoint files created", len(cp_files) == 0)


# ── Tests: title length validation ───────────────────────────────────────────

section("Input validation")

with tempfile.TemporaryDirectory() as td:
    tmp = Path(td)
    make_session_dir(tmp, "val-session", cwd=str(Path.cwd()))
    long_title = "x" * 201
    ret = main(["--session-dir", str(tmp), "--title", long_title])
    test("title > 200 chars → non-zero return", ret != 0)


# ── Tests: SESSION_STATE default path ────────────────────────────────────────

section("SESSION_STATE default path")

import importlib.util as _ilu

def _load_fresh_module(env_overrides: dict) -> object:
    """Re-exec checkpoint-save in an isolated environment with given env overrides."""
    old_env = {k: os.environ.get(k) for k in env_overrides}
    for k, v in env_overrides.items():
        if v is None:
            os.environ.pop(k, None)
        else:
            os.environ[k] = v
    try:
        spec = _ilu.spec_from_file_location(
            "_cp_save_fresh", TOOLS_DIR / "checkpoint-save.py"
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

# When env var is unset → should be ~/.copilot/session-state
mod_default = _load_fresh_module({"COPILOT_SESSION_STATE": None})
expected_default = Path.home() / ".copilot" / "session-state"
test(
    "SESSION_STATE defaults to ~/.copilot/session-state when env var unset",
    mod_default.SESSION_STATE == expected_default,
    f"got {mod_default.SESSION_STATE}",
)

# When env var is set → should use that path
mod_custom = _load_fresh_module({"COPILOT_SESSION_STATE": "/custom/state/dir"})
test(
    "SESSION_STATE uses COPILOT_SESSION_STATE env var when set",
    mod_custom.SESSION_STATE == Path("/custom/state/dir"),
    f"got {mod_custom.SESSION_STATE}",
)

# Env var empty string → should still default (not use cwd)
mod_empty = _load_fresh_module({"COPILOT_SESSION_STATE": ""})
test(
    "SESSION_STATE defaults when env var is empty string (not cwd)",
    mod_empty.SESSION_STATE == expected_default,
    f"got {mod_empty.SESSION_STATE}",
)


# ── Tests: exact cwd matching ─────────────────────────────────────────────────

section("detect_session() exact cwd matching")

with tempfile.TemporaryDirectory() as td:
    tmp = Path(td)
    cwd = str(Path.cwd())

    # Create a session whose cwd is a *parent* of the real cwd (substring match
    # would be incorrect here if parent contains cwd as a prefix substring).
    # More importantly: create a session whose cwd is an extension of the real
    # cwd (the old `cwd in line` check would still match it incorrectly).
    extended_cwd = cwd + "-extra-suffix"
    s_extended = make_session_dir(tmp, "session-extended", cwd=extended_cwd)
    s_exact = make_session_dir(tmp, "session-exact", cwd=cwd)

    # Touch s_extended to make it more recently modified so it wins by mtime if
    # matching is broken.
    import time
    time.sleep(0.01)
    (s_extended / "workspace.yaml").touch()

    result = detect_session(tmp)
    test(
        "exact cwd match preferred over substring match",
        result is not None and result.name == "session-exact",
        f"got {result.name if result else None}",
    )

    # Session with cwd that is a sub-path of the real cwd should NOT match
    with tempfile.TemporaryDirectory() as td2:
        tmp2 = Path(td2)
        sub_cwd = cwd + "/child/path"
        make_session_dir(tmp2, "session-subpath", cwd=sub_cwd)
        result2 = detect_session(tmp2)
        # sub_cwd != cwd, so no cwd match → falls back to best_any
        if result2:
            test(
                "session with sub-path cwd does not exact-match current cwd",
                result2.name == "session-subpath",  # fallback: only one session
            )
        else:
            test("detect_session returns something (fallback)", False, "returned None")




# ── Tests: session-end.py SESSION_STATE env-var consistency ──────────────────
#
# Regression guard: session-end.py must respect COPILOT_SESSION_STATE the same
# way checkpoint-save.py does.  Previously it hardcoded ~/.copilot/session-state,
# which caused false checkpoint reminders when a custom path was used.

section("session-end.py: COPILOT_SESSION_STATE consistency")

import importlib.util as _se_ilu


def _load_session_end(env_overrides: dict) -> object:
    """Re-exec session-end.py in an isolated env with given overrides."""
    old_env = {k: os.environ.get(k) for k in env_overrides}
    for k, v in env_overrides.items():
        if v is None:
            os.environ.pop(k, None)
        else:
            os.environ[k] = v
    try:
        spec = _se_ilu.spec_from_file_location(
            "_session_end_fresh",
            TOOLS_DIR / "hooks" / "session-end.py",
        )
        mod = _se_ilu.module_from_spec(spec)
        spec.loader.exec_module(mod)
        return mod
    finally:
        for k, v in old_env.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v


# Default path when env var is unset
se_default = _load_session_end({"COPILOT_SESSION_STATE": None})
test(
    "session-end SESSION_STATE defaults to ~/.copilot/session-state",
    se_default.SESSION_STATE == Path.home() / ".copilot" / "session-state",
    f"got {se_default.SESSION_STATE}",
)

# Custom path when env var is set
se_custom = _load_session_end({"COPILOT_SESSION_STATE": "/custom/se/state"})
test(
    "session-end SESSION_STATE uses COPILOT_SESSION_STATE when set",
    se_custom.SESSION_STATE == Path("/custom/se/state"),
    f"got {se_custom.SESSION_STATE}",
)

# Both checkpoint-save and session-end must agree when env var is set
with tempfile.TemporaryDirectory() as td_se:
    custom_dir = str(Path(td_se) / "custom-state")
    cp_mod = _load_fresh_module({"COPILOT_SESSION_STATE": custom_dir})
    se_mod = _load_session_end({"COPILOT_SESSION_STATE": custom_dir})
    test(
        "checkpoint-save and session-end resolve same SESSION_STATE",
        cp_mod.SESSION_STATE == se_mod.SESSION_STATE,
        f"checkpoint-save={cp_mod.SESSION_STATE}, session-end={se_mod.SESSION_STATE}",
    )

# _has_checkpoints looks in the env-var-specified dir, not the hardcoded default
with tempfile.TemporaryDirectory() as td_hc:
    custom_root = Path(td_hc) / "custom-hc"
    session_id = "ses-hc-test"
    cp_dir = custom_root / session_id / "checkpoints"
    cp_dir.mkdir(parents=True)
    # Write a valid index with one entry
    idx = cp_dir / "index.md"
    idx.write_text(
        "# Checkpoint History\n\n| # | Title | File |\n|---|-------|------|\n"
        "| 1 | Test | 001-test.md |\n",
        encoding="utf-8",
    )

    # Load session-end pointing at custom_root
    se_hc = _load_session_end({"COPILOT_SESSION_STATE": str(custom_root)})
    test(
        "_has_checkpoints finds checkpoint in custom SESSION_STATE dir",
        se_hc._has_checkpoints(session_id) is True,
        f"SESSION_STATE={se_hc.SESSION_STATE}",
    )

    # Without env var, the default path is used → checkpoint NOT found there
    se_default2 = _load_session_end({"COPILOT_SESSION_STATE": None})
    # The session lives in custom_root, not default; so _has_checkpoints must return False
    # (or True if the user's real default happens to have the same session — skip that edge case
    #  by using an unlikely session_id).
    unlikely_id = "ses-unlikely-hc-regression-test-12345"
    test(
        "_has_checkpoints returns False for unknown session in default dir",
        se_default2._has_checkpoints(unlikely_id) is False,
    )


total = _passed + _failed
print(f"\n{'─' * 40}")
print(f"Results: {_passed}/{total} passed", end="")
if _failed:
    print(f", {_failed} FAILED")
    sys.exit(1)
else:
    print(" ✓")
    sys.exit(0)
