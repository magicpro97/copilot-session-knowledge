"""Shared utilities for hook rules.

Single source of truth for constants, path helpers, and result constructors.
"""
import re
from pathlib import Path

MARKERS_DIR = Path.home() / ".copilot" / "markers"
TOOLS_DIR = Path.home() / ".copilot" / "tools"

SAFE_PATH_PREFIXES = ("/tmp/", "/var/", "/dev/", "/proc/")

# CODE_EXTENSIONS: files counted as code edits for learn-gate and tentacle tracking.
# Markdown (.md) is intentionally excluded: session-research and documentation writes
# must not inflate multi-module edit counters and trigger false tentacle enforcement.
CODE_EXTENSIONS = {
    ".py", ".kt", ".ts", ".tsx", ".js", ".jsx", ".swift", ".java",
    ".go", ".rs", ".json", ".yaml", ".yml", ".xml", ".html", ".css",
    ".toml", ".sh", ".bat", ".ps1",
}

# SOURCE_EXTENSIONS: broader set used by is_source_path() for bash-write detection.
# Keeps .md so bash commands writing markdown are still visible to safety helpers.
SOURCE_EXTENSIONS = {
    ".py", ".kt", ".ts", ".tsx", ".js", ".jsx", ".swift", ".java",
    ".go", ".rs", ".json", ".yaml", ".yml", ".xml", ".html", ".css",
    ".md", ".toml",
}

# Absolute prefix for session-state files (e.g. research markdown under ~/.copilot/session-state/).
_SESSION_STATE_ABS = str(Path.home() / ".copilot" / "session-state")


def is_session_path(path: str) -> bool:
    """Return True if path is under the Copilot session-state directory.

    Session-state files (research notes, briefings, knowledge fragments) are
    not project source code and must not count as code edits for learn-gate
    or tentacle-enforcement purposes.
    """
    p = str(path)
    return p.startswith(_SESSION_STATE_ABS) or ".copilot/session-state" in p

MODULE_MARKERS = (
    "src", "lib", "app", "pkg", "internal", "cmd",
    "hooks", "skills", "templates", "tests", "test",
    "components", "screens", "services", "utils", "models",
    "views", "controllers", "routes", "pages", "features",
    "presentation", "domain", "data", "core", "common",
    "ui", "api", "db", "auth", "config", "settings",
    "alarm", "timer", "stopwatch", "clock", "widget",
)


def _strip_shell_quotes(s: str) -> str:
    """Strip surrounding single or double shell quotes from a string."""
    if len(s) >= 2 and s[0] == s[-1] and s[0] in ('"', "'"):
        return s[1:-1]
    return s


def is_source_path(path):
    """Check if a path is a source code file (not in safe temp dirs or session-state)."""
    if any(path.startswith(p) for p in SAFE_PATH_PREFIXES):
        return False
    if is_session_path(path):
        return False
    return Path(path).suffix.lower() in SOURCE_EXTENSIONS


def get_module(file_path):
    """Extract module from path using deepest meaningful directory."""
    parts = Path(file_path).parts
    best = ""
    for i, p in enumerate(parts[:-1]):
        if p in MODULE_MARKERS:
            best = f"{p}/{parts[i + 1]}" if i + 1 < len(parts) - 1 else p
    if best:
        return best
    return parts[-2] if len(parts) >= 2 else ""


def bash_writes_source_files(command):
    """Detect if a bash command writes to source files."""
    if "<<" in command:
        if "open(" in command and ("'w'" in command or '"w"' in command):
            return True
        if "writeFileSync" in command or "writeFile(" in command:
            return True
        if "File.write" in command or "File.open" in command:
            return True
        if re.search(r"open\s*\(.*['\"]>['\"]", command):
            return True

    for m in re.finditer(r">{1,2}\s*([^\s;|&]+)", command):
        if is_source_path(_strip_shell_quotes(m.group(1))):
            return True

    if re.search(r"\bsed\s+-i", command):
        return True

    for m in re.finditer(r"\btee\s+(?:-a\s+)?([^\s;|&]+)", command):
        if is_source_path(_strip_shell_quotes(m.group(1))):
            return True

    for m in re.finditer(r"\b(?:cp|mv|install)\b.*\s([^\s;|&]+)(?:\s|$)", command):
        if is_source_path(_strip_shell_quotes(m.group(1))):
            return True

    if re.search(r"\b(?:python3?|node|ruby|perl)\s+-[ce]\s", command):
        if ("open(" in command or "writeFile" in command or
                "File.write" in command or "File.open" in command):
            return True

    for m in re.finditer(r"\b(?:curl\s+-o|wget\s+-O)\s+([^\s;|&]+)", command):
        if is_source_path(_strip_shell_quotes(m.group(1))):
            return True

    if re.search(r"\bdd\b.*of=", command):
        return True
    for m in re.finditer(r"\b(?:patch|rsync)\b.*\s([^\s;|&]+)(?:\s|$)", command):
        if is_source_path(_strip_shell_quotes(m.group(1))):
            return True

    return False


def deny(reason):
    """Create a preToolUse deny result."""
    return {"permissionDecision": "deny", "permissionDecisionReason": reason}


def info(message):
    """Create an informational message result."""
    return {"message": message}
