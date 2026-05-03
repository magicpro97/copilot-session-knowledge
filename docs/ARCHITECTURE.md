# Architecture & Conventions

> Canonical reference for the copilot-session-knowledge architecture, data pipeline, and coding conventions.

This repo is a **set of standalone Python CLI scripts** — not a package or library. Each script is independently runnable, duplicates its own constants, and has no inter-script imports. This is intentional: the goal is operator-first simplicity, not framework cohesion.

## Data Pipeline

```
Session files (.md / .jsonl)
        │
        ▼
build-session-index.py  ──→  SQLite FTS5 (knowledge.db)
        │                             │
        ▼                             ▼
extract-knowledge.py  ──→  7 knowledge categories
        │                   (mistake, pattern, decision,
        │                    tool, feature, refactor, discovery)
        ▼
query-session.py / briefing.py  ──→  Search & recall
        │
        ▼
watch-sessions.py  ──→  Incremental re-indexing (adaptive polling)
```

**Phases:**
1. `build-session-index.py` — Phase 1 (session metadata) + Phase 2 (event content) via `providers/` → SQLite FTS5 (schema v8; current migration level v15)
2. `extract-knowledge.py` — classifies into 7 types, deduplicates by content hash, auto-detects relations; category-aware confidence floors (pattern=0.5, others=0.4) and recurrence reward (+0.03 per upsert, capped)
3. `query-session.py` / `briefing.py` — BM25 keyword search + optional semantic vector search (RRF blend)
4. `watch-sessions.py` — adaptive polling (5 s / 30 s / 300 s tiers), auto re-indexes on file changes
5. `learn.py` — manual knowledge entry; CLI interface for agents to record learnings during a session

## Script Inventory

| Script | Role |
|--------|------|
| `build-session-index.py` | Indexes session files → FTS5 DB |
| `extract-knowledge.py` | Classifies + deduplicates knowledge entries; category-aware confidence floors; recurrence reward |
| `query-session.py` | FTS5 + semantic search; JSON/markdown export |
| `briefing.py` | Task-scoped recall; context packs for agent injection |
| `watch-sessions.py` | File watcher; triggers incremental re-indexing |
| `learn.py` | Manual knowledge entry |
| `tentacle.py` | Multi-agent orchestration (create → todo → bundle → swarm → complete) |
| `embed.py` | Optional semantic search via embedding APIs (OpenAI, Fireworks, etc.) with TF-IDF fallback |
| `claude-adapter.py` | Parses Claude Code JSONL sessions into the common DB format |
| `sync-knowledge.py` | Merges `knowledge.db` files across environments (Windows ↔ WSL); MAX confidence semantics |
| `sync-config.py` | Single `connection_string` config; `--setup`, `--setup-env`, `--status --json` |
| `sync-daemon.py` | Local-first push/pull runtime; backlog-aware adaptive limits |
| `sync-status.py` | Local sync diagnostics; `--health-check`, `--audit`, `--json` |
| `auto-update-tools.py` | Smart git-diff–based update pipeline; `sk-update` alias |
| `migrate.py` | Versioned schema migrations via `schema_version` table |
| `install.py` | Deploy skills/hooks; inject global AI instructions |
| `setup-project.py` | Full project onboarding: skills + hooks + WORKFLOW.md |
| `host_manifest.py` | Single source of truth for supported hosts + their filesystem paths |
| `index-status.py` | Row counts, FTS integrity, event-offset coverage |
| `knowledge-health.py` | Knowledge base health + recall telemetry |
| `benchmark.py` | Commit-keyed benchmark ledger for retro + health snapshots |
| `checkpoint-save.py` | Save named checkpoint |
| `checkpoint-restore.py` | List/restore checkpoints |
| `checkpoint-diff.py` | Diff two checkpoints |
| `browse.py` | Local web UI (127.0.0.1, token auth) with read-only diagnostics plus the authenticated `/v2/chat` operator console |
| `project-context.py` | Deterministic project-context.md generator |
| `codebase-map.py` | Repo structure snapshot (auto-refreshed at session start) |
| `trend-scout.py` | GitHub repo discovery via multi-lane search |
| `copilot-cli-healer.py` | Repairs stale Copilot CLI package state |

## Browse UI Operator Console (`/v2/chat`)

The browse UI exposes a browser-managed Copilot CLI execution console at `/v2/chat`. It is the only browse surface that actively launches Copilot CLI; the rest of the UI remains read-only diagnostics and search.

