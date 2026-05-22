//! Pure-Rust TF-IDF build and search.
//!
//! Implements a TF-IDF vectorizer that:
//! - Tokenizes text using a simple ASCII tokenizer (words of 2+ chars, unigrams + bigrams)
//! - Builds a vocabulary of up to 8 000 features with smooth IDF weights
//! - Uses sublinear TF: `1 + log(tf)` when `tf > 0`
//! - L2-normalises document vectors
//!
//! The model is serialised as JSON in the same format as Python's
//! `build_tfidf()` in `embed.py`, allowing cross-use between Rust and Python:
//!
//! ```json
//! {
//!   "vocabulary": {"term": index, ...},
//!   "idf":        [float, ...],
//!   "matrix_row": [int, ...],
//!   "matrix_col": [int, ...],
//!   "matrix_data":[float, ...],
//!   "matrix_shape":[n_docs, n_terms],
//!   "doc_ids":    [int, ...],
//!   "params":     { ... }
//! }
//! ```
//!
//! # Tokenisation difference from Python
//! Python's sklearn uses `(?u)\b\w\w+\b` with `strip_accents="unicode"`.
//! Our tokeniser uses ASCII-only alphanumeric sequences of length ≥ 2.
//! For English session-knowledge content this difference is negligible.

use std::collections::HashMap;
use std::sync::{Arc, Mutex};

// ── Binary format (#356) ──────────────────────────────────────────────────

/// Magic bytes for the compact binary TF-IDF format (SKTIDF + version 0x01).
///
/// Rust prefers this format when available; Python always reads JSON.
/// A missing or corrupt binary blob falls back to JSON automatically.
pub const BINARY_MAGIC: &[u8] = b"SKTIDF\x01";

// Header layout after magic (19 bytes total):
//   [7..11]  n_vocab:  u32 LE
//   [11..15] n_docs:   u32 LE
//   [15..19] n_nnz:    u32 LE
// Data blocks (in order):
//   idf:         n_vocab * f64 LE
//   doc_ids:     n_docs  * i64 LE
//   matrix_row:  n_nnz   * u32 LE
//   matrix_col:  n_nnz   * u32 LE
//   matrix_data: n_nnz   * f64 LE
//   vocabulary:  n_vocab * (col_idx: u32, term_len: u16, term_bytes: [u8; term_len])

fn write_u16_le(buf: &mut Vec<u8>, v: u16) {
    buf.extend_from_slice(&v.to_le_bytes());
}
fn write_u32_le(buf: &mut Vec<u8>, v: u32) {
    buf.extend_from_slice(&v.to_le_bytes());
}
fn write_i64_le(buf: &mut Vec<u8>, v: i64) {
    buf.extend_from_slice(&v.to_le_bytes());
}
fn write_f64_le(buf: &mut Vec<u8>, v: f64) {
    buf.extend_from_slice(&v.to_le_bytes());
}

fn read_u16_le(data: &[u8], pos: &mut usize) -> Option<u16> {
    let end = pos.checked_add(2)?;
    if end > data.len() {
        return None;
    }
    let v = u16::from_le_bytes(data[*pos..end].try_into().ok()?);
    *pos = end;
    Some(v)
}
fn read_u32_le(data: &[u8], pos: &mut usize) -> Option<u32> {
    let end = pos.checked_add(4)?;
    if end > data.len() {
        return None;
    }
    let v = u32::from_le_bytes(data[*pos..end].try_into().ok()?);
    *pos = end;
    Some(v)
}
fn read_i64_le(data: &[u8], pos: &mut usize) -> Option<i64> {
    let end = pos.checked_add(8)?;
    if end > data.len() {
        return None;
    }
    let v = i64::from_le_bytes(data[*pos..end].try_into().ok()?);
    *pos = end;
    Some(v)
}
fn read_f64_le(data: &[u8], pos: &mut usize) -> Option<f64> {
    let end = pos.checked_add(8)?;
    if end > data.len() {
        return None;
    }
    let v = f64::from_le_bytes(data[*pos..end].try_into().ok()?);
    *pos = end;
    Some(v)
}

// ── Parsed model type ─────────────────────────────────────────────────────

/// A fully-parsed TF-IDF model ready for repeated search queries.
///
/// Parsing is expensive (JSON or binary deserialization). This struct holds
/// the decoded model so the in-memory cache (`TFIDF_CACHE`) can serve
/// subsequent queries without re-parsing.
pub struct TfIdfModel {
    pub vocabulary: HashMap<String, usize>,
    pub idf: Vec<f64>,
    pub matrix_row: Vec<usize>,
    pub matrix_col: Vec<usize>,
    pub matrix_data: Vec<f64>,
    pub doc_ids: Vec<i64>,
    pub n_docs: usize,
    pub n_terms: usize,
}

