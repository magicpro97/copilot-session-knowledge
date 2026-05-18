use super::*;

// ---------------------------------------------------------------------------
// SessionStartRule
// ---------------------------------------------------------------------------

/// Lifecycle acknowledgement rule for sessionStart.
///
/// Emits the initial "[sk] Session started — hooks active." message.
/// `AutoBriefingRule` and `IntegrityRule` follow in `all_rules()` and handle
/// the substantive session-start work (briefing subprocess + integrity manifest).
pub struct SessionStartRule;

impl HookRule for SessionStartRule {
    fn name(&self) -> &'static str {
        "session-start"
    }

    fn events(&self) -> &'static [&'static str] {
        &["sessionStart"]
    }

    fn tools(&self) -> &'static [&'static str] {
        &[] // event-level, no tool filter
    }

    fn evaluate(&self, _event: &str, _data: &Value) -> Option<Value> {
        Some(info("[sk] Session started — hooks active."))
    }
}

// ---------------------------------------------------------------------------
// MEMORY.md injection helpers (issue #161)
// ---------------------------------------------------------------------------

/// Load `~/.copilot/hooks-config.json`; returns a null `Value` on any error.
///
/// Mirrors `hooks/rules/briefing.py::_load_hooks_config()`.
pub(crate) fn load_hooks_config() -> Value {
    let home = resolve_home_dir().unwrap_or_else(|| PathBuf::from("."));
    let path = home.join(".copilot").join("hooks-config.json");
    if path.is_file() {
        if let Ok(text) = fs::read_to_string(&path) {
            if let Ok(val) = serde_json::from_str::<Value>(&text) {
                return val;
            }
        }
    }
    Value::Null
}

/// Load `MEMORY.md` from `cwd` (defaults to the process working directory)
/// for injection into the `sessionStart` auto-briefing.
///
/// Returns the (possibly truncated) file content when:
///   - `memory_inject_enabled` is not explicitly `false` in hooks-config,
///   - `MEMORY.md` exists in `cwd`,
///   - the file is not older than `memory_inject_max_age_days` (default 1 day),
///   - the effective content is non-empty.
///
/// Returns `None` for a graceful no-op in all other cases.
///
/// Config keys (`~/.copilot/hooks-config.json`):
///   `memory_inject_enabled`      — bool, default `true`
///   `memory_inject_max_tokens`   — int, default `500` (1 token ≈ 4 chars)
///   `memory_inject_max_age_days` — number, default `1`
///
/// Mirrors `hooks/rules/briefing.py::_load_memory_md()`.
pub(crate) fn load_memory_md(cwd: Option<&Path>) -> Option<String> {
    let cfg = load_hooks_config();

    // memory_inject_enabled: default true; skip only when explicitly false.
    if cfg
        .get("memory_inject_enabled")
        .and_then(|v| v.as_bool())
        .map(|b| !b)
        .unwrap_or(false)
    {
        return None;
    }

    // Max age in seconds; default 1 day (86 400 s).
    let max_age_secs: u64 = cfg
        .get("memory_inject_max_age_days")
        .and_then(|v| v.as_f64())
        .map(|days| (days * 86_400.0) as u64)
        .unwrap_or(86_400);

    // Token budget (1 token ≈ 4 chars); default 500 tokens.
    let token_budget: u64 = cfg
        .get("memory_inject_max_tokens")
        .and_then(|v| v.as_u64())
        .unwrap_or(500);

    let base = cwd
        .map(|p| p.to_path_buf())
        .unwrap_or_else(|| std::env::current_dir().unwrap_or_else(|_| PathBuf::from(".")));
    let memory_path = base.join("MEMORY.md");

    if !memory_path.is_file() {
        return None;
    }

    // Age guard: skip if the file is older than max_age_secs.
    // `duration_since` returns Err when mtime is in the future (clock skew);
    // treat that as age = 0 (fresh), matching the Python hook behaviour where
    //   age_secs = time.time() - mtime  →  negative  →  not > max_age_secs.
    let age_ok = memory_path
        .metadata()
        .ok()
        .and_then(|m| m.modified().ok())
        .map(|mtime| {
            SystemTime::now()
                .duration_since(mtime)
                .unwrap_or(Duration::ZERO)
                .as_secs()
                <= max_age_secs
        })
        .unwrap_or(false);
    if !age_ok {
        return None;
    }

    let content = fs::read_to_string(&memory_path).ok()?;
    let mut trimmed = content.trim().to_string();
    if trimmed.is_empty() {
        return None;
    }

    // Approximate token budget: 1 token ≈ 4 Unicode characters (matching
    // Python's len() semantics which counts Unicode code points, not bytes).
    let char_limit = ((token_budget * 4) as usize).max(1);
    if trimmed.chars().count() > char_limit {
        // Find the byte offset of the char_limit-th Unicode scalar so that
        // String::truncate lands on a valid char boundary.  This preserves
        // UTF-8 safety while counting characters rather than bytes, matching
        // Python's character-count semantics for non-ASCII content (issue #161).
        let byte_offset = trimmed
            .char_indices()
            .nth(char_limit)
            .map(|(i, _)| i)
            .unwrap_or(trimmed.len());
        trimmed.truncate(byte_offset);
        trimmed = trimmed.trim_end().to_string();
        trimmed.push_str("\n\u{2026} (truncated to token budget)");
    }

    Some(trimmed)
}

// ---------------------------------------------------------------------------
// Goal resume breadcrumb helpers (issue #185)
// ---------------------------------------------------------------------------

pub(crate) const BREADCRUMB_FILENAME: &str = "goal-resume-breadcrumb.json";

/// Map a raw ``pause_reason`` string to a short human-readable label.
///
/// The prefix before `:` is extracted and matched so that "session_end:normal"
/// yields "session end".  Unknown prefixes fall back to "paused".
///
/// Future-compatible: "compaction" and "quota" prefixes are recognised even
/// though those pause paths are not yet implemented (issues #182 / #187).
pub(crate) fn format_pause_reason(raw: &str) -> &'static str {
    let prefix = raw.split(':').next().unwrap_or("").trim();
    match prefix {
        "session_end" => "session end",
        "compaction" => "context compaction",
        "quota" => "quota limit",
        _ => "paused",
    }
}

