#!/usr/bin/env python3
"""
test_skill_metrics.py — Regression tests for skill-metrics.py, tentacle-status.py,
and auto-update-tools.py proxy wiring.

Run:
    python3 test_skill_metrics.py
"""

import importlib.util
import json
import os
import sqlite3
import subprocess
import sys
from pathlib import Path

if os.name == "nt":
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

REPO = Path(__file__).parent.parent
ARTIFACT_DIR = REPO / ".skill-metrics-test-artifacts"

PASS = 0
FAIL = 0


def test(name: str, passed: bool, detail: str = "") -> None:
    global PASS, FAIL
    if passed:
        PASS += 1
        print(f"  ✅ {name}")
    else:
        FAIL += 1
        print(f"  ❌ {name}" + (f" — {detail}" if detail else ""))


def load_module(mod_name: str, filename: str):
    spec = importlib.util.spec_from_file_location(mod_name, str(REPO / filename))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def reset_artifacts() -> None:
    if ARTIFACT_DIR.exists():
        for p in sorted(ARTIFACT_DIR.rglob("*"), reverse=True):
            if p.is_file():
                p.unlink(missing_ok=True)
            elif p.is_dir():
                p.rmdir()
        ARTIFACT_DIR.rmdir()
    ARTIFACT_DIR.mkdir(parents=True, exist_ok=True)


def _make_metrics_db(db_path: Path, *, with_data: bool = False) -> None:
    db = sqlite3.connect(str(db_path))
    db.executescript(
        """
        CREATE TABLE IF NOT EXISTS tentacle_outcomes (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            tentacle_name TEXT NOT NULL,
            tentacle_id TEXT,
            git_root TEXT,
            description TEXT,
            outcome_status TEXT NOT NULL,
            recorded_at TEXT NOT NULL,
            worktree_used INTEGER NOT NULL DEFAULT 0,
            worktree_path TEXT,
            verification_total INTEGER NOT NULL DEFAULT 0,
            verification_passed INTEGER NOT NULL DEFAULT 0,
            verification_failed INTEGER NOT NULL DEFAULT 0,
            todo_total INTEGER NOT NULL DEFAULT 0,
            todo_done INTEGER NOT NULL DEFAULT 0,
            learned INTEGER NOT NULL DEFAULT 0,
            duration_seconds REAL,
            summary TEXT
        );
        CREATE TABLE IF NOT EXISTS tentacle_outcome_skills (
            outcome_id INTEGER NOT NULL,
            skill_name TEXT NOT NULL,
            PRIMARY KEY (outcome_id, skill_name)
        );
        CREATE TABLE IF NOT EXISTS tentacle_verifications (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            outcome_id INTEGER,
            tentacle_name TEXT NOT NULL,
            tentacle_id TEXT,
            label TEXT NOT NULL,
            command TEXT NOT NULL,
            cwd TEXT NOT NULL,
            exit_code INTEGER NOT NULL,
            started_at TEXT NOT NULL,
            finished_at TEXT NOT NULL,
            duration_seconds REAL NOT NULL,
            log_path TEXT
        );
        """
    )
    if with_data:
        db.execute(
            "INSERT INTO tentacle_outcomes "
            "(tentacle_name, tentacle_id, outcome_status, recorded_at, "
            "verification_passed, verification_failed, todo_done, todo_total, summary) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            ("test-tentacle", "uuid-1", "completed", "2026-04-28T00:00:00+00:00", 2, 0, 3, 3, "All done"),
        )
        db.execute("INSERT INTO tentacle_outcome_skills (outcome_id, skill_name) VALUES (1, 'karpathy-guidelines')")
        db.execute(
            "INSERT INTO tentacle_verifications "
            "(outcome_id, tentacle_name, tentacle_id, label, command, cwd, "
            "exit_code, started_at, finished_at, duration_seconds) "
            "VALUES (1, 'test-tentacle', 'uuid-1', 'tests', 'python3 test.py', '/repo', "
            "0, '2026-04-28T00:00:00+00:00', '2026-04-28T00:00:05+00:00', 5.0)"
        )
    db.commit()
    db.close()


