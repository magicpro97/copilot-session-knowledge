#!/usr/bin/env python3
"""
test_workflow_health.py — Tests for workflow-health.py heuristics.

Uses in-memory SQLite fixtures and temporary files.
Never touches the real session-knowledge.db, skill-metrics.db, or any live data.

Run: python3 test_workflow_health.py
"""

import importlib.util
import json
import os
import sqlite3
import sys
import tempfile
import time
from datetime import datetime, timezone
from pathlib import Path

if os.name == "nt":
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

# ---------------------------------------------------------------------------
# Load module under test
# ---------------------------------------------------------------------------
_HERE = Path(__file__).parent
_MOD_PATH = _HERE / "workflow-health.py"
spec = importlib.util.spec_from_file_location("workflow_health", str(_MOD_PATH))
wh = importlib.util.module_from_spec(spec)
spec.loader.exec_module(wh)

# ---------------------------------------------------------------------------
# Test harness
# ---------------------------------------------------------------------------
PASS = 0
FAIL = 0


def test(name: str, condition: bool, detail: str = "") -> None:
    global PASS, FAIL
    if condition:
        PASS += 1
        print(f"  ✅ {name}")
    else:
        FAIL += 1
        print(f"  ❌ {name}" + (f" — {detail}" if detail else ""))


# ---------------------------------------------------------------------------
# In-memory DB helpers
# ---------------------------------------------------------------------------
_DB_COUNTER = [0]


def _new_uri() -> str:
    _DB_COUNTER[0] += 1
    return f"file:wh_test_{_DB_COUNTER[0]}?mode=memory&cache=shared"


_SESSIONS_SCHEMA = """
CREATE TABLE sessions (
    id TEXT PRIMARY KEY,
    path TEXT NOT NULL DEFAULT '',
    summary TEXT DEFAULT '',
    total_checkpoints INTEGER DEFAULT 0,
    total_research INTEGER DEFAULT 0,
    total_files INTEGER DEFAULT 0,
    has_plan INTEGER DEFAULT 0,
    source TEXT DEFAULT 'copilot',
    indexed_at TEXT,
    file_mtime REAL,
    indexed_at_r REAL,
    fts_indexed_at REAL,
    event_count_estimate INTEGER DEFAULT 0,
    file_size_bytes INTEGER DEFAULT 0
)
"""

_KE_SCHEMA = """
CREATE TABLE knowledge_entries (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id TEXT NOT NULL,
    category TEXT,
    title TEXT,
    content TEXT,
    confidence REAL DEFAULT 0.5,
    occurrence_count INTEGER DEFAULT 1,
    last_seen TEXT
)
"""


def _make_sessions_db(uri: str) -> sqlite3.Connection:
    """Create an in-memory DB with sessions + knowledge_entries tables."""
    db = sqlite3.connect(uri, uri=True)
    db.row_factory = sqlite3.Row
    db.execute(_SESSIONS_SCHEMA)
    db.execute(_KE_SCHEMA)
    db.commit()
    return db


# ---------------------------------------------------------------------------
# Tests: Heuristic 1 — heavy_sessions
# ---------------------------------------------------------------------------
print("\n[Heuristic 1] heavy_sessions")

# Positive: large session with 0 checkpoints and many files → flagged
uri = _new_uri()
db = _make_sessions_db(uri)
db.execute(
    "INSERT INTO sessions (id, path, file_size_bytes, total_checkpoints, total_files, indexed_at) "
    "VALUES (?, ?, ?, ?, ?, ?)",
    ("sess-heavy-001", "/foo", 600 * 1024, 0, 15, "2025-01-01"),
)
db.commit()
findings = wh.check_heavy_sessions(db)
db.close()
test(
    "positive: large session (600KB, 0 chkpts, 15 files) is flagged",
    len(findings) == 1 and findings[0]["id"] == "heavy_sessions",
    str(findings),
)

# Negative: same size but has 1 checkpoint → not flagged
uri = _new_uri()
db = _make_sessions_db(uri)
db.execute(
    "INSERT INTO sessions (id, path, file_size_bytes, total_checkpoints, total_files, indexed_at) "
    "VALUES (?, ?, ?, ?, ?, ?)",
    ("sess-ckpt-001", "/bar", 600 * 1024, 1, 15, "2025-01-01"),
)
db.commit()
findings = wh.check_heavy_sessions(db)
db.close()
test(
    "negative: large session with 1 checkpoint is not flagged",
    len(findings) == 0,
    str(findings),
)

