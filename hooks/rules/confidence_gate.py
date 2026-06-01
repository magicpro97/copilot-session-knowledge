"""Block write/closeout actions while a decision-confidence research gate is open."""

import json
import re
import subprocess
from pathlib import Path

from . import Rule
from .common import MARKERS_DIR, bash_writes_source_files, deny

PLAN_RELATIVE_PATHS = (
    Path(".github") / "conductor" / "last-plan.json",
    Path(".copilot") / "confidence-gate.json",
)
GLOBAL_GATE_FILE = MARKERS_DIR / "confidence-gate.json"

_CLOSEOUT_RE = re.compile(
    r"\b("
    r"git\s+(push|merge|commit)"
    r"|gh\s+pr\s+(create|merge)"
    r"|gh\s+issue\s+(close|comment)"
    r"|task_complete"
    r")\b",
    re.IGNORECASE,
)


def _get_git_root(cwd=None):
    try:
        result = subprocess.run(
            ["git", "-C", str(cwd or Path.cwd()), "rev-parse", "--show-toplevel"],
            capture_output=True,
            text=True,
            timeout=5,
        )
        if result.returncode == 0:
            return Path(result.stdout.strip())
    except Exception:
        pass
    return None


def _load_json(path):
    try:
        if path.is_file():
            return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError, TypeError, ValueError):
        return None
    return None


def _candidate_gate_files(git_root):
    if git_root:
        for rel in PLAN_RELATIVE_PATHS:
            yield git_root / rel
    yield GLOBAL_GATE_FILE


def _open_gate_from_payload(payload):
    if not isinstance(payload, dict):
        return None
    if payload.get("override") is True or payload.get("confidence_gate_override") is True:
        return None
    gate = payload.get("research_gate", payload)
    if isinstance(gate, dict) and gate.get("required") is True:
        return gate
    return None


def _find_open_gate(git_root):
    for path in _candidate_gate_files(git_root):
        gate = _open_gate_from_payload(_load_json(path))
        if gate is not None:
            return path, gate
    return None, None


def _is_blocked_bash(command):
    return bool(_CLOSEOUT_RE.search(command)) or bash_writes_source_files(command)


class ConfidenceGateRule(Rule):
    """Deny writes and closeout when conductor says confidence is below 1.0."""

    name = "confidence-gate"
    events = ["preToolUse"]
    tools = ["edit", "create", "bash", "task_complete"]

    def evaluate(self, event, data):
        tool_name = data.get("toolName", "")
        _ta = data.get("toolArgs")
        _ti = data.get("toolInput")
        tool_args = {**(_ta if isinstance(_ta, dict) else {}), **(_ti if isinstance(_ti, dict) else {})}
        if not isinstance(tool_args, dict):
            tool_args = {}

        if tool_name == "bash" and not _is_blocked_bash(tool_args.get("command", "")):
            return None

        gate_path, gate = _find_open_gate(_get_git_root())
        if gate is None:
            return None

        confidence = gate.get("confidence_score", "unknown")
        required = gate.get("required_confidence", "1.0")
        return deny(
            "Decision confidence gate is open "
            f"({confidence} < {required}) from {gate_path}. "
            "Split the ambiguity, dispatch independent opus-class research/validation agents, "
            "then rerun conductor until confidence is 1.0 or record an explicit override."
        )
