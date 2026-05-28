// Public API consumed by sibling tentacles; allow dead_code until
// issue-614-retry-rule wires this module up.
#![allow(dead_code)]
//! Retry-queue JSONL I/O — issue #614 Phase-1.
//!
//! # Responsibilities
//!
//! * Define [`RetryRecord`] — the on-disk JSONL record type.
//! * [`append_record`] — atomic HMAC-signed append to
//!   `~/.copilot/markers/retry-queue.jsonl`.
//! * [`read_records`] — deserialise all valid records; skip + log tampered
//!   lines via [`crate::hooks::audit::audit_log`].
//! * [`rotate_if_needed`] — rename the queue file when it exceeds
//!   `ROTATION_BYTES` (10 MB), using the same 3-try / 30 ms Windows-safe
//!   rename-retry pattern from `sync_markers.rs`.
//! * [`load_or_create_state_key`] — read (or generate) the per-machine HMAC
//!   secret at `~/.copilot/sk/state.key` with 0600 permissions on Unix.
//! * Path-safety: reject symlink queue paths before any I/O.
//!
//! # What is NOT here
//!
//! Pure policy / classification logic lives in `retry::policy` and
//! `retry::classifier`.  `HookRule` registration belongs to
//! `issue-614-retry-rule`.  CLI read / status lives in
//! `issue-614-retry-cli`.

use crate::config::resolve_home_dir;
use crate::hooks::audit::audit_log;
use crate::redact::redact;
use hmac::{Hmac, Mac};
use serde::{Deserialize, Serialize};
use sha2::Sha256;
use std::fs;
use std::io::Write;
use std::path::PathBuf;
use std::time::{SystemTime, UNIX_EPOCH};

type HmacSha256 = Hmac<Sha256>;

// ---------------------------------------------------------------------------
// Constants
// ---------------------------------------------------------------------------

/// Rotate the queue file once it exceeds this size (10 MB).
pub const ROTATION_BYTES: u64 = 10 * 1_024 * 1_024;

/// Maximum number of records to accumulate before a forced flush comment.
/// (Informational; enforcement is size-based.)
pub const MAX_RECORDS_SOFT: usize = 50_000;

// ---------------------------------------------------------------------------
// Paths
// ---------------------------------------------------------------------------

/// `~/.copilot/markers/`
fn markers_dir() -> PathBuf {
    resolve_home_dir()
        .unwrap_or_else(|| PathBuf::from("."))
        .join(".copilot")
        .join("markers")
}

/// `~/.copilot/markers/retry-queue.jsonl`
pub fn queue_path() -> PathBuf {
    markers_dir().join("retry-queue.jsonl")
}

/// `~/.copilot/sk/state.key`
pub fn state_key_path() -> PathBuf {
    resolve_home_dir()
        .unwrap_or_else(|| PathBuf::from("."))
        .join(".copilot")
        .join("sk")
        .join("state.key")
}

// ---------------------------------------------------------------------------
// Record type
// ---------------------------------------------------------------------------

/// A single JSONL record in `retry-queue.jsonl`.
///
/// All string fields are bounded / redacted before serialisation.
/// The `hmac` field is the HMAC-SHA256 hex of every other field combined
/// into a canonical JSON blob (keys sorted, no extra whitespace).
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct RetryRecord {
    /// Timestamp (`unix:<secs>` until a full RFC3339 formatter is needed).
    pub ts: String,
    /// Logical hook name (`"429-retry"` per issue #614).
    pub hook: String,
    /// Agent identifier (e.g. `"copilot"`), up to 64 chars.
    pub agent: String,
    /// Zero-indexed attempt number.
    pub attempt: u32,
    pub max_attempts: u32,
    pub detected_pattern: String,
    pub status_code: Option<u16>,
    pub retry_after_hint_seconds: Option<u64>,
    pub computed_delay_seconds: f64,
    pub delay_source: String,
    pub elapsed_total_seconds: f64,
    pub stop_reason: Option<String>,
    pub exit_code_prev: Option<i32>,
    pub outcome: String,
    /// Sanitised / redacted subset of the original hook payload/message.
    pub redacted_error_preview: String,
    /// HMAC-SHA256 hex over canonical JSON of all other fields.
    pub hmac: String,
}

