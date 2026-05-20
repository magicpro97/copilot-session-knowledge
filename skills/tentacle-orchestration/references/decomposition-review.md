# Decomposition Review Guide

Use this guide after `task-step-generator` creates a step file and before creating tentacles. The goal is to turn a plausible generated plan into an auditable, low-conflict execution plan.

## Why this review exists

Generated plans are useful scaffolds, but they can anchor on an early decomposition and hide missing evidence, unclear dependencies, or overlapping ownership. Research-informed workflow practices point in the same direction:

- Cognitive-load management: smaller, named work units reduce working-memory pressure and make handoffs more reliable.
- Checklist discipline: short explicit checks catch omissions better than memory, especially under time pressure.
- Code-review efficiency: small, focused changes are easier to review and less likely to hide defects.
- Perspective-based reading: plans improve when reviewed from several viewpoints, such as user behavior, data flow, operations, and rollback.

Use the generated step file as a draft. Keep what is useful, edit what is vague, and reject steps that do not map to a clear owner or evidence gate.

## Review pass

For each generated step, fill this table before dispatch:

| Check | Question | Required outcome |
|-------|----------|------------------|
| Acceptance signal | What observable behavior proves the step is done? | A command, assertion, screenshot, log, trace, artifact, or documented non-code output |
| RED before GREEN | For implementation/fix work, what fails before the change? | A failing test, reproduction, screenshot, log, or deterministic assertion captured before implementation |
| Dependency order | What must finish before this starts? | Sequential dependencies named; only independent work may run in parallel |
| Scope boundary | Which files or systems may this step modify? | A narrow, non-overlapping scope suitable for one tentacle |
| Decision confidence | Is the scope/dependency/evidence plan known with confidence `1.0`? | If not, split the ambiguity and dispatch opus-class research before implementation |
| Agent fit | Which agent type/model should own it? | Specialist agent when available; adequate model tier for code generation/review |
| Evidence owner | Who captures proof and where is it stored? | Evidence path or handoff field named before dispatch |
| Risk | What can silently regress? | Targeted review/test point added |

## Accept, edit, reject

Record the result in the orchestrator notes and in each tentacle's `CONTEXT.md`.

```markdown
## Step-plan review
- Source step file: `.github/steps/<task>.md`
- Accepted steps: 1, 2, 4
- Edited steps: 3 (split tests from implementation), 5 (added hash evidence)
- Rejected steps: 6 (scope overlaps with existing migration tentacle)
- Dependency order: foundation -> implementation -> tests -> review -> goal-eval
- Evidence contract: unit test output, integration trace, screenshots with hashes
```

## Splitting rules

Split a generated step when:

- it mixes test creation, implementation, and review in one owner;
- it touches multiple independent modules;
- it cannot be reviewed in one context window;
- it has multiple unrelated acceptance signals;
- it would require one agent to edit files also owned by another tentacle.

Keep steps together when:

- separating them would force repeated setup or duplicate context;
- the change is atomic and one test proves the full behavior;
- one specialist agent clearly owns the entire concern.

## Evidence rules

Evidence must distinguish the intended behavior from accidental success:

- Prefer stable assertions over screenshots when possible.
- When screenshots are necessary, include visible unique data and record hashes.
- Count-only evidence is weak unless the acceptance criterion is explicitly count-based.
- Logs are useful only when they identify the request, entity, or correlation id being proven.
- Green evidence must test the same criterion that failed in RED.

## Parallelism rules

Parallelize only when all are true:

1. File scopes do not overlap.
2. Runtime state does not conflict.
3. Dependencies are explicit.
4. Each agent can complete without asking another agent for hidden context.
5. The orchestrator can verify each result independently.
6. Every routing/scope decision has confidence `1.0`; otherwise research tentacles run first.

If any condition is false, run sequentially or create a foundation tentacle first.
