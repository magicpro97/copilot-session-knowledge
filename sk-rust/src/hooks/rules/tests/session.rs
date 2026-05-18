use super::*;

// --- SessionEndRule ---

#[test]
fn session_end_returns_info_message() {
    let rule = SessionEndRule;
    let data = json!({});
    let result = rule.evaluate("sessionEnd", &data);
    assert!(result.is_some());
    let msg = result.unwrap();
    assert!(msg["message"].as_str().unwrap().contains("Session ended"));
}

// --- deny / info helpers ---

#[test]
fn deny_produces_correct_shape() {
    let v = deny("test reason");
    assert_eq!(v["permissionDecision"].as_str().unwrap(), "deny");
    assert_eq!(
        v["permissionDecisionReason"].as_str().unwrap(),
        "test reason"
    );
}

#[test]
fn info_produces_correct_shape() {
    let v = info("hello");
    assert_eq!(v["message"].as_str().unwrap(), "hello");
}

// --- SessionStartRule ---

#[test]
fn session_start_returns_info_message() {
    let rule = SessionStartRule;
    let data = json!({});
    let result = rule.evaluate("sessionStart", &data);
    assert!(result.is_some());
    let msg = result.unwrap();
    assert!(
        msg["message"].as_str().unwrap().contains("Session started"),
        "expected 'Session started' in message"
    );
}

#[test]
fn session_start_fires_only_on_session_start() {
    let rule = SessionStartRule;
    assert!(rule.events().contains(&"sessionStart"));
    assert!(!rule.events().contains(&"sessionEnd"));
    assert!(!rule.events().contains(&"preToolUse"));
    assert!(!rule.events().contains(&"postToolUse"));
}

#[test]
fn session_start_has_no_tool_filter() {
    let rule = SessionStartRule;
    assert!(
        rule.tools().is_empty(),
        "SessionStartRule should have no tool filter"
    );
}

// --- AutoBriefingRule ---

#[test]
fn auto_briefing_fires_only_on_session_start() {
    let rule = AutoBriefingRule;
    assert!(rule.events().contains(&"sessionStart"));
    assert!(!rule.events().contains(&"sessionEnd"));
    assert!(!rule.events().contains(&"preToolUse"));
}

#[test]
fn auto_briefing_has_no_tool_filter() {
    let rule = AutoBriefingRule;
    assert!(
        rule.tools().is_empty(),
        "AutoBriefingRule should have no tool filter"
    );
}

#[test]
fn auto_briefing_is_fail_open_when_briefing_py_absent() {
    // Point SK_TOOLS_DIR at an empty directory so briefing.py is absent.
    use std::fs;
    let _guard = env_lock();
    let tmp = std::env::temp_dir().join("sk_auto_briefing_test");
    let _ = fs::create_dir_all(&tmp);

    let old = std::env::var("SK_TOOLS_DIR").ok();
    std::env::set_var("SK_TOOLS_DIR", &tmp);

    let rule = AutoBriefingRule;
    let data = json!({});
    let result = rule.evaluate("sessionStart", &data);
    // Must be None (fail-open) when briefing.py is missing.
    assert!(
        result.is_none(),
        "AutoBriefingRule must return None when briefing.py is absent"
    );

    // Restore
    match old {
        Some(v) => std::env::set_var("SK_TOOLS_DIR", v),
        None => std::env::remove_var("SK_TOOLS_DIR"),
    }
    let _ = fs::remove_dir_all(&tmp);
}

#[test]
fn auto_briefing_never_denies() {
    // Even when briefing.py is absent, the rule must not return a deny.
    use std::fs;
    let _guard = env_lock();
    let tmp = std::env::temp_dir().join("sk_auto_briefing_no_deny_test");
    let _ = fs::create_dir_all(&tmp);

    let old = std::env::var("SK_TOOLS_DIR").ok();
    std::env::set_var("SK_TOOLS_DIR", &tmp);

    let rule = AutoBriefingRule;
    let data = json!({});
    if let Some(v) = rule.evaluate("sessionStart", &data) {
        assert!(
            v.get("permissionDecision").is_none(),
            "AutoBriefingRule must never produce a deny"
        );
    }

    match old {
        Some(v) => std::env::set_var("SK_TOOLS_DIR", v),
        None => std::env::remove_var("SK_TOOLS_DIR"),
    }
    let _ = fs::remove_dir_all(&tmp);
}

// --- load_memory_md helper tests (issue #161 native parity) ---

/// Graceful no-op: MEMORY.md absent → returns None.
#[test]
fn memory_inject_returns_none_when_memory_md_absent() {
    let tmp = std::env::temp_dir().join("sk_mem_inject_absent");
    let _ = std::fs::create_dir_all(&tmp);
    // Ensure no MEMORY.md in tmp.
    let _ = std::fs::remove_file(tmp.join("MEMORY.md"));
    let result = load_memory_md(Some(&tmp));
    assert!(
        result.is_none(),
        "load_memory_md must return None when MEMORY.md is absent"
    );
    let _ = std::fs::remove_dir_all(&tmp);
}

/// Fresh MEMORY.md with default config → returns content.
#[test]
fn memory_inject_returns_content_when_memory_md_present() {
    use std::fs;
    let _guard = env_lock();
    let tmp = std::env::temp_dir().join("sk_mem_inject_present");
    let copilot_dir = tmp.join(".copilot");
    let _ = fs::create_dir_all(&copilot_dir);
    fs::write(tmp.join("MEMORY.md"), "## Key Facts\n- Important thing\n").unwrap();
    // No hooks-config.json → defaults apply (enabled=true, max_age=1 day).
    let old_home = std::env::var("HOME").ok();
    let old_up = std::env::var("USERPROFILE").ok();
    std::env::set_var("HOME", &tmp);
    std::env::set_var("USERPROFILE", &tmp);

    let result = load_memory_md(Some(&tmp));
    assert!(
        result.is_some(),
        "load_memory_md must return content when MEMORY.md is fresh"
    );
    let content = result.unwrap();
    assert!(
        content.contains("Important thing"),
        "returned content must include MEMORY.md text; got: {content:?}"
    );

    match old_home {
        Some(v) => std::env::set_var("HOME", v),
        None => std::env::remove_var("HOME"),
    }
    match old_up {
        Some(v) => std::env::set_var("USERPROFILE", v),
        None => std::env::remove_var("USERPROFILE"),
    }
    let _ = fs::remove_dir_all(&tmp);
}

/// Explicit opt-out: `memory_inject_enabled: false` → returns None even with fresh file.
#[test]
fn memory_inject_skipped_when_disabled_in_config() {
    use std::fs;
    let _guard = env_lock();
    let tmp = std::env::temp_dir().join("sk_mem_inject_disabled");
    let copilot_dir = tmp.join(".copilot");
    let _ = fs::create_dir_all(&copilot_dir);
    // Explicit opt-out in hooks-config.json.
    fs::write(
        copilot_dir.join("hooks-config.json"),
        r#"{"memory_inject_enabled": false}"#,
    )
    .unwrap();
    // Write a fresh MEMORY.md so file-absence is not the reason for skip.
    fs::write(
        tmp.join("MEMORY.md"),
        "## Should not appear\n- Hidden content\n",
    )
    .unwrap();

    let old_home = std::env::var("HOME").ok();
    let old_up = std::env::var("USERPROFILE").ok();
    std::env::set_var("HOME", &tmp);
    std::env::set_var("USERPROFILE", &tmp);

    let result = load_memory_md(Some(&tmp));
    assert!(
        result.is_none(),
        "load_memory_md must return None when memory_inject_enabled is false"
    );

    match old_home {
        Some(v) => std::env::set_var("HOME", v),
        None => std::env::remove_var("HOME"),
    }
    match old_up {
        Some(v) => std::env::set_var("USERPROFILE", v),
        None => std::env::remove_var("USERPROFILE"),
    }
    let _ = fs::remove_dir_all(&tmp);
}

