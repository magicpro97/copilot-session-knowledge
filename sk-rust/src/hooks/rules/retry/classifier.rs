// Public items here are the API for sibling tentacles; allow dead_code until
// issue-614-retry-queue and issue-614-retry-rule are implemented.
#![allow(dead_code)]
//! Retry classifier: determines whether an error payload is retryable and
//! extracts a `retry_after` hint (in seconds) when available.
//!
//! Canonical 429/rate-limit patterns supported:
//!   - OpenAI:     error.type == "rate_limit_exceeded" / "RateLimitError"
//!   - OpenAI:     error.code == "insufficient_quota"        → NOT retryable
//!   - AWS:        error.__type == "ThrottlingException" / "ProvisionedThroughputExceededException"
//!   - GitHub:     message contains "secondary rate limit"
//!   - Anthropic:  error.type == "overloaded_error" / HTTP 529
//!   - Generic:    HTTP 429 / 503 with optional Retry-After header
//!   - x-should-retry header: explicit opt-out
//!   - payment_required / billing errors → NOT retryable
use serde_json::Value;

/// Outcome of classifying an error payload.
#[derive(Debug, Clone, PartialEq, Eq)]
pub enum RetryClass {
    /// The caller may retry after `retry_after_secs` seconds.
    /// `None` means no server hint was found; the policy will compute a delay.
    Retryable { retry_after_secs: Option<u64> },
    /// The error is permanent (auth, quota, explicit opt-out, unknown).
    NotRetryable { reason: &'static str },
}

impl RetryClass {
    /// Convenience: is this a retryable outcome?
    #[inline]
    pub fn is_retryable(&self) -> bool {
        matches!(self, RetryClass::Retryable { .. })
    }
}

/// Classify the error payload from an `errorOccurred` hook event.
///
/// The payload may contain any of the following fields (all optional):
///   - `error`:         string message OR object with `type`, `code`, `message`
///   - `status`:        HTTP status code (integer or string)
///   - `headers`:       object mapping header names → values (case-insensitive keys)
///   - `__type`:        AWS-style error type at top-level
///
/// Classification priority (first match wins):
///   1. `x-should-retry: false` header → NotRetryable
///   2. Auth/billing markers             → NotRetryable
///   3. Provider-specific patterns       → Retryable / NotRetryable
///   4. HTTP status 429 / 503           → Retryable
///   5. HTTP status 4xx (other)         → NotRetryable
///   6. Unknown                         → NotRetryable
pub fn classify(payload: &Value) -> RetryClass {
    // ── 1. x-should-retry header opt-out ──────────────────────────────────
    if header_is_false(payload, "x-should-retry") {
        return RetryClass::NotRetryable {
            reason: "x-should-retry: false",
        };
    }

    // ── 2. Provider-specific classification (runs before generic auth/billing
    //       so that provider-specific reasons take precedence and providers
    //       such as GitHub can override generic 403 for secondary rate limits)
    if let Some(cls) = classify_provider(payload) {
        return cls;
    }

    // ── 3. Auth / billing permanent errors (generic fallback) ─────────────
    if is_auth_or_billing(payload) {
        return RetryClass::NotRetryable {
            reason: "auth/billing error",
        };
    }

    // ── 4. Generic HTTP status ────────────────────────────────────────────
    let status = extract_status(payload);
    match status {
        Some(429) | Some(503) => {
            let retry_after = extract_retry_after(payload);
            RetryClass::Retryable {
                retry_after_secs: retry_after,
            }
        }
        Some(s) if (400u16..500).contains(&s) => RetryClass::NotRetryable {
            reason: "4xx non-retryable status",
        },
        Some(529) => {
            // Anthropic overloaded (non-standard HTTP code)
            let retry_after = extract_retry_after(payload);
            RetryClass::Retryable {
                retry_after_secs: retry_after,
            }
        }
        _ => RetryClass::NotRetryable {
            reason: "unknown/non-retryable error",
        },
    }
}

// ---------------------------------------------------------------------------
// Provider-specific patterns
// ---------------------------------------------------------------------------

fn classify_provider(payload: &Value) -> Option<RetryClass> {
    // OpenAI
    if let Some(cls) = classify_openai(payload) {
        return Some(cls);
    }
    // AWS
    if let Some(cls) = classify_aws(payload) {
        return Some(cls);
    }
    // GitHub
    if let Some(cls) = classify_github(payload) {
        return Some(cls);
    }
    // Anthropic
    if let Some(cls) = classify_anthropic(payload) {
        return Some(cls);
    }
    None
}

fn classify_openai(payload: &Value) -> Option<RetryClass> {
    let err = payload.get("error")?;

    let err_type = str_field(err, "type").unwrap_or_default();
    let err_code = str_field(err, "code").unwrap_or_default();
    let err_msg = err_message(err).unwrap_or_default();

    // insufficient_quota → permanent billing failure
    if err_code == "insufficient_quota"
        || err_type == "insufficient_quota"
        || err_msg.contains("insufficient_quota")
    {
        return Some(RetryClass::NotRetryable {
            reason: "OpenAI insufficient_quota",
        });
    }

    // payment_required → permanent billing failure
    if err_code == "payment_required"
        || err_type == "payment_required"
        || err_msg.to_lowercase().contains("payment required")
    {
        return Some(RetryClass::NotRetryable {
            reason: "OpenAI payment_required",
        });
    }

    // RateLimitError / rate_limit_exceeded → retryable
    if err_type == "rate_limit_exceeded"
        || err_type == "RateLimitError"
        || err_code == "rate_limit_exceeded"
        || err_msg.to_lowercase().contains("rate limit")
    {
        let retry_after = extract_retry_after(payload);
        return Some(RetryClass::Retryable {
            retry_after_secs: retry_after,
        });
    }

    None
}

fn classify_aws(payload: &Value) -> Option<RetryClass> {
    // AWS puts the type at top-level as `__type` or inside `error.__type`
    let type_str = payload
        .get("__type")
        .and_then(|v| v.as_str())
        .or_else(|| {
            payload
                .get("error")
                .and_then(|e| e.get("__type"))
                .and_then(|v| v.as_str())
        })
        .or_else(|| payload.get("error").and_then(|e| str_field(e, "type")))
        .unwrap_or_default();

    match type_str {
        "ThrottlingException"
        | "ProvisionedThroughputExceededException"
        | "RequestThrottled"
        | "TooManyRequestsException"
        | "SlowDown" => {
            let retry_after = extract_retry_after(payload);
            Some(RetryClass::Retryable {
                retry_after_secs: retry_after,
            })
        }
        _ => {
            // Also check error message for common AWS throttle phrasing
            let msg = payload
                .get("error")
                .and_then(|e| err_message(e))
                .unwrap_or_default();
            if msg.contains("ThrottlingException") || msg.contains("Rate exceeded") {
                let retry_after = extract_retry_after(payload);
                Some(RetryClass::Retryable {
                    retry_after_secs: retry_after,
                })
            } else {
                None
            }
        }
    }
}

fn classify_github(payload: &Value) -> Option<RetryClass> {
    let msg = payload
        .get("error")
        .and_then(|e| err_message(e))
        .unwrap_or_default();

    let msg_lower = msg.to_lowercase();

    if msg_lower.contains("secondary rate limit")
        || msg_lower.contains("you have exceeded a secondary rate limit")
        || msg_lower.contains("abuse detection")
    {
        let retry_after = extract_retry_after(payload);
        return Some(RetryClass::Retryable {
            retry_after_secs: retry_after,
        });
    }

    // GitHub also returns 429 with `Retry-After` for primary rate limits
    if msg_lower.contains("api rate limit exceeded") || msg_lower.contains("rate limit exceeded") {
        let retry_after = extract_retry_after(payload);
        return Some(RetryClass::Retryable {
            retry_after_secs: retry_after,
        });
    }

    None
}

fn classify_anthropic(payload: &Value) -> Option<RetryClass> {
    // Anthropic uses error.type == "overloaded_error" for 529
    let err = payload.get("error")?;
    let err_type = str_field(err, "type").unwrap_or_default();

    if err_type == "overloaded_error" {
        let retry_after = extract_retry_after(payload);
        return Some(RetryClass::Retryable {
            retry_after_secs: retry_after,
        });
    }

    // Also check via message
    let msg = err_message(err).unwrap_or_default();
    if msg.to_lowercase().contains("overloaded") {
        let retry_after = extract_retry_after(payload);
        return Some(RetryClass::Retryable {
            retry_after_secs: retry_after,
        });
    }

    None
}

// ---------------------------------------------------------------------------
// Auth / billing guard
// ---------------------------------------------------------------------------

fn is_auth_or_billing(payload: &Value) -> bool {
    // HTTP 401 / 403
    if matches!(extract_status(payload), Some(401) | Some(403)) {
        return true;
    }

    let err = match payload.get("error") {
        Some(e) => e,
        None => return false,
    };

    let code = str_field(err, "code").unwrap_or_default();
    let err_type = str_field(err, "type").unwrap_or_default();
    let msg = err_message(err).unwrap_or_default().to_lowercase();

    // Explicit billing/auth codes
    if matches!(
        code,
        "insufficient_quota" | "payment_required" | "invalid_api_key" | "unauthorized"
    ) {
        return true;
    }

    if matches!(
        err_type,
        "insufficient_quota" | "payment_required" | "authentication_error" | "permission_denied"
    ) {
        return true;
    }

    // Message heuristics
    if msg.contains("invalid api key")
        || msg.contains("unauthorized")
        || msg.contains("forbidden")
        || msg.contains("insufficient_quota")
        || msg.contains("payment required")
    {
        return true;
    }

    false
}

// ---------------------------------------------------------------------------
// Field extraction helpers
// ---------------------------------------------------------------------------

/// Extract the HTTP status code from `payload.status` (integer or numeric string).
pub(crate) fn extract_status(payload: &Value) -> Option<u16> {
    let s = payload.get("status")?;
    if let Some(n) = s.as_u64() {
        return u16::try_from(n).ok();
    }
    if let Some(text) = s.as_str() {
        return text.trim().parse::<u16>().ok();
    }
    None
}

/// Extract `Retry-After` seconds from response headers or a top-level field.
///
/// Handles:
///   - `headers["retry-after"]` (case-insensitive) — integer seconds or
///     HTTP-date (not parsed; falls back to None for date strings).
///   - `headers["x-ratelimit-reset-requests"]` / `x-ratelimit-reset-tokens`
///     (OpenAI delta-seconds strings like "1s", "200ms").
///   - Top-level `retry_after` field.
pub(crate) fn extract_retry_after(payload: &Value) -> Option<u64> {
    // Top-level numeric hint
    if let Some(v) = payload.get("retry_after").and_then(|v| v.as_u64()) {
        return Some(v.min(MAX_RETRY_AFTER_SECS));
    }

    // Headers object
    let headers = payload.get("headers")?;

    // Standard Retry-After
    if let Some(ra) = header_value(headers, "retry-after") {
        if let Ok(secs) = ra.trim().parse::<u64>() {
            return Some(secs.min(MAX_RETRY_AFTER_SECS));
        }
        // HTTP-date format — not parsed; caller will use policy default
    }

    // OpenAI x-ratelimit-reset-requests / x-ratelimit-reset-tokens (pick smaller)
    let reset_req =
        header_value(headers, "x-ratelimit-reset-requests").and_then(|v| parse_delta_seconds(&v));
    let reset_tok =
        header_value(headers, "x-ratelimit-reset-tokens").and_then(|v| parse_delta_seconds(&v));

    match (reset_req, reset_tok) {
        (Some(a), Some(b)) => Some(a.min(b).min(MAX_RETRY_AFTER_SECS)),
        (Some(a), None) | (None, Some(a)) => Some(a.min(MAX_RETRY_AFTER_SECS)),
        (None, None) => None,
    }
}

/// Maximum Retry-After value we will honour (cap to prevent accidental long sleeps).
pub const MAX_RETRY_AFTER_SECS: u64 = 60;

/// Parse OpenAI-style delta strings: "1s", "500ms", "2m", "100" (bare seconds).
fn parse_delta_seconds(s: &str) -> Option<u64> {
    let s = s.trim();
    if let Some(ms_str) = s.strip_suffix("ms") {
        let ms: u64 = ms_str.trim().parse().ok()?;
        // Round up to seconds (minimum 1 s for non-zero ms values)
        return Some(if ms == 0 {
            0
        } else {
            ms.saturating_add(999) / 1000
        });
    }
    if let Some(sec_str) = s.strip_suffix('s') {
        return sec_str.trim().parse::<u64>().ok();
    }
    if let Some(min_str) = s.strip_suffix('m') {
        let mins: u64 = min_str.trim().parse().ok()?;
        return Some(mins.saturating_mul(60));
    }
    // Bare numeric — treat as seconds
    s.parse::<u64>().ok()
}

/// Look up a header value by case-insensitive key from `headers` object.
fn header_value(headers: &Value, key: &str) -> Option<String> {
    let obj = headers.as_object()?;
    for (k, v) in obj {
        if k.to_lowercase() == key {
            return v.as_str().map(|s| s.to_owned());
        }
    }
    None
}

/// Check whether a header has the value "false" (case-insensitive).
fn header_is_false(payload: &Value, key: &str) -> bool {
    let headers = match payload.get("headers") {
        Some(h) => h,
        None => return false,
    };
    header_value(headers, key)
        .map(|v| v.trim().to_lowercase() == "false")
        .unwrap_or(false)
}

/// Extract a string field from an error object.
fn str_field<'a>(obj: &'a Value, key: &str) -> Option<&'a str> {
    obj.get(key)?.as_str()
}

