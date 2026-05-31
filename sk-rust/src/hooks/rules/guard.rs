use super::*;

// ---------------------------------------------------------------------------
// SubagentGitGuardRule
// ---------------------------------------------------------------------------

/// Blocks `git commit` and `git push` bash commands while the
/// `dispatched-subagent-active` marker is present and fresh.
///
/// Mirrors `hooks/rules/subagent_guard.py::SubagentGitGuardRule`.
///
/// Differences vs. the Python version:
///   - HMAC verification via [`marker_auth::verify_marker`] (wave6): the
///     marker signature is validated before TTL data is trusted. An
///     unsigned or tampered marker causes the function to return `false`
///     (fail-open — no new denial paths from authentication problems).
///   - No-secret mode: `verify_marker` returns `true` for any existing
///     file, preserving backward compatibility.
///   - Repo-scope check omitted (conservative: blocks when uncertain).
///   - Mixed new/old marker format both handled (see `subagent_marker_is_fresh`).
pub struct SubagentGitGuardRule;

pub(crate) const SUBAGENT_MARKER_TTL_SECS: u64 = 14400; // 4 hours

/// Return `true` iff the dispatched-subagent-active marker is present and fresh.
///
/// Fail-open: any read / parse error → `false` (allow through).
///
/// Wave6: calls `marker_auth::verify_marker` before trusting TTL data.
/// - With a secret: unsigned or tampered markers are not trusted → `false`.
/// - No secret: backward-compat — any existing file is accepted.
///   Authentication failures never add new denials (fail-open).
pub(crate) fn subagent_marker_is_fresh() -> bool {
    let path = markers_dir().join("dispatched-subagent-active");
    if !path.is_file() {
        return false;
    }

    // HMAC pre-check (wave6): verify marker authenticity before trusting
    // TTL data.  With a secret configured, an unsigned or tampered marker
    // is not trusted and we return false (fail-open — no new denials).
    // Without a secret, verify_marker returns true for any existing file
    // (backward-compat, mirrors Python no-secret behaviour).
    if !marker_auth::verify_marker(&path, "dispatched-subagent-active") {
        return false;
    }

    let content = match fs::read_to_string(&path) {
        Ok(c) => c,
        Err(_) => return false, // fail-open
    };

    let data: Value = match serde_json::from_str(content.trim()) {
        Ok(v) => v,
        Err(_) => return false, // fail-open
    };

    // Timestamp check.
    let ts = match data.get("ts").and_then(|v| v.as_u64()) {
        Some(t) => t,
        None => return false, // missing ts → fail-open
    };

    let now = SystemTime::now()
        .duration_since(UNIX_EPOCH)
        .unwrap_or_default()
        .as_secs();

    if now.saturating_sub(ts) >= SUBAGENT_MARKER_TTL_SECS {
        return false; // expired
    }

    // Zombie check: active_tentacles = [] means the dispatch is complete.
    if let Some(active) = data.get("active_tentacles") {
        if let Some(arr) = active.as_array() {
            if arr.is_empty() {
                return false;
            }
        }
    }

    true
}

/// Return the tentacle name(s) from the marker for UX messaging (best-effort).
pub(crate) fn read_tentacle_info() -> String {
    let path = markers_dir().join("dispatched-subagent-active");
    let content = match fs::read_to_string(&path) {
        Ok(c) => c,
        Err(_) => return String::new(),
    };
    let data: Value = match serde_json::from_str(content.trim()) {
        Ok(v) => v,
        Err(_) => return String::new(),
    };

    if let Some(active) = data.get("active_tentacles").and_then(|v| v.as_array()) {
        let names: Vec<String> = active
            .iter()
            .filter_map(|entry| {
                if let Some(s) = entry.as_str() {
                    Some(s.to_string())
                } else if let Some(obj) = entry.as_object() {
                    obj.get("name")
                        .and_then(|v| v.as_str())
                        .map(|s| s.to_string())
                } else {
                    None
                }
            })
            .collect();
        if !names.is_empty() {
            return names.join(", ");
        }
    }

    // Fallback: old single-owner format.
    data.get("tentacle")
        .or_else(|| data.get("detail"))
        .and_then(|v| v.as_str())
        .unwrap_or("")
        .to_string()
}

/// Detect `git commit` or `git push` in a bash command string.
pub(crate) fn command_is_git_commit_or_push(command: &str) -> bool {
    contains_ordered_shell_tokens(command, &["git", "commit"])
        || contains_ordered_shell_tokens(command, &["git", "push"])
}

