#!/usr/bin/env python3
"""
extract-knowledge.py — Extract structured knowledge from session checkpoints

Parses checkpoint sections to identify and categorize:
  - Patterns: Reusable coding/architecture best practices
  - Mistakes: Errors made and lessons learned
  - Decisions: Technical choices and their rationale
  - Tools: Tool configurations and usage notes

Usage:
    python extract-knowledge.py                # Extract from all checkpoints
    python extract-knowledge.py --stats        # Show extraction statistics
    python extract-knowledge.py --list         # List all extracted entries
    python extract-knowledge.py --category mistakes  # Show specific category

Cross-platform: Windows, macOS, Linux. Pure Python stdlib.
"""

import sqlite3
import re
import sys
import os
import hashlib
import json
import time
from pathlib import Path
from datetime import datetime

# Fix Windows console encoding for Unicode output
if os.name == "nt":
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

SESSION_STATE = Path.home() / ".copilot" / "session-state"
DB_PATH = SESSION_STATE / "knowledge.db"


def _stable_sha256(*parts) -> str:
    payload = "\0".join("" if p is None else str(p) for p in parts)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _knowledge_stable_id(session_id: str, category: str, title: str, topic_key: str) -> str:
    return _stable_sha256("knowledge", session_id, category, title or "", topic_key or "")


def _knowledge_relation_stable_id(source_stable_id: str, target_stable_id: str, relation_type: str) -> str:
    return _stable_sha256(
        "knowledge_relation",
        source_stable_id or "",
        target_stable_id or "",
        relation_type or "",
    )


def _utc_now() -> str:
    return datetime.utcnow().replace(microsecond=0).isoformat() + "Z"


def _default_local_replica_id() -> str:
    host = os.environ.get("HOSTNAME") or os.environ.get("COMPUTERNAME") or ""
    user = os.environ.get("USER") or os.environ.get("USERNAME") or ""
    return f"replica-{_stable_sha256('local-replica', host, user, str(Path.home()))[:16]}"


def _get_local_replica_id(db: sqlite3.Connection) -> str:
    try:
        row = db.execute("SELECT value FROM sync_state WHERE key='local_replica_id'").fetchone()
        current = str(row[0]) if row and row[0] else ""
        if current and current != "local":
            return current
        replica_id = _default_local_replica_id()
        db.execute("""
            INSERT INTO sync_state (key, value)
            VALUES ('local_replica_id', ?)
            ON CONFLICT(key) DO UPDATE SET
                value = excluded.value,
                updated_at = datetime('now')
        """, (replica_id,))
        db.execute("""
            INSERT INTO sync_metadata (key, value)
            VALUES ('local_replica_id', ?)
            ON CONFLICT(key) DO UPDATE SET
                value = excluded.value,
                updated_at = datetime('now')
        """, (replica_id,))
        return replica_id
    except Exception:
        return ""


def _enqueue_sync_op_fail_open(
    db: sqlite3.Connection,
    table_name: str,
    row_stable_id: str,
    row_payload: dict,
    op_type: str = "upsert",
):
    if not row_stable_id:
        return
    try:
        policy = db.execute(
            "SELECT sync_scope FROM sync_table_policies WHERE table_name = ?",
            (table_name,),
        ).fetchone()
        if not policy or policy[0] != "canonical":
            return
        replica_id = _get_local_replica_id(db)
        if not replica_id:
            return
        now = _utc_now()
        txn_id = _stable_sha256("sync-txn", replica_id, table_name, row_stable_id, time.time_ns())
        db.execute("""
            INSERT INTO sync_txns (txn_id, replica_id, status, created_at, committed_at)
            VALUES (?, ?, 'pending', ?, '')
        """, (txn_id, replica_id, now))
        db.execute("""
            INSERT INTO sync_ops (txn_id, table_name, op_type, row_stable_id, row_payload, op_index, created_at)
            VALUES (?, ?, ?, ?, ?, 0, ?)
        """, (txn_id, table_name, op_type, row_stable_id, json.dumps(row_payload, ensure_ascii=False), now))
    except Exception:
        return


