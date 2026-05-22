"""verification_gate.py — Require fresh verification evidence before closeout actions.

Tracks two code surfaces (Python / browse-ui) and records evidence from
successful verification commands (tests, pnpm lint/typecheck/format/build).

Blocks closeout-style actions (task_complete, gh issue close/comment,
gh pr merge, tentacle handoff --status DONE, tentacle complete) when the
evidence ledger is empty or stale for an edited surface.

Evidence becomes stale when further edits occur on the same surface.

Fail-open: any exception inside this rule lets the operation through.
"""

import json
import os
import re
import sys
from pathlib import Path

if os.name == "nt":
    for _s in (sys.stdout, sys.stderr):
        if hasattr(_s, "reconfigure"):
            _s.reconfigure(encoding="utf-8", errors="replace")

from . import Rule
from .common import MARKERS_DIR, bash_writes_source_files, deny

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
try:
    from marker_auth import sign_list_marker, verify_list_marker
except ImportError:

    def sign_list_marker(p, lines):
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text("\n".join(sorted(lines)), encoding="utf-8")

    def verify_list_marker(p):
        try:
            return set(p.read_text(encoding="utf-8").strip().splitlines()) if p.is_file() else set()
        except Exception:
            return set()


LEDGER_FILE = MARKERS_DIR / "verification-ledger"

# Surface keys
SURFACE_PY = "py"
SURFACE_UI = "ui"

# Evidence keys — Python suites split into per-file evidence (wave 11).
# `EV_PY_TESTS` retained as an alias of the broad key for external importers
# (`tests/test_hook_rules_more.py`, Rust mirror tests) — one-release migration.
EV_PY_SECURITY = "py_security"
EV_PY_FIXES = "py_fixes"
EV_PY_TESTS_BROAD = "py_tests"
EV_PY_TESTS = EV_PY_TESTS_BROAD  # backwards-compatible alias
EV_UI_FORMAT = "ui_format"
EV_UI_LINT = "ui_lint"
EV_UI_TYPECHECK = "ui_typecheck"
EV_UI_BUILD = "ui_build"

# Required evidence per dirty surface (all must be present).
# SURFACE_PY requires BOTH suites — broad runs (`run_all_tests.py`, `pytest`)
# also satisfy this because they record both per-suite keys (see
# `_evidence_from_command`).
_REQUIREMENTS = {
    SURFACE_PY: {EV_PY_SECURITY, EV_PY_FIXES},
    SURFACE_UI: {EV_UI_FORMAT, EV_UI_LINT, EV_UI_TYPECHECK, EV_UI_BUILD},
}

# Human-readable commands to fix missing evidence
_FIX_COMMANDS = {
    EV_PY_SECURITY: "python3 test_security.py",
    EV_PY_FIXES: "python3 test_fixes.py",
    EV_PY_TESTS_BROAD: "python3 run_all_tests.py",
    EV_UI_FORMAT: "cd browse-ui && pnpm format:check",
    EV_UI_LINT: "cd browse-ui && pnpm lint",
    EV_UI_TYPECHECK: "cd browse-ui && pnpm typecheck",
    EV_UI_BUILD: "cd browse-ui && pnpm build",
}

# Evidence keys that must also be cleared when a surface is freshly dirtied,
# even though they are not strictly part of `_REQUIREMENTS`. Used for
# deprecated aliases like `py_tests` (the broad Python key) so the legacy-
# upgrade path does not re-pollute the in-memory ledger after a stale clear.
_DEPRECATED_STALE_KEYS = {
    SURFACE_PY: {EV_PY_TESTS_BROAD},
}

# Stable Python deny message — always lists both suites in [security, fixes]
# order so test assertions and operator UX stay consistent regardless of which
# subset is missing.
_PY_DENY_FIX = "python3 test_security.py && python3 test_fixes.py"

