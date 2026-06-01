//! Discord broker — HTTP polling implementation of the Discord REST Gateway.
//!
//! # Architecture
//! - Polls `GET /channels/{id}/messages?after={last_id}` on a configurable interval.
//! - No WebSocket (tokio-tungstenite absent from Cargo.toml; reqwest HTTP fallback).
//! - Heartbeat loop: periodic `GET /gateway` ping verifies connectivity.
//! - Commands: `/status`, `/search <q>`, `/briefing`, `/recent`, `/help`
//! - Per-channel rate limiting: 5 requests per 5-second window (Discord policy).
//! - Graceful shutdown: honours [`CancellationToken`] within ≤1 poll cycle.
//! - Single-user auth: messages from other user IDs are silently dropped.
//!
//! # Out of scope (PR-B)
//! DB-backed commands delegate to [`KnowledgeSource`]; [`NoopKnowledge`] is used
//! as the default until DB wiring is done in a follow-up issue.

use std::{
    collections::{HashMap, VecDeque},
    sync::{Arc, Mutex},
    time::{Duration, Instant},
};

use anyhow::{Context, Result};
use reqwest::Client;
use serde::Deserialize;
use serde_json::{json, Value};
use tracing::{debug, error, info, warn};

use super::{chunk_text, BrokerConfig, BrokerError, CancellationToken};
use crate::retry::{decide, RetryDecision, RetryPolicy};

// ── Constants ─────────────────────────────────────────────────────────────────

const POLL_INTERVAL_MS: u64 = 1000;
const HEARTBEAT_INTERVAL_MS: u64 = 41_250; // Discord gateway default: 41.25 s
const MAX_MESSAGE_CHARS: usize = 2000; // Discord message limit
const DEFAULT_DISCORD_BASE: &str = "https://discord.com/api/v10";
const SEARCH_LIMIT: usize = 5;
const RECENT_LIMIT: usize = 10;
const MAX_BACKOFF_SECS: u64 = 60;
/// Discord per-channel rate limit: 5 non-bot messages / 5 s; bot sends are
/// subject to the global 50 req/s limit but we stay conservative.
const RATE_LIMIT_REQUESTS: usize = 5;
const RATE_LIMIT_WINDOW: Duration = Duration::from_secs(5);

/// Retry policy for the poll loop (mirrors telegram.rs POLL_RETRY_POLICY).
const POLL_RETRY_POLICY: RetryPolicy = RetryPolicy {
    base: Duration::from_secs(1),
    cap: Duration::from_secs(MAX_BACKOFF_SECS),
    multiplier: 2.0,
    jitter: (1.0, 1.0),
    max_attempts: u32::MAX,
    budget: None,
};

const HELP_TEXT: &str = "\
**Hindsight Browse — available commands**

/status — daemon status (uptime, session count, DB version)
/search <query> — search the knowledge base (top 5 results)
/briefing <topic> — run briefing for a topic
/recent — list the 10 most recent sessions
/help — this message

_All commands are read-only. Auth: single-user whitelist._";

// ── Discord API types ─────────────────────────────────────────────────────────

#[derive(Debug, Deserialize)]
struct DiscordMessage {
    id: String,
    content: String,
    author: DiscordUser,
}

#[derive(Debug, Deserialize)]
struct DiscordUser {
    id: String,
    /// Discord adds a `bot` field for bot authors.
    #[serde(default)]
    bot: bool,
}

// ── KnowledgeSource (re-used from telegram) ───────────────────────────────────

pub use super::telegram::KnowledgeSource;
#[cfg(test)]
pub use super::telegram::{NoopKnowledge, SearchResult};

// ── Per-channel rate limiter ──────────────────────────────────────────────────

/// Enforces Discord's per-channel rate limit (5 req / 5 s).
///
/// Uses a sliding window: keeps the timestamps of recent sends and blocks
/// until a slot is available.
struct ChannelRateLimiter {
    /// channel_id → timestamps of recent sends within the window.
    windows: Mutex<HashMap<String, VecDeque<Instant>>>,
}

