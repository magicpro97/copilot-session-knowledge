# Usage Guide

> Full command reference for all copilot-session-knowledge tools.

## Briefing

Run before every major task to surface past mistakes and relevant knowledge:

```bash
# macOS/Linux: python3 | Windows: python or py
brief "implement user CRUD"          # Compact ~500 tokens
brief "implement user CRUD" --full   # Full detail ~3K tokens
brief --auto                         # Auto-detect from git state
brief --wakeup                       # Ultra-compact (~170 tokens) for session start
brief --titles-only                  # Index only (~10 tok/entry) — progressive disclosure
brief --titles-only "DynamoDB"       # Filtered titles
brief --wing backend --room patient  # Filter by wing/room (palace-style)
brief "task" --for-subagent --budget 3000  # Manual compatibility path for ad hoc sub-agent injection
brief "task" --min-confidence 0.7    # High-quality entries only
brief "task" --for-subagent          # Manual compatibility context block for sub-agent prompts
brief "task" --compact               # XML compact block for AI context injection
brief --task "memory-surface"        # Task-scoped recall: entries tagged with this task ID
brief --task "memory-surface" --json # Includes source_document + code-location/snippet fields
brief "fix Docker" --json            # JSON output for programmatic use
```

Token-distillation flags: `--compact` produces an XML compact block; `--budget N` hard-caps output to N characters (frozen snapshot, highest-confidence entries first); `--titles-only` gives ~10 tok/entry for progressive disclosure.

For tentacle delegation, prefer `tentacle.py ... --briefing`: it injects bounded `[KNOWLEDGE EVIDENCE]` from task-scoped JSON recall first, then `--pack` fallback when task recall is empty. Bullets stay unchanged; runtime may add one optional bounded `From:` provenance line. Keep `--for-subagent` for manual compatibility and ad hoc prompts.

## Search

```bash
qs "search terms"                    # Compact results
qs "search terms" --verbose          # Full content
qs "docker" --type research          # Filter by doc type
qs "search" --budget 2000            # Cap output to 2000 chars
qs "search" --compact                # Titles-only with ~token hint
qs "spring" --source copilot         # Filter by agent source
qs --mistakes                        # View past errors
qs --patterns                        # View best practices
qs --decisions                       # View architecture decisions
qs --file src/auth.py                # Entries that touched a specific file
qs --module auth                     # Entries for a module or directory
qs --task memory-surface             # Entries tagged with a specific task ID
qs --task memory-surface --export json   # JSON object with entries[] (includes snippet_freshness + related_entry_ids)
qs --diff                            # Entries for files in the current git diff
qs "search" --export json            # Export results as JSON
qs "search" --export markdown        # Export results as Markdown
```

Default `qs "query"` telemetry records the full emitted search surface (primary block + `sessions_fts` block + knowledge-entry block), not just the first printed block.

## Drill Down

Use entry ID from search results:

```bash
qs --detail 2045                     # View full entry details (+ Snippet freshness: fresh|drifted|missing|unknown)
qs --context 2045                    # Entry + entries from same session
qs --related 2045                    # Entry + knowledge graph connections
qs --graph "spring boot"             # Mini knowledge graph by topic
```

`qs --detail <id>` writes a stateless `detail_open` telemetry row:
- existing ID → `hit_count=1`, `selected_entry_ids=[id]`
- missing ID → `hit_count=0`, `selected_entry_ids=[]`

## Recall Telemetry Stats

```bash
python3 ~/.copilot/tools/knowledge-health.py --recall         # Recall-only text dashboard
python3 ~/.copilot/tools/knowledge-health.py --recall --json  # Recall-only JSON payload
```

- `--recall` output is recall-only (it does not append the default health dashboard).
- `recall_events` is lean telemetry only (IDs/counts/output size), not verbose payload logging.
- If `recall_events` is absent (older schema), recall commands still work and stats report unavailable/empty.
- Browse UI, contextual summaries, and provider rerank are out of scope for this telemetry surface.

## Semantic Search

Requires an embedding API key (optional):

```bash
qs "deployment error" --semantic     # Search by meaning (compact output; no feedback fragment)
qs "deployment error" --semantic --verbose  # Adds feedback bias fragment only when non-zero
python3 ~/.copilot/tools/embed.py --setup   # Setup API key
```

## Sync Rollout (Local-First, Optional)

Sync is local-first: `~/.copilot/session-state/knowledge.db` stays primary for reads/search.
Remote sync is optional replication transport, not the query authority.

### Configure sync endpoint (single connection string)

`sync-config.py` stores one `connection_string` in `~/.copilot/tools/sync-config.json` (HTTP(S) gateway URL, not raw Postgres/libSQL DSN).

```bash
python3 ~/.copilot/tools/sync-config.py --setup https://gateway.example.com
python3 ~/.copilot/tools/sync-config.py --setup-env SYNC_GATEWAY_URL
python3 ~/.copilot/tools/sync-config.py --status
python3 ~/.copilot/tools/sync-config.py --status --json
python3 ~/.copilot/tools/sync-config.py --get
python3 ~/.copilot/tools/sync-config.py --clear
```

### Run sync runtime

