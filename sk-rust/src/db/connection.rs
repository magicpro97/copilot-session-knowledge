use crate::config::resolve_home_dir;
use rusqlite::{Connection, OpenFlags, Result};
use std::path::PathBuf;

/// Read-only handle to knowledge.db with performance PRAGMAs applied.
pub struct KnowledgeDb {
    pub conn: Connection,
}

impl KnowledgeDb {
    pub fn open() -> Result<Self> {
        let path = knowledge_db_path();
        let conn = Connection::open_with_flags(
            &path,
            OpenFlags::SQLITE_OPEN_READ_ONLY | OpenFlags::SQLITE_OPEN_NO_MUTEX,
        )?;
        conn.execute_batch(
            "PRAGMA mmap_size=268435456;
             PRAGMA journal_mode=WAL;
             PRAGMA query_only=ON;",
        )?;
        Ok(Self { conn })
    }
}

pub fn knowledge_db_path() -> PathBuf {
    // Allow tests and callers to override the DB path via SK_DB env var
    if let Ok(path) = std::env::var("SK_DB") {
        return PathBuf::from(path);
    }
    resolve_home_dir()
        .unwrap_or_else(|| PathBuf::from("."))
        .join(".copilot")
        .join("session-state")
        .join("knowledge.db")
}
