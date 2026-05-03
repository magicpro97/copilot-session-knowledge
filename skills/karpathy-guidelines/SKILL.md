---
name: karpathy-guidelines
description: Behavioral guidelines to reduce common LLM coding mistakes. Use when writing, reviewing, or refactoring code to avoid overcomplication, make surgical changes, surface assumptions, and define verifiable success criteria.
license: MIT
vendored-from: https://github.com/forrestchang/andrej-karpathy-skills
vendored-commit: main
supported-hosts: Copilot CLI, Claude Code
---

# Karpathy Guidelines

Behavioral guidelines to reduce common LLM coding mistakes, derived from [Andrej Karpathy's observations](https://x.com/karpathy/status/2015883857489522876) on LLM coding pitfalls.

**Tradeoff:** These guidelines bias toward caution over speed. For trivial tasks, use judgment.

## When to Use

Invoke this skill when:
- Writing new code and at risk of over-engineering
- Reviewing or refactoring existing code
- Debugging a bug (need verifiable fix criteria)
- Starting a multi-step task (need a scoped plan)
- Feeling tempted to "improve" adjacent unrelated code

## Workflow

Apply the four guidelines in order for any non-trivial coding task:

### 1. Think Before Coding

**Don't assume. Don't hide confusion. Surface tradeoffs.**

Before implementing:
- State your assumptions explicitly. If uncertain, ask.
- If multiple interpretations exist, present them - don't pick silently.
- If a simpler approach exists, say so. Push back when warranted.
- If something is unclear, stop. Name what's confusing. Ask.

### 2. Simplicity First

**Minimum code that solves the problem. Nothing speculative.**

- No features beyond what was asked.
- No abstractions for single-use code.
- No "flexibility" or "configurability" that wasn't requested.
- No error handling for impossible scenarios.
- If you write 200 lines and it could be 50, rewrite it.

Ask yourself: "Would a senior engineer say this is overcomplicated?" If yes, simplify.

### 3. Surgical Changes

**Touch only what you must. Clean up only your own mess.**

When editing existing code:
- Don't "improve" adjacent code, comments, or formatting.
- Don't refactor things that aren't broken.
- Match existing style, even if you'd do it differently.
- If you notice unrelated dead code, mention it - don't delete it.

When your changes create orphans:
- Remove imports/variables/functions that YOUR changes made unused.
- Don't remove pre-existing dead code unless asked.

The test: Every changed line should trace directly to the user's request.

### 4. Goal-Driven Execution

**Define success criteria. Loop until verified.**

Transform tasks into verifiable goals:
- "Add validation" → "Write tests for invalid inputs, then make them pass"
- "Fix the bug" → "Write a test that reproduces it, then make it pass"
- "Refactor X" → "Ensure tests pass before and after"

For multi-step tasks, state a brief plan:
```
1. [Step] → verify: [check]
2. [Step] → verify: [check]
3. [Step] → verify: [check]
```

Strong success criteria let you loop independently. Weak criteria ("make it work") require constant clarification.

**Within orchestrated workflows (multi-tentacle goals):** Apply this principle at the goal level, not just the task level. Before dispatching any tentacle:
1. Write the goal's success criteria explicitly (e.g., in `CONTEXT.md` or the first tentacle's description).
2. After all verification gates pass, evaluate the goal against those criteria.
3. If unmet, loop: plan new tentacles scoped to remaining gaps. If met, proceed to commit and close.

The loop-until-verified semantic applies at every level — individual task, tentacle, and orchestrated goal — and produces the same result: no "done" claim without evidence.

## Examples

<example>
User asks: "Add dark mode support to the dashboard"

✅ Karpathy-aligned response:
1. Clarify scope: "Do you want CSS variables only, or also a toggle UI element?"
2. After clarification: implement exactly what was asked — no additional themes, no config system
3. Success criteria: "Dark mode renders correctly on dashboard; no other pages changed"
</example>

<example>
User asks: "Fix the login bug"

✅ Karpathy-aligned response:
1. State assumption: "I assume the bug is the 401 on expired tokens based on recent context"
2. Write a failing test that reproduces the bug
3. Make the minimal change to pass the test
4. Verify: no other tests broken, no adjacent refactoring done
</example>

<example>
User asks: "Refactor the data processing module"

✅ Karpathy-aligned response:
1. Ask: "What's the goal — readability, performance, or testability?"
2. After answer: change only what serves the stated goal
3. Match existing code style even if you'd write it differently
4. Ensure all existing tests still pass before and after
</example>

## Attribution

Vendored from [forrestchang/andrej-karpathy-skills](https://github.com/forrestchang/andrej-karpathy-skills) under MIT license.
Original guidelines derived from Andrej Karpathy's public observations on LLM coding pitfalls.
