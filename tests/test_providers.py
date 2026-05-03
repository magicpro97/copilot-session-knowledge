#!/usr/bin/env python3
"""
test_providers.py — Unit tests for the providers package (Batch A).

Uses the same test() harness pattern as test_security.py (no pytest).
Run with: python test_providers.py

Covers:
  B1. Event is hashable (add to set() with tool_args set)
  B2. SessionMeta is hashable
  B3. PROVIDER_REGISTRY contains both providers
  B4. from_event=N skips correctly on a synthetic session
  B5. ClaudeProvider.list_sessions extracts parent_id from JSONL
  B6. Backward compat: find_claude_sessions() returns dicts with original keys
"""

import json
import os
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path

# Fix Windows console encoding — mandatory pattern in this repo.
if os.name == "nt":
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

sys.path.insert(0, str(Path(__file__).parent.parent))

from providers import (
    ClaudeProvider,
    CopilotProvider,
    Event,
    PROVIDER_REGISTRY,
    SessionMeta,
)


# ══════════════════════════════════════════════════════
# B1: Event hashability
# ══════════════════════════════════════════════════════

def test_event_hashable():
    """Event with tool_args (tuple-of-pairs) must be hashable and usable in a set."""
    e1 = Event(
        session_id="s1",
        event_id=0,
        ts=None,
        kind="tool_call",
        content="[Bash] ls",
        tool_name="Bash",
        tool_args=(("command", "ls -la"), ("cwd", "/tmp")),
    )
    e2 = Event(
        session_id="s1",
        event_id=1,
        ts=None,
        kind="user_msg",
        content="Hello",
    )
    e3 = Event(
        session_id="s1",
        event_id=2,
        ts=datetime(2024, 1, 1, tzinfo=timezone.utc),
        kind="tool_call",
        content="[Edit] file.py",
        tool_name="Edit",
        tool_args=(("file_path", "file.py"), ("old_str", "x"), ("new_str", "y")),
    )

    # All must be hashable
    s = {e1, e2, e3}
    assert len(s) == 3, "Events with distinct IDs must be distinct in a set"

    # Event with None tool_args must also be hashable
    e4 = Event(session_id="s2", event_id=0, ts=None, kind="system", content="init")
    s.add(e4)
    assert len(s) == 4

    # Duplicate event must collapse in set
    e1_dup = Event(
        session_id="s1",
        event_id=0,
        ts=None,
        kind="tool_call",
        content="[Bash] ls",
        tool_name="Bash",
        tool_args=(("command", "ls -la"), ("cwd", "/tmp")),
    )
    s.add(e1_dup)
    assert len(s) == 4, "Duplicate event must not inflate set size"

    print("  ✓ Event hashability tests passed")


# ══════════════════════════════════════════════════════
# B2: SessionMeta hashability
# ══════════════════════════════════════════════════════

def test_session_meta_hashable():
    """SessionMeta must be hashable (frozen dataclass with tuple extra)."""
    meta = SessionMeta(
        id="abc123-de45-fg67-hi89-jk0123456789",
        provider="claude",
        path=Path("/tmp/fake.jsonl"),
        title="Test session",
        mtime=1700000000.0,
        parent_id=None,
        extra=(("project_hash", "deadbeef"),),
    )
    meta2 = SessionMeta(
        id="abc123-de45-fg67-hi89-jk0123456789",
        provider="claude",
        path=Path("/tmp/fake.jsonl"),
        title="Test session",
        mtime=1700000000.0,
        parent_id=None,
        extra=(("project_hash", "deadbeef"),),
    )
    s = {meta, meta2}
    assert len(s) == 1, "Identical SessionMeta must deduplicate in set"

    meta3 = SessionMeta(
        id="different-id",
        provider="copilot",
        path=Path("/tmp/session-dir"),
        title=None,
        mtime=1700000001.0,
    )
    s.add(meta3)
    assert len(s) == 2

    print("  ✓ SessionMeta hashability tests passed")


# ══════════════════════════════════════════════════════
# B3: PROVIDER_REGISTRY
# ══════════════════════════════════════════════════════

def test_provider_registry():
    """PROVIDER_REGISTRY must contain both 'copilot' and 'claude' providers."""
    assert "copilot" in PROVIDER_REGISTRY, "copilot missing from PROVIDER_REGISTRY"
    assert "claude" in PROVIDER_REGISTRY, "claude missing from PROVIDER_REGISTRY"
    assert PROVIDER_REGISTRY["copilot"] is CopilotProvider
    assert PROVIDER_REGISTRY["claude"] is ClaudeProvider

    # Instantiate both
    p1 = PROVIDER_REGISTRY["copilot"]()
    assert isinstance(p1, CopilotProvider)
    p2 = PROVIDER_REGISTRY["claude"]()
    assert isinstance(p2, ClaudeProvider)

    print("  ✓ PROVIDER_REGISTRY tests passed")


