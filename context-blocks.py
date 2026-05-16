#!/usr/bin/env python3
"""
context-blocks.py - Manage safe, marker-bounded context blocks in agent config files.

Usage:
    python context-blocks.py upsert --agent copilot "content"
    python context-blocks.py upsert --agent copilot --block-id "AWS, Docker" "content"
    python context-blocks.py remove --agent copilot
    python context-blocks.py remove --agent copilot --block-id "AWS, Docker"
"""

import argparse
import json
import os
import sys
from pathlib import Path

if os.name == "nt":
    for _stream in (sys.stdout, sys.stderr):
        if hasattr(_stream, "reconfigure"):
            _stream.reconfigure(encoding="utf-8", errors="replace")

from agent_adapters import DEFAULT_BLOCK_ID, get_adapter, validate_block_id  # noqa: E402
from host_manifest import COPILOT_DIR  # noqa: E402

REGISTRY_PATH = COPILOT_DIR / "session-state" / "tools-managed-projects.json"


def _atomic_write_text(path: Path, content: str, encoding: str = "utf-8") -> None:
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


def _load_project_registry() -> list[str]:
    try:
        if REGISTRY_PATH.exists():
            data = json.loads(REGISTRY_PATH.read_text(encoding="utf-8"))
            paths: list[str] = []
            for entry in data.get("projects", []):
                if isinstance(entry, str):
                    paths.append(entry)
                elif isinstance(entry, dict):
                    value = entry.get("path", "")
                    if isinstance(value, str) and value:
                        paths.append(value)
            return paths
    except Exception:
        pass
    return []


def _register_project(project_root: Path) -> None:
    try:
        key = str(project_root.resolve())
        raw: list = []
        if REGISTRY_PATH.exists():
            try:
                raw = json.loads(REGISTRY_PATH.read_text(encoding="utf-8")).get("projects", [])
            except Exception:
                raw = []
        existing = set()
        for entry in raw:
            if isinstance(entry, str):
                existing.add(entry)
            elif isinstance(entry, dict):
                value = entry.get("path", "")
                if value:
                    existing.add(value)
        if key not in existing:
            raw.append(key)
            REGISTRY_PATH.parent.mkdir(parents=True, exist_ok=True)
            _atomic_write_text(REGISTRY_PATH, json.dumps({"projects": raw}, indent=2))
    except Exception:
        pass


def _find_git_root(start: Path | None = None) -> Path:
    current = (start or Path.cwd()).resolve()
    for candidate in [current, *current.parents]:
        if (candidate / ".git").exists():
            return candidate
    return current


def _normalize_agent(agent: str) -> str:
    return get_adapter(agent).name


def _validate_block_id(block_id: str) -> str:
    return validate_block_id(block_id)


def _resolve_target(repo_root: Path, agent: str) -> Path:
    return get_adapter(agent).target_path(repo_root)


def _detect_newline(text: str) -> str:
    if "\r\n" in text:
        return "\r\n"
    return "\n"


def _marker_pair(block_id: str) -> tuple[str, str]:
    normalized = _validate_block_id(block_id)
    return f"<!-- {normalized} SK START -->", f"<!-- {normalized} SK END -->"


def _line_without_newline(line: str) -> str:
    return line.rstrip("\r\n")


def _find_block_span(lines: list[str], start_marker: str, end_marker: str) -> tuple[int, int] | None:
    start_indexes = [idx for idx, line in enumerate(lines) if _line_without_newline(line) == start_marker]
    end_indexes = [idx for idx, line in enumerate(lines) if _line_without_newline(line) == end_marker]
    if not start_indexes and not end_indexes:
        return None
    if len(start_indexes) != 1 or len(end_indexes) != 1:
        raise ValueError("Managed block markers are duplicated or incomplete")
    start_idx = start_indexes[0]
    end_idx = end_indexes[0]
    if end_idx < start_idx:
        raise ValueError("Managed block end marker appears before start marker")
    return start_idx, end_idx


def _build_block_lines(content: str, start_marker: str, end_marker: str, newline: str) -> list[str]:
    normalized = content.replace("\r\n", "\n").replace("\r", "\n")
    payload = normalized.split("\n")
    if payload and payload[-1] == "":
        payload = payload[:-1]
    block = [start_marker + newline]
    block.extend(line + newline for line in payload)
    block.append(end_marker + newline)
    return block


