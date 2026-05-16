---
name: session-knowledge
description: >-
  Use when starting a complex task, hitting an error, or making a design decision — search
  past Copilot/Claude session knowledge. Run briefing.py for relevant mistakes, patterns,
  decisions. Use query-session.py to search errors, tools, architecture choices.
  Supports semantic search with embeddings.
---

# Session Knowledge

You have access to a knowledge base built from past Copilot and Claude sessions.
Use it to avoid repeating mistakes, reuse proven patterns, and recall past decisions.

All tools are available as `sk <command>` (the preferred short form). If `sk` is not yet on PATH, use `python3 ~/.copilot/tools/<script>.py` on macOS/Linux, or `python "$env:USERPROFILE\.copilot\tools\<script>.py"` in Windows PowerShell.

## When to Use

- **Starting a complex task** → run `briefing.py` to check for relevant past experience
- **Hitting an error** → search for the error message, someone may have solved it before
- **Making a design decision** → check `--decisions` for past architectural choices
- **Unsure about a tool/config** → search for the tool name

**Skip** when the task is trivial (renaming a variable, formatting code, etc.)

## Context Budget

Always start with the lightest fetch, then escalate only when a hit is relevant:

| Task complexity | Recommended command | Approx tokens |
|----------------|---------------------|---------------|
| Trivial / session start | `briefing.py --wakeup` | ~170 |
| Moderate (bug fix, small feature) | `briefing.py --auto --compact` | ~500 |
| Complex / unfamiliar area | `briefing.py "task" --full` | ~3K |
| Drill into one entry | `query-session.py --detail <id>` | varies |

**Do not load `--full` when `--compact` shows no relevant hits.** A large briefing that
surfaces nothing useful costs tokens without benefit.

## Core Commands

### 1. Briefing (recommended first step)

```bash
sk briefing "your task description"    # Compact ~500 tokens
sk briefing "your task" --full         # Full detail ~3K tokens
sk briefing --auto                     # Auto-detect from git/plan
sk briefing --wakeup                   # Ultra-compact ~170 tokens for session start
sk briefing --titles-only              # Index only ~10 tok/entry — progressive disclosure
sk briefing --titles-only "topic"      # Filtered titles
sk briefing "task" --wing ui --room settings  # Filter by wing/room
sk briefing "task" --min-confidence 0.7       # High-quality entries only
# fallback: macOS/Linux `python3 ~/.copilot/tools/briefing.py <args>`;
# Windows PowerShell `python "$env:USERPROFILE\.copilot\tools\briefing.py" <args>`
```

Output includes: relevant mistakes to avoid, patterns to follow, related past work.
**Read entry IDs in the output** — use them to drill down.

### 1b. Sub-agent Context Injection

When dispatching tentacle agents, prefer the bundle-first structured recall path:

```bash
sk tentacle swarm <name> --briefing
# fallback: macOS/Linux `python3 ~/.copilot/tools/tentacle.py swarm <name> --briefing`;
# Windows PowerShell `python "$env:USERPROFILE\.copilot\tools\tentacle.py" swarm <name> --briefing`
```

This materializes `.octogent/tentacles/<name>/bundle/` by default, keeps the dispatch prompt
lean, and writes bounded `[KNOWLEDGE EVIDENCE]` to `briefing.md` plus machine-readable
`recall-pack.json` by running task-scoped `briefing.py --task <id> --json` first, then
`briefing.py "<query>" --pack --limit 3` only when task recall is empty.
`--task --json` exposes `tagged_entries[]` / `related_entries[]`; `--pack` keeps
`entries.<category>[]`. Drilldown may append `query-session.py --related <entry_id>`
only when the first evidence bullet has related entries.

For manual compatibility and ad hoc non-tentacle prompts, inject context directly:

```bash
sk briefing "task description" --for-subagent
# fallbacks follow the same macOS/Linux `python3 ...` and Windows PowerShell `python "$env:USERPROFILE\..."` pattern above
```

This outputs a compact `[KNOWLEDGE CONTEXT]` block (~200 tokens) designed to be
embedded directly into prompts. Example manual workflow:

1. Run `sk briefing "fix Docker networking" --for-subagent` → get context block
2. Prepend the context block to the sub-agent's prompt
3. Sub-agent now knows past mistakes/patterns without querying KB directly

### 2. Search

