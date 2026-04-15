---
name: tentacle-orchestration
description: Break complex tasks into scoped, parallel work units for multi-agent execution. Use when a task touches multiple modules, layers, or features that can be worked on independently. Also use when someone says "orchestrate", "multi-agent", "parallel agents", "tentacle", or "swarm". This skill replaces ad-hoc agent-teams with structured, file-based context that persists across agent boundaries.
---

# Tentacle Orchestration

Break a complex task into scoped work units ("tentacles"), enrich each with context, then dispatch agents in parallel. Results persist in files so nothing is lost between agent boundaries.

Adapted from the [OctoGent](https://github.com/hesamsheikh/octogent) tentacle pattern.

## When to use

| Good fit | Not a good fit |
|----------|----------------|
| Feature spanning multiple modules | Simple 1-2 file changes |
| Parallel agents with clear scope boundaries | Strictly sequential tasks |
| Multi-layer work (API + DB + Tests + UI) | Limited token budget |
| Bug investigation with multiple hypotheses | Single-file edits |

## Core concept

A **tentacle** is a scoped work unit stored as files:

```
.octogent/tentacles/<name>/
├── CONTEXT.md    ← What the agent needs to know (scope, constraints, key files)
├── todo.md       ← Checkbox items — each is a delegation unit
├── handoff.md    ← Agent writes results here when done
└── meta.json     ← Metadata (scope, status, timestamps)
```

The octopus metaphor: one orchestrator (you), multiple tentacles (agents), each handling a distinct code region.

## Workflow

The workflow has 4 phases: **Plan → Execute → Verify → Close**.

Verification is not optional — without it, you're trusting agent output blindly. Agents hallucinate, skip edge cases, and claim "tests pass" without running them.

### Phase 1: Plan (Steps 1–4)

#### Step 1: Decompose the task into modules

Read the task description and identify independent code regions. Each region becomes one tentacle.

#### Step 2: Create tentacles

```bash
python3 ~/.copilot/tools/tentacle.py create <module-name> \
  --scope "<file-patterns>" \
  --desc "<short description>" \
  --briefing
```

The `--briefing` flag injects past mistakes and patterns from session-knowledge into CONTEXT.md — use it every time.

#### Step 3: Add todos

```bash
python3 ~/.copilot/tools/tentacle.py todo <name> add "<specific, atomic task>"
```

Each todo should be one deliverable — testable, reviewable, and completable in isolation.

#### Step 4: Enrich CONTEXT.md

Read reference files with `view`, then edit CONTEXT.md to add:
- **What exists**: describe the current code in the scope area
- **Key files**: full paths to reference files the agent needs
- **Constraints**: rules specific to this code region

This is the most important step. Agent quality is directly proportional to CONTEXT.md quality.

### Phase 2: Execute (Steps 5–6)

#### Step 5: Dispatch agents (swarm)

```bash
python3 ~/.copilot/tools/tentacle.py swarm <name> --agent-type <type> --model <model>
```

Use the output as the prompt for `task()`. Launch independent tentacles in parallel.

#### Step 6: Monitor progress

```bash
python3 ~/.copilot/tools/tentacle.py status
python3 ~/.copilot/tools/tentacle.py show <name>
```

### Phase 3: Verify (Steps 7–10)

This is the critical phase. Every step here catches a different class of agent error.

#### Step 7: Build gate

Run the project's compiler/linter on all changed files. Do not trust agent claims that "it compiles."

```bash
# Examples — use whatever your project uses:
npx tsc --noEmit                    # TypeScript
cargo check                         # Rust
go build ./...                      # Go
python -m py_compile <file>         # Python
```

**Gate**: If build fails → fix before proceeding. Either fix yourself or re-dispatch the responsible tentacle agent with the error output.

#### Step 8: Test gate

Run actual tests. Agents often claim "all tests pass" without running them, or write tests that don't actually assert anything meaningful.

```bash
# Run tests for the affected modules
yarn test <changed-files> --maxWorkers=1
pytest <changed-files>
go test ./...
```

**Gate**: If tests fail → fix before proceeding. Check whether the agent wrote the tests — agents sometimes write tests that are trivially correct (e.g., testing that `true === true`).

#### Step 9: Code review

Dispatch a code-review agent (in a separate context — never let code review itself) to review all changes across tentacles.

```python
task(
    agent_type="code-review",
    model="claude-sonnet-4.6",
    prompt="Review all files changed by tentacle agents: <file list>. "
           "Focus on: correctness, security, scope violations, and missed edge cases."
)
```

**Gate**: If review finds issues → fix → re-review → loop until verdict is CLEAN (max 5 rounds).

#### Step 10: QA audit (high-risk changes only)

For changes touching auth, data integrity, financial logic, or infrastructure, add a cross-check by a different agent. This catches errors that code-review misses because the reviewer may share the same blind spots as the author.

```python
task(
    agent_type="general-purpose",  # or qa-auditor if available
    model="claude-sonnet-4.6",
    prompt="Audit these changes as a QA engineer. Verify: "
           "1. Do the tests actually test the stated requirements? "
           "2. Are there untested edge cases? "
           "3. Does the code match the spec/task description? "
           "Task was: <original task description>"
)
```

Skip this step for low-risk changes (documentation, formatting, simple refactors).

### Phase 4: Close (Steps 11–12)

#### Step 11: Complete and learn

```bash
python3 ~/.copilot/tools/tentacle.py complete <name>
```

Only call `complete` after all verification gates pass. This marks all todos done and auto-extracts learnings from handoff.md into long-term knowledge.

#### Step 12: Cleanup

```bash
python3 ~/.copilot/tools/tentacle.py delete <name>
```

## Verification summary

| Gate | What it catches | Skip when |
|------|----------------|-----------|
| **Build** | Syntax errors, type mismatches, import failures | Never skip |
| **Test** | Logic bugs, regressions, broken contracts | Never skip |
| **Review** | Security issues, style violations, scope creep | Never skip |
| **QA audit** | Hallucinated tests, spec mismatches, blind spots | Low-risk changes only |

The first 3 gates are mandatory. Skipping any of them means you don't know if the agent output is correct — you're just hoping it is.

## CLI reference

```bash
# Create a tentacle (--briefing injects past knowledge into CONTEXT.md)
python3 ~/.copilot/tools/tentacle.py create <name> --scope "<paths>" --desc "<desc>" --briefing

# Add todo items
python3 ~/.copilot/tools/tentacle.py todo <name> add "<task>"

# View all tentacles
python3 ~/.copilot/tools/tentacle.py status

# View one tentacle in detail
python3 ~/.copilot/tools/tentacle.py show <name>

# Mark a todo done
python3 ~/.copilot/tools/tentacle.py todo <name> done <index>

# Record agent output (--learn saves to long-term knowledge)
python3 ~/.copilot/tools/tentacle.py handoff <name> "<message>" --learn

# Generate dispatch prompt for an agent
python3 ~/.copilot/tools/tentacle.py swarm <name> --agent-type <type> --model <model>

# Generate parallel dispatch (one agent per todo)
python3 ~/.copilot/tools/tentacle.py swarm <name> --output parallel

# Complete tentacle (auto-learn from handoff)
python3 ~/.copilot/tools/tentacle.py complete <name>

# Delete a tentacle
python3 ~/.copilot/tools/tentacle.py delete <name>
```

## Session-knowledge integration

The tentacle pattern integrates with `briefing.py` / `learn.py` for long-term memory:

| Flag | When | Effect |
|------|------|--------|
| `create --briefing` | Creating tentacle | Fetches past mistakes/patterns → injects into CONTEXT.md |
| `handoff --learn` | Agent finishes | Saves handoff to long-term knowledge base |
| `complete` | Closing tentacle | Marks done + auto-extracts learnings from handoff.md |

Lifecycle: `briefing → create → dispatch → handoff --learn → complete → delete`

## Agent selection guidance

Map module types to agent types based on what's available in your project (check AGENTS.md). Default mapping if no custom agents exist:

| Module type | agent-type | model |
|-------------|-----------|-------|
| Backend logic | `general-purpose` | `claude-sonnet-4.6` |
| Frontend UI | `general-purpose` | `claude-sonnet-4.6` |
| Tests | `general-purpose` | `claude-sonnet-4.6` |
| Code review | `code-review` | `claude-sonnet-4.6` |

If the project has custom agents (e.g., `lambda-developer`, `frontend-developer`), prefer those — they carry domain knowledge.

## CONTEXT.md template

```markdown
# <module-name>

<one-line description>

## Scope
- `<file-pattern-1>`
- `<file-pattern-2>`

## What exists
<!-- Read existing code and summarize -->

## Constraints
- DO NOT modify files outside your scope
- <project-specific conventions>

## Key files
- `<path/to/reference-file>` — <why it matters>
```

## Tips

1. **Invest in CONTEXT.md** — 2-3 minutes writing good context saves 10 minutes of agent confusion
2. **Keep todos atomic** — each item = one testable deliverable
3. **No scope overlap** — overlapping scopes cause agents to overwrite each other
4. **Complete before delete** — `complete` saves learnings; `delete` alone loses them
5. **No worktree needed** — Copilot CLI `task` tool already isolates agent context
