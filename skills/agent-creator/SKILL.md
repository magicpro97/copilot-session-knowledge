---
name: agent-creator
description: >
  Generate project-specific .agent.md files for GitHub Copilot from curated templates.
  Use when setting up a new project, onboarding a codebase, running /init, or when
  the user mentions "create agents", "setup copilot agents", "generate .agent.md",
  or wants specialized AI agents for their development workflow. Also triggers when
  tentacle-creator needs default agents for a project that has none.
handoffs:
  - label: "Add quality hooks"
    skill: hook-creator
    prompt: "Generate project-specific hooks that enforce the workflow the new agents describe."
    send: false
  - label: "Review generated agents"
    skill: code-reviewer
    prompt: "Review the generated .agent.md files for correctness gaps before the team relies on them."
    send: false
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

Templates live in `references/`. Some capture universal development workflows
(planning, TDD, debugging, verification, research); specialist-profile templates
also carry expert contracts (`profile_id`, role, domain, triggers, quality gates,
escalation rules, and evidence requirements). The skill adapts them to the
project's toolchain, conventions, and directory structure.

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
| `spec-clarifier.agent.md` | Every project — analyze specs for ambiguities, risks, trade-offs before coding | `references/spec-clarifier.agent.md` |
| `plan.agent.md` | Every project — strategic planning before implementation | `references/plan.agent.md` |
| `tdd-red.agent.md` | Projects with test infrastructure — write failing tests first | `references/tdd-red.agent.md` |
| `tdd-green.agent.md` | Companion to tdd-red — minimal implementation | `references/tdd-green.agent.md` |
| `tdd-refactor.agent.md` | Companion to tdd-green — quality + security hardening | `references/tdd-refactor.agent.md` |
| `debug.agent.md` | Every project — systematic bug investigation | `references/debug.agent.md` |
| `doublecheck.agent.md` | Projects needing verification — fact-check AI output | `references/doublecheck.agent.md` |
| `research-spike.agent.md` | Technical exploration — exhaustive spike research | `references/research-spike.agent.md` |
| `staff-engineer.agent.md` | Architecture/design ownership — ADRs, trade-offs, hard-to-reverse decisions | `references/staff-engineer.agent.md` |
| `backend-python-specialist.agent.md` | Python/stdlib/SQLite/TDD work — backend scripts, hooks, regression fixes | `references/backend-python-specialist.agent.md` |
| `security-reviewer.agent.md` | Security review — OWASP/ASVS/STRIDE, auth, secrets, injection, CORS/PNA | `references/security-reviewer.agent.md` |
| `browse-ui-specialist.agent.md` | browse-ui React/TypeScript work — host state, pnpm gates, Playwright | `references/browse-ui-specialist.agent.md` |
| `qa-specialist.agent.md` | Independent verification — evidence ledger, scope audit, test quality | `references/qa-specialist.agent.md` |
| `docs-writer.agent.md` | Documentation — Diataxis, README/API/runbook/changelog accuracy | `references/docs-writer.agent.md` |
| `research-planner.agent.md` | Evidence-first research orchestration with source-backed findings | `references/research-planner.agent.md` |

For most projects, start with **spec-clarifier + plan + debug + tdd-red/green/refactor** (6 agents).
Add doublecheck and research-spike for teams that value verification rigor. For
tentacle orchestration, also install the specialist profiles that match the stack
so `sk tentacle create --profile <profile_id>` can inject expert behavior into
CONTEXT.md, meta.json, and dispatch prompts.

### Step 3: Customize Each Template

Read the selected template from `references/`, then adapt:

1. **Tools list** — match the project's available tools (VS Code extensions, MCP servers, CLI tools)
2. **Test commands** — replace generic "run tests" with actual commands (`yarn test`, `pytest`, etc.)
3. **File patterns** — reference actual directories (`src/`, `tests/`, etc.)
4. **Conventions** — incorporate naming patterns, linting rules, commit formats from project docs
5. **Issue integration** — configure branch-to-issue mapping for the project's tracker
6. **Profile contract** — preserve or add `profile_id`, `role`, `domain`,
   `triggers`, `quality_gates`, `escalation_rules`, `anti_patterns`, and
   `evidence_required` so tentacle dispatch can treat the agent as a real expert

Keep customizations focused. Workflow templates should stay general, but specialist
profiles must include enough project-specific gates and evidence to change behavior.
Do not ship a specialist profile that is only a title plus a generic checklist.

<example>
**Before** (generic template `tdd-red.agent.md`):
```yaml
tools: ['run_tests']
```
```markdown
Run the test suite to confirm the test fails.
```

**After** (customized for a yarn + Jest project):
```yaml
tools: ['run_terminal_cmd']
```
```markdown
Run `yarn test --testPathPattern=<filename> --no-coverage` to confirm the new test fails.
The `--no-coverage` flag keeps feedback fast during the red phase.
```

Only the test command and the performance note were added — everything else stays generic.
</example>

### Step 4: Write Agent Files

Place generated agents in `.github/agents/` (GitHub Copilot convention):

```
.github/agents/
├── plan.agent.md
├── tdd-red.agent.md
├── tdd-green.agent.md
├── tdd-refactor.agent.md
├── debug.agent.md
├── backend-python-specialist.agent.md
├── security-reviewer.agent.md
├── qa-specialist.agent.md
├── doublecheck.agent.md          # optional
└── research-spike.agent.md       # optional
```

### Step 5: Verify

After creating agents, confirm:
- Each file has valid YAML frontmatter (`name`, `description`, `tools`)
- Descriptions are "pushy" — include trigger phrases so the agent activates reliably (Copilot uses description text for trigger matching; vague descriptions cause agents to never activate)
- Commands referenced in agents actually exist in the project
- File paths referenced in agents match the real directory structure
- Specialist profiles include role/domain/triggers/gates/escalations/evidence
- `sk tentacle create <name> --profile <profile_id>` can load the profile

## .agent.md Format Reference

Every `.agent.md` file needs YAML frontmatter:

```yaml
---
name: 'Human-Readable Agent Name'
description: 'What it does. Use when [specific triggers]. Activates for [keywords].'
tools: ['list', 'of', 'available', 'tools']
model: 'Claude Sonnet 4'  # optional but recommended
profile_id: 'backend-specialist'  # optional but recommended for tentacle profiles
role: 'Backend Specialist'
domain: 'backend'
quality_gates:
  - 'Relevant tests pass with output attached'
evidence_required:
  - 'Changed-file receipts and verification logs'
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
