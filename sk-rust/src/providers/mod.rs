//! Native session provider module.
//!
//! Provides `ClaudeProvider` — the Rust port of `providers/claude_provider.py`.
//! Discovers and parses Claude Code JSONL sessions from `~/.claude/projects/`.
//!
//! ## Contract
//! - Session ID is the JSONL filename stem (UUID).
//! - `parent_id` is read from JSONL entries (`parentSessionId` / `parent_session_id`),
//!   never from the directory name (§A-BL-02).
//! - Byte offsets are per-line start positions tracked via binary-mode read.
//! - Noise filter: `note` kind always dropped; `system` kind if boilerplate.

use serde_json::Value;
use std::fs;
use std::io::{BufRead, BufReader};
use std::path::{Path, PathBuf};
use std::time::UNIX_EPOCH;

// ── Constants ─────────────────────────────────────────────────────────────────

pub const MAX_CONTENT_CHARS: usize = 10_000;
pub const MAX_TOOL_RESULT_CHARS: usize = 2_000;
const MIN_SESSION_BYTES: u64 = 1_024;
const MAX_LINE_BYTES: usize = 50_000_000;

// ── Public types ──────────────────────────────────────────────────────────────

/// Lightweight session descriptor for a Claude JSONL session file.
///
/// Mirrors Python's `SessionMeta` when produced by `ClaudeProvider`.
#[derive(Debug, Clone)]
pub struct ClaudeSession {
    /// Session UUID — stem of the `.jsonl` filename.
    pub id: String,
    /// Absolute path to the `.jsonl` file.
    pub path: PathBuf,
    /// File mtime as seconds since Unix epoch.
    pub mtime: f64,
    /// File size in bytes.
    pub size: u64,
    /// Title peeked from the first `user` message (≤ 200 chars).
    pub title: Option<String>,
    /// Parent session UUID for sub-agent sessions (§A-BL-02).
    pub parent_id: Option<String>,
    /// Project hash — parent directory name inside the Claude projects root.
    pub project_hash: String,
}

/// A normalized event from a Claude JSONL session.
///
/// `kind` is one of: `user_msg`, `assistant_msg`, `tool_call`, `tool_result`,
/// `system`, `note` (matches the 7-value EventKind literal set in Python's IR).
#[derive(Debug, Clone)]
pub struct ClaudeEvent {
    /// FK → `sessions.id`.
    pub session_id: String,
    /// Monotonically increasing within a session (0-based).
    pub event_id: u32,
    /// One of the 6 active EventKind values; `diff` not emitted by JSONL.
    pub kind: &'static str,
    /// Primary text, ≤ `MAX_CONTENT_CHARS` chars.
    pub content: String,
    /// Populated for `tool_call` / `tool_result` events.
    pub tool_name: Option<String>,
}

// ── ClaudeProvider ────────────────────────────────────────────────────────────

/// Discovers and parses Claude Code JSONL sessions.
///
/// Equivalent to `ClaudeProvider` in `providers/claude_provider.py`.
pub struct ClaudeProvider {
    root: PathBuf,
}

impl ClaudeProvider {
    /// Create from the env-variable-overridable Claude projects root.
    ///
    /// Returns `None` when the root directory does not exist.
    pub fn new() -> Option<Self> {
        let root = if let Ok(ov) = std::env::var("CLAUDE_PROJECTS") {
            let p = ov.trim().to_string();
            if p.is_empty() {
                default_claude_root()?
            } else {
                PathBuf::from(p)
            }
        } else {
            default_claude_root()?
        };
        if root.exists() {
            Some(Self { root })
        } else {
            None
        }
    }

