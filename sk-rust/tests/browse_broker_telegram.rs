//! Integration tests for the Telegram broker — issue #454 PR-A.
//!
//! Uses `wiremock` to mock the Telegram Bot API so no real network calls are made.
//! All tests run with `--features browse-broker`.

#![cfg(feature = "browse-broker")]

use std::sync::Arc;

use sk::browse::broker::telegram::{NoopKnowledge, TelegramBroker};
use sk::browse::broker::{chunk_text, Broker, CancellationToken};
use wiremock::{
    matchers::{method, path},
    Mock, MockServer, ResponseTemplate,
};

// ── Helpers ───────────────────────────────────────────────────────────────────

/// Build a minimal `getUpdates` JSON response with no updates.
fn empty_updates_response() -> serde_json::Value {
    serde_json::json!({ "ok": true, "result": [] })
}

/// Build a `getUpdates` response containing a single authorized command.
fn command_update(update_id: i64, user_id: i64, chat_id: i64, text: &str) -> serde_json::Value {
    serde_json::json!({
        "ok": true,
        "result": [{
            "update_id": update_id,
            "message": {
                "message_id": 1,
                "chat": { "id": chat_id },
                "from": { "id": user_id, "is_bot": false, "first_name": "Test" },
                "date": 1700000000,
                "text": text
            }
        }]
    })
}

/// Build a `getUpdates` response with a message that has no `from` field.
fn no_sender_update(update_id: i64, chat_id: i64, text: &str) -> serde_json::Value {
    serde_json::json!({
        "ok": true,
        "result": [{
            "update_id": update_id,
            "message": {
                "message_id": 1,
                "chat": { "id": chat_id },
                "date": 1700000000,
                "text": text
            }
        }]
    })
}

/// Build a successful `sendMessage` response.
fn send_message_ok(chat_id: i64) -> serde_json::Value {
    serde_json::json!({
        "ok": true,
        "result": {
            "message_id": 42,
            "chat": { "id": chat_id },
            "date": 1700000000,
            "text": "reply"
        }
    })
}

// ── chunk_text unit tests (no network) ───────────────────────────────────────

#[test]
fn chunk_text_short_message() {
    let chunks = chunk_text("hello world", 4096);
    assert_eq!(chunks, vec!["hello world"]);
}

#[test]
fn chunk_text_splits_at_paragraph() {
    let a = "a".repeat(80);
    let b = "b".repeat(80);
    let text = format!("{}\n\n{}", a, b);
    let chunks = chunk_text(&text, 90);
    assert!(chunks.len() >= 2, "should split into multiple chunks");
    assert_eq!(chunks[0], a, "first chunk should be the first paragraph");
}

#[test]
fn chunk_text_splits_at_newline() {
    let a = "a".repeat(50);
    let b = "b".repeat(50);
    let text = format!("{}\n{}", a, b);
    let chunks = chunk_text(&text, 60);
    assert!(
        chunks.len() >= 2,
        "should split at newline when no blank line"
    );
}

#[test]
fn chunk_text_hard_splits() {
    let text = "x".repeat(300);
    let chunks = chunk_text(&text, 100);
    assert_eq!(chunks.len(), 3, "300 chars / 100 = 3 chunks");
    for chunk in &chunks {
        assert_eq!(chunk.len(), 100);
    }
}

#[test]
fn chunk_text_strips_whitespace_at_boundary() {
    // The chunk before the split should have trailing whitespace removed.
    let text = "a".repeat(50) + "   \n   " + &"b".repeat(50);
    let chunks = chunk_text(&text, 60);
    // Just ensure no chunk starts or ends with just whitespace
    for chunk in &chunks {
        assert_eq!(chunk.as_str(), chunk.trim_end(), "no trailing whitespace");
    }
}

// ── Cancellation test (no network needed) ────────────────────────────────────

