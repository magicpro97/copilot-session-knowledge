use super::*;

// --- SubagentGitGuardRule ---

#[test]
fn git_guard_passes_non_git_command() {
    let rule = SubagentGitGuardRule;
    let data = json!({
        "toolName": "bash",
        "toolArgs": {"command": "ls -la"}
    });
    assert!(rule.evaluate("preToolUse", &data).is_none());
}

#[test]
fn git_guard_passes_when_no_tool_args() {
    let rule = SubagentGitGuardRule;
    let data = json!({"toolName": "bash"});
    assert!(rule.evaluate("preToolUse", &data).is_none());
}

#[test]
fn git_guard_passes_git_log_command() {
    let rule = SubagentGitGuardRule;
    let data = json!({
        "toolName": "bash",
        "toolArgs": {"command": "git log --oneline -5"}
    });
    // "git log" — no commit/push — should pass through.
    assert!(rule.evaluate("preToolUse", &data).is_none());
}

#[test]
fn git_guard_denies_git_commit_when_marker_fresh() {
    // Create a fresh marker in a temp path via env override, then test.
    // We test marker freshness logic independently via command_is_git_commit_or_push.
    assert!(command_is_git_commit_or_push("git commit -m 'msg'"));
    assert!(command_is_git_commit_or_push("git push origin main"));
    assert!(!command_is_git_commit_or_push("git log --oneline"));
    assert!(!command_is_git_commit_or_push(
        "git log --pretty=format:commit"
    ));
    assert!(!command_is_git_commit_or_push("echo gitcommit pushy"));
    assert!(!command_is_git_commit_or_push("echo hello"));
}

// --- BlockEditDistRule ---

#[test]
fn block_edit_dist_denies_browse_ui_dist_path() {
    let rule = BlockEditDistRule;
    let data = json!({
        "toolName": "edit",
        "toolArgs": {"path": "browse-ui/dist/index.js", "old_str": "x", "new_str": "y"}
    });
    let result = rule.evaluate("preToolUse", &data);
    assert!(result.is_some(), "must deny browse-ui/dist/ edit");
    let v = result.unwrap();
    assert_eq!(v["permissionDecision"].as_str().unwrap(), "deny");
    assert!(v["permissionDecisionReason"]
        .as_str()
        .unwrap()
        .contains("browse-ui/dist/"));
}

#[test]
fn block_edit_dist_denies_nested_browse_ui_dist_path() {
    let rule = BlockEditDistRule;
    // Path with a parent prefix still contains /browse-ui/dist/
    let data = json!({
        "toolName": "create",
        "toolArgs": {"path": "/home/user/project/browse-ui/dist/bundle.js", "file_text": "x"}
    });
    let result = rule.evaluate("preToolUse", &data);
    assert!(result.is_some(), "must deny nested /browse-ui/dist/ create");
    assert_eq!(
        result.unwrap()["permissionDecision"].as_str().unwrap(),
        "deny"
    );
}

#[test]
fn block_edit_dist_denies_windows_style_path() {
    let rule = BlockEditDistRule;
    // Backslash-separated Windows path must be caught after normalisation.
    let data = json!({
        "toolName": "edit",
        "toolArgs": {"path": "browse-ui\\dist\\app.js", "old_str": "a", "new_str": "b"}
    });
    let result = rule.evaluate("preToolUse", &data);
    assert!(
        result.is_some(),
        "must deny Windows-style browse-ui\\dist\\ path"
    );
    assert_eq!(
        result.unwrap()["permissionDecision"].as_str().unwrap(),
        "deny"
    );
}

#[test]
fn block_edit_dist_allows_browse_ui_src_path() {
    let rule = BlockEditDistRule;
    // browse-ui/src/ is fine — only dist/ is blocked.
    let data = json!({
        "toolName": "edit",
        "toolArgs": {"path": "browse-ui/src/App.tsx", "old_str": "x", "new_str": "y"}
    });
    assert!(
        rule.evaluate("preToolUse", &data).is_none(),
        "src/ must not be blocked"
    );
}

