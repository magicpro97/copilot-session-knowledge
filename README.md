# Copilot Session Knowledge

> Cross-session memory for AI coding agents — never repeat past mistakes.

[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)
[![Python 3.10+](https://img.shields.io/badge/python-3.10+-blue.svg)]()
[![Platform](https://img.shields.io/badge/platform-macOS%20%7C%20Linux%20%7C%20Windows-lightgrey)]()
[![Zero Dependencies](https://img.shields.io/badge/dependencies-0-success)]()

## Table of Contents

- [Why?](#why)
- [Quick Start](#quick-start)
- [Installation](#installation)
- [Usage](#usage)
- [Architecture](#architecture)
- [Auto-Update](#auto-update)
- [Skills & Hooks](#skills--hooks)
- [Trend Scout](#trend-scout)
- [Security](#security)
- [Testing](#testing)
- [FAQ](#faq)
- [Contributing](#contributing)
- [License](#license)

## Why?

Each Copilot CLI / Claude Code session accumulates valuable experience — bugs encountered, patterns discovered, architecture decisions made. But every new session starts from zero, repeating past mistakes.

This tool **indexes all session data** into SQLite FTS5, **auto-extracts knowledge** into 7 categories (mistakes, patterns, decisions, tools, features, refactors, discoveries), and provides **search + briefing** so your AI agent never forgets what it learned.

## Quick Start

```bash
# 1. Clone
git clone https://github.com/magicpro97/copilot-session-knowledge.git ~/.copilot/tools

# 2. Build knowledge base
python3 ~/.copilot/tools/build-session-index.py && python3 ~/.copilot/tools/extract-knowledge.py

# 3. Get a briefing
python3 ~/.copilot/tools/briefing.py "your task description"
```

That's it. Your AI agent now has memory across sessions.

## Installation

### Prerequisites

- Python 3.10+ (no pip packages needed — pure stdlib)
- Copilot CLI (`~/.copilot/session-state/`) and/or Claude Code

> **Note:** Use `python3` on macOS/Linux, `python` or `py` on Windows.

### Recommended (auto-update enabled)

```bash
git clone https://github.com/magicpro97/copilot-session-knowledge.git ~/.copilot/tools
python3 ~/.copilot/tools/build-session-index.py
python3 ~/.copilot/tools/extract-knowledge.py
python3 ~/.copilot/tools/migrate.py
python3 ~/.copilot/tools/install.py --test

# macOS: install LaunchAgents (auto-start watcher + daily auto-update)
bash ~/.copilot/tools/launchd/install-launchd.sh
```

### Alternative (manual copy)

```bash
git clone https://github.com/magicpro97/copilot-session-knowledge.git
cd copilot-session-knowledge
mkdir -p ~/.copilot/tools && cp *.py *.sh ~/.copilot/tools/
```

### Windows (PowerShell)

```powershell
git clone https://github.com/magicpro97/copilot-session-knowledge.git
cd copilot-session-knowledge
New-Item -ItemType Directory -Force "$env:USERPROFILE\.copilot\tools"
Copy-Item *.py,*.sh "$env:USERPROFILE\.copilot\tools\"
python "$env:USERPROFILE\.copilot\tools\build-session-index.py"
python "$env:USERPROFILE\.copilot\tools\extract-knowledge.py"
python "$env:USERPROFILE\.copilot\tools\migrate.py"
```

### Aliases (optional)

```bash
alias qs='python3 ~/.copilot/tools/query-session.py'
alias brief='python3 ~/.copilot/tools/briefing.py'
alias learn='python3 ~/.copilot/tools/learn.py'
```

## Usage

### Briefing (before every task)

```bash
brief "implement user CRUD"          # Compact ~500 tokens
brief "implement user CRUD" --full   # Full detail ~3K tokens
brief --auto                         # Auto-detect from git state
brief "task" --for-subagent          # Compact context for sub-agents
brief --task "memory-surface"        # Task-scoped recall by task ID
brief "fix Docker" --json            # JSON output for programmatic use
brief "task" --budget 3000           # Cap output to 3000 chars (frozen snapshot)
brief "task" --compact               # XML compact block for AI context injection
```

### Search

```bash
qs "search terms"                    # Compact results
qs "docker" --type research          # Filter by doc type
qs --mistakes                        # View past errors
qs --detail 2045                     # Full entry by ID
qs "deployment error" --semantic     # Semantic search (requires API key)
qs --file src/auth.py                # Entries touching a specific file
qs --module auth                     # Entries for a module or directory
qs --task memory-surface             # Entries tagged with a task ID
qs --diff                            # Entries for current git diff files
qs "search" --export json            # Export results as JSON
qs "search" --budget 2000            # Cap output to 2000 chars
qs "search" --compact                # Titles-only with ~token hint
```

### Record Knowledge

```bash
learn --mistake "Title"  "What went wrong"     --tags "docker"
learn --pattern "Title"  "Best practice"       --tags "lambda"
learn --decision "Title" "Architecture choice" --tags "cdk"
learn --mistake "Title"  "Description" --task "memory-surface" --file "briefing.py"
learn --mistake "Title"  "Description" --json  # Machine-readable JSON output
```

### Tentacle Orchestration (runtime-bundle workflow)

Multi-agent parallel execution via scoped work units. The runtime-bundle workflow:

```bash
# 1. Create a tentacle with scope + briefing
python3 ~/.copilot/tools/tentacle.py create api-export \
  --scope "src/api/*.py" --desc "Export API endpoints" --briefing

# 2. Add atomic todo items (one per agent delegation unit)
python3 ~/.copilot/tools/tentacle.py todo api-export add "Generate OpenAPI schema"

# 3. Dispatch — choose output mode:
python3 ~/.copilot/tools/tentacle.py swarm api-export \
  --agent-type general-purpose --model claude-sonnet-4.6              # single prompt
python3 ~/.copilot/tools/tentacle.py swarm api-export --output parallel  # one dispatch per todo
python3 ~/.copilot/tools/tentacle.py swarm api-export --output json      # structured JSON

# 4. After agents finish: record results and close
python3 ~/.copilot/tools/tentacle.py handoff api-export "Done. Learned X" --learn
python3 ~/.copilot/tools/tentacle.py complete api-export
```

> `--output parallel` maximises parallelism (one agent per todo). `--output json` is for
> programmatic consumption. `--briefing` injects live session-knowledge at dispatch time
> (incompatible with `--output json`).

**Commit restriction:** Sub-agents must not run `git commit` or `git push`. When git hooks
are installed (`install.py --install-git-hooks`), both operations are **blocked at the git level**
while a `dispatched-subagent-active` marker is active and the marker's `git_root` matches the
current repo. Even without hooks, this is a hard convention: only the orchestrator commits,
after merging and verifying tentacle results. Enforcement is local-only — cloud-delegated runs
are not covered.

> **Cross-repo isolation:** Each marker entry carries a `git_root` field. If Terminal A has an
> active tentacle in repo A, a `git commit` in repo B is **not blocked** — the hook skips
> markers whose `git_root` doesn't match the committing repo. Markers written without `git_root`
> (old format or dispatch from a non-git directory) conservatively block as before.

> **Stuck marker:** If the orchestrator crashes before `tentacle.py complete`, the marker stays
> active for up to 4 hours (TTL dead-man switch). To clear it manually:
> ```bash
> python3 ~/.copilot/tools/tentacle.py complete <name>
> # or directly:
> rm ~/.copilot/markers/dispatched-subagent-active
> ```

### Tentacle Next Step

```bash
# Show the grounded next step for a named tentacle (read-only)
python3 ~/.copilot/tools/tentacle.py next-step api-export         # First pending todo + checkpoint context
python3 ~/.copilot/tools/tentacle.py next-step api-export --all   # All pending todos
python3 ~/.copilot/tools/tentacle.py next-step api-export --briefing        # + live knowledge briefing
python3 ~/.copilot/tools/tentacle.py next-step api-export --no-checkpoint   # Skip checkpoint context
python3 ~/.copilot/tools/tentacle.py next-step api-export --format json     # JSON output
```

### Project Context

```bash
# Generate deterministic project-context.md (repo structure, profile, hooks, test expectations)
python3 ~/.copilot/tools/project-context.py                # Write to session files/ dir
python3 ~/.copilot/tools/project-context.py --stdout       # Print to stdout only
python3 ~/.copilot/tools/project-context.py --output PATH  # Write to explicit path
python3 ~/.copilot/tools/project-context.py --profile python  # Force a preset profile
python3 ~/.copilot/tools/project-context.py --list-profiles   # Show available profiles
```

No AI generation, no network access. The artifact is derived purely from repo/profile facts and is deterministic for the same repo state.

### Checkpoint Lifecycle

```bash
# Save
python3 ~/.copilot/tools/checkpoint-save.py --title "Auth done" --overview "JWT added"
# Read back (read-only)
python3 ~/.copilot/tools/checkpoint-restore.py --list
python3 ~/.copilot/tools/checkpoint-restore.py --show latest
python3 ~/.copilot/tools/checkpoint-restore.py --export latest --format json
# Compare
python3 ~/.copilot/tools/checkpoint-diff.py --from 1 --to latest
python3 ~/.copilot/tools/checkpoint-diff.py --summary
```

### Profile Lifecycle

Build, share, and deploy custom workflow profiles:

```bash
python3 ~/.copilot/tools/profile-builder.py --name myteam \
  --hooks dangerous-blocker.sh commit-gate.sh --phases CLARIFY BUILD TEST COMMIT
python3 ~/.copilot/tools/profile-export.py --profile myteam --output myteam.json
python3 ~/.copilot/tools/profile-import.py --file myteam.json
python3 ~/.copilot/tools/setup-project.py --profile myteam   # deploy
```

📖 **Full command reference:** [docs/USAGE.md](docs/USAGE.md)

## Architecture

```mermaid
flowchart TD
  subgraph Data["📁 ~/.copilot/session-state/"]
    RAW["Session checkpoints<br/>plan.md, research/, files/"]
    DB[("knowledge.db<br/>FTS5 + vectors + graph")]
  end

  subgraph Tools["🔧 ~/.copilot/tools/"]
    IDX[build-session-index.py]
    EXT[extract-knowledge.py]
    QRY[query-session.py]
    BRF[briefing.py]
    WCH[watch-sessions.py]
  end

  RAW -->|index| IDX -->|write| DB
  DB -->|extract| EXT -->|relations + dedup| DB
  DB -->|search| QRY
  DB -->|briefing| BRF
  WCH -->|auto-trigger| IDX

  style DB fill:#f59e0b,color:#000
```

### How it works

1. **Index** — `build-session-index.py` scans session `.md` files → SQLite FTS5
2. **Extract** — `extract-knowledge.py` classifies into 7 types, dedup by content hash
3. **Graph** — Auto-detect relations: same session, same tag, mistake→fix
4. **Search** — FTS5 keyword + optional semantic vector (Reciprocal Rank Fusion)
5. **Watch** — `watch-sessions.py` polls for changes, auto re-indexes
6. **Update** — `auto-update-tools.py` smart pipeline: git pull → diff-based update
7. **Host metadata** — `host_manifest.py` is the single source of truth for supported hosts (Copilot CLI + Claude Code only) and their file-system paths; imported by `install.py`, `setup-project.py`, `watch-sessions.py`, and `auto-update-tools.py`
8. **Tentacle workspace** — `.octogent/` stores local tentacle state and is gitignored in this repo

## Auto-Update

```bash
python3 ~/.copilot/tools/auto-update-tools.py           # Auto-update (24h cooldown)
python3 ~/.copilot/tools/auto-update-tools.py --force    # Force update now
python3 ~/.copilot/tools/auto-update-tools.py --doctor   # Health check
```

Smart pipeline analyzes `git diff` to run only what changed. Post-merge hook auto-triggers on `git pull`.

📖 **Details:** [docs/AUTO-UPDATE.md](docs/AUTO-UPDATE.md)

## Skills & Hooks

14 built-in skills (session-knowledge-creator, agent-creator, hook-creator, tentacle-creator, tentacle-orchestration, workflow-creator, find-skills, agent-instructions-auditor, forge-ecosystem, code-reviewer, task-step-generator, conductor-creator, project-onboarding, karpathy-guidelines) plus 10 hook templates for quality enforcement.

`setup-project.py` is the canonical deployment surface: it reads the skill list from `INSTALL_ITEMS` and deploys all 14 skills to `.github/skills/<skill-name>/SKILL.md` in the **target project** (project scope only). Vendored skills (`karpathy-guidelines`) are additionally deployed to `.claude/skills/` for Claude Code. To make skills available globally across all projects, copy them to `~/.copilot/skills/` manually — `install.py --deploy-skill` deploys to the current project only and does not support a global target; `setup-project.py` does not perform global installation either. **Auto-update for globally installed vendored skills:** once `~/.copilot/skills/karpathy-guidelines/SKILL.md` already exists (placed there manually), auto-update will keep it current whenever the source changes — this is update-only and applies to Copilot CLI global scope (`~/.copilot/skills/`) only; `~/.claude/skills/` does not receive global auto-updates. When auto-update runs inside WSL and can resolve the current Windows user's profile, it refreshes that Windows Copilot CLI global install too — but that Windows-side install must also have been copied there manually first, because the refresh remains update-only. When a skill is available globally, **do not redeploy it at project scope** — Copilot deduplicates by skill name but having the same name at both scopes adds catalog noise and increases load.

Unified hook runner architecture — 1 Python process per event with fail-open, HMAC-signed markers, audit logging, and tamper protection. Hook deployment is **Copilot CLI only**; Claude Code does not support the `hook_runner.py` format.

**Dispatched-subagent git guard (phase 3+):** `install.py --install-git-hooks` deploys
`pre-commit` and `pre-push` scripts into the current repo's `.git/hooks/`. When the
`dispatched-subagent-active` marker is fresh and its `git_root` matches the committing repo,
both scripts block the git operation. This is the **primary enforcement surface** for subagent
commit restrictions — it fires at the filesystem level regardless of which agent process calls
git. The `preToolUse` hook provides defense-in-depth but cannot be relied on inside delegated
subagent contexts. Enforcement is **local-only**; cloud-delegated runs are not covered.

The marker now stores `active_tentacles` as a list of objects (`{name, ts, git_root, tentacle_id}`)
instead of a flat string list. Each entry carries its own dispatch timestamp, the git repository
root where the dispatch originated, and a stable UUID (`tentacle_id`) generated at `create` time.
Primary deduplication key is `tentacle_id` (phase 5, when present) with `(name, git_root)` as the
fallback for legacy entries. Two instances with the same logical name — whether in different repos
or in the same repo — each produce separate entries because their `tentacle_id` values differ.
The hook compares `git_root` against the repo running git using canonical path resolution
(`Path.resolve()`) so symlinks and dotdot paths that resolve to the same physical directory are
treated as the same repo — a marker from a different repo does not block commits there (cross-repo
isolation). Markers written without `git_root` (old format or non-git CWD) conservatively block.

> **Upgrade migration:** Cross-repo isolation is not retroactive for in-flight old-format
> markers. If a tentacle is still active when you upgrade, its existing marker entry has no
> `git_root` and will continue to block all repos until it completes, is cleared, or the 4-hour
> TTL expires. To get isolation immediately: `tentacle.py complete <name>` (or
> `rm ~/.copilot/markers/dispatched-subagent-active`), then re-dispatch.

**Same-repo multi-session (phase 5):** `tentacle.py create` now generates a `tentacle_id` UUID per
instance and auto-resolves directory collisions — if `<name>` already exists, it creates
`<name>-<uuid[:8]>` instead of exiting. All subsequent commands must use the printed slug name.
When a collision rename occurs, the runtime bundle surfaces the actual invocation slug in two
places: `manifest.json` gains a `slug` field and `session-metadata.md` gains a `Slug:` header
line, so sub-agents can always determine the correct name to use in follow-up commands.
Runtime identity ensures `complete` clears only the matching entry, not a sibling with the same
logical name. **Working-tree caveat:** this isolation is at the marker/enforcement layer only.
Concurrent tentacles in the same repo that touch overlapping files still share one working tree
and git index — file-level conflicts must be managed through non-overlapping scope declarations.

**Limitations:** `preToolUse` non-inheritance inside `task()`-spawned subagents remains a
platform limitation — git hooks are the reliable surface. `auto-update-tools.py` does **not**
auto-reinstall git hooks in registered repos — when hook files change it prints three warnings
to stderr: `"Git hook scripts updated — installed per-repo hooks are NOT automatically
refreshed."`, `"ACTION REQUIRED to pick up the cross-repo isolation fix (and future hook
changes):"`, and `"Re-run in EVERY protected repo: python3 ~/.copilot/tools/install.py
--install-git-hooks"`. Re-run that command in every protected repo after relevant tool updates.

```bash
python3 ~/.copilot/tools/install.py --deploy-skill        # Deploy skill to project
python3 ~/.copilot/tools/install.py --deploy-hooks        # Deploy enforcement hooks (Copilot CLI)
python3 ~/.copilot/tools/install.py --lock-hooks          # Lock hooks (tamper protection)
python3 ~/.copilot/tools/install.py --install-git-hooks   # Install pre-commit/pre-push into current repo

# Project setup with a workflow profile
python3 ~/.copilot/tools/setup-project.py --profile python      # Python hook bundle + WORKFLOW.md
python3 ~/.copilot/tools/install-project-hooks.py --profile mobile  # Mobile hooks standalone

# Custom profile lifecycle
python3 ~/.copilot/tools/profile-builder.py --name myteam --hooks dangerous-blocker.sh --phases BUILD TEST COMMIT
python3 ~/.copilot/tools/profile-export.py --profile myteam --output myteam.json
python3 ~/.copilot/tools/profile-import.py --file myteam.json
```

**Session-start hooks** run through the unified `hook_runner.py` architecture (1 Python process per event, fail-open, HMAC-signed markers, audit logging) and automatically refresh the codebase map (`codebase-map.py`) at the start of each session — no manual step required.

**Session-end hooks** (`hooks/session-end.py`) are **reminder-only**: they never auto-save checkpoints.
Set `COPILOT_CHECKPOINT_REMIND=1` to log a reminder when a session ends without saved checkpoints.
To save a checkpoint yourself, run `python3 ~/.copilot/tools/checkpoint-save.py`.

### Context Load Management

Excessive context load in Copilot sessions comes primarily from **duplicate skills** and **overly broad instruction surfaces** — not from the unified hook runner (which is a single process per event and is efficient). To keep load manageable:

- **Minimal-context-first**: start with `briefing.py --compact` (~500 tokens) before escalating to `--full`. Instruction files with `applyTo: '**/*'` inject into every context — scope them narrowly (e.g., `**/*.ts`) when they apply only to specific file types.
- **Progressive escalation**: retrieve only what the current step needs. Full session history and semantic search are available but should be requested on demand, not injected by default.
- **Propagation discipline**: when a skill or instruction is promoted to global scope (`~/.copilot/skills/`, `~/.github/instructions/`), remove the project-local copy to prevent duplication. Audit by manually comparing `~/.copilot/skills/` against `.github/skills/` in each project and removing any skill that exists at both levels. (`hooks/lint-skills.py --all` validates schema — it does not detect cross-scope duplicates.)

📖 **Skills reference:** [docs/SKILLS.md](docs/SKILLS.md) · **Hooks reference:** [docs/HOOKS.md](docs/HOOKS.md)

## Trend Scout

`trend-scout.py` discovers relevant GitHub repositories daily via the **GitHub Search API** (not the unofficial trending page — there is no official GitHub Trending API) and opens structured issues in the target repo for review.

```bash
python3 ~/.copilot/tools/trend-scout.py                 # Full pipeline
python3 ~/.copilot/tools/trend-scout.py --dry-run       # Preview without creating issues
python3 ~/.copilot/tools/trend-scout.py --search-only   # Discovery + shortlist, no issues
python3 ~/.copilot/tools/trend-scout.py --limit 3       # Cap issues created this run
python3 ~/.copilot/tools/trend-scout.py --repo owner/repo  # Override target repo
python3 ~/.copilot/tools/trend-scout.py --config path.json # Custom config file
python3 ~/.copilot/tools/trend-scout.py --token TOKEN   # Explicit GitHub token (overrides GITHUB_TOKEN env)
python3 ~/.copilot/tools/trend-scout.py --force         # Bypass grace window; force a new run
```

Requires a `GITHUB_TOKEN` env var (or `--token TOKEN` flag) to avoid rate limits. The tool auto-creates the `trend-scout` label and deduplicates against both open and closed issues using hidden deterministic markers — each marker is a 16-character truncated SHA-256 hash of the lowercased `owner/name`.

**Optional GitHub Models analysis:** set `analysis.enabled=true` in `trend-scout-config.json` to replace the repetitive heuristic learning bullets with repo-specific LLM analysis. The models path calls `https://models.github.ai/inference/chat/completions`, expects a publisher-qualified model ID such as `openai/gpt-4o-mini`, and reads its credential from `analysis.token_env` (default `GITHUB_MODELS_TOKEN`). If the token is missing, the model ID is invalid, or the response cannot be parsed, Trend Scout falls back to the heuristic engine automatically.

**Veto gate:** set `veto.require_domain_signals=1` in `trend-scout-config.json` to skip candidates whose heuristic learning engine produces only the generic fallback bullet (no domain-specific signals matched). Default is `0` — all shortlisted candidates are written.

**Grace window:** set `run_control.grace_window_hours` in config to prevent runs that are too close together. The last-run timestamp is persisted locally in `.trend-scout-state.json` (adjacent to the script). Use `--force` to bypass the grace window. Default is `0` (disabled). In GitHub Actions, the `.trend-scout-state.json` file is cached between runs via `actions/cache` so the grace window persists across GitHub-hosted runner instances.

**Tune discovery:** edit `trend-scout-config.json` to adjust seed keywords, topic filters, scoring weights, `min_stars`, `enrichment.readme_max_chars`, the optional `analysis.*` settings (`model`, `temperature`, `max_learnings`, `token_env`), `veto.require_domain_signals`, and `run_control.grace_window_hours`.

**GitHub Actions workflow** — `.github/workflows/trend-scout.yml` runs daily at 07:00 UTC with permissions `contents: read`, `issues: write`, and `models: read`. It also maps `secrets.GITHUB_TOKEN` into `GITHUB_MODELS_TOKEN`, so enabling `analysis.enabled` in config works in Actions without a separate secret. Manual runs via `workflow_dispatch` support `dry_run`, `search_only`, `repo`, `limit`, and `force` inputs.

📖 **Details:** [docs/USAGE.md#trend-scout](docs/USAGE.md#trend-scout)

## Security

- **Parameterized SQL** — zero SQL injection vectors
- **FTS5 sanitization** — strips operators (`OR`, `AND`, `NOT`, `NEAR`, `*`, `"`)
- **No pickle** — JSON serialization only (legacy pickle detection + warning)
- **Atomic locks** — `O_CREAT | O_EXCL` eliminates TOCTOU race conditions
- **API key protection** — config files chmod `0o600`, env vars preferred
- **Input limits** — title 200 chars, content 10K chars, FTS query 500 chars
- **Injection scanning** — `learn.py` scans entries against 15 regex patterns
- **Hook tamper protection** — OS immutable flags + SHA256 manifest verification

📖 **Full security policy:** [SECURITY.md](SECURITY.md)

## Testing

```bash
python3 test_security.py      # security regression tests
python3 test_fixes.py         # functional and integration regression tests
python3 test_trend_scout.py   # trend scout unit tests
```

## FAQ

**Q: Does it work with Claude Code?**
A: Yes. `claude-adapter.py` parses Claude Code JSONL sessions into the common format.

**Q: Do I need an API key?**
A: No. API keys are optional — only needed for semantic search via embedding providers (OpenAI, Fireworks, OpenRouter). Without it, FTS5 keyword search and TF-IDF fallback work offline.

**Q: Where is the data stored?**
A: `~/.copilot/session-state/knowledge.db` — a single SQLite file with FTS5 indexes.

**Q: Does it work on Windows?**
A: Yes. All scripts include Windows encoding fixes. Use `python` instead of `python3`. See [Installation](#windows-powershell). POSIX-style home paths from Git Bash (`/c/Users/...`), WSL (`/mnt/c/...`), and Cygwin (`/cygdrive/c/...`) are automatically normalised to native Windows paths for marker lookups.

**Q: How do I update?**
A: `python3 ~/.copilot/tools/auto-update-tools.py --force` or just `git pull` (post-merge hook handles the rest).

**Q: Will hooks crash my AI agent?**
A: No. The unified hook runner uses fail-open architecture — if any rule crashes, it logs the error and allows the action to proceed.

## Troubleshooting

### Copilot CLI auto-heal

If `copilot update` fails with an error like:

```
ENOENT: no such file or directory, rename .../.copilot/pkg/universal/.replaced-1.0.35-<pid>-<ts>
```

or `EPERM` on a rename inside `pkg/universal/`, run the healer:

```bash
python ~/.copilot/tools/copilot-cli-healer.py --status   # Diagnose
python ~/.copilot/tools/copilot-cli-healer.py --heal     # Fix
python ~/.copilot/tools/copilot-cli-healer.py --update   # Heal + retry copilot update
```

**Root cause:** The upstream Copilot CLI Node updater calls `fs.rename(src, dst)` without checking that `src` exists, leaving stale `.replaced-*` dirs and `pkg/tmp/` partial downloads behind. The healer removes these safely.

To prevent recurrence, register a daily scheduled task:

```bash
python ~/.copilot/tools/copilot-cli-healer.py --install-schedule
# or:
python ~/.copilot/tools/install.py --install-healer
```

📖 **Details:** [docs/copilot-cli-healer.md](docs/copilot-cli-healer.md)

## Contributing

See [CONTRIBUTING.md](CONTRIBUTING.md) for guidelines on reporting bugs, suggesting features, and submitting pull requests.

## License

[MIT](LICENSE) © [magicpro97](https://github.com/magicpro97)
