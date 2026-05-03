#!/usr/bin/env python3
"""
learn.py — Record new knowledge from AI agent sessions

Allows AI agents (Copilot, Claude, Cursor) to write learnings back to the
shared knowledge base during or after work.

Usage:
    python learn.py --mistake "Title" "Description of what went wrong and fix"
    python learn.py --pattern "Title" "Description of what works well"
    python learn.py --decision "Title" "Architecture decision and rationale"
    python learn.py --tool "Title" "Tool/config that was useful"
    python learn.py --feature "Title" "New feature implementation details"
    python learn.py --refactor "Title" "Code improvement description"
    python learn.py --discovery "Title" "Codebase finding or insight"

    python learn.py --mistake "Title" "Description" --tags "docker,compose"
    python learn.py --mistake "Title" "Description" --session abc123
    python learn.py --mistake "Title" "Description" --confidence 0.8
    python learn.py --mistake "Title" "Description" --wing backend --room dynamodb
    python learn.py --pattern "Title" "Description" --fact "batch limit is 25" --fact "GSI eventual"
    python learn.py --mistake "Title" "Description" --task "memory-surface" --file "briefing.py" --file "learn.py"
    python learn.py --pattern "Title" "Description" --code-location "path/to/file.py:50-75"

    python learn.py --relate "copyToGroup" "reads_from" "patient-dynamic-form.json"
    python learn.py --relate "addPatient Lambda" "writes_to" "dataTable"

    python learn.py --from-file notes.md          # Bulk import from markdown
    python learn.py --list                        # List recent entries
    python learn.py --stats                       # Show knowledge stats
"""

import hashlib
import json
import os
import sqlite3
import sys
import time
from pathlib import Path

if os.name == "nt":
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

TOOLS_DIR = Path(__file__).parent
SESSION_STATE = Path.home() / ".copilot" / "session-state"
DB_PATH = SESSION_STATE / "knowledge.db"

# Wing auto-detection rules: tag patterns → wing
_WING_RULES = [
    (
        {
            "lambda",
            "dynamodb",
            "sqs",
            "cdk",
            "api",
            "cognito",
            "s3",
            "eventbridge",
            "cloudwatch",
            "sns",
            "websocket",
            "nefoap",
        },
        "backend",
    ),
    ({"expo", "react", "react-native", "screen", "component", "css", "ui", "navigation", "hook"}, "frontend"),
    ({"jest", "playwright", "e2e", "test", "testing", "coverage"}, "testing"),
    ({"vpc", "cloudwatch", "cdk", "cloudformation", "infrastructure", "deploy", "pipeline"}, "infrastructure"),
    ({"git", "ci", "cd", "docker", "devops", "proxy", "tls", "npm", "yarn", "package-manager"}, "devops"),
    ({"typescript", "javascript", "eslint", "prettier", "i18n", "mermaid", "openapi"}, "shared"),
]

# Room auto-detection rules: tag/title patterns → room
_ROOM_RULES = [
    ({"patient", "patient-search", "傷病者"}, "patient"),
    ({"hospital", "病院"}, "hospital"),
    ({"copytogroup", "copy-to-group", "傷病者追加"}, "copyToGroup"),
    ({"websocket", "ws"}, "websocket"),
    ({"dynamodb", "dao", "repository"}, "dynamodb"),
    ({"auth", "cognito", "login"}, "auth"),
    ({"s3", "media", "upload", "presigned"}, "s3-media"),
    ({"sqs", "queue", "consumer"}, "sqs"),
    ({"notification", "通知"}, "notification"),
    ({"audit", "audit-log"}, "audit-log"),
    ({"nefoap", "指令"}, "nefoap"),
    ({"lambda", "handler"}, "lambda"),
    ({"playwright", "e2e"}, "e2e"),
    ({"excel", "spreadsheet", "tsv", "csv"}, "data-export"),
    ({"cdk", "cloudformation", "stack"}, "cdk"),
]


def _detect_wing(tags: str, title: str, content: str) -> str:
    """Auto-detect wing from tags/title/content."""
    tag_set = {t.strip().lower() for t in tags.split(",") if t.strip()}
    text_lower = f"{title} {content[:200]}".lower()
    for patterns, wing in _WING_RULES:
        if tag_set & patterns:
            return wing
        if any(p in text_lower for p in patterns):
            return wing
    return ""


def _detect_room(tags: str, title: str, content: str) -> str:
    """Auto-detect room from tags/title/content."""
    tag_set = {t.strip().lower() for t in tags.split(",") if t.strip()}
    text_lower = f"{title} {content[:300]}".lower()
    for patterns, room in _ROOM_RULES:
        if tag_set & patterns:
            return room
        if any(p in text_lower for p in patterns):
            return room
    return ""


# Injection scanning patterns (inspired by Hermes Agent memory security)
# Block prompt injection, role hijacking, credential exfiltration, invisible Unicode
import re

