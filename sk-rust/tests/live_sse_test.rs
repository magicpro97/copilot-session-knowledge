//! Integration tests for `GET /api/live` SSE endpoint (issue #448).
//!
//! Tests cover:
//! - Content-type and cache-control response headers.
//! - Auth: 401 when a server token is set and no credentials are provided.
//! - Seeded events: two entries added after connection appear in the stream
//!   with correct `id:` fields and JSON shape.
//!
//! The seeded-events test uses a real TCP listener so the streaming body can be
//! read incrementally.  All other tests use `tower::ServiceExt::oneshot`.

#![cfg(feature = "browse-server")]

use std::sync::Arc;
use std::time::Duration;

use axum::body::Body;
use axum::http::{Request, StatusCode};
use tower::ServiceExt;

use sk::browse::db::{BrowseDb, BrowseDbConfig};
use sk::browse::server::{app, AppState, ServerConfig};

// ── Helpers ───────────────────────────────────────────────────────────────────

fn mk_db_at(path: &std::path::Path) -> Arc<BrowseDb> {
    let conn = rusqlite::Connection::open(path).unwrap();
    conn.execute_batch(
        "PRAGMA journal_mode=WAL;\
         CREATE TABLE IF NOT EXISTS knowledge_entries (\
           id INTEGER PRIMARY KEY AUTOINCREMENT,\
           category TEXT NOT NULL DEFAULT '',\
           title TEXT NOT NULL DEFAULT '',\
           content TEXT NOT NULL DEFAULT '',\
           tags TEXT NOT NULL DEFAULT '',\
           wing TEXT, room TEXT,\
           confidence REAL NOT NULL DEFAULT 0.5,\
           deleted_at INTEGER\
         );\
         CREATE TABLE IF NOT EXISTS migration_log (version INTEGER NOT NULL);",
    )
    .unwrap();
    drop(conn);
    let cfg = BrowseDbConfig {
        path: path.to_path_buf(),
        checkpoint_interval: None,
        ..Default::default()
    };
    Arc::new(BrowseDb::new_without_checkpoint(cfg).unwrap())
}

fn unique_db_path(tag: &str) -> std::path::PathBuf {
    use std::sync::atomic::{AtomicU64, Ordering};
    static CTR: AtomicU64 = AtomicU64::new(0);
    let n = CTR.fetch_add(1, Ordering::SeqCst);
    std::env::temp_dir().join(format!(
        "sk_live_test_{tag}_{pid}_{n}.db",
        pid = std::process::id()
    ))
}

fn open_state_with_db(db: Arc<BrowseDb>) -> AppState {
    AppState::new(
        Arc::new(ServerConfig {
            cors_origins: vec!["https://allowed.example".to_string()],
            ..ServerConfig::default()
        }),
        db,
    )
}

/// Insert two knowledge entries directly into the DB at `path` (bypasses the
/// r2d2 pool so it can run from a test thread while the pool is open elsewhere).
fn seed_two_entries(path: &std::path::Path) {
    let conn = rusqlite::Connection::open(path).unwrap();
    conn.execute_batch(
        "INSERT INTO knowledge_entries (category, title, content, wing, room) \
         VALUES \
           ('pattern', 'Entry One',   'content 1', 'backend',  'auth'), \
           ('mistake', 'Entry Two',   'content 2', 'frontend', 'ui');",
    )
    .unwrap();
}

