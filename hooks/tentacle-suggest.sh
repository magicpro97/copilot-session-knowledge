#!/bin/bash
# tentacle-suggest.sh — postToolUse hook
#
# Tracks edit/create across different module directories.
# When edits span 3+ files across 2+ distinct modules, suggests
# the tentacle-orchestration skill for parallel multi-agent work.
set -euo pipefail

INPUT="$(cat)"
TOOL_NAME=$(echo "$INPUT" | jq -r '.toolName // empty')
RESULT_TYPE=$(echo "$INPUT" | jq -r '.toolResult.resultType // empty')

# Only track successful edit/create
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
if [ -z "$FILE_PATH" ]; then
    exit 0
fi

# Skip non-code files (docs, configs, markdown)
if echo "$FILE_PATH" | grep -qE '\.(md|txt|json|yaml|yml|toml|lock)$'; then
    exit 0
fi

STATE_FILE="/tmp/copilot-tentacle-edits"
SUGGESTED="/tmp/copilot-tentacle-suggested"

# Don't suggest more than once per session
if [ -f "$SUGGESTED" ]; then
    exit 0
fi

# Extract module directory (2 levels up from filename for typical project structures)
# e.g. /Users/x/Project/src/auth/service.kt → src/auth
# e.g. /Users/x/Project/presentation/alarm/AlarmScreen.kt → presentation/alarm
MODULE_DIR=$(dirname "$FILE_PATH" | rev | cut -d'/' -f1-2 | rev)

# Append to state file: one line per edit (path\tmodule)
echo -e "${FILE_PATH}\t${MODULE_DIR}" >> "$STATE_FILE"

# Count unique files and unique modules
TOTAL_FILES=$(cut -f1 "$STATE_FILE" | sort -u | wc -l | tr -d ' ')
UNIQUE_MODULES=$(cut -f2 "$STATE_FILE" | sort -u | wc -l | tr -d ' ')

# Trigger: 3+ files across 2+ modules
if [ "$TOTAL_FILES" -ge 3 ] && [ "$UNIQUE_MODULES" -ge 2 ]; then
    MODULES_LIST=$(cut -f2 "$STATE_FILE" | sort -u | head -5 | tr '\n' ', ' | sed 's/,$//')
    touch "$SUGGESTED"
    echo ""
    echo "  🐙 TENTACLE SUGGESTION: ${TOTAL_FILES} files edited across ${UNIQUE_MODULES} modules"
    echo "  Modules: ${MODULES_LIST}"
    echo "  Consider using tentacle-orchestration for parallel multi-agent work:"
    echo "    → Invoke skill: tentacle-orchestration"
    echo "    → Or decompose manually with sub-agents per module"
    echo ""
fi

exit 0
