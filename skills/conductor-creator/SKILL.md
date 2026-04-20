---
name: conductor-creator
description: Generate a project-specific Conductor task router. Use when setting up a new project, onboarding a codebase, creating routing rules, or making task-to-workflow-to-skill mapping deterministic. Run after agent-creator and workflow-creator.
---

# Conductor Creator

Generate a project-specific Conductor that routes every task to the right workflow,
skills, and agents — deterministically.

## When to Use

- Setting up a new project that needs deterministic task routing
- User mentions "create conductor", "setup task routing", or "generate routing rules"
- You already have agents and workflows, but routing is still ad hoc
- The same task is triggering different skills or workflows across sessions

## Why this exists

The Conductor is the coordination layer that ties everything together: skills on disk,
agents from AGENTS.md, and workflows from WORKFLOW.md. Without it, the AI re-derives
routing decisions every time, often inconsistently. Two identical tasks may get different
skill combinations in different sessions. The conductor eliminates this variance by
encoding routing logic as deterministic rules that produce transparent, auditable plans.

## Prerequisites

Run these creators FIRST so conductor-creator has inputs to work with:

```
1. agent-creator      → .github/agents/*.agent.md   (agent definitions)
2. workflow-creator    → WORKFLOW.md                  (phased workflow)
3. conductor-creator   → conductor-rules.json         (routing rules)
```

Order matters: conductor reads outputs from the first two.

## Workflow

### Step 1: Analyze the project

Run these in parallel to understand what you are working with:

```bash
# Skills inventory with descriptions
for d in .github/skills/*/; do
  name=$(basename "$d")
  desc=""
  if [ -f "$d/.skill-meta.json" ]; then
    desc=$(python3 -c "import json; print(json.load(open('$d/.skill-meta.json')).get('description','')[:80])" 2>/dev/null)
  elif [ -f "$d/SKILL.md" ]; then
    desc=$(grep -m1 '^description:' "$d/SKILL.md" 2>/dev/null | cut -d: -f2- | head -c80)
  fi
  printf "  %-30s %s\n" "$name" "$desc"
done

# Agents
cat AGENTS.md 2>/dev/null | head -60
ls .github/agents/ 2>/dev/null

# Workflows
cat .github/WORKFLOW.md 2>/dev/null | head -40
ls .github/skills/*workflow* .github/skills/*tdd* 2>/dev/null

# Tech stack
cat package.json 2>/dev/null | python3 -c "
import sys,json; d=json.load(sys.stdin)
print('name:', d.get('name'))
print('deps:', list(d.get('dependencies',{}).keys())[:15])
" 2>/dev/null

# Project structure
find . -maxdepth 3 -type d \
  -not -path '*/node_modules/*' -not -path '*/.git/*' \
  -not -path '*/dist/*' -not -path '*/cdk.out/*' | head -40
```

### Step 2: Build a project profile

| Field | What to extract |
|-------|----------------|
| Skills | All names + descriptions from `.skill-meta.json` or SKILL.md frontmatter |
| Agents | Names + roles from AGENTS.md or `.github/agents/` |
| Workflows | Phase names + gates from WORKFLOW.md or workflow skills |
| Tech stack | Language, framework, test runner, build tool |
| Module zones | Major directories that represent independent layers |
| Conventions | Branch naming, commit format, mandatory steps |

### Step 3: Generate `conductor-rules.json`

Start from the template at `references/rules-template.json`. Populate each section:

**task_types** — The 8 default types (feature, bug, refactor, ops, docs, analysis,
test, config) work for most projects. Customize by:
- Adding project-specific keywords (framework terms, domain language)
- Adding local-language keywords if the team uses non-English
- Adjusting negative_keywords for domain-specific conflicts

**phrase_priority** — Add rules for phrases that cause conflicts in YOUR project.
Common patterns: "review and fix" -> bug, "write test" -> test, "optimize X" -> refactor.

**multi_module_indicators** — Define zone keywords from the project profile:
```json
"zones": {
  "backend": ["lambda", "handler", "api", "server"],
  "frontend": ["screen", "component", "ui", "page"],
  "shared": ["shared", "utility", "model", "common"],
  "database": ["dynamodb", "repository", "query", "migration"]
}
```

**workflows** — Map from WORKFLOW.md phases or existing workflow skills:
- If WORKFLOW.md exists, translate each phase into the conductor format
- If strict-tdd-workflow or similar exists, reference it as the feature workflow
- Each workflow needs: `applies_to`, `phases`, `skip_conditions`

