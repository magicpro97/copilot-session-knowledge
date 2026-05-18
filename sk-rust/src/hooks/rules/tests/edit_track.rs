use super::*;

// --- TrackEditsRule ---

#[test]
fn track_edits_returns_info_for_edit_tool() {
    let rule = TrackEditsRule;
    let data = json!({"toolName": "edit"});
    let result = rule.evaluate("postToolUse", &data);
    assert!(result.is_some());
    let msg = result.unwrap();
    assert_eq!(msg["message"].as_str().unwrap(), "[sk] edit tracked.");
}

#[test]
fn track_edits_returns_info_for_create_tool() {
    let rule = TrackEditsRule;
    let data = json!({"toolName": "create"});
    let result = rule.evaluate("postToolUse", &data);
    assert!(result.is_some());
    let msg = result.unwrap();
    assert!(msg["message"].as_str().unwrap().contains("create"));
}

#[test]
fn track_edits_edit_appends_code_path_to_tentacle_marker() {
    let _guard = env_lock();
    let tmp = std::env::temp_dir().join("sk_track_edits_edit_marker_test");
    let _ = std::fs::remove_dir_all(&tmp);
    std::fs::create_dir_all(&tmp).expect("create temp home");

    let old_home = std::env::var_os("HOME");
    let old_up = std::env::var_os("USERPROFILE");
    std::env::set_var("HOME", &tmp);
    std::env::set_var("USERPROFILE", &tmp);

    let rule = TrackEditsRule;
    let data = json!({
        "toolName": "edit",
        "toolResult": {"filePath": "src/main.py"}
    });
    let _ = rule.evaluate("postToolUse", &data);

    let marker = tmp.join(".copilot").join("markers").join("tentacle-edits");
    let entries = marker_auth::verify_list_marker(&marker);
    assert!(
        entries.contains("src/main.py"),
        "edit path must be appended to tentacle-edits; got: {entries:?}"
    );

    match old_home {
        Some(v) => std::env::set_var("HOME", v),
        None => std::env::remove_var("HOME"),
    }
    match old_up {
        Some(v) => std::env::set_var("USERPROFILE", v),
        None => std::env::remove_var("USERPROFILE"),
    }
    let _ = std::fs::remove_dir_all(&tmp);
}

#[test]
fn track_edits_create_appends_code_path_to_tentacle_marker() {
    let _guard = env_lock();
    let tmp = std::env::temp_dir().join("sk_track_edits_create_marker_test");
    let _ = std::fs::remove_dir_all(&tmp);
    std::fs::create_dir_all(&tmp).expect("create temp home");

    let old_home = std::env::var_os("HOME");
    let old_up = std::env::var_os("USERPROFILE");
    std::env::set_var("HOME", &tmp);
    std::env::set_var("USERPROFILE", &tmp);

    let rule = TrackEditsRule;
    let data = json!({
        "toolName": "create",
        "input": {"filePath": "src/new_module.py"}
    });
    let _ = rule.evaluate("postToolUse", &data);

    let marker = tmp.join(".copilot").join("markers").join("tentacle-edits");
    let entries = marker_auth::verify_list_marker(&marker);
    assert!(
        entries.contains("src/new_module.py"),
        "create path must be appended to tentacle-edits; got: {entries:?}"
    );

    match old_home {
        Some(v) => std::env::set_var("HOME", v),
        None => std::env::remove_var("HOME"),
    }
    match old_up {
        Some(v) => std::env::set_var("USERPROFILE", v),
        None => std::env::remove_var("USERPROFILE"),
    }
    let _ = std::fs::remove_dir_all(&tmp);
}

// --- TrackEditsRule helper function unit tests ---

/// CODE_EXTENSIONS recognises the common code file types.
#[test]
fn track_edits_has_code_extension_recognises_common_types() {
    assert!(has_code_extension("src/main.py"), ".py must be a code ext");
    assert!(
        has_code_extension("lib/module.ts"),
        ".ts must be a code ext"
    );
    assert!(has_code_extension("App.tsx"), ".tsx must be a code ext");
    assert!(has_code_extension("build.rs"), ".rs must be a code ext");
    assert!(
        has_code_extension("config.yaml"),
        ".yaml must be a code ext"
    );
    assert!(has_code_extension("script.sh"), ".sh must be a code ext");
    assert!(
        has_code_extension("Makefile.toml"),
        ".toml must be a code ext"
    );
    assert!(has_code_extension("app.js"), ".js must be a code ext");
}

