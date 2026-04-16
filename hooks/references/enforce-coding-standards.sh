#!/bin/bash
# enforce-coding-standards.sh — TEMPLATE
#
# preToolUse hook that enforces project coding standards.
# Denies edit/create operations that violate configured rules.
#
# DESIGN:
#   Two-tier detection:
#     1. Fast regex checks (~5ms) — always runs
#     2. Optional linter integration (~200ms-2s) — runs if configured
#
#   The regex tier catches common violations instantly.
#   The linter tier runs the project's ACTUAL linter on the snippet for
#   deeper, AST-level analysis (catches patterns regex can't).
#
# CUSTOMIZATION (3 steps):
#   1. Set FILE_EXTENSIONS for your language(s)
#   2. Edit REGEX RULES section — add/remove patterns
#   3. (Optional) Configure LINTER INTEGRATION for AST-level checks
#
# EXAMPLES PER LANGUAGE:
#
#   TypeScript/JS:
#     FILE_EXTENSIONS="ts|tsx|js|jsx"
#     Rules: no !!, no lodash, compositeKeys(), no ?? null
#     Linter: eslint --no-eslintrc --rule '{...}' or espree AST
#
#   Python:
#     FILE_EXTENSIONS="py"
#     Rules: no print(), no import *, type hints required
#     Linter: ruff check --select E,W,I or flake8
#
#   Go:
#     FILE_EXTENSIONS="go"
#     Rules: no fmt.Println, error handling
#     Linter: staticcheck or golangci-lint
#
#   Kotlin/Java:
#     FILE_EXTENSIONS="kt|java"
#     Rules: no System.out, no wildcard imports
#     Linter: ktlint or checkstyle
#
# SPEED: regex ~5ms, linter ~200ms-2s (within 10s timeout)
set -euo pipefail

INPUT="$(cat)"
TOOL_NAME=$(echo "$INPUT" | jq -r '.toolName // empty')

if [[ "$TOOL_NAME" != "edit" && "$TOOL_NAME" != "create" ]]; then
  exit 0
fi

TOOL_ARGS=$(echo "$INPUT" | jq -r '.toolArgs // empty')
if ! echo "$TOOL_ARGS" | jq -e . >/dev/null 2>&1; then
  exit 0
fi

FILE_PATH=$(echo "$TOOL_ARGS" | jq -r '.path // empty')
NEW_STR=$(echo "$TOOL_ARGS" | jq -r '.new_str // .file_text // empty')

# ══════════════════════════════════════════════════════
# CONFIGURATION — customize for your project
# ══════════════════════════════════════════════════════

# File extensions to check (pipe-separated regex)
FILE_EXTENSIONS="ts|tsx|js|jsx"

# File patterns to SKIP (test files, mocks, generated code)
SKIP_PATTERNS="__tests__|__mocks__|\.test\.|\.spec\.|\.generated\.|\.g\."

# ══════════════════════════════════════════════════════

# Check file extension
if ! echo "$FILE_PATH" | grep -qE "\.($FILE_EXTENSIONS)$"; then
  exit 0
fi

# Skip excluded files
if echo "$FILE_PATH" | grep -qE "$SKIP_PATTERNS"; then
  exit 0
fi

[ -z "$NEW_STR" ] && exit 0

deny() {
  echo "{\"permissionDecision\":\"deny\",\"permissionDecisionReason\":\"$1\"}"
  exit 0
}

# ══════════════════════════════════════════════════════
# TIER 1: REGEX RULES — fast, ~5ms total
# ══════════════════════════════════════════════════════
# Each rule: pattern to match, deny message.
# Delete/add rules for your project. Regex uses grep -P (PCRE).
#
# Format:
#   echo "$NEW_STR" | grep -qP 'PATTERN' 2>/dev/null && \
#     deny "Message explaining the violation and correct alternative."

# --- Rule: Banned imports ---
# Customize: replace the import source and its alternative
# Example (JS/TS): no lodash → use es-toolkit
echo "$NEW_STR" | grep -qP "from\s+['\"]lodash|require\(['\"]lodash" 2>/dev/null && \
  deny "Coding standard: Use es-toolkit instead of lodash."

# Example (JS/TS): no moment → use date-fns
echo "$NEW_STR" | grep -qP "from\s+['\"]moment|require\(['\"]moment" 2>/dev/null && \
  deny "Coding standard: Use date-fns or native Date instead of moment.js."

# Example (Python): no import *
# echo "$NEW_STR" | grep -qP '^\s*from\s+\S+\s+import\s+\*' 2>/dev/null && \
#   deny "Coding standard: Do not use wildcard imports (from x import *)."

# Example (Go): no fmt.Println in production
# echo "$NEW_STR" | grep -qP '\bfmt\.Println\b' 2>/dev/null && \
#   deny "Coding standard: Use structured logging (log.Info) instead of fmt.Println."

# --- Rule: Banned patterns ---
# Example (JS/TS): no !! for null checks → use isNotNil()
echo "$NEW_STR" | grep -qP '(?<!=)!!(?=[a-zA-Z_$\(])' 2>/dev/null && \
  deny "Coding standard: Use isNotNil() instead of !! for null checks (!!0 is false)."

# Example (JS/TS): no ?? null
echo "$NEW_STR" | grep -qP '\?\?\s*null\b' 2>/dev/null && \
  deny "Coding standard: Let undefined remain undefined. Do not use ?? null."

