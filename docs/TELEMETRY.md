# Telemetry & Contracts

> Canonical reference for telemetry surfaces, JSON field envelopes, and API contracts.

## Recall Telemetry (Phase 5)

Recall telemetry tracks how the knowledge base is accessed — counts, IDs, and output sizes only. No payload bodies are logged.

### Commands

```bash
sk index health --recall         # Recall-only text dashboard
sk index health --recall --json  # Recall-only JSON payload
# fallback: python3 ~/.copilot/tools/knowledge-health.py --recall [--json]
```

### Telemetry contract

- `recall_events` table stores lean telemetry: `call_type`, `query`, `hit_count`, `selected_entry_ids`, `output_size_chars`
- `qs --detail <id>` writes a stateless `detail_open` row per call:
  - Found entry → `hit_count=1`, `selected_entry_ids=[id]`
  - Missing entry → `hit_count=0`, `selected_entry_ids=[]`
- Default `qs "query"` aggregates the full emitted surface: primary search block + `sessions_fts` block + knowledge-entry block
- If `recall_events` is absent (older schema), recall commands still run — telemetry writes are best-effort

### Boundaries

- `--recall` output is **recall-only** — it does not append the default health dashboard
- No browse UI route, contextual summary, or provider rerank behavior in this telemetry surface

---

## Per-Entry Aggregated Recall Telemetry (issue #157 / migration v18)

Per-entry counters give each knowledge entry its own recall history: how many times it has been
surfaced, on how many unique calendar days, and from how many distinct queries.

### Tables

| Table | Purpose |
|-------|---------|
| `entry_recall_stats` | One row per `knowledge_entries.id`. Aggregated counters. |
| `entry_recall_day_log` | Dedupe log keyed by `(entry_id, day)`. Prevents same-day double-counting. |
| `entry_recall_query_log` | Dedupe log keyed by `(entry_id, query_hash)`. Prevents same-query double-counting. |

#### `entry_recall_stats` schema

| Column | Type | Description |
|--------|------|-------------|
| `entry_id` | INTEGER PK | References `knowledge_entries.id` |
| `recall_count` | INTEGER | Total times this entry was surfaced (increments every recall) |
| `recall_days` | INTEGER | Unique calendar days this entry was surfaced (UTC date) |
| `unique_queries` | INTEGER | Unique rewritten queries that surfaced this entry (SHA-256[:16] hash) |
| `first_recalled_at` | TEXT | ISO 8601 UTC timestamp of first recall |
| `last_recalled_at` | TEXT | ISO 8601 UTC timestamp of most recent recall |

### How counters are updated

`briefing.py` calls `_upsert_entry_recall_stats(db, entry_ids, rewritten_query)` using the
**caller's already-open DB connection** (not a fresh connection) from both `generate_briefing()`
and `generate_task_briefing()`, just before the connection is closed.

Increment semantics:

- **`recall_count`** — always `+1` once per unique entry per call (duplicate `entry_id`s in a
  single helper invocation are deduplicated before writing).
- **`recall_days`** — `+1` only when the today's UTC date is new for that entry (guarded by
  `INSERT OR IGNORE INTO entry_recall_day_log`).
- **`unique_queries`** — `+1` only when the `sha256[:16]` of the rewritten query is new for that
  entry (guarded by `INSERT OR IGNORE INTO entry_recall_query_log`).

All writes are **best-effort** (wrapped in `try/except`). A missing table or any DB error is
silently swallowed — the main briefing surface is never impacted.

### Sync policy

All three tables are registered as `upload_only` — they are pushed to the remote replica but
never pulled back, matching the same policy as `recall_events`.

```
entry_recall_stats    → upload_only
entry_recall_day_log  → upload_only
entry_recall_query_log → upload_only
```

This policy is registered in:
- `migrate.py` → `_seed_sync_table_policies()`
- `sync-daemon.py` → `DEFAULT_SYNC_TABLE_POLICIES`
- `sync-knowledge.py` → `ensure_sync_runtime_schema()`
- `sk-rust/src/sync/schema.rs` → `DEFAULT_SYNC_TABLE_POLICIES`

---

## JSON Field Envelopes

These output shapes are **stable contracts** — do not change key names or nesting without a migration path.

### `query-session.py --task --export json`

```json
{
  "entries": [ { ... } ]
}
```

### `briefing.py --task --json`

```json
{
  "tagged_entries": [ { ... } ],
  "related_entries": [ { ... } ]
}
```

### `briefing.py --pack`

```json
{
  "entries": {
    "mistake": [ { ... } ],
    "pattern": [ { ... } ],
    "decision": [ { ... } ]
  }
}
```

### Phase 4 read-surface metadata

- `snippet_freshness` — exactly one of: `fresh | drifted | missing | unknown`
- `related_entry_ids` — JSON integer array, confidence-ranked, capped to top 3

---

## Sync Contracts

### Config key

Single config key: `connection_string` in `~/.copilot/tools/sync-config.json`

```bash
python3 ~/.copilot/tools/sync-config.py --setup https://gateway.example.com
python3 ~/.copilot/tools/sync-config.py --setup-env SYNC_GATEWAY_URL
python3 ~/.copilot/tools/sync-config.py --status --json
python3 ~/.copilot/tools/sync-config.py --get
python3 ~/.copilot/tools/sync-config.py --clear
```

