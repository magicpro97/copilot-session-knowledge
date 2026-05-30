#!/usr/bin/env python3
"""
install.py — Smart installer for session knowledge tools

Usage:
    python install.py                        # Auto-detect and show status
    python install.py --install-sk           # Install/refresh sk launcher in ~/.copilot/bin/
    python install.py --uninstall-launcher   # Remove only the managed sk launcher
    python install.py --deploy-skill         # Deploy SKILL.md to current project
    python install.py --deploy-global-skills # Deploy all skills to ~/.copilot/skills/
    python install.py --deploy-hooks         # Deploy hooks.json to ~/.copilot/hooks/
    python install.py --deploy-instructions  # Deploy global instructions to ~/.github/
    python install.py --inject-global        # Add session-knowledge to global copilot-instructions
    python install.py --install-git-hooks    # Install pre-commit/pre-push into current repo's .git/hooks/
    python install.py --lock-hooks           # Lock hooks with OS immutable flags (tamper protection)
    python install.py --repair-hooks         # Clear hooks-tampered marker (no sudo required)
    python install.py --unlock-hooks         # Unlock hooks for updates
    python install.py --doctor [--manifest]  # Verify install health / manifest drift
    python install.py --test                 # Run self-test
    python install.py --uninstall            # Remove installed files
    python install.py --help                 # Show this help

Windows Task Scheduler (WBS-006):
    python install.py --setup-watch-task            # Register sk watch ONLOGON scheduled task
    python install.py --setup-watch-task --dry-run  # Preview without registering
    python install.py --remove-watch-task           # Remove the scheduled task

sk Launcher (managed cross-platform):
    --install-sk creates ~/.copilot/bin/sk  (POSIX) or ~/.copilot/bin/sk.cmd (Windows)
    and idempotently adds ~/.copilot/bin to your shell profile PATH.
    After install: open a new shell (or 'source ~/.zshrc') then type 'sk --help'.
    auto-update-tools.py calls --install-sk --quiet whenever sk.py or install.py changes.

Tamper Protection:
    --lock-hooks sets OS-level immutable flags on all hook scripts + hooks.json:
      macOS: chflags uchg (user immutable, no sudo needed)
      Linux: chattr +i (requires sudo)
      Windows: attrib +R (read-only, weaker)
    Also generates SHA256 manifest checked by verify-integrity.py at session start.
"""

import hashlib
import importlib.util
import json
import os
import platform
import re
import shutil
import sqlite3
import subprocess
import sys
import textwrap
from datetime import datetime, timezone
from importlib import metadata
from pathlib import Path
from urllib.parse import urlparse
from urllib.request import url2pathname

# ---------------------------------------------------------------------------
# Windows console encoding fix (same pattern as other tools)
# ---------------------------------------------------------------------------
if os.name == "nt":
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass


# ---------------------------------------------------------------------------
# Atomic write helper (P1-5, P1-7)
# ---------------------------------------------------------------------------
def _atomic_write_text(path: Path, content: str, encoding: str = "utf-8") -> None:
    """Write content to path atomically via temp + os.replace. No CRLF translation."""
    tmp = path.with_suffix(path.suffix + ".tmp")
    try:
        tmp.write_bytes(content.encode(encoding))
        os.replace(str(tmp), str(path))
    except Exception:
        try:
            tmp.unlink(missing_ok=True)
        except Exception:
            pass
        raise


def _atomic_write_bytes(path: Path, content: bytes) -> None:
    """Write bytes to path atomically via temp + os.replace."""
    tmp = path.with_suffix(path.suffix + ".tmp")
    try:
        tmp.write_bytes(content)
        os.replace(str(tmp), str(path))
    except Exception:
        try:
            tmp.unlink(missing_ok=True)
        except Exception:
            pass
        raise


# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------
HOME = Path.home()


def _real_home():
    """Get real user home, even under sudo (where Path.home() returns /root)."""
    sudo_user = os.environ.get("SUDO_USER")
    if sudo_user and os.name != "nt":
        import pwd

        try:
            return Path(pwd.getpwnam(sudo_user).pw_dir)
        except KeyError:
            pass
    return HOME


# Host metadata is centralised in host_manifest.py — import canonical constants.
# Do NOT add new hosts here; update host_manifest.py through the review process.
from host_manifest import (  # noqa: E402
    CLAUDE_DIR,
    COPILOT_DIR,
    HOST_INSTRUCTION_FILES,
    HOST_SKILL_SUBPATHS,
)
from host_manifest import (
    HOST_DIRS as KNOWN_HOSTS,
)

TOOLS_DIR = COPILOT_DIR / "tools"
SESSION_STATE = COPILOT_DIR / "session-state"
DB_PATH = SESSION_STATE / "knowledge.db"
SKILLS_SRC = COPILOT_DIR / "skills" / "session-knowledge" / "SKILL.md"
GLOBAL_INSTRUCTIONS = HOME / ".github" / "copilot-instructions.md"
LOCK_FILE = SESSION_STATE / ".watcher.lock"

# Registry of projects that have received a skill deployment via install.py
# --deploy-skill or setup-project.py.  auto-update-tools.py reads this so
# vendored-skill updates propagate to every registered project even when
# auto-update runs from the tools repo or a non-project context (e.g. launchd).
REGISTRY_PATH = SESSION_STATE / "tools-managed-projects.json"


def _load_project_registry() -> list[str]:
    """Return the list of registered project root paths (strings).

    Handles both the legacy plain-string format and the richer dict format
    written by project-registry.py (``{"name": ..., "path": ..., "created_at": ...}``).
    Only the path string is extracted; callers receive a flat list of strings.
    """
    try:
        if REGISTRY_PATH.exists():
            data = json.loads(REGISTRY_PATH.read_text(encoding="utf-8"))
            paths: list[str] = []
            for entry in data.get("projects", []):
                if isinstance(entry, str):
                    paths.append(entry)
                elif isinstance(entry, dict):
                    p = entry.get("path", "")
                    if isinstance(p, str) and p:
                        paths.append(p)
            return paths
    except Exception:
        pass
    return []


def _register_project(project_root: Path) -> None:
    """Add *project_root* to the persistent registry (idempotent, silent on error).

    Reads the full raw registry (which may contain richer dict entries written by
    project-registry.py) and appends a plain string entry when the path is new.
    Existing entries of either format are preserved unchanged.
    """
    try:
        key = str(project_root.resolve())
        try:
            raw: list = (
                json.loads(REGISTRY_PATH.read_text(encoding="utf-8")).get("projects", [])
                if REGISTRY_PATH.exists()
                else []
            )
        except Exception:
            raw = []

        existing_paths: set[str] = set()
        for entry in raw:
            if isinstance(entry, str):
                existing_paths.add(entry)
            elif isinstance(entry, dict):
                p = entry.get("path", "")
                if p:
                    existing_paths.add(p)

        if key not in existing_paths:
            raw.append(key)
            REGISTRY_PATH.parent.mkdir(parents=True, exist_ok=True)
            # P1-7: atomic write prevents registry truncation on concurrent access
            _atomic_write_text(REGISTRY_PATH, json.dumps({"projects": raw}, indent=2))
    except Exception:
        pass


# Resolve the repo's templates/ directory (works when run from repo or ~/.copilot/tools/)
_SCRIPT_DIR = Path(__file__).resolve().parent
_REPO_SKILL_MD = _SCRIPT_DIR / "templates" / "SKILL.md"

TOOL_FILES = [
    "build-session-index.py",
    "extract-knowledge.py",
    "query-session.py",
    "briefing.py",
    "clarify.py",
    "constitution.py",
    "specify.py",
    "task.py",
    "watch-sessions.py",
    "learn.py",
    "embed.py",
    "sync_enqueue.py",
    "agent_adapters.py",
    "claude-adapter.py",
    "context-blocks.py",
    "cron-tasks.py",
    "sync-knowledge.py",
    "sync-config.py",
    "sync-daemon.py",
    "sync-status.py",
    "sync-gateway.py",
    "generate-summary.py",
    "events.py",
    "sk.py",
    "skill-catalog.py",
    "preset-manager.py",
    "install.py",
    "statusline.py",
]

SUPPORT_FILES = [
    "README.md",
    "KNOWLEDGE.md",
    "embedding-config.json",
    "pyproject.toml",
]

SUPPORT_DIRS = [
    "skills",
]

# Managed sk launcher directory (cross-platform: ~/.copilot/bin/)
SK_LAUNCHER_DIR = HOME / ".copilot" / "bin"
_SK_PATH_MARKER_START = "# >>> session-knowledge sk launcher >>>"
_SK_PATH_MARKER_END = "# <<< session-knowledge sk launcher <<<"
_CONTEXT_START_RE = re.compile(r"^\s*<!--\s*(?P<block>[^\r\n<>]+?)\s+SK START\s*-->\s*$")

# ---------------------------------------------------------------------------
# Markers
# ---------------------------------------------------------------------------
OK = "\u2713"  # ✓
FAIL = "\u2717"  # ✗
INFO = "\u2139"  # ℹ
WARN = "\u26a0"  # ⚠


# ===================================================================
# Helpers
# ===================================================================


def _tilde(p: Path) -> str:
    """Show path relative to ~ for readability."""
    try:
        return "~/" + p.relative_to(HOME).as_posix()
    except ValueError:
        return p.as_posix()


def _count_scripts(d: Path) -> int:
    """Count .py files in a directory."""
    if not d.is_dir():
        return 0
    return sum(1 for f in d.iterdir() if f.suffix == ".py")


def _support_dir_files(base_dir: Path) -> list[Path]:
    """Return managed files under support directories."""
    files: list[Path] = []
    for rel_dir in SUPPORT_DIRS:
        root = base_dir / rel_dir
        if not root.is_dir():
            continue
        files.extend(sorted(p for p in root.rglob("*") if p.is_file()))
    return files


def _is_deployable_skill_asset(rel_path: Path) -> bool:
    """Return False for generated artifacts that should not deploy with skills."""
    return "__pycache__" not in rel_path.parts and rel_path.suffix != ".pyc"


def _sync_skill_file(src: Path, dst: Path) -> bool:
    """Copy a skill file atomically when content differs."""
    content = src.read_bytes()
    if dst.is_file() and dst.read_bytes() == content:
        return False
    dst.parent.mkdir(parents=True, exist_ok=True)
    _atomic_write_bytes(dst, content)
    return True


def _prune_empty_dir(root: Path) -> None:
    """Remove an empty directory tree from the bottom up."""
    if not root.is_dir():
        return
    for child in sorted((p for p in root.rglob("*") if p.is_dir()), key=lambda p: len(p.parts), reverse=True):
        try:
            child.rmdir()
        except OSError:
            pass
    try:
        root.rmdir()
    except OSError:
        pass


def _replace_support_dir(src_dir: Path, dst_dir: Path) -> None:
    """Refresh a bundled support directory via staged same-volume renames."""
    staging_dir = dst_dir.with_name(dst_dir.name + ".new")
    backup_dir = dst_dir.with_name(dst_dir.name + ".old")
    shutil.rmtree(staging_dir, ignore_errors=True)
    shutil.rmtree(backup_dir, ignore_errors=True)
    shutil.copytree(str(src_dir), str(staging_dir))
    try:
        if dst_dir.exists():
            os.replace(str(dst_dir), str(backup_dir))
        os.replace(str(staging_dir), str(dst_dir))
    except Exception:
        shutil.rmtree(staging_dir, ignore_errors=True)
        if backup_dir.exists() and not dst_dir.exists():
            os.replace(str(backup_dir), str(dst_dir))
        raise
    shutil.rmtree(backup_dir, ignore_errors=True)


MANAGED_MANIFEST_VERSION = "1.0.0"


def _managed_manifest_path() -> Path:
    """Return the general installer manifest path (~/.copilot/manifest.json)."""
    return HOME / ".copilot" / "manifest.json"


def _manifest_root() -> Path:
    """Return the root directory all manifest entries are relative to."""
    return HOME.resolve()


def _manifest_timestamp() -> str:
    """Return the current UTC timestamp for manifest metadata."""
    return datetime.now(timezone.utc).isoformat()


def _empty_managed_manifest() -> dict:
    """Return the canonical empty installer-manifest payload."""
    return {
        "files": {},
        "version": MANAGED_MANIFEST_VERSION,
        "installed_at": _manifest_timestamp(),
    }


def _load_managed_manifest() -> dict | None:
    """Load ~/.copilot/manifest.json or return None when absent/invalid."""
    manifest_path = _managed_manifest_path()
    if not manifest_path.is_file():
        return None
    try:
        data = json.loads(manifest_path.read_text(encoding="utf-8"))
    except Exception:
        return None
    if not isinstance(data, dict):
        return None
    raw_files = data.get("files")
    if not isinstance(raw_files, dict):
        return None
    files = {key: value for key, value in raw_files.items() if isinstance(key, str) and isinstance(value, str)}
    version = data.get("version") if isinstance(data.get("version"), str) else MANAGED_MANIFEST_VERSION
    installed_at = data.get("installed_at") if isinstance(data.get("installed_at"), str) else _manifest_timestamp()
    return {
        "files": files,
        "version": version,
        "installed_at": installed_at,
    }


def _path_has_symlink(path: Path, root: Path) -> bool:
    """Return True when any existing component between root and path is a symlink."""
    try:
        rel = path.relative_to(root)
    except ValueError:
        return True
    probe = root
    for part in rel.parts:
        probe = probe / part
        if probe.exists() and probe.is_symlink():
            return True
    return False


def _manifest_key_to_path(key: str) -> Path | None:
    """Resolve a manifest key to a concrete path under HOME, rejecting unsafe keys."""
    if not isinstance(key, str) or not key.strip():
        return None
    rel = Path(key)
    if rel.is_absolute():
        return None
    if not rel.parts or any(part in ("", ".", "..") for part in rel.parts):
        return None
    root = _manifest_root()
    candidate = root / rel
    if _path_has_symlink(candidate, root):
        return None
    return candidate


def _path_to_manifest_key(path: Path) -> str | None:
    """Convert an on-disk file path into a safe manifest key relative to HOME."""
    expanded = path.expanduser()
    if expanded.exists() and expanded.is_symlink():
        return None
    try:
        candidate = expanded.resolve(strict=False)
    except OSError:
        candidate = expanded.absolute()
    root = _manifest_root()
    try:
        rel = candidate.relative_to(root)
    except ValueError:
        return None
    if not rel.parts or any(part in ("", ".", "..") for part in rel.parts):
        return None
    if _path_has_symlink(candidate, root):
        return None
    return rel.as_posix()


def _sha256_file(path: Path) -> str:
    """Return the SHA-256 digest for a file."""
    digest = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _write_managed_manifest(manifest: dict) -> None:
    """Persist the installer manifest atomically, deleting it when empty."""
    manifest_path = _managed_manifest_path()
    files = manifest.get("files", {})
    if not files:
        manifest_path.unlink(missing_ok=True)
        return
    manifest_path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "files": dict(sorted(files.items())),
        "version": MANAGED_MANIFEST_VERSION,
        "installed_at": _manifest_timestamp(),
    }
    _atomic_write_text(manifest_path, json.dumps(payload, indent=2, sort_keys=True))


def _record_managed_paths(paths: list[Path], *, quiet: bool = False) -> int:
    """Record manifest entries for files managed by install.py."""
    manifest = _load_managed_manifest() or _empty_managed_manifest()
    recorded = 0
    for path in paths:
        if not path.is_file():
            continue
        key = _path_to_manifest_key(path)
        if key is None:
            if not quiet:
                print(f"  {WARN} Manifest skip (unsafe or outside HOME): {_tilde(path)}")
            continue
        manifest["files"][key] = _sha256_file(path)
        recorded += 1
    if recorded:
        _write_managed_manifest(manifest)
    return recorded