#[tokio::test]
async fn broker_respects_cancellation_token() {
    // We set up a wiremock server that never responds to simulate a hung poll,
    // then cancel immediately. The broker must return Ok(()) quickly.

    let server = MockServer::start().await;

    // Respond with empty updates; we just need the broker to make at least one
    // call, then we cancel it.
    Mock::given(method("POST"))
        .and(path("/fake_token/getUpdates"))
        .respond_with(ResponseTemplate::new(200).set_body_json(empty_updates_response()))
        .mount(&server)
        .await;

    let base_url = format!("{}/", server.uri());
    let broker = TelegramBroker::new(
        "fake_token",
        12345,
        Arc::new(NoopKnowledge),
        Some(&base_url),
    )
    .expect("broker construction should not fail");

    let token = CancellationToken::new();
    let child = token.child_token();

    // Cancel after a short delay so the broker processes at least one poll cycle.
    tokio::spawn(async move {
        tokio::time::sleep(std::time::Duration::from_millis(100)).await;
        token.cancel();
    });

    let result = broker.run(child).await;

    assert!(
        result.is_ok(),
        "broker.run should return Ok on cancellation"
    );
}

// ── Auth: unauthorized user is dropped ───────────────────────────────────────

#[tokio::test]
async fn broker_drops_unauthorized_user() {
    let server = MockServer::start().await;
    let authorized_id: i64 = 99999;
    let unauthorized_id: i64 = 11111;
    let chat_id: i64 = 42;

    // First response: update from unauthorized user.
    Mock::given(method("POST"))
        .and(path("/fake_token/getUpdates"))
        .respond_with(ResponseTemplate::new(200).set_body_json(command_update(
            1,
            unauthorized_id,
            chat_id,
            "/help",
        )))
        .up_to_n_times(1)
        .mount(&server)
        .await;

    Mock::given(method("POST"))
        .and(path("/fake_token/getUpdates"))
        .respond_with(ResponseTemplate::new(200).set_body_json(empty_updates_response()))
        .mount(&server)
        .await;

    // sendMessage should NOT be called for the unauthorized user.
    Mock::given(method("POST"))
        .and(path("/fake_token/sendMessage"))
        .respond_with(ResponseTemplate::new(200).set_body_json(send_message_ok(chat_id)))
        .expect(0)
        .mount(&server)
        .await;

    let base_url = format!("{}/", server.uri());
    let broker = TelegramBroker::new(
        "fake_token",
        authorized_id,
        Arc::new(NoopKnowledge),
        Some(&base_url),
    )
    .expect("broker construction");

    let token = CancellationToken::new();
    // Cancel after a short delay to let the broker process the update.
    let child = token.child_token();
    tokio::spawn(async move {
        tokio::time::sleep(std::time::Duration::from_millis(200)).await;
        token.cancel();
    });

    broker.run(child).await.expect("broker.run");

    // wiremock verifies the expect(0) on sendMessage automatically on drop.
}

// ── Auth: update with no `from` field is dropped (regression for unwrap_or(0)) ──

#[tokio::test]
async fn broker_drops_update_with_no_sender() {
    let server = MockServer::start().await;
    let chat_id: i64 = 42;

    // Deliver one update that has a `message` but no `from` field.
    Mock::given(method("POST"))
        .and(path("/fake_token/getUpdates"))
        .respond_with(
            ResponseTemplate::new(200).set_body_json(no_sender_update(1, chat_id, "/help")),
        )
        .up_to_n_times(1)
        .mount(&server)
        .await;

    Mock::given(method("POST"))
        .and(path("/fake_token/getUpdates"))
        .respond_with(ResponseTemplate::new(200).set_body_json(empty_updates_response()))
        .mount(&server)
        .await;

    // sendMessage must NOT be called: no `from` means anonymous sender, which
    // must be dropped regardless of authorized_user_id (including 0 = disabled).
    Mock::given(method("POST"))
        .and(path("/fake_token/sendMessage"))
        .respond_with(ResponseTemplate::new(200).set_body_json(send_message_ok(chat_id)))
        .expect(0)
        .mount(&server)
        .await;

    let base_url = format!("{}/", server.uri());
    // authorized_user_id == 0 means "block all"; this also exercises the old
    // unwrap_or(0) bug where a missing `from` would equal 0 and pass auth.
    let broker = TelegramBroker::new(
        "fake_token",
        0, // disabled / block-all
        Arc::new(NoopKnowledge),
        Some(&base_url),
    )
    .expect("broker construction");

    let token = CancellationToken::new();
    let child = token.child_token();
    tokio::spawn(async move {
        tokio::time::sleep(std::time::Duration::from_millis(200)).await;
        token.cancel();
    });

    broker.run(child).await.expect("broker.run");
    // wiremock asserts expect(0) on drop.
}

