---
name: tentacle-orchestration
description: Break complex tasks into scoped parallel work units for multi-agent execution. Always use task-step-generator first as a reviewed planning scaffold, then adapt the reviewed steps into tentacles. Use when a task spans multiple modules or layers, needs agent delegation, or the user says "orchestrate", "multi-agent", "parallel agents", "tentacle", or "swarm". Each implementation/fix tentacle runs strict-tdd-workflow internally. Features Opus Leader Council for quality-first multi-platform work.
---

# Tentacle Orchestration — copilot-session-knowledge

Break a complex task into scoped work units ("tentacles"), enrich each with context, then dispatch agents in parallel. Results persist in files so nothing is lost between agent boundaries.

Adapted from the [OctoGent](https://github.com/hesamsheikh/octogent) tentacle pattern. Customized for **copilot-session-knowledge**: a multi-platform hybrid of Python stdlib tools, Next.js/React browse-ui, and a Rust binary — where **quality outweighs speed**.

> **Relationship with strict-tdd-workflow**: Tentacle is the **orchestrator** (splits work), strict-tdd is the **executor** (runs inside each implementation/fix tentacle). For single-module tasks, skip tentacle and use strict-tdd directly.
>
> **Relationship with task-step-generator**: `task-step-generator` is the **planning scaffold**. Run it before creating tentacles, then review and edit the generated steps. Do not copy generated steps blindly.

---

## ⚡ Opus Leader Council (Project-Specific Pattern)

This project uses **Opus-class Leader agents** instead of flat swarming. Each leader owns a domain and cannot be bypassed. Leaders discuss before acting and escalate to peers when stuck.

### Leader Roster

| Leader | Model | Domain | Scope |
|--------|-------|--------|-------|
| **dev-leader** | `claude-opus-4.7` | Python tools, hooks, Rust binary | `*.py`, `hooks/**/*`, `crates/**/*` |
| **test-leader** | `claude-opus-4.7` | All test surfaces (Python + TypeScript + Rust) | `test_*.py`, `run_all_tests.py`, `browse-ui/src/**/*.test.*`, `browse-ui/e2e/**/*` |
| **qa-leader** | `claude-opus-4.7` | Verification gates, cross-surface sync, security | All changed surfaces |
| **browse-leader** | `claude-opus-4.7` | Next.js/React browse-ui frontend | `browse-ui/src/**/*`, `browse-ui/e2e/**/*` |
| **research-leader** | `claude-opus-4.7` | Any question with confidence < 1.0 | Read-only, all surfaces |

### Leader Dispatch Pattern

```bash
# Each leader gets its own tentacle with --model claude-opus-4.7
sk tentacle create dev-<feature> --scope "*.py hooks/**/*" --desc "Python implementation" --briefing
sk tentacle create test-<feature> --scope "test_*.py run_all_tests.py" --desc "Test coverage" --briefing
sk tentacle create qa-<feature> --scope "." --desc "Verification + cross-surface sync" --briefing

# Dispatch leaders in parallel — all use opus
sk tentacle swarm dev-<feature> --agent-type general-purpose --model claude-opus-4.7 --briefing
sk tentacle swarm test-leader --agent-type general-purpose --model claude-opus-4.7 --briefing
sk tentacle swarm qa-leader --agent-type verification-gate --model claude-opus-4.7 --briefing
```

### Leader Peer-Discussion Protocol

When a leader is stuck or has confidence < 1.0, it does NOT stop — it requests peer input:

1. **dev-leader stuck on architecture** → write question to `handoff.md` with `STATUS: PEER_DISCUSS`, dispatch `research-leader` tentacle with the question
2. **test-leader unsure of coverage strategy** → write question, dispatch `dev-leader` review, loop until both agree
3. **qa-leader finds gate failure** → write failure report, dispatch `dev-leader` fix tentacle, loop back through qa-leader

```bash
# Peer discussion: dev-leader writes question, research-leader answers
sk tentacle handoff dev-<feature> "Architecture question: [question]" --status AMBIGUOUS
sk tentacle create research-<question> --scope "." --desc "Research: [question]" --briefing
sk tentacle swarm research-<question> --agent-type research-planner --model claude-opus-4.7 --briefing
# After research-leader answers → continue dev-leader
sk tentacle resume dev-<feature>
```

---

## ♾️ Infinite Confidence Loop Protocol

**This project never marks BLOCKED when confidence < 1.0.** Instead, it loops until certainty.

### The Loop

```
CONFIDENCE CHECK
      │
      ▼
  ≥ 1.0? ──YES──► Proceed to execution
      │
      NO
      │
      ▼
  Split ambiguity into atomic questions
      │
      ▼
  Dispatch research-leader tentacle(s) on claude-opus-4.7
      │
      ▼
  Research completes, evidence recorded
      │
      ▼
  Re-evaluate confidence ───────────────► back to top
      │
  (loop forever until ≥ 1.0 or user explicitly overrides)
```

### Implementation

```bash
# Step 1: Spawn research tentacle for each ambiguous question
sk tentacle create research-q1 --scope "." --desc "Research: <question 1>" --briefing
sk tentacle swarm research-q1 --agent-type research-planner --model claude-opus-4.7 --briefing

# Step 2: After research, record evidence and re-evaluate
sk tentacle goal gate pass research-q1 --reason "Evidence: <summary>"

# Step 3: Only proceed when ALL research gates pass
sk tentacle goal criteria check
# If not met: loop back to Step 1 with remaining questions
# If met: proceed to Plan phase
```

**Override rule**: Only the human operator can exit the loop early. Record the override with rationale:
```bash
sk tentacle goal gate pass override --reason "Human override: <rationale>"
```

---

## Planning Discipline

Use this sequence before creating any tentacle:

1. Generate a step file with `task-step-generator` (`.github/steps/<task-slug>.md`).
2. Review the generated step file with `references/decomposition-review.md`.
3. Record what was accepted, edited, and rejected before dispatching agents.
4. Convert only the reviewed steps into non-overlapping tentacles and atomic todos.

Why: decomposition and checklists reduce avoidable cognitive load, but generated plans can anchor on the first plausible split. Treat the step file as a draft planning artifact, not as authority.

---

## Decision Confidence Gate

Before creating, dispatching, merging, deleting, or closing tentacles, verify the routing/plan
confidence. Any confidence below `1.0` is not "good enough"; it means the orchestrator is still
guessing. Split the noisy point into focused research concerns and dispatch independent research
or validation agents first.

Required behavior when confidence `< 1.0`:

1. Stop implementation/deletion/merge decisions for the uncertain scope.
2. Split the ambiguity into atomic questions: task type, scope boundaries, dependencies, acceptance evidence, and affected systems.
3. Dispatch research/validation tentacles on **`claude-opus-4.7`** (opus-class mandatory, no exceptions).
4. Record the evidence and rejected alternatives in the tentacle `handoff.md`.
5. Continue only after the synthesized decision reaches confidence `1.0`, or after an explicit user override is recorded with its rationale.
6. **Never mark BLOCKED** — instead create a `research-<topic>` tentacle, loop, and resume when certain.

Why this gate exists: low-confidence orchestration creates the worst kind of parallelism — many agents confidently doing the wrong work. Research-first decomposition is cheaper than unwinding a bad swarm.

---

## When to use

| Scope | Approach |
|-------|----------|
| 1-2 files, single concern | Direct work — no tentacle needed |
| 3+ files, single module | Optional — tentacle helps track but not required |
| 3+ files, multiple modules | **Tentacle required** — decompose into scoped units |
| Multi-phase with agent delegation | **Tentacle required** — each delegated agent gets a tentacle |
| Bug investigation, multiple hypotheses | Tentacle recommended — one tentacle per hypothesis |
| Cross-surface (Python + browse-ui + Rust) | **Opus Leader Council required** — one leader per surface |
| Confidence < 1.0 at any point | **Research tentacle required** — loop until certain |

**Not a good fit:** strictly sequential single-file tasks, limited token budget, trivial edits.

---

## Sub-agent Guardrails

These guardrails apply to dispatched sub-agents. The **commit restriction is enforced at the
git level** when hooks are installed.

**How enforcement works:**
1. `tentacle.py create` generates a UUID `tentacle_id` stored in the tentacle's `meta.json`.
2. `hooks/pre-commit` and `hooks/pre-push` call `hooks/check_subagent_marker.py`, which blocks git operations while the marker is fresh.
3. `sk tentacle complete <name>` removes the matching marker entry.

**Install the git hooks** (once per repository):

```bash
sk install --install-git-hooks
# fallback: python3 ~/.copilot/tools/install.py --install-git-hooks
```

| Convention | What to do |
|------------|-----------|
| **Commit restriction** | Do not run `git commit` or `git push`. Both are blocked at the filesystem level while `dispatched-subagent-active` marker is fresh. |
| **Stay in scope** | Do not edit files outside your tentacle's declared `scope`. |
| **Escalate, don't expand** | If scope is insufficient, write the gap to `handoff.md` and stop. |
| **No over-implementation** | Implement only what your todos specify. |
| **Handoff before stopping** | Always write a structured handoff: `sk tentacle handoff <name> "<summary>" --status DONE --changed-file <path> --learn` |
| **No platform `create` for reports** | Use `tentacle.py handoff` to persist output to `handoff.md`. |

---

## Anti-patterns

- ❌ SQL/markdown todos only for multi-agent work → agents lose scope isolation
- ❌ Launching sub-agents without `swarm` prompt → no scope, constraints, or key files
- ❌ Skipping `--briefing` → past mistakes not injected into CONTEXT.md
- ❌ Skipping `complete` before `delete` → learnings from handoff.md lost permanently
- ❌ Overlapping tentacle scopes → agents overwrite each other's work
- ❌ Creating tentacles from intuition without a generated-and-reviewed step file
- ❌ Copying `task-step-generator` output blindly without checking dependencies
- ❌ Skipping the runtime bundle on multi-agent work → no `recall-pack.json`
- ❌ Sub-agent commits or pushes → blocked by git hooks when installed
- ❌ Sub-agent edits files outside declared scope → silent conflicts
- ❌ Treating confidence `< 1.0` as acceptable → use opus research loop, never skip
- ❌ **Marking BLOCKED when stuck** → loop via peer leader discussion instead
- ❌ **Using haiku/sonnet for leader agents** → all leaders must use `claude-opus-4.7`
- ❌ **Skipping test-leader** → every feature/fix needs test coverage verified by test-leader
- ❌ **qa-leader bypassed on cross-surface changes** → Python + browse-ui + Rust changes always need qa-leader
- ❌ Accepting sub-agent claims of "tests pass" without running commands → unverified claims are not evidence
- ❌ Closing a tentacle `DONE` with no verification evidence → treated as `AMBIGUOUS`
- ❌ Sub-agent uses the platform `create` file-creation tool for research output → use `tentacle.py handoff`

---

## Core concept

A **tentacle** is a scoped work unit stored as files:

```
.octogent/tentacles/<name>/
├── CONTEXT.md    ← What the agent needs to know (scope, constraints, key files)
├── todo.md       ← Checkbox items — each is a delegation unit
├── handoff.md    ← Agent writes results here when done
├── meta.json     ← Metadata (scope, status, timestamps)
└── bundle/       ← Runtime context artifacts (created by dispatch by default)
    ├── manifest.json
    ├── session-metadata.md
    ├── recall-pack.json
    ├── briefing.md
    ├── instructions.md
    └── skills.md
```

The octopus metaphor: one orchestrator (you), multiple tentacles (agents), each handling a distinct code region.

<example>
**Task:** Add token cost display to browse-ui (multi-surface: Python API + React component + Vitest tests)

**Confidence check:** Python API shape? → confidence 0.8 → spawn research-leader tentacle first

**After research (confidence 1.0):**

**Opus Leader decomposition:**
- `dev-leader-cost-api` — scope: `browse/routes/*.py`, `browse/api/*.py` — add /api/session/cost endpoint
- `browse-leader-cost-ui` — scope: `browse-ui/src/components/**/*` — React cost display component
- `test-leader-cost` — scope: `test_*.py`, `browse-ui/src/**/*.test.*` — Python unit tests + Vitest tests
- `qa-leader-cost` — scope: `.` — verify cross-surface sync, run all gates

**Dispatch order:** dev-leader + browse-leader in parallel → test-leader → qa-leader → commit

Each leader uses `--model claude-opus-4.7`.
</example>

---

## Internal workflow

The workflow has 5 phases: **Clarify → Plan → Execute → Verify → Close**.

Clarification is the most important phase. A bug found in spec costs 1x to fix. Found in code: 10x. Found in production: 100x. Never skip this phase.

### Phase 0: Clarify Spec (Steps 0.0–0.5)

This phase takes a raw specification and makes it implementation-ready through iterative Q&A. No planning or coding happens until the spec is CLEAN.

- **Step 0.0** (optional): Co-author the spec when user has no written spec
- **Steps 0.1–0.4**: Analyze spec against 8 quality dimensions, generate Spec Health Report, iterative refinement until CLEAN
- **Step 0.5**: Reader Testing — verify a fresh agent (no context) can correctly understand the spec

For the full process, see `references/spec-clarification.md`.

**Gate**: Never proceed to Phase 1 until the spec is CLEAN and reader-tested.

**Multi-platform note**: For cross-surface tasks, clarification must identify which surfaces are affected (Python / browse-ui / Rust) so the leader mapping is correct before any planning.

### Phase 1: Plan

Use the CLEAN spec and its Impact Analysis / Risk Assessment to inform decomposition.

#### Plan A: Generate a step file

Use `task-step-generator` before creating tentacles:

```text
Generate a step file for this task. Include CLARIFY, RED evidence/test strategy, BUILD, TEST, REVIEW, LOOP-EVAL, and COMMIT/CLOSE.
```

#### Plan B: Review and edit the generated steps

Apply `references/decomposition-review.md`. Verify:
- acceptance signal is observable,
- RED evidence/test strategy exists before implementation,
- dependencies are ordered before parallel work,
- steps are small enough to review in one context,
- each step maps to the correct **Opus Leader** agent type.

#### Plan C: Confidence check before decomposition

```bash
# For each ambiguous point in the step file:
sk tentacle create research-<topic> --scope "." --desc "Research: <topic>" --briefing
sk tentacle swarm research-<topic> --agent-type research-planner --model claude-opus-4.7 --briefing
# Wait for handoff, evaluate evidence, repeat until confidence = 1.0
sk tentacle goal gate pass research-<topic> --reason "Evidence: <summary>"
```

#### Plan D: Map steps to Opus Leaders

For each tentacle, assign an appropriate leader model:

| Tentacle type | agent_type | Model | Scope pattern |
|--------------|-----------|-------|--------------|
| Python dev (tools/hooks) | general-purpose | `claude-opus-4.7` | `*.py`, `hooks/**/*` |
| Python dev (browse backend) | python-browse-backend | `claude-opus-4.7` | `browse/**/*.py` |
| browse-ui frontend | browse-ui-host-state | `claude-opus-4.7` | `browse-ui/src/**/*` |
| Browse-ui tests (Vitest/Playwright) | general-purpose | `claude-opus-4.7` | `browse-ui/src/**/*.test.*`, `browse-ui/e2e/**/*` |
| Python tests | general-purpose | `claude-opus-4.7` | `test_*.py`, `run_all_tests.py` |
| Security/auth review | browser-security-reviewer | `claude-opus-4.7` | `browse/**/*`, `browse-ui/src/**/*` |
| Research / architecture decisions | research-planner | `claude-opus-4.7` | Read-only, all |
| Cross-surface verification | verification-gate | `claude-opus-4.7` | All changed surfaces |
| Whole-app impact audit | whole-app-impact-auditor | `claude-opus-4.7` | All |
| Docs / skills / hooks | general-purpose | `claude-opus-4.7` | `docs/**/*`, `.github/**/*` |

#### Plan E: Create tentacles

```bash
sk tentacle create <name> \
  --scope "<file-patterns>" \
  --profile "<agent-profile-id>" \
  --desc "<short description>" \
  --briefing
```

Available profiles (from `.github/agents/`):
- `browse-ui-host-state` — browse-ui host/provider work
- `python-browse-backend` — Python backend routes
- `browser-security-reviewer` — security review
- `research-planner` — research and architecture
- `verification-gate` — gate verification
- `whole-app-impact-auditor` — cross-surface impact

#### Plan F: Add todos

```bash
sk tentacle todo <name> add "<specific, atomic task>"
```

#### Plan G: Enrich CONTEXT.md

Add to each tentacle's CONTEXT.md:
- **Step-plan review**: accepted/edited/rejected steps, confidence evidence
- **Multi-platform context**: which surfaces are touched and why
- **Key files**: full paths to reference files
- **Constraints**: project rules (stdlib-only Python, parameterized SQL, no pickle, etc.)
- **Verification requirement**: what commands prove this tentacle DONE

---

### Phase 2: Execute (Steps 5–6)

#### Step 5: Dispatch Opus Leaders (swarm)

```bash
# Always use claude-opus-4.7 for all leaders
sk tentacle swarm <name> --agent-type <type> --model claude-opus-4.7 --briefing
sk tentacle swarm <name> --output parallel --briefing

# Example: multi-surface feature
# Wave 1 (parallel): dev + browse
sk tentacle swarm dev-<feature> --agent-type general-purpose --model claude-opus-4.7 --briefing &
sk tentacle swarm browse-<feature> --agent-type browse-ui-host-state --model claude-opus-4.7 --briefing &
wait

# Wave 2 (after Wave 1): tests
sk tentacle swarm test-<feature> --agent-type general-purpose --model claude-opus-4.7 --briefing

# Wave 3 (after Wave 2): QA gate
sk tentacle swarm qa-<feature> --agent-type verification-gate --model claude-opus-4.7 --briefing
```

Every implementation tentacle must execute strict-TDD internally: define failing evidence first, make the smallest change, prove criterion turns green.

#### Step 6: Monitor progress

```bash
sk tentacle status
sk tentacle show <name>
```

If a leader reports `AMBIGUOUS` or `PEER_DISCUSS`, dispatch a research or peer tentacle immediately — do not wait:

```bash
# Leader is stuck: dispatch research immediately
sk tentacle create research-<blocker> --scope "." --desc "Research: <blocker>" --briefing
sk tentacle swarm research-<blocker> --agent-type research-planner --model claude-opus-4.7 --briefing
# After research resolves: resume the stuck leader
sk tentacle resume <stuck-tentacle>
```

---

### Phase 3: Verify (Steps 7–12)

Every step catches a different class of agent error.

| Gate | What it catches | Command | Skip when |
|------|----------------|---------|-----------|
| **Python build** | Syntax, import failures | `python3 -c "import ast; ast.parse(open('file.py').read())"` | Never |
| **Python lint** | Ruff violations | `ruff check *.py hooks/**/*.py` | Never |
| **Python tests** | Logic bugs, regressions | `python3 test_security.py && python3 test_fixes.py` | Never |
| **browse-ui typecheck** | TypeScript errors | `cd browse-ui && pnpm typecheck` | Never (if browse-ui touched) |
| **browse-ui lint** | ESLint violations | `cd browse-ui && pnpm lint` | Never (if browse-ui touched) |
| **browse-ui format** | Prettier violations | `cd browse-ui && pnpm format:check` | Never (if browse-ui touched) |
| **browse-ui test** | Vitest failures | `cd browse-ui && pnpm test` | Never (if browse-ui touched) |
| **browse-ui build** | Next.js build | `cd browse-ui && pnpm build` | Never (if browse-ui touched) |
| **Rust** | Compile + clippy | `cargo fmt --check && cargo clippy -- -D warnings && cargo test` | If Rust untouched |
| **Review** | Security, design flaws | `code-reviewer` agent | Never |
| **Docs sync** | Stale docs | Check `docs/` matches changed behavior | Internal refactors only |
| **QA audit** | Hallucinated tests, blind spots | `qa-leader` tentacle | Low-risk only |

**Verification surface matrix** (from `copilot-instructions.md`):

| Surface | Required evidence |
|---------|-------------------|
| Python | `python3 test_security.py && python3 test_fixes.py` |
| Hooks/docs/skills | `python3 tests/test_quality_gates.py` |
| browse-ui | `pnpm typecheck && pnpm lint && pnpm format:check && pnpm test && pnpm build` |
| Rust | `cargo fmt --all -- --check && cargo clippy -- -D warnings && cargo test` |
| remote-terminal | `npm test && npm run lint && npm run lint:clean` |

**Evidence requirement:** Run commands yourself. Record output. "Tests pass" is not evidence — the command output is.

---

### Phase 3.5: Goal Evaluation Loop

After all verification gates pass, evaluate whether the overarching goal is met. This is the **loop-until-verified** phase.

```bash
# Run success-criteria check and persist result
sk tentacle verify <name> "<success-criteria-command>" --label "goal-eval"
sk tentacle goal criteria check
sk tentacle goal eval --decision continue|complete
```

| Result | Action |
|--------|--------|
| Goal met — all success criteria satisfied | Proceed to Phase 4 |
| Goal partially met — gaps identified | Return to Phase 1, create new leader tentacles for gaps |
| Goal blocked by external dependency | Surface to user. **Do not exit loop without user decision.** |

**Loop rules:**
1. Success criteria defined in Phase 1, not invented during evaluation.
2. The orchestrator owns the loop — sub-agents report via handoff and stop.
3. Create **new tentacles** for remaining gaps; do not re-open completed tentacles.
4. Record evidence for every evaluation using `tentacle.py verify`.
5. Do not infer goal status from handoff prose — run the command.

---

### Phase 4: Commit + Close (Steps 13–17)

#### Step 13: Commit (orchestrator only)

```bash
git add -A && git commit -m "feat(<scope>): <description>

Co-authored-by: Copilot <223556219+Copilot@users.noreply.github.com>"
```

Commit cadence:
- After Phase 1 foundation tentacles + build passes → commit
- After each parallel batch + build passes → commit
- After Phase 3 verification → commit
- Final integration → commit

#### Step 14: Runtime verification

```bash
# Python browse backend
python3 browse.py --dev &
curl -s http://localhost:8765/healthz | jq .

# browse-ui
cd browse-ui && pnpm dev &
# Check browser: http://localhost:3000
```

#### Step 15: Complete and learn

```bash
sk tentacle complete <name>
```

Always call `complete` before `delete`. `complete` auto-extracts learnings into long-term knowledge.

#### Step 16: Resume an interrupted tentacle

```bash
sk tentacle resume <name>             # Refresh briefing, mark active
sk tentacle resume <name> --no-briefing  # Skip if briefing is fresh
```

#### Step 17: Cleanup

```bash
sk tentacle delete <name>
```

---

## Verification summary

Full gate table (mirrors Phase 3 gates):

| Gate | Command | Surface |
|------|---------|---------|
| Python security | `python3 test_security.py` | Python |
| Python fixes | `python3 test_fixes.py` | Python |
| Python all | `python3 run_all_tests.py` | Python |
| Quality gates | `python3 tests/test_quality_gates.py` | Hooks/docs/skills |
| TS typecheck | `cd browse-ui && pnpm typecheck` | browse-ui |
| TS lint | `cd browse-ui && pnpm lint` | browse-ui |
| TS format | `cd browse-ui && pnpm format:check` | browse-ui |
| Vitest | `cd browse-ui && pnpm test` | browse-ui |
| Next.js build | `cd browse-ui && pnpm build` | browse-ui |
| Rust | `cargo fmt --all -- --check && cargo clippy -- -D warnings && cargo test` | Rust |
| Code review | `code-reviewer` agent | Any |

---

## CLI reference

```bash
# Planning
sk tentacle create <name> --scope "<paths>" --desc "<desc>" --briefing [--profile <agent-id>]
sk tentacle todo <name> add "<task>"

# Dispatch (always use --model claude-opus-4.7 for leaders)
sk tentacle swarm <name> --agent-type <type> --model claude-opus-4.7 --briefing
sk tentacle swarm <name> --output parallel --briefing
sk tentacle dispatch <name> --agent-type <type> --model claude-opus-4.7 --briefing

# Monitoring
sk tentacle status
sk tentacle show <name>

# Handoff
sk tentacle handoff <name> "<summary>" --status DONE --changed-file <path> --learn
sk tentacle handoff <name> "<question>" --status AMBIGUOUS   # request peer input

# Goal loop
sk tentacle goal init --title "<goal>"
sk tentacle goal link <name>
sk tentacle goal criteria check
sk tentacle goal eval --decision continue|complete
sk tentacle goal gate pass <id> --reason "<evidence>"
sk tentacle verify <name> "<command>" --label "goal-eval"
sk tentacle goal verify-loop [--escalate]
sk tentacle goal resume

# Close
sk tentacle resume <name>
sk tentacle complete <name>
sk tentacle delete <name>
sk tentacle marker-cleanup [--apply]

# fallback: python3 ~/.copilot/tools/tentacle.py <cmd> <args>
```

---

## Tips

1. **Opus for all leaders** — never dispatch a leader with haiku or sonnet; quality-over-speed means `claude-opus-4.7` for every leader tentacle
2. **Loop, never block** — when confidence < 1.0, create a research-leader tentacle; never write BLOCKED to a handoff without first trying the loop
3. **Peer leaders as peers** — if dev-leader is stuck on a test strategy, handoff to test-leader for input; leaders collaborate, not silo
4. **Invest in CONTEXT.md** — 2-3 minutes writing good context saves 10 minutes of agent confusion
5. **Keep todos atomic** — each item = one testable deliverable
6. **No scope overlap** — overlapping scopes cause agents to overwrite each other
7. **Complete before delete** — `complete` saves learnings; `delete` alone loses them
8. **Commit after each phase** — uncommitted code is lost if the session crashes
9. **Run the app** — build+test ≠ works. Launch browse-ui + Python backend to verify E2E behavior
10. **Multi-surface = multi-wave** — dev + browse-leader in parallel, then test-leader, then qa-leader; never send qa-leader before tests pass
11. **⚠️ Commit restriction** — Sub-agents must not run `git commit`/`git push`; enforced by git hooks when installed

---

## ⛔ Workflow Integration

This project's verification workflow (from `copilot-instructions.md` and `AGENTS.md`) maps to tentacle phases as follows:

| Outer Workflow Phase | Tentacle Phase |
|---------------------|---------------|
| **Preflight**: `sk briefing --auto --compact` | Phase 0: Clarify Spec |
| **Edit**: minimal footprint, no SQL interpolation | Phase 2: Execute |
| **Verification by surface**: run all gates | Phase 3: Verify |
| **Closeout**: `sk learn`, `task_complete` | Phase 4: Close |

**Key rule**: The tentacle's internal lifecycle (Clarify→Plan→Execute→Verify→Close) is NOT the entire workflow. The outer workflow gates (briefing, verification, learn) must run AROUND the tentacle lifecycle.

```
sk briefing --auto --compact          ← BEFORE first tentacle
  │
  ▼
Tentacle Lifecycle (Clarify→Plan→Execute→Verify→Close)
  │
  ▼
python3 test_security.py              ← AFTER all tentacles complete (Python surface)
python3 test_fixes.py
  │
  ▼
sk learn --pattern/--mistake          ← BEFORE task_complete
  │
  ▼
task_complete / git commit
```

---

## Reference docs

- `~/.copilot/tools/skills/tentacle-orchestration/references/` — canonical reference docs
  - `cli-reference.md` — full command reference and CONTEXT.md template
  - `decomposition-review.md` — step file review checklist
  - `verification-gates.md` — gate descriptions
  - `spec-clarification.md` — Phase 0 full process
- `docs/AGENT-RULES.md` — all 11 agent rules including confidence gate and tentacle obligations
- `docs/ARCHITECTURE.md` — Python/Rust boundary, script inventory
- `docs/HOOKS.md` — hook enforcement table
- `.github/agents/*.agent.md` — available specialist agent profiles
