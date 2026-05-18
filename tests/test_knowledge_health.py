#!/usr/bin/env python3
"""
Tests for knowledge-health.py --insights and supporting functions.
Uses synthetic in-memory SQLite DBs; never touches the real knowledge.db.
"""

import importlib.util
import json
import os
import shutil
import sqlite3
import sys
import tempfile
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
_HERE = Path(__file__).parent.parent
_MOD_PATH = _HERE / "knowledge-health.py"
spec = importlib.util.spec_from_file_location("knowledge_health", str(_MOD_PATH))
kh = importlib.util.module_from_spec(spec)
spec.loader.exec_module(kh)

# ---------------------------------------------------------------------------
# Shared in-memory DB helpers
# ---------------------------------------------------------------------------
_DB_COUNTER = [0]


def _new_uri():
    _DB_COUNTER[0] += 1
    return f"file:kh_test_{_DB_COUNTER[0]}?mode=memory&cache=shared"


_KE_SCHEMA = """
CREATE TABLE knowledge_entries (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id TEXT,
    document_id INTEGER,
    category TEXT,
    title TEXT,
    content TEXT,
    tags TEXT,
    confidence REAL DEFAULT 0.5,
    occurrence_count INTEGER DEFAULT 1,
    first_seen TEXT,
    last_seen TEXT,
    source TEXT,
    topic_key TEXT,
    revision_count INTEGER DEFAULT 0,
    content_hash TEXT,
    wing TEXT,
    room TEXT,
    facts TEXT,
    est_tokens INTEGER,
    task_id TEXT,
    affected_files TEXT,
    source_section TEXT,
    source_file TEXT,
    start_line INTEGER,
    end_line INTEGER,
    code_language TEXT,
    code_snippet TEXT,
    stable_id TEXT
)
"""

_RELATIONS_SCHEMA = "CREATE TABLE knowledge_relations (id INTEGER PRIMARY KEY, source_id INTEGER, target_id INTEGER, relation_type TEXT)"
_ENTITY_REL_SCHEMA = (
    "CREATE TABLE entity_relations (id INTEGER PRIMARY KEY, source TEXT, target TEXT, relation_type TEXT)"
)
_EMBEDDINGS_SCHEMA = (
    "CREATE TABLE embeddings ("
    "id INTEGER PRIMARY KEY, "
    "source_type TEXT NOT NULL DEFAULT 'knowledge', "
    "source_id INTEGER, "
    "vector BLOB)"
)
_SCHEMA_VER_SCHEMA = "CREATE TABLE schema_version (version INTEGER, name TEXT)"


def _make_db(uri, with_relations=True, with_embeddings=True):
    """Create a fresh synthetic DB at the given shared-memory URI."""
    db = sqlite3.connect(uri, uri=True)
    db.row_factory = sqlite3.Row
    db.execute(_KE_SCHEMA)
    db.execute(_SCHEMA_VER_SCHEMA)
    if with_relations:
        db.execute(_RELATIONS_SCHEMA)
        db.execute(_ENTITY_REL_SCHEMA)
    if with_embeddings:
        db.execute(_EMBEDDINGS_SCHEMA)
    db.commit()
    return db


def _get_db_factory(uri):
    """Return a get_db() replacement that returns a new connection to uri."""

    def _get_db():
        conn = sqlite3.connect(uri, uri=True)
        conn.row_factory = sqlite3.Row
        return conn

    return _get_db


