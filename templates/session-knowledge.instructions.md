---
applyTo: "**/*"
---

# Session Knowledge (AUTO-LOADED)

> Auto-injected into every context. Start minimal — escalate only when needed.

## Before Starting a Task

Use the lightest fetch that covers the task complexity:

```bash
# Trivial tasks (rename, formatting, single-line fix) — skip or ultra-compact
python3 ~/.copilot/tools/briefing.py --wakeup          # ~170 tokens, titles only

# Moderate tasks (bug fix, small feature) — compact is usually enough
python3 ~/.copilot/tools/briefing.py --auto --compact  # ~500 tokens, top results

# Complex or unfamiliar tasks — request full detail only after compact reveals a hit
python3 ~/.copilot/tools/query-session.py --detail <id>  # Expand one entry by ID
python3 ~/.copilot/tools/briefing.py "task" --full       # Full detail ~3K tokens
```

Read the output before acting. It surfaces past mistakes and proven patterns.

## Sub-Agent Context Injection

When dispatching tentacle agents, prefer the structured recall path in `tentacle.py`:

```bash
python3 ~/.copilot/tools/tentacle.py swarm <name> --briefing
```

This injects bounded `[KNOWLEDGE EVIDENCE]` by trying `briefing.py --task <id> --json`
first and falling back to `briefing.py "<query>" --pack --limit 3` only when task recall is empty.
`briefing.py --task --json` exposes `tagged_entries[]` and `related_entries[]`; `--pack`
keeps category buckets in `entries.<category>[]`. Drilldown may add
`query-session.py --related <entry_id>` only when the first evidence bullet has related entries.

For manual compatibility or ad hoc non-tentacle prompts, inject compact context directly:

```bash
# Manual compatibility path — compact and directly injectable
python3 ~/.copilot/tools/briefing.py "task description" --for-subagent
```

Include output verbatim in the sub-agent prompt under a `## Past Knowledge` section.
Do **not** use `--full` for sub-agent injection; it bloats the prompt unnecessarily.

## Progressive Escalation

Start with `--compact` or `--wakeup`. Escalate to `--full` or `--detail <id>` only
when the compact output shows a directly relevant past mistake or decision.
This keeps context lean — escalating to full detail for every task defeats the purpose.

## Sync Rollout (Optional, Local-First)

When sync is configured, keep guidance aligned to shipped behavior:

```bash
# one connection_string in ~/.copilot/tools/sync-config.json
python3 ~/.copilot/tools/sync-config.py --setup <https://gateway>
python3 ~/.copilot/tools/sync-config.py --setup-env SYNC_GATEWAY_URL
python3 ~/.copilot/tools/sync-config.py --status
python3 ~/.copilot/tools/sync-config.py --status --json
python3 ~/.copilot/tools/sync-config.py --clear
python3 ~/.copilot/tools/sync-config.py --get

# local-first runtime + diagnostics
python3 ~/.copilot/tools/sync-daemon.py --once
python3 ~/.copilot/tools/sync-daemon.py --daemon
python3 ~/.copilot/tools/sync-daemon.py --interval 30
python3 ~/.copilot/tools/sync-daemon.py --push-only
python3 ~/.copilot/tools/sync-daemon.py --pull-only
python3 ~/.copilot/tools/sync-status.py --json
python3 ~/.copilot/tools/sync-status.py --watch-status --json
python3 ~/.copilot/tools/sync-status.py --health-check --json
python3 ~/.copilot/tools/sync-status.py --audit --json
python3 ~/.copilot/tools/auto-update-tools.py --restart-watch
python3 ~/.copilot/tools/auto-update-tools.py --watch-status
python3 ~/.copilot/tools/auto-update-tools.py --health-check
python3 ~/.copilot/tools/auto-update-tools.py --audit-runtime
```

- Missing `connection_string` means local-only idle sync (not fatal).
- Runtime hardening: daemon auto-adjusts per-cycle limits on backlog, consumes multiple pull pages per cycle, and refreshes touched `knowledge_fts` / `ke_fts` rows after pull apply.
- `sync-gateway.py` is **reference/mock only** in this repo.
- `sync-config.py --setup` accepts an HTTP(S) gateway URL, not a raw Postgres/libSQL DSN.
- Default provider rollout recommendation: Neon (backing Postgres) + Railway (thin gateway host), while keeping the same HTTP gateway contract.
- Browse sync status is read-only: `/healthz` → `sync_status_endpoint: "/api/sync/status"`.

## Recall Telemetry (Phase 5)

```bash
python3 ~/.copilot/tools/knowledge-health.py --recall
python3 ~/.copilot/tools/knowledge-health.py --recall --json
```

- `recall_events` is lean telemetry only (counts/IDs/output size), not verbose output logging.
- `query-session.py --detail <id>` is stateless telemetry:
  - found entry → `detail_open` with `hit_count=1`, `selected_entry_ids=[id]`
  - missing entry → `detail_open` with `hit_count=0`, `selected_entry_ids=[]`
- default `query-session.py "query"` telemetry aggregates the full emitted search surface
  (primary search block + `sessions_fts` block + knowledge-entry block).
- If `recall_events` is unavailable on an older DB, recall commands still run (best-effort telemetry).
- Browse UI, contextual summaries, and provider rerank are outside this telemetry scope.

## After Completing Work

Record what you learned (choose the most specific type):

```bash
python3 ~/.copilot/tools/learn.py --mistake "Title"   "Root cause and fix"  --tags "module,tech" --wing <wing> --room <room>
python3 ~/.copilot/tools/learn.py --pattern "Title"   "What works well"     --tags "module,tech" --wing <wing> --room <room>
python3 ~/.copilot/tools/learn.py --feature "Title"   "What was built"      --tags "module,tech" --wing <wing> --room <room>
python3 ~/.copilot/tools/learn.py --discovery "Title" "Codebase insight"    --tags "module,tech" --wing <wing> --room <room>
```

## Rules

- ❌ NEVER skip briefing before a non-trivial task
- ❌ NEVER skip learn after fixing a non-trivial bug
- ❌ NEVER load `--full` briefing when `--compact` shows no relevant hits
- ✅ Start minimal; escalate to `--detail <id>` for specific entries only
- ✅ Keep learn entries concise (1–3 sentences)
- ✅ Use `--wing` / `--room` to organise entries by domain

## Avoiding Context Bloat

If session-knowledge instructions are already installed at user/global level
(`~/.github/instructions/session-knowledge.instructions.md`), remove the project-level
copy to avoid loading these rules twice. One always-loaded copy is sufficient.
