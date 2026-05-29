# sk retry-listener hook

The retry-listener hook lets you observe and influence `sk`'s automatic retry
behaviour for rate-limited API calls (HTTP 429 and related errors).

When `sk` decides to retry a request it spawns an external script, sends a
JSON event payload on **stdin**, and optionally reads a decision on **stdout**.

---

## Path resolution

The listener script is resolved in this order:

| Priority | Source | Value |
|----------|--------|-------|
| 1 | `$SK_RETRY_LISTENER` env var | Absolute path to any executable or sh-runnable script |
| 2 | Default (Unix) | `~/.copilot/hooks/sk-retry-listener.sh` |
| 2 | Default (Windows) | `~\.copilot\hooks\sk-retry-listener.ps1` |

If no file exists at the resolved path the hook is silently skipped.

---

## Payload schema (stdin JSON)

One JSON object is sent as a single line on **stdin**:

| Field | Type | Description |
|-------|------|-------------|
| `ts` | string | ISO-8601 UTC timestamp of the retry decision |
| `hook` | string | Always `"429-retry"` |
| `agent` | string | Agent identifier (default `"copilot"`) |
| `attempt` | integer | Zero-indexed retry attempt number |
| `max_attempts` | integer | Maximum attempts from the active policy |
| `detected_pattern` | string | Classified error kind (e.g. `"RateLimit"`, `"Server5xx"`) |
| `status_code` | integer\|null | HTTP status code if available |
| `retry_after_hint_seconds` | number\|null | Server-supplied `Retry-After` hint in seconds |
| `computed_delay_seconds` | number | Delay `sk` computed before any override |
| `delay_source` | string | `"retry_after"` or `"exponential"` |
| `elapsed_total_seconds` | number | Total wall-clock time elapsed so far in this retry sequence |
| `outcome` | string | `"queued"` (retry will proceed unless listener says otherwise) |

### Example payload

```json
{
  "ts": "2025-01-01T12:34:56Z",
  "hook": "429-retry",
  "agent": "copilot",
  "attempt": 2,
  "max_attempts": 5,
  "detected_pattern": "RateLimit",
  "status_code": 429,
  "retry_after_hint_seconds": null,
  "computed_delay_seconds": 3.14,
  "delay_source": "exponential",
  "elapsed_total_seconds": 4.27,
  "outcome": "queued"
}
```

---

## Stdout response (optional)

Your script may write **one JSON object** to stdout to influence the retry:

| Response | Effect |
|----------|--------|
| `{"abort": true}` | Abort the retry sequence immediately (no further retries) |
| `{"delay_override_seconds": N}` | Use `N` seconds as the delay (clamped to `[0, 300]`) |
| *(empty or any other output)* | Observe — no change to the computed behaviour |

Non-JSON or unparseable output is logged as a warning and treated as *observe*.

---

## Exit codes

The exit code of the listener is **not inspected**.  Only stdout is read.
Use stderr for diagnostics; up to 2 KB of stderr is captured and included in
internal audit logs.

---

## Timeout

The listener has a **2-second hard timeout**.  If it does not respond within
2 seconds the hook is treated as a no-op (`Observe`) and `sk` proceeds with
the computed delay.  The child process is left to finish on its own.

---

## Platform notes

| Platform | Spawn behaviour |
|----------|----------------|
| Unix (executable bit set) | Executed directly |
| Unix (no executable bit) | Run via `sh <path>` |
| Windows | `powershell.exe -NoProfile -ExecutionPolicy Bypass -File <path>` |

---

## Environment variables available in the listener

In addition to the JSON payload on stdin, the following environment variables
are set for convenience:

| Variable | Value |
|----------|-------|
| `SK_RETRY_HOOK` | `"429-retry"` |
| `SK_RETRY_AGENT` | Agent identifier |
| `SK_RETRY_ATTEMPT` | Attempt number |
| `SK_RETRY_MAX_ATTEMPTS` | Maximum attempts |
| `SK_RETRY_DETECTED_PATTERN` | Classified error kind |
| `SK_RETRY_COMPUTED_DELAY_SECONDS` | Computed delay in seconds |
| `SK_RETRY_ELAPSED_SECONDS` | Total elapsed seconds |

---

## Example: log to file (Unix)

```sh
#!/usr/bin/env sh
PAYLOAD=$(cat)
echo "$PAYLOAD" >> "${HOME}/.copilot/retry-events.log"
```

## Example: abort after 3 attempts

```sh
#!/usr/bin/env sh
PAYLOAD=$(cat)
ATTEMPT=$(echo "$PAYLOAD" | grep -o '"attempt":[0-9]*' | grep -o '[0-9]*')
if [ "${ATTEMPT:-0}" -ge 3 ]; then
  printf '{"abort":true}'
fi
```

## Example: log to file (Windows PowerShell)

```powershell
$payload = $input | ConvertFrom-Json
$logPath = Join-Path $env:USERPROFILE ".copilot\retry-events.log"
Add-Content -Path $logPath -Value ($payload | ConvertTo-Json -Compress)
```
