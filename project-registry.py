#!/usr/bin/env python3
"""
project-registry.py — Manage the sk project registry.

Commands:
    add   [path]       Register a project root (default: auto-detect)
    remove [path]      Unregister a project root (default: auto-detect)
    list               List all registered projects

Registry file: ~/.copilot/session-state/tools-managed-projects.json

Schema (backward-compatible):
  Old entries: "/abs/path"                (plain string, written by install.py / setup-project.py)
  New entries: {"name": "x", "path": ..., "created_at": "ISO8601"}   (written here)

Both formats co-exist.  All read paths normalize to the path string.
"""

import argparse
import datetime
import json
import os
import subprocess
import sys
from pathlib import Path

if os.name == "nt":
    for _stream in (sys.stdout, sys.stderr):
        if hasattr(_stream, "reconfigure"):
            _stream.reconfigure(encoding="utf-8", errors="replace")

REGISTRY_PATH = Path.home() / ".copilot" / "session-state" / "tools-managed-projects.json"


# ---------------------------------------------------------------------------
# Atomic write (self-contained per architecture — no inter-script imports)
# ---------------------------------------------------------------------------

def _atomic_write_text(path: Path, content: str, encoding: str = "utf-8") -> None:
    """Write *content* to *path* atomically via a sibling .tmp + os.replace."""
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


# ---------------------------------------------------------------------------
# Registry helpers — backward-compatible with string-only registries
# ---------------------------------------------------------------------------

def _entry_path(entry: object) -> str:
    """Return the path string from a registry entry (string or dict)."""
    if isinstance(entry, str):
        return entry
    if isinstance(entry, dict):
        return entry.get("path", "")
    return ""


def _load_raw_registry() -> list:
    """Return the raw list of registry entries (strings and/or dicts); never raises."""
    try:
        if REGISTRY_PATH.exists():
            data = json.loads(REGISTRY_PATH.read_text(encoding="utf-8"))
            return [e for e in data.get("projects", []) if isinstance(e, (str, dict))]
    except Exception:
        pass
    return []


def _load_project_paths() -> list[str]:
    """Return a list of registered path strings in insertion order (de-duplicated)."""
    seen: set[str] = set()
    result: list[str] = []
    for entry in _load_raw_registry():
        p = _entry_path(entry)
        if p and p not in seen:
            seen.add(p)
            result.append(p)
    return result


def _save_registry(entries: list) -> None:
    """Persist the entry list atomically."""
    REGISTRY_PATH.parent.mkdir(parents=True, exist_ok=True)
    _atomic_write_text(REGISTRY_PATH, json.dumps({"projects": entries}, indent=2))


# ---------------------------------------------------------------------------
# Auto-detection helpers
# ---------------------------------------------------------------------------

def _detect_project_root(start: Path | None = None) -> Path | None:
    """
    Detect the project root by:
    1. Walking up from *start* (or cwd) looking for a ``.copilot/`` directory.
    2. Falling back to ``git rev-parse --show-toplevel``.

    Returns an absolute Path, or None if detection fails.
    """
    cwd = (start or Path.cwd()).resolve()

    # Walk up looking for .copilot/
    probe = cwd
    for _ in range(32):  # safety cap
        if (probe / ".copilot").is_dir():
            return probe
        parent = probe.parent
        if parent == probe:
            break
        probe = parent

    # Git root fallback
    try:
        proc = subprocess.run(
            ["git", "rev-parse", "--show-toplevel"],
            capture_output=True,
            text=True,
            cwd=str(cwd),
            timeout=5,
        )
        if proc.returncode == 0:
            root = proc.stdout.strip()
            if root:
                return Path(root).resolve()
    except Exception:
        pass

    return None


# ---------------------------------------------------------------------------
# Subcommand handlers
# ---------------------------------------------------------------------------

