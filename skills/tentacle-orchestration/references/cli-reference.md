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

# Record agent output (--learn saves to long-term knowledge)
tentacle.py handoff <name> "<message>" --learn

# Generate dispatch prompt for an agent
tentacle.py swarm <name> --agent-type <type> --model <model>

# Generate parallel dispatch (one agent per todo)
tentacle.py swarm <name> --output parallel

# Complete tentacle (auto-learn from handoff)
tentacle.py complete <name>

# Delete a tentacle
tentacle.py delete <name>
```

## Session-Knowledge Integration

| Flag | When | Effect |
|------|------|--------|
| `create --briefing` | Creating tentacle | Fetches past mistakes/patterns → injects into CONTEXT.md |
| `handoff --learn` | Agent finishes | Saves handoff to long-term knowledge base |
| `complete` | Closing tentacle | Marks done + auto-extracts learnings from handoff.md |

Lifecycle: `briefing → create → dispatch → handoff --learn → complete → delete`

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
