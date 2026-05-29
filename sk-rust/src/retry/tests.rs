//! Table-driven tests for the shared `retry` module.
//!
//! Covers:
//! - Each `RetryKind` via `classify_error`
//! - Each `StopReason` via `decide`
//! - Jitter bounds and no-jitter mode
//! - Overflow guard in `next_delay` / `compute_backoff`
//! - `retry_after` precedence
//! - Telegram broker backoff golden sequence

use std::time::Duration;

use super::{
    classify_error, decide, next_delay, seed_prng, RetryDecision, RetryKind, RetryPolicy,
    StopReason,
};

// ── Helper ────────────────────────────────────────────────────────────────────

/// A no-jitter policy convenient for deterministic tests.
fn no_jitter(base_secs: u64, cap_secs: u64, multiplier: f64, max_attempts: u32) -> RetryPolicy {
    RetryPolicy {
        base: Duration::from_secs(base_secs),
        cap: Duration::from_secs(cap_secs),
        multiplier,
        jitter: (1.0, 1.0), // lo == hi → no jitter
        max_attempts,
        budget: None,
    }
}

// ── classify_error ────────────────────────────────────────────────────────────

#[test]
fn classify_empty_returns_none() {
    assert!(classify_error("").is_none());
}

#[test]
fn classify_429_rate_limit() {
    assert_eq!(
        classify_error("429 Too Many Requests"),
        Some(RetryKind::RateLimit)
    );
}

#[test]
fn classify_rate_limit_keyword() {
    assert_eq!(
        classify_error("rate_limit_exceeded"),
        Some(RetryKind::RateLimit)
    );
}

#[test]
fn classify_too_many_requests_phrase() {
    assert_eq!(
        classify_error("error: too many requests, slow down"),
        Some(RetryKind::RateLimit)
    );
}

#[test]
fn classify_500_server_error() {
    assert_eq!(
        classify_error("500 Internal Server Error"),
        Some(RetryKind::Server5xx)
    );
}

#[test]
fn classify_503_service_unavailable() {
    assert_eq!(
        classify_error("503 Service Unavailable"),
        Some(RetryKind::Server5xx)
    );
}

#[test]
fn classify_throttled() {
    assert_eq!(
        classify_error("Request throttled by upstream"),
        Some(RetryKind::Throttle)
    );
}

#[test]
fn classify_overloaded() {
    assert_eq!(
        classify_error("Server overloaded, try again later"),
        Some(RetryKind::Overloaded)
    );
}

#[test]
fn classify_over_capacity() {
    assert_eq!(
        classify_error("over_capacity: cluster at 100%"),
        Some(RetryKind::Overloaded)
    );
}

#[test]
fn classify_quota_exhausted() {
    assert_eq!(classify_error("insufficient_quota"), Some(RetryKind::Quota));
}

#[test]
fn classify_quota_exceeded_variant() {
    assert_eq!(
        classify_error("quota_exceeded: monthly limit reached"),
        Some(RetryKind::Quota)
    );
}

#[test]
fn classify_401_auth() {
    assert_eq!(classify_error("401 Unauthorized"), Some(RetryKind::Auth));
}

#[test]
fn classify_403_forbidden() {
    assert_eq!(classify_error("403 Forbidden"), Some(RetryKind::Auth));
}

#[test]
fn classify_invalid_api_key() {
    assert_eq!(
        classify_error("invalid_api_key: check your credentials"),
        Some(RetryKind::Auth)
    );
}

#[test]
fn classify_unknown_for_network_error() {
    // Network errors (connection reset, timeout, etc.) should be Unknown (retryable).
    assert_eq!(
        classify_error("transport error: connection reset by peer"),
        Some(RetryKind::Unknown)
    );
}

#[test]
fn classify_unknown_for_arbitrary_text() {
    assert_eq!(
        classify_error("something went wrong"),
        Some(RetryKind::Unknown)
    );
}

// ── next_delay ────────────────────────────────────────────────────────────────

#[test]
fn next_delay_attempt0_no_jitter() {
    let p = no_jitter(1, 60, 2.0, 5);
    assert_eq!(next_delay(&p, 0, None), Duration::from_secs(1));
}

#[test]
fn next_delay_attempt1_no_jitter() {
    let p = no_jitter(1, 60, 2.0, 5);
    assert_eq!(next_delay(&p, 1, None), Duration::from_secs(2));
}

#[test]
fn next_delay_attempt2_no_jitter() {
    let p = no_jitter(1, 60, 2.0, 5);
    assert_eq!(next_delay(&p, 2, None), Duration::from_secs(4));
}

