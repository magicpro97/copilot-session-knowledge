#!/usr/bin/env python3
"""
test_sync_capture.py — regression coverage for local sync capture enqueue paths.

Run:
    python3 test_sync_capture.py
"""

import importlib.util
import io
import json
import os
import sqlite3
import sys
from contextlib import redirect_stdout
from pathlib import Path

if os.name == "nt":
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

REPO = Path(__file__).parent.parent
ARTIFACT_DIR = REPO / ".sync-capture-test-artifacts"

PASS = 0
FAIL = 0


def test(name: str, passed: bool, detail: str = ""):
    global PASS, FAIL
    if passed:
        PASS += 1
        print(f"  ✅ {name}")
    else:
        FAIL += 1
        print(f"  ❌ {name}" + (f" — {detail}" if detail else ""))


def load_module(name: str, filename: str):
    spec = importlib.util.spec_from_file_location(name, str(REPO / filename))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def latest_payload_for_table(db: sqlite3.Connection, table_name: str):
    row = db.execute(
        """
        SELECT row_payload
        FROM sync_ops
        WHERE table_name = ?
        ORDER BY id DESC
        LIMIT 1
        """,
        (table_name,),
    ).fetchone()
    return json.loads(row[0]) if row and row[0] else None


def has_unique_stable_index(db: sqlite3.Connection, table_name: str) -> bool:
    for idx in db.execute(f"PRAGMA index_list({table_name})").fetchall():
        idx_name = idx[1]
        is_unique = int(idx[2]) == 1
        if not is_unique:
            continue
        cols = [row[2] for row in db.execute(f"PRAGMA index_info({idx_name})").fetchall()]
        if cols == ["stable_id"]:
            return True
    return False


def reset_artifacts():
    if ARTIFACT_DIR.exists():
        for p in sorted(ARTIFACT_DIR.rglob("*"), reverse=True):
            if p.is_file():
                p.unlink(missing_ok=True)
            elif p.is_dir():
                p.rmdir()
        ARTIFACT_DIR.rmdir()
    ARTIFACT_DIR.mkdir(parents=True, exist_ok=True)


def write_sample_session(root: Path) -> tuple[Path, str]:
    session_id = "aaaaaaaa-0000-0000-0000-000000000001"
    sdir = root / session_id
    (sdir / "checkpoints").mkdir(parents=True, exist_ok=True)
    (sdir / "research").mkdir(parents=True, exist_ok=True)
    (sdir / "files").mkdir(parents=True, exist_ok=True)

    (sdir / "checkpoints" / "index.md").write_text(
        "| Seq | Title | File |\n| 1 | Sync Capture | 001-sync-capture.md |\n",
        encoding="utf-8",
    )
    (sdir / "checkpoints" / "001-sync-capture.md").write_text(
        """
<overview>sync summary</overview>
<history>implemented stable write path</history>
<work_done>fixed bug root cause and should avoid wrong fallback</work_done>
<technical_details>error was caused by wrong config, fixed with stable id write.</technical_details>
<important_files>build-session-index.py</important_files>
<next_steps>add regression coverage</next_steps>
""".strip(),
        encoding="utf-8",
    )
    (sdir / "research" / "001-notes.md").write_text("research content", encoding="utf-8")
    (sdir / "files" / "artifact.txt").write_text("artifact content", encoding="utf-8")
    (sdir / "plan.md").write_text("plan for capture regression testing", encoding="utf-8")
    return sdir, session_id


print("\n🔎 sync capture regression")
reset_artifacts()

build = load_module("build_session_index_capture", "build-session-index.py")
extract = load_module("extract_knowledge_capture", "extract-knowledge.py")
learn = load_module("learn_capture", "learn.py")
embed = load_module("embed_capture", "embed.py")

