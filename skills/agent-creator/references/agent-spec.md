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

## Specialist Profile Fields (Optional, Recommended for Tentacles)

Tentacle orchestration can load `.agent.md` files as specialist profiles with
`sk tentacle create <name> --profile <profile_id>`. These fields are additive:
Copilot can ignore unknown keys, while `tentacle.py` mirrors them into `meta.json`
and injects them into CONTEXT.md and dispatch prompts.

```yaml
---
name: 'Security Reviewer'
description: 'Use for auth, secrets, injection, CORS, OWASP, ASVS, threat model.'
tools: ['grep', 'glob', 'read', 'bash']
model: 'claude-opus-4.6'
profile_id: 'security-reviewer'
agent_type: 'code-review'
role: 'Security Reviewer'
domain: 'security'
model_tier: 'security'
goal: |
  Find exploitable vulnerabilities before code ships.
expertise:
  - 'OWASP Top 10 and ASVS'
triggers:
  - 'auth, secrets, CORS, PNA, injection, threat model'
quality_gates:
  - 'No unresolved HIGH or CRITICAL finding is marked safe'
escalation_rules:
  - 'Use BLOCKED when a high-risk issue lacks mitigation or risk acceptance'
anti_patterns:
  - 'Approving security-sensitive code without exploit-path analysis'
evidence_required:
  - 'Findings with severity, file:line, impact, and remediation'
tools_denied:
  - 'git commit'
  - 'git push'
---
```

| Field | Purpose |
|-------|---------|
| `profile_id` | Stable slug used by `--profile` and stored in `meta.json` |
| `agent_type` | Default Copilot agent type when dispatching a profiled tentacle |
| `role` / `goal` / `domain` | Specialist identity and ownership injected into prompts |
| `expertise` | Concrete domains the agent should reason from |
| `triggers` | Keywords, paths, or events that indicate the profile should own the work |
| `quality_gates` | Role-specific pass/fail checks before handoff |
| `escalation_rules` | Exact conditions for BLOCKED, AMBIGUOUS, REGRESSED, or scope escalation |
| `anti_patterns` | Behaviors that make the agent superficial or unsafe |
| `evidence_required` | Artifacts required before a DONE handoff is accepted |
| `tools_denied` | Explicit prompt-level boundaries in addition to platform hooks |

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
