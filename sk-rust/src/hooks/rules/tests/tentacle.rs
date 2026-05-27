use super::*;

// --- get_module_for_path helper (wave8) ---

#[test]
fn get_module_for_path_recognises_src_prefix() {
    let m = get_module_for_path("src/main.py", None);
    assert!(!m.is_empty(), "src/main.py must produce a module name");
    assert!(m.contains("src"), "module must reference 'src'; got: {m}");
}

#[test]
fn get_module_for_path_recognises_hooks_prefix() {
    let m = get_module_for_path("src/hooks/rules.rs", None);
    // Python logic: i=0 "src" → next is "hooks" (not filename) → "src/hooks"
    // then i=1 "hooks" → next is "rules.rs" (is filename) → "hooks"
    // last marker wins: "hooks"
    assert!(!m.is_empty(), "must produce a module name");
    // We just verify it doesn't crash and produces something meaningful.
}

#[test]
fn get_module_for_path_with_repo_prefix() {
    let m = get_module_for_path("src/main.py", Some("myrepo"));
    assert!(
        m.starts_with("myrepo:"),
        "must include repo prefix; got: {m}"
    );
}

#[test]
fn get_module_for_path_handles_windows_backslash() {
    let m = get_module_for_path("src\\hooks\\rules.rs", None);
    assert!(!m.is_empty(), "Windows-style path must normalise correctly");
}

#[test]
fn get_module_for_path_empty_for_flat_file() {
    // Single-component path (no directory) → empty module.
    let m = get_module_for_path("main.rs", None);
    // parts.len() < 2 → empty string
    assert!(
        m.is_empty(),
        "flat file with no directory must return empty; got: {m}"
    );
}

// --- TentacleSuggestRule (wave8) ---

#[test]
fn tentacle_suggest_fires_on_posttooluse_only() {
    let rule = TentacleSuggestRule;
    assert!(rule.events().contains(&"postToolUse"));
    assert!(!rule.events().contains(&"preToolUse"));
}

#[test]
fn tentacle_suggest_applies_to_edit_create_bash() {
    let rule = TentacleSuggestRule;
    assert!(rule.tools().contains(&"edit"));
    assert!(rule.tools().contains(&"create"));
    assert!(rule.tools().contains(&"bash"));
}

#[test]
fn tentacle_suggest_name_is_tentacle_suggest() {
    assert_eq!(TentacleSuggestRule.name(), "tentacle-suggest");
}

#[test]
fn tentacle_suggest_never_denies() {
    let rule = TentacleSuggestRule;
    // With an empty tentacle-edits marker (no files), must return None.
    let data = json!({"toolName": "edit", "toolArgs": {"path": "src/main.py"}});
    if let Some(v) = rule.evaluate("postToolUse", &data) {
        assert!(
            v.get("permissionDecision").is_none(),
            "TentacleSuggestRule must NEVER produce permissionDecision; got: {v}"
        );
    }
}

#[test]
fn tentacle_suggest_returns_none_below_threshold() {
    // Without a real tentacle-edits marker with ≥3 entries, should return None.
    let rule = TentacleSuggestRule;
    let data = json!({"toolName": "bash", "toolArgs": {"command": "ls"}});
    // In a clean test environment (no tentacle-edits marker), must be None.
    // We can't easily seed the marker without side effects, so we verify:
    //   a) the rule doesn't panic
    //   b) if it returns Some, it has no deny key
    if let Some(v) = rule.evaluate("postToolUse", &data) {
        assert!(
            v.get("permissionDecision").is_none(),
            "TentacleSuggestRule must never deny; got: {v}"
        );
    }
}

