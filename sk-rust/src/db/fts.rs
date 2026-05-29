use std::sync::{Arc, Mutex};

use rusqlite::Connection;

/// A knowledge entry row returned from queries.
#[derive(Debug, Clone)]
#[allow(dead_code)]
pub struct KnowledgeEntry {
    pub id: i64,
    pub title: String,
    pub content: String,
    pub tags: String,
    pub confidence: f64,
    pub wing: String,
    pub room: String,
}

/// Probe whether migration v28 has been applied by checking if the
/// `deleted_at` column exists on `knowledge_entries`.
///
/// Returns `true` when soft-delete is supported; `false` for older schemas.
/// The probe compiles but never executes the query, so it is lightweight.
pub fn has_soft_delete(conn: &Connection) -> bool {
    conn.prepare("SELECT deleted_at FROM knowledge_entries LIMIT 0")
        .is_ok()
}

/// Returns `" AND {col_prefix}deleted_at IS NULL"` when the soft-delete
/// column exists in the schema, or an empty `String` for older DBs.
///
/// Pass `col_prefix = "ke."` for JOIN queries, `""` for direct-table queries.
fn soft_delete_sql(conn: &Connection, col_prefix: &str) -> String {
    if has_soft_delete(conn) {
        format!(" AND {}deleted_at IS NULL", col_prefix)
    } else {
        String::new()
    }
}

/// Sanitize user input for FTS5 MATCH queries.
///
/// Mirrors `_sanitize_fts_query()` from briefing.py exactly:
/// - Strips special chars: `"*(){}:^`
/// - Removes FTS5 operator keywords (OR, AND, NOT, NEAR)
/// - Wraps each remaining term as `"term"*` for prefix matching
pub fn sanitize_fts_query(query: &str) -> String {
    let query = query.trim();
    let query = if query.len() > 500 {
        &query[..500]
    } else {
        query
    };

    let special: &[char] = &['"', '*', '(', ')', '{', '}', ':', '^'];
    let cleaned: String = query
        .chars()
        .map(|c| if special.contains(&c) { ' ' } else { c })
        .collect();

    let operator_words = ["OR", "AND", "NOT", "NEAR"];
    let terms: Vec<&str> = cleaned
        .split_whitespace()
        .filter(|t| {
            let upper = t.to_ascii_uppercase();
            !operator_words.contains(&upper.as_str())
        })
        .collect();

    if terms.is_empty() {
        return "\"\"".to_string();
    }

    terms
        .iter()
        .map(|t| format!("\"{}\"*", t))
        .collect::<Vec<_>>()
        .join(" ")
}

/// Search knowledge entries for a given category using FTS5.
/// Returns entries ordered by confidence DESC.
/// Excludes soft-deleted entries (deleted_at IS NOT NULL) when migration v28
/// has been applied; falls back to no filter on older schemas.
pub fn search_fts(
    conn: &Connection,
    fts_query: &str,
    category: &str,
    limit: usize,
) -> Vec<KnowledgeEntry> {
    let sd = soft_delete_sql(conn, "ke.");
    let sql = format!(
        "SELECT ke.id, ke.title, ke.content, ke.tags,
                ke.confidence,
                COALESCE(ke.wing, '') AS wing,
                COALESCE(ke.room, '') AS room
         FROM ke_fts fts
         JOIN knowledge_entries ke ON fts.rowid = ke.id
         WHERE ke_fts MATCH ?
           AND ke.category = ?{}
         ORDER BY ke.confidence DESC
         LIMIT ?",
        sd
    );

    let mut stmt = match conn.prepare(&sql) {
        Ok(s) => s,
        Err(_) => return vec![],
    };

    let rows = stmt.query_map(
        rusqlite::params![fts_query, category, limit as i64],
        row_to_entry,
    );

    match rows {
        Ok(rows) => rows.filter_map(|r| r.ok()).collect(),
        Err(_) => vec![],
    }
}