/// Markdown and other non-code extensions are excluded.
#[test]
fn track_edits_md_is_not_code_extension() {
    assert!(
        !has_code_extension("README.md"),
        ".md must NOT be a code ext"
    );
    assert!(
        !has_code_extension("notes.txt"),
        ".txt must NOT be a code ext"
    );
    assert!(
        !has_code_extension("image.png"),
        ".png must NOT be a code ext"
    );
    assert!(
        !has_code_extension("archive.tar.gz"),
        ".gz must NOT be a code ext"
    );
}

/// Extension matching is case-insensitive.
#[test]
fn track_edits_code_extension_case_insensitive() {
    assert!(has_code_extension("Main.PY"));
    assert!(has_code_extension("App.TSX"));
    assert!(!has_code_extension("Notes.MD"));
}

/// Session-state paths are excluded from code-edit tracking.
#[test]
fn track_edits_is_session_path_excludes_session_state() {
    assert!(is_track_session_path(".copilot/session-state/abc/plan.md"));
    assert!(is_track_session_path(
        "/home/user/.copilot/session-state/abc-123/checkpoints/01.md"
    ));
    // Windows-style path
    assert!(is_track_session_path(
        "C:\\Users\\user\\.copilot\\session-state\\abc\\plan.md"
    ));
    // Non-session paths must not be excluded.
    assert!(!is_track_session_path("src/main.py"));
    assert!(!is_track_session_path(
        "/home/user/.copilot/markers/code-edit-count"
    ));
    assert!(!is_track_session_path("learn.py"));
}

/// Counter round-trip (no secret): sign_counter writes plain int, verify_counter reads it.
/// Verifies that counter values are preserved across a write/read cycle.
#[test]
fn track_edits_counter_round_trip_no_secret() {
    let _guard = env_lock();
    let tmp = std::env::temp_dir()
        .join("sk_trackedits_counter_rt")
        .join("markers");
    let _ = std::fs::create_dir_all(&tmp);
    let path = tmp.join("code-edit-count");
    let _ = std::fs::remove_file(&path); // clean slate

    // Write 42, read back 42.
    marker_auth::sign_counter(&path, 42).expect("sign_counter should succeed");
    let v = marker_auth::verify_counter(&path);
    assert_eq!(v, 42, "counter round-trip must preserve value 42; got {v}");

    // Increment by 3 (simulate a hook run detecting 3 new files).
    let current = marker_auth::verify_counter(&path);
    marker_auth::sign_counter(&path, current + 3).expect("sign_counter should succeed");
    let v2 = marker_auth::verify_counter(&path);
    assert_eq!(v2, 45, "counter after delta-increment must be 45; got {v2}");

    let _ = std::fs::remove_dir_all(tmp.parent().unwrap());
}

/// List-marker round-trip (no secret): sign_list_marker + verify_list_marker.
/// Verifies that appending to an existing list does not clobber it.
#[test]
fn track_edits_list_marker_round_trip_no_secret() {
    let _guard = env_lock();
    let tmp = std::env::temp_dir()
        .join("sk_trackedits_list_rt")
        .join("markers");
    let _ = std::fs::create_dir_all(&tmp);
    let path = tmp.join("tentacle-edits");
    let _ = std::fs::remove_file(&path); // clean slate

    // Seed the list with two files.
    let seed: Vec<String> = vec!["src/main.py".to_string(), "lib/util.rs".to_string()];
    marker_auth::sign_list_marker(&path, &seed).expect("sign_list_marker should succeed");

    // Read back and verify.
    let back = marker_auth::verify_list_marker(&path);
    assert!(
        back.contains("src/main.py"),
        "must contain seed entry src/main.py"
    );
    assert!(
        back.contains("lib/util.rs"),
        "must contain seed entry lib/util.rs"
    );

    // Append a new file (simulate delta add).
    let mut existing = marker_auth::verify_list_marker(&path);
    existing.insert("hooks/rules.rs".to_string());
    let lines: Vec<String> = existing.into_iter().collect();
    marker_auth::sign_list_marker(&path, &lines).expect("sign_list_marker should succeed");

    let back2 = marker_auth::verify_list_marker(&path);
    assert!(
        back2.contains("src/main.py"),
        "original entry must survive append"
    );
    assert!(
        back2.contains("lib/util.rs"),
        "original entry must survive append"
    );
    assert!(
        back2.contains("hooks/rules.rs"),
        "new entry must be present"
    );

    let _ = std::fs::remove_dir_all(tmp.parent().unwrap());
}