# Failure indicators in toolResult output (non-zero counts only)
_FAIL_RE = re.compile(
    r"(?:"
    r"FAILED\b"
    r"|[Ff]ailed:\s*[1-9]\d*"
    r"|[Ee]rror(?:s)?:\s+[1-9]\d*"
    r"|error\s+TS\d+"
    r"|[Ee]xit\s+(?:code|status)\s*[1-9]"
    r"|[1-9]\d*\s+fail(?:ed|ure)"
    r")"
)


def _parse_ledger_payload(raw_text):
    try:
        data = json.loads(raw_text)
    except (json.JSONDecodeError, TypeError, ValueError):
        return None
    if not isinstance(data, dict):
        return None
    return {
        "dirty": set(data.get("dirty", [])),
        "evidence": set(data.get("evidence", [])),
    }


def _read_ledger():
    """Read the verification ledger. Returns dict: {dirty: set, evidence: set}.

    Legacy upgrade: if the on-disk ledger has `py_tests` but neither
    `py_security` nor `py_fixes`, the in-memory result is expanded to include
    both per-suite keys. This treats a prior broad-suite pass as covering the
    new per-suite requirements (one-release migration). The on-disk file is
    not rewritten here — the next legitimate `_write_ledger` normalizes it.
    """

    def _upgrade(parsed):
        ev = parsed["evidence"]
        if EV_PY_TESTS_BROAD in ev and not (ev & {EV_PY_SECURITY, EV_PY_FIXES}):
            ev.update({EV_PY_SECURITY, EV_PY_FIXES})
        return parsed

    raw_set = verify_list_marker(LEDGER_FILE)
    if raw_set:
        if len(raw_set) == 1:
            sole = next(iter(raw_set))
            if sole.startswith("{"):
                parsed = _parse_ledger_payload(sole)
                if parsed is not None:
                    return _upgrade(parsed)
        return {"dirty": set(), "evidence": set()}

    # Backward compatibility: older upstream versions wrote plain JSON directly.
    if LEDGER_FILE.is_file():
        try:
            parsed = _parse_ledger_payload(LEDGER_FILE.read_text(encoding="utf-8"))
            if parsed is not None:
                return _upgrade(parsed)
        except Exception:
            pass
    return {"dirty": set(), "evidence": set()}


def _tool_input(data):
    """Return tool arguments from either payload shape used by CLI hooks.

    Most hooks historically used `toolArgs`, but newer/runtime-specific tool
    events may provide the same values under `toolInput` or `input`.
    Verification must accept all observed shapes so successful test commands
    are not missed.
    """
    first_non_empty = {}
    for key in ("toolArgs", "toolInput", "input"):
        value = data.get(key, {})
        if isinstance(value, dict):
            if "command" in value or "path" in value:
                return value
            if value and not first_non_empty:
                first_non_empty = value
    return first_non_empty


def _write_ledger(dirty, evidence):
    """Write the verification ledger (HMAC-signed via sign_list_marker)."""
    try:
        MARKERS_DIR.mkdir(parents=True, exist_ok=True)
        payload = json.dumps(
            {"dirty": sorted(dirty), "evidence": sorted(evidence)},
            separators=(",", ":"),
            sort_keys=True,
        )
        sign_list_marker(LEDGER_FILE, {payload})
    except Exception:
        pass


def _surfaces_from_path(path):
    """Return set of surfaces affected by editing path."""
    surfaces = set()
    p = str(path)
    norm = p.replace("\\", "/").lower()
    if "/.copilot/session-state/" in norm or "/.copilot/skills/" in norm:
        return surfaces
    suffix = Path(path).suffix.lower()
    if "browse-ui/" in p or p.startswith("browse-ui/"):
        if suffix in (".ts", ".tsx", ".js", ".jsx"):
            surfaces.add(SURFACE_UI)
    if suffix == ".py":
        surfaces.add(SURFACE_PY)
    return surfaces


def _strip_shell_quotes(path):
    if len(path) >= 2 and path[0] == path[-1] and path[0] in ('"', "'"):
        return path[1:-1]
    return path


