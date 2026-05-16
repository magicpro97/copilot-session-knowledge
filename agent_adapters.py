#!/usr/bin/env python3
"""
agent_adapters.py — Adapter registry for project-level AI instruction files.

This module intentionally covers project config/instruction surfaces only.
Runtime/session host support remains grounded elsewhere (see host_manifest.py).
"""

from __future__ import annotations

import os
import sys
from dataclasses import dataclass
from pathlib import Path

if os.name == "nt":
    for _stream in (sys.stdout, sys.stderr):
        if hasattr(_stream, "reconfigure"):
            _stream.reconfigure(encoding="utf-8", errors="replace")

DEFAULT_BLOCK_ID = "SESSION-KNOWLEDGE"

AGENTS_BLOCK_CONTENT = """## Session Knowledge

- Run `sk briefing --auto --compact` before starting work.
- Use `sk query "<topic>"` to recall prior project decisions and patterns.
- Record durable lessons with `sk learn --pattern ...` or `sk learn --mistake ...` after meaningful changes.
"""

COPILOT_BLOCK_CONTENT = """## Session Knowledge

- Keep `.github/instructions/session-knowledge.instructions.md` installed for the full workflow policy.
- Keep `.github/skills/session-knowledge/SKILL.md` available so Copilot can discover briefing, query, and learn commands.
"""

IMPORT_AGENTS_CONTENT = "@AGENTS.md"


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


def validate_block_id(block_id: str) -> str:
    value = (block_id or "").strip()
    if not value:
        raise ValueError("--block-id must not be empty")
    if "<!--" in value or "-->" in value or "\n" in value or "\r" in value:
        raise ValueError("--block-id cannot contain comment delimiters or newlines")
    return value


def _detect_newline(text: str) -> str:
    return "\r\n" if "\r\n" in text else "\n"


def _line_without_newline(line: str) -> str:
    return line.rstrip("\r\n")


def _marker_pair(block_id: str) -> tuple[str, str]:
    normalized = validate_block_id(block_id)
    return f"<!-- {normalized} SK START -->", f"<!-- {normalized} SK END -->"


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


@dataclass(frozen=True)
class AgentAdapter:
    key: str
    name: str
    context_file: str
    aliases: tuple[str, ...]
    detect_paths: tuple[str, ...]
    default_content: str
    bootstrap_agents: tuple[str, ...] = ()
    public: bool = True

    def target_path(self, repo_root: Path) -> Path:
        return repo_root / self.context_file

    def detect(self, repo_root: Path) -> bool:
        return any((repo_root / rel).exists() for rel in self.detect_paths)

    def upsert_context(
        self,
        repo_root: Path,
        content: str | None = None,
        *,
        block_id: str = DEFAULT_BLOCK_ID,
        dry_run: bool = False,
    ) -> str:
        target = self.target_path(repo_root)
        payload = self.default_content if content is None else content
        start_marker, end_marker = _marker_pair(block_id)
        existed = target.exists()
        existing = target.read_text(encoding="utf-8") if existed else ""
        updated = _upsert_block_text(existing, payload, start_marker, end_marker)
        if updated == existing:
            return "unchanged"
        if not dry_run:
            target.parent.mkdir(parents=True, exist_ok=True)
            _atomic_write_text(target, updated)
        return "updated" if existed else "created"

    def remove_context(
        self,
        repo_root: Path,
        *,
        block_id: str = DEFAULT_BLOCK_ID,
        dry_run: bool = False,
    ) -> tuple[str, bool]:
        target = self.target_path(repo_root)
        if not target.exists():
            return "missing", False
        start_marker, end_marker = _marker_pair(block_id)
        existing = target.read_text(encoding="utf-8")
        updated, changed = _remove_block_text(existing, start_marker, end_marker)
        if changed and not dry_run:
            _atomic_write_text(target, updated)
            return "removed", True
        if changed:
            return "removed", True
        return "absent", False


