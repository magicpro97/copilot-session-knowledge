use super::*;

// --- LearnReminderRule ---

#[test]
fn learn_reminder_returns_none_for_bash_without_learn_py() {
    let rule = LearnReminderRule;
    let data = json!({"toolName": "bash", "toolArgs": {"command": "ls -la"}});
    assert!(
        rule.evaluate("postToolUse", &data).is_none(),
        "bash without learn.py must return None"
    );
}

#[test]
fn learn_reminder_emits_skill_followup_for_bash_with_learn_py() {
    // bash + learn.py detected: marker written and skill follow-up emitted.
    let _guard = env_lock();
    let tmp = std::env::temp_dir().join("sk_learn_reminder_learn_py");
    let _ = std::fs::remove_dir_all(&tmp);
    let old_home = std::env::var_os("HOME");
    let old_up = std::env::var_os("USERPROFILE");
    std::env::set_var("HOME", &tmp);
    std::env::set_var("USERPROFILE", &tmp);

    let rule = LearnReminderRule;
    let data = json!({
        "toolName": "bash",
        "toolArgs": {"command": "python3 ~/.copilot/tools/learn.py --mistake 'title' 'desc'"},
        "toolResult": {"resultType": "success"}
    });
    let result = rule.evaluate("postToolUse", &data);

    match old_home {
        Some(v) => std::env::set_var("HOME", v),
        None => std::env::remove_var("HOME"),
    }
    match old_up {
        Some(v) => std::env::set_var("USERPROFILE", v),
        None => std::env::remove_var("USERPROFILE"),
    }
    let _ = std::fs::remove_dir_all(&tmp);

    let text = result
        .expect("bash + learn.py must emit skill follow-up")
        .get("message")
        .and_then(|v| v.as_str())
        .unwrap_or("")
        .to_string();
    assert!(
        text.contains("SKILL UPDATE CHECK") && text.contains("skill-creator"),
        "learn follow-up must mention skill update and skill-creator"
    );
}

#[test]
fn learn_reminder_emits_skill_followup_for_bash_with_sk_learn() {
    let _guard = env_lock();
    let tmp = std::env::temp_dir().join("sk_learn_reminder_sk_learn");
    let _ = std::fs::remove_dir_all(&tmp);
    let old_home = std::env::var_os("HOME");
    let old_up = std::env::var_os("USERPROFILE");
    std::env::set_var("HOME", &tmp);
    std::env::set_var("USERPROFILE", &tmp);

    let rule = LearnReminderRule;
    let data = json!({
        "toolName": "bash",
        "toolArgs": {"command": "sk learn --pattern 'title' 'desc'"},
        "toolResult": {"resultType": "success"}
    });
    let result = rule.evaluate("postToolUse", &data);

    match old_home {
        Some(v) => std::env::set_var("HOME", v),
        None => std::env::remove_var("HOME"),
    }
    match old_up {
        Some(v) => std::env::set_var("USERPROFILE", v),
        None => std::env::remove_var("USERPROFILE"),
    }
    let _ = std::fs::remove_dir_all(&tmp);

    let text = result
        .expect("bash + sk learn must emit skill follow-up")
        .get("message")
        .and_then(|v| v.as_str())
        .unwrap_or("")
        .to_string();
    assert!(
        text.contains("SKILL UPDATE CHECK") && text.contains("skill-creator"),
        "sk learn follow-up must mention skill update and skill-creator"
    );
}

#[test]
fn learn_reminder_emits_info_for_task_complete_success() {
    let rule = LearnReminderRule;
    let data = json!({
        "toolName": "task_complete",
        "toolResult": {"resultType": "success"}
    });
    let result = rule.evaluate("postToolUse", &data);
    assert!(result.is_some(), "task_complete success must emit reminder");
    let msg = result.unwrap();
    let text = msg["message"].as_str().expect("must have message field");
    assert!(
        text.contains("LEARN REMINDER"),
        "message must mention LEARN REMINDER"
    );
    assert!(
        text.contains("SKILL UPDATE CHECK") && text.contains("skill-creator"),
        "message must mention skill update and skill-creator"
    );
    // Ensure it is informational only (no deny key).
    assert!(
        msg.get("permissionDecision").is_none(),
        "LearnReminderRule must never produce a deny"
    );
}

#[test]
fn learn_reminder_returns_none_for_task_complete_non_success() {
    let rule = LearnReminderRule;
    // resultType absent → treated as non-success
    let data = json!({"toolName": "task_complete", "toolResult": {"resultType": "failure"}});
    assert!(
        rule.evaluate("postToolUse", &data).is_none(),
        "non-success task_complete must return None"
    );
}

#[test]
fn learn_reminder_returns_none_for_task_complete_missing_result() {
    let rule = LearnReminderRule;
    // toolResult absent entirely → non-success → None
    let data = json!({"toolName": "task_complete"});
    assert!(
        rule.evaluate("postToolUse", &data).is_none(),
        "task_complete without toolResult must return None"
    );
}

#[test]
fn learn_reminder_returns_none_for_unknown_tool() {
    let rule = LearnReminderRule;
    let data = json!({"toolName": "view"});
    assert!(
        rule.evaluate("postToolUse", &data).is_none(),
        "unknown tool must return None"
    );
}

#[test]
fn learn_reminder_fires_only_on_posttooluse() {
    let rule = LearnReminderRule;
    assert!(rule.events().contains(&"postToolUse"));
    assert!(!rule.events().contains(&"preToolUse"));
    assert!(!rule.events().contains(&"sessionStart"));
}

#[test]
fn learn_reminder_applies_to_bash_and_task_complete() {
    let rule = LearnReminderRule;
    assert!(rule.tools().contains(&"bash"));
    assert!(rule.tools().contains(&"task_complete"));
    assert!(!rule.tools().contains(&"edit"));
}

#[test]
fn command_invokes_learn_py_detects_python3_variant() {
    assert!(command_invokes_learn_py(
        "python3 ~/.copilot/tools/learn.py --mistake 'T' 'D'"
    ));
}

#[test]
fn command_invokes_learn_py_detects_python_variant() {
    assert!(command_invokes_learn_py(
        "python learn.py --pattern 'P' 'D'"
    ));
}

#[test]
fn command_invokes_learn_py_detects_sk_learn() {
    assert!(command_invokes_learn_py("sk learn --mistake 'T' 'D'"));
    assert!(command_invokes_learn_py("sk.exe learn --mistake 'T' 'D'"));
}

#[test]
fn command_invokes_learn_py_rejects_no_python_prefix() {
    // "learn.py" present but no python prefix → false
    assert!(!command_invokes_learn_py("cat learn.py"));
    assert!(!command_invokes_learn_py("echo learn.py"));
    assert!(!command_invokes_learn_py("learn.py --help"));
}

// --- wave11: EnforceLearnRule ---

#[test]
fn enforce_learn_fires_on_pretooluse_only() {
    let rule = EnforceLearnRule;
    assert!(rule.events().contains(&"preToolUse"));
    assert!(!rule.events().contains(&"postToolUse"));
}

#[test]
fn enforce_learn_covers_edit_create_bash_task_complete() {
    let rule = EnforceLearnRule;
    assert!(rule.tools().contains(&"edit"));
    assert!(rule.tools().contains(&"create"));
    assert!(rule.tools().contains(&"bash"));
    assert!(rule.tools().contains(&"task_complete"));
}