# ---------------------------------------------------------------------------
print("\n📊 skill-metrics.py — no DB")
reset_artifacts()
skill_metrics = load_module("skill_metrics_test", "skill-metrics.py")
skill_metrics.METRICS_DB_PATH = ARTIFACT_DIR / "missing-skill-metrics.db"

status_no_db = skill_metrics.collect_status()
test("no-db: db_exists is False", status_no_db["db_exists"] is False)
test("no-db: total_outcomes is 0", status_no_db["total_outcomes"] == 0)
test("no-db: tables_missing reported", len(status_no_db["tables_missing"]) > 0)
test("no-db: skill_usage is empty", status_no_db["skill_usage"] == [])
test("no-db: recent_outcomes is empty", status_no_db["recent_outcomes"] == [])

audit_no_db = skill_metrics._runtime_audit(status_no_db)
test("no-db: audit has checks", len(audit_no_db.get("checks", [])) > 0)
test("no-db: audit ok (no critical failures)", audit_no_db["ok"] is True, str(audit_no_db))

# ---------------------------------------------------------------------------
print("\n📊 skill-metrics.py — empty DB (tables present, no rows)")
empty_db_path = ARTIFACT_DIR / "empty-skill-metrics.db"
_make_metrics_db(empty_db_path, with_data=False)
skill_metrics.METRICS_DB_PATH = empty_db_path

status_empty = skill_metrics.collect_status()
test("empty-db: db_exists is True", status_empty["db_exists"] is True)
test("empty-db: tables_missing is empty", status_empty["tables_missing"] == [])
test("empty-db: total_outcomes is 0", status_empty["total_outcomes"] == 0)
test("empty-db: audit ok", skill_metrics._runtime_audit(status_empty)["ok"] is True)

# ---------------------------------------------------------------------------
print("\n📊 skill-metrics.py — populated DB")
populated_db_path = ARTIFACT_DIR / "populated-skill-metrics.db"
_make_metrics_db(populated_db_path, with_data=True)
skill_metrics.METRICS_DB_PATH = populated_db_path

status_pop = skill_metrics.collect_status()
test("populated: total_outcomes == 1", status_pop["total_outcomes"] == 1)
test("populated: outcomes_complete == 1", status_pop["outcomes_complete"] == 1)
test("populated: outcomes_with_skills == 1", status_pop["outcomes_with_skills"] == 1)
test(
    "populated: skill_usage has karpathy-guidelines",
    any(e["skill"] == "karpathy-guidelines" for e in status_pop["skill_usage"]),
)
test("populated: total_verifications == 1", status_pop["total_verifications"] == 1)
test("populated: verifications_passed == 1", status_pop["verifications_passed"] == 1)
test("populated: recent_outcomes non-empty", len(status_pop["recent_outcomes"]) > 0)
test(
    "populated: recent outcome has expected fields",
    "tentacle_name" in status_pop["recent_outcomes"][0],
)

# format_status runs without error
formatted = skill_metrics.format_status(status_pop)
test("populated: format_status returns non-empty string", bool(formatted))

# ---------------------------------------------------------------------------
print("\n🐙 tentacle-status.py — minimal octogent dir")
tentacle_status = load_module("tentacle_status_test", "tentacle-status.py")
tentacle_status.METRICS_DB_PATH = ARTIFACT_DIR / "missing.db"

# Build a fake octogent dir with one tentacle meta.json
fake_octogent = ARTIFACT_DIR / ".octogent" / "tentacles"
fake_tent_dir = fake_octogent / "my-tentacle"
fake_tent_dir.mkdir(parents=True, exist_ok=True)
(fake_tent_dir / "meta.json").write_text(
    json.dumps(
        {
            "name": "my-tentacle",
            "tentacle_id": "abc-123",
            "status": "idle",
            "description": "Test tentacle",
            "created_at": "2026-04-28T00:00:00+00:00",
            "scope": ["foo.py"],
            "worktree": {"prepared": True, "path": "/fake/worktree/path"},
            "verifications": [
                {"label": "tests", "exit_code": 0},
                {"label": "lint", "exit_code": 1},
            ],
        }
    ),
    encoding="utf-8",
)
tentacle_status.OCTOGENT_DIR = fake_octogent

