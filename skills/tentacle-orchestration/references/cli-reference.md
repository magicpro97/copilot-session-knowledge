# Tentacle CLI Reference

All commands use `python3 ~/.copilot/tools/tentacle.py`.

## Lifecycle Commands

```bash
# Create a tentacle (--briefing injects past knowledge into CONTEXT.md)
tentacle.py create <name> --scope "<paths>" --desc "<desc>" --briefing

# Add todo items
tentacle.py todo <name> add "<task>"

# View all tentacles
tentacle.py status

# View one tentacle in detail
tentacle.py show <name>

# Mark a todo done
tentacle.py todo <name> done <index>

# Record structured agent output (preferred form)
# --status: DONE | BLOCKED | TOO_BIG | AMBIGUOUS | REGRESSED
# --changed-file is repeatable (one per file modified)
tentacle.py handoff <name> "<prose summary>" --status DONE --changed-file path/to/file.py --learn

# Backward-compatible free-form handoff (no structured status)
tentacle.py handoff <name> "<message>" --learn

# Generate bundle-first dispatch prompt for an agent
tentacle.py swarm <name> --agent-type <type> --model <model> --briefing

# Generate parallel dispatch (one agent per todo, bundle-first by default)
tentacle.py swarm <name> --output parallel --briefing

# Structured JSON dispatch; includes bundle_path by default
tentacle.py swarm <name> --output json --briefing

# Rare opt-out for tiny/manual prompts
tentacle.py swarm <name> --no-bundle

# Complete tentacle (auto-learn from handoff)
tentacle.py complete <name>

# Delete a tentacle
tentacle.py delete <name>
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
| `complete` | Closing tentacle | Marks done + auto-extracts learnings from handoff.md; parses latest `STATUS:` and all `Changed:` receipts into `meta.json` |

Lifecycle: `briefing → create → todo add → swarm/dispatch (bundle-first) → handoff --status DONE --changed-file … --learn → complete → delete`

### Handoff status allowlist

`--status` must be one of: `AMBIGUOUS`, `BLOCKED`, `DONE`, `REGRESSED`, `TOO_BIG`

| Status | Meaning | Orchestrator action |
|--------|---------|---------------------|
| `DONE` | Work complete, gates passed | No triage signal |
| `BLOCKED` | Cannot proceed — needs orchestrator intervention | ⚠️ Triage signal printed |
| `TOO_BIG` | Scope too large for a single tentacle | ⚠️ Triage signal printed |
| `AMBIGUOUS` | Spec or requirements unclear | ⚠️ Triage signal printed |
| `REGRESSED` | Change introduced a regression | ⚠️ Triage signal printed |

### Handoff examples

```bash
# Successful completion with two changed files
tentacle.py handoff my-feature "Implemented auth refresh. All tests pass." \
  --status DONE \
  --changed-file src/auth/refresh.py \
  --changed-file tests/test_auth.py \
  --learn

# Blocked — needs scope expansion
tentacle.py handoff my-feature "Cannot complete: db schema change required in src/db/ (out of scope)" \
  --status BLOCKED \
  --learn

# Free-form (no structured status — backward-compatible)
tentacle.py handoff my-feature "Updated config docs" --learn
```

## CONTEXT.md Template

```markdown
# <module-name>

<one-line description>

## Scope
- `<file-pattern-1>`
- `<file-pattern-2>`

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
