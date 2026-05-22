//! Discord broker stub — issue #454 follow-up (PR-B).
//!
//! Intentionally unimplemented.  Compiles and is feature-gated behind
//! `browse-broker`; not wired into any runtime path.
use anyhow::Result;
use tokio_util::sync::CancellationToken;

/// Discord broker stub (not implemented — see issue #454 follow-up PR-B).
pub struct DiscordBroker;

impl super::Broker for DiscordBroker {
    async fn run(&self, _token: CancellationToken) -> Result<()> {
        unimplemented!("Discord broker — see issue #454 follow-up PR-B")
    }
}

#[cfg(test)]
mod tests {
    #[test]
    #[ignore = "Discord broker not implemented — see issue #454 PR-B"]
    fn placeholder() {}
}