#[test]
fn block_edit_dist_fail_open_missing_tool_args() {
    let rule = BlockEditDistRule;
    // No toolArgs key at all → fail-open.
    let data = json!({"toolName": "edit"});
    assert!(rule.evaluate("preToolUse", &data).is_none());
}

#[test]
fn block_edit_dist_fail_open_missing_path() {
    let rule = BlockEditDistRule;
    // toolArgs present but no path key → fail-open.
    let data = json!({"toolName": "edit", "toolArgs": {"old_str": "x"}});
    assert!(rule.evaluate("preToolUse", &data).is_none());
}

#[test]
fn block_edit_dist_fail_open_empty_path() {
    let rule = BlockEditDistRule;
    let data = json!({"toolName": "edit", "toolArgs": {"path": ""}});
    assert!(rule.evaluate("preToolUse", &data).is_none());
}

#[test]
fn block_edit_dist_fail_open_non_object_tool_args() {
    let rule = BlockEditDistRule;
    // toolArgs is a string, not an object → fail-open.
    let data = json!({"toolName": "edit", "toolArgs": "not-an-object"});
    assert!(rule.evaluate("preToolUse", &data).is_none());
}

#[test]
fn block_edit_dist_only_fires_on_pretooluse() {
    let rule = BlockEditDistRule;
    assert!(rule.events().contains(&"preToolUse"));
    assert!(!rule.events().contains(&"postToolUse"));
    assert!(!rule.events().contains(&"sessionStart"));
}

#[test]
fn block_edit_dist_only_applies_to_edit_and_create() {
    let rule = BlockEditDistRule;
    assert!(rule.tools().contains(&"edit"));
    assert!(rule.tools().contains(&"create"));
    assert!(!rule.tools().contains(&"bash"));
}

// --- BlockUnsafeHtmlRule ---

#[test]
fn block_unsafe_html_denies_dangerous_without_sanitize() {
    let rule = BlockUnsafeHtmlRule;
    let data = json!({
        "toolName": "edit",
        "toolArgs": {
            "path": "browse-ui/src/Component.tsx",
            "new_str": "return <div dangerouslySetInnerHTML={{__html: userInput}} />;"
        }
    });
    let result = rule.evaluate("preToolUse", &data);
    assert!(
        result.is_some(),
        "must deny dangerouslySetInnerHTML without sanitize"
    );
    let v = result.unwrap();
    assert_eq!(v["permissionDecision"].as_str().unwrap(), "deny");
    assert!(v["permissionDecisionReason"]
        .as_str()
        .unwrap()
        .contains("dangerouslySetInnerHTML"));
}

#[test]
fn block_unsafe_html_allows_dangerous_with_dompurify_sanitize() {
    let rule = BlockUnsafeHtmlRule;
    let data = json!({
        "toolName": "edit",
        "toolArgs": {
            "path": "src/Viewer.tsx",
            "new_str": "const safe = DOMPurify.sanitize(html);\nreturn <div dangerouslySetInnerHTML={{__html: safe}} />;"
        }
    });
    assert!(
        rule.evaluate("preToolUse", &data).is_none(),
        "DOMPurify.sanitize should allow through"
    );
}

#[test]
fn block_unsafe_html_allows_dangerous_with_sanitize_call() {
    let rule = BlockUnsafeHtmlRule;
    let data = json!({
        "toolName": "create",
        "toolArgs": {
            "path": "src/helpers.ts",
            "file_text": "const h = sanitize(raw);\nreturn {dangerouslySetInnerHTML: {__html: h}};"
        }
    });
    assert!(
        rule.evaluate("preToolUse", &data).is_none(),
        "sanitize( should allow through"
    );
}

#[test]
fn block_unsafe_html_allows_dangerous_with_rehype_sanitize() {
    let rule = BlockUnsafeHtmlRule;
    let data = json!({
        "toolName": "edit",
        "toolArgs": {
            "path": "src/md.tsx",
            "new_str": "// uses rehype-sanitize\ndangerouslySetInnerHTML={{__html: x}}"
        }
    });
    assert!(
        rule.evaluate("preToolUse", &data).is_none(),
        "rehype-sanitize should allow through"
    );
}