/// Extract the human-readable message from an error value.
///
/// Accepts:
///   - bare string: use as-is
///   - object with `message` field
fn err_message(err: &Value) -> Option<&str> {
    if let Some(s) = err.as_str() {
        return Some(s);
    }
    err.get("message")?.as_str()
}

// ---------------------------------------------------------------------------
// Tests
// ---------------------------------------------------------------------------

#[cfg(test)]
mod tests {
    use super::*;
    use serde_json::json;

    // ── OpenAI ───────────────────────────────────────────────────────────────

    #[test]
    fn test_openai_rate_limit_error() {
        let payload = json!({
            "error": {
                "type": "rate_limit_exceeded",
                "code": "rate_limit_exceeded",
                "message": "You have exceeded your rate limit."
            },
            "status": 429
        });
        assert!(classify(&payload).is_retryable());
    }

    #[test]
    fn test_openai_rate_limit_error_type_name() {
        let payload = json!({
            "error": {
                "type": "RateLimitError",
                "message": "Rate limit reached for default-gpt-4"
            }
        });
        assert!(classify(&payload).is_retryable());
    }

    #[test]
    fn test_openai_insufficient_quota() {
        let payload = json!({
            "error": {
                "type": "insufficient_quota",
                "code": "insufficient_quota",
                "message": "You exceeded your current quota"
            },
            "status": 429
        });
        assert!(!classify(&payload).is_retryable());
        assert_eq!(
            classify(&payload),
            RetryClass::NotRetryable {
                reason: "OpenAI insufficient_quota"
            }
        );
    }