_REGISTRY: tuple[AgentAdapter, ...] = (
    AgentAdapter(
        key="copilot",
        name="Copilot CLI",
        context_file=".github/copilot-instructions.md",
        aliases=("copilot", "copilot-cli", "github-copilot"),
        detect_paths=(".github/copilot-instructions.md",),
        default_content=COPILOT_BLOCK_CONTENT,
    ),
    AgentAdapter(
        key="claude",
        name="Claude Code",
        context_file="CLAUDE.md",
        aliases=("claude", "claude-code"),
        detect_paths=("CLAUDE.md", ".claude"),
        default_content=IMPORT_AGENTS_CONTENT,
        bootstrap_agents=("agents",),
    ),
    AgentAdapter(
        key="codex",
        name="Codex",
        context_file="AGENTS.md",
        aliases=("codex",),
        detect_paths=(".codex",),
        default_content=AGENTS_BLOCK_CONTENT,
    ),
    AgentAdapter(
        key="cursor",
        name="Cursor",
        context_file="AGENTS.md",
        aliases=("cursor",),
        detect_paths=(".cursor",),
        default_content=AGENTS_BLOCK_CONTENT,
    ),
    AgentAdapter(
        key="windsurf",
        name="Windsurf",
        context_file="AGENTS.md",
        aliases=("windsurf",),
        detect_paths=(".windsurf",),
        default_content=AGENTS_BLOCK_CONTENT,
    ),
    AgentAdapter(
        key="gemini",
        name="Gemini CLI",
        context_file="GEMINI.md",
        aliases=("gemini", "gemini-cli"),
        detect_paths=("GEMINI.md", ".gemini"),
        default_content=IMPORT_AGENTS_CONTENT,
        bootstrap_agents=("agents",),
    ),
    AgentAdapter(
        key="agents",
        name="All agents",
        context_file="AGENTS.md",
        aliases=("agents", "all", "all-agents"),
        detect_paths=("AGENTS.md",),
        default_content=AGENTS_BLOCK_CONTENT,
        public=False,
    ),
)

AGENT_ADAPTERS: dict[str, AgentAdapter] = {adapter.key: adapter for adapter in _REGISTRY}
_ALIAS_TO_KEY: dict[str, str] = {
    alias: adapter.key for adapter in _REGISTRY for alias in adapter.aliases
}
PUBLIC_AGENT_KEYS: tuple[str, ...] = tuple(adapter.key for adapter in _REGISTRY if adapter.public)
CLI_AGENT_KEYS: tuple[str, ...] = tuple(adapter.key for adapter in _REGISTRY)


def resolve_agent_key(agent: str) -> str:
    key = (agent or "").strip().lower()
    if key not in _ALIAS_TO_KEY:
        allowed = ", ".join(CLI_AGENT_KEYS)
        raise ValueError(f"Unknown --agent '{agent}'. Choose from: {allowed}")
    return _ALIAS_TO_KEY[key]


def get_adapter(agent: str) -> AgentAdapter:
    return AGENT_ADAPTERS[resolve_agent_key(agent)]


def detect_agents(repo_root: Path) -> list[str]:
    detected: list[str] = []
    for key in PUBLIC_AGENT_KEYS:
        if AGENT_ADAPTERS[key].detect(repo_root):
            detected.append(key)
    if not detected and AGENT_ADAPTERS["agents"].detect(repo_root):
        detected.append("agents")
    return detected


def expand_init_agents(agent_keys: list[str]) -> list[str]:
    expanded: list[str] = []

    def add(key: str) -> None:
        normalized = resolve_agent_key(key)
        if normalized not in expanded:
            expanded.append(normalized)

    for raw in agent_keys:
        adapter = get_adapter(raw)
        for dep in adapter.bootstrap_agents:
            add(dep)
        add(adapter.key)
    return expanded


def unique_target_adapters(adapters: list[AgentAdapter]) -> list[AgentAdapter]:
    seen: set[str] = set()
    unique: list[AgentAdapter] = []
    for adapter in adapters:
        if adapter.context_file in seen:
            continue
        seen.add(adapter.context_file)
        unique.append(adapter)
    return unique