# ══════════════════════════════════════════════════════
# B4: from_event=N skips correctly (CopilotProvider synthetic session)
# ══════════════════════════════════════════════════════

def test_from_event_skip():
    """from_event=N must skip the first N events from a synthetic session."""
    # Build a synthetic Copilot session directory
    with tempfile.TemporaryDirectory() as tmpdir:
        session_dir = Path(tmpdir) / "a1b2c3d4-0000-0000-0000-000000000001"
        session_dir.mkdir()

        # Create plan.md (→ 1 system event); pad to exceed MIN_SESSION_BYTES
        plan = session_dir / "plan.md"
        plan.write_text(
            "# Test Plan\nSome content here.\n" + "x" * 1100,
            encoding="utf-8",
        )

        # Create checkpoints/index.md + one checkpoint with 2 sections
        (session_dir / "checkpoints").mkdir()
        index = session_dir / "checkpoints" / "index.md"
        index.write_text(
            "| Seq | Title | File |\n"
            "|-----|-------|------|\n"
            "| 1   | CP1   | 001-cp1.md |\n",
            encoding="utf-8",
        )
        cp1 = session_dir / "checkpoints" / "001-cp1.md"
        cp1.write_text(
            "<overview>Overview content</overview>\n"
            "<work_done>Work done here</work_done>\n",
            encoding="utf-8",
        )

        # Create research file (→ 1 assistant_msg event)
        (session_dir / "research").mkdir()
        (session_dir / "research" / "r1.md").write_text("Research notes.", encoding="utf-8")

        # Collect all events
        provider = CopilotProvider()

        # We need to inject a fake session_dir — patch COPILOT root via env
        original_env = os.environ.get("COPILOT_SESSION_STATE")
        os.environ["COPILOT_SESSION_STATE"] = tmpdir

        try:
            sessions = list(provider.list_sessions())
            assert len(sessions) == 1, f"Expected 1 session, got {len(sessions)}"
            session = sessions[0]

            # Collect all events (from_event=0)
            all_events = list(provider.iter_events(session, from_event=0))
            n_total = len(all_events)
            assert n_total > 0, "Expected at least 1 event"

            # All event_ids should be 0..N-1
            ids = [e.event_id for e in all_events]
            assert ids == list(range(n_total)), f"Non-sequential event_ids: {ids}"

            # Skip first 1
            skipped_1 = list(provider.iter_events(session, from_event=1))
            assert len(skipped_1) == n_total - 1, \
                f"from_event=1 should yield {n_total - 1} events, got {len(skipped_1)}"

            # Skip all
            skipped_all = list(provider.iter_events(session, from_event=n_total))
            assert len(skipped_all) == 0, \
                f"from_event={n_total} should yield 0 events, got {len(skipped_all)}"

            # Skip more than total
            skipped_over = list(provider.iter_events(session, from_event=n_total + 100))
            assert len(skipped_over) == 0

        finally:
            if original_env is None:
                os.environ.pop("COPILOT_SESSION_STATE", None)
            else:
                os.environ["COPILOT_SESSION_STATE"] = original_env

    print("  ✓ from_event skip tests passed")


# ══════════════════════════════════════════════════════
# B5: ClaudeProvider parent_id from JSONL
# ══════════════════════════════════════════════════════

