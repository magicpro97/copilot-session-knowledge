//! Native HTTP embedding API client (requires `native-embed` feature).
//!
//! Calls any OpenAI-compatible `/embeddings` endpoint using
//! `reqwest::blocking`.  Mirrors Python's `call_embedding_api()` with the
//! same error classification and retry logic:
//!
//! | HTTP status  | Category    | Action                           |
//! |--------------|-------------|----------------------------------|
//! | 401/403/404  | Auth        | Return immediately, no retry     |
//! | 429          | RateLimit   | Retry with exponential backoff   |
//! | 5xx / other  | Server      | Retry with exponential backoff   |
//! | Network err  | Network     | Retry with exponential backoff   |
//!
//! Available only when the `native-embed` Cargo feature is enabled.

use std::time::Duration;

use crate::embeddings::config::{get_api_key, ProviderConfig};

// ── Error types ──────────────────────────────────────────────────────────

/// Error categories returned by the embedding API.
///
/// Mirrors Python's `EmbeddingAuthError`, `EmbeddingRateLimitError`, and
/// `EmbeddingNetworkError`.
#[derive(Debug)]
pub enum EmbedApiError {
    /// 401 / 403 / 404 — bad key or unknown model; do not retry.
    Auth(String),
    /// 429 — rate limited; caller may retry with backoff.
    RateLimit(String),
    /// Network/timeout; caller may retry.
    Network(String),
    /// All other failures (bad response, JSON parse error, etc.).
    Other(String),
}

impl std::fmt::Display for EmbedApiError {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        match self {
            EmbedApiError::Auth(m)
            | EmbedApiError::RateLimit(m)
            | EmbedApiError::Network(m)
            | EmbedApiError::Other(m) => write!(f, "{m}"),
        }
    }
}

impl std::error::Error for EmbedApiError {}

// ── Core API call ────────────────────────────────────────────────────────

/// Call an OpenAI-compatible `/embeddings` endpoint.
///
/// Posts `{"input": texts, "model": prov.model, "dimensions": prov.dimensions}`
/// (dimensions omitted when 0) and returns the embedding vectors in input order.
///
/// # Retry policy
/// Retries `max_retries` times with `min(2^attempt + 1, 30)` second backoff.
/// Auth errors are returned immediately without retrying.
pub fn call_embedding_api(
    texts: &[String],
    prov: &ProviderConfig,
    max_retries: u32,
) -> Result<Vec<Vec<f32>>, EmbedApiError> {
    let api_key = get_api_key(prov);
    let url = format!("{}/embeddings", prov.base_url.trim_end_matches('/'));

    let client = reqwest::blocking::Client::builder()
        .timeout(Duration::from_secs(120))
        .build()
        .map_err(|e| EmbedApiError::Network(e.to_string()))?;

    let dims_opt: Option<u32> = if prov.dimensions > 0 {
        Some(prov.dimensions)
    } else {
        None
    };

    let mut last_err = String::new();

    for attempt in 0..max_retries {
        let mut body = serde_json::json!({
            "input": texts,
            "model": prov.model,
        });
        if let Some(dims) = dims_opt {
            body["dimensions"] = serde_json::json!(dims);
        }

        let result = client
            .post(&url)
            .header("Content-Type", "application/json")
            .header("Authorization", format!("Bearer {api_key}"))
            .header("User-Agent", "copilot-session-tools/1.0")
            .json(&body)
            .send();

        match result {
            Err(e) => {
                let msg = e.to_string();
                last_err = format!("Network error: {msg}");
                let wait = ((1u64 << attempt) + 1).min(30);
                eprintln!(
                    "    🌐 {last_err} — retry {}/{max_retries} in {wait}s",
                    attempt + 1
                );
                std::thread::sleep(Duration::from_secs(wait));
            }

            Ok(resp) => {
                let status = resp.status();

                if status.is_success() {
                    let parsed: serde_json::Value = resp
                        .json()
                        .map_err(|e| EmbedApiError::Other(e.to_string()))?;

                    let data = parsed["data"].as_array().ok_or_else(|| {
                        EmbedApiError::Other("missing 'data' field in API response".into())
                    })?;

                    // Parse and sort by index to match input order
                    let mut indexed: Vec<(usize, Vec<f32>)> = data
                        .iter()
                        .filter_map(|item| {
                            let idx = item["index"].as_u64()? as usize;
                            let emb: Vec<f32> = item["embedding"]
                                .as_array()?
                                .iter()
                                .map(|v| v.as_f64().unwrap_or(0.0) as f32)
                                .collect();
                            Some((idx, emb))
                        })
                        .collect();

                    indexed.sort_by_key(|(i, _)| *i);
                    let vecs: Vec<Vec<f32>> = indexed.into_iter().map(|(_, v)| v).collect();

                    if vecs.len() != texts.len() {
                        return Err(EmbedApiError::Other(format!(
                            "expected {} embeddings, got {}",
                            texts.len(),
                            vecs.len()
                        )));
                    }
                    return Ok(vecs);
                } else if status == 401 || status == 403 {
                    let body_txt = resp.text().unwrap_or_default();
                    return Err(EmbedApiError::Auth(format!(
                        "🔑 Auth failed ({}): API key invalid or expired. {}",
                        status,
                        &body_txt[..body_txt.len().min(100)]
                    )));
                } else if status == 404 {
                    let body_txt = resp.text().unwrap_or_default();
                    return Err(EmbedApiError::Auth(format!(
                        "🔍 Model not found (404): Check model name in config. {}",
                        &body_txt[..body_txt.len().min(100)]
                    )));
                } else if status == 429 {
                    let wait = ((1u64 << attempt) + 1).min(30);
                    last_err = "Rate limited (429)".to_string();
                    eprintln!(
                        "    ⏳ {last_err} — retry {}/{max_retries} in {wait}s",
                        attempt + 1
                    );
                    std::thread::sleep(Duration::from_secs(wait));
                } else {
                    let code = status.as_u16();
                    let body_txt = resp.text().unwrap_or_default();
                    last_err =
                        format!("API error {code}: {}", &body_txt[..body_txt.len().min(200)]);
                    let wait = ((1u64 << attempt) + 1).min(30);
                    eprintln!(
                        "    ❌ {last_err} — retry {}/{max_retries} in {wait}s",
                        attempt + 1
                    );
                    std::thread::sleep(Duration::from_secs(wait));
                }
            }
        }
    }

    // Classify the last error
    if last_err.contains("429") || last_err.contains("Rate limit") {
        Err(EmbedApiError::RateLimit(last_err))
    } else if last_err.contains("Network") || last_err.contains("Connection") {
        Err(EmbedApiError::Network(last_err))
    } else {
        Err(EmbedApiError::Other(format!(
            "Max retries exceeded: {last_err}"
        )))
    }
}