def _forget_managed_paths(paths: list[Path]) -> None:
    """Remove manifest entries for paths no longer managed."""
    manifest = _load_managed_manifest()
    if not manifest:
        return
    changed = False
    for path in paths:
        key = _path_to_manifest_key(path)
        if key and key in manifest["files"]:
            manifest["files"].pop(key, None)
            changed = True
    if changed:
        _write_managed_manifest(manifest)


def _manifest_drift_report() -> tuple[list[str], list[str], list[str], int]:
    """Return (missing, modified, unsafe, tracked_count) for the installer manifest."""
    manifest = _load_managed_manifest()
    if not manifest:
        return [], [], [], 0
    missing: list[str] = []
    modified: list[str] = []
    unsafe: list[str] = []
    for key, expected_hash in manifest.get("files", {}).items():
        path = _manifest_key_to_path(key)
        if path is None:
            unsafe.append(key)
            continue
        if not path.exists():
            missing.append(key)
            continue
        if path.is_symlink() or not path.is_file():
            unsafe.append(key)
            continue
        actual_hash = _sha256_file(path)
        if actual_hash != expected_hash:
            modified.append(key)
    tracked = len(manifest.get("files", {}))
    return missing, modified, unsafe, tracked


def _partition_manifest_removals(paths: list[Path]) -> tuple[list[Path], list[Path], list[Path]]:
    """Split paths into (safe_to_remove, modified, untracked_or_unsafe)."""
    manifest = _load_managed_manifest()
    tracked = manifest.get("files", {}) if manifest else {}
    removable: list[Path] = []
    modified: list[Path] = []
    untracked: list[Path] = []
    for path in paths:
        if not path.is_file():
            continue
        key = _path_to_manifest_key(path)
        expected = tracked.get(key, "") if key else ""
        if not expected:
            untracked.append(path)
            continue
        try:
            actual = _sha256_file(path)
        except OSError:
            untracked.append(path)
            continue
        if actual == expected:
            removable.append(path)
        else:
            modified.append(path)
    return removable, modified, untracked


def _remove_managed_context_blocks_from_text(text: str) -> tuple[str, int]:
    """Remove all managed `<!-- ... SK START -->` blocks from text."""
    lines = text.splitlines(keepends=True)
    if not lines:
        return text, 0
    output: list[str] = []
    removed = 0
    idx = 0
    while idx < len(lines):
        match = _CONTEXT_START_RE.match(lines[idx].rstrip("\r\n"))
        if not match:
            output.append(lines[idx])
            idx += 1
            continue
        block_id = match.group("block")
        end_marker = f"<!-- {block_id} SK END -->"
        end_idx = None
        for probe in range(idx + 1, len(lines)):
            if lines[probe].rstrip("\r\n") == end_marker:
                end_idx = probe
                break
        if end_idx is None:
            output.append(lines[idx])
            idx += 1
            continue
        removed += 1
        idx = end_idx + 1
        if output and idx < len(lines) and not output[-1].strip() and not lines[idx].strip():
            idx += 1
        if not output:
            while idx < len(lines) and not lines[idx].strip():
                idx += 1
    if removed and output and all(not line.strip() for line in output):
        output = []
    return "".join(output), removed


def _remove_managed_context_blocks_from_file(path: Path, *, quiet: bool = False) -> bool:
    """Strip all managed context blocks from a single instruction file."""
    if not path.is_file():
        return False
    original = path.read_text(encoding="utf-8")
    updated, removed = _remove_managed_context_blocks_from_text(original)
    if removed <= 0 or updated == original:
        return False
    _atomic_write_text(path, updated)
    if not quiet:
        print(f"  {OK} Removed {removed} managed context block(s) from {_tilde(path)}")
    return True


def _managed_context_targets(project_root: Path) -> list[Path]:
    """Return unique project instruction-file targets that may hold managed blocks."""
    targets: list[Path] = []
    seen: set[str] = set()
    for rel in HOST_INSTRUCTION_FILES.values():
        key = str(rel)
        if key in seen:
            continue
        seen.add(key)
        targets.append(project_root / rel)
    return targets


def _remove_registered_project_context_blocks(*, quiet: bool = False) -> int:
    """Remove managed context blocks from every registered project instruction file."""
    changed = 0
    for raw_root in _load_project_registry():
        project_root = Path(raw_root).expanduser()
        if not project_root.is_dir():
            continue
        for target in _managed_context_targets(project_root):
            if _remove_managed_context_blocks_from_file(target, quiet=quiet):
                changed += 1
    return changed


def _file_url_to_path(url: str) -> Path | None:
    """Convert a file:// URL from direct_url.json to a local path."""
    if not url:
        return None
    parsed = urlparse(url)
    if parsed.scheme != "file":
        return None
    path_str = url2pathname(parsed.path)
    if parsed.netloc and parsed.netloc not in ("", "localhost"):
        path_str = f"//{parsed.netloc}{path_str}"
    if not path_str:
        return None
    return Path(path_str).expanduser().resolve()


def _matching_editable_install_source_dir(target_dir: Path | None = None) -> Path | None:
    """Return the editable-install source dir when it matches this checkout."""
    target = (target_dir or TOOLS_DIR).resolve()
    try:
        distributions = metadata.distributions()
    except Exception:
        return None

    for dist in distributions:
        dist_name = (dist.metadata.get("Name") or "").strip().lower().replace("_", "-")
        if dist_name != "copilot-session-knowledge":
            continue
        direct_url_text = dist.read_text("direct_url.json")
        if not direct_url_text:
            continue
        try:
            direct_url = json.loads(direct_url_text)
        except Exception:
            continue
        if not direct_url.get("dir_info", {}).get("editable"):
            continue
        source_dir = _file_url_to_path(direct_url.get("url", ""))
        if source_dir == target:
            return source_dir
    return None


def _pip_uninstall_command() -> str:
    """Command that removes the editable `sk` console entrypoint cleanly."""
    return f"{sys.executable} -m pip uninstall copilot-session-knowledge"


def _git_hook_install_text(src_text: str) -> str:
    """Adjust Python git-hook shebangs for the host's launcher naming."""
    if os.name == "nt" and src_text.startswith("#!/usr/bin/env python3\n"):
        return "#!/usr/bin/env python\n" + src_text.split("\n", 1)[1]
    return src_text


def _db_counts() -> dict:
    """Read document / knowledge-entry / relation counts from the DB."""
    result = {"documents": 0, "entries": 0, "relations": 0, "sessions": 0}
    if not DB_PATH.is_file():
        return result
    try:
        db = sqlite3.connect(str(DB_PATH))
        _ALLOWED_TABLES = {"documents", "knowledge_entries", "knowledge_relations", "sessions"}
        for table, key in [
            ("documents", "documents"),
            ("knowledge_entries", "entries"),
            ("knowledge_relations", "relations"),
            ("sessions", "sessions"),
        ]:
            assert table in _ALLOWED_TABLES, f"Unexpected table: {table}"
            try:
                result[key] = db.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
            except sqlite3.OperationalError:
                pass
        db.close()
    except Exception:
        pass
    return result


def _watcher_running() -> bool:
    """Check if the session watcher is currently running."""
    if not LOCK_FILE.is_file():
        return False
    try:
        raw = LOCK_FILE.read_text(encoding="utf-8").strip()
        # Handle both formats: bare PID ("9119") and JSON ({"pid": 9119})
        try:
            data = json.loads(raw)
        except (json.JSONDecodeError, ValueError):
            return False
        if isinstance(data, int):
            pid = data
        elif isinstance(data, dict):
            pid = data.get("pid")
        else:
            return False
        if pid is None:
            return False
        if os.name == "nt":
            import ctypes

            kernel32 = ctypes.windll.kernel32
            handle = kernel32.OpenProcess(0x100000, False, int(pid))
            if handle:
                kernel32.CloseHandle(handle)
                return True
            return False
        else:
            os.kill(int(pid), 0)
            return True
    except (OSError, ValueError, PermissionError, TypeError):
        return False


def _git_root() -> "Path | None":
    """Find the git root from cwd."""
    try:
        r = subprocess.run(
            ["git", "rev-parse", "--show-toplevel"],
            capture_output=True,
            text=True,
            timeout=5,
        )
        if r.returncode == 0:
            return Path(r.stdout.strip())
    except Exception:
        pass
    return None


def _fts_working() -> bool:
    """Quick FTS5 probe."""
    if not DB_PATH.is_file():
        return False
    try:
        db = sqlite3.connect(str(DB_PATH))
        rows = db.execute("SELECT COUNT(*) FROM knowledge_fts WHERE knowledge_fts MATCH 'test OR error'").fetchone()
        db.close()
        return rows[0] >= 0
    except Exception:
        return False


# ===================================================================
# SK Launcher: managed cross-platform sk command
# ===================================================================


def _sk_launcher_script_paths() -> "list[Path]":
    """Return platform-appropriate launcher file paths under SK_LAUNCHER_DIR."""
    if os.name == "nt":
        return [SK_LAUNCHER_DIR / "sk.cmd"]
    return [SK_LAUNCHER_DIR / "sk"]


def _launcher_backup_path(script: Path) -> Path:
    """Return the rollback copy path for a replaced launcher."""
    return script.with_name(script.name + ".backup")


def _sk_launcher_managed_paths() -> "list[Path]":
    """Return existing managed launcher files, including rollback backups."""
    paths: list[Path] = []
    for script in _sk_launcher_script_paths():
        for candidate in (script, _launcher_backup_path(script)):
            if candidate.is_file():
                paths.append(candidate)
    return paths


def _sk_launcher_content() -> str:
    """Return the launcher script body for the current platform.

    WBS-004: prefer py -3, fallback to python3/python on Windows.
    WBS-005: honor SK_TOOLS_DIR env var on both platforms.
    """
    if os.name == "nt":
        # WBS-005: honor SK_TOOLS_DIR; WBS-004: prefer py -3, fallback to python3/python
        python_exe = str(Path(sys.executable))
        return (
            "@echo off\r\n"
            "setlocal\r\n"
            'set "TOOLS_DIR=%USERPROFILE%\\.copilot\\tools"\r\n'
            'if defined SK_TOOLS_DIR set "TOOLS_DIR=%SK_TOOLS_DIR%"\r\n'
            f'set "PYTHON_EXE={python_exe}"\r\n'
            'if exist "%PYTHON_EXE%" (\r\n'
            '    "%PYTHON_EXE%" "%TOOLS_DIR%\\sk.py" %*\r\n'
            "    exit /b\r\n"
            ")\r\n"
            "where py >nul 2>&1\r\n"
            "if not errorlevel 1 (\r\n"
            '    py -3 "%TOOLS_DIR%\\sk.py" %*\r\n'
            "    exit /b\r\n"
            ")\r\n"
            "where python3 >nul 2>&1\r\n"
            "if not errorlevel 1 (\r\n"
            '    python3 "%TOOLS_DIR%\\sk.py" %*\r\n'
            "    exit /b\r\n"
            ")\r\n"
            "where python >nul 2>&1\r\n"
            "if not errorlevel 1 (\r\n"
            '    python "%TOOLS_DIR%\\sk.py" %*\r\n'
            "    exit /b\r\n"
            ")\r\n"
            "echo sk launcher could not find py, python3, or python on PATH. 1>&2\r\n"
            "exit /b 1\r\n"
        )
    # POSIX: WBS-005: honor SK_TOOLS_DIR, fallback to $HOME/.copilot/tools
    return '#!/bin/sh\nTOOLS_DIR="${SK_TOOLS_DIR:-$HOME/.copilot/tools}"\nexec python3 "$TOOLS_DIR/sk.py" "$@"\n'


def _shell_profiles() -> "list[Path]":
    """Return candidate POSIX shell profile files for PATH injection."""
    return [
        HOME / ".bash_profile",
        HOME / ".bashrc",
        HOME / ".zprofile",
        HOME / ".zshrc",
        HOME / ".profile",
    ]


def _preferred_shell_profile() -> Path:
    """Pick the best profile file to create when none already exist."""
    shell_name = Path(os.environ.get("SHELL", "")).name.lower()
    if "zsh" in shell_name:
        return HOME / ".zshrc"
    if "bash" in shell_name:
        return HOME / ".bash_profile"
    return HOME / ".profile"


def _build_sk_path_block() -> str:
    """Return the expected sk launcher PATH export block for the current launcher dir.

    WBS-010: single source-of-truth for the managed block content so both
    _inject_launcher_path and tests can compare against it precisely.
    """
    bin_str = str(SK_LAUNCHER_DIR)
    return f'{_SK_PATH_MARKER_START}\nexport PATH="{bin_str}:$PATH"\n{_SK_PATH_MARKER_END}\n'


def _inject_launcher_path(quiet: bool = False) -> None:
    """Idempotently add ~/.copilot/bin to existing shell profiles (POSIX only).

    WBS-010: atomic write + precise idempotency — never duplicates the block,
    never corrupts the profile on write failure (uses _atomic_write_text).
    Updates an existing block when the launcher dir has changed.
    """
    bin_str = str(SK_LAUNCHER_DIR)
    expected_block = _build_sk_path_block()
    profiles = [profile for profile in _shell_profiles() if profile.exists()]
    if not profiles:
        profiles = [_preferred_shell_profile()]
    for profile in profiles:
        if not profile.parent.exists():
            profile.parent.mkdir(parents=True, exist_ok=True)
        content = profile.read_text(encoding="utf-8") if profile.exists() else ""

        if _SK_PATH_MARKER_START in content:
            # Marker found: check whether the block content is already correct.
            if expected_block in content:
                if not quiet:
                    print(f"  {INFO} sk PATH already in {_tilde(profile)}")
            else:
                # Block is stale (launcher dir changed): replace it atomically.
                cleaned, _ = _remove_launcher_path_block_from_text(content)
                base = cleaned.rstrip("\n")
                new_content = (base + "\n\n" + expected_block) if base.strip() else expected_block
                _atomic_write_text(profile, new_content)
                if not quiet:
                    print(f"  {OK} Updated sk launcher PATH in {_tilde(profile)}")
            continue

        if bin_str in content:
            # User has manually added the path without our managed markers — leave it alone.
            if not quiet:
                print(f"  {INFO} sk PATH already in {_tilde(profile)} (without managed markers)")
            continue

        # Path not present at all: append the managed block atomically.
        new_content = expected_block if not content else content.rstrip("\n") + "\n\n" + expected_block
        _atomic_write_text(profile, new_content)
        if not quiet:
            print(f"  {OK} Added sk launcher PATH to {_tilde(profile)}")


def _remove_launcher_path_block_from_text(text: str) -> tuple[str, int]:
    """Remove the managed sk launcher PATH block without leaving an injected blank line."""
    block_re = re.escape(_SK_PATH_MARKER_START) + r".*?" + re.escape(_SK_PATH_MARKER_END) + r"\n?"
    updated, removed = re.subn(r"\n" + block_re, "", text, flags=re.DOTALL)
    if removed:
        return updated, removed
    return re.subn(block_re, "", text, flags=re.DOTALL)


