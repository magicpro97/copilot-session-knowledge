"""User-prompt audit rule (WBS-025).

Logs sanitized prompt metadata when ``userPromptSubmitted`` fires.
Opt-out via environment variable ``SK_PROMPT_AUDIT=0``.

Security properties:
- Prompt content is truncated to 300 chars before storage.
- Injection patterns are stripped (replaced with ``[REDACTED]``) so that
  stored metadata cannot itself become an injection vector.
- Storage is best-effort / fail-open: any exception is silently swallowed
  so this hook never blocks agent interaction.
- Audit log is a JSONL file (not the knowledge DB) to prevent prompt data
  from leaking into briefing output.
"""

import json
import os
import re
import time
from pathlib import Path

from . import Rule
from .common import MARKERS_DIR, get_session_id, sanitize_session_id

# Opt-out env var (WBS-025 acceptance criterion)
_AUDIT_ENV = "SK_PROMPT_AUDIT"
_AUDIT_LOG = MARKERS_DIR / "prompt-audit.jsonl"

# Patterns that are redacted from stored prompt snippets
_REDACT_PATTERNS = [
    re.compile(r"(?i)\b(api[_-]?key|secret[_-]?key|password|token)\s*[:=]\s*\S+"),
    re.compile(r"(?i)ssh-rsa\s+AAAA[^\s]{0,200}"),
    re.compile(r"(?i)-----BEGIN\s+(RSA\s+)?PRIVATE\s+KEY-----[\s\S]*?-----END[^-]+-----"),
    re.compile(r"\beyJ[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}\b"),
    re.compile(r"(?i)\bAuthorization\s*:\s*Bearer\s+\S{16,}"),
    re.compile(r"\bAKIA[0-9A-Z]{16}\b"),
    re.compile(r"(?i)\bignore\s+(all\s+)?previous\s+instructions?\b"),
    re.compile(r"(?i)\byou\s+are\s+now\b"),
]

_MAX_PROMPT_CHARS = 300


def _redact(text: str) -> str:
    """Replace credential/injection patterns with [REDACTED]."""
    for pat in _REDACT_PATTERNS:
        text = pat.sub("[REDACTED]", text)
    return text


class UserPromptAuditRule(Rule):
    """Audit userPromptSubmitted events (WBS-025).

    Stores sanitized prompt metadata in prompt-audit.jsonl.
    Opt-out: set SK_PROMPT_AUDIT=0 to disable entirely.
    """

    name = "user-prompt-audit"
    events = ["userPromptSubmitted"]
    tools = []

    def evaluate(self, event, data):
        if event != "userPromptSubmitted":
            return None

        # Opt-out check
        if os.environ.get(_AUDIT_ENV, "1") == "0":
            return None

        try:
            prompt = data.get("prompt", "") or ""
            if not isinstance(prompt, str):
                prompt = str(prompt)

            # Truncate then redact
            snippet = _redact(prompt[:_MAX_PROMPT_CHARS])

            session_id = get_session_id(data)
            entry = json.dumps(
                {
                    "ts": time.strftime("%Y-%m-%dT%H:%M:%S"),
                    "session_id": session_id,
                    "prompt_snippet": snippet,
                    "prompt_chars": len(prompt),
                },
                ensure_ascii=False,
            )

            MARKERS_DIR.mkdir(parents=True, exist_ok=True)
            # Rotate if > 500 KB
            if _AUDIT_LOG.is_file() and _AUDIT_LOG.stat().st_size > 500_000:
                rotated = _AUDIT_LOG.with_suffix(".jsonl.old")
                try:
                    _AUDIT_LOG.rename(rotated)
                except Exception:
                    pass

            with open(_AUDIT_LOG, "a", encoding="utf-8") as f:
                f.write(entry + "\n")
        except Exception:
            pass  # fail-open: audit is non-critical

        return None