/// Search knowledge entries by FTS5 + optional wing/room SQL filter (#378).
///
/// Applies wing and/or room constraints directly in SQL, avoiding the
/// in-memory post-filter used by the old `search_fts` + manual filter path.
/// Falls back to `search_fts` when both `wing` and `room` are `None`.
/// Excludes soft-deleted entries on migrated schemas (see `has_soft_delete`).
pub fn search_fts_filtered(
    conn: &Connection,
    fts_query: &str,
    category: &str,
    wing: Option<&str>,
    room: Option<&str>,
    limit: usize,
) -> Vec<KnowledgeEntry> {
    match (wing, room) {
        (None, None) => search_fts(conn, fts_query, category, limit),
        (Some(w), Some(r)) => {
            let sd = soft_delete_sql(conn, "ke.");
            let sql = format!(
                "SELECT ke.id, ke.title, ke.content, ke.tags,
                        ke.confidence,
                        COALESCE(ke.wing,'') AS wing,
                        COALESCE(ke.room,'') AS room
                 FROM ke_fts fts
                 JOIN knowledge_entries ke ON fts.rowid = ke.id
                 WHERE ke_fts MATCH ?
                   AND ke.category = ?
                   AND ke.wing = ?
                   AND ke.room = ?{}
                 ORDER BY ke.confidence DESC
                 LIMIT ?",
                sd
            );
            let mut stmt = match conn.prepare(&sql) {
                Ok(s) => s,
                Err(_) => return vec![],
            };
            stmt.query_map(
                rusqlite::params![fts_query, category, w, r, limit as i64],
                row_to_entry,
            )
            .map(|rows| rows.filter_map(|r| r.ok()).collect())
            .unwrap_or_default()
        }
        (Some(w), None) => {
            let sd = soft_delete_sql(conn, "ke.");
            let sql = format!(
                "SELECT ke.id, ke.title, ke.content, ke.tags,
                        ke.confidence,
                        COALESCE(ke.wing,'') AS wing,
                        COALESCE(ke.room,'') AS room
                 FROM ke_fts fts
                 JOIN knowledge_entries ke ON fts.rowid = ke.id
                 WHERE ke_fts MATCH ?
                   AND ke.category = ?
                   AND ke.wing = ?{}
                 ORDER BY ke.confidence DESC
                 LIMIT ?",
                sd
            );
            let mut stmt = match conn.prepare(&sql) {
                Ok(s) => s,
                Err(_) => return vec![],
            };
            stmt.query_map(
                rusqlite::params![fts_query, category, w, limit as i64],
                row_to_entry,
            )
            .map(|rows| rows.filter_map(|r| r.ok()).collect())
            .unwrap_or_default()
        }
        (None, Some(r)) => {
            let sd = soft_delete_sql(conn, "ke.");
            let sql = format!(
                "SELECT ke.id, ke.title, ke.content, ke.tags,
                        ke.confidence,
                        COALESCE(ke.wing,'') AS wing,
                        COALESCE(ke.room,'') AS room
                 FROM ke_fts fts
                 JOIN knowledge_entries ke ON fts.rowid = ke.id
                 WHERE ke_fts MATCH ?
                   AND ke.category = ?
                   AND ke.room = ?{}
                 ORDER BY ke.confidence DESC
                 LIMIT ?",
                sd
            );
            let mut stmt = match conn.prepare(&sql) {
                Ok(s) => s,
                Err(_) => return vec![],
            };
            stmt.query_map(
                rusqlite::params![fts_query, category, r, limit as i64],
                row_to_entry,
            )
            .map(|rows| rows.filter_map(|r| r.ok()).collect())
            .unwrap_or_default()
        }
    }
}

/// Search knowledge entries filtered by wing and/or room (no FTS).
/// Excludes soft-deleted entries on migrated schemas.
pub fn search_by_wing_room(
    conn: &Connection,
    wing: Option<&str>,
    room: Option<&str>,
    category: &str,
    limit: usize,
) -> Vec<KnowledgeEntry> {
    let sd = soft_delete_sql(conn, "");
    match (wing, room) {
        (Some(w), Some(r)) => {
            let sql = format!(
                "SELECT id, title, content, tags,
                        confidence,
                        COALESCE(wing,'') AS wing,
                        COALESCE(room,'') AS room
                 FROM knowledge_entries
                 WHERE category = ? AND wing = ? AND room = ?{}
                 ORDER BY confidence DESC, occurrence_count DESC
                 LIMIT ?",
                sd
            );
            run_query(conn, &sql, rusqlite::params![category, w, r, limit as i64])
        }
        (Some(w), None) => {
            let sql = format!(
                "SELECT id, title, content, tags,
                        confidence,
                        COALESCE(wing,'') AS wing,
                        COALESCE(room,'') AS room
                 FROM knowledge_entries
                 WHERE category = ? AND wing = ?{}
                 ORDER BY confidence DESC, occurrence_count DESC
                 LIMIT ?",
                sd
            );
            run_query(conn, &sql, rusqlite::params![category, w, limit as i64])
        }
        (None, Some(r)) => {
            let sql = format!(
                "SELECT id, title, content, tags,
                        confidence,
                        COALESCE(wing,'') AS wing,
                        COALESCE(room,'') AS room
                 FROM knowledge_entries
                 WHERE category = ? AND room = ?{}
                 ORDER BY confidence DESC, occurrence_count DESC
                 LIMIT ?",
                sd
            );
            run_query(conn, &sql, rusqlite::params![category, r, limit as i64])
        }
        (None, None) => {
            let sql = format!(
                "SELECT id, title, content, tags,
                        confidence,
                        COALESCE(wing,'') AS wing,
                        COALESCE(room,'') AS room
                 FROM knowledge_entries
                 WHERE category = ?{}
                 ORDER BY confidence DESC, occurrence_count DESC
                 LIMIT ?",
                sd
            );
            run_query(conn, &sql, rusqlite::params![category, limit as i64])
        }
    }
}

