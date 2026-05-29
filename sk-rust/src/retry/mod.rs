//! Crate-wide shared retry library.
//!
//! Provides a unified [`RetryPolicy`], error classification, delay calculation,
//! and retry decision logic for use across all native retry call sites:
//! the Telegram broker, file-rename loop in sync_markers, and the embedding
//! HTTP client.
//!
//! # Security
//! [`classify_error`] inspects only the *structure* of the error text (regex
//! pattern matching).  It never returns raw error text — only a [`RetryKind`]
//! enum variant — so credentials embedded in error messages cannot leak.
//!
//! # Zero new dependencies
//! Uses only `regex` (already a workspace dep) and a stdlib SplitMix64 PRNG
//! for jitter.  No `fastrand` or any other new crate is introduced.

use std::sync::OnceLock;
use std::time::Duration;

use regex::RegexSet;

// ── Public types ──────────────────────────────────────────────────────────────

/// Retry policy parameters.
///
/// All durations use [`Duration`] (monotonic).  The budget check uses elapsed
/// wall-time supplied by the caller via [`decide`] — callers must use
/// [`std::time::Instant`], never `SystemTime`, for elapsed computation.
#[derive(Debug, Clone)]
pub struct RetryPolicy {
    /// Base delay for the first retry.  Default: 1 s.
    pub base: Duration,
    /// Maximum delay (exponential cap).  Default: 60 s.
    pub cap: Duration,
    /// Exponential back-off multiplier.  Default: 1.6.
    pub multiplier: f64,
    /// Jitter range `(lo_factor, hi_factor)`.  Default: `(0.5, 1.0)`.
    ///
    /// When `lo >= hi`, jitter is disabled and the exact computed delay is used.
    pub jitter: (f64, f64),
    /// Maximum number of retry attempts before stopping.  Default: 5.
    pub max_attempts: u32,
    /// Optional wall-clock budget.  `None` means no budget limit.  Default: `Some(5 min)`.
    pub budget: Option<Duration>,
}

impl Default for RetryPolicy {
    fn default() -> Self {
        RetryPolicy {
            base: Duration::from_secs(1),
            cap: Duration::from_secs(60),
            multiplier: 1.6,
            jitter: (0.5, 1.0),
            max_attempts: 5,
            budget: Some(Duration::from_secs(300)),
        }
    }
}

/// Classification of why a retry is occurring.
#[derive(Debug, Clone, PartialEq, Eq)]
pub enum RetryKind {
    /// HTTP 429 / "rate limit exceeded".
    RateLimit,
    /// HTTP 5xx server error.
    Server5xx,
    /// Explicit throttling signal.
    Throttle,
    /// Backend overloaded / over capacity.
    Overloaded,
    /// API quota exhausted (distinct from per-request rate limiting).
    Quota,
    /// Authentication / authorization failure.
    Auth,
    /// Non-empty error that matches no specific pattern; retrying may help.
    Unknown,
}

/// Decision returned by [`decide`].
#[derive(Debug)]
pub enum RetryDecision {
    /// Caller should sleep `Duration` then retry; error was classified as `RetryKind`.
    Retry(Duration, RetryKind),
    /// Caller should stop retrying.
    Stop(StopReason),
}

/// Reason a retry sequence is terminated.
#[derive(Debug, PartialEq, Eq)]
pub enum StopReason {
    /// Reached `policy.max_attempts`.
    MaxAttempts,
    /// Elapsed time exceeded `policy.budget`.
    Budget,
    /// Error is an authentication failure; retrying will not help.
    AuthFailure,
    /// API quota exhausted; retrying will not help immediately.
    QuotaExhausted,
    /// Upstream sent `x-should-retry: false`.
    XShouldRetryFalse,
    /// Error text is empty (truly unclassifiable).
    NonRetryable,
    /// An external retry listener requested that the sequence be aborted.
    ListenerAbort,
}

// ── Pattern indices (must stay in sync with PATTERNS array below) ─────────────

const IDX_AUTH: usize = 0;
const IDX_QUOTA: usize = 1;
const IDX_RATE_LIMIT: usize = 2;
const IDX_THROTTLE: usize = 3;
const IDX_OVERLOADED: usize = 4;
const IDX_SERVER5XX: usize = 5;
const IDX_NO_RETRY: usize = 6;

/// Lazily-compiled pattern set.  Compiled exactly once per process.
static PATTERNS: OnceLock<RegexSet> = OnceLock::new();