#[test]
fn next_delay_clamped_to_cap() {
    let p = no_jitter(1, 60, 2.0, 5);
    // 2^10 = 1024 > 60 → should be capped
    assert_eq!(next_delay(&p, 10, None), Duration::from_secs(60));
}

#[test]
fn next_delay_overflow_guard_attempt_over_64() {
    let p = no_jitter(1, 60, 2.0, 5);
    assert_eq!(next_delay(&p, 65, None), Duration::from_secs(60));
    assert_eq!(next_delay(&p, u32::MAX, None), Duration::from_secs(60));
}

#[test]
fn next_delay_retry_after_wins() {
    let p = no_jitter(1, 60, 2.0, 5);
    assert_eq!(
        next_delay(&p, 0, Some(Duration::from_secs(10))),
        Duration::from_secs(10)
    );
}

#[test]
fn next_delay_retry_after_clamped_to_cap() {
    let p = no_jitter(1, 60, 2.0, 5);
    // retry_after=90 > cap=60 → clamped to 60
    assert_eq!(
        next_delay(&p, 0, Some(Duration::from_secs(90))),
        Duration::from_secs(60)
    );
}

#[test]
fn next_delay_retry_after_zero_ignored() {
    let p = no_jitter(1, 60, 2.0, 5);
    // retry_after=0 → fall through to exponential back-off
    assert_eq!(
        next_delay(&p, 0, Some(Duration::ZERO)),
        Duration::from_secs(1)
    );
}

#[test]
fn next_delay_no_jitter_lo_equals_hi() {
    // jitter (1.0, 1.0): lo == hi → no jitter, exact value returned
    let p = no_jitter(4, 60, 1.0, 5);
    assert_eq!(next_delay(&p, 0, None), Duration::from_secs(4));
    assert_eq!(next_delay(&p, 3, None), Duration::from_secs(4));
}

#[test]
fn next_delay_with_jitter_within_bounds() {
    seed_prng(0xdead_beef_1234_5678);
    let p = RetryPolicy {
        base: Duration::from_secs(10),
        cap: Duration::from_secs(60),
        multiplier: 1.0,
        jitter: (0.5, 1.0),
        max_attempts: 5,
        budget: None,
    };
    for _ in 0..50 {
        let d = next_delay(&p, 0, None);
        assert!(
            d >= Duration::from_secs(5) && d <= Duration::from_secs(10),
            "jitter out of [5s, 10s]: {d:?}"
        );
    }
}

#[test]
fn next_delay_invalid_multiplier_caps_to_cap() {
    let p = RetryPolicy {
        base: Duration::from_secs(1),
        cap: Duration::from_secs(60),
        multiplier: f64::NAN,
        jitter: (1.0, 1.0),
        max_attempts: 5,
        budget: None,
    };
    assert_eq!(next_delay(&p, 0, None), Duration::from_secs(60));
}

#[test]
fn next_delay_multiplier_one_flat_backoff() {
    // multiplier=1.0 → all attempts use base delay (like sync_markers policy)
    let p = RetryPolicy {
        base: Duration::from_millis(30),
        cap: Duration::from_millis(90),
        multiplier: 1.0,
        jitter: (1.0, 1.0),
        max_attempts: 3,
        budget: None,
    };
    assert_eq!(next_delay(&p, 0, None), Duration::from_millis(30));
    assert_eq!(next_delay(&p, 1, None), Duration::from_millis(30));
    assert_eq!(next_delay(&p, 2, None), Duration::from_millis(30));
}

// ── decide ────────────────────────────────────────────────────────────────────

#[test]
fn decide_stop_max_attempts() {
    let p = no_jitter(1, 60, 2.0, 3);
    match decide(&p, 3, Duration::ZERO, "network error", None) {
        RetryDecision::Stop(StopReason::MaxAttempts) => {}
        other => panic!("expected MaxAttempts, got {other:?}"),
    }
}

#[test]
fn decide_stop_budget_exceeded() {
    let p = RetryPolicy {
        budget: Some(Duration::from_secs(5)),
        ..no_jitter(1, 60, 2.0, 10)
    };
    match decide(&p, 0, Duration::from_secs(10), "network error", None) {
        RetryDecision::Stop(StopReason::Budget) => {}
        other => panic!("expected Budget, got {other:?}"),
    }
}

#[test]
fn decide_stop_auth_failure() {
    let p = no_jitter(1, 60, 2.0, 5);
    match decide(&p, 0, Duration::ZERO, "401 Unauthorized", None) {
        RetryDecision::Stop(StopReason::AuthFailure) => {}
        other => panic!("expected AuthFailure, got {other:?}"),
    }
}