def _seed_sync_table_policies(db: sqlite3.Connection):
    rows = [
        ("sessions", "canonical", "id"),
        ("documents", "canonical", "stable_id"),
        ("sections", "canonical", "stable_id"),
        ("knowledge_entries", "canonical", "stable_id"),
        ("knowledge_relations", "canonical", "stable_id"),
        ("entity_relations", "canonical", "stable_id"),
        ("search_feedback", "canonical", "stable_id"),
        ("recall_events", "upload_only", ""),
        ("knowledge_fts", "local_only", ""),
        ("ke_fts", "local_only", ""),
        ("sessions_fts", "local_only", ""),
        ("event_offsets", "local_only", ""),
        ("embeddings", "local_only", ""),
        ("embedding_meta", "local_only", ""),
        ("tfidf_model", "local_only", ""),
    ]
    policy_sql = db.execute(
        "SELECT sql FROM sqlite_master WHERE type='table' AND name='sync_table_policies'"
    ).fetchone()
    needs_rebuild = policy_sql and "upload_only" not in (policy_sql[0] or "")
    if needs_rebuild:
        db.executescript("""
            CREATE TABLE sync_table_policies_new (
                table_name TEXT PRIMARY KEY,
                sync_scope TEXT NOT NULL CHECK(sync_scope IN ('canonical', 'local_only', 'upload_only')),
                stable_id_column TEXT DEFAULT ''
            );
            INSERT INTO sync_table_policies_new (table_name, sync_scope, stable_id_column)
            SELECT table_name, sync_scope, COALESCE(stable_id_column, '')
            FROM sync_table_policies;
            DROP TABLE sync_table_policies;
            ALTER TABLE sync_table_policies_new RENAME TO sync_table_policies;
        """)
    db.executescript("""
        CREATE TABLE IF NOT EXISTS sync_metadata (
            key TEXT PRIMARY KEY,
            value TEXT NOT NULL,
            updated_at TEXT DEFAULT (datetime('now'))
        );
        CREATE TABLE IF NOT EXISTS sync_state (
            key TEXT PRIMARY KEY,
            value TEXT NOT NULL,
            updated_at TEXT DEFAULT (datetime('now'))
        );
        CREATE TABLE IF NOT EXISTS sync_txns (
            txn_id TEXT PRIMARY KEY,
            replica_id TEXT NOT NULL,
            status TEXT NOT NULL CHECK(status IN ('pending', 'committed', 'failed')),
            created_at TEXT NOT NULL,
            committed_at TEXT DEFAULT ''
        );
        CREATE TABLE IF NOT EXISTS sync_ops (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            txn_id TEXT NOT NULL,
            table_name TEXT NOT NULL,
            op_type TEXT NOT NULL CHECK(op_type IN ('insert', 'update', 'delete', 'upsert')),
            row_stable_id TEXT NOT NULL,
            row_payload TEXT NOT NULL,
            op_index INTEGER NOT NULL,
            created_at TEXT NOT NULL,
            UNIQUE(txn_id, op_index)
        );
        CREATE INDEX IF NOT EXISTS idx_sync_ops_txn ON sync_ops(txn_id);
        CREATE INDEX IF NOT EXISTS idx_sync_ops_table_row ON sync_ops(table_name, row_stable_id);
        CREATE TABLE IF NOT EXISTS sync_cursors (
            replica_id TEXT PRIMARY KEY,
            last_txn_id TEXT DEFAULT '',
            updated_at TEXT DEFAULT (datetime('now'))
        );
        CREATE TABLE IF NOT EXISTS sync_failures (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            txn_id TEXT DEFAULT '',
            table_name TEXT DEFAULT '',
            row_stable_id TEXT DEFAULT '',
            error_code TEXT DEFAULT '',
            error_message TEXT DEFAULT '',
            failed_at TEXT NOT NULL,
            retry_count INTEGER DEFAULT 0
        );
        CREATE INDEX IF NOT EXISTS idx_sync_failures_txn ON sync_failures(txn_id);
        CREATE TABLE IF NOT EXISTS sync_table_policies (
            table_name TEXT PRIMARY KEY,
            sync_scope TEXT NOT NULL CHECK(sync_scope IN ('canonical', 'local_only', 'upload_only')),
            stable_id_column TEXT DEFAULT ''
        );
    """)
    db.executemany("""
        INSERT INTO sync_table_policies (table_name, sync_scope, stable_id_column)
        VALUES (?, ?, ?)
        ON CONFLICT(table_name) DO UPDATE SET
            sync_scope = excluded.sync_scope,
            stable_id_column = excluded.stable_id_column
    """, rows)
    db.execute("""
        INSERT OR IGNORE INTO sync_metadata (key, value)
        VALUES ('local_replica_id', 'local')
    """)
    db.execute("""
        INSERT OR IGNORE INTO sync_state (key, value)
        VALUES ('local_replica_id', 'local')
    """)


def _backfill_stable_ids(db: sqlite3.Connection):
    for row in db.execute("""
        SELECT id, session_id, category, title, COALESCE(topic_key, ''), COALESCE(stable_id, '')
        FROM knowledge_entries
    """).fetchall():
        ke_id, session_id, category, title, topic_key, existing = row
        stable = _knowledge_stable_id(session_id, category, title, topic_key)
        if existing != stable:
            db.execute("UPDATE knowledge_entries SET stable_id = ? WHERE id = ?", (stable, ke_id))
            _enqueue_sync_op_fail_open(
                db,
                "knowledge_entries",
                stable,
                {
                    "session_id": session_id,
                    "category": category,
                    "title": title,
                    "topic_key": topic_key,
                    "stable_id": stable,
                },
            )

    for row in db.execute("""
        SELECT id, subject, predicate, object, COALESCE(stable_id, '')
        FROM entity_relations
    """).fetchall():
        er_id, subject, predicate, obj, existing = row
        stable = _stable_sha256("entity_relation", subject or "", predicate or "", obj or "")
        if existing != stable:
            db.execute("UPDATE entity_relations SET stable_id = ? WHERE id = ?", (stable, er_id))
            _enqueue_sync_op_fail_open(
                db,
                "entity_relations",
                stable,
                {
                    "subject": subject,
                    "predicate": predicate,
                    "object": obj,
                    "stable_id": stable,
                },
            )

    for row in db.execute("""
        SELECT kr.id,
               kr.source_id,
               kr.target_id,
               kr.relation_type,
               COALESCE(kr.source_stable_id, ''),
               COALESCE(kr.target_stable_id, ''),
               COALESCE(kr.stable_id, ''),
               COALESCE(src.stable_id, ''),
               COALESCE(tgt.stable_id, '')
        FROM knowledge_relations kr
        LEFT JOIN knowledge_entries src ON kr.source_id = src.id
        LEFT JOIN knowledge_entries tgt ON kr.target_id = tgt.id
    """).fetchall():
        rel_id, _, _, relation_type, src_existing, tgt_existing, existing, src_stable, tgt_stable = row
        if not src_stable or not tgt_stable:
            continue
        stable = _knowledge_relation_stable_id(src_stable, tgt_stable, relation_type)
        if src_existing != src_stable or tgt_existing != tgt_stable or existing != stable:
            db.execute("""
                UPDATE knowledge_relations
                SET source_stable_id = ?, target_stable_id = ?, stable_id = ?
                WHERE id = ?
            """, (src_stable, tgt_stable, stable, rel_id))


def _dedupe_stable_rows(db: sqlite3.Connection, table: str):
    if table not in {"knowledge_entries", "knowledge_relations", "entity_relations"}:
        return
    db.execute("""
        DELETE FROM {table}
        WHERE id IN (
            SELECT dupe.id
            FROM {table} dupe
            JOIN (
                SELECT stable_id, MIN(id) AS keep_id
                FROM {table}
                WHERE COALESCE(stable_id, '') != ''
                GROUP BY stable_id
                HAVING COUNT(*) > 1
            ) grouped ON grouped.stable_id = dupe.stable_id
            WHERE dupe.id != grouped.keep_id
        )
    """.replace("{table}", table))


def _enforce_stable_id_uniqueness(db: sqlite3.Connection):
    has_table = lambda t: db.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?",
        (t,),
    ).fetchone() is not None
    for table, index_name in [
        ("knowledge_entries", "uq_knowledge_entries_stable_id"),
        ("knowledge_relations", "uq_knowledge_relations_stable_id"),
        ("entity_relations", "uq_entity_relations_stable_id"),
    ]:
        if has_table(table):
            _dedupe_stable_rows(db, table)
            db.execute(f"CREATE UNIQUE INDEX IF NOT EXISTS {index_name} ON {table}(stable_id)")

