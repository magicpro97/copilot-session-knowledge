# Operator Playbook

> Day-to-day health monitoring, maintenance, and troubleshooting for copilot-session-knowledge operators.

## Health Checks

### Knowledge base health

```bash
python3 ~/.copilot/tools/index-status.py              # Row counts, FTS integrity, event-offset coverage
python3 ~/.copilot/tools/knowledge-health.py           # Full health dashboard
python3 ~/.copilot/tools/knowledge-health.py --recall  # Recall-only telemetry
python3 ~/.copilot/tools/knowledge-health.py --recall --json  # Machine-readable recall stats
```

### Sync health

```bash
python3 ~/.copilot/tools/sync-status.py               # Local sync state summary
python3 ~/.copilot/tools/sync-status.py --health-check --json  # Exit 0/2 health check
python3 ~/.copilot/tools/sync-status.py --audit --json         # Detailed audit
python3 ~/.copilot/tools/sync-status.py --watch-status --json  # File-watcher status
```

### Runtime health

```bash
python3 ~/.copilot/tools/auto-update-tools.py --doctor        # Auto-update pipeline health
python3 ~/.copilot/tools/auto-update-tools.py --watch-status  # Watcher daemon status
python3 ~/.copilot/tools/auto-update-tools.py --health-check  # Exit-code health check
python3 ~/.copilot/tools/auto-update-tools.py --audit-runtime # Runtime audit
```

---

## Auto-Update

```bash
python3 ~/.copilot/tools/auto-update-tools.py          # Auto-update (24h cooldown)
python3 ~/.copilot/tools/auto-update-tools.py --force  # Force update now
python3 ~/.copilot/tools/auto-update-tools.py --restart-watch  # Restart watcher daemon
```

The smart pipeline analyzes `git diff` to run only what changed. The post-merge hook auto-triggers on `git pull`.

> **After major updates:** re-run `python3 ~/.copilot/tools/install.py --install-git-hooks` in every protected repo to refresh per-repo git hooks.

📖 Full auto-update reference: [docs/AUTO-UPDATE.md](AUTO-UPDATE.md)

---

## Hook Maintenance

```bash
# Deploy / re-deploy hooks
python3 ~/.copilot/tools/install.py --deploy-hooks

# Lock hooks against AI modification (OS immutable flags)
python3 ~/.copilot/tools/install.py --lock-hooks

# Unlock for updates
python3 ~/.copilot/tools/install.py --unlock-hooks

# Install per-repo git-level subagent guard
python3 ~/.copilot/tools/install.py --install-git-hooks
```

### Dry-run mode

Test hook behavior without blocking:

```bash
HOOK_DRY_RUN=1 python3 ~/.copilot/hooks/hook_runner.py preToolUse
```

### Audit log

Every hook decision is logged:

```bash
tail -f ~/.copilot/markers/audit.jsonl
```

---

## DB Migrations

```bash
python3 ~/.copilot/tools/migrate.py     # Apply all pending migrations
```

Migrations are versioned in `migrate.py`'s `MIGRATIONS` list. Running `migrate.py` is idempotent — it only applies migrations not already applied.

Current schema: **v8** (`sessions_fts` contentless FTS5 + BM25).

---

## Watcher Management

```bash
# Start watcher manually
python3 ~/.copilot/tools/watch-sessions.py

# macOS: via LaunchAgent (auto-start on login)
launchctl load ~/Library/LaunchAgents/com.copilot.watch-sessions.plist

# Restart via auto-update operator surface
python3 ~/.copilot/tools/auto-update-tools.py --restart-watch
```

The watcher uses adaptive polling: 5 s / 30 s / 300 s tiers based on session activity.

---

## Troubleshooting

### Copilot CLI auto-heal

If `copilot update` fails with `ENOENT` or `EPERM` on a rename inside `pkg/universal/`:

```bash
python3 ~/.copilot/tools/copilot-cli-healer.py --status   # Diagnose
python3 ~/.copilot/tools/copilot-cli-healer.py --heal     # Fix
python3 ~/.copilot/tools/copilot-cli-healer.py --update   # Heal + retry copilot update
```

Root cause: upstream Node updater calls `fs.rename(src, dst)` without checking that `src` exists, leaving stale `.replaced-*` dirs behind.

Prevent recurrence by scheduling a daily heal:

```bash
python3 ~/.copilot/tools/copilot-cli-healer.py --install-schedule
# or:
python3 ~/.copilot/tools/install.py --install-healer
```

📖 Details: [docs/copilot-cli-healer.md](copilot-cli-healer.md)

### Stuck dispatched-subagent marker

If `git commit` is blocked by a stale `dispatched-subagent-active` marker:

```bash
# Preferred: complete the tentacle cleanly
python3 ~/.copilot/tools/tentacle.py complete <name>

# Emergency: remove marker directly
rm ~/.copilot/markers/dispatched-subagent-active
```

Markers expire automatically after 4 hours (TTL dead-man switch). After clearing, re-dispatch any tentacles still in flight.

### FTS integrity errors

```bash
python3 ~/.copilot/tools/index-status.py   # Check FTS integrity
python3 ~/.copilot/tools/build-session-index.py  # Rebuild index
```

### Sync not working

```bash
python3 ~/.copilot/tools/sync-config.py --status --json  # Check config
python3 ~/.copilot/tools/sync-status.py --health-check   # Health check
python3 ~/.copilot/tools/sync-daemon.py --once           # Manual one-shot sync
```

