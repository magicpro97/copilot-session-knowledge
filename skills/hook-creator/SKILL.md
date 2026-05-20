---
name: hook-creator
description: >
  Generate project-specific Copilot CLI hooks (.github/hooks/) for quality enforcement.
  Use when setting up a new project, onboarding a codebase, or when the user mentions
  "create hooks", "setup guards", "enforce rules", "quality gates", "preToolUse",
  "postToolUse", or wants automated safety rails for AI coding sessions. Also use when
  the user wants to enforce coding standards, block dangerous commands, protect secrets,
  gate commits, enforce TDD pipelines, or add any form of automated guardrails.
handoffs:
  - label: "Audit generated hooks"
    skill: code-reviewer
    prompt: "Review the generated hook scripts for safety gaps, broken assumptions, and enforcement holes."
    send: true
---

# Hook Creator

Generate `.github/hooks/` preToolUse and postToolUse scripts tailored to a project's
tech stack, architecture rules, and workflow conventions.

> **Host scope:** Hooks (both global `~/.copilot/hooks/` and project-level `.github/hooks/`)
> are supported by **Copilot CLI only**. Claude Code does not use this hook format.
> Project-level hooks registered via `hooks.json` / `review-policy.json` are Copilot CLI only.

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

## Architecture: Unified Hook Runner

For **global** (user-level) hooks, use the unified runner architecture in
`~/.copilot/tools/hooks/`. Instead of standalone scripts, create rule modules:

```
hooks/
  hook_runner.py          # Dispatcher — reads stdin once, runs matching rules
  rules/
    __init__.py           # Registry — ordered list of all rules
    common.py             # Shared: get_module(), deny(), info(), constants
    your_rule.py           # Your custom rule module
```

**Rule module template:**
```python
from . import Rule
from .common import deny, info

class MyRule(Rule):
    name = "my-rule"
    events = ["preToolUse"]           # Which events to handle
    tools = ["edit", "create", "bash"] # Which tools to match (empty = all)

    def evaluate(self, event, data):
        tool_args = data.get("toolArgs", {})
        # Return deny("reason") to block, info("msg") to inform, None to pass
        return None
```

Register in `rules/__init__.py` by adding to `ALL_RULES`.

**Benefits over standalone scripts:**
- Single process per event (not N processes)
- Shared stdin parsing, marker auth, and tamper checks
- Fail-open: rule errors don't block the agent
- Audit log: all decisions in `~/.copilot/markers/audit.jsonl`
- Dry-run mode: `HOOK_DRY_RUN=1`

For **project-level** hooks (`.github/hooks/`), standalone bash/Python scripts
are still the correct approach — they're simpler and self-contained.

## When to Create Hooks

Create hooks when:
- Setting up a new project that needs guardrails
- Architecture rules are being violated repeatedly
- Sensitive operations need blocking (secrets, destructive commands)
- Workflow compliance needs enforcement (test before commit, build after edit)
- Coding standards need real-time enforcement during AI edits
- A TDD/quality pipeline must be completed before task completion

## Creation Workflow

### Step 1: Analyze the Project

Examine the codebase to understand:
- Language(s) and framework(s)
- Architecture layers and import rules
- Existing hooks in `.github/hooks/`
- Available linters/formatters (eslint, ruff, golangci-lint, etc.)
- Workflow requirements (TDD phases, review gates)

### Step 2: Select Applicable Hooks

Choose from the curated templates in `references/`. Templates are organized by
concern — pick the ones relevant to the project, then customize.

**Security hooks** (recommended for every project):

| Template | Type | Purpose |
|----------|------|---------|
| `dangerous-blocker.py` | preToolUse | Blocks sudo, rm -rf /, force push, DB drops |
| `secret-detector.py` | preToolUse | Blocks hardcoded API keys, tokens, private keys |

**Quality enforcement hooks**:

| Template | Type | Purpose |
|----------|------|---------|
| `enforce-coding-standards.py` | preToolUse | Blocks coding standard violations with 2-tier detection (regex + optional linter) |
| `enforce-tdd-pipeline.py` | preToolUse | Blocks task_complete without valid evidence from quality pipeline |
| `architecture-guard.py` | preToolUse | Enforces layer boundaries (clean arch, hexagonal, KMP) |
| `commit-gate.py` | preToolUse | Blocks commit until verification requirements are met |

**Reminder hooks**:

