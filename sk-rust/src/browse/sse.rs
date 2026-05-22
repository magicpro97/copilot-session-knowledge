//! SSE response helpers for the browse HTTP server (issue #448).
//!
//! # Client-disconnect cleanup
//!
//! When an SSE client disconnects, axum drops the streaming body future.
//! This causes the [`tokio_stream::wrappers::ReceiverStream`] wrapping the
//! [`tokio::sync::mpsc::Receiver`] to be dropped, which closes the channel
//! and causes [`tokio::sync::mpsc::Sender::closed`] to resolve in the polling
//! task. Polling tasks must select on `tx.closed()` (or check
//! `tx.send(…).await.is_err()`) so they exit promptly without leaking.
//!
//! # Heartbeat comment semantics
//!
//! [`sse_response`] configures [`axum::response::sse::KeepAlive`] with
//! `.text("ping")`.  This emits SSE *comment* lines (`: ping\n\n`) on the
//! wire every [`HEARTBEAT_SECS`] seconds.  SSE comments (lines starting with
//! `:`) are valid per the specification and pass through HTTP/2 proxies and
//! load balancers, but the browser `EventSource` API silently ignores them.
//! They keep the TCP connection alive through idle-connection timeouts without
//! triggering `onmessage` on the client.

use axum::response::sse::{Event, KeepAlive, Sse};
use std::time::Duration;
use tokio::sync::mpsc::Receiver;
use tokio_stream::wrappers::ReceiverStream;

/// Interval between SSE keep-alive comment frames (seconds).
pub const HEARTBEAT_SECS: u64 = 15;

/// Maximum SSE connection lifetime in seconds.
///
/// After this duration the server closes the stream.  The client is expected
/// to reconnect using its stored `Last-Event-ID` value so the server can
/// resume from the correct cursor.
pub const MAX_CONNECTION_SECS: u64 = 600;

/// Wrap a [`ReceiverStream`] in an [`axum::response::sse::Sse`] response with
/// a periodic keep-alive comment (`: ping`) every [`HEARTBEAT_SECS`] seconds.
///
/// `E` must satisfy `Into<Box<dyn Error + Send + Sync>>` so axum can convert
/// stream errors into HTTP error responses.
pub fn sse_response<E>(
    stream: ReceiverStream<Result<Event, E>>,
) -> Sse<ReceiverStream<Result<Event, E>>>
where
    E: Into<axum::BoxError> + Send + 'static,
{
    Sse::new(stream).keep_alive(
        KeepAlive::new()
            .interval(Duration::from_secs(HEARTBEAT_SECS))
            .text("ping"),
    )
}

/// Serialize `data` as a JSON-encoded SSE `data:` field.
///
/// Returns `Err(axum::Error)` if `data` cannot be serialized to JSON.
pub fn json_event<T: serde::Serialize>(data: &T) -> Result<Event, axum::Error> {
    Event::default().json_data(data)
}

/// Serialize `data` as a JSON-encoded SSE event with an `id:` field.
///
/// The `id` is emitted before the `data` line.  Clients store the last
/// received `id` as the `Last-Event-ID` for reconnection, allowing the server
/// to resume polling from the correct cursor position.
///
/// Returns `Err(axum::Error)` if `data` cannot be serialized to JSON.
pub fn json_event_with_id<T: serde::Serialize>(data: &T, id: &str) -> Result<Event, axum::Error> {
    Ok(Event::default().json_data(data)?.id(id))
}

/// Convert a [`tokio::sync::mpsc::Receiver`] into a
/// [`tokio_stream::wrappers::ReceiverStream`] suitable for use with
/// [`sse_response`].
pub fn channel_stream<T>(rx: Receiver<T>) -> ReceiverStream<T> {
    ReceiverStream::new(rx)
}

// ── Tests ─────────────────────────────────────────────────────────────────────

#[cfg(test)]
mod tests {
    use super::*;
    use serde_json::json;

    #[test]
    fn json_event_ok_for_valid_data() {
        let v = json!({"hello": "world"});
        assert!(json_event(&v).is_ok());
    }

    #[test]
    fn json_event_with_id_ok_for_valid_data() {
        let v = json!({"n": 42});
        assert!(json_event_with_id(&v, "42").is_ok());
    }

    #[test]
    fn json_event_null_is_valid() {
        assert!(json_event(&json!(null)).is_ok());
    }

    const _: () = {
        assert!(HEARTBEAT_SECS > 0);
        assert!(MAX_CONNECTION_SECS > HEARTBEAT_SECS);
    };

    #[test]
    fn heartbeat_interval_compiles() {
        // Verify KeepAlive uses HEARTBEAT_SECS constant correctly.
        let _ka = KeepAlive::new()
            .interval(Duration::from_secs(HEARTBEAT_SECS))
            .text("ping");
    }

    #[tokio::test]
    async fn channel_stream_yields_sent_values() {
        use tokio_stream::StreamExt as _;
        let (tx, rx) = tokio::sync::mpsc::channel::<i32>(4);
        tx.send(10).await.unwrap();
        tx.send(20).await.unwrap();
        drop(tx);
        let mut s = channel_stream(rx);
        assert_eq!(s.next().await, Some(10));
        assert_eq!(s.next().await, Some(20));
        assert_eq!(s.next().await, None);
    }

    #[tokio::test]
    async fn channel_stream_ends_when_sender_dropped() {
        use tokio_stream::StreamExt as _;
        let (tx, rx) = tokio::sync::mpsc::channel::<u8>(1);
        drop(tx);
        let mut s = channel_stream(rx);
        assert_eq!(s.next().await, None);
    }
}