/// Simple wakeup query: top entries by occurrence + confidence for a category.
/// Excludes soft-deleted entries on migrated schemas.
pub fn search_top_by_category(
    conn: &Connection,
    category: &str,
    limit: usize,
    min_confidence: f64,
) -> Vec<KnowledgeEntry> {
    let sd = soft_delete_sql(conn, "");
    let sql = format!(
        "SELECT id, title, content, tags,
                confidence,
                COALESCE(wing,'') AS wing,
                COALESCE(room,'') AS room
         FROM knowledge_entries
         WHERE category = ? AND confidence >= ?{}
         ORDER BY occurrence_count DESC, confidence DESC
         LIMIT ?",
        sd
    );
    run_query(
        conn,
        &sql,
        rusqlite::params![category, min_confidence, limit as i64],
    )
}

/// Most recent entries for a category (for decisions in wakeup).
/// Excludes soft-deleted entries on migrated schemas.
pub fn search_recent_by_category(
    conn: &Connection,
    category: &str,
    limit: usize,
) -> Vec<KnowledgeEntry> {
    let sd = soft_delete_sql(conn, "");
    let sql = format!(
        "SELECT id, title, content, tags,
                confidence,
                COALESCE(wing,'') AS wing,
                COALESCE(room,'') AS room
         FROM knowledge_entries
         WHERE category = ?{}
         ORDER BY last_seen DESC
         LIMIT ?",
        sd
    );
    run_query(conn, &sql, rusqlite::params![category, limit as i64])
}

/// Search knowledge entries across all categories, suitable for hook output.
///
/// Tries FTS5 MATCH first; falls back to LIKE substring search if FTS returns
/// nothing.  Returns at most `limit` formatted snippet lines (numbered list).
/// Each entry formats as:
///   `N. [category] title`
///   `   Tags: <tags>`        (if tags non-empty)
///   `   <first content line>` (truncated to 80 chars)
///
/// Returns an empty vec when the DB has no matching entries.  Never panics.
/// Excludes soft-deleted entries on migrated schemas.
pub fn search_kb_snippet(conn: &Connection, query: &str, limit: usize) -> Vec<String> {
    let fts_query = sanitize_fts_query(query);
    let sd = soft_delete_sql(conn, "ke.");

    let sql = format!(
        "SELECT ke.category, ke.title, ke.content, COALESCE(ke.tags, '') \
         FROM ke_fts fts \
         JOIN knowledge_entries ke ON fts.rowid = ke.id \
         WHERE ke_fts MATCH ?{} \
         ORDER BY rank \
         LIMIT ?",
        sd
    );

    let mut stmt = match conn.prepare(&sql) {
        Ok(s) => s,
        Err(_) => return search_kb_snippet_like(conn, query, limit),
    };

    let rows: Vec<(String, String, String, String)> = stmt
        .query_map(rusqlite::params![fts_query, limit as i64], |row| {
            Ok((row.get(0)?, row.get(1)?, row.get(2)?, row.get(3)?))
        })
        .map(|r| r.filter_map(|x| x.ok()).collect())
        .unwrap_or_default();

    if rows.is_empty() {
        return search_kb_snippet_like(conn, query, limit);
    }

    format_kb_snippet_rows(&rows)
}

fn search_kb_snippet_like(conn: &Connection, query: &str, limit: usize) -> Vec<String> {
    let pattern = format!("%{}%", query.to_lowercase());
    let sd = soft_delete_sql(conn, "");
    // Wrap the OR condition in parentheses before appending the soft-delete
    // AND clause so operator precedence doesn't leak soft-deleted title matches.
    let sql = format!(
        "SELECT category, title, content, COALESCE(tags,'') \
         FROM knowledge_entries \
         WHERE (LOWER(title) LIKE ? OR LOWER(content) LIKE ?){} \
         ORDER BY confidence DESC \
         LIMIT ?",
        sd
    );

    let mut stmt = match conn.prepare(&sql) {
        Ok(s) => s,
        Err(_) => return vec![],
    };

    let rows: Vec<(String, String, String, String)> = stmt
        .query_map(rusqlite::params![pattern, pattern, limit as i64], |row| {
            Ok((row.get(0)?, row.get(1)?, row.get(2)?, row.get(3)?))
        })
        .map(|r| r.filter_map(|x| x.ok()).collect())
        .unwrap_or_default();

    format_kb_snippet_rows(&rows)
}

fn format_kb_snippet_rows(rows: &[(String, String, String, String)]) -> Vec<String> {
    let mut lines = Vec::new();
    for (i, (cat, title, content, tags)) in rows.iter().enumerate() {
        lines.push(format!("{}. [{}] {}", i + 1, cat, title));
        if !tags.is_empty() {
            lines.push(format!("   Tags: {tags}"));
        }
        let first_line = content.lines().next().unwrap_or("").trim();
        if !first_line.is_empty() {
            let preview: String = first_line.chars().take(80).collect();
            lines.push(format!("   {preview}"));
        }
    }
    lines
}

