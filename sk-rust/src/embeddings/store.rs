//! Vector storage helpers for embeddings.
//!
//! Matches Python embed.py:
//! - `serialize_vector` / `deserialize_vector`: little-endian float32 blobs
//! - `cosine_similarity`: pure-Rust dot-product cosine
//! - `ensure_embedding_tables`: idempotent schema creation
//! - `vector_search_stored`: brute-force cosine search over stored blobs
//! - `store_batch_embeddings`: write a batch of embedding vectors to the DB
//! - `store_tfidf_model`: write / replace the TF-IDF model blob in the DB

use rusqlite::Connection;

// ── Serialization ─────────────────────────────────────────────────────

/// Serialize a float32 slice to little-endian bytes.
///
/// Matches Python: `struct.pack(f"<{n}f", *vec)`
pub fn serialize_vector(vec: &[f32]) -> Vec<u8> {
    let mut bytes = Vec::with_capacity(vec.len() * 4);
    for &v in vec {
        bytes.extend_from_slice(&v.to_le_bytes());
    }
    bytes
}

/// Deserialize little-endian bytes back to a float32 vector.
///
/// Matches Python: `struct.unpack(f"<{n}f", blob)`
pub fn deserialize_vector(blob: &[u8]) -> Vec<f32> {
    blob.chunks_exact(4)
        .map(|b| f32::from_le_bytes([b[0], b[1], b[2], b[3]]))
        .collect()
}

// ── Similarity ─────────────────────────────────────────────────────────

/// Cosine similarity between two float32 vectors.
///
/// Returns 0.0 for zero-length vectors, zero-norm vectors, or mismatched lengths.
/// Mirrors Python's `cosine_similarity_vectors()` in embed.py.
///
/// ## Optimisation (#359)
/// Uses a single pass over both vectors (computing dot, ‖a‖², ‖b‖² together)
/// with 4-wide loop unrolling to encourage the compiler's auto-vectoriser.
/// The scalar tail handles any remaining elements, giving a pure-Rust scalar
/// fallback that is correct on all targets.
pub fn cosine_similarity(a: &[f32], b: &[f32]) -> f32 {
    if a.len() != b.len() || a.is_empty() {
        return 0.0;
    }

    let n = a.len();
    let mut dot = 0.0f32;
    let mut norm_a_sq = 0.0f32;
    let mut norm_b_sq = 0.0f32;

    // 4-wide unrolled inner loop — single pass, no separate norm sweeps.
    let chunks = n / 4;
    for i in 0..chunks {
        let base = i * 4;
        // SAFETY: base + 3 < n because chunks = n/4 and base = i*4 ≤ (n/4-1)*4 = n - 4
        let (a0, a1, a2, a3) = (a[base], a[base + 1], a[base + 2], a[base + 3]);
        let (b0, b1, b2, b3) = (b[base], b[base + 1], b[base + 2], b[base + 3]);
        dot += a0 * b0 + a1 * b1 + a2 * b2 + a3 * b3;
        norm_a_sq += a0 * a0 + a1 * a1 + a2 * a2 + a3 * a3;
        norm_b_sq += b0 * b0 + b1 * b1 + b2 * b2 + b3 * b3;
    }

    // Scalar tail for remainder elements.
    let tail_start = chunks * 4;
    for i in tail_start..n {
        let (ai, bi) = (a[i], b[i]);
        dot += ai * bi;
        norm_a_sq += ai * ai;
        norm_b_sq += bi * bi;
    }

    if norm_a_sq == 0.0 || norm_b_sq == 0.0 {
        return 0.0;
    }
    dot / (norm_a_sq.sqrt() * norm_b_sq.sqrt())
}

// ── Schema ─────────────────────────────────────────────────────────────

