#!/usr/bin/env python3
"""
test_build_session_index.py — Focused tests for build-session-index.py helper logic.

Covers:
  - _normalize_title() whitespace normalization
  - _stable_sha256() determinism and uniqueness
  - _document_stable_id() / _section_stable_id() reproducibility
  - create_db() schema creation and idempotence
  - _is_system_boilerplate() noise filter
  - should_skip_session() two-phase skip logic
  - phase1_upsert_session() metadata upsert
  - file_hash() MD5 computation

Run: python3 tests/test_build_session_index.py
"""

import importlib.util
import os
import sqlite3
import sys
from pathlib import Path

if os.name == "nt":
    for _s in (sys.stdout, sys.stderr):
        if hasattr(_s, "reconfigure"):
            _s.reconfigure(encoding="utf-8", errors="replace")

PASS = 0
FAIL = 0
REPO = Path(__file__).parent.parent

SCRATCH = REPO / ".test-scratch" / "build-session-index-tests"
SCRATCH.mkdir(parents=True, exist_ok=True)

# Ensure local modules are importable
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

_script = REPO / "build-session-index.py"
_spec = importlib.util.spec_from_file_location("_bsi", _script)
_bsi = importlib.util.module_from_spec(_spec)
_saved_argv = sys.argv[:]
sys.argv = [str(_script)]
try:
    _spec.loader.exec_module(_bsi)
finally:
    sys.argv = _saved_argv


# ── 1. _normalize_title ───────────────────────────────────────────────────────

print("\n📝 _normalize_title")

test("strips leading/trailing whitespace", _bsi._normalize_title("  hello  ") == "hello")
test("collapses internal whitespace", _bsi._normalize_title("hello   world") == "hello world")
test("lowercases", _bsi._normalize_title("HELLO WORLD") == "hello world")
test("handles None gracefully", _bsi._normalize_title(None) == "")
test("handles empty string", _bsi._normalize_title("") == "")


# ── 2. _stable_sha256 ────────────────────────────────────────────────────────

print("\n🔐 _stable_sha256")

h1 = _bsi._stable_sha256("a", "b", "c")
h2 = _bsi._stable_sha256("a", "b", "c")
h3 = _bsi._stable_sha256("a", "b", "d")

test("returns a 64-char hex string", len(h1) == 64)
test("deterministic for same inputs", h1 == h2)
test("different for different inputs", h1 != h3)
test("handles None parts", isinstance(_bsi._stable_sha256(None, "x"), str))
test("handles int parts", isinstance(_bsi._stable_sha256(1, 2, 3), str))


# ── 3. _document_stable_id / _section_stable_id ──────────────────────────────

print("\n🆔 Stable IDs")

doc_id = _bsi._document_stable_id("sess-abc", "checkpoint", 1, "My Title")
doc_id2 = _bsi._document_stable_id("sess-abc", "checkpoint", 1, "My Title")
doc_id3 = _bsi._document_stable_id("sess-abc", "checkpoint", 2, "My Title")

test("document stable ID is 64-char hex", len(doc_id) == 64)
test("document stable ID is deterministic", doc_id == doc_id2)
test("different seq → different stable ID", doc_id != doc_id3)

sec_id = _bsi._section_stable_id(doc_id, "overview")
sec_id2 = _bsi._section_stable_id(doc_id, "overview")
sec_id3 = _bsi._section_stable_id(doc_id, "history")

test("section stable ID is 64-char hex", len(sec_id) == 64)
test("section stable ID is deterministic", sec_id == sec_id2)
test("different section → different stable ID", sec_id != sec_id3)
test("different doc ID → different section stable ID",
     _bsi._section_stable_id(doc_id3, "overview") != sec_id)


# ── 4. create_db — schema idempotence ────────────────────────────────────────

print("\n🗄️  create_db")

db_path = SCRATCH / "test_schema.db"
if db_path.exists():
    db_path.unlink()

db = _bsi.create_db(db_path)

# Core tables exist
tables_q = "SELECT name FROM sqlite_master WHERE type='table' ORDER BY name"
table_names = {r[0] for r in db.execute(tables_q).fetchall()}

test("sessions table created", "sessions" in table_names)
test("documents table created", "documents" in table_names)
test("sections table created", "sections" in table_names)
test("knowledge_fts virtual table created", "knowledge_fts" in table_names)
test("sync_table_policies created", "sync_table_policies" in table_names)

# Calling create_db again is idempotent
db.close()
db2 = _bsi.create_db(db_path)
table_names2 = {r[0] for r in db2.execute(tables_q).fetchall()}
test("idempotent: same tables on second call", table_names == table_names2)
db2.close()


# ── 5. _is_system_boilerplate noise filter ───────────────────────────────────

print("\n🔕 _is_system_boilerplate")

class FakeEvent:
    def __init__(self, kind, content=""):
        self.kind = kind
        self.content = content

# 'note' kind is always boilerplate
test("note kind always filtered", _bsi._is_system_boilerplate(FakeEvent("note", "anything")))

