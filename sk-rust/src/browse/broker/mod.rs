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
    /// Transport/HTTP error. The message is pre-sanitized: the request URL
    /// (which contains the bot token as a path component) is stripped via
    /// `reqwest::Error::without_url()` before storing, so this variant is safe
    /// to log or display at any log level without leaking credentials.
    Http(String),
    Api(String),
    Json(String),
    Shutdown,
}

impl std::fmt::Display for BrokerError {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        match self {
            BrokerError::Http(s) => write!(f, "HTTP error: {s}"),
            BrokerError::Api(s) => write!(f, "API error: {s}"),
            BrokerError::Json(s) => write!(f, "JSON parse error: {s}"),
            BrokerError::Shutdown => write!(f, "shutdown requested"),
        }
    }
}

impl std::error::Error for BrokerError {}

impl From<reqwest::Error> for BrokerError {
    fn from(e: reqwest::Error) -> Self {
        // Strip the request URL before converting to a string. Telegram URLs
        // have the form `/bot<TOKEN>/method`, so without_url() prevents the
        // bot token from appearing in any formatted or logged error.
        BrokerError::Http(e.without_url().to_string())
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

/// Returns the largest byte index ≤ `index` that lies on a valid UTF-8 char
/// boundary in `s`.  If `index ≥ s.len()`, returns `s.len()`.
///
/// Equivalent to `str::floor_char_boundary` (stable since Rust 1.91); written
/// here to stay compatible with our MSRV (Rust 1.75).
fn floor_char_boundary(s: &str, index: usize) -> usize {
    let capped = index.min(s.len());
    let mut i = capped;
    // Walk backwards until we hit a valid boundary (is_char_boundary is true
    // at every position that is the start of a Unicode scalar value).
    while i > 0 && !s.is_char_boundary(i) {
        i -= 1;
    }
    i
}

/// Split `text` into chunks of at most `max_chars` *bytes*.
///
/// Prefers splitting at `\n\n` (paragraph boundary), falls back to `\n`,
/// then hard-splits at or before the byte limit, always on a valid UTF-8
/// char boundary.  Mirrors Python `_chunk_text` semantics: trailing
/// whitespace is stripped from each chunk; leading whitespace from the
/// remainder is stripped.
///
/// # Safety
/// Never panics: all slice indices are validated against UTF-8 char
/// boundaries using [`str::floor_char_boundary`] (stable since Rust 1.73).
/// Never produces an infinite loop: each iteration advances by at least
/// one character.
pub fn chunk_text(text: &str, max_chars: usize) -> Vec<String> {
    if max_chars == 0 {
        return vec![];
    }
    if text.len() <= max_chars {
        return vec![text.to_owned()];
    }

    let mut chunks = Vec::new();
    let mut remaining = text;

    while remaining.len() > max_chars {
        // Find the largest byte index ≤ max_chars that sits on a UTF-8 char
        // boundary.  This is always safe: floor_char_boundary is guaranteed
        // to return an index in 0..=remaining.len().
        let byte_limit = floor_char_boundary(remaining, max_chars);

        // If the first character alone exceeds max_chars bytes (e.g. a 4-byte
        // emoji when max_chars = 1), advance past it so we never loop forever.
        let byte_limit = if byte_limit == 0 {
            remaining.chars().next().map_or(1, char::len_utf8)
        } else {
            byte_limit
        };

        let window = &remaining[..byte_limit];

        // Prefer blank-line split, fallback to newline, fallback to hard split.
        let mut split_at = window
            .rfind("\n\n")
            .or_else(|| window.rfind('\n'))
            .unwrap_or(byte_limit);

        // Ensure we always advance (rfind("\n\n") can return 0 when the
        // window starts with "\n\n").
        if split_at == 0 {
            split_at = byte_limit;
        }

        let (chunk, rest) = remaining.split_at(split_at);
        chunks.push(chunk.trim_end().to_owned());
        remaining = rest.trim_start();
    }

    if !remaining.is_empty() {
        chunks.push(remaining.to_owned());
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
    fn broker_error_http_display_never_contains_token() {
        // BrokerError::Http stores a sanitized string (no URL).
        // Verify Display does not accidentally embed a token-shaped string.
        let token = "MY_SECRET_BOT_TOKEN";
        let err = BrokerError::Http("connection refused".to_owned());
        let displayed = format!("{err}");
        assert!(
            !displayed.contains(token),
            "Http variant must not contain token: {displayed}"
        );
        assert!(
            displayed.starts_with("HTTP error:"),
            "Http variant Display should start with 'HTTP error:': {displayed}"
        );
    }

    #[test]
    fn chunk_text_non_ascii_no_panic() {
        // Each emoji is 4 bytes. 1025 emojis = 4100 bytes, slightly over 4096.
        // With a hard byte limit of 4096, a naive slice would land mid-emoji
        // and panic; floor_char_boundary must prevent that.
        let emoji = "😀";
        assert_eq!(emoji.len(), 4, "sanity: emoji is 4 bytes");
        let text: String = emoji.repeat(1025); // 4100 bytes, no newlines
        let chunks = chunk_text(&text, 4096);
        // Must produce chunks; no panic
        assert!(!chunks.is_empty(), "should produce at least one chunk");
        for chunk in &chunks {
            assert!(!chunk.is_empty(), "no empty chunks");
            // Every chunk must be valid UTF-8 (already guaranteed by &str, but
            // verify boundary slicing didn't corrupt anything).
            assert!(std::str::from_utf8(chunk.as_bytes()).is_ok());
        }
        // Reassembling should give back the original text.
        let reassembled: String = chunks.join("");
        assert_eq!(reassembled, text, "chunks must reassemble to original text");
    }

    #[test]
    fn chunk_text_cjk_boundary_no_panic() {
        // CJK characters are 3 bytes each. 1366 CJK chars = 4098 bytes (> 4096).
        let cjk = "中";
        assert_eq!(cjk.len(), 3, "sanity: CJK char is 3 bytes");
        let text: String = cjk.repeat(1366); // 4098 bytes, no newlines
        let chunks = chunk_text(&text, 4096);
        assert!(!chunks.is_empty());
        let reassembled: String = chunks.join("");
        assert_eq!(reassembled, text);
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