/// Create embedding tables if they do not exist.
///
/// Idempotent — safe to call on every startup.
/// Mirrors Python's `ensure_embedding_tables()`.
pub fn ensure_embedding_tables(conn: &Connection) -> rusqlite::Result<()> {
    conn.execute_batch(
        "CREATE TABLE IF NOT EXISTS embeddings (
            id           INTEGER PRIMARY KEY AUTOINCREMENT,
            source_type  TEXT    NOT NULL,
            source_id    INTEGER NOT NULL,
            provider     TEXT    NOT NULL,
            model        TEXT    NOT NULL,
            dimensions   INTEGER NOT NULL,
            vector       BLOB    NOT NULL,
            text_preview TEXT    DEFAULT '',
            created_at   TEXT,
            UNIQUE(source_type, source_id)
        );
        CREATE INDEX IF NOT EXISTS idx_emb_source
            ON embeddings(source_type, source_id);
        CREATE TABLE IF NOT EXISTS embedding_meta (
            key   TEXT PRIMARY KEY,
            value TEXT
        );
        CREATE TABLE IF NOT EXISTS tfidf_model (
            id         INTEGER PRIMARY KEY DEFAULT 1,
            model_blob BLOB,
            doc_count  INTEGER DEFAULT 0,
            built_at   TEXT
        );",
    )?;
    // Add binary column to tfidf_model if it does not exist yet (#356).
    // ALTER TABLE returns an error when the column already exists; we ignore it.
    let _ = conn.execute("ALTER TABLE tfidf_model ADD COLUMN model_bin BLOB", []);
    Ok(())
}

// ── Vector search ──────────────────────────────────────────────────────

/// Brute-force cosine similarity search over all stored embedding blobs.
///
/// Returns `(source_type, source_id, score)` tuples, sorted by score descending.
/// Only results with `score > 0.01` are returned.
///
/// This runs entirely in Rust without any network calls — it re-uses
/// the float blobs already stored by `embed.py --build`.
#[allow(dead_code)]
pub fn vector_search_stored(
    conn: &Connection,
    query_vec: &[f32],
    source_type_filter: Option<&str>,
    limit: usize,
) -> rusqlite::Result<Vec<(String, i64, f32)>> {
    let (sql, use_filter) = if source_type_filter.is_some() {
        (
            "SELECT source_type, source_id, vector \
             FROM embeddings WHERE source_type = ?",
            true,
        )
    } else {
        (
            "SELECT source_type, source_id, vector FROM embeddings",
            false,
        )
    };

    let mut stmt = conn.prepare(sql)?;

    let rows: Vec<(String, i64, Vec<u8>)> = if use_filter {
        let st = source_type_filter.unwrap();
        stmt.query_map(rusqlite::params![st], |row| {
            Ok((
                row.get::<_, String>(0)?,
                row.get::<_, i64>(1)?,
                row.get::<_, Vec<u8>>(2)?,
            ))
        })?
        .filter_map(|r| r.ok())
        .collect()
    } else {
        stmt.query_map([], |row| {
            Ok((
                row.get::<_, String>(0)?,
                row.get::<_, i64>(1)?,
                row.get::<_, Vec<u8>>(2)?,
            ))
        })?
        .filter_map(|r| r.ok())
        .collect()
    };

    let mut results: Vec<(String, i64, f32)> = rows
        .into_iter()
        .map(|(st, sid, blob)| {
            let vec = deserialize_vector(&blob);
            let score = cosine_similarity(query_vec, &vec);
            (st, sid, score)
        })
        .filter(|(_, _, score)| *score > 0.01)
        .collect();

    results.sort_by(|a, b| b.2.partial_cmp(&a.2).unwrap_or(std::cmp::Ordering::Equal));
    results.truncate(limit);
    Ok(results)
}

// ── Timestamp helper ───────────────────────────────────────────────────

