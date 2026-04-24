#!/usr/bin/env python3
"""copilot-cli-healer.py — Detect and clean stale Copilot CLI pkg state.

Addresses upstream Node updater bug that leaves .replaced-* dirs and
tmp/ partial downloads behind, causing ENOENT/EPERM on subsequent updates.

CLI:
    python copilot-cli-healer.py --status           # Summarise pkg state
    python copilot-cli-healer.py --check            # Exit 0 healthy, 1 needs heal
    python copilot-cli-healer.py --heal             # Clean stale state (idempotent)
    python copilot-cli-healer.py --heal --dry-run   # Print actions, make none
    python copilot-cli-healer.py --update           # Heal then retry copilot update
    python copilot-cli-healer.py --install-schedule # Register Task Scheduler/launchd/systemd
    python copilot-cli-healer.py --uninstall-schedule
"""

import argparse
import os
import platform
import shutil
import subprocess
import sys
import time
from pathlib import Path

if os.name == "nt":
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------
HEALER_VERSION = "1.0.0"
TASK_NAME = "CopilotCLIHealer"
LAUNCHD_LABEL = "com.copilot.cli-healer"


def _get_pkg_dir() -> Path:
    """Return Copilot CLI pkg directory (env override for tests)."""
    override = os.environ.get("COPILOT_HEALER_PKG_DIR", "").strip()
    if override:
        return Path(override)
    return Path.home() / ".copilot" / "pkg"


def _lock_path() -> Path:
    return _get_pkg_dir() / ".healer.lock"


# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------
def log(msg: str) -> None:
    print(f"[healer] {msg}")


def ok(msg: str) -> None:
    print(f"[healer] \u2705 {msg}")


def warn(msg: str) -> None:
    print(f"[healer] \u26a0\ufe0f  {msg}", file=sys.stderr)


def err(msg: str) -> None:
    print(f"[healer] \u274c {msg}", file=sys.stderr)


# ---------------------------------------------------------------------------
# Windows-safe rmtree
# ---------------------------------------------------------------------------
def _retry_remove(func, path, exc_info):
    """onerror callback for shutil.rmtree — retries on Windows lock failures."""
    import stat

    for attempt in range(3):
        try:
            os.chmod(path, stat.S_IWRITE)
            func(path)
            return
        except Exception:
            if attempt < 2:
                time.sleep(0.5)
    warn(f"Could not remove: {path}")


def _rmtree(path: Path) -> None:
    """Remove directory tree with Windows retry logic."""
    shutil.rmtree(str(path), onerror=_retry_remove)


# ---------------------------------------------------------------------------
# Lock (O_CREAT | O_EXCL — no TOCTOU races)
# ---------------------------------------------------------------------------
def _acquire_lock() -> "int | None":
    """Acquire exclusive lock. Returns fd on success, None if already locked.

    Stale-lock recovery: if an existing lock file is older than 10 minutes,
    it is treated as abandoned (previous healer SIGKILL'd / crashed) and
    replaced.  Matches the pattern used by auto-update-tools.py's update lock.
    """
    lock = _lock_path()
    try:
        lock.parent.mkdir(parents=True, exist_ok=True)
        fd = os.open(str(lock), os.O_CREAT | os.O_EXCL | os.O_WRONLY)
        os.write(fd, str(os.getpid()).encode())
        return fd
    except (FileExistsError, OSError):
        # Stale-lock recovery: break lock if it's older than 10 min.
        try:
            age = time.time() - lock.stat().st_mtime
            if age > 600:
                lock.unlink(missing_ok=True)
                fd = os.open(str(lock), os.O_CREAT | os.O_EXCL | os.O_WRONLY)
                os.write(fd, str(os.getpid()).encode())
                return fd
        except (FileNotFoundError, OSError):
            pass
        return None


def _release_lock(fd: int) -> None:
    """Release lock file."""
    try:
        os.close(fd)
    except OSError:
        pass
    try:
        _lock_path().unlink(missing_ok=True)
    except OSError:
        pass


# ---------------------------------------------------------------------------
# Detection helpers
# ---------------------------------------------------------------------------
def _dir_size_bytes(d: Path) -> int:
    """Return total size of all files under d."""
    total = 0
    try:
        for f in d.rglob("*"):
            if f.is_file():
                total += f.stat().st_size
    except OSError:
        pass
    return total


def _is_version_dir(name: str) -> bool:
    """Return True if name looks like a version dir (e.g. 1.0.35)."""
    if name.startswith("."):
        return False
    parts = name.split(".")
    return len(parts) >= 2 and all(p.isdigit() for p in parts)


