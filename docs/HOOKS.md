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
| `tentacle-enforce` | preToolUse | Blocks (deny) edits once ≥3 files across ≥2 modules are reached without tentacle setup. The deny message contains convention-level guidance: if you are the **orchestrator**, follow the runtime-bundle workflow — `tentacle.py create <name> --scope "<paths>" --desc "<desc>" --briefing` → `tentacle.py todo <name> add "<task>"` → `tentacle.py swarm <name> --agent-type general-purpose --model claude-sonnet-4.6`; if you are a **dispatched sub-agent**, stay within your declared scope, write any scope gaps to `handoff.md`, and by convention avoid `git commit`/`git push`. |
| `subagent-git-guard` | preToolUse | **Defense-in-depth**: blocks `git commit`/`git push` bash commands when the `dispatched-subagent-active` marker is fresh. This is a secondary surface — **not** the primary enforcement path (see §Dispatched-Subagent Git Guard below). Whether `preToolUse` fires inside a delegated subagent context is not guaranteed by the platform. |
| `track-edits` | postToolUse | Detects file changes via `git status` (language-agnostic) |
| `learn-reminder` | postToolUse | Reminds to record learnings after task_complete |
| `test-reminder` | postToolUse | Reminds to run tests after 3+ Python file edits |
| `tentacle-suggest` | postToolUse | Suggests tentacle when edits reach ≥3 files across ≥2 modules (same threshold as tentacle-enforce) |
| `error-kb` | errorOccurred | Auto-searches knowledge base on errors |
| `pre-commit` | git pre-commit | (1) Blocks commit when `dispatched-subagent-active` marker is fresh (primary subagent guard); (2) validates `.agent.md` / `SKILL.md` via `lint-skills.py`. Requires `install.py --install-git-hooks`. |
| `pre-push` | git pre-push | Blocks push when `dispatched-subagent-active` marker is fresh. Requires `install.py --install-git-hooks`. |

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
python3 ~/.copilot/tools/install.py --deploy-hooks       # Deploy Copilot CLI hooks
python3 ~/.copilot/tools/install.py --lock-hooks          # Lock (AI can't modify)
python3 ~/.copilot/tools/install.py --unlock-hooks        # Unlock for updates
python3 ~/.copilot/tools/install.py --install-git-hooks   # Install pre-commit/pre-push into current repo
```

> **Note:** `--install-git-hooks` must be run separately per repository to install the git-level
> subagent guard. It is not performed automatically by `--deploy-hooks`. Re-run after major tool
> updates to refresh hook scripts in `.git/hooks/`.

## Dispatched-Subagent Git Guard

Phase 3 adds git-level enforcement that blocks `git commit` and `git push` while a dispatched
subagent session is active. The design is **marker-based** rather than hook-only because
`preToolUse` hook inheritance inside delegated/background agent contexts is not guaranteed by
the platform — a hook that fires reliably in the orchestrator session may silently not fire
inside a `task()`-spawned subagent.

### How it works

**Step 1 — Marker write (orchestrator, via `tentacle.py swarm`)**

When `tentacle.py swarm` dispatches a subagent, it writes an HMAC-signed marker file:

```
~/.copilot/markers/dispatched-subagent-active
```

The marker is a JSON file with the following contract:

| Field | Description |
|-------|-------------|
| `name` | Always `"dispatched-subagent-active"` |
| `ts` | UNIX timestamp of the most-recent write (used for HMAC + TTL) |
| `sig` | HMAC-SHA256 over `"name:ts"` (omitted when no secret is configured) |
| `active_tentacles` | Ordered list of tentacle names currently dispatched (deduped, concurrency-safe) |
| `scope` | File-scope list from the most-recently-dispatching tentacle |
| `dispatch_mode` | Dispatch mode of the most-recently-dispatching tentacle |
| `ttl_seconds` | Expected lifetime; consumers treat markers older than this as stale |
| `written_at` | ISO 8601 human-readable timestamp |

**Concurrent tentacles:** Multiple tentacles dispatched in parallel each add their name to
`active_tentacles` rather than overwriting the file. `tentacle.py complete <name>` removes only
that tentacle's entry; the marker is deleted only when `active_tentacles` becomes empty.

**Step 2 — Git pre-commit / pre-push (primary enforcement)**

`hooks/check_subagent_marker.py` is called by both `hooks/pre-commit` and `hooks/pre-push`.
When the marker is present, auth-valid, and within the 4-hour TTL, it exits with code 1 and
prints a diagnostic message — blocking the git operation.

This is the **primary enforcement surface**: git hooks fire at the filesystem level for any
`git commit` or `git push` call, regardless of which agent spawned the process.

**Step 3 — `preToolUse` guard (defense-in-depth, secondary)**

`hooks/rules/subagent_guard.py` (`SubagentGitGuardRule`) checks the same marker on every
`preToolUse` event that contains a `git commit` or `git push` bash command. This provides a
second interception point when `preToolUse` does fire inside the subagent. However, it is
**not the primary path** — whether `preToolUse` events from the parent `hooks.json` propagate
into a delegated subagent context is undefined by the platform.

**Step 4 — Marker cleanup**

`tentacle.py complete <name>` removes the tentacle's entry from `active_tentacles`. The marker
file is deleted when the list becomes empty. The 4-hour TTL acts as a dead-man switch for
sessions that crash without calling `complete`.

### Enforcement scope

> **Local-only.** This enforcement covers local git operations on the machine where the tools
> are installed. It does **not** cover:
>
> - Cloud-hosted or remote-delegated agent runs (hooks.json is not copied to cloud environments)
> - Any environment where git hooks are not installed (`install.py --install-git-hooks`)
> - Manual filesystem operations that bypass git (direct file writes without committing)

### Installing the git hooks

The git-level guard requires installation per repository:

```bash
# Install into the current repo's .git/hooks/
python3 ~/.copilot/tools/install.py --install-git-hooks

