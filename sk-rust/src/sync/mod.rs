//! Native sync engine modules.
//!
//! Always compiled:
//! - [`schema`] — SQL schema constants and `ensure_sync_schema`
//! - [`db`]     — SQLite operations (replica ID, collect txns, apply ops, …)
//!
//! Compiled only with `--features native-sync`:
//! - [`engine`] — HTTP push/pull cycle using `reqwest::blocking`

pub mod db;
pub mod schema;

#[cfg(feature = "native-sync")]
pub mod engine;