/// Token budget truncation: content longer than budget ends with truncation notice.
#[test]
fn memory_inject_truncates_to_token_budget() {
    use std::fs;
    let _guard = env_lock();
    let tmp = std::env::temp_dir().join("sk_mem_inject_truncate");
    let copilot_dir = tmp.join(".copilot");
    let _ = fs::create_dir_all(&copilot_dir);
    // Very small budget: 5 tokens × 4 chars = 20-char limit.
    fs::write(
        copilot_dir.join("hooks-config.json"),
        r#"{"memory_inject_max_tokens": 5}"#,
    )
    .unwrap();
    // Write MEMORY.md much longer than 20 chars.
    let long_content = "A".repeat(200);
    fs::write(tmp.join("MEMORY.md"), &long_content).unwrap();

    let old_home = std::env::var("HOME").ok();
    let old_up = std::env::var("USERPROFILE").ok();
    std::env::set_var("HOME", &tmp);
    std::env::set_var("USERPROFILE", &tmp);

    let result = load_memory_md(Some(&tmp));
    assert!(
        result.is_some(),
        "load_memory_md must return truncated content (not None)"
    );
    let content = result.unwrap();
    assert!(
        content.contains("truncated to token budget"),
        "truncated output must include notice; got: {content:?}"
    );
    assert!(
        content.len() < long_content.len(),
        "truncated output must be shorter than original"
    );

    match old_home {
        Some(v) => std::env::set_var("HOME", v),
        None => std::env::remove_var("HOME"),
    }
    match old_up {
        Some(v) => std::env::set_var("USERPROFILE", v),
        None => std::env::remove_var("USERPROFILE"),
    }
    let _ = fs::remove_dir_all(&tmp);
}

/// Non-ASCII (multi-byte UTF-8) content must truncate without panic.
///
/// token_budget = 0 → char_limit = max(1, 0) = 1.  The content starts
/// with '中' (3 UTF-8 bytes), so byte 1 is a continuation byte — not a
/// char boundary.  The old `String::truncate(1)` would panic; the fixed
/// `is_char_boundary` walk-back must produce valid UTF-8 + truncation notice.
#[test]
fn memory_inject_truncates_non_ascii_utf8_safe() {
    use std::fs;
    let _guard = env_lock();
    let tmp = std::env::temp_dir().join("sk_mem_inject_utf8_safe");
    let copilot_dir = tmp.join(".copilot");
    let _ = fs::create_dir_all(&copilot_dir);
    // token_budget 0 → char_limit = max(1, 0*4) = 1; the first byte of
    // '中' (U+4E2D, encoded as [0xE4,0xB8,0xAD]) is a char boundary but
    // byte 1 is not — the old truncate(1) would panic here.
    fs::write(
        copilot_dir.join("hooks-config.json"),
        r#"{"memory_inject_max_tokens": 0}"#,
    )
    .unwrap();
    let content = "中文重要笔记".repeat(20);
    fs::write(tmp.join("MEMORY.md"), &content).unwrap();

    let old_home = std::env::var("HOME").ok();
    let old_up = std::env::var("USERPROFILE").ok();
    std::env::set_var("HOME", &tmp);
    std::env::set_var("USERPROFILE", &tmp);

    // Must not panic; must return Some with a truncation notice.
    let result = load_memory_md(Some(&tmp));
    assert!(
        result.is_some(),
        "non-ASCII MEMORY.md with tiny budget must return Some (not panic)"
    );
    let text = result.unwrap();
    assert!(
        text.contains("truncated to token budget"),
        "output must contain truncation notice; got: {text:?}"
    );
    // The String type guarantees valid UTF-8, but verify explicitly.
    assert!(
        std::str::from_utf8(text.as_bytes()).is_ok(),
        "truncated output must be valid UTF-8"
    );

    match old_home {
        Some(v) => std::env::set_var("HOME", v),
        None => std::env::remove_var("HOME"),
    }
    match old_up {
        Some(v) => std::env::set_var("USERPROFILE", v),
        None => std::env::remove_var("USERPROFILE"),
    }
    let _ = fs::remove_dir_all(&tmp);
}

/// Non-ASCII character-count parity with Python's `len()` semantics.
///
/// Python's `len()` counts Unicode code points, not UTF-8 bytes.
/// CJK characters (e.g. '中') are 3 UTF-8 bytes but 1 Unicode char.
///
/// With budget=5 tokens: char_limit = 20.
/// 20 CJK chars = 20 bytes under old (byte-count) code → would truncate.
/// 20 CJK chars = 20 chars under new (char-count) code → must NOT truncate.
/// 21 CJK chars = 21 chars → must truncate at exactly 20 chars.
#[test]
fn memory_inject_non_ascii_char_count_parity() {
    use std::fs;
    let _guard = env_lock();
    let tmp = std::env::temp_dir().join("sk_mem_inject_nonascii_parity");
    let copilot_dir = tmp.join(".copilot");
    let _ = fs::create_dir_all(&copilot_dir);
    // Budget: 5 tokens × 4 chars = 20-char limit.
    fs::write(
        copilot_dir.join("hooks-config.json"),
        r#"{"memory_inject_max_tokens": 5}"#,
    )
    .unwrap();

    let old_home = std::env::var("HOME").ok();
    let old_up = std::env::var("USERPROFILE").ok();
    std::env::set_var("HOME", &tmp);
    std::env::set_var("USERPROFILE", &tmp);

    // --- Case 1: exactly at budget (20 CJK chars = 60 UTF-8 bytes) ---
    // Old byte-count code: 60 bytes > 20 → would truncate (bug).
    // New char-count code: 20 chars == 20 → must NOT truncate.
    let exactly_budget = "中".repeat(20);
    fs::write(tmp.join("MEMORY.md"), &exactly_budget).unwrap();
    let result = load_memory_md(Some(&tmp));
    assert!(
        result.is_some(),
        "load_memory_md must return Some for content at char budget"
    );
    let text = result.unwrap();
    assert!(
        !text.contains("truncated to token budget"),
        "20 CJK chars at 20-char budget must NOT be truncated; got: {text:?}"
    );
    assert_eq!(
        text.chars().count(),
        20,
        "returned text must have exactly 20 Unicode chars; got {}",
        text.chars().count()
    );

    // --- Case 2: one over budget (21 CJK chars) ---
    let over_budget = "中".repeat(21);
    fs::write(tmp.join("MEMORY.md"), &over_budget).unwrap();
    let result2 = load_memory_md(Some(&tmp));
    assert!(
        result2.is_some(),
        "21-char CJK content must return Some (truncated)"
    );
    let text2 = result2.unwrap();
    assert!(
        text2.contains("truncated to token budget"),
        "21-char CJK content must include truncation notice; got: {text2:?}"
    );
    // The body before the truncation notice must be exactly 20 Unicode chars.
    let body = text2.split('\n').next().unwrap_or("");
    assert_eq!(
        body.chars().count(),
        20,
        "truncated body must be exactly 20 Unicode chars; got {} chars: {body:?}",
        body.chars().count()
    );
    // Truncated output must be valid UTF-8.
    assert!(
        std::str::from_utf8(text2.as_bytes()).is_ok(),
        "truncated non-ASCII output must be valid UTF-8"
    );

    match old_home {
        Some(v) => std::env::set_var("HOME", v),
        None => std::env::remove_var("HOME"),
    }
    match old_up {
        Some(v) => std::env::set_var("USERPROFILE", v),
        None => std::env::remove_var("USERPROFILE"),
    }
    let _ = fs::remove_dir_all(&tmp);
}