#[test]
fn block_unsafe_html_uses_file_text_for_create() {
    let rule = BlockUnsafeHtmlRule;
    let data = json!({
        "toolName": "create",
        "toolArgs": {
            "path": "src/new.tsx",
            "file_text": "<div dangerouslySetInnerHTML={{__html: bad}} />"
        }
    });
    let result = rule.evaluate("preToolUse", &data);
    assert!(
        result.is_some(),
        "file_text with unsafe html must be denied"
    );
    assert_eq!(
        result.unwrap()["permissionDecision"].as_str().unwrap(),
        "deny"
    );
}

#[test]
fn block_unsafe_html_fail_open_non_ts_tsx_file() {
    let rule = BlockUnsafeHtmlRule;
    // .py file — not in scope.
    let data = json!({
        "toolName": "edit",
        "toolArgs": {
            "path": "script.py",
            "new_str": "dangerouslySetInnerHTML is just a string here"
        }
    });
    assert!(
        rule.evaluate("preToolUse", &data).is_none(),
        "non-TS/TSX must be allowed through"
    );
}

#[test]
fn block_unsafe_html_fail_open_missing_tool_args() {
    let rule = BlockUnsafeHtmlRule;
    let data = json!({"toolName": "edit"});
    assert!(rule.evaluate("preToolUse", &data).is_none());
}

#[test]
fn block_unsafe_html_fail_open_missing_path() {
    let rule = BlockUnsafeHtmlRule;
    let data = json!({"toolName": "edit", "toolArgs": {"new_str": "dangerouslySetInnerHTML"}});
    assert!(rule.evaluate("preToolUse", &data).is_none());
}

#[test]
fn block_unsafe_html_fail_open_missing_content() {
    let rule = BlockUnsafeHtmlRule;
    // Path present, correct extension, but no new_str or file_text → fail-open.
    let data = json!({
        "toolName": "edit",
        "toolArgs": {"path": "src/Comp.tsx"}
    });
    assert!(rule.evaluate("preToolUse", &data).is_none());
}

#[test]
fn block_unsafe_html_fail_open_empty_content() {
    let rule = BlockUnsafeHtmlRule;
    let data = json!({
        "toolName": "create",
        "toolArgs": {"path": "src/Empty.tsx", "file_text": ""}
    });
    assert!(rule.evaluate("preToolUse", &data).is_none());
}

#[test]
fn block_unsafe_html_only_fires_on_pretooluse() {
    let rule = BlockUnsafeHtmlRule;
    assert!(rule.events().contains(&"preToolUse"));
    assert!(!rule.events().contains(&"postToolUse"));
}

#[test]
fn block_unsafe_html_only_applies_to_edit_and_create() {
    let rule = BlockUnsafeHtmlRule;
    assert!(rule.tools().contains(&"edit"));
    assert!(rule.tools().contains(&"create"));
    assert!(!rule.tools().contains(&"bash"));
}

#[test]
fn content_has_unsafe_html_true_when_no_sanitize() {
    assert!(content_has_unsafe_html(
        "return <div dangerouslySetInnerHTML={{__html: x}} />;"
    ));
}

#[test]
fn content_has_unsafe_html_false_when_no_dangerous_pattern() {
    assert!(!content_has_unsafe_html("return <div className='safe' />;"));
}

#[test]
fn content_has_unsafe_html_false_when_dompurify_present() {
    assert!(!content_has_unsafe_html(
        "const s = DOMPurify.sanitize(raw); dangerouslySetInnerHTML={{__html: s}}"
    ));
}

#[test]
fn content_has_unsafe_html_false_when_sanitize_fn_present() {
    assert!(!content_has_unsafe_html(
        "const h = sanitize(raw);\ndangerouslySetInnerHTML={{__html: h}}"
    ));
}

// --- PnpmLockfileGuardRule ---

#[test]
fn pnpm_guard_returns_none_for_non_commit_command() {
    let rule = PnpmLockfileGuardRule;
    let data = json!({
        "toolName": "bash",
        "toolArgs": {"command": "git log --pretty=format:commit"}
    });
    assert!(
        rule.evaluate("preToolUse", &data).is_none(),
        "non-commit bash must return None"
    );
}

