#!/bin/bash
# learn-reminder.sh — postToolUse hook
#
# Remind to record learnings after task_complete is called.
# Helps build the knowledge base with mistakes, patterns, and decisions.
set -euo pipefail

INPUT="$(cat)"
TOOL_NAME=$(echo "$INPUT" | jq -r '.toolName // empty')
RESULT_TYPE=$(echo "$INPUT" | jq -r '.toolResult.resultType // empty')

# Only trigger after task_complete
if [[ "$TOOL_NAME" != "task_complete" ]]; then
    exit 0
fi

if [[ "$RESULT_TYPE" != "success" ]]; then
    exit 0
fi

echo ""
echo "  🧠 LEARN REMINDER: Task completed! Did you learn something?"
echo "  Record mistakes, patterns, or decisions for future sessions:"
echo ""
echo "    python3 ~/.copilot/tools/learn.py"
echo ""

exit 0
