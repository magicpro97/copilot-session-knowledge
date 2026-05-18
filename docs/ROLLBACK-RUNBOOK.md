# Rollback and Fallback Runbook

Operator guide for reverting failed installer, database, Rust binary, and hook
changes. Run commands from the repository root unless a section says otherwise.

## 1. Installer rollback

Use this when `install.py`, `auto-update-tools.py`, or a local tool refresh leaves
the launcher or copied tools in a bad state.

### 1.1 Snapshot before risky installer work

```bash
python install.py --doctor --manifest > /tmp/sk-install-before.txt
python install.py --install-sk --quiet
```

### 1.2 Remove only the managed `sk` launcher

```bash
python install.py --uninstall-launcher
python install.py --doctor --manifest
```

### 1.3 Remove managed copied tools while preserving session data

```bash
python install.py --uninstall
```

`--uninstall` preserves `~/.copilot/session-state/` and the knowledge database.
If the checkout was installed with editable pip, remove that wrapper first:

```bash
python -m pip uninstall copilot-session-knowledge
python install.py --uninstall
```

### 1.4 Reinstall from a known-good checkout

```bash
git fetch origin main
git switch main
git pull --ff-only
python install.py --install-sk --quiet
python install.py --doctor --manifest
```

## 2. Database schema rollback

Use this before migrations, manual repair, or any change that writes
`~/.copilot/session-state/knowledge.db`.

### 2.1 Create a verified rollback backup

POSIX:

```bash
python migrate.py ~/.copilot/session-state/knowledge.db --backup-only --backup-path /tmp/knowledge.db.backup
python migrate.py ~/.copilot/session-state/knowledge.db
```

Windows PowerShell:

```powershell
New-Item -ItemType Directory -Force -Path "C:\Temp" | Out-Null
python migrate.py "$env:USERPROFILE\.copilot\session-state\knowledge.db" --backup-only --backup-path "C:\Temp\knowledge.db.backup"
python migrate.py "$env:USERPROFILE\.copilot\session-state\knowledge.db"
```

### 2.2 Restore a known-good backup on POSIX

Stop active writers first (`sk watch`, sync daemons, launchd services, CI jobs),
then replace the database and remove WAL sidecars:

```bash
rm -f ~/.copilot/session-state/knowledge.db-wal ~/.copilot/session-state/knowledge.db-shm
cp /tmp/knowledge.db.backup ~/.copilot/session-state/knowledge.db
python migrate.py ~/.copilot/session-state/knowledge.db
```

The migration run should report `Schema up to date`.

### 2.3 Restore a known-good backup on Windows PowerShell

```powershell
Remove-Item "$env:USERPROFILE\.copilot\session-state\knowledge.db-wal" -ErrorAction SilentlyContinue
Remove-Item "$env:USERPROFILE\.copilot\session-state\knowledge.db-shm" -ErrorAction SilentlyContinue
Copy-Item "C:\Temp\knowledge.db.backup" "$env:USERPROFILE\.copilot\session-state\knowledge.db" -Force
python migrate.py "$env:USERPROFILE\.copilot\session-state\knowledge.db"
```

### 2.4 Preserve a bad database for investigation

```bash
mv ~/.copilot/session-state/knowledge.db ~/.copilot/session-state/knowledge.db.corrupt
mv ~/.copilot/session-state/knowledge.db-wal ~/.copilot/session-state/knowledge.db-wal.corrupt 2>/dev/null || true
mv ~/.copilot/session-state/knowledge.db-shm ~/.copilot/session-state/knowledge.db-shm.corrupt 2>/dev/null || true
python migrate.py ~/.copilot/session-state/knowledge.db
```

## 3. Rust binary rollback

Use this when a released `sk` binary fails at startup, routes commands
incorrectly, or regresses native hooks/watch behavior.

### 3.1 Replace the binary with the latest release

POSIX:

```bash
bash sk-rust/install.sh
sk --help
```

Windows PowerShell:

```powershell
powershell -ExecutionPolicy Bypass -File sk-rust\install.ps1
sk --help
```

### 3.2 Roll back to the Python shim temporarily

If the Rust binary is broken but Python tools are intact, move the binary aside
and call the shim directly:

```bash
mv ~/.copilot/bin/sk ~/.copilot/bin/sk.broken
python ~/.copilot/tools/sk.py --help
python ~/.copilot/tools/sk.py hooks run sessionStart
```

Windows PowerShell:

```powershell
Rename-Item "$env:USERPROFILE\.copilot\bin\sk.exe" "sk.exe.broken"
python "$env:USERPROFILE\.copilot\tools\sk.py" --help
python "$env:USERPROFILE\.copilot\tools\sk.py" hooks run sessionStart
```

### 3.3 Validate before re-enabling the binary

```bash
cd sk-rust
cargo test --quiet
cargo clippy -- -D warnings
cd ..
python tests/test_py_rust_boundary.py --rust-bin sk-rust/target/debug/sk
```

Use `sk-rust\target\debug\sk.exe` for `--rust-bin` on Windows.

## 4. Hook provisioning rollback

Use this when hook installation, tamper protection, or generated hook config
blocks legitimate work.

### 4.1 Unlock hooks before repair

```bash
python install.py --unlock-hooks
```

### 4.2 Restore a backed-up hook

```bash
cp .git/hooks/pre-commit.backup .git/hooks/pre-commit
cp .git/hooks/pre-push.backup .git/hooks/pre-push
chmod +x .git/hooks/pre-commit .git/hooks/pre-push
```

Windows PowerShell:

```powershell
Copy-Item .git\hooks\pre-commit.backup .git\hooks\pre-commit -Force
Copy-Item .git\hooks\pre-push.backup .git\hooks\pre-push -Force
```

### 4.3 Re-provision known-good hooks

```bash
python install.py --deploy-hooks
python install.py --install-git-hooks
python install.py --lock-hooks
python tests/test_hook_compat.py
python tests/test_quality_gates.py
```

### 4.4 Emergency bypass policy

Prefer restoring hooks over bypassing them. If a human authorizes a one-off
bypass, document the reason in the PR and run the same tests the hook would have
enforced before merge.

## 5. Evidence checklist for PR closeout

Every PR that changes installer, migration, hook, Rust binary, or rollback
surfaces must include either command output or an explicit `N/A` reason for:

- `python test_security.py`
- `python test_fixes.py`
- `python run_all_tests.py`
- `python tests/test_rollback_runbook.py`
- `python tests/test_migration_rehearsal.py`
- `python tests/test_install_sandbox.py`
- `python tests/test_hook_compat.py`
- Cross-platform CI status: Linux, macOS, and Windows
- Migration rehearsal evidence when `migrate.py` or DB schema changes
- Install sandbox evidence when installer/update scripts change
- Hook security/compat evidence when hook provisioning or hook rules change
