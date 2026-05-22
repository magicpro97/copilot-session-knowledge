//! Library surface for `sk` — currently only exposes browse modules for
//! integration testing and future crate-level reuse.

#[cfg(feature = "browse-server")]
pub mod browse;

#[cfg(feature = "browse-server")]
#[allow(dead_code)]
mod config;

#[cfg(feature = "browse-server")]
#[allow(dead_code)]
mod db;
