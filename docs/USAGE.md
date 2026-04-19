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
brief --task "memory-surface"        # Task-scoped recall: entries tagged with this task ID
```

## Search

```bash
qs "search terms"                    # Compact results
qs "search terms" --verbose          # Full content
qs "docker" --type research          # Filter by doc type
qs "spring" --source copilot         # Filter by agent source
qs --mistakes                        # View past errors
qs --patterns                        # View best practices
qs --decisions                       # View architecture decisions
qs --file src/auth.py                # Entries that touched a specific file
qs --module auth                     # Entries for a module or directory
qs --task memory-surface             # Entries tagged with a specific task ID
qs --diff                            # Entries for files in the current git diff
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
python3 ~/.copilot/tools/install.py --deploy-hooks              # Deploy hooks
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
