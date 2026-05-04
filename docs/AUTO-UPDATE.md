# Auto-Update

> Smart update mechanism that keeps tools current across machines.

## Commands

```bash
python3 ~/.copilot/tools/auto-update-tools.py           # Auto-update (24h cooldown)
python3 ~/.copilot/tools/auto-update-tools.py --force    # Force update now
python3 ~/.copilot/tools/auto-update-tools.py --check    # Check only (no apply)
python3 ~/.copilot/tools/auto-update-tools.py --status   # Show version info
python3 ~/.copilot/tools/auto-update-tools.py --doctor   # Health check + manifest verify
python3 ~/.copilot/tools/auto-update-tools.py --skip-pull # Run pipeline only (post-merge)
```

## Runtime Operator Commands

Read-only commands for monitoring the watch-sessions watcher and sync runtime.
None of these commands trigger an update or restart:

```bash
# Watcher status (delegates to sync-status.py --watch-status)
python3 ~/.copilot/tools/auto-update-tools.py --watch-status

# Sync runtime health check (delegates to sync-status.py --health-check)
python3 ~/.copilot/tools/auto-update-tools.py --health-check

# Runtime operations audit (delegates to sync-status.py --audit)
python3 ~/.copilot/tools/auto-update-tools.py --audit-runtime

# List all tracked paths/patterns used by the smart-diff coverage check
python3 ~/.copilot/tools/auto-update-tools.py --list-coverage
```

### Lifecycle command

`--restart-watch` is **not** read-only. It does not run `git pull` or the update pipeline, but it
does issue a controlled watcher restart through the active service-manager path (launchd, systemd,
Task Scheduler, or the manual fallback path).

```bash
# Restart the watch-sessions watcher (controlled restart, macOS/Linux/Windows)
python3 ~/.copilot/tools/auto-update-tools.py --restart-watch
```

### Watcher status semantics

`--watch-status` reports from two sources and reconciles them:

| Field | Meaning |
|---|---|
| `pid_running` | A `.watcher.lock` file exists and the PID in it is alive. This is the ground-truth liveness check. |
| `managed_by` | Which service manager owns the lifecycle (`launchd`, `systemd`, `task-scheduler`, or `none`). |
| `manager_state` | What the service manager reports: `active` (systemd), `loaded` (launchd), `registered` (Task Scheduler), or `inactive`/`unavailable`. |

**Normal healthy states:**
- `pid_running=True` + `manager_state=loaded` (macOS launchd): watcher is running, managed by LaunchAgent.
- `pid_running=True` + `managed_by=none`: watcher was started manually or by cron.
- `pid_running=False` + `manager_state=loaded` (macOS launchd, `com.copilot.watch-sessions` only): watcher is **not running** — the LaunchAgent is loaded but the process has exited or crashed. Use `--restart-watch` to recover.

**Audit exit codes:**
- Exit 0: all critical checks pass (warnings are informational).
- Exit 2: one or more critical checks failed (e.g. local DB missing).

### Health-check vs audit

| Command | Checks | Exit on failure |
|---|---|---|
| `--health-check` | DB exists + gateway OK (if configured) | exit 2 |
| `--audit-runtime` | DB + gateway + watcher + sync-config | exit 2 (critical only) |
| `--doctor` | Python + core tools + local DB + git + manifest + service-manager state + coverage audit (plus optional Copilot CLI healer check) | exit 0 (prints warnings) |

Use `--health-check` or `--audit-runtime` when you need gateway-aware sync checks.
`--doctor` is the broader local-install/runtime diagnostic surface; it does **not** replace gateway probing.

## Smart Pipeline

After `git pull`, auto-update analyzes `git diff` to run only what changed:

| Changed Files | Action |
|---|---|
| Python scripts (`*.py`) | Restart services |
| LaunchAgent templates (`launchd/`) | Reinstall LaunchAgents |
| `skills/` or `templates/` | Redeploy session-knowledge SKILL (`templates/SKILL.md`), update built-in skill bodies + assets for all skills in `BUILTIN_PROJECT_SKILLS` (e.g. `forge-ecosystem`) and vendored skills (e.g. `karpathy-guidelines`) to already-deployed **project** destinations, and refresh already-installed **global Copilot CLI** `~/.copilot/skills/<name>/` entries for vendored + built-in skills |
| Embedding logic | Rebuild embeddings (background) |
| `auto-update-tools.py` itself | Self-exec with new code |
| Hook templates (`hooks/references/`) | Detected only — no auto-deploy (templates are copied manually) |

