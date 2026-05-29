#!/usr/bin/env sh
# Example sk retry-listener hook — append retry events to a local log file.
#
# Install: copy to ~/.copilot/hooks/sk-retry-listener.sh and chmod +x it.
# Or set SK_RETRY_LISTENER=/path/to/this/script.
#
# The payload JSON is sent on stdin; you may also respond on stdout with:
#   {"abort": true}                       — abort the retry sequence
#   {"delay_override_seconds": 5.0}       — override the computed delay
# Anything else (or empty stdout) is treated as "observe" (no change).

PAYLOAD=$(cat)
echo "$PAYLOAD" >> "${HOME}/.copilot/retry-events.log"

# Optionally: post to Slack webhook
# curl -s -X POST "$SLACK_WEBHOOK" \
#   -H "Content-Type: application/json" \
#   -d "{\"text\":\"sk retry: $PAYLOAD\"}" >/dev/null 2>&1