# Build a fake marker
fake_marker = ARTIFACT_DIR / "dispatched-subagent-active"
fake_marker.write_text(
    json.dumps(
        {
            "name": "dispatched-subagent-active",
            "ts": "1234567890",
            "active_tentacles": [
                {"name": "my-tentacle", "ts": "1234567890", "git_root": "/repo", "tentacle_id": "abc-123"}
            ],
            "dispatch_mode": "prompt",
            "written_at": "2026-04-28T00:00:00+00:00",
            "ttl_seconds": 14400,
            "sig": "abc123",
        }
    ),
    encoding="utf-8",
)
tentacle_status.MARKER_PATH = fake_marker

ts_status = tentacle_status.collect_status()
test("tentacle-status: octogent_dir_exists True", ts_status["octogent_dir_exists"] is True)
test("tentacle-status: marker_exists True", ts_status["marker"]["marker_exists"] is True)
test("tentacle-status: sig_present True", ts_status["marker"]["sig_present"] is True)
test("tentacle-status: active_tentacles count == 1", len(ts_status["marker"]["active_tentacles"]) == 1)
test("tentacle-status: total_tentacles == 1", ts_status["summary"]["total_tentacles"] == 1)
test("tentacle-status: marker_active == 1", ts_status["summary"]["marker_active"] == 1)
test("tentacle-status: with_worktree derived from nested worktree meta", ts_status["summary"]["with_worktree"] == 1)
test(
    "tentacle-status: verification_total derived from verifications list",
    ts_status["tentacles"][0]["verification_total"] == 2,
)
test(
    "tentacle-status: verification_passed derived from verifications list",
    ts_status["tentacles"][0]["verification_passed"] == 1,
)
test(
    "tentacle-status: verification_failed derived from verifications list",
    ts_status["tentacles"][0]["verification_failed"] == 1,
)

health = tentacle_status.runtime_health(ts_status)
test("tentacle-status: health ok with valid setup", health["ok"] is True)
test("tentacle-status: health reports active_tentacles", health["active_tentacles"] == 1)

audit_ts = tentacle_status._runtime_audit(ts_status)
test("tentacle-status: audit has checks", len(audit_ts.get("checks", [])) > 0)
test("tentacle-status: audit ok", audit_ts["ok"] is True)

# format_status produces non-empty string
formatted_ts = tentacle_status.format_status(ts_status)
test("tentacle-status: format_status non-empty", bool(formatted_ts))

# ---------------------------------------------------------------------------
print("\n🧭 auto-update-tools.py — tentacle and skill-metrics proxy wiring")
auto_update = load_module("auto_update_skill_ops_test", "auto-update-tools.py")
auto_update.TOOLS_DIR = ARTIFACT_DIR

# Create fake proxy scripts
fake_tent_status = ARTIFACT_DIR / "tentacle-status.py"
fake_tent_status.write_text("print('tentacle-status ok')\n", encoding="utf-8")
fake_skill_metrics = ARTIFACT_DIR / "skill-metrics.py"
fake_skill_metrics.write_text("print('skill-metrics ok')\n", encoding="utf-8")

captured: dict = {}
orig_run = auto_update.subprocess.run


def _fake_run(cmd, *args, **kwargs):
    captured["cmd"] = list(cmd)
    return subprocess.CompletedProcess(args=cmd, returncode=0)


# Test tentacle-status proxy
auto_update.subprocess.run = _fake_run
try:
    code_ts = auto_update._run_tentacle_status_surface([])
finally:
    auto_update.subprocess.run = orig_run

test("auto-update: tentacle proxy returns 0", code_ts == 0)
test(
    "auto-update: tentacle proxy calls tentacle-status.py",
    bool(captured.get("cmd")) and str(captured["cmd"][1]).endswith("tentacle-status.py"),
    str(captured.get("cmd")),
)

# Test skill-metrics proxy
auto_update.subprocess.run = _fake_run
captured.clear()
try:
    code_sm = auto_update._run_skill_metrics_surface(["--audit"])
finally:
    auto_update.subprocess.run = orig_run

test("auto-update: skill-metrics proxy returns 0", code_sm == 0)
test(
    "auto-update: skill-metrics proxy calls skill-metrics.py",
    bool(captured.get("cmd")) and str(captured["cmd"][1]).endswith("skill-metrics.py"),
    str(captured.get("cmd")),
)
test(
    "auto-update: skill-metrics proxy passes --audit arg",
    "--audit" in (captured.get("cmd") or []),
)

