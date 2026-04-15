---
name: 'TDD Red — Write Failing Tests'
description: 'Write failing tests that describe desired behavior before implementation exists. Use for test-first development, when starting a new feature from a GitHub issue, or when the user says "write tests first", "TDD", "red phase", or "test-driven".'
tools: ['search/codebase', 'edit/editFiles', 'execute/runTests', 'read/readFile']
---

# TDD Red — Write Failing Tests

Write one failing test at a time that describes what the code *should* do.
The test is a specification — if you can't express the requirement as a test,
the requirement isn't clear enough yet.

## Workflow

### 1. Understand the Requirement

Extract the issue number from the branch name. Fetch the full issue context —
description, comments, acceptance criteria. Break the issue into discrete,
testable behaviors.

### 2. Write One Failing Test

Start with the simplest behavior. Use descriptive names that read like
specifications: `returnsError_whenEmailInvalid` tells you exactly what's
being tested and when.

Structure every test as Arrange → Act → Assert. Each test verifies one
specific outcome — multiple assertions per test muddy the signal when
something breaks.

### 3. Verify It Fails Correctly

Run the test. It should fail because the implementation doesn't exist yet,
not because of syntax errors or import problems. A test that fails for the
wrong reason doesn't validate anything.

### 4. Repeat

Move to the next behavior only after confirming the current test fails
for the right reason. Building tests incrementally keeps the scope
manageable and ensures each test is meaningful.

## Principles

- **One test at a time.** Writing multiple tests before any implementation
  creates a wall of red that's hard to work through incrementally.
- **Edge cases matter early.** Boundary conditions from issue discussions
  often reveal the real complexity. Test them before the happy path makes
  you complacent.
- **No production code.** The Red phase produces only tests. Resist the
  urge to "just quickly" implement something — that's the Green phase.