```bash
python3 ~/.copilot/tools/sync-daemon.py --once
python3 ~/.copilot/tools/sync-daemon.py --daemon
python3 ~/.copilot/tools/sync-daemon.py --interval 30
python3 ~/.copilot/tools/sync-daemon.py --push-only
python3 ~/.copilot/tools/sync-daemon.py --pull-only
```

If `connection_string` is unset, daemon mode remains local-only (idle/no-op for remote sync).
When backlog is large, daemon applies adaptive per-cycle limits automatically, paginates pull within one cycle, and refreshes local `knowledge_fts` / `ke_fts` rows touched by pulled canonical changes.

### Inspect sync status

```bash
python3 ~/.copilot/tools/sync-status.py
python3 ~/.copilot/tools/sync-status.py --json
python3 ~/.copilot/tools/sync-status.py --watch-status --json
python3 ~/.copilot/tools/sync-status.py --health-check --json   # exit 0/2
python3 ~/.copilot/tools/sync-status.py --audit --json          # exit 0/2
python3 ~/.copilot/tools/auto-update-tools.py --restart-watch
python3 ~/.copilot/tools/auto-update-tools.py --watch-status
python3 ~/.copilot/tools/auto-update-tools.py --health-check
python3 ~/.copilot/tools/auto-update-tools.py --audit-runtime
```

### Browse diagnostics surfaces (read-only)

- `GET /healthz` includes: `sync_status_endpoint: "/api/sync/status"`
- `GET /api/sync/status` reports local sync diagnostics (configured endpoint preview, queue/failure counts, cursor and replica state)

### Reference/mock gateway

`sync-gateway.py` is intentionally **reference/mock only** in this repo.
For provider-backed rollout, the default recommendation is Neon (backing Postgres) + Railway (thin gateway host) while keeping the same HTTP gateway contract.

```bash
python3 ~/.copilot/tools/sync-gateway.py --host 127.0.0.1 --port 8765
```

Endpoints: `/sync/push`, `/sync/pull`, `/healthz`.

## Record Knowledge (learn.py)

```bash
# 7 observation types
learn --mistake "Title"   "What went wrong and fix"         --tags "docker,compose"
learn --pattern "Title"   "What works well / best practice" --tags "lambda"
learn --decision "Title"  "Architecture decision rationale" --tags "cdk"
learn --tool "Title"      "Useful tool/config details"      --tags "vscode"
learn --feature "Title"   "New feature implementation"      --tags "api"
learn --refactor "Title"  "Code improvement description"    --tags "cleanup"
learn --discovery "Title" "Codebase finding or insight"     --tags "dynamodb"

# Tag entry with a task ID and affected files (for task-scoped recall)
learn --mistake "Title" "Description" --task "memory-surface" --file "briefing.py" --file "learn.py"

# Attach a concrete code location (path:line or path:start-end)
learn --pattern "FTS sanitizer fix" "Strip operators before MATCH" --code-location "query-session.py:120-142"

# Structured facts (discrete, verifiable statements)
learn --pattern "DynamoDB Batch Ops" "How to use batch writes" \
  --fact "batch write limit is 25 items" \
  --fact "GSI eventually consistent"

# Palace categorization
learn --mistake "Auth bug" "Description" --wing backend --room auth

# Knowledge graph relations
learn --relate "copyToGroup" "reads_from" "patient-dynamic-form.json"
learn --relate "addPatient Lambda" "writes_to" "dataTable"

# Bulk import
learn --from-file notes.md  # Format: ## category: Title

# View
learn --list               # Recent entries
learn --stats              # Knowledge base statistics

# JSON output (machine-readable; emits JSON object with id, title, category, tags, etc.)
learn --mistake "Title" "Description" --json
```

## Palace Concepts (Wing/Room)

Organize knowledge hierarchically:

| Wing | Description | Example Rooms |
|------|-------------|---------------|
| `backend` | Lambda, DynamoDB, SQS, API | patient, websocket, auth, dynamodb |
| `frontend` | Expo, React Native, screens | navigation, components, hooks |
| `testing` | Jest, Playwright, E2E | e2e, unit-test |
| `infrastructure` | CDK, VPC, CloudWatch | cdk, vpc, cloudwatch |
| `devops` | Git, CI/CD, Docker | git, pipeline, proxy |
| `shared` | TypeScript, ESLint, i18n | typescript, openapi |

Wings and rooms are **auto-detected** from tags/title. Override with `--wing`/`--room`.

## Codebase Map

`codebase-map.py` generates a structural snapshot of the current project (file tree, key modules) and writes it to the session `files/` directory.

```bash
python3 ~/.copilot/tools/codebase-map.py            # Refresh codebase map for current project
```

The map is **automatically refreshed at session start** by `hooks/auto-briefing.py` — no manual step needed during normal workflow.

## Checkpoint Save

`checkpoint-save.py` writes structured checkpoint files to `~/.copilot/session-state/<session>/checkpoints/`. Checkpoints are **never auto-written** — the agent must call this explicitly.

```bash
python3 ~/.copilot/tools/checkpoint-save.py \
  --title "Implemented auth module" \
  --overview "Added JWT login/logout" \
  --next_steps "Add refresh token support"

python3 ~/.copilot/tools/checkpoint-save.py --list   # List checkpoints for current session
python3 ~/.copilot/tools/checkpoint-save.py --dry-run --title "Test" --overview "Preview only"
```