# ---------------------------------------------------------------------------
# Issue type
# ---------------------------------------------------------------------------
class Issue:
    """Represents a single detected stale-state issue."""

    def __init__(self, kind: str, path: Path, description: str):
        self.kind = kind  # "replaced_dir" | "tmp_entry" | "empty_dummy" | "corrupt_dir"
        self.path = path
        self.description = description

    def __repr__(self) -> str:
        return f"Issue({self.kind}, {self.path.name})"


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------
def check(pkg_dir: "Path | None" = None) -> list:
    """Scan pkg dir for stale state. Returns list of Issues (empty = healthy).

    Does NOT make any filesystem changes.
    """
    if pkg_dir is None:
        pkg_dir = _get_pkg_dir()

    if not pkg_dir.exists():
        return []

    issues: list[Issue] = []

    universal = pkg_dir / "universal"
    if universal.is_dir():
        try:
            entries = list(universal.iterdir())
        except OSError:
            entries = []

        for entry in entries:
            name = entry.name
            if name.startswith(".replaced-"):
                issues.append(Issue(
                    "replaced_dir", entry,
                    f"Stale rename-backup dir: {name}",
                ))
            elif entry.is_dir() and _is_version_dir(name):
                try:
                    contents = list(entry.iterdir())
                except OSError:
                    contents = []
                if not contents:
                    issues.append(Issue(
                        "empty_dummy", entry,
                        f"Empty dummy version dir: {name}",
                    ))
                elif _dir_size_bytes(entry) < 1024:
                    issues.append(Issue(
                        "corrupt_dir", entry,
                        f"Suspected corrupt version dir (< 1 KB): {name}",
                    ))

    tmp = pkg_dir / "tmp"
    if tmp.is_dir():
        try:
            tmp_entries = list(tmp.iterdir())
        except OSError:
            tmp_entries = []
        for entry in tmp_entries:
            issues.append(Issue(
                "tmp_entry", entry,
                f"Stale partial-download in tmp/: {entry.name}",
            ))

    return issues


def heal(pkg_dir: "Path | None" = None, dry_run: bool = False) -> list:
    """Remove stale state. Idempotent, never touches healthy version dirs.

    Returns list of action strings (or would-take if dry_run).
    """
    if pkg_dir is None:
        pkg_dir = _get_pkg_dir()

    if not pkg_dir.exists():
        log("No Copilot CLI pkg dir found — nothing to heal.")
        return []

    issues = check(pkg_dir)
    actions: list[str] = []

    for issue in issues:
        p = issue.path
        action_desc = f"Remove {issue.kind}: {p.name}"
        actions.append(action_desc)

        if dry_run:
            log(f"[dry-run] Would remove: {p}")
            continue

        if issue.kind in ("replaced_dir", "empty_dummy", "corrupt_dir"):
            if p.is_dir():
                log(f"Removing dir: {p.name}")
                _rmtree(p)
        elif issue.kind == "tmp_entry":
            if p.is_dir():
                log(f"Removing tmp entry (dir): {p.name}")
                _rmtree(p)
            elif p.is_file():
                log(f"Removing tmp entry (file): {p.name}")
                try:
                    p.unlink()
                except OSError as e:
                    warn(f"Could not remove {p}: {e}")

    if not actions:
        log("Nothing to clean — pkg state is healthy.")
    elif not dry_run:
        ok(f"Healed: removed {len(actions)} stale item(s).")

    return actions


def status(pkg_dir: "Path | None" = None) -> None:
    """Print a human-readable summary of pkg dir state."""
    if pkg_dir is None:
        pkg_dir = _get_pkg_dir()

    print(f"=== Copilot CLI Healer {HEALER_VERSION} ===")
    print(f"pkg dir : {pkg_dir}")

    if not pkg_dir.exists():
        print("state   : no Copilot CLI detected (pkg dir absent)")
        return

    universal = pkg_dir / "universal"
    tmp = pkg_dir / "tmp"

    if universal.is_dir():
        try:
            all_entries = list(universal.iterdir())
        except OSError:
            all_entries = []
        versions = sorted(e.name for e in all_entries if e.is_dir() and _is_version_dir(e.name))
        replaced = sorted(e.name for e in all_entries if e.name.startswith(".replaced-"))
        print(f"versions: {', '.join(versions) if versions else '(none)'}")
        if replaced:
            print(f"stale   : {', '.join(replaced)}")
    else:
        print("universal/: absent")

    if tmp.is_dir():
        try:
            tmp_entries = list(tmp.iterdir())
        except OSError:
            tmp_entries = []
        if tmp_entries:
            print(f"tmp/    : {len(tmp_entries)} stale entry/entries")
        else:
            print("tmp/    : clean")
    else:
        print("tmp/    : absent")

    issues = check(pkg_dir)
    if issues:
        print(f"status  : \u26a0\ufe0f  {len(issues)} issue(s) detected — run --heal to fix")
        for i in issues:
            print(f"          {i.description}")
    else:
        print("status  : \u2705 healthy")