#[test]
fn enforce_learn_returns_none_for_edit_non_code_file() {
    let _guard = env_lock();
    // Use isolated HOME so tamper marker and learn-done markers are absent.
    let tmp = std::env::temp_dir().join("sk_enforce_learn_noncodedit");
    let _ = std::fs::remove_dir_all(&tmp);
    std::fs::create_dir_all(tmp.join(".copilot").join("markers")).expect("create markers dir");

    let old_home = std::env::var_os("HOME");
    let old_up = std::env::var_os("USERPROFILE");
    std::env::set_var("HOME", &tmp);
    std::env::set_var("USERPROFILE", &tmp);

    // edit on a .txt file (not code extension) → None (no counter write).
    let rule = EnforceLearnRule;
    let data = json!({
        "toolName": "edit",
        "toolArgs": {"path": "notes.txt"}
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

    assert!(result.is_none(), "edit on non-code file must return None");
}

#[test]
fn enforce_learn_increments_counter_on_code_edit() {
    let _guard = env_lock();
    let tmp = std::env::temp_dir().join("sk_enforce_learn_counter_test");
    let _ = std::fs::remove_dir_all(&tmp);
    let mdir = tmp.join(".copilot").join("markers");
    std::fs::create_dir_all(&mdir).expect("create markers dir");

    let old_home = std::env::var_os("HOME");
    let old_up = std::env::var_os("USERPROFILE");
    std::env::set_var("HOME", &tmp);
    std::env::set_var("USERPROFILE", &tmp);

    let rule = EnforceLearnRule;
    let data = json!({
        "toolName": "edit",
        "toolArgs": {"path": "src/main.py"}
    });
    let result = rule.evaluate("preToolUse", &data);

    // Counter must have been incremented.
    let counter_path = mdir.join("code-edit-count");
    let count = marker_auth::verify_counter(&counter_path);

    match old_home {
        Some(v) => std::env::set_var("HOME", v),
        None => std::env::remove_var("HOME"),
    }
    match old_up {
        Some(v) => std::env::set_var("USERPROFILE", v),
        None => std::env::remove_var("USERPROFILE"),
    }
    let _ = std::fs::remove_dir_all(&tmp);

    assert!(result.is_none(), "edit must return None (never deny)");
    assert_eq!(count, 1, "counter must be 1 after one code edit");
}

#[test]
fn enforce_learn_returns_none_for_non_commit_bash() {
    let _guard = env_lock();
    // Isolated HOME so no tamper marker fires.
    let tmp = std::env::temp_dir().join("sk_enforce_learn_noncommit");
    let _ = std::fs::remove_dir_all(&tmp);
    std::fs::create_dir_all(tmp.join(".copilot").join("markers")).expect("create markers dir");

    let old_home = std::env::var_os("HOME");
    let old_up = std::env::var_os("USERPROFILE");
    std::env::set_var("HOME", &tmp);
    std::env::set_var("USERPROFILE", &tmp);

    let rule = EnforceLearnRule;
    let data = json!({
        "toolName": "bash",
        "toolArgs": {"command": "ls -la"}
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

    assert!(result.is_none(), "non-commit bash must return None");
}

#[test]
fn enforce_learn_allows_git_commit_below_threshold() {
    let _guard = env_lock();
    let tmp = std::env::temp_dir().join("sk_enforce_learn_below_thresh");
    let _ = std::fs::remove_dir_all(&tmp);
    let mdir = tmp.join(".copilot").join("markers");
    std::fs::create_dir_all(&mdir).expect("create markers dir");

    // Write counter = 1 (below threshold of 3).
    let counter_path = mdir.join("code-edit-count");
    marker_auth::sign_counter(&counter_path, 1).expect("sign_counter");

    let old_home = std::env::var_os("HOME");
    let old_up = std::env::var_os("USERPROFILE");
    std::env::set_var("HOME", &tmp);
    std::env::set_var("USERPROFILE", &tmp);

    let rule = EnforceLearnRule;
    let data = json!({
        "toolName": "bash",
        "toolArgs": {"command": "git commit -m 'fix'"}
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
        "git commit below threshold must return None (allow)"
    );
}

#[test]
fn enforce_learn_denies_git_commit_above_threshold() {
    let _guard = env_lock();
    let tmp = std::env::temp_dir().join("sk_enforce_learn_above_thresh");
    let _ = std::fs::remove_dir_all(&tmp);
    let mdir = tmp.join(".copilot").join("markers");
    std::fs::create_dir_all(&mdir).expect("create markers dir");

    // Override HOME/USERPROFILE before signing so that sign and verify
    // both resolve the same marker secret from the temp dir (none).
    let old_home = std::env::var_os("HOME");
    let old_up = std::env::var_os("USERPROFILE");
    std::env::set_var("HOME", &tmp);
    std::env::set_var("USERPROFILE", &tmp);

    // Write counter = 5 (above threshold of 3).
    let counter_path = mdir.join("code-edit-count");
    marker_auth::sign_counter(&counter_path, 5).expect("sign_counter");

    let rule = EnforceLearnRule;
    let data = json!({
        "toolName": "bash",
        "toolArgs": {"command": "git commit -m 'fix'"}
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

    assert!(result.is_some(), "git commit above threshold must deny");
    let v = result.unwrap();
    assert_eq!(v["permissionDecision"].as_str().unwrap_or(""), "deny");
    assert!(
        v["permissionDecisionReason"]
            .as_str()
            .unwrap_or("")
            .contains("LEARN"),
        "deny reason must mention LEARN"
    );
}

#[test]
fn enforce_learn_allows_git_commit_with_learn_done_marker() {
    let _guard = env_lock();
    let tmp = std::env::temp_dir().join("sk_enforce_learn_learn_done");
    let _ = std::fs::remove_dir_all(&tmp);
    let mdir = tmp.join(".copilot").join("markers");
    std::fs::create_dir_all(&mdir).expect("create markers dir");

    // Counter above threshold but learn-done marker present.
    marker_auth::sign_counter(&mdir.join("code-edit-count"), 5).expect("sign_counter");
    marker_auth::sign_marker(&mdir.join("learn-done"), "learn-done").expect("sign_marker");

    let old_home = std::env::var_os("HOME");
    let old_up = std::env::var_os("USERPROFILE");
    std::env::set_var("HOME", &tmp);
    std::env::set_var("USERPROFILE", &tmp);

    let rule = EnforceLearnRule;
    let data = json!({
        "toolName": "bash",
        "toolArgs": {"command": "git commit -m 'fix'"}
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
        "git commit with learn-done marker must return None (allow)"
    );
}

#[test]
fn enforce_learn_denies_task_complete_above_threshold() {
    let _guard = env_lock();
    let tmp = std::env::temp_dir().join("sk_enforce_learn_tc_deny");
    let _ = std::fs::remove_dir_all(&tmp);
    let mdir = tmp.join(".copilot").join("markers");
    std::fs::create_dir_all(&mdir).expect("create markers dir");

    // Override HOME/USERPROFILE before signing so that sign and verify
    // both resolve the same marker secret from the temp dir (none).
    let old_home = std::env::var_os("HOME");
    let old_up = std::env::var_os("USERPROFILE");
    std::env::set_var("HOME", &tmp);
    std::env::set_var("USERPROFILE", &tmp);

    marker_auth::sign_counter(&mdir.join("code-edit-count"), 4).expect("sign_counter");

    let rule = EnforceLearnRule;
    let data = json!({
        "toolName": "task_complete",
        "toolArgs": {}
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

    assert!(result.is_some(), "task_complete above threshold must deny");
    let v = result.unwrap();
    assert_eq!(v["permissionDecision"].as_str().unwrap_or(""), "deny");
    assert!(
        v["permissionDecisionReason"]
            .as_str()
            .unwrap_or("")
            .contains("LEARN"),
        "deny reason must mention LEARN"
    );
}

#[test]
fn enforce_learn_denies_when_tamper_marker_present() {
    let _guard = env_lock();
    let tmp = std::env::temp_dir().join("sk_enforce_learn_tampered");
    let _ = std::fs::remove_dir_all(&tmp);
    let mdir = tmp.join(".copilot").join("markers");
    std::fs::create_dir_all(&mdir).expect("create markers dir");
    marker_auth::sign_marker(&mdir.join("hooks-tampered"), "hooks-tampered")
        .expect("sign tamper marker");

    let old_home = std::env::var_os("HOME");
    let old_up = std::env::var_os("USERPROFILE");
    std::env::set_var("HOME", &tmp);
    std::env::set_var("USERPROFILE", &tmp);

    let rule = EnforceLearnRule;
    let data = json!({
        "toolName": "bash",
        "toolArgs": {"command": "git commit -m 'fix'"}
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

#[test]
fn enforce_learn_tamper_marker_allows_lock_hooks_recovery_only() {
    let _guard = env_lock();
    let tmp = std::env::temp_dir().join("sk_enforce_learn_recovery");
    let _ = std::fs::remove_dir_all(&tmp);
    let mdir = tmp.join(".copilot").join("markers");
    std::fs::create_dir_all(&mdir).expect("create markers dir");
    marker_auth::sign_marker(&mdir.join("hooks-tampered"), "hooks-tampered")
        .expect("sign tamper marker");

    let old_home = std::env::var_os("HOME");
    let old_up = std::env::var_os("USERPROFILE");
    std::env::set_var("HOME", &tmp);
    std::env::set_var("USERPROFILE", &tmp);

    let rule = EnforceLearnRule;

    let recovery = json!({
        "toolName": "bash",
        "toolArgs": {"command": "sudo python3 $HOME/.copilot/tools/install.py --lock-hooks"}
    });
    let recovery_result = rule.evaluate("preToolUse", &recovery);

    let ls = json!({"toolName": "bash", "toolArgs": {"command": "ls -la"}});
    let ls_result = rule.evaluate("preToolUse", &ls);

    let chained = json!({
        "toolName": "bash",
        "toolArgs": {"command": "sudo python3 $HOME/.copilot/tools/install.py --lock-hooks && echo done"}
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

// --- wave13: AutoBugDetectorRule ---

#[test]
fn auto_bug_detector_fires_on_posttooluse_only() {
    let rule = AutoBugDetectorRule;
    assert!(rule.events().contains(&"postToolUse"));
    assert!(!rule.events().contains(&"preToolUse"));
    assert!(!rule.events().contains(&"sessionStart"));
}

#[test]
fn auto_bug_detector_covers_edit_and_create() {
    let rule = AutoBugDetectorRule;
    assert!(rule.tools().contains(&"edit"));
    assert!(rule.tools().contains(&"create"));
    assert!(!rule.tools().contains(&"bash"));
}

#[test]
fn all_rules_includes_auto_bug_detector() {
    let rules = all_rules();
    assert!(
        rules.iter().any(|r| r.name() == "auto-bug-detector"),
        "all_rules must include auto-bug-detector (wave13 issue #86)"
    );
}

#[test]
fn all_rules_auto_bug_detector_after_test_reminder() {
    let rules = all_rules();
    let abd = rules.iter().position(|r| r.name() == "auto-bug-detector");
    let tr = rules.iter().position(|r| r.name() == "test-reminder");
    assert!(
        abd.is_some() && tr.is_some(),
        "both auto-bug-detector and test-reminder must be registered"
    );
    assert!(
        tr.unwrap() < abd.unwrap(),
        "auto-bug-detector must come after test-reminder (mirrors Python registry order)"
    );
}

#[test]
fn all_rules_auto_bug_detector_before_tentacle_suggest() {
    let rules = all_rules();
    let abd = rules.iter().position(|r| r.name() == "auto-bug-detector");
    let ts = rules.iter().position(|r| r.name() == "tentacle-suggest");
    assert!(
        abd.is_some() && ts.is_some(),
        "both auto-bug-detector and tentacle-suggest must be registered"
    );
    assert!(
        abd.unwrap() < ts.unwrap(),
        "auto-bug-detector must come before tentacle-suggest (mirrors Python registry order)"
    );
}

#[test]
fn auto_bug_detect_edit_error_handling() {
    // Adding try/except in new_str (absent in old_str) → error-handling
    let detections = auto_bug_detect_edit(
        "x = risky()",
        "try:\n    x = risky()\nexcept ValueError:\n    pass",
    );
    assert!(
        detections.iter().any(|(cat, _)| *cat == "error-handling"),
        "try/except added → error-handling detected"
    );
}

#[test]
fn auto_bug_detect_edit_error_handling_already_present() {
    // Both old and new have try/except → no new detection
    let old = "try:\n    x = a()\nexcept ValueError:\n    pass";
    let new = "try:\n    x = a()\n    y = b()\nexcept ValueError:\n    pass";
    let detections = auto_bug_detect_edit(old, new);
    assert!(
        !detections.iter().any(|(cat, _)| *cat == "error-handling"),
        "error-handling already present in old → no detection"
    );
}

#[test]
fn auto_bug_detect_edit_inline_comment_throw_new_error_no_detect() {
    let detections = auto_bug_detect_edit(
        "function foo() { return 1; }",
        "function foo() { return 1; } // throw new TypeError if invalid",
    );
    assert!(
        !detections.iter().any(|(cat, _)| *cat == "error-handling"),
        "inline comment `throw new TypeError` must NOT trigger error-handling"
    );
}

#[test]
fn auto_bug_detect_edit_inline_comment_catch_no_detect() {
    let detections = auto_bug_detect_edit(
        "function foo() { return fetch(url); }",
        "function foo() { return fetch(url); } // always use .catch() for errors",
    );
    assert!(
        !detections.iter().any(|(cat, _)| *cat == "error-handling"),
        "inline comment `.catch()` must NOT trigger error-handling"
    );
}

#[test]
fn auto_bug_detect_edit_null_safety() {
    let detections = auto_bug_detect_edit(
        "return obj.value",
        "if obj is None:\n    return None\nreturn obj.value",
    );
    assert!(
        detections.iter().any(|(cat, _)| *cat == "null-safety"),
        "is None guard added → null-safety detected"
    );
}

#[test]
fn auto_bug_detect_edit_async_fix() {
    let detections = auto_bug_detect_edit(
        "def fetch():\n    return requests.get(url)",
        "async def fetch():\n    return await session.get(url)",
    );
    assert!(
        detections.iter().any(|(cat, _)| *cat == "async-fix"),
        "async def + await added → async-fix detected"
    );
}

#[test]
fn auto_bug_detect_edit_type_fix() {
    let detections = auto_bug_detect_edit(
        "def greet(name):\n    return name",
        "def greet(name: str) -> str:\n    return name",
    );
    assert!(
        detections.iter().any(|(cat, _)| *cat == "type-fix"),
        "type annotation added → type-fix detected"
    );
}

#[test]
fn auto_bug_detect_edit_type_fix_compact_annotation() {
    let detections = auto_bug_detect_edit(
        "def greet(name):\n    return name",
        "def greet(name:str)->str:\n    return name",
    );
    assert!(
        detections.iter().any(|(cat, _)| *cat == "type-fix"),
        "compact `name:str` annotation must still detect as type-fix"
    );
}

#[test]
fn auto_bug_detect_edit_empty_inputs() {
    // Empty old and new → no detections
    let detections = auto_bug_detect_edit("", "");
    assert!(detections.is_empty(), "empty inputs → no detections");
}

#[test]
fn auto_bug_detector_rule_no_new_str() {
    let rule = AutoBugDetectorRule;
    // Missing new_str → None (fail-open)
    let data = json!({
        "toolName": "edit",
        "toolArgs": {"path": "src/main.py", "old_str": "x = 1"}
    });
    let result = rule.evaluate("postToolUse", &data);
    assert!(result.is_none(), "missing new_str → None");
}

#[test]
fn auto_bug_detector_rule_no_path() {
    let rule = AutoBugDetectorRule;
    let data = json!({
        "toolName": "edit",
        "toolArgs": {"old_str": "x", "new_str": "try:\n    x()\nexcept ValueError:\n    pass"}
    });
    let result = rule.evaluate("postToolUse", &data);
    assert!(result.is_none(), "missing path → None (fail-open)");
}

#[test]
fn auto_bug_detector_rule_non_dict_toolargs() {
    let rule = AutoBugDetectorRule;
    let data = json!({
        "toolName": "edit",
        "toolArgs": null
    });
    let result = rule.evaluate("postToolUse", &data);
    assert!(result.is_none(), "null toolArgs → None (fail-open)");
}

#[test]
fn auto_bug_detector_rule_create_error_handling_none() {
    // create: error-handling has create_conf 0.0 → still returns None
    let rule = AutoBugDetectorRule;
    let data = json!({
        "toolName": "create",
        "toolArgs": {
            "path": "src/foo.py",
            "file_text": "try:\n    x()\nexcept ValueError:\n    pass\n"
        }
    });
    let result = rule.evaluate("postToolUse", &data);
    assert!(
        result.is_none(),
        "create with try/except → None (error-handling create_conf=0.0)"
    );
}

#[test]
fn auto_bug_detect_create_null_safety() {
    // create: null-safety enabled at 0.62
    let detections = auto_bug_detect_create(
        "def get(obj):\n    if obj is None:\n        return None\n    return obj.value\n",
    );
    assert!(
        detections
            .iter()
            .any(|(cat, conf)| *cat == "null-safety" && *conf >= 0.62),
        "null guard in create file_text → null-safety detected (create_conf=0.62)"
    );
}

#[test]
fn auto_bug_detect_create_async_fix() {
    // create: async-fix enabled at 0.62
    let detections = auto_bug_detect_create("async def handle():\n    return await fetch()\n");
    assert!(
        detections
            .iter()
            .any(|(cat, conf)| *cat == "async-fix" && *conf >= 0.62),
        "async def + await in create file_text → async-fix detected (create_conf=0.62)"
    );
}

#[test]
fn auto_bug_detect_create_type_fix_excluded() {
    // type-fix stays excluded on create
    let detections = auto_bug_detect_create("def greet(name: str) -> str:\n    return name\n");
    assert!(
        !detections.iter().any(|(cat, _)| *cat == "type-fix"),
        "type annotation in create file_text → no type-fix detection (excluded)"
    );
}

#[test]
fn auto_bug_detect_edit_raise_stop_iteration_no_suppress() {
    // Regression for Rust parity bug: old code has `raise StopIteration` (not an Error)
    // and new code adds `try/except ValueError`.  Python detects this; Rust must too.
    let old = "def next_val(it):\n    raise StopIteration\n";
    let new = "def next_val(it):\n    raise StopIteration\n    try:\n        return next(it)\n    except ValueError:\n        return None\n";
    let detections = auto_bug_detect_edit(old, new);
    assert!(
        detections.iter().any(|(cat, _)| *cat == "error-handling"),
        "old code has raise StopIteration (non-Error) + new adds try/except → \
             error-handling detected (parity with Python)"
    );
}

#[test]
fn auto_bug_detect_edit_no_false_positive_no_change() {
    // Identical old and new → no detections (nothing was added)
    let src = "def foo():\n    return bar()";
    let detections = auto_bug_detect_edit(src, src);
    assert!(detections.is_empty(), "identical old/new → no detections");
}

// --- Blocker 1 regressions: session-state path skip ---

#[test]
fn auto_bug_detector_edit_skips_session_state_path() {
    // edit on a session-state file must return None even if the diff has a detectable pattern.
    let rule = AutoBugDetectorRule;
    let data = json!({
        "toolName": "edit",
        "toolArgs": {
            "path": ".copilot/session-state/abc-123/plan.md",
            "old_str": "x = 1",
            "new_str": "try:\n    x()\nexcept ValueError:\n    pass"
        }
    });
    assert!(
        rule.evaluate("postToolUse", &data).is_none(),
        "edit on session-state path must be skipped (mirrors Python is_session_path guard)"
    );
}

#[test]
fn auto_bug_detector_create_skips_session_state_path() {
    // create on a session-state file must return None even with null-safety patterns.
    let rule = AutoBugDetectorRule;
    let data = json!({
        "toolName": "create",
        "toolArgs": {
            "path": "C:\\Users\\user\\.copilot\\session-state\\abc\\notes.md",
            "file_text": "if obj is None:\n    return None\nasync def handle():\n    pass"
        }
    });
    assert!(
        rule.evaluate("postToolUse", &data).is_none(),
        "create on session-state path must be skipped (mirrors Python is_session_path guard)"
    );
}

// --- Blocker 2 regressions: guard-clause adjacency ---

#[test]
fn auto_bug_detect_edit_guard_clause_disjoint_no_detect() {
    // Negative regression: disjoint `if ...:` body (not `return`) + later `return`
    // must NOT be detected as a guard-clause addition.
    let old = "def process(data):\n    return data";
    let new =
        "def process(data):\n    if result.is_valid:\n        do_something()\n    return result";
    let detections = auto_bug_detect_edit(old, new);
    assert!(
        !detections.iter().any(|(cat, _)| *cat == "guard-clause"),
        "disjoint if-colon + later return must NOT be a guard-clause detection"
    );
}

#[test]
fn auto_bug_detect_edit_guard_clause_adjacent_detects() {
    // Positive regression: `if <cond>:\n    return` adjacency still detects.
    let old = "def validate(val):\n    process(val)";
    let new = "def validate(val):\n    if val is None:\n        return None\n    process(val)";
    let detections = auto_bug_detect_edit(old, new);
    // "is None" also triggers null-safety; guard-clause must detect via adjacency.
    assert!(
        detections.iter().any(|(cat, _)| *cat == "guard-clause"),
        "adjacent if-colon + return must still be detected as guard-clause"
    );
}

// --- Blocker 3 regressions: error-handling token-level matching ---

#[test]
fn auto_bug_detect_edit_stray_error_in_comment_no_suppress() {
    // Negative regression for old-code check:
    // old code has `raise StopIteration` + a comment containing "Error".
    // That stray "Error" must NOT suppress detection of a newly added try/except.
    let old = "# Error handling is not implemented here\ndef gen():\n    raise StopIteration\n";
    let new = "# Error handling is not implemented here\ndef gen():\n    raise StopIteration\n    try:\n        return next(it)\n    except ValueError:\n        return None\n";
    let detections = auto_bug_detect_edit(old, new);
    assert!(
        detections.iter().any(|(cat, _)| *cat == "error-handling"),
        "stray 'Error' in comment + raise StopIteration must NOT suppress new try/except detection"
    );
}

#[test]
fn auto_bug_detect_edit_stray_error_in_new_code_no_spurious() {
    // Negative regression for new-code check:
    // new code has bare `raise StopIteration` + a stray "Error" in a comment.
    // That must NOT count as new error-handling by itself.
    let old = "def gen():\n    yield 1\n";
    let new = "def gen():\n    # Error: this generator stops early\n    raise StopIteration\n";
    let detections = auto_bug_detect_edit(old, new);
    assert!(
        !detections.iter().any(|(cat, _)| *cat == "error-handling"),
        "bare raise StopIteration + stray 'Error' comment must NOT be detected as error-handling"
    );
}

// --- Blocker 6 regressions: is_auto_bug_session_path narrower than is_track_session_path ---

#[test]
fn is_auto_bug_session_path_skips_copilot_session_state_segment() {
    // Paths with `.copilot/session-state` must be skipped.
    assert!(is_auto_bug_session_path(
        ".copilot/session-state/abc/plan.md"
    ));
    assert!(is_auto_bug_session_path(
        "/home/user/.copilot/session-state/abc-123/checkpoints/01.md"
    ));
}

#[test]
fn is_auto_bug_session_path_does_not_skip_project_session_state_filename() {
    // Legitimate project files whose names merely contain "session-state" must NOT be skipped.
    assert!(
        !is_auto_bug_session_path("src/session-state-manager.py"),
        "src/session-state-manager.py must NOT be skipped by is_auto_bug_session_path"
    );
    assert!(
        !is_auto_bug_session_path("docs/session-state.md"),
        "docs/session-state.md must NOT be skipped by is_auto_bug_session_path"
    );
    assert!(
        !is_auto_bug_session_path("tests/test_session_state.py"),
        "tests/test_session_state.py must NOT be skipped by is_auto_bug_session_path"
    );
}

#[test]
fn auto_bug_detector_edit_does_not_skip_project_session_state_file() {
    // AutoBugDetectorRule must NOT skip a legitimate project file whose name contains
    // "session-state" but is not under .copilot/session-state/.
    // Note: the rule calls learn.py which may fail in test, but the point is evaluate()
    // must not return None from the session-path guard for this path.
    // We verify by checking that the path guard doesn't short-circuit:
    // is_auto_bug_session_path("src/session-state-manager.py") must be false.
    assert!(
        !is_auto_bug_session_path("src/session-state-manager.py"),
        "AutoBugDetectorRule must not skip src/session-state-manager.py \
             (is_auto_bug_session_path should return false for ordinary project paths)"
    );
}

// --- Blocker 7 regressions: `if not` guard-clause identifier check ---

#[test]
fn auto_bug_has_guard_clause_if_not_bare_identifier_detects() {
    // Positive: `if not foo:` → guard-clause (bare identifier).
    assert!(
        auto_bug_has_guard_clause("if not foo:\n    return None\n"),
        "if not foo: must be detected as guard-clause"
    );
}

#[test]
fn auto_bug_has_guard_clause_if_not_dot_path_detects() {
    // Positive: `if not obj.value:` → guard-clause (dot-path identifier).
    assert!(
        auto_bug_has_guard_clause("if not obj.value:\n    return\n"),
        "if not obj.value: must be detected as guard-clause"
    );
}

#[test]
fn auto_bug_has_guard_clause_if_not_isinstance_no_detect() {
    // Negative: `if not isinstance(x, T):` must NOT be detected.
    // `isinstance` is a function call, not a bare identifier/dot-path.
    assert!(
            !auto_bug_has_guard_clause(
                "def process(x, T):\n    if not isinstance(x, T):\n        do_something()\n    return result\n"
            ),
            "if not isinstance(x, T): without adjacent return must NOT be detected as guard-clause"
        );
}

#[test]
fn auto_bug_has_guard_clause_if_not_paren_expr_no_detect() {
    // Negative: `if not (a and b):` must NOT be detected (parenthesized expression).
    assert!(
        !auto_bug_has_guard_clause(
            "def check(a, b):\n    if not (a and b):\n        do_something()\n    return True\n"
        ),
        "if not (a and b): without adjacent return must NOT be detected as guard-clause"
    );
}

#[test]
fn auto_bug_detect_edit_guard_clause_if_not_isinstance_no_detect() {
    // End-to-end negative regression through auto_bug_detect_edit:
    // adding `if not isinstance(x, T):` (without adjacent return) must NOT detect.
    let old = "def process(data):\n    return data";
    let new = "def process(data, T):\n    if not isinstance(data, T):\n        raise TypeError()\n    return data";
    let detections = auto_bug_detect_edit(old, new);
    assert!(
        !detections.iter().any(|(cat, _)| *cat == "guard-clause"),
        "if not isinstance(x, T): with non-return body must NOT be detected as guard-clause"
    );
}

// --- Blocker 8 regressions: `except` word-boundary / noexcept ---

#[test]
fn auto_bug_has_error_indicator_noexcept_no_detect() {
    // `noexcept` in C++ code must NOT be treated as an error-handling indicator.
    assert!(
        !auto_bug_has_error_indicator("void foo() noexcept { return; }"),
        "noexcept must NOT be detected as an error-handling indicator"
    );
}

#[test]
fn auto_bug_detect_edit_noexcept_no_detect() {
    // End-to-end: adding `noexcept` to a function signature must NOT trigger error-handling.
    let old = "void foo() { return; }";
    let new = "void foo() noexcept { return; }";
    let detections = auto_bug_detect_edit(old, new);
    assert!(
        !detections.iter().any(|(cat, _)| *cat == "error-handling"),
        "adding noexcept to a function must NOT be detected as error-handling"
    );
}

#[test]
fn auto_bug_has_error_indicator_real_except_still_detects() {
    // `except ValueError:` at line-start must still be detected (word boundary check
    // only excludes the case where "except" is preceded by a word character).
    assert!(
        auto_bug_has_error_indicator("try:\n    x()\nexcept ValueError:\n    pass"),
        "standalone except ValueError must still be detected as error-handling"
    );
}

// --- Blocker 9 regressions: null-safety structured-form requirement ---

#[test]
fn auto_bug_null_safety_assert_is_none_no_detect() {
    // `assert x is None` must NOT be treated as a null-safety indicator —
    // only structured `if ... is None` forms count.
    assert!(
        !auto_bug_has_null_safety_indicator("assert x is None"),
        "assert x is None must NOT be detected as null-safety"
    );
}

#[test]
fn auto_bug_null_safety_return_is_none_no_detect() {
    // `return x is None` must NOT be treated as a null-safety indicator.
    assert!(
        !auto_bug_has_null_safety_indicator("return x is None"),
        "return x is None must NOT be detected as null-safety"
    );
}

#[test]
fn auto_bug_null_safety_assignment_is_none_no_detect() {
    // `x = result is None` must NOT be treated as a null-safety indicator.
    assert!(
        !auto_bug_has_null_safety_indicator("x = result is None"),
        "assignment `x = result is None` must NOT be detected as null-safety"
    );
}

#[test]
fn auto_bug_null_safety_comment_is_none_no_detect() {
    // A comment mentioning `is None` must NOT fire.
    assert!(
        !auto_bug_has_null_safety_indicator("# check if x is None before processing"),
        "comment containing `is None` must NOT be detected as null-safety"
    );
}

#[test]
fn auto_bug_null_safety_if_is_none_detects() {
    // `if x is None:` IS the structured form — must still detect.
    assert!(
        auto_bug_has_null_safety_indicator("if x is None:\n    return"),
        "if x is None: must be detected as null-safety"
    );
}

#[test]
fn auto_bug_detect_edit_null_safety_non_if_no_detect() {
    // End-to-end: editing a file that adds `return x is None` must NOT trigger
    // null-safety because it is not a conditional guard form.
    let old = "def is_missing(x):\n    return False";
    let new = "def is_missing(x):\n    return x is None";
    let detections = auto_bug_detect_edit(old, new);
    assert!(
        !detections.iter().any(|(cat, _)| *cat == "null-safety"),
        "adding `return x is None` must NOT trigger null-safety detection"
    );
}

// --- Blocker 10 regressions: `try {` JS/TS-style error-handling ---

#[test]
fn auto_bug_has_error_indicator_try_brace_space_detects() {
    // JS/TS `try { ... } catch (e) { ... }` must be detected as error-handling.
    assert!(
        auto_bug_has_error_indicator("try {\n  x();\n} catch (e) {\n  console.error(e);\n}"),
        "try {{}} catch style must be detected as error-handling"
    );
}

#[test]
fn auto_bug_detect_edit_try_brace_js_detects() {
    // End-to-end: adding a JS/TS `try { ... } catch (e) { ... }` block must
    // trigger the error-handling category.
    let old = "function call() { return fetch(url); }";
    let new = "function call() {\n  try {\n    return fetch(url);\n  } catch (e) {\n    return null;\n  }\n}";
    let detections = auto_bug_detect_edit(old, new);
    assert!(
        detections.iter().any(|(cat, _)| *cat == "error-handling"),
        "adding JS/TS try {{}} catch => error-handling detected"
    );
}

// --- Blocker 18 regressions: null-comparison `== null` must require `if` prefix ---

#[test]
fn auto_bug_null_safety_eq_null_bare_no_detect() {
    // Bare `x == null` (not in an `if`) must NOT detect as null-safety.
    assert!(
        !auto_bug_has_null_safety_indicator("x == null"),
        "bare `x == null` must NOT be detected as null-safety"
    );
}

#[test]
fn auto_bug_null_safety_assert_eq_null_no_detect() {
    // `assert x == null` must NOT detect as null-safety.
    assert!(
        !auto_bug_has_null_safety_indicator("assert x == null"),
        "`assert x == null` must NOT be detected as null-safety"
    );
}

#[test]
fn auto_bug_null_safety_return_eq_null_no_detect() {
    // `return x == null` must NOT detect as null-safety.
    assert!(
        !auto_bug_has_null_safety_indicator("return x == null"),
        "`return x == null` must NOT be detected as null-safety"
    );
}

#[test]
fn auto_bug_null_safety_neq_null_bare_no_detect() {
    // Bare `x != null` (not in an `if`) must NOT detect as null-safety.
    assert!(
        !auto_bug_has_null_safety_indicator("x != null"),
        "bare `x != null` must NOT be detected as null-safety"
    );
}

#[test]
fn auto_bug_null_safety_strict_eq_null_bare_no_detect() {
    // Bare `x === null` must NOT detect as null-safety.
    assert!(
        !auto_bug_has_null_safety_indicator("x === null"),
        "bare `x === null` must NOT be detected as null-safety"
    );
}

#[test]
fn auto_bug_null_safety_strict_neq_null_bare_no_detect() {
    // Bare `x !== null` must NOT detect as null-safety.
    assert!(
        !auto_bug_has_null_safety_indicator("x !== null"),
        "bare `x !== null` must NOT be detected as null-safety"
    );
}

#[test]
fn auto_bug_null_safety_if_eq_null_detects() {
    // `if value == null` must be detected (positive regression).
    assert!(
        auto_bug_has_null_safety_indicator("if value == null:\n    return"),
        "`if value == null` must be detected as null-safety"
    );
}

#[test]
fn auto_bug_null_safety_if_neq_null_detects() {
    // `if value != null` must be detected.
    assert!(
        auto_bug_has_null_safety_indicator("if value != null:\n    handle()"),
        "`if value != null` must be detected as null-safety"
    );
}

#[test]
fn auto_bug_null_safety_if_strict_eq_null_detects() {
    // `if value === null` (JS/TS) must be detected.
    assert!(
        auto_bug_has_null_safety_indicator("if (value === null) {"),
        "`if (value === null)` must be detected as null-safety"
    );
}

// --- Blocker 19 regressions: `try` word boundary ---

#[test]
fn auto_bug_has_error_indicator_retry_no_detect() {
    // `retry:` contains `try:` as a substring but must NOT match.
    assert!(
        !auto_bug_has_error_indicator("retry:\n  x()"),
        "`retry:` must NOT be detected as error-handling"
    );
}

#[test]
fn auto_bug_detect_edit_retry_no_detect() {
    // End-to-end: adding `retry:` must NOT trigger error-handling.
    let old = "x = 1";
    let new = "retry:\n  x = call()";
    let detections = auto_bug_detect_edit(old, new);
    assert!(
        !detections.iter().any(|(cat, _)| *cat == "error-handling"),
        "adding `retry:` must NOT be detected as error-handling"
    );
}

#[test]
fn auto_bug_has_error_indicator_try_colon_detects() {
    // Standalone `try:` must still be detected after word-boundary fix.
    assert!(
        auto_bug_has_error_indicator("try:\n    x()\nexcept ValueError:\n    pass"),
        "`try:` must still be detected as error-handling"
    );
}

#[test]
fn auto_bug_has_error_indicator_try_brace_compact_detects() {
    // `try{` (compact, no space) must still be detected.
    assert!(
        auto_bug_has_error_indicator("try{\n  x();\n} catch(e) {}"),
        "`try{{` must still be detected as error-handling"
    );
}

// --- Blocker 21 regressions: `??` comment-only line must NOT detect ---

#[test]
fn auto_bug_null_safety_comment_double_question_mark_no_detect() {
    // `# Is this correct?? might be wrong` — comment-only line containing `??`
    // must NOT be treated as null-safety.
    assert!(
        !auto_bug_has_null_safety_indicator("# Is this correct?? might be wrong"),
        "comment-only `??` must NOT be detected as null-safety"
    );
}

#[test]
fn auto_bug_detect_edit_comment_double_question_mark_no_detect() {
    // End-to-end: adding `??` only in a comment must NOT trigger null-safety.
    let old = "return obj.value";
    let new = "# Is this correct?? might be wrong\nreturn obj.value";
    let detections = auto_bug_detect_edit(old, new);
    assert!(
        !detections.iter().any(|(cat, _)| *cat == "null-safety"),
        "adding `??` only in a comment must NOT trigger null-safety"
    );
}

#[test]
fn auto_bug_null_safety_code_double_question_mark_detects() {
    // `??` in real code (not a comment) must still be detected.
    assert!(
        auto_bug_has_null_safety_indicator("const x = foo ?? bar;"),
        "`??` in real code must be detected as null-safety"
    );
}

// --- Blocker 22 regressions: type-fix dict literals / config must NOT detect ---

#[test]
fn auto_bug_type_fix_dict_string_key_list_no_detect() {
    // `{'items': list, 'data': dict}` — string-keyed dict literal must NOT
    // trigger type-fix because `: list` / `: dict` follow a quote character.
    assert!(
        !auto_bug_has_type_annotation("schema = {'items': list, 'data': dict}"),
        "string-keyed dict literal must NOT be detected as type annotation"
    );
}

#[test]
fn auto_bug_type_fix_spaced_dict_string_key_list_no_detect() {
    assert!(
        !auto_bug_has_type_annotation("schema = {'items' : list, 'data' : dict}"),
        "spaced string-keyed dict literal must NOT be detected as type annotation"
    );
}

#[test]
fn auto_bug_detect_edit_type_fix_dict_literal_no_detect() {
    // End-to-end: adding a dict literal with string keys must NOT trigger type-fix.
    let old = "schema = {}";
    let new = "schema = {'items': list, 'data': dict}";
    let detections = auto_bug_detect_edit(old, new);
    assert!(
        !detections.iter().any(|(cat, _)| *cat == "type-fix"),
        "adding dict literal {{'items': list}} must NOT trigger type-fix"
    );
}

#[test]
fn auto_bug_detect_edit_type_fix_spaced_dict_literal_no_detect() {
    let old = "schema = {}";
    let new = "schema = {'items' : list, 'data' : dict}";
    let detections = auto_bug_detect_edit(old, new);
    assert!(
        !detections.iter().any(|(cat, _)| *cat == "type-fix"),
        "adding spaced dict literal {{'items' : list}} must NOT trigger type-fix"
    );
}

#[test]
fn auto_bug_type_fix_return_type_none_no_detect() {
    // `return_type: None` — config-like key-value with `None` must NOT trigger
    // type-fix because `None` is excluded from the indicator list (too ambiguous).
    assert!(
        !auto_bug_has_type_annotation("return_type: None"),
        "`return_type: None` must NOT be detected as type annotation"
    );
}

#[test]
fn auto_bug_detect_edit_return_type_none_no_detect() {
    // End-to-end: adding `return_type: None` must NOT trigger type-fix.
    let old = "cfg = {}";
    let new = "return_type: None";
    let detections = auto_bug_detect_edit(old, new);
    assert!(
        !detections.iter().any(|(cat, _)| *cat == "type-fix"),
        "adding `return_type: None` must NOT trigger type-fix"
    );
}

#[test]
fn auto_bug_type_fix_func_annotation_detects() {
    // `def greet(name: str) -> str:` — real function annotation must still detect.
    assert!(
        auto_bug_has_type_annotation("def greet(name: str) -> str:\n    return name"),
        "function parameter annotation must be detected as type annotation"
    );
}

// ── Blocker 1: inline trailing comment `??` / `?.` regressions ──

#[test]
fn auto_bug_null_safety_inline_trailing_comment_double_question_no_detect() {
    // `x = foo  # is this right?? maybe` — `??` is in a trailing Python comment,
    // not in the code portion.  Must NOT fire as a null-safety indicator.
    assert!(
        !auto_bug_has_null_safety_indicator("x = foo  # is this right?? maybe"),
        "`??` only in trailing `#` comment must NOT be detected as null-safety"
    );
}

#[test]
fn auto_bug_null_safety_inline_trailing_js_comment_optional_chain_no_detect() {
    // `const x = getData(); // no ?. used here` — `?.` is in a trailing `//` comment.
    // Must NOT fire as a null-safety indicator.
    assert!(
        !auto_bug_has_null_safety_indicator("const x = getData(); // no ?. used here"),
        "`?.` only in trailing `//` comment must NOT be detected as null-safety"
    );
}

#[test]
fn auto_bug_null_safety_code_before_comment_double_question_detects() {
    // `const x = foo ?? bar;  # assign` — `??` is in the code part (before `#`).
    // Must still detect.
    assert!(
        auto_bug_has_null_safety_indicator("const x = foo ?? bar;  # assign with fallback"),
        "`??` in code part before trailing `#` comment must still detect"
    );
}

#[test]
fn auto_bug_null_safety_code_before_js_comment_optional_chain_detects() {
    // `const v = obj?.value;  // safe` — `?.` is before `//` comment.
    // Must still detect.
    assert!(
        auto_bug_has_null_safety_indicator("const v = obj?.value;  // safe access"),
        "`?.` in code part before trailing `//` comment must still detect"
    );
}

#[test]
fn auto_bug_null_safety_comment_unwrap_or_no_detect() {
    assert!(
        !auto_bug_has_null_safety_indicator("# prefer value.unwrap_or(default)"),
        "comment-only `.unwrap_or(` must NOT be detected as null-safety"
    );
}

#[test]
fn auto_bug_null_safety_comment_ok_or_no_detect() {
    assert!(
        !auto_bug_has_null_safety_indicator("// prefer result.ok_or(err)"),
        "comment-only `.ok_or(` must NOT be detected as null-safety"
    );
}

#[test]
fn auto_bug_null_safety_code_unwrap_or_detects() {
    assert!(
        auto_bug_has_null_safety_indicator("return value.unwrap_or(default)"),
        "code-side `.unwrap_or(` must still detect as null-safety"
    );
}

#[test]
fn auto_bug_null_safety_code_ok_or_detects() {
    assert!(
        auto_bug_has_null_safety_indicator("return result.ok_or(err)"),
        "code-side `.ok_or(` must still detect as null-safety"
    );
}

#[test]
fn auto_bug_null_safety_if_line_trailing_comment_is_none_no_detect() {
    assert!(
        !auto_bug_has_null_safety_indicator("if condition:  # check if x is None later"),
        "`is None` only in trailing `#` comment on an if-line must NOT detect"
    );
}

#[test]
fn auto_bug_null_safety_if_line_trailing_comment_null_cmp_no_detect() {
    assert!(
        !auto_bug_has_null_safety_indicator("if (ready) { // compare == null later"),
        "`== null` only in trailing `//` comment on an if-line must NOT detect"
    );
}

#[test]
fn auto_bug_detect_edit_inline_comment_nullish_no_detect() {
    // End-to-end: only adding `??` inside a trailing JS comment must NOT detect.
    let old = "const x = getData();";
    let new = "const x = getData(); // no ?. used here";
    let detections = auto_bug_detect_edit(old, new);
    assert!(
        !detections.iter().any(|(cat, _)| *cat == "null-safety"),
        "adding `?.` only inside trailing `//` comment must NOT trigger null-safety"
    );
}

#[test]
fn auto_bug_detect_edit_old_if_comment_is_none_does_not_suppress_real_guard() {
    let old = "if condition:  # check if x is None later\n    return condition\n";
    let new = "if value is None:\n    return None\n";
    let detections = auto_bug_detect_edit(old, new);
    assert!(
        detections.iter().any(|(cat, _)| *cat == "null-safety"),
        "old trailing-comment `is None` must NOT suppress a real new null guard"
    );
}

#[test]
fn auto_bug_detect_edit_old_if_comment_null_cmp_does_not_suppress_real_guard() {
    let old = "if (ready) { // compare == null later\n  return ready;\n}\n";
    let new = "if (value == null) {\n  return fallback;\n}\n";
    let detections = auto_bug_detect_edit(old, new);
    assert!(
        detections.iter().any(|(cat, _)| *cat == "null-safety"),
        "old trailing-comment `== null` must NOT suppress a real new null guard"
    );
}

// ── Blocker 2: Rust type-annotation word-boundary regressions ──

#[test]
fn auto_bug_type_fix_yaml_type_string_no_detect() {
    // `type: string` — YAML/OpenAPI style value where `string` starts with `str`.
    // Must NOT trigger type-fix because there is no word boundary after `: str`.
    assert!(
        !auto_bug_has_type_annotation("  type: string"),
        "`type: string` (YAML value) must NOT be detected as type annotation"
    );
}

#[test]
fn auto_bug_type_fix_yaml_type_boolean_no_detect() {
    // `type: boolean` — `bool` is a prefix of `boolean`.
    assert!(
        !auto_bug_has_type_annotation("  type: boolean"),
        "`type: boolean` (YAML value) must NOT be detected as type annotation"
    );
}

#[test]
fn auto_bug_type_fix_yaml_type_integer_no_detect() {
    // `type: integer` — `int` is a prefix of `integer`.
    assert!(
        !auto_bug_has_type_annotation("  type: integer"),
        "`type: integer` (YAML value) must NOT be detected as type annotation"
    );
}

#[test]
fn auto_bug_detect_edit_yaml_type_string_no_detect() {
    // End-to-end: adding YAML `type: string` must NOT trigger type-fix.
    let old = "fields: {}";
    let new = "fields:\n  type: string\n  required: true\n";
    let detections = auto_bug_detect_edit(old, new);
    assert!(
        !detections.iter().any(|(cat, _)| *cat == "type-fix"),
        "adding YAML `type: string` must NOT trigger type-fix"
    );
}

#[test]
fn auto_bug_detect_edit_yaml_type_boolean_no_detect() {
    let old = "fields: {}";
    let new = "fields:\n  type: boolean\n";
    let detections = auto_bug_detect_edit(old, new);
    assert!(
        !detections.iter().any(|(cat, _)| *cat == "type-fix"),
        "adding YAML `type: boolean` must NOT trigger type-fix"
    );
}

#[test]
fn auto_bug_detect_edit_yaml_type_integer_no_detect() {
    let old = "fields: {}";
    let new = "fields:\n  type: integer\n";
    let detections = auto_bug_detect_edit(old, new);
    assert!(
        !detections.iter().any(|(cat, _)| *cat == "type-fix"),
        "adding YAML `type: integer` must NOT trigger type-fix"
    );
}

#[test]
fn auto_bug_type_fix_real_str_annotation_still_detects() {
    // `name: str` (real Python annotation) must still detect after the boundary fix.
    assert!(
        auto_bug_has_type_annotation("def foo(name: str) -> None:\n    pass"),
        "`name: str` (real annotation) must still be detected as type annotation"
    );
}

#[test]
fn auto_bug_type_fix_real_compact_annotation_still_detects() {
    assert!(
        auto_bug_has_type_annotation("def foo(name:str)->None:\n    pass"),
        "`name:str` (real annotation without spaces) must still be detected"
    );
}

// ── Blocker 3: code-extension gate regressions ──

#[test]
fn auto_bug_detector_edit_skips_readme_md() {
    // README.md is not a code file — must be skipped even when `??` is present.
    let rule = AutoBugDetectorRule;
    let data = serde_json::json!({
        "toolName": "edit",
        "toolArgs": {
            "path": "README.md",
            "old_str": "What?? maybe later",
            "new_str": "const x = foo ?? bar;"
        }
    });
    assert!(
        rule.evaluate("postToolUse", &data).is_none(),
        "edit on README.md must be skipped (not a code file)"
    );
}

#[test]
fn auto_bug_detector_edit_skips_type_fix_on_yaml() {
    let rule = AutoBugDetectorRule;
    let data = serde_json::json!({
        "toolName": "edit",
        "toolArgs": {
            "path": "workflow.yaml",
            "old_str": "jobs: {}\n",
            "new_str": "jobs:\n  timeout: int\n"
        }
    });
    assert!(
        rule.evaluate("postToolUse", &data).is_none(),
        "type-fix must be suppressed for .yaml edits"
    );
}

#[test]
fn auto_bug_detector_edit_skips_type_fix_on_yml() {
    let rule = AutoBugDetectorRule;
    let data = serde_json::json!({
        "toolName": "edit",
        "toolArgs": {
            "path": "workflow.yml",
            "old_str": "jobs: {}\n",
            "new_str": "jobs:\n  enabled: bool\n"
        }
    });
    assert!(
        rule.evaluate("postToolUse", &data).is_none(),
        "type-fix must be suppressed for .yml edits"
    );
}

#[test]
fn auto_bug_detector_create_skips_markdown_file() {
    // docs/notes.md is not a code file — must be skipped even with null guards.
    let rule = AutoBugDetectorRule;
    let data = serde_json::json!({
        "toolName": "create",
        "toolArgs": {
            "path": "docs/notes.md",
            "file_text": "if obj is None:\n    return None\n"
        }
    });
    assert!(
        rule.evaluate("postToolUse", &data).is_none(),
        "create on docs/notes.md must be skipped (not a code file)"
    );
}

#[test]
fn auto_bug_detector_edit_allows_py_code_file() {
    // src/utils.py IS a code file — the session-state check passes.
    // We only verify the path/extension gate here (no subprocess spawned).
    // The rule will return None only if no patterns are detected;
    // verify that it does NOT return None due to path gating.
    // Use a pattern that always detects: add `?. ` to a ts-style expression.
    // (In unit-test context, learn.py won't be called so this is safe.)
    assert!(
        has_code_extension("src/utils.py"),
        "src/utils.py must pass the code-extension gate"
    );
    assert!(
        !is_auto_bug_session_path("src/utils.py"),
        "src/utils.py must not be flagged as session-state"
    );
}