# Extraction patterns — regex + heuristics for each category
MISTAKE_INDICATORS = [
    r"(?:mistake|error|bug|wrong|incorrect|broken|fail|crash|fix(?:ed)?)\b",
    r"(?:should\s+(?:have|not)|shouldn't|don't|avoid|never|careful)",
    r"(?:root\s+cause|caused\s+by|problem\s+was|issue\s+was)",
    r"(?:lỗi|sai|sửa|tránh|không\s+nên|nguyên\s+nhân)",
]

PATTERN_INDICATORS = [
    r"(?:always|must|should|convention|pattern|best\s+practice|rule)\b",
    r"(?:use\s+\w+\s+instead\s+of|prefer|recommend)",
    r"(?:standard|template|reusable|common\s+(?:pattern|style|approach))",
    r"(?:luôn|nên|quy\s+tắc|mẫu|chuẩn)",
]

DECISION_INDICATORS = [
    r"(?:chose|decided|selected|picked|went\s+with|opted)\b",
    r"(?:because|reason|rationale|trade-off|tradeoff)",
    r"(?:option\s+[A-C]|alternative|compared|versus|vs\.?)\b",
    r"(?:chọn|quyết\s+định|lý\s+do|so\s+sánh)",
]

TOOL_INDICATORS = [
    r"(?:install|configure|setup|version|upgrade|dependency)\b",
    r"(?:gradle|maven|docker|redis|postgres|spring\s+boot)\b",
    r"(?:JDK|SDK|IDE|VSCode|extension)\b",
    r"(?:cài|cấu\s+hình|phiên\s+bản|nâng\s+cấp)",
]

FEATURE_INDICATORS = [
    r"\b(?:implement(?:ed|ing)?|add(?:ed|ing)?|create(?:d|ing)?|build|built|develop(?:ed|ing)?)\b",
    r"(?:new\s+(?:feature|endpoint|handler|screen|component|API))\b",
    r"\b(?:feature|functionality|capability|user\s+story)\b",
    r"(?:thêm|tạo|xây\s+dựng|tính\s+năng|chức\s+năng)\b",
]

REFACTOR_INDICATORS = [
    r"\b(?:refactor|restructur|simplif|clean\s*up|extract|reorganiz)",
    r"\b(?:rename[ds]?|move[ds]?|split|merge[ds]?|consolidat|dedup)",
    r"\b(?:improv(?:e[ds]?|ing)|optimiz|reduc)",
    r"(?:tái\s+cấu\s+trúc|đơn\s+giản\s+hóa|tối\s+ưu)",
]

DISCOVERY_INDICATORS = [
    r"\b(?:discover|found|learn|realiz|notic|observ)",
    r"\b(?:turns\s+out|apparently|actually|interesting)\b",
    r"\b(?:TIL|insight|understanding|revelation)\b",
    r"(?:phát\s+hiện|nhận\s+ra|hiểu|thấy\s+rằng)",
]