    #[test]
    fn test_openai_payment_required() {
        let payload = json!({
            "error": {
                "type": "payment_required",
                "message": "Payment required to continue."
            },
            "status": 402
        });
        assert!(!classify(&payload).is_retryable());
        assert_eq!(
            classify(&payload),
            RetryClass::NotRetryable {
                reason: "OpenAI payment_required"
            }
        );
    }

    // ── AWS ──────────────────────────────────────────────────────────────────

    #[test]
    fn test_aws_throttling_exception_top_level() {
        let payload = json!({
            "__type": "ThrottlingException",
            "message": "Rate exceeded"
        });
        assert!(classify(&payload).is_retryable());
    }

    #[test]
    fn test_aws_provisioned_throughput_exceeded() {
        let payload = json!({
            "__type": "ProvisionedThroughputExceededException",
            "message": "Provisioned throughput exceeded"
        });
        assert!(classify(&payload).is_retryable());
    }

    #[test]
    fn test_aws_throttling_in_error_object() {
        let payload = json!({
            "error": {
                "__type": "ThrottlingException",
                "message": "Rate exceeded"
            }
        });
        assert!(classify(&payload).is_retryable());
    }

    // ── GitHub ───────────────────────────────────────────────────────────────

    #[test]
    fn test_github_secondary_rate_limit() {
        let payload = json!({
            "error": {
                "message": "You have exceeded a secondary rate limit and have been temporarily blocked."
            },
            "status": 403
        });
        // GitHub secondary rate limit takes priority over generic 403
        assert!(classify(&payload).is_retryable());
    }