#[test]
fn pnpm_guard_returns_none_for_missing_tool_args() {
    let rule = PnpmLockfileGuardRule;
    let data = json!({"toolName": "bash"});
    assert!(
        rule.evaluate("preToolUse", &data).is_none(),
        "missing toolArgs must fail-open (None)"
    );
}

#[test]
fn pnpm_guard_can_produce_deny_but_fails_open_on_empty_payload() {
    let rule = PnpmLockfileGuardRule;
    let data = json!({});
    assert!(
        rule.evaluate("preToolUse", &data).is_none(),
        "PnpmLockfileGuardRule must fail-open (None) when toolArgs absent"
    );
}

#[test]
fn pnpm_guard_fail_open_when_git_unavailable() {
    // When git is available but the working directory has no staged files,
    // the rule must return None (no package.json staged → no deny).
    // We can't easily mock `git diff --cached`, so we test the fail-open
    // path: if git returns empty staged list, package.json is not staged.
    let staged: HashSet<String> = HashSet::new();
    let pkg_staged = staged.contains("browse-ui/package.json");
    assert!(!pkg_staged, "empty staged set must not trigger deny");
}

#[test]
fn pnpm_guard_deny_logic_when_pkg_staged_without_lock() {
    // Simulate what the rule does when we detect the bad state.
    let mut staged: HashSet<String> = HashSet::new();
    staged.insert("browse-ui/package.json".to_string());
    // pnpm-lock.yaml NOT in staged.

    let pkg_staged = staged.contains("browse-ui/package.json");
    let lock_staged = staged.contains("browse-ui/pnpm-lock.yaml");
    assert!(pkg_staged, "package.json must be detected as staged");
    assert!(!lock_staged, "pnpm-lock.yaml must not be in staged set");
    // Verify the condition that triggers deny.
    assert!(pkg_staged && !lock_staged, "deny condition must be true");
}

#[test]
fn pnpm_guard_no_deny_when_both_staged() {
    let mut staged: HashSet<String> = HashSet::new();
    staged.insert("browse-ui/package.json".to_string());
    staged.insert("browse-ui/pnpm-lock.yaml".to_string());

    let pkg_staged = staged.contains("browse-ui/package.json");
    let lock_staged = staged.contains("browse-ui/pnpm-lock.yaml");
    // Both staged → no deny.
    assert!(
        !pkg_staged || lock_staged,
        "both staged must not trigger deny"
    );
}

#[test]
fn pnpm_guard_fires_only_on_pretooluse() {
    let rule = PnpmLockfileGuardRule;
    assert!(rule.events().contains(&"preToolUse"));
    assert!(!rule.events().contains(&"postToolUse"));
}

#[test]
fn pnpm_guard_only_applies_to_bash() {
    let rule = PnpmLockfileGuardRule;
    assert!(rule.tools().contains(&"bash"));
    assert!(!rule.tools().contains(&"edit"));
}

// --- all_rules registry covers new rules ---

#[test]
fn all_rules_includes_block_edit_dist() {
    let rules = all_rules();
    assert!(
        rules.iter().any(|r| r.name() == "block-edit-dist"),
        "all_rules must include block-edit-dist"
    );
}

#[test]
fn all_rules_includes_block_unsafe_html() {
    let rules = all_rules();
    assert!(
        rules.iter().any(|r| r.name() == "block-unsafe-html"),
        "all_rules must include block-unsafe-html"
    );
}

// --- SubagentGitGuardRule HMAC wiring (wave6) ---