impl HookRule for SubagentGitGuardRule {
    fn name(&self) -> &'static str {
        "subagent-git-guard"
    }

    fn events(&self) -> &'static [&'static str] {
        &["preToolUse"]
    }

    fn tools(&self) -> &'static [&'static str] {
        &["bash"]
    }

    fn evaluate(&self, _event: &str, data: &Value) -> Option<Value> {
        let tool_args = data.get("toolArgs").and_then(|v| v.as_object())?;
        let command = tool_args.get("command").and_then(|v| v.as_str())?;

        if !command_is_git_commit_or_push(command) {
            return None;
        }

        if !subagent_marker_is_fresh() {
            return None;
        }

        let tentacle_info = read_tentacle_info();
        let detail = if tentacle_info.is_empty() {
            String::new()
        } else {
            format!(" (tentacle: {tentacle_info})")
        };

        Some(deny(&format!(
            "\u{1f6ab} SUBAGENT MODE: git commit/push blocked{detail}. \
             This session is a dispatched subagent — only the orchestrator may commit or push. \
             Write your output to handoff.md and signal the orchestrator.\n  \
             Clear marker: python ~/.copilot/tools/tentacle.py complete <name>\n  \
             Note: this check is local-only; cloud-delegated agents are not covered."
        )))
    }
}

// ---------------------------------------------------------------------------
// PnpmLockfileGuardRule
// ---------------------------------------------------------------------------

/// Blocks `git commit` when `browse-ui/package.json` is staged without
/// `browse-ui/pnpm-lock.yaml`.
///
/// Ports `hooks/rules/pnpm_lockfile_guard.py::PnpmLockfileGuardRule` (wave7):
///   1. On preToolUse / bash: checks if the command matches `git commit`.
///   2. Runs `git diff --cached --name-only` to discover staged files.
///   3. If `browse-ui/package.json` is staged AND `browse-ui/pnpm-lock.yaml`
///      is NOT staged, returns a deny result.
///
/// Fail-open at every step:
///   - `toolArgs` absent or not an object → `None`.
///   - Command does not contain `git commit` → `None`.
///   - `git diff` subprocess fails → `None`.
///   - `browse-ui/package.json` not staged → `None`.
pub struct PnpmLockfileGuardRule;

impl HookRule for PnpmLockfileGuardRule {
    fn name(&self) -> &'static str {
        "pnpm-lockfile-guard"
    }

    fn events(&self) -> &'static [&'static str] {
        &["preToolUse"]
    }

    fn tools(&self) -> &'static [&'static str] {
        &["bash"]
    }

    fn evaluate(&self, _event: &str, data: &Value) -> Option<Value> {
        let tool_args = data.get("toolArgs")?.as_object()?;
        let command = tool_args.get("command")?.as_str()?;

        // Only fire on `git commit` commands.
        if !contains_ordered_shell_tokens(command, &["git", "commit"]) {
            return None;
        }

        // Run `git diff --cached --name-only` (fail-open on any error).
        let output = Command::new("git")
            .args(["diff", "--cached", "--name-only"])
            .stdout(Stdio::piped())
            .stderr(Stdio::null())
            .output()
            .ok()?;

        let stdout = String::from_utf8_lossy(&output.stdout).into_owned();
        let staged: HashSet<String> = stdout
            .lines()
            .map(|l| l.trim().to_string())
            .filter(|l| !l.is_empty())
            .collect();

        let pkg_staged = staged.contains("browse-ui/package.json");
        let lock_staged = staged.contains("browse-ui/pnpm-lock.yaml");

        if pkg_staged && !lock_staged {
            return Some(deny(
                "\u{1f6ab} browse-ui/package.json is staged but pnpm-lock.yaml is not.\n\
                 Run: cd browse-ui && pnpm install\n\
                 Then: git add browse-ui/pnpm-lock.yaml",
            ));
        }

        None
    }
}

// ---------------------------------------------------------------------------
// SyntaxGateRule
// ---------------------------------------------------------------------------

/// Blocks `edit`/`create` on `*.py` files when the resulting file would have
/// a Python syntax error.
///
/// Mirrors `hooks/rules/syntax_gate.py::SyntaxGateRule`.
///
/// Implementation strategy (subprocess boundary):
///   1. Extract `path` from `toolArgs`.  Non-`.py` paths pass unconditionally.
///   2. For `create`: use `file_text` directly.
///   3. For `edit`: read the existing file from disk, apply the replacement
///      exactly once (count != 1 → pass, mirrors Python behaviour).
///   4. Write the resulting content to a temp file.
///   5. Spawn `python -c "import py_compile; py_compile.compile('<path>', doraise=True)"`.
///   6. Parse stderr for the error message; replace the temp path with `label`.
///   7. Return deny if syntax error found; `None` on success.
///
/// Fail-open at every step:
///   - `toolArgs` absent or not an object → `None`.
///   - `path` absent, empty, or not `.py` → `None`.
///   - `file_text` absent on `create` → `None`.
///   - File not on disk on `edit` → `None` (edit tool will handle its own error).
///   - File read fails → `None`.
///   - Replacement count != 1 → `None` (mirrors Python — edit will fail itself).
///   - Temp-file write fails → `None`.
///   - Python executable unavailable → `None`.
///   - Subprocess timeout (10 s) → `None`.
///   - Any other subprocess error → `None`.
///
/// No HMAC dependency.  No markers written.  Pure syntax check.
pub struct SyntaxGateRule;

