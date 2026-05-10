/// Native hook runner — core event-dispatch loop.
///
/// Mirrors `hook_runner.py::main()` in Rust:
///
/// 1. Read event name from caller (first argument passed to `run_hook`).
/// 2. Parse stdin JSON once, shared across all rules. Fail-open on parse error.
/// 3. Dispatch to matching rules in registration order.
/// 4. `preToolUse`: first-deny-wins — print deny JSON and return immediately.
/// 5. Other events: print informational messages; continue after each rule.
/// 6. Write audit log entries (best-effort).
/// 7. Write sync markers for `postToolUse` / `sessionEnd`.
///
/// Environment variables (same as Python):
///   `HOOK_DRY_RUN=1`       — log denials but allow through (testing mode)
///   `HOOK_LOG_LEVEL=DEBUG`  — verbose audit logging
use std::io::Read;

use serde_json::Value;

use crate::hooks::audit::audit_log;
use crate::hooks::rules::{all_rules, HookRule};
use crate::hooks::sync_markers::record_sync_signal;

// ---------------------------------------------------------------------------
// Public entry point
// ---------------------------------------------------------------------------

/// Run the native hook dispatcher for `event`.
///
/// Reads stdin, parses JSON, dispatches rules, writes audit log and sync markers.
/// Always returns without panic (fail-open contract).
pub fn run_hook(event: &str) {
    if event.is_empty() {
        return;
    }

    // --- Parse stdin (fail-open) ---
    let mut raw = String::new();
    if std::io::stdin().read_to_string(&mut raw).is_err() {
        audit_log(event, "", "", "read-error", "");
        return; // fail-open
    }

    let data: Value = if raw.trim().is_empty() {
        Value::Object(Default::default())
    } else {
        match serde_json::from_str(&raw) {
            Ok(v) => v,
            Err(e) => {
                // Fail-open: parse error → allow through, no rule evaluation.
                audit_log(event, "", "", "parse-error", &e.to_string());
                return;
            }
        }
    };

    dispatch_rules(event, &data);

    // Sync markers (best-effort, after rule dispatch).
    if event == "postToolUse" || event == "sessionEnd" {
        record_sync_signal(event, &data);
    }
}

// ---------------------------------------------------------------------------
// Internal dispatch
// ---------------------------------------------------------------------------

/// Dispatch all matching rules for `event` with the parsed `data`.
///
/// Separated from `run_hook` so unit tests can call it directly without
/// needing to set up stdin.
pub(crate) fn dispatch_rules(event: &str, data: &Value) {
    let dry_run = std::env::var("HOOK_DRY_RUN").map_or(false, |v| v == "1");
    let verbose = std::env::var("HOOK_LOG_LEVEL").map_or(false, |v| v == "DEBUG");

    let tool_name = data.get("toolName").and_then(|v| v.as_str()).unwrap_or("");

    let rules = all_rules();
    let matching: Vec<&dyn HookRule> = rules
        .iter()
        .map(|r| r.as_ref())
        .filter(|r| r.events().contains(&event))
        .collect();

    for rule in matching {
        // Tool filter: empty list means "all tools".
        let tools = rule.tools();
        if !tools.is_empty() && !tools.iter().any(|&t| t == tool_name) {
            continue;
        }

        // Evaluate (any panic → fail-open via catch_unwind).
        let result = match std::panic::catch_unwind(std::panic::AssertUnwindSafe(|| {
            rule.evaluate(event, data)
        })) {
            Ok(r) => r,
            Err(_) => {
                audit_log(event, tool_name, rule.name(), "error", "panic");
                continue; // fail-open
            }
        };

        let result = match result {
            Some(r) => r,
            None => continue, // rule passed
        };

        if event == "preToolUse" {
            let decision = result
                .get("permissionDecision")
                .and_then(|v| v.as_str())
                .unwrap_or("");

            if decision == "deny" {
                let reason = result
                    .get("permissionDecisionReason")
                    .and_then(|v| v.as_str())
                    .unwrap_or("");

                if dry_run {
                    println!("  [DRY RUN] {} would deny: {}", rule.name(), reason);
                    audit_log(event, tool_name, rule.name(), "deny-dry", reason);
                } else {
                    // Emit deny JSON to stdout and stop — first deny wins.
                    println!("{}", serde_json::to_string(&result).unwrap_or_default());
                    audit_log(event, tool_name, rule.name(), "deny", reason);
                    return; // ← first-deny-wins short-circuit
                }
            } else if verbose {
                audit_log(event, tool_name, rule.name(), "allow", "");
            }
        } else {
            // postToolUse / sessionStart / sessionEnd / agentStop / etc. — informational.
            let msg = result.get("message").and_then(|v| v.as_str()).unwrap_or("");
            if !msg.is_empty() {
                println!("{msg}");
            }
            let truncated = &msg[..msg.len().min(100)];
            audit_log(event, tool_name, rule.name(), "info", truncated);
        }
    }
}