> **New root-level scripts:** Any Python script added to the root tools directory
> (`project-context.py`, `host_manifest.py`, `codebase-map.py`, etc.) is automatically
> covered by the `*.py` detection rule — the watcher service is restarted when they change.
>
> **`skills/` changes and references/:** When files under `skills/` change, auto-update calls
> `deploy_skills()`, which does five things:
> (1) updates `templates/SKILL.md` (the session-knowledge skill) in already-deployed project destinations,
> (2) updates vendored skill bodies and asset subdirs for skills listed in `VENDORED_SKILLS`
> (currently `karpathy-guidelines`) in already-deployed project destinations,
> (3) updates non-vendored built-in project skill bodies and asset subdirs for skills listed in
> `BUILTIN_PROJECT_SKILLS` (including `forge-ecosystem` and all other skills deployed by
> `setup-project.py`) in already-deployed **Copilot CLI** project destinations (`.github/skills/<name>/`),
> (4) updates already-installed **global Copilot CLI** skill directories at `~/.copilot/skills/<name>/`
> for whitelisted vendored skills (currently `karpathy-guidelines`) using update-only behavior for directories and files,
> and
> (5) updates already-installed **global Copilot CLI** skill directories for `BUILTIN_PROJECT_SKILLS`
> entries using update-only behavior for directories, while syncing missing asset files inside those already-installed dirs.
> When auto-update runs inside WSL and
> can resolve the current Windows user's profile, it also refreshes that Windows Copilot CLI global
> skill directory — but only if it already exists there from a separate manual install. This is
> **Copilot CLI scope only** —
> `~/.claude/skills/` global installs are **not** touched by auto-update. All five operations are
> **update-only, don't-create** at the deployment-directory level — new skill deployments are never
> created automatically. Custom or third-party skill files not
> listed in `BUILTIN_PROJECT_SKILLS` or `VENDORED_SKILLS` are not re-deployed by auto-update; to pick
> up changes to those, run `setup-project.py` (or `install.py --deploy-skill`) manually in the
> target project.
>
> **Project discovery (registry-backed):** `deploy_skills()` finds which projects to update via
> `~/.copilot/session-state/tools-managed-projects.json`. A project is added to this registry
> whenever `setup-project.py` **or** `install.py --deploy-skill` performs a real deployment in
> that project. Projects that were set up by other means (manual file copies, etc.) and have never
> been run through either of those commands are not auto-updated from the tools-repo context; in
> that case, run `install.py --deploy-skill` once from inside the project to register it.
> As a fallback, `deploy_skills()` also checks the current git root (handles ad-hoc installs run
> directly from the target project).
>
> **Hook templates:** Files in `hooks/references/` are classified under the `hooks` category
> but auto-update intentionally does **not** deploy them — they are manually copied at project
> setup time via `hook-creator` or `setup-project.py`.
>
> **Git hook reinstall reminder:** when git-hook scripts (pre-commit/pre-push/check_subagent marker guard)
> change, auto-update prints an action-required warning and does **not** rewrite `.git/hooks/` across repos.
> Re-run `python3 ~/.copilot/tools/install.py --install-git-hooks` in each protected repo.

## Post-Merge Hook

Automatically installed in `.git/hooks/post-merge`. Triggers the pipeline on manual `git pull` too — no need to remember to restart services.

## Scheduled Updates

- **macOS**: LaunchAgent runs daily at 9 AM (`install-launchd.sh`)
- **Linux**: systemd timer or cron
- **Windows**: Task Scheduler

## Manual Shell Auto-Start

If not using LaunchAgents/systemd:

```bash
# Add to ~/.zshrc or ~/.bashrc
(python3 ~/.copilot/tools/auto-update-tools.py &) 2>/dev/null
```

## Version Manifest

After each update, `.update-manifest.json` is written to the tools directory with:
- Git SHA and timestamp
- Changed files count
- Pipeline actions taken
- Service status (running/stopped)

This file is **local runtime state** — it is listed in `.gitignore` and is never committed.
Each machine maintains its own copy reflecting its last update.

Use `--doctor` to verify the manifest and check overall health.

## Compatibility with the Browse Operator Console

The auto-update pipeline and the browse UI operator console (`/v2/chat`) are independent:

- Auto-update may restart `watch-sessions.py`, but it does **not** restart the browse server or interrupt an in-progress Copilot CLI run.
- Operator run history is persisted under `~/.copilot/session-state/operator-console/` and is reloaded by `browse/core/operator_console.py` on the next request.
- UI-only `browse-ui/dist/` updates are served from disk after rebuild/deploy. Python updates to `browse/api/operator.py` or `browse/core/operator_console.py` still require a manual browse server restart.

After pulling a release that changes the Python operator backend, restart the browse server:

```bash
python3 ~/.copilot/tools/browse.py --port <port>
```

## Compatibility with browse-wide host state

The host selection layer (`host-provider.tsx`, `host-profiles.ts`) is purely client-side (localStorage). Auto-update has no direct interaction with it:

- Auto-update rebuilds or deploys `browse-ui/dist/` when browse-ui source files change — this includes any new version of the host-management components.
- Host profiles stored in `localStorage` by the browser are unaffected by auto-update, browse server restarts, or `browse-ui/dist/` rebuilds.
- After a rebuild, the new `dist/` takes effect immediately on the next page load — host profiles already saved in `localStorage` are preserved.
