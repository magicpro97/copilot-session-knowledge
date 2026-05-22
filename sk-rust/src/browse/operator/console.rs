//! Operator console — session CRUD, path confinement and model normalisation
//! (issue #451 PR-A).
//!
//! Design invariants (mirroring Python operator_console.py):
//! - Sessions live at `state_dir()/sessions/{uuid}.json`.
//! - JSON file writes are atomic: write to `.tmp` then rename.
//! - Corrupt JSON on list is skipped with `tracing::warn!`.
//! - `list_sessions()` returns newest-first by `created_at`.
//! - `confine_path` rejects empty, outside-home, traversal, symlink/junction escapes.
//! - `normalize_model_id` converts hyphenated version suffixes to dotted form.
//! - POST accepts any mode; PATCH validates against `interactive`, `plan`, `autopilot`.
//! - `has_active_run` is dormant for PR-A — TODO(#451 PR-B).

use std::fs;
use std::io;
use std::path::{Component, Path, PathBuf};

use chrono::Utc;
use regex::Regex;
use serde::{Deserialize, Serialize};
use uuid::Uuid;

// ── Session struct ─────────────────────────────────────────────────────────────

/// A browser-managed Copilot operator session persisted to disk as JSON.
#[derive(Serialize, Deserialize, Clone, Debug)]
pub struct Session {
    pub id: String,
    pub name: String,
    pub model: String,
    pub mode: String,
    pub workspace: String,
    pub add_dirs: Vec<String>,
    pub created_at: String,
    pub updated_at: String,
    pub run_count: u64,
    pub last_run_id: Option<String>,
    pub resume_ready: bool,
}

// ── UUID4 validation ───────────────────────────────────────────────────────────

fn uuid4_re() -> &'static Regex {
    use std::sync::OnceLock;
    static RE: OnceLock<Regex> = OnceLock::new();
    RE.get_or_init(|| {
        Regex::new(r"^[a-f0-9]{8}-[a-f0-9]{4}-4[a-f0-9]{3}-[89ab][a-f0-9]{3}-[a-f0-9]{12}$")
            .expect("uuid4 regex is valid")
    })
}

/// Return `true` if `s` is a well-formed UUID v4 string (lowercase hex).
pub(crate) fn is_valid_uuid4(s: &str) -> bool {
    !s.is_empty() && uuid4_re().is_match(s)
}

// ── Model normalisation ────────────────────────────────────────────────────────

fn model_alias_re() -> &'static Regex {
    use std::sync::OnceLock;
    static RE: OnceLock<Regex> = OnceLock::new();
    RE.get_or_init(|| {
        Regex::new(r"^((?:claude-(?:sonnet|opus|haiku)|gpt)-\d)-(\d{1,2})((?:-.+)?)$")
            .expect("model alias regex is valid")
    })
}

/// Normalise a legacy hyphenated model version suffix to the dotted form.
///
/// e.g. `claude-sonnet-4-6` → `claude-sonnet-4.6`
pub fn normalize_model_id(model: &str) -> String {
    let s = model.trim();
    if s.is_empty() {
        return String::new();
    }
    if let Some(caps) = model_alias_re().captures(s) {
        return format!("{}.{}{}", &caps[1], &caps[2], &caps[3]);
    }
    s.to_string()
}

// ── State directories ──────────────────────────────────────────────────────────

/// Return the operator-console state root.
///
/// Honours the `COPILOT_OPERATOR_STATE` env var; falls back to
/// `~/.copilot/session-state/operator-console`.
pub fn state_dir() -> PathBuf {
    if let Ok(env) = std::env::var("COPILOT_OPERATOR_STATE") {
        if !env.trim().is_empty() {
            return PathBuf::from(env.trim());
        }
    }
    dirs::home_dir()
        .unwrap_or_else(|| PathBuf::from("."))
        .join(".copilot")
        .join("session-state")
        .join("operator-console")
}

/// Return (and create) the sessions sub-directory.
pub fn sessions_dir() -> io::Result<PathBuf> {
    let d = state_dir().join("sessions");
    fs::create_dir_all(&d)?;
    Ok(d)
}

// ── Path confinement ───────────────────────────────────────────────────────────

