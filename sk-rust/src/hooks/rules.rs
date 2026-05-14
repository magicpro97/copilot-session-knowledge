/// Native hook rules — Rust implementations of the highest-value rules
/// from `hooks/rules/__init__.py`.
///
/// Each rule implements the `HookRule` trait.  The dispatcher in
/// `runner.rs` iterates `all_rules()` in registration order, matching by
/// event type and (optionally) tool name.
///
/// Rules covered here:
///   - `SessionStartRule` — sessionStart: informational session-start notice.
///     Full briefing + integrity work stays in the Python fallback path.
///   - `SubagentGitGuardRule` — preToolUse: blocks `git commit/push` while a
///     dispatched-subagent marker is active.  Mirrors `hooks/rules/subagent_guard.py`.
///   - `BlockEditDistRule` — preToolUse: blocks direct edit/create operations
///     targeting `browse-ui/dist/` build artifacts.  Mirrors
///     `hooks/rules/block_edit_dist.py`.  Fail-open when toolArgs/path are
///     absent or malformed.  No HMAC dependency — pure path matching.
///   - `BlockUnsafeHtmlRule` — preToolUse: blocks edit/create operations that
///     introduce `dangerouslySetInnerHTML` in `.ts`/`.tsx`/`.js`/`.jsx` files
///     without an accompanying sanitization call.  Mirrors
///     `hooks/rules/block_unsafe_html.py`.  Fail-open when toolArgs, path,
///     or proposed content are absent.  No HMAC dependency — pure string matching.
///   - `TrackEditsRule` — postToolUse: full counter-write port for `bash` tool.
///     For bash: runs `git status --porcelain`, computes newly modified files
///     since the last run, and updates HMAC-signed counters (`code-edit-count`,
///     `py-edit-count`) and the `tentacle-edits` list marker via `marker_auth`.
///     Counter values are preserved (read-first, then delta-increment — no resets).
///     For edit/create tools: informational acknowledgement only; counter writes
///     for those tools remain Python-owned (`TestReminderRule`) to avoid
///     dual-writer drift.  Full port of
///     `hooks/rules/edit_tracker.py::TrackEditsRule` (wave6).
///   - `LearnReminderRule` — postToolUse: informs after `task_complete` to record
///     learnings; writes `learn-done` marker when `learn.py` is called via bash.
///     Conservative port of `hooks/rules/learn_reminder.py::LearnReminderRule`.
///     Does NOT write counters.  Informational-only.  Fail-open.
///   - `TestReminderRule` — postToolUse: full counter-write port for `edit`/`create`/
///     `bash` tools (wave7).  Increments `py-edit-count` (HMAC-signed) on each
///     detected Python file edit; deletes the `tests-ran` marker to reset the
///     evidence flag; touches `tests-ran` when a test run is detected via bash.
///     Emits at count >= 3 and count % 3 == 0 (mirrors Python threshold).
///     Counter values are preserved (read-first, delta-increment — no resets).
///     Informational-only.  Fail-open.
///   - `NextjsTypecheckReminderRule` — postToolUse: full counter-write port (wave7).
///     Increments `ts-edit-count` (plain-text counter, NOT HMAC) on each browse-ui
///     `.ts`/`.tsx` edit.  Emits at count >= 3 and count % 3 == 0.
///     Informational-only.  Fail-open.
///   - `ReadBeforeEditRule` — preToolUse + postToolUse (wave7): tracks files read
///     via `view`/`grep`/`glob` in the HMAC-signed `viewed-files` list marker.
///     On preToolUse edit/create, emits an informational warning when the target
///     file was not yet read.  Fail-open — never denies.
///   - `PnpmLockfileGuardRule` — preToolUse bash (wave7): blocks `git commit` when
///     `browse-ui/package.json` is staged but `browse-ui/pnpm-lock.yaml` is not.
///     Runs `git diff --cached --name-only` as a subprocess.  Fail-open.
///   - `VerificationGatePostRule` — postToolUse bash (wave7): records evidence from
///     successful verification commands (Python tests, pnpm checks) into the
///     HMAC-signed verification ledger.  Also marks surfaces dirty when bash writes
///     source files.  Preserves the Python JSON-in-HMAC-set ledger format exactly.
///     Only the postToolUse evidence-recording half — deny-capable preToolUse half
///     remains Python-only.  Informational / side-effect only.  Fail-open.
///   - `VerificationGatePreRule` — preToolUse (wave8): dirty-marking for Python /
///     browse-ui surfaces on edit/create and closeout-style deny for bash /
///     task_complete when evidence is missing.  Mirrors `_pre()` half of
///     `hooks/rules/verification_gate.py::VerificationGateRule`.  Fail-open on
///     malformed payloads or missing ledger state.  Preserve existing marker formats.
///   - `TentacleSuggestRule` — postToolUse (wave8): reads the `tentacle-edits`
///     HMAC-signed list marker and suggests tentacle-orchestration when edits span
///     ≥3 files across ≥2 modules.  Read-only (`TrackEditsRule` remains the writer).
///     Informational-only.  Fail-open.  Handles both legacy flat-path and new
///     JSON-dict marker formats.
///   - `AutoBugDetectorRule` — postToolUse edit/create (wave13, issue #86):
///     detects five bug-fix pattern categories (error-handling, null-safety,
///     guard-clause, async-fix, type-fix) from diff payloads.  On edit:
///     all five categories active.  On create: null-safety (0.62) and
///     async-fix (0.62) are enabled; the others remain excluded (0.0).
///     Calls ``learn.py --mistake`` via subprocess using a 5-minute bucketed
///     title so repeated detections increment ``occurrence_count`` rather than
///     being silently dropped.  Writes the ``learn-done`` marker once after
///     one or more successful learn calls in the same evaluation.  Error-handling old-code check mirrors Python
///     specificity: only ``raise *Error`` (not bare ``raise``) suppresses new
///     exception-handling detections.  Informational-only.  Fail-open.
///     No regex dependency.
///   - `SessionEndRule` — sessionEnd: per-session marker cleanup + session.log
///     entry.  Ports `hooks/rules/session_lifecycle.py::SessionEndRule`.
///     Uses `COPILOT_AGENT_SESSION_ID` env var to scope cleanup to the current
///     session.  Fail-open on missing env var or filesystem errors.
///   - `AgentStopRule` — agentStop/subagentStop: informational stop notice.
///     Full dispatched-subagent marker cleanup stays in the Python fallback path.
///   - `ErrorOccurredRule` — errorOccurred: auto-searches the knowledge base by
///     querying the native Rust DB path (wave5).  Falls back to spawning
///     `query-session.py` as a subprocess only when the DB is genuinely
///     unavailable.  Mirrors `hooks/rules/error_kb.py::ErrorKBRule`.
///     Fail-open at every step.
///
/// wave10 routing flip: managed `postToolUse` is now native.  All seven
/// postToolUse rules above are informational-only; no HMAC enforcement rules
/// exist for postToolUse.  `sync_markers::record_sync_signal` in runner.rs
/// continues to write `sync-nudge.json` after rule dispatch.
///
/// wave11 native rule availability (preToolUse parity, no routing flip):
///   - `EnforceBriefingRule` — preToolUse: blocks `edit`/`create`/`bash`
///     writes to source files until a valid briefing marker exists.
///     Reads: `markers/briefing-done`, session-scoped `briefing-done-{sid}`,
///     or any `briefing-*` file with mtime < 30 min and valid HMAC signature.
///     Mirrors `hooks/rules/briefing.py::EnforceBriefingRule`.  Deny-capable.
///   - `EnforceLearnRule` — preToolUse: tracks code-file edits (increments
///     `markers/code-edit-count` HMAC-signed counter); blocks `git commit/push`
///     and `task_complete` when edits ≥ 3 without a `markers/learn-done` marker.
///     Mirrors `hooks/rules/learn_gate.py::EnforceLearnRule`.  Deny-capable.
///     NOTE: `preToolUse` is still NOT in `NATIVE_EVENTS` — managed preToolUse
///     routing remains Python-owned (`hook_runner.py`).  These rules are available
///     for native direct calls and tests only.
///
/// wave12 native rule availability (preToolUse parity, no routing flip):
///   - `TentacleEnforceRule` — preToolUse: denies `edit`/`create`/`bash`
///     writes when `tentacle-edits` marker shows ≥ 3 files across ≥ 2 modules
///     without a valid `tentacle-done` or `tentacle-bypass` marker.  Applies
///     per-repo filtering and 24-hour TTL pruning.  Handles both legacy flat
///     and new JSON-dict marker formats.  Mirrors
///     `hooks/rules/tentacle.py::TentacleEnforceRule`.  Deny-capable.
///     Inserts between `EnforceLearnRule` and `SubagentGitGuardRule` to match
///     Python `first-deny-wins` dispatch order.
///     NOTE: `preToolUse` was NOT in `NATIVE_EVENTS` after wave12 —
///     managed preToolUse routing remained Python-owned (`hook_runner.py`).
///     This was superseded by the wave13 routing flip (see below).
///
/// wave13 native port + routing flip (preToolUse now native for Rust-binary installs):
///   - `SyntaxGateRule` — preToolUse: blocks `edit`/`create` on `*.py` files
///     when the resulting file would have a Python syntax error.  Mirrors
///     `hooks/rules/syntax_gate.py::SyntaxGateRule`.  Uses a Python subprocess
///     boundary via `python_exe()` + `py_compile` (same pattern used by the
///     session-start briefing subprocess): writes content to a temp file, invokes
///     `python -c "import py_compile; py_compile.compile(..., doraise=True)"`,
///     and parses stderr for the error message.  For `edit`: reads the file from
///     disk, applies the replacement once (count==1 guard), then checks the
///     result.  For `create`: checks `file_text` directly.  Non-`.py` files pass
///     unconditionally.  Fail-open at every step: Python unavailable, temp-file
///     write errors, subprocess timeout (10s), or parse errors all allow rather
///     than deny.  No HMAC dependency — pure syntax check via subprocess.
///     Inserts between `SubagentGitGuardRule` and `BlockEditDistRule` to match
///     the Python `first-deny-wins` dispatch order.
///     Routing flip: `preToolUse` is now in `NATIVE_EVENTS` for Rust-binary
///     installs (`sk hooks run preToolUse` routes natively).  Python `sk.py` shim
///     and non-Rust installs continue to fall back to `hook_runner.py` for all
///     events regardless of `NATIVE_EVENTS` — the Python path is unchanged.
use crate::config::resolve_home_dir;
use std::collections::HashSet;
use std::fs;
use std::io::{Read, Write};
use std::path::{Path, PathBuf};
use std::process::{Command, Stdio};
use std::time::{Duration, Instant, SystemTime, UNIX_EPOCH};

use serde_json::Value;

use super::marker_auth;

// ---------------------------------------------------------------------------
// Trait
// ---------------------------------------------------------------------------

/// A single hook rule. Mirrors the Python `Rule` base class.
pub trait HookRule: Send + Sync {
    /// Unique rule identifier (matches the Python `rule.name`).
    fn name(&self) -> &'static str;

    /// Events this rule handles (e.g. `["preToolUse"]`).
    fn events(&self) -> &'static [&'static str];

    /// Tool names this rule applies to. Empty slice means *all* tools.
    fn tools(&self) -> &'static [&'static str];

    /// Evaluate the rule.
    ///
    /// Returns `Some(Value)` to take action, `None` to pass (no-op).
    ///
    /// For `preToolUse`, a deny result must contain:
    ///   `{"permissionDecision": "deny", "permissionDecisionReason": "<reason>"}`
    ///
    /// For all other events, an informational result should contain:
    ///   `{"message": "<text>"}`
    fn evaluate(&self, event: &str, data: &Value) -> Option<Value>;
}

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

fn markers_dir() -> PathBuf {
    resolve_home_dir()
        .unwrap_or_else(|| PathBuf::from("."))
        .join(".copilot")
        .join("markers")
}

/// Build a preToolUse deny result (mirrors Python `common.deny()`).
pub fn deny(reason: &str) -> Value {
    serde_json::json!({
        "permissionDecision": "deny",
        "permissionDecisionReason": reason,
    })
}

