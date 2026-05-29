//! Retry-event listener hook — spawn an external script, parse its decision.
//!
//! Path resolution order:
//!   1. `$SK_RETRY_LISTENER` env var (absolute path to an existing file).
//!   2. `~/.copilot/hooks/sk-retry-listener.ps1` (Windows) or `.sh` (Unix).
//!
//! If no listener file exists the call is a silent no-op.
//!
//! The listener receives a JSON payload on stdin, and may respond on stdout with
//! `{"abort":true}` or `{"delay_override_seconds": N}`.  Any other output
//! (including empty or non-JSON) is treated as `ListenerDecision::Observe`.
//!
//! Timeout: 2 seconds.  After the timeout the child is left to finish in its own
//! time (fire-and-forget) and `listener_timeout: true` is set in the result.

// New module — public API not yet wired into a binary call site.
#![allow(dead_code)]

use std::env;
use std::io::{Read, Write};
use std::path::PathBuf;
use std::process::{Command, Stdio};
use std::sync::mpsc;
use std::thread;
use std::time::Duration;

use serde::Serialize;

/// Maximum bytes captured from the listener's stderr.
const MAX_STDERR_BYTES: u64 = 2048;
/// Hard timeout for waiting on the listener.
const LISTENER_TIMEOUT: Duration = Duration::from_secs(2);

// ── Public types ──────────────────────────────────────────────────────────────

/// JSON payload sent to the listener on stdin.
#[derive(Debug, Serialize)]
pub struct RetryListenerPayload {
    pub ts: String,
    pub hook: String,
    pub agent: String,
    pub attempt: u32,
    pub max_attempts: u32,
    pub detected_pattern: String,
    pub status_code: Option<u32>,
    pub retry_after_hint_seconds: Option<f64>,
    pub computed_delay_seconds: f64,
    pub delay_source: String,
    pub elapsed_total_seconds: f64,
    pub outcome: String,
}

/// Decision returned by the external listener.
#[derive(Debug)]
pub enum ListenerDecision {
    /// No opinion — proceed with the computed delay.
    Observe,
    /// Abort the retry sequence immediately.
    Abort,
    /// Use a custom delay in seconds (clamped to `[0.0, 300.0]`).
    DelayOverride(f64),
}

/// Result of `invoke_retry_listener`.
pub struct ListenerResult {
    pub decision: ListenerDecision,
    /// `true` when the listener did not respond within the 2-second window.
    pub listener_timeout: bool,
    /// Up to 2 KB of stderr from the listener process.
    pub listener_stderr: Option<String>,
    /// Resolved file path, for audit/logging.
    pub listener_path: Option<String>,
}

// ── Public entry point ────────────────────────────────────────────────────────

