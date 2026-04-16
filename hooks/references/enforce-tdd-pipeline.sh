#!/bin/bash
# enforce-tdd-pipeline.sh — TEMPLATE
#
# preToolUse hook that blocks task_complete unless a TDD/quality pipeline
# is completed with valid evidence.
#
# PURPOSE:
#   Prevents AI agents from marking tasks done without actually running
#   the required quality gates (tests, reviews, audits).
#
# DESIGN:
#   - Evidence-based: requires actual output files, not just claims
#   - Git-aware: links evidence to current branch/commits
#   - Tamper-resistant: validates content, not just file existence
#   - Freshness check: evidence expires after configurable hours
#   - Graceful skip: non-pipeline tasks pass through cleanly
#
# CUSTOMIZATION:
#   1. Set PHASES array for your project's quality gates
#   2. Set EVIDENCE_BASE_DIR for where evidence lives
#   3. Set MAX_EVIDENCE_AGE_HOURS for expiry
#   4. Edit phase validation functions for your evidence format
#
# EXAMPLES:
#
#   Strict TDD (5 phases):
#     PHASES: e2e-red → implement → review → test-execution → qa-audit
#
#   Standard (3 phases):
#     PHASES: tests → review → verify
#
#   Minimal (2 phases):
#     PHASES: tests → review
set -euo pipefail

INPUT="$(cat)"
TOOL_NAME=$(echo "$INPUT" | jq -r '.toolName // empty')

if [ "$TOOL_NAME" != "task_complete" ]; then
  exit 0
fi

# ══════════════════════════════════════════════════════
# CONFIGURATION — customize for your project
# ══════════════════════════════════════════════════════

# Where TDD evidence directories are stored
EVIDENCE_BASE_DIR="test-results/strict-tdd"

# Maximum age of evidence before it's considered stale
MAX_EVIDENCE_AGE_HOURS=48

# State file created when TDD workflow starts (optional but recommended)
# Format: {"feature":"name","started_sha":"abc123","started_at":"ISO8601"}
STATE_FILE=".tdd-pipeline-active"

# ══════════════════════════════════════════════════════
# PHASE DEFINITIONS — customize for your workflow
# ══════════════════════════════════════════════════════
# Each phase: directory name, required files, validation function.
# Validation functions are defined below. Delete/add phases as needed.
#
# Default: 5-phase strict TDD pipeline.
# For simpler workflows, comment out phases you don't need.

# Phase spec format: "dir_name:required_file:validator"
PHASES=(
  "phase1-red:test-output.log:validate_red"
  "phase2-green:test-output.log:validate_green"
  "phase3-review:review-report.md:validate_review"
  "phase4-execution:test-output.log:validate_execution"
  "phase5-qa-audit:audit-report.md:validate_audit"
)

# For a simpler 3-phase pipeline, replace PHASES with:
# PHASES=(
#   "tests:test-output.log:validate_green"
#   "review:review-report.md:validate_review"
#   "verify:test-output.log:validate_execution"
# )

# ══════════════════════════════════════════════════════

deny() {
  echo "{\"permissionDecision\":\"deny\",\"permissionDecisionReason\":\"$1\"}"
  exit 0
}

# ══════════════════════════════════════════════════════
# STEP 1: Identify the current feature/task
# ══════════════════════════════════════════════════════

FEATURE_NAME=""

# Method 1: Explicit state file (agents create this when starting pipeline)
if [ -f "$STATE_FILE" ]; then
  FEATURE_NAME=$(jq -r '.feature // empty' "$STATE_FILE" 2>/dev/null)
  STARTED_SHA=$(jq -r '.started_sha // empty' "$STATE_FILE" 2>/dev/null)
fi