> **Session-end reminder (opt-in):** `hooks/session-end.py` is reminder-only — it never writes checkpoints automatically. Set `COPILOT_CHECKPOINT_REMIND=1` in your environment to log a reminder when a session ends without a saved checkpoint.

## Checkpoint Restore (read-only)

`checkpoint-restore.py` reads and displays checkpoints written by `checkpoint-save.py`. All operations are **read-only** — no session state is mutated.

```bash
python3 ~/.copilot/tools/checkpoint-restore.py --list                      # List all checkpoints
python3 ~/.copilot/tools/checkpoint-restore.py --show latest               # Show most recent
python3 ~/.copilot/tools/checkpoint-restore.py --show 1                    # Show by sequence number
python3 ~/.copilot/tools/checkpoint-restore.py --export latest             # Export as text (default)
python3 ~/.copilot/tools/checkpoint-restore.py --export latest --format md    # Markdown (indexer-compatible)
python3 ~/.copilot/tools/checkpoint-restore.py --export latest --format json  # Machine-readable JSON
python3 ~/.copilot/tools/checkpoint-restore.py --session SESSION_ID        # Specify a session
```

Selectors for `--show` / `--export`: `N` (sequence number), `latest`, `first`.

## Checkpoint Diff

`checkpoint-diff.py` compares two checkpoints and shows what changed. All operations are **read-only**.

```bash
python3 ~/.copilot/tools/checkpoint-diff.py --from 1 --to latest          # Diff checkpoint 1 vs latest
python3 ~/.copilot/tools/checkpoint-diff.py --from 2 --to 3               # Diff two specific checkpoints
python3 ~/.copilot/tools/checkpoint-diff.py --consecutive                  # Diff all consecutive pairs
python3 ~/.copilot/tools/checkpoint-diff.py --summary                      # Change progression across all
python3 ~/.copilot/tools/checkpoint-diff.py --show-unchanged               # Include unchanged sections
python3 ~/.copilot/tools/checkpoint-diff.py --session SESSION_ID           # Specify a session
```

## Profile Builder

`profile-builder.py` creates custom workflow profiles (saved to `presets/`) that can then be deployed via `setup-project.py --profile <name>` or `install-project-hooks.py --profile <name>`.

```bash
python3 ~/.copilot/tools/profile-builder.py --list-hooks                          # List available hook templates
python3 ~/.copilot/tools/profile-builder.py --list-phases                         # List available workflow phases
python3 ~/.copilot/tools/profile-builder.py \
  --name myteam \
  --description "My team workflow" \
  --hooks dangerous-blocker.sh secret-detector.sh commit-gate.sh \
  --phases CLARIFY BUILD TEST COMMIT                                               # Create a profile
python3 ~/.copilot/tools/profile-builder.py --name myteam ... --dry-run           # Preview JSON without writing
python3 ~/.copilot/tools/profile-builder.py --name myteam ... --force             # Overwrite existing profile
```

## Profile Export

`profile-export.py` exports profiles from `presets/` to portable JSON files for sharing or backup.

```bash
python3 ~/.copilot/tools/profile-export.py --profile python --output python.json          # Export single profile
python3 ~/.copilot/tools/profile-export.py --profile python --output p.bundle.json --format bundle  # With metadata wrapper
python3 ~/.copilot/tools/profile-export.py --all --output-dir ./exported/                 # Export all profiles
python3 ~/.copilot/tools/profile-export.py --all --output all.bundle.json --format bundle # All in one bundle
python3 ~/.copilot/tools/profile-export.py --profile python --dry-run                     # Preview without writing
```

## Profile Import

`profile-import.py` imports profiles exported by `profile-export.py` back into `presets/`.

```bash
python3 ~/.copilot/tools/profile-import.py --file custom-profile.json                     # Import a profile
python3 ~/.copilot/tools/profile-import.py --file all-profiles.bundle.json                # Import bundle
python3 ~/.copilot/tools/profile-import.py --file bundle.json --name python               # Import one from bundle
python3 ~/.copilot/tools/profile-import.py --file custom.json --force                     # Overwrite existing
python3 ~/.copilot/tools/profile-import.py --file custom.json --dry-run                   # Validate without writing
```


## Tentacle Orchestration

Full workflow for multi-agent parallel execution across scoped work units.

### Step-by-step workflow

```bash
# 1. Create tentacle with scope + briefing
python3 ~/.copilot/tools/tentacle.py create api-export \
  --scope "src/api/*.py" --desc "Export API endpoints" --briefing

# 2. Add atomic todo items (one per sub-agent delegation unit)
python3 ~/.copilot/tools/tentacle.py todo api-export add "Generate OpenAPI schema"
python3 ~/.copilot/tools/tentacle.py todo api-export add "Add auth middleware"

# 3. (Optional) Pre-materialize isolated context bundle before dispatch
#    Writes briefing.md, instructions.md, skills.md, session-metadata.md, manifest.json
#    to .octogent/tentacles/<name>/bundle/ for sub-agents that need artifacts on disk
python3 ~/.copilot/tools/tentacle.py bundle api-export

# 4. Dispatch — use --bundle to surface the bundle path in the prompt
python3 ~/.copilot/tools/tentacle.py swarm api-export \
  --agent-type general-purpose --model claude-sonnet-4.6 --briefing      # single prompt
python3 ~/.copilot/tools/tentacle.py swarm api-export --output parallel  # one per todo
python3 ~/.copilot/tools/tentacle.py dispatch api-export --briefing --bundle  # single dispatch + bundle ref

# 5. Monitor runtime (read-only)
python3 ~/.copilot/tools/tentacle.py status                # dashboard: all tentacles + states

# 6. Verify results, record learnings, and close (orchestrator only)
python3 ~/.copilot/tools/tentacle.py handoff api-export "Completed. Patterns learned." --learn
python3 ~/.copilot/tools/tentacle.py complete api-export   # marks done, auto-learns from handoff
```

