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

    python learn.py --relate "copyToGroup" "reads_from" "patient-dynamic-form.json"
    python learn.py --relate "addPatient Lambda" "writes_to" "dataTable"

    python learn.py --from-file notes.md          # Bulk import from markdown
    python learn.py --list                        # List recent entries
    python learn.py --stats                       # Show knowledge stats
"""

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
    ({"lambda", "dynamodb", "sqs", "cdk", "api", "cognito", "s3",
      "eventbridge", "cloudwatch", "sns", "websocket", "nefoap"}, "backend"),
    ({"expo", "react", "react-native", "screen", "component", "css",
      "ui", "navigation", "hook"}, "frontend"),
    ({"jest", "playwright", "e2e", "test", "testing", "coverage"}, "testing"),
    ({"vpc", "cloudwatch", "cdk", "cloudformation", "infrastructure",
      "deploy", "pipeline"}, "infrastructure"),
    ({"git", "ci", "cd", "docker", "devops", "proxy", "tls", "npm",
      "yarn", "package-manager"}, "devops"),
    ({"typescript", "javascript", "eslint", "prettier", "i18n",
      "mermaid", "openapi"}, "shared"),
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
    (re.compile(r"(?i)\bignore\s+(all\s+)?previous\s+instructions?\b"), "prompt injection: 'ignore previous instructions'"),
    (re.compile(r"(?i)\byou\s+are\s+now\b"), "role hijacking: 'you are now'"),
    (re.compile(r"(?i)\bsystem\s*:\s*"), "role injection: 'system:' prefix"),
    (re.compile(r"(?i)\b(assistant|user|human)\s*:\s*"), "role injection: fake role prefix"),
    (re.compile(r"(?i)\bforget\s+(everything|all|your)\b"), "memory manipulation: 'forget everything'"),
    (re.compile(r"(?i)\bdo\s+not\s+follow\b"), "instruction override: 'do not follow'"),
    (re.compile(r"(?i)\b(api[_-]?key|secret[_-]?key|password|token)\s*[:=]\s*\S+"), "credential leak: API key/password/token"),
    (re.compile(r"(?i)ssh-rsa\s+AAAA"), "credential leak: SSH public key"),
    (re.compile(r"(?i)-----BEGIN\s+(RSA\s+)?PRIVATE\s+KEY-----"), "credential leak: private key"),
    (re.compile(r"(?i)\beval\s*\("), "code injection: eval()"),
    (re.compile(r"(?i)\bexec\s*\("), "code injection: exec()"),
    (re.compile(r"[\u200b\u200c\u200d\u2060\ufeff]"), "invisible Unicode characters (zero-width)"),
    (re.compile(r"(?i)\bACT\s+AS\b"), "role hijacking: 'act as'"),
    (re.compile(r"(?i)\bpretend\s+(you\s+are|to\s+be)\b"), "role hijacking: 'pretend to be'"),
    (re.compile(r"(?i)\b(curl|wget|nc|ncat)\s+.*\|\s*(ba)?sh\b"), "remote code execution pattern"),
]


def scan_content_for_injection(title: str, content: str) -> list:
    """Scan title + content for injection patterns. Returns list of warnings."""
    warnings = []
    text = f"{title}\n{content}"
    for pattern, description in _INJECTION_PATTERNS:
        if pattern.search(text):
            warnings.append(description)
    return warnings


def get_db() -> sqlite3.Connection:
    if not DB_PATH.exists():
        print("Error: Knowledge DB not found. Run build-session-index.py first.",
              file=sys.stderr)
        sys.exit(1)
    db = sqlite3.connect(str(DB_PATH))
    db.row_factory = sqlite3.Row
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


def add_entry(category: str, title: str, content: str,
              tags: str = "", session_id: str = None,
              confidence: float = None,
              wing: str = "", room: str = "",
              facts: list = None, skip_gate: bool = False) -> int:
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

    # Injection scanning (before any DB writes)
    if not skip_gate:  # skip_gate also skips injection scan (for meta-entries about injection)
        injection_warnings = scan_content_for_injection(title, content)
        if injection_warnings:
            print(f"  ⚠ REJECTED — injection pattern detected:", file=sys.stderr)
            for w in injection_warnings:
                print(f"    ✗ {w}", file=sys.stderr)
            print(f"  Use --skip-gate to bypass (only for documenting injection patterns)", file=sys.stderr)
            return -1

    if not session_id:
        session_id = detect_session_id()

    if confidence is None:
        confidence = {"mistake": 0.7, "pattern": 0.6,
                      "decision": 0.8, "tool": 0.5,
                      "feature": 0.7, "refactor": 0.6,
                      "discovery": 0.6}.get(category, 0.5)

    # Auto-detect wing/room if not provided
    if not wing:
        wing = _detect_wing(tags, title, content)
    if not room:
        room = _detect_room(tags, title, content)

    # Serialize facts to JSON
    facts_json = json.dumps(facts or [], ensure_ascii=False)

    now = time.strftime("%Y-%m-%dT%H:%M:%S")

    # Check for existing entry with same title in same category
    existing = db.execute("""
        SELECT id, occurrence_count, content FROM knowledge_entries
        WHERE category = ? AND title = ?
        ORDER BY confidence DESC LIMIT 1
    """, (category, title)).fetchone()

    if existing:
        # Update existing: bump occurrence count, update content if longer
        new_count = existing["occurrence_count"] + 1
        new_content = content if len(content) > len(existing["content"]) else existing["content"]
        new_confidence = min(1.0, confidence + 0.05 * (new_count - 1))

        # Estimate token cost from the content that will actually be stored
        est_tokens = len(f"{title} {new_content}") // 4

        db.execute("""
            UPDATE knowledge_entries
            SET content = ?, occurrence_count = ?, confidence = ?,
                last_seen = ?, tags = CASE WHEN ? != '' THEN ? ELSE tags END,
                wing = CASE WHEN ? != '' THEN ? ELSE wing END,
                room = CASE WHEN ? != '' THEN ? ELSE room END,
                facts = CASE WHEN ? != '[]' THEN ? ELSE facts END,
                est_tokens = ?
            WHERE id = ?
        """, (new_content, new_count, new_confidence, now,
              tags, tags, wing, wing, room, room,
              facts_json, facts_json, est_tokens, existing["id"]))
        entry_id = existing["id"]
        loc = f" [{wing}/{room}]" if wing or room else ""
        print(f"  Updated existing entry #{entry_id} (seen {new_count}x, "
              f"confidence → {new_confidence:.2f}){loc}")
    else:
        # Estimate token cost for new entry
        est_tokens = len(f"{title} {content}") // 4

        # Insert new entry
        db.execute("""
            INSERT INTO knowledge_entries
                (category, title, content, tags, confidence, session_id,
                 occurrence_count, first_seen, last_seen, wing, room,
                 facts, est_tokens)
            VALUES (?, ?, ?, ?, ?, ?, 1, ?, ?, ?, ?, ?, ?)
        """, (category, title, content, tags, confidence,
              session_id, now, now, wing, room, facts_json, est_tokens))
        entry_id = db.execute("SELECT last_insert_rowid()").fetchone()[0]
        loc = f" [{wing}/{room}]" if wing or room else ""
        print(f"  Added new {category} #{entry_id}{loc}")

    # Update FTS index
    _update_fts(db, entry_id, title, content, tags, category, wing, room, facts_json)

    # Generate embedding for the new entry
    _embed_entry(db, entry_id, title, content)

    db.commit()
    db.close()
    return entry_id


def _update_fts(db: sqlite3.Connection, entry_id: int,
                title: str, content: str, tags: str, category: str,
                wing: str = "", room: str = "", facts_json: str = "[]"):
    """Update the standalone FTS5 table for this entry."""
    try:
        db.execute("DELETE FROM ke_fts WHERE rowid = ?", (entry_id,))
        db.execute("""
            INSERT INTO ke_fts (rowid, title, content, tags, category, wing, room, facts)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """, (entry_id, title, content, tags, category, wing, room, facts_json))
    except sqlite3.OperationalError:
        pass  # ke_fts might not exist yet


def _embed_entry(db: sqlite3.Connection, entry_id: int,
                 title: str, content: str):
    """Generate and store embedding for a single entry."""
    try:
        sys.path.insert(0, str(TOOLS_DIR))
        from embed import (load_config, resolve_provider, call_embedding_api,
                           ensure_embedding_tables, serialize_vector)

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
            db.execute("""
                INSERT OR REPLACE INTO embeddings
                    (source_type, source_id, provider, model, dimensions,
                     vector, text_preview, created_at)
                VALUES ('knowledge', ?, ?, ?, ?, ?, ?, ?)
            """, (entry_id, provider_name, provider_config["model"],
                  provider_config.get("dimensions", 768), blob,
                  title[:200], now))
            print(f"  Embedded with {provider_name}")
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
                if cat in ("mistake", "pattern", "decision", "tool",
                           "feature", "refactor", "discovery"):
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
    rows = db.execute("""
        SELECT id, category, title, confidence, occurrence_count,
               last_seen, session_id
        FROM knowledge_entries
        ORDER BY last_seen DESC
        LIMIT ?
    """, (limit,)).fetchall()

    for r in rows:
        sid = r["session_id"][:8] if r["session_id"] else "?"
        count = f" ×{r['occurrence_count']}" if r["occurrence_count"] > 1 else ""
        print(f"  #{r['id']:3d} [{r['category']:8s}] {r['title'][:60]}")
        print(f"       conf={r['confidence']:.2f}{count}  "
              f"session={sid}..  {r['last_seen'] or '?'}")

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
        print(f"  {row['category']:10s}: {row['cnt']:3d} entries  "
              f"avg_conf={row['avg_conf']}  total_seen={row['total_seen']}")

    # Wing breakdown
    wings = db.execute("""
        SELECT wing, COUNT(*) as cnt FROM knowledge_entries
        WHERE wing != '' GROUP BY wing ORDER BY cnt DESC
    """).fetchall()
    if wings:
        print(f"\nWings:")
        for w in wings:
            print(f"  {w['wing']:15s}: {w['cnt']:3d}")

    # Room breakdown (top 10)
    rooms = db.execute("""
        SELECT room, COUNT(*) as cnt FROM knowledge_entries
        WHERE room != '' GROUP BY room ORDER BY cnt DESC LIMIT 10
    """).fetchall()
    if rooms:
        print(f"\nTop rooms:")
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
        emb_count = db.execute(
            "SELECT COUNT(*) FROM embeddings WHERE source_type='knowledge'"
        ).fetchone()[0]
        print(f"Embedded: {emb_count}/{total}")
    except sqlite3.OperationalError:
        pass

    db.close()


def add_relation(subject: str, predicate: str, obj: str,
                 session_id: str = None):
    """Add a knowledge relation (lightweight knowledge graph)."""
    db = get_db()
    if not session_id:
        session_id = detect_session_id()
    now = time.strftime("%Y-%m-%dT%H:%M:%S")

    try:
        db.execute("""
            INSERT OR IGNORE INTO entity_relations
                (subject, predicate, object, noted_at, session_id)
            VALUES (?, ?, ?, ?, ?)
        """, (subject, predicate, obj, now, session_id))
        db.commit()
        if db.total_changes:
            print(f"  ✅ Relation: {subject} --[{predicate}]--> {obj}")
        else:
            print(f"  — Relation already exists")
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
        positional = [a for a in args[idx + 1:] if not a.startswith("--")]
        if len(positional) < 3:
            print('Error: --relate needs 3 args: subject predicate object')
            print('  Example: python learn.py --relate "copyToGroup" "reads_from" "config.json"')
            return
        add_relation(positional[0], positional[1], positional[2])
        return

    # Parse category flag
    category = None
    for flag, cat in [("--mistake", "mistake"), ("--pattern", "pattern"),
                      ("--decision", "decision"), ("--tool", "tool"),
                      ("--feature", "feature"), ("--refactor", "refactor"),
                      ("--discovery", "discovery")]:
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

    # Collect all --fact values (repeatable flag)
    for i, a in enumerate(args):
        if a == "--fact" and i + 1 < len(args):
            facts.append(args[i + 1])

    # Extract title and content (positional args after flag)
    positional = []
    skip_next = False
    for i, a in enumerate(args):
        if skip_next:
            skip_next = False
            continue
        if a in ("--mistake", "--pattern", "--decision", "--tool",
                 "--feature", "--refactor", "--discovery"):
            continue
        if a in ("--tags", "--session", "--confidence", "--limit",
                 "--wing", "--room", "--fact"):
            skip_next = True
            continue
        if a.startswith("--"):
            continue
        positional.append(a)

    if len(positional) < 2:
        print(f'Error: Need title and content. Example:')
        print(f'  python learn.py --{category} "Title" "Description"')
        return

    title = positional[0][:200]  # Limit title length
    content = " ".join(positional[1:])[:10000]  # Limit content to 10KB

    # Quality gate for mistake/pattern/discovery
    skip_gate = "--skip-gate" in args
    gate_categories = {"mistake", "pattern", "discovery"}
    if category in gate_categories and not skip_gate:
        print(f"Recording {category}...")
        print(f"  ℹ Quality gate (bypass with --skip-gate):")
        print(f"    ✓ Could someone Google this in 5 min? → Must be NO")
        print(f"    ✓ Specific to THIS codebase/project? → Must be YES")
        print(f"    ✓ Required real debugging/investigation? → Must be YES")
        print(f"  Gate passed (agent responsibility — record honestly)")
    else:
        print(f"Recording {category}...")

    add_entry(category, title, content, tags=tags,
              session_id=session_id, confidence=confidence,
              wing=wing, room=room, facts=facts, skip_gate=skip_gate)
    if facts:
        print(f"  With {len(facts)} fact(s)")
    print("Done.")


if __name__ == "__main__":
    main()
