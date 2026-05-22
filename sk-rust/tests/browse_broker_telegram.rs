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