### Operator status

```bash
python3 ~/.copilot/tools/tentacle.py create api-export \
  --scope "backend/lambda/export*" --desc "Export API" --briefing \
  --skill karpathy-guidelines --skill code-reviewer
python3 ~/.copilot/tools/tentacle.py status       # Dashboard: name, status, todos done/total, last update
python3 ~/.copilot/tools/tentacle.py show api-export   # Full details for one tentacle
python3 ~/.copilot/tools/tentacle.py list              # One-line list of all tentacles
```

### Worktree + verification runtime commands

```bash
python3 ~/.copilot/tools/tentacle.py worktree api-export prepare
python3 ~/.copilot/tools/tentacle.py worktree api-export status
python3 ~/.copilot/tools/tentacle.py swarm api-export --worktree
python3 ~/.copilot/tools/tentacle.py dispatch api-export --worktree
python3 ~/.copilot/tools/tentacle.py verify api-export "python3 test_fixes.py" --label "tests"
python3 ~/.copilot/tools/tentacle.py worktree api-export cleanup
```

### Operator runtime (auto-update + watch)

```bash
python3 ~/.copilot/tools/auto-update-tools.py --restart-watch   # Restart session watcher
python3 ~/.copilot/tools/auto-update-tools.py --watch-status    # Check watcher state
python3 ~/.copilot/tools/auto-update-tools.py --health-check    # Runtime health
python3 ~/.copilot/tools/auto-update-tools.py --audit-runtime   # Audit active runtime surfaces
```

### Bundle for isolated context

`tentacle.py bundle` materializes a per-run context bundle — all artifacts needed by a
sub-agent written to `.octogent/tentacles/<name>/bundle/`. Run before dispatch when sub-agents
need file-backed context (rather than only inline prompt injection).

```bash
python3 ~/.copilot/tools/tentacle.py bundle api-export               # Fetch briefing + write all artifacts
python3 ~/.copilot/tools/tentacle.py bundle api-export --no-briefing    # Skip live briefing
python3 ~/.copilot/tools/tentacle.py bundle api-export --no-checkpoint  # Skip checkpoint context
python3 ~/.copilot/tools/tentacle.py bundle api-export --output json    # JSON manifest + bundle_path
```

Pass `--bundle` to `swarm` or `dispatch` to materialize the bundle and surface its path in the
prompt so sub-agents know where to find it:

```bash
python3 ~/.copilot/tools/tentacle.py swarm api-export --bundle
python3 ~/.copilot/tools/tentacle.py dispatch api-export --bundle
python3 ~/.copilot/tools/tentacle.py swarm api-export --bundle --worktree
python3 ~/.copilot/tools/tentacle.py dispatch api-export --bundle --worktree
```

### Completing and verifying results

`tentacle.py complete` is the orchestrator verification step — it marks the tentacle done,
clears the active marker (unblocking `git commit`/`git push`), and auto-learns from `handoff.md`:

```bash
python3 ~/.copilot/tools/tentacle.py complete api-export          # Mark done + auto-learn
python3 ~/.copilot/tools/tentacle.py complete api-export --no-learn  # Mark done, skip learn
```

Run `complete` only after reviewing sub-agent results and resolving any conflicts. The
orchestrator then commits and pushes — sub-agents must never commit or push.

---

## Tentacle Next Step

`tentacle.py next-step` shows the grounded next step for a named tentacle — the first pending
todo plus optional checkpoint and briefing context. **Read-only**: does not mutate tentacle state.

```bash
python3 ~/.copilot/tools/tentacle.py next-step api-export              # First pending todo + checkpoint context
python3 ~/.copilot/tools/tentacle.py next-step api-export --all        # All pending todos (not just the first)
python3 ~/.copilot/tools/tentacle.py next-step api-export --briefing   # + live knowledge briefing from briefing.py
python3 ~/.copilot/tools/tentacle.py next-step api-export --no-checkpoint  # Omit checkpoint context
python3 ~/.copilot/tools/tentacle.py next-step api-export --format json    # Machine-readable JSON output
```

JSON output includes `tentacle`, `status`, `todos_done`, `todos_total`, `pending`, `next_step`,
`checkpoint_context`, and `briefing` fields.

### Sub-agent conventions

These apply to every dispatched sub-agent.

