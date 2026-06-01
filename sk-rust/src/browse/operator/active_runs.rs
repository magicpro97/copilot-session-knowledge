//! In-memory active-run registry (issue #451 PR-B).
//!
//! Tracks tentacle runs that are currently in progress.  The registry is
//! intentionally in-memory (not persisted) — it reflects the live state of
//! the current process.  File-persisted historical runs live in `runs.rs`.
//!
//! # Real-time consumers
//! The existing minimal HTTP server does not support SSE streams.  Clients
//! that need live progress should poll `GET /api/operator/runs` (list endpoint).
//! A future SSE upgrade can reuse this registry without changing its API.
//!
//! # Thread-safety
//! A process-global `RwLock<HashMap>` protected by `OnceLock` is used,
//! matching the `OnceLock<Mutex<…>>` pattern already used in the crate
//! (e.g. `embeddings/tfidf.rs` and `hooks/rules/retry/queue.rs`).

use std::collections::HashMap;
use std::sync::{OnceLock, RwLock};

use serde::{Deserialize, Serialize};

// ── ActiveRun struct ───────────────────────────────────────────────────────────

/// An in-progress tentacle run tracked in the live registry.
///
/// `progress` is a percentage in `[0.0, 100.0]`.
#[derive(Serialize, Deserialize, Clone, Debug, PartialEq)]
pub struct ActiveRun {
    /// Unique run identifier (UUID v4).
    pub id: String,
    /// The tentacle that owns this run.
    pub tentacle_name: String,
    /// Lifecycle status: `running`, `done`, `failed`, `timeout`, `cancelled`.
    pub status: String,
    /// RFC-3339 timestamp when the run started.
    pub started_at: String,
    /// Completion percentage in `[0.0, 100.0]`.
    pub progress: f64,
    /// Files declared in the tentacle's scope.
    pub assigned_files: Vec<String>,
}

// ── Registry ───────────────────────────────────────────────────────────────────

/// Process-global active-run registry.
fn registry() -> &'static RwLock<HashMap<String, ActiveRun>> {
    static REGISTRY: OnceLock<RwLock<HashMap<String, ActiveRun>>> = OnceLock::new();
    REGISTRY.get_or_init(|| RwLock::new(HashMap::new()))
}

/// Insert or replace a run in the registry.
pub fn add(run: ActiveRun) {
    registry()
        .write()
        .expect("active-run registry write lock poisoned")
        .insert(run.id.clone(), run);
}

/// Remove a run from the registry by ID.  Returns the removed run, or `None`.
pub fn remove(id: &str) -> Option<ActiveRun> {
    registry()
        .write()
        .expect("active-run registry write lock poisoned")
        .remove(id)
}

/// Return a snapshot of all active runs.  Order is unspecified.
pub fn list() -> Vec<ActiveRun> {
    registry()
        .read()
        .expect("active-run registry read lock poisoned")
        .values()
        .cloned()
        .collect()
}

/// Look up a single run by ID.  Returns `None` when not found.
pub fn get(id: &str) -> Option<ActiveRun> {
    registry()
        .read()
        .expect("active-run registry read lock poisoned")
        .get(id)
        .cloned()
}

/// Return `true` if any run in the registry is associated with `session_id`.
///
/// A run is considered to belong to a session when its `tentacle_name` starts
/// with the session ID.  Callers that register runs with other naming schemes
/// should call `add` with a `tentacle_name` that includes the session ID as a
/// prefix (e.g. `"{session_id}/{tentacle_name}"`).
pub fn has_run_for_session(session_id: &str) -> bool {
    registry()
        .read()
        .expect("active-run registry read lock poisoned")
        .values()
        .any(|r| r.tentacle_name.starts_with(session_id))
}

// ── Unit tests ─────────────────────────────────────────────────────────────────

#[cfg(test)]
mod tests {
    use super::*;

    fn sample(id: &str) -> ActiveRun {
        ActiveRun {
            id: id.to_string(),
            tentacle_name: format!("session-abc/{id}"),
            status: "running".to_string(),
            started_at: "2024-01-01T00:00:00Z".to_string(),
            progress: 0.0,
            assigned_files: vec!["src/main.rs".to_string()],
        }
    }

    // Each test uses distinct IDs to avoid cross-test interference from the
    // shared global registry (tests may run in parallel or sequentially).

    #[test]
    fn test_add_and_list() {
        let run = sample("aaaa-add-list-0001");
        add(run.clone());
        let found = list().into_iter().any(|r| r.id == run.id);
        assert!(found, "added run should appear in list()");
        remove(&run.id);
    }

    #[test]
    fn test_get_hit() {
        let run = sample("bbbb-get-hit-0002");
        add(run.clone());
        let result = get(&run.id);
        assert_eq!(
            result,
            Some(run.clone()),
            "get() should return the added run"
        );
        remove(&run.id);
    }

    #[test]
    fn test_get_miss() {
        let result = get("cccc-missing-0003-not-added");
        assert_eq!(result, None, "get() on unknown id should return None");
    }

    #[test]
    fn test_remove() {
        let run = sample("dddd-remove-0004");
        add(run.clone());
        assert!(get(&run.id).is_some(), "run should exist before remove");
        let removed = remove(&run.id);
        assert_eq!(removed, Some(run.clone()), "remove() should return the run");
        assert_eq!(get(&run.id), None, "run should be gone after remove");
    }

    #[test]
    fn test_has_run_for_session_true() {
        let mut run = sample("eeee-session-0005");
        run.tentacle_name = "session-xyz/tentacle-1".to_string();
        add(run.clone());
        assert!(has_run_for_session("session-xyz"));
        remove(&run.id);
    }

    #[test]
    fn test_has_run_for_session_false() {
        assert!(!has_run_for_session("session-no-such-0006"));
    }
}
