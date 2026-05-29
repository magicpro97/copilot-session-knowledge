use std::collections::HashSet;
use std::process::ExitCode;

use crate::db::connection::KnowledgeDb;
use crate::db::fts::{hybrid_search_ke, rrf_k_from_env, sanitize_fts_query, RankMode, ScoredEntry};

type DetailRow = (
    i64,
    String,
    String,
    String,
    String,
    f64,
    i64,
    String,
    String,
    String,
    String,
);
type FtsRow = (i64, String, String, String, String, f64, String, String);
type LikeRow = (i64, String, String, String, String);

#[derive(Debug)]
struct SessionHistoryRow {
    source: String,
    title: String,
    snippet: String,
    session_id: String,
}

/// Entry point called from main's dispatch for the `query` command.
pub fn run_query_command(args: &[String]) -> ExitCode {
    let params = parse_query_args(args);

    let db = match KnowledgeDb::open() {
        Ok(db) => db,
        Err(e) => {
            eprintln!("sk query: cannot open knowledge.db: {e}");
            return ExitCode::from(1);
        }
    };

    if params.show_wings {
        return show_wings(&db);
    }

    if params.show_rooms {
        return show_rooms(&db, params.wing_filter.as_deref());
    }

    if let Some(id) = params.detail_id {
        return show_detail(&db, id);
    }

    if let Some(cat) = params.category_filter.as_deref() {
        return show_by_category(
            &db,
            cat,
            params.limit,
            params.verbose,
            params.wing_filter.as_deref(),
            params.room_filter.as_deref(),
        );
    }

    if !params.search_terms.is_empty() {
        return search_fts_cmd(
            &db,
            &params.search_terms,
            params.limit,
            params.verbose,
            params.wing_filter.as_deref(),
            params.room_filter.as_deref(),
            params.rank_mode,
        );
    }

    // No args: show recent entries across all categories
    show_recent_all(&db, params.limit)
}

struct QueryParams {
    search_terms: String,
    category_filter: Option<String>,
    show_wings: bool,
    show_rooms: bool,
    detail_id: Option<i64>,
    limit: usize,
    verbose: bool,
    wing_filter: Option<String>,
    room_filter: Option<String>,
    rank_mode: RankMode,
}

fn parse_query_args(args: &[String]) -> QueryParams {
    let mut search_terms = String::new();
    let mut category_filter: Option<String> = None;
    let mut show_wings = false;
    let mut show_rooms = false;
    let mut detail_id: Option<i64> = None;
    let mut limit = 10usize;
    let mut verbose = false;
    let mut wing_filter: Option<String> = None;
    let mut room_filter: Option<String> = None;
    let mut rank_mode = RankMode::Hybrid;

    let mut i = 0;
    while i < args.len() {
        match args[i].as_str() {
            "--mistakes" => {
                category_filter = Some("mistake".to_string());
                i += 1;
            }
            "--patterns" => {
                category_filter = Some("pattern".to_string());
                i += 1;
            }
            "--decisions" => {
                category_filter = Some("decision".to_string());
                i += 1;
            }
            "--tools" => {
                category_filter = Some("tool".to_string());
                i += 1;
            }
            "--wings" => {
                show_wings = true;
                i += 1;
            }
            "--rooms" => {
                show_rooms = true;
                // Optional wing argument after --rooms
                if let Some(next) = args.get(i + 1) {
                    if !next.starts_with('-') {
                        wing_filter = Some(next.clone());
                        i += 2;
                        continue;
                    }
                }
                i += 1;
            }
            "--detail" => {
                if let Some(v) = args.get(i + 1) {
                    detail_id = v.parse().ok();
                }
                i += 2;
            }
            "--limit" => {
                if let Some(v) = args.get(i + 1) {
                    limit = v.parse().unwrap_or(10);
                }
                i += 2;
            }
            "--verbose" => {
                verbose = true;
                i += 1;
            }
            "--wing" => {
                wing_filter = args.get(i + 1).cloned();
                i += 2;
            }
            "--room" => {
                room_filter = args.get(i + 1).cloned();
                i += 2;
            }
            "--rank" => {
                if let Some(v) = args.get(i + 1) {
                    rank_mode = RankMode::from_str(v);
                }
                i += 2;
            }
            s if !s.starts_with('-') => {
                if search_terms.is_empty() {
                    search_terms = s.to_string();
                } else {
                    search_terms = format!("{} {}", search_terms, s);
                }
                i += 1;
            }
            _ => {
                i += 1;
            }
        }
    }

    QueryParams {
        search_terms,
        category_filter,
        show_wings,
        show_rooms,
        detail_id,
        limit,
        verbose,
        wing_filter,
        room_filter,
        rank_mode,
    }
}

