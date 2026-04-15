# Phase 0: Spec Clarification

This phase takes a raw specification and makes it implementation-ready through iterative Q&A. No planning or coding happens until the spec is CLEAN.

## Step 0.1: Analyze the Specification

Read the spec and evaluate it against 8 quality dimensions:

| Dimension | What to check |
|-----------|--------------|
| **Clarity** | Are terms unambiguous? Could two engineers build the same thing from this spec? |
| **Completeness** | Are all flows covered (happy + error + edge)? Boundary conditions? Non-functional requirements? |
| **Consistency** | Do any requirements contradict each other or conflict with existing system behavior? |
| **Testability** | Can each requirement be verified with a concrete test? Are acceptance criteria measurable? |
| **Feasibility** | Are there technically impossible requirements? Undeclared dependencies? |
| **Impact** | What existing features, modules, APIs, schemas are affected? What is the blast radius? |
| **Trade-offs** | Are there competing concerns (perf vs simplicity)? What alternatives weren't considered? |
| **Risks** | What could go wrong? What are the unknowns? Backward compatibility? Rollback strategy? |

Investigate the codebase to understand current behavior BEFORE generating questions. Use `grep`, `glob`, and `view` to find relevant code — questions backed by code evidence get faster, better answers.

## Step 0.2: Generate the Spec Health Report

Produce a structured report with:

```markdown
## Spec Health Report: <feature name>

### Summary
- **Clarity**: 🟢 Clear / 🟡 Minor gaps / 🔴 Ambiguous
- **Completeness**: 🟢 / 🟡 / 🔴
- **Consistency**: 🟢 / 🟡 / 🔴
- **Testability**: 🟢 / 🟡 / 🔴
- **Feasibility**: 🟢 / 🟡 / 🔴
- **Verdict**: ✅ CLEAN — ready to plan / ⚠️ BLOCKED — N questions need answers

### Impact Analysis
- **Affected modules**: <list>
- **Affected APIs**: <list>
- **Database changes**: <yes/no, details>
- **Blast radius**: <low/medium/high> — <explanation>

### Trade-offs
| Decision | Option A | Option B | Recommendation |
|----------|----------|----------|----------------|

### Risks
| Risk | Likelihood | Impact | Mitigation |
|------|-----------|--------|------------|

### Blocking Questions
Q1. <question with context, code evidence, and A/B/C options>

### Non-blocking Questions (have reasonable defaults)
Q2. <question> — Default: <recommended option>
```

Question writing rules:
- **Closed-form** — provide A/B/C options, never open-ended "how should we handle this?"
- **Context-rich** — quote the spec, show the code, explain your reasoning
- **Code-backed** — investigate the codebase FIRST, then ask for confirmation of what you found
- **Prioritized** — blocking questions first, non-blocking second

## Step 0.3: Iterative Refinement

After receiving answers:
1. Re-evaluate the spec with the new information
2. Check if answers introduced NEW ambiguities or conflicts
3. Generate follow-up questions if needed
4. Update the Spec Health Report
5. Repeat until all blocking questions are resolved

Each round should produce fewer questions — if questions are increasing, you're asking the wrong questions or the spec has fundamental design issues that need escalation.

## Step 0.4: Declare CLEAN or Escalate

The spec is **CLEAN** when:
- Zero blocking questions remain
- All 8 dimensions are 🟢 or 🟡 (no 🔴)
- Impact analysis is complete with blast radius assessed
- At least one risk mitigation exists for each high-impact risk
- Trade-offs are documented with explicit decisions

If the spec cannot reach CLEAN after 3 rounds of Q&A, escalate — the problem is likely at the requirements level, not the clarification level.

**Gate**: Planning on an unclear spec produces incorrect decomposition, wasted agent work, and rework.