/// Regression: a future mtime (clock skew) must be treated as fresh, not stale.
///
/// Python behaviour:
///   `age_secs = time.time() - mtime`  →  negative when mtime is future
///   `if age_secs > max_age_secs`      →  False  →  file is fresh
///
/// Previous Rust behaviour:
///   `duration_since(future_mtime).ok()` → `None` → `unwrap_or(false)` → stale
///   The file was silently skipped even though it was not old.
#[test]
fn memory_inject_future_mtime_treated_as_fresh() {
    use std::fs::{File, FileTimes};
    let _guard = env_lock();
    let tmp = std::env::temp_dir().join("sk_mem_inject_future_mtime");
    let copilot_dir = tmp.join(".copilot");
    let _ = fs::create_dir_all(&copilot_dir);
    let memory_path = tmp.join("MEMORY.md");
    fs::write(&memory_path, "## Future mtime\n- content\n").unwrap();

    // Set the file's mtime 30 seconds into the future to simulate clock skew.
    let future_mtime = SystemTime::now() + Duration::from_secs(30);
    let file = File::options().write(true).open(&memory_path).unwrap();
    let times = FileTimes::new().set_modified(future_mtime);
    file.set_times(times).unwrap();
    drop(file);

    let old_home = std::env::var("HOME").ok();
    let old_up = std::env::var("USERPROFILE").ok();
    std::env::set_var("HOME", &tmp);
    std::env::set_var("USERPROFILE", &tmp);

    // Must return Some — future mtime is treated as fresh (age = 0).
    let result = load_memory_md(Some(&tmp));
    assert!(
        result.is_some(),
        "load_memory_md must treat a future mtime as fresh (not stale); got None"
    );

    match old_home {
        Some(v) => std::env::set_var("HOME", v),
        None => std::env::remove_var("HOME"),
    }
    match old_up {
        Some(v) => std::env::set_var("USERPROFILE", v),
        None => std::env::remove_var("USERPROFILE"),
    }
    let _ = fs::remove_dir_all(&tmp);
}

// --- IntegrityRule ---

// --- load_goal_resume_hint helper tests (issue #185 native parity) ---
//
// Each test creates a *unique* temp directory using the process ID and an
// atomic counter to avoid races when the test binary runs concurrently with
// itself (e.g. parallel CI shards) and to stay clean even if a test panics
// before reaching its cleanup call.

/// Returns a unique temp dir path: `<temp>/<prefix>_<pid>_<n>`.
/// The directory is NOT created here; callers use create_dir_all.
fn resume_test_dir(tag: &str) -> std::path::PathBuf {
    use std::sync::atomic::{AtomicU64, Ordering};
    static CTR: AtomicU64 = AtomicU64::new(0);
    let n = CTR.fetch_add(1, Ordering::Relaxed);
    std::env::temp_dir().join(format!("sk_resume_{}_{}_{}", tag, std::process::id(), n))
}

/// Graceful no-op: breadcrumb absent → returns None.
#[test]
fn auto_briefing_resume_hint_none_when_breadcrumb_absent() {
    let tmp = resume_test_dir("absent");
    let _ = std::fs::create_dir_all(&tmp);
    // No breadcrumb written — just ensure the dir exists with no .octogent child.
    let result = load_goal_resume_hint(Some(&tmp));
    assert!(
        result.is_none(),
        "load_goal_resume_hint must return None when breadcrumb is absent"
    );
    let _ = std::fs::remove_dir_all(&tmp);
}

/// Paused goal + valid breadcrumb → banner lines with goal title and resume command.
#[test]
fn auto_briefing_resume_hint_shows_banner_when_paused() {
    use std::fs;
    let tmp = resume_test_dir("paused");
    let octogent = tmp.join(".octogent");
    let _ = fs::create_dir_all(&octogent);

    // Write a paused goal.json
    fs::write(
        octogent.join("goal.json"),
        r#"{"status": "paused", "title": "My Test Goal", "goal_id": "g1"}"#,
    )
    .unwrap();

    // Write a breadcrumb
    let bc_path = octogent.join(BREADCRUMB_FILENAME);
    fs::write(
        &bc_path,
        serde_json::json!({
            "goal_id": "g1",
            "goal_title": "My Test Goal",
            "goal_path": octogent.join("goal.json").to_string_lossy().to_string(),
            "pause_reason": "session_end:normal",
            "resume_command": "sk tentacle goal resume",
            "paused_at": "2026-01-01T00:00:00Z",
            "previous_status": "active"
        })
        .to_string(),
    )
    .unwrap();

    let result = load_goal_resume_hint(Some(&tmp));
    assert!(result.is_some(), "expected banner lines for paused goal");
    let lines = result.unwrap();
    let combined = lines.join("\n");
    assert!(
        combined.contains("My Test Goal"),
        "banner must include goal title; got: {combined:?}"
    );
    assert!(
        combined.contains("sk tentacle goal resume"),
        "banner must include exact resume command; got: {combined:?}"
    );
    assert!(
        combined.contains("session end"),
        "banner must map session_end reason; got: {combined:?}"
    );
    assert!(
        lines[0].contains("Paused goal"),
        "first line must lead with 'Paused goal'; got: {:?}",
        lines[0]
    );
    let _ = fs::remove_dir_all(&tmp);
}

/// Stale breadcrumb (goal already resumed) → None (suppressed).
#[test]
fn auto_briefing_resume_hint_suppressed_when_goal_resumed() {
    use std::fs;
    let tmp = resume_test_dir("stale");
    let octogent = tmp.join(".octogent");
    let _ = fs::create_dir_all(&octogent);

    // goal.json status is now "active" (already resumed)
    fs::write(
        octogent.join("goal.json"),
        r#"{"status": "active", "title": "Resumed Goal", "goal_id": "g2"}"#,
    )
    .unwrap();

    // Write a breadcrumb that refers to this goal
    let bc_path = octogent.join(BREADCRUMB_FILENAME);
    fs::write(
        &bc_path,
        serde_json::json!({
            "goal_id": "g2",
            "goal_title": "Resumed Goal",
            "goal_path": octogent.join("goal.json").to_string_lossy().to_string(),
            "pause_reason": "session_end:normal",
            "resume_command": "sk tentacle goal resume",
            "paused_at": "2026-01-01T00:00:00Z",
            "previous_status": "active"
        })
        .to_string(),
    )
    .unwrap();

    let result = load_goal_resume_hint(Some(&tmp));
    assert!(
        result.is_none(),
        "load_goal_resume_hint must suppress banner when goal is no longer paused"
    );
    let _ = fs::remove_dir_all(&tmp);
}

/// Stale breadcrumb (goal completed) → None (suppressed).
#[test]
fn auto_briefing_resume_hint_suppressed_when_goal_completed() {
    use std::fs;
    let tmp = resume_test_dir("completed");
    let octogent = tmp.join(".octogent");
    let _ = fs::create_dir_all(&octogent);

    fs::write(
        octogent.join("goal.json"),
        r#"{"status": "completed", "title": "Done Goal", "goal_id": "g3"}"#,
    )
    .unwrap();

    let bc_path = octogent.join(BREADCRUMB_FILENAME);
    fs::write(
        &bc_path,
        serde_json::json!({
            "goal_id": "g3",
            "goal_title": "Done Goal",
            "goal_path": octogent.join("goal.json").to_string_lossy().to_string(),
            "pause_reason": "session_end:normal",
            "resume_command": "sk tentacle goal resume",
            "paused_at": "2026-01-01T00:00:00Z",
            "previous_status": "active"
        })
        .to_string(),
    )
    .unwrap();

    let result = load_goal_resume_hint(Some(&tmp));
    assert!(
        result.is_none(),
        "load_goal_resume_hint must suppress banner when goal is completed"
    );
    let _ = fs::remove_dir_all(&tmp);
}

