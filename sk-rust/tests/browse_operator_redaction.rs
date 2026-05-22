//! Integration tests for operator debug-log redaction (issue #451 PR-B).
//!
//! Covers all acceptance criteria from the PR spec.

#![cfg(feature = "browse-server")]

use serde_json::{json, Value};
use sk::browse::operator::redaction::redact_entry;

// ── Helper ────────────────────────────────────────────────────────────────────

fn minimal_valid() -> Value {
    json!({
        "idx": 1,
        "timestamp": "2024-01-15T12:00:00Z",
        "kind": "generic",
        "level": "info",
        "source": "cli",
        "attrs": {}
    })
}

fn redacted_flag(v: &Value) -> bool {
    v["redacted"].as_bool().unwrap_or(false)
}

fn message_str(v: &Value) -> &str {
    v["message"].as_str().unwrap_or("")
}

// ── 1. Bearer token in message → [REDACTED] ───────────────────────────────────

#[test]
fn test_bearer_token_redacted() {
    let entry = json!({
        "idx": 1,
        "kind": "generic",
        "level": "info",
        "source": "cli",
        "message": "Authorization: Bearer eyABC123secret",
        "attrs": {}
    });
    let out = redact_entry(&entry);
    assert!(
        message_str(&out).contains("[REDACTED]"),
        "Bearer token should be redacted"
    );
    assert!(!message_str(&out).contains("eyABC123secret"));
    assert!(redacted_flag(&out));
}

// ── 2. JWT in message → [REDACTED] ────────────────────────────────────────────

#[test]
fn test_jwt_redacted() {
    let entry = json!({
        "idx": 2,
        "kind": "generic",
        "level": "info",
        "source": "cli",
        "message": "token: eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiJ1c2VyIn0.SflKxwRJSMeKKF2QT4fwpMeJf36POk6yJV_adQssw5c",
        "attrs": {}
    });
    let out = redact_entry(&entry);
    assert!(
        message_str(&out).contains("[REDACTED]"),
        "JWT should be redacted"
    );
    assert!(!message_str(&out).contains("eyJhbGciOiJIUzI1NiJ9"));
    assert!(redacted_flag(&out));
}

// ── 3. URL query tokens redacted ──────────────────────────────────────────────

#[test]
fn test_url_query_token_redacted() {
    for msg in &[
        "https://example.com/api?token=abc123",
        "https://example.com/api?key=abc123",
        "https://example.com/api?secret=abc123",
        "https://example.com/api?auth=abc123",
    ] {
        let entry = json!({
            "idx": 3,
            "kind": "generic",
            "level": "info",
            "source": "cli",
            "message": msg,
            "attrs": {}
        });
        let out = redact_entry(&entry);
        assert!(
            message_str(&out).contains("[REDACTED]"),
            "URL query token should be redacted in: {msg}"
        );
        assert!(
            !message_str(&out).contains("abc123"),
            "Token value leaked in: {msg}"
        );
        assert!(redacted_flag(&out));
    }
}

// ── 4. Windows path username redacted ─────────────────────────────────────────

#[test]
fn test_windows_path_username_redacted() {
    let entry = json!({
        "idx": 4,
        "kind": "generic",
        "level": "info",
        "source": "cli",
        "message": r"Loading config from C:\Users\alice\.copilot\config.json",
        "attrs": {}
    });
    let out = redact_entry(&entry);
    assert!(
        message_str(&out).contains("[REDACTED]"),
        "Windows username should be redacted"
    );
    assert!(!message_str(&out).contains("alice"), "Username leaked");
    assert!(redacted_flag(&out));
}

// ── 5. Unix path username redacted ────────────────────────────────────────────

#[test]
fn test_unix_path_username_redacted() {
    let entry = json!({
        "idx": 5,
        "kind": "generic",
        "level": "info",
        "source": "cli",
        "message": "Loading config from /home/bob/.copilot/config.json",
        "attrs": {}
    });
    let out = redact_entry(&entry);
    assert!(
        message_str(&out).contains("[REDACTED]"),
        "Unix username should be redacted"
    );
    assert!(!message_str(&out).contains("bob"), "Username leaked");
    assert!(redacted_flag(&out));
}