/// Invoke the external retry listener, if configured.
///
/// Fail-open: spawn errors, JSON parse errors, and timeouts all resolve to
/// `ListenerDecision::Observe` so the retry sequence continues unaffected.
pub fn invoke_retry_listener(payload: &RetryListenerPayload) -> ListenerResult {
    let noop = ListenerResult {
        decision: ListenerDecision::Observe,
        listener_timeout: false,
        listener_stderr: None,
        listener_path: None,
    };

    let listener_path = match resolve_listener_path() {
        Some(p) => p,
        None => return noop,
    };

    let path_str = listener_path.to_string_lossy().to_string();

    let payload_json = match serde_json::to_string(payload) {
        Ok(j) => j,
        Err(e) => {
            eprintln!("sk-retry-listener: payload serialization failed: {e}");
            return ListenerResult {
                decision: ListenerDecision::Observe,
                listener_timeout: false,
                listener_stderr: None,
                listener_path: Some(path_str),
            };
        }
    };

    let mut cmd = match build_command(&listener_path) {
        Some(c) => c,
        None => {
            return ListenerResult {
                decision: ListenerDecision::Observe,
                listener_timeout: false,
                listener_stderr: None,
                listener_path: Some(path_str),
            };
        }
    };

    // cwd → ~/.copilot/hooks/ (best-effort; fall back to current dir on error)
    if let Some(hooks_dir) = hooks_dir() {
        let _ = std::fs::create_dir_all(&hooks_dir);
        cmd.current_dir(&hooks_dir);
    }

    // Inject SK_RETRY_* environment snapshot.
    cmd.env("SK_RETRY_HOOK", &payload.hook);
    cmd.env("SK_RETRY_AGENT", &payload.agent);
    cmd.env("SK_RETRY_ATTEMPT", payload.attempt.to_string());
    cmd.env("SK_RETRY_MAX_ATTEMPTS", payload.max_attempts.to_string());
    cmd.env("SK_RETRY_DETECTED_PATTERN", &payload.detected_pattern);
    cmd.env(
        "SK_RETRY_COMPUTED_DELAY_SECONDS",
        payload.computed_delay_seconds.to_string(),
    );
    cmd.env(
        "SK_RETRY_ELAPSED_SECONDS",
        payload.elapsed_total_seconds.to_string(),
    );

    cmd.stdin(Stdio::piped())
        .stdout(Stdio::piped())
        .stderr(Stdio::piped());

    let mut child = match cmd.spawn() {
        Ok(c) => c,
        Err(e) => {
            eprintln!("sk-retry-listener: spawn error for {path_str}: {e}");
            return ListenerResult {
                decision: ListenerDecision::Observe,
                listener_timeout: false,
                listener_stderr: None,
                listener_path: Some(path_str),
            };
        }
    };

    // Write JSON payload to stdin, then close stdin (signals EOF to the child).
    if let Some(mut stdin) = child.stdin.take() {
        let _ = writeln!(stdin, "{payload_json}");
        // stdin dropped here → child receives EOF
    }

    let stdout_handle = child.stdout.take();
    let stderr_handle = child.stderr.take();

    // Background thread: read stdout + stderr, then wait for child exit.
    let (tx, rx) = mpsc::channel::<(String, String)>();
    thread::spawn(move || {
        let mut stdout_buf = String::new();
        let mut stderr_buf = String::new();
        if let Some(s) = stdout_handle {
            let _ = s.take(4096).read_to_string(&mut stdout_buf);
        }
        if let Some(s) = stderr_handle {
            let _ = s.take(MAX_STDERR_BYTES).read_to_string(&mut stderr_buf);
        }
        let _ = child.wait();
        let _ = tx.send((stdout_buf, stderr_buf));
    });

    match rx.recv_timeout(LISTENER_TIMEOUT) {
        Ok((stdout_str, stderr_str)) => {
            let decision = parse_stdout_decision(&stdout_str);
            let listener_stderr = if stderr_str.is_empty() {
                None
            } else {
                Some(stderr_str)
            };
            ListenerResult {
                decision,
                listener_timeout: false,
                listener_stderr,
                listener_path: Some(path_str),
            }
        }
        Err(_) => {
            // Timeout: fire-and-forget — let background thread clean up.
            ListenerResult {
                decision: ListenerDecision::Observe,
                listener_timeout: true,
                listener_stderr: None,
                listener_path: Some(path_str),
            }
        }
    }
}

// ── Internal helpers ──────────────────────────────────────────────────────────

fn resolve_listener_path() -> Option<PathBuf> {
    // 1. Explicit env-var override.
    if let Ok(val) = env::var("SK_RETRY_LISTENER") {
        if !val.is_empty() {
            let p = PathBuf::from(&val);
            if p.is_file() {
                return Some(p);
            }
            eprintln!(
                "sk-retry-listener: SK_RETRY_LISTENER={val:?} not found — falling back to default"
            );
        }
    }

    // 2. Default location.
    let default_path = hooks_dir()?.join(default_listener_filename());
    if default_path.is_file() {
        return Some(default_path);
    }

    None
}

fn hooks_dir() -> Option<PathBuf> {
    home_dir().map(|h| h.join(".copilot").join("hooks"))
}

fn home_dir() -> Option<PathBuf> {
    #[cfg(target_os = "windows")]
    {
        env::var("USERPROFILE").ok().map(PathBuf::from)
    }
    #[cfg(not(target_os = "windows"))]
    {
        env::var("HOME").ok().map(PathBuf::from)
    }
}

fn default_listener_filename() -> &'static str {
    #[cfg(target_os = "windows")]
    {
        "sk-retry-listener.ps1"
    }
    #[cfg(not(target_os = "windows"))]
    {
        "sk-retry-listener.sh"
    }
}

fn build_command(path: &PathBuf) -> Option<Command> {
    #[cfg(target_os = "windows")]
    {
        let mut cmd = Command::new("powershell.exe");
        cmd.args(["-NoProfile", "-ExecutionPolicy", "Bypass", "-File"])
            .arg(path);
        Some(cmd)
    }
    #[cfg(not(target_os = "windows"))]
    {
        use std::os::unix::fs::PermissionsExt;
        let is_exec = std::fs::metadata(path)
            .map(|m| m.permissions().mode() & 0o111 != 0)
            .unwrap_or(false);
        if is_exec {
            Some(Command::new(path))
        } else {
            // Not executable — attempt to run via sh.
            let mut cmd = Command::new("sh");
            cmd.arg(path);
            Some(cmd)
        }
    }
}

fn parse_stdout_decision(stdout: &str) -> ListenerDecision {
    let trimmed = stdout.trim();
    if trimmed.is_empty() {
        return ListenerDecision::Observe;
    }
    match serde_json::from_str::<serde_json::Value>(trimmed) {
        Ok(v) => {
            if v.get("abort").and_then(|a| a.as_bool()).unwrap_or(false) {
                return ListenerDecision::Abort;
            }
            if let Some(d) = v.get("delay_override_seconds").and_then(|d| d.as_f64()) {
                return ListenerDecision::DelayOverride(d.clamp(0.0, 300.0));
            }
            // Valid JSON with unrecognised fields → Observe.
            ListenerDecision::Observe
        }
        Err(e) => {
            eprintln!("sk-retry-listener: non-JSON stdout (ignored): {e}");
            ListenerDecision::Observe
        }
    }
}

