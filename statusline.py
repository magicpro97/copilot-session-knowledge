#!/usr/bin/env python3
"""
statusline.py — Custom statusline cho GitHub Copilot CLI.

Hiển thị model, input/output tokens, ước tính cost USD, và quota còn lại
trong terminal footer của Copilot CLI.

Modes:
  1. Copilot CLI statusLine mode:  đọc JSON payload từ stdin, in 1 dòng ANSI text
  2. sk status / sk statusline:    in bảng summary session hiện tại (no stdin)

Cấu hình trong ~/.copilot/settings.json:
  {
    "statusLine": {
      "type": "command",
      "command": "~/.copilot/tools/statusline.py",
      "padding": 1
    }
  }

Chạy trực tiếp:
  sk status             # summary session hiện tại
  sk statusline         # alias
  sk statusline --quota # force refresh quota từ API

Token cost được ước tính từ per-model rates (không chính xác 100% vì thiếu
cache_write_tokens trong stdin JSON). Dấu * biểu thị estimated value.

Quota được lấy từ API nội bộ gh api /copilot_internal/user với TTL cache
60 giây. Cần gh CLI đã authenticate.
"""

# Windows UTF-8 guard (mandatory for all scripts in this repo)
import os
import sys

if os.name == "nt":
    for _s in (sys.stdout, sys.stderr):
        if hasattr(_s, "reconfigure"):
            _s.reconfigure(encoding="utf-8", errors="replace")

import json
import re
import subprocess
import time
from pathlib import Path

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

TOKENS_PER_MILLION = 1_000_000
AI_CREDIT_USD = 0.01  # 1 AI Credit = $0.01 USD (from June 2026)
PREMIUM_REQUEST_USD = 0.04  # $0.04 per overage premium request (current billing)
MARKERS_DIR = Path.home() / ".copilot" / "markers"
QUOTA_CACHE_FILE = MARKERS_DIR / "quota-cache.json"
QUOTA_CACHE_TTL = 60  # seconds between quota API refreshes

# Per-model rates in USD per 1M tokens (effective with AI Credit billing June 2026).
# Source: github/docs:data/tables/copilot/models-and-pricing.yml (SHA 00152a3d)
# fmt: off
_MODEL_RATES: dict[str, dict[str, float]] = {
    "claude-sonnet-4.6":  {"input": 3.00, "cached_input": 0.30, "cache_write": 3.75, "output": 15.00},
    "claude-sonnet-4.5":  {"input": 3.00, "cached_input": 0.30, "cache_write": 3.75, "output": 15.00},
    "claude-sonnet-4":    {"input": 3.00, "cached_input": 0.30, "cache_write": 3.75, "output": 15.00},
    "claude-opus-4.7":    {"input": 5.00, "cached_input": 0.50, "cache_write": 6.25, "output": 25.00},
    "claude-opus-4.6":    {"input": 5.00, "cached_input": 0.50, "cache_write": 6.25, "output": 25.00},
    "claude-opus-4.5":    {"input": 5.00, "cached_input": 0.50, "cache_write": 6.25, "output": 25.00},
    "claude-haiku-4.5":   {"input": 1.00, "cached_input": 0.10, "cache_write": 1.25, "output": 5.00},
    "gpt-4.1":            {"input": 2.00, "cached_input": 0.50, "output": 8.00},
    "gpt-4o":             {"input": 2.00, "cached_input": 0.50, "output": 8.00},
    "gpt-5-mini":         {"input": 0.25, "cached_input": 0.025, "output": 2.00},
    "gpt-5.2":            {"input": 1.75, "cached_input": 0.175, "output": 14.00},
    "gpt-5.2-codex":      {"input": 1.75, "cached_input": 0.175, "output": 14.00},
    "gpt-5.3-codex":      {"input": 1.75, "cached_input": 0.175, "output": 14.00},
    "gpt-5.4":            {"input": 2.50, "cached_input": 0.25, "output": 15.00},
    "gpt-5.4-mini":       {"input": 0.75, "cached_input": 0.075, "output": 4.50},
    "gpt-5.4-nano":       {"input": 0.20, "cached_input": 0.02, "output": 1.25},
    "gpt-5.5":            {"input": 5.00, "cached_input": 0.50, "output": 30.00},
    "gemini-2.5-pro":     {"input": 1.25, "cached_input": 0.125, "output": 10.00},
    "gemini-3-flash":     {"input": 0.50, "cached_input": 0.05, "output": 3.00},
    "gemini-3.1-pro":     {"input": 2.00, "cached_input": 0.20, "output": 12.00},
    "gemini-3.5-flash":   {"input": 1.50, "cached_input": 0.15, "output": 9.00},
    "raptor-mini":        {"input": 0.25, "cached_input": 0.025, "output": 2.00},
}