def update_copilot(pkg_dir: "Path | None" = None) -> int:
    """Run heal() then invoke `copilot update`, retrying up to 3x on rename errors."""
    if pkg_dir is None:
        pkg_dir = _get_pkg_dir()

    RENAME_PATTERNS = ("ENOENT", "EPERM", "rename", ".replaced-")
    MAX_ATTEMPTS = 3
    RETRY_DELAY = 2.0

    for attempt in range(1, MAX_ATTEMPTS + 1):
        log(f"Heal pass {attempt}/{MAX_ATTEMPTS}...")
        heal(pkg_dir)

        log("Running: copilot update")
        try:
            result = subprocess.run(
                ["copilot", "update"],
                capture_output=True,
                text=True,
                timeout=120,
            )
        except subprocess.TimeoutExpired:
            err("copilot update timed out (120s)")
            if attempt < MAX_ATTEMPTS:
                log(f"Retrying in {RETRY_DELAY:.0f}s...")
                time.sleep(RETRY_DELAY)
                continue
            return 1
        except FileNotFoundError:
            err("copilot not found in PATH — is Copilot CLI installed?")
            return 1

        if result.returncode == 0:
            ok("copilot update succeeded.")
            if result.stdout:
                print(result.stdout)
            return 0

        combined = (result.stdout or "") + (result.stderr or "")
        if any(p in combined for p in RENAME_PATTERNS):
            warn(f"Rename error detected (attempt {attempt}): {combined[:200]}")
            if attempt < MAX_ATTEMPTS:
                log(f"Healing again and retrying in {RETRY_DELAY:.0f}s...")
                time.sleep(RETRY_DELAY)
                continue
        else:
            err(f"copilot update failed (exit {result.returncode}):")
            print(combined, file=sys.stderr)
            return result.returncode

    err(f"copilot update failed after {MAX_ATTEMPTS} attempts.")
    err("Manual fix: python copilot-cli-healer.py --heal && copilot update")
    return 1


# ---------------------------------------------------------------------------
# Schedule management
# ---------------------------------------------------------------------------
def _script_path() -> Path:
    return Path(__file__).resolve()


def install_schedule() -> int:
    """Register a daily healer task with the OS scheduler."""
    system = platform.system()
    script = _script_path()
    python = sys.executable

    if system == "Windows":
        return _install_windows(python, script)
    elif system == "Darwin":
        return _install_macos(python, script)
    elif system == "Linux":
        return _install_linux(python, script)
    else:
        err(f"Unsupported OS: {system}")
        return 1


def _install_windows(python: str, script: Path) -> int:
    xml = (
        '<?xml version="1.0" encoding="UTF-16"?>\n'
        '<Task version="1.2" xmlns="http://schemas.microsoft.com/windows/2004/02/mit/task">\n'
        "  <Triggers>\n"
        "    <CalendarTrigger>\n"
        "      <StartBoundary>2024-01-01T10:00:00</StartBoundary>\n"
        "      <ScheduleByDay><DaysInterval>1</DaysInterval></ScheduleByDay>\n"
        "      <Enabled>true</Enabled>\n"
        "    </CalendarTrigger>\n"
        "  </Triggers>\n"
        "  <Actions Context=\"Author\">\n"
        "    <Exec>\n"
        f'      <Command>{python}</Command>\n'
        f'      <Arguments>"{script}" --heal</Arguments>\n'
        "    </Exec>\n"
        "  </Actions>\n"
        "  <Settings>\n"
        "    <MultipleInstancesPolicy>IgnoreNew</MultipleInstancesPolicy>\n"
        "    <DisallowStartIfOnBatteries>false</DisallowStartIfOnBatteries>\n"
        "    <StopIfGoingOnBatteries>false</StopIfGoingOnBatteries>\n"
        "    <ExecutionTimeLimit>PT10M</ExecutionTimeLimit>\n"
        "  </Settings>\n"
        "</Task>"
    )
    xml_file = Path.home() / ".copilot" / "session-state" / "copilot-healer-task.xml"
    xml_file.parent.mkdir(parents=True, exist_ok=True)
    xml_file.write_text(xml, encoding="utf-16")

    try:
        r = subprocess.run(
            ["schtasks", "/Create", "/F", "/TN", TASK_NAME, "/XML", str(xml_file)],
            capture_output=True, text=True, timeout=30,
        )
        if r.returncode == 0:
            ok(f"Task Scheduler: {TASK_NAME} registered (daily 10:00)")
            return 0
        else:
            err(f"schtasks failed: {r.stderr.strip()}")
            return 1
    except FileNotFoundError:
        err("schtasks not found — run from Windows (not WSL/Git Bash for this step)")
        return 1