/// Current UTC time as `YYYY-MM-DDTHH:MM:SS` string.
///
/// Implemented with stdlib `SystemTime` to avoid pulling in chrono.
fn now_iso() -> String {
    use std::time::{SystemTime, UNIX_EPOCH};
    let secs = SystemTime::now()
        .duration_since(UNIX_EPOCH)
        .unwrap_or_default()
        .as_secs();
    let sec = secs % 60;
    let min = (secs / 60) % 60;
    let hour = (secs / 3600) % 24;
    let days = secs / 86400;

    // Days → (year, month, day) via the proleptic Gregorian calendar
    let (y, m, d) = days_to_ymd(days);
    format!("{y:04}-{m:02}-{d:02}T{hour:02}:{min:02}:{sec:02}")
}

fn days_to_ymd(mut days: u64) -> (u32, u32, u32) {
    // Gregorian calendar cycle: 400 years = 146 097 days
    let mut y = 1970u32;
    loop {
        let days_in_year: u64 = if is_leap(y) { 366 } else { 365 };
        if days < days_in_year {
            break;
        }
        days -= days_in_year;
        y += 1;
    }
    let leap = is_leap(y);
    let month_days: [u64; 12] = [
        31,
        if leap { 29 } else { 28 },
        31,
        30,
        31,
        30,
        31,
        31,
        30,
        31,
        30,
        31,
    ];
    let mut m = 0u32;
    for (i, &md) in month_days.iter().enumerate() {
        if days < md {
            m = i as u32 + 1;
            break;
        }
        days -= md;
    }
    (y, m, days as u32 + 1)
}

fn is_leap(y: u32) -> bool {
    (y % 4 == 0 && y % 100 != 0) || y % 400 == 0
}

// ── Batch write ────────────────────────────────────────────────────────

/// Write a batch of embedding vectors to the `embeddings` table.
///
/// Mirrors Python's `store_embeddings()`:
/// - Uses `INSERT OR REPLACE` (upsert on `(source_type, source_id)`)
/// - Updates `embedding_meta.last_build` with the current timestamp
/// - `items` = slice of `(source_id, vector, text_preview)` tuples
///
/// Does **not** commit — callers must call `conn.execute("PRAGMA wal_checkpoint",…)`
/// or wrap in a transaction as appropriate.
pub fn store_batch_embeddings(
    conn: &Connection,
    source_type: &str,
    items: &[(i64, Vec<f32>, String)],
    provider: &str,
    model: &str,
    dimensions: u32,
) -> rusqlite::Result<()> {
    let now = now_iso();
    for (source_id, vector, preview) in items {
        let blob = serialize_vector(vector);
        let preview_trimmed = if preview.len() > 200 {
            &preview[..200]
        } else {
            preview.as_str()
        };
        conn.execute(
            "INSERT OR REPLACE INTO embeddings \
             (source_type, source_id, provider, model, dimensions, vector, \
              text_preview, created_at) \
             VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            rusqlite::params![
                source_type,
                source_id,
                provider,
                model,
                dimensions,
                &blob as &[u8],
                preview_trimmed,
                now,
            ],
        )?;
    }
    conn.execute(
        "INSERT OR REPLACE INTO embedding_meta (key, value) VALUES ('last_build', ?)",
        rusqlite::params![now],
    )?;
    Ok(())
}

/// Write (or replace) the TF-IDF model blob in the `tfidf_model` table.
///
/// Mirrors Python's inline `INSERT OR REPLACE INTO tfidf_model …` in
/// `build_embeddings()`.
pub fn store_tfidf_model(
    conn: &Connection,
    model_blob: &[u8],
    doc_count: usize,
) -> rusqlite::Result<()> {
    let now = now_iso();
    conn.execute(
        "INSERT OR REPLACE INTO tfidf_model (id, model_blob, doc_count, built_at) \
         VALUES (1, ?, ?, ?)",
        rusqlite::params![model_blob, doc_count as i64, now],
    )?;
    Ok(())
}