/// Resolve `raw` and return `Some(path)` only if the result is strictly under
/// the user's home directory.  Returns `None` for:
/// - empty strings
/// - paths that resolve outside `~`
/// - traversal attempts (`../`)
/// - symlinks / junctions that escape home
///
/// Mirrors Python `Path.expanduser().resolve(strict=False).relative_to(home)`:
/// - tilde expansion handled explicitly
/// - `..` components normalised before canonicalisation
/// - lenient resolution: for partly-nonexistent paths the deepest existing
///   ancestor is canonicalised and the non-existent suffix is re-appended
pub fn confine_path(raw: &str) -> Option<PathBuf> {
    let raw = raw.trim();
    if raw.is_empty() {
        return None;
    }

    let home = dirs::home_dir()?;
    let home_canon = lenient_canonicalize(&home)?;

    let expanded = expand_tilde(raw, &home)?;

    // Normalise away `.` and `..` before canonicalisation to block traversal
    // via non-existent-suffix paths (e.g. `~/nonexistent/../../etc/passwd`).
    let normalized = normalize_path_components(&expanded);

    // Lenient canonicalise (follows symlinks for existing components, handles
    // partly-nonexistent paths).
    let resolved = lenient_canonicalize(&normalized)?;

    // Must be strictly under home — symlink following already done above.
    resolved.strip_prefix(&home_canon).ok()?;

    Some(resolved)
}

/// Expand a leading `~` to the supplied `home` path.
/// Returns `None` for `~username` forms (not supported).
/// On Windows, `~\subdir` is accepted in addition to `~/subdir`.
fn expand_tilde(raw: &str, home: &Path) -> Option<PathBuf> {
    if raw == "~" {
        Some(home.to_path_buf())
    } else if let Some(rest) = raw.strip_prefix("~/") {
        Some(home.join(rest))
    } else if cfg!(windows) && raw.starts_with(r"~\") {
        // Windows backslash separator: `~\subdir` → `home\subdir`
        Some(home.join(&raw[2..]))
    } else if raw.starts_with('~') {
        // ~user form — not supported
        None
    } else {
        // Absolute or relative path: pass through; relative paths are
        // resolved against CWD just as Python's Path(raw).resolve() does.
        let p = PathBuf::from(raw);
        if p.is_absolute() {
            Some(p)
        } else {
            std::env::current_dir().ok().map(|cwd| cwd.join(raw))
        }
    }
}

/// Normalise path components by resolving `.` and `..` lexically,
/// without touching the filesystem.
fn normalize_path_components(path: &Path) -> PathBuf {
    let mut parts: Vec<Component> = Vec::new();
    for component in path.components() {
        match component {
            Component::CurDir => {}
            Component::ParentDir => match parts.last() {
                Some(Component::Prefix(_)) | Some(Component::RootDir) => {
                    // Cannot go above root/prefix — ignore the `..`.
                }
                Some(Component::ParentDir) | None => {
                    // Relative path going above start — keep `..`
                    parts.push(component);
                }
                Some(_) => {
                    parts.pop();
                }
            },
            other => parts.push(other),
        }
    }
    parts.iter().collect()
}

/// Canonicalise `path`, handling partly-nonexistent paths by walking up to the
/// deepest existing ancestor, canonicalising it (following any symlinks), then
/// re-appending the non-existent suffix.
///
/// Returns `None` only when no existing ancestor can be found at all.
fn lenient_canonicalize(path: &Path) -> Option<PathBuf> {
    // Fast path: full canonicalization works when path exists.
    if let Ok(c) = dunce::canonicalize(path) {
        return Some(c);
    }

    // Walk up the path to find the deepest existing ancestor.
    let mut existing = path.to_path_buf();
    let mut suffix = PathBuf::new();

    while let Some(parent) = existing.parent() {
        if parent == existing {
            break;
        }
        let Some(file_name) = existing.file_name() else {
            break;
        };
        let file_name = file_name.to_owned();
        // Prepend file_name to suffix
        let new_suffix = PathBuf::from(&file_name).join(&suffix);
        suffix = new_suffix;
        existing = parent.to_path_buf();

        if existing.exists() {
            break;
        }
    }

    if !existing.exists() {
        return None;
    }

    let canon = dunce::canonicalize(&existing).ok()?;
    if suffix.as_os_str().is_empty() {
        Some(canon)
    } else {
        Some(canon.join(&suffix))
    }
}

// ── Error types ────────────────────────────────────────────────────────────────

/// Error codes returned by `update_session`.
#[derive(Debug, PartialEq, Eq)]
pub enum UpdateError {
    NotFound,
    BadMode,
    ActiveRun,
}

