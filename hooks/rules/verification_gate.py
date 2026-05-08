"""verification_gate.py — preToolUse/postToolUse hook that tracks which
code surfaces have been edited and gates closeout actions (task_complete,
gh issue close, tentacle handoff --status DONE) until matching verification
evidence (test runs, lint, build) has been recorded.

Surfaces:
  SURFACE_PY — any .py file
  SURFACE_UI — browse-ui/**/*.{ts,tsx,js,jsx}

Evidence keys:
  EV_PY_TESTS    — python test_security.py / test_fixes.py / run_all_tests.py / pytest
  EV_UI_FORMAT   — pnpm format:check / pnpm format
  EV_UI_LINT     — pnpm lint
  EV_UI_TYPECHECK — pnpm typecheck
  EV_UI_BUILD    — pnpm build

Ledger: a JSON file under MARKERS_DIR tracking {dirty: set, evidence: set}.

Fail-open: all exceptions are caught so the rule never blocks work by crashing.
"""

import json
import os
import re
import sys
from pathlib import Path

from . import Rule
from .common import MARKERS_DIR, deny

if os.name == "nt":
    for _s in (sys.stdout, sys.stderr):
        if hasattr(_s, "reconfigure"):
            _s.reconfigure(encoding="utf-8", errors="replace")

# ── Surface and evidence constants ────────────────────────────────────────

SURFACE_PY = "py"
SURFACE_UI = "ui"

EV_PY_TESTS = "py_tests"
EV_UI_FORMAT = "ui_format"
EV_UI_LINT = "ui_lint"
EV_UI_TYPECHECK = "ui_typecheck"
EV_UI_BUILD = "ui_build"

# Evidence required per surface before closeout is allowed
_REQUIRED_EVIDENCE = {
    SURFACE_PY: {EV_PY_TESTS},
    SURFACE_UI: {EV_UI_FORMAT, EV_UI_LINT, EV_UI_TYPECHECK, EV_UI_BUILD},
}

# Evidence to clear when a surface is re-dirtied (all evidence for that surface)
_SURFACE_EVIDENCE = {
    SURFACE_PY: {EV_PY_TESTS},
    SURFACE_UI: {EV_UI_FORMAT, EV_UI_LINT, EV_UI_TYPECHECK, EV_UI_BUILD},
}

LEDGER_FILE = MARKERS_DIR / "verification-ledger"

# ── Path → surface mapping ────────────────────────────────────────────────

_PY_SUFFIX = ".py"
_UI_SUFFIXES = {".ts", ".tsx", ".js", ".jsx"}


def _surfaces_from_path(path: str) -> set:
    """Determine which surfaces a file path belongs to."""
    surfaces = set()
    p = Path(path)

    if p.suffix == _PY_SUFFIX:
        surfaces.add(SURFACE_PY)

    # browse-ui source files (not CSS, not config)
    parts = p.parts
    if any(part == "browse-ui" for part in parts) and p.suffix in _UI_SUFFIXES:
        surfaces.add(SURFACE_UI)

    return surfaces


# ── Command → evidence mapping ────────────────────────────────────────────

_PY_TEST_PATTERNS = re.compile(
    r"test_security\.py|test_fixes\.py|run_all_tests\.py|pytest"
)
_UI_FORMAT_PATTERNS = re.compile(r"pnpm\s+format(?::check)?")
_UI_LINT_PATTERNS = re.compile(r"pnpm\s+lint")
_UI_TYPECHECK_PATTERNS = re.compile(r"pnpm\s+typecheck")
_UI_BUILD_PATTERNS = re.compile(r"pnpm\s+build")


def _evidence_from_command(command: str) -> set:
    """Determine which evidence keys a bash command produces."""
    evidence = set()

    if _PY_TEST_PATTERNS.search(command):
        evidence.add(EV_PY_TESTS)
    if _UI_FORMAT_PATTERNS.search(command):
        evidence.add(EV_UI_FORMAT)
    if _UI_LINT_PATTERNS.search(command):
        evidence.add(EV_UI_LINT)
    if _UI_TYPECHECK_PATTERNS.search(command):
        evidence.add(EV_UI_TYPECHECK)
    if _UI_BUILD_PATTERNS.search(command):
        evidence.add(EV_UI_BUILD)

    return evidence


# ── Bash write detection for postToolUse ──────────────────────────────────

_BASH_WRITE_RE = re.compile(
    r">{1,2}\s*([^\s;|&]+)|"
    r"\btee\s+(?:-a\s+)?([^\s;|&]+)|"
    r"\bprintf\b.*>\s*([^\s;|&]+)"
)


def _bash_write_targets(command: str) -> list:
    """Extract file paths that a bash command writes to."""
    targets = []
    for m in _BASH_WRITE_RE.finditer(command):
        path = m.group(1) or m.group(2) or m.group(3)
        if path:
            # Strip quotes
            if len(path) >= 2 and path[0] == path[-1] and path[0] in ('"', "'"):
                path = path[1:-1]
            targets.append(path)
    return targets


# ── Success detection ─────────────────────────────────────────────────────


def _looks_successful(data: dict) -> bool:
    """Heuristic: did the tool run succeed? Fail-open (return True on ambiguity)."""
    result = data.get("toolResult")
    if result is None:
        return True

    if isinstance(result, dict):
        exit_code = result.get("exitCode")
        if isinstance(exit_code, int) and exit_code != 0:
            return False
        return True

    if isinstance(result, str):
        # TypeScript compiler errors (e.g. "error TS2339: Property 'x' does not exist")
        if re.search(r"\berror TS\d", result):
            return False
        # Uppercase FAILED usually indicates test framework failure output
        if re.search(r"\bFAILED\b", result):
            return False
        # "N failed" where N > 0 (lowercase, from test runners like "3 failed")
        m = re.search(r"(\d+)\s+failed\b", result.lower())
        if m and int(m.group(1)) > 0:
            return False

    return True