/// Read `.octogent/goal-resume-breadcrumb.json` relative to `project_root`
/// and return a short banner if the goal is still paused.
///
/// Returns `None` (suppresses the banner) when:
///   - the breadcrumb file is absent,
///   - the goal is already resumed / in a terminal state, or
///   - breadcrumb read / parse / type errors occur (treated as absent).
///
/// Shows the banner (fail-open) when `goal.json` cannot be read or parsed —
/// the staleness check is skipped so the operator still sees the resume hint.
///
/// Mirrors `hooks/rules/briefing.py::_load_goal_resume_hint()`.
pub(crate) fn load_goal_resume_hint(project_root: Option<&Path>) -> Option<Vec<String>> {
    let root = match project_root {
        Some(p) => p.to_path_buf(),
        None => std::env::current_dir().ok()?,
    };

    let bc_path = root.join(".octogent").join(BREADCRUMB_FILENAME);
    if !bc_path.is_file() {
        return None;
    }

    let bc_text = fs::read_to_string(&bc_path).ok()?;
    let bc: Value = serde_json::from_str(&bc_text).ok()?;

    // Guard: valid but non-object JSON (e.g. [], 42, "x") must not produce a
    // spurious banner.  Mirrors Python's AttributeError path where bc.get()
    // raises on a non-dict and the outer except swallows it.
    if !bc.is_object() {
        return None;
    }

    // Trim each field independently so a whitespace-only goal_title falls
    // back to goal_id before the final "(untitled goal)" sentinel, mirroring
    // hooks/rules/briefing.py::_load_goal_resume_hint().
    let goal_title = {
        let trimmed_title = bc
            .get("goal_title")
            .and_then(|v| v.as_str())
            .map(|s| s.trim())
            .filter(|s| !s.is_empty());
        let trimmed_id = bc
            .get("goal_id")
            .and_then(|v| v.as_str())
            .map(|s| s.trim())
            .filter(|s| !s.is_empty());
        trimmed_title.or(trimmed_id).unwrap_or("(untitled goal)")
    };

    let resume_cmd = bc
        .get("resume_command")
        .and_then(|v| v.as_str())
        .map(str::trim)
        .filter(|s| !s.is_empty())
        .unwrap_or("sk tentacle goal resume");

    // Staleness check: if goal.json status is no longer "paused", suppress.
    let goal_json_path = bc
        .get("goal_path")
        .and_then(|v| v.as_str())
        .filter(|s| !s.is_empty())
        .map(PathBuf::from)
        .unwrap_or_else(|| root.join(".octogent").join("goal.json"));

    if goal_json_path.is_file() {
        if let Ok(goal_text) = fs::read_to_string(&goal_json_path) {
            if let Ok(goal_state) = serde_json::from_str::<Value>(&goal_text) {
                // Only suppress when goal.json parses as a JSON *object* and
                // its "status" is not "paused".  Non-object JSON ([], 42, "x")
                // must not trigger suppression — fall through and show the banner,
                // matching Python's AttributeError fail-open path where
                // state.get("status") raises on a non-dict and the except swallows it.
                if goal_state.is_object()
                    && goal_state
                        .get("status")
                        .and_then(|v| v.as_str())
                        .unwrap_or("")
                        != "paused"
                {
                    return None; // goal resumed or in terminal state — suppress
                }
            }
        }
        // can't read goal.json → fail-open (show banner)
    }

    let pause_reason = bc
        .get("pause_reason")
        .and_then(|v| v.as_str())
        .unwrap_or("");
    let reason_label = format_pause_reason(pause_reason);
    let sep = format!("  {}", "\u{2500}".repeat(33));

    let mut lines = vec![
        format!("\n  \u{23f8}  Paused goal: {goal_title}  ({reason_label})"),
        format!("  \u{25b6}  Run: {resume_cmd}"),
    ];

    // Optional one-line budget detail from issue #182 structured snapshot.
    // Backward-compatible: old breadcrumbs without budget_snapshot skip this.
    if let Some(snap) = bc.get("budget_snapshot").and_then(|v| v.as_object()) {
        let ci = snap
            .get("current_iteration")
            .and_then(|v| v.as_i64())
            .map(|n| n.to_string())
            .unwrap_or_else(|| "?".to_string());
        let iter_str = if let Some(mi) = snap.get("max_iterations").and_then(|v| v.as_i64()) {
            format!("{}/{}", ci, mi)
        } else {
            ci
        };
        let tc = snap
            .get("tentacle_count")
            .and_then(|v| v.as_i64())
            .map(|n| n.to_string())
            .unwrap_or_else(|| "?".to_string());
        let tent_str = if let Some(mt) = snap.get("max_tentacles").and_then(|v| v.as_i64()) {
            format!("{}/{}", tc, mt)
        } else {
            tc
        };
        lines.push(format!(
            "  \u{2139}  Budget: iter {}, tentacles {}",
            iter_str, tent_str
        ));
    }

    lines.push(sep);
    Some(lines)
}

// ---------------------------------------------------------------------------
// AutoBriefingRule
// ---------------------------------------------------------------------------

/// Run `briefing.py` at session start, prepend `MEMORY.md`, and sign HMAC
/// markers (wave9, extended in wave10 with issue #161 MEMORY.md injection,
/// wave16 with issue #185 paused-goal resume banner).
///
/// Ports `hooks/rules/briefing.py::AutoBriefingRule`.
///
/// What this rule does:
///   0. If `.octogent/goal-resume-breadcrumb.json` is present and the goal
///      is still paused, emits a concise resume-hint banner BEFORE all other
///      output (issue #185).
///   1. Reads `COPILOT_AGENT_SESSION_ID` to identify the current session.
///   2. Cleans up stale session-specific markers (own session: deleted and
///      re-signed below; orphaned `briefing-done*` markers older than 2h:
///      deleted).
///   3. Prepends `MEMORY.md` content when present, fresh, and injection is
///      not explicitly disabled via `hooks-config.json` (issue #161).
///   4. Spawns `briefing.py <project> --budget 2000` as a subprocess and
///      captures its stdout to follow the MEMORY.md section.
///   5. Signs `briefing-done` and `briefing-done-{session_id}` HMAC markers
///      via [`marker_auth::sign_marker`].
///   6. Returns an informational message.
///
/// Fail-open at every step:
///   - `briefing.py` absent → `None` (no briefing, no markers).
///   - Python unavailable → emits an informational notice, still signs markers.
///   - Hung subprocess → killed after 10s with a timeout notice (mirrors Python).
///   - Marker signing error → silently skipped.
///   - Filesystem errors during cleanup → silently swallowed.
///   - MEMORY.md absent, stale, or disabled → silently skipped (no-op).
///   - Breadcrumb absent, stale, or unreadable → silently skipped (no-op).
///
/// Informational only; never produces `permissionDecision`.
pub struct AutoBriefingRule;