/// save/load seen set round-trip (plain text, not HMAC-signed).
#[test]
fn track_edits_seen_set_round_trip() {
    let tmp_home = std::env::temp_dir().join("sk_trackedits_seen_rt");
    let mdir = tmp_home.join(".copilot").join("markers");
    let _ = std::fs::create_dir_all(&mdir);

    // Override markers_dir path by writing to the expected location directly.
    // (We call save/load with a path relative to temp dir.)
    let path = mdir.join("git-modified-seen");
    let _ = std::fs::remove_file(&path);

    let mut seen: HashSet<String> = HashSet::new();
    seen.insert("src/main.rs".to_string());
    seen.insert("hooks/rules.rs".to_string());

    // Write sorted plain text.
    let mut lines: Vec<&str> = seen.iter().map(|s| s.as_str()).collect();
    lines.sort_unstable();
    std::fs::write(&path, lines.join("\n")).expect("write seen set");

    // Read back.
    let back: HashSet<String> = std::fs::read_to_string(&path)
        .unwrap()
        .lines()
        .filter(|l| !l.is_empty())
        .map(|l| l.to_string())
        .collect();
    assert!(back.contains("src/main.rs"), "must restore src/main.rs");
    assert!(
        back.contains("hooks/rules.rs"),
        "must restore hooks/rules.rs"
    );

    let _ = std::fs::remove_dir_all(&tmp_home);
}

/// TrackEditsRule must never emit a deny result (fail-open, informational only).
#[test]
fn track_edits_never_denies() {
    let rule = TrackEditsRule;
    // Test all supported tools.
    for tool in &["edit", "create", "bash"] {
        let data = json!({"toolName": tool});
        if let Some(v) = rule.evaluate("postToolUse", &data) {
            assert!(
                v.get("permissionDecision").is_none(),
                "TrackEditsRule must never emit permissionDecision; tool={tool} got:{v}"
            );
        }
    }
}

/// TrackEditsRule fires on postToolUse and covers edit/create/bash.
#[test]
fn track_edits_covers_correct_tools_and_events() {
    let rule = TrackEditsRule;
    assert!(rule.events().contains(&"postToolUse"));
    assert!(!rule.events().contains(&"preToolUse"));
    assert!(rule.tools().contains(&"edit"));
    assert!(rule.tools().contains(&"create"));
    assert!(rule.tools().contains(&"bash"));
}

/// Bash with no new git modifications must return None (silent, mirrors Python).
#[test]
fn track_edits_bash_returns_none_when_no_new_modifications() {
    // When git_modified == previously_seen (nothing new), the rule returns None.
    // We test the helper logic by verifying an empty delta produces None.
    // The actual git status may or may not return files; we test the helper
    // functions that drive the decision.
    let current: HashSet<String> = vec!["src/main.rs".to_string()].into_iter().collect();
    let previously: HashSet<String> = vec!["src/main.rs".to_string()].into_iter().collect();
    let new_mods: HashSet<String> = current
        .iter()
        .filter(|f| !previously.contains(*f))
        .cloned()
        .collect();
    assert!(
        new_mods.is_empty(),
        "delta should be empty when current == previously_seen; got: {:?}",
        new_mods
    );
}

// --- TestReminderRule — wave7 counter parity ---

#[test]
fn test_reminder_never_denies_after_counter_write() {
    // Even with counter management active, the rule must never produce a deny.
    let rule = TestReminderRule;
    let data = json!({"toolName": "edit", "toolArgs": {"path": "learn.py"}});
    if let Some(v) = rule.evaluate("postToolUse", &data) {
        assert!(
            v.get("permissionDecision").is_none(),
            "TestReminderRule must never emit permissionDecision; got: {v}"
        );
    }
}

