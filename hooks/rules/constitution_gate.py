"""ConstitutionGateRule — enforce constitution rule tags from .copilot/constitution.md."""

import os
import re
from pathlib import Path

from . import Rule
from .common import deny

_RULE_TAG_RE = re.compile(r"\[rule:(?P<rule>[a-z0-9-]+)\]", re.IGNORECASE)
_CONSTITUTION_RELATIVE_PATH = Path(".copilot") / "constitution.md"
_NO_DESTRUCTIVE_GIT = (
    re.compile(r"\bgit\s+reset\s+--hard\b"),
    re.compile(r"\bgit\s+checkout\s+--\b"),
    re.compile(r"\bgit\s+clean\b[^\n]*(?:-fd|-df|-xdf|-xfd)\b"),
)
_NO_FORCE_PUSH = (
    re.compile(r"\bgit\s+push\b[^\n]*\s--force(?:\s|$)"),
    re.compile(r"\bgit\s+push\b[^\n]*\s-f(?:\s|$)"),
)


def _find_git_root(start: Path | None = None) -> Path:
    current = (start or Path.cwd()).resolve()
    for candidate in [current, *current.parents]:
        if (candidate / ".git").exists():
            return candidate
    return current


def _load_declared_rules(repo_root: Path | None = None) -> set[str]:
    root = _find_git_root(repo_root or Path.cwd())
    constitution_path = root / _CONSTITUTION_RELATIVE_PATH
    if not constitution_path.is_file():
        return set()
    try:
        text = constitution_path.read_text(encoding="utf-8")
    except Exception:
        return set()
    return {match.group("rule").lower() for match in _RULE_TAG_RE.finditer(text)}


def _matches_any(command: str, patterns: tuple[re.Pattern, ...]) -> bool:
    return any(pattern.search(command) for pattern in patterns)


class ConstitutionGateRule(Rule):
    """Block commands that violate declared constitution rule tags."""

    name = "constitution-gate"
    events = ["preToolUse"]
    tools = ["bash"]

    def evaluate(self, event, data):
        del event
        command = str((data.get("toolInput") or {}).get("command") or "")
        if not command:
            return None

        cwd = (data.get("toolInput") or {}).get("cwd") or data.get("cwd") or os.getcwd()
        declared_rules = _load_declared_rules(Path(cwd))
        if not declared_rules:
            return None

        if "no-destructive-git" in declared_rules and _matches_any(command, _NO_DESTRUCTIVE_GIT):
            return deny(
                "Constitution violation: no-destructive-git forbids destructive git commands declared in .copilot/constitution.md"
            )

        if "no-force-push" in declared_rules and _matches_any(command, _NO_FORCE_PUSH):
            return deny(
                "Constitution violation: no-force-push forbids force pushes declared in .copilot/constitution.md"
            )

        return None