def ensure_tables(db: sqlite3.Connection):
    """Create knowledge_entries table if not exists."""
    db.executescript("""
        CREATE TABLE IF NOT EXISTS knowledge_entries (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            session_id TEXT NOT NULL,
            document_id INTEGER,
            category TEXT NOT NULL,
            title TEXT NOT NULL,
            stable_id TEXT,
            content TEXT NOT NULL,
            tags TEXT DEFAULT '',
            confidence REAL DEFAULT 1.0,
            occurrence_count INTEGER DEFAULT 1,
            first_seen TEXT,
            last_seen TEXT,
            source TEXT DEFAULT 'copilot',
            topic_key TEXT,
            revision_count INTEGER DEFAULT 1,
            content_hash TEXT,
            wing TEXT DEFAULT '',
            room TEXT DEFAULT '',
            facts TEXT DEFAULT '[]',
            est_tokens INTEGER DEFAULT 0,
            task_id TEXT DEFAULT '',
            affected_files TEXT DEFAULT '[]',
            source_section TEXT DEFAULT '',
            source_file TEXT DEFAULT '',
            start_line INTEGER DEFAULT 0,
            end_line INTEGER DEFAULT 0,
            code_language TEXT DEFAULT '',
            code_snippet TEXT DEFAULT '',
            UNIQUE(category, title, session_id)
        );

        CREATE INDEX IF NOT EXISTS idx_ke_category ON knowledge_entries(category);
        CREATE INDEX IF NOT EXISTS idx_ke_session ON knowledge_entries(session_id);
        CREATE INDEX IF NOT EXISTS idx_ke_source ON knowledge_entries(source);
        CREATE INDEX IF NOT EXISTS idx_ke_topic ON knowledge_entries(topic_key);
        CREATE INDEX IF NOT EXISTS idx_ke_hash ON knowledge_entries(content_hash);
        CREATE INDEX IF NOT EXISTS idx_ke_task ON knowledge_entries(task_id);
        CREATE INDEX IF NOT EXISTS idx_ke_stable_id ON knowledge_entries(stable_id);

        CREATE TABLE IF NOT EXISTS knowledge_relations (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            source_id INTEGER REFERENCES knowledge_entries(id),
            target_id INTEGER REFERENCES knowledge_entries(id),
            source_stable_id TEXT DEFAULT '',
            target_stable_id TEXT DEFAULT '',
            relation_type TEXT NOT NULL,
            stable_id TEXT,
            confidence REAL DEFAULT 0.8,
            created_at TEXT,
            UNIQUE(source_id, target_id, relation_type)
        );

        CREATE INDEX IF NOT EXISTS idx_kr_source ON knowledge_relations(source_id);
        CREATE INDEX IF NOT EXISTS idx_kr_target ON knowledge_relations(target_id);
        CREATE INDEX IF NOT EXISTS idx_kr_source_stable ON knowledge_relations(source_stable_id);
        CREATE INDEX IF NOT EXISTS idx_kr_target_stable ON knowledge_relations(target_stable_id);
        CREATE INDEX IF NOT EXISTS idx_kr_stable_id ON knowledge_relations(stable_id);

        CREATE TABLE IF NOT EXISTS entity_relations (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            subject TEXT NOT NULL,
            predicate TEXT NOT NULL,
            object TEXT NOT NULL,
            stable_id TEXT,
            noted_at TEXT DEFAULT (datetime('now')),
            session_id TEXT DEFAULT '',
            UNIQUE(subject, predicate, object)
        );

        CREATE INDEX IF NOT EXISTS idx_er_subject ON entity_relations(subject);
        CREATE INDEX IF NOT EXISTS idx_er_object ON entity_relations(object);
        CREATE INDEX IF NOT EXISTS idx_er_stable_id ON entity_relations(stable_id);

        CREATE TABLE IF NOT EXISTS wakeup_config (
            key TEXT PRIMARY KEY,
            value TEXT NOT NULL,
            updated_at TEXT DEFAULT (datetime('now'))
        );

        CREATE TABLE IF NOT EXISTS schema_version (
            version INTEGER PRIMARY KEY,
            name TEXT NOT NULL,
            applied_at TEXT DEFAULT (datetime('now'))
        );
    """)

    # Migrate existing databases: add new columns if missing
    _ALLOWED_COLUMNS = {"stable_id", "source", "topic_key", "revision_count", "content_hash",
                        "wing", "room", "facts", "est_tokens", "task_id",
                        "affected_files", "source_section", "source_file",
                        "start_line", "end_line", "code_language", "code_snippet"}
    for col, col_def in [
        ("stable_id", "TEXT"),
        ("source", "TEXT DEFAULT 'copilot'"),
        ("topic_key", "TEXT"),
        ("revision_count", "INTEGER DEFAULT 1"),
        ("content_hash", "TEXT"),
        ("wing", "TEXT DEFAULT ''"),
        ("room", "TEXT DEFAULT ''"),
        ("facts", "TEXT DEFAULT '[]'"),
        ("est_tokens", "INTEGER DEFAULT 0"),
        ("task_id", "TEXT DEFAULT ''"),
        ("affected_files", "TEXT DEFAULT '[]'"),
        ("source_section", "TEXT DEFAULT ''"),
        ("source_file", "TEXT DEFAULT ''"),
        ("start_line", "INTEGER DEFAULT 0"),
        ("end_line", "INTEGER DEFAULT 0"),
        ("code_language", "TEXT DEFAULT ''"),
        ("code_snippet", "TEXT DEFAULT ''"),
    ]:
        assert col in _ALLOWED_COLUMNS, f"Unexpected column: {col}"
        try:
            db.execute(f"ALTER TABLE knowledge_entries ADD COLUMN {col} {col_def}")
        except sqlite3.OperationalError:
            pass  # Column already exists

    db.execute("CREATE INDEX IF NOT EXISTS idx_ke_task ON knowledge_entries(task_id)")
    db.execute("CREATE INDEX IF NOT EXISTS idx_ke_stable_id ON knowledge_entries(stable_id)")

    for col, col_def in [
        ("source_stable_id", "TEXT DEFAULT ''"),
        ("target_stable_id", "TEXT DEFAULT ''"),
        ("stable_id", "TEXT"),
    ]:
        try:
            db.execute(f"ALTER TABLE knowledge_relations ADD COLUMN {col} {col_def}")
        except sqlite3.OperationalError:
            pass
    db.execute("CREATE INDEX IF NOT EXISTS idx_kr_source_stable ON knowledge_relations(source_stable_id)")
    db.execute("CREATE INDEX IF NOT EXISTS idx_kr_target_stable ON knowledge_relations(target_stable_id)")
    db.execute("CREATE INDEX IF NOT EXISTS idx_kr_stable_id ON knowledge_relations(stable_id)")

    try:
        db.execute("ALTER TABLE entity_relations ADD COLUMN stable_id TEXT")
    except sqlite3.OperationalError:
        pass
    db.execute("CREATE INDEX IF NOT EXISTS idx_er_stable_id ON entity_relations(stable_id)")

    # Create FTS table if needed (standalone, no content= sync issues)
    db.execute("""
        CREATE VIRTUAL TABLE IF NOT EXISTS ke_fts USING fts5(
            title, content, tags, category, wing, room, facts,
            tokenize='unicode61 remove_diacritics 2'
        )
    """)
    _seed_sync_table_policies(db)
    _backfill_stable_ids(db)
    _enforce_stable_id_uniqueness(db)


def classify_paragraph(text: str) -> list[tuple[str, float]]:
    """Classify a paragraph into knowledge categories with confidence."""
    # Skip noise: interview Q&A, pure tables, pure code
    if _is_noise(text):
        return []

    text_lower = text.lower()
    results = []

    for category, indicators in [
        ("mistake", MISTAKE_INDICATORS),
        ("pattern", PATTERN_INDICATORS),
        ("decision", DECISION_INDICATORS),
        ("tool", TOOL_INDICATORS),
        ("feature", FEATURE_INDICATORS),
        ("refactor", REFACTOR_INDICATORS),
        ("discovery", DISCOVERY_INDICATORS),
    ]:
        score = 0
        for pattern in indicators:
            matches = len(re.findall(pattern, text_lower, re.IGNORECASE))
            score += matches
        if score >= 2:  # At least 2 indicator matches
            confidence = min(1.0, score / 5.0)
            results.append((category, confidence))

    return results


# Noise detection patterns
_NOISE_PATTERNS = [
    r"(?:phỏng\s*vấn|interview|câu\s*hỏi|bộ\s*câu)",
    r"(?:đáp\s*án|mong\s*đợi|tiêu\s*chí|ghi\s*điểm)",
    r"(?:bảng\s*đánh\s*giá|evaluation\s*rubric)",
    r"(?:trọng\s*số|scoring|rubric|interviewer)",
]

# Strong noise — single match is enough to discard
_STRONG_NOISE_PATTERNS = [
    r"đáp\s*án\s*(mong\s*đợi|chi\s*tiết)",
    r"bảng\s*(đánh\s*giá|ghi\s*điểm)",
    r"câu\s*hỏi\s*phỏng\s*vấn",
    r"interview\s*question",
    r"nội\s*dung\s*cần\s*đề\s*cập",
]

