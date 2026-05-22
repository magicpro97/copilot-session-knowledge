#!/usr/bin/env python3
"""
test_watch_sessions.py — Focused tests for watch-sessions.py helper logic.

Covers:
  - Lock acquire / release lifecycle (temp directory, no real watcher)
  - Stale-PID detection and stale-lock recovery
  - get_file_signatures() scans correct extensions
  - _content_hash() returns hex string and empty string on missing file
  - _extract_session_ids_from_paths() parses Copilot and Claude layouts
  - _adaptive_poll_interval() tier logic
  - check_and_index() content-hash change detection (new/changed/unchanged)
  - save_state() / load_state() round-trip (temp files, no real session-state)

Run: python3 tests/test_watch_sessions.py
"""

import hashlib
import importlib.util
import json
import os
import sys
import time
from pathlib import Path

if os.name == "nt":
    for _s in (sys.stdout, sys.stderr):
        if hasattr(_s, "reconfigure"):
            _s.reconfigure(encoding="utf-8", errors="replace")

PASS = 0
FAIL = 0
REPO = Path(__file__).parent.parent

# Ensure host_manifest and other local modules are importable
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

# ---------------------------------------------------------------------------
# Minimal helper
# ---------------------------------------------------------------------------

def test(name: str, condition: bool, detail: str = "") -> None:
    global PASS, FAIL
    if condition:
        PASS += 1
        print(f"  ✅ {name}")
    else:
        FAIL += 1
        print(f"  ❌ {name}" + (f" — {detail}" if detail else ""))


# ---------------------------------------------------------------------------
# Load watch-sessions module without executing main() or requiring real paths
# ---------------------------------------------------------------------------

_script = REPO / "watch-sessions.py"
_spec = importlib.util.spec_from_file_location("_ws", _script)
_ws = importlib.util.module_from_spec(_spec)
_saved_argv = sys.argv[:]
sys.argv = [str(_script)]
try:
    _spec.loader.exec_module(_ws)
finally:
    sys.argv = _saved_argv


# ---------------------------------------------------------------------------
# Scratch directory (inside repo — /tmp is prohibited)
# ---------------------------------------------------------------------------

SCRATCH = REPO / ".test-scratch" / "watch-sessions-tests"
SCRATCH.mkdir(parents=True, exist_ok=True)


# ── 1. _content_hash ─────────────────────────────────────────────────────────

print("\n🔑 _content_hash")

