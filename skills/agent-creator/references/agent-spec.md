# .agent.md Format Specification

Reference for the GitHub Copilot `.agent.md` format. Each agent file defines a
specialized AI persona with specific capabilities and behavior.

## Frontmatter (Required)

```yaml
---
name: 'Human-Readable Agent Name'
description: 'What it does. Use when [triggers]. Activates for [keywords].'
tools: ['tool1', 'tool2']      # optional but recommended
model: 'Claude Sonnet 4'       # optional — specify for consistency
---
```

### Field Details

| Field | Required | Purpose |
|-------|----------|---------|
| `name` | Yes | Human-readable display name |
| `description` | Yes | Trigger mechanism — be broad and keyword-rich |
| `tools` | Recommended | Available tool categories (array) |
| `model` | Optional | Pin a specific model for consistent behavior |
| `user-invocable` | Optional | Whether user can invoke directly (default: true) |
| `disable-model-invocation` | Optional | Prevent AI from auto-triggering (default: false) |
| `mcp-servers` | Optional | MCP server configurations |
| `github` | Optional | GitHub-specific settings |
| `skills` | Optional | Skills this agent can use |

> **⚠️ Deprecated:** `infer` is deprecated. Use `user-invocable` + `disable-model-invocation` instead.

### Valid Tool Names (Copilot CLI)

Use these names in the `tools` array:

| Category | Maps to |
|----------|---------|
| `bash`, `execute`, `shell` | Shell execution |
| `read`, `view` | File reading |
| `edit` | File editing |
| `grep`, `search` | Code search |
| `glob` | File pattern matching |
| `task`, `agent` | Sub-agent dispatch |
| `web_search` | Web search |
| `web_fetch` | Web fetching |
| `ask_user` | User interaction |
| `lsp` | Language server |
| `sql` | Database queries |

> **⚠️ Cross-platform:** VS Code tool names (`search/codebase`, `edit/editFiles`) and
> Claude Code names (`Bash`, `Read`, `Edit`) are NOT valid in Copilot CLI.

### Description Best Practices

The description is the primary trigger mechanism. AI tends to **under-trigger**
agents, so descriptions should be "pushy" — include many keywords and scenarios:

```yaml
# Too narrow — will miss many relevant contexts
description: 'Debug applications'

# Good — catches a wide range of trigger phrases
description: 'Systematically find and fix bugs through structured investigation.
  Use when something is broken, tests fail unexpectedly, behavior differs from
  expectations, or when asked to "debug", "fix", "investigate", or "why is this failing".'
```

## Body Structure

After frontmatter, write instructions in standard Markdown:

```markdown
# Agent Title

Brief purpose statement (1-2 sentences).

## Workflow / Phases

### Phase 1: Name
Steps in imperative form...

### Phase 2: Name
Steps in imperative form...

## Principles
- Explain WHY each principle matters
```

## Writing Guidelines

These principles (from Anthropic's skill-creator) produce better agent behavior:

1. **Explain why.** "Check for null because `!!0` returns false in JavaScript"
   teaches reasoning. "ALWAYS check for null" just adds noise.

2. **Imperative form.** "Trace the execution path" not "You should trace."

3. **Phases over flat lists.** Named phases (Assessment → Investigation →
   Resolution) give the model hierarchical structure to follow.

4. **Keep it lean.** If the model would do something without being told,
   the instruction wastes context. Remove it.

5. **Be general.** Describe patterns, not specific instances. An agent
   that references `src/auth/jwt.ts` breaks when the file moves. An agent
   that says "find the authentication module" works everywhere.

## File Placement

```
.github/agents/           # GitHub Copilot convention
  ├── plan.agent.md
  ├── debug.agent.md
  └── ...
```

## Attribution

When adapting agents from community sources, add an HTML comment:

```markdown
<!-- Based on: https://github.com/github/awesome-copilot/blob/main/agents/debug.agent.md -->
```
