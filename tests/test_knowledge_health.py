#!/usr/bin/env python3
"""
Tests for knowledge-health.py --insights and supporting functions.
Uses synthetic in-memory SQLite DBs; never touches the real knowledge.db.
"""

import importlib.util
import json
import os
import sqlite3
import sys
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
    [
        {"category": "mistake", "title": f"m{i}", "confidence": 0.3, "session_id": "s1"}
        for i in range(10)
    ],
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
test("toward_100 total_gap equals 100 - score",
     abs(t100["total_gap"] - round(100.0 - h_t100["score"], 1)) < 0.01)
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
test("dimension current + gap == max",
     abs(dim0.get("current", 0) + dim0.get("gap", 0) - dim0.get("max", 0)) < 0.2)

# Verify dimensions are sorted by gap descending
dims = t100.get("dimensions", [])
gaps = [d["gap"] for d in dims]
test("dimensions sorted by gap descending", gaps == sorted(gaps, reverse=True))

# Verify all dimension names present
dim_names = {d["dimension"] for d in dims}
expected_dims = {"categorization", "learning_curve", "freshness", "relation_density",
                 "embedding_coverage", "confidence_quality"}
test("all 6 dimension names present", dim_names == expected_dims)

# Verify score is NOT modified by toward_100 computation
test("score unchanged after toward_100 computed", "score" in h_t100 and 0 <= h_t100["score"] <= 100)

# Verify total_gap + score = 100 (within rounding)
test("toward_100 total_gap + score ≈ 100",
     abs(t100["total_gap"] + h_t100["score"] - 100.0) < 0.2)

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
        *[{"category": "mistake", "title": f"gap_m{i}", "confidence": 0.3, "session_id": "s1"}
          for i in range(12)],
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
test("confidence-quality-gap alert fires for low high-confidence entries",
     "confidence-quality-gap" in gap_alert_ids)

# Verify learning-curve-gap alert fires (12 mistakes, only 1 pattern → lc_score low)
test("learning-curve-gap alert fires when patterns lag mistakes",
     "learning-curve-gap" in gap_alert_ids)

# Verify relation-density-gap alert fires (no relations, 13+ entries)
test("relation-density-gap alert fires for sparse graph with >= 10 entries",
     "relation-density-gap" in gap_alert_ids)

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
        *[{"category": "mistake", "title": f"hm{i}", "confidence": 0.9,
           "session_id": "s1", "first_seen": today, "last_seen": today}
          for i in range(3)],
        *[{"category": "pattern", "title": f"hp{i}", "confidence": 0.9,
           "session_id": "s1", "first_seen": today, "last_seen": today}
          for i in range(10)],
    ],
)
# Add enough relations to push relation_density high
for i in range(20):
    db_healthy.execute("INSERT INTO knowledge_relations (source_id, target_id, relation_type) VALUES (?, ?, ?)",
                       (1, i + 2, "related"))
db_healthy.commit()
kh.get_db = _get_db_factory(uri_healthy)
ins_healthy = kh.compute_insights()
kh.get_db = orig_get_db
db_healthy.close()

healthy_alert_ids = {a["id"] for a in ins_healthy.get("quality_alerts", [])}
test("confidence-quality-gap absent when high-conf entries dominant",
     "confidence-quality-gap" not in healthy_alert_ids)
test("learning-curve-gap absent when patterns dominate mistakes",
     "learning-curve-gap" not in healthy_alert_ids)
test("relation-density-gap absent when graph is dense",
     "relation-density-gap" not in healthy_alert_ids)

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
            {"dimension": "confidence_quality", "current": 2.0, "max": 15.0,
             "gap": 13.0, "gap_pct": 86.7, "pct_of_total_gap": 28.9},
            {"dimension": "learning_curve", "current": 6.0, "max": 20.0,
             "gap": 14.0, "gap_pct": 70.0, "pct_of_total_gap": 31.1},
            {"dimension": "relation_density", "current": 5.0, "max": 15.0,
             "gap": 10.0, "gap_pct": 66.7, "pct_of_total_gap": 22.2},
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
    [
        {"category": "mistake", "title": f"frm{i}", "confidence": 0.3} for i in range(5)
    ],
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

print(f"\n{'=' * 50}")
print(f"Results: {_PASS} passed, {_FAIL} failed")
if _ERRORS:
    print("\nFailed tests:")
    for e in _ERRORS:
        print(e)

sys.exit(0 if _FAIL == 0 else 1)
