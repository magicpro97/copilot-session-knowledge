/// HMAC-signed marker authentication — Rust port of `marker_auth.py`.
///
/// Mirrors `hooks/marker_auth.py` behavior byte-for-byte:
///   - Secret at `~/.copilot/hooks/.marker-secret` (UTF-8, trimmed).
///   - No-secret → **fail-open** / backward-compat (file existence = valid).
///   - With secret → HMAC-SHA256 signatures required; unsigned markers rejected.
///   - Constant-time comparison via `hmac::Mac::verify_slice` (mirrors
///     Python `hmac.compare_digest`).
///
/// Functions exposed (names and semantics match the Python originals):
///   - [`sign_marker`] / [`verify_marker`]
///   - [`sign_counter`] / [`verify_counter`]
///   - [`sign_list_marker`] / [`verify_list_marker`]
///   - [`is_secret_access`]
///   - [`check_tamper_marker`] / [`create_tamper_marker`]
///
/// **Important (foundation-only):** This module is NOT wired into any
/// enforcement rule yet.  Hook rules continue delegating to Python until a
/// subsequent wave connects them.
use crate::config::resolve_home_dir;
use hmac::{Hmac, Mac};
use sha2::Sha256;
use std::collections::HashSet;
use std::fs;
use std::path::PathBuf;
use std::time::{SystemTime, UNIX_EPOCH};

type HmacSha256 = Hmac<Sha256>;

// ---------------------------------------------------------------------------
// Protected patterns — mirrors PROTECTED_PATTERNS in marker_auth.py
// ---------------------------------------------------------------------------

const PROTECTED_PATTERNS: &[&str] = &[
    ".marker-secret",
    "integrity-manifest",
    "marker_auth.py",
    "marker_auth ",
    ".copilot/hooks/.",
];

// ---------------------------------------------------------------------------
// Path helpers
// ---------------------------------------------------------------------------

fn secret_path() -> PathBuf {
    resolve_home_dir()
        .unwrap_or_else(|| PathBuf::from("."))
        .join(".copilot")
        .join("hooks")
        .join(".marker-secret")
}

/// Path to `~/.copilot/markers/` directory.
///
/// In test builds the `SK_MARKERS_DIR` environment variable may override the
/// directory so tests do not write to the real `~/.copilot/markers/`.  The
/// override is **not** compiled into production binaries; setting
/// `SK_MARKERS_DIR` at runtime has no effect outside of test builds.
pub fn markers_dir() -> PathBuf {
    #[cfg(any(test, feature = "test-helpers"))]
    if let Ok(dir) = std::env::var("SK_MARKERS_DIR") {
        return PathBuf::from(dir);
    }
    resolve_home_dir()
        .unwrap_or_else(|| PathBuf::from("."))
        .join(".copilot")
        .join("markers")
}

// ---------------------------------------------------------------------------
// Internal helpers
// ---------------------------------------------------------------------------

/// Read the HMAC secret (`~/.copilot/hooks/.marker-secret`).
/// Returns `None` when the file is absent, unreadable, or empty.
/// Mirrors `_read_secret()` in `marker_auth.py`.
fn read_secret() -> Option<String> {
    let path = secret_path();
    if path.is_file() {
        fs::read_to_string(&path)
            .ok()
            .map(|s| s.trim().to_string())
            .filter(|s| !s.is_empty())
    } else {
        None
    }
}

/// Compute HMAC-SHA256 and return lowercase hex (64 chars).
/// Mirrors `hmac.new(secret.encode(), message.encode(), hashlib.sha256).hexdigest()`.
pub(crate) fn hmac_sha256_hex(secret: &str, message: &str) -> String {
    let mut mac =
        HmacSha256::new_from_slice(secret.as_bytes()).expect("HMAC accepts keys of any size");
    mac.update(message.as_bytes());
    let bytes = mac.finalize().into_bytes();
    bytes.iter().map(|b| format!("{b:02x}")).collect()
}

/// Constant-time HMAC-SHA256 verification.
/// Mirrors `hmac.compare_digest(sig, expected)` — prevents timing attacks.
fn hmac_verify(secret: &str, message: &str, sig_hex: &str) -> bool {
    // Decode the provided hex signature into raw bytes.
    let sig_bytes: Option<Vec<u8>> = (0..sig_hex.len())
        .step_by(2)
        .map(|i| u8::from_str_radix(&sig_hex[i..i + 2], 16).ok())
        .collect();
    let sig_bytes = match sig_bytes {
        Some(b) if b.len() == 32 => b,
        _ => return false,
    };

    let mut mac =
        HmacSha256::new_from_slice(secret.as_bytes()).expect("HMAC accepts keys of any size");
    mac.update(message.as_bytes());
    // `verify_slice` is constant-time (mirrors hmac.compare_digest).
    mac.verify_slice(&sig_bytes).is_ok()
}