def _inject_launcher_path_windows(quiet: bool = False) -> bool:
    """Try adding ~/.copilot/bin to user PATH in Windows Registry.

    WBS-007: uses canonical 'Path' key and minimal KEY_READ | KEY_SET_VALUE access.
    WBS-001: sends WM_SETTINGCHANGE after actual mutation; no broadcast on no-op.
    """
    bin_str = str(SK_LAUNCHER_DIR)
    try:
        import winreg

        key = winreg.OpenKey(
            winreg.HKEY_CURRENT_USER,
            "Environment",
            0,
            winreg.KEY_READ | winreg.KEY_SET_VALUE,  # WBS-007: minimal rights
        )
        try:
            cur_path, _ = winreg.QueryValueEx(key, "Path")  # WBS-007: canonical "Path"
        except FileNotFoundError:
            cur_path = ""
        entries = [entry for entry in cur_path.split(";") if entry.strip()]
        launcher_key = _windows_path_entry_key(bin_str)
        if not any(_windows_path_entry_key(entry) == launcher_key for entry in entries):
            new_path = f"{bin_str};{cur_path}" if cur_path else bin_str
            winreg.SetValueEx(key, "Path", 0, winreg.REG_EXPAND_SZ, new_path)  # WBS-007
            if not quiet:
                print(f"  {OK} Added sk launcher dir to Windows user PATH")
            winreg.CloseKey(key)
            _broadcast_windows_path_change(quiet=quiet)  # WBS-001
            return True
        else:
            if not quiet:
                print(f"  {INFO} sk launcher dir already in Windows user PATH")
        winreg.CloseKey(key)
    except Exception as exc:
        if not quiet:
            print(f"  {WARN} Could not update Windows PATH: {exc}")
            print(f"    Add manually: {bin_str}")
    return False


def _remove_launcher_path_windows(quiet: bool = False) -> bool:
    """Try removing ~/.copilot/bin from the Windows user PATH.

    WBS-007: uses canonical 'Path' key and minimal KEY_READ | KEY_SET_VALUE access.
    """
    bin_str = str(SK_LAUNCHER_DIR)
    try:
        import winreg

        key = winreg.OpenKey(
            winreg.HKEY_CURRENT_USER,
            "Environment",
            0,
            winreg.KEY_READ | winreg.KEY_SET_VALUE,  # WBS-007: minimal rights
        )
        try:
            cur_path, _ = winreg.QueryValueEx(key, "Path")  # WBS-007: canonical "Path"
        except FileNotFoundError:
            cur_path = ""
        entries = [entry for entry in cur_path.split(";") if entry]
        launcher_key = _windows_path_entry_key(bin_str)
        filtered = [entry for entry in entries if _windows_path_entry_key(entry) != launcher_key]
        if filtered != entries:
            winreg.SetValueEx(
                key,
                "Path",  # WBS-007: canonical "Path"
                0,
                winreg.REG_EXPAND_SZ,
                ";".join(filtered),
            )
            if not quiet:
                print(f"  {OK} Removed sk launcher dir from Windows user PATH")
            winreg.CloseKey(key)
            return True
        if not quiet:
            print(f"  {INFO} sk launcher dir not present in Windows user PATH")
        winreg.CloseKey(key)
    except Exception as exc:
        if not quiet:
            print(f"  {WARN} Could not remove Windows PATH entry: {exc}")
    return False


# ---------------------------------------------------------------------------
# Windows Task Scheduler — sk watch auto-start (WBS-006)
# ---------------------------------------------------------------------------

_WINDOWS_WATCH_TASK_NAME = "CopilotSessionKnowledgeWatch"


def _windows_watch_task_name() -> str:
    """Return the Windows Task Scheduler task name for the sk watch background service."""
    return _WINDOWS_WATCH_TASK_NAME


def _windows_watch_task_exists() -> bool:
    """Return True when the sk watch scheduled task is registered.

    WBS-006: Uses /Query with /FO LIST to check existence without side effects.
    Fail-open: returns False on any error so callers treat absent as safe.
    """
    try:
        result = subprocess.run(
            ["schtasks", "/Query", "/TN", _WINDOWS_WATCH_TASK_NAME, "/FO", "LIST"],
            capture_output=True,
            text=True,
            timeout=15,
        )
        return result.returncode == 0
    except Exception:
        return False


def _windows_watch_task_create_args(sk_cmd_path: str | None = None) -> list[str]:
    """Return the schtasks /Create arguments for the sk watch scheduled task.

    WBS-006: /F makes the operation idempotent (overwrites existing task).
    /DELAY 0001:30 adds a 1m30s logon delay to avoid login slowdown.
    /RL LIMITED runs without elevation.
    """
    if sk_cmd_path is None:
        sk_cmd_path = str(SK_LAUNCHER_DIR / "sk.cmd")
    # schtasks /TR expects the executable + args quoted as one argument
    task_run = f'"{sk_cmd_path}" watch'
    return [
        "schtasks",
        "/Create",
        "/F",  # Force: overwrite if task already exists (idempotent)
        "/SC",
        "ONLOGON",
        "/TN",
        _WINDOWS_WATCH_TASK_NAME,
        "/TR",
        task_run,
        "/RL",
        "LIMITED",  # No elevation needed
        "/DELAY",
        "0001:30",  # 1m30s delay after logon
    ]


def _windows_watch_task_delete_args() -> list[str]:
    """Return the schtasks /Delete arguments for the sk watch scheduled task."""
    return [
        "schtasks",
        "/Delete",
        "/F",
        "/TN",
        _WINDOWS_WATCH_TASK_NAME,
    ]


def setup_windows_watch_task(dry_run: bool = False, quiet: bool = False) -> bool:
    """Register sk watch as a Windows Task Scheduler task (ONLOGON trigger).

    WBS-006: Idempotent — /F overwrites if already present.
    Runs at LIMITED privilege; delayed 1m30s after logon.
    Returns True when a task was created/updated, False on dry-run/no-op/error.
    """
    if os.name != "nt":
        if not quiet:
            print(f"  {INFO} Windows Task Scheduler setup skipped (not Windows)")
        return False

    sk_cmd = str(SK_LAUNCHER_DIR / "sk.cmd")
    create_args = _windows_watch_task_create_args(sk_cmd)

    if dry_run:
        print(f"  [dry-run] Would register Windows Task Scheduler task: {_windows_watch_task_name()}")
        print(f"  [dry-run] Command: {' '.join(create_args)}")
        return False

    if not (SK_LAUNCHER_DIR / "sk.cmd").is_file():
        if not quiet:
            print(f"  {WARN} sk.cmd not found at {_tilde(SK_LAUNCHER_DIR / 'sk.cmd')} — install sk launcher first")
            print("    Run: python install.py --install-sk")
        return False

    try:
        result = subprocess.run(
            create_args,
            capture_output=True,
            text=True,
            timeout=30,
        )
        if result.returncode == 0:
            if not quiet:
                print(f"  {OK} Windows Task Scheduler task registered: {_windows_watch_task_name()}")
                print(f"  {INFO} Task runs 'sk watch' at logon (1m30s delay, LIMITED privilege)")
            return True
        else:
            stderr = (result.stderr or result.stdout).strip()
            if not quiet:
                print(f"  {WARN} schtasks /Create failed (exit {result.returncode}): {stderr[:200]}")
            return False
    except FileNotFoundError:
        if not quiet:
            print(f"  {WARN} schtasks not found — Windows Task Scheduler unavailable on this system")
        return False
    except Exception as exc:
        if not quiet:
            print(f"  {WARN} Could not register Task Scheduler task: {exc}")
        return False


def remove_windows_watch_task(quiet: bool = False) -> bool:
    """Remove the sk watch Windows Task Scheduler task if it exists.

    WBS-006: Fail-open — returns False without raising if task absent or schtasks unavailable.
    """
    if os.name != "nt":
        return False

    if not _windows_watch_task_exists():
        if not quiet:
            print(f"  {INFO} Windows Task Scheduler task not present: {_windows_watch_task_name()}")
        return False

    try:
        result = subprocess.run(
            _windows_watch_task_delete_args(),
            capture_output=True,
            text=True,
            timeout=30,
        )
        if result.returncode == 0:
            if not quiet:
                print(f"  {OK} Windows Task Scheduler task removed: {_windows_watch_task_name()}")
            return True
        else:
            stderr = (result.stderr or result.stdout).strip()
            if not quiet:
                print(f"  {WARN} schtasks /Delete failed (exit {result.returncode}): {stderr[:200]}")
            return False
    except Exception as exc:
        if not quiet:
            print(f"  {WARN} Could not remove Task Scheduler task: {exc}")
        return False


# ---------------------------------------------------------------------------
# Windows Task Scheduler — browse-backend auto-start
# ---------------------------------------------------------------------------

_WINDOWS_BROWSE_TASK_NAME = "CopilotBrowseBackend"


def _windows_browse_task_exists() -> bool:
    """Return True when the browse-backend scheduled task is registered."""
    try:
        result = subprocess.run(
            ["schtasks", "/Query", "/TN", _WINDOWS_BROWSE_TASK_NAME, "/FO", "LIST"],
            capture_output=True,
            text=True,
            timeout=15,
        )
        return result.returncode == 0
    except Exception:
        return False


def _windows_browse_task_create_args() -> list[str]:
    """Return the schtasks /Create arguments for browse-backend."""
    python_bin = shutil.which("python3") or shutil.which("python") or sys.executable
    task_run = (
        f'"{python_bin}" -c "'
        "import sys; sys.argv = ['browse','--port','8765','--hosted-bootstrap','--debug-log'];"
        'from browse import main; main()"'
    )
    return [
        "schtasks",
        "/Create",
        "/F",
        "/SC",
        "ONLOGON",
        "/TN",
        _WINDOWS_BROWSE_TASK_NAME,
        "/TR",
        task_run,
        "/RL",
        "LIMITED",
        "/DELAY",
        "0002:00",  # 2min delay after logon
    ]


def setup_windows_browse_task(dry_run: bool = False, quiet: bool = False) -> bool:
    """Register browse-backend as a Windows Task Scheduler task (ONLOGON trigger).

    Idempotent — /F overwrites if already present.
    Returns True when a task was created/updated.
    """
    if os.name != "nt":
        if not quiet:
            print(f"  {INFO} Windows Task Scheduler setup skipped (not Windows)")
        return False

    create_args = _windows_browse_task_create_args()

    if dry_run:
        print(f"  [dry-run] Would register Windows Task Scheduler task: {_WINDOWS_BROWSE_TASK_NAME}")
        return False

    try:
        result = subprocess.run(
            create_args,
            capture_output=True,
            text=True,
            timeout=30,
        )
        if result.returncode == 0:
            if not quiet:
                print(f"  {OK} Windows Task Scheduler task registered: {_WINDOWS_BROWSE_TASK_NAME}")
                print(f"  {INFO} Browse backend starts at logon (2min delay, port 8765)")
            return True
        else:
            stderr = (result.stderr or result.stdout).strip()
            if not quiet:
                print(f"  {WARN} schtasks /Create failed (exit {result.returncode}): {stderr[:200]}")
            return False
    except FileNotFoundError:
        if not quiet:
            print(f"  {WARN} schtasks not found — Windows Task Scheduler unavailable")
        return False
    except Exception as exc:
        if not quiet:
            print(f"  {WARN} Could not register Task Scheduler task: {exc}")
        return False


def remove_windows_browse_task(quiet: bool = False) -> bool:
    """Remove the browse-backend Windows Task Scheduler task if it exists."""
    if os.name != "nt":
        return False
    if not _windows_browse_task_exists():
        if not quiet:
            print(f"  {INFO} Windows Task Scheduler task not present: {_WINDOWS_BROWSE_TASK_NAME}")
        return False
    try:
        result = subprocess.run(
            ["schtasks", "/Delete", "/F", "/TN", _WINDOWS_BROWSE_TASK_NAME],
            capture_output=True,
            text=True,
            timeout=30,
        )
        if result.returncode == 0:
            if not quiet:
                print(f"  {OK} Windows Task Scheduler task removed: {_WINDOWS_BROWSE_TASK_NAME}")
            return True
        else:
            stderr = (result.stderr or result.stdout).strip()
            if not quiet:
                print(f"  {WARN} schtasks /Delete failed (exit {result.returncode}): {stderr[:200]}")
            return False
    except Exception as exc:
        if not quiet:
            print(f"  {WARN} Could not remove Task Scheduler task: {exc}")
        return False


# ---------------------------------------------------------------------------
# Linux systemd — browse-backend auto-start
# ---------------------------------------------------------------------------

_LINUX_BROWSE_SERVICE_NAME = "copilot-browse-backend.service"


def setup_linux_browse_service(dry_run: bool = False, quiet: bool = False) -> bool:
    """Install a systemd user service for browse-backend on Linux.

    Returns True when the service was created/enabled.
    """
    if platform.system() != "Linux":
        return False

    systemd_dir = Path.home() / ".config" / "systemd" / "user"
    service_file = systemd_dir / _LINUX_BROWSE_SERVICE_NAME
    python_bin = shutil.which("python3") or sys.executable
    tools_dir = Path.home() / ".copilot" / "tools"

    unit_content = f"""\
[Unit]
Description=Copilot Browse Backend (port 8765)
After=network.target

[Service]
Type=simple
WorkingDirectory={tools_dir}
ExecStart={python_bin} -c "import sys; sys.argv = ['browse','--port','8765','--hosted-bootstrap','--debug-log']; from browse import main; main()"
Restart=on-failure
RestartSec=10
StandardOutput=append:{Path.home()}/.copilot/session-state/.browse-backend.log
StandardError=append:{Path.home()}/.copilot/session-state/.browse-backend.log

[Install]
WantedBy=default.target
"""

    if dry_run:
        if not quiet:
            print(f"  [dry-run] Would install systemd user service: {_LINUX_BROWSE_SERVICE_NAME}")
        return False

    try:
        systemd_dir.mkdir(parents=True, exist_ok=True)
        service_file.write_text(unit_content, encoding="utf-8")
        subprocess.run(["systemctl", "--user", "daemon-reload"], capture_output=True, timeout=15)
        subprocess.run(["systemctl", "--user", "enable", _LINUX_BROWSE_SERVICE_NAME], capture_output=True, timeout=15)
        subprocess.run(["systemctl", "--user", "start", _LINUX_BROWSE_SERVICE_NAME], capture_output=True, timeout=15)
        if not quiet:
            print(f"  {OK} systemd user service installed and started: {_LINUX_BROWSE_SERVICE_NAME}")
        return True
    except FileNotFoundError:
        if not quiet:
            print(f"  {INFO} systemctl not found — systemd service not available")
        return False
    except Exception as exc:
        if not quiet:
            print(f"  {WARN} Could not install systemd service: {exc}")
        return False


def remove_linux_browse_service(quiet: bool = False) -> bool:
    """Remove the browse-backend systemd user service if it exists."""
    if platform.system() != "Linux":
        return False
    service_file = Path.home() / ".config" / "systemd" / "user" / _LINUX_BROWSE_SERVICE_NAME
    if not service_file.is_file():
        if not quiet:
            print(f"  {INFO} systemd service not present: {_LINUX_BROWSE_SERVICE_NAME}")
        return False
    try:
        subprocess.run(["systemctl", "--user", "stop", _LINUX_BROWSE_SERVICE_NAME], capture_output=True, timeout=15)
        subprocess.run(["systemctl", "--user", "disable", _LINUX_BROWSE_SERVICE_NAME], capture_output=True, timeout=15)
        service_file.unlink()
        subprocess.run(["systemctl", "--user", "daemon-reload"], capture_output=True, timeout=15)
        if not quiet:
            print(f"  {OK} systemd service removed: {_LINUX_BROWSE_SERVICE_NAME}")
        return True
    except Exception as exc:
        if not quiet:
            print(f"  {WARN} Could not remove systemd service: {exc}")
        return False


def _windows_path_entry_key(entry: str) -> str:
    """Normalize a Windows PATH entry for stable comparisons."""
    return entry.strip().rstrip("\\/").lower()


def _path_entry_key(entry: str, *, windows_style: bool | None = None) -> str:
    """Normalize a PATH entry for comparison without resolving the filesystem."""
    use_windows = os.name == "nt" if windows_style is None else windows_style
    if use_windows:
        return _windows_path_entry_key(entry)
    return entry.strip().rstrip("/")