impl RetryRecord {
    /// Canonical representation for signing / verification (no `hmac` field).
    fn canonical_json(&self) -> String {
        let inner = RetryRecordInner {
            ts: &self.ts,
            hook: &self.hook,
            agent: &self.agent,
            attempt: self.attempt,
            max_attempts: self.max_attempts,
            detected_pattern: &self.detected_pattern,
            status_code: self.status_code,
            retry_after_hint_seconds: self.retry_after_hint_seconds,
            computed_delay_seconds: self.computed_delay_seconds,
            delay_source: &self.delay_source,
            elapsed_total_seconds: self.elapsed_total_seconds,
            stop_reason: self.stop_reason.as_deref(),
            exit_code_prev: self.exit_code_prev,
            outcome: &self.outcome,
            redacted_error_preview: &self.redacted_error_preview,
        };
        serde_json::to_string(&inner).unwrap_or_default()
    }
}

/// Inner struct (no `hmac`) used for deterministic HMAC computation.
#[derive(Serialize)]
struct RetryRecordInner<'a> {
    ts: &'a str,
    hook: &'a str,
    agent: &'a str,
    attempt: u32,
    max_attempts: u32,
    detected_pattern: &'a str,
    status_code: Option<u16>,
    retry_after_hint_seconds: Option<u64>,
    computed_delay_seconds: f64,
    delay_source: &'a str,
    elapsed_total_seconds: f64,
    stop_reason: Option<&'a str>,
    exit_code_prev: Option<i32>,
    outcome: &'a str,
    redacted_error_preview: &'a str,
}

/// Caller-provided fields for building a queue record.
pub struct RetryRecordInput<'a> {
    pub agent: &'a str,
    pub attempt: u32,
    pub max_attempts: u32,
    pub detected_pattern: &'a str,
    pub status_code: Option<u16>,
    pub retry_after_hint_seconds: Option<u64>,
    pub computed_delay_seconds: f64,
    pub delay_source: &'a str,
    pub elapsed_total_seconds: f64,
    pub stop_reason: Option<&'a str>,
    pub exit_code_prev: Option<i32>,
    pub outcome: &'a str,
    pub error_preview: &'a str,
}

// ---------------------------------------------------------------------------
// State key
// ---------------------------------------------------------------------------

/// Load the HMAC state key from `~/.copilot/sk/state.key`.
///
/// If the file is missing or empty, generate 32 pseudo-random bytes (seeded
/// from the current nanosecond timestamp combined with process ID), store as
/// lowercase hex, and set 0600 permissions on Unix.
///
/// Returns the hex key string (64 chars) or an error.
pub fn load_or_create_state_key() -> std::io::Result<String> {
    let path = state_key_path();

    if path.is_file() {
        let raw = fs::read_to_string(&path)?;
        let key = raw.trim().to_string();
        if !key.is_empty() {
            return Ok(key);
        }
    }

    let key = generate_state_key();

    if let Some(parent) = path.parent() {
        fs::create_dir_all(parent)?;
    }

    fs::write(&path, &key)?;

    #[cfg(unix)]
    {
        use std::os::unix::fs::PermissionsExt;
        let perms = fs::Permissions::from_mode(0o600);
        fs::set_permissions(&path, perms)?;
    }

    Ok(key)
}

