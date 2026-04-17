---
name: tentacle-orchestration
description: Break complex tasks into scoped parallel work units for multi-agent execution. Use when a task spans multiple modules or layers (API + DB + UI). Each tentacle runs strict-tdd-workflow internally. Not for simple single-module tasks — use strict-tdd-workflow directly instead. Trigger words — "orchestrate", "multi-agent", "parallel agents", "tentacle", "swarm", or any task touching 3+ files across different modules.
---

# Tentacle Orchestration

Break a complex task into scoped work units ("tentacles"), enrich each with context, then dispatch agents in parallel. Results persist in files so nothing is lost between agent boundaries.

Adapted from the [OctoGent](https://github.com/hesamsheikh/octogent) tentacle pattern.

> **Relationship with strict-tdd-workflow**: Tentacle is the **orchestrator** (splits work), strict-tdd is the **executor** (runs inside each tentacle). For single-module tasks, skip tentacle and use strict-tdd directly.

## When to use

| Scope | Approach |
|-------|----------|
| 1-2 files, single concern | Direct work — no tentacle needed |
| 3+ files, single module | Optional — tentacle helps track but not required |
| 3+ files, multiple modules | **Tentacle required** — decompose into scoped units |
| Multi-phase with agent delegation | **Tentacle required** — each delegated agent gets a tentacle |
| Bug investigation, multiple hypotheses | Tentacle recommended — one tentacle per hypothesis |

**Not a good fit:** strictly sequential single-file tasks, limited token budget, trivial edits.

## Anti-patterns

- ❌ SQL/markdown todos only for multi-agent work → agents lose scope isolation and CONTEXT.md
- ❌ Launching sub-agents without `swarm` prompt → agent gets no scope, constraints, or key files
- ❌ Skipping `--briefing` on create → past mistakes not injected into CONTEXT.md
- ❌ Skipping `complete` before `delete` → learnings from handoff.md lost permanently
- ❌ Treating tentacle Close as task done → return to the project's outer workflow after tentacles
- ❌ Overlapping tentacle scopes → agents overwrite each other's work

## Core concept

A **tentacle** is a scoped work unit stored as files:

```
.octogent/tentacles/<name>/
├── CONTEXT.md    ← What the agent needs to know (scope, constraints, key files)
├── todo.md       ← Checkbox items — each is a delegation unit
├── handoff.md    ← Agent writes results here when done
└── meta.json     ← Metadata (scope, status, timestamps)
```

The octopus metaphor: one orchestrator (you), multiple tentacles (agents), each handling a distinct code region.

<example>
**Task:** Add dark mode support to a Next.js app

**Decomposition:**
- `theme-tokens` tentacle — scope: `src/styles/tokens.css`, `tailwind.config.ts` — create CSS variables for dark/light palettes
- `component-update` tentacle — scope: `src/components/**/*` — apply `dark:` Tailwind classes to all components
- `test-suite` tentacle — scope: `tests/**/*` — write Playwright visual regression tests for dark mode

Each tentacle is independent, non-overlapping, and completable in isolation. The orchestrator merges results after all three pass verification gates.
</example>

## ⛔ Workflow integration

Tentacles are a decomposition tool, not a complete workflow. If the project has its own
workflow (e.g., WORKFLOW.md with phases like DESIGN → VERIFY → BUILD → TEST → REVIEW → QA → COMMIT),
tentacles run INSIDE the BUILD phase only. The outer workflow gates still apply before and after.

**Common violation:** AI completes tentacle's internal "Verify → Close" and marks task done,
skipping the project's E2E testing, code review, QA, and commit phases entirely. The tentacle's
verify phase only covers build+test — not the full quality pipeline.

**Rule:** After tentacle Close, return to the project's workflow and continue from the next phase.

## Internal workflow

The tentacle lifecycle has 5 phases: **Clarify → Plan → Execute → Verify → Close**.

Clarification is the most important phase. A bug found in spec costs 1x to fix. Found in code: 10x. Found in production: 100x. Never skip this phase — time invested here prevents entire categories of downstream waste.

### Phase 0: Clarify Spec (Steps 0.0–0.5)

This phase takes a raw specification and makes it implementation-ready through iterative Q&A. No planning or coding happens until the spec is CLEAN.

- **Step 0.0** (optional): Co-author the spec when user has no written spec — structured context gathering + iterative drafting
- **Steps 0.1–0.4**: Analyze spec against 8 quality dimensions, generate Spec Health Report, iterative refinement until CLEAN
- **Step 0.5**: Reader Testing — verify a fresh agent (no context) can correctly understand the spec

For the full process, see `references/spec-clarification.md`.

**Gate**: Planning on an unclear spec produces incorrect decomposition, wasted agent work, and rework. Never proceed to Phase 1 until the spec is CLEAN and reader-tested.

### Phase 1: Plan (Steps 1–4)

Use the CLEAN spec and its Impact Analysis / Risk Assessment to inform decomposition.

#### Step 1: Decompose the task into modules

Read the task description and identify independent code regions. Each region becomes one tentacle.

#### Step 2: Create tentacles

```bash
python3 ~/.copilot/tools/tentacle.py create <module-name> \
  --scope "<file-patterns>" \
  --desc "<short description>" \
  --briefing
```

The `--briefing` flag injects past mistakes and patterns from session-knowledge into CONTEXT.md — use it every time.

#### Step 3: Add todos

```bash
python3 ~/.copilot/tools/tentacle.py todo <name> add "<specific, atomic task>"
```

Each todo should be one deliverable — testable, reviewable, and completable in isolation.

#### Step 4: Enrich CONTEXT.md

Read reference files with `view`, then edit CONTEXT.md to add:
- **What exists**: describe the current code in the scope area
- **Key files**: full paths to reference files the agent needs
- **Constraints**: rules specific to this code region

This is the most important step. Agent quality is directly proportional to CONTEXT.md quality.

### Phase 2: Execute (Steps 5–6)

#### Step 5: Dispatch agents (swarm)

```bash
python3 ~/.copilot/tools/tentacle.py swarm <name> --agent-type <type> --model <model>
```

Use the output as the prompt for `task()`. Launch independent tentacles in parallel.

#### Step 6: Monitor progress

```bash
python3 ~/.copilot/tools/tentacle.py status
python3 ~/.copilot/tools/tentacle.py show <name>
```

### Phase 3: Verify (Steps 7–12)

Every step here catches a different class of agent error. For detailed gate descriptions (build, lint, test, review, docs, QA audit), see `references/verification-gates.md`.

Summary:

| Gate | What it catches | Skip when |
|------|----------------|-----------|
| **Build** | Syntax errors, type mismatches, import failures | Never skip |
| **Lint** | Style violations, unused imports, formatting | Never skip |
| **Test** | Logic bugs, regressions, broken contracts | Never skip |
| **Review** | Security issues, design flaws, scope creep | Never skip |
| **Docs** | Stale README, outdated JSDoc, missing CHANGELOG | Internal refactors only |
| **QA audit** | Hallucinated tests, spec mismatches, blind spots | Low-risk changes only |

The first 4 gates are mandatory. Skipping any of them means you don't know if the agent output is correct.

### Phase 4: Commit + Close (Steps 13–17)

#### Step 13: Commit after each completed phase

Commit working code after completing each major phase — not just at the end.
If a later phase fails or the session crashes, earlier work is preserved and rollback is possible.

```bash
git add -A && git commit -m "feat(<scope>): <phase description>"
```

**Commit cadence:**
- After Phase 1 shared/foundation tentacles complete + build passes → commit
- After each Phase 2 parallel batch completes + build passes → commit
- After Phase 3 verification passes → commit
- Final integration wiring → commit

Never commit from parallel sub-agents — only the orchestrator commits after merging results.

#### Step 14: Runtime verification

Build passing ≠ app works. After all tentacles are merged, run the app:

```bash
# Desktop: ./gradlew :composeApp:jvmRun
# Mobile: deploy to emulator/simulator
# Web: npm run dev / python manage.py runserver
```

DI frameworks (Koin, Dagger, Spring) crash at runtime if bindings are missing — the compiler
won't catch this. A 30-second launch test catches what build+test cannot.

#### Step 15: Complete and learn

```bash
python3 ~/.copilot/tools/tentacle.py complete <name>
```

Only call `complete` after all verification gates pass. This marks all todos done and auto-extracts learnings from handoff.md into long-term knowledge.

#### Step 16: Cleanup

```bash
python3 ~/.copilot/tools/tentacle.py delete <name>
```

## CLI reference

See `references/cli-reference.md` for the full command reference, CONTEXT.md template, and agent selection guidance.

Quick reference:

```bash
tentacle.py create <name> --scope "<paths>" --desc "<desc>" --briefing
tentacle.py todo <name> add "<task>"
tentacle.py swarm <name> --agent-type <type> --model <model>
tentacle.py status
tentacle.py complete <name>
tentacle.py delete <name>
```

## Tips

1. **Invest in CONTEXT.md** — 2-3 minutes writing good context saves 10 minutes of agent confusion
2. **Keep todos atomic** — each item = one testable deliverable
3. **No scope overlap** — overlapping scopes cause agents to overwrite each other
4. **Complete before delete** — `complete` saves learnings; `delete` alone loses them
5. **Commit after each phase** — uncommitted code is lost if the session crashes or compacts
6. **Run the app** — build+test ≠ works. Launch the app to verify DI resolution and runtime behavior
7. **⚠️ Shared workspace** — Sub-agents share the same filesystem, git index, and build cache. Parallel mode requires strictly non-overlapping file scopes. Never `git commit` from parallel agents. Consider `git worktree` for true isolation