def _path_contains_dir(path_value: str, target_dir: Path, *, delimiter: str | None = None) -> bool:
    """Return True when *path_value* contains *target_dir* as a PATH entry."""
    sep = delimiter if delimiter is not None else os.pathsep
    windows_style = sep == ";" or os.name == "nt"
    target_key = _path_entry_key(str(target_dir), windows_style=windows_style)
    for entry in path_value.split(sep):
        if _path_entry_key(entry, windows_style=windows_style) == target_key:
            return True
    return False


def _current_path_has_launcher_dir(path_value: str | None = None, *, delimiter: str | None = None) -> bool:
    """Return True when the current process PATH can discover the launcher dir."""
    current_path = os.environ.get("PATH", "") if path_value is None else path_value
    return _path_contains_dir(current_path, SK_LAUNCHER_DIR, delimiter=delimiter)


def _windows_current_path_refresh_command() -> str:
    """PowerShell command that makes the launcher available in the current process."""
    return '$env:Path = "$env:USERPROFILE\\.copilot\\bin;$env:Path"'


def _emit_windows_current_path_hint(quiet: bool = False) -> None:
    """Tell Windows users how to refresh PATH for the already-running shell."""
    if quiet or os.name != "nt" or _current_path_has_launcher_dir():
        return
    print(f"  {WARN} Current process PATH does not include {_tilde(SK_LAUNCHER_DIR)} yet.")
    print("    For this PowerShell/Copilot session, run:")
    print(f"    {_windows_current_path_refresh_command()}")
    print("    New terminals pick up the user PATH after restart.")


def _read_windows_user_path() -> tuple[str | None, str | None]:
    """Read the Windows user PATH from HKCU\\Environment.

    Returns (path_value, error_message). On success error_message is None.
    On failure path_value is None and error_message describes the problem.
    Uses canonical 'Path' key name (WBS-007).
    """
    try:
        import winreg

        key = winreg.OpenKey(winreg.HKEY_CURRENT_USER, "Environment", 0, winreg.KEY_READ)
        try:
            value, _ = winreg.QueryValueEx(key, "Path")
        except FileNotFoundError:
            value = ""
        finally:
            winreg.CloseKey(key)
        return value, None
    except Exception as exc:
        return None, str(exc)


def _broadcast_windows_path_change(quiet: bool = False) -> bool:
    """Send WM_SETTINGCHANGE to notify windows that user PATH has changed.

    WBS-001: called after actual registry PATH mutation; not called on no-op.
    Fail-open: returns False without raising if broadcast is unavailable.
    """
    try:
        import ctypes

        HWND_BROADCAST = 0xFFFF
        WM_SETTINGCHANGE = 0x001A
        SMTO_ABORTIFHUNG = 0x0002
        result = ctypes.windll.user32.SendMessageTimeoutW(
            HWND_BROADCAST,
            WM_SETTINGCHANGE,
            0,
            "Environment",
            SMTO_ABORTIFHUNG,
            5000,
            None,
        )
        if result == 0 and not quiet:
            print(
                f"  {WARN} PATH broadcast (WM_SETTINGCHANGE) returned 0; "
                "new terminals should still inherit the change after restart."
            )
        return result != 0
    except Exception as exc:
        if not quiet:
            print(f"  {WARN} Could not broadcast PATH change: {exc}")
        return False


def _which_command(name: str) -> str | None:
    """Wrapper around shutil.which for testability."""
    return shutil.which(name)


def _is_windows_store_python_alias(path: str | None) -> bool:
    """Return True when python3 resolves to the Microsoft Store alias path."""
    if not path:
        return False
    normalized = path.replace("/", "\\").lower()
    return "\\microsoft\\windowsapps\\python3" in normalized or "\\windowsapps\\python3" in normalized


def _python3_alias_risk(
    python3_path: str | None = None,
    path_value: str | None = None,
    *,
    assume_windows: bool | None = None,
) -> bool:
    """Return True when Windows python3 likely resolves to the Store alias before the shim."""
    is_windows = os.name == "nt" if assume_windows is None else assume_windows
    if not is_windows:
        return False
    resolved = _which_command("python3") if python3_path is None else python3_path
    if not _is_windows_store_python_alias(resolved):
        return False
    return not _current_path_has_launcher_dir(path_value, delimiter=";")


def _launcher_probe() -> tuple[Path | None, bool, str]:
    """Run the direct managed launcher with --version and report the result."""
    scripts = [script for script in _sk_launcher_script_paths() if script.is_file()]
    if not scripts:
        return None, False, "launcher file not found"
    script = scripts[0]
    try:
        result = subprocess.run(
            [str(script), "--version"],
            capture_output=True,
            text=True,
            timeout=10,
        )
    except Exception as exc:
        return script, False, f"{type(exc).__name__}: {exc}"
    output = (result.stdout or result.stderr).strip().splitlines()
    detail = output[0] if output else f"exit {result.returncode}"
    return script, result.returncode == 0, detail


def install_sk_launcher(quiet: bool = False, dry_run: bool = False) -> bool:
    """Create/update the managed sk launcher in ~/.copilot/bin/.

    Idempotent — safe to call on every install or update.
    Returns True if any launcher file was created or updated.

    WBS-012: dry_run=True prints intended writes without filesystem mutation.
    """
    if dry_run:
        for script in _sk_launcher_script_paths():
            action = "update" if script.is_file() else "create"
            print(f"  [dry-run] Would {action}: {_tilde(script)}")
        if os.name != "nt":
            profiles = [p for p in _shell_profiles() if p.exists()] or [_preferred_shell_profile()]
            for profile in profiles:
                content = profile.read_text(encoding="utf-8") if profile.exists() else ""
                if _SK_PATH_MARKER_START not in content and str(SK_LAUNCHER_DIR) not in content:
                    print(f"  [dry-run] Would add sk PATH block to: {_tilde(profile)}")
        else:
            print("  [dry-run] Would update Windows user PATH registry (Path key)")
        return False
    SK_LAUNCHER_DIR.mkdir(parents=True, exist_ok=True)
    content = _sk_launcher_content()
    content_bytes = content.encode("utf-8")
    changed = False

    for script in _sk_launcher_script_paths():
        existing_bytes = script.read_bytes() if script.is_file() else None
        if existing_bytes == content_bytes:
            if not quiet:
                print(f"  {INFO} sk launcher — already up to date ({_tilde(script)})")
        else:
            backup = None
            if existing_bytes is not None:
                backup = _launcher_backup_path(script)
                shutil.copy2(str(script), str(backup))
            _atomic_write_text(script, content)
            if os.name != "nt":
                script.chmod(script.stat().st_mode | 0o755)
            if not quiet:
                verb = "updated" if existing_bytes is not None else "created"
                print(f"  {OK} sk launcher {verb}: {_tilde(script)}")
                if backup is not None:
                    print(f"  {INFO} Previous launcher backed up: {_tilde(backup)}")
            changed = True

    if os.name != "nt":
        _inject_launcher_path(quiet=quiet)
    else:
        _inject_launcher_path_windows(quiet=quiet)
        _emit_windows_current_path_hint(quiet=quiet)

    _record_managed_paths(_sk_launcher_managed_paths(), quiet=quiet)
    return changed


def uninstall_sk_launcher(quiet: bool = False) -> int:
    """Remove managed sk launcher files and shell profile PATH injections.

    Returns the number of items removed.
    """
    removed = 0
    launcher_paths = _sk_launcher_managed_paths()
    safe_scripts, modified_scripts, untracked_scripts = _partition_manifest_removals(launcher_paths)

    for script in modified_scripts:
        if not quiet:
            print(f"  {WARN} Preserved modified sk launcher: {_tilde(script)}")
    for script in untracked_scripts:
        if not quiet:
            print(f"  {WARN} Preserved untracked sk launcher (no manifest entry): {_tilde(script)}")

    for script in safe_scripts:
        try:
            script.unlink()
            removed += 1
            if not quiet:
                print(f"  {OK} Removed sk launcher: {_tilde(script)}")
        except Exception as exc:
            if not quiet:
                print(f"  {FAIL} Could not remove {_tilde(script)}: {exc}")

    if safe_scripts:
        _forget_managed_paths(safe_scripts)

    # Remove launcher dir if now empty
    if SK_LAUNCHER_DIR.is_dir():
        try:
            remaining = list(SK_LAUNCHER_DIR.iterdir())
            if not remaining:
                SK_LAUNCHER_DIR.rmdir()
                removed += 1
                if not quiet:
                    print(f"  {OK} Removed empty {_tilde(SK_LAUNCHER_DIR)}")
        except Exception:
            pass

    remaining_scripts = [script for script in _sk_launcher_script_paths() if script.is_file()]
    if not remaining_scripts:
        # Remove PATH injections from POSIX shell profiles
        if os.name != "nt":
            for profile in _shell_profiles():
                if not profile.exists():
                    continue
                content = profile.read_text(encoding="utf-8")
                if _SK_PATH_MARKER_START not in content:
                    continue
                new_content, removed_blocks = _remove_launcher_path_block_from_text(content)
                if new_content != content:
                    _atomic_write_text(profile, new_content)
                    removed += removed_blocks
                    if not quiet:
                        print(f"  {OK} Removed sk PATH injection from {_tilde(profile)}")
        elif _remove_launcher_path_windows(quiet=quiet):
            removed += 1
    elif not quiet:
        print(f"  {INFO} Kept launcher PATH injection because a launcher script was preserved.")

    return removed


# ===================================================================
# 1. Detection / Status
# ===================================================================


def show_status() -> bool:
    """Print agent detection table. Returns True if tools are installed."""
    print("\nAgent Detection:")

    # Iterate over KNOWN_HOSTS to keep Copilot CLI + Claude Code symmetrical.
    for host_name, host_dir in KNOWN_HOSTS.items():
        if host_dir.is_dir():
            print(f"  {OK} {host_name}: {_tilde(host_dir)} found")
        else:
            print(f"  {FAIL} {host_name}: {_tilde(host_dir)} not found")

    # Tools directory
    n_scripts = _count_scripts(TOOLS_DIR)
    if TOOLS_DIR.is_dir() and n_scripts > 0:
        print(f"  {OK} Tools dir:   {_tilde(TOOLS_DIR)} ({n_scripts} scripts)")
        installed = True
    else:
        print(f"  {FAIL} Tools dir:   {_tilde(TOOLS_DIR)} not found")
        installed = False

    # Session data
    if SESSION_STATE.is_dir():
        counts = _db_counts()
        sessions = counts["sessions"]
        if sessions > 0:
            print(f"  {OK} Session data: {sessions} sessions indexed")
        else:
            n_dirs = sum(1 for d in SESSION_STATE.iterdir() if d.is_dir() and not d.name.startswith("."))
            if n_dirs:
                print(f"  {OK} Session data: {n_dirs} session dirs (not yet indexed)")
            else:
                print(f"  {FAIL} Session data: empty")
    else:
        print(f"  {FAIL} Session data: {_tilde(SESSION_STATE)} not found")

    # Knowledge DB
    if DB_PATH.is_file():
        counts = _db_counts()
        print(f"  {OK} Knowledge DB: {counts['entries']} entries, {counts['relations']} relations")
    else:
        print(f"  {FAIL} Knowledge DB: not built")

    # Watcher
    if _watcher_running():
        print(f"  {OK} Watcher:     running")
    else:
        print(f"  {FAIL} Watcher:     not running")

    return installed


# ===================================================================
# 2. Skill Deployment
# ===================================================================

MINIMAL_SKILL_MD = textwrap.dedent("""\
    ---
    name: session-knowledge
    description: >-
      Search past Copilot/Claude session knowledge before complex tasks. Run briefing.py
      for relevant mistakes, patterns, decisions. Use query-session.py to search errors,
      tools, architecture choices. Supports semantic search with embeddings.
    ---

    # Session Knowledge Tools — Agent Skill

    Before starting any task, query the shared knowledge base for relevant past
    experience, patterns, and known mistakes.

    ## Available Tools

    ```bash
    # Search the knowledge base
    sk query "search terms"
    # Fallbacks: macOS/Linux `python3 ~/.copilot/tools/query-session.py ...`;
    # Windows PowerShell `python "$env:USERPROFILE\\.copilot\\tools\\query-session.py" ...`

    # Get a context briefing for your current task
    sk briefing "task description"

    # Manual compatibility path for ad hoc sub-agent prompts
    sk briefing "task description" --for-subagent

    # Preferred delegated-agent path (tentacle structured evidence)
    sk tentacle swarm <name> --briefing

    # Record new learnings
    sk learn --mistake "Title" "What went wrong and fix"
    sk learn --pattern "Title" "What works well"
    sk learn --decision "Title" "Choice and rationale"

    # Show past mistakes and patterns
    sk query --mistakes
    sk query --patterns
    sk query --decisions
    ```

    ## Workflow

    1. **Before starting**: run `briefing.py` with a description of your task.
    2. **During work**: if you hit an issue, search for similar past problems.
    3. **After finishing**: record any new mistakes, patterns, or decisions
       with `learn.py`.
    4. **Delegated agents (preferred)**: use `tentacle.py ... --briefing`
       so dispatch injects bounded `[KNOWLEDGE EVIDENCE]` from task recall first.
    5. **Manual compatibility**: for ad hoc prompts, run
       `briefing.py --for-subagent` and inject output directly.
""")


def deploy_skill():
    """Deploy SKILL.md to the current project directory."""
    project_root = _git_root() or Path.cwd()
    print("\nSkill Deployment")
    print(f"  Project: {project_root}")

    # Read source skill content (priority: installed > repo template > minimal)
    if SKILLS_SRC.is_file():
        skill_content = SKILLS_SRC.read_text(encoding="utf-8")
        print(f"  {OK} Source: {_tilde(SKILLS_SRC)}")
    elif _REPO_SKILL_MD.is_file():
        skill_content = _REPO_SKILL_MD.read_text(encoding="utf-8")
        print(f"  {OK} Source: {_REPO_SKILL_MD}")
    else:
        skill_content = MINIMAL_SKILL_MD
        print(f"  {INFO} Source: generating minimal SKILL.md (no source template found)")

    deployed = []

    # Iterate manifest-defined hosts so adding a new host only requires
    # updating host_manifest.py — no changes needed here.
    for host_name, host_dir in KNOWN_HOSTS.items():
        if not host_dir.is_dir():
            continue
        subpath = HOST_SKILL_SUBPATHS.get(host_name)
        if subpath is None:
            continue
        target = project_root / subpath
        target.parent.mkdir(parents=True, exist_ok=True)
        _atomic_write_text(target, skill_content)  # P1-5: atomic write
        deployed.append((host_name, target))
        print(f"  {OK} {host_name}: {target.relative_to(project_root)}")

    if not deployed:
        print(f"  {FAIL} No agents detected — nothing deployed")
        print("      Create ~/.copilot/ or ~/.claude/ first.")
        return

    _record_managed_paths([target for _host_name, target in deployed])
    print(f"\n  Deployed {len(deployed)} skill file(s).")

    # Register this project so auto-update-tools.py can propagate vendored-skill
    # updates even when running from the tools repo / launchd / shell auto-start.
    _register_project(project_root)


