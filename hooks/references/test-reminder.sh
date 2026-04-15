#!/bin/bash
# test-reminder.sh — TEMPLATE
#
# postToolUse hook that reminds to write tests when creating new source files.
# Customize the FILE_PATTERN and TEST_DIR for your project structure.
set -euo pipefail

INPUT="$(cat)"
TOOL_NAME=$(echo "$INPUT" | jq -r '.toolName // empty')
RESULT_TYPE=$(echo "$INPUT" | jq -r '.toolResult.resultType // empty')

# Only check successful create operations
if [[ "$TOOL_NAME" != "create" || "$RESULT_TYPE" != "success" ]]; then
    exit 0
fi

TOOL_ARGS_RAW=$(echo "$INPUT" | jq -r '.toolArgs // empty')
if ! echo "$TOOL_ARGS_RAW" | jq -e . >/dev/null 2>&1; then
    exit 0
fi

FILE_PATH=$(echo "$TOOL_ARGS_RAW" | jq -r '.path // empty')

# === CUSTOMIZE: Map source patterns to test suggestions ===
NEEDS_TEST=""

# Kotlin/Java examples
if echo "$FILE_PATH" | grep -qE 'domain/usecase/.*\.kt$'; then
    NEEDS_TEST="Use case"
elif echo "$FILE_PATH" | grep -qE 'data/repository/.*\.kt$'; then
    NEEDS_TEST="Repository"
elif echo "$FILE_PATH" | grep -qE 'presentation/.*ViewModel\.kt$'; then
    NEEDS_TEST="ViewModel"
fi

# TypeScript/JavaScript examples (uncomment for web projects)
# if echo "$FILE_PATH" | grep -qE 'services/.*\.(ts|js)$'; then
#     NEEDS_TEST="Service"
# elif echo "$FILE_PATH" | grep -qE 'hooks/.*\.(ts|js)$'; then
#     NEEDS_TEST="Hook"
# elif echo "$FILE_PATH" | grep -qE 'utils/.*\.(ts|js)$'; then
#     NEEDS_TEST="Utility"
# fi

# Python examples (uncomment for Python projects)
# if echo "$FILE_PATH" | grep -qE 'services/.*\.py$'; then
#     NEEDS_TEST="Service"
# elif echo "$FILE_PATH" | grep -qE 'models/.*\.py$'; then
#     NEEDS_TEST="Model"
# fi

if [ -n "$NEEDS_TEST" ]; then
    FILENAME=$(basename "$FILE_PATH" | sed 's/\.[^.]*$//')
    echo ""
    echo "  TEST REMINDER: New $NEEDS_TEST created ($FILENAME)"
    echo "  Consider adding: .../${FILENAME}Test.* or .../${FILENAME}.test.*"
    echo ""
fi

exit 0