# User-quote patterns — checkpoint summaries quoting the user, not real mistakes
# KEEP: "User pointed out", "User noticed", "User called out", "User criticized",
#        "User demanded" — these are real bug reports / legitimate feedback
_USER_QUOTE_PATTERNS = [
    r"^(?:\d+\.\s*)?user\s+(?:said|asked|reported|requested|mentioned|noted)\b",
    r"^(?:\d+\.\s*)?user\s+(?:wants?|confirmed|approved|rejected)\b",
    r"^(?:\d+\.\s*)?user\s+(?:clarified|provided|applied|selected|chose)\b",
    r'^(?:\d+\.\s*)?user\s+said\s*[:"]',
    r'^(?:\d+\.\s*)?user\s+reported\s*[:"]',
]

# Action-summary patterns — past-tense descriptions of completed work
_ACTION_SUMMARY_PATTERNS = [
    r"^(?:\d+\.\s*)?(?:fixed|implemented|launched|created|updated|added|deployed|"
    r"committed|pushed|merged|resolved|completed|refactored|migrated|upgraded|"
    r"configured|installed|removed|deleted|replaced|renamed|moved)\s",
]


def _is_noise(text: str) -> bool:
    """Check if text is interview Q&A, scoring rubric, or other noise."""
    text_lower = text.lower()

    # Strong noise — single match enough
    for pattern in _STRONG_NOISE_PATTERNS:
        if re.search(pattern, text_lower):
            return True

    # User quotes — checkpoint summaries that quote what user said
    first_line = text_lower.strip().split("\n")[0].strip()
    for pattern in _USER_QUOTE_PATTERNS:
        if re.search(pattern, first_line):
            return True

    # Action summaries — past-tense descriptions of completed work
    # Only filter short entries (< 200 chars) that are just action log lines
    if len(text.strip()) < 200:
        for pattern in _ACTION_SUMMARY_PATTERNS:
            if re.search(pattern, first_line):
                return True

    # Weak noise — need 2+ matches
    noise_score = 0
    for pattern in _NOISE_PATTERNS:
        if re.search(pattern, text_lower):
            noise_score += 1
    if noise_score >= 2:
        return True

    # Pure markdown table (>70% of lines are table rows)
    lines = [l.strip() for l in text.split("\n") if l.strip()]
    if lines:
        table_lines = sum(1 for l in lines if l.startswith("|") and l.endswith("|"))
        if table_lines / len(lines) > 0.7 and len(lines) > 3:
            return True

    # Pure code block (>70% of content inside ```)
    code_chars = sum(len(m.group(0)) for m in re.finditer(r"```[\s\S]*?```", text))
    if len(text) > 100 and code_chars / len(text) > 0.7:
        return True

    return False


def extract_title(text: str, max_len: int = 100) -> str:
    """Extract a meaningful title from paragraph text."""
    lines = text.strip().split("\n")

    for line in lines[:5]:  # Try first 5 lines
        line = line.strip()
        if not line:
            continue
        # Skip table rows, code markers, empty headings
        if line.startswith("|") or line.startswith("```") or line.startswith("---"):
            continue
        # Skip lines that are just separators
        if re.match(r"^[-=_]{3,}$", line):
            continue

        # Remove markdown formatting
        title = re.sub(r"[#*_`\[\]]", "", line).strip()
        # Remove leading bullets/numbers/emoji
        title = re.sub(r"^[\d.)\-•]+\s*", "", title).strip()
        title = re.sub(r"^[^\w\s]{1,3}\s*", "", title).strip()  # emoji prefix

        if len(title) >= 10:
            if len(title) > max_len:
                title = title[:max_len - 3] + "..."
            return title

    # Fallback: first 100 chars of text
    fallback = re.sub(r"\s+", " ", text[:max_len]).strip()
    return fallback or "Untitled"


def extract_tags(text: str) -> str:
    """Extract relevant tags from text."""
    tag_patterns = [
        (r"\b(?:Spring\s+Boot|SpringBoot)\b", "spring-boot"),
        (r"\b(?:Thymeleaf)\b", "thymeleaf"),
        (r"\b(?:JPQL|JPA|Hibernate)\b", "jpa"),
        (r"\b(?:PostgreSQL|Postgres|PG)\b", "postgresql"),
        (r"\b(?:Docker|docker-compose)\b", "docker"),
        (r"\b(?:Redis)\b", "redis"),
        (r"\b(?:Gradle)\b", "gradle"),
        (r"\b(?:CSRF)\b", "csrf"),
        (r"\b(?:Liquibase)\b", "liquibase"),
        (r"\b(?:JavaScript|jQuery|JS)\b", "javascript"),
        (r"\b(?:CSS|styles?\.css)\b", "css"),
        (r"\b(?:i18n|internationalization|messages\.properties)\b", "i18n"),
        (r"\b(?:JDK|Java\s+\d+)\b", "java"),
        (r"\b(?:Git|git\s+hook)\b", "git"),
        (r"\b(?:VSCode|VS\s+Code)\b", "vscode"),
        (r"\b(?:Excel|xlsx)\b", "excel"),
        (r"\b(?:CRUD)\b", "crud"),
        (r"\b(?:pagination)\b", "pagination"),
        (r"\b(?:modal|dialog)\b", "ui"),
        (r"\b(?:SQL|native\s+SQL)\b", "sql"),
    ]

    tags = set()
    for pattern, tag in tag_patterns:
        if re.search(pattern, text, re.IGNORECASE):
            tags.add(tag)
    return ",".join(sorted(tags))


def split_into_knowledge_chunks(content: str) -> list[str]:
    """Split section content into meaningful chunks for classification."""
    chunks = []

    # Split by numbered items, bullet points, or double newlines
    # Prefer structured items (numbered lists, bullets)
    items = re.split(r"\n(?=\d+\.\s|\-\s|\*\s|#{1,3}\s)", content)

    for item in items:
        item = item.strip()
        if len(item) < 30:  # Skip very short fragments
            continue
        if len(item) > 2000:  # Split long chunks by paragraphs
            paragraphs = item.split("\n\n")
            for p in paragraphs:
                if len(p.strip()) >= 30:
                    chunks.append(p.strip())
        else:
            chunks.append(item)

    return chunks


