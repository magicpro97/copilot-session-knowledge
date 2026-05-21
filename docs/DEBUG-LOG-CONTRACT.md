# Agent Debug Log Browse Contract

**Schema version:** `1`
**Status:** WBS-101 complete; WBS-103 storage/transport complete; WBS-105 operator capture complete
**Blocks:** WBS-104 (UI panel), WBS-106 (TS/Zod schemas)
**Redaction policy:** See [Security & Redaction](#security--redaction) — WBS-102

---

## Overview

This document defines the read-only browse contract for surfacing Copilot CLI agent debug log
entries in the browser UI. The contract covers:

- The `BrowseDebugEntry` per-event shape
- The `DebugLogResponse` envelope
- Event taxonomy (kinds)
- Synthetic span-id derivation rule
- Timestamp and duration fallback semantics
- Message preview / truncation rules
- Source-of-truth mapping: VS Code Agent Debug Log, OTel `ReadableSpan`, OTLP, future ATIF
- Non-goals (what this contract intentionally excludes)
- A reserved placeholder for the WBS-102 redaction policy

---

## Schemas

### `BrowseDebugEntry`

One entry in the debug log, corresponding to a single parsed JSONL line from the Copilot CLI
output stream.

| Field | Type | Nullable | Description |
|---|---|---|---|
| `idx` | `integer` | no | Zero-based position of this event in the output stream. Monotonically increasing per session run. |
| `timestamp` | `string (ISO-8601)` | yes | Event wall-clock time. `null` when the source line carries no timestamp; never synthesized. |
| `kind` | `string` | no | Event taxonomy value (see [Event Taxonomy](#event-taxonomy)). |
| `level` | `string` | yes | Severity level: `"debug"`, `"info"`, `"warn"`, `"error"`. `null` when absent in source. |
| `source` | `string` | no | Source identifier, e.g. `"operator_console"`, `"hook_runner"`, `"sk_watch"`. |
| `message` | `string` | no | Human-readable event message, truncated to 200 chars (preview; see [Preview / Truncation](#preview--truncation)). |
| `tool_name` | `string` | yes | Tool name for `tool_call` events. `null` for other kinds. |
| `duration_ms` | `number` | yes | Elapsed milliseconds for completed tool calls or spans. `null` when no paired completion exists. **Never `0` as a sentinel.** |
| `span_id` | `string` | yes | 16 lowercase hex chars. Synthetic when no native `spanId` is present; see [Synthetic Span-ID Rule](#synthetic-span-id-rule). |
| `parent_span_id` | `string` | yes | 16 lowercase hex chars for the parent span, or `null`. |
| `status` | `string` | yes | Terminal status: `"ok"`, `"error"`, `"cancelled"`. `null` for non-terminal events. |
| `attrs` | `object` | yes | Arbitrary key/value attributes promoted from the source event. Redacted before serving (WBS-102). |
| `redacted` | `boolean` | no | `true` if one or more fields were modified by the redaction pass (WBS-102). `false` otherwise. |

**Constraints:**
- `idx` must be unique within a session run.
- `span_id`, when present, must be exactly 16 lowercase hex characters.
- `redacted` is always present (never `null`).

---

### `DebugLogResponse`

The HTTP response envelope for planned `GET /api/session/{id}/debug-log` consumers.
For the WBS-104 operator run debug endpoint, see
[WBS-104 Response Shape](#response-shape); it uses `events` instead of `entries`
and includes `run_id`, `from`, `limit`, and `has_more`.

| Field | Type | Nullable | Description |
|---|---|---|---|
| `entries` | `BrowseDebugEntry[]` | no | Ordered list of debug entries for the requested page/window. |
| `total` | `integer` | no | Total number of entries for this session run (for pagination). |
| `session_id` | `string` | no | Opaque session identifier (UUID). |
| `schema_version` | `string` | no | Contract version string; currently `"1"`. Increment on breaking changes. |

---

## Event Taxonomy

The `kind` field classifies each entry into one of the following values.

| `kind` | Description | Source signals |
|---|---|---|
| `session_start` | Session or run lifecycle start event. | `type == "session_start"` or equivalent lifecycle marker |
| `turn_start` | Beginning of a new agent turn (user prompt submitted). | `type == "turn_start"` |
| `llm_request` | LLM API request dispatched. | `type == "llm_request"` or `"assistant.request"` |
| `tool_call` | Tool invocation (start or completion). Pair linked by `span_id`. | `type == "tool_call"` or `"tool_result"` |
| `hook` | Pre/post tool use hook execution. | `type == "hook"` |
| `subagent` | Sub-agent dispatch or result. | `type == "subagent"` |
| `agent_response` | Final assistant message or delta. | `type == "assistant.message"` or `"assistant.message_delta"` |
| `error` | Error or exception raised during execution. | `type == "error"` or `"exception"` |
| `generic` | Any valid JSON event with a recognized `type` that does not match the above. | Fallback for structured but unclassified events |
| `raw` | Non-JSON line or JSON object without a `type` field. | `_raw_event()` path in `operator_console.py` |

**Classification rule:** The classifier maps the source `type` string to a `kind`. Unknown `type`
values map to `generic`. Lines that fail `json.loads` or lack a `type` key map to `raw`.

---

## Synthetic Span-ID Rule

When a source event does not carry a native 16-hex `spanId`, assign a deterministic synthetic
span ID:

```python
import hashlib

def synthetic_span_id(source: str, idx: int, monotonic_seq: int) -> str:
    """Return a 16-char lowercase hex span_id.

    source       -- source identifier string (e.g. "operator_console")
    idx          -- zero-based event index within the stream
    monotonic_seq -- monotonically increasing counter per session run (start at 1)
    """
    candidate = hashlib.sha1(
        f"{source}:{idx}:{monotonic_seq}".encode("utf-8")
    ).hexdigest()[:16]
    if candidate == "0000000000000000":
        # Astronomically unlikely; increment seq and re-hash to avoid sentinel value
        return synthetic_span_id(source, idx, monotonic_seq + 1)
    return candidate
```

**Worked example:**

```
source = "operator_console", idx = 0, monotonic_seq = 1
sha1("operator_console:0:1") -> 297c86d9d2c468a3...
span_id = "297c86d9d2c468a3"
```

**Rules:**
- The result must be exactly 16 lowercase hex characters.
- It must not equal `"0000000000000000"` (increment `monotonic_seq` and re-hash if it does).
- If the source event carries a native `spanId` that is already 16 lowercase hex chars, use it
  directly — do **not** re-hash it.
- `parent_span_id` follows the same rule: use the native value if present, otherwise `null`.

**Paired tool-call completion rule (carve-out):**

For `tool_call` completion events (e.g. `tool_result` events) that have **no native `spanId`**,
reuse the synthetic `span_id` that was assigned to the paired start event rather than computing a
new one from their own `idx`. This matches standard OpenTelemetry semantics where a span covers
the full duration from start to completion.

Concretely: when a `tool_result` or `tool_call` completion row is paired with a `tool_call` start
row, set `span_id = <start row's span_id>`. The `idx` of the completion row is **not** used in
the formula for its own `span_id`; it simply inherits the start row's value.

> **Why:** OTel spans are identified by a single `spanId` for the whole operation interval. Using
> the same `span_id` on both start and complete rows lets the UI link the two rows into a single
> visual span and compute `duration_ms` from the two timestamps without an additional lookup key.

---

## Timestamp Fallback

- When a source event includes a parseable ISO-8601 timestamp, populate `timestamp`.
- When absent, set `timestamp = null`. **Do not synthesize wall-clock values.**
- Wall-clock synthesis would create false ordering in the UI and violate the principle of
  surfacing only what the agent actually recorded.

---

## Duration Fallback

- Populate `duration_ms` only when the event has a paired completion (tool start + tool result
  linked by `span_id`, or a native OTel `duration` field).
- Set `duration_ms = null` when no paired completion exists.
- **Never use `0` as a sentinel** for "unknown duration." Zero is a valid measured duration
  (sub-millisecond operations); `null` unambiguously means "not measured."

---

## Preview / Truncation

- The `message` field contains a **200-character preview** of the event's human-readable content.
  This matches `_PREVIEW_LEN = 200` in `browse/routes/timeline.py`.
- Full raw prompt text, tool input/output payloads, and assistant response bodies are **excluded**
  from the default `BrowseDebugEntry` contract.
- If the UI requires full content, a separate `GET /api/session/{id}/debug-log/{idx}/raw`
  endpoint is reserved for a future WBS iteration beyond WBS-103.

---

## Source-of-Truth Mapping

### VS Code Agent Debug Log (`IDebugLogEntry`)

| VS Code field | `BrowseDebugEntry` field | Notes |
|---|---|---|
| `timestamp` | `timestamp` | ISO-8601 string or null |
| `message` | `message` | Truncated to 200 chars |
| `severity` | `level` | `"debug"`, `"info"`, `"warn"`, `"error"` |
| (event type) | `kind` | Mapped via taxonomy table |
| `spanId` | `span_id` | Use native if 16-hex; else synthetic |
| `parentSpanId` | `parent_span_id` | Use native if 16-hex; else null |

### OTel `ReadableSpan`

| OTel field | `BrowseDebugEntry` field | Notes |
|---|---|---|
| `name` | `message` (preview) | Span name as message prefix |
| `startTime` | `timestamp` | Converted to ISO-8601 |
| `duration` | `duration_ms` | Nanoseconds / 1e6 |
| `spanId` | `span_id` | 16 lowercase hex |
| `parentSpanId` | `parent_span_id` | 16 lowercase hex or null |
| `status.code` | `status` | `"ok"` / `"error"` / `"cancelled"` |
| `attributes` | `attrs` | Promoted as-is; redacted by WBS-102 pass |

### Future OTLP / ATIF mapping

- OTLP export will use the same `span_id` / `parent_span_id` contract above.
- ATIF (Agent Telemetry Interchange Format) is an anticipated future standard; the `attrs` object
  is deliberately open to carry ATIF extensions without a contract version bump.

### Explicitly rejected sources

- **`chatSessions`** — contains user conversation content; not agent telemetry.
- **`transcripts`** — contains user prompts and responses; violates privacy/redaction boundary.

---

## Security & Redaction

> **WBS-102 placeholder** — the redaction policy is defined in a separate issue/PR.
> See: <https://github.com/magicpro97/copilot-session-knowledge/issues/427>

This contract defines only the **boundary**:

- `attrs` and `message` fields **must** pass through a redaction pass before being served.
  The redaction pass is implemented in WBS-102.
- `redacted = true` signals to the UI that at least one field was modified.
- The backend route (WBS-103) **must not** serve `BrowseDebugEntry` objects without applying the
  WBS-102 redaction policy.
- The existing `redact_secrets()` and `_sanitize_event_value()` in
  `browse/core/operator_console.py` are the reference implementation that WBS-102 extends.

---

## WBS-103 Storage and Transport Contract

### Feature gating

The debug-log feature is **disabled by default**. Enable via:

- CLI flag `--debug-log`, or
- environment variable `BROWSE_DEBUG_LOG_ENABLED=1`

When disabled, `/api/debug-log/healthz` returns **404**. No DB is opened, no events are stored.

### Storage location and isolation

| Property | Value |
|---|---|
| Default DB path | `~/.copilot/operator-console/debug-log/debug-log.db` |
| Override directory | `--debug-log-dir <dir>` / `BROWSE_DEBUG_LOG_DIR` |
| SQLite pragmas | `journal_mode=WAL`, `synchronous=NORMAL` |
| Relation to `knowledge.db` | **Separate DB**; never merged or co-located |
| Relation to session-state | Path is outside `~/.copilot/session-state/` by default |

**Isolation guarantees (verified by `tests/test_browse_debug_log_exclusion.py`):**

- `watch-sessions.py` ignores `.db` files by extension — `debug-log.db` is never indexed.
- `sync-knowledge.py` auto-detect targets only `knowledge.db` paths; `operator-console/` is excluded.
- `build-session-index.py` has no reference to `operator-console` or `debug-log`.
- Session export body (`/session/{id}.md`) excludes `debug-log`/`operator-console` content.

### CLI flags and environment variables

| CLI flag | Env variable | Default | Description |
|---|---|---|---|
| `--debug-log` | `BROWSE_DEBUG_LOG_ENABLED=1` | disabled | Enable feature |
| `--debug-log-dir` | `BROWSE_DEBUG_LOG_DIR` | `~/.copilot/operator-console/debug-log/` | Storage directory |
| `--debug-log-max-age-seconds` | `BROWSE_DEBUG_LOG_MAX_AGE_S` | `86400` (24 h) | Max event age in seconds |
| `--debug-log-max-bytes` | `BROWSE_DEBUG_LOG_MAX_BYTES` | `52428800` (50 MiB) | Max total DB payload bytes |
| `--debug-log-retention-interval` | `BROWSE_DEBUG_LOG_RETENTION_INTERVAL_S` | `300` (5 min) | Retention daemon poll interval |
| `--debug-log-ephemeral` | `BROWSE_DEBUG_LOG_EPHEMERAL=1` | off | Remove DB + WAL/SHM on clean shutdown |

### Storage schema (`debug_log_storage.py`)

```sql
CREATE TABLE debug_log_events (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    ts_ns      INTEGER NOT NULL,
    session_id TEXT NOT NULL,
    idx        INTEGER NOT NULL,
    kind       TEXT NOT NULL,
    payload    TEXT NOT NULL,   -- redacted JSON only
    byte_len   INTEGER NOT NULL
);
CREATE TABLE debug_log_meta (key TEXT PRIMARY KEY, value TEXT NOT NULL);
-- schema_version = '1'
```

### Retention and pruning

Pruning is deterministic and runs in two ordered passes:

1. **Age pass** — delete rows where `ts_ns < now_ns - (max_age_s × 1 000 000 000)`.
2. **Size pass** — while `SUM(byte_len) > max_bytes`, delete the oldest 100 rows by `(ts_ns ASC, id ASC)`.

The optional background retention thread polls every `retention_interval_s` seconds.
`shutdown_storage()` stops the thread (2-second join), closes the DB, and optionally removes files.
**No `atexit` handlers; no signal handlers.** Clean shutdown must be triggered explicitly from `main`'s
`finally` block. SIGKILL / process kill can leave `debug-log.db`, `-wal`, and `-shm` files on disk.

### Payload serialization

`append_event()` passes every payload through `browse.core.redaction.redact_entry` before insert.
Only the **redacted JSON string** is stored; unredacted data is never written to the DB.
If `redact_entry` returns a dict without a `redacted` key, the insert is refused with `ValueError`.

### `/api/debug-log/healthz` endpoint

| Property | Value |
|---|---|
| Method | `GET` |
| Path | `/api/debug-log/healthz` |
| Registered when | feature enabled only |
| Response `Content-Type` | `application/json` |

**Response body (non-sensitive only):**

```json
{
  "ok": true,
  "enabled": true,
  "retention": {
    "max_age_seconds": 86400,
    "max_bytes": 52428800,
    "interval_seconds": 300
  }
}
```

The response **never** includes filesystem paths, event counts, session content, or stored debug data.

### Authentication and status semantics

| Condition | Response |
|---|---|
| Feature disabled (route not registered) | `404 Not Found` |
| Feature enabled; `?token=` query-string auth present | `401 Unauthorized` (query-string auth rejected for debug routes) |
| Feature enabled; static/demo slot active | `403 Forbidden` |
| Feature enabled; non-loopback host with no server token configured | `403 Forbidden` |
| Feature enabled; missing or invalid Bearer/cookie token | `401 Unauthorized` |
| Feature enabled; valid Bearer header or session cookie | `200 OK` |

Auth is handled by `check_debug_token()` in `browse/core/auth.py`: Bearer header > cookie; empty
server token always returns `(False, "")` — **no open-auth debug route**.

### Transport security

`/api/debug-log/*` inherits the same CORS/PNA/Vary policy as all `/api` routes:
allowlisted `Origin` only; `Vary: Origin` set; Private Network Access preflight supported.
No CSP loosening is applied for debug routes.

---

## Non-Goals

The following are explicitly **out of scope** for the current debug-log contract:

1. Session-scoped debug-log read/query API (`GET /api/session/{id}/debug-log`) — future work.
   WBS-104 implements the operator-run read endpoint below.
2. Frontend UI panel or event row renderer — future work.
3. TypeScript / Zod schema implementation — WBS-106.
4. SSE streaming of debug log events — WBS-107.
5. Redaction engine implementation — WBS-102.
6. Syncing or indexing debug-log entries into `knowledge.db` / session-state — WBS-103 persistence
   stays in the isolated local debug-log DB only.
7. Real-time filtering or search — WBS-108+.
8. Performance benchmarks — WBS-109.
9. CI integration — WBS-110.

---

## WBS-104 Debug-Log Read API

### Endpoint

```
GET /api/operator/sessions/{session_id}/runs/{run_id}/debug
```

**Registered with `debug=True`** — uses the same Bearer/cookie-only auth gate as
`/api/debug-log/healthz`.  Query-string `?token=` is rejected with `401`.

### Auth and Status Semantics

| Condition | Response |
|---|---|
| Route not registered | `404 Not Found` (falls through to regular 404 dispatch) |
| `?token=` query-string auth present | `401 Unauthorized` |
| Static/demo slot active | `403 Forbidden` |
| Non-loopback host, no server token configured | `403 Forbidden` |
| Missing or invalid Bearer/cookie token | `401 Unauthorized` |
| Valid Bearer header or session cookie | `200 OK` |
| Unknown `session_id` (or not a valid UUID4) | `404 application/json` |
| Unknown `run_id` | `404 application/json` |
| `run.session_id != session_id` (ownership mismatch) | `404 application/json` |
| Bad query parameter | `400 application/json` |

**No UUID / path leakage** — 404 error bodies never echo back the session_id, run_id,
or any filesystem path.

### Query Parameters

| Parameter | Type | Default | Constraint | Description |
|---|---|---|---|---|
| `from` | integer | `0` | `>= 0` | Pagination offset (zero-based) |
| `limit` | integer | `100` | `1..100` | Page size |
| `kind` | string | — | one of `_KIND_ENUM` | Filter by event kind |
| `level` | string | — | one of `_LEVEL_ENUM` | Filter by severity level |
| `since` | string | — | parseable ISO-8601 | Include only events with `timestamp >= since` |

Bad parameter values → `400 application/json` with `{"error": "...", "code": "BAD_PARAM"}`.

### Response Shape

```json
{
  "schema_version": "1",
  "session_id": "<uuid>",
  "run_id": "<uuid>",
  "total": 42,
  "from": 0,
  "limit": 100,
  "has_more": false,
  "events": [<BrowseDebugEntry>, ...]
}
```

`total` is the count **after** filters are applied but **before** pagination.
`has_more` is `true` when `from + limit < total`.
`events` contains at most `limit` entries, starting from the `from` index of the
filtered result set.  All entries pass through `browse.core.redaction.redact_entry`
before being returned.
Operator-console entries return nullable `tool_name`, `duration_ms`,
`parent_span_id`, and `status` keys explicitly as `null` when no source value is
available.

### Data Source

Entries are read from persisted operator run JSON `run["events"]` only:
- `get_session(session_id)` — validates the session exists
- `get_run_status(run_id)` — loads from `_ACTIVE_RUNS` memory or `~/.copilot/session-state/operator-console/runs/{session_id}/{run_id}.json`
- Strict `run["session_id"] == session_id` ownership check before reading events

**No WBS-103 SQLite debug-log storage is consulted** — that store has no `run_id`
key and no current producers.

**No WBS-105 sidecar source yet** — this endpoint intentionally does not read
`run["debug_events"]`.  Reconciling the sidecar with the read endpoint is a
follow-up for #434, because the sidecar currently has different span-id,
timestamp, nullable-field, and cap semantics.

### Operator Event → BrowseDebugEntry Mapping

| Operator event `type` | `BrowseDebugEntry.kind` |
|---|---|
| `"raw"` (non-JSON line) | `"raw"` |
| `"assistant.message"`, `"assistant.message_delta"` | `"agent_response"` |
| `"tool_call"`, `"tool_result"` | `"tool_call"` |
| `"session_start"` | `"session_start"` |
| `"turn_start"` | `"turn_start"` |
| `"llm_request"` | `"llm_request"` |
| `"hook"` | `"hook"` |
| `"subagent"` | `"subagent"` |
| `"error"`, `"exception"` | `"error"` |
| any other `type` | `"generic"` |

**Fixed fields for all operator events:**
- `source` = `"operator_console"`
- `level` = `null` (operator events carry no severity field)
- `timestamp` = extracted from inner event `timestamp`/`ts` field; `null` when absent
- `span_id` = `synthetic_span_id("operator_console", idx, 1)` (see
  [Synthetic Span-ID Rule](#synthetic-span-id-rule))

### Large-Event Truncation

If the serialized JSON bytes of a raw operator event exceed **8192 bytes**:

- `message` is replaced with: `[TRUNCATED sha256=<hex16> bytes=<n>]`
- `attrs.truncated` = `true`
- `attrs.bytes_in` = `<n>` (original byte count)
- Raw event content is **never** included in the response

Both `truncated` and `bytes_in` are in the `attrs` allowlist and survive the
redaction pass.

### `since` Filter Semantics

Events with `timestamp = null` are **excluded** when the `since` parameter is
provided.  If the timestamp cannot be parsed as ISO-8601, the event is also
excluded.

### Registry-Driven Debug Auth Gate (WBS-104 Generalisation)

`browse/core/server.py` no longer hard-codes the `/api/debug-log/` URL prefix.
The gate is now driven by `match_route(path, "GET")` returning `debug_flag=True`.
Any route registered with `debug=True` — regardless of path prefix — automatically
receives Bearer/cookie-only auth, `?token=` rejection, static-slot blocking, and the
non-loopback insecure-config 403 rule.

The `/api/debug-log/healthz` behaviour is unchanged.

### Performance

The implementation slices the filtered entry list **before** calling `redact_entry`,
so at most `limit` (default 100, max 100) entries are redacted per request.

A performance test (`tests/test_browse_debug_log_api.py::DB26`) verifies that
100 entries are served from a 1000-event run in under 200 ms.

---

## WBS-105 Operator Debug Event Capture

### Overview

WBS-105 adds a bounded, redacted **debug-event sidecar** to every operator run.  The sidecar is
stored as `run["debug_events"]` in-memory and persisted to disk alongside `run["events"]` by
`_persist_run`.  It is **never emitted over the SSE stream** — the SSE stream shape, resume
tokens, `_CHECKPOINT_INTERVAL`, and `_MAX_OUTPUT_LINES` semantics are unchanged.

### Constants

| Constant | Value | Description |
|---|---|---|
| `_MAX_DEBUG_EVENTS` | `5000` | Maximum sidecar entries per run; on overflow a sentinel is appended and further events are dropped |
| `_DEBUG_SOURCE` | `"operator_console"` | `source` label applied to every debug entry |

### Event classification (`_classify_debug_kind`)

Each Copilot CLI output line is classified into a `kind` value for the debug sidecar.

| Source event `type` | BrowseDebugEntry `kind` |
|---|---|
| `session_start` | `session_start` |
| `turn_start` | `turn_start` |
| `llm_request`, `assistant.request` | `llm_request` |
| `tool_call`, `tool_result` | `tool_call` |
| `hook`, `hook_pre`, `hook_post` | `hook` |
| `subagent`, `subagent_start`, `subagent_result` | `subagent` |
| `assistant.message`, `assistant.message_delta` | `agent_response` |
| `error`, `exception` | `error` |
| Any other typed JSON | `generic` |
| Missing `type` or non-JSON | `raw` |

### Synthetic span-ID formula

Every debug entry receives a deterministic synthetic `span_id` computed as:

```python
hashlib.sha1(f"operator_console:{idx}:{seq}".encode("utf-8")).hexdigest()[:16]
```

The result must not equal `"0000000000000000"` (increment `seq` and re-hash if it does).

### Sidecar lifecycle

1. **Initialization** — `start_run` initializes `debug_events: []`, `_debug_idx: 0`, `_debug_seq: 1`.
2. **Per-event append** — `_run_copilot_thread` calls `_build_debug_entry` + `_append_debug_event`
   after each call to `_parse_output_event`.
3. **Terminal debug events** — appended before `_persist_run` for success, failure, timeout,
   cancellation, `FileNotFoundError`, and generic exceptions.
4. **Persistence** — `_persist_run` writes `debug_events` to disk while excluding `_debug_idx`,
   `_debug_seq`, and `proc` (private mutable state).
5. **Storage** — when `BROWSE_DEBUG_LOG_ENABLED=1` and WBS-103 storage is initialized,
   `_store_debug_event` calls `debug_log_storage.append_event`.  Storage errors are logged and
   do **not** propagate — operator runs must never fail because of a debug-log hiccup.

### Truncation sentinel

When the sidecar reaches `_MAX_DEBUG_EVENTS - 1` entries, exactly one sentinel is appended:

```json
{
  "kind": "generic",
  "source": "operator_console",
  "message": "[DEBUG TRUNCATED]",
  "attrs": {"truncated": true, "event_count": 5000}
}
```

All subsequent `_append_debug_event` calls are silently dropped.  The `run["events"]` SSE list
and `_MAX_OUTPUT_LINES` are **unaffected**.

### Terminal debug event shapes

**Success** (`exit_code == 0`):
```json
{"kind": "generic", "status": "ok", "attrs": {"exit_code": 0}}
```

**Failure** (`exit_code != 0`):
```json
{"kind": "error", "status": "error", "attrs": {"exit_code": N, "error_category": "nonzero_exit"}}
```

**Timeout**:
```json
{"kind": "error", "status": "error", "attrs": {"error_category": "timeout"}}
```

**Cancellation**:
```json
{"kind": "generic", "status": "cancelled", "attrs": {}}
```

**FileNotFoundError** (CLI not found):
```json
{"kind": "error", "status": "error", "attrs": {"error_category": "cli_not_found"}}
```

**Generic exception**:
```json
{"kind": "error", "status": "error", "attrs": {"error_category": "exception"}}
```

---

## Acceptance Evidence Commands

```bash
# 1. JSONL fixtures parse cleanly
python -c "import json; [json.loads(l) for l in open('tests/fixtures/debug-log/debug-log-sample.jsonl') if l.strip()]"

# 2. Contract doc contains BrowseDebugEntry
grep -c "BrowseDebugEntry" docs/DEBUG-LOG-CONTRACT.md

# 3. Contract doc contains DebugLogResponse
grep -c "DebugLogResponse" docs/DEBUG-LOG-CONTRACT.md

# 4. Contract doc covers session_start taxonomy
grep -c "session_start" docs/DEBUG-LOG-CONTRACT.md

# 5. No real session paths in fixtures
grep -rn "\.copilot\|USERPROFILE\|/home/" tests/fixtures/debug-log/

# 6. No secrets/tokens in fixtures
grep -rn "ghp_\|sk-\|AKIA\|ey[A-Za-z]" tests/fixtures/debug-log/

# 7. Security tests
python test_security.py

# 8. Verify all non-paired synthetic span_ids match formula sha1(source:idx:1)[:16]
#    and the paired tool-call completion (idx=4) reuses its start span (idx=3).
python -c "
import json, hashlib

def formula(source, idx, seq=1):
    h = hashlib.sha1(f'{source}:{idx}:{seq}'.encode()).hexdigest()[:16]
    return h if h != '0000000000000000' else formula(source, idx, seq+1)

entries = [json.loads(l) for l in open('tests/fixtures/debug-log/debug-log-sample.jsonl') if l.strip()]

# Identify paired tool-call completion rows: share span with their start counterpart
# In this fixture idx=4 is the paired completion for idx=3.
paired_completions = {4: 3}  # {completion_idx: start_idx}

span_by_idx = {e['idx']: e['span_id'] for e in entries}
errors = []
for e in entries:
    idx = e['idx']
    sid = e['span_id']
    if sid is None:
        continue  # raw event, no span expected
    if idx in paired_completions:
        start_idx = paired_completions[idx]
        expected = span_by_idx[start_idx]
        if sid != expected:
            errors.append(f'idx={idx}: paired completion span {sid!r} != start span {expected!r}')
    else:
        expected = formula(e['source'], idx)
        if sid != expected:
            errors.append(f'idx={idx}: span {sid!r} != formula {expected!r}')

if errors:
    for err in errors: print('FAIL:', err)
    raise SystemExit(1)
print('OK: all synthetic span_ids consistent with formula and paired-tool-call reuse rule')
"
```