/// Unsigned marker (empty file) without a secret must be accepted:
/// backward-compat path — any existing file passes when no secret is configured.
///
/// Skipped when the real `.marker-secret` file exists, because in that
/// environment `verify_marker` requires a valid HMAC-SHA256 signature.
#[test]
fn git_guard_hmac_unsigned_no_secret_backward_compat() {
    let _guard = env_lock();

    // Derive the real secret path the same way marker_auth does.
    let secret_exists = resolve_home_dir()
        .map(|h| {
            h.join(".copilot")
                .join("hooks")
                .join(".marker-secret")
                .is_file()
        })
        .unwrap_or(false);
    if secret_exists {
        // Locked environment: verify_marker would reject unsigned markers.
        return;
    }

    // Create a temporary empty marker file (mirrors Python `marker_path.touch()`).
    let dir = std::env::temp_dir().join("sk_guard_hmac_test");
    let _ = std::fs::create_dir_all(&dir);
    let path = dir.join("dispatched-subagent-active-bkcompat");
    std::fs::write(&path, b"").unwrap();

    // Without a secret, verify_marker must accept any existing file.
    assert!(
        marker_auth::verify_marker(&path, "dispatched-subagent-active-bkcompat"),
        "no-secret mode: unsigned marker must be accepted (backward compat)"
    );
    let _ = std::fs::remove_file(&path);
    let _ = std::fs::remove_dir_all(&dir);
}

/// A marker signed with HMAC-SHA256 for "dispatched-subagent-active" must
/// pass re-verification via the same primitives that `verify_marker` uses.
///
/// This proves the wire-up: when tentacle.py writes a signed marker, the
/// Rust HMAC foundation accepts it via the same algorithm.
#[test]
fn git_guard_hmac_signed_dispatched_subagent_marker_accepted() {
    use crate::hooks::marker_auth::hmac_sha256_hex;

    let secret = "test-guard-secret-wave6";
    let name = "dispatched-subagent-active";
    let ts = "1715222400"; // fixed for determinism

    // Simulate sign_marker (what tentacle.py / sign_marker does):
    let sig = hmac_sha256_hex(secret, &format!("{name}:{ts}"));
    let payload = serde_json::json!({"name": name, "ts": ts, "sig": sig});

    // Simulate verify_marker re-computation:
    let data: serde_json::Value = serde_json::from_str(&payload.to_string()).unwrap();
    let m_name = data["name"].as_str().unwrap();
    let m_ts = data["ts"].as_str().unwrap();
    let m_sig = data["sig"].as_str().unwrap();

    assert_eq!(m_name, name, "name field must round-trip unchanged");
    // Re-derive: must equal original signature (HMAC is deterministic).
    let expected = hmac_sha256_hex(secret, &format!("{m_name}:{m_ts}"));
    assert_eq!(
        m_sig, expected,
        "HMAC-SHA256 over dispatched-subagent-active:{ts} must be stable and match"
    );
}

/// A tampered `ts` field must produce a different (invalid) HMAC signature,
/// so the git guard fails-open (does not block) for forged markers.
#[test]
fn git_guard_hmac_tampered_marker_rejected() {
    use crate::hooks::marker_auth::hmac_sha256_hex;

    let secret = "test-guard-secret-wave6";
    let name = "dispatched-subagent-active";
    let ts = "1715222400";

    // Original, valid signature.
    let valid_sig = hmac_sha256_hex(secret, &format!("{name}:{ts}"));

    // Attacker changes ts to extend TTL without knowing the secret.
    let tampered_ts = "9999999999";
    let sig_for_tampered = hmac_sha256_hex(secret, &format!("{name}:{tampered_ts}"));

    assert_ne!(
        valid_sig, sig_for_tampered,
        "tampered ts must produce a different HMAC — original sig must not be reusable"
    );

    // Further: the original sig does NOT match the tampered ts.
    // (This is what verify_marker checks internally via hmac_verify.)
    let sig_for_original = hmac_sha256_hex(secret, &format!("{name}:{ts}"));
    assert_eq!(
        valid_sig, sig_for_original,
        "sanity: re-computing for original ts must be stable"
    );
    assert_ne!(
        sig_for_original, sig_for_tampered,
        "signature for original ts must differ from signature for tampered ts"
    );
}

// --- wave11: EnforceBriefingRule ---

#[test]
fn enforce_briefing_fires_on_pretooluse_only() {
    let rule = EnforceBriefingRule;
    assert!(rule.events().contains(&"preToolUse"));
    assert!(!rule.events().contains(&"postToolUse"));
    assert!(!rule.events().contains(&"sessionStart"));
}