/// Breadcrumb present but goal.json absent → fail-open (banner still shown).
#[test]
fn auto_briefing_resume_hint_fail_open_when_goal_json_absent() {
    use std::fs;
    let tmp = resume_test_dir("no_goal_json");
    let octogent = tmp.join(".octogent");
    let _ = fs::create_dir_all(&octogent);

    // No goal.json — breadcrumb points to a non-existent path
    let goal_json_path = octogent.join("goal.json");
    // Ensure it does not exist (it won't in a fresh unique dir)
    let _ = fs::remove_file(&goal_json_path);

    let bc_path = octogent.join(BREADCRUMB_FILENAME);
    fs::write(
        &bc_path,
        serde_json::json!({
            "goal_id": "g4",
            "goal_title": "Orphaned Goal",
            "goal_path": goal_json_path.to_string_lossy().to_string(),
            "pause_reason": "session_end:crash",
            "resume_command": "sk tentacle goal resume",
            "paused_at": "2026-01-01T00:00:00Z",
            "previous_status": "active"
        })
        .to_string(),
    )
    .unwrap();

    // Must fail-open: if goal.json is absent we cannot confirm resumption,
    // so the banner should still appear.
    let result = load_goal_resume_hint(Some(&tmp));
    assert!(
        result.is_some(),
        "load_goal_resume_hint must show banner when goal.json is absent (fail-open)"
    );
    let _ = fs::remove_dir_all(&tmp);
}

/// Whitespace-only goal_title must fall back to trimmed goal_id, not "(untitled goal)".
/// This is the regression case for the parity fix: Python and Rust must agree.
#[test]
fn auto_briefing_resume_hint_whitespace_title_falls_back_to_goal_id() {
    use std::fs;
    let tmp = resume_test_dir("ws_title");
    let octogent = tmp.join(".octogent");
    let _ = fs::create_dir_all(&octogent);

    fs::write(
        octogent.join("goal.json"),
        r#"{"status": "paused", "goal_id": "ws-goal-id"}"#,
    )
    .unwrap();

    let bc_path = octogent.join(BREADCRUMB_FILENAME);
    fs::write(
        &bc_path,
        serde_json::json!({
            "goal_id": "ws-goal-id",
            "goal_title": "   ",   // whitespace-only — should be treated as absent
            "goal_path": octogent.join("goal.json").to_string_lossy().to_string(),
            "pause_reason": "session_end:normal",
            "resume_command": "sk tentacle goal resume",
            "paused_at": "2026-01-01T00:00:00Z",
            "previous_status": "active"
        })
        .to_string(),
    )
    .unwrap();

    let result = load_goal_resume_hint(Some(&tmp));
    assert!(
        result.is_some(),
        "expected banner for paused goal with whitespace title"
    );
    let combined = result.unwrap().join("\n");
    assert!(
        combined.contains("ws-goal-id"),
        "banner must fall back to goal_id when goal_title is whitespace-only; got: {combined:?}"
    );
    assert!(
        !combined.contains("(untitled goal)"),
        "banner must NOT show '(untitled goal)' when goal_id is available; got: {combined:?}"
    );
    let _ = fs::remove_dir_all(&tmp);
}