```bash
sk query "search terms"              # Compact results
sk query "docker error" --verbose    # Full content
sk query "deployment error" --semantic            # Compact semantic output
sk query "deployment error" --semantic --verbose  # Shows feedback bias only when non-zero
sk query "spring" --source copilot   # Filter by agent
sk query "gradle" --type research    # Filter by doc type
# fallback: macOS/Linux `python3 ~/.copilot/tools/query-session.py <args>`;
# Windows PowerShell `python "$env:USERPROFILE\.copilot\tools\query-session.py" <args>`
```

### 3. Drill Down (use entry IDs from search/briefing results)

```bash
sk query --detail <id>     # Full content of one entry
sk query --context <id>    # Entry + same-session entries
sk query --related <id>    # Entry + graph connections
# fallback: macOS/Linux `python3 ~/.copilot/tools/query-session.py <flag> <id>`;
# Windows PowerShell `python "$env:USERPROFILE\.copilot\tools\query-session.py" <flag> <id>`
```

`sk query --detail <id>` writes stateless `detail_open` telemetry:
- found entry → `hit_count=1`, `selected_entry_ids=[id]`
- missing entry → `hit_count=0`, `selected_entry_ids=[]`

### 4. Browse by Category

```bash
sk query --mistakes    # Past errors and how they were fixed
sk query --patterns    # Reusable best practices
sk query --decisions   # Architecture/design choices
sk query --tools       # Tool configs and usage notes
```

### 4b. Recall Telemetry Stats

```bash
sk index health --recall
sk index health --recall --json
# fallback: python3 ~/.copilot/tools/knowledge-health.py --recall [--json]
```

- `recall_events` is lean telemetry (counts/IDs/output size only), not verbose output logging.
- Default `sk query "query"` telemetry aggregates the full emitted surface
  (primary search + `sessions_fts` + knowledge-entry blocks).
- `--recall` outputs recall-only stats (text or JSON). No browse UI / contextual summary / provider rerank scope here.

### 5. Knowledge Graph

```bash
sk query --graph "topic"   # Visual: entries + connections
```

Shows how knowledge entries relate to each other:
- **RESOLVED_BY** — a mistake linked to the pattern/tool that fixed it
- **TAG_OVERLAP** — entries sharing similar tags (related domain)
- **SAME_SESSION** — entries discovered together in one session
- **SAME_TOPIC** — same topic tracked across multiple sessions

### 6. Record Knowledge

```bash
# 7 observation types
sk learn --mistake "Title"   "What went wrong and fix"         --tags "tag1,tag2"
sk learn --pattern "Title"   "What works well / best practice" --tags "tag1"
sk learn --decision "Title"  "Architecture decision rationale" --tags "tag1"
sk learn --tool "Title"      "Tool/config that was useful"     --tags "tag1"
sk learn --feature "Title"   "New feature implementation"      --tags "tag1"
sk learn --refactor "Title"  "Code improvement description"    --tags "tag1"
sk learn --discovery "Title" "Codebase finding or insight"     --tags "tag1"

# Structured facts (discrete, verifiable statements)
sk learn --pattern "Title" "Description" \
  --fact "max retries is 3" --fact "timeout is 30s"

# Palace categorization (wing/room)
sk learn --mistake "Title" "Description" --wing ui --room settings

# Knowledge graph relations
sk learn --relate "ScreenA" "navigates_to" "ScreenB"
sk learn --relate "ComponentX" "uses" "ThemeToken"

# Bulk import / view
sk learn --from-file notes.md    # Bulk import from markdown
sk learn --list                   # List recent entries
sk learn --stats                  # Knowledge base statistics
# fallback: macOS/Linux `python3 ~/.copilot/tools/learn.py <args>`;
# Windows PowerShell `python "$env:USERPROFILE\.copilot\tools\learn.py" <args>`
```

### 7. Auto-Update Tools

```bash
sk update              # Auto-update (24h cooldown)
sk update --force       # Force update now
sk update --status      # Show version info
sk update --doctor      # Health check
# fallback: python3 ~/.copilot/tools/auto-update-tools.py <args>
```

### 8. Optional Sync Runtime (local-first)

Use these only when sync replication is needed. Local `knowledge.db` remains primary.

```bash
# Single connection string in ~/.copilot/tools/sync-config.json
sk sync config --setup https://gateway.example.com
sk sync config --setup-env SYNC_GATEWAY_URL
sk sync config --status
sk sync config --status --json
sk sync config --get
sk sync config --clear

# Local-first runtime + diagnostics
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
# fallback: python3 ~/.copilot/tools/sync-config.py / sync-daemon.py / sync-status.py / auto-update-tools.py
```