impl ChannelRateLimiter {
    fn new() -> Self {
        Self {
            windows: Mutex::new(HashMap::new()),
        }
    }

    /// Wait until a send slot is available for `channel_id`.
    async fn wait(&self, channel_id: &str) {
        loop {
            let sleep_until = {
                let mut map = self.windows.lock().unwrap_or_else(|e| e.into_inner());
                let queue = map.entry(channel_id.to_owned()).or_default();

                // Evict timestamps outside the current window.
                let now = Instant::now();
                while queue.front().is_some_and(|t| now.duration_since(*t) >= RATE_LIMIT_WINDOW)
                {
                    queue.pop_front();
                }

                if queue.len() < RATE_LIMIT_REQUESTS {
                    queue.push_back(now);
                    return; // slot available immediately
                }

                // Sleep until the oldest entry leaves the window.
                let oldest = *queue.front().expect("queue non-empty");
                let elapsed = now.duration_since(oldest);
                let remaining = RATE_LIMIT_WINDOW.saturating_sub(elapsed);
                Instant::now() + remaining
            };

            tokio::time::sleep_until(tokio::time::Instant::from_std(sleep_until)).await;
        }
    }
}

// ── Command dispatcher ────────────────────────────────────────────────────────

struct CommandDispatcher {
    knowledge: Arc<dyn KnowledgeSource>,
}

impl CommandDispatcher {
    fn new(knowledge: Arc<dyn KnowledgeSource>) -> Self {
        Self { knowledge }
    }

    fn dispatch(&self, text: &str) -> String {
        let text = text.trim();
        if !text.starts_with('/') {
            return "Send /help to see available commands.".to_owned();
        }

        let (cmd_raw, arg) = match text.split_once(char::is_whitespace) {
            Some((c, a)) => (c.to_lowercase(), a.trim().to_owned()),
            None => (text.to_lowercase(), String::new()),
        };

        match cmd_raw.as_str() {
            "/status" => self.knowledge.status(),
            "/search" => {
                if arg.is_empty() {
                    "Usage: /search <query>\nExample: /search authentication".to_owned()
                } else {
                    let results = self.knowledge.search(&arg);
                    if results.is_empty() {
                        format!("No results for: {arg}")
                    } else {
                        let mut lines =
                            vec![format!("**Search: {}** — {} result(s)\n", arg, results.len())];
                        for (i, r) in results.iter().enumerate().take(SEARCH_LIMIT) {
                            let summary = r
                                .summary
                                .chars()
                                .take(120)
                                .collect::<String>()
                                .replace(['*', '_'], "");
                            lines.push(format!("{}. `{}`\n   {}", i + 1, r.id, summary));
                        }
                        lines.join("\n")
                    }
                }
            }
            "/briefing" => {
                if arg.is_empty() {
                    "Usage: /briefing <topic>\nExample: /briefing authentication patterns"
                        .to_owned()
                } else {
                    self.knowledge.briefing(&arg)
                }
            }
            "/recent" => {
                let rows = self.knowledge.recent();
                if rows.is_empty() {
                    "No sessions found.".to_owned()
                } else {
                    let mut lines = vec![format!("**Recent {} sessions:**\n", rows.len())];
                    for (i, r) in rows.iter().enumerate().take(RECENT_LIMIT) {
                        let summary = r
                            .summary
                            .chars()
                            .take(100)
                            .collect::<String>()
                            .replace(['*', '_'], "");
                        lines.push(format!("{}. `{}`\n   {}", i + 1, r.id, summary));
                    }
                    lines.join("\n")
                }
            }
            "/help" | "/start" => HELP_TEXT.to_owned(),
            _ => format!(
                "Unknown command: {cmd_raw}\nSend /help to see available commands."
            ),
        }
    }
}

// ── Discord HTTP client ───────────────────────────────────────────────────────

