---
name: 'TDD Green — Make Tests Pass'
description: 'Implement minimal code to make failing tests pass without over-engineering. Use after the Red phase, when tests exist but implementation is missing, or when the user says "make it pass", "green phase", or "implement minimally".'
tools: ['search/codebase', 'edit/editFiles', 'execute/runTests', 'read/readFile']
---

# TDD Green — Make Tests Pass

Write the least code necessary to turn the test green. Speed matters more
than elegance here — duplication and rough edges are fine because the
Refactor phase handles cleanup.

## Workflow

### 1. Run the Failing Test

Confirm exactly what needs to change. Read the error message carefully —
it often points directly to what's missing.

### 2. Implement Minimally

Start with the most obvious implementation. Hard-coded returns are
legitimate if they make the test pass — additional tests will force
generalization naturally.

Progress through: constants → conditionals → loops → abstractions.
Each step is driven by a failing test, not by anticipation.

### 3. Run All Tests

The new code must not break existing tests. If it does, the implementation
is too broad or has unintended side effects. Fix the regression before
moving on.

### 4. Do Not Modify Tests

If a test seems wrong during Green, go back to Red and fix the test
there. Changing tests and implementation simultaneously makes it
impossible to know which one is correct.

## Principles

- **Just enough code.** The goal is a green bar, not a beautiful design.
  Over-engineering in Green wastes effort that Refactor handles better.
- **Stay in scope.** Implement only what the current test requires. Future
  requirements belong in future tests.
- **Fake it till you make it.** Starting with simple returns and gradually
  generalizing is faster and produces better-tested code than trying to
  write the final implementation immediately.