/// List all wings with entry counts. Matches Python list_wings().
fn show_wings(db: &KnowledgeDb) -> ExitCode {
    let mut stmt = match db.conn.prepare(
        "SELECT wing, COUNT(*) as cnt \
         FROM knowledge_entries \
         WHERE wing != '' AND wing IS NOT NULL \
         GROUP BY wing \
         ORDER BY cnt DESC",
    ) {
        Ok(s) => s,
        Err(e) => {
            eprintln!("sk query: {e}");
            return ExitCode::from(1);
        }
    };

    let rows: Vec<(String, i64)> = stmt
        .query_map([], |row| Ok((row.get(0)?, row.get(1)?)))
        .map(|mapped| mapped.filter_map(|r| r.ok()).collect())
        .unwrap_or_default();

    if rows.is_empty() {
        println!("No wings found. Use learn --wing to categorize entries.");
        return ExitCode::SUCCESS;
    }

    println!("\nWings");
    println!("{}", "=".repeat(40));
    let total: i64 = rows.iter().map(|(_, c)| c).sum();
    for (wing, cnt) in &rows {
        let bar_len = (*cnt as usize / 5).min(30);
        let bar = "█".repeat(bar_len);
        println!("  {:<20} {:>4}  {}", wing, cnt, bar);
    }
    println!("\n  {:<20} {:>4}", "Total", total);
    ExitCode::SUCCESS
}

/// List rooms, optionally filtered by wing. Matches Python list_rooms().
fn show_rooms(db: &KnowledgeDb, wing: Option<&str>) -> ExitCode {
    let rows: Vec<(String, String, i64)> = if let Some(w) = wing {
        let mut stmt = match db.conn.prepare(
            "SELECT room, '', COUNT(*) as cnt \
             FROM knowledge_entries \
             WHERE room != '' AND room IS NOT NULL AND wing = ? \
             GROUP BY room \
             ORDER BY cnt DESC",
        ) {
            Ok(s) => s,
            Err(e) => {
                eprintln!("sk query: {e}");
                return ExitCode::from(1);
            }
        };
        stmt.query_map(rusqlite::params![w], |row| {
            Ok((row.get(0)?, row.get(1)?, row.get(2)?))
        })
        .map(|mapped| mapped.filter_map(|x| x.ok()).collect())
        .unwrap_or_default()
    } else {
        let mut stmt = match db.conn.prepare(
            "SELECT room, COALESCE(wing,''), COUNT(*) as cnt \
             FROM knowledge_entries \
             WHERE room != '' AND room IS NOT NULL \
             GROUP BY room, wing \
             ORDER BY cnt DESC",
        ) {
            Ok(s) => s,
            Err(e) => {
                eprintln!("sk query: {e}");
                return ExitCode::from(1);
            }
        };
        stmt.query_map([], |row| Ok((row.get(0)?, row.get(1)?, row.get(2)?)))
            .map(|mapped| mapped.filter_map(|x| x.ok()).collect())
            .unwrap_or_default()
    };

    if rows.is_empty() {
        let suffix = wing.map(|w| format!(" in wing '{w}'")).unwrap_or_default();
        println!("No rooms found{suffix}.");
        return ExitCode::SUCCESS;
    }

    let title = wing
        .map(|w| format!("Rooms in '{w}'"))
        .unwrap_or_else(|| "All Rooms".to_string());
    println!("\n{title}");
    println!("{}", "=".repeat(50));
    for (room, w, cnt) in &rows {
        let wing_label = if wing.is_none() && !w.is_empty() {
            format!(" [{w}]")
        } else {
            String::new()
        };
        let bar_len = (*cnt as usize / 3).min(30);
        let bar = "█".repeat(bar_len);
        println!("  {:<20}{:<12} {:>4}  {}", room, wing_label, cnt, bar);
    }
    ExitCode::SUCCESS
}

/// Show full detail of an entry by ID. Matches Python show_detail().
fn show_detail(db: &KnowledgeDb, entry_id: i64) -> ExitCode {
    let row: Option<DetailRow> = db
        .conn
        .query_row(
            "SELECT id, category, title, content, COALESCE(tags,''), confidence, occurrence_count, \
                    COALESCE(session_id,''), COALESCE(wing,''), COALESCE(room,''), \
                    COALESCE(first_seen,'') \
             FROM knowledge_entries WHERE id = ?",
            rusqlite::params![entry_id],
            |row| {
                Ok((
                    row.get(0)?,
                    row.get(1)?,
                    row.get(2)?,
                    row.get(3)?,
                    row.get(4)?,
                    row.get::<_, f64>(5).unwrap_or(0.0),
                    row.get(6)?,
                    row.get(7)?,
                    row.get(8)?,
                    row.get(9)?,
                    row.get(10)?,
                ))
            },
        )
        .ok();

    match row {
        None => {
            println!("No knowledge entry with ID {entry_id}");
            ExitCode::from(1)
        }
        Some((id, cat, title, content, tags, conf, count, session_id, wing, room, first_seen)) => {
            let sid = if session_id.len() >= 12 {
                format!("{}...", &session_id[..12])
            } else {
                session_id.clone()
            };
            println!("\nKnowledge Entry #{id}");
            println!("{}", "=".repeat(60));
            println!("Category:   {}", cat.to_uppercase());
            println!("Title:      {title}");
            println!("Session:    {sid}");
            println!("Confidence: {conf:.2}  Occurrences: {count}");
            if !tags.is_empty() {
                println!("Tags:       {tags}");
            }
            if !wing.is_empty() || !room.is_empty() {
                println!("Location:   [{wing}/{room}]");
            }
            if !first_seen.is_empty() {
                println!("First seen: {first_seen}");
            }
            println!("\nContent:");
            println!("{}", "-".repeat(60));
            println!("{content}");
            println!("{}", "-".repeat(60));
            ExitCode::SUCCESS
        }
    }
}