- **Commit restriction (enforced + convention)**: Sub-agents must not run `git commit` or
  `git push`. When git hooks are installed (`install.py --install-git-hooks`), both operations
  are **blocked at the git level** by `hooks/check_subagent_marker.py` whenever the
  `dispatched-subagent-active` marker is present, fresh, and its `git_root` matches the repo
  running the git command. Even without git hooks, this remains a firm convention: only the
  orchestrator commits, after merging and verifying all tentacle results. A sub-agent commit
  mid-run risks corrupting the orchestrator's merge flow.

  > **Cross-repo isolation (phase 4+):** A marker written in repo A does not block `git commit`
  > in repo B. Each marker entry carries a `git_root` field; the hook skips entries from
  > different repos. The same tentacle name can be active in multiple repos at once — each
  > produces a separate marker entry. Dedup key: `tentacle_id` (primary, for phase-5 entries)
  > → `(name, git_root)` fallback (for phase-4 / legacy entries without `tentacle_id`).
  > Entries without `git_root` (old format) conservatively block all repos.
  >
  > **Upgrade migration:** Cross-repo isolation is not retroactive. In-flight old-format markers
  > have no `git_root` and continue to block all repos until completed, cleared, or expired.
  > To get isolation immediately after upgrading: `tentacle.py complete <name>`, then re-dispatch.

  > **Local-only enforcement**: the git hook guard fires only on local machines where hooks are
  > installed. Cloud-delegated or remote agent runs are not covered.

  > **Same-repo multi-session (phase 5 — supported at runtime, with caveats):**
  > `tentacle.py create` now generates a `tentacle_id` UUID per instance. If the requested
  > directory already exists, `create` auto-resolves the collision by creating
  > `<name>-<8-char-uuid>` and printing the slug. Two sessions in the same repo can each hold
  > separate, non-colliding marker entries; `complete` removes only the matching identity.
  >
  > **Working-tree caveat:** Runtime identity isolation does not create separate working trees
  > or index snapshots. Two tentacles in the same repo with overlapping file scopes will still
  > produce merge conflicts or overwritten files in the shared working directory. Keep tentacle
  > scopes non-overlapping when running same-repo concurrent sessions.
  >
  > **Slug name caveat:** When a collision-resolved slug (`<name>-<uuid[:8]>`) is created, all
  > subsequent commands (`todo`, `swarm`, `complete`, `handoff`) must use the slug, not the
  > original logical name. The slug is printed by `create` and stored as `dir_name` in
  > `meta.json`.

- **Stay in scope**: Avoid editing files outside your tentacle's declared scope.
- **Escalate, don't expand**: If scope is insufficient, record the gap in `handoff.md` and stop.
  Do not expand scope or commit partial work unilaterally.
- **No over-implementation**: Implement only what your todos specify.
- **Write handoff.md before stopping**: Even if your session ends early, always leave a summary so
  the orchestrator can resume or reassign.

## Tentacle Bundle

`tentacle.py bundle` materializes a per-run context bundle for a tentacle subagent — a local
`bundle/` directory containing `briefing.md`, `instructions.md`, `skills.md`,
`session-metadata.md`, and a `manifest.json`. Useful when a sub-agent needs all context
artifacts written to disk before execution.

```bash
python3 ~/.copilot/tools/tentacle.py bundle api-export              # Materialize bundle (fetches briefing)
python3 ~/.copilot/tools/tentacle.py bundle api-export --no-briefing   # Skip live briefing fetch
python3 ~/.copilot/tools/tentacle.py bundle api-export --no-checkpoint # Skip checkpoint context
python3 ~/.copilot/tools/tentacle.py bundle api-export --output json   # JSON output (manifest + bundle_path)
```

The bundle is written under `.octogent/tentacles/<name>/bundle/`. Existing files are
overwritten on each run. JSON output returns `manifest` and `bundle_path` fields.

## Project Context

`project-context.py` generates a deterministic `project-context.md` artifact from repo and
profile facts — no AI generation, no network access. The output derives from git-tracked files,
the active preset profile, deployed hooks metadata, and test file discovery.

```bash
python3 ~/.copilot/tools/project-context.py                  # Write to session files/ dir
python3 ~/.copilot/tools/project-context.py --stdout         # Print to stdout only
python3 ~/.copilot/tools/project-context.py --output PATH    # Write to an explicit file path
python3 ~/.copilot/tools/project-context.py --repo PATH      # Use a different repo root
python3 ~/.copilot/tools/project-context.py --profile python # Force a specific preset profile
python3 ~/.copilot/tools/project-context.py --no-write       # Dry-run: show target path without writing
python3 ~/.copilot/tools/project-context.py --list-profiles  # Show available preset profiles
```

The output is **deterministic**: same repo state → same output. The last-commit date (not wall-clock
time) is used as the timestamp, so re-running without new commits produces an identical artifact.

## Trend Scout

`trend-scout.py` queries the **GitHub Search API** (keyword + topic searches) using a **multi-lane discovery** architecture to find relevant repos, scores and deduplicates candidates across lanes, then creates or updates structured issues in the target repo. There is no official GitHub Trending API; results are ranked by keyword match, topic overlap, star count, and recency. Each lane is an independent search channel — lanes run in parallel and their candidate sets are merged, deduplicated, and re-scored with cross-lane term-set signals before shortlisting.

### Basic usage