Common issues:
- `connection_string` not set → daemon is local-only/idle (not an error)
- Gateway URL must be HTTP/HTTPS — not a raw Postgres/libSQL DSN
- `sync-gateway.py` is reference/mock only — use a real gateway in production

### Knowledge base not growing

```bash
# Trigger a manual re-index
python3 ~/.copilot/tools/build-session-index.py
python3 ~/.copilot/tools/extract-knowledge.py
```

If running in watch mode, check watcher status:

```bash
python3 ~/.copilot/tools/auto-update-tools.py --watch-status
```

---

## Quality Gates

| Gate | What it checks | How to run |
|------|---------------|-----------|
| `scripts/check_syntax.py` | `py_compile` every `.py` file | `python3 ~/.copilot/tools/scripts/check_syntax.py` |
| `run_all_tests.py` | Discovers and runs all `test_*.py` files | `python3 ~/.copilot/tools/run_all_tests.py` |
| `hooks/rules/syntax_gate.py` | Blocks bad `.py` edits at hook level | Automatic via `hook_runner.py preToolUse` |
| CI (`ci.yml`) | Syntax check + all tests on push/PR | GitHub Actions (ubuntu-latest, Python 3.11) |

Pre-commit checklist:

```bash
python3 ~/.copilot/tools/scripts/check_syntax.py
python3 ~/.copilot/tools/test_security.py
python3 ~/.copilot/tools/test_fixes.py
git diff --stat
```

---

## Checkpoint Lifecycle

```bash
# Save a checkpoint
python3 ~/.copilot/tools/checkpoint-save.py --title "Auth done" --overview "JWT added"

# List checkpoints
python3 ~/.copilot/tools/checkpoint-restore.py --list

# Restore / inspect
python3 ~/.copilot/tools/checkpoint-restore.py --show latest
python3 ~/.copilot/tools/checkpoint-restore.py --export latest --format json

# Diff checkpoints
python3 ~/.copilot/tools/checkpoint-diff.py --from 1 --to latest
python3 ~/.copilot/tools/checkpoint-diff.py --summary
```

Hooks **never** auto-save checkpoints. Save them manually at meaningful milestones.

---

## Tentacle Operator View

```bash
# Dashboard: all tentacles and states
python3 ~/.copilot/tools/tentacle.py status

# Next step for a specific tentacle
python3 ~/.copilot/tools/tentacle.py next-step <name>
python3 ~/.copilot/tools/tentacle.py next-step <name> --all     # All pending todos
python3 ~/.copilot/tools/tentacle.py next-step <name> --format json

# Verify and close
python3 ~/.copilot/tools/tentacle.py verify <name> "python3 test_fixes.py" --label "tests"
python3 ~/.copilot/tools/tentacle.py handoff <name> "Summary" --learn
python3 ~/.copilot/tools/tentacle.py complete <name>
```

> Full tentacle workflow: **[docs/USAGE.md](USAGE.md#tentacle-orchestration)**

---

## Browse UI — Operator Diagnostics Settings Page

The `/v2/settings/` page in the Browse UI is the primary **browser-based operator surface**.
All diagnostic panels are read-only — no write operations are exposed.

| Card | API endpoint | Shows |
|------|-------------|-------|
| Sync diagnostics | `/api/sync/status` | Mode, pending txns, failed ops, gateway config, rollout guidance |
| Trend Scout diagnostics | `/api/scout/status` | Config, grace window, audit checks, discovery lanes |
| Tentacle runtime diagnostics | `/api/tentacles/status` | Active tentacles, dispatch marker, registry, audit checks |
| Skill outcome metrics | `/api/skills/metrics` | Pass rate, outcomes, skill usage summary |
| System health | `/healthz` | DB schema version, session count, knowledge entries, last indexed |

Each card with live data also renders an **Operator checks (read-only)** panel that lists
safe CLI commands the operator can **copy** to their terminal. The browser never executes
commands — the panel is display-only with copy-to-clipboard buttons.

Navigate to the Settings page:
```
http://localhost:<port>/v2/settings/?token=<token>
```

---



## Retrospective

View the composite operator score across knowledge, skills, hooks, and git signals.

```bash
# CLI — full text report
python3 ~/.copilot/tools/retro.py

# Repo-only (safe in CI; no local DB needed)
python3 ~/.copilot/tools/retro.py --mode repo

# JSON payload (stable contract consumed by the browse API)
python3 ~/.copilot/tools/retro.py --json

# Single score line
python3 ~/.copilot/tools/retro.py --score

# One section only: knowledge | skills | hooks | git
python3 ~/.copilot/tools/retro.py --subreport knowledge
```

Browse UI: the **Retrospective** collapsible panel on the Insights → Dashboard tab
fetches `/api/retro/summary?mode=repo` and shows the composite score and per-section
subscores. It fails gracefully if the API is unavailable.

Standalone retro HTML page: `http://localhost:<port>/retro?token=<token>` renders
the same payload in a lightweight page suitable for quick browser-based checks.
The page fetches `/api/retro/summary?mode=repo` and renders grade, subscores, and
a link to the full JSON payload. Gracefully shows an error message if unavailable.

GitHub Actions: trigger **Retrospective** (`retro.yml`) via `workflow_dispatch` to run
`retro.py --mode repo --json`, produce a markdown summary artifact, and write to the
job summary. Read-only — no issues, commits, or DB writes.