// ── 6. macOS path username redacted ───────────────────────────────────────────

#[test]
fn test_macos_path_username_redacted() {
    let entry = json!({
        "idx": 6,
        "kind": "generic",
        "level": "info",
        "source": "cli",
        "message": "Loading config from /Users/carol/.copilot/config.json",
        "attrs": {}
    });
    let out = redact_entry(&entry);
    assert!(
        message_str(&out).contains("[REDACTED]"),
        "macOS username should be redacted"
    );
    assert!(!message_str(&out).contains("carol"), "Username leaked");
    assert!(redacted_flag(&out));
}

// ── 7. Unknown top-level keys dropped + redacted: true ────────────────────────

#[test]
fn test_unknown_top_level_keys_dropped() {
    let entry = json!({
        "idx": 7,
        "kind": "generic",
        "level": "info",
        "source": "cli",
        "attrs": {},
        "secret_field": "should_be_dropped",
        "another_unknown": 42
    });
    let out = redact_entry(&entry);
    assert!(
        out.get("secret_field").is_none(),
        "Unknown key should be dropped"
    );
    assert!(
        out.get("another_unknown").is_none(),
        "Unknown key should be dropped"
    );
    assert!(redacted_flag(&out));
}

// ── 8. Unknown attrs keys dropped + redacted: true ────────────────────────────

#[test]
fn test_unknown_attrs_keys_dropped() {
    let entry = json!({
        "idx": 8,
        "kind": "generic",
        "level": "info",
        "source": "cli",
        "attrs": {
            "model": "claude-3",
            "evil_key": "should_be_dropped"
        }
    });
    let out = redact_entry(&entry);
    assert!(
        out["attrs"].get("evil_key").is_none(),
        "Unknown attrs key should be dropped"
    );
    assert_eq!(out["attrs"]["model"], "claude-3");
    assert!(redacted_flag(&out));
}

// ── 9. Non-object attrs → {} + redacted: true ────────────────────────────────

#[test]
fn test_non_object_attrs_replaced() {
    for bad_attrs in &[json!("a string"), json!(42), json!(true), json!([1, 2, 3])] {
        let entry = json!({
            "idx": 9,
            "kind": "generic",
            "level": "info",
            "source": "cli",
            "attrs": bad_attrs
        });
        let out = redact_entry(&entry);
        assert_eq!(
            out["attrs"],
            json!({}),
            "Non-object attrs should become {{}}"
        );
        assert!(
            redacted_flag(&out),
            "Non-object attrs should set redacted=true"
        );
    }
}

// ── 10. Message > 2048 chars truncated ────────────────────────────────────────

#[test]
fn test_message_truncated_at_2048() {
    let long_msg: String = "x".repeat(3000);
    let entry = json!({
        "idx": 10,
        "kind": "generic",
        "level": "info",
        "source": "cli",
        "message": long_msg,
        "attrs": {}
    });
    let out = redact_entry(&entry);
    let msg = message_str(&out);
    assert!(
        msg.len() <= 2048,
        "Message should be truncated to 2048 chars, got {}",
        msg.len()
    );
    assert!(redacted_flag(&out));
}

// ── 11. Route with ? in attrs rejected ────────────────────────────────────────

#[test]
fn test_route_with_query_string_rejected() {
    let entry = json!({
        "idx": 11,
        "kind": "generic",
        "level": "info",
        "source": "cli",
        "attrs": {
            "route": "/api/sessions?token=abc"
        }
    });
    let out = redact_entry(&entry);
    assert!(
        out["attrs"].get("route").is_none(),
        "Route with ? should be dropped"
    );
    assert!(redacted_flag(&out));
}

// ── 12. Invalid kind/level/source/status replaced + redacted: true ────────────