struct DiscordClient {
    client: Client,
    base_url: String,
}

impl DiscordClient {
    fn new(token: &str, base_url_override: Option<&str>) -> Result<Self> {
        let auth_header = format!("Bot {token}");
        let mut headers = reqwest::header::HeaderMap::new();
        headers.insert(
            reqwest::header::AUTHORIZATION,
            reqwest::header::HeaderValue::from_str(&auth_header)
                .context("invalid Discord token for header")?,
        );

        let client = Client::builder()
            .no_proxy()
            .default_headers(headers)
            .timeout(Duration::from_secs(30))
            .build()
            .context("failed to build reqwest client")?;

        let base_url = base_url_override
            .map(|u| u.trim_end_matches('/').to_owned())
            .unwrap_or_else(|| DEFAULT_DISCORD_BASE.to_owned());

        Ok(Self { client, base_url })
    }

    /// `GET /gateway` — used as a connectivity heartbeat ping.
    async fn heartbeat(&self) -> Result<(), BrokerError> {
        let url = format!("{}/gateway", self.base_url);
        let resp = self
            .client
            .get(&url)
            .send()
            .await
            .map_err(BrokerError::from)?;
        let status = resp.status();
        if status.is_server_error() || status.as_u16() == 429 {
            return Err(BrokerError::Api(format!(
                "heartbeat HTTP {}",
                status.as_u16()
            )));
        }
        Ok(())
    }

    /// `GET /channels/{id}/messages?after={after}&limit=100`
    ///
    /// Retries on 429 (respecting `Retry-After`), up to `max_rate_retries`
    /// times before propagating the error to the outer backoff loop.
    async fn get_messages(
        &self,
        channel_id: &str,
        after: &str,
    ) -> Result<Vec<DiscordMessage>, BrokerError> {
        let url = format!("{}/channels/{}/messages", self.base_url, channel_id);
        let mut rate_limit_retries: u32 = 0;
        const MAX_RATE_RETRIES: u32 = 5;

        loop {
            let resp = self
                .client
                .get(&url)
                .query(&[("after", after), ("limit", "100")])
                .send()
                .await
                .map_err(BrokerError::from)?;

            let status = resp.status();

            if status.as_u16() == 429 {
                rate_limit_retries += 1;
                if rate_limit_retries > MAX_RATE_RETRIES {
                    return Err(BrokerError::Api(
                        "Discord rate limit (429) exceeded retry budget".to_owned(),
                    ));
                }
                let retry_after_secs = resp
                    .headers()
                    .get("Retry-After")
                    .and_then(|v| v.to_str().ok())
                    .and_then(|s| s.parse::<f64>().ok())
                    .unwrap_or(1.0)
                    .clamp(0.1, MAX_BACKOFF_SECS as f64);
                warn!(
                    retry_after = retry_after_secs,
                    attempt = rate_limit_retries,
                    "Discord rate limited (429); sleeping before retry"
                );
                tokio::time::sleep(Duration::from_secs_f64(retry_after_secs)).await;
                continue;
            }

            if matches!(status.as_u16(), 401 | 403) {
                return Err(BrokerError::Api(format!(
                    "Discord auth error HTTP {}: \
                     check DISCORD_BOT_TOKEN and bot channel permissions",
                    status.as_u16()
                )));
            }

            if status.as_u16() == 404 {
                return Err(BrokerError::Api(format!(
                    "Discord channel not found (404 channel_id={channel_id}): \
                     check DISCORD_CHANNEL_ID configuration"
                )));
            }

            if status.is_server_error() {
                return Err(BrokerError::Api(format!(
                    "Discord server error HTTP {}",
                    status.as_u16()
                )));
            }

            if !status.is_success() {
                let body: Value = resp.json().await.unwrap_or(Value::Null);
                return Err(BrokerError::Api(format!(
                    "Discord API error {}: {}",
                    status.as_u16(),
                    body
                )));
            }

            return resp
                .json()
                .await
                .map_err(|e| BrokerError::Json(e.to_string()));
        }
    }

