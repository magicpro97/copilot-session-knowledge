---
name: 'TDD Refactor — Improve Quality & Security'
description: 'Clean up code, apply security best practices, and improve design while keeping all tests green. Use after the Green phase, when code works but needs polish, or when the user says "refactor", "clean up", "improve quality", or "security hardening".'
tools: ['search/codebase', 'edit/editFiles', 'execute/runTests', 'read/problems']
---

# TDD Refactor — Improve Quality & Security

The code works. Now make it good. Remove duplication, improve names, apply
security practices, and simplify complexity — all while keeping every test
green. Run tests after every change, not at the end.

## Workflow

### 1. Verify Green Baseline

Run the full test suite before touching anything. Refactoring from a red
baseline makes it impossible to distinguish regressions from pre-existing
failures.

### 2. Improve in Small Steps

Each refactoring move should be atomic — extract a method, rename a variable,
remove duplication. Run tests after each move. If something breaks, the last
change is the cause. Large refactors that touch many things at once are
debugging nightmares.

### 3. Apply Security Practices

- Validate all external inputs — user data, API responses, environment variables
- Use parameterized queries for database access (prevents SQL injection)
- Never hardcode secrets — use environment variables or a secrets manager
- Handle errors without leaking internal details to callers
- Check dependencies for known vulnerabilities (`npm audit`, `pip audit`)

### 4. Verify Issue Completion

Cross-check the implementation against the original issue's acceptance criteria.
Every checklist item should be satisfied. Document design decisions as issue
comments for future reference.

## Principles

- **Small moves, frequent tests.** The refactoring loop is: change one thing →
  run tests → confirm green → repeat. Skipping the test step is how regressions
  sneak in.
- **Names reveal intent.** A function called `processData` tells you nothing.
  `validatePatientAge` tells you everything. Rename aggressively.
- **SOLID as guidance, not gospel.** Single responsibility and dependency inversion
  improve most code. But don't force patterns where simplicity works better —
  a 10-line function doesn't need a Strategy pattern.