#[test]
fn decide_stop_quota_exhausted() {
    let p = no_jitter(1, 60, 2.0, 5);
    match decide(&p, 0, Duration::ZERO, "insufficient_quota", None) {
        RetryDecision::Stop(StopReason::QuotaExhausted) => {}
        other => panic!("expected QuotaExhausted, got {other:?}"),
    }
}

#[test]
fn decide_stop_x_should_retry_false() {
    let p = no_jitter(1, 60, 2.0, 5);
    match decide(&p, 0, Duration::ZERO, "x-should-retry: false", None) {
        RetryDecision::Stop(StopReason::XShouldRetryFalse) => {}
        other => panic!("expected XShouldRetryFalse, got {other:?}"),
    }
}

#[test]
fn decide_stop_nonretryable_on_empty_error() {
    let p = no_jitter(1, 60, 2.0, 5);
    match decide(&p, 0, Duration::ZERO, "", None) {
        RetryDecision::Stop(StopReason::NonRetryable) => {}
        other => panic!("expected NonRetryable, got {other:?}"),
    }
}

#[test]
fn decide_retry_rate_limit() {
    let p = no_jitter(1, 60, 2.0, 5);
    match decide(&p, 0, Duration::ZERO, "429 rate_limit_exceeded", None) {
        RetryDecision::Retry(_, RetryKind::RateLimit) => {}
        other => panic!("expected Retry(RateLimit), got {other:?}"),
    }
}

#[test]
fn decide_retry_server5xx() {
    let p = no_jitter(1, 60, 2.0, 5);
    match decide(&p, 1, Duration::ZERO, "500 internal server error", None) {
        RetryDecision::Retry(d, RetryKind::Server5xx) => {
            assert_eq!(d, Duration::from_secs(2));
        }
        other => panic!("expected Retry(Server5xx), got {other:?}"),
    }
}

#[test]
fn decide_retry_unknown_network_error() {
    let p = no_jitter(1, 60, 2.0, 5);
    match decide(&p, 0, Duration::ZERO, "connection reset by peer", None) {
        RetryDecision::Retry(d, RetryKind::Unknown) => {
            assert_eq!(d, Duration::from_secs(1));
        }
        other => panic!("expected Retry(Unknown), got {other:?}"),
    }
}

#[test]
fn decide_no_budget_does_not_stop_on_elapsed() {
    // budget=None → elapsed check skipped, should retry
    let p = RetryPolicy {
        budget: None,
        ..no_jitter(1, 60, 2.0, u32::MAX)
    };
    match decide(&p, 0, Duration::from_secs(99999), "network error", None) {
        RetryDecision::Retry(_, _) => {}
        other => panic!("expected Retry, got {other:?}"),
    }
}

#[test]
fn decide_retry_after_passed_through() {
    let p = no_jitter(1, 60, 2.0, 5);
    match decide(
        &p,
        0,
        Duration::ZERO,
        "429 rate limit",
        Some(Duration::from_secs(7)),
    ) {
        RetryDecision::Retry(d, RetryKind::RateLimit) => {
            assert_eq!(d, Duration::from_secs(7));
        }
        other => panic!("expected Retry with 7s delay, got {other:?}"),
    }
}

// ── Telegram broker golden backoff sequence ───────────────────────────────────
//
// Original formula: `(1u64 << consecutive_errors.min(63)).min(MAX_BACKOFF_SECS)`
// with consecutive_errors = 1, 2, 3, 4, 5, 6, ...
//
// Equivalent policy: base=1s, multiplier=2.0, cap=60s, no jitter,
// attempt = consecutive_errors (1-indexed after increment).
//
// Expected sequence: 2, 4, 8, 16, 32, 60, 60, ...

#[test]
fn telegram_backoff_golden_sequence() {
    let policy = RetryPolicy {
        base: Duration::from_secs(1),
        cap: Duration::from_secs(60),
        multiplier: 2.0,
        jitter: (1.0, 1.0), // no jitter — deterministic
        max_attempts: u32::MAX,
        budget: None,
    };

    let expected = [2u64, 4, 8, 16, 32, 60, 60, 60, 60, 60];
    for (i, &exp_secs) in expected.iter().enumerate() {
        // consecutive_errors is 1-indexed (pre-incremented before decide() call)
        let attempt = (i + 1) as u32;
        let d = next_delay(&policy, attempt, None);
        assert_eq!(
            d,
            Duration::from_secs(exp_secs),
            "attempt {attempt}: expected {exp_secs}s, got {d:?}"
        );
    }
}
