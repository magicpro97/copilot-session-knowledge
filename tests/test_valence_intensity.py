#!/usr/bin/env python3
"""
test_valence_intensity.py - Regression tests for issue #88.

Covers:
  - migrate.py v21 adds valence + intensity columns to knowledge_entries
  - learn.py --valence and --intensity flags write correct DB values
  - briefing.py _ke_has_intensity() and _intensity_order_expr() helpers
  - High-intensity entries rank above low-intensity entries in search results

Run: python tests/test_valence_intensity.py
"""
import importlib.util
import os
import sqlite3
import subprocess
import sys
import textwrap
from pathlib import Path

if os.name == "nt":
    for _s in (sys.stdout, sys.stderr):
        if hasattr(_s, "reconfigure"):
            _s.reconfigure(encoding="utf-8", errors="replace")

PASS = 0
FAIL = 0
REPO = Path(__file__).parent.parent

if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))


def test(name, condition, detail=""):
    global PASS, FAIL
    if condition:
        PASS += 1
        print("  OK  " + name)
    else:
        FAIL += 1
        print("  FAIL " + name + (" -- " + detail if detail else ""))


def _load_module(script_name, db_path):
    """Load a script as a module, patching its DB_PATH to db_path."""
    script = REPO / script_name
    spec = importlib.util.spec_from_file_location("_vi_" + script_name.replace(".", "_"), script)
    mod = importlib.util.module_from_spec(spec)
    saved_argv = sys.argv[:]
    sys.argv = [str(script)]
    try:
        spec.loader.exec_module(mod)
    finally:
        sys.argv = saved_argv
    if hasattr(mod, "DB_PATH"):
        mod.DB_PATH = db_path
    return mod


def _make_base_db(db_path):
    """Create a minimal knowledge DB with base schema for testing."""
    db_path.parent.mkdir(parents=True, exist_ok=True)
    db = sqlite3.connect(str(db_path))
    db.executescript(textwrap.dedent("""
        CREATE TABLE IF NOT EXISTS schema_version (
            version INTEGER PRIMARY KEY,
            name TEXT DEFAULT ''
        );
        CREATE TABLE IF NOT EXISTS knowledge_entries (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            session_id TEXT NOT NULL DEFAULT '',
            category TEXT NOT NULL,
            title TEXT NOT NULL,
            content TEXT NOT NULL,
            topic_key TEXT,
            tags TEXT DEFAULT '',
            confidence REAL DEFAULT 0.7,
            wing TEXT DEFAULT '',
            room TEXT DEFAULT '',
            occurrence_count INTEGER DEFAULT 1,
            first_seen TEXT DEFAULT (datetime('now')),
            last_seen TEXT DEFAULT (datetime('now')),
            facts TEXT DEFAULT '[]',
            est_tokens INTEGER DEFAULT 0,
            task_id TEXT DEFAULT '',
            affected_files TEXT DEFAULT '[]',
            stable_id TEXT
        );
    """))
    db.execute("""
        CREATE VIRTUAL TABLE IF NOT EXISTS ke_fts USING fts5(
            title, content, tags, category, wing, room, facts,
            error_type, root_cause,
            tokenize='porter unicode61 remove_diacritics 2'
        )
    """)
    db.commit()
    db.close()


def _apply_v21(db_path):
    """Apply migration v21 (valence/intensity) directly to a test DB."""
    db = sqlite3.connect(str(db_path))
    for stmt in [
        "ALTER TABLE knowledge_entries ADD COLUMN valence TEXT DEFAULT ''",
        "ALTER TABLE knowledge_entries ADD COLUMN intensity REAL DEFAULT 0.5",
        "CREATE INDEX IF NOT EXISTS idx_ke_intensity ON knowledge_entries(intensity DESC)",
    ]:
        try:
            db.execute(stmt)
        except sqlite3.OperationalError:
            pass
    db.commit()
    db.close()


def _cleanup(db_path):
    try:
        db_path.unlink(missing_ok=True)
    except Exception:
        pass


# ---------------------------------------------------------------------------
# 1. Migration -- v21 adds valence + intensity via migrate.py subprocess
# ---------------------------------------------------------------------------

print("\n[1] Migration v21 - valence + intensity columns")

mig_db = REPO / "_test_vi_migrate.db"
_cleanup(mig_db)
_make_base_db(mig_db)

result = subprocess.run(
    [sys.executable, str(REPO / "migrate.py"), str(mig_db)],
    capture_output=True,
    text=True,
    cwd=str(REPO),
)
mig_out = result.stdout + result.stderr

