use std::process::ExitCode;

use crate::db::write::{insert_or_update_entry, open_writable, rebuild_fts, NewEntry};

/// Entry point called from main's dispatch for the `learn` command.
pub fn run_learn_command(args: &[String]) -> ExitCode {
    match parse_learn_args(args) {
        Ok(params) => execute_learn(params),
        Err(msg) => {
            eprintln!("sk learn: {msg}");
            eprintln!("Usage: sk learn --mistake|--pattern|--decision|--discovery|--feature|--refactor|--tool \"Title\" \"Description\"");
            eprintln!("       [--tags \"tag1,tag2\"] [--wing WING] [--room ROOM] [--confidence 0.7] [--fact \"fact\"]");
            ExitCode::from(1)
        }
    }
}

struct LearnParams {
    category: String,
    title: String,
    description: String,
    tags: String,
    wing: String,
    room: String,
    confidence: f64,
    facts: Vec<String>,
}

/// Default confidence per category (mirrors Python learn.py).
fn default_confidence(category: &str) -> f64 {
    match category {
        "decision" => 0.8,
        "tool" => 0.5,
        "refactor" => 0.6,
        "discovery" => 0.6,
        _ => 0.7, // mistake, pattern, feature
    }
}

fn parse_learn_args(args: &[String]) -> Result<LearnParams, String> {
    let mut category: Option<String> = None;
    let mut title: Option<String> = None;
    let mut description = String::new();
    let mut tags = String::new();
    let mut wing = String::new();
    let mut room = String::new();
    let mut confidence: Option<f64> = None;
    let mut facts: Vec<String> = Vec::new();

    let category_flags = [
        "--mistake", "--pattern", "--decision", "--discovery",
        "--feature", "--refactor", "--tool",
    ];

    let mut i = 0;
    while i < args.len() {
        let arg = args[i].as_str();

        // Category flags: --mistake "Title"
        if category_flags.contains(&arg) {
            let cat = arg.trim_start_matches('-').to_string();
            // Title is the next positional argument
            let next = args.get(i + 1).cloned().unwrap_or_default();
            if next.is_empty() || next.starts_with('-') {
                return Err(format!("{arg} requires a title argument"));
            }
            category = Some(cat);
            title = Some(next);
            i += 2;
            continue;
        }

        match arg {
            "--tags" => {
                tags = args.get(i + 1).cloned().unwrap_or_default();
                i += 2;
            }
            "--wing" => {
                wing = args.get(i + 1).cloned().unwrap_or_default();
                i += 2;
            }
            "--room" => {
                room = args.get(i + 1).cloned().unwrap_or_default();
                i += 2;
            }
            "--confidence" => {
                if let Some(v) = args.get(i + 1) {
                    confidence = v.parse().ok();
                }
                i += 2;
            }
            "--fact" => {
                if let Some(v) = args.get(i + 1) {
                    facts.push(v.clone());
                }
                i += 2;
            }
            // Skip flags we don't handle (--skip-gate, --skip-scan, etc.)
            s if s.starts_with("--") => {
                i += 2; // skip flag + value
            }
            // Positional: description comes after category+title
            s if !s.starts_with('-') => {
                if title.is_some() && description.is_empty() {
                    description = s.to_string();
                }
                i += 1;
            }
            _ => {
                i += 1;
            }
        }
    }

    let category = category.ok_or("must specify a category flag (--mistake, --pattern, --decision, --discovery, --feature, --refactor, --tool)")?;
    let title = title.ok_or("must provide a title after the category flag")?;

    if description.is_empty() {
        return Err("must provide a description after the title".to_string());
    }

    let conf = confidence.unwrap_or_else(|| default_confidence(&category));

    // Auto-detect wing/room if not provided (simplified rules matching Python)
    if wing.is_empty() {
        wing = auto_detect_wing(&tags, &title, &description);
    }
    if room.is_empty() {
        room = auto_detect_room(&tags, &title, &description);
    }

    Ok(LearnParams {
        category,
        title,
        description,
        tags,
        wing,
        room,
        confidence: conf,
        facts,
    })
}