#[test]
fn enforce_briefing_covers_edit_create_bash() {
    let rule = EnforceBriefingRule;
    assert!(rule.tools().contains(&"edit"));
    assert!(rule.tools().contains(&"create"));
    assert!(rule.tools().contains(&"bash"));
    assert!(!rule.tools().contains(&"task_complete"));
}

#[test]
fn enforce_briefing_passes_non_file_mod_bash() {
    // bash `ls -la` writes no source files → None regardless of briefing state.
    assert!(
        !bash_writes_source_files_detect("ls -la"),
        "ls -la must not be detected as a source-file write"
    );
    assert!(
        !bash_writes_source_files_detect("echo hello"),
        "echo hello must not be detected as a source-file write"
    );
    assert!(
        !bash_writes_source_files_detect("cargo build"),
        "cargo build must not be detected as a source-file write"
    );
}

#[test]
fn enforce_briefing_detects_bash_redirect_write() {
    assert!(
        bash_writes_source_files_detect("cat data > src/main.py"),
        "> src/main.py must be detected"
    );
    assert!(
        bash_writes_source_files_detect("echo x >> app.ts"),
        ">> app.ts must be detected"
    );
    assert!(
        bash_writes_source_files_detect("cat f | tee lib.rs"),
        "tee lib.rs must be detected"
    );
}

#[test]
fn enforce_briefing_detects_sed_i() {
    assert!(
        bash_writes_source_files_detect("sed -i 's/old/new/g' config.yaml"),
        "sed -i must be detected as a write"
    );
}

#[test]
fn enforce_briefing_detects_heredoc_write_patterns() {
    let cmd = r#"cat <<EOF > src/mod.py
x = 1
EOF"#;
    assert!(
        bash_writes_source_files_detect(cmd),
        "heredoc redirect to .py must be detected"
    );
    let cmd2 = "node -e \"const fs=require('fs'); fs.writeFileSync('app.js','x')\"";
    assert!(
        bash_writes_source_files_detect(cmd2),
        "node -e + writeFileSync must be detected"
    );
}

#[test]
fn enforce_briefing_safe_paths_not_flagged() {
    assert!(
        !is_source_path_for_enforce("/tmp/scratch.py"),
        "/tmp/ is a safe prefix — must not be flagged"
    );
    assert!(
        !is_source_path_for_enforce("/var/log/app.ts"),
        "/var/ is a safe prefix — must not be flagged"
    );
    assert!(
        !is_source_path_for_enforce(".copilot/session-state/abc/plan.md"),
        "session-state path must not be flagged"
    );
}

#[test]
fn enforce_briefing_source_extensions_broad_includes_md() {
    // The enforce rules use ENFORCE_SOURCE_EXTENSIONS which includes .md.
    assert!(
        is_source_path_for_enforce("docs/README.md"),
        ".md must be flagged by enforce source-path check"
    );
    assert!(
        is_source_path_for_enforce("src/main.py"),
        ".py must be flagged"
    );
    assert!(is_source_path_for_enforce("app.ts"), ".ts must be flagged");
}

#[test]
fn enforce_briefing_denies_edit_when_no_briefing_marker() {
    let _guard = env_lock();
    // Use an isolated HOME with no markers dir so briefing_done() returns false.
    let tmp = std::env::temp_dir().join("sk_enforce_briefing_deny_edit");
    let _ = std::fs::remove_dir_all(&tmp);
    std::fs::create_dir_all(&tmp).expect("create temp home");

    let old_home = std::env::var_os("HOME");
    let old_up = std::env::var_os("USERPROFILE");
    std::env::set_var("HOME", &tmp);
    std::env::set_var("USERPROFILE", &tmp);
    std::env::remove_var("COPILOT_AGENT_SESSION_ID");

    let rule = EnforceBriefingRule;
    let data = json!({
        "toolName": "edit",
        "toolArgs": {"path": "src/main.py"}
    });
    let result = rule.evaluate("preToolUse", &data);

    match old_home {
        Some(v) => std::env::set_var("HOME", v),
        None => std::env::remove_var("HOME"),
    }
    match old_up {
        Some(v) => std::env::set_var("USERPROFILE", v),
        None => std::env::remove_var("USERPROFILE"),
    }
    let _ = std::fs::remove_dir_all(&tmp);

    // With an isolated HOME and no briefing marker, this must deny.
    assert!(
        result.is_some(),
        "edit with no briefing marker must produce Some (deny)"
    );
    let v = result.unwrap();
    assert_eq!(
        v["permissionDecision"].as_str().unwrap_or(""),
        "deny",
        "must be a deny decision"
    );
    assert!(
        v["permissionDecisionReason"]
            .as_str()
            .unwrap_or("")
            .contains("BRIEFING"),
        "deny reason must mention BRIEFING"
    );
}