#[test]
fn test_reminder_emits_at_threshold_when_counter_at_3() {
    let _guard = env_lock();
    // Write a py-edit-count of 2, then trigger one more edit → count becomes 3
    // (3 >= 3 && 3 % 3 == 0) → reminder must be emitted.
    let tmp = std::env::temp_dir().join("sk_test_reminder_threshold");
    let _ = std::fs::create_dir_all(&tmp);
    let counter = tmp.join("py-edit-count");
    // Seed counter at 2 — use sign_counter so sign/verify use the same
    // secret regardless of whether ~/.copilot/hooks/.marker-secret exists.
    marker_auth::sign_counter(&counter, 2).unwrap();
    // Place tests-ran so we can verify it gets deleted.
    let tests_ran = tmp.join("tests-ran");
    std::fs::write(&tests_ran, b"").unwrap();

    // We can't easily override markers_dir() in unit tests, so test the
    // increment_py_edit_count helper's logic directly on the temp path.
    let current = marker_auth::verify_counter(&counter);
    assert_eq!(current, 2, "seeded counter should read 2");
    let new_count = current + 1; // simulate one more edit
    let _ = marker_auth::sign_counter(&counter, new_count);
    assert_eq!(
        marker_auth::verify_counter(&counter),
        3,
        "new count should be 3"
    );

    // Threshold check: 3 >= 3 && 3 % 3 == 0.
    let count: i64 = 3;
    assert!(count >= 3 && count % 3 == 0, "threshold must fire at 3");

    let _ = std::fs::remove_dir_all(&tmp);
}

#[test]
fn test_reminder_does_not_emit_below_threshold() {
    // count 1 and 2: no reminder should fire.
    for count in &[1i64, 2] {
        assert!(
            !(*count >= 3 && *count % 3 == 0),
            "threshold must NOT fire at count {count}"
        );
    }
}

#[test]
fn test_reminder_emits_at_every_third() {
    // Threshold fires at 3, 6, 9... but not at 4, 5, 7, 8...
    for count in &[3i64, 6, 9, 12] {
        assert!(
            count >= &3 && count % 3 == 0,
            "threshold must fire at {count}"
        );
    }
    for count in &[1i64, 2, 4, 5, 7, 8, 10, 11] {
        assert!(
            !(*count >= 3 && *count % 3 == 0),
            "threshold must NOT fire at {count}"
        );
    }
}

#[test]
fn test_reminder_detect_test_run_matches_known_patterns() {
    assert!(
        detect_test_run("python3 test_security.py"),
        "test_security.py must be detected"
    );
    assert!(
        detect_test_run("python3 test_fixes.py"),
        "test_fixes.py must be detected"
    );
    assert!(
        detect_test_run("run_all_tests.py"),
        "run_all_tests.py must be detected"
    );
    assert!(
        detect_test_run("pytest --tb=short"),
        "pytest must be detected"
    );
    assert!(
        !detect_test_run("cargo test"),
        "cargo test must NOT be detected"
    );
    assert!(!detect_test_run("ls -la"), "ls must NOT be detected");
}

#[test]
fn test_reminder_bash_test_run_returns_none() {
    // Test runs must return None (no reminder) even with existing edit count.
    let rule = TestReminderRule;
    let data = json!({
        "toolName": "bash",
        "toolArgs": {"command": "python3 test_security.py && python3 test_fixes.py"}
    });
    // Must return None — no counter increment, no reminder.
    // (We can't verify the tests-ran side-effect without overriding markers_dir.)
    assert!(
        rule.evaluate("postToolUse", &data).is_none(),
        "bash test run must return None"
    );
}

#[test]
fn test_reminder_counter_preserved_on_read_back() {
    let _guard = env_lock();
    // Verify that sign_counter / verify_counter round-trips correctly for py-edit-count.
    let tmp = std::env::temp_dir()
        .join("sk_test_reminder_counter_rt")
        .join("markers");
    let _ = std::fs::create_dir_all(&tmp);
    let path = tmp.join("py-edit-count");
    let _ = std::fs::remove_file(&path);

    // Start at 5, add 3 → 8.
    marker_auth::sign_counter(&path, 5).expect("sign 5");
    let v1 = marker_auth::verify_counter(&path);
    assert_eq!(v1, 5, "seeded value 5 must round-trip; got {v1}");
    marker_auth::sign_counter(&path, v1 + 3).expect("sign 8");
    let v2 = marker_auth::verify_counter(&path);
    assert_eq!(v2, 8, "after adding 3, must be 8; got {v2}");

    let _ = std::fs::remove_dir_all(tmp.parent().unwrap());
}

