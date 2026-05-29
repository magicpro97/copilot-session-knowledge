//! Library surface for `sk` — currently only exposes browse modules for
//! integration testing and future crate-level reuse.

// Shared retry library: policy, classification, delay, and decision.
// Zero new dependencies -- uses only `regex` (already a dep) and a stdlib PRNG.
pub mod retry;

// `browse::algo` (pure algorithms) is always compiled in; feature-gated
// submodules inside `browse/mod.rs` guard the server-specific code.
pub mod browse;

#[cfg(feature = "browse-server")]
#[allow(dead_code)]
mod config;

#[cfg(feature = "browse-server")]
#[allow(dead_code)]
mod db;

// Expose only the always-compiled `extract_schema` helper from the `index`
// tree so that `db::writer_broker` can bootstrap fresh-DB schema in lib
// builds (issue #572 CI fix). The bin target still pulls in the full
// `index` module via `main.rs`; this `#[path]` alias points the lib at
// the same `src/index/` directory while only declaring the leaf needed
// here, keeping the lib free of feature-gated `index::extract` and the
// other indexer-only submodules.
#[cfg(feature = "browse-server")]
#[allow(dead_code)]
#[path = "index"]
mod index {
    pub mod extract_schema;
}

#[cfg(feature = "browse-server")]
#[allow(dead_code)]
mod sync;
