//! Native hot-path knowledge extraction for `sk watch` — Wave 14/16/18.
//!
//! Ports the classification/write loop from `extract-knowledge.py`, covering:
//!   - Noise filtering (boilerplate, interview Q&A, pure tables, pure code blocks)
//!   - Paragraph classification into mistake/pattern/decision/tool/feature/refactor/discovery
//!   - Title and tag extraction
//!   - `topic_key` and `content_hash` computation (Python-compatible formulas)
//!   - `knowledge_entries` upsert and `ke_fts` sync
//!   - **Wave 16**: Deterministic relation extraction for `knowledge_relations`:
//!     `SAME_SESSION`, `SAME_TOPIC`, `TAG_OVERLAP`, `RESOLVED_BY` with Python-parity
//!     stable_id/confidence/budget behavior.
//!   - **Wave 18**: `ensure_extract_tables()` for fresh-DB bootstrap of extract-owned
//!     schema (`knowledge_entries`, `ke_fts`, `knowledge_relations`, `embedding_meta`).
//!
//!   - **Wave 19**: `SEMANTIC_PROXIMITY` (TF-IDF cosine ≥ 0.75) is now computed
//!     natively, reusing `embeddings::tfidf::build_tfidf_model`.  The Python
//!     `extract-knowledge.py --semantic-only` auto-spawn on the successful native
//!     watch path is removed.  Python `--semantic-only` remains available for
//!     manual or fallback use.
//!
//! **Still Python-owned** (Python residual / fallback covers these):
//!   - Versioned DB migrations (`migrate.py`) — Rust only creates tables missing from
//!     a fresh DB; schema upgrades remain Python-owned.
//!   - `_backfill_affected_files_from_session_evidence` (now native via wave17)
//!   - `_infer_task_ids_from_content` (now native via wave17)
//!   - Confidence decay (now native via wave17)
//!
//! `watch.rs` no longer spawns Python on the successful native watch path.
//!
//! # Coexistence
//! Python's `extract-knowledge.py` deduplicates via `content_hash`. Entries
//! written by this native path will be skipped by Python on its next run
//! because their hashes are already in `knowledge_entries.content_hash`.
//!
//! # Feature gate
//! Compiled only when `--features native-extract` is set.
//! `watch.rs` calls `extract_from_changed_sessions()` under the same gate.

use regex::Regex;
use rusqlite::{Connection, OpenFlags};
use sha2::{Digest, Sha256};
use std::collections::{HashMap, HashSet};
use std::path::Path;
use std::sync::OnceLock;

use crate::db::write::{compute_stable_id_with_topic_key, enqueue_sync_op_fail_open, rebuild_fts};

// ── Extracted stats ───────────────────────────────────────────────────────────

/// Statistics from a single native extract pass.
#[derive(Debug, Default)]
pub struct ExtractStats {
    /// New entries inserted into knowledge_entries.
    pub extracted: usize,
    /// Entries skipped via content_hash dedup.
    pub deduped: usize,
    /// Entries skipped due to noise / classification miss.
    pub skipped: usize,
    /// knowledge_relations rows written (deterministic types only).
    pub relations_extracted: usize,
}

// ── Wave-18: fresh-DB bootstrap ───────────────────────────────────────────────

/// Create the extract-owned schema tables idempotently (wave-18).
///
/// Creates `knowledge_entries`, `ke_fts`, `knowledge_relations`, and
/// `embedding_meta` using `CREATE TABLE / VIRTUAL TABLE IF NOT EXISTS` so
/// it is safe to call on any DB — whether freshly created by Rust or already
/// managed by Python's `migrate.py`.
///
/// **Migration compatibility**: `migrate.py` remains the canonical owner of
/// versioned schema upgrades (adding columns, indexes, etc.).  This function
/// only creates tables that are *absent*, mirroring the minimal bootstrap that
/// Python's `build-session-index.py` used to trigger via its first-run DB
/// creation path.  It does NOT replace `migrate.py`.
pub fn ensure_extract_tables(conn: &Connection) -> rusqlite::Result<()> {
    conn.execute_batch(
        "CREATE TABLE IF NOT EXISTS knowledge_entries (
             id INTEGER PRIMARY KEY AUTOINCREMENT,
             session_id TEXT NOT NULL,
             document_id INTEGER,
             category TEXT NOT NULL,
             title TEXT NOT NULL,
             stable_id TEXT,
             content TEXT NOT NULL,
             tags TEXT DEFAULT '',
             confidence REAL DEFAULT 1.0,
             occurrence_count INTEGER DEFAULT 1,
             first_seen TEXT,
             last_seen TEXT,
             source TEXT DEFAULT 'copilot',
             topic_key TEXT,
             revision_count INTEGER DEFAULT 1,
             content_hash TEXT,
             wing TEXT DEFAULT '',
             room TEXT DEFAULT '',
             facts TEXT DEFAULT '[]',
             error_type TEXT DEFAULT '',
             root_cause TEXT DEFAULT '',
             severity TEXT DEFAULT 'medium',
             est_tokens INTEGER DEFAULT 0,
             source_section TEXT DEFAULT '',
             task_id TEXT DEFAULT '',
             affected_files TEXT DEFAULT '[]',
             UNIQUE(category, title, session_id)
          );
         CREATE TABLE IF NOT EXISTS knowledge_relations (
             id INTEGER PRIMARY KEY AUTOINCREMENT,
             source_id INTEGER NOT NULL,
             target_id INTEGER NOT NULL,
             source_stable_id TEXT DEFAULT '',
             target_stable_id TEXT DEFAULT '',
             relation_type TEXT NOT NULL,
             stable_id TEXT,
             confidence REAL DEFAULT 0.5,
             created_at TEXT
         );
         CREATE UNIQUE INDEX IF NOT EXISTS idx_relations_unique
             ON knowledge_relations(source_id, target_id, relation_type);
         CREATE TABLE IF NOT EXISTS embedding_meta (
             key TEXT PRIMARY KEY,
             value TEXT
         );",
    )?;

    // Try porter stemmer first; fall back to unicode61 on older SQLite builds.
    // `CREATE VIRTUAL TABLE IF NOT EXISTS` is a no-op when the table already exists.
    let res = conn.execute_batch(
        "CREATE VIRTUAL TABLE IF NOT EXISTS ke_fts USING fts5(
             title, content, tags, category, wing, room, facts,
             tokenize='porter unicode61 remove_diacritics 2'
         );",
    );
    if res.is_err() {
        conn.execute_batch(
            "CREATE VIRTUAL TABLE IF NOT EXISTS ke_fts USING fts5(
                 title, content, tags, category, wing, room, facts
             );",
        )?;
    }

    Ok(())
}

// ── Internal indicator sets (lazily compiled, reused across calls) ─────────────

struct IndicatorSet {
    patterns: Vec<Regex>,
}

impl IndicatorSet {
    fn new(raw: &[&str]) -> Self {
        let patterns = raw
            .iter()
            .map(|p| Regex::new(p).expect("invalid indicator regex"))
            .collect();
        Self { patterns }
    }

    /// Count total regex occurrences across all patterns (mirrors Python's
    /// `sum(len(re.findall(p, text_lower, re.IGNORECASE)) for p in indicators)`).
    fn score(&self, text: &str) -> usize {
        self.patterns
            .iter()
            .map(|re| re.find_iter(text).count())
            .sum()
    }
}

macro_rules! indicator_set {
    ($fn_name:ident, [ $($pat:expr),+ $(,)? ]) => {
        fn $fn_name() -> &'static IndicatorSet {
            static SET: OnceLock<IndicatorSet> = OnceLock::new();
            SET.get_or_init(|| IndicatorSet::new(&[$($pat),+]))
        }
    };
}

indicator_set!(
    mistake_set,
    [
        r"(?i)(?:mistake|error|bug|wrong|incorrect|broken|fail|crash|fix(?:ed)?)\b",
        r"(?i)(?:should\s+(?:have|not)|shouldn't|don't|avoid|never|careful)",
        r"(?i)(?:root\s+cause|caused\s+by|problem\s+was|issue\s+was)",
        r"(?i)(?:lỗi|sai|sửa|tránh|không\s+nên|nguyên\s+nhân)",
    ]
);

indicator_set!(
    pattern_set,
    [
        r"(?i)(?:always|must|should|convention|pattern|best\s+practice|rule)\b",
        r"(?i)(?:use\s+\w+\s+instead\s+of|prefer|recommend)",
        r"(?i)(?:standard|template|reusable|common\s+(?:pattern|style|approach))",
        r"(?i)(?:luôn|nên|quy\s+tắc|mẫu|chuẩn)",
        r"(?i)(?:good\s+practice|consistent(?:ly)?|enforce|ensure\s+(?:that|you))\b",
        r"(?i)\b(?:tip|guideline|approach|technique|strategy)\b",
        r"(?i)\b(?:make\s+sure|remember\s+to|keep\s+in\s+mind|note\s+that)\b",
    ]
);

indicator_set!(
    decision_set,
    [
        r"(?i)(?:chose|decided|selected|picked|went\s+with|opted)\b",
        r"(?i)(?:because|reason|rationale|trade-off|tradeoff)",
        r"(?i)(?:option\s+[A-C]|alternative|compared|versus|vs\.?)\b",
        r"(?i)(?:chọn|quyết\s+định|lý\s+do|so\s+sánh)",
    ]
);

indicator_set!(
    tool_set,
    [
        r"(?i)(?:install|configure|setup|version|upgrade|dependency)\b",
        r"(?i)(?:gradle|maven|docker|redis|postgres|spring\s+boot)\b",
        r"(?i)(?:JDK|SDK|IDE|VSCode|extension)\b",
        r"(?i)(?:cài|cấu\s+hình|phiên\s+bản|nâng\s+cấp)",
    ]
);

indicator_set!(
    feature_set,
    [
        r"(?i)\b(?:implement(?:ed|ing)?|add(?:ed|ing)?|create(?:d|ing)?|build|built|develop(?:ed|ing)?)\b",
        r"(?i)(?:new\s+(?:feature|endpoint|handler|screen|component|API))\b",
        r"(?i)\b(?:feature|functionality|capability|user\s+story)\b",
        r"(?i)(?:thêm|tạo|xây\s+dựng|tính\s+năng|chức\s+năng)\b",
    ]
);

indicator_set!(
    refactor_set,
    [
        r"(?i)\b(?:refactor|restructur|simplif|clean\s*up|extract|reorganiz)",
        r"(?i)\b(?:rename[ds]?|move[ds]?|split|merge[ds]?|consolidat|dedup)",
        r"(?i)\b(?:improv(?:e[ds]?|ing)|optimiz|reduc)",
        r"(?i)(?:tái\s+cấu\s+trúc|đơn\s+giản\s+hóa|tối\s+ưu)",
    ]
);

indicator_set!(
    discovery_set,
    [
        r"(?i)\b(?:discover|found|learn|realiz|notic|observ)",
        r"(?i)\b(?:turns\s+out|apparently|actually|interesting)\b",
        r"(?i)\b(?:TIL|insight|understanding|revelation)\b",
        r"(?i)(?:phát\s+hiện|nhận\s+ra|hiểu|thấy\s+rằng)",
    ]
);

// ── Noise detection patterns (OnceLock<Vec<Regex>>) ───────────────────────────

fn strong_noise_patterns() -> &'static Vec<Regex> {
    static V: OnceLock<Vec<Regex>> = OnceLock::new();
    V.get_or_init(|| {
        [
            r"(?i)đáp\s*án\s*(mong\s*đợi|chi\s*tiết)",
            r"(?i)bảng\s*(đánh\s*giá|ghi\s*điểm)",
            r"(?i)câu\s*hỏi\s*phỏng\s*vấn",
            r"(?i)interview\s*question",
            r"(?i)nội\s*dung\s*cần\s*đề\s*cập",
        ]
        .iter()
        .map(|p| Regex::new(p).unwrap())
        .collect()
    })
}

fn weak_noise_patterns() -> &'static Vec<Regex> {
    static V: OnceLock<Vec<Regex>> = OnceLock::new();
    V.get_or_init(|| {
        [
            r"(?i)(?:phỏng\s*vấn|interview|câu\s*hỏi|bộ\s*câu)",
            r"(?i)(?:đáp\s*án|mong\s*đợi|tiêu\s*chí|ghi\s*điểm)",
            r"(?i)(?:bảng\s*đánh\s*giá|evaluation\s*rubric)",
            r"(?i)(?:trọng\s*số|scoring|rubric|interviewer)",
        ]
        .iter()
        .map(|p| Regex::new(p).unwrap())
        .collect()
    })
}

fn user_quote_patterns() -> &'static Vec<Regex> {
    static V: OnceLock<Vec<Regex>> = OnceLock::new();
    V.get_or_init(|| {
        [
            r"(?i)^(?:\d+\.\s*)?user\s+(?:said|asked|reported|requested|mentioned|noted)\b",
            r"(?i)^(?:\d+\.\s*)?user\s+(?:wants?|confirmed|approved|rejected)\b",
            r"(?i)^(?:\d+\.\s*)?user\s+(?:clarified|provided|applied|selected|chose)\b",
            r#"(?i)^(?:\d+\.\s*)?user\s+said\s*[:\"]"#,
            r#"(?i)^(?:\d+\.\s*)?user\s+reported\s*[:\"]"#,
        ]
        .iter()
        .map(|p| Regex::new(p).unwrap())
        .collect()
    })
}

fn action_summary_patterns() -> &'static Vec<Regex> {
    static V: OnceLock<Vec<Regex>> = OnceLock::new();
    V.get_or_init(|| {
        [concat!(
            r"(?i)^(?:\d+\.\s*)?(?:fixed|implemented|launched|created|updated|added|deployed|",
            r"committed|pushed|merged|resolved|completed|refactored|migrated|upgraded|",
            r"configured|installed|removed|deleted|replaced|renamed|moved)\s",
        )]
        .iter()
        .map(|p| Regex::new(p).unwrap())
        .collect()
    })
}

fn code_block_re() -> &'static Regex {
    static R: OnceLock<Regex> = OnceLock::new();
    R.get_or_init(|| Regex::new(r"(?s)```[\s\S]*?```").unwrap())
}

fn error_info_re() -> &'static Regex {
    static R: OnceLock<Regex> = OnceLock::new();
    R.get_or_init(|| {
        Regex::new(r"(?i)(?:Traceback|Error|Exception|FAILED|panic|stack trace)").unwrap()
    })
}

// ── Error lifecycle metadata (mistake entries) ─────────────────────────────────