replica_fallback_db = sqlite3.connect(":memory:")
replica_fallback_db.execute("""
    CREATE TABLE sync_metadata (
        key TEXT PRIMARY KEY,
        value TEXT NOT NULL,
        updated_at TEXT DEFAULT (datetime('now'))
    )
""")
replica_fallback_db.execute(
    "INSERT INTO sync_metadata (key, value) VALUES ('local_replica_id', 'replica-from-metadata')"
)
fallback_replica_id = embed._get_local_replica_id(replica_fallback_db)
test(
    "embed local replica helper reads sync_metadata when sync_state missing",
    fallback_replica_id == "replica-from-metadata",
    fallback_replica_id,
)
fallback_origin = embed._normalize_search_feedback_origin("local", fallback_replica_id)
test(
    "embed local replica helper avoids empty local-origin normalization",
    bool(fallback_origin) and fallback_origin == "replica-from-metadata",
    fallback_origin,
)
replica_fallback_db.close()

db_path = ARTIFACT_DIR / "knowledge.db"
session_root = ARTIFACT_DIR / "sessions"
session_root.mkdir(parents=True, exist_ok=True)
session_dir, session_id = write_sample_session(session_root)

db = build.create_db(db_path)
build.index_session(db, session_dir, incremental=False)
db.commit()

local_replica_id = db.execute(
    "SELECT value FROM sync_state WHERE key='local_replica_id'"
).fetchone()
replica_value = local_replica_id[0] if local_replica_id else ""
test("build writes set non-shared local_replica_id", bool(replica_value) and replica_value != "local", str(replica_value))

ops_by_table = {
    row[0]: row[1]
    for row in db.execute(
        """
        SELECT table_name, COUNT(*)
        FROM sync_ops
        WHERE table_name IN ('sessions', 'documents', 'sections')
        GROUP BY table_name
        """
    ).fetchall()
}
test("build captures sessions writes", ops_by_table.get("sessions", 0) > 0, str(ops_by_table))
test("build captures documents writes", ops_by_table.get("documents", 0) > 0, str(ops_by_table))
test("build captures sections writes", ops_by_table.get("sections", 0) > 0, str(ops_by_table))
doc_payload = latest_payload_for_table(db, "documents")
sec_payload = latest_payload_for_table(db, "sections")
test("documents payload omits local id",
     isinstance(doc_payload, dict) and "id" not in doc_payload,
     str(doc_payload))
test("sections payload uses document_stable_id",
     isinstance(sec_payload, dict) and "document_id" not in sec_payload and bool(sec_payload.get("document_stable_id")),
     str(sec_payload))
test("build schema enforces unique documents.stable_id", has_unique_stable_index(db, "documents"))
test("build schema enforces unique sections.stable_id", has_unique_stable_index(db, "sections"))

policy_rows = {
    row[0]: row[1]
    for row in db.execute(
        "SELECT table_name, sync_scope FROM sync_table_policies WHERE table_name IN ('sessions', 'documents', 'sections', 'knowledge_entries', 'entity_relations', 'search_feedback')"
    ).fetchall()
}
test("sync policy sessions canonical", policy_rows.get("sessions") == "canonical", str(policy_rows))
test("sync policy documents canonical", policy_rows.get("documents") == "canonical", str(policy_rows))
test("sync policy sections canonical", policy_rows.get("sections") == "canonical", str(policy_rows))

extract.ensure_tables(db)
extract.extract_from_sections(db, session_ids=[session_id])
ke_ops = db.execute(
    "SELECT COUNT(*) FROM sync_ops WHERE table_name='knowledge_entries'"
).fetchone()[0]
test("extract captures knowledge_entries writes", ke_ops > 0, f"count={ke_ops}")
test("extract schema enforces unique knowledge_entries.stable_id", has_unique_stable_index(db, "knowledge_entries"))
test("extract schema enforces unique knowledge_relations.stable_id", has_unique_stable_index(db, "knowledge_relations"))
test("extract schema enforces unique entity_relations.stable_id", has_unique_stable_index(db, "entity_relations"))
ke_payload = latest_payload_for_table(db, "knowledge_entries")
test("knowledge_entries payload omits local ids",
     isinstance(ke_payload, dict)
     and "id" not in ke_payload
     and "document_id" not in ke_payload
     and bool(ke_payload.get("document_stable_id")),
     str(ke_payload))
