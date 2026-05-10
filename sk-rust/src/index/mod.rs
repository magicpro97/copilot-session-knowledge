//! Native session-state document indexer.
//!
//! Wave-2 landing zone for Copilot session indexing logic ported out of
//! `build-session-index.py`.  Only the **Copilot session-state path** is
//! handled natively; Claude JSONL sessions are handled by `claude` (Wave 3).
//!
//! Wave-14: `extract` provides the native hot-path classification/write loop
//! from `extract-knowledge.py`, feature-gated behind `native-extract`.

pub mod claude;
pub mod session;

#[cfg(feature = "native-extract")]
pub mod extract;