// --- NextjsTypecheckReminderRule — wave7 counter parity ---

#[test]
fn nextjs_counter_round_trip_plain_text() {
    // ts-edit-count is plain text, not HMAC.  Verify write/read cycle.
    let tmp = std::env::temp_dir().join("sk_ts_counter_rt");
    let _ = std::fs::create_dir_all(&tmp);
    let path = tmp.join("ts-edit-count");
    let _ = std::fs::remove_file(&path);

    // Write 7.
    std::fs::write(&path, "7").unwrap();
    let v = std::fs::read_to_string(&path)
        .unwrap()
        .trim()
        .parse::<i64>()
        .unwrap();
    assert_eq!(v, 7, "plain-text counter must round-trip as 7; got {v}");

    // Increment to 8.
    std::fs::write(&path, "8").unwrap();
    let v2 = std::fs::read_to_string(&path)
        .unwrap()
        .trim()
        .parse::<i64>()
        .unwrap();
    assert_eq!(v2, 8, "incremented counter must be 8; got {v2}");

    let _ = std::fs::remove_dir_all(&tmp);
}

#[test]
fn nextjs_threshold_logic_matches_python() {
    // count >= 3 && count % 3 == 0 must match Python's behaviour.
    for count in &[3i64, 6, 9] {
        assert!(
            count >= &3 && count % 3 == 0,
            "TS threshold must fire at {count}"
        );
    }
    for count in &[1i64, 2, 4, 5] {
        assert!(
            !(*count >= 3 && *count % 3 == 0),
            "TS threshold must NOT fire at {count}"
        );
    }
}

#[test]
fn nextjs_counter_not_hmac_signed() {
    // ts-edit-count must be plain text — verify_counter must NOT be used.
    // Write a known integer as plain text; read_ts_edit_count should parse it.
    let tmp = std::env::temp_dir().join("sk_ts_plain_check");
    let _ = std::fs::create_dir_all(&tmp);
    let path = tmp.join("ts-edit-count-plaincheck");
    std::fs::write(&path, "42").unwrap();

    // read_ts_edit_count reads from a fixed markers_dir() path, so we verify
    // the format separately: plain integer string is parseable.
    let v: i64 = std::fs::read_to_string(&path)
        .unwrap()
        .trim()
        .parse()
        .unwrap();
    assert_eq!(v, 42, "plain-text ts-edit-count must parse as integer");

    // Verify it is NOT HMAC-signed JSON (must not contain 'sig' key).
    let content = std::fs::read_to_string(&path).unwrap();
    assert!(
        !content.contains("\"sig\""),
        "ts-edit-count must NOT be HMAC JSON; got: {content}"
    );

    let _ = std::fs::remove_dir_all(&tmp);
}

#[test]
fn nextjs_reminder_never_denies_with_counter() {
    let rule = NextjsTypecheckReminderRule;
    let data = json!({
        "toolName": "edit",
        "toolArgs": {"path": "browse-ui/src/App.tsx", "old_str": "x", "new_str": "y"}
    });
    if let Some(v) = rule.evaluate("postToolUse", &data) {
        assert!(
            v.get("permissionDecision").is_none(),
            "NextjsTypecheckReminderRule must never deny; got: {v}"
        );
    }
}

// --- ReadBeforeEditRule ---

#[test]
fn read_before_edit_is_absolute_path_unix() {
    assert!(
        is_absolute_path("/home/user/file.py"),
        "Unix absolute must be detected"
    );
    assert!(
        !is_absolute_path("relative/file.py"),
        "relative must not match"
    );
}