impl HookRule for AutoBriefingRule {
    fn name(&self) -> &'static str {
        "auto-briefing"
    }

    fn events(&self) -> &'static [&'static str] {
        &["sessionStart"]
    }

    fn tools(&self) -> &'static [&'static str] {
        &[] // event-level, no tool filter
    }

    fn evaluate(&self, _event: &str, _data: &Value) -> Option<Value> {
        use crate::config::{python_exe, resolve_tools_dir};

        // Session ID: env var from the Copilot platform, or empty (fail-open).
        let session_id = std::env::var("COPILOT_AGENT_SESSION_ID").unwrap_or_default();

        let mdir = markers_dir();

        // --- Clean up stale markers (fail-open) ---
        if mdir.is_dir() {
            let stale_cutoff = SystemTime::now()
                .duration_since(UNIX_EPOCH)
                .map(|d| d.as_secs())
                .unwrap_or(0)
                .saturating_sub(7200); // 2 hours

            let _ = fs::read_dir(&mdir).map(|entries| {
                for entry in entries.flatten() {
                    let fname = entry.file_name();
                    let name = fname.to_string_lossy();
                    // Preserve permanent system markers and sync markers (issue #347).
                    if SESSION_PROTECTED_MARKERS
                        .iter()
                        .any(|p| *p == name.as_ref())
                    {
                        continue;
                    }
                    // Delete own session-specific markers (will re-sign below).
                    if !session_id.is_empty() && name.ends_with(&format!("-{session_id}")) {
                        let _ = fs::remove_file(entry.path());
                        continue;
                    }
                    // Delete stale briefing-done markers older than 2h.
                    if name.starts_with("briefing-done") {
                        if let Ok(meta) = entry.metadata() {
                            if let Ok(mtime) = meta.modified() {
                                if let Ok(elapsed) = mtime.duration_since(UNIX_EPOCH) {
                                    if elapsed.as_secs() < stale_cutoff {
                                        let _ = fs::remove_file(entry.path());
                                    }
                                }
                            }
                        }
                    }
                }
            });
        }

        let tools_dir = resolve_tools_dir();
        let briefing_script = tools_dir.join("briefing.py");
        if !briefing_script.is_file() {
            return None; // fail-open: briefing.py absent
        }

        // --- Determine project root and name (mirrors Python _get_project()) ---
        let git_root_opt: Option<PathBuf> = Command::new("git")
            .args(["rev-parse", "--show-toplevel"])
            .stdout(Stdio::piped())
            .stderr(Stdio::null())
            .output()
            .ok()
            .and_then(|o| {
                if o.status.success() {
                    String::from_utf8(o.stdout)
                        .ok()
                        .map(|s| PathBuf::from(s.trim()))
                } else {
                    None
                }
            });

        let project = git_root_opt
            .as_ref()
            .and_then(|root| {
                root.file_name()
                    .and_then(|n| n.to_str())
                    .map(|n| n.to_string())
            })
            .filter(|s| !s.is_empty())
            .unwrap_or_else(|| {
                std::env::current_dir()
                    .ok()
                    .and_then(|p| {
                        p.file_name()
                            .and_then(|n| n.to_str())
                            .map(|s| s.to_string())
                    })
                    .unwrap_or_default()
            });

        let mut lines: Vec<String> = Vec::new();

        // --- Goal resume banner (issue #185): prepend BEFORE briefing header ---
        if let Some(hint) = load_goal_resume_hint(git_root_opt.as_deref()) {
            lines.extend(hint);
        }

        lines.push(format!("\n  \u{1f4cb} Session briefing for: {project}"));
        lines.push("  \u{2500}\u{2500}\u{2500}\u{2500}\u{2500}\u{2500}\u{2500}\u{2500}\u{2500}\u{2500}\u{2500}\u{2500}\u{2500}\u{2500}\u{2500}\u{2500}\u{2500}\u{2500}\u{2500}\u{2500}\u{2500}\u{2500}\u{2500}\u{2500}\u{2500}\u{2500}\u{2500}\u{2500}\u{2500}\u{2500}\u{2500}\u{2500}\u{2500}\u{2500}\u{2500}".to_string());

        // --- MEMORY.md injection (issue #161): prepend promoted knowledge ---
        if let Some(mem) = load_memory_md(None) {
            lines.push("\n  \u{1f4cc} MEMORY.md (promoted knowledge):".to_string());
            for mem_line in mem.lines() {
                lines.push(format!("  {mem_line}"));
            }
            lines.push("  \u{2500}\u{2500}\u{2500}\u{2500}\u{2500}\u{2500}\u{2500}\u{2500}\u{2500}\u{2500}\u{2500}\u{2500}\u{2500}\u{2500}\u{2500}\u{2500}\u{2500}\u{2500}\u{2500}\u{2500}\u{2500}\u{2500}\u{2500}\u{2500}\u{2500}\u{2500}\u{2500}\u{2500}\u{2500}\u{2500}\u{2500}\u{2500}\u{2500}\u{2500}\u{2500}".to_string());
        }

        // --- Spawn briefing.py (capture stdout so it follows MEMORY.md in the message) ---
        // Drain stdout in a dedicated thread to prevent pipe-buffer deadlock.
        // If briefing.py writes more bytes than the OS pipe buffer (~64 KB on
        // Linux, 4–64 KB on Windows) the child blocks mid-write and never
        // exits; the parent's try_wait() loop sees None forever and eventually
        // kills what appeared to be a 10-second hang — even though the child
        // had real output ready.  Moving the read into a separate thread lets
        // the OS buffer stay empty while we poll for exit.
        let python = python_exe();
        match Command::new(python)
            .arg(&briefing_script)
            .arg(&project)
            .args(["--budget", "2000", "--session-start"])
            // Recursion guard (issue #396): prevent briefing.py from
            // triggering the hook again when it spawns further tool calls.
            .env("SK_HOOK_ACTIVE", "1")
            .stdout(Stdio::piped())
            .stderr(Stdio::null())
            .spawn()
        {
            Ok(mut child) => {
                // Take the stdout handle *before* any wait/poll call so the
                // reader thread can drain the pipe concurrently.
                let reader_thread = child.stdout.take().map(|mut stdout| {
                    std::thread::spawn(move || -> Vec<u8> {
                        let mut buf = Vec::new();
                        let _ = stdout.read_to_end(&mut buf);
                        buf
                    })
                });
                let deadline = Instant::now() + Duration::from_secs(10);
                let mut timed_out = false;
                loop {
                    match child.try_wait() {
                        Ok(Some(_status)) => break,
                        Ok(None) => {
                            if Instant::now() >= deadline {
                                let _ = child.kill();
                                let _ = child.wait();
                                timed_out = true;
                                break;
                            }
                            std::thread::sleep(Duration::from_millis(50));
                        }
                        Err(_) => break,
                    }
                }
                if timed_out {
                    // Reap the reader thread (pipe is closed after kill+wait),
                    // then preserve any partial output already collected before
                    // the timeout — e.g. the Level 0 skill index emitted by
                    // --session-start mode before the knowledge DB work begins.
                    if let Some(handle) = reader_thread {
                        if let Ok(bytes) = handle.join() {
                            let partial = String::from_utf8_lossy(&bytes);
                            let partial_out = partial.trim_end().to_string();
                            if !partial_out.is_empty() {
                                lines.push(partial_out);
                            }
                        }
                    }
                    lines.push("  \u{23f1} Briefing timed out (10s)".to_string());
                } else if let Some(handle) = reader_thread {
                    if let Ok(bytes) = handle.join() {
                        let output = String::from_utf8_lossy(&bytes);
                        let briefing_out = output.trim_end().to_string();
                        if !briefing_out.is_empty() {
                            lines.push(briefing_out);
                        }
                    }
                }
            }
            Err(_) => {
                lines.push("  \u{23f1} Briefing unavailable (Python not found)".to_string());
            }
        }

        // --- Sign markers (fail-open) ---
        let _ = fs::create_dir_all(&mdir);
        let global_marker = mdir.join("briefing-done");
        let _ = marker_auth::sign_marker(&global_marker, "briefing-done");
        if !session_id.is_empty() {
            let name = format!("briefing-done-{session_id}");
            let session_marker = mdir.join(&name);
            let _ = marker_auth::sign_marker(&session_marker, &name);
        }

        Some(info(&lines.join("\n")))
    }
}