def _sha256_prefix(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()[:16]

# Known content
tf = SCRATCH / "hash_test.md"
tf.write_bytes(b"hello world")
h = _ws._content_hash(tf)
test("returns 16-char hex for existing file", len(h) == 16 and all(c in "0123456789abcdef" for c in h))
test("matches manual SHA256 prefix", h == _sha256_prefix(b"hello world"))

# Missing file → empty string
missing = SCRATCH / "does_not_exist.md"
test("returns empty string for missing file", _ws._content_hash(missing) == "")

# Empty file → deterministic non-empty hash
empty_f = SCRATCH / "empty.md"
empty_f.write_bytes(b"")
h_empty = _ws._content_hash(empty_f)
test("returns non-empty hash for empty file", len(h_empty) == 16)


# ── 2. get_file_signatures ───────────────────────────────────────────────────

print("\n📁 get_file_signatures")

sig_root = SCRATCH / "sig_root"
sess_dir = sig_root / "session-abc"
sess_dir.mkdir(parents=True, exist_ok=True)

(sess_dir / "plan.md").write_text("# plan", encoding="utf-8")
(sess_dir / "notes.txt").write_text("notes", encoding="utf-8")
(sess_dir / "session.jsonl").write_text('{"a":1}', encoding="utf-8")
(sess_dir / "ignored.py").write_text("x=1", encoding="utf-8")  # not indexed

sigs = _ws.get_file_signatures([sig_root])
keys = [Path(k).name for k in sigs]
test("indexes .md files", "plan.md" in keys)
test("indexes .txt files", "notes.txt" in keys)
test("indexes .jsonl files", "session.jsonl" in keys)
test("ignores .py files", "ignored.py" not in keys)
test("returns (mtime, size) tuples", all(len(v) == 2 for v in sigs.values()))

# Hidden directories are skipped
hidden_dir = sig_root / ".hidden-session"
hidden_dir.mkdir(exist_ok=True)
(hidden_dir / "secret.md").write_text("secret", encoding="utf-8")
sigs2 = _ws.get_file_signatures([sig_root])
hidden_keys = [Path(k).name for k in sigs2]
test("skips hidden directories", "secret.md" not in hidden_keys)


# ── 3. _extract_session_ids_from_paths ────────────────────────────────────────

print("\n🔍 _extract_session_ids_from_paths")

copilot_root = SCRATCH / "copilot-root"
claude_root = SCRATCH / "claude-root"

UUID1 = "12345678-1234-1234-1234-1234567890ab"
UUID2 = "abcdef12-abcd-abcd-abcd-abcdef123456"

# Copilot layout: <root>/<uuid>/plan.md
copilot_path = str(copilot_root / UUID1 / "plan.md")
# Claude layout: <root>/<project-hash>/<uuid>.jsonl
claude_path = str(claude_root / "proj-abc123" / f"{UUID2}.jsonl")
# Non-UUID directory — should not be extracted
non_uuid_path = str(copilot_root / "not-a-uuid" / "plan.md")

ids = _ws._extract_session_ids_from_paths(
    [copilot_path, claude_path, non_uuid_path],
    [copilot_root, claude_root],
)
test("extracts Copilot UUID from path", UUID1 in ids)
test("extracts Claude UUID from JSONL path", UUID2 in ids)
test("ignores non-UUID directory names", "not-a-uuid" not in ids)
test("returns sorted deduplicated list", ids == sorted(set(ids)))

# Both pointing to same UUID
dup_path = str(copilot_root / UUID1 / "another.md")
ids_dup = _ws._extract_session_ids_from_paths([copilot_path, dup_path], [copilot_root])
test("deduplicates same UUID", ids_dup.count(UUID1) == 1)

# Empty input → empty result
test("empty paths returns empty list", _ws._extract_session_ids_from_paths([], [copilot_root]) == [])


# ── 4. _adaptive_poll_interval ────────────────────────────────────────────────

print("\n⏱️  _adaptive_poll_interval")

now = time.time()

# Active tier: file modified < 2 minutes ago
active_sigs = {"f1": [now - 30, 100, "abc"]}
test("active tier → 5s", _ws._adaptive_poll_interval(active_sigs) == 5)

# Recent tier: modified between 2 min and 1 hour ago
recent_sigs = {"f1": [now - 600, 100, "abc"]}
test("recent tier → 30s", _ws._adaptive_poll_interval(recent_sigs) == 30)

# Idle tier: older than 1 hour
idle_sigs = {"f1": [now - 7200, 100, "abc"]}
test("idle tier → 300s", _ws._adaptive_poll_interval(idle_sigs) == 300)

# Empty dict → idle tier
test("empty sigs → 300s", _ws._adaptive_poll_interval({}) == 300)

# Old-format 2-element tuples are handled
old_fmt_sigs = {"f1": (now - 30, 100)}
test("old 2-element tuple → active tier", _ws._adaptive_poll_interval(old_fmt_sigs) == 5)


# ── 5. save_state / load_state round-trip ────────────────────────────────────

print("\n💾 save_state / load_state")

# Monkey-patch STATE_FILE to scratch location
original_state_file = _ws.STATE_FILE
_ws.STATE_FILE = SCRATCH / ".watch-state-test.json"
if _ws.STATE_FILE.exists():
    _ws.STATE_FILE.unlink()

# Fresh state when file absent
fresh = _ws.load_state()
test("returns default state when file absent", fresh == {"signatures": {}, "last_index": None})

# Round-trip
payload = {"signatures": {"file.md": [1000.5, 512, "deadbeef"]}, "last_index": "2025-01-01T00:00:00"}
_ws.save_state(payload)
loaded = _ws.load_state()
test("loaded state matches saved payload", loaded == payload)

# Corrupted file → returns defaults
_ws.STATE_FILE.write_text("{corrupt json{{", encoding="utf-8")
corrupt_loaded = _ws.load_state()
test("corrupted JSON returns default state", corrupt_loaded == {"signatures": {}, "last_index": None})

# Restore
_ws.STATE_FILE = original_state_file


# ── 6. check_and_index — content hash change detection (no real indexer) ─────

print("\n🔄 check_and_index (content-hash logic)")

# Patch run_indexer and run_extractor to no-ops so we don't spawn subprocesses
_orig_run_indexer = _ws.run_indexer
_orig_run_extractor = _ws.run_extractor
_ws.run_indexer = lambda incremental=True: True
_ws.run_extractor = lambda changed_files=None, session_ids=None: True

idx_root = SCRATCH / "idx_root"
idx_sess = idx_root / "session-xyz"
idx_sess.mkdir(parents=True, exist_ok=True)
f1 = idx_sess / "plan.md"
f1.write_text("original content", encoding="utf-8")

# Build initial sigs (no previous state)
new_sigs = _ws.check_and_index({}, [idx_root])
test("returns dict of enriched sigs", isinstance(new_sigs, dict))
test("new file included in returned sigs", str(f1) in new_sigs)
test("enriched sig has 3 elements", len(new_sigs.get(str(f1), [])) == 3)

# Same content → should NOT trigger reindex (content hash matches)
# Build prev_sigs from new_sigs (content already there)
prev_sigs = dict(new_sigs)
indexer_called = []
_ws.run_indexer = lambda incremental=True: indexer_called.append(True) or True
sigs_again = _ws.check_and_index(prev_sigs, [idx_root])
test("no reindex when content unchanged", len(indexer_called) == 0)

# Modify content → should trigger reindex
f1.write_text("changed content", encoding="utf-8")
indexer_called2 = []
_ws.run_indexer = lambda incremental=True: indexer_called2.append(True) or True
sigs_changed = _ws.check_and_index(prev_sigs, [idx_root])
test("reindex triggered when content changes", len(indexer_called2) >= 1)

_ws.run_indexer = _orig_run_indexer
_ws.run_extractor = _orig_run_extractor


# ── 6b. Hot-path: unchanged files skip _content_hash entirely ────────────────

print("\n⚡ check_and_index — hot-path hash-skip")

# Patch to no-ops again
_ws.run_indexer = lambda incremental=True: True
_ws.run_extractor = lambda changed_files=None, session_ids=None: True

hp_root = SCRATCH / "hp_root"
hp_sess = hp_root / "session-hp"
hp_sess.mkdir(parents=True, exist_ok=True)
hp_f = hp_sess / "notes.md"
hp_f.write_text("stable content", encoding="utf-8")

# First poll: establish enriched sigs (hash gets computed once)
hp_sigs = _ws.check_and_index({}, [hp_root])
test("hp: initial sigs has 3-element entry", len(hp_sigs.get(str(hp_f), [])) == 3)

# Instrument _content_hash to count calls
_hash_call_count = []
_orig_content_hash = _ws._content_hash

def _counting_hash(path: Path) -> str:
    _hash_call_count.append(str(path))
    return _orig_content_hash(path)

_ws._content_hash = _counting_hash

# Second poll: mtime+size unchanged → _content_hash must NOT be called for hp_f
_hash_call_count.clear()
hp_sigs2 = _ws.check_and_index(hp_sigs, [hp_root])
called_for_hp = any(str(hp_f) in p for p in _hash_call_count)
test("hot-path: _content_hash NOT called for stable file", not called_for_hp,
     f"hash called for: {_hash_call_count}")
test("hot-path: stored hash preserved", hp_sigs2.get(str(hp_f), [None, None, None])[2] == hp_sigs.get(str(hp_f))[2])

# Write new content; sleep briefly so mtime advances on coarse-grained filesystems.
time.sleep(0.05)
hp_f.write_text("changed content now", encoding="utf-8")

# Capture the signature as seen by the OS *after* the write, before calling
# check_and_index.  On coarse-grained filesystems or very fast writes the OS
# may not have advanced mtime yet; skip the mtime-triggered assertions in that
# case — the hot-path correctly reused the stored hash.
_sig_after_write = _ws.get_file_signatures([hp_root]).get(str(hp_f))
_prev_meta = (hp_sigs.get(str(hp_f), [None, None])[0], hp_sigs.get(str(hp_f), [None, None])[1])
_sig_changed = _sig_after_write is not None and (
    _sig_after_write[0] != _prev_meta[0] or _sig_after_write[1] != _prev_meta[1]
)

_hash_call_count.clear()
_ws.run_indexer = lambda incremental=True: True  # keep no-op
hp_sigs3 = _ws.check_and_index(hp_sigs, [hp_root])
called_after_change = any(str(hp_f) in p for p in _hash_call_count)
if _sig_changed:
    test("hot-path: _content_hash IS called after mtime changes", called_after_change,
         f"hash NOT called despite content change")
    test("hot-path: new hash stored after change",
         hp_sigs3.get(str(hp_f), [None, None, None])[2] != hp_sigs.get(str(hp_f))[2])
else:
    # Coarse-grained filesystem: signature unchanged → hot-path correctly
    # reused stored hash; just verify the stored value was preserved.
    test("hot-path: coarse-fs stable sig → stored hash preserved (hot-path no-op)",
         hp_sigs3.get(str(hp_f), [None, None, None])[2] == hp_sigs.get(str(hp_f))[2])

# Legacy backfill: prev_sigs has 2-element entry (no stored hash) → one hash call expected
hp_f.write_text("stable content", encoding="utf-8")  # reset to stable
_hash_call_count.clear()
legacy_prev = {str(hp_f): list(_ws.get_file_signatures([hp_root]).get(str(hp_f), (0, 0)))}  # 2-elem
hp_sigs4 = _ws.check_and_index(legacy_prev, [hp_root])
called_for_backfill = any(str(hp_f) in p for p in _hash_call_count)
test("hot-path: legacy 2-elem entry triggers one-time backfill hash", called_for_backfill,
     "expected _content_hash called once for upgrade backfill")
test("hot-path: backfill produces 3-elem entry", len(hp_sigs4.get(str(hp_f), [])) == 3)

# Subsequent poll after backfill: now 3-elem → no hash call
_hash_call_count.clear()
_ws.check_and_index(hp_sigs4, [hp_root])
called_after_backfill = any(str(hp_f) in p for p in _hash_call_count)
test("hot-path: no hash call on poll after backfill", not called_after_backfill,
     f"unexpected hash calls: {_hash_call_count}")

# Restore
_ws._content_hash = _orig_content_hash
_ws.run_indexer = _orig_run_indexer
_ws.run_extractor = _orig_run_extractor


# ── 7. _is_pid_running ────────────────────────────────────────────────────────

print("\n🔒 _is_pid_running")

# Current process must be running
test("current PID is running", _ws._is_pid_running(os.getpid()))

# Unlikely-to-exist PID (very large)
test("non-existent PID returns False", not _ws._is_pid_running(999_999_999))


# ── 8. Lock acquire / release ─────────────────────────────────────────────────

print("\n🔐 Lock acquire / release")

# Redirect LOCK_FILE to scratch
orig_lock = _ws.LOCK_FILE
_ws.LOCK_FILE = SCRATCH / ".test-watcher.lock"
if _ws.LOCK_FILE.exists():
    _ws.LOCK_FILE.unlink()

acquired = _ws.acquire_lock()
test("first acquire succeeds", acquired)
test("lock file created", _ws.LOCK_FILE.exists())

# Second acquire from same process: lock file exists, PID is our own → different pid check
# (Can't call acquire_lock twice without releasing since it uses O_EXCL)
_ws.release_lock()
test("lock file removed after release", not _ws.LOCK_FILE.exists())

# Stale lock: write a dead PID, then acquire
_ws.LOCK_FILE.write_text("999999999", encoding="utf-8")
stale_acquired = _ws.acquire_lock()
test("stale lock is recovered and acquired", stale_acquired)
_ws.release_lock()

_ws.LOCK_FILE = orig_lock


# ── Summary ──────────────────────────────────────────────────────────────────

print(f"\n{'='*50}")
print(f"Results: {PASS} passed, {FAIL} failed")

# Cleanup
import shutil
try:
    shutil.rmtree(SCRATCH, ignore_errors=True)
except Exception:
    pass

sys.exit(1 if FAIL else 0)