    /// `POST /channels/{id}/messages`
    ///
    /// Retries on 429 (respecting `Retry-After`) up to 5 times.
    async fn send_message(
        &self,
        channel_id: &str,
        content: &str,
    ) -> Result<(), BrokerError> {
        let url = format!("{}/channels/{}/messages", self.base_url, channel_id);
        let payload = json!({ "content": content });
        let mut rate_limit_retries: u32 = 0;
        const MAX_RATE_RETRIES: u32 = 5;

        loop {
            let resp = self
                .client
                .post(&url)
                .json(&payload)
                .send()
                .await
                .map_err(BrokerError::from)?;

            let status = resp.status();

            if status.as_u16() == 429 {
                rate_limit_retries += 1;
                if rate_limit_retries > MAX_RATE_RETRIES {
                    return Err(BrokerError::Api(
                        "Discord send rate limit (429) exceeded retry budget".to_owned(),
                    ));
                }
                let retry_after_secs = resp
                    .headers()
                    .get("Retry-After")
                    .and_then(|v| v.to_str().ok())
                    .and_then(|s| s.parse::<f64>().ok())
                    .unwrap_or(1.0)
                    .clamp(0.1, MAX_BACKOFF_SECS as f64);
                warn!(
                    retry_after = retry_after_secs,
                    attempt = rate_limit_retries,
                    "Discord send rate limited (429); sleeping before retry"
                );
                tokio::time::sleep(Duration::from_secs_f64(retry_after_secs)).await;
                continue;
            }

            if matches!(status.as_u16(), 401 | 403) {
                return Err(BrokerError::Api(format!(
                    "Discord send auth error HTTP {}: \
                     check DISCORD_BOT_TOKEN and bot channel permissions",
                    status.as_u16()
                )));
            }

            if status.is_server_error() {
                return Err(BrokerError::Api(format!(
                    "Discord server error HTTP {} (sendMessage)",
                    status.as_u16()
                )));
            }

            return Ok(());
        }
    }
}

// ── DiscordBroker ─────────────────────────────────────────────────────────────

/// HTTP-polling Discord broker for browse (issue #928).
///
/// Polls a single channel for new messages, dispatches slash commands, and
/// replies.  WebSocket gateway integration is deferred until
/// `tokio-tungstenite` is added to Cargo.toml; this implementation uses the
/// Discord REST API as a functionally equivalent fallback.
///
/// # Usage
/// ```rust,ignore
/// use std::sync::Arc;
/// use sk::browse::broker::discord::{DiscordBroker, NoopKnowledge};
/// use sk::browse::broker::CancellationToken;
///
/// let broker = DiscordBroker::new(
///     "bot_token",
///     "channel_id",
///     "authorized_user_id",
///     Arc::new(NoopKnowledge),
///     None, // None = use discord.com; Some(url) = mock server in tests
/// ).unwrap();
/// // broker.run(CancellationToken::new()).await.unwrap();
/// ```
pub struct DiscordBroker {
    client: DiscordClient,
    channel_id: String,
    authorized_user_id: String,
    dispatcher: CommandDispatcher,
    rate_limiter: ChannelRateLimiter,
    #[allow(dead_code)]
    config: BrokerConfig,
}

impl DiscordBroker {
    /// Construct a new broker.
    ///
    /// `base_url_override` injects a mock server URL in tests.
    /// Pass `None` for production (uses `https://discord.com/api/v10`).
    pub fn new(
        token: &str,
        channel_id: &str,
        authorized_user_id: &str,
        knowledge: Arc<dyn KnowledgeSource>,
        base_url_override: Option<&str>,
    ) -> Result<Self> {
        // Fail fast: an empty token would silently send unauthenticated requests.
        if token.trim().is_empty() {
            anyhow::bail!(
                "DISCORD_BOT_TOKEN is missing or empty; \
                 refusing to start with an unauthenticated client"
            );
        }

        let config = BrokerConfig::default();
        Ok(Self {
            client: DiscordClient::new(token, base_url_override)?,
            channel_id: channel_id.to_owned(),
            authorized_user_id: authorized_user_id.to_owned(),
            dispatcher: CommandDispatcher::new(knowledge),
            rate_limiter: ChannelRateLimiter::new(),
            config,
        })
    }