| Template | Type | Purpose |
|----------|------|---------|
| `test-reminder.py` | postToolUse | Reminds to write/run tests after source file edits |
| `build-reminder.py` | postToolUse | Reminds to verify build after N source file edits |
| `docs-reminder.py` | postToolUse | Warns after 3+ code edits without doc updates |
| `session-banner.py` | postToolUse | Shows session start checklist |

> **Cross-platform note:** bundled templates are Python-only and use only stdlib. They run on
> Windows, macOS, and Linux without Bash/JQ. Register them with Python commands for each host
> shell field, for example: `"bash": "python3 ./scripts/hook.py"` and
> `"powershell": "python ./scripts/hook.py"`.

### Step 3: Customize Each Template

Read the selected template from `references/`, then adapt. Each template has a clearly
marked `CONFIGURATION` section at the top. Key customizations:

1. **Language/file extensions** — set which files the hook applies to
2. **Rules** — add/remove/edit rules for the project's conventions
3. **Linter integration** — uncomment and configure the project's linter for AST-level checks
4. **Architecture rules** — map the project's actual layer names and import boundaries
5. **Pipeline phases** — define quality gates and evidence requirements
6. **Secret patterns** — add project-specific credential patterns

If any regex, command pattern, protected path, or phase gate has project-fit confidence below
`1.0`, do not write it into a blocking hook yet. Dispatch research/validation on the strongest
available model (`claude-opus-4.7` when available), capture evidence, and only then encode the
rule. Guessed hooks create false positives that train agents to bypass guardrails.

#### Customizing `enforce-coding-standards.py`

This template uses a two-tier detection strategy:

**Tier 1: Regex rules (~5ms)** — always runs, catches common violations instantly.
Edit the `REGEX RULES` section to add project-specific patterns.

**Tier 2: Linter integration (~200ms-2s)** — optional, AST-level analysis.
Uncomment ONE linter block matching the project's stack:

| Language | Linter | Speed | Config |
|----------|--------|-------|--------|
| TypeScript/JS | espree (AST) | ~35ms | Option B in template |
| TypeScript/JS | eslint (full) | ~1-2s | Option A in template |
| Python | ruff | ~50ms | Option C in template |
| Go | golangci-lint | ~500ms | Option D in template |

**Example: Adapting for a Python project:**
```python
# 1. Change file extensions
FILE_EXTENSIONS = re.compile(r"\.py$")

# 2. Replace JS rules with Python rules
REGEX_RULES = (
    (re.compile(r"^\s*from\s+\S+\s+import\s+\*", re.MULTILINE), "No wildcard imports."),
    (re.compile(r"^\s*except\s*:", re.MULTILINE), "No bare except. Catch specific exceptions."),
)

# 3. Enable the optional ruff integration if the project has ruff installed.
```

#### Customizing `enforce-tdd-pipeline.py`

This template validates evidence files from a quality pipeline before allowing
task_complete. Customize the `PHASES` array for your workflow:

```python
# Strict 5-phase TDD:
PHASES = (
  ("phase1-red", "test-output.log", "red"),
  ("phase2-green", "test-output.log", "green"),
  ("phase3-review", "review-report.md", "review"),
  ("phase4-execution", "test-output.log", "execution"),
  ("phase5-qa-audit", "audit-report.md", "audit"),
)

# Standard 3-phase:
PHASES = (
  ("tests", "test-output.log", "green"),
  ("review", "review-report.md", "review"),
  ("verify", "test-output.log", "execution"),
)

# Minimal 2-phase:
PHASES = (
  ("tests", "test-output.log", "green"),
  ("review", "review-report.md", "review"),
)
```

Key protections built in:
- **Freshness**: evidence expires after `MAX_EVIDENCE_AGE_HOURS` (default 48h)
- **Content validation**: checks file contents, not just existence
- **Git SHA linking**: evidence must match current branch/commits
- **Anti-tamper**: structured verdict parsing prevents appending "APPROVED" to a "REJECTED" report
- **Branch matching**: tries to match evidence dir to current git branch name

### Step 4: Install Hooks

Place scripts in `.github/hooks/scripts/` and register in `hooks.json`:

```json
{
  "version": 1,
  "hooks": {
    "preToolUse": [
      {
        "type": "command",
        "bash": "python3 .github/hooks/scripts/enforce-coding-standards.py",
        "powershell": "python .github/hooks/scripts/enforce-coding-standards.py",
        "comment": "Block coding standard violations",
        "timeoutSec": 10
      }
    ]
  }
}
```

