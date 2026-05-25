//! Library surface for `sk` — currently only exposes browse modules for
//! integration testing and future crate-level reuse.

// `browse::algo` (pure algorithms) is always compiled in; feature-gated
// submodules inside `browse/mod.rs` guard the server-specific code.
pub mod browse;

#[cfg(feature = "browse-server")]
#[allow(dead_code)]
mod config;

#[cfg(feature = "browse-server")]
#[allow(dead_code)]
mod db;

#[cfg(feature = "browse-server")]
#[allow(dead_code)]
mod sync;