/// Generate 32 pseudo-random bytes as a hex string using SplitMix64-style PRNG.
///
/// On Unix this reads `/dev/urandom`. Other platforms use a stdlib-only
/// SplitMix64 fallback seeded from time and process ID (zero new runtime deps).
fn generate_state_key() -> String {
    #[cfg(unix)]
    {
        use std::io::Read;
        let mut bytes = [0u8; 32];
        if fs::File::open("/dev/urandom")
            .and_then(|mut f| f.read_exact(&mut bytes))
            .is_ok()
        {
            return bytes.iter().map(|b| format!("{b:02x}")).collect();
        }
    }

    let seed = {
        let nanos = SystemTime::now()
            .duration_since(UNIX_EPOCH)
            .unwrap_or_default()
            .subsec_nanos() as u64;
        let pid = std::process::id() as u64;
        nanos.wrapping_add(pid.wrapping_mul(0x9e37_79b9_7f4a_7c15))
    };
    let mut state = seed.wrapping_add(0x9e37_79b9_7f4a_7c15);
    let mut bytes = [0u8; 32];
    for chunk in bytes.chunks_mut(8) {
        state = state.wrapping_add(0x9e37_79b9_7f4a_7c15);
        let mut z = state;
        z = (z ^ (z >> 30)).wrapping_mul(0xbf58_476d_1ce4_e5b9);
        z = (z ^ (z >> 27)).wrapping_mul(0x94d0_49bb_1331_11eb);
        z ^= z >> 31;
        let octets = z.to_le_bytes();
        let n = chunk.len();
        chunk.copy_from_slice(&octets[..n]);
    }
    bytes.iter().map(|b| format!("{b:02x}")).collect()
}

// ---------------------------------------------------------------------------
// HMAC helpers
// ---------------------------------------------------------------------------

/// Compute HMAC-SHA256 over `message` using `key`; return lowercase hex.
fn hmac_hex(key: &str, message: &str) -> String {
    let mut mac =
        HmacSha256::new_from_slice(key.as_bytes()).expect("HMAC accepts keys of any size");
    mac.update(message.as_bytes());
    mac.finalize()
        .into_bytes()
        .iter()
        .map(|b| format!("{b:02x}"))
        .collect()
}

/// Constant-time HMAC-SHA256 verification.
fn hmac_verify(key: &str, message: &str, sig_hex: &str) -> bool {
    let sig_bytes: Option<Vec<u8>> = (0..sig_hex.len())
        .step_by(2)
        .map(|i| u8::from_str_radix(&sig_hex[i..i + 2], 16).ok())
        .collect();
    let sig_bytes = match sig_bytes {
        Some(b) if b.len() == 32 => b,
        _ => return false,
    };
    let mut mac =
        HmacSha256::new_from_slice(key.as_bytes()).expect("HMAC accepts keys of any size");
    mac.update(message.as_bytes());
    mac.verify_slice(&sig_bytes).is_ok()
}

// ---------------------------------------------------------------------------
// Path safety
// ---------------------------------------------------------------------------

/// Reject symlink queue paths.
///
/// Returns `Err(PermissionDenied)` when `path` is a symlink so that the
/// caller can skip I/O and produce an audit entry.
pub fn reject_symlink(path: &std::path::Path) -> std::io::Result<()> {
    if let Ok(meta) = path.symlink_metadata() {
        if meta.file_type().is_symlink() {
            return Err(std::io::Error::new(
                std::io::ErrorKind::PermissionDenied,
                format!(
                    "retry-queue path is a symlink and is not trusted: {}",
                    path.display()
                ),
            ));
        }
    }
    Ok(())
}

// ---------------------------------------------------------------------------
// Rotation
// ---------------------------------------------------------------------------

/// Rotate the queue file to `retry-queue.jsonl.old` when it exceeds
/// [`ROTATION_BYTES`].
///
/// Uses the 3-try / 30 ms Windows-safe rename-retry pattern from
/// `sync_markers.rs`.  Best-effort: failures are silently swallowed.
pub fn rotate_if_needed(path: &std::path::Path) {
    let _ = try_rotate_if_needed(path);
}

fn try_rotate_if_needed(path: &std::path::Path) -> std::io::Result<()> {
    let meta = match fs::metadata(path) {
        Ok(m) => m,
        Err(_) => return Ok(()),
    };
    if meta.len() < ROTATION_BYTES {
        return Ok(());
    }

    let rotated = path.with_extension("jsonl.old");
    reject_symlink(&rotated)?;
    let mut last_err = None;
    for attempt in 0..3u8 {
        match fs::rename(path, &rotated) {
            Ok(()) => return Ok(()),
            Err(e) => {
                last_err = Some(e);
                if attempt < 2 {
                    std::thread::sleep(std::time::Duration::from_millis(30));
                }
            }
        }
    }
    // Last-resort copy-and-remove (less atomic, better than losing records).
    fs::write(&rotated, fs::read(path).unwrap_or_default())?;
    fs::remove_file(path)?;
    drop(last_err);
    Ok(())
}

