# Skills & Templates

> Skills, agents, hooks, and project setup tools.

## Skills vs Agents — Important Distinction

This repo contains both **Skills** (SKILL.md) and **Agent templates** (.agent.md). They follow different specs:

| | Skills (SKILL.md) | Agents (.agent.md) |
|---|---|---|
| **Standard** | [Anthropic Agent Skills](https://github.com/anthropics/skills) | GitHub Copilot / Claude Code |
| **Purpose** | Instructions for specific tasks | Specialized sub-agent persona |
| **Frontmatter** | `name`, `description`, `license`, `allowed-tools`, `metadata`, `compatibility` | `name`, `description`, `tools`, `model` |
| **Triggered by** | AI matching description to user intent | Explicit delegation or keyword match |
| **Validation** | `quick_validate.py` from anthropics/skills | `hooks/lint-skills.py` (14 rules, auto-parses CLI schemas) |

**Key rule:** Skills use `allowed-tools` (optional string). Agents use `tools` (YAML list). Don't mix them.

## Available Skills

| Skill | Purpose |
|-------|---------|
| `session-knowledge-creator` | Generate session-knowledge SKILL.md for new projects |
| `agent-creator` | Generate `.agent.md` files from 8 reference templates |
| `tentacle-creator` | Create tentacles for multi-agent orchestration |
| `tentacle-orchestration` | Map tentacles to phased workflows |
| `hook-creator` | Generate quality enforcement hooks (preToolUse/postToolUse) |
| `workflow-creator` | Create phased development workflows with quality gates |
| `find-skills` | Discover and install agent skills from the registry |
| `agent-instructions-auditor` | Audit and improve agent instruction files |
| `forge-ecosystem` | Scaffold and manage app/game projects via forge CLI tools |

## Hook Templates (`skills/hook-creator/references/`)

Pre-built Copilot CLI hook scripts:

| Hook | Type | Description |
|------|------|-------------|
| `dangerous-blocker.sh` | preToolUse | Blocks sudo, rm -rf /, force push, DB drops |
| `secret-detector.sh` | preToolUse | Blocks hardcoded API keys, tokens, private keys |
| `enforce-coding-standards.sh` | preToolUse | Blocks coding standard violations |
| `enforce-tdd-pipeline.sh` | preToolUse | Blocks task_complete without valid TDD evidence |
| `architecture-guard.sh` | preToolUse | Enforces layer boundaries (clean arch, KMP, etc.) |
| `commit-gate.sh` | preToolUse | Blocks commit until verification requirements met |
| `test-reminder.sh` | postToolUse | Reminds to write tests when creating source files |
| `build-reminder.sh` | postToolUse | Reminds to verify build after N source file edits |
| `docs-reminder.sh/.py` | postToolUse | Warns after 3+ code edits without doc updates |
| `session-banner.sh` | postToolUse | Shows session start checklist |

## Skill & Agent Linter (`hooks/lint-skills.py`)

Validates `.agent.md` and `SKILL.md` files against the Copilot CLI schema.

```bash
python3 ~/.copilot/tools/hooks/lint-skills.py path/to/file.agent.md    # Single file
python3 ~/.copilot/tools/hooks/lint-skills.py --all                     # All files
python3 ~/.copilot/tools/hooks/lint-skills.py --all --dir /path/to/project  # Specific project
```

## SKILL.md Validator (`validate-skill.py`)

Validates `SKILL.md` files against the [Anthropic Agent Skills](https://github.com/anthropics/skills) standard.

```bash
python3 ~/.copilot/tools/validate-skill.py path/to/SKILL.md   # Single file
python3 ~/.copilot/tools/validate-skill.py --all               # All skills
python3 ~/.copilot/tools/validate-skill.py path/to/SKILL.md --verbose  # Verbose
```

## Project Setup

```bash
python3 ~/.copilot/tools/setup-project.py              # Full setup
python3 ~/.copilot/tools/setup-project.py --skill-only  # Skills only
python3 ~/.copilot/tools/setup-project.py --dry-run     # Dry run
```

## AI Agent Integration

Deploy the skill into your project for automatic knowledge-base usage:

```bash
python3 ~/.copilot/tools/install.py --deploy-skill
# → Creates .github/skills/session-knowledge/SKILL.md (Copilot CLI)
# → Creates .claude/skills/session-knowledge.md (Claude Code)
```

### Enforce AI Usage (mandatory, not optional)

Skills are suggestions — AI agents can skip them. To **enforce** usage:

```bash
python3 ~/.copilot/tools/install.py --inject-global
```

This adds a `🧠 Session Knowledge — MANDATORY` section to `~/.github/copilot-instructions.md` with HTML markers for idempotent updates.

### Sub-agent Context Injection

Sub-agents don't access the knowledge base directly. The main agent injects context:

```bash
python3 ~/.copilot/tools/briefing.py "task description" --for-subagent
```

Output is a compact `[KNOWLEDGE CONTEXT]` block (~200 tokens) for sub-agent prompts.