```bash
# Full pipeline — search, shortlist, enrich, create issues
python3 ~/.copilot/tools/trend-scout.py

# Preview without writing anything
python3 ~/.copilot/tools/trend-scout.py --dry-run

# Discovery + shortlist only; skip issue creation
python3 ~/.copilot/tools/trend-scout.py --search-only

# Emit a discovery explainability artifact (JSON) documenting lane results and scoring
python3 ~/.copilot/tools/trend-scout.py --explain

# Cap the number of issues created this run
python3 ~/.copilot/tools/trend-scout.py --limit 3

# Override the target repo
python3 ~/.copilot/tools/trend-scout.py --repo owner/repo

# Use a custom config file
python3 ~/.copilot/tools/trend-scout.py --config /path/to/config.json

# Explicit GitHub token (overrides GITHUB_TOKEN env var)
python3 ~/.copilot/tools/trend-scout.py --token TOKEN

# Bypass grace window and force a new run regardless of last-run state
python3 ~/.copilot/tools/trend-scout.py --force
```

Set `GITHUB_TOKEN` in the environment, or pass `--token TOKEN`, to avoid API rate limits.
`--limit` caps **new issue creates** only; marker-matched updates are still evaluated.
`--explain` writes a JSON artifact listing which lanes fired, candidate scores, and which
cross-lane term-set overlaps influenced final scoring. Combine with `--search-only` for a
read-only discovery audit. When `trend-scout-goldset.json` is present, the same artifact also
evaluates each curated watchlist repo as `missing`, `raw`, or `shortlisted`. In GitHub Actions
manual runs, `explain=true` uploads this file as a workflow artifact.

### Multi-lane discovery

The pipeline supports parallel discovery lanes configured in the `lanes[]` array of
`trend-scout-config.json`. Each lane can specify:

| Lane field | Effect |
|---|---|
| `name` | Lane identifier; tagged on each candidate as `_discovery_lane` |
| `keywords` | GitHub Search queries for this lane (plain phrases or qualifier-rich expressions such as exact phrases / `topic:` filters) |
| `topics` | Topic filters to search in parallel with keywords |
| `language` | Language filter (`null` = language-agnostic) |
| `min_stars` | Minimum star count for this lane (can differ from primary) |
| `max_per_query` | Result cap per individual search query |
| `lookback_days` | Repo-age window for this lane |

The primary `search.*` section still defines the default lane. `lanes[]` entries are additional
channels that run independently and merge into the same candidate pool. After merge, cross-lane
term-set scoring adjusts composite scores for repos that appear in multiple lanes.

The browse UI Settings page (`/settings`) surfaces lane metadata from `/api/scout/status`
as `discovery_lanes[]`, showing each lane name, keyword/topic count, language, and `min_stars`
in a read-only diagnostics card.

### Gold-set / watchlist replay

Trend Scout also supports a repo-local strategic watchlist in `trend-scout-goldset.json`.
This file is not another discovery lane; it is a regression benchmark used by `--explain`.
Each entry can define:

| Gold-set field | Effect |
|---|---|
| `repo` | Exact `owner/name` to track |
| `required` | Whether the repo counts toward missing-required coverage **and gets priority in the shortlist within the cap** |
| `expected_lane` | Optional expected discovery lane (for example `adjacent-ai-dev`) |
| `min_score` | Optional minimum acceptable shortlist score |
| `category` | Optional grouping label for operator review |
| `notes` | Human-readable reason the repo matters |

When `python3 ~/.copilot/tools/trend-scout.py --search-only --explain` runs, the explain JSON
adds a `goldset` block summarizing:

- total watchlist entries
- how many were found in raw discovery
- how many survived shortlist
- which required repos are still missing
- lane mismatches / score failures for tracked repos

This is the durable guardrail for repos like `1jehuang/jcode`: even if no issue is created, the
operator can still tell whether Trend Scout recall regressed.

#### Required-repo shortlist retention

Repos with `"required": true` are **prioritized** into the shortlist whenever they appear in raw
discovery and meet their per-entry `min_score` (or the global `shortlist.min_score` if not set).
They preempt non-required repos within `shortlist.max_candidates`, so high-scoring non-required
repos can never crowd them out. The terminal output prints a `📌 Retaining N required gold-set
repo(s)` line listing which repos were retained — this is the operator-visible confirmation that
retention fired.

If required repos themselves ever outnumber `shortlist.max_candidates`, Trend Scout keeps the
highest-scoring required repos, emits a warning, and leaves the cap intact. Raise
`shortlist.max_candidates` if you want to retain all required repos simultaneously.

Non-required gold-set entries (or any repo absent from `trend-scout-goldset.json`) follow the
standard top-N scoring logic unchanged.

### Operator-safe automation flow

Use this sequence to keep automation practical and low-noise:

```bash
# 1) Discovery sanity check (no issue writes)
python3 ~/.copilot/tools/trend-scout.py --search-only

# 2) Explainability audit (shows lane contributions + scoring)
python3 ~/.copilot/tools/trend-scout.py --search-only --explain

# 3) Render verification (body previews only)
python3 ~/.copilot/tools/trend-scout.py --dry-run --limit 1 --force

# 4) Controlled live write
python3 ~/.copilot/tools/trend-scout.py --limit 1 --force
```

After validation, let `.github/workflows/trend-scout.yml` handle daily scheduling.