// ---------------------------------------------------------------------------
// IntegrityRule
// ---------------------------------------------------------------------------

/// Verify hook file integrity at session start (wave9).
///
/// Ports `hooks/rules/integrity.py::IntegrityRule`.
///
/// What this rule does:
///   1. Checks `~/.copilot/config.json` for `disableAllHooks` (config
///      poisoning).  If found, creates a tamper marker and returns an
///      informational warning.
///   2. Reads the SHA256 integrity manifest at
///      `~/.copilot/hooks/integrity-manifest.json`.
///      If absent, generates it and returns a "first run" message.
///   3. Compares the current SHA256 of each tracked file against the manifest.
///      If any file changed or is missing, regenerates the manifest and returns
///      a message listing what changed.
///   4. If all files match, returns a "verified" info message.
///
/// Fail-open at every step:
///   - Any filesystem / JSON / hash error → `None`.
///   - Informational only; never produces `permissionDecision` / deny.
///   - Config-poisoning path creates a tamper marker but still returns info.
pub struct IntegrityRule;

/// Compute the SHA256 hex digest of a file.  Returns `None` on any error.
pub(crate) fn sha256_file(path: &std::path::Path) -> Option<String> {
    use sha2::{Digest, Sha256};

    let mut hasher = Sha256::new();
    let mut f = std::fs::File::open(path).ok()?;
    let mut buf = [0u8; 8192];
    loop {
        let n = f.read(&mut buf).ok()?;
        if n == 0 {
            break;
        }
        hasher.update(&buf[..n]);
    }
    Some(format!("{:x}", hasher.finalize()))
}

/// `~/.copilot/tools/hooks` — the hooks source directory.
pub(crate) fn hooks_src_dir() -> PathBuf {
    resolve_home_dir()
        .unwrap_or_else(|| PathBuf::from("."))
        .join(".copilot")
        .join("tools")
        .join("hooks")
}

/// `~/.copilot/hooks` — the hooks installation directory (holds hooks.json etc).
pub(crate) fn hooks_dst_dir() -> PathBuf {
    resolve_home_dir()
        .unwrap_or_else(|| PathBuf::from("."))
        .join(".copilot")
        .join("hooks")
}

/// Path of the integrity manifest JSON file.
pub(crate) fn integrity_manifest_path() -> PathBuf {
    hooks_dst_dir().join("integrity-manifest.json")
}

/// Regenerate the integrity manifest from the current hook files.
///
/// Mirrors `IntegrityRule._regenerate_manifest()`.
pub(crate) fn regenerate_manifest() {
    let hooks_dir = hooks_src_dir();
    let dst_dir = hooks_dst_dir();
    let mut files = serde_json::Map::new();

    // Top-level .py files in hooks/
    if hooks_dir.is_dir() {
        let mut paths: Vec<_> = fs::read_dir(&hooks_dir)
            .into_iter()
            .flatten()
            .flatten()
            .filter(|e| {
                e.path()
                    .extension()
                    .is_some_and(|x| x.eq_ignore_ascii_case("py"))
            })
            .collect();
        paths.sort_by_key(|e| e.file_name());
        for entry in &paths {
            let name = entry.file_name().to_string_lossy().to_string();
            if let Some(h) = sha256_file(&entry.path()) {
                files.insert(name, serde_json::Value::String(h));
            }
        }

        // rules/ subdirectory
        let rules_dir = hooks_dir.join("rules");
        if rules_dir.is_dir() {
            let mut rpaths: Vec<_> = fs::read_dir(&rules_dir)
                .into_iter()
                .flatten()
                .flatten()
                .filter(|e| {
                    e.path()
                        .extension()
                        .is_some_and(|x| x.eq_ignore_ascii_case("py"))
                })
                .collect();
            rpaths.sort_by_key(|e| e.file_name());
            for entry in &rpaths {
                let key = format!("rules/{}", entry.file_name().to_string_lossy());
                if let Some(h) = sha256_file(&entry.path()) {
                    files.insert(key, serde_json::Value::String(h));
                }
            }
        }
    }

    // hooks.json hash
    let hooks_json_path = dst_dir.join("hooks.json");
    let hooks_json_hash = sha256_file(&hooks_json_path)
        .map(serde_json::Value::String)
        .unwrap_or(serde_json::Value::Null);

    let manifest = serde_json::json!({
        "files": files,
        "hooks_json": hooks_json_hash,
    });

    let manifest_path = integrity_manifest_path();
    if let Some(parent) = manifest_path.parent() {
        let _ = fs::create_dir_all(parent);
    }
    if let Ok(text) = serde_json::to_string_pretty(&manifest) {
        let _ = fs::write(&manifest_path, text);
    }
}

