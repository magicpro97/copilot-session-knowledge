---
applyTo: "**/*"
---

# Session Knowledge (AUTO-LOADED)

> Auto-injected into every context. Start minimal — escalate only when needed.

## Before Starting a Task

Use the lightest fetch that covers the task complexity:

```bash
# Trivial tasks (rename, formatting, single-line fix) — skip or ultra-compact
sk briefing --wakeup          # ~170 tokens, titles only

# Moderate tasks (bug fix, small feature) — compact is usually enough
sk briefing --auto --compact  # ~500 tokens, top results

# Complex or unfamiliar tasks — request full detail only after compact reveals a hit
sk query --detail <id>  # Expand one entry by ID
sk briefing "task" --full       # Full detail ~3K tokens
# fallback: sk briefing <args> / sk query <args>
```

Read the output before acting. It surfaces past mistakes and proven patterns.

## Sub-Agent Context Injection

When dispatching tentacle agents, prefer the structured recall path in `tentacle.py`:

```bash
sk tentacle swarm <name> --briefing
# fallback: sk tentacle swarm <name> --briefing
```

This injects bounded `[KNOWLEDGE EVIDENCE]` by trying `briefing.py --task <id> --json`
first and falling back to `briefing.py "<query>" --pack --limit 3` only when task recall is empty.
`briefing.py --task --json` exposes `tagged_entries[]` and `related_entries[]`; `--pack`
keeps category buckets in `entries.<category>[]`. Drilldown may add
`query-session.py --related <entry_id>` only when the first evidence bullet has related entries.

### Full tentacle workflow

```bash
# 1. Create tentacle
sk tentacle create <name> --scope "<paths>" --desc "<desc>" --briefing --skill <skill-name>

# 2. Add todos
sk tentacle todo <name> add "<task>"

# 3. (Optional) Prepare isolated worktree + pre-warm bundle before dispatch
sk tentacle worktree <name> prepare
sk tentacle bundle <name> --worktree

# 4. Dispatch (bundle is default; --worktree surfaces isolated repo path too)
sk tentacle swarm <name> --agent-type general-purpose --model claude-sonnet-4.6 --briefing --worktree
sk tentacle dispatch <name> --briefing --worktree

# 5. Operator monitoring (read-only)
sk tentacle status

# 6. Verify, record learnings, close (orchestrator only)
sk tentacle verify <name> "python3 test_fixes.py" --label "tests"
sk tentacle handoff <name> "summary" --learn
sk tentacle complete <name>   # marks done, clears marker, auto-learns
sk tentacle worktree <name> cleanup
# fallback: sk tentacle <subcommand> <args>
```

For manual compatibility or ad hoc non-tentacle prompts, inject compact context directly:

```bash
# Manual compatibility path — compact and directly injectable
sk briefing "task description" --for-subagent
# fallback: sk briefing "task description" --for-subagent
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
sk sync config --setup <https://gateway>
sk sync config --setup-env SYNC_GATEWAY_URL
sk sync config --status
sk sync config --status --json
sk sync config --clear
sk sync config --get

# local-first runtime + diagnostics
sk sync run --once
sk sync run --daemon
sk sync run --interval 30
sk sync run --push-only
sk sync run --pull-only
sk sync status --json
sk sync status --watch-status --json
sk sync status --health-check --json
sk sync status --audit --json
sk update --restart-watch
sk update --watch-status
sk update --health-check
sk update --audit-runtime
# fallback: sk sync config / sync-daemon.py / sync-status.py / auto-update-tools.py
```

- Missing `connection_string` means local-only idle sync (not fatal).
- Runtime hardening: daemon auto-adjusts per-cycle limits on backlog, consumes multiple pull pages per cycle, and refreshes touched `knowledge_fts` / `ke_fts` rows after pull apply.
- `sync-gateway.py` is **reference/mock only** in this repo.
- `sk sync config --setup` accepts an HTTP(S) gateway URL, not a raw Postgres/libSQL DSN.
- Default provider rollout recommendation: Neon (backing Postgres) + Railway (thin gateway host), while keeping the same HTTP gateway contract.
- Browse sync status is read-only: `/healthz` → `sync_status_endpoint: "/api/sync/status"`.

## Trend Scout (automation contract)

Use Trend Scout as explicit/scheduled automation, not an interactive hook:

```bash
sk scout run --search-only
sk scout run --dry-run --limit 1 --force
sk scout run --limit 1 --force
# fallback: sk scout run <args>
```

- It creates or updates marker-linked issues in the target repo.
- Veto + grace behavior are config-driven (`trend-scout-config.json`).
- Do **not** attach Trend Scout to `preToolUse`/`postToolUse` hooks (avoid noisy per-tool spam).

## Recall Telemetry (Phase 5)

```bash
sk index health --recall
sk index health --recall --json
# fallback: sk index health --recall [--json]
```

- `recall_events` is lean telemetry only (counts/IDs/output size), not verbose output logging.
- `sk query --detail <id>` is stateless telemetry:
  - found entry → `detail_open` with `hit_count=1`, `selected_entry_ids=[id]`
  - missing entry → `detail_open` with `hit_count=0`, `selected_entry_ids=[]`
- default `sk query "query"` telemetry aggregates the full emitted search surface
  (primary search block + `sessions_fts` block + knowledge-entry block).
- If `recall_events` is unavailable on an older DB, recall commands still run (best-effort telemetry).
- Browse UI, contextual summaries, and provider rerank are outside this telemetry scope.

## After Completing Work

Record what you learned (choose the most specific type):

```bash
sk learn --mistake "Title"   "Root cause and fix"  --tags "module,tech" --wing <wing> --room <room>
sk learn --pattern "Title"   "What works well"     --tags "module,tech" --wing <wing> --room <room>
sk learn --feature "Title"   "What was built"      --tags "module,tech" --wing <wing> --room <room>
sk learn --discovery "Title" "Codebase insight"    --tags "module,tech" --wing <wing> --room <room>
# fallback: sk learn <type> <args>
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
