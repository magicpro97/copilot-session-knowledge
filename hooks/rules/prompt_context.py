"""Dynamic context injection via CONTEXT.md (Issue #666).

On every ``userPromptSubmitted`` event, reads project-specific or global
CONTEXT.md and injects its content as additionalContext for the LLM.

Lookup order:
1. ``$CWD/.copilot/CONTEXT.md`` (project-local)
2. ``~/.copilot/CONTEXT.md`` (global fallback)

Configuration (``~/.copilot/hooks-config.json``):
- ``prompt_context_enabled``: bool (default: true)
- ``prompt_context_max_tokens``: int (default: 1000; 1 token ≈ 4 chars)

Fail-open: any exception → return None (never blocks agent interaction).
"""

import json
import os
from pathlib import Path

from . import Rule
from .common import context

_HOOKS_CONFIG_PATH = Path.home() / ".copilot" / "hooks-config.json"
_GLOBAL_CONTEXT_MD = Path.home() / ".copilot" / "CONTEXT.md"
_DEFAULT_MAX_TOKENS = 1000


def _load_hooks_config() -> dict:
    """Load ~/.copilot/hooks-config.json; return empty dict on any error."""
    try:
        if _HOOKS_CONFIG_PATH.is_file():
            return json.loads(_HOOKS_CONFIG_PATH.read_text(encoding="utf-8"))
    except Exception:
        pass
    return {}


class UserPromptContextRule(Rule):
    """Inject CONTEXT.md content on userPromptSubmitted (Issue #666).

    Reads project-local or global CONTEXT.md, truncates to configured
    max_tokens, and returns as additionalContext. Fail-open on all errors.
    """

    name = "prompt-context"
    events = ["userPromptSubmitted"]
    tools = []

    def evaluate(self, event, data):
        if event != "userPromptSubmitted":
            return None

        try:
            config = _load_hooks_config()

            # Check if disabled
            if not config.get("prompt_context_enabled", True):
                return None

            max_tokens = config.get("prompt_context_max_tokens", _DEFAULT_MAX_TOKENS)
            if not isinstance(max_tokens, int) or max_tokens <= 0:
                max_tokens = _DEFAULT_MAX_TOKENS
            max_chars = max_tokens * 4

            # Resolve CWD for project-local lookup
            cwd = data.get("cwd") or os.getcwd()
            project_context = Path(cwd) / ".copilot" / "CONTEXT.md"

            # Lookup order: project-local first, then global
            context_path = None
            if project_context.is_file():
                context_path = project_context
            elif _GLOBAL_CONTEXT_MD.is_file():
                context_path = _GLOBAL_CONTEXT_MD

            if context_path is None:
                return None

            content = context_path.read_text(encoding="utf-8")
            if not content.strip():
                return None

            # Truncate to max_chars
            if len(content) > max_chars:
                content = content[:max_chars] + "\n[...truncated]"

            return context(content)

        except Exception:
            return None  # fail-open