_INJECTION_PATTERNS = [
    (
        re.compile(r"(?i)\bignore\s+(all\s+)?previous\s+instructions?\b"),
        "prompt injection: 'ignore previous instructions'",
    ),
    (re.compile(r"(?i)\byou\s+are\s+now\b"), "role hijacking: 'you are now'"),
    (re.compile(r"(?i)\bsystem\s*:\s*"), "role injection: 'system:' prefix"),
    (re.compile(r"(?i)\b(assistant|user|human)\s*:\s*"), "role injection: fake role prefix"),
    (re.compile(r"(?i)\bforget\s+(everything|all|your)\b"), "memory manipulation: 'forget everything'"),
    (re.compile(r"(?i)\bdo\s+not\s+follow\b"), "instruction override: 'do not follow'"),
    (
        re.compile(r"(?i)\b(api[_-]?key|secret[_-]?key|password|token)\s*[:=]\s*\S+"),
        "credential leak: API key/password/token",
    ),
    (re.compile(r"(?i)ssh-rsa\s+AAAA"), "credential leak: SSH public key"),
    (re.compile(r"(?i)-----BEGIN\s+(RSA\s+)?PRIVATE\s+KEY-----"), "credential leak: private key"),
    (re.compile(r"(?i)\beval\s*\("), "code injection: eval()"),
    (re.compile(r"(?i)\bexec\s*\("), "code injection: exec()"),
    (re.compile(r"[\u200b\u200c\u200d\u2060\ufeff]"), "invisible Unicode characters (zero-width)"),
    (re.compile(r"(?i)\bACT\s+AS\b"), "role hijacking: 'act as'"),
    (re.compile(r"(?i)\bpretend\s+(you\s+are|to\s+be)\b"), "role hijacking: 'pretend to be'"),
    (re.compile(r"(?i)\b(curl|wget|nc|ncat)\s+.*\|\s*(ba)?sh\b"), "remote code execution pattern"),
]

_CODE_LANGUAGE_MAP = {
    ".py": "python",
    ".js": "javascript",
    ".jsx": "jsx",
    ".ts": "typescript",
    ".tsx": "tsx",
    ".go": "go",
    ".rs": "rust",
    ".java": "java",
    ".kt": "kotlin",
    ".swift": "swift",
    ".sh": "bash",
    ".md": "markdown",
}


def _stable_sha256(*parts) -> str:
    payload = "\0".join("" if p is None else str(p) for p in parts)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _knowledge_stable_id(session_id: str, category: str, title: str, topic_key: str = "") -> str:
    return _stable_sha256("knowledge", session_id or "", category or "", title or "", topic_key or "")


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
        db.execute(
            """
            INSERT INTO sync_state (key, value)
            VALUES ('local_replica_id', ?)
            ON CONFLICT(key) DO UPDATE SET
                value = excluded.value,
                updated_at = datetime('now')
        """,
            (replica_id,),
        )
        try:
            db.execute(
                """
                INSERT INTO sync_metadata (key, value)
                VALUES ('local_replica_id', ?)
                ON CONFLICT(key) DO UPDATE SET
                    value = excluded.value,
                    updated_at = datetime('now')
            """,
                (replica_id,),
            )
        except sqlite3.OperationalError:
            pass
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
        now = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
        txn_id = _stable_sha256("sync-txn", replica_id, table_name, row_stable_id, time.time_ns())
        db.execute(
            """
            INSERT INTO sync_txns (txn_id, replica_id, status, created_at, committed_at)
            VALUES (?, ?, 'pending', ?, '')
        """,
            (txn_id, replica_id, now),
        )
        db.execute(
            """
            INSERT INTO sync_ops (txn_id, table_name, op_type, row_stable_id, row_payload, op_index, created_at)
            VALUES (?, ?, ?, ?, ?, 0, ?)
        """,
            (txn_id, table_name, op_type, row_stable_id, json.dumps(row_payload, ensure_ascii=False), now),
        )
    except Exception:
        return


def scan_content_for_injection(title: str, content: str) -> list:
    """Scan title + content for injection patterns. Returns list of warnings."""
    warnings = []
    text = f"{title}\n{content}"
    for pattern, description in _INJECTION_PATTERNS:
        if pattern.search(text):
            warnings.append(description)
    return warnings


def _parse_code_location(value: str) -> tuple[str, int, int]:
    """Parse <path>:<line> or <path>:<start>-<end> from rightmost numeric suffix."""
    m = re.match(r"^(?P<path>.+):(?P<start>\d+)(?:-(?P<end>\d+))?$", value or "")
    if not m:
        raise ValueError(f"Invalid --code-location: {value!r}. Expected path:line or path:start-end")
    source_file = m.group("path")
    start_line = int(m.group("start"))
    end_line = int(m.group("end") or m.group("start"))
    if start_line <= 0 or end_line <= 0 or end_line < start_line:
        raise ValueError(f"Invalid --code-location line range: {value!r}")
    return source_file, start_line, end_line


