/// Native hook rules split by concern.
///
/// The dispatcher in `runner.rs` iterates `all_rules()` in registration order,
/// matching by event type and optional tool name. The order below mirrors the
/// Python registry in `hooks/rules/__init__.py` and preserves first-deny-wins
/// semantics for `preToolUse`.
use crate::config::resolve_home_dir;
use std::collections::HashSet;
use std::fs;
use std::io::{Read, Write};
use std::path::{Path, PathBuf};
use std::process::{Command, Stdio};
use std::time::{Duration, Instant, SystemTime, UNIX_EPOCH};

use serde_json::Value;

use super::marker_auth;

mod edit_track;
mod guard;
mod learn;
mod session;
mod tentacle;
mod verification;

pub use edit_track::*;
pub use guard::*;
pub use learn::*;
pub use session::*;
pub use tentacle::*;
pub use verification::*;

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
    fn evaluate(&self, event: &str, data: &Value) -> Option<Value>;
}

fn markers_dir() -> PathBuf {
    resolve_home_dir()
        .unwrap_or_else(|| PathBuf::from("."))
        .join(".copilot")
        .join("markers")
}

fn is_word_byte(byte: u8) -> bool {
    byte.is_ascii_alphanumeric() || byte == b'_'
}

/// Return true when `words` appear in order as whole shell words.
pub(crate) fn contains_ordered_words(text: &str, words: &[&str]) -> bool {
    let bytes = text.as_bytes();
    let mut search_from = 0usize;
    for word in words {
        let needle = word.as_bytes();
        if needle.is_empty() {
            continue;
        }
        let mut found_end = None;
        let mut idx = search_from;
        while idx + needle.len() <= bytes.len() {
            if bytes[idx..idx + needle.len()] == *needle {
                let before_ok = idx == 0 || !is_word_byte(bytes[idx - 1]);
                let after_idx = idx + needle.len();
                let after_ok = after_idx == bytes.len() || !is_word_byte(bytes[after_idx]);
                if before_ok && after_ok {
                    found_end = Some(after_idx);
                    break;
                }
            }
            idx += 1;
        }
        match found_end {
            Some(end) => search_from = end,
            None => return false,
        }
    }
    true
}

fn shell_token_equals(token: &str, word: &str) -> bool {
    token.trim_matches(|c: char| matches!(c, '\'' | '"' | '`' | ',' | ':' | '[' | ']' | '{' | '}'))
        == word
}

/// Return true when `words` appear in order as shell-style whitespace tokens.
pub(crate) fn contains_ordered_shell_tokens(text: &str, words: &[&str]) -> bool {
    let mut remaining = words.iter();
    let Some(mut expected) = remaining.next() else {
        return true;
    };
    for token in text.split(|c: char| c.is_whitespace() || matches!(c, '&' | '|' | ';' | '(' | ')'))
    {
        if shell_token_equals(token, expected) {
            match remaining.next() {
                Some(next) => expected = next,
                None => return true,
            }
        }
    }
    false
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

/// Return all registered native rules in dispatch order.
pub fn all_rules() -> Vec<Box<dyn HookRule>> {
    vec![
        Box::new(SessionStartRule),
        Box::new(AutoBriefingRule),
        Box::new(IntegrityRule),
        Box::new(EnforceBriefingRule),
        Box::new(EnforceLearnRule),
        Box::new(TentacleEnforceRule),
        Box::new(SubagentGitGuardRule),
        Box::new(SyntaxGateRule),
        Box::new(BlockEditDistRule),
        Box::new(PnpmLockfileGuardRule),
        Box::new(BlockUnsafeHtmlRule),
        Box::new(VerificationGatePreRule),
        Box::new(ReadBeforeEditRule),
        Box::new(FileSizeAdvisoryRule),
        Box::new(NewFileAdvisoryRule),
        Box::new(TrackEditsRule),
        Box::new(LearnReminderRule),
        Box::new(TestReminderRule),
        Box::new(AutoBugDetectorRule),
        Box::new(NextjsTypecheckReminderRule),
        Box::new(VerificationGatePostRule),
        Box::new(SkillUsageRule),
        Box::new(TentacleSuggestRule),
        Box::new(SessionEndRule),
        Box::new(RecurrenceDetectorRule),
        Box::new(AgentStopRule),
        Box::new(ErrorOccurredRule),
    ]
}

#[cfg(test)]
mod tests;