/// Show entries filtered by category. Matches Python show_knowledge().
fn show_by_category(
    db: &KnowledgeDb,
    category: &str,
    limit: usize,
    verbose: bool,
    wing: Option<&str>,
    room: Option<&str>,
) -> ExitCode {
    let entries = if wing.is_some() || room.is_some() {
        crate::db::fts::search_by_wing_room(&db.conn, wing, room, category, limit)
    } else {
        crate::db::fts::search_top_by_category(&db.conn, category, limit, 0.0)
    };

    if entries.is_empty() {
        println!("No {category} entries found.");
        return ExitCode::SUCCESS;
    }

    println!(
        "\n{} entries ({} results)\n",
        category.to_uppercase(),
        entries.len()
    );
    for e in entries.iter() {
        let conf = format!("{:.1}", e.confidence);
        println!("  #{:>4} {} [conf:{}]", e.id, truncate(&e.title, 60), conf);
        if verbose && !e.content.is_empty() {
            let first_line = e
                .content
                .lines()
                .next()
                .unwrap_or("")
                .chars()
                .take(80)
                .collect::<String>();
            println!("        {first_line}");
        }
    }
    if !verbose {
        println!("\nUse --detail <id> for full content, --verbose for expanded view");
    }
    ExitCode::SUCCESS
}

/// Full-text search across knowledge entries — routes to hybrid or legacy FTS.
fn search_fts_cmd(
    db: &KnowledgeDb,
    query: &str,
    limit: usize,
    verbose: bool,
    wing: Option<&str>,
    room: Option<&str>,
    rank_mode: RankMode,
) -> ExitCode {
    if rank_mode != RankMode::Fts {
        let scored = hybrid_search_ke(&db.conn, query, None, limit);
        if scored.is_empty() {
            return search_fts_cmd_legacy(db, query, limit, verbose, wing, room);
        }
        print_hybrid_rows(query, &scored, verbose);
        return ExitCode::SUCCESS;
    }
    search_fts_cmd_legacy(db, query, limit, verbose, wing, room)
}

/// Legacy BM25-only FTS path (pre-§611).
fn search_fts_cmd_legacy(
    db: &KnowledgeDb,
    query: &str,
    limit: usize,
    verbose: bool,
    wing: Option<&str>,
    room: Option<&str>,
) -> ExitCode {
    let fts_query = sanitize_fts_query(query);
    let mut rows = match search_knowledge_rows(db, &fts_query, limit, wing, room) {
        Ok(rows) => rows,
        Err(e) => {
            eprintln!("sk query: FTS search error: {e}");
            return ExitCode::from(1);
        }
    };

    if rows.is_empty() {
        if let Some(expanded_query) = expanded_fts_query(query) {
            if expanded_query != fts_query {
                rows = match search_knowledge_rows(db, &expanded_query, limit, wing, room) {
                    Ok(rows) => rows,
                    Err(e) => {
                        eprintln!("sk query: expanded FTS search error: {e}");
                        return ExitCode::from(1);
                    }
                };
                rows = filter_expanded_knowledge_rows(rows, query);
                if !rows.is_empty() {
                    println!("(No exact FTS match — showing expanded knowledge matches)");
                }
            }
        }
    }

    if rows.is_empty() {
        return search_like_fallback(db, query, limit, verbose);
    }

    print_knowledge_rows(query, &rows, verbose);
    ExitCode::SUCCESS
}