/// Run `python -c "import py_compile; py_compile.compile(<tmp>, doraise=True)"` on
/// `content`, using `label` as the display path in any error message.
///
/// Returns `Some(error_string)` when a `SyntaxError` is detected, `None` otherwise.
/// All failure paths return `None` (fail-open).
pub(crate) fn check_python_syntax(content: &str, label: &str) -> Option<String> {
    use crate::config::python_exe;

    let python = python_exe();

    // Write content to a temp file with .py suffix.
    let tmp_dir = std::env::temp_dir();
    let tmp_path = tmp_dir.join(format!("sk_syntax_{}_{}.py", std::process::id(), {
        use std::time::{SystemTime, UNIX_EPOCH};
        SystemTime::now()
            .duration_since(UNIX_EPOCH)
            .map(|d| d.subsec_nanos())
            .unwrap_or(0)
    }));

    if fs::write(&tmp_path, content.as_bytes()).is_err() {
        return None; // fail-open: can't write temp file
    }

    let tmp_str = tmp_path.to_string_lossy().into_owned();
    // Build inline Python: compile the temp file and raise on error.
    let script = format!(
        "import py_compile; py_compile.compile({:?}, doraise=True)",
        tmp_str
    );

    // Spawn the subprocess.
    let mut child = match Command::new(python)
        .args(["-c", &script])
        .stdout(Stdio::null())
        .stderr(Stdio::piped())
        .spawn()
    {
        Ok(c) => c,
        Err(_) => {
            let _ = fs::remove_file(&tmp_path);
            return None; // fail-open: Python unavailable
        }
    };

    // Poll with 10s timeout (mirrors AutoBriefingRule timeout pattern).
    let deadline = Instant::now() + Duration::from_secs(10);
    let timed_out = loop {
        match child.try_wait() {
            Ok(Some(_)) => break false,
            Ok(None) => {
                if Instant::now() >= deadline {
                    let _ = child.kill();
                    let _ = child.wait();
                    let _ = fs::remove_file(&tmp_path);
                    return None; // fail-open: timeout
                }
                std::thread::sleep(Duration::from_millis(50));
            }
            Err(_) => {
                let _ = fs::remove_file(&tmp_path);
                return None; // fail-open
            }
        }
    };

    let _ = fs::remove_file(&tmp_path);

    if timed_out {
        return None;
    }

    // Collect output.
    let output = match child.wait_with_output() {
        Ok(o) => o,
        Err(_) => return None, // fail-open
    };

    if output.status.success() {
        return None; // no syntax error
    }

    // Parse stderr; replace temp path with the display label (mirrors Python).
    let stderr = String::from_utf8_lossy(&output.stderr).into_owned();
    let msg = stderr.replace(&tmp_str, label).trim().to_string();
    Some(if msg.is_empty() {
        "Syntax error detected".to_string()
    } else {
        msg
    })
}

impl HookRule for SyntaxGateRule {
    fn name(&self) -> &'static str {
        "syntax-gate"
    }

    fn events(&self) -> &'static [&'static str] {
        &["preToolUse"]
    }

    fn tools(&self) -> &'static [&'static str] {
        &["edit", "create"]
    }

    fn evaluate(&self, _event: &str, data: &Value) -> Option<Value> {
        let tool_name = data.get("toolName").and_then(|v| v.as_str())?;
        let tool_args = data.get("toolArgs")?.as_object()?;

        let file_path = tool_args.get("path").and_then(|v| v.as_str())?;
        if file_path.is_empty() || !file_path.ends_with(".py") {
            return None;
        }

        let content: String = if tool_name == "create" {
            // Fail-open: file_text absent → pass through.
            tool_args.get("file_text")?.as_str()?.to_string()
        } else if tool_name == "edit" {
            let disk_path = Path::new(file_path);
            if !disk_path.is_file() {
                // File absent — edit tool will handle its own error.
                return None;
            }
            let original = match fs::read_to_string(disk_path) {
                Ok(s) => s,
                Err(_) => return None, // fail-open: read error
            };
            let old_str = tool_args
                .get("old_str")
                .and_then(|v| v.as_str())
                .unwrap_or("");
            let new_str = tool_args
                .get("new_str")
                .and_then(|v| v.as_str())
                .unwrap_or("");

            // Python behaviour: count != 1 → pass (edit tool will fail itself).
            let count = original.matches(old_str).count();
            if count != 1 {
                return None;
            }
            original.replacen(old_str, new_str, 1)
        } else {
            return None;
        };

        if let Some(error) = check_python_syntax(&content, file_path) {
            let display_name = Path::new(file_path)
                .file_name()
                .and_then(|n| n.to_str())
                .unwrap_or(file_path);
            return Some(deny(&format!(
                "\u{1f6ab} Syntax gate blocked: {display_name} would introduce a \
                 SyntaxError.\n\n{error}\n\n\
                 Fix the syntax error before applying this edit."
            )));
        }

        None
    }
}

// ---------------------------------------------------------------------------
// BlockEditDistRule
// ---------------------------------------------------------------------------

/// Blocks direct edits or creates targeting `browse-ui/dist/` build artifacts.
///
/// Mirrors `hooks/rules/block_edit_dist.py::BlockEditDistRule`.
///
/// Scope: preToolUse / edit + create only.
/// Fail-open: returns `None` when `toolArgs` or `path` is absent or malformed.
/// Path comparison is separator-normalised (Windows `\` → `/`) so both
/// Unix and Windows path representations are caught.
pub struct BlockEditDistRule;

