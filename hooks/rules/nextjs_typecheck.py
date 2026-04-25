"""nextjs_typecheck.py — postToolUse: suggest typecheck after TS edits."""
import os
import sys

if os.name == "nt":
    for _s in (sys.stdout, sys.stderr):
        if hasattr(_s, "reconfigure"):
            _s.reconfigure(encoding="utf-8", errors="replace")

from . import Rule
from .common import MARKERS_DIR, info

TS_EDIT_COUNTER = MARKERS_DIR / "ts-edit-count"


class NextjsTypecheckRule(Rule):
    """Remind to run typecheck after browse-ui TS edits."""

    name = "nextjs-typecheck-reminder"
    events = ["postToolUse"]
    tools = ["edit", "create"]

    def evaluate(self, event, data):
        tool_args = data.get("toolArgs", {})
        if not isinstance(tool_args, dict):
            return None

        file_path = tool_args.get("path", "")
        if not file_path:
            return None

        if not any(file_path.endswith(ext) for ext in (".ts", ".tsx")):
            return None
        if "browse-ui/" not in file_path:
            return None

        # Increment counter
        count = 0
        try:
            if TS_EDIT_COUNTER.is_file():
                count = int(TS_EDIT_COUNTER.read_text().strip())
        except (ValueError, OSError):
            pass
        count += 1
        try:
            MARKERS_DIR.mkdir(parents=True, exist_ok=True)
            TS_EDIT_COUNTER.write_text(str(count))
        except OSError:
            pass

        if count >= 3 and count % 3 == 0:
            return info(
                f"\n  ⚠️ TS REMINDER: {count} browse-ui .ts/.tsx files edited.\n"
                "  Run: cd browse-ui && pnpm typecheck\n"
            )
        return None