fn run_query(conn: &Connection, sql: &str, params: impl rusqlite::Params) -> Vec<KnowledgeEntry> {
    let mut stmt = match conn.prepare(sql) {
        Ok(s) => s,
        Err(_) => return vec![],
    };
    let rows = stmt.query_map(params, row_to_entry);
    match rows {
        Ok(rows) => rows.filter_map(|r| r.ok()).collect(),
        Err(_) => vec![],
    }
}

fn row_to_entry(row: &rusqlite::Row<'_>) -> rusqlite::Result<KnowledgeEntry> {
    Ok(KnowledgeEntry {
        id: row.get(0)?,
        title: row.get(1)?,
        content: row.get(2)?,
        tags: row.get(3)?,
        confidence: row.get::<_, f64>(4).unwrap_or(0.0),
        wing: row.get(5)?,
        room: row.get(6)?,
    })
}

// ── §611 Hybrid FTS5 + TF-IDF + RRF retrieval ────────────────────────────

use crate::embeddings::tfidf::{build_tfidf_model, TfIdfModel};

/// Ranking mode selectable via `--rank` flag.
#[derive(Debug, Clone, PartialEq, Eq, Default)]
pub enum RankMode {
    #[default]
    Hybrid,
    Fts,
    Tfidf,
}

impl RankMode {
    pub fn from_str(s: &str) -> Self {
        match s.to_lowercase().as_str() {
            "fts" => Self::Fts,
            "tfidf" => Self::Tfidf,
            _ => Self::Hybrid,
        }
    }
}

/// A knowledge entry augmented with retrieval scores.
#[derive(Debug, Clone)]
pub struct ScoredEntry {
    pub entry: KnowledgeEntry,
    pub rrf_score: f64,
    pub bm25_rank: Option<usize>,
    pub tfidf_rank: Option<usize>,
}

struct KeModelCache {
    entry_count: i64,
    model: Arc<TfIdfModel>,
}

static KE_TFIDF_CACHE: Mutex<Option<KeModelCache>> = Mutex::new(None);

/// Read `SK_RRF_K` env var (default 60).
pub fn rrf_k_from_env() -> f64 {
    std::env::var("SK_RRF_K")
        .ok()
        .and_then(|v| v.parse::<f64>().ok())
        .unwrap_or(60.0)
}

/// Return up to `limit` BM25-ranked entry IDs for `query` (optional category).
pub fn search_fts_bm25_ids(
    conn: &Connection,
    query: &str,
    category: Option<&str>,
    limit: usize,
) -> Vec<i64> {
    let sq = sanitize_fts_query(query);
    let has_del = has_soft_delete(conn);
    let del_clause = if has_del {
        "AND ke.deleted_at IS NULL"
    } else {
        ""
    };
    let cat_clause = if category.is_some() {
        "AND ke.category = ?"
    } else {
        ""
    };
    let sql = format!(
        "SELECT ke.id FROM ke_fts f \
         JOIN knowledge_entries ke ON ke.id = f.rowid \
         WHERE ke_fts MATCH ? {del_clause} {cat_clause} \
         ORDER BY rank LIMIT ?"
    );
    let limit_i = limit as i64;
    let ids: Vec<i64> = if let Some(cat) = category {
        let mut st = match conn.prepare(&sql) {
            Ok(s) => s,
            Err(_) => return vec![],
        };
        st.query_map(rusqlite::params![sq, cat, limit_i], |r| r.get(0))
            .map(|rows| rows.filter_map(|r| r.ok()).collect())
            .unwrap_or_default()
    } else {
        let mut st = match conn.prepare(&sql) {
            Ok(s) => s,
            Err(_) => return vec![],
        };
        st.query_map(rusqlite::params![sq, limit_i], |r| r.get(0))
            .map(|rows| rows.filter_map(|r| r.ok()).collect())
            .unwrap_or_default()
    };
    ids
}

/// Fetch full KnowledgeEntry rows for a list of IDs (order preserved).
pub fn fetch_ke_by_ids(conn: &Connection, ids: &[i64]) -> Vec<KnowledgeEntry> {
    if ids.is_empty() {
        return vec![];
    }
    let placeholders = ids.iter().map(|_| "?").collect::<Vec<_>>().join(",");
    let sql = format!(
        "SELECT id, title, content, tags, confidence, wing, room \
         FROM knowledge_entries WHERE id IN ({placeholders})"
    );
    let mut st = match conn.prepare(&sql) {
        Ok(s) => s,
        Err(_) => return vec![],
    };
    let rows: Vec<KnowledgeEntry> = st
        .query_map(rusqlite::params_from_iter(ids.iter()), row_to_entry)
        .map(|rs| rs.filter_map(|r| r.ok()).collect())
        .unwrap_or_default();
    // Restore caller-specified order
    let mut out: Vec<KnowledgeEntry> = Vec::with_capacity(ids.len());
    for &id in ids {
        if let Some(e) = rows.iter().find(|e| e.id == id) {
            out.push(e.clone());
        }
    }
    out
}