# On Windows (PowerShell)
python "$env:USERPROFILE\.copilot\tools\install.py" --install-git-hooks
```

`install.py --install-git-hooks` also sets `core.hooksPath = .git/hooks` in the repository
config to ensure the hooks fire even when a project-level override is present.

After tool updates (`git pull` or `auto-update-tools.py --force`), re-run
`--install-git-hooks` to refresh the hook scripts in `.git/hooks/`.

### Fail-open behavior

All enforcement surfaces are fail-open:

- Missing marker → allow (no false positives)
- Stale marker (age ≥ 4 hours) → allow
- HMAC auth failure (marker tampered or written without secret) → allow
- Any unexpected error in `check_subagent_marker.py` → allow

To clear a stuck marker manually:

```bash
python3 ~/.copilot/tools/tentacle.py complete <name>
# or delete the marker file directly:
rm ~/.copilot/markers/dispatched-subagent-active
```

## Host Scope

Hook deployment is **Copilot CLI only** (`~/.copilot/hooks/`). Claude Code does not support
the Copilot CLI hook runner format (`hook_runner.py` / `hooks.json`). The global enforcement
hooks documented here run exclusively inside Copilot CLI sessions.

For project-level hooks (`.github/hooks/`) that enforce coding standards, commit gates, and
TDD pipelines, see [docs/SKILLS.md — Hook Templates](SKILLS.md) and the `hook-creator` skill.
Those hooks are registered via `hooks.json` / `review-policy.json` in the project repo and
are also **Copilot CLI only**.

## Load Awareness

The unified hook runner (`hook_runner.py`) is **not** a significant context-load contributor: it
runs as a single Python process per event type, outside the LLM context window, and its output
(markers, audit log entries) does not increase prompt tokens.

Context load problems come from instruction surfaces and skill duplication, not hooks:

| Root cause | Effect | Remedy |
|---|---|---|
| Skill deployed at both `~/.copilot/skills/` and `.github/skills/` | Skill appears twice in catalog; Copilot deduplicates by name but extra copy adds noise | Remove project copy once globally deployed — see [docs/SKILLS.md](SKILLS.md#meta-skill-rollout--global-vs-project-scope) |
| Instruction file with `applyTo: '**/*'` | File is injected into every context, including trivial ones | Narrow `applyTo` to the file patterns that actually need the instruction |
| Same instruction deployed at both user-level and project-level | Duplicate injection on every context | Remove the project copy; keep only the user-level one |

Hook rules themselves follow a **minimal-output-first** discipline: `deny()` and `info()` outputs
are kept short; verbose details are written to `~/.copilot/markers/audit.jsonl` only, not surfaced
as inline context. If a hook rule needs to escalate (e.g., the briefing gate hasn't fired), it
blocks with a single concise message — it does not dump session history into the prompt.