    /// Discover all JSONL session files, returning a sorted list.
    ///
    /// Mirrors Python's `list_sessions()`:
    ///   - `root/<project-hash>/<session>.jsonl` — top-level sessions
    ///   - `root/<project-hash>/<subdir>/subagents/<session>.jsonl` — sub-agents
    pub fn list_sessions(&self) -> Vec<ClaudeSession> {
        let mut sessions = Vec::new();
        let Ok(entries) = fs::read_dir(&self.root) else {
            return sessions;
        };
        let mut project_dirs: Vec<_> = entries.flatten().filter(|e| e.path().is_dir()).collect();
        project_dirs.sort_by_key(|e| e.path());

        for pe in project_dirs {
            let project_dir = pe.path();
            let project_hash = project_dir
                .file_name()
                .and_then(|n| n.to_str())
                .unwrap_or("")
                .to_string();

            // Top-level sessions
            if let Ok(files) = fs::read_dir(&project_dir) {
                let mut jsonl: Vec<_> = files
                    .flatten()
                    .filter(|e| e.path().extension().and_then(|x| x.to_str()) == Some("jsonl"))
                    .collect();
                jsonl.sort_by_key(|e| e.path());
                for fe in jsonl {
                    if let Some(s) = make_session(fe.path(), None, &project_hash) {
                        sessions.push(s);
                    }
                }
            }

            // Nested sub-agent sessions: project/<subdir>/subagents/*.jsonl
            if let Ok(subdirs) = fs::read_dir(&project_dir) {
                let mut subdirs: Vec<_> = subdirs.flatten().filter(|e| e.path().is_dir()).collect();
                subdirs.sort_by_key(|e| e.path());
                for subdir_entry in subdirs {
                    let subagents_dir = subdir_entry.path().join("subagents");
                    if !subagents_dir.exists() {
                        continue;
                    }
                    let Ok(files) = fs::read_dir(&subagents_dir) else {
                        continue;
                    };
                    let mut jsonl: Vec<_> = files
                        .flatten()
                        .filter(|e| e.path().extension().and_then(|x| x.to_str()) == Some("jsonl"))
                        .collect();
                    jsonl.sort_by_key(|e| e.path());
                    for fe in jsonl {
                        let path = fe.path();
                        // §A-BL-02: parent_id from JSONL content, NOT directory name
                        let parent_id = read_parent_session_id(&path);
                        if let Some(s) = make_session(path, parent_id, &project_hash) {
                            sessions.push(s);
                        }
                    }
                }
            }
        }
        sessions
    }

    /// Iterate `(ClaudeEvent, byte_offset)` pairs from a session file.
    ///
    /// Opens the file in binary-read mode and tracks line-start byte offsets
    /// manually via `read_until`.  All events from the same JSONL line share
    /// that line's start offset (matching Python's `iter_events_with_offset()`).
    pub fn iter_events_with_offset(&self, session: &ClaudeSession) -> Vec<(ClaudeEvent, u64)> {
        let Ok(fh) = fs::File::open(&session.path) else {
            return vec![];
        };
        let mut reader = BufReader::new(fh);
        let mut result: Vec<(ClaudeEvent, u64)> = Vec::new();
        let mut byte_pos: u64 = 0;
        let mut event_counter: u32 = 0;
        let mut warned_once = false;
        let mut line_num: u32 = 0;

        loop {
            let line_byte_offset = byte_pos;
            let mut buf = Vec::new();
            let bytes_read = match reader.read_until(b'\n', &mut buf) {
                Ok(0) => break,
                Ok(n) => n,
                Err(_) => break,
            };
            byte_pos += bytes_read as u64;
            line_num += 1;

            if buf.len() > MAX_LINE_BYTES {
                continue;
            }

            let raw_line = String::from_utf8_lossy(&buf).trim().to_string();
            if raw_line.is_empty() {
                continue;
            }

            let entry: Value = match serde_json::from_str(&raw_line) {
                Ok(v) => v,
                Err(_) => {
                    if !warned_once {
                        eprintln!(
                            "[ClaudeProvider] Malformed JSONL in {} (line {line_num}), skipping",
                            session.path.display()
                        );
                        warned_once = true;
                    }
                    continue;
                }
            };

            let entry_type = entry["type"].as_str().unwrap_or("");
            let new_events = decompose_entry(entry_type, &entry, &session.id, event_counter);
            let ev_count = new_events.len() as u32;
            for ev in new_events {
                result.push((ev, line_byte_offset));
            }
            event_counter += ev_count;
        }
        result
    }
}

