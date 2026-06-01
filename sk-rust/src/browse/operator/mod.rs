//! Operator module — session CRUD console and operator actions.
//!
//! - `console` — session lifecycle, path confinement, model normalization
//!   (issue #451 PR-A)
//! - `actions` — read-only diagnostic command suggestions for the browse UI
//!   settings page (NEVER browser-executed)
//! - `redaction` — allowlist-first `BrowseDebugEntry` redaction helper
//!   (issue #451 PR-B)

pub mod actions;
pub mod active_runs;
pub mod console;
pub mod redaction;
pub mod runs;
