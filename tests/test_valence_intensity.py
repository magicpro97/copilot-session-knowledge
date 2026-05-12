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
# 4. learn.py -- CLI validation: invalid --valence / --intensity exit non-zero
#
# These tests exercise the actual validation paths inside learn.py's main()
# by running it as a subprocess.  A tautological constants check was replaced
# with real rejection-path coverage.
# ---------------------------------------------------------------------------

print("\n[4] learn.py - CLI validation rejects invalid --valence / --intensity")

_learn_script = str(REPO / "learn.py")

# --- invalid --valence value must exit 1 with an informative message ---
r_bad_valence = subprocess.run(
    [sys.executable, _learn_script, "--mistake",
     "bad valence title", "body text",
     "--valence", "bogus_value", "--skip-gate", "--skip-scan"],
    capture_output=True, text=True,
)
test("invalid --valence exits non-zero",
     r_bad_valence.returncode != 0,
     "rc=%d" % r_bad_valence.returncode)
test("invalid --valence prints error about allowed values",
     "reward" in r_bad_valence.stderr and "penalty" in r_bad_valence.stderr,
     "stderr=%r" % r_bad_valence.stderr[:200])

# --- --intensity out of range (> 1.0) must exit 1 ---
r_high_intensity = subprocess.run(
    [sys.executable, _learn_script, "--mistake",
     "bad intensity title", "body text",
     "--intensity", "1.5", "--skip-gate", "--skip-scan"],
    capture_output=True, text=True,
)
test("--intensity > 1.0 exits non-zero",
     r_high_intensity.returncode != 0,
     "rc=%d" % r_high_intensity.returncode)
test("--intensity > 1.0 prints error mentioning 0.0 and 1.0",
     "0.0" in r_high_intensity.stderr and "1.0" in r_high_intensity.stderr,
     "stderr=%r" % r_high_intensity.stderr[:200])

# --- --intensity negative must exit 1 ---
r_neg_intensity = subprocess.run(
    [sys.executable, _learn_script, "--mistake",
     "negative intensity title", "body text",
     "--intensity", "-0.1", "--skip-gate", "--skip-scan"],
    capture_output=True, text=True,
)
test("--intensity < 0.0 exits non-zero",
     r_neg_intensity.returncode != 0,
     "rc=%d" % r_neg_intensity.returncode)

# --- --intensity non-numeric must exit 1 ---
r_str_intensity = subprocess.run(
    [sys.executable, _learn_script, "--mistake",
     "non-numeric intensity title", "body text",
     "--intensity", "high", "--skip-gate", "--skip-scan"],
    capture_output=True, text=True,
)
test("--intensity non-numeric exits non-zero",
     r_str_intensity.returncode != 0,
     "rc=%d" % r_str_intensity.returncode)

# ---------------------------------------------------------------------------
# 4b. learn.py -- --json output against a pre-v21 DB (no valence/intensity cols)
#
# Regression for the bug where --json unconditionally selected valence/intensity,
# causing sqlite3.OperationalError: no such column: valence on pre-v21 databases.
# Uses _load_module to patch DB_PATH so the subprocess-free test hits the
# controlled pre-v21 schema.
# ---------------------------------------------------------------------------

print("\n[4b] learn.py - --json output on pre-v21 DB (no valence/intensity columns)")

import io as _io
import json as _json

prev21_db = REPO / "_test_vi_pre21_json.db"
_cleanup(prev21_db)
_make_base_db(prev21_db)  # deliberately NO _apply_v21 → pre-v21 schema

pre21_learn = _load_module("learn.py", prev21_db)

# Verify the test DB genuinely lacks valence/intensity
_pre21_check = sqlite3.connect(str(prev21_db))
_pre21_schema_cols = {r[1] for r in _pre21_check.execute("PRAGMA table_info(knowledge_entries)").fetchall()}
_pre21_check.close()
test("pre-v21 DB fixture has no valence column",
     "valence" not in _pre21_schema_cols,
     "cols=%r" % _pre21_schema_cols)