# ── Closeout detection ────────────────────────────────────────────────────

_CLOSEOUT_BASH_PATTERNS = [
    (re.compile(r"\bgh\s+issue\s+close\b"), "gh issue close"),
    (re.compile(r"\bgh\s+issue\s+comment\b"), "gh issue comment"),
    (re.compile(r"\bgh\s+pr\s+merge\b"), "gh pr merge"),
    (re.compile(r"tentacle\.py\s+handoff\b.*--status\s+DONE\b"), "tentacle handoff --status DONE"),
    (re.compile(r"\bsk\s+tentacle\s+handoff\b.*--status\s+DONE\b"), "sk tentacle handoff --status DONE"),
    (re.compile(r"tentacle\.py\s+complete\b"), "tentacle complete"),
]


def _is_closeout(tool_name: str, tool_args: dict) -> tuple:
    """Return (is_closeout: bool, description: str)."""
    if tool_name == "task_complete":
        return True, "task_complete"

    if tool_name == "bash":
        command = tool_args.get("command", "")
        for pattern, desc in _CLOSEOUT_BASH_PATTERNS:
            if pattern.search(command):
                return True, desc

    return False, ""


# ── Ledger I/O ────────────────────────────────────────────────────────────


def _read_ledger() -> dict:
    """Read the verification ledger. Returns {dirty: set, evidence: set}."""
    try:
        if LEDGER_FILE.exists():
            raw = json.loads(LEDGER_FILE.read_text(encoding="utf-8"))
            return {
                "dirty": set(raw.get("dirty", [])),
                "evidence": set(raw.get("evidence", [])),
            }
    except Exception:
        pass
    return {"dirty": set(), "evidence": set()}


def _write_ledger(dirty: set, evidence: set) -> None:
    """Write the verification ledger atomically."""
    try:
        MARKERS_DIR.mkdir(parents=True, exist_ok=True)
        data = json.dumps({
            "dirty": sorted(dirty),
            "evidence": sorted(evidence),
        })
        LEDGER_FILE.write_text(data, encoding="utf-8")
    except Exception:
        pass


# ── Rule ──────────────────────────────────────────────────────────────────


class VerificationGateRule(Rule):
    """Track code edits and gate closeout until verification evidence exists."""

    name = "verification-gate"
    events = ["preToolUse", "postToolUse"]
    tools = ["edit", "create", "bash", "task_complete"]

    def evaluate(self, event, data):
        try:
            return self._evaluate_inner(event, data)
        except Exception:
            # Fail-open: never block on internal errors
            return None

    def _evaluate_inner(self, event, data):
        tool_name = data.get("toolName", "")
        tool_args = data.get("toolArgs") or {}
        if not isinstance(tool_args, dict):
            tool_args = {}

        if event == "preToolUse":
            return self._on_pre(tool_name, tool_args, data)
        elif event == "postToolUse":
            return self._on_post(tool_name, tool_args, data)
        return None

    # ── preToolUse ────────────────────────────────────────────────────

    def _on_pre(self, tool_name, tool_args, data):
        # Track edit/create → mark surface dirty, clear stale evidence
        if tool_name in ("edit", "create"):
            path = tool_args.get("path", "")
            surfaces = _surfaces_from_path(path)
            if surfaces:
                ledger = _read_ledger()
                ledger["dirty"] |= surfaces
                # Clear evidence for re-dirtied surfaces
                for s in surfaces:
                    ledger["evidence"] -= _SURFACE_EVIDENCE.get(s, set())
                _write_ledger(ledger["dirty"], ledger["evidence"])
            return None

        # Gate closeout actions
        is_close, desc = _is_closeout(tool_name, tool_args)
        if not is_close:
            return None

        ledger = _read_ledger()
        if not ledger["dirty"]:
            return None  # No edits tracked → no requirement

        missing = []
        for surface in ledger["dirty"]:
            required = _REQUIRED_EVIDENCE.get(surface, set())
            lacking = required - ledger["evidence"]
            if lacking:
                missing.append((surface, lacking))

        if not missing:
            return None  # All evidence collected

        # Build denial message
        parts = ["⚠️ VERIFICATION REQUIRED before closeout\n"]
        for surface, lacking in missing:
            if surface == SURFACE_PY:
                parts.append(
                    "Python files were modified. Run verification:\n"
                    "  python3 test_security.py && python3 test_fixes.py"
                )
            elif surface == SURFACE_UI:
                parts.append(
                    "browse-ui files were modified. Run verification:\n"
                    "  cd browse-ui && pnpm format:check && pnpm lint && pnpm typecheck && pnpm build"
                )

        return deny("\n".join(parts))

    # ── postToolUse ───────────────────────────────────────────────────

    def _on_post(self, tool_name, tool_args, data):
        if tool_name != "bash":
            return None

        command = tool_args.get("command", "")

        # Check for bash writes that dirty surfaces
        write_targets = _bash_write_targets(command)
        write_surfaces = set()
        for target in write_targets:
            write_surfaces |= _surfaces_from_path(target)

        if write_surfaces:
            ledger = _read_ledger()
            ledger["dirty"] |= write_surfaces
            for s in write_surfaces:
                ledger["evidence"] -= _SURFACE_EVIDENCE.get(s, set())
            _write_ledger(ledger["dirty"], ledger["evidence"])

        # Record evidence from successful verification commands
        evidence = _evidence_from_command(command)
        if evidence and _looks_successful(data):
            ledger = _read_ledger()
            ledger["evidence"] |= evidence
            _write_ledger(ledger["dirty"], ledger["evidence"])

        return None
