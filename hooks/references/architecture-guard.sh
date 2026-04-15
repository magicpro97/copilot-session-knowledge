#!/bin/bash
# architecture-guard.sh — TEMPLATE
#
# preToolUse hook that enforces architectural layer boundaries.
# Customize LAYER RULES section for your project's architecture.
#
# Common patterns:
#   - Clean Architecture: presentation → domain ← data (domain has no outward deps)
#   - Hexagonal: adapters → ports ← core
#   - MVC: views → controllers → models (no reverse deps)
#   - KMP: commonMain cannot import platform-specific packages
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

FILE_PATH=$(echo "$TOOL_ARGS_RAW" | jq -r '.path // empty')
CONTENT=$(echo "$TOOL_ARGS_RAW" | jq -r '.file_text // .new_str // empty')

deny() {
    echo "{\"permissionDecision\":\"deny\",\"permissionDecisionReason\":\"Architecture violation: $1\"}"
    exit 0
}

# === LAYER RULES — CUSTOMIZE THESE FOR YOUR PROJECT ===

# Example: Clean Architecture (Kotlin/Java)
# Presentation must not import from data layer
if echo "$FILE_PATH" | grep -q 'presentation/' && echo "$CONTENT" | grep -qE 'import.*\.data\.'; then
    deny "presentation layer must not import from data layer."
fi

# Domain must not import platform/framework code
if echo "$FILE_PATH" | grep -q 'domain/' && echo "$CONTENT" | grep -qE 'import (android|javax|spring|express)\.'; then
    deny "domain must not depend on platform-specific or framework code."
fi

# Example: KMP — commonMain must not import platform packages
# if echo "$FILE_PATH" | grep -q 'commonMain/'; then
#     if echo "$CONTENT" | grep -qE 'import android\.(app|content|os|widget)\.'; then
#         deny "commonMain must not import android.* packages."
#     fi
#     if echo "$CONTENT" | grep -qE 'import (java\.awt|javax\.swing|UIKit|AppKit)\.'; then
#         deny "commonMain must not import platform-specific packages."
#     fi
# fi

# Example: Frontend — components should not import API clients directly
# if echo "$FILE_PATH" | grep -qE 'components/.*\.(tsx|vue|svelte)$'; then
#     if echo "$CONTENT" | grep -qE 'import.*from.*["\x27](axios|fetch|api/)'; then
#         deny "UI components should not import API clients directly. Use hooks/stores."
#     fi
# fi

# Example: Backend — routes should not contain business logic
# if echo "$FILE_PATH" | grep -qE 'routes/.*\.(ts|js)$'; then
#     if echo "$CONTENT" | grep -qE 'prisma\.|\.findMany|\.create\(|\.update\('; then
#         deny "Routes should not contain database queries. Use service layer."
#     fi
# fi

exit 0