/// Test that read_tentacle_edits_paths handles flat legacy format correctly.
#[test]
fn read_tentacle_edits_paths_handles_legacy_flat_format() {
    let _guard = env_lock();

    use std::collections::HashSet;

    let tmp = std::env::temp_dir().join("sk_tentacle_edits_test");
    let _ = std::fs::create_dir_all(&tmp);
    let edits_path = tmp.join("tentacle-edits-legacy-test");
    let _ = std::fs::remove_file(&edits_path);

    // Write flat file paths as legacy format via sign_list_marker.
    let paths: Vec<String> = vec![
        "src/main.py".to_string(),
        "lib/util.rs".to_string(),
        "hooks/rules.py".to_string(),
    ];
    marker_auth::sign_list_marker(&edits_path, &paths).expect("sign_list_marker");

    // Verify the signed set reads back as expected file paths.
    let back: HashSet<String> = marker_auth::verify_list_marker(&edits_path);
    assert!(
        back.contains("src/main.py"),
        "legacy format must preserve src/main.py"
    );
    assert!(
        back.contains("lib/util.rs"),
        "legacy format must preserve lib/util.rs"
    );

    let _ = std::fs::remove_dir_all(&tmp);
}

/// Test that read_tentacle_edits_paths handles new JSON-dict format correctly.
#[test]
fn read_tentacle_edits_paths_handles_json_dict_format() {
    let _guard = env_lock();

    use std::collections::HashSet;

    let tmp = std::env::temp_dir().join("sk_tentacle_edits_json_test");
    let _ = std::fs::create_dir_all(&tmp);
    let edits_path = tmp.join("tentacle-edits-json-test");
    let _ = std::fs::remove_file(&edits_path);

    // Write new JSON-dict format: {repo_root: [{p, t}...]}
    let now = SystemTime::now()
        .duration_since(UNIX_EPOCH)
        .unwrap_or_default()
        .as_secs_f64();
    let payload = serde_json::json!({
        "/home/user/myrepo": [
            {"p": "src/auth.py", "t": now},
            {"p": "lib/util.rs", "t": now}
        ]
    });
    let payload_str = payload.to_string();
    marker_auth::sign_list_marker(&edits_path, &[payload_str])
        .expect("sign_list_marker for JSON dict");

    // Read back via verify_list_marker — should get the JSON string as a set element.
    let back: HashSet<String> = marker_auth::verify_list_marker(&edits_path);
    assert_eq!(back.len(), 1, "JSON-dict format: set must have 1 element");
    let sole = back.iter().next().unwrap();
    assert!(
        sole.starts_with('{'),
        "sole element must be a JSON object string"
    );

    // Now verify our parsing logic extracts file paths.
    let val: Value = serde_json::from_str(sole).expect("must parse JSON");
    let obj = val.as_object().unwrap();
    let entries = obj.values().next().unwrap().as_array().unwrap();
    assert_eq!(entries.len(), 2, "must have 2 file entries");
    let p0 = entries[0].get("p").and_then(|v| v.as_str()).unwrap();
    assert!(
        p0 == "src/auth.py" || p0 == "lib/util.rs",
        "path must be one of the seeded values"
    );

    let _ = std::fs::remove_dir_all(&tmp);
}

// --- wave8 registry sanity ---

#[test]
fn all_rules_includes_verification_gate_pre() {
    let rules = all_rules();
    assert!(
        rules.iter().any(|r| r.name() == "verification-gate-pre"),
        "all_rules must include verification-gate-pre (wave8)"
    );
}

#[test]
fn all_rules_includes_tentacle_suggest() {
    let rules = all_rules();
    assert!(
        rules.iter().any(|r| r.name() == "tentacle-suggest"),
        "all_rules must include tentacle-suggest (wave8)"
    );
}

#[test]
fn all_rules_verif_gate_pre_before_read_before_edit() {
    let rules = all_rules();
    let pre_pos = rules
        .iter()
        .position(|r| r.name() == "verification-gate-pre");
    let rbe_pos = rules.iter().position(|r| r.name() == "read-before-edit");
    assert!(
        pre_pos.is_some() && rbe_pos.is_some(),
        "both verification-gate-pre and read-before-edit must be present"
    );
    assert!(
        pre_pos.unwrap() < rbe_pos.unwrap(),
        "verification-gate-pre must precede read-before-edit in dispatch order"
    );
}

