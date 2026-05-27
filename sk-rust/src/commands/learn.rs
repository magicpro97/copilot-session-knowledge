use std::fs;
use std::path::PathBuf;
use std::process::{Command, ExitCode};
use std::time::{SystemTime, UNIX_EPOCH};

use rusqlite::ErrorCode;
use serde_json::json;
use sha2::{Digest, Sha256};

use crate::config::{python_exe, resolve_home_dir, resolve_tools_dir};
use crate::db::write::{
    compute_stable_id, insert_or_update_entry, open_writable_with_busy_timeout, rebuild_fts,
    NewEntry,
};
use crate::hooks::audit::audit_log;
use crate::redact::{redact, Finding, RedactMode, RedactionResult};

const DEFAULT_LEARN_DB_BUSY_TIMEOUT_MS: u64 = 30_000;
const DEFAULT_LEARN_QUEUE_BUSY_TIMEOUT_MS: u64 = 250;

/// Exit codes per issue #571.
const EXIT_FLUSHED: u8 = 0;
const EXIT_QUEUED: u8 = 2;
const EXIT_FATAL: u8 = 1;
/// Reserved per issue #571 for "blocked by a learn gate".
#[allow(dead_code)]
const EXIT_BLOCKED_BY_GATE: u8 = 3;
/// Used by issue #577 when SK_REDACT=deny finds secrets.
const EXIT_REDACT_DENY: u8 = 4;

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
enum ReceiptMode {
    Text,
    Json,
}