def test_claude_parent_id_from_jsonl():
    """ClaudeProvider.list_sessions must extract parent_id from JSONL parentSessionId."""
    PARENT_UUID = "parent-uuid-0000-1111-222233334444"
    CHILD_UUID  = "child-uuid-aaaa-bbbb-ccccddddeeee"

    with tempfile.TemporaryDirectory() as tmpdir:
        root = Path(tmpdir)
        project_dir = root / "proj-abc123"
        project_dir.mkdir()

        # Create subagent JSONL with parentSessionId
        subdir = project_dir / "some-subdir"
        subagents_dir = subdir / "subagents"
        subagents_dir.mkdir(parents=True)

        child_jsonl = subagents_dir / f"{CHILD_UUID}.jsonl"
        lines = [
            json.dumps({
                "type": "system",
                "sessionId": CHILD_UUID,
                "parentSessionId": PARENT_UUID,
                "timestamp": "2024-01-01T00:00:00Z",
                "message": {"role": "system", "content": "init"},
            }),
            json.dumps({
                "type": "user",
                "sessionId": CHILD_UUID,
                "timestamp": "2024-01-01T00:00:01Z",
                "message": {"role": "user", "content": "Do the task"},
            }),
        ]
        child_jsonl.write_text("\n".join(lines) + "\n" + ("# " + "x" * 100 + "\n") * 12, encoding="utf-8")

        # Create a dummy top-level session too (no parentSessionId)
        top_uuid = "top-level-uuid-1234-5678-abcdef000000"
        top_jsonl = project_dir / f"{top_uuid}.jsonl"
        top_lines = [
            json.dumps({
                "type": "user",
                "sessionId": top_uuid,
                "timestamp": "2024-01-01T00:00:00Z",
                "message": {"role": "user", "content": "Hello"},
            }),
        ]
        top_jsonl.write_text("\n".join(top_lines) + "\n" + ("# " + "x" * 100 + "\n") * 12, encoding="utf-8")

        # Run list_sessions with env override
        original_env = os.environ.get("CLAUDE_PROJECTS")
        os.environ["CLAUDE_PROJECTS"] = str(root)

        try:
            provider = ClaudeProvider()
            sessions = {s.id: s for s in provider.list_sessions()}

            # Check subagent session has correct parent_id
            assert CHILD_UUID in sessions, f"Child session {CHILD_UUID} not found"
            child_meta = sessions[CHILD_UUID]
            assert child_meta.parent_id == PARENT_UUID, \
                f"Expected parent_id={PARENT_UUID!r}, got {child_meta.parent_id!r}"
            assert child_meta.parent_id != "some-subdir", \
                "parent_id must NOT be the directory name (A-BL-02)"

            # Check top-level session has no parent
            assert top_uuid in sessions, f"Top-level session {top_uuid} not found"
            assert sessions[top_uuid].parent_id is None

        finally:
            if original_env is None:
                os.environ.pop("CLAUDE_PROJECTS", None)
            else:
                os.environ["CLAUDE_PROJECTS"] = original_env

    print("  ✓ ClaudeProvider parent_id extraction tests passed")


# ══════════════════════════════════════════════════════
# B6: ClaudeProvider iter_events from_event skip
# ══════════════════════════════════════════════════════

def test_claude_iter_events_from_event():
    """ClaudeProvider.iter_events must skip first N events when from_event=N."""
    SESSION_UUID = "session-uuid-1111-2222-333344445555"

    with tempfile.TemporaryDirectory() as tmpdir:
        root = Path(tmpdir)
        project_dir = root / "proj-xyz"
        project_dir.mkdir()

        # 5 user messages → 5 user_msg events
        lines = []
        for i in range(5):
            lines.append(json.dumps({
                "type": "user",
                "sessionId": SESSION_UUID,
                "timestamp": f"2024-01-01T00:00:0{i}Z",
                "message": {"role": "user", "content": f"Message {i}"},
            }))
        jsonl = project_dir / f"{SESSION_UUID}.jsonl"
        jsonl.write_text("\n".join(lines) + "\n" + "# padding\n" * 60, encoding="utf-8")

        original_env = os.environ.get("CLAUDE_PROJECTS")
        os.environ["CLAUDE_PROJECTS"] = str(root)

        try:
            provider = ClaudeProvider()
            sessions = list(provider.list_sessions())
            assert len(sessions) == 1
            session = sessions[0]

            all_events = list(provider.iter_events(session, from_event=0))
            assert len(all_events) == 5, f"Expected 5 events, got {len(all_events)}"

            skipped_2 = list(provider.iter_events(session, from_event=2))
            assert len(skipped_2) == 3, \
                f"from_event=2 should yield 3 events, got {len(skipped_2)}"

            skipped_all = list(provider.iter_events(session, from_event=5))
            assert len(skipped_all) == 0

        finally:
            if original_env is None:
                os.environ.pop("CLAUDE_PROJECTS", None)
            else:
                os.environ["CLAUDE_PROJECTS"] = original_env

    print("  ✓ ClaudeProvider from_event skip tests passed")


# ══════════════════════════════════════════════════════
# B7: Backward compat — find_claude_sessions() dict shape
# ══════════════════════════════════════════════════════