### Optional GitHub Models analysis

Trend Scout can replace the static learning-bullet heuristics with GitHub Models inference:

```json
{
  "analysis": {
    "enabled": true,
    "model": "openai/gpt-4o-mini",
    "endpoint": "https://models.github.ai/inference/chat/completions",
    "token_env": "GITHUB_MODELS_TOKEN"
  }
}
```

- `analysis.model` must use the GitHub Models `publisher/model` format.
- `analysis.token_env` is explicit on purpose: locally, export `GITHUB_MODELS_TOKEN`; in GitHub Actions, either set `token_env` to `GITHUB_TOKEN` or map `GITHUB_MODELS_TOKEN` from `secrets.GITHUB_TOKEN`.
- If the token is missing, the model ID is invalid, or the response is malformed, Trend Scout logs the reason and falls back to the heuristic `_derive_learnings()` path.

### Veto gate

Before creating a **new** issue, the pipeline evaluates the candidate against the configured veto gate.
Set `veto.require_domain_signals=1` in `trend-scout-config.json` to skip any candidate whose heuristic
learning engine returns only the generic fallback bullet (no domain-specific signals matched). The
script default is `0` (disabled). In this repository, the bundled `trend-scout-config.json` currently
sets `1`.

Set `veto.min_distinct_learnings` to require a minimum count of **distinct novel insight families**
after already-implemented bullets are filtered out. Script default is `0` (disabled). In this
repository, the bundled config currently sets `2`, which means a candidate with only one genuinely
new idea is vetoed instead of creating a new issue. Veto decisions are printed to stdout as
`⊘ Veto (<reason>): owner/repo`.

> **Note:** the veto gate applies to **new creates only** — existing **open** issues can still update when
> marker-matched and changed. Marker-matched **closed** issues suppress writes.

### Grace window and run state

`run_control.grace_window_hours` prevents back-to-back runs from firing within the configured window.
After each successful full run (non-dry-run, non-search-only), the last-run timestamp is written to
`.trend-scout-state.json` adjacent to the script (or to the path set in `run_control.state_file`). On
the next run, if the elapsed time since `last_run_utc` is less than `grace_window_hours`, the run
exits 0 immediately and prints the remaining window. Use `--force` (or the `force` workflow input) to
bypass the grace window unconditionally.

The script default value is `0` (disabled). In this repository, the bundled config currently sets `20`.
In GitHub Actions, `.trend-scout-state.json` is preserved between runner instances via `actions/cache`
so the grace window works across daily scheduled runs.

> `.trend-scout-state.json` is a local runtime artifact — it is listed in `.gitignore` and should not
> be committed.

### Deduplication

Before create/update decisions, the script scans **all open and closed** `trend-scout`-labelled
issues for a hidden deterministic marker. The marker is a 16-character truncated SHA-256
hash of the lowercased `owner/name` embedded as an HTML comment:
`<!-- trend-scout:repo:<16-char-hex> -->`.

- **Marker missing** → create a new issue.
- **Marker present + existing issue is open + rendered body changed** → update the existing issue in place.
- **Marker present + existing issue is closed** → suppress (skip write/update).
- **Marker present + rendered body unchanged** → skip.

### Config tuning (`trend-scout-config.json`)

| Key | Effect |
|-----|--------|
| `search.seed_keywords` | Free-text queries for the primary lane |
| `search.extra_topics` | Additional topic filters for the primary lane |
| `search.min_stars` | Minimum star count for the primary lane |
| `lanes[]` | Array of additional discovery lanes (each with `name`, `keywords`, `topics`, `language`, `min_stars`, `max_per_query`, `lookback_days`) |
| `shortlist.max_candidates` | How many repos advance to enrichment |
| `shortlist.min_score` | Minimum composite score threshold (unbounded sum; default `0.15`) |
| `shortlist.scoring.*_weight` | Adjust keyword, topic, star, and recency weights |
| `enrichment.readme_max_chars` | Characters of README to fetch and pass to the heuristic engine (default `3000`); increase for feature-dense READMEs, decrease to reduce issue size |
| `dedup.search_closed_issues` | Whether to scan closed issues for markers |
| `dedup.max_issues_scan` | Max issues scanned per dedup pass (default 300); increase on busy repos to avoid missing old markers |
| `search.lookback_days` | Repo age window for the primary lane (default 730 days); lower to focus on recently active repos |
| `analysis.enabled` | Enables GitHub Models per-repo learning analysis before issue rendering |
| `analysis.model` | GitHub Models model ID in `publisher/model` format (default `openai/gpt-4o-mini`) |
| `analysis.token_env` | Environment variable that holds the models-capable token (default `GITHUB_MODELS_TOKEN`) |
| `analysis.max_learnings` | Caps LLM-generated bullets per repo before rendering |
| `analysis.temperature`, `analysis.max_tokens`, `analysis.timeout` | Controls inference determinism, output size, and request timeout |
| `veto.require_domain_signals` | Script default: `0` (disabled). Set `1` to skip candidates whose heuristic engine produces only the generic fallback bullet (no domain-specific signals). Applies to new creates only; existing open issues are still eligible for update (marker-matched closed issues suppress writes). |
| `veto.min_distinct_learnings` | Script default: `0` (disabled). Requires at least _N_ distinct novel insight families after already-implemented bullets are removed; values like `2` suppress create-stage issues when only one genuinely new insight remains. Applies to new creates only. |
| `run_control.grace_window_hours` | Script default: `0` (disabled). Hours to wait between full runs. Grace window state is persisted to `.trend-scout-state.json`. Use `--force` to bypass. |
| `run_control.state_file` | Path to the run-state JSON file. `null` or absent resolves to `.trend-scout-state.json` adjacent to the script. |