fn patterns() -> &'static RegexSet {
    PATTERNS.get_or_init(|| {
        RegexSet::new([
            // 0: Auth — do not retry
            r"(?i)\b(401|403|unauthori[sz]ed|forbidden|invalid[._]api[._]key|api[._]key[._]invalid|authentication[._]failed)\b",
            // 1: Quota — do not retry
            r"(?i)\b(insufficient[._]quota|quota[._]exceeded|billing[._]hard[._]limit)\b",
            // 2: RateLimit — retry
            r"(?i)\b(429|rate[._-]?limit|too[\s._-]many[\s._-]requests|ratelimited|rate_limit_exceeded)\b",
            // 3: Throttle — retry
            r"(?i)\bthrottl",
            // 4: Overloaded — retry
            r"(?i)\b(overload\w*|over[._]capacity|server[._]overload)\b",
            // 5: Server5xx — retry
            r"(?i)\b(5[0-9]{2}|internal[._]server[._]error|bad[._]gateway|service[._]unavailable|gateway[._]timeout)\b",
            // 6: x-should-retry: false — explicit no-retry signal
            r"(?i)x-should-retry\s*:\s*false",
        ])
        .expect("retry: invalid regex pattern -- compile-time bug")
    })
}

// ── Public API ────────────────────────────────────────────────────────────────

/// Classify an error string into a [`RetryKind`].
///
/// Returns `None` only when `text` is empty (unclassifiable).  Returns
/// `Some(RetryKind::Unknown)` for non-empty text that matches no pattern.
///
/// # Security
/// Only the enum variant is returned.  The raw `text` is never stored,
/// returned, or logged, so credentials in error messages cannot leak.
pub fn classify_error(text: &str) -> Option<RetryKind> {
    if text.is_empty() {
        return None;
    }
    let ms = patterns().matches(text);
    // Priority: Auth > Quota > RateLimit > Throttle > Overloaded > Server5xx > Unknown
    if ms.matched(IDX_AUTH) {
        return Some(RetryKind::Auth);
    }
    if ms.matched(IDX_QUOTA) {
        return Some(RetryKind::Quota);
    }
    if ms.matched(IDX_RATE_LIMIT) {
        return Some(RetryKind::RateLimit);
    }
    if ms.matched(IDX_THROTTLE) {
        return Some(RetryKind::Throttle);
    }
    if ms.matched(IDX_OVERLOADED) {
        return Some(RetryKind::Overloaded);
    }
    if ms.matched(IDX_SERVER5XX) {
        return Some(RetryKind::Server5xx);
    }
    Some(RetryKind::Unknown)
}

/// Compute the next retry delay.
///
/// # Precedence
/// 1. `retry_after` — server-supplied hint (clamped to `policy.cap`).
/// 2. Exponential back-off: `base * multiplier^attempt`, clamped to `cap`.
/// 3. Jitter: if `policy.jitter.0 < policy.jitter.1`, a random factor in
///    `[lo, hi)` is applied; otherwise the exact computed delay is returned.
///
/// # Overflow guard
/// When `attempt > 64`, returns `policy.cap` immediately without computing
/// the exponentiation (avoids `powi` overflow on absurdly large attempts).
pub fn next_delay(policy: &RetryPolicy, attempt: u32, retry_after: Option<Duration>) -> Duration {
    // Server-supplied Retry-After wins.
    if let Some(ra) = retry_after {
        if ra > Duration::ZERO {
            return ra.min(policy.cap);
        }
    }

    // Compute exponential back-off, guarded against overflow.
    let base_delay = compute_backoff(policy, attempt);

    // Apply jitter.
    apply_jitter(base_delay, policy)
}

/// Decide whether to retry or stop.
///
/// Checks (in order):
/// 1. `attempt >= policy.max_attempts` → [`StopReason::MaxAttempts`]
/// 2. `elapsed >= policy.budget` (when budget is `Some`) → [`StopReason::Budget`]
/// 3. `x-should-retry: false` in `err` → [`StopReason::XShouldRetryFalse`]
/// 4. [`classify_error`] returns `None` → [`StopReason::NonRetryable`]
/// 5. [`RetryKind::Auth`] → [`StopReason::AuthFailure`]
/// 6. [`RetryKind::Quota`] → [`StopReason::QuotaExhausted`]
/// 7. All other kinds → [`RetryDecision::Retry`] with delay from [`next_delay`]
pub fn decide(
    policy: &RetryPolicy,
    attempt: u32,
    elapsed: Duration,
    err: &str,
    retry_after: Option<Duration>,
) -> RetryDecision {
    // 1. Max attempts guard.
    if attempt >= policy.max_attempts {
        return RetryDecision::Stop(StopReason::MaxAttempts);
    }

    // 2. Budget guard (budget=None means unlimited).
    if let Some(budget) = policy.budget {
        if elapsed >= budget {
            return RetryDecision::Stop(StopReason::Budget);
        }
    }

    // 3. x-should-retry: false — upstream explicitly forbids retry.
    if !err.is_empty() && patterns().matches(err).matched(IDX_NO_RETRY) {
        return RetryDecision::Stop(StopReason::XShouldRetryFalse);
    }

    // 4–6. Classify error.
    match classify_error(err) {
        None => RetryDecision::Stop(StopReason::NonRetryable),
        Some(RetryKind::Auth) => RetryDecision::Stop(StopReason::AuthFailure),
        Some(RetryKind::Quota) => RetryDecision::Stop(StopReason::QuotaExhausted),
        Some(kind) => {
            let delay = next_delay(policy, attempt, retry_after);
            RetryDecision::Retry(delay, kind)
        }
    }
}

