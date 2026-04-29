# Telemetry & Contracts

> Canonical reference for telemetry surfaces, JSON field envelopes, and API contracts.

## Recall Telemetry (Phase 5)

Recall telemetry tracks how the knowledge base is accessed — counts, IDs, and output sizes only. No payload bodies are logged.

### Commands

```bash
python3 ~/.copilot/tools/knowledge-health.py --recall         # Recall-only text dashboard
python3 ~/.copilot/tools/knowledge-health.py --recall --json  # Recall-only JSON payload
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
