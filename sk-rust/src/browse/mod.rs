//! Native Rust port of `browse/` Python modules.
//!
//! Phase 1 (issue #455): pure parser + helpers scaffold.
//! Phase 2 (issue #447): HTTP server core — behind `browse-server` feature.
//! Phase 3 (issue #449): DB connection pool — behind `browse-server` feature.

#![allow(dead_code)]

pub mod db;
pub mod importers;
pub mod server;