/// Get or build the in-memory KE TF-IDF model (cached by entry count).
fn get_or_build_ke_tfidf(conn: &Connection) -> Option<Arc<TfIdfModel>> {
    let count: i64 = conn
        .query_row("SELECT COUNT(*) FROM knowledge_entries", [], |r| r.get(0))
        .unwrap_or(0);
    if count == 0 {
        return None;
    }
    let guard = KE_TFIDF_CACHE.lock().ok()?;
    if let Some(ref c) = *guard {
        if c.entry_count == count {
            return Some(Arc::clone(&c.model));
        }
    }
    drop(guard);
    build_and_cache_ke_tfidf(conn, count)
}

/// Build a TF-IDF model from KE content and store it in the static cache.
fn build_and_cache_ke_tfidf(conn: &Connection, count: i64) -> Option<Arc<TfIdfModel>> {
    let cap = 2000_i64;
    let mut st = conn
        .prepare(
            "SELECT id, title || ' ' || COALESCE(tags,'') || ' ' || content \
             FROM knowledge_entries WHERE deleted_at IS NULL \
             ORDER BY id DESC LIMIT ?",
        )
        .ok()?;
    let rows: Vec<(i64, String)> = st
        .query_map(rusqlite::params![cap], |r| Ok((r.get(0)?, r.get(1)?)))
        .ok()?
        .filter_map(|r| r.ok())
        .collect();
    if rows.is_empty() {
        return None;
    }
    let docs: Vec<&str> = rows.iter().map(|(_, t)| t.as_str()).collect();
    let ids: Vec<i64> = rows.iter().map(|(id, _)| *id).collect();
    let blob = build_tfidf_model(&docs, &ids);
    let model = TfIdfModel::from_json_blob(&blob)?;
    let arc = Arc::new(model);
    let mut guard = KE_TFIDF_CACHE.lock().ok()?;
    *guard = Some(KeModelCache {
        entry_count: count,
        model: Arc::clone(&arc),
    });
    Some(arc)
}

/// Return up to `limit` TF-IDF-ranked entry IDs for `query`.
pub fn search_tfidf_ke_ids(conn: &Connection, query: &str, limit: usize) -> Vec<i64> {
    let model = match get_or_build_ke_tfidf(conn) {
        Some(m) => m,
        None => return vec![],
    };
    model
        .search(query, limit)
        .into_iter()
        .map(|(id, _)| id)
        .collect()
}

/// Merge two ranked ID lists using Reciprocal Rank Fusion.
pub fn rrf_merge_ke(bm25_ids: &[i64], tfidf_ids: &[i64], k: f64) -> Vec<i64> {
    use std::collections::HashMap;
    let mut scores: HashMap<i64, f64> = HashMap::new();
    for (rank, &id) in bm25_ids.iter().enumerate() {
        *scores.entry(id).or_insert(0.0) += 1.0 / (k + (rank + 1) as f64);
    }
    for (rank, &id) in tfidf_ids.iter().enumerate() {
        *scores.entry(id).or_insert(0.0) += 1.0 / (k + (rank + 1) as f64);
    }
    let mut pairs: Vec<(i64, f64)> = scores.into_iter().collect();
    pairs.sort_by(|a, b| b.1.partial_cmp(&a.1).unwrap_or(std::cmp::Ordering::Equal));
    pairs.into_iter().map(|(id, _)| id).collect()
}

/// Build a `ScoredEntry` list from merged IDs plus rank information.
fn build_scored(
    entries: Vec<KnowledgeEntry>,
    bm25_ids: &[i64],
    tfidf_ids: &[i64],
    rrf_ids: &[i64],
    rrf_k: f64,
) -> Vec<ScoredEntry> {
    use std::collections::HashMap;
    let mut rrf_scores: HashMap<i64, f64> = HashMap::new();
    for (rank, &id) in bm25_ids.iter().enumerate() {
        *rrf_scores.entry(id).or_insert(0.0) += 1.0 / (rrf_k + (rank + 1) as f64);
    }
    for (rank, &id) in tfidf_ids.iter().enumerate() {
        *rrf_scores.entry(id).or_insert(0.0) += 1.0 / (rrf_k + (rank + 1) as f64);
    }
    let bm25_pos: HashMap<i64, usize> = bm25_ids
        .iter()
        .enumerate()
        .map(|(i, &id)| (id, i + 1))
        .collect();
    let tfidf_pos: HashMap<i64, usize> = tfidf_ids
        .iter()
        .enumerate()
        .map(|(i, &id)| (id, i + 1))
        .collect();
    // Preserve rrf_ids order
    rrf_ids
        .iter()
        .filter_map(|id| entries.iter().find(|e| &e.id == id))
        .map(|e| ScoredEntry {
            entry: e.clone(),
            rrf_score: *rrf_scores.get(&e.id).unwrap_or(&0.0),
            bm25_rank: bm25_pos.get(&e.id).copied(),
            tfidf_rank: tfidf_pos.get(&e.id).copied(),
        })
        .collect()
}

