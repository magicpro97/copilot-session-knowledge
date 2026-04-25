#!/usr/bin/env python3
"""Create deterministic SQLite fixture for browse-ui Playwright tests."""

import os
import sqlite3
import sys
from pathlib import Path

if os.name == "nt":
    for _s in (sys.stdout, sys.stderr):
        if hasattr(_s, "reconfigure"):
            _s.reconfigure(encoding="utf-8", errors="replace")

DB_PATH = Path(__file__).resolve().parent.parent / "e2e" / ".fixtures" / "playwright.db"
SESSION_ID = "e2e-session-0001-abcdef"


def main() -> int:
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    if DB_PATH.exists():
        DB_PATH.unlink()

    db = sqlite3.connect(DB_PATH)
    try:
        db.executescript("""
            CREATE TABLE sessions (
                id TEXT PRIMARY KEY,
                path TEXT NOT NULL,
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
            );
            CREATE TABLE documents (
                id INTEGER PRIMARY KEY,
                session_id TEXT,
                doc_type TEXT,
                seq INTEGER,
                title TEXT,
                file_path TEXT,
                file_hash TEXT,
                size_bytes INTEGER,
                content_preview TEXT,
                indexed_at TEXT,
                source TEXT
            );
            CREATE TABLE sections (
                id INTEGER PRIMARY KEY,
                document_id INTEGER,
                section_name TEXT,
                content TEXT
            );
            CREATE TABLE knowledge_entries (
                id INTEGER PRIMARY KEY,
                title TEXT,
                content TEXT,
                tags TEXT DEFAULT '',
                category TEXT,
                wing TEXT,
                room TEXT,
                entry_type TEXT,
                session_id TEXT,
                created_at TEXT,
                facts TEXT DEFAULT '[]',
                est_tokens INTEGER DEFAULT 0
            );
            CREATE TABLE entity_relations (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                subject TEXT NOT NULL,
                predicate TEXT NOT NULL,
                object TEXT NOT NULL,
                noted_at TEXT DEFAULT (datetime('now')),
                session_id TEXT DEFAULT '',
                UNIQUE(subject, predicate, object)
            );
            CREATE TABLE embeddings (
                id INTEGER PRIMARY KEY,
                source_type TEXT,
                source_id INTEGER,
                dimensions INTEGER,
                vector BLOB
            );
            CREATE TABLE event_offsets (
                session_id TEXT NOT NULL,
                event_id INTEGER NOT NULL,
                byte_offset INTEGER NOT NULL,
                file_mtime REAL NOT NULL,
                PRIMARY KEY (session_id, event_id)
            );
            CREATE TABLE search_feedback (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                query TEXT,
                result_id TEXT,
                result_kind TEXT,
                verdict INTEGER NOT NULL CHECK(verdict IN (-1,0,1)),
                comment TEXT,
                user_agent TEXT,
                created_at TEXT NOT NULL
            );
            CREATE TABLE schema_version (
                version INTEGER PRIMARY KEY,
                name TEXT,
                migrated_at TEXT DEFAULT (datetime('now'))
            );
            CREATE VIRTUAL TABLE sessions_fts USING fts5(
                session_id UNINDEXED,
                title,
                user_messages,
                assistant_messages,
                tool_names,
                tokenize='porter unicode61 remove_diacritics 2'
            );
            CREATE VIRTUAL TABLE ke_fts USING fts5(
                title, content, tags, category, wing, room, facts,
                tokenize='unicode61 remove_diacritics 2'
            );
        """)

        db.execute(
            """INSERT INTO sessions (
                id, path, summary, total_checkpoints, total_research, total_files, has_plan,
                source, indexed_at, file_mtime, indexed_at_r, fts_indexed_at, event_count_estimate, file_size_bytes
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, datetime('now'), ?, ?, ?, ?, ?)""",
            (SESSION_ID, "", "E2E deterministic session", 1, 0, 2, 0, "copilot", 1.0, 2.0, 3.0, 4, 1024),
        )
        db.execute(
            """INSERT INTO documents (
                id, session_id, doc_type, seq, title, file_path, file_hash, size_bytes, content_preview, indexed_at, source
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, datetime('now'), ?)""",
            (1, SESSION_ID, "checkpoint", 1, "E2E Checkpoint", "", "", 128, "Seeded preview", "copilot"),
        )
        db.execute(
            "INSERT INTO sections (id, document_id, section_name, content) VALUES (?, ?, ?, ?)",
            (1, 1, "overview", "Seeded content for deterministic Playwright tests."),
        )
        db.execute(
            """INSERT INTO knowledge_entries (
                id, title, content, tags, category, wing, room, entry_type, session_id, created_at, facts, est_tokens
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, datetime('now'), ?, ?)""",
            (
                1,
                "Deterministic E2E knowledge",
                "Session detail and graph fixture entry.",
                "e2e",
                "pattern",
                "tests",
                "browse-ui",
                "discovery",
                SESSION_ID,
                "[]",
                8,
            ),
        )
        db.execute(
            "INSERT INTO entity_relations (subject, predicate, object, session_id) VALUES (?, ?, ?, ?)",
            ("Deterministic E2E knowledge", "supports", "Playwright hardening", SESSION_ID),
        )
        db.execute(
            "INSERT INTO sessions_fts (session_id, title, user_messages, assistant_messages, tool_names) VALUES (?, ?, ?, ?, ?)",
            (SESSION_ID, "E2E deterministic session", "seeded user", "seeded assistant", "playwright"),
        )
        db.execute(
            "INSERT INTO ke_fts (rowid, title, content, tags, category, wing, room, facts) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (1, "Deterministic E2E knowledge", "Session detail and graph fixture entry.", "e2e", "pattern", "tests", "browse-ui", "[]"),
        )
        db.execute("INSERT INTO schema_version (version, name) VALUES (?, ?)", (9, "search_feedback_table"))
        db.commit()
    finally:
        db.close()

    print(f"Prepared deterministic Playwright DB: {DB_PATH}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