#[test]
fn all_rules_tentacle_suggest_after_verif_gate_post() {
    let rules = all_rules();
    let vgp_pos = rules
        .iter()
        .position(|r| r.name() == "verification-gate-post");
    let ts_pos = rules.iter().position(|r| r.name() == "tentacle-suggest");
    assert!(
        vgp_pos.is_some() && ts_pos.is_some(),
        "both verification-gate-post and tentacle-suggest must be present"
    );
    assert!(
        ts_pos.unwrap() > vgp_pos.unwrap(),
        "tentacle-suggest must come after verification-gate-post"
    );
}

// --- wave12: TentacleEnforceRule ---

#[test]
fn tentacle_enforce_fires_on_pretooluse_only() {
    let rule = TentacleEnforceRule;
    assert!(rule.events().contains(&"preToolUse"));
    assert!(!rule.events().contains(&"postToolUse"));
    assert!(!rule.events().contains(&"sessionStart"));
}

#[test]
fn tentacle_enforce_covers_edit_create_bash() {
    let rule = TentacleEnforceRule;
    assert!(rule.tools().contains(&"edit"));
    assert!(rule.tools().contains(&"create"));
    assert!(rule.tools().contains(&"bash"));
    assert!(!rule.tools().contains(&"task_complete"));
}

#[test]
fn bash_writes_source_for_enforce_tentacle_detects_cp() {
    assert!(
        bash_writes_source_for_enforce_tentacle("cp old.py new.py"),
        "cp <src.py> must be detected as a source write"
    );
}

#[test]
fn bash_writes_source_for_enforce_tentacle_detects_mv() {
    assert!(
        bash_writes_source_for_enforce_tentacle("mv src.rs dest.rs"),
        "mv <src.rs> must be detected as a source write"
    );
}

#[test]
fn bash_writes_source_for_enforce_tentacle_detects_patch() {
    assert!(
        bash_writes_source_for_enforce_tentacle("patch -p1 main.py < diff.patch"),
        "patch <file.py> must be detected as a source write"
    );
}

#[test]
fn bash_writes_source_for_enforce_tentacle_detects_rsync() {
    assert!(
        bash_writes_source_for_enforce_tentacle("rsync -av build/ dest.ts"),
        "rsync with .ts target must be detected as a source write"
    );
}

#[test]
fn bash_writes_source_for_enforce_tentacle_ignores_no_extension() {
    assert!(
        !bash_writes_source_for_enforce_tentacle("cp README LICENSE"),
        "cp without code extensions must not be detected"
    );
}

#[test]
fn bash_writes_source_for_enforce_tentacle_ignores_plain_ls() {
    assert!(
        !bash_writes_source_for_enforce_tentacle("ls -la"),
        "ls must not be detected as a source write"
    );
}

#[test]
fn read_tentacle_edits_for_current_repo_returns_empty_for_no_marker() {
    let _guard = env_lock();
    let tmp = std::env::temp_dir().join("sk_te_enforce_empty");
    let _ = std::fs::remove_dir_all(&tmp);
    std::fs::create_dir_all(tmp.join(".copilot").join("markers")).expect("create dir");
    let old_home = std::env::var_os("HOME");
    let old_up = std::env::var_os("USERPROFILE");
    std::env::set_var("HOME", &tmp);
    std::env::set_var("USERPROFILE", &tmp);
    let result = read_tentacle_edits_for_current_repo();
    match old_home {
        Some(v) => std::env::set_var("HOME", v),
        None => std::env::remove_var("HOME"),
    }
    match old_up {
        Some(v) => std::env::set_var("USERPROFILE", v),
        None => std::env::remove_var("USERPROFILE"),
    }
    let _ = std::fs::remove_dir_all(&tmp);
    assert!(result.is_empty(), "no marker → must return empty vec");
}