impl TfIdfModel {
    /// Parse from the canonical JSON blob (Python-compatible format).
    ///
    /// Rejects legacy pickle blobs (prefix `\x80\x04` / `\x80\x05`).
    pub fn from_json_blob(blob: &[u8]) -> Option<Self> {
        if blob.starts_with(b"\x80\x04") || blob.starts_with(b"\x80\x05") {
            return None;
        }
        let model: serde_json::Value = serde_json::from_slice(blob).ok()?;

        let vocabulary: HashMap<String, usize> = model
            .get("vocabulary")?
            .as_object()?
            .iter()
            .filter_map(|(k, v)| v.as_u64().map(|idx| (k.clone(), idx as usize)))
            .collect();

        let idf: Vec<f64> = model
            .get("idf")?
            .as_array()?
            .iter()
            .map(|v| v.as_f64().unwrap_or(0.0))
            .collect();

        let matrix_row: Vec<usize> = model
            .get("matrix_row")?
            .as_array()?
            .iter()
            .map(|v| v.as_i64().unwrap_or(0) as usize)
            .collect();
        let matrix_col: Vec<usize> = model
            .get("matrix_col")?
            .as_array()?
            .iter()
            .map(|v| v.as_i64().unwrap_or(0) as usize)
            .collect();
        let matrix_data: Vec<f64> = model
            .get("matrix_data")?
            .as_array()?
            .iter()
            .map(|v| v.as_f64().unwrap_or(0.0))
            .collect();

        let doc_ids: Vec<i64> = model
            .get("doc_ids")?
            .as_array()?
            .iter()
            .map(|v| v.as_i64().unwrap_or(0))
            .collect();
        let matrix_shape: Vec<usize> = model
            .get("matrix_shape")?
            .as_array()?
            .iter()
            .map(|v| v.as_u64().unwrap_or(0) as usize)
            .collect();

        if matrix_shape.len() < 2 {
            return None;
        }

        Some(TfIdfModel {
            vocabulary,
            idf,
            matrix_row,
            matrix_col,
            matrix_data,
            doc_ids,
            n_docs: matrix_shape[0],
            n_terms: matrix_shape[1],
        })
    }

    /// Parse from the compact binary format (SKTIDF\x01 magic).
    ///
    /// Returns `None` if the magic does not match or the data is truncated.
    /// Callers should fall back to `from_json_blob` on `None`.
    pub fn from_binary(data: &[u8]) -> Option<Self> {
        if !data.starts_with(BINARY_MAGIC) {
            return None;
        }
        let mut pos = BINARY_MAGIC.len();

        let n_vocab = read_u32_le(data, &mut pos)? as usize;
        let n_docs = read_u32_le(data, &mut pos)? as usize;
        let n_nnz = read_u32_le(data, &mut pos)? as usize;

        let mut idf = Vec::with_capacity(n_vocab);
        for _ in 0..n_vocab {
            idf.push(read_f64_le(data, &mut pos)?);
        }

        let mut doc_ids = Vec::with_capacity(n_docs);
        for _ in 0..n_docs {
            doc_ids.push(read_i64_le(data, &mut pos)?);
        }

        let mut matrix_row = Vec::with_capacity(n_nnz);
        for _ in 0..n_nnz {
            matrix_row.push(read_u32_le(data, &mut pos)? as usize);
        }
        let mut matrix_col = Vec::with_capacity(n_nnz);
        for _ in 0..n_nnz {
            matrix_col.push(read_u32_le(data, &mut pos)? as usize);
        }
        let mut matrix_data = Vec::with_capacity(n_nnz);
        for _ in 0..n_nnz {
            matrix_data.push(read_f64_le(data, &mut pos)?);
        }

        let mut vocabulary = HashMap::with_capacity(n_vocab);
        for _ in 0..n_vocab {
            let col_idx = read_u32_le(data, &mut pos)? as usize;
            let term_len = read_u16_le(data, &mut pos)? as usize;
            if pos + term_len > data.len() {
                return None;
            }
            let term = String::from_utf8(data[pos..pos + term_len].to_vec()).ok()?;
            pos += term_len;
            vocabulary.insert(term, col_idx);
        }

        Some(TfIdfModel {
            vocabulary,
            idf,
            matrix_row,
            matrix_col,
            matrix_data,
            doc_ids,
            n_docs,
            n_terms: n_vocab,
        })
    }