def _detect_code_language(source_file: str) -> str:
    return _CODE_LANGUAGE_MAP.get(Path(source_file).suffix.lower(), "")


def _extract_code_snippet(source_file: str, start_line: int, end_line: int, quiet: bool = False) -> tuple[str, str]:
    """Best-effort snippet extraction; never raises for unreadable files."""
    code_language = _detect_code_language(source_file)
    path = Path(source_file)
    try:
        if not path.exists() or not path.is_file():
            raise OSError("missing or not a regular file")
        text = path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError) as e:
        print(f"  [warn] Could not read code location '{source_file}': {e}", file=sys.stderr if quiet else sys.stdout)
        return "", code_language

    normalized = text.replace("\r\n", "\n").replace("\r", "\n")
    lines = normalized.split("\n")
    if start_line > len(lines):
        return "", code_language
    snippet = "\n".join(lines[start_line - 1 : end_line])
    if len(snippet) > 2000:
        snippet = snippet[:1999] + "…"
    return snippet, code_language


def get_db() -> sqlite3.Connection:
    if not DB_PATH.exists():
        print("Error: Knowledge DB not found. Run build-session-index.py first.", file=sys.stderr)
        sys.exit(1)
    db = sqlite3.connect(str(DB_PATH))
    db.row_factory = sqlite3.Row
    # WAL mode for concurrent reads; busy_timeout lets writers retry up to 5 s
    # before failing with SQLITE_BUSY when the indexer or sync is also writing.
    db.execute("PRAGMA journal_mode=WAL")
    db.execute("PRAGMA busy_timeout=5000")
    return db


def detect_session_id() -> str:
    """Try to detect current session ID from environment or recent sessions."""
    # Check if there's a session env var
    sid = os.environ.get("COPILOT_SESSION_ID", "")
    if sid:
        return sid

    # Find most recently modified session
    if SESSION_STATE.exists():
        sessions = []
        for d in SESSION_STATE.iterdir():
            if d.is_dir() and len(d.name) > 8 and "-" in d.name:
                try:
                    mtime = max(f.stat().st_mtime for f in d.rglob("*") if f.is_file())
                    sessions.append((mtime, d.name))
                except (ValueError, OSError) as e:
                    print(f"⚠ Error reading session dir: {e}", file=sys.stderr)
        if sessions:
            sessions.sort(reverse=True)
            return sessions[0][1]

    return "manual"