# Premium request multipliers (current request-based billing, until May 31 2026).
# Source: github/docs:data/tables/copilot/model-multipliers.yml (SHA b64928774b)
_MODEL_MULTIPLIERS: dict[str, float] = {
    "gpt-4.1": 0, "gpt-4o": 0, "gpt-5-mini": 0, "raptor-mini": 0,
    "claude-haiku-4.5": 0.33, "gpt-5.4-mini": 0.33, "gpt-5.4-nano": 0.25,
    "gemini-3-flash": 0.33,
    "claude-sonnet-4.5": 1.0, "claude-sonnet-4.6": 1.0, "claude-sonnet-4": 1.0,
    "gpt-5.2": 1.0, "gpt-5.2-codex": 1.0, "gpt-5.3-codex": 1.0, "gpt-5.4": 1.0,
    "gemini-2.5-pro": 1.0, "gemini-3.1-pro": 1.0,
    "claude-opus-4.5": 3.0, "claude-opus-4.6": 3.0,
    "claude-opus-4.7": 15.0, "gpt-5.5": 7.5,
    "gemini-3.5-flash": 14.0,
}
# fmt: on

# ---------------------------------------------------------------------------
# ANSI helpers
# ---------------------------------------------------------------------------

R = "\033[0;31m"
G = "\033[0;32m"
Y = "\033[0;33m"  # noqa: E702
B = "\033[0;34m"
M = "\033[0;35m"
C = "\033[0;36m"  # noqa: E702
DIM = "\033[2m"
BOLD = "\033[1m"
RST = "\033[0m"  # noqa: E702
SEP = f"{DIM}│{RST}"

# ASCII fallbacks for terminals that cannot render Unicode
try:
    "│⟳📊".encode(sys.stdout.encoding or "utf-8")
except (UnicodeEncodeError, LookupError):
    SEP = f"{DIM}|{RST}"
    _SYNC_ICON = "~"
    _CHART_ICON = "#"
else:
    _SYNC_ICON = "⟳"
    _CHART_ICON = "📊"


def _no_color() -> bool:
    """Return True when color output should be suppressed."""
    return (
        os.environ.get("NO_COLOR") is not None
        or os.environ.get("TERM") == "dumb"
        or not sys.stdout.isatty()
        and sys.stdin.isatty()
    )


def _ansi(code: str) -> str:
    return "" if _no_color() else code


# ---------------------------------------------------------------------------
# Formatting helpers
# ---------------------------------------------------------------------------


def _fmt_tokens(n: int) -> str:
    """1_234_567 → '1.2M', 12_345 → '12.3k', 123 → '123'."""
    if n >= 1_000_000:
        return f"{n / 1_000_000:.1f}M"
    if n >= 1_000:
        return f"{n / 1_000:.1f}k"
    return str(n)


def _fmt_cost(usd: float) -> str:
    """$0.0012 → '$0.0012', $1.23 → '$1.23'."""
    if usd < 0.001:
        return f"${usd:.5f}"
    if usd < 0.01:
        return f"${usd:.4f}"
    return f"${usd:.2f}"


def _fmt_bar(pct_used: float, width: int = 8) -> str:
    """██████░░ — green/yellow/red based on usage %."""
    filled = max(0, min(width, round(pct_used / 100 * width)))
    empty = width - filled
    if pct_used > 80:
        color = _ansi(R)
    elif pct_used > 50:
        color = _ansi(Y)
    else:
        color = _ansi(G)
    return f"{color}{'█' * filled}{'░' * empty}{_ansi(RST)}"


