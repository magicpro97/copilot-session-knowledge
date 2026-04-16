#!/bin/bash
# error-search-kb.sh — errorOccurred hook
#
# When an error occurs, auto-search the knowledge base for solutions.
# Surfaces past fixes so the agent doesn't debug from scratch.
set -euo pipefail

INPUT="$(cat)"
ERROR_MSG=$(echo "$INPUT" | jq -r '.error.message // empty')

if [ -z "$ERROR_MSG" ]; then
    exit 0
fi

TOOLS_DIR="${HOME}/.copilot/tools"
QUERY_SCRIPT="${TOOLS_DIR}/query-session.py"

if [ ! -f "$QUERY_SCRIPT" ]; then
    exit 0
fi

# Truncate error message for search (max 100 chars)
SEARCH_TERM=$(echo "$ERROR_MSG" | head -c 100)

# Search knowledge base (timeout 5s via perl, suppress errors)
RESULT=$(perl -e 'alarm 5; exec @ARGV' python3 "$QUERY_SCRIPT" "$SEARCH_TERM" 2>/dev/null | head -5) || true

if [ -n "$RESULT" ] && ! echo "$RESULT" | grep -q "No results"; then
    echo ""
    echo "  🔍 KB MATCH: Found past knowledge about this error:"
    echo "$RESULT" | sed 's/^/  /'
    echo ""
    echo "  Run: python3 ~/.copilot/tools/query-session.py \"$SEARCH_TERM\" --verbose"
    echo ""
fi

exit 0