def _slugify(text: str) -> str:
    """Convert text to URL-friendly slug for topic keys."""
    text = text.lower().strip()
    text = re.sub(r"[^\w\s-]", "", text)
    text = re.sub(r"[\s_]+", "-", text)
    text = re.sub(r"-+", "-", text).strip("-")
    return text[:60]


def _compute_content_hash(category: str, title: str, content: str) -> str:
    """Compute dedup hash from normalized category + title + key content."""
    normalized = (
        category.lower().strip() + "|" +
        re.sub(r"\s+", " ", title.lower().strip()) + "|" +
        re.sub(r"\s+", " ", content[:200].lower().strip())
    )
    return hashlib.sha256(normalized.encode("utf-8")).hexdigest()[:16]


def _generate_topic_key(category: str, title: str) -> str:
    """Generate a topic key like 'decision/auth-jwt-approach'."""
    return f"{category}/{_slugify(title)}"


def extract_from_sections(db: sqlite3.Connection, session_ids: list = None):
    """Extract knowledge entries from indexed sections.
    
    Args:
        session_ids: If provided, only extract from these sessions (selective mode).
    """
    now = datetime.now().isoformat()
    extracted = 0
    skipped = 0
    deduped = 0

    # Focus on the most knowledge-rich sections
    target_sections = ["technical_details", "history", "work_done", "next_steps", "full", "conversation"]

    section_placeholders = ",".join("?" for _ in target_sections)
    base_query = f"""
        SELECT s.id, s.document_id, s.section_name, s.content, d.session_id,
               COALESCE(d.source, 'copilot') as source,
               COALESCE(d.stable_id, '') as document_stable_id
        FROM sections s
        JOIN documents d ON s.document_id = d.id
        WHERE s.section_name IN ({section_placeholders})
    """
    query_params = list(target_sections)

    if session_ids:
        session_placeholders = ",".join("?" for _ in session_ids)
        base_query += f" AND d.session_id IN ({session_placeholders})"
        query_params.extend(session_ids)

    base_query += " ORDER BY d.session_id, d.seq"
    rows = db.execute(base_query, query_params).fetchall()

    # Pre-load existing content hashes for fast dedup
    existing_hashes = set()
    try:
        for row in db.execute("SELECT content_hash FROM knowledge_entries WHERE content_hash IS NOT NULL"):
            existing_hashes.add(row[0])
    except sqlite3.OperationalError:
        pass

    for section_id, doc_id, section_name, content, session_id, source, document_stable_id in rows:
        chunks = split_into_knowledge_chunks(content)

        for chunk in chunks:
            classifications = classify_paragraph(chunk)

            for category, confidence in classifications:
                title = extract_title(chunk)
                tags = extract_tags(chunk)
                content_hash = _compute_content_hash(category, title, chunk)
                topic_key = _generate_topic_key(category, title)

                # Hash-based dedup: skip if exact content already exists
                if content_hash in existing_hashes:
                    deduped += 1
                    continue

                # Topic key upsert: if same topic exists, update instead of insert
                existing = db.execute(
                    "SELECT id, revision_count FROM knowledge_entries WHERE topic_key = ? AND session_id != ?",
                    (topic_key, session_id)
                ).fetchone()

                if existing:
                    stable_id = _knowledge_stable_id(session_id, category, title, topic_key)
                    # Upsert: update existing entry with newer content
                    db.execute("""
                        UPDATE knowledge_entries
                        SET content = ?, confidence = MAX(confidence, ?),
                            revision_count = revision_count + 1,
                            last_seen = ?, content_hash = ?, tags = ?,
                            source_section = ?, stable_id = CASE
                                WHEN COALESCE(stable_id, '') = '' THEN ?
                                ELSE stable_id
                            END
                        WHERE id = ?
                    """, (chunk[:3000], confidence, now, content_hash, tags, section_name, stable_id, existing[0]))
                    _enqueue_sync_op_fail_open(
                        db,
                        "knowledge_entries",
                        stable_id,
                        {
                            "session_id": session_id,
                            "document_stable_id": document_stable_id,
                            "category": category,
                            "title": title,
                            "stable_id": stable_id,
                            "content": chunk[:3000],
                            "tags": tags,
                            "confidence": confidence,
                            "last_seen": now,
                            "content_hash": content_hash,
                            "source": source,
                            "topic_key": topic_key,
                            "source_section": section_name,
                        },
                    )
                    existing_hashes.add(content_hash)
                    extracted += 1
                    continue

                try:
                    est_tokens = len(f"{title} {chunk[:3000]}") // 4
                    stable_id = _knowledge_stable_id(session_id, category, title, topic_key)
                    db.execute("""
                        INSERT INTO knowledge_entries
                        (session_id, document_id, category, title, stable_id, content, tags,
                         confidence, first_seen, last_seen, source, topic_key,
                         revision_count, content_hash, est_tokens, source_section)
                        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 1, ?, ?, ?)
                        ON CONFLICT(category, title, session_id) DO UPDATE SET
                            confidence = MAX(knowledge_entries.confidence, excluded.confidence),
                            occurrence_count = knowledge_entries.occurrence_count + 1,
                            last_seen = excluded.last_seen,
                            content_hash = excluded.content_hash,
                            topic_key = excluded.topic_key,
                            est_tokens = excluded.est_tokens,
                            source_section = excluded.source_section,
                            stable_id = excluded.stable_id
                    """, (session_id, doc_id, category, title, stable_id, chunk[:3000], tags,
                          confidence, now, now, source, topic_key, content_hash,
                          est_tokens, section_name))
                    _enqueue_sync_op_fail_open(
                        db,
                        "knowledge_entries",
                        stable_id,
                        {
                            "session_id": session_id,
                            "document_stable_id": document_stable_id,
                            "category": category,
                            "title": title,
                            "stable_id": stable_id,
                            "content": chunk[:3000],
                            "tags": tags,
                            "confidence": confidence,
                            "first_seen": now,
                            "last_seen": now,
                            "source": source,
                            "topic_key": topic_key,
                            "revision_count": 1,
                            "content_hash": content_hash,
                            "est_tokens": est_tokens,
                            "source_section": section_name,
                        },
                    )
                    existing_hashes.add(content_hash)
                    extracted += 1
                except sqlite3.IntegrityError as e:
                    print(f"⚠ Duplicate entry skipped: {e}", file=sys.stderr)
                    skipped += 1

    # Confidence decay: entries not seen recently get slightly lower confidence
    # Apply at most once per day to prevent compounding from repeated runs
    try:
        today = now[:10]
        last_decay = None
        try:
            row = db.execute(
                "SELECT value FROM embedding_meta WHERE key = 'last_decay_date'"
            ).fetchone()
            if row:
                last_decay = row[0]
        except sqlite3.OperationalError:
            pass  # embedding_meta table may not exist

        if last_decay != today:
            db.execute("""
                UPDATE knowledge_entries
                SET confidence = MAX(0.3, confidence * 0.95)
                WHERE last_seen < ? AND confidence > 0.3
            """, (today,))
            try:
                db.execute("""
                    CREATE TABLE IF NOT EXISTS embedding_meta (
                        key TEXT PRIMARY KEY, value TEXT
                    )
                """)
                db.execute("""
                    INSERT OR REPLACE INTO embedding_meta (key, value)
                    VALUES ('last_decay_date', ?)
                """, (today,))
            except sqlite3.OperationalError:
                pass
    except sqlite3.OperationalError:
        pass

    # Rebuild FTS
    db.execute("DELETE FROM ke_fts")
    db.execute("""
        INSERT INTO ke_fts (rowid, title, content, tags, category, wing, room, facts)
        SELECT id, title, content, tags, category,
               COALESCE(wing,''), COALESCE(room,''), COALESCE(facts,'[]')
        FROM knowledge_entries
    """)

    # Extract relations between knowledge entries
    relations_count = extract_relations(db)

    db.commit()
    return extracted, skipped, deduped, relations_count


