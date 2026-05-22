//! Async broker core — behind `browse-broker` feature (issue #454 PR-A).
//!
//! Provides the [`Broker`] trait, shared types, retry/backoff helper,
//! and graceful-shutdown via [`tokio_util::sync::CancellationToken`].
//!
//! # Out of scope (PR-A)
//! - Discord, Ably, Slack real implementations (stub files only, see follow-up issues).
//! - Wiring into CLI/server/router — this module is feature-gated and unreachable
//!   from default builds.

use std::time::Duration;

pub use tokio_util::sync::CancellationToken;

pub mod ably;
pub mod discord;
pub mod slack;
pub mod telegram;

// ── Shared types ──────────────────────────────────────────────────────────────

/// Configuration shared across broker implementations.
#[derive(Debug, Clone)]
pub struct BrokerConfig {
    /// Only this user ID may interact with the broker (0 = disabled, block all).
    pub authorized_user_id: i64,
    /// Long-poll timeout in seconds.
    pub poll_timeout_secs: u64,
    /// Minimum interval between outbound send operations.
    pub min_send_interval: Duration,
}

impl Default for BrokerConfig {
    fn default() -> Self {
        Self {
            authorized_user_id: 0,
            poll_timeout_secs: 25,
            min_send_interval: Duration::from_millis(1050),
        }
    }
}

/// Errors that may occur during broker operation.
#[derive(Debug)]
pub enum BrokerError {
    Http(reqwest::Error),
    Api(String),
    Json(String),
    Shutdown,
}

impl std::fmt::Display for BrokerError {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        match self {
            BrokerError::Http(e) => write!(f, "HTTP error: {e}"),
            BrokerError::Api(s) => write!(f, "API error: {s}"),
            BrokerError::Json(s) => write!(f, "JSON parse error: {s}"),
            BrokerError::Shutdown => write!(f, "shutdown requested"),
        }
    }
}

impl std::error::Error for BrokerError {
    fn source(&self) -> Option<&(dyn std::error::Error + 'static)> {
        match self {
            BrokerError::Http(e) => Some(e),
            _ => None,
        }
    }
}

impl From<reqwest::Error> for BrokerError {
    fn from(e: reqwest::Error) -> Self {
        BrokerError::Http(e)
    }
}

// ── Broker trait ──────────────────────────────────────────────────────────────

/// Async polling broker.  Each implementation long-polls its platform API and
/// dispatches incoming commands to a [`telegram::KnowledgeSource`].
///
/// Implementations MUST:
/// - respect the `token` cancellation signal and return within ≤1 s of cancellation.
/// - never panic on transport failures.
/// - never log raw credentials.
pub trait Broker: Send + Sync {
    /// Start the polling loop.  Returns when `token` is cancelled or a fatal error occurs.
    fn run(
        &self,
        token: CancellationToken,
    ) -> impl std::future::Future<Output = anyhow::Result<()>> + Send;
}

// ── Retry/backoff helper ──────────────────────────────────────────────────────

/// Sleep for `min(2^consecutive_errors, max_secs)` seconds.
///
/// Deterministic: no jitter added (makes unit tests predictable).
/// The cap prevents indefinite delays.
pub async fn backoff_sleep(consecutive_errors: u32, max_secs: u64) {
    let secs = (1u64 << consecutive_errors.min(63)).min(max_secs);
    tokio::time::sleep(Duration::from_secs(secs)).await;
}

// ── Message chunking ──────────────────────────────────────────────────────────

/// Split `text` into chunks of at most `max_chars` characters.
///
/// Prefers splitting at `\n\n` (paragraph boundary), falls back to `\n`,
/// then hard-splits at `max_chars`.  Mirrors Python `_chunk_text` semantics:
/// trailing whitespace is stripped from each chunk; leading whitespace from
/// the remainder is stripped.
pub fn chunk_text(text: &str, max_chars: usize) -> Vec<String> {
    if text.len() <= max_chars {
        return vec![text.to_owned()];
    }

    let mut chunks = Vec::new();
    let mut remaining = text.to_owned();

    while remaining.len() > max_chars {
        let window = &remaining[..max_chars];

        // Prefer blank-line split, fallback to newline, fallback to hard split.
        let split_at = window
            .rfind("\n\n")
            .or_else(|| window.rfind('\n'))
            .unwrap_or(max_chars);

        let (chunk, rest) = remaining.split_at(split_at);
        chunks.push(chunk.trim_end().to_owned());
        remaining = rest.trim_start().to_owned();
    }

    if !remaining.is_empty() {
        chunks.push(remaining);
    }

    chunks
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn chunk_text_short() {
        let chunks = chunk_text("hello", 4096);
        assert_eq!(chunks, vec!["hello"]);
    }

    #[test]
    fn chunk_text_blank_line_split() {
        let para1 = "a".repeat(100);
        let para2 = "b".repeat(100);
        let text = format!("{}\n\n{}", para1, para2);
        let chunks = chunk_text(&text, 110);
        assert!(chunks.len() >= 2);
        assert!(chunks[0].ends_with(&"a".repeat(100)));
    }

    #[test]
    fn chunk_text_newline_fallback() {
        let line1 = "a".repeat(60);
        let line2 = "b".repeat(60);
        let text = format!("{}\n{}", line1, line2);
        let chunks = chunk_text(&text, 70);
        assert!(chunks.len() >= 2);
    }

    #[test]
    fn chunk_text_hard_split() {
        let text = "x".repeat(200);
        let chunks = chunk_text(&text, 100);
        assert_eq!(chunks.len(), 2);
        assert_eq!(chunks[0].len(), 100);
        assert_eq!(chunks[1].len(), 100);
    }

    #[test]
    fn backoff_cap_logic() {
        // Verify the cap arithmetic used in backoff_sleep without actually sleeping.
        // The production function uses `consecutive_errors.min(63)` to avoid overflow,
        // but here we directly test the final `.min(max_secs)` capping.
        fn cap(errors: u32, max: u64) -> u64 {
            (1u64 << errors.min(63)).min(max)
        }
        assert_eq!(cap(0, 1), 1); // 2^0 = 1, capped at 1
        assert_eq!(cap(3, 60), 8); // 2^3 = 8, not capped
        assert_eq!(cap(6, 60), 60); // 2^6 = 64, capped at 60
        assert_eq!(cap(63, 60), 60); // huge shift, capped at 60
    }
}