### Step 5: Verify

Test each hook by piping mock JSON input and checking for correct allow/deny behavior:

```powershell
# Test that a violation is denied
'{"toolName":"edit","toolArgs":{"path":"src/app.ts","new_str":"import _ from ''lodash''"}}' |
  python .github/hooks/scripts/enforce-coding-standards.py

# Test that clean code is allowed
'{"toolName":"edit","toolArgs":{"path":"src/app.ts","new_str":"import { map } from ''es-toolkit''"}}' |
  python .github/hooks/scripts/enforce-coding-standards.py
```

## Hook Script Format

### preToolUse (can DENY)

```python
#!/usr/bin/env python3
import json
import re
import sys

data = json.loads(sys.stdin.read() or "{}")
if data.get("toolName") != "bash":
    raise SystemExit(0)
command = (data.get("toolArgs") or {}).get("command", "")
if re.search(r"pattern", command):
    print(json.dumps({"permissionDecision": "deny", "permissionDecisionReason": "Reason"}))
```

### postToolUse (warnings only)

```python
#!/usr/bin/env python3
import json
import sys

data = json.loads(sys.stdin.read() or "{}")
result_type = (data.get("toolResult") or {}).get("resultType", "")
if result_type == "success":
    print("Warning: remember to verify")
```

## Writing Principles

1. **Minimal false positives.** Precise patterns — `rm -rf /` is dangerous, `rm -rf ./dist` is fine.
2. **Clear deny reasons.** AI reads and adjusts. "Architecture violation: X must not import Y" teaches the rule.
3. **Fast execution.** Under 100ms. Use stdlib Python; no network calls.
4. **Exit 0 to allow.** Only output deny JSON to block. Other output = informational.
5. **Composable.** Each hook does ONE thing. Multiple hooks chain together.
6. **Cross-platform.** Use `.py` templates with only stdlib dependencies; no Bash/JQ requirement.

## Integration with Other Skills

- **agent-creator** — hooks enforce rules agents should follow. Even if agent prompt misses a rule, hook catches it.
- **tentacle-orchestration** — hooks protect against scope violations during parallel agent work.
- **session-knowledge** — hooks can remind about briefing and log events.

## Python Hook Template (Windows-compatible)

```python
#!/usr/bin/env python3
"""postToolUse hook — pure Python, no bash/jq needed."""
import json, os, sys, tempfile

def main():
    data = json.loads(sys.stdin.read())
    tool_name = data.get('toolName', '')
    result = (data.get('toolResult') or {}).get('resultType', '')
    if result != 'success' or tool_name not in ('edit', 'create'):
        sys.exit(0)
    args = data.get('toolArgs', {})
    if isinstance(args, str):
        args = json.loads(args)
    file_path = args.get('path', '')
    # ... your logic here ...
    sys.exit(0)

if __name__ == '__main__':
    main()
```

Register in `review-policy.json`:
```json
{
  "type": "command",
   "bash": "python3 ./scripts/my-hook.py",
  "powershell": "python scripts/my-hook.py",
  "cwd": ".github/hooks",
  "timeoutSec": 5
}
```

<example>
**Project:** Python/Django REST API

**Goal:** Block wildcard imports, bare `except`, and hardcoded secrets.

**Step 1 – Analyze:** Python project using ruff. No existing hooks. Architecture: views → services → models.

**Step 2 – Select templates:** `secret-detector.py` (security) + `enforce-coding-standards.py` (quality).

**Step 3 – Customize `enforce-coding-standards.py`:**
```python
FILE_EXTENSIONS = re.compile(r"\.py$")
# Regex rules
REGEX_RULES = (
    (re.compile(r"^\s*from\s+\S+\s+import\s+\*", re.MULTILINE), "No wildcard imports."),
    (re.compile(r"^\s*except\s*:", re.MULTILINE), "No bare except. Catch specific exceptions."),
)
```

**Step 4 – Install:** Placed in `.github/hooks/scripts/`, registered in `hooks.json`.

**Step 5 – Verify:**
```powershell
'{"toolName":"edit","toolArgs":{"path":"api/views.py","new_str":"from utils import *"}}' |
  python .github/hooks/scripts/enforce-coding-standards.py
# → {"permissionDecision":"deny","permissionDecisionReason":"No wildcard imports."}
```
</example>