# Method 2: Match evidence dir to current git branch
if [ -z "$FEATURE_NAME" ]; then
  CURRENT_BRANCH=$(git rev-parse --abbrev-ref HEAD 2>/dev/null || echo "")
  if [ -n "$CURRENT_BRANCH" ]; then
    # Extract last path segment: dev/fpt/feature/5022-desc → 5022-desc
    BRANCH_FEATURE=$(echo "$CURRENT_BRANCH" | sed -E 's|.*/([^/]+)$|\1|')
    [ -d "$EVIDENCE_BASE_DIR/$BRANCH_FEATURE" ] && FEATURE_NAME="$BRANCH_FEATURE"
  fi
fi

# Method 3: Most recent evidence directory (weakest — use only as fallback)
if [ -z "$FEATURE_NAME" ]; then
  LATEST_DIR=$(find "$EVIDENCE_BASE_DIR/" -maxdepth 1 -mindepth 1 -type d 2>/dev/null | sort | tail -1)
  [ -n "$LATEST_DIR" ] && FEATURE_NAME=$(basename "$LATEST_DIR")
fi

# No evidence at all → not a TDD task → allow
if [ -z "$FEATURE_NAME" ]; then
  exit 0
fi

EVIDENCE_DIR="$EVIDENCE_BASE_DIR/$FEATURE_NAME"
[ ! -d "$EVIDENCE_DIR" ] && exit 0

# ══════════════════════════════════════════════════════
# STEP 2: Check evidence freshness
# ══════════════════════════════════════════════════════

NEWEST_FILE=$(find "$EVIDENCE_DIR" -type f \( -name "*.log" -o -name "*.md" -o -name "*.json" \) -printf '%T@ %p\n' 2>/dev/null | sort -rn | head -1 | cut -d' ' -f2-)

if [ -n "$NEWEST_FILE" ]; then
  FILE_MOD=$(stat -c %Y "$NEWEST_FILE" 2>/dev/null || echo 0)
  NOW=$(date +%s)
  AGE_SEC=$(( NOW - FILE_MOD ))
  MAX_SEC=$(( MAX_EVIDENCE_AGE_HOURS * 3600 ))

  if [ "$AGE_SEC" -gt "$MAX_SEC" ]; then
    deny "TDD evidence expired: newest file is $(( AGE_SEC / 3600 ))h old (max ${MAX_EVIDENCE_AGE_HOURS}h). Re-run pipeline for '$FEATURE_NAME'."
  fi
fi

# ══════════════════════════════════════════════════════
# STEP 3: Phase validators — customize these
# ══════════════════════════════════════════════════════
# Each validator receives the phase directory as $1.
# Return 0 = valid, return 1 = invalid (set INVALID_REASON).

INVALID_REASON=""

# RED phase: tests must FAIL or be explicitly SKIPPED with reason
validate_red() {
  local dir="$1"
  local json="$dir/evidence.json"
  if [ -f "$json" ]; then
    local result
    result=$(jq -r '.result // empty' "$json" 2>/dev/null)
    if [ "$result" != "FAIL" ] && [ "$result" != "SKIPPED" ]; then
      INVALID_REASON="RED phase must show FAIL or SKIPPED (got: ${result:-empty})"
      return 1
    fi
    if [ "$result" = "SKIPPED" ]; then
      local reason
      reason=$(jq -r '.reason // empty' "$json" 2>/dev/null)
      if [ -z "$reason" ]; then
        INVALID_REASON="RED phase SKIPPED without reason"
        return 1
      fi
    fi
  fi
  return 0
}

# GREEN phase: test output must show passing tests
validate_green() {
  local dir="$1"
  local log="$dir/test-output.log"
  if [ -f "$log" ]; then
    # Look for common test framework success patterns
    if ! grep -qEi '(pass|passed|✓|✅|OK|succeeded|All.*tests)' "$log" 2>/dev/null; then
      local json="$dir/evidence.json"
      if [ -f "$json" ]; then
        local exit_code
        exit_code=$(jq -r '.exit_code // empty' "$json" 2>/dev/null)
        [ "$exit_code" = "0" ] && return 0
      fi
      INVALID_REASON="GREEN phase: no passing tests found in output"
      return 1
    fi
  fi
  return 0
}

