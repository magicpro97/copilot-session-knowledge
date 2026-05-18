use super::*;

// ---------------------------------------------------------------------------
// TrackEditsRule — helpers
// ---------------------------------------------------------------------------

/// Code extensions that count as code edits.
///
/// Mirrors Python `CODE_EXTENSIONS` in `hooks/rules/common.py`.
/// Markdown (.md) is intentionally excluded: session-research and docs writes
/// must not inflate multi-module edit counters.
pub(crate) const TRACK_CODE_EXTENSIONS: &[&str] = &[
    ".py", ".kt", ".ts", ".tsx", ".js", ".jsx", ".swift", ".java", ".go", ".rs", ".json", ".yaml",
    ".yml", ".xml", ".html", ".css", ".toml", ".sh", ".bat", ".ps1",
];

/// Return `true` if `path` is under the Copilot session-state directory.
///
/// Mirrors Python `is_session_path(path)` in `hooks/rules/common.py`.
pub(crate) fn is_track_session_path(path: &str) -> bool {
    path.contains("session-state") || path.contains(".copilot/session-state")
}

/// Return `true` when `path` should be skipped by `AutoBugDetectorRule`.
///
/// Mirrors Python `is_session_path(path)` semantics more precisely than the
/// broad `is_track_session_path` helper, which matches any path whose name
/// contains the substring `session-state`.  That is too wide for this rule:
/// legitimate project files like `src/session-state-manager.py`,
/// `docs/session-state.md`, or `tests/test_session_state.py` would be
/// wrongly skipped.
///
/// This function only matches:
///   - Paths containing the literal segment `".copilot/session-state"` (Unix/cross-platform)
///   - Paths containing the literal segment `".copilot\session-state"` (Windows backslash form)
///   - Absolute paths whose prefix matches `<HOME>/.copilot/session-state` (or the Windows
///     backslash equivalent), mirroring Python's `Path(path).resolve().startswith(session_dir)`.
pub(crate) fn is_auto_bug_session_path(path: &str) -> bool {
    // Literal segment checks — mirrors Python's `".copilot/session-state" in path` fallback.
    if path.contains(".copilot/session-state") || path.contains(".copilot\\session-state") {
        return true;
    }
    // Absolute home-relative prefix check — mirrors Python's `Path(path).resolve().startswith(...)`.
    if let Some(home) = std::env::var_os("HOME").or_else(|| std::env::var_os("USERPROFILE")) {
        let home_str = home.to_string_lossy();
        // Unix / macOS: /home/user/.copilot/session-state/...
        if path.starts_with(format!("{}/.copilot/session-state", home_str).as_str()) {
            return true;
        }
        // Windows: C:\Users\user\.copilot\session-state\...
        if path.starts_with(format!("{}\\.copilot\\session-state", home_str).as_str()) {
            return true;
        }
    }
    false
}

/// Return `true` if `path` has a code extension (case-insensitive).
///
/// Mirrors Python `suffix in CODE_EXTENSIONS` check.
pub(crate) fn has_code_extension(path: &str) -> bool {
    let lower = path.to_lowercase();
    TRACK_CODE_EXTENSIONS.iter().any(|ext| lower.ends_with(ext))
}

/// Run `git status --porcelain -uall` and return the set of modified/added files.
///
/// Returns an empty set on any error (fail-open).
/// Mirrors Python `TrackEditsRule._get_git_modified()`.
pub(crate) fn get_git_modified() -> HashSet<String> {
    let output = match Command::new("git")
        .args(["status", "--porcelain", "-uall"])
        .stdout(Stdio::piped())
        .stderr(Stdio::null())
        .output()
    {
        Ok(o) if o.status.success() => o,
        _ => return HashSet::new(), // fail-open: git not available or not a repo
    };
    let stdout = String::from_utf8_lossy(&output.stdout);
    let mut files = HashSet::new();
    for line in stdout.lines() {
        if line.len() < 4 {
            continue;
        }
        // Python: `if line[:2].strip().startswith("D"): continue`
        if line[..2].trim().starts_with('D') {
            continue; // skip deleted files
        }
        let mut filepath = line[3..].trim().to_string();
        // Rename format: "old -> new"
        if let Some(pos) = filepath.find(" -> ") {
            filepath = filepath[pos + 4..].to_string();
        }
        if !filepath.is_empty() {
            files.insert(filepath);
        }
    }
    files
}