impl HookRule for BlockEditDistRule {
    fn name(&self) -> &'static str {
        "block-edit-dist"
    }

    fn events(&self) -> &'static [&'static str] {
        &["preToolUse"]
    }

    fn tools(&self) -> &'static [&'static str] {
        &["edit", "create"]
    }

    fn evaluate(&self, _event: &str, data: &Value) -> Option<Value> {
        // Fail-open: missing toolArgs or non-object → pass through.
        let tool_args = data.get("toolArgs")?.as_object()?;

        // Fail-open: missing or empty path → pass through.
        let file_path = tool_args.get("path")?.as_str()?;
        if file_path.is_empty() {
            return None;
        }

        // Normalise separators so Windows paths match the repository prefix.
        let rel = file_path.replace('\\', "/");

        if rel.starts_with("browse-ui/dist/") || rel.contains("/browse-ui/dist/") {
            return Some(deny(
                "\u{1f6ab} Direct edits to browse-ui/dist/ are blocked.\n\
                 These are build artifacts. Run instead:\n  \
                 cd browse-ui && pnpm build",
            ));
        }

        None
    }
}

// ---------------------------------------------------------------------------
// BlockUnsafeHtmlRule
// ---------------------------------------------------------------------------

/// Blocks edits/creates that introduce `dangerouslySetInnerHTML` in
/// TypeScript / JavaScript files without an accompanying sanitization call.
///
/// Mirrors `hooks/rules/block_unsafe_html.py::BlockUnsafeHtmlRule`.
///
/// Scope: preToolUse / edit + create for `.ts` / `.tsx` / `.js` / `.jsx` paths.
/// Fail-open at every step:
///   - `toolArgs` absent or not an object → `None`.
///   - `path` absent / empty / wrong extension → `None`.
///   - `new_str` and `file_text` both absent or empty → `None`.
///   - Pattern present AND sanitizer present → `None` (allowed).
///
/// Sanitizer patterns recognised (mirrors the Python regex):
///   - `DOMPurify.sanitize`
///   - `sanitize(`
///   - `rehype-sanitize`
pub struct BlockUnsafeHtmlRule;

/// Return `true` when the proposed content contains `dangerouslySetInnerHTML`
/// without any recognised sanitization call.
pub(crate) fn content_has_unsafe_html(content: &str) -> bool {
    if !content.contains("dangerouslySetInnerHTML") {
        return false;
    }
    // If any recognised sanitizer pattern is present in the same chunk, allow.
    let has_sanitize = content.contains("DOMPurify.sanitize")
        || content.contains("sanitize(")
        || content.contains("rehype-sanitize");
    !has_sanitize
}

impl HookRule for BlockUnsafeHtmlRule {
    fn name(&self) -> &'static str {
        "block-unsafe-html"
    }

    fn events(&self) -> &'static [&'static str] {
        &["preToolUse"]
    }

    fn tools(&self) -> &'static [&'static str] {
        &["edit", "create"]
    }

    fn evaluate(&self, _event: &str, data: &Value) -> Option<Value> {
        // Fail-open: missing toolArgs or non-object → pass through.
        let tool_args = data.get("toolArgs")?.as_object()?;

        // Fail-open: missing or empty path → pass through.
        let file_path = tool_args.get("path")?.as_str()?;
        if file_path.is_empty() {
            return None;
        }

        // Scope: TypeScript and JavaScript files only.
        let is_target_ext = file_path.ends_with(".tsx")
            || file_path.ends_with(".jsx")
            || file_path.ends_with(".ts")
            || file_path.ends_with(".js");
        if !is_target_ext {
            return None;
        }

        // Prefer new_str (edit tool), fall back to file_text (create tool).
        let new_str = tool_args
            .get("new_str")
            .and_then(|v| v.as_str())
            .unwrap_or("");
        let file_text = tool_args
            .get("file_text")
            .and_then(|v| v.as_str())
            .unwrap_or("");
        let content = if !new_str.is_empty() {
            new_str
        } else {
            file_text
        };

        // Fail-open: no proposed content to analyse → pass through.
        if content.is_empty() {
            return None;
        }

        if content_has_unsafe_html(content) {
            return Some(deny(
                "\u{1f6ab} dangerouslySetInnerHTML detected without sanitization.\n\
                 Session data may contain user-controlled content (XSS risk).\n\
                 Use DOMPurify.sanitize() or render via <Highlight> component.\n\
                 See 01-system-architecture.md \u{a7}6.4 for approved patterns.",
            ));
        }

        None
    }
}

// ---------------------------------------------------------------------------
// EnforceBriefingRule + EnforceLearnRule — wave11 helpers
// ---------------------------------------------------------------------------

