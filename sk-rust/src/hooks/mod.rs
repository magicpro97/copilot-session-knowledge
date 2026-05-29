/// Native hook runner modules.
///
/// Ports the Python `hook_runner.py` event-dispatch framework to Rust while
/// preserving full behavioural compatibility:
///   - stdin JSON parsed once, fail-open on parse error
///   - `preToolUse`: first-deny-wins semantics
///   - all other events: informational output only
///   - best-effort audit logging (never blocks)
///   - sync markers written for postToolUse / sessionEnd
pub mod audit;
pub mod marker_auth;
pub mod retry_listener;
pub mod rules;
pub mod runner;
pub mod sync_markers;