/// Load the previously-seen set from the `git-modified-seen` marker file.
///
/// Plain text (not HMAC-signed), one path per line.
/// Mirrors Python `TrackEditsRule._load_seen()`.
pub(crate) fn load_git_modified_seen() -> HashSet<String> {
    let path = markers_dir().join("git-modified-seen");
    match fs::read_to_string(&path) {
        Ok(content) => content
            .lines()
            .filter(|l| !l.is_empty())
            .map(|l| l.to_string())
            .collect(),
        Err(_) => HashSet::new(),
    }
}

/// Save the seen set to the `git-modified-seen` marker file.
///
/// Sorted, newline-separated, plain text (matches Python `_save_seen()`).
pub(crate) fn save_git_modified_seen(seen: &HashSet<String>) {
    let mdir = markers_dir();
    let _ = fs::create_dir_all(&mdir);
    let path = mdir.join("git-modified-seen");
    let mut lines: Vec<&str> = seen.iter().map(|s| s.as_str()).collect();
    lines.sort_unstable();
    let _ = fs::write(&path, lines.join("\n"));
}

// ---------------------------------------------------------------------------
// TrackEditsRule
// ---------------------------------------------------------------------------

/// postToolUse rule: tracks file changes after bash commands (git status scan)
/// and updates HMAC-signed counters + list markers.
///
/// Full port of the write-side in `hooks/rules/edit_tracker.py::TrackEditsRule`:
///   - Runs `git status --porcelain -uall` to discover newly modified/added files.
///   - Computes the delta vs. `git-modified-seen` (the persistent seen set).
///   - For code file changes: increments `code-edit-count` and appends to the
///     `tentacle-edits` list marker, both HMAC-signed via `marker_auth`.
///   - For Python file changes: increments `py-edit-count` (HMAC-signed).
///   - Saves the updated seen set (plain text — not HMAC-signed, mirrors Python).
///
/// Counter preservation: counters are **read first** via `verify_counter` and
/// incremented by the delta size, never reset.  This is safe even across
/// Python↔Rust migration because both use the same on-disk format.
///
/// Dual-writer safety:
///   - For `bash` tool: only this Rust rule writes these counters on the native
///     direct path.  On the managed path, `hook_runner.py` runs the Python
///     `TrackEditsRule` instead.  The two paths are mutually exclusive.
///   - For `edit`/`create` tools: counter writes remain Python-owned via
///     `TestReminderRule` on the managed path.  This rule still appends the
///     current code path to the `tentacle-edits` list marker on the native
///     direct path so `TentacleSuggestRule` can accumulate multi-file state
///     across direct edit/create calls without becoming a writer itself.
///
/// Fail-open at every step: any subprocess error, filesystem error, or missing
/// field causes the rule to return `None` or an informational message only —
/// never a deny result.
pub struct TrackEditsRule;