# Test missing-file failure paths
fake_tent_status.unlink(missing_ok=True)
code_missing_ts = auto_update._run_tentacle_status_surface([])
test("auto-update: tentacle proxy fails when script missing", code_missing_ts == 1)

fake_skill_metrics.unlink(missing_ok=True)
code_missing_sm = auto_update._run_skill_metrics_surface([])
test("auto-update: skill-metrics proxy fails when script missing", code_missing_sm == 1)

# ---------------------------------------------------------------------------
print("\n📊 skill-metrics.py — outcomes_with_passing_verification")

# Reload skill_metrics to use a fresh artifact DB
verified_db_path = ARTIFACT_DIR / "verified-skill-metrics.db"

db_verified = sqlite3.connect(str(verified_db_path))
db_verified.executescript(
    """
    CREATE TABLE IF NOT EXISTS tentacle_outcomes (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        tentacle_name TEXT NOT NULL,
        tentacle_id TEXT,
        git_root TEXT,
        description TEXT,
        outcome_status TEXT NOT NULL,
        recorded_at TEXT NOT NULL,
        worktree_used INTEGER NOT NULL DEFAULT 0,
        worktree_path TEXT,
        verification_total INTEGER NOT NULL DEFAULT 0,
        verification_passed INTEGER NOT NULL DEFAULT 0,
        verification_failed INTEGER NOT NULL DEFAULT 0,
        todo_total INTEGER NOT NULL DEFAULT 0,
        todo_done INTEGER NOT NULL DEFAULT 0,
        learned INTEGER NOT NULL DEFAULT 0,
        duration_seconds REAL,
        summary TEXT
    );
    CREATE TABLE IF NOT EXISTS tentacle_outcome_skills (
        outcome_id INTEGER NOT NULL,
        skill_name TEXT NOT NULL,
        PRIMARY KEY (outcome_id, skill_name)
    );
    CREATE TABLE IF NOT EXISTS tentacle_verifications (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        outcome_id INTEGER,
        tentacle_name TEXT NOT NULL,
        tentacle_id TEXT,
        label TEXT NOT NULL,
        command TEXT NOT NULL,
        cwd TEXT NOT NULL,
        exit_code INTEGER NOT NULL,
        started_at TEXT NOT NULL,
        finished_at TEXT NOT NULL,
        duration_seconds REAL NOT NULL,
        log_path TEXT
    );
    """
)
# Insert two outcomes: one with passing verifications, one without
db_verified.execute(
    "INSERT INTO tentacle_outcomes "
    "(tentacle_name, outcome_status, recorded_at, verification_passed, verification_failed) "
    "VALUES (?, ?, ?, ?, ?)",
    ("verified-tent", "completed", "2026-05-01T00:00:00+00:00", 2, 0),
)
db_verified.execute(
    "INSERT INTO tentacle_outcomes "
    "(tentacle_name, outcome_status, recorded_at, verification_passed, verification_failed) "
    "VALUES (?, ?, ?, ?, ?)",
    ("unverified-tent", "completed", "2026-05-01T00:00:01+00:00", 0, 0),
)
# Insert verifications for outcome id=1
db_verified.execute(
    "INSERT INTO tentacle_verifications "
    "(outcome_id, tentacle_name, tentacle_id, label, command, cwd, "
    "exit_code, started_at, finished_at, duration_seconds) "
    "VALUES (1, 'verified-tent', 'uid-v1', 'tests', 'python3 test.py', '/repo', "
    "0, '2026-05-01T00:00:00+00:00', '2026-05-01T00:00:05+00:00', 5.0)",
)
db_verified.execute(
    "INSERT INTO tentacle_verifications "
    "(outcome_id, tentacle_name, tentacle_id, label, command, cwd, "
    "exit_code, started_at, finished_at, duration_seconds) "
    "VALUES (1, 'verified-tent', 'uid-v1', 'lint', 'python3 lint.py', '/repo', "
    "0, '2026-05-01T00:00:05+00:00', '2026-05-01T00:00:06+00:00', 1.0)",
)
db_verified.commit()
db_verified.close()

