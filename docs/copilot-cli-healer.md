# copilot-cli-healer

Auto-detect and clean stale Copilot CLI pkg state caused by the upstream Node updater bug.

## Root Cause

GitHub Copilot CLI's auto-updater (Node.js, upstream) corrupts its own state on Windows:

1. Calls `fs.rename('~/.copilot/pkg/universal/<ver>', '~/.copilot/pkg/universal/.replaced-<ver>-<pid>-<ts>')` in its cleanup path **without checking whether the source exists** → `ENOENT` on first-run or partial-download.
2. On retry, leaves stale `.replaced-*` dirs and `pkg/tmp/<ver>-<pid>-<ts>/` partial downloads behind. State never self-heals.
3. `EPERM` when a dummy dir is present — Windows rejects rename-over-existing-dir.

**Exact error string you may see in your terminal:**

```
ENOENT: no such file or directory, rename '.../.copilot/pkg/universal/1.0.35' -> '.../.copilot/pkg/universal/.replaced-1.0.35-<pid>-<ts>'
```

Since the upstream bug cannot be patched by end users, this tool provides **three layers of client-side defence**:

| Layer | What | When |
|-------|------|------|
| L1 | `copilot-cli-healer.py` — detect + clean + retry | On demand / scheduled |
| L2 | Scheduled task / launchd / systemd | Daily at 10:00 |
| L3 | `auto-update-tools.py doctor` + `sessionStart` hook | Session start |

---

## Detection Rules (`--check`)

All detection is pure filesystem — no `copilot` process invoked:

| Pattern | Kind | Action |
|---------|------|--------|
| `pkg/universal/.replaced-*` | `replaced_dir` | Delete |
| Any entry under `pkg/tmp/` | `tmp_entry` | Delete (keep `tmp/` dir) |
| `pkg/universal/<ver>/` that is empty | `empty_dummy` | Delete |
| `pkg/universal/<ver>/` with total size < 1 KB | `corrupt_dir` | Delete |
| `pkg/` absent entirely | — | Exit 0, no-op |

---

## CLI

```
python copilot-cli-healer.py --status           # Summarise pkg dir state
python copilot-cli-healer.py --check            # Exit 0 = healthy, 1 = needs heal
python copilot-cli-healer.py --heal             # Clean stale state (idempotent)
python copilot-cli-healer.py --heal --dry-run   # Print planned actions, make none
python copilot-cli-healer.py --update           # Heal + `copilot update` (up to 3 retries)
python copilot-cli-healer.py --install-schedule   # Register daily OS task
python copilot-cli-healer.py --uninstall-schedule # Remove daily OS task
```

Exit codes:

| Code | Meaning |
|------|---------|
| 0 | Healthy (or healed successfully) |
| 1 | Issues detected (`--check`) or update failed |
| 2 | Another heal in progress (lock held) |

---

## Heal Procedure

1. Acquire `~/.copilot/pkg/.healer.lock` via `O_CREAT | O_EXCL` (no TOCTOU races).
2. Scan `pkg/universal/` for `.replaced-*` dirs, empty dummies, and corrupt dirs.
3. Scan `pkg/tmp/` for any entries.
4. Delete matched items using `shutil.rmtree` with a 3-retry / 500ms backoff loop (handles AV scanner and Explorer locks on Windows).
5. Never touch a non-empty, healthy version dir.
6. Release lock.

**`--update` flow:**

1. `heal()` — clean stale state
2. `subprocess.run(["copilot", "update"], timeout=120)`
3. On `ENOENT` / `EPERM` / `rename` / `.replaced-` in output → `heal()` again + retry (max 3 attempts, 2s backoff)
4. Exit 0 on success; 1 with actionable message on failure.

---

## Schedule Configuration

### Windows (Task Scheduler)

```powershell
python ~/.copilot/tools/copilot-cli-healer.py --install-schedule
# or via install.py:
python ~/.copilot/tools/install.py --install-healer
```

Registers a `CopilotCLIHealer` task that runs daily at 10:00.
XML task definition saved to `~/.copilot/session-state/copilot-healer-task.xml`.

### macOS (launchd)

```bash
python ~/.copilot/tools/copilot-cli-healer.py --install-schedule
# or via launchd installer:
bash ~/.copilot/tools/launchd/install-launchd.sh
```

Installs `~/Library/LaunchAgents/com.copilot.cli-healer.plist` (daily 10:00).
Log: `~/.copilot/session-state/.cli-healer.log`.

Template plist: `launchd/com.copilot.cli-healer.plist` (uses `__HOME__` / `__PYTHON3__` tokens, rendered by `install-launchd.sh`).

### Linux (systemd)

```bash
python ~/.copilot/tools/copilot-cli-healer.py --install-schedule
```

Installs `~/.config/systemd/user/copilot-cli-healer.{service,timer}` and enables the timer.

---

## sessionStart Hook

`hooks/copilot-cli-healer-check.py` runs at session start via `hook_runner.py`. It performs a quick filesystem scan (<500ms) and warns to stderr if stale state is detected:

```
  ⚠️  Copilot CLI pkg: stale state detected (2 issue(s))
       Stale .replaced-* dir: .replaced-1.0.35-12345-1714000000
  → Fix: python ~/.copilot/tools/copilot-cli-healer.py --heal
```

The hook does **not** auto-heal — it only notifies. Healing modifies the filesystem unexpectedly during a session.

---

## Environment Variables

| Variable | Purpose |
|----------|---------|
| `COPILOT_HEALER_PKG_DIR` | Override pkg directory (used by tests and CI) |

---

## Safety Guarantees

- **Idempotent** — running heal multiple times has the same effect as once.
- **Non-destructive** — healthy, non-empty version dirs are never deleted.
- **Concurrent-safe** — `O_CREAT | O_EXCL` lock prevents two simultaneous heals.
- **No shell-outs for deletion** — pure `shutil.rmtree` (no `cmd /c rmdir`).
- **Stdlib only** — zero pip dependencies.
- **Dry-run** — `--heal --dry-run` shows exactly what would be removed without doing it.

---

## Testing

```bash
python test_copilot_cli_healer.py
```

9 test cases covering: clean state, stale `.replaced-*`, stale `tmp/`, empty dummy dir, healthy dir preservation, absent pkg dir, concurrent lock, Windows path override, dry-run correctness.