def _install_macos(python: str, script: Path) -> int:
    home = Path.home()
    agents_dir = home / "Library" / "LaunchAgents"
    agents_dir.mkdir(parents=True, exist_ok=True)
    plist_path = agents_dir / f"{LAUNCHD_LABEL}.plist"
    log_path = home / ".copilot" / "session-state" / ".cli-healer.log"

    plist_content = (
        '<?xml version="1.0" encoding="UTF-8"?>\n'
        '<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN"'
        ' "http://www.apple.com/DTDs/PropertyList-1.0.dtd">\n'
        '<plist version="1.0">\n'
        "<dict>\n"
        "    <key>Label</key>\n"
        f"    <string>{LAUNCHD_LABEL}</string>\n"
        "    <key>ProgramArguments</key>\n"
        "    <array>\n"
        f"        <string>{python}</string>\n"
        f"        <string>{script}</string>\n"
        "        <string>--heal</string>\n"
        "    </array>\n"
        "    <key>StartCalendarInterval</key>\n"
        "    <dict>\n"
        "        <key>Hour</key>\n"
        "        <integer>10</integer>\n"
        "        <key>Minute</key>\n"
        "        <integer>0</integer>\n"
        "    </dict>\n"
        "    <key>StandardOutPath</key>\n"
        f"    <string>{log_path}</string>\n"
        "    <key>StandardErrorPath</key>\n"
        f"    <string>{log_path}</string>\n"
        "</dict>\n"
        "</plist>"
    )
    plist_path.write_text(plist_content, encoding="utf-8")

    try:
        subprocess.run(
            ["launchctl", "unload", str(plist_path)],
            capture_output=True, timeout=10,
        )
        subprocess.run(
            ["launchctl", "load", str(plist_path)],
            check=True, capture_output=True, timeout=10,
        )
        ok(f"launchd: {LAUNCHD_LABEL} loaded (daily 10:00)")
        return 0
    except subprocess.CalledProcessError as e:
        err(f"launchctl load failed: {e}")
        return 1


def _install_linux(python: str, script: Path) -> int:
    systemd_dir = Path.home() / ".config" / "systemd" / "user"
    systemd_dir.mkdir(parents=True, exist_ok=True)

    service = (
        "[Unit]\n"
        "Description=Copilot CLI Healer (daily pkg cleanup)\n"
        "After=network.target\n\n"
        "[Service]\n"
        "Type=oneshot\n"
        f"ExecStart={python} {script} --heal\n"
        "StandardOutput=journal\n"
        "StandardError=journal\n"
    )
    timer = (
        "[Unit]\n"
        "Description=Copilot CLI Healer daily timer\n"
        "Requires=copilot-cli-healer.service\n\n"
        "[Timer]\n"
        "OnCalendar=daily\n"
        "Persistent=true\n"
        "Unit=copilot-cli-healer.service\n\n"
        "[Install]\n"
        "WantedBy=timers.target\n"
    )
    (systemd_dir / "copilot-cli-healer.service").write_text(service, encoding="utf-8")
    (systemd_dir / "copilot-cli-healer.timer").write_text(timer, encoding="utf-8")

    try:
        subprocess.run(
            ["systemctl", "--user", "daemon-reload"],
            check=True, timeout=10, capture_output=True,
        )
        subprocess.run(
            ["systemctl", "--user", "enable", "--now", "copilot-cli-healer.timer"],
            check=True, timeout=10, capture_output=True,
        )
        ok("systemd: copilot-cli-healer.timer enabled (daily)")
        return 0
    except subprocess.CalledProcessError as e:
        err(f"systemctl failed: {e}")
        return 1