skill_metrics_verified = load_module("skill_metrics_verified", "skill-metrics.py")
skill_metrics_verified.METRICS_DB_PATH = verified_db_path
status_verified = skill_metrics_verified.collect_status()

test(
    "outcomes_with_passing_verification == 1 (only verified-tent)",
    status_verified["outcomes_with_passing_verification"] == 1,
    f"got {status_verified['outcomes_with_passing_verification']}",
)
test(
    "total_outcomes == 2",
    status_verified["total_outcomes"] == 2,
    f"got {status_verified['total_outcomes']}",
)
test(
    "total_verifications == 2",
    status_verified["total_verifications"] == 2,
    f"got {status_verified['total_verifications']}",
)
test(
    "verifications_passed == 2",
    status_verified["verifications_passed"] == 2,
    f"got {status_verified['verifications_passed']}",
)
test(
    "verifications_failed == 0",
    status_verified["verifications_failed"] == 0,
    f"got {status_verified['verifications_failed']}",
)
test(
    "recent_outcomes contains verified-tent",
    any(r["tentacle_name"] == "verified-tent" for r in status_verified["recent_outcomes"]),
)
test(
    "recent_outcomes contains unverified-tent",
    any(r["tentacle_name"] == "unverified-tent" for r in status_verified["recent_outcomes"]),
)
# Audit should pass (no critical failures)
audit_verified = skill_metrics_verified._runtime_audit(status_verified)
test("audit ok", audit_verified["ok"] is True, str(audit_verified))

# ---------------------------------------------------------------------------
print("\n📊 skill-metrics.py — patch_history / skill_patch_history table")

patch_db_path = ARTIFACT_DIR / "patch-skill-metrics.db"

db_patch = sqlite3.connect(str(patch_db_path))
db_patch.executescript(
    """
    CREATE TABLE IF NOT EXISTS tentacle_outcomes (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        tentacle_name TEXT NOT NULL,
        tentacle_id TEXT,
        git_root TEXT,
        description TEXT,
        outcome_status TEXT NOT NULL,
        recorded_at TEXT NOT NULL,
        worktree_used INTEGER NOT NULL DEFAULT 0,
        worktree_path TEXT,
        verification_total INTEGER NOT NULL DEFAULT 0,
        verification_passed INTEGER NOT NULL DEFAULT 0,
        verification_failed INTEGER NOT NULL DEFAULT 0,
        todo_total INTEGER NOT NULL DEFAULT 0,
        todo_done INTEGER NOT NULL DEFAULT 0,
        learned INTEGER NOT NULL DEFAULT 0,
        duration_seconds REAL,
        summary TEXT
    );
    CREATE TABLE IF NOT EXISTS tentacle_outcome_skills (
        outcome_id INTEGER NOT NULL,
        skill_name TEXT NOT NULL,
        PRIMARY KEY (outcome_id, skill_name)
    );
    CREATE TABLE IF NOT EXISTS tentacle_verifications (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        outcome_id INTEGER,
        tentacle_name TEXT NOT NULL,
        tentacle_id TEXT,
        label TEXT NOT NULL,
        command TEXT NOT NULL,
        cwd TEXT NOT NULL,
        exit_code INTEGER NOT NULL,
        started_at TEXT NOT NULL,
        finished_at TEXT NOT NULL,
        duration_seconds REAL NOT NULL,
        log_path TEXT
    );
    CREATE TABLE IF NOT EXISTS skill_patch_history (
        id                   INTEGER PRIMARY KEY AUTOINCREMENT,
        skill_path           TEXT NOT NULL,
        patched_at           TEXT NOT NULL,
        old_text             TEXT NOT NULL,
        new_text             TEXT NOT NULL,
        occurrences_replaced INTEGER NOT NULL DEFAULT 0,
        replace_all          INTEGER NOT NULL DEFAULT 0,
        dry_run              INTEGER NOT NULL DEFAULT 0,
        validation_passed    INTEGER,
        validation_errors    INTEGER NOT NULL DEFAULT 0,
        validation_warnings  INTEGER NOT NULL DEFAULT 0
    );
    """
)
# Insert two patch history rows
db_patch.execute(
    "INSERT INTO skill_patch_history "
    "(skill_path, patched_at, old_text, new_text, occurrences_replaced, "
    "replace_all, dry_run, validation_passed, validation_errors, validation_warnings) "
    "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
    ("/skills/test-skill/SKILL.md", "2026-05-01T00:00:00+00:00", "old text", "new text", 1, 0, 0, 1, 0, 0),
)
db_patch.execute(
    "INSERT INTO skill_patch_history "
    "(skill_path, patched_at, old_text, new_text, occurrences_replaced, "
    "replace_all, dry_run, validation_passed, validation_errors, validation_warnings) "
    "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
    ("/skills/other-skill/SKILL.md", "2026-05-02T00:00:00+00:00", "another old", "another new", 3, 1, 0, 1, 0, 2),
)
db_patch.commit()
db_patch.close()

