/// Pure retry classification, policy, delay, and jitter logic.
///
/// Scope: sk-rust/src/hooks/rules/retry/{mod,classifier,policy,jitter}.rs
///
/// This module owns only pure logic — no filesystem I/O, no CLI, and no
/// `HookRule` registration.  Queue I/O belongs to `issue-614-retry-queue`;
/// `HookRule` glue belongs to `issue-614-retry-rule`.
///
/// # Public surface
///
/// ```text
/// retry::classifier::classify(payload)     → RetryClass
/// retry::policy::RetryPolicy               → delay_ms / should_retry
/// retry::jitter::jitter_factor_bp()        → u64 in [5000, 10000]
/// retry::compute_delay(policy, attempt, payload) → u64   (convenience)
/// ```
pub mod classifier;
pub mod jitter;
pub mod policy;
pub mod queue;
pub mod rule;

// These re-exports are the public API consumed by sibling tentacles
// (issue-614-retry-queue, issue-614-retry-rule). They are intentionally
// pub even though no in-crate caller exists yet.
#[allow(unused_imports)]
pub use classifier::{classify, RetryClass, MAX_RETRY_AFTER_SECS};
#[allow(unused_imports)]
pub use jitter::jitter_factor_bp;
#[allow(unused_imports)]
pub use policy::RetryPolicy;
#[allow(unused_imports)]
pub use rule::RateLimitRetryRule;

use serde_json::Value;

/// Compute the total retry delay (ms) combining policy back-off, server hint,
/// and random jitter.
///
/// Arguments:
///   - `policy`:  the active `RetryPolicy`.
///   - `attempt`: zero-indexed attempt number that just failed.
///   - `payload`: the error payload (used to extract `Retry-After`).
///
/// Returns 0 when `attempt >= policy.max_attempts` (no retry will occur).
#[allow(dead_code)]
pub fn compute_delay(policy: &RetryPolicy, attempt: u32, payload: &Value) -> u64 {
    if !policy.should_retry(attempt) {
        return 0;
    }
    let server_hint = classifier::extract_retry_after(payload);
    let jitter = jitter_factor_bp();
    policy.delay_ms(attempt, jitter, server_hint)
}

// ---------------------------------------------------------------------------
// Integration-level tests for the combined compute_delay path
// ---------------------------------------------------------------------------

#[cfg(test)]
mod tests {
    use super::*;
    use serde_json::json;

    #[test]
    fn test_compute_delay_no_retry_remaining() {
        let p = RetryPolicy {
            max_attempts: 1,
            ..RetryPolicy::default_policy()
        };
        // Only attempt 0 is allowed; attempt 1 is already exhausted.
        assert_eq!(compute_delay(&p, 1, &json!({})), 0);
    }

    #[test]
    fn test_compute_delay_with_server_hint() {
        let p = RetryPolicy {
            max_attempts: 3,
            base_delay_ms: 1_000,
            backoff_multiplier: 2.0,
            max_delay_ms: 60_000,
            budget_secs: 300,
        };
        let payload = json!({
            "status": 429,
            "headers": { "retry-after": "5" }
        });
        // server hint 5 s > back-off 1 s → delay = 5000 ms
        let delay = compute_delay(&p, 1, &payload);
        assert_eq!(delay, 5_000);
    }

    #[test]
    fn test_compute_delay_jitter_within_bounds() {
        let p = RetryPolicy {
            max_attempts: 3,
            base_delay_ms: 1_000,
            backoff_multiplier: 2.0,
            max_delay_ms: 60_000,
            budget_secs: 300,
        };
        jitter::seed_prng(99);
        let payload = json!({ "status": 429 });
        let delay = compute_delay(&p, 1, &payload);
        // attempt 1 => 2000ms local backoff, multiplied by [0.5, 1.0]
        assert!(
            (1_000..=2_000).contains(&delay),
            "delay {delay} out of expected range"
        );
    }

    #[test]
    fn test_classify_then_compute_flow() {
        let payload = json!({
            "error": { "type": "rate_limit_exceeded", "message": "rate limit" },
            "status": 429,
            "headers": { "retry-after": "2" }
        });

        let cls = classify(&payload);
        assert!(cls.is_retryable());
        assert_eq!(
            cls,
            RetryClass::Retryable {
                retry_after_secs: Some(2)
            }
        );

        let p = RetryPolicy {
            max_attempts: 3,
            ..RetryPolicy::default_policy()
        };
        let delay = compute_delay(&p, 1, &payload);
        assert_eq!(delay, 2_000); // server hint wins
    }

    #[test]
    fn test_not_retryable_classify_and_zero_delay() {
        let payload = json!({
            "error": { "type": "insufficient_quota", "message": "quota exceeded" },
            "status": 429
        });

        let cls = classify(&payload);
        assert!(!cls.is_retryable());

        let p = RetryPolicy::default_policy();
        // Even if policy allows more attempts, the caller should check classify first.
        // compute_delay only skips on attempt budget; it doesn't re-check classify.
        // This test documents the caller's responsibility:
        assert!(p.should_retry(1)); // budget available...
                                    // ...but classify says permanent → caller must honour RetryClass.
    }
}