def cmd_add(path_arg: str | None, quiet: bool = False) -> int:
    """Register a project root in the registry."""
    if path_arg:
        root = Path(path_arg).resolve()
    else:
        detected = _detect_project_root()
        if detected is None:
            print(
                "sk project add: could not auto-detect a project root.\n"
                "  Looked for .copilot/ in ancestor directories and tried git rev-parse.\n"
                "  Supply a path explicitly:  sk project add <path>",
                file=sys.stderr,
            )
            return 1
        root = detected

    key = str(root)
    raw = _load_raw_registry()
    existing_paths = {_entry_path(e) for e in raw}

    if key in existing_paths:
        if not quiet:
            print(f"already registered: {key}")
        return 0

    name = root.name
    created_at = datetime.datetime.now(datetime.timezone.utc).isoformat()
    raw.append({"name": name, "path": key, "created_at": created_at})

    try:
        _save_registry(raw)
    except Exception as exc:
        print(f"sk project add: failed to write registry: {exc}", file=sys.stderr)
        return 1

    if not quiet:
        print(f"registered: {key}")
    return 0


def cmd_remove(path_arg: str | None, quiet: bool = False) -> int:
    """Unregister a project root from the registry."""
    if path_arg:
        key = str(Path(path_arg).resolve())
    else:
        detected = _detect_project_root()
        if detected is None:
            print(
                "sk project remove: could not auto-detect a project root.\n"
                "  Supply a path explicitly:  sk project remove <path>",
                file=sys.stderr,
            )
            return 1
        key = str(detected)

    raw = _load_raw_registry()
    new_raw = [e for e in raw if _entry_path(e) != key]

    if len(new_raw) == len(raw):
        if not quiet:
            print(f"not registered: {key}")
        return 0

    try:
        _save_registry(new_raw)
    except Exception as exc:
        print(f"sk project remove: failed to write registry: {exc}", file=sys.stderr)
        return 1

    if not quiet:
        print(f"removed: {key}")
    return 0


def _load_deduped_registry() -> list:
    """Return raw entries de-duplicated by path (first occurrence wins)."""
    seen: set[str] = set()
    result: list = []
    for entry in _load_raw_registry():
        p = _entry_path(entry)
        if p and p not in seen:
            seen.add(p)
            result.append(entry)
    return result


def cmd_list(json_output: bool = False) -> int:
    """List registered projects (de-duplicated by path)."""
    deduped = _load_deduped_registry()

    if not deduped:
        if json_output:
            print("[]")
        else:
            print("no projects registered")
        return 0

    if json_output:
        normalized = []
        for entry in deduped:
            if isinstance(entry, str):
                normalized.append({"name": Path(entry).name, "path": entry, "created_at": None})
            elif isinstance(entry, dict):
                normalized.append(entry)
        print(json.dumps(normalized, indent=2))
    else:
        for entry in deduped:
            if isinstance(entry, str):
                print(entry)
            elif isinstance(entry, dict):
                path = entry.get("path", "")
                name = entry.get("name", Path(path).name if path else "")
                added = entry.get("created_at", "")
                if added:
                    print(f"{path}  ({name}, added {added})")
                else:
                    print(f"{path}  ({name})")
    return 0


# ---------------------------------------------------------------------------
# Argument parsing and entry point
# ---------------------------------------------------------------------------

def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="sk project",
        description="Manage the sk project registry.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "Registry: ~/.copilot/session-state/tools-managed-projects.json\n"
            "Auto-detect: walks up from cwd for .copilot/, then falls back to git root."
        ),
    )
    sub = parser.add_subparsers(dest="subcommand", metavar="<subcommand>")

    add_p = sub.add_parser("add", help="Register a project root")
    add_p.add_argument("path", nargs="?", default=None, help="Absolute or relative path (default: auto-detect)")
    add_p.add_argument("--quiet", "-q", action="store_true", help="Suppress output")

    rm_p = sub.add_parser("remove", help="Unregister a project root")
    rm_p.add_argument("path", nargs="?", default=None, help="Absolute or relative path (default: auto-detect)")
    rm_p.add_argument("--quiet", "-q", action="store_true", help="Suppress output")

    ls_p = sub.add_parser("list", help="List all registered projects")
    ls_p.add_argument("--json", action="store_true", dest="json_output", help="Output as JSON array")

    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)

    if args.subcommand is None:
        parser.print_help()
        return 0

    if args.subcommand == "add":
        return cmd_add(args.path, quiet=args.quiet)
    if args.subcommand == "remove":
        return cmd_remove(args.path, quiet=args.quiet)
    if args.subcommand == "list":
        return cmd_list(json_output=args.json_output)

    parser.print_help()
    return 1


if __name__ == "__main__":
    sys.exit(main())