def _upsert_block_text(text: str, content: str, start_marker: str, end_marker: str) -> str:
    newline = _detect_newline(text)
    lines = text.splitlines(keepends=True)
    block_lines = _build_block_lines(content, start_marker, end_marker, newline)
    span = _find_block_span(lines, start_marker, end_marker)
    if span is not None:
        start_idx, end_idx = span
        lines[start_idx : end_idx + 1] = block_lines
        return "".join(lines)

    if lines and not lines[-1].endswith(("\n", "\r")):
        lines[-1] = lines[-1] + newline
    if lines and _line_without_newline(lines[-1]).strip():
        lines.append(newline)
    lines.extend(block_lines)
    return "".join(lines) if lines else "".join(block_lines)


def _remove_block_text(text: str, start_marker: str, end_marker: str) -> tuple[str, bool]:
    lines = text.splitlines(keepends=True)
    span = _find_block_span(lines, start_marker, end_marker)
    if span is None:
        return text, False
    start_idx, end_idx = span
    before = lines[:start_idx]
    after = lines[end_idx + 1 :]

    if (
        before
        and after
        and not _line_without_newline(before[-1]).strip()
        and not _line_without_newline(after[0]).strip()
    ):
        after = after[1:]
    if not before:
        while after and not _line_without_newline(after[0]).strip():
            after = after[1:]
    if not after:
        while before and not _line_without_newline(before[-1]).strip():
            before = before[:-1]

    return "".join(before + after), True


def _upsert_file(target: Path, block_id: str, content: str) -> str:
    start_marker, end_marker = _marker_pair(block_id)
    existing = target.read_text(encoding="utf-8") if target.exists() else ""
    target.parent.mkdir(parents=True, exist_ok=True)
    _atomic_write_text(target, _upsert_block_text(existing, content, start_marker, end_marker))
    return "updated" if existing else "created"


def _remove_file_block(target: Path, block_id: str) -> tuple[str, bool]:
    if not target.exists():
        return "missing", False
    start_marker, end_marker = _marker_pair(block_id)
    existing = target.read_text(encoding="utf-8")
    updated, changed = _remove_block_text(existing, start_marker, end_marker)
    if changed:
        _atomic_write_text(target, updated)
        return "removed", True
    return "absent", False


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="context-blocks",
        description="Manage safe, marker-bounded content blocks inside project agent config files.",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    upsert = sub.add_parser("upsert", help="Create or update a managed block")
    upsert.add_argument("--agent", required=True, help="Target adapter config file")
    upsert.add_argument("--block-id", default=DEFAULT_BLOCK_ID, help="Managed block label shown in markers")
    upsert.add_argument("--repo", type=Path, default=None, help="Project root (defaults to git root or cwd)")
    upsert.add_argument("content", help="Managed block content")

    remove = sub.add_parser("remove", help="Remove a managed block only")
    remove.add_argument("--agent", required=True, help="Target adapter config file")
    remove.add_argument("--block-id", default=DEFAULT_BLOCK_ID, help="Managed block label shown in markers")
    remove.add_argument("--repo", type=Path, default=None, help="Project root (defaults to git root or cwd)")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)
    try:
        repo_root = _find_git_root(args.repo) if args.repo is not None else _find_git_root()
        adapter = get_adapter(args.agent)
        target = adapter.target_path(repo_root)
        block_id = _validate_block_id(args.block_id)
    except ValueError as exc:
        print(f"context-blocks: {exc}", file=sys.stderr)
        return 2

    if args.command == "upsert":
        action = _upsert_file(target, block_id, args.content)
        _register_project(repo_root)
        print(f"{action}: {target.relative_to(repo_root)} [{block_id}]")
        return 0

    if args.command == "remove":
        action, changed = _remove_file_block(target, block_id)
        if action == "missing":
            print(f"missing: {target.relative_to(repo_root)}")
        elif changed:
            print(f"removed: {target.relative_to(repo_root)} [{block_id}]")
        else:
            print(f"absent: {target.relative_to(repo_root)} [{block_id}]")
        return 0

    print(f"context-blocks: unsupported command '{args.command}'", file=sys.stderr)
    return 2


if __name__ == "__main__":
    sys.exit(main())