#[test]
fn test_invalid_kind_replaced() {
    let entry = json!({
        "idx": 12,
        "kind": "malicious_kind",
        "level": "info",
        "source": "cli",
        "attrs": {}
    });
    let out = redact_entry(&entry);
    assert_eq!(out["kind"], "generic", "Invalid kind should become generic");
    assert!(redacted_flag(&out));
}

#[test]
fn test_invalid_level_replaced() {
    let entry = json!({
        "idx": 12,
        "kind": "generic",
        "level": "critical",
        "source": "cli",
        "attrs": {}
    });
    let out = redact_entry(&entry);
    assert_eq!(
        out["level"],
        Value::Null,
        "Invalid level should become null"
    );
    assert!(redacted_flag(&out));
}

#[test]
fn test_invalid_source_replaced() {
    let entry = json!({
        "idx": 12,
        "kind": "generic",
        "level": "info",
        "source": "attacker_source",
        "attrs": {}
    });
    let out = redact_entry(&entry);
    assert_eq!(
        out["source"], "unknown",
        "Invalid source should become unknown"
    );
    assert!(redacted_flag(&out));
}

#[test]
fn test_invalid_status_dropped() {
    let entry = json!({
        "idx": 12,
        "kind": "generic",
        "level": "info",
        "source": "cli",
        "status": "pending",
        "attrs": {}
    });
    let out = redact_entry(&entry);
    assert!(
        out.get("status").is_none(),
        "Invalid status should be dropped"
    );
    assert!(redacted_flag(&out));
}

// ── 13. Invalid tool_name (fails regex) dropped ───────────────────────────────

#[test]
fn test_invalid_tool_name_dropped() {
    for bad_name in &["has spaces!", "../traversal", "a".repeat(65).as_str(), ""] {
        let entry = json!({
            "idx": 13,
            "kind": "tool_call",
            "level": "info",
            "source": "cli",
            "tool_name": bad_name,
            "attrs": {}
        });
        let out = redact_entry(&entry);
        assert!(
            out.get("tool_name").is_none(),
            "Invalid tool_name '{bad_name}' should be dropped"
        );
        assert!(redacted_flag(&out));
    }
}

#[test]
fn test_valid_tool_name_kept() {
    let entry = json!({
        "idx": 13,
        "kind": "tool_call",
        "level": "info",
        "source": "cli",
        "tool_name": "bash_exec",
        "attrs": {}
    });
    let out = redact_entry(&entry);
    assert_eq!(out["tool_name"], "bash_exec");
}

// ── 14. Invalid span_id dropped ───────────────────────────────────────────────

#[test]
fn test_invalid_span_id_dropped() {
    for bad_span in &[
        "notenoughchars",
        "UPPERCASE1234567",
        "too-long-span-id-value",
    ] {
        let entry = json!({
            "idx": 14,
            "kind": "generic",
            "level": "info",
            "source": "cli",
            "span_id": bad_span,
            "parent_span_id": bad_span,
            "attrs": {}
        });
        let out = redact_entry(&entry);
        assert!(
            out.get("span_id").is_none(),
            "Invalid span_id '{bad_span}' should be dropped"
        );
        assert!(
            out.get("parent_span_id").is_none(),
            "Invalid parent_span_id '{bad_span}' should be dropped"
        );
        assert!(redacted_flag(&out));
    }
}

#[test]
fn test_valid_span_id_kept() {
    let entry = json!({
        "idx": 14,
        "kind": "generic",
        "level": "info",
        "source": "cli",
        "span_id": "abcdef1234567890",
        "attrs": {}
    });
    let out = redact_entry(&entry);
    assert_eq!(out["span_id"], "abcdef1234567890");
}

// ── 15. Invalid timestamp dropped ────────────────────────────────────────────