// ---------------------------------------------------------------------------
// Build a record
// ---------------------------------------------------------------------------

/// Construct a [`RetryRecord`] from caller-supplied fields.
///
/// `payload_json` is passed through the redactor; only the first 200
/// character-boundary bytes are stored (display preview, not a forensic dump).
pub fn build_record(
    _event: &str,
    agent: &str,
    attempt: u32,
    delay_ms: u64,
    payload_json: &str,
    state_key: &str,
) -> RetryRecord {
    let class_retryable = delay_ms > 0;
    build_record_from_input(
        RetryRecordInput {
            agent,
            attempt,
            max_attempts: 5,
            detected_pattern: if class_retryable {
                "RateLimitError"
            } else {
                "NonRetryable"
            },
            status_code: None,
            retry_after_hint_seconds: None,
            computed_delay_seconds: delay_ms as f64 / 1000.0,
            delay_source: if class_retryable {
                "exponential"
            } else {
                "none"
            },
            elapsed_total_seconds: 0.0,
            stop_reason: if class_retryable {
                None
            } else {
                Some("non_retryable")
            },
            exit_code_prev: Some(1),
            outcome: if class_retryable { "queued" } else { "stopped" },
            error_preview: payload_json,
        },
        state_key,
    )
}

/// Construct a full issue #614 queue record.
pub fn build_record_from_input(input: RetryRecordInput<'_>, state_key: &str) -> RetryRecord {
    let ts_secs = SystemTime::now()
        .duration_since(UNIX_EPOCH)
        .unwrap_or_default()
        .as_secs();

    let redacted = redact(input.error_preview).redacted;
    let preview = safe_truncate(&redacted, 200).to_string();

    let mut rec = RetryRecord {
        ts: format!("unix:{ts_secs}"),
        hook: "429-retry".to_string(),
        agent: truncate_str(input.agent, 64).to_string(),
        attempt: input.attempt,
        max_attempts: input.max_attempts,
        detected_pattern: truncate_str(input.detected_pattern, 96).to_string(),
        status_code: input.status_code,
        retry_after_hint_seconds: input.retry_after_hint_seconds,
        computed_delay_seconds: input.computed_delay_seconds,
        delay_source: truncate_str(input.delay_source, 32).to_string(),
        elapsed_total_seconds: input.elapsed_total_seconds,
        stop_reason: input.stop_reason.map(|s| truncate_str(s, 64).to_string()),
        exit_code_prev: input.exit_code_prev,
        outcome: truncate_str(input.outcome, 32).to_string(),
        redacted_error_preview: preview,
        hmac: String::new(),
    };

    let canonical = rec.canonical_json();
    rec.hmac = hmac_hex(state_key, &canonical);
    rec
}

// ---------------------------------------------------------------------------
// Append
// ---------------------------------------------------------------------------

/// In-process serialisation lock for queue appends.
///
/// O_APPEND guarantees atomic writes only when a single `write` syscall
/// succeeds in one shot.  On macOS APFS, concurrent `write_all` calls from
/// different file descriptors can interleave when records exceed a single
/// kernel transaction boundary.  A process-level mutex makes in-process
/// concurrent appends strictly sequential so that every line written to the
/// JSONL file is complete and parseable.  Cross-process atomicity is handled
/// by the kernel for O_APPEND writes small enough to fit in one syscall, which
/// is satisfied in practice for our ~200-byte records.
static APPEND_LOCK: std::sync::OnceLock<std::sync::Mutex<()>> = std::sync::OnceLock::new();

fn append_lock() -> &'static std::sync::Mutex<()> {
    APPEND_LOCK.get_or_init(|| std::sync::Mutex::new(()))
}

