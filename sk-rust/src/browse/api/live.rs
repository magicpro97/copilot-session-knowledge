//! `GET /api/live` — SSE live-stream of new knowledge entries (issue #448).
//!
//! Streams [`serde_json`] events as new rows are inserted into `knowledge_entries`.
//! The handler snapshots the current `MAX(id)` as the starting cursor, then
//! polls every 2 seconds via a background [`tokio::task`] for rows with
//! `id > cursor`.  Each row is sent as a JSON-framed SSE event with an `id:`
//! field so clients can resume with `Last-Event-ID` on reconnect.
//!
//! On reconnect, a client may supply a `Last-Event-ID` header with the last
//! event id it received.  When the value is a valid positive integer the
//! handler uses it as the starting cursor so no events are missed during the
//! reconnect window.  An invalid or absent header falls back to the latest
//! snapshot without panicking.
//!
//! The poll task exits when:
//! - the client disconnects (the [`tokio::sync::mpsc::Sender`] reports closed),
//! - the connection reaches [`MAX_CONNECTION_SECS`], or
//! - a database query returns an error (logged via [`tracing`]).

use std::sync::Arc;
use std::time::Duration;

use axum::extract::State;
use axum::http::HeaderMap;
use axum::response::IntoResponse;
use tokio_stream::wrappers::ReceiverStream;

use crate::browse::db::BrowseDb;
use crate::browse::sse::{json_event_with_id, sse_response, MAX_CONNECTION_SECS};

/// `GET /api/live` handler.
///
/// Extracts [`Arc<BrowseDb>`] from the router state via
/// [`axum::extract::FromRef`] and opens an SSE stream of new
/// `knowledge_entries` rows.
///
/// Honors the `Last-Event-ID` request header for reconnecting clients: a valid
/// positive integer value is used directly as the starting cursor so that
/// events emitted between the disconnect and the reconnect are not lost.
/// Invalid or missing values fall back to a fresh `MAX(id)` snapshot.
pub async fn handler(headers: HeaderMap, State(db): State<Arc<BrowseDb>>) -> impl IntoResponse {
    // Honor Last-Event-ID for reconnecting clients (SSE spec §9.2).
    // A valid positive integer resumes from that id; anything else falls back
    // to the latest snapshot so new connections only receive future events.
    let resume_id: Option<i64> = headers
        .get("last-event-id")
        .and_then(|v| v.to_str().ok())
        .and_then(|s| s.parse::<i64>().ok())
        .filter(|&id| id > 0);

    let cursor: i64 = if let Some(id) = resume_id {
        id
    } else {
        // Snapshot current max id on a blocking thread to avoid stalling the
        // Tokio executor with a synchronous SQLite/r2d2 call.
        let db2 = Arc::clone(&db);
        match tokio::task::spawn_blocking(move || db2.latest_entry_id()).await {
            Ok(Ok(Some(id))) => id,
            Ok(Ok(None)) => 0,
            Ok(Err(e)) => {
                tracing::warn!("live handler: failed to snapshot latest_entry_id: {e}");
                0
            }
            Err(e) => {
                tracing::warn!("live handler: spawn_blocking join error: {e}");
                0
            }
        }
    };

    let (tx, rx) =
        tokio::sync::mpsc::channel::<Result<axum::response::sse::Event, axum::Error>>(64);

    tokio::spawn(poll_task(db, cursor, tx));

    sse_response(ReceiverStream::new(rx))
}

/// Background polling task: queries new entries every 2 seconds and forwards
/// them as SSE events.  Exits on client disconnect, deadline, or DB error.
async fn poll_task(
    db: Arc<BrowseDb>,
    initial_cursor: i64,
    tx: tokio::sync::mpsc::Sender<Result<axum::response::sse::Event, axum::Error>>,
) {
    let deadline = tokio::time::Instant::now() + Duration::from_secs(MAX_CONNECTION_SECS);
    let mut poll = tokio::time::interval(Duration::from_secs(2));
    let mut cursor = initial_cursor;

    loop {
        // Wait for the next poll tick, a disconnect, or deadline.
        tokio::select! {
            _ = poll.tick() => {}
            _ = tx.closed() => { return; }
            _ = tokio::time::sleep_until(deadline) => { return; }
        }

        // Run the DB query on a blocking thread to avoid stalling the async executor.
        let db2 = Arc::clone(&db);
        let cur = cursor;
        let result = tokio::task::spawn_blocking(move || db2.entries_after_live(cur, 50)).await;

        let entries = match result {
            Ok(Ok(rows)) => rows,
            Ok(Err(e)) => {
                tracing::warn!("live handler: db query error: {e}");
                return;
            }
            Err(e) => {
                tracing::warn!("live handler: spawn_blocking join error: {e}");
                return;
            }
        };

        for entry in &entries {
            let id = entry["id"].as_i64().unwrap_or(0);
            let event = json_event_with_id(entry, &id.to_string());
            if tx.send(event).await.is_err() {
                // Receiver dropped — client disconnected.
                return;
            }
            cursor = id;
        }
    }
}
