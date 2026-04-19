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
| `session-banner.sh` | postToolUse | Shows session start checklist |

> **Additional templates in `hooks/references/`:** `docs-reminder.sh` and its Windows-compatible
> Python companion `docs-reminder.py` (warns after 3+ code edits without doc updates) live in
> `hooks/references/`, not in `skills/hook-creator/references/`. `docs-reminder.py` is also the
> only `.py` companion among the bundled templates — all other templates above are `.sh`-only.

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
python3 ~/.copilot/tools/setup-project.py --profile python      # Python hooks + WORKFLOW.md
python3 ~/.copilot/tools/setup-project.py --profile typescript  # TypeScript hooks + WORKFLOW.md
python3 ~/.copilot/tools/setup-project.py --profile mobile      # Android/iOS/KMP hooks + WORKFLOW.md
python3 ~/.copilot/tools/setup-project.py --profile fullstack   # Full-stack web hooks + WORKFLOW.md
```

`--profile` installs a **preset hook bundle** and generates a starter `WORKFLOW.md`. Available
profiles are defined in `presets/` (`default`, `python`, `typescript`, `mobile`, `fullstack`).

### Creating custom profiles

Use `profile-builder.py` to build a new profile and save it to `presets/`:

```bash
python3 ~/.copilot/tools/profile-builder.py --list-hooks          # List available hook templates
python3 ~/.copilot/tools/profile-builder.py --list-phases         # List available workflow phases
python3 ~/.copilot/tools/profile-builder.py \
  --name myteam \
  --description "My team workflow" \
  --hooks dangerous-blocker.sh commit-gate.sh \
  --phases CLARIFY BUILD TEST COMMIT
```

### Sharing profiles (export / import)

Export profiles to JSON for sharing across machines or teams:

```bash
python3 ~/.copilot/tools/profile-export.py --profile python --output python.json
python3 ~/.copilot/tools/profile-export.py --all --output-dir ./exported/
python3 ~/.copilot/tools/profile-export.py --all --output all.bundle.json --format bundle
```

Import profiles shared by others:

```bash
python3 ~/.copilot/tools/profile-import.py --file custom-profile.json
python3 ~/.copilot/tools/profile-import.py --file bundle.json --name python  # one from bundle
python3 ~/.copilot/tools/profile-import.py --file custom.json --dry-run      # validate first
```

### Installing hooks standalone

Use `install-project-hooks.py` to install a hook bundle without the full project setup:

```bash
python3 ~/.copilot/tools/install-project-hooks.py --list-profiles   # List available profiles
python3 ~/.copilot/tools/install-project-hooks.py --profile python  # Install Python hooks
python3 ~/.copilot/tools/install-project-hooks.py --profile mobile --project /path/to/project
python3 ~/.copilot/tools/install-project-hooks.py --profile fullstack --workflow  # + WORKFLOW.md
python3 ~/.copilot/tools/install-project-hooks.py --dry-run         # Preview without changes
```

### Tentacle setup

`setup-project.py` handles tentacle orchestration setup automatically. The legacy `tentacle-setup.sh`
script is **deprecated** — prefer `setup-project.py` which covers tentacle setup and more in one step.
`tentacle-setup.sh` remains for backwards compatibility and simple shell-only environments.

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

## Host Scope

The tools in this repo are validated and supported on **Copilot CLI** and **Claude Code** only.

| Feature | Copilot CLI | Claude Code | Other hosts |
|---------|------------|-------------|-------------|
| Skill deployment (`--deploy-skill`) | ✅ `.github/skills/` | ✅ `.claude/skills/` | ❌ not supported |
| Hook deployment (`--deploy-hooks`) | ✅ `.copilot/hooks/` | ❌ not supported | ❌ not supported |
| Global instruction injection | ✅ `~/.github/copilot-instructions.md` | via CLAUDE.md | ❌ not supported |
| Session indexing | ✅ | ✅ via `claude-adapter.py` | ❌ not supported |

The `KNOWN_HOSTS` list in `install.py` and `setup-project.py` is intentionally restricted to
Copilot CLI and Claude Code. Do **not** add Codex, Cursor, or other hosts without documented
session and hook formats.