// ── Internals ─────────────────────────────────────────────────────────────────

/// Compute exponential back-off delay, clamped to `policy.cap`.
///
/// Overflow guard: returns `cap` immediately for `attempt > 64`.
fn compute_backoff(policy: &RetryPolicy, attempt: u32) -> Duration {
    if attempt > 64 {
        return policy.cap;
    }
    let m = policy.multiplier;
    if !m.is_finite() || m <= 0.0 {
        return policy.cap;
    }
    let delay_f = policy.base.as_secs_f64() * m.powi(attempt as i32);
    if !delay_f.is_finite() || delay_f >= policy.cap.as_secs_f64() {
        return policy.cap;
    }
    Duration::from_secs_f64(delay_f.max(0.0)).min(policy.cap)
}

/// Apply jitter to `base_delay` using the `policy.jitter` range.
///
/// When `lo >= hi` (or either is non-finite), returns `base_delay` unchanged
/// (no-jitter / deterministic mode).
fn apply_jitter(base_delay: Duration, policy: &RetryPolicy) -> Duration {
    let (lo, hi) = policy.jitter;
    if !lo.is_finite() || !hi.is_finite() || lo >= hi {
        return base_delay;
    }
    let factor = jitter_in_range(lo, hi);
    let jittered = base_delay.as_secs_f64() * factor;
    Duration::from_secs_f64(jittered.max(0.0)).min(policy.cap)
}

/// Return a random `f64` in `[lo, hi)` using the thread-local SplitMix64 PRNG.
fn jitter_in_range(lo: f64, hi: f64) -> f64 {
    // Map a 53-bit integer uniformly to [0.0, 1.0).
    let r = next_u64_tl();
    let t = (r >> 11) as f64 / (1u64 << 53) as f64;
    lo + t * (hi - lo)
}

// ── SplitMix64 thread-local PRNG ─────────────────────────────────────────────
//
// A minimal, zero-allocation, no-unsafe SplitMix64 PRNG.  Each thread gets
// a distinct seed derived from wall-clock nanoseconds XOR'd with the stack
// frame address (proxy for thread identity).  No external crate needed.

std::thread_local! {
    static PRNG_STATE: std::cell::Cell<u64> = const { std::cell::Cell::new(0) };
    static PRNG_SEEDED: std::cell::Cell<bool> = const { std::cell::Cell::new(false) };
}

fn next_u64_tl() -> u64 {
    PRNG_STATE.with(|cell| {
        if !PRNG_SEEDED.with(|b| b.get()) {
            let seed = prng_initial_seed();
            cell.set(seed);
            PRNG_SEEDED.with(|b| b.set(true));
        }
        let next = cell.get().wrapping_add(0x9e37_79b9_7f4a_7c15);
        cell.set(next);
        splitmix64_mix(next)
    })
}

#[inline(always)]
fn splitmix64_mix(mut z: u64) -> u64 {
    z = (z ^ (z >> 30)).wrapping_mul(0xbf58_476d_1ce4_e5b9);
    z = (z ^ (z >> 27)).wrapping_mul(0x94d0_49bb_1331_11eb);
    z ^ (z >> 31)
}

fn prng_initial_seed() -> u64 {
    use std::time::{SystemTime, UNIX_EPOCH};
    let time_ns = SystemTime::now()
        .duration_since(UNIX_EPOCH)
        .map(|d| d.subsec_nanos() as u64)
        .unwrap_or(0xdead_beef_cafe_babe);
    let addr = stack_addr();
    time_ns ^ addr
}

#[inline(never)]
fn stack_addr() -> u64 {
    let x: u8 = 0;
    &x as *const u8 as u64
}

/// Seed the thread-local PRNG to a known value (test use only).
#[cfg(test)]
pub fn seed_prng(seed: u64) {
    PRNG_STATE.with(|c| c.set(seed));
    PRNG_SEEDED.with(|b| b.set(true));
}

#[cfg(test)]
mod tests;
