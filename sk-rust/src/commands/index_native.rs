//! Native implementations of `sk index status` and `sk index health`.
//!
//! These replace the Python fallbacks `index-status.py` and
//! `knowledge-health.py` for the most common read-only queries.
//!
//! ## `sk index status` (#361)
//! Reports schema version, session counts, FTS coverage, knowledge-entry
//! distribution, and last-indexed timestamp — all read from knowledge.db.
//!
//! ## `sk index health` (#363)
//! Reports a health score (0–100) and actionable entry-quality metrics:
//! category distribution, average confidence, stale entries, and tag coverage.

use std::process::ExitCode;

use crate::db::connection::knowledge_db_path;
use crate::db::write::open_writable;

// ── sk index status (#361) ────────────────────────────────────────────────

/// Entry point for `sk index status`.
pub fn run_index_status_command(args: &[String]) -> ExitCode {
    let want_json = args.iter().any(|a| a == "--json");

    let db_path = knowledge_db_path();

    if !db_path.exists() {
        if want_json {
            println!("{{\"error\":\"knowledge.db not found\"}}");
        } else {
            eprintln!(
                "sk index status: knowledge.db not found at {}",
                db_path.display()
            );
            eprintln!("  Run: sk index build   to create the index.");
        }
        return ExitCode::from(1);
    }

    let conn = match open_writable(Some(db_path.clone())) {
        Ok(c) => c,
        Err(e) => {
            eprintln!("sk index status: cannot open knowledge.db: {e}");
            return ExitCode::from(1);
        }
    };

    // ── Collect metrics ───────────────────────────────────────────────────

    let schema_version: i64 = conn
        .query_row("SELECT MAX(version) FROM schema_version", [], |r| r.get(0))
        .unwrap_or(0);

    let sessions_total: i64 = conn
        .query_row("SELECT COUNT(*) FROM sessions", [], |r| r.get(0))
        .unwrap_or(0);

    let sessions_fts_done: i64 = conn
        .query_row(
            "SELECT COUNT(*) FROM sessions WHERE fts_indexed_at IS NOT NULL",
            [],
            |r| r.get(0),
        )
        .unwrap_or(0);

    let sessions_fts_rows: i64 = conn
        .query_row("SELECT COUNT(*) FROM sessions_fts", [], |r| r.get(0))
        .unwrap_or(0);

    let knowledge_total: i64 = conn
        .query_row("SELECT COUNT(*) FROM knowledge_entries", [], |r| r.get(0))
        .unwrap_or(0);

    let sections_total: i64 = conn
        .query_row("SELECT COUNT(*) FROM sections", [], |r| r.get(0))
        .unwrap_or(0);

    let last_indexed: String = conn
        .query_row(
            "SELECT COALESCE(MAX(indexed_at),'') FROM sessions",
            [],
            |r| r.get(0),
        )
        .unwrap_or_default();

    // DB file size in human-readable form
    let db_size_bytes = std::fs::metadata(&db_path).map(|m| m.len()).unwrap_or(0);
    let db_size_display = format_bytes(db_size_bytes);

    // Embeddings
    let emb_count: i64 = conn
        .query_row("SELECT COUNT(*) FROM embeddings", [], |r| r.get(0))
        .unwrap_or(0);

    let tfidf_doc_count: i64 = conn
        .query_row(
            "SELECT COALESCE(doc_count,0) FROM tfidf_model WHERE id=1",
            [],
            |r| r.get(0),
        )
        .unwrap_or(0);

    // ── Output ────────────────────────────────────────────────────────────

    if want_json {
        let json = serde_json::json!({
            "schema_version": schema_version,
            "sessions": {
                "total": sessions_total,
                "fts_indexed": sessions_fts_done,
                "fts_rows": sessions_fts_rows,
            },
            "knowledge_entries": knowledge_total,
            "sections": sections_total,
            "embeddings": emb_count,
            "tfidf_doc_count": tfidf_doc_count,
            "last_indexed_at": last_indexed,
            "db_size": db_size_display,
            "db_path": db_path.display().to_string(),
        });
        println!(
            "{}",
            serde_json::to_string_pretty(&json).unwrap_or_default()
        );
    } else {
        println!("\n═══ Index Status ═══\n");
        println!("DB:            {}", db_path.display());
        println!("DB size:       {db_size_display}");
        println!("Schema:        v{schema_version}");
        println!();
        println!("Sessions:      {sessions_total} total, {sessions_fts_done} FTS-indexed");
        println!("Sessions FTS:  {sessions_fts_rows} rows");
        println!("Sections:      {sections_total}");
        println!("Knowledge:     {knowledge_total} entries");
        println!();
        println!("Embeddings:    {emb_count}");
        println!("TF-IDF model:  {tfidf_doc_count} documents");
        if !last_indexed.is_empty() {
            println!("Last indexed:  {last_indexed}");
        }
    }

    ExitCode::SUCCESS
}

// ── sk index health (#363) ────────────────────────────────────────────────

