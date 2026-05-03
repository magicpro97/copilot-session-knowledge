---
name: task-step-generator
description: >
  Generate a structured STEPS.md file that breaks a specific task into concrete, ordered
  steps grounded in the project's phased workflow. Use when a task is too complex for a
  single prompt but too small for full tentacle orchestration — e.g., a single-module
  feature, a multi-stage bug fix, or a scripted migration. Trigger phrases: "create step
  file", "generate steps", "make a task plan", "write out the steps", "break this into
  steps", "step-by-step plan", "task steps", "execution plan".
---

# Task Step Generator

Generate a `STEPS.md` file that breaks a specific task into concrete, ordered steps an
agent can follow without re-reading the full specification. Steps are grounded in the
project's existing phases (from `WORKFLOW.md` or the standard phased lifecycle) and
scoped to complete in a single agent session.

## When to Use

- Task is too complex to fit in one prompt response but touches only 1–3 files or modules
- You want a traceable, reviewable execution plan before starting implementation
- A task has non-obvious ordering constraints (e.g., schema migration before code change)
- User says "create step file", "break this into steps", "make a task plan"

**Not for:**
- Tasks spanning 3+ independent modules → use `tentacle-orchestration` to decompose first
- Project-level process templates → use `workflow-creator` to generate `WORKFLOW.md`
- Pure research or exploration tasks (no implementation deliverable)

## Why Step Files Help

Without explicit steps, agents make predictable mistakes:
- Start coding before understanding requirements
- Skip verification steps when they "seem" done
- Lose track of intermediate deliverables between session boundaries
- Make irreversible changes (DB migrations, file deletions) before testing

A step file makes the execution plan visible and checkable — each step has a concrete
done condition, so the agent knows when to proceed and a human can audit progress.

## How to Generate

### Phase 1: Understand the task

Before writing any steps, investigate:

1. Read the task description and clarify any ambiguities (apply spec-clarification if unclear)
2. Identify the implementation target: which files change? What is the entry point?
3. Check if a `WORKFLOW.md` exists (`cat .github/WORKFLOW.md 2>/dev/null`) — use its phases
   as the skeleton; if not, use the standard phases below
4. Identify ordering constraints: what must happen before what?

### Phase 2: Map to phases

Assign each piece of work to a phase. Use only the phases the task actually needs.