/// Entry point called from main's dispatch for the `learn` command.
pub fn run_learn_command(args: &[String]) -> ExitCode {
    if args.iter().any(|arg| arg == "--flush-inbox") {
        return delegate_to_python_learn(args);
    }

    match parse_learn_args(args) {
        Ok(params) => execute_learn(params),
        Err(msg) => {
            eprintln!("sk learn: {msg}");
            eprintln!("Usage: sk learn --mistake|--pattern|--decision|--discovery|--feature|--refactor|--tool \"Title\" \"Description\"");
            eprintln!("       [--tags \"tag1,tag2\"] [--wing WING] [--room ROOM] [--confidence 0.7] [--fact \"fact\"]");
            eprintln!(
                "       [--receipt text|json] (env: SK_LEARN_STRICT, SK_REDACT=off|warn|deny)"
            );
            ExitCode::from(EXIT_FATAL)
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
    argv: Vec<String>,
    skip_gate: bool,
    skip_scan: bool,
    receipt: ReceiptMode,
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
    let mut skip_gate = false;
    let mut skip_scan = false;
    let mut receipt = ReceiptMode::Text;

    let category_flags = [
        "--mistake",
        "--pattern",
        "--decision",
        "--discovery",
        "--feature",
        "--refactor",
        "--tool",
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
            "--skip-gate" => {
                skip_gate = true;
                i += 1;
            }
            "--skip-scan" => {
                skip_scan = true;
                i += 1;
            }
            "--receipt" => {
                let v = args.get(i + 1).cloned().unwrap_or_default();
                receipt = match v.as_str() {
                    "json" => ReceiptMode::Json,
                    "text" | "" => ReceiptMode::Text,
                    other => {
                        return Err(format!("--receipt expects 'text' or 'json'; got '{other}'"));
                    }
                };
                i += 2;
            }
            // Skip unknown flags we don't handle.
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
        argv: args.to_vec(),
        skip_gate,
        skip_scan,
        receipt,
    })
}

/// Apply `crate::redact::redact` to every free-form text field, mutating
/// `params` in place. Returns the aggregated findings across all fields
/// (deduped by `(kind, hash)`) so the caller can decide whether to log,
/// warn, or deny.
fn redact_params_in_place(params: &mut LearnParams) -> Vec<Finding> {
    fn apply(field: &mut String, all: &mut Vec<Finding>) {
        let r: RedactionResult = redact(field);
        if !r.findings.is_empty() {
            *field = r.redacted;
            all.extend(r.findings);
        }
    }
    let mut findings: Vec<Finding> = Vec::new();
    apply(&mut params.title, &mut findings);
    apply(&mut params.description, &mut findings);
    apply(&mut params.tags, &mut findings);
    apply(&mut params.wing, &mut findings);
    apply(&mut params.room, &mut findings);
    for fact in params.facts.iter_mut() {
        apply(fact, &mut findings);
    }
    // #577 BLOCKER FIX: redact every argv element so the queued JSON
    // payload (`write_learn_payload` writes `argv` verbatim) cannot
    // contain raw secrets that a user typed on the CLI (e.g.
    // `--description "password=hunter2hunter2"` or an AWS key in a
    // free-form positional arg). Without this, SK_REDACT=warn would
    // scrub `entry.*` fields but leak the same secret via `argv`.
    for arg in params.argv.iter_mut() {
        apply(arg, &mut findings);
    }
    // Deduplicate by (kind, hash) so a single secret repeated across
    // fields is counted once in the summary surface.
    findings.sort_by(|a, b| {
        (a.kind.as_str(), a.hash.as_str()).cmp(&(b.kind.as_str(), b.hash.as_str()))
    });
    findings.dedup_by(|a, b| a.kind == b.kind && a.hash == b.hash);
    findings
}

fn findings_summary(findings: &[Finding]) -> String {
    let parts: Vec<String> = findings
        .iter()
        .map(|f| format!("{}:{}", f.kind, f.hash))
        .collect();
    parts.join(",")
}

fn execute_learn(mut params: LearnParams) -> ExitCode {
    // -- Redaction: applied BEFORE any DB write or queue write ---------
    let mode = RedactMode::from_env();
    let findings = match mode {
        RedactMode::Off => Vec::new(),
        RedactMode::Warn | RedactMode::Deny => redact_params_in_place(&mut params),
    };
    if !findings.is_empty() {
        let summary = findings_summary(&findings);
        match mode {
            RedactMode::Warn => {
                eprintln!(
                    "sk learn: redacted {} secret pattern(s): {summary}",
                    findings.len()
                );
            }
            RedactMode::Deny => {
                eprintln!(
                    "sk learn: SK_REDACT=deny; refused write: {} secret pattern(s): {summary}",
                    findings.len()
                );
                // Emit a deny audit event (hash-only, no raw secret).
                let detail = json!({
                    "kinds": findings.iter().map(|f| f.kind.clone()).collect::<Vec<_>>(),
                    "n": findings.len(),
                });
                audit_log("learn.deny", "sk", "redact", "deny", &detail.to_string());
                if matches!(params.receipt, ReceiptMode::Json) {
                    let receipt = json!({
                        "status": "denied",
                        "reason": "secrets_redacted",
                        "findings": findings
                            .iter()
                            .map(|f| json!({"kind": f.kind, "hash": f.hash}))
                            .collect::<Vec<_>>(),
                    });
                    println!("{receipt}");
                }
                return ExitCode::from(EXIT_REDACT_DENY);
            }
            RedactMode::Off => {}
        }
    }

    // -- Test seam: force queue path -----------------------------------
    if std::env::var("SK_LEARN_FORCE_QUEUE").is_ok_and(|v| v != "0") {
        return queue_learn_params(&params);
    }

    let facts_json = params.facts_json();

    let entry = NewEntry {
        category: params.category.clone(),
        title: params.title.clone(),
        content: params.description.clone(),
        tags: params.tags.clone(),
        wing: params.wing.clone(),
        room: params.room.clone(),
        confidence: params.confidence,
        facts_json,
    };

    let conn = match open_writable_with_busy_timeout(None, learn_write_busy_timeout_ms()) {
        Ok(c) => c,
        Err(e) => {
            if is_busy_error(&e) && queue_on_lock_enabled() {
                return queue_learn_params(&params);
            }
            eprintln!("sk learn: cannot open knowledge.db for writing: {e}");
            eprintln!("Hint: Run 'sk index build' first to initialize the database.");
            return ExitCode::from(EXIT_FATAL);
        }
    };

    let entry_id = match insert_or_update_entry(&conn, &entry) {
        Ok(id) => id,
        Err(e) => {
            if is_busy_error(&e) && queue_on_lock_enabled() {
                return queue_learn_params(&params);
            }
            eprintln!("sk learn: DB write failed: {e}");
            return ExitCode::from(EXIT_FATAL);
        }
    };

    // Rebuild FTS index for this entry
    let _ = rebuild_fts(&conn, entry_id);

    let stable_id = compute_stable_id("manual", &params.category, &params.title);
    let stable_id_short = format!("sha256:{}", &stable_id[..16]);
    // Audit detail uses the bare 16-char short form (no "sha256:" prefix)
    // so it pairs with briefing.served's `stable_ids` array under the
    // audit writer's 200-char detail truncation (#574).
    let stable_id_audit = stable_id[..16].to_string();

    // -- Audit emit: learn.flushed (best-effort) -----------------------
    let detail = json!({
        "stable_id": stable_id_audit,
        "category": params.category,
        "entry_id": entry_id,
    });
    audit_log(
        "learn.flushed",
        "sk",
        "learn",
        "flushed",
        &detail.to_string(),
    );

    // -- Receipt -------------------------------------------------------
    match params.receipt {
        ReceiptMode::Text => {
            let loc = if !params.wing.is_empty() || !params.room.is_empty() {
                format!(" [{}/{}]", params.wing, params.room)
            } else {
                String::new()
            };
            println!("  Added new {} #{}{}", params.category, entry_id, loc);
        }
        ReceiptMode::Json => {
            let receipt = json!({
                "status": "flushed",
                "id": entry_id,
                "stable_id": stable_id_short,
            });
            println!("{receipt}");
        }
    }

    ExitCode::from(EXIT_FLUSHED)
}

fn delegate_to_python_learn(args: &[String]) -> ExitCode {
    let learn_py = resolve_tools_dir().join("learn.py");
    let status = Command::new(python_exe()).arg(learn_py).args(args).status();
    match status {
        Ok(status) if status.success() => ExitCode::SUCCESS,
        Ok(_) => ExitCode::from(EXIT_FATAL),
        Err(err) => {
            eprintln!("sk learn: failed to run learn.py for --flush-inbox: {err}");
            ExitCode::from(EXIT_FATAL)
        }
    }
}

fn queue_on_lock_enabled() -> bool {
    std::env::var("SK_LEARN_QUEUE_ON_LOCK").map_or(true, |value| value != "0")
}

fn strict_mode() -> bool {
    std::env::var("SK_LEARN_STRICT").is_ok_and(|v| v != "0" && !v.is_empty())
}

fn learn_write_busy_timeout_ms() -> u64 {
    if !queue_on_lock_enabled() {
        return DEFAULT_LEARN_DB_BUSY_TIMEOUT_MS;
    }
    std::env::var("SK_LEARN_BUSY_TIMEOUT_MS")
        .ok()
        .and_then(|value| value.parse::<u64>().ok())
        .unwrap_or(DEFAULT_LEARN_QUEUE_BUSY_TIMEOUT_MS)
}

fn is_busy_error(err: &rusqlite::Error) -> bool {
    match err {
        rusqlite::Error::SqliteFailure(sqlite_err, _) => {
            matches!(
                sqlite_err.code,
                ErrorCode::DatabaseBusy | ErrorCode::DatabaseLocked
            )
        }
        _ => {
            let message = err.to_string().to_ascii_lowercase();
            message.contains("database is locked") || message.contains("database is busy")
        }
    }
}

fn learn_inbox_dir() -> PathBuf {
    if let Ok(path) = std::env::var("SK_LEARN_INBOX") {
        return expand_tilde(PathBuf::from(path));
    }
    resolve_home_dir()
        .unwrap_or_else(|| PathBuf::from("."))
        .join(".copilot")
        .join("session-state")
        .join("learn-inbox")
}

fn expand_tilde(path: PathBuf) -> PathBuf {
    let raw = path.to_string_lossy();
    if raw == "~" {
        return resolve_home_dir().unwrap_or(path);
    }
    if let Some(rest) = raw.strip_prefix("~/").or_else(|| raw.strip_prefix("~\\")) {
        if let Some(home) = resolve_home_dir() {
            return home.join(rest);
        }
    }
    path
}

fn queue_learn_params(params: &LearnParams) -> ExitCode {
    match write_learn_payload(params) {
        Ok(path) => {
            let queue_id = path
                .file_stem()
                .and_then(|s| s.to_str())
                .unwrap_or("queued")
                .to_string();
            let stable_id = compute_stable_id("manual", &params.category, &params.title);
            let stable_id_short = format!("sha256:{}", &stable_id[..16]);
            let stable_id_audit = stable_id[..16].to_string();

            // -- Audit emit: learn.queued (best-effort) -----------------
            let detail = json!({
                "stable_id": stable_id_audit,
                "category": params.category,
                "queue_id": queue_id,
            });
            audit_log("learn.queued", "sk", "learn", "queued", &detail.to_string());

            // -- Receipt ------------------------------------------------
            match params.receipt {
                ReceiptMode::Text => {
                    eprintln!(
                        "  DB busy; queued learn entry for later flush: {}",
                        path.file_name()
                            .and_then(|name| name.to_str())
                            .unwrap_or("queued learn entry")
                    );
                    eprintln!("  Run `sk learn --flush-inbox` to replay queued entries.");
                }
                ReceiptMode::Json => {
                    let receipt = json!({
                        "status": "queued",
                        "queue_id": queue_id,
                        "path": path.display().to_string(),
                        "stable_id": stable_id_short,
                    });
                    println!("{receipt}");
                }
            }

            if strict_mode() {
                ExitCode::from(EXIT_FATAL)
            } else {
                ExitCode::from(EXIT_QUEUED)
            }
        }
        Err(err) => {
            eprintln!("sk learn: DB locked and failed to queue entry: {err}");
            ExitCode::from(EXIT_FATAL)
        }
    }
}

fn write_learn_payload(params: &LearnParams) -> Result<PathBuf, String> {
    let inbox = learn_inbox_dir();
    fs::create_dir_all(&inbox).map_err(|err| err.to_string())?;
    let payload = json!({
        "schema_version": 1,
        "queued_at": chrono::Utc::now().format("%Y-%m-%dT%H:%M:%S").to_string(),
        "reason": "database_locked",
        "argv": params.argv.clone(),
        "entry": {
            "category": params.category.clone(),
            "title": params.title.clone(),
            "content": params.description.clone(),
            "tags": params.tags.clone(),
            "session_id": "manual",
            "confidence": params.confidence,
            "wing": params.wing.clone(),
            "room": params.room.clone(),
            "facts": params.facts.clone(),
            "skip_gate": params.skip_gate,
            "skip_scan": params.skip_scan,
            "task_id": "",
            "affected_files": [],
            "source_file": "",
            "start_line": 0,
            "end_line": 0,
            "code_language": "",
            "code_snippet": "",
            "code_location_set": false,
            "quiet": false,
            "error_type": "",
            "root_cause": "",
            "severity": "",
            "fix_steps": "",
            "valence": "",
            "intensity": null,
            "priority": "",
            "agent_id": "",
            "certainty": "",
            "caveats": ""
        },
        "cerebrum": {
            "update": false,
            "output": "CEREBRUM.md",
            "sections": null
        }
    });
    let raw = serde_json::to_string(&payload).map_err(|err| err.to_string())?;
    let digest = Sha256::digest(raw.as_bytes());
    let hash = format!("{:x}", digest);
    let nanos = SystemTime::now()
        .duration_since(UNIX_EPOCH)
        .map_err(|err| err.to_string())?
        .as_nanos();
    let name = format!(
        "{}-{}-{}.json",
        chrono::Utc::now().format("%Y%m%dT%H%M%S"),
        nanos,
        &hash[..16]
    );
    let final_path = inbox.join(&name);
    let tmp_path = inbox.join(format!(".{name}.tmp"));
    fs::write(&tmp_path, format!("{raw}\n")).map_err(|err| err.to_string())?;
    fs::rename(&tmp_path, &final_path).map_err(|err| err.to_string())?;
    Ok(final_path)
}

impl LearnParams {
    fn facts_json(&self) -> String {
        serde_json::to_string(&self.facts).unwrap_or_else(|_| "[]".to_string())
    }
}

/// Return a prefix of `s` containing at most `max_bytes`, ending on a UTF-8
/// char boundary. Prevents panics when byte caps land mid-codepoint.
fn utf8_safe_prefix(s: &str, max_bytes: usize) -> &str {
    if s.len() <= max_bytes {
        return s;
    }
    let mut end = max_bytes;
    while end > 0 && !s.is_char_boundary(end) {
        end -= 1;
    }
    &s[..end]
}

/// Simplified wing auto-detection (mirrors Python _WING_RULES).
fn auto_detect_wing(tags: &str, title: &str, content: &str) -> String {
    let text = format!(
        "{} {} {}",
        tags.to_lowercase(),
        title.to_lowercase(),
        utf8_safe_prefix(content, 200).to_lowercase()
    );
    let tag_set: std::collections::HashSet<&str> = tags.split(',').map(|t| t.trim()).collect();

    let backend_kws = [
        "lambda",
        "dynamodb",
        "sqs",
        "cdk",
        "api",
        "cognito",
        "s3",
        "eventbridge",
        "sns",
        "websocket",
    ];
    let frontend_kws = [
        "expo",
        "react",
        "react-native",
        "screen",
        "component",
        "css",
        "ui",
        "navigation",
        "hook",
    ];
    let devops_kws = [
        "git", "ci", "cd", "docker", "devops", "proxy", "tls", "npm", "yarn",
    ];
    let shared_kws = [
        "typescript",
        "javascript",
        "eslint",
        "prettier",
        "i18n",
        "openapi",
    ];

    for kw in &backend_kws {
        if tag_set.contains(kw) || text.contains(kw) {
            return "backend".to_string();
        }
    }
    for kw in &frontend_kws {
        if tag_set.contains(kw) || text.contains(kw) {
            return "frontend".to_string();
        }
    }
    for kw in &devops_kws {
        if tag_set.contains(kw) || text.contains(kw) {
            return "devops".to_string();
        }
    }
    for kw in &shared_kws {
        if tag_set.contains(kw) || text.contains(kw) {
            return "shared".to_string();
        }
    }
    String::new()
}

/// Simplified room auto-detection (mirrors Python _ROOM_RULES).
fn auto_detect_room(tags: &str, title: &str, content: &str) -> String {
    let text = format!(
        "{} {} {}",
        tags.to_lowercase(),
        title.to_lowercase(),
        utf8_safe_prefix(content, 300).to_lowercase()
    );
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

#[cfg(test)]
mod tests {
    use super::*;
    use std::time::{SystemTime, UNIX_EPOCH};

    #[test]
    fn facts_json_escapes_special_characters() {
        let params = LearnParams {
            category: "pattern".to_string(),
            title: "title".to_string(),
            description: "description".to_string(),
            tags: String::new(),
            wing: String::new(),
            room: String::new(),
            confidence: 0.7,
            facts: vec!["C:\\tmp\nquoted \"fact\"".to_string()],
            argv: vec![],
            skip_gate: false,
            skip_scan: false,
            receipt: ReceiptMode::Text,
        };

        let parsed: Vec<String> = serde_json::from_str(&params.facts_json()).unwrap();
        assert_eq!(parsed, params.facts);
    }

    #[test]
    fn queued_payload_preserves_flags_and_facts() {
        let unique = SystemTime::now()
            .duration_since(UNIX_EPOCH)
            .unwrap()
            .as_nanos();
        let inbox = std::env::temp_dir().join(format!("sk_learn_inbox_test_{unique}"));
        let old_inbox = std::env::var("SK_LEARN_INBOX").ok();
        std::env::set_var("SK_LEARN_INBOX", &inbox);

        let params = LearnParams {
            category: "decision".to_string(),
            title: "queue title".to_string(),
            description: "queue description".to_string(),
            tags: "sqlite".to_string(),
            wing: "devops".to_string(),
            room: "tooling".to_string(),
            confidence: 0.8,
            facts: vec!["C:\\tmp\nfact".to_string()],
            argv: vec![
                "--decision".to_string(),
                "queue title".to_string(),
                "queue description".to_string(),
            ],
            skip_gate: false,
            skip_scan: true,
            receipt: ReceiptMode::Text,
        };

        let queued = write_learn_payload(&params).unwrap();
        let payload: serde_json::Value =
            serde_json::from_str(&std::fs::read_to_string(&queued).unwrap()).unwrap();
        assert_eq!(payload["entry"]["skip_gate"], false);
        assert_eq!(payload["entry"]["skip_scan"], true);
        assert_eq!(payload["entry"]["facts"][0], "C:\\tmp\nfact");

        let _ = std::fs::remove_dir_all(&inbox);
        match old_inbox {
            Some(value) => std::env::set_var("SK_LEARN_INBOX", value),
            None => std::env::remove_var("SK_LEARN_INBOX"),
        }
    }

    #[test]
    fn learn_inbox_dir_expands_tilde_env() {
        let old_inbox = std::env::var("SK_LEARN_INBOX").ok();
        std::env::set_var(
            "SK_LEARN_INBOX",
            "~/.copilot/session-state/learn-inbox-test",
        );
        let resolved = learn_inbox_dir();
        assert!(!resolved.to_string_lossy().starts_with('~'));
        assert!(resolved.ends_with(".copilot/session-state/learn-inbox-test"));
        match old_inbox {
            Some(value) => std::env::set_var("SK_LEARN_INBOX", value),
            None => std::env::remove_var("SK_LEARN_INBOX"),
        }
    }

    #[test]
    fn utf8_safe_prefix_keeps_short_ascii_unchanged() {
        let s = "hello world";
        assert_eq!(utf8_safe_prefix(s, 200), s);
        assert_eq!(utf8_safe_prefix(s, 5), "hello");
    }

    #[test]
    fn auto_detect_wing_does_not_panic_on_cjk_around_cap() {
        // Each CJK char is 3 bytes in UTF-8; produce >200 bytes of CJK so the
        // 200-byte cap is likely to land mid-codepoint.
        let cjk: String = "中文测试内容".repeat(20);
        assert!(cjk.len() > 200);
        // Must not panic regardless of where the cap lands.
        let _ = auto_detect_wing("", "", &cjk);
        // Mixed: ASCII keyword inside cap should still be detected.
        let mixed = format!("lambda {}", cjk);
        assert_eq!(auto_detect_wing("", "", &mixed), "backend");
    }

    #[test]
    fn auto_detect_room_does_not_panic_on_emoji_and_cjk_around_cap() {
        // Emoji are 4 bytes; CJK are 3 bytes. Both will straddle 300 bytes.
        let emoji: String = "🚀🔥✨".repeat(40);
        assert!(emoji.len() > 300);
        let _ = auto_detect_room("", "", &emoji);
        let cjk: String = "数据库患者".repeat(30);
        assert!(cjk.len() > 300);
        let _ = auto_detect_room("", "", &cjk);
        // ASCII keyword within cap still detected.
        let mixed = format!("patient {}", cjk);
        assert_eq!(auto_detect_room("", "", &mixed), "patient");
    }
}