impl HookRule for IntegrityRule {
    fn name(&self) -> &'static str {
        "integrity"
    }

    fn events(&self) -> &'static [&'static str] {
        &["sessionStart"]
    }

    fn tools(&self) -> &'static [&'static str] {
        &[] // event-level, no tool filter
    }

    fn evaluate(&self, _event: &str, _data: &Value) -> Option<Value> {
        // --- Config poisoning check ---
        let config_path = resolve_home_dir()
            .unwrap_or_else(|| PathBuf::from("."))
            .join(".copilot")
            .join("config.json");
        if config_path.is_file() {
            if let Ok(content) = fs::read_to_string(&config_path) {
                if let Ok(cfg) = serde_json::from_str::<serde_json::Value>(&content) {
                    if cfg
                        .get("disableAllHooks")
                        .and_then(|v| v.as_bool())
                        .unwrap_or(false)
                    {
                        marker_auth::create_tamper_marker();
                        return Some(info(concat!(
                            "\n  \u{1f6a8} CONFIG POISONED: disableAllHooks detected in config.json!\n",
                            "  This disables ALL hook enforcement.\n",
                            "  Run: sudo python3 ~/.copilot/tools/install.py --lock-hooks\n",
                        )));
                    }
                }
            }
        }

        // --- Manifest check / generation ---
        let manifest_path = integrity_manifest_path();
        if !manifest_path.is_file() {
            // First run or after reset: generate manifest.
            regenerate_manifest();
            return Some(info(
                "  \u{1f512} Hook integrity manifest generated (first run)",
            ));
        }

        let manifest: serde_json::Value = fs::read_to_string(&manifest_path)
            .ok()
            .and_then(|s| serde_json::from_str(&s).ok())?;

        let hooks_dir = hooks_src_dir();
        let dst_dir = hooks_dst_dir();
        let mut changed: Vec<String> = Vec::new();
        let mut missing: Vec<String> = Vec::new();

        if let Some(files) = manifest.get("files").and_then(|v| v.as_object()) {
            for (filename, expected) in files {
                let expected_hash = expected.as_str().unwrap_or("");
                // Both top-level and rules/ files are joined from hooks_dir.
                let filepath = hooks_dir.join(filename.as_str());
                if !filepath.is_file() {
                    missing.push(filename.clone());
                    continue;
                }
                match sha256_file(&filepath) {
                    Some(actual) if actual == expected_hash => {} // matches
                    Some(_) => changed.push(filename.clone()),
                    None => {} // hash error — fail-open
                }
            }
        }

        // Check hooks.json hash.
        if let Some(expected_v) = manifest.get("hooks_json") {
            if let Some(expected_hash) = expected_v.as_str() {
                let hooks_json = dst_dir.join("hooks.json");
                if hooks_json.is_file() {
                    match sha256_file(&hooks_json) {
                        Some(actual) if actual != expected_hash => {
                            changed.push("hooks.json".to_string());
                        }
                        _ => {}
                    }
                } else {
                    changed.push("hooks.json (MISSING)".to_string());
                }
            }
        }

        // Tamper-marker path for clearing stale false positives.
        let tamper_path = markers_dir().join("hooks-tampered");

        if !changed.is_empty() || !missing.is_empty() {
            // Auto-update manifest (legitimate updates / git pull / agent fixes).
            regenerate_manifest();
            if tamper_path.is_file() {
                let _ = fs::remove_file(&tamper_path);
            }
            let mut lines =
                vec!["  \u{1f504} Hook files updated \u{2014} manifest refreshed".to_string()];
            if !changed.is_empty() {
                lines.push(format!("  Changed: {}", changed.join(", ")));
            }
            if !missing.is_empty() {
                lines.push(format!("  Removed: {}", missing.join(", ")));
            }
            return Some(info(&lines.join("\n")));
        }

        // All files verified.
        if tamper_path.is_file() {
            let _ = fs::remove_file(&tamper_path);
        }
        let count = manifest
            .get("files")
            .and_then(|v| v.as_object())
            .map_or(0, |m| m.len());
        Some(info(&format!(
            "  \u{1f512} Hook integrity verified ({count} files + hooks.json)"
        )))
    }
}

// ---------------------------------------------------------------------------
// SessionEndRule
// ---------------------------------------------------------------------------

/// Per-session marker cleanup + session.log entry on sessionEnd.
///
/// Ports `hooks/rules/session_lifecycle.py::SessionEndRule`.
///
/// What this rule does:
///   1. Reads `COPILOT_AGENT_SESSION_ID` from the environment (set by the
///      Copilot platform) to identify the current session.
///   2. Deletes any marker files whose name ends with `-{session_id}`,
///      preserving permanent system files (`audit.jsonl`, `session.log`,
///      `hooks-tampered`).
///   3. Appends a one-line entry to `~/.copilot/markers/session.log`.
///   4. Returns an informational message.
///
/// Fail-open at every step:
///   - If `COPILOT_AGENT_SESSION_ID` is not set, marker cleanup is skipped
///     (no-op — the current process PID is not the same as the agent session
///     ID, so we avoid accidentally deleting the wrong markers).
///   - Any filesystem error is silently swallowed.
///
/// Remaining Python-only work on sessionEnd (NOT ported here):
///   - `recurrence-detector`: requires live DB search via `query-session.py`
///     and HMAC-signed knowledge-health counters.
///   - Checkpoint reminder: reads `COPILOT_CHECKPOINT_REMIND` env var and
///     emits a reminder — low value to port alone.
///   - Goal pause + resume breadcrumb (issue #184): reads `goal.json` via
///     `_goal_transact`, transitions `active`/`awaiting-gate` goals to
///     `paused`, and writes `.octogent/goal-resume-breadcrumb.json`.
///     Porting this to Rust would require reimplementing the full tentacle.py
///     goal-lock discipline and file-format contract, which creates a
///     dual-writer drift risk.  The behavior is therefore intentionally kept
///     in the Python layer (`hooks/rules/session_lifecycle.py::SessionEndRule`
///     and `hooks/session-end.py`).
///
///     **Routing boundary for default Rust-binary installs:** `sessionEnd` is
///     in `NATIVE_EVENTS`, so the native Rust rule runs for managed events.
///     The Python `session_lifecycle.py::SessionEndRule` runs separately only
///     when the Python `sk.py` shim calls `hook_runner.py`.  Operators using
///     a pure native binary (no Python shim) do not get goal-pause
///     automatically; they can run `python ~/.copilot/tools/tentacle.py goal
///     resume` manually after a session restart to reactivate a paused goal.
pub struct SessionEndRule;

/// The set of marker filenames that are permanent and must never be deleted
/// during per-session cleanup.
///
/// Sync marker files (`sync-nudge.json`, `sync-flush.json`) are written by
/// `postToolUse` / `sessionEnd` hooks and consumed by watch-sessions / sync-daemon.
/// They must survive session cleanup so consumers can read them (issue #347).
pub(crate) const SESSION_PROTECTED_MARKERS: &[&str] = &[
    "audit.jsonl",
    "session.log",
    "hooks-tampered",
    "sync-nudge.json",
    "sync-flush.json",
];

impl HookRule for SessionEndRule {
    fn name(&self) -> &'static str {
        "session-end"
    }

    fn events(&self) -> &'static [&'static str] {
        &["sessionEnd"]
    }

    fn tools(&self) -> &'static [&'static str] {
        &[] // event-level, no tool filter
    }

    fn evaluate(&self, _event: &str, data: &Value) -> Option<Value> {
        let reason = data
            .get("reason")
            .and_then(|v| v.as_str())
            .unwrap_or("unknown");

        // Session ID from the Copilot platform env var.
        // If absent we skip cleanup (fail-open — avoid deleting wrong markers).
        let session_id = std::env::var("COPILOT_AGENT_SESSION_ID").ok();

        let mdir = markers_dir();
        let mut cleaned: usize = 0;

        if let Some(ref sid) = session_id {
            if mdir.is_dir() {
                if let Ok(entries) = fs::read_dir(&mdir) {
                    let suffix = format!("-{sid}");
                    for entry in entries.flatten() {
                        let name = entry.file_name();
                        let name_str = name.to_string_lossy();
                        if SESSION_PROTECTED_MARKERS
                            .iter()
                            .any(|p| *p == name_str.as_ref())
                        {
                            continue; // preserve system files
                        }
                        if name_str.ends_with(suffix.as_str()) {
                            let _ = fs::remove_file(entry.path());
                            cleaned += 1;
                        }
                    }
                }
            }
        }

        // Append to session.log (best-effort).
        let _ = (|| -> std::io::Result<()> {
            fs::create_dir_all(&mdir)?;
            let log_path = mdir.join("session.log");
            let mut fh = fs::OpenOptions::new()
                .append(true)
                .create(true)
                .open(&log_path)?;
            let sid_display = session_id.as_deref().unwrap_or("unknown");
            let sid_short = &sid_display[..sid_display.len().min(8)];
            writeln!(fh, "Session ended ({sid_short}): {reason}")?;
            Ok(())
        })();

        let msg = if cleaned > 0 {
            format!("[sk] Session ended — {cleaned} marker(s) cleaned up.")
        } else {
            "[sk] Session ended — sync signal written.".to_string()
        };

        Some(info(&msg))
    }
}

