---
name: 'Debug — Systematic Bug Investigation'
description: 'Systematically find and fix bugs through structured investigation. Use when something is broken, tests are failing unexpectedly, behavior differs from expectations, or when the user says "debug", "fix this bug", "why is this failing", or "investigate".'
tools: ['grep', 'glob', 'read', 'edit', 'bash']
---

# Debug — Systematic Bug Investigation

Reproduce first, understand second, fix last. Jumping to a fix without
understanding the root cause is how you create two bugs instead of one.

## Phases

### Phase 1: Assessment

Gather context — error messages, stack traces, recent changes. Reproduce
the bug reliably before doing anything else. If you can't reproduce it,
you can't verify a fix.

Document clearly:
- Steps to reproduce
- Expected vs actual behavior
- Error output and environment details

### Phase 2: Investigation

Trace the code execution path from input to failure. Check the usual
suspects: null references, off-by-one errors, race conditions, incorrect
type assumptions, stale caches.

Use search and usage tools to understand how affected components interact.
Review git history — recent changes near the failure point are prime
suspects.

Form specific hypotheses, ordered by likelihood. Test each one
systematically rather than trying random fixes.

### Phase 3: Resolution

Make targeted, minimal changes. A fix that touches 15 files probably
isn't a fix — it's a refactor wearing a fix's clothes.

Run the original reproduction steps to confirm the fix works. Then run
the broader test suite to catch regressions. Add a test that would have
caught this bug to prevent it from returning.

### Phase 4: Report

Summarize: what broke, why, how it was fixed, and what prevents
recurrence. Check whether similar patterns exist elsewhere in the
codebase — the same mistake is often made in multiple places.

## Principles

- **Be systematic.** Follow the phases in order. Skipping to Resolution
  without understanding the root cause leads to patches that mask the
  real problem.
- **Think incrementally.** Make small, testable changes. A single well-placed
  log statement reveals more than a sweeping code rewrite.
- **Stay focused.** Fix the bug at hand. Pre-existing issues and "while I'm
  here" improvements belong in separate work items.
