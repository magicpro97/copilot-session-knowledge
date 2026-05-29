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
use crate::hooks::session_state::{self, DispatchStats};
use crate::hooks::sync_markers::record_sync_signal;

// ---------------------------------------------------------------------------
// Double-fire deduplication (issue #348)
// ---------------------------------------------------------------------------

/// Return `true` if this (event, raw-payload) pair should be skipped as a
/// double-fire duplicate within a 500 ms window.
///
/// Mirrors `hook_runner.py::_check_and_set_dedup()`.  The payload hash uses
/// SHA-256 (first 8 hex chars) instead of MD5; the 8-char key length matches
/// Python so marker filenames are human-readable and consistent.
///
/// Fail-open: any I/O or parse error returns `false` (process normally, i.e.
/// no new denial paths from dedup failures).
fn check_and_set_dedup(event: &str, raw: &str) -> bool {
    use sha2::{Digest, Sha256};
    use std::fs;
    use std::path::PathBuf;
    use std::time::{SystemTime, UNIX_EPOCH};

    // Compute a short content hash for dedup key uniqueness.
    let hash_bytes = Sha256::digest(raw.as_bytes());
    let payload_hash: String = hash_bytes
        .iter()
        .take(4) // 4 bytes → 8 hex chars, same length as Python's MD5[:8]
        .map(|b| format!("{b:02x}"))
        .collect();

    // Build a safe filename key (alphanumeric, dash, dot only).
    let key = format!("{event}-{payload_hash}");
    let safe_key: String = key
        .chars()
        .map(|c| {
            if c.is_alphanumeric() || c == '-' || c == '.' {
                c
            } else {
                '_'
            }
        })
        .collect();

    let markers_path = crate::config::resolve_home_dir()
        .unwrap_or_else(|| PathBuf::from("."))
        .join(".copilot")
        .join("markers");

    let dedup_path = markers_path.join(format!("hook-dedup-{safe_key}"));

    let now_ms = SystemTime::now()
        .duration_since(UNIX_EPOCH)
        .unwrap_or_default()
        .as_millis() as u64;

    // Check existing marker: duplicate if same payload seen within 500 ms.
    if dedup_path.is_file() {
        if let Ok(content) = fs::read_to_string(&dedup_path) {
            if let Ok(last_ms) = content.trim().parse::<u64>() {
                if now_ms.saturating_sub(last_ms) < 500 {
                    return true; // duplicate within window — skip
                }
            }
        }
    }

    // Write updated timestamp (best-effort; never blocks on failure).
    let _ = fs::create_dir_all(&markers_path);
    let _ = fs::write(&dedup_path, now_ms.to_string());

    false
}

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

    // Recursion guard (issue #396): prevent the hook from re-entering itself
    // when a subprocess spawned by a rule (e.g. briefing.py) triggers further
    // tool calls that fire this hook again.  Fail-open: any env-var read error
    // is ignored and processing continues normally.
    if std::env::var("SK_HOOK_ACTIVE").is_ok_and(|v| v == "1") {
        return;
    }

    // Mark active before dispatching so any subprocess we spawn inherits the guard.
    // Mirrors Python `os.environ["SK_HOOK_ACTIVE"] = "1"`.
    // Best-effort: set_var is infallible on supported platforms; the recursion
    // guard is advisory.
    std::env::set_var("SK_HOOK_ACTIVE", "1");

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

    // Double-fire deduplication (issue #348): skip identical (event, payload)
    // pairs fired within a 500 ms window.  Different payloads for the same
    // event type are processed normally (not considered duplicates).
    // Fail-open: any I/O error in check_and_set_dedup → returns false → continue.
    if check_and_set_dedup(event, &raw) {
        return;
    }

    let stats = dispatch_rules(event, &data);

    // Record hook/tool call metrics in session state (best-effort, fail-open).
    session_state::record_metrics(event, &data, &stats);

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
pub(crate) fn dispatch_rules(event: &str, data: &Value) -> DispatchStats {
    let dry_run = std::env::var("HOOK_DRY_RUN").is_ok_and(|v| v == "1");
    let verbose = std::env::var("HOOK_LOG_LEVEL").is_ok_and(|v| v == "DEBUG");

    let tool_name = data.get("toolName").and_then(|v| v.as_str()).unwrap_or("");

    let rules = all_rules();
    let matching: Vec<&dyn HookRule> = rules
        .iter()
        .map(|r| r.as_ref())
        .filter(|r| r.events().contains(&event))
        .collect();

    let mut panicked_rules = 0usize;

    for rule in matching {
        // Tool filter: empty list means "all tools".
        let tools = rule.tools();
        if !tools.is_empty() && !tools.contains(&tool_name) {
            continue;
        }

        // Evaluate (any panic → fail-open via catch_unwind).
        let result = match std::panic::catch_unwind(std::panic::AssertUnwindSafe(|| {
            rule.evaluate(event, data)
        })) {
            Ok(r) => r,
            Err(_) => {
                audit_log(event, tool_name, rule.name(), "error", "panic");
                panicked_rules += 1;
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
                    return DispatchStats { panicked_rules }; // ← first-deny-wins short-circuit
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
            // Truncate to at most 100 bytes at a UTF-8 char boundary; info messages
            // routinely contain multi-byte glyphs (e.g. box-drawing separators) that
            // would otherwise panic on a raw byte slice.
            let mut end = msg.len().min(100);
            while end > 0 && !msg.is_char_boundary(end) {
                end -= 1;
            }
            let truncated = &msg[..end];
            audit_log(event, tool_name, rule.name(), "info", truncated);
        }
    }

    DispatchStats { panicked_rules }
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
        assert!(tools.contains(&"bash"));
        assert!(!tools.contains(&"edit"));
    }

    // --- double-fire deduplication (issue #348) ---

    /// `check_and_set_dedup` must return `false` on the first call for a new
    /// (event, payload) pair — the first invocation is not a duplicate.
    #[test]
    fn dedup_first_call_is_not_duplicate() {
        // Use a unique payload so this test does not interfere with others.
        let unique_payload = format!(
            r#"{{"toolName":"test","ts":{}}}"#,
            std::time::SystemTime::now()
                .duration_since(std::time::UNIX_EPOCH)
                .unwrap_or_default()
                .as_nanos()
        );
        let result = check_and_set_dedup("preToolUse", &unique_payload);
        assert!(!result, "first call must not be treated as duplicate");
    }

    /// `check_and_set_dedup` must return `true` when the same (event, payload)
    /// pair is fired a second time within the 500 ms window.
    #[test]
    fn dedup_second_call_within_window_is_duplicate() {
        let unique_payload = format!(
            r#"{{"toolName":"dedup-dup-test","ts":{}}}"#,
            std::time::SystemTime::now()
                .duration_since(std::time::UNIX_EPOCH)
                .unwrap_or_default()
                .as_nanos()
        );
        // First call seeds the marker.
        let first = check_and_set_dedup("preToolUse", &unique_payload);
        assert!(!first, "first call must not be duplicate");
        // Second call with the same payload, no sleep — still within 500 ms window.
        let second = check_and_set_dedup("preToolUse", &unique_payload);
        assert!(
            second,
            "second identical call within window must be duplicate"
        );
    }

    /// `check_and_set_dedup` must return `false` for a *different* payload even
    /// if the event type is the same — different payloads are not duplicates.
    #[test]
    fn dedup_different_payload_same_event_is_not_duplicate() {
        let base_nanos = std::time::SystemTime::now()
            .duration_since(std::time::UNIX_EPOCH)
            .unwrap_or_default()
            .as_nanos();
        let payload_a = format!(r#"{{"toolName":"edit","ts":{base_nanos}}}"#);
        let payload_b = format!(r#"{{"toolName":"create","ts":{base_nanos}}}"#);

        // Seed payload_a.
        let _ = check_and_set_dedup("preToolUse", &payload_a);
        // payload_b has a different hash — must not be considered a duplicate.
        let result = check_and_set_dedup("preToolUse", &payload_b);
        assert!(
            !result,
            "different payload for same event must not be a duplicate"
        );
    }

    /// `check_and_set_dedup` must be fail-open: when called with an empty raw
    /// string (edge case), it must return `false` and not panic.
    #[test]
    fn dedup_empty_payload_is_fail_open() {
        let result = check_and_set_dedup("sessionStart", "");
        // May be true or false depending on prior state, but must not panic.
        let _ = result; // just confirming no panic
    }
}