fn execute_learn(params: LearnParams) -> ExitCode {
    let facts_json = if params.facts.is_empty() {
        "[]".to_string()
    } else {
        // Build simple JSON array
        let items: Vec<String> = params
            .facts
            .iter()
            .map(|f| format!("\"{}\"", f.replace('"', "\\\"")))
            .collect();
        format!("[{}]", items.join(","))
    };

    let entry = NewEntry {
        category: params.category.clone(),
        title: params.title.clone(),
        content: params.description,
        tags: params.tags,
        wing: params.wing.clone(),
        room: params.room.clone(),
        confidence: params.confidence,
        facts_json,
    };

    let conn = match open_writable(None) {
        Ok(c) => c,
        Err(e) => {
            eprintln!("sk learn: cannot open knowledge.db for writing: {e}");
            eprintln!("Hint: Run 'sk index build' first to initialize the database.");
            return ExitCode::from(1);
        }
    };

    let entry_id = match insert_or_update_entry(&conn, &entry) {
        Ok(id) => id,
        Err(e) => {
            eprintln!("sk learn: DB write failed: {e}");
            return ExitCode::from(1);
        }
    };

    // Rebuild FTS index for this entry
    let _ = rebuild_fts(&conn, entry_id);

    // Print confirmation matching Python format
    let loc = if !params.wing.is_empty() || !params.room.is_empty() {
        format!(" [{}/{}]", params.wing, params.room)
    } else {
        String::new()
    };
    println!("  Added new {} #{}{}", params.category, entry_id, loc);

    ExitCode::SUCCESS
}

/// Simplified wing auto-detection (mirrors Python _WING_RULES).
fn auto_detect_wing(tags: &str, title: &str, content: &str) -> String {
    let text = format!("{} {} {}", tags.to_lowercase(), title.to_lowercase(), &content[..content.len().min(200)].to_lowercase());
    let tag_set: std::collections::HashSet<&str> = tags.split(',').map(|t| t.trim()).collect();

    let backend_kws = ["lambda", "dynamodb", "sqs", "cdk", "api", "cognito", "s3", "eventbridge", "sns", "websocket"];
    let frontend_kws = ["expo", "react", "react-native", "screen", "component", "css", "ui", "navigation", "hook"];
    let devops_kws = ["git", "ci", "cd", "docker", "devops", "proxy", "tls", "npm", "yarn"];
    let shared_kws = ["typescript", "javascript", "eslint", "prettier", "i18n", "openapi"];

    for kw in &backend_kws {
        if tag_set.contains(kw) || text.contains(kw) { return "backend".to_string(); }
    }
    for kw in &frontend_kws {
        if tag_set.contains(kw) || text.contains(kw) { return "frontend".to_string(); }
    }
    for kw in &devops_kws {
        if tag_set.contains(kw) || text.contains(kw) { return "devops".to_string(); }
    }
    for kw in &shared_kws {
        if tag_set.contains(kw) || text.contains(kw) { return "shared".to_string(); }
    }
    String::new()
}

/// Simplified room auto-detection (mirrors Python _ROOM_RULES).
fn auto_detect_room(tags: &str, title: &str, content: &str) -> String {
    let text = format!("{} {} {}", tags.to_lowercase(), title.to_lowercase(), &content[..content.len().min(300)].to_lowercase());
    let tag_set: std::collections::HashSet<&str> = tags.split(',').map(|t| t.trim()).collect();

    let rules: &[(&[&str], &str)] = &[
        (&["patient", "patient-search"], "patient"),
        (&["hospital"], "hospital"),
        (&["websocket", "ws"], "websocket"),
        (&["dynamodb", "dao", "repository"], "dynamodb"),
        (&["auth", "cognito", "login"], "auth"),
        (&["s3", "media", "upload", "presigned"], "s3-media"),
        (&["sqs", "queue", "consumer"], "sqs"),
        (&["lambda", "handler"], "lambda"),
        (&["playwright", "e2e"], "e2e"),
        (&["cdk", "cloudformation", "stack"], "cdk"),
    ];

    for (patterns, room) in rules {
        for kw in *patterns {
            if tag_set.contains(kw) || text.contains(kw) {
                return room.to_string();
            }
        }
    }
    String::new()
}