/// Fuse two ranked ID lists, returning (merged_ids, bm25_ids, tfidf_ids).
fn fuse_ranks(
    conn: &Connection,
    query: &str,
    category: Option<&str>,
    limit: usize,
    rrf_k: f64,
) -> (Vec<i64>, Vec<i64>, Vec<i64>) {
    let bm25 = search_fts_bm25_ids(conn, query, category, 100);
    let tfidf = search_tfidf_ke_ids(conn, query, 100);
    let merged = rrf_merge_ke(&bm25, &tfidf, rrf_k);
    let top: Vec<i64> = merged.into_iter().take(limit).collect();
    (top, bm25, tfidf)
}

/// Main hybrid search entry point. Returns scored entries ranked by RRF.
pub fn hybrid_search_ke(
    conn: &Connection,
    query: &str,
    category: Option<&str>,
    limit: usize,
) -> Vec<ScoredEntry> {
    let rrf_k = rrf_k_from_env();
    let (top_ids, bm25_ids, tfidf_ids) = fuse_ranks(conn, query, category, limit, rrf_k);
    let entries = fetch_ke_by_ids(conn, &top_ids);
    build_scored(entries, &bm25_ids, &tfidf_ids, &top_ids, rrf_k)
}

#[cfg(test)]
mod tests {
    use super::*;

    // ── Sanitizer tests ───────────────────────────────────────────────────

    #[test]
    fn sanitize_removes_operator_keywords() {
        let result = sanitize_fts_query("hello AND world OR NOT test NEAR me");
        // No operator keywords remain; each term is wrapped
        assert!(result.contains("\"hello\"*"));
        assert!(result.contains("\"world\"*"));
        assert!(result.contains("\"test\"*"));
        assert!(result.contains("\"me\"*"));
        // AND / OR / NOT / NEAR must be stripped
        assert!(!result.contains("\"AND\""));
        assert!(!result.contains("\"OR\""));
        assert!(!result.contains("\"NOT\""));
        assert!(!result.contains("\"NEAR\""));
    }

    #[test]
    fn sanitize_removes_special_chars() {
        let result = sanitize_fts_query("rust* (fts5) \"quote\" {brace}:colon^hat");
        // Special chars stripped from input; output uses standard "term"* format
        // All terms from the input appear in the output (with prefix wildcard)
        assert!(result.contains("\"rust\"*"), "rust should appear: {result}");
        assert!(result.contains("\"fts5\"*"), "fts5 should appear: {result}");
        assert!(
            result.contains("\"quote\"*"),
            "quote should appear: {result}"
        );
        // The output should only contain * as part of "term"* patterns, not standalone
        assert!(
            !result.contains(" * "),
            "no standalone * expected: {result}"
        );
    }

    #[test]
    fn sanitize_empty_returns_empty_quoted() {
        assert_eq!(sanitize_fts_query(""), "\"\"");
        assert_eq!(sanitize_fts_query("   "), "\"\"");
        // Query of only operator words also returns empty
        assert_eq!(sanitize_fts_query("AND OR NOT NEAR"), "\"\"");
    }

    #[test]
    fn sanitize_wraps_terms_with_prefix_wildcard() {
        let result = sanitize_fts_query("sqlite fts5");
        assert_eq!(result, "\"sqlite\"* \"fts5\"*");
    }

    #[test]
    fn sanitize_case_insensitive_operators() {
        // Lower-case versions of operators must also be stripped
        let result = sanitize_fts_query("hello and world not rust");
        // 'and' and 'not' should be removed (case-insensitive match)
        assert!(!result.contains("\"and\""));
        assert!(!result.contains("\"not\""));
        assert!(result.contains("\"hello\"*"));
        assert!(result.contains("\"world\"*"));
        assert!(result.contains("\"rust\"*"));
    }

    #[test]
    fn sanitize_truncates_at_500_chars() {
        let long_query = "a ".repeat(300); // 600 chars
        let result = sanitize_fts_query(&long_query);
        // Should still produce valid output without panic
        assert!(!result.is_empty());
    }

    // ── Soft-delete tests ─────────────────────────────────────────────────

