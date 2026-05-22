//! Shared path-safety, dedup, hashing, and span-ID helpers.
//!
//! Ports `browse/importers/_common.py`.  No third-party dependencies beyond
//! `sha1` and `sha2` (already in Cargo.toml).

use std::collections::{BTreeMap, HashSet};
use std::io::{BufRead, Read};
use std::path::{Path, PathBuf};

use sha1::{Digest, Sha1};
use sha2::Sha256;

// ── Line size cap ─────────────────────────────────────────────────────────────

const DEFAULT_MAX_LINE_BYTES: usize = 1024 * 1024; // 1 MiB

/// Return the configured max bytes per raw JSONL line (default 1 MiB).
///
/// Override via `BROWSE_DEBUG_LOG_MAX_LINE_BYTES` environment variable.
pub fn max_line_bytes() -> usize {
    if let Ok(val) = std::env::var("BROWSE_DEBUG_LOG_MAX_LINE_BYTES") {
        if let Ok(n) = val.trim().parse::<usize>() {
            return n.max(1);
        }
    }
    DEFAULT_MAX_LINE_BYTES
}

// ── Bounded line iterator ─────────────────────────────────────────────────────

/// A single yielded item from [`iter_bounded_lines`].
pub struct BoundedLine {
    /// 1-based line number.
    pub line_no: usize,
    /// The line bytes (up to `cap + 1` bytes; may lack trailing `\n` if oversized).
    pub bytes: Vec<u8>,
    /// When `Some(n)`, the line was oversized; `n` is the total bytes consumed.
    pub oversize_total: Option<usize>,
}

/// Yield JSONL lines without buffering any single line beyond `cap + 1` bytes.
///
/// Over-long lines are consumed to the next newline in bounded chunks so the
/// reader position is always advanced correctly.
///
/// Mirrors Python `iter_bounded_lines(fh, cap)`.
pub fn iter_bounded_lines<R: Read>(reader: R, cap: usize) -> BoundedLineIter<R> {
    BoundedLineIter {
        inner: std::io::BufReader::new(reader),
        cap,
        line_no: 0,
    }
}

pub struct BoundedLineIter<R> {
    inner: std::io::BufReader<R>,
    cap: usize,
    line_no: usize,
}

impl<R: Read> Iterator for BoundedLineIter<R> {
    type Item = BoundedLine;

    fn next(&mut self) -> Option<Self::Item> {
        let cap = self.cap;
        // Read up to cap+1 bytes stopping at newline.
        let mut buf = Vec::with_capacity(cap + 2);
        let n = read_up_to(&mut self.inner, &mut buf, cap + 1);
        if n == 0 {
            return None;
        }
        self.line_no += 1;

        let ends_with_newline = buf.last() == Some(&b'\n');

        if buf.len() <= cap || ends_with_newline {
            // Normal line (fits or exactly ends at newline boundary).
            return Some(BoundedLine {
                line_no: self.line_no,
                bytes: buf,
                oversize_total: None,
            });
        }

        // Oversized: keep preview, consume rest of line.
        let preview = buf.clone();
        let mut total = buf.len();
        loop {
            let mut tail = Vec::with_capacity(cap + 1);
            let k = read_up_to(&mut self.inner, &mut tail, cap);
            if k == 0 {
                break;
            }
            total += k;
            if tail.last() == Some(&b'\n') {
                break;
            }
        }
        Some(BoundedLine {
            line_no: self.line_no,
            bytes: preview,
            oversize_total: Some(total),
        })
    }
}

/// Read up to `limit` bytes from `reader`, stopping (and including) the first
/// `\n` encountered.  Returns bytes actually read.
fn read_up_to<R: BufRead>(reader: &mut R, buf: &mut Vec<u8>, limit: usize) -> usize {
    let start = buf.len();
    let mut remaining = limit;
    while remaining > 0 {
        let available = match reader.fill_buf() {
            Ok([]) => break,
            Ok(b) => b,
            Err(_) => break,
        };
        let take = available.len().min(remaining);
        // Find newline position within the slice we're going to take.
        let newline_pos = available[..take].iter().position(|&b| b == b'\n');
        let end = newline_pos.map(|p| p + 1).unwrap_or(take);
        buf.extend_from_slice(&available[..end]);
        reader.consume(end);
        remaining -= end;
        if newline_pos.is_some() {
            break;
        }
    }
    buf.len() - start
}

