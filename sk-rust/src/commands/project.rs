use std::collections::HashSet;
use std::fs;
use std::path::{Path, PathBuf};
use std::process::ExitCode;

use serde::{Deserialize, Serialize};
use serde_json::Value;

use crate::config::resolve_home_dir;

#[derive(Debug, Deserialize, Serialize)]
struct ProjectEntry {
    name: String,
    path: String,
    created_at: Option<String>,
    session_state: String,
    db_path: String,
}

#[derive(Debug, Clone)]
enum RawProjectEntry {
    Path(String),
    Object(serde_json::Map<String, Value>),
}

pub fn run_project_command(args: &[String]) -> Option<ExitCode> {
    let subcommand = args.first().map(|s| s.as_str());
    if subcommand != Some("list") {
        return None;
    }

    let mut json_output = false;
    for arg in args.get(1..).unwrap_or(&[]) {
        match arg.as_str() {
            "--json" => json_output = true,
            "-h" | "--help" => return None,
            _ => return None,
        }
    }

    let raw = load_raw_registry();
    if !uses_string_schema(&raw) {
        return None;
    }

    Some(cmd_list(json_output, load_deduped_registry(raw)))
}

fn cmd_list(json_output: bool, deduped: Vec<RawProjectEntry>) -> ExitCode {
    if deduped.is_empty() {
        if json_output {
            println!("[]");
        } else {
            println!("no projects registered");
        }
        return ExitCode::SUCCESS;
    }

    if json_output {
        let normalized: Vec<ProjectEntry> = deduped.iter().map(normalize_entry).collect();
        match serde_json::to_string_pretty(&normalized) {
            Ok(payload) => {
                println!("{payload}");
                ExitCode::SUCCESS
            }
            Err(e) => {
                eprintln!("sk project list: failed to serialize registry: {e}");
                ExitCode::from(1)
            }
        }
    } else {
        for entry in &deduped {
            let normalized = normalize_entry(entry);
            let mut details = Vec::new();
            if !normalized.name.is_empty() {
                details.push(normalized.name);
            }
            if let Some(added) = normalized.created_at {
                if !added.is_empty() {
                    details.push(format!("added {added}"));
                }
            }
            if !normalized.db_path.is_empty() {
                details.push(format!("db {}", normalized.db_path));
            }

            if details.is_empty() {
                println!("{}", normalized.path);
            } else {
                println!("{}  ({})", normalized.path, details.join(", "));
            }
        }
        ExitCode::SUCCESS
    }
}

fn registry_path() -> Option<PathBuf> {
    resolve_home_dir().map(|home| {
        home.join(".copilot")
            .join("session-state")
            .join("tools-managed-projects.json")
    })
}

fn load_raw_registry() -> Vec<RawProjectEntry> {
    let Some(path) = registry_path() else {
        return Vec::new();
    };
    let Ok(content) = fs::read_to_string(path) else {
        return Vec::new();
    };
    let Ok(Value::Object(root)) = serde_json::from_str::<Value>(&content) else {
        return Vec::new();
    };
    let Some(Value::Array(projects)) = root.get("projects") else {
        return Vec::new();
    };

    projects
        .iter()
        .filter_map(|entry| match entry {
            Value::String(path) => Some(RawProjectEntry::Path(path.clone())),
            Value::Object(obj) => Some(RawProjectEntry::Object(obj.clone())),
            _ => None,
        })
        .collect()
}

fn uses_string_schema(entries: &[RawProjectEntry]) -> bool {
    entries.iter().all(|entry| match entry {
        RawProjectEntry::Path(_) => true,
        RawProjectEntry::Object(obj) => ["path", "name", "created_at", "session_state", "db_path"]
            .iter()
            .all(|key| {
                obj.get(*key)
                    .map_or(true, |value| value.is_null() || value.is_string())
            }),
    })
}

fn load_deduped_registry(raw: Vec<RawProjectEntry>) -> Vec<RawProjectEntry> {
    let mut seen = HashSet::new();
    let mut result = Vec::new();
    for entry in raw {
        let path = entry_path(&entry);
        if path.is_empty() || !seen.insert(path) {
            continue;
        }
        result.push(entry);
    }
    result
}

fn entry_path(entry: &RawProjectEntry) -> String {
    match entry {
        RawProjectEntry::Path(path) => path.clone(),
        RawProjectEntry::Object(obj) => obj
            .get("path")
            .and_then(Value::as_str)
            .unwrap_or_default()
            .to_string(),
    }
}

fn normalize_entry(entry: &RawProjectEntry) -> ProjectEntry {
    let path = entry_path(entry);
    let (mut name, created_at, mut session_state, mut db_path) = match entry {
        RawProjectEntry::Path(_) => (String::new(), None, String::new(), String::new()),
        RawProjectEntry::Object(obj) => (
            string_field(obj, "name"),
            optional_string_field(obj, "created_at"),
            string_field(obj, "session_state"),
            string_field(obj, "db_path"),
        ),
    };

    if !path.is_empty() {
        let root = Path::new(&path);
        if name.is_empty() {
            name = root
                .file_name()
                .map(|s| s.to_string_lossy().to_string())
                .unwrap_or_default();
        }
        if session_state.is_empty() {
            session_state = project_session_state(root);
        }
        if db_path.is_empty() {
            db_path = project_db_path(root);
        }
    }

    ProjectEntry {
        name,
        path,
        created_at,
        session_state,
        db_path,
    }
}

fn string_field(obj: &serde_json::Map<String, Value>, key: &str) -> String {
    obj.get(key)
        .and_then(Value::as_str)
        .unwrap_or_default()
        .to_string()
}

fn optional_string_field(obj: &serde_json::Map<String, Value>, key: &str) -> Option<String> {
    obj.get(key)
        .and_then(Value::as_str)
        .map(std::string::ToString::to_string)
}

fn project_session_state(project_root: &Path) -> String {
    project_root
        .join(".copilot")
        .join("session-state")
        .to_string_lossy()
        .to_string()
}

fn project_db_path(project_root: &Path) -> String {
    project_root
        .join(".copilot")
        .join("session-state")
        .join("knowledge.db")
        .to_string_lossy()
        .to_string()
}