// ── Help command is dispatched correctly ──────────────────────────────────────

#[tokio::test]
async fn broker_dispatches_help_command() {
    let server = MockServer::start().await;
    let authorized_id: i64 = 12345;
    let chat_id: i64 = 42;

    // First request: /help command from authorized user.
    Mock::given(method("POST"))
        .and(path("/fake_token/getUpdates"))
        .respond_with(ResponseTemplate::new(200).set_body_json(command_update(
            1,
            authorized_id,
            chat_id,
            "/help",
        )))
        .up_to_n_times(1)
        .mount(&server)
        .await;

    // Subsequent requests: empty.
    Mock::given(method("POST"))
        .and(path("/fake_token/getUpdates"))
        .respond_with(ResponseTemplate::new(200).set_body_json(empty_updates_response()))
        .mount(&server)
        .await;

    // sendMessage should be called at least once with the help text.
    Mock::given(method("POST"))
        .and(path("/fake_token/sendMessage"))
        .respond_with(ResponseTemplate::new(200).set_body_json(send_message_ok(chat_id)))
        .expect(1..)
        .mount(&server)
        .await;

    let base_url = format!("{}/", server.uri());
    let broker = TelegramBroker::new(
        "fake_token",
        authorized_id,
        Arc::new(NoopKnowledge),
        Some(&base_url),
    )
    .expect("broker construction");

    let token = CancellationToken::new();
    let child = token.child_token();
    tokio::spawn(async move {
        tokio::time::sleep(std::time::Duration::from_millis(400)).await;
        token.cancel();
    });

    broker.run(child).await.expect("broker.run");
}

// ── @BotUsername suffix is stripped ──────────────────────────────────────────

#[tokio::test]
async fn broker_strips_bot_username_suffix() {
    let server = MockServer::start().await;
    let authorized_id: i64 = 12345;
    let chat_id: i64 = 42;

    // /help@MyBot should be treated the same as /help.
    Mock::given(method("POST"))
        .and(path("/fake_token/getUpdates"))
        .respond_with(ResponseTemplate::new(200).set_body_json(command_update(
            1,
            authorized_id,
            chat_id,
            "/help@CoolBot",
        )))
        .up_to_n_times(1)
        .mount(&server)
        .await;

    Mock::given(method("POST"))
        .and(path("/fake_token/getUpdates"))
        .respond_with(ResponseTemplate::new(200).set_body_json(empty_updates_response()))
        .mount(&server)
        .await;

    Mock::given(method("POST"))
        .and(path("/fake_token/sendMessage"))
        .respond_with(ResponseTemplate::new(200).set_body_json(send_message_ok(chat_id)))
        .expect(1..)
        .mount(&server)
        .await;

    let base_url = format!("{}/", server.uri());
    let broker = TelegramBroker::new(
        "fake_token",
        authorized_id,
        Arc::new(NoopKnowledge),
        Some(&base_url),
    )
    .expect("broker construction");

    let token = CancellationToken::new();
    let child = token.child_token();
    tokio::spawn(async move {
        tokio::time::sleep(std::time::Duration::from_millis(400)).await;
        token.cancel();
    });

    broker.run(child).await.expect("broker.run");
}

// ── Token-leak regression test ────────────────────────────────────────────────