#[test]
fn test_invalid_timestamp_replaced_with_null() {
    // The ISO UTC regex validates format only, not calendar correctness.
    // Only structurally malformed timestamps are rejected.
    for bad_ts in &["not-a-date", "2024-01-01", "12345678"] {
        let entry = json!({
            "idx": 15,
            "kind": "generic",
            "level": "info",
            "source": "cli",
            "timestamp": bad_ts,
            "attrs": {}
        });
        let out = redact_entry(&entry);
        assert_eq!(
            out["timestamp"],
            Value::Null,
            "Invalid timestamp '{bad_ts}' should become null"
        );
        assert!(redacted_flag(&out));
    }
}

#[test]
fn test_valid_timestamp_kept() {
    let entry = json!({
        "idx": 15,
        "kind": "generic",
        "level": "info",
        "source": "cli",
        "timestamp": "2024-01-15T12:00:00Z",
        "attrs": {}
    });
    let out = redact_entry(&entry);
    assert_eq!(out["timestamp"], "2024-01-15T12:00:00Z");
}

// ── 16. Invalid session_uuid in attrs dropped ─────────────────────────────────

#[test]
fn test_invalid_session_uuid_dropped() {
    for bad_uuid in &["not-a-uuid", "12345678-1234-1234-1234-12345678901z", ""] {
        let entry = json!({
            "idx": 16,
            "kind": "generic",
            "level": "info",
            "source": "cli",
            "attrs": {
                "session_uuid": bad_uuid
            }
        });
        let out = redact_entry(&entry);
        assert!(
            out["attrs"].get("session_uuid").is_none(),
            "Invalid session_uuid '{bad_uuid}' should be dropped"
        );
        assert!(redacted_flag(&out));
    }
}

#[test]
fn test_valid_session_uuid_kept() {
    let entry = json!({
        "idx": 16,
        "kind": "generic",
        "level": "info",
        "source": "cli",
        "attrs": {
            "session_uuid": "12345678-1234-1234-1234-123456789abc"
        }
    });
    let out = redact_entry(&entry);
    assert_eq!(
        out["attrs"]["session_uuid"],
        "12345678-1234-1234-1234-123456789abc"
    );
}

// ── 17. Empty/null input → minimal output without panic ──────────────────────

#[test]
fn test_null_input_no_panic() {
    let out = redact_entry(&Value::Null);
    // Should return a valid JSON object with at least idx, kind, source, attrs, redacted
    assert!(out.is_object());
    assert!(out.get("idx").is_some());
    assert!(out.get("kind").is_some());
    assert!(out.get("attrs").is_some());
    assert!(out.get("redacted").is_some());
}

#[test]
fn test_empty_object_no_panic() {
    let out = redact_entry(&json!({}));
    assert!(out.is_object());
    assert_eq!(out["kind"], "generic");
    assert_eq!(out["source"], "unknown");
    assert_eq!(out["attrs"], json!({}));
    // Empty object: idx missing → redacted
    assert!(redacted_flag(&out));
}

// ── 18. Deep nested attacker payload → no panic, no leak ─────────────────────

#[test]
fn test_deeply_nested_attrs_no_panic_no_leak() {
    let nested = json!({
        "level1": {
            "level2": {
                "level3": {
                    "level4": "deep_secret"
                }
            }
        }
    });
    let entry = json!({
        "idx": 18,
        "kind": "generic",
        "level": "info",
        "source": "cli",
        "attrs": nested
    });
    let out = redact_entry(&entry);
    // Should not panic, nested object attrs → {} + redacted
    assert!(out.is_object());
    assert_eq!(out["attrs"], json!({}));
    assert!(redacted_flag(&out));
    // No leaked nested content
    let out_str = out.to_string();
    assert!(!out_str.contains("deep_secret"), "Nested secret leaked");
}

#[test]
fn test_deeply_nested_message_no_leak() {
    // Non-string message (array of nested objects) → coerced to string, truncated, scrubbed
    let entry = json!({
        "idx": 18,
        "kind": "generic",
        "level": "info",
        "source": "cli",
        "message": {"nested": {"secret": "bearer abcxyz"}},
        "attrs": {}
    });
    // Should not panic
    let out = redact_entry(&entry);
    assert!(out.is_object());
    assert!(
        !out["message"].as_str().unwrap_or("").contains("abcxyz"),
        "Secret leaked from non-string message"
    );
}

