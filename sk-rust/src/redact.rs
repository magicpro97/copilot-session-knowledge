//! Pre-write secret redactor (issue #577).
//!
//! Scans free-form text for common secret/credential patterns and
//! replaces matches with deterministic, dedup-friendly tokens of the
//! form `[REDACTED:{kind}:{sha8}]`. Returns a `RedactionResult` with
//! the redacted text plus structured `Finding`s for callers to log or
//! deny on.
//!
//! Design constraints (from #577):
//! - Zero new dependencies — uses the existing `regex` and `sha2`.
//! - Cross-platform.
//! - No raw secret bytes in `Finding`/audit/log output.
//! - Replacement carries an 8-char hash so identical secrets produce
//!   identical replacement tokens (enables FTS dedup).
//! - SK_REDACT=off|warn|deny via env (default `deny` for new writes).

use sha2::{Digest, Sha256};

#[derive(Debug, Clone, PartialEq, Eq)]
pub struct Finding {
    pub kind: String,
    pub hash: String,
}

#[derive(Debug, Clone)]
pub struct RedactionResult {
    pub redacted: String,
    pub findings: Vec<Finding>,
}

#[allow(dead_code)]
impl RedactionResult {
    pub fn unchanged(text: &str) -> Self {
        Self {
            redacted: text.to_string(),
            findings: Vec::new(),
        }
    }
    pub fn has_findings(&self) -> bool {
        !self.findings.is_empty()
    }
}

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum RedactMode {
    Off,
    Warn,
    Deny,
}

impl RedactMode {
    pub fn from_env() -> Self {
        match std::env::var("SK_REDACT").as_deref() {
            Ok("off") => RedactMode::Off,
            Ok("warn") => RedactMode::Warn,
            Ok("deny") | Err(_) => RedactMode::Deny,
            // Unknown value falls back to deny so we err on the safe side.
            Ok(_) => RedactMode::Deny,
        }
    }
}

fn sha8(value: &str) -> String {
    let h = Sha256::digest(value.as_bytes());
    let hex = format!("{h:x}");
    hex[..8].to_string()
}

fn token(kind: &str, value: &str) -> String {
    format!("[REDACTED:{kind}:{}]", sha8(value))
}

/// Built-in redactor patterns. Order matters: longer/more specific
/// matchers (PEM, JWT) run before generic key/value heuristics so we
/// do not double-redact already-scrubbed regions.
fn redact_pass(input: &str) -> RedactionResult {
    use regex::Regex;
    use std::sync::OnceLock;

    // Each rule: (kind, regex, group-index-of-secret).
    // - group 0 = replace whole match.
    // - group n>0 = replace only that capture (used for k/v rules so
    //   we keep the `password=` literal).
    struct Rule {
        kind: &'static str,
        re: Regex,
        group: usize,
    }

    static RULES: OnceLock<Vec<Rule>> = OnceLock::new();
    let rules = RULES.get_or_init(|| {
        vec![
            // PEM private key blocks — match the whole block from
            // BEGIN through END (multiline, lazy) so the key body and
            // END marker are scrubbed too (#577). If the END marker
            // is absent, the bare-header rule below still catches it.
            Rule {
                kind: "pem_private_key",
                re: Regex::new(
                    r"(?s)-----BEGIN [A-Z ]+PRIVATE KEY-----.*?-----END [A-Z ]+PRIVATE KEY-----",
                )
                .unwrap(),
                group: 0,
            },
            // Fallback: BEGIN header without a paired END (e.g. truncated logs).
            Rule {
                kind: "pem_private_key",
                re: Regex::new(r"-----BEGIN [A-Z ]+PRIVATE KEY-----").unwrap(),
                group: 0,
            },
            // JWT: three b64url segments. Anchor on the `eyJ` header
            // so we do not eat ordinary dot-separated identifiers.
            Rule {
                kind: "jwt",
                re: Regex::new(
                    r"eyJ[A-Za-z0-9_\-]{10,}\.[A-Za-z0-9_\-]{10,}\.[A-Za-z0-9_\-]{10,}",
                )
                .unwrap(),
                group: 0,
            },
            // GitHub tokens (ghp_, gho_, ghu_, ghs_, ghr_).
            Rule {
                kind: "github_token",
                re: Regex::new(r"gh[pousr]_[A-Za-z0-9]{36,}").unwrap(),
                group: 0,
            },
            // AWS access key ID.
            Rule {
                kind: "aws_key",
                re: Regex::new(r"AKIA[0-9A-Z]{16}").unwrap(),
                group: 0,
            },
            // Generic credential k/v assignments: password=…, token=…,
            // secret=…, api_key=…. The secret is the captured value
            // group; literal keyword stays in place.
            Rule {
                kind: "credential_kv",
                re: Regex::new(
                    r#"(?i)(?:password|passwd|secret|token|api[_\-]?key)\s*[=:]\s*['"]?([^\s'",;]{6,})['"]?"#,
                )
                .unwrap(),
                group: 1,
            },
        ]
    });

    let mut text = input.to_string();
    let mut findings: Vec<Finding> = Vec::new();

    // The "[REDACTED:" sentinel prevents re-matching scrubbed regions
    // during later passes.
    for rule in rules.iter() {
        let mut out = String::with_capacity(text.len());
        let mut last_end = 0usize;
        for caps in rule.re.captures_iter(&text) {
            let Some(m) = caps.get(rule.group) else {
                continue;
            };
            let raw = m.as_str();
            // Skip if the matched text is already a [REDACTED:...] token,
            // or if it sits inside an already-redacted region.
            if raw.contains("[REDACTED:") {
                continue;
            }
            // Walk `look_start` back from `m.start()` up to 24 bytes,
            // stopping only on a UTF-8 char boundary. Byte-slicing
            // `m.start() - 24` would panic when the look-back lands
            // inside a multi-byte codepoint (CJK / emoji prefixes),
            // and that input is reachable via user CLI args under
            // both warn and default deny (#577 blocker B).
            let mut look_start = m.start().saturating_sub(24);
            while look_start > 0 && !text.is_char_boundary(look_start) {
                look_start -= 1;
            }
            if text[look_start..m.start()].contains("[REDACTED:") {
                continue;
            }
            out.push_str(&text[last_end..m.start()]);
            out.push_str(&token(rule.kind, raw));
            findings.push(Finding {
                kind: rule.kind.to_string(),
                hash: sha8(raw),
            });
            last_end = m.end();
        }
        out.push_str(&text[last_end..]);
        text = out;
    }

    RedactionResult {
        redacted: text,
        findings,
    }
}