    /// Serialise this model to the compact binary format.
    ///
    /// The resulting bytes can be stored in `tfidf_model.model_bin` and
    /// later restored with `from_binary`.  The JSON blob in `model_blob`
    /// remains unchanged for Python compatibility.
    pub fn to_binary(&self) -> Vec<u8> {
        let n_vocab = self.vocabulary.len() as u32;
        let n_docs = self.n_docs as u32;
        let n_nnz = self.matrix_row.len() as u32;

        let capacity = BINARY_MAGIC.len()
            + 12 // n_vocab + n_docs + n_nnz
            + n_vocab as usize * 8   // idf
            + n_docs as usize * 8    // doc_ids
            + n_nnz as usize * 4     // matrix_row
            + n_nnz as usize * 4     // matrix_col
            + n_nnz as usize * 8     // matrix_data
            + n_vocab as usize * 16; // vocab block (upper bound)

        let mut buf = Vec::with_capacity(capacity);

        buf.extend_from_slice(BINARY_MAGIC);
        write_u32_le(&mut buf, n_vocab);
        write_u32_le(&mut buf, n_docs);
        write_u32_le(&mut buf, n_nnz);

        for &v in &self.idf {
            write_f64_le(&mut buf, v);
        }
        for &v in &self.doc_ids {
            write_i64_le(&mut buf, v);
        }
        for &v in &self.matrix_row {
            write_u32_le(&mut buf, v as u32);
        }
        for &v in &self.matrix_col {
            write_u32_le(&mut buf, v as u32);
        }
        for &v in &self.matrix_data {
            write_f64_le(&mut buf, v);
        }

        // Vocabulary sorted by column index
        let mut vocab_sorted: Vec<(&str, u32)> = self
            .vocabulary
            .iter()
            .map(|(k, &v)| (k.as_str(), v as u32))
            .collect();
        vocab_sorted.sort_by_key(|(_, idx)| *idx);

        for (term, col_idx) in vocab_sorted {
            let term_bytes = term.as_bytes();
            let term_len = term_bytes.len().min(65535) as u16;
            write_u32_le(&mut buf, col_idx);
            write_u16_le(&mut buf, term_len);
            buf.extend_from_slice(&term_bytes[..term_len as usize]);
        }

        buf
    }

    /// Run TF-IDF cosine similarity search against this parsed model.
    ///
    /// Returns `(section_id, score)` pairs with score > 0.01, sorted descending.
    /// Mirrors the logic of `search_tfidf_native` but operates on the already-
    /// parsed struct instead of a raw blob, enabling cache reuse.
    pub fn search(&self, query: &str, limit: usize) -> Vec<(i64, f32)> {
        if self.doc_ids.is_empty() || self.vocabulary.is_empty() {
            return vec![];
        }

        let query_tokens = tokenize(query);
        let mut query_tf: HashMap<String, usize> = HashMap::new();
        for token in &query_tokens {
            *query_tf.entry(token.clone()).or_insert(0) += 1;
        }

        let mut query_vec: Vec<f64> = vec![0.0; self.n_terms];
        for (term, &tf) in &query_tf {
            if let Some(&col) = self.vocabulary.get(term) {
                if col < self.n_terms && tf > 0 {
                    let tf_val = 1.0 + (tf as f64).ln();
                    query_vec[col] = tf_val * self.idf.get(col).copied().unwrap_or(0.0);
                }
            }
        }

        let qnorm: f64 = query_vec.iter().map(|v| v * v).sum::<f64>().sqrt();
        if qnorm == 0.0 {
            return vec![];
        }
        for v in &mut query_vec {
            *v /= qnorm;
        }

        let mut scores: Vec<f64> = vec![0.0; self.n_docs];
        let len = self
            .matrix_row
            .len()
            .min(self.matrix_col.len())
            .min(self.matrix_data.len());
        for i in 0..len {
            let r = self.matrix_row[i];
            let c = self.matrix_col[i];
            if r < self.n_docs && c < self.n_terms {
                scores[r] += query_vec[c] * self.matrix_data[i];
            }
        }

        let mut results: Vec<(i64, f32)> = scores
            .iter()
            .enumerate()
            .filter(|(_, &s)| s > 0.01)
            .map(|(i, &s)| (self.doc_ids.get(i).copied().unwrap_or(0), s as f32))
            .collect();

        results.sort_by(|a, b| b.1.partial_cmp(&a.1).unwrap_or(std::cmp::Ordering::Equal));
        results.truncate(limit);
        results
    }
}

// ── In-memory cache (issue #355) ──────────────────────────────────────────

struct CachedTfIdf {
    generation: String,
    model: Arc<TfIdfModel>,
}