manual_sid_a = extract._knowledge_stable_id("manual-rel-session", "mistake", "Manual A", "manual/a")
manual_sid_b = extract._knowledge_stable_id("manual-rel-session", "pattern", "Manual B", "manual/b")
db.execute(
    """
    INSERT INTO knowledge_entries (session_id, category, title, stable_id, content, topic_key)
    VALUES (?, ?, ?, ?, ?, ?)
    """,
    ("manual-rel-session", "mistake", "Manual A", manual_sid_a, "a", "manual/a"),
)
db.execute(
    """
    INSERT INTO knowledge_entries (session_id, category, title, stable_id, content, topic_key)
    VALUES (?, ?, ?, ?, ?, ?)
    """,
    ("manual-rel-session", "pattern", "Manual B", manual_sid_b, "b", "manual/b"),
)
extract.extract_relations(db)
kr_payload = latest_payload_for_table(db, "knowledge_relations")
test("knowledge_relations payload uses stable references",
     isinstance(kr_payload, dict)
     and "source_id" not in kr_payload
     and "target_id" not in kr_payload
     and bool(kr_payload.get("source_stable_id"))
     and bool(kr_payload.get("target_stable_id")),
     str(kr_payload))
db.commit()

learn.DB_PATH = db_path
entry_id = learn.add_entry(
    category="pattern",
    title="Sync capture via learn",
    content="stable knowledge write for capture",
    session_id="manual-sync-session",
    skip_gate=True,
    skip_scan=True,
    quiet=True,
)
learn.add_relation("sync-capture", "writes_to", "entity-relations", session_id="manual-sync-session")
learn_ke_sid = db.execute(
    "SELECT stable_id FROM knowledge_entries WHERE id = ?",
    (entry_id,),
).fetchone()
learn_er_count = db.execute(
    "SELECT COUNT(*) FROM sync_ops WHERE table_name='entity_relations'"
).fetchone()[0]
test("learn captures knowledge_entries writes", learn_ke_sid is not None and bool(learn_ke_sid[0]), str(learn_ke_sid))
test("learn captures entity_relations writes", learn_er_count > 0, f"count={learn_er_count}")
learn_ke_payload = latest_payload_for_table(db, "knowledge_entries")
learn_er_payload = latest_payload_for_table(db, "entity_relations")
test("learn knowledge_entries payload omits local id",
     isinstance(learn_ke_payload, dict) and "id" not in learn_ke_payload and "document_id" not in learn_ke_payload,
     str(learn_ke_payload))
test("entity_relations payload omits local id",
     isinstance(learn_er_payload, dict) and "id" not in learn_er_payload,
     str(learn_er_payload))

db.execute(
    """
    CREATE TABLE IF NOT EXISTS search_feedback (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        query TEXT,
        result_id TEXT,
        result_kind TEXT,
        verdict INTEGER NOT NULL CHECK(verdict IN (-1,0,1)),
        comment TEXT,
        user_agent TEXT,
        created_at TEXT NOT NULL,
        origin_replica_id TEXT DEFAULT '',
        stable_id TEXT
    )
    """
)
db.execute(
    """
    INSERT INTO search_feedback (query, result_id, result_kind, verdict, created_at, origin_replica_id, stable_id)
    VALUES (?, ?, ?, ?, ?, '', '')
    """,
    ("capture query", "1", "knowledge", 1, "2026-01-01T00:00:00Z"),
)
db.execute(
    """
    INSERT INTO search_feedback (query, result_id, result_kind, verdict, created_at, origin_replica_id, stable_id)
    VALUES (?, ?, ?, ?, ?, 'local', '')
    """,
    ("capture query local", "2", "knowledge", -1, "2026-01-01T00:01:00Z"),
)
embed.ensure_embedding_tables(db)
db.commit()
test("embed schema enforces unique search_feedback.stable_id", has_unique_stable_index(db, "search_feedback"))

