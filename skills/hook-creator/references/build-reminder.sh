#!/bin/bash
# build-reminder.sh — TEMPLATE
#
# postToolUse hook that counts source file edits and reminds to verify build.
# Customize the FILE_EXTENSION and BUILD_COMMAND for your project.
set -euo pipefail

INPUT="$(cat)"
TOOL_NAME=$(echo "$INPUT" | jq -r '.toolName // empty')
RESULT_TYPE=$(echo "$INPUT" | jq -r '.toolResult.resultType // empty')

if [[ "$TOOL_NAME" != "edit" || "$RESULT_TYPE" != "success" ]]; then
    exit 0
fi

TOOL_ARGS_RAW=$(echo "$INPUT" | jq -r '.toolArgs // empty')
if ! echo "$TOOL_ARGS_RAW" | jq -e . >/dev/null 2>&1; then
    exit 0
fi

FILE_PATH=$(echo "$TOOL_ARGS_RAW" | jq -r '.path // empty')

# === CUSTOMIZE: File extension and build command ===
FILE_EXTENSION='\.kt$'         # Change to: \.ts$, \.rs$, \.go$, \.py$, etc.
BUILD_COMMAND="./gradlew build" # Change to: npm run build, cargo check, go build, etc.
REMIND_EVERY=10                 # Remind after every N edits

if echo "$FILE_PATH" | grep -qE "$FILE_EXTENSION"; then
    EDITS_FILE="/tmp/copilot-source-edits-count"
    COUNT=0
    if [ -f "$EDITS_FILE" ]; then
        COUNT=$(cat "$EDITS_FILE")
    fi
    COUNT=$((COUNT + 1))
    echo "$COUNT" > "$EDITS_FILE"

    if [ $((COUNT % REMIND_EVERY)) -eq 0 ]; then
        echo ""
        echo "  BUILD CHECK: $COUNT source files edited since last reminder."
        echo "  Consider running: $BUILD_COMMAND"
        echo ""
    fi
fi

exit 0
