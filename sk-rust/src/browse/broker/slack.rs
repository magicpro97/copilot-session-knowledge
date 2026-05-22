//! Slack broker stub — issue #454 follow-up (PR-D).
//!
//! Intentionally unimplemented.  Compiles and is feature-gated behind
//! `browse-broker`; not wired into any runtime path.
use anyhow::Result;
use tokio_util::sync::CancellationToken;

/// Slack broker stub (not implemented — see issue #454 follow-up PR-D).
pub struct SlackBroker;

impl super::Broker for SlackBroker {
    async fn run(&self, _token: CancellationToken) -> Result<()> {
        unimplemented!("Slack broker — see issue #454 follow-up PR-D")
    }
}

#[cfg(test)]
mod tests {
    #[test]
    #[ignore = "Slack broker not implemented — see issue #454 PR-D"]
    fn placeholder() {}
}