/// Append one [`RetryRecord`] to the queue file (best-effort).
///
/// 1. Reject symlink queue path.
/// 2. Rotate if the file exceeds [`ROTATION_BYTES`].
/// 3. Append the JSON line followed by `\n` under the in-process write lock.
pub fn append_record(record: &RetryRecord) -> std::io::Result<()> {
    let _guard = append_lock().lock().unwrap_or_else(|e| e.into_inner());
    let path = queue_path();
    let dir = markers_dir();
    fs::create_dir_all(&dir)?;

    reject_symlink(&path)?;
    rotate_if_needed(&path);

    let line = serde_json::to_string(record)
        .map_err(|e| std::io::Error::new(std::io::ErrorKind::InvalidData, e))?;

    let mut f = fs::OpenOptions::new()
        .create(true)
        .append(true)
        .open(&path)?;
    let mut buf = line.into_bytes();
    buf.push(b'\n');
    f.write_all(&buf)?;
    Ok(())
}

// ---------------------------------------------------------------------------
// Read
// ---------------------------------------------------------------------------

/// Read all HMAC-valid records from the queue file.
///
/// Lines with a bad / missing HMAC are skipped; each skip produces an
/// `audit_log` entry (tamper evidence) and is counted in `skipped`.
///
/// Returns `(records, skipped_count)`.
pub fn read_records(state_key: &str) -> std::io::Result<(Vec<RetryRecord>, usize)> {
    let path = queue_path();
    reject_symlink(&path)?;

    let content = match fs::read_to_string(&path) {
        Ok(c) => c,
        Err(e) if e.kind() == std::io::ErrorKind::NotFound => return Ok((Vec::new(), 0)),
        Err(e) => return Err(e),
    };

    let mut records = Vec::new();
    let mut skipped = 0usize;

    for (line_no, line) in content.lines().enumerate() {
        let line = line.trim();
        if line.is_empty() {
            continue;
        }

        match parse_and_verify_line(line, state_key) {
            Ok(rec) => records.push(rec),
            Err(reason) => {
                skipped += 1;
                audit_log(
                    "retry_queue_tamper",
                    "retry-queue",
                    "retry_queue",
                    "tampered",
                    &format!("line {line_no}: {reason}"),
                );
            }
        }
    }

    Ok((records, skipped))
}

fn parse_and_verify_line(line: &str, state_key: &str) -> Result<RetryRecord, String> {
    let rec: RetryRecord =
        serde_json::from_str(line).map_err(|e| format!("json parse error: {e}"))?;

    let canonical = rec.canonical_json();
    if !hmac_verify(state_key, &canonical, &rec.hmac) {
        return Err("HMAC mismatch".to_string());
    }

    Ok(rec)
}

// ---------------------------------------------------------------------------
// String helpers
// ---------------------------------------------------------------------------

/// Truncate `s` to at most `max_bytes` bytes at a UTF-8 char boundary.
fn safe_truncate(s: &str, max_bytes: usize) -> &str {
    if s.len() <= max_bytes {
        return s;
    }
    let mut end = max_bytes;
    while end > 0 && !s.is_char_boundary(end) {
        end -= 1;
    }
    &s[..end]
}

/// Truncate `s` to at most `max_chars` *characters* (not bytes).
fn truncate_str(s: &str, max_chars: usize) -> &str {
    match s.char_indices().nth(max_chars) {
        Some((byte_idx, _)) => &s[..byte_idx],
        None => s,
    }
}

/// Serialise tests that mutate HOME/USERPROFILE env vars across retry modules.
#[cfg(test)]
pub(crate) static TEST_ENV_LOCK: std::sync::OnceLock<std::sync::Arc<std::sync::Mutex<()>>> =
    std::sync::OnceLock::new();

// ---------------------------------------------------------------------------
// Tests
// ---------------------------------------------------------------------------

#[cfg(test)]
mod tests {
    use super::*;
    use std::path::Path;
    use std::sync::Arc;

    // ------------------------------------------------------------------
    // Helpers
    // ------------------------------------------------------------------

    /// Create a unique temporary directory under `./target/` (never /tmp).
    fn make_test_dir(name: &str) -> PathBuf {
        let d = std::env::current_dir()
            .unwrap_or_else(|_| PathBuf::from("."))
            .join("target")
            .join("retry_queue_test")
            .join(name);
        // Always start fresh so stale state from previously-failed runs
        // does not pollute subsequent test assertions.
        let _ = fs::remove_dir_all(&d);
        fs::create_dir_all(&d).expect("create test dir");
        d
    }

