use super::*;

#[test]
fn all_rules_includes_enforce_briefing() {
    let rules = all_rules();
    assert!(
        rules.iter().any(|r| r.name() == "enforce-briefing"),
        "all_rules must include enforce-briefing (wave11)"
    );
}

#[test]
fn all_rules_includes_enforce_learn() {
    let rules = all_rules();
    assert!(
        rules.iter().any(|r| r.name() == "enforce-learn"),
        "all_rules must include enforce-learn (wave11)"
    );
}

#[test]
fn all_rules_includes_learn_reminder() {
    let rules = all_rules();
    assert!(
        rules.iter().any(|r| r.name() == "learn-reminder"),
        "all_rules must include learn-reminder"
    );
}

#[test]
fn all_rules_includes_test_reminder() {
    let rules = all_rules();
    assert!(
        rules.iter().any(|r| r.name() == "test-reminder"),
        "all_rules must include test-reminder"
    );
}

#[test]
fn all_rules_includes_nextjs_typecheck_reminder() {
    let rules = all_rules();
    assert!(
        rules
            .iter()
            .any(|r| r.name() == "nextjs-typecheck-reminder"),
        "all_rules must include nextjs-typecheck-reminder"
    );
}

#[test]
fn all_rules_includes_pnpm_lockfile_guard() {
    let rules = all_rules();
    assert!(
        rules.iter().any(|r| r.name() == "pnpm-lockfile-guard"),
        "all_rules must include pnpm-lockfile-guard"
    );
}

#[test]
fn all_rules_includes_read_before_edit() {
    let rules = all_rules();
    assert!(
        rules.iter().any(|r| r.name() == "read-before-edit"),
        "all_rules must include read-before-edit"
    );
}

#[test]
fn all_rules_includes_verification_gate_post() {
    let rules = all_rules();
    assert!(
        rules.iter().any(|r| r.name() == "verification-gate-post"),
        "all_rules must include verification-gate-post"
    );
}

#[test]
fn all_rules_enforce_briefing_before_subagent_git_guard() {
    let rules = all_rules();
    let sgg = rules.iter().position(|r| r.name() == "subagent-git-guard");
    let eb = rules.iter().position(|r| r.name() == "enforce-briefing");
    assert!(
        sgg.is_some() && eb.is_some(),
        "both subagent-git-guard and enforce-briefing must be registered"
    );
    assert!(
        eb.unwrap() < sgg.unwrap(),
        "enforce-briefing must precede subagent-git-guard to match Python dispatch order"
    );
}

#[test]
fn all_rules_enforce_learn_after_enforce_briefing() {
    let rules = all_rules();
    let eb = rules.iter().position(|r| r.name() == "enforce-briefing");
    let el = rules.iter().position(|r| r.name() == "enforce-learn");
    assert!(
        eb.is_some() && el.is_some(),
        "both enforce-briefing and enforce-learn must be registered"
    );
    assert!(
        el.unwrap() > eb.unwrap(),
        "enforce-learn must follow enforce-briefing in dispatch order"
    );
}

#[test]
fn all_rules_enforce_learn_before_subagent_git_guard() {
    let rules = all_rules();
    let el = rules.iter().position(|r| r.name() == "enforce-learn");
    let sgg = rules.iter().position(|r| r.name() == "subagent-git-guard");
    assert!(
        el.is_some() && sgg.is_some(),
        "both enforce-learn and subagent-git-guard must be registered"
    );
    assert!(
        el.unwrap() < sgg.unwrap(),
        "enforce-learn must precede subagent-git-guard to match Python dispatch order"
    );
}

#[test]
fn all_rules_wave7_rules_never_deny_on_empty_payload() {
    let informational_names = ["read-before-edit", "verification-gate-post"];
    let data = json!({});
    let rules = all_rules();
    for rule in rules
        .iter()
        .filter(|r| informational_names.contains(&r.name()))
    {
        for event in &["preToolUse", "postToolUse"] {
            if let Some(result) = rule.evaluate(event, &data) {
                assert!(
                    result.get("permissionDecision").is_none(),
                    "rule '{}' on event '{}' must never produce permissionDecision; got: {result}",
                    rule.name(),
                    event
                );
            }
        }
    }
}

#[test]
fn all_rules_new_rules_are_informational_only() {
    let informational_names = [
        "learn-reminder",
        "test-reminder",
        "nextjs-typecheck-reminder",
    ];
    let data = json!({});
    let rules = all_rules();
    for rule in rules
        .iter()
        .filter(|r| informational_names.contains(&r.name()))
    {
        if let Some(result) = rule.evaluate("postToolUse", &data) {
            assert!(
                result.get("permissionDecision").is_none(),
                "rule '{}' must never produce permissionDecision (informational-only)",
                rule.name()
            );
        }
    }
}

// ---------------------------------------------------------------------------
// Wave 2a: FileSizeAdvisoryRule + NewFileAdvisoryRule parity (issue #341)
// ---------------------------------------------------------------------------

#[test]
fn all_rules_includes_file_size_advisory() {
    let rules = all_rules();
    assert!(
        rules.iter().any(|r| r.name() == "file-size-advisory"),
        "all_rules must include file-size-advisory (issue #341 parity)"
    );
}

#[test]
fn all_rules_includes_new_file_advisory() {
    let rules = all_rules();
    assert!(
        rules.iter().any(|r| r.name() == "new-file-advisory"),
        "all_rules must include new-file-advisory (issue #341 parity)"
    );
}

#[test]
fn advisory_rules_never_deny_on_empty_payload() {
    // FileSizeAdvisoryRule and NewFileAdvisoryRule must be purely informational.
    let advisory_names = ["file-size-advisory", "new-file-advisory"];
    let data = json!({});
    let rules = all_rules();
    for rule in rules.iter().filter(|r| advisory_names.contains(&r.name())) {
        for event in &["preToolUse"] {
            if let Some(result) = rule.evaluate(event, &data) {
                assert!(
                    result.get("permissionDecision").is_none(),
                    "advisory rule '{}' on event '{}' must never produce permissionDecision; got: {result}",
                    rule.name(),
                    event
                );
            }
        }
    }
}

#[test]
fn advisory_rules_before_track_edits() {
    // Both advisory rules should come before track-edits in dispatch order so
    // the agent sees the advisory before changes are tracked.
    let rules = all_rules();
    let fsa = rules.iter().position(|r| r.name() == "file-size-advisory");
    let nfa = rules.iter().position(|r| r.name() == "new-file-advisory");
    let te = rules.iter().position(|r| r.name() == "track-edits");
    assert!(fsa.is_some(), "file-size-advisory must be registered");
    assert!(nfa.is_some(), "new-file-advisory must be registered");
    if let (Some(fsa_pos), Some(te_pos)) = (fsa, te) {
        assert!(
            fsa_pos < te_pos,
            "file-size-advisory must precede track-edits"
        );
    }
    if let (Some(nfa_pos), Some(te_pos)) = (nfa, te) {
        assert!(
            nfa_pos < te_pos,
            "new-file-advisory must precede track-edits"
        );
    }
}
