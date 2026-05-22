//! Operator module — session CRUD console and operator actions.
//!
//! - `console` — session lifecycle, path confinement, model normalization
//!   (issue #451 PR-A)
//! - `actions` — read-only diagnostic command suggestions for the browse UI
//!   settings page (NEVER browser-executed)

pub mod actions;
pub mod console;