    fn cleanup(d: &Path) {
        let _ = fs::remove_dir_all(d);
    }

    /// Override HOME so queue_path / state_key_path resolve into `dir`.
    fn with_home<F: FnOnce() -> R, R>(dir: &Path, f: F) -> R {
        let lock = TEST_ENV_LOCK
            .get_or_init(|| std::sync::Arc::new(std::sync::Mutex::new(())))
            .clone();
        // Use `unwrap_or_else(|e| e.into_inner())` to recover from a
        // poisoned mutex (a previous test panicked while holding the lock).
        let _guard = lock.lock().unwrap_or_else(|e| e.into_inner());

        let old_home = std::env::var("HOME").ok();
        let old_user = std::env::var("USERPROFILE").ok();
        std::env::set_var("HOME", dir.as_os_str());
        std::env::set_var("USERPROFILE", dir.as_os_str());

        let result = f();

        match old_home {
            Some(v) => std::env::set_var("HOME", v),
            None => std::env::remove_var("HOME"),
        }
        match old_user {
            Some(v) => std::env::set_var("USERPROFILE", v),
            None => std::env::remove_var("USERPROFILE"),
        }

        result
    }

    fn make_key() -> String {
        "a".repeat(64)
    }

    fn make_record(key: &str) -> RetryRecord {
        build_record(
            "errorOccurred",
            "copilot",
            0,
            1000,
            r#"{"status":429}"#,
            key,
        )
    }

    // ------------------------------------------------------------------
    // roundtrip: append → read returns the same record
    // ------------------------------------------------------------------

    #[test]
    fn test_roundtrip_append_read() {
        let dir = make_test_dir("roundtrip");
        let key = make_key();

        with_home(&dir, || {
            let rec = make_record(&key);
            append_record(&rec).expect("append should succeed");
            let (records, skipped) = read_records(&key).expect("read should succeed");
            assert_eq!(skipped, 0, "no tampered lines");
            assert_eq!(records.len(), 1);
            let r = &records[0];
            assert_eq!(r.hook, "429-retry");
            assert_eq!(r.agent, "copilot");
            assert_eq!(r.attempt, 0);
            assert_eq!(r.computed_delay_seconds, 1.0);
            assert_eq!(r.outcome, "queued");
            assert!(!r.hmac.is_empty());
        });

        cleanup(&dir);
    }

    // ------------------------------------------------------------------
    // tamper: corrupted HMAC → record is skipped and skipped count == 1
    // ------------------------------------------------------------------

    #[test]
    fn test_hmac_tamper_rejection() {
        let dir = make_test_dir("tamper");
        let key = make_key();

        with_home(&dir, || {
            let rec = make_record(&key);
            append_record(&rec).expect("append should succeed");

            let path = queue_path();
            let content = fs::read_to_string(&path).unwrap();
            let corrupted = content.replace(&rec.hmac, &"0".repeat(64));
            fs::write(&path, corrupted).unwrap();

            let (records, skipped) = read_records(&key).expect("read should succeed");
            assert_eq!(records.len(), 0, "tampered record must be skipped");
            assert_eq!(skipped, 1, "one tampered line must be counted");
        });

        cleanup(&dir);
    }

    // ------------------------------------------------------------------
    // tamper: wrong key → record skipped
    // ------------------------------------------------------------------

    #[test]
    fn test_wrong_key_tamper_detection() {
        let dir = make_test_dir("wrongkey");
        let key = make_key();

        with_home(&dir, || {
            let rec = make_record(&key);
            append_record(&rec).expect("append");

            let bad_key = "b".repeat(64);
            let (records, skipped) = read_records(&bad_key).expect("read");
            assert_eq!(records.len(), 0);
            assert_eq!(skipped, 1);
        });

        cleanup(&dir);
    }

    // ------------------------------------------------------------------
    // rotation: file > ROTATION_BYTES → renamed to .old
    // ------------------------------------------------------------------