def extract_relations(db: sqlite3.Connection) -> int:
    """Detect and insert relationships between knowledge entries.

    Relation types:
      SAME_SESSION  — entries from same session but different categories (0.7)
      SAME_TOPIC    — entries with same topic_key from different sessions (0.9)
      TAG_OVERLAP   — entries sharing 2+ tags (0.5 + 0.1 * shared_count)
      RESOLVED_BY   — mistake paired with pattern/tool in same session (0.8)
    """
    now = datetime.now().isoformat()
    MAX_PER_TYPE = 1500  # budget per relation type
    MAX_RELATIONS = 5000

    try:
        db.execute("DELETE FROM knowledge_relations")
    except sqlite3.OperationalError:
        pass

    # Ensure unique constraint index exists
    db.execute("""
        CREATE UNIQUE INDEX IF NOT EXISTS idx_relations_unique
        ON knowledge_relations(source_id, target_id, relation_type)
    """)

    # Load all entries needed for relation detection
    entries = db.execute("""
        SELECT id, session_id, category, title, tags, topic_key, COALESCE(stable_id, '')
        FROM knowledge_entries
    """).fetchall()

    if not entries:
        return 0

    relations: list[tuple] = []  # (source_id, target_id, src_stable, tgt_stable, relation_type, stable_id, confidence, created_at)

    # Build indexes for efficient lookups
    by_session: dict[str, list[tuple]] = {}
    by_topic: dict[str, list[tuple]] = {}
    by_id: dict[int, tuple] = {}
    for e in entries:
        eid, sid, cat, title, tags, topic, stable_id = e
        by_session.setdefault(sid, []).append(e)
        if topic:
            by_topic.setdefault(topic, []).append(e)
        by_id[eid] = e

    seen = set()  # (source_id, target_id, relation_type)
    type_counts = {}  # track count per relation type

    def _add(src: int, tgt: int, rtype: str, conf: float) -> bool:
        if src == tgt:
            return False
        key = (src, tgt, rtype)
        if key not in seen:
            cnt = type_counts.get(rtype, 0)
            if cnt >= MAX_PER_TYPE or len(relations) >= MAX_RELATIONS:
                return True  # signal budget exhausted
            seen.add(key)
            src = by_id.get(key[0])
            tgt = by_id.get(key[1])
            if not src or not tgt:
                return False
            src_sid = src[6] or _knowledge_stable_id(src[1], src[2], src[3], src[5] or "")
            tgt_sid = tgt[6] or _knowledge_stable_id(tgt[1], tgt[2], tgt[3], tgt[5] or "")
            stable_id = _knowledge_relation_stable_id(src_sid, tgt_sid, rtype)
            relations.append((key[0], key[1], src_sid, tgt_sid, rtype, stable_id, round(conf, 2), now))
            type_counts[rtype] = cnt + 1
        return False

    # 1. SAME_SESSION — different categories within same session
    for sid, group in by_session.items():
        if len(group) < 2:
            continue
        done = False
        for i, a in enumerate(group):
            for b in group[i + 1:]:
                if a[2] != b[2]:  # different category
                    if _add(a[0], b[0], "SAME_SESSION", 0.7):
                        done = True
                        break
            if done:
                break
        if done:
            break

    # 2. SAME_TOPIC — same topic_key, different sessions
    for topic, group in by_topic.items():
        if len(group) < 2:
            continue
        done = False
        for i, a in enumerate(group):
            for b in group[i + 1:]:
                if a[1] != b[1]:  # different session_id
                    if _add(a[0], b[0], "SAME_TOPIC", 0.9):
                        done = True
                        break
            if done:
                break
        if done:
            break

    # 3. TAG_OVERLAP — entries sharing 2+ tags
    entry_tags = []
    for e in entries:
        tags_str = e[4] or ""
        tag_set = frozenset(t.strip() for t in tags_str.split(",") if t.strip())
        if len(tag_set) >= 2:
            entry_tags.append((e[0], tag_set))

    for i, (aid, atags) in enumerate(entry_tags):
        done = False
        for bid, btags in entry_tags[i + 1:]:
            shared = len(atags & btags)
            if shared >= 2:
                conf = min(1.0, 0.5 + 0.1 * min(shared, 5))
                if _add(aid, bid, "TAG_OVERLAP", conf):
                    done = True
                    break
        if done:
            break

    # 4. RESOLVED_BY — mistake + pattern/tool in same session
    for sid, group in by_session.items():
        mistakes = [e for e in group if e[2] == "mistake"]
        resolvers = [e for e in group if e[2] in ("pattern", "tool")]
        done = False
        for m in mistakes:
            for r in resolvers:
                if _add(m[0], r[0], "RESOLVED_BY", 0.8):
                    done = True
                    break
            if done:
                break
        if done:
            break

    # Batch insert all relations
    if relations:
        db.executemany("""
            INSERT OR IGNORE INTO knowledge_relations
            (source_id, target_id, source_stable_id, target_stable_id, relation_type, stable_id, confidence, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """, relations)
        for rel in relations:
            _enqueue_sync_op_fail_open(
                db,
                "knowledge_relations",
                rel[5],
                {
                    "source_stable_id": rel[2],
                    "target_stable_id": rel[3],
                    "relation_type": rel[4],
                    "stable_id": rel[5],
                    "confidence": rel[6],
                    "created_at": rel[7],
                },
            )

    return len(relations)


