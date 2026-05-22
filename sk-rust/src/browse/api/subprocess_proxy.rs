//! Shared subprocess-proxy helper for browse API endpoints (issue #453 PR-B).
//!
//! Spawns a Python script via `tokio::process::Command` (no shell interpolation),
//! captures stdout/stderr, enforces a configurable timeout, and returns a
//! structured result.
//!
//! # Python discovery
//!
//! Probes in order:
//! 1. `COPILOT_PYTHON` env var (explicit override).
//! 2. On Windows: `py` with arg `-3` (Python Launcher for Windows).
//! 3. `python3`.
//! 4. `python`.

#![cfg(feature = "browse-server")]

use std::path::Path;
use std::time::Duration;

use axum::http::StatusCode;
use axum::response::{IntoResponse, Json, Response};
use serde_json::{json, Value};

/// Default subprocess timeout.
pub const DEFAULT_TIMEOUT: Duration = Duration::from_secs(30);

// ── Python interpreter discovery ──────────────────────────────────────────────

/// A resolved Python interpreter with optional prefix arguments (e.g. `-3` for `py`).
pub struct PythonInterpreter {
    pub exe: String,
    pub prepend_args: Vec<String>,
}

impl PythonInterpreter {
    fn new(exe: impl Into<String>, prepend_args: Vec<String>) -> Self {
        Self {
            exe: exe.into(),
            prepend_args,
        }
    }

    /// Build a `tokio::process::Command` for running a script.
    pub fn command(&self, script: &Path, args: &[&str]) -> tokio::process::Command {
        let mut cmd = tokio::process::Command::new(&self.exe);
        for a in &self.prepend_args {
            cmd.arg(a);
        }
        cmd.arg(script);
        for a in args {
            cmd.arg(a);
        }
        cmd
    }
}

/// Probe whether an interpreter is available by running `--version`.
async fn probe(exe: &str, prepend: &[&str]) -> bool {
    let mut cmd = tokio::process::Command::new(exe);
    for a in prepend {
        cmd.arg(a);
    }
    cmd.arg("--version");
    cmd.stdout(std::process::Stdio::null());
    cmd.stderr(std::process::Stdio::null());
    match cmd.spawn() {
        Ok(mut child) => {
            let _ = child.wait().await;
            true
        }
        Err(_) => false,
    }
}

/// Resolve the best available Python interpreter on the current platform.
///
/// Returns `None` when no interpreter can be found.
pub async fn resolve_python() -> Option<PythonInterpreter> {
    // 1. Explicit override via COPILOT_PYTHON.
    if let Ok(python_exe) = std::env::var("COPILOT_PYTHON") {
        if !python_exe.is_empty() {
            return Some(PythonInterpreter::new(python_exe, vec![]));
        }
    }

    // 2. On Windows: try `py -3`.
    #[cfg(windows)]
    {
        if probe("py", &["-3"]).await {
            return Some(PythonInterpreter::new("py", vec!["-3".to_string()]));
        }
    }

    // 3. `python3`
    if probe("python3", &[]).await {
        return Some(PythonInterpreter::new("python3", vec![]));
    }

    // 4. `python`
    if probe("python", &[]).await {
        return Some(PythonInterpreter::new("python", vec![]));
    }

    None
}

// ── Error type ────────────────────────────────────────────────────────────────

/// Errors returned by [`run_python_script`].
#[derive(Debug)]
pub enum SubprocessError {
    /// Script or interpreter not available (503-worthy).
    Unavailable(String),
    /// Script exited with a non-zero exit code.
    NonZeroExit { code: i32, stderr_tail: String },
    /// Script did not exit within the timeout.
    Timeout,
    /// Script output was not valid JSON.
    InvalidJson(String),
    /// Script output was valid JSON but not an object.
    NotObject(String),
    /// The process could not be spawned (non-NotFound OS error).
    SpawnError(String),
}

// ── Output type ───────────────────────────────────────────────────────────────

/// Successful output from [`run_python_script`].
#[derive(Debug)]
pub struct SubprocessOutput {
    pub data: serde_json::Map<String, Value>,
}

// ── Core runner ───────────────────────────────────────────────────────────────