    #[test]
    fn test_rotation() {
        let dir = make_test_dir("rotation");

        with_home(&dir, || {
            fs::create_dir_all(dir.join(".copilot").join("markers")).unwrap();
            let path = queue_path();
            let big = vec![b'x'; (ROTATION_BYTES + 1) as usize];
            fs::write(&path, &big).unwrap();

            rotate_if_needed(&path);

            assert!(
                !path.exists(),
                "original queue file should be gone after rotation"
            );
            let rotated = path.with_extension("jsonl.old");
            assert!(rotated.exists(), "rotated file should exist");
        });

        cleanup(&dir);
    }

    // ------------------------------------------------------------------
    // rotation: file < ROTATION_BYTES → no rotation
    // ------------------------------------------------------------------

    #[test]
    fn test_no_rotation_below_threshold() {
        let dir = make_test_dir("norotation");

        with_home(&dir, || {
            fs::create_dir_all(dir.join(".copilot").join("markers")).unwrap();
            let path = queue_path();
            fs::write(&path, b"x").unwrap();

            rotate_if_needed(&path);

            assert!(path.exists(), "file should remain when below threshold");
        });

        cleanup(&dir);
    }

    // ------------------------------------------------------------------
    // symlink rejection on append (Unix only)
    // ------------------------------------------------------------------

    #[test]
    #[cfg(unix)]
    fn test_symlink_rejection_on_append() {
        let dir = make_test_dir("symlink_append");
        let key = make_key();

        with_home(&dir, || {
            fs::create_dir_all(dir.join(".copilot").join("markers")).unwrap();
            let real_target = dir
                .join(".copilot")
                .join("markers")
                .join("real_target.jsonl");
            fs::write(&real_target, b"").unwrap();

            let link_path = queue_path();
            let _ = fs::remove_file(&link_path);
            std::os::unix::fs::symlink(&real_target, &link_path).unwrap();

            let rec = make_record(&key);
            let result = append_record(&rec);
            assert!(result.is_err(), "append to symlink must be rejected");
            assert_eq!(
                result.unwrap_err().kind(),
                std::io::ErrorKind::PermissionDenied
            );
        });

        cleanup(&dir);
    }

    // ------------------------------------------------------------------
    // symlink rejection on read (Unix only)
    // ------------------------------------------------------------------

    #[test]
    #[cfg(unix)]
    fn test_symlink_rejection_on_read() {
        let dir = make_test_dir("symlink_read");
        let key = make_key();

        with_home(&dir, || {
            fs::create_dir_all(dir.join(".copilot").join("markers")).unwrap();
            let real_target = dir.join(".copilot").join("markers").join("real_read.jsonl");
            fs::write(&real_target, b"").unwrap();

            let link_path = queue_path();
            let _ = fs::remove_file(&link_path);
            std::os::unix::fs::symlink(&real_target, &link_path).unwrap();

            let result = read_records(&key);
            assert!(result.is_err(), "read from symlink must be rejected");
            assert_eq!(
                result.unwrap_err().kind(),
                std::io::ErrorKind::PermissionDenied
            );
        });

        cleanup(&dir);
    }

    // ------------------------------------------------------------------
    // concurrent append: no torn JSON lines
    //
    // Note: spawned threads inherit the HOME env var set by the outer
    // `with_home`; they must NOT call `with_home` again or they will
    // deadlock trying to re-acquire ENV_LOCK.
    //
    // `append_record` holds APPEND_LOCK internally, serialising writes
    // within the process.  This guarantees every line in the file is a
    // complete, parseable JSON record.
    // ------------------------------------------------------------------

