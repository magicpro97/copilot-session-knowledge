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

11 built-in skills (session-knowledge-creator, agent-creator, hook-creator, tentacle-creator, tentacle-orchestration, workflow-creator, find-skills, agent-instructions-auditor, forge-ecosystem, code-reviewer, task-step-generator) plus 10 hook templates for quality enforcement.

Unified hook runner architecture — 1 Python process per event with fail-open, HMAC-signed markers, audit logging, and tamper protection. Hook deployment is **Copilot CLI only**; Claude Code does not support the `hook_runner.py` format.

```bash
python3 ~/.copilot/tools/install.py --deploy-skill    # Deploy skill to project
python3 ~/.copilot/tools/install.py --deploy-hooks    # Deploy enforcement hooks (Copilot CLI)
python3 ~/.copilot/tools/install.py --lock-hooks      # Lock hooks (tamper protection)

# Project setup with a workflow profile
python3 ~/.copilot/tools/setup-project.py --profile python      # Python hook bundle + WORKFLOW.md
python3 ~/.copilot/tools/install-project-hooks.py --profile mobile  # Mobile hooks standalone

# Custom profile lifecycle
python3 ~/.copilot/tools/profile-builder.py --name myteam --hooks dangerous-blocker.sh --phases BUILD TEST COMMIT
python3 ~/.copilot/tools/profile-export.py --profile myteam --output myteam.json
python3 ~/.copilot/tools/profile-import.py --file myteam.json
```

**Session-start hooks** (`hooks/auto-briefing.py`) automatically refresh the codebase map
(`codebase-map.py`) at the start of each session — no manual step required.

**Session-end hooks** (`hooks/session-end.py`) are **reminder-only**: they never auto-save checkpoints.
Set `COPILOT_CHECKPOINT_REMIND=1` to log a reminder when a session ends without saved checkpoints.
To save a checkpoint yourself, run `python3 ~/.copilot/tools/checkpoint-save.py`.

📖 **Skills reference:** [docs/SKILLS.md](docs/SKILLS.md) · **Hooks reference:** [docs/HOOKS.md](docs/HOOKS.md)

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
python3 test_security.py    # injection, pickle, locks, paths
python3 test_fixes.py       # noise filter, sub-agent, launchd, SKILL.md, skill packaging
```

## FAQ

**Q: Does it work with Claude Code?**
A: Yes. `claude-adapter.py` parses Claude Code JSONL sessions into the common format.

**Q: Do I need an API key?**
A: No. API keys are optional — only needed for semantic search via embedding providers (OpenAI, Fireworks, OpenRouter). Without it, FTS5 keyword search and TF-IDF fallback work offline.

**Q: Where is the data stored?**
A: `~/.copilot/session-state/knowledge.db` — a single SQLite file with FTS5 indexes.

**Q: Does it work on Windows?**
A: Yes. All scripts include Windows encoding fixes. Use `python` instead of `python3`. See [Installation](#windows-powershell).

**Q: How do I update?**
A: `python3 ~/.copilot/tools/auto-update-tools.py --force` or just `git pull` (post-merge hook handles the rest).

**Q: Will hooks crash my AI agent?**
A: No. The unified hook runner uses fail-open architecture — if any rule crashes, it logs the error and allows the action to proceed.

## Contributing

See [CONTRIBUTING.md](CONTRIBUTING.md) for guidelines on reporting bugs, suggesting features, and submitting pull requests.

## License

[MIT](LICENSE) © [magicpro97](https://github.com/magicpro97)