// ── Noise filter ──────────────────────────────────────────────────────────────

/// Return `true` if this event should be excluded from FTS indexing.
///
/// Mirrors Python's `_is_system_boilerplate()` / noise filter (Batch B §B-BL-01):
/// - `note` kind: always excluded (scope cut per ir-contract).
/// - `system` kind: excluded when content matches known low-signal boilerplate.
pub fn is_noise(event: &ClaudeEvent) -> bool {
    if event.kind == "note" {
        return true;
    }
    if event.kind != "system" {
        return false;
    }

    let c = &event.content;
    if c.starts_with("<context")
        || c.starts_with("<Context")
        || c.starts_with("<system")
        || c.starts_with("<System")
    {
        return true;
    }
    let cl = c.to_lowercase();
    cl.contains("you are claude")
        || cl.contains("the assistant is claude")
        || cl.starts_with("here are some instructions")
        || cl.starts_with("this is a conversation")
}

// ── Entry decomposition ───────────────────────────────────────────────────────

fn decompose_entry(
    entry_type: &str,
    entry: &Value,
    session_id: &str,
    base_event_id: u32,
) -> Vec<ClaudeEvent> {
    match entry_type {
        "user" => decompose_user_content(&entry["message"]["content"], session_id, base_event_id),
        "assistant" => {
            decompose_assistant_content(&entry["message"]["content"], session_id, base_event_id)
        }
        "system" => {
            let msg = &entry["message"];
            let sys_text = if let Some(s) = msg["content"].as_str() {
                s.to_string()
            } else {
                let t = msg["content"].to_string();
                if t == "null" {
                    return vec![];
                }
                t
            };
            if sys_text.is_empty() {
                return vec![];
            }
            vec![ClaudeEvent {
                session_id: session_id.to_string(),
                event_id: base_event_id,
                kind: "system",
                content: truncate_chars(&sys_text, MAX_CONTENT_CHARS),
                tool_name: None,
            }]
        }
        "attachment" | "last-prompt" => {
            let note_text = serde_json::to_string(entry).unwrap_or_default();
            vec![ClaudeEvent {
                session_id: session_id.to_string(),
                event_id: base_event_id,
                kind: "note",
                content: truncate_chars(&note_text, MAX_CONTENT_CHARS),
                tool_name: None,
            }]
        }
        // queue-operation, permission-mode, file-history-snapshot: silently skip.
        _ => vec![],
    }
}

fn decompose_user_content(
    content: &Value,
    session_id: &str,
    base_event_id: u32,
) -> Vec<ClaudeEvent> {
    let mut events = Vec::new();
    let mut counter = base_event_id;

    if let Some(text) = content.as_str() {
        let t = text.trim();
        if !t.is_empty() {
            events.push(ClaudeEvent {
                session_id: session_id.to_string(),
                event_id: counter,
                kind: "user_msg",
                content: truncate_chars(t, MAX_CONTENT_CHARS),
                tool_name: None,
            });
        }
        return events;
    }

    if let Some(blocks) = content.as_array() {
        let mut text_parts: Vec<String> = Vec::new();
        for block in blocks {
            let btype = block["type"].as_str().unwrap_or("");
            if btype == "text" {
                let t = block["text"].as_str().unwrap_or("").trim().to_string();
                if !t.is_empty() {
                    text_parts.push(t);
                }
            } else if btype == "tool_result" {
                let rc = &block["content"];
                let result_text = if let Some(s) = rc.as_str() {
                    s.to_string()
                } else if let Some(arr) = rc.as_array() {
                    arr.iter()
                        .filter(|b| b["type"].as_str() == Some("text"))
                        .map(|b| b["text"].as_str().unwrap_or(""))
                        .collect::<Vec<_>>()
                        .join("\n")
                } else {
                    let s = rc.to_string();
                    if s == "null" {
                        String::new()
                    } else {
                        s
                    }
                };
                let tool_id = block["tool_use_id"].as_str().map(|s| s.to_string());
                events.push(ClaudeEvent {
                    session_id: session_id.to_string(),
                    event_id: counter,
                    kind: "tool_result",
                    content: truncate_chars(&result_text, MAX_TOOL_RESULT_CHARS),
                    tool_name: tool_id,
                });
                counter += 1;
            }
        }
        let combined = text_parts.join("\n");
        if !combined.is_empty() {
            events.push(ClaudeEvent {
                session_id: session_id.to_string(),
                event_id: counter,
                kind: "user_msg",
                content: truncate_chars(&combined, MAX_CONTENT_CHARS),
                tool_name: None,
            });
        }
    }
    events
}