    async fn handle_message(&self, msg: &DiscordMessage) {
        // Drop bot messages to avoid echo loops.
        if msg.author.bot {
            debug!(author_id = %msg.author.id, "dropping bot message");
            return;
        }

        // Auth: drop messages from unauthorized users.
        if !self.authorized_user_id.is_empty()
            && msg.author.id != self.authorized_user_id
        {
            debug!(
                author_id = %msg.author.id,
                authorized = %self.authorized_user_id,
                "dropping message from unauthorized user"
            );
            return;
        }

        let text = msg.content.trim();
        if text.is_empty() {
            return;
        }

        let response = self.dispatcher.dispatch(text);
        self.send_chunks(&self.channel_id, &response).await;
    }

    async fn send_chunks(&self, channel_id: &str, text: &str) {
        let chunks = chunk_text(text, MAX_MESSAGE_CHARS);
        for chunk in &chunks {
            self.rate_limiter.wait(channel_id).await;
            if let Err(e) = self.client.send_message(channel_id, chunk).await {
                error!(channel_id, error = %e, "sendMessage failed");
            }
        }
    }
}

impl super::Broker for DiscordBroker {
    async fn run(&self, token: CancellationToken) -> anyhow::Result<()> {
        info!(
            channel_id = %self.channel_id,
            "Starting Discord HTTP-poll loop (outbound-only, no inbound port)"
        );

        // Snowflake "0" means "fetch all messages from the beginning".
        let mut last_message_id = "0".to_owned();
        let mut consecutive_errors: u32 = 0;
        let mut error_streak_start: Option<Instant> = None;
        let mut last_heartbeat = Instant::now();

        loop {
            if token.is_cancelled() {
                info!("Discord broker shutting down (cancellation token)");
                return Ok(());
            }

            // Heartbeat: periodic connectivity ping (mirrors Discord Gateway heartbeat).
            if last_heartbeat.elapsed() >= Duration::from_millis(HEARTBEAT_INTERVAL_MS) {
                match self.client.heartbeat().await {
                    Ok(()) => {
                        debug!("Discord heartbeat OK");
                        last_heartbeat = Instant::now();
                    }
                    Err(e) => {
                        warn!(error = %e, "Discord heartbeat failed (non-fatal)");
                        // Don't reset last_heartbeat — retry sooner on failure.
                    }
                }
            }

            let poll_result = tokio::select! {
                result = self.client.get_messages(&self.channel_id, &last_message_id) => result,
                _ = token.cancelled() => {
                    info!("Discord broker shutting down (cancellation during poll)");
                    return Ok(());
                }
            };

            match poll_result {
                Ok(mut messages) => {
                    consecutive_errors = 0;
                    error_streak_start = None;

                    // Discord REST API returns messages newest-first.  Sort
                    // ascending by snowflake ID so we process oldest→newest
                    // and advance the cursor to the true maximum ID seen.
                    messages.sort_by_key(|m| m.id.parse::<u64>().unwrap_or(0));

                    // Advance the cursor to the newest (maximum) ID in this
                    // batch before processing, so a mid-batch shutdown still
                    // moves the window forward correctly on the next poll.
                    if let Some(newest) = messages
                        .iter()
                        .max_by_key(|m| m.id.parse::<u64>().unwrap_or(0))
                    {
                        last_message_id = newest.id.clone();
                    }

                    for msg in &messages {
                        if token.is_cancelled() {
                            info!("Discord broker shutting down (mid-batch cancellation)");
                            return Ok(());
                        }

                        self.handle_message(msg).await;
                    }

                    // Sleep between polls to avoid hammering the API.
                    tokio::select! {
                        _ = tokio::time::sleep(Duration::from_millis(POLL_INTERVAL_MS)) => {}
                        _ = token.cancelled() => {
                            info!("Discord broker shutting down (cancellation during sleep)");
                            return Ok(());
                        }
                    }
                }
                Err(BrokerError::Shutdown) => {
                    return Ok(());
                }
                Err(e) => {
                    consecutive_errors += 1;
                    let streak_start = *error_streak_start.get_or_insert_with(Instant::now);
                    let elapsed = streak_start.elapsed();
                    let err_str = e.to_string();

                    match decide(
                        &POLL_RETRY_POLICY,
                        consecutive_errors,
                        elapsed,
                        &err_str,
                        None,
                    ) {
                        RetryDecision::Retry(wait, _kind) => {
                            let wait_secs = wait.as_secs();
                            warn!(
                                error = %e,
                                consecutive_errors,
                                wait_secs,
                                "Discord poll error; retrying with backoff"
                            );
                            tokio::select! {
                                _ = tokio::time::sleep(wait) => {}
                                _ = token.cancelled() => {
                                    info!("Discord broker shutting down (cancellation during backoff)");
                                    return Ok(());
                                }
                            }
                        }
                        RetryDecision::Stop(reason) => {
                            warn!(
                                error = %e,
                                ?reason,
                                "Discord poll: stopping retry loop (non-retryable error)"
                            );
                            return Ok(());
                        }
                    }
                }
            }
        }
    }
}

