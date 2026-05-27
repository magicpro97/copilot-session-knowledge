use super::*;

// ---------------------------------------------------------------------------
// LearnReminderRule
// ---------------------------------------------------------------------------

/// Informational postToolUse learn reminder.
///
/// Conservative port of `hooks/rules/learn_reminder.py::LearnReminderRule`
/// for the native direct path:
///   - When a `bash` command invokes `learn.py` or `sk learn`, writes the
///     `learn-done` marker via [`marker_auth::sign_marker`] so the enforce-learn
///     hook can verify it. The write is idempotent and safe even when Python also
///     writes the same marker.
///   - After learning, emits a skill-update follow-up so reusable lessons get
///     folded back into skills with skill-creator standards.
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
pub(crate) fn command_invokes_learn_py(command: &str) -> bool {
    if !command.contains("learn.py")
        && !command.contains("sk learn")
        && !command.contains("sk.exe learn")
    {
        return false;
    }
    if command.contains("sk learn") || command.contains("sk.exe learn") {
        return true;
    }
    // Must have a python prefix somewhere before learn.py.
    command.contains("python3 ")
        || command.contains("python3\t")
        || command.contains("python ")
        || command.contains("python\t")
}

fn learn_skill_followup_message() -> &'static str {
    "\n  \u{1f9e0} LEARN RECORDED: lesson marker updated.\n\
      \u{1f6e0}\u{fe0f} SKILL UPDATE CHECK: If this learning changes a repeatable\n\
      workflow, guardrail, trigger rule, or output standard, update the relevant\n\
      skill now using skill-creator standards.\n\n\
      skill-creator                 # invoke for non-trivial skill edits\n\
      sk skill-suggest --limit 5    # mine candidates from session knowledge\n\n\
      Compare the whole skill tree (SKILL.md, scripts, references, assets,\n\
      metadata), refresh evals when behavior changes, then validate/package.\n"
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
                let result_type = data
                    .get("toolResult")
                    .and_then(|r| r.as_object())
                    .and_then(|o| o.get("resultType"))
                    .and_then(|v| v.as_str())
                    .unwrap_or("");
                if result_type.is_empty() || result_type == "success" {
                    return Some(info(learn_skill_followup_message()));
                }
            }
            return None;
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
                  docs/SYNC-MATRIX.md \u{2014} docs \u{00b7} memory \u{00b7} operator follow-ups\n\
                  \u{1f6e0}\u{fe0f} SKILL UPDATE CHECK: After learning, decide whether the lesson\n\
                  belongs in a skill. Use skill-creator for non-trivial updates and follow\n\
                  its full-tree compare, eval refresh, validation, and packaging flow.\n",
            ));
        }

        None
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