fn decompose_assistant_content(
    content: &Value,
    session_id: &str,
    base_event_id: u32,
) -> Vec<ClaudeEvent> {
    let mut events = Vec::new();
    let mut counter = base_event_id;
    let mut text_parts: Vec<String> = Vec::new();

    if let Some(blocks) = content.as_array() {
        for block in blocks {
            let btype = block["type"].as_str().unwrap_or("");
            if btype == "text" {
                let t = block["text"].as_str().unwrap_or("").trim().to_string();
                if !t.is_empty() {
                    text_parts.push(t);
                }
            } else if btype == "tool_use" {
                let tool_name = block["name"].as_str().unwrap_or("unknown").to_string();
                let summary = tool_call_summary(&tool_name, &block["input"]);
                events.push(ClaudeEvent {
                    session_id: session_id.to_string(),
                    event_id: counter,
                    kind: "tool_call",
                    content: truncate_chars(&summary, MAX_CONTENT_CHARS),
                    tool_name: Some(tool_name),
                });
                counter += 1;
            }
        }
    }

    let combined = text_parts.join("\n");
    if !combined.is_empty() {
        events.push(ClaudeEvent {
            session_id: session_id.to_string(),
            event_id: counter,
            kind: "assistant_msg",
            content: truncate_chars(&combined, MAX_CONTENT_CHARS),
            tool_name: None,
        });
    }
    events
}

fn tool_call_summary(tool_name: &str, tool_input: &Value) -> String {
    if tool_name.to_lowercase() == "bash" {
        if let Some(cmd) = tool_input["command"].as_str() {
            return format!("[{tool_name}] {cmd}");
        }
    }
    for key in &["file_path", "filePath", "path"] {
        if let Some(fp) = tool_input[key].as_str() {
            return format!("[{tool_name}] {fp}");
        }
    }
    format!("[{tool_name}]")
}

// ── Peek / scan helpers ───────────────────────────────────────────────────────

/// Peek title from the first `user` message in a JSONL file (≤ 200 chars).
///
/// Mirrors Python's `_peek_title()`.
fn peek_title(path: &Path) -> Option<String> {
    let fh = fs::File::open(path).ok()?;
    let reader = BufReader::new(fh);
    for (i, line) in reader.lines().enumerate() {
        if i >= 50 {
            break;
        }
        let Ok(line) = line else { continue };
        let entry: Value = serde_json::from_str(line.trim()).ok()?;
        if entry["type"].as_str() != Some("user") {
            continue;
        }
        let content = &entry["message"]["content"];
        if let Some(text) = content.as_str() {
            let t = text.trim();
            if !t.is_empty() {
                return Some(truncate_chars(t, 200));
            }
        }
        if let Some(blocks) = content.as_array() {
            for block in blocks {
                if block["type"].as_str() == Some("text") {
                    let t = block["text"].as_str().unwrap_or("").trim().to_string();
                    if !t.is_empty() {
                        return Some(truncate_chars(&t, 200));
                    }
                }
            }
        }
    }
    None
}

