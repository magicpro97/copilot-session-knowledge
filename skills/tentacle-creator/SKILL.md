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

### Step 3: Generate the skill file

Create `.github/skills/tentacle-orchestration/SKILL.md` customized for the project.

The generated skill needs these sections:

**Agent mapping table** — Map module types to agent types. If the project has custom agents (from AGENTS.md), use those. Otherwise, fall back to `general-purpose`:

```markdown
| Module type | agent-type | model |
|-------------|-----------|-------|
| Backend logic | general-purpose | claude-sonnet-4.6 |
| Tests | general-purpose | claude-sonnet-4.6 |
| Code review | code-review | claude-sonnet-4.6 |
```

**Workflow examples** — Use actual folder patterns from the project:

```bash
# Next.js example:
tentacle.py create api-routes --scope "src/app/api/**/*" --desc "API routes"

# Django example:
tentacle.py create views --scope "myapp/views/*,myapp/serializers/*" --desc "Views layer"

# Go example:
tentacle.py create handlers --scope "internal/handler/*" --desc "HTTP handlers"
```

**CONTEXT.md template** — Include project-specific conventions (linting rules, import patterns, naming conventions) so agents follow them.

**Knowledge integration** — If `~/.copilot/tools/briefing.py` exists, include the `--briefing` and `--learn` flags. If not, note that session-knowledge can be installed for long-term memory.

See `references/skill-template.md` for the full template with placeholders.

### Step 4: Set up .gitignore

```bash
if ! grep -qF '.octogent/' .gitignore 2>/dev/null; then
    echo -e "\n# Tentacle orchestration (local work contexts)\n.octogent/" >> .gitignore
fi
```

### Step 5: Verify tentacle.py exists

```bash
ls ~/.copilot/tools/tentacle.py && python3 ~/.copilot/tools/tentacle.py --help
```

If missing, tell the user:
```
tentacle.py not found at ~/.copilot/tools/tentacle.py
Install: git clone https://github.com/magicpro97/copilot-session-knowledge.git ~/.copilot/tools
```

### Step 6: Report

Present a summary: project profile, files created, agent mappings detected, and example usage commands.

## Compatibility

| Component | Required | Fallback |
|-----------|----------|----------|
| `tentacle.py` | `~/.copilot/tools/` | Prompt to install |
| `briefing.py` | `~/.copilot/tools/` | Skip knowledge integration |
| `learn.py` | `~/.copilot/tools/` | Skip auto-learn |
| Custom agents | AGENTS.md | Use `general-purpose` |
| Git repo | `.git/` | Required (tentacles stored in git root) |