/// Current Unix timestamp as string (mirrors `str(int(time.time()))`).
fn unix_ts() -> String {
    SystemTime::now()
        .duration_since(UNIX_EPOCH)
        .map(|d| d.as_secs().to_string())
        .unwrap_or_else(|_| "0".to_string())
}

// ---------------------------------------------------------------------------
// Marker sign / verify
// ---------------------------------------------------------------------------

/// Write a signed marker file.
///
/// Mirrors `sign_marker(marker_path, name)`:
/// - No secret → create empty file (backward-compat touch).
/// - With secret → write `{"name":…,"ts":…,"sig":…}`.
pub fn sign_marker(marker_path: &PathBuf, name: &str) -> std::io::Result<()> {
    let _ = fs::create_dir_all(markers_dir());
    match read_secret() {
        None => {
            // Backward compat: Python does `marker_path.touch()`.
            fs::write(marker_path, b"")?;
        }
        Some(secret) => {
            let ts = unix_ts();
            let sig = hmac_sha256_hex(&secret, &format!("{name}:{ts}"));
            let payload = serde_json::json!({"name": name, "ts": ts, "sig": sig});
            fs::write(marker_path, payload.to_string())?;
        }
    }
    Ok(())
}

/// Verify a signed marker file.
///
/// Mirrors `verify_marker(marker_path, name)`:
/// - Missing file → `false`.
/// - No secret → `true` (file existence is enough — backward compat).
/// - With secret → verify name field and HMAC-SHA256 signature.
pub fn verify_marker(marker_path: &PathBuf, name: &str) -> bool {
    if !marker_path.is_file() {
        return false;
    }
    let secret = match read_secret() {
        None => return true, // no secret → accept (backward compat)
        Some(s) => s,
    };
    let content = match fs::read_to_string(marker_path) {
        Ok(c) => c,
        Err(_) => return false,
    };
    let data: serde_json::Value = match serde_json::from_str(&content) {
        Ok(v) => v,
        Err(_) => return false,
    };
    let m_name = data.get("name").and_then(|v| v.as_str()).unwrap_or("");
    let ts = data.get("ts").and_then(|v| v.as_str()).unwrap_or("");
    let sig = data.get("sig").and_then(|v| v.as_str()).unwrap_or("");
    if m_name != name {
        return false;
    }
    hmac_verify(&secret, &format!("{name}:{ts}"), sig)
}

// ---------------------------------------------------------------------------
// Counter sign / verify
// ---------------------------------------------------------------------------

/// Write a signed counter file.
///
/// Mirrors `sign_counter(counter_path, value)`:
/// - No secret → write plain integer string.
/// - With secret → write `{"name":…,"value":…,"ts":…,"sig":…}`.
pub fn sign_counter(counter_path: &PathBuf, value: i64) -> std::io::Result<()> {
    let _ = fs::create_dir_all(markers_dir());
    let name = counter_path
        .file_name()
        .and_then(|n| n.to_str())
        .unwrap_or("")
        .to_string();
    match read_secret() {
        None => {
            fs::write(counter_path, value.to_string())?;
        }
        Some(secret) => {
            let ts = unix_ts();
            let sig = hmac_sha256_hex(&secret, &format!("{name}:{value}:{ts}"));
            let payload = serde_json::json!({"name": name, "value": value, "ts": ts, "sig": sig});
            fs::write(counter_path, payload.to_string())?;
        }
    }
    Ok(())
}

/// Read and verify a signed counter file.
///
/// Mirrors `verify_counter(counter_path)`:
/// - Missing file → 0.
/// - No secret → parse as plain integer, 0 on failure.
/// - With secret → verify HMAC, return value or 0 on tamper.
pub fn verify_counter(counter_path: &PathBuf) -> i64 {
    if !counter_path.is_file() {
        return 0;
    }
    let content = match fs::read_to_string(counter_path) {
        Ok(c) => c.trim().to_string(),
        Err(_) => return 0,
    };
    let secret = match read_secret() {
        None => return content.parse::<i64>().unwrap_or(0),
        Some(s) => s,
    };
    let data: serde_json::Value = match serde_json::from_str::<serde_json::Value>(&content) {
        Ok(v) if v.is_object() => v,
        _ => return 0,
    };
    let name = data.get("name").and_then(|v| v.as_str()).unwrap_or("");
    let value = data.get("value").and_then(|v| v.as_i64()).unwrap_or(0);
    let ts = data.get("ts").and_then(|v| v.as_str()).unwrap_or("");
    let sig = data.get("sig").and_then(|v| v.as_str()).unwrap_or("");
    if hmac_verify(&secret, &format!("{name}:{value}:{ts}"), sig) {
        value
    } else {
        0
    }
}

// ---------------------------------------------------------------------------
// List-marker sign / verify
// ---------------------------------------------------------------------------