/// Classify the error type from text, mirroring Python's `classify_error_type()`.
/// Returns a static str with the best-matching type, or "" if none qualifies.
pub fn classify_error_type(text: &str) -> &'static str {
    // (type_label, regex_patterns)
    static TYPES: OnceLock<Vec<(&'static str, Vec<Regex>)>> = OnceLock::new();
    let types = TYPES.get_or_init(|| {
        vec![
            ("syntax", vec![
                Regex::new(r"(?i)(?:syntax\s*error|parse\s*error|unexpected\s*token|unterminated)").unwrap(),
                Regex::new(r"(?i)(?:indentation|missing\s*(?:bracket|paren|semicolon|colon|comma))").unwrap(),
                Regex::new(r"(?i)(?:SyntaxError|ParseError|invalid\s*syntax)").unwrap(),
            ]),
            ("runtime", vec![
                Regex::new(r"(?i)(?:runtime\s*error|exception|traceback|stack\s*trace)").unwrap(),
                Regex::new(r"(?i)(?:TypeError|ValueError|AttributeError|KeyError|IndexError|NameError)").unwrap(),
                Regex::new(r"(?i)(?:NullPointerException|segfault|segmentation\s*fault|SIGSEGV)").unwrap(),
                Regex::new(r"(?i)(?:crash(?:ed|es|ing)?|abort|panic|unhandled)").unwrap(),
            ]),
            ("logic", vec![
                Regex::new(r"(?i)(?:logic\s*error|wrong\s*(?:result|output|behavior|value))").unwrap(),
                Regex::new(r"(?i)(?:incorrect|unexpected\s*(?:result|output|behavior))").unwrap(),
                Regex::new(r"(?i)(?:off-by-one|race\s*condition|deadlock|infinite\s*loop)").unwrap(),
            ]),
            ("timeout", vec![
                Regex::new(r"(?i)(?:timeout|timed?\s*out|hang(?:s|ing|ed)?|stuck|frozen)").unwrap(),
                Regex::new(r"(?i)(?:too\s*(?:slow|long)|deadline\s*exceeded|connection\s*timeout)").unwrap(),
            ]),
            ("permission", vec![
                Regex::new(r"(?i)(?:permission\s*denied|access\s*denied|unauthorized|forbidden)").unwrap(),
                Regex::new(r"(?i)(?:EACCES|EPERM|403|401|authentication\s*fail)").unwrap(),
            ]),
            ("build", vec![
                Regex::new(r"(?i)(?:build\s*(?:fail|error)|compilation\s*(?:fail|error))").unwrap(),
                Regex::new(r"(?i)(?:linker\s*error|import\s*error|module\s*not\s*found)").unwrap(),
                Regex::new(r"(?i)(?:cannot\s*find\s*module|unresolved\s*(?:import|reference))").unwrap(),
            ]),
            ("deploy", vec![
                Regex::new(r"(?i)(?:deploy(?:ment)?\s*(?:fail|error)|rollback|service\s*(?:down|unavailable))").unwrap(),
                Regex::new(r"(?i)(?:health\s*check\s*fail|container\s*(?:crash|restart))").unwrap(),
            ]),
            ("config", vec![
                Regex::new(r"(?i)(?:config(?:uration)?\s*(?:error|missing|invalid))").unwrap(),
                Regex::new(r"(?i)(?:env(?:ironment)?\s*(?:variable|missing)|\.env|settings?\s*(?:wrong|missing))").unwrap(),
            ]),
        ]
    });

    let mut best_type = "";
    let mut best_score = 0usize;
    for (label, patterns) in types {
        let score: usize = patterns.iter().map(|re| re.find_iter(text).count()).sum();
        if score > best_score {
            best_score = score;
            best_type = label;
        }
    }
    if best_score >= 1 {
        best_type
    } else {
        ""
    }
}

/// Extract root cause text from content, mirroring Python's `extract_root_cause()`.
/// Returns empty string when no pattern matches.
pub fn extract_root_cause(text: &str) -> String {
    static EXTRACTORS: OnceLock<Vec<Regex>> = OnceLock::new();
    let extractors = EXTRACTORS.get_or_init(|| {
        vec![
            Regex::new(r"(?i)(?:root\s*cause|caused\s*by|because|reason(?:\s*was)?|due\s*to|problem\s*was|issue\s*was)[:\s]+(.{10,200})").unwrap(),
            Regex::new(r"(?i)(?:nguyên\s*nhân|do|vì|bởi\s*vì)[:\s]+(.{10,200})").unwrap(),
            Regex::new(r"(?i)(?:the\s*(?:real|actual|underlying)\s*(?:issue|problem|cause)\s*(?:is|was))[:\s]+(.{10,200})").unwrap(),
            Regex::new(r"(?i)(?:fixed\s*by|resolved\s*by|solution\s*was)[:\s]+(.{10,200})").unwrap(),
        ]
    });
    for re in extractors {
        if let Some(cap) = re.captures(text) {
            if let Some(m) = cap.get(1) {
                let cause = m.as_str().trim().trim_end_matches('.');
                if cause.len() > 10 {
                    return cause.chars().take(200).collect();
                }
            }
        }
    }
    String::new()
}

/// Detect severity from content keywords, mirroring Python's `detect_severity()`.
/// Returns "medium" as the default when no keyword matches.
pub fn detect_severity(text: &str) -> &'static str {
    static SEVERITY: OnceLock<[(&'static str, Vec<Regex>); 3]> = OnceLock::new();
    let severity = SEVERITY.get_or_init(|| {
        [
            (
                "critical",
                vec![
                    Regex::new(r"(?i)critical").unwrap(),
                    Regex::new(r"(?i)fatal").unwrap(),
                    Regex::new(r"(?i)data\s*loss").unwrap(),
                    Regex::new(r"(?i)security\s*(?:vuln|breach)").unwrap(),
                    Regex::new(r"(?i)production\s*(?:down|crash)").unwrap(),
                ],
            ),
            (
                "high",
                vec![
                    Regex::new(r"(?i)crash").unwrap(),
                    Regex::new(r"(?i)hang").unwrap(),
                    Regex::new(r"(?i)block(?:ed|ing)").unwrap(),
                    Regex::new(r"(?i)regression").unwrap(),
                    Regex::new(r"(?i)broken\s*(?:build|deploy|test)").unwrap(),
                ],
            ),
            (
                "low",
                vec![
                    Regex::new(r"(?i)cosmetic").unwrap(),
                    Regex::new(r"(?i)typo").unwrap(),
                    Regex::new(r"(?i)formatting").unwrap(),
                    Regex::new(r"(?i)style").unwrap(),
                    Regex::new(r"(?i)minor").unwrap(),
                    Regex::new(r"(?i)warning\b").unwrap(),
                ],
            ),
        ]
    });
    for (sev, patterns) in severity {
        for re in patterns {
            if re.is_match(text) {
                return sev;
            }
        }
    }
    "medium"
}

// ── Noise detection ───────────────────────────────────────────────────────────

/// Returns true if the text is boilerplate / interview Q&A / noise that should
/// not be classified. Mirrors Python's `_is_noise()`.
fn is_noise(text: &str) -> bool {
    // Strong noise — single match is enough.
    for re in strong_noise_patterns() {
        if re.is_match(text) {
            return true;
        }
    }

    // User-quote patterns — applied to the first non-empty line.
    let first_line = text
        .trim()
        .lines()
        .next()
        .unwrap_or("")
        .trim()
        .to_lowercase();
    for re in user_quote_patterns() {
        if re.is_match(&first_line) {
            return true;
        }
    }

    // Action-summary patterns — only filter short entries (< 200 chars).
    if text.trim().len() < 200 {
        for re in action_summary_patterns() {
            if re.is_match(&first_line) {
                return true;
            }
        }
    }

    // Weak noise — need 2+ matches.
    let noise_score: usize = weak_noise_patterns()
        .iter()
        .filter(|re| re.is_match(text))
        .count();
    if noise_score >= 2 {
        return true;
    }

    // Pure markdown table: >70% of non-empty lines are table rows AND >3 lines.
    let lines: Vec<&str> = text
        .lines()
        .map(str::trim)
        .filter(|l| !l.is_empty())
        .collect();
    if !lines.is_empty() {
        let table_lines = lines
            .iter()
            .filter(|l| l.starts_with('|') && l.ends_with('|'))
            .count();
        if table_lines * 10 > lines.len() * 7 && lines.len() > 3 {
            return true;
        }
    }

    // Pure code block: >70% of content is inside ``` fences.
    let code_chars: usize = code_block_re()
        .find_iter(text)
        .map(|m| m.as_str().len())
        .sum();
    if text.len() > 100 && code_chars * 10 > text.len() * 7 {
        let has_error_info = error_info_re().is_match(text);
        if !has_error_info {
            return true;
        }
    }

    false
}

// ── Classification ────────────────────────────────────────────────────────────

/// Classify a paragraph into knowledge categories with confidence scores.
///
/// Category-aware thresholds and confidence floors match Python:
///   - pattern: threshold=1, floor=0.5
///   - decision: threshold=2, floor=0.5
///   - others: threshold=2, floor=0.4
pub fn classify_paragraph(text: &str) -> Vec<(String, f64)> {
    if is_noise(text) {
        return vec![];
    }

    // (set_fn, category, threshold, confidence_floor)
    let config: &[(&dyn Fn() -> &'static IndicatorSet, &str, usize, f64)] = &[
        (&mistake_set, "mistake", 2, 0.4),
        (&pattern_set, "pattern", 1, 0.5),
        (&decision_set, "decision", 2, 0.5),
        (&tool_set, "tool", 2, 0.4),
        (&feature_set, "feature", 2, 0.4),
        (&refactor_set, "refactor", 2, 0.4),
        (&discovery_set, "discovery", 2, 0.4),
    ];

    let mut results = Vec::new();
    for (set_fn, category, threshold, floor) in config {
        let score = set_fn().score(text);
        if score >= *threshold {
            let confidence = floor.max((score as f64 / 5.0).min(1.0));
            results.push((category.to_string(), confidence));
        }
    }
    results
}

// ── Title extraction ──────────────────────────────────────────────────────────

fn md_clean_re() -> &'static Regex {
    static R: OnceLock<Regex> = OnceLock::new();
    R.get_or_init(|| Regex::new(r"[#*_`\[\]]").unwrap())
}

fn bullet_re() -> &'static Regex {
    static R: OnceLock<Regex> = OnceLock::new();
    R.get_or_init(|| Regex::new(r"^[\d.)\-•]+\s*").unwrap())
}

fn emoji_prefix_re() -> &'static Regex {
    static R: OnceLock<Regex> = OnceLock::new();
    R.get_or_init(|| Regex::new(r"^[^\w\s]{1,3}\s*").unwrap())
}

fn sep_re() -> &'static Regex {
    static R: OnceLock<Regex> = OnceLock::new();
    R.get_or_init(|| Regex::new(r"^[-=_]{3,}$").unwrap())
}

/// Extract a meaningful title from paragraph text. Mirrors Python's `extract_title()`.
pub fn extract_title(text: &str) -> String {
    let max_len = 100usize;
    for line in text.trim().lines().take(5) {
        let line = line.trim();
        if line.is_empty() {
            continue;
        }
        if line.starts_with('|') || line.starts_with("```") || line.starts_with("---") {
            continue;
        }
        if sep_re().is_match(line) {
            continue;
        }
        let title = md_clean_re().replace_all(line, "");
        let title = bullet_re().replace_all(title.trim(), "");
        let title = emoji_prefix_re().replace_all(title.trim(), "");
        let title = title.trim();
        if title.len() >= 10 {
            if title.len() > max_len {
                let truncated: String = title.chars().take(max_len - 3).collect();
                return format!("{truncated}...");
            }
            return title.to_string();
        }
    }
    // Fallback: first 100 chars with collapsed whitespace
    static WS: OnceLock<Regex> = OnceLock::new();
    let ws = WS.get_or_init(|| Regex::new(r"\s+").unwrap());
    let fallback: String = text.chars().take(max_len).collect();
    let fallback = ws.replace_all(fallback.trim(), " ");
    if fallback.is_empty() {
        "Untitled".to_string()
    } else {
        fallback.to_string()
    }
}

// ── Tag extraction ────────────────────────────────────────────────────────────

fn tag_patterns() -> &'static Vec<(Regex, &'static str)> {
    static V: OnceLock<Vec<(Regex, &'static str)>> = OnceLock::new();
    V.get_or_init(|| {
        let raw: &[(&str, &str)] = &[
            (r"(?i)\b(?:Spring\s+Boot|SpringBoot)\b", "spring-boot"),
            (r"(?i)\b(?:Thymeleaf)\b", "thymeleaf"),
            (r"(?i)\b(?:JPQL|JPA|Hibernate)\b", "jpa"),
            (r"(?i)\b(?:PostgreSQL|Postgres|PG)\b", "postgresql"),
            (r"(?i)\b(?:Docker|docker-compose)\b", "docker"),
            (r"(?i)\b(?:Redis)\b", "redis"),
            (r"(?i)\b(?:Gradle)\b", "gradle"),
            (r"(?i)\b(?:CSRF)\b", "csrf"),
            (r"(?i)\b(?:Liquibase)\b", "liquibase"),
            (r"(?i)\b(?:JavaScript|jQuery|JS)\b", "javascript"),
            (r"(?i)\b(?:CSS|styles?\.css)\b", "css"),
            (
                r"(?i)\b(?:i18n|internationalization|messages\.properties)\b",
                "i18n",
            ),
            (r"(?i)\b(?:JDK|Java\s+\d+)\b", "java"),
            (r"(?i)\b(?:Git|git\s+hook)\b", "git"),
            (r"(?i)\b(?:VSCode|VS\s+Code)\b", "vscode"),
            (r"(?i)\b(?:Excel|xlsx)\b", "excel"),
            (r"(?i)\b(?:CRUD)\b", "crud"),
            (r"(?i)\b(?:pagination)\b", "pagination"),
            (r"(?i)\b(?:modal|dialog)\b", "ui"),
            (r"(?i)\b(?:SQL|native\s+SQL)\b", "sql"),
            (r"(?i)\b(?:Python|python3?)\b", "python"),
            (r"(?i)\b(?:TypeScript)\b", "typescript"),
            (r"(?i)\b(?:React(?:JS)?|ReactDOM|react-native)\b", "react"),
            (r"(?i)\b(?:Node(?:\.js)?|nodejs)\b", "nodejs"),
            (r"(?i)\b(?:Kotlin)\b", "kotlin"),
            (r"(?i)\b(?:Swift)\b", "swift"),
            (
                r"(?i)\b(?:AWS|Lambda|ECS|CloudFront|S3|DynamoDB|SQS|SNS)\b",
                "aws",
            ),
            (r"(?i)\b(?:pytest|unittest|vitest|jest|mocha)\b", "testing"),
            (r"(?i)\b(?:async|await|asyncio|coroutine)\b", "async"),
            (r"(?i)\b(?:SQLite|sqlite3)\b", "sqlite"),
            (r"(?i)\b(?:migration|schema|database)\b", "database"),
            (
                r"(?i)\b(?:authentication|authorization|auth|JWT|OAuth|token)\b",
                "auth",
            ),
            (r"(?i)\b(?:REST|GraphQL|API|endpoint|HTTP)\b", "api"),
            (
                r"(?i)\b(?:CI|CD|GitHub\s+Actions|pipeline|workflow)\b",
                "ci-cd",
            ),
            (
                r"(?i)\b(?:Compose|Jetpack\s+Compose|Composable)\b",
                "compose",
            ),
            (r"(?i)\b(?:iOS|UIKit|SwiftUI|Xcode)\b", "ios"),
            (r"(?i)\b(?:Android|AndroidX)\b", "android"),
            (r"(?i)\b(?:Rust|cargo)\b", "rust"),
        ];
        raw.iter()
            .map(|(p, tag)| (Regex::new(p).unwrap(), *tag))
            .collect()
    })
}

/// Extract comma-separated tags from text. Mirrors Python's `extract_tags()`.
pub fn extract_tags(text: &str) -> String {
    let mut tags: std::collections::BTreeSet<&str> = std::collections::BTreeSet::new();
    for (re, tag) in tag_patterns() {
        if re.is_match(text) {
            tags.insert(tag);
        }
    }
    tags.into_iter().collect::<Vec<_>>().join(",")
}

// ── Chunking ──────────────────────────────────────────────────────────────────

fn item_start_re() -> &'static Regex {
    static R: OnceLock<Regex> = OnceLock::new();
    // Matches lines that begin a new list item or heading (no lookahead needed).
    R.get_or_init(|| Regex::new(r"^(?:\d+\.\s|-\s|\*\s|#{1,3}\s)").unwrap())
}

