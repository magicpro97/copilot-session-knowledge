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

git_push_args() {
    # Inspect only the arguments that belong to `git push`.
    # Wrapper options such as `powershell.exe -NoProfile` must not be treated
    # as git push flags.
    echo "$COMMAND" | sed -nE "s/.*(^|[;&|\"'[:space:]])git[[:space:]]+push([[:space:]\"']|\$)//p" | head -n1
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

# Dangerous git operations — inspect only `git push` args; allow --force-with-lease
if echo "$COMMAND" | grep -qE "(^|[;&|\"'[:space:]])git[[:space:]]+push([[:space:]\"']|\$)"; then
    PUSH_ARGS="$(git_push_args)"
    PUSH_ARGS_FOR_CHECK="$(echo "$PUSH_ARGS" | sed "s/[\"']/ /g")"
    echo "$PUSH_ARGS_FOR_CHECK" | grep -qE '(^|[[:space:]])--force([=[:space:]]|$)' && deny "Force push blocked — use --force-with-lease"
    echo "$PUSH_ARGS_FOR_CHECK" | grep -qE '(^|[[:space:]])-[^-[:space:]]*f[^[:space:]]*([[:space:]]|$)' && deny "Force push blocked — use --force-with-lease"
    echo "$PUSH_ARGS_FOR_CHECK" | grep -qE '(^|[[:space:]])\+[^[:space:]]+' && deny "Force push via +refspec blocked — use --force-with-lease"
fi
echo "$COMMAND" | grep -qE 'git\s+reset\s+--hard\s+HEAD~[2-9]' && deny "Hard reset of multiple commits blocked"

# Database destruction
echo "$COMMAND" | grep -qEi 'DROP\s+(TABLE|DATABASE)' && deny "Database drop operation blocked"

exit 0