impl HookRule for TrackEditsRule {
    fn name(&self) -> &'static str {
        "track-edits"
    }

    fn events(&self) -> &'static [&'static str] {
        &["postToolUse"]
    }

    fn tools(&self) -> &'static [&'static str] {
        // edit, create, bash — the three tools that modify files.
        &["edit", "create", "bash"]
    }

    fn evaluate(&self, _event: &str, data: &Value) -> Option<Value> {
        let tool = data.get("toolName").and_then(|v| v.as_str()).unwrap_or("");

        // For edit/create: keep counters Python-owned, but still append the current
        // code path to `tentacle-edits` so TentacleSuggestRule can accumulate
        // state across direct edit/create calls.
        if tool != "bash" {
            if tool == "edit" || tool == "create" {
                if let Some(path) = hook_file_path(data) {
                    if has_code_extension(path) && !is_track_session_path(path) {
                        let mdir = markers_dir();
                        let list_path = mdir.join("tentacle-edits");
                        let mut existing = marker_auth::verify_list_marker(&list_path);
                        existing.insert(path.to_string());
                        let lines: Vec<String> = existing.into_iter().collect();
                        let _ = marker_auth::sign_list_marker(&list_path, &lines);
                    }
                }
            }
            return Some(info(&format!("[sk] {tool} tracked.")));
        }

        // --- bash path: git-status scan + HMAC counter/list-marker writes ---

        let current_modified = get_git_modified();
        if current_modified.is_empty() {
            // No modified files in the repo (or git not available) — silent.
            // Mirrors Python: `if not current_modified: return None`
            return None;
        }

        let previously_seen = load_git_modified_seen();
        let new_modifications: HashSet<String> = current_modified
            .iter()
            .filter(|f| !previously_seen.contains(*f))
            .cloned()
            .collect();

        if new_modifications.is_empty() {
            // No new modifications since the last hook run.
            // Still update the seen set to catch any deletions.
            save_git_modified_seen(&current_modified.union(&previously_seen).cloned().collect());
            return None;
        }

        let mut new_code_files: Vec<String> = new_modifications
            .iter()
            .filter(|f| has_code_extension(f) && !is_track_session_path(f))
            .cloned()
            .collect();
        let new_py_files: Vec<String> = new_modifications
            .iter()
            .filter(|f| f.ends_with(".py") && !is_track_session_path(f))
            .cloned()
            .collect();

        let mdir = markers_dir();

        // Increment HMAC-signed counters — preserve existing values (read-first).
        if !new_code_files.is_empty() {
            let counter_path = mdir.join("code-edit-count");
            let current_count = marker_auth::verify_counter(&counter_path);
            let _ = marker_auth::sign_counter(
                &counter_path,
                current_count + new_code_files.len() as i64,
            );

            // Append new files to the tentacle-edits list marker.
            let list_path = mdir.join("tentacle-edits");
            let mut existing = marker_auth::verify_list_marker(&list_path);
            for f in &new_code_files {
                existing.insert(f.clone());
            }
            let lines: Vec<String> = existing.into_iter().collect();
            let _ = marker_auth::sign_list_marker(&list_path, &lines);
        }

        if !new_py_files.is_empty() {
            let py_counter_path = mdir.join("py-edit-count");
            let py_count = marker_auth::verify_counter(&py_counter_path);
            let _ =
                marker_auth::sign_counter(&py_counter_path, py_count + new_py_files.len() as i64);
        }

        // Save the union of previously-seen and current sets.
        save_git_modified_seen(&current_modified.union(&previously_seen).cloned().collect());

        // Return informational message when code files were detected (mirrors Python).
        if !new_code_files.is_empty() {
            new_code_files.sort_unstable();
            let display: Vec<&str> = new_code_files.iter().take(5).map(|s| s.as_str()).collect();
            let files_str = display.join(", ");
            let mut msg = format!(
                "  \u{1f4dd} Detected {} file change(s) via bash: {}",
                new_code_files.len(),
                files_str
            );
            if new_code_files.len() > 5 {
                msg += &format!("\n     ... and {} more", new_code_files.len() - 5);
            }
            return Some(info(&msg));
        }

        // Only py files detected but no code files? Shouldn't happen since .py is
        // in CODE_EXTENSIONS, but guard it defensively.
        None
    }
}

// ---------------------------------------------------------------------------
// TestReminderRule
// ---------------------------------------------------------------------------

/// postToolUse test reminder after Python file edits — with counter writes (wave7).
///
/// Full parity port of `hooks/rules/edit_tracker.py::TestReminderRule`:
///   - For `edit`/`create` tools: if the target file is a `.py` source file,
///     increments `py-edit-count` (HMAC-signed) and deletes `tests-ran`.
///   - For `bash` tool: if the command detects a Python test run, touches `tests-ran`;
///     if the command detects a `.py` file write, increments `py-edit-count`.
///   - Emits a reminder when count >= 3 AND count % 3 == 0 (mirrors Python threshold).
///
/// Counter format: `py-edit-count` is HMAC-signed (same on-disk format as Python).
/// Counter values are PRESERVED — read-first, delta-increment, NEVER reset.
///
/// Dual-writer safety: the managed path (`sk hooks run postToolUse`) uses Python
/// `TestReminderRule`; the native direct path uses this Rust rule.  Both paths
/// are mutually exclusive — no dual-write drift.
///
/// Informational-only: no `permissionDecision: deny` is ever returned.
/// Fail-open at every step.
pub struct TestReminderRule;

/// Return `true` when `file_path` is a Python source file (not a session-state path).
pub(crate) fn is_py_source_path(file_path: &str) -> bool {
    if !file_path.ends_with(".py") {
        return false;
    }
    // Exclude session-state paths (mirrors Python is_session_path).
    !file_path.contains("session-state")
}