// ── Error types ───────────────────────────────────────────────────────────────

#[derive(Debug, PartialEq)]
pub enum PathCheckError {
    /// Path contains raw `..` component or escapes safe_base.
    Traversal(String),
    /// A symlink component resolved outside safe_base.
    SymlinkEscape(String),
    /// Path does not exist.
    NotFound(String),
}

impl std::fmt::Display for PathCheckError {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        match self {
            PathCheckError::Traversal(s) => write!(f, "Path traversal: {s}"),
            PathCheckError::SymlinkEscape(s) => write!(f, "Symlink escape: {s}"),
            PathCheckError::NotFound(s) => write!(f, "Path not found: {s}"),
        }
    }
}

impl std::error::Error for PathCheckError {}

// ── Path safety ────────────────────────────────────────────────────────────────

/// Validate that `path` is safe to read.
///
/// 1. Reject raw `..` components in the unresolved path.
/// 2. Canonicalise (returns `NotFound` if absent).
/// 3. If `safe_base` is given, require the canonical path to be under it;
///    distinguishes symlink escape when a symlink component is detected.
pub fn check_path_safe(path: &Path, safe_base: Option<&Path>) -> Result<PathBuf, PathCheckError> {
    // 1. Reject raw '..' components.
    for component in path.components() {
        use std::path::Component;
        if matches!(component, Component::ParentDir) {
            return Err(PathCheckError::Traversal(format!(
                "Path contains traversal component '..': {}",
                path.display()
            )));
        }
    }

    // 2. Canonicalise (strict: requires existence).
    let resolved = path
        .canonicalize()
        .map_err(|_| PathCheckError::NotFound(format!("Path not found: {}", path.display())))?;

    // 3. Safe-base containment.
    if let Some(base) = safe_base {
        let base_resolved = base.canonicalize().map_err(|_| {
            PathCheckError::NotFound(format!("safe_base not found: {}", base.display()))
        })?;
        if !resolved.starts_with(&base_resolved) {
            // Detect whether any component of the original path is a symlink.
            if has_symlink_component(path) {
                return Err(PathCheckError::SymlinkEscape(format!(
                    "Symlink at {} resolves outside safe_base {}",
                    path.display(),
                    base_resolved.display()
                )));
            }
            return Err(PathCheckError::Traversal(format!(
                "Path {} is outside safe_base {}",
                resolved.display(),
                base_resolved.display()
            )));
        }
    }

    Ok(resolved)
}

/// Return `true` if any prefix of `path` is a symlink.
fn has_symlink_component(path: &Path) -> bool {
    let mut current = PathBuf::new();
    for component in path.components() {
        current.push(component);
        if current.is_symlink() {
            return true;
        }
    }
    false
}

// ── File hash ─────────────────────────────────────────────────────────────────

/// Return `"sha256:<hex>"` of the file at `path`, reading in 64 KiB chunks.
pub fn file_hash_sha256(path: &Path) -> std::io::Result<String> {
    let mut hasher = Sha256::new();
    let mut file = std::fs::File::open(path)?;
    let mut buf = [0u8; 65536];
    loop {
        let n = file.read(&mut buf)?;
        if n == 0 {
            break;
        }
        hasher.update(&buf[..n]);
    }
    Ok(format!("sha256:{:x}", hasher.finalize()))
}

// ── Synthetic span ID ─────────────────────────────────────────────────────────

/// Return a 16-char lowercase hex span-ID.
///
/// Mirrors Python: `hashlib.sha1(f"{source}:{idx}:{seq}").hexdigest()[:16]`
/// with all-zero retry (`seq` incremented until result ≠ `"0000000000000000"`).
pub fn synthetic_span_id(source: &str, idx: u64) -> String {
    synthetic_span_id_seq(source, idx, 1)
}

