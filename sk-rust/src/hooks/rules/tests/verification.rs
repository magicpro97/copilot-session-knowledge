use super::*;

// --- VerificationGatePostRule ---

#[test]
fn verif_gate_post_never_denies() {
    let rule = VerificationGatePostRule;
    let data = json!({
        "toolName": "bash",
        "toolArgs": {"command": "python3 test_security.py"},
        "toolResult": {"exitCode": 0, "output": "All tests passed"}
    });
    if let Some(v) = rule.evaluate("postToolUse", &data) {
        assert!(
            v.get("permissionDecision").is_none(),
            "VerificationGatePostRule must NEVER deny; got: {v}"
        );
    }
}

#[test]
fn verif_gate_post_returns_none_for_non_bash() {
    let rule = VerificationGatePostRule;
    let data = json!({"toolName": "edit"});
    assert!(
        rule.evaluate("postToolUse", &data).is_none(),
        "non-bash tool must return None"
    );
}

#[test]
fn verif_gate_post_returns_none_on_failure_output() {
    // When toolResult shows failure, evidence must NOT be recorded.
    let rule = VerificationGatePostRule;
    let data = json!({
        "toolName": "bash",
        "toolArgs": {"command": "python3 test_security.py"},
        "toolResult": {"exitCode": 1, "output": "FAILED: 3 errors"}
    });
    // Rule should return None (no evidence recorded on failure).
    assert!(
        rule.evaluate("postToolUse", &data).is_none(),
        "failed command must return None (no evidence recorded)"
    );
}

#[test]
fn extract_written_paths_detects_open_in_heredoc() {
    let command = "python3 - <<'PY'\nwith open('/repo/file.py', 'w') as f:\n    f.write('x')\nPY";
    let paths = extract_written_paths_simple(command);
    assert!(
        paths.iter().any(|p| p == "/repo/file.py"),
        "must extract open(...) path from heredoc; got: {paths:?}"
    );
}

#[test]
fn extract_written_paths_detects_sed_in_place_target() {
    let command = "sed -i 's/x/y/' browse-ui/src/app.tsx";
    let paths = extract_written_paths_simple(command);
    assert!(
        paths.iter().any(|p| p == "browse-ui/src/app.tsx"),
        "must extract sed -i target path; got: {paths:?}"
    );
}

#[test]
fn extract_written_paths_detects_tee_target() {
    let command = "echo hi | tee -a browse-ui/src/output.ts";
    let paths = extract_written_paths_simple(command);
    assert!(
        paths.iter().any(|p| p == "browse-ui/src/output.ts"),
        "must extract tee target path; got: {paths:?}"
    );
}

#[test]
fn verif_gate_post_fires_on_posttooluse_bash_only() {
    let rule = VerificationGatePostRule;
    assert!(rule.events().contains(&"postToolUse"));
    assert!(!rule.events().contains(&"preToolUse"));
    assert!(rule.tools().contains(&"bash"));
    assert!(!rule.tools().contains(&"edit"));
}

#[test]
fn evidence_from_command_detects_py_tests() {
    assert!(evidence_from_command("python3 test_security.py").contains(&EV_PY_TESTS));
    assert!(evidence_from_command("python3 test_fixes.py").contains(&EV_PY_TESTS));
    assert!(evidence_from_command("python3 run_all_tests.py").contains(&EV_PY_TESTS));
    assert!(evidence_from_command("pytest --tb=short").contains(&EV_PY_TESTS));
    assert!(!evidence_from_command("cargo test").contains(&EV_PY_TESTS));
}

#[test]
fn evidence_from_command_detects_pnpm_checks() {
    assert!(evidence_from_command("pnpm format:check").contains(&EV_UI_FORMAT));
    assert!(evidence_from_command("cd browse-ui && pnpm lint").contains(&EV_UI_LINT));
    assert!(evidence_from_command("pnpm typecheck").contains(&EV_UI_TYPECHECK));
    assert!(evidence_from_command("pnpm build").contains(&EV_UI_BUILD));
    assert!(!evidence_from_command("pnpm install").contains(&EV_PY_TESTS));
}

#[test]
fn looks_successful_fail_open_when_absent() {
    // No toolResult → assume success (fail-open).
    let data = json!({"toolName": "bash"});
    assert!(
        looks_successful(&data),
        "absent toolResult must be treated as success"
    );
}

#[test]
fn looks_successful_detects_exit_code_nonzero() {
    let data = json!({"toolResult": {"exitCode": 1, "output": ""}});
    assert!(
        !looks_successful(&data),
        "non-zero exitCode must be detected as failure"
    );
}

#[test]
fn looks_successful_exit_code_zero_passes() {
    let data = json!({"toolResult": {"exitCode": 0, "output": "all good"}});
    assert!(
        looks_successful(&data),
        "exit code 0 must be treated as success"
    );
}

#[test]
fn looks_successful_detects_failed_in_output() {
    let data = json!({"toolResult": "FAILED: 3 test(s) failed"});
    assert!(
        !looks_successful(&data),
        "FAILED in output must be detected"
    );
}