### Limitations

- Uses GitHub Search API heuristics — not an official trending list.
- Freshness depends on GitHub's search index; very new repos may not appear immediately.
- The primary lane defaults to `language: python`; other lanes can be language-agnostic.

### GitHub Actions workflow

The workflow (`.github/workflows/trend-scout.yml`) runs **daily at 07:00 UTC**. It requires
no secrets beyond the automatic `GITHUB_TOKEN` and uses minimal permissions:
`contents: read`, `issues: write`, `models: read`.

The workflow exports both `GITHUB_TOKEN` and `GITHUB_MODELS_TOKEN` from `secrets.GITHUB_TOKEN`, so the optional `analysis.enabled` path works without extra secrets when running in GitHub Actions.

**Manual dispatch inputs:**

| Input | Type | Description |
|-------|------|-------------|
| `dry_run` | boolean | Preview without creating issues |
| `search_only` | boolean | Discovery + shortlist only |
| `explain` | boolean | Emit and upload the discovery explainability artifact |
| `repo` | string | Override target repo (`OWNER/REPO`) |
| `limit` | string | Max issues to create this run |
| `force` | boolean | Bypass grace window and force a full run |

### Hook behavior (anti-spam)

Trend Scout is intentionally **not** registered in Copilot runtime hooks (`hooks/hooks.json`).
Keep scouting in explicit CLI runs or scheduled workflow automation; avoid per-tool hook triggers
that would spam session output.


## Maintenance

```bash
python3 ~/.copilot/tools/build-session-index.py --incremental   # Update changed files + auto-embed
python3 ~/.copilot/tools/build-session-index.py --no-embed      # Index only, skip embeddings
python3 ~/.copilot/tools/extract-knowledge.py --stats           # View knowledge statistics
python3 ~/.copilot/tools/extract-knowledge.py --relations       # View relation statistics
python3 ~/.copilot/tools/watch-sessions.py --install-hint      # Show auto-start setup instructions
python3 ~/.copilot/tools/embed.py --status                      # Embedding coverage stats
python3 ~/.copilot/tools/embed.py --build                       # Rebuild all embeddings
python3 ~/.copilot/tools/install.py --deploy-skill              # Deploy SKILL.md
python3 ~/.copilot/tools/install.py --deploy-hooks              # Deploy Copilot CLI hooks
python3 ~/.copilot/tools/install.py --install-git-hooks         # Install pre-commit/pre-push git hooks (per repo)
python3 ~/.copilot/tools/install.py --deploy-instructions       # Deploy global instructions
python3 ~/.copilot/tools/install.py --inject-global             # Inject into global copilot-instructions
```

## Auto-Start (Background Watcher)

### macOS — LaunchAgents (recommended)

```bash
bash ~/.copilot/tools/launchd/install-launchd.sh           # Install both agents
bash ~/.copilot/tools/launchd/install-launchd.sh --remove   # Uninstall

# Installs two LaunchAgents:
#   com.copilot.watch-sessions  — foreground watcher managed by launchd (auto-indexes + auto-embeds)
#   com.copilot.auto-update     — daily 9 AM, git pulls tool updates + migrates DB
```

### Windows — Task Scheduler

```powershell
$action = New-ScheduledTaskAction `
    -Execute "python" `
    -Argument "$env:USERPROFILE\.copilot\tools\watch-sessions.py --daemon" `
    -WorkingDirectory "$env:USERPROFILE\.copilot"

$trigger = New-ScheduledTaskTrigger -AtLogOn
$settings = New-ScheduledTaskSettingsSet -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries `
    -RestartCount 3 -RestartInterval (New-TimeSpan -Minutes 1)

Register-ScheduledTask -TaskName "CopilotWatchSessions" `
    -Action $action -Trigger $trigger -Settings $settings `
    -Description "Auto-index Copilot session knowledge"
```

### Linux — systemd user service

```bash
mkdir -p ~/.config/systemd/user
cat > ~/.config/systemd/user/copilot-watch.service << 'SVC'
[Unit]
Description=Copilot Session Knowledge Watcher

[Service]
ExecStart=/usr/bin/python3 %h/.copilot/tools/watch-sessions.py
WorkingDirectory=%h/.copilot
Restart=on-failure
RestartSec=30

[Install]
WantedBy=default.target
SVC

systemctl --user enable --now copilot-watch.service
```

## Aliases

Add to your `~/.zshrc` or `~/.bashrc`:

```bash
alias qs='python3 ~/.copilot/tools/query-session.py'
alias brief='python3 ~/.copilot/tools/briefing.py'
alias learn='python3 ~/.copilot/tools/learn.py'
# Usage: qs "docker error" | brief "fix login" | learn --pattern "Title" "Desc"
```