# Edge: exactly at threshold (500KB exactly) → NOT flagged (must be strictly >)
uri = _new_uri()
db = _make_sessions_db(uri)
db.execute(
    "INSERT INTO sessions (id, path, file_size_bytes, total_checkpoints, total_files, indexed_at) "
    "VALUES (?, ?, ?, ?, ?, ?)",
    ("sess-edge-001", "/baz", 500 * 1024, 0, 15, "2025-01-01"),
)
db.commit()
findings = wh.check_heavy_sessions(db)
db.close()
test(
    "edge: session at exactly 500KB threshold is not flagged (must be >500KB)",
    len(findings) == 0,
    str(findings),
)

# Edge: large size, 0 checkpoints but total_files == 10 (not > 10) → not flagged
uri = _new_uri()
db = _make_sessions_db(uri)
db.execute(
    "INSERT INTO sessions (id, path, file_size_bytes, total_checkpoints, total_files, indexed_at) "
    "VALUES (?, ?, ?, ?, ?, ?)",
    ("sess-edge-002", "/qux", 600 * 1024, 0, 10, "2025-01-01"),
)
db.commit()
findings = wh.check_heavy_sessions(db)
db.close()
test(
    "edge: session with total_files == 10 (not > 10) is not flagged",
    len(findings) == 0,
    str(findings),
)

# Severity check
uri = _new_uri()
db = _make_sessions_db(uri)
db.execute(
    "INSERT INTO sessions (id, path, file_size_bytes, total_checkpoints, total_files, indexed_at) "
    "VALUES (?, ?, ?, ?, ?, ?)",
    ("sess-sev-001", "/s", 1024 * 1024, 0, 20, "2025-01-01"),
)
db.commit()
findings = wh.check_heavy_sessions(db)
db.close()
test(
    "severity is 'warning' for heavy_sessions",
    len(findings) == 1 and findings[0]["severity"] == "warning",
    str(findings),
)

# ---------------------------------------------------------------------------
# Tests: Heuristic 2 — low_yield_sessions
# ---------------------------------------------------------------------------
print("\n[Heuristic 2] low_yield_sessions")

# Positive: indexed session with >20 events and 0 knowledge entries → flagged
uri = _new_uri()
db = _make_sessions_db(uri)
db.execute(
    "INSERT INTO sessions (id, path, event_count_estimate, indexed_at) VALUES (?, ?, ?, ?)",
    ("sess-ly-001", "/lypath", 25, "2025-01-01T00:00:00"),
)
db.commit()
findings = wh.check_low_yield_sessions(db)
db.close()
test(
    "positive: indexed session with 25 events and 0 entries is flagged",
    len(findings) == 1 and findings[0]["id"] == "low_yield_sessions",
    str(findings),
)

# Negative: session has knowledge entries → not flagged
uri = _new_uri()
db = _make_sessions_db(uri)
db.execute(
    "INSERT INTO sessions (id, path, event_count_estimate, indexed_at) VALUES (?, ?, ?, ?)",
    ("sess-ly-002", "/path2", 30, "2025-01-01T00:00:00"),
)
db.execute(
    "INSERT INTO knowledge_entries (session_id, category, title) VALUES (?, ?, ?)",
    ("sess-ly-002", "mistake", "some entry"),
)
db.commit()
findings = wh.check_low_yield_sessions(db)
db.close()
test(
    "negative: session with knowledge entries is not flagged",
    len(findings) == 0,
    str(findings),
)

# Edge: event_count_estimate == 20 (not > 20) → not flagged
uri = _new_uri()
db = _make_sessions_db(uri)
db.execute(
    "INSERT INTO sessions (id, path, event_count_estimate, indexed_at) VALUES (?, ?, ?, ?)",
    ("sess-ly-003", "/path3", 20, "2025-01-01T00:00:00"),
)
db.commit()
findings = wh.check_low_yield_sessions(db)
db.close()
test(
    "edge: session with exactly 20 events (not > 20) is not flagged",
    len(findings) == 0,
    str(findings),
)

