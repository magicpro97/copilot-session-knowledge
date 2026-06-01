//! Tests for the retry_listener hook module.
//!
//! Each test that modifies env vars takes the `TEST_LOCK` mutex to avoid
//! races when the test suite runs tests in parallel.

#[cfg(unix)]
use std::sync::Mutex;

#[cfg(unix)]
use std::io::Write;
#[cfg(unix)]
use std::time::Instant;

#[cfg(unix)]
use super::RetryListenerPayload;
#[cfg(unix)]
use super::{decide_with_listener, invoke_retry_listener, ListenerDecision, RetryListenerContext};
#[cfg(unix)]
use crate::retry::{RetryDecision, RetryPolicy, StopReason};
#[cfg(unix)]
use std::time::Duration;

#[cfg(unix)]
static TEST_LOCK: Mutex<()> = Mutex::new(());

#[cfg(unix)]
fn lock_test() -> std::sync::MutexGuard<'static, ()> {
    // Recover from poison — a prior panicking test must not block other tests.
    TEST_LOCK.lock().unwrap_or_else(|e| e.into_inner())
}

#[cfg(unix)]
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
        computed_delay_seconds: 3.0,
        delay_source: "exponential".to_string(),
        elapsed_total_seconds: 4.5,
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
    let _guard = lock_test();

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
    let _guard = lock_test();
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
    let _guard = lock_test();
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
    let _guard = lock_test();
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
    let _guard = lock_test();
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

// ── decide_with_listener integration tests ────────────────────────────────────

/// A no-jitter policy for deterministic delay assertions.
#[cfg(unix)]
fn no_jitter_policy() -> RetryPolicy {
    RetryPolicy {
        base: Duration::from_secs(5),
        cap: Duration::from_secs(60),
        multiplier: 1.0,
        jitter: (1.0, 1.0), // lo == hi → no jitter
        max_attempts: 5,
        budget: None,
    }
}

/// No listener configured → decide_with_listener returns the same decision as
/// decide() (behaviour is entirely unchanged).
#[test]
#[cfg(unix)]
fn test_decide_with_listener_no_listener_unchanged() {
    let _guard = lock_test();
    let tmp = tempdir();
    let orig_home = std::env::var("HOME").ok();
    let orig_listener = std::env::var("SK_RETRY_LISTENER").ok();

    std::env::set_var("HOME", &tmp);
    std::env::remove_var("SK_RETRY_LISTENER");

    let policy = no_jitter_policy();
    let result = decide_with_listener(
        &policy,
        0,
        Duration::ZERO,
        "429 rate_limit_exceeded",
        None,
        &RetryListenerContext::default(),
    );

    // Restore environment.
    match orig_home {
        Some(h) => std::env::set_var("HOME", h),
        None => std::env::remove_var("HOME"),
    }
    match orig_listener {
        Some(v) => std::env::set_var("SK_RETRY_LISTENER", v),
        None => std::env::remove_var("SK_RETRY_LISTENER"),
    }

    // With no listener, the result must equal the plain decide() output.
    match result {
        RetryDecision::Retry(d, _) => {
            // base=5s, multiplier=1.0, attempt=0, no jitter → 5s
            assert_eq!(d, Duration::from_secs(5), "expected 5s default delay");
        }
        other => panic!("expected Retry, got {other:?}"),
    }
}

/// Listener script writes `{{"abort":true}}` → ListenerAbort stop reason.
#[test]
#[cfg(unix)]
fn test_decide_with_listener_abort() {
    let _guard = lock_test();
    let tmp = tempdir();

    let script = write_script(&tmp, "abort_dwl.sh", r#"printf '{"abort":true}\n'"#);
    std::env::set_var("SK_RETRY_LISTENER", &script);

    let policy = no_jitter_policy();
    let result = decide_with_listener(
        &policy,
        0,
        Duration::ZERO,
        "429 rate_limit_exceeded",
        None,
        &RetryListenerContext::default(),
    );
    std::env::remove_var("SK_RETRY_LISTENER");

    match result {
        RetryDecision::Stop(StopReason::ListenerAbort) => {}
        other => panic!("expected Stop(ListenerAbort), got {other:?}"),
    }
}

/// Listener script overrides the delay → decide_with_listener returns the
/// overridden duration, not the policy-computed one.
#[test]
#[cfg(unix)]
fn test_decide_with_listener_delay_override() {
    let _guard = lock_test();
    let tmp = tempdir();

    let script = write_script(
        &tmp,
        "delay_dwl.sh",
        r#"printf '{"delay_override_seconds":12.0}\n'"#,
    );
    std::env::set_var("SK_RETRY_LISTENER", &script);

    let policy = no_jitter_policy();
    let result = decide_with_listener(
        &policy,
        0,
        Duration::ZERO,
        "429 rate_limit_exceeded",
        None,
        &RetryListenerContext::default(),
    );
    std::env::remove_var("SK_RETRY_LISTENER");

    match result {
        RetryDecision::Retry(d, _) => {
            assert!(
                (d.as_secs_f64() - 12.0).abs() < 1e-9,
                "expected 12s override, got {d:?}"
            );
        }
        other => panic!("expected Retry with 12s override, got {other:?}"),
    }
}

/// Slow listener (5 s) → 2 s timeout fires, decide_with_listener falls
/// through to the default computed delay.
#[test]
#[cfg(unix)]
fn test_decide_with_listener_timeout_fallthrough() {
    let _guard = lock_test();
    let tmp = tempdir();

    let script = write_script(&tmp, "slow_dwl.sh", "sleep 5");
    std::env::set_var("SK_RETRY_LISTENER", &script);

    let policy = no_jitter_policy();
    let start = Instant::now();
    let result = decide_with_listener(
        &policy,
        0,
        Duration::ZERO,
        "429 rate_limit_exceeded",
        None,
        &RetryListenerContext::default(),
    );
    let elapsed = start.elapsed();
    std::env::remove_var("SK_RETRY_LISTENER");

    // Must complete within the 2 s timeout window (≤3 s with CI slack).
    assert!(
        elapsed.as_secs() <= 3,
        "decide_with_listener should return within 3 s, took {elapsed:.2?}"
    );
    // Timed-out listener → fall through to the default delay (5 s).
    match result {
        RetryDecision::Retry(d, _) => {
            assert_eq!(
                d,
                Duration::from_secs(5),
                "expected fallthrough to default 5s delay"
            );
        }
        other => panic!("expected Retry (timeout fallthrough), got {other:?}"),
    }
}

// ── Helpers ───────────────────────────────────────────────────────────────────

/// Create a temporary directory; panics on failure.
/// Returns the directory path (the directory will NOT be auto-cleaned — tests
/// are short-lived and the OS will reclaim space).  We avoid the `tempfile`
/// crate to keep this stdlib-only.
#[cfg(unix)]
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