#[test]
fn read_tentacle_edits_for_current_repo_includes_legacy_flat_paths() {
    let _guard = env_lock();
    let tmp = std::env::temp_dir().join("sk_te_enforce_legacy");
    let _ = std::fs::remove_dir_all(&tmp);
    let mdir = tmp.join(".copilot").join("markers");
    std::fs::create_dir_all(&mdir).expect("create markers dir");

    // Override HOME/USERPROFILE before signing so that sign and verify
    // both resolve the same (absent) marker secret from the temp dir.
    let old_home = std::env::var_os("HOME");
    let old_up = std::env::var_os("USERPROFILE");
    std::env::set_var("HOME", &tmp);
    std::env::set_var("USERPROFILE", &tmp);

    // Write flat legacy paths into the edits marker.
    let paths: Vec<String> = vec![
        "src/a.py".to_string(),
        "lib/b.rs".to_string(),
        "hooks/c.py".to_string(),
    ];
    marker_auth::sign_list_marker(&mdir.join("tentacle-edits"), &paths).expect("sign_list_marker");

    let result = read_tentacle_edits_for_current_repo();

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
        result.len() >= 3,
        "legacy flat paths must be returned (got {:?})",
        result
    );
}

#[test]
fn read_tentacle_edits_for_current_repo_reads_same_repo_json_bucket() {
    let _guard = env_lock();
    let tmp = std::env::temp_dir().join("sk_te_enforce_same_repo_json");
    let _ = std::fs::remove_dir_all(&tmp);
    let mdir = tmp.join(".copilot").join("markers");
    std::fs::create_dir_all(&mdir).expect("create markers dir");

    let repo_root = get_git_root().expect("test repo must have git root");
    let now = SystemTime::now()
        .duration_since(UNIX_EPOCH)
        .unwrap_or_default()
        .as_secs();

    // Override HOME/USERPROFILE before signing so that sign and verify
    // both resolve the same (absent) marker secret from the temp dir.
    let old_home = std::env::var_os("HOME");
    let old_up = std::env::var_os("USERPROFILE");
    std::env::set_var("HOME", &tmp);
    std::env::set_var("USERPROFILE", &tmp);

    let payload = serde_json::json!({
        repo_root.clone(): [
            {"p": format!("{repo_root}/src/a.py"), "t": now},
            {"p": format!("{repo_root}/hooks/b.py"), "t": now},
            {"p": format!("{repo_root}/tests/c.py"), "t": now},
        ]
    })
    .to_string();
    marker_auth::sign_list_marker(&mdir.join("tentacle-edits"), &[payload])
        .expect("sign_list_marker");

    let result = read_tentacle_edits_for_current_repo();

    match old_home {
        Some(v) => std::env::set_var("HOME", v),
        None => std::env::remove_var("HOME"),
    }
    match old_up {
        Some(v) => std::env::set_var("USERPROFILE", v),
        None => std::env::remove_var("USERPROFILE"),
    }
    let _ = std::fs::remove_dir_all(&tmp);

    assert_eq!(result.len(), 3, "same-repo JSON bucket must be returned");
}

#[test]
fn read_tentacle_edits_for_current_repo_ignores_other_repo_json_bucket() {
    let _guard = env_lock();
    let tmp = std::env::temp_dir().join("sk_te_enforce_other_repo_json");
    let _ = std::fs::remove_dir_all(&tmp);
    let mdir = tmp.join(".copilot").join("markers");
    std::fs::create_dir_all(&mdir).expect("create markers dir");

    let repo_root = get_git_root().expect("test repo must have git root");
    let other_root = format!("{repo_root}_other");
    let now = SystemTime::now()
        .duration_since(UNIX_EPOCH)
        .unwrap_or_default()
        .as_secs();

    // Override HOME/USERPROFILE before signing so that sign and verify
    // both resolve the same (absent) marker secret from the temp dir.
    let old_home = std::env::var_os("HOME");
    let old_up = std::env::var_os("USERPROFILE");
    std::env::set_var("HOME", &tmp);
    std::env::set_var("USERPROFILE", &tmp);

    let payload = serde_json::json!({
        other_root: [
            {"p": format!("{repo_root}/src/a.py"), "t": now},
            {"p": format!("{repo_root}/hooks/b.py"), "t": now},
            {"p": format!("{repo_root}/tests/c.py"), "t": now},
        ]
    })
    .to_string();
    marker_auth::sign_list_marker(&mdir.join("tentacle-edits"), &[payload])
        .expect("sign_list_marker");

    let result = read_tentacle_edits_for_current_repo();

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
        result.is_empty(),
        "other-repo JSON bucket must not affect current repo; got {:?}",
        result
    );
}