/// Redact secrets from `text` using the built-in pattern set.
pub fn redact(text: &str) -> RedactionResult {
    redact_pass(text)
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn aws_access_key_is_redacted() {
        let r = redact("token AKIAABCDEFGHIJKLMNOP rest");
        assert!(r.has_findings(), "expected findings; got {r:?}");
        assert!(
            r.redacted.contains("[REDACTED:aws_key:"),
            "expected aws_key token in: {}",
            r.redacted
        );
        assert!(
            !r.redacted.contains("AKIAABCDEFGHIJKLMNOP"),
            "raw secret leaked in: {}",
            r.redacted
        );
        assert_eq!(r.findings[0].kind, "aws_key");
        assert_eq!(r.findings[0].hash.len(), 8);
    }

    #[test]
    fn jwt_is_redacted_but_dotted_identifiers_are_not() {
        // Positive
        let secret = "eyJabcdefghij.eyJabcdefghij.signatureXYZ123";
        let r = redact(secret);
        assert!(r.has_findings(), "JWT not detected: {r:?}");
        assert!(r.redacted.contains("[REDACTED:jwt:"));

        // Negative: ordinary dotted text must NOT trigger
        let r2 = redact("see file foo.bar.baz for the answer");
        assert!(!r2.has_findings(), "false positive on dotted text: {r2:?}");
    }

    #[test]
    fn pem_private_key_header_is_redacted() {
        let body = "before\n-----BEGIN RSA PRIVATE KEY-----\nKEYBYTES==\n-----END RSA PRIVATE KEY-----\nafter";
        let r = redact(body);
        assert!(r.has_findings(), "PEM not detected: {r:?}");
        assert!(r.redacted.contains("[REDACTED:pem_private_key:"));
        // #577: the key body and END marker must NOT remain in the output.
        assert!(
            !r.redacted.contains("KEYBYTES=="),
            "PEM body leaked: {}",
            r.redacted
        );
        assert!(
            !r.redacted.contains("-----END"),
            "PEM END marker leaked: {}",
            r.redacted
        );
        // Surrounding text should be preserved.
        assert!(r.redacted.starts_with("before\n"));
        assert!(r.redacted.ends_with("\nafter"));
    }

    #[test]
    fn pem_private_key_bare_header_still_redacted_without_end() {
        // Truncated log: BEGIN present, END missing. Bare-header
        // fallback rule must still scrub it.
        let body = "preamble -----BEGIN OPENSSH PRIVATE KEY----- and then truncated";
        let r = redact(body);
        assert!(r.has_findings(), "bare PEM header not detected: {r:?}");
        assert!(
            r.redacted.contains("[REDACTED:pem_private_key:"),
            "expected pem token in: {}",
            r.redacted
        );
        assert!(
            !r.redacted.contains("-----BEGIN"),
            "PEM BEGIN marker leaked: {}",
            r.redacted
        );
    }

    #[test]
    fn github_token_is_redacted() {
        let s = "GITHUB_TOKEN=ghp_AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA";
        let r = redact(s);
        assert!(r.has_findings(), "ghp_ token not detected: {r:?}");
        // The GitHub-token rule should fire first.
        assert!(
            r.redacted.contains("[REDACTED:github_token:"),
            "expected github_token in: {}",
            r.redacted
        );
        assert!(!r.redacted.contains("ghp_AAAA"));
    }

    #[test]
    fn credential_kv_assignments_are_redacted() {
        let r = redact("password=hunter2hunter2 other field");
        assert!(r.has_findings(), "kv password not detected: {r:?}");
        assert!(r.redacted.contains("[REDACTED:credential_kv:"));
        // The literal keyword must survive.
        assert!(
            r.redacted.starts_with("password="),
            "kv keyword stripped: {}",
            r.redacted
        );
    }

    #[test]
    fn no_findings_for_clean_text() {
        let r = redact("nothing sensitive here, just a regular sentence.");
        assert!(!r.has_findings());
        assert_eq!(
            r.redacted,
            "nothing sensitive here, just a regular sentence."
        );
    }

    #[test]
    fn same_secret_yields_same_replacement_token() {
        let r1 = redact("first AKIAABCDEFGHIJKLMNOP one");
        let r2 = redact("second AKIAABCDEFGHIJKLMNOP two");
        // Same raw secret → same sha8 → same redaction token (dedup).
        let extract = |s: &str| -> String {
            let start = s.find("[REDACTED:aws_key:").unwrap();
            let end = s[start..].find(']').unwrap();
            s[start..start + end + 1].to_string()
        };
        assert_eq!(extract(&r1.redacted), extract(&r2.redacted));
    }

    #[test]
    fn mode_from_env_defaults_to_deny() {
        let prev = std::env::var("SK_REDACT").ok();
        std::env::remove_var("SK_REDACT");
        assert_eq!(RedactMode::from_env(), RedactMode::Deny);
        std::env::set_var("SK_REDACT", "warn");
        assert_eq!(RedactMode::from_env(), RedactMode::Warn);
        std::env::set_var("SK_REDACT", "off");
        assert_eq!(RedactMode::from_env(), RedactMode::Off);
        match prev {
            Some(v) => std::env::set_var("SK_REDACT", v),
            None => std::env::remove_var("SK_REDACT"),
        }
    }

    #[test]
    fn findings_never_contain_raw_secret() {
        let s = "AKIAABCDEFGHIJKLMNOP";
        let r = redact(&format!("x {s} y"));
        for f in &r.findings {
            assert!(
                !f.hash.contains(s) && f.kind != s,
                "raw secret leaked in finding: {f:?}"
            );
        }
    }

    /// Regression for Opus blocker #577 (B): the look-back guard that
    /// prevents double-redaction byte-sliced `text[m.start()-24 ..
    /// m.start()]`. When the preceding 24 bytes contained multi-byte
    /// UTF-8 (CJK or emoji), the slice landed inside a codepoint and
    /// panicked. User CLI args reach this code under both warn and
    /// default deny, so a panic = denial of service.
    #[test]
    fn cjk_before_aws_key_does_not_panic_and_redacts() {
        // 8 CJK chars (3 bytes each) = 24 bytes; the AKIA match starts
        // exactly 25 bytes in (after the trailing space), forcing the
        // look-back to land mid-codepoint with the unsafe arithmetic.
        let input = "中中中中中中中中 AKIAABCDEFGHIJKLMNOP end";
        let r = redact(input);
        assert!(r.has_findings(), "CJK+AWS not redacted: {r:?}");
        assert!(r.redacted.contains("[REDACTED:aws_key:"));
        assert!(
            !r.redacted.contains("AKIAABCDEFGHIJKLMNOP"),
            "raw AWS key leaked: {}",
            r.redacted
        );
        // CJK prefix must survive intact.
        assert!(r.redacted.starts_with("中中中中中中中中"));
    }

    #[test]
    fn emoji_before_aws_key_does_not_panic_and_redacts() {
        // 6 rocket emojis (4 bytes each) = 24 bytes; same boundary risk.
        let input = "🚀🚀🚀🚀🚀🚀 AKIAABCDEFGHIJKLMNOP end";
        let r = redact(input);
        assert!(r.has_findings(), "emoji+AWS not redacted: {r:?}");
        assert!(r.redacted.contains("[REDACTED:aws_key:"));
        assert!(
            !r.redacted.contains("AKIAABCDEFGHIJKLMNOP"),
            "raw AWS key leaked: {}",
            r.redacted
        );
    }

    #[test]
    fn already_redacted_sentinel_in_lookback_still_suppresses_rematch() {
        // The look-back guard's positive case (existing sentinel within
        // 24 bytes) must keep working after the char-boundary fix.
        let pre = format!("{} AKIAABCDEFGHIJKLMNOP", token("aws_key", "x"));
        let r = redact(&pre);
        // Only the trailing AKIA should be redacted; the existing
        // sentinel is preserved (not double-wrapped).
        let count = r.redacted.matches("[REDACTED:").count();
        assert_eq!(
            count, 2,
            "expected 2 sentinels (existing + new), got {count}: {}",
            r.redacted
        );
    }
}