def deploy_global_skills():
    """Create or refresh ~/.copilot/skills/<name>/ from tools/skills/<name>/."""
    global_skills_dir = COPILOT_DIR / "skills"
    skills_src_dir = TOOLS_DIR / "skills"

    print("\nDeploy Global Skills")
    print(f"  Source: {_tilde(skills_src_dir)}")
    print(f"  Target: {_tilde(global_skills_dir)}")

    if not skills_src_dir.is_dir():
        print(f"  {FAIL} Source skills dir not found: {_tilde(skills_src_dir)}")
        return

    global_skills_dir.mkdir(parents=True, exist_ok=True)
    changed: list[Path] = []
    up_to_date = 0

    for skill_dir in sorted(p for p in skills_src_dir.iterdir() if p.is_dir()):
        skill_md_src = skill_dir / "SKILL.md"
        if not skill_md_src.is_file():
            continue

        target_dir = global_skills_dir / skill_dir.name
        target_dir.mkdir(parents=True, exist_ok=True)
        target_md = target_dir / "SKILL.md"
        skill_changed = _sync_skill_file(skill_md_src, target_md)
        if skill_changed:
            changed.append(target_md)

        for asset_file in sorted(p for p in skill_dir.rglob("*") if p.is_file() and p.name != "SKILL.md"):
            rel = asset_file.relative_to(skill_dir)
            if not _is_deployable_skill_asset(rel):
                continue
            target_asset = target_dir / rel
            if _sync_skill_file(asset_file, target_asset):
                changed.append(target_asset)
                skill_changed = True

        if skill_changed:
            print(f"  {OK} {skill_dir.name}")
        else:
            up_to_date += 1

    if up_to_date:
        print(f"  {INFO} {up_to_date} skill(s) already up to date")
    print(f"\n  Deployed {len(changed)} file(s) to {_tilde(global_skills_dir)}")
    if changed:
        _record_managed_paths(changed)


# ===================================================================
# 2b. Global Instructions Injection
# ===================================================================

_INJECT_MARKER_START = "<!-- SESSION-KNOWLEDGE-START -->"
_INJECT_MARKER_END = "<!-- SESSION-KNOWLEDGE-END -->"

GLOBAL_INJECT_BLOCK = textwrap.dedent(f"""\
{_INJECT_MARKER_START}
## 🧠 Session Knowledge

> Full briefing strategy and escalation rules are in the always-loaded
> `~/.github/instructions/session-knowledge.instructions.md`.
> Follow the progressive escalation model defined there — start minimal,
> escalate only when compact output reveals a relevant hit.

Quick reference:

```bash
# Before moderate/complex tasks — start here, escalate only if needed
sk briefing --auto --compact
# Fallbacks: macOS/Linux `python3 ~/.copilot/tools/briefing.py --auto --compact`;
# Windows PowerShell `python "$env:USERPROFILE\\.copilot\\tools\\briefing.py" --auto --compact`

# For delegated tentacle agents — preferred structured recall path
sk tentacle swarm <name> --briefing
# Fallbacks: macOS/Linux `python3 ~/.copilot/tools/tentacle.py swarm <name> --briefing`;
# Windows PowerShell `python "$env:USERPROFILE\\.copilot\\tools\\tentacle.py" swarm <name> --briefing`

# Manual compatibility for ad hoc sub-agent prompts
sk briefing "task description" --for-subagent

# After resolving a non-trivial issue — record the learning
sk learn --mistake "Title" "Root cause and fix"
sk learn --pattern "Title" "What works well"
```
{_INJECT_MARKER_END}
""")


_TEMPLATES_DIR = _SCRIPT_DIR / "templates"
_INSTRUCTIONS_TEMPLATES = _TEMPLATES_DIR / "instructions"


def deploy_hooks():
    """Deploy hooks.json and Python hook scripts to ~/.copilot/hooks/.

    Hook deployment is Copilot CLI-only.  Claude Code configures hooks via
    ~/.claude/settings.json, which uses a different format not managed here.
    """
    print("\nDeploy Hooks (Copilot CLI)")

    hooks_src = _SCRIPT_DIR / "hooks" / "hooks.json"
    if not hooks_src.is_file():
        # Backward-compatible fallback for older repos.
        hooks_src = _SCRIPT_DIR / ".github" / "hooks" / "hooks.json"
    hooks_dst_dir = COPILOT_DIR / "hooks"
    hooks_dst = hooks_dst_dir / "hooks.json"

    if not hooks_src.is_file():
        print(f"  {FAIL} Source hooks.json not found: {_tilde(hooks_src)}")
        return

    hooks_dst_dir.mkdir(parents=True, exist_ok=True)

    # Deploy hooks.json
    new = hooks_src.read_text(encoding="utf-8")
    if hooks_dst.is_file():
        old = hooks_dst.read_text(encoding="utf-8")
        if old == new:
            print(f"  {INFO} hooks.json — already up to date")
        else:
            backup = hooks_dst.with_suffix(".json.backup")
            shutil.copy2(str(hooks_dst), str(backup))
            _atomic_write_text(hooks_dst, new)  # P1-5: atomic write
            print(f"  {OK} hooks.json — updated (backup: {backup.name})")
    else:
        _atomic_write_text(hooks_dst, new)  # P1-5: atomic write
        print(f"  {OK} hooks.json — created")

    # Inform about native sk preference
    sk_bin_dir = HOME / ".copilot" / "bin"
    sk_native = sk_bin_dir / ("sk.exe" if __import__("platform").system() == "Windows" else "sk-native")
    sk_shim = sk_bin_dir / ("sk.cmd" if __import__("platform").system() == "Windows" else "sk")
    if sk_native.exists():
        print(f"  {INFO} Native sk binary detected ({sk_native.name}) — hooks will prefer 'sk hooks run <event>'")
    elif sk_shim.exists():
        print(f"  {INFO} sk shim detected ({sk_shim.name}) — hooks will prefer 'sk hooks run <event>'")
    else:
        hook_python = "python" if os.name == "nt" else "python3"
        print(f"  {INFO} sk binary not found in {_tilde(sk_bin_dir)} — hooks fall back to {hook_python} hook_runner.py")
        print(f"  {INFO} Run 'python {_tilde(_SCRIPT_DIR / 'install.py')} --install-sk' to install the sk launcher")

    # Ensure markers directory exists
    markers_dir = COPILOT_DIR / "markers"
    markers_dir.mkdir(parents=True, exist_ok=True)
    print(f"  {OK} markers/ directory ready")

    # List available hooks (root-level + any subdirectories, e.g. hooks/rules/)
    hooks_dir = _SCRIPT_DIR / "hooks"
    py_hooks: list[Path] = sorted(hooks_dir.glob("*.py")) if hooks_dir.is_dir() else []
    if hooks_dir.is_dir():
        for sub in sorted(hooks_dir.iterdir()):
            if sub.is_dir() and not sub.name.startswith((".", "_")):
                py_hooks += sorted(sub.glob("*.py"))
    print(f"\n  {len(py_hooks)} Python hook scripts available:")
    for h in py_hooks:
        rel = h.relative_to(hooks_dir)
        print(f"    • {rel.as_posix()}")

    _record_managed_paths([hooks_dst])


def deploy_instructions():
    """Deploy global copilot-instructions.md and scope-specific instruction files."""
    print("\nDeploy Global Instructions")

    github_dir = HOME / ".github"
    instructions_dir = github_dir / "instructions"
    github_dir.mkdir(parents=True, exist_ok=True)
    instructions_dir.mkdir(parents=True, exist_ok=True)

    deployed = 0
    manifest_paths: list[Path] = []

    # 1. Core: copilot-instructions.md
    src = _TEMPLATES_DIR / "copilot-instructions.md"
    dst = GLOBAL_INSTRUCTIONS
    if src.is_file():
        if dst.is_file():
            old = dst.read_text(encoding="utf-8")
            new = src.read_text(encoding="utf-8")
            if old == new:
                print(f"  {INFO} copilot-instructions.md — already up to date")
            else:
                backup = dst.with_suffix(".md.backup")
                shutil.copy2(str(dst), str(backup))
                dst.write_text(new, encoding="utf-8")
                print(f"  {OK} copilot-instructions.md — updated (backup: {backup.name})")
                deployed += 1
                manifest_paths.append(dst)
        else:
            dst.write_text(src.read_text(encoding="utf-8"), encoding="utf-8")
            print(f"  {OK} copilot-instructions.md — created")
            deployed += 1
            manifest_paths.append(dst)
    else:
        print(f"  {FAIL} Template not found: {_tilde(src)}")

    # 2. Scope-specific instructions
    if _INSTRUCTIONS_TEMPLATES.is_dir():
        for src_file in sorted(_INSTRUCTIONS_TEMPLATES.glob("*.instructions.md")):
            dst_file = instructions_dir / src_file.name
            if dst_file.is_file():
                old = dst_file.read_text(encoding="utf-8")
                new = src_file.read_text(encoding="utf-8")
                if old == new:
                    print(f"  {INFO} {src_file.name} — already up to date")
                else:
                    dst_file.write_text(new, encoding="utf-8")
                    print(f"  {OK} {src_file.name} — updated")
                    deployed += 1
                    manifest_paths.append(dst_file)
            else:
                dst_file.write_text(src_file.read_text(encoding="utf-8"), encoding="utf-8")
                print(f"  {OK} {src_file.name} — created")
                deployed += 1
                manifest_paths.append(dst_file)

    # 3. session-knowledge.instructions.md (from templates root)
    sk_src = _TEMPLATES_DIR / "session-knowledge.instructions.md"
    sk_dst = instructions_dir / "session-knowledge.instructions.md"
    if sk_src.is_file():
        if sk_dst.is_file():
            if sk_dst.read_text(encoding="utf-8") == sk_src.read_text(encoding="utf-8"):
                print(f"  {INFO} session-knowledge.instructions.md — already up to date")
            else:
                sk_dst.write_text(sk_src.read_text(encoding="utf-8"), encoding="utf-8")
                print(f"  {OK} session-knowledge.instructions.md — updated")
                deployed += 1
                manifest_paths.append(sk_dst)
        else:
            sk_dst.write_text(sk_src.read_text(encoding="utf-8"), encoding="utf-8")
            print(f"  {OK} session-knowledge.instructions.md — created")
            deployed += 1
            manifest_paths.append(sk_dst)

    _record_managed_paths(manifest_paths)
    print(f"\n  Deployed {deployed} file(s) to {_tilde(github_dir)}")


def inject_global():
    """Inject session-knowledge section into global copilot-instructions.md."""
    print("\nGlobal Instructions Injection")
    print(f"  Target: {_tilde(GLOBAL_INSTRUCTIONS)}")

    # Warn if the canonical instructions target the pointer block references is missing.
    _sk_instructions = HOME / ".github" / "instructions" / "session-knowledge.instructions.md"
    if not _sk_instructions.is_file():
        print(
            f"  {WARN} session-knowledge.instructions.md not found at "
            f"{_tilde(_sk_instructions)}.\n"
            f"        Run `python3 install.py --deploy-instructions` first so the pointer "
            f"block does not reference a missing file."
        )

    # Ensure ~/.github/ exists
    GLOBAL_INSTRUCTIONS.parent.mkdir(parents=True, exist_ok=True)

    if GLOBAL_INSTRUCTIONS.is_file():
        content = GLOBAL_INSTRUCTIONS.read_text(encoding="utf-8")

        # Check if already injected
        if _INJECT_MARKER_START in content:
            # Replace existing block
            import re

            pattern = re.escape(_INJECT_MARKER_START) + r".*?" + re.escape(_INJECT_MARKER_END)
            new_content = re.sub(pattern, GLOBAL_INJECT_BLOCK.strip(), content, flags=re.DOTALL)
            if new_content != content:
                GLOBAL_INSTRUCTIONS.write_text(new_content, encoding="utf-8")
                print(f"  {OK} Updated existing session-knowledge section")
            else:
                print(f"  {INFO} Already up to date")
            return

        # Find insertion point: after the mandatory section header
        # Look for the numbered list in "BẮT BUỘC" section and insert after it
        lines = content.split("\n")
        insert_idx = None

        # Strategy: insert after the "KHÔNG ĐƯỢC:" block ends (first blank line after it)
        in_mandatory = False
        found_khong_duoc = False
        for i, line in enumerate(lines):
            if "BẮT BUỘC" in line:
                in_mandatory = True
            if in_mandatory and "KHÔNG ĐƯỢC" in line:
                found_khong_duoc = True
            if found_khong_duoc and line.strip() == "" and i > 0 and lines[i - 1].strip().startswith("- "):
                insert_idx = i + 1
                break

        if insert_idx is None:
            # Fallback: insert after the "---" separator or at position 2 (after title)
            for i, line in enumerate(lines):
                if line.strip() == "---" and i > 5:
                    insert_idx = i
                    break
            if insert_idx is None:
                insert_idx = 2  # After title

        lines.insert(insert_idx, "\n" + GLOBAL_INJECT_BLOCK)
        GLOBAL_INSTRUCTIONS.write_text("\n".join(lines), encoding="utf-8")
        print(f"  {OK} Injected session-knowledge section at line {insert_idx}")
    else:
        # Create new file with just the injection block
        header = "# Global Copilot Instructions\n\n"
        GLOBAL_INSTRUCTIONS.write_text(header + GLOBAL_INJECT_BLOCK, encoding="utf-8")
        print(f"  {OK} Created {_tilde(GLOBAL_INSTRUCTIONS)} with session-knowledge section")

    print(f"  {INFO} Injected pointer block — full policy lives in session-knowledge.instructions.md")


# ===================================================================
# 3. Self-Test
# ===================================================================


def run_self_test():
    """Import each tool module and verify the knowledge base."""
    print("\nSelf-Test Results:")

    tool_scripts = [f for f in TOOL_FILES if f != "install.py"]
    pass_count = 0
    fail_count = 0

    for filename in tool_scripts:
        filepath = TOOLS_DIR / filename
        if not filepath.is_file():
            print(f"  {FAIL} {filename} \u2014 not found")
            fail_count += 1
            continue
        try:
            spec = importlib.util.spec_from_file_location(
                filename.replace("-", "_").replace(".py", ""),
                str(filepath),
            )
            if spec and spec.loader:
                with open(filepath, encoding="utf-8") as fh:
                    compile(fh.read(), str(filepath), "exec")
                print(f"  {OK} {filename} \u2014 importable")
                pass_count += 1
            else:
                print(f"  {FAIL} {filename} \u2014 spec creation failed")
                fail_count += 1
        except SyntaxError as e:
            print(f"  {FAIL} {filename} \u2014 SyntaxError: {e.msg} (line {e.lineno})")
            fail_count += 1
        except Exception as e:
            print(f"  {FAIL} {filename} \u2014 {type(e).__name__}: {e}")
            fail_count += 1

    # Knowledge DB
    if DB_PATH.is_file():
        try:
            counts = _db_counts()
            print(f"  {OK} Knowledge DB \u2014 {counts['entries']} entries")
            pass_count += 1

            if _fts_working():
                print(f"  {OK} FTS index \u2014 working")
                pass_count += 1
            else:
                print(f"  {FAIL} FTS index \u2014 query failed")
                fail_count += 1
        except Exception as e:
            print(f"  {FAIL} Knowledge DB \u2014 {e}")
            fail_count += 1
    else:
        print(f"  {FAIL} Knowledge DB \u2014 not found at {_tilde(DB_PATH)}")
        fail_count += 1
        print(f"  {FAIL} FTS index \u2014 no DB")
        fail_count += 1

    # Watcher
    if _watcher_running():
        print(f"  {OK} Watcher \u2014 running")
        pass_count += 1
    else:
        print(f"  {FAIL} Watcher \u2014 not running")
        fail_count += 1

    total = pass_count + fail_count
    print(f"\n  {pass_count}/{total} checks passed", end="")
    if fail_count:
        print(f" ({fail_count} failed)")
    else:
        print(" \u2014 all good!")


# ===================================================================
# 4. Uninstall
# ===================================================================


