---
name: tentacle-creator
description: Analyze a project and generate a customized tentacle-orchestration skill for multi-agent workflows. Use when someone says "set up tentacle", "configure multi-agent", "set up orchestration", or when onboarding a project that needs parallel agent coordination. Also use when the user wants to break complex tasks into scoped work units for multiple agents.
---

# Tentacle Creator

A meta-skill that analyzes your project and generates a customized `tentacle-orchestration` skill — tailored to your project's language, framework, folder structure, and available agents.

Think of it like `/init` — you run it once per project to bootstrap the tentacle pattern.

## Why customize per project

The generic tentacle skill works everywhere but produces generic dispatch prompts. A customized version knows your project's agent types (e.g., `lambda-developer` vs `general-purpose`), folder conventions (e.g., `src/app/api/**/*` vs `backend/handlers/*`), and coding standards. This means richer CONTEXT.md files and better agent output.

## Workflow

### Step 1: Analyze the project

Run these in parallel to understand what you're working with:

```bash
# Directory structure
find . -maxdepth 3 -type d -not -path '*/node_modules/*' -not -path '*/.git/*' -not -path '*/dist/*' | head -50

# Language and framework
ls package.json pyproject.toml Cargo.toml go.mod pom.xml build.gradle Gemfile mix.exs 2>/dev/null
cat package.json 2>/dev/null | head -30

# Custom agents
cat AGENTS.md 2>/dev/null | head -50
ls .github/agents/ 2>/dev/null

# Existing skills
ls .github/skills/ 2>/dev/null

# Coding conventions
cat .editorconfig 2>/dev/null
ls doc/ docs/ 2>/dev/null | head -10

# Git info
git remote -v 2>/dev/null | head -2
```

### Step 2: Build a project profile

From the analysis, determine:

| Field | Examples |
|-------|---------|
| Language | TypeScript, Python, Go, Java, Rust |
| Framework | Next.js, Django, Spring Boot, Express |
| Architecture | Monorepo, microservices, monolith, serverless |
| Layers | API, service, repository, model, test |
| Test framework | Jest, pytest, JUnit, go test |
| Custom agents | From AGENTS.md (if any) |
| Folder patterns | `src/app/api/*`, `backend/handlers/*`, `tests/**/*` |

### Step 3: Generate agents (if missing)

If the project has no custom agents in AGENTS.md or `.github/agents/`, use the `agent-creator` skill to generate `.agent.md` files first. Tentacle orchestration works best when agents are domain-specific (e.g., `backend-dev`, `test-writer`) rather than generic `general-purpose`.

Run the agent-creator skill, or read `~/.copilot/tools/skills/agent-creator/SKILL.md` and follow its workflow to generate agents from the curated templates in `references/`.

### Step 4: Generate the skill file

Create `.github/skills/tentacle-orchestration/SKILL.md` customized for the project.

**IMPORTANT: Fork, don't rewrite.** Start from the canonical reference at
`~/.copilot/tools/skills/tentacle-orchestration/SKILL.md` and customize it.
Every section in the reference MUST appear in the output. You are ADDING
project-specific content, not cherry-picking sections.

#### 4a: Required sections (ALL must be present)

Read the reference and extract its section headings (`## ` and `### `). The generated file
MUST include ALL of these sections. Use this checklist:

| # | Required Section | Source | Action |
|---|-----------------|--------|--------|
| 1 | `## When to use` | Reference | Keep table, add project-specific rows |
| 2 | `## Anti-patterns` | Reference | Keep all items, add project-specific items |
| 3 | `## Core concept` | Reference | **Copy verbatim** — file tree diagram, octopus metaphor |
| 4 | `## Workflow` (or `## Internal Workflow`) | Reference | Keep 5-phase labels: Clarify → Plan → Execute → Verify → Close |
| 5 | `### Phase 0: Clarify Spec` | Reference | Keep motivation text ("bug found in spec costs 1x..."), Steps 0.0–0.5 |
| 6 | `### Phase 1: Plan` | Reference | Keep "Impact Analysis / Risk Assessment" mention, customize folder patterns |
| 7 | `### Phase 2: Execute` | Reference | Keep `--model` param in swarm, customize agent mapping table |
| 8 | `### Phase 3: Verify` | Reference | Keep 6-gate table, replace commands with project's build/lint/test |
| 9 | `### Phase 4: Close` | Reference | Keep `complete` before `delete` warning |
| 10 | `## Verification summary` | Reference | Keep 6-gate table (mirrors Phase 3 gates) |
| 11 | `## CLI reference` | Reference | Keep all commands, include `--model` in swarm |
| 12 | `## Tips` | Reference | Keep all tips, add project-specific tips |
| 13 | `## Reference docs` | New | Link to `~/.copilot/tools/skills/tentacle-orchestration/references/` |