fn synthetic_span_id_seq(source: &str, idx: u64, start_seq: u64) -> String {
    let mut seq = start_seq;
    loop {
        let input = format!("{source}:{idx}:{seq}");
        let hash = Sha1::digest(input.as_bytes());
        let hex: String = hash.iter().map(|b| format!("{b:02x}")).collect();
        let candidate = &hex[..16];
        if candidate != "0000000000000000" {
            return candidate.to_string();
        }
        seq += 1;
    }
}

// ── Content hash for dedup ────────────────────────────────────────────────────

/// Return the first 16 hex chars of SHA-256 of the canonical JSON of `value`.
///
/// Canonical form matches Python's
/// `json.dumps(obj, sort_keys=True, ensure_ascii=True, separators=(",", ":"))`:
/// - Keys sorted lexicographically at every nesting level.
/// - No whitespace.
/// - Non-ASCII characters escaped as `\uXXXX`.
pub fn content_hash_16(value: &serde_json::Value) -> String {
    let canon = canonical_json(value);
    let hash = Sha256::digest(canon.as_bytes());
    let hex: String = hash.iter().map(|b| format!("{b:02x}")).collect();
    hex[..16].to_string()
}

/// Produce canonical JSON matching Python's `json.dumps(sort_keys=True,
/// ensure_ascii=True, separators=(",",":"))`.
pub fn canonical_json(value: &serde_json::Value) -> String {
    use serde_json::Value;
    match value {
        Value::Null => "null".to_string(),
        Value::Bool(b) => if *b { "true" } else { "false" }.to_string(),
        Value::Number(n) => n.to_string(),
        Value::String(s) => encode_json_string(s),
        Value::Array(arr) => {
            let items: Vec<String> = arr.iter().map(canonical_json).collect();
            format!("[{}]", items.join(","))
        }
        Value::Object(map) => {
            // Sort keys lexicographically (BTreeMap preserves insertion order
            // only; collect into sorted structure).
            let sorted: BTreeMap<&str, &Value> = map.iter().map(|(k, v)| (k.as_str(), v)).collect();
            let pairs: Vec<String> = sorted
                .iter()
                .map(|(k, v)| format!("{}:{}", encode_json_string(k), canonical_json(v)))
                .collect();
            format!("{{{}}}", pairs.join(","))
        }
    }
}