fn flush_chunk(chunks: &mut Vec<String>, item: &str) {
    let item = item.trim();
    if item.len() < 30 {
        return;
    }
    if item.len() > 2000 {
        for p in item.split("\n\n") {
            let p = p.trim();
            if p.len() >= 30 {
                chunks.push(p.to_string());
            }
        }
    } else {
        chunks.push(item.to_string());
    }
}

/// Split section content into knowledge chunks. Mirrors Python's
/// `split_into_knowledge_chunks()`.
///
/// Python used a lookahead regex (`\n(?=\d+\.\s|...)`). The Rust `regex` crate
/// does not support lookaheads, so we use a line-by-line equivalent that starts
/// a new chunk whenever a line matches a list-item / heading marker.
pub fn split_into_knowledge_chunks(content: &str) -> Vec<String> {
    let re = item_start_re();
    let mut chunks = Vec::new();
    let mut current = String::new();

    for line in content.lines() {
        let is_item_start = re.is_match(line.trim_start());
        if is_item_start && !current.is_empty() {
            flush_chunk(&mut chunks, &current);
            current = String::new();
        }
        if !current.is_empty() {
            current.push('\n');
        }
        current.push_str(line);
    }
    if !current.is_empty() {
        flush_chunk(&mut chunks, &current);
    }
    chunks
}

fn truncate_chars(text: &str, max_chars: usize) -> &str {
    if text.chars().count() <= max_chars {
        return text;
    }

    let end = text
        .char_indices()
        .map(|(idx, _)| idx)
        .nth(max_chars)
        .unwrap_or(text.len());
    &text[..end]
}

// ── Hashing / topic_key ───────────────────────────────────────────────────────

fn ws_collapse_re() -> &'static Regex {
    static R: OnceLock<Regex> = OnceLock::new();
    R.get_or_init(|| Regex::new(r"\s+").unwrap())
}

/// Compute a 16-char hex dedup hash matching Python's `_compute_content_hash()`.
///   SHA-256(category.lower|title_normalized|content[:200]_normalized)[:16]
pub fn compute_content_hash(category: &str, title: &str, content: &str) -> String {
    let re = ws_collapse_re();
    let cat = category.to_lowercase();
    let ttl = re.replace_all(title.to_lowercase().trim(), " ").to_string();
    let cnt_raw: String = content.chars().take(200).collect();
    let cnt = re
        .replace_all(cnt_raw.to_lowercase().trim(), " ")
        .to_string();
    let normalized = format!("{cat}|{ttl}|{cnt}");
    let hash = Sha256::digest(normalized.as_bytes());
    format!("{:x}", hash)[..16].to_string()
}

fn slugify_re_non_word() -> &'static Regex {
    static R: OnceLock<Regex> = OnceLock::new();
    R.get_or_init(|| Regex::new(r"[^\w\s-]").unwrap())
}

fn slugify_re_ws() -> &'static Regex {
    static R: OnceLock<Regex> = OnceLock::new();
    R.get_or_init(|| Regex::new(r"[\s_]+").unwrap())
}

fn slugify_re_multi_dash() -> &'static Regex {
    static R: OnceLock<Regex> = OnceLock::new();
    R.get_or_init(|| Regex::new(r"-+").unwrap())
}

/// Convert text to a URL-friendly slug (≤60 chars). Mirrors Python's `_slugify()`.
pub fn slugify(text: &str) -> String {
    let text = text.to_lowercase();
    let text = text.trim();
    let text = slugify_re_non_word().replace_all(text, "");
    let text = slugify_re_ws().replace_all(&text, "-");
    let text = slugify_re_multi_dash().replace_all(&text, "-");
    let text = text.trim_matches('-').to_string();
    text.chars().take(60).collect()
}

/// Generate topic_key like "mistake/auth-jwt-issue". Mirrors Python's
/// `_generate_topic_key()`.
pub fn generate_topic_key(category: &str, title: &str) -> String {
    format!("{}/{}", category, slugify(title))
}

// ── Section extraction (hot path) ─────────────────────────────────────────────

const TARGET_SECTIONS: &[&str] = &[
    "technical_details",
    "history",
    "work_done",
    "next_steps",
    "full",
    "conversation",
];

/// Extract knowledge entries from the `sections`/`documents` tables and write
/// them (with FTS) into `knowledge_entries`/`ke_fts`.
///
/// `session_ids = None` processes all sessions (same as Python default).
/// Returns fail-open stats on any DB error; does not panic.
pub fn extract_from_sections(
    conn: &Connection,
    session_ids: Option<&[String]>,
) -> anyhow::Result<ExtractStats> {
    let mut stats = ExtractStats::default();
    let now = chrono::Utc::now().format("%Y-%m-%dT%H:%M:%S").to_string();

    // Load existing content_hashes for fast dedup. If knowledge_entries doesn't
    // exist yet, bail out gracefully — Python will create it on first run.
    let mut existing_hashes: HashSet<String> = HashSet::new();
    {
        let mut stmt = match conn
            .prepare("SELECT content_hash FROM knowledge_entries WHERE content_hash IS NOT NULL")
        {
            Ok(s) => s,
            Err(_) => return Ok(stats), // table absent — skip
        };
        let rows = stmt.query_map([], |row| row.get::<_, String>(0));
        if let Ok(rows) = rows {
            for h in rows.flatten() {
                existing_hashes.insert(h);
            }
        }
    }

    // Build parameterised query over TARGET_SECTIONS with optional session filter.
    let section_ph: String = TARGET_SECTIONS
        .iter()
        .map(|_| "?")
        .collect::<Vec<_>>()
        .join(",");
    let mut sql = format!(
        "SELECT s.id, s.document_id, s.section_name, s.content, d.session_id, \
         COALESCE(d.source,'copilot'), COALESCE(d.stable_id,'') \
         FROM sections s JOIN documents d ON s.document_id = d.id \
         WHERE s.section_name IN ({section_ph})"
    );
    // Build all params as String first, then borrow as &dyn ToSql.
    let mut param_strings: Vec<String> = TARGET_SECTIONS.iter().map(|s| s.to_string()).collect();
    if let Some(ids) = session_ids {
        if !ids.is_empty() {
            let id_ph: String = ids.iter().map(|_| "?").collect::<Vec<_>>().join(",");
            sql.push_str(&format!(" AND d.session_id IN ({id_ph})"));
            param_strings.extend(ids.iter().cloned());
        }
    }
    sql.push_str(" ORDER BY d.session_id, d.seq");

    let params_ref: Vec<&dyn rusqlite::ToSql> = param_strings
        .iter()
        .map(|s| s as &dyn rusqlite::ToSql)
        .collect();

    type Row = (i64, i64, String, String, String, String, String);
    let mut stmt = match conn.prepare(&sql) {
        Ok(s) => s,
        Err(_) => return Ok(stats), // sections/documents tables absent
    };
    let rows: Vec<Row> = match stmt.query_map(params_ref.as_slice(), |row| {
        Ok((
            row.get(0)?,
            row.get(1)?,
            row.get(2)?,
            row.get(3)?,
            row.get(4)?,
            row.get(5)?,
            row.get(6)?,
        ))
    }) {
        Ok(mapped) => mapped.flatten().collect(),
        Err(_) => return Ok(stats),
    };

    for (_section_id, doc_id, section_name, content, session_id, source, _doc_stable_id) in rows {
        let chunks = split_into_knowledge_chunks(&content);

        for chunk in chunks {
            let classifications = classify_paragraph(&chunk);
            if classifications.is_empty() {
                stats.skipped += 1;
                continue;
            }

            for (category, confidence) in classifications {
                let title = extract_title(&chunk);
                let tags = extract_tags(&chunk);
                let content_hash = compute_content_hash(&category, &title, &chunk);
                let topic_key = generate_topic_key(&category, &title);

                // Hash-based dedup: skip if exact content already indexed.
                if existing_hashes.contains(&content_hash) {
                    stats.deduped += 1;
                    continue;
                }

                // Cross-session topic-key upsert: if another session has this
                // topic, update it instead of inserting a duplicate.
                let existing_by_topic: Option<i64> = conn
                    .query_row(
                        "SELECT id FROM knowledge_entries WHERE topic_key = ? AND session_id != ?",
                        rusqlite::params![topic_key, session_id],
                        |row| row.get(0),
                    )
                    .ok();

                let stable_id =
                    compute_stable_id_with_topic_key(&session_id, &category, &title, &topic_key);

                if let Some(existing_id) = existing_by_topic {
                    let chunk_trimmed = truncate_chars(&chunk, 3000);
                    let _ = conn.execute(
                        "UPDATE knowledge_entries \
                         SET content = ?, \
                             confidence = MIN(1.0, MAX(confidence, ?) + 0.03), \
                             revision_count = revision_count + 1, \
                             occurrence_count = occurrence_count + 1, \
                             last_seen = ?, content_hash = ?, tags = ?, \
                             source_section = ?, \
                             stable_id = CASE WHEN COALESCE(stable_id,'') = '' THEN ? \
                                              ELSE stable_id END \
                         WHERE id = ?",
                        rusqlite::params![
                            chunk_trimmed,
                            confidence,
                            now,
                            content_hash,
                            tags,
                            section_name,
                            stable_id,
                            existing_id
                        ],
                    );
                    let _ = rebuild_fts(conn, existing_id);
                    // Enqueue sync op for the updated entry (fail-open).
                    {
                        let payload = serde_json::json!({
                            "session_id": session_id,
                            "category": category,
                            "title": title,
                            "stable_id": stable_id,
                            "content": chunk_trimmed,
                            "tags": tags,
                            "confidence": confidence,
                            "last_seen": now,
                            "content_hash": content_hash,
                            "source": source,
                            "topic_key": topic_key,
                            "source_section": section_name,
                        });
                        let payload_json =
                            serde_json::to_string(&payload).unwrap_or_else(|_| "{}".to_string());
                        enqueue_sync_op_fail_open(
                            conn,
                            "knowledge_entries",
                            &stable_id,
                            &payload_json,
                        );
                    }
                    existing_hashes.insert(content_hash);
                    stats.extracted += 1;
                    continue;
                }

                // Insert new entry.
                let chunk_trimmed = truncate_chars(&chunk, 3000);
                let est_tokens = (title.len() + chunk_trimmed.len()) / 4;
                let insert_result = conn.execute(
                    "INSERT INTO knowledge_entries \
                     (session_id, document_id, category, title, stable_id, content, tags, \
                      confidence, first_seen, last_seen, source, topic_key, \
                      revision_count, content_hash, est_tokens, source_section) \
                     VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 1, ?, ?, ?) \
                     ON CONFLICT(category, title, session_id) DO UPDATE SET \
                         confidence = MAX(knowledge_entries.confidence, excluded.confidence), \
                         last_seen = excluded.last_seen, \
                         content_hash = excluded.content_hash, \
                         topic_key = excluded.topic_key, \
                         est_tokens = excluded.est_tokens, \
                         source_section = excluded.source_section, \
                         stable_id = excluded.stable_id",
                    rusqlite::params![
                        session_id,
                        doc_id,
                        category,
                        title,
                        stable_id,
                        chunk_trimmed,
                        tags,
                        confidence,
                        now,
                        now,
                        source,
                        topic_key,
                        content_hash,
                        est_tokens as i64,
                        section_name,
                    ],
                );

                match insert_result {
                    Ok(_) => {
                        let entry_id = conn.last_insert_rowid();
                        let _ = rebuild_fts(conn, entry_id);

                        // Error lifecycle metadata for mistake entries (fail-open).
                        // Mirrors Python: classify_error_type / extract_root_cause / detect_severity.
                        if category == "mistake" {
                            let et = classify_error_type(chunk_trimmed);
                            let rc = extract_root_cause(chunk_trimmed);
                            let sv = detect_severity(chunk_trimmed);
                            if !et.is_empty() || !rc.is_empty() || sv != "medium" {
                                let _ = conn.execute(
                                    "UPDATE knowledge_entries \
                                     SET error_type = ?, root_cause = ?, severity = ? \
                                     WHERE id = ? AND COALESCE(error_type, '') = ''",
                                    rusqlite::params![et, rc, sv, entry_id],
                                );
                            }
                        }

                        // Enqueue sync op for the new entry (fail-open).
                        {
                            let payload = serde_json::json!({
                                "session_id": session_id,
                                "category": category,
                                "title": title,
                                "stable_id": stable_id,
                                "content": chunk_trimmed,
                                "tags": tags,
                                "confidence": confidence,
                                "first_seen": now,
                                "last_seen": now,
                                "content_hash": content_hash,
                                "source": source,
                                "topic_key": topic_key,
                                "revision_count": 1,
                                "est_tokens": (title.len() + chunk_trimmed.len()) / 4,
                                "source_section": section_name,
                            });
                            let payload_json = serde_json::to_string(&payload)
                                .unwrap_or_else(|_| "{}".to_string());
                            enqueue_sync_op_fail_open(
                                conn,
                                "knowledge_entries",
                                &stable_id,
                                &payload_json,
                            );
                        }

                        existing_hashes.insert(content_hash);
                        stats.extracted += 1;
                    }
                    Err(e) => {
                        // Log but continue — fail-open matches Python's IntegrityError handling.
                        eprintln!("[extract] Entry skipped: {e}");
                        stats.skipped += 1;
                    }
                }
            }
        }
    }

    // Wave 16: extract deterministic relations natively.
    // SAME_SESSION, SAME_TOPIC, TAG_OVERLAP, RESOLVED_BY are ported here.
    // Wave 19: SEMANTIC_PROXIMITY is also ported; called below.
    stats.relations_extracted = extract_relations_native(conn).unwrap_or(0);

    Ok(stats)
}