/// Print hybrid search results; includes RRF scores when --verbose is set.
fn print_hybrid_rows(query: &str, scored: &[ScoredEntry], verbose: bool) {
    println!("Knowledge entries matching '{query}':\n");
    for se in scored {
        let e = &se.entry;
        let cat_abbr = match e.tags.as_str() {
            t if t.contains("mistake") => "M",
            t if t.contains("pattern") => "P",
            t if t.contains("decision") => "D",
            _ => "K",
        };
        println!(
            "[{}] #{} {} — {}",
            cat_abbr,
            e.id,
            e.title,
            e.content.lines().next().unwrap_or("")
        );
        if verbose {
            let bm = se.bm25_rank.map_or("-".to_string(), |r| r.to_string());
            let td = se.tfidf_rank.map_or("-".to_string(), |r| r.to_string());
            println!(
                "    rrf={:.4} bm25_rank={} tfidf_rank={}",
                se.rrf_score, bm, td
            );
        }
    }
    let k = rrf_k_from_env();
    println!("\nHybrid FTS+TF-IDF+RRF (k={k:.0}). Use --rank fts for legacy BM25-only.");
}

fn search_knowledge_rows(
    db: &KnowledgeDb,
    fts_query: &str,
    limit: usize,
    wing: Option<&str>,
    room: Option<&str>,
) -> rusqlite::Result<Vec<FtsRow>> {
    let sql = "SELECT ke.id, ke.category, ke.title, ke.content, COALESCE(ke.tags,'') as tags, \
               ke.confidence, COALESCE(ke.wing,'') as wing, COALESCE(ke.room,'') as room \
               FROM ke_fts fts \
               JOIN knowledge_entries ke ON fts.rowid = ke.id \
               WHERE ke_fts MATCH ? \
               ORDER BY rank \
               LIMIT ?";

    let mut stmt = db.conn.prepare(sql)?;

    let rows: Vec<FtsRow> = stmt
        .query_map(rusqlite::params![fts_query, limit as i64], |row| {
            Ok((
                row.get(0)?,
                row.get(1)?,
                row.get(2)?,
                row.get(3)?,
                row.get(4)?,
                row.get::<_, f64>(5).unwrap_or(0.0),
                row.get(6)?,
                row.get(7)?,
            ))
        })?
        .collect::<rusqlite::Result<Vec<_>>>()?;

    Ok(rows
        .into_iter()
        .filter(|(_, _, _, _, _, _, w, r)| {
            wing.map_or(true, |wf| w == wf) && room.map_or(true, |rf| r == rf)
        })
        .collect())
}

fn print_knowledge_rows(query: &str, rows: &[FtsRow], verbose: bool) {
    println!(
        "\nKnowledge entries matching: {query} ({} results)\n",
        rows.len()
    );
    for (i, (_id, cat, title, content, tags, _conf, _w, _r)) in rows.iter().enumerate() {
        println!("{}. [{}] {}", i + 1, cat, title);
        println!("   Tags: {tags}");
        if verbose && !content.is_empty() {
            let preview = content.chars().take(300).collect::<String>();
            println!("   {}", preview.replace('\n', "\n   "));
        } else {
            let preview = preview_content_line(content, query, 100);
            if !preview.is_empty() {
                println!("   {preview}");
            }
        }
        println!();
    }
}

fn preview_content_line(content: &str, query: &str, max: usize) -> String {
    let terms = expanded_query_terms(query);

    for line in content.lines() {
        let lower = line.to_lowercase();
        if terms
            .iter()
            .any(|term| text_contains_query_term(&lower, &term.to_lowercase()))
        {
            return truncate(line.trim(), max);
        }
    }

    content
        .lines()
        .next()
        .map(|line| truncate(line.trim(), max))
        .unwrap_or_default()
}

fn filter_expanded_knowledge_rows(rows: Vec<FtsRow>, query: &str) -> Vec<FtsRow> {
    let terms = expanded_query_terms(query);
    rows.into_iter()
        .filter(|row| should_keep_expanded_knowledge_row(row, &terms, query))
        .collect()
}

fn should_keep_expanded_knowledge_row(row: &FtsRow, terms: &[String], query: &str) -> bool {
    if is_retrieval_meta_row(row) && !query_is_about_session_knowledge(query) {
        return false;
    }

    let min_matches = if terms.len() <= 2 { 1 } else { 2 };
    count_row_term_matches(row, terms) >= min_matches
}

fn is_retrieval_meta_row(row: &FtsRow) -> bool {
    let text = format!("{} {} {}", row.2, row.3, row.4).to_lowercase();
    text.contains("sk query misses")
        || (text.contains("query-expansion")
            && (text.contains("session-store") || text.contains("knowledge-db")))
}

fn query_is_about_session_knowledge(query: &str) -> bool {
    let lower = query.to_lowercase();
    lower.contains("sk")
        || lower.contains("knowledge")
        || lower.contains("session")
        || lower.contains("recall")
        || lower.contains("query-expansion")
}

fn count_row_term_matches(row: &FtsRow, terms: &[String]) -> usize {
    let text = format!("{} {} {}", row.2, row.3, row.4).to_lowercase();
    let mut seen_terms = Vec::new();
    let mut count = 0;
    for term in terms {
        let lower = term.to_lowercase();
        if seen_terms.iter().any(|seen| seen == &lower) {
            continue;
        }
        seen_terms.push(lower.clone());
        if text_contains_query_term(&text, &lower) {
            count += 1;
        }
    }
    count
}