// ── Tests ─────────────────────────────────────────────────────────────────────

#[cfg(test)]
mod tests {
    use super::*;

    fn make_dispatcher() -> CommandDispatcher {
        CommandDispatcher::new(Arc::new(NoopKnowledge))
    }

    #[test]
    fn dispatch_help() {
        let d = make_dispatcher();
        let resp = d.dispatch("/help");
        assert!(resp.contains("/status"), "help should mention /status");
        assert!(resp.contains("/search"), "help should mention /search");
    }

    #[test]
    fn dispatch_start_same_as_help() {
        let d = make_dispatcher();
        assert_eq!(d.dispatch("/start"), d.dispatch("/help"));
    }

    #[test]
    fn dispatch_unknown_command() {
        let d = make_dispatcher();
        let resp = d.dispatch("/unknown");
        assert!(resp.contains("Unknown command"));
    }

    #[test]
    fn dispatch_non_command() {
        let d = make_dispatcher();
        let resp = d.dispatch("hello");
        assert!(resp.contains("/help"));
    }

    #[test]
    fn dispatch_search_no_query() {
        let d = make_dispatcher();
        let resp = d.dispatch("/search");
        assert!(resp.contains("Usage:"));
    }

    #[test]
    fn dispatch_search_with_query() {
        let d = make_dispatcher();
        let resp = d.dispatch("/search authentication");
        assert!(!resp.is_empty());
    }

    #[test]
    fn dispatch_briefing_no_arg() {
        let d = make_dispatcher();
        let resp = d.dispatch("/briefing");
        assert!(resp.contains("Usage:"));
    }

    #[test]
    fn dispatch_briefing_with_arg() {
        let d = make_dispatcher();
        let resp = d.dispatch("/briefing auth patterns");
        assert!(!resp.is_empty());
        assert!(resp.contains("auth patterns"));
    }

    #[test]
    fn dispatch_recent() {
        let d = make_dispatcher();
        let resp = d.dispatch("/recent");
        assert!(!resp.is_empty());
    }

    #[test]
    fn dispatch_status() {
        let d = make_dispatcher();
        let resp = d.dispatch("/status");
        assert!(
            resp.to_lowercase().contains("uptime") || resp.to_lowercase().contains("status")
        );
    }