sf = db.execute(
    "SELECT stable_id, origin_replica_id FROM search_feedback WHERE query='capture query'"
).fetchone()
sf_ops = db.execute(
    "SELECT COUNT(*) FROM sync_ops WHERE table_name='search_feedback'"
).fetchone()[0]
test("embed backfills search_feedback stable_id", sf is not None and bool(sf[0]), str(sf))
test("embed backfills search_feedback replica_id", sf is not None and sf[1] == replica_value, str(sf))
expected_sf_sid = embed._stable_sha256(
    "search_feedback", "2026-01-01T00:00:00Z", "knowledge", "1", 1, "capture query", replica_value
)
test("embed normalizes empty search_feedback origin deterministically",
     sf is not None and sf[0] == expected_sf_sid,
     str(sf))
sf_local = db.execute(
    "SELECT stable_id, origin_replica_id FROM search_feedback WHERE query='capture query local'"
).fetchone()
expected_sf_local_sid = embed._stable_sha256(
    "search_feedback", "2026-01-01T00:01:00Z", "knowledge", "2", -1, "capture query local", replica_value
)
test("embed normalizes 'local' search_feedback origin to local replica_id",
     sf_local is not None and sf_local[1] == replica_value and sf_local[0] == expected_sf_local_sid,
     str(sf_local))
test("embed captures search_feedback writes", sf_ops > 0, f"count={sf_ops}")
sf_payload = latest_payload_for_table(db, "search_feedback")
test("search_feedback payload omits local id",
     isinstance(sf_payload, dict) and "id" not in sf_payload,
     str(sf_payload))

build_dupe_db = sqlite3.connect(":memory:")
build_dupe_db.execute("CREATE TABLE documents (id INTEGER PRIMARY KEY AUTOINCREMENT, stable_id TEXT)")
build_dupe_db.execute("CREATE TABLE sections (id INTEGER PRIMARY KEY AUTOINCREMENT, stable_id TEXT)")
build_dupe_db.execute("INSERT INTO documents (stable_id) VALUES ('dup-doc'), ('dup-doc'), ('uniq-doc')")
build_dupe_db.execute("INSERT INTO sections (stable_id) VALUES ('dup-sec'), ('dup-sec'), ('uniq-sec')")
build_enforce_error = ""
try:
    build._enforce_stable_id_uniqueness(build_dupe_db)
except Exception as exc:
    build_enforce_error = str(exc)
test("build uniqueness enforcement dedupes preexisting stable_id collisions",
     build_enforce_error == "",
     build_enforce_error)
if build_enforce_error == "":
    test("build dedupe keeps one duplicate documents row",
         build_dupe_db.execute("SELECT COUNT(*) FROM documents WHERE stable_id='dup-doc'").fetchone()[0] == 1)
    test("build dedupe keeps one duplicate sections row",
         build_dupe_db.execute("SELECT COUNT(*) FROM sections WHERE stable_id='dup-sec'").fetchone()[0] == 1)
    test("build dedupe still creates unique documents index", has_unique_stable_index(build_dupe_db, "documents"))
    test("build dedupe still creates unique sections index", has_unique_stable_index(build_dupe_db, "sections"))
build_dupe_db.close()

