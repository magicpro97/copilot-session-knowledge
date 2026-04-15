#!/bin/bash
# commit-gate.sh — TEMPLATE
#
# preToolUse hook that blocks git commit until verification requirements are met.
# Customize the VERIFICATION GATES section for your project's needs.
#
# Common gates:
#   - Tests must pass recently
#   - Screenshots taken for UI changes
#   - Lint passes
#   - Security scan clean
set -euo pipefail

INPUT="$(cat)"
TOOL_NAME=$(echo "$INPUT" | jq -r '.toolName // empty')

if [[ "$TOOL_NAME" != "bash" ]]; then
    exit 0
fi

TOOL_ARGS_RAW=$(echo "$INPUT" | jq -r '.toolArgs // empty')
if ! echo "$TOOL_ARGS_RAW" | jq -e . >/dev/null 2>&1; then
    exit 0
fi

COMMAND=$(echo "$TOOL_ARGS_RAW" | jq -r '.command // empty')

# Only check git commit commands
if ! echo "$COMMAND" | grep -qE 'git\s+(commit|.*&&.*git\s+commit)'; then
    exit 0
fi

REPO_ROOT=$(git rev-parse --show-toplevel 2>/dev/null || pwd)
STAGED=$(cd "$REPO_ROOT" && git diff --cached --name-only 2>/dev/null || true)

deny() {
    echo "{\"permissionDecision\":\"deny\",\"permissionDecisionReason\":\"$1\"}"
    exit 0
}

# === VERIFICATION GATES — CUSTOMIZE THESE ===

# Gate 1: Check if source files changed (define what needs verification)
# Example: UI files need screenshot verification
HAS_UI_FILES=$(echo "$STAGED" | grep -cE '\.(tsx|vue|svelte|kt|swift)$' || true)

if [ "$HAS_UI_FILES" -gt 0 ]; then
    # Gate 2: Require recent test run (within 30 min)
    TEST_RESULT_FILE="/tmp/copilot-last-test-pass"
    if [ -f "$TEST_RESULT_FILE" ]; then
        LAST_PASS=$(cat "$TEST_RESULT_FILE")
        NOW=$(date +%s)
        AGE=$((NOW - LAST_PASS))
        if [ "$AGE" -gt 1800 ]; then
            deny "COMMIT BLOCKED: Tests haven't passed in the last 30 minutes. Run tests first."
        fi
    fi
    # Uncomment to require test pass file:
    # else
    #     deny "COMMIT BLOCKED: No test results found. Run tests before committing."
    # fi
fi

# Gate 3: Check for debug artifacts left in code
for FILE in $(echo "$STAGED" | grep -E '\.(ts|js|kt|swift|py|go|rs)$' || true); do
    FULL_PATH="$REPO_ROOT/$FILE"
    if [ -f "$FULL_PATH" ]; then
        if grep -qE '(console\.log|print\(.*DEBUG|debugger;|TODO.*REMOVE)' "$FULL_PATH" 2>/dev/null; then
            deny "COMMIT BLOCKED: Debug artifacts found in $FILE. Remove console.log/debugger/TODO:REMOVE."
        fi
    fi
done

# Gate 4: Ensure no large files
for FILE in $(echo "$STAGED" || true); do
    FULL_PATH="$REPO_ROOT/$FILE"
    if [ -f "$FULL_PATH" ]; then
        SIZE=$(wc -c < "$FULL_PATH" | tr -d ' ')
        if [ "$SIZE" -gt 1048576 ]; then  # 1MB
            deny "COMMIT BLOCKED: $FILE is $(( SIZE / 1024 ))KB. Large files should use Git LFS."
        fi
    fi
done

exit 0
