---
name: project-onboarding
description: Complete guide to set up the full AI-assisted development ecosystem for any project. Use when joining a new project, bootstrapping AI tools, initializing Copilot, or onboarding a codebase so no creator, hook, workflow, or routing layer is missed.
---

# Project Onboarding

Set up the complete AI-assisted development ecosystem for any project.
Each phase builds on the previous one. **Skipping a phase leaves a gap
that compounds downstream.** Follow the phases in order.

## When to Use

- Joining a new codebase and you want the full Copilot/agent setup done in the right order
- User mentions "setup AI tools", "bootstrap project", "initialize copilot", or "onboard project"
- You want to ensure memory, hooks, workflows, tentacles, and conductor are all installed
- You need one canonical checklist instead of running creators piecemeal

## Why this matters

Without a structured onboarding, teams cherry-pick tools and miss critical
infrastructure. The AI has no memory across sessions, no guardrails against
dangerous commands, no workflow phases, and picks different skills each time.
This guide ensures every layer is in place before you write the first line of code.

Equally important: **deploying too much at once is its own failure mode.**
Over-broad instructions (`applyTo: "**/*"`) injected at every tool call, duplicate
skills across global and project surfaces, and routing rules that drift from what
is actually installed all compound into a bloated context that slows the AI and
masks real errors. This guide teaches both what to install and how to keep it lean.

## Staged Rollout Principles

Follow these principles throughout every phase. Violating them is how projects
end up with 100+ skills loaded simultaneously and 9 instructions firing on every
tool call.

1. **Minimal first.** Deploy only what the current phase needs. Add more only after
   the previous layer is verified clean. Resist installing "just in case" extras.

2. **Progressive escalation.** Each creator output is an input to the next. Do not
   run conductor-creator before you have agents and workflows to route — the rules
   it generates will be empty or wrong.

3. **Verify before advancing.** Each phase has an explicit verify step. Do not move
   on if the verify command returns errors or warnings. Fix the gap now; it compounds.

4. **Load budget awareness.** Every `applyTo: "**/*"` instruction fires on every tool
   call. Every global skill adds to the catalog the AI must scan. After setup, audit:
   - Instruction count: aim for ≤6 always-loaded instructions.
   - `applyTo` scope: narrow to the file types or paths that actually need the rule.
   - Skills: remove project-local copies of any skill that exists in `~/.copilot/skills/`.
   - Duplicates: if a skill name appears in both global and project surfaces, keep only one.

5. **Single source of truth.** If a skill is deployed globally (in `~/.copilot/skills/`),
   do not re-deploy it in `.github/skills/`. The project copy silently duplicates context
   without adding value.

## Overview

```
Phase 0: FOUNDATION  ->  Memory + Agents + Safety
Phase 1: PROCESS     ->  Workflows + Orchestration
Phase 2: ROUTING     ->  Conductor ties everything together
Phase 3: VERIFY      ->  Confirm zero gaps and healthy load budget
```

## Phase 0: Foundation

Run these three creators first. Everything else depends on agents, memory,
and guardrails being in place.

### 0.1 Session Knowledge

**Invoke:** `session-knowledge-creator` skill

**Output:** `briefing.py`, `learn.py`, `.instructions.md`

**Why it matters:** Without session knowledge, every session starts from zero.
The AI repeats mistakes, forgets past decisions, and has no institutional memory.
Briefing gives pre-task context; learn records post-task insights.

**Verify:** `python3 ~/.copilot/tools/briefing.py --wakeup` returns output.

### 0.2 Agent Creator

**Invoke:** `agent-creator` skill

**Output:** `.github/agents/*.agent.md`

**Why it matters:** Generic agents produce generic output. Specialized agents
encode your architecture, test framework, and domain knowledge.

**Verify:** `ls .github/agents/` shows 5-8 agent files.

### 0.3 Hook Creator

**Invoke:** `hook-creator` skill

**Output:** `.github/hooks/hooks.json` + `scripts/`