### Components

| Component | Role |
|-----------|------|
| `browse/core/operator_console.py` | Secure execution/persistence adapter. Starts Copilot CLI runs, normalizes event streams, and persists operator state under `~/.copilot/session-state/operator-console/`. |
| `browse/api/operator.py` | Authenticated REST + SSE surface for session CRUD, prompt submission, run status/history, path suggestions, previews, and diffs. |
| `browse-ui/src/app/chat/` | Next.js route wrapper for the `/chat` operator console. |
| `browse-ui/src/components/chat/` | `ChatShell`, `Transcript`, `Composer`, `SessionCreateDialog`, `MetadataBar`, and file review components. |
| `browse-ui/src/lib/api/{types,schemas,hooks}.ts` | Stable frontend contract layer for `/api/operator/*`. |

### API surface (`/api/operator/*`)

```text
POST /api/operator/sessions                  → create session
GET  /api/operator/sessions                  → list sessions
GET  /api/operator/sessions/{id}             → session detail
POST /api/operator/sessions/{id}/prompt      → submit prompt → {run_id}
GET  /api/operator/sessions/{id}/stream      → SSE run output
GET  /api/operator/sessions/{id}/status      → session + active run status (?run=<run_id>)
GET  /api/operator/sessions/{id}/runs        → persisted run history
POST /api/operator/sessions/{id}/delete      → delete session
GET  /api/operator/suggest                   → path/workspace suggestions under ~/
GET  /api/operator/preview                   → file preview under ~/
GET  /api/operator/diff                      → unified diff for two files under ~/
```

### Guardrails

- **Workspace confinement:** every workspace or file path is normalized with `confine_path()` and rejected unless it resolves under `Path.home()`.
- **Token auth:** all `/api/operator/*` routes require the same per-launch browse token as the rest of the UI.
- **Prompt cap:** prompts longer than 4096 characters are rejected.
- **Path cap:** oversized path inputs are rejected before filesystem access.
- **Separate persistence:** operator run history is stored under `~/.copilot/session-state/operator-console/` and replayed from disk on reload.
- **Same Copilot policy surface:** operator-console runs still inherit the installed Copilot CLI's hooks, custom instructions, and permission system. Browser mediation does not bypass briefing, tentacle, or other active policy gates.

### Compatibility with watch-sessions and auto-update

- `watch-sessions.py` still tracks normal Copilot session artifacts under `~/.copilot/session-state/`; the operator console reads its own persisted run history directly from `operator-console/`.
- `auto-update-tools.py` can restart `watch-sessions.py`, but it does not restart the browse server or interfere with active operator runs.
- UI-only `browse-ui/dist/` updates are served from disk after rebuild/deploy; Python changes to `browse/api/operator.py` or `browse/core/operator_console.py` still require a manual browse server restart.

## Enforcement Hooks

Hooks live in `hooks/` and are deployed to `~/.copilot/hooks/` (Copilot CLI only).

- **Unified runner:** `hook_runner.py` dispatches all hook events (1 Python process per event)
- **Supported events:** `sessionStart`, `sessionEnd`, `preToolUse`, `postToolUse`, `agentStop`, `subagentStop`, `errorOccurred`
- **Fail-open:** rule errors never block the agent
- **HMAC-signed markers:** tamper-resistant counter state
- **Audit log:** `~/.copilot/markers/audit.jsonl`

> Full hook architecture, rule inventory, and dispatched-subagent git guard: **[docs/HOOKS.md](HOOKS.md)**

## Central Database

`~/.copilot/session-state/knowledge.db` — SQLite with FTS5, WAL journal mode, and optional vector embeddings.

**Schema versions:** v1–v6 (legacy) → v7 (two-phase indexing + `event_offsets`) → v8 (`sessions_fts` contentless FTS5 + BM25) → v9–v14 (eval, provenance, recall, sync, benchmark) → v15 (`confidence_backfill_wave3`: raises pattern confidence floor to 0.5 and applies recurrence reward to existing entries). Run `python3 ~/.copilot/tools/migrate.py` to upgrade.

## `providers/` Package

`SessionProvider` ABC defines `iter_sessions()` and `iter_events_with_offset()`.  
- `CopilotProvider` — handles `.md` session checkpoints  
- `ClaudeProvider` — handles JSONL with real byte-offset seeks for Phase 2

