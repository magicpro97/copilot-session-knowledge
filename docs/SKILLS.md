# Skills & Templates

> Skills, agents, hooks, and project setup tools.

## Skills vs Agents — Important Distinction

This repo contains both **Skills** (SKILL.md) and **Agent templates** (.agent.md). They follow different specs:

| | Skills (SKILL.md) | Agents (.agent.md) |
|---|---|---|
| **Standard** | [Anthropic Agent Skills](https://github.com/anthropics/skills) | GitHub Copilot / Claude Code |
| **Purpose** | Instructions for specific tasks | Specialized sub-agent persona |
| **Frontmatter** | `name`, `description`, `license`, `allowed-tools`, `metadata`, `version` | `name`, `description`, `tools`, `model` |
| **Triggered by** | AI matching description to user intent | Explicit delegation or keyword match |
| **Validation** | `validate-skill.py` (local validator in this repo) | `hooks/lint-skills.py` (schema + tool-name rules, auto-parses CLI schemas) |

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
| `conductor-creator` | Generate a project-specific Conductor (task router) mapping tasks to workflows and skills |
| `project-onboarding` | Complete guide to set up the full AI-assisted development ecosystem for any project |
| `find-skills` | Discover and install agent skills from the registry |
| `agent-instructions-auditor` | Audit and improve agent instruction files |
| `forge-ecosystem` | Scaffold and manage app/game projects via forge CLI tools |
| `code-reviewer` | Skeptical, signal-over-noise code review (bugs, security, logic errors only) |
| `task-step-generator` | Generate structured STEPS.md for tasks too complex for one prompt |
| `karpathy-guidelines` | Behavioral guidelines to reduce common LLM coding mistakes (anti-overcomplication, surgical changes, verifiable success criteria) — vendored from [forrestchang/andrej-karpathy-skills](https://github.com/forrestchang/andrej-karpathy-skills); deployed to **both** Copilot CLI and Claude Code |

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
python3 ~/.copilot/tools/hooks/lint-skills.py /path/to/project               # Specific project
```

## SKILL.md Validator (`validate-skill.py`)

Validates `SKILL.md` files against the [Anthropic Agent Skills](https://github.com/anthropics/skills) standard.

```bash
python3 ~/.copilot/tools/validate-skill.py path/to/SKILL.md   # Single file
python3 ~/.copilot/tools/validate-skill.py path/to/skill-dir/ # Skill directory
```

To validate all agent and skill files in a project, use the agent/skill linter instead:

```bash
python3 ~/.copilot/tools/hooks/lint-skills.py --all            # All .agent.md and SKILL.md files
python3 ~/.copilot/tools/hooks/lint-skills.py /path/to/project
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

When deploying skills, `setup-project.py` copies each skill's `SKILL.md` **and** all auxiliary
asset subdirectories found alongside it (`references/`, `templates/`, `evals/`, or any other
subdirectory). This is intentionally generic: adding a new asset directory to a skill is
sufficient for `setup-project.py` to pick it up — no script change is needed. Relative links
inside `SKILL.md` (e.g. `references/foo.md`, `templates/bar.py`) resolve correctly after
deployment to `.github/skills/<skill-name>/`.

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

### Meta-skill rollout — global vs project scope

`setup-project.py` deploys all 14 skills to `.github/skills/<skill-name>/SKILL.md` in the target project (project scope). The full list of skills it deploys is defined in `INSTALL_ITEMS["skills"]` in that script — `host_manifest.py` is the authoritative host-metadata source and `setup-project.py` is the authoritative skill-list source.

**Vendored skills** (`karpathy-guidelines`) are deployed to **both** Copilot CLI (`.github/skills/`) and Claude Code (`.claude/skills/`) by `setup-project.py`. All other skills in `INSTALL_ITEMS["skills"]` are deployed to **Copilot CLI only** (`.github/skills/`). The `VENDORED_SKILLS` tuple in both `setup-project.py` and `auto-update-tools.py` is the authoritative list of dual-host skills.

Creator and meta-skills (session-knowledge-creator, agent-creator, hook-creator, tentacle-creator, tentacle-orchestration, workflow-creator, conductor-creator, project-onboarding, find-skills) are often promoted to **global scope** (`~/.copilot/skills/`) so they are available in every project without per-project deployment. When you do this:

1. **Remove the project-local copy** — having the same skill name at both global (`~/.copilot/skills/`) and project (`.github/skills/`) scope causes the skill to appear twice in the catalog. Copilot deduplicates by name at runtime, but the extra copy adds load.
2. **Audit after rollout** — manually inspect `~/.copilot/skills/` and `.github/skills/` to surface orphaned or duplicated entries. `hooks/lint-skills.py` is a schema linter (validates frontmatter fields), not a duplicate-surface audit tool.
3. **Do not deploy project-specific skills globally** — skills that reference project-specific paths, tags, or conventions belong at project scope only.

**Global auto-update for vendored skills (`karpathy-guidelines`):** if `~/.copilot/skills/karpathy-guidelines/SKILL.md` already exists (placed there manually), auto-update will keep it current whenever the source under `skills/karpathy-guidelines/` changes. This is **update-only** — it never creates a new global install from scratch; it only refreshes pre-existing files. This applies to **Copilot CLI scope only** (`~/.copilot/skills/`); a global `~/.claude/skills/` directory is not auto-updated. On WSL, auto-update also refreshes the current Windows user's Copilot CLI global install when that profile can be resolved, but that Windows-side install must already exist from a separate manual copy. To opt in, copy the skill manually to `~/.copilot/skills/karpathy-guidelines/` once; if you also want the WSL-driven Windows refresh path, manually copy it once into the resolved Windows profile's `.copilot/skills/karpathy-guidelines/` directory too.

## AI Agent Integration

Deploy the skill into your project for automatic knowledge-base usage:

```bash
python3 ~/.copilot/tools/install.py --deploy-skill
# → Creates .github/skills/session-knowledge/SKILL.md (Copilot CLI)
# → Creates .claude/skills/session-knowledge/SKILL.md (Claude Code)
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

## Context Load Guidance

> **Context load** is the total tokens Copilot injects into every tool call. High load degrades response quality and increases latency. The main culprits are duplicate skills and overly broad instruction surfaces — **not** the unified hook runner (which is a single process per event).

### Minimal-context-first

Start narrow, then escalate:

- Use `briefing.py --compact` (~500 tokens) before escalating to `--full` (~3 K tokens) for your own briefings. For sub-agent prompts, use `briefing.py --for-subagent` (~200 tokens) — it is the cheapest output mode.
- Instruction files with `applyTo: '**/*'` are injected into **every** context — scope them to specific file patterns (e.g., `**/*.ts`) when they only apply to certain files.
- Sub-agents should receive a focused `[KNOWLEDGE CONTEXT]` block via `briefing.py --for-subagent`, not the full KB dump.

### Progressive escalation

- Retrieve only what the current step needs. Full session history and semantic search are available on demand via `query-session.py` — do not inject them by default.
- For briefings: `--for-subagent` (~200 tokens) → `--compact` (~500 tokens) → `--full` (~3 K tokens) in order of cost.
- For searches: FTS keyword first; add `--semantic` only when keyword results are insufficient.

### Propagation discipline

When a skill or instruction is promoted to global scope, remove the project-local copy:

| Scope change | Action |
|---|---|
| Skill moved to `~/.copilot/skills/` | Delete `.github/skills/<name>/` in project |
| Instruction moved to `~/.github/instructions/` | Delete `.github/instructions/<file>` in project |
| Instruction already at user-level | Do not create a project-level copy with identical `applyTo` |

To surface stale copies, manually inspect `~/.copilot/skills/` and project `.github/skills/` directories and remove duplicates. Use `python3 ~/.copilot/tools/hooks/lint-skills.py --all` only for schema validation (frontmatter fields, deprecated flags, invalid tool names) — it does not detect duplicate or stale skill files.

## Host Scope

The tools in this repo are validated and supported on **Copilot CLI** and **Claude Code** only.

| Feature | Copilot CLI | Claude Code | Other hosts |
|---------|------------|-------------|-------------|
| Skill deployment (`--deploy-skill`) | ✅ `.github/skills/` | ✅ `.claude/skills/` | ❌ not supported |
| Hook deployment (`--deploy-hooks`) | ✅ `.copilot/hooks/` | ❌ not supported | ❌ not supported |
| Global instruction injection | ✅ `~/.github/copilot-instructions.md` | via CLAUDE.md | ❌ not supported |
| Session indexing | ✅ | ✅ via `claude-adapter.py` | ❌ not supported |

The canonical host metadata is centralised in `host_manifest.py`, which exports `SUPPORTED_HOSTS`,
`UNSUPPORTED_HOSTS`, and related manifest maps/tuples. It is consumed by `install.py`,
`setup-project.py`, `watch-sessions.py`, and `auto-update-tools.py`. The supported set is
intentionally restricted to Copilot CLI and Claude Code. Do **not** add Codex, Cursor, or other
hosts without documented session and hook formats.