/// Source-file extensions for preToolUse enforcement (includes `.md`).
///
/// Mirrors Python `SOURCE_EXTENSIONS` in `hooks/rules/common.py`.
/// Unlike `TRACK_CODE_EXTENSIONS`, `.md` is included because the briefing /
/// learn gates must also fire when an agent tries to edit documentation or
/// markdown under non-session paths.
pub(crate) const ENFORCE_SOURCE_EXTENSIONS: &[&str] = &[
    ".py", ".kt", ".ts", ".tsx", ".js", ".jsx", ".swift", ".java", ".go", ".rs", ".json", ".yaml",
    ".yml", ".xml", ".html", ".css", ".md", ".toml", ".sh", ".bat", ".ps1",
];

/// Filesystem path prefixes that are considered safe to write without a
/// briefing marker (temp dirs, device files, etc.).
///
/// Mirrors Python `SAFE_PATH_PREFIXES` in `hooks/rules/common.py`.
pub(crate) const ENFORCE_SAFE_PATH_PREFIXES: &[&str] = &["/tmp/", "/var/", "/dev/", "/proc/"];

/// Minimum code-edit count before the learn gate fires.
pub(crate) const LEARN_EDIT_THRESHOLD: i64 = 3;

/// Return `true` if `path` is a source file the enforcement rules care about.
///
/// Excludes safe-tmp prefixes and session-state paths; includes all source
/// extensions from `ENFORCE_SOURCE_EXTENSIONS`.
/// Mirrors Python `is_source_path(path)` in `hooks/rules/common.py`.
pub(crate) fn is_source_path_for_enforce(path: &str) -> bool {
    let norm = path.replace('\\', "/");
    if ENFORCE_SAFE_PATH_PREFIXES
        .iter()
        .any(|p| norm.starts_with(p))
    {
        return false;
    }
    if is_track_session_path(path) {
        return false;
    }
    let lower = path.to_lowercase();
    ENFORCE_SOURCE_EXTENSIONS
        .iter()
        .any(|ext| lower.ends_with(ext))
}

/// Return `true` if the bash command appears to write a source file.
///
/// Checks redirect targets, heredoc write patterns, `sed -i`, `tee`, `dd`,
/// and interpreter `-c/-e` calls with embedded write indicators.
/// Mirrors Python `bash_writes_source_files(command)` in `hooks/rules/common.py`.
pub(crate) fn bash_writes_source_files_detect(command: &str) -> bool {
    // Heredoc patterns: << + file-write indicators.
    if command.contains("<<") {
        let has_heredoc_write = (command.contains("open(")
            && (command.contains("'w'") || command.contains("\"w\"")))
            || command.contains("writeFileSync")
            || command.contains("writeFile(")
            || command.contains("File.write")
            || command.contains("File.open");
        if has_heredoc_write {
            return true;
        }
    }

    // Redirect `>` / `>>` and `tee` / `sed -i` paths via shared helper.
    let written = extract_written_paths_simple(command);
    for path in &written {
        if is_source_path_for_enforce(path) {
            return true;
        }
    }

    // `sed -i` modifies in place — always treat as a write.
    if command.contains("sed") && (command.contains(" -i") || command.contains("\t-i")) {
        return true;
    }

    // python3?/node/ruby/perl -[ce] with file-write indicators.
    let has_interp = command.contains("python3 ")
        || command.contains("python ")
        || command.contains("node ")
        || command.contains("ruby ")
        || command.contains("perl ");
    let has_flag = command.contains(" -c ") || command.contains(" -e ");
    if has_interp
        && has_flag
        && (command.contains("open(")
            || command.contains("writeFile")
            || command.contains("File.write")
            || command.contains("File.open"))
    {
        return true;
    }

    // `dd` with `of=` writes arbitrary bytes to a file.
    if command.contains("dd") && command.contains("of=") {
        return true;
    }

    false
}

/// Return `true` if a valid briefing marker exists for the current session.
///
/// Checks (in order):
///   1. Global `markers/briefing-done` marker (HMAC-signed).
///   2. Session-scoped `markers/briefing-done-{session_id}` (from env var).
///   3. Any `markers/briefing-*` file with `mtime` within the last 30 min that
///      carries a valid HMAC signature (fallback for older marker names).
///
/// Mirrors `EnforceBriefingRule._briefing_done()` in `hooks/rules/briefing.py`.
pub(crate) fn briefing_done() -> bool {
    let mdir = markers_dir();

    // 1. Global marker.
    if marker_auth::verify_marker(&mdir.join("briefing-done"), "briefing-done") {
        return true;
    }

    // 2. Session-scoped marker.
    let session_id = std::env::var("COPILOT_AGENT_SESSION_ID")
        .ok()
        .filter(|v| !v.is_empty())
        .unwrap_or_else(|| std::process::id().to_string());
    if !session_id.is_empty() {
        let name = format!("briefing-done-{session_id}");
        if marker_auth::verify_marker(&mdir.join(&name), &name) {
            return true;
        }
    }

    // 3. Fallback: any briefing-* file modified within the last 30 min with a
    //    valid HMAC signature.
    let cutoff = SystemTime::now()
        .duration_since(UNIX_EPOCH)
        .map(|d| d.as_secs())
        .unwrap_or(0)
        .saturating_sub(1800); // 30 minutes

    if let Ok(entries) = fs::read_dir(&mdir) {
        for entry in entries.flatten() {
            let fname = entry.file_name();
            let name_str = fname.to_string_lossy().into_owned();
            if !name_str.starts_with("briefing-") {
                continue;
            }
            if let Ok(meta) = entry.metadata() {
                if let Ok(mtime) = meta.modified() {
                    if let Ok(elapsed) = mtime.duration_since(UNIX_EPOCH) {
                        if elapsed.as_secs() > cutoff
                            && marker_auth::verify_marker(&entry.path(), &name_str)
                        {
                            return true;
                        }
                    }
                }
            }
        }
    }

    false
}

