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
| Python scripts | Restart services |
| LaunchAgent templates | Reinstall LaunchAgents |
| SKILL.md templates | Redeploy to projects |
| Embedding logic | Rebuild embeddings (background) |
| `auto-update-tools.py` itself | Self-exec with new code |

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