**skill_routing** — For each task type, categorize all discovered skills:
- `always`: skills needed for EVERY task of this type (e.g., coding-standards for features)
- `conditional`: skills triggered by specific keywords in the task description
- Use `|` for OR in conditional keys: `"dynamodb|repository"` maps to dynamodb skills
- Every skill should appear in at least ONE type's routing
- Skills that do not fit any type go to `_meta.intentionally_unrouted` with a reason

**agent_routing** — From AGENTS.md, map each agent to roles per task type:
```json
"feature": {
  "backend": {"build": "backend-dev", "test": "test-writer", "review": "code-reviewer"},
  "frontend": {"build": "frontend-dev", "test": "test-writer", "review": "code-reviewer"}
}
```

**mandatory_steps** — From project conventions:
- `pre_task`: briefing (if session-knowledge exists)
- `post_code`: review (if code-reviewer agent exists)
- `post_feature`: PR creation (if pr-workflow skill exists)
- `post_task`: learn (if session-knowledge exists)

### Step 4: Install the conductor engine

Copy the engine from `templates/conductor.py` (bundled with this skill).
The engine is generic — only `conductor-rules.json` is project-specific.

Core features the engine must support:
- Word-boundary keyword matching with suffix handling (s/es/ed/ing/er)
- Phrase priority rules (checked before keyword scoring)
- Confidence scoring (high/medium/low based on score gap)
- Multi-module zone detection
- CLI with `--verbose`, `--json`, `--audit`, `--sync`, `--override-type`

Place at: `.github/skills/conductor/scripts/conductor.py`

### Step 5: Create conductor SKILL.md

Generate `.github/skills/conductor/SKILL.md` documenting usage, how it works,
confidence levels, and customization instructions.

### Step 6: Create integration instructions

Create `.github/instructions/conductor-routing.instructions.md` with `applyTo: "**/*"`
so the conductor is auto-loaded into every AI context:

```markdown
## Before Starting ANY Task
Run: python3 .github/skills/conductor/scripts/conductor.py "<task>" --verbose
Read the output. Follow the workflow, skills, and agent recommendations.
```

### Step 7: Run --sync to verify

```bash
python3 .github/skills/conductor/scripts/conductor.py --sync
```

Expect: 0 new unrouted, 0 stale references, 100% coverage.
If gaps exist, use `--sync --fix` to auto-add missing skills.

### Step 8: Create tests

Write tests that verify:
- Each task type classifies correctly with sample descriptions
- Phrase priority rules resolve known conflicts
- Skill routing returns expected skills for each type
- Agent routing assigns correct agents and models
- Word boundary matching prevents false positives
- Orphan audit shows 0 unrouted skills

Run: `python3 .github/skills/conductor/scripts/test-conductor.py`

### Step 9: Report

Present summary: skills discovered, agents mapped, workflows configured,
coverage percentage, files created.

## Integration with Other Creators

| Creator | Output | Conductor Consumes |
|---------|--------|--------------------|
| agent-creator | `.github/agents/*.agent.md` | Agent names and roles for `agent_routing` |
| workflow-creator | `WORKFLOW.md` | Phase definitions for `workflows` |
| conductor-creator | `conductor-rules.json` | The routing brain itself |

Changes flow downstream: if agents change, re-run `--sync`. If workflows change,
update the `workflows` section in rules.json.

## Ongoing Maintenance

```bash
# After adding/removing skills
python3 conductor.py --sync

# Auto-fix routing gaps
python3 conductor.py --sync --fix

# Review all rules
python3 conductor.py --audit
```

<example>
**Project:** Full-stack TypeScript app with backend, frontend, and shared packages

**Inputs already present:**
- `.github/agents/*.agent.md`
- `.github/WORKFLOW.md`
- `.github/skills/*`

**User asks:** "Create a conductor so feature, bug, and docs tasks route consistently"

**What to generate:**
- `.github/skills/conductor/scripts/conductor.py`
- `.github/skills/conductor/scripts/conductor-rules.json`
- `.github/instructions/conductor-routing.instructions.md`

**Verification:**
- `python3 .github/skills/conductor/scripts/conductor.py --sync`
- `python3 .github/skills/conductor/scripts/test-conductor.py`
</example>