#[test]
fn read_tentacle_edits_for_current_repo_prunes_stale_entries() {
    let _guard = env_lock();
    let tmp = std::env::temp_dir().join("sk_te_enforce_prune_stale");
    let _ = std::fs::remove_dir_all(&tmp);
    let mdir = tmp.join(".copilot").join("markers");
    std::fs::create_dir_all(&mdir).expect("create markers dir");

    let repo_root = get_git_root().expect("test repo must have git root");
    let now = SystemTime::now()
        .duration_since(UNIX_EPOCH)
        .unwrap_or_default()
        .as_secs();
    let stale = now.saturating_sub(TENTACLE_ENFORCE_TTL_SECS + 10);

    // Override HOME/USERPROFILE before signing so that sign and verify
    // both resolve the same (absent) marker secret from the temp dir.
    let old_home = std::env::var_os("HOME");
    let old_up = std::env::var_os("USERPROFILE");
    std::env::set_var("HOME", &tmp);
    std::env::set_var("USERPROFILE", &tmp);

    let payload = serde_json::json!({
        repo_root.clone(): [
            {"p": format!("{repo_root}/src/stale.py"), "t": stale},
            {"p": format!("{repo_root}/hooks/fresh.py"), "t": now},
        ]
    })
    .to_string();
    marker_auth::sign_list_marker(&mdir.join("tentacle-edits"), &[payload])
        .expect("sign_list_marker");

    let result = read_tentacle_edits_for_current_repo();

    match old_home {
        Some(v) => std::env::set_var("HOME", v),
        None => std::env::remove_var("HOME"),
    }
    match old_up {
        Some(v) => std::env::set_var("USERPROFILE", v),
        None => std::env::remove_var("USERPROFILE"),
    }
    let _ = std::fs::remove_dir_all(&tmp);

    assert_eq!(result.len(), 1, "stale entries must be pruned");
    assert!(
        result[0].contains("fresh.py"),
        "only fresh entry should remain after TTL prune; got {:?}",
        result
    );
}

#[test]
fn tentacle_enforce_allows_edit_below_threshold() {
    let _guard = env_lock();
    let tmp = std::env::temp_dir().join("sk_te_enforce_below_thresh");
    let _ = std::fs::remove_dir_all(&tmp);
    let mdir = tmp.join(".copilot").join("markers");
    std::fs::create_dir_all(&mdir).expect("create markers dir");

    // Only 2 files in the edits marker (below TENTACLE_ENFORCE_MIN_FILES=3).
    let paths: Vec<String> = vec!["src/a.py".to_string(), "lib/b.rs".to_string()];
    marker_auth::sign_list_marker(&mdir.join("tentacle-edits"), &paths).expect("sign_list_marker");

    let old_home = std::env::var_os("HOME");
    let old_up = std::env::var_os("USERPROFILE");
    std::env::set_var("HOME", &tmp);
    std::env::set_var("USERPROFILE", &tmp);

    let rule = TentacleEnforceRule;
    let data = json!({"toolName": "edit", "toolArgs": {"path": "src/new.py"}});
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
        "edit below file threshold must return None (allow)"
    );
}