check_conn = sqlite3.connect(str(mig_db))
cols = {row[1] for row in check_conn.execute("PRAGMA table_info(knowledge_entries)").fetchall()}
schema_versions = [r[0] for r in check_conn.execute("SELECT version FROM schema_version").fetchall()]
check_conn.close()
_cleanup(mig_db)

test("migrate.py exits cleanly", result.returncode == 0,
     "exit=%d err=%s" % (result.returncode, result.stderr[:200]))
test("v21 applied (version in schema_version)", 21 in schema_versions, str(schema_versions))
test("valence column exists after v21", "valence" in cols, str(cols))
test("intensity column exists after v21", "intensity" in cols, str(cols))

# ---------------------------------------------------------------------------
# 2. learn.py -- --valence and --intensity flags write correct values
# ---------------------------------------------------------------------------

print("\n[2] learn.py - --valence / --intensity flags (INSERT path)")

learn_db = REPO / "_test_vi_learn.db"
_cleanup(learn_db)
_make_base_db(learn_db)
_apply_v21(learn_db)

learn = _load_module("learn.py", learn_db)

entry_id = learn.add_entry(
    "mistake",
    "Test valence penalty",
    "Should surface at top of briefing",
    skip_gate=True,
    skip_scan=True,
    valence="penalty",
    intensity=0.9,
)
test("add_entry returns a valid ID", isinstance(entry_id, int) and entry_id > 0, str(entry_id))

lconn = sqlite3.connect(str(learn_db))
lconn.row_factory = sqlite3.Row
row = lconn.execute(
    "SELECT valence, intensity FROM knowledge_entries WHERE id = ?", (entry_id,)
).fetchone()
lconn.close()

test("valence='penalty' written to DB",
     row is not None and row["valence"] == "penalty",
     repr(row["valence"] if row else None))
test("intensity=0.9 written to DB",
     row is not None and abs(row["intensity"] - 0.9) < 1e-9,
     repr(row["intensity"] if row else None))

entry_reward = learn.add_entry(
    "pattern",
    "Test valence reward",
    "Good pattern to follow",
    skip_gate=True,
    skip_scan=True,
    valence="reward",
    intensity=0.3,
)
lconn2 = sqlite3.connect(str(learn_db))
lconn2.row_factory = sqlite3.Row
row2 = lconn2.execute(
    "SELECT valence, intensity FROM knowledge_entries WHERE id = ?", (entry_reward,)
).fetchone()
lconn2.close()
test("valence='reward' written to DB",
     row2 is not None and row2["valence"] == "reward",
     repr(row2["valence"] if row2 else None))
test("intensity=0.3 written to DB",
     row2 is not None and abs(row2["intensity"] - 0.3) < 1e-9,
     repr(row2["intensity"] if row2 else None))

entry_default = learn.add_entry(
    "mistake",
    "Test default intensity",
    "No explicit intensity set",
    skip_gate=True,
    skip_scan=True,
)
lconn3 = sqlite3.connect(str(learn_db))
lconn3.row_factory = sqlite3.Row
row3 = lconn3.execute(
    "SELECT valence, intensity FROM knowledge_entries WHERE id = ?", (entry_default,)
).fetchone()
lconn3.close()
test("default intensity is 0.5 when not set",
     row3 is not None and (row3["intensity"] is None or abs(row3["intensity"] - 0.5) < 1e-9),
     repr(row3["intensity"] if row3 else None))

_cleanup(learn_db)

# ---------------------------------------------------------------------------
# 3. learn.py -- UPDATE path also writes valence/intensity correctly
# ---------------------------------------------------------------------------

print("\n[3] learn.py - UPDATE path (existing entry) writes valence/intensity")

upd_db = REPO / "_test_vi_update.db"
_cleanup(upd_db)
_make_base_db(upd_db)
_apply_v21(upd_db)
ulearn = _load_module("learn.py", upd_db)

uid1 = ulearn.add_entry(
    "mistake", "Reusable mistake", "First version",
    skip_gate=True, skip_scan=True,
    valence="neutral", intensity=0.4,
)
uid2 = ulearn.add_entry(
    "mistake", "Reusable mistake", "Second version",
    skip_gate=True, skip_scan=True,
    valence="penalty", intensity=0.85,
)
uconn = sqlite3.connect(str(upd_db))
uconn.row_factory = sqlite3.Row
urow = uconn.execute(
    "SELECT id, valence, intensity, occurrence_count FROM knowledge_entries WHERE title = 'Reusable mistake'"
).fetchone()
uconn.close()
_cleanup(upd_db)