#[test]
fn looks_successful_detects_counted_failure_patterns() {
    for output in [
        "failed: 3",
        "Errors: 1",
        "error TS1234",
        "Exit status 2",
        "3 failed",
        "1 failure",
    ] {
        let data = json!({"toolResult": output});
        assert!(
            !looks_successful(&data),
            "counted failure pattern must be detected: {output}"
        );
    }
}

#[test]
fn looks_successful_ignores_zero_count_patterns() {
    for output in ["Failed: 0", "Errors: 0", "0 failed"] {
        let data = json!({"toolResult": output});
        assert!(
            looks_successful(&data),
            "zero-count pattern must not be treated as failure: {output}"
        );
    }
}

#[test]
fn surfaces_from_path_detects_py_surface() {
    assert!(surfaces_from_path("hooks/rules/edit_tracker.py").contains(&SURFACE_PY));
    assert!(!surfaces_from_path("src/main.rs").contains(&SURFACE_PY));
}

#[test]
fn surfaces_from_path_detects_ui_surface() {
    assert!(surfaces_from_path("browse-ui/src/App.tsx").contains(&SURFACE_UI));
    assert!(surfaces_from_path("browse-ui/src/utils.js").contains(&SURFACE_UI));
    assert!(!surfaces_from_path("src/main.ts").contains(&SURFACE_UI));
}

#[test]
fn ledger_write_read_round_trip() {
    // Test that write_ledger / read_ledger preserve dirty and evidence sets.
    // We can't override markers_dir() in unit tests, so test the JSON payload
    // parsing logic directly.
    let parse_payload = |s: &str| -> Option<(HashSet<String>, HashSet<String>)> {
        let val: serde_json::Value = serde_json::from_str(s).ok()?;
        let dirty: HashSet<String> = val
            .get("dirty")?
            .as_array()?
            .iter()
            .filter_map(|v| v.as_str().map(|s| s.to_string()))
            .collect();
        let evidence: HashSet<String> = val
            .get("evidence")?
            .as_array()?
            .iter()
            .filter_map(|v| v.as_str().map(|s| s.to_string()))
            .collect();
        Some((dirty, evidence))
    };

    // Build a payload manually.
    let dirty: HashSet<String> = vec!["py".to_string(), "ui".to_string()]
        .into_iter()
        .collect();
    let evidence: HashSet<String> = vec!["py_tests".to_string()].into_iter().collect();

    let mut dirty_sorted: Vec<&str> = dirty.iter().map(|s| s.as_str()).collect();
    dirty_sorted.sort_unstable();
    let mut ev_sorted: Vec<&str> = evidence.iter().map(|s| s.as_str()).collect();
    ev_sorted.sort_unstable();

    let payload = format!(
        "{{\"dirty\":[{}],\"evidence\":[{}]}}",
        dirty_sorted
            .iter()
            .map(|s| format!("\"{}\"", s))
            .collect::<Vec<_>>()
            .join(","),
        ev_sorted
            .iter()
            .map(|s| format!("\"{}\"", s))
            .collect::<Vec<_>>()
            .join(","),
    );

    // Verify payload parses correctly.
    let (d, e) = parse_payload(&payload).expect("must parse");
    assert!(d.contains("py"), "dirty must contain 'py'");
    assert!(d.contains("ui"), "dirty must contain 'ui'");
    assert!(e.contains("py_tests"), "evidence must contain 'py_tests'");
}

#[test]
fn ledger_payload_format_matches_python_compact_json() {
    // Python: json.dumps({"dirty": ["py"], "evidence": ["py_tests"]},
    //                    separators=(",", ":"), sort_keys=True)
    // → '{"dirty":["py"],"evidence":["py_tests"]}'
    let mut dirty_sorted = ["py"];
    dirty_sorted.sort_unstable();
    let mut ev_sorted = ["py_tests"];
    ev_sorted.sort_unstable();

    let payload = format!(
        "{{\"dirty\":[{}],\"evidence\":[{}]}}",
        dirty_sorted
            .iter()
            .map(|s| format!("\"{}\"", s))
            .collect::<Vec<_>>()
            .join(","),
        ev_sorted
            .iter()
            .map(|s| format!("\"{}\"", s))
            .collect::<Vec<_>>()
            .join(","),
    );
    assert_eq!(
        payload, "{\"dirty\":[\"py\"],\"evidence\":[\"py_tests\"]}",
        "payload format must match Python compact JSON"
    );
}

// --- VerificationGatePreRule (wave8) ---

#[test]
fn verif_gate_pre_fires_on_pretooluse_only() {
    let rule = VerificationGatePreRule;
    assert!(rule.events().contains(&"preToolUse"));
    assert!(!rule.events().contains(&"postToolUse"));
}

#[test]
fn verif_gate_pre_applies_to_edit_create_bash_task_complete() {
    let rule = VerificationGatePreRule;
    assert!(rule.tools().contains(&"edit"));
    assert!(rule.tools().contains(&"create"));
    assert!(rule.tools().contains(&"bash"));
    assert!(rule.tools().contains(&"task_complete"));
}