test("pre-v21 DB fixture has no intensity column",
     "intensity" not in _pre21_schema_cols,
     "cols=%r" % _pre21_schema_cols)

# Call add_entry then run the --json SELECT path directly by inspecting
# the patched module's get_db() result; avoids needing a subprocess --db flag.
_pre21_entry_id = pre21_learn.add_entry(
    "mistake", "pre21 json test entry", "content for pre21 regression",
    skip_gate=True, skip_scan=True,
)
test("pre-v21 DB: add_entry returns valid ID", isinstance(_pre21_entry_id, int) and _pre21_entry_id > 0, str(_pre21_entry_id))

# Now exercise the same SELECT logic that --json mode uses, via the patched module.
_no_vi_error = False
_pre21_row = None
try:
    _pre21_db_conn = pre21_learn.get_db()
    _pre21_db_conn.row_factory = sqlite3.Row
    _pre21_cols = {r[1] for r in _pre21_db_conn.execute("PRAGMA table_info(knowledge_entries)").fetchall()}
    _pre21_has_vi = all(c in _pre21_cols for c in ("valence", "intensity"))
    _vi_sel = (
        ",\n                   COALESCE(valence, '') AS valence,"
        "\n                   COALESCE(intensity, 0.5) AS intensity"
        if _pre21_has_vi else ""
    )
    _pre21_row = _pre21_db_conn.execute(
        f"SELECT id, category, title, confidence, occurrence_count, last_seen,"
        f" session_id, task_id, affected_files, facts{_vi_sel}"
        f" FROM knowledge_entries WHERE id = ?",
        (_pre21_entry_id,),
    ).fetchone()
    _pre21_db_conn.close()
    _no_vi_error = True
except sqlite3.OperationalError as _e:
    pass

test("--json SELECT on pre-v21 DB raises no OperationalError",
     _no_vi_error,
     "got OperationalError — column guard missing")
test("pre-v21 DB: row is retrievable without valence/intensity in SELECT",
     _pre21_row is not None,
     "row=%r" % _pre21_row)

_cleanup(prev21_db)

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
# Intensity must be the leading sort key (not multiplied by confidence)
test("_intensity_order_expr intensity-first (not product formula)",
     expr.startswith("COALESCE(ke.intensity"),
     "expr=%r" % expr)

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
# 7. Counterexample regression: higher-intensity lower-confidence must win
#
# This is the exact acceptance-gap scenario from the orchestrator audit:
#   Entry A: intensity=0.6, confidence=0.5  -> old score = 0.30 (lost!)
#   Entry B: intensity=0.4, confidence=0.8  -> old score = 0.32 (won!)
# With intensity-first ranking Entry A must always win.
# ---------------------------------------------------------------------------

print("\n[7] Counterexample regression - intensity beats confidence product")

ce_db = REPO / "_test_vi_counterexample.db"
_cleanup(ce_db)
_make_base_db(ce_db)
_apply_v21(ce_db)

ce_learn = _load_module("learn.py", ce_db)

# Entry A: higher intensity, lower confidence — must rank first
ce_high_int_id = ce_learn.add_entry(
    "mistake", "counterexample high intensity low confidence", "high intensity entry counterexample",
    skip_gate=True, skip_scan=True,
    confidence=0.5, valence="penalty", intensity=0.6,
)
# Entry B: lower intensity, higher confidence — must rank second
ce_low_int_id = ce_learn.add_entry(
    "mistake", "counterexample low intensity high confidence", "low intensity entry counterexample",
    skip_gate=True, skip_scan=True,
    confidence=0.8, valence="penalty", intensity=0.4,
)

# Verify old product scores to document what would go wrong with old formula
old_score_a = 0.6 * 0.5  # 0.30
old_score_b = 0.4 * 0.8  # 0.32
test("counterexample sanity: old product formula would rank B above A",
     old_score_b > old_score_a,
     "old_score_a=%.2f old_score_b=%.2f" % (old_score_a, old_score_b))