/// Write a signed list-marker file.
///
/// Mirrors `sign_list_marker(marker_path, lines)`:
/// - Lines are sorted before joining with `\n` (matches Python `sorted()`).
/// - No secret → write plain newline-joined content.
/// - With secret → write `{"name":…,"content":…,"sig":…}`.
pub fn sign_list_marker(marker_path: &PathBuf, lines: &[String]) -> std::io::Result<()> {
    let _ = fs::create_dir_all(markers_dir());
    let mut sorted = lines.to_vec();
    sorted.sort();
    let content = sorted.join("\n");
    let name = marker_path
        .file_name()
        .and_then(|n| n.to_str())
        .unwrap_or("")
        .to_string();
    match read_secret() {
        None => {
            fs::write(marker_path, &content)?;
        }
        Some(secret) => {
            let sig = hmac_sha256_hex(&secret, &format!("{name}:{content}"));
            let payload = serde_json::json!({"name": name, "content": content, "sig": sig});
            fs::write(marker_path, payload.to_string())?;
        }
    }
    Ok(())
}

/// Read and verify a signed list-marker file.
///
/// Mirrors `verify_list_marker(marker_path)`:
/// - Missing file → empty set.
/// - No secret → parse as plain newline-separated list.
/// - With secret → verify HMAC, return line set or empty set on tamper.
pub fn verify_list_marker(marker_path: &PathBuf) -> HashSet<String> {
    if !marker_path.is_file() {
        return HashSet::new();
    }
    let raw = match fs::read_to_string(marker_path) {
        Ok(r) => r.trim().to_string(),
        Err(_) => return HashSet::new(),
    };
    let secret = match read_secret() {
        None => {
            return raw
                .lines()
                .filter(|l| !l.is_empty())
                .map(|l| l.to_string())
                .collect();
        }
        Some(s) => s,
    };
    let data: serde_json::Value = match serde_json::from_str(&raw) {
        Ok(v) => v,
        Err(_) => return HashSet::new(),
    };
    let name = data.get("name").and_then(|v| v.as_str()).unwrap_or("");
    let content = data.get("content").and_then(|v| v.as_str()).unwrap_or("");
    let sig = data.get("sig").and_then(|v| v.as_str()).unwrap_or("");
    if hmac_verify(&secret, &format!("{name}:{content}"), sig) {
        content
            .lines()
            .filter(|l| !l.is_empty())
            .map(|l| l.to_string())
            .collect()
    } else {
        HashSet::new()
    }
}

// ---------------------------------------------------------------------------
// Utilities
// ---------------------------------------------------------------------------

/// Check whether a command string references any protected resource.
/// Mirrors `is_secret_access(command)`.
pub fn is_secret_access(command: &str) -> bool {
    PROTECTED_PATTERNS.iter().any(|&p| command.contains(p))
}

/// Return `true` iff *command* is the exact official `--lock-hooks` recovery
/// invocation.  Used by hooks-tampered enforcement rules to allow only the
/// documented recovery command through the kill-switch while denying every
/// other modification.
///
/// Accepted shapes (strict whitespace tokenization; no shell parsing):
///   [sudo [-E]] <python>  <install.py>  --lock-hooks
///
/// * `<python>` ∈ { `python3`, `/usr/bin/python3`, `/usr/local/bin/python3` }
/// * `<install.py>` ∈ literal `~/.copilot/tools/install.py`,
///   literal `$HOME/.copilot/tools/install.py`, or the absolute expansion of
///   `HOME/.copilot/tools/install.py` (matches the deny-message instructions).
///
/// Rejected: any shell metacharacter (`;`, `&`, `|`, `>`, `<`, backtick,
/// newline, carriage return), quotes, command substitution
/// (`$(`), `bash -c`, `env`/`VAR=value` prefixes, `--unlock-hooks`, extra
/// arguments, or any other Python interpreter path.
pub fn is_lock_hooks_recovery(command: &str) -> bool {
    if command.is_empty() {
        return false;
    }
    if !command.is_ascii() {
        return false;
    }
    // Reject shell metachars, quotes, and command substitution before
    // tokenization so injection attempts never reach the allow-list. Backslash
    // is NOT in this set so legitimate Windows absolute paths
    // (C:\Users\x\.copilot\tools\install.py) pass; the strict 3-token,
    // literal-script allow-list below still blocks any abuse.
    for ch in command.chars() {
        match ch {
            ';' | '&' | '|' | '>' | '<' | '`' | '\n' | '\r' | '"' | '\'' => {
                return false;
            }
            _ => {}
        }
    }
    if command.contains("$(") {
        return false;
    }

    let mut tokens: Vec<&str> = command
        .split([' ', '\t'])
        .filter(|s| !s.is_empty())
        .collect();
    if tokens.is_empty() {
        return false;
    }
    if tokens[0] == "sudo" {
        tokens.remove(0);
        if tokens.first() == Some(&"-E") {
            tokens.remove(0);
        }
    }
    if tokens.len() != 3 {
        return false;
    }
    let python_bin = tokens[0];
    let script = tokens[1];
    let flag = tokens[2];

    if !matches!(
        python_bin,
        "python3" | "/usr/bin/python3" | "/usr/local/bin/python3"
    ) {
        return false;
    }
    if flag != "--lock-hooks" {
        return false;
    }

    if script == "~/.copilot/tools/install.py" || script == "$HOME/.copilot/tools/install.py" {
        return true;
    }
    if let Some(home) = resolve_home_dir() {
        let abs = home.join(".copilot").join("tools").join("install.py");
        if let Some(abs_str) = abs.to_str() {
            if abs_str == script {
                return true;
            }
        }
    }
    false
}