/// Extract the best-effort file path from a hook payload.
///
/// Supports the shapes used across hook payloads today:
///   - `toolResult.filePath` (edit)
///   - `toolArgs.path` (edit/create direct-path tests and preToolUse)
///   - `input.filePath` (create in Python-managed postToolUse rules)
pub(crate) fn hook_file_path(data: &Value) -> Option<&str> {
    data.get("toolResult")
        .and_then(|r| r.as_object())
        .and_then(|o| o.get("filePath"))
        .and_then(|v| v.as_str())
        .filter(|s| !s.is_empty())
        .or_else(|| {
            data.get("toolArgs")
                .and_then(|a| a.as_object())
                .and_then(|o| o.get("path"))
                .and_then(|v| v.as_str())
                .filter(|s| !s.is_empty())
        })
        .or_else(|| {
            data.get("input")
                .and_then(|i| i.as_object())
                .and_then(|o| o.get("filePath"))
                .and_then(|v| v.as_str())
                .filter(|s| !s.is_empty())
        })
}

/// Return `true` when `command` appears to write to a `.py` file via bash.
///
/// Simplified mirror of `TestReminderRule._detect_py_writes()`.
pub(crate) fn bash_writes_py_file(command: &str) -> bool {
    if !command.contains(".py") {
        return false;
    }
    // Redirect-to-py: `> /some/path.py` or heredoc with `open('...py', ...)`
    (command.contains("> ") && command.ends_with(".py"))
        || (command.contains("open(") && command.contains(".py"))
        || (command.contains("sed -i") && command.contains(".py"))
}

/// Path to the `tests-ran` marker file (plain file, not HMAC-signed).
pub(crate) fn tests_ran_path() -> PathBuf {
    markers_dir().join("tests-ran")
}

/// Touch (create) the `tests-ran` marker — signals that tests were run.
pub(crate) fn mark_tests_ran() {
    let _ = fs::create_dir_all(markers_dir());
    let _ = fs::write(tests_ran_path(), b"");
}

/// Delete the `tests-ran` marker file if it exists.
pub(crate) fn clear_tests_ran() {
    let p = tests_ran_path();
    if p.is_file() {
        let _ = fs::remove_file(p);
    }
}

/// Return `true` when `command` looks like a test runner invocation.
///
/// Mirrors the bash branch of `TestReminderRule.evaluate()` in `edit_tracker.py`.
pub(crate) fn detect_test_run(command: &str) -> bool {
    command.contains("test_security.py")
        || command.contains("test_fixes.py")
        || command.contains("run_all_tests.py")
        || command.contains("pytest")
}

/// Increment `py-edit-count` (HMAC-signed) by `added`, clear `tests-ran`, and
/// return the new count.
///
/// Counter values are PRESERVED (read-first, delta-increment — never reset).
/// Fail-open: on any error the new value is still returned (best-effort write).
pub(crate) fn increment_py_edit_count(added: i64) -> i64 {
    let mdir = markers_dir();
    let _ = fs::create_dir_all(&mdir);
    let counter_path = mdir.join("py-edit-count");
    let current = marker_auth::verify_counter(&counter_path);
    let new_count = current + added;
    let _ = marker_auth::sign_counter(&counter_path, new_count);
    clear_tests_ran();
    new_count
}

impl HookRule for TestReminderRule {
    fn name(&self) -> &'static str {
        "test-reminder"
    }

    fn events(&self) -> &'static [&'static str] {
        &["postToolUse"]
    }

    fn tools(&self) -> &'static [&'static str] {
        &["edit", "create", "bash"]
    }

    fn evaluate(&self, _event: &str, data: &Value) -> Option<Value> {
        let tool_name = data.get("toolName").and_then(|v| v.as_str()).unwrap_or("");

        match tool_name {
            "edit" | "create" => {
                if !hook_file_path(data).is_some_and(is_py_source_path) {
                    return None;
                }
                let count = increment_py_edit_count(1);
                if count >= 3 && count % 3 == 0 {
                    return Some(info(&format!(
                        "\n  \u{26a0}\u{fe0f} TEST REMINDER: {count} Python files edited without running tests!\n  \
                         Run: python3 test_security.py && python3 test_fixes.py\n"
                    )));
                }
                None
            }
            "bash" => {
                let command = data
                    .get("toolArgs")
                    .and_then(|a| a.as_object())
                    .and_then(|o| o.get("command"))
                    .and_then(|v| v.as_str())
                    .unwrap_or("");

                // Test run detected: touch tests-ran, no reminder.
                if detect_test_run(command) {
                    mark_tests_ran();
                    return None;
                }

                // Python file write detected: increment counter + maybe remind.
                if bash_writes_py_file(command) {
                    let count = increment_py_edit_count(1);
                    if count >= 3 && count % 3 == 0 {
                        return Some(info(&format!(
                            "\n  \u{26a0}\u{fe0f} TEST REMINDER: {count} Python files edited without running tests!\n  \
                             Run: python3 test_security.py && python3 test_fixes.py\n"
                        )));
                    }
                }
                None
            }
            _ => None,
        }
    }
}