def uninstall(non_interactive: bool = False) -> int:
    """Remove installed tools. Preserves session-state data.

    WBS-012: non_interactive=True skips the confirmation prompt and proceeds.
    """
    print("\nUninstall \u2014 Session Knowledge Tools")
    print("=" * 50)

    editable_source = _matching_editable_install_source_dir()
    if editable_source is not None:
        print(f"\n  {WARN} Detected an editable pip install for this checkout.")
        print("  Remove the `sk` console command first so it does not break:")
        print(f"    {_pip_uninstall_command()}")
        print("  Then rerun `python install.py --uninstall` if you also want to remove this checkout.")
        return 1

    managed_files: list[Path] = []
    for f in TOOL_FILES + SUPPORT_FILES:
        p = TOOLS_DIR / f
        if p.is_file():
            managed_files.append(p)
    managed_files.extend(_support_dir_files(TOOLS_DIR))

    removable, preserved_modified, preserved_untracked = _partition_manifest_removals(managed_files)
    runtime_removable: list[Path] = []

    pycache = TOOLS_DIR / "__pycache__"
    if pycache.is_dir():
        runtime_removable.append(pycache)

    watch_state = SESSION_STATE / ".watch-state.json"
    if watch_state.is_file():
        runtime_removable.append(watch_state)
    if LOCK_FILE.is_file():
        runtime_removable.append(LOCK_FILE)

    launcher_scripts = [s for s in _sk_launcher_script_paths() if s.is_file()]
    launcher_removable, launcher_modified, launcher_untracked = _partition_manifest_removals(launcher_scripts)

    if not removable and not runtime_removable and not launcher_removable:
        print("\n  Nothing to remove.")
        if preserved_modified or preserved_untracked or launcher_modified or launcher_untracked:
            print(f"  {WARN} Only preserved modified/untracked files remain.")
        return 0

    print("\n  Files to remove:")
    for p in removable:
        label = "dir " if p.is_dir() else ""
        print(f"    {label}{_tilde(p)}")
    for p in runtime_removable:
        label = "dir " if p.is_dir() else ""
        print(f"    {label}{_tilde(p)}")
    for s in launcher_removable:
        print(f"    {_tilde(s)}  (sk launcher)")

    preserved = preserved_modified + preserved_untracked + launcher_modified + launcher_untracked
    if preserved:
        print("\n  Preserved (manifest safety):")
        for p in preserved_modified:
            print(f"    {_tilde(p)}  (modified since install)")
        for p in preserved_untracked:
            print(f"    {_tilde(p)}  (no manifest entry)")
        for p in launcher_modified:
            print(f"    {_tilde(p)}  (modified sk launcher)")
        for p in launcher_untracked:
            print(f"    {_tilde(p)}  (untracked sk launcher)")

    print("\n  Preserved (your data):")
    print(f"    {_tilde(SESSION_STATE)}  (session data)")
    if DB_PATH.is_file():
        print(f"    {_tilde(DB_PATH)}  (knowledge database)")
    print(f"\n  {INFO} If you also exposed `sk` with pip, remove that wrapper separately:")
    print(f"    {_pip_uninstall_command()}")

    print()
    if non_interactive:
        print("  Proceeding (non-interactive mode).")
    else:
        try:
            answer = input("  Proceed with uninstall? [y/N] ").strip().lower()
        except (EOFError, KeyboardInterrupt):
            print("\n  Cancelled.")
            return 0

        if answer not in ("y", "yes"):
            print("  Cancelled.")
            return 0

    removed = 0
    had_error = False
    for p in removable + runtime_removable:
        try:
            if p.is_dir():
                shutil.rmtree(str(p))
            else:
                p.unlink()
            print(f"  {OK} Removed {_tilde(p)}")
            removed += 1
        except Exception as e:
            print(f"  {FAIL} Could not remove {_tilde(p)}: {e}")
            had_error = True

    if removable:
        _forget_managed_paths(removable)
    for rel_dir in SUPPORT_DIRS:
        _prune_empty_dir(TOOLS_DIR / rel_dir)

    if TOOLS_DIR.is_dir():
        remaining = list(TOOLS_DIR.iterdir())
        if not remaining:
            try:
                TOOLS_DIR.rmdir()
                print(f"  {OK} Removed empty {_tilde(TOOLS_DIR)}")
            except Exception:
                pass

    # Remove managed sk launcher files and PATH injections
    launcher_removed = uninstall_sk_launcher(quiet=False)
    removed += launcher_removed

    context_removed = _remove_registered_project_context_blocks(quiet=False)
    removed += context_removed

    print(f"\n  Uninstall complete \u2014 removed {removed} item(s).")
    print(f"  Session data preserved at {_tilde(SESSION_STATE)}")
    return 1 if had_error else 0


# ===================================================================
# 5. Install / First-Run
# ===================================================================


def install():
    """Copy tools into place and build the initial index."""
    print("\nInstalling Session Knowledge Tools...")
    print(f"  Target: {_tilde(TOOLS_DIR)}")

    TOOLS_DIR.mkdir(parents=True, exist_ok=True)
    print(f"  {OK} Tools directory ready")

    managed_paths: list[Path] = []
    source_dir = Path(__file__).resolve().parent
    if source_dir.resolve() != TOOLS_DIR.resolve():
        copied = 0
        for f in TOOL_FILES + SUPPORT_FILES:
            src = source_dir / f
            dst = TOOLS_DIR / f
            if src.is_file():
                shutil.copy2(str(src), str(dst))
                managed_paths.append(dst)
                copied += 1
        copied_support_dirs = 0
        for rel_dir in SUPPORT_DIRS:
            src_dir = source_dir / rel_dir
            dst_dir = TOOLS_DIR / rel_dir
            if not src_dir.is_dir():
                continue
            _replace_support_dir(src_dir, dst_dir)
            copied_support_dirs += 1
        print(f"  {OK} Copied {copied} files")
        if copied_support_dirs:
            noun = "directory" if copied_support_dirs == 1 else "directories"
            print(f"  {OK} Copied {copied_support_dirs} support {noun}")
    else:
        managed_paths.extend([TOOLS_DIR / f for f in TOOL_FILES + SUPPORT_FILES if (TOOLS_DIR / f).is_file()])
        print(f"  {OK} Scripts already in place")
    managed_paths.extend(_support_dir_files(TOOLS_DIR))

    print("\n  Building knowledge index...")
    if SESSION_STATE.is_dir():
        indexer = TOOLS_DIR / "build-session-index.py"
        if indexer.is_file():
            result = subprocess.run(
                [sys.executable, str(indexer)],
                capture_output=True,
                text=True,
                timeout=120,
            )
            if result.returncode == 0:
                for line in result.stdout.splitlines():
                    lo = line.lower()
                    if any(
                        k in lo
                        for k in [
                            "indexed",
                            "sessions:",
                            "documents:",
                            "fts",
                            "total",
                        ]
                    ):
                        print(f"    {line.strip()}")
                print(f"  {OK} Index built")
            else:
                print(f"  {FAIL} Indexer error: {result.stderr[:200]}")

        extractor = TOOLS_DIR / "extract-knowledge.py"
        if extractor.is_file():
            print("\n  Extracting knowledge...")
            result = subprocess.run(
                [sys.executable, str(extractor)],
                capture_output=True,
                text=True,
                timeout=120,
            )
            if result.returncode == 0:
                for line in result.stdout.splitlines():
                    lo = line.lower()
                    if any(
                        k in lo
                        for k in [
                            "extracted",
                            "total",
                            "category",
                            "entries",
                        ]
                    ):
                        print(f"    {line.strip()}")
                print(f"  {OK} Knowledge extracted")
    else:
        print(f"  {INFO} No session-state yet \u2014 index will be built on first use")

    print(f"\n{'=' * 50}")
    print("  Installation complete!")
    print(f"{'=' * 50}")
    # Install the managed sk launcher
    print("\n  Installing sk launcher...")
    install_sk_launcher()
    deploy_global_skills()
    _record_managed_paths(managed_paths)
    _show_usage_hints()


def _show_usage_hints():
    """Print helpful next-step commands."""
    qs = _tilde(TOOLS_DIR / "query-session.py")
    br = _tilde(TOOLS_DIR / "briefing.py")
    ws = _tilde(TOOLS_DIR / "watch-sessions.py")
    inst = _tilde(TOOLS_DIR / "install.py")
    launcher_dir = _tilde(SK_LAUNCHER_DIR)
    print("\n  Quick start (after opening a new shell or 'source ~/.zshrc'):")
    print('    sk briefing "your task"         # Context briefing (short command)')
    print('    sk query "search terms"          # Search knowledge base')
    print('    sk learn --mistake "Title" "..." # Record a mistake')
    print("    sk update                         # Pull latest tools update")
    print(f"\n  Launcher location: {launcher_dir}/sk  (managed by install.py --install-sk)")
    print("\n  Full path fallbacks also work:")
    print(f'    python {qs} "search terms"   # Search knowledge base')
    print(f'    python {br} "your task"       # Context briefing')
    print(f"    python {ws}                    # Start watcher daemon")
    print("\n  Management:")
    print(f"    python {inst} --install-sk             # Refresh sk launcher")
    print(f"    python {inst} --uninstall-launcher    # Remove sk launcher only")
    print(f"    python {inst} --deploy-skill          # Add skill to project")
    print(f"    python {inst} --deploy-global-skills  # Deploy skills to ~/.copilot/skills/")
    print(f"    python {inst} --deploy-hooks           # Deploy hooks")
    print(f"    python {inst} --deploy-instructions   # Deploy global instructions")
    print(f"    python {inst} --inject-global         # Add to global copilot-instructions")
    print(f"    python {inst} --install-git-hooks     # Install pre-commit/pre-push git hooks")
    print(f"    python {inst} --lock-hooks             # Lock hooks (tamper protection)")
    print(f"    python {inst} --unlock-hooks           # Unlock hooks for updates")
    print(f"    python {inst} --doctor --manifest      # Check manifest drift")
    print(f"    python {inst} --doctor --json          # Doctor output as JSON (non-zero exit on issues)")
    print(f"    python {inst} --test                  # Run self-test")
    print(f"    python {inst} --uninstall             # Remove tools")
    if os.name == "nt":
        print("\n  Windows Task Scheduler (auto-start sk watch at logon):")
        print(f"    python {inst} --setup-watch-task      # Register sk watch ONLOGON task (WBS-006)")
        print(f"    python {inst} --setup-watch-task --dry-run  # Preview without registering")
        print(f"    python {inst} --remove-watch-task     # Remove the scheduled task")
    if os.name == "nt" or platform.system() == "Linux":
        print("\n  Browse backend auto-start:")
        print(f"    python {inst} --setup-browse-service  # Register browse backend auto-start")
        print(f"    python {inst} --remove-browse-service # Remove browse backend auto-start")
    print("\n  Sync rollout note:")
    print("    sync-config.py --setup expects an HTTP(S) gateway URL (not raw Postgres/libSQL DSN)")
    print("    Default provider rollout recommendation: Neon (Postgres) + Railway (thin gateway host)")
    print("\n  Trend Scout automation note:")
    print(
        "    Use trend-scout.py / trend-scout.yml for scheduled scouting; do not wire it to preToolUse/postToolUse hooks"
    )


def _doctor_watcher_status() -> dict:
    """Return watcher health as a structured dict.

    Keys: running (bool), pid (int|None).
    """
    running = _watcher_running()
    pid: "int | None" = None
    if running and LOCK_FILE.is_file():
        try:
            raw = LOCK_FILE.read_text(encoding="utf-8").strip()
            data = json.loads(raw)
            if isinstance(data, int):
                pid = data
            elif isinstance(data, dict):
                pid = data.get("pid")
        except Exception:
            pass
    return {"running": running, "pid": pid}


def _doctor_db_size() -> dict:
    """Return DB size in MB as a structured dict.

    Keys: size_mb (float), db_path (str), exists (bool).
    """
    exists = DB_PATH.is_file()
    size_mb = 0.0
    if exists:
        try:
            size_mb = round(DB_PATH.stat().st_size / (1024 * 1024), 1)
        except OSError:
            pass
    return {"size_mb": size_mb, "db_path": str(DB_PATH), "exists": exists}


def _doctor_index_health() -> dict:
    """Return index health score by calling knowledge-health.py --json.

    Keys: score (float|None), total (int), available (bool), error (str).
    Fails open: if the script is missing or crashes, available=False.
    """
    health_script = _SCRIPT_DIR / "knowledge-health.py"
    if not health_script.is_file():
        return {"score": None, "total": 0, "available": False, "error": "knowledge-health.py not found"}
    try:
        result = subprocess.run(
            [sys.executable, str(health_script), "--json"],
            capture_output=True,
            text=True,
            timeout=15,
        )
        if result.returncode != 0 or not result.stdout.strip():
            return {"score": None, "total": 0, "available": False, "error": result.stderr.strip()[:200]}
        data = json.loads(result.stdout)
        return {
            "score": data.get("score"),
            "total": data.get("total", 0),
            "available": True,
            "error": "",
        }
    except Exception as exc:
        return {"score": None, "total": 0, "available": False, "error": str(exc)[:200]}


def _doctor_sync_status() -> dict:
    """Return sync status by calling sync-status.py --json.

    Keys: configured (bool), gateway_available (bool), available (bool), error (str).
    Fails open: if the script is missing or crashes, available=False.
    """
    sync_script = _SCRIPT_DIR / "sync-status.py"
    if not sync_script.is_file():
        return {"configured": False, "gateway_available": False, "available": False, "error": "sync-status.py not found"}
    try:
        result = subprocess.run(
            [sys.executable, str(sync_script), "--json"],
            capture_output=True,
            text=True,
            timeout=10,
        )
        if result.returncode != 0 or not result.stdout.strip():
            return {"configured": False, "gateway_available": False, "available": False, "error": result.stderr.strip()[:200]}
        data = json.loads(result.stdout)
        gateway_health = data.get("gateway_health", {})
        return {
            "configured": bool(data.get("configured", False)),
            "gateway_available": bool(gateway_health.get("available", False)),
            "available": True,
            "error": "",
        }
    except Exception as exc:
        return {"configured": False, "gateway_available": False, "available": False, "error": str(exc)[:200]}


def _doctor_hooks_count() -> dict:
    """Return count of installed hook entries from ~/.copilot/hooks/hooks.json.

    Keys: count (int), hooks_json_exists (bool), error (str).
    """
    hooks_json = COPILOT_DIR / "hooks" / "hooks.json"
    if not hooks_json.is_file():
        return {"count": 0, "hooks_json_exists": False, "error": "hooks.json not found"}
    try:
        data = json.loads(hooks_json.read_text(encoding="utf-8"))
        hooks = data.get("hooks", {})
        if isinstance(hooks, dict):
            count = sum(len(v) if isinstance(v, list) else 1 for v in hooks.values())
        elif isinstance(hooks, list):
            count = len(hooks)
        else:
            count = 0
        return {"count": count, "hooks_json_exists": True, "error": ""}
    except Exception as exc:
        return {"count": 0, "hooks_json_exists": True, "error": str(exc)[:200]}