def uninstall_schedule() -> int:
    """Unregister the scheduled healer task."""
    system = platform.system()

    if system == "Windows":
        try:
            r = subprocess.run(
                ["schtasks", "/Delete", "/F", "/TN", TASK_NAME],
                capture_output=True, text=True, timeout=30,
            )
            if r.returncode == 0:
                ok(f"Task Scheduler: {TASK_NAME} removed")
                return 0
            else:
                warn(f"schtasks delete: {r.stderr.strip()}")
                return 1
        except FileNotFoundError:
            err("schtasks not found")
            return 1

    elif system == "Darwin":
        plist = Path.home() / "Library" / "LaunchAgents" / f"{LAUNCHD_LABEL}.plist"
        if plist.exists():
            subprocess.run(
                ["launchctl", "unload", str(plist)],
                capture_output=True, timeout=10,
            )
            plist.unlink()
            ok(f"launchd: {LAUNCHD_LABEL} removed")
        else:
            log("launchd: not installed")
        return 0

    elif system == "Linux":
        try:
            subprocess.run(
                ["systemctl", "--user", "disable", "--now", "copilot-cli-healer.timer"],
                capture_output=True, timeout=10,
            )
        except Exception:
            pass
        for unit in ("copilot-cli-healer.timer", "copilot-cli-healer.service"):
            p = Path.home() / ".config" / "systemd" / "user" / unit
            if p.exists():
                p.unlink()
        try:
            subprocess.run(
                ["systemctl", "--user", "daemon-reload"],
                capture_output=True, timeout=10,
            )
        except Exception:
            pass
        ok("systemd: copilot-cli-healer units removed")
        return 0

    else:
        err(f"Unsupported OS: {system}")
        return 1


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------
def main() -> int:
    parser = argparse.ArgumentParser(
        prog="copilot-cli-healer",
        description="Detect and clean stale Copilot CLI pkg state.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "Examples:\n"
            "  python copilot-cli-healer.py --status           # Show pkg state\n"
            "  python copilot-cli-healer.py --check            # Exit 0=healthy, 1=needs heal\n"
            "  python copilot-cli-healer.py --heal             # Clean stale state\n"
            "  python copilot-cli-healer.py --heal --dry-run   # Preview without changes\n"
            "  python copilot-cli-healer.py --update           # Heal + copilot update (retry)\n"
            "  python copilot-cli-healer.py --install-schedule   # Register daily job\n"
            "  python copilot-cli-healer.py --uninstall-schedule # Remove daily job\n"
        ),
    )
    parser.add_argument("--status", action="store_true", help="Print pkg dir summary")
    parser.add_argument(
        "--check", action="store_true",
        help="Exit 0 if healthy, 1 if stale state detected",
    )
    parser.add_argument(
        "--heal", action="store_true",
        help="Remove stale .replaced-* dirs and tmp/ contents",
    )
    parser.add_argument(
        "--update", action="store_true",
        help="Heal then invoke 'copilot update' with retry",
    )
    parser.add_argument(
        "--install-schedule", action="store_true",
        help="Register daily healer with OS scheduler",
    )
    parser.add_argument(
        "--uninstall-schedule", action="store_true",
        help="Unregister scheduled healer",
    )
    parser.add_argument(
        "--dry-run", action="store_true",
        help="Print actions without making changes (use with --heal)",
    )
    parser.add_argument(
        "--version", action="version", version=f"copilot-cli-healer {HEALER_VERSION}",
    )

    args = parser.parse_args()
    pkg_dir = _get_pkg_dir()

    if args.status:
        status(pkg_dir)
        return 0

    if args.check:
        if not pkg_dir.exists():
            log("No Copilot CLI detected (pkg dir absent) — nothing to heal.")
            return 0
        issues = check(pkg_dir)
        if not issues:
            ok("Copilot CLI pkg: healthy")
            return 0
        warn(
            f"Copilot CLI pkg: {len(issues)} stale item(s) — "
            "run: python copilot-cli-healer.py --heal"
        )
        for i in issues:
            warn(f"  {i.description}")
        return 1

    if args.heal:
        if not pkg_dir.exists():
            log("No Copilot CLI detected (pkg dir absent) — nothing to heal.")
            return 0
        fd = _acquire_lock()
        if fd is None:
            warn("Another heal is already in progress — exiting.")
            return 2
        try:
            heal(pkg_dir, dry_run=args.dry_run)
        finally:
            _release_lock(fd)
        return 0

    if args.update:
        fd = _acquire_lock()
        if fd is None:
            warn("Another heal is already in progress — exiting.")
            return 2
        try:
            return update_copilot(pkg_dir)
        finally:
            _release_lock(fd)

    if args.install_schedule:
        return install_schedule()

    if args.uninstall_schedule:
        return uninstall_schedule()

    # No args: show status
    status(pkg_dir)
    return 0


if __name__ == "__main__":
    sys.exit(main())