skill_metrics_patch = load_module("skill_metrics_patch", "skill-metrics.py")
skill_metrics_patch.METRICS_DB_PATH = patch_db_path
status_patch = skill_metrics_patch.collect_status()

test(
    "patch-history: total_patches == 2",
    status_patch.get("total_patches") == 2,
    f"got {status_patch.get('total_patches')}",
)
test(
    "patch-history: patch_history list has 2 entries",
    len(status_patch.get("patch_history", [])) == 2,
    f"got {len(status_patch.get('patch_history', []))}",
)
test(
    "patch-history: first entry has skill_path",
    "skill_path" in (status_patch.get("patch_history") or [{}])[0],
)
test(
    "patch-history: first entry has patched_at",
    "patched_at" in (status_patch.get("patch_history") or [{}])[0],
)
test(
    "patch-history: first entry has occurrences_replaced",
    "occurrences_replaced" in (status_patch.get("patch_history") or [{}])[0],
)
test(
    "patch-history: first entry replace_all is True",
    (status_patch.get("patch_history") or [{}])[0].get("replace_all") is True,
)

# Verify format_status includes patch history section
formatted_patch = skill_metrics_patch.format_status(status_patch)
test(
    "patch-history: format_status includes patch history heading",
    "patch" in formatted_patch.lower(),
    formatted_patch,
)

# Verify collect_status on a DB without skill_patch_history table still works
db_no_patch_path = ARTIFACT_DIR / "no-patch-skill-metrics.db"
_make_metrics_db(db_no_patch_path, with_data=True)
skill_metrics_no_patch = load_module("skill_metrics_no_patch", "skill-metrics.py")
skill_metrics_no_patch.METRICS_DB_PATH = db_no_patch_path
status_no_patch = skill_metrics_no_patch.collect_status()
test(
    "no-patch-table: total_patches defaults to 0",
    status_no_patch.get("total_patches", 0) == 0,
    f"got {status_no_patch.get('total_patches')}",
)
test(
    "no-patch-table: patch_history defaults to empty list",
    status_no_patch.get("patch_history", []) == [],
    f"got {status_no_patch.get('patch_history')}",
)
test(
    "no-patch-table: collect_status still has total_outcomes",
    status_no_patch.get("total_outcomes", 0) > 0,
    f"got {status_no_patch.get('total_outcomes')}",
)

# ---------------------------------------------------------------------------
print("\n📊 skill-metrics.py — event-level skill_usage_events table")

