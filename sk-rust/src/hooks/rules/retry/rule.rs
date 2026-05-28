//! HookRule glue for issue #614 rate-limit retry telemetry.

use serde_json::Value;

use crate::hooks::audit::audit_log;
use crate::hooks::rules::{info, HookRule};

use super::classifier::{classify, extract_retry_after, extract_status, RetryClass};
use super::jitter::jitter_factor_bp;
use super::policy::RetryPolicy;
use super::queue::{
    append_record, build_record_from_input, load_or_create_state_key, RetryRecordInput,
};

/// Detect rate-limit failures, append a redacted queue record, and surface a
/// concise recovery hint without blocking the original hook flow.
pub struct RateLimitRetryRule;

impl HookRule for RateLimitRetryRule {
    fn name(&self) -> &'static str {
        "429-retry"
    }

    fn events(&self) -> &'static [&'static str] {
        &["errorOccurred"]
    }

    fn tools(&self) -> &'static [&'static str] {
        &[]
    }

    fn evaluate(&self, _event: &str, data: &Value) -> Option<Value> {
        let policy = RetryPolicy::default_policy();
        let class = classify(data);
        let attempt = extract_u32(data, &["attempt", "retry_attempt"]).unwrap_or(0);
        let elapsed = extract_f64(data, &["elapsed_total_seconds", "elapsed_secs"]).unwrap_or(0.0);
        let exit_code = extract_i32(data, &["exit_code", "exitCode", "exit_code_prev"]);
        let status = extract_status(data);
        let retry_after = extract_retry_after(data);

        let (outcome, stop_reason, delay_ms, delay_source, detected_pattern) = match &class {
            RetryClass::Retryable { retry_after_secs } => {
                if !policy.should_retry(attempt) {
                    (
                        "stopped",
                        Some("max_attempts"),
                        0,
                        "none",
                        detected_pattern(data, &class),
                    )
                } else if !policy.within_budget(elapsed.max(0.0) as u64) {
                    (
                        "stopped",
                        Some("budget_exhausted"),
                        0,
                        "none",
                        detected_pattern(data, &class),
                    )
                } else {
                    let server_hint = *retry_after_secs;
                    let delay = policy.delay_ms(attempt, jitter_factor_bp(), server_hint);
                    (
                        "queued",
                        None,
                        delay,
                        if server_hint.is_some() {
                            "server-hint"
                        } else {
                            "exponential"
                        },
                        detected_pattern(data, &class),
                    )
                }
            }
            RetryClass::NotRetryable { reason } => (
                "stopped",
                Some(*reason),
                0,
                "none",
                detected_pattern(data, &class),
            ),
        };

        let key = match load_or_create_state_key() {
            Ok(key) => key,
            Err(err) => {
                audit_log(
                    "errorOccurred",
                    "",
                    self.name(),
                    "fail-open",
                    &format!("state-key error: {err}"),
                );
                return None;
            }
        };

        let record = build_record_from_input(
            RetryRecordInput {
                agent: extract_str(data, &["agent", "agent_name"]).unwrap_or("copilot"),
                attempt,
                max_attempts: policy.max_attempts,
                detected_pattern: &detected_pattern,
                status_code: status,
                retry_after_hint_seconds: retry_after,
                computed_delay_seconds: delay_ms as f64 / 1000.0,
                delay_source,
                elapsed_total_seconds: elapsed.max(0.0),
                stop_reason,
                exit_code_prev: exit_code,
                outcome,
                error_preview: &error_preview(data),
            },
            &key,
        );

        if let Err(err) = append_record(&record) {
            audit_log(
                "errorOccurred",
                "",
                self.name(),
                "fail-open",
                &format!("append error: {err}"),
            );
            return None;
        }

        audit_log(
            "errorOccurred",
            "",
            self.name(),
            outcome,
            &format!("attempt={attempt} delay_ms={delay_ms} pattern={detected_pattern}"),
        );

        if outcome == "queued" {
            Some(info(&format!(
                "Rate limit detected; queued retry attempt {attempt}/{} after {:.2}s. Run: sk retry list",
                policy.max_attempts,
                delay_ms as f64 / 1000.0
            )))
        } else {
            None
        }
    }
}

fn detected_pattern(payload: &Value, class: &RetryClass) -> String {
    match class {
        RetryClass::Retryable { .. } => {
            let text = payload.to_string().to_lowercase();
            if text.contains("throttling") || text.contains("provisionedthroughput") {
                "AWSRateLimit"
            } else if text.contains("secondary rate limit") || text.contains("github") {
                "GitHubSecondaryRateLimit"
            } else if text.contains("overloaded_error") || extract_status(payload) == Some(529) {
                "AnthropicOverloaded"
            } else if text.contains("rate_limit_exceeded") || text.contains("ratelimiterror") {
                "RateLimitError"
            } else {
                "HTTPRateLimit"
            }
        }
        RetryClass::NotRetryable { reason } => reason,
    }
    .to_string()
}