def _insert_entries(db, entries):
    for e in entries:
        db.execute(
            """INSERT INTO knowledge_entries
               (category, title, content, confidence, occurrence_count,
                first_seen, last_seen, session_id, affected_files)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                e.get("category", "mistake"),
                e.get("title", "untitled"),
                e.get("content", ""),
                e.get("confidence", 0.5),
                e.get("occurrence_count", 1),
                e.get("first_seen", "2025-01-01"),
                e.get("last_seen", "2025-01-01"),
                e.get("session_id", "sess1"),
                e.get("affected_files", None),
            ),
        )
    db.commit()


# ---------------------------------------------------------------------------
# Minimal test harness (mirrors repo convention)
# ---------------------------------------------------------------------------
_PASS = 0
_FAIL = 0
_ERRORS = []


def test(name, condition, detail=""):
    global _PASS, _FAIL
    if condition:
        _PASS += 1
        print(f"  ✅ {name}")
    else:
        _FAIL += 1
        msg = f"  ❌ {name}" + (f" — {detail}" if detail else "")
        _ERRORS.append(msg)
        print(msg)


def section(title):
    print(f"\n{title}")
    print("-" * len(title))


# ===========================================================================
# _is_file_path tests
# ===========================================================================

section("_is_file_path — valid file paths")
test("plain script", kh._is_file_path("scripts/fix-ssl.sh"))
test("nested path with dots", kh._is_file_path("composeApp/src/main/kotlin/Foo.kt"))
test("config file", kh._is_file_path("cloudflared/config.yml"))
test("dot-relative", kh._is_file_path("./scripts/run.sh"))
test("dot-relative extensionless build file", kh._is_file_path("./Makefile"))
test("pbxproj", kh._is_file_path("iosApp/iosApp.xcodeproj/project.pbxproj"))
test("plain extension", kh._is_file_path("main.py"))

section("_is_file_path — prose/noise strings (should return False)")
test("absolute path", not kh._is_file_path("/Users/linhn/project/main.py"))
test("system plist path", not kh._is_file_path("/Library/LaunchAgents/com.foo.plist"))
test("sentence with colon", not kh._is_file_path("Usage: run this script"))
test("long prose", not kh._is_file_path("Changed: custom sound selection (line ~29-32), scheduleReminders() method"))
test("prose with spaces", not kh._is_file_path("macOS LaunchAgent that triggers autostart-screener.sh"))
test("colon in middle", not kh._is_file_path("iosApp: changed entitlements"))
test("parent-relative path", not kh._is_file_path("../outside-repo.py"))
test("empty string", not kh._is_file_path(""))
test("only whitespace", not kh._is_file_path("   "))
test("very long string", not kh._is_file_path("a" * 200 + ".py"))
test("backtick prose", not kh._is_file_path("`alarm_default_30s.caf` and `alarm_ramp_30s.caf`"))
test("no dot or slash", not kh._is_file_path("justidentifier"))

# ===========================================================================
# compute_insights — empty DB
# ===========================================================================

section("compute_insights — empty DB")

uri_empty = _new_uri()
db_empty = _make_db(uri_empty)
orig_get_db = kh.get_db
kh.get_db = _get_db_factory(uri_empty)
kh.DB_PATH = Path("/nonexistent/fake.db")

ins_empty = kh.compute_insights()

kh.get_db = orig_get_db

test("generated_at present", bool(ins_empty.get("generated_at")))
test("summary non-empty", bool(ins_empty.get("summary")))
test("overview is dict", isinstance(ins_empty.get("overview"), dict))
test("quality_alerts is list", isinstance(ins_empty.get("quality_alerts"), list))
test("recommended_actions is list", isinstance(ins_empty.get("recommended_actions"), list))
test("recurring_noise_titles is list", isinstance(ins_empty.get("recurring_noise_titles"), list))
test("hot_files is list", isinstance(ins_empty.get("hot_files"), list))
test("entries is dict", isinstance(ins_empty.get("entries"), dict))
test("entries has mistakes key", "mistakes" in ins_empty.get("entries", {}))
test("entries has patterns key", "patterns" in ins_empty.get("entries", {}))
test("entries has decisions key", "decisions" in ins_empty.get("entries", {}))
test("entries has tools key", "tools" in ins_empty.get("entries", {}))
test(
    "empty-db critical alert present",
    any(a["id"] == "empty-db" for a in ins_empty["quality_alerts"]),
)
test("empty DB total_entries is 0", ins_empty["overview"]["total_entries"] == 0)

db_empty.close()

# ===========================================================================
# compute_insights — synthetic entries with noise
# ===========================================================================

section("compute_insights — synthetic DB with entries")

uri_syn = _new_uri()
db_syn = _make_db(uri_syn)

_insert_entries(
    db_syn,
    [
        # High-confidence mistakes
        {
            "category": "mistake",
            "title": "Used wrong API endpoint",
            "confidence": 0.9,
            "occurrence_count": 3,
            "session_id": "s1",
            "affected_files": '["src/api.py", "tests/test_api.py"]',
        },
        {
            "category": "mistake",
            "title": "Forgot await on async call",
            "confidence": 0.85,
            "occurrence_count": 2,
            "session_id": "s1",
            "affected_files": '["src/main.py"]',
        },
        # Low-confidence noise (3+ duplicates of same title)
        {
            "category": "mistake",
            "title": "generic noise title",
            "confidence": 0.2,
            "occurrence_count": 1,
            "session_id": "s2",
        },
        {
            "category": "mistake",
            "title": "generic noise title",
            "confidence": 0.2,
            "occurrence_count": 1,
            "session_id": "s3",
        },
        {
            "category": "mistake",
            "title": "generic noise title",
            "confidence": 0.2,
            "occurrence_count": 1,
            "session_id": "s4",
        },
        # Cross-category noise should collapse into one mixed row
        {
            "category": "mistake",
            "title": "cross-category noise",
            "confidence": 0.2,
            "occurrence_count": 1,
            "session_id": "s5",
        },
        {
            "category": "pattern",
            "title": "cross-category noise",
            "confidence": 0.2,
            "occurrence_count": 1,
            "session_id": "s6",
        },
        {
            "category": "decision",
            "title": "cross-category noise",
            "confidence": 0.2,
            "occurrence_count": 1,
            "session_id": "s7",
        },
        # Patterns
        {"category": "pattern", "title": "Always use async/await for IO", "confidence": 0.8, "session_id": "s1"},
        {"category": "pattern", "title": "Validate inputs at boundaries", "confidence": 0.75, "session_id": "s2"},
        # Decisions
        {"category": "decision", "title": "Use SQLite for local state", "confidence": 0.9, "session_id": "s1"},
        # Tools
        {"category": "tool", "title": "ripgrep for code search", "confidence": 0.8, "session_id": "s1"},
        # More low confidence to trigger alert
        *[{"category": "mistake", "title": f"noise_{i}", "confidence": 0.3, "session_id": "s2"} for i in range(20)],
    ],
)
# Add a repeated hot file path
for i in range(3):
    db_syn.execute(
        "INSERT INTO knowledge_entries (category, title, confidence, affected_files) VALUES (?, ?, ?, ?)",
        ("mistake", f"hot file entry {i}", 0.5, '["src/api.py", "Usage: see readme", "src/main.py"]'),
    )
db_syn.commit()

# Mixed embedding source types should not inflate knowledge coverage.
db_syn.execute(
    "INSERT INTO embeddings (source_type, source_id, vector) VALUES (?, ?, ?)",
    ("knowledge", 1, b"k"),
)
for i in range(50):
    db_syn.execute(
        "INSERT INTO embeddings (source_type, source_id, vector) VALUES (?, ?, ?)",
        ("session", 1000 + i, b"s"),
    )
db_syn.commit()

kh.get_db = _get_db_factory(uri_syn)

ins_syn = kh.compute_insights()

kh.get_db = orig_get_db

test("total_entries > 0", ins_syn["overview"]["total_entries"] > 0)
test("overview health_score is number", isinstance(ins_syn["overview"]["health_score"], (int, float)))
test("high_confidence_pct in [0,100]", 0 <= ins_syn["overview"]["high_confidence_pct"] <= 100)
test("low_confidence_pct in [0,100]", 0 <= ins_syn["overview"]["low_confidence_pct"] <= 100)
test("stale_pct in [0,100]", 0 <= ins_syn["overview"]["stale_pct"] <= 100)
test("relation_density >= 0", ins_syn["overview"]["relation_density"] >= 0)
test("embedding_pct >= 0", ins_syn["overview"]["embedding_pct"] >= 0)
test("embedding_pct only counts knowledge embeddings", 0 < ins_syn["overview"]["embedding_pct"] < 10)

# Verify entries shape
for cat_key in ("mistakes", "patterns", "decisions", "tools"):
    cat_list = ins_syn["entries"].get(cat_key, [])
    test(f"entries.{cat_key} is list", isinstance(cat_list, list))
    if cat_list:
        first = cat_list[0]
        test(f"entries.{cat_key}[0] has id", "id" in first)
        test(f"entries.{cat_key}[0] has title", "title" in first)
        test(f"entries.{cat_key}[0] has confidence", "confidence" in first)
        test(f"entries.{cat_key}[0] has occurrence_count", "occurrence_count" in first)
        test(f"entries.{cat_key}[0] has last_seen", "last_seen" in first)
        test(f"entries.{cat_key}[0] has summary", "summary" in first)
        test(f"entries.{cat_key}[0] has session_id", "session_id" in first)

# Verify noise detection
noise = ins_syn["recurring_noise_titles"]
test("recurring_noise_titles is list", isinstance(noise, list))
if noise:
    n = noise[0]
    test("noise item has title", "title" in n)
    test("noise item has category", "category" in n)
    test("noise item has entry_count", "entry_count" in n)
    test("noise item has avg_confidence", "avg_confidence" in n)
    test("noise avg_confidence < 0.5", n["avg_confidence"] < 0.5)
mixed_noise = [n for n in noise if n["title"] == "cross-category noise"]
test("cross-category noise collapses to one row", len(mixed_noise) == 1)
if mixed_noise:
    test("cross-category noise category is mixed", mixed_noise[0]["category"] == "mixed")

# Verify hot_files filter prose out
hot = ins_syn["hot_files"]
test("hot_files only real paths", all("/" in hf["path"] or "." in hf["path"] for hf in hot))
test("hot_files no prose (no colons)", all(":" not in hf["path"] for hf in hot))
test("hot_files no spaces", all(" " not in hf["path"] for hf in hot))
if hot:
    test("hot_file has path and references", "path" in hot[0] and "references" in hot[0])
    test("hot_file references >= 2", hot[0]["references"] >= 2)

# Verify alerts shape
alerts = ins_syn["quality_alerts"]
test("alerts is list", isinstance(alerts, list))
if alerts:
    a = alerts[0]
    test("alert has id", "id" in a)
    test("alert has title", "title" in a)
    test("alert has severity", a.get("severity") in ("info", "warning", "critical"))
    test("alert has detail", "detail" in a)

# Verify actions shape
actions = ins_syn["recommended_actions"]
test("actions is list", isinstance(actions, list))
if actions:
    ac = actions[0]
    test("action has id", "id" in ac)
    test("action has title", "title" in ac)
    test("action has detail", "detail" in ac)
    test("action has command", "command" in ac)
    test("actions never use nonexistent --add flag", all("--add" not in a["command"] for a in actions))
    test(
        "actions never use bare --relate command",
        all(a["command"].strip() != "python3 learn.py --relate" for a in actions),
    )

db_syn.close()

# ===========================================================================
# compute_insights — missing optional tables (graceful degradation)
# ===========================================================================

section("compute_insights — missing optional tables")

uri_no_opt = _new_uri()
db_no_opt = _make_db(uri_no_opt, with_relations=False, with_embeddings=False)
_insert_entries(
    db_no_opt,
    [
        {"category": "mistake", "title": "Something went wrong", "confidence": 0.7},
        {"category": "pattern", "title": "Always check return values", "confidence": 0.8},
    ],
)

kh.get_db = _get_db_factory(uri_no_opt)

try:
    ins_no_opt = kh.compute_insights()
    test("no crash without optional tables", True)
    test("relation_density is 0 when tables absent", ins_no_opt["overview"]["relation_density"] == 0)
    test("embedding_pct is 0 when embeddings absent", ins_no_opt["overview"]["embedding_pct"] == 0)
    test("hot_files is list even without data", isinstance(ins_no_opt["hot_files"], list))
except Exception as e:
    test("no crash without optional tables", False, str(e))

kh.get_db = orig_get_db
db_no_opt.close()

# ===========================================================================
# format_insights_report — structure tests
# ===========================================================================

section("format_insights_report — output structure")

sample_insights = {
    "generated_at": "2025-01-01T00:00:00+00:00",
    "summary": "Test summary",
    "overview": {
        "health_score": 55,
        "total_entries": 100,
        "sessions": 5,
        "high_confidence_pct": 10.0,
        "low_confidence_pct": 60.0,
        "stale_pct": 30.0,
        "relation_density": 0.1,
        "embedding_pct": 5.0,
    },
    "quality_alerts": [
        {"id": "test-alert", "title": "Test Alert", "severity": "warning", "detail": "Some detail"},
        {"id": "critical-alert", "title": "Critical", "severity": "critical", "detail": "Very bad"},
    ],
    "recommended_actions": [
        {"id": "action-01", "title": "Do something", "detail": "It helps", "command": "python3 embed.py --build"},
    ],
    "recurring_noise_titles": [
        {"title": "noisy title", "category": "mistake", "entry_count": 5, "avg_confidence": 0.25},
    ],
    "hot_files": [
        {"path": "src/api.py", "references": 10},
        {"path": "src/main.py", "references": 5},
    ],
    "entries": {
        "mistakes": [
            {
                "id": 1,
                "title": "A mistake",
                "confidence": 0.9,
                "occurrence_count": 2,
                "last_seen": "2025-01-01",
                "summary": "short summary",
                "session_id": "s1",
            }
        ],
        "patterns": [],
        "decisions": [],
        "tools": [],
    },
}

report = kh.format_insights_report(sample_insights)

test("report is non-empty string", isinstance(report, str) and len(report) > 50)
test("report contains health score", "55" in report)
test("report contains overview section", "Overview" in report)
test("report contains Quality Alerts section", "Quality Alerts" in report)
test("report contains Recommended Actions section", "Recommended Actions" in report)
test("report contains noise section", "Recurring" in report or "noise" in report.lower())
test("report contains hot files section", "Hot Files" in report)
test("report contains mistakes section", "Mistakes" in report)
test("report contains command", "embed.py" in report)
test("warning emoji in alerts", "🟡" in report)
test("critical emoji in alerts", "🔴" in report)

# Empty insights — no crash
empty_report = kh.format_insights_report(
    {
        "generated_at": "",
        "summary": "",
        "overview": {},
        "quality_alerts": [],
        "recommended_actions": [],
        "recurring_noise_titles": [],
        "hot_files": [],
        "entries": {},
    }
)
test("format with empty insights does not crash", isinstance(empty_report, str))

# ===========================================================================
# JSON contract validation
# ===========================================================================

section("JSON contract — required keys and types")

uri_contract = _new_uri()
db_contract = _make_db(uri_contract)
_insert_entries(
    db_contract,
    [
        {"category": "mistake", "title": "Test mistake", "confidence": 0.8, "session_id": "s1"},
        {"category": "pattern", "title": "Test pattern", "confidence": 0.7, "session_id": "s1"},
    ],
)

kh.get_db = _get_db_factory(uri_contract)

ins_contract = kh.compute_insights()

kh.get_db = orig_get_db
db_contract.close()

REQUIRED_TOP = {
    "generated_at",
    "summary",
    "overview",
    "quality_alerts",
    "recommended_actions",
    "recurring_noise_titles",
    "hot_files",
    "entries",
    "toward_100",
}
REQUIRED_OVERVIEW = {
    "health_score",
    "total_entries",
    "sessions",
    "high_confidence_pct",
    "low_confidence_pct",
    "stale_pct",
    "relation_density",
    "embedding_pct",
}
REQUIRED_ENTRY_CATS = {"mistakes", "patterns", "decisions", "tools"}

test(
    "all top-level keys present",
    REQUIRED_TOP <= set(ins_contract.keys()),
    f"missing: {REQUIRED_TOP - set(ins_contract.keys())}",
)
test(
    "all overview keys present",
    REQUIRED_OVERVIEW <= set(ins_contract["overview"].keys()),
    f"missing: {REQUIRED_OVERVIEW - set(ins_contract['overview'].keys())}",
)
test(
    "all entry categories present",
    REQUIRED_ENTRY_CATS <= set(ins_contract["entries"].keys()),
    f"missing: {REQUIRED_ENTRY_CATS - set(ins_contract['entries'].keys())}",
)

test(
    "generated_at is ISO string", isinstance(ins_contract["generated_at"], str) and "T" in ins_contract["generated_at"]
)
test("summary is string", isinstance(ins_contract["summary"], str))
test("overview.health_score is number", isinstance(ins_contract["overview"]["health_score"], (int, float)))
test("overview.total_entries is int", isinstance(ins_contract["overview"]["total_entries"], int))
test("overview.sessions is int", isinstance(ins_contract["overview"]["sessions"], int))
test("overview.high_confidence_pct is float", isinstance(ins_contract["overview"]["high_confidence_pct"], float))
test("overview.low_confidence_pct is float", isinstance(ins_contract["overview"]["low_confidence_pct"], float))
test("overview.stale_pct is float", isinstance(ins_contract["overview"]["stale_pct"], float))
test("overview.relation_density is float", isinstance(ins_contract["overview"]["relation_density"], float))
test("overview.embedding_pct is float", isinstance(ins_contract["overview"]["embedding_pct"], float))
test("quality_alerts is list", isinstance(ins_contract["quality_alerts"], list))
test("recommended_actions is list", isinstance(ins_contract["recommended_actions"], list))
test("recurring_noise_titles is list", isinstance(ins_contract["recurring_noise_titles"], list))
test("hot_files is list", isinstance(ins_contract["hot_files"], list))

for cat_key in ("mistakes", "patterns", "decisions", "tools"):
    test(f"entries.{cat_key} is list", isinstance(ins_contract["entries"][cat_key], list))

# Validate JSON serializability
try:
    serialized = json.dumps(ins_contract)
    test("output is JSON serializable", True)
except TypeError as e:
    test("output is JSON serializable", False, str(e))

# ===========================================================================
# sync_advisory — advisory signal tests (does NOT affect score)
# ===========================================================================

section("sync_advisory — advisory signal in compute_insights")

test(
    "sync_advisory key present in compute_insights output",
    "sync_advisory" in ins_contract,
)
adv = ins_contract.get("sync_advisory", {})
test("sync_advisory is dict", isinstance(adv, dict))
test("sync_advisory has status", adv.get("status") in ("ok", "suggest", "review"))
test("sync_advisory has reasons list", isinstance(adv.get("reasons"), list))
test("sync_advisory has checklist key", "checklist" in adv)
test("sync_advisory checklist references SYNC-MATRIX.md", "SYNC-MATRIX" in adv.get("checklist", ""))

# Advisory with empty DB: should be "ok" (no hot files, no mistakes)
test(
    "sync_advisory status ok for empty DB",
    ins_empty.get("sync_advisory", {}).get("status") == "ok",
)

# Advisory should appear in format_insights_report when status is suggest/review
_adv_suggest_insights = {
    "generated_at": "2025-01-01T00:00:00+00:00",
    "summary": "Test",
    "overview": {
        "health_score": 50,
        "total_entries": 10,
        "sessions": 2,
        "high_confidence_pct": 20.0,
        "low_confidence_pct": 30.0,
        "stale_pct": 20.0,
        "relation_density": 0.1,
        "embedding_pct": 5.0,
    },
    "quality_alerts": [],
    "recommended_actions": [],
    "recurring_noise_titles": [],
    "hot_files": [],
    "entries": {},
    "sync_advisory": {
        "status": "suggest",
        "reasons": ["Test reason for suggestion."],
        "checklist": "docs/SYNC-MATRIX.md",
    },
}
_adv_report = kh.format_insights_report(_adv_suggest_insights)
test(
    "format_insights_report renders sync advisory when status is suggest",
    "Sync Advisory" in _adv_report,
)
test(
    "format_insights_report includes advisory reason text",
    "Test reason for suggestion." in _adv_report,
)
test(
    "format_insights_report includes SYNC-MATRIX.md reference",
    "SYNC-MATRIX.md" in _adv_report,
)

# Advisory should NOT appear when status is ok
_adv_ok_insights = dict(_adv_suggest_insights)
_adv_ok_insights["sync_advisory"] = {"status": "ok", "reasons": [], "checklist": "docs/SYNC-MATRIX.md"}
_ok_report = kh.format_insights_report(_adv_ok_insights)
test(
    "format_insights_report suppresses sync advisory when status is ok",
    "Sync Advisory" not in _ok_report,
)

# Verify score is not affected by sync advisory
uri_adv = _new_uri()
db_adv = _make_db(uri_adv)
_insert_entries(
    db_adv,
    [
        # Many hot-file mistakes to potentially trigger the advisory
        *[
            {
                "category": "mistake",
                "title": f"churn mistake {i}",
                "confidence": 0.7,
                "affected_files": '["src/core.py", "src/api.py", "src/utils.py"]',
            }
            for i in range(10)
        ],
    ],
)
kh.get_db = _get_db_factory(uri_adv)

_health_before = kh.compute_health()
_ins_adv = kh.compute_insights()
_health_after = kh.compute_health()

kh.get_db = orig_get_db
db_adv.close()

test("score unchanged by sync_advisory", _health_before["score"] == _health_after["score"])
test("subscores unchanged by sync_advisory", _health_before.get("subscores") == _health_after.get("subscores"))

# Regression: "No decision entries" advisory must NOT fire when decision entries exist
# (fix: _compute_sync_advisory reads categories.decision, not the absent top-level "decisions" key)
uri_nodecision_bug = _new_uri()
db_nodecision_bug = _make_db(uri_nodecision_bug)
_insert_entries(
    db_nodecision_bug,
    [
        *[{"category": "mistake", "title": f"m{i}", "confidence": 0.7} for i in range(8)],
        *[{"category": "decision", "title": f"d{i}", "confidence": 0.8} for i in range(3)],
    ],
)
kh.get_db = _get_db_factory(uri_nodecision_bug)
_ins_nodecision_bug = kh.compute_insights()
kh.get_db = orig_get_db
db_nodecision_bug.close()
_adv_nodecision_bug = _ins_nodecision_bug.get("sync_advisory", {})
_adv_reasons_nodecision = _adv_nodecision_bug.get("reasons", [])
test(
    "sync_advisory no-decision advisory absent when decision entries exist (regression)",
    not any("No decision entries" in r for r in _adv_reasons_nodecision),
    f"reasons={_adv_reasons_nodecision}",
)

# ===========================================================================
# Existing mode non-regression
# ===========================================================================

section("Non-regression — existing CLI flags not affected")

uri_nr = _new_uri()
db_nr = _make_db(uri_nr)
_insert_entries(
    db_nr,
    [
        {"category": "mistake", "title": "Old mistake", "confidence": 0.6},
        {"category": "pattern", "title": "Good pattern", "confidence": 0.75},
    ],
)

kh.get_db = _get_db_factory(uri_nr)

health = kh.compute_health()
test("compute_health still returns score", "score" in health)
test("compute_health score is 0-100", 0 <= health["score"] <= 100)
test("compute_health total matches", health["total"] == 2)

recall = kh.compute_recall_stats()
test("compute_recall_stats returns dict", isinstance(recall, dict))
test("recall has available key", "available" in recall)

sync = kh.compute_sync_stats()
test("compute_sync_stats returns dict", isinstance(sync, dict))
test("sync has available key", "available" in sync)

kh.get_db = orig_get_db
db_nr.close()

# ===========================================================================
# compute_health — toward_100 structure
# ===========================================================================

section("compute_health — toward_100 structure")

uri_t100 = _new_uri()
db_t100 = _make_db(uri_t100)
_insert_entries(
    db_t100,
    [{"category": "mistake", "title": f"m{i}", "confidence": 0.3, "session_id": "s1"} for i in range(10)],
)
kh.get_db = _get_db_factory(uri_t100)
h_t100 = kh.compute_health()
kh.get_db = orig_get_db
db_t100.close()

test("toward_100 key present in compute_health", "toward_100" in h_t100)
t100 = h_t100.get("toward_100", {})
test("toward_100 has total_gap", "total_gap" in t100)
test("toward_100 has dimensions", "dimensions" in t100)
test("toward_100 has top_gaps", "top_gaps" in t100)
test("toward_100 total_gap is float", isinstance(t100.get("total_gap"), (int, float)))
test("toward_100 total_gap equals 100 - score", abs(t100["total_gap"] - round(100.0 - h_t100["score"], 1)) < 0.01)
test("toward_100 dimensions has 6 entries", len(t100.get("dimensions", [])) == 6)
test("toward_100 top_gaps has 3 entries", len(t100.get("top_gaps", [])) == 3)

# Verify each dimension entry shape
dim0 = t100["dimensions"][0] if t100.get("dimensions") else {}
test("dimension has 'dimension' key", "dimension" in dim0)
test("dimension has 'current' key", "current" in dim0)
test("dimension has 'max' key", "max" in dim0)
test("dimension has 'gap' key", "gap" in dim0)
test("dimension has 'gap_pct' key", "gap_pct" in dim0)
test("dimension has 'pct_of_total_gap' key", "pct_of_total_gap" in dim0)
test("dimension current + gap == max", abs(dim0.get("current", 0) + dim0.get("gap", 0) - dim0.get("max", 0)) < 0.2)

# Verify dimensions are sorted by gap descending
dims = t100.get("dimensions", [])
gaps = [d["gap"] for d in dims]
test("dimensions sorted by gap descending", gaps == sorted(gaps, reverse=True))

# Verify all dimension names present
dim_names = {d["dimension"] for d in dims}
expected_dims = {
    "categorization",
    "learning_curve",
    "freshness",
    "relation_density",
    "embedding_coverage",
    "confidence_quality",
}
test("all 6 dimension names present", dim_names == expected_dims)

# Verify score is NOT modified by toward_100 computation
test("score unchanged after toward_100 computed", "score" in h_t100 and 0 <= h_t100["score"] <= 100)

# Verify total_gap + score = 100 (within rounding)
test("toward_100 total_gap + score ≈ 100", abs(t100["total_gap"] + h_t100["score"] - 100.0) < 0.2)

# ===========================================================================
# compute_insights — toward_100 payload and gap alerts
# ===========================================================================

section("compute_insights — toward_100 and gap alerts")

# DB with low confidence quality, low learning curve, low relation density
uri_gap = _new_uri()
db_gap = _make_db(uri_gap)
_insert_entries(
    db_gap,
    [
        # 12 low-confidence mistakes, 1 pattern → low lc_score, very low cq_score
        *[{"category": "mistake", "title": f"gap_m{i}", "confidence": 0.3, "session_id": "s1"} for i in range(12)],
        {"category": "pattern", "title": "one pattern", "confidence": 0.7, "session_id": "s1"},
    ],
)
# No relations, no embeddings → sparse graph
kh.get_db = _get_db_factory(uri_gap)
ins_gap = kh.compute_insights()
kh.get_db = orig_get_db
db_gap.close()

test("toward_100 key present in compute_insights", "toward_100" in ins_gap)
t100_ins = ins_gap.get("toward_100", {})
test("compute_insights toward_100 has total_gap", "total_gap" in t100_ins)
test("compute_insights toward_100 has top_gaps", "top_gaps" in t100_ins)
test("compute_insights toward_100 top_gaps is list", isinstance(t100_ins.get("top_gaps"), list))
test("compute_insights toward_100 top_gaps has 3 entries", len(t100_ins.get("top_gaps", [])) == 3)

# Verify confidence-quality-gap alert fires (very low high-conf with >= 10 total)
gap_alert_ids = {a["id"] for a in ins_gap.get("quality_alerts", [])}
test("confidence-quality-gap alert fires for low high-confidence entries", "confidence-quality-gap" in gap_alert_ids)

# Verify learning-curve-gap alert fires (12 mistakes, only 1 pattern → lc_score low)
test("learning-curve-gap alert fires when patterns lag mistakes", "learning-curve-gap" in gap_alert_ids)

# Verify relation-density-gap alert fires (no relations, 13+ entries)
test("relation-density-gap alert fires for sparse graph with >= 10 entries", "relation-density-gap" in gap_alert_ids)

# Verify gap alert shape
for aid in ("confidence-quality-gap", "learning-curve-gap", "relation-density-gap"):
    matching = [a for a in ins_gap["quality_alerts"] if a["id"] == aid]
    if matching:
        a = matching[0]
        test(f"{aid} has id field", "id" in a)
        test(f"{aid} has title field", "title" in a)
        test(f"{aid} has severity field", a.get("severity") in ("info", "warning", "critical"))
        test(f"{aid} has detail field", "detail" in a and len(a["detail"]) > 10)
        test(f"{aid} detail mentions points", "point" in a["detail"].lower())

# Verify gap alerts do NOT fire for a healthy DB (all metrics at max)
uri_healthy = _new_uri()
db_healthy = _make_db(uri_healthy)
today = __import__("datetime").date.today().isoformat()
_insert_entries(
    db_healthy,
    [
        # 10 high-confidence entries and 10 patterns > mistakes → healthy subscores
        *[
            {
                "category": "mistake",
                "title": f"hm{i}",
                "confidence": 0.9,
                "session_id": "s1",
                "first_seen": today,
                "last_seen": today,
            }
            for i in range(3)
        ],
        *[
            {
                "category": "pattern",
                "title": f"hp{i}",
                "confidence": 0.9,
                "session_id": "s1",
                "first_seen": today,
                "last_seen": today,
            }
            for i in range(10)
        ],
    ],
)
# Add enough relations to push relation_density high
for i in range(20):
    db_healthy.execute(
        "INSERT INTO knowledge_relations (source_id, target_id, relation_type) VALUES (?, ?, ?)", (1, i + 2, "related")
    )
db_healthy.commit()
kh.get_db = _get_db_factory(uri_healthy)
ins_healthy = kh.compute_insights()
kh.get_db = orig_get_db
db_healthy.close()

healthy_alert_ids = {a["id"] for a in ins_healthy.get("quality_alerts", [])}
test("confidence-quality-gap absent when high-conf entries dominant", "confidence-quality-gap" not in healthy_alert_ids)
test("learning-curve-gap absent when patterns dominate mistakes", "learning-curve-gap" not in healthy_alert_ids)
test("relation-density-gap absent when graph is dense", "relation-density-gap" not in healthy_alert_ids)

# ===========================================================================
# format_insights_report — Toward 100 section
# ===========================================================================

section("format_insights_report — Toward 100 section")

sample_t100_insights = {
    "generated_at": "2025-01-01T00:00:00+00:00",
    "summary": "Test summary",
    "overview": {
        "health_score": 55,
        "total_entries": 100,
        "sessions": 5,
        "high_confidence_pct": 10.0,
        "low_confidence_pct": 60.0,
        "stale_pct": 30.0,
        "relation_density": 0.1,
        "embedding_pct": 5.0,
    },
    "quality_alerts": [],
    "recommended_actions": [],
    "recurring_noise_titles": [],
    "hot_files": [],
    "entries": {},
    "sync_advisory": {"status": "ok", "reasons": [], "checklist": "docs/SYNC-MATRIX.md"},
    "toward_100": {
        "total_gap": 45.0,
        "top_gaps": [
            {
                "dimension": "confidence_quality",
                "current": 2.0,
                "max": 15.0,
                "gap": 13.0,
                "gap_pct": 86.7,
                "pct_of_total_gap": 28.9,
            },
            {
                "dimension": "learning_curve",
                "current": 6.0,
                "max": 20.0,
                "gap": 14.0,
                "gap_pct": 70.0,
                "pct_of_total_gap": 31.1,
            },
            {
                "dimension": "relation_density",
                "current": 5.0,
                "max": 15.0,
                "gap": 10.0,
                "gap_pct": 66.7,
                "pct_of_total_gap": 22.2,
            },
        ],
        "dimensions": [],
    },
}

t100_report = kh.format_insights_report(sample_t100_insights)
test("format_insights_report contains Toward 100 section", "Toward 100" in t100_report)
test("format_insights_report shows total gap", "45.0" in t100_report)
test("format_insights_report shows Confidence Quality dimension", "Confidence Quality" in t100_report)
test("format_insights_report shows Learning Curve dimension", "Learning Curve" in t100_report)
test("format_insights_report shows Relation Density dimension", "Relation Density" in t100_report)

# Toward 100 section absent when total_gap == 0 (perfect score)
no_gap_insights = dict(sample_t100_insights)
no_gap_insights["toward_100"] = {"total_gap": 0, "top_gaps": [], "dimensions": []}
no_gap_report = kh.format_insights_report(no_gap_insights)
test("Toward 100 section absent when gap is zero", "Toward 100" not in no_gap_report)

# Toward 100 section absent when toward_100 key is missing (backward compat)
no_t100_insights = {k: v for k, v in sample_t100_insights.items() if k != "toward_100"}
no_t100_report = kh.format_insights_report(no_t100_insights)
test("format_insights_report tolerates missing toward_100 key", isinstance(no_t100_report, str))

# ===========================================================================
# format_report — Toward 100 section
# ===========================================================================

section("format_report — Toward 100 section")

uri_fr = _new_uri()
db_fr = _make_db(uri_fr)
_insert_entries(
    db_fr,
    [{"category": "mistake", "title": f"frm{i}", "confidence": 0.3} for i in range(5)],
)
kh.get_db = _get_db_factory(uri_fr)
h_fr = kh.compute_health()
kh.get_db = orig_get_db
db_fr.close()

fr_report = kh.format_report(h_fr)
test("format_report contains Toward 100 section", "Toward 100" in fr_report)
test("format_report contains total gap value", str(h_fr["toward_100"]["total_gap"]) in fr_report)

# ===========================================================================
# JSON serialisability of new toward_100 payload
# ===========================================================================

section("toward_100 JSON serialisability")

try:
    json.dumps(ins_gap)
    test("compute_insights with gap alerts is JSON serializable", True)
except TypeError as e:
    test("compute_insights with gap alerts is JSON serializable", False, str(e))

try:
    json.dumps(h_t100)
    test("compute_health toward_100 is JSON serializable", True)
except TypeError as e:
    test("compute_health toward_100 is JSON serializable", False, str(e))

# ===========================================================================
# Confidence quality: high-confidence patterns improve metric
# ===========================================================================

section("high-confidence patterns improve high_confidence_pct")

_uri_high_conf = _new_uri()
_db_high_conf = _make_db(_uri_high_conf)
kh.get_db = _get_db_factory(_uri_high_conf)

# Seed with 6 entries: 4 patterns at confidence >= 0.8, 2 mistakes at 0.4
_insert_entries(
    _db_high_conf,
    [
        {"category": "pattern", "confidence": 0.8, "title": "use context manager", "session_id": "s1"},
        {"category": "pattern", "confidence": 0.85, "title": "prefer dataclass", "session_id": "s1"},
        {"category": "pattern", "confidence": 0.9, "title": "cache results", "session_id": "s2"},
        {"category": "pattern", "confidence": 0.82, "title": "validate early", "session_id": "s2"},
        {"category": "mistake", "confidence": 0.4, "title": "forgot to close file", "session_id": "s1"},
        {"category": "mistake", "confidence": 0.45, "title": "import error", "session_id": "s2"},
    ],
)
_ins_high_conf = kh.compute_insights()
_high_conf_pct = _ins_high_conf.get("overview", {}).get("high_confidence_pct", 0)
test("high_confidence_pct >= 50.0 with 4/6 high-conf entries", _high_conf_pct >= 50.0, f"got {_high_conf_pct}")
test("high_confidence_pct <= 100.0", _high_conf_pct <= 100.0, f"got {_high_conf_pct}")

# Verify low-confidence only DB gives lower pct
_uri_low = _new_uri()
_db_low = _make_db(_uri_low)
kh.get_db = _get_db_factory(_uri_low)
_insert_entries(
    _db_low,
    [
        {"category": "pattern", "confidence": 0.4, "title": "p1", "session_id": "s1"},
        {"category": "pattern", "confidence": 0.4, "title": "p2", "session_id": "s1"},
        {"category": "mistake", "confidence": 0.4, "title": "m1", "session_id": "s1"},
    ],
)
_ins_low = kh.compute_insights()
_low_conf_pct = _ins_low.get("overview", {}).get("high_confidence_pct", 0)
test(
    "low-confidence DB has lower pct than high-confidence DB",
    _low_conf_pct < _high_conf_pct,
    f"low={_low_conf_pct} high={_high_conf_pct}",
)

kh.get_db = orig_get_db

# ===========================================================================
# Integrity lints — dangling relation detection (#386, WBS-061)
# ===========================================================================

section("integrity_lints — dangling relation detection (#386)")

_KR_FULL_SCHEMA = """CREATE TABLE knowledge_relations (
    id INTEGER PRIMARY KEY,
    source_id INTEGER,
    target_id INTEGER,
    relation_type TEXT,
    stable_id TEXT,
    source_stable_id TEXT DEFAULT '',
    target_stable_id TEXT DEFAULT ''
)"""

# DB with 1 valid relation + 1 dangling (target_id=999 does not exist)
uri_dangle = _new_uri()
db_dangle = sqlite3.connect(uri_dangle, uri=True)
db_dangle.row_factory = sqlite3.Row
db_dangle.execute(_KE_SCHEMA)
db_dangle.execute(_KR_FULL_SCHEMA)
db_dangle.execute(_SCHEMA_VER_SCHEMA)
db_dangle.execute(
    "INSERT INTO knowledge_entries (id, category, title, content, session_id) VALUES (1, 'mistake', 'e1', 'c1', 's1')"
)
db_dangle.execute(
    "INSERT INTO knowledge_entries (id, category, title, content, session_id) VALUES (2, 'pattern', 'e2', 'c2', 's1')"
)
db_dangle.execute("INSERT INTO knowledge_relations (source_id, target_id, relation_type) VALUES (1, 2, 'related')")
db_dangle.execute("INSERT INTO knowledge_relations (source_id, target_id, relation_type) VALUES (1, 999, 'orphaned')")
db_dangle.commit()

kh.get_db = _get_db_factory(uri_dangle)
ins_dangle = kh.compute_insights()
kh.get_db = orig_get_db
db_dangle.close()

test("integrity_lints key present in compute_insights", "integrity_lints" in ins_dangle)
lint_dangle = ins_dangle.get("integrity_lints", {})
test("dangling_relations count == 1", lint_dangle.get("dangling_relations", -1) == 1)
test(
    "dangling-relations alert fires",
    any(a.get("id") == "dangling-relations" for a in ins_dangle.get("quality_alerts", [])),
)
test(
    "dangling-relations alert is warning severity",
    any(
        a.get("id") == "dangling-relations" and a.get("severity") == "warning"
        for a in ins_dangle.get("quality_alerts", [])
    ),
)

# DB with only valid relations — no alert
uri_valid_rel = _new_uri()
db_valid_rel = sqlite3.connect(uri_valid_rel, uri=True)
db_valid_rel.row_factory = sqlite3.Row
db_valid_rel.execute(_KE_SCHEMA)
db_valid_rel.execute(_KR_FULL_SCHEMA)
db_valid_rel.execute(_SCHEMA_VER_SCHEMA)
db_valid_rel.execute(
    "INSERT INTO knowledge_entries (id, category, title, content, session_id) VALUES (1, 'mistake', 'e1', 'c1', 's1')"
)
db_valid_rel.execute(
    "INSERT INTO knowledge_entries (id, category, title, content, session_id) VALUES (2, 'pattern', 'e2', 'c2', 's1')"
)
db_valid_rel.execute("INSERT INTO knowledge_relations (source_id, target_id, relation_type) VALUES (1, 2, 'related')")
db_valid_rel.commit()

kh.get_db = _get_db_factory(uri_valid_rel)
ins_valid_rel = kh.compute_insights()
kh.get_db = orig_get_db
db_valid_rel.close()

test(
    "dangling-relations alert absent when all relations valid",
    not any(a.get("id") == "dangling-relations" for a in ins_valid_rel.get("quality_alerts", [])),
)
test(
    "dangling_relations count is 0 for valid DB",
    ins_valid_rel.get("integrity_lints", {}).get("dangling_relations", -1) == 0,
)

# ===========================================================================
# Integrity lints — contradiction detection (#388, WBS-063)
# ===========================================================================

section("integrity_lints — contradiction detection (#388)")

uri_contra = _new_uri()
db_contra = sqlite3.connect(uri_contra, uri=True)
db_contra.row_factory = sqlite3.Row
db_contra.execute(_KE_SCHEMA)
db_contra.execute(_SCHEMA_VER_SCHEMA)
# Two contradicting entries with the same tags
db_contra.execute(
    "INSERT INTO knowledge_entries (id, category, title, content, tags, session_id) VALUES (1, 'pattern', 'Always use X for production', 'Use X', 'tool-x,production', 's1')"
)
db_contra.execute(
    "INSERT INTO knowledge_entries (id, category, title, content, tags, session_id) VALUES (2, 'pattern', 'Never use X in production', 'Avoid X', 'tool-x,production', 's1')"
)
# Non-contradicting entry
db_contra.execute(
    "INSERT INTO knowledge_entries (id, category, title, content, tags, session_id) VALUES (3, 'pattern', 'Always use linting', 'Lint code', 'quality', 's1')"
)
db_contra.commit()

kh.get_db = _get_db_factory(uri_contra)
ins_contra = kh.compute_insights()
kh.get_db = orig_get_db
db_contra.close()

lint_contra = ins_contra.get("integrity_lints", {})
test("contradictions key present in integrity_lints", "contradictions" in lint_contra)
test("contradiction pair detected (Always/Never same tags)", len(lint_contra.get("contradictions", [])) >= 1)
test(
    "contradiction-pairs alert fires",
    any(a.get("id") == "contradiction-pairs" for a in ins_contra.get("quality_alerts", [])),
)
if lint_contra.get("contradictions"):
    c = lint_contra["contradictions"][0]
    test("contradiction entry has entry_a_id", "entry_a_id" in c)
    test("contradiction entry has entry_b_id", "entry_b_id" in c)
    test("contradiction entry has shared_tags", "shared_tags" in c)

# Empty DB — no contradiction alert
uri_no_contra = _new_uri()
db_no_contra = sqlite3.connect(uri_no_contra, uri=True)
db_no_contra.row_factory = sqlite3.Row
db_no_contra.execute(_KE_SCHEMA)
db_no_contra.execute(_SCHEMA_VER_SCHEMA)
db_no_contra.commit()

kh.get_db = _get_db_factory(uri_no_contra)
ins_no_contra = kh.compute_insights()
kh.get_db = orig_get_db
db_no_contra.close()

test(
    "contradiction-pairs alert absent for empty DB",
    not any(a.get("id") == "contradiction-pairs" for a in ins_no_contra.get("quality_alerts", [])),
)
test(
    "contradictions list empty for empty DB", ins_no_contra.get("integrity_lints", {}).get("contradictions", None) == []
)

# Non-contradicting DB (all "Always", no "Never") — no alert
uri_always_only = _new_uri()
db_always_only = sqlite3.connect(uri_always_only, uri=True)
db_always_only.row_factory = sqlite3.Row
db_always_only.execute(_KE_SCHEMA)
db_always_only.execute(_SCHEMA_VER_SCHEMA)
db_always_only.execute(
    "INSERT INTO knowledge_entries (id, category, title, content, tags, session_id) VALUES (1, 'pattern', 'Always use X', 'Use X', 'tool-x', 's1')"
)
db_always_only.execute(
    "INSERT INTO knowledge_entries (id, category, title, content, tags, session_id) VALUES (2, 'pattern', 'Always use Y', 'Use Y', 'tool-x', 's1')"
)
db_always_only.commit()

kh.get_db = _get_db_factory(uri_always_only)
ins_always_only = kh.compute_insights()
kh.get_db = orig_get_db
db_always_only.close()

test(
    "contradiction-pairs absent when no Never/Always pair exists",
    not any(a.get("id") == "contradiction-pairs" for a in ins_always_only.get("quality_alerts", [])),
)

# SQL precedence regression: Always/Never pair with NULL tags must NOT fire
# (first OR branch was previously unguarded by tags filter due to AND > OR precedence)
section("integrity_lints — contradiction SQL precedence regression (null/empty tags)")

uri_null_tags = _new_uri()
db_null_tags = sqlite3.connect(uri_null_tags, uri=True)
db_null_tags.row_factory = sqlite3.Row
db_null_tags.execute(_KE_SCHEMA)
db_null_tags.execute(_SCHEMA_VER_SCHEMA)
# Always/Never pair whose tags are NULL — must NOT be flagged as contradictions
db_null_tags.execute(
    "INSERT INTO knowledge_entries (id, category, title, content, tags, session_id) VALUES (1, 'pattern', 'Always do X', 'Do X', NULL, 's1')"
)
db_null_tags.execute(
    "INSERT INTO knowledge_entries (id, category, title, content, tags, session_id) VALUES (2, 'pattern', 'Never do X', 'Avoid X', NULL, 's1')"
)
# Also add an Always/Never pair with an empty-string tag — also must NOT fire
db_null_tags.execute(
    "INSERT INTO knowledge_entries (id, category, title, content, tags, session_id) VALUES (3, 'pattern', 'Always do Y', 'Do Y', '', 's1')"
)
db_null_tags.execute(
    "INSERT INTO knowledge_entries (id, category, title, content, tags, session_id) VALUES (4, 'pattern', 'Never do Y', 'Avoid Y', '', 's1')"
)
db_null_tags.commit()

kh.get_db = _get_db_factory(uri_null_tags)
ins_null_tags = kh.compute_insights()
kh.get_db = orig_get_db
db_null_tags.close()

test(
    "contradiction-pairs absent when Always/Never pair has NULL tags (SQL precedence regression)",
    not any(a.get("id") == "contradiction-pairs" for a in ins_null_tags.get("quality_alerts", [])),
)
test(
    "contradictions list empty when tags are NULL/empty (SQL precedence regression)",
    ins_null_tags.get("integrity_lints", {}).get("contradictions", None) == [],
)

# ===========================================================================
# Integrity lints — stable_id collision detection (#390, WBS-065)
# ===========================================================================

section("integrity_lints — stable_id collision detection (#390)")

uri_sid_coll = _new_uri()
db_sid_coll = sqlite3.connect(uri_sid_coll, uri=True)
db_sid_coll.row_factory = sqlite3.Row
db_sid_coll.execute(_KE_SCHEMA)
db_sid_coll.execute(_SCHEMA_VER_SCHEMA)
# Two entries sharing the same stable_id but different content (collision)
_SAME_SID = "deadbeef1234567890abcdef"
db_sid_coll.execute(
    "INSERT INTO knowledge_entries (id, category, title, content, stable_id, session_id) VALUES (1, 'mistake', 'entry A', 'content A', ?, 's1')",
    (_SAME_SID,),
)
db_sid_coll.execute(
    "INSERT INTO knowledge_entries (id, category, title, content, stable_id, session_id) VALUES (2, 'mistake', 'entry B', 'content B different', ?, 's2')",
    (_SAME_SID,),
)
# One unique entry
db_sid_coll.execute(
    "INSERT INTO knowledge_entries (id, category, title, content, stable_id, session_id) VALUES (3, 'pattern', 'unique entry', 'unique content', 'uniquestableid999', 's1')"
)
db_sid_coll.commit()

kh.get_db = _get_db_factory(uri_sid_coll)
ins_sid_coll = kh.compute_insights()
kh.get_db = orig_get_db
db_sid_coll.close()

lint_sid = ins_sid_coll.get("integrity_lints", {})
test("stable_id_collisions key present in integrity_lints", "stable_id_collisions" in lint_sid)
test("stable_id_collisions count == 1 for duplicate stable_id", lint_sid.get("stable_id_collisions", -1) == 1)
test(
    "stable-id-collision alert fires",
    any(a.get("id") == "stable-id-collision" for a in ins_sid_coll.get("quality_alerts", [])),
)

# DB with all unique stable_ids — no alert
uri_sid_ok = _new_uri()
db_sid_ok = sqlite3.connect(uri_sid_ok, uri=True)
db_sid_ok.row_factory = sqlite3.Row
db_sid_ok.execute(_KE_SCHEMA)
db_sid_ok.execute(_SCHEMA_VER_SCHEMA)
db_sid_ok.execute(
    "INSERT INTO knowledge_entries (id, category, title, content, stable_id, session_id) VALUES (1, 'mistake', 'e1', 'c1', 'stable001', 's1')"
)
db_sid_ok.execute(
    "INSERT INTO knowledge_entries (id, category, title, content, stable_id, session_id) VALUES (2, 'mistake', 'e2', 'c2', 'stable002', 's1')"
)
db_sid_ok.commit()

kh.get_db = _get_db_factory(uri_sid_ok)
ins_sid_ok = kh.compute_insights()
kh.get_db = orig_get_db
db_sid_ok.close()

test(
    "stable-id-collision alert absent when all stable_ids unique",
    not any(a.get("id") == "stable-id-collision" for a in ins_sid_ok.get("quality_alerts", [])),
)
test(
    "stable_id_collisions == 0 for unique DB",
    ins_sid_ok.get("integrity_lints", {}).get("stable_id_collisions", -1) == 0,
)

# DB with no stable_ids set (all NULL) — no collision alert
uri_sid_null = _new_uri()
db_sid_null = sqlite3.connect(uri_sid_null, uri=True)
db_sid_null.row_factory = sqlite3.Row
db_sid_null.execute(_KE_SCHEMA)
db_sid_null.execute(_SCHEMA_VER_SCHEMA)
db_sid_null.execute(
    "INSERT INTO knowledge_entries (id, category, title, content, session_id) VALUES (1, 'mistake', 'e1', 'c1', 's1')"
)
db_sid_null.execute(
    "INSERT INTO knowledge_entries (id, category, title, content, session_id) VALUES (2, 'mistake', 'e2', 'c2', 's1')"
)
db_sid_null.commit()

kh.get_db = _get_db_factory(uri_sid_null)
ins_sid_null = kh.compute_insights()
kh.get_db = orig_get_db
db_sid_null.close()

test(
    "stable-id-collision absent when all stable_ids are NULL",
    not any(a.get("id") == "stable-id-collision" for a in ins_sid_null.get("quality_alerts", [])),
)

# ===========================================================================
# Integrity lints — DB size budget warning (#391, WBS-066)
# ===========================================================================

section("integrity_lints — DB size budget (#391)")

_size_tmp = Path(tempfile.mkdtemp(prefix="kh-size-test-"))
try:
    # Create a real file to test size budget
    _db_small_path = _size_tmp / "small.db"
    _small_conn = sqlite3.connect(str(_db_small_path))
    _small_conn.execute("CREATE TABLE t (x TEXT)")
    _small_conn.close()

    # Test: below threshold — no alert
    _old_db_path = kh.DB_PATH
    kh.DB_PATH = _db_small_path
    _budget_env_bak = os.environ.pop("SK_DB_SIZE_BUDGET_MB", None)
    os.environ["SK_DB_SIZE_BUDGET_MB"] = "500"  # 500 MB budget, tiny DB is under it

    lint_small = kh._compute_db_size_lint()
    test("_compute_db_size_lint returns dict", isinstance(lint_small, dict))
    test("db_size_lint has size_bytes key", "size_bytes" in lint_small)
    test("db_size_lint has budget_bytes key", "budget_bytes" in lint_small)
    test("db_size_lint has over_budget key", "over_budget" in lint_small)
    test("db_size_lint has size_mb key", "size_mb" in lint_small)
    test("db_size_lint has budget_mb key", "budget_mb" in lint_small)
    test("small DB not over budget at 500MB threshold", lint_small.get("over_budget") is False)

    # Test: above threshold (set tiny threshold so small file exceeds it)
    os.environ["SK_DB_SIZE_BUDGET_MB"] = "0"  # 0 MB = anything exceeds it
    lint_over = kh._compute_db_size_lint()
    test("tiny threshold triggers over_budget=True", lint_over.get("over_budget") is True)

    # Test: config override (env var)
    os.environ["SK_DB_SIZE_BUDGET_MB"] = "1"  # 1 MB; small DB is under it
    lint_1mb = kh._compute_db_size_lint()
    test("budget_mb == 1.0 when SK_DB_SIZE_BUDGET_MB=1", lint_1mb.get("budget_mb") == 1.0)
    test("small DB is under 1MB budget", lint_1mb.get("over_budget") is False)

    # Test: nonexistent path — graceful degradation
    kh.DB_PATH = _size_tmp / "nonexistent.db"
    lint_missing = kh._compute_db_size_lint()
    test("size_bytes == 0 when file does not exist", lint_missing.get("size_bytes") == 0)
    test("over_budget False when file missing", lint_missing.get("over_budget") is False)

    # Restore
    if _budget_env_bak is not None:
        os.environ["SK_DB_SIZE_BUDGET_MB"] = _budget_env_bak
    else:
        os.environ.pop("SK_DB_SIZE_BUDGET_MB", None)
    kh.DB_PATH = _old_db_path

    # Test: compute_insights surfaces db_size_budget key
    uri_sz = _new_uri()
    db_sz = _make_db(uri_sz)
    _insert_entries(db_sz, [{"category": "mistake", "title": "sz1", "confidence": 0.7}])
    kh.get_db = _get_db_factory(uri_sz)
    _sz_db_path_bak = kh.DB_PATH
    kh.DB_PATH = _db_small_path  # real file
    os.environ["SK_DB_SIZE_BUDGET_MB"] = "500"
    ins_sz = kh.compute_insights()
    kh.get_db = orig_get_db
    kh.DB_PATH = _sz_db_path_bak
    os.environ.pop("SK_DB_SIZE_BUDGET_MB", None)

    test("db_size_budget key present in compute_insights output", "db_size_budget" in ins_sz)
    sz_budget = ins_sz.get("db_size_budget", {})
    test("db_size_budget has over_budget key", "over_budget" in sz_budget)
    test("db_size_budget has size_mb key", "size_mb" in sz_budget)
    test("db_size_budget has budget_mb key", "budget_mb" in sz_budget)

    # Test: over-budget triggers alert in compute_insights
    os.environ["SK_DB_SIZE_BUDGET_MB"] = "0"  # everything exceeds it
    kh.get_db = _get_db_factory(uri_sz)
    kh.DB_PATH = _db_small_path
    ins_over_budget = kh.compute_insights()
    kh.get_db = orig_get_db
    kh.DB_PATH = _sz_db_path_bak
    os.environ.pop("SK_DB_SIZE_BUDGET_MB", None)
    db_sz.close()

    test(
        "db-size-over-budget alert fires when DB exceeds threshold",
        any(a.get("id") == "db-size-over-budget" for a in ins_over_budget.get("quality_alerts", [])),
    )
    test(
        "db-size-over-budget alert absent when DB under threshold",
        not any(a.get("id") == "db-size-over-budget" for a in ins_sz.get("quality_alerts", [])),
    )

finally:
    shutil.rmtree(str(_size_tmp), ignore_errors=True)

# ===========================================================================
# integrity_lints JSON serialisability
# ===========================================================================

section("integrity_lints — JSON serialisability")

try:
    json.dumps(ins_dangle.get("integrity_lints", {}))
    test("integrity_lints is JSON serializable (dangling)", True)
except TypeError as e:
    test("integrity_lints is JSON serializable (dangling)", False, str(e))

try:
    json.dumps(ins_contra.get("integrity_lints", {}))
    test("integrity_lints is JSON serializable (contradiction)", True)
except TypeError as e:
    test("integrity_lints is JSON serializable (contradiction)", False, str(e))

try:
    json.dumps(ins_sid_coll.get("integrity_lints", {}))
    test("integrity_lints is JSON serializable (stable_id collision)", True)
except TypeError as e:
    test("integrity_lints is JSON serializable (stable_id collision)", False, str(e))

# ===========================================================================
# format_insights_report — integrity lint rendering
# ===========================================================================

section("format_insights_report — integrity lint section")

_il_sample = {
    "generated_at": "2025-01-01T00:00:00+00:00",
    "summary": "Test",
    "overview": {
        "health_score": 50,
        "total_entries": 5,
        "sessions": 1,
        "high_confidence_pct": 20.0,
        "low_confidence_pct": 30.0,
        "stale_pct": 0.0,
        "relation_density": 0.0,
        "embedding_pct": 0.0,
    },
    "quality_alerts": [
        {
            "id": "dangling-relations",
            "title": "1 dangling relation(s)",
            "severity": "warning",
            "detail": "Some relations reference missing entries.",
        },
        {
            "id": "stable-id-collision",
            "title": "1 stable_id collision(s)",
            "severity": "warning",
            "detail": "Duplicate stable_ids detected.",
        },
        {
            "id": "contradiction-pairs",
            "title": "1 contradiction pair(s)",
            "severity": "info",
            "detail": "Contradicting entries detected.",
        },
        {
            "id": "db-size-over-budget",
            "title": "DB size exceeds budget (10.0 MB / 0.0 MB)",
            "severity": "warning",
            "detail": "Consider archiving.",
        },
    ],
    "recommended_actions": [],
    "recurring_noise_titles": [],
    "hot_files": [],
    "entries": {},
    "integrity_lints": {
        "dangling_relations": 1,
        "stable_id_collisions": 1,
        "contradictions": [
            {
                "entry_a_id": 1,
                "entry_a_title": "Always use X",
                "entry_b_id": 2,
                "entry_b_title": "Never use X",
                "shared_tags": "tool-x",
            }
        ],
    },
    "db_size_budget": {
        "size_bytes": 10485760,
        "budget_bytes": 0,
        "over_budget": True,
        "size_mb": 10.0,
        "budget_mb": 0.0,
    },
    "sync_advisory": {"status": "ok", "reasons": [], "checklist": "docs/SYNC-MATRIX.md"},
}
_il_report = kh.format_insights_report(_il_sample)
test(
    "format_insights_report renders integrity lints section",
    "Integrity" in _il_report or "dangling" in _il_report.lower() or "Dangling" in _il_report,
)

# ===========================================================================
# Soft-delete filtering (#387)
# ===========================================================================

section("soft-delete filtering in compute_health and compute_insights")

_KE_SCHEMA_WITH_SOFTDELETE = _KE_SCHEMA.rstrip().rstrip(")").rstrip() + ",\n    deleted_at TEXT DEFAULT NULL\n)"

uri_sd = _new_uri()
db_sd = sqlite3.connect(uri_sd, uri=True)
db_sd.row_factory = sqlite3.Row
db_sd.execute(_KE_SCHEMA_WITH_SOFTDELETE)
db_sd.execute(_SCHEMA_VER_SCHEMA)
db_sd.execute(_RELATIONS_SCHEMA)
db_sd.execute(_ENTITY_REL_SCHEMA)
db_sd.execute(_EMBEDDINGS_SCHEMA)
# Insert 3 active entries and 1 soft-deleted
db_sd.execute(
    "INSERT INTO knowledge_entries (category, title, confidence, first_seen, last_seen, deleted_at)"
    " VALUES ('mistake', 'active1', 0.6, '2025-01-01', '2025-01-01', NULL)"
)
db_sd.execute(
    "INSERT INTO knowledge_entries (category, title, confidence, first_seen, last_seen, deleted_at)"
    " VALUES ('pattern', 'active2', 0.8, '2025-01-01', '2025-01-01', NULL)"
)
db_sd.execute(
    "INSERT INTO knowledge_entries (category, title, confidence, first_seen, last_seen, deleted_at)"
    " VALUES ('decision', 'active3', 0.7, '2025-01-01', '2025-01-01', NULL)"
)
db_sd.execute(
    "INSERT INTO knowledge_entries (category, title, confidence, first_seen, last_seen, deleted_at)"
    " VALUES ('mistake', 'deleted_entry', 0.9, '2025-01-01', '2025-01-01', '2025-06-01')"
)
db_sd.commit()
kh.get_db = _get_db_factory(uri_sd)
h_sd = kh.compute_health()
kh.get_db = orig_get_db
db_sd.close()

test("compute_health excludes soft-deleted entries from total", h_sd["total"] == 3, f"total={h_sd['total']}")
test("compute_health score is still a number", isinstance(h_sd["score"], (int, float)))

# ===========================================================================
# Token budget audit (#397)
# ===========================================================================

section("token budget audit in compute_insights")

uri_tb = _new_uri()
db_tb = _make_db(uri_tb)
# Insert entries with large est_tokens sum (>100000)
_insert_entries(
    db_tb,
    [{"category": "mistake", "title": f"big{i}", "confidence": 0.5, "est_tokens": 12000} for i in range(10)],
)
# Update est_tokens via raw SQL since _insert_entries doesn't handle it
db_tb.execute("UPDATE knowledge_entries SET est_tokens = 12000")
db_tb.commit()
kh.get_db = _get_db_factory(uri_tb)
ins_tb = kh.compute_insights()
kh.get_db = orig_get_db
db_tb.close()

_tb_alert_ids = {a["id"] for a in ins_tb.get("quality_alerts", [])}
test(
    "token-budget-exceeded alert fires when est_tokens sum > 100000",
    "token-budget-exceeded" in _tb_alert_ids,
    f"alerts={_tb_alert_ids}",
)

# Small DB should NOT trigger token budget alert
uri_tb_small = _new_uri()
db_tb_small = _make_db(uri_tb_small)
_insert_entries(db_tb_small, [{"category": "mistake", "title": "tiny", "confidence": 0.5}])
db_tb_small.execute("UPDATE knowledge_entries SET est_tokens = 100")
db_tb_small.commit()
kh.get_db = _get_db_factory(uri_tb_small)
ins_tb_small = kh.compute_insights()
kh.get_db = orig_get_db
db_tb_small.close()
_tb_small_alert_ids = {a["id"] for a in ins_tb_small.get("quality_alerts", [])}
test(
    "token-budget-exceeded alert absent when est_tokens sum small",
    "token-budget-exceeded" not in _tb_small_alert_ids,
)

# ===========================================================================
# compute_confidence_decay (#400)
# ===========================================================================

section("compute_confidence_decay function")

uri_cd = _new_uri()
db_cd = _make_db(uri_cd)
# Insert one stale entry (old last_seen) and one fresh entry
_insert_entries(
    db_cd,
    [
        {"category": "mistake", "title": "stale_entry", "confidence": 0.5, "last_seen": "2020-01-01"},
        {"category": "pattern", "title": "fresh_entry", "confidence": 0.8, "last_seen": "2099-01-01"},
    ],
)
kh.get_db = _get_db_factory(uri_cd)
result_cd = kh.compute_confidence_decay(stale_days=90, decay_rate=0.05)
kh.get_db = orig_get_db

test("compute_confidence_decay returns dict", isinstance(result_cd, dict))
test("compute_confidence_decay has decayed_count", "decayed_count" in result_cd)
test("compute_confidence_decay has entries list", "entries" in result_cd and isinstance(result_cd["entries"], list))
test("stale entry was decayed", result_cd["decayed_count"] >= 1, f"count={result_cd['decayed_count']}")
test(
    "decayed entry confidence reduced",
    any(e["new_confidence"] < e["old_confidence"] for e in result_cd["entries"]),
    f"entries={result_cd['entries']}",
)
# Fresh entry (last_seen far in future) should not be decayed
test(
    "fresh entry not in decayed list",
    not any(e["title"] == "fresh_entry" for e in result_cd["entries"]),
)
db_cd.close()

# Zero decay_rate should do nothing
uri_cd0 = _new_uri()
db_cd0 = _make_db(uri_cd0)
_insert_entries(db_cd0, [{"category": "mistake", "title": "nodecay", "confidence": 0.5, "last_seen": "2020-01-01"}])
kh.get_db = _get_db_factory(uri_cd0)
result_cd0 = kh.compute_confidence_decay(stale_days=90, decay_rate=0.0)
kh.get_db = orig_get_db
db_cd0.close()
test("zero decay_rate returns 0 decayed", result_cd0["decayed_count"] == 0)

# ===========================================================================
# compute_eviction_candidates (#401)
# ===========================================================================

section("compute_eviction_candidates function")

uri_ev = _new_uri()
db_ev = _make_db(uri_ev)
_insert_entries(
    db_ev,
    [
        # Low-value: old, low confidence, single occurrence
        {
            "category": "mistake",
            "title": "low_value",
            "confidence": 0.1,
            "occurrence_count": 1,
            "first_seen": "2020-01-01",
        },
        # High-value: recent, high confidence, many occurrences
        {
            "category": "pattern",
            "title": "high_value",
            "confidence": 0.95,
            "occurrence_count": 50,
            "first_seen": "2025-06-01",
        },
        {
            "category": "pattern",
            "title": "mid_value",
            "confidence": 0.6,
            "occurrence_count": 5,
            "first_seen": "2024-01-01",
        },
    ],
)
kh.get_db = _get_db_factory(uri_ev)
result_ev = kh.compute_eviction_candidates(limit=5)
kh.get_db = orig_get_db
db_ev.close()

test("compute_eviction_candidates returns dict", isinstance(result_ev, dict))
test("eviction result has candidates list", "candidates" in result_ev)
candidates = result_ev["candidates"]
test("eviction returns candidates", len(candidates) >= 1)
test(
    "lowest-scored candidate is first",
    candidates[0]["eviction_score"] <= candidates[-1]["eviction_score"] if len(candidates) > 1 else True,
)
test("each candidate has id field", all("id" in c for c in candidates))
test("each candidate has title field", all("title" in c for c in candidates))
test("each candidate has eviction_score", all("eviction_score" in c for c in candidates))
test(
    "low_value entry is top eviction candidate",
    candidates[0]["title"] == "low_value" if candidates else False,
    f"top candidate: {candidates[0]['title'] if candidates else 'none'}",
)

# Limit parameter respected
uri_ev2 = _new_uri()
db_ev2 = _make_db(uri_ev2)
_insert_entries(db_ev2, [{"category": "mistake", "title": f"e{i}", "confidence": 0.5} for i in range(10)])
kh.get_db = _get_db_factory(uri_ev2)
result_ev2 = kh.compute_eviction_candidates(limit=3)
kh.get_db = orig_get_db
db_ev2.close()
test("eviction limit parameter respected", len(result_ev2["candidates"]) <= 3)

print(f"\n{'=' * 50}")
print(f"Results: {_PASS} passed, {_FAIL} failed")
if _ERRORS:
    print("\nFailed tests:")
    for e in _ERRORS:
        print(e)

sys.exit(0 if _FAIL == 0 else 1)