test("UPDATE path: same ID on second insert", uid1 == uid2, "uid1=%s uid2=%s" % (uid1, uid2))
test("UPDATE path: valence updated to 'penalty'",
     urow is not None and urow["valence"] == "penalty",
     repr(urow["valence"] if urow else None))
test("UPDATE path: intensity updated to 0.85",
     urow is not None and abs(urow["intensity"] - 0.85) < 1e-9,
     repr(urow["intensity"] if urow else None))
test("UPDATE path: occurrence_count >= 2",
     urow is not None and urow["occurrence_count"] >= 2,
     repr(urow["occurrence_count"] if urow else None))

# ---------------------------------------------------------------------------
# 4. learn.py -- CLI validation constants
# ---------------------------------------------------------------------------

print("\n[4] learn.py - valence/intensity validation constants")

valid_valences = ("reward", "neutral", "penalty", "trauma", "")
for v in valid_valences:
    test("valence '%s' is valid" % v, v in valid_valences)

for good_i in (0.0, 0.5, 1.0, 0.99):
    test("intensity %.2f in [0,1]" % good_i, 0.0 <= good_i <= 1.0)
for bad_i in (-0.1, 1.1, 2.0):
    test("intensity %.1f outside [0,1]" % bad_i, not (0.0 <= bad_i <= 1.0))

# ---------------------------------------------------------------------------
# 5. briefing.py -- _ke_has_intensity helper and _intensity_order_expr
# ---------------------------------------------------------------------------

print("\n[5] briefing.py - _ke_has_intensity / _intensity_order_expr")

brief_db = REPO / "_test_vi_brief.db"
_cleanup(brief_db)
_make_base_db(brief_db)

briefing = _load_module("briefing.py", brief_db)

bconn_no = sqlite3.connect(str(brief_db))
test("_ke_has_intensity False when column absent",
     not briefing._ke_has_intensity(bconn_no))
bconn_no.close()

bconn_add = sqlite3.connect(str(brief_db))
bconn_add.execute("ALTER TABLE knowledge_entries ADD COLUMN intensity REAL DEFAULT 0.5")
bconn_add.commit()
test("_ke_has_intensity True after column added",
     briefing._ke_has_intensity(bconn_add))
bconn_add.close()
_cleanup(brief_db)

expr = briefing._intensity_order_expr()
test("_intensity_order_expr uses COALESCE(ke.intensity...)", "COALESCE(ke.intensity" in expr)
test("_intensity_order_expr includes confidence", "confidence" in expr)
test("_intensity_order_expr includes DESC", "DESC" in expr)

expr_t = briefing._intensity_order_expr("t")
test("_intensity_order_expr respects custom alias", "COALESCE(t.intensity" in expr_t)

# ---------------------------------------------------------------------------
# 6. Ranking: high-intensity entry ranks above low-intensity entry
# ---------------------------------------------------------------------------

print("\n[6] Ranking - high-intensity entries rank first")

rank_db = REPO / "_test_vi_rank.db"
_cleanup(rank_db)
_make_base_db(rank_db)
_apply_v21(rank_db)

rlearn = _load_module("learn.py", rank_db)

low_id = rlearn.add_entry(
    "mistake", "Low intensity valence mistake", "minor warning low priority",
    skip_gate=True, skip_scan=True,
    confidence=0.8, valence="penalty", intensity=0.2,
)
high_id = rlearn.add_entry(
    "mistake", "High intensity valence mistake", "critical warning high priority",
    skip_gate=True, skip_scan=True,
    confidence=0.8, valence="penalty", intensity=0.9,
)

rank_brief = _load_module("briefing.py", rank_db)
rank_conn = sqlite3.connect(str(rank_db))
rank_conn.row_factory = sqlite3.Row
rank_results = rank_brief.search_knowledge_entries(rank_conn, "valence mistake", "mistake", limit=5)
rank_conn.close()
_cleanup(rank_db)

ids_in_order = [r.get("id") for r in rank_results]
both_present = high_id in ids_in_order and low_id in ids_in_order
test("both entries returned in results",
     both_present,
     "ids=%s high=%s low=%s" % (ids_in_order, high_id, low_id))
if both_present:
    test("high-intensity before low-intensity in results",
         ids_in_order.index(high_id) < ids_in_order.index(low_id),
         "order=%s high=%s low=%s" % (ids_in_order, high_id, low_id))

# ---------------------------------------------------------------------------
# Summary
# ---------------------------------------------------------------------------

print("\n" + "=" * 50)
print("Results: %d passed, %d failed" % (PASS, FAIL))
if FAIL > 0:
    sys.exit(1)