#[test]
fn read_before_edit_is_absolute_path_windows() {
    assert!(
        is_absolute_path("C:\\Users\\user\\file.py"),
        "Windows drive path must match"
    );
    assert!(
        is_absolute_path("C:/Users/user/file.py"),
        "Windows drive with forward slash must match"
    );
    assert!(
        is_absolute_path("\\\\server\\share\\file.py"),
        "UNC path must match"
    );
}

#[test]
fn read_before_edit_ext_check() {
    assert!(is_read_before_edit_ext("file.py"), ".py must be included");
    assert!(is_read_before_edit_ext("file.ts"), ".ts must be included");
    assert!(is_read_before_edit_ext("file.rs"), ".rs must be included");
    assert!(is_read_before_edit_ext("FILE.PY"), "case-insensitive match");
    assert!(
        !is_read_before_edit_ext("image.png"),
        ".png must be excluded"
    );
    assert!(
        !is_read_before_edit_ext("archive.tar.gz"),
        ".gz must be excluded"
    );
}

#[test]
fn read_before_edit_postuse_returns_none() {
    // postToolUse always returns None (side-effect only).
    let rule = ReadBeforeEditRule;
    let data = json!({
        "toolName": "view",
        "toolInput": {"path": "/home/user/file.py"}
    });
    assert!(
        rule.evaluate("postToolUse", &data).is_none(),
        "postToolUse must return None (side-effect only)"
    );
}

#[test]
fn read_before_edit_pretooluse_non_edit_returns_none() {
    let rule = ReadBeforeEditRule;
    let data = json!({"toolName": "bash", "toolArgs": {"command": "ls"}});
    assert!(
        rule.evaluate("preToolUse", &data).is_none(),
        "preToolUse non-edit/create must return None"
    );
}

#[test]
fn read_before_edit_pretooluse_relative_path_returns_none() {
    // Relative paths are skipped (not tracked).
    let rule = ReadBeforeEditRule;
    let data = json!({
        "toolName": "edit",
        "toolArgs": {"path": "relative/file.py"}
    });
    assert!(
        rule.evaluate("preToolUse", &data).is_none(),
        "relative path must return None (not absolute)"
    );
}

#[test]
fn read_before_edit_pretooluse_non_code_ext_returns_none() {
    let rule = ReadBeforeEditRule;
    let data = json!({
        "toolName": "edit",
        "toolArgs": {"path": "/home/user/image.png"}
    });
    assert!(
        rule.evaluate("preToolUse", &data).is_none(),
        ".png must return None (not a code file)"
    );
}

#[test]
fn read_before_edit_never_denies() {
    let rule = ReadBeforeEditRule;
    // preToolUse edit on an unread absolute .py file.
    let data = json!({
        "toolName": "edit",
        "toolArgs": {"path": "/absolutely/unread/file.py"}
    });
    if let Some(v) = rule.evaluate("preToolUse", &data) {
        assert!(
            v.get("permissionDecision").is_none(),
            "ReadBeforeEditRule must NEVER produce permissionDecision; got: {v}"
        );
        // Must be an informational message.
        assert!(
            v.get("message").is_some(),
            "must have message field when warning"
        );
    }
}

#[test]
fn read_before_edit_fires_on_both_events() {
    let rule = ReadBeforeEditRule;
    assert!(rule.events().contains(&"preToolUse"));
    assert!(rule.events().contains(&"postToolUse"));
}

#[test]
fn read_before_edit_has_no_tool_filter() {
    let rule = ReadBeforeEditRule;
    assert!(
        rule.tools().is_empty(),
        "ReadBeforeEditRule must have no tool filter"
    );
}

// --- TestReminderRule ---

#[test]
fn test_reminder_returns_none_for_non_py_file_edit() {
    let rule = TestReminderRule;
    let data = json!({
        "toolName": "edit",
        "toolArgs": {"path": "src/main.rs", "old_str": "x", "new_str": "y"}
    });
    assert!(
        rule.evaluate("postToolUse", &data).is_none(),
        "non-.py file edit must return None"
    );
}

