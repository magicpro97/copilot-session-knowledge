"""Tentacle enforcement + suggestion (merged).

Combines enforce-tentacle.py and tentacle-suggest.py into one module.
Single get_module() implementation, shared edit tracking.

Edit tracking uses a per-repo-partitioned JSON payload so edits in one git
repository never inflate counts for a different repository.  Entries carry a
Unix timestamp and are dropped after 24 h (TTL).
"""
import json
import re
import subprocess
import sys
import time
from pathlib import Path

from . import Rule
from .common import MARKERS_DIR, CODE_EXTENSIONS, get_module, is_session_path, is_source_path, deny, info

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
try:
    from marker_auth import (verify_marker, verify_list_marker, sign_list_marker,
                             is_secret_access, check_tamper_marker)
except ImportError:
    def verify_marker(p, n): return False
    def verify_list_marker(p): return set()
    def sign_list_marker(p, lines):
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text("\n".join(sorted(lines)))
    def is_secret_access(c): return True
    def check_tamper_marker(): return False

EDITS_FILE = MARKERS_DIR / "tentacle-edits"
TENTACLE_DONE = MARKERS_DIR / "tentacle-done"
TENTACLE_BYPASS = MARKERS_DIR / "tentacle-bypass"
SUGGESTED_FILE = MARKERS_DIR / "tentacle-suggested"

MIN_FILES = 3
MIN_MODULES = 2


# ── Edit-tracking helpers ──────────────────────────────────────────────────

def _get_git_root(cwd=None):
    """Return the git root for *cwd* (or cwd()) as a string, or None."""
    try:
        r = subprocess.run(
            ["git", "-C", str(cwd or Path.cwd()), "rev-parse", "--show-toplevel"],
            capture_output=True, text=True, timeout=5,
        )
        if r.returncode == 0:
            return r.stdout.strip()
    except Exception:
        pass
    return None


def _prune_ttl(entries, now):
    """Return entries whose timestamp is within the last 24 hours."""
    cutoff = now - 86400
    return [e for e in entries if isinstance(e, dict) and e.get("t", 0) >= cutoff]


def _read_edits(path):
    """Read the HMAC-signed edits marker and return a dict of the form::

        {git_root: [{"p": file_path, "t": unix_ts}, ...], ...}

    Accepts both:
    * **New format** – the signed content is a single-element set whose sole
      element is a JSON-encoded dict (written by ``_write_edits``).
    * **Legacy format** – the signed content is a set of bare file-path
      strings (old format).  They are migrated into a ``"legacy"`` bucket
      with the current timestamp so they expire naturally after 24 h.
    """
    raw_set = verify_list_marker(path)
    if not raw_set:
        return {}
    # New format: exactly one element that is a JSON dict
    if len(raw_set) == 1:
        sole = next(iter(raw_set))
        if sole.startswith("{"):
            try:
                data = json.loads(sole)
                if isinstance(data, dict):
                    return data
            except (json.JSONDecodeError, ValueError):
                pass
    # Legacy: flat set of file paths → migrate with current timestamp
    now = time.time()
    legacy_entries = [{"p": fp, "t": now} for fp in raw_set if fp]
    return {"legacy": legacy_entries}


def _write_edits(path, data):
    """Write *data* as an HMAC-signed single-JSON-element list marker."""
    payload = json.dumps(data, separators=(",", ":"), sort_keys=True)
    sign_list_marker(path, {payload})


def _get_entries_for_repo(data, git_root):
    """Return entries that belong to *git_root*.

    * If *git_root* is present as a key, return those entries directly.
    * Otherwise fall back to the ``"legacy"`` bucket, filtering by path
      prefix when *git_root* is known (heuristic for migrated markers).
    * If *git_root* is ``None`` (git unavailable), return all legacy entries
      without filtering.
    """
    if git_root and git_root in data:
        return list(data[git_root])
    if "legacy" in data:
        if git_root:
            return [e for e in data["legacy"] if str(e.get("p", "")).startswith(git_root)]
        return list(data["legacy"])
    return []


class TentacleEnforceRule(Rule):
    """Block edits across too many modules without tentacle-orchestration."""

    name = "tentacle-enforce"
    events = ["preToolUse"]
    tools = ["edit", "create", "bash"]

    def evaluate(self, event, data):
        tool_name = data.get("toolName", "")
        tool_args = data.get("toolArgs", {})
        if not isinstance(tool_args, dict):
            tool_args = {}

        # Kill-switch
        if check_tamper_marker():
            return deny(
                "\U0001f6a8 HOOKS TAMPERED: All modifications blocked. "
                "Run: sudo python3 ~/.copilot/tools/install.py --lock-hooks"
            )

        # For bash, check if it writes source files
        if tool_name == "bash":
            command = tool_args.get("command", "")
            if is_secret_access(command):
                return deny("\U0001f512 Access to protected hook files is blocked.")
            source_exts = tuple(CODE_EXTENSIONS)
            writes_source = False
            if any(ext in command for ext in source_exts):
                if any(p in command for p in ("<<", "write_text", "open(",
                                               "sed -i", "tee ", "cp ", "mv ",
                                               "dd ", "patch ", "rsync ", "install ")):
                    writes_source = True
                elif re.search(r">{1,2}\s*\S+", command):
                    for m in re.finditer(r">{1,2}\s*([^\s;|&]+)", command):
                        p = m.group(1)
                        # Strip surrounding shell quotes before path checks
                        if len(p) >= 2 and p[0] == p[-1] and p[0] in ('"', "'"):
                            p = p[1:-1]
                        if is_source_path(p):
                            writes_source = True
                            break
            if not writes_source:
                return None

        # FP-1: session-state files (e.g. /research outputs) are not project source;
        # skip threshold check so create/edit to these paths is never blocked.
        if tool_name in ("edit", "create"):
            file_path = tool_args.get("path", "")
            if file_path and is_session_path(file_path):
                return None

        # Check bypass markers
        if verify_marker(TENTACLE_DONE, "tentacle-done"):
            return None
        if verify_marker(TENTACLE_BYPASS, "tentacle-bypass"):
            return None

        # Check tracked edits — only count files for the current repo
        git_root = _get_git_root()
        repo_prefix = Path(git_root).name if git_root else "legacy"
        edited_dict = _read_edits(EDITS_FILE)
        now = time.time()
        for key in list(edited_dict.keys()):
            edited_dict[key] = _prune_ttl(edited_dict[key], now)
        entries = _get_entries_for_repo(edited_dict, git_root)
        if not entries or len(entries) < MIN_FILES:
            return None

        modules = {get_module(e["p"], repo_prefix) for e in entries if get_module(e["p"])}
        if len(modules) < MIN_MODULES:
            return None

        return deny(
            f"\U0001f419 TENTACLE REQUIRED: {len(entries)} files across {len(modules)} modules "
            f"({', '.join(sorted(modules))}). "
            "Multi-module edits should use tentacle-orchestration. "
            "If you are the orchestrator: (1) tentacle.py create <name> --scope \"<paths>\" --desc \"<desc>\" --briefing  "
            "(2) tentacle.py todo <name> add \"<task>\"  "
            "(3) tentacle.py swarm <name> --agent-type general-purpose --model claude-sonnet-4.6  "
            "If you are a dispatched sub-agent: stay within your assigned scope, write results to "
            "handoff.md, and avoid git commit or git push — by convention the orchestrator "
            "commits and pushes after all tentacles are verified."
        )