extract_dupe_db = sqlite3.connect(":memory:")
extract_dupe_db.execute("CREATE TABLE knowledge_entries (id INTEGER PRIMARY KEY AUTOINCREMENT, stable_id TEXT)")
extract_dupe_db.execute("CREATE TABLE knowledge_relations (id INTEGER PRIMARY KEY AUTOINCREMENT, stable_id TEXT)")
extract_dupe_db.execute("CREATE TABLE entity_relations (id INTEGER PRIMARY KEY AUTOINCREMENT, stable_id TEXT)")
extract_dupe_db.execute("INSERT INTO knowledge_entries (stable_id) VALUES ('dup-ke'), ('dup-ke'), ('uniq-ke')")
extract_dupe_db.execute("INSERT INTO knowledge_relations (stable_id) VALUES ('dup-kr'), ('dup-kr'), ('uniq-kr')")
extract_dupe_db.execute("INSERT INTO entity_relations (stable_id) VALUES ('dup-er'), ('dup-er'), ('uniq-er')")
extract_enforce_error = ""
try:
    extract._enforce_stable_id_uniqueness(extract_dupe_db)
except Exception as exc:
    extract_enforce_error = str(exc)
test("extract uniqueness enforcement dedupes preexisting stable_id collisions",
     extract_enforce_error == "",
     extract_enforce_error)
if extract_enforce_error == "":
    test("extract dedupe keeps one duplicate knowledge_entries row",
         extract_dupe_db.execute("SELECT COUNT(*) FROM knowledge_entries WHERE stable_id='dup-ke'").fetchone()[0] == 1)
    test("extract dedupe keeps one duplicate knowledge_relations row",
         extract_dupe_db.execute("SELECT COUNT(*) FROM knowledge_relations WHERE stable_id='dup-kr'").fetchone()[0] == 1)
    test("extract dedupe keeps one duplicate entity_relations row",
         extract_dupe_db.execute("SELECT COUNT(*) FROM entity_relations WHERE stable_id='dup-er'").fetchone()[0] == 1)
    test("extract dedupe still creates unique knowledge_entries index", has_unique_stable_index(extract_dupe_db, "knowledge_entries"))
    test("extract dedupe still creates unique knowledge_relations index", has_unique_stable_index(extract_dupe_db, "knowledge_relations"))
    test("extract dedupe still creates unique entity_relations index", has_unique_stable_index(extract_dupe_db, "entity_relations"))
extract_dupe_db.close()

local_only_ops = db.execute(
    "SELECT COUNT(*) FROM sync_ops WHERE table_name IN ('knowledge_fts', 'ke_fts', 'sessions_fts', 'event_offsets', 'embeddings', 'embedding_meta', 'tfidf_model')"
).fetchone()[0]
test("capture does not enqueue local_only tables", local_only_ops == 0, f"count={local_only_ops}")

pending_replica_ids = {
    row[0]
    for row in db.execute(
        "SELECT DISTINCT t.replica_id FROM sync_txns t JOIN sync_ops o ON o.txn_id=t.txn_id WHERE t.status='pending'"
    ).fetchall()
}
test("pending txns use local replica_id", pending_replica_ids == {replica_value}, str(pending_replica_ids))

runtime_tools = ARTIFACT_DIR / "runtime-tools"
runtime_state = ARTIFACT_DIR / "runtime-state"
runtime_tools.mkdir(parents=True, exist_ok=True)
runtime_state.mkdir(parents=True, exist_ok=True)

sync_config = load_module("sync_config_runtime_ops", "sync-config.py")
sync_config.TOOLS_DIR = runtime_tools
sync_config.CONFIG_PATH = runtime_tools / "sync-config.json"

os.environ["SYNC_GATEWAY_URL_TEST"] = "http://127.0.0.1:8787/"
sync_config.main.__globals__["sys"].argv = ["sync-config.py", "--setup-env", "SYNC_GATEWAY_URL_TEST"]
sync_config.main()
config_status = sync_config.get_status()
test(
    "sync-config setup-env writes normalized gateway URL",
    config_status.get("connection_string") == "http://127.0.0.1:8787",
    str(config_status),
)

buf = io.StringIO()
sync_config.main.__globals__["sys"].argv = ["sync-config.py", "--status", "--json"]
with redirect_stdout(buf):
    sync_config.main()