/// Read from `conn` accumulating bytes until the HTTP header separator
/// `\r\n\r\n` is found, the 5-second deadline expires, or the connection
/// closes.  Prevents packet-splitting flakes caused by a single fixed-size
/// read.
async fn read_http_headers(conn: &mut tokio::net::TcpStream) -> String {
    use tokio::io::AsyncReadExt;
    let mut buf: Vec<u8> = Vec::with_capacity(2048);
    let deadline = tokio::time::Instant::now() + Duration::from_secs(5);
    loop {
        let remaining = deadline.saturating_duration_since(tokio::time::Instant::now());
        if remaining.is_zero() {
            break;
        }
        let mut chunk = vec![0u8; 512];
        match tokio::time::timeout(remaining, conn.read(&mut chunk)).await {
            Ok(Ok(0)) | Err(_) => break,
            Ok(Ok(n)) => {
                buf.extend_from_slice(&chunk[..n]);
                if buf.windows(4).any(|w| w == b"\r\n\r\n") {
                    break;
                }
            }
            Ok(Err(_)) => break,
        }
    }
    String::from_utf8_lossy(&buf).into_owned()
}

/// Read SSE body chunks from `conn`, accumulating until *all* strings in
/// `expect` are present in the buffer or a 6-second wall-clock deadline
/// passes.  Avoids chunk/timing flakes caused by a single read after a fixed
/// sleep.
async fn read_until_sse_contains(conn: &mut tokio::net::TcpStream, expect: &[&str]) -> String {
    use tokio::io::AsyncReadExt;
    let mut accumulated = String::new();
    let deadline = tokio::time::Instant::now() + Duration::from_secs(6);
    loop {
        if expect.iter().all(|e| accumulated.contains(e)) {
            break;
        }
        let remaining = deadline.saturating_duration_since(tokio::time::Instant::now());
        if remaining.is_zero() {
            break;
        }
        let mut chunk = vec![0u8; 4096];
        match tokio::time::timeout(
            remaining.min(Duration::from_millis(500)),
            conn.read(&mut chunk),
        )
        .await
        {
            Ok(Ok(0)) | Err(_) => {
                // Either connection closed or this poll window expired; loop
                // back to check the deadline and expected-string conditions.
            }
            Ok(Ok(n)) => {
                accumulated.push_str(&String::from_utf8_lossy(&chunk[..n]));
            }
            Ok(Err(_)) => break,
        }
    }
    accumulated
}

// ── Header tests (oneshot) ────────────────────────────────────────────────────

#[tokio::test]
async fn live_emits_x_accel_buffering_no() {
    let path = unique_db_path("xab");
    let db = mk_db_at(&path);
    let r = app(open_state_with_db(db))
        .oneshot(
            Request::builder()
                .uri("/api/live")
                .body(Body::empty())
                .unwrap(),
        )
        .await
        .unwrap();

    assert_eq!(r.status(), StatusCode::OK);
    let val = r
        .headers()
        .get("x-accel-buffering")
        .expect("x-accel-buffering header required for nginx proxy parity")
        .to_str()
        .unwrap();
    assert_eq!(
        val, "no",
        "x-accel-buffering must be 'no' to disable nginx proxy buffering, got {val}"
    );
}

#[tokio::test]
async fn live_returns_text_event_stream_content_type() {
    let path = unique_db_path("ct");
    let db = mk_db_at(&path);
    let r = app(open_state_with_db(db))
        .oneshot(
            Request::builder()
                .uri("/api/live")
                .body(Body::empty())
                .unwrap(),
        )
        .await
        .unwrap();

    assert_eq!(r.status(), StatusCode::OK);
    let ct = r
        .headers()
        .get("content-type")
        .expect("content-type header required")
        .to_str()
        .unwrap();
    assert!(
        ct.contains("text/event-stream"),
        "content-type must be text/event-stream, got {ct}"
    );
}

#[tokio::test]
async fn live_has_cache_control_no_cache() {
    let path = unique_db_path("cc");
    let db = mk_db_at(&path);
    let r = app(open_state_with_db(db))
        .oneshot(
            Request::builder()
                .uri("/api/live")
                .body(Body::empty())
                .unwrap(),
        )
        .await
        .unwrap();

    assert_eq!(r.status(), StatusCode::OK);
    let cc = r
        .headers()
        .get("cache-control")
        .expect("cache-control header required")
        .to_str()
        .unwrap();
    assert!(
        cc.contains("no-cache"),
        "cache-control must contain no-cache for SSE, got {cc}"
    );
}