fn text_contains_query_term(text_lower: &str, term_lower: &str) -> bool {
    if term_lower.chars().count() <= 4 {
        if term_lower.chars().any(|c| c == '_' || c == '-') {
            return text_lower.contains(term_lower);
        }
        text_lower
            .split(|c: char| !c.is_alphanumeric())
            .any(|token| token == term_lower)
    } else {
        text_lower.contains(term_lower)
    }
}

/// LIKE fallback when FTS returns nothing.
fn search_like_fallback(db: &KnowledgeDb, query: &str, limit: usize, verbose: bool) -> ExitCode {
    let rows = match search_like_rows(db, query, limit) {
        Ok(rows) => rows,
        Err(e) => {
            eprintln!("sk query: substring search error: {e}");
            return ExitCode::from(1);
        }
    };

    if rows.is_empty() {
        let session_rows = expanded_fts_query(query)
            .map(|expanded_query| search_session_history_rows(db, &expanded_query, limit))
            .unwrap_or_default();
        if !session_rows.is_empty() {
            println!("(Learned knowledge returned 0 — showing session history fallback)");
            print_session_history_rows(query, &session_rows, verbose);
            return ExitCode::SUCCESS;
        }

        println!("No results for: {query}");
        println!("Tip: Try broader terms or check --wings/--rooms for available categories.");
        return ExitCode::SUCCESS;
    }

    println!("(FTS returned 0 — showing substring matches)");
    println!(
        "\nKnowledge entries matching: {query} ({} results)\n",
        rows.len()
    );
    for (i, (_id, cat, title, content, tags)) in rows.iter().enumerate() {
        println!("{}. [{}] {}", i + 1, cat, title);
        println!("   Tags: {tags}");
        if verbose && !content.is_empty() {
            let preview = content.chars().take(300).collect::<String>();
            println!("   {}", preview.replace('\n', "\n   "));
        } else {
            let preview = preview_content_line(content, query, 100);
            if !preview.is_empty() {
                println!("   {preview}");
            }
        }
        println!();
    }
    ExitCode::SUCCESS
}

fn search_like_rows(db: &KnowledgeDb, query: &str, limit: usize) -> rusqlite::Result<Vec<LikeRow>> {
    let pattern = format!("%{}%", query.to_lowercase());
    let sql = "SELECT id, category, title, content, COALESCE(tags,'') \
               FROM knowledge_entries \
               WHERE LOWER(title) LIKE ? OR LOWER(content) LIKE ? \
               ORDER BY confidence DESC \
               LIMIT ?";

    let mut stmt = db.conn.prepare(sql)?;

    let rows = stmt
        .query_map(rusqlite::params![pattern, pattern, limit as i64], |row| {
            Ok((
                row.get(0)?,
                row.get(1)?,
                row.get(2)?,
                row.get(3)?,
                row.get(4)?,
            ))
        })?
        .collect::<rusqlite::Result<Vec<_>>>()?;

    Ok(rows)
}

fn search_session_history_rows(
    db: &KnowledgeDb,
    fts_query: &str,
    limit: usize,
) -> Vec<SessionHistoryRow> {
    let mut rows = Vec::new();
    rows.extend(search_sessions_fts_rows(db, fts_query, limit));
    if rows.len() < limit {
        rows.extend(search_knowledge_fts_rows(
            db,
            fts_query,
            limit.saturating_sub(rows.len()),
        ));
    }
    dedupe_session_history_rows(&mut rows);
    rows.truncate(limit);
    rows
}

fn dedupe_session_history_rows(rows: &mut Vec<SessionHistoryRow>) {
    let mut seen = HashSet::new();
    rows.retain(|row| {
        let key = if row.session_id.trim().is_empty() {
            format!("{}:{}:{}", row.source, row.title, row.snippet)
        } else {
            row.session_id.clone()
        };
        seen.insert(key)
    });
}

fn search_sessions_fts_rows(
    db: &KnowledgeDb,
    fts_query: &str,
    limit: usize,
) -> Vec<SessionHistoryRow> {
    let sql = "SELECT COALESCE(session_id,''), COALESCE(title,''), \
                      snippet(sessions_fts, -1, '[', ']', ' ... ', 18) \
               FROM sessions_fts \
               WHERE sessions_fts MATCH ? \
               ORDER BY rank \
               LIMIT ?";
    let mut stmt = match db.conn.prepare(sql) {
        Ok(s) => s,
        Err(_) => return Vec::new(),
    };
    stmt.query_map(rusqlite::params![fts_query, limit as i64], |row| {
        Ok(SessionHistoryRow {
            source: "session".to_string(),
            session_id: row.get(0)?,
            title: row.get(1)?,
            snippet: row.get(2)?,
        })
    })
    .map(|r| r.filter_map(|x| x.ok()).collect())
    .unwrap_or_default()
}