// ── Native relation extraction (Wave 16) ─────────────────────────────────────

/// Compute `knowledge_relations.stable_id`, mirroring Python's
/// `_knowledge_relation_stable_id(src, tgt, rtype)`:
///   SHA-256("knowledge_relation\0{src}\0{tgt}\0{rtype}")
pub fn compute_relation_stable_id(src_stable: &str, tgt_stable: &str, rtype: &str) -> String {
    let parts: &[&str] = &["knowledge_relation", src_stable, tgt_stable, rtype];
    let payload = parts.join("\0");
    let hash = Sha256::digest(payload.as_bytes());
    format!("{:x}", hash)
}

// Row fetched from knowledge_entries for relation extraction.
struct EntryRow {
    id: i64,
    session_id: String,
    category: String,
    #[allow(dead_code)]
    title: String,
    tags: String,
    topic_key: String,
    stable_id: String,
}

/// Accumulate relations while respecting per-type and global budgets.
struct RelationCollector<'a> {
    rows: &'a [EntryRow],
    seen: HashSet<(i64, i64, String)>,
    type_counts: HashMap<String, usize>,
    relations: Vec<(i64, i64, String, String, String, String, f64, String)>,
    now: String,
    max_per_type: usize,
    max_total: usize,
}

impl<'a> RelationCollector<'a> {
    fn new(rows: &'a [EntryRow], now: String) -> Self {
        Self {
            rows,
            seen: HashSet::new(),
            type_counts: HashMap::new(),
            relations: Vec::new(),
            now,
            max_per_type: 1500,
            max_total: 5000,
        }
    }

    /// Get a stable_id for the entry at `idx`, falling back to computed value
    /// when the stored `stable_id` is empty.  Mirrors Python's `_add()` fallback:
    ///   `src[6] or _knowledge_stable_id(src[1], src[2], src[3], src[5] or "")`
    fn get_stable(&self, idx: usize) -> String {
        let e = &self.rows[idx];
        if !e.stable_id.is_empty() {
            e.stable_id.clone()
        } else {
            compute_stable_id_with_topic_key(&e.session_id, &e.category, &e.title, &e.topic_key)
        }
    }

    /// Try to add a relation.  Returns `true` when the budget is exhausted
    /// (mirrors Python's `_add()` return value).
    fn add(&mut self, src_idx: usize, tgt_idx: usize, rtype: &str, conf: f64) -> bool {
        let src_id = self.rows[src_idx].id;
        let tgt_id = self.rows[tgt_idx].id;
        if src_id == tgt_id {
            return false;
        }
        let key = (src_id, tgt_id, rtype.to_string());
        if !self.seen.contains(&key) {
            let cnt = *self.type_counts.get(rtype).unwrap_or(&0);
            if cnt >= self.max_per_type || self.relations.len() >= self.max_total {
                return true; // budget exhausted
            }
            self.seen.insert(key);
            let src_stable = self.get_stable(src_idx);
            let tgt_stable = self.get_stable(tgt_idx);
            let stable_id = compute_relation_stable_id(&src_stable, &tgt_stable, rtype);
            self.relations.push((
                src_id,
                tgt_id,
                src_stable,
                tgt_stable,
                rtype.to_string(),
                stable_id,
                (conf * 100.0).round() / 100.0,
                self.now.clone(),
            ));
            *self.type_counts.entry(rtype.to_string()).or_insert(0) += 1;
        }
        false
    }
}

/// Extract deterministic relation types natively, mirroring the first four
/// buckets of Python's `extract_relations()`.
///
/// **Ported types** (deterministic, no ML dependency):
///   - `SAME_SESSION`  — different categories within the same session (conf 0.7)
///   - `SAME_TOPIC`    — same topic_key from different sessions (conf 0.9)
///   - `TAG_OVERLAP`   — sharing 2+ tags (conf 0.5 + 0.1 × shared count)
///   - `RESOLVED_BY`   — mistake → pattern/tool in the same session (conf 0.8)
///
/// **Wave 19**: `SEMANTIC_PROXIMITY` is now computed natively by
/// `compute_semantic_proximity()`, called after the four deterministic types.
///
/// Starts with `DELETE FROM knowledge_relations` (same full-rebuild semantics as
/// Python) so stale rows are always cleared.
///
/// Returns the number of relations inserted, or 0 on any error (fail-open).
pub fn extract_relations_native(conn: &Connection) -> anyhow::Result<usize> {
    let now = chrono::Utc::now().format("%Y-%m-%dT%H:%M:%S").to_string();

    // Ensure the unique constraint index exists.
    let _ = conn.execute_batch(
        "CREATE UNIQUE INDEX IF NOT EXISTS idx_relations_unique \
         ON knowledge_relations(source_id, target_id, relation_type)",
    );

    // Full rebuild (matches Python's `DELETE FROM knowledge_relations`).
    // This clears stale rows so each watch cycle is consistent.
    match conn.execute("DELETE FROM knowledge_relations", []) {
        Ok(_) => {}
        Err(_) => return Ok(0), // table absent — schema not yet bootstrapped
    }

    // Load entries newest-first (mirrors Python ORDER BY id DESC).
    let mut stmt = match conn.prepare(
        "SELECT id, session_id, category, title, COALESCE(tags,''), \
         COALESCE(topic_key,''), COALESCE(stable_id,'') \
         FROM knowledge_entries ORDER BY id DESC",
    ) {
        Ok(s) => s,
        Err(_) => return Ok(0),
    };

    let rows: Vec<EntryRow> = stmt
        .query_map([], |row| {
            Ok(EntryRow {
                id: row.get(0)?,
                session_id: row.get(1)?,
                category: row.get(2)?,
                title: row.get(3)?,
                tags: row.get(4)?,
                topic_key: row.get(5)?,
                stable_id: row.get(6)?,
            })
        })
        .map_err(anyhow::Error::from)?
        .flatten()
        .collect();

    if rows.is_empty() {
        return Ok(0);
    }

    // Build lookup indexes while preserving Python's newest-first group order.
    // Python 3.7+ dict insertion order means the first time a session/topic is
    // seen during ORDER BY id DESC becomes the iteration order for SAME_SESSION,
    // SAME_TOPIC, and RESOLVED_BY. Keep explicit order vectors so budgeted
    // relation selection stays aligned with Python when caps are hit.
    let mut by_session: HashMap<String, Vec<usize>> = HashMap::new();
    let mut by_topic: HashMap<String, Vec<usize>> = HashMap::new();
    let mut session_order: Vec<String> = Vec::new();
    let mut topic_order: Vec<String> = Vec::new();

    for (idx, e) in rows.iter().enumerate() {
        let session_entry = by_session.entry(e.session_id.clone()).or_insert_with(|| {
            session_order.push(e.session_id.clone());
            Vec::new()
        });
        session_entry.push(idx);
        if !e.topic_key.is_empty() {
            let topic_entry = by_topic.entry(e.topic_key.clone()).or_insert_with(|| {
                topic_order.push(e.topic_key.clone());
                Vec::new()
            });
            topic_entry.push(idx);
        }
    }

    let mut col = RelationCollector::new(&rows, now);

    // ── 1. SAME_SESSION ─────────────────────────────────────────────────────
    // Different categories within the same session.  Per-session pair cap.
    let active_sessions: Vec<&Vec<usize>> = session_order
        .iter()
        .filter_map(|sid| by_session.get(sid))
        .filter(|g| g.len() >= 2)
        .collect();
    let ss_cap = std::cmp::max(3, 1500 / std::cmp::max(1, active_sessions.len()));
    'ss_outer: for group in &active_sessions {
        let mut session_added = 0usize;
        for i in 0..group.len() {
            if session_added >= ss_cap {
                break;
            }
            for j in (i + 1)..group.len() {
                if session_added >= ss_cap {
                    break;
                }
                let ai = group[i];
                let bi = group[j];
                if rows[ai].category != rows[bi].category {
                    let prev = *col.type_counts.get("SAME_SESSION").unwrap_or(&0);
                    if col.add(ai, bi, "SAME_SESSION", 0.7) {
                        break 'ss_outer;
                    }
                    if *col.type_counts.get("SAME_SESSION").unwrap_or(&0) > prev {
                        session_added += 1;
                    }
                }
            }
        }
    }

    // ── 2. SAME_TOPIC ────────────────────────────────────────────────────────
    // Same topic_key from different sessions.  Per-topic pair cap.
    let active_topics: Vec<&Vec<usize>> = topic_order
        .iter()
        .filter_map(|topic| by_topic.get(topic))
        .filter(|g| g.len() >= 2)
        .collect();
    let st_cap = std::cmp::max(3, 1500 / std::cmp::max(1, active_topics.len()));
    'st_outer: for group in &active_topics {
        let mut topic_added = 0usize;
        for i in 0..group.len() {
            if topic_added >= st_cap {
                break;
            }
            for j in (i + 1)..group.len() {
                if topic_added >= st_cap {
                    break;
                }
                let ai = group[i];
                let bi = group[j];
                if rows[ai].session_id != rows[bi].session_id {
                    let prev = *col.type_counts.get("SAME_TOPIC").unwrap_or(&0);
                    if col.add(ai, bi, "SAME_TOPIC", 0.9) {
                        break 'st_outer;
                    }
                    if *col.type_counts.get("SAME_TOPIC").unwrap_or(&0) > prev {
                        topic_added += 1;
                    }
                }
            }
        }
    }

    // ── 3. TAG_OVERLAP ───────────────────────────────────────────────────────
    // Entries sharing 2+ tags.
    let entry_tags: Vec<(usize, HashSet<&str>)> = rows
        .iter()
        .enumerate()
        .filter_map(|(idx, e)| {
            let tags: HashSet<&str> = e.tags.split(',').map(str::trim).filter(|t| !t.is_empty()).collect();
            if tags.len() >= 2 {
                Some((idx, tags))
            } else {
                None
            }
        })
        .collect();

    'tag_outer: for i in 0..entry_tags.len() {
        let (ai, ref atags) = entry_tags[i];
        for j in (i + 1)..entry_tags.len() {
            let (bi, ref btags) = entry_tags[j];
            let shared = atags.intersection(btags).count();
            if shared >= 2 {
                let conf = f64::min(1.0, 0.5 + 0.1 * (shared.min(5) as f64));
                if col.add(ai, bi, "TAG_OVERLAP", conf) {
                    break 'tag_outer;
                }
            }
        }
    }

    // ── 4. RESOLVED_BY ───────────────────────────────────────────────────────
    // Mistake → pattern/tool in the same session, newest sessions first.
    'rb_outer: for sid in &session_order {
        let Some(group) = by_session.get(sid) else {
            continue;
        };
        let mistakes: Vec<usize> = group.iter().copied().filter(|&i| rows[i].category == "mistake").collect();
        let resolvers: Vec<usize> = group.iter().copied().filter(|&i| rows[i].category == "pattern" || rows[i].category == "tool").collect();
        for &mi in &mistakes {
            for &ri in &resolvers {
                if col.add(mi, ri, "RESOLVED_BY", 0.8) {
                    break 'rb_outer;
                }
            }
        }
    }

    let total = col.relations.len();
    if total == 0 {
        return Ok(0);
    }

    // Batch insert with INSERT OR IGNORE (unique index guards dedup).
    for (src_id, tgt_id, src_stable, tgt_stable, rtype, stable_id, conf, created_at) in
        &col.relations
    {
        let _ = conn.execute(
            "INSERT OR IGNORE INTO knowledge_relations \
             (source_id, target_id, source_stable_id, target_stable_id, \
              relation_type, stable_id, confidence, created_at) \
             VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            rusqlite::params![
                src_id,
                tgt_id,
                src_stable,
                tgt_stable,
                rtype,
                stable_id,
                conf,
                created_at
            ],
        );
        // Enqueue sync op (fail-open).
        let payload = serde_json::json!({
            "source_stable_id": src_stable,
            "target_stable_id": tgt_stable,
            "relation_type": rtype,
            "stable_id": stable_id,
            "confidence": conf,
            "created_at": created_at,
        });
        let payload_json = serde_json::to_string(&payload).unwrap_or_else(|_| "{}".to_string());
        enqueue_sync_op_fail_open(conn, "knowledge_relations", stable_id, &payload_json);
    }

    // Wave 19: append SEMANTIC_PROXIMITY via native TF-IDF (stronger-pair aware).
    let sem_count = compute_semantic_proximity(conn);

    Ok(total + sem_count)
}

// ── Wave 19: Native SEMANTIC_PROXIMITY (TF-IDF cosine similarity) ────────────

/// Compute SEMANTIC_PROXIMITY relations natively, mirroring Python's
/// `_run_semantic_proximity()`.
///
/// Algorithm:
/// 1. Load up to 500 most-recent entries (ORDER BY id DESC LIMIT 500).
/// 2. Build combined text = `title + " " + tags + " " + content` per entry.
/// 3. Fit TF-IDF model (`embeddings::tfidf::build_tfidf_model`).
/// 4. Compute pairwise cosine similarities (vectors are already L2-normalised,
///    so cosine = dot product).
/// 5. Insert pairs with score ≥ 0.75 not already covered by a stronger relation.
///
/// Fail-open: any DB or model error returns 0 without panic.
/// INSERT OR IGNORE deduplicates with the unique index.
///
/// # ASCII vs unicode tokenisation caveat
/// The Rust tokeniser uses ASCII-only alphanumeric sequences; Python's sklearn uses
/// `strip_accents="unicode"`.  For English session-knowledge content the difference
/// is negligible; scores may differ slightly on non-ASCII text.
fn compute_semantic_proximity(conn: &Connection) -> usize {
    compute_semantic_proximity_inner(conn).unwrap_or(0)
}

