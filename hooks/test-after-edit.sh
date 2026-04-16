#!/bin/bash
# test-after-edit.sh — postToolUse hook
#
# Tracks .py file edits and reminds to run tests after 3+ edits.
# Specific to copilot-session-knowledge Python tools.
set -euo pipefail

INPUT="$(cat)"
TOOL_NAME=$(echo "$INPUT" | jq -r '.toolName // empty')
RESULT_TYPE=$(echo "$INPUT" | jq -r '.toolResult.resultType // empty')

# Only check successful edit/create operations
if [[ "$RESULT_TYPE" != "success" ]]; then
    exit 0
fi
if [[ "$TOOL_NAME" != "edit" && "$TOOL_NAME" != "create" ]]; then
    exit 0
fi

TOOL_ARGS_RAW=$(echo "$INPUT" | jq -r '.toolArgs // empty')
if ! echo "$TOOL_ARGS_RAW" | jq -e . >/dev/null 2>&1; then
    exit 0
fi

FILE_PATH=$(echo "$TOOL_ARGS_RAW" | jq -r '.path // empty')

# Only track Python files in the tools directory
if ! echo "$FILE_PATH" | grep -qE '\.copilot/tools/.*\.py$'; then
    exit 0
fi

# Skip test files themselves
if echo "$FILE_PATH" | grep -qE 'test_.*\.py$'; then
    exit 0
fi

STATE_FILE="/tmp/copilot-py-edit-count"
TESTS_RAN="/tmp/copilot-tests-ran"

# Reset if tests were recently run
if [ -f "$TESTS_RAN" ]; then
    RECENT=$(find /tmp -maxdepth 1 -name "copilot-tests-ran" -mmin -10 2>/dev/null | head -1)
    if [ -n "$RECENT" ]; then
        exit 0
    fi
    rm -f "$TESTS_RAN"
fi

# Increment edit counter
COUNT=0
if [ -f "$STATE_FILE" ]; then
    COUNT=$(cat "$STATE_FILE" 2>/dev/null || echo "0")
fi
COUNT=$((COUNT + 1))
echo "$COUNT" > "$STATE_FILE"

# Warn after 3+ edits
if [ "$COUNT" -ge 3 ]; then
    BASENAME=$(basename "$FILE_PATH")
    echo ""
    echo "  🧪 TEST REMINDER: $COUNT Python files edited ($BASENAME, etc.)"
    echo "  Run tests before committing:"
    echo "    python3 test_security.py && python3 test_fixes.py"
    echo ""
    # Reset counter after warning
    echo "0" > "$STATE_FILE"
fi

exit 0