fn search_knowledge_fts_rows(
    db: &KnowledgeDb,
    fts_query: &str,
    limit: usize,
) -> Vec<SessionHistoryRow> {
    let sql = "SELECT COALESCE(session_id,''), COALESCE(title,''), \
                      snippet(knowledge_fts, -1, '[', ']', ' ... ', 18) \
               FROM knowledge_fts \
               WHERE knowledge_fts MATCH ? \
               ORDER BY rank \
               LIMIT ?";
    let mut stmt = match db.conn.prepare(sql) {
        Ok(s) => s,
        Err(_) => return Vec::new(),
    };
    stmt.query_map(rusqlite::params![fts_query, limit as i64], |row| {
        Ok(SessionHistoryRow {
            source: "indexed-doc".to_string(),
            session_id: row.get(0)?,
            title: row.get(1)?,
            snippet: row.get(2)?,
        })
    })
    .map(|r| r.filter_map(|x| x.ok()).collect())
    .unwrap_or_default()
}

fn print_session_history_rows(query: &str, rows: &[SessionHistoryRow], verbose: bool) {
    println!(
        "\nSession history matching: {query} ({} results)\n",
        rows.len()
    );
    for (i, row) in rows.iter().enumerate() {
        let title = if row.title.trim().is_empty() {
            "(untitled session)"
        } else {
            row.title.trim()
        };
        println!("{}. [{}] {}", i + 1, row.source, truncate(title, 80));
        if !row.session_id.is_empty() {
            println!("   Session: {}", truncate(&row.session_id, 24));
        }
        let snippet = row.snippet.replace('\n', " ");
        if verbose {
            println!("   {}", snippet);
        } else {
            println!("   {}", truncate(&snippet, 160));
        }
        println!();
    }
}

fn expanded_fts_query(query: &str) -> Option<String> {
    let terms = expanded_query_terms(query);
    if terms.is_empty() {
        None
    } else {
        Some(
            terms
                .iter()
                .map(|term| format!("\"{}\"*", term))
                .collect::<Vec<_>>()
                .join(" OR "),
        )
    }
}

fn expanded_query_terms(query: &str) -> Vec<String> {
    let mut terms = fallback_terms(query);

    for term in safe_query_terms(query) {
        for part in split_query_identifier(&term) {
            if !is_query_stopword(&part) {
                push_unique(&mut terms, part);
            }
        }
    }

    terms.truncate(24);
    terms
}

fn fallback_terms(query: &str) -> Vec<String> {
    let mut terms = Vec::new();
    for term in safe_query_terms(query) {
        if !is_query_stopword(&term) {
            push_unique(&mut terms, term);
        }
    }
    terms
}

fn safe_query_terms(query: &str) -> Vec<String> {
    let cleaned: String = query
        .chars()
        .map(|c| {
            if c.is_alphanumeric() || c == '_' || c == '-' {
                c
            } else {
                ' '
            }
        })
        .collect();
    cleaned
        .split_whitespace()
        .filter(|term| term.chars().count() >= 3)
        .map(|term| term.to_string())
        .collect()
}

fn split_query_identifier(term: &str) -> Vec<String> {
    let mut parts = Vec::new();
    let mut current = String::new();
    let mut previous: Option<char> = None;

    for ch in term.chars() {
        if ch == '_' || ch == '-' {
            push_identifier_part(&mut parts, &mut current);
            previous = None;
            continue;
        }

        if let Some(prev) = previous {
            if prev.is_lowercase() && ch.is_uppercase() {
                push_identifier_part(&mut parts, &mut current);
            }
        }

        current.push(ch);
        previous = Some(ch);
    }
    push_identifier_part(&mut parts, &mut current);
    parts
}

fn push_identifier_part(parts: &mut Vec<String>, current: &mut String) {
    if current.chars().count() >= 3 {
        push_unique(parts, current.clone());
    }
    current.clear();
}

fn is_query_stopword(term: &str) -> bool {
    matches!(
        term.to_lowercase().as_str(),
        "and"
            | "the"
            | "for"
            | "not"
            | "with"
            | "source"
            | "truth"
            | "manual"
            | "pattern"
            | "processing"
            | "generated"
            | "data"
    )
}

fn push_unique(terms: &mut Vec<String>, term: String) {
    if !terms
        .iter()
        .any(|existing| existing.eq_ignore_ascii_case(&term))
    {
        terms.push(term);
    }
}