/// A forced HTTP transport error must not expose the bot token in its Display.
///
/// Telegram Bot API URLs contain the token as a path component
/// (`/bot<TOKEN>/method`).  `reqwest::Error::Display` includes the request URL
/// by default; `BrokerError::from` must strip it via `without_url()`.
#[tokio::test]
async fn http_error_does_not_leak_bot_token() {
    let server = MockServer::start().await;
    let token = "SUPER_SECRET_FAKE_TOKEN_ABCXYZ";

    // Respond only after a 30-second delay — far longer than our client
    // timeout — so reqwest times out and returns an error that normally
    // embeds the request URL (and thus the token).
    Mock::given(method("POST"))
        .respond_with(ResponseTemplate::new(200).set_delay(std::time::Duration::from_secs(30)))
        .mount(&server)
        .await;

    let url = format!("{}/bot{}/getUpdates", server.uri(), token);
    let client = reqwest::Client::builder()
        .no_proxy()
        .timeout(std::time::Duration::from_millis(500))
        .build()
        .unwrap();

    let raw_err = client
        .post(&url)
        .json(&serde_json::json!({}))
        .send()
        .await
        .expect_err("request must time out before the delayed response");

    // After conversion through BrokerError::from, the token must be absent.
    // (reqwest::Error::without_url() strips the URL path before we format it.)
    let broker_err = sk::browse::broker::BrokerError::from(raw_err);
    let safe_display = format!("{broker_err}");
    assert!(
        !safe_display.contains(token),
        "BrokerError::from must strip the token from the error; got: {safe_display}"
    );
    assert!(
        safe_display.starts_with("HTTP error:"),
        "should still identify as HTTP error: {safe_display}"
    );
}

// ── Non-ASCII chunking regression tests ─────────────────────────────────────

/// chunk_text must not panic when a multi-byte emoji sits on the 4096-byte boundary.
#[test]
fn chunk_text_emoji_crossing_byte_boundary_no_panic() {
    // 😀 is 4 bytes. 1025 repetitions = 4100 bytes (just over Telegram's 4096 limit).
    // Without floor_char_boundary the old code would try to slice at byte 4096,
    // which falls in the middle of the 1024th emoji and would panic.
    let text: String = "😀".repeat(1025);
    assert_eq!(text.len(), 4100);
    let chunks = chunk_text(&text, 4096);
    assert!(!chunks.is_empty(), "must produce at least one chunk");
    // Chunks must reassemble cleanly.
    let reassembled: String = chunks.join("");
    assert_eq!(reassembled, text, "reassembled text must equal original");
    // Every chunk must be valid UTF-8 (slice alignment correctness).
    for chunk in &chunks {
        assert!(
            std::str::from_utf8(chunk.as_bytes()).is_ok(),
            "chunk is not valid UTF-8: {chunk:?}"
        );
    }
}

/// Same boundary test with 3-byte CJK characters.
#[test]
fn chunk_text_cjk_crossing_byte_boundary_no_panic() {
    // '中' is 3 bytes. 1366 repetitions = 4098 bytes (just over 4096).
    let text: String = "中".repeat(1366);
    assert_eq!(text.len(), 4098);
    let chunks = chunk_text(&text, 4096);
    assert!(!chunks.is_empty());
    let reassembled: String = chunks.join("");
    assert_eq!(reassembled, text);
}

// ── Retry on 500 ─────────────────────────────────────────────────────────────

#[tokio::test]
async fn broker_retries_on_server_error() {
    let server = MockServer::start().await;

    // First call returns 500; second call returns empty updates.
    Mock::given(method("POST"))
        .and(path("/fake_token/getUpdates"))
        .respond_with(ResponseTemplate::new(500))
        .up_to_n_times(1)
        .mount(&server)
        .await;

    Mock::given(method("POST"))
        .and(path("/fake_token/getUpdates"))
        .respond_with(ResponseTemplate::new(200).set_body_json(empty_updates_response()))
        .mount(&server)
        .await;

    let base_url = format!("{}/", server.uri());
    let broker = TelegramBroker::new(
        "fake_token",
        12345,
        Arc::new(NoopKnowledge),
        Some(&base_url),
    )
    .expect("broker construction");

    let token = CancellationToken::new();
    let child = token.child_token();

    // Cancel quickly — we just want to verify the broker doesn't crash on a 500.
    // The backoff will sleep 2s (2^1), so we cancel before that.
    tokio::spawn(async move {
        tokio::time::sleep(std::time::Duration::from_millis(50)).await;
        token.cancel();
    });

    let result = broker.run(child).await;
    assert!(result.is_ok(), "broker must not crash on 500: {:?}", result);
}