def doctor(*, manifest_only: bool = False, as_json: bool = False) -> int:
    """Verify install health; optionally limit output to manifest drift only.

    When as_json=True, prints a JSON dict with an issues[] array and returns
    non-zero when issues are present.
    """
    issues = 0
    issues_list: list[dict] = []

    def _add_issue(code: str, message: str, severity: str = "error") -> None:
        issues_list.append({"code": code, "message": message, "severity": severity})

    if not manifest_only:
        if not as_json:
            print("\nInstall Doctor")
            print("=" * 50)
        if as_json:
            import io as _io_doctor
            _dev_null = _io_doctor.StringIO()
            _orig_stdout = sys.stdout
            sys.stdout = _dev_null
            try:
                _core_ok = show_status()
                _launcher_issues = _launcher_diagnostics()
            finally:
                sys.stdout = _orig_stdout
        else:
            _core_ok = show_status()
            _launcher_issues = _launcher_diagnostics()
        if _core_ok:
            if not as_json:
                print(f"  {OK} Core install looks present")
        else:
            issues += 1
            _add_issue("core-install-missing", "Core install not detected")
        issues += _launcher_issues

        # --- Global Health Surface ---
        if not as_json:
            print("\nGlobal Health:")

        # Watcher status
        watcher = _doctor_watcher_status()
        if watcher["running"]:
            if not as_json:
                pid_info = f" (pid {watcher['pid']})" if watcher["pid"] else ""
                print(f"  {OK} Watcher: running{pid_info}")
        else:
            issues += 1
            _add_issue("watcher-not-running", "Session watcher is not running", "warning")
            if not as_json:
                print(f"  {WARN} Watcher: not running")

        # DB size
        db_info = _doctor_db_size()
        if db_info["exists"]:
            if not as_json:
                print(f"  {OK} DB size: {db_info['size_mb']} MB")
        else:
            issues += 1
            _add_issue("db-not-found", "Knowledge DB not found", "warning")
            if not as_json:
                print(f"  {WARN} DB: not found at {_tilde(DB_PATH)}")

        # Index health score
        health_info = _doctor_index_health()
        if health_info["available"]:
            score = health_info["score"]
            total = health_info["total"]
            if not as_json:
                print(f"  {OK} Index health: {score}/100 ({total} entries)")
            if score is not None and score < 50:
                issues += 1
                _add_issue("index-health-low", f"Index health score {score}/100 is below 50", "warning")
        else:
            if not as_json:
                print(f"  {INFO} Index health: unavailable ({health_info['error']})")

        # Sync status
        sync_info = _doctor_sync_status()
        if sync_info["available"]:
            if sync_info["configured"]:
                if sync_info["gateway_available"]:
                    if not as_json:
                        print(f"  {OK} Sync: configured, gateway reachable")
                else:
                    issues += 1
                    _add_issue("sync-gateway-unreachable", "Sync is configured but gateway is unreachable", "warning")
                    if not as_json:
                        print(f"  {WARN} Sync: configured but gateway unreachable")
            else:
                if not as_json:
                    print(f"  {INFO} Sync: not configured (local-only)")
        else:
            if not as_json:
                print(f"  {INFO} Sync status: unavailable ({sync_info['error']})")

        # Hooks installed count
        hooks_info = _doctor_hooks_count()
        if hooks_info["hooks_json_exists"] and not hooks_info["error"]:
            if not as_json:
                print(f"  {OK} Hooks installed: {hooks_info['count']} hook entries")
            if hooks_info["count"] == 0:
                issues += 1
                _add_issue("no-hooks-installed", "hooks.json exists but contains no hook entries", "warning")
        elif not hooks_info["hooks_json_exists"]:
            issues += 1
            _add_issue("hooks-json-missing", "hooks.json not found — run: python install.py --deploy-hooks", "error")
            if not as_json:
                print(f"  {FAIL} Hooks: hooks.json missing — run: python install.py --deploy-hooks")

        if not as_json:
            print()

    manifest_path = _managed_manifest_path()
    if not as_json:
        print("Manifest Drift:")
    if not manifest_path.exists():
        if not as_json:
            print(f"  {WARN} No manifest found at {_tilde(manifest_path)}")
        _add_issue("manifest-missing", f"No manifest found at {_tilde(manifest_path)}", "warning")
        if as_json:
            print(json.dumps({
                "issues": issues_list,
                "issue_count": issues + 1,
                "watcher": _doctor_watcher_status() if manifest_only else {},
                "db": _doctor_db_size() if manifest_only else {},
                "index_health": _doctor_index_health() if manifest_only else {},
                "sync": _doctor_sync_status() if manifest_only else {},
                "hooks": _doctor_hooks_count() if manifest_only else {},
            }, indent=2))
        return issues + 1

    manifest = _load_managed_manifest()
    if manifest is None:
        if not as_json:
            print(f"  {FAIL} Manifest is unreadable: {_tilde(manifest_path)}")
        _add_issue("manifest-unreadable", f"Manifest is unreadable at {_tilde(manifest_path)}", "error")
        if as_json:
            print(json.dumps({
                "issues": issues_list,
                "issue_count": issues + 1,
                "watcher": _doctor_watcher_status() if manifest_only else {},
                "db": _doctor_db_size() if manifest_only else {},
                "index_health": _doctor_index_health() if manifest_only else {},
                "sync": _doctor_sync_status() if manifest_only else {},
                "hooks": _doctor_hooks_count() if manifest_only else {},
            }, indent=2))
        return issues + 1

    missing, modified, unsafe, tracked = _manifest_drift_report()
    if not as_json:
        print(f"  {OK} Manifest present: {_tilde(manifest_path)}")
        print(f"  {OK} Tracked files: {tracked}")
    if not missing and not modified and not unsafe:
        if not as_json:
            print(f"  {OK} No drift detected")
        if as_json:
            print(json.dumps({
                "issues": issues_list,
                "issue_count": issues,
                "watcher": _doctor_watcher_status(),
                "db": _doctor_db_size(),
                "index_health": _doctor_index_health(),
                "sync": _doctor_sync_status(),
                "hooks": _doctor_hooks_count(),
            }, indent=2))
        return issues

    if missing:
        issues += len(missing)
        for key in missing:
            _add_issue("manifest-missing-file", f"Missing file: {key}", "warning")
        if not as_json:
            print(f"  {WARN} Missing files ({len(missing)}):")
            for key in missing:
                print(f"    - {key}")
    if modified:
        issues += len(modified)
        for key in modified:
            _add_issue("manifest-modified-file", f"Modified file: {key}", "warning")
        if not as_json:
            print(f"  {WARN} Modified files ({len(modified)}):")
            for key in modified:
                print(f"    - {key}")
    if unsafe:
        issues += len(unsafe)
        for key in unsafe:
            _add_issue("manifest-unsafe-entry", f"Unsafe manifest entry: {key}", "error")
        if not as_json:
            print(f"  {FAIL} Unsafe manifest entries ({len(unsafe)}):")
            for key in unsafe:
                print(f"    - {key}")

    if as_json:
        print(json.dumps({
            "issues": issues_list,
            "issue_count": issues,
            "watcher": _doctor_watcher_status(),
            "db": _doctor_db_size(),
            "index_health": _doctor_index_health(),
            "sync": _doctor_sync_status(),
            "hooks": _doctor_hooks_count(),
        }, indent=2))
    return issues


def _launcher_diagnostics() -> int:
    """Print launcher/PATH diagnostics for the current process."""
    issues = 0
    print("\nLauncher Diagnostics:")

    launcher_path, launcher_ok, launcher_detail = _launcher_probe()
    if launcher_path is None:
        print(f"  {FAIL} sk launcher file: not found in {_tilde(SK_LAUNCHER_DIR)}")
        issues += 1
    else:
        print(f"  {OK} sk launcher file: {_tilde(launcher_path)}")
        if launcher_ok:
            print(f"  {OK} Direct launcher runs: {launcher_detail}")
        else:
            print(f"  {FAIL} Direct launcher failed: {launcher_detail}")
            issues += 1

    current_has_path = _current_path_has_launcher_dir()
    if current_has_path:
        print(f"  {OK} Current process PATH includes {_tilde(SK_LAUNCHER_DIR)}")
    elif os.name == "nt":
        print(f"  {WARN} Current process PATH is missing {_tilde(SK_LAUNCHER_DIR)}")
        print(f"    Refresh this PowerShell/Copilot session: {_windows_current_path_refresh_command()}")
        print("    Or restart the terminal/Copilot CLI to inherit the user PATH.")
    else:
        print(f"  {WARN} Current process PATH is missing {_tilde(SK_LAUNCHER_DIR)}")

    sk_path = _which_command("sk")
    if sk_path and Path(sk_path).suffix.lower() != ".py":
        print(f"  {OK} `sk` resolves in this process: {sk_path}")
    elif sk_path:
        print(f"  {WARN} `sk` resolves to an unexpected Python script path: {sk_path}")
        print("    Refresh PATH so the managed launcher or native binary wins.")
    elif current_has_path:
        print(f"  {FAIL} `sk` does not resolve even though {_tilde(SK_LAUNCHER_DIR)} is on PATH")
        issues += 1
    else:
        print(f"  {WARN} `sk` does not resolve in this process until PATH is refreshed")

    if os.name == "nt":
        user_path, user_path_error = _read_windows_user_path()
        if user_path_error:
            print(f"  {WARN} Windows user PATH could not be read: {user_path_error}")
        elif user_path is not None and _path_contains_dir(user_path, SK_LAUNCHER_DIR, delimiter=";"):
            print(f"  {OK} Windows user PATH includes {_tilde(SK_LAUNCHER_DIR)}")
        else:
            print(f"  {FAIL} Windows user PATH is missing {_tilde(SK_LAUNCHER_DIR)}")
            issues += 1

        python3_path = _which_command("python3")
        if _python3_alias_risk(python3_path=python3_path):
            print(f"  {WARN} `python3` resolves to Windows Store alias: {python3_path}")
            print('    On Windows, use `python "$env:USERPROFILE\\.copilot\\tools\\sk.py" ...`')
            print("    or refresh PATH so ~/.copilot/bin/python3.cmd wins.")
        elif python3_path:
            print(f"  {OK} `python3` resolution is not the Windows Store alias: {python3_path}")
        else:
            print(f"  {INFO} `python3` is not on PATH; use `python` or `py` on Windows.")

        # WBS-008: CRLF and SK_TOOLS_DIR checks on sk.cmd
        for script in _sk_launcher_script_paths():
            if script.is_file():
                raw = script.read_bytes()
                if b"\r\n" in raw:
                    print(f"  {OK} {script.name} uses CRLF line endings (correct for Windows)")
                else:
                    print(f"  {WARN} {script.name} missing CRLF line endings — reinstall launcher to fix")
                    issues += 1
                content_str = raw.decode("utf-8", errors="replace")
                if "py -3" in content_str:
                    print(f"  {OK} {script.name} uses py -3 (Python Launcher preferred)")
                else:
                    print(f"  {WARN} {script.name} missing py -3 — reinstall launcher to fix")
                    issues += 1
                if "PYTHON_EXE" in content_str:
                    print(f"  {OK} {script.name} includes installer Python fallback")
                else:
                    print(f"  {WARN} {script.name} missing installer Python fallback — reinstall launcher to fix")
                    issues += 1
                if "python3" in content_str:
                    print(f"  {OK} {script.name} includes python3 fallback")
                else:
                    print(f"  {WARN} {script.name} missing python3 fallback — reinstall launcher to fix")
                    issues += 1
                if "SK_TOOLS_DIR" in content_str:
                    print(f"  {OK} {script.name} honors SK_TOOLS_DIR env var")
                else:
                    print(f"  {WARN} {script.name} missing SK_TOOLS_DIR support — reinstall launcher to fix")
                    issues += 1

        # WBS-008: check SK_TOOLS_DIR env var
        sk_tools_dir_env = os.environ.get("SK_TOOLS_DIR")
        if sk_tools_dir_env:
            p = Path(sk_tools_dir_env)
            if p.is_dir():
                print(f"  {OK} SK_TOOLS_DIR set and valid: {sk_tools_dir_env}")
            else:
                print(f"  {WARN} SK_TOOLS_DIR set but directory missing: {sk_tools_dir_env}")
                issues += 1
        else:
            print(f"  {INFO} SK_TOOLS_DIR not set (using default: {_tilde(TOOLS_DIR)})")

    return issues


# ===================================================================
# Main
# ===================================================================


def lock_hooks():
    """Lock hook files with OS-level immutable flags + SHA256 manifest.

    macOS: chflags schg (system immutable — requires sudo)
    Linux: chattr +i (needs sudo/root)
    Windows: attrib +R
    Also: generates HMAC secret, sanitizes config.json, locks config.json.
    """
    import hashlib
    import platform
    import secrets

    hooks_dir = _SCRIPT_DIR / "hooks"
    real_home = _real_home()
    hooks_dst_dir = real_home / ".copilot" / "hooks"
    hooks_dst = hooks_dst_dir / "hooks.json"
    config_json = real_home / ".copilot" / "config.json"
    secret_path = hooks_dst_dir / ".marker-secret"

    print("\n🔒 Lock Hooks — Tamper Protection")
    system = platform.system()

    def _unlock_for_manifest_write(path):
        """Best-effort removal of immutable flags before rewriting manifest."""
        if not path.is_file():
            return True
        if system == "Darwin":
            is_root = os.geteuid() == 0
            for flag in ("noschg", "nouchg"):
                cmd = ["chflags", flag, str(path)] if is_root else ["sudo", "chflags", flag, str(path)]
                result = subprocess.run(cmd, capture_output=True, text=True)
                if result.returncode == 0:
                    return True
            err = result.stderr.strip() or "unknown error"
            print(f"  {FAIL} Could not unlock existing manifest for rewrite: {err}")
            return False
        if system == "Linux":
            if str(path).startswith("/mnt/"):
                try:
                    path.chmod(0o644)
                    return True
                except OSError as e:
                    print(f"  {FAIL} Could not chmod existing manifest for rewrite: {e}")
                    return False
            is_root = os.geteuid() == 0
            cmd = ["chattr", "-i", str(path)] if is_root else ["sudo", "chattr", "-i", str(path)]
            result = subprocess.run(cmd, capture_output=True, text=True)
            if result.returncode == 0:
                return True
            err = result.stderr.strip() or "unknown error"
            print(f"  {FAIL} Could not unlock existing manifest for rewrite: {err}")
            return False
        if system == "Windows":
            subprocess.run(["attrib", "-R", str(path)], capture_output=True, text=True)
        return True

    # Collect all hook files to protect (including rules/ subdirectory)
    hook_files = sorted(hooks_dir.glob("*.py")) if hooks_dir.is_dir() else []
    rules_dir = hooks_dir / "rules"
    if rules_dir.is_dir():
        hook_files += sorted(rules_dir.glob("*.py"))
    if not hook_files:
        print(f"  {FAIL} No hook scripts found in {_tilde(hooks_dir)}")
        return

    # 0. Generate HMAC secret if not exists
    hooks_dst_dir.mkdir(parents=True, exist_ok=True)
    if not secret_path.is_file():
        secret_path.write_text(secrets.token_hex(32), encoding="utf-8")
        print(f"  {OK} HMAC secret generated: {_tilde(secret_path)}")
    else:
        print(f"  {OK} HMAC secret exists: {_tilde(secret_path)}")

    # 0b. Sanitize config.json — remove disableAllHooks
    if config_json.is_file():
        try:
            cfg = json.loads(config_json.read_text(encoding="utf-8"))
            if "disableAllHooks" in cfg:
                del cfg["disableAllHooks"]
                config_json.write_text(json.dumps(cfg, indent=2), encoding="utf-8")
                print(f"  {WARN} Removed disableAllHooks from config.json!")
            else:
                print(f"  {OK} config.json clean (no disableAllHooks)")
        except Exception as e:
            print(f"  {WARN} Could not check config.json: {e}")

    # 0c. Clear tamper marker since we're re-locking
    tamper_marker = real_home / ".copilot" / "markers" / "hooks-tampered"
    if tamper_marker.is_file():
        try:
            tamper_marker.unlink()
            print(f"  {OK} Cleared hooks-tampered kill-switch")
        except Exception:
            pass

    # 1. Generate SHA256 manifest
    manifest = {"files": {}, "hooks_json": None}
    for hf in hook_files:
        h = hashlib.sha256(hf.read_bytes()).hexdigest()
        # Use relative path for subdirectory files (e.g., rules/briefing.py)
        rel_name = hf.relative_to(hooks_dir).as_posix()
        manifest["files"][rel_name] = h
        print(f"  {OK} {rel_name}: {h[:16]}...")

    if hooks_dst.is_file():
        h = hashlib.sha256(hooks_dst.read_bytes()).hexdigest()
        manifest["hooks_json"] = h
        print(f"  {OK} hooks.json: {h[:16]}...")

    # Save manifest
    manifest_path = hooks_dst_dir / "integrity-manifest.json"
    hooks_dst_dir.mkdir(parents=True, exist_ok=True)
    if not _unlock_for_manifest_write(manifest_path):
        print(f"  {WARN} Run: sudo python3 {_tilde(_SCRIPT_DIR / 'install.py')} --unlock-hooks")
        return
    manifest_path.write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    print(f"\n  {OK} Manifest saved: {_tilde(manifest_path)}")

    # 2. Set OS-level immutable flags
    protected = 0

    files_to_lock = list(hook_files) + [hooks_dst, manifest_path, secret_path, config_json]

    if system == "Darwin":
        # schg = system immutable — requires sudo to set, cannot be removed without sudo
        # Much stronger than uchg (user immutable) which same user can remove
        is_root = os.geteuid() == 0
        flag = "schg"
        for f in files_to_lock:
            if f.is_file():
                cmd = ["chflags", flag, str(f)] if is_root else ["sudo", "chflags", flag, str(f)]
                result = subprocess.run(cmd, capture_output=True, text=True)
                if result.returncode == 0:
                    protected += 1
                else:
                    err = result.stderr.strip() or "unknown error"
                    print(f"  {FAIL} chflags {flag} failed: {f.name} ({err})")
        if protected:
            print(f"\n  {OK} {protected} files locked (chflags {flag} — system immutable)")
            print("  To unlock: sudo python3 install.py --unlock-hooks")
        else:
            print(f"\n  {WARN} chflags {flag} requires sudo. Run: sudo python3 install.py --lock-hooks")

    elif system == "Linux":
        # chattr +i requires root — detect if already root to avoid double-sudo
        is_root = os.geteuid() == 0
        chmod_fallback = 0
        for f in files_to_lock:
            if f.is_file():
                # WSL: files on /mnt/ (NTFS) don't support chattr — fallback to chmod
                on_ntfs = str(f).startswith("/mnt/")
                if on_ntfs:
                    try:
                        f.chmod(0o444)
                        chmod_fallback += 1
                    except OSError as e:
                        print(f"  {FAIL} chmod failed: {f.name} ({e})")
                else:
                    cmd = ["chattr", "+i", str(f)] if is_root else ["sudo", "chattr", "+i", str(f)]
                    result = subprocess.run(cmd, capture_output=True, text=True)
                    if result.returncode == 0:
                        protected += 1
                    else:
                        err = result.stderr.strip() or "unknown error"
                        print(f"  {FAIL} chattr failed: {f.name} ({err})")
        if protected:
            print(f"\n  {OK} {protected} files locked (chattr +i)")
        if chmod_fallback:
            print(f"  {OK} {chmod_fallback} files set read-only (chmod 444 — WSL/NTFS fallback)")
        if not protected and not chmod_fallback:
            print(f"\n  {WARN} chattr requires root. Run: sudo python3 install.py --lock-hooks")

    elif system == "Windows":
        for f in files_to_lock:
            if f.is_file():
                result = subprocess.run(["attrib", "+R", str(f)], capture_output=True, text=True)
                if result.returncode == 0:
                    protected += 1
        print(f"\n  {OK} {protected} files set read-only (attrib +R)")
        print("  Note: attrib +R is weaker than Unix immutable flags")

    else:
        print(f"\n  {WARN} Unknown OS: {system}. Manual protection needed.")

    print("\n  Agent CANNOT modify hook files without unlocking first.")