class TentacleSuggestRule(Rule):
    """Suggest tentacle when edits span multiple modules (postToolUse)."""

    name = "tentacle-suggest"
    events = ["postToolUse"]
    tools = ["edit", "create", "bash"]

    def evaluate(self, event, data):
        tool_name = data.get("toolName", "")
        tool_args = data.get("toolArgs", {})
        if not isinstance(tool_args, dict):
            tool_args = {}

        if SUGGESTED_FILE.is_file():
            return None

        # Collect edited file paths
        file_paths = []
        if tool_name in ("edit", "create"):
            fp = ""
            if tool_name == "edit":
                fp = (data.get("toolResult") or {}).get("filePath", "")
            elif tool_name == "create":
                fp = (data.get("input") or {}).get("filePath", "")
            if fp:
                file_paths.append(fp)
        elif tool_name == "bash":
            command = tool_args.get("command", "")
            if "<<" in command and "open(" in command:
                for m in re.finditer(r"open\(['\"]([^'\"]+)['\"]", command):
                    p = m.group(1)
                    if not p.startswith(("/tmp/", "/var/", "/dev/")):
                        file_paths.append(p)
            if ">" in command:
                for m in re.finditer(r">{1,2}\s*([^\s;|&]+)", command):
                    p = m.group(1)
                    # Strip surrounding shell quotes before path checks
                    if len(p) >= 2 and p[0] == p[-1] and p[0] in ('"', "'"):
                        p = p[1:-1]
                    if not p.startswith(("/tmp/", "/var/", "/dev/")):
                        file_paths.append(p)
            # Mirror enforce: also track paths written by sed -i and tee
            for m in re.finditer(r"\bsed\s+-i[^\s]*\s+(?:'[^']*'|\"[^\"]*\")\s+(\S+)", command):
                p = m.group(1)
                if not p.startswith(("/tmp/", "/var/", "/dev/")):
                    file_paths.append(p)
            for m in re.finditer(r"\btee\s+(?:-[a-z]+\s+)?(\S+)", command):
                p = m.group(1)
                if not p.startswith(("/tmp/", "/var/", "/dev/")):
                    file_paths.append(p)

        if not file_paths:
            return None

        # Filter: only track actual code/config files, not markdown or session-state paths.
        # This prevents session-research markdown writes from accumulating in the
        # tentacle-edits marker and falsely triggering multi-module enforcement.
        file_paths = [
            fp for fp in file_paths
            if Path(fp).suffix.lower() in CODE_EXTENSIONS and not is_session_path(fp)
        ]

        if not file_paths:
            return None

        # Track edited files using HMAC-signed list markers, partitioned by git repo
        MARKERS_DIR.mkdir(parents=True, exist_ok=True)
        git_root = _get_git_root()
        repo_key = git_root if git_root else "legacy"
        repo_prefix = Path(git_root).name if git_root else "legacy"
        now = time.time()
        edited_dict = _read_edits(EDITS_FILE)
        # Prune TTL on every bucket
        for key in list(edited_dict.keys()):
            edited_dict[key] = _prune_ttl(edited_dict[key], now)
        entries = edited_dict.get(repo_key, [])
        existing_paths = {e["p"] for e in entries}
        for fp in file_paths:
            if fp not in existing_paths:
                entries.append({"p": fp, "t": now})
                existing_paths.add(fp)
        edited_dict[repo_key] = entries
        try:
            _write_edits(EDITS_FILE, edited_dict)
        except Exception:
            pass

        if len(entries) < MIN_FILES:
            return None

        modules = {get_module(e["p"], repo_prefix) for e in entries if get_module(e["p"])}
        if len(modules) < MIN_MODULES:
            return None

        try:
            SUGGESTED_FILE.touch()
        except Exception:
            pass

        return info(
            f"\n  \U0001f419 TENTACLE SUGGESTION: {len(entries)} files across "
            f"{len(modules)} modules detected.\n"
            "  Consider using tentacle-orchestration for parallel multi-agent execution.\n"
            f"  Modules: {', '.join(sorted(modules))}\n"
        )