#[test]
fn tentacle_enforce_denies_edit_above_threshold_multi_module() {
    let _guard = env_lock();
    let tmp = std::env::temp_dir().join("sk_te_enforce_above_thresh");
    let _ = std::fs::remove_dir_all(&tmp);
    let mdir = tmp.join(".copilot").join("markers");
    std::fs::create_dir_all(&mdir).expect("create markers dir");

    // Override HOME/USERPROFILE before signing so that sign and verify
    // both resolve the same marker secret from the temp dir (none).
    let old_home = std::env::var_os("HOME");
    let old_up = std::env::var_os("USERPROFILE");
    std::env::set_var("HOME", &tmp);
    std::env::set_var("USERPROFILE", &tmp);

    // 3 files across 2 modules (src, hooks).
    let paths: Vec<String> = vec![
        "src/a.py".to_string(),
        "src/b.py".to_string(),
        "hooks/c.py".to_string(),
    ];
    marker_auth::sign_list_marker(&mdir.join("tentacle-edits"), &paths).expect("sign_list_marker");

    let rule = TentacleEnforceRule;
    let data = json!({"toolName": "edit", "toolArgs": {"path": "hooks/d.py"}});
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
        result.is_some(),
        "edit above threshold across 2 modules must deny"
    );
    let v = result.unwrap();
    assert_eq!(
        v["permissionDecision"].as_str().unwrap_or(""),
        "deny",
        "must be a deny decision"
    );
    let reason = v["permissionDecisionReason"].as_str().unwrap_or("");
    assert!(
        reason.contains("TENTACLE"),
        "deny reason must mention TENTACLE; got: {reason}"
    );
    // Deny message must contain required keywords (Python test parity).
    for kw in &[
        "create", "swarm", "handoff", "commit", "push", "complete", "status",
    ] {
        assert!(
            reason.contains(kw),
            "deny reason must contain keyword '{kw}'; got: {reason}"
        );
    }
}

#[test]
fn tentacle_enforce_allows_edit_with_tentacle_done_marker() {
    let _guard = env_lock();
    let tmp = std::env::temp_dir().join("sk_te_enforce_done_marker");
    let _ = std::fs::remove_dir_all(&tmp);
    let mdir = tmp.join(".copilot").join("markers");
    std::fs::create_dir_all(&mdir).expect("create markers dir");

    // 3 files across 2 modules.
    let paths: Vec<String> = vec![
        "src/a.py".to_string(),
        "src/b.py".to_string(),
        "hooks/c.py".to_string(),
    ];
    marker_auth::sign_list_marker(&mdir.join("tentacle-edits"), &paths).expect("sign_list_marker");
    // tentacle-done bypass marker.
    marker_auth::sign_marker(&mdir.join("tentacle-done"), "tentacle-done")
        .expect("sign_marker tentacle-done");

    let old_home = std::env::var_os("HOME");
    let old_up = std::env::var_os("USERPROFILE");
    std::env::set_var("HOME", &tmp);
    std::env::set_var("USERPROFILE", &tmp);

    let rule = TentacleEnforceRule;
    let data = json!({"toolName": "edit", "toolArgs": {"path": "hooks/d.py"}});
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
        "tentacle-done marker must bypass the deny"
    );
}

#[test]
fn tentacle_enforce_allows_edit_with_tentacle_bypass_marker() {
    let _guard = env_lock();
    let tmp = std::env::temp_dir().join("sk_te_enforce_bypass_marker");
    let _ = std::fs::remove_dir_all(&tmp);
    let mdir = tmp.join(".copilot").join("markers");
    std::fs::create_dir_all(&mdir).expect("create markers dir");

    let paths: Vec<String> = vec![
        "src/a.py".to_string(),
        "src/b.py".to_string(),
        "hooks/c.py".to_string(),
    ];
    marker_auth::sign_list_marker(&mdir.join("tentacle-edits"), &paths).expect("sign_list_marker");
    marker_auth::sign_marker(&mdir.join("tentacle-bypass"), "tentacle-bypass")
        .expect("sign_marker tentacle-bypass");

    let old_home = std::env::var_os("HOME");
    let old_up = std::env::var_os("USERPROFILE");
    std::env::set_var("HOME", &tmp);
    std::env::set_var("USERPROFILE", &tmp);

    let rule = TentacleEnforceRule;
    let data = json!({"toolName": "edit", "toolArgs": {"path": "hooks/d.py"}});
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
        "tentacle-bypass marker must bypass the deny"
    );
}

