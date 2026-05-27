"""browse/core/preflight.py — Prompt preflight estimation (issue #557).

Provides a structured pre-submission estimate for a prompt:
    * estimated input token count (rough 4-chars-per-token heuristic)
    * model context-window fit (fits / warn / overflow)
    * attachment byte summary (counts only — never reads attachment payloads)
    * redaction-hit summary (counts + safe categories; never echoes raw secrets)
    * structured warnings and hard_errors for the UI to render chips

SECURITY:
    * Prompt body is NEVER persisted or logged.
    * Redaction summary contains category labels and short safe excerpts only.
      Even the excerpt is the [REDACTED] sentinel — the raw matched substring
      is never returned.
"""

from __future__ import annotations

import os
import sys

if os.name == "nt":
    for _s in (sys.stdout, sys.stderr):
        if hasattr(_s, "reconfigure"):
            _s.reconfigure(encoding="utf-8", errors="replace")


# Rough token estimate: ~4 chars per token for English text (GPT-family heuristic).
_CHARS_PER_TOKEN = 4

# Operator hard cap on prompt body length (matches browse/api/operator.py _MAX_PROMPT_LEN).
_MAX_PROMPT_CHARS = 4096

# Operator hard cap on aggregate attachment bytes (matches issue #557 contract).
_MAX_ATTACHMENT_BYTES_TOTAL = 50 * 1024 * 1024  # 50 MiB

# Context window soft threshold — warn when prompt + attachments occupy at least
# this fraction of the model's window. Hard "overflow" is anything > 100%.
_CONTEXT_WARN_FRACTION = 0.8

# Model cost tiers (for UI display only — opaque relative measure).
_MODEL_COST_TIERS: dict[str, str] = {
    "gpt-4.1": "standard",
    "gpt-5-mini": "standard",
    "gpt-5.2": "standard",
    "gpt-5.2-codex": "standard",
    "gpt-5.3-codex": "standard",
    "gpt-5.4": "standard",
    "gpt-5.4-mini": "standard",
    "gpt-5.5": "premium",
    "claude-haiku-4.5": "standard",
    "claude-sonnet-4.5": "standard",
    "claude-sonnet-4.6": "standard",
    "claude-opus-4.5": "premium",
    "claude-opus-4.6": "premium",
    "claude-opus-4.7": "premium",
}

# Conservative model context-window catalog (input-token budget).
# Values are deliberately conservative; an unknown model falls back to default.
_MODEL_CONTEXT_WINDOWS: dict[str, int] = {
    "gpt-4.1": 128_000,
    "gpt-5-mini": 128_000,
    "gpt-5.2": 200_000,
    "gpt-5.2-codex": 200_000,
    "gpt-5.3-codex": 200_000,
    "gpt-5.4": 200_000,
    "gpt-5.4-mini": 200_000,
    "gpt-5.5": 400_000,
    "claude-haiku-4.5": 200_000,
    "claude-sonnet-4.5": 200_000,
    "claude-sonnet-4.6": 200_000,
    "claude-opus-4.5": 200_000,
    "claude-opus-4.6": 200_000,
    "claude-opus-4.7": 200_000,
}

# Default window when the model is unknown — UI surfaces this as
# unknown_model so the user is aware the fit estimate is best-effort.
_DEFAULT_CONTEXT_WINDOW = 128_000

# Stable category labels for redaction hits. Order matches operator_console
# secret patterns so we can map by index.
_REDACTION_CATEGORIES = (
    "github_token",
    "aws_access_key",
    "openai_key",
    "jwt",
    "generic_secret_assignment",
)


