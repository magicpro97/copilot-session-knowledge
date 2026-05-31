"""PostToolUse hook: emit scoped mini-briefing when git commit completes.

Triggers on ``postToolUse`` for the ``bash`` tool when the command contains
``git commit``.  Queries ``knowledge_entries`` for entries whose
``code_location`` or ``content`` references the files changed in that commit,
then emits a lean ``additionalContext`` block so the LLM sees relevant past
mistakes and patterns.

Fail-open: any exception returns ``None`` to keep the dispatch chain unblocked.
Silent when no relevant entries are found (no noise).
"""

import re
import sqlite3
import subprocess
from pathlib import Path

from . import Rule
from .common import TOOLS_DIR, context

DB_PATH = TOOLS_DIR / "knowledge.db"
_MAX_ENTRIES = 8
_GIT_COMMIT_RE = re.compile(r"\bgit\s+commit\b")


def _get_changed_files() -> list[str]:
    """Return file paths changed in the most recent commit."""
    try:
        result = subprocess.run(
            ["git", "diff", "HEAD~1..HEAD", "--name-only"],
            capture_output=True,
            text=True,
            timeout=10,
        )
        if result.returncode == 0 and result.stdout.strip():
            return [f.strip() for f in result.stdout.splitlines() if f.strip()]
    except Exception:
        pass
    return []


def _get_entries_for_files(db: sqlite3.Connection, file_paths: list[str]) -> list[dict]:
    """Query knowledge_entries matching changed files (by path or basename)."""
    entries: list[dict] = []
    seen_ids: set[int] = set()
    for fp in file_paths[:10]:
        basename = Path(fp).name
        rows = db.execute(
            "SELECT id, category, title, substr(content, 1, 200) FROM knowledge_entries "
            "WHERE (code_location LIKE ? OR content LIKE ?) "
            "AND category IN ('mistake', 'pattern', 'decision') "
            "ORDER BY confidence DESC, last_seen DESC LIMIT 3",
            (f"%{fp}%", f"%{basename}%"),
        ).fetchall()
        for row in rows:
            if row[0] not in seen_ids:
                seen_ids.add(row[0])
                entries.append(
                    {
                        "id": row[0],
                        "category": row[1],
                        "title": row[2],
                        "content": row[3],
                    }
                )
    return entries[:_MAX_ENTRIES]


class PostCommitBriefingRule(Rule):
    """Emit a scoped mini-briefing via additionalContext after a git commit."""

    name = "post-commit-briefing"
    events = ["postToolUse"]
    tools = ["bash"]

    def evaluate(self, event, data):  # noqa: ARG002
        try:
            tool_args = data.get("toolArgs", {})
            if not isinstance(tool_args, dict):
                return None

            cmd = tool_args.get("command", tool_args.get("cmd", ""))
            if not isinstance(cmd, str) or not _GIT_COMMIT_RE.search(cmd):
                return None

            changed_files = _get_changed_files()
            if not changed_files:
                return None

            if not DB_PATH.exists():
                return None

            with sqlite3.connect(DB_PATH) as db:
                entries = _get_entries_for_files(db, changed_files)

            if not entries:
                return None

            # Build a lean briefing label
            names = [Path(f).name for f in changed_files[:3]]
            files_label = ", ".join(names)
            if len(changed_files) > 3:
                files_label += f" +{len(changed_files) - 3} more"

            lines = [f"📚 sk post-commit briefing ({files_label}):"]
            for e in entries:
                emoji = "⚠️" if e["category"] == "mistake" else "✅"
                lines.append(f"  {emoji} [{e['category']}] #{e['id']} — {e['title'][:60]}")

            return context("\n".join(lines))
        except Exception:
            return None
