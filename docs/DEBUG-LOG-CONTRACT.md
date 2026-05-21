# Agent Debug Log Browse Contract

**Schema version:** `1`
**Status:** Draft — WBS-101
**Blocks:** WBS-103 (backend route), WBS-104/WBS-105 (UI), WBS-106 (TS/Zod schemas)
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

The HTTP response envelope for `GET /api/session/{id}/debug-log`.

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
  endpoint is reserved for WBS-103 design.

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

## Non-Goals

The following are explicitly **out of scope** for this contract (WBS-101):

1. Backend HTTP route implementation (`GET /api/session/{id}/debug-log`) — WBS-103.
2. Frontend UI panel or event row renderer — WBS-104/WBS-105.
3. TypeScript / Zod schema implementation — WBS-106.
4. SSE streaming of debug log events — WBS-107.
5. Redaction engine implementation — WBS-102.
6. Persistence or indexing of debug log entries — existing `event_offsets` table handles raw
   indexing; WBS-101 does not change the DB schema.
7. Real-time filtering or search — WBS-108+.
8. Performance benchmarks — WBS-109.
9. CI integration — WBS-110.

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