pub(crate) fn auto_bug_bucket_id() -> u64 {
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
pub(crate) fn auto_bug_trimmed_code_part(line: &str) -> &str {
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

pub(crate) fn auto_bug_line_has_try_indicator(code_part: &str) -> bool {
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

pub(crate) fn auto_bug_line_has_except_indicator(code_part: &str) -> bool {
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

pub(crate) fn auto_bug_has_error_indicator(text: &str) -> bool {
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
pub(crate) fn auto_bug_has_null_safety_indicator(text: &str) -> bool {
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
pub(crate) fn auto_bug_has_guard_clause(text: &str) -> bool {
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
pub(crate) fn auto_bug_has_type_annotation(text: &str) -> bool {
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
pub(crate) fn auto_bug_detect_edit(old_str: &str, new_str: &str) -> Vec<(&'static str, f64)> {
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
pub(crate) fn auto_bug_detect_create(file_text: &str) -> Vec<(&'static str, f64)> {
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

pub(crate) fn auto_bug_filter_detections_for_path(
    path: &str,
    detections: &mut Vec<(&'static str, f64)>,
) {
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
pub(crate) fn auto_bug_call_learn(
    file_path: &str,
    category: &str,
    confidence: f64,
    bucket: u64,
) -> bool {
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
pub(crate) fn auto_bug_write_learn_done() {
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

/// Return `true` when the learn gate should block the current operation.
///
/// Blocks when: learn-done marker is absent AND code-edit-count ≥ threshold.
/// Mirrors `EnforceLearnRule._should_block()` in `hooks/rules/learn_gate.py`.
pub(crate) fn learn_should_block() -> bool {
    let mdir = markers_dir();
    if marker_auth::verify_marker(&mdir.join("learn-done"), "learn-done") {
        return false; // already recorded learnings this session
    }
    marker_auth::verify_counter(&mdir.join("code-edit-count")) >= LEARN_EDIT_THRESHOLD
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
// AutoFlushLearnInboxRule (issue #573)
// ---------------------------------------------------------------------------

/// Auto-flush queued `learn-inbox` entries before the agent finishes a turn.
///
/// Fires on two timing points:
///   - `sessionEnd`                       — drains anything stuck before shutdown.
///   - `preToolUse` with `tool=task_complete` — drains BEFORE `task_complete`
///     returns, so lessons queued during the turn are never lost.
///     `postToolUse` for `task_complete` is too late — `task_complete` ends
///     the model turn before any postToolUse fires.
///
/// Environment variables:
///   - `SK_AUTOFLUSH=0`                   — disable (rule becomes no-op).
///   - `SK_AUTOFLUSH_MAX_AGE_S=<n>`       — retry-stale-only mode: only files
///     whose mtime is at least `n` seconds in the past are flushed. Unset =
///     flush all ages (the common case for the task_complete trigger).
///   - `SK_LEARN_INBOX=<dir>`             — overrides the default inbox path.
///
/// Mechanics:
///   - Empty inbox → fast return (no subprocess), single audit line.
///   - Non-empty → spawn `python <tools>/learn.py --flush-inbox --json
///     [--min-age-s n]` with a 10s wall-clock budget. Reader thread drains
///     stdout to avoid pipe-buffer deadlock.
///   - On stdout JSON parse, audit `flushed=<processed> queued=<remaining>
///     failed=<failed> rejected=<rejected>` and emit an info message.
///   - On subprocess failure or timeout: audit a failure detail; never block
///     shutdown (preToolUse never returns `deny`).
///
/// Security (DoD #573):
///   - The Python flush path validates each file's filename hash against
///     `sha256(file_bytes.rstrip(b"\n"))[:16]` before insert and rejects
///     mismatches as `.rejected`. The Rust rule does not bypass that gate.
pub struct AutoFlushLearnInboxRule;

fn autoflush_enabled() -> bool {
    std::env::var("SK_AUTOFLUSH").map_or(true, |v| v != "0")
}

fn autoflush_min_age_s() -> Option<f64> {
    std::env::var("SK_AUTOFLUSH_MAX_AGE_S")
        .ok()
        .and_then(|v| v.parse::<f64>().ok())
        .filter(|n| *n > 0.0)
}

fn autoflush_inbox_dir() -> PathBuf {
    if let Ok(path) = std::env::var("SK_LEARN_INBOX") {
        let raw: &str = &path;
        if raw == "~" {
            return resolve_home_dir().unwrap_or_else(|| PathBuf::from(raw));
        }
        if let Some(rest) = raw.strip_prefix("~/").or_else(|| raw.strip_prefix("~\\")) {
            if let Some(home) = resolve_home_dir() {
                return home.join(rest);
            }
        }
        return PathBuf::from(path);
    }
    resolve_home_dir()
        .unwrap_or_else(|| PathBuf::from("."))
        .join(".copilot")
        .join("session-state")
        .join("learn-inbox")
}

fn count_inbox_json(inbox: &Path) -> usize {
    if !inbox.is_dir() {
        return 0;
    }
    match fs::read_dir(inbox) {
        Ok(entries) => entries
            .flatten()
            .filter(|e| {
                e.path()
                    .extension()
                    .and_then(|s| s.to_str())
                    .is_some_and(|ext| ext == "json")
            })
            .count(),
        Err(_) => 0,
    }
}

impl HookRule for AutoFlushLearnInboxRule {
    fn name(&self) -> &'static str {
        "auto-flush-learn-inbox"
    }

    fn events(&self) -> &'static [&'static str] {
        &["sessionEnd", "preToolUse"]
    }

    fn tools(&self) -> &'static [&'static str] {
        // Empty = match all tools at the dispatcher level. We filter the
        // preToolUse-only case (`task_complete`) inside `evaluate` so the
        // sessionEnd path (which has no `toolName`) is not accidentally
        // filtered out.
        &[]
    }

    fn evaluate(&self, event: &str, data: &Value) -> Option<Value> {
        // preToolUse: only fire on task_complete. Cheap early bail for every
        // other tool invocation.
        if event == "preToolUse" {
            let tool_name = data.get("toolName").and_then(|v| v.as_str()).unwrap_or("");
            if tool_name != "task_complete" {
                return None;
            }
        }

        if !autoflush_enabled() {
            crate::hooks::audit::audit_log(
                event,
                "task_complete",
                self.name(),
                "info",
                "disabled SK_AUTOFLUSH=0",
            );
            return None;
        }

        let inbox = autoflush_inbox_dir();
        let before = count_inbox_json(&inbox);
        if before == 0 {
            crate::hooks::audit::audit_log(
                event,
                "task_complete",
                self.name(),
                "info",
                "flushed=0 queued=0 failed=0 rejected=0 (empty)",
            );
            return None;
        }

        // Non-empty: invoke learn.py --flush-inbox under a 10s wall budget.
        use crate::config::{python_exe, resolve_tools_dir};
        let learn_py = resolve_tools_dir().join("learn.py");
        if !learn_py.is_file() {
            crate::hooks::audit::audit_log(
                event,
                "task_complete",
                self.name(),
                "error",
                "learn.py missing",
            );
            return None;
        }

        let mut cmd = Command::new(python_exe());
        cmd.arg(&learn_py)
            .arg("--flush-inbox")
            .arg("--json")
            .arg("--limit")
            .arg("100");
        if let Some(min_age) = autoflush_min_age_s() {
            cmd.arg("--min-age-s").arg(format!("{min_age}"));
        }
        // Recursion guard: prevent the spawned learn.py from re-triggering
        // hooks or producing user-facing learn reminders. Also skip embedding
        // so a 50-entry drain stays within the 10s wall budget — embeddings
        // are recomputed by the next scheduled embed run (issue #573 perf DoD).
        cmd.env("COPILOT_HOOKS_SUPPRESS", "1");
        cmd.env("SK_LEARN_SKIP_EMBED", "1");
        cmd.stdout(Stdio::piped()).stderr(Stdio::null());

        let mut child = match cmd.spawn() {
            Ok(c) => c,
            Err(err) => {
                crate::hooks::audit::audit_log(
                    event,
                    "task_complete",
                    self.name(),
                    "error",
                    &format!("spawn_failed:{err}"),
                );
                return None;
            }
        };

        let reader = child.stdout.take().map(|mut out| {
            std::thread::spawn(move || -> Vec<u8> {
                let mut buf = Vec::new();
                let _ = out.read_to_end(&mut buf);
                buf
            })
        });

        let deadline = Instant::now() + Duration::from_secs(10);
        let mut timed_out = false;
        loop {
            match child.try_wait() {
                Ok(Some(_)) => break,
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

        let stdout_bytes = reader.and_then(|h| h.join().ok()).unwrap_or_default();
        let stdout = String::from_utf8_lossy(&stdout_bytes);

        if timed_out {
            let after = count_inbox_json(&inbox);
            let drained = before.saturating_sub(after);
            let detail =
                format!("timeout flushed={drained} queued={after} failed=? rejected=? wall=10s");
            crate::hooks::audit::audit_log(event, "task_complete", self.name(), "info", &detail);
            return Some(info(&format!(
                "[sk] learn-inbox auto-flush timed out: {detail}"
            )));
        }

        let parsed: Option<serde_json::Value> = serde_json::from_str(stdout.trim()).ok();
        let (processed, remaining, failed, rejected) = match parsed {
            Some(v) => (
                v.get("processed").and_then(|x| x.as_u64()).unwrap_or(0),
                v.get("remaining").and_then(|x| x.as_u64()).unwrap_or(0),
                v.get("failed").and_then(|x| x.as_u64()).unwrap_or(0),
                v.get("rejected").and_then(|x| x.as_u64()).unwrap_or(0),
            ),
            None => {
                // Fall back to before/after counts.
                let after = count_inbox_json(&inbox) as u64;
                ((before as u64).saturating_sub(after), after, 0, 0)
            }
        };

        let detail =
            format!("flushed={processed} queued={remaining} failed={failed} rejected={rejected}");
        crate::hooks::audit::audit_log(event, "task_complete", self.name(), "info", &detail);
        Some(info(&format!("[sk] learn-inbox auto-flush: {detail}")))
    }
}
