---
name: session-knowledge-creator
description: Generate a project-customized session-knowledge skill with enforced AI integration. Use this skill whenever someone says "set up knowledge tracking", "configure session knowledge", "make AI remember things", "set up briefing", or when onboarding a new project that should have cross-session learning. Also use when the user complains that AI keeps forgetting things or repeating past mistakes across sessions.
---

# Session Knowledge Creator

A meta-skill that analyzes your project and generates a customized session-knowledge integration — including the critical `.instructions.md` enforcement file that makes AI agents actually use it.

## When to Use

- Someone says "set up knowledge tracking", "make AI remember things", or "configure session knowledge"
- Onboarding a new project where AI should retain context across sessions
- AI keeps repeating past mistakes or forgetting decisions made earlier
- A project already uses `briefing.py`/`learn.py` but has no enforcement file

## Why this exists

AI agents ignore session-knowledge tools even when SKILL.md says "MANDATORY". The root cause: SKILL.md is reference-only documentation that AI must actively choose to read. Most don't. The solution is `.instructions.md` files — Copilot CLI auto-injects these into every context, so the AI has no choice but to see the rules.

This skill generates three files that work together:
1. **SKILL.md** — detailed reference (AI reads on demand)
2. **`.instructions.md`** — short imperative rules (auto-injected into every context)
3. **CLAUDE.md patch** — brief pointer to the other two files

## Workflow

### Step 1: Analyze the project

Run these in parallel to understand the project:

```bash
# Structure
find . -maxdepth 3 -type d -not -path '*/node_modules/*' -not -path '*/.git/*' | head -40

# Language and framework
ls package.json pyproject.toml Cargo.toml go.mod pom.xml 2>/dev/null
cat package.json 2>/dev/null | python3 -c "import sys,json; d=json.load(sys.stdin); print('name:', d.get('name')); print('deps:', list(d.get('dependencies',{}).keys())[:10])" 2>/dev/null

# Existing AI configuration
ls .github/skills/ .github/instructions/ 2>/dev/null
head -20 CLAUDE.md 2>/dev/null
head -20 .github/copilot-instructions.md 2>/dev/null
cat AGENTS.md 2>/dev/null | head -30

# Tools availability
ls ~/.copilot/tools/briefing.py ~/.copilot/tools/learn.py 2>/dev/null
python3 ~/.copilot/tools/learn.py --stats 2>/dev/null | head -10
```

If `briefing.py` or `learn.py` are missing, tell the user to install first:
```
git clone https://github.com/magicpro97/copilot-session-knowledge.git ~/.copilot/tools
```

### Step 2: Build a project profile

From the analysis, determine:

| Field | Where to find it |
|-------|-----------------|
| Project name | package.json / pyproject.toml / repo name |
| Language | File extensions + config files |
| Domain | README.md / CLAUDE.md description |
| Key modules | Folder structure |
| Test command | package.json scripts / Makefile |
| Custom agents | AGENTS.md |

Also determine **wing/room mappings** — these organize knowledge hierarchically. Wings are top-level categories (matching your project's architecture layers), rooms are specific modules within each wing.

Example mappings for common project types:

- **Next.js e-commerce**: wings = frontend, backend, database, devops; rooms = auth, products, cart, payments
- **Django blog**: wings = backend, frontend, testing; rooms = posts, users, comments, media
- **Go microservices**: wings = api-gateway, user-service, order-service, infra; rooms = handlers, repository, config

Derive yours from the actual folder structure.

### Step 3: Generate three files

#### File 1: `.github/instructions/session-knowledge.instructions.md`

This is the most important file — it's auto-injected into every AI context. Keep it short and imperative. Use the template in `references/instructions-template.md`, replacing domain-specific tags with actual project terms.

The file must have `applyTo: "**/*"` in its YAML frontmatter so it loads for all file types.

#### File 2: `.github/skills/session-knowledge/SKILL.md`

A detailed reference with project-specific examples, wing/room mappings, and workflow integration. Use the template in `references/skill-template.md`, replacing all `<PLACEHOLDER>` values.

The examples matter — use actual domain terms from the project (not generic "DynamoDB" or "patient" examples). This helps AI understand what kind of knowledge to record.

#### File 3: CLAUDE.md or copilot-instructions.md patch

Add a brief section pointing to the other two files. Keep it short — enforcement comes from the `.instructions.md` file, not from CLAUDE.md:

```markdown
## Session Knowledge

> Enforced by `.github/instructions/session-knowledge.instructions.md` (auto-loaded).
> Tools: `~/.copilot/tools/briefing.py`, `learn.py`, `query-session.py`
> JSON output: `briefing.py "task" --json`, `learn.py ... --json`, `query-session.py "q" --export json`
> Details: `.github/skills/session-knowledge/SKILL.md`
```

### Step 4: Verify

```bash
python3 ~/.copilot/tools/briefing.py --wakeup
python3 ~/.copilot/tools/briefing.py "task description" --json    # verify JSON output works
python3 ~/.copilot/tools/learn.py --discovery "Setup test" "Session knowledge configured" --tags "setup" --json
cat .github/instructions/session-knowledge.instructions.md
head -5 .github/skills/session-knowledge/SKILL.md

# Validate the generated SKILL.md meets standards
python3 ~/.copilot/tools/validate-skill.py .github/skills/session-knowledge/SKILL.md
```

The generated SKILL.md must comply with `~/.copilot/tools/skills/references/skill-standards.md`.

### Step 5: Report

Present a summary showing: project profile, files created, enforcement status, and a test command.

## Reference files

Templates for generated files are in the `references/` directory:
- `references/instructions-template.md` — `.instructions.md` template
- `references/skill-template.md` — SKILL.md template

<example>
**Project:** Next.js e-commerce app

**Profile:** TypeScript | Next.js 14 | Wings: frontend, backend, database | Rooms: auth, products, cart, payments

**Generated files:**
1. `.github/instructions/session-knowledge.instructions.md` — imperative rules, auto-injected
2. `.github/skills/session-knowledge/SKILL.md` — full reference with e-commerce domain examples
3. `CLAUDE.md` patch — 3-line pointer to above files

**Validation:**
```bash
python3 ~/.copilot/tools/validate-skill.py .github/skills/session-knowledge/SKILL.md
# → PASS  .github/skills/session-knowledge/SKILL.md
```
</example>