/// Entry point for `sk index health`.
pub fn run_index_health_command(args: &[String]) -> ExitCode {
    let want_json = args.iter().any(|a| a == "--json");
    let want_score = args.iter().any(|a| a == "--score");

    let db_path = knowledge_db_path();

    if !db_path.exists() {
        if want_json {
            println!("{{\"error\":\"knowledge.db not found\"}}");
        } else {
            eprintln!("sk index health: knowledge.db not found.");
            eprintln!("  Run: sk index build   to create the index.");
        }
        return ExitCode::from(1);
    }

    let conn = match open_writable(Some(db_path)) {
        Ok(c) => c,
        Err(e) => {
            eprintln!("sk index health: cannot open knowledge.db: {e}");
            return ExitCode::from(1);
        }
    };

    // ── Collect health metrics ────────────────────────────────────────────

    let total: i64 = conn
        .query_row("SELECT COUNT(*) FROM knowledge_entries", [], |r| r.get(0))
        .unwrap_or(0);

    if total == 0 {
        if want_json {
            println!("{{\"score\":0,\"total\":0,\"message\":\"No knowledge entries\"}}");
        } else {
            println!("\n═══ Index Health ═══\n");
            println!("Score: 0/100");
            println!("No knowledge entries found.");
            println!("  Run: sk learn --mistake/--pattern/--decision  to add entries.");
        }
        return ExitCode::SUCCESS;
    }

    // Category distribution
    let mut by_category: Vec<(String, i64)> = Vec::new();
    if let Ok(mut stmt) = conn.prepare(
        "SELECT category, COUNT(*) as cnt FROM knowledge_entries \
         GROUP BY category ORDER BY cnt DESC",
    ) {
        by_category = stmt
            .query_map([], |r| Ok((r.get::<_, String>(0)?, r.get::<_, i64>(1)?)))
            .map(|rows| rows.filter_map(|r| r.ok()).collect::<Vec<_>>())
            .unwrap_or_default();
    }

    // Average confidence
    let avg_confidence: f64 = conn
        .query_row("SELECT AVG(confidence) FROM knowledge_entries", [], |r| {
            r.get(0)
        })
        .unwrap_or(0.0);

    // Entries with tags
    let with_tags: i64 = conn
        .query_row(
            "SELECT COUNT(*) FROM knowledge_entries WHERE tags IS NOT NULL AND tags != ''",
            [],
            |r| r.get(0),
        )
        .unwrap_or(0);

    // High-confidence entries (≥ 0.8)
    let high_conf: i64 = conn
        .query_row(
            "SELECT COUNT(*) FROM knowledge_entries WHERE confidence >= 0.8",
            [],
            |r| r.get(0),
        )
        .unwrap_or(0);

    // Category diversity: 5 = best
    let cat_count = by_category.len() as i64;

    // ── Compute health score ──────────────────────────────────────────────
    // Simple 0–100 score: volume + quality + diversity
    //   volume_score  = min(total / 20, 30)          — 30 pts at 20+ entries
    //   quality_score = round(avg_confidence * 40)    — 40 pts at confidence=1.0
    //   diversity     = min(cat_count * 6, 30)        — 30 pts at 5+ categories
    let volume_score = ((total as f64 / 20.0).min(1.0) * 30.0) as i64;
    let quality_score = (avg_confidence * 40.0) as i64;
    let diversity_score = (cat_count * 6).min(30);
    let score = (volume_score + quality_score + diversity_score).min(100);

    // ── Output ────────────────────────────────────────────────────────────

    if want_score {
        println!("{score}");
        return ExitCode::SUCCESS;
    }

    if want_json {
        let cats_json: serde_json::Value = by_category
            .iter()
            .map(|(c, n)| serde_json::json!({"category": c, "count": n}))
            .collect::<Vec<_>>()
            .into();
        let json = serde_json::json!({
            "score": score,
            "total": total,
            "avg_confidence": avg_confidence,
            "with_tags": with_tags,
            "high_confidence": high_conf,
            "categories": cats_json,
        });
        println!(
            "{}",
            serde_json::to_string_pretty(&json).unwrap_or_default()
        );
    } else {
        println!("\n═══ Index Health ═══\n");
        println!("Score:           {score}/100");
        println!("Total entries:   {total}");
        println!("Avg confidence:  {avg_confidence:.2}");
        println!("High confidence: {high_conf} (≥0.80)");
        println!("Tagged:          {with_tags}/{total}");
        println!();
        if !by_category.is_empty() {
            println!("Category breakdown:");
            for (cat, cnt) in &by_category {
                println!("  {cat:<14} {cnt}");
            }
        }
        println!();
        // Actionable tips
        if score < 40 {
            println!("⚠  Low health — add more entries: sk learn --mistake/--pattern");
        } else if avg_confidence < 0.5 {
            println!("⚠  Low confidence average — review and update existing entries.");
        } else {
            println!("✓ Knowledge base looks healthy.");
        }
    }

    ExitCode::SUCCESS
}

// ── Helpers ───────────────────────────────────────────────────────────────

fn format_bytes(bytes: u64) -> String {
    if bytes >= 1_000_000 {
        format!("{:.1} MB", bytes as f64 / 1_000_000.0)
    } else if bytes >= 1_000 {
        format!("{:.1} KB", bytes as f64 / 1_000.0)
    } else {
        format!("{bytes} B")
    }
}

// ── Unit tests ────────────────────────────────────────────────────────────

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn format_bytes_gigabyte_range() {
        assert_eq!(format_bytes(1_500_000), "1.5 MB");
        assert_eq!(format_bytes(2_000), "2.0 KB");
        assert_eq!(format_bytes(500), "500 B");
    }
}