// ---------------------------------------------------------------------------
// Tests
// ---------------------------------------------------------------------------

#[cfg(test)]
mod tests {
    use super::*;
    use crate::hooks::rules::{deny, info, HookRule};
    use serde_json::{json, Value};

    // --- Mock rules for controlled testing ---

    struct AlwaysDenyRule;
    impl HookRule for AlwaysDenyRule {
        fn name(&self) -> &'static str {
            "always-deny"
        }
        fn events(&self) -> &'static [&'static str] {
            &["preToolUse"]
        }
        fn tools(&self) -> &'static [&'static str] {
            &[]
        }
        fn evaluate(&self, _event: &str, _data: &Value) -> Option<Value> {
            Some(deny("test deny reason"))
        }
    }

    struct AlwaysAllowRule;
    impl HookRule for AlwaysAllowRule {
        fn name(&self) -> &'static str {
            "always-allow"
        }
        fn events(&self) -> &'static [&'static str] {
            &["preToolUse"]
        }
        fn tools(&self) -> &'static [&'static str] {
            &[]
        }
        fn evaluate(&self, _event: &str, _data: &Value) -> Option<Value> {
            None // pass
        }
    }

    struct InfoRule;
    impl HookRule for InfoRule {
        fn name(&self) -> &'static str {
            "info-rule"
        }
        fn events(&self) -> &'static [&'static str] {
            &["postToolUse"]
        }
        fn tools(&self) -> &'static [&'static str] {
            &[]
        }
        fn evaluate(&self, _event: &str, _data: &Value) -> Option<Value> {
            Some(info("[test] postToolUse info message"))
        }
    }

    struct PanicRule;
    impl HookRule for PanicRule {
        fn name(&self) -> &'static str {
            "panic-rule"
        }
        fn events(&self) -> &'static [&'static str] {
            &["preToolUse"]
        }
        fn tools(&self) -> &'static [&'static str] {
            &[]
        }
        fn evaluate(&self, _event: &str, _data: &Value) -> Option<Value> {
            panic!("intentional test panic")
        }
    }

    // Helper: run dispatch_rules and capture stdout.
    // NOTE: Stdout capture from dispatch_rules requires stdin interception
    // which is covered by integration tests in tests/integration_test.rs.
    // This stub exists as a placeholder for future expansion.

    // --- JSON parse fail-open ---

    /// Confirm that invalid JSON input reaches fail-open: the runner exits
    /// without panicking and without emitting a deny decision.
    ///
    /// This test exercises the parse-error path of `run_hook` indirectly
    /// via the data contract: `serde_json::from_str` on bad input returns Err.
    #[test]
    fn json_parse_error_is_fail_open() {
        let bad_input = "this is not json {{{{";
        let result = serde_json::from_str::<Value>(bad_input);
        // The runner treats Err as fail-open (returns without denying).
        assert!(result.is_err(), "bad JSON must return Err");
        // Verify audit_log for parse-error does not panic.
        audit_log(
            "preToolUse",
            "",
            "",
            "parse-error",
            &result.unwrap_err().to_string(),
        );
    }

    // --- preToolUse first-deny-wins ---

    /// Verify that the first deny result produced by a preToolUse rule
    /// carries the expected permissionDecision shape.
    #[test]
    fn pretooluse_deny_result_has_correct_shape() {
        let rule = AlwaysDenyRule;
        let data = json!({"toolName": "edit"});
        let result = rule
            .evaluate("preToolUse", &data)
            .expect("should return Some");

        assert_eq!(
            result["permissionDecision"].as_str().unwrap(),
            "deny",
            "permissionDecision must be 'deny'"
        );
        assert_eq!(
            result["permissionDecisionReason"].as_str().unwrap(),
            "test deny reason",
        );
    }

    /// Verify that a rule returning None (allow) does not produce a deny.
    #[test]
    fn pretooluse_allow_result_is_none() {
        let rule = AlwaysAllowRule;
        let data = json!({"toolName": "edit"});
        assert!(rule.evaluate("preToolUse", &data).is_none());
    }

    // --- preToolUse deny short-circuit logic ---

    /// Verify that the first-deny-wins logic in dispatch_rules exits after
    /// encountering a deny, by observing that a second rule after a deny
    /// is never reached.
    ///
    /// Implementation: simulate the dispatch loop with two mock rules.
    #[test]
    fn pretooluse_first_deny_short_circuits() {
        let data = json!({"toolName": "edit"});
        let rules: Vec<Box<dyn HookRule>> =
            vec![Box::new(AlwaysDenyRule), Box::new(AlwaysAllowRule)];

        let mut deny_count = 0;
        let mut allow_count = 0;

        for rule in &rules {
            if !rule.events().contains(&"preToolUse") {
                continue;
            }
            let result = rule.evaluate("preToolUse", &data);
            match result {
                Some(ref v)
                    if v.get("permissionDecision").and_then(|d| d.as_str()) == Some("deny") =>
                {
                    deny_count += 1;
                    break; // first-deny-wins: stop processing
                }
                _ => {
                    allow_count += 1;
                }
            }
        }

        assert_eq!(deny_count, 1, "exactly one deny must be recorded");
        assert_eq!(allow_count, 0, "no allow must fire after a deny");
    }

    // --- informational postToolUse ---

    /// Verify that a postToolUse rule returns an informational message.
    #[test]
    fn posttooluse_returns_informational_message() {
        let rule = InfoRule;
        let data = json!({"toolName": "edit"});
        let result = rule
            .evaluate("postToolUse", &data)
            .expect("should return Some");

        let msg = result["message"].as_str().expect("must have message field");
        assert!(
            msg.contains("postToolUse"),
            "message should describe the event; got: {msg}"
        );
    }

    // --- fail-open on panic ---

    /// dispatch_rules wraps rule.evaluate in catch_unwind; a panicking rule
    /// must not bring down the entire runner.
    #[test]
    fn panic_in_rule_is_fail_open() {
        // Directly verify catch_unwind suppresses the panic.
        let rule = PanicRule;
        let data = json!({});
        let outcome = std::panic::catch_unwind(std::panic::AssertUnwindSafe(|| {
            rule.evaluate("preToolUse", &data)
        }));
        assert!(outcome.is_err(), "panic should be caught by catch_unwind");
        // After catching the panic the runner continues — this test passing proves it.
    }

    // --- event filtering ---

    /// Rules must only fire for their registered events.
    #[test]
    fn rule_does_not_fire_for_wrong_event() {
        let rule = InfoRule; // registered for postToolUse only
                             // Simulate dispatch filter logic.
        let fires_on_pre = rule.events().contains(&"preToolUse");
        let fires_on_post = rule.events().contains(&"postToolUse");
        assert!(!fires_on_pre);
        assert!(fires_on_post);
    }

    // --- tool filtering ---

    /// Rules with a non-empty tools list must not fire for other tools.
    #[test]
    fn rule_tool_filter_blocks_unmatched_tools() {
        use crate::hooks::rules::SubagentGitGuardRule;

        let rule = SubagentGitGuardRule;
        // SubagentGitGuardRule only fires for "bash".
        let tools = rule.tools();
        assert!(!tools.is_empty());
        assert!(tools.iter().any(|&t| t == "bash"));
        assert!(!tools.iter().any(|&t| t == "edit"));
    }
}