/// Dual-write the TF-IDF model: JSON blob (Python-compatible) + binary blob (Rust fast path).
///
/// Rust searches will prefer `model_bin` when available; Python reads `model_blob` only.
/// A missing or corrupt `model_bin` falls back transparently to `model_blob`.
///
/// This is the preferred write path for all native Rust rebuild code paths (#356).
pub fn store_tfidf_model_with_binary(
    conn: &Connection,
    json_blob: &[u8],
    bin_blob: &[u8],
    doc_count: usize,
) -> rusqlite::Result<()> {
    let now = now_iso();
    conn.execute(
        "INSERT OR REPLACE INTO tfidf_model (id, model_blob, model_bin, doc_count, built_at) \
         VALUES (1, ?, ?, ?, ?)",
        rusqlite::params![json_blob, bin_blob, doc_count as i64, now],
    )?;
    Ok(())
}

// ── Tests ──────────────────────────────────────────────────────────────

#[cfg(test)]
mod tests {
    use super::*;

    // -- Serialization --

    #[test]
    fn serialize_deserialize_roundtrip() {
        let original = vec![0.1f32, -0.5, 1.0, 0.0];
        let bytes = serialize_vector(&original);
        assert_eq!(bytes.len(), original.len() * 4);
        let decoded = deserialize_vector(&bytes);
        for (a, b) in original.iter().zip(decoded.iter()) {
            assert!((a - b).abs() < 1e-6, "roundtrip mismatch: {a} vs {b}");
        }
    }

    #[test]
    fn serialize_matches_python_little_endian() {
        // Python: struct.pack("<2f", 1.0, 0.5)
        // 1.0f32 LE bytes = [0x00, 0x00, 0x80, 0x3f]
        // 0.5f32 LE bytes = [0x00, 0x00, 0x00, 0x3f]
        let bytes = serialize_vector(&[1.0f32, 0.5f32]);
        assert_eq!(&bytes[0..4], &[0x00u8, 0x00, 0x80, 0x3f]);
        assert_eq!(&bytes[4..8], &[0x00u8, 0x00, 0x00, 0x3f]);
    }

    #[test]
    fn deserialize_empty_blob() {
        let result = deserialize_vector(&[]);
        assert!(result.is_empty());
    }

    #[test]
    fn deserialize_truncated_blob_ignores_partial_bytes() {
        // 5 bytes: only 1 complete float32 (4 bytes), last byte ignored
        let bytes = vec![0x00u8, 0x00, 0x80, 0x3f, 0xFF];
        let result = deserialize_vector(&bytes);
        assert_eq!(result.len(), 1);
        assert!((result[0] - 1.0f32).abs() < 1e-6);
    }

    // -- Cosine similarity --

    #[test]
    fn cosine_identical_vectors_is_one() {
        let v = vec![1.0f32, 2.0, 3.0];
        let sim = cosine_similarity(&v, &v);
        assert!(
            (sim - 1.0).abs() < 1e-5,
            "identical vectors → 1.0, got {sim}"
        );
    }

    #[test]
    fn cosine_orthogonal_vectors_is_zero() {
        let a = vec![1.0f32, 0.0, 0.0];
        let b = vec![0.0f32, 1.0, 0.0];
        let sim = cosine_similarity(&a, &b);
        assert!(sim.abs() < 1e-5, "orthogonal vectors → 0.0, got {sim}");
    }

    #[test]
    fn cosine_opposite_vectors_is_minus_one() {
        let a = vec![1.0f32, 0.0];
        let b = vec![-1.0f32, 0.0];
        let sim = cosine_similarity(&a, &b);
        assert!(
            (sim + 1.0).abs() < 1e-5,
            "opposite vectors → -1.0, got {sim}"
        );
    }

    #[test]
    fn cosine_zero_vector_returns_zero() {
        let a = vec![0.0f32, 0.0, 0.0];
        let b = vec![1.0f32, 2.0, 3.0];
        assert_eq!(cosine_similarity(&a, &b), 0.0);
    }

    #[test]
    fn cosine_mismatched_length_returns_zero() {
        let a = vec![1.0f32, 2.0];
        let b = vec![1.0f32, 2.0, 3.0];
        assert_eq!(cosine_similarity(&a, &b), 0.0);
    }