// ---------------------------------------------------------------------------
// RecurrenceDetectorRule
// ---------------------------------------------------------------------------

/// Detect briefed mistakes that recurred in this session (wave9).
///
/// Ports `hooks/rules/recurrence_detector.py::RecurrenceDetectorRule`.
///
/// What this rule does:
///   1. Reads `COPILOT_SESSION_ID` (or derives from `COPILOT_SESSION_STATE`)
///      to identify the current session.
///   2. Opens `knowledge.db` in read-write mode (no-op if absent).
///   3. Checks that `briefing_deliveries` and `knowledge_entries` tables exist.
///   4. Queries for mistakes that were briefed to this session (via
///      `briefing_deliveries`) AND for which a new mistake with similar topic
///      was added in the same session after the delivery timestamp.
///   5. Increments `recurrence_after_briefing` on each recurred entry.
///   6. Returns `None` — informational DB side-effect only; no hook output.
///
/// Fail-open at every step:
///   - Session ID absent → `None`.
///   - DB absent or locked → `None`.
///   - Tables absent (first-run, not yet migrated) → `None`.
///   - Any SQL error → silently swallowed per row.
pub struct RecurrenceDetectorRule;

impl HookRule for RecurrenceDetectorRule {
    fn name(&self) -> &'static str {
        "recurrence-detector"
    }

    fn events(&self) -> &'static [&'static str] {
        &["sessionEnd"]
    }

    fn tools(&self) -> &'static [&'static str] {
        &[] // event-level, no tool filter
    }

    fn evaluate(&self, event: &str, _data: &Value) -> Option<Value> {
        if event != "sessionEnd" {
            return None;
        }

        // --- Resolve session ID ---
        let session_id = std::env::var("COPILOT_SESSION_ID")
            .ok()
            .filter(|s| !s.is_empty())
            .or_else(|| {
                std::env::var("COPILOT_SESSION_STATE")
                    .ok()
                    .and_then(|p| {
                        PathBuf::from(&p)
                            .file_name()
                            .and_then(|n| n.to_str())
                            .map(|n| n.to_string())
                    })
                    .filter(|s| !s.is_empty())
            });

        let session_id = session_id?;

        // --- Open DB read-write (fail-open if absent or locked) ---
        use crate::db::connection::knowledge_db_path;
        use rusqlite::Connection;

        let db_path = knowledge_db_path();
        if !db_path.exists() {
            return None; // fail-open: DB not yet initialised
        }

        let conn = match Connection::open(&db_path) {
            Ok(c) => c,
            Err(_) => return None, // fail-open
        };
        // Short busy-timeout so we don't block the hook.
        let _ = conn.busy_timeout(std::time::Duration::from_secs(5));

        // --- Check required tables exist ---
        let mut stmt = match conn.prepare("SELECT name FROM sqlite_master WHERE type='table'") {
            Ok(s) => s,
            Err(_) => return None,
        };
        let tables: std::collections::HashSet<String> =
            match stmt.query_map([], |row| row.get::<_, String>(0)) {
                Ok(rows) => rows.flatten().collect(),
                Err(_) => return None,
            };
        if !tables.contains("briefing_deliveries") || !tables.contains("knowledge_entries") {
            return None; // tables not yet created (first-run)
        }

        // --- Query recurred mistakes ---
        // Mirrors the Python SQL exactly: mistakes that were briefed to this
        // session and for which a new mistake (same session, after delivery)
        // was also recorded.
        let query = "
            SELECT DISTINCT bd.entry_id, ke.title
            FROM briefing_deliveries bd
            JOIN knowledge_entries ke ON bd.entry_id = ke.id
            WHERE bd.session_id = ?1
              AND ke.category = 'mistake'
              AND EXISTS (
                SELECT 1 FROM knowledge_entries new_ke
                WHERE new_ke.session_id = ?1
                  AND new_ke.category = 'mistake'
                  AND new_ke.id != ke.id
                  AND new_ke.first_seen >= bd.delivered_at
              )
        ";
        let mut stmt = match conn.prepare(query) {
            Ok(s) => s,
            Err(_) => return None,
        };
        let recurred: Vec<(i64, String)> = match stmt.query_map([&session_id], |row| {
            Ok((row.get::<_, i64>(0)?, row.get::<_, String>(1)?))
        }) {
            Ok(rows) => rows.flatten().collect(),
            Err(_) => return None,
        };

        if recurred.is_empty() {
            return None;
        }

        // --- Increment recurrence counter (fail-open per row) ---
        for (entry_id, _) in &recurred {
            let _ = conn.execute(
                "UPDATE knowledge_entries \
                 SET recurrence_after_briefing = COALESCE(recurrence_after_briefing, 0) + 1 \
                 WHERE id = ?1",
                [entry_id],
            );
        }

        // Return None — mirrors Python: `info(...)` is called but evaluate()
        // returns None.  The counter increment is the only observable effect.
        None
    }
}

/// Dispatched-subagent marker cleanup via stable CLI boundary, then informational
/// agentStop / subagentStop notice (native direct path).
///
/// Calls `python tentacle.py marker-cleanup --from-stop-event` as a subprocess,
/// piping the event JSON payload to stdin.  This is the stable CLI boundary that
/// allows Rust to perform marker cleanup without importing Python internals or
/// HMAC-signing markers directly.
///
/// Fail-open at every step: if Python is absent, tentacle.py is missing, or the
/// subprocess fails for any reason, this rule falls back to emitting the
/// informational event notice only.
///
/// Hard blockers that prevented earlier parity:
///   - `tentacle._clear_dispatched_subagent_marker` was only callable as a Python
///     internal.  The `--from-stop-event` CLI boundary resolves this.
///   - `marker_auth.py` HMAC signing is now handled by tentacle.py itself inside
///     the subprocess, so the Rust side never needs to read or write HMAC signatures.
///
/// Remaining Python-only path (via `sk hooks run`):
///   - `agentStop`/`subagentStop` now use the native path exclusively.
///   - See docs/HOOKS.md §Native Parity Gap Analysis for the full list of
///     events still routed to `hook_runner.py`.
pub struct AgentStopRule;