/// Encode a Rust string as a JSON string with `ensure_ascii=True` (non-ASCII
/// characters are emitted as `\uXXXX` escape sequences).
fn encode_json_string(s: &str) -> String {
    let mut out = String::with_capacity(s.len() + 2);
    out.push('"');
    for ch in s.chars() {
        match ch {
            '"' => out.push_str(r#"\""#),
            '\\' => out.push_str(r"\\"),
            '\n' => out.push_str(r"\n"),
            '\r' => out.push_str(r"\r"),
            '\t' => out.push_str(r"\t"),
            c if (c as u32) < 0x20 => {
                out.push_str(&format!("\\u{:04x}", c as u32));
            }
            c if c.is_ascii() => out.push(c),
            c => {
                // Non-ASCII: emit as \uXXXX (or surrogate pair for > U+FFFF).
                let code = c as u32;
                if code <= 0xFFFF {
                    out.push_str(&format!("\\u{:04x}", code));
                } else {
                    // Surrogate pair encoding (matches Python's ensure_ascii).
                    let code = code - 0x10000;
                    let high = 0xD800 + (code >> 10);
                    let low = 0xDC00 + (code & 0x3FF);
                    out.push_str(&format!("\\u{:04x}\\u{:04x}", high, low));
                }
            }
        }
    }
    out.push('"');
    out
}

// ── Dedup set ─────────────────────────────────────────────────────────────────

/// Track seen dedup keys and count duplicates.
#[derive(Default)]
pub struct DedupSet {
    seen: HashSet<String>,
    pub deduped: usize,
}

impl DedupSet {
    pub fn new() -> Self {
        Self::default()
    }

    /// Return `true` (and increment counter) if `key` was already seen.
    pub fn is_duplicate(&mut self, key: &str) -> bool {
        if self.seen.contains(key) {
            self.deduped += 1;
            true
        } else {
            self.seen.insert(key.to_string());
            false
        }
    }
}

// ── Span ID validation ────────────────────────────────────────────────────────

/// Return `true` iff `s` is a 16-char lowercase hex string.
pub fn is_valid_span_id(s: &str) -> bool {
    s.len() == 16 && s.chars().all(|c| matches!(c, '0'..='9' | 'a'..='f'))
}

// ── Tests ─────────────────────────────────────────────────────────────────────

#[cfg(test)]
mod tests {
    use super::*;
    use std::io::Cursor;

    // ── synthetic_span_id ─────────────────────────────────────────────────────

    #[test]
    fn synthetic_span_id_known_value_vscode_0() {
        // Python: hashlib.sha1(b"vscode:0:1").hexdigest()[:16] = "88fdec75ea74c57f"
        let id = synthetic_span_id("vscode", 0);
        assert_eq!(id.len(), 16, "span id must be 16 chars");
        assert!(
            id.chars().all(|c| matches!(c, '0'..='9' | 'a'..='f')),
            "span id must be lowercase hex: {id}"
        );
        assert_eq!(id, "88fdec75ea74c57f");
    }

    #[test]
    fn synthetic_span_id_known_value_otel_5() {
        // Python: hashlib.sha1(b"otel:5:1").hexdigest()[:16] = "27ce4d26fcc7932d"
        let id = synthetic_span_id("otel", 5);
        assert_eq!(id.len(), 16);
        assert_eq!(id, "27ce4d26fcc7932d");
    }

    #[test]
    fn synthetic_span_id_never_all_zero() {
        // For any reasonable source/idx combination the result should be non-zero.
        // We can't easily force the all-zero case, but we can verify the normal path.
        for idx in 0u64..10 {
            let id = synthetic_span_id("test", idx);
            assert_ne!(id, "0000000000000000", "span_id must never be all-zero");
        }
    }

    // ── content_hash_16 ───────────────────────────────────────────────────────

    #[test]
    fn content_hash_16_empty_object() {
        // Python: content_hash_16({}) = sha256(b"{}").hexdigest()[:16] = "44136fa355b3678a"
        let val = serde_json::json!({});
        assert_eq!(content_hash_16(&val), "44136fa355b3678a");
    }

    #[test]
    fn content_hash_16_sorted_keys() {
        // Python: json.dumps({"b":1,"a":2}, sort_keys=True, ...) = '{"a":2,"b":1}'
        // sha256(b'{"a":2,"b":1}').hexdigest()[:16] = "d3626ac30a87e6f7"
        let val = serde_json::json!({"b": 1, "a": 2});
        assert_eq!(content_hash_16(&val), "d3626ac30a87e6f7");
    }

    #[test]
    fn content_hash_16_ensure_ascii_non_ascii() {
        // Python encodes non-ASCII as \uXXXX with ensure_ascii=True.
        // "café" → "caf\u00e9"
        let val = serde_json::json!("café");
        let canon = canonical_json(&val);
        assert_eq!(canon, r#""caf\u00e9""#);
    }

    // ── is_valid_span_id ──────────────────────────────────────────────────────

    #[test]
    fn valid_span_id_accepts_16_lower_hex() {
        assert!(is_valid_span_id("0123456789abcdef"));
        assert!(is_valid_span_id("aaaaaaaaaaaaaaaa"));
        assert!(is_valid_span_id("0000000000000000"));
    }

    #[test]
    fn valid_span_id_rejects_uppercase() {
        assert!(!is_valid_span_id("0123456789ABCDEF"));
    }

    #[test]
    fn valid_span_id_rejects_wrong_length() {
        assert!(!is_valid_span_id("0123456789abcde")); // 15 chars
        assert!(!is_valid_span_id("0123456789abcdef0")); // 17 chars
        assert!(!is_valid_span_id(""));
    }

    // ── DedupSet ──────────────────────────────────────────────────────────────

    #[test]
    fn dedup_set_counts_duplicates() {
        let mut d = DedupSet::new();
        assert!(!d.is_duplicate("a"));
        assert!(!d.is_duplicate("b"));
        assert!(d.is_duplicate("a"));
        assert_eq!(d.deduped, 1);
        assert!(d.is_duplicate("b"));
        assert_eq!(d.deduped, 2);
    }

    // ── iter_bounded_lines ────────────────────────────────────────────────────

    #[test]
    fn bounded_lines_normal_lines() {
        let data = b"line1\nline2\nline3\n";
        let lines: Vec<_> = iter_bounded_lines(Cursor::new(data), 100).collect();
        assert_eq!(lines.len(), 3);
        assert_eq!(lines[0].bytes, b"line1\n");
        assert_eq!(lines[0].oversize_total, None);
        assert_eq!(lines[1].line_no, 2);
    }

    #[test]
    fn bounded_lines_oversized_line_reported() {
        // cap = 9; first line is "0123456789\n" = 11 bytes > cap+1=10 without trailing newline.
        // second line "normal\n" = 7 bytes < cap+1=10, fits normally.
        let data = b"0123456789\nnormal\n";
        let lines: Vec<_> = iter_bounded_lines(Cursor::new(data), 9).collect();
        // First line is oversized.
        assert!(
            lines[0].oversize_total.is_some(),
            "first line should be oversized"
        );
        // Preview is cap+1 = 10 bytes.
        assert_eq!(lines[0].bytes.len(), 10);
        // Second line is normal (7 bytes ≤ cap+1=10 and ends with newline).
        assert_eq!(lines[1].bytes, b"normal\n");
        assert_eq!(lines[1].oversize_total, None);
    }

    #[test]
    fn bounded_lines_eof_without_trailing_newline() {
        let data = b"only";
        let lines: Vec<_> = iter_bounded_lines(Cursor::new(data), 100).collect();
        assert_eq!(lines.len(), 1);
        assert_eq!(lines[0].bytes, b"only");
        assert_eq!(lines[0].oversize_total, None);
    }

    // ── check_path_safe ───────────────────────────────────────────────────────

    #[test]
    fn path_safe_rejects_dotdot() {
        let p = Path::new("foo/../bar");
        let result = check_path_safe(p, None);
        assert!(matches!(result, Err(PathCheckError::Traversal(_))));
    }

    #[test]
    fn path_safe_accepts_existing_file() {
        // Use Cargo.toml as a known-existing file.
        let manifest = Path::new(env!("CARGO_MANIFEST_DIR")).join("Cargo.toml");
        let result = check_path_safe(&manifest, None);
        assert!(result.is_ok(), "should accept existing file: {result:?}");
    }

    #[test]
    fn path_safe_rejects_outside_base() {
        let manifest = Path::new(env!("CARGO_MANIFEST_DIR")).join("Cargo.toml");
        // Use a safe_base that definitely does NOT contain Cargo.toml.
        let tmp_base = std::env::temp_dir();
        let result = check_path_safe(&manifest, Some(&tmp_base));
        assert!(
            matches!(result, Err(PathCheckError::Traversal(_))),
            "expected Traversal, got: {result:?}"
        );
    }

    #[test]
    fn path_safe_accepts_file_under_base() {
        let base = Path::new(env!("CARGO_MANIFEST_DIR"));
        let target = base.join("Cargo.toml");
        let result = check_path_safe(&target, Some(base));
        assert!(result.is_ok(), "should accept file under base: {result:?}");
    }

    // ── file_hash_sha256 ──────────────────────────────────────────────────────

    #[test]
    fn file_hash_sha256_produces_sha256_prefix() {
        let manifest = Path::new(env!("CARGO_MANIFEST_DIR")).join("Cargo.toml");
        let hash = file_hash_sha256(&manifest).expect("should hash Cargo.toml");
        assert!(hash.starts_with("sha256:"), "hash: {hash}");
        assert_eq!(hash.len(), 7 + 64); // "sha256:" + 64 hex chars
    }
}