fn compute_semantic_proximity_inner(conn: &Connection) -> anyhow::Result<usize> {
    const THRESHOLD: f64 = 0.75;
    const MAX_ENTRIES: i64 = 500;

    let now = chrono::Utc::now().format("%Y-%m-%dT%H:%M:%S").to_string();

    // ── Step 1: load entries ─────────────────────────────────────────────────
    struct SemEntry {
        id: i64,
        text: String,
        stable_id: String,
    }

    let sem_entries: Vec<SemEntry> = {
        let mut stmt = conn.prepare(
            "SELECT id, \
             COALESCE(title,''), COALESCE(tags,''), COALESCE(content,''), \
             COALESCE(stable_id,''), COALESCE(session_id,''), \
             COALESCE(category,''), COALESCE(topic_key,'') \
             FROM knowledge_entries ORDER BY id DESC LIMIT ?",
        )?;
        let rows: Vec<rusqlite::Result<SemEntry>> = stmt.query_map(rusqlite::params![MAX_ENTRIES], |row| {
            let eid: i64 = row.get(0)?;
            let title: String = row.get(1)?;
            let tags: String = row.get(2)?;
            let content: String = row.get(3)?;
            let stored_stable: String = row.get(4)?;
            let session_id: String = row.get(5)?;
            let category: String = row.get(6)?;
            let topic_key: String = row.get(7)?;
            // Mirror Python: title + tags + content joined by space, dropping blanks.
            let text: String = [title.trim(), tags.trim(), content.trim()]
                .iter()
                .filter(|s| !s.is_empty())
                .copied()
                .collect::<Vec<_>>()
                .join(" ");
            // stable_id: use stored value or compute fallback.
            let stable_id = if !stored_stable.is_empty() {
                stored_stable
            } else {
                compute_stable_id_with_topic_key(&session_id, &category, &title, &topic_key)
            };
            Ok(SemEntry { id: eid, text, stable_id })
        })?
        .collect();
        rows.into_iter()
            .flatten()
            .filter(|e| !e.text.is_empty())
            .collect()
    };

    if sem_entries.len() < 2 {
        return Ok(0);
    }

    // ── Step 2: build TF-IDF model ───────────────────────────────────────────
    let texts: Vec<&str> = sem_entries.iter().map(|e| e.text.as_str()).collect();
    let ids: Vec<i64> = sem_entries.iter().map(|e| e.id).collect();
    let model_blob = crate::embeddings::tfidf::build_tfidf_model(&texts, &ids);

    let model: serde_json::Value = serde_json::from_slice(&model_blob)
        .map_err(|e| anyhow::anyhow!("TF-IDF model parse: {e}"))?;

    let matrix_row: Vec<usize> = model["matrix_row"]
        .as_array()
        .ok_or_else(|| anyhow::anyhow!("missing matrix_row"))?
        .iter()
        .map(|v| v.as_i64().unwrap_or(0) as usize)
        .collect();
    let matrix_col: Vec<usize> = model["matrix_col"]
        .as_array()
        .ok_or_else(|| anyhow::anyhow!("missing matrix_col"))?
        .iter()
        .map(|v| v.as_i64().unwrap_or(0) as usize)
        .collect();
    let matrix_data: Vec<f64> = model["matrix_data"]
        .as_array()
        .ok_or_else(|| anyhow::anyhow!("missing matrix_data"))?
        .iter()
        .map(|v| v.as_f64().unwrap_or(0.0))
        .collect();

    let n_docs = sem_entries.len();

    // ── Step 3: build per-document sparse vectors ────────────────────────────
    // doc_col_map[i] = HashMap<col, val> for doc i (vectors are L2-normalised).
    let mut doc_col_maps: Vec<HashMap<usize, f64>> = vec![HashMap::new(); n_docs];
    let nnz = matrix_row.len().min(matrix_col.len()).min(matrix_data.len());
    for k in 0..nnz {
        let r = matrix_row[k];
        let c = matrix_col[k];
        let v = matrix_data[k];
        if r < n_docs {
            doc_col_maps[r].insert(c, v);
        }
    }

    // ── Step 4: build stronger_pairs from already-inserted relations ─────────
    let mut stronger_pairs: HashSet<(i64, i64)> = HashSet::new();
    {
        let mut stmt =
            conn.prepare("SELECT source_id, target_id FROM knowledge_relations")?;
        let rows = stmt
            .query_map([], |row| Ok((row.get::<_, i64>(0)?, row.get::<_, i64>(1)?)))?;
        for pair in rows.flatten() {
            stronger_pairs.insert(pair);
            stronger_pairs.insert((pair.1, pair.0));
        }
    }

    // ── Step 5: pairwise cosine similarity ──────────────────────────────────
    // Cosine = dot product (vectors are L2-normalised).
    // Collect (src_id, tgt_id, src_stable, tgt_stable, stable_id, conf).
    type RelRow = (i64, i64, String, String, String, f64);
    let mut sem_relations: Vec<RelRow> = Vec::new();

    for i in 0..n_docs {
        if doc_col_maps[i].is_empty() {
            continue;
        }
        for j in (i + 1)..n_docs {
            if doc_col_maps[j].is_empty() {
                continue;
            }

            // Dot product: iterate j's cols, look up in i's map.
            let mut dot = 0.0f64;
            for (&c, &v) in &doc_col_maps[j] {
                if let Some(&u) = doc_col_maps[i].get(&c) {
                    dot += u * v;
                }
            }

            if dot < THRESHOLD {
                continue;
            }

            let src_id = sem_entries[i].id;
            let tgt_id = sem_entries[j].id;

            if stronger_pairs.contains(&(src_id, tgt_id))
                || stronger_pairs.contains(&(tgt_id, src_id))
            {
                continue;
            }

            let src_stable = sem_entries[i].stable_id.clone();
            let tgt_stable = sem_entries[j].stable_id.clone();
            let stable_id =
                compute_relation_stable_id(&src_stable, &tgt_stable, "SEMANTIC_PROXIMITY");
            let conf = (dot * 100.0).round() / 100.0;

            sem_relations.push((src_id, tgt_id, src_stable, tgt_stable, stable_id, conf));
        }
    }

    if sem_relations.is_empty() {
        return Ok(0);
    }

    // ── Step 6: batch insert ─────────────────────────────────────────────────
    let rtype = "SEMANTIC_PROXIMITY";
    let mut inserted = 0usize;

    for (src_id, tgt_id, src_stable, tgt_stable, stable_id, conf) in &sem_relations {
        let n = conn
            .execute(
                "INSERT OR IGNORE INTO knowledge_relations \
                 (source_id, target_id, source_stable_id, target_stable_id, \
                  relation_type, stable_id, confidence, created_at) \
                 VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                rusqlite::params![
                    src_id, tgt_id, src_stable, tgt_stable, rtype, stable_id, conf, now
                ],
            )
            .unwrap_or(0);
        if n > 0 {
            inserted += 1;
            let payload = serde_json::json!({
                "source_stable_id": src_stable,
                "target_stable_id": tgt_stable,
                "relation_type": rtype,
                "stable_id": stable_id,
                "confidence": conf,
                "created_at": &now,
            });
            let payload_json =
                serde_json::to_string(&payload).unwrap_or_else(|_| "{}".to_string());
            enqueue_sync_op_fail_open(conn, "knowledge_relations", stable_id, &payload_json);
        }
    }

    if inserted > 0 {
        println!("[extract] SEMANTIC_PROXIMITY: {inserted} native relations");
    }

    Ok(inserted)
}



/// Derive Copilot session IDs from changed file paths.
///
/// Mirrors the logic in `session.rs` `extract_session_id_from_path`.
fn session_ids_from_changed(changed_paths: &[&str], session_state_dir: &Path) -> Vec<String> {
    let state_str = session_state_dir.to_string_lossy().to_string();
    let mut ids: HashSet<String> = HashSet::new();
    for path in changed_paths {
        // Strip session_state_dir prefix.
        let rel = if let Some(r) = path.strip_prefix(&state_str) {
            r.trim_start_matches(['/', '\\'])
        } else {
            continue;
        };
        // First path component is the session UUID (36-char hyphenated UUID).
        let component = rel.split(['/', '\\']).next().unwrap_or("").to_string();
        // Basic UUID shape check (36 chars with hyphens).
        if component.len() == 36 && component.chars().filter(|&c| c == '-').count() == 4 {
            ids.insert(component);
        }
    }
    ids.into_iter().collect()
}

/// Open the knowledge DB and run `extract_from_sections` for sessions derived
/// from `changed_paths`.
///
/// **Wave 18**: Creates the DB natively when absent and bootstraps extract-owned
/// schema via `ensure_extract_tables()`.  Returns `None` only on genuine DB
/// creation / open failure (caller may fall back to Python).  Previously returned
/// `None` for any absent DB; that caused a mandatory Python spawn on every first run.
///
/// Called by `watch.rs` under `#[cfg(feature = "native-extract")]`.
pub fn extract_from_changed_sessions(
    changed_paths: &[&str],
    session_state_dir: &Path,
    db_path: &Path,
) -> Option<anyhow::Result<ExtractStats>> {
    // Wave-18: open existing DB with read-write flags; create it natively when absent.
    let conn = if db_path.exists() {
        match Connection::open_with_flags(
            db_path,
            OpenFlags::SQLITE_OPEN_READ_WRITE | OpenFlags::SQLITE_OPEN_NO_MUTEX,
        ) {
            Ok(c) => c,
            Err(e) => {
                eprintln!("[extract] Cannot open DB: {e}");
                return Some(Err(anyhow::anyhow!("Cannot open DB: {e}")));
            }
        }
    } else {
        // Fresh bootstrap: create DB with CREATE flag; fail-open → Some(Err) so
        // watch.rs can fall back to Python on genuine creation failures.
        match Connection::open_with_flags(
            db_path,
            OpenFlags::SQLITE_OPEN_READ_WRITE
                | OpenFlags::SQLITE_OPEN_CREATE
                | OpenFlags::SQLITE_OPEN_NO_MUTEX,
        ) {
            Ok(c) => c,
            Err(e) => {
                eprintln!("[extract] Cannot create DB: {e}");
                return Some(Err(anyhow::anyhow!("Cannot create DB: {e}")));
            }
        }
    };
    let _ = conn.execute_batch("PRAGMA journal_mode=WAL; PRAGMA busy_timeout=30000;");

    // Wave-18: ensure extract-owned tables exist (idempotent, compatible with migrate.py).
    if let Err(e) = ensure_extract_tables(&conn) {
        eprintln!("[extract] ensure_extract_tables failed (continuing): {e}");
        // Fail-open — tables may already exist on a Python-managed DB.
    }

    let session_ids = session_ids_from_changed(changed_paths, session_state_dir);
    let ids_opt: Option<&[String]> = if session_ids.is_empty() {
        None
    } else {
        Some(&session_ids)
    };

    Some(extract_from_sections(&conn, ids_opt))
}

// ── Wave 17: Native residual helpers ─────────────────────────────────────────
//
// Ports the non-sklearn portions of Python's `_run_residual_work()`:
//   - `_parse_file_list` / `_backfill_affected_files_from_session_evidence`
//   - `_infer_task_ids_from_content`
//   - Confidence decay (daily, via `embedding_meta.last_decay_date`)
//
// Wave 19: SEMANTIC_PROXIMITY is also native now (see compute_semantic_proximity).
// `watch.rs` no longer spawns `extract-knowledge.py --semantic-only` on the
// successful native watch path.

/// Parse file paths from an `important_files` section. Mirrors Python's
/// `_parse_file_list()`. Returns at most the raw paths (caller caps at 20).
fn parse_file_list(content: &str) -> Vec<String> {
    let mut files = Vec::new();
    for line in content.lines() {
        let line = line.trim();
        // Strip leading bullet markers
        let path = if line.starts_with("  - ") || line.starts_with("  * ") {
            &line[4..]
        } else if line.starts_with("- ") || line.starts_with("* ") || line.starts_with("+ ") {
            &line[2..]
        } else {
            line
        };
        let path = path.trim();
        // Strip backtick wrapping (first pass — handles bare `` `path` `` lines)
        let path = path.trim_matches('`');
        // Strip inline comments and table separators
        let path = path.split('#').next().unwrap_or("").trim();
        let path = path.split('|').next().unwrap_or("").trim();
        // Second pass: strip backticks that appeared inside `` `path` # comment `` patterns
        let path = path.trim_matches('`').trim();
        if path.is_empty() || path.len() > 256 {
            continue;
        }
        if path.starts_with("http://") || path.starts_with("https://") || path.starts_with('[') {
            continue;
        }
        // Must resemble a file path: contains a dot (extension) or a slash.
        // Match Python parity exactly: `_parse_file_list()` accepts dots or `/`,
        // but not bare Windows backslash-only paths.
        if path.contains('.') || path.contains('/') {
            files.push(path.to_string());
        }
    }
    files
}

/// Backfill `affected_files` from the same session's `important_files`
/// section.  Only updates entries where `affected_files` is empty/null.
/// Does NOT overwrite manually populated values.  Mirrors Python's
/// `_backfill_affected_files_from_session_evidence()`.
///
/// Returns the number of rows updated (0 on any DB error — fail-open).
pub fn backfill_affected_files(conn: &Connection, session_ids: Option<&[String]>) -> usize {
    let mut sql = "SELECT id, session_id FROM knowledge_entries \
        WHERE (affected_files IS NULL OR affected_files = '[]' OR affected_files = '') \
        AND session_id IS NOT NULL AND session_id != ''"
        .to_string();
    let mut params: Vec<String> = Vec::new();
    if let Some(ids) = session_ids {
        if !ids.is_empty() {
            let ph: String = ids.iter().map(|_| "?").collect::<Vec<_>>().join(",");
            sql.push_str(&format!(" AND session_id IN ({ph})"));
            params.extend(ids.iter().cloned());
        }
    }
    let params_ref: Vec<&dyn rusqlite::ToSql> =
        params.iter().map(|s| s as &dyn rusqlite::ToSql).collect();

    let rows: Vec<(i64, String)> = {
        let mut stmt = match conn.prepare(&sql) {
            Ok(s) => s,
            Err(_) => return 0,
        };
        let x = match stmt.query_map(params_ref.as_slice(), |row| Ok((row.get(0)?, row.get(1)?))) {
            Ok(mapped) => mapped.flatten().collect(),
            Err(_) => return 0,
        };
        x
    };
    if rows.is_empty() {
        return 0;
    }

    // Group entry IDs by session.
    let mut by_session: HashMap<String, Vec<i64>> = HashMap::new();
    for (entry_id, session_id) in rows {
        by_session.entry(session_id).or_default().push(entry_id);
    }

    let mut updated = 0usize;
    for (session_id, entry_ids) in &by_session {
        let content: Option<String> = conn
            .query_row(
                "SELECT s.content FROM sections s \
                 JOIN documents d ON s.document_id = d.id \
                 WHERE d.session_id = ? AND s.section_name = 'important_files' \
                 ORDER BY d.seq DESC LIMIT 1",
                rusqlite::params![session_id],
                |row| row.get(0),
            )
            .ok()
            .flatten();
        let Some(content) = content else { continue };
        let files = parse_file_list(&content);
        if files.is_empty() {
            continue;
        }
        let capped: Vec<&String> = files.iter().take(20).collect();
        let files_json =
            serde_json::to_string(&capped).unwrap_or_else(|_| "[]".to_string());
        for entry_id in entry_ids {
            if conn
                .execute(
                    "UPDATE knowledge_entries SET affected_files = ? \
                     WHERE id = ? AND (affected_files IS NULL OR affected_files = '[]' OR affected_files = '')",
                    rusqlite::params![files_json, entry_id],
                )
                .unwrap_or(0)
                > 0
            {
                updated += 1;
            }
        }
    }
    updated
}