def test_backward_compat_find_claude_sessions():
    """find_claude_sessions() must return dicts with the original keys."""
    import importlib.util

    adapter_path = Path(__file__).parent.parent / "claude-adapter.py"
    spec = importlib.util.spec_from_file_location("claude_adapter", adapter_path)
    ca = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(ca)

    # Build a minimal fixture under a temp directory
    with tempfile.TemporaryDirectory() as tmpdir:
        root = Path(tmpdir)
        project_dir = root / "proj-compat-test"
        project_dir.mkdir()

        sess_uuid = "compat-uuid-0000-aaaa-bbbb-ccccddddeeee"
        jsonl = project_dir / f"{sess_uuid}.jsonl"
        jsonl.write_text(
            json.dumps({
                "type": "user",
                "sessionId": sess_uuid,
                "timestamp": "2024-01-01T00:00:00Z",
                "message": {"role": "user", "content": "hello compat"},
            }) + "\n" + ("# " + "x" * 100 + "\n") * 12,
            encoding="utf-8",
        )

        # Patch CLAUDE_PROJECTS so find_claude_sessions scans our temp dir
        original = os.environ.get("CLAUDE_PROJECTS_OVERRIDE")
        original_ca_path = ca.CLAUDE_PROJECTS
        ca.CLAUDE_PROJECTS = root  # direct attribute patch

        try:
            sessions = ca.find_claude_sessions()
        finally:
            ca.CLAUDE_PROJECTS = original_ca_path

    # Every returned dict must have the required original keys
    assert isinstance(sessions, list), "find_claude_sessions must return a list"
    for s in sessions:
        assert "project_hash" in s, f"Missing 'project_hash' key: {s}"
        assert "session_id" in s, f"Missing 'session_id' key: {s}"
        assert "path" in s, f"Missing 'path' key: {s}"
        assert "size_bytes" in s, f"Missing 'size_bytes' key: {s}"
        assert isinstance(s["path"], Path), "path must be a Path object"

    # Verify our session was found
    found_ids = [s["session_id"] for s in sessions]
    assert sess_uuid in found_ids, \
        f"Expected session {sess_uuid!r} in find_claude_sessions() output"

    print("  ✓ Backward compat find_claude_sessions() tests passed")


# ══════════════════════════════════════════════════════
# B8: Event kind validation
# ══════════════════════════════════════════════════════

def test_event_kind_validation():
    """Event must raise ValueError for invalid kinds."""
    import traceback

    try:
        bad = Event(session_id="x", event_id=0, ts=None, kind="unknown_kind", content="x")
        assert False, "Expected ValueError for invalid kind"
    except ValueError as e:
        assert "unknown_kind" in str(e)

    # All 7 valid kinds must construct without error
    valid_kinds = ("user_msg", "assistant_msg", "tool_call", "tool_result",
                   "diff", "system", "note")
    for k in valid_kinds:
        e = Event(session_id="s", event_id=0, ts=None, kind=k, content="test")
        assert e.kind == k

    print("  ✓ Event kind validation tests passed")


# ══════════════════════════════════════════════════════
# B9: compute_session_hash returns consistent cache key
# ══════════════════════════════════════════════════════

def test_compute_session_hash():
    """compute_session_hash returns a mtime+size cache key string."""
    with tempfile.TemporaryDirectory() as tmpdir:
        p = Path(tmpdir) / "test.jsonl"
        p.write_text("hello", encoding="utf-8")

        meta = SessionMeta(
            id="test-id",
            provider="claude",
            path=p,
            title=None,
            mtime=p.stat().st_mtime,
        )
        provider = ClaudeProvider()
        h = provider.compute_session_hash(meta)
        assert isinstance(h, str) and len(h) > 0
        # Format: "<float>:<int>"
        parts = h.split(":")
        assert len(parts) == 2, f"Expected 'mtime:size' format, got: {h!r}"
        float(parts[0])  # must be parseable as float
        int(parts[1])    # must be parseable as int

        # Same metadata → same hash
        assert provider.compute_session_hash(meta) == h

    print("  ✓ compute_session_hash tests passed")


# ══════════════════════════════════════════════════════
# Main runner (matches test_security.py pattern)
# ══════════════════════════════════════════════════════

def main():
    print("\n🧪 Running providers tests (Batch A)...\n")
    passed = 0
    failed = 0

    tests = [
        test_event_hashable,
        test_session_meta_hashable,
        test_provider_registry,
        test_from_event_skip,
        test_claude_parent_id_from_jsonl,
        test_claude_iter_events_from_event,
        test_backward_compat_find_claude_sessions,
        test_event_kind_validation,
        test_compute_session_hash,
    ]

    for test in tests:
        try:
            test()
            passed += 1
        except (AssertionError, Exception) as e:
            import traceback
            print(f"  ✗ {test.__name__}: {e}")
            traceback.print_exc()
            failed += 1

    print(f"\n{'='*50}")
    print(f"Results: {passed} passed, {failed} failed")
    if failed:
        sys.exit(1)
    else:
        print("✅ All provider tests passed!")


if __name__ == "__main__":
    main()
