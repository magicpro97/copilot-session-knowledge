"""Edit tracking + test reminder (merged).

Combines track-bash-edits.py and test-after-edit.py.
Uses HMAC-signed counters consistently (fixes test-after-edit plain counter bug).
"""

import os
import re
import subprocess
import sys
from pathlib import Path

from . import Rule
from .common import CODE_EXTENSIONS, MARKERS_DIR, get_session_marker_suffix, info, is_session_path, update_session_state

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
try:
    from marker_auth import sign_counter, sign_list_marker, verify_counter, verify_list_marker
except ImportError:

    def sign_counter(p, v):
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(str(v))

    def verify_counter(p):
        try:
            return int(p.read_text().strip()) if p.is_file() else 0
        except Exception:
            return 0

    def sign_list_marker(p, lines):
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text("\n".join(sorted(lines)))

    def verify_list_marker(p):
        try:
            return set(p.read_text().strip().splitlines()) if p.is_file() else set()
        except Exception:
            return set()


CODE_EDIT_COUNTER = MARKERS_DIR / "code-edit-count"
PY_EDIT_COUNTER = MARKERS_DIR / "py-edit-count"
SEEN_MODIFIED = MARKERS_DIR / "git-modified-seen"
TENTACLE_EDITS = MARKERS_DIR / "tentacle-edits"
TESTS_RAN = MARKERS_DIR / "tests-ran"

SAFE_PATH_PREFIXES = ("/tmp/", "/var/", "/dev/", "/proc/")


# Per-file repeated-edit tracking (issue #93).
# Warn when the same file is edited >= FILE_EDIT_THRESHOLD times in one session.
def _parse_file_edit_threshold() -> int:
    """Parse COPILOT_FILE_EDIT_THRESHOLD, falling back to 3 on invalid or sub-1 input."""
    raw = os.environ.get("COPILOT_FILE_EDIT_THRESHOLD", "")
    try:
        val = int(raw)
        # 0 or negative would fire on every edit — use the safe default instead.
        if val < 1:
            return 3
        return val
    except (ValueError, TypeError):
        return 3


FILE_EDIT_THRESHOLD: int = _parse_file_edit_threshold()


class TrackEditsRule(Rule):
    """Track file changes after bash commands via git status."""

    name = "track-edits"
    events = ["postToolUse"]
    tools = ["bash"]

    def evaluate(self, event, data):
        current_modified = self._get_git_modified()
        if not current_modified:
            return None

        previously_seen = self._load_seen()
        new_modifications = current_modified - previously_seen
        if not new_modifications:
            return None

        new_code_files = set()
        new_py_files = set()
        for f in new_modifications:
            suffix = Path(f).suffix.lower()
            if suffix in CODE_EXTENSIONS and not is_session_path(f):
                new_code_files.add(f)
            if suffix == ".py":
                new_py_files.add(f)

        # Update HMAC-signed counters
        if new_code_files:
            current_count = verify_counter(CODE_EDIT_COUNTER)
            sign_counter(CODE_EDIT_COUNTER, current_count + len(new_code_files))
            existing = verify_list_marker(TENTACLE_EDITS)
            existing.update(new_code_files)
            sign_list_marker(TENTACLE_EDITS, existing)

        if new_py_files:
            py_count = verify_counter(PY_EDIT_COUNTER)
            sign_counter(PY_EDIT_COUNTER, py_count + len(new_py_files))

        self._save_seen(previously_seen | current_modified)

        if new_code_files:
            files_str = ", ".join(sorted(new_code_files)[:5])
            msg = f"  \U0001f4dd Detected {len(new_code_files)} file change(s) via bash: {files_str}"
            if len(new_code_files) > 5:
                msg += f"\n     ... and {len(new_code_files) - 5} more"
            return info(msg)

        return None

    def _get_git_modified(self):
        try:
            result = subprocess.run(
                ["git", "status", "--porcelain", "-uall"], capture_output=True, text=True, timeout=5, cwd=os.getcwd()
            )
            if result.returncode != 0:
                return set()
            files = set()
            for line in result.stdout.strip().splitlines():
                if not line or len(line) < 4:
                    continue
                if line[:2].strip().startswith("D"):
                    continue
                filepath = line[3:].strip()
                if " -> " in filepath:
                    filepath = filepath.split(" -> ")[-1]
                files.add(filepath)
            return files
        except Exception:
            return set()

    def _load_seen(self):
        try:
            if SEEN_MODIFIED.is_file():
                return set(SEEN_MODIFIED.read_text().strip().splitlines())
        except Exception:
            pass
        return set()

    def _save_seen(self, seen):
        try:
            MARKERS_DIR.mkdir(parents=True, exist_ok=True)
            SEEN_MODIFIED.write_text("\n".join(sorted(seen)))
        except Exception:
            pass