If no `connection_string` is configured, daemon sync remains local-only/idle.
Daemon runtime is hardened for backlog catch-up: adaptive per-cycle limits, multi-page pull in one cycle, and post-pull targeted refresh of `knowledge_fts` / `ke_fts`.
`sk sync config --setup` expects an HTTP(S) gateway URL (not a raw Postgres/libSQL DSN).
`sync-gateway.py` is a **reference/mock** contract surface in this repo (not production authority).
Default provider rollout recommendation: Neon (backing Postgres) + Railway (thin gateway host), while preserving the same HTTP gateway contract.

### 9. Trend Scout operations (scheduled, not hook-driven)

```bash
sk scout run --search-only
sk scout run --dry-run --limit 1 --force
sk scout run --limit 1 --force
# fallback: python3 ~/.copilot/tools/trend-scout.py <args>
```

- Trend Scout creates **or updates** marker-linked issues.
- Veto and grace behavior come from `trend-scout-config.json` (script defaults may differ from repo-configured values).
- Keep Trend Scout out of interactive hooks (`preToolUse`/`postToolUse`) to avoid session spam.

## Interpreting Results

- **`[mistake]`** entries = things that went wrong → read carefully to avoid repeating
- **`[pattern]`** entries = proven solutions → consider applying directly
- **`[decision]`** entries = past choices with rationale → check if still valid
- **`[tool]`** entries = configurations, commands → copy-paste ready
- **`[feature]`** entries = feature implementation details → reference for similar work
- **`[refactor]`** entries = code improvements → reuse approach
- **`[discovery]`** entries = codebase insights → context for decisions
- **Confidence score** (0.3–1.0) = how reliable the entry is. Below 0.5 = verify before using.
- **Entry ID `#1234`** = use with `--detail 1234` to see full content

## Workflow Example

```
1. sk briefing "fix Docker compose networking"
   → shows 2 past mistakes about Docker DNS, 1 pattern about compose networks

2. sk query --detail 2045
   → reads the full mistake: was using wrong network driver

3. Apply the fix using the pattern from the briefing

4. sk learn --pattern "Docker DNS Fix" "Use bridge network with explicit DNS" \
     --fact "compose DNS uses service names" --wing infrastructure --room docker
```

<example>
User: "I need to add retry logic to the payment service. Where should I start?"

1. Run briefing before touching anything:
   ```
   sk briefing "add retry logic payment service" --auto --compact
   ```
   → Output surfaces a past mistake: "Exponential backoff not applied to idempotent endpoints"
   and a pattern: "Use tenacity library with max_attempts=3, wait=wait_exponential(min=1, max=10)"

2. Drill into the pattern entry shown in results:
   ```
   sk query --detail 1842
   ```
   → Full entry: exact tenacity config that worked in the order service

3. Implement retry logic using the pattern, avoiding the known mistake.

4. Record what was learned:
   ```
   sk learn --pattern "Payment retry with tenacity" \
     "Use tenacity with max_attempts=3, wait_exponential(min=1, max=10) on POST /charge" \
     --fact "idempotency key required on retry" --wing backend --room payments
   ```
</example>

<example>
User: "Getting 'SSL: CERTIFICATE_VERIFY_FAILED' on CI — has this come up before?"

1. Search for the error message:
   ```
   sk query "SSL CERTIFICATE_VERIFY_FAILED"
   ```
   → Finds a past mistake entry explaining that the corporate proxy strips certs and the fix
   was to set `REQUESTS_CA_BUNDLE` to the internal CA bundle path.

2. Apply the fix directly from the KB entry — no need to debug from scratch.

3. If it was a new variant, record it:
   ```
   sk learn --mistake "SSL verify failed behind proxy" \
     "Corporate proxy strips SSL — set REQUESTS_CA_BUNDLE=/etc/ssl/certs/ca-bundle.crt" \
     --tags "ssl,ci,proxy" --wing devops --room ci
   ```
</example>

## Semantic Search (if embeddings configured)

```bash
sk query "deployment error" --semantic
# fallback: python3 ~/.copilot/tools/query-session.py "deployment error" --semantic
```

Works with meaning, not just keywords. Requires API key setup via `embed.py --setup`.