// ---------------------------------------------------------------------------
// EnforceBriefingRule
// ---------------------------------------------------------------------------

/// Deny `edit`/`create`/`bash`-writes until a valid briefing marker exists.
///
/// Mirrors `EnforceBriefingRule` in `hooks/rules/briefing.py`.  Fail-open on
/// any missing payload fields.
///
pub struct EnforceBriefingRule;

impl HookRule for EnforceBriefingRule {
    fn name(&self) -> &'static str {
        "enforce-briefing"
    }

    fn events(&self) -> &'static [&'static str] {
        &["preToolUse"]
    }

    fn tools(&self) -> &'static [&'static str] {
        &["edit", "create", "bash"]
    }

    fn evaluate(&self, _event: &str, data: &Value) -> Option<Value> {
        let tool_name = data.get("toolName").and_then(|v| v.as_str()).unwrap_or("");
        let tool_args = data
            .get("toolArgs")
            .and_then(|v| v.as_object())
            .cloned()
            .unwrap_or_default();

        if marker_auth::check_tamper_marker() {
            if tool_name == "bash" {
                let command = tool_args
                    .get("command")
                    .and_then(|v| v.as_str())
                    .unwrap_or("");
                if marker_auth::is_lock_hooks_recovery(command) {
                    return None;
                }
            }
            return Some(deny(
                "\u{1f6a8} HOOKS TAMPERED: All modifications blocked. Run: sudo python3 ~/.copilot/tools/install.py --lock-hooks",
            ));
        }

        // Determine whether this call involves a source-file write.
        let is_file_mod = if tool_name == "bash" {
            let command = tool_args
                .get("command")
                .and_then(|v| v.as_str())
                .unwrap_or("");
            // Secret-access check mirrors Python's early-exit before bash analysis.
            if marker_auth::is_secret_access(command) {
                return Some(deny("\u{1f512} Access to protected hook files is blocked."));
            }
            bash_writes_source_files_detect(command)
        } else {
            // edit / create always count as file modifications.
            true
        };

        if !is_file_mod {
            return None;
        }

        if briefing_done() {
            return None;
        }

        Some(deny(
            "\u{26a0}\u{fe0f} BRIEFING REQUIRED: Run briefing before editing code. \
             Command: sk briefing --auto --compact\n\
             (fallback: python3 ~/.copilot/tools/briefing.py \"your task\")",
        ))
    }
}

// ---------------------------------------------------------------------------
// FileSizeAdvisoryRule (issue #341)
// ---------------------------------------------------------------------------

/// Emit an informational advisory when a Python file being edited or created
/// exceeds 400 lines.  Never denies — purely advisory (fail-open).
///
/// Mirrors `FileSizeAdvisoryRule` in `hooks/rules/file_size_advisory.py`.
pub struct FileSizeAdvisoryRule;

impl HookRule for FileSizeAdvisoryRule {
    fn name(&self) -> &'static str {
        "file-size-advisory"
    }

    fn events(&self) -> &'static [&'static str] {
        &["preToolUse"]
    }

    fn tools(&self) -> &'static [&'static str] {
        &["edit", "create"]
    }

    fn evaluate(&self, _event: &str, data: &Value) -> Option<Value> {
        let tool_name = data.get("toolName").and_then(|v| v.as_str())?;
        let tool_args = data
            .get("toolArgs")
            .and_then(|v| v.as_object())
            .cloned()
            .unwrap_or_default();

        // Only Python files are governed by the 400-line rule.
        let path_str = tool_args.get("path").and_then(|v| v.as_str()).unwrap_or("");
        if !path_str.ends_with(".py") {
            return None;
        }

        // Compute the proposed post-edit content, mirroring Python's
        // `_proposed_content()`.  For `edit`, apply the replacement and count
        // lines of the result (not existing + new_str).  For `create`, count
        // lines of `file_text`.  Fail-open on any I/O or missing-field error.
        let projected_lines: usize = if tool_name == "create" {
            let file_text = tool_args
                .get("file_text")
                .and_then(|v| v.as_str())
                .unwrap_or("");
            file_text.lines().count()
        } else if tool_name == "edit" {
            let original = match fs::read_to_string(path_str) {
                Ok(s) => s,
                Err(_) => return None, // fail-open: file absent or unreadable
            };
            let old_str = tool_args
                .get("old_str")
                .and_then(|v| v.as_str())
                .unwrap_or("");
            let new_str = tool_args
                .get("new_str")
                .and_then(|v| v.as_str())
                .unwrap_or("");
            // Mirror Python: count != 1 → pass (edit tool will fail itself).
            if original.matches(old_str).count() != 1 {
                return None;
            }
            original.replacen(old_str, new_str, 1).lines().count()
        } else {
            return None;
        };

        if projected_lines > 400 {
            return Some(info(
                &format!(
                    "\u{26a0}\u{fe0f} FILE SIZE: `{path_str}` will be ~{projected_lines} lines after this change (limit: 400). \
                     Consider decomposing or justify why keeping it together is safer. \
                     (Rule 10 — Minimum Footprint)"
                ),
            ));
        }

        None
    }
}

