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

# Project workflow (if any)
cat .github/WORKFLOW.md 2>/dev/null | head -30
cat WORKFLOW.md 2>/dev/null | head -30

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
| Workflow | WORKFLOW.md phases (if any) — e.g., CLARIFY → DESIGN → BUILD → TEST → COMMIT |

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
MUST include ALL of these sections. The generated file must also comply with
`~/.copilot/tools/skills/references/skill-standards.md` (YAML frontmatter, line count,
example blocks, description trigger words). Use this checklist:

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

**Agent mapping table** — Map module types to agents (with model tier + ID). Use project-specific agent names:

| Tentacle type | agent_type | Model | Scope pattern |
|--------------|-----------|-------|--------------|
| Backend logic | backend-dev | claude-sonnet-4.6 | `src/app/api/**/*` |
| Tests | test-writer | claude-sonnet-4.6 | `tests/**/*` |
| Code review | code-review | claude-sonnet-4.6 | `src/**/*` |

**Verification commands** — Replace generic commands with the project's actual build/test/lint commands in the Phase 3 gate table (e.g., `npx tsc --noEmit`, `npx eslint .`, `yarn test`).

**CONTEXT.md template** — Include project-specific conventions (linting rules, import patterns,
naming conventions, theme tokens, i18n rules) so agents follow them.

**Shared workspace warning** — Include the warning about parallel agents sharing filesystem.

**Knowledge integration** — If `~/.copilot/tools/briefing.py` exists, include `--briefing` and
`--learn` flags, `--budget 3000` for sub-agent injection, and agent timeout rules.
If not, note that session-knowledge can be installed for long-term memory.

**Workflow integration** — If the project has a WORKFLOW.md (detected in Step 1), add a
`## ⛔ WORKFLOW INTEGRATION` section showing which outer phases must complete BEFORE tentacles
start and which must run AFTER tentacles close. This prevents AI from treating the tentacle's
internal lifecycle as the entire workflow. Include a diagram like:

```
BEFORE tentacles: [phases from WORKFLOW.md before BUILD]
TENTACLE WORK:    [BUILD phase only]
AFTER tentacles:  [phases from WORKFLOW.md after BUILD]
```

If the project has no WORKFLOW.md, skip this section — the tentacle's internal lifecycle
is the entire workflow.

#### 4c: Structural validation (MANDATORY before proceeding)

After generating the file, run these checks:

```bash
# Validate all canonical headings are present
diff <(grep '^## ' ~/.copilot/tools/skills/tentacle-orchestration/SKILL.md | sort) \
     <(grep '^## ' .github/skills/tentacle-orchestration/SKILL.md | sort)

# Verify Core concept has file tree
grep -q '\.octogent/tentacles/' .github/skills/tentacle-orchestration/SKILL.md || echo "FAIL: missing file tree"

# Verify Phase 0 motivation text
grep -q 'costs 1x' .github/skills/tentacle-orchestration/SKILL.md || echo "FAIL: missing motivation text"
```

Any output (diff lines or FAIL messages) means the section is missing — add it before proceeding.

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

<example>
**Project:** Next.js + TypeScript monorepo with yarn workspaces

**Profile:** Language: TypeScript | Framework: Next.js 14 | Test: Jest + Playwright | Agents: backend-dev, test-writer, code-review

**Generated:** `.github/skills/tentacle-orchestration/SKILL.md` (customized from reference)

**Agent mapping:** backend-dev → `src/app/api/**/*` | test-writer → `tests/**/*` | code-review → any PR

**Verification commands:** `npx tsc --noEmit` (build) · `npx eslint .` (lint) · `yarn test` (tests)

**Usage:** `python3 ~/.copilot/tools/tentacle.py create api-routes --scope "src/app/api/**/*" --desc "REST endpoint changes" --briefing`
</example>

## Compatibility

| Component | Required | Fallback |
|-----------|----------|----------|
| `tentacle.py` | `~/.copilot/tools/` | Prompt to install |
| `briefing.py` | `~/.copilot/tools/` | Skip knowledge integration |
| `learn.py` | `~/.copilot/tools/` | Skip auto-learn |
| `agent-creator` | `~/.copilot/tools/skills/` | Fall back to `general-purpose` agents |
| Custom agents | AGENTS.md or `.github/agents/` | Generate via agent-creator, or use `general-purpose` |
| Git repo | `.git/` | Required (tentacles stored in git root) |
