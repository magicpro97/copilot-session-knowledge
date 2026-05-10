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
/// Mirrors Python's `search_tfidf()` but implemented entirely in Rust — no
/// `numpy`, `scipy`, or `sklearn` required.
///
/// # Arguments
/// * `query`      — raw query string (tokenised internally)
/// * `model_blob` — JSON bytes from `tfidf_model.model_blob`
/// * `limit`      — maximum number of results to return
///
/// Returns results with score > 0.01, sorted by score descending.
///
/// # Safety
/// Legacy pickle blobs (prefix `\x80\x04` / `\x80\x05`) are rejected and
/// an empty vec is returned, matching Python's guard in `search_tfidf()`.
pub fn search_tfidf_native(query: &str, model_blob: &[u8], limit: usize) -> Vec<(i64, f32)> {
    // Reject old pickle format — unsafe deserialization
    if model_blob.starts_with(b"\x80\x04") || model_blob.starts_with(b"\x80\x05") {
        return vec![];
    }

    let model: serde_json::Value = match serde_json::from_slice(model_blob) {
        Ok(v) => v,
        Err(_) => return vec![],
    };

    // ── Parse model fields ───────────────────────────────────────────────
    let vocabulary: HashMap<String, usize> =
        match model.get("vocabulary").and_then(|v| v.as_object()) {
            Some(obj) => obj
                .iter()
                .filter_map(|(k, v)| v.as_u64().map(|idx| (k.clone(), idx as usize)))
                .collect(),
            None => return vec![],
        };

    let idf: Vec<f64> = match model.get("idf").and_then(|v| v.as_array()) {
        Some(arr) => arr.iter().map(|v| v.as_f64().unwrap_or(0.0)).collect(),
        None => return vec![],
    };

    let matrix_row: Vec<usize> = match model.get("matrix_row").and_then(|v| v.as_array()) {
        Some(arr) => arr
            .iter()
            .map(|v| v.as_i64().unwrap_or(0) as usize)
            .collect(),
        None => return vec![],
    };
    let matrix_col: Vec<usize> = match model.get("matrix_col").and_then(|v| v.as_array()) {
        Some(arr) => arr
            .iter()
            .map(|v| v.as_i64().unwrap_or(0) as usize)
            .collect(),
        None => return vec![],
    };
    let matrix_data: Vec<f64> = match model.get("matrix_data").and_then(|v| v.as_array()) {
        Some(arr) => arr.iter().map(|v| v.as_f64().unwrap_or(0.0)).collect(),
        None => return vec![],
    };
    let doc_ids: Vec<i64> = match model.get("doc_ids").and_then(|v| v.as_array()) {
        Some(arr) => arr.iter().map(|v| v.as_i64().unwrap_or(0)).collect(),
        None => return vec![],
    };
    let matrix_shape: Vec<usize> = match model.get("matrix_shape").and_then(|v| v.as_array()) {
        Some(arr) => arr
            .iter()
            .map(|v| v.as_u64().unwrap_or(0) as usize)
            .collect(),
        None => return vec![],
    };

    if matrix_shape.len() < 2 || doc_ids.is_empty() || vocabulary.is_empty() {
        return vec![];
    }
    let n_docs = matrix_shape[0];
    let n_terms = matrix_shape[1];

    // ── Transform query to TF-IDF vector ────────────────────────────────
    let query_tokens = tokenize(query);
    let mut query_tf: HashMap<String, usize> = HashMap::new();
    for token in &query_tokens {
        *query_tf.entry(token.clone()).or_insert(0) += 1;
    }

    let mut query_vec: Vec<f64> = vec![0.0; n_terms];
    for (term, &tf) in &query_tf {
        if let Some(&col) = vocabulary.get(term) {
            if col < n_terms && tf > 0 {
                let tf_val = 1.0 + (tf as f64).ln();
                query_vec[col] = tf_val * idf.get(col).copied().unwrap_or(0.0);
            }
        }
    }

    // L2 normalise query
    let qnorm: f64 = query_vec.iter().map(|v| v * v).sum::<f64>().sqrt();
    if qnorm == 0.0 {
        return vec![];
    }
    for v in &mut query_vec {
        *v /= qnorm;
    }

    // ── Accumulate dot products (matrix rows are already L2-normalised) ─
    let mut scores: Vec<f64> = vec![0.0; n_docs];
    let len = matrix_row
        .len()
        .min(matrix_col.len())
        .min(matrix_data.len());
    for i in 0..len {
        let r = matrix_row[i];
        let c = matrix_col[i];
        if r < n_docs && c < n_terms {
            scores[r] += query_vec[c] * matrix_data[i];
        }
    }

    let mut results: Vec<(i64, f32)> = scores
        .iter()
        .enumerate()
        .filter(|(_, &s)| s > 0.01)
        .map(|(i, &s)| (doc_ids.get(i).copied().unwrap_or(0), s as f32))
        .collect();

    results.sort_by(|a, b| b.1.partial_cmp(&a.1).unwrap_or(std::cmp::Ordering::Equal));
    results.truncate(limit);
    results
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
