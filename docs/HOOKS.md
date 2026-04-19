# Enforcement Hooks

> Cross-platform Python hooks that enforce knowledge-base usage across sessions.

## Architecture

Uses a **unified hook runner** — one `hook_runner.py` dispatcher per event instead of separate scripts. Reduces process overhead from ~11 Python processes per tool call to 1.

```
hooks/
  hook_runner.py          # Single entry point — dispatches to rules
  marker_auth.py          # HMAC-signed marker authentication
  rules/
    __init__.py           # Rule registry
    common.py             # Shared utilities (get_module, deny, info, etc.)
    briefing.py           # Auto-briefing + enforce-briefing
    learn_gate.py         # Enforce learn.py before commit/task_complete
    learn_reminder.py     # Remind to record learnings
    tentacle.py           # Tentacle enforce + suggest (merged)
    edit_tracker.py       # Track bash edits + test reminder (merged)
    error_kb.py           # Auto-search KB on errors
    integrity.py          # Verify hook file integrity
    session_lifecycle.py  # Session end cleanup
```

## Rules

| Rule | Event | Description |
|------|-------|-------------|
| `auto-briefing` | sessionStart | Auto-runs briefing.py + refreshes codebase-map.py, creates HMAC-signed marker |
| `integrity` | sessionStart | Verifies hook files via SHA256 manifest |
| `session-end` | sessionEnd | Cleans up marker files, writes session.log entry, opt-in checkpoint reminder (`COPILOT_CHECKPOINT_REMIND=1`) |
| `enforce-briefing` | preToolUse | Blocks edit/create/bash-writes until briefing done |
| `enforce-learn` | preToolUse | Blocks git commit AND task_complete without learn.py |
| `tentacle-enforce` | preToolUse | Blocks edits across ≥3 files in ≥2 modules without tentacle |
| `track-edits` | postToolUse | Detects file changes via `git status` (language-agnostic) |
| `learn-reminder` | postToolUse | Reminds to record learnings after task_complete |
| `test-reminder` | postToolUse | Reminds to run tests after 3+ Python file edits |
| `tentacle-suggest` | postToolUse | Suggests tentacle when edits span multiple modules |
| `error-kb` | errorOccurred | Auto-searches knowledge base on errors |
| `pre-commit` | git pre-commit | Validates `.agent.md` / `SKILL.md` via `lint-skills.py` |

## Key Features

- **Single process per event** — 1 Python process instead of 3-4
- **Fail-open** — rule errors/crashes don't block the agent
- **HMAC-signed counters** — all counters use HMAC (fixes plain counter bug)
- **Audit logging** — all decisions logged to `~/.copilot/markers/audit.jsonl`
- **Dry-run mode** — set `HOOK_DRY_RUN=1` to test without blocking
- **Merged duplicates** — tentacle enforce+suggest, track+test share code

## Bash Bypass Protection

Hooks detect file writes via bash commands (heredocs, redirects, `sed -i`, `tee`, `cp`, `mv`, `curl -o`, etc.) AND verify actual changes via `git status`.

## Tamper Protection

Hook files are locked with OS immutable flags:
- **macOS**: `chflags uchg` — user immutable
- **Linux**: `chattr +i` — requires root to modify
- **Windows**: `attrib +R` — read-only (weaker)

```bash
python3 ~/.copilot/tools/install.py --deploy-hooks   # Deploy hooks
python3 ~/.copilot/tools/install.py --lock-hooks      # Lock (AI can't modify)
python3 ~/.copilot/tools/install.py --unlock-hooks    # Unlock for updates
```

## Host Scope

Hook deployment is **Copilot CLI only** (`~/.copilot/hooks/`). Claude Code does not support
the Copilot CLI hook runner format (`hook_runner.py` / `hooks.json`). The global enforcement
hooks documented here run exclusively inside Copilot CLI sessions.

For project-level hooks (`.github/hooks/`) that enforce coding standards, commit gates, and
TDD pipelines, see [docs/SKILLS.md — Hook Templates](SKILLS.md) and the `hook-creator` skill.
Those hooks are registered via `hooks.json` / `review-policy.json` in the project repo and
are also **Copilot CLI only**.
