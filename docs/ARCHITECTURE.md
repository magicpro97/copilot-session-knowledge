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
1. `build-session-index.py` — Phase 1 (session metadata) + Phase 2 (event content) via `providers/` → SQLite FTS5 (schema v8)
2. `extract-knowledge.py` — classifies into 7 types, deduplicates by content hash, auto-detects relations
3. `query-session.py` / `briefing.py` — BM25 keyword search + optional semantic vector search (RRF blend)
4. `watch-sessions.py` — adaptive polling (5 s / 30 s / 300 s tiers), auto re-indexes on file changes
5. `learn.py` — manual knowledge entry; CLI interface for agents to record learnings during a session

## Script Inventory

| Script | Role |
|--------|------|
| `build-session-index.py` | Indexes session files → FTS5 DB |
| `extract-knowledge.py` | Classifies + deduplicates knowledge entries |
| `query-session.py` | FTS5 + semantic search; JSON/markdown export |
| `briefing.py` | Task-scoped recall; context packs for agent injection |
| `watch-sessions.py` | File watcher; triggers incremental re-indexing |
| `learn.py` | Manual knowledge entry |
| `tentacle.py` | Multi-agent orchestration (create → todo → bundle → swarm → complete) |
| `embed.py` | Optional semantic search via embedding APIs (OpenAI, Fireworks, etc.) with TF-IDF fallback |
| `claude-adapter.py` | Parses Claude Code JSONL sessions into the common DB format |
| `sync-knowledge.py` | Merges `knowledge.db` files across environments (Windows ↔ WSL) |
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
| `checkpoint-save.py` | Save named checkpoint |
| `checkpoint-restore.py` | List/restore checkpoints |
| `checkpoint-diff.py` | Diff two checkpoints |
| `browse.py` | Read-only local web UI (127.0.0.1, token auth) |
| `project-context.py` | Deterministic project-context.md generator |
| `codebase-map.py` | Repo structure snapshot (auto-refreshed at session start) |
| `trend-scout.py` | GitHub repo discovery via multi-lane search |
| `copilot-cli-healer.py` | Repairs stale Copilot CLI package state |

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

**Schema versions:** v1–v6 (legacy) → v7 (two-phase indexing + `event_offsets`) → v8 (current: `sessions_fts` contentless FTS5 + BM25). Run `python3 ~/.copilot/tools/migrate.py` to upgrade.

## `providers/` Package

`SessionProvider` ABC defines `iter_sessions()` and `iter_events_with_offset()`.  
- `CopilotProvider` — handles `.md` session checkpoints  
- `ClaudeProvider` — handles JSONL with real byte-offset seeks for Phase 2

## Tentacle Workspace

`.octogent/` stores local tentacle state and is gitignored in this repo.  
Runtime-bundle workflow: `create` → `todo add` → `bundle` (optional) → `swarm` → `complete`.

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

### CI Quality Gates

GitHub Actions runs two jobs on every push / PR:
- **`quality-gates`** — syntax check, scoped Ruff lint, and the Python test suites. The Ruff lint surface is: `embed.py`, `scout-config.py`, `scout-status.py`, `sync-config.py`, `sync-daemon.py`, `sync-status.py`, `migrate.py`, `generate-summary.py`, `briefing.py`, `learn.py`, `query-session.py`, `extract-knowledge.py`, `build-session-index.py`, `tentacle.py`, `checkpoint-diff.py`, `checkpoint-restore.py`, `checkpoint-save.py`, `browse/`, `hooks/`. Ruff lint is **scoped** to this surface; other root scripts outside it are not linted by CI.
- **`browse-ui`** — `pnpm typecheck`, `pnpm lint`, `pnpm format:check`, `pnpm test`, `pnpm build`.

### Automation Surfaces

- **Trend Scout** is scheduled/manual (`trend-scout.yml` or explicit CLI runs) — NOT bound to `preToolUse`/`postToolUse` hooks (avoid output spam during sessions). Multi-lane discovery (`lanes[]` config) and `--explain` are CLI/workflow-only features.
- **Sync browse diagnostics** are read-only: `/healthz` advertises `/api/sync/status`; `/api/sync/status` reports local queue/failure/config/cursor state only.
- **Retrospective** (`retro.py`) aggregates knowledge health, skill/tentacle outcomes, hook audit decisions, and git history into a composite operator score. The browse server exposes `GET /api/retro/summary` (defaults to `?mode=repo`; pass `?mode=local` for full multi-source data) and a minimal HTML page at `/retro`. The `retro.yml` workflow is `workflow_dispatch`-only; it runs `retro.py --mode repo --json`, writes a markdown summary artifact (including confidence, distortion flags, accuracy notes, and improvement actions when present), and appends to the job summary. No issues, commits, or DB writes are created. A collapsible `RetroSection` panel in the browse insights dashboard consumes `/api/retro/summary` and renders the richer explanation fields (`score_confidence`, `distortion_flags`, `accuracy_notes`, `improvement_actions`) when present, failing gracefully when absent or when the API is unavailable. Local mode (`?mode=local`) includes all sections but may emit `score_confidence=low` and distortion flags (e.g. `hook_deny_dry_noise`, `skills_unverified`) that indicate the score should be treated as a rough signal only; repo mode scores are typically cleaner but cover git signals only.