/// Attempt dispatched-subagent marker cleanup via the `tentacle.py` stable boundary.
///
/// Spawns `python tentacle.py marker-cleanup --from-stop-event` with the event
/// JSON payload on stdin.  Parses stdout for "Cleared: <name>" lines.
///
/// Returns `Some(cleared_names)` when at least one entry was removed, `None` when
/// nothing was cleared or the subprocess failed (fail-open).
pub(crate) fn try_stop_cleanup(data: &Value) -> Option<String> {
    use crate::config::{python_exe, resolve_tools_dir};

    let tools_dir = resolve_tools_dir();
    let tentacle_py = tools_dir.join("tentacle.py");
    if !tentacle_py.exists() {
        return None; // fail-open: tentacle.py not present
    }

    let json_input = match serde_json::to_string(data) {
        Ok(s) => s,
        Err(_) => return None,
    };

    let python = python_exe();
    let mut child = match Command::new(python)
        .arg(&tentacle_py)
        .arg("marker-cleanup")
        .arg("--from-stop-event")
        .stdin(Stdio::piped())
        .stdout(Stdio::piped())
        .stderr(Stdio::null()) // suppress Python tracebacks from hook output
        // Recursion guard (issue #396): prevent tentacle.py from re-triggering hooks.
        .env("SK_HOOK_ACTIVE", "1")
        .spawn()
    {
        Ok(c) => c,
        Err(_) => return None, // fail-open: Python not available
    };

    // Write event JSON to stdin then close so the subprocess can read EOF.
    if let Some(mut stdin) = child.stdin.take() {
        let _ = stdin.write_all(json_input.as_bytes());
        // stdin dropped here → EOF sent to subprocess
    }

    let output = match child.wait_with_output() {
        Ok(o) => o,
        Err(_) => return None,
    };

    // Extract "Cleared: <name>" lines from stdout.
    let stdout = String::from_utf8_lossy(&output.stdout);
    let cleared: Vec<&str> = stdout
        .lines()
        .filter_map(|line| line.strip_prefix("Cleared: "))
        .collect();

    if cleared.is_empty() {
        None
    } else {
        Some(cleared.join(", "))
    }
}

impl HookRule for AgentStopRule {
    fn name(&self) -> &'static str {
        "agent-stop"
    }

    fn events(&self) -> &'static [&'static str] {
        &["agentStop", "subagentStop"]
    }

    fn tools(&self) -> &'static [&'static str] {
        &[] // event-level, no tool filter
    }

    fn evaluate(&self, event: &str, data: &Value) -> Option<Value> {
        // Attempt marker cleanup via the stable CLI boundary (fail-open).
        let cleanup_msg = try_stop_cleanup(data);

        let base = format!("[sk] {event} received.");
        let message = match cleanup_msg {
            Some(names) => format!("{base} Marker cleanup: cleared {names}."),
            None => base,
        };
        Some(info(&message))
    }
}

// ---------------------------------------------------------------------------
// ErrorOccurredRule
// ---------------------------------------------------------------------------

/// Auto-searches the knowledge base when an error occurs.
///
/// Ports `hooks/rules/error_kb.py::ErrorKBRule`.
///
/// What this rule does:
///   1. Extracts the error message from the event payload
///      (`data.error` may be a string or an object with a `message` field).
///   2. Builds a focused search query from the first non-empty line of the
///      error message (up to 200 chars), matching the Python behaviour.
///   3. Searches the knowledge base natively (wave5): opens `knowledge.db`
///      directly and runs a FTS5 query via `crate::db::fts::search_kb_snippet`.
///      Falls back to spawning `query-session.py` only when the DB is
///      genuinely unavailable (connection error).
///   4. Returns an informational message with the snippets, or `None` when
///      no match is found.
///
/// Fail-open at every step:
///   - Empty error message → `None`.
///   - DB unavailable AND `query-session.py` absent/Python unavailable → `None`.
///   - Native search returns no results → `None` (no Python fallback for empty).
///   - Subprocess non-zero exit / empty output / "No results" → `None`.
///
/// Simplified vs. the Python version:
///   - No explicit 8-second subprocess timeout (Python uses `timeout=8`).
///     The risk is bounded because the native FTS query is synchronous and fast.
///   - Tool name and file path are included in the message when present.
pub struct ErrorOccurredRule;

/// Try the native Rust KB search path (no subprocess).
///
/// Opens `knowledge.db` read-only, runs an FTS5 query (with LIKE fallback),
/// and returns at most 8 formatted lines.  Returns `None` when:
///   - The DB cannot be opened (fail-open).
///   - The search produces no results.
pub(crate) fn try_query_kb_native(search_query: &str) -> Option<String> {
    use crate::db::connection::KnowledgeDb;
    use crate::db::fts::search_kb_snippet;

    let db = KnowledgeDb::open().ok()?; // fail-open: DB not available
    let lines = search_kb_snippet(&db.conn, search_query, 5);
    if lines.is_empty() {
        return None;
    }
    // Take at most 8 output lines (mirrors Python behaviour).
    let snippet: Vec<&str> = lines.iter().map(|s| s.as_str()).take(8).collect();
    Some(snippet.join("\n"))
}

/// Search the KB for the given query, trying native DB path first.
///
/// 1. Tries `try_query_kb_native` (direct SQLite — no subprocess).
/// 2. If native returns `None` (DB unavailable), falls back to spawning
///    `query-session.py` (Python subprocess).
///
/// Returns `None` when both paths fail or return empty/no-results output.
pub(crate) fn try_query_kb(search_query: &str) -> Option<String> {
    // Fast path: native Rust DB access (no subprocess).
    if let Some(result) = try_query_kb_native(search_query) {
        return Some(result);
    }

    // Python fallback: only reached when the DB is genuinely unavailable.
    use crate::config::{python_exe, resolve_tools_dir};

    let tools_dir = resolve_tools_dir();
    let query_script = tools_dir.join("query-session.py");
    if !query_script.exists() {
        return None; // fail-open: query-session.py not present
    }

    let python = python_exe();
    let output = match Command::new(python)
        .arg(&query_script)
        .arg(search_query)
        .stdout(Stdio::piped())
        .stderr(Stdio::null()) // suppress Python tracebacks from hook output
        .output()
    {
        Ok(o) => o,
        Err(_) => return None, // fail-open: Python not available
    };

    let stdout = String::from_utf8_lossy(&output.stdout);
    let trimmed = stdout.trim();

    if trimmed.is_empty() || trimmed.contains("No results") {
        return None;
    }

    // Take up to 8 lines (mirrors Python behaviour).
    let lines: Vec<&str> = trimmed.lines().take(8).collect();
    Some(lines.join("\n"))
}