# Edge: session not fully indexed (indexed_at IS NULL) → not flagged even with many events
uri = _new_uri()
db = _make_sessions_db(uri)
db.execute(
    "INSERT INTO sessions (id, path, event_count_estimate, indexed_at) VALUES (?, ?, ?, NULL)",
    ("sess-ly-004", "/path4", 50),
)
db.commit()
findings = wh.check_low_yield_sessions(db)
db.close()
test(
    "edge: un-indexed session (indexed_at IS NULL) is excluded from low_yield check",
    len(findings) == 0,
    str(findings),
)

# Severity check
uri = _new_uri()
db = _make_sessions_db(uri)
db.execute(
    "INSERT INTO sessions (id, path, event_count_estimate, indexed_at) VALUES (?, ?, ?, ?)",
    ("sess-ly-005", "/path5", 100, "2025-01-01T00:00:00"),
)
db.commit()
findings = wh.check_low_yield_sessions(db)
db.close()
test(
    "severity is 'warning' for low_yield_sessions",
    len(findings) == 1 and findings[0]["severity"] == "warning",
    str(findings),
)

# ---------------------------------------------------------------------------
# Tests: Heuristic 3 — stale_research_packs
# ---------------------------------------------------------------------------
print("\n[Heuristic 3] stale_research_packs")

# Negative: no scout config → skip entirely (no findings)
with tempfile.TemporaryDirectory() as td:
    tdir = Path(td)
    nonexistent_config = tdir / "no-scout-config.json"
    nonexistent_pack = tdir / ".trend-scout-research-pack.json"
    findings = wh.check_stale_research_packs(
        scout_config_path=nonexistent_config,
        research_pack_path=nonexistent_pack,
    )
test(
    "negative: no scout config → skip (0 findings)",
    len(findings) == 0,
    str(findings),
)

# Edge: config exists but pack file missing → warning
with tempfile.TemporaryDirectory() as td:
    tdir = Path(td)
    config_path = tdir / "trend-scout-config.json"
    config_path.write_text('{"target_repo": "foo/bar"}', encoding="utf-8")
    missing_pack = tdir / ".trend-scout-research-pack.json"
    findings = wh.check_stale_research_packs(
        scout_config_path=config_path,
        research_pack_path=missing_pack,
    )
test(
    "edge: config exists but pack missing → warning",
    len(findings) == 1 and findings[0]["severity"] == "warning" and findings[0]["id"] == "stale_research_packs",
    str(findings),
)

# Positive: config exists, pack is older than 7 days → warning
with tempfile.TemporaryDirectory() as td:
    tdir = Path(td)
    config_path = tdir / "trend-scout-config.json"
    config_path.write_text('{"target_repo": "foo/bar"}', encoding="utf-8")
    pack_path = tdir / ".trend-scout-research-pack.json"
    pack_path.write_text('{"topics": []}', encoding="utf-8")
    # Set mtime to 8 days ago
    old_ts = time.time() - 8 * 86400
    os.utime(str(pack_path), (old_ts, old_ts))
    findings = wh.check_stale_research_packs(
        scout_config_path=config_path,
        research_pack_path=pack_path,
    )
test(
    "positive: pack older than 7 days → stale_research_packs warning",
    len(findings) == 1 and findings[0]["id"] == "stale_research_packs",
    str(findings),
)

# Negative: config exists, pack is fresh (1 day old) → no findings
with tempfile.TemporaryDirectory() as td:
    tdir = Path(td)
    config_path = tdir / "trend-scout-config.json"
    config_path.write_text('{"target_repo": "foo/bar"}', encoding="utf-8")
    pack_path = tdir / ".trend-scout-research-pack.json"
    pack_path.write_text('{"topics": []}', encoding="utf-8")
    # Set mtime to 1 day ago (fresh)
    recent_ts = time.time() - 1 * 86400
    os.utime(str(pack_path), (recent_ts, recent_ts))
    findings = wh.check_stale_research_packs(
        scout_config_path=config_path,
        research_pack_path=pack_path,
    )
test(
    "negative: fresh pack (1 day old) → no findings",
    len(findings) == 0,
    str(findings),
)