# system kind with boilerplate patterns
test("<context> block filtered", _bsi._is_system_boilerplate(FakeEvent("system", "<context>some stuff")))
test("you are claude filtered", _bsi._is_system_boilerplate(FakeEvent("system", "You are Claude, a helpful assistant")))
test("here are some instructions filtered", _bsi._is_system_boilerplate(FakeEvent("system", "Here are some instructions for you")))

# system kind with real content — not boilerplate
test("real system event not filtered", not _bsi._is_system_boilerplate(FakeEvent("system", "Build the feature")))

# non-system, non-note → not boilerplate
test("user kind not filtered", not _bsi._is_system_boilerplate(FakeEvent("user", "<context>foo</context>")))
test("assistant kind not filtered", not _bsi._is_system_boilerplate(FakeEvent("assistant", "You are claude")))


# ── 6. should_skip_session ───────────────────────────────────────────────────

print("\n⏭️  should_skip_session")

skip_db_path = SCRATCH / "skip_test.db"
if skip_db_path.exists():
    skip_db_path.unlink()

skip_db = _bsi.create_db(skip_db_path)
mtime = 1000.0

# Session not in DB → do not skip
test("unknown session not skipped", not _bsi.should_skip_session(skip_db, "sess-new", mtime))

# Insert a session with matching mtime and fts_indexed_at >= mtime
skip_db.execute("""
    INSERT INTO sessions (id, path, file_mtime, fts_indexed_at)
    VALUES ('sess-uptodate', 'test/path', ?, ?)
""", (mtime, mtime + 1))
skip_db.commit()
test("up-to-date session is skipped", _bsi.should_skip_session(skip_db, "sess-uptodate", mtime))

# Same mtime but fts_indexed_at is NULL → do not skip
skip_db.execute("""
    INSERT INTO sessions (id, path, file_mtime, fts_indexed_at)
    VALUES ('sess-nofts', 'test/path', ?, NULL)
""", (mtime,))
skip_db.commit()
test("session without fts_indexed_at not skipped", not _bsi.should_skip_session(skip_db, "sess-nofts", mtime))

# Different mtime → do not skip (file changed since last index)
skip_db.execute("""
    INSERT INTO sessions (id, path, file_mtime, fts_indexed_at)
    VALUES ('sess-stale', 'test/path', ?, ?)
""", (mtime - 10, mtime))
skip_db.commit()
test("stale mtime not skipped", not _bsi.should_skip_session(skip_db, "sess-stale", mtime))

skip_db.close()


# ── 7. phase1_upsert_session ─────────────────────────────────────────────────

print("\n📥 phase1_upsert_session")

p1_db_path = SCRATCH / "phase1_test.db"
if p1_db_path.exists():
    p1_db_path.unlink()
p1_db = _bsi.create_db(p1_db_path)

_bsi.phase1_upsert_session(
    p1_db, "sess-ph1", "/fake/path", "copilot", 1234.5, 4096, 10
)
p1_db.commit()

row = p1_db.execute(
    "SELECT id, path, source, file_mtime, file_size_bytes, event_count_estimate FROM sessions WHERE id='sess-ph1'"
).fetchone()
test("session row inserted", row is not None)
test("path stored correctly", row[1] == "/fake/path")
test("source stored correctly", row[2] == "copilot")
test("file_mtime stored correctly", row[3] == 1234.5)
test("file_size_bytes stored correctly", row[4] == 4096)
test("event_count_estimate stored correctly", row[5] == 10)

# Upsert — update mtime
_bsi.phase1_upsert_session(
    p1_db, "sess-ph1", "/fake/path", "copilot", 9999.0, 8192, 20
)
p1_db.commit()
row2 = p1_db.execute(
    "SELECT file_mtime, file_size_bytes FROM sessions WHERE id='sess-ph1'"
).fetchone()
test("upsert updates file_mtime", row2[0] == 9999.0)
test("upsert updates file_size_bytes", row2[1] == 8192)

p1_db.close()


# ── 8. file_hash ─────────────────────────────────────────────────────────────

print("\n#️⃣  file_hash")

fh_path = SCRATCH / "file_hash_test.bin"
fh_path.write_bytes(b"\x00\x01\x02\x03")
h = _bsi.file_hash(fh_path)
import hashlib as _hl
expected = _hl.md5(b"\x00\x01\x02\x03").hexdigest()
test("file_hash returns 32-char hex string", len(h) == 32)
test("file_hash matches manual MD5", h == expected)

# Deterministic
test("file_hash is deterministic", _bsi.file_hash(fh_path) == h)


# ── Summary ──────────────────────────────────────────────────────────────────

print(f"\n{'='*50}")
print(f"Results: {PASS} passed, {FAIL} failed")

import shutil
try:
    shutil.rmtree(SCRATCH, ignore_errors=True)
except Exception:
    pass

sys.exit(1 if FAIL else 0)