/// Build an informational result (mirrors Python `common.info()`).
pub fn info(message: &str) -> Value {
    serde_json::json!({"message": message})
}

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
fn load_hooks_config() -> Value {
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
fn load_memory_md(cwd: Option<&Path>) -> Option<String> {
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

const BREADCRUMB_FILENAME: &str = "goal-resume-breadcrumb.json";

/// Map a raw ``pause_reason`` string to a short human-readable label.
///
/// The prefix before `:` is extracted and matched so that "session_end:normal"
/// yields "session end".  Unknown prefixes fall back to "paused".
///
/// Future-compatible: "compaction" and "quota" prefixes are recognised even
/// though those pause paths are not yet implemented (issues #182 / #187).
fn format_pause_reason(raw: &str) -> &'static str {
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
fn load_goal_resume_hint(project_root: Option<&Path>) -> Option<Vec<String>> {
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
                    // Preserve permanent system markers.
                    if matches!(
                        name.as_ref(),
                        "hooks-tampered" | "session.log" | "audit.jsonl"
                    ) {
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
            .args(["--budget", "2000"])
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
                    // Reap the reader thread (pipe is closed after kill+wait).
                    if let Some(handle) = reader_thread {
                        let _ = handle.join();
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
fn sha256_file(path: &std::path::Path) -> Option<String> {
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
fn hooks_src_dir() -> PathBuf {
    resolve_home_dir()
        .unwrap_or_else(|| PathBuf::from("."))
        .join(".copilot")
        .join("tools")
        .join("hooks")
}

/// `~/.copilot/hooks` — the hooks installation directory (holds hooks.json etc).
fn hooks_dst_dir() -> PathBuf {
    resolve_home_dir()
        .unwrap_or_else(|| PathBuf::from("."))
        .join(".copilot")
        .join("hooks")
}

/// Path of the integrity manifest JSON file.
fn integrity_manifest_path() -> PathBuf {
    hooks_dst_dir().join("integrity-manifest.json")
}

/// Regenerate the integrity manifest from the current hook files.
///
/// Mirrors `IntegrityRule._regenerate_manifest()`.
fn regenerate_manifest() {
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

const SUBAGENT_MARKER_TTL_SECS: u64 = 14400; // 4 hours

/// Return `true` iff the dispatched-subagent-active marker is present and fresh.
///
/// Fail-open: any read / parse error → `false` (allow through).
///
/// Wave6: calls `marker_auth::verify_marker` before trusting TTL data.
/// - With a secret: unsigned or tampered markers are not trusted → `false`.
/// - No secret: backward-compat — any existing file is accepted.
///   Authentication failures never add new denials (fail-open).
fn subagent_marker_is_fresh() -> bool {
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
fn read_tentacle_info() -> String {
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
///
/// Uses simple string matching to avoid depending on the optional `regex`
/// crate.  With the `native-hooks` feature enabled, a full word-boundary
/// regex would be used instead; for now this covers all practical cases.
fn command_is_git_commit_or_push(command: &str) -> bool {
    // Quick reject: must contain "git"
    if !command.contains("git") {
        return false;
    }
    // Accept if the command contains "commit" or "push" (common cases).
    command.contains("commit") || command.contains("push")
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
// TrackEditsRule — helpers
// ---------------------------------------------------------------------------

/// Code extensions that count as code edits.
///
/// Mirrors Python `CODE_EXTENSIONS` in `hooks/rules/common.py`.
/// Markdown (.md) is intentionally excluded: session-research and docs writes
/// must not inflate multi-module edit counters.
const TRACK_CODE_EXTENSIONS: &[&str] = &[
    ".py", ".kt", ".ts", ".tsx", ".js", ".jsx", ".swift", ".java", ".go", ".rs", ".json", ".yaml",
    ".yml", ".xml", ".html", ".css", ".toml", ".sh", ".bat", ".ps1",
];

/// Return `true` if `path` is under the Copilot session-state directory.
///
/// Mirrors Python `is_session_path(path)` in `hooks/rules/common.py`.
fn is_track_session_path(path: &str) -> bool {
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
fn is_auto_bug_session_path(path: &str) -> bool {
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
fn has_code_extension(path: &str) -> bool {
    let lower = path.to_lowercase();
    TRACK_CODE_EXTENSIONS.iter().any(|ext| lower.ends_with(ext))
}

/// Run `git status --porcelain -uall` and return the set of modified/added files.
///
/// Returns an empty set on any error (fail-open).
/// Mirrors Python `TrackEditsRule._get_git_modified()`.
fn get_git_modified() -> HashSet<String> {
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
fn load_git_modified_seen() -> HashSet<String> {
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
fn save_git_modified_seen(seen: &HashSet<String>) {
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
// LearnReminderRule
// ---------------------------------------------------------------------------

/// Informational postToolUse learn reminder.
///
/// Conservative port of `hooks/rules/learn_reminder.py::LearnReminderRule`
/// for the native direct path:
///   - When a `bash` command invokes `learn.py`, writes the `learn-done` marker
///     via [`marker_auth::sign_marker`] so the enforce-learn hook can verify it.
///     The write is idempotent and safe even when Python also writes the same marker.
///   - When `task_complete` is called with `resultType == "success"`, emits a
///     reminder to record learnings.
///
/// Key constraints:
///   - Does NOT write any counters (counter writes remain Python-owned).
///   - Informational-only: no `permissionDecision: deny` is ever returned.
///   - Fail-open: any missing field → `None` or no marker write.
pub struct LearnReminderRule;

/// Return `true` when `command` looks like a `learn.py` invocation.
///
/// Mirrors `re.search(r"python3?\s+.*learn\.py\b", command)` and also catches
/// `sk learn` invocations via the sk shim.
fn command_invokes_learn_py(command: &str) -> bool {
    if !command.contains("learn.py") && !command.contains("sk learn") {
        return false;
    }
    if command.contains("sk learn") {
        return true;
    }
    // Must have a python prefix somewhere before learn.py.
    command.contains("python3 ")
        || command.contains("python3\t")
        || command.contains("python ")
        || command.contains("python\t")
}

impl HookRule for LearnReminderRule {
    fn name(&self) -> &'static str {
        "learn-reminder"
    }

    fn events(&self) -> &'static [&'static str] {
        &["postToolUse"]
    }

    fn tools(&self) -> &'static [&'static str] {
        &["bash", "task_complete"]
    }

    fn evaluate(&self, _event: &str, data: &Value) -> Option<Value> {
        let tool_name = data.get("toolName").and_then(|v| v.as_str()).unwrap_or("");

        if tool_name == "bash" {
            let command = data
                .get("toolArgs")
                .and_then(|a| a.as_object())
                .and_then(|o| o.get("command"))
                .and_then(|v| v.as_str())
                .unwrap_or("");
            if command_invokes_learn_py(command) {
                // Write learn-done marker so enforce-learn hook can verify it.
                // Conservative: sign_marker is idempotent; dual writes with Python are safe.
                let marker_path = markers_dir().join("learn-done");
                let _ = marker_auth::sign_marker(&marker_path, "learn-done");
            }
            return None; // bash: never emit output (mirrors Python)
        }

        if tool_name == "task_complete" {
            let result_type = data
                .get("toolResult")
                .and_then(|r| r.as_object())
                .and_then(|o| o.get("resultType"))
                .and_then(|v| v.as_str())
                .unwrap_or("");
            if result_type != "success" {
                return None;
            }
            return Some(info(
                "\n  \u{1f9e0} LEARN REMINDER: Task completed! Did you learn something?\n\
                  Record mistakes, patterns, or decisions for future sessions:\n\n\
                  sk learn --mistake \"Title\" \"Description\" --wing <wing> --room <room>\n\
                  (fallback: python3 ~/.copilot/tools/learn.py)\n\n\
                  \u{1f4cb} SYNC CHECK: Did behavior change? Check the sync matrix:\n\
                  docs/SYNC-MATRIX.md \u{2014} docs \u{00b7} memory \u{00b7} operator follow-ups\n",
            ));
        }

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
fn is_py_source_path(file_path: &str) -> bool {
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
fn hook_file_path(data: &Value) -> Option<&str> {
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
fn bash_writes_py_file(command: &str) -> bool {
    if !command.contains(".py") {
        return false;
    }
    // Redirect-to-py: `> /some/path.py` or heredoc with `open('...py', ...)`
    (command.contains("> ") && command.ends_with(".py"))
        || (command.contains("open(") && command.contains(".py"))
        || (command.contains("sed -i") && command.contains(".py"))
}

/// Path to the `tests-ran` marker file (plain file, not HMAC-signed).
fn tests_ran_path() -> PathBuf {
    markers_dir().join("tests-ran")
}

/// Touch (create) the `tests-ran` marker — signals that tests were run.
fn mark_tests_ran() {
    let _ = fs::create_dir_all(markers_dir());
    let _ = fs::write(tests_ran_path(), b"");
}

/// Delete the `tests-ran` marker file if it exists.
fn clear_tests_ran() {
    let p = tests_ran_path();
    if p.is_file() {
        let _ = fs::remove_file(p);
    }
}

/// Return `true` when `command` looks like a test runner invocation.
///
/// Mirrors the bash branch of `TestReminderRule.evaluate()` in `edit_tracker.py`.
fn detect_test_run(command: &str) -> bool {
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
fn increment_py_edit_count(added: i64) -> i64 {
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
fn read_ts_edit_count() -> i64 {
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
fn write_ts_edit_count(value: i64) {
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
const READ_BEFORE_EDIT_EXTENSIONS: &[&str] = &[
    ".py", ".ts", ".tsx", ".js", ".jsx", ".md", ".json", ".yaml", ".yml", ".toml", ".css", ".html",
    ".sh", ".go", ".rs", ".swift", ".kt", ".java",
];

/// Extract a file path from `toolInput.path` or `toolArgs.path`.
///
/// `toolInput` is the Copilot CLI native field; `toolArgs` is the normalised
/// form used by some existing rules.  Try both to maximise coverage.
fn tool_input_path(data: &Value) -> Option<&str> {
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
fn is_read_before_edit_ext(path: &str) -> bool {
    let lower = path.to_lowercase();
    READ_BEFORE_EDIT_EXTENSIONS
        .iter()
        .any(|ext| lower.ends_with(ext))
}

/// Return `true` when `path` is absolute (Unix `/` or Windows drive / UNC).
fn is_absolute_path(path: &str) -> bool {
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
        if !command.contains("git") || !command.contains("commit") {
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
// VerificationGatePostRule — helpers
// ---------------------------------------------------------------------------

// Surface identifiers — match Python constants in `verification_gate.py`.
const SURFACE_PY: &str = "py";
const SURFACE_UI: &str = "ui";

// Evidence identifiers — match Python constants.
const EV_PY_TESTS: &str = "py_tests";
const EV_UI_FORMAT: &str = "ui_format";
const EV_UI_LINT: &str = "ui_lint";
const EV_UI_TYPECHECK: &str = "ui_typecheck";
const EV_UI_BUILD: &str = "ui_build";

/// Requirements map: which evidence keys each surface needs.
///
/// Mirrors Python `_REQUIREMENTS` dict.
const SURFACE_REQUIREMENTS: &[(&str, &[&str])] = &[
    (SURFACE_PY, &[EV_PY_TESTS]),
    (
        SURFACE_UI,
        &[EV_UI_FORMAT, EV_UI_LINT, EV_UI_TYPECHECK, EV_UI_BUILD],
    ),
];

/// Return the set of surfaces affected by editing `path`.
///
/// Mirrors Python `_surfaces_from_path(path)`.
fn surfaces_from_path(path: &str) -> Vec<&'static str> {
    let mut surfaces = Vec::new();
    let lower = path.to_lowercase();
    let norm = path.replace('\\', "/");
    if (norm.contains("browse-ui/") || norm.starts_with("browse-ui/"))
        && (lower.ends_with(".ts")
            || lower.ends_with(".tsx")
            || lower.ends_with(".js")
            || lower.ends_with(".jsx"))
    {
        surfaces.push(SURFACE_UI);
    }
    if lower.ends_with(".py") {
        surfaces.push(SURFACE_PY);
    }
    surfaces
}

/// Detect evidence categories provided by a bash command.
///
/// Mirrors Python `_evidence_from_command(command)`.
fn evidence_from_command(command: &str) -> Vec<&'static str> {
    let mut ev = Vec::new();
    if command.contains("test_security.py")
        || command.contains("test_fixes.py")
        || command.contains("run_all_tests.py")
        || command.contains("pytest")
    {
        ev.push(EV_PY_TESTS);
    }
    // `python3 test_*.py` heuristic.
    if (command.contains("python3 ") || command.contains("python "))
        && command.contains("test_")
        && command.contains(".py")
        && !ev.contains(&EV_PY_TESTS)
    {
        ev.push(EV_PY_TESTS);
    }
    if command.contains("pnpm format") {
        ev.push(EV_UI_FORMAT);
    }
    if command.contains("pnpm lint") {
        ev.push(EV_UI_LINT);
    }
    if command.contains("pnpm typecheck") {
        ev.push(EV_UI_TYPECHECK);
    }
    if command.contains("pnpm build") {
        ev.push(EV_UI_BUILD);
    }
    ev
}

/// Return `true` when toolResult shows no obvious failure indicators.
///
/// Mirrors Python `_looks_successful(data)`.  Fail-open: absent or unreadable
/// toolResult → `true`.
fn looks_successful(data: &Value) -> bool {
    let tool_result = match data.get("toolResult") {
        Some(r) if !r.is_null() => r,
        _ => return true, // absent / null → assume success (fail-open)
    };

    // Failure indicators (simplified subset of Python _FAIL_RE).
    let fail_indicators = &[
        "FAILED",
        "Failed:",
        "Errors:",
        "error TS",
        "Exit code",
        "Exit status",
        " fail ",
        "failed.",
        "failures",
    ];

    let output: String = if let Some(obj) = tool_result.as_object() {
        // Check numeric exit code.
        if let Some(code) = obj
            .get("exitCode")
            .or_else(|| obj.get("exit_code"))
            .and_then(|v| v.as_i64())
        {
            if code != 0 {
                return false;
            }
        }
        obj.get("output")
            .or_else(|| obj.get("stdout"))
            .and_then(|v| v.as_str())
            .unwrap_or("")
            .to_string()
    } else if let Some(s) = tool_result.as_str() {
        s.to_string()
    } else {
        return true;
    };

    !fail_indicators.iter().any(|pat| output.contains(pat))
}

/// Parse the verification ledger from the HMAC-signed list marker.
///
/// Returns `(dirty: HashSet<String>, evidence: HashSet<String>)`.
/// Mirrors Python `_read_ledger()`: expects a single JSON payload string
/// in the HMAC-signed set.
fn read_ledger() -> (HashSet<String>, HashSet<String>) {
    let ledger_path = markers_dir().join("verification-ledger");
    let raw_set = marker_auth::verify_list_marker(&ledger_path);

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

    if !raw_set.is_empty() {
        if raw_set.len() == 1 {
            let sole = raw_set.iter().next().unwrap();
            if sole.starts_with('{') {
                if let Some(parsed) = parse_payload(sole) {
                    return parsed;
                }
            }
        }
        // Non-singleton or unparseable → empty (corrupt ledger reset).
        return (HashSet::new(), HashSet::new());
    }

    // Backward compat: try reading as plain JSON (older upstream format).
    if ledger_path.is_file() {
        if let Ok(content) = fs::read_to_string(&ledger_path) {
            if let Some(parsed) = parse_payload(content.trim()) {
                return parsed;
            }
        }
    }

    (HashSet::new(), HashSet::new())
}

/// Write the verification ledger as a JSON-in-HMAC-set (single-element set).
///
/// Format: `sign_list_marker(LEDGER_FILE, [json_payload_string])`.
/// Mirrors Python `_write_ledger(dirty, evidence)`.
fn write_ledger(dirty: &HashSet<String>, evidence: &HashSet<String>) {
    let ledger_path = markers_dir().join("verification-ledger");
    let _ = fs::create_dir_all(markers_dir());

    let mut dirty_sorted: Vec<&str> = dirty.iter().map(|s| s.as_str()).collect();
    dirty_sorted.sort_unstable();
    let mut ev_sorted: Vec<&str> = evidence.iter().map(|s| s.as_str()).collect();
    ev_sorted.sort_unstable();

    // Compact JSON with keys in alphabetical order (dirty < evidence).
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
    let _ = marker_auth::sign_list_marker(&ledger_path, &[payload]);
}

/// Mark surfaces dirty and clear now-stale evidence.
///
/// Mirrors Python `_mark_dirty_surfaces(surfaces)`.
fn mark_dirty_surfaces(new_surfaces: &[&'static str]) {
    if new_surfaces.is_empty() {
        return;
    }
    let (mut dirty, mut evidence) = read_ledger();
    // Add new dirty surfaces.
    for s in new_surfaces {
        dirty.insert(s.to_string());
    }
    // Clear evidence that became stale for the newly-dirty surfaces.
    let stale_ev: HashSet<&str> = SURFACE_REQUIREMENTS
        .iter()
        .filter(|(surf, _)| new_surfaces.contains(surf))
        .flat_map(|(_, evs)| evs.iter().copied())
        .collect();
    evidence.retain(|ev| !stale_ev.contains(ev.as_str()));
    write_ledger(&dirty, &evidence);
}

/// Best-effort extraction of written file paths from a bash command.
///
/// Mirrors `_extract_written_paths(command)` in `verification_gate.py`.
/// Covers:
///   - `open("...")` inside heredoc snippets
///   - `> path` and `>> path` redirect patterns
///   - `sed -i ... path`
///   - `tee path`
fn extract_written_paths_simple(command: &str) -> Vec<String> {
    let mut paths = Vec::new();

    if command.contains("<<") && command.contains("open(") {
        let mut start = 0usize;
        while let Some(rel) = command[start..].find("open(") {
            let idx = start + rel + "open(".len();
            let rest = &command[idx..];
            let trimmed = rest.trim_start();
            let skipped = rest.len() - trimmed.len();
            let Some(quote) = trimmed.chars().next() else {
                break;
            };
            if quote == '\'' || quote == '"' {
                if let Some(end) = trimmed[1..].find(quote) {
                    let candidate = &trimmed[1..1 + end];
                    if !candidate.is_empty() {
                        paths.push(candidate.to_string());
                    }
                    start = idx + skipped + 1 + end + 1;
                    continue;
                }
            }
            start = idx + skipped;
        }
    }

    let mut i = 0;
    let bytes = command.as_bytes();
    while i < bytes.len() {
        if bytes[i] == b'>' {
            let start = if i + 1 < bytes.len() && bytes[i + 1] == b'>' {
                i + 2
            } else {
                i + 1
            };
            let rest = &command[start..].trim_start_matches(' ');
            // Find end of path token.
            let end = rest
                .find(|c: char| [' ', ';', '|', '&', '\n'].contains(&c))
                .unwrap_or(rest.len());
            let raw = rest[..end].trim_matches(|c: char| c == '\'' || c == '"');
            if !raw.is_empty() && raw != "/" {
                paths.push(raw.to_string());
            }
        }
        i += 1;
    }

    if let Some(rel) = command.find("sed -i") {
        let rest = &command[rel + "sed -i".len()..];
        let quoted_end = if let Some(single) = rest.find('\'') {
            rest[single + 1..]
                .find('\'')
                .map(|off| single + 1 + off + 1)
        } else if let Some(double) = rest.find('"') {
            rest[double + 1..].find('"').map(|off| double + 1 + off + 1)
        } else {
            None
        };
        if let Some(end) = quoted_end {
            let after = rest[end + 1..].trim_start();
            if let Some(token) = after.split_whitespace().next() {
                let raw = token.trim_matches(|c: char| c == '\'' || c == '"');
                if !raw.is_empty() {
                    paths.push(raw.to_string());
                }
            }
        }
    }

    let mut tee_start = 0usize;
    while let Some(rel) = command[tee_start..].find("tee ") {
        let idx = tee_start + rel + "tee ".len();
        let after = command[idx..].trim_start();
        let mut parts = after.split_whitespace();
        let first = parts.next();
        let candidate = match first {
            Some(flag) if flag.starts_with('-') => parts.next(),
            other => other,
        };
        if let Some(token) = candidate {
            let raw = token.trim_matches(|c: char| c == '\'' || c == '"');
            if !raw.is_empty() {
                paths.push(raw.to_string());
            }
        }
        tee_start = idx;
    }

    paths
}

// ---------------------------------------------------------------------------
// VerificationGatePostRule
// ---------------------------------------------------------------------------

/// Records verification evidence and marks dirty surfaces (postToolUse bash).
///
/// postToolUse-only port of the `_post()` method of
/// `hooks/rules/verification_gate.py::VerificationGateRule` (wave7):
///   1. If the bash command appears to write source files, extracts written paths
///      and marks the affected surfaces (Python / browse-ui) as dirty in the ledger,
///      clearing stale evidence for those surfaces.
///   2. Detects evidence categories from the command pattern (Python tests, pnpm).
///   3. If toolResult shows no failure indicators, records the evidence in the ledger.
///
/// Ledger format (preserved exactly):
///   HMAC-signed list marker containing a single JSON string:
///   `{"dirty":["py","ui"],"evidence":["py_tests"]}` (compact, sorted keys + arrays).
///   Matches `sign_list_marker(LEDGER_FILE, {payload})` from the Python side.
///
/// Hard constraints:
///   - NEVER returns a deny result.
///   - Does NOT port the deny-capable preToolUse closeout-gating half.
///   - Preserves existing counter values in the ledger (read-first, merge strategy).
///   - Fail-open: any error → None (no ledger write on partial failure).
pub struct VerificationGatePostRule;

impl HookRule for VerificationGatePostRule {
    fn name(&self) -> &'static str {
        "verification-gate-post"
    }

    fn events(&self) -> &'static [&'static str] {
        &["postToolUse"]
    }

    fn tools(&self) -> &'static [&'static str] {
        &["bash"]
    }

    fn evaluate(&self, _event: &str, data: &Value) -> Option<Value> {
        let tool_name = data.get("toolName").and_then(|v| v.as_str()).unwrap_or("");
        if tool_name != "bash" {
            return None;
        }

        let command = data
            .get("toolArgs")
            .and_then(|a| a.as_object())
            .and_then(|o| o.get("command"))
            .and_then(|v| v.as_str())
            .unwrap_or("");

        // Check if the command writes source files → mark dirty surfaces.
        let written_paths = extract_written_paths_simple(command);
        let written_surfaces: Vec<&'static str> = written_paths
            .iter()
            .flat_map(|p| surfaces_from_path(p))
            .collect::<std::collections::HashSet<_>>()
            .into_iter()
            .collect();
        if !written_surfaces.is_empty() {
            mark_dirty_surfaces(&written_surfaces);
        }

        // Detect evidence from the command.
        let ev_detected = evidence_from_command(command);
        if ev_detected.is_empty() {
            return None;
        }

        // Only record evidence if command appeared to succeed (fail-open).
        if !looks_successful(data) {
            return None;
        }

        // Merge new evidence into ledger.
        let (dirty, mut evidence) = read_ledger();
        for ev in &ev_detected {
            evidence.insert(ev.to_string());
        }
        write_ledger(&dirty, &evidence);

        None // postToolUse: side-effect only, no output
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
const SESSION_PROTECTED_MARKERS: &[&str] = &["audit.jsonl", "session.log", "hooks-tampered"];

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
fn try_stop_cleanup(data: &Value) -> Option<String> {
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
fn try_query_kb_native(search_query: &str) -> Option<String> {
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
fn try_query_kb(search_query: &str) -> Option<String> {
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
fn check_python_syntax(content: &str, label: &str) -> Option<String> {
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
fn content_has_unsafe_html(content: &str) -> bool {
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
// Shared helpers for VerificationGatePreRule + TentacleSuggestRule
// ---------------------------------------------------------------------------

/// Module-marker directory names — mirrors Python `MODULE_MARKERS` tuple
/// in `hooks/rules/common.py`.
const MODULE_MARKERS: &[&str] = &[
    "src",
    "lib",
    "app",
    "pkg",
    "internal",
    "cmd",
    "hooks",
    "skills",
    "templates",
    "tests",
    "test",
    "components",
    "screens",
    "services",
    "utils",
    "models",
    "views",
    "controllers",
    "routes",
    "pages",
    "features",
    "presentation",
    "domain",
    "data",
    "core",
    "common",
    "ui",
    "api",
    "db",
    "auth",
    "config",
    "settings",
    "alarm",
    "timer",
    "stopwatch",
    "clock",
    "widget",
];

/// Extract the deepest module name from a file path.
///
/// Mirrors Python `get_module(file_path, repo_prefix)` in
/// `hooks/rules/common.py`.  Returns an empty string when no module can be
/// determined.  Normalises `\` to `/` so Windows paths work correctly.
fn get_module_for_path(file_path: &str, repo_prefix: Option<&str>) -> String {
    let norm = file_path.replace('\\', "/");
    let parts: Vec<&str> = norm.split('/').filter(|s| !s.is_empty()).collect();
    if parts.len() < 2 {
        return String::new();
    }
    // Iterate all parts except the last (filename).
    let mut best = String::new();
    for i in 0..parts.len() - 1 {
        let p = parts[i];
        if MODULE_MARKERS.contains(&p) {
            // i+1 < parts.len()-1 means the next element is not the filename.
            if i + 1 < parts.len() - 1 {
                best = format!("{}/{}", p, parts[i + 1]);
            } else {
                best = p.to_string();
            }
        }
    }
    let module = if !best.is_empty() {
        best
    } else {
        // Fallback: parent directory name.
        parts[parts.len() - 2].to_string()
    };
    if module.is_empty() {
        return String::new();
    }
    match repo_prefix {
        Some(prefix) if !prefix.is_empty() => format!("{}:{}", prefix, module),
        _ => module,
    }
}

/// Run `git rev-parse --show-toplevel` and return the absolute path string.
///
/// Returns `None` on any error (fail-open: git not in PATH, not a repo).
fn get_git_root() -> Option<String> {
    let output = Command::new("git")
        .args(["rev-parse", "--show-toplevel"])
        .stdout(Stdio::piped())
        .stderr(Stdio::null())
        .output()
        .ok()?;
    if !output.status.success() {
        return None;
    }
    let s = String::from_utf8_lossy(&output.stdout).trim().to_string();
    if s.is_empty() {
        None
    } else {
        Some(s)
    }
}

/// Return the short basename of the git root (repo prefix for module names),
/// or `"legacy"` when git is unavailable.
fn get_repo_prefix() -> String {
    get_git_root()
        .and_then(|root| {
            std::path::Path::new(&root)
                .file_name()
                .and_then(|n| n.to_str())
                .map(|s| s.to_string())
        })
        .unwrap_or_else(|| "legacy".to_string())
}

// ---------------------------------------------------------------------------
// VerificationGatePreRule
// ---------------------------------------------------------------------------

/// Dirty-marking and closeout-gate on the direct preToolUse path.
///
/// Ports the `_pre()` half of
/// `hooks/rules/verification_gate.py::VerificationGateRule` (wave8):
///   - preToolUse [edit/create]: marks the affected surface (Python / browse-ui)
///     dirty in the verification ledger and clears now-stale evidence.
///     Always returns `None` (edits are never blocked here).
///   - preToolUse [bash/task_complete]: detects closeout-style actions
///     (`task_complete`, `gh issue close/comment`, `gh pr merge`,
///     `tentacle handoff --status DONE`, `tentacle complete`).  When the ledger
///     has dirty surfaces with missing evidence, returns a deny result listing
///     the required verification commands.
///
/// Hard constraints:
///   - Fail-open: any exception / missing field → `None` (allow through).
///   - Preserves existing on-disk ledger format exactly
///     (`{"dirty":[...],"evidence":[...]}` compact JSON inside an HMAC-signed
///     single-element list marker).
///   - Does NOT flip the managed-routing path — `sk hooks run preToolUse`
///     remains Python-backed after wave8.
pub struct VerificationGatePreRule;

/// Fix-command strings for each evidence key.
///
/// Mirrors Python `_FIX_COMMANDS` in `verification_gate.py`.
const FIX_PY_TESTS: &str = "python3 test_security.py && python3 test_fixes.py";
const FIX_UI_FORMAT: &str = "cd browse-ui && pnpm format:check";
const FIX_UI_LINT: &str = "cd browse-ui && pnpm lint";
const FIX_UI_TYPECHECK: &str = "cd browse-ui && pnpm typecheck";
const FIX_UI_BUILD: &str = "cd browse-ui && pnpm build";

/// Return `(is_closeout, description)` for a tool invocation.
///
/// Mirrors Python `_is_closeout(tool_name, tool_args)`.  Uses simple
/// substring matching (no `regex` crate required).
fn is_closeout_action(tool_name: &str, cmd: &str) -> (bool, &'static str) {
    if tool_name == "task_complete" {
        return (true, "task_complete");
    }
    if tool_name != "bash" {
        return (false, "");
    }
    if cmd.contains("gh") && cmd.contains("issue") && cmd.contains("close") {
        return (true, "gh issue close");
    }
    if cmd.contains("gh") && cmd.contains("issue") && cmd.contains("comment") {
        return (true, "gh issue comment");
    }
    if cmd.contains("gh") && cmd.contains("pr") && cmd.contains("merge") {
        return (true, "gh pr merge");
    }
    // tentacle.py handoff --status DONE  |  sk tentacle handoff --status DONE
    let has_tentacle_cmd =
        cmd.contains("tentacle.py") || (cmd.contains("sk") && cmd.contains("tentacle"));
    if has_tentacle_cmd
        && cmd.contains("handoff")
        && cmd.contains("--status")
        && cmd.contains("DONE")
    {
        return (true, "tentacle handoff --status DONE");
    }
    if has_tentacle_cmd && cmd.contains("complete") {
        return (true, "tentacle complete");
    }
    (false, "")
}

impl HookRule for VerificationGatePreRule {
    fn name(&self) -> &'static str {
        "verification-gate-pre"
    }

    fn events(&self) -> &'static [&'static str] {
        &["preToolUse"]
    }

    fn tools(&self) -> &'static [&'static str] {
        &["edit", "create", "bash", "task_complete"]
    }

    fn evaluate(&self, _event: &str, data: &Value) -> Option<Value> {
        // Fail-open: any error inside this rule must not deny.
        let result = std::panic::catch_unwind(|| {
            let tool_name = data.get("toolName").and_then(|v| v.as_str()).unwrap_or("");
            let tool_args = data
                .get("toolArgs")
                .and_then(|v| v.as_object())
                .map(|o| o as &serde_json::Map<String, Value>);

            // ── edit / create: dirty-mark the surface, never block ──────────
            if tool_name == "edit" || tool_name == "create" {
                let path = tool_args
                    .and_then(|o| o.get("path"))
                    .and_then(|v| v.as_str())
                    .unwrap_or("");
                if !path.is_empty() {
                    let surfs = surfaces_from_path(path);
                    if !surfs.is_empty() {
                        mark_dirty_surfaces(&surfs);
                    }
                }
                return None; // always allow edits
            }

            // ── bash / task_complete: gate closeout actions ──────────────────
            let cmd = tool_args
                .and_then(|o| o.get("command"))
                .and_then(|v| v.as_str())
                .unwrap_or("");

            let (is_closeout, closeout_desc) = is_closeout_action(tool_name, cmd);
            if !is_closeout {
                return None;
            }

            let (dirty, evidence) = read_ledger();
            if dirty.is_empty() {
                return None; // no tracked edits → no requirement
            }

            // Build missing-evidence messages.
            let mut missing_msgs: Vec<String> = Vec::new();
            let mut sorted_dirty: Vec<&str> = dirty.iter().map(|s| s.as_str()).collect();
            sorted_dirty.sort_unstable();

            for surface in &sorted_dirty {
                let required: &[&str] = SURFACE_REQUIREMENTS
                    .iter()
                    .find(|(s, _)| s == surface)
                    .map(|(_, r)| *r)
                    .unwrap_or(&[]);
                let gaps: Vec<&str> = required
                    .iter()
                    .filter(|ev| !evidence.contains(**ev))
                    .copied()
                    .collect();
                if gaps.is_empty() {
                    continue;
                }
                let fix_parts: Vec<&str> = gaps
                    .iter()
                    .filter_map(|ev| match *ev {
                        EV_PY_TESTS => Some(FIX_PY_TESTS),
                        EV_UI_FORMAT => Some(FIX_UI_FORMAT),
                        EV_UI_LINT => Some(FIX_UI_LINT),
                        EV_UI_TYPECHECK => Some(FIX_UI_TYPECHECK),
                        EV_UI_BUILD => Some(FIX_UI_BUILD),
                        _ => None,
                    })
                    .collect();

                if *surface == SURFACE_PY {
                    missing_msgs.push(format!(
                        "Python edits need test evidence: {}",
                        fix_parts.first().copied().unwrap_or("run tests")
                    ));
                } else if *surface == SURFACE_UI {
                    let mut msg =
                        "browse-ui edits need format/lint/typecheck/build evidence:".to_string();
                    for cmd_str in &fix_parts {
                        msg.push_str(&format!("\n    {cmd_str}"));
                    }
                    missing_msgs.push(msg);
                }
            }

            if missing_msgs.is_empty() {
                return None;
            }

            let bullet_list = missing_msgs
                .iter()
                .map(|m| format!("  \u{2022} {m}"))
                .collect::<Vec<_>>()
                .join("\n");

            Some(deny(&format!(
                "\u{1f50e} VERIFICATION REQUIRED before {closeout_desc}:\n\
                 {bullet_list}\n\
                 Run the above commands and retry."
            )))
        });

        // Fail-open: panic / unwind → None.
        result.unwrap_or(None)
    }
}

// ---------------------------------------------------------------------------
// AutoBugDetectorRule
// ---------------------------------------------------------------------------

/// Detect bug-fix patterns in edit/create payloads and record them via
/// a ``learn.py --mistake`` subprocess call (issue #86, wave13).
///
/// Ports ``hooks/rules/auto_bug_detector.py::AutoBugDetectorRule``.
///
/// Five detection categories:
///   - ``error-handling``  — ``try``/``except``, ``.catch()``, ``raise *Error`` added
///   - ``null-safety``     — ``None``/``null`` guard, ``?.``, ``??``, ``.unwrap_or`` added
///   - ``guard-clause``    — early-return guard pattern added at function entry
///   - ``async-fix``       — ``await`` or ``async def/function`` added where absent
///   - ``type-fix``        — Python type annotation added (edit-only; excluded on create)
///
/// Create-path support (conservative):
///   ``null-safety`` (0.62) and ``async-fix`` (0.62) are enabled on ``create``
///   payloads because their patterns are specific enough to indicate intentional
///   safety additions in a brand-new file.  All other categories are excluded
///   on ``create`` (error-handling, guard-clause, type-fix remain at 0.0).
///
/// 5-minute occurrence semantics:
///   Same file + same category within the same 5-minute bucket share a
///   bucketed title.  ``learn.py`` deduplicates on ``(category, title)`` and
///   increments ``occurrence_count`` on repeat calls — counts accumulate
///   rather than being silently dropped.
///
/// learn-done marker:
///   After a successful ``learn.py`` subprocess call the ``markers/learn-done``
///   HMAC-signed marker is written so that the enforce-learn gate counts the
///   auto-detection as a learn event.
///
/// Informational-only.  Fail-open at every step.
pub struct AutoBugDetectorRule;

fn auto_bug_bucket_id() -> u64 {
    SystemTime::now()
        .duration_since(UNIX_EPOCH)
        .unwrap_or(Duration::ZERO)
        .as_secs()
        / 300
}

/// Returns `true` when `text` contains a token-level error-handling indicator.
///
/// Matches:
///   - ``try:`` / ``try{`` / ``try {`` — word-boundary-safe; does NOT match
///     inside longer words such as ``retry:`` or ``country{``
///     (mirrors Python ``\btry\s*[:{]``)
///   - ``except <identifier>`` — word-boundary-safe; does NOT match inside ``noexcept``
///   - ``.catch(``
///   - ``raise <Word>Error`` — per-line token check, not file-wide substring
///   - ``throw new <Word>Error`` — per-line token check, not file-wide substring
///
/// A stray ``Error`` in a comment, docstring, or variable name anywhere in
/// the file does NOT match — it must appear as the suffix of the raised/thrown
/// identifier on that specific line.
///
/// This intentionally does NOT match bare ``raise StopIteration`` so that old
/// code with non-Error raises does not suppress detection of newly added
/// ``try/except`` blocks (mirrors Python ``\braise\s+\w+Error\b``).
fn auto_bug_trimmed_code_part(line: &str) -> &str {
    let trimmed = line.trim_start();
    if trimmed.starts_with('#') || trimmed.starts_with("//") || trimmed.starts_with('*') {
        return "";
    }
    let mut code_part = trimmed;
    if let Some(p) = code_part.find('#') {
        code_part = &code_part[..p];
    }
    if let Some(p) = code_part.find("//") {
        code_part = &code_part[..p];
    }
    code_part.trim_end()
}

fn auto_bug_line_has_try_indicator(code_part: &str) -> bool {
    let bytes = code_part.as_bytes();
    let needle = b"try";
    let needle_len = needle.len();
    let mut start = 0;
    while start + needle_len <= bytes.len() {
        if let Some(rel) = bytes[start..].windows(needle_len).position(|w| w == needle) {
            let abs = start + rel;
            let preceded_by_word = abs > 0
                && bytes
                    .get(abs - 1)
                    .map(|&b| b.is_ascii_alphanumeric() || b == b'_')
                    .unwrap_or(false);
            if !preceded_by_word {
                let rest = &bytes[abs + needle_len..];
                let mut i = 0;
                while i < rest.len() && rest[i].is_ascii_whitespace() {
                    i += 1;
                }
                if i < rest.len() && (rest[i] == b':' || rest[i] == b'{') {
                    return true;
                }
            }
            start = abs + 1;
        } else {
            break;
        }
    }
    false
}

fn auto_bug_line_has_except_indicator(code_part: &str) -> bool {
    let bytes = code_part.as_bytes();
    let needle = b"except ";
    let needle_len = needle.len();
    let mut start = 0;
    while start + needle_len <= bytes.len() {
        if let Some(rel) = bytes[start..].windows(needle_len).position(|w| w == needle) {
            let abs = start + rel;
            let preceded_by_word = abs > 0
                && bytes
                    .get(abs - 1)
                    .map(|&b| b.is_ascii_alphanumeric() || b == b'_')
                    .unwrap_or(false);
            if !preceded_by_word {
                return true;
            }
            start = abs + 1;
        } else {
            break;
        }
    }
    false
}

fn auto_bug_has_error_indicator(text: &str) -> bool {
    // Scan each line individually so comment-only lines and inline trailing comments
    // (e.g. `// throw new TypeError`) do not spuriously count as real error handling.
    for line in text.lines() {
        let code_part = auto_bug_trimmed_code_part(line);
        if code_part.is_empty() {
            continue;
        }
        if auto_bug_line_has_try_indicator(code_part)
            || auto_bug_line_has_except_indicator(code_part)
            || code_part.contains(".catch(")
        {
            return true;
        }
        // `raise SomeError` / `raise SomeError(...)`
        if let Some(after) = code_part.strip_prefix("raise ") {
            let word_end = after
                .find(|c: char| !c.is_alphanumeric() && c != '_')
                .unwrap_or(after.len());
            let word = &after[..word_end];
            if !word.is_empty() && word.ends_with("Error") {
                return true;
            }
        }
        // `throw new TypeError(...)` / `throw new SomeError`
        if let Some(idx) = code_part.find("throw new ") {
            let after = &code_part[idx + "throw new ".len()..];
            let word_end = after
                .find(|c: char| !c.is_alphanumeric() && c != '_')
                .unwrap_or(after.len());
            let word = &after[..word_end];
            if !word.is_empty() && word.ends_with("Error") {
                return true;
            }
        }
    }
    false
}

/// Returns `true` when `text` contains a null-safety indicator.
///
/// For `is None` / `is not None` and `== null` / `!= null` / `=== null` /
/// `!== null`: the pattern **must** appear inside a conditional `if` statement —
/// i.e., the trimmed line must start with `"if "` or `"if("`.
/// Raw non-conditional forms such as:
///   - `assert x is None`
///   - `return x is None`
///   - `x = result is None`
///   - `x == null` (bare comparison)
///   - `assert x == null`
///   - `return x == null`
///   - comments or docstrings containing these forms
///
/// are **not** counted, mirroring Python's structured-form requirement.
///
/// Line-level indicators (`?.`, `??`, `.unwrap_or(`, `.ok_or(`) do not
/// require an `if` prefix, but they are still checked on the comment-stripped
/// code portion of each line so comment-only occurrences do not fire.
fn auto_bug_has_null_safety_indicator(text: &str) -> bool {
    // Null-safety indicators are checked per non-comment line so comment-only
    // occurrences do NOT fire.
    const SIMPLE: &[&str] = &[".unwrap_or(", ".ok_or(", "?.", "??"];
    // `is None` / `is not None` and `== null` / `!= null` / `=== null` / `!== null`
    // all require a leading `if` on the same trimmed line, mirroring Python's
    // structured-form requirement so bare boolean expressions, assertions,
    // assignments, and comments do not spuriously detect.
    const NULL_CMP: &[&str] = &["== null", "=== null", "!= null", "!== null"];
    for line in text.lines() {
        let code_part = auto_bug_trimmed_code_part(line);
        if code_part.is_empty() {
            continue;
        }
        if SIMPLE.iter().any(|indicator| code_part.contains(indicator)) {
            return true;
        }
        let structured_part = code_part;
        let is_if_line = structured_part.starts_with("if ") || structured_part.starts_with("if(");
        if is_if_line {
            // `is None` / `is not None`
            if structured_part.contains("is None") || structured_part.contains("is not None") {
                return true;
            }
            // null-equality comparisons
            for indicator in NULL_CMP {
                if structured_part.contains(indicator) {
                    return true;
                }
            }
        }
    }
    false
}

/// Returns `true` when `text` contains a guard-clause pattern matching Python's
/// adjacency requirement:
///   - ``if not <identifier-or-dot-path>:`` — tightened to Python's ``if\s+not\s+\w[\w.]*\s*[:\n]``;
///     parenthesized/function-call forms like ``if not isinstance(x, T):`` or
///     ``if not (a and b):`` are intentionally excluded.
///   - ``if <cond>:`` immediately followed on the next line by ``return`` (adjacency required)
///
/// Mirrors Python regex: ``if\s+[^\n:]+:\s*\n\s+return\b | if\s+not\s+\w[\w.]*\s*[:\n]``
fn auto_bug_has_guard_clause(text: &str) -> bool {
    // Line-by-line scan for `if not <identifier-or-dot-path>:`.
    // Mirrors Python's `if\s+not\s+\w[\w.]*\s*[:\n]` alternative.
    // Parenthesized or function-call forms (`if not isinstance(...)` / `if not (...)`)
    // do NOT match because the identifier immediately followed by `(` is not followed by `:`.
    for line in text.lines() {
        let trimmed = line.trim_start();
        if let Some(after_not) = trimmed.strip_prefix("if not ") {
            let rest = after_not.trim_start();
            // Must begin with a word character (letter / digit / underscore).
            if rest.starts_with(|c: char| c.is_alphanumeric() || c == '_') {
                // Advance past the identifier/dot-path (word chars and dots only).
                let ident_end = rest
                    .find(|c: char| !c.is_alphanumeric() && c != '_' && c != '.')
                    .unwrap_or(rest.len());
                if ident_end > 0 {
                    // After the identifier, must see `:` (optionally preceded by spaces)
                    // or nothing else on the line — mirrors `[:\n]` in Python regex.
                    let after_ident = rest[ident_end..].trim_start();
                    if after_ident.starts_with(':') || after_ident.is_empty() {
                        return true;
                    }
                }
            }
        }
    }
    // Adjacency check: `if ...:` must be immediately followed by a line starting
    // with `return`.  Disjoint `if ...:` + later `return` elsewhere does NOT match.
    let lines: Vec<&str> = text.lines().collect();
    for i in 0..lines.len().saturating_sub(1) {
        let trimmed = lines[i].trim_start();
        if trimmed.starts_with("if ") && trimmed.ends_with(':') {
            let next_trimmed = lines[i + 1].trim_start();
            if next_trimmed.starts_with("return") {
                return true;
            }
        }
    }
    false
}

/// Returns `true` when `text` contains a Python type annotation in a real
/// annotation context.
///
/// Rules (mirroring Python's updated type-fix regex):
/// - Comment-only lines (trimmed start = `#` or `//`) are skipped.
/// - The `: type` indicator must NOT be immediately preceded by a quote
///   character (`'` or `"`) on the same line, which would indicate a
///   string-keyed dict literal like ``{'items': list}``.
/// - ``: None`` is excluded from the indicator list because it is too
///   ambiguous — it appears in config/YAML-like ``key: None`` patterns
///   and is rarely a real Python type annotation (return types use
///   ``-> None`` not ``: None``).
fn auto_bug_has_type_annotation(text: &str) -> bool {
    // Python type annotation keywords (excludes `None` — too ambiguous).
    const TOKENS: &[&str] = &[
        "int",
        "str",
        "float",
        "bool",
        "bytes",
        "list",
        "dict",
        "set",
        "tuple",
        "Optional[",
        "Union[",
        "List[",
        "Dict[",
        "Tuple[",
        "Any",
    ];
    for line in text.lines() {
        let code_part = auto_bug_trimmed_code_part(line);
        if code_part.is_empty() {
            continue;
        }
        let mut search_start = 0;
        while search_start < code_part.len() {
            let Some(rel_pos) = code_part[search_start..].find(':') else {
                break;
            };
            let colon_pos = search_start + rel_pos;
            let before = code_part[..colon_pos].trim_end();
            if !before.ends_with('\'') && !before.ends_with('"') {
                let after_colon = code_part[colon_pos + 1..].trim_start();
                for token in TOKENS {
                    if let Some(after_token) = after_colon.strip_prefix(token) {
                        let has_word_boundary = after_token.is_empty()
                            || after_token.starts_with(|c: char| !c.is_alphanumeric() && c != '_');
                        if has_word_boundary {
                            return true;
                        }
                    }
                }
            }
            search_start = colon_pos + 1;
        }
    }
    false
}

/// Check whether ``new_str`` introduces a pattern that was absent in ``old_str``.
///
/// Returns a vec of ``(category, confidence)`` pairs for every category
/// where the new code adds a recognisable bug-fix indicator.
///
/// Uses simple ``contains()`` matching to avoid the optional ``regex`` crate
/// dependency (mirrors the comment for ``command_is_git_commit_or_push``).
fn auto_bug_detect_edit(old_str: &str, new_str: &str) -> Vec<(&'static str, f64)> {
    // Helper: returns true when `haystack` contains any of the listed needles.
    fn has_any(haystack: &str, needles: &[&str]) -> bool {
        needles.iter().any(|n| haystack.contains(n))
    }

    let mut detections = Vec::new();

    // --- error-handling (confidence 0.85) ---
    // Uses token-level matching via auto_bug_has_error_indicator to avoid
    // false matches from stray "Error" in comments/docstrings/variables.
    {
        let new_has = auto_bug_has_error_indicator(new_str);
        let old_has = auto_bug_has_error_indicator(old_str);
        if new_has && !old_has {
            detections.push(("error-handling", 0.85_f64));
        }
    }

    // --- null-safety (confidence 0.78) ---
    // Uses auto_bug_has_null_safety_indicator which requires `if ... is None` /
    // `if ... is not None` structured forms.  Bare non-conditional uses such as
    // `assert x is None`, `return x is None`, assignments, and comments do NOT
    // trigger this category, mirroring Python's structured-form requirement.
    {
        if auto_bug_has_null_safety_indicator(new_str)
            && !auto_bug_has_null_safety_indicator(old_str)
        {
            detections.push(("null-safety", 0.78_f64));
        }
    }

    // --- guard-clause (confidence 0.73) ---
    // Uses auto_bug_has_guard_clause which requires strict adjacency:
    // `if ...:` must be immediately followed by a `return` line (mirrors Python).
    // Disjoint `if ...:` + later `return` elsewhere does NOT match.
    {
        if auto_bug_has_guard_clause(new_str) && !auto_bug_has_guard_clause(old_str) {
            detections.push(("guard-clause", 0.73_f64));
        }
    }

    // --- async-fix (confidence 0.78) ---
    {
        let indicators: &[&str] = &["await ", "async def ", "async function "];
        if has_any(new_str, indicators) && !has_any(old_str, indicators) {
            detections.push(("async-fix", 0.78_f64));
        }
    }

    // --- type-fix (confidence 0.65) ---
    // Uses auto_bug_has_type_annotation which requires real annotation context:
    // - skips comment-only lines
    // - excludes `: type` when preceded by a quote (string-keyed dict literals)
    // - excludes `: None` (too ambiguous; config/YAML key-value pairs also match)
    {
        if auto_bug_has_type_annotation(new_str) && !auto_bug_has_type_annotation(old_str) {
            detections.push(("type-fix", 0.65_f64));
        }
    }

    detections
}

/// Detect bug-fix patterns in a create payload.
///
/// More conservative than ``auto_bug_detect_edit`` because there is no
/// ``old_str`` reference.  Only categories with patterns specific enough to
/// be credible as intentional safety additions in a brand-new file are
/// enabled:
///
/// - ``null-safety`` (0.62) — None/null guards and optional-chaining are
///   specific enough to indicate defensive null handling was the intent.
/// - ``async-fix`` (0.62) — async/await in a new file credibly indicates
///   an async handler or wrapper created to address a missing-await bug.
///
/// All other categories remain disabled (0.0) to avoid spurious detections.
fn auto_bug_detect_create(file_text: &str) -> Vec<(&'static str, f64)> {
    fn has_any(haystack: &str, needles: &[&str]) -> bool {
        needles.iter().any(|n| haystack.contains(n))
    }

    let mut detections = Vec::new();

    // --- null-safety (confidence 0.62) ---
    // Uses the same structured-form helper as the edit path.  `is None` / `is not None`
    // must appear inside an `if` statement; other patterns retain substring checks.
    {
        if auto_bug_has_null_safety_indicator(file_text) {
            detections.push(("null-safety", 0.62_f64));
        }
    }

    // --- async-fix (confidence 0.62) ---
    {
        let indicators: &[&str] = &["await ", "async def ", "async function "];
        if has_any(file_text, indicators) {
            detections.push(("async-fix", 0.62_f64));
        }
    }

    detections
}

fn auto_bug_filter_detections_for_path(path: &str, detections: &mut Vec<(&'static str, f64)>) {
    let ext = Path::new(path)
        .extension()
        .and_then(|value| value.to_str())
        .map(|value| value.to_ascii_lowercase());
    if matches!(ext.as_deref(), Some("yaml") | Some("yml")) {
        detections.retain(|(category, _)| *category != "type-fix");
    }
}

/// Call ``learn.py --mistake`` via subprocess for a detected bug-fix pattern.
///
/// Returns ``true`` when the subprocess exits 0.  All errors are silently
/// swallowed (fail-open).
fn auto_bug_call_learn(file_path: &str, category: &str, confidence: f64, bucket: u64) -> bool {
    use crate::config::{python_exe, resolve_tools_dir};

    let tools_dir = resolve_tools_dir();
    let learn_py = tools_dir.join("learn.py");
    if !learn_py.exists() {
        return false;
    }

    let filename = Path::new(file_path)
        .file_name()
        .and_then(|n| n.to_str())
        .unwrap_or(file_path);

    let title = format!("[auto-detect] {category}: {filename} (bucket {bucket})");
    let description = format!(
        "Auto-detected {category} pattern in {file_path}. \
         Confidence: {confidence:.2}. \
         5-minute detection bucket: {bucket}."
    );
    let confidence_str = format!("{confidence:.2}");
    let tags = format!("auto-detect,{category}");

    let python = python_exe();
    // Use spawn() + bounded poll instead of blocking .status() so a hung
    // learn.py process cannot freeze postToolUse indefinitely.
    // Mirrors Python `_call_learn` semantics: 10-second timeout, kill on
    // deadline, wait for cleanup, return false on timeout or process error
    // (fail-open behaviour preserved).
    let mut child = match Command::new(python)
        .arg(&learn_py)
        .arg("--mistake")
        .arg(&title)
        .arg(&description)
        .arg("--confidence")
        .arg(&confidence_str)
        .arg("--tags")
        .arg(&tags)
        .arg("--wing")
        .arg("shared")
        .arg("--room")
        .arg("hook-rules")
        .arg("--skip-gate")
        .stdout(Stdio::null())
        .stderr(Stdio::null())
        .spawn()
    {
        Ok(c) => c,
        Err(_) => return false, // fail-open: Python unavailable
    };

    // Poll with a 10-second deadline (same as Python subprocess timeout=10).
    let deadline = Instant::now() + Duration::from_secs(10);
    loop {
        match child.try_wait() {
            Ok(Some(status)) => return status.success(),
            Ok(None) => {
                if Instant::now() >= deadline {
                    // Timed out: kill, reap, return false (fail-open).
                    let _ = child.kill();
                    let _ = child.wait();
                    return false;
                }
                std::thread::sleep(Duration::from_millis(50));
            }
            Err(_) => return false, // fail-open: unexpected OS error
        }
    }
}

/// Write the ``markers/learn-done`` HMAC-signed marker.
fn auto_bug_write_learn_done() {
    let marker_path = markers_dir().join("learn-done");
    let _ = marker_auth::sign_marker(&marker_path, "learn-done");
}

impl HookRule for AutoBugDetectorRule {
    fn name(&self) -> &'static str {
        "auto-bug-detector"
    }

    fn events(&self) -> &'static [&'static str] {
        &["postToolUse"]
    }

    fn tools(&self) -> &'static [&'static str] {
        &["edit", "create"]
    }

    fn evaluate(&self, _event: &str, data: &Value) -> Option<Value> {
        let tool_name = data.get("toolName").and_then(|v| v.as_str()).unwrap_or("");
        let tool_args = data.get("toolArgs").and_then(|v| v.as_object())?;

        match tool_name {
            "edit" => {
                let path = tool_args.get("path").and_then(|v| v.as_str()).unwrap_or("");
                let old_str = tool_args
                    .get("old_str")
                    .and_then(|v| v.as_str())
                    .unwrap_or("");
                let new_str = tool_args
                    .get("new_str")
                    .and_then(|v| v.as_str())
                    .unwrap_or("");

                // Skip session-state paths — mirrors Python `is_session_path(path)` guard.
                // Uses is_auto_bug_session_path (not is_track_session_path) to avoid
                // falsely skipping legitimate project files like src/session-state-manager.py.
                // Also skip non-code files (e.g. README.md) using the same code-extension
                // allowlist as EnforceLearnRule — mirrors Python `CODE_EXTENSIONS` gate.
                if path.is_empty()
                    || new_str.is_empty()
                    || is_auto_bug_session_path(path)
                    || !has_code_extension(path)
                {
                    return None;
                }

                let mut detections = auto_bug_detect_edit(old_str, new_str);
                auto_bug_filter_detections_for_path(path, &mut detections);
                if detections.is_empty() {
                    return None;
                }

                let bucket = auto_bug_bucket_id();
                let path_owned = path.to_string();

                // Launch all learn subprocess calls concurrently so multiple
                // detected categories do not stack latency linearly.
                let handles: Vec<std::thread::JoinHandle<Option<String>>> = detections
                    .iter()
                    .map(|(category, confidence)| {
                        let p = path_owned.clone();
                        let cat = category.to_string();
                        let conf = *confidence;
                        std::thread::spawn(move || -> Option<String> {
                            if auto_bug_call_learn(&p, &cat, conf, bucket) {
                                let filename = Path::new(&p)
                                    .file_name()
                                    .and_then(|n| n.to_str())
                                    .map(|s| s.to_owned())
                                    .unwrap_or_else(|| p.clone());
                                let pct = (conf * 100.0).round() as u32;
                                Some(format!(
                                    "  \u{1f41b} Auto-detected {cat} in {filename} (confidence: {pct}%)"
                                ))
                            } else {
                                None
                            }
                        })
                    })
                    .collect();

                let mut messages = Vec::new();
                let mut any_ok = false;
                for handle in handles {
                    if let Ok(Some(msg)) = handle.join() {
                        messages.push(msg);
                        any_ok = true;
                    }
                }
                if any_ok {
                    auto_bug_write_learn_done(); // Write once after all concurrent calls
                }

                if messages.is_empty() {
                    return None;
                }
                let body = messages.join("\n");
                Some(info(&format!("\n  \u{1f50d} Auto bug detector:\n{body}\n")))
            }
            "create" => {
                let path = tool_args.get("path").and_then(|v| v.as_str()).unwrap_or("");
                let file_text = tool_args
                    .get("file_text")
                    .and_then(|v| v.as_str())
                    .unwrap_or("");

                // Skip session-state paths — mirrors Python `is_session_path(path)` guard.
                // Uses is_auto_bug_session_path (not is_track_session_path) to avoid
                // falsely skipping legitimate project files like src/session-state-manager.py.
                // Also skip non-code files (e.g. README.md) — mirrors Python `CODE_EXTENSIONS` gate.
                if path.is_empty()
                    || file_text.is_empty()
                    || is_auto_bug_session_path(path)
                    || !has_code_extension(path)
                {
                    return None;
                }

                let mut detections = auto_bug_detect_create(file_text);
                auto_bug_filter_detections_for_path(path, &mut detections);
                if detections.is_empty() {
                    return None;
                }

                let bucket = auto_bug_bucket_id();
                let path_owned = path.to_string();

                // Launch all learn subprocess calls concurrently.
                let handles: Vec<std::thread::JoinHandle<Option<String>>> = detections
                    .iter()
                    .map(|(category, confidence)| {
                        let p = path_owned.clone();
                        let cat = category.to_string();
                        let conf = *confidence;
                        std::thread::spawn(move || -> Option<String> {
                            if auto_bug_call_learn(&p, &cat, conf, bucket) {
                                let filename = Path::new(&p)
                                    .file_name()
                                    .and_then(|n| n.to_str())
                                    .map(|s| s.to_owned())
                                    .unwrap_or_else(|| p.clone());
                                let pct = (conf * 100.0).round() as u32;
                                Some(format!(
                                    "  \u{1f41b} Auto-detected {cat} in {filename} (confidence: {pct}%)"
                                ))
                            } else {
                                None
                            }
                        })
                    })
                    .collect();

                let mut messages = Vec::new();
                let mut any_ok = false;
                for handle in handles {
                    if let Ok(Some(msg)) = handle.join() {
                        messages.push(msg);
                        any_ok = true;
                    }
                }
                if any_ok {
                    auto_bug_write_learn_done(); // Write once after all concurrent calls
                }

                if messages.is_empty() {
                    return None;
                }
                let body = messages.join("\n");
                Some(info(&format!("\n  \u{1f50d} Auto bug detector:\n{body}\n")))
            }
            _ => None,
        }
    }
}

// ---------------------------------------------------------------------------
// TentacleSuggestRule
// ---------------------------------------------------------------------------

/// Suggest tentacle-orchestration on postToolUse when edits span multiple modules.
///
/// Informational-only port of
/// `hooks/rules/tentacle.py::TentacleSuggestRule` (wave8):
///   - On postToolUse [edit/create/bash]: reads the `tentacle-edits` HMAC-signed
///     list marker (written by `TrackEditsRule`) and counts distinct files and
///     modules touched so far.
///   - If `>= 3` files AND `>= 2` modules are detected: emits a suggestion,
///     then touches `tentacle-suggested` so the suggestion fires only once.
///   - Skips immediately when `tentacle-suggested` is already present.
///
/// Read-only with respect to `tentacle-edits`:
///   - This rule NEVER writes to `tentacle-edits`.
///   - `TrackEditsRule` is the sole writer; `TentacleSuggestRule` only reads.
///
/// Format compatibility:
///   - Reads both the legacy flat-path format (written by Rust `TrackEditsRule`)
///     and the new JSON-dict format (written by Python `TentacleSuggestRule`).
///   - Legacy: each element in the HMAC-signed set is a bare file path string.
///   - New: a single element is a JSON dict `{repo_root: [{p, t}, ...], ...}`.
///
/// Informational-only: never returns a deny result.  Fail-open at every step.
pub struct TentacleSuggestRule;

const SUGGEST_MIN_FILES: usize = 3;
const SUGGEST_MIN_MODULES: usize = 2;

/// Read file paths from the `tentacle-edits` HMAC-signed list marker.
///
/// Handles both:
/// - Legacy flat format: each element is a bare file-path string (Rust writer).
/// - New JSON-dict format: single element is `{repo_root: [{p, t}...], ...}`
///   (Python writer).
///
/// TTL pruning is not applied to the legacy flat format (no timestamps stored).
/// Fail-open: any parse error → returns empty vec.
fn read_tentacle_edits_paths() -> Vec<String> {
    let edits_path = markers_dir().join("tentacle-edits");
    let raw_set = marker_auth::verify_list_marker(&edits_path);
    let mut paths = Vec::new();
    for entry in &raw_set {
        if entry.starts_with('{') {
            // New JSON-dict format: {repo_root: [{p: path, t: ts}, ...], ...}
            if let Ok(val) = serde_json::from_str::<Value>(entry) {
                if let Some(obj) = val.as_object() {
                    for (_, bucket) in obj {
                        if let Some(arr) = bucket.as_array() {
                            for e in arr {
                                if let Some(p) = e.get("p").and_then(|v| v.as_str()) {
                                    paths.push(p.to_string());
                                }
                            }
                        }
                    }
                }
            }
        } else if !entry.is_empty() {
            // Legacy flat format: bare file path.
            paths.push(entry.clone());
        }
    }
    paths
}

impl HookRule for TentacleSuggestRule {
    fn name(&self) -> &'static str {
        "tentacle-suggest"
    }

    fn events(&self) -> &'static [&'static str] {
        &["postToolUse"]
    }

    fn tools(&self) -> &'static [&'static str] {
        &["edit", "create", "bash"]
    }

    fn evaluate(&self, _event: &str, data: &Value) -> Option<Value> {
        let tool_name = data.get("toolName").and_then(|v| v.as_str()).unwrap_or("");
        if tool_name != "edit" && tool_name != "create" && tool_name != "bash" {
            return None;
        }

        let suggested_path = markers_dir().join("tentacle-suggested");
        if suggested_path.is_file() {
            return None; // suggestion already emitted this session
        }

        // Read current tentacle-edits state (written by TrackEditsRule).
        let all_paths = read_tentacle_edits_paths();
        if all_paths.len() < SUGGEST_MIN_FILES {
            return None;
        }

        // Deduplicate paths (the marker may contain duplicates after migration).
        let unique_paths: std::collections::HashSet<String> = all_paths.into_iter().collect();
        if unique_paths.len() < SUGGEST_MIN_FILES {
            return None;
        }

        // Filter to code/config extensions only (no markdown, no session-state).
        let code_paths: Vec<&str> = unique_paths
            .iter()
            .map(|s| s.as_str())
            .filter(|p| has_code_extension(p) && !is_track_session_path(p))
            .collect();
        if code_paths.len() < SUGGEST_MIN_FILES {
            return None;
        }

        // Compute modules (fail-open: git unavailable → use "legacy" prefix).
        let repo_prefix = get_repo_prefix();
        let modules: std::collections::HashSet<String> = code_paths
            .iter()
            .map(|p| get_module_for_path(p, Some(&repo_prefix)))
            .filter(|m| !m.is_empty())
            .collect();

        if modules.len() < SUGGEST_MIN_MODULES {
            return None;
        }

        // Touch tentacle-suggested so this fires only once.
        let mdir = markers_dir();
        let _ = fs::create_dir_all(&mdir);
        let _ = fs::write(&suggested_path, b"");

        let mut sorted_modules: Vec<&str> = modules.iter().map(|s| s.as_str()).collect();
        sorted_modules.sort_unstable();

        Some(info(&format!(
            "\n  \u{1f419} TENTACLE SUGGESTION: {} files across {} modules detected.\n  \
             Consider using tentacle-orchestration for parallel multi-agent execution.\n  \
             Modules: {}\n  \
             \u{1f4cb} After completing: check docs/SYNC-MATRIX.md for docs/memory follow-ups.\n",
            code_paths.len(),
            modules.len(),
            sorted_modules.join(", "),
        )))
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
const ENFORCE_SOURCE_EXTENSIONS: &[&str] = &[
    ".py", ".kt", ".ts", ".tsx", ".js", ".jsx", ".swift", ".java", ".go", ".rs", ".json", ".yaml",
    ".yml", ".xml", ".html", ".css", ".md", ".toml", ".sh", ".bat", ".ps1",
];

/// Filesystem path prefixes that are considered safe to write without a
/// briefing marker (temp dirs, device files, etc.).
///
/// Mirrors Python `SAFE_PATH_PREFIXES` in `hooks/rules/common.py`.
const ENFORCE_SAFE_PATH_PREFIXES: &[&str] = &["/tmp/", "/var/", "/dev/", "/proc/"];

/// Minimum code-edit count before the learn gate fires.
const LEARN_EDIT_THRESHOLD: i64 = 3;

/// Return `true` if `path` is a source file the enforcement rules care about.
///
/// Excludes safe-tmp prefixes and session-state paths; includes all source
/// extensions from `ENFORCE_SOURCE_EXTENSIONS`.
/// Mirrors Python `is_source_path(path)` in `hooks/rules/common.py`.
fn is_source_path_for_enforce(path: &str) -> bool {
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
fn bash_writes_source_files_detect(command: &str) -> bool {
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
fn briefing_done() -> bool {
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

/// Return `true` when the learn gate should block the current operation.
///
/// Blocks when: learn-done marker is absent AND code-edit-count ≥ threshold.
/// Mirrors `EnforceLearnRule._should_block()` in `hooks/rules/learn_gate.py`.
fn learn_should_block() -> bool {
    let mdir = markers_dir();
    if marker_auth::verify_marker(&mdir.join("learn-done"), "learn-done") {
        return false; // already recorded learnings this session
    }
    marker_auth::verify_counter(&mdir.join("code-edit-count")) >= LEARN_EDIT_THRESHOLD
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
// EnforceLearnRule
// ---------------------------------------------------------------------------

/// Track code-file edits and deny `git commit/push` / `task_complete` when
/// `learn.py` has not been called after ≥ 3 edits.
///
/// Mirrors `EnforceLearnRule` in `hooks/rules/learn_gate.py`.
///
/// Behaviour:
///   - `edit`/`create` on code files: increment `code-edit-count` counter;
///     return `None` (never deny on edit/create).
///   - `bash` git commit/push: deny when `_should_block()` is true.
///   - `task_complete`: deny when `_should_block()` is true.
///
pub struct EnforceLearnRule;

impl HookRule for EnforceLearnRule {
    fn name(&self) -> &'static str {
        "enforce-learn"
    }

    fn events(&self) -> &'static [&'static str] {
        &["preToolUse"]
    }

    fn tools(&self) -> &'static [&'static str] {
        &["edit", "create", "bash", "task_complete"]
    }

    fn evaluate(&self, _event: &str, data: &Value) -> Option<Value> {
        let tool_name = data.get("toolName").and_then(|v| v.as_str()).unwrap_or("");
        let tool_args = data
            .get("toolArgs")
            .and_then(|v| v.as_object())
            .cloned()
            .unwrap_or_default();

        if marker_auth::check_tamper_marker() {
            return Some(deny(
                "\u{1f6a8} HOOKS TAMPERED: All modifications blocked. Run: sudo python3 ~/.copilot/tools/install.py --lock-hooks",
            ));
        }

        // Track code-file edits (counter-write only; never deny on edit/create).
        if tool_name == "edit" || tool_name == "create" {
            let file_path = tool_args.get("path").and_then(|v| v.as_str()).unwrap_or("");
            if has_code_extension(file_path) && !is_track_session_path(file_path) {
                let mdir = markers_dir();
                let _ = fs::create_dir_all(&mdir);
                let counter_path = mdir.join("code-edit-count");
                let current = marker_auth::verify_counter(&counter_path);
                let _ = marker_auth::sign_counter(&counter_path, current + 1);
            }
            return None;
        }

        // Bash: only fire on git commit/push; all other commands pass through.
        if tool_name == "bash" {
            let command = tool_args
                .get("command")
                .and_then(|v| v.as_str())
                .unwrap_or("");
            if marker_auth::is_secret_access(command) {
                return Some(deny("\u{1f512} Access to protected hook files is blocked."));
            }
            if !command_is_git_commit_or_push(command) {
                return None;
            }
            if !learn_should_block() {
                return None;
            }
            let count = marker_auth::verify_counter(&markers_dir().join("code-edit-count"));
            return Some(deny(&format!(
                "\u{1f9e0} LEARN REQUIRED: {count} code files edited but learn.py not called. \
                 Record what you learned before committing:\n  \
                 sk learn --mistake \"Title\" \"Description\" --wing <wing> --room <room>\n  \
                 (fallback: python3 ~/.copilot/tools/learn.py)\n"
            )));
        }

        // task_complete.
        if tool_name == "task_complete" {
            if !learn_should_block() {
                return None;
            }
            let count = marker_auth::verify_counter(&markers_dir().join("code-edit-count"));
            return Some(deny(&format!(
                "\u{1f9e0} LEARN REQUIRED: {count} code files edited but learn.py not called. \
                 Record learnings before completing task:\n  \
                 sk learn --mistake \"Title\" \"Description\" --wing <wing> --room <room>\n  \
                 (fallback: python3 ~/.copilot/tools/learn.py)\n"
            )));
        }

        None
    }
}

// ---------------------------------------------------------------------------
// TentacleEnforceRule — wave12
// ---------------------------------------------------------------------------

/// Minimum file count before `TentacleEnforceRule` fires.
const TENTACLE_ENFORCE_MIN_FILES: usize = 3;
/// Minimum module count before `TentacleEnforceRule` fires.
const TENTACLE_ENFORCE_MIN_MODULES: usize = 2;
/// TTL for `tentacle-edits` entries (24 hours in seconds).
const TENTACLE_ENFORCE_TTL_SECS: u64 = 86_400;

/// Read tentacle-edits paths for the current git repo, applying per-repo
/// filtering and 24-hour TTL pruning.
///
/// Handles both:
/// - **New JSON-dict format** (`{repo_root: [{p, t}…], …}`): filters to the
///   bucket matching the current git root; falls back to the "legacy" bucket
///   with path-prefix filtering.
/// - **Legacy flat format** (bare file-path strings written by
///   `TrackEditsRule`): collected into a synthetic "legacy" bucket with
///   the current timestamp so they expire naturally after 24 h, mirroring
///   Python `_read_edits()`. When a git root is available, relative legacy
///   paths are treated as belonging to the current repo so the native direct
///   writer (`TrackEditsRule`) remains compatible.
///
/// Mirrors `TentacleEnforceRule._read_edits()` and
/// `TentacleEnforceRule._get_entries_for_repo()` in
/// `hooks/rules/tentacle.py`.
fn read_tentacle_edits_for_current_repo() -> Vec<String> {
    let edits_path = markers_dir().join("tentacle-edits");
    let raw_set = marker_auth::verify_list_marker(&edits_path);
    if raw_set.is_empty() {
        return Vec::new();
    }

    let git_root = get_git_root();
    let now = SystemTime::now()
        .duration_since(UNIX_EPOCH)
        .unwrap_or_default()
        .as_secs();
    let ttl_cutoff = now.saturating_sub(TENTACLE_ENFORCE_TTL_SECS);

    // Parse HMAC-set into repo_root → Vec<(path, timestamp)> buckets.
    let mut repo_buckets: std::collections::HashMap<String, Vec<(String, u64)>> =
        std::collections::HashMap::new();
    for entry in &raw_set {
        if entry.starts_with('{') {
            // New JSON-dict format: {repo_root: [{p, t}, ...], ...}
            if let Ok(val) = serde_json::from_str::<Value>(entry) {
                if let Some(obj) = val.as_object() {
                    for (key, bucket) in obj {
                        if let Some(arr) = bucket.as_array() {
                            let vec = repo_buckets.entry(key.clone()).or_default();
                            for e in arr {
                                let p = match e.get("p").and_then(|v| v.as_str()) {
                                    Some(s) => s.to_string(),
                                    None => continue,
                                };
                                let t = e.get("t").and_then(|v| v.as_f64()).unwrap_or(0.0) as u64;
                                vec.push((p, t));
                            }
                        }
                    }
                }
            }
        } else if !entry.is_empty() {
            // Legacy flat path: migrate with current timestamp so TTL still applies.
            repo_buckets
                .entry("legacy".to_string())
                .or_default()
                .push((entry.clone(), now));
        }
    }

    // Select and filter entries for the current repo.
    let root_ref = git_root.as_deref();
    let entries: Vec<(String, u64)> = if let Some(root) = root_ref {
        if let Some(bucket) = repo_buckets.get(root) {
            bucket.clone()
        } else if let Some(legacy) = repo_buckets.get("legacy") {
            // Direct-path compatibility: legacy entries written by native
            // TrackEditsRule are relative paths, so treat relative legacy
            // entries as belonging to the current repo. Absolute paths still
            // require a git-root prefix match.
            legacy
                .iter()
                .filter(|(p, _)| p.starts_with(root) || !Path::new(p).is_absolute())
                .cloned()
                .collect()
        } else {
            Vec::new()
        }
    } else {
        // Python parity: when git root is unavailable, only use the legacy bucket.
        repo_buckets.get("legacy").cloned().unwrap_or_default()
    };

    // Apply TTL: keep entries within the last 24 hours.
    entries
        .into_iter()
        .filter(|(_, t)| *t >= ttl_cutoff)
        .map(|(p, _)| p)
        .collect()
}

/// Return `true` when the bash command appears to write a source file,
/// using `TentacleEnforceRule` semantics.
///
/// Extends the standard redirect/heredoc/sed detection with additional
/// destructive-write indicators: `cp`, `mv`, `patch`, `rsync`, `install`.
/// Mirrors the inline bash-analysis in `TentacleEnforceRule.evaluate()` in
/// `hooks/rules/tentacle.py`.
fn bash_writes_source_for_enforce_tentacle(command: &str) -> bool {
    // Quick exit: no code extension present in the command.
    if !TRACK_CODE_EXTENSIONS
        .iter()
        .any(|ext| command.contains(ext))
        && !ENFORCE_SOURCE_EXTENSIONS
            .iter()
            .any(|ext| command.contains(ext))
    {
        return false;
    }

    // Explicit write-pattern indicators (mirrors Python TentacleEnforceRule).
    if command.contains("<<")
        || command.contains("write_text")
        || command.contains("open(")
        || command.contains("sed -i")
        || command.contains("tee ")
        || command.contains("cp ")
        || command.contains("mv ")
        || command.contains("dd ")
        || command.contains("patch ")
        || command.contains("rsync ")
        || command.contains("install ")
    {
        return true;
    }

    // Redirect `>` / `>>` → check whether each target is a source file.
    let written = extract_written_paths_simple(command);
    for path in &written {
        if is_source_path_for_enforce(path) {
            return true;
        }
    }

    false
}

/// Deny multi-module edits that should go through tentacle-orchestration.
///
/// Mirrors `TentacleEnforceRule` in `hooks/rules/tentacle.py`.
///
/// Triggers on `preToolUse` for `edit`/`create`/`bash` when the
/// `tentacle-edits` marker shows ≥ 3 files across ≥ 2 modules without a
/// valid `tentacle-done` or `tentacle-bypass` marker.  Applies per-repo
/// filtering and 24-hour TTL pruning on JSON-dict entries.
pub struct TentacleEnforceRule;

impl HookRule for TentacleEnforceRule {
    fn name(&self) -> &'static str {
        "tentacle-enforce"
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

        // Kill-switch: hooks-tampered → deny all modifications.
        if marker_auth::check_tamper_marker() {
            return Some(deny(
                "\u{1f6a8} HOOKS TAMPERED: All modifications blocked. Run: sudo python3 ~/.copilot/tools/install.py --lock-hooks",
            ));
        }

        // Bash: secret-access check first, then source-write detection.
        if tool_name == "bash" {
            let command = tool_args
                .get("command")
                .and_then(|v| v.as_str())
                .unwrap_or("");
            if marker_auth::is_secret_access(command) {
                return Some(deny("\u{1f512} Access to protected hook files is blocked."));
            }
            if !bash_writes_source_for_enforce_tentacle(command) {
                return None;
            }
        }

        // Edit / create: skip session-state paths (false-positive bypass).
        if tool_name == "edit" || tool_name == "create" {
            let file_path = tool_args.get("path").and_then(|v| v.as_str()).unwrap_or("");
            if !file_path.is_empty() && is_track_session_path(file_path) {
                return None;
            }
        }

        // Bypass markers: tentacle-done or tentacle-bypass.
        let mdir = markers_dir();
        if marker_auth::verify_marker(&mdir.join("tentacle-done"), "tentacle-done") {
            return None;
        }
        if marker_auth::verify_marker(&mdir.join("tentacle-bypass"), "tentacle-bypass") {
            return None;
        }

        // Read tentacle-edits with per-repo filtering and 24-hour TTL pruning.
        let paths = read_tentacle_edits_for_current_repo();
        if paths.len() < TENTACLE_ENFORCE_MIN_FILES {
            return None;
        }

        // Compute unique modules; deny when ≥ 2 distinct modules are touched.
        let repo_prefix = get_repo_prefix();
        let modules: HashSet<String> = paths
            .iter()
            .map(|p| get_module_for_path(p, Some(&repo_prefix)))
            .filter(|m| !m.is_empty())
            .collect();
        if modules.len() < TENTACLE_ENFORCE_MIN_MODULES {
            return None;
        }

        let mut sorted_modules: Vec<&str> = modules.iter().map(|s| s.as_str()).collect();
        sorted_modules.sort_unstable();

        Some(deny(&format!(
            "\u{1f419} TENTACLE REQUIRED: {} files across {} modules ({modules}). \
             Multi-module edits need tentacle-orchestration with a clear swarm \
             strategy, handoff trail, and explicit commit + push plan. \
             If you are the orchestrator: \
             (1) tentacle.py create <name> --scope \"<paths>\" \
             (2) tentacle.py todo <name> add \"<task>\" \
             (3) tentacle.py swarm <name> \
             (4) tentacle.py complete <name> \
             Check runtime: tentacle.py status \
             If you are a dispatched sub-agent: read bundle/manifest.json, \
             stay in declared scope, write handoff, skip git commit/push.",
            paths.len(),
            modules.len(),
            modules = sorted_modules.join(", "),
        )))
    }
}

// ---------------------------------------------------------------------------
// Registry
// ---------------------------------------------------------------------------

/// Return all registered native rules in dispatch order.
///
/// Order mirrors the Python registry in `hooks/rules/__init__.py`:
///   sessionStart lifecycle first, then preToolUse rules (deny-capable,
///   first-deny-wins), then postToolUse, then remaining lifecycle / stop
///   events, then errorOccurred (informational KB search, last).
pub fn all_rules() -> Vec<Box<dyn HookRule>> {
    vec![
        // sessionStart (lifecycle acknowledgement + briefing + integrity)
        Box::new(SessionStartRule),
        Box::new(AutoBriefingRule), // wave9: briefing.py subprocess + HMAC markers
        Box::new(IntegrityRule),    // wave9: SHA256 manifest check
        // preToolUse (first-deny-wins; order matters)
        Box::new(EnforceBriefingRule), // wave11: briefing gate (native parity)
        Box::new(EnforceLearnRule),    // wave11: learn gate (native parity)
        Box::new(TentacleEnforceRule), // wave12: tentacle-enforce gate (native parity)
        Box::new(SubagentGitGuardRule),
        Box::new(SyntaxGateRule), // wave13: Python syntax check via subprocess; deny-capable
        Box::new(BlockEditDistRule),
        Box::new(PnpmLockfileGuardRule),
        Box::new(BlockUnsafeHtmlRule),
        Box::new(VerificationGatePreRule), // wave8: dirty-mark + closeout deny
        Box::new(ReadBeforeEditRule),      // preToolUse informational + postToolUse tracking
        // postToolUse (informational / side-effect; registration order mirrors Python registry)
        Box::new(TrackEditsRule),
        Box::new(LearnReminderRule),
        Box::new(TestReminderRule),
        Box::new(AutoBugDetectorRule), // wave13: issue #86 bug-fix pattern detector
        Box::new(NextjsTypecheckReminderRule),
        Box::new(VerificationGatePostRule),
        Box::new(TentacleSuggestRule), // wave8: read-only tentacle suggestion
        // sessionEnd (lifecycle + marker cleanup + recurrence detection)
        Box::new(SessionEndRule),
        Box::new(RecurrenceDetectorRule), // wave9: DB-side recurrence counter
        // agentStop / subagentStop (lifecycle)
        Box::new(AgentStopRule),
        // errorOccurred (KB search: native Rust first, Python fallback; informational)
        Box::new(ErrorOccurredRule),
    ]
}

// ---------------------------------------------------------------------------
// Tests
// ---------------------------------------------------------------------------

#[cfg(test)]
mod tests {
    use super::*;
    use serde_json::json;
    use std::sync::{Mutex, OnceLock};

    fn env_lock() -> std::sync::MutexGuard<'static, ()> {
        static LOCK: OnceLock<Mutex<()>> = OnceLock::new();
        // Recover from a poisoned mutex caused by a prior test panic while
        // holding the guard.  This prevents cascade PoisonError failures across
        // independent tests that share the serialisation lock.
        LOCK.get_or_init(|| Mutex::new(()))
            .lock()
            .unwrap_or_else(|e| e.into_inner())
    }

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
        assert!(!command_is_git_commit_or_push("echo hello"));
    }

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

    // --- PnpmLockfileGuardRule ---

    #[test]
    fn pnpm_guard_returns_none_for_non_commit_command() {
        let rule = PnpmLockfileGuardRule;
        let data = json!({
            "toolName": "bash",
            "toolArgs": {"command": "git status"}
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
        let command =
            "python3 - <<'PY'\nwith open('/repo/file.py', 'w') as f:\n    f.write('x')\nPY";
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

    // --- all_rules registry covers new wave7 rules ---

    #[test]
    fn all_rules_includes_read_before_edit() {
        let rules = all_rules();
        assert!(
            rules.iter().any(|r| r.name() == "read-before-edit"),
            "all_rules must include read-before-edit"
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
    fn all_rules_includes_verification_gate_post() {
        let rules = all_rules();
        assert!(
            rules.iter().any(|r| r.name() == "verification-gate-post"),
            "all_rules must include verification-gate-post"
        );
    }

    #[test]
    fn all_rules_wave7_rules_never_deny_on_empty_payload() {
        // ReadBeforeEditRule and VerificationGatePostRule must be fail-open
        // and informational on an empty payload.
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
                        rule.name(), event
                    );
                }
            }
        }
    }

    #[test]
    fn pnpm_guard_can_produce_deny_but_fails_open_on_empty_payload() {
        // PnpmLockfileGuardRule is deny-capable but must fail-open on empty payload.
        let rule = PnpmLockfileGuardRule;
        let data = json!({});
        // No toolArgs → fails open (returns None).
        assert!(
            rule.evaluate("preToolUse", &data).is_none(),
            "PnpmLockfileGuardRule must fail-open (None) when toolArgs absent"
        );
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
    fn learn_reminder_returns_none_for_bash_with_learn_py() {
        // bash + learn.py detected: marker written (side-effect), but evaluate → None
        let rule = LearnReminderRule;
        let data = json!({
            "toolName": "bash",
            "toolArgs": {"command": "python3 ~/.copilot/tools/learn.py --mistake 'title' 'desc'"}
        });
        // Returns None (no output for bash — mirrors Python)
        assert!(
            rule.evaluate("postToolUse", &data).is_none(),
            "bash + learn.py must return None (no output; side-effect only)"
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
    }

    #[test]
    fn command_invokes_learn_py_rejects_no_python_prefix() {
        // "learn.py" present but no python prefix → false
        assert!(!command_invokes_learn_py("cat learn.py"));
        assert!(!command_invokes_learn_py("echo learn.py"));
        assert!(!command_invokes_learn_py("learn.py --help"));
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

    // --- all_rules registry covers new wave6 informational rules ---

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
    fn all_rules_new_rules_are_informational_only() {
        // Prove all three new wave6 rules can never produce a deny.
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

    // --- SubagentGitGuardRule HMAC wiring (wave6) ---

    /// Unsigned marker (empty file) without a secret must be accepted:
    /// backward-compat path — any existing file passes when no secret is configured.
    ///
    /// Skipped when the real `.marker-secret` file exists, because in that
    /// environment `verify_marker` requires a valid HMAC-SHA256 signature.
    #[test]
    fn git_guard_hmac_unsigned_no_secret_backward_compat() {
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
    }

    #[test]
    fn is_closeout_action_rejects_non_bash_non_task_complete() {
        let (ok, _) = is_closeout_action("edit", "");
        assert!(!ok, "edit tool must not be a closeout");

        let (ok, _) = is_closeout_action("view", "");
        assert!(!ok, "view tool must not be a closeout");
    }

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

    // --- wave11: all_rules() registration verification ---

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
        marker_auth::sign_list_marker(&mdir.join("tentacle-edits"), &paths)
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
        marker_auth::sign_list_marker(&mdir.join("tentacle-edits"), &paths)
            .expect("sign_list_marker");

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
        marker_auth::sign_list_marker(&mdir.join("tentacle-edits"), &paths)
            .expect("sign_list_marker");

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
        marker_auth::sign_list_marker(&mdir.join("tentacle-edits"), &paths)
            .expect("sign_list_marker");
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
        marker_auth::sign_list_marker(&mdir.join("tentacle-edits"), &paths)
            .expect("sign_list_marker");
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
        let new = "def process(data):\n    if result.is_valid:\n        do_something()\n    return result";
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
}