def add_entry(
    category: str,
    title: str,
    content: str,
    tags: str = "",
    session_id: str = None,
    confidence: float = None,
    wing: str = "",
    room: str = "",
    facts: list = None,
    skip_gate: bool = False,
    skip_scan: bool = False,
    task_id: str = "",
    affected_files: list = None,
    source_file: str = "",
    start_line: int = 0,
    end_line: int = 0,
    code_language: str = "",
    code_snippet: str = "",
    code_location_set: bool = False,
    quiet: bool = False,
) -> int:
    """Add a knowledge entry to the database. Returns entry ID.

    Quality gate (for mistake/pattern/discovery): 3 questions must all be YES:
    1. "Could someone Google this in 5 minutes?" → NO (otherwise not worth recording)
    2. "Is this specific to THIS codebase?" → YES (generic knowledge doesn't belong)
    3. "Did this require real debugging/investigation?" → YES (trivial findings = noise)

    Gate is auto-skipped for decision/tool/feature/refactor (always worth recording)
    and for bulk imports (--from-file). Use --skip-gate to bypass manually.

    Injection scanning: All entries are scanned for prompt injection, role hijacking,
    credential leaks, and invisible Unicode. Matching entries are REJECTED unless
    --skip-scan is passed (for documenting injection patterns themselves).
    """
    db = get_db()
    ke_columns = {row[1] for row in db.execute("PRAGMA table_info(knowledge_entries)").fetchall()}
    has_code_location_columns = all(
        c in ke_columns for c in ("source_file", "start_line", "end_line", "code_language", "code_snippet")
    )
    has_stable_id_column = "stable_id" in ke_columns
    has_topic_key_column = "topic_key" in ke_columns
    if code_location_set and not has_code_location_columns:
        print(
            "  [warn] DB schema missing code-location columns; run migrate.py to persist snippets",
            file=sys.stderr if quiet else sys.stdout,
        )
        code_location_set = False

    # Injection scanning (before any DB writes) — always runs unless --skip-scan
    if not skip_scan:
        injection_warnings = scan_content_for_injection(title, content)
        if injection_warnings:
            print("  ⚠ REJECTED — injection pattern detected:", file=sys.stderr)
            for w in injection_warnings:
                print(f"    ✗ {w}", file=sys.stderr)
            print("  Use --skip-scan to bypass (only for documenting injection patterns)", file=sys.stderr)
            return -1

    if not session_id:
        session_id = detect_session_id()

    if confidence is None:
        confidence = {
            "mistake": 0.7,
            "pattern": 0.7,
            "decision": 0.8,
            "tool": 0.5,
            "feature": 0.7,
            "refactor": 0.6,
            "discovery": 0.6,
        }.get(category, 0.5)

    # Auto-detect wing/room if not provided
    if not wing:
        wing = _detect_wing(tags, title, content)
    if not room:
        room = _detect_room(tags, title, content)

    # Serialize facts and affected_files to JSON
    facts_json = json.dumps(facts or [], ensure_ascii=False)
    # Enforce path length limit for each file
    files_list = [(f[:256] if f else "") for f in (affected_files or [])]
    files_json = json.dumps(files_list, ensure_ascii=False)

    # Enforce task_id length limit
    task_id = (task_id or "")[:200]

    now = time.strftime("%Y-%m-%dT%H:%M:%S")

    # Check for existing entry with same title in same category
    existing_sql = """
        SELECT id, occurrence_count, content, session_id
    """
    if has_topic_key_column:
        existing_sql += ", COALESCE(topic_key, '') AS topic_key"
    else:
        existing_sql += ", '' AS topic_key"
    existing_sql += """
        FROM knowledge_entries
        WHERE category = ? AND title = ?
        ORDER BY confidence DESC LIMIT 1
    """
    existing = db.execute(existing_sql, (category, title)).fetchone()

    if existing:
        # Update existing: bump occurrence count, update content if longer
        new_count = existing["occurrence_count"] + 1
        new_content = content if len(content) > len(existing["content"]) else existing["content"]
        new_confidence = min(1.0, confidence + 0.05 * (new_count - 1))

        # Estimate token cost from the content that will actually be stored
        est_tokens = len(f"{title} {new_content}") // 4

        update_sql = """
            UPDATE knowledge_entries
            SET content = ?, occurrence_count = ?, confidence = ?,
                last_seen = ?, tags = CASE WHEN ? != '' THEN ? ELSE tags END,
                wing = CASE WHEN ? != '' THEN ? ELSE wing END,
                room = CASE WHEN ? != '' THEN ? ELSE room END,
                facts = CASE WHEN ? != '[]' THEN ? ELSE facts END,
                task_id = CASE WHEN ? != '' THEN ? ELSE task_id END,
                affected_files = CASE WHEN ? != '[]' THEN ? ELSE affected_files END,
        """
        update_params = [
            new_content,
            new_count,
            new_confidence,
            now,
            tags,
            tags,
            wing,
            wing,
            room,
            room,
            facts_json,
            facts_json,
            task_id,
            task_id,
            files_json,
            files_json,
        ]
        if has_stable_id_column:
            stable_id = _knowledge_stable_id(
                existing["session_id"], category, title, existing["topic_key"] if has_topic_key_column else ""
            )
            update_sql += " stable_id = ?,"
            update_params.append(stable_id)
        if has_code_location_columns:
            update_sql += """
                source_file = CASE WHEN ? THEN ? ELSE source_file END,
                start_line = CASE WHEN ? THEN ? ELSE start_line END,
                end_line = CASE WHEN ? THEN ? ELSE end_line END,
                code_language = CASE WHEN ? THEN ? ELSE code_language END,
                code_snippet = CASE WHEN ? THEN ? ELSE code_snippet END,
            """
            update_params.extend(
                [
                    int(code_location_set),
                    source_file,
                    int(code_location_set),
                    start_line,
                    int(code_location_set),
                    end_line,
                    int(code_location_set),
                    code_language,
                    int(code_location_set),
                    code_snippet,
                ]
            )
        update_sql += " est_tokens = ? WHERE id = ?"
        update_params.extend([est_tokens, existing["id"]])
        db.execute(update_sql, update_params)
        entry_id = existing["id"]
        if has_stable_id_column:
            _enqueue_sync_op_fail_open(
                db,
                "knowledge_entries",
                stable_id,
                {
                    "category": category,
                    "title": title,
                    "stable_id": stable_id,
                    "content": new_content,
                    "tags": tags,
                    "confidence": new_confidence,
                    "session_id": existing["session_id"],
                    "occurrence_count": new_count,
                    "last_seen": now,
                    "wing": wing,
                    "room": room,
                    "facts": facts_json,
                    "task_id": task_id,
                    "affected_files": files_json,
                    "est_tokens": est_tokens,
                },
            )
        loc = f" [{wing}/{room}]" if wing or room else ""
        msg = f"  Updated existing entry #{entry_id} (seen {new_count}x, confidence → {new_confidence:.2f}){loc}"
        print(msg, file=sys.stderr if quiet else sys.stdout)
    else:
        # Estimate token cost for new entry
        est_tokens = len(f"{title} {content}") // 4

        # Insert new entry
        if has_code_location_columns:
            if has_stable_id_column:
                stable_id = _knowledge_stable_id(session_id, category, title, "")
                db.execute(
                    """
                INSERT INTO knowledge_entries
                    (category, title, stable_id, content, tags, confidence, session_id,
                     occurrence_count, first_seen, last_seen, wing, room,
                     facts, est_tokens, task_id, affected_files,
                     source_file, start_line, end_line, code_language, code_snippet)
                VALUES (?, ?, ?, ?, ?, ?, ?, 1, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
                    (
                        category,
                        title,
                        stable_id,
                        content,
                        tags,
                        confidence,
                        session_id,
                        now,
                        now,
                        wing,
                        room,
                        facts_json,
                        est_tokens,
                        task_id,
                        files_json,
                        source_file,
                        start_line,
                        end_line,
                        code_language,
                        code_snippet,
                    ),
                )
            else:
                db.execute(
                    """
                INSERT INTO knowledge_entries
                    (category, title, content, tags, confidence, session_id,
                     occurrence_count, first_seen, last_seen, wing, room,
                     facts, est_tokens, task_id, affected_files,
                     source_file, start_line, end_line, code_language, code_snippet)
                VALUES (?, ?, ?, ?, ?, ?, 1, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
                    (
                        category,
                        title,
                        content,
                        tags,
                        confidence,
                        session_id,
                        now,
                        now,
                        wing,
                        room,
                        facts_json,
                        est_tokens,
                        task_id,
                        files_json,
                        source_file,
                        start_line,
                        end_line,
                        code_language,
                        code_snippet,
                    ),
                )
        else:
            if has_stable_id_column:
                stable_id = _knowledge_stable_id(session_id, category, title, "")
                db.execute(
                    """
                INSERT INTO knowledge_entries
                    (category, title, stable_id, content, tags, confidence, session_id,
                     occurrence_count, first_seen, last_seen, wing, room,
                     facts, est_tokens, task_id, affected_files)
                VALUES (?, ?, ?, ?, ?, ?, ?, 1, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
                    (
                        category,
                        title,
                        stable_id,
                        content,
                        tags,
                        confidence,
                        session_id,
                        now,
                        now,
                        wing,
                        room,
                        facts_json,
                        est_tokens,
                        task_id,
                        files_json,
                    ),
                )
            else:
                db.execute(
                    """
                INSERT INTO knowledge_entries
                    (category, title, content, tags, confidence, session_id,
                     occurrence_count, first_seen, last_seen, wing, room,
                     facts, est_tokens, task_id, affected_files)
                VALUES (?, ?, ?, ?, ?, ?, 1, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
                    (
                        category,
                        title,
                        content,
                        tags,
                        confidence,
                        session_id,
                        now,
                        now,
                        wing,
                        room,
                        facts_json,
                        est_tokens,
                        task_id,
                        files_json,
                    ),
                )
        entry_id = db.execute("SELECT last_insert_rowid()").fetchone()[0]
        if has_stable_id_column:
            inserted_stable_id = db.execute(
                "SELECT COALESCE(stable_id, '') FROM knowledge_entries WHERE id = ?",
                (entry_id,),
            ).fetchone()[0]
            _enqueue_sync_op_fail_open(
                db,
                "knowledge_entries",
                inserted_stable_id,
                {
                    "category": category,
                    "title": title,
                    "stable_id": inserted_stable_id,
                    "content": content,
                    "tags": tags,
                    "confidence": confidence,
                    "session_id": session_id,
                    "occurrence_count": 1,
                    "first_seen": now,
                    "last_seen": now,
                    "wing": wing,
                    "room": room,
                    "facts": facts_json,
                    "est_tokens": est_tokens,
                    "task_id": task_id,
                    "affected_files": files_json,
                    "source_file": source_file if has_code_location_columns else "",
                    "start_line": start_line if has_code_location_columns else 0,
                    "end_line": end_line if has_code_location_columns else 0,
                    "code_language": code_language if has_code_location_columns else "",
                    "code_snippet": code_snippet if has_code_location_columns else "",
                },
            )
        loc = f" [{wing}/{room}]" if wing or room else ""
        task_note = f" task={task_id}" if task_id else ""
        msg = f"  Added new {category} #{entry_id}{loc}{task_note}"
        print(msg, file=sys.stderr if quiet else sys.stdout)

    # Update FTS index
    _update_fts(db, entry_id, title, content, tags, category, wing, room, facts_json)

    # Generate embedding for the new entry
    _embed_entry(db, entry_id, title, content, quiet=quiet)

    db.commit()
    db.close()
    return entry_id


def _update_fts(
    db: sqlite3.Connection,
    entry_id: int,
    title: str,
    content: str,
    tags: str,
    category: str,
    wing: str = "",
    room: str = "",
    facts_json: str = "[]",
):
    """Update the standalone FTS5 table for this entry."""
    try:
        db.execute("DELETE FROM ke_fts WHERE rowid = ?", (entry_id,))
        db.execute(
            """
            INSERT INTO ke_fts (rowid, title, content, tags, category, wing, room, facts)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """,
            (entry_id, title, content, tags, category, wing, room, facts_json),
        )
    except sqlite3.OperationalError:
        pass  # ke_fts might not exist yet


def _embed_entry(db: sqlite3.Connection, entry_id: int, title: str, content: str, quiet: bool = False):
    """Generate and store embedding for a single entry."""
    try:
        sys.path.insert(0, str(TOOLS_DIR))
        from embed import call_embedding_api, ensure_embedding_tables, load_config, resolve_provider, serialize_vector

        config = load_config()
        provider_name, provider_config = resolve_provider(config)

        if not provider_name:
            return

        ensure_embedding_tables(db)
        text = f"{title}: {content[:2000]}"
        vecs = call_embedding_api([text], provider_config)

        if vecs:
            blob = serialize_vector(vecs[0])
            now = time.strftime("%Y-%m-%dT%H:%M:%S")
            db.execute(
                """
                INSERT OR REPLACE INTO embeddings
                    (source_type, source_id, provider, model, dimensions,
                     vector, text_preview, created_at)
                VALUES ('knowledge', ?, ?, ?, ?, ?, ?, ?)
            """,
                (
                    entry_id,
                    provider_name,
                    provider_config["model"],
                    provider_config.get("dimensions", 768),
                    blob,
                    title[:200],
                    now,
                ),
            )
            print(f"  Embedded with {provider_name}", file=sys.stderr if quiet else sys.stdout)
    except Exception as e:
        print(f"  [info] Embedding skipped: {e}", file=sys.stderr)


def import_from_file(filepath: str):
    """Bulk import knowledge entries from a markdown file.

    Expected format:
    ## mistake: Title Here
    Content describing the mistake...

    ## pattern: Title Here
    Content describing the pattern...
    """
    path = Path(filepath)
    if not path.exists():
        print(f"Error: File not found: {filepath}")
        return

    content = path.read_text(encoding="utf-8", errors="replace")
    entries = []
    current = None

    for line in content.splitlines():
        if line.startswith("## "):
            if current:
                entries.append(current)
            # Parse "## category: Title"
            rest = line[3:].strip()
            if ":" in rest:
                cat, title = rest.split(":", 1)
                cat = cat.strip().lower()
                if cat in ("mistake", "pattern", "decision", "tool", "feature", "refactor", "discovery"):
                    current = {"category": cat, "title": title.strip(), "lines": []}
                else:
                    current = None
            else:
                current = None
        elif current is not None:
            current["lines"].append(line)

    if current:
        entries.append(current)

    if not entries:
        print("No entries found. Use format: ## category: Title")
        return

    print(f"Importing {len(entries)} entries from {filepath}...")
    for entry in entries:
        content = "\n".join(entry["lines"]).strip()
        if content:
            add_entry(entry["category"], entry["title"], content)

    print(f"Done. Imported {len(entries)} entries.")


def list_recent(limit: int = 10):
    """List recently added/updated knowledge entries."""
    db = get_db()

    print(f"\nRecent Knowledge Entries (last {limit})\n")
    rows = db.execute(
        """
        SELECT id, category, title, confidence, occurrence_count,
               last_seen, session_id, est_tokens
        FROM knowledge_entries
        ORDER BY last_seen DESC
        LIMIT ?
    """,
        (limit,),
    ).fetchall()

    for r in rows:
        sid = r["session_id"][:8] if r["session_id"] else "?"
        count = f" ×{r['occurrence_count']}" if r["occurrence_count"] > 1 else ""
        tok = f"  ~{r['est_tokens']}tok" if r["est_tokens"] else ""
        print(f"  #{r['id']:3d} [{r['category']:8s}] {r['title'][:60]}")
        print(f"       conf={r['confidence']:.2f}{count}{tok}  session={sid}..  {r['last_seen'] or '?'}")

    db.close()


def show_stats():
    """Show knowledge base statistics."""
    db = get_db()

    print("\nKnowledge Base Statistics\n")

    total = db.execute("SELECT COUNT(*) FROM knowledge_entries").fetchone()[0]
    print(f"Total entries: {total}")

    for row in db.execute("""
        SELECT category, COUNT(*) as cnt,
               ROUND(AVG(confidence), 2) as avg_conf,
               SUM(occurrence_count) as total_seen
        FROM knowledge_entries
        GROUP BY category ORDER BY cnt DESC
    """):
        print(
            f"  {row['category']:10s}: {row['cnt']:3d} entries  "
            f"avg_conf={row['avg_conf']}  total_seen={row['total_seen']}"
        )

    # Wing breakdown
    wings = db.execute("""
        SELECT wing, COUNT(*) as cnt FROM knowledge_entries
        WHERE wing != '' GROUP BY wing ORDER BY cnt DESC
    """).fetchall()
    if wings:
        print("\nWings:")
        for w in wings:
            print(f"  {w['wing']:15s}: {w['cnt']:3d}")

    # Room breakdown (top 10)
    rooms = db.execute("""
        SELECT room, COUNT(*) as cnt FROM knowledge_entries
        WHERE room != '' GROUP BY room ORDER BY cnt DESC LIMIT 10
    """).fetchall()
    if rooms:
        print("\nTop rooms:")
        for r in rooms:
            print(f"  {r['room']:15s}: {r['cnt']:3d}")

    # Knowledge graph stats
    try:
        rel_count = db.execute("SELECT COUNT(*) FROM entity_relations").fetchone()[0]
        print(f"\nKnowledge graph: {rel_count} relations")
    except sqlite3.OperationalError:
        pass

    # Embedding coverage
    try:
        emb_count = db.execute("SELECT COUNT(*) FROM embeddings WHERE source_type='knowledge'").fetchone()[0]
        print(f"Embedded: {emb_count}/{total}")
    except sqlite3.OperationalError:
        pass

    db.close()


def add_relation(subject: str, predicate: str, obj: str, session_id: str = None):
    """Add a knowledge relation (lightweight knowledge graph)."""
    db = get_db()
    er_columns = {row[1] for row in db.execute("PRAGMA table_info(entity_relations)").fetchall()}
    has_stable_id_column = "stable_id" in er_columns
    if not session_id:
        session_id = detect_session_id()
    now = time.strftime("%Y-%m-%dT%H:%M:%S")
    stable_id = _stable_sha256("entity_relation", subject or "", predicate or "", obj or "")

    try:
        if has_stable_id_column:
            db.execute(
                """
            INSERT OR IGNORE INTO entity_relations
                (subject, predicate, object, stable_id, noted_at, session_id)
            VALUES (?, ?, ?, ?, ?, ?)
        """,
                (subject, predicate, obj, stable_id, now, session_id),
            )
        else:
            db.execute(
                """
            INSERT OR IGNORE INTO entity_relations
                (subject, predicate, object, noted_at, session_id)
            VALUES (?, ?, ?, ?, ?)
        """,
                (subject, predicate, obj, now, session_id),
            )
        inserted = db.execute("SELECT changes()").fetchone()[0] > 0
        if inserted and has_stable_id_column:
            _enqueue_sync_op_fail_open(
                db,
                "entity_relations",
                stable_id,
                {
                    "subject": subject,
                    "predicate": predicate,
                    "object": obj,
                    "stable_id": stable_id,
                    "noted_at": now,
                    "session_id": session_id,
                },
            )
        db.commit()
        if db.total_changes:
            print(f"  ✅ Relation: {subject} --[{predicate}]--> {obj}")
        else:
            print("  — Relation already exists")
    except sqlite3.OperationalError as e:
        print(f"  ❌ Error: {e}. Run migrate-knowledge-v2.py first.")
    db.close()


def main():
    args = sys.argv[1:]

    if not args or "--help" in args or "-h" in args:
        print(__doc__)
        return

    if "--list" in args:
        limit = 10
        if "--limit" in args:
            idx = args.index("--limit")
            limit = int(args[idx + 1]) if idx + 1 < len(args) else 10
        list_recent(limit)
        return

    if "--stats" in args:
        show_stats()
        return

    if "--from-file" in args:
        idx = args.index("--from-file")
        if idx + 1 < len(args):
            import_from_file(args[idx + 1])
        else:
            print("Error: --from-file requires a filepath")
        return

    # Handle --relate command
    if "--relate" in args:
        idx = args.index("--relate")
        positional = [a for a in args[idx + 1 :] if not a.startswith("--")]
        if len(positional) < 3:
            print("Error: --relate needs 3 args: subject predicate object")
            print('  Example: python learn.py --relate "copyToGroup" "reads_from" "config.json"')
            return
        add_relation(positional[0], positional[1], positional[2])
        return

    # Parse category flag
    category = None
    for flag, cat in [
        ("--mistake", "mistake"),
        ("--pattern", "pattern"),
        ("--decision", "decision"),
        ("--tool", "tool"),
        ("--feature", "feature"),
        ("--refactor", "refactor"),
        ("--discovery", "discovery"),
    ]:
        if flag in args:
            category = cat
            break

    if not category:
        print("Error: Specify a category: --mistake, --pattern, --decision, --tool, --feature, --refactor, --discovery")
        print("Or use --relate for knowledge graph. Run --help for usage.")
        return

    # Parse optional flags
    tags = ""
    session_id = None
    confidence = None
    wing = ""
    room = ""
    facts = []
    task_id = ""
    affected_files = []
    source_file = ""
    start_line = 0
    end_line = 0
    code_language = ""
    code_snippet = ""
    code_location_set = False

    if "--tags" in args:
        idx = args.index("--tags")
        tags = args[idx + 1] if idx + 1 < len(args) else ""

    if "--session" in args:
        idx = args.index("--session")
        session_id = args[idx + 1] if idx + 1 < len(args) else None

    if "--confidence" in args:
        idx = args.index("--confidence")
        confidence = float(args[idx + 1]) if idx + 1 < len(args) else None

    if "--wing" in args:
        idx = args.index("--wing")
        wing = args[idx + 1] if idx + 1 < len(args) else ""

    if "--room" in args:
        idx = args.index("--room")
        room = args[idx + 1] if idx + 1 < len(args) else ""

    if "--task" in args:
        idx = args.index("--task")
        task_id = args[idx + 1] if idx + 1 < len(args) else ""

    if "--code-location" in args:
        idx = args.index("--code-location")
        if idx + 1 >= len(args):
            print("Error: --code-location requires a value", file=sys.stderr)
            sys.exit(1)
        raw_code_location = args[idx + 1]
        try:
            source_file, start_line, end_line = _parse_code_location(raw_code_location)
        except ValueError as e:
            print(f"Error: {e}", file=sys.stderr)
            sys.exit(1)
        code_snippet, code_language = _extract_code_snippet(source_file, start_line, end_line)
        code_location_set = True

    # Collect all --fact and --file values (repeatable flags)
    for i, a in enumerate(args):
        if a == "--fact" and i + 1 < len(args):
            facts.append(args[i + 1])
        elif a == "--file" and i + 1 < len(args):
            affected_files.append(args[i + 1])

    # Extract title and content (positional args after flag)
    positional = []
    skip_next = False
    for i, a in enumerate(args):
        if skip_next:
            skip_next = False
            continue
        if a in ("--mistake", "--pattern", "--decision", "--tool", "--feature", "--refactor", "--discovery"):
            continue
        if a in (
            "--tags",
            "--session",
            "--confidence",
            "--limit",
            "--wing",
            "--room",
            "--fact",
            "--task",
            "--file",
            "--code-location",
        ):
            skip_next = True
            continue
        if a.startswith("--"):
            continue
        positional.append(a)

    if len(positional) < 2:
        print("Error: Need title and content. Example:")
        print(f'  python learn.py --{category} "Title" "Description"')
        return

    title = positional[0][:200]  # Limit title length
    content = " ".join(positional[1:])[:10000]  # Limit content to 10KB

    # Quality gate for mistake/pattern/discovery
    skip_gate = "--skip-gate" in args
    skip_scan = "--skip-scan" in args
    json_mode = "--json" in args
    gate_categories = {"mistake", "pattern", "discovery"}
    if not json_mode:
        if category in gate_categories and not skip_gate:
            print(f"Recording {category}...")
            print("  ℹ Quality gate (bypass with --skip-gate):")
            print("    ✓ Could someone Google this in 5 min? → Must be NO")
            print("    ✓ Specific to THIS codebase/project? → Must be YES")
            print("    ✓ Required real debugging/investigation? → Must be YES")
            print("  Gate passed (agent responsibility — record honestly)")
        else:
            print(f"Recording {category}...")

    entry_id = add_entry(
        category,
        title,
        content,
        tags=tags,
        session_id=session_id,
        confidence=confidence,
        wing=wing,
        room=room,
        facts=facts,
        skip_gate=skip_gate,
        skip_scan=skip_scan,
        task_id=task_id,
        affected_files=affected_files,
        source_file=source_file,
        start_line=start_line,
        end_line=end_line,
        code_language=code_language,
        code_snippet=code_snippet,
        code_location_set=code_location_set,
        quiet=json_mode,
    )

    if json_mode:
        # Machine-readable output: emit structured JSON with write result
        if entry_id < 0:
            print(json.dumps({"status": "rejected", "reason": "injection_scan_failed"}, indent=2))
            return
        db = get_db()
        row = db.execute(
            """
            SELECT id, category, title, confidence, session_id, task_id,
                   affected_files, facts, occurrence_count, last_seen
            FROM knowledge_entries WHERE id = ?
        """,
            (entry_id,),
        ).fetchone()
        db.close()
        if row:
            try:
                files = json.loads(row["affected_files"] or "[]")
            except Exception:
                files = []
            try:
                facts_out = json.loads(row["facts"] or "[]")
            except Exception:
                facts_out = []
            status = "added" if row["occurrence_count"] == 1 else "updated"
            print(
                json.dumps(
                    {
                        "status": status,
                        "id": row["id"],
                        "category": row["category"],
                        "title": row["title"],
                        "confidence": row["confidence"],
                        "session_id": row["session_id"],
                        "task_id": row["task_id"] or "",
                        "affected_files": files,
                        "facts": facts_out,
                        "occurrence_count": row["occurrence_count"],
                        "last_seen": row["last_seen"],
                    },
                    indent=2,
                    ensure_ascii=False,
                )
            )
        else:
            print(json.dumps({"status": "error", "id": entry_id, "reason": "entry_not_found_after_write"}, indent=2))
        return

    if facts:
        print(f"  With {len(facts)} fact(s)")
    if affected_files:
        print(f"  Affecting {len(affected_files)} file(s): {', '.join(affected_files[:3])}")
    print("Done.")


if __name__ == "__main__":
    main()