    #[test]
    fn test_github_abuse_detection() {
        let payload = json!({
            "error": {
                "message": "Request forbidden by abuse detection mechanisms."
            }
        });
        assert!(classify(&payload).is_retryable());
    }

    // ── Anthropic ────────────────────────────────────────────────────────────

    #[test]
    fn test_anthropic_overloaded_error() {
        let payload = json!({
            "error": {
                "type": "overloaded_error",
                "message": "Overloaded"
            },
            "status": 529
        });
        assert!(classify(&payload).is_retryable());
    }

    #[test]
    fn test_anthropic_overloaded_message() {
        let payload = json!({
            "error": {
                "message": "API is temporarily overloaded, please try again."
            }
        });
        assert!(classify(&payload).is_retryable());
    }

    #[test]
    fn test_http_529_direct() {
        // 529 without an error object
        let payload = json!({ "status": 529 });
        assert!(classify(&payload).is_retryable());
    }

    // ── x-should-retry opt-out ───────────────────────────────────────────────

    #[test]
    fn test_x_should_retry_false_overrides_429() {
        let payload = json!({
            "status": 429,
            "headers": { "x-should-retry": "false" }
        });
        assert!(!classify(&payload).is_retryable());
        assert_eq!(
            classify(&payload),
            RetryClass::NotRetryable {
                reason: "x-should-retry: false"
            }
        );
    }