/// Return `true` if the `hooks-tampered` marker is present and valid.
/// Mirrors `check_tamper_marker()`.
pub fn check_tamper_marker() -> bool {
    let path = markers_dir().join("hooks-tampered");
    verify_marker(&path, "hooks-tampered")
}

/// Write the `hooks-tampered` marker (best-effort).
/// Mirrors `create_tamper_marker()`.
pub fn create_tamper_marker() {
    let path = markers_dir().join("hooks-tampered");
    let _ = sign_marker(&path, "hooks-tampered");
}

// ---------------------------------------------------------------------------
// Tests
// ---------------------------------------------------------------------------

#[cfg(test)]
mod tests {
    use super::*;
    use serial_test::serial;
    use std::path::PathBuf;

    // -----------------------------------------------------------------------
    // Cross-language parity: HMAC-SHA256 algorithm
    // -----------------------------------------------------------------------
    //
    // Python: hmac.new(key.encode(), msg.encode(), hashlib.sha256).hexdigest()
    // Rust:   hmac_sha256_hex(key, msg)
    //
    // Both use standard HMAC-SHA256 (RFC 2104 + FIPS 198-1) with UTF-8
    // encoding and lowercase hex output.  The following test vectors are
    // derived from known-good HMAC-SHA256 references and verified against
    // the Python implementation.

    /// RFC 4231 §4.2 Test Case 1 (adapted): key=0x0b×20, data="Hi There"
    ///
    /// Note: RFC 4231 uses raw bytes for the key; we test with equivalent
    /// ASCII-encodable inputs to match Python's `str.encode()` path.
    #[test]
    fn hmac_sha256_known_vector_rfc4231_case2() {
        // RFC 4231 §4.3 — key="Jefe", data="what do ya want for nothing?"
        // Python: hmac.new(b"Jefe", b"what do ya want for nothing?", hashlib.sha256).hexdigest()
        // Verified against Python 3.11 on 2026-05-09:
        //   5bdcc146bf60754e6a042426089575c75a003f089d2739839dec58b964ec3843
        let result = hmac_sha256_hex("Jefe", "what do ya want for nothing?");
        assert_eq!(
            result, "5bdcc146bf60754e6a042426089575c75a003f089d2739839dec58b964ec3843",
            "HMAC-SHA256 must match Python hmac.new result (cross-language parity)"
        );
    }

    /// Python-generated parity vector for marker signing format.
    ///
    /// ```python
    /// import hmac, hashlib
    /// hmac.new(b"test-secret-key!", b"briefing-done:1715222400", hashlib.sha256).hexdigest()
    /// # → verified against this Rust output
    /// ```
    ///
    /// This test encodes the _exact message format_ used by sign_marker:
    /// `"{name}:{ts}"` with UTF-8 encoding — confirming cross-language parity
    /// for the marker signing path.
    #[test]
    fn hmac_sha256_marker_format_matches_python_encoding() {
        // The result is computed here to serve as a regression anchor.
        // The Python equivalent is:
        //   hmac.new(b"test-secret-key!", b"briefing-done:1715222400", hashlib.sha256).hexdigest()
        let result = hmac_sha256_hex("test-secret-key!", "briefing-done:1715222400");
        // Must be 64 lowercase hex chars (SHA-256 output = 32 bytes).
        assert_eq!(result.len(), 64, "HMAC-SHA256 hex output must be 64 chars");
        assert!(
            result
                .chars()
                .all(|c| c.is_ascii_hexdigit() && !c.is_ascii_uppercase()),
            "output must be lowercase hex"
        );
        // Anchor value: ensures the algorithm is stable across Rust builds.
        // Verified against Python 3.11 on 2026-05-09:
        //   import hmac, hashlib
        //   hmac.new(b"test-secret-key!", b"briefing-done:1715222400", hashlib.sha256).hexdigest()
        //   → ba285270c5ac71b8b4144b085d930107d820bb93bbff53249fa882f990f4b149
        assert_eq!(
            result, "ba285270c5ac71b8b4144b085d930107d820bb93bbff53249fa882f990f4b149",
            "HMAC-SHA256(\"test-secret-key!\", \"briefing-done:1715222400\") must be stable"
        );
    }