    #[test]
    fn cosine_empty_returns_zero() {
        assert_eq!(cosine_similarity(&[], &[]), 0.0);
    }

    #[test]
    fn cosine_known_value() {
        // [1,1] vs [1,0]: dot=1, norms=sqrt(2)*1 → sim=1/sqrt(2)≈0.7071
        let a = vec![1.0f32, 1.0];
        let b = vec![1.0f32, 0.0];
        let sim = cosine_similarity(&a, &b);
        assert!(
            (sim - std::f32::consts::FRAC_1_SQRT_2).abs() < 1e-5,
            "got {sim}"
        );
    }

    // -- DB + vector search --

    #[test]
    fn ensure_tables_idempotent() {
        let conn = rusqlite::Connection::open_in_memory().unwrap();
        ensure_embedding_tables(&conn).unwrap();
        ensure_embedding_tables(&conn).unwrap(); // second call must not error
    }

    #[test]
    fn vector_search_finds_nearest_neighbor() {
        let conn = rusqlite::Connection::open_in_memory().unwrap();
        ensure_embedding_tables(&conn).unwrap();

        let v1 = serialize_vector(&[1.0f32, 0.0, 0.0]);
        let v2 = serialize_vector(&[0.0f32, 1.0, 0.0]);
        let v3 = serialize_vector(&[0.9f32, 0.1, 0.0]); // closest to v1

        for (i, (st, blob)) in [("section", &v1), ("section", &v2), ("knowledge", &v3)]
            .iter()
            .enumerate()
        {
            conn.execute(
                "INSERT INTO embeddings \
                 (source_type, source_id, provider, model, dimensions, vector) \
                 VALUES (?, ?, 'test', 'test', 3, ?)",
                rusqlite::params![st, (i + 1) as i64, blob as &[u8]],
            )
            .unwrap();
        }

        let query = vec![1.0f32, 0.0, 0.0];
        let results = vector_search_stored(&conn, &query, None, 10).unwrap();

        assert!(!results.is_empty(), "expected at least one result");
        let (_, _, top_score) = &results[0];
        assert!(
            *top_score > 0.99,
            "top result should be near-identical, got {top_score}"
        );
    }

    #[test]
    fn vector_search_filter_by_source_type() {
        let conn = rusqlite::Connection::open_in_memory().unwrap();
        ensure_embedding_tables(&conn).unwrap();

        let v = serialize_vector(&[1.0f32, 0.0]);
        conn.execute(
            "INSERT INTO embeddings \
             (source_type, source_id, provider, model, dimensions, vector) \
             VALUES ('section', 1, 'test', 'test', 2, ?)",
            rusqlite::params![&v as &[u8]],
        )
        .unwrap();
        conn.execute(
            "INSERT INTO embeddings \
             (source_type, source_id, provider, model, dimensions, vector) \
             VALUES ('knowledge', 2, 'test', 'test', 2, ?)",
            rusqlite::params![&v as &[u8]],
        )
        .unwrap();

        let query = vec![1.0f32, 0.0];
        let sec_results = vector_search_stored(&conn, &query, Some("section"), 10).unwrap();
        let ke_results = vector_search_stored(&conn, &query, Some("knowledge"), 10).unwrap();

        assert_eq!(sec_results.len(), 1);
        assert_eq!(sec_results[0].0, "section");
        assert_eq!(ke_results.len(), 1);
        assert_eq!(ke_results[0].0, "knowledge");
    }

    #[test]
    fn vector_search_empty_db_returns_empty() {
        let conn = rusqlite::Connection::open_in_memory().unwrap();
        ensure_embedding_tables(&conn).unwrap();
        let query = vec![1.0f32, 0.0];
        let results = vector_search_stored(&conn, &query, None, 10).unwrap();
        assert!(results.is_empty());
    }

    // ── store_batch_embeddings ──