cfg_json = json.loads(buf.getvalue())
test(
    "sync-config status json includes local-first contract",
    cfg_json.get("client_contract") == "http-gateway" and cfg_json.get("direct_db_sync") is False,
    str(cfg_json),
)

sync_status = load_module("sync_status_runtime_ops", "sync-status.py")
sync_status.SESSION_STATE = runtime_state
sync_status.TOOLS_DIR = runtime_tools
sync_status.CONFIG_PATH = runtime_tools / "sync-config.json"
sync_status.DB_PATH = db_path

(runtime_state / ".watcher.lock").write_text(str(os.getpid()), encoding="utf-8")
(runtime_state / "watcher.log").write_text("watch log\n", encoding="utf-8")
sync_status.CONFIG_PATH.write_text(json.dumps({"connection_string": ""}), encoding="utf-8")

runtime_status = sync_status.collect_status(db_path=db_path, check_health=False)
watch_status = runtime_status.get("watch_status", {})
test(
    "sync-status reports watcher pid from lock",
    watch_status.get("pid") == os.getpid() and watch_status.get("pid_running") is True,
    str(watch_status),
)
test(
    "sync-status watch status exposes service manager surface",
    bool(watch_status.get("managed_by")) and bool(watch_status.get("manager_state")),
    str(watch_status),
)

runtime_audit_ok = sync_status._runtime_audit(runtime_status)
test(
    "sync-status audit passes critical checks for local-first unconfigured runtime",
    runtime_audit_ok.get("ok") is True and runtime_audit_ok.get("critical_failures") == 0,
    str(runtime_audit_ok),
)

sync_status.CONFIG_PATH.write_text(json.dumps({"connection_string": "http://127.0.0.1:8787"}), encoding="utf-8")
configured_skip_status = sync_status.collect_status(db_path=db_path, check_health=False)
test(
    "sync-status marks configured gateway health as skipped when probing disabled",
    configured_skip_status.get("gateway_health", {}).get("status") == "skipped",
    str(configured_skip_status.get("gateway_health")),
)
configured_skip_audit = sync_status._runtime_audit(configured_skip_status)
configured_skip_gateway = next(
    (chk for chk in configured_skip_audit.get("checks", []) if chk.get("name") == "gateway-health"),
    {},
)
test(
    "sync-status audit does not false-fail configured gateway when probing is skipped",
    configured_skip_audit.get("ok") is True and configured_skip_gateway.get("ok") is True,
    str(configured_skip_audit),
)

health_buf = io.StringIO()
health_exit = 0
sync_status.main.__globals__["sys"].argv = ["sync-status.py", "--health-check", "--no-health", "--json"]
orig_collect_status = sync_status.collect_status
sync_status.collect_status = lambda check_health=True: orig_collect_status(db_path=db_path, check_health=check_health)
try:
    with redirect_stdout(health_buf):
        sync_status.main()
except SystemExit as exc:
    health_exit = int(exc.code)
finally:
    sync_status.collect_status = orig_collect_status
health_payload = json.loads(health_buf.getvalue())
test(
    "sync-status health-check treats configured skipped probe as healthy",
    health_exit == 0 and health_payload.get("gateway_status") == "skipped" and health_payload.get("ok") is True,
    f"exit={health_exit} payload={health_payload}",
)

missing_db_status = sync_status.collect_status(
    db_path=ARTIFACT_DIR / "missing-runtime.db",
    check_health=False,
)
runtime_audit_fail = sync_status._runtime_audit(missing_db_status)
test(
    "sync-status audit fails when local DB is missing",
    runtime_audit_fail.get("ok") is False and runtime_audit_fail.get("critical_failures", 0) > 0,
    str(runtime_audit_fail),
)

db.close()

print(f"\nResult: {PASS} passed, {FAIL} failed")
if FAIL:
    sys.exit(1)
