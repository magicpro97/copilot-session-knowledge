use super::*;

// ---------------------------------------------------------------------------
// VerificationGatePostRule — helpers
// ---------------------------------------------------------------------------

// Surface identifiers — match Python constants in `verification_gate.py`.
pub(crate) const SURFACE_PY: &str = "py";
pub(crate) const SURFACE_UI: &str = "ui";

// Evidence identifiers — match Python constants.
pub(crate) const EV_PY_TESTS: &str = "py_tests";
pub(crate) const EV_UI_FORMAT: &str = "ui_format";
pub(crate) const EV_UI_LINT: &str = "ui_lint";
pub(crate) const EV_UI_TYPECHECK: &str = "ui_typecheck";
pub(crate) const EV_UI_BUILD: &str = "ui_build";

/// Requirements map: which evidence keys each surface needs.
///
/// Mirrors Python `_REQUIREMENTS` dict.
pub(crate) const SURFACE_REQUIREMENTS: &[(&str, &[&str])] = &[
    (SURFACE_PY, &[EV_PY_TESTS]),
    (
        SURFACE_UI,
        &[EV_UI_FORMAT, EV_UI_LINT, EV_UI_TYPECHECK, EV_UI_BUILD],
    ),
];

/// Return the set of surfaces affected by editing `path`.
///
/// Mirrors Python `_surfaces_from_path(path)`.
pub(crate) fn surfaces_from_path(path: &str) -> Vec<&'static str> {
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
pub(crate) fn evidence_from_command(command: &str) -> Vec<&'static str> {
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
pub(crate) fn looks_successful(data: &Value) -> bool {
    let tool_result = match data.get("toolResult") {
        Some(r) if !r.is_null() => r,
        _ => return true, // absent / null → assume success (fail-open)
    };

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

    !output_has_failure_indicator(&output)
}

fn has_nonzero_count_after_prefix(lower: &str, prefix: &str) -> bool {
    lower.match_indices(prefix).any(|(idx, _)| {
        let after = &lower[idx + prefix.len()..];
        let trimmed = after.trim_start();
        trimmed
            .as_bytes()
            .first()
            .is_some_and(|byte| matches!(byte, b'1'..=b'9'))
    })
}

fn output_has_failure_indicator(output: &str) -> bool {
    let lower = output.to_ascii_lowercase();
    if contains_ordered_words(output, &["FAILED"]) {
        return true;
    }
    if has_nonzero_count_after_prefix(&lower, "failed:")
        || has_nonzero_count_after_prefix(&lower, "error:")
        || has_nonzero_count_after_prefix(&lower, "errors:")
        || has_nonzero_count_after_prefix(&lower, "exit code")
        || has_nonzero_count_after_prefix(&lower, "exit status")
        || has_nonzero_count_after_prefix(&lower, "error ts")
    {
        return true;
    }
    let mut previous_nonzero_number = false;
    for token in lower.split_whitespace() {
        let cleaned = token.trim_matches(|c: char| !c.is_ascii_alphanumeric());
        let is_nonzero_number = cleaned
            .as_bytes()
            .first()
            .is_some_and(|byte| matches!(byte, b'1'..=b'9'))
            && cleaned.as_bytes().iter().all(u8::is_ascii_digit);
        if previous_nonzero_number && (cleaned == "failed" || cleaned == "failure") {
            return true;
        }
        previous_nonzero_number = is_nonzero_number;
    }
    false
}

/// Parse the verification ledger from the HMAC-signed list marker.
///
/// Returns `(dirty: HashSet<String>, evidence: HashSet<String>)`.
/// Mirrors Python `_read_ledger()`: expects a single JSON payload string
/// in the HMAC-signed set.
pub(crate) fn read_ledger() -> (HashSet<String>, HashSet<String>) {
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
pub(crate) fn write_ledger(dirty: &HashSet<String>, evidence: &HashSet<String>) {
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
pub(crate) fn mark_dirty_surfaces(new_surfaces: &[&'static str]) {
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
pub(crate) fn extract_written_paths_simple(command: &str) -> Vec<String> {
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
pub(crate) const FIX_PY_TESTS: &str = "python3 test_security.py && python3 test_fixes.py";
pub(crate) const FIX_UI_FORMAT: &str = "cd browse-ui && pnpm format:check";
pub(crate) const FIX_UI_LINT: &str = "cd browse-ui && pnpm lint";
pub(crate) const FIX_UI_TYPECHECK: &str = "cd browse-ui && pnpm typecheck";
pub(crate) const FIX_UI_BUILD: &str = "cd browse-ui && pnpm build";

/// Return `(is_closeout, description)` for a tool invocation.
///
/// Mirrors Python `_is_closeout(tool_name, tool_args)`.  Uses simple
/// substring matching (no `regex` crate required).
pub(crate) fn is_closeout_action(tool_name: &str, cmd: &str) -> (bool, &'static str) {
    if tool_name == "task_complete" {
        return (true, "task_complete");
    }
    if tool_name != "bash" {
        return (false, "");
    }
    if contains_ordered_words(cmd, &["gh", "issue", "close"]) {
        return (true, "gh issue close");
    }
    if contains_ordered_words(cmd, &["gh", "issue", "comment"]) {
        return (true, "gh issue comment");
    }
    if contains_ordered_words(cmd, &["gh", "pr", "merge"]) {
        return (true, "gh pr merge");
    }
    // tentacle.py handoff --status DONE  |  sk tentacle handoff --status DONE
    let has_tentacle_cmd =
        cmd.contains("tentacle.py") || contains_ordered_words(cmd, &["sk", "tentacle"]);
    if has_tentacle_cmd && contains_ordered_words(cmd, &["handoff", "status", "DONE"]) {
        return (true, "tentacle handoff --status DONE");
    }
    if has_tentacle_cmd && contains_ordered_words(cmd, &["complete"]) {
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