def _extract_written_paths(command):
    """Best-effort extraction of source-file write targets from bash commands."""
    paths = []
    if "<<" in command and "open(" in command:
        for m in re.finditer(r"open\(['\"]([^'\"]+)['\"]", command):
            paths.append(m.group(1))
    for m in re.finditer(r">{1,2}\s*([^\s;|&]+)", command):
        paths.append(_strip_shell_quotes(m.group(1)))
    for m in re.finditer(r"\bsed\s+-i[^\s]*\s+(?:'[^']*'|\"[^\"]*\")\s+(\S+)", command):
        paths.append(_strip_shell_quotes(m.group(1)))
    for m in re.finditer(r"\btee\s+(?:-[a-z]+\s+)?(\S+)", command):
        paths.append(_strip_shell_quotes(m.group(1)))
    return [p for p in paths if p]


def _evidence_from_command(command):
    """Detect evidence categories a bash command provides (pattern-based).

    Python suites are tracked per-file:
      - `test_security.py` substring earns `py_security`.
      - `test_fixes.py` substring earns `py_fixes`.
      - `run_all_tests.py` or `pytest` (broad runs) earn all three keys.
      - Generic `python[3]? test_<name>.py` earns only the broad alias.
    """
    ev = set()
    # Python per-suite triggers
    if "test_security.py" in command:
        ev.add(EV_PY_SECURITY)
    if "test_fixes.py" in command:
        ev.add(EV_PY_FIXES)
    broad = ("run_all_tests.py" in command) or bool(re.search(r"\bpytest\b", command))
    if broad:
        ev.update({EV_PY_SECURITY, EV_PY_FIXES, EV_PY_TESTS_BROAD})
    elif re.search(r"\bpython3?\s+test_\w+\.py\b", command) and not (ev & {EV_PY_SECURITY, EV_PY_FIXES}):
        ev.add(EV_PY_TESTS_BROAD)
    # browse-ui pnpm checks
    if "pnpm format" in command:
        ev.add(EV_UI_FORMAT)
    if "pnpm lint" in command:
        ev.add(EV_UI_LINT)
    if "pnpm typecheck" in command:
        ev.add(EV_UI_TYPECHECK)
    if "pnpm build" in command:
        ev.add(EV_UI_BUILD)
    return ev


def _mark_dirty_surfaces(surfaces):
    """Mark surfaces dirty and clear evidence that became stale.

    Clears every evidence key referenced by `_REQUIREMENTS[surface]` and any
    `_DEPRECATED_STALE_KEYS[surface]` (e.g. the `py_tests` broad alias for
    Python). Clearing deprecated keys prevents the read-time legacy upgrade
    from re-adding them on the next read.
    """
    if not surfaces:
        return
    ledger = _read_ledger()
    new_dirty = ledger["dirty"] | surfaces
    stale_keys = set()
    for surface in surfaces:
        stale_keys |= _REQUIREMENTS.get(surface, set())
        stale_keys |= _DEPRECATED_STALE_KEYS.get(surface, set())
    new_ev = {ev_key for ev_key in ledger["evidence"] if ev_key not in stale_keys}
    _write_ledger(new_dirty, new_ev)


def _looks_successful(data):
    """Return True if toolResult shows no obvious failure indicators.

    Fail-open: missing or unreadable toolResult → assume success.
    """
    tool_result = data.get("toolResult", "")
    if not tool_result:
        return True
    # Handle dict result (may have exitCode or output key)
    if isinstance(tool_result, dict):
        exit_code = tool_result.get("exitCode") or tool_result.get("exit_code")
        if isinstance(exit_code, int) and exit_code != 0:
            return False
        output = str(tool_result.get("output", tool_result.get("stdout", "")))
    else:
        output = str(tool_result)
    return not bool(_FAIL_RE.search(output))