**Why it matters:** Hooks are the **strongest enforcement** mechanism.
They physically intercept and block violations before they happen. Unlike
skills (AI can ignore) or instructions (AI can rationalize skipping), hooks
run on every tool call. They prevent commits to protected branches, block
credential leaks, and guard auto-generated files.

**Verify:** `cat .github/hooks/hooks.json` lists preToolUse/postToolUse hooks.

## Phase 1: Process

With agents and safety in place, define **how work gets done.**

### 1.1 Workflow Creator

**Invoke:** `workflow-creator` skill

**Output:** `WORKFLOW.md` or a `strict-tdd-workflow` skill

**Why it matters:** Without phases, the AI jumps straight to coding before
understanding requirements, skips testing, and produces unreviewed output.
Workflows add blocking quality gates between phases.

**Verify:** A `WORKFLOW.md` or `.github/skills/*workflow*/SKILL.md` exists.

### 1.2 Tentacle Creator

**Invoke:** `tentacle-creator` skill

**Output:** `.github/skills/tentacle-orchestration/SKILL.md`

**Why it matters:** Tasks spanning multiple modules run serially without
orchestration. Tentacle breaks them into parallel work units with clear file
ownership so agents do not overwrite each other.

**Verify:** `.github/skills/tentacle-orchestration/SKILL.md` exists.

## Phase 2: Routing

The conductor connects **everything from Phase 0 and Phase 1** into a single
deterministic router.

### 2.1 Conductor Creator

**Invoke:** `conductor-creator` skill

**Output:**
- `.github/skills/conductor/scripts/conductor.py` (engine)
- `.github/skills/conductor/scripts/conductor-rules.json` (rules)
- `.github/instructions/conductor-routing.instructions.md` (auto-load)

**Why it matters:** Without a conductor, the AI re-derives routing every time,
picking different skills and workflows for the same task across sessions.
The conductor makes this deterministic: same input = same plan.

**Verify:**

```bash
python3 .github/skills/conductor/scripts/conductor.py --sync
# Expect: 0 new unrouted, 0 stale, 100% coverage
```

## Phase 3: Verify

Run these checks to confirm **zero gaps** in the setup AND a healthy load budget.

```bash
# 3.1 Sync check — routing rules match installed skills
python3 .github/skills/conductor/scripts/conductor.py --sync

# 3.2 Rule audit — no orphan or stale references
python3 .github/skills/conductor/scripts/conductor.py --audit

# 3.3 Test suite
python3 .github/skills/conductor/scripts/test-conductor.py

# 3.4 Smoke test
python3 .github/skills/conductor/scripts/conductor.py "implement user login" --verbose
python3 .github/skills/conductor/scripts/conductor.py "fix crash on startup" --verbose
```

### 3.5 Load Budget Audit

After conductor passes, audit the instruction and skill surfaces to prevent
context bloat from accumulating silently.

```bash
# Count always-loaded instructions (applyTo: **/* or no filter)
grep -rl 'applyTo.*\*\*/\*' .github/instructions/ ~/.github/instructions/ 2>/dev/null

# Find duplicate skill names across global and project
comm -12 \
  <(ls ~/.copilot/skills/ 2>/dev/null | sort) \
  <(ls .github/skills/ 2>/dev/null | sort)
# Any name printed here = duplicate. Remove the project copy if the global copy exists.

# List skills that are no longer referenced in conductor routing
# --sync reports coverage gaps; use it as the authoritative check:
python3 .github/skills/conductor/scripts/conductor.py --sync
# Any skill on disk not covered by a routing rule is flagged by --sync.
# To review all rules for stale references, run --audit and inspect the output manually.
```

**Healthy targets after onboarding:**

| Surface | Target |
|---------|--------|
| Always-loaded instructions (`applyTo: **/*`) | ≤ 6 total across global + project |
| Duplicate skills (same name in global + project) | 0 |
| Conductor orphans (skill on disk, not in rules) | 0 (or explicitly listed in `_meta.intentionally_unrouted`) |
| Conductor stale refs (rule references missing skill) | 0 |