# Edge: pack is exactly at threshold (just under 7 days) → no findings
with tempfile.TemporaryDirectory() as td:
    tdir = Path(td)
    config_path = tdir / "trend-scout-config.json"
    config_path.write_text('{"target_repo": "foo/bar"}', encoding="utf-8")
    pack_path = tdir / ".trend-scout-research-pack.json"
    pack_path.write_text('{"topics": []}', encoding="utf-8")
    # Set mtime to 6.9 days ago (safely under the 7-day threshold)
    exact_ts = time.time() - (7 * 86400 - 3600)
    os.utime(str(pack_path), (exact_ts, exact_ts))
    findings = wh.check_stale_research_packs(
        scout_config_path=config_path,
        research_pack_path=pack_path,
    )
test(
    "edge: pack just under 7-day threshold is not flagged",
    len(findings) == 0,
    str(findings),
)

# ---------------------------------------------------------------------------
# Tests: Heuristic 4 — unused_skills
# ---------------------------------------------------------------------------
print("\n[Heuristic 4] unused_skills")

_SKILL_METRICS_SCHEMA = """
CREATE TABLE tentacle_outcomes (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    tentacle_name TEXT,
    tentacle_id TEXT,
    outcome_status TEXT,
    recorded_at TEXT
);
CREATE TABLE tentacle_outcome_skills (
    outcome_id INTEGER,
    skill_name TEXT
);
"""


def _make_skill_metrics_db(db_path: Path) -> None:
    """Create a minimal skill-metrics.db fixture."""
    db = sqlite3.connect(str(db_path))
    db.executescript(_SKILL_METRICS_SCHEMA)
    db.commit()
    db.close()


# Positive: skill deployed but never used → unused_skills finding (info severity)
with tempfile.TemporaryDirectory() as td:
    tdir = Path(td)
    # Create deployed skill
    skill_dir = tdir / "skills" / "my-skill"
    skill_dir.mkdir(parents=True)
    (skill_dir / "SKILL.md").write_text("# My Skill", encoding="utf-8")
    # Create empty metrics DB (no usage)
    metrics_db_path = tdir / "skill-metrics.db"
    _make_skill_metrics_db(metrics_db_path)
    findings = wh.check_unused_skills(
        skills_dir=tdir / "skills",
        skill_metrics_db=metrics_db_path,
    )
test(
    "positive: deployed skill with no usage → unused_skills finding",
    len(findings) == 1 and findings[0]["id"] == "unused_skills",
    str(findings),
)

# Info severity check
with tempfile.TemporaryDirectory() as td:
    tdir = Path(td)
    skill_dir = tdir / "skills" / "another-skill"
    skill_dir.mkdir(parents=True)
    (skill_dir / "SKILL.md").write_text("# Another Skill", encoding="utf-8")
    metrics_db_path = tdir / "skill-metrics.db"
    _make_skill_metrics_db(metrics_db_path)
    findings = wh.check_unused_skills(
        skills_dir=tdir / "skills",
        skill_metrics_db=metrics_db_path,
    )
test(
    "unused_skills severity is 'info' (not warning)",
    len(findings) == 1 and findings[0]["severity"] == "info",
    str(findings),
)

# Negative: skill used within last 30 days → not flagged
with tempfile.TemporaryDirectory() as td:
    tdir = Path(td)
    skill_dir = tdir / "skills" / "used-skill"
    skill_dir.mkdir(parents=True)
    (skill_dir / "SKILL.md").write_text("# Used Skill", encoding="utf-8")
    metrics_db_path = tdir / "skill-metrics.db"
    _make_skill_metrics_db(metrics_db_path)
    # Insert a recent usage record
    recent_ts = datetime.fromtimestamp(time.time() - 5 * 86400, tz=timezone.utc).isoformat()
    db = sqlite3.connect(str(metrics_db_path))
    db.execute(
        "INSERT INTO tentacle_outcomes (tentacle_name, outcome_status, recorded_at) VALUES (?, ?, ?)",
        ("some-tentacle", "success", recent_ts),
    )
    db.execute(
        "INSERT INTO tentacle_outcome_skills (outcome_id, skill_name) VALUES (?, ?)",
        (1, "used-skill"),
    )
    db.commit()
    db.close()
    findings = wh.check_unused_skills(
        skills_dir=tdir / "skills",
        skill_metrics_db=metrics_db_path,
    )
test(
    "negative: skill used within last 30 days → not flagged",
    len(findings) == 0,
    str(findings),
)