/// Read parent session UUID from the first 20 JSONL entries (§A-BL-02).
fn read_parent_session_id(path: &Path) -> Option<String> {
    let fh = fs::File::open(path).ok()?;
    let reader = BufReader::new(fh);
    for (i, line) in reader.lines().enumerate() {
        if i >= 20 {
            break;
        }
        let Ok(line) = line else { continue };
        let Ok(entry) = serde_json::from_str::<Value>(line.trim()) else {
            continue;
        };
        for key in &["parentSessionId", "parent_session_id", "parent_session"] {
            if let Some(val) = entry[key].as_str() {
                if !val.is_empty() {
                    return Some(val.to_string());
                }
            }
        }
    }
    None
}

// ── Internal helpers ──────────────────────────────────────────────────────────

fn default_claude_root() -> Option<PathBuf> {
    dirs::home_dir().map(|h| h.join(".claude").join("projects"))
}

fn make_session(
    path: PathBuf,
    parent_id: Option<String>,
    project_hash: &str,
) -> Option<ClaudeSession> {
    let meta = path.metadata().ok()?;
    if meta.len() < MIN_SESSION_BYTES {
        return None;
    }
    let mtime = meta
        .modified()
        .ok()
        .and_then(|t| t.duration_since(UNIX_EPOCH).ok())
        .map(|d| d.as_secs_f64())
        .unwrap_or(0.0);
    let id = path
        .file_stem()
        .and_then(|s| s.to_str())
        .unwrap_or("")
        .to_string();
    let title = peek_title(&path);
    Some(ClaudeSession {
        id,
        path,
        mtime,
        size: meta.len(),
        title,
        parent_id,
        project_hash: project_hash.to_string(),
    })
}

pub fn truncate_chars(s: &str, max: usize) -> String {
    if s.chars().count() <= max {
        s.to_string()
    } else {
        s.chars().take(max).collect()
    }
}

// ── Unit tests ────────────────────────────────────────────────────────────────

#[cfg(test)]
mod tests {
    use super::*;

    // ── Noise filter ──────────────────────────────────────────────────────────

    fn note_event() -> ClaudeEvent {
        ClaudeEvent {
            session_id: "s".into(),
            event_id: 0,
            kind: "note",
            content: "x".into(),
            tool_name: None,
        }
    }
    fn sys_event(content: &str) -> ClaudeEvent {
        ClaudeEvent {
            session_id: "s".into(),
            event_id: 0,
            kind: "system",
            content: content.into(),
            tool_name: None,
        }
    }
    fn user_event(content: &str) -> ClaudeEvent {
        ClaudeEvent {
            session_id: "s".into(),
            event_id: 0,
            kind: "user_msg",
            content: content.into(),
            tool_name: None,
        }
    }

    #[test]
    fn note_is_always_noise() {
        assert!(is_noise(&note_event()));
    }

    #[test]
    fn user_msg_is_never_noise() {
        assert!(!is_noise(&user_event("Hello world")));
    }

    #[test]
    fn system_xml_context_is_noise() {
        assert!(is_noise(&sys_event("<context>some context here</context>")));
    }

    #[test]
    fn system_you_are_claude_is_noise() {
        assert!(is_noise(&sys_event(
            "You are Claude, an AI assistant made by Anthropic."
        )));
    }

    #[test]
    fn system_the_assistant_is_claude_is_noise() {
        assert!(is_noise(&sys_event(
            "The assistant is Claude, made by Anthropic."
        )));
    }

    #[test]
    fn system_here_are_instructions_is_noise() {
        assert!(is_noise(&sys_event(
            "Here are some instructions for this session."
        )));
    }

    #[test]
    fn system_normal_content_is_not_noise() {
        assert!(!is_noise(&sys_event("The build failed with exit code 1.")));
    }

    // ── Event decomposition ───────────────────────────────────────────────────