def _model_color(model_id: str) -> str:
    mid = model_id.lower()
    if "opus" in mid:
        return _ansi(M)
    if "sonnet" in mid:
        return _ansi(C)
    if "haiku" in mid:
        return _ansi(G)
    if "gpt-5.5" in mid:
        return _ansi(R)
    if "gpt" in mid:
        return _ansi(B)
    if "gemini" in mid:
        return _ansi(Y)
    return _ansi(W := "\033[0;37m")  # noqa: F841


# ---------------------------------------------------------------------------
# Model lookup
# ---------------------------------------------------------------------------


def _normalize_model(raw: str) -> str:
    """Lower-case and strip whitespace."""
    return raw.lower().strip()


def _get_rate(model_id: str) -> dict[str, float]:
    """Return per-token rates for *model_id*; fall back to claude-sonnet-4.6."""
    mid = _normalize_model(model_id)
    if mid in _MODEL_RATES:
        return _MODEL_RATES[mid]
    # Fuzzy: find a key that is a substring of mid or vice-versa
    for key, rate in _MODEL_RATES.items():
        if key in mid or mid in key:
            return rate
    return _MODEL_RATES["claude-sonnet-4.6"]


# ---------------------------------------------------------------------------
# Cost calculation
# ---------------------------------------------------------------------------


def _estimate_cost_usd(
    model_id: str,
    total_input: int,
    cached_input: int,
    last_output: int,
) -> float:
    """
    Estimate session cost in USD from token counts.

    Note: cache_write_tokens is absent from the statusline stdin JSON, so the
    estimate will be slightly lower than the true cost for Anthropic models.
    The asterisk (*) in the output signals this is an approximation.
    """
    rate = _get_rate(model_id)
    uncached = max(total_input - cached_input, 0)
    input_usd = (uncached / TOKENS_PER_MILLION) * rate["input"]
    cached_usd = (cached_input / TOKENS_PER_MILLION) * rate["cached_input"]
    output_usd = (last_output / TOKENS_PER_MILLION) * rate["output"]
    return round(input_usd + cached_usd + output_usd, 6)


# ---------------------------------------------------------------------------
# Quota API (cached)
# ---------------------------------------------------------------------------


def _fetch_quota(force: bool = False) -> dict | None:
    """
    Return quota snapshot from ~/.copilot/markers/quota-cache.json.
    Refreshes from `gh api /copilot_internal/user` when stale.
    Returns None silently on any error (fail-open).
    """
    try:
        MARKERS_DIR.mkdir(parents=True, exist_ok=True)
        if not force and QUOTA_CACHE_FILE.exists():
            cached = json.loads(QUOTA_CACHE_FILE.read_text(encoding="utf-8"))
            if time.time() - cached.get("_ts", 0) < QUOTA_CACHE_TTL:
                return cached
        # Refresh from API
        result = subprocess.run(
            [
                "gh",
                "api",
                "/copilot_internal/user",
                "-H",
                "X-GitHub-Api-Version: 2025-04-01",
                "-H",
                "Editor-Version: vscode/1.96.2",
                "-H",
                "User-Agent: GitHubCopilotChat/0.26.7",
            ],
            capture_output=True,
            text=True,
            timeout=5,
        )
        if result.returncode == 0 and result.stdout.strip():
            data = json.loads(result.stdout)
            data["_ts"] = time.time()
            tmp = QUOTA_CACHE_FILE.with_suffix(".tmp")
            tmp.write_text(json.dumps(data), encoding="utf-8")
            os.replace(str(tmp), str(QUOTA_CACHE_FILE))
            return data
    except Exception:
        pass
    return None


def _get_premium_snapshot(quota_data: dict | None) -> dict | None:
    """Extract premium_interactions snapshot dict from quota response."""
    if not quota_data:
        return None
    snaps = quota_data.get("quota_snapshots") or {}
    return snaps.get("premium_interactions") or snaps.get("premium_models")


# ---------------------------------------------------------------------------
# Session state helpers (mirrors common.py without importing it)
# ---------------------------------------------------------------------------


def _get_session_id() -> str:
    for env in ("COPILOT_AGENT_SESSION_ID", "COPILOT_SESSION_ID"):
        v = os.environ.get(env)
        if v:
            return v
    state_path = os.environ.get("COPILOT_SESSION_STATE", "")
    if state_path:
        return os.path.basename(state_path)
    return f"ppid-{os.getppid()}"


