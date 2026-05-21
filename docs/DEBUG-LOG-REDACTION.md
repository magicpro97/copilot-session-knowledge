# DEBUG-LOG-REDACTION.md

> **Status:** Implemented — `browse/core/redaction.py` (WBS-102 / Issue #427).
> Merge is blocked on WBS-101 final field allowlist reconciliation (#426 merge-gate).

---

## Overview

The `browse.core.redaction` module provides an **allowlist-first** sanitizer
for structured `BrowseDebugEntry` dicts before they are written to any
debug-log sink.  Every field not in the top-level allowlist is silently
dropped; the `redacted` boolean flag is set to `True` whenever any drop or
transformation occurs.

The module is pure Python 3.10+ stdlib (no third-party dependencies) and
follows the `browse/core/csp.py` pattern of a small, focused helper.

---

## Write-Time Allowlist Policy

Only fields explicitly listed below may appear in a `BrowseDebugEntry` that
leaves the sanitizer.  **Unknown keys are dropped, not passed through.**

This is the opposite of a denylist approach: new fields must be explicitly
allow-listed and reviewed before they can carry data through the pipeline.

### Top-Level Allowlist

| Field | Type | Rule |
|---|---|---|
| `idx` | `int` | Required; must be `>= 0`; coerced to `0` if invalid. |
| `timestamp` | `str \| None` | ISO-8601 UTC string or `None`; invalid → `None`. |
| `kind` | `str` | Enum — see below; unknown → `"generic"`. |
| `level` | `str` | Enum `debug\|info\|warn\|error`; unknown → `"info"`. |
| `source` | `str` | Enum `cli\|hook\|browse\|vscode\|unknown`; unknown → `"unknown"`. |
| `message` | `str` | Capped at 2048 chars; bearer/URL-token/path-username redacted. |
| `tool_name` | `str` | Pattern `^[a-zA-Z0-9_.-]{1,64}$`; invalid → dropped. |
| `duration_ms` | `int` | Must be `>= 0`; invalid → dropped. |
| `span_id` | `str` | Lowercase hex `^[0-9a-f]{8,32}$`; invalid → dropped. |
| `parent_span_id` | `str` | Same pattern as `span_id`. |
| `status` | `str` | Enum `ok\|error\|cancelled\|timeout\|pending`; unknown → `"error"`. |
| `attrs` | `dict` | Strict flat scalar allowlist (see below). |
| `redacted` | `bool` | Set by the sanitizer; any input value is overwritten. |

### `kind` Enum

`session_start` · `turn_start` · `llm_request` · `tool_call` · `hook` ·
`subagent` · `agent_response` · `error` · `generic`

---

## `attrs` Allowlist

The `attrs` dict is constrained to **flat scalar values only** (`str`, `int`,
`float`, `bool`, `None`).  Nested dicts or lists are dropped regardless of
the key name.

### Allowed Keys

| Key | Notes |
|---|---|
| `schema_version` | Integer schema version tag. |
| `session_uuid` | Must match RFC 4122 UUID format; malformed → dropped. |
| `model` | Free string; run through text-redaction pass. |
| `route` | Path only — **query string forbidden**; `?` present → dropped. |
| `status_code` | HTTP status integer. |
| `exit_code` | Process exit code. |
| `event_count` | Non-negative integer. |
| `bytes_in` | Ingress byte count. |
| `bytes_out` | Egress byte count. |
| `tokens_in` | LLM input token count. |
| `tokens_out` | LLM output token count. |
| `attempt` | Retry attempt number. |
| `cache_hit` | Boolean cache indicator. |
| `truncated` | Boolean truncation indicator. |
| `error_category` | Short error classifier string. |
| `latency_ms` | Latency in milliseconds. |
| `queue_depth` | Queue depth integer. |

### Explicitly Forbidden Keys (representative list)

The following are dropped even if they appear in `attrs`.  Any key **not**
in the allowed list above is also dropped.

`args` · `input` · `output` · `result` · `payload` · `body` · `request` ·
`response` · `messages` · `prompt` · `completion` · `headers` · `cookies` ·
`authorization` · `env` · `environment` · `cwd` · `path` · `file` ·
`filename` · `url` · `referer` · `origin` · `host` · `dom` · `html` ·
`network` · `localStorage` · `sessionStorage` · `host_profile` · `token` ·
`secret` · `key` · `password` · `credential` · `pairing` · `ticket`

Also dropped: any key matching `token`, `secret`, `key`, `password`, `pass`,
`credential`, or `auth` (substring), and any key with prefixes `http_`,
`dom_`, `network_`, `vs_`, `vscode_`.

---

## Text and Path Redaction Patterns

Applied (in order) to every `message` string and every allowlisted `attrs`
string value:

1. **Bearer tokens** — `(?i)\bbearer\s+\S+` → `[REDACTED]`
2. **URL query-string tokens** — `([?&]\w*(?:token|key|secret|auth)\w*=)[^\s&]+`
   → `\1[REDACTED]` (parameter name preserved, value scrubbed)
3. **Windows path usernames** — `(?i)([A-Za-z]:[/\\]Users[/\\])[^/\\\s]+`
   → `\1[REDACTED]`
4. **Unix home usernames** — `(/home/)[^/\s]+` → `\1[REDACTED]`
5. **macOS path usernames** — `(/Users/)[^/\s]+` → `\1[REDACTED]`
6. **Defence-in-depth** — `browse.core.operator_console.redact_secrets` is
   applied as a final pass to catch GitHub tokens, AWS keys, OpenAI keys,
   JWTs, and generic `key=value` assignments.  Import failures are logged at
   DEBUG and the module-level patterns above still apply.

`message` is capped at **2048 characters** before any redaction pass.

---

## Fail-Closed Behaviour

`redact_entry(entry)` wraps all processing in a `try/except` block.  On any
unhandled exception:

1. The error is logged via `logging.getLogger("browse.redaction").exception(…)`
   using only the *type name* of the entry — no field values are included in
   the log message.
2. A minimal **sentinel dict** is returned:

   ```json
   {
     "idx": <original idx if safe to read, else 0>,
     "kind": "generic",
     "level": "error",
     "source": "unknown",
     "attrs": {},
     "redacted": true
   }
   ```

This guarantees that callers always receive a dict with `redacted: true` and
never receive an exception, even when processing malformed or adversarial
inputs.

---

## Threat Model

### In-scope threats

| Threat | Mitigation |
|---|---|
| Bearer / API tokens in log messages | `_BEARER_RE` pattern + `redact_secrets` pass |
| JWTs embedded in free-text or allowlisted attrs | `redact_secrets` JWT pattern |
| OS path usernames (Windows `Users\`, Unix `/home/`, macOS `/Users/`) | `_WIN_PATH_RE`, `_UNIX_PATH_RE`, `_MACOS_PATH_RE` |
| Query-string tokens in `route` attr | Route dropped if `?` present |
| Sensitive payload keys (`prompt`, `messages`, etc.) | Allowlist-first attrs policy |
| Host-profile dicts carrying tokens | `host_profile` not in allowlist; nested dicts always dropped |
| Attacker-controlled field names injecting new top-level keys | Unknown top-level keys dropped |
| Future field additions bypassing review | Allowlist requires explicit additions |
| Exception revealing original values in logs | Fail-closed uses `type(entry).__name__` only |

### Out-of-scope (separate controls)

- Transport-layer encryption (TLS) — handled by the HTTP/WebSocket layer.
- Access control to the debug-log endpoint — handled by `browse/core/auth.py`.
- Long-term storage retention — handled by the operator/admin configuration.
- Redaction of already-persisted entries — not retroactive.

---

## Relationship to `operator_console.redact_secrets`

`browse.core.operator_console.redact_secrets` is a **denylist** pattern-
scanner for raw CLI output streams.  It is *not* replaced or deprecated by
this module.

`redaction.redact_entry` provides **allowlist-first structured-field
classification** for `BrowseDebugEntry` dicts.  It calls `redact_secrets`
internally as a defence-in-depth text pass on string fields, so the two
controls layer:

```
raw CLI output  →  redact_secrets (denylist, free-text)
BrowseDebugEntry →  redact_entry   (allowlist-first, structured)
                         └── _redact_text() → redact_secrets (defence-in-depth)
```

Do not remove `redact_secrets`; do not remove `_sanitize_event_value`; do not
merge this module's allowlist logic back into `operator_console.py`.

---

## #426 Merge-Gate Reconciliation

This module was implemented in parallel with **WBS-101** (which produces
`docs/DEBUG-LOG-CONTRACT.md`).  The provisional field allowlists above are
based on the Opus security decision recorded in the WBS-102 context packet.

**Before merging this PR:**

1. Compare `_TOP_LEVEL_KEYS` and `_ATTRS_ALLOWLIST` in `browse/core/redaction.py`
   against the final field table in `docs/DEBUG-LOG-CONTRACT.md` (WBS-101).
2. For any field in the contract that is absent from the allowlists here, add
   it with an explicit validator and a corresponding test case.
3. For any field in the allowlists here that is absent from the contract,
   either add it to the contract or remove it from the allowlist.
4. Update this doc to reference the contract version after reconciliation.
5. Re-run `python tests/test_browse_redaction.py` and `python test_security.py`
   to confirm no regressions.

See also: issue [#427](https://github.com/magicpro97/copilot-session-knowledge/issues/427)
and the blocking issue [#426](https://github.com/magicpro97/copilot-session-knowledge/issues/426).

---

## Testing

```bash
# Targeted redaction tests (15 required security cases)
python tests/test_browse_redaction.py

# Or with pytest if available
python -m pytest tests/test_browse_redaction.py -v

# Security regression suite
python test_security.py

# Full suite
python run_all_tests.py

# Syntax check
python -m py_compile browse/core/redaction.py tests/test_browse_redaction.py

# Ruff lint
ruff check browse/core/redaction.py tests/test_browse_redaction.py
```