// ── Session CRUD ───────────────────────────────────────────────────────────────

/// Parameters for creating a new session.
pub struct CreateSessionParams {
    pub name: String,
    pub model: String,
    /// Accepted verbatim (any non-empty trimmed string ≤ 64 chars).
    pub mode: String,
    pub workspace: String,
    pub add_dirs: Vec<String>,
}

/// Create and persist a new operator session.
///
/// Returns `Err(message)` if `workspace` or any `add_dir` is outside `~/`.
pub fn create_session(params: CreateSessionParams) -> Result<Session, String> {
    let id = Uuid::new_v4().to_string();
    let now = Utc::now().to_rfc3339_opts(chrono::SecondsFormat::Micros, true);

    let ws_path = if !params.workspace.trim().is_empty() {
        confine_path(&params.workspace)
            .map(|p| p.to_string_lossy().into_owned())
            .ok_or_else(|| format!("workspace path '{}' is not under ~/", params.workspace))?
    } else {
        String::new()
    };

    let mut validated_dirs = Vec::new();
    for d in &params.add_dirs {
        let d = d.trim();
        if d.is_empty() {
            continue;
        }
        let p = confine_path(d).ok_or_else(|| format!("add_dir path '{d}' is not under ~/"))?;
        validated_dirs.push(p.to_string_lossy().into_owned());
    }

    let session = Session {
        id: id.clone(),
        name: params.name.trim().chars().take(128).collect(),
        model: normalize_model_id(&params.model).chars().take(64).collect(),
        mode: params.mode.trim().chars().take(64).collect(),
        workspace: ws_path,
        add_dirs: validated_dirs,
        created_at: now.clone(),
        updated_at: now,
        run_count: 0,
        last_run_id: None,
        resume_ready: false,
    };

    let dir = sessions_dir().map_err(|e| e.to_string())?;
    write_json_atomic(&dir.join(format!("{id}.json")), &session).map_err(|e| e.to_string())?;

    Ok(session)
}

/// List all sessions, newest-first by `created_at`.
///
/// Corrupt JSON files are skipped with a `tracing::warn!` log.
pub fn list_sessions() -> Vec<Session> {
    let dir = match sessions_dir() {
        Ok(d) => d,
        Err(_) => return vec![],
    };

    let entries = match fs::read_dir(&dir) {
        Ok(e) => e,
        Err(_) => return vec![],
    };

    let mut sessions = Vec::new();
    for entry in entries.flatten() {
        let path = entry.path();
        if path.extension().and_then(|s| s.to_str()) != Some("json") {
            continue;
        }
        match read_session_file(&path) {
            Ok(Some(s)) => sessions.push(s),
            Ok(None) => {}
            Err(_) => {
                tracing::warn!(
                    "operator: skipping corrupt session file: {}",
                    path.display()
                );
            }
        }
    }

    sessions.sort_by(|a, b| b.created_at.cmp(&a.created_at));
    sessions
}

/// Load a session by ID.  Returns `None` if the ID is invalid or the file is
/// missing/corrupt.
pub fn get_session(session_id: &str) -> Option<Session> {
    if !is_valid_uuid4(session_id) {
        return None;
    }
    let dir = sessions_dir().ok()?;
    let path = dir.join(format!("{session_id}.json"));
    read_session_file(&path).ok().flatten()
}

/// Delete a session file.  Returns `true` if the file existed and was removed.
pub fn delete_session(session_id: &str) -> bool {
    if !is_valid_uuid4(session_id) {
        return false;
    }
    let dir = match sessions_dir() {
        Ok(d) => d,
        Err(_) => return false,
    };
    let path = dir.join(format!("{session_id}.json"));
    if !path.exists() {
        return false;
    }
    fs::remove_file(&path).is_ok()
}