event_db_path = ARTIFACT_DIR / "event-skill-metrics.db"
db_event = sqlite3.connect(str(event_db_path))
db_event.executescript(
    """
    CREATE TABLE IF NOT EXISTS tentacle_outcomes (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        tentacle_name TEXT NOT NULL,
        tentacle_id TEXT,
        outcome_status TEXT NOT NULL,
        recorded_at TEXT NOT NULL,
        worktree_used INTEGER NOT NULL DEFAULT 0,
        verification_total INTEGER NOT NULL DEFAULT 0,
        verification_passed INTEGER NOT NULL DEFAULT 0,
        verification_failed INTEGER NOT NULL DEFAULT 0,
        todo_total INTEGER NOT NULL DEFAULT 0,
        todo_done INTEGER NOT NULL DEFAULT 0,
        learned INTEGER NOT NULL DEFAULT 0,
        duration_seconds REAL,
        summary TEXT
    );
    CREATE TABLE IF NOT EXISTS tentacle_outcome_skills (
        outcome_id INTEGER NOT NULL,
        skill_name TEXT NOT NULL,
        PRIMARY KEY (outcome_id, skill_name)
    );
    CREATE TABLE IF NOT EXISTS tentacle_verifications (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        outcome_id INTEGER,
        tentacle_name TEXT NOT NULL,
        tentacle_id TEXT,
        label TEXT NOT NULL,
        command TEXT NOT NULL,
        cwd TEXT NOT NULL,
        exit_code INTEGER NOT NULL,
        started_at TEXT NOT NULL,
        finished_at TEXT NOT NULL,
        duration_seconds REAL NOT NULL,
        log_path TEXT
    );
    CREATE TABLE IF NOT EXISTS skill_usage_events (
        id         INTEGER PRIMARY KEY AUTOINCREMENT,
        skill_name TEXT NOT NULL,
        event      TEXT NOT NULL,
        session_id TEXT NOT NULL,
        timestamp  TEXT NOT NULL
    );
    """
)
# Insert event rows for two skills
for skill, evt in [
    ("karpathy-guidelines", "triggered"),
    ("karpathy-guidelines", "loaded"),
    ("karpathy-guidelines", "triggered"),
    ("karpathy-guidelines", "loaded"),
    ("frontend-dev", "triggered"),
    ("frontend-dev", "skipped"),
]:
    db_event.execute(
        "INSERT INTO skill_usage_events (skill_name, event, session_id, timestamp) VALUES (?, ?, ?, ?)",
        (skill, evt, "sess-test-1", "2026-05-14T00:00:00Z"),
    )
db_event.commit()
db_event.close()

skill_metrics_event = load_module("skill_metrics_event", "skill-metrics.py")
skill_metrics_event.METRICS_DB_PATH = event_db_path
status_event = skill_metrics_event.collect_status()

test(
    "event-table: event_skill_usage is a list",
    isinstance(status_event.get("event_skill_usage"), list),
)
test(
    "event-table: total_skill_events == 6",
    status_event.get("total_skill_events") == 6,
    f"got {status_event.get('total_skill_events')}",
)
test(
    "event-table: karpathy-guidelines triggered==2",
    any(
        e["skill"] == "karpathy-guidelines" and e["event"] == "triggered" and e["count"] == 2
        for e in status_event.get("event_skill_usage", [])
    ),
    str([e for e in status_event.get("event_skill_usage", [])]),
)
test(
    "event-table: frontend-dev skipped==1",
    any(
        e["skill"] == "frontend-dev" and e["event"] == "skipped" and e["count"] == 1
        for e in status_event.get("event_skill_usage", [])
    ),
    str([e for e in status_event.get("event_skill_usage", [])]),
)

formatted_event = skill_metrics_event.format_status(status_event)
test(
    "event-table: format_status includes 'Event-level' section",
    "Event-level" in formatted_event or "event" in formatted_event.lower(),
    formatted_event[:300],
)
test(
    "event-table: format_status includes karpathy-guidelines in event section",
    "karpathy-guidelines" in formatted_event,
    formatted_event[:500],
)

# Collect_status on DB without skill_usage_events still works
no_event_db = ARTIFACT_DIR / "no-event-skill-metrics.db"
_make_metrics_db(no_event_db, with_data=True)
skill_metrics_no_event = load_module("skill_metrics_no_event", "skill-metrics.py")
skill_metrics_no_event.METRICS_DB_PATH = no_event_db
status_no_event = skill_metrics_no_event.collect_status()
test(
    "no-event-table: event_skill_usage defaults to empty list",
    status_no_event.get("event_skill_usage", []) == [],
    f"got {status_no_event.get('event_skill_usage')}",
)
test(
    "no-event-table: total_skill_events defaults to 0",
    status_no_event.get("total_skill_events", 0) == 0,
    f"got {status_no_event.get('total_skill_events')}",
)

# ---------------------------------------------------------------------------
print("\n" + "=" * 72)
print(f"PASS: {PASS}")
print(f"FAIL: {FAIL}")

if FAIL > 0:
    sys.exit(1)

print("\n✅ test_skill_metrics.py passed")