#[test]
fn verif_gate_pre_always_allows_edit() {
    // Editing never blocked — even when dirty ledger exists (fail-open for edits).
    let rule = VerificationGatePreRule;
    let data = json!({
        "toolName": "edit",
        "toolArgs": {"path": "hooks/rules/edit_tracker.py", "old_str": "x", "new_str": "y"}
    });
    // Must return None (allow) — edits are never blocked.
    assert!(
        rule.evaluate("preToolUse", &data).is_none(),
        "edit must always return None (never blocked)"
    );
}

#[test]
fn verif_gate_pre_always_allows_create() {
    let rule = VerificationGatePreRule;
    let data = json!({
        "toolName": "create",
        "toolArgs": {"path": "src/new_module.py", "file_text": "pass"}
    });
    assert!(
        rule.evaluate("preToolUse", &data).is_none(),
        "create must always return None (never blocked)"
    );
}

#[test]
fn verif_gate_pre_fail_open_on_missing_tool_args() {
    let rule = VerificationGatePreRule;
    // task_complete with no toolArgs → fail-open.
    let data = json!({"toolName": "task_complete"});
    // When ledger is clean (no dirty), this returns None (allow).
    // If ledger happens to be dirty in CI, the rule may deny, but
    // it must not panic.
    let _ = rule.evaluate("preToolUse", &data); // must not panic
}

#[test]
fn verif_gate_pre_fail_open_empty_payload() {
    let rule = VerificationGatePreRule;
    let data = json!({});
    // Unknown toolName → falls through cleanly.
    let result = rule.evaluate("preToolUse", &data);
    // Should return None (unknown tool → no closeout action).
    assert!(
        result.is_none(),
        "empty payload must fail-open (None); got: {result:?}"
    );
}

#[test]
fn verif_gate_pre_non_closeout_bash_returns_none() {
    let rule = VerificationGatePreRule;
    let data = json!({
        "toolName": "bash",
        "toolArgs": {"command": "cargo test --quiet"}
    });
    // Not a closeout action → None.
    assert!(
        rule.evaluate("preToolUse", &data).is_none(),
        "non-closeout bash must return None"
    );
}

#[test]
fn verif_gate_pre_name_is_verification_gate_pre() {
    assert_eq!(VerificationGatePreRule.name(), "verification-gate-pre");
}

/// Verify the is_closeout_action helper recognises all closeout patterns.
#[test]
fn is_closeout_action_detects_task_complete() {
    let (ok, desc) = is_closeout_action("task_complete", "");
    assert!(ok, "task_complete must be a closeout");
    assert_eq!(desc, "task_complete");
}

#[test]
fn is_closeout_action_detects_gh_issue_close() {
    let (ok, desc) = is_closeout_action("bash", "gh issue close 42");
    assert!(ok, "gh issue close must be a closeout");
    assert_eq!(desc, "gh issue close");
}

#[test]
fn is_closeout_action_detects_gh_issue_comment() {
    let (ok, desc) = is_closeout_action("bash", "gh issue comment 12 --body 'done'");
    assert!(ok, "gh issue comment must be a closeout");
    assert_eq!(desc, "gh issue comment");
}

#[test]
fn is_closeout_action_detects_gh_pr_merge() {
    let (ok, desc) = is_closeout_action("bash", "gh pr merge 5 --squash");
    assert!(ok, "gh pr merge must be a closeout");
    assert_eq!(desc, "gh pr merge");
}

#[test]
fn is_closeout_action_detects_tentacle_handoff_done() {
    let (ok, desc) = is_closeout_action(
        "bash",
        "python tentacle.py handoff rust-wave8 'done' --status DONE",
    );
    assert!(ok, "tentacle handoff --status DONE must be a closeout");
    assert_eq!(desc, "tentacle handoff --status DONE");
}

#[test]
fn is_closeout_action_detects_sk_tentacle_complete() {
    let (ok, desc) = is_closeout_action("bash", "sk tentacle complete rust-wave8");
    assert!(ok, "sk tentacle complete must be a closeout");
    assert_eq!(desc, "tentacle complete");
}

#[test]
fn is_closeout_action_rejects_non_closeout_bash() {
    let (ok, _) = is_closeout_action("bash", "cargo test");
    assert!(!ok, "cargo test must not be a closeout");

    let (ok, _) = is_closeout_action("bash", "git status");
    assert!(!ok, "git status must not be a closeout");

    let (ok, _) = is_closeout_action("bash", "echo sigh issue close");
    assert!(!ok, "substring-only gh matches must not be closeouts");

    let (ok, _) = is_closeout_action("bash", "gh issue disclose 12");
    assert!(!ok, "substring-only close matches must not be closeouts");
}

#[test]
fn is_closeout_action_rejects_non_bash_non_task_complete() {
    let (ok, _) = is_closeout_action("edit", "");
    assert!(!ok, "edit tool must not be a closeout");

    let (ok, _) = is_closeout_action("view", "");
    assert!(!ok, "view tool must not be a closeout");
}