// ---------------------------------------------------------------------------
// NextjsTypecheckReminderRule
// ---------------------------------------------------------------------------

/// postToolUse typecheck reminder after browse-ui TS/TSX edits — with counter (wave7).
///
/// Full parity port of `hooks/rules/nextjs_typecheck.py::NextjsTypecheckRule`:
///   - Fires on `edit` and `create` tools when the path is a `.ts` or `.tsx` file
///     under `browse-ui/`.
///   - Increments `ts-edit-count` (plain-text integer, NOT HMAC-signed — mirrors
///     the Python format exactly).  Counter values are preserved (read-first,
///     delta-increment — no resets).
///   - Emits a reminder when count >= 3 AND count % 3 == 0 (mirrors Python threshold).
///
/// Counter format: `ts-edit-count` is a plain UTF-8 integer string (same as Python).
/// Dual-writer safety: managed path uses Python; native direct path uses this Rust rule.
///
/// Informational-only: no `permissionDecision: deny` is ever returned.
/// Fail-open: any missing field or filesystem error → `None`.
pub struct NextjsTypecheckReminderRule;

/// Read the `ts-edit-count` plain-text counter (NOT HMAC-signed).
///
/// Mirrors the counter read in `nextjs_typecheck.py::NextjsTypecheckRule.evaluate()`.
/// Returns 0 on missing file or parse error.
pub(crate) fn read_ts_edit_count() -> i64 {
    let path = markers_dir().join("ts-edit-count");
    if !path.is_file() {
        return 0;
    }
    fs::read_to_string(&path)
        .ok()
        .and_then(|s| s.trim().parse::<i64>().ok())
        .unwrap_or(0)
}

/// Write the `ts-edit-count` plain-text counter (NOT HMAC-signed).
pub(crate) fn write_ts_edit_count(value: i64) {
    let mdir = markers_dir();
    let _ = fs::create_dir_all(&mdir);
    let _ = fs::write(mdir.join("ts-edit-count"), value.to_string());
}

impl HookRule for NextjsTypecheckReminderRule {
    fn name(&self) -> &'static str {
        "nextjs-typecheck-reminder"
    }

    fn events(&self) -> &'static [&'static str] {
        &["postToolUse"]
    }

    fn tools(&self) -> &'static [&'static str] {
        &["edit", "create"]
    }

    fn evaluate(&self, _event: &str, data: &Value) -> Option<Value> {
        let file_path = hook_file_path(data)?;
        if file_path.is_empty() {
            return None;
        }

        // Scope: .ts / .tsx files under browse-ui/ only.
        let is_ts = file_path.ends_with(".ts") || file_path.ends_with(".tsx");
        if !is_ts {
            return None;
        }
        // Normalise separators so Windows paths match.
        let norm = file_path.replace('\\', "/");
        if !norm.contains("browse-ui/") {
            return None;
        }

        // Increment plain-text counter (NOT HMAC — mirrors Python format exactly).
        let count = read_ts_edit_count() + 1;
        write_ts_edit_count(count);

        if count >= 3 && count % 3 == 0 {
            return Some(info(&format!(
                "\n  \u{26a0}\u{fe0f} TS REMINDER: {count} browse-ui .ts/.tsx files edited.\n  \
                 Run: cd browse-ui && pnpm typecheck\n"
            )));
        }
        None
    }
}

// ---------------------------------------------------------------------------
// ReadBeforeEditRule
// ---------------------------------------------------------------------------