## Tentacle Workspace

`.octogent/` stores local tentacle state and is gitignored in this repo.  
Runtime-bundle workflow: `create` → `todo add` → `bundle` (optional) → `swarm` → `complete`.

`complete` accepts an optional `--auto-verify <cmd>` flag (fail-open): runs the command, persists the result as verification evidence before closing. Use `--auto-verify-timeout <seconds>` (default: 120 s) if the command is long-running.

Sub-agents **must** write a structured handoff before stopping:
```
tentacle.py handoff <name> "<summary>" --status DONE --changed-file <path> [--changed-file ...] --learn
```
`--status` must be one of `DONE`, `BLOCKED`, `TOO_BIG`, `AMBIGUOUS`, or `REGRESSED`. Include one `--changed-file` receipt per modified file so the orchestrator can verify the handoff trail.

`tentacle.py marker-cleanup` (dry-run by default, `--apply` to act) inspects and removes stale
entries from the dispatched-subagent marker without completing a tentacle. Only entries whose
per-entry timestamp exceeds the declared TTL are eligible; live entries are never touched.

> Full tentacle workflow reference: **[docs/USAGE.md](USAGE.md)**

## Host Scope

Tools are validated on **Copilot CLI** and **Claude Code** only.

| Feature | Copilot CLI | Claude Code |
|---------|------------|-------------|
| Skill deployment | ✅ `.github/skills/` | ✅ `.claude/skills/` |
| Hook deployment | ✅ `.copilot/hooks/` | ❌ not supported |
| Global instruction injection | ✅ `~/.github/copilot-instructions.md` | via CLAUDE.md |
| Session indexing | ✅ | ✅ via `claude-adapter.py` |

`host_manifest.py` is the single authoritative source for supported hosts and their filesystem paths. Do **not** add Codex, Cursor, or other hosts without documented session and hook formats.

> Full skill deployment and host scope details: **[docs/SKILLS.md](SKILLS.md)**

## Sync Architecture

Sync is **local-first**: `knowledge.db` is the authoritative read/query source; remote sync is optional transport/storage only.

- Single config key: `connection_string` in `~/.copilot/tools/sync-config.json`
- `sync-config.py --setup` accepts HTTP(S) gateway URLs only (not raw Postgres/libSQL DSNs)
- `sync-gateway.py` is **reference/mock only** — not a production authority
- Default provider recommendation: Neon (backing Postgres) + Railway (thin gateway host)
- Missing `connection_string` → daemon stays local-only/idle (not a fatal error)