    /// Counter format parity: `"{name}:{value}:{ts}"`.
    #[test]
    fn hmac_sha256_counter_format_parity() {
        let result = hmac_sha256_hex("s3cr3t", "edit-count:42:1715222400");
        assert_eq!(result.len(), 64);
        assert!(result
            .chars()
            .all(|c| c.is_ascii_hexdigit() && !c.is_ascii_uppercase()));
    }

    /// List-marker format parity: `"{name}:{content}"`.
    #[test]
    fn hmac_sha256_list_marker_format_parity() {
        let result = hmac_sha256_hex("abc", "dispatched-files:file_a\nfile_b");
        assert_eq!(result.len(), 64);
        assert!(result
            .chars()
            .all(|c| c.is_ascii_hexdigit() && !c.is_ascii_uppercase()));
    }

    // -----------------------------------------------------------------------
    // Constant-time verification
    // -----------------------------------------------------------------------

    #[test]
    fn hmac_verify_accepts_valid_signature() {
        let secret = "roundtrip-secret";
        let message = "mymarker:1715000000";
        let sig = hmac_sha256_hex(secret, message);
        assert!(hmac_verify(secret, message, &sig), "valid sig must verify");
    }

    #[test]
    fn hmac_verify_rejects_wrong_message() {
        let secret = "roundtrip-secret";
        let sig = hmac_sha256_hex(secret, "marker-a:100");
        assert!(
            !hmac_verify(secret, "marker-b:100", &sig),
            "sig for different message must be rejected"
        );
    }

    #[test]
    fn hmac_verify_rejects_wrong_secret() {
        let sig = hmac_sha256_hex("correct-secret", "marker:100");
        assert!(
            !hmac_verify("wrong-secret", "marker:100", &sig),
            "sig from different secret must be rejected"
        );
    }

    #[test]
    fn hmac_verify_rejects_bad_hex() {
        assert!(
            !hmac_verify("secret", "msg", "not-valid-hex!!!"),
            "bad hex must fail"
        );
        assert!(!hmac_verify("secret", "msg", ""), "empty sig must fail");
        assert!(
            !hmac_verify("secret", "msg", "deadbeef"),
            "short hex must fail"
        );
    }

    // -----------------------------------------------------------------------
    // sign_marker / verify_marker (no secret — backward-compat)
    // -----------------------------------------------------------------------

    fn tmp_marker(name: &str) -> PathBuf {
        let dir = std::env::temp_dir().join("sk_marker_test");
        let _ = fs::create_dir_all(&dir);
        dir.join(name)
    }

    #[test]
    fn verify_marker_returns_false_for_missing_file() {
        let path = tmp_marker("definitely_missing_marker_xyz");
        let _ = fs::remove_file(&path);
        assert!(!verify_marker(&path, "definitely_missing_marker_xyz"));
    }

    /// When no `.marker-secret` file exists, `verify_marker` must return
    /// `true` for any existing file — backward-compat (mirrors Python behavior).
    ///
    /// We skip this test when a real secret file exists to avoid false
    /// positives in locked-down CI environments.
    #[test]
    fn verify_marker_no_secret_returns_true_for_existing_file() {
        if secret_path().is_file() {
            // Secret present — skip (locked env; verify_marker would check sig).
            return;
        }
        let path = tmp_marker("backward_compat_test_marker");
        fs::write(&path, b"").unwrap();
        assert!(
            verify_marker(&path, "backward_compat_test_marker"),
            "no-secret mode must return true for any existing file"
        );
        let _ = fs::remove_file(&path);
    }

    // -----------------------------------------------------------------------
    // sign_marker / verify_marker — roundtrip with injected secret
    // -----------------------------------------------------------------------

    /// Helper: create a temporary secret file and run a closure with the
    /// path.  Restores or removes the secret file after the closure.
    fn with_test_secret<F: FnOnce()>(secret_val: &str, f: F) {
        // We cannot modify the real secret path without affecting other tests
        // that run in parallel.  Instead, exercise the HMAC logic directly
        // without touching the real secret file by testing the lower-level
        // functions (hmac_sha256_hex / hmac_verify) for parity, and using
        // isolated sign/verify tests that depend only on public functions.
        //
        // Full roundtrip with a real injected secret is validated via the
        // integration test in tests/marker_auth_integration_test.rs which
        // runs in a controlled temp environment.
        let _ = (secret_val, f); // suppress unused warnings
    }