impl HookRule for ErrorOccurredRule {
    fn name(&self) -> &'static str {
        "error-kb"
    }

    fn events(&self) -> &'static [&'static str] {
        &["errorOccurred"]
    }

    fn tools(&self) -> &'static [&'static str] {
        &[] // event-level, no tool filter
    }

    fn evaluate(&self, _event: &str, data: &Value) -> Option<Value> {
        // Extract error message: data["error"] may be a string or {message: ...}.
        let error_msg = if let Some(e) = data.get("error") {
            if let Some(s) = e.as_str() {
                s
            } else if let Some(obj) = e.as_object() {
                obj.get("message").and_then(|v| v.as_str()).unwrap_or("")
            } else {
                ""
            }
        } else {
            ""
        };

        if error_msg.is_empty() {
            return None;
        }

        // Use at most 500 chars of the error message (mirrors Python).
        let error_msg = &error_msg[..error_msg.len().min(500)];

        // Build focused search query from the first meaningful line (≤200 chars).
        let search_query: &str = error_msg
            .lines()
            .map(|l| l.trim())
            .find(|l| !l.is_empty())
            .map(|l| &l[..l.len().min(200)])
            .unwrap_or(error_msg);

        if search_query.is_empty() {
            return None;
        }

        let kb_output = try_query_kb(search_query)?;

        let tool_name = data
            .get("toolName")
            .or_else(|| data.get("tool"))
            .and_then(|v| v.as_str())
            .unwrap_or("");

        let file_path = data
            .get("error")
            .and_then(|e| e.as_object())
            .and_then(|obj| obj.get("file").or_else(|| obj.get("path")))
            .and_then(|v| v.as_str())
            .unwrap_or("");

        let mut lines: Vec<String> =
            vec!["\n  \u{1f50d} KB MATCH: Found past knowledge about this error:".to_string()];
        // Context line (tool / file).
        let mut ctx_parts: Vec<&str> = Vec::new();
        if !tool_name.is_empty() {
            ctx_parts.push(tool_name);
        }
        if !file_path.is_empty() {
            ctx_parts.push(file_path);
        }
        if !ctx_parts.is_empty() {
            lines.push(format!("  ({})", ctx_parts.join(", ")));
        }
        for kb_line in kb_output.lines() {
            lines.push(format!("  {kb_line}"));
        }
        let display_query = &search_query[..search_query.len().min(80)];
        lines.push(String::new());
        lines.push(format!("  Run: sk query \"{display_query}\" --verbose"));
        lines.push(String::new());

        Some(info(&lines.join("\n")))
    }
}

// ---------------------------------------------------------------------------
// SkillUsageRule (wave28, issue #119)
// ---------------------------------------------------------------------------

/// Records event-level skill usage (triggered / loaded / skipped) on
/// ``postToolUse`` for the ``skill`` tool.
///
/// Two events per invocation:
///   - ``triggered`` — always recorded.
///   - ``loaded`` or ``skipped`` — determined by ``toolResult``:
///     - absent / null / empty string → ``loaded`` (fail-open default)
///     - dict with ``exitCode``/``exit_code``:
///       - 0 → ``loaded``
///       - non-zero → ``skipped``
///     - dict without exit code, or plain string:
///       short output (< 200 bytes) containing a skill-loader-specific
///       skip phrase → ``skipped``; everything else → ``loaded``
///
/// Mirrors ``hooks/rules/skill_usage.py::SkillUsageRule``.
/// Fail-open: any DB or filesystem error is silently discarded.
pub struct SkillUsageRule;

impl SkillUsageRule {
    pub(crate) fn skill_metrics_db_path() -> PathBuf {
        resolve_home_dir()
            .unwrap_or_else(|| PathBuf::from("."))
            .join(".copilot")
            .join("session-state")
            .join("skill-metrics.db")
    }

    pub(crate) fn detect_secondary_event(data: &Value) -> &'static str {
        let tool_result = data.get("toolResult");
        match tool_result {
            None | Some(Value::Null) => return "loaded",
            Some(Value::String(s)) if s.is_empty() => return "loaded",
            _ => {}
        }
        // Check exitCode / exit_code (exitCode takes precedence).
        if let Some(obj) = tool_result.and_then(|v| v.as_object()) {
            let exit_code = obj
                .get("exitCode")
                .or_else(|| obj.get("exit_code"))
                .and_then(|v| v.as_i64());
            if let Some(code) = exit_code {
                return if code == 0 { "loaded" } else { "skipped" };
            }
            // No numeric exit code — check output text.
            let output = obj
                .get("output")
                .or_else(|| obj.get("stdout"))
                .and_then(|v| v.as_str())
                .unwrap_or("");
            return Self::classify_output(output);
        }
        // Plain string toolResult.
        if let Some(Value::String(s)) = tool_result {
            return Self::classify_output(s.as_str());
        }
        "loaded"
    }

    pub(crate) fn classify_output(output: &str) -> &'static str {
        if output.len() >= 200 {
            return "loaded";
        }
        let lower = output.to_lowercase();
        const SKIP_MARKERS: &[&str] = &[
            "skill skipped",
            "skill was skipped",
            "skill skipping",
            "skipping skill",
            "skill not found",
            "skill unavailable",
            "skill_skip",
            "cannot load skill",
            "unable to load skill",
            "could not load skill",
            "skill could not be loaded",
            "no skill matched",
            "no skill found",
        ];
        if SKIP_MARKERS.iter().any(|&m| lower.contains(m)) {
            "skipped"
        } else {
            "loaded"
        }
    }

    pub(crate) fn record_events(
        skill_name: &str,
        events: &[&str],
        session_id: &str,
        db_path: &Path,
    ) {
        // Fail-open: any error is silently discarded (telemetry must not block).
        let _ = (|| -> rusqlite::Result<()> {
            if let Some(parent) = db_path.parent() {
                let _ = std::fs::create_dir_all(parent);
            }
            let conn = rusqlite::Connection::open(db_path)?;
            conn.execute_batch(
                "CREATE TABLE IF NOT EXISTS skill_usage_events (
                    id         INTEGER PRIMARY KEY AUTOINCREMENT,
                    skill_name TEXT NOT NULL,
                    event      TEXT NOT NULL,
                    session_id TEXT NOT NULL,
                    timestamp  TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_sue_skill_name ON skill_usage_events (skill_name);
                CREATE INDEX IF NOT EXISTS idx_sue_event      ON skill_usage_events (event);
                CREATE INDEX IF NOT EXISTS idx_sue_session    ON skill_usage_events (session_id);",
            )?;
            let now = chrono::Utc::now().format("%Y-%m-%dT%H:%M:%SZ").to_string();
            for event in events {
                conn.execute(
                    "INSERT INTO skill_usage_events \
                     (skill_name, event, session_id, timestamp) \
                     VALUES (?1, ?2, ?3, ?4)",
                    rusqlite::params![skill_name, event, session_id, now],
                )?;
            }
            Ok(())
        })();
    }
}

impl HookRule for SkillUsageRule {
    fn name(&self) -> &'static str {
        "skill-usage"
    }

    fn events(&self) -> &'static [&'static str] {
        &["postToolUse"]
    }

    fn tools(&self) -> &'static [&'static str] {
        &["skill"]
    }

    fn evaluate(&self, _event: &str, data: &Value) -> Option<Value> {
        let skill_name = data
            .get("toolInput")
            .or_else(|| data.get("toolArgs"))
            .and_then(|v| v.get("skill"))
            .and_then(|v| v.as_str())
            .map(str::trim)
            .filter(|s| !s.is_empty())?;

        let session_id = data
            .get("sessionId")
            .or_else(|| data.get("session_id"))
            .and_then(|v| v.as_str())
            .unwrap_or("unknown");

        let secondary = Self::detect_secondary_event(data);
        let db_path = Self::skill_metrics_db_path();
        Self::record_events(skill_name, &["triggered", secondary], session_id, &db_path);

        None // informational — no user-visible message
    }
}
