# Tentacle CLI Reference

All commands use `sk tentacle` (fallback: `python3 ~/.copilot/tools/tentacle.py`).

## Lifecycle Commands

Before lifecycle commands, generate and review a task step file with `task-step-generator`:

```text
Use task-step-generator to write a project-local step file, then review it with `references/decomposition-review.md`.
Do not create tentacles until accepted/edited/rejected steps, dependencies, and evidence gates are explicit.
```

```bash
# Create a tentacle (--briefing injects past knowledge into CONTEXT.md)
sk tentacle create <name> --scope "<paths>" --desc "<desc>" --briefing

# Add todo items
sk tentacle todo <name> add "<task>"

# View all tentacles
sk tentacle status

# View one tentacle in detail
sk tentacle show <name>

# Mark a todo done
sk tentacle todo <name> done <index>

# Record structured agent output (preferred form)
# --status: DONE | BLOCKED | TOO_BIG | AMBIGUOUS | REGRESSED
# --changed-file is repeatable (one per file modified)
sk tentacle handoff <name> "<prose summary>" --status DONE --changed-file path/to/file.py --learn

# Backward-compatible free-form handoff (no structured status)
sk tentacle handoff <name> "<message>" --learn

# Run a verification command and persist results (for goal-eval and gate evidence)
sk tentacle verify <name> "<shell-command>" --label "<human-readable label>"

# Orchestrator-level goal state helpers
sk tentacle goal init --title "<goal title>" [--desc "<goal description>"] [--force]
sk tentacle goal status [--format text|json]
sk tentacle goal link <name>
sk tentacle goal eval [--decision continue|pause|complete|abandon] [--notes "<notes>"]
sk tentacle goal resume
sk tentacle goal resilience-status [--format text|json]

# Generate bundle-first dispatch prompt for an agent
sk tentacle swarm <name> --agent-type <type> --model <model> --briefing

# Generate parallel dispatch (one agent per todo, bundle-first by default)
sk tentacle swarm <name> --output parallel --briefing

# Structured JSON dispatch; includes bundle_path by default
sk tentacle swarm <name> --output json --briefing

# Rare opt-out for tiny/manual prompts
sk tentacle swarm <name> --no-bundle

# Complete tentacle (auto-learn from handoff)
sk tentacle complete <name>

# Delete a tentacle
sk tentacle delete <name>
```

## Session-Knowledge Integration

| Flag | When | Effect |
|------|------|--------|
| `create --briefing` | Creating tentacle | Fetches past mistakes/patterns → injects into CONTEXT.md |
| `swarm/dispatch` | Dispatching agent | Materializes bundle/ by default and surfaces `bundle_path` |
| `swarm/dispatch --no-bundle` | Tiny/manual dispatch | Opts out of file-backed context and uses inline prompt context |
| `handoff --status STATUS` | Agent finishes | Writes `STATUS: <value>` receipt into handoff.md; extracted by `complete` into `meta.json` as `terminal_status`. Triage statuses (`BLOCKED`, `TOO_BIG`, `AMBIGUOUS`, `REGRESSED`) print a visible orchestrator review signal. `DONE` does not. |
| `handoff --changed-file FILE` | Agent finishes | Appends `Changed: <path>` receipt (repeatable); all receipts are deduplicated and extracted by `complete` into `meta.json` as `changed_files[]` |
| `handoff --learn` | Agent finishes | Saves handoff to long-term knowledge base |
| `verify --label goal-eval` | Orchestrator evaluates goal | Runs a check command and persists pass/fail + duration as verification evidence; use after all Verify gates pass to record goal-evaluation results |
| `complete` | Closing tentacle | Marks done + auto-extracts learnings from handoff.md; parses latest `STATUS:` and all `Changed:` receipts into `meta.json` |

Lifecycle: `goal init → create/link → todo add → swarm/dispatch (bundle-first) → handoff --status DONE --changed-file … --learn → verify … --label goal-eval → goal eval --decision ... → [loop if goal unmet] → complete → delete`

### Handoff status allowlist

`--status` must be one of: `AMBIGUOUS`, `BLOCKED`, `DONE`, `REGRESSED`, `TOO_BIG`

| Status | Meaning | Orchestrator action |
|--------|---------|---------------------|
| `DONE` | Work complete, gates passed | No triage signal |
| `BLOCKED` | Cannot proceed — needs orchestrator intervention | ⚠️ Triage signal printed |
| `TOO_BIG` | Scope too large for a single tentacle | ⚠️ Triage signal printed |
| `AMBIGUOUS` | Spec or requirements unclear | ⚠️ Triage signal printed |
| `REGRESSED` | Change introduced a regression | ⚠️ Triage signal printed |

### goal resilience-status

Operator dashboard that classifies health, surfaces budget pressure, and lists blocking issues.

| Health | Condition |
|--------|-----------|
| `healthy` | Active, no budget pressure, no blocking gates, no failed criteria |
| `at-risk` | Budget approaching limit (≤1 iter remaining, ≥80 % timeout, ≤2 tentacles left), pending/rejected gates, failed criteria, or `paused` for a non-quota reason |
| `needs-action` | `needs-human`, `awaiting-gate`, `abandoned`, or `budget_limited` status; over budget; or `paused` with quota / rate-limit / blocked-retry signals (or non-empty `retry_queue` — persisted as `quota_retry_queue` by production writers) |

```bash
# Text dashboard (human-readable)
sk tentacle goal resilience-status

# Stable JSON output (machine-consumable; future fields default to null)
sk tentacle goal resilience-status --format json
```

JSON top-level keys: `goal_id`, `title`, `status`, `health`, `iteration`, `budget`, `gates`, `criteria`,
`needs_human_reason`, `awaiting_gate_id`, `awaiting_gate_reason`, `snapshot_state`, `pause_metadata`, `retry_queue`.

### Handoff examples

```bash
# Successful completion with two changed files
sk tentacle handoff my-feature "Implemented auth refresh. All tests pass." \
  --status DONE \
  --changed-file src/auth/refresh.py \
  --changed-file tests/test_auth.py \
  --learn

# Blocked — needs scope expansion
sk tentacle handoff my-feature "Cannot complete: db schema change required in src/db/ (out of scope)" \
  --status BLOCKED \
  --learn

# Free-form (no structured status — backward-compatible)
sk tentacle handoff my-feature "Updated config docs" --learn
```

## CONTEXT.md Template

```markdown
# <module-name>

<one-line description>

## Scope
- `<file-pattern-1>`
- `<file-pattern-2>`

## Step-plan review
- Source step file: `<path>`
- Accepted/edited/rejected steps: <summary>
- Dependency order: <what this tentacle waits for>
- Evidence contract: <logs/screenshots/traces/hashes or equivalent artifacts>

## What exists
<!-- Read existing code and summarize -->

## Constraints
- Avoid modifying files outside your scope — overlapping changes cause agent conflicts
- <project-specific conventions>

## Key files
- `<path/to/reference-file>` — <why it matters>
```

## Agent Selection Guidance

Map module types to agent types based on what's available in your project (check AGENTS.md). Default mapping if no custom agents exist:

| Module type | agent-type | model |
|-------------|-----------|-------|
| Backend logic | `general-purpose` | `claude-sonnet-4.6` |
| Frontend UI | `general-purpose` | `claude-sonnet-4.6` |
| Tests | `general-purpose` | `claude-sonnet-4.6` |
| Code review | `code-review` | `claude-sonnet-4.6` |

If the project has custom agents (e.g., `lambda-developer`, `frontend-developer`), prefer those — they carry domain knowledge.