# Example (Python): no bare except
# echo "$NEW_STR" | grep -qP '^\s*except\s*:' 2>/dev/null && \
#   deny "Coding standard: Do not use bare except:. Catch specific exceptions."

# Example (Java/Kotlin): no System.out
# echo "$NEW_STR" | grep -qP '\bSystem\.(out|err)\.print' 2>/dev/null && \
#   deny "Coding standard: Use logger instead of System.out."

# --- Rule: Key construction (DynamoDB / composite keys) ---
# Example: no manual # concatenation → use compositeKeys() utility
# Regex tier: catches string concat 'X' + '#' + 'Y'
echo "$NEW_STR" | grep -qP "(pk|sk|PK|SK|partitionKey|sortKey)\s*[:=].*(\+\s*['\"]#|['\"]#['\"]?\s*\+)" 2>/dev/null && \
  deny "Coding standard: Use compositeKeys() instead of manual # concatenation for keys."

# Template literal tier: catches `X#${Y}` (regex can detect backtick patterns)
echo "$NEW_STR" | grep -qP '`[^`]*[A-Z_]+#\$\{' 2>/dev/null && \
  deny "Coding standard: Use compositeKeys() instead of template literal with # for keys."

# ══════════════════════════════════════════════════════
# TIER 2: LINTER INTEGRATION — AST-level, ~200ms-2s
# ══════════════════════════════════════════════════════
# Uncomment and configure ONE linter block for your project.
# The linter runs on a temp file containing NEW_STR.
# If linter finds errors → deny. If linter passes → allow.
#
# Benefits over regex:
#   - Catches patterns regex can't (template literals, AST structure)
#   - Uses your project's actual lint rules
#   - Understands language syntax (not just text matching)

REPO_ROOT=$(git rev-parse --show-toplevel 2>/dev/null || pwd)

# --- Option A: ESLint (TypeScript/JavaScript) ---
# Speed: ~1-2s (full ESLint), ~35ms (espree only)
# Requires: eslint in node_modules
#
# run_eslint_check() {
#   local tmpfile
#   tmpfile=$(mktemp /tmp/hook-lint-XXXXXX.ts)
#   printf '%s' "$NEW_STR" > "$tmpfile"
#   local output
#   output=$(cd "$REPO_ROOT" && npx eslint \
#     --rule '{"no-restricted-syntax": ["error",
#       {"selector": "UnaryExpression[operator=\"!\"] > UnaryExpression[operator=\"!\"]",
#        "message": "Use isNotNil() instead of !!"},
#       {"selector": "CallExpression[callee.name=\"Boolean\"][arguments.length=1]",
#        "message": "Use isNotNil() instead of Boolean()"}
#     ]}' \
#     --no-inline-config --format json "$tmpfile" 2>/dev/null)
#   rm -f "$tmpfile"
#   local errors
#   errors=$(echo "$output" | jq -r '.[0].messages[]? | select(.severity==2) | .message' 2>/dev/null | head -1)
#   if [ -n "$errors" ]; then
#     deny "Lint error: $errors"
#   fi
# }
# run_eslint_check

# --- Option B: espree/acorn AST (JS/TS — lightweight, ~35ms) ---
# Speed: ~35ms. No eslint overhead. Pure AST pattern matching.
# Requires: espree in node_modules (installed with eslint)
#
# run_ast_check() {
#   local tmpfile
#   tmpfile=$(mktemp /tmp/hook-lint-XXXXXX.js)
#   printf '%s' "$NEW_STR" > "$tmpfile"
#   local result
#   result=$(cd "$REPO_ROOT" && node --max-old-space-size=64 -e "
#     const fs = require('fs');
#     const espree = require('espree');
#     const code = fs.readFileSync('$tmpfile','utf8');
#     let ast;
#     try { ast = espree.parse(code, {ecmaVersion:2024,sourceType:'module',loc:true,tolerant:true}); }
#     catch { process.stdout.write('OK'); process.exit(0); }
#     // ... add your AST walk rules here ...
#     process.stdout.write('OK');
#   " 2>/dev/null || echo "OK")
#   rm -f "$tmpfile"
#   if [ "$result" != "OK" ] && [ -n "$result" ]; then
#     deny "AST violation: $result"
#   fi
# }
# run_ast_check

# --- Option C: Ruff (Python — extremely fast, ~50ms) ---
# Speed: ~50ms. Fastest Python linter.
# Requires: ruff installed
#
# run_ruff_check() {
#   local tmpfile
#   tmpfile=$(mktemp /tmp/hook-lint-XXXXXX.py)
#   printf '%s' "$NEW_STR" > "$tmpfile"
#   local output
#   output=$(ruff check --select E,W,I --output-format text "$tmpfile" 2>/dev/null)
#   rm -f "$tmpfile"
#   if [ -n "$output" ]; then
#     local first_error
#     first_error=$(echo "$output" | head -1)
#     deny "Ruff: $first_error"
#   fi
# }
# run_ruff_check

# --- Option D: golangci-lint (Go — ~500ms) ---
# run_go_check() {
#   local tmpfile
#   tmpfile=$(mktemp /tmp/hook-lint-XXXXXX.go)
#   printf '%s' "$NEW_STR" > "$tmpfile"
#   local output
#   output=$(golangci-lint run --no-config --disable-all \
#     --enable errcheck,govet,staticcheck "$tmpfile" 2>/dev/null)
#   rm -f "$tmpfile"
#   if [ -n "$output" ]; then
#     deny "Go lint: $(echo "$output" | head -1)"
#   fi
# }
# run_go_check

exit 0
