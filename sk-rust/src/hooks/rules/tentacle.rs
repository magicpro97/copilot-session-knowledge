use super::*;

// ---------------------------------------------------------------------------
// Shared helpers for VerificationGatePreRule + TentacleSuggestRule
// ---------------------------------------------------------------------------

/// Module-marker directory names — mirrors Python `MODULE_MARKERS` tuple
/// in `hooks/rules/common.py`.
pub(crate) const MODULE_MARKERS: &[&str] = &[
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
pub(crate) fn get_module_for_path(file_path: &str, repo_prefix: Option<&str>) -> String {
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
pub(crate) fn get_git_root() -> Option<String> {
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
pub(crate) fn get_repo_prefix() -> String {
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

pub(crate) const SUGGEST_MIN_FILES: usize = 3;
pub(crate) const SUGGEST_MIN_MODULES: usize = 2;

/// Read file paths from the `tentacle-edits` HMAC-signed list marker.
///
/// Handles both:
/// - Legacy flat format: each element is a bare file-path string (Rust writer).
/// - New JSON-dict format: single element is `{repo_root: [{p, t}...], ...}`
///   (Python writer).
///
/// TTL pruning is not applied to the legacy flat format (no timestamps stored).
/// Fail-open: any parse error → returns empty vec.
pub(crate) fn read_tentacle_edits_paths() -> Vec<String> {
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
// TentacleEnforceRule — wave12
// ---------------------------------------------------------------------------

/// Minimum file count before `TentacleEnforceRule` fires.
pub(crate) const TENTACLE_ENFORCE_MIN_FILES: usize = 3;
/// Minimum module count before `TentacleEnforceRule` fires.
pub(crate) const TENTACLE_ENFORCE_MIN_MODULES: usize = 2;
/// TTL for `tentacle-edits` entries (24 hours in seconds).
pub(crate) const TENTACLE_ENFORCE_TTL_SECS: u64 = 86_400;

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
pub(crate) fn read_tentacle_edits_for_current_repo() -> Vec<String> {
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
pub(crate) fn bash_writes_source_for_enforce_tentacle(command: &str) -> bool {
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
