use rusqlite::Connection;

/// A knowledge entry row returned from queries.
#[derive(Debug)]
pub struct KnowledgeEntry {
    pub id: i64,
    pub title: String,
    pub content: String,
    #[allow(dead_code)]
    pub tags: String,
    pub confidence: f64,
    pub wing: String,
    pub room: String,
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
pub fn search_fts(
    conn: &Connection,
    fts_query: &str,
    category: &str,
    limit: usize,
) -> Vec<KnowledgeEntry> {
    let sql = "
        SELECT ke.id, ke.title, ke.content, ke.tags,
               ke.confidence,
               COALESCE(ke.wing, '') AS wing,
               COALESCE(ke.room, '') AS room
        FROM ke_fts fts
        JOIN knowledge_entries ke ON fts.rowid = ke.id
        WHERE ke_fts MATCH ?
          AND ke.category = ?
        ORDER BY ke.confidence DESC
        LIMIT ?";

    let mut stmt = match conn.prepare(sql) {
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

/// Search knowledge entries filtered by wing and/or room (no FTS).
pub fn search_by_wing_room(
    conn: &Connection,
    wing: Option<&str>,
    room: Option<&str>,
    category: &str,
    limit: usize,
) -> Vec<KnowledgeEntry> {
    match (wing, room) {
        (Some(w), Some(r)) => {
            let sql = "SELECT id, title, content, tags,
                              confidence,
                              COALESCE(wing,'') AS wing,
                              COALESCE(room,'') AS room
                       FROM knowledge_entries
                       WHERE category = ? AND wing = ? AND room = ?
                       ORDER BY confidence DESC, occurrence_count DESC
                       LIMIT ?";
            run_query(conn, sql, rusqlite::params![category, w, r, limit as i64])
        }
        (Some(w), None) => {
            let sql = "SELECT id, title, content, tags,
                              confidence,
                              COALESCE(wing,'') AS wing,
                              COALESCE(room,'') AS room
                       FROM knowledge_entries
                       WHERE category = ? AND wing = ?
                       ORDER BY confidence DESC, occurrence_count DESC
                       LIMIT ?";
            run_query(conn, sql, rusqlite::params![category, w, limit as i64])
        }
        (None, Some(r)) => {
            let sql = "SELECT id, title, content, tags,
                              confidence,
                              COALESCE(wing,'') AS wing,
                              COALESCE(room,'') AS room
                       FROM knowledge_entries
                       WHERE category = ? AND room = ?
                       ORDER BY confidence DESC, occurrence_count DESC
                       LIMIT ?";
            run_query(conn, sql, rusqlite::params![category, r, limit as i64])
        }
        (None, None) => {
            let sql = "SELECT id, title, content, tags,
                              confidence,
                              COALESCE(wing,'') AS wing,
                              COALESCE(room,'') AS room
                       FROM knowledge_entries
                       WHERE category = ?
                       ORDER BY confidence DESC, occurrence_count DESC
                       LIMIT ?";
            run_query(conn, sql, rusqlite::params![category, limit as i64])
        }
    }
}

/// Simple wakeup query: top entries by occurrence + confidence for a category.
pub fn search_top_by_category(
    conn: &Connection,
    category: &str,
    limit: usize,
    min_confidence: f64,
) -> Vec<KnowledgeEntry> {
    let sql = "SELECT id, title, content, tags,
                      confidence,
                      COALESCE(wing,'') AS wing,
                      COALESCE(room,'') AS room
               FROM knowledge_entries
               WHERE category = ? AND confidence >= ?
               ORDER BY occurrence_count DESC, confidence DESC
               LIMIT ?";
    run_query(
        conn,
        sql,
        rusqlite::params![category, min_confidence, limit as i64],
    )
}

/// Most recent entries for a category (for decisions in wakeup).
pub fn search_recent_by_category(
    conn: &Connection,
    category: &str,
    limit: usize,
) -> Vec<KnowledgeEntry> {
    let sql = "SELECT id, title, content, tags,
                      confidence,
                      COALESCE(wing,'') AS wing,
                      COALESCE(room,'') AS room
               FROM knowledge_entries
               WHERE category = ?
               ORDER BY last_seen DESC
               LIMIT ?";
    run_query(conn, sql, rusqlite::params![category, limit as i64])
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
pub fn search_kb_snippet(conn: &Connection, query: &str, limit: usize) -> Vec<String> {
    let fts_query = sanitize_fts_query(query);

    let sql = "SELECT ke.category, ke.title, ke.content, COALESCE(ke.tags, '') \
               FROM ke_fts fts \
               JOIN knowledge_entries ke ON fts.rowid = ke.id \
               WHERE ke_fts MATCH ? \
               ORDER BY rank \
               LIMIT ?";

    let mut stmt = match conn.prepare(sql) {
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
    let sql = "SELECT category, title, content, COALESCE(tags,'') \
               FROM knowledge_entries \
               WHERE LOWER(title) LIKE ? OR LOWER(content) LIKE ? \
               ORDER BY confidence DESC \
               LIMIT ?";

    let mut stmt = match conn.prepare(sql) {
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

#[cfg(test)]
mod tests {
    use super::*;

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
}