// ── Batch embed ──────────────────────────────────────────────────────────

/// Embed texts in batches, respecting the configured batch size.
///
/// Splits `texts` into chunks of `batch_size`, calls `call_embedding_api`
/// for each chunk, and returns all vectors in input order.
///
/// Matches Python's `embed_batch()`:
/// - Prints `batch N/M (K items)... ✓` progress lines
/// - Pauses 300 ms between batches (rate-limit courtesy)
pub fn batch_embed(
    texts: &[String],
    prov: &ProviderConfig,
    batch_size: usize,
) -> Result<Vec<Vec<f32>>, EmbedApiError> {
    let total = texts.len();
    let effective_batch = batch_size.max(1);
    let num_batches = (total + effective_batch - 1) / effective_batch;
    let mut all_vecs: Vec<Vec<f32>> = Vec::with_capacity(total);

    for (batch_num, chunk) in texts.chunks(effective_batch).enumerate() {
        print!(
            "    batch {}/{num_batches} ({} items)...",
            batch_num + 1,
            chunk.len()
        );
        let _ = std::io::Write::flush(&mut std::io::stdout());

        let chunk_owned: Vec<String> = chunk.to_vec();
        let vecs = call_embedding_api(&chunk_owned, prov, 3)?;
        all_vecs.extend(vecs);
        println!(" ✓");

        // Courtesy pause between batches (mirrors Python's time.sleep(0.3))
        if batch_num + 1 < num_batches {
            std::thread::sleep(Duration::from_millis(300));
        }
    }

    Ok(all_vecs)
}

// ── Tests ─────────────────────────────────────────────────────────────────

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn embed_api_error_auth_display() {
        let e = EmbedApiError::Auth("invalid key".into());
        assert!(e.to_string().contains("invalid key"));
    }

    #[test]
    fn embed_api_error_network_display() {
        let e = EmbedApiError::Network("connection refused".into());
        assert!(e.to_string().contains("connection refused"));
    }

    #[test]
    fn embed_api_error_rate_limit_display() {
        let e = EmbedApiError::RateLimit("429 too many requests".into());
        assert!(e.to_string().contains("429"));
    }

    #[test]
    fn embed_api_error_other_display() {
        let e = EmbedApiError::Other("unexpected parse failure".into());
        assert!(e.to_string().contains("unexpected parse failure"));
    }
}
