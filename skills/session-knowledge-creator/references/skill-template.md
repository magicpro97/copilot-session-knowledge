# `SKILL.md` Template — Session Knowledge

> **Copy this file to** `.github/skills/session-knowledge/SKILL.md` in the target project.
> Replace every `<PLACEHOLDER>` with project-specific values before saving.
> The generated file must pass `python3 ~/.copilot/tools/validate-skill.py`.

---

```markdown
---
name: session-knowledge
description: >-
  Search past <PROJECT_NAME> Copilot/Claude sessions before complex tasks.
  Run briefing.py for relevant mistakes, patterns, decisions from <DOMAIN> work.
  Use when starting <KEY_ACTIVITY_1>, debugging <KEY_ACTIVITY_2>,
  or making architecture decisions about <KEY_ACTIVITY_3>.
  Activated by keywords: briefing, past sessions, knowledge base, <KEY_TAG_1>, <KEY_TAG_2>.
---

# Session Knowledge — <PROJECT_NAME>

Past mistakes and patterns from <PROJECT_NAME> (<LANGUAGE> / <FRAMEWORK>) are indexed
in a local knowledge base. Use these tools to avoid repeating errors and reuse
proven solutions specific to this codebase.

## When to Use

- **Starting a complex task** → run briefing.py to surface relevant past experience
- **Hitting an unfamiliar error** → search for the error message
- **Making a design decision** → check --decisions for past architectural choices
- **Working in <KEY_MODULE_1> or <KEY_MODULE_2>** → especially worth a briefing

**Skip** for trivial changes (renaming variables, formatting, etc.)

## Core Commands

```bash
# Before task — get context
python3 ~/.copilot/tools/briefing.py "<task description>" --compact

# Search for a specific error
python3 ~/.copilot/tools/query-session.py "<error message>" --verbose

# See past decisions about <TECH>
python3 ~/.copilot/tools/query-session.py "<TECH>" --decisions

# After fixing a bug — record it
python3 ~/.copilot/tools/learn.py --mistake "Title" "Root cause and fix" \
  --tags "<KEY_TAG_1>,<KEY_TAG_2>" --wing <WING> --room <ROOM>

# After implementing a feature
python3 ~/.copilot/tools/learn.py --feature "Title" "What was built" \
  --tags "<KEY_TAG_1>"
```

## Wing / Room Mappings

Knowledge is organized hierarchically. Use these when recording entries:

| Wing | Rooms | What belongs here |
|------|-------|-------------------|
| `<WING_1>` | `<ROOM_1_A>`, `<ROOM_1_B>` | <WING_1_DESCRIPTION> |
| `<WING_2>` | `<ROOM_2_A>`, `<ROOM_2_B>` | <WING_2_DESCRIPTION> |
| `<WING_3>` | `<ROOM_3_A>`, `<ROOM_3_B>` | <WING_3_DESCRIPTION> |

## Interpreting Results

- **`[mistake]`** — what went wrong; read carefully to avoid repeating
- **`[pattern]`** — proven solutions for <PROJECT_NAME>; apply directly
- **`[decision]`** — past architectural choices; check if still valid
- **`[tool]`** — configurations specific to <FRAMEWORK>
- **Confidence < 0.5** — verify before using

<example>
**Task:** Fix <KEY_ACTIVITY_1> bug in <KEY_MODULE_1>

```bash
python3 ~/.copilot/tools/briefing.py "fix <KEY_ACTIVITY_1> in <KEY_MODULE_1>"
# → shows 1 past mistake about <DOMAIN_ISSUE>, 1 pattern for <DOMAIN_SOLUTION>

python3 ~/.copilot/tools/query-session.py "<ERROR_TYPE>" --verbose
# → finds the root cause from a previous session

# After fixing:
python3 ~/.copilot/tools/learn.py --mistake "<KEY_MODULE_1> <ERROR_TYPE>" \
  "Root cause: <CAUSE>. Fix: <FIX_DESCRIPTION>" \
  --tags "<KEY_TAG_1>,<KEY_TAG_2>" --wing <WING_1> --room <ROOM_1_A>
```
</example>
```

---

## How to customize this template

| Placeholder | What to put there |
|-------------|-------------------|
| `<PROJECT_NAME>` | Actual project name |
| `<LANGUAGE>` | e.g. TypeScript, Kotlin, Python |
| `<FRAMEWORK>` | e.g. Next.js, Spring Boot, Django |
| `<DOMAIN>` | Short domain description (e.g. "e-commerce", "mobile health") |
| `<KEY_ACTIVITY_1/2/3>` | Common developer activities in this project |
| `<KEY_MODULE_1/2>` | Most-touched modules/directories |
| `<KEY_TAG_1/2>` | Tags derived from the module/tech stack |
| `<WING_N>` | Top-level architecture layer |
| `<ROOM_N_X>` | Sub-module within the wing |
| `<WING_N_DESCRIPTION>` | One-line description of what goes in that wing |
| `<DOMAIN_ISSUE/SOLUTION>` | Domain-specific example for the `<example>` block |
| `<ERROR_TYPE>` | Realistic error type for the project |
| `<CAUSE>/<FIX_DESCRIPTION>` | Example root cause / fix language |

Remove the outer markdown fence before saving. The file must start with `---` (YAML frontmatter).