def _sanitize_sid(sid: str) -> str:
    """Remove path-traversal characters from session ID."""
    return re.sub(r"[^\w.\-]", "_", sid)[:64]


def _load_session_state(sid: str) -> dict:
    state_file = MARKERS_DIR / f"session-state-{_sanitize_sid(sid)}"
    try:
        return json.loads(state_file.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return {}
    except Exception:
        return {}


# ---------------------------------------------------------------------------
# Mode 1: Statusline (reads stdin JSON from Copilot CLI)
# ---------------------------------------------------------------------------


def _render_statusline(payload: dict) -> str:
    """Build the one-line statusline string from the Copilot CLI JSON payload."""
    model_obj = payload.get("model") or {}
    model_id = model_obj.get("id", "unknown")
    model_name = model_obj.get("display_name", model_id)
    ctx = payload.get("context_window") or {}
    cost_obj = payload.get("cost") or {}

    total_in = int(ctx.get("total_input_tokens", 0))
    cached_in = int(ctx.get("total_cache_read_tokens", 0))
    last_in = int(ctx.get("last_call_input_tokens", 0))
    last_out = int(ctx.get("last_call_output_tokens", 0))
    ctx_pct = float(ctx.get("used_percentage", 0))
    ctx_size = int(ctx.get("context_window_size", 0))
    total_pru = int(cost_obj.get("total_premium_requests", 0))

    # ── Model segment ────────────────────────────────────────────────────────
    mc = _model_color(model_id)
    # Strip effort annotation "(low/medium/high)" from display name
    short_name = re.sub(r"\s*\([^)]*\)\s*$", "", model_name).strip() or model_id
    seg_model = f"{mc}{_ansi(BOLD)}{short_name}{_ansi(RST)}"

    # ── Token segment: last call ──────────────────────────────────────────────
    seg_in = f"{_ansi(DIM)}↑{_ansi(RST)}{_ansi(C)}{_fmt_tokens(last_in)}{_ansi(RST)}"
    seg_out = f"{_ansi(DIM)}↓{_ansi(RST)}{_ansi(G)}{_fmt_tokens(last_out)}{_ansi(RST)}"
    seg_tokens = f"{seg_in} {seg_out}"
    if cached_in > 0:
        seg_tokens += f" {_ansi(DIM)}{_SYNC_ICON}{_ansi(RST)}{_ansi(Y)}{_fmt_tokens(cached_in)}{_ansi(RST)}"

    # ── Context window ────────────────────────────────────────────────────────
    ctx_col = _ansi(R) if ctx_pct > 80 else _ansi(Y) if ctx_pct > 50 else _ansi(G)
    seg_ctx = f"{ctx_col}{ctx_pct:.0f}%{_ansi(RST)}"
    if ctx_size > 0:
        seg_ctx += f"{_ansi(DIM)}/{_fmt_tokens(ctx_size)}{_ansi(RST)}"

    # ── Cost estimate ─────────────────────────────────────────────────────────
    # Use total session tokens for estimate (more meaningful than last-call only)
    est_usd = _estimate_cost_usd(model_id, total_in, cached_in, last_out)
    seg_cost = f"{_ansi(Y)}{_fmt_cost(est_usd)}{_ansi(RST)}{_ansi(DIM)}*{_ansi(RST)}"

    # ── Premium requests ──────────────────────────────────────────────────────
    multiplier = _MODEL_MULTIPLIERS.get(_normalize_model(model_id), 1.0)
    pru_display = f"{total_pru}×" if multiplier == 0 else f"{total_pru}pru"
    seg_pru = f"{_ansi(DIM)}{pru_display}{_ansi(RST)}"

    # ── Quota bar (cached, non-blocking) ─────────────────────────────────────
    seg_quota = ""
    quota_data = _fetch_quota()
    pi = _get_premium_snapshot(quota_data)
    if pi:
        remaining = pi.get("remaining", 0)
        entitlement = pi.get("entitlement", 0)
        if entitlement:
            pct_used = max(0, (entitlement - remaining) / entitlement * 100)
            bar = _fmt_bar(pct_used, width=5)
            seg_quota = f" {SEP} {bar} {_ansi(DIM)}{remaining}/{entitlement}{_ansi(RST)}"

    parts = [seg_model, seg_tokens, seg_ctx, seg_cost, seg_pru]
    return f" {SEP} ".join(parts) + seg_quota


# ---------------------------------------------------------------------------
# Mode 2: Status table (sk status / sk statusline — invoked from terminal)
# ---------------------------------------------------------------------------


def _print_status_table(force_quota: bool = False) -> None:
    """Print a rich summary table of the current session."""
    sid = _get_session_id()
    state = _load_session_state(sid)

    total_tokens = state.get("total_tokens", 0)
    budget = int(os.environ.get("TOKEN_BUDGET", 100_000))
    pct = round(total_tokens / budget * 100) if budget else 0
    files_read = state.get("files_read", {})

    print(f"\n{_ansi(BOLD)}{_CHART_ICON} Copilot Session Status{_ansi(RST)}")
    print(f"  Session : {_ansi(DIM)}{_sanitize_sid(sid)}{_ansi(RST)}")

    if total_tokens > 0:
        tok_color = _ansi(R) if pct > 95 else _ansi(Y) if pct > 80 else _ansi(G)
        print(f"  Tokens  : {tok_color}{total_tokens:,}{_ansi(RST)}/{budget:,} ({pct}%)")
        print(f"  Progress: {_fmt_bar(pct, 20)}")
    else:
        print(f"  Tokens  : {_ansi(DIM)}(no data — TokenTrackerRule estimates on tool calls){_ansi(RST)}")

    if files_read:
        print(f"  Files   : {len(files_read)} accessed this session")

    # Quota info
    print()
    quota_data = _fetch_quota(force=force_quota)
    pi = _get_premium_snapshot(quota_data)
    if pi:
        remaining = pi.get("remaining", "?")
        entitlement = pi.get("entitlement", "?")
        pct_left = pi.get("percent_remaining")
        plan = quota_data.get("copilot_plan", "?")  # type: ignore[union-attr]
        reset_date = quota_data.get("quota_reset_date", "?")  # type: ignore[union-attr]
        unlimited = pi.get("unlimited", False)

        print(f"  Plan     : {_ansi(BOLD)}{plan}{_ansi(RST)}")
        if unlimited:
            print(f"  Quota    : {_ansi(G)}unlimited{_ansi(RST)}")
        else:
            q_color = _ansi(R) if (pct_left or 100) < 20 else _ansi(Y) if (pct_left or 100) < 50 else _ansi(G)
            print(
                f"  Quota    : {q_color}{remaining}{_ansi(RST)}/{entitlement}"
                f" premium requests remaining" + (f" ({pct_left:.1f}%)" if pct_left is not None else "")
            )
            if isinstance(remaining, int) and isinstance(entitlement, int) and entitlement:
                pct_used = (entitlement - remaining) / entitlement * 100
                print(f"  Bar      : {_fmt_bar(pct_used, 20)}")
        print(f"  Resets   : {reset_date}")
        overage = pi.get("overage_count", 0)
        if overage:
            ov_cost = round(overage * PREMIUM_REQUEST_USD, 2)
            print(f"  Overage  : {_ansi(R)}{overage} requests (${ov_cost:.2f}){_ansi(RST)}")
    else:
        print(f"  Quota    : {_ansi(DIM)}unavailable (gh CLI not found or not authenticated){_ansi(RST)}")

    print()
    print(f"  {_ansi(DIM)}Cost note: $* = estimated from token counts × model rates.{_ansi(RST)}")
    print(f"  {_ansi(DIM)}Models from github/docs pricing YAML. Not exact GitHub billing.{_ansi(RST)}")
    print()


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------


def main() -> None:
    args = sys.argv[1:]
    force_quota = "--quota" in args or "--refresh" in args

    # Mode 2: status table when invoked from terminal (no piped stdin)
    if sys.stdin.isatty():
        _print_status_table(force_quota=force_quota)
        return

    # Mode 1: statusline — read JSON from stdin (Copilot CLI pipes this)
    try:
        raw = sys.stdin.read(131072)  # max 128 KB
        if not raw.strip():
            return
        payload = json.loads(raw)
    except (json.JSONDecodeError, Exception):
        return  # fail-open — never break the CLI footer

    line = _render_statusline(payload)
    print(line)


if __name__ == "__main__":
    main()
