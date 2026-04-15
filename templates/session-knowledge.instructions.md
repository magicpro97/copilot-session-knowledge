---
applyTo: "**/*"
---

# Session Knowledge (AUTO-LOADED)

> Auto-injected into EVERY context. Knowledge tools are NOT optional.

## Before Starting ANY Task

```bash
python3 ~/.copilot/tools/briefing.py --auto --compact
```

Read the output. It contains past mistakes to avoid and patterns to follow.

## After Completing Work

Record what you learned (choose appropriate type):

```bash
# After fixing a bug:
python3 ~/.copilot/tools/learn.py --mistake "Title" "Root cause and fix" --tags "module,tech"

# After implementing a feature:
python3 ~/.copilot/tools/learn.py --feature "Title" "What was built" --tags "module,tech"

# After discovering useful pattern:
python3 ~/.copilot/tools/learn.py --pattern "Title" "What works well" --tags "module,tech"

# After codebase insight:
python3 ~/.copilot/tools/learn.py --discovery "Title" "What was found" --tags "module,tech"
```

## Rules

- ❌ NEVER skip briefing before starting work
- ❌ NEVER skip learn after fixing a non-trivial bug
- ✅ Keep entries concise (1-3 sentences)
- ✅ Use domain tags: module names, technology keywords