// ---------------------------------------------------------------------------
// NewFileAdvisoryRule (issue #341)
// ---------------------------------------------------------------------------

/// Emit an informational advisory when a new root-level Python file is about
/// to be created without an explicit justification.  Never denies — purely
/// advisory (fail-open).
///
/// Mirrors `NewFileAdvisoryRule` in `hooks/rules/new_file_advisory.py`.
pub struct NewFileAdvisoryRule;

impl HookRule for NewFileAdvisoryRule {
    fn name(&self) -> &'static str {
        "new-file-advisory"
    }

    fn events(&self) -> &'static [&'static str] {
        &["preToolUse"]
    }

    fn tools(&self) -> &'static [&'static str] {
        &["create"]
    }

    fn evaluate(&self, _event: &str, data: &Value) -> Option<Value> {
        let tool_args = data
            .get("toolArgs")
            .and_then(|v| v.as_object())
            .cloned()
            .unwrap_or_default();

        let path_str = tool_args.get("path").and_then(|v| v.as_str()).unwrap_or("");

        if !path_str.ends_with(".py") {
            return None;
        }

        // Consider a file "root-level" when the path contains no directory
        // separators, or only a single component that is the tools directory.
        let is_root = std::path::Path::new(path_str)
            .parent()
            .map(|p| p.as_os_str().is_empty() || p == std::path::Path::new("."))
            .unwrap_or(true);

        if !is_root {
            return None;
        }

        // File already exists → this is a truncating create, not a new file.
        if std::path::Path::new(path_str).exists() {
            return None;
        }

        Some(info(&format!(
            "\u{1f4c4} NEW FILE: `{path_str}` is a new root-level Python file. \
                 Before creating it, confirm: (1) no existing file can own this behavior, \
                 (2) its responsibility is stated in the issue/PR/handoff, \
                 (3) it is wired into lint/test/docs/CI. \
                 (Rule 11 — New File Justification)"
        )))
    }
}

// ---------------------------------------------------------------------------
// LoopDetectorRule
// ---------------------------------------------------------------------------

/// Detect and block repeated identical tool calls within a session.
///
/// Ports `hooks/rules/loop_detector.py::LoopDetectorRule` (issue #663, #868).
///
/// Detection strategy
/// ------------------
/// * Fires on `preToolUse` for **all** tools.
/// * Computes a SHA-256 signature of `{"tool": tool_name, "args": cleaned_args}`
///   with transient metadata keys stripped (same keys as the Python version).
/// * Tracks consecutive identical-signature calls via a per-session JSON state
///   file `~/.copilot/markers/loop-state-{session_id}.json`.
/// * Skips detection when `toolInput`/`toolArgs` is absent/empty (fail-open;
///   prevents false positives when the hook payload omits tool arguments).
///
/// Thresholds (configurable via env vars)
/// ---------------------------------------
/// * **Soft** (default 3, `LOOP_SOFT_THRESHOLD`): `info()` warning — non-blocking,
///   fires once per streak.
/// * **Hard** (default 5, `LOOP_HARD_THRESHOLD`): `deny()` — blocks the tool call.
///
/// Fail-open: any I/O or parse error returns `None` (never blocks).
pub struct LoopDetectorRule {
    pub soft_threshold: usize,
    pub hard_threshold: usize,
}

impl Default for LoopDetectorRule {
    fn default() -> Self {
        Self {
            soft_threshold: 3,
            hard_threshold: 5,
        }
    }
}

/// Metadata keys stripped before hashing — transient per-invocation fields.
/// Mirrors `_STRIP_KEYS` in `loop_detector.py`.
const STRIP_KEYS: &[&str] = &[
    "_session_id",
    "_timestamp",
    "_request_id",
    "_trace_id",
    "sessionId",
    "timestamp",
];

/// Recursively sort all JSON object keys for deterministic serialisation.
/// Matches Python's `json.dumps(sort_keys=True)` which sorts at every depth.
fn sort_value_recursive(v: &Value) -> Value {
    match v {
        Value::Object(map) => {
            let mut keys: Vec<&String> = map.keys().collect();
            keys.sort();
            let sorted: serde_json::Map<String, Value> = keys
                .into_iter()
                .map(|k| (k.clone(), sort_value_recursive(&map[k])))
                .collect();
            Value::Object(sorted)
        }
        Value::Array(arr) => Value::Array(arr.iter().map(sort_value_recursive).collect()),
        other => other.clone(),
    }
}