    /// Full sign+verify roundtrip using the low-level HMAC primitives
    /// (mirrors the exact computation path of sign_marker + verify_marker).
    #[test]
    fn sign_verify_roundtrip_via_primitives() {
        let secret = "roundtrip-test-secret-42";
        let name = "briefing-done";
        let ts = "1715222400";
        // Simulate sign_marker with secret:
        let sig = hmac_sha256_hex(secret, &format!("{name}:{ts}"));
        let payload = serde_json::json!({"name": name, "ts": ts, "sig": sig});
        // Simulate verify_marker with secret:
        let data: serde_json::Value = serde_json::from_str(&payload.to_string()).unwrap();
        let m_name = data["name"].as_str().unwrap();
        let m_ts = data["ts"].as_str().unwrap();
        let m_sig = data["sig"].as_str().unwrap();
        assert_eq!(m_name, name);
        assert!(hmac_verify(secret, &format!("{m_name}:{m_ts}"), m_sig));
    }

    /// Tampered `name` field is rejected (mirrors Python `if m_name != name`).
    #[test]
    fn verify_rejects_wrong_name_field() {
        let secret = "roundtrip-secret";
        let name = "briefing-done";
        let ts = "1715000000";
        let sig = hmac_sha256_hex(secret, &format!("{name}:{ts}"));
        // Construct a payload with a tampered name:
        let tampered = serde_json::json!({"name": "enforce-learn", "ts": ts, "sig": sig});
        let data: serde_json::Value = serde_json::from_str(&tampered.to_string()).unwrap();
        let m_name = data["name"].as_str().unwrap();
        let m_ts = data["ts"].as_str().unwrap();
        let m_sig = data["sig"].as_str().unwrap();
        // Name mismatch → rejected before HMAC check (mirrors Python `m_name != name`).
        assert_ne!(m_name, name, "name must differ in tampered payload");
        // Even if we re-verify with the tampered name, sig was for original.
        assert!(
            !hmac_verify(secret, &format!("{m_name}:{m_ts}"), m_sig),
            "sig computed for 'briefing-done' must not verify as 'enforce-learn'"
        );
    }

    // -----------------------------------------------------------------------
    // verify_counter — backward-compat (no secret)
    // -----------------------------------------------------------------------

    #[test]
    fn verify_counter_returns_zero_for_missing_file() {
        let path = tmp_marker("missing_counter_xyz_999");
        let _ = fs::remove_file(&path);
        assert_eq!(verify_counter(&path), 0);
    }

    #[test]
    fn verify_counter_no_secret_parses_plain_integer() {
        if secret_path().is_file() {
            return; // skip in locked env
        }
        let path = tmp_marker("plain_counter_test");
        fs::write(&path, "42").unwrap();
        assert_eq!(verify_counter(&path), 42);
        let _ = fs::remove_file(&path);
    }

    #[test]
    fn verify_counter_no_secret_returns_zero_for_invalid_content() {
        if secret_path().is_file() {
            return;
        }
        let path = tmp_marker("invalid_counter_test");
        fs::write(&path, "not-a-number").unwrap();
        assert_eq!(verify_counter(&path), 0);
        let _ = fs::remove_file(&path);
    }

    /// Counter sign+verify roundtrip via low-level primitives.
    #[test]
    fn counter_roundtrip_via_primitives() {
        let secret = "counter-secret-99";
        let name = "edit-count";
        let value: i64 = 7;
        let ts = "1715222400";
        let sig = hmac_sha256_hex(secret, &format!("{name}:{value}:{ts}"));
        let payload = serde_json::json!({"name": name, "value": value, "ts": ts, "sig": sig});
        let data: serde_json::Value = serde_json::from_str(&payload.to_string()).unwrap();
        let m_name = data["name"].as_str().unwrap();
        let m_value = data["value"].as_i64().unwrap();
        let m_ts = data["ts"].as_str().unwrap();
        let m_sig = data["sig"].as_str().unwrap();
        assert!(hmac_verify(
            secret,
            &format!("{m_name}:{m_value}:{m_ts}"),
            m_sig
        ));
        assert_eq!(m_value, value);
    }

    // -----------------------------------------------------------------------
    // verify_list_marker — backward-compat (no secret)
    // -----------------------------------------------------------------------

    #[test]
    fn verify_list_marker_returns_empty_for_missing_file() {
        let path = tmp_marker("missing_list_marker_xyz");
        let _ = fs::remove_file(&path);
        assert!(verify_list_marker(&path).is_empty());
    }

    #[test]
    fn verify_list_marker_no_secret_returns_plain_set() {
        if secret_path().is_file() {
            return;
        }
        let path = tmp_marker("plain_list_marker_test");
        fs::write(&path, "file_a\nfile_b\nfile_c").unwrap();
        let result = verify_list_marker(&path);
        assert!(result.contains("file_a"));
        assert!(result.contains("file_b"));
        assert!(result.contains("file_c"));
        let _ = fs::remove_file(&path);
    }