# Edge: skill-metrics.db missing → skip gracefully (no crash, no findings)
with tempfile.TemporaryDirectory() as td:
    tdir = Path(td)
    skill_dir = tdir / "skills" / "some-skill"
    skill_dir.mkdir(parents=True)
    (skill_dir / "SKILL.md").write_text("# Some Skill", encoding="utf-8")
    nonexistent_metrics = tdir / "no-metrics.db"
    findings = wh.check_unused_skills(
        skills_dir=tdir / "skills",
        skill_metrics_db=nonexistent_metrics,
    )
test(
    "edge: skill-metrics.db missing → skip gracefully (0 findings)",
    len(findings) == 0,
    str(findings),
)

# Edge: no SKILL.md files in skills dir → no findings
with tempfile.TemporaryDirectory() as td:
    tdir = Path(td)
    empty_skills_dir = tdir / "skills"
    empty_skills_dir.mkdir()
    metrics_db_path = tdir / "skill-metrics.db"
    _make_skill_metrics_db(metrics_db_path)
    findings = wh.check_unused_skills(
        skills_dir=empty_skills_dir,
        skill_metrics_db=metrics_db_path,
    )
test(
    "edge: no SKILL.md files deployed → no findings",
    len(findings) == 0,
    str(findings),
)

# Edge: skill used 31 days ago (outside 30-day window) → flagged
with tempfile.TemporaryDirectory() as td:
    tdir = Path(td)
    skill_dir = tdir / "skills" / "old-skill"
    skill_dir.mkdir(parents=True)
    (skill_dir / "SKILL.md").write_text("# Old Skill", encoding="utf-8")
    metrics_db_path = tdir / "skill-metrics.db"
    _make_skill_metrics_db(metrics_db_path)
    old_ts = datetime.fromtimestamp(time.time() - 31 * 86400, tz=timezone.utc).isoformat()
    db = sqlite3.connect(str(metrics_db_path))
    db.execute(
        "INSERT INTO tentacle_outcomes (tentacle_name, outcome_status, recorded_at) VALUES (?, ?, ?)",
        ("some-tentacle", "success", old_ts),
    )
    db.execute(
        "INSERT INTO tentacle_outcome_skills (outcome_id, skill_name) VALUES (?, ?)",
        (1, "old-skill"),
    )
    db.commit()
    db.close()
    findings = wh.check_unused_skills(
        skills_dir=tdir / "skills",
        skill_metrics_db=metrics_db_path,
    )
test(
    "edge: skill last used 31 days ago → flagged as unused",
    len(findings) == 1 and findings[0]["id"] == "unused_skills",
    str(findings),
)

# ---------------------------------------------------------------------------
# Tests: run_health() integration
# ---------------------------------------------------------------------------
print("\n[Integration] run_health()")

# Missing DB → returns valid dict with N/A grade and empty findings
with tempfile.TemporaryDirectory() as td:
    result = wh.run_health(db_path=Path(td) / "nonexistent.db")
test(
    "missing DB → health_grade == 'N/A' and findings == []",
    result.get("health_grade") == "N/A" and result.get("findings") == [],
    str(result),
)

# Grade A when no findings
test(
    "_grade([]) == 'A'",
    wh._grade([]) == "A",
)

# Grade B: only info findings
test(
    "_grade([{severity: info}]) == 'B'",
    wh._grade([{"severity": "info"}]) == "B",
)

# Grade C: 1 warning
test(
    "_grade([{severity: warning}]) == 'C'",
    wh._grade([{"severity": "warning"}]) == "C",
)

# Grade D: 3 warnings
test(
    "_grade([warning, warning, warning]) == 'D'",
    wh._grade([{"severity": "warning"}] * 3) == "D",
)

# Grade D: 1 critical
test(
    "_grade([{severity: critical}]) == 'D'",
    wh._grade([{"severity": "critical"}]) == "D",
)

# Grade F: 2 criticals
test(
    "_grade([critical, critical]) == 'F'",
    wh._grade([{"severity": "critical"}] * 2) == "F",
)

# ---------------------------------------------------------------------------
# Summary
# ---------------------------------------------------------------------------
print(f"\n{'='*50}")
print(f"Results: {PASS} passed, {FAIL} failed")
if FAIL:
    sys.exit(1)
else:
    print("All tests passed ✅")