/// Show recent entries across all categories when no search terms given.
fn show_recent_all(db: &KnowledgeDb, limit: usize) -> ExitCode {
    let sql = "SELECT id, category, title, confidence, COALESCE(wing,''), COALESCE(room,'') \
               FROM knowledge_entries \
               ORDER BY last_seen DESC, id DESC \
               LIMIT ?";

    let mut stmt = match db.conn.prepare(sql) {
        Ok(s) => s,
        Err(e) => {
            eprintln!("sk query: {e}");
            return ExitCode::from(1);
        }
    };

    let rows: Vec<(i64, String, String, f64, String, String)> = stmt
        .query_map(rusqlite::params![limit as i64], |row| {
            Ok((
                row.get(0)?,
                row.get(1)?,
                row.get(2)?,
                row.get::<_, f64>(3).unwrap_or(0.0),
                row.get(4)?,
                row.get(5)?,
            ))
        })
        .map(|r| r.filter_map(|x| x.ok()).collect())
        .unwrap_or_default();

    if rows.is_empty() {
        println!("No knowledge entries found.");
        println!("Tip: Use 'sk learn --mistake ...' to add entries.");
        return ExitCode::SUCCESS;
    }

    println!("\nRecent knowledge entries ({} results)\n", rows.len());
    for (id, cat, title, conf, wing, room) in &rows {
        let loc = if !wing.is_empty() || !room.is_empty() {
            format!(" [{wing}/{room}]")
        } else {
            String::new()
        };
        println!(
            "  #{:>4} [{cat}] {} [conf:{:.1}]{}",
            id,
            truncate(title, 55),
            conf,
            loc
        );
    }
    println!("\nUse --mistakes/--patterns/--decisions or search terms to filter");
    ExitCode::SUCCESS
}

