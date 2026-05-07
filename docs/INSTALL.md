# Installation Guide

> Step-by-step install walkthrough for copilot-session-knowledge.
>
> **Quick start (3 commands):** see [README.md](../README.md#quick-start).  
> This guide covers all install methods, post-install verification, and upgrade paths.

## Prerequisites

- **Python 3.10+** (no pip packages needed — pure stdlib)
- **Git** (for cloning and auto-update)
- **Copilot CLI** (`~/.copilot/session-state/`) and/or **Claude Code** (JSONL sessions at `~/.claude/`)
- macOS / Linux / Windows (WSL or PowerShell)

> On macOS/Linux, use `python3`. On Windows, use `python` or `py`.

---

## Method 1 — Recommended (auto-update enabled)

Clone directly to `~/.copilot/tools/` so the auto-update pipeline and LaunchAgent paths work out of the box.

```bash
# 1. Clone
git clone https://github.com/magicpro97/copilot-session-knowledge.git ~/.copilot/tools

# 2. Build knowledge base from existing sessions
python3 ~/.copilot/tools/build-session-index.py
python3 ~/.copilot/tools/extract-knowledge.py

# 3. Apply DB migrations
python3 ~/.copilot/tools/migrate.py

# 4. Verify install — also auto-provisions the sk launcher on your PATH
python3 ~/.copilot/tools/install.py --test
```

After step 4, the `sk` launcher is available on your PATH. Verify with `sk --help`.
On Windows PowerShell without a PATH update, run `python ~/.copilot/tools/sk.py` as the equivalent fallback.

### macOS — LaunchAgent (auto-start on login)

```bash
# Install watcher + daily auto-update LaunchAgents
python3 ~/.copilot/tools/launchd/install-launchd.py
```

This installs two LaunchAgents:
- `watch-sessions` — starts automatically at login, watches for new session files
- `auto-update-tools` — runs daily at a fixed time, pulls the latest tools

---

## Method 2 — Manual Copy

Use this if you want to clone elsewhere and copy the repository contents manually.

```bash
git clone https://github.com/magicpro97/copilot-session-knowledge.git
cd copilot-session-knowledge
mkdir -p ~/.copilot/tools
rsync -a --exclude '.git' ./ ~/.copilot/tools/
```

> **Note:** auto-update and some path resolution assumes `~/.copilot/tools/`. If you install elsewhere, adjust `DB_PATH` in each script accordingly.

---

## Method 3 — Windows (PowerShell)

```powershell
git clone https://github.com/magicpro97/copilot-session-knowledge.git
cd copilot-session-knowledge
New-Item -ItemType Directory -Force "$env:USERPROFILE\.copilot\tools"
Copy-Item * "$env:USERPROFILE\.copilot\tools\" -Recurse -Force
python "$env:USERPROFILE\.copilot\tools\build-session-index.py"
python "$env:USERPROFILE\.copilot\tools\extract-knowledge.py"
python "$env:USERPROFILE\.copilot\tools\migrate.py"
```

> POSIX-style home paths from Git Bash (`/c/Users/...`), WSL (`/mnt/c/...`), and Cygwin (`/cygdrive/c/...`) are automatically normalised to native Windows paths for marker lookups.

---

## Method 4 — Alternative: editable pip install

The standard install auto-provisions `sk` via the launcher. Use this method only if you need a formal console-script entry point (e.g., system-wide packaging or non-standard PATH environments).

```bash
git clone https://github.com/magicpro97/copilot-session-knowledge.git ~/.copilot/tools
python3 -m pip install -e ~/.copilot/tools

# Now the unified front door is available as a pip-registered console command
sk briefing "test query"
sk index status
```

Notes:
- This is additive. All direct `python3 ~/.copilot/tools/<script>.py` workflows still work.
- If you move the repo later, reinstall or set `SK_TOOLS_DIR=/new/path/to/copilot-session-knowledge`.
- Before removing the checkout, run `python3 -m pip uninstall copilot-session-knowledge` so the pip-registered `sk` wrapper is removed cleanly.
- For most users, the auto-provisioned launcher from Method 1 is sufficient; this method adds pip packaging overhead.

---

## Post-Install Verification

```bash
# Check index status
sk index status

# Get a test briefing
sk briefing "test query"

# Run the test suites
python3 ~/.copilot/tools/test_security.py
python3 ~/.copilot/tools/test_fixes.py
```

---

## The `sk` Unified CLI

After the standard install (`install.py --test`), the `sk` command is automatically available on your PATH:

```bash
sk briefing "your task"
sk index build && sk index extract
sk query "docker error"
```

`sk` is a thin dispatcher — it routes sub-commands to the underlying standalone scripts without adding business logic. All scripts remain directly invocable as a fallback.

### When to use `sk` vs direct scripts

| Situation | Recommended |
|-----------|-------------|
| Day-to-day usage after standard install | `sk <command>` |
| Passing extra flags not yet surfaced by `sk` | `python3 ~/.copilot/tools/<script>.py <flags>` |
| CI / automation pipelines (explicit, reproducible) | Direct script invocation |
| Windows (PowerShell, no PATH update) | `python ~/.copilot/tools/sk.py <command>` |
| Debugging a specific script | Direct script invocation |

---

## Deploy AI Integration

### Skill deployment (Copilot CLI + Claude Code)

```bash
# Deploy session-knowledge skill to current project
python3 ~/.copilot/tools/install.py --deploy-skill
# → .github/skills/session-knowledge/SKILL.md  (Copilot CLI)
# → .claude/skills/session-knowledge/SKILL.md   (Claude Code)
```

### Enforce AI usage (mandatory, not optional)

Skills are suggestions — AI agents can skip them. To enforce usage:

```bash
python3 ~/.copilot/tools/install.py --inject-global
# → Adds "🧠 Session Knowledge — MANDATORY" section to ~/.github/copilot-instructions.md
```

### Hook deployment (Copilot CLI only)

```bash
python3 ~/.copilot/tools/install.py --deploy-hooks        # Deploy enforcement hooks
python3 ~/.copilot/tools/install.py --lock-hooks          # Lock against AI modification
python3 ~/.copilot/tools/install.py --install-git-hooks   # Install pre-commit/pre-push (per repo)
```

### Full project setup (recommended for new projects)

```bash
python3 ~/.copilot/tools/setup-project.py --profile python      # Python profile
python3 ~/.copilot/tools/setup-project.py --profile typescript  # TypeScript profile
python3 ~/.copilot/tools/setup-project.py --profile mobile      # Android/iOS/KMP profile
python3 ~/.copilot/tools/setup-project.py --profile fullstack   # Full-stack web profile
```

---

## Shell Aliases (optional — fallback for non-standard environments)

The `sk` launcher is provisioned automatically by the standard install. Shell aliases below are useful if the launcher is unavailable (e.g., non-standard PATH on some shells, Windows without a PATH update, or minimal CI environments).

```bash
# Add to ~/.bashrc or ~/.zshrc (optional backup)

# Unified sk front door
alias sk='python3 ~/.copilot/tools/sk.py'

# Legacy per-script aliases (still work; use sk above if available)
alias qs='python3 ~/.copilot/tools/query-session.py'
alias brief='python3 ~/.copilot/tools/briefing.py'
alias learn='python3 ~/.copilot/tools/learn.py'
```

With the `sk` alias you can run: `sk briefing "task"`, `sk query "docker"`, `sk index build`, etc. See [docs/USAGE.md](USAGE.md#sk--unified-cli) for the full command surface.

---

## Upgrading

```bash
# Auto-update (24h cooldown)
sk update

# Force update now
sk update --force

# Or: plain git pull (post-merge hook runs the update pipeline automatically)
cd ~/.copilot/tools && git pull
```

> **After tool updates:** the `sk` launcher stays current automatically (it points to the checkout). Re-run `sk install --install-git-hooks` in every protected repo to refresh per-repo git hooks.

---

## Optional: Sync Setup

To sync your knowledge base across machines (optional, local-first):

```bash
# Point at a sync gateway (HTTP/HTTPS only, not a raw DB DSN)
python3 ~/.copilot/tools/sync-config.py --setup https://your-gateway.example.com

# Or use an env var
python3 ~/.copilot/tools/sync-config.py --setup-env SYNC_GATEWAY_URL

# Run a one-shot sync
python3 ~/.copilot/tools/sync-daemon.py --once

# Start as a background daemon
python3 ~/.copilot/tools/sync-daemon.py --daemon
```

> Full sync reference: **[docs/USAGE.md](USAGE.md#sync-rollout)**

---

## Uninstall

```bash
# Remove the sk launcher (auto-provisioned by install.py --test)
python3 ~/.copilot/tools/install.py --uninstall-launcher 2>/dev/null || true

# If you also used the editable pip install, remove that console command
python3 -m pip uninstall copilot-session-knowledge 2>/dev/null || true

# Then remove tools
python3 ~/.copilot/tools/install.py --uninstall
# or: rm -rf ~/.copilot/tools/

# Remove knowledge DB (data loss — back up first)
rm ~/.copilot/session-state/knowledge.db

# Remove LaunchAgents (macOS)
launchctl unload ~/Library/LaunchAgents/com.copilot.watch-sessions.plist 2>/dev/null || true
launchctl unload ~/Library/LaunchAgents/com.copilot.auto-update.plist 2>/dev/null || true
rm -f ~/Library/LaunchAgents/com.copilot.watch-sessions.plist
rm -f ~/Library/LaunchAgents/com.copilot.auto-update.plist

# Remove deployed hooks
rm -rf ~/.copilot/hooks/

# Remove global AI instructions injection (edit manually)
# ~/.github/copilot-instructions.md — remove the "🧠 Session Knowledge" section
```
