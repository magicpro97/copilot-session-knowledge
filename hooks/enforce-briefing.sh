#!/bin/bash
# enforce-briefing.sh — preToolUse hook
#
# Block edit/create until briefing.py has been run in this session.
# Uses a state file to track whether briefing was completed.
# Resets each session (tmp file cleared on reboot or session start).
set -euo pipefail

INPUT="$(cat)"
TOOL_NAME=$(echo "$INPUT" | jq -r '.toolName // empty')

# Only gate edit and create operations
if [[ "$TOOL_NAME" != "edit" && "$TOOL_NAME" != "create" ]]; then
    exit 0
fi

STATE_FILE="/tmp/copilot-briefing-done-$$"

# Check if auto-briefing already ran (set by auto-briefing.sh via shared marker)
MARKER="/tmp/copilot-briefing-done"
if [ -f "$MARKER" ]; then
    exit 0
fi

# Check if briefing.py was run manually in this session
if [ -f "$STATE_FILE" ]; then
    exit 0
fi

# Check recent briefing runs (within last 30 minutes)
TOOLS_DIR="$(cd "$(dirname "$0")/.." && pwd)"
if [ -f "$TOOLS_DIR/briefing.py" ]; then
    # Look for briefing output in temp
    RECENT=$(find /tmp -maxdepth 1 -name "copilot-briefing-*" -mmin -30 2>/dev/null | head -1)
    if [ -n "$RECENT" ]; then
        exit 0
    fi
fi

echo ""
echo "  ⚠️  BRIEFING REQUIRED: Run briefing.py before editing code."
echo "  python3 ~/.copilot/tools/briefing.py \"your task\""
echo ""

# Don't block — just warn. Exit 0 so the edit proceeds.
exit 0
