//! Native Rust port of `browse/` Python modules.
//!
//! Phase 1 (issue #455): pure parser + helpers scaffold.
//! Phase 2 (issue #447): HTTP server core — behind `browse-server` feature.
//! Phase 3 (issue #449): DB connection pool — behind `browse-server` feature.

#![allow(dead_code)]

#[cfg(feature = "browse-server")]
pub mod auth;
#[cfg(feature = "browse-server")]
pub mod cors;
#[cfg(feature = "browse-server")]
pub mod db;
#[cfg(feature = "browse-server")]
pub mod db_debug_log;
pub mod importers;
#[cfg(feature = "browse-server")]
pub mod server;
#[cfg(feature = "browse-server")]
pub mod static_files;
