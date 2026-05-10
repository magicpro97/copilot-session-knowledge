//! Native HTTP push/pull engine for `sk sync run`.
//!
//! Gated behind the `native-sync` feature flag.  When this feature is active,
//! `run_sync_run_command` calls `run_native_sync_cycle` instead of shelling out
//! to `sync-daemon.py --once`.
//!
//! ## What this module does (mirroring sync-daemon.py)
//!
//! 1. `gateway_health` → GET `{base_url}/healthz`
//! 2. `push_once`      → collect pending txns, POST `{base_url}/sync/push`,
//!                        validate response, mark committed
//! 3. `pull_once`      → paginated GET `{base_url}/sync/pull?…`,
//!                        apply each remote txn to the local DB, then refresh
//!                        `knowledge_fts` / `ke_fts` for touched rows
//! 4. `run_native_sync_cycle` — orchestrates health + push + pull

#![cfg(feature = "native-sync")]

use std::collections::HashSet;
use std::path::Path;
use std::time::Duration;

use reqwest::blocking::Client;
use serde_json::Value;

use crate::sync::db::{
    apply_remote_txn, collect_pending_txns, effective_sync_limit, get_or_create_replica_id,
    lookup_local_id_by_stable_id, mark_txns_committed, open_sync_db, record_failure,
    refresh_local_retrieval_surfaces, repair_nonlocal_committed_txns, set_sync_state,
    MAX_PULL_PAGES,
};

const PUSH_TIMEOUT_SECS: u64 = 120;
const PULL_TIMEOUT_SECS: u64 = 15;
const HEALTH_TIMEOUT_SECS: u64 = 5;

// ── Public entry point ────────────────────────────────────────────────────────

/// Run one native sync cycle (health check → optional push → optional pull).
///
/// Replaces the Python `run_sync_cycle` + `sync-daemon.py --once` subprocess.
/// Returns `Ok(())` on success or no-remote; `Err(msg)` on failure.
pub fn run_native_sync_cycle(
    db_path: &Path,
    base_url: &str,
    limit: usize,
    do_push: bool,
    do_pull: bool,
) -> Result<(), String> {
    let conn = open_sync_db(db_path).map_err(|e| format!("open_sync_db: {e}"))?;

    let replica_id = get_or_create_replica_id(&conn).map_err(|e| format!("get_replica_id: {e}"))?;

    repair_nonlocal_committed_txns(&conn, &replica_id).map_err(|e| format!("repair_txns: {e}"))?;

    if base_url.is_empty() {
        set_sync_state(
            &conn,
            "last_error",
            "sync disabled: no connection_string configured",
        )
        .ok();
        return Ok(());
    }

    // Health check — abort the cycle if the gateway is unreachable.
    if let Err(e) = gateway_health(base_url) {
        let msg = format!("health check failed: {e}");
        eprintln!("[sync] {msg}");
        let _ = record_failure(&conn, "gateway_health", &msg, "", "", "");
        return Err(msg);
    }

    let eff_limit = effective_sync_limit(&conn, limit, &replica_id);

    if do_push {
        match push_once(&conn, base_url, &replica_id, eff_limit) {
            Ok(r) => println!(
                "[sync] push  attempted={} accepted={} duplicates={}",
                r.attempted, r.accepted, r.duplicates
            ),
            Err(e) => {
                let msg = format!("push failed: {e}");
                eprintln!("[sync] {msg}");
                let _ = record_failure(&conn, "push_cycle", &msg, "", "", "");
                return Err(msg);
            }
        }
    }

    if do_pull {
        match pull_once(&conn, base_url, &replica_id, eff_limit) {
            Ok(r) => println!("[sync] pull  applied={} has_more={}", r.applied, r.has_more),
            Err(e) => {
                let msg = format!("pull failed: {e}");
                eprintln!("[sync] {msg}");
                let _ = record_failure(&conn, "pull_cycle", &msg, "", "", "");
                return Err(msg);
            }
        }
    }

    let now = utc_now();
    set_sync_state(&conn, "last_sync_activity", &now)
        .map_err(|e| format!("set_sync_activity: {e}"))?;
    set_sync_state(&conn, "last_error", "").ok();

    Ok(())
}

// ── Health check ──────────────────────────────────────────────────────────────

fn gateway_health(base_url: &str) -> Result<(), String> {
    let url = format!("{}/healthz", base_url.trim_end_matches('/'));
    let client = build_client(HEALTH_TIMEOUT_SECS)?;
    client
        .get(&url)
        .send()
        .and_then(|r| r.error_for_status())
        .map(|_| ())
        .map_err(|e| e.to_string())
}

