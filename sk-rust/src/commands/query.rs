use std::process::ExitCode;

use crate::db::connection::KnowledgeDb;
use crate::db::fts::sanitize_fts_query;

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

/// Full-text search across knowledge entries. Matches Python search_knowledge().
fn search_fts_cmd(
    db: &KnowledgeDb,
    query: &str,
    limit: usize,
    verbose: bool,
    wing: Option<&str>,
    room: Option<&str>,
) -> ExitCode {
    let fts_query = sanitize_fts_query(query);

    let sql = "SELECT ke.id, ke.category, ke.title, ke.content, COALESCE(ke.tags,'') as tags, \
               ke.confidence, COALESCE(ke.wing,'') as wing, COALESCE(ke.room,'') as room \
               FROM ke_fts fts \
               JOIN knowledge_entries ke ON fts.rowid = ke.id \
               WHERE ke_fts MATCH ? \
               ORDER BY rank \
               LIMIT ?";

    let mut stmt = match db.conn.prepare(sql) {
        Ok(s) => s,
        Err(e) => {
            eprintln!("sk query: FTS prepare error: {e}");
            return ExitCode::from(1);
        }
    };

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
        })
        .map(|r| r.filter_map(|x| x.ok()).collect())
        .unwrap_or_default();

    // Filter by wing/room in memory if needed
    let rows: Vec<_> = rows
        .into_iter()
        .filter(|(_, _, _, _, _, _, w, r)| {
            wing.map_or(true, |wf| w == wf) && room.map_or(true, |rf| r == rf)
        })
        .collect();

    if rows.is_empty() {
        // Fallback: LIKE search
        return search_like_fallback(db, query, limit, verbose);
    }

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
            let first_line = content
                .lines()
                .next()
                .unwrap_or("")
                .chars()
                .take(80)
                .collect::<String>();
            if !first_line.is_empty() {
                println!("   {first_line}");
            }
        }
        println!();
    }
    ExitCode::SUCCESS
}

/// LIKE fallback when FTS returns nothing.
fn search_like_fallback(db: &KnowledgeDb, query: &str, limit: usize, verbose: bool) -> ExitCode {
    let pattern = format!("%{}%", query.to_lowercase());
    let sql = "SELECT id, category, title, content, COALESCE(tags,'') \
               FROM knowledge_entries \
               WHERE LOWER(title) LIKE ? OR LOWER(content) LIKE ? \
               ORDER BY confidence DESC \
               LIMIT ?";

    let mut stmt = match db.conn.prepare(sql) {
        Ok(s) => s,
        Err(_) => {
            println!("No results for: {query}");
            return ExitCode::SUCCESS;
        }
    };

    let rows: Vec<(i64, String, String, String, String)> = stmt
        .query_map(rusqlite::params![pattern, pattern, limit as i64], |row| {
            Ok((
                row.get(0)?,
                row.get(1)?,
                row.get(2)?,
                row.get(3)?,
                row.get(4)?,
            ))
        })
        .map(|r| r.filter_map(|x| x.ok()).collect())
        .unwrap_or_default();

    if rows.is_empty() {
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
        }
        println!();
    }
    ExitCode::SUCCESS
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