// ── decide_with_listener integration ─────────────────────────────────────────

use crate::retry::{decide, RetryDecision, RetryPolicy, StopReason};

/// Additional context supplied to `decide_with_listener`.
///
/// All fields are optional; defaults produce sensible payload values.
#[derive(Debug, Default)]
pub struct RetryListenerContext {
    /// Agent identifier written into the listener payload. Default: "copilot".
    pub agent: String,
    /// HTTP status code, if known.
    pub status_code: Option<u32>,
    /// Server-supplied Retry-After in seconds, if parsed from headers.
    pub retry_after_hint_seconds: Option<f64>,
}

/// Call [`decide`], then — if the decision is [`RetryDecision::Retry`] — invoke
/// the configured external retry listener and honour its override.
///
/// Returns `RetryDecision::Stop(StopReason::ListenerAbort)` when the listener
/// asks to abort.  Returns `RetryDecision::Retry` with an overridden delay
/// when the listener returns `delay_override_seconds`.  In all other cases
/// (listener absent, Observe, timeout, error) the original decision stands.
pub fn decide_with_listener(
    policy: &RetryPolicy,
    attempt: u32,
    elapsed: Duration,
    err: &str,
    retry_after: Option<Duration>,
    ctx: &RetryListenerContext,
) -> RetryDecision {
    let base = decide(policy, attempt, elapsed, err, retry_after);

    let (delay, kind) = match base {
        RetryDecision::Retry(d, k) => (d, k),
        stop => return stop,
    };

    use std::time::{SystemTime, UNIX_EPOCH};

    let unix_secs = SystemTime::now()
        .duration_since(UNIX_EPOCH)
        .unwrap_or_default()
        .as_secs();
    let ts = format_unix_ts(unix_secs);

    let delay_source = if retry_after.map(|d| d > Duration::ZERO).unwrap_or(false) {
        "retry_after"
    } else {
        "exponential"
    };

    let agent = if ctx.agent.is_empty() {
        "copilot".to_string()
    } else {
        ctx.agent.clone()
    };

    let payload = RetryListenerPayload {
        ts,
        hook: "429-retry".to_string(),
        agent,
        attempt,
        max_attempts: policy.max_attempts,
        detected_pattern: format!("{kind:?}"),
        status_code: ctx.status_code,
        retry_after_hint_seconds: ctx.retry_after_hint_seconds,
        computed_delay_seconds: delay.as_secs_f64(),
        delay_source: delay_source.to_string(),
        elapsed_total_seconds: elapsed.as_secs_f64(),
        outcome: "queued".to_string(),
    };

    let result = invoke_retry_listener(&payload);

    match result.decision {
        ListenerDecision::Abort => RetryDecision::Stop(StopReason::ListenerAbort),
        ListenerDecision::DelayOverride(secs) => {
            RetryDecision::Retry(Duration::from_secs_f64(secs), kind)
        }
        ListenerDecision::Observe => RetryDecision::Retry(delay, kind),
    }
}

/// Format a Unix timestamp as a minimal ISO-8601 UTC string.
fn format_unix_ts(unix_secs: u64) -> String {
    let time_of_day = unix_secs % 86_400;
    let h = time_of_day / 3600;
    let m = (time_of_day % 3600) / 60;
    let s = time_of_day % 60;

    // Days since Unix epoch → approximate Gregorian date (ignores leap seconds).
    let days = unix_secs / 86_400;
    let (y, mo, d) = days_to_ymd(days);
    format!("{y:04}-{mo:02}-{d:02}T{h:02}:{m:02}:{s:02}Z")
}

/// Minimal days-since-epoch to (year, month, day) conversion.
fn days_to_ymd(days: u64) -> (u64, u64, u64) {
    // Algorithm from Richards (2013), adapted for u64.
    let z = days + 719_468;
    let era = z / 146_097;
    let doe = z % 146_097;
    let yoe = (doe - doe / 1460 + doe / 36524 - doe / 146_096) / 365;
    let y = yoe + era * 400;
    let doy = doe - (365 * yoe + yoe / 4 - yoe / 100);
    let mp = (5 * doy + 2) / 153;
    let d = doy - (153 * mp + 2) / 5 + 1;
    let m = if mp < 10 { mp + 3 } else { mp - 9 };
    let y = if m <= 2 { y + 1 } else { y };
    (y, m, d)
}

// ── Tests ─────────────────────────────────────────────────────────────────────

#[cfg(test)]
#[path = "retry_listener_tests.rs"]
mod retry_listener_tests;