**If a reference section exists but is not in the checklist above, include it anyway.**
The checklist is a minimum — not an exclusive list.

#### 4b: Project-specific additions

Layer these ON TOP of the canonical structure:

**Agent mapping table** — Map module types to the agents detected in Step 2 or generated in Step 3.
Include model tier AND model ID columns. Use project-specific agent names, not generic `general-purpose`:

```markdown
| Tentacle type | agent_type | Model tier | Model ID |
|--------------|-----------|------------|----------|
| Backend logic | backend-dev | standard | claude-sonnet-4.6 |
| Tests | test-writer | standard | claude-sonnet-4.6 |
| Code review | code-review | standard | claude-sonnet-4.6 |
```

**Architecture decomposition table** — Map the project's folder structure to tentacle scope patterns:

```markdown
| Layer | Scope Pattern | Example Tentacle |
|-------|---------------|-----------------|
| API routes | `src/app/api/**/*` | `api-routes` |
| Database | `src/lib/db/*` | `database` |
| Tests | `tests/**/*` | `test-suite` |
```

**Verification commands** — Replace the reference's generic commands with the project's actual
build/test/lint commands in the Phase 3 verification gate table:

```markdown
| Step | Gate | Command | What it catches | Skip? |
|------|------|---------|----------------|-------|
| 7 | Build | `npx tsc --noEmit` | Syntax errors | Never |
| 8 | Lint | `npx eslint .` | Style violations | Never |
| 9 | Test | `yarn test` | Logic bugs | Never |
| 10 | Review | Dispatch code-reviewer | Design flaws | Never |
| 11 | Docs | Check README/CHANGELOG | Stale docs | Internal refactors |
| 12 | QA audit | Manual review | Visual bugs | Low-risk only |
```

**CONTEXT.md template** — Include project-specific conventions (linting rules, import patterns,
naming conventions, theme tokens, i18n rules) so agents follow them.

**Shared workspace warning** — Include the warning about parallel agents sharing filesystem.

**Knowledge integration** — If `~/.copilot/tools/briefing.py` exists, include `--briefing` and
`--learn` flags, `--budget 3000` for sub-agent injection, and agent timeout rules.
If not, note that session-knowledge can be installed for long-term memory.

#### 4c: Structural validation (MANDATORY before proceeding)

After generating the file, run this self-check:

```
1. Extract all `## ` headings from the GENERATED file
2. Extract all `## ` headings from the REFERENCE file
3. Every reference heading MUST have a corresponding heading in the generated file
4. Missing heading = FAIL → add the section before proceeding
5. Check Phase 0 contains motivation text ("costs 1x / 10x / 100x")
6. Check Phase 1 mentions "Impact Analysis"
7. Check Phase 2 swarm command includes `--model`
8. Check "Core concept" section has file tree diagram
9. Check "Reference docs" section exists with links to canonical references/
```

Only proceed to Step 5 after all checks pass.

### Step 5: Set up .gitignore

```bash
if ! grep -qF '.octogent/' .gitignore 2>/dev/null; then
    echo -e "\n# Tentacle orchestration (local work contexts)\n.octogent/" >> .gitignore
fi
```

### Step 6: Verify tentacle.py exists

```bash
ls ~/.copilot/tools/tentacle.py && python3 ~/.copilot/tools/tentacle.py --help
```

If missing, tell the user:
```
tentacle.py not found at ~/.copilot/tools/tentacle.py
Install: git clone https://github.com/magicpro97/copilot-session-knowledge.git ~/.copilot/tools
```

### Step 7: Report

Present a summary: project profile, files created, agent mappings detected, verification commands, and example usage commands.

## Compatibility

| Component | Required | Fallback |
|-----------|----------|----------|
| `tentacle.py` | `~/.copilot/tools/` | Prompt to install |
| `briefing.py` | `~/.copilot/tools/` | Skip knowledge integration |
| `learn.py` | `~/.copilot/tools/` | Skip auto-learn |
| `agent-creator` | `~/.copilot/tools/skills/` | Fall back to `general-purpose` agents |
| Custom agents | AGENTS.md or `.github/agents/` | Generate via agent-creator, or use `general-purpose` |
| Git repo | `.git/` | Required (tentacles stored in git root) |
