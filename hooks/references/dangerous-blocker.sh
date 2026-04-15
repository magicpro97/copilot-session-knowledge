#!/bin/bash
# dangerous-blocker.sh — TEMPLATE
#
# preToolUse hook that blocks dangerous bash commands.
# Applicable to EVERY project. Install as-is.
#
# Blocks: privilege escalation, destructive filesystem ops, disk formatting,
# download-and-execute, dangerous git operations, database drops.
set -euo pipefail

INPUT="$(cat)"
TOOL_NAME=$(echo "$INPUT" | jq -r '.toolName // empty')

if [ "$TOOL_NAME" != "bash" ]; then
    exit 0
fi

TOOL_ARGS_RAW=$(echo "$INPUT" | jq -r '.toolArgs // empty')
if ! echo "$TOOL_ARGS_RAW" | jq -e . >/dev/null 2>&1; then
    exit 0
fi

COMMAND=$(echo "$TOOL_ARGS_RAW" | jq -r '.command // empty')

deny() {
    echo "{\"permissionDecision\":\"deny\",\"permissionDecisionReason\":\"$1\"}"
    exit 0
}

# Privilege escalation
echo "$COMMAND" | grep -qE '\b(sudo|su|runas)\b' && deny "Privilege escalation not allowed"

# Destructive filesystem
echo "$COMMAND" | grep -qE 'rm\s+-rf\s*/($|\s)' && deny "Destructive: rm -rf on root"

# Disk operations
echo "$COMMAND" | grep -qE '\b(mkfs|diskpart)\b' && deny "Disk operations not allowed"

# Download-and-execute
echo "$COMMAND" | grep -qE 'curl.*\|\s*(bash|sh)' && deny "Download-and-execute blocked"
echo "$COMMAND" | grep -qE 'wget.*\|\s*(bash|sh)' && deny "Download-and-execute blocked"

# Dangerous git operations
echo "$COMMAND" | grep -qE 'git\s+push\s+.*--force\b' && deny "Force push blocked — use --force-with-lease"
echo "$COMMAND" | grep -qE 'git\s+reset\s+--hard\s+HEAD~[2-9]' && deny "Hard reset of multiple commits blocked"

# Database destruction
echo "$COMMAND" | grep -qEi 'DROP\s+(TABLE|DATABASE)' && deny "Database drop operation blocked"

exit 0