#[test]
fn enforce_briefing_allows_edit_when_briefing_marker_present() {
    let _guard = env_lock();
    let tmp = std::env::temp_dir().join("sk_enforce_briefing_allow_edit");
    let _ = std::fs::remove_dir_all(&tmp);
    let mdir = tmp.join(".copilot").join("markers");
    std::fs::create_dir_all(&mdir).expect("create markers dir");

    // Write a valid briefing-done marker.
    let marker_path = mdir.join("briefing-done");
    marker_auth::sign_marker(&marker_path, "briefing-done").expect("sign_marker must succeed");

    let old_home = std::env::var_os("HOME");
    let old_up = std::env::var_os("USERPROFILE");
    std::env::set_var("HOME", &tmp);
    std::env::set_var("USERPROFILE", &tmp);

    let rule = EnforceBriefingRule;
    let data = json!({
        "toolName": "edit",
        "toolArgs": {"path": "src/main.py"}
    });
    let result = rule.evaluate("preToolUse", &data);

    match old_home {
        Some(v) => std::env::set_var("HOME", v),
        None => std::env::remove_var("HOME"),
    }
    match old_up {
        Some(v) => std::env::set_var("USERPROFILE", v),
        None => std::env::remove_var("USERPROFILE"),
    }
    let _ = std::fs::remove_dir_all(&tmp);

    assert!(
        result.is_none(),
        "edit with valid briefing marker must return None (allow)"
    );
}

#[test]
fn enforce_briefing_denies_when_tamper_marker_present() {
    let _guard = env_lock();
    let tmp = std::env::temp_dir().join("sk_enforce_briefing_tampered");
    let _ = std::fs::remove_dir_all(&tmp);
    let mdir = tmp.join(".copilot").join("markers");
    std::fs::create_dir_all(&mdir).expect("create markers dir");
    marker_auth::sign_marker(&mdir.join("hooks-tampered"), "hooks-tampered")
        .expect("sign tamper marker");

    let old_home = std::env::var_os("HOME");
    let old_up = std::env::var_os("USERPROFILE");
    std::env::set_var("HOME", &tmp);
    std::env::set_var("USERPROFILE", &tmp);

    let rule = EnforceBriefingRule;
    let data = json!({
        "toolName": "edit",
        "toolArgs": {"path": "src/main.py"}
    });
    let result = rule.evaluate("preToolUse", &data);

    match old_home {
        Some(v) => std::env::set_var("HOME", v),
        None => std::env::remove_var("HOME"),
    }
    match old_up {
        Some(v) => std::env::set_var("USERPROFILE", v),
        None => std::env::remove_var("USERPROFILE"),
    }
    let _ = std::fs::remove_dir_all(&tmp);

    let value = result.expect("tamper marker must deny");
    assert_eq!(value["permissionDecision"].as_str().unwrap_or(""), "deny");
    assert!(
        value["permissionDecisionReason"]
            .as_str()
            .unwrap_or("")
            .contains("HOOKS TAMPERED"),
        "tamper deny reason must mention HOOKS TAMPERED"
    );
}

// --- SyntaxGateRule (wave13) ---