fn truncate(s: &str, max: usize) -> String {
    if s.chars().count() <= max {
        s.to_string()
    } else {
        let mut end = 0;
        for (count, c) in s.chars().enumerate() {
            if count >= max.saturating_sub(3) {
                break;
            }
            end += c.len_utf8();
        }
        format!("{}...", &s[..end])
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    fn make_query_db() -> KnowledgeDb {
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
                 room TEXT DEFAULT ''
             );
             CREATE VIRTUAL TABLE ke_fts USING fts5(
                 title, content, tags, category, wing, room, facts
             );
             CREATE VIRTUAL TABLE sessions_fts USING fts5(
                 session_id, title, user_messages, assistant_messages, tool_names
             );
             CREATE VIRTUAL TABLE knowledge_fts USING fts5(
                 title, section_name, content, doc_type, session_id, document_id
             );",
        )
        .unwrap();
        KnowledgeDb { conn }
    }

    fn insert_knowledge(db: &KnowledgeDb, id: i64, title: &str, content: &str) {
        db.conn
            .execute(
                "INSERT INTO knowledge_entries
                 (id, category, title, content, tags, confidence, wing, room)
                 VALUES (?, 'pattern', ?, ?, 'test', 1.0, 'backend', 'patient')",
                rusqlite::params![id, title, content],
            )
            .unwrap();
        db.conn
            .execute(
                "INSERT INTO ke_fts
                 (rowid, title, content, tags, category, wing, room, facts)
                 VALUES (?, ?, ?, 'test', 'pattern', 'backend', 'patient', '')",
                rusqlite::params![id, title, content],
            )
            .unwrap();
    }

    fn insert_indexed_doc(db: &KnowledgeDb, session_id: &str, title: &str, body: &str) {
        db.conn
            .execute(
                "INSERT INTO knowledge_fts
                 (title, section_name, content, doc_type, session_id, document_id)
                 VALUES (?, 'section', ?, 'session', ?, 'doc')",
                rusqlite::params![title, body, session_id],
            )
            .unwrap();
    }

    fn insert_session(db: &KnowledgeDb, session_id: &str, title: &str, body: &str) {
        db.conn
            .execute(
                "INSERT INTO sessions_fts
                 (session_id, title, user_messages, assistant_messages, tool_names)
                 VALUES (?, ?, ?, ?, 'rg view')",
                rusqlite::params![session_id, title, body, body],
            )
            .unwrap();
    }

    #[test]
    fn expanded_query_splits_identifier_terms() {
        let query = expanded_fts_query("userProfile created-at migration").unwrap();
        assert!(query.contains("\"userProfile\"*"), "{query}");
        assert!(query.contains("\"user\"*"), "{query}");
        assert!(query.contains("\"Profile\"*"), "{query}");
        assert!(query.contains("\"created\"*"), "{query}");
        assert!(query.contains(" OR "), "{query}");
    }

    #[test]
    fn expanded_query_keeps_technology_terms() {
        let terms = expanded_query_terms("DynamoDB manual hash repository pattern");
        assert!(terms.iter().any(|term| term == "DynamoDB"), "{terms:?}");
        assert!(terms.iter().any(|term| term == "repository"), "{terms:?}");
    }

    #[test]
    fn expanded_query_keeps_user_terms() {
        let terms = expanded_query_terms("export WebSocket SQS ZIP TSV async");
        assert!(terms.iter().any(|term| term == "WebSocket"), "{terms:?}");
        assert!(terms.iter().any(|term| term == "SQS"), "{terms:?}");
        assert!(terms.iter().any(|term| term == "export"), "{terms:?}");
        assert_eq!(
            terms
                .iter()
                .filter(|term| term.eq_ignore_ascii_case("websocket"))
                .count(),
            1,
            "{terms:?}"
        );
    }

    #[test]
    fn short_terms_match_snake_and_kebab_identifiers() {
        assert!(text_contains_query_term("openapi_dto", "dto"));
        assert!(text_contains_query_term("openapi-dto", "dto"));
        assert!(!text_contains_query_term("metadata", "data"));
        assert!(text_contains_query_term("mongo _id field", "_id"));
        assert!(text_contains_query_term("co-op workflow", "co-op"));
        assert!(!text_contains_query_term("coop workflow", "co-op"));
    }

    #[test]
    fn expanded_filter_hides_retrieval_meta_for_domain_queries() {
        let row = (
            1,
            "discovery".to_string(),
            "sk query misses recoverable session history".to_string(),
            "Benchmark follow-up mentions WebSocket SQS ZIP TSV misses.".to_string(),
            "sk,knowledge-db,session-store,recall,query-expansion".to_string(),
            1.0,
            String::new(),
            String::new(),
        );

        assert!(!should_keep_expanded_knowledge_row(
            &row,
            &expanded_query_terms("export WebSocket SQS ZIP TSV async"),
            "export WebSocket SQS ZIP TSV async"
        ));
    }

    #[test]
    fn expanded_filter_keeps_retrieval_meta_for_sk_queries() {
        let row = (
            1,
            "discovery".to_string(),
            "sk query misses recoverable session history".to_string(),
            "Benchmark follow-up mentions session-store and query-expansion.".to_string(),
            "sk,knowledge-db,session-store,recall,query-expansion".to_string(),
            1.0,
            String::new(),
            String::new(),
        );

        assert!(should_keep_expanded_knowledge_row(
            &row,
            &expanded_query_terms("sk query expansion session history"),
            "sk query expansion session history"
        ));
    }

    #[test]
    fn preview_prefers_query_matching_line() {
        let content = "Generic opening line\nRun cd shared && yarn gen after OpenAPI updates";
        let preview = preview_content_line(content, "OpenAPI DTO yarn gen shared interface", 100);
        assert_eq!(preview, "Run cd shared && yarn gen after OpenAPI updates");
    }

    #[test]
    fn primary_knowledge_search_still_returns_learned_entries() {
        let db = make_query_db();
        insert_knowledge(
            &db,
            1,
            "Box CLI folded Location redirect",
            "box-download.ps1 must parse folded Location redirect output.",
        );

        let rows = search_knowledge_rows(
            &db,
            &sanitize_fts_query("Box CLI folded Location"),
            10,
            None,
            None,
        )
        .unwrap();

        assert_eq!(rows.len(), 1);
        assert_eq!(rows[0].2, "Box CLI folded Location redirect");
    }

    #[test]
    fn knowledge_search_surfaces_prepare_errors() {
        let db = KnowledgeDb {
            conn: rusqlite::Connection::open_in_memory().unwrap(),
        };

        assert!(search_knowledge_rows(&db, "\"missing\"*", 10, None, None).is_err());
        assert!(search_like_rows(&db, "missing", 10).is_err());
    }

    #[test]
    fn session_history_fallback_recovers_unlearned_concepts() {
        let db = make_query_db();
        insert_session(
            &db,
            "session-user-profile",
            "userProfile createdAt investigation",
            "createdAt userProfile migration fallback for a stale cache issue",
        );

        let exact_rows = search_knowledge_rows(
            &db,
            &sanitize_fts_query("userProfile createdAt stale cache migration"),
            10,
            None,
            None,
        )
        .unwrap();
        assert!(exact_rows.is_empty(), "fixture must have no learned rows");

        let expanded = expanded_fts_query("userProfile createdAt stale cache migration")
            .expect("expanded query");
        let rows = search_session_history_rows(&db, &expanded, 5);

        assert_eq!(rows.len(), 1);
        assert_eq!(rows[0].source, "session");
        assert!(rows[0].snippet.contains("userProfile"), "{rows:?}");
    }

    #[test]
    fn session_history_dedupes_same_session_across_indexes() {
        let db = make_query_db();
        insert_session(
            &db,
            "session-user-profile",
            "userProfile createdAt investigation",
            "createdAt userProfile migration fallback",
        );
        insert_indexed_doc(
            &db,
            "session-user-profile",
            "userProfile createdAt investigation",
            "createdAt userProfile migration fallback",
        );

        let expanded = expanded_fts_query("userProfile createdAt migration").unwrap();
        let rows = search_session_history_rows(&db, &expanded, 5);

        assert_eq!(rows.len(), 1, "{rows:?}");
        assert_eq!(rows[0].source, "session");
    }
}