> Full sync diagnostics reference: **[docs/USAGE.md](USAGE.md#sync-rollout)**

---

## Conventions

These conventions apply to all scripts in this repo. Follow them in every change.

### Language & Runtime

- **Pure stdlib Python 3.10+** — zero pip dependencies required. `scikit-learn` and embedding API keys are optional.
- **Every script is standalone** — no shared library or package imports between scripts. Each script duplicates its own DB path constants, encoding fix, etc.
- **Windows encoding fix** — every script starts with the same `os.name == "nt"` block to reconfigure stdout/stderr to UTF-8. Preserve this pattern in every new script.

### SQL Safety

- **Parameterized SQL only** — all user input uses `?` placeholders. Never interpolate strings into SQL.
- **FTS5 query sanitization** — strip FTS5 operators (`OR`, `AND`, `NOT`, `NEAR`, `*`, `"`) before passing to `MATCH`. See `_sanitize_fts_query()` in `query-session.py`.

### Serialization & Locking

- **JSON serialization only** — never use pickle. Legacy pickle detection exists but new code must use JSON / `struct.pack`.
- **Atomic lock files** — use `O_CREAT | O_EXCL` for process locks (no TOCTOU races).

### Input Limits

- Title ≤ 200 chars
- Content ≤ 10 K chars
- FTS queries ≤ 500 chars
- Paths ≤ 256 chars

### Cross-Platform Paths

- Use `Path.home()` and `pathlib` throughout. Handle WSL path differences explicitly.

### JSON Field Envelopes (stable contracts)

- `query-session.py --task --export json` → `entries[]`
- `briefing.py --task --json` → `tagged_entries[]` / `related_entries[]`
- `briefing.py --pack` → `entries.<category>[]`
- `snippet_freshness` values: `fresh | drifted | missing | unknown`
- `related_entry_ids` — JSON ints, confidence-ranked, capped to top 3

### DB Migrations

Add new migrations to the `MIGRATIONS` list in `migrate.py` with incrementing version numbers and a descriptive name.

### Script Guards

- **`if __name__ == "__main__":`** — `migrate.py` and `generate-summary.py` are both guarded; they can be imported without side effects. New scripts that may be imported or tested should follow this pattern.
- **`TOOLS_DIR` resolution** — root scripts that need a reliable tools-directory path use `Path(__file__).resolve().parent`. `hooks/rules/common.py` intentionally keeps the installed-hook `Path.home() / ".copilot" / "tools"` form (hooks run from `~/.copilot/hooks/`, not the source tree).

### Docs Quality Gates

Agent-authored docs and operator/research outputs (tentacle handoffs, retro summaries, knowledge-health reports) must follow the four-layer QA rubric defined in [docs/AGENT-RULES.md](AGENT-RULES.md#rule-7--docs-output-quality): facts, interpretation, actions, and verification evidence are kept distinct. Contributor-facing docs (`CONTRIBUTING.md`) use the existing concise tone and are not in scope for this rubric.

### CI Quality Gates

GitHub Actions runs two jobs on every push / PR:
- **`quality-gates`** — syntax check, scoped Ruff lint, and the Python test suites. The Ruff lint surface is: `embed.py`, `scout-config.py`, `scout-status.py`, `sync-config.py`, `sync-daemon.py`, `sync-status.py`, `migrate.py`, `generate-summary.py`, `briefing.py`, `learn.py`, `query-session.py`, `extract-knowledge.py`, `build-session-index.py`, `tentacle.py`, `checkpoint-diff.py`, `checkpoint-restore.py`, `checkpoint-save.py`, `browse/`, `hooks/`. Ruff lint is **scoped** to this surface; other root scripts outside it are not linted by CI.
- **`browse-ui`** — `pnpm typecheck`, `pnpm lint`, `pnpm format:check`, `pnpm test`, `pnpm build`.

Playwright E2E runs are manual-dispatch only. The stable `behavioral` project covers `smoke.spec.ts`, `shortcuts.spec.ts`, and `chat.spec.ts`; `visual.spec.ts` remains outside always-on CI because screenshot output differs across platforms.

### Automation Surfaces

- **Trend Scout** is scheduled/manual (`trend-scout.yml` or explicit CLI runs) — NOT bound to `preToolUse`/`postToolUse` hooks (avoid output spam during sessions). Multi-lane discovery (`lanes[]` config) and `--explain` are CLI/workflow-only features.
- **Sync browse diagnostics** are read-only: `/healthz` advertises `/api/sync/status`; `/api/sync/status` reports local queue/failure/config/cursor state only.
- **Retrospective** (`retro.py`) aggregates knowledge health, skill/tentacle outcomes, hook audit decisions, and git history into a composite operator score. The browse server exposes `GET /api/retro/summary` (defaults to `?mode=repo`; pass `?mode=local` for full multi-source data) and a minimal HTML page at `/retro`. The `retro.yml` workflow is `workflow_dispatch`-only; it runs `retro.py --mode repo --json`, writes a markdown summary artifact (including confidence, distortion flags, accuracy notes, and improvement actions when present), and appends to the job summary. No issues, commits, or DB writes are created. A collapsible `RetroSection` panel in the browse insights dashboard consumes `/api/retro/summary` and renders the richer explanation fields (`score_confidence`, `distortion_flags`, `accuracy_notes`, `improvement_actions`, `scout`, `toward_100`) when present, failing gracefully when absent or when the API is unavailable. Local mode (`?mode=local`) includes all sections (including `behavior`) but may emit `score_confidence=low` and distortion flags (e.g. `hook_deny_dry_noise`, `skills_unverified`) that indicate the score should be treated as a rough signal only; repo mode scores are typically cleaner but cover git signals only. The optional top-level `scout` object provides read-only Trend Scout coverage health without affecting the composite score. The optional top-level `toward_100` array is an additive diagnostic: a list of sections scoring below 100, each with `section`, `score`, `gap` (100 − score), and metric-derived `barriers`; it explains the current gap but does **not** change the score formula or any subscore. `benchmark.py` stores commit-keyed snapshots and exposes `retro_gap`/`health_gap` gap-to-target fields in compare output so measurable progress is explicit.