    #[test]
    fn store_batch_embeddings_inserts_rows() {
        let conn = rusqlite::Connection::open_in_memory().unwrap();
        ensure_embedding_tables(&conn).unwrap();

        let items = vec![
            (1i64, vec![0.1f32, 0.2, 0.3], "preview one".to_string()),
            (2i64, vec![0.4f32, 0.5, 0.6], "preview two".to_string()),
        ];
        store_batch_embeddings(&conn, "section", &items, "test-provider", "test-model", 3).unwrap();

        let count: i64 = conn
            .query_row("SELECT COUNT(*) FROM embeddings", [], |r| r.get(0))
            .unwrap();
        assert_eq!(count, 2);
    }

    #[test]
    fn store_batch_embeddings_upserts_on_conflict() {
        let conn = rusqlite::Connection::open_in_memory().unwrap();
        ensure_embedding_tables(&conn).unwrap();

        let items = vec![(1i64, vec![0.1f32, 0.2], "old".to_string())];
        store_batch_embeddings(&conn, "section", &items, "p1", "m1", 2).unwrap();

        // Same source_type + source_id → should upsert
        let items2 = vec![(1i64, vec![0.9f32, 0.8], "new".to_string())];
        store_batch_embeddings(&conn, "section", &items2, "p2", "m2", 2).unwrap();

        let count: i64 = conn
            .query_row("SELECT COUNT(*) FROM embeddings", [], |r| r.get(0))
            .unwrap();
        assert_eq!(count, 1, "upsert should replace, not duplicate");

        let provider: String = conn
            .query_row(
                "SELECT provider FROM embeddings WHERE source_id=1",
                [],
                |r| r.get(0),
            )
            .unwrap();
        assert_eq!(provider, "p2", "provider should be updated on upsert");
    }

    #[test]
    fn store_batch_embeddings_writes_embedding_meta() {
        let conn = rusqlite::Connection::open_in_memory().unwrap();
        ensure_embedding_tables(&conn).unwrap();

        let items = vec![(1i64, vec![0.1f32], "p".to_string())];
        store_batch_embeddings(&conn, "section", &items, "p", "m", 1).unwrap();

        let val: String = conn
            .query_row(
                "SELECT value FROM embedding_meta WHERE key='last_build'",
                [],
                |r| r.get(0),
            )
            .unwrap();
        assert!(!val.is_empty(), "last_build metadata should be written");
    }

    // ── store_tfidf_model ──

    #[test]
    fn store_tfidf_model_writes_and_retrieves_blob() {
        let conn = rusqlite::Connection::open_in_memory().unwrap();
        ensure_embedding_tables(&conn).unwrap();

        let blob = b"test-model-blob";
        store_tfidf_model(&conn, blob, 42).unwrap();

        let (stored_blob, doc_count): (Vec<u8>, i64) = conn
            .query_row(
                "SELECT model_blob, doc_count FROM tfidf_model WHERE id=1",
                [],
                |r| Ok((r.get(0)?, r.get(1)?)),
            )
            .unwrap();
        assert_eq!(stored_blob, blob);
        assert_eq!(doc_count, 42);
    }

    #[test]
    fn store_tfidf_model_replaces_on_second_call() {
        let conn = rusqlite::Connection::open_in_memory().unwrap();
        ensure_embedding_tables(&conn).unwrap();

        store_tfidf_model(&conn, b"first", 10).unwrap();
        store_tfidf_model(&conn, b"second", 20).unwrap();

        let count: i64 = conn
            .query_row("SELECT COUNT(*) FROM tfidf_model", [], |r| r.get(0))
            .unwrap();
        assert_eq!(count, 1, "only one tfidf_model row expected");

        let doc_count: i64 = conn
            .query_row("SELECT doc_count FROM tfidf_model WHERE id=1", [], |r| {
                r.get(0)
            })
            .unwrap();
        assert_eq!(doc_count, 20, "doc_count should reflect the latest store");
    }
}