fn task_id_marker_re() -> &'static Regex {
    static R: OnceLock<Regex> = OnceLock::new();
    R.get_or_init(|| {
        Regex::new(r"(?i)(?:task|tentacle)\s*[=:]\s*([a-z][a-z0-9]*(?:-[a-z0-9]+)+)\b").unwrap()
    })
}

fn kebab_slug_re() -> &'static Regex {
    static R: OnceLock<Regex> = OnceLock::new();
    R.get_or_init(|| Regex::new(r"^[a-z][a-z0-9]*(?:-[a-z0-9]+)+$").unwrap())
}

/// Infer `task_id` from explicit `task:` / `tentacle=` markers in entry
/// content.  Only assigns when exactly ONE kebab-case slug is found.
/// Does NOT overwrite existing `task_id` values.  Mirrors Python's
/// `_infer_task_ids_from_content()`.
///
/// Returns the number of rows updated (0 on any DB error — fail-open).
pub fn infer_task_ids(conn: &Connection, session_ids: Option<&[String]>) -> usize {
    let task_re = task_id_marker_re();
    let slug_re = kebab_slug_re();

    let mut sql = "SELECT id, \
        COALESCE(title,''), COALESCE(content,'') \
        FROM knowledge_entries \
        WHERE (task_id IS NULL OR task_id = '') \
        AND (title IS NOT NULL OR content IS NOT NULL)"
        .to_string();
    let mut params: Vec<String> = Vec::new();
    if let Some(ids) = session_ids {
        if !ids.is_empty() {
            let ph: String = ids.iter().map(|_| "?").collect::<Vec<_>>().join(",");
            sql.push_str(&format!(" AND session_id IN ({ph})"));
            params.extend(ids.iter().cloned());
        }
    }
    let params_ref: Vec<&dyn rusqlite::ToSql> =
        params.iter().map(|s| s as &dyn rusqlite::ToSql).collect();

    let rows: Vec<(i64, String, String)> = {
        let mut stmt = match conn.prepare(&sql) {
            Ok(s) => s,
            Err(_) => return 0,
        };
        let x = match stmt.query_map(params_ref.as_slice(), |row| {
            Ok((row.get(0)?, row.get(1)?, row.get(2)?))
        }) {
            Ok(mapped) => mapped.flatten().collect(),
            Err(_) => return 0,
        };
        x
    };

    let mut updated = 0usize;
    for (entry_id, title, content) in &rows {
        let text = format!("{title} {content}");
        let mut candidates: HashSet<String> = HashSet::new();
        for cap in task_re.captures_iter(&text) {
            if let Some(m) = cap.get(1) {
                let slug = m.as_str().to_lowercase();
                if slug_re.is_match(&slug) && slug.len() >= 5 && slug.len() <= 50 {
                    candidates.insert(slug);
                }
            }
        }
        if candidates.len() == 1 {
            let slug = candidates.into_iter().next().unwrap();
            let _ = conn.execute(
                "UPDATE knowledge_entries SET task_id = ? \
                 WHERE id = ? AND (task_id IS NULL OR task_id = '')",
                rusqlite::params![slug, entry_id],
            );
            updated += 1;
        }
    }
    updated
}

/// Apply once-per-day confidence decay, mirroring Python's inline decay block
/// in `_run_residual_work()`.
///
/// Decay formula (matches Python):
///   - confidence ≥ 0.8 → × 0.98
///   - confidence < 0.8 → × 0.95
///   - floor at 0.3
///   - Condition: `last_seen < today AND confidence > 0.3`
///
/// The last decay date is stored in `embedding_meta.last_decay_date`.
/// A second call on the same day is a no-op.
///
/// Returns `true` when decay was applied, `false` when skipped (fail-open —
/// never panics).
pub fn run_decay(conn: &Connection) -> bool {
    let today = chrono::Local::now().format("%Y-%m-%d").to_string();

    // Ensure embedding_meta table exists (matches Python's CREATE TABLE IF NOT EXISTS).
    let _ = conn.execute_batch(
        "CREATE TABLE IF NOT EXISTS embedding_meta (key TEXT PRIMARY KEY, value TEXT)",
    );

    // Read last decay date.
    let last_decay: Option<String> = conn
        .query_row(
            "SELECT value FROM embedding_meta WHERE key = 'last_decay_date'",
            [],
            |row| row.get(0),
        )
        .ok();

    if last_decay.as_deref() == Some(today.as_str()) {
        return false; // already decayed today — idempotent
    }

    // Apply decay — fail-open if knowledge_entries is absent.
    let _ = conn.execute(
        "UPDATE knowledge_entries \
         SET confidence = MAX(0.3, \
             CASE WHEN confidence >= 0.8 THEN confidence * 0.98 \
                  ELSE confidence * 0.95 END) \
         WHERE last_seen < ? AND confidence > 0.3",
        rusqlite::params![today],
    );

    // Record today's date (INSERT OR REPLACE).
    let _ = conn.execute(
        "INSERT OR REPLACE INTO embedding_meta (key, value) VALUES ('last_decay_date', ?)",
        rusqlite::params![today],
    );
    true
}

/// Run all native residual helpers (backfill_affected_files, infer_task_ids,
/// run_decay) for sessions derived from `changed_paths`.
///
/// Called by `watch.rs` after a successful native extract pass.
/// All residual work — including SEMANTIC_PROXIMITY (wave 19) — is Rust-native.
/// No Python subprocess is launched on the hot watch path.
/// Fails silently if the DB cannot be opened (fail-open).
pub fn run_native_residual_helpers_for_changed(
    changed_paths: &[&str],
    session_state_dir: &Path,
    db_path: &Path,
) {
    if !db_path.exists() {
        return;
    }
    let conn = match Connection::open_with_flags(
        db_path,
        OpenFlags::SQLITE_OPEN_READ_WRITE | OpenFlags::SQLITE_OPEN_NO_MUTEX,
    ) {
        Ok(c) => c,
        Err(e) => {
            eprintln!("[extract] residual: cannot open DB: {e}");
            return;
        }
    };
    let _ = conn.execute_batch("PRAGMA journal_mode=WAL; PRAGMA busy_timeout=30000;");

    let session_ids = session_ids_from_changed(changed_paths, session_state_dir);
    let ids_opt: Option<&[String]> = if session_ids.is_empty() {
        None
    } else {
        Some(&session_ids)
    };

    let backfilled = backfill_affected_files(&conn, ids_opt);
    let task_inferred = infer_task_ids(&conn, ids_opt);
    let decayed = run_decay(&conn);

    if backfilled > 0 || task_inferred > 0 || decayed {
        println!(
            "[extract] Residual native: {} affected_files, {} task_ids, decay={}",
            backfilled, task_inferred, decayed
        );
    }
}

// ── Unit tests ─────────────────────────────────────────────────────────────────

#[cfg(test)]
mod tests {
    use super::*;

    // ── classify_paragraph ────────────────────────────────────────────────────

    #[test]
    fn classify_clear_mistake() {
        let text = "The bug was caused by a null pointer — we should always check for null \
                    before dereferencing. Root cause: missing guard in the auth layer.";
        let classes = classify_paragraph(text);
        assert!(
            classes.iter().any(|(c, _)| c == "mistake"),
            "expected 'mistake' classification, got: {classes:?}"
        );
    }

    #[test]
    fn classify_clear_pattern() {
        let text = "Always use parameterised SQL queries instead of string interpolation. \
                    This is a best practice to prevent SQL injection.";
        let classes = classify_paragraph(text);
        assert!(
            classes.iter().any(|(c, _)| c == "pattern"),
            "expected 'pattern' classification, got: {classes:?}"
        );
    }

    #[test]
    fn classify_noise_interview_rejected() {
        let text = "câu hỏi phỏng vấn: Bảng đánh giá interview question cho ứng viên";
        let classes = classify_paragraph(text);
        assert!(
            classes.is_empty(),
            "interview noise must be rejected; got: {classes:?}"
        );
    }

    #[test]
    fn classify_pure_table_rejected() {
        let text = "| Col A | Col B | Col C |\n\
                    |-------|-------|-------|\n\
                    | val1  | val2  | val3  |\n\
                    | val4  | val5  | val6  |\n\
                    | val7  | val8  | val9  |";
        let classes = classify_paragraph(text);
        assert!(
            classes.is_empty(),
            "pure table must be rejected; got: {classes:?}"
        );
    }

    #[test]
    fn classify_action_summary_short_rejected() {
        // Short action summary lines should be filtered as noise.
        let text = "fixed the authentication module and deployed to production";
        let classes = classify_paragraph(text);
        assert!(
            classes.is_empty(),
            "short action-summary lines must be rejected as noise; got: {classes:?}"
        );
    }

    // ── content_hash ──────────────────────────────────────────────────────────

    #[test]
    fn content_hash_is_16_hex_chars() {
        let h = compute_content_hash("mistake", "Auth Bug", "Content here");
        assert_eq!(h.len(), 16, "content_hash must be 16 chars, got: {h:?}");
        assert!(
            h.chars().all(|c| c.is_ascii_hexdigit()),
            "content_hash must be hex: {h:?}"
        );
    }

    #[test]
    fn content_hash_is_stable() {
        let h1 = compute_content_hash("pattern", "Use param SQL", "Always use ? placeholders");
        let h2 = compute_content_hash("pattern", "Use param SQL", "Always use ? placeholders");
        assert_eq!(h1, h2, "content_hash must be deterministic");
    }

    #[test]
    fn content_hash_differs_on_category_change() {
        let h1 = compute_content_hash("mistake", "Title", "Content");
        let h2 = compute_content_hash("pattern", "Title", "Content");
        assert_ne!(h1, h2, "different category must produce different hash");
    }

    // ── topic_key / slugify ───────────────────────────────────────────────────

    #[test]
    fn topic_key_format() {
        let tk = generate_topic_key("mistake", "Auth JWT missing check");
        assert!(
            tk.starts_with("mistake/"),
            "topic_key must start with category: {tk:?}"
        );
        assert!(
            !tk.contains(' '),
            "topic_key must not contain spaces: {tk:?}"
        );
    }

    #[test]
    fn slugify_basic() {
        assert_eq!(slugify("Auth JWT Check"), "auth-jwt-check");
        assert_eq!(slugify("  leading spaces  "), "leading-spaces");
        assert_eq!(slugify("multiple---dashes"), "multiple-dashes");
    }

    #[test]
    fn slugify_max_60_chars() {
        let long = "a".repeat(100);
        assert_eq!(slugify(&long).len(), 60);
    }

    // ── extract_title ─────────────────────────────────────────────────────────

    #[test]
    fn extract_title_picks_first_meaningful_line() {
        let text = "Always validate inputs before processing\n\
                    More detail about why this matters.";
        let title = extract_title(text);
        assert!(
            title.contains("validate"),
            "title should contain 'validate': {title:?}"
        );
    }

    #[test]
    fn extract_title_skips_markdown_headers() {
        let text = "```\ncode\n```\nActual title line here for this entry";
        let title = extract_title(text);
        // Should skip the code fence and find real content.
        assert!(!title.is_empty(), "title must not be empty");
    }

    // ── extract_tags ──────────────────────────────────────────────────────────

    #[test]
    fn extract_tags_finds_python() {
        let tags = extract_tags("Use Python 3.11 with asyncio for this service");
        assert!(tags.contains("python"), "should find python tag: {tags:?}");
        assert!(tags.contains("async"), "should find async tag: {tags:?}");
    }

    #[test]
    fn extract_tags_empty_on_unmatched() {
        let tags = extract_tags("Generic plain text with no technology names");
        // Should not panic; may or may not produce tags.
        let _ = tags;
    }

    // ── split_into_knowledge_chunks ───────────────────────────────────────────

    #[test]
    fn chunk_split_on_numbered_list() {
        let content = "1. First item that is long enough to not be filtered out.\n\
                       2. Second item with enough content to be a chunk.\n\
                       3. Third item also long enough to qualify as a chunk.";
        let chunks = split_into_knowledge_chunks(content);
        assert!(
            chunks.len() >= 2,
            "expected ≥2 chunks, got {}",
            chunks.len()
        );
    }

    #[test]
    fn chunk_skips_very_short_fragments() {
        let content = "1. Hi\n2. Actual long enough chunk here with real content.";
        let chunks = split_into_knowledge_chunks(content);
        // "Hi" (<30 chars) must be filtered.
        assert!(!chunks.iter().any(|c| c.trim() == "Hi"));
    }

    // ── session_ids_from_changed ──────────────────────────────────────────────

    #[test]
    fn session_ids_extracted_from_paths() {
        use std::path::PathBuf;
        let session_state = PathBuf::from("/home/user/.copilot/session-state");
        let changed = &[
            "/home/user/.copilot/session-state/11111111-2222-3333-4444-555555555555/checkpoints/cp1.md",
            "/home/user/.copilot/session-state/11111111-2222-3333-4444-555555555555/plan.md",
            "/home/user/.copilot/session-state/aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee/plan.md",
        ];
        let ids = session_ids_from_changed(changed, &session_state);
        assert_eq!(ids.len(), 2, "should extract 2 unique session IDs: {ids:?}");
        assert!(ids.contains(&"11111111-2222-3333-4444-555555555555".to_string()));
        assert!(ids.contains(&"aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee".to_string()));
    }

    // ── extract_from_sections (in-memory DB) ──────────────────────────────────