fn error_preview(data: &Value) -> String {
    data.get("error")
        .map(|v| {
            v.as_str()
                .map(str::to_string)
                .unwrap_or_else(|| v.to_string())
        })
        .unwrap_or_else(|| data.to_string())
}

fn extract_str<'a>(data: &'a Value, keys: &[&str]) -> Option<&'a str> {
    keys.iter().find_map(|key| data.get(*key)?.as_str())
}

fn extract_u32(data: &Value, keys: &[&str]) -> Option<u32> {
    keys.iter().find_map(|key| {
        let value = data.get(*key)?;
        value
            .as_u64()
            .and_then(|n| u32::try_from(n).ok())
            .or_else(|| value.as_str()?.parse::<u32>().ok())
    })
}

fn extract_i32(data: &Value, keys: &[&str]) -> Option<i32> {
    keys.iter().find_map(|key| {
        let value = data.get(*key)?;
        value
            .as_i64()
            .and_then(|n| i32::try_from(n).ok())
            .or_else(|| value.as_str()?.parse::<i32>().ok())
    })
}

fn extract_f64(data: &Value, keys: &[&str]) -> Option<f64> {
    keys.iter().find_map(|key| {
        let value = data.get(*key)?;
        value
            .as_f64()
            .or_else(|| value.as_str()?.parse::<f64>().ok())
    })
}

#[cfg(test)]
mod tests {
    use super::super::queue::{queue_path, read_records, state_key_path, TEST_ENV_LOCK};
    use super::*;
    use serde_json::json;
    use std::path::Path;
    use std::{env, fs};

    fn with_home<T>(dir: &Path, f: impl FnOnce() -> T) -> T {
        let lock = TEST_ENV_LOCK
            .get_or_init(|| std::sync::Arc::new(std::sync::Mutex::new(())))
            .clone();
        let _guard = lock.lock().unwrap_or_else(|e| e.into_inner());
        let old_home = env::var_os("HOME");
        unsafe {
            env::set_var("HOME", dir);
        }
        let result = f();
        unsafe {
            match old_home {
                Some(v) => env::set_var("HOME", v),
                None => env::remove_var("HOME"),
            }
        }
        result
    }

    #[test]
    fn queues_retryable_rate_limit() {
        let dir = env::temp_dir().join(format!("sk-retry-rule-{}", std::process::id()));
        let _ = fs::remove_dir_all(&dir);
        fs::create_dir_all(&dir).unwrap();

        with_home(&dir, || {
            let rule = RateLimitRetryRule;
            let data = json!({
                "agent": "copilot",
                "attempt": 0,
                "status": 429,
                "headers": {"retry-after": "2"},
                "error": {"type": "rate_limit_exceeded", "message": "rate limit token=ghp_abcdefghijklmnopqrstuvwxyz0123456789AB"},
                "exit_code": 1
            });
            let result = rule.evaluate("errorOccurred", &data);
            assert!(result.is_some());

            let key = fs::read_to_string(state_key_path()).unwrap();
            let (records, skipped) = read_records(key.trim()).unwrap();
            assert_eq!(skipped, 0);
            assert_eq!(records.len(), 1);
            let rec = &records[0];
            assert_eq!(rec.hook, "429-retry");
            assert_eq!(rec.attempt, 0);
            assert_eq!(rec.max_attempts, 5);
            assert_eq!(rec.status_code, Some(429));
            assert_eq!(rec.retry_after_hint_seconds, Some(2));
            assert_eq!(rec.computed_delay_seconds, 2.0);
            assert_eq!(rec.delay_source, "server-hint");
            assert_eq!(rec.outcome, "queued");
            assert!(!rec
                .redacted_error_preview
                .contains("ghp_abcdefghijklmnopqrstuvwxyz0123456789AB"));
            assert!(queue_path().exists());
        });

        let _ = fs::remove_dir_all(&dir);
    }

    #[test]
    fn records_non_retryable_without_message() {
        let dir = env::temp_dir().join(format!("sk-retry-rule-stop-{}", std::process::id()));
        let _ = fs::remove_dir_all(&dir);
        fs::create_dir_all(&dir).unwrap();

        with_home(&dir, || {
            let rule = RateLimitRetryRule;
            let data = json!({
                "agent": "copilot",
                "attempt": 0,
                "status": 401,
                "error": {"message": "unauthorized"}
            });
            assert!(rule.evaluate("errorOccurred", &data).is_none());

            let key = fs::read_to_string(state_key_path()).unwrap();
            let (records, skipped) = read_records(key.trim()).unwrap();
            assert_eq!(skipped, 0);
            assert_eq!(records.len(), 1);
            assert_eq!(records[0].outcome, "stopped");
            assert!(records[0].stop_reason.is_some());
        });

        let _ = fs::remove_dir_all(&dir);
    }
}