// ── Push ──────────────────────────────────────────────────────────────────────

pub struct PushResult {
    pub attempted: usize,
    pub accepted: usize,
    pub duplicates: usize,
}

fn push_once(
    conn: &rusqlite::Connection,
    base_url: &str,
    replica_id: &str,
    limit: usize,
) -> Result<PushResult, String> {
    let txns = collect_pending_txns(conn, limit, replica_id)
        .map_err(|e| format!("collect_pending_txns: {e}"))?;

    if txns.is_empty() {
        return Ok(PushResult {
            attempted: 0,
            accepted: 0,
            duplicates: 0,
        });
    }

    let sent_ids: HashSet<String> = txns
        .iter()
        .filter_map(|t| {
            t.get("txn_id")
                .and_then(|v| v.as_str())
                .map(|s| s.to_string())
        })
        .collect();

    let payload = serde_json::json!({ "replica_id": replica_id, "txns": txns });
    let url = format!("{}/sync/push", base_url.trim_end_matches('/'));
    let client = build_client(PUSH_TIMEOUT_SECS)?;

    let resp: Value = client
        .post(&url)
        .json(&payload)
        .send()
        .and_then(|r| r.error_for_status())
        .and_then(|r| r.json())
        .map_err(|e| format!("push request: {e}"))?;

    let accepted = gateway_txn_ids(&resp, "accepted_txn_ids")?;
    let duplicates = gateway_txn_ids(&resp, "duplicate_txn_ids")?;

    // Validate gateway response: no overlap, no unrequested IDs.
    let accepted_set: HashSet<_> = accepted.iter().cloned().collect();
    let dup_set: HashSet<_> = duplicates.iter().cloned().collect();
    if !accepted_set.is_disjoint(&dup_set) {
        return Err("gateway listed txn_ids as both accepted and duplicate".into());
    }
    let all_returned: HashSet<_> = accepted_set.union(&dup_set).cloned().collect();
    let unexpected: Vec<_> = all_returned
        .difference(&sent_ids)
        .take(5)
        .cloned()
        .collect();
    if !unexpected.is_empty() {
        return Err(format!(
            "gateway referenced unsent txn_ids: {:?}",
            unexpected
        ));
    }

    let latest = resp
        .get("latest_txn_id")
        .and_then(|v| v.as_str())
        .unwrap_or("")
        .to_string();

    let all_committed: Vec<String> = accepted.iter().chain(duplicates.iter()).cloned().collect();
    mark_txns_committed(conn, &all_committed).map_err(|e| format!("mark_committed: {e}"))?;

    if !latest.is_empty() {
        set_sync_state(conn, "last_pushed_txn_id", &latest).ok();
    }
    set_sync_state(conn, "last_push_at", &utc_now()).ok();
    set_sync_state(conn, "last_error", "").ok();

    Ok(PushResult {
        attempted: txns.len(),
        accepted: accepted.len(),
        duplicates: duplicates.len(),
    })
}

// ── Pull ──────────────────────────────────────────────────────────────────────

pub struct PullResult {
    pub applied: usize,
    pub has_more: bool,
}