# REVIEW phase: must contain CLEAN verdict
validate_review() {
  local dir="$1"
  local report="$dir/review-report.md"
  if [ -f "$report" ]; then
    if ! grep -qEi '(verdict|status).*CLEAN|\bCLEAN\b' "$report" 2>/dev/null; then
      INVALID_REASON="REVIEW phase: verdict is not CLEAN"
      return 1
    fi
  fi
  return 0
}

# EXECUTION phase: must show all tests passing, no failures
validate_execution() {
  local dir="$1"
  local log="$dir/test-output.log"
  if [ -f "$log" ]; then
    local fail_lines
    fail_lines=$(grep -cEi '^\s*(FAIL|✗|✘)\s' "$log" 2>/dev/null || echo "0")
    if [ "$fail_lines" -gt 0 ]; then
      INVALID_REASON="EXECUTION phase: test output contains $fail_lines failure(s)"
      return 1
    fi
  fi
  return 0
}

# AUDIT phase: must have APPROVED verdict (tamper-resistant check)
validate_audit() {
  local dir="$1"
  local report="$dir/audit-report.md"
  if [ -f "$report" ]; then
    # Structured check: "Verdict: APPROVED" or "## Verdict: APPROVED"
    if grep -qEi '(verdict|result)\s*[:=]\s*APPROVED' "$report" 2>/dev/null; then
      return 0
    fi
    # Fallback: both first and last verdict mentions must be APPROVED
    local first last
    first=$(grep -oE 'APPROVED|REJECTED' "$report" 2>/dev/null | head -1)
    last=$(grep -oE 'APPROVED|REJECTED' "$report" 2>/dev/null | tail -1)
    if [ "$first" = "APPROVED" ] && [ "$last" = "APPROVED" ]; then
      return 0
    fi
    if [ "$first" = "REJECTED" ] && [ "$last" = "APPROVED" ]; then
      INVALID_REASON="AUDIT: was REJECTED then APPROVED appended (suspicious)"
      return 1
    fi
    INVALID_REASON="AUDIT: verdict is not APPROVED (found: ${last:-none})"
    return 1
  fi
  return 0
}

# ══════════════════════════════════════════════════════
# STEP 4: Check all phases
# ══════════════════════════════════════════════════════

MISSING=""
INVALID=""

for phase_spec in "${PHASES[@]}"; do
  IFS=':' read -r dir_name req_file validator <<< "$phase_spec"
  phase_dir="$EVIDENCE_DIR/$dir_name"
  phase_file="$phase_dir/$req_file"
  evidence_json="$phase_dir/evidence.json"

  # Check existence (file OR evidence.json)
  if [ ! -f "$phase_file" ] && [ ! -f "$evidence_json" ]; then
    MISSING="$MISSING $dir_name"
    continue
  fi

  # Run content validator
  INVALID_REASON=""
  if type "$validator" >/dev/null 2>&1; then
    if ! "$validator" "$phase_dir"; then
      INVALID="$INVALID $dir_name($INVALID_REASON)"
    fi
  fi
done

if [ -n "$MISSING" ]; then
  deny "TDD Pipeline incomplete for '$FEATURE_NAME'. Missing:${MISSING}. Complete ALL phases before task_complete."
fi

if [ -n "$INVALID" ]; then
  deny "TDD Pipeline evidence invalid for '$FEATURE_NAME':${INVALID}. Fix and re-run."
fi

# ══════════════════════════════════════════════════════
# STEP 5: Git SHA validation (optional)
# ══════════════════════════════════════════════════════

CURRENT_SHA=$(git rev-parse HEAD 2>/dev/null || echo "")

if [ -n "${STARTED_SHA:-}" ] && [ -n "$CURRENT_SHA" ] && [ "$STARTED_SHA" != "unknown" ]; then
  if ! git merge-base --is-ancestor "$STARTED_SHA" "$CURRENT_SHA" 2>/dev/null; then
    deny "TDD evidence created for $STARTED_SHA but HEAD is $CURRENT_SHA (not a descendant). Evidence may be from a different branch."
  fi
fi

exit 0