    /// Minimal schema with `deleted_at` (migration v28 applied).
    fn make_ke_with_soft_delete() -> rusqlite::Connection {
        let conn = rusqlite::Connection::open_in_memory().unwrap();
        conn.execute_batch(
            "CREATE TABLE knowledge_entries (
                 id INTEGER PRIMARY KEY,
                 category TEXT NOT NULL,
                 title TEXT NOT NULL,
                 content TEXT NOT NULL,
                 tags TEXT DEFAULT '',
                 confidence REAL DEFAULT 1.0,
                 wing TEXT DEFAULT '',
                 room TEXT DEFAULT '',
                 occurrence_count INTEGER DEFAULT 1,
                 last_seen TEXT DEFAULT '',
                 deleted_at TEXT DEFAULT NULL
             );
             CREATE VIRTUAL TABLE ke_fts USING fts5(
                 title, content, tags, category, wing, room, facts
             );",
        )
        .unwrap();
        conn
    }

    /// Minimal schema without `deleted_at` (pre-v28 / old DB).
    fn make_ke_without_soft_delete() -> rusqlite::Connection {
        let conn = rusqlite::Connection::open_in_memory().unwrap();
        conn.execute_batch(
            "CREATE TABLE knowledge_entries (
                 id INTEGER PRIMARY KEY,
                 category TEXT NOT NULL,
                 title TEXT NOT NULL,
                 content TEXT NOT NULL,
                 tags TEXT DEFAULT '',
                 confidence REAL DEFAULT 1.0,
                 wing TEXT DEFAULT '',
                 room TEXT DEFAULT '',
                 occurrence_count INTEGER DEFAULT 1,
                 last_seen TEXT DEFAULT ''
             );
             CREATE VIRTUAL TABLE ke_fts USING fts5(
                 title, content, tags, category, wing, room, facts
             );",
        )
        .unwrap();
        conn
    }

    fn insert_entry(
        conn: &rusqlite::Connection,
        id: i64,
        category: &str,
        title: &str,
        content: &str,
        deleted_at: Option<&str>,
    ) {
        // Insert into ke_fts (FTS virtual table)
        conn.execute(
            "INSERT INTO ke_fts (rowid, title, content, tags, category, wing, room, facts) \
             VALUES (?, ?, ?, '', ?, '', '', '')",
            rusqlite::params![id, title, content, category],
        )
        .unwrap();
        // Insert into knowledge_entries with optional deleted_at
        conn.execute(
            "INSERT INTO knowledge_entries \
             (id, category, title, content, confidence) VALUES (?, ?, ?, ?, 1.0)",
            rusqlite::params![id, category, title, content],
        )
        .unwrap();
        if let Some(ts) = deleted_at {
            conn.execute(
                "UPDATE knowledge_entries SET deleted_at = ? WHERE id = ?",
                rusqlite::params![ts, id],
            )
            .unwrap();
        }
    }

    #[test]
    fn has_soft_delete_detects_column_presence() {
        let with_col = make_ke_with_soft_delete();
        let without_col = make_ke_without_soft_delete();
        assert!(
            has_soft_delete(&with_col),
            "should detect deleted_at column"
        );
        assert!(
            !has_soft_delete(&without_col),
            "should not detect deleted_at on old schema"
        );
    }

    #[test]
    fn search_fts_excludes_soft_deleted_on_v28_schema() {
        let conn = make_ke_with_soft_delete();
        insert_entry(&conn, 1, "mistake", "Active entry", "active content", None);
        insert_entry(
            &conn,
            2,
            "mistake",
            "Deleted entry",
            "deleted content",
            Some("2025-01-01T00:00:00"),
        );

        let results = search_fts(&conn, "\"content\"*", "mistake", 10);
        let titles: Vec<&str> = results.iter().map(|e| e.title.as_str()).collect();

        assert!(
            titles.contains(&"Active entry"),
            "active entry must be returned; got: {titles:?}"
        );
        assert!(
            !titles.contains(&"Deleted entry"),
            "soft-deleted entry must be excluded; got: {titles:?}"
        );
    }

    #[test]
    fn search_fts_works_on_old_schema_without_deleted_at() {
        let conn = make_ke_without_soft_delete();
        insert_entry(
            &conn,
            1,
            "pattern",
            "Old entry",
            "old pattern content",
            None,
        );

        let results = search_fts(&conn, "\"old\"*", "pattern", 10);
        assert_eq!(results.len(), 1, "old schema: query must still return rows");
        assert_eq!(results[0].title, "Old entry");
    }

    #[test]
    fn search_top_by_category_excludes_soft_deleted() {
        let conn = make_ke_with_soft_delete();
        insert_entry(
            &conn,
            1,
            "decision",
            "Keep this",
            "important decision",
            None,
        );
        insert_entry(
            &conn,
            2,
            "decision",
            "Remove this",
            "stale decision",
            Some("2025-06-01T00:00:00"),
        );

        let results = search_top_by_category(&conn, "decision", 10, 0.0);
        let titles: Vec<&str> = results.iter().map(|e| e.title.as_str()).collect();
        assert!(titles.contains(&"Keep this"), "active must appear");
        assert!(!titles.contains(&"Remove this"), "deleted must be excluded");
    }

    #[test]
    fn search_recent_by_category_excludes_soft_deleted() {
        let conn = make_ke_with_soft_delete();
        insert_entry(&conn, 1, "pattern", "Active pattern", "content a", None);
        insert_entry(
            &conn,
            2,
            "pattern",
            "Deleted pattern",
            "content b",
            Some("2025-06-01T00:00:00"),
        );

        let results = search_recent_by_category(&conn, "pattern", 10);
        let titles: Vec<&str> = results.iter().map(|e| e.title.as_str()).collect();
        assert!(titles.contains(&"Active pattern"));
        assert!(!titles.contains(&"Deleted pattern"));
    }

    #[test]
    fn search_by_wing_room_excludes_soft_deleted() {
        let conn = make_ke_with_soft_delete();
        conn.execute(
            "UPDATE knowledge_entries SET wing='backend', room='api' WHERE id=?",
            rusqlite::params![0_i64], // no-op seed
        )
        .ok();
        insert_entry(&conn, 1, "mistake", "Wing active", "wing content", None);
        insert_entry(
            &conn,
            2,
            "mistake",
            "Wing deleted",
            "wing content 2",
            Some("2025-06-01T00:00:00"),
        );
        conn.execute(
            "UPDATE knowledge_entries SET wing='backend', room='api'",
            [],
        )
        .unwrap();

        let results = search_by_wing_room(&conn, Some("backend"), Some("api"), "mistake", 10);
        let titles: Vec<&str> = results.iter().map(|e| e.title.as_str()).collect();
        assert!(titles.contains(&"Wing active"), "active must appear");
        assert!(
            !titles.contains(&"Wing deleted"),
            "deleted must be excluded"
        );
    }

    #[test]
    fn search_kb_snippet_like_excludes_soft_deleted() {
        let conn = make_ke_with_soft_delete();
        insert_entry(
            &conn,
            1,
            "mistake",
            "Visible entry",
            "uniquetoken123 content",
            None,
        );
        insert_entry(
            &conn,
            2,
            "mistake",
            "Hidden entry",
            "uniquetoken123 deleted",
            Some("2025-06-01T00:00:00"),
        );

        let snippets = search_kb_snippet(&conn, "uniquetoken123", 10);
        let combined = snippets.join("\n");
        assert!(
            combined.contains("Visible entry"),
            "active entry must appear in snippets"
        );
        assert!(
            !combined.contains("Hidden entry"),
            "soft-deleted entry must not appear in snippets"
        );
    }

    // ── §611 Hybrid retrieval tests ───────────────────────────────────────

    #[test]
    fn rrf_k_env_var_default_and_custom() {
        // Run sequentially: set custom, verify, remove, verify default
        std::env::remove_var("SK_RRF_K");
        assert!(
            (rrf_k_from_env() - 60.0).abs() < f64::EPSILON,
            "default must be 60.0"
        );
        std::env::set_var("SK_RRF_K", "30");
        assert!(
            (rrf_k_from_env() - 30.0).abs() < f64::EPSILON,
            "must read SK_RRF_K=30"
        );
        std::env::remove_var("SK_RRF_K");
    }

    #[test]
    fn rrf_merge_ke_combines_ranks() {
        // Items in both lists get higher score than items in only one
        let bm25 = vec![1, 2, 3];
        let tfidf = vec![2, 4, 5];
        let merged = rrf_merge_ke(&bm25, &tfidf, 60.0);
        // id=2 appears in both lists — must be ranked first
        assert_eq!(merged[0], 2, "id in both lists should rank first");
        // All 5 unique IDs must appear in merged
        assert_eq!(merged.len(), 5);
    }

    #[test]
    fn rrf_merge_ke_top_wins() {
        // Item ranked #1 in both lists must beat item ranked last in each
        let bm25 = vec![10, 20, 30];
        let tfidf = vec![10, 40, 50];
        let merged = rrf_merge_ke(&bm25, &tfidf, 60.0);
        assert_eq!(merged[0], 10, "top of both lists wins");
    }

    #[test]
    fn fetch_ke_by_ids_preserves_order() {
        let conn = make_ke_with_soft_delete();
        insert_entry(&conn, 10, "cat", "Entry Ten", "content ten", None);
        insert_entry(&conn, 20, "cat", "Entry Twenty", "content twenty", None);
        insert_entry(&conn, 30, "cat", "Entry Thirty", "content thirty", None);

        // Request in reverse DB insertion order
        let ids = vec![30_i64, 10, 20];
        let entries = fetch_ke_by_ids(&conn, &ids);
        assert_eq!(entries.len(), 3);
        assert_eq!(entries[0].id, 30);
        assert_eq!(entries[1].id, 10);
        assert_eq!(entries[2].id, 20);
    }

    #[test]
    fn search_fts_bm25_ids_returns_matching_ids() {
        let conn = make_ke_with_soft_delete();
        insert_entry(&conn, 1, "rust", "Async Rust", "async await content", None);
        insert_entry(&conn, 2, "rust", "Sync Rust", "synchronous content", None);
        insert_entry(&conn, 3, "python", "Python asyncio", "asyncio loop", None);

        let ids = search_fts_bm25_ids(&conn, "async", None, 10);
        assert!(!ids.is_empty(), "should find async entries");
        // ids 1 and 3 both contain 'async'; id=2 does not
        assert!(!ids.contains(&2), "sync-only entry should not match async");
    }

    #[test]
    fn hybrid_search_ke_empty_db_returns_empty() {
        let conn = make_ke_with_soft_delete();
        // No entries seeded — must return empty vec without panic
        let results = hybrid_search_ke(&conn, "anything", None, 10);
        assert!(results.is_empty(), "empty DB should yield empty results");
    }
}