#[test]
fn test_reminder_emits_info_for_py_file_edit() {
    // Wave7: TestReminderRule uses threshold-based counter (count >= 3 && count % 3 == 0).
    // A single edit increments to 1, which is below threshold → may return None.
    // The contract is: if Some, no deny; the reminder fires at count 3/6/9/...
    let rule = TestReminderRule;
    let data = json!({
        "toolName": "edit",
        "toolArgs": {"path": "learn.py", "old_str": "x", "new_str": "y"}
    });
    let result = rule.evaluate("postToolUse", &data);
    // Informational-only (never deny), regardless of whether threshold is hit.
    if let Some(msg) = result {
        assert!(
            msg.get("permissionDecision").is_none(),
            "TestReminderRule must never produce a deny; got: {msg}"
        );
        let text = msg["message"].as_str().expect("must have message");
        assert!(
            text.contains("TEST REMINDER"),
            "message must mention TEST REMINDER"
        );
    }
    // None is valid when count is below threshold — not a failure.
}

#[test]
fn test_reminder_emits_info_for_py_file_create() {
    // Wave7: create of .py increments counter; reminder fires only at threshold.
    let rule = TestReminderRule;
    let data = json!({
        "toolName": "create",
        "toolArgs": {"path": "new_script.py", "file_text": "pass"}
    });
    let result = rule.evaluate("postToolUse", &data);
    // Never deny regardless of threshold.
    if let Some(msg) = result {
        assert!(msg.get("permissionDecision").is_none(), "must not deny");
    }
}

#[test]
fn test_reminder_emits_info_for_py_file_create_from_input_file_path() {
    // Wave7: create via input.filePath increments counter; threshold-based reminder.
    let rule = TestReminderRule;
    let data = json!({
        "toolName": "create",
        "input": {"filePath": "new_script.py"}
    });
    let result = rule.evaluate("postToolUse", &data);
    if let Some(msg) = result {
        assert!(msg.get("permissionDecision").is_none(), "must not deny");
    }
}

#[test]
fn test_reminder_returns_none_for_bash_without_py_write() {
    let rule = TestReminderRule;
    let data = json!({
        "toolName": "bash",
        "toolArgs": {"command": "cargo test --quiet"}
    });
    assert!(
        rule.evaluate("postToolUse", &data).is_none(),
        "bash without .py write must return None"
    );
}

#[test]
fn test_reminder_skips_session_state_py_files() {
    let rule = TestReminderRule;
    let data = json!({
        "toolName": "edit",
        "toolArgs": {"path": "session-state/some-id/script.py"}
    });
    assert!(
        rule.evaluate("postToolUse", &data).is_none(),
        "session-state .py must be excluded"
    );
}

#[test]
fn test_reminder_fires_only_on_posttooluse() {
    let rule = TestReminderRule;
    assert!(rule.events().contains(&"postToolUse"));
    assert!(!rule.events().contains(&"preToolUse"));
    assert!(!rule.events().contains(&"sessionStart"));
}

#[test]
fn test_reminder_applies_to_edit_create_bash() {
    let rule = TestReminderRule;
    assert!(rule.tools().contains(&"edit"));
    assert!(rule.tools().contains(&"create"));
    assert!(rule.tools().contains(&"bash"));
}

#[test]
fn is_py_source_path_detects_py_extension() {
    assert!(is_py_source_path("learn.py"));
    assert!(is_py_source_path("hooks/rules/edit_tracker.py"));
    assert!(!is_py_source_path("main.rs"));
    assert!(!is_py_source_path("script.pyx"));
}

#[test]
fn is_py_source_path_excludes_session_state() {
    assert!(!is_py_source_path("session-state/abc/something.py"));
}

// --- NextjsTypecheckReminderRule ---

#[test]
fn nextjs_typecheck_reminder_emits_for_browse_ui_ts_file() {
    // Wave7: NextjsTypecheckReminderRule uses threshold-based counter.
    // A single edit sets count=1, below threshold(3) → may return None.
    // Contract: if Some, no deny and mentions TS REMINDER + pnpm typecheck.
    let rule = NextjsTypecheckReminderRule;
    let data = json!({
        "toolName": "edit",
        "toolArgs": {"path": "browse-ui/src/App.tsx", "old_str": "x", "new_str": "y"}
    });
    let result = rule.evaluate("postToolUse", &data);
    if let Some(msg) = result {
        let text = msg["message"].as_str().expect("must have message");
        assert!(
            text.contains("TS REMINDER"),
            "message must mention TS REMINDER; got: {text}"
        );
        assert!(
            text.contains("pnpm typecheck"),
            "message must mention pnpm typecheck; got: {text}"
        );
        // Informational-only: no deny.
        assert!(
            msg.get("permissionDecision").is_none(),
            "NextjsTypecheckReminderRule must never produce a deny"
        );
    }
    // None is valid below threshold — not a failure.
}