/// Process-global TF-IDF cache, protected by a `Mutex` for concurrent safety.
///
/// `Arc<TfIdfModel>` allows returning a reference-counted clone so callers
/// can release the lock before running the (potentially expensive) search.
static TFIDF_CACHE: Mutex<Option<CachedTfIdf>> = Mutex::new(None);

/// Return a reference-counted handle to the parsed TF-IDF model.
///
/// If the cache holds a model whose `generation` matches, it is returned
/// immediately (O(1), no parsing).  Otherwise `blob` is parsed (JSON or
/// binary based on `is_binary`), stored in the cache, and returned.
///
/// Returns `None` if parsing fails — the caller should fall back gracefully.
pub fn get_or_update_tfidf_cache(
    generation: &str,
    blob: &[u8],
    is_binary: bool,
) -> Option<Arc<TfIdfModel>> {
    let mut guard = TFIDF_CACHE.lock().unwrap_or_else(|e| e.into_inner());

    if let Some(ref cached) = *guard {
        if cached.generation == generation {
            return Some(Arc::clone(&cached.model));
        }
    }

    // Cache miss or stale generation → parse.
    let model = if is_binary {
        // Binary preferred; fall back to JSON if magic check fails.
        TfIdfModel::from_binary(blob).or_else(|| TfIdfModel::from_json_blob(blob))?
    } else {
        TfIdfModel::from_json_blob(blob)?
    };

    let arc = Arc::new(model);
    *guard = Some(CachedTfIdf {
        generation: generation.to_string(),
        model: Arc::clone(&arc),
    });
    Some(arc)
}

/// Invalidate the in-memory TF-IDF cache.
///
/// Call this after storing a new model blob so the next search re-parses.
pub fn invalidate_tfidf_cache() {
    let mut guard = TFIDF_CACHE.lock().unwrap_or_else(|e| e.into_inner());
    *guard = None;
}

// ── Tokeniser ────────────────────────────────────────────────────────────

/// Tokenise text into unigrams + bigrams of ASCII-lowercase word tokens (≥ 2 chars).
///
/// Non-ASCII characters are treated as whitespace so accented chars split
/// words rather than corrupt them.  This is a close approximation of
/// sklearn's `strip_accents="unicode"` + default `token_pattern`.
pub fn tokenize(text: &str) -> Vec<String> {
    let normalized: String = text
        .chars()
        .map(|c| {
            if c.is_ascii_alphanumeric() {
                c.to_ascii_lowercase()
            } else {
                ' '
            }
        })
        .collect();

    let unigrams: Vec<&str> = normalized
        .split_ascii_whitespace()
        .filter(|w| w.len() >= 2)
        .collect();

    let mut tokens: Vec<String> = unigrams.iter().map(|&w| w.to_string()).collect();

    // Bigrams (ngram_range=(1,2))
    for window in unigrams.windows(2) {
        tokens.push(format!("{} {}", window[0], window[1]));
    }

    tokens
}

// ── TF-IDF Build ─────────────────────────────────────────────────────────

fn default_params() -> serde_json::Value {
    serde_json::json!({
        "max_features": 8000,
        "ngram_range":  [1, 2],
        "sublinear_tf": true,
        "strip_accents": "unicode",
        "min_df": 1,
        "max_df": 0.95
    })
}

