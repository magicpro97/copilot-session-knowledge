//! Tests for the retry_listener hook module.
//!
//! Each test that modifies env vars takes the `TEST_LOCK` mutex to avoid
//! races when the test suite runs tests in parallel.

use std::io::Write;
use std::sync::Mutex;
use std::time::Instant;

use super::{invoke_retry_listener, ListenerDecision, RetryListenerPayload};

static TEST_LOCK: Mutex<()> = Mutex::new(());

fn sample_payload() -> RetryListenerPayload {
    RetryListenerPayload {
        ts: "2025-01-01T00:00:00Z".to_string(),
        hook: "429-retry".to_string(),
        agent: "copilot".to_string(),
        attempt: 2,
        max_attempts: 5,
        detected_pattern: "RateLimitError".to_string(),
        status_code: Some(429),
        retry_after_hint_seconds: None,
        computed_delay_seconds: 3.14,
        delay_source: "exponential".to_string(),
        elapsed_total_seconds: 4.27,
        outcome: "queued".to_string(),
    }
}

/// Write a shell script to a temp file and make it executable.
#[cfg(unix)]
fn write_script(dir: &std::path::Path, name: &str, content: &str) -> std::path::PathBuf {
    use std::os::unix::fs::PermissionsExt;
    let path = dir.join(name);
    let mut f = std::fs::File::create(&path).unwrap();
    writeln!(f, "#!/usr/bin/env sh").unwrap();
    writeln!(f, "{content}").unwrap();
    let mut perms = f.metadata().unwrap().permissions();
    perms.set_mode(0o755);
    std::fs::set_permissions(&path, perms).unwrap();
    path
}

// ── Tests ─────────────────────────────────────────────────────────────────────

/// No SK_RETRY_LISTENER env var set, and HOME points to an empty temp dir →
/// no listener found → silent no-op (Observe, no error).
#[test]
#[cfg(unix)]
fn test_missing_listener_is_noop() {
    let _guard = TEST_LOCK.lock().unwrap();

    // Point HOME to a fresh temp dir so the default path doesn't exist.
    let tmp = tempdir();
    let orig_home = std::env::var("HOME").ok();
    let orig_listener = std::env::var("SK_RETRY_LISTENER").ok();

    std::env::set_var("HOME", &tmp);
    std::env::remove_var("SK_RETRY_LISTENER");

    let result = invoke_retry_listener(&sample_payload());

    // Restore environment.
    match orig_home {
        Some(h) => std::env::set_var("HOME", h),
        None => std::env::remove_var("HOME"),
    }
    match orig_listener {
        Some(v) => std::env::set_var("SK_RETRY_LISTENER", v),
        None => std::env::remove_var("SK_RETRY_LISTENER"),
    }

    assert!(
        matches!(result.decision, ListenerDecision::Observe),
        "expected Observe for missing listener"
    );
    assert!(!result.listener_timeout);
    assert!(result.listener_stderr.is_none());
    assert!(result.listener_path.is_none());
}

/// Listener script writes `{"abort":true}` → decision is Abort.
#[test]
#[cfg(unix)]
fn test_always_abort_listener() {
    let _guard = TEST_LOCK.lock().unwrap();
    let tmp = tempdir();

    let script = write_script(&tmp, "abort.sh", r#"printf '{"abort":true}\n'"#);
    std::env::set_var("SK_RETRY_LISTENER", &script);

    let result = invoke_retry_listener(&sample_payload());
    std::env::remove_var("SK_RETRY_LISTENER");

    assert!(
        matches!(result.decision, ListenerDecision::Abort),
        "expected Abort, got {:?}",
        result.decision
    );
    assert!(!result.listener_timeout);
}

/// Listener script writes `{"delay_override_seconds":1.5}` → DelayOverride(1.5).
#[test]
#[cfg(unix)]
fn test_delay_override_listener() {
    let _guard = TEST_LOCK.lock().unwrap();
    let tmp = tempdir();

    let script = write_script(
        &tmp,
        "delay.sh",
        r#"printf '{"delay_override_seconds":1.5}\n'"#,
    );
    std::env::set_var("SK_RETRY_LISTENER", &script);

    let result = invoke_retry_listener(&sample_payload());
    std::env::remove_var("SK_RETRY_LISTENER");

    match result.decision {
        ListenerDecision::DelayOverride(d) => {
            assert!((d - 1.5).abs() < 1e-9, "expected delay 1.5, got {d}");
        }
        other => panic!("expected DelayOverride(1.5), got {other:?}"),
    }
    assert!(!result.listener_timeout);
}

/// Listener script writes garbage JSON → gracefully ignored (Observe).
#[test]
#[cfg(unix)]
fn test_bad_json_listener() {
    let _guard = TEST_LOCK.lock().unwrap();
    let tmp = tempdir();

    let script = write_script(&tmp, "bad_json.sh", "printf 'not-json-at-all!!!'");
    std::env::set_var("SK_RETRY_LISTENER", &script);

    let result = invoke_retry_listener(&sample_payload());
    std::env::remove_var("SK_RETRY_LISTENER");

    assert!(
        matches!(result.decision, ListenerDecision::Observe),
        "expected Observe for bad JSON"
    );
    assert!(!result.listener_timeout);
}

/// Listener script sleeps 5 s → completes in ≤3 s (2 s timeout fires).
#[test]
#[cfg(unix)]
fn test_slow_listener_timeout() {
    let _guard = TEST_LOCK.lock().unwrap();
    let tmp = tempdir();

    let script = write_script(&tmp, "slow.sh", "sleep 5");
    std::env::set_var("SK_RETRY_LISTENER", &script);

    let start = Instant::now();
    let result = invoke_retry_listener(&sample_payload());
    let elapsed = start.elapsed();
    std::env::remove_var("SK_RETRY_LISTENER");

    assert!(result.listener_timeout, "expected listener_timeout=true");
    assert!(
        matches!(result.decision, ListenerDecision::Observe),
        "timed-out listener should return Observe"
    );
    assert!(
        elapsed.as_secs() <= 3,
        "invoke should complete in ≤3 s, took {elapsed:.2?}"
    );
}

// ── Helpers ───────────────────────────────────────────────────────────────────

/// Create a temporary directory; panics on failure.
/// Returns the directory path (the directory will NOT be auto-cleaned — tests
/// are short-lived and the OS will reclaim space).  We avoid the `tempfile`
/// crate to keep this stdlib-only.
fn tempdir() -> std::path::PathBuf {
    use std::time::{SystemTime, UNIX_EPOCH};
    let ns = SystemTime::now()
        .duration_since(UNIX_EPOCH)
        .unwrap_or_default()
        .subsec_nanos();
    // Use the project's own target dir so we never write to /tmp.
    let base = std::path::PathBuf::from(env!("CARGO_MANIFEST_DIR"))
        .join("target")
        .join("test-scratch")
        .join(format!("retry-listener-{ns}"));
    std::fs::create_dir_all(&base).expect("create test scratch dir");
    base
}