- Accepts HTTP(S) gateway URLs **only** — not raw Postgres or libSQL DSNs
- Missing `connection_string` → daemon stays local-only/idle (not fatal)

### Gateway contract (`sync-gateway.py`)

`sync-gateway.py` is **reference/mock only** — not a production authority. It exposes:

| Endpoint | Method | Description |
|----------|--------|-------------|
| `/sync/push` | POST | Receive push from client |
| `/sync/pull` | GET | Return records for client |
| `/healthz` | GET | Gateway health; advertises `/api/sync/status` |

### Daemon behavior

```bash
python3 ~/.copilot/tools/sync-daemon.py --once              # One-shot sync
python3 ~/.copilot/tools/sync-daemon.py --daemon            # Continuous daemon
python3 ~/.copilot/tools/sync-daemon.py --interval 30       # Custom interval (seconds)
python3 ~/.copilot/tools/sync-daemon.py --push-only         # Push only
python3 ~/.copilot/tools/sync-daemon.py --pull-only         # Pull only
```

- Backlog-aware adaptive per-cycle sync limits (`sync_txns` volume + relation-heavy queue boost)
- Pull consumes multiple pages per cycle (`MAX_PULL_PAGES_PER_CYCLE`)
- After pull: refreshes local retrieval surfaces (`knowledge_fts`, `ke_fts`) for touched rows
- Local DB is **always** the authoritative read source; remote is transport/storage only

### Default provider recommendation

Neon (backing Postgres) + Railway (thin gateway host) — this is default rollout guidance, not a vendor lock.

---

## Browse Diagnostics (read-only)

Browse sync and health surfaces are **read-only**:

| Endpoint | Description |
|----------|-------------|
| `/healthz` | Advertises `sync_status_endpoint: "/api/sync/status"` |
| `/api/sync/status` | Local queue / failure / config / cursor state only |
| `/api/scout/status` | Trend Scout discovery status, including `discovery_lanes[]` |

Do **not** implement write behavior in browse diagnostics routes.

---

## Tentacle Outcome Metrics

Metrics for structured handoff status and file receipts are persisted in `skill-metrics.db`.

### `tentacle_outcomes` table

| Column | Type | Description |
|--------|------|-------------|
| `tentacle_name` | TEXT | Tentacle name |
| `outcome_status` | TEXT | Lifecycle status at completion (e.g., `completed`) |
| `terminal_status` | TEXT | Structured handoff status: `DONE`, `BLOCKED`, `TOO_BIG`, `AMBIGUOUS`, or `REGRESSED`; NULL when handoff has no `STATUS:` line |
| `recorded_at` | TEXT | ISO 8601 timestamp of `tentacle.py complete` |

`terminal_status` is populated by `tentacle.py complete` from the latest `STATUS:` line in
`handoff.md`. Free-form handoffs without a `STATUS:` line leave this column NULL.

### `meta.json` fields (written by `complete`)

| Field | Type | Description |
|-------|------|-------------|
| `terminal_status` | string or absent | Latest `STATUS:` value from handoff.md (only present if a valid status was found) |
| `changed_files` | string[] or absent | Deduplicated paths from all `Changed:` receipt lines in handoff.md, preserving first-seen handoff order (only present if receipts exist) |

These fields are extracted by `_parse_handoff_status` and `_parse_handoff_changed_files` in
`tentacle.py` and written into the tentacle's `meta.json` before metrics recording.

### Triage signal

`tentacle.py handoff` and `tentacle.py complete` print a triage line when `terminal_status` is a
non-`DONE` value:

```
⚠️  TRIAGE: terminal_status=BLOCKED — orchestrator review required
```

`DONE` does not produce a triage signal.

---


## Dispatched-Subagent Marker Contract


The `dispatched-subagent-active` marker at `~/.copilot/markers/dispatched-subagent-active` is a JSON file with this contract:

| Field | Description |
|-------|-------------|
| `name` | Always `"dispatched-subagent-active"` |
| `ts` | UNIX timestamp of most-recent write (used for HMAC + global TTL anchor) |
| `sig` | HMAC-SHA256 over `"name:ts"` (omitted when no secret configured) |
| `active_tentacles` | List of per-entry objects: `{"name", "ts", "git_root", "tentacle_id"}` |
| `git_root` | Top-level field: abs git root of most-recent writer (legacy path only) |
| `scope` | File-scope list from most-recently-dispatching tentacle |
| `dispatch_mode` | Dispatch mode of most-recently-dispatching tentacle |
| `ttl_seconds` | Expected lifetime; consumers treat markers older than this as stale (4 hours) |
| `written_at` | ISO 8601 human-readable timestamp |

- Deduplication key: `tentacle_id` (primary) → `(name, git_root)` fallback for legacy entries
- Per-entry TTL: each `active_tentacles` entry's `ts` is used for its own TTL check
- `tentacle.py complete <name>` removes only that tentacle's entry; marker file deleted when list is empty
- Old entries without `git_root` conservatively block all repos until TTL expires

> Full dispatched-subagent guard design: **[docs/HOOKS.md](HOOKS.md#dispatched-subagent-git-guard)**
