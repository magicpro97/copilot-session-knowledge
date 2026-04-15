---
name: agent-creator
description: >
  Generate project-specific .agent.md files for GitHub Copilot from curated templates.
  Use when setting up a new project, onboarding a codebase, running /init, or when
  the user mentions "create agents", "setup copilot agents", "generate .agent.md",
  or wants specialized AI agents for their development workflow. Also triggers when
  tentacle-creator needs default agents for a project that has none.
---

# Agent Creator

Generate `.agent.md` files tailored to a project's tech stack, conventions, and workflow.

Projects without custom agents miss out on focused AI behavior — a planning agent that
understands your architecture, a debugger that knows your test framework, or a TDD cycle
tuned to your issue tracker. This skill bridges that gap by analyzing the codebase and
producing ready-to-use agents from battle-tested templates.

## How It Works

```
Analyze project → Select relevant templates → Customize → Write .agent.md files
```

Templates live in `references/` — each one captures a universal development workflow
(planning, TDD, debugging, verification, research). The skill adapts them to the
project's specific toolchain, conventions, and directory structure.

## When to Create Agents

Create agents when:
- Setting up a new project (no `.github/agents/` directory exists)
- The tentacle-creator or session-knowledge-creator needs default agent definitions
- A user asks for specialized AI workflows
- Onboarding a codebase that would benefit from structured AI assistance

## Creation Workflow

### Step 1: Analyze the Project

Examine the codebase to understand:

- **Language & framework** — check `package.json`, `pyproject.toml`, `Cargo.toml`, `go.mod`
- **Test framework** — look for jest, vitest, pytest, junit config files
- **Build system** — check for makefiles, scripts, CI configs
- **Issue tracker** — GitHub Issues, Jira, Linear integration
- **Existing conventions** — read README.md, AGENTS.md, CLAUDE.md, copilot-instructions.md
- **Directory structure** — understand where source, tests, and config live

### Step 2: Select Templates

Choose from the bundled templates based on project needs:

| Template | Best For | Read From |
|----------|----------|-----------|
| `plan.agent.md` | Every project — strategic planning before implementation | `references/plan.agent.md` |
| `tdd-red.agent.md` | Projects with test infrastructure — write failing tests first | `references/tdd-red.agent.md` |
| `tdd-green.agent.md` | Companion to tdd-red — minimal implementation | `references/tdd-green.agent.md` |
| `tdd-refactor.agent.md` | Companion to tdd-green — quality + security hardening | `references/tdd-refactor.agent.md` |
| `debug.agent.md` | Every project — systematic bug investigation | `references/debug.agent.md` |
| `doublecheck.agent.md` | Projects needing verification — fact-check AI output | `references/doublecheck.agent.md` |
| `research-spike.agent.md` | Technical exploration — exhaustive spike research | `references/research-spike.agent.md` |

For most projects, start with **plan + debug + tdd-red/green/refactor** (5 agents).
Add doublecheck and research-spike for teams that value verification rigor.

### Step 3: Customize Each Template

Read the selected template from `references/`, then adapt:

1. **Tools list** — match the project's available tools (VS Code extensions, MCP servers, CLI tools)
2. **Test commands** — replace generic "run tests" with actual commands (`yarn test`, `pytest`, etc.)
3. **File patterns** — reference actual directories (`src/`, `tests/`, etc.)
4. **Conventions** — incorporate naming patterns, linting rules, commit formats from project docs
5. **Issue integration** — configure branch-to-issue mapping for the project's tracker

Keep customizations minimal — the templates are intentionally general so they adapt
to different contexts. Only add project-specific details that meaningfully change behavior.

### Step 4: Write Agent Files

Place generated agents in `.github/agents/` (GitHub Copilot convention):

```
.github/agents/
├── plan.agent.md
├── tdd-red.agent.md
├── tdd-green.agent.md
├── tdd-refactor.agent.md
├── debug.agent.md
├── doublecheck.agent.md          # optional
└── research-spike.agent.md       # optional
```

### Step 5: Verify

After creating agents, confirm:
- Each file has valid YAML frontmatter (`name`, `description`, `tools`)
- Descriptions are "pushy" — include trigger phrases so the agent activates reliably
- Commands referenced in agents actually exist in the project
- File paths referenced in agents match the real directory structure

## .agent.md Format Reference

Every `.agent.md` file needs YAML frontmatter:

```yaml
---
name: 'Human-Readable Agent Name'
description: 'What it does. Use when [specific triggers]. Activates for [keywords].'
tools: ['list', 'of', 'available', 'tools']
model: 'Claude Sonnet 4'  # optional but recommended
---

# Agent Title

Instructions in markdown...
```

The `description` field is the trigger mechanism — make it broad and keyword-rich.
AI tends to under-trigger agents, so lean toward being "pushy" about when the agent
should activate. Better to trigger too often than miss relevant contexts.

## Writing Principles

These principles come from Anthropic's skill-creator and produce better agent behavior:

- **Explain why, not just what.** "Check for null because JavaScript's `!!0` returns false,
  hiding valid zero values" teaches the model to reason about edge cases.
- **Imperative form.** "Trace the execution path" not "You should trace the execution path."
- **Phases over checklists.** Group related steps into named phases (Assessment → Investigation
  → Resolution) rather than flat numbered lists. Models handle hierarchical structure better.
- **Keep it lean.** Remove instructions that don't change behavior. If the model would do
  something anyway without being told, the instruction is noise.
- **Be general.** Avoid narrowing to specific examples that become brittle. Describe the
  pattern, not the instance.

## Integration with Other Skills

This skill works with the broader setup toolkit:

- **tentacle-creator** — when generating a tentacle-orchestration skill, it maps workflow
  steps to agents. If the project has no agents, tentacle-creator invokes agent-creator
  to produce defaults.
- **session-knowledge-creator** — references the agents created here in the project's
  copilot-instructions or CLAUDE.md.
- **skill-creator** (Anthropic) — if the project needs custom skills alongside agents,
  use skill-creator for that. This skill focuses specifically on `.agent.md` files.
