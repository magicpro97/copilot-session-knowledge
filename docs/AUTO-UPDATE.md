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
