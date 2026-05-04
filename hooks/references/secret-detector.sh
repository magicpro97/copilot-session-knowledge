#!/bin/bash
# secret-detector.sh — TEMPLATE
#
# preToolUse hook that blocks hardcoded secrets in edit/create operations.
# Applicable to EVERY project. Customize patterns for your stack.
#
# Checks: cloud provider keys, API tokens, private keys, JWTs.
# Excludes: placeholder values (example, TODO, REPLACE_ME, xxx).
set -euo pipefail

INPUT="$(cat)"
TOOL_NAME=$(echo "$INPUT" | jq -r '.toolName // empty')

# Only check edit/create tools
if [[ "$TOOL_NAME" != "edit" && "$TOOL_NAME" != "create" ]]; then
    exit 0
fi

TOOL_ARGS_RAW=$(echo "$INPUT" | jq -r '.toolArgs // empty')
if ! echo "$TOOL_ARGS_RAW" | jq -e . >/dev/null 2>&1; then
    exit 0
fi

CONTENT=$(echo "$TOOL_ARGS_RAW" | jq -r '.file_text // .new_str // empty')
if [ -z "$CONTENT" ]; then
    exit 0
fi

deny() {
    echo "{\"permissionDecision\":\"deny\",\"permissionDecisionReason\":\"Potential secret detected ($1). Use environment variables instead of hardcoding.\"}"
    exit 0
}

check_secret() {
    local name="$1"
    local pattern="$2"
    if echo "$CONTENT" | grep -qE -- "$pattern"; then
        if echo "$CONTENT" | grep -E -- "$pattern" | grep -qEi '(example|placeholder|TODO|REPLACE_ME|your[_-]|xxx|000)'; then
            return 0
        fi
        deny "$name"
    fi
}

# === CUSTOMIZE PATTERNS FOR YOUR STACK ===
# Cloud providers
check_secret "AWS Access Key" 'AK[[:upper:]]{2}[0-9A-Z]{16}'
# AWS Secret Access Key — requires assignment context to avoid false positives on
# camelCase/PascalCase identifiers (e.g. checkAndSendAmbulanceApproachNotification).
# IMPORTANT: do NOT use a raw character-class+length-only pattern here — it matches
# ordinary long identifiers and produces workflow-blocking false positives (issue #20).
check_secret "AWS Secret Key" '(AWS_SECRET_ACCESS_KEY|aws_secret_access_key|secretAccessKey|SecretAccessKey|secret_access_key)[[:space:]]*[=:][[:space:]]*[A-Za-z0-9/+]{40}'
check_secret "Google API Key" 'AIza[0-9A-Za-z_-]{35}'

# Version control / CI tokens
check_secret "GitHub Token" 'gh[pousr]_[A-Za-z0-9_]{20,}'

# Cryptographic material
check_secret "Private Key" '-----BEGIN.*PRIVATE KEY'

# JWTs
check_secret "JWT Token" 'eyJ[A-Za-z0-9_-]{10,}\.eyJ[A-Za-z0-9_-]{10,}\.'

# === PROJECT-SPECIFIC: Add patterns below ===
# check_secret "Stripe Key" 'sk_live_[0-9a-zA-Z]{24}'
# check_secret "Slack Token" 'xox[bprs]-[0-9a-zA-Z]{10,}'

exit 0