/// Update mutable fields on an existing session.
///
/// - `name`  — optional; stored after trim + truncation to 128 chars.
/// - `model` — optional; normalised via `normalize_model_id`.
/// - `mode`  — optional; **validated** against `interactive | plan | autopilot`.
///
/// Returns the updated session on success, or an `UpdateError` variant.
pub fn update_session(
    session_id: &str,
    name: Option<&str>,
    model: Option<&str>,
    mode: Option<&str>,
) -> Result<Session, UpdateError> {
    if !is_valid_uuid4(session_id) {
        return Err(UpdateError::NotFound);
    }

    let mut session = get_session(session_id).ok_or(UpdateError::NotFound)?;

    // Dormant for PR-A; will check in-memory registry in PR-B.
    if has_active_run(session_id) {
        return Err(UpdateError::ActiveRun);
    }

    // Validate mode before mutating (PATCH-only restriction).
    if let Some(m) = mode {
        let m = m.trim();
        if !matches!(m, "interactive" | "plan" | "autopilot") {
            return Err(UpdateError::BadMode);
        }
    }

    let now = Utc::now().to_rfc3339_opts(chrono::SecondsFormat::Micros, true);

    if let Some(n) = name {
        session.name = n.trim().chars().take(128).collect();
    }
    if let Some(m) = model {
        session.model = normalize_model_id(m.trim()).chars().take(64).collect();
    }
    if let Some(m) = mode {
        session.mode = m.trim().chars().take(64).collect();
    }
    session.updated_at = now;

    let dir = sessions_dir().map_err(|_| UpdateError::NotFound)?;
    write_json_atomic(&dir.join(format!("{session_id}.json")), &session)
        .map_err(|_| UpdateError::NotFound)?;

    Ok(session)
}

/// Check whether the session has a currently-active (non-terminal) run.
///
/// Dormant for PR-A — the in-memory run registry will be wired in PR-B.
// TODO(#451 PR-B): wire to the active-run registry.
fn has_active_run(_session_id: &str) -> bool {
    false
}

// ── I/O helpers ────────────────────────────────────────────────────────────────

fn read_session_file(path: &Path) -> io::Result<Option<Session>> {
    let text = match fs::read_to_string(path) {
        Ok(t) => t,
        Err(e) if e.kind() == io::ErrorKind::NotFound => return Ok(None),
        Err(e) => return Err(e),
    };
    let session: Session =
        serde_json::from_str(&text).map_err(|e| io::Error::new(io::ErrorKind::InvalidData, e))?;
    Ok(Some(session))
}

/// Atomically write `value` as pretty JSON to `path` via a `.tmp` rename.
fn write_json_atomic(path: &Path, value: &impl Serialize) -> io::Result<()> {
    let tmp = path.with_extension("tmp");
    let json = serde_json::to_string_pretty(value)
        .map_err(|e| io::Error::new(io::ErrorKind::InvalidInput, e))?;
    fs::write(&tmp, json.as_bytes())?;
    fs::rename(&tmp, path)?;
    Ok(())
}

// ── Unit tests ─────────────────────────────────────────────────────────────────

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn test_is_valid_uuid4_ok() {
        assert!(is_valid_uuid4("550e8400-e29b-41d4-a716-446655440000"));
        // variant byte must be [89ab]
        let id = Uuid::new_v4().to_string();
        assert!(is_valid_uuid4(&id));
    }

    #[test]
    fn test_is_valid_uuid4_rejects_bad() {
        assert!(!is_valid_uuid4(""));
        assert!(!is_valid_uuid4("not-a-uuid"));
        // version 3, not 4
        assert!(!is_valid_uuid4("550e8400-e29b-31d4-a716-446655440000"));
    }

    #[test]
    fn test_normalize_model_id_hyphenated() {
        assert_eq!(normalize_model_id("claude-sonnet-4-6"), "claude-sonnet-4.6");
        assert_eq!(normalize_model_id("claude-opus-4-7"), "claude-opus-4.7");
        assert_eq!(normalize_model_id("claude-haiku-4-5"), "claude-haiku-4.5");
        assert_eq!(normalize_model_id("gpt-4-1"), "gpt-4.1");
    }

    #[test]
    fn test_normalize_model_id_no_change() {
        assert_eq!(normalize_model_id("claude-sonnet-4.6"), "claude-sonnet-4.6");
        assert_eq!(normalize_model_id("gpt-4.1"), "gpt-4.1");
        assert_eq!(normalize_model_id(""), "");
    }

    #[test]
    fn test_normalize_model_id_with_suffix() {
        assert_eq!(
            normalize_model_id("claude-sonnet-4-5-20250219"),
            "claude-sonnet-4.5-20250219"
        );
    }

    #[test]
    fn test_confine_path_empty_returns_none() {
        assert!(confine_path("").is_none());
        assert!(confine_path("   ").is_none());
    }

    #[test]
    fn test_confine_path_tilde_home() {
        // `~` alone should resolve to home — which is under home.
        let result = confine_path("~");
        assert!(result.is_some(), "~ should resolve to home");
    }
}
