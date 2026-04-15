---
name: hook-creator
description: >
  Generate project-specific Copilot CLI hooks (.github/hooks/) for quality enforcement.
  Use when setting up a new project, onboarding a codebase, or when the user mentions
  "create hooks", "setup guards", "enforce rules", "quality gates", "preToolUse",
  "postToolUse", or wants automated safety rails for AI coding sessions.
---

# Hook Creator

Generate `.github/hooks/` preToolUse and postToolUse scripts tailored to a project's
tech stack, architecture rules, and workflow conventions.

Hooks are the strongest enforcement mechanism in Copilot CLI — they intercept EVERY tool
call before/after execution. Unlike skills (which AI can ignore) or instructions (which
AI can rationalize skipping), hooks physically block violations.

## How Hooks Work

```
User request → AI plans tool call → preToolUse hook → ALLOW/DENY → tool executes → postToolUse hook
```

- **preToolUse**: Intercepts BEFORE execution. Can DENY with reason. Use for blocking violations.
- **postToolUse**: Runs AFTER execution. Can emit warnings. Use for reminders and tracking.

Hook scripts receive JSON on stdin with `toolName`, `toolArgs`, and (for post) `toolResult`.

## When to Create Hooks

Create hooks when:
- Setting up a new project that needs guardrails
- Architecture rules are being violated repeatedly
- Sensitive operations need blocking (secrets, destructive commands)
- Workflow compliance needs enforcement (test before commit, build after edit)

## Creation Workflow

### Step 1: Analyze the Project

Examine the codebase to understand its architecture layers, language, framework,
existing hooks, and import rules.

### Step 2: Select Applicable Hooks

Choose from the curated templates in `references/`:

| Template | Type | Best For | Risk Level |
|----------|------|----------|------------|
| `dangerous-blocker.sh` | preToolUse | **Every project** — blocks sudo, rm -rf /, force push | Critical |
| `secret-detector.sh` | preToolUse | **Every project** — blocks credentials in code | Critical |
| `architecture-guard.sh` | preToolUse | Projects with layer boundaries (clean arch, hexagonal) | High |
| `test-reminder.sh` | postToolUse | Projects with test infrastructure | Medium |
| `build-reminder.sh` | postToolUse | Compiled language projects | Medium |
| `commit-gate.sh` | preToolUse | Projects requiring verification before commit | High |
| `session-banner.sh` | postToolUse | Projects with onboarding checklists | Low |

### Step 3: Customize Each Template

Read the selected template from `references/`, then adapt:

1. **Architecture rules** — map the project's actual layer names and import boundaries
2. **Secret patterns** — add project-specific credential patterns
3. **Build commands** — replace generic commands with actual ones
4. **File patterns** — reference actual directory structure
5. **Commit gates** — define what verification is needed before commit

### Step 4: Install Hooks

Place scripts in `.github/hooks/scripts/` and configure hooks in the project's
copilot config or settings file.

### Step 5: Verify

Test each hook by piping mock JSON input and checking for correct allow/deny behavior.

## Hook Script Format

### preToolUse (can DENY)

```bash
#!/bin/bash
set -euo pipefail
INPUT="$(cat)"
TOOL_NAME=$(echo "$INPUT" | jq -r '.toolName // empty')

if [ "$TOOL_NAME" != "bash" ]; then exit 0; fi

TOOL_ARGS_RAW=$(echo "$INPUT" | jq -r '.toolArgs // empty')
COMMAND=$(echo "$TOOL_ARGS_RAW" | jq -r '.command // empty')

deny() {
    echo "{\"permissionDecision\":\"deny\",\"permissionDecisionReason\":\"$1\"}"
    exit 0
}

echo "$COMMAND" | grep -qE 'pattern' && deny "Reason"
exit 0
```

### postToolUse (warnings only)

```bash
#!/bin/bash
set -euo pipefail
INPUT="$(cat)"
TOOL_NAME=$(echo "$INPUT" | jq -r '.toolName // empty')
RESULT_TYPE=$(echo "$INPUT" | jq -r '.toolResult.resultType // empty')

if [[ "$RESULT_TYPE" != "success" ]]; then exit 0; fi

echo "Warning: remember to verify"
exit 0
```

## Writing Principles

1. **Minimal false positives.** Precise patterns — `rm -rf /` is dangerous, `rm -rf ./dist` is fine.
2. **Clear deny reasons.** AI reads and adjusts. "Architecture violation: X must not import Y" teaches the rule.
3. **Fast execution.** Under 100ms. Use grep, not Python. No network calls.
4. **Exit 0 to allow.** Only output deny JSON to block. Other output = informational.
5. **Composable.** Each hook does ONE thing. Multiple hooks chain together.

## Integration with Other Skills

- **agent-creator** — hooks enforce rules agents should follow. Even if agent prompt misses a rule, hook catches it.
- **tentacle-orchestration** — hooks protect against scope violations during parallel agent work.
- **session-knowledge** — hooks can remind about briefing and log events.
