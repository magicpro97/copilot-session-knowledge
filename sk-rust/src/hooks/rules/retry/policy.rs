// Public items are the API for sibling tentacles; allow dead_code until
// issue-614-retry-rule uses them.
#![allow(dead_code)]
//! Retry policy for issue #614 Phase-1 rate-limit telemetry.
//!
//! The issue defines attempts as zero-indexed queue decisions:
//! `delay = min(cap, base * multiplier^attempt) * uniform(0.5, 1.0)`.
//! A parseable `Retry-After` hint in `(0, 60]` seconds is used directly.

/// Jitter factor basis points for the lower bound (50%).
pub const MIN_JITTER_BP: u64 = 5_000;
/// Jitter factor basis points for the upper bound (100%).
pub const MAX_JITTER_BP: u64 = 10_000;
/// Hard overflow guard for exponentiation.
pub const MAX_SAFE_ATTEMPT: u32 = 64;
/// Maximum supported retry budget (24h) for env/config consumers.
pub const MAX_BUDGET_SECS: u64 = 86_400;

/// Retry policy parameters.
#[derive(Debug, Clone, PartialEq)]
pub struct RetryPolicy {
    /// Maximum queued retry attempts before stop.
    pub max_attempts: u32,
    /// Base delay in milliseconds.
    pub base_delay_ms: u64,
    /// Maximum local backoff delay in milliseconds.
    pub max_delay_ms: u64,
    /// Multiplicative back-off factor.
    pub backoff_multiplier: f64,
    /// Total wall-clock budget in seconds.
    pub budget_secs: u64,
}

impl RetryPolicy {
    /// Issue #614 defaults:
    /// max_attempts=5, base=1s, cap=60s, multiplier=1.6, budget=300s.
    pub const fn default_policy() -> Self {
        RetryPolicy {
            max_attempts: 5,
            base_delay_ms: 1_000,
            max_delay_ms: 60_000,
            backoff_multiplier: 1.6,
            budget_secs: 300,
        }
    }

    /// Whether a retry decision may still be queued for `attempt`.
    ///
    /// `attempt` is zero-indexed; `attempt >= max_attempts` stops.
    #[inline]
    pub fn should_retry(&self, attempt: u32) -> bool {
        attempt < self.max_attempts
    }

    /// Whether elapsed wall-clock time is still within retry budget.
    #[inline]
    pub fn within_budget(&self, elapsed_secs: u64) -> bool {
        elapsed_secs <= self.budget_secs.min(MAX_BUDGET_SECS)
    }

    /// Compute delay in milliseconds.
    ///
    /// `jitter_bp` is a basis-point factor in `[5000, 10000]` produced by
    /// `jitter::jitter_factor_bp()`. Values outside the range are clamped.
    pub fn delay_ms(&self, attempt: u32, jitter_bp: u64, server_hint_secs: Option<u64>) -> u64 {
        if let Some(secs @ 1..=60) = server_hint_secs {
            return secs.saturating_mul(1_000);
        }

        let local_ms = self.compute_backoff(attempt);
        let factor = jitter_bp.clamp(MIN_JITTER_BP, MAX_JITTER_BP);
        local_ms.saturating_mul(factor) / MAX_JITTER_BP
    }

    /// Exponential back-off with overflow protection.
    fn compute_backoff(&self, attempt: u32) -> u64 {
        if attempt > MAX_SAFE_ATTEMPT {
            return self.max_delay_ms;
        }

        let multiplier = if self.backoff_multiplier.is_finite() && self.backoff_multiplier > 0.0 {
            self.backoff_multiplier
        } else {
            return self.max_delay_ms;
        };

        let delay_f = (self.base_delay_ms as f64) * multiplier.powi(attempt as i32);
        if !delay_f.is_finite() || delay_f >= self.max_delay_ms as f64 {
            self.max_delay_ms
        } else {
            delay_f as u64
        }
    }
}

impl Default for RetryPolicy {
    fn default() -> Self {
        Self::default_policy()
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn default_policy_matches_issue_614() {
        let p = RetryPolicy::default_policy();
        assert_eq!(p.max_attempts, 5);
        assert_eq!(p.base_delay_ms, 1_000);
        assert_eq!(p.max_delay_ms, 60_000);
        assert_eq!(p.backoff_multiplier, 1.6);
        assert_eq!(p.budget_secs, 300);
    }

    #[test]
    fn should_retry_is_zero_indexed() {
        let p = RetryPolicy::default_policy();
        assert!(p.should_retry(0));
        assert!(p.should_retry(4));
        assert!(!p.should_retry(5));
    }

    #[test]
    fn budget_is_capped_and_inclusive() {
        let p = RetryPolicy {
            budget_secs: 999_999,
            ..RetryPolicy::default_policy()
        };
        assert!(p.within_budget(MAX_BUDGET_SECS));
        assert!(!p.within_budget(MAX_BUDGET_SECS + 1));
    }

    #[test]
    fn delay_uses_local_backoff_times_jitter_factor() {
        let p = RetryPolicy {
            base_delay_ms: 1_000,
            backoff_multiplier: 2.0,
            max_delay_ms: 60_000,
            ..RetryPolicy::default_policy()
        };
        assert_eq!(p.delay_ms(0, 10_000, None), 1_000);
        assert_eq!(p.delay_ms(1, 10_000, None), 2_000);
        assert_eq!(p.delay_ms(2, 5_000, None), 2_000);
    }

    #[test]
    fn jitter_factor_is_clamped() {
        let p = RetryPolicy::default_policy();
        assert_eq!(p.delay_ms(0, 0, None), 500);
        assert_eq!(p.delay_ms(0, 99_999, None), 1_000);
    }

    #[test]
    fn server_hint_in_range_wins_directly() {
        let p = RetryPolicy::default_policy();
        assert_eq!(p.delay_ms(0, 5_000, Some(5)), 5_000);
        assert_eq!(p.delay_ms(10, 5_000, Some(60)), 60_000);
    }

    #[test]
    fn server_hint_zero_or_over_cap_uses_local_backoff() {
        let p = RetryPolicy::default_policy();
        assert_eq!(p.delay_ms(0, 10_000, Some(0)), 1_000);
        assert_eq!(p.delay_ms(0, 10_000, Some(61)), 1_000);
    }

    #[test]
    fn attempt_overflow_caps_without_panic() {
        let p = RetryPolicy::default_policy();
        assert_eq!(
            p.delay_ms(MAX_SAFE_ATTEMPT + 1, 10_000, None),
            p.max_delay_ms
        );
        assert_eq!(p.delay_ms(u32::MAX, 10_000, None), p.max_delay_ms);
    }

    #[test]
    fn invalid_multiplier_caps() {
        let p = RetryPolicy {
            backoff_multiplier: f64::NAN,
            ..RetryPolicy::default_policy()
        };
        assert_eq!(p.delay_ms(0, 10_000, None), p.max_delay_ms);
    }
}
