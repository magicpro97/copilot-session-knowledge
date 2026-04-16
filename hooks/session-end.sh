#!/bin/bash
# session-end.sh — sessionEnd hook
#
# Cleanup temporary marker files when a session ends.
# Prevents stale markers from affecting future sessions.
set -euo pipefail

INPUT="$(cat)"
REASON=$(echo "$INPUT" | jq -r '.reason // empty')

# Cleanup session markers
rm -f /tmp/copilot-briefing-done
rm -f /tmp/copilot-briefing-done-*
rm -f /tmp/copilot-py-edit-count
rm -f /tmp/copilot-tests-ran
rm -f /tmp/copilot-docs-tracker

# Log session end
echo "Session ended: ${REASON:-unknown}" >> /tmp/copilot-session.log 2>/dev/null || true

exit 0