def show_stats(db: sqlite3.Connection):
    """Show extraction statistics."""
    print(f"\n{'='*50}")
    print(f"  Knowledge Extraction Statistics")
    print(f"{'='*50}")

    total = db.execute("SELECT COUNT(*) FROM knowledge_entries").fetchone()[0]
    print(f"  Total entries: {total}")

    print("\n  By category:")
    for row in db.execute("""
        SELECT category, COUNT(*), ROUND(AVG(confidence), 2)
        FROM knowledge_entries GROUP BY category ORDER BY COUNT(*) DESC
    """):
        print(f"    {row[0]:12s}: {row[1]:3d} entries (avg confidence: {row[2]})")

    print("\n  Top tags:")
    # Manually count tags since they're comma-separated
    tag_counts = {}
    for row in db.execute("SELECT tags FROM knowledge_entries WHERE tags != ''"):
        for tag in row[0].split(","):
            tag = tag.strip()
            if tag:
                tag_counts[tag] = tag_counts.get(tag, 0) + 1
    for tag, count in sorted(tag_counts.items(), key=lambda x: -x[1])[:15]:
        print(f"    {tag:20s}: {count}")

    print(f"\n  Cross-session patterns (appearing in 2+ sessions):")
    for row in db.execute("""
        SELECT title, category, COUNT(DISTINCT session_id) as sessions
        FROM knowledge_entries
        GROUP BY title, category
        HAVING sessions >= 2
        ORDER BY sessions DESC
        LIMIT 10
    """):
        print(f"    [{row[1]}] {row[0][:60]} ({row[2]} sessions)")


def list_entries(db: sqlite3.Connection, category: str = None, limit: int = 20):
    """List knowledge entries."""
    sql = "SELECT id, category, title, tags, confidence, session_id FROM knowledge_entries"
    params = []
    if category:
        sql += " WHERE category = ?"
        params.append(category)
    sql += " ORDER BY confidence DESC, category LIMIT ?"
    params.append(limit)

    print(f"\n{'ID':>4s} {'Category':12s} {'Conf':>5s} {'Session':10s} Title")
    print(f"{'─'*4} {'─'*12} {'─'*5} {'─'*10} {'─'*50}")

    for row in db.execute(sql, params):
        sid = row[5][:8] + ".."
        print(f"{row[0]:4d} {row[1]:12s} {row[4]:5.2f} {sid:10s} {row[2][:50]}")


def main():
    args = sys.argv[1:]

    if "--help" in args or "-h" in args:
        print(__doc__)
        return

    if not DB_PATH.exists():
        print(f"Error: Knowledge database not found at {DB_PATH}")
        print("Run 'python build-session-index.py' first.")
        sys.exit(1)

    db = sqlite3.connect(str(DB_PATH))
    ensure_tables(db)

    if "--stats" in args:
        show_stats(db)
        db.close()
        return

    if "--list" in args:
        category = None
        if "--category" in args:
            idx = args.index("--category")
            category = args[idx + 1] if idx + 1 < len(args) else None
        limit = 20
        if "--limit" in args:
            idx = args.index("--limit")
            limit = int(args[idx + 1]) if idx + 1 < len(args) else 20
        list_entries(db, category, limit)
        db.close()
        return

    if "--relations" in args:
        try:
            total = db.execute("SELECT COUNT(*) FROM knowledge_relations").fetchone()[0]
            print(f"\n{'='*50}")
            print(f"  Knowledge Relations Statistics")
            print(f"{'='*50}")
            print(f"  Total relations: {total}")
            print("\n  By type:")
            for row in db.execute("""
                SELECT relation_type, COUNT(*), ROUND(AVG(confidence), 2)
                FROM knowledge_relations GROUP BY relation_type ORDER BY COUNT(*) DESC
            """):
                print(f"    {row[0]:15s}: {row[1]:4d} relations (avg confidence: {row[2]})")
        except sqlite3.OperationalError:
            print("  No relations found. Run extraction first.")
        db.close()
        return

    # Default: run extraction
    session_ids = None
    if "--sessions" in args:
        idx = args.index("--sessions")
        session_ids = [s.strip() for s in args[idx + 1].split(",") if s.strip()] if idx + 1 < len(args) else None
        if session_ids:
            print(f"Selective extraction for {len(session_ids)} session(s)...")
    else:
        print("Extracting knowledge from indexed sessions...")
    extracted, skipped, deduped, relations_count = extract_from_sections(db, session_ids=session_ids)
    print(f"Extracted {extracted} entries ({skipped} duplicates skipped, {deduped} deduped by hash)")
    print(f"Extracted {relations_count} relations")

    # Clean up stale embeddings and orphan relations
    try:
        stale_embeds = db.execute("""
            DELETE FROM embeddings WHERE
            (source_type = 'knowledge' AND source_id NOT IN (SELECT id FROM knowledge_entries))
            OR (source_type = 'section' AND source_id NOT IN (SELECT id FROM sections))
        """).rowcount
        orphan_rels = db.execute("""
            DELETE FROM knowledge_relations WHERE
            source_id NOT IN (SELECT id FROM knowledge_entries)
            OR target_id NOT IN (SELECT id FROM knowledge_entries)
        """).rowcount
        if stale_embeds or orphan_rels:
            db.commit()
            print(f"Cleaned up {stale_embeds} stale embeddings, {orphan_rels} orphan relations")
    except Exception:
        pass

    show_stats(db)
    db.close()


if __name__ == "__main__":
    main()