    #[test]
    fn decompose_user_string_content() {
        let entry: Value = serde_json::json!({
            "type": "user",
            "message": { "content": "Hello there" }
        });
        let events = decompose_entry("user", &entry, "sess-1", 0);
        assert_eq!(events.len(), 1);
        assert_eq!(events[0].kind, "user_msg");
        assert_eq!(events[0].content, "Hello there");
        assert_eq!(events[0].event_id, 0);
    }

    #[test]
    fn decompose_assistant_tool_use_and_text() {
        let entry: Value = serde_json::json!({
            "type": "assistant",
            "message": {
                "content": [
                    { "type": "tool_use", "name": "Read", "input": { "file_path": "src/main.rs" } },
                    { "type": "text", "text": "Reading the file now." }
                ]
            }
        });
        let events = decompose_entry("assistant", &entry, "sess-1", 0);
        assert_eq!(events.len(), 2);
        assert_eq!(events[0].kind, "tool_call");
        assert_eq!(events[0].tool_name.as_deref(), Some("Read"));
        assert!(events[0].content.contains("src/main.rs"));
        assert_eq!(events[1].kind, "assistant_msg");
        assert_eq!(events[1].event_id, 1);
    }

    #[test]
    fn decompose_user_tool_result_block() {
        let entry: Value = serde_json::json!({
            "type": "user",
            "message": {
                "content": [
                    { "type": "tool_result", "tool_use_id": "tool-123", "content": "file content here" }
                ]
            }
        });
        let events = decompose_entry("user", &entry, "sess-1", 0);
        assert_eq!(events.len(), 1);
        assert_eq!(events[0].kind, "tool_result");
        assert_eq!(events[0].tool_name.as_deref(), Some("tool-123"));
    }

    #[test]
    fn decompose_system_entry() {
        let entry: Value = serde_json::json!({
            "type": "system",
            "message": { "content": "Shell is active in /project" }
        });
        let events = decompose_entry("system", &entry, "sess-1", 0);
        assert_eq!(events.len(), 1);
        assert_eq!(events[0].kind, "system");
    }

    #[test]
    fn decompose_unknown_type_yields_nothing() {
        let entry: Value = serde_json::json!({ "type": "queue-operation", "data": {} });
        let events = decompose_entry("queue-operation", &entry, "sess-1", 0);
        assert!(events.is_empty());
    }

    #[test]
    fn truncate_chars_truncates_correctly() {
        let s = "abcde";
        assert_eq!(truncate_chars(s, 3), "abc");
        assert_eq!(truncate_chars(s, 10), "abcde");
    }

    // ── Byte-offset iterator ──────────────────────────────────────────────────

    #[test]
    fn iter_events_with_offset_tracks_byte_offsets() {
        use std::io::Write;
        // Write a minimal 2-line JSONL to a temp-like named path in cwd.
        let path = std::path::PathBuf::from("target").join("test_byte_offsets.jsonl");
        // Ensure target dir exists.
        let _ = std::fs::create_dir_all("target");
        {
            let mut f = std::fs::File::create(&path).unwrap();
            writeln!(f, r#"{{"type":"user","message":{{"content":"hello"}}}}"#).unwrap();
            writeln!(f, r#"{{"type":"assistant","message":{{"content":[{{"type":"text","text":"world"}}]}}}}"#).unwrap();
        }
        let session = ClaudeSession {
            id: "test-sess".into(),
            path: path.clone(),
            mtime: 0.0,
            size: 1025,
            title: None,
            parent_id: None,
            project_hash: "ph".into(),
        };
        let provider = ClaudeProvider {
            root: PathBuf::from("."),
        };
        let pairs = provider.iter_events_with_offset(&session);
        assert!(!pairs.is_empty(), "should yield at least 1 event");
        // First event must be at offset 0.
        assert_eq!(pairs[0].1, 0, "first event byte offset should be 0");
        // Second JSONL line offset > 0
        if pairs.len() > 1 {
            assert!(
                pairs.last().unwrap().1 > 0,
                "later event should have non-zero offset"
            );
        }
        let _ = std::fs::remove_file(&path);
    }
}