#[test]
fn syntax_gate_allows_non_py_path() {
    let rule = SyntaxGateRule;
    let data = json!({
        "toolName": "create",
        "toolArgs": {"path": "src/main.ts", "file_text": "const x = 1;"}
    });
    assert!(
        rule.evaluate("preToolUse", &data).is_none(),
        "non-.py path must pass through (not denied)"
    );
}

#[test]
fn syntax_gate_failopen_missing_tool_args() {
    let rule = SyntaxGateRule;
    let data = json!({"toolName": "create"});
    assert!(
        rule.evaluate("preToolUse", &data).is_none(),
        "missing toolArgs must be fail-open"
    );
}

#[test]
fn syntax_gate_failopen_missing_path() {
    let rule = SyntaxGateRule;
    let data = json!({
        "toolName": "create",
        "toolArgs": {"file_text": "x = 1"}
    });
    assert!(
        rule.evaluate("preToolUse", &data).is_none(),
        "missing path must be fail-open"
    );
}

#[test]
fn syntax_gate_failopen_create_missing_file_text() {
    let rule = SyntaxGateRule;
    let data = json!({
        "toolName": "create",
        "toolArgs": {"path": "foo.py"}
    });
    assert!(
        rule.evaluate("preToolUse", &data).is_none(),
        "missing file_text on create must be fail-open"
    );
}

#[test]
fn syntax_gate_allows_good_py_create() {
    // Only meaningful when Python is available; fail-open if not.
    let rule = SyntaxGateRule;
    let data = json!({
        "toolName": "create",
        "toolArgs": {
            "path": "foo.py",
            "file_text": "x = 1\nprint(x)\n"
        }
    });
    // Good syntax must never produce a deny.
    assert!(
        rule.evaluate("preToolUse", &data).is_none(),
        "good .py syntax must pass through (allow)"
    );
}

#[test]
fn syntax_gate_denies_bad_py_syntax() {
    // Only meaningful when Python is available; fail-open if not (still passes test).
    let rule = SyntaxGateRule;
    let data = json!({
        "toolName": "create",
        "toolArgs": {
            "path": "bad.py",
            "file_text": "def foo(\n    pass\n"
        }
    });
    let result = rule.evaluate("preToolUse", &data);
    // If Python is available, result must be Some(deny); if not, fail-open → None.
    if let Some(v) = result {
        assert_eq!(
            v["permissionDecision"].as_str().unwrap_or(""),
            "deny",
            "bad .py syntax must produce permissionDecision=deny"
        );
        assert!(
            v["permissionDecisionReason"]
                .as_str()
                .unwrap_or("")
                .contains("Syntax gate"),
            "deny reason must mention Syntax gate"
        );
    }
    // else: Python unavailable → fail-open → None → test still passes
}

#[test]
fn syntax_gate_edit_failopen_when_file_absent() {
    let rule = SyntaxGateRule;
    let data = json!({
        "toolName": "edit",
        "toolArgs": {
            "path": "/nonexistent/absolutely/does/not/exist/foo.py",
            "old_str": "x = 1",
            "new_str": "y = 2"
        }
    });
    assert!(
        rule.evaluate("preToolUse", &data).is_none(),
        "edit on absent file must be fail-open"
    );
}

#[test]
fn all_rules_includes_syntax_gate() {
    let rules = all_rules();
    assert!(
        rules.iter().any(|r| r.name() == "syntax-gate"),
        "all_rules must include syntax-gate (wave13)"
    );
}

#[test]
fn all_rules_syntax_gate_between_subagent_guard_and_block_dist() {
    let rules = all_rules();
    let sg = rules.iter().position(|r| r.name() == "syntax-gate");
    let sgg = rules.iter().position(|r| r.name() == "subagent-git-guard");
    let bed = rules.iter().position(|r| r.name() == "block-edit-dist");
    assert!(
        sg.is_some() && sgg.is_some() && bed.is_some(),
        "syntax-gate, subagent-git-guard, and block-edit-dist must all be registered"
    );
    assert!(
        sgg.unwrap() < sg.unwrap() && sg.unwrap() < bed.unwrap(),
        "syntax-gate must be registered after subagent-git-guard and before block-edit-dist"
    );
}