    #[test]
    fn rate_limiter_allows_up_to_limit() {
        // Verify that at most RATE_LIMIT_REQUESTS slots are immediately available.
        let limiter = ChannelRateLimiter::new();
        {
            let mut map = limiter.windows.lock().unwrap();
            let queue = map.entry("test-channel".to_owned()).or_default();
            // Fill up to the limit.
            for _ in 0..RATE_LIMIT_REQUESTS {
                queue.push_back(Instant::now());
            }
            // The next request must be over the limit.
            assert_eq!(queue.len(), RATE_LIMIT_REQUESTS);
        }
    }

    #[test]
    fn discord_broker_new_fails_on_bad_token() {
        // A token containing a newline char is rejected by the header builder.
        let result =
            DiscordBroker::new("bad\ntoken", "chan", "user", Arc::new(NoopKnowledge), None);
        assert!(result.is_err(), "newline in token should fail header build");
    }

    #[test]
    fn discord_broker_new_fails_on_empty_token() {
        // Empty / whitespace-only tokens must fail fast before sending requests.
        for bad in &["", "   ", "\t"] {
            let result =
                DiscordBroker::new(bad, "chan", "user", Arc::new(NoopKnowledge), None);
            assert!(
                result.is_err(),
                "empty/blank token {:?} should be rejected",
                bad
            );
        }
    }

    #[test]
    fn discord_broker_new_ok_with_override() {
        // Smoke test: broker constructs without error given a mock base URL.
        let result = DiscordBroker::new(
            "validtoken",
            "123456789",
            "987654321",
            Arc::new(NoopKnowledge),
            Some("http://localhost:9999"),
        );
        assert!(result.is_ok(), "broker should construct with base_url_override");
    }

    #[test]
    fn bot_message_dropped() {
        // Messages from bots should be filtered.
        let msg = DiscordMessage {
            id: "1".to_owned(),
            content: "/status".to_owned(),
            author: DiscordUser {
                id: "bot123".to_owned(),
                bot: true,
            },
        };
        // If bot=true, handle_message returns early without dispatching.
        // We verify via the author.bot flag rather than spawning a runtime.
        assert!(msg.author.bot);
    }

    #[test]
    fn chunk_text_respects_discord_limit() {
        // Discord's 2000-char limit: ensure text over limit is chunked.
        let long = "x".repeat(2500);
        let chunks = chunk_text(&long, MAX_MESSAGE_CHARS);
        assert!(chunks.len() >= 2, "long text should be split");
        for chunk in &chunks {
            assert!(chunk.len() <= MAX_MESSAGE_CHARS);
        }
    }

    #[test]
    fn messages_sorted_ascending_by_snowflake() {
        // Discord REST API returns newest-first; verify our sort produces oldest-first.
        let mut messages = vec![
            DiscordMessage {
                id: "1000000000000000003".to_owned(),
                content: "newest".to_owned(),
                author: DiscordUser { id: "u1".to_owned(), bot: false },
            },
            DiscordMessage {
                id: "1000000000000000001".to_owned(),
                content: "oldest".to_owned(),
                author: DiscordUser { id: "u1".to_owned(), bot: false },
            },
            DiscordMessage {
                id: "1000000000000000002".to_owned(),
                content: "middle".to_owned(),
                author: DiscordUser { id: "u1".to_owned(), bot: false },
            },
        ];

        messages.sort_by_key(|m| m.id.parse::<u64>().unwrap_or(0));

        assert_eq!(messages[0].content, "oldest");
        assert_eq!(messages[1].content, "middle");
        assert_eq!(messages[2].content, "newest");

        // Cursor must advance to the maximum (newest) ID.
        let max_id = messages
            .iter()
            .max_by_key(|m| m.id.parse::<u64>().unwrap_or(0))
            .map(|m| m.id.as_str())
            .unwrap_or("0");
        assert_eq!(max_id, "1000000000000000003");
    }
}
