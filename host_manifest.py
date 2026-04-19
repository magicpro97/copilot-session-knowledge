#!/usr/bin/env python3
"""
host_manifest.py — Single source of truth for supported-host metadata.

Supported hosts: Copilot CLI and Claude Code ONLY.

Do NOT add Codex, Cursor, Windsurf, Cline, or any other host without
documented hook/session formats and an explicit review of this file.

This module is imported by install.py, watch-sessions.py, and
setup-project.py to eliminate duplicated host constants.  auto-update-tools.py
covers it automatically via its *.py classification rule.
"""

import os
import sys
from pathlib import Path

# ---------------------------------------------------------------------------
# Windows console encoding fix (standard pattern for this repo)
# ---------------------------------------------------------------------------
if os.name == "nt":
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

# ---------------------------------------------------------------------------
# Host root directories
# ---------------------------------------------------------------------------

_HOME = Path.home()

COPILOT_DIR = _HOME / ".copilot"
CLAUDE_DIR  = _HOME / ".claude"

# Session data roots (each host's location for session/project files)
SESSION_STATE   = COPILOT_DIR / "session-state"
CLAUDE_PROJECTS = CLAUDE_DIR  / "projects"

# ---------------------------------------------------------------------------
# Supported-host manifests
# ---------------------------------------------------------------------------

# Canonical ordered list of supported host names.
# Copilot CLI and Claude Code are the ONLY grounded hosts.
SUPPORTED_HOSTS: tuple[str, ...] = ("Copilot CLI", "Claude Code")

# Hosts explicitly excluded from this tool's scope.
UNSUPPORTED_HOSTS: tuple[str, ...] = ("Codex", "Cursor", "Windsurf", "Cline", "Copilot Chat")

# Top-level host config directories — used by install.py for existence checks.
HOST_DIRS: dict[str, Path] = {
    "Copilot CLI": COPILOT_DIR,
    "Claude Code":  CLAUDE_DIR,
}

# Session file roots — used by watch-sessions.py to discover session files.
# Copilot CLI stores sessions under ~/.copilot/session-state/
# Claude Code stores projects under ~/.claude/projects/
HOST_SESSION_ROOTS: tuple[tuple[str, Path], ...] = (
    ("Copilot CLI", SESSION_STATE),
    ("Claude Code", CLAUDE_PROJECTS),
)

# Project-level instruction file paths (project-relative).
# Used by setup-project.py when patching host-specific AI instruction files.
# "All agents" is host-agnostic and intentionally included for completeness.
HOST_INSTRUCTION_FILES: dict[str, str] = {
    "Copilot CLI": ".github/copilot-instructions.md",
    "Claude Code": "CLAUDE.md",
    "All agents":  "AGENTS.md",   # host-agnostic; read by Claude, Codex, and others
}
