# sk Retry System

## Overview

The native sk retry library (`sk-rust/src/retry/mod.rs`) provides a unified retry
policy, error classification, delay calculation, and retry decision logic.

- **`decide_with_listener()`** — used by the embedding HTTP client; invokes the
  external listener hook on each retry.
- **`next_delay()`** — used directly by the sync markers file-rename loop; does not
  invoke the listener hook.

## Retry Policy

| Parameter      | Default | Description                                       |
|----------------|---------|---------------------------------------------------|
| `base`         | 1 s     | Base delay for the first retry                    |
| `cap`          | 60 s    | Maximum delay (exponential cap)                   |
| `multiplier`   | 1.6     | Exponential back-off multiplier                   |
| `jitter`       | (0.5, 1.0) | Jitter range applied to the computed delay     |
| `max_attempts` | 5       | Maximum number of retry attempts before stopping  |
| `budget`       | 5 min   | Optional wall-clock budget; `None` = no limit     |

## Error Classification

`classify_error()` inspects only the *structure* of the error text via regex pattern
matching. Raw error text is never stored or returned — only a `RetryKind` variant — so
the retry library itself does not persist or log raw error content; callers should
sanitize before logging.

| Kind          | Examples                                              | Retried? |
|---------------|-------------------------------------------------------|----------|
| `Auth`        | 401, 403, unauthorized, forbidden, invalid_api_key    | No       |
| `Quota`       | insufficient_quota, quota_exceeded, billing_hard_limit| No       |
| `RateLimit`   | 429, rate_limit, too_many_requests                    | Yes      |
| `Throttle`    | throttl*                                              | Yes      |
| `Overloaded`  | overloaded, over_capacity, server_overload            | Yes      |
| `Server5xx`   | 5xx, internal_server_error, bad_gateway, service_unavailable | Yes |
| `Unknown`     | Non-empty text matching no pattern                    | Yes      |

`x-should-retry: false` in the error text always stops the retry sequence regardless
of other patterns.

## Retry Decision

`decide()` evaluates in priority order:

1. `attempt >= max_attempts` → `Stop(MaxAttempts)`
2. `elapsed >= budget` → `Stop(Budget)`
3. `x-should-retry: false` → `Stop(XShouldRetryFalse)`
4. Empty error text → `Stop(NonRetryable)`
5. `Auth` kind → `Stop(AuthFailure)`
6. `Quota` kind → `Stop(QuotaExhausted)`
7. All other kinds → `Retry(delay, kind)`

## `SK_RETRY_LISTENER` — External Retry Listener Hook

Set the `SK_RETRY_LISTENER` environment variable to the absolute path of an executable
script to receive retry events and optionally abort or delay retries.

```sh
export SK_RETRY_LISTENER=/path/to/my-retry-listener.sh
```

When `SK_RETRY_LISTENER` is **unset** and no default listener file exists, the hook adds
**minimal overhead**: one env-var check and one filesystem probe, no subprocess.

### Default listener location

If `SK_RETRY_LISTENER` is not set, sk looks for a default listener at:

| Platform | Default path                                   |
|----------|------------------------------------------------|
| Unix     | `~/.copilot/hooks/sk-retry-listener.sh`        |
| Windows  | `~/.copilot/hooks/sk-retry-listener.ps1`       |

### Protocol

The listener is invoked as a subprocess whenever the retry engine is about to retry a
request. A JSON payload is written to the listener's **stdin** describing the pending
retry:

```json
{
  "ts": "2025-01-01T12:00:00Z",
  "hook": "429-retry",
  "agent": "copilot",
  "attempt": 0,
  "max_attempts": 5,
  "detected_pattern": "RateLimit",
  "status_code": 429,
  "retry_after_hint_seconds": 2.0,
  "computed_delay_seconds": 1.6,
  "delay_source": "exponential",
  "elapsed_total_seconds": 0.5,
  "outcome": "queued"
}
```

> **`attempt` is 0-indexed** — the first retry attempt has `attempt: 0`, the second has
> `attempt: 1`, and so on.

The listener may write one of the following JSON responses to **stdout**:

| stdout                              | Effect                                         |
|-------------------------------------|------------------------------------------------|
| `{"abort":true}`                    | Stop the retry sequence immediately            |
| `{"delay_override_seconds": N}`     | Sleep N seconds instead (clamped to 0–300 s)   |
| *(empty, non-JSON, or other fields)*| Proceed with the computed delay (Observe)      |

### Timeout

The listener must respond within **2 seconds**. If the deadline is exceeded, sk proceeds
with the computed delay (fire-and-forget — the child process is left to finish on its
own).

### Environment variables

In addition to stdin JSON, the following environment variables are set for the listener:

| Variable                       | Value                                   |
|--------------------------------|-----------------------------------------|
| `SK_RETRY_HOOK`                | Hook name (e.g. `429-retry`)            |
| `SK_RETRY_AGENT`               | Agent identifier (e.g. `copilot`)       |
| `SK_RETRY_ATTEMPT`             | Current attempt number                  |
| `SK_RETRY_MAX_ATTEMPTS`        | Policy max attempts                     |
| `SK_RETRY_DETECTED_PATTERN`    | Classified retry kind (e.g. `RateLimit`)|
| `SK_RETRY_COMPUTED_DELAY_SECONDS` | Computed delay in seconds            |
| `SK_RETRY_ELAPSED_SECONDS`     | Total elapsed time so far in seconds    |

### Example listener (bash)

```bash
#!/usr/bin/env bash
# ~/.copilot/hooks/sk-retry-listener.sh
# Abort after 3 attempts; use a flat 5-second delay for rate-limit errors.

read -r payload

attempt=$(echo "$payload" | python3 -c "import sys,json; print(json.load(sys.stdin)['attempt'])")
pattern=$(echo "$payload" | python3 -c "import sys,json; print(json.load(sys.stdin)['detected_pattern'])")

if [ "$attempt" -ge 3 ]; then
  echo '{"abort":true}'
elif [ "$pattern" = "RateLimit" ]; then
  echo '{"delay_override_seconds":5}'
fi
```

### Stop reason

When the listener returns `{"abort":true}`, the retry sequence stops with
`StopReason::ListenerAbort`. Callers can distinguish this from other stop reasons.

## Implementation

The wiring is implemented in `sk-rust/src/hooks/retry_listener.rs` via
`decide_with_listener()`, which wraps `decide()` and invokes the listener only when a
retry would otherwise proceed. Call sites (e.g. `sk-rust/src/embeddings/http.rs`) use
`decide_with_listener()` instead of `decide()` directly to participate in the listener
hook.
