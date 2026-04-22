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
brief "task" --for-subagent --budget 3000  # Capped output for sub-agent injection
brief "task" --min-confidence 0.7    # High-quality entries only
brief "task" --for-subagent          # Compact context block for sub-agent prompts
brief "task" --compact               # XML compact block for AI context injection
brief --task "memory-surface"        # Task-scoped recall: entries tagged with this task ID
brief "fix Docker" --json            # JSON output for programmatic use
```

Token-distillation flags: `--compact` produces an XML compact block; `--budget N` hard-caps output to N characters (frozen snapshot, highest-confidence entries first); `--titles-only` gives ~10 tok/entry for progressive disclosure.

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
qs --diff                            # Entries for files in the current git diff
qs "search" --export json            # Export results as JSON
qs "search" --export markdown        # Export results as Markdown
```

## Drill Down

Use entry ID from search results:

```bash
qs --detail 2045                     # View full entry details
qs --context 2045                    # Entry + entries from same session
qs --related 2045                    # Entry + knowledge graph connections
qs --graph "spring boot"             # Mini knowledge graph by topic
```

## Semantic Search

Requires an embedding API key (optional):

```bash
qs "deployment error" --semantic     # Search by meaning, not just keywords
python3 ~/.copilot/tools/embed.py --setup   # Setup API key
```

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
  `dispatched-subagent-active` marker is present and fresh. Even without git hooks, this
  remains a firm convention: only the orchestrator commits, after merging and verifying all
  tentacle results. A sub-agent commit mid-run risks corrupting the orchestrator's merge flow.

  > **Local-only enforcement**: the git hook guard fires only on local machines where hooks are
  > installed. Cloud-delegated or remote agent runs are not covered.

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

`trend-scout.py` queries the **GitHub Search API** (keyword + topic searches) to discover
relevant repos, scores and deduplicates candidates, then opens structured issues in the target
repo. There is no official GitHub Trending API; results are ranked by keyword match, topic
overlap, star count, and recency.

### Basic usage

```bash
# Full pipeline — search, shortlist, enrich, create issues
python3 ~/.copilot/tools/trend-scout.py

# Preview without writing anything
python3 ~/.copilot/tools/trend-scout.py --dry-run

# Discovery + shortlist only; skip issue creation
python3 ~/.copilot/tools/trend-scout.py --search-only

# Cap the number of issues created this run
python3 ~/.copilot/tools/trend-scout.py --limit 3

# Override the target repo
python3 ~/.copilot/tools/trend-scout.py --repo owner/repo

# Use a custom config file
python3 ~/.copilot/tools/trend-scout.py --config /path/to/config.json

# Explicit GitHub token (overrides GITHUB_TOKEN env var)
python3 ~/.copilot/tools/trend-scout.py --token TOKEN
```

Set `GITHUB_TOKEN` in the environment, or pass `--token TOKEN`, to avoid API rate limits.

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

### Deduplication

Before creating each issue the script scans **all open and closed** `trend-scout`-labelled
issues for a hidden deterministic marker. The marker is a 16-character truncated SHA-256
hash of the lowercased `owner/name` embedded as an HTML comment:
`<!-- trend-scout:repo:<16-char-hex> -->`. A repo is skipped if its marker already exists,
regardless of issue state.

### Config tuning (`trend-scout-config.json`)

| Key | Effect |
|-----|--------|
| `search.seed_keywords` | Free-text queries sent to GitHub Search API |
| `search.extra_topics` | Additional topic filters |
| `search.min_stars` | Minimum star count to consider a repo |
| `shortlist.max_candidates` | How many repos advance to enrichment |
| `shortlist.min_score` | Minimum composite score threshold (unbounded sum; default `0.15`) |
| `shortlist.scoring.*_weight` | Adjust keyword, topic, star, and recency weights |
| `enrichment.readme_max_chars` | Characters of README to fetch and pass to the heuristic engine (default `3000`); increase for feature-dense READMEs, decrease to reduce issue size |
| `dedup.search_closed_issues` | Whether to scan closed issues for markers |
| `dedup.max_issues_scan` | Max issues scanned per dedup pass (default 300); increase on busy repos to avoid missing old markers |
| `search.lookback_days` | Repo age window for search results (default 730 days); lower to focus on recently active repos |
| `analysis.enabled` | Enables GitHub Models per-repo learning analysis before issue rendering |
| `analysis.model` | GitHub Models model ID in `publisher/model` format (default `openai/gpt-4o-mini`) |
| `analysis.token_env` | Environment variable that holds the models-capable token (default `GITHUB_MODELS_TOKEN`) |
| `analysis.max_learnings` | Caps LLM-generated bullets per repo before rendering |
| `analysis.temperature`, `analysis.max_tokens`, `analysis.timeout` | Controls inference determinism, output size, and request timeout |

### Limitations

- Uses GitHub Search API heuristics — not an official trending list.
- Freshness depends on GitHub's search index; very new repos may not appear immediately.
- Results are filtered to `language: python` by default (configurable).

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
| `repo` | string | Override target repo (`OWNER/REPO`) |
| `limit` | string | Max issues to create this run |

## Maintenance

```bash
python3 ~/.copilot/tools/build-session-index.py --incremental   # Update changed files + auto-embed
python3 ~/.copilot/tools/build-session-index.py --no-embed      # Index only, skip embeddings
python3 ~/.copilot/tools/extract-knowledge.py --stats           # View knowledge statistics
python3 ~/.copilot/tools/extract-knowledge.py --relations       # View relation statistics
python3 ~/.copilot/tools/watch-sessions.py --daemon             # Run in background, auto-index
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
#   com.copilot.watch-sessions  — daemon, auto-indexes sessions + auto-embeds
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
ExecStart=/usr/bin/python3 %h/.copilot/tools/watch-sessions.py --daemon
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