/// Build a TF-IDF model from a slice of texts and their corresponding DB IDs.
///
/// Returns JSON bytes in the format consumed by Python's `search_tfidf()` and
/// by [`search_tfidf_native`].  The model is immediately ready to store in the
/// `tfidf_model.model_blob` column.
///
/// # Hyperparameters (fixed, matching Python defaults)
/// - `max_features = 8000`
/// - `ngram_range  = (1, 2)`
/// - `sublinear_tf = true`
/// - `max_df       = 0.95`
/// - Smooth IDF: `log((1 + n) / (1 + df)) + 1`
pub fn build_tfidf_model(texts: &[&str], doc_ids: &[i64]) -> Vec<u8> {
    let n_docs = texts.len();

    if n_docs == 0 {
        return serde_json::to_vec(&serde_json::json!({
            "vocabulary":    {},
            "idf":           [],
            "matrix_row":    [],
            "matrix_col":    [],
            "matrix_data":   [],
            "matrix_shape":  [0i64, 0i64],
            "doc_ids":       doc_ids,
            "params":        default_params(),
        }))
        .unwrap_or_default();
    }

    const MAX_FEATURES: usize = 8_000;
    const MAX_DF_RATIO: f64 = 0.95;

    // ── Step 1: tokenise + compute TF per doc + document frequency ─────
    let mut doc_term_freqs: Vec<HashMap<String, usize>> = Vec::with_capacity(n_docs);
    let mut doc_freq: HashMap<String, usize> = HashMap::new();

    for text in texts {
        let tokens = tokenize(text);
        let mut tf: HashMap<String, usize> = HashMap::new();
        for token in tokens {
            *tf.entry(token).or_insert(0) += 1;
        }
        for term in tf.keys() {
            *doc_freq.entry(term.clone()).or_insert(0) += 1;
        }
        doc_term_freqs.push(tf);
    }

    // ── Step 2: filter by max_df, select top MAX_FEATURES by df ────────
    let max_df_count = ((MAX_DF_RATIO * n_docs as f64) as usize).max(1);
    let mut vocab_by_df: Vec<(String, usize)> = doc_freq
        .iter()
        .filter(|(_, &df)| df <= max_df_count)
        .map(|(term, &df)| (term.clone(), df))
        .collect();

    // Higher df first; alphabetical tie-break for reproducibility
    vocab_by_df.sort_unstable_by(|a, b| b.1.cmp(&a.1).then(a.0.cmp(&b.0)));
    vocab_by_df.truncate(MAX_FEATURES);
    let n_terms = vocab_by_df.len();

    // ── Step 3: vocabulary map term → column index ──────────────────────
    let mut vocabulary: HashMap<String, usize> = HashMap::with_capacity(n_terms);
    for (idx, (term, _)) in vocab_by_df.iter().enumerate() {
        vocabulary.insert(term.clone(), idx);
    }

    // ── Step 4: smooth IDF ───────────────────────────────────────────────
    let idf: Vec<f64> = vocab_by_df
        .iter()
        .map(|(term, _)| {
            let df = *doc_freq.get(term).unwrap_or(&1) as f64;
            ((1.0 + n_docs as f64) / (1.0 + df)).ln() + 1.0
        })
        .collect();

    // ── Step 5: COO sparse matrix with L2-normalised TF-IDF rows ────────
    let mut row_indices: Vec<i64> = Vec::new();
    let mut col_indices: Vec<i64> = Vec::new();
    let mut data: Vec<f64> = Vec::new();

    for (row_idx, tf_map) in doc_term_freqs.iter().enumerate() {
        let row_entries: Vec<(usize, f64)> = tf_map
            .iter()
            .filter_map(|(term, &tf)| {
                vocabulary.get(term).and_then(|&col| {
                    // sublinear_tf: 1 + ln(tf), then multiply by IDF
                    let tf_val = 1.0 + (tf as f64).ln();
                    let v = tf_val * idf[col];
                    if v.is_finite() && v > 0.0 {
                        Some((col, v))
                    } else {
                        None
                    }
                })
            })
            .collect();

        // L2 normalise
        let norm: f64 = row_entries.iter().map(|(_, v)| v * v).sum::<f64>().sqrt();
        if norm > 0.0 {
            for (col, val) in row_entries {
                row_indices.push(row_idx as i64);
                col_indices.push(col as i64);
                data.push(val / norm);
            }
        }
    }

    // ── Step 6: serialise vocabulary in index order for sklearn compat ──
    let mut vocab_json = serde_json::Map::new();
    let mut sorted_vocab: Vec<(&str, usize)> =
        vocabulary.iter().map(|(k, &v)| (k.as_str(), v)).collect();
    sorted_vocab.sort_by_key(|(_, idx)| *idx);
    for (term, idx) in sorted_vocab {
        vocab_json.insert(term.to_string(), serde_json::json!(idx));
    }

    let model = serde_json::json!({
        "vocabulary":   serde_json::Value::Object(vocab_json),
        "idf":          idf,
        "matrix_row":   row_indices,
        "matrix_col":   col_indices,
        "matrix_data":  data,
        "matrix_shape": [n_docs as i64, n_terms as i64],
        "doc_ids":      doc_ids,
        "params":       default_params(),
    });

    serde_json::to_vec(&model).unwrap_or_default()
}

// ── TF-IDF Search ────────────────────────────────────────────────────────