    #[test]
    fn test_x_should_retry_true_allows_retry() {
        let payload = json!({
            "status": 429,
            "headers": { "x-should-retry": "true" }
        });
        assert!(classify(&payload).is_retryable());
    }

    // ── Retry-After extraction ────────────────────────────────────────────────

    #[test]
    fn test_retry_after_integer_header() {
        let payload = json!({
            "status": 429,
            "headers": { "retry-after": "30" }
        });
        assert_eq!(
            classify(&payload),
            RetryClass::Retryable {
                retry_after_secs: Some(30)
            }
        );
    }

    #[test]
    fn test_retry_after_zero() {
        let payload = json!({
            "status": 429,
            "headers": { "retry-after": "0" }
        });
        assert_eq!(
            classify(&payload),
            RetryClass::Retryable {
                retry_after_secs: Some(0)
            }
        );
    }

    #[test]
    fn test_retry_after_max_cap() {
        let payload = json!({
            "status": 429,
            "headers": { "retry-after": "9999" }
        });
        assert_eq!(
            classify(&payload),
            RetryClass::Retryable {
                retry_after_secs: Some(MAX_RETRY_AFTER_SECS)
            }
        );
    }

    #[test]
    fn test_retry_after_openai_delta_seconds() {
        let payload = json!({
            "status": 429,
            "headers": { "x-ratelimit-reset-requests": "1s" }
        });
        assert_eq!(
            classify(&payload),
            RetryClass::Retryable {
                retry_after_secs: Some(1)
            }
        );
    }

    #[test]
    fn test_retry_after_openai_delta_ms() {
        let payload = json!({
            "status": 429,
            "headers": { "x-ratelimit-reset-requests": "500ms" }
        });
        assert_eq!(
            classify(&payload),
            RetryClass::Retryable {
                retry_after_secs: Some(1)
            } // rounds up
        );
    }

    #[test]
    fn test_retry_after_header_case_insensitive() {
        let payload = json!({
            "status": 429,
            "headers": { "Retry-After": "15" }
        });
        assert_eq!(
            classify(&payload),
            RetryClass::Retryable {
                retry_after_secs: Some(15)
            }
        );
    }

    // ── Generic HTTP status ──────────────────────────────────────────────────

    #[test]
    fn test_http_503_retryable() {
        let payload = json!({ "status": 503 });
        assert!(classify(&payload).is_retryable());
    }

    #[test]
    fn test_http_401_not_retryable() {
        let payload = json!({ "status": 401 });
        assert!(!classify(&payload).is_retryable());
    }

    #[test]
    fn test_http_403_not_retryable() {
        // Plain 403 without secondary rate limit message
        let payload = json!({ "status": 403 });
        assert!(!classify(&payload).is_retryable());
    }

    #[test]
    fn test_unknown_error_not_retryable() {
        let payload = json!({ "error": "some unknown internal error" });
        assert!(!classify(&payload).is_retryable());
    }

    #[test]
    fn test_empty_payload_not_retryable() {
        let payload = json!({});
        assert!(!classify(&payload).is_retryable());
    }

    // ── parse_delta_seconds ──────────────────────────────────────────────────

    #[test]
    fn test_parse_delta_seconds_bare() {
        assert_eq!(parse_delta_seconds("10"), Some(10));
    }

    #[test]
    fn test_parse_delta_seconds_s_suffix() {
        assert_eq!(parse_delta_seconds("5s"), Some(5));
    }

    #[test]
    fn test_parse_delta_seconds_ms_suffix_rounds_up() {
        assert_eq!(parse_delta_seconds("1ms"), Some(1));
        assert_eq!(parse_delta_seconds("999ms"), Some(1));
        assert_eq!(parse_delta_seconds("1000ms"), Some(1));
        assert_eq!(parse_delta_seconds("1001ms"), Some(2));
    }

    #[test]
    fn test_parse_delta_seconds_zero_ms() {
        assert_eq!(parse_delta_seconds("0ms"), Some(0));
    }

    #[test]
    fn test_parse_delta_seconds_minutes() {
        assert_eq!(parse_delta_seconds("2m"), Some(120));
    }
}