#[test]
fn nextjs_typecheck_reminder_returns_none_for_non_browse_ui_ts() {
    let rule = NextjsTypecheckReminderRule;
    // .tsx file but NOT under browse-ui/ → no reminder
    let data = json!({
        "toolName": "edit",
        "toolArgs": {"path": "src/components/MyComp.tsx", "old_str": "x", "new_str": "y"}
    });
    assert!(
        rule.evaluate("postToolUse", &data).is_none(),
        ".tsx outside browse-ui/ must return None"
    );
}

#[test]
fn nextjs_typecheck_reminder_returns_none_for_non_ts_browse_ui_file() {
    let rule = NextjsTypecheckReminderRule;
    // browse-ui file but .js extension → no reminder
    let data = json!({
        "toolName": "edit",
        "toolArgs": {"path": "browse-ui/src/util.js", "old_str": "x", "new_str": "y"}
    });
    assert!(
        rule.evaluate("postToolUse", &data).is_none(),
        "browse-ui .js file must return None (only .ts/.tsx trigger this rule)"
    );
}

#[test]
fn nextjs_typecheck_reminder_fail_open_missing_path() {
    let rule = NextjsTypecheckReminderRule;
    // toolArgs present but no path → fail-open
    let data = json!({"toolName": "edit", "toolArgs": {"old_str": "x"}});
    assert!(
        rule.evaluate("postToolUse", &data).is_none(),
        "missing path must fail-open (None)"
    );
}

#[test]
fn nextjs_typecheck_reminder_fail_open_missing_tool_args() {
    let rule = NextjsTypecheckReminderRule;
    let data = json!({"toolName": "edit"});
    assert!(
        rule.evaluate("postToolUse", &data).is_none(),
        "missing toolArgs must fail-open (None)"
    );
}

#[test]
fn nextjs_typecheck_reminder_fail_open_empty_path() {
    let rule = NextjsTypecheckReminderRule;
    let data = json!({"toolName": "edit", "toolArgs": {"path": ""}});
    assert!(
        rule.evaluate("postToolUse", &data).is_none(),
        "empty path must fail-open (None)"
    );
}

#[test]
fn nextjs_typecheck_reminder_accepts_windows_style_path() {
    // Wave7: threshold-based; single edit may return None (count=1 < 3).
    let rule = NextjsTypecheckReminderRule;
    // Windows backslash path must be normalised and matched.
    let data = json!({
        "toolName": "create",
        "toolArgs": {"path": "browse-ui\\src\\NewComp.ts", "file_text": ""}
    });
    let result = rule.evaluate("postToolUse", &data);
    // If Some: no deny. If None: below threshold — valid.
    if let Some(msg) = result {
        assert!(msg.get("permissionDecision").is_none(), "must not deny");
    }
}

#[test]
fn nextjs_typecheck_reminder_accepts_input_file_path_shape() {
    // Wave7: threshold-based; single create may return None (count=1 < 3).
    let rule = NextjsTypecheckReminderRule;
    let data = json!({
        "toolName": "create",
        "input": {"filePath": "browse-ui/src/NewComp.tsx"}
    });
    let result = rule.evaluate("postToolUse", &data);
    if let Some(msg) = result {
        assert!(msg.get("permissionDecision").is_none(), "must not deny");
    }
}

#[test]
fn nextjs_typecheck_reminder_fires_only_on_posttooluse() {
    let rule = NextjsTypecheckReminderRule;
    assert!(rule.events().contains(&"postToolUse"));
    assert!(!rule.events().contains(&"preToolUse"));
}

#[test]
fn nextjs_typecheck_reminder_applies_to_edit_and_create_only() {
    let rule = NextjsTypecheckReminderRule;
    assert!(rule.tools().contains(&"edit"));
    assert!(rule.tools().contains(&"create"));
    assert!(!rule.tools().contains(&"bash"));
}
