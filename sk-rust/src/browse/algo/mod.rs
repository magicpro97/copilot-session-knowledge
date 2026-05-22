//! Pure algorithm port of browse Python modules: projection, similarity, communities.
//! No I/O, no HTTP, no DB, no cache. Suitable for use without any Cargo features.

pub mod communities;
pub mod projection;
pub mod similarity;
