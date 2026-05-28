// `seed_prng` is the test-only API; allow dead_code in non-test builds.
#![allow(dead_code)]
//! Stdlib-only deterministic jitter for retry delays.
//!
//! Implements a SplitMix64 pseudo-random number generator seeded from
//! `SystemTime` (nanoseconds) XOR-folded with the current thread's stack
//! address, giving different sequences per thread without requiring any
//! external crate.
//!
//! Properties:
//!   - No `unsafe` code.
//!   - No heap allocation.
//!   - Period 2^64 − suitable for producing thousands of jitter values.
//!   - Output is uniformly distributed across `[0, max_ms]`.
use std::time::{SystemTime, UNIX_EPOCH};

/// Return a jitter value in `[0, max_ms]` milliseconds.
///
/// Each call advances the thread-local PRNG state.  The result is bounded by
/// `max_ms`; when `max_ms == 0` the function returns 0 immediately.
pub fn jitter_ms(max_ms: u64) -> u64 {
    if max_ms == 0 {
        return 0;
    }
    // Draw a 64-bit random value from the thread-local PRNG.
    let r = next_u64();
    // Map uniformly into [0, max_ms] via rejection-free modulo.
    // For simplicity we use `r % (max_ms + 1)`.  The bias is negligible when
    // max_ms << u64::MAX, which is always the case for retry delays.
    r % (max_ms.saturating_add(1))
}

/// Return a multiplicative jitter factor in basis points for `[0.5, 1.0]`.
pub fn jitter_factor_bp() -> u64 {
    5_000 + jitter_ms(5_000)
}

/// Seed the thread-local PRNG with an explicit value.
///
/// Primarily useful in tests to produce deterministic sequences.
/// Production code does not call this — the default seed is derived from time.
pub fn seed_prng(seed: u64) {
    PRNG.with(|cell| {
        cell.set(seed);
    });
    // Mark as seeded so next_u64 does not overwrite with initial_seed().
    PRNG_SEEDED.with(|b| b.set(true));
}

// ---------------------------------------------------------------------------
// SplitMix64 PRNG
// ---------------------------------------------------------------------------

// Thread-local PRNG state (SplitMix64).
// Initialised lazily on first use via `initial_seed()`.
std::thread_local! {
    static PRNG: std::cell::Cell<u64> = const { std::cell::Cell::new(0) };
    static PRNG_SEEDED: std::cell::Cell<bool> = const { std::cell::Cell::new(false) };
}

/// Draw the next 64-bit pseudo-random value from the thread-local SplitMix64 state.
fn next_u64() -> u64 {
    PRNG.with(|cell| {
        // Lazy seed on first use.
        let state = if !PRNG_SEEDED.with(|b| b.get()) {
            let s = initial_seed();
            cell.set(s);
            PRNG_SEEDED.with(|b| b.set(true));
            s
        } else {
            cell.get()
        };

        // SplitMix64 step.
        let next_state = state.wrapping_add(0x9e37_79b9_7f4a_7c15);
        cell.set(next_state);

        splitmix64_mix(next_state)
    })
}

/// SplitMix64 finalizer.
#[inline(always)]
fn splitmix64_mix(mut z: u64) -> u64 {
    z = (z ^ (z >> 30)).wrapping_mul(0xbf58_476d_1ce4_e5b9);
    z = (z ^ (z >> 27)).wrapping_mul(0x94d0_49bb_1331_11eb);
    z ^ (z >> 31)
}

/// Derive an initial seed from wall-clock nanoseconds XOR the stack frame
/// address (a reasonable proxy for thread identity without unsafe code).
///
/// Falls back gracefully: if `SystemTime::now()` returns before the epoch
/// (shouldn't happen in practice), the address component alone is used.
fn initial_seed() -> u64 {
    // Nanosecond timestamp since UNIX_EPOCH.
    let time_ns = SystemTime::now()
        .duration_since(UNIX_EPOCH)
        .map(|d| d.subsec_nanos() as u64)
        .unwrap_or(0xdead_beef_cafe_babe);

    // Stack-frame address as a thread-differentiation component.
    let addr = stack_address();

    // XOR-fold to combine both sources.
    time_ns ^ addr
}

/// Return a 64-bit value derived from the address of a stack variable.
///
/// This gives a different value per thread since each thread has its own stack.
/// No `unsafe` required — Rust allows taking the address of local variables via
/// a reference cast.
#[inline(never)]
fn stack_address() -> u64 {
    let x: u8 = 0;
    let ptr = &x as *const u8 as usize;
    ptr as u64
}

// ---------------------------------------------------------------------------
// Tests
// ---------------------------------------------------------------------------

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn test_jitter_zero_max_returns_zero() {
        assert_eq!(jitter_ms(0), 0);
    }

    #[test]
    fn test_jitter_within_bounds() {
        for max in [1u64, 10, 100, 1_000, 60_000] {
            for _ in 0..50 {
                let j = jitter_ms(max);
                assert!(j <= max, "jitter_ms({max}) returned {j}, expected <= {max}");
            }
        }
    }

    #[test]
    fn test_jitter_distribution_not_constant() {
        // Seed PRNG deterministically and confirm not all values are the same.
        seed_prng(0x1234_5678_9abc_def0);
        let values: Vec<u64> = (0..20).map(|_| jitter_ms(1_000)).collect();
        let distinct: std::collections::HashSet<u64> = values.iter().copied().collect();
        assert!(
            distinct.len() > 1,
            "All jitter values identical — PRNG may be broken: {values:?}"
        );
    }

    #[test]
    fn test_jitter_deterministic_with_seed() {
        seed_prng(42);
        let first: Vec<u64> = (0..10).map(|_| jitter_ms(1_000)).collect();
        seed_prng(42);
        let second: Vec<u64> = (0..10).map(|_| jitter_ms(1_000)).collect();
        assert_eq!(
            first, second,
            "PRNG with same seed should produce same sequence"
        );
    }

    #[test]
    fn test_jitter_max_one() {
        // When max_ms = 1, every result must be 0 or 1.
        for _ in 0..100 {
            let j = jitter_ms(1);
            assert!(j == 0 || j == 1);
        }
    }

    #[test]
    fn test_splitmix64_mix_nonzero_input_gives_nonzero() {
        // The SplitMix64 mixer of 0 is 0 (mathematical property of the algorithm).
        // Verify that a non-zero input produces a non-zero output, which confirms
        // the mixer is running (not short-circuiting to a constant 0).
        let out = splitmix64_mix(0x9e3779b97f4a7c15);
        assert_ne!(out, 0, "splitmix64_mix of non-zero should not return 0");
    }

    #[test]
    fn test_next_u64_advances_state() {
        seed_prng(1);
        let a = next_u64();
        let b = next_u64();
        assert_ne!(
            a, b,
            "Consecutive next_u64 calls should produce different values"
        );
    }

    #[test]
    fn test_jitter_overflow_guard_max_u64() {
        // max_ms near u64::MAX — should not panic (saturating_add prevents overflow in modulo).
        let _ = jitter_ms(u64::MAX - 1);
        let _ = jitter_ms(u64::MAX);
    }

    #[test]
    fn test_jitter_ms_60_seconds_range() {
        // Typical use-case: up to 60 s of jitter.
        seed_prng(0xabcdef01);
        for _ in 0..100 {
            let j = jitter_ms(60_000);
            assert!(j <= 60_000);
        }
    }

    #[test]
    fn test_jitter_factor_bp_bounds() {
        seed_prng(0x614);
        for _ in 0..100 {
            let factor = jitter_factor_bp();
            assert!((5_000..=10_000).contains(&factor));
        }
    }
}