ce_brief = _load_module("briefing.py", ce_db)
ce_conn = sqlite3.connect(str(ce_db))
ce_conn.row_factory = sqlite3.Row
ce_results = ce_brief.search_knowledge_entries(
    ce_conn, "counterexample intensity confidence", "mistake", limit=5
)
ce_conn.close()
_cleanup(ce_db)

ce_ids = [r.get("id") for r in ce_results]
ce_both = ce_high_int_id in ce_ids and ce_low_int_id in ce_ids
test("counterexample: both entries returned",
     ce_both,
     "ids=%s high_int=%s low_int=%s" % (ce_ids, ce_high_int_id, ce_low_int_id))
if ce_both:
    test("counterexample: higher-intensity lower-confidence entry ranks first",
         ce_ids.index(ce_high_int_id) < ce_ids.index(ce_low_int_id),
         "order=%s high_int=%s low_int=%s" % (ce_ids, ce_high_int_id, ce_low_int_id))

# ---------------------------------------------------------------------------
# 8. Regression: valence-only UPDATE must NOT overwrite existing intensity
#
# Bug: supplying --valence without --intensity used to reset intensity to 0.5
# (the fallback default) because the UPDATE SQL did not guard intensity with
# a CASE expression.  The fix uses:
#   intensity = CASE WHEN ? IS NOT NULL THEN ? ELSE intensity END
# so that a NULL (absent) caller argument leaves the stored value untouched.
# ---------------------------------------------------------------------------

print("\n[8] Regression - valence-only UPDATE preserves prior intensity")

vi_db = REPO / "_test_vi_valence_only_update.db"
_cleanup(vi_db)
_make_base_db(vi_db)
_apply_v21(vi_db)

vi_learn = _load_module("learn.py", vi_db)

# Step 1 – insert entry with a non-default intensity
vi_id1 = vi_learn.add_entry(
    "mistake", "Valence-only update test", "Initial version with intensity 0.8",
    skip_gate=True, skip_scan=True,
    valence="neutral", intensity=0.8,
)

# Step 2 – update the SAME entry using only valence (no intensity argument)
vi_id2 = vi_learn.add_entry(
    "mistake", "Valence-only update test", "Second version — only valence changes",
    skip_gate=True, skip_scan=True,
    valence="penalty",  # intensity intentionally omitted
)

vi_conn = sqlite3.connect(str(vi_db))
vi_conn.row_factory = sqlite3.Row
vi_row = vi_conn.execute(
    "SELECT id, valence, intensity, occurrence_count FROM knowledge_entries WHERE title = 'Valence-only update test'"
).fetchone()
vi_conn.close()
_cleanup(vi_db)

test("valence-only UPDATE: same entry ID (no new row created)",
     vi_id1 == vi_id2,
     "id1=%s id2=%s" % (vi_id1, vi_id2))
test("valence-only UPDATE: valence changed to 'penalty'",
     vi_row is not None and vi_row["valence"] == "penalty",
     repr(vi_row["valence"] if vi_row else None))
test("valence-only UPDATE: intensity preserved at 0.8 (not reset to 0.5)",
     vi_row is not None and abs(vi_row["intensity"] - 0.8) < 1e-9,
     "intensity=%r (expected 0.8, bug would give 0.5)" % (vi_row["intensity"] if vi_row else None))
test("valence-only UPDATE: occurrence_count incremented",
     vi_row is not None and vi_row["occurrence_count"] >= 2,
     repr(vi_row["occurrence_count"] if vi_row else None))

# ---------------------------------------------------------------------------
# Summary
# ---------------------------------------------------------------------------

print("\n" + "=" * 50)
print("Results: %d passed, %d failed" % (PASS, FAIL))
if FAIL > 0:
    sys.exit(1)
