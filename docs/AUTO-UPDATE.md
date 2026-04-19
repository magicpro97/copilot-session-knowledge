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
| `skills/` or `templates/` | Redeploy session-knowledge SKILL (`templates/SKILL.md`) to projects |
| Embedding logic | Rebuild embeddings (background) |
| `auto-update-tools.py` itself | Self-exec with new code |
| Hook templates (`hooks/references/`) | Detected only — no auto-deploy (templates are copied manually) |

> **New root-level scripts:** Any Python script added to the root tools directory
> (`project-context.py`, `host_manifest.py`, `codebase-map.py`, etc.) is automatically
> covered by the `*.py` detection rule — the watcher service is restarted when they change.
>
> **`skills/` changes and references/:** When files under `skills/` change, auto-update calls
> `deploy_skills()`, which redeploys only `templates/SKILL.md` to already-deployed project
> destinations. The full skill `SKILL.md` files and their `references/` subdirectories under
> `skills/<name>/` are **not** individually re-deployed by auto-update. To pick up changes to
> skill bodies or references after a git pull, run `setup-project.py` (or `--deploy-skill`)
> manually in the target project.
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
