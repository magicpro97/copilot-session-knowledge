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
| `skills/` or `templates/` | Redeploy session-knowledge SKILL (`templates/SKILL.md`), update vendored skill bodies + assets (e.g. `karpathy-guidelines`) to already-deployed **project** destinations, and update already-installed **global** `~/.copilot/skills/<name>/` entries |
| Embedding logic | Rebuild embeddings (background) |
| `auto-update-tools.py` itself | Self-exec with new code |
| Hook templates (`hooks/references/`) | Detected only — no auto-deploy (templates are copied manually) |

> **New root-level scripts:** Any Python script added to the root tools directory
> (`project-context.py`, `host_manifest.py`, `codebase-map.py`, etc.) is automatically
> covered by the `*.py` detection rule — the watcher service is restarted when they change.
>
> **`skills/` changes and references/:** When files under `skills/` change, auto-update calls
> `deploy_skills()`, which does three things:
> (1) updates `templates/SKILL.md` (the session-knowledge skill) in already-deployed project destinations,
> (2) updates vendored skill bodies and asset subdirs for skills listed in `VENDORED_SKILLS`
> (currently `karpathy-guidelines`) in already-deployed project destinations, and
> (3) updates already-installed **global Copilot CLI** skill directories at `~/.copilot/skills/<name>/`
> for whitelisted vendored skills (currently `karpathy-guidelines`). This is **Copilot CLI scope only** —
> `~/.claude/skills/` global installs are **not** touched by auto-update. All three operations follow an
> **update-only, don't-create** rule — files are only updated if they already exist at the target
> location; new deployments are never created automatically. Non-vendored skill `SKILL.md` files and
> their `references/` subdirectories under `skills/<name>/` are **not** re-deployed by auto-update.
> To pick up changes to those skill bodies or references after a git pull, run `setup-project.py`
> (or `install.py --deploy-skill`) manually in the target project.
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

After each update, `.update-manifest.json` records:
- Git SHA and timestamp
- Changed files count
- Pipeline actions taken
- Service status (running/stopped)

Use `--doctor` to verify the manifest and check overall health.