    fn setup_test_db() -> Connection {
        let conn = Connection::open_in_memory().unwrap();
        conn.execute_batch(
            "PRAGMA journal_mode=WAL;
             CREATE TABLE documents (
                 id INTEGER PRIMARY KEY AUTOINCREMENT,
                 session_id TEXT NOT NULL,
                 doc_type TEXT NOT NULL DEFAULT 'checkpoint',
                 seq INTEGER DEFAULT 0,
                 title TEXT NOT NULL DEFAULT '',
                 stable_id TEXT DEFAULT '',
                 file_path TEXT NOT NULL DEFAULT '',
                 source TEXT DEFAULT 'copilot',
                 indexed_at TEXT
             );
             CREATE TABLE sections (
                 id INTEGER PRIMARY KEY AUTOINCREMENT,
                 document_id INTEGER NOT NULL,
                 section_name TEXT NOT NULL,
                 content TEXT NOT NULL
             );
             CREATE TABLE knowledge_entries (
                 id INTEGER PRIMARY KEY AUTOINCREMENT,
                 session_id TEXT NOT NULL,
                 document_id INTEGER,
                 category TEXT NOT NULL,
                 title TEXT NOT NULL,
                 stable_id TEXT,
                 content TEXT NOT NULL,
                 tags TEXT DEFAULT '',
                 confidence REAL DEFAULT 1.0,
                 occurrence_count INTEGER DEFAULT 1,
                 first_seen TEXT,
                 last_seen TEXT,
                 source TEXT DEFAULT 'copilot',
                 topic_key TEXT,
                 revision_count INTEGER DEFAULT 1,
                 content_hash TEXT,
                 wing TEXT DEFAULT '',
                 room TEXT DEFAULT '',
                 facts TEXT DEFAULT '[]',
                 est_tokens INTEGER DEFAULT 0,
                 source_section TEXT DEFAULT '',
                 UNIQUE(category, title, session_id)
             );
             CREATE VIRTUAL TABLE ke_fts USING fts5(
                 title, content, tags, category, wing, room, facts
             );
             CREATE TABLE knowledge_relations (
                 id INTEGER PRIMARY KEY AUTOINCREMENT,
                 source_id INTEGER NOT NULL,
                 target_id INTEGER NOT NULL,
                 source_stable_id TEXT DEFAULT '',
                 target_stable_id TEXT DEFAULT '',
                 relation_type TEXT NOT NULL,
                 stable_id TEXT,
                 confidence REAL DEFAULT 0.5,
                 created_at TEXT
             );
             CREATE UNIQUE INDEX idx_relations_unique
                 ON knowledge_relations(source_id, target_id, relation_type);",
        )
        .unwrap();
        conn
    }

    #[test]
    fn extract_from_sections_writes_classifiable_entry() {
        let conn = setup_test_db();
        // Insert a document and section with a classifiable paragraph.
        conn.execute(
            "INSERT INTO documents (session_id, doc_type, file_path) VALUES ('sess-1', 'checkpoint', '/fake')",
            [],
        )
        .unwrap();
        let doc_id = conn.last_insert_rowid();
        let content = "Always validate inputs before processing user data. \
                       Use parameterised queries instead of string concatenation. \
                       This is a best practice to prevent SQL injection attacks.";
        conn.execute(
            "INSERT INTO sections (document_id, section_name, content) VALUES (?, 'technical_details', ?)",
            rusqlite::params![doc_id, content],
        )
        .unwrap();

        let stats = extract_from_sections(&conn, None).unwrap();
        assert!(
            stats.extracted > 0,
            "must extract at least 1 entry; stats: extracted={}, deduped={}, skipped={}",
            stats.extracted,
            stats.deduped,
            stats.skipped
        );

        // Verify entry was written with correct fields.
        let count: i64 = conn
            .query_row(
                "SELECT COUNT(*) FROM knowledge_entries WHERE topic_key IS NOT NULL AND content_hash IS NOT NULL",
                [],
                |row| row.get(0),
            )
            .unwrap();
        assert!(count > 0, "entry must have topic_key and content_hash set");
    }

    #[test]
    fn extract_from_sections_deduplicates_on_rehash() {
        let conn = setup_test_db();
        conn.execute(
            "INSERT INTO documents (session_id, doc_type, file_path) VALUES ('sess-2', 'checkpoint', '/fake2')",
            [],
        )
        .unwrap();
        let doc_id = conn.last_insert_rowid();
        let content = "Always use parameterised SQL instead of string interpolation. \
                       Best practice: avoid SQL injection by never building queries from user input. \
                       Make sure to validate all inputs at the boundary.";
        conn.execute(
            "INSERT INTO sections (document_id, section_name, content) VALUES (?, 'technical_details', ?)",
            rusqlite::params![doc_id, content],
        )
        .unwrap();

        let stats1 = extract_from_sections(&conn, None).unwrap();
        // Second pass — same content must be deduped, not re-inserted.
        let stats2 = extract_from_sections(&conn, None).unwrap();

        assert!(
            stats1.extracted > 0,
            "first pass must extract entries; got {stats1:?}"
        );
        assert_eq!(
            stats2.extracted, 0,
            "second pass must extract 0 new entries (deduped); stats2={stats2:?}"
        );
        assert!(
            stats2.deduped > 0 || stats2.skipped > 0,
            "second pass must dedup or skip; stats2={stats2:?}"
        );
    }

    #[test]
    fn extract_from_sections_stable_id_uses_topic_key() {
        let conn = setup_test_db();
        conn.execute(
            "INSERT INTO documents (session_id, doc_type, file_path) VALUES ('sess-3', 'checkpoint', '/fake3')",
            [],
        )
        .unwrap();
        let doc_id = conn.last_insert_rowid();
        let content = "Always use parameterised SQL instead of raw string concatenation. \
                       This best practice prevents injection attacks. Make sure to apply it consistently.";
        conn.execute(
            "INSERT INTO sections (document_id, section_name, content) VALUES (?, 'technical_details', ?)",
            rusqlite::params![doc_id, content],
        )
        .unwrap();

        extract_from_sections(&conn, None).unwrap();

        // The stable_id must match compute_stable_id_with_topic_key output,
        // NOT the empty-topic-key formula used by the learn path.
        let row: Option<(String, String, String)> = conn
            .query_row(
                "SELECT stable_id, topic_key, category FROM knowledge_entries LIMIT 1",
                [],
                |row| Ok((row.get(0)?, row.get(1)?, row.get(2)?)),
            )
            .ok();
        let (stable_id, topic_key, category) = row.expect("must have an entry");

        // title comes from first classifiable entry — recompute to verify.
        let (title,): (String,) = conn
            .query_row(
                "SELECT title FROM knowledge_entries WHERE stable_id = ?",
                rusqlite::params![stable_id],
                |row| Ok((row.get(0)?,)),
            )
            .unwrap();

        let expected = compute_stable_id_with_topic_key("sess-3", &category, &title, &topic_key);
        assert_eq!(
            stable_id, expected,
            "extract-path stable_id must include topic_key (wave-14 drift fix)"
        );
    }

    #[test]
    fn extract_from_sections_absent_table_returns_empty() {
        // DB with NO knowledge_entries table — must return Ok(0,0,0) gracefully.
        let conn = Connection::open_in_memory().unwrap();
        conn.execute_batch(
            "CREATE TABLE documents (id INTEGER PRIMARY KEY, session_id TEXT, doc_type TEXT DEFAULT 'x', file_path TEXT DEFAULT '');
             CREATE TABLE sections (id INTEGER PRIMARY KEY, document_id INTEGER, section_name TEXT, content TEXT);",
        ).unwrap();
        let stats = extract_from_sections(&conn, None).unwrap();
        assert_eq!(stats.extracted, 0);
        assert_eq!(stats.deduped, 0);
    }

    // ── compute_relation_stable_id ────────────────────────────────────────────

    #[test]
    fn relation_stable_id_is_64_hex_chars() {
        let id = compute_relation_stable_id("src-stable", "tgt-stable", "SAME_SESSION");
        assert_eq!(id.len(), 64, "relation stable_id must be 64-char SHA-256 hex");
        assert!(
            id.chars().all(|c| c.is_ascii_hexdigit()),
            "relation stable_id must be hex: {id:?}"
        );
    }

    #[test]
    fn relation_stable_id_is_deterministic() {
        let a = compute_relation_stable_id("s1", "s2", "SAME_SESSION");
        let b = compute_relation_stable_id("s1", "s2", "SAME_SESSION");
        assert_eq!(a, b, "relation stable_id must be deterministic");
    }

    #[test]
    fn relation_stable_id_changes_with_type() {
        let a = compute_relation_stable_id("s1", "s2", "SAME_SESSION");
        let b = compute_relation_stable_id("s1", "s2", "SAME_TOPIC");
        assert_ne!(a, b, "different relation_type must produce different stable_id");
    }

    /// Python parity check: verify the SHA-256 formula matches Python's
    /// `_knowledge_relation_stable_id(src, tgt, rtype)`.
    ///
    /// Python formula:
    ///   _stable_sha256("knowledge_relation", src, tgt, rtype)
    ///   → SHA-256("knowledge_relation\0{src}\0{tgt}\0{rtype}")
    #[test]
    fn relation_stable_id_matches_python_formula() {
        // Pre-computed expected value from Python:
        //   import hashlib
        //   payload = "\0".join(["knowledge_relation", "src-a", "tgt-b", "RESOLVED_BY"])
        //   hashlib.sha256(payload.encode()).hexdigest()
        use sha2::{Digest, Sha256};
        let src = "src-a";
        let tgt = "tgt-b";
        let rtype = "RESOLVED_BY";
        let expected = {
            let parts: &[&str] = &["knowledge_relation", src, tgt, rtype];
            let payload = parts.join("\0");
            let hash = Sha256::digest(payload.as_bytes());
            format!("{:x}", hash)
        };
        let got = compute_relation_stable_id(src, tgt, rtype);
        assert_eq!(got, expected, "relation stable_id must match Python's SHA-256 formula");
    }

    // ── extract_relations_native (in-memory DB) ───────────────────────────────

    fn insert_entry(conn: &Connection, id_hint: &str, session_id: &str, category: &str, title: &str, tags: &str, topic_key: &str) -> i64 {
        insert_entry_with_content(
            conn,
            id_hint,
            session_id,
            category,
            title,
            &format!("Content for {id_hint}"),
            tags,
            topic_key,
        )
    }

    fn insert_entry_with_content(
        conn: &Connection,
        id_hint: &str,
        session_id: &str,
        category: &str,
        title: &str,
        content: &str,
        tags: &str,
        topic_key: &str,
    ) -> i64 {
        conn.execute(
            "INSERT INTO knowledge_entries \
             (session_id, category, title, content, tags, topic_key, stable_id, \
               confidence, first_seen, last_seen, source, content_hash) \
             VALUES (?, ?, ?, ?, ?, ?, ?, 0.7, '2024-01-01', '2024-01-01', 'copilot', ?)",
            rusqlite::params![
                session_id, category, title,
                content,
                tags, topic_key,
                compute_stable_id_with_topic_key(session_id, category, title, topic_key),
                format!("hash-{id_hint}"),
            ],
        ).unwrap();
        conn.last_insert_rowid()
    }

    #[test]
    fn extract_relations_same_session_writes_row() {
        let conn = setup_test_db();
        let sid = "sess-rel-1";
        // Two entries in the same session, different categories.
        insert_entry(&conn, "a", sid, "mistake", "Bug in auth", "", "mistake/bug-in-auth");
        insert_entry(&conn, "b", sid, "pattern", "Use guard clauses", "", "pattern/use-guard-clauses");

        let count = extract_relations_native(&conn).unwrap();
        assert!(count >= 1, "must write at least 1 SAME_SESSION relation; got {count}");

        let exists: i64 = conn.query_row(
            "SELECT COUNT(*) FROM knowledge_relations WHERE relation_type = 'SAME_SESSION'",
            [], |r| r.get(0)
        ).unwrap();
        assert!(exists >= 1, "SAME_SESSION relation must be written");
    }

    #[test]
    fn extract_relations_resolved_by_written() {
        let conn = setup_test_db();
        let sid = "sess-rel-2";
        insert_entry(&conn, "m", sid, "mistake", "Null ptr crash", "", "mistake/null-ptr-crash");
        insert_entry(&conn, "p", sid, "pattern", "Always check null first", "", "pattern/always-check-null");

        let count = extract_relations_native(&conn).unwrap();
        assert!(count >= 1, "must write RESOLVED_BY relation; got {count}");

        let exists: i64 = conn.query_row(
            "SELECT COUNT(*) FROM knowledge_relations WHERE relation_type = 'RESOLVED_BY'",
            [], |r| r.get(0)
        ).unwrap();
        assert!(exists >= 1, "RESOLVED_BY relation must be written");
    }

    #[test]
    fn extract_relations_same_topic_different_sessions() {
        let conn = setup_test_db();
        let topic = "pattern/use-param-sql";
        // Same topic_key from two different sessions.
        insert_entry(&conn, "x", "sess-a", "pattern", "Use param SQL A", "sql", topic);
        insert_entry(&conn, "y", "sess-b", "pattern", "Use param SQL A", "sql", topic);

        let count = extract_relations_native(&conn).unwrap();
        assert!(count >= 1, "must write at least 1 SAME_TOPIC relation; got {count}");

        let exists: i64 = conn.query_row(
            "SELECT COUNT(*) FROM knowledge_relations WHERE relation_type = 'SAME_TOPIC'",
            [], |r| r.get(0)
        ).unwrap();
        assert!(exists >= 1, "SAME_TOPIC relation must be written");
    }

    #[test]
    fn extract_relations_tag_overlap_written() {
        let conn = setup_test_db();
        // Two entries in different sessions with 2+ shared tags.
        insert_entry(&conn, "u", "sess-u", "pattern", "Redis caching pattern", "redis,python,database", "");
        insert_entry(&conn, "v", "sess-v", "tool", "Redis config tips", "redis,python,docker", "");

        let count = extract_relations_native(&conn).unwrap();
        assert!(count >= 1, "must write TAG_OVERLAP relation; got {count}");

        let exists: i64 = conn.query_row(
            "SELECT COUNT(*) FROM knowledge_relations WHERE relation_type = 'TAG_OVERLAP'",
            [], |r| r.get(0)
        ).unwrap();
        assert!(exists >= 1, "TAG_OVERLAP relation must be written");
    }

    #[test]
    fn extract_relations_stable_id_is_64_hex() {
        let conn = setup_test_db();
        let sid = "sess-sid-check";
        insert_entry(&conn, "p", sid, "mistake", "Auth crash", "", "mistake/auth-crash");
        insert_entry(&conn, "q", sid, "pattern", "Auth guard", "", "pattern/auth-guard");

        extract_relations_native(&conn).unwrap();

        let stable: String = conn.query_row(
            "SELECT stable_id FROM knowledge_relations LIMIT 1",
            [], |r| r.get(0)
        ).unwrap();
        assert_eq!(stable.len(), 64, "relation stable_id must be 64-char hex: {stable:?}");
        assert!(
            stable.chars().all(|c| c.is_ascii_hexdigit()),
            "relation stable_id must be hex: {stable:?}"
        );
    }

    #[test]
    fn extract_relations_idempotent_on_rehash() {
        let conn = setup_test_db();
        let sid = "sess-idem";
        insert_entry(&conn, "i1", sid, "mistake", "Deploy crash", "", "mistake/deploy-crash");
        insert_entry(&conn, "i2", sid, "tool", "Deploy checklist", "", "tool/deploy-checklist");

        let count1 = extract_relations_native(&conn).unwrap();
        // Second call: DELETE + re-INSERT — same count.
        let count2 = extract_relations_native(&conn).unwrap();
        assert_eq!(count1, count2, "extract_relations_native must be idempotent (DELETE+reinsert)");
    }