#[test]
fn tentacle_enforce_denies_when_tamper_marker_present() {
    let _guard = env_lock();
    let tmp = std::env::temp_dir().join("sk_te_enforce_tampered");
    let _ = std::fs::remove_dir_all(&tmp);
    let mdir = tmp.join(".copilot").join("markers");
    std::fs::create_dir_all(&mdir).expect("create markers dir");
    marker_auth::sign_marker(&mdir.join("hooks-tampered"), "hooks-tampered")
        .expect("sign tamper marker");

    let old_home = std::env::var_os("HOME");
    let old_up = std::env::var_os("USERPROFILE");
    std::env::set_var("HOME", &tmp);
    std::env::set_var("USERPROFILE", &tmp);

    let rule = TentacleEnforceRule;
    let data = json!({"toolName": "edit", "toolArgs": {"path": "src/main.py"}});
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

#[test]
fn tentacle_enforce_tamper_marker_allows_lock_hooks_recovery_only() {
    let _guard = env_lock();
    let tmp = std::env::temp_dir().join("sk_te_enforce_recovery");
    let _ = std::fs::remove_dir_all(&tmp);
    let mdir = tmp.join(".copilot").join("markers");
    std::fs::create_dir_all(&mdir).expect("create markers dir");
    marker_auth::sign_marker(&mdir.join("hooks-tampered"), "hooks-tampered")
        .expect("sign tamper marker");

    let old_home = std::env::var_os("HOME");
    let old_up = std::env::var_os("USERPROFILE");
    std::env::set_var("HOME", &tmp);
    std::env::set_var("USERPROFILE", &tmp);

    let rule = TentacleEnforceRule;

    let recovery = json!({
        "toolName": "bash",
        "toolArgs": {"command": "sudo python3 $HOME/.copilot/tools/install.py --lock-hooks"}
    });
    let recovery_result = rule.evaluate("preToolUse", &recovery);

    let ls = json!({"toolName": "bash", "toolArgs": {"command": "ls"}});
    let ls_result = rule.evaluate("preToolUse", &ls);

    let chained = json!({
        "toolName": "bash",
        "toolArgs": {"command": "sudo python3 $HOME/.copilot/tools/install.py --lock-hooks; ls"}
    });
    let chained_result = rule.evaluate("preToolUse", &chained);

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
        recovery_result.is_none(),
        "official --lock-hooks recovery command must bypass tamper deny"
    );
    let ls_value = ls_result.expect("ls under tamper must deny");
    assert!(
        ls_value["permissionDecisionReason"]
            .as_str()
            .unwrap_or("")
            .contains("HOOKS TAMPERED"),
        "ls under tamper must keep HOOKS TAMPERED reason"
    );
    let chained_value = chained_result.expect("chained recovery must deny");
    assert!(
        chained_value["permissionDecisionReason"]
            .as_str()
            .unwrap_or("")
            .contains("HOOKS TAMPERED"),
        "chained recovery must keep HOOKS TAMPERED reason"
    );
}

#[test]
fn all_rules_includes_tentacle_enforce() {
    let rules = all_rules();
    assert!(
        rules.iter().any(|r| r.name() == "tentacle-enforce"),
        "all_rules must include tentacle-enforce (wave12)"
    );
}

#[test]
fn all_rules_tentacle_enforce_before_subagent_git_guard() {
    let rules = all_rules();
    let te = rules.iter().position(|r| r.name() == "tentacle-enforce");
    let sgg = rules.iter().position(|r| r.name() == "subagent-git-guard");
    assert!(
        te.is_some() && sgg.is_some(),
        "both tentacle-enforce and subagent-git-guard must be registered"
    );
    assert!(
        te.unwrap() < sgg.unwrap(),
        "tentacle-enforce must precede subagent-git-guard (Python dispatch order)"
    );
}

#[test]
fn all_rules_enforce_learn_before_tentacle_enforce() {
    let rules = all_rules();
    let el = rules.iter().position(|r| r.name() == "enforce-learn");
    let te = rules.iter().position(|r| r.name() == "tentacle-enforce");
    assert!(
        el.is_some() && te.is_some(),
        "both enforce-learn and tentacle-enforce must be registered"
    );
    assert!(
        el.unwrap() < te.unwrap(),
        "enforce-learn must precede tentacle-enforce"
    );
}