/// Compute SHA-256 hex digest of `{"tool": tool_name, "args": cleaned_args}`.
pub(crate) fn loop_compute_signature(
    tool_name: &str,
    tool_args: &serde_json::Map<String, Value>,
) -> String {
    use sha2::{Digest, Sha256};

    // Strip transient metadata keys.
    let cleaned: serde_json::Map<String, Value> = tool_args
        .iter()
        .filter(|(k, _)| !STRIP_KEYS.contains(&k.as_str()))
        .map(|(k, v)| (k.clone(), v.clone()))
        .collect();

    // Recursively sort keys (matches Python json.dumps(sort_keys=True)).
    let sorted = sort_value_recursive(&Value::Object(cleaned));

    let payload = serde_json::json!({"tool": tool_name, "args": sorted});
    let raw = payload.to_string();
    let hash = Sha256::digest(raw.as_bytes());
    format!("{hash:x}")
}

/// Read the integer threshold from an env var, falling back to `default`.
fn loop_threshold(env_var: &str, default: usize) -> usize {
    std::env::var(env_var)
        .ok()
        .and_then(|v| v.trim().parse::<usize>().ok())
        .unwrap_or(default)
}

/// Resolve the per-session loop state file path.
/// Reuses `session_state::{get_session_id, sanitize_session_id}` for
/// consistent session-ID detection and filename sanitisation.
pub(crate) fn loop_state_path(data: &Value) -> PathBuf {
    let session_id = crate::hooks::session_state::get_session_id(data);
    let sid = crate::hooks::session_state::sanitize_session_id(&session_id);
    markers_dir().join(format!("loop-state-{sid}.json"))
}

/// Per-session loop detector state stored in the JSON state file.
#[derive(serde::Serialize, serde::Deserialize, Default)]
struct LoopState {
    #[serde(default)]
    last_sig: String,
    #[serde(default)]
    streak: usize,
    #[serde(default)]
    soft_warned: bool,
}

/// Load loop state from the JSON file; return default on any error (fail-open).
fn load_loop_state(path: &Path) -> LoopState {
    let content = match fs::read_to_string(path) {
        Ok(c) => c,
        Err(_) => return LoopState::default(),
    };
    serde_json::from_str(&content).unwrap_or_default()
}

/// Save loop state to the JSON file; best-effort atomic write, never panics.
fn save_loop_state(path: &Path, state: &LoopState) {
    if let Some(parent) = path.parent() {
        let _ = fs::create_dir_all(parent);
    }
    let json = match serde_json::to_string(state) {
        Ok(j) => j,
        Err(_) => return,
    };
    // Atomic write: write to a PID-unique temp file, then rename.
    let tmp = path.with_extension(format!("tmp.{}", std::process::id()));
    if fs::write(&tmp, json.as_bytes()).is_ok() {
        let _ = fs::rename(&tmp, path);
    }
}

impl HookRule for LoopDetectorRule {
    fn name(&self) -> &'static str {
        "loop-detector"
    }

    fn events(&self) -> &'static [&'static str] {
        &["preToolUse"]
    }

    fn tools(&self) -> &'static [&'static str] {
        &[] // All tools
    }

    fn evaluate(&self, event: &str, data: &Value) -> Option<Value> {
        if event != "preToolUse" {
            return None;
        }
        // Fail-open: any error → None.
        self.run(data)
    }
}

impl LoopDetectorRule {
    fn run(&self, data: &Value) -> Option<Value> {
        let tool_name = data.get("toolName").and_then(|v| v.as_str()).unwrap_or("");

        // Accept toolInput (Copilot) or toolArgs (legacy).
        let tool_args_val = data.get("toolInput").or_else(|| data.get("toolArgs"));
        let tool_args = tool_args_val.and_then(|v| v.as_object())?;

        // Skip when args are empty — cannot distinguish calls.
        if tool_args.is_empty() {
            return None;
        }

        let soft = loop_threshold("LOOP_SOFT_THRESHOLD", self.soft_threshold);
        let hard = loop_threshold("LOOP_HARD_THRESHOLD", self.hard_threshold);

        let sig = loop_compute_signature(tool_name, tool_args);
        let state_path = loop_state_path(data);
        let mut state = load_loop_state(&state_path);

        if sig == state.last_sig {
            state.streak += 1;
        } else {
            state.last_sig = sig;
            state.streak = 1;
            state.soft_warned = false;
        }

        let streak = state.streak;
        let result = if streak >= hard {
            Some(deny(&format!(
                "Loop detected: tool '{tool_name}' called {streak} times \
                 with identical arguments (hard threshold {hard}). \
                 Try a different approach."
            )))
        } else if streak >= soft && !state.soft_warned {
            state.soft_warned = true;
            Some(info(&format!(
                "  Warning: tool '{tool_name}' called {streak} \
                 times with identical arguments. Consider varying your \
                 approach before the hard limit ({hard})."
            )))
        } else {
            None
        };

        save_loop_state(&state_path, &state);
        result
    }
}