// ── 19. PARITY-NOTE comment present in redaction.rs ──────────────────────────

#[test]
fn test_parity_note_comment_exists_in_source() {
    // This test reads the source file to verify the PARITY-NOTE comment is present.
    // It intentionally checks the documentation contract, not runtime behavior.
    let src = include_str!("../src/browse/operator/redaction.rs");
    assert!(
        src.contains("PARITY-NOTE"),
        "redaction.rs must contain a PARITY-NOTE comment near omitted redact_secrets"
    );
    assert!(
        src.contains("redact_secrets"),
        "redaction.rs must mention redact_secrets in the PARITY-NOTE"
    );
}

// ── Additional: valid clean entry passes through without redaction ────────────

#[test]
fn test_clean_entry_not_redacted() {
    let entry = minimal_valid();
    let out = redact_entry(&entry);
    assert!(!redacted_flag(&out), "Clean entry should not be redacted");
    assert_eq!(out["idx"], 1);
    assert_eq!(out["kind"], "generic");
    assert_eq!(out["level"], "info");
    assert_eq!(out["source"], "cli");
}

// ── Additional: absent attrs (no attrs key) → {} no redaction ────────────────

#[test]
fn test_absent_attrs_no_redaction() {
    let entry = json!({
        "idx": 0,
        "kind": "generic",
        "level": "info",
        "source": "cli"
    });
    let out = redact_entry(&entry);
    assert_eq!(out["attrs"], json!({}));
    // No unknown keys, attrs absent = not redacted by attrs
    // But idx=0 is valid (>=0), so overall should not be redacted
    assert!(!redacted_flag(&out));
}

// ── Additional: null attrs → {} no redaction ─────────────────────────────────

#[test]
fn test_null_attrs_no_redaction() {
    let entry = json!({
        "idx": 1,
        "kind": "generic",
        "level": "info",
        "source": "cli",
        "attrs": null
    });
    let out = redact_entry(&entry);
    assert_eq!(out["attrs"], json!({}));
    assert!(!redacted_flag(&out));
}

// ── Additional: valid attrs scalar values pass through ───────────────────────

#[test]
fn test_valid_attrs_pass_through() {
    let entry = json!({
        "idx": 1,
        "kind": "llm_request",
        "level": "info",
        "source": "cli",
        "attrs": {
            "model": "claude-sonnet-4.6",
            "tokens_in": 100,
            "tokens_out": 200,
            "cache_hit": false,
            "latency_ms": 350.5
        }
    });
    let out = redact_entry(&entry);
    assert_eq!(out["attrs"]["model"], "claude-sonnet-4.6");
    assert_eq!(out["attrs"]["tokens_in"], 100);
    assert_eq!(out["attrs"]["tokens_out"], 200);
    assert_eq!(out["attrs"]["cache_hit"], false);
    assert!(!redacted_flag(&out));
}

// ── Additional: nested object in attrs value rejected ────────────────────────

#[test]
fn test_nested_object_in_attrs_value_rejected() {
    let entry = json!({
        "idx": 1,
        "kind": "generic",
        "level": "info",
        "source": "cli",
        "attrs": {
            "model": {"nested": "value"}
        }
    });
    let out = redact_entry(&entry);
    assert!(
        out["attrs"].get("model").is_none(),
        "Nested attrs value should be dropped"
    );
    assert!(redacted_flag(&out));
}

// ── Additional: array in attrs value rejected ────────────────────────────────

#[test]
fn test_array_in_attrs_value_rejected() {
    let entry = json!({
        "idx": 1,
        "kind": "generic",
        "level": "info",
        "source": "cli",
        "attrs": {
            "tokens_in": [1, 2, 3]
        }
    });
    let out = redact_entry(&entry);
    assert!(
        out["attrs"].get("tokens_in").is_none(),
        "Array attrs value should be dropped"
    );
    assert!(redacted_flag(&out));
}
