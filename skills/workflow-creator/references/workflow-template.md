# WORKFLOW.md Template

Copy and customize for your project.

```markdown
# Development Workflow

> This document defines the phased development lifecycle with quality gates.
> Every phase has a BLOCKING gate — cannot proceed until the previous phase's artifact exists.

## Phase Overview

| Phase | Name | Owner | Gate Artifact | Skip When |
|-------|------|-------|---------------|-----------|
| 0 | CLARIFY | spec-clarifier | Spec Health Report (verdict=CLEAN) | Trivial bugfix (<3 files, clear repro) |
| 1 | DESIGN | designer | Design files (HTML/PNG/Figma) | Non-UI changes |
| 2 | VERIFY | 3 reviewers (parallel) | All 3 verdicts = PASS | Phase 1 skipped |
| 3 | BUILD | builder | Compiling code + passing unit tests | — |
| 4 | TEST | test runners (parallel) | All test suites pass | — |
| 5 | REVIEW | code-reviewer | Review approval | — |
| 6 | QA | qa-verifier | Screenshots + OCR evidence | Non-UI changes |
| 7 | COMMIT | conductor | Clean git commit | — |

## ⛔ BLOCKING WAIT Rule

Start Phase N+1 ONLY after Phase N artifacts exist.
Parallelism is allowed WITHIN a single phase (e.g., 3 test suites in parallel).

Starting the next phase early means working blind — code written without verification
results almost always needs rewriting.

## Phase Gate Evidence

Each gate requires specific artifacts before the phase can be marked "done":

| Phase | Required Evidence | Verification |
|-------|-------------------|-------------|
| 0: CLARIFY | Spec Health Report with verdict | Check verdict = ✅ CLEAN |
| 1: DESIGN | Design files exist | File existence check |
| 2: VERIFY | All reviewer verdicts | Count PASS verdicts = total reviewers |
| 3: BUILD | Build output + test output | Exit code = 0 |
| 4: TEST | Test results per platform | 0 failures across all suites |
| 5: REVIEW | Review comments | No blocking issues |
| 6: QA | Screenshots + text verification | OCR confirms expected elements |
| 7: COMMIT | Git commit hash | Commit exists in log |

## Self-Check Protocol

At every phase transition, run this check:

```
□ Previous phase artifact exists (not just "I think it passed")
□ Artifact meets quality criteria (not empty, not trivially correct)
□ No blocking issues from previous phase remain unresolved
□ Current phase has clear input from previous phase's output
```

## Phase Details

### Phase 0: CLARIFY
<!-- Customize: what constitutes a "clear spec" for your project -->

### Phase 1: DESIGN
<!-- Customize: design tool, output format, ad slot requirements -->

### Phase 2: VERIFY
<!-- Customize: how many reviewers, what they check -->

### Phase 3: BUILD
<!-- Customize: build command, test command, E2E impact check -->

### Phase 4: TEST
<!-- Customize: test suites, platforms, parallel execution -->

### Phase 5: REVIEW
<!-- Customize: review checklist, independence rule -->

### Phase 6: QA
<!-- Customize: platforms, themes, screenshot process, OCR -->

### Phase 7: COMMIT
<!-- Customize: commit message format, trailers -->

## Anti-Patterns

| Anti-Pattern | Why It Fails |
|-------------|-------------|
| Skipping phases for "simple changes" | Simple changes still break things |
| Starting Phase N+1 while N runs | Working blind = rework guaranteed |
| Marking phases done without artifacts | "Trust me" doesn't catch bugs |
| Using same agent for build and review | Self-review misses builder's blind spots |
| Sequential test suites when parallel is possible | Wastes time proportional to suite count |
```