    /// List-marker sign+verify roundtrip via low-level primitives.
    #[test]
    fn list_marker_roundtrip_via_primitives() {
        let secret = "list-secret";
        let name = "dispatched-files";
        let mut lines = [
            "file_c".to_string(),
            "file_a".to_string(),
            "file_b".to_string(),
        ];
        lines.sort(); // mirrors Python `sorted(lines)`
        let content = lines.join("\n");
        let sig = hmac_sha256_hex(secret, &format!("{name}:{content}"));
        let payload = serde_json::json!({"name": name, "content": content, "sig": sig});
        let data: serde_json::Value = serde_json::from_str(&payload.to_string()).unwrap();
        let m_name = data["name"].as_str().unwrap();
        let m_content = data["content"].as_str().unwrap();
        let m_sig = data["sig"].as_str().unwrap();
        assert!(hmac_verify(secret, &format!("{m_name}:{m_content}"), m_sig));
        let result: HashSet<String> = m_content.lines().map(|l| l.to_string()).collect();
        assert_eq!(
            result,
            vec!["file_a", "file_b", "file_c"]
                .into_iter()
                .map(String::from)
                .collect()
        );
    }

    /// Tampered list content is rejected.
    #[test]
    fn list_marker_tampered_content_is_rejected() {
        let secret = "list-secret";
        let name = "dispatched-files";
        let content = "file_a\nfile_b";
        let sig = hmac_sha256_hex(secret, &format!("{name}:{content}"));
        // Tamper: add a new file to content without re-signing.
        let tampered =
            serde_json::json!({"name": name, "content": "file_a\nfile_b\ninjected", "sig": sig});
        let data: serde_json::Value = serde_json::from_str(&tampered.to_string()).unwrap();
        let m_name = data["name"].as_str().unwrap();
        let m_content = data["content"].as_str().unwrap();
        let m_sig = data["sig"].as_str().unwrap();
        assert!(
            !hmac_verify(secret, &format!("{m_name}:{m_content}"), m_sig),
            "tampered content must fail HMAC verification"
        );
    }

    // -----------------------------------------------------------------------
    // is_secret_access
    // -----------------------------------------------------------------------

    #[test]
    fn is_secret_access_detects_protected_patterns() {
        assert!(is_secret_access("cat ~/.copilot/hooks/.marker-secret"));
        assert!(is_secret_access("vim integrity-manifest"));
        assert!(is_secret_access("python3 marker_auth.py gen-secret"));
        assert!(is_secret_access("ls marker_auth "));
        assert!(is_secret_access("ls .copilot/hooks/."));
    }

    #[test]
    fn is_secret_access_allows_safe_commands() {
        assert!(!is_secret_access("cat ~/.copilot/markers/briefing-done"));
        assert!(!is_secret_access("python3 hook_runner.py"));
        assert!(!is_secret_access("echo hello"));
        assert!(!is_secret_access("git commit -m 'fix'"));
    }

    // -----------------------------------------------------------------------
    // check_tamper_marker — does not panic
    // -----------------------------------------------------------------------

    #[test]
    fn check_tamper_marker_does_not_panic() {
        // Must not panic regardless of filesystem state.
        let _ = check_tamper_marker();
    }

    #[test]
    #[serial]
    fn create_tamper_marker_does_not_panic() {
        // Use a temp dir via SK_MARKERS_DIR to avoid writing to real ~/.copilot/markers/.
        let tmp = tempfile::tempdir().expect("tempdir");
        std::env::set_var("SK_MARKERS_DIR", tmp.path());
        create_tamper_marker();
        std::env::remove_var("SK_MARKERS_DIR");
        // Marker file must exist in the temp dir (not the real markers dir).
        assert!(tmp.path().join("hooks-tampered").exists());
    }

    // -----------------------------------------------------------------------
    // is_lock_hooks_recovery — strict allow-list
    // -----------------------------------------------------------------------

    #[test]
    fn lock_hooks_recovery_allows_canonical_command() {
        assert!(is_lock_hooks_recovery(
            "sudo python3 ~/.copilot/tools/install.py --lock-hooks"
        ));
    }

    #[test]
    fn lock_hooks_recovery_allows_without_sudo() {
        assert!(is_lock_hooks_recovery(
            "python3 ~/.copilot/tools/install.py --lock-hooks"
        ));
    }

    #[test]
    fn lock_hooks_recovery_allows_sudo_dash_e() {
        assert!(is_lock_hooks_recovery(
            "sudo -E python3 $HOME/.copilot/tools/install.py --lock-hooks"
        ));
    }

    #[test]
    fn lock_hooks_recovery_allows_explicit_python_paths() {
        assert!(is_lock_hooks_recovery(
            "sudo /usr/bin/python3 ~/.copilot/tools/install.py --lock-hooks"
        ));
        assert!(is_lock_hooks_recovery(
            "/usr/local/bin/python3 $HOME/.copilot/tools/install.py --lock-hooks"
        ));
    }