/// Tracks viewed files and emits informational warning on edit of unread files.
///
/// Ports `hooks/rules/read_before_edit.py::ReadBeforeEditRule` (wave7):
///   - postToolUse [view/grep/glob]: records `toolInput.path` or `toolArgs.path`
///     in the HMAC-signed `viewed-files` list marker when the path is absolute.
///     Preserves existing entries (append-only).
///   - preToolUse [edit/create]: if the absolute target path has a code extension
///     and is NOT in `viewed-files`, emits an informational warning (Rule 1 reminder).
///     NEVER denies — informational-only, fail-open.
///
/// `viewed-files` marker format: HMAC-signed list marker (matching Python).
/// Fail-open at every step.
pub struct ReadBeforeEditRule;

/// Code extensions for read-before-edit enforcement.
///
/// Mirrors the extension set in `read_before_edit.py`.
pub(crate) const READ_BEFORE_EDIT_EXTENSIONS: &[&str] = &[
    ".py", ".ts", ".tsx", ".js", ".jsx", ".md", ".json", ".yaml", ".yml", ".toml", ".css", ".html",
    ".sh", ".go", ".rs", ".swift", ".kt", ".java",
];

/// Extract a file path from `toolInput.path` or `toolArgs.path`.
///
/// `toolInput` is the Copilot CLI native field; `toolArgs` is the normalised
/// form used by some existing rules.  Try both to maximise coverage.
pub(crate) fn tool_input_path(data: &Value) -> Option<&str> {
    data.get("toolInput")
        .and_then(|v| v.as_object())
        .and_then(|o| o.get("path"))
        .and_then(|v| v.as_str())
        .filter(|s| !s.is_empty())
        .or_else(|| {
            data.get("toolArgs")
                .and_then(|v| v.as_object())
                .and_then(|o| o.get("path"))
                .and_then(|v| v.as_str())
                .filter(|s| !s.is_empty())
        })
}

/// Return `true` when `path` has one of the code extensions we track.
pub(crate) fn is_read_before_edit_ext(path: &str) -> bool {
    let lower = path.to_lowercase();
    READ_BEFORE_EDIT_EXTENSIONS
        .iter()
        .any(|ext| lower.ends_with(ext))
}

/// Return `true` when `path` is absolute (Unix `/` or Windows drive / UNC).
pub(crate) fn is_absolute_path(path: &str) -> bool {
    if path.starts_with('/') || path.starts_with("\\\\") || path.starts_with("//") {
        return true;
    }
    // Windows drive: `C:\` or `C:/`
    let chars: Vec<char> = path.chars().collect();
    chars.len() >= 3 && chars[1] == ':' && (chars[2] == '\\' || chars[2] == '/')
}

impl HookRule for ReadBeforeEditRule {
    fn name(&self) -> &'static str {
        "read-before-edit"
    }

    fn events(&self) -> &'static [&'static str] {
        &["preToolUse", "postToolUse"]
    }

    fn tools(&self) -> &'static [&'static str] {
        &[] // all tools
    }

    fn evaluate(&self, event: &str, data: &Value) -> Option<Value> {
        let tool = data.get("toolName").and_then(|v| v.as_str()).unwrap_or("");

        if event == "postToolUse" {
            // Track files read via view/grep/glob.
            if tool == "view" || tool == "grep" || tool == "glob" {
                if let Some(path) = tool_input_path(data) {
                    if is_absolute_path(path) {
                        let viewed_path = markers_dir().join("viewed-files");
                        let mut viewed = marker_auth::verify_list_marker(&viewed_path);
                        viewed.insert(path.to_string());
                        let lines: Vec<String> = viewed.into_iter().collect();
                        let _ = marker_auth::sign_list_marker(&viewed_path, &lines);
                    }
                }
            }
            return None;
        }

        if event == "preToolUse" {
            // Only for edit/create.
            if tool != "edit" && tool != "create" {
                return None;
            }
            let path = tool_input_path(data)?;
            if !is_absolute_path(path) {
                return None;
            }
            // Scope: code extensions only.
            if !is_read_before_edit_ext(path) {
                return None;
            }
            let viewed_path = markers_dir().join("viewed-files");
            let viewed = marker_auth::verify_list_marker(&viewed_path);
            if !viewed.contains(path) {
                let basename = std::path::Path::new(path)
                    .file_name()
                    .and_then(|n| n.to_str())
                    .unwrap_or(path);
                // Informational warning — no deny (fail-open mirrors Python).
                return Some(info(&format!(
                    "\u{26a0} {tool} on {basename} \u{2014} file not read in this session \
                     (Rule 1: investigate before acting)"
                )));
            }
        }

        None
    }
}