**Standard phases** (skip phases the task doesn't need):

| Phase | Purpose | Gate artifact |
|-------|---------|--------------|
| CLARIFY | Confirm requirements are implementation-ready | Spec health report or confirmed spec |
| DESIGN | Produce a technical design or interface sketch | Design doc or interface definition |
| VERIFY | Review the design before touching code | Explicit approval (PASS/FAIL) |
| BUILD | Implement the code change | Compiling code with no regressions |
| TEST | Run and write tests | All tests green |
| REVIEW | Check correctness, security, contracts | Code review findings addressed |
| LOOP-EVAL | Evaluate whether the overarching goal is met; decide to iterate or close | Goal met (proceed to COMMIT) or remaining gaps identified (loop to BUILD/TEST) |
| COMMIT | Package and ship | Clean git commit |

Each phase produces one concrete artifact. A step only advances when that artifact exists.

Include a **LOOP-EVAL** step when:
- The task has an explicit overarching goal (e.g., "all benchmarks pass", "all tests green")
- The task may require multiple iterations to reach the goal (e.g., a fix that reveals follow-on failures)
- The agent will be operating semi-autonomously and must decide whether to continue or stop

Omit LOOP-EVAL for strictly bounded tasks with a single deliverable (e.g., "add this one column", "rename this function").

### Phase 3: Write concrete steps

Each step must answer: **What exactly do I do, and how do I know I'm done?**

Step format:
```markdown
## Step N: <Phase> — <Action>

**Goal:** One sentence describing what this step produces.

**Actions:**
1. <Concrete command or action>
2. <Concrete command or action>

**Done when:** <Observable, verifiable condition — not "seems right">
```

Use real commands from the project's toolchain. Avoid vague verbs like "check" or
"ensure" — replace them with the exact command that confirms the condition.

### Phase 4: Add a self-check table

After all steps, add a phase-gate table for quick progress tracking:

```markdown
## Phase Gates

| Phase | Artifact | Status |
|-------|---------|--------|
| CLARIFY | Confirmed spec | ☐ |
| BUILD | `npx tsc --noEmit` passes | ☐ |
| TEST | `yarn test` passes | ☐ |
| LOOP-EVAL | Goal criteria met (or single-pass task: skip) | ☐ |
| COMMIT | Clean `git diff --stat` | ☐ |
```

## Output Format

Write to `.github/steps/<task-slug>.md` or to the path the user specifies.
If neither exists, write to `STEPS.md` in the project root.

See `references/step-file-template.md` for the full annotated template.

## Anti-Patterns

| Anti-Pattern | Why It Fails |
|-------------|-------------|
| Steps without done conditions | Agent can't tell when to proceed |
| Mixing phases (build + test in one step) | Gate is ambiguous; errors mix together |
| Vague actions ("verify it works") | Not actionable; agent guesses |
| No ordering constraints | Agent skips steps that depend on earlier output |
| Generating for 3+ independent modules | Step file becomes too large; use tentacle instead |

<example>
**Task:** Add a `created_at` timestamp column to the `orders` table and expose it in the API response.

**Project:** Python + SQLite + FastAPI, no existing WORKFLOW.md.

**Generated step file:**

```markdown
# STEPS: Add created_at to orders

**Task:** Add `created_at` timestamp to `orders` table and return it in GET /orders.
**Scope:** `migrations/`, `models/order.py`, `routers/orders.py`, `tests/test_orders.py`

## Step 1: CLARIFY — Confirm scope

**Goal:** Confirm there are no ambiguities before touching the schema.

**Actions:**
1. `grep -r "orders" migrations/` — confirm existing migrations are in order
2. `grep -r "created_at" models/` — check if pattern exists elsewhere for consistency

**Done when:** Migration baseline known; no contradicting existing column found.

## Step 2: BUILD — Add migration

**Goal:** Add migration file that adds `created_at` with a non-null default.

**Actions:**
1. Create `migrations/003_add_orders_created_at.sql`:
   ```sql
   ALTER TABLE orders ADD COLUMN created_at TEXT NOT NULL DEFAULT (datetime('now'));
   ```
2. Run migration: `sqlite3 app.db < migrations/003_add_orders_created_at.sql`
3. `python -c "import ast; ast.parse(open('models/order.py').read())"` — syntax check

**Done when:** `sqlite3 app.db ".schema orders"` shows `created_at` column.

## Step 3: BUILD — Update model and router

**Goal:** Surface `created_at` in the Pydantic model and API response.

**Actions:**
1. Add `created_at: str` field to `OrderResponse` in `models/order.py`
2. Map DB column to field in `routers/orders.py` query result

**Done when:** `python -m py_compile models/order.py routers/orders.py` exits 0.

## Step 4: TEST — Run and extend tests

**Goal:** Verify the field appears in the API response and no existing tests broke.

**Actions:**
1. `pytest tests/test_orders.py -v` — confirm existing tests still pass
2. Add assertion: `assert "created_at" in response.json()[0]`
3. `pytest tests/test_orders.py -v` — confirm new assertion passes

**Done when:** All tests green; new assertion present and passing.

## Step 5: REVIEW — Quick correctness check

**Goal:** Verify no injection vectors, no null risks, no contract breaks.

**Actions:**
1. Confirm `created_at` default is server-side (not user-supplied)
2. Confirm migration is reversible (document rollback in a comment)

**Done when:** No critical findings from the review checklist.

## Step 6: COMMIT — Ship

**Actions:**
1. `git add migrations/ models/ routers/ tests/`
2. `git diff --stat` — confirm only expected files changed
3. `git commit -m "feat(orders): add created_at timestamp column and API field"`

**Done when:** `git log --oneline -1` shows the commit.

## Phase Gates

| Phase | Artifact | Status |
|-------|---------|--------|
| CLARIFY | Migration baseline confirmed | ☐ |
| BUILD (migration) | Schema shows `created_at` | ☐ |
| BUILD (code) | `py_compile` exits 0 | ☐ |
| TEST | All tests green | ☐ |
| REVIEW | No critical findings | ☐ |
| COMMIT | Clean commit | ☐ |
```
</example>