fn pull_once(
    conn: &rusqlite::Connection,
    base_url: &str,
    replica_id: &str,
    limit: usize,
) -> Result<PullResult, String> {
    // Read the pull cursor (last seen txn_id for this replica).
    let after: String = conn
        .query_row(
            "SELECT last_txn_id FROM sync_cursors WHERE replica_id = ?",
            [replica_id],
            |r| r.get::<_, Option<String>>(0),
        )
        .ok()
        .flatten()
        .unwrap_or_default();

    let client = build_client(PULL_TIMEOUT_SECS)?;
    let base = base_url.trim_end_matches('/');
    let now = utc_now();

    let mut next_after = after;
    let mut applied = 0usize;
    let mut has_more = false;
    let mut pages = 0usize;

    // FTS refresh tracking — mirrors Python pull_once.
    // touched_doc/entry_stable_ids: from upsert ops (post-apply)
    // touched_doc/entry_ids: from delete ops (pre-apply, before rows are removed)
    let mut touched_doc_stable_ids: std::collections::HashSet<String> =
        std::collections::HashSet::new();
    let mut touched_entry_stable_ids: std::collections::HashSet<String> =
        std::collections::HashSet::new();
    let mut touched_doc_ids: std::collections::HashSet<i64> = std::collections::HashSet::new();
    let mut touched_entry_ids: std::collections::HashSet<i64> = std::collections::HashSet::new();

    while pages < MAX_PULL_PAGES {
        let url = format!(
            "{}/sync/pull?replica_id={}&after={}&limit={}",
            base,
            urlenccode(&replica_id),
            urlenccode(&next_after),
            limit.max(1)
        );

        let resp: Value = client
            .get(&url)
            .send()
            .and_then(|r| r.error_for_status())
            .and_then(|r| r.json())
            .map_err(|e| format!("pull request (page {}): {e}", pages + 1))?;

        let txns = resp
            .get("txns")
            .and_then(|v| v.as_array())
            .cloned()
            .unwrap_or_default();

        let mut last_seen = next_after.clone();
        for txn in &txns {
            let ops = txn
                .get("ops")
                .and_then(|v| v.as_array())
                .cloned()
                .unwrap_or_default();

            // Pre-apply: look up local integer IDs for delete ops before the
            // canonical rows are removed.  Mirrors Python pull_once first loop.
            for op in &ops {
                let table_name = op.get("table_name").and_then(|v| v.as_str()).unwrap_or("");
                let op_type = op.get("op_type").and_then(|v| v.as_str()).unwrap_or("");
                let row_stable_id = op
                    .get("row_stable_id")
                    .and_then(|v| v.as_str())
                    .unwrap_or("");
                if op_type == "delete" && !row_stable_id.is_empty() {
                    match table_name {
                        "documents" => {
                            if let Some(id) =
                                lookup_local_id_by_stable_id(conn, "documents", row_stable_id)
                            {
                                touched_doc_ids.insert(id);
                            }
                        }
                        "knowledge_entries" => {
                            if let Some(id) = lookup_local_id_by_stable_id(
                                conn,
                                "knowledge_entries",
                                row_stable_id,
                            ) {
                                touched_entry_ids.insert(id);
                            }
                        }
                        _ => {}
                    }
                }
            }

            // Apply the transaction (per-op errors are fail-open inside apply_remote_txn).
            apply_remote_txn(conn, txn, &now).map_err(|e| format!("apply_remote_txn: {e}"))?;

            // Post-apply: record stable IDs for upserted documents/entries.
            // Mirrors Python pull_once second loop.
            for op in &ops {
                let table_name = op.get("table_name").and_then(|v| v.as_str()).unwrap_or("");
                let row_stable_id = op
                    .get("row_stable_id")
                    .and_then(|v| v.as_str())
                    .unwrap_or("");
                let row_payload = op.get("row_payload");
                match table_name {
                    "documents" if !row_stable_id.is_empty() => {
                        touched_doc_stable_ids.insert(row_stable_id.to_string());
                    }
                    "sections" => {
                        let doc_stable = row_payload
                            .and_then(|v| v.get("document_stable_id"))
                            .and_then(|v| v.as_str())
                            .unwrap_or("");
                        if !doc_stable.is_empty() {
                            touched_doc_stable_ids.insert(doc_stable.to_string());
                        }
                    }
                    "knowledge_entries" if !row_stable_id.is_empty() => {
                        touched_entry_stable_ids.insert(row_stable_id.to_string());
                    }
                    _ => {}
                }
            }

            if let Some(id) = txn.get("txn_id").and_then(|v| v.as_str()) {
                last_seen = id.to_string();
            }
            applied += 1;
        }

        next_after = resp
            .get("next_after")
            .and_then(|v| v.as_str())
            .filter(|s| !s.is_empty())
            .map(|s| s.to_string())
            .unwrap_or(last_seen);

        has_more = resp
            .get("has_more")
            .and_then(|v| v.as_bool())
            .unwrap_or(false);

        pages += 1;
        if !has_more || txns.is_empty() {
            break;
        }
    }

    // Refresh FTS indices for all touched documents and knowledge entries.
    // Fail-open: errors are recorded in sync_failures, not propagated.
    // Mirrors _refresh_local_retrieval_surfaces called at the end of Python pull_once.
    {
        let doc_stable: Vec<String> = touched_doc_stable_ids.into_iter().collect();
        let entry_stable: Vec<String> = touched_entry_stable_ids.into_iter().collect();
        let doc_ids: Vec<i64> = touched_doc_ids.into_iter().collect();
        let entry_ids: Vec<i64> = touched_entry_ids.into_iter().collect();
        if let Err(e) =
            refresh_local_retrieval_surfaces(conn, &doc_stable, &entry_stable, &doc_ids, &entry_ids)
        {
            let _ = record_failure(conn, "local_fts_refresh", &e.to_string(), "", "", "");
        }
    }

    // Persist the pull cursor and sync state.
    if !next_after.is_empty() {
        conn.execute(
            "INSERT INTO sync_cursors (replica_id, last_txn_id, updated_at)
             VALUES (?1, ?2, datetime('now'))
             ON CONFLICT(replica_id) DO UPDATE SET
                 last_txn_id = excluded.last_txn_id,
                 updated_at  = datetime('now')",
            [replica_id, &next_after],
        )
        .map_err(|e| format!("update cursor: {e}"))?;
        set_sync_state(conn, "last_pulled_txn_id", &next_after).ok();
    }
    set_sync_state(conn, "last_pull_at", &utc_now()).ok();
    set_sync_state(conn, "last_error", "").ok();

    Ok(PullResult { applied, has_more })
}