def _is_closeout(tool_name, tool_args):
    """Return (is_closeout: bool, description: str) for known closeout actions."""
    if tool_name == "task_complete":
        return True, "task_complete"
    if tool_name not in ("bash", "powershell"):
        return False, ""
    cmd = tool_args.get("command", "")
    if re.search(r"\bgh\b.*\bissue\b.*\bclose\b", cmd):
        return True, "gh issue close"
    if re.search(r"\bgh\b.*\bissue\b.*\bcomment\b", cmd):
        return True, "gh issue comment"
    if re.search(r"\bgh\b.*\bpr\b.*\bmerge\b", cmd):
        return True, "gh pr merge"
    if re.search(r"(?:tentacle\.py|sk\s+tentacle)\b.*\bhandoff\b.*--status\s+DONE\b", cmd):
        return True, "tentacle handoff --status DONE"
    if re.search(r"(?:tentacle\.py|sk\s+tentacle)\b.*\bcomplete\b", cmd):
        return True, "tentacle complete"
    return False, ""


class VerificationGateRule(Rule):
    """Require verification evidence before closeout actions.

    preToolUse[edit/create]: mark surface dirty, clear stale evidence.
    postToolUse[bash/powershell]: record evidence from successful commands.
    preToolUse[bash/powershell/task_complete]: block closeout when evidence is missing.
    """

    name = "verification-gate"
    events = ["preToolUse", "postToolUse"]
    tools = ["edit", "create", "bash", "powershell", "task_complete"]

    def evaluate(self, event, data):
        try:
            if event == "preToolUse":
                return self._pre(data)
            if event == "postToolUse":
                return self._post(data)
        except Exception:
            pass  # fail-open: never block on rule errors
        return None

    # ── preToolUse ─────────────────────────────────────────────────────────

    def _pre(self, data):
        tool_name = data.get("toolName", "")
        tool_args = _tool_input(data)

        # Track edits: mark surfaces dirty and clear now-stale evidence
        if tool_name in ("edit", "create"):
            path = tool_args.get("path", "")
            if path:
                _mark_dirty_surfaces(_surfaces_from_path(path))
            return None  # always allow edits

        # Gate closeout actions
        is_closeout, closeout_desc = _is_closeout(tool_name, tool_args)
        if not is_closeout:
            return None

        ledger = _read_ledger()
        if not ledger["dirty"]:
            return None  # No tracked edits → no requirement

        missing_msgs = []
        for surface in sorted(ledger["dirty"]):
            required = _REQUIREMENTS.get(surface, set())
            gaps = required - ledger["evidence"]
            if not gaps:
                continue
            fix_parts = [_FIX_COMMANDS[k] for k in sorted(gaps) if k in _FIX_COMMANDS]
            if surface == SURFACE_PY:
                # Always present the AND-joined pair so the message is stable
                # regardless of which subset of py_security/py_fixes is missing.
                missing_msgs.append(f"Python edits need test evidence: {_PY_DENY_FIX}")
            elif surface == SURFACE_UI:
                missing_msgs.append(
                    "browse-ui edits need format/lint/typecheck/build evidence:\n"
                    + "\n".join(f"    {cmd}" for cmd in fix_parts)
                )

        if not missing_msgs:
            return None

        bullet_list = "\n".join(f"  • {m}" for m in missing_msgs)
        return deny(
            f"\U0001f50e VERIFICATION REQUIRED before {closeout_desc}:\n"
            f"{bullet_list}\n"
            "Run the above commands and retry."
        )

    # ── postToolUse ────────────────────────────────────────────────────────

    def _post(self, data):
        tool_name = data.get("toolName", "")
        if tool_name not in ("bash", "powershell"):
            return None
        tool_args = _tool_input(data)
        command = tool_args.get("command", "")
        if bash_writes_source_files(command):
            written_surfaces = set()
            for path in _extract_written_paths(command):
                written_surfaces |= _surfaces_from_path(path)
            _mark_dirty_surfaces(written_surfaces)
        ev_detected = _evidence_from_command(command)
        if not ev_detected:
            return None
        # Only record evidence if the command appeared to succeed
        if not _looks_successful(data):
            return None
        ledger = _read_ledger()
        new_evidence = ledger["evidence"] | ev_detected
        _write_ledger(ledger["dirty"], new_evidence)
        return None  # postToolUse: informational only
