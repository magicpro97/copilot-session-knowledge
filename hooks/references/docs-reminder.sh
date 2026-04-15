#!/bin/bash
# docs-reminder.sh — TEMPLATE
#
# postToolUse hook that counts code/config file edits and warns when
# documentation hasn't been updated alongside code changes.
# Warns after 3+ code edits without any doc file edit. Resets on doc edit.
#
# Cross-platform: use docs-reminder.py for Windows (no bash required).
# Customize DOC_PATTERNS and CODE_PATTERNS for your project.
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

STATE_FILE="/tmp/copilot-docs-tracker"

# Track doc edits
if echo "$FILE_PATH" | grep -qiE '(README|AGENTS|WORKFLOW|SKILL|copilot-instructions|copilot-rules)\.md$'; then
    echo "docs_updated=true" > "$STATE_FILE"
    exit 0
fi

# Track code edits that likely need doc updates
NEEDS_DOCS=""
if echo "$FILE_PATH" | grep -qE '\.copilot/tools/.*\.(py|sh)$'; then
    NEEDS_DOCS="tools"
elif echo "$FILE_PATH" | grep -qE '\.github/(hooks|agents|skills)/.*\.(sh|md|json)$'; then
    NEEDS_DOCS="hooks/agents"
elif echo "$FILE_PATH" | grep -qE '\.github/(WORKFLOW|copilot-rules)\.md$'; then
    NEEDS_DOCS="workflow"
fi

if [ -z "$NEEDS_DOCS" ]; then
    exit 0
fi

# Count code edits without doc update
CODE_COUNT=0
DOCS_UPDATED=0
if [ -f "$STATE_FILE" ]; then
    DOCS_UPDATED=$(grep -c 'docs_updated=true' "$STATE_FILE" 2>/dev/null) || true
    CODE_COUNT=$(grep -c 'code_edit' "$STATE_FILE" 2>/dev/null) || true
    # Ensure numeric
    DOCS_UPDATED=${DOCS_UPDATED:-0}
    CODE_COUNT=${CODE_COUNT:-0}
fi

echo "code_edit" >> "$STATE_FILE"
CODE_COUNT=$((CODE_COUNT + 1))

# Warn after 3+ code edits without docs
if [ "$CODE_COUNT" -ge 3 ] && [ "$DOCS_UPDATED" -eq 0 ]; then
    FILENAME=$(basename "$FILE_PATH")
    echo ""
    echo "  📝 DOCS REMINDER: $CODE_COUNT $NEEDS_DOCS files changed, no docs updated yet."
    echo "  Check if README.md, AGENTS.md, or SKILL.md need updates."
    echo ""
    # Reset counter so it reminds again after 3 more
    echo "" > "$STATE_FILE"
fi

exit 0