class TestReminderRule(Rule):
    """Remind to run tests after Python file edits. Uses HMAC-signed counters."""

    name = "test-reminder"
    events = ["postToolUse"]
    tools = ["edit", "create", "bash"]

    def evaluate(self, event, data):
        tool_name = data.get("toolName", "")
        tool_args = data.get("toolArgs", {})
        if not isinstance(tool_args, dict):
            tool_args = {}

        if tool_name in ("edit", "create"):
            file_path = ""
            if tool_name == "edit":
                file_path = (data.get("toolResult") or {}).get("filePath", "")
            elif tool_name == "create":
                file_path = (data.get("input") or {}).get("filePath", "")

            # Per-file repeated-edit tracking (issue #93) — informational only.
            per_file_msg = None
            if file_path and not is_session_path(file_path):
                per_file_msg = self._track_file_edit(file_path, data=data)

            # Existing .py test-run reminder.
            if file_path and file_path.endswith(".py") and not is_session_path(file_path):
                py_msg = self._increment_and_warn()
                if per_file_msg and py_msg:
                    combined = per_file_msg.get("message", "") + py_msg.get("message", "")
                    return info(combined)
                return py_msg or per_file_msg

            return per_file_msg

        if tool_name == "bash":
            command = tool_args.get("command", "")
            py_writes = self._detect_py_writes(command)
            if py_writes:
                return self._increment_and_warn(len(py_writes))
            # Detect test runs
            if "test_security.py" in command or "test_fixes.py" in command or "pytest" in command:
                try:
                    MARKERS_DIR.mkdir(parents=True, exist_ok=True)
                    TESTS_RAN.touch()
                except Exception:
                    pass
            return None

        return None

    def _track_file_edit(self, file_path: str, data=None):
        """Increment per-file edit count and warn if threshold is reached.

        Uses ``common.update_session_state`` for a locked per-session RMW so
        that concurrent ``postToolUse`` calls in the same session cannot lose
        increments or double-fire the threshold warning.  This is informational
        only — never blocks (fail-open).  The state is session-scoped and lives
        under the ``file_edit_counts`` key of the shared session-state JSON,
        which is cleaned up by SessionEndRule via the shared session-state file.
        """
        result_holder = [None]

        def _updater(state):
            counts = state.setdefault("file_edit_counts", {})
            counts[file_path] = counts.get(file_path, 0) + 1
            n = counts[file_path]
            if n >= FILE_EDIT_THRESHOLD:
                result_holder[0] = info(
                    f"\n  ⚠️ REPEATED EDIT ALERT: '{Path(file_path).name}' has been edited "
                    f"{n}x this session (threshold: {FILE_EDIT_THRESHOLD}).\n"
                    "  If this edit fixes a bug, log it: sk learn --mistake\n"
                    "  Consider splitting changes into smaller commits or reviewing your approach.\n"
                )

        try:
            update_session_state(_updater, data)
        except Exception:
            pass
        return result_holder[0]

    def _detect_py_writes(self, command):
        paths = []
        if "<<" in command and "open(" in command:
            for m in re.finditer(r"open\(['\"]([^'\"]+\.py)['\"]", command):
                if not any(m.group(1).startswith(p) for p in SAFE_PATH_PREFIXES):
                    paths.append(m.group(1))
        for m in re.finditer(r">\s*(/[^\s;|&]+\.py)", command):
            if not any(m.group(1).startswith(p) for p in SAFE_PATH_PREFIXES):
                paths.append(m.group(1))
        if re.search(r"\bsed\s+-i\b.*\.py", command):
            paths.append("sed-edit")
        return paths

    def _increment_and_warn(self, added=1):
        MARKERS_DIR.mkdir(parents=True, exist_ok=True)
        count = verify_counter(PY_EDIT_COUNTER) + added
        sign_counter(PY_EDIT_COUNTER, count)

        if TESTS_RAN.is_file():
            try:
                TESTS_RAN.unlink()
            except Exception:
                pass

        if count >= 3 and count % 3 == 0:
            return info(
                f"\n  \u26a0\ufe0f TEST REMINDER: {count} Python files edited without running tests!\n"
                "  Run: python3 test_security.py && python3 test_fixes.py\n"
            )
        return None