## Quick Reference

| Phase | Creator | Output | Verify |
|-------|---------|--------|--------|
| 0.1 | `session-knowledge-creator` | briefing.py, learn.py | `briefing.py --wakeup` |
| 0.2 | `agent-creator` | .github/agents/*.agent.md | `ls .github/agents/` |
| 0.3 | `hook-creator` | .github/hooks/ | `cat hooks.json` |
| 1.1 | `workflow-creator` | WORKFLOW.md | File exists with phases |
| 1.2 | `tentacle-creator` | tentacle-orchestration | SKILL.md exists |
| 2.1 | `conductor-creator` | conductor-rules.json | `--sync` reports clean |
| 3 | Load audit | — | ≤6 always-loaded instr., 0 duplicate skills |

## Dependency Map

```
session-knowledge-creator ---+
                             |
agent-creator ---------------+
                             |
hook-creator ----------------+---> conductor-creator ---> READY
                             |
workflow-creator ------------+
                             |
tentacle-creator ------------+
```

All five creators feed into conductor-creator. The conductor is the
integration point that ties the ecosystem together.

## Ongoing Maintenance

| Event | Action |
|-------|--------|
| Added/removed a skill | `conductor.py --sync --fix` then re-run load audit |
| Added a new agent | Update `agent_routing` in conductor-rules.json |
| Changed workflow phases | Update `workflows` in conductor-rules.json |
| New session starts | `briefing.py --auto --compact` |
| After fixing a bug | `learn.py --mistake "Title" "Details" --tags "tags"` |
| After completing feature | `learn.py --feature "Title" "Details" --tags "tags"` |
| Skill installed globally | Remove project-local copy if it exists in `.github/skills/` |
| Instruction added | Check that `applyTo` is as narrow as possible; re-run load audit |

## Platform Notes

- **macOS/Linux:** Use `python3`. Paths use `/`.
- **Windows:** Use `python` instead. All scripts are cross-platform Python.
- **Copilot CLI:** Skills auto-discover via `.skill-meta.json`.
  Instructions auto-inject via `.instructions.md` with `applyTo` frontmatter.

## Troubleshooting

| Problem | Cause | Fix |
|---------|-------|-----|
| `--sync` shows orphans | New skill added after setup | `--sync --fix` |
| Agent uses wrong model | Model not specified | Check `.instructions.md` model rules |
| Hook blocks valid action | Overly strict regex | Edit `.github/hooks/scripts/` |
| Briefing returns empty | No entries recorded | Start using `learn.py` after tasks |
| Context feels slow / bloated | Too many `applyTo: **/*` instructions | Narrow `applyTo` on each instruction file |
| Same skill name in global + project | Old project copy not removed after global rollout | Delete `.github/skills/<name>/` when `~/.copilot/skills/<name>/` exists |
| Conductor rules reference missing skill | Skill removed but rule not updated | `conductor.py --audit`, then remove stale rule or restore skill |

<example>
**Project:** Existing Python backend with no AI scaffolding yet

**User asks:** "Onboard this project so future Copilot sessions have memory, hooks, workflows, and routing"

**Recommended order:**
1. `session-knowledge-creator`
2. `agent-creator`
3. `hook-creator`
4. `workflow-creator`
5. `tentacle-creator`
6. `conductor-creator`

**Expected result:**
- session memory tools installed
- project agents created
- hooks deployed
- workflow defined
- tentacle orchestration available
- conductor routing synced with zero gaps
- load audit: ≤6 always-loaded instructions, 0 duplicate skill names, 0 conductor orphans

**Propagation note:** If any of the above meta-skills are already installed globally
(in `~/.copilot/skills/`), skip re-deploying them to `.github/skills/`. The project
should extend, not duplicate, the global layer.
</example>