/// Valid non-object breadcrumb JSON (array, number, string) must return None.
/// Regression for review finding: serde_json::from_str succeeds for any valid
/// JSON value, not just objects; the loader must guard with is_object() so
/// native behaviour matches Python's AttributeError fail-open path.
#[test]
fn auto_briefing_resume_hint_none_for_non_object_breadcrumb() {
    use std::fs;
    for (tag, payload) in &[("array", "[]"), ("number", "42"), ("string", r#""x""#)] {
        let tmp = resume_test_dir(&format!("non_obj_{}", tag));
        let octogent = tmp.join(".octogent");
        let _ = fs::create_dir_all(&octogent);
        let bc_path = octogent.join(BREADCRUMB_FILENAME);
        fs::write(&bc_path, payload).unwrap();
        let result = load_goal_resume_hint(Some(&tmp));
        assert!(
            result.is_none(),
            "non-object breadcrumb JSON ({tag:?}) must return None; got: {result:?}"
        );
        let _ = fs::remove_dir_all(&tmp);
    }
}

/// Non-object goal.json ([], 42, "running") → fail-open: banner still shown.
/// Regression for issue #185 staleness-check parity gap: when goal.json exists
/// but contains valid non-object JSON, the native loader must NOT suppress the
/// banner (previously unwrap_or("") != "paused" triggered suppression).
/// Must match Python's fail-open path where state.get() raises on non-dict.
#[test]
fn auto_briefing_resume_hint_fail_open_for_non_object_goal_json() {
    use std::fs;
    for (tag, payload) in &[
        ("array", "[]"),
        ("number", "42"),
        ("string", r#""running""#),
    ] {
        let tmp = resume_test_dir(&format!("goal_nonobj_{}", tag));
        let octogent = tmp.join(".octogent");
        let _ = fs::create_dir_all(&octogent);

        // Write non-object goal.json
        fs::write(octogent.join("goal.json"), payload).unwrap();

        let bc_path = octogent.join(BREADCRUMB_FILENAME);
        fs::write(
            &bc_path,
            serde_json::json!({
                "goal_id": "g-nonobj",
                "goal_title": "Goal With Non-Object Status File",
                "goal_path": octogent.join("goal.json").to_string_lossy().to_string(),
                "pause_reason": "session_end:normal",
                "resume_command": "sk tentacle goal resume",
                "paused_at": "2026-01-01T00:00:00Z",
                "previous_status": "active"
            })
            .to_string(),
        )
        .unwrap();

        let result = load_goal_resume_hint(Some(&tmp));
        assert!(
            result.is_some(),
            "non-object goal.json ({tag:?}) must show banner (fail-open); got: {result:?}"
        );
        let _ = fs::remove_dir_all(&tmp);
    }
}

/// Non-string pause_reason in breadcrumb → banner shown with generic "paused" label.
/// Regression for issue #185 parity: Rust's .as_str().unwrap_or("") already
/// coerces non-string values to ""; this test documents and locks that behaviour.
#[test]
fn auto_briefing_resume_hint_non_string_pause_reason_falls_back_to_paused() {
    use std::fs;
    // pause_reason values that are valid JSON but not strings
    for (tag, pr_val) in &[("number", "42"), ("array", r#"["x"]"#), ("null", "null")] {
        let tmp = resume_test_dir(&format!("pause_reason_nonstr_{}", tag));
        let octogent = tmp.join(".octogent");
        let _ = fs::create_dir_all(&octogent);

        fs::write(
            octogent.join("goal.json"),
            r#"{"status": "paused", "goal_id": "gpr"}"#,
        )
        .unwrap();

        // Build breadcrumb JSON with a non-string pause_reason
        let bc_json = format!(
            r#"{{"goal_id":"gpr","goal_title":"Reason Test Goal","goal_path":"{goal_path}","pause_reason":{pr},"resume_command":"sk tentacle goal resume","paused_at":"2026-01-01T00:00:00Z","previous_status":"active"}}"#,
            goal_path = octogent
                .join("goal.json")
                .to_string_lossy()
                .replace('\\', "\\\\"),
            pr = pr_val,
        );
        fs::write(octogent.join(BREADCRUMB_FILENAME), &bc_json).unwrap();

        let result = load_goal_resume_hint(Some(&tmp));
        assert!(
            result.is_some(),
            "non-string pause_reason ({tag:?}) must show banner; got: {result:?}"
        );
        let combined = result.unwrap().join("\n");
        assert!(
            combined.contains("paused"),
            "non-string pause_reason ({tag:?}) must fall back to 'paused' label; got: {combined:?}"
        );
        let _ = fs::remove_dir_all(&tmp);
    }
}

/// Non-string resume_command in breadcrumb → falls back to default "sk tentacle goal resume".
/// Regression for issue #185 review: non-string values (int, array, null, object) must not
/// produce a spurious or empty resume command; the default must be shown in the banner.
#[test]
fn auto_briefing_resume_hint_non_string_resume_command_falls_back_to_default() {
    use std::fs;
    for (tag, rc_val) in &[
        ("number", "42"),
        ("array", r#"["sk","tentacle"]"#),
        ("null", "null"),
        ("object", r#"{"cmd":"x"}"#),
    ] {
        let tmp = resume_test_dir(&format!("resume_cmd_nonstr_{}", tag));
        let octogent = tmp.join(".octogent");
        let _ = fs::create_dir_all(&octogent);
        fs::write(
            octogent.join("goal.json"),
            r#"{"status": "paused", "goal_id": "grc"}"#,
        )
        .unwrap();
        let bc_json = format!(
            r#"{{"goal_id":"grc","goal_title":"RC Test","goal_path":"{goal_path}","pause_reason":"session_end","resume_command":{rc},"paused_at":"2026-01-01T00:00:00Z","previous_status":"active"}}"#,
            goal_path = octogent
                .join("goal.json")
                .to_string_lossy()
                .replace('\\', "\\\\"),
            rc = rc_val,
        );
        fs::write(octogent.join(BREADCRUMB_FILENAME), &bc_json).unwrap();
        let result = load_goal_resume_hint(Some(&tmp));
        assert!(
            result.is_some(),
            "non-string resume_command ({tag:?}) must still show banner; got: {result:?}"
        );
        let combined = result.unwrap().join("\n");
        assert!(
            combined.contains("sk tentacle goal resume"),
            "non-string resume_command ({tag:?}) must fall back to default; got: {combined:?}"
        );
        let _ = fs::remove_dir_all(&tmp);
    }
}

/// Whitespace-only resume_command → falls back to default "sk tentacle goal resume".
/// Regression for issue #185 review: a resume_command that is all whitespace must be
/// treated as absent and the default command shown, matching Python's .strip() or "".
#[test]
fn auto_briefing_resume_hint_whitespace_resume_command_falls_back_to_default() {
    use std::fs;
    for (tag, rc_val) in &[
        ("spaces", "\"   \""),
        ("tab", "\"\\t\""),
        ("newline", "\"\\n\""),
    ] {
        let tmp = resume_test_dir(&format!("resume_cmd_ws_{}", tag));
        let octogent = tmp.join(".octogent");
        let _ = fs::create_dir_all(&octogent);
        fs::write(
            octogent.join("goal.json"),
            r#"{"status": "paused", "goal_id": "gws"}"#,
        )
        .unwrap();
        let bc_json = format!(
            r#"{{"goal_id":"gws","goal_title":"WS RC Test","goal_path":"{goal_path}","pause_reason":"session_end","resume_command":{rc},"paused_at":"2026-01-01T00:00:00Z","previous_status":"active"}}"#,
            goal_path = octogent
                .join("goal.json")
                .to_string_lossy()
                .replace('\\', "\\\\"),
            rc = rc_val,
        );
        fs::write(octogent.join(BREADCRUMB_FILENAME), &bc_json).unwrap();
        let result = load_goal_resume_hint(Some(&tmp));
        assert!(
            result.is_some(),
            "whitespace resume_command ({tag:?}) must still show banner; got: {result:?}"
        );
        let combined = result.unwrap().join("\n");
        assert!(
            combined.contains("sk tentacle goal resume"),
            "whitespace resume_command ({tag:?}) must fall back to default; got: {combined:?}"
        );
        let _ = fs::remove_dir_all(&tmp);
    }
}

/// Breadcrumb with budget_snapshot → banner includes a budget detail line
/// (issue #182 native parity: mirrors Python _load_goal_resume_hint reader).
#[test]
fn auto_briefing_resume_hint_shows_budget_snapshot_line() {
    use std::fs;
    let tmp = resume_test_dir("budget_snap");
    let octogent = tmp.join(".octogent");
    let _ = fs::create_dir_all(&octogent);

    fs::write(
        octogent.join("goal.json"),
        r#"{"status": "paused", "goal_id": "bs-goal"}"#,
    )
    .unwrap();

    let bc_path = octogent.join(BREADCRUMB_FILENAME);
    fs::write(
        &bc_path,
        serde_json::json!({
            "goal_id": "bs-goal",
            "goal_title": "Budget Snapshot Goal",
            "goal_path": octogent.join("goal.json").to_string_lossy().to_string(),
            "pause_reason": "session_end:normal",
            "resume_command": "sk tentacle goal resume",
            "paused_at": "2026-01-01T00:00:00Z",
            "previous_status": "active",
            "goal_status_at_pause": "paused",
            "budget_snapshot": {
                "current_iteration": 6,
                "max_iterations": 30,
                "tentacle_count": 38,
                "max_tentacles": 100
            }
        })
        .to_string(),
    )
    .unwrap();

    let result = load_goal_resume_hint(Some(&tmp));
    assert!(
        result.is_some(),
        "expected banner for paused goal with budget_snapshot"
    );
    let lines = result.unwrap();
    let combined = lines.join("\n");
    assert!(
        combined.contains("Budget:"),
        "banner must include a 'Budget:' detail line; got: {combined:?}"
    );
    assert!(
        combined.contains("6/30"),
        "banner must show current_iteration/max_iterations; got: {combined:?}"
    );
    assert!(
        combined.contains("38/100"),
        "banner must show tentacle_count/max_tentacles; got: {combined:?}"
    );
    let _ = fs::remove_dir_all(&tmp);
}

/// Old breadcrumb without budget_snapshot → banner still shown, no Budget: line
/// (issue #182 backward-compat: old breadcrumbs must not crash the reader).
#[test]
fn auto_briefing_resume_hint_no_budget_line_for_old_breadcrumb() {
    use std::fs;
    let tmp = resume_test_dir("no_budget_snap");
    let octogent = tmp.join(".octogent");
    let _ = fs::create_dir_all(&octogent);

    fs::write(
        octogent.join("goal.json"),
        r#"{"status": "paused", "goal_id": "old-bc"}"#,
    )
    .unwrap();

    let bc_path = octogent.join(BREADCRUMB_FILENAME);
    fs::write(
        &bc_path,
        serde_json::json!({
            "goal_id": "old-bc",
            "goal_title": "Old Breadcrumb Goal",
            "goal_path": octogent.join("goal.json").to_string_lossy().to_string(),
            "pause_reason": "session_end:normal",
            "resume_command": "sk tentacle goal resume",
            "paused_at": "2026-01-01T00:00:00Z",
            "previous_status": "active"
        })
        .to_string(),
    )
    .unwrap();

    let result = load_goal_resume_hint(Some(&tmp));
    assert!(
        result.is_some(),
        "old breadcrumb must still show banner (backward compat); got: {result:?}"
    );
    let combined = result.unwrap().join("\n");
    assert!(
        combined.contains("Old Breadcrumb Goal"),
        "banner must include goal title; got: {combined:?}"
    );
    assert!(
        !combined.contains("Budget:"),
        "old breadcrumb must NOT show Budget: line; got: {combined:?}"
    );
    let _ = fs::remove_dir_all(&tmp);
}

/// format_pause_reason maps known and unknown prefixes correctly.
#[test]
fn auto_briefing_format_pause_reason_known_and_unknown() {
    assert_eq!(format_pause_reason("session_end:normal"), "session end");
    assert_eq!(format_pause_reason("session_end:"), "session end");
    assert_eq!(format_pause_reason("session_end"), "session end");
    assert_eq!(
        format_pause_reason("compaction:quota_triggered"),
        "context compaction"
    );
    assert_eq!(format_pause_reason("quota:low_context"), "quota limit");
    assert_eq!(format_pause_reason("unknown_reason"), "paused");
    assert_eq!(format_pause_reason(""), "paused");
}

#[test]
fn integrity_rule_fires_only_on_session_start() {
    let rule = IntegrityRule;
    assert!(rule.events().contains(&"sessionStart"));
    assert!(!rule.events().contains(&"sessionEnd"));
    assert!(!rule.events().contains(&"preToolUse"));
}

#[test]
fn integrity_rule_has_no_tool_filter() {
    let rule = IntegrityRule;
    assert!(
        rule.tools().is_empty(),
        "IntegrityRule should have no tool filter"
    );
}

#[test]
fn integrity_rule_generates_manifest_when_absent() {
    let _guard = env_lock();
    // Use an isolated HOME so the rule generates a fresh manifest without
    // touching the real ~/.copilot/hooks/integrity-manifest.json.
    use std::fs;
    let tmp = std::env::temp_dir().join("sk_integrity_manifest_test");
    let hooks_dir = tmp.join(".copilot").join("hooks");
    let _ = fs::remove_dir_all(&tmp);
    fs::create_dir_all(&hooks_dir).unwrap();

    let old_home = std::env::var("HOME").ok();
    let old_up = std::env::var("USERPROFILE").ok();
    std::env::set_var("HOME", &tmp);
    std::env::set_var("USERPROFILE", &tmp);

    let rule = IntegrityRule;
    let data = json!({});
    let result = rule.evaluate("sessionStart", &data);
    // Must return Some (first-run manifest generation message).
    if let Some(v) = result {
        let msg = v["message"].as_str().unwrap_or("");
        assert!(
            msg.contains("manifest") || msg.contains("integrity") || msg.contains("\u{1f512}"),
            "first-run message should mention manifest; got: {msg}"
        );
    }
    // Must NOT produce a deny.
    if let Some(v) = IntegrityRule.evaluate("sessionStart", &data) {
        assert!(
            v.get("permissionDecision").is_none(),
            "IntegrityRule must never deny"
        );
    }

    // Restore
    match old_home {
        Some(v) => std::env::set_var("HOME", v),
        None => std::env::remove_var("HOME"),
    }
    match old_up {
        Some(v) => std::env::set_var("USERPROFILE", v),
        None => std::env::remove_var("USERPROFILE"),
    }
    let _ = fs::remove_dir_all(&tmp);
}

// --- RecurrenceDetectorRule ---

#[test]
fn recurrence_detector_fires_only_on_session_end() {
    let rule = RecurrenceDetectorRule;
    assert!(rule.events().contains(&"sessionEnd"));
    assert!(!rule.events().contains(&"sessionStart"));
    assert!(!rule.events().contains(&"preToolUse"));
    assert!(!rule.events().contains(&"postToolUse"));
}

#[test]
fn recurrence_detector_has_no_tool_filter() {
    let rule = RecurrenceDetectorRule;
    assert!(
        rule.tools().is_empty(),
        "RecurrenceDetectorRule should have no tool filter"
    );
}

#[test]
fn recurrence_detector_returns_none_when_no_session_id() {
    // When COPILOT_SESSION_ID is absent, the rule must be a no-op.
    std::env::remove_var("COPILOT_SESSION_ID");
    std::env::remove_var("COPILOT_SESSION_STATE");
    let rule = RecurrenceDetectorRule;
    let data = json!({});
    let result = rule.evaluate("sessionEnd", &data);
    assert!(
        result.is_none(),
        "RecurrenceDetectorRule must return None when session ID is absent"
    );
}

#[test]
fn recurrence_detector_returns_none_when_db_absent() {
    // Point SK_DB at a non-existent path — rule must be fail-open.
    std::env::set_var("SK_DB", "/nonexistent/path/knowledge.db");
    let rule = RecurrenceDetectorRule;
    let data = json!({});
    let result = rule.evaluate("sessionEnd", &data);
    assert!(
        result.is_none(),
        "RecurrenceDetectorRule must return None when DB is absent"
    );
    std::env::remove_var("SK_DB");
}

#[test]
fn recurrence_detector_never_denies() {
    // Even in the most adversarial environment, the rule must not deny.
    std::env::set_var("COPILOT_SESSION_ID", "test-session-id");
    std::env::set_var("SK_DB", "/nonexistent/path/knowledge.db");
    let rule = RecurrenceDetectorRule;
    let data = json!({});
    if let Some(v) = rule.evaluate("sessionEnd", &data) {
        assert!(
            v.get("permissionDecision").is_none(),
            "RecurrenceDetectorRule must never produce a deny"
        );
    }
    std::env::remove_var("COPILOT_SESSION_ID");
    std::env::remove_var("SK_DB");
}

#[test]
fn all_rules_includes_auto_briefing() {
    let rules = all_rules();
    let names: Vec<&str> = rules.iter().map(|r| r.name()).collect();
    assert!(
        names.contains(&"auto-briefing"),
        "all_rules() must include AutoBriefingRule; got: {names:?}"
    );
}

#[test]
fn all_rules_includes_integrity() {
    let rules = all_rules();
    let names: Vec<&str> = rules.iter().map(|r| r.name()).collect();
    assert!(
        names.contains(&"integrity"),
        "all_rules() must include IntegrityRule; got: {names:?}"
    );
}

#[test]
fn all_rules_includes_recurrence_detector() {
    let rules = all_rules();
    let names: Vec<&str> = rules.iter().map(|r| r.name()).collect();
    assert!(
        names.contains(&"recurrence-detector"),
        "all_rules() must include RecurrenceDetectorRule; got: {names:?}"
    );
}

#[test]
fn all_rules_session_start_before_auto_briefing_before_integrity() {
    // Registration order: SessionStartRule → AutoBriefingRule → IntegrityRule
    let rules = all_rules();
    let names: Vec<&str> = rules.iter().map(|r| r.name()).collect();
    let pos_start = names.iter().position(|&n| n == "session-start");
    let pos_briefing = names.iter().position(|&n| n == "auto-briefing");
    let pos_integrity = names.iter().position(|&n| n == "integrity");
    assert!(
        pos_start.is_some() && pos_briefing.is_some() && pos_integrity.is_some(),
        "session-start, auto-briefing, and integrity must all be registered"
    );
    assert!(
        pos_start.unwrap() < pos_briefing.unwrap(),
        "SessionStartRule must precede AutoBriefingRule in all_rules()"
    );
    assert!(
        pos_briefing.unwrap() < pos_integrity.unwrap(),
        "AutoBriefingRule must precede IntegrityRule in all_rules()"
    );
}

#[test]
fn all_rules_recurrence_detector_after_session_end() {
    // RecurrenceDetectorRule must follow SessionEndRule in registration order.
    let rules = all_rules();
    let names: Vec<&str> = rules.iter().map(|r| r.name()).collect();
    let pos_end = names.iter().position(|&n| n == "session-end");
    let pos_recur = names.iter().position(|&n| n == "recurrence-detector");
    assert!(
        pos_end.is_some() && pos_recur.is_some(),
        "session-end and recurrence-detector must both be registered"
    );
    assert!(
        pos_end.unwrap() < pos_recur.unwrap(),
        "SessionEndRule must precede RecurrenceDetectorRule in all_rules()"
    );
}

#[test]
fn agent_stop_returns_info_message_for_agent_stop() {
    let rule = AgentStopRule;
    let data = json!({});
    let result = rule.evaluate("agentStop", &data);
    assert!(result.is_some());
    let msg = result.unwrap();
    let text = msg["message"].as_str().expect("must have message field");
    assert!(
        text.contains("agentStop"),
        "message should include event name; got: {text}"
    );
}

#[test]
fn agent_stop_returns_info_message_for_subagent_stop() {
    let rule = AgentStopRule;
    let data = json!({});
    let result = rule.evaluate("subagentStop", &data);
    assert!(result.is_some());
    let msg = result.unwrap();
    let text = msg["message"].as_str().expect("must have message field");
    assert!(
        text.contains("subagentStop"),
        "message should include event name; got: {text}"
    );
}

/// AgentStopRule must be fail-open: when marker cleanup subprocess fails or
/// finds nothing to clear, the rule still returns an informational message.
#[test]
fn agent_stop_fail_open_when_no_cleanup() {
    let rule = AgentStopRule;
    // Empty payload → no tentacle hints → cleanup returns None → still info
    let data = json!({});
    let result = rule.evaluate("agentStop", &data);
    assert!(
        result.is_some(),
        "must return Some even when cleanup finds nothing"
    );
    let msg = result.unwrap();
    let text = msg["message"].as_str().expect("must have message");
    // Must emit the event name regardless of cleanup outcome.
    assert!(text.contains("agentStop"));
}

/// AgentStopRule with a tentacle-hint payload must still be fail-open when the
/// subprocess is unavailable (tentacle.py absent, Python missing, etc.).
#[test]
fn agent_stop_fail_open_with_tentacle_hint_payload() {
    let rule = AgentStopRule;
    let data = json!({
        "tentacle": "my-test-tentacle",
        "tentacleId": "abc-123"
    });
    let result = rule.evaluate("subagentStop", &data);
    assert!(
        result.is_some(),
        "must return Some even with hints when subprocess unavailable"
    );
    let msg = result.unwrap();
    let text = msg["message"].as_str().expect("must have message");
    assert!(text.contains("subagentStop"));
}

#[test]
fn agent_stop_fires_on_agent_and_subagent_stop() {
    let rule = AgentStopRule;
    assert!(rule.events().contains(&"agentStop"));
    assert!(rule.events().contains(&"subagentStop"));
    assert!(!rule.events().contains(&"sessionStart"));
    assert!(!rule.events().contains(&"preToolUse"));
}

#[test]
fn agent_stop_has_no_tool_filter() {
    let rule = AgentStopRule;
    assert!(
        rule.tools().is_empty(),
        "AgentStopRule should have no tool filter"
    );
}

// --- all_rules sanity ---

#[test]
fn all_rules_non_empty() {
    let rules = all_rules();
    assert!(!rules.is_empty());
    // Check each rule has non-empty name and at least one event.
    for rule in &rules {
        assert!(!rule.name().is_empty(), "rule has empty name");
        assert!(
            !rule.events().is_empty(),
            "rule {} has no events",
            rule.name()
        );
    }
}

#[test]
fn all_rules_covers_all_lifecycle_events() {
    let rules = all_rules();
    let covered_events: std::collections::HashSet<&str> = rules
        .iter()
        .flat_map(|r| r.events().iter().copied())
        .collect();
    // Every event handled by the native runner should have at least one rule.
    for event in &[
        "sessionStart",
        "preToolUse",
        "postToolUse",
        "sessionEnd",
        "agentStop",
        "subagentStop",
        "errorOccurred",
    ] {
        assert!(
            covered_events.contains(event),
            "no rule covers event '{event}'"
        );
    }
}

// --- SessionEndRule (extended tests) ---

#[test]
fn session_end_carries_reason_in_session_log_and_ack() {
    // We can only test that the rule returns Some and is fail-open without
    // a real session ID (no actual cleanup happens when env var is absent).
    let rule = SessionEndRule;
    let data = json!({"reason": "user-cancelled"});
    let result = rule.evaluate("sessionEnd", &data);
    assert!(
        result.is_some(),
        "must return Some even when env var absent"
    );
    let msg = result.unwrap();
    assert!(
        msg["message"].as_str().unwrap().contains("Session ended"),
        "ack message must mention 'Session ended'"
    );
}

#[test]
fn session_end_fires_only_on_session_end() {
    let rule = SessionEndRule;
    assert!(rule.events().contains(&"sessionEnd"));
    assert!(!rule.events().contains(&"sessionStart"));
    assert!(!rule.events().contains(&"postToolUse"));
}

#[test]
fn session_end_has_no_tool_filter() {
    let rule = SessionEndRule;
    assert!(
        rule.tools().is_empty(),
        "SessionEndRule should have no tool filter"
    );
}

#[test]
fn session_end_cleanup_skipped_when_session_id_absent() {
    // When COPILOT_AGENT_SESSION_ID is not set, cleanup is skipped (fail-open).
    // We verify by ensuring the rule still returns Some (not a panic or error).
    std::env::remove_var("COPILOT_AGENT_SESSION_ID");
    let rule = SessionEndRule;
    let data = json!({});
    let result = rule.evaluate("sessionEnd", &data);
    assert!(
        result.is_some(),
        "fail-open: must return Some when env var absent"
    );
}

// --- ErrorOccurredRule ---

#[test]
fn error_occurred_returns_none_for_empty_error() {
    let rule = ErrorOccurredRule;
    let data = json!({});
    assert!(
        rule.evaluate("errorOccurred", &data).is_none(),
        "no error field → must return None"
    );
}

#[test]
fn error_occurred_returns_none_for_empty_error_string() {
    let rule = ErrorOccurredRule;
    let data = json!({"error": ""});
    assert!(
        rule.evaluate("errorOccurred", &data).is_none(),
        "empty error string → must return None"
    );
}

#[test]
fn error_occurred_fail_open_when_kb_unavailable() {
    // When both the native DB path and Python fallback are unavailable,
    // the rule must return None (fail-open).
    // We use SK_DB pointing to a non-existent file (native fails-open) and
    // SK_TOOLS_DIR pointing to a temp dir without query-session.py (Python fails-open).
    let _guard = env_lock();
    let tmp = std::env::temp_dir().join("sk_error_kb_test");
    let _ = std::fs::create_dir_all(&tmp);
    let nonexistent_db = tmp.join("nonexistent.db");
    let old_tools_dir = std::env::var("SK_TOOLS_DIR").ok();
    let old_db = std::env::var("SK_DB").ok();
    std::env::set_var("SK_TOOLS_DIR", &tmp);
    std::env::set_var("SK_DB", &nonexistent_db);
    let rule = ErrorOccurredRule;
    let data = json!({"error": "some error message"});
    let result = rule.evaluate("errorOccurred", &data);
    // With no DB and no query-session.py, must fail-open (return None).
    assert!(
        result.is_none(),
        "must return None when native DB and Python fallback are both unavailable"
    );
    match old_tools_dir {
        Some(v) => std::env::set_var("SK_TOOLS_DIR", v),
        None => std::env::remove_var("SK_TOOLS_DIR"),
    }
    match old_db {
        Some(v) => std::env::set_var("SK_DB", v),
        None => std::env::remove_var("SK_DB"),
    }
    let _ = std::fs::remove_dir_all(&tmp);
}

#[test]
fn error_occurred_native_kb_search_returns_results() {
    use rusqlite::Connection;

    // Create a temp-file DB with the production ke_fts schema.
    let tmp_dir = std::env::temp_dir().join("sk_error_kb_native_test");
    // Clean up any leftover from a prior run before we start.
    let _ = std::fs::remove_dir_all(&tmp_dir);
    std::fs::create_dir_all(&tmp_dir).expect("create tmp dir");
    let db_path = tmp_dir.join("knowledge.db");

    {
        let conn = Connection::open(&db_path).expect("create temp db");
        conn.execute_batch(
                // Enable WAL mode so KnowledgeDb::open() (which sets PRAGMA journal_mode=WAL)
                // succeeds on the read-only re-open.
                "PRAGMA journal_mode=WAL;
                CREATE TABLE IF NOT EXISTS knowledge_entries (
                    id INTEGER PRIMARY KEY,
                    category TEXT, title TEXT, content TEXT, tags TEXT,
                    confidence REAL DEFAULT 1.0, occurrence_count INTEGER DEFAULT 1,
                    wing TEXT, room TEXT, session_id TEXT,
                    first_seen TEXT, last_seen TEXT
                );
                CREATE VIRTUAL TABLE IF NOT EXISTS ke_fts USING fts5(
                    title, content, tags, category, wing, room,
                    content='knowledge_entries', content_rowid='id'
                );
                INSERT INTO knowledge_entries (id, category, title, content, tags)
                VALUES (1, 'mistake', 'Rust borrow checker', 'Ownership rules prevent data races', 'rust,borrow');
                INSERT INTO ke_fts (rowid, title, content, tags, category, wing, room)
                SELECT id, title, content, COALESCE(tags,''), category,
                       COALESCE(wing,''), COALESCE(room,'')
                FROM knowledge_entries;",
            )
            .expect("setup temp db");
    }

    std::env::set_var("SK_DB", &db_path);
    let rule = ErrorOccurredRule;
    // "borrow checker" matches title "Rust borrow checker" via FTS.
    let data = json!({"error": "borrow checker"});
    let result = rule.evaluate("errorOccurred", &data);
    std::env::remove_var("SK_DB");
    let _ = std::fs::remove_dir_all(&tmp_dir);

    // The native path should find the entry and return Some.
    assert!(
        result.is_some(),
        "native KB search should return Some when DB has a matching entry"
    );
}

#[test]
fn error_occurred_extracts_message_from_dict_error() {
    // Test that dict-style error {"error": {"message": "msg"}} is parsed correctly.
    // We just verify the extraction logic, not the subprocess call.
    let rule = ErrorOccurredRule;
    let data = json!({"error": {"message": ""}});
    // Empty message → None (extraction works, just nothing to search).
    assert!(rule.evaluate("errorOccurred", &data).is_none());
}

#[test]
fn error_occurred_fires_only_on_error_occurred() {
    let rule = ErrorOccurredRule;
    assert!(rule.events().contains(&"errorOccurred"));
    assert!(!rule.events().contains(&"sessionStart"));
    assert!(!rule.events().contains(&"preToolUse"));
    assert!(!rule.events().contains(&"postToolUse"));
}

#[test]
fn error_occurred_has_no_tool_filter() {
    let rule = ErrorOccurredRule;
    assert!(
        rule.tools().is_empty(),
        "ErrorOccurredRule should have no tool filter"
    );
}

#[test]
fn error_occurred_name_is_error_kb() {
    let rule = ErrorOccurredRule;
    assert_eq!(rule.name(), "error-kb");
}

// ── SkillUsageRule ────────────────────────────────────────────────────────

#[test]
fn skill_usage_rule_name_and_events() {
    let rule = SkillUsageRule;
    assert_eq!(rule.name(), "skill-usage");
    assert!(rule.events().contains(&"postToolUse"));
    assert!(!rule.events().contains(&"preToolUse"));
}

#[test]
fn skill_usage_rule_only_fires_for_skill_tool() {
    let rule = SkillUsageRule;
    assert_eq!(rule.tools(), &["skill"]);
    assert!(!rule.tools().is_empty());
}

#[test]
fn skill_usage_rule_in_all_rules() {
    let names: Vec<&str> = all_rules().iter().map(|r| r.name()).collect();
    assert!(
        names.contains(&"skill-usage"),
        "skill-usage must be in all_rules(); got: {names:?}"
    );
}

#[test]
fn skill_usage_all_rules_is_post_tool_use() {
    let rules = all_rules();
    let su = rules.iter().find(|r| r.name() == "skill-usage").unwrap();
    assert!(
        su.events().contains(&"postToolUse"),
        "skill-usage must be registered for postToolUse"
    );
}

#[test]
fn skill_usage_detect_absent_tool_result_is_loaded() {
    let data = json!({"toolName": "skill"});
    assert_eq!(SkillUsageRule::detect_secondary_event(&data), "loaded");
}

#[test]
fn skill_usage_detect_empty_string_is_loaded() {
    let data = json!({"toolName": "skill", "toolResult": ""});
    assert_eq!(SkillUsageRule::detect_secondary_event(&data), "loaded");
}

#[test]
fn skill_usage_detect_exit_code_zero_is_loaded() {
    let data = json!({"toolResult": {"exitCode": 0, "output": "skill skipped"}});
    assert_eq!(
        SkillUsageRule::detect_secondary_event(&data),
        "loaded",
        "exitCode=0 must always yield loaded even with skip markers"
    );
}

#[test]
fn skill_usage_detect_exit_code_nonzero_is_skipped() {
    let data = json!({"toolResult": {"exitCode": 1, "output": ""}});
    assert_eq!(SkillUsageRule::detect_secondary_event(&data), "skipped");
}

#[test]
fn skill_usage_detect_exit_code_key_precedence() {
    // exitCode takes precedence over exit_code when both present.
    let data = json!({"toolResult": {"exitCode": 0, "exit_code": 1}});
    assert_eq!(
        SkillUsageRule::detect_secondary_event(&data),
        "loaded",
        "exitCode=0 must win over exit_code=1"
    );
}

#[test]
fn skill_usage_detect_skip_marker_short_output() {
    let data = json!({"toolResult": "skill not found"});
    assert_eq!(SkillUsageRule::detect_secondary_event(&data), "skipped");
}

#[test]
fn skill_usage_detect_long_output_is_loaded() {
    let long = "x".repeat(300);
    let data = json!({"toolResult": long});
    assert_eq!(SkillUsageRule::detect_secondary_event(&data), "loaded");
}

#[test]
fn skill_usage_detect_generic_error_prose_is_loaded() {
    // Generic error text in non-skip context must not be misclassified.
    for prose in &[
        "Use error handling patterns",
        "Task failed gracefully",
        "cannot load config",
        "unable to load module",
    ] {
        let data = json!({"toolResult": prose});
        assert_eq!(
            SkillUsageRule::detect_secondary_event(&data),
            "loaded",
            "generic prose {prose:?} must not be classified as skipped"
        );
    }
}

#[test]
fn skill_usage_evaluate_returns_none_on_success() {
    let rule = SkillUsageRule;
    // Even with a valid payload the rule returns None (informational only).
    let data = json!({
        "toolName": "skill",
        "toolInput": {"skill": "karpathy-guidelines"},
        "toolResult": "x".repeat(300),
        "sessionId": "test-sess-001",
    });
    // The rule may attempt to write to the real DB; fail-open means it
    // returns None regardless of whether the write succeeds.
    assert!(
        rule.evaluate("postToolUse", &data).is_none(),
        "evaluate must return None (informational)"
    );
}

#[test]
fn skill_usage_evaluate_missing_skill_name_returns_none() {
    let rule = SkillUsageRule;
    let data = json!({
        "toolName": "skill",
        "toolInput": {},
        "toolResult": "",
        "sessionId": "test-sess-002",
    });
    // Missing skill name → early return None (no DB write attempted).
    assert!(rule.evaluate("postToolUse", &data).is_none());
}

#[test]
fn skill_usage_record_events_is_fail_open() {
    // record_events to an unwritable path must not panic.
    // Use a path with a null byte which is invalid on all platforms.
    let bad_path = std::path::Path::new("\x00invalid\x00db.db");
    SkillUsageRule::record_events("test-skill", &["triggered", "loaded"], "sess", bad_path);
    // No panic = pass.
}
