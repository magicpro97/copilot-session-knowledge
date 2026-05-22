//! Ably broker stub — issue #454 follow-up (PR-C).
//!
//! Intentionally unimplemented.  Compiles and is feature-gated behind
//! `browse-broker`; not wired into any runtime path.
use anyhow::Result;
use tokio_util::sync::CancellationToken;

/// Ably broker stub (not implemented — see issue #454 follow-up PR-C).
pub struct AblyBroker;

impl super::Broker for AblyBroker {
    async fn run(&self, _token: CancellationToken) -> Result<()> {
        unimplemented!("Ably broker — see issue #454 follow-up PR-C")
    }
}

#[cfg(test)]
mod tests {
    #[test]
    #[ignore = "Ably broker not implemented — see issue #454 PR-C"]
    fn placeholder() {}
}