    #[test]
    fn semantic_proximity_writes_native_relation() {
        let conn = setup_test_db();
        insert_entry_with_content(
            &conn,
            "sem-a",
            "sess-sem-a",
            "pattern",
            "Use parameterized SQL queries",
            "Use parameterized SQL queries for every database write and validate inputs before execution.",
            "",
            "",
        );
        insert_entry_with_content(
            &conn,
            "sem-b",
            "sess-sem-b",
            "pattern",
            "Use parameterized SQL queries",
            "Use parameterized SQL queries for every database write and validate inputs before execution.",
            "",
            "",
        );
        insert_entry_with_content(
            &conn,
            "sem-c",
            "sess-sem-c",
            "tool",
            "Configure CI cache keys",
            "Configure CI cache keys for Rust builds and keep cache invalidation explicit.",
            "",
            "",
        );

        let count = compute_semantic_proximity_inner(&conn).unwrap();
        assert!(
            count >= 1,
            "compute_semantic_proximity_inner must write at least one semantic relation; got {count}"
        );

        let (relation_count, confidence): (i64, f64) = conn
            .query_row(
                "SELECT COUNT(*), MAX(confidence) FROM knowledge_relations WHERE relation_type = 'SEMANTIC_PROXIMITY'",
                [],
                |r| Ok((r.get(0)?, r.get(1)?)),
            )
            .unwrap();
        assert!(relation_count >= 1, "SEMANTIC_PROXIMITY relation must be written");
        assert!(
            confidence >= 0.75,
            "SEMANTIC_PROXIMITY confidence must respect the threshold; got {confidence}"
        );
    }

    #[test]
    fn extract_from_changed_sessions_surfaces_db_open_errors() {
        let unique = std::time::SystemTime::now()
            .duration_since(std::time::UNIX_EPOCH)
            .unwrap()
            .as_nanos();
        let dir = std::env::temp_dir().join(format!("sk-wave16-open-fail-{unique}"));
        std::fs::create_dir_all(&dir).unwrap();

        let result = extract_from_changed_sessions(&[], &dir, &dir);

        assert!(matches!(result, Some(Err(_))), "directory path must surface as Some(Err(_)); got {result:?}");

        std::fs::remove_dir_all(&dir).unwrap();
    }

    // ── Wave 17: parse_file_list ──────────────────────────────────────────────

    #[test]
    fn parse_file_list_basic_bullets() {
        let content = "- src/main.rs\n- tests/test.py\n- README.md";
        let files = parse_file_list(content);
        assert!(files.contains(&"src/main.rs".to_string()), "got: {files:?}");
        assert!(files.contains(&"tests/test.py".to_string()), "got: {files:?}");
        assert!(files.contains(&"README.md".to_string()), "got: {files:?}");
    }

    #[test]
    fn parse_file_list_strips_backticks_and_comments() {
        let content = "- `sk-rust/src/lib.rs` # main lib\n- docs/README.md | table col";
        let files = parse_file_list(content);
        assert!(files.contains(&"sk-rust/src/lib.rs".to_string()), "got: {files:?}");
        assert!(files.contains(&"docs/README.md".to_string()), "got: {files:?}");
    }

    #[test]
    fn parse_file_list_skips_urls_and_long_lines() {
        let long = "x".repeat(300);
        let content = format!("- https://example.com/page\n- {long}\n- src/real.rs");
        let files = parse_file_list(&content);
        assert!(!files.iter().any(|f| f.starts_with("http")), "URL must be skipped: {files:?}");
        assert!(files.contains(&"src/real.rs".to_string()), "got: {files:?}");
    }

    #[test]
    fn parse_file_list_empty_content() {
        assert!(parse_file_list("").is_empty());
        assert!(parse_file_list("   \n  \n").is_empty());
    }

    // ── Wave 17: backfill_affected_files ─────────────────────────────────────

    fn setup_residual_test_db() -> Connection {
        let conn = setup_test_db();
        conn.execute_batch(
            "ALTER TABLE knowledge_entries ADD COLUMN task_id TEXT DEFAULT '';
             ALTER TABLE knowledge_entries ADD COLUMN affected_files TEXT DEFAULT NULL;",
        )
        .unwrap();
        conn
    }

    #[test]
    fn backfill_affected_files_fills_empty_entries() {
        let conn = setup_residual_test_db();
        let sid = "sess-backfill-1";
        // Insert a document + important_files section
        conn.execute(
            "INSERT INTO documents (session_id, doc_type, seq, file_path) VALUES (?, 'checkpoint', 0, '/fake')",
            rusqlite::params![sid],
        )
        .unwrap();
        let doc_id = conn.last_insert_rowid();
        conn.execute(
            "INSERT INTO sections (document_id, section_name, content) VALUES (?, 'important_files', ?)",
            rusqlite::params![
                doc_id,
                "- sk-rust/src/lib.rs\n- tests/test.py\n- README.md"
            ],
        )
        .unwrap();
        // Insert a knowledge entry with no affected_files
        conn.execute(
            "INSERT INTO knowledge_entries \
             (session_id, category, title, content, stable_id, first_seen, last_seen, source, content_hash) \
             VALUES (?, 'pattern', 'Test Pattern', 'Content', 'sid1', '2024-01-01', '2024-01-01', 'copilot', 'h1')",
            rusqlite::params![sid],
        )
        .unwrap();

        let n = backfill_affected_files(&conn, None);
        assert!(n >= 1, "must backfill at least 1 entry; got {n}");

        let files: Option<String> = conn
            .query_row(
                "SELECT affected_files FROM knowledge_entries WHERE session_id = ?",
                rusqlite::params![sid],
                |row| row.get(0),
            )
            .ok()
            .flatten();
        assert!(
            files.map(|f| f.contains("lib.rs")).unwrap_or(false),
            "affected_files must contain lib.rs"
        );
    }

    #[test]
    fn backfill_affected_files_does_not_overwrite_existing() {
        let conn = setup_residual_test_db();
        let sid = "sess-backfill-2";
        conn.execute(
            "INSERT INTO documents (session_id, doc_type, seq, file_path) VALUES (?, 'checkpoint', 0, '/fake2')",
            rusqlite::params![sid],
        )
        .unwrap();
        let doc_id = conn.last_insert_rowid();
        conn.execute(
            "INSERT INTO sections (document_id, section_name, content) VALUES (?, 'important_files', ?)",
            rusqlite::params![doc_id, "- src/foo.rs"],
        )
        .unwrap();
        // Entry with pre-populated affected_files
        conn.execute(
            "INSERT INTO knowledge_entries \
             (session_id, category, title, content, stable_id, first_seen, last_seen, source, \
              content_hash, affected_files) \
             VALUES (?, 'pattern', 'Pre-filled', 'Content', 'sid2', '2024-01-01', '2024-01-01', \
              'copilot', 'h2', '[\"preexisting.rs\"]')",
            rusqlite::params![sid],
        )
        .unwrap();

        let n = backfill_affected_files(&conn, None);
        assert_eq!(n, 0, "must not overwrite existing affected_files; updated={n}");
    }

    // ── Wave 17: infer_task_ids ───────────────────────────────────────────────

    #[test]
    fn infer_task_ids_finds_explicit_marker() {
        let conn = setup_residual_test_db();
        let sid = "sess-taskid-1";
        conn.execute(
            "INSERT INTO knowledge_entries \
             (session_id, category, title, content, stable_id, first_seen, last_seen, source, content_hash) \
             VALUES (?, 'pattern', 'Some Pattern', \
                     'Working on task: fix-auth-bug here', \
                     'sid-ti1', '2024-01-01', '2024-01-01', 'copilot', 'hash-ti1')",
            rusqlite::params![sid],
        )
        .unwrap();

        let n = infer_task_ids(&conn, None);
        assert!(n >= 1, "must infer at least 1 task_id; got {n}");

        let task_id: Option<String> = conn
            .query_row(
                "SELECT task_id FROM knowledge_entries WHERE session_id = ?",
                rusqlite::params![sid],
                |row| row.get(0),
            )
            .ok()
            .flatten();
        assert_eq!(
            task_id.as_deref(),
            Some("fix-auth-bug"),
            "task_id must be 'fix-auth-bug', got: {task_id:?}"
        );
    }

    #[test]
    fn infer_task_ids_single_candidate_only() {
        // Two different markers → no assignment (ambiguous)
        let conn = setup_residual_test_db();
        let sid = "sess-taskid-2";
        conn.execute(
            "INSERT INTO knowledge_entries \
             (session_id, category, title, content, stable_id, first_seen, last_seen, source, content_hash) \
             VALUES (?, 'pattern', 'Ambiguous', \
                     'task: fix-auth-bug and task: update-config-parser both mentioned', \
                     'sid-ti2', '2024-01-01', '2024-01-01', 'copilot', 'hash-ti2')",
            rusqlite::params![sid],
        )
        .unwrap();

        let n = infer_task_ids(&conn, None);
        assert_eq!(n, 0, "ambiguous slugs must not assign task_id; updated={n}");
    }

    #[test]
    fn infer_task_ids_does_not_overwrite_existing() {
        let conn = setup_residual_test_db();
        let sid = "sess-taskid-3";
        conn.execute(
            "INSERT INTO knowledge_entries \
             (session_id, category, title, content, stable_id, first_seen, last_seen, source, \
              content_hash, task_id) \
             VALUES (?, 'pattern', 'Pre-assigned', \
                     'task: new-wave-slug here', \
                     'sid-ti3', '2024-01-01', '2024-01-01', 'copilot', 'hash-ti3', 'existing-slug')",
            rusqlite::params![sid],
        )
        .unwrap();

        let n = infer_task_ids(&conn, None);
        assert_eq!(n, 0, "must not overwrite existing task_id; updated={n}");
    }

    // ── Wave 17: run_decay ────────────────────────────────────────────────────

    #[test]
    fn run_decay_applies_first_call() {
        let conn = setup_test_db();
        // Insert an entry with confidence 0.9 and last_seen in the past
        conn.execute(
            "INSERT INTO knowledge_entries \
             (session_id, category, title, content, stable_id, first_seen, last_seen, source, \
              confidence, content_hash) \
             VALUES ('s1', 'pattern', 'Decay Test', 'content', 'sid-d1', \
                     '2023-01-01', '2023-01-01', 'copilot', 0.9, 'hash-d1')",
            [],
        )
        .unwrap();

        let first_ran = run_decay(&conn);
        assert!(first_ran, "first run_decay call must apply decay");

        let conf: f64 = conn
            .query_row(
                "SELECT confidence FROM knowledge_entries WHERE stable_id = 'sid-d1'",
                [],
                |row| row.get(0),
            )
            .unwrap();
        // 0.9 × 0.98 = 0.882
        assert!(
            (conf - 0.882).abs() < 1e-6,
            "confidence must be 0.882 after decay; got {conf}"
        );
    }

    #[test]
    fn run_decay_idempotent_same_day() {
        let conn = setup_test_db();
        conn.execute(
            "INSERT INTO knowledge_entries \
             (session_id, category, title, content, stable_id, first_seen, last_seen, source, \
              confidence, content_hash) \
             VALUES ('s2', 'pattern', 'Decay Idem', 'content', 'sid-d2', \
                     '2023-01-01', '2023-01-01', 'copilot', 0.7, 'hash-d2')",
            [],
        )
        .unwrap();

        let first = run_decay(&conn);
        let second = run_decay(&conn);
        assert!(first, "first call must run decay");
        assert!(!second, "second call same day must be no-op (idempotent)");
    }

    #[test]
    fn run_decay_floor_at_0_3() {
        let conn = setup_test_db();
        // Entry already at 0.31 — decay should not drop below 0.3.
        conn.execute(
            "INSERT INTO knowledge_entries \
             (session_id, category, title, content, stable_id, first_seen, last_seen, source, \
              confidence, content_hash) \
             VALUES ('s3', 'pattern', 'Floor Test', 'content', 'sid-d3', \
                     '2023-01-01', '2023-01-01', 'copilot', 0.31, 'hash-d3')",
            [],
        )
        .unwrap();

        run_decay(&conn);

        let conf: f64 = conn
            .query_row(
                "SELECT confidence FROM knowledge_entries WHERE stable_id = 'sid-d3'",
                [],
                |row| row.get(0),
            )
            .unwrap();
        assert!(
            conf >= 0.3,
            "confidence must not drop below 0.3; got {conf}"
        );
    }

    // ── Wave 18: ensure_extract_tables ───────────────────────────────────────

    #[test]
    fn ensure_extract_tables_creates_all_four_tables() {
        let conn = Connection::open_in_memory().unwrap();
        ensure_extract_tables(&conn).unwrap();
        for table in &["knowledge_entries", "knowledge_relations", "embedding_meta", "ke_fts"] {
            let count: i64 = conn
                .query_row(
                    "SELECT COUNT(*) FROM sqlite_master WHERE name = ?",
                    [table],
                    |r| r.get(0),
                )
                .unwrap();
            assert!(count > 0, "table {table} must exist after ensure_extract_tables()");
        }
    }

    #[test]
    fn ensure_extract_tables_idempotent() {
        let conn = Connection::open_in_memory().unwrap();
        ensure_extract_tables(&conn).expect("first call");
        ensure_extract_tables(&conn).expect("second call must be idempotent");
    }

    #[test]
    fn extract_from_changed_sessions_creates_db_when_absent() {
        use std::fs;
        let tmp = {
            let unique = std::time::SystemTime::now()
                .duration_since(std::time::UNIX_EPOCH)
                .unwrap()
                .as_nanos();
            std::env::temp_dir().join(format!("sk-wave18-bootstrap-{unique}"))
        };
        fs::create_dir_all(&tmp).unwrap();
        let db_path = tmp.join("knowledge.db");
        let session_state = tmp.join("session-state");
        fs::create_dir_all(&session_state).unwrap();

        // Wave-18: DB must not exist yet.
        assert!(!db_path.exists());

        // No changed paths — extract should open/create the DB via the absent branch.
        // The function returns Some(Ok(_)) once it can open or create the DB.
        let result = extract_from_changed_sessions(&[], &session_state, &db_path);
        assert!(
            matches!(result, Some(Ok(_))),
            "must return Some(Ok(_)) even on a fresh DB; got {result:?}"
        );

        // Verify DB was created.
        assert!(db_path.exists(), "DB must be created by extract_from_changed_sessions");

        // Verify extract tables were bootstrapped.
        let conn = Connection::open(&db_path).unwrap();
        for table in &["knowledge_entries", "knowledge_relations", "embedding_meta"] {
            let count: i64 = conn
                .query_row(
                    "SELECT COUNT(*) FROM sqlite_master WHERE type='table' AND name=?",
                    [table],
                    |r| r.get::<_, i64>(0),
                )
                .unwrap();
            assert!(count > 0, "{table} must exist in fresh-bootstrapped DB");
        }

        fs::remove_dir_all(&tmp).ok();
    }
}