def _estimate_tokens_from_chars(char_count: int) -> int:
    """Return a non-negative integer token estimate for ``char_count`` chars."""
    if char_count <= 0:
        return 0
    return max(1, char_count // _CHARS_PER_TOKEN)


def _context_window_for(model: str) -> int:
    """Resolve the configured context window for ``model``."""
    return _MODEL_CONTEXT_WINDOWS.get((model or "").strip(), _DEFAULT_CONTEXT_WINDOW)


def _cost_tier_for(model: str) -> str:
    """Resolve the model cost tier label for ``model``."""
    return _MODEL_COST_TIERS.get((model or "").strip(), "unknown")


def _summarize_redaction_hits(prompt: str) -> dict:
    """Count secret-pattern matches in ``prompt`` without echoing raw values.

    Returns ``{"hits": int, "categories": [str], "safe_excerpts": [str]}``.
    ``safe_excerpts`` contains only the literal sentinel ``[REDACTED]`` per
    category that matched — never the underlying captured string.
    """
    # Local import to avoid a circular dependency at module load time
    # (operator_console -> preflight is never imported, but be defensive).
    from browse.core.operator_console import _SECRET_PATTERNS  # noqa: PLC0415

    hits = 0
    categories: list[str] = []
    safe_excerpts: list[str] = []
    for idx, pat in enumerate(_SECRET_PATTERNS):
        try:
            count = len(pat.findall(prompt))
        except Exception:
            count = 0
        if count > 0:
            hits += count
            label = _REDACTION_CATEGORIES[idx] if idx < len(_REDACTION_CATEGORIES) else f"category_{idx}"
            categories.append(label)
            safe_excerpts.append("[REDACTED]")
    return {
        "hits": hits,
        "categories": categories,
        "safe_excerpts": safe_excerpts,
    }


def estimate_preflight(
    prompt: str,
    model: str = "",
    attachment_count: int = 0,
    attachment_total_bytes: int = 0,
) -> dict:
    """Compute a preflight estimate for a prompt without persisting it.

    Returns a dict with the keys described in issue #557:
      - estimated_input_tokens: int
      - model: str — resolved model id (may be empty)
      - model_known: bool
      - model_cost_tier: str — "standard" | "premium" | "unknown"
      - model_context_window: int — token budget for the model
      - context_fit: str — "fits" | "warn" | "overflow"
      - context_fit_fraction: float — input_tokens / window
      - attachment_count: int
      - attachment_total_bytes: int
      - redaction: dict — {"hits", "categories", "safe_excerpts"}
      - warnings: list[dict] — [{"code", "message", "severity": "warn"}]
      - hard_errors: list[dict] — [{"code", "message"}]
      - within_limit: bool — true when hard_errors is empty
    """
    if not isinstance(prompt, str):
        prompt = "" if prompt is None else str(prompt)

    char_count = len(prompt)
    prompt_tokens = _estimate_tokens_from_chars(char_count)

    # Attachments add tokens (base64 overhead included by treating bytes ≈ chars).
    attachment_tokens = _estimate_tokens_from_chars(max(0, int(attachment_total_bytes)))
    estimated_input_tokens = prompt_tokens + attachment_tokens

    cost_tier = _cost_tier_for(model)
    window = _context_window_for(model)
    fit_fraction = estimated_input_tokens / window if window > 0 else 0.0

    if fit_fraction > 1.0:
        context_fit = "overflow"
    elif fit_fraction >= _CONTEXT_WARN_FRACTION:
        context_fit = "warn"
    else:
        context_fit = "fits"

    redaction = _summarize_redaction_hits(prompt)

    warnings: list[dict] = []
    hard_errors: list[dict] = []

    # Hard error: prompt body exceeds operator length cap.
    if char_count > _MAX_PROMPT_CHARS:
        hard_errors.append(
            {
                "code": "PROMPT_TOO_LONG",
                "message": (f"Prompt is {char_count} characters; the maximum is {_MAX_PROMPT_CHARS}."),
            }
        )

    # Hard error: empty prompt is not acceptable for a preflight submission.
    if char_count == 0:
        hard_errors.append({"code": "EMPTY_PROMPT", "message": "Prompt is empty."})

    # Hard error: attachments would not fit at all in the model window.
    if context_fit == "overflow":
        hard_errors.append(
            {
                "code": "CONTEXT_OVERFLOW",
                "message": (
                    f"Estimated {estimated_input_tokens} tokens exceeds the "
                    f"{window}-token context window for model "
                    f"{model or '<default>'}."
                ),
            }
        )

    # Hard error: aggregate attachment bytes exceed operator cap.
    if int(attachment_total_bytes) > _MAX_ATTACHMENT_BYTES_TOTAL:
        hard_errors.append(
            {
                "code": "ATTACHMENT_TOTAL_TOO_LARGE",
                "message": (
                    f"Attachment total bytes "
                    f"{int(attachment_total_bytes)} exceeds the limit of "
                    f"{_MAX_ATTACHMENT_BYTES_TOTAL}."
                ),
            }
        )

    # Warnings (non-blocking; UI requires explicit override to submit).
    if context_fit == "warn":
        warnings.append(
            {
                "code": "CONTEXT_NEAR_LIMIT",
                "message": (
                    f"Estimated {estimated_input_tokens} tokens uses "
                    f"{int(fit_fraction * 100)}% of the {window}-token window."
                ),
                "severity": "warn",
            }
        )

    model_known = bool(model) and model.strip() in _MODEL_CONTEXT_WINDOWS
    if model and not model_known:
        warnings.append(
            {
                "code": "UNKNOWN_MODEL",
                "message": (
                    f"Model {model!r} is not in the preflight catalog; fit estimate uses a conservative default window."
                ),
                "severity": "warn",
            }
        )

    if redaction["hits"] > 0:
        warnings.append(
            {
                "code": "REDACTION_HITS",
                "message": (
                    f"Detected {redaction['hits']} potential secret pattern(s) "
                    "in the prompt; values will be redacted before submission."
                ),
                "severity": "warn",
            }
        )

    return {
        "estimated_input_tokens": estimated_input_tokens,
        "model": (model or "").strip(),
        "model_known": model_known,
        "model_cost_tier": cost_tier,
        "model_context_window": window,
        "context_fit": context_fit,
        "context_fit_fraction": round(fit_fraction, 4),
        "attachment_count": int(attachment_count),
        "attachment_total_bytes": int(attachment_total_bytes),
        "redaction": redaction,
        "warnings": warnings,
        "hard_errors": hard_errors,
        "within_limit": not hard_errors,
    }