def unlock_hooks():
    """Remove OS-level immutable flags from hook files, config.json, and secret."""
    import platform

    hooks_dir = _SCRIPT_DIR / "hooks"
    real_home = _real_home()
    hooks_dst_dir = real_home / ".copilot" / "hooks"
    hooks_dst = hooks_dst_dir / "hooks.json"
    manifest_path = hooks_dst_dir / "integrity-manifest.json"
    config_json = real_home / ".copilot" / "config.json"
    secret_path = hooks_dst_dir / ".marker-secret"

    print("\n🔓 Unlock Hooks")

    hook_files = sorted(hooks_dir.glob("*.py")) if hooks_dir.is_dir() else []
    rules_dir = hooks_dir / "rules"
    if rules_dir.is_dir():
        hook_files += sorted(rules_dir.glob("*.py"))
    files_to_unlock = list(hook_files) + [hooks_dst, manifest_path, secret_path, config_json]

    system = platform.system()
    unlocked = 0

    if system == "Darwin":
        is_root = os.geteuid() == 0
        for f in files_to_unlock:
            if f.is_file():
                # Try noschg first (system immutable), fallback to nouchg (user immutable)
                cmd = ["chflags", "noschg", str(f)] if is_root else ["sudo", "chflags", "noschg", str(f)]
                result = subprocess.run(cmd, capture_output=True, text=True)
                if result.returncode == 0:
                    unlocked += 1
                else:
                    # Fallback: try nouchg for legacy locks
                    cmd2 = ["chflags", "nouchg", str(f)]
                    result2 = subprocess.run(cmd2, capture_output=True, text=True)
                    if result2.returncode == 0:
                        unlocked += 1
        print(f"  {OK} {unlocked} files unlocked")

    elif system == "Linux":
        is_root = os.geteuid() == 0
        for f in files_to_unlock:
            if f.is_file():
                on_ntfs = str(f).startswith("/mnt/")
                if on_ntfs:
                    try:
                        f.chmod(0o644)
                        unlocked += 1
                    except OSError:
                        pass
                else:
                    cmd = ["chattr", "-i", str(f)] if is_root else ["sudo", "chattr", "-i", str(f)]
                    result = subprocess.run(cmd, capture_output=True, text=True)
                    if result.returncode == 0:
                        unlocked += 1
                    else:
                        err = result.stderr.strip() or "unknown error"
                        print(f"  {FAIL} chattr unlock failed: {f.name} ({err})")
        print(f"  {OK} {unlocked} files unlocked (chattr -i / chmod 644)")

    elif system == "Windows":
        for f in files_to_unlock:
            if f.is_file():
                result = subprocess.run(["attrib", "-R", str(f)], capture_output=True, text=True)
                if result.returncode == 0:
                    unlocked += 1
        print(f"  {OK} {unlocked} files unlocked (attrib -R)")

    print("  ⚠️  Re-lock after updates: python3 install.py --lock-hooks")


def repair_hooks():
    """Clear the hooks-tampered kill-switch without requiring sudo.

    The hooks-tampered marker file has no OS-level immutable flag, so any
    user process can delete it.  Use this when the marker is blocking agent
    sessions but you cannot or do not want to run --lock-hooks with sudo.

    Does NOT regenerate the integrity manifest (that still requires sudo).
    Run 'sudo python3 install.py --lock-hooks' afterwards to fully reset.
    """
    print("\n🔧 Repair Hooks — Clear hooks-tampered marker (no sudo required)")

    real_home = _real_home()
    tamper_marker = real_home / ".copilot" / "markers" / "hooks-tampered"

    if tamper_marker.is_file():
        try:
            tamper_marker.unlink()
            print(f"  {OK} Cleared hooks-tampered marker: {_tilde(tamper_marker)}")
        except Exception as e:
            print(f"  {FAIL} Could not clear marker: {e}")
            print(f"  Run: rm -f {tamper_marker}")
            return
    else:
        print(f"  {OK} No hooks-tampered marker present — nothing to clear")

    print()
    print("  ℹ  Agent operations are now unblocked.")
    print("  ℹ  To fully reset integrity manifest, run (with sudo):")
    print(f"       sudo python3 {_tilde(_SCRIPT_DIR / 'install.py')} --lock-hooks")


def install_git_hooks(target_dir: "Path | None" = None, non_interactive: bool = False) -> None:
    """Install pre-commit and pre-push git hooks into a repository's .git/hooks/.

    Copies hooks/pre-commit and hooks/pre-push from the tools source tree into
    <target_dir>/.git/hooks/ (or the git root of cwd if target_dir is None).
    Makes both files executable (chmod +x on POSIX).  Does NOT overwrite existing
    hooks that differ without interactive confirmation.

    The installed hooks reference $HOME/.copilot/tools unconditionally so they
    work correctly in any repo, not just the tools repo itself.

    WBS-012: non_interactive=True skips the overwrite prompt and skips differing hooks.
    """
    print("\nInstall Git Hooks (pre-commit / pre-push)")

    hooks_src_dir = _SCRIPT_DIR / "hooks"
    hook_names = ["pre-commit", "pre-push"]

    if target_dir is None:
        target_dir = _git_root()
    if target_dir is None:
        print(f"  {FAIL} Not inside a git repository — run from a project directory.")
        return

    git_hooks_dir = target_dir / ".git" / "hooks"
    if not git_hooks_dir.is_dir():
        print(f"  {FAIL} .git/hooks/ not found at {git_hooks_dir}")
        return

    installed = []
    skipped = []

    for hook_name in hook_names:
        src = hooks_src_dir / hook_name
        dst = git_hooks_dir / hook_name

        if not src.is_file():
            print(f"  {FAIL} Source hook not found: {_tilde(src)}")
            continue

        src_text = _git_hook_install_text(src.read_text(encoding="utf-8"))

        if dst.is_file():
            dst_text = dst.read_text(encoding="utf-8")
            if dst_text == src_text:
                print(f"  {INFO} {hook_name} — already up to date")
                skipped.append(hook_name)
                continue
            if not sys.stdin.isatty() or non_interactive:
                print(
                    f"  {WARN} {hook_name} already exists and differs — "
                    "skipping (non-interactive). Back it up and re-run to overwrite."
                )
                skipped.append(hook_name)
                continue
            try:
                answer = input(f"  {WARN} {hook_name} already exists and differs. Overwrite? [y/N] ").strip().lower()
            except (EOFError, KeyboardInterrupt):
                print(f"\n  Skipping {hook_name}.")
                skipped.append(hook_name)
                continue
            if answer not in ("y", "yes"):
                print(f"  Skipping {hook_name}.")
                skipped.append(hook_name)
                continue
            backup = dst.with_name(hook_name + ".backup")
            shutil.copy2(str(dst), str(backup))
            print(f"  {INFO} Backed up existing hook to {backup.name}")

        dst.write_text(src_text, encoding="utf-8")
        if os.name != "nt":
            dst.chmod(dst.stat().st_mode | 0o111)
        installed.append(hook_name)
        print(f"  {OK} {hook_name} installed → {_tilde(dst)}")

    if installed:
        try:
            r = subprocess.run(
                ["git", "-C", str(target_dir), "config", "core.hooksPath", ".git/hooks"],
                capture_output=True,
                text=True,
                timeout=5,
            )
            if r.returncode == 0:
                print(f"  {OK} core.hooksPath confirmed as .git/hooks")
        except Exception:
            pass

    total = len(installed) + len(skipped)
    if not installed and not skipped:
        print(f"  {FAIL} Nothing installed.")
    else:
        print(f"\n  {len(installed)} installed, {len(skipped)} already up to date (of {total} hooks).")
        print("  Hooks block git commit/push when dispatched-subagent-active marker is fresh.")
        print("  NOTE: After each 'auto-update-tools.py' run, re-run --install-git-hooks here")
        print("        to pick up new hook logic (auto-update cannot do this for you safely).")


def _dispatch_healer(flag: str) -> None:
    """Dispatch to copilot-cli-healer.py for schedule management."""
    healer = _SCRIPT_DIR / "copilot-cli-healer.py"
    if not healer.exists():
        print(f"  {FAIL} copilot-cli-healer.py not found at {_tilde(healer)}")
        return
    import subprocess as _sp

    _sp.run([sys.executable, str(healer), flag])


def main():
    args = sys.argv[1:]

    if "--help" in args or "-h" in args:
        print(__doc__)
        return

    if "--install-healer" in args:
        _dispatch_healer("--install-schedule")
        return

    if "--uninstall-healer" in args:
        _dispatch_healer("--uninstall-schedule")
        return

    # WBS-012: global flags parsed once
    dry_run = "--dry-run" in args
    non_interactive = "--non-interactive" in args

    if "--install-sk" in args:
        quiet = "--quiet" in args
        if not quiet:
            print("\nInstalling sk launcher...")
        install_sk_launcher(quiet=quiet, dry_run=dry_run)
        return

    if "--setup-watch-task" in args:
        quiet = "--quiet" in args
        if not quiet:
            print("\nSetting up Windows Task Scheduler for sk watch (WBS-006)...")
        setup_windows_watch_task(dry_run=dry_run, quiet=quiet)
        return

    if "--remove-watch-task" in args:
        quiet = "--quiet" in args
        if not quiet:
            print("\nRemoving Windows Task Scheduler task for sk watch...")
        remove_windows_watch_task(quiet=quiet)
        return

    if "--setup-browse-service" in args:
        quiet = "--quiet" in args
        if not quiet:
            print("\nSetting up browse-backend auto-start service...")
        if os.name == "nt":
            setup_windows_browse_task(dry_run=dry_run, quiet=quiet)
        elif platform.system() == "Linux":
            setup_linux_browse_service(dry_run=dry_run, quiet=quiet)
        elif platform.system() == "Darwin":
            if not quiet:
                print(f"  {INFO} macOS uses launchd — run: python launchd/install-launchd.py")
        return

    if "--remove-browse-service" in args:
        quiet = "--quiet" in args
        if not quiet:
            print("\nRemoving browse-backend auto-start service...")
        if os.name == "nt":
            remove_windows_browse_task(quiet=quiet)
        elif platform.system() == "Linux":
            remove_linux_browse_service(quiet=quiet)
        elif platform.system() == "Darwin":
            if not quiet:
                print(f"  {INFO} macOS uses launchd — run: python launchd/install-launchd.py --uninstall")
        return

    if "--install-binary" in args:
        extra = [a for a in args if a not in ("--install-binary",)]
        import subprocess as _sp

        _sp.run([sys.executable, str(_SCRIPT_DIR / "install-binary.py")] + extra)
        return

    if "--uninstall-launcher" in args:
        quiet = "--quiet" in args
        if not quiet:
            print("\nRemoving sk launcher...")
        uninstall_sk_launcher(quiet=quiet)
        return

    if "--deploy-skill" in args:
        deploy_skill()
        return

    if "--deploy-global-skills" in args:
        deploy_global_skills()
        return

    if "--deploy-hooks" in args:
        deploy_hooks()
        return

    if "--install-git-hooks" in args:
        install_git_hooks(non_interactive=non_interactive)
        return

    if "--lock-hooks" in args:
        lock_hooks()
        return

    if "--repair-hooks" in args:
        repair_hooks()
        return

    if "--unlock-hooks" in args:
        unlock_hooks()
        return

    if "--deploy-instructions" in args:
        deploy_instructions()
        return

    if "--inject-global" in args:
        inject_global()
        return

    if "--test" in args:
        run_self_test()
        return

    if "--doctor" in args or "--windows" in args:
        manifest_only = "--manifest" in args
        as_json = "--json" in args
        rc = doctor(manifest_only=manifest_only, as_json=as_json)
        if as_json:
            return 1 if rc else 0
        return rc

    if "--uninstall" in args:
        return uninstall(non_interactive=non_interactive)

    # Default: show status, then install if needed or show hints
    installed = show_status()
    if not installed:
        print()
        install()
    else:
        print("\n  Tools are installed and ready.")
        _show_usage_hints()


if __name__ == "__main__":
    raise SystemExit(main() or 0)