/// Run a Python script and return its parsed JSON object output.
///
/// # Arguments
///
/// - `script`      — path to the `.py` file (must exist).
/// - `args`        — additional CLI arguments passed after the script path.
/// - `cwd`         — working directory for the child process (optional).
/// - `timeout_dur` — maximum time allowed before returning `Timeout`.
pub async fn run_python_script(
    script: &Path,
    args: &[&str],
    cwd: Option<&Path>,
    timeout_dur: Duration,
) -> Result<SubprocessOutput, SubprocessError> {
    // Check the script exists.
    if !script.exists() {
        return Err(SubprocessError::Unavailable(format!(
            "script not found: {}",
            script.display()
        )));
    }

    // Resolve Python.
    let interp = resolve_python()
        .await
        .ok_or_else(|| SubprocessError::Unavailable("no Python interpreter found".to_string()))?;

    // Build the command.
    let mut cmd = interp.command(script, args);
    cmd.stdout(std::process::Stdio::piped());
    cmd.stderr(std::process::Stdio::piped());
    cmd.env("PYTHONIOENCODING", "utf-8");
    cmd.env("PYTHONUTF8", "1");
    if let Some(dir) = cwd {
        cmd.current_dir(dir);
    }
    cmd.kill_on_drop(true);

    // Spawn.
    let child = cmd.spawn().map_err(|e| {
        if e.kind() == std::io::ErrorKind::NotFound {
            SubprocessError::Unavailable(format!("interpreter not found: {}", interp.exe))
        } else {
            SubprocessError::SpawnError(e.to_string())
        }
    })?;

    // Drive to completion with a timeout.
    let handle = tokio::spawn(async move { child.wait_with_output().await });

    let output = tokio::select! {
        result = handle => {
            result
                .map_err(|e| SubprocessError::SpawnError(e.to_string()))?
                .map_err(|e| SubprocessError::SpawnError(e.to_string()))?
        }
        _ = tokio::time::sleep(timeout_dur) => {
            return Err(SubprocessError::Timeout);
        }
    };

    // Non-zero exit.
    if !output.status.success() {
        let code = output.status.code().unwrap_or(-1);
        let stderr_str = String::from_utf8_lossy(&output.stderr).into_owned();
        let stderr_tail = stderr_tail_str(&stderr_str, 512);
        return Err(SubprocessError::NonZeroExit { code, stderr_tail });
    }

    // Parse stdout as JSON.
    let stdout_str = String::from_utf8_lossy(&output.stdout).into_owned();
    let parsed: Value = serde_json::from_str(stdout_str.trim())
        .map_err(|e| SubprocessError::InvalidJson(format!("invalid JSON from script: {e}")))?;

    // Must be a JSON object.
    match parsed {
        Value::Object(map) => Ok(SubprocessOutput { data: map }),
        _ => Err(SubprocessError::NotObject(
            "output was not a JSON object".to_string(),
        )),
    }
}

// ── Error → HTTP response ─────────────────────────────────────────────────────

/// Convert a [`SubprocessError`] into an Axum [`Response`].
///
/// Callers supply the error-code strings for each variant so that each
/// endpoint can use domain-specific codes.
pub fn subprocess_error_response(
    err: SubprocessError,
    unavailable_code: &str,
    error_code: &str,
    timeout_code: &str,
    parse_code: &str,
) -> Response {
    match err {
        SubprocessError::Unavailable(msg) => (
            StatusCode::SERVICE_UNAVAILABLE,
            Json(json!({ "error": msg, "code": unavailable_code })),
        )
            .into_response(),

        SubprocessError::NonZeroExit { code, stderr_tail } => (
            StatusCode::BAD_GATEWAY,
            Json(json!({
                "error": format!("script exited with code {code}"),
                "code": error_code,
                "stderr": stderr_tail,
            })),
        )
            .into_response(),

        SubprocessError::Timeout => (
            StatusCode::BAD_GATEWAY,
            Json(json!({ "error": "script timed out", "code": timeout_code })),
        )
            .into_response(),

        SubprocessError::InvalidJson(msg) => (
            StatusCode::BAD_GATEWAY,
            Json(json!({ "error": msg, "code": parse_code })),
        )
            .into_response(),

        SubprocessError::NotObject(msg) => (
            StatusCode::BAD_GATEWAY,
            Json(json!({ "error": msg, "code": parse_code })),
        )
            .into_response(),

        SubprocessError::SpawnError(msg) => (
            StatusCode::BAD_GATEWAY,
            Json(json!({ "error": msg, "code": error_code })),
        )
            .into_response(),
    }
}

// ── Helpers ───────────────────────────────────────────────────────────────────

/// Return the tail of `s` that fits within `max_bytes` UTF-8 bytes,
/// aligned on a character boundary.
fn stderr_tail_str(s: &str, max_bytes: usize) -> String {
    if s.len() <= max_bytes {
        return s.to_string();
    }
    let start = s.len() - max_bytes;
    // Walk forward to find the next char boundary.
    let aligned = (start..=s.len())
        .find(|&i| s.is_char_boundary(i))
        .unwrap_or(s.len());
    s[aligned..].to_string()
}

// ── Unit tests ────────────────────────────────────────────────────────────────

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn test_stderr_tail_short_string() {
        let s = "hello";
        assert_eq!(stderr_tail_str(s, 512), "hello");
    }

    #[test]
    fn test_stderr_tail_exact_length() {
        let s = "a".repeat(512);
        assert_eq!(stderr_tail_str(&s, 512), s);
    }

    #[test]
    fn test_stderr_tail_truncates() {
        let s = "a".repeat(600);
        let tail = stderr_tail_str(&s, 512);
        assert_eq!(tail.len(), 512);
        assert!(tail.chars().all(|c| c == 'a'));
    }

    #[test]
    fn test_stderr_tail_unicode_boundary() {
        // 200 × '€' (3 bytes each) = 600 bytes total.
        let s: String = "€".repeat(200);
        assert_eq!(s.len(), 600);
        let tail = stderr_tail_str(&s, 512);
        // Must be valid UTF-8 (char-boundary aligned).
        assert!(std::str::from_utf8(tail.as_bytes()).is_ok());
        // Must not be longer than 512 bytes.
        assert!(tail.len() <= 512);
    }

    #[test]
    fn test_stderr_tail_empty() {
        assert_eq!(stderr_tail_str("", 512), "");
    }
}