    #[test]
    fn test_concurrent_append_line_integrity() {
        let dir = make_test_dir("concurrent");
        let key = make_key();

        with_home(&dir, || {
            use std::thread;

            const THREADS: usize = 4;
            const RECORDS_PER_THREAD: u32 = 10;

            let barrier = Arc::new(std::sync::Barrier::new(THREADS));
            let mut handles = Vec::new();

            for t in 0..THREADS {
                let barrier = Arc::clone(&barrier);
                let key = key.clone();
                // Capture the queue path while HOME is set (main thread holds lock).
                let path = queue_path();
                handles.push(thread::spawn(move || {
                    barrier.wait();
                    // HOME is already set; threads inherit it.  Do NOT call
                    // with_home here — it would deadlock on ENV_LOCK.
                    let dir_for_create = path.parent().unwrap().to_path_buf();
                    let _ = fs::create_dir_all(&dir_for_create);
                    for i in 0..RECORDS_PER_THREAD {
                        let rec = build_record(
                            "errorOccurred",
                            &format!("agent-{t}"),
                            i,
                            100,
                            "{}",
                            &key,
                        );
                        // append_record holds APPEND_LOCK internally.
                        let _ = append_record(&rec);
                    }
                }));
            }

            for h in handles {
                h.join().unwrap();
            }

            let path = queue_path();
            if path.exists() {
                let content = fs::read_to_string(&path).unwrap();
                let mut bad_lines = 0usize;
                for line in content.lines() {
                    let line = line.trim();
                    if line.is_empty() {
                        continue;
                    }
                    if serde_json::from_str::<RetryRecord>(line).is_err() {
                        bad_lines += 1;
                    }
                }
                assert_eq!(
                    bad_lines, 0,
                    "concurrent appends must not produce torn JSON lines"
                );
            }
        });

        cleanup(&dir);
    }

    // ------------------------------------------------------------------
    // state-key: generated value is 64 hex chars
    // ------------------------------------------------------------------

    #[test]
    fn test_generate_state_key_format() {
        let key = generate_state_key();
        assert_eq!(key.len(), 64, "state key must be 64 hex chars");
        assert!(
            key.chars().all(|c| c.is_ascii_hexdigit()),
            "key must be hex"
        );
    }

    // ------------------------------------------------------------------
    // state-key: load_or_create_state_key persists across calls
    // ------------------------------------------------------------------

    #[test]
    fn test_load_or_create_state_key() {
        let dir = make_test_dir("statekey");

        with_home(&dir, || {
            let key1 = load_or_create_state_key().expect("create key");
            assert_eq!(key1.len(), 64);

            let key2 = load_or_create_state_key().expect("load key");
            assert_eq!(key1, key2, "loaded key must match created key");

            let path = state_key_path();
            assert!(path.exists(), "state.key must be persisted");
        });

        cleanup(&dir);
    }

    // ------------------------------------------------------------------
    // safe_truncate: never panics on multibyte input
    // ------------------------------------------------------------------

    #[test]
    fn test_safe_truncate_multibyte() {
        let s: String = "😀".repeat(60); // 240 bytes, each char 4 bytes
        let truncated = safe_truncate(&s, 200);
        assert!(truncated.len() <= 200);
        assert!(s.is_char_boundary(truncated.len()));
    }

    // ------------------------------------------------------------------
    // redacted_error_preview: raw secrets are redacted before storage
    // ------------------------------------------------------------------

    #[test]
    fn test_payload_preview_redacted() {
        let dir = make_test_dir("redact");
        let key = make_key();

        with_home(&dir, || {
            let secret_payload =
                r#"{"token":"ghp_abcdefghijklmnopqrstuvwxyz0123456789AB","status":429}"#;
            let rec = build_record("errorOccurred", "copilot", 0, 0, secret_payload, &key);
            assert!(
                !rec.redacted_error_preview
                    .contains("ghp_abcdefghijklmnopqrstuvwxyz0123456789AB"),
                "raw token must be redacted from redacted_error_preview"
            );
        });

        cleanup(&dir);
    }

    // ------------------------------------------------------------------
    // multiple records: read preserves order and field values
    // ------------------------------------------------------------------

    #[test]
    fn test_multiple_records_roundtrip() {
        let dir = make_test_dir("multi");
        let key = make_key();

        with_home(&dir, || {
            for i in 1u32..=5 {
                let rec = build_record(
                    "errorOccurred",
                    "copilot",
                    i - 1,
                    i as u64 * 1000,
                    "{}",
                    &key,
                );
                append_record(&rec).expect("append");
            }

            let (records, skipped) = read_records(&key).expect("read");
            assert_eq!(skipped, 0);
            assert_eq!(records.len(), 5);
            for (i, r) in records.iter().enumerate() {
                assert_eq!(r.attempt, i as u32);
                assert_eq!(r.computed_delay_seconds, i as f64 + 1.0);
            }
        });

        cleanup(&dir);
    }
}
