#!/bin/bash
# auto-briefing.sh — sessionStart hook
#
# Auto-run briefing.py at session start to surface past mistakes,
# patterns, and decisions relevant to the current working directory.
set -euo pipefail

TOOLS_DIR="$(cd "$(dirname "$0")/.." && pwd)"
BRIEFING="$TOOLS_DIR/briefing.py"

if [ ! -f "$BRIEFING" ]; then
    exit 0
fi

# Get project name from git or cwd
PROJECT=""
if command -v git >/dev/null 2>&1; then
    PROJECT=$(git rev-parse --show-toplevel 2>/dev/null | xargs basename 2>/dev/null || true)
fi
if [ -z "$PROJECT" ]; then
    PROJECT=$(basename "$PWD")
fi

echo ""
echo "  📋 Session briefing for: $PROJECT"
echo "  ─────────────────────────────────"

# Run briefing with project context, timeout after 10s
timeout 10 python3 "$BRIEFING" "$PROJECT" --budget 500 2>/dev/null || true

exit 0