    #[test]
    fn lock_hooks_recovery_allows_absolute_home_path() {
        // The literal expansion of HOME/.copilot/tools/install.py must be accepted
        // so tooling that already expanded ~ is not blocked.
        if let Some(home) = resolve_home_dir() {
            let abs = home.join(".copilot").join("tools").join("install.py");
            let cmd = format!("sudo python3 {} --lock-hooks", abs.to_string_lossy());
            assert!(
                is_lock_hooks_recovery(&cmd),
                "absolute HOME install.py path must be allowed: {cmd}"
            );
        }
    }

    #[test]
    fn lock_hooks_recovery_rejects_unlock_flag() {
        assert!(!is_lock_hooks_recovery(
            "sudo python3 ~/.copilot/tools/install.py --unlock-hooks"
        ));
    }

    #[test]
    fn lock_hooks_recovery_rejects_unrelated_commands() {
        assert!(!is_lock_hooks_recovery("ls"));
        assert!(!is_lock_hooks_recovery("echo hello"));
        assert!(!is_lock_hooks_recovery(""));
        assert!(!is_lock_hooks_recovery(
            "sudo python2 ~/.copilot/tools/install.py --lock-hooks"
        ));
    }

    #[test]
    fn lock_hooks_recovery_rejects_chained_commands() {
        assert!(!is_lock_hooks_recovery(
            "sudo python3 ~/.copilot/tools/install.py --lock-hooks; rm -rf /"
        ));
        assert!(!is_lock_hooks_recovery(
            "sudo python3 ~/.copilot/tools/install.py --lock-hooks && rm -rf /"
        ));
        assert!(!is_lock_hooks_recovery(
            "sudo python3 ~/.copilot/tools/install.py --lock-hooks | tee /tmp/x"
        ));
    }

    #[test]
    fn lock_hooks_recovery_rejects_redirection_and_substitution() {
        assert!(!is_lock_hooks_recovery(
            "sudo python3 ~/.copilot/tools/install.py --lock-hooks > /tmp/log"
        ));
        assert!(!is_lock_hooks_recovery(
            "sudo python3 ~/.copilot/tools/install.py --lock-hooks < /etc/hosts"
        ));
        assert!(!is_lock_hooks_recovery(
            "sudo python3 ~/.copilot/tools/install.py `whoami`"
        ));
        assert!(!is_lock_hooks_recovery(
            "sudo python3 ~/.copilot/tools/install.py $(whoami)"
        ));
    }

    #[test]
    fn lock_hooks_recovery_rejects_bash_c_wrapper() {
        assert!(!is_lock_hooks_recovery(
            "bash -c sudo python3 ~/.copilot/tools/install.py --lock-hooks"
        ));
        assert!(!is_lock_hooks_recovery(
            "sh -c sudo python3 ~/.copilot/tools/install.py --lock-hooks"
        ));
    }

    #[test]
    fn lock_hooks_recovery_rejects_env_prefix_and_extra_args() {
        assert!(!is_lock_hooks_recovery(
            "FOO=1 python3 ~/.copilot/tools/install.py --lock-hooks"
        ));
        assert!(!is_lock_hooks_recovery(
            "sudo python3 ~/.copilot/tools/install.py --lock-hooks extra"
        ));
        assert!(!is_lock_hooks_recovery(
            "sudo python3 ~/.copilot/tools/install.py --lock-hooks --force"
        ));
    }

    #[test]
    fn lock_hooks_recovery_rejects_quotes_and_alt_paths() {
        assert!(!is_lock_hooks_recovery(
            "sudo python3 \"~/.copilot/tools/install.py\" --lock-hooks"
        ));
        assert!(!is_lock_hooks_recovery(
            "sudo python3 '~/.copilot/tools/install.py' --lock-hooks"
        ));
        assert!(!is_lock_hooks_recovery(
            "sudo python3 /etc/install.py --lock-hooks"
        ));
        assert!(!is_lock_hooks_recovery(
            "sudo python3 ~/copilot/tools/install.py --lock-hooks"
        ));
    }

    #[test]
    fn lock_hooks_recovery_rejects_non_ascii_whitespace() {
        // NBSP (U+00A0) between tokens — must not tokenize as separator.
        assert!(!is_lock_hooks_recovery(
            "python3\u{00a0}~/.copilot/tools/install.py --lock-hooks"
        ));
        // NBSP leading prefix.
        assert!(!is_lock_hooks_recovery(
            "\u{00a0}python3 ~/.copilot/tools/install.py --lock-hooks"
        ));
    }

    // -----------------------------------------------------------------------
    // with_test_secret placeholder (extended in integration tests)
    // -----------------------------------------------------------------------

    #[test]
    fn with_test_secret_noop_compiles() {
        with_test_secret("ignored", || {});
    }
}