/// Query a stored TF-IDF model blob and return `(section_id, score)` pairs.
///
/// Auto-detects binary (SKTIDF\x01) vs JSON format; delegates to
/// `TfIdfModel::from_binary` / `TfIdfModel::from_json_blob` accordingly.
/// Rejects legacy pickle blobs (prefix `\x80\x04` / `\x80\x05`).
#[allow(dead_code)] // public test helper / library entry point; not consumed in this binary
pub fn search_tfidf_native(query: &str, model_blob: &[u8], limit: usize) -> Vec<(i64, f32)> {
    if model_blob.starts_with(b"\x80\x04") || model_blob.starts_with(b"\x80\x05") {
        return vec![];
    }
    let model = if model_blob.starts_with(BINARY_MAGIC) {
        TfIdfModel::from_binary(model_blob).or_else(|| TfIdfModel::from_json_blob(model_blob))
    } else {
        TfIdfModel::from_json_blob(model_blob)
    };
    match model {
        Some(m) => m.search(query, limit),
        None => vec![],
    }
}

// ── Tests ─────────────────────────────────────────────────────────────────

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn tokenize_produces_unigrams_and_bigrams() {
        let tokens = tokenize("hello world foo");
        assert!(tokens.contains(&"hello".to_string()));
        assert!(tokens.contains(&"world".to_string()));
        assert!(tokens.contains(&"foo".to_string()));
        assert!(tokens.contains(&"hello world".to_string()));
        assert!(tokens.contains(&"world foo".to_string()));
    }

    #[test]
    fn tokenize_filters_single_char_words() {
        let tokens = tokenize("a bb ccc");
        assert!(!tokens.iter().any(|t| t == "a"), "single chars filtered");
        assert!(tokens.contains(&"bb".to_string()));
        assert!(tokens.contains(&"ccc".to_string()));
    }

    #[test]
    fn tokenize_lowercases() {
        let tokens = tokenize("Hello WORLD");
        assert!(tokens.contains(&"hello".to_string()));
        assert!(tokens.contains(&"world".to_string()));
        assert!(!tokens.iter().any(|t| t.contains("WORLD")));
    }

    #[test]
    fn tokenize_non_ascii_treated_as_space() {
        // Non-ASCII → space; "test" remains
        let tokens = tokenize("café test");
        assert!(tokens.contains(&"test".to_string()));
    }

    #[test]
    fn build_tfidf_empty_corpus_returns_valid_json() {
        let blob = build_tfidf_model(&[], &[]);
        let model: serde_json::Value = serde_json::from_slice(&blob).unwrap();
        assert_eq!(model["matrix_shape"][0], 0);
        assert_eq!(model["vocabulary"].as_object().unwrap().len(), 0);
    }

    #[test]
    fn build_tfidf_single_doc_has_correct_shape() {
        let texts = vec!["hello world rust programming"];
        let doc_ids = vec![42i64];
        let blob = build_tfidf_model(&texts, &doc_ids);
        let model: serde_json::Value = serde_json::from_slice(&blob).unwrap();
        assert!(!model["vocabulary"].as_object().unwrap().is_empty());
        assert_eq!(model["doc_ids"][0], 42);
        assert_eq!(model["matrix_shape"][0], 1i64);
    }

    #[test]
    fn build_tfidf_roundtrip_search_returns_best_match() {
        let texts = vec![
            "rust programming language fast",
            "python scripting language slow",
            "database sql sqlite queries",
        ];
        let doc_ids = vec![1i64, 2, 3];
        let blob = build_tfidf_model(&texts, &doc_ids);

        let results = search_tfidf_native("rust programming", &blob, 10);
        assert!(!results.is_empty(), "expected TF-IDF results");
        assert_eq!(results[0].0, 1, "rust doc should be top result");
    }

    #[test]
    fn search_tfidf_rejects_pickle_blob() {
        let blob = b"\x80\x04some_pickle_data";
        let results = search_tfidf_native("query", blob, 10);
        assert!(results.is_empty(), "pickle blobs must be rejected");
    }

    #[test]
    fn search_tfidf_empty_query_returns_empty() {
        let texts = vec!["hello world"];
        let doc_ids = vec![1i64];
        let blob = build_tfidf_model(&texts, &doc_ids);
        let results = search_tfidf_native("", &blob, 10);
        assert!(results.is_empty());
    }

    #[test]
    fn build_tfidf_params_match_python_defaults() {
        let texts = vec!["test document content"];
        let doc_ids = vec![1i64];
        let blob = build_tfidf_model(&texts, &doc_ids);
        let model: serde_json::Value = serde_json::from_slice(&blob).unwrap();
        let p = &model["params"];
        assert_eq!(p["max_features"], 8000);
        assert_eq!(p["sublinear_tf"], true);
        assert_eq!(p["strip_accents"], "unicode");
        let ng = p["ngram_range"].as_array().unwrap();
        assert_eq!(ng[0], 1);
        assert_eq!(ng[1], 2);
    }

    // ── Binary format tests (#356) ─────────────────────────────────────

    #[test]
    fn binary_magic_identified() {
        let texts = vec!["rust programming language"];
        let doc_ids = vec![1i64];
        let json_blob = build_tfidf_model(&texts, &doc_ids);
        let model = TfIdfModel::from_json_blob(&json_blob).unwrap();
        let bin = model.to_binary();
        assert!(
            bin.starts_with(BINARY_MAGIC),
            "binary must start with magic bytes"
        );
    }

    #[test]
    fn binary_roundtrip_preserves_vocabulary_and_idf() {
        let texts = vec!["alpha beta gamma", "alpha delta epsilon"];
        let doc_ids = vec![10i64, 20];
        let json_blob = build_tfidf_model(&texts, &doc_ids);
        let from_json = TfIdfModel::from_json_blob(&json_blob).unwrap();
        let bin = from_json.to_binary();
        let from_bin = TfIdfModel::from_binary(&bin).unwrap();

        assert_eq!(from_json.vocabulary.len(), from_bin.vocabulary.len());
        assert_eq!(from_json.idf.len(), from_bin.idf.len());
        assert_eq!(from_json.n_docs, from_bin.n_docs);
        assert_eq!(from_json.n_terms, from_bin.n_terms);
        assert_eq!(from_json.doc_ids, from_bin.doc_ids);

        for (term, &idx) in &from_json.vocabulary {
            assert_eq!(
                from_bin.vocabulary.get(term),
                Some(&idx),
                "vocab mismatch: {term}"
            );
        }
        for (i, (&a, &b)) in from_json.idf.iter().zip(from_bin.idf.iter()).enumerate() {
            assert!((a - b).abs() < 1e-12, "idf[{i}] mismatch: {a} vs {b}");
        }
    }

    #[test]
    fn json_binary_search_parity() {
        let texts = vec![
            "rust programming language fast",
            "python scripting language slow",
            "database sql queries",
        ];
        let doc_ids = vec![1i64, 2, 3];
        let json_blob = build_tfidf_model(&texts, &doc_ids);
        let json_results = search_tfidf_native("rust programming", &json_blob, 10);

        let model = TfIdfModel::from_json_blob(&json_blob).unwrap();
        let bin = model.to_binary();
        let bin_results = search_tfidf_native("rust programming", &bin, 10);

        assert_eq!(
            json_results.len(),
            bin_results.len(),
            "JSON and binary must return same number of results"
        );
        for (j, b) in json_results.iter().zip(bin_results.iter()) {
            assert_eq!(j.0, b.0, "doc_ids must match");
            assert!(
                (j.1 - b.1).abs() < 1e-4,
                "scores must be close: {} vs {}",
                j.1,
                b.1
            );
        }
    }

    #[test]
    fn from_binary_rejects_bad_magic() {
        let data = b"BADMAGIC some random bytes";
        assert!(TfIdfModel::from_binary(data).is_none());
    }

    #[test]
    fn from_binary_rejects_truncated_header() {
        let data = b"SKTIDF\x01\x01"; // magic ok but incomplete u32s
        assert!(TfIdfModel::from_binary(data).is_none());
    }

    #[test]
    fn search_tfidf_native_accepts_binary() {
        let texts = vec!["binary format acceptance test rust"];
        let doc_ids = vec![99i64];
        let json_blob = build_tfidf_model(&texts, &doc_ids);
        let model = TfIdfModel::from_json_blob(&json_blob).unwrap();
        let bin = model.to_binary();
        let results = search_tfidf_native("binary format", &bin, 5);
        assert!(
            !results.is_empty(),
            "binary blob must be searchable via search_tfidf_native"
        );
    }

    // ── Cache tests (#355) ────────────────────────────────────────────────

    #[test]
    fn cache_hit_returns_same_arc() {
        invalidate_tfidf_cache();
        let texts = vec!["cache test document one", "cache test document two"];
        let doc_ids = vec![100i64, 200];
        let json_blob = build_tfidf_model(&texts, &doc_ids);
        let gen = "2025-01-01T00:00:00_cache_hit";

        let arc1 = get_or_update_tfidf_cache(gen, &json_blob, false).unwrap();
        let arc2 = get_or_update_tfidf_cache(gen, &json_blob, false).unwrap();
        assert!(Arc::ptr_eq(&arc1, &arc2), "cache hit must return same Arc");
    }

    #[test]
    fn cache_invalidated_on_new_generation() {
        invalidate_tfidf_cache();
        let texts = vec!["invalidation test alpha"];
        let doc_ids = vec![1i64];
        let blob = build_tfidf_model(&texts, &doc_ids);
        let gen1 = "2025-01-01T00:00:00_inv_gen1";
        let gen2 = "2025-01-02T00:00:00_inv_gen2";

        let arc1 = get_or_update_tfidf_cache(gen1, &blob, false).unwrap();
        let arc2 = get_or_update_tfidf_cache(gen2, &blob, false).unwrap();
        assert!(
            !Arc::ptr_eq(&arc1, &arc2),
            "new generation must cause cache miss"
        );
    }

    #[test]
    fn cache_search_results_consistent() {
        invalidate_tfidf_cache();
        let texts = vec!["knowledge database search", "embedding vector cosine"];
        let doc_ids = vec![1i64, 2];
        let blob = build_tfidf_model(&texts, &doc_ids);
        let gen = "2025-01-01T00:00:00_cache_search";

        let arc1 = get_or_update_tfidf_cache(gen, &blob, false).unwrap();
        let r1 = arc1.search("knowledge database", 5);
        let arc2 = get_or_update_tfidf_cache(gen, &blob, false).unwrap();
        let r2 = arc2.search("knowledge database", 5);

        assert_eq!(
            r1.len(),
            r2.len(),
            "cached search must produce same result count"
        );
        for (a, b) in r1.iter().zip(r2.iter()) {
            assert_eq!(a.0, b.0);
        }
    }

    #[test]
    fn invalidate_then_reload_works() {
        let texts = vec!["reload after invalidation test"];
        let doc_ids = vec![42i64];
        let blob = build_tfidf_model(&texts, &doc_ids);
        let gen = "2025-01-01T00:00:00_reload";

        get_or_update_tfidf_cache(gen, &blob, false).unwrap();
        invalidate_tfidf_cache();
        let arc = get_or_update_tfidf_cache(gen, &blob, false);
        assert!(arc.is_some(), "reload after invalidation must succeed");
    }

    #[test]
    fn cache_binary_path_works() {
        invalidate_tfidf_cache();
        let texts = vec!["binary cache path test"];
        let doc_ids = vec![1i64];
        let json_blob = build_tfidf_model(&texts, &doc_ids);
        let model = TfIdfModel::from_json_blob(&json_blob).unwrap();
        let bin = model.to_binary();
        let gen = "2025-01-01T00:00:00_bin_cache";

        let arc = get_or_update_tfidf_cache(gen, &bin, true);
        assert!(arc.is_some(), "binary blob must be cacheable");
    }

    #[test]
    fn build_tfidf_max_df_filters_ubiquitous_terms() {
        // "common" appears in all 3 docs (df=3); max_df_count = floor(0.95*3)=2
        // → df=3 > 2 → filtered out
        let texts = vec![
            "common rare word",
            "common another word",
            "common third word",
        ];
        let doc_ids = vec![1i64, 2, 3];
        let blob = build_tfidf_model(&texts, &doc_ids);
        let model: serde_json::Value = serde_json::from_slice(&blob).unwrap();
        let vocab = model["vocabulary"].as_object().unwrap();
        assert!(
            !vocab.contains_key("common"),
            "ubiquitous term should be filtered by max_df"
        );
        assert!(vocab.contains_key("rare"), "rare term should be kept");
    }

    #[test]
    fn build_tfidf_idf_dimension_matches_vocabulary() {
        let texts = vec!["alpha beta gamma", "alpha delta epsilon"];
        let doc_ids = vec![10i64, 20];
        let blob = build_tfidf_model(&texts, &doc_ids);
        let model: serde_json::Value = serde_json::from_slice(&blob).unwrap();
        let vocab_len = model["vocabulary"].as_object().unwrap().len();
        let idf_len = model["idf"].as_array().unwrap().len();
        assert_eq!(
            vocab_len, idf_len,
            "vocabulary and idf must have same length"
        );
    }

    #[test]
    fn build_tfidf_doc_ids_preserved() {
        let texts = vec!["first doc", "second doc"];
        let doc_ids = vec![100i64, 200];
        let blob = build_tfidf_model(&texts, &doc_ids);
        let model: serde_json::Value = serde_json::from_slice(&blob).unwrap();
        assert_eq!(model["doc_ids"][0], 100);
        assert_eq!(model["doc_ids"][1], 200);
    }

    #[test]
    fn search_tfidf_native_scores_in_range() {
        let texts = vec!["knowledge database search queries"];
        let doc_ids = vec![1i64];
        let blob = build_tfidf_model(&texts, &doc_ids);
        let results = search_tfidf_native("knowledge search", &blob, 5);
        if let Some((_, score)) = results.first() {
            assert!(
                *score > 0.0 && *score <= 1.01,
                "cosine score must be in (0, 1]"
            );
        }
    }
}