// ── Auth test (oneshot) ───────────────────────────────────────────────────────

#[tokio::test]
async fn live_returns_401_without_token_when_auth_required() {
    let path = unique_db_path("auth");
    let db = mk_db_at(&path);
    let state = AppState::new(
        Arc::new(ServerConfig {
            server_token: "secret".to_string(),
            ..ServerConfig::default()
        }),
        db,
    );

    let r = app(state)
        .oneshot(
            Request::builder()
                .uri("/api/live")
                .body(Body::empty())
                .unwrap(),
        )
        .await
        .unwrap();

    assert_eq!(r.status(), StatusCode::UNAUTHORIZED);
}

// ── Seeded-events test (real TCP server) ─────────────────────────────────────

/// Start the router on a random port, connect via raw TCP, seed two entries,
/// wait for the 2-second poll, then verify the SSE stream contains both events
/// with `id:` fields and JSON with the required shape keys.
#[tokio::test]
async fn live_seeded_two_events_appear_in_stream() {
    use tokio::io::AsyncWriteExt;

    let db_path = unique_db_path("seed");
    let db = mk_db_at(&db_path);
    let state = open_state_with_db(db);

    // Bind a random OS-assigned port.
    let listener = tokio::net::TcpListener::bind("127.0.0.1:0").await.unwrap();
    let addr = listener.local_addr().unwrap();

    // Spawn the server; it will shut itself down after 8 seconds via a sleep.
    tokio::spawn(async move {
        axum::serve(listener, app(state))
            .with_graceful_shutdown(async {
                tokio::time::sleep(Duration::from_secs(8)).await;
            })
            .await
            .ok();
    });

    // Give the server a moment to bind.
    tokio::time::sleep(Duration::from_millis(50)).await;

    // Open a raw HTTP/1.1 connection for the SSE request.
    let mut conn = tokio::net::TcpStream::connect(addr).await.unwrap();
    conn.write_all(
        b"GET /api/live HTTP/1.1\r\n\
          Host: 127.0.0.1\r\n\
          Accept: text/event-stream\r\n\
          Connection: keep-alive\r\n\
          \r\n",
    )
    .await
    .unwrap();

    // Read until the HTTP header separator to verify the 200 / content-type.
    // Using a loop-until-\r\n\r\n helper prevents packet-splitting flakes.
    let header_str = read_http_headers(&mut conn).await;
    assert!(
        header_str.contains("200"),
        "expected 200 response, got: {header_str}"
    );
    assert!(
        header_str.contains("text/event-stream"),
        "expected text/event-stream in response headers"
    );

    // Seed two entries AFTER the connection is established (cursor = 0 on empty DB).
    seed_two_entries(&db_path);

    // Read SSE body chunks until both expected fields appear or the 6-second
    // deadline passes.  Avoids a fixed sleep + single-read timing fragility.
    let event_str = read_until_sse_contains(
        &mut conn,
        &["\"category\"", "\"title\"", "\"wing\"", "\"room\""],
    )
    .await;

    // In HTTP/1.1 chunked encoding the SSE text is embedded inside chunk
    // boundaries.  We search the raw bytes for the SSE fields we expect.
    assert!(
        event_str.contains("id:") || event_str.contains("id: "),
        "expected id: field in SSE stream, got: {event_str:?}"
    );
    assert!(
        event_str.contains("data:") || event_str.contains("data: "),
        "expected data: field in SSE stream, got: {event_str:?}"
    );
    // Verify JSON keys in the data payload.
    assert!(
        event_str.contains("\"category\""),
        "SSE data must include 'category' key"
    );
    assert!(
        event_str.contains("\"title\""),
        "SSE data must include 'title' key"
    );
    assert!(
        event_str.contains("\"wing\""),
        "SSE data must include 'wing' key"
    );
    assert!(
        event_str.contains("\"room\""),
        "SSE data must include 'room' key"
    );
}