// ── Helpers ───────────────────────────────────────────────────────────────────

fn build_client(timeout_secs: u64) -> Result<Client, String> {
    Client::builder()
        .timeout(Duration::from_secs(timeout_secs))
        .build()
        .map_err(|e| format!("build HTTP client: {e}"))
}

fn gateway_txn_ids(resp: &Value, field: &str) -> Result<Vec<String>, String> {
    let raw = resp.get(field).and_then(|v| v.as_array()).cloned();
    match raw {
        None => Ok(vec![]),
        Some(arr) => arr
            .into_iter()
            .map(|v| {
                v.as_str()
                    .filter(|s| !s.is_empty())
                    .map(|s| s.to_string())
                    .ok_or_else(|| format!("gateway response {field} contains invalid txn_id: {v}"))
            })
            .collect(),
    }
}

/// Minimal percent-encoding for query-string values (RFC 3986 unreserved chars).
fn urlenccode(s: &str) -> String {
    let mut out = String::with_capacity(s.len());
    for b in s.bytes() {
        match b {
            b'A'..=b'Z' | b'a'..=b'z' | b'0'..=b'9' | b'-' | b'_' | b'.' | b'~' => {
                out.push(b as char);
            }
            other => {
                out.push_str(&format!("%{:02X}", other));
            }
        }
    }
    out
}

fn utc_now() -> String {
    chrono::Utc::now().format("%Y-%m-%dT%H:%M:%SZ").to_string()
}

// ── Tests ─────────────────────────────────────────────────────────────────────

#[cfg(test)]
mod tests {
    use super::*;
    use std::sync::atomic::{AtomicU64, Ordering};

    static COUNTER: AtomicU64 = AtomicU64::new(0);

    /// Create a unique temp path for a test DB file (no external crates needed).
    fn temp_db_path() -> std::path::PathBuf {
        let n = COUNTER.fetch_add(1, Ordering::SeqCst);
        std::env::temp_dir().join(format!("sk_engine_test_{}_{}.db", std::process::id(), n))
    }

    #[test]
    fn run_native_sync_cycle_no_remote_is_ok() {
        // When base_url is empty, the cycle should succeed immediately (fail-open).
        let db_path = temp_db_path();
        let result = run_native_sync_cycle(&db_path, "", 50, true, true);
        let _ = std::fs::remove_file(&db_path);
        assert!(result.is_ok(), "no-remote cycle should be Ok: {:?}", result);
    }

    #[test]
    fn urlenccode_encodes_special_chars() {
        assert_eq!(urlenccode("abc"), "abc");
        assert_eq!(urlenccode("a b"), "a%20b");
        assert_eq!(urlenccode("a/b"), "a%2Fb");
        assert_eq!(urlenccode(""), "");
    }

    #[test]
    fn gateway_txn_ids_parses_correctly() {
        let resp = serde_json::json!({
            "accepted_txn_ids": ["txn-1", "txn-2"],
            "duplicate_txn_ids": []
        });
        let accepted = gateway_txn_ids(&resp, "accepted_txn_ids").unwrap();
        assert_eq!(accepted, vec!["txn-1", "txn-2"]);
        let dups = gateway_txn_ids(&resp, "duplicate_txn_ids").unwrap();
        assert!(dups.is_empty());
    }

    #[test]
    fn gateway_txn_ids_missing_field_returns_empty() {
        let resp = serde_json::json!({});
        let ids = gateway_txn_ids(&resp, "accepted_txn_ids").unwrap();
        assert!(ids.is_empty());
    }
}
